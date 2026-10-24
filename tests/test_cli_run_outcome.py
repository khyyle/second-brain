"""The CLI submits work to Django without running algorithms or touching the database."""

from click.testing import CliRunner

from second_brain.cli import main


def test_compile_submits_job(monkeypatch):
    calls = []

    def request(self, method, path, **kwargs):
        calls.append((method, path, kwargs["payload"]))
        return {"id": "attempt", "status": "queued"}

    monkeypatch.setattr("second_brain.backend_client.BackendClient.request", request)
    result = CliRunner().invoke(main, ["compile", "--dry-run"])
    assert result.exit_code == 0
    assert calls == [("POST", "/builds", {"full": False, "dry_run": True})]
    assert "attempt" in result.output


def test_backend_error_is_a_cli_error(monkeypatch):
    def request(*args, **kwargs):
        raise RuntimeError("The worker is unavailable.")

    monkeypatch.setattr("second_brain.backend_client.BackendClient.request", request)
    result = CliRunner().invoke(main, ["status"])
    assert result.exit_code == 1
    assert "The worker is unavailable." in result.output


def test_invalid_json_never_reaches_backend(monkeypatch):
    result = CliRunner().invoke(main, ["api", "POST", "/builds", "--json", "{"])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output
