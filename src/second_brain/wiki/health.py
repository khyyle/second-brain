"""Health checks — contradiction detection, gap analysis, stale page detection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from second_brain.wiki.structure import (
    build_link_graph,
    detect_gaps,
    detect_orphans,
    discover_all_pages,
)

logger = logging.getLogger(__name__)


@dataclass
class HealthReport:
    """
    Aggregated results from all wiki health checks.

    Each field collects a specific category of issue; an empty
    list means no problems were found in that category.
    """

    orphan_pages: list[str] = field(default_factory=list)
    gap_links: list[str] = field(default_factory=list)
    oversized_pages: list[tuple[str, int]] = field(default_factory=list)
    undersized_pages: list[tuple[str, int]] = field(default_factory=list)
    missing_frontmatter: list[str] = field(default_factory=list)
    stale_pages: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return not any(
            [
                self.orphan_pages,
                self.gap_links,
                self.oversized_pages,
                self.missing_frontmatter,
            ]
        )

    def summary(self) -> str:
        """
        Format a human-readable summary of this report.

        Returns
        -------
        str
            Multi-line text summarizing issue counts.
        """
        lines = ["=== Health Report ==="]
        lines.append(f"Orphan pages (no incoming links): {len(self.orphan_pages)}")
        lines.append(f"Gap links (broken wikilinks): {len(self.gap_links)}")
        lines.append(f"Oversized pages (>4000 words): {len(self.oversized_pages)}")
        lines.append(f"Undersized pages (<150 words): {len(self.undersized_pages)}")
        lines.append(f"Missing required frontmatter: {len(self.missing_frontmatter)}")
        lines.append(f"Stale pages (source updated): {len(self.stale_pages)}")
        return "\n".join(lines)


REQUIRED_FRONTMATTER = {"title", "type", "domains"}
SPLIT_THRESHOLD = 4000
MERGE_THRESHOLD = 150


def run_health_check(
    wiki_dir: Path,
    raw_dir: Path | None = None,
) -> HealthReport:
    """
    Run all health checks against the wiki.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    raw_dir: Path | None
        Directory containing raw sources. When provided,
        stale-page detection is included.

    Returns
    -------
    HealthReport
        Aggregated results across all check categories.
    """
    pages = discover_all_pages(wiki_dir)
    graph = build_link_graph(pages)

    report = HealthReport()

    report.orphan_pages = detect_orphans(pages, graph)
    report.gap_links = detect_gaps(pages, graph)

    for stem, page in pages.items():
        if page.word_count > SPLIT_THRESHOLD:
            report.oversized_pages.append((stem, page.word_count))
        if page.word_count < MERGE_THRESHOLD:
            report.undersized_pages.append((stem, page.word_count))

        fm_keys = set(page.frontmatter.keys())
        missing = REQUIRED_FRONTMATTER - fm_keys
        if missing:
            report.missing_frontmatter.append(f"{stem}: missing {missing}")

    if raw_dir and raw_dir.exists():
        report.stale_pages = _find_stale_pages(wiki_dir, raw_dir, pages)

    logger.info(report.summary())
    return report


def _find_stale_pages(
    wiki_dir: Path,
    raw_dir: Path,
    pages: dict,
) -> list[str]:
    """
    Find wiki pages whose sources changed since last compile.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    raw_dir: Path
        Directory containing raw source files.
    pages: dict
        Mapping of page stem to WikiPage.

    Returns
    -------
    list[str]
        Stems of wiki pages that are stale.
    """
    stale: list[str] = []

    for stem, page in pages.items():
        sources = page.frontmatter.get("sources", [])
        if not sources:
            continue

        wiki_mtime = page.path.stat().st_mtime

        for src in sources:
            for raw_file in raw_dir.rglob(src):
                if raw_file.stat().st_mtime > wiki_mtime:
                    stale.append(stem)
                    break
            else:
                continue
            break

    return stale
