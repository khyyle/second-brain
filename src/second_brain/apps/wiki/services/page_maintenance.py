"""Merge duplicate pages and repair links while preserving source metadata."""

import re
from dataclasses import dataclass
from pathlib import Path

from second_brain.apps.wiki.services.git_history import WikiRepoError, commit_all
from second_brain.apps.wiki.services.slugs import (
    _WIKILINK_RE,
    _mask_protected_spans,
    _restore_protected_spans,
    normalize_link_list,
    normalize_link_target,
)
from second_brain.apps.wiki.services.wiki_structure import (
    _FRONTMATTER_RE,
    CONTENT_DIRS,
    FRONTMATTER_EDGE_FIELDS,
    _parse_frontmatter,
    content_page_path,
    rebuild_structure,
    update_frontmatter,
)


@dataclass(frozen=True)
class PageRepair:
    """How many links one page had repointed by a repair pass."""

    rel_path: str
    links_repointed: int


def repair_links(
    wiki_dir: Path, mapping: dict[str, str], dry_run: bool = False
) -> list[PageRepair]:
    """
    Repoint every link whose target stem was renamed.

    Walks all content pages and rewrites body wikilinks and frontmatter edges
    that resolve to an old stem in `mapping`, leaving display labels and all
    other text untouched. Code and math spans are never rewritten.

    Parameters
    ----------
    wiki_dir: Path
        path to the top level wiki folder
    mapping: dict[str, str]
        old stem -> new stem, as returned by `detect_renames`.
    dry_run: bool
        when True, report what would change without writing anything.

    Returns
    -------
    list[PageRepair]
        One entry per page that changed, with its repointed-link count.
    """
    if not mapping:
        return []

    repairs: list[PageRepair] = []
    for content_dir in CONTENT_DIRS:
        dir_path = wiki_dir / content_dir
        if not dir_path.exists():
            continue
        for md_file in sorted(dir_path.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            rewritten, repointed = _repoint_page(content, mapping)
            if not repointed:
                continue
            repairs.append(PageRepair(f"{content_dir}/{md_file.name}", repointed))
            if not dry_run:
                md_file.write_text(rewritten, encoding="utf-8")
    return repairs


def _repoint_page(content: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Rewrite one page's renamed link targets, returning the result and count."""
    repointed = 0

    def _repoint(match: re.Match) -> str:
        nonlocal repointed
        target, display = match.group(1), match.group(2)
        new_stem = mapping.get(normalize_link_target(target))
        if new_stem is None:
            return match.group(0)
        repointed += 1
        return f"[[{new_stem}|{display}]]" if display else f"[[{new_stem}]]"

    working = content

    # frontmatter edges first, so the body pass never sees the yaml block
    if _FRONTMATTER_RE.match(working):
        frontmatter = _parse_frontmatter(working)
        updates: dict[str, list[str]] = {}
        for edge_field in FRONTMATTER_EDGE_FIELDS:
            entries = frontmatter.get(edge_field)
            if not isinstance(entries, list):
                continue
            rewritten_entries: list[str] = []
            field_changed = False
            for entry in entries:
                new_stem = (
                    mapping.get(normalize_link_target(entry)) if isinstance(entry, str) else None
                )
                if new_stem is None:
                    rewritten_entries.append(entry)
                else:
                    rewritten_entries.append(f"[[{new_stem}]]")
                    field_changed = True
                    repointed += 1
            if field_changed:
                updates[edge_field] = rewritten_entries
        if updates:
            rewritten = update_frontmatter(working, updates)
            if rewritten is not None:
                working = rewritten

    # body wikilinks, with code and math masked so bracket literals survive
    boundary = _FRONTMATTER_RE.match(working)
    head = working[: boundary.end()] if boundary else ""
    body = working[boundary.end() :] if boundary else working
    masked, spans = _mask_protected_spans(body)
    masked = _WIKILINK_RE.sub(_repoint, masked)
    body = _restore_protected_spans(masked, spans)

    return head + body, repointed


# The list-valued frontmatter fields a merge unions into the destination.
_MERGEABLE_LIST_FIELDS = ("domains", "tags", "sources", *FRONTMATTER_EDGE_FIELDS)


def merge_pages(wiki_dir: Path, dest: str, sources: list[str]) -> list[PageRepair]:
    """
    Retire duplicate pages into one, keeping provenance and repointing links.

    Prose is not moved. The merge unions each retired page's list frontmatter
    into the destination, deletes the retired pages, repoints every link in the
    vault at the destination, rebuilds the derived views, and checkpoints
    the result into the wiki's git history. The retired pages' raw sources
    stay marked compiled, since their knowledge now lives in the destination.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    dest: str
        stem or title of the page that survives.
    sources: list[str]
        stems or titles of the duplicate pages to retire into `dest`.

    Returns
    -------
    list[PageRepair]
        One entry per page whose links were repointed.

    Raises
    ------
    WikiRepoError
        When a named page does not exist, or `dest` is also listed in
        `sources`.
    """
    dest_stem = normalize_link_target(dest)
    dest_path = content_page_path(wiki_dir, dest_stem)
    if dest_path is None:
        raise WikiRepoError(f"No page found for '{dest}'")

    retired: dict[str, Path] = {}
    for source in sources:
        stem = normalize_link_target(source)
        if stem == dest_stem:
            raise WikiRepoError("Cannot merge a page into itself")
        path = content_page_path(wiki_dir, stem)
        if path is None:
            raise WikiRepoError(f"No page found for '{source}'")
        retired[stem] = path

    # union each retired page's list frontmatter into the destination,
    # dropping edges that would point at the merged result itself
    merged_stems = {dest_stem, *retired}
    dest_text = dest_path.read_text(encoding="utf-8")
    frontmatter = _parse_frontmatter(dest_text)
    unions: dict[str, list[str]] = {}
    for field_name in _MERGEABLE_LIST_FIELDS:
        combined = _as_list(frontmatter.get(field_name))
        for path in retired.values():
            retired_frontmatter = _parse_frontmatter(path.read_text(encoding="utf-8"))
            combined += _as_list(retired_frontmatter.get(field_name))
        if field_name in FRONTMATTER_EDGE_FIELDS:
            combined = [
                entry
                for entry in normalize_link_list(combined)
                if normalize_link_target(entry) not in merged_stems
            ]
        else:
            combined = list(dict.fromkeys(combined))
        # An empty union still overwrites: it means the destination's only
        # edge pointed at a retired page, and leaving it would let the link
        # repair below turn it into a self-link.
        if combined != _as_list(frontmatter.get(field_name)):
            unions[field_name] = combined
    if unions:
        updated = update_frontmatter(dest_text, unions)
        if updated is not None:
            dest_path.write_text(updated, encoding="utf-8")

    for path in retired.values():
        path.unlink()

    repairs = repair_links(wiki_dir, dict.fromkeys(retired, dest_stem))

    rebuild_structure(wiki_dir)
    commit_all(wiki_dir, f"auto: merge {', '.join(retired)} into {dest_stem}")
    return repairs


def _as_list(value: object) -> list[str]:
    """Coerce a frontmatter value to a list of strings, dropping non-strings."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str)]
    return []
