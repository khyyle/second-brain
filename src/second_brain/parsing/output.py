"""Writes dual .md + .json parse output to the raw directory."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from second_brain.parsing.provider import ParseResult

logger = logging.getLogger(__name__)


def write_parse_output(
    result: ParseResult,
    output_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    """
    Write a ParseResult as paired .md and .json files.

    The .md file is human-readable for the compilation agent. The
    .json sidecar preserves block-level detail (bboxes, page/reading
    order) for diagnostics and potential re-parsing without
    re-running the parser.

    Parameters
    ----------
    result: ParseResult
        Parsed document output to persist.
    output_dir: Path
        Directory to write files into (created if missing).
    stem: str
        Base filename without extension (e.g. ``"lecture_01"``).

    Returns
    -------
    md_path: Path
        Path to the written markdown file.
    json_path: Path
        Path to the written JSON sidecar file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{stem}.md"
    json_path = output_dir / f"{stem}.json"

    md_path.write_text(result.markdown, encoding="utf-8")

    # Block content is already in the .md file — the JSON sidecar stores
    # per-block structure (type, page, reading order, bbox) for diagnostics.
    json_data = {
        "source": result.metadata.get("source", ""),
        "parse_lane": result.metadata.get("parse_lane", ""),
        "pages": result.metadata.get("pages", 0),
        "blocks": [
            {
                "block_type": b.block_type,
                "page_number": b.page_number,
                "reading_order": b.reading_order,
                "bbox": b.bbox,
            }
            for b in result.blocks
        ],
        "diagrams": result.diagrams,
    }
    json_path.write_text(
        json.dumps(json_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info("Wrote parse output: %s (.md + .json)", stem)
    return md_path, json_path
