"""Keep startup subprocesses independent of personal environment files and vaults."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from second_brain.apps.inference.catalog import MODELS_BY_PURPOSE, ModelPurpose
from second_brain.apps.inference.services.worker_status import WorkerStatus, write_worker_status
from second_brain.django_config.paths import get_vault_paths
from second_brain.pipeline_config import PipelineConfig


@pytest.fixture
def publish_ready_model(config: PipelineConfig) -> Callable[[ModelPurpose], None]:
    """Publish a live status record for tests that supply the model response."""

    def publish(purpose: ModelPurpose) -> None:
        write_worker_status(
            get_vault_paths().backend_dir,
            WorkerStatus(
                purpose=purpose,
                model=MODELS_BY_PURPOSE[purpose].name,
                phase="ready",
                pid=os.getpid(),
                server_pid=os.getpid(),
                port=50111,
            ),
        )

    return publish


@pytest.fixture
def backend_env(tmp_path: Path) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("SECOND_BRAIN_") and name != "PYTHON_DOTENV_DISABLED"
    }
    env.update(
        DJANGO_SETTINGS_MODULE="second_brain.django_config.settings.runtime",
        PYTHON_DOTENV_DISABLED="1",
        SECOND_BRAIN_VAULT=str(tmp_path / "vault"),
        SECOND_BRAIN_SECRET_KEY="test-only-signing-key",
    )
    return env
