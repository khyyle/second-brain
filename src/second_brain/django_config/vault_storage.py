"""Cross-process file locks and atomic replacement for vault-owned artifacts."""

import fcntl
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from second_brain.django_config.errors import ConflictError
from second_brain.django_config.paths import get_vault_paths


@contextmanager
def vault_lock(name: str, *, blocking: bool = True) -> Iterator[None]:
    """Hold a named advisory lock across processes; release it even after exceptions.

    Parameters
    ----------
    name: str
        Internal lock name, never supplied by an HTTP client.
    blocking: bool
        Wait for the owner, or raise ConflictError immediately if busy.

    Yields
    ------
    None
        Exclusive ownership of the named vault lock until context exit.

    Notes
    -----
    The OS releases the lock after process death. The lock file is never unlinked:
    replacing it would allow two processes to lock different inodes.
    """
    directory = get_vault_paths().backend_dir / "locks"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / f"{name}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ConflictError(
                f"Another operation is using {name}. Try again when it finishes."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_write(path: Path, content: str) -> None:
    """Replace a UTF-8 file with owner-only permissions after flushing its contents.

    Parameters
    ----------
    path: Path
        Destination file; missing parent directories are created.
    content: str
        UTF-8 text to publish.

    Returns
    -------
    None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
