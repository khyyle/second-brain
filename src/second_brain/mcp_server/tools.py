"""MCP tool implementations for wiki access."""

from __future__ import annotations

import logging
import time
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
            for f in d.glob("*.md"):
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

    def invalidate(self) -> None:
        """
        Force a full rebuild on the next access.

        Notes
        -----
        Methods like ``WikiTools.update_page()`` call this to force a rebuild.
        At the expected scale of a personal wiki, a full rebuild is cheaper
        than tracking incremental graph deltas.
        """
        self._graph = None
        self._last_mtime = 0.0


class WikiTools:
    """Implements the tool functions exposed via MCP.

    Acts as the service layer between the MCP server endpoints and the
    underlying search index, filesystem, and link graph. Holds a
    ``_GraphCache`` for efficient repeated graph queries.
    """

    def __init__(
        self, wiki_dir: Path, raw_dir: Path, search_index: SearchIndex
    ) -> None:
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

    def ensure_synced(self) -> None:
        """Refresh the search index from disk when wiki files have changed.

        Lets newly compiled or edited pages become searchable mid-session
        without restarting the server. Filesystem checks are rate-limited,
        and the underlying sync only re-embeds pages whose content changed,
        so the common (unchanged) case costs a directory stat.
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
            for md_file in dir_path.glob("*.md"):
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

        Returns
        -------
        str
            Newline-separated list of matching pages.
        """
        pages = self._search.list_pages(
            domain=domain, content_type=content_type, tag=tag
        )
        if not pages:
            return "No pages match the given filters."

        lines = []
        for p in pages:
            lines.append(
                f"- {p['title']} ({p['content_type']}) — {p['path']}"
            )
        return "\n".join(lines)

    def read_index(self) -> str:
        """
        Read the master index view.

        Returns
        -------
        str
            Index markdown content, or a prompt to run compilation.
        """
        index_path = self._wiki / "_views" / "index.md"
        if index_path.exists():
            return index_path.read_text(encoding="utf-8")
        return "Index not yet generated. Run compilation first."

    def update_page(self, title: str, content: str) -> str:
        """
        Overwrite an existing wiki page and re-index it.

        Parameters
        ----------
        title: str
            Page title or slug to update.
        content: str
            New full markdown content (including frontmatter).

        Returns
        -------
        str
            Confirmation message or a not-found message.
        """
        slug = title.lower().replace(" ", "-")
        for content_dir in CONTENT_DIRS:
            path = self._wiki / content_dir / f"{slug}.md"
            if path.exists():
                path.write_text(content, encoding="utf-8")
                fm = _parse_frontmatter(content)
                self._search.index_page(
                    stem=slug,
                    title=fm.get("title", slug),
                    content=content,
                    content_type=fm.get("type", "unknown"),
                    domains=fm.get("domains", []),
                    tags=fm.get("tags", []),
                    word_count=len(content.split()),
                    path=f"{content_dir}/{slug}.md",
                    mtime=path.stat().st_mtime,
                )
                self._cache.invalidate()
                return f"Updated: {path.relative_to(self._wiki)}"

        return f"Page not found: {title}"

    def create_page(
        self,
        title: str,
        content: str,
        content_type: str = "concept",
    ) -> str:
        """
        Create a new wiki page, index it, and invalidate the cache.

        Parameters
        ----------
        title: str
            Human-readable title (slugified for the filename).
        content: str
            Full markdown content (including frontmatter).
        content_type: str
            Page type used to choose the target directory.

        Returns
        -------
        str
            Confirmation with the relative path, or an error message.
        """
        from second_brain.compilation.schema import load_schema

        schema = load_schema(self._wiki)
        if content_type not in schema.valid_types:
            return f"Invalid content type: {content_type}"

        slug = title.lower().replace(" ", "-")
        directory = schema.directory_for_type(content_type)
        path = self._wiki / directory / f"{slug}.md"

        if path.exists():
            return f"Page already exists: {path.relative_to(self._wiki)}"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        fm = _parse_frontmatter(content)
        self._search.index_page(
            stem=slug,
            title=fm.get("title", title),
            content=content,
            content_type=content_type,
            domains=fm.get("domains", []),
            tags=fm.get("tags", []),
            word_count=len(content.split()),
            path=f"{directory}{slug}.md",
            mtime=path.stat().st_mtime,
        )
        self._cache.invalidate()
        return f"Created: {path.relative_to(self._wiki)}"

    def get_sources(self, title: str) -> str:
        """
        Retrieve raw source documents for a wiki page.

        Parameters
        ----------
        title: str
            Page title or slug whose sources to look up.

        Returns
        -------
        str
            Concatenated source previews (first 2000 chars each),
            or a not-found / no-sources message.
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

            results = []
            for src in sources:
                matches = list(self._raw.rglob(src))
                if matches:
                    results.append(
                        f"### {src}\n"
                        f"{matches[0].read_text(encoding='utf-8')[:2000]}"
                    )
                else:
                    results.append(f"### {src}\n(source file not found)")
            return "\n\n".join(results)

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
                matches = list(self._raw.rglob(src))
                if not matches:
                    results.append(f"### {src}\n(source file not found)")
                    continue
                preview = _source_preview(matches[0].read_text(encoding="utf-8"))
                results.append(f"### {src}\n{preview}")
            return "\n\n".join(results)

        return f"Page not found: {title}"
    
    

    def find_related(self, title: str, depth: int = 2) -> str:
        """
        Find pages related to a concept via backlink graph traversal.

        Parameters
        ----------
        title: str
            Page title or slug to start from.
        depth: int
            Number of link hops to traverse.

        Returns
        -------
        str
            Wikilink list of related pages, or a not-found message.
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

        lines = []
        for stem in sorted(related):
            page = pages.get(stem)
            if page:
                t = page.frontmatter.get("title", stem)
                lines.append(f"- [[{stem}|{t}]]")
            else:
                lines.append(f"- [[{stem}]] (gap)")
        return "\n".join(lines)
