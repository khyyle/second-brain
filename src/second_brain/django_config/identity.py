"""Persistent private identity for a backend installation in a vault."""

import os
import secrets
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def signing_key(vault: Path) -> str:
    """Read or create the vault's persistent Django signing key.

    Parameters
    ----------
    vault: Path
        Vault root whose .backend directory stores the key.

    Returns
    -------
    str
        Existing or newly generated private signing key.
    """
    return private_token(vault, "django-secret-key")


def private_token(vault: Path, name: str) -> str:
    """Create a private token once, including when two processes start together.

    Parameters
    ----------
    vault: Path
        Vault root whose .backend directory stores private identity files.
    name: str
        Trusted filename inside .backend; this function does not validate path components.

    Returns
    -------
    str
        Nonempty stored token. Concurrent creators reuse the first fully published file. An
        empty file raises ImproperlyConfigured.
    """
    directory = vault / ".backend"
    key_file = directory / name
    if not key_file.exists():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Publish a complete, owner-only file without replacing another process's
        # key. tempfile creates mode 0600; the hard link retains those permissions.
        descriptor, temporary = tempfile.mkstemp(prefix=".identity-", dir=directory)
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(secrets.token_urlsafe(64))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, key_file)
            except FileExistsError:
                pass
        finally:
            Path(temporary).unlink(missing_ok=True)
    key = key_file.read_text().strip()
    if not key:
        raise ImproperlyConfigured(f"The backend identity file is empty: {key_file}")
    return key
