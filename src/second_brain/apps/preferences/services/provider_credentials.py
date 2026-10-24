"""Read provider credentials from macOS Keychain; never persist or serialize secrets."""

import subprocess
import sys

from second_brain.django_config.errors import DependencyError, ServiceError
from second_brain.llm.providers import SUPPORTED_MODELS

KEYCHAIN_SERVICE = "com.second-brain.providers"


def read_api_key(provider: str) -> str | None:
    """Read a provider credential from the macOS Keychain generic-password store.

    Parameters
    ----------
    provider: str
        Supported provider ID, used as the account under the
        com.second-brain.providers Keychain service.

    Returns
    -------
    str | None
        Secret in process memory only, or None when the item does not exist.

    Raises
    ------
    ServiceError
        Unknown provider.
    DependencyError
        Keychain is unavailable, locked, or access is denied. No environment fallback.
    """
    if provider not in SUPPORTED_MODELS:
        raise ServiceError("Unknown model provider.")
    if sys.platform != "darwin":
        raise DependencyError("Provider credentials require macOS Keychain.")
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                provider,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DependencyError("Unable to read the provider credential from Keychain.") from exc
    if result.returncode == 44:
        return None
    if result.returncode:
        raise DependencyError("Keychain denied access to the provider credential.")
    return result.stdout.rstrip("\r\n") or None
