"""Authoritative derived state for the menu bar app.

The pipeline and the GUI mutation commands write this file after they change
the pipeline database or the cluster preview; the app reads it instead of recomputing
the staged set, build cost, stale flag, and review and skipped counts itself.
Mirrors the ``.status.json`` heartbeat pattern, for slower-changing state.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from second_brain.clustering.preview import (
    group_cost_usd,
    load_preview,
    model_prices,
    preview_members,
    reconcile_work_units,
)
from second_brain.compilation.compiler import find_new_sources, find_set_aside_sources
from second_brain.compilation.store import DeferredSource
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.ingestion.triage.pipeline import worthwhile_sources
from second_brain.ingestion.triage.skipped import SKIPPED_DIRNAME
from second_brain.ingestion.triage.store import TriageStore
from second_brain.wiki.structure import CONTENT_DIRS

logger = logging.getLogger(__name__)

STATE_FILENAME = ".state.json"


def _staged_with_sizes(raw_dir: Path, rels: list[str]) -> list[dict]:
    """Pair each staged source with its byte size for the cost estimate."""
    sized: list[dict] = []
    for rel in rels:
        path = raw_dir / rel
        sized.append({"rel": rel, "bytes": path.stat().st_size if path.exists() else 0})
    return sized


def _deferred_entries(raw_dir: Path, deferred_sources: dict[str, DeferredSource]) -> list[dict]:
    """Staged-list entries for set-aside sources, each with its reason and whether
    a retry could succeed."""
    return [
        {
            "rel": rel,
            "bytes": (raw_dir / rel).stat().st_size,
            "defer_reason": deferred.reason,
            "retryable": deferred.retryable,
        }
        for rel, deferred in sorted(deferred_sources.items())
    ]


def _build_costs(raw_dir: Path, work_units: list[list[str]]) -> dict[str, float]:
    """Per-model USD estimate for compiling the work units, summed over groups."""
    prices = model_prices()
    totals = dict.fromkeys(prices, 0.0)
    for unit in work_units:
        total_bytes = sum(
            (raw_dir / rel).stat().st_size for rel in unit if (raw_dir / rel).exists()
        )
        for model, (input_price, output_price) in prices.items():
            totals[model] += group_cost_usd(total_bytes, input_price, output_price)
    return {model: round(value, 2) for model, value in totals.items()}


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


def compute_state(config: Config) -> dict:
    """
    Build the authoritative derived state the app renders.

    Parameters
    ----------
    config: Config
        Application configuration (paths and model selection).

    Returns
    -------
    dict
        The staged sources with sizes, the count of built pages, per-model
        build cost, needs-review and skipped counts, whether a reviewed
        grouping has drifted from staging, and why each failed file failed.
    """
    queue = worthwhile_sources(config, find_new_sources(config))
    work_units = reconcile_work_units(config.data_dir, queue) or [[rel] for rel in queue]
    preview = load_preview(config.data_dir)
    stale = preview is not None and preview_members(preview) != set(queue)
    staged = _staged_with_sizes(config.raw_dir, queue) + _deferred_entries(
        config.raw_dir, find_set_aside_sources(config)
    )
    needs_review, skipped = _triage_counts(
        config.raw_dir, TriageStore(config.manifest_db_path).get_decisions()
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "staged": staged,
        "built_count": _built_count(config.wiki_dir),
        "costs": _build_costs(config.raw_dir, work_units),
        "stale": stale,
        "needs_review": needs_review,
        "skipped": skipped,
        "ingest_failures": Manifest(config.manifest_db_path).get_ingest_failures(),
    }


def emit_state(config: Config) -> None:
    """Atomically write the derived state file. Best-effort; never raises."""
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        path = config.data_dir / STATE_FILENAME
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(compute_state(config)), encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        logger.debug("Could not write state file: %s", exc)
