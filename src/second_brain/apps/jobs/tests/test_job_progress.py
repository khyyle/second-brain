"""Tests for the run heartbeat the menu bar app polls."""

from __future__ import annotations

from second_brain.apps.jobs.services.job_progress import (
    finish_progress,
    now_iso,
    touch_heartbeat,
    update_progress,
)


def test_a_build_heartbeat_names_the_sources_being_compiled(read_job_progress) -> None:
    update_progress(
        phase="compile",
        current=1,
        total=3,
        started_at=now_iso(),
        current_sources=("chatgpt/a.md", "chatgpt/b.md"),
    )

    assert read_job_progress()["current_sources"] == ["chatgpt/a.md", "chatgpt/b.md"]


def test_other_stages_name_no_sources(read_job_progress) -> None:
    update_progress(phase="ingest", current=0, total=2, started_at=now_iso())

    assert read_job_progress()["current_sources"] == []


def test_a_keepalive_keeps_the_sources(read_job_progress) -> None:
    update_progress(
        phase="compile",
        current=0,
        total=1,
        started_at=now_iso(),
        current_sources=("documents/notes.md",),
    )

    touch_heartbeat()

    assert read_job_progress()["current_sources"] == ["documents/notes.md"]


def test_an_idle_heartbeat_names_no_sources(read_job_progress) -> None:
    update_progress(
        phase="compile",
        current=0,
        total=1,
        started_at=now_iso(),
        current_sources=("documents/notes.md",),
    )

    finish_progress()

    heartbeat = read_job_progress()
    assert heartbeat["running"] is False
    assert heartbeat["current_sources"] == ["documents/notes.md"]
