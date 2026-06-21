"""MCP server — exposes wiki tools to Claude Desktop and Cursor."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from second_brain.config import load_config
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import WikiTools

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
def search_wiki(query: str, limit: int = 10) -> str:
    """Search wiki pages by keyword (FTS/BM25). Best for exact terms and names."""
    return _get_tools().search_wiki(query, limit)


@mcp.tool()
def semantic_search(query: str, limit: int = 10) -> str:
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
) -> str:
    """List all wiki pages, optionally filtered by domain, tag, or content type."""
    return _get_tools().list_pages(domain=domain, tag=tag, content_type=content_type)


@mcp.tool()
def read_index() -> str:
    """Read the master wiki index with concept map organized by domain."""
    return _get_tools().read_index()


@mcp.tool()
def update_page(title: str, content: str) -> str:
    """Update an existing wiki page with new content."""
    return _get_tools().update_page(title, content)


@mcp.tool()
def create_page(title: str, content: str, content_type: str = "concept") -> str:
    """Create a new wiki page. Content types: concept, problem, project, insight."""
    return _get_tools().create_page(title, content, content_type)


@mcp.tool()
def get_sources(title: str) -> str:
    """Retrieve the raw source documents that a wiki page was compiled from."""
    return _get_tools().get_sources(title)


@mcp.tool()
def get_sources_summary(title: str) -> str:
    """
    Retrieve a lightweight summary of source documents for a wiki page.
    Returns frontmatter + first paragraph only, not full content.
    Use get_sources() only when full source text is needed.
    """
    return _get_tools().get_sources_summary(title)


@mcp.tool()
def find_related(title: str, depth: int = 2) -> str:
    """Find pages related to a concept via wikilinks and backlink graph traversal."""
    return _get_tools().find_related(title, depth)


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
