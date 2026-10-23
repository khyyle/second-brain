"""Keep startup subprocesses independent of personal environment files and vaults."""

import os
from pathlib import Path

import pytest


@pytest.fixture
def backend_env(tmp_path: Path) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("SECOND_BRAIN_") and name != "PYTHON_DOTENV_DISABLED"
    }
    env.update(
        DJANGO_SETTINGS_MODULE="second_brain.django_config.settings.runtime",
        SECOND_BRAIN_ENV_FILE="",
        SECOND_BRAIN_VAULT=str(tmp_path / "vault"),
        SECOND_BRAIN_SECRET_KEY="test-only-signing-key",
    )
    return env
