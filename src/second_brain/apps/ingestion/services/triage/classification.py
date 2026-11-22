"""Classify source text as worthwhile, review, or skip using a local model."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from second_brain.apps.inference.services.worker_client import generate_json
from second_brain.apps.ingestion.services.triage.prompts import get_prompt
from second_brain.pipeline_config import TriageConfig

logger = logging.getLogger(__name__)

TRIAGE_TRUNCATE_CHARS = 8000
TRIAGE_MAX_ATTEMPTS = 2


class TriageDecision(StrEnum):
    """Where a source goes after triage."""

    WORTHWHILE = "worthwhile"  # -> Claude compilation
    REVIEW = "review"  # -> inbox for a manual pass
    SKIP = "skip"  # -> recorded, never compiled


class TriageResult(BaseModel):
    """A source verdict with model confidence (0–1), suggested labels, and a reason."""

    model_config = ConfigDict(frozen=True)

    decision: TriageDecision
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    concept_hints: list[str] = Field(default_factory=list)
    content_type_hint: str = "insight"
    domain_hints: list[str] = Field(default_factory=list)
    reason: str = ""


def heuristic_skip(content: str, min_word_count: int) -> bool:
    """
    Return ``True`` when content is obviously too thin to be worthwhile.

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


def triage_content(content: str, config: TriageConfig) -> TriageResult:
    """Classify content via the configured triage profile.

    Retries malformed model output once, then sends the source to review.
    Inference failures propagate without assigning a verdict. Low-confidence
    worthwhile verdicts are demoted to review.

    Parameters
    ----------
    content: str
        Raw source text (truncated before sending to the model).
    config: TriageConfig
        Prompt profile and confidence threshold.

    Returns
    -------
    TriageResult
        The classification.

    Raises
    ------
    InferenceUnavailableError
        When the local model cannot serve the request.
    """
    prompt = f"{get_prompt(config.profile)}\n\nDocument:\n{content[:TRIAGE_TRUNCATE_CHARS]}"

    for attempt in range(TRIAGE_MAX_ATTEMPTS):
        response = generate_json(prompt, max_tokens=None)
        try:
            result = TriageResult.model_validate_json(response)
        except ValidationError:
            logger.debug("Triage produced invalid output (attempt %d)", attempt + 1)
            continue
        if (
            result.decision == TriageDecision.WORTHWHILE
            and result.confidence < config.worthwhile_threshold
        ):
            result = result.model_copy(update={"decision": TriageDecision.REVIEW})
        return result

    logger.warning("Triage output invalid after retry; sending to review")
    return TriageResult(
        decision=TriageDecision.REVIEW,
        confidence=0.0,
        reason="triage-invalid-output",
    )


def triage_file(path: Path, config: TriageConfig) -> TriageResult:
    """
    Triage a single raw source file.

    Skips files below the word-count threshold without calling inference.

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

    Raises
    ------
    OSError
        The source cannot be read.
    InferenceUnavailableError
        The local model cannot serve the request.
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    if heuristic_skip(content, config.min_word_count):
        return TriageResult(
            decision=TriageDecision.SKIP,
            confidence=1.0,
            reason=f"below {config.min_word_count}-word heuristic floor",
        )
    return triage_content(content, config)
