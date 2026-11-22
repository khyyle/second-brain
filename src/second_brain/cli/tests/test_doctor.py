"""The doctor command reports both managed workers and preserves status JSON."""

import json

import pytest
from click.testing import CliRunner

from second_brain.cli import main


def test_doctor_reads_both_worker_statuses(monkeypatch):
    calls = []

    def request(self, method, path, **kwargs):
        calls.append((method, path))
        return {
            "worker_statuses": {
                "generation": {"model": "gemma4:12b", "phase": "ready"},
                "embedding": {"model": "nomic-embed-text", "phase": "ready"},
            }
        }

    monkeypatch.setattr("second_brain.backend_client.BackendClient.request", request)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert calls == [("GET", "/inference/status")]
    assert result.output.splitlines() == [
        "[OK] generation: gemma4:12b: ready",
        "[OK] embedding: nomic-embed-text: ready",
    ]


@pytest.mark.parametrize("purpose", ["generation", "embedding"])
@pytest.mark.parametrize(
    "status, detail",
    [
        (None, "Not running"),
        ({"model": "test-model", "phase": "loading"}, "test-model: loading"),
        (
            {"model": "test-model", "phase": "error", "error": "Model download failed."},
            "test-model: error — Model download failed.",
        ),
    ],
)
def test_unready_doctor_explains_failure_and_preserves_json(monkeypatch, purpose, status, detail):
    diagnostics = {
        "worker_statuses": {
            "generation": {"model": "gemma4:12b", "phase": "ready"},
            "embedding": {"model": "nomic-embed-text", "phase": "ready"},
        }
    }
    diagnostics["worker_statuses"][purpose] = status
    monkeypatch.setattr(
        "second_brain.backend_client.BackendClient.request", lambda *args, **kwargs: diagnostics
    )
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 1
    assert f"[FAIL] {purpose}: {detail}" in result.output
    result = CliRunner().invoke(main, ["doctor", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output) == diagnostics
