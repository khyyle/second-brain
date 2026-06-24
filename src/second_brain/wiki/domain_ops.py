"""Bulk domain vocabulary edits — list, rename, merge, and delete.

A domain lives in two places that must stay in lockstep: the ``domains`` list in
each page's frontmatter (the truth) and the ``domains`` map in
``_meta/topic_schema.yaml`` (the canonical vocabulary). These operations rewrite
both, prune stale per-domain views, and regenerate derived structure so counts
and ``_views/domains/`` stay correct. The search index is reconciled separately
by the caller, which holds the search configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from second_brain.wiki.schema import remap_schema_domains
from second_brain.wiki.structure import (
    _FRONTMATTER_RE,
    CONTENT_DIRS,
    discover_all_pages,
    rebuild_structure,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainInfo:
    """A domain and how many pages currently declare it."""

    name: str
    page_count: int
    in_schema: bool


def list_domains(wiki_dir: Path) -> list[DomainInfo]:
    """Return every domain in use or registered, with its page count.

    The list is the union of domains found in page frontmatter (the truth) and
    those registered in ``topic_schema.yaml``, so a registered-but-empty domain
    still shows up (and can be cleaned up) and a used-but-unregistered one is
    visible too.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.

    Returns
    -------
    list[DomainInfo]
        Domains sorted by name.
    """
    counts: dict[str, int] = {}
    for page in discover_all_pages(wiki_dir).values():
        for domain in page.frontmatter.get("domains") or []:
            counts[domain] = counts.get(domain, 0) + 1
    registered = _registered_domains(wiki_dir)
    names = sorted(set(counts) | registered)
    return [DomainInfo(name, counts.get(name, 0), name in registered) for name in names]


def rename_domain(wiki_dir: Path, old: str, new: str) -> int:
    """Rename a domain everywhere it appears.

    Returns
    -------
    int
        Number of pages whose frontmatter changed.
    """
    return _remap(wiki_dir, {old.strip(): _validate_target(new)})


def merge_domains(wiki_dir: Path, sources: list[str], dest: str) -> int:
    """Merge each domain in ``sources`` into ``dest``.

    Returns
    -------
    int
        Number of pages whose frontmatter changed.
    """
    dest = _validate_target(dest)
    mapping = {s.strip(): dest for s in sources if s.strip() and s.strip() != dest}
    if not mapping:
        return 0
    return _remap(wiki_dir, mapping)


def delete_domain(wiki_dir: Path, name: str) -> int:
    """Remove a domain from every page and the schema.

    A page left with no domains keeps its ``domains`` key as an empty list and
    is surfaced under "Uncategorized" in the index.

    Returns
    -------
    int
        Number of pages whose frontmatter changed.
    """
    return _remap(wiki_dir, {name.strip(): None})


def _validate_target(name: str) -> str:
    """Return a cleaned target domain name or raise on an unusable one."""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Domain name cannot be empty")
    if "," in cleaned:
        # Domains are stored comma-joined in the search index; a comma would
        # split one domain into two.
        raise ValueError("Domain name cannot contain a comma")
    return cleaned


def _registered_domains(wiki_dir: Path) -> set[str]:
    """Read the domain names registered in the schema without writing defaults."""
    schema_path = wiki_dir / "_meta" / "topic_schema.yaml"
    if not schema_path.exists():
        return set()
    raw = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
    return set(raw.get("domains") or {})


def _remap(wiki_dir: Path, mapping: dict[str, str | None]) -> int:
    """Apply an old->new (or old->None to delete) domain mapping end to end."""
    changed = 0
    for content_dir in CONTENT_DIRS:
        dir_path = wiki_dir / content_dir
        if not dir_path.exists():
            continue
        for md_file in dir_path.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            rewritten = _rewrite_page(content, mapping)
            if rewritten is not None and rewritten != content:
                md_file.write_text(rewritten, encoding="utf-8")
                changed += 1

    remap_schema_domains(wiki_dir, mapping)
    _prune_domain_views(wiki_dir, mapping)
    rebuild_structure(wiki_dir)
    logger.info("Remapped domains %s across %d page(s)", mapping, changed)
    return changed


def _rewrite_page(content: str, mapping: dict[str, str | None]) -> str | None:
    """Return page content with frontmatter domains remapped, or None to skip.

    None means the page has no frontmatter/domains, or the remap leaves the list
    unchanged, so the caller can avoid a needless write.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None

    raw_domains = frontmatter.get("domains")
    if not raw_domains:
        return None
    original = [raw_domains] if isinstance(raw_domains, str) else list(raw_domains)

    remapped: list[str] = []
    for domain in original:
        target = mapping[domain] if domain in mapping else domain
        if target is None:
            continue
        if target not in remapped:
            remapped.append(target)

    if remapped == original:
        return None

    frontmatter["domains"] = remapped
    new_frontmatter = yaml.safe_dump(
        frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    body = content[match.end() :]
    return f"---\n{new_frontmatter}---\n{body}"


def _prune_domain_views(wiki_dir: Path, mapping: dict[str, str | None]) -> None:
    """Delete view files for source domains removed by the remap.

    ``rebuild_structure`` writes a view per current domain but never removes one,
    so a renamed/merged/deleted source would otherwise leave a stale file behind.
    """
    views_dir = wiki_dir / "_views" / "domains"
    for old, new in mapping.items():
        if new != old:
            (views_dir / f"{old}.md").unlink(missing_ok=True)
