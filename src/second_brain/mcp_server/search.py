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
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from second_brain.config import SearchConfig
from second_brain.wiki.structure import LINK_KINDS, extract_typed_edges

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_SECONDS = 5.0


def _sanitize_fts_query(query: str) -> str:
    """Turn arbitrary text into a safe FTS5 MATCH expression.

    Extracts alphanumeric terms and double-quotes each one, so punctuation,
    bare operators, or unbalanced quotes in natural-language input cannot
    produce an FTS5 syntax error.

    Parameters
    ----------
    query: str
        Raw user/agent query text.

    Returns
    -------
    str
        A space-separated list of quoted terms (implicit AND), or an empty
        string if the input has no alphanumeric terms.
    """
    return " ".join(f'"{term}"' for term in re.findall(r"\w+", query))


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
                    mtime REAL,
                    embedded_hash TEXT
                )
            """)
            # for a given wiki_link:
            # - `source` is the page that declares the link
            # - `target` is what it points at.
            # for example, 'prerequisite' edges run from the dependent
            # page (source) to the concept it needs (target).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wiki_links (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    PRIMARY KEY (source, target, kind)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_links_source ON wiki_links(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_links_target ON wiki_links(target)")

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
        conn = sqlite3.connect(str(self._db_path), timeout=BUSY_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        self._apply_pragmas(conn)
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

        conn = sqlite3.connect(str(self._db_path), timeout=BUSY_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        self._apply_pragmas(conn)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _apply_pragmas(conn: sqlite3.Connection) -> None:
        """Enable WAL and a busy timeout so the request path and the
        background embedder can read and write the same database
        concurrently without spurious 'database is locked' failures.
        """
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")

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

        Does not compute embeddings: the page is left marked as pending
        (its ``embedded_hash`` is cleared) so the semantic layer is
        refreshed off the request path by :meth:`embed_pending`.

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
                "content_hash, mtime, embedded_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
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
            # delete every edge this page declares
            conn.execute("DELETE FROM wiki_links WHERE source = ?", (stem,))
            # then insert its current set
            conn.executemany(
                "INSERT OR IGNORE INTO wiki_links (source, target, kind) VALUES (?, ?, ?)",
                [(stem, target, kind) for target, kind in extract_typed_edges(content)],
            )

    def embed_pending(self, limit: int | None = None) -> int:
        """
        Embed pages whose stored embedding is missing or stale.

        A page is pending when its ``embedded_hash`` differs from its
        current ``content_hash`` (cleared whenever the page is indexed).
        This is the only place embeddings are computed, and it is meant to
        run off the request path (e.g. a background thread) so Ollama
        latency never blocks tool calls. Best-effort: if the embedder is
        unavailable it stops early, leaving the rest pending for a later
        pass.

        Parameters
        ----------
        limit: int | None
            Maximum number of pages to embed in this pass; ``None`` embeds
            all pending pages.

        Returns
        -------
        int
            Number of pages embedded.
        """
        if not self._semantic or self._search_config is None:
            return 0

        from sqlite_vec import serialize_float32

        from second_brain.mcp_server.embeddings import embed_text

        query = (
            "SELECT m.stem, m.title, m.content_hash, f.content "
            "FROM wiki_meta m JOIN wiki_fts f ON f.stem = m.stem "
            "WHERE m.embedded_hash IS NULL OR m.embedded_hash != m.content_hash"
        )
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        with self._conn() as conn:
            pending = conn.execute(query, params).fetchall()

        embedded_count = 0
        for row in pending:
            vector = embed_text(f"{row['title']}\n\n{row['content']}", self._search_config)
            if vector is None:
                # Embedder unavailable; leave the rest pending for next pass.
                break
            try:
                with self._vec_conn() as conn:
                    conn.execute("DELETE FROM wiki_vec WHERE stem = ?", (row["stem"],))
                    conn.execute(
                        "INSERT INTO wiki_vec (stem, embedding) VALUES (?, ?)",
                        (row["stem"], serialize_float32(vector)),
                    )
                with self._conn() as conn:
                    # Guard on content_hash so a concurrent re-index (which
                    # clears embedded_hash) is not masked as embedded here.
                    conn.execute(
                        "UPDATE wiki_meta SET embedded_hash = ? "
                        "WHERE stem = ? AND content_hash = ?",
                        (row["content_hash"], row["stem"], row["content_hash"]),
                    )
                embedded_count += 1
            except sqlite3.Error as exc:
                logger.debug("Embedding store failed for %s: %s", row["stem"], exc)

        if embedded_count:
            logger.info("Embedded %d pending page(s)", embedded_count)
        return embedded_count

    def _run_fts(self, match_expr: str, limit: int) -> list[sqlite3.Row] | None:
        """Run one FTS5 MATCH query; return ``None`` on a syntax error."""
        try:
            with self._conn() as conn:
                return conn.execute(
                    """SELECT stem, title,
                              snippet(wiki_fts, 2, '<b>', '</b>', '...', 40) as snip,
                              rank, content_type, domains
                       FROM wiki_fts
                       WHERE wiki_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (match_expr, limit),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.debug("FTS query rejected (%s): %s", match_expr, exc)
            return None

    def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        """
        Keyword search using FTS5 with BM25 ranking.

        The raw query is tried first so FTS5 operators (``AND``, ``OR``,
        ``NEAR``, quoted phrases) keep working. If it is not valid FTS5
        syntax, the query is sanitized into quoted terms and retried, so a
        natural-language query never raises and never silently returns a
        misleading empty result for a recoverable input.

        Parameters
        ----------
        query: str
            FTS5 match expression or plain text.
        limit: int
            Maximum number of results to return.

        Returns
        -------
        list[SearchHit]
            Hits ordered by descending relevance score.
        """
        rows = self._run_fts(query, limit)
        if rows is None:
            sanitized = _sanitize_fts_query(query)
            rows = self._run_fts(sanitized, limit) or [] if sanitized else []

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

    @staticmethod
    def _kind_filter(kinds: tuple[str, ...] | None) -> tuple[str, list[str]]:
        """
        Build the SQL fragment and params that restrict a query to ``kinds``.

        The fragment extends an existing ``WHERE`` clause, and is empty when
        ``kinds`` is ``None`` (every kind allowed).

        Parameters
        ----------
        kinds: tuple[str, ...] | None
            Edge kinds to keep; each must be one of
            ``second_brain.wiki.structure.LINK_KINDS``.

        Returns
        -------
        clause: str
            A trailing ``" AND kind IN (...)"`` fragment, or ``""``.
        params: list[str]
            The kind values to bind, positionally matching the fragment.

        Raises
        ------
        ValueError
            If ``kinds`` contains a value that is not a known edge kind.
        """
        if not kinds:
            return "", []
        unknown = set(kinds) - set(LINK_KINDS)
        if unknown:
            raise ValueError(
                f"Unknown edge kind(s): {sorted(unknown)}. Valid kinds: {list(LINK_KINDS)}"
            )
        return f" AND kind IN ({','.join('?' * len(kinds))})", list(kinds)

    def neighbors(
        self,
        stems: set[str],
        kinds: tuple[str, ...] | None = None,
        following: str = "both",
    ) -> set[str]:
        """
        Find the stems one edge away from any of ``stems`` in the link graph.

        Parameters
        ----------
        stems: set[str]
            Page stems to walk out from.
        kinds: tuple[str, ...] | None, default=None
            Edge kinds to traverse; each must be one of
            ``second_brain.wiki.structure.LINK_KINDS``. ``None`` traverses
            every kind.
        following: str
            Which end of each edge to walk toward: ``"targets"`` for what a
            page points at (e.g. its prerequisites), ``"sources"`` for what
            points at it (e.g. its dependents), or ``"both"`` for the union.

        Returns
        -------
        set[str]
            The reached stems, which may include members of ``stems`` (a mutual
            link) and gaps (a stem with no page).

        Raises
        ------
        ValueError
            If ``kinds`` has an unknown edge kind, or ``following`` is not
            ``"targets"``, ``"sources"``, or ``"both"``.
        """
        if not stems:
            return set()
        if following not in ("targets", "sources", "both"):
            raise ValueError(
                f"following must be 'targets', 'sources', or 'both', got '{following}'"
            )
        kind_sql, kind_params = self._kind_filter(kinds)
        placeholders = ",".join("?" * len(stems))

        reached: set[str] = set()
        with self._conn() as conn:
            if following in ("targets", "both"):
                outgoing = conn.execute(
                    f"SELECT target FROM wiki_links WHERE source IN ({placeholders}){kind_sql}",
                    [*stems, *kind_params],
                ).fetchall()
                reached.update(row["target"] for row in outgoing)

            if following in ("sources", "both"):
                incoming = conn.execute(
                    f"SELECT source FROM wiki_links WHERE target IN ({placeholders}){kind_sql}",
                    [*stems, *kind_params],
                ).fetchall()
                reached.update(row["source"] for row in incoming)
        return reached

    def edges_from(
        self, stems: set[str], kinds: tuple[str, ...] | None = None
    ) -> list[tuple[str, str]]:
        """
        Read the edges that originate at any of ``stems``.

        Unlike :meth:`neighbors`, this keeps each edge's endpoints paired, so a
        caller can rebuild the local subgraph rather than a flat neighbor set.

        Parameters
        ----------
        stems: set[str]
            Stems to read outgoing edges from; each is matched as an edge source.
        kinds: tuple[str, ...] | None
            Edge kinds to include; each must be one of
            ``second_brain.wiki.structure.LINK_KINDS``. ``None`` includes every
            kind.

        Returns
        -------
        list[tuple[str, str]]
            ``(source, target)`` pairs, where the target may be a gap (no page).

        Raises
        ------
        ValueError
            If ``kinds`` contains a value that is not a known edge kind.
        """
        if not stems:
            return []
        kind_sql, kind_params = self._kind_filter(kinds)
        placeholders = ",".join("?" * len(stems))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT source, target FROM wiki_links "
                f"WHERE source IN ({placeholders}){kind_sql}",
                [*stems, *kind_params],
            ).fetchall()
        return [(row["source"], row["target"]) for row in rows]

    def page_titles(self, stems: set[str]) -> dict[str, str]:
        """
        Find the title of each stem that is a real page.

        Parameters
        ----------
        stems: set[str]
            Page stems to look up.

        Returns
        -------
        dict[str, str]
            ``{stem: title}`` for the stems that resolve to a page.
        """
        if not stems:
            return {}
        placeholders = ",".join("?" * len(stems))

        with self._conn() as conn:
            titles = conn.execute(
                f"SELECT stem, title FROM wiki_meta WHERE stem IN ({placeholders})", list(stems)
            ).fetchall()
        return {row["stem"]: row["title"] for row in titles}

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
        """Remove a page from the FTS, metadata, link, and vector tables"""
        with self._conn() as conn:
            conn.execute("DELETE FROM wiki_fts WHERE stem = ?", (stem,))
            conn.execute("DELETE FROM wiki_meta WHERE stem = ?", (stem,))
            # only delete edges coming out of the page
            conn.execute("DELETE FROM wiki_links WHERE source = ?", (stem,))
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
            conn.execute("UPDATE wiki_meta SET mtime = ? WHERE stem = ?", (mtime, stem))

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
        from second_brain.wiki.structure import CONTENT_DIRS, _parse_frontmatter

        with self._conn() as conn:
            rows = conn.execute("SELECT stem, content_hash, mtime FROM wiki_meta").fetchall()
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
        from second_brain.wiki.structure import CONTENT_DIRS, _parse_frontmatter

        with self._conn() as conn:
            conn.execute("DELETE FROM wiki_fts")
            conn.execute("DELETE FROM wiki_meta")
            conn.execute("DELETE FROM wiki_links")
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
