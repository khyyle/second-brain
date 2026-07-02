import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from second_brain.wiki.slugs import (
    _WIKILINK_RE,
    _mask_protected_spans,
    _restore_protected_spans,
    normalize_link_list,
    normalize_link_target,
)
from second_brain.wiki.structure import (
    _FRONTMATTER_RE,
    CONTENT_DIRS,
    FRONTMATTER_EDGE_FIELDS,
    _parse_frontmatter,
    content_page_path,
    rebuild_structure,
    update_frontmatter,
)

logger = logging.getLogger(__name__)

# Injected via %x1e in the log format so records split unambiguously
_RECORD_SEPARATOR = "\x1e"


class WikiRepoError(Exception):
    """A wiki git operation could not run or would lose work."""


@dataclass(frozen=True)
class WikiCommit:
    """One commit in the wiki repo's build history"""

    short_hash: str
    date: str
    subject: str
    files_changed: int


def _git(wiki_dir: Path, *args: str) -> str:
    """Run a git command in the wiki repo and return its stdout."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=wiki_dir, capture_output=True, text=True, check=True
        )
    except FileNotFoundError as exc:
        raise WikiRepoError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"git {' '.join(args)} failed"
        raise WikiRepoError(detail) from exc
    return result.stdout


def list_commits(wiki_dir: Path, limit: int) -> list[WikiCommit]:
    """
    Return the wiki repo's most recent commits, newest first.

    Parameters
    ----------
    wiki_dir: Path
        Root of the wiki git repository.
    limit: int
        Maximum number of commits to return.

    Returns
    -------
    list[WikiCommit]
        Up to `limit` commits, each with the number of files it touched.
    """
    out = _git(
        wiki_dir,
        "log",
        f"-{limit}",
        "--date=short",
        "--numstat",
        f"--format={_RECORD_SEPARATOR}%h%x09%ad%x09%s",
    )

    commits: list[WikiCommit] = []
    for record in out.split(_RECORD_SEPARATOR):
        record = record.strip()
        if not record:
            continue
        header, _, numstat = record.partition("\n")
        short_hash, date, subject = header.split("\t", 2)
        files_changed = sum(1 for line in numstat.splitlines() if line.strip())
        commits.append(WikiCommit(short_hash, date, subject, files_changed))
    return commits


def count_commits(wiki_dir: Path) -> int:
    """Return how many commits the wiki repository currently has."""
    return int(_git(wiki_dir, "rev-list", "--count", "HEAD").strip())


def commit_all(wiki_dir: Path, message: str) -> bool:
    """
    Best-effort checkpoint of the whole wiki tree into its git history.

    Initializes the repository on first use, stages everything, and commits
    with `message`. A clean tree is a quiet no-op, and any git failure is
    logged rather than raised so a checkpoint problem never aborts the
    pipeline work that produced the changes.

    Parameters
    ----------
    wiki_dir: Path
        path to the top level wiki folder
    message: str
        commit message for the checkpoint

    Returns
    -------
    bool
        True when a commit was created, False when the tree was clean or git
        was unavailable.
    """
    try:
        if not (wiki_dir / ".git").exists():
            _git(wiki_dir, "init")
        _git(wiki_dir, "add", "-A")
        if not _git(wiki_dir, "status", "--porcelain").strip():
            return False
        _git(wiki_dir, "commit", "-m", message)
        logger.info("Committed wiki changes: %s", message)
        return True
    except WikiRepoError as exc:
        logger.warning("Wiki commit failed: %s", exc)
        return False


def rollback(wiki_dir: Path, count: int) -> list[str]:
    """
    Reset the wiki repo back an arbitrary number of commits

    Parameters
    ----------
    wiki_dir: Path
        path to the top level wiki folder
    count: int
        how many commits to roll the wiki back

    Returns
    -------
    list[str]
        Source paths of the pages the rolled-back commits touched, exactly as
        their frontmatter records them (``raw/...``), deduplicated in
        first-seen order.
    """

    # if wiki tree dirty, abort to avoid destroying uncommitted edits
    status = _git(wiki_dir, "status", "--porcelain")
    if status.strip():
        raise WikiRepoError(
            "The wiki has uncommitted changes. Commit or discard them before rolling back."
        )

    # refuse to roll back past the root commit
    total_commits = count_commits(wiki_dir)
    if count >= total_commits:
        raise WikiRepoError(
            f"Cannot roll back {count} commit(s): the wiki only has {total_commits}."
        )

    # collect pages touched by the last `count` commits, excluding deletions
    # (the reset restores deleted pages, so their sources need no recompile)
    touched_pages = _git(
        wiki_dir, "diff", "--name-only", "--diff-filter=d", f"HEAD~{count}", "HEAD"
    ).splitlines()

    # read each touched page's sources as committed, before the reset drops them
    rolled_back_sources: list[str] = []
    seen: set[str] = set()
    for page_path in touched_pages:
        if page_path.split("/", 1)[0] not in CONTENT_DIRS:
            continue
        content = _git(wiki_dir, "show", f"HEAD:{page_path}")
        sources = _parse_frontmatter(content).get("sources") or []
        if isinstance(sources, str):
            sources = [sources]
        for source in sources:
            if isinstance(source, str) and source not in seen:
                seen.add(source)
                rolled_back_sources.append(source)

    # reset the wiki. the caller unmarks the returned sources as compiled so
    # the next build regenerates exactly what was rolled away
    _git(wiki_dir, "reset", "--hard", f"HEAD~{count}")

    return rolled_back_sources


def detect_renames(wiki_dir: Path) -> dict[str, str]:
    """
    Map old page stems to new ones for uncommitted renames of content pages.

    Rename detection needs both names in git's index, so the working tree is
    staged, diffed against HEAD with similarity detection, and unstaged again.
    Only the index is touched--no file on disk is modified.

    Parameters
    ----------
    wiki_dir: Path
        path to the top level wiki folder

    Returns
    -------
    dict[str, str]
        old stem -> new stem for every rename detected under a content
        directory. Empty when nothing was renamed.
    """

    # stage everything so rename detection can see the untracked new names
    _git(wiki_dir, "add", "-A")
    try:
        out = _git(wiki_dir, "diff", "--cached", "-M", "--name-status", "HEAD")
    finally:
        # unstage again as a bare reset only rewinds the index
        _git(wiki_dir, "reset")

    mapping: dict[str, str] = {}
    for line in out.splitlines():
        # a rename row is "R<score>\t<old-path>\t<new-path>"
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].startswith("R"):
            continue
        old_path, new_path = parts[1], parts[2]
        if old_path.split("/", 1)[0] not in CONTENT_DIRS:
            continue
        if not old_path.endswith(".md") or not new_path.endswith(".md"):
            continue
        old_stem, new_stem = Path(old_path).stem, Path(new_path).stem
        if old_stem != new_stem:
            mapping[old_stem] = new_stem
    return mapping


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
