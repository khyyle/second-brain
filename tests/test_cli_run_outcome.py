"""CLI exit codes and the run record for compile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from second_brain import cli
from second_brain.clustering.preview import CLUSTERS_FILENAME
from second_brain.config import Config, ConfigError
from second_brain.ingestion.pdf_handler import ParserNotAvailableError
from second_brain.ollama.client import OllamaStatus, OllamaUnavailableError
from second_brain.run_record import CRASH_REASON, RUN_RECORD_FILENAME, StageOutcome
from second_brain.status import STATUS_FILENAME
from second_brain.triage.pipeline import TriageInterruptedError
from second_brain.wiki.repo import WikiRepoError


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
        tmp_path, monkeypatch, _compile_stats(StageOutcome.PARTIAL, "1 source was set aside.")
    )

    assert result.exit_code == 2
    assert "1 source was set aside." in result.output
    assert record["compile"]["outcome"] == "partial"
    assert record["compile"]["reason"] == "1 source was set aside."
    assert record["compile"]["started_at"]
    assert record["compile"]["finished_at"]


def test_ingest_outcome_phrases() -> None:
    assert cli._ingest_outcome(2, 0) == (StageOutcome.OK, "")
    assert cli._ingest_outcome(3, 1) == (StageOutcome.PARTIAL, "1 file failed.")
    assert cli._ingest_outcome(1, 2) == (StageOutcome.PARTIAL, "2 files failed.")
    assert cli._ingest_outcome(0, 2) == (StageOutcome.FAILED, "2 files failed.")


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
        "second_brain.ollama.client.check_ollama",
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
    ("untriaged_count", "reason"),
    [
        (
            2,
            "Couldn't triage 2 chats because Ollama isn't responding. Start Ollama and try again.",
        ),
        (
            1,
            "Couldn't triage 1 chat because Ollama isn't responding. Start Ollama and try again.",
        ),
    ],
)
def test_ingest_triage_interrupted_exits_1_and_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    untriaged_count: int,
    reason: str,
) -> None:
    config = Config(data_dir=tmp_path / "vault")
    emitted: list[Config] = []
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_preflight_check", lambda: None)
    monkeypatch.setattr("second_brain.state.emit_state", emitted.append)
    monkeypatch.setattr("second_brain.ingestion.watcher.batch_scan", lambda *_args, **_kwargs: 0)

    def _triage(*_args: object, **_kwargs: object) -> dict[str, int]:
        raise TriageInterruptedError(untriaged_count=untriaged_count)

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
    reason = "Ollama stopped responding. Start Ollama and build again."
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
        tmp_path,
        monkeypatch,
        _compile_stats(StageOutcome.FAILED, "Your API account is out of credits."),
    )

    assert result.exit_code == 1
    assert "Build stopped: Your API account is out of credits." in result.output
    assert record["compile"]["outcome"] == "failed"
    assert record["compile"]["reason"] == "Your API account is out of credits."


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            WikiRepoError("git is not installed or not on PATH"),
            "git is not installed or not on PATH",
        ),
        (KeyError("bug"), CRASH_REASON),
    ],
)
def test_compile_records_expected_errors_by_reason_and_bugs_as_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, reason: str
) -> None:
    config = Config(data_dir=tmp_path / "vault")
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_require_ollama", lambda _config: None)

    def _compile(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise error

    monkeypatch.setattr("second_brain.compilation.compiler.run_compilation", _compile)

    result = CliRunner().invoke(cli.main, ["compile"])
    record = json.loads((config.data_dir / RUN_RECORD_FILENAME).read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert record["compile"]["outcome"] == "failed"
    assert record["compile"]["reason"] == reason


def test_ingest_records_an_expected_error_by_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(data_dir=tmp_path / "vault")
    reason = "Parser 'docling' requires package docling"
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_preflight_check", lambda: None)

    def _scan(*_args: object, **_kwargs: object) -> int:
        raise ParserNotAvailableError(reason)

    monkeypatch.setattr("second_brain.ingestion.watcher.batch_scan", _scan)

    result = CliRunner().invoke(cli.main, ["ingest"])
    record = json.loads((config.data_dir / RUN_RECORD_FILENAME).read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert reason in result.output
    assert record["ingest"]["outcome"] == "failed"
    assert record["ingest"]["reason"] == reason


def test_a_broken_config_is_recorded_for_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reason = "Invalid setting in config.yaml: triage.profile must be one of balanced, not 'x'."
    monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", tmp_path)

    def _broken(_path: object) -> Config:
        raise ConfigError(reason)

    monkeypatch.setattr(cli, "_load_config", _broken)

    result = CliRunner().invoke(cli.main, ["compile"])
    record = json.loads((tmp_path / RUN_RECORD_FILENAME).read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert reason in result.output
    assert record["compile"]["outcome"] == "failed"
    assert record["compile"]["reason"] == reason


def test_any_command_shows_an_expected_failure_as_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(data_dir=tmp_path / "vault")
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)

    result = CliRunner().invoke(cli.main, ["domain", "rename", "math", " "])

    assert result.exit_code == 1
    assert "A domain name can't be empty." in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
