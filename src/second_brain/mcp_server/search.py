"""Hybrid search index — SQLite FTS5 for keyword search, sqlite-vec for semantic.

The semantic (vector) layer is optional and isolated: if it can't be
set up (no ``SearchConfig`` passed, sqlite-vec not loadable, or Ollama
unavailable for embeddings) the index still serves keyword search
normally. Callers ask for semantic results explicitly via
:meth:`SearchIndex.semantic_search`.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from second_brain.config import SearchConfig

logger = logging.getLogger(__name__)


def _hash_content(content: str) -> str:
    """Return a stable content fingerprint used to detect page changes.

    Parameters
    ----------
    content: str
        Full markdown body of a wiki page.

    Returns
    -------
    str
        Hex SHA-256 digest of the UTF-8 encoded content.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SearchHit:
    stem: str
    title: str
    snippet: str
    score: float
    content_type: str
    domains: list[str]


class SearchIndex:
    """Hybrid FTS5 + optional vector search over wiki pages.

    Wraps a SQLite database with an FTS5 virtual table for full-text
    keyword search and a companion metadata table for filtered listing.
    When a ``SearchConfig`` with ``semantic_enabled`` is supplied and
    sqlite-vec loads, a vec0 table is added for embedding search.
    The database is created on first use and reused across calls.
    """

    def __init__(
        self,
        db_path: Path,
        search_config: SearchConfig | None = None,
    ) -> None:
        """
        Initialize the search index, creating tables if needed.

        Parameters
        ----------
        db_path: Path
            Filesystem path to the SQLite database file.
        search_config: SearchConfig | None
            When provided with ``semantic_enabled=True``, enables the
            optional embedding-backed semantic layer.
        """
        self._db_path = db_path
        self._search_config = search_config
        # Semantic stays off unless explicitly enabled AND the sqlite-vec
        # extension actually loads on this platform (checked in _init_db).
        self._semantic = bool(search_config and search_config.semantic_enabled)
        self._dim = search_config.embedding_dimensions if search_config else 768
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
                    stem,
                    title,
                    content,
                    domains,
                    tags,
                    content_type,
                    tokenize='porter unicode61'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wiki_meta (
                    stem TEXT PRIMARY KEY,
                    title TEXT,
                    content_type TEXT,
                    domains TEXT,
                    tags TEXT,
                    word_count INTEGER,
                    path TEXT,
                    content_hash TEXT,
                    mtime REAL
                )
            """)

        if self._semantic:
            self._init_vec_table()

    def _init_vec_table(self) -> None:
        """Create the vec0 embedding table, disabling semantics if it fails."""
        try:
            with self._vec_conn() as conn:
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS wiki_vec USING vec0("
                    f"stem TEXT PRIMARY KEY, embedding FLOAT[{self._dim}])"
                )
        except (sqlite3.Error, AttributeError, OSError) as exc:
            logger.warning("Semantic search disabled (sqlite-vec unavailable): %s", exc)
            self._semantic = False

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _vec_conn(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with the sqlite-vec extension loaded.

        Required for any statement that touches the ``wiki_vec`` table.
        """
        import sqlite_vec

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @property
    def semantic_enabled(self) -> bool:
        """Whether the embedding-backed semantic layer is active."""
        return self._semantic

    def index_page(
        self,
        stem: str,
        title: str,
        content: str,
        content_type: str,
        domains: list[str],
        tags: list[str],
        word_count: int,
        path: str,
        mtime: float | None = None,
    ) -> None:
        """
        Add or replace a page in both the FTS and metadata tables.

        Always recomputes the page embedding when the semantic layer is
        active, so callers should only invoke this for new or changed
        pages (see :meth:`sync_from_wiki`).

        Parameters
        ----------
        stem: str
            URL-safe slug used as the unique key.
        title: str
            Human-readable page title.
        content: str
            Full markdown body indexed for search.
        content_type: str
            Page type (e.g. ``"concept"``, ``"problem"``).
        domains: list[str]
            Knowledge domains the page belongs to.
        tags: list[str]
            Free-form tags for filtering.
        word_count: int
            Pre-computed word count of *content*.
        path: str
            Relative path from the wiki root to the ``.md`` file.
        mtime: float | None
            Filesystem modification time of the source file, stored so
            :meth:`sync_from_wiki` can skip unchanged files cheaply.
        """
        domains_str = ",".join(domains)
        tags_str = ",".join(tags)
        content_hash = _hash_content(content)

        with self._conn() as conn:
            conn.execute("DELETE FROM wiki_fts WHERE stem = ?", (stem,))
            conn.execute(
                "INSERT INTO wiki_fts (stem, title, content, domains, tags, content_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (stem, title, content, domains_str, tags_str, content_type),
            )
            conn.execute(
                "INSERT OR REPLACE INTO wiki_meta "
                "(stem, title, content_type, domains, tags, word_count, path, "
                "content_hash, mtime) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    stem,
                    title,
                    content_type,
                    domains_str,
                    tags_str,
                    word_count,
                    path,
                    content_hash,
                    mtime,
                ),
            )

        if self._semantic:
            self._index_embedding(stem, title, content)

    def _index_embedding(self, stem: str, title: str, content: str) -> None:
        """Compute and store the page embedding; best-effort, never raises.

        Parameters
        ----------
        stem: str
            Page key.
        title: str
            Page title (prepended to give the embedding a topical anchor).
        content: str
            Full markdown body.
        """
        from sqlite_vec import serialize_float32

        from second_brain.mcp_server.embeddings import embed_text

        if self._search_config is None:
            return
        vector = embed_text(f"{title}\n\n{content}", self._search_config)
        if vector is None:
            return
        try:
            with self._vec_conn() as conn:
                conn.execute("DELETE FROM wiki_vec WHERE stem = ?", (stem,))
                conn.execute(
                    "INSERT INTO wiki_vec (stem, embedding) VALUES (?, ?)",
                    (stem, serialize_float32(vector)),
                )
        except sqlite3.Error as exc:
            logger.debug("Embedding store failed for %s: %s", stem, exc)

    def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        """
        Keyword search using FTS5 with BM25 ranking.

        Parameters
        ----------
        query: str
            FTS5 match expression (supports boolean operators).
        limit: int
            Maximum number of results to return.

        Returns
        -------
        list[SearchHit]
            Hits ordered by descending relevance score.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT stem, title, snippet(wiki_fts, 2, '<b>', '</b>', '...', 40) as snip,
                          rank, content_type, domains
                   FROM wiki_fts
                   WHERE wiki_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            ).fetchall()

        return [
            SearchHit(
                stem=r["stem"],
                title=r["title"],
                snippet=r["snip"],
                score=abs(r["rank"]),
                content_type=r["content_type"],
                domains=r["domains"].split(",") if r["domains"] else [],
            )
            for r in rows
        ]

    def semantic_search(self, query: str, limit: int = 10) -> list[SearchHit]:
        """Embedding-based nearest-neighbor search over wiki pages.

        Finds pages by meaning rather than keyword overlap. Returns an
        empty list when the semantic layer is disabled or Ollama is
        unavailable, so callers can fall back to keyword search.

        Parameters
        ----------
        query: str
            Natural-language query to embed and match.
        limit: int
            Maximum number of results.

        Returns
        -------
        list[SearchHit]
            Hits ordered by ascending vector distance (closest first).
        """
        if not self._semantic or self._search_config is None:
            return []

        from sqlite_vec import serialize_float32

        from second_brain.mcp_server.embeddings import embed_text

        vector = embed_text(query, self._search_config)
        if vector is None:
            return []

        with self._vec_conn() as conn:
            knn = conn.execute(
                "SELECT stem, distance FROM wiki_vec "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (serialize_float32(vector), limit),
            ).fetchall()
            hits: list[SearchHit] = []
            for row in knn:
                meta = conn.execute(
                    "SELECT title, content_type, domains FROM wiki_meta WHERE stem = ?",
                    (row["stem"],),
                ).fetchone()
                if meta is None:
                    continue
                hits.append(
                    SearchHit(
                        stem=row["stem"],
                        title=meta["title"],
                        snippet=f"(semantic match, distance {row['distance']:.3f})",
                        score=row["distance"],
                        content_type=meta["content_type"],
                        domains=meta["domains"].split(",") if meta["domains"] else [],
                    )
                )
        return hits

    def list_pages(
        self,
        domain: str | None = None,
        content_type: str | None = None,
        tag: str | None = None,
    ) -> list[dict]:
        """
        List pages from the metadata table with optional filtering.

        Parameters
        ----------
        domain: str | None
            Filter to pages whose domains contain this value.
        content_type: str | None
            Filter to pages with this exact content type.
        tag: str | None
            Filter to pages whose tags contain this value.

        Returns
        -------
        list[dict]
            Metadata rows as dicts, ordered by title.
        """
        with self._conn() as conn:
            query = "SELECT * FROM wiki_meta WHERE 1=1"
            params: list = []

            if domain:
                query += " AND domains LIKE ?"
                params.append(f"%{domain}%")
            if content_type:
                query += " AND content_type = ?"
                params.append(content_type)
            if tag:
                query += " AND tags LIKE ?"
                params.append(f"%{tag}%")

            query += " ORDER BY title"
            rows = conn.execute(query, params).fetchall()

        return [dict(r) for r in rows]

    def _delete_page(self, stem: str) -> None:
        """Remove a page from the FTS, metadata, and vector tables."""
        with self._conn() as conn:
            conn.execute("DELETE FROM wiki_fts WHERE stem = ?", (stem,))
            conn.execute("DELETE FROM wiki_meta WHERE stem = ?", (stem,))
        if self._semantic:
            try:
                with self._vec_conn() as conn:
                    conn.execute("DELETE FROM wiki_vec WHERE stem = ?", (stem,))
            except sqlite3.Error as exc:
                logger.debug("Embedding delete failed for %s: %s", stem, exc)

    def _touch_mtime(self, stem: str, mtime: float) -> None:
        """Refresh a page's stored mtime without re-indexing its content.

        Used when a file's mtime changed but its content hash did not, so
        future syncs can skip it via the cheap mtime gate.
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE wiki_meta SET mtime = ? WHERE stem = ?", (mtime, stem)
            )

    def sync_from_wiki(self, wiki_dir: Path) -> int:
        """
        Incrementally reconcile the index with the wiki on disk.

        New and changed pages are (re)indexed and re-embedded; pages whose
        content is unchanged are skipped (no embedding work); pages removed
        from disk are dropped from the index. Change detection uses a cheap
        mtime gate backed by a content hash, so an unchanged wiki costs only
        a stat per file.

        Parameters
        ----------
        wiki_dir: Path
            Root directory of the compiled wiki.

        Returns
        -------
        int
            Number of pages (re)indexed during this sync.
        """
        from second_brain.compilation.structure import CONTENT_DIRS, _parse_frontmatter

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT stem, content_hash, mtime FROM wiki_meta"
            ).fetchall()
        indexed_pages = {r["stem"]: (r["content_hash"], r["mtime"]) for r in rows}

        seen_stems: set[str] = set()
        indexed_count = 0

        for content_dir in CONTENT_DIRS:
            dir_path = wiki_dir / content_dir
            if not dir_path.exists():
                continue
            for md_file in dir_path.glob("*.md"):
                stem = md_file.stem
                seen_stems.add(stem)
                file_mtime = md_file.stat().st_mtime
                previous = indexed_pages.get(stem)

                if previous is not None and previous[1] == file_mtime:
                    continue

                content = md_file.read_text(encoding="utf-8")
                content_hash = _hash_content(content)
                if previous is not None and previous[0] == content_hash:
                    self._touch_mtime(stem, file_mtime)
                    continue

                fm = _parse_frontmatter(content)
                self.index_page(
                    stem=stem,
                    title=fm.get("title", stem),
                    content=content,
                    content_type=fm.get("type", "unknown"),
                    domains=fm.get("domains", []),
                    tags=fm.get("tags", []),
                    word_count=len(content.split()),
                    path=f"{content_dir}/{md_file.name}",
                    mtime=file_mtime,
                )
                indexed_count += 1

        removed_count = 0
        for stem in indexed_pages.keys() - seen_stems:
            self._delete_page(stem)
            removed_count += 1

        if indexed_count or removed_count:
            logger.info(
                "Synced search index: %d indexed, %d removed",
                indexed_count,
                removed_count,
            )
        return indexed_count

    def rebuild_from_wiki(self, wiki_dir: Path) -> int:
        """
        Drop and recreate the entire search index from wiki files.

        Unconditionally re-embeds every page; prefer :meth:`sync_from_wiki`
        for routine refreshes. Use this only when a full rebuild is wanted.

        Parameters
        ----------
        wiki_dir: Path
            Root directory of the compiled wiki.

        Returns
        -------
        int
            Number of pages indexed.
        """
        from second_brain.compilation.structure import CONTENT_DIRS, _parse_frontmatter

        with self._conn() as conn:
            conn.execute("DELETE FROM wiki_fts")
            conn.execute("DELETE FROM wiki_meta")
        if self._semantic:
            try:
                with self._vec_conn() as conn:
                    conn.execute("DELETE FROM wiki_vec")
            except sqlite3.Error as exc:
                logger.debug("Embedding table clear failed: %s", exc)

        count = 0
        for content_dir in CONTENT_DIRS:
            dir_path = wiki_dir / content_dir
            if not dir_path.exists():
                continue
            for md_file in dir_path.glob("*.md"):
                content = md_file.read_text(encoding="utf-8")
                fm = _parse_frontmatter(content)
                self.index_page(
                    stem=md_file.stem,
                    title=fm.get("title", md_file.stem),
                    content=content,
                    content_type=fm.get("type", "unknown"),
                    domains=fm.get("domains", []),
                    tags=fm.get("tags", []),
                    word_count=len(content.split()),
                    path=f"{content_dir}/{md_file.name}",
                    mtime=md_file.stat().st_mtime,
                )
                count += 1

        logger.info("Rebuilt search index: %d pages", count)
        return count
