"""MCP server that exposes wiki tools to supported desktop assistants."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from second_brain.backend_client import BackendClient, BackendRequestError
from second_brain.mcp_server.tools import (
    DEFAULT_GAPS_LIMIT,
    DEFAULT_LIST_LIMIT,
    DEFAULT_PREREQUISITE_DEPTH,
    DEFAULT_RELATED_DEPTH,
    DEFAULT_RELATED_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SOURCES_LIMIT,
    INDEX_PAGES_PER_DOMAIN,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("second-brain")

_client = BackendClient()


class RemoteTools:
    """Dispatch named assistant tools through the local HTTP API."""

    def __getattr__(self, name: str) -> Callable[..., str]:
        def call(*args: Any, **kwargs: Any) -> str:
            return _client.request(
                "POST", f"/assistant-tools/{name}", payload={"args": list(args), "kwargs": kwargs}
            )

        return call


def _get_tools() -> RemoteTools:
    return RemoteTools()


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
    failing with a syntax error. A valid query may still have no matches.

    Parameters
    ----------
    query: str
        An FTS5 match expression or plain text.
    limit: int
        Maximum number of pages to return.

    Returns
    -------
    str
        Ranked page titles, snippets, and scores, or a no-results message.
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
    """Search wiki pages by embedding similarity to a natural-language query.

    Matches can differ in wording from the query. Similarity scores measure vector
    closeness and do not establish factual relevance. Reports unavailable semantic
    search or its keyword fallback explicitly.

    Parameters
    ----------
    query: str
        A natural-language description of what you want, matched by meaning.
    limit: int
        Maximum number of pages to return.

    Returns
    -------
    str
        Ranked page matches or a message describing unavailable semantic search.
    """
    try:
        return _get_tools().semantic_search(query, limit)
    except BackendRequestError as error:
        if error.status_code != 503:
            raise
        return (
            "Ollama isn't responding, so search by meaning is unavailable. Tell the "
            "user, and use search_wiki for keyword search instead."
        )


@mcp.tool()
def read_page(
    title: Annotated[str, Field(description="Page title or slug to read.")],
) -> str:
    """Read one wiki page in full by its title or slug.

    Parameters
    ----------
    title: str
        Page title or slug to read.

    Returns
    -------
    str
        Complete Markdown including frontmatter, or a not-found message.
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
    """List wiki pages matching all supplied domain, tag, and content-type filters.

    Parameters
    ----------
    domain: str | None
        Keep only pages declaring this knowledge domain.
    tag: str | None
        Keep only pages carrying this tag.
    content_type: str | None
        Keep only pages of this type (concept, problem, project, insight).
    limit: int
        Maximum pages to return in this call.
    offset: int
        Number of leading matches to skip, for paging.

    Returns
    -------
    str
        Page summaries with total count and pagination details when more matches remain.
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
    """Summarize current wiki pages by domain, limiting the pages listed in each domain.

    Parameters
    ----------
    pages_per_domain: int
        Max pages to show per domain before linking onward.

    Returns
    -------
    str
        Domain overview with wikilinks and a query for any remaining pages.
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
    """Save a freeform note as an ingestion source and trigger ingestion when available.

    The content should contain the original detail and context, without wiki-page
    formatting or YAML frontmatter. The note remains a source for a later build;
    this operation does not write a compiled wiki page.

    Parameters
    ----------
    content: str
        Freeform insight text to capture. Write all relevant detail and context. Do not
        format it as a finished page or add frontmatter.
    title: str | None
        Optional short title, else the first line of content is used.
    topic: str | None
        Optional topic hint recorded in frontmatter to guide compilation.

    Returns
    -------
    str
        Saved source path and ingestion-submission status.
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
    """Read excerpts of the raw documents listed in a wiki page's provenance.

    Parameters
    ----------
    title: str
        Page title or slug whose sources to retrieve.
    limit: int
        Maximum number of sources to return in this call.
    offset: int
        Number of leading sources to skip, for paging.

    Returns
    -------
    str
        Up to limit source excerpts, each capped at 2,000 characters, with pagination
        details. Missing pages or source files are reported in the text.
    """
    return _get_tools().get_sources(title, limit=limit, offset=offset)


@mcp.tool()
def get_sources_summary(
    title: Annotated[str, Field(description="Page title or slug whose sources to preview.")],
) -> str:
    """Read frontmatter and the first paragraph of each source listed by a wiki page.

    Parameters
    ----------
    title: str
        Page title or slug whose sources to preview.

    Returns
    -------
    str
        Source previews, including a notice for missing files or a missing page.
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
    """Walk wiki links and backlinks across all relationship types.

    Parameters
    ----------
    title: str
        Page title or slug to start from.
    depth: int
        How many link hops to walk outward.
    limit: int
        Maximum number of pages to return.

    Returns
    -------
    str
        Related pages within depth hops, limited to limit entries, with the total found.
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
    """Follow prerequisites transitively and order them from fundamentals to the topic.

    Parameters
    ----------
    title: str
        Page title or slug to derive from its fundamentals.
    max_depth: int
        Maximum number of prerequisite hops to follow.

    Returns
    -------
    str
        Ordered prerequisite tree with missing-page markers and shared-concept references.
    """
    return _get_tools().prerequisite_closure(title, max_depth=max_depth)


@mcp.tool()
def dependents(
    title: Annotated[str, Field(description="Page title or slug to find dependents of.")],
    limit: Annotated[
        int, Field(ge=1, le=200, description="Maximum number of pages to return.")
    ] = DEFAULT_RELATED_LIMIT,
) -> str:
    """Find pages that directly declare the selected concept as a prerequisite.

    Parameters
    ----------
    title: str
        Page title or slug to find dependents of.
    limit: int
        Maximum number of pages to return.

    Returns
    -------
    str
        Direct dependent pages, limited to limit entries, with the total found.
    """
    return _get_tools().dependents(title, limit=limit)


@mcp.tool()
def list_domains() -> str:
    """List wiki domains and the number of pages assigned to each.

    Returns
    -------
    str
        Domain names and counts, or a message that no domains exist.
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
    """List referenced concepts that have no wiki page, most referenced first.

    Parameters
    ----------
    limit: int
        Maximum number of gaps to return in this call.
    offset: int
        Number of leading gaps to skip, for paging.

    Returns
    -------
    str
        Gap names and reference counts with total and pagination details.
    """
    return _get_tools().list_gaps(limit=limit, offset=offset)


def serve(client: BackendClient | None = None) -> None:
    """Run the MCP stdio server using a local HTTP backend client.

    Parameters
    ----------
    client: BackendClient | None
        Backend client to bind for tool requests, or None to keep the existing client.

    Returns
    -------
    None
    """
    global _client
    if client is not None:
        _client = client
    mcp.run()


if __name__ == "__main__":
    serve()
