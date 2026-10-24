"""Check runtime configuration in fresh interpreters, outside Django's settings cache."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from second_brain.django_config import environment

_INSPECT_SETTINGS = """
import json
import sys
import django
from django.conf import settings
from django.db import connection
from django.test import Client

django.setup()
client = Client(HTTP_HOST='127.0.0.1')
health = client.get('/v1/health')
docs = client.get('/v1/docs')
schema = client.get('/v1/openapi.json')
print(json.dumps({
    'database': str(settings.DATABASES['default']['NAME']),
    'debug': settings.DEBUG,
    'port': getattr(settings, 'BACKEND_PORT', None),
    'health': health.status_code,
    'docs': docs.status_code,
    'schema': schema.status_code,
    'database_open': connection.connection is not None,
    'pipeline_loaded': any(name in sys.modules for name in (
        'second_brain.apps.compilation.services.compiler',
        'second_brain.apps.ingestion.services.source_scanning',
    )),
}))
"""


def _inspect_settings(
    env: dict[str, str], bootstrap: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", bootstrap + _INSPECT_SETTINGS],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def dotenv_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    checkout = tmp_path / "checkout"
    module = checkout / "src" / "second_brain" / "django_config" / "environment.py"
    module.parent.mkdir(parents=True)
    (checkout / "pyproject.toml").touch()
    monkeypatch.setattr(environment, "__file__", str(module))
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    # Restore variables that load_dotenv may add to the test process.
    for name in ("SECOND_BRAIN_VAULT", "SECOND_BRAIN_DEBUG", "SECOND_BRAIN_PORT"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return checkout


def test_defaults_work_without_an_env_file(backend_env: dict[str, str]) -> None:
    backend_env.pop("SECOND_BRAIN_VAULT")
    result = _inspect_settings(backend_env)

    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["database"] == str(Path.home() / "second-brain" / "manifest.db")
    assert config["debug"] is False
    assert config["port"] == 8000
    assert config["docs"] == 404
    assert config["database_open"] is False


@pytest.mark.parametrize("debug", ["true", "false"])
def test_asgi_with_explicit_signing_key_does_not_create_storage_or_load_pipeline(
    backend_env: dict[str, str], debug: str
) -> None:
    backend_env.update(SECOND_BRAIN_DEBUG=debug, SECOND_BRAIN_SECRET_KEY="test-only-signing-key")
    backend_env.pop("DJANGO_SETTINGS_MODULE")  # Exercise ASGI's default.
    result = subprocess.run(
        [sys.executable, "-c", "import second_brain.django_config.asgi\n" + _INSPECT_SETTINGS],
        env=backend_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    vault = Path(backend_env["SECOND_BRAIN_VAULT"])
    assert config["database"] == str(vault / "manifest.db")
    assert config["health"] == 200
    assert config["database_open"] is False
    assert config["pipeline_loaded"] is False
    assert not vault.exists()
    assert config["debug"] is (debug == "true")
    assert config["docs"] == (200 if debug == "true" else 404)
    assert config["schema"] == (200 if debug == "true" else 404)


def test_env_file_is_shared_by_management_and_asgi_with_process_values_taking_priority(
    tmp_path: Path, backend_env: dict[str, str], dotenv_checkout: Path
) -> None:
    vault = tmp_path / "vault from dotenv"
    env_file = dotenv_checkout / ".env"
    env_file.write_text(
        f'SECOND_BRAIN_VAULT="{vault}"\n'
        "SECOND_BRAIN_DEBUG=false\n"
        "SECOND_BRAIN_SECRET_KEY=test-only-signing-key\n"
        "SECOND_BRAIN_PORT=8100\n"
    )
    backend_env.pop("SECOND_BRAIN_VAULT")
    backend_env.pop("PYTHON_DOTENV_DISABLED")
    backend_env["SECOND_BRAIN_PORT"] = "8200"
    bootstrap = (
        "from second_brain.django_config import environment\n"
        f"environment.__file__ = {environment.__file__!r}\n"
    )
    result = _inspect_settings(backend_env, bootstrap)

    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["database"] == str(vault / "manifest.db")
    assert config["debug"] is False
    assert config["port"] == 8200

    backend_env.pop("DJANGO_SETTINGS_MODULE")  # Exercise manage.py's default.
    manage = Path(__file__).resolve().parents[4] / "manage.py"
    managed = subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap + "import runpy, sys; sys.argv = sys.argv[1:]; "
            "runpy.run_path(sys.argv[0], run_name='__main__')",
            str(manage),
            "shell",
            "--command",
            "from django.conf import settings; "
            "print(settings.DATABASES['default']['NAME']); print(settings.BACKEND_PORT)",
        ],
        cwd=tmp_path,
        env=backend_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert managed.returncode == 0, managed.stderr
    assert str(vault / "manifest.db") in managed.stdout
    assert "8200" in managed.stdout
    assert not vault.exists()


def test_default_dotenv_is_resolved_from_the_checkout_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dotenv_checkout: Path
) -> None:
    (dotenv_checkout / ".env").write_text("SECOND_BRAIN_PORT=8100\n")
    (tmp_path / ".env").write_text("SECOND_BRAIN_PORT=9999\n")
    monkeypatch.chdir(tmp_path)

    environment.load_environment()

    assert os.environ["SECOND_BRAIN_PORT"] == "8100"


def test_launcher_can_disable_dotenv(monkeypatch: pytest.MonkeyPatch, dotenv_checkout: Path):
    (dotenv_checkout / ".env").write_text("SECOND_BRAIN_DEBUG=true\n")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    environment.load_environment()

    assert "SECOND_BRAIN_DEBUG" not in os.environ


def test_test_settings_ignore_invalid_runtime_values(
    backend_env: dict[str, str],
) -> None:
    backend_env.update(
        DJANGO_SETTINGS_MODULE="second_brain.django_config.settings.test",
        SECOND_BRAIN_DEBUG="invalid",
        SECOND_BRAIN_PORT="invalid",
        SECOND_BRAIN_VAULT="relative/path",
    )
    result = _inspect_settings(backend_env)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["database"] == ":memory:"


@pytest.mark.parametrize(
    ("extra_env", "message"),
    [
        ({"SECOND_BRAIN_DEBUG": "maybe"}, "SECOND_BRAIN_DEBUG must be true or false"),
        ({"SECOND_BRAIN_VAULT": ""}, "SECOND_BRAIN_VAULT must not be empty"),
        ({"SECOND_BRAIN_VAULT": "relative/path"}, "must be an absolute path"),
        ({"SECOND_BRAIN_PORT": "not-a-port"}, "SECOND_BRAIN_PORT must be an integer"),
        ({"SECOND_BRAIN_PORT": "65536"}, "SECOND_BRAIN_PORT must be an integer"),
    ],
)
def test_invalid_configuration_fails_before_touching_storage(
    backend_env: dict[str, str], extra_env: dict[str, str], message: str
) -> None:
    vault = Path(backend_env["SECOND_BRAIN_VAULT"])
    backend_env.update(extra_env)
    result = _inspect_settings(backend_env)

    assert result.returncode != 0
    assert message in result.stderr
    assert not vault.exists()
