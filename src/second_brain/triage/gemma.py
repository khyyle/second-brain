"""Local-model triage via Ollama (Gemma).

Classifies each raw sources as worthwhile / review / skip before it
reaches the expensive Claude compilation stage. Most ChatGPT
conversations and many note pages are not worth synthesizing; running a
free local model to filter them keeps Claude reserved for genuine
knowledge.

Two layers:
1. A zero-cost heuristic pre-filter (word count) that needs no model.
2. A Gemma classification over Ollama, used only on content that clears
   the heuristic.

Both degrade gracefully: if Ollama is unreachable the content passes
through as ``worthwhile`` (fail-open, so nothing is silently dropped),
while the heuristic still filters obviously-thin content for free.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import httpx

from second_brain.config import TriageConfig
from second_brain.triage.prompts import get_prompt

logger = logging.getLogger(__name__)

TRIAGE_TIMEOUT_SECONDS = 60
# Truncate long sources before sending to the small model — the first
# several thousand chars are plenty to judge worthwhileness.
TRIAGE_TRUNCATE_CHARS = 8000
# Small local models occasionally emit malformed JSON; one retry clears
# most of those without meaningful cost.
TRIAGE_MAX_ATTEMPTS = 2


class TriageDecision(StrEnum):
    """Where a source goes after triage."""
    WORTHWHILE = "worthwhile"  # -> Claude compilation
    REVIEW = "review"          # -> inbox for a manual pass
    SKIP = "skip"              # -> recorded, never compiled


@dataclass(frozen=True)
class TriageResult:
    """Outcome of triaging a single source."""
    decision: TriageDecision
    confidence: float
    concept_hints: list[str] = field(default_factory=list)
    content_type_hint: str = "insight"
    domain_hints: list[str] = field(default_factory=list)
    reason: str = ""


def heuristic_skip(content: str, min_word_count: int) -> bool:
    """
    Return ``True`` when content is obviously too thin to be worthwhile.

    A free pre-filter that needs no model, so it runs even when Ollama
    is unavailable.

    Parameters
    ----------
    content: str
        Raw source text.
    min_word_count: int
        Minimum words below which the source is auto-skipped.

    Returns
    -------
    bool
        ``True`` if the source should be skipped without a model call.
    """
    return len(content.split()) < min_word_count


def _ollama_generate(prompt: str, config: TriageConfig) -> dict | None:
    """Call Ollama and return the parsed JSON object, or None on failure.

    Parameters
    ----------
    prompt: str
        Full prompt (profile instructions + document).
    config: TriageConfig
        Model and host settings.

    Returns
    -------
    dict | None
        The model's parsed JSON object, or ``None`` if Ollama is
        unreachable or its response is not parseable JSON.
    """
    payload = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    try:
        response = httpx.post(
            f"{config.ollama_host}/api/generate",
            json=payload,
            timeout=TRIAGE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return json.loads(response.json()["response"])
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.debug("Ollama generate failed: %s", exc)
        return None


def _interpret(response_json: dict, config: TriageConfig) -> TriageResult | None:
    """Validate a model response dict into a TriageResult, or None if invalid.

    Parameters
    ----------
    response_json: dict
        Parsed JSON object from the model.
    config: TriageConfig
        Threshold settings (for confidence-based demotion).

    Returns
    -------
    TriageResult | None
        The result, or ``None`` if the decision field is missing/invalid.
    """
    try:
        decision = TriageDecision(response_json["decision"])
    except (KeyError, ValueError):
        return None

    confidence = float(response_json.get("confidence", 0.0))
    # A low-confidence "worthwhile" is demoted to review rather than
    # spending Claude tokens on a guess.
    if decision == TriageDecision.WORTHWHILE and confidence < config.worthwhile_threshold:
        decision = TriageDecision.REVIEW

    return TriageResult(
        decision=decision,
        confidence=confidence,
        concept_hints=list(response_json.get("concept_hints", [])),
        content_type_hint=str(response_json.get("content_type_hint", "insight")),
        domain_hints=list(response_json.get("domain_hints", [])),
        reason=str(response_json.get("reason", "")),
    )


def triage_content(content: str, config: TriageConfig) -> TriageResult:
    """Classify content via the configured triage profile, failing open.

    Uses the prompt profile named by ``config.profile`` and retries once
    on malformed model output (small local models occasionally ignore the
    JSON format request). On a network error or persistent invalid output
    it returns a ``WORTHWHILE`` result so nothing is silently dropped.

    Parameters
    ----------
    content: str
        Raw source text (truncated before sending to the model).
    config: TriageConfig
        Triage model, host, profile, and threshold settings.

    Returns
    -------
    TriageResult
        The classification.
    """
    prompt = f"{get_prompt(config.profile)}\n\nDocument:\n{content[:TRIAGE_TRUNCATE_CHARS]}"

    for attempt in range(TRIAGE_MAX_ATTEMPTS):
        response_json = _ollama_generate(prompt, config)
        if response_json is None:
            logger.warning("Triage unavailable — passing through as worthwhile")
            return TriageResult(
                decision=TriageDecision.WORTHWHILE,
                confidence=0.0,
                reason="triage-unavailable",
            )
        result = _interpret(response_json, config)
        if result is not None:
            return result
        logger.debug("Triage produced invalid output (attempt %d)", attempt + 1)

    logger.warning("Triage output invalid after retry — passing through as worthwhile")
    return TriageResult(
        decision=TriageDecision.WORTHWHILE,
        confidence=0.0,
        reason="triage-invalid-output",
    )


def triage_file(path: Path, config: TriageConfig) -> TriageResult:
    """
    Triage a single raw source file.

    Applies the heuristic pre-filter first, then Gemma classification.

    Parameters
    ----------
    path: Path
        Raw markdown source to triage.
    config: TriageConfig
        Triage settings.

    Returns
    -------
    TriageResult
        The classification for this file.
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    if heuristic_skip(content, config.min_word_count):
        return TriageResult(
            decision=TriageDecision.SKIP,
            confidence=1.0,
            reason=f"below {config.min_word_count}-word heuristic floor",
        )
    return triage_content(content, config)
