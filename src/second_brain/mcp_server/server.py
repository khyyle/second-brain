"""MCP server — exposes wiki tools to Claude Desktop and Cursor."""

from __future__ import annotations

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

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
    INDEX_PAGES_PER_DOMAIN,
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
def search_wiki(
    query: Annotated[str, Field(description="An FTS5 match expression or plain text.")],
    limit: Annotated[
        int, Field(ge=1, le=50, description="Maximum number of pages to return.")
    ] = DEFAULT_SEARCH_LIMIT,
) -> str:
    """Search wiki pages by keyword, ranked by relevance (full-text BM25).

    Best for exact terms and names. Plain text works directly: `gradient descent`
    matches pages containing both words (multiple terms are implicitly AND-ed).
    Word endings are stemmed, so `model` also matches `models` and `modeling`.

    Supported query operators:
    - Phrase: `"central limit theorem"` matches that exact sequence of words.
    - OR, AND, NOT must be UPPERCASE: `swaps OR etfs`, `bias NOT bayesian`.
      Lowercase `or`/`and`/`not` are treated as ordinary search words.
    - Grouping: `(swaps OR etfs) AND leverage`.
    - Proximity: `NEAR(bias variance, 3)` matches the terms within 3 words of
      each other. Choose the number to tune how close, or omit it
      (`NEAR(bias variance)`) to use the default span of 10.
    - Field filter: `title:estimation`, `tags:probability`, `domains:economics`.
      The searchable fields are title, content, tags, domains, content_type, stem.

    A query that is not valid syntax is treated as plain AND-ed terms rather than
    failing, so a malformed query still returns results. For fuzzy or conceptual
    lookup where you do not know the wording, use `semantic_search`.
    """
    return _get_tools().search_wiki(query, limit)


@mcp.tool()
def semantic_search(
    query: Annotated[
        str,
        Field(description="A natural-language description of what you want, matched by meaning."),
    ],
    limit: Annotated[
        int, Field(ge=1, le=50, description="Maximum number of pages to return.")
    ] = DEFAULT_SEARCH_LIMIT,
) -> str:
    """Search wiki pages by meaning using embeddings, for fuzzy or conceptual lookup.

    Give a natural-language description of what you want, not just keywords: a
    phrase, topic, or question like `how variance affects model error`. It is embedded
    and checked against the wiki's vector database, matching on conceptual similarity,
    so it surfaces relevant pages even when they do not contain your exact words.
    Prefer this when you do not know the wording or want conceptually-related pages,
    and `search_wiki` when you have exact terms or names. Falls back with a notice
    when the semantic layer is unavailable.

    Stay aware of the pitfalls of semantic search: cosine similarity is not always
    relevance, exact matches are less reliable, it can lack differentiating nuance,
    relevant pages may be missed entirely, and contextual meaning can be lost. Use it
    as a supplementary tool and reason over graph structure, not as an authoritative
    search tool.
    """
    return _get_tools().semantic_search(query, limit)


@mcp.tool()
def read_page(
    title: Annotated[str, Field(description="Page title or slug to read.")],
) -> str:
    """Read one wiki page in full by its title or slug.

    Use to pull a page's complete content (frontmatter + body) once you've
    located it, e.g. via `search_wiki`, `semantic_search`, `list_pages`, or a graph
    tool. Returns a not-found message when no page matches.
    """
    return _get_tools().read_page(title)


@mcp.tool()
def list_pages(
    domain: Annotated[
        str | None, Field(description="Keep only pages declaring this knowledge domain.")
    ] = None,
    tag: Annotated[str | None, Field(description="Keep only pages carrying this tag.")] = None,
    content_type: Annotated[
        str | None,
        Field(description="Keep only pages of this type (concept, problem, project, insight)."),
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=200, description="Maximum pages to return in this call.")
    ] = DEFAULT_LIST_LIMIT,
    offset: Annotated[
        int, Field(ge=0, description="Number of leading matches to skip, for paging.")
    ] = 0,
) -> str:
    """List wiki pages by structured filter: domain, tag, and/or content type.

    Use this to browse a known slice of the wiki (e.g. every concept in a domain)
    when you have a filter rather than a search query; for free-text lookup use
    `search_wiki` or `semantic_search`, and to see which domains exist first use
    `list_domains`. Filters combine with AND. Returns at most `limit` pages from
    `offset`, with a note giving the total and next offset when more match.
    """
    return _get_tools().list_pages(
        domain=domain, tag=tag, content_type=content_type, limit=limit, offset=offset
    )


@mcp.tool()
def read_index(
    pages_per_domain: Annotated[
        int,
        Field(ge=1, le=50, description="Max pages to show per domain before linking onward."),
    ] = INDEX_PAGES_PER_DOMAIN,
) -> str:
    """Get a one-shot overview of the whole wiki: pages grouped under each domain.

    Built live from the current wiki. Each domain lists its first several pages as
    wikilinks plus a pointer to `list_pages(domain=...)` for the rest, so the
    overview stays a fixed size at any scale. Good for orienting before you search
    or drill in. Use `list_domains` for the lighter domains-and-counts map.
    """
    return _get_tools().read_index(pages_per_domain=pages_per_domain)


