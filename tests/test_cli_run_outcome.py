"""CLI exit codes and the run record for compile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from second_brain import cli
from second_brain.clustering.preview import CLUSTERS_FILENAME
from second_brain.config import Config
from second_brain.ollama import OllamaStatus, OllamaUnavailableError
from second_brain.run_record import RUN_RECORD_FILENAME, StageOutcome
from second_brain.status import STATUS_FILENAME
from second_brain.triage.pipeline import TriageInterruptedError


def _compile_stats(outcome: StageOutcome, reason: str) -> dict[str, object]:
    return {
        "sources_compiled": 1,
        "total_pages": 2,
        "total_links": 3,
        "orphans": 0,
        "gaps": 1,
        "domains": {},
        "outcome": outcome,
        "reason": reason,
        "counts": {"completed": 1, "deferred": 0, "left": 0},
    }


def _invoke_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stats: dict[str, object]
) -> tuple[Result, dict[str, object]]:
    config = Config(data_dir=tmp_path / "vault")
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_require_ollama", lambda _config: None)
    monkeypatch.setattr("second_brain.state.emit_state", lambda _config: None)
    monkeypatch.setattr(
        "second_brain.compilation.compiler.run_compilation",
        lambda *_args, **_kwargs: stats,
    )
    result = CliRunner().invoke(cli.main, ["compile"])
    record_path = config.data_dir / RUN_RECORD_FILENAME
    record = json.loads(record_path.read_text(encoding="utf-8"))
    return result, record


def test_compile_partial_exits_2_and_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, record = _invoke_compile(
        tmp_path, monkeypatch, _compile_stats(StageOutcome.PARTIAL, "1 source was set aside")
    )

    assert result.exit_code == 2
    assert "1 source was set aside" in result.output
    assert record["compile"]["outcome"] == "partial"
    assert record["compile"]["reason"] == "1 source was set aside"
    assert record["compile"]["started_at"]
    assert record["compile"]["finished_at"]


def test_ingest_outcome_phrases() -> None:
    assert cli._ingest_outcome(2, 0) == (StageOutcome.OK, "")
    assert cli._ingest_outcome(3, 1) == (StageOutcome.PARTIAL, "1 file failed to ingest")
    assert cli._ingest_outcome(1, 2) == (StageOutcome.PARTIAL, "2 files failed to ingest")
    assert cli._ingest_outcome(0, 2) == (StageOutcome.FAILED, "2 files failed to ingest")


def test_ingest_document_does_not_require_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(data_dir=tmp_path / "vault")
    document = tmp_path / "notes.md"
    document.write_text("# Notes\n\nA document.\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_preflight_check", lambda: None)
    monkeypatch.setattr("second_brain.state.emit_state", lambda _config: None)
    monkeypatch.setattr(
        "second_brain.ollama.check_ollama",
        lambda _config: OllamaStatus(
            host="http://localhost:11434",
            reachable=False,
            required_models=("gemma4:12b",),
            missing_models=("gemma4:12b",),
        ),
    )

    result = CliRunner().invoke(cli.main, ["ingest", "--path", str(document)])
    record = json.loads((config.data_dir / RUN_RECORD_FILENAME).read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert record["ingest"]["outcome"] == "ok"
    assert record["ingest"]["counts"] == {"completed": 1, "failed": 0}


@pytest.mark.parametrize(
    ("unsorted_count", "reason"),
    [
        (
            2,
            "Couldn't sort 2 chats because Ollama isn't responding. Start Ollama and try again.",
        ),
        (
            1,
            "Couldn't sort 1 chat because Ollama isn't responding. Start Ollama and try again.",
        ),
    ],
)
def test_ingest_triage_interrupted_exits_1_and_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsorted_count: int,
    reason: str,
) -> None:
    config = Config(data_dir=tmp_path / "vault")
    emitted: list[Config] = []
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_preflight_check", lambda: None)
    monkeypatch.setattr("second_brain.state.emit_state", emitted.append)
    monkeypatch.setattr("second_brain.ingestion.watcher._batch_scan", lambda *_args, **_kwargs: 0)

    def _triage(*_args: object, **_kwargs: object) -> dict[str, int]:
        raise TriageInterruptedError(unsorted_count=unsorted_count)

    monkeypatch.setattr("second_brain.triage.pipeline.triage_pending", _triage)

    result = CliRunner().invoke(cli.main, ["ingest"])
    record = json.loads((config.data_dir / RUN_RECORD_FILENAME).read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert reason in result.output
    assert record["ingest"]["outcome"] == "failed"
    assert record["ingest"]["reason"] == reason
    assert record["ingest"]["counts"] == {"completed": 0, "failed": 0}
    assert emitted == [config]


def test_compile_ollama_unavailable_exits_1_and_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(data_dir=tmp_path / "vault")
    reason = "Build stopped because Ollama isn't responding. Start Ollama and build again."
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_require_ollama", lambda _config: None)
    monkeypatch.setattr("second_brain.state.emit_state", lambda _config: None)

    def _compile(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise OllamaUnavailableError("down")

    monkeypatch.setattr("second_brain.compilation.compiler.run_compilation", _compile)

    result = CliRunner().invoke(cli.main, ["compile"])
    record = json.loads((config.data_dir / RUN_RECORD_FILENAME).read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert reason in result.output
    assert record["compile"]["outcome"] == "failed"
    assert record["compile"]["reason"] == reason
    assert "crashed" not in record["compile"]["reason"]


def test_preview_clusters_ollama_unavailable_leaves_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(data_dir=tmp_path / "vault")
    config.ensure_directories()
    preview_path = config.data_dir / CLUSTERS_FILENAME
    original = '{"source_count": 1}\n'
    preview_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)

    def _preview(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise OllamaUnavailableError("down")

    monkeypatch.setattr("second_brain.clustering.preview.write_preview", _preview)

    result = CliRunner().invoke(cli.main, ["preview-clusters"])

    assert result.exit_code == 1
    assert (
        "Couldn't group chats because Ollama isn't responding. Your grouping wasn't changed."
        in result.output
    )
    assert preview_path.read_text(encoding="utf-8") == original
    status = json.loads((config.data_dir / STATUS_FILENAME).read_text(encoding="utf-8"))
    assert status["running"] is False


def test_compile_failed_exits_1_and_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, record = _invoke_compile(
        tmp_path, monkeypatch, _compile_stats(StageOutcome.FAILED, "Out of API credits")
    )

    assert result.exit_code == 1
    assert "Build stopped: Out of API credits" in result.output
    assert record["compile"]["outcome"] == "failed"
    assert record["compile"]["reason"] == "Out of API credits"
