"""Raw-file containment and reversible moves shared by library operations.

Public raw paths always look like chatgpt/chat.md, including for skipped files.
The service chooses raw/ or raw/.skipped/; clients never address hidden directories.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from second_brain.apps.ingestion.services.triage.skipped import SKIPPED_DIRNAME
from second_brain.apps.library.errors import (
    InvalidSourcePathError,
    SourceConflictError,
    SourceNotFoundError,
)
from second_brain.django_config.paths import VaultPaths


def validate_raw_path(raw_path: str) -> str:
    """Validate the logical identifier used to address parsed Markdown.

    Parameters
    ----------
    raw_path: str
        Relative path such as "documents/notes.md", without a raw/ or .skipped/
        prefix. Absolute paths, hidden components, and traversal are unsupported.

    Returns
    -------
    str
        The original path after validation; this function does not normalize it.

    Raises
    ------
    InvalidSourcePathError
        The identifier is empty, malformed, hidden, or does not end in .md.

    Notes
    -----
    Checks only the string. checked_path performs filesystem containment checks.
    """
    parts = raw_path.split("/")
    if (
        not raw_path
        or "\x00" in raw_path
        or "\\" in raw_path
        or any(not part or part.startswith(".") for part in parts)
        or Path(raw_path).is_absolute()
        or Path(raw_path).suffix != ".md"
    ):
        raise InvalidSourcePathError(
            "Use a relative Markdown path such as chatgpt/chat.md, without hidden folders."
        )
    return raw_path


def checked_path(root: Path, *parts: str) -> Path:
    """Resolve a candidate file under the trusted vault without following symlinks.

    Parameters
    ----------
    root: Path
        Trusted vault root selected by the backend launcher.
    *parts: str
        Path components to append, including service-managed folders such as raw/
        and .skipped/. Validate caller-supplied raw identifiers before passing them.

    Returns
    -------
    Path
        Contained candidate path. The file need not exist, allowing destination checks.

    Raises
    ------
    InvalidSourcePathError
        Any component below root is a symlink, the resolved path escapes root, or
        the final candidate exists but is not a regular file.
    """
    candidate = root
    for part in parts:
        for component in Path(part).parts:
            candidate = candidate / component
            if candidate.is_symlink():
                raise InvalidSourcePathError("Library operations cannot follow symbolic links.")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise InvalidSourcePathError("The source must stay inside the selected vault.")
    if candidate.exists() and not candidate.is_file():
        raise InvalidSourcePathError("The source path must identify a Markdown file.")
    return candidate


def source_locations(vault: Path, raw_path: str) -> tuple[Path, Path]:
    """Check both possible locations for a raw source before reading or moving it.

    Parameters
    ----------
    vault: Path
        Active vault root containing the raw/ directory.
    raw_path: str
        Logical Markdown path relative to raw/, without a .skipped/ prefix.

    Returns
    -------
    tuple[Path, Path]
        Active path followed by skipped path. Either or both may be absent.

    Raises
    ------
    InvalidSourcePathError
        The identifier or either candidate location is unsafe.
    SourceConflictError
        Both copies exist, so choosing one could lose or hide content.
    """
    validate_raw_path(raw_path)
    active = checked_path(vault, "raw", raw_path)
    skipped = checked_path(vault, "raw", SKIPPED_DIRNAME, raw_path)
    if active.exists() and skipped.exists():
        raise SourceConflictError(
            f"{raw_path} exists in both raw and skipped folders. Resolve the duplicate first."
        )
    return active, skipped


def existing_source(vault: Path, raw_path: str) -> Path:
    """Find the single existing active or skipped copy of a raw source.

    Parameters
    ----------
    vault: Path
        Active vault root containing raw/ and raw/.skipped/.
    raw_path: str
        Logical Markdown path relative to raw/, unchanged for skipped sources.

    Returns
    -------
    Path
        The active file, or its skipped copy if only that copy exists.

    Raises
    ------
    SourceNotFoundError
        Neither copy exists.
    InvalidSourcePathError
        The identifier or either candidate location is unsafe.
    SourceConflictError
        Both active and skipped copies exist.
    """
    active, skipped = source_locations(vault, raw_path)
    if active.is_file():
        return active
    if skipped.is_file():
        return skipped
    raise SourceNotFoundError(f"The raw file for {raw_path} is no longer available.")


def read_content(paths: VaultPaths, raw_path: str) -> str:
    """Read parsed Markdown from its active or skipped vault location.

    Parameters
    ----------
    paths: VaultPaths
        Configuration identifying the active vault's data_dir.
    raw_path: str
        Logical Markdown path relative to raw/, without a .skipped/ prefix.

    Returns
    -------
    str
        Complete UTF-8 Markdown text, including any front matter.

    Raises
    ------
    SourceNotFoundError
        The raw file is absent or disappears before it can be opened.
    InvalidSourcePathError
        The identifier or file location is unsafe.
    SourceConflictError
        Both active and skipped copies exist.

    Notes
    -----
    Reads no database records and never opens the original watched input. Other I/O
    failures and invalid UTF-8 propagate rather than being reported as missing files.
    """
    source = existing_source(paths.data_dir, raw_path)
    try:
        return source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        # The file may disappear after lookup; unrelated I/O failures still propagate.
        raise SourceNotFoundError(f"The raw file for {raw_path} is no longer available.") from exc


class FileMoves:
    """Track renames so a failed database transaction can move the files back."""

    def __init__(self) -> None:
        self.moves: list[tuple[Path, Path]] = []

    def move(self, source: Path, destination: Path) -> None:
        """Rename a previously checked file and remember how to restore it.

        Parameters
        ----------
        source: Path
            Existing source path already checked for containment and symlinks.
        destination: Path
            Checked destination path. Missing parent directories are created.

        Returns
        -------
        None

        Raises
        ------
        SourceConflictError
            The destination already exists, including a dangling symlink.
        OSError
            The directory creation or rename fails.

        Notes
        -----
        A successful rename is recorded for undo(); the file's contents are unchanged.
        """
        if destination.exists() or destination.is_symlink():
            raise SourceConflictError("The destination already exists; no files were overwritten.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        self.moves.append((source, destination))

    def undo(self) -> None:
        """Reverse completed moves in reverse order after a failed operation.

        Returns
        -------
        None

        Raises
        ------
        SourceConflictError
            A source path has been recreated by another writer; recovery refuses to
            overwrite it. Copies already moved remain available at their destinations.
        OSError
            A recovery rename fails. Empty directories created by move() are retained.
        """
        for source, destination in reversed(self.moves):
            if source.exists() or source.is_symlink():
                raise SourceConflictError("A file changed during recovery; both copies were kept.")
            destination.rename(source)


@contextmanager
def reversible_moves() -> Iterator[FileMoves]:
    """Provide a move tracker and undo completed moves if the enclosed operation fails.

    Yields
    ------
    FileMoves
        Per-operation tracker used to move validated source files.

    Notes
    -----
    Place outside atomic(durable=True), so database commit failures also undo moves.
    Successful exit keeps the new locations; exceptional exit reverses completed
    moves and re-raises. If recovery itself fails, its exception is propagated.

    This compensates for exceptions, not power loss: SQLite cannot transact files.
    Do not nest a file mutation inside another application's database transaction.
    """
    changes = FileMoves()
    try:
        yield changes
    except BaseException:
        changes.undo()
        raise