@mcp.tool()
def capture_note(
    content: Annotated[
        str,
        Field(
            description=(
                "Freeform insight text to capture. Write all relevant detail and "
                "context. Do not format it as a finished page or add frontmatter."
            )
        ),
    ],
    title: Annotated[
        str | None,
        Field(description="Optional short title, else the first line of content is used."),
    ] = None,
    topic: Annotated[
        str | None,
        Field(description="Optional topic hint recorded in frontmatter to guide compilation."),
    ] = None,
) -> str:
    """Capture a freeform insight from this conversation into the knowledge base.

    Use this to save something worth keeping. Dump the insight as detailed,
    freeform notes--capture relevant substance and context, keeping it detailed. Do
    NOT write a finished wiki page or YAML frontmatter. The note is saved as a
    source document. Compilation will later title/place/link/synthesize this into the graph.
    It does NOT author a page directly, so don't pre-summarize or structure it. Richer
    raw detail compiles into a better page. Optionally pass a short `title` and a `topic` hint.
    """
    return _get_tools().capture_note(content, title=title, topic=topic)


@mcp.tool()
def get_sources(
    title: Annotated[str, Field(description="Page title or slug whose sources to retrieve.")],
    limit: Annotated[
        int, Field(ge=1, le=50, description="Maximum number of sources to return in this call.")
    ] = DEFAULT_SOURCES_LIMIT,
    offset: Annotated[
        int, Field(ge=0, description="Number of leading sources to skip, for paging.")
    ] = 0,
) -> str:
    """Retrieve the full raw source documents a wiki page was compiled from.

    Use this to read the original material behind a page, e.g. to verify a claim
    or expand the page. Each source is truncated to its first 2000 characters; to
    judge which sources are worth pulling in full first, use `get_sources_summary`.
    Returns at most `limit` sources from `offset`, with a note giving the total
    and next offset.
    """
    return _get_tools().get_sources(title, limit=limit, offset=offset)


@mcp.tool()
def get_sources_summary(
    title: Annotated[str, Field(description="Page title or slug whose sources to preview.")],
) -> str:
    """Preview a page's sources cheaply: each source's frontmatter and first paragraph.

    Use this first to judge which of a page's sources are relevant before pulling
    any in full with `get_sources`, especially when a page has many sources. Returns
    the lightweight preview only, never the full source text.
    """
    return _get_tools().get_sources_summary(title)


@mcp.tool()
def find_related(
    title: Annotated[str, Field(description="Page title or slug to start from.")],
    depth: Annotated[
        int, Field(ge=1, le=5, description="How many link hops to walk outward.")
    ] = DEFAULT_RELATED_DEPTH,
    limit: Annotated[
        int, Field(ge=1, le=200, description="Maximum number of pages to return.")
    ] = DEFAULT_RELATED_LIMIT,
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
def prerequisite_closure(
    title: Annotated[
        str, Field(description="Page title or slug to derive from its fundamentals.")
    ],
    max_depth: Annotated[
        int, Field(ge=1, le=10, description="Maximum number of prerequisite hops to follow.")
    ] = DEFAULT_PREREQUISITE_DEPTH,
) -> str:
    """Lay out what a concept builds on, from fundamentals up to the concept itself.

    Use this to explain or derive a topic from first principles. It walks the
    page's prerequisites transitively and returns them in learning order
    (fundamentals first, the topic last), flags any prerequisite that has no
    page yet, and notes concepts that several branches share. Then read the
    listed pages with `read_page` to pull the ones you actually need.
    """
    return _get_tools().prerequisite_closure(title, max_depth=max_depth)


@mcp.tool()
def dependents(
    title: Annotated[str, Field(description="Page title or slug to find dependents of.")],
    limit: Annotated[
        int, Field(ge=1, le=200, description="Maximum number of pages to return.")
    ] = DEFAULT_RELATED_LIMIT,
) -> str:
    """List the pages that build on a concept by declaring it a prerequisite.

    Walks prerequisites in the reverse direction from `prerequisite_closure`, but
    only one hop — the pages that directly require this one. Use it to see where a
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
def list_gaps(
    limit: Annotated[
        int, Field(ge=1, le=200, description="Maximum number of gaps to return in this call.")
    ] = DEFAULT_GAPS_LIMIT,
    offset: Annotated[
        int, Field(ge=0, description="Number of leading gaps to skip, for paging.")
    ] = 0,
) -> str:
    """List concepts that pages reference but that have no page yet.

    These are the wiki's known holes, ranked by how many pages point at them
    (most-wanted first) — useful for deciding what to learn or write next, and
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
