"""Triage orchestration shared by the ingest and compile stages.

Triage runs during ingestion (it uses the local Gemma model, so it is
free and does not need to wait for the paid compile step). Decisions are
recorded in the manifest, so by the time the user reviews or builds, every
ingested source already has a worthwhile / review / skip verdict.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.ollama import OllamaUnavailableError
from second_brain.status import clear_status, now_iso, write_status
from second_brain.triage.gemma import TriageDecision, triage_file
from second_brain.triage.skipped import move_skips_to_holding

logger = logging.getLogger(__name__)


class TriageInterruptedError(OllamaUnavailableError):
    """Ollama stopped answering partway through sorting chats."""

    def __init__(self, unsorted_count: int) -> None:
        super().__init__(f"Ollama stopped answering; {unsorted_count} chats left unsorted")
        self.unsorted_count = unsorted_count


def _source_of(rel_path: str) -> str:
    """Return the source name from a raw path's first component (e.g. 'chatgpt')."""
    parts = Path(rel_path).parts
    return parts[0] if parts else ""


def _find_untriaged(config: Config, manifest: Manifest) -> list[str]:
    """Return undecided raw .md paths in triage-scoped lanes.

    Only lanes listed in ``config.triage.sources`` are triaged. Sources in
    other lanes (e.g. deliberately dropped documents) get no decision and
    pass straight through to compilation.

    Parameters
    ----------
    config: Config
        Application configuration (raw directory and triage scope).
    manifest: Manifest
        Manifest holding existing triage decisions.

    Returns
    -------
    list[str]
        Sorted relative raw paths awaiting a decision.
    """
    raw_dir = config.raw_dir
    if not raw_dir.exists():
        return []
    decided = set(manifest.get_triage_decisions().keys())
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


def triage_pending(config: Config, manifest: Manifest) -> dict[str, int]:
    """Triage every untriaged source in a scoped lane and record decisions.

    Review-tier sources are also copied into the inbox for a manual pass.
    Skipped sources are moved into the holding folder under ``raw/``.
    Writes a status heartbeat so an external reader can show triage
    progress. Safe to call repeatedly: already-decided files are skipped.
    If Ollama stops answering, the pass stops at that chat. Decisions
    already recorded still get their follow-up; the rest stay undecided.

    Parameters
    ----------
    config: Config
        Application configuration (triage settings, paths).
    manifest: Manifest
        Manifest for reading/recording triage decisions.

    Returns
    -------
    dict[str, int]
        Counts keyed by ``"worthwhile"``, ``"review"``, ``"skip"``.

    Raises
    ------
    TriageInterruptedError
        When Ollama stops answering. ``unsorted_count`` is the chats left
        undecided; they are sorted on the next run.
    """
    if not config.triage.enabled:
        return {"worthwhile": 0, "review": 0, "skip": 0}

    pending = _find_untriaged(config, manifest)
    counts = {"worthwhile": 0, "review": 0, "skip": 0}
    interruption: TriageInterruptedError | None = None
    interruption_cause: OllamaUnavailableError | None = None
    if pending:
        total = len(pending)
        started = now_iso()
        review_paths: list[str] = []
        try:
            for idx, rel in enumerate(pending):
                write_status(
                    config.data_dir,
                    phase="triage",
                    current=idx,
                    total=total,
                    started_at=started,
                )
                try:
                    result = triage_file(config.raw_dir / rel, config.triage)
                except OSError as exc:
                    # A source can vanish mid-run (e.g. un-ingested in the app),
                    # so skip it rather than letting one missing file abort the
                    # whole triage pass and stall every later source.
                    logger.warning("Skipping triage for %s: %s", rel, exc)
                    continue
                except OllamaUnavailableError as exc:
                    interruption = TriageInterruptedError(total - idx)
                    interruption_cause = exc
                    logger.warning("%s", interruption)
                    break
                manifest.record_triage(
                    rel, result.decision.value, result.confidence, result.reason
                )
                counts[result.decision.value] += 1
                if result.decision == TriageDecision.REVIEW:
                    review_paths.append(rel)
        finally:
            clear_status(config.data_dir)

        _route_review_to_inbox(config, review_paths)
        logger.info(
            "Triage: %d worthwhile, %d review, %d skip (of %d)",
            counts["worthwhile"],
            counts["review"],
            counts["skip"],
            total,
        )

    moved = move_skips_to_holding(config.raw_dir, manifest.get_triage_decisions())
    if moved:
        logger.info("Moved %d skipped source(s) into holding folder", moved)
    if interruption is not None and interruption_cause is not None:
        raise interruption from interruption_cause
    return counts


def _route_review_to_inbox(config: Config, review: list[str]) -> None:
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


def worthwhile_sources(config: Config, manifest: Manifest, sources: list[str]) -> list[str]:
    """Filter sources to those a paid build should compile.

    Review and skip decisions are excluded. When triage is enabled, a source
    in a triaged lane with no decision yet is also excluded, so an interrupted
    or skipped triage cannot send unsorted chats to a paid build. Sources in
    untriaged lanes, and every undecided source when triage is disabled, pass
    through.

    Parameters
    ----------
    config: Config
        Application configuration (triage scope and whether it is on).
    manifest: Manifest
        Manifest holding triage decisions.
    sources: list[str]
        Candidate raw source paths.

    Returns
    -------
    list[str]
        The subset to compile.
    """
    decisions = manifest.get_triage_decisions()
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
