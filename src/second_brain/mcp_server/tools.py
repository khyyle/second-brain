"""MCP tool implementations for wiki access."""

from __future__ import annotations

import heapq
import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from second_brain.mcp_server.search import SearchIndex
from second_brain.wiki.structure import CONTENT_DIRS, _parse_frontmatter

logger = logging.getLogger(__name__)

SYNC_MIN_INTERVAL_SECONDS = 1.0

# Default caps on how much a single tool returns, so a large wiki can't flood
# the caller's context window. Each capped tool also reports the total and how
# to fetch more, so a partial result is never silently mistaken for the whole.
DEFAULT_SEARCH_LIMIT = 10
DEFAULT_LIST_LIMIT = 50
DEFAULT_RELATED_LIMIT = 50
DEFAULT_RELATED_DEPTH = 2
DEFAULT_SOURCES_LIMIT = 10
DEFAULT_GAPS_LIMIT = 50
DEFAULT_PREREQUISITE_DEPTH = 6
INDEX_PAGES_PER_DOMAIN = 10
MAX_SOURCE_LENGTH_CHARS = 2000


def _more_note(shown: int, total: int, *, offset: int = 0, pageable: bool = True) -> str:
    """A trailing coverage line, or '' when the whole result is shown.

    When ``pageable``, the note tells the caller the next ``offset`` to fetch.
    Otherwise it just reports how many more were withheld (for a capped result
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
    """Grab a source's frontmatter block plus its first body paragraph.

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


def _wikilink(stem: str, titles: dict[str, str]) -> str:
    """Render a stem as a wikilink, marking it a gap when no page resolves it."""
    return f"[[{stem}|{titles[stem]}]]" if stem in titles else f"[[{stem}]] (gap)"


def _topological_order(
    nodes: set[str],
    prerequisites_of: dict[str, set[str]],
) -> tuple[list[str], list[str]]:
    """
    Order ``nodes`` so each comes after every prerequisite it depends on.

    Parameters
    ----------
    nodes: set[str]
        Every stem to order, including prerequisite gaps that have no page.
    prerequisites_of: dict[str, set[str]]
        Each stem mapped to the stems it directly depends on.

    Returns
    -------
    ordered: list[str]
        Stems fundamentals-first; mutually independent stems fall in
        alphabetical order so the result is reproducible.
    cyclic: list[str]
        Stems left out because a dependency cycle never lets their pending
        prerequisite count reach zero; empty when the input is acyclic.
    """
    # implemented via Kahn's algorithm

    # map each node to the prerequisites it still waits on, ignoring any outside the closure
    pending = {node: set(prerequisites_of.get(node, set())) & nodes for node in nodes}

    # map each prerequisite to the nodes waiting on it
    waiting_on: dict[str, set[str]] = defaultdict(set)
    for node, prerequisites in pending.items():
        for prerequisite in prerequisites:
            waiting_on[prerequisite].add(node)

    # initialize the queue with nodes that don't have prerequisites
    ready = [node for node, prerequisites in pending.items() if not prerequisites]
    heapq.heapify(ready)

    # topo-sorted list we'll add to as we go
    ordered: list[str] = []
    while ready:
        node = heapq.heappop(ready)  # grab lexicographically next node
        ordered.append(node)

        # drop the emitted node from everything that was waiting on it
        for dependent in waiting_on.get(node, set()):
            pending[dependent].discard(node)

            # that was its last prerequisite, so it's ready now
            if not pending[dependent]:
                heapq.heappush(ready, dependent)

    # anything never emitted was stuck in a cycle
    cyclic = sorted(nodes - set(ordered))

    return ordered, cyclic


