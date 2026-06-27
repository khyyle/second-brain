"""Cluster preview artifact shared between the build and the menu-bar app.

The compiler can group staged sources so a topic compiles in one agent
run. Before paying for that build, the user can preview the grouping in
the app and lightly correct it (split a group, pop a source out). This
module computes the grouping, writes it to a JSON artifact the app reads,
and applies the user's corrections so the build honors what was shown.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from second_brain.clustering import get_clusterer
from second_brain.clustering.sources import cluster_scoped_sources
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.llm_providers import SUPPORTED_MODELS, resolve_profile

logger = logging.getLogger(__name__)

CLUSTERS_FILENAME = ".clusters.json"
OVERRIDES_FILENAME = ".cluster-overrides.json"

_TITLE_SCAN_BYTES = 4096

# Cost model for the preview, matching the app's per-unit assumptions. Assumed that the
# agent re-reads each source over a few turns and writes ~2 pages per group.
_INPUT_TURNS = 4
_OUTPUT_TOKENS_PER_GROUP = 4000
_CHARS_PER_TOKEN = 4


def _group_id(member_rels: list[str]) -> str:
    """Return a stable id for a group from its sorted member paths.

    Parameters
    ----------
    member_rels: list[str]
        Raw paths of the group's members.

    Returns
    -------
    str
        A short hex digest, identical across previews when membership is
        unchanged so user overrides keyed on it survive a re-preview.
    """
    joined = "\n".join(sorted(member_rels))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _read_title(path: Path) -> str:
    """Return a source's front-matter title, or its cleaned stem if absent.

    Parameters
    ----------
    path: Path
        Raw markdown source file.

    Returns
    -------
    str
        The title for display as the group's representative label.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(_TITLE_SCAN_BYTES)
    except OSError:
        return path.stem
    for line in head.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("title:"):
            return stripped.split(":", 1)[1].strip().strip('"').strip("'") or path.stem
    return path.stem


def group_cost_usd(
    total_bytes: int, input_usd_per_mtok: float, output_usd_per_mtok: float
) -> float:
    """Estimate the USD cost of compiling one group from its total bytes."""
    input_tokens = max(total_bytes / _CHARS_PER_TOKEN, 1) * _INPUT_TURNS
    return (
        input_tokens / 1_000_000 * input_usd_per_mtok
        + _OUTPUT_TOKENS_PER_GROUP / 1_000_000 * output_usd_per_mtok
    )


def model_prices() -> dict[str, tuple[float, float]]:
    """Return (input, output) USD-per-Mtok for every selectable model.

    Precomputed once per preview so the plan can carry a cost for each
    model, letting the app switch models without re-running the preview.
    """
    prices: dict[str, tuple[float, float]] = {}
    for provider, models in SUPPORTED_MODELS.items():
        for model in models:
            profile = resolve_profile(provider, model)
            prices[model] = (profile.input_price_per_mtok, profile.output_price_per_mtok)
    return prices


def _staged_sources(config: Config, manifest: Manifest) -> list[str]:
    """Return the raw paths the build would compile: new and worthwhile.

    Parameters
    ----------
    config: Config
        Application configuration.
    manifest: Manifest
        Ingestion manifest holding compiled and triage records.

    Returns
    -------
    list[str]
        Sorted relative raw paths staged for compilation.
    """
    from second_brain.compilation.compiler import find_new_sources
    from second_brain.triage.pipeline import worthwhile_sources

    new_sources = find_new_sources(config, manifest)
    if config.triage.enabled:
        new_sources = worthwhile_sources(manifest, new_sources)
    return new_sources


