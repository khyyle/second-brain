"""Triage classification validates model output without hiding inference failures."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.ingestion.services.triage import classification
from second_brain.apps.ingestion.services.triage.classification import (
    TriageDecision,
    heuristic_skip,
    triage_content,
    triage_file,
)
from second_brain.pipeline_config import TriageConfig


@pytest.fixture
def config() -> TriageConfig:
    return TriageConfig(min_word_count=10, worthwhile_threshold=0.6)


@pytest.fixture
def generate(monkeypatch: pytest.MonkeyPatch) -> Mock:
    generate = Mock(return_value=_reply("worthwhile", 0.9))
    monkeypatch.setattr(classification, "generate_json", generate)
    return generate


def _reply(decision: str, confidence: float) -> str:
    return json.dumps(
        {
            "decision": decision,
            "confidence": confidence,
            "concept_hints": ["x"],
            "content_type_hint": "concept",
            "domain_hints": ["math"],
            "reason": "test",
        }
    )


def test_heuristic_skips_thin_content() -> None:
    assert heuristic_skip("one two three", min_word_count=10) is True
    assert heuristic_skip("word " * 50, min_word_count=10) is False


def test_triage_content_worthwhile(config: TriageConfig, generate: Mock) -> None:
    result = triage_content("a substantive document " * 50, config)
    assert result.decision == TriageDecision.WORTHWHILE
    assert result.confidence == pytest.approx(0.9)
    assert result.concept_hints == ["x"]
    assert result.content_type_hint == "concept"
    assert result.domain_hints == ["math"]
    assert result.reason == "test"
    generate.assert_called_once()


@pytest.mark.parametrize(
    "decision,confidence,expected",
    [("worthwhile", 0.3, "review"), ("worthwhile", 0.6, "worthwhile"), ("skip", 0.95, "skip")],
)
def test_confidence_threshold(
    config: TriageConfig, generate: Mock, decision: str, confidence: float, expected: str
) -> None:
    generate.return_value = _reply(decision, confidence)
    result = triage_content("content " * 50, config)
    assert result.decision == expected
    assert result.confidence == confidence


def test_inference_failure_propagates_without_retry(config: TriageConfig, generate: Mock) -> None:
    failure = InferenceUnavailableError("Local inference is still loading.")
    generate.side_effect = failure
    with pytest.raises(InferenceUnavailableError) as raised:
        triage_content("content " * 50, config)
    assert raised.value is failure
    generate.assert_called_once()


def test_triage_file_heuristic_skip_avoids_model(
    config: TriageConfig, tmp_path: Path, generate: Mock
) -> None:
    path = tmp_path / "thin.md"
    path.write_text("too short")
    result = triage_file(path, config)
    assert result.decision == TriageDecision.SKIP
    assert result.reason == "below 10-word heuristic floor"
    generate.assert_not_called()


def test_prompt_includes_selected_profile_and_bounded_document(generate: Mock) -> None:
    content = "a" * 8000 + "excluded ending"
    triage_content(content, TriageConfig(profile="technical"))
    (prompt,), options = generate.call_args
    assert "STEM researcher" in prompt
    assert prompt.endswith("\n\nDocument:\n" + "a" * 8000)
    assert "excluded ending" not in prompt
    assert options == {"max_tokens": None}


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "null",
        "[]",
        "{}",
        '{"decision": "unknown"}',
        '{"decision": "worthwhile", "confidence": "certain"}',
        '{"decision": "worthwhile", "confidence": null}',
        '{"decision": "worthwhile", "confidence": NaN}',
        '{"decision": "worthwhile", "confidence": 1.1}',
        '{"decision": "worthwhile", "confidence": -0.1}',
        '{"decision": "worthwhile", "concept_hints": "not a list"}',
        '{"decision": "worthwhile", "reason": {}}',
    ],
)
def test_invalid_output_is_retried_then_sent_to_review(generate: Mock, response: str) -> None:
    generate.return_value = response
    result = triage_content("content " * 50, TriageConfig())
    assert generate.call_count == 2
    assert result.decision == TriageDecision.REVIEW
    assert result.confidence == 0.0
    assert result.reason == "triage-invalid-output"


def test_valid_retry_is_used(generate: Mock) -> None:
    generate.side_effect = ["invalid", _reply("worthwhile", 0.9)]
    result = triage_content("content " * 50, TriageConfig())
    assert result.decision == TriageDecision.WORTHWHILE
    assert generate.call_count == 2


def test_missing_confidence_cannot_approve_a_source(generate: Mock) -> None:
    generate.return_value = '{"decision": "worthwhile"}'
    result = triage_content("content " * 50, TriageConfig())
    assert result.decision == TriageDecision.REVIEW
    assert result.confidence == 0.0
