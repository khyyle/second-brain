"""Concurrent index requests share a single queued attempt."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

import pytest
from django.db import connection, connections

from second_brain.apps.jobs.services.automatic_jobs import schedule_for_file_change
from second_brain.pipeline_config import PipelineConfig


def test_concurrent_index_requests_enqueue_once(
    config: PipelineConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # File-backed SQLite waits for competing writers. A shared in-memory test
    # database uses table locks with different contention behavior.
    path = tmp_path / "queue.db"
    connection.ensure_connection()
    with closing(sqlite3.connect(path)) as target:
        connection.connection.backup(target)
    monkeypatch.setitem(
        connections.databases, "default", {**connection.settings_dict, "NAME": path}
    )
    start = Barrier(4)

    def submit(after_edit: bool) -> str:
        try:
            start.wait(timeout=5)
            return schedule_for_file_change(
                "index", observed_inputs=(("note.md", 1, 20),), queue_after_running=after_edit
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=4) as callers:
        outcomes = list(callers.map(submit, [True, False, True, False]))
    assert sorted(outcomes) == ["covered", "covered", "covered", "queued"]
