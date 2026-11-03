"""Diagnostics are a backend operation exposed by the thin CLI."""

import json

from click.testing import CliRunner

from second_brain.cli import main


def test_doctor_reads_backend_diagnostics(monkeypatch):
    calls = []

    def request(self, method, path, **kwargs):
        calls.append((method, path))
        return {"healthy": True}

    monkeypatch.setattr("second_brain.backend_client.BackendClient.request", request)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert calls == [("GET", "/diagnostics")]


def test_unhealthy_doctor_preserves_json_and_exits_nonzero(monkeypatch):
    diagnostics = {"healthy": False, "message": "Start Ollama and try again."}
    monkeypatch.setattr(
        "second_brain.backend_client.BackendClient.request", lambda *args, **kwargs: diagnostics
    )
    result = CliRunner().invoke(main, ["doctor", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output) == diagnostics
