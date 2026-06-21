"""Text/markdown passthrough handler — copies with minimal transformation."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from second_brain.parsing.output import write_parse_output
from second_brain.parsing.provider import ParseBlock, ParseLane, ParseResult

logger = logging.getLogger(__name__)

# DOTALL so the lazy .*? matches multi-line YAML frontmatter blocks
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _has_frontmatter(content: str) -> bool:
    """Check if the file already starts with a YAML frontmatter block."""
    return bool(_FRONTMATTER_RE.match(content))


def _add_frontmatter(content: str, source_path: Path) -> str:
    """Prepend minimal YAML frontmatter derived from the filename.

    Parameters
    ----------
    content: str
        Original file content (without existing frontmatter).
    source_path: Path
        Source file, used to derive the title.

    Returns
    -------
    str
        Content with a YAML frontmatter header prepended.
    """
    title = source_path.stem.replace("-", " ").replace("_", " ").title()
    header = f'---\ntitle: "{title}"\ntype: document\nsource: {source_path.name}\n---\n\n'
    return header + content


def process_text_file(
    file_path: Path,
    output_dir: Path,
) -> Path:
    """Process a text/markdown/tex file into raw markdown output.

    Reads the file, adds YAML frontmatter if missing, and writes a
    ``ParseResult`` to *output_dir*.

    Parameters
    ----------
    file_path: Path
        Source text file.
    output_dir: Path
        Directory to write the markdown output into.

    Returns
    -------
    Path
        Path to the written markdown file.
    """
    # errors="replace" handles mixed-encoding files (e.g., LaTeX with
    # non-UTF8 special characters) without crashing the pipeline
    content = file_path.read_text(encoding="utf-8", errors="replace")

    if not _has_frontmatter(content):
        content = _add_frontmatter(content, file_path)

    blocks = [
        ParseBlock(
            content=content,
            block_type="text",
            page_number=1,
            reading_order=0,
        )
    ]

    result = ParseResult(
        markdown=content,
        blocks=blocks,
        metadata={
            "source": str(file_path),
            "parse_lane": ParseLane.PASSTHROUGH.value,
            "pages": 1,
        },
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path, _ = write_parse_output(result, output_dir, file_path.stem)
    logger.info("Processed text file: %s", file_path.name)
    return md_path
