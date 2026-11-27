"""Derive library state from ORM records and vault files for GET /state."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from second_brain.apps.compilation.models import CompiledSource, DeferredSource
from second_brain.apps.compilation.services.clustering.preview import (
    group_cost_usd,
    load_preview,
    preview_members,
    reconcile_work_units,
)
from second_brain.apps.compilation.services.staging import (
    _raw_sources,
    _record_is_current,
)
from second_brain.apps.ingestion.services.triage.skipped import SKIPPED_DIRNAME
from second_brain.apps.library.models import Source, TriageDecision
from second_brain.apps.library.services.source_files import existing_source, validate_raw_path
from second_brain.apps.preferences.services.agent_provider import model_selection
from second_brain.apps.preferences.services.agent_provider.model_selection import ModelPrice
from second_brain.apps.wiki.services.wiki_structure import CONTENT_DIRS
from second_brain.pipeline_config import PipelineConfig


def _staged_entries(raw_dir: Path, rels: list[str], price: ModelPrice | None) -> list[dict]:
    """Staged-list entries, each with its byte size and the estimated cost of building it alone."""
    entries: list[dict] = []
    for rel in rels:
        path = raw_dir / rel
        size_bytes = path.stat().st_size if path.exists() else 0
        entries.append(
            {"rel": rel, "bytes": size_bytes, "estimated_cost_usd": _estimate(size_bytes, price)}
        )
    return entries


def _deferred_entries(
    raw_dir: Path, deferred_sources: dict[str, DeferredSource], price: ModelPrice | None
) -> list[dict]:
    """Staged-list entries for set-aside sources, each with its reason, whether
    a retry could succeed, and what building it again would cost."""
    entries: list[dict] = []
    for rel, deferred in sorted(deferred_sources.items()):
        size_bytes = (raw_dir / rel).stat().st_size
        entries.append(
            {
                "rel": rel,
                "bytes": size_bytes,
                "estimated_cost_usd": _estimate(size_bytes, price),
                "defer_reason": deferred.reason,
                "retryable": deferred.retryable,
            }
        )
    return entries


def _estimate(size_bytes: int, price: ModelPrice | None, digits: int = 4) -> float | None:
    cost = group_cost_usd(size_bytes, price)
    return None if cost is None else round(cost, digits)


def _build_cost(
    raw_dir: Path, work_units: list[list[str]], price: ModelPrice | None
) -> float | None:
    """Estimated USD cost of compiling the work units, or None when the price is unknown."""
    if price is None:
        return None
    total = 0.0
    for unit in work_units:
        total_bytes = sum(
            (raw_dir / rel).stat().st_size for rel in unit if (raw_dir / rel).exists()
        )
        total += group_cost_usd(total_bytes, price) or 0.0
    return round(total, 2)


def _built_count(wiki_dir: Path) -> int:
    """Count compiled content pages across the wiki's content directories."""
    count = 0
    for content_dir in CONTENT_DIRS:
        directory = wiki_dir / content_dir
        if directory.exists():
            count += sum(1 for _ in directory.glob("*.md"))
    return count


def _triage_counts(raw_dir: Path, decisions: dict[str, str]) -> tuple[int, int]:
    """Count review files in raw/ and skip files in the holding folder."""
    needs_review = 0
    skipped = 0
    for rel, decision in decisions.items():
        if decision == "review" and (raw_dir / rel).exists():
            needs_review += 1
        elif decision == "skip" and (raw_dir / SKIPPED_DIRNAME / rel).exists():
            skipped += 1
    return needs_review, skipped


def _assemble_state(
    config: PipelineConfig,
    *,
    queue: list[str],
    deferred: dict[str, DeferredSource],
    decisions: dict[str, str],
    pending: list[str],
    failures: dict[str, str],
) -> dict:
    """Combine selected sources, grouping, and calculated display fields."""
    work_units = reconcile_work_units(config.data_dir, queue) or [[rel] for rel in queue]
    preview = load_preview(config.data_dir)
    # Sources built or removed since the grouping leave it valid for the rest;
    # only a staged source it never covered makes it stale.
    stale = preview is not None and not set(queue) <= preview_members(preview)
    model = model_selection.model_for_estimates(
        config.compilation.provider, config.compilation.model
    )
    price = model and model.price
    staged = _staged_entries(config.raw_dir, queue, price) + _deferred_entries(
        config.raw_dir, deferred, price
    )
    needs_review, skipped = _triage_counts(config.raw_dir, decisions)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "staged": staged,
        "built_count": _built_count(config.wiki_dir),
        "estimated_cost_usd": _build_cost(config.raw_dir, work_units, price),
        "stale": stale,
        "needs_review": needs_review,
        "skipped": skipped,
        "ingest_failures": failures,
        "pending_triage": pending,
    }


def get_state(config: PipelineConfig) -> dict:
    """Read persisted state in bulk through the ORM, then derive the library summary.

    Parameters
    ----------
    config: PipelineConfig
        Active vault paths and triage rules. Django settings select the database;
        config selects the corresponding raw files, wiki pages, and grouping artifacts.

    Returns
    -------
    dict
        generated_at timestamp; staged source entries with byte sizes, cost estimates
        for the chosen model (None when its price is unknown) and optional deferral
        details; built wiki-page count; the estimated cost of the whole build; grouping
        staleness; review/skipped counts; ingestion failure messages; and undecided
        chat paths. The view serializes this through LibraryState.

    Raises
    ------
    InvalidSourcePathError
        A recorded or discovered raw path is unsafe or follows a symlink.
    SourceNotFoundError
        A discovered source disappears before its location is checked.
    SourceConflictError
        Both active and skipped copies of a discovered source exist.

    Notes
    -----
    Loads source failures, compiled records, deferred records, and triage verdicts
    in bulk. Hashes distinguish changed source versions from already-built/deferred
    versions. Documents bypass chat triage; review/skip verdicts remain excluded.
    Reads files but does not write artifacts, create jobs, or call a model. Database
    and filesystem reads are not a single atomic snapshot; unrelated I/O errors
    propagate, including files disappearing later during hashing or cost calculation.
    """
    compiled = dict(CompiledSource.objects.values_list("raw_path", "content_hash"))
    deferred = DeferredSource.objects.in_bulk()
    decisions = dict(TriageDecision.objects.values_list("raw_path", "decision"))
    failures = dict(
        Source.objects.filter(status="failed", error__isnull=False).values_list(
            "file_path", "error"
        )
    )
    queue: list[str] = []
    pending: list[str] = []
    set_aside: dict[str, DeferredSource] = {}
    for rel in decisions:
        validate_raw_path(rel)
    for rel in _raw_sources(config.raw_dir):
        raw_file = existing_source(config.data_dir, rel)
        decision = decisions.get(rel)
        awaiting_triage = (
            config.triage.enabled
            and rel.split("/", 1)[0] in config.triage.sources
            and decision is None
        )
        if awaiting_triage:
            pending.append(rel)
        if rel in deferred and _record_is_current(deferred[rel].content_hash, raw_file):
            set_aside[rel] = deferred[rel]
        elif (
            not awaiting_triage
            and decision not in {"review", "skip"}
            and (rel not in compiled or not _record_is_current(compiled[rel], raw_file))
        ):
            queue.append(rel)
    return _assemble_state(
        config,
        queue=queue,
        deferred=set_aside,
        decisions=decisions,
        pending=pending,
        failures=failures,
    )
