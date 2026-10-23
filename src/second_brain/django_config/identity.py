"""Persistent private identity for a backend installation in a vault."""

import os
import secrets
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def signing_key(vault: Path) -> str:
    """Create the signing key once, including when two processes start together."""
    directory = vault / ".backend"
    key_file = directory / "django-secret-key"
    if not key_file.exists():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Publish a complete, owner-only file without replacing another process's
        # key. tempfile creates mode 0600; the hard link retains those permissions.
        descriptor, temporary = tempfile.mkstemp(prefix=".signing-key-", dir=directory)
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
        raise ImproperlyConfigured(f"The backend signing-key file is empty: {key_file}")
    return key
