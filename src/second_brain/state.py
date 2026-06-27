"""Authoritative derived state for the menu bar app.

The pipeline and the GUI mutation commands write this file after they change
the manifest or the cluster preview; the app reads it instead of recomputing
the staged set, build cost, and stale flag itself. Mirrors the ``.status.json``
heartbeat pattern, for slower-changing state.
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
from second_brain.compilation.compiler import find_new_sources
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.triage.pipeline import worthwhile_sources
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


def compute_state(config: Config, manifest: Manifest) -> dict:
    """
    Build the authoritative derived state the app renders.

    Parameters
    ----------
    config: Config
        Application configuration (paths and model selection).
    manifest: Manifest
        Ingestion manifest holding compiled and triage records.

    Returns
    -------
    dict
        The staged sources with sizes, the count of built pages, per-model
        build cost, and whether a reviewed grouping has drifted from staging.
    """
    staged = worthwhile_sources(manifest, find_new_sources(config, manifest))
    work_units = reconcile_work_units(config.data_dir, staged) or [[rel] for rel in staged]
    preview = load_preview(config.data_dir)
    stale = preview is not None and preview_members(preview) != set(staged)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "staged": _staged_with_sizes(config.raw_dir, staged),
        "built_count": _built_count(config.wiki_dir),
        "costs": _build_costs(config.raw_dir, work_units),
        "stale": stale,
    }


def write_state(config: Config, manifest: Manifest) -> None:
    """Atomically write the derived state file. Best-effort; never raises."""
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        path = config.data_dir / STATE_FILENAME
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(compute_state(config, manifest)), encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        logger.debug("Could not write state file: %s", exc)


def emit_state(config: Config) -> None:
    """Recompute and write the state file with a fresh manifest handle."""
    write_state(config, Manifest(config.manifest_db_path))
