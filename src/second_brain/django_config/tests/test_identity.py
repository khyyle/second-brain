"""Signing identity survives restarts without requiring developer configuration."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from second_brain.django_config.identity import signing_key


def test_concurrent_startup_uses_one_private_persistent_key(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _: signing_key(vault), range(16)))

    assert len(set(keys)) == 1
    assert len(keys[0]) >= 64
    assert signing_key(vault) == keys[0]
    assert (vault / ".backend" / "django-secret-key").stat().st_mode & 0o777 == 0o600
    assert list((vault / ".backend").glob(".identity-*")) == []
