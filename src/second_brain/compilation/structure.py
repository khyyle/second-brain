"""Deterministic structure rebuild — no LLM, pure filesystem graph analysis."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Captures the target from [[target]] and [[target|display text]] wikilinks
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
# DOTALL so .*? spans multi-line YAML blocks between --- fences
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

CONTENT_DIRS = ("concepts", "problems", "projects", "insights")


@dataclass
class WikiPage:
    """In-memory representation of a wiki page for graph construction."""

    path: Path
    rel_path: str
    stem: str  # filename without extension, used as the page identifier
    frontmatter: dict = field(default_factory=dict)
    outgoing_links: list[str] = field(default_factory=list)
    word_count: int = 0


@dataclass
class LinkGraph:
    """Bidirectional adjacency list of wikilink relationships.

    Uses adjacency lists (not a matrix) because knowledge graphs are sparse —
    most pages link to a small subset of the total, so O(V^2) matrix storage
    would be wasteful.
    """

    pages: dict[str, WikiPage] = field(default_factory=dict)
    forward: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    backward: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content, returning {} on failure."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _normalize_link_target(target: str) -> str:
    """Reduce a wikilink target to a bare page stem.

    Wikilinks appear both bare (``[[point-estimation]]``) and folder-
    prefixed (``[[concepts/point-estimation]]``), and may carry a ``.md``
    suffix or an ``#anchor``. Pages are keyed by bare stem, so every
    target is reduced to that form for consistent graph resolution.

    Parameters
    ----------
    target: str
        The raw text captured between ``[[`` and ``]]`` (display text
        already stripped).

    Returns
    -------
    str
        The bare stem the link points to.
    """
    target = target.strip().split("#", 1)[0].strip()
    target = target.rsplit("/", 1)[-1]
    if target.endswith(".md"):
        target = target[:-3]
    return target.strip()


def _extract_wikilinks(content: str) -> list[str]:
    """Return all wikilink target stems found in the content, normalized."""
    return [stem for raw in _WIKILINK_RE.findall(content) if (stem := _normalize_link_target(raw))]


def _count_words(content: str) -> int:
    """Count words in the body only, excluding YAML frontmatter."""
    fm = _FRONTMATTER_RE.match(content)
    body = content[fm.end() :] if fm else content
    return len(body.split())


def discover_all_pages(wiki_dir: Path) -> dict[str, WikiPage]:
    """
    Walk wiki content directories and parse each page.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.

    Returns
    -------
    dict[str, WikiPage]
        Mapping of page stem to parsed WikiPage.
    """
    pages: dict[str, WikiPage] = {}
    for content_dir in CONTENT_DIRS:
        dir_path = wiki_dir / content_dir
        if not dir_path.exists():
            continue
        for md_file in dir_path.rglob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            stem = md_file.stem
            pages[stem] = WikiPage(
                path=md_file,
                rel_path=str(md_file.relative_to(wiki_dir)),
                stem=stem,
                frontmatter=_parse_frontmatter(content),
                outgoing_links=_extract_wikilinks(content),
                word_count=_count_words(content),
            )
    return pages


def build_link_graph(pages: dict[str, WikiPage]) -> LinkGraph:
    """
    Build bidirectional link graph from wiki pages.

    Parameters
    ----------
    pages: dict[str, WikiPage]
        Mapping of page stem to WikiPage, as returned by
        ``discover_all_pages``.

    Returns
    -------
    LinkGraph
        Graph with forward and backward adjacency lists.
    """
    graph = LinkGraph(pages=pages)
    for stem, page in pages.items():
        for target in page.outgoing_links:
            graph.forward[stem].add(target)
            graph.backward[target].add(stem)
    return graph


def generate_index(wiki_dir: Path, pages: dict[str, WikiPage]) -> Path:
    """
    Generate ``index.md`` grouped by domain.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    pages: dict[str, WikiPage]
        Mapping of page stem to WikiPage.

    Returns
    -------
    Path
        Path to the written index file.
    """
    by_domain: dict[str, list[WikiPage]] = defaultdict(list)
    uncategorized: list[WikiPage] = []

    for page in pages.values():
        domains = page.frontmatter.get("domains", [])
        if not domains:
            uncategorized.append(page)
            continue
        for domain in domains:
            by_domain[domain].append(page)

    lines = [
        "---",
        "title: Wiki Index",
        "type: index",
        "---",
        "",
        "# Knowledge Base Index",
        "",
    ]

    for domain in sorted(by_domain.keys()):
        domain_pages = sorted(by_domain[domain], key=lambda p: p.stem)
        lines.append(f"## {domain.replace('-', ' ').title()}")
        lines.append("")
        for page in domain_pages:
            title = page.frontmatter.get("title", page.stem)
            ptype = page.frontmatter.get("type", "")
            tag = f" _({ptype})_" if ptype else ""
            lines.append(f"- [[{page.stem}|{title}]]{tag}")
        lines.append("")

    if uncategorized:
        lines.append("## Uncategorized")
        lines.append("")
        for page in sorted(uncategorized, key=lambda p: p.stem):
            title = page.frontmatter.get("title", page.stem)
            lines.append(f"- [[{page.stem}|{title}]]")
        lines.append("")

    index_path = wiki_dir / "_views" / "index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def generate_backlinks(wiki_dir: Path, graph: LinkGraph) -> Path:
    """Write backlinks.json mapping each page to its incoming links."""
    backlinks = {target: sorted(sources) for target, sources in graph.backward.items() if sources}
    out_path = wiki_dir / "_meta" / "backlinks.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(backlinks, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out_path


def detect_orphans(pages: dict[str, WikiPage], graph: LinkGraph) -> list[str]:
    """
    Detect pages with zero incoming links.

    Parameters
    ----------
    pages: dict[str, WikiPage]
        Mapping of page stem to WikiPage.
    graph: LinkGraph
        Bidirectional link graph.

    Returns
    -------
    list[str]
        Sorted stems of orphan pages (potential entry points
        or stale content).
    """
    return sorted(stem for stem in pages if not graph.backward.get(stem))


def detect_gaps(pages: dict[str, WikiPage], graph: LinkGraph) -> list[str]:
    """
    Detect wikilink targets with no corresponding page.

    Parameters
    ----------
    pages: dict[str, WikiPage]
        Mapping of page stem to WikiPage.
    graph: LinkGraph
        Bidirectional link graph.

    Returns
    -------
    list[str]
        Sorted stems referenced by wikilinks but missing a
        page file (broken links).
    """
    all_targets = set()
    for targets in graph.forward.values():
        all_targets.update(targets)
    return sorted(t for t in all_targets if t not in pages)


def generate_gaps_view(wiki_dir: Path, gaps: list[str]) -> Path:
    """
    Write ``gaps.md`` listing broken wikilinks.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    gaps: list[str]
        Stems that are linked to but have no page file.

    Returns
    -------
    Path
        Path to the written gaps view file.
    """
    lines = [
        "---",
        "title: Gap Analysis",
        "type: view",
        "---",
        "",
        "# Missing Pages (Gaps)",
        "",
        "These [[wikilinks]] reference pages that don't exist yet:",
        "",
    ]
    for gap in gaps:
        lines.append(f"- [[{gap}]]")

    path = wiki_dir / "_views" / "gaps.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def generate_domain_views(
    wiki_dir: Path,
    pages: dict[str, WikiPage],
) -> list[Path]:
    """
    Generate per-domain view files in ``_views/domains/``.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    pages: dict[str, WikiPage]
        Mapping of page stem to WikiPage.

    Returns
    -------
    list[Path]
        Paths to all written domain view files.
    """
    by_domain: dict[str, list[WikiPage]] = defaultdict(list)
    for page in pages.values():
        for domain in page.frontmatter.get("domains", []):
            by_domain[domain].append(page)

    views_dir = wiki_dir / "_views" / "domains"
    views_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for domain, domain_pages in sorted(by_domain.items()):
        domain_pages.sort(key=lambda p: p.stem)
        nice_name = domain.replace("-", " ").title()

        by_type: dict[str, list[WikiPage]] = defaultdict(list)
        for p in domain_pages:
            by_type[p.frontmatter.get("type", "other")].append(p)

        lines = [
            "---",
            f"title: {nice_name}",
            "type: domain-view",
            "---",
            "",
            f"# {nice_name}",
            "",
        ]

        for ctype in ("concept", "problem", "project", "insight", "other"):
            type_pages = by_type.get(ctype, [])
            if not type_pages:
                continue
            lines.append(f"## {ctype.title()}s")
            lines.append("")
            for p in type_pages:
                title = p.frontmatter.get("title", p.stem)
                lines.append(f"- [[{p.stem}|{title}]]")
            lines.append("")

        path = views_dir / f"{domain}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        paths.append(path)

    return paths


def generate_recently_updated(
    wiki_dir: Path,
    pages: dict[str, WikiPage],
    limit: int = 30,
) -> Path:
    """
    Generate ``recently-updated.md`` sorted by file mtime.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    pages: dict[str, WikiPage]
        Mapping of page stem to WikiPage.
    limit: int
        Maximum number of pages to include.

    Returns
    -------
    Path
        Path to the written recently-updated view file.
    """
    page_list = sorted(
        pages.values(),
        key=lambda p: p.path.stat().st_mtime,
        reverse=True,
    )[:limit]

    lines = [
        "---",
        "title: Recently Updated",
        "type: view",
        "---",
        "",
        "# Recently Updated Pages",
        "",
    ]
    for page in page_list:
        title = page.frontmatter.get("title", page.stem)
        lines.append(f"- [[{page.stem}|{title}]]")

    path = wiki_dir / "_views" / "recently-updated.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def rebuild_structure(wiki_dir: Path) -> dict:
    """
    Run a full deterministic structure rebuild.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.

    Returns
    -------
    dict
        Summary statistics with keys ``total_pages``,
        ``total_links``, ``orphans``, and ``gaps``.
    """
    pages = discover_all_pages(wiki_dir)
    graph = build_link_graph(pages)

    generate_index(wiki_dir, pages)
    generate_backlinks(wiki_dir, graph)
    orphans = detect_orphans(pages, graph)
    gaps = detect_gaps(pages, graph)
    generate_gaps_view(wiki_dir, gaps)
    generate_domain_views(wiki_dir, pages)
    generate_recently_updated(wiki_dir, pages)

    stats = {
        "total_pages": len(pages),
        "total_links": sum(len(v) for v in graph.forward.values()),
        "orphans": len(orphans),
        "gaps": len(gaps),
    }
    logger.info(
        "Structure rebuild: %d pages, %d links, %d orphans, %d gaps",
        stats["total_pages"],
        stats["total_links"],
        stats["orphans"],
        stats["gaps"],
    )
    return stats
