"""Git checkpoints, history, rollback, and rename detection for the wiki."""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from second_brain.apps.wiki.services.wiki_structure import (
    CONTENT_DIRS,
    _parse_frontmatter,
)
from second_brain.django_config.errors import ServiceError

logger = logging.getLogger(__name__)

# Injected via %x1e in the log format so records split unambiguously
_RECORD_SEPARATOR = "\x1e"


class WikiRepoError(ServiceError):
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
    Checkpoint the wiki tree before recording a completed build version.

    Initializes the repository on first use, stages everything, and commits
    with `message`. A clean tree is a quiet no-op. Git failures propagate so
    the caller cannot mark source versions built without a durable checkpoint.
    Commits use the application identity without requiring global Git configuration.

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
        was unchanged.

    Raises
    ------
    WikiRepoError
        The Git checkpoint failed.
    """
    if not (wiki_dir / ".git").exists():
        _git(wiki_dir, "init")
    _git(wiki_dir, "add", "-A")
    if not _git(wiki_dir, "status", "--porcelain").strip():
        return False
    _git(
        wiki_dir,
        "-c",
        "user.name=Second Brain",
        "-c",
        "user.email=second-brain@localhost",
        "commit",
        "-m",
        message,
    )
    logger.info("Committed wiki changes: %s", message)
    return True


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
