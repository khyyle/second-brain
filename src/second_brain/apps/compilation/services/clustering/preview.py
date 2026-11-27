"""Compute source grouping previews and persist reviewed grouping overrides."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from second_brain.apps.compilation.services.clustering import get_clusterer
from second_brain.apps.compilation.services.clustering.sources import cluster_scoped_sources
from second_brain.apps.preferences.services.agent_provider.model_selection import ModelPrice
from second_brain.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)

CLUSTERS_FILENAME = ".clusters.json"
OVERRIDES_FILENAME = ".cluster-overrides.json"

_TITLE_SCAN_BYTES = 4096

# Estimates assume four reads of the input and roughly two output pages per group.
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


def group_cost_usd(total_bytes: int, price: ModelPrice | None) -> float | None:
    """Estimate the USD cost of compiling one group from its total bytes.

    Parameters
    ----------
    total_bytes: int
        Combined size of source text in bytes.
    price: ModelPrice | None
        The chosen model's price, or None when it is unknown.

    Returns
    -------
    float | None
        Estimated compilation cost using the configured token and turn assumptions,
        or None when the price is unknown. An unknown cost is not a free build.
    """
    if price is None:
        return None
    tokens_per_read = max(total_bytes / _CHARS_PER_TOKEN, 1)
    input_rate, output_rate = price.rates_for(tokens_per_read)
    return (
        tokens_per_read * _INPUT_TURNS / 1_000_000 * input_rate
        + _OUTPUT_TOKENS_PER_GROUP / 1_000_000 * output_rate
    )


def with_estimates(plan: dict, price: ModelPrice | None) -> dict:
    """Add what each group, and the whole plan, would cost to build with the chosen model.

    The saved plan holds no prices: the model can change after the grouping was made.

    Parameters
    ----------
    plan: dict
        Preview artifact from :func:`load_preview`.
    price: ModelPrice | None
        The chosen model's price, or None when it is unknown.

    Returns
    -------
    dict
        The plan with ``estimated_cost_usd`` on it and on each group, None when unknown.
    """
    costs = [
        group_cost_usd(sum(member["bytes"] for member in group["members"]), price)
        for group in plan["groups"]
    ]
    groups = [
        {**group, "estimated_cost_usd": None if cost is None else round(cost, 4)}
        for group, cost in zip(plan["groups"], costs, strict=True)
    ]
    total = None if price is None else round(sum(costs), 2)
    return {**plan, "groups": groups, "estimated_cost_usd": total}


def _staged_sources(config: PipelineConfig) -> list[str]:
    """Return the raw paths the build would compile: new and worthwhile.

    Parameters
    ----------
    config: PipelineConfig
        Application configuration.

    Returns
    -------
    list[str]
        Sorted relative raw paths staged for compilation.
    """
    from second_brain.apps.compilation.services.staging import find_new_sources
    from second_brain.apps.ingestion.services.triage.pipeline import worthwhile_sources

    new_sources = find_new_sources(config)
    if config.triage.enabled:
        new_sources = worthwhile_sources(config, new_sources)
    return new_sources


def build_preview(
    config: PipelineConfig,
    progress: Callable[[int, int], None] | None = None,
    *,
    sources: list[str] | None = None,
) -> dict:
    """Cluster the staged sources and return a preview artifact dict.

    Parameters
    ----------
    config: PipelineConfig
        Vault paths and clustering settings.
    progress: Callable[[int, int], None] | None
        Called with `(done, total)` as sources are embedded.
    sources: list[str] | None
        Explicit raw-relative source paths, or None to discover eligible sources.

    Returns
    -------
    dict
        The preview artifact: metadata plus a list of group descriptors,
        each with a stable id, representative title, and members.
    """
    staged = _staged_sources(config) if sources is None else sources
    if config.clustering.enabled:
        clusters = cluster_scoped_sources(
            staged,
            config.raw_dir,
            get_clusterer(config.clustering),
            config.clustering.sources,
            signature_chars=config.clustering.signature_chars,
            progress=progress,
        )

    else:
        clusters = [[source] for source in staged]

    groups: list[dict] = []
    for members in clusters:
        sized = [(rel, (config.raw_dir / rel).stat().st_size) for rel in members]
        representative = max(sized, key=lambda pair: pair[1])[0]
        groups.append(
            {
                "id": _group_id(members),
                "title": _read_title(config.raw_dir / representative),
                "members": [{"rel": rel, "bytes": size} for rel, size in sized],
            }
        )

    groups.sort(key=lambda group: len(group["members"]), reverse=True)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "algorithm": config.clustering.algorithm,
        "enabled": config.clustering.enabled,
        "source_count": len(staged),
        "group_count": len(groups),
        "groups": groups,
    }


def load_preview(data_dir: Path) -> dict | None:
    """Return the written preview artifact, or None if absent or invalid.

    Parameters
    ----------
    data_dir: Path
        Vault root containing the saved grouping preview.

    Returns
    -------
    dict | None
        Saved preview, or None when absent, unreadable, or invalid JSON.
    """
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
    """Return every source path covered by a preview artifact.

    Parameters
    ----------
    preview: dict
        Grouping preview containing group member paths.

    Returns
    -------
    set[str]
        All source paths included in the preview groups.
    """
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
    """Remove the preview artifact and overrides once a build consumes them.

    Parameters
    ----------
    data_dir: Path
        Vault root containing the preview and override files.

    Returns
    -------
    None
    """
    (data_dir / CLUSTERS_FILENAME).unlink(missing_ok=True)
    (data_dir / OVERRIDES_FILENAME).unlink(missing_ok=True)
