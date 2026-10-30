"""Typed assistant tools and their descriptions, independent of server startup."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Annotated

from pydantic import Field, create_model

from second_brain.apps.wiki.tool_limits import (
    DEFAULT_GAPS_LIMIT,
    DEFAULT_LIST_LIMIT,
    DEFAULT_PREREQUISITE_DEPTH,
    DEFAULT_RELATED_DEPTH,
    DEFAULT_RELATED_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SOURCES_LIMIT,
    INDEX_PAGES_PER_DOMAIN,
)

WIKI_TOOL_NAMES = (
    "search_wiki",
    "semantic_search",
    "read_page",
    "list_pages",
    "read_index",
    "capture_note",
    "get_sources",
    "get_sources_summary",
    "find_related",
    "prerequisite_closure",
    "dependents",
    "list_domains",
    "list_gaps",
)


class WikiToolDefinitions:
    """Typed wiki operations dispatched through a supplied callable.

    Parameters
    ----------
    dispatch: Callable[..., str]
        Receives the operation name followed by its positional and keyword arguments.
        Returns the operation's text result.
    """

    def __init__(self, dispatch: Callable[..., str]) -> None:
        self._dispatch = dispatch

    def search_wiki(
        self,
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
        return self._dispatch("search_wiki", query, limit)

    def semantic_search(
        self,
        query: Annotated[
            str,
            Field(
                description="A natural-language description of what you want, matched by meaning."
            ),
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
        return self._dispatch("semantic_search", query, limit)

    def read_page(
        self,
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
        return self._dispatch("read_page", title)

    def list_pages(
        self,
        domain: Annotated[
            str | None, Field(description="Keep only pages declaring this knowledge domain.")
        ] = None,
        tag: Annotated[str | None, Field(description="Keep only pages carrying this tag.")] = None,
        content_type: Annotated[
            str | None,
            Field(
                description="Keep only pages of this type (concept, problem, project, insight)."
            ),
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
        return self._dispatch(
            "list_pages",
            domain=domain,
            tag=tag,
            content_type=content_type,
            limit=limit,
            offset=offset,
        )

    def read_index(
        self,
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
        return self._dispatch("read_index", pages_per_domain=pages_per_domain)

    def capture_note(
        self,
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
        return self._dispatch("capture_note", content, title=title, topic=topic)

    def get_sources(
        self,
        title: Annotated[str, Field(description="Page title or slug whose sources to retrieve.")],
        limit: Annotated[
            int,
            Field(ge=1, le=50, description="Maximum number of sources to return in this call."),
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
        return self._dispatch("get_sources", title, limit=limit, offset=offset)

    def get_sources_summary(
        self,
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
        return self._dispatch("get_sources_summary", title)

    def find_related(
        self,
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
        return self._dispatch("find_related", title, depth, limit=limit)

    def prerequisite_closure(
        self,
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
        return self._dispatch("prerequisite_closure", title, max_depth=max_depth)

    def dependents(
        self,
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
        return self._dispatch("dependents", title, limit=limit)

    def list_domains(self) -> str:
        """List wiki domains and the number of pages assigned to each.

        Returns
        -------
        str
            Domain names and counts, or a message that no domains exist.
        """
        return self._dispatch("list_domains")

    def list_gaps(
        self,
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
        return self._dispatch("list_gaps", limit=limit, offset=offset)


def get_tool_schemas(names: tuple[str, ...]) -> list[dict]:
    """Describe named tools as JSON schemas without binding an executor.

    Parameters
    ----------
    names: tuple[str, ...]
        Tool names in the desired output order.

    Returns
    -------
    list[dict]
        Name, description, and input_schema for each tool.

    Raises
    ------
    ValueError
        A name is not a supported wiki tool.
    """
    schemas = []
    for name in names:
        if name not in WIKI_TOOL_NAMES:
            raise ValueError(f"Unknown wiki tool: {name}")
        method = getattr(WikiToolDefinitions, name)
        signature = inspect.signature(method, eval_str=True)
        fields = {
            parameter.name: (
                parameter.annotation,
                ... if parameter.default is inspect.Parameter.empty else parameter.default,
            )
            for parameter in signature.parameters.values()
            if parameter.name != "self"
        }
        arguments = create_model(f"{name}Arguments", **fields)
        schemas.append(
            {
                "name": name,
                "description": method.__doc__ or "",
                "input_schema": arguments.model_json_schema(by_alias=True),
            }
        )
    return schemas
