from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.apps.compilation.services.build_records import BuildStore
from second_brain.apps.ingestion.services.source_manifest import Manifest
from second_brain.apps.ingestion.services.triage.triage_decisions import TriageStore
from second_brain.pipeline_config import PipelineConfig, SearchConfig


def pytest_addoption(parser):
    parser.addoption(
        "--run-gui-http",
        action="store_true",
        default=False,
        help="Run macOS Swift integration tests against a disposable local backend.",
    )


@pytest.fixture(autouse=True)
def _block_real_keychain(monkeypatch):
    """Unit tests must explicitly supply credentials; never read the user's Keychain."""

    def unexpected_read(provider):
        raise AssertionError("Use provider_key or mock read_api_key in this test.")

    monkeypatch.setattr(
        "second_brain.apps.preferences.services.provider_credentials.read_api_key",
        unexpected_read,
    )


@pytest.fixture
def provider_key(monkeypatch, _block_real_keychain):
    """A fake Keychain credential shared by compilation and API-to-worker tests."""
    key = "test-only-provider-key"
    monkeypatch.setattr(
        "second_brain.apps.preferences.services.provider_credentials.read_api_key",
        lambda provider: key,
    )
    return key


@pytest.fixture
def config(tmp_path: Path, settings, transactional_db) -> PipelineConfig:
    """Isolated default PipelineConfig rooted under a temporary directory."""
    cfg = PipelineConfig(data_dir=tmp_path / "second-brain")
    settings.VAULT_DIR = cfg.data_dir
    settings.BACKEND_PORT = 8000
    cfg.ensure_directories()
    return cfg


@pytest.fixture
def manifest(config: PipelineConfig, db) -> Manifest:
    """Fresh manifest matching the test's isolated config."""
    return Manifest()


@pytest.fixture
def build_store(config: PipelineConfig, db) -> BuildStore:
    """Build records in the test's isolated database."""
    return BuildStore()


@pytest.fixture
def triage_store(config: PipelineConfig, db) -> TriageStore:
    """Triage verdicts in the test's isolated database."""
    return TriageStore()


@pytest.fixture
def search_config() -> SearchConfig:
    """3-dimensional search config for deterministic testing."""
    return SearchConfig(embedding_dimensions=3)


@pytest.fixture(autouse=True)
def _block_unmocked_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests fail immediately if an unmocked HTTP request is attempted."""

    def _fail_network(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Unmocked network request attempted in test suite!")

    monkeypatch.setattr("httpx.Client.request", _fail_network)
    monkeypatch.setattr("httpx.AsyncClient.request", _fail_network)


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate Git operations in tests from user config and host environment."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test Committer")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)


@pytest.fixture
def api_client(config, client, settings):
    """Authenticated Django client bound to this test's isolated vault."""
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {settings.API_TOKEN}"
    return client


@pytest.fixture
def job_factory(transactional_db):
    """Create durable attempts with valid lane defaults; callers override relevant fields."""
    from second_brain.apps.jobs.models import Job

    def create(*, kind="build", **fields):
        values = {"lane": "ingest" if kind == "ingest" else "build", **fields}
        return Job.objects.create(kind=kind, **values)

    return create


@pytest.fixture
def active_job(job_factory):
    """Bind worker progress to a real job and always clear process-local worker state."""
    from second_brain.apps.jobs.services import job_progress

    job = job_factory(status="running")
    job_progress.bind_job(job.pk)
    job_progress._shutdown = False
    try:
        yield job
    finally:
        job_progress.bind_job(None)
        job_progress._shutdown = False


@pytest.fixture
def read_job_progress(active_job):
    """Read a fresh progress snapshot from the ORM, shared by all worker feature tests."""

    def read():
        active_job.refresh_from_db()
        progress = dict(active_job.progress)
        if active_job.heartbeat_at:
            progress["updated_at"] = active_job.heartbeat_at.isoformat()
        return progress

    return read
