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

    Fields split into two groups. Growth signals (``gap_links``,
    ``orphan_pages``, ``possible_duplicates``) describe where the wiki could
    expand, connect, or consolidate and are expected, not defects. Defect
    signals (``oversized_pages``, ``undersized_pages``, ``missing_frontmatter``)
    should trend to zero. Each ``gap_links`` entry pairs the referenced-but-
    unwritten stem with how many pages point at it, and each
    ``possible_duplicates`` entry pairs two page stems with their embedding
    similarity.
    """

    orphan_pages: list[str] = field(default_factory=list)
    gap_links: list[tuple[str, int]] = field(default_factory=list)
    possible_duplicates: list[tuple[str, str, float]] = field(default_factory=list)
    oversized_pages: list[tuple[str, int]] = field(default_factory=list)
    undersized_pages: list[tuple[str, int]] = field(default_factory=list)
    missing_frontmatter: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        # growth signals (gaps, orphans, duplicates) and the soft undersized
        # signal are omitted as they aren't necessarily hard defects
        return not any(
            [
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
        lines = ["=== Wiki Report ==="]
        lines.append(f"Referenced but not written (gaps): {len(self.gap_links)}")
        lines.append(f"Not linked from any page (orphans): {len(self.orphan_pages)}")
        lines.append(f"Possible duplicates: {len(self.possible_duplicates)}")
        lines.append(f"Oversized pages (>4000 words): {len(self.oversized_pages)}")
        lines.append(f"Undersized pages (<150 words): {len(self.undersized_pages)}")
        lines.append(f"Missing required frontmatter: {len(self.missing_frontmatter)}")
        return "\n".join(lines)


REQUIRED_FRONTMATTER = {"title", "type", "domains"}
SPLIT_THRESHOLD = 4000
MERGE_THRESHOLD = 150


def run_health_check(
    wiki_dir: Path,
    duplicate_pairs: list[tuple[str, str, float]] | None = None,
) -> HealthReport:
    """
    Run all health checks against the wiki.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    duplicate_pairs: list[tuple[str, str, float]] | None
        Near-duplicate page pairs from the search index's embedding sweep.

    Returns
    -------
    HealthReport
        Aggregated results across all check categories.
    """
    pages = discover_all_pages(wiki_dir)
    graph = build_link_graph(pages)

    report = HealthReport()

    report.orphan_pages = detect_orphans(pages, graph)
    # Pair each gap with how many pages reference it, most-referenced first, so
    # the list reads as a demand-ranked "write these next" queue.
    report.gap_links = sorted(
        ((stem, len(graph.backward.get(stem, ()))) for stem in detect_gaps(pages, graph)),
        key=lambda gap: (-gap[1], gap[0]),
    )
    report.possible_duplicates = duplicate_pairs or []

    for stem, page in pages.items():
        if page.word_count > SPLIT_THRESHOLD:
            report.oversized_pages.append((stem, page.word_count))
        if page.word_count < MERGE_THRESHOLD:
            report.undersized_pages.append((stem, page.word_count))

        fm_keys = set(page.frontmatter.keys())
        missing = REQUIRED_FRONTMATTER - fm_keys
        if missing:
            report.missing_frontmatter.append(f"{stem}: missing {missing}")

    logger.info(report.summary())
    return report
