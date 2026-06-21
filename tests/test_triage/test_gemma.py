"""Tests for the Gemma/Ollama triage layer (heuristic + mocked model)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from second_brain.config import TriageConfig
from second_brain.triage import gemma
from second_brain.triage.gemma import (
    TriageDecision,
    heuristic_skip,
    triage_content,
    triage_file,
)


class FakeResponse:
    """Minimal stand-in for an httpx.Response wrapping an Ollama reply."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def config() -> TriageConfig:
    return TriageConfig(min_word_count=10, worthwhile_threshold=0.6)


def _ollama_reply(decision: str, confidence: float) -> dict:
    """Build an Ollama /api/generate reply whose 'response' is triage JSON."""
    inner = json.dumps(
        {
            "decision": decision,
            "confidence": confidence,
            "concept_hints": ["x"],
            "content_type_hint": "concept",
            "domain_hints": ["math"],
            "reason": "test",
        }
    )
    return {"response": inner}


def test_heuristic_skips_thin_content() -> None:
    assert heuristic_skip("one two three", min_word_count=10) is True
    assert heuristic_skip("word " * 50, min_word_count=10) is False


def test_triage_content_worthwhile(
    config: TriageConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gemma.httpx, "post", lambda *a, **k: FakeResponse(_ollama_reply("worthwhile", 0.9))
    )
    result = triage_content("a substantive document " * 50, config)
    assert result.decision == TriageDecision.WORTHWHILE
    assert result.confidence == pytest.approx(0.9)
    assert "math" in result.domain_hints


def test_low_confidence_worthwhile_demoted_to_review(
    config: TriageConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gemma.httpx, "post", lambda *a, **k: FakeResponse(_ollama_reply("worthwhile", 0.3))
    )
    result = triage_content("content " * 50, config)
    assert result.decision == TriageDecision.REVIEW


def test_skip_decision_passthrough(
    config: TriageConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gemma.httpx, "post", lambda *a, **k: FakeResponse(_ollama_reply("skip", 0.95))
    )
    result = triage_content("content " * 50, config)
    assert result.decision == TriageDecision.SKIP


def test_fails_open_when_ollama_unavailable(
    config: TriageConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(gemma.httpx, "post", _raise)
    result = triage_content("content " * 50, config)
    assert result.decision == TriageDecision.WORTHWHILE
    assert result.reason == "triage-unavailable"


def test_triage_file_heuristic_skip_avoids_model(
    config: TriageConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def _should_not_run(*_a, **_k):
        nonlocal called
        called = True
        raise AssertionError("model must not be called for thin content")

    monkeypatch.setattr(gemma.httpx, "post", _should_not_run)
    f = tmp_path / "thin.md"
    f.write_text("too short")
    result = triage_file(f, config)
    assert result.decision == TriageDecision.SKIP
    assert called is False


def test_profile_selects_matching_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured profile's prompt text is what gets sent to Ollama."""
    captured: dict[str, str] = {}

    def _capture(url, json, timeout):  # noqa: A002
        captured["prompt"] = json["prompt"]
        return FakeResponse(_ollama_reply("worthwhile", 0.9))

    monkeypatch.setattr(gemma.httpx, "post", _capture)
    triage_content("content " * 50, TriageConfig(profile="technical"))
    assert "STEM researcher" in captured["prompt"]
    assert "Document:" in captured["prompt"]


def test_retries_on_invalid_then_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed model output is retried, then fails open as worthwhile."""
    calls = {"n": 0}

    def _invalid(*_a, **_k):
        calls["n"] += 1
        return FakeResponse({"response": json.dumps({"decision": "garbage"})})

    monkeypatch.setattr(gemma.httpx, "post", _invalid)
    result = triage_content("content " * 50, TriageConfig())
    assert calls["n"] == gemma.TRIAGE_MAX_ATTEMPTS
    assert result.decision == TriageDecision.WORTHWHILE
    assert result.reason == "triage-invalid-output"
