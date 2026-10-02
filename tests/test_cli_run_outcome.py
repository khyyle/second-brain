"""CLI exit codes and the run record for compile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from second_brain import cli
from second_brain.config import Config
from second_brain.run_record import RUN_RECORD_FILENAME, StageOutcome


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
        "counts": {"completed": 1, "failed": 0, "deferred": 0, "left": 0},
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
