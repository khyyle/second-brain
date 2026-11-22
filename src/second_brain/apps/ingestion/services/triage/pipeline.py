"""Record triage decisions and route sources to staging, review, or holding."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from second_brain.apps.ingestion.services.triage.classification import TriageDecision, triage_file
from second_brain.apps.ingestion.services.triage.skipped import move_skips_to_holding
from second_brain.apps.ingestion.services.triage.triage_decisions import TriageStore
from second_brain.apps.jobs.services.job_progress import (
    finish_progress,
    now_iso,
    stop_requested,
    update_progress,
)
from second_brain.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)


def _source_of(rel_path: str) -> str:
    """Return the source name from a raw path's first component (e.g. 'chatgpt')."""
    parts = Path(rel_path).parts
    return parts[0] if parts else ""


def find_untriaged_sources(config: PipelineConfig, triage_store: TriageStore) -> list[str]:
    """Return undecided raw .md paths in triage-scoped lanes.

    Only lanes listed in ``config.triage.sources`` are triaged. Sources in
    other lanes (e.g. deliberately dropped documents) get no decision and
    pass straight through to compilation.

    Parameters
    ----------
    config: PipelineConfig
        Application configuration (raw directory and triage scope).
    triage_store: TriageStore
        Store holding existing triage decisions.

    Returns
    -------
    list[str]
        Sorted relative raw paths awaiting a decision.
    """
    raw_dir = config.raw_dir
    if not config.triage.enabled or not raw_dir.exists():
        return []
    decided = set(triage_store.get_decisions())
    pending: list[str] = []
    for md_file in raw_dir.rglob("*.md"):
        relative = md_file.relative_to(raw_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue  # skip the .skipped/ holding folder and other dotfiles
        rel = str(relative)
        if _source_of(rel) not in config.triage.sources:
            continue  # untriaged lanes pass through without a decision
        if rel not in decided:
            pending.append(rel)
    return sorted(pending)


def triage_pending(
    config: PipelineConfig, on_decided: Callable[[], None] | None = None
) -> dict[str, int]:
    """Triage every untriaged source in a scoped lane and record decisions.

    Review-tier sources are also copied into the inbox for a manual pass.
    Skipped sources are moved into the holding folder under ``raw/``.
    Reports progress for the current job. Already-decided files are skipped.
    An inference failure stops the pass and leaves unfinished sources undecided.
    Completed decisions are saved and routed even when the pass stops early.

    Parameters
    ----------
    config: PipelineConfig
        Application configuration (triage settings, paths).
    on_decided: Callable[[], None] | None
        Called after each chat's decision is recorded, so a reader of the
        staged and review sets sees each chat land as it is sorted.

    Returns
    -------
    dict[str, int]
        Counts keyed by ``"worthwhile"``, ``"review"``, ``"skip"``.

    Raises
    ------
    InferenceUnavailableError
        When the local model cannot serve a request.
    """
    if not config.triage.enabled:
        return {"worthwhile": 0, "review": 0, "skip": 0}

    triage_store = TriageStore()
    pending = find_untriaged_sources(config, triage_store)
    counts = {"worthwhile": 0, "review": 0, "skip": 0}
    total = len(pending)
    started = now_iso()
    review_paths: list[str] = []
    try:
        for idx, rel in enumerate(pending):
            if stop_requested():
                break
            update_progress(
                phase="triage",
                current=idx,
                total=total,
                started_at=started,
                current_sources=(rel,),
                current_started_at=now_iso(),
                queued_sources=tuple(pending[idx + 1 :]),
            )
            try:
                result = triage_file(config.raw_dir / rel, config.triage)
            except OSError as exc:
                # A missing or unreadable source must not block the remaining files.
                logger.warning("Skipping triage for %s: %s", rel, exc)
                continue
            triage_store.record(rel, result.decision.value, result.confidence, result.reason)
            counts[result.decision.value] += 1
            if result.decision == TriageDecision.SKIP:
                move_skips_to_holding(config.raw_dir, {rel: result.decision.value})
            if result.decision == TriageDecision.REVIEW:
                review_paths.append(rel)
            if on_decided is not None:
                on_decided()
    finally:
        if pending:
            finish_progress()
        _route_review_to_inbox(config, review_paths)
        moved = move_skips_to_holding(config.raw_dir, triage_store.get_decisions())
        if moved:
            logger.info("Moved %d skipped source(s) into holding folder", moved)

    if pending:
        logger.info(
            "Triage: %d worthwhile, %d review, %d skip (of %d)",
            counts["worthwhile"],
            counts["review"],
            counts["skip"],
            total,
        )

    return counts


def _route_review_to_inbox(config: PipelineConfig, review: list[str]) -> None:
    """Copy review-tier sources into the inbox for a manual pass."""
    if not review:
        return
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    for rel in review:
        src = config.raw_dir / rel
        if not src.exists():
            continue
        try:
            shutil.copy2(src, config.inbox_dir / Path(rel).name)
        except OSError as exc:
            logger.warning("Could not copy %s to inbox: %s", rel, exc)


def worthwhile_sources(config: PipelineConfig, sources: list[str]) -> list[str]:
    """Filter sources to those a paid build should compile.

    Review and skip decisions are excluded. When triage is enabled, a source
    in a triaged lane with no decision yet is also excluded, so an interrupted
    or skipped triage cannot send untriaged chats to a paid build. Sources in
    untriaged lanes, and every undecided source when triage is disabled, pass
    through.

    Parameters
    ----------
    config: PipelineConfig
        Application configuration (triage scope and whether it is on).
    sources: list[str]
        Candidate raw source paths.

    Returns
    -------
    list[str]
        The subset to compile.
    """
    decisions = TriageStore().get_decisions()
    held = {TriageDecision.REVIEW.value, TriageDecision.SKIP.value}
    triaged_lanes = set(config.triage.sources)
    kept: list[str] = []
    for rel in sources:
        decision = decisions.get(rel)
        if decision in held:
            continue
        if config.triage.enabled and decision is None and _source_of(rel) in triaged_lanes:
            continue
        kept.append(rel)
    return kept
