"""Delete and restage raw sources while keeping file moves and related records consistent."""

from uuid import uuid4

from django.db import transaction

from second_brain.apps.compilation.models import CompiledSource, DeferredSource
from second_brain.apps.library.errors import SourceConflictError, SourceNotFoundError
from second_brain.apps.library.models import Source, TriageDecision
from second_brain.apps.library.services.source_files import (
    checked_path,
    reversible_moves,
    source_locations,
)
from second_brain.apps.library.services.source_locks import require_sources_idle
from second_brain.django_config.paths import VaultPaths
from second_brain.django_config.vault_storage import vault_lock


def delete_source(paths: VaultPaths, raw_path: str) -> None:
    """Move the vault copy to .trash/<unique-id>/ and remove its related DB records.

    Parameters
    ----------
    paths: VaultPaths
        Startup-owned paths for the active vault. Django settings select the matching database.
    raw_path: str
        Logical Markdown path relative to raw/, such as "chatgpt/chat.md". Use the
        same path for a skipped source; do not include the .skipped/ prefix.

    Returns
    -------
    None

    Raises
    ------
    InvalidSourcePathError
        The path is unsafe, follows a symlink, or identifies a directory.
    SourceConflictError
        Both active and skipped copies exist, or a move would overwrite a file.

    Notes
    -----
    Removes matching Source, TriageDecision, CompiledSource, and DeferredSource rows
    in one database transaction.
    Repeating a deletion succeeds. Shared page-cache entries and original watched
    files are preserved. A failed database write/commit restores the moved raw file.
    Filesystem/database failures propagate. This service must own the outer database
    transaction so file recovery also covers commit failure; it is not crash recovery.
    """
    with (
        vault_lock("raw-sources", blocking=False),
        reversible_moves() as files,
        transaction.atomic(durable=True),
    ):
        require_sources_idle([raw_path])
        active, skipped = source_locations(paths.data_dir, raw_path)
        source = active if active.exists() else skipped
        if source.exists():
            trash = checked_path(paths.data_dir, ".trash", uuid4().hex, raw_path)
            files.move(source, trash)
        # Related records use raw paths without foreign keys; delete each explicitly.
        Source.objects.filter(raw_output=raw_path).delete()
        TriageDecision.objects.filter(raw_path=raw_path).delete()
        CompiledSource.objects.filter(raw_path=raw_path).delete()
        DeferredSource.objects.filter(raw_path=raw_path).delete()


def stage_recompile(paths: VaultPaths, raw_paths: list[str]) -> None:
    """Clear completion/deferral marks atomically; the next manual build does the work.

    Parameters
    ----------
    paths: VaultPaths
        Configuration identifying the active vault's raw directory.
    raw_paths: list[str]
        Logical paths relative to raw/ for the selected Markdown sources. Duplicate
        paths are processed once.

    Returns
    -------
    None

    Raises
    ------
    InvalidSourcePathError
        Any selected path is unsafe, follows a symlink, or identifies a directory.
    SourceNotFoundError
        A selected raw file is missing.
    SourceConflictError
        A selected source is skipped, or both active and skipped copies exist.

    Notes
    -----
    Validates the full selection before clearing records. Existing triage decisions
    still apply; skipped sources must be restored before requesting recompilation.
    """
    selected = list(dict.fromkeys(raw_paths))
    with vault_lock("raw-sources", blocking=False), transaction.atomic():
        require_sources_idle(selected)
        for raw_path in selected:
            active, skipped = source_locations(paths.data_dir, raw_path)
            if skipped.exists():
                raise SourceConflictError(f"Restore {raw_path} before requesting recompilation.")
            if not active.exists():
                raise SourceNotFoundError(f"The raw file for {raw_path} is no longer available.")
        CompiledSource.objects.filter(raw_path__in=selected).delete()
        DeferredSource.objects.filter(raw_path__in=selected).delete()
