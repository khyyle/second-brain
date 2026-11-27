"""Tests for the cluster preview artifact and override resolution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from second_brain.apps.compilation.services.clustering import preview as preview_mod
from second_brain.apps.compilation.services.clustering.preview import (
    CLUSTERS_FILENAME,
    OVERRIDES_FILENAME,
    apply_overrides,
    clear_preview,
    group_cost_usd,
    load_preview,
    preview_members,
    reconcile_work_units,
    with_estimates,
    work_units_from_preview,
)
from second_brain.apps.preferences.services.agent_provider.model_selection import ModelPrice


def _group(group_id: str, *rels: str) -> dict:
    return {
        "id": group_id,
        "title": rels[0],
        "members": [{"rel": rel, "bytes": 100} for rel in rels],
    }


def test_apply_overrides_no_corrections_keeps_groups() -> None:
    groups = [_group("g1", "a.md", "b.md"), _group("g2", "c.md")]
    units = apply_overrides(groups, {"excluded": [], "split_groups": []})
    assert sorted(map(sorted, units)) == [["a.md", "b.md"], ["c.md"]]


def test_apply_overrides_pops_excluded_source_to_singleton() -> None:
    groups = [_group("g1", "a.md", "b.md")]
    units = apply_overrides(groups, {"excluded": ["b.md"], "split_groups": []})
    assert sorted(map(sorted, units)) == [["a.md"], ["b.md"]]


def test_apply_overrides_splits_group_into_singletons() -> None:
    groups = [_group("g1", "a.md", "b.md", "c.md")]
    units = apply_overrides(groups, {"excluded": [], "split_groups": ["g1"]})
    assert sorted(map(sorted, units)) == [["a.md"], ["b.md"], ["c.md"]]


def test_preview_members_covers_every_source() -> None:
    preview = {"groups": [_group("g1", "a.md", "b.md"), _group("g2", "c.md")]}
    assert preview_members(preview) == {"a.md", "b.md", "c.md"}


def test_work_units_from_preview_applies_overrides(tmp_path: Path) -> None:
    artifact = {"groups": [_group("g1", "a.md", "b.md")]}
    (tmp_path / CLUSTERS_FILENAME).write_text(json.dumps(artifact))
    (tmp_path / OVERRIDES_FILENAME).write_text(
        json.dumps({"excluded": ["b.md"], "split_groups": []})
    )
    units = work_units_from_preview(tmp_path)
    assert units is not None
    assert sorted(map(sorted, units)) == [["a.md"], ["b.md"]]


def test_work_units_from_preview_missing_artifact_returns_none(tmp_path: Path) -> None:
    assert work_units_from_preview(tmp_path) is None


def test_reconcile_work_units_missing_artifact_returns_none(tmp_path: Path) -> None:
    assert reconcile_work_units(tmp_path, ["a.md"]) is None


def test_reconcile_work_units_narrows_groups_to_staged(tmp_path: Path) -> None:
    artifact = {"groups": [_group("g1", "a.md", "b.md"), _group("g2", "c.md")]}
    (tmp_path / CLUSTERS_FILENAME).write_text(json.dumps(artifact))
    # b.md is no longer staged; its group keeps the rest.
    units = reconcile_work_units(tmp_path, ["a.md", "c.md"])
    assert sorted(map(sorted, units)) == [["a.md"], ["c.md"]]


def test_reconcile_work_units_adds_unplanned_sources_as_singletons(tmp_path: Path) -> None:
    artifact = {"groups": [_group("g1", "a.md", "b.md")]}
    (tmp_path / CLUSTERS_FILENAME).write_text(json.dumps(artifact))
    # d.md was staged after the preview, so it compiles on its own.
    units = reconcile_work_units(tmp_path, ["a.md", "b.md", "d.md"])
    assert sorted(map(sorted, units)) == [["a.md", "b.md"], ["d.md"]]


def test_reconcile_work_units_covers_exactly_the_staged_set(tmp_path: Path) -> None:
    artifact = {"groups": [_group("g1", "a.md", "b.md"), _group("g2", "c.md")]}
    (tmp_path / CLUSTERS_FILENAME).write_text(json.dumps(artifact))
    staged = ["a.md", "c.md", "e.md"]
    units = reconcile_work_units(tmp_path, staged)
    assert sorted(source for unit in units for source in unit) == sorted(staged)


def test_clear_preview_removes_both_files(tmp_path: Path) -> None:
    (tmp_path / ".clusters.json").write_text("{}")
    (tmp_path / OVERRIDES_FILENAME).write_text("{}")
    clear_preview(tmp_path)
    assert not (tmp_path / ".clusters.json").exists()
    assert not (tmp_path / OVERRIDES_FILENAME).exists()


def test_load_preview_invalid_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / ".clusters.json").write_text("{not json")
    assert load_preview(tmp_path) is None


def test_estimates_follow_the_chosen_models_price_and_stay_unknown_without_one() -> None:
    plan = {"groups": [_group("big", "a.md", "b.md", "c.md", "d.md"), _group("small", "e.md")]}
    cheap, dear = ModelPrice(input=1, output=1), ModelPrice(input=10, output=50)

    priced = with_estimates(plan, dear)
    big, small = (group["estimated_cost_usd"] for group in priced["groups"])
    assert big > small > 0
    assert priced["estimated_cost_usd"] == pytest.approx(big + small, abs=0.01)
    assert with_estimates(plan, cheap)["estimated_cost_usd"] < priced["estimated_cost_usd"]
    # 400 bytes are about 100 tokens, read four times, plus 4,000 tokens of pages.
    assert group_cost_usd(400, dear) == pytest.approx((400 * 10 + 4000 * 50) / 1e6)
    # A source longer than a tier's threshold is priced at that tier for every read.
    tiered = ModelPrice(
        input=1, output=1, tiers=[{"inputTokensAbove": 100, "input": 10, "output": 50}]
    )
    assert group_cost_usd(400, tiered) == group_cost_usd(400, cheap)
    assert group_cost_usd(404, tiered) == pytest.approx(group_cost_usd(404, dear))

    unknown = with_estimates(plan, None)
    assert unknown["estimated_cost_usd"] is None
    assert [group["estimated_cost_usd"] for group in unknown["groups"]] == [None, None]
    assert "estimated_cost_usd" not in plan  # The saved plan itself carries no prices.


def test_build_preview_groups_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = tmp_path / "raw"
    (raw / "chatgpt").mkdir(parents=True)
    for name, body in [
        ("a.md", '---\ntitle: "Alpha"\n---\nalpha body'),
        ("b.md", '---\ntitle: "Beta"\n---\nbeta body'),
        ("c.md", "no frontmatter here"),
    ]:
        (raw / "chatgpt" / name).write_text(body, encoding="utf-8")

    # Group a+b together, c alone, without running inference.
    monkeypatch.setattr(
        preview_mod,
        "cluster_scoped_sources",
        lambda *args, **kwargs: [["chatgpt/a.md", "chatgpt/b.md"], ["chatgpt/c.md"]],
    )

    stub_config = SimpleNamespace(
        raw_dir=raw,
        clustering=SimpleNamespace(
            algorithm="threshold", signature_chars=8000, sources=("chatgpt",), enabled=True
        ),
    )

    monkeypatch.setattr(preview_mod, "get_clusterer", lambda config: object())
    monkeypatch.setattr(
        preview_mod,
        "_staged_sources",
        lambda config: ["chatgpt/a.md", "chatgpt/b.md", "chatgpt/c.md"],
    )

    artifact = preview_mod.build_preview(stub_config)

    assert artifact["source_count"] == 3
    assert artifact["group_count"] == 2
    assert artifact["enabled"] is True
    assert [len(group["members"]) for group in artifact["groups"]] == [2, 1]
    # Largest group first; its representative title comes from front matter.
    assert artifact["groups"][0]["title"] in {"Alpha", "Beta"}
    assert len(artifact["groups"][0]["members"]) == 2