class WikiTools:
    """Implements the tool functions exposed via MCP.

    Acts as the service layer between the MCP server endpoints and the
    underlying search index, filesystem, and link graph.
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
            for md_file in dir_path.glob("*.md"):
                latest = max(latest, md_file.stat().st_mtime)
        return latest

    def search_wiki(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
        """
        Keyword-search wiki pages, ranked by BM25 relevance.

        Parameters
        ----------
        query: str
            An FTS5 match expression or plain text.
        limit: int
            Maximum number of pages to return.

        Returns
        -------
        str
            One block per matching page with its title, content type, domains, and a
            short excerpt of the body with the matched terms emphasized or a
            no-results message. Doesn't return full page bodies; read a page
            in full with ``read_page``.
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

    def semantic_search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
        """
        Find wiki pages by meaning, using embedding nearest-neighbor search.

        Matches on conceptual similarity rather than shared words, so it surfaces
        related pages even when the query wording does not appear in them.

        Parameters
        ----------
        query: str
            A natural-language query to match by meaning.
        limit: int
            Maximum number of pages to return.

        Returns
        -------
        str
            One block per matching page with its title, content type, domains, and a
            relevance distance (closest first). Falls back to a notice pointing at
            ``search_wiki`` when the semantic layer is unavailable.
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

    def _resolve_page(self, title: str) -> Path | None:
        """Resolve a title/slug to its page file, or None if no page matches."""
        # the wiki is flat by design, probe <content_dir>/<slug>.md
        slug = title.lower().replace(" ", "-")
        for content_dir in CONTENT_DIRS:
            path = self._wiki / content_dir / f"{slug}.md"
            if path.exists():
                return path
        return None

    def read_page(self, title: str) -> str:
        """
        Read a single wiki page in full by its title or slug.

        Parameters
        ----------
        title: str
            Page title or slug to look up.

        Returns
        -------
        str
            The complete page file (YAML frontmatter and markdown body) or a
            not-found message.
        """
        path = self._resolve_page(title)
        if path is not None:
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
        List wiki pages matching the given filters, ordered by title.

        Filters combine with AND--omitting all of them lists every page.

        Parameters
        ----------
        domain: str | None
            Keep only pages declaring this knowledge domain.
        tag: str | None
            Keep only pages carrying this tag.
        content_type: str | None
            Keep only pages of this type (e.g. ``concept``, ``problem``).
        limit: int
            Maximum pages to return in this call.
        offset: int
            Number of leading matches to skip, for paging.

        Returns
        -------
        str
            One line per page including its title, content type, and file path, followed
            by a coverage note when more pages match than this call returned, or a
            no-matches message.
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

    def read_index(self, pages_per_domain: int = INDEX_PAGES_PER_DOMAIN) -> str:
        """
        Summarize the whole wiki as pages grouped under each domain.

        Built live from the search index as opposed to the direct `_meta/index.md`,
        so it reflects the current wiki rather than the last compile snapshot.

        Parameters
        ----------
        pages_per_domain: int
            Maximum pages to list under each domain before linking onward.

        Returns
        -------
        str
            A header with the page and domain totals, then one section per domain
            (alphabetical) listing its pages as wikilinks and a ``+N more`` pointer
            to ``list_pages`` when a domain has more, or an empty-wiki notice.
        """
        pages = self._search.list_pages()
        if not pages:
            return "No pages yet. Run compilation first."

        # a page counts under every domain it declares, or "uncategorized" if it declares none
        by_domain: dict[str, list[dict]] = defaultdict(list)
        for page in pages:
            domains = [d.strip() for d in (page["domains"] or "").split(",") if d.strip()]
            for domain in domains or ["uncategorized"]:
                by_domain[domain].append(page)

        #
        lines = [f"# Wiki index — {len(pages)} pages across {len(by_domain)} domains", ""]
        for domain in sorted(by_domain):
            domain_pages = by_domain[domain]
            lines.append(f"## {domain} ({len(domain_pages)})")
            shown = domain_pages[: max(pages_per_domain, 1)]
            lines += [f"- [[{p['stem']}|{p['title']}]] ({p['content_type']})" for p in shown]
            if len(domain_pages) > len(shown):
                extra = len(domain_pages) - len(shown)
                lines.append(f'  (+{extra} more — list_pages(domain="{domain}"))')
            lines.append("")
        return "\n".join(lines).rstrip()

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
        Retrieve the raw source documents a wiki page was compiled from.

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
            Each source under its path heading, truncated to its first MAX_SOURCE_LENGTH_CHARS
            characters, followed by a coverage note when more sources remain, or
            a not-found / no-sources message. For judging relevance without the
            full text, use ``get_sources_summary``.
        """
        path = self._resolve_page(title)
        if path is None:
            return f"Page not found: {title}"
        fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
        sources = fm.get("sources", [])
        if not sources:
            return f"No sources listed for {title}."

        offset = max(offset, 0)
        window = sources[offset : offset + max(limit, 1)]
        results = []
        for src in window:
            resolved = self._resolve_source(src)
            if resolved:
                results.append(
                    f"### {src}\n{resolved.read_text(encoding='utf-8')[:MAX_SOURCE_LENGTH_CHARS]}"
                )
            else:
                results.append(f"### {src}\n(source file not found)")
        return "\n\n".join(results) + _more_note(len(window), len(sources), offset=offset)

    def get_sources_summary(self, title: str) -> str:
        """
        Summarize a page's sources with frontmatter and opening text only.

        A cheap way to judge each source's relevance before pulling its full body
        with ``get_sources``.

        Parameters
        ----------
        title: str
            Page title or slug whose sources to summarize.

        Returns
        -------
        str
            Each source under its path heading, showing only its frontmatter block
            and first paragraph, or a not-found / no-sources message.
        """
        path = self._resolve_page(title)
        if path is None:
            return f"Page not found: {title}"
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

    def find_related(
        self, title: str, depth: int = DEFAULT_RELATED_DEPTH, limit: int = DEFAULT_RELATED_LIMIT
    ) -> str:
        """
        List the pages connected to a page, in either direction across all link kinds.

        Parameters
        ----------
        title: str
            Page title or slug to start from.
        depth: int
            How many link hops to walk outward from the starting page.
        limit: int
            Maximum number of pages to list.

        Returns
        -------
        str
            One wikilink per connected page, ordered by semantic similarity to the
            starting page (most relevant first) and marking pages that don't exist
            yet as gaps, followed by a coverage note when the result is capped, or
            a not-found / no-relations message.
        """
        slug = title.lower().replace(" ", "-")
        if not self._search.page_titles({slug}):
            return f"Page not found: {title}"

        related: set[str] = set()
        frontier = {slug}
        for _ in range(depth):
            frontier = self._search.neighbors(frontier) - related - {slug}
            if not frontier:
                break
            related.update(frontier)

        if not related:
            return f"No related pages found for {title}."

        # rank by similarity to the source so the cap keeps the most relevant
        ranked = self._search.rank_by_similarity(slug, related)
        ordered = ranked + sorted(related - set(ranked))

        # cap the fan-out so a high-degree hub can't flood the caller's context.
        window = ordered[: max(limit, 1)]
        titles = self._search.page_titles(set(window))
        lines = [f"- {_wikilink(stem, titles)}" for stem in window]
        return "\n".join(lines) + _more_note(len(window), len(ordered), pageable=False)

    def _walk_prerequisite_edges(
        self, start: str, max_depth: int
    ) -> tuple[list[tuple[str, str]], int, bool]:
        """
        Collect prerequisite edges reachable from ``start``, breadth-first.

        Parameters
        ----------
        start: str
            Stem to walk out from.
        max_depth: int
            Greatest number of hops to follow.

        Returns
        -------
        edges: list[tuple[str, str]]
            ``(dependent, prerequisite)`` pairs found along the walk.
        depth_reached: int
            How many hops were actually taken.
        truncated: bool
            True when the depth cap halted the walk with prerequisites still
            left to expand.
        """
        edges: list[tuple[str, str]] = []
        recorded: set[tuple[str, str]] = set()
        visited: set[str] = {start}
        frontier: set[str] = {start}
        depth_reached = 0
        while frontier and depth_reached < max_depth:
            depth_reached += 1
            unexplored: set[str] = set()
            # a page's prerequisites are the targets of its prerequisite edges
            for dependent, prerequisite in self._search.edges_from(
                frontier, kinds=("prerequisite",)
            ):
                if (dependent, prerequisite) not in recorded:
                    recorded.add((dependent, prerequisite))
                    edges.append((dependent, prerequisite))
                if prerequisite not in visited:
                    visited.add(prerequisite)
                    unexplored.add(prerequisite)
            frontier = unexplored
        return edges, depth_reached, bool(frontier)

    def prerequisite_closure(self, title: str, max_depth: int = DEFAULT_PREREQUISITE_DEPTH) -> str:
        """
        Lay out everything a page rests on, in the order you would learn it.

        Walks ``prerequisite`` edges outward and returns the reachable concepts
        sorted fundamentals-first, so the result reads as a derivation from
        first principles up to the page itself. Prerequisites that have no page
        yet are flagged as gaps, concepts shared across branches are called out,
        and the walk is bounded by ``max_depth``.

        Parameters
        ----------
        title: str
            Page title or slug to derive from its fundamentals.
        max_depth: int
            Greatest number of prerequisite hops to follow before stopping.

        Returns
        -------
        str
            A numbered fundamentals-first list of the prerequisites as wikilinks
            (gaps marked, the queried page tagged ``(target)``, each annotated
            with which earlier entries it builds on), followed by a shared-
            foundations note, and a cycle or depth-cutoff note when either
            applies. Falls back to a not-found / no-prerequisites message.
        """
        slug = title.lower().replace(" ", "-")
        if not self._search.page_titles({slug}):
            return f"Page not found: {title}"

        edges, depth_reached, truncated = self._walk_prerequisite_edges(slug, max(max_depth, 1))
        if not edges:
            return f"{title} declares no prerequisites, so it has no derivation to lay out."

        prerequisites_of: dict[str, set[str]] = defaultdict(set)
        dependents_of: dict[str, set[str]] = defaultdict(set)
        nodes: set[str] = {slug}
        for dependent, prerequisite in edges:
            prerequisites_of[dependent].add(prerequisite)
            dependents_of[prerequisite].add(dependent)
            nodes.update((dependent, prerequisite))

        ordered, cyclic = _topological_order(nodes, prerequisites_of)
        titles = self._search.page_titles(nodes)
        position = {stem: index for index, stem in enumerate(ordered, start=1)}

        lines = [f"Prerequisites for {_wikilink(slug, titles)}, fundamentals first:", ""]
        for index, stem in enumerate(ordered, start=1):
            marker = " (target)" if stem == slug else ""
            prerequisite_indices = sorted(
                position[p] for p in prerequisites_of.get(stem, set()) if p in position
            )
            trail = (
                f" — builds on {', '.join(map(str, prerequisite_indices))}"
                if prerequisite_indices
                else ""
            )
            lines.append(f"{index}. {_wikilink(stem, titles)}{marker}{trail}")

        shared: list[str] = []
        for stem in ordered:
            dependent_indices = sorted(
                position[d] for d in dependents_of.get(stem, set()) if d in position
            )
            if len(dependent_indices) >= 2:
                where = ", ".join(map(str, dependent_indices))
                shared.append(f"{_wikilink(stem, titles)} (under {where})")
        if shared:
            lines += ["", f"Shared foundations: {'; '.join(shared)}."]
        if cyclic:
            cycle = ", ".join(_wikilink(stem, titles) for stem in cyclic)
            lines += ["", f"Prerequisite cycle (unordered): {cycle}."]
        if truncated:
            lines += ["", f"Stopped at depth {depth_reached}, deeper prerequisites not expanded."]
        return "\n".join(lines)

    def dependents(self, title: str, limit: int = DEFAULT_RELATED_LIMIT) -> str:
        """
        List the pages that build on a page by declaring it a prerequisite.

        Parameters
        ----------
        title: str
            Page title or slug to look up dependents for.
        limit: int
            Maximum number of dependents to list in this call.

        Returns
        -------
        str
            A wikilink list of the dependent pages, with a coverage note when
            the result is capped, or a not-found / none message.
        """
        slug = title.lower().replace(" ", "-")
        if not self._search.page_titles({slug}):
            return f"Page not found: {title}"

        # dependents declare this page as a prerequisite, so they're the sources of its edges
        dependent_stems = self._search.neighbors(
            {slug}, kinds=("prerequisite",), following="sources"
        )
        dependent_stems.discard(slug)
        if not dependent_stems:
            return f"Nothing depends on {title} as a prerequisite."

        ordered = sorted(dependent_stems)
        window = ordered[: max(limit, 1)]
        titles = self._search.page_titles(set(window))
        lines = [f"- {_wikilink(stem, titles)}" for stem in window]
        return "\n".join(lines) + _more_note(len(window), len(ordered), pageable=False)

    def list_domains(self) -> str:
        """
        List the knowledge domains in the wiki with their page counts.

        Returns:
        --------
        str
            One line per domain with its name and page count, ordered by descending
            count then name, or an empty-wiki notice.
        """
        counts = self._search.domain_counts()
        if not counts:
            return "No domains yet."
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return "\n".join(f"- {domain} ({count} pages)" for domain, count in ordered)

    def list_gaps(self, limit: int = DEFAULT_GAPS_LIMIT, offset: int = 0) -> str:
        """
        List referenced-but-unwritten concepts, most-referenced first.

        Parameters:
        -----------
        limit: int
            Maximum number of gaps to return in this call.
        offset: int
            Number of leading gaps to skip, for paging.

        Returns:
        --------
        str
            One line per gap with its wikilink and how many pages reference it,
            ordered by descending reference count, followed by a coverage note
            when paged, or a no-gaps notice.
        """
        gaps = self._search.list_gaps()
        total = len(gaps)
        if total == 0:
            return "No gaps: every referenced concept has a page."

        offset = max(offset, 0)
        window = gaps[offset : offset + max(limit, 1)]
        if not window:
            return f"No gaps at offset {offset} (total {total})."

        lines = [
            f"- [[{target}]] ({refs} reference{'s' if refs != 1 else ''})"
            for target, refs in window
        ]
        return "\n".join(lines) + _more_note(len(window), total, offset=offset)
