"""Tests for the per-stage run record."""

from __future__ import annotations

import json
from pathlib import Path

from second_brain.run_record import (
    RUN_RECORD_FILENAME,
    StageOutcome,
    exit_code_for,
    record_run,
)


def _read(data_dir: Path) -> dict[str, object]:
    return json.loads((data_dir / RUN_RECORD_FILENAME).read_text(encoding="utf-8"))


def test_record_run_writes_one_stage(tmp_path: Path) -> None:
    record_run(
        tmp_path,
        "ingest",
        StageOutcome.OK,
        "",
        {"completed": 1, "failed": 0},
        "2026-01-01T00:00:00+00:00",
    )

    entry = _read(tmp_path)["ingest"]
    assert entry["outcome"] == "ok"
    assert entry["reason"] == ""
    assert entry["counts"] == {"completed": 1, "failed": 0}
    assert entry["started_at"] == "2026-01-01T00:00:00+00:00"
    assert "T" in entry["finished_at"]


def test_recording_one_stage_keeps_the_other(tmp_path: Path) -> None:
    record_run(
        tmp_path,
        "compile",
        StageOutcome.FAILED,
        "Out of API credits",
        {"completed": 0, "failed": 0, "deferred": 0, "left": 2},
        "2026-01-01T00:00:00+00:00",
    )
    record_run(
        tmp_path,
        "ingest",
        StageOutcome.OK,
        "",
        {"completed": 1, "failed": 0},
        "2026-01-01T00:01:00+00:00",
    )

    record = _read(tmp_path)
    assert record["compile"]["outcome"] == "failed"
    assert record["compile"]["reason"] == "Out of API credits"
    assert record["ingest"]["outcome"] == "ok"


def test_invalid_existing_file_is_replaced(tmp_path: Path) -> None:
    (tmp_path / RUN_RECORD_FILENAME).write_text("not json", encoding="utf-8")

    record_run(
        tmp_path,
        "compile",
        StageOutcome.CAPPED,
        "Reached the spend cap with 1 source left",
        {"completed": 1, "failed": 0, "deferred": 0, "left": 1},
        "2026-01-01T00:00:00+00:00",
    )

    record = _read(tmp_path)
    assert set(record) == {"compile"}
    assert record["compile"]["outcome"] == "capped"


def test_exit_code_for_covers_every_outcome() -> None:
    assert exit_code_for(StageOutcome.OK) == 0
    assert exit_code_for(StageOutcome.STOPPED) == 0
    assert exit_code_for(StageOutcome.FAILED) == 1
    assert exit_code_for(StageOutcome.PARTIAL) == 2
    assert exit_code_for(StageOutcome.CAPPED) == 2
