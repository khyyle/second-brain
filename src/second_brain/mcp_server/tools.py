"""MCP tool implementations for wiki access."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from second_brain.compilation.structure import (
    CONTENT_DIRS,
    LinkGraph,
    WikiPage,
    _parse_frontmatter,
    build_link_graph,
    discover_all_pages,
)
from second_brain.mcp_server.search import SearchIndex

logger = logging.getLogger(__name__)

SYNC_MIN_INTERVAL_SECONDS = 1.0

# Default caps on how much a single tool returns, so a large wiki can't flood
# the caller's context window. Each capped tool also reports the total and how
# to fetch more, so a partial result is never silently mistaken for the whole.
DEFAULT_LIST_LIMIT = 50
DEFAULT_RELATED_LIMIT = 50
DEFAULT_SOURCES_LIMIT = 10
MAX_INDEX_CHARS = 8000


def _more_note(shown: int, total: int, *, offset: int = 0, pageable: bool = True) -> str:
    """A trailing coverage line, or '' when the whole result is shown.

    When ``pageable``, the note tells the caller the next ``offset`` to fetch;
    otherwise it just reports how many more were withheld (for a capped result
    with no stable paging order, like graph traversal).
    """
    if offset + shown >= total:
        return "" if offset == 0 else f"\n\n(showing {offset + 1}-{offset + shown} of {total})"
    if pageable:
        return (
            f"\n\n(showing {offset + 1}-{offset + shown} of {total}; "
            f"pass offset={offset + shown} for more)"
        )
    return f"\n\n(showing {shown} of {total}; raise limit to see more)"


def _source_preview(text: str) -> str:
    """Return a source's frontmatter block plus its first body paragraph.

    Parameters
    ----------
    text: str
        Full Markdown text of a raw source file.

    Returns
    -------
    str
        The leading ``---`` frontmatter block (when present) followed by
        the first non-empty paragraph of the body.
    """
    body = text
    header = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            header = text[: end + 4].rstrip()
            body = text[end + 4 :]
    for chunk in body.split("\n\n"):
        lead = chunk.strip()
        if lead:
            return f"{header}\n\n{lead}".strip()
    return header or "(empty source)"


class _GraphCache:
    """In-memory cache of the wiki link graph, invalidated by mtime.

    Avoids rebuilding the full link graph on every MCP tool call by
    tracking the latest modification time across wiki content dirs and
    rate-limiting filesystem stat checks to once per second.
    """

    def __init__(self, wiki_dir: Path) -> None:
        """
        Initialize the cache for a wiki directory.

        Parameters
        ----------
        wiki_dir: Path
            Root directory of the compiled wiki.
        """
        self._wiki_dir = wiki_dir
        self._pages: dict[str, WikiPage] = {}
        self._graph: LinkGraph | None = None
        self._last_mtime: float = 0.0
        self._last_check: float = 0.0

    def _current_mtime(self) -> float:
        """
        Get the most recent mtime across all wiki content dirs.

        Includes each content directory's own mtime, not just its files', so a
        deletion is detected: removing a page bumps the directory's mtime but
        may leave the max file mtime unchanged.

        Returns
        -------
        float
            Latest ``st_mtime`` found, or ``0.0`` if no files exist.
        """
        latest = 0.0
        for content_dir in CONTENT_DIRS:
            d = self._wiki_dir / content_dir
            if not d.exists():
                continue
            latest = max(latest, d.stat().st_mtime)
            for f in d.rglob("*.md"):
                latest = max(latest, f.stat().st_mtime)
        return latest

    def get(self) -> tuple[dict[str, WikiPage], LinkGraph]:
        """
        Return the cached page map and link graph.

        Rebuilds only when wiki files have changed. Filesystem checks
        are rate-limited to at most once per second.

        Returns
        -------
        pages: dict[str, WikiPage]
            Mapping of stem to WikiPage.
        graph: LinkGraph
            Forward and backward link adjacency sets.
        """
        now = time.monotonic()
        if now - self._last_check < 1.0 and self._graph is not None:
            return self._pages, self._graph

        self._last_check = now
        current = self._current_mtime()

        if current != self._last_mtime or self._graph is None:
            self._pages = discover_all_pages(self._wiki_dir)
            self._graph = build_link_graph(self._pages)
            self._last_mtime = current
            logger.debug("Graph cache rebuilt: %d pages", len(self._pages))

        return self._pages, self._graph


class WikiTools:
    """Implements the tool functions exposed via MCP.

    Acts as the service layer between the MCP server endpoints and the
    underlying search index, filesystem, and link graph. Holds a
    ``_GraphCache`` for efficient repeated graph queries.
    """

    def __init__(self, wiki_dir: Path, raw_dir: Path, search_index: SearchIndex) -> None:
        """
        Initialize WikiTools with directory paths and a search index.

        Parameters
        ----------
        wiki_dir: Path
            Root directory of the compiled wiki.
        raw_dir: Path
            Root directory of raw ingested source documents.
        search_index: SearchIndex
            Shared search index instance for FTS queries.
        """
        self._wiki = wiki_dir
        self._raw = raw_dir
        self._search = search_index
        self._cache = _GraphCache(wiki_dir)
        self._last_sync_check = 0.0
        self._last_sync_mtime = -1.0
        self._embed_lock = threading.Lock()
        self._embed_thread: threading.Thread | None = None

    def ensure_synced(self) -> None:
        """Refresh the search index from disk when wiki files have changed.

        Lets newly compiled or edited pages become searchable mid-session
        without restarting the server. The keyword/metadata sync is fast
        and runs inline (rate-limited by a directory-stat check), so
        ``search_wiki`` and ``list_pages`` stay correct and responsive.
        Embeddings are refreshed on a background thread so Ollama latency
        never blocks a tool call.
        """
        now = time.monotonic()
        if now - self._last_sync_check < SYNC_MIN_INTERVAL_SECONDS:
            return
        self._last_sync_check = now

        current_mtime = self._latest_mtime()
        if current_mtime == self._last_sync_mtime:
            return

        self._search.sync_from_wiki(self._wiki)
        self._last_sync_mtime = current_mtime
        self._start_background_embed()

    def _start_background_embed(self) -> None:
        """Embed pending pages on a daemon thread if one is not running.

        Keeps embedding (and its Ollama round-trips) entirely off the
        request path. At most one embed pass runs at a time; pages that
        change while a pass is in flight are picked up by the next call.
        """
        if not self._search.semantic_enabled:
            return
        with self._embed_lock:
            if self._embed_thread is not None and self._embed_thread.is_alive():
                return
            self._embed_thread = threading.Thread(target=self._search.embed_pending, daemon=True)
            self._embed_thread.start()

    def _latest_mtime(self) -> float:
        """Return the newest mtime across wiki content dirs and their files.

        Directory mtimes are included so file additions and deletions are
        detected, not just edits to existing files.
        """
        latest = 0.0
        for content_dir in CONTENT_DIRS:
            dir_path = self._wiki / content_dir
            if not dir_path.exists():
                continue
            latest = max(latest, dir_path.stat().st_mtime)
            for md_file in dir_path.rglob("*.md"):
                latest = max(latest, md_file.stat().st_mtime)
        return latest

    def search_wiki(self, query: str, limit: int = 10) -> str:
        """
        Search wiki pages and return formatted results.

        Parameters
        ----------
        query: str
            FTS5 search expression.
        limit: int
            Maximum number of results.

        Returns
        -------
        str
            Markdown-formatted search results or a no-results message.
        """
        hits = self._search.search(query, limit=limit)
        if not hits:
            return "No results found."

        results = []
        for h in hits:
            results.append(
                f"**{h.title}** ({h.content_type})\n"
                f"  Domains: {', '.join(h.domains)}\n"
                f"  {h.snippet}\n"
            )
        return "\n---\n".join(results)

    def semantic_search(self, query: str, limit: int = 10) -> str:
        """
        Semantic (embedding) search over wiki pages.

        Parameters
        ----------
        query: str
            Natural-language query.
        limit: int
            Maximum number of results.

        Returns
        -------
        str
            Markdown-formatted results, or a clear notice when the
            semantic layer is unavailable so the caller can use
            ``search_wiki`` instead.
        """
        if not self._search.semantic_enabled:
            return (
                "Semantic search is unavailable (the embedding layer is off or "
                "Ollama is not running). Use search_wiki for keyword search instead."
            )

        hits = self._search.semantic_search(query, limit=limit)
        if not hits:
            return (
                "No semantic results (the embedding index may be empty or Ollama "
                "is not running). Try search_wiki instead."
            )

        results = []
        for h in hits:
            results.append(
                f"**{h.title}** ({h.content_type})\n"
                f"  Domains: {', '.join(h.domains)}\n"
                f"  {h.snippet}\n"
            )
        return "\n---\n".join(results)

    def read_page(self, title: str) -> str:
        """
        Read a wiki page by stem or title.

        Parameters
        ----------
        title: str
            Page title or slug to look up.

        Returns
        -------
        str
            Full markdown content, or a not-found message.
        """
        slug = title.lower().replace(" ", "-")
        for content_dir in CONTENT_DIRS:
            path = self._wiki / content_dir / f"{slug}.md"
            if path.exists():
                return path.read_text(encoding="utf-8")

        return f"Page not found: {title}"

    def list_pages(
        self,
        domain: str | None = None,
        tag: str | None = None,
        content_type: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> str:
        """
        List wiki pages with optional filters.

        Parameters
        ----------
        domain: str | None
            Filter by knowledge domain.
        tag: str | None
            Filter by tag.
        content_type: str | None
            Filter by content type.
        limit: int
            Maximum pages to return in this call.
        offset: int
            Number of leading matches to skip, for paging.

        Returns
        -------
        str
            Newline-separated matching pages, with a coverage note when the
            result is paged.
        """
        pages = self._search.list_pages(domain=domain, content_type=content_type, tag=tag)
        total = len(pages)
        if total == 0:
            return "No pages match the given filters."

        offset = max(offset, 0)
        window = pages[offset : offset + max(limit, 1)]
        if not window:
            return f"No pages at offset {offset} (total {total})."

        lines = [f"- {p['title']} ({p['content_type']}) — {p['path']}" for p in window]
        return "\n".join(lines) + _more_note(len(window), total, offset=offset)

    def read_index(self) -> str:
        """
        Read the master index view.

        The index grows with the wiki, so it is truncated past a character
        budget with a pointer to browse by filter instead of returning an
        unbounded dump.

        Returns
        -------
        str
            Index markdown content (possibly truncated), or a prompt to run
            compilation.
        """
        index_path = self._wiki / "_views" / "index.md"
        if not index_path.exists():
            return "Index not yet generated. Run compilation first."

        text = index_path.read_text(encoding="utf-8")
        if len(text) <= MAX_INDEX_CHARS:
            return text
        return (
            text[:MAX_INDEX_CHARS]
            + f"\n\n[index truncated at {MAX_INDEX_CHARS} of {len(text)} chars — "
            "use list_pages(domain=...) or search to browse specific pages]"
        )

    def capture_note(
        self,
        content: str,
        title: str | None = None,
        topic: str | None = None,
    ) -> str:
        """
        Capture chat content as a source document for the pipeline.

        Writes a markdown file into the ``drops/documents`` intake queue so
        the content flows through the normal ingest -> triage -> compile
        path, gaining a real source entry and quality gating, rather than
        authoring a finished wiki page directly. The wiki stays a single
        compiled artifact with one source of truth.

        Parameters
        ----------
        content: str
            The raw text to capture.
        title: str | None
            Short title; the first line of *content* is used if omitted.
        topic: str | None
            Optional hint recorded in frontmatter to guide compilation.

        Returns
        -------
        str
            Confirmation with the relative path of the captured note.
        """
        text = content.strip()
        if not text:
            return "Nothing to capture: content is empty."

        heading = (title or text.split("\n", 1)[0]).strip()[:80] or "captured note"
        slug = "".join(c if c.isalnum() or c in " -" else "" for c in heading.lower())
        slug = "-".join(slug.split()) or "note"

        captured_at = datetime.now(tz=UTC)
        drops_documents = self._raw.parent / "drops" / "documents"
        drops_documents.mkdir(parents=True, exist_ok=True)
        path = drops_documents / f"{captured_at:%Y-%m-%d-%H%M%S}-{slug}.md"

        frontmatter = [
            "---",
            f'title: "{heading}"',
            "origin: chat-capture",
            f"captured_at: {captured_at.isoformat()}",
        ]
        if topic:
            frontmatter.append(f'suggested_topic: "{topic}"')
        frontmatter.append("---")

        path.write_text("\n".join(frontmatter) + "\n\n" + text + "\n", encoding="utf-8")
        return (
            f"Captured to {path.relative_to(self._raw.parent)}. "
            "It will be triaged and compiled into the wiki on the next build."
        )

    def _resolve_source(self, src: str) -> Path | None:
        """Resolve a frontmatter source reference to an existing raw file.

        Source references are written relative to the data directory (e.g.
        ``raw/documents/foo.md``), but may also appear relative to the raw
        directory or as a bare filename. Each interpretation is tried in
        turn, falling back to a recursive search by filename.

        Parameters
        ----------
        src: str
            The source path as stored in a page's frontmatter.

        Returns
        -------
        Path | None
            The first matching file on disk, or ``None`` if none match.
        """
        data_dir = self._raw.parent
        candidates = [data_dir / src, self._raw / src]
        if src.startswith("raw/"):
            candidates.append(self._raw / src[len("raw/") :])
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        matches = list(self._raw.rglob(Path(src).name))
        return matches[0] if matches else None

    def get_sources(self, title: str, limit: int = DEFAULT_SOURCES_LIMIT, offset: int = 0) -> str:
        """
        Retrieve raw source documents for a wiki page.

        Parameters
        ----------
        title: str
            Page title or slug whose sources to look up.
        limit: int
            Maximum number of sources to return in this call.
        offset: int
            Number of leading sources to skip, for paging a many-source page.

        Returns
        -------
        str
            Concatenated source previews (first 2000 chars each), with a
            coverage note when paged, or a not-found / no-sources message.
        """
        slug = title.lower().replace(" ", "-")
        for content_dir in CONTENT_DIRS:
            path = self._wiki / content_dir / f"{slug}.md"
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            fm = _parse_frontmatter(content)
            sources = fm.get("sources", [])
            if not sources:
                return f"No sources listed for {title}."

            offset = max(offset, 0)
            window = sources[offset : offset + max(limit, 1)]
            results = []
            for src in window:
                resolved = self._resolve_source(src)
                if resolved:
                    results.append(f"### {src}\n{resolved.read_text(encoding='utf-8')[:2000]}")
                else:
                    results.append(f"### {src}\n(source file not found)")
            return "\n\n".join(results) + _more_note(len(window), len(sources), offset=offset)

        return f"Page not found: {title}"

    def get_sources_summary(self, title: str) -> str:
        """
        Summarize a page's sources with frontmatter and opening text only.

        Returns each source's frontmatter block and first paragraph rather
        than its full body, so a caller can judge relevance cheaply before
        pulling complete sources with ``get_sources``.

        Parameters
        ----------
        title: str
            Page title or slug whose sources to summarize.

        Returns
        -------
        str
            Per-source previews, or a not-found / no-sources message.
        """
        slug = title.lower().replace(" ", "-")
        for content_dir in CONTENT_DIRS:
            path = self._wiki / content_dir / f"{slug}.md"
            if not path.exists():
                continue
            fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
            sources = fm.get("sources", [])
            if not sources:
                return f"No sources listed for {title}."

            results = []
            for src in sources:
                resolved = self._resolve_source(src)
                if not resolved:
                    results.append(f"### {src}\n(source file not found)")
                    continue
                preview = _source_preview(resolved.read_text(encoding="utf-8"))
                results.append(f"### {src}\n{preview}")
            return "\n\n".join(results)

        return f"Page not found: {title}"

    def find_related(self, title: str, depth: int = 2, limit: int = DEFAULT_RELATED_LIMIT) -> str:
        """
        Find pages related to a concept via backlink graph traversal.

        Parameters
        ----------
        title: str
            Page title or slug to start from.
        depth: int
            Number of link hops to traverse.
        limit: int
            Maximum number of related pages to return; the reachable set can
            grow combinatorially with depth, so it is capped.

        Returns
        -------
        str
            Wikilink list of related pages, with a count note when capped, or
            a not-found message.
        """
        slug = title.lower().replace(" ", "-")
        pages, graph = self._cache.get()

        if slug not in pages:
            return f"Page not found: {title}"

        related: set[str] = set()
        frontier = {slug}

        for _ in range(depth):
            next_frontier: set[str] = set()
            for node in frontier:
                next_frontier.update(graph.forward.get(node, set()))
                next_frontier.update(graph.backward.get(node, set()))
            next_frontier -= related
            next_frontier.discard(slug)
            related.update(next_frontier)
            frontier = next_frontier

        if not related:
            return f"No related pages found for {title}."

        ordered = sorted(related)
        window = ordered[: max(limit, 1)]
        lines = []
        for stem in window:
            page = pages.get(stem)
            if page:
                t = page.frontmatter.get("title", stem)
                lines.append(f"- [[{stem}|{t}]]")
            else:
                lines.append(f"- [[{stem}]] (gap)")
        return "\n".join(lines) + _more_note(len(window), len(ordered), pageable=False)
