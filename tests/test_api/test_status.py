"""Tests for the run heartbeat the menu bar app polls."""

from __future__ import annotations

import json
from pathlib import Path

from second_brain.api.status import (
    STATUS_FILENAME,
    clear_status,
    now_iso,
    touch_status,
    write_status,
)


def _read_heartbeat(data_dir: Path) -> dict:
    """The raw JSON, since that is what the app decodes."""
    return json.loads((data_dir / STATUS_FILENAME).read_text(encoding="utf-8"))


def test_a_build_heartbeat_names_the_sources_being_compiled(tmp_path: Path) -> None:
    write_status(
        tmp_path,
        phase="compile",
        current=1,
        total=3,
        started_at=now_iso(),
        current_sources=("chatgpt/a.md", "chatgpt/b.md"),
    )

    assert _read_heartbeat(tmp_path)["current_sources"] == ["chatgpt/a.md", "chatgpt/b.md"]


def test_other_stages_name_no_sources(tmp_path: Path) -> None:
    write_status(tmp_path, phase="ingest", current=0, total=2, started_at=now_iso())

    assert _read_heartbeat(tmp_path)["current_sources"] == []


def test_a_keepalive_keeps_the_sources(tmp_path: Path) -> None:
    write_status(
        tmp_path,
        phase="compile",
        current=0,
        total=1,
        started_at=now_iso(),
        current_sources=("documents/notes.md",),
    )

    touch_status(tmp_path)

    assert _read_heartbeat(tmp_path)["current_sources"] == ["documents/notes.md"]


def test_an_idle_heartbeat_names_no_sources(tmp_path: Path) -> None:
    write_status(
        tmp_path,
        phase="compile",
        current=0,
        total=1,
        started_at=now_iso(),
        current_sources=("documents/notes.md",),
    )

    clear_status(tmp_path)

    heartbeat = _read_heartbeat(tmp_path)
    assert heartbeat["running"] is False
    assert heartbeat["current_sources"] == []
