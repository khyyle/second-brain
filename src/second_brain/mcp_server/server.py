"""MCP server — exposes wiki tools to Claude Desktop and Cursor."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from second_brain.config import load_config
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import (
    DEFAULT_GAPS_LIMIT,
    DEFAULT_LIST_LIMIT,
    DEFAULT_PREREQUISITE_DEPTH,
    DEFAULT_RELATED_DEPTH,
    DEFAULT_RELATED_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SOURCES_LIMIT,
    WikiTools,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("second-brain")

_tools: WikiTools | None = None


def _get_tools() -> WikiTools:
    """
    Lazily initialize and return the shared WikiTools instance.

    Returns
    -------
    WikiTools
        Singleton tools instance backed by the user's config.
    """
    global _tools
    if _tools is None:
        config = load_config()
        search_index = SearchIndex(config.search_db_path, config.search)
        _tools = WikiTools(config.wiki_dir, config.raw_dir, search_index)

    _tools.ensure_synced()
    return _tools


@mcp.tool()
def search_wiki(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """Search wiki pages by keyword (full-text BM25). Best for exact terms and names.

    Plain text works directly (e.g. `gradient descent`); FTS5 operators
    are also supported for power use: `AND`, `OR`, `NEAR`, and quoted
    phrases like `"central limit theorem"`. For fuzzy or conceptual
    queries where you don't know the exact wording, use `semantic_search`.
    """
    return _get_tools().search_wiki(query, limit)


@mcp.tool()
def semantic_search(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """Search wiki pages by meaning using embeddings, for fuzzy/conceptual queries.

    Complements `search_wiki`: use this when the exact wording is unknown
    or you want conceptually-related pages. Falls back with a clear notice
    if the semantic layer is unavailable (e.g. Ollama not running), in
    which case use `search_wiki` instead.
    """
    return _get_tools().semantic_search(query, limit)


@mcp.tool()
def read_page(title: str) -> str:
    """Read a specific wiki page by its title or slug name."""
    return _get_tools().read_page(title)


@mcp.tool()
def list_pages(
    domain: str | None = None,
    tag: str | None = None,
    content_type: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> str:
    """List wiki pages, optionally filtered by domain, tag, or content type.

    Returns at most `limit` pages starting at `offset`; when more match, the
    result ends with a note giving the total and the next offset to page.
    """
    return _get_tools().list_pages(
        domain=domain, tag=tag, content_type=content_type, limit=limit, offset=offset
    )


@mcp.tool()
def read_index() -> str:
    """Read the master wiki index with concept map organized by domain."""
    return _get_tools().read_index()


@mcp.tool()
def capture_note(content: str, title: str | None = None, topic: str | None = None) -> str:
    """Capture a note or insight from this conversation into the knowledge base.

    Saves the content as a source document in the intake queue so it flows
    through the normal ingest -> triage -> compile pipeline, gaining source
    traceability and quality gating. It does NOT author a finished wiki
    page directly; the page is written by the next compile run. Use this to
    save something worth keeping for later. Optionally pass a short `title`
    and a `topic` hint to guide compilation.
    """
    return _get_tools().capture_note(content, title=title, topic=topic)


@mcp.tool()
def get_sources(title: str, limit: int = DEFAULT_SOURCES_LIMIT, offset: int = 0) -> str:
    """Retrieve the raw source documents that a wiki page was compiled from.

    Returns at most `limit` sources starting at `offset`; a page built from
    many sources is paged, with a note giving the total and the next offset.
    """
    return _get_tools().get_sources(title, limit=limit, offset=offset)


@mcp.tool()
def get_sources_summary(title: str) -> str:
    """
    Retrieve a lightweight summary of source documents for a wiki page.
    Returns frontmatter + first paragraph only, not full content.
    Use get_sources() only when full source text is needed.
    """
    return _get_tools().get_sources_summary(title)


@mcp.tool()
def find_related(
    title: str, depth: int = DEFAULT_RELATED_DEPTH, limit: int = DEFAULT_RELATED_LIMIT
) -> str:
    """Find pages loosely connected to a concept, in any direction across all link types.

    Walks the link graph outward over every relationship (prerequisites, related,
    mentions, and their backlinks) up to `depth` hops, answering the open-ended
    "what touches this?" without imposing order. For the ordered chain a concept
    builds on, use `prerequisite_closure`; for what builds on it, use `dependents`.
    Returns at most `limit` pages, with a note giving the total found.
    """
    return _get_tools().find_related(title, depth, limit=limit)


@mcp.tool()
def prerequisite_closure(title: str, max_depth: int = DEFAULT_PREREQUISITE_DEPTH) -> str:
    """Lay out what a concept builds on, from fundamentals up to the concept itself.

    Use this to explain or derive a topic from first principles. It walks the
    page's prerequisites transitively and returns them in learning order
    (fundamentals first, the topic last), flags any prerequisite that has no
    page yet, and notes concepts that several branches share. Then read the
    listed pages with `read_page` to pull the ones you actually need.
    """
    return _get_tools().prerequisite_closure(title, max_depth=max_depth)


@mcp.tool()
def dependents(title: str, limit: int = DEFAULT_RELATED_LIMIT) -> str:
    """List the pages that build on a concept by declaring it a prerequisite.

    Walks prerequisites in the reverse direction from `prerequisite_closure`, but
    only one hop--the pages that directly require this one. Use it to see where a
    concept leads next. Returns at most `limit` pages, with a note giving the total.
    """
    return _get_tools().dependents(title, limit=limit)


@mcp.tool()
def list_domains() -> str:
    """List the knowledge domains in the wiki, with how many pages each holds.

    The cheapest map of what this knowledge base covers. Start here when you
    don't yet know the vault, then narrow with `list_pages(domain=...)` or search.
    """
    return _get_tools().list_domains()


@mcp.tool()
def list_gaps(limit: int = DEFAULT_GAPS_LIMIT, offset: int = 0) -> str:
    """List concepts that pages reference but that have no page yet.

    These are the wiki's known holes, ranked by how many pages point at them
    (most-wanted first) -useful for deciding what to learn or write next, and
    for avoiding `read_page` calls on concepts that don't exist. Returns at most
    `limit` starting at `offset`, with a note giving the total and next offset.
    """
    return _get_tools().list_gaps(limit=limit, offset=offset)


def serve() -> None:
    """
    Start the MCP server.

    Rebuilds the search index from any existing wiki content, then
    enters the FastMCP run loop (called by CLI or spawned by Claude
    Desktop).
    """
    config = load_config()
    search_index = SearchIndex(config.search_db_path, config.search)

    if config.wiki_dir.exists():
        search_index.sync_from_wiki(config.wiki_dir)

    logger.info("Starting MCP server for wiki at %s", config.wiki_dir)
    mcp.run()


if __name__ == "__main__":
    serve()
