"""Startup-owned scan scheduling and database-connection cleanup."""

import threading
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from second_brain.django_config import file_watching


@pytest.fixture
def watchers(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    sources, wiki = Mock(), Mock()
    preferences = Mock(side_effect=["first preferences", "updated preferences"])
    connections = Mock()
    monkeypatch.setattr(file_watching, "SourceWatcher", lambda: sources)
    monkeypatch.setattr(file_watching, "WikiIndexWatcher", lambda: wiki)
    monkeypatch.setattr(file_watching, "get_pipeline_config", preferences)
    monkeypatch.setattr(file_watching, "close_old_connections", Mock())
    monkeypatch.setattr(file_watching, "connections", connections)
    return SimpleNamespace(
        sources=sources, wiki=wiki, preferences=preferences, connections=connections
    )


def test_scan_failure_does_not_skip_other_watcher_or_later_pass(
    watchers: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stop = Mock()
    stop.wait.side_effect = [False, False, True]
    stop.is_set.return_value = False
    watchers.sources.scan.side_effect = [OSError("source volume unavailable"), None]
    thread = Mock(ident=1)

    def create_thread(*, target: Callable[[], None], daemon: bool) -> Mock:
        thread.start.side_effect = target
        return thread

    monkeypatch.setattr(
        file_watching,
        "threading",
        SimpleNamespace(
            Event=lambda: stop,
            Thread=create_thread,
        ),
    )
    with file_watching.watch_vault_changes():
        pass

    assert watchers.sources.scan.call_args_list == [
        call("first preferences"),
        call("updated preferences"),
    ]
    assert watchers.wiki.scan.call_args_list == watchers.sources.scan.call_args_list
    assert "source volume unavailable" in caplog.text
    stop.set.assert_called_once()
    thread.join.assert_called_once_with(timeout=file_watching.SHUTDOWN_TIMEOUT_SECONDS)
    watchers.connections.close_all.assert_called_once()


def test_context_failure_stops_real_scan_thread_and_closes_connections(
    watchers: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    scanned = threading.Event()
    closed = threading.Event()
    watchers.preferences.side_effect = None
    watchers.wiki.scan.side_effect = lambda config: scanned.set()
    watchers.connections.close_all.side_effect = closed.set
    monkeypatch.setattr(file_watching, "SCAN_INTERVAL_SECONDS", 0.01)

    with pytest.raises(ValueError, match="server failed"):
        with file_watching.watch_vault_changes():
            assert scanned.wait(timeout=2)
            raise ValueError("server failed")

    assert closed.is_set()
