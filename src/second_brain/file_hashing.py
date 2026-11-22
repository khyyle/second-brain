"""Content fingerprints for change detection."""

from __future__ import annotations

import hashlib
from pathlib import Path

# Bound memory use when hashing large source files.
_READ_CHUNK_BYTES = 65536


def hash_file(file_path: Path) -> str:
    """
    Compute the SHA-256 of a file's bytes, read in chunks.

    Parameters
    ----------
    file_path: Path
        File to hash.

    Returns
    -------
    str
        Hex-encoded SHA-256 digest.
    """
    digest = hashlib.sha256()
    with open(file_path, "rb") as file:
        while chunk := file.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
