"""Tests for the Ollama dependency probe."""

from __future__ import annotations

import httpx
import pytest

from second_brain import ollama
from second_brain.config import Config


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_unreachable_server(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(ollama.httpx, "get", _raise)
    status = ollama.check_ollama(config)

    assert not status.reachable
    assert not status.healthy
    assert status.missing_models == status.required_models
    assert "not running" in status.message()


def test_healthy_when_all_models_present(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"models": [{"name": "gemma4:12b"}, {"name": "nomic-embed-text:latest"}]}
    monkeypatch.setattr(ollama.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    status = ollama.check_ollama(config)

    assert status.reachable
    assert status.healthy
    assert status.missing_models == ()


def test_reports_missing_model(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # Embedding model absent; only the tagged Gemma is installed.
    payload = {"models": [{"name": "gemma4:12b"}]}
    monkeypatch.setattr(ollama.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    status = ollama.check_ollama(config)

    assert status.reachable
    assert not status.healthy
    assert status.missing_models == ("nomic-embed-text",)
    assert "missing required model" in status.message()


def test_wrong_gemma_tag_is_missing(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # A tagged requirement (gemma4:12b) must match exactly, not just the repo.
    payload = {"models": [{"name": "gemma4:4b"}, {"name": "nomic-embed-text:latest"}]}
    monkeypatch.setattr(ollama.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    status = ollama.check_ollama(config)

    assert status.missing_models == ("gemma4:12b",)