def build_preview(
    config: Config,
    manifest: Manifest,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Cluster the staged sources and return a preview artifact dict.

    Parameters
    ----------
    config: Config
        Application configuration (clustering and embedding settings).
    manifest: Manifest
        Ingestion manifest for source selection.
    progress: Callable[[int, int], None] | None
        Called with ``(done, total)`` as sources are embedded, for a live
        progress readout.

    Returns
    -------
    dict
        The preview artifact: metadata plus a list of group descriptors,
        each with a stable id, representative title, members, and cost.
    """
    staged = _staged_sources(config, manifest)
    profile = resolve_profile(config.compilation.provider, config.compilation.model)
    prices = model_prices()
    clusters = cluster_scoped_sources(
        staged,
        config.raw_dir,
        config.search,
        get_clusterer(config.clustering),
        config.clustering.sources,
        signature_chars=config.clustering.signature_chars,
        progress=progress,
    )

    groups: list[dict] = []
    total_costs: dict[str, float] = dict.fromkeys(prices, 0.0)
    for members in clusters:
        sized = [(rel, (config.raw_dir / rel).stat().st_size) for rel in members]
        total_bytes = sum(size for _, size in sized)
        representative = max(sized, key=lambda pair: pair[1])[0]
        costs = {
            model: group_cost_usd(total_bytes, input_price, output_price)
            for model, (input_price, output_price) in prices.items()
        }
        for model, cost in costs.items():
            total_costs[model] += cost
        groups.append(
            {
                "id": _group_id(members),
                "title": _read_title(config.raw_dir / representative),
                "members": [{"rel": rel, "bytes": size} for rel, size in sized],
                "estimated_cost_usd": round(costs[profile.model], 4),
                "costs": {model: round(cost, 4) for model, cost in costs.items()},
            }
        )

    groups.sort(key=lambda group: len(group["members"]), reverse=True)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "algorithm": config.clustering.algorithm,
        "enabled": config.clustering.enabled,
        "source_count": len(staged),
        "group_count": len(groups),
        "estimated_cost_usd": round(total_costs[profile.model], 2),
        "costs": {model: round(cost, 2) for model, cost in total_costs.items()},
        "groups": groups,
    }


def write_preview(
    config: Config,
    manifest: Manifest,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Compute the preview, write it to the vault, and clear stale overrides.

    Parameters
    ----------
    config: Config
        Application configuration.
    manifest: Manifest
        Ingestion manifest for source selection.
    progress: Callable[[int, int], None] | None
        Forwarded to :func:`build_preview` for a live progress readout.

    Returns
    -------
    dict
        The artifact that was written (so callers can report counts/cost).
    """
    artifact = build_preview(config, manifest, progress=progress)
    path = config.data_dir / CLUSTERS_FILENAME
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    # A fresh grouping invalidates corrections aimed at the previous one.
    (config.data_dir / OVERRIDES_FILENAME).unlink(missing_ok=True)
    logger.info(
        "Cluster preview: %d sources -> %d groups (~$%.2f)",
        artifact["source_count"],
        artifact["group_count"],
        artifact["estimated_cost_usd"],
    )
    return artifact


def load_preview(data_dir: Path) -> dict | None:
    """Return the written preview artifact, or None if absent or invalid."""
    path = data_dir / CLUSTERS_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring invalid cluster preview: %s", exc)
        return None


def _load_overrides(data_dir: Path) -> dict:
    """Return user corrections to the preview, or empty defaults."""
    path = data_dir / OVERRIDES_FILENAME
    if not path.exists():
        return {"excluded": [], "split_groups": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring invalid cluster overrides: %s", exc)
        return {"excluded": [], "split_groups": []}
    return {
        "excluded": list(data.get("excluded", [])),
        "split_groups": list(data.get("split_groups", [])),
    }


def apply_overrides(groups: list[dict], overrides: dict) -> list[list[str]]:
    """Resolve preview groups and user corrections into logical work units.

    A popped-out source and the members of a split group each become their
    own singleton; everything else keeps its computed grouping.

    Parameters
    ----------
    groups: list[dict]
        Group descriptors from the preview artifact.
    overrides: dict
        ``excluded`` member paths and ``split_groups`` ids.

    Returns
    -------
    list[list[str]]
        Member-path groups to hand to the compiler, one per work unit.
    """
    excluded = set(overrides.get("excluded", []))
    split = set(overrides.get("split_groups", []))

    units: list[list[str]] = []
    for group in groups:
        member_rels = [member["rel"] for member in group["members"]]
        kept = [rel for rel in member_rels if rel not in excluded]
        popped = [rel for rel in member_rels if rel in excluded]
        if group["id"] in split:
            units.extend([rel] for rel in kept)
        elif kept:
            units.append(kept)
        units.extend([rel] for rel in popped)
    return units


def work_units_from_preview(data_dir: Path) -> list[list[str]] | None:
    """Return the work units implied by the preview plus user overrides.

    Parameters
    ----------
    data_dir: Path
        Vault root holding the artifact and overrides.

    Returns
    -------
    list[list[str]] | None
        Logical work units, or None when no preview artifact is present.
    """
    preview = load_preview(data_dir)
    if preview is None:
        return None
    return apply_overrides(preview["groups"], _load_overrides(data_dir))


def preview_members(preview: dict) -> set[str]:
    """Return every source path covered by a preview artifact."""
    return {member["rel"] for group in preview["groups"] for member in group["members"]}


def reconcile_work_units(data_dir: Path, staged: list[str]) -> list[list[str]] | None:
    """Map the reviewed preview onto the sources currently staged.

    Each reviewed group is narrowed to the members still staged, and any staged
    source the preview never covered is returned as its own unit. The result
    covers exactly ``staged``, so a drifted staged set keeps the user's grouping
    rather than discarding it.

    Parameters
    ----------
    data_dir: Path
        Vault root holding the preview artifact and overrides.
    staged: list[str]
        Source paths currently staged for the build.

    Returns
    -------
    list[list[str]] | None
        Work units covering exactly ``staged``, or None when no preview exists.
    """
    units = work_units_from_preview(data_dir)
    if units is None:
        return None

    staged_set = set(staged)
    covered: set[str] = set()
    reconciled: list[list[str]] = []

    for unit in units:
        covered.update(unit)
        kept = [rel for rel in unit if rel in staged_set]
        if kept:
            reconciled.append(kept)

    reconciled.extend([rel] for rel in staged if rel not in covered)
    return reconciled


def clear_preview(data_dir: Path) -> None:
    """Remove the preview artifact and overrides once a build consumes them."""
    (data_dir / CLUSTERS_FILENAME).unlink(missing_ok=True)
    (data_dir / OVERRIDES_FILENAME).unlink(missing_ok=True)
