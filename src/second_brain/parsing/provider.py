"""Document parser interface and structured output types."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class PageParseError(RuntimeError):
    """Raised when a parser fails to extract a page.

    A document with any failed page is marked failed at ingest and
    surfaced for retry; there is no silent fallback or quality score.
    """


class ParseLane(enum.Enum):
    """Which parser pipeline a document is routed through."""

    DOCLING = "docling"
    CHANDRA = "chandra"
    CLAUDE_FALLBACK = "claude_fallback"
    PASSTHROUGH = "passthrough"


@dataclass(frozen=True)
class ParseBlock:
    """Single extracted element from a document (paragraph, table, diagram, etc).

    Frozen so blocks can be safely shared across parse results without
    accidental mutation.
    """

    content: str | None
    block_type: str
    page_number: int
    reading_order: int
    bbox: tuple[float, ...] | None = None  # (left, top, right, bottom) if available
    diagram_description: str | None = None


@dataclass
class ParseResult:
    """Complete output of a document parser — markdown text, structured blocks, and metadata."""

    markdown: str
    blocks: list[ParseBlock]
    metadata: dict
    diagrams: list[dict] = field(default_factory=list)


class DocumentParser(ABC):
    """Interface all document parsers must implement."""

    @abstractmethod
    async def parse(self, file_path: str) -> ParseResult:
        """
        Parse a document into structured markdown and block data.

        Parameters
        ----------
        file_path: str
            Absolute path to the document to parse.

        Returns
        -------
        ParseResult
            Extracted markdown, blocks, and metadata.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Verify the parser is available and responding.

        Returns
        -------
        bool
            True if the parser backend is reachable and functional.
        """
        ...
