"""Tests for the cluster preview artifact and override resolution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from second_brain.clustering import preview as preview_mod
from second_brain.clustering.preview import (
    CLUSTERS_FILENAME,
    OVERRIDES_FILENAME,
    apply_overrides,
    clear_preview,
    load_preview,
    preview_members,
    work_units_from_preview,
)


def _group(group_id: str, *rels: str) -> dict:
    return {
        "id": group_id,
        "title": rels[0],
        "members": [{"rel": rel, "bytes": 100} for rel in rels],
        "estimated_cost_usd": 0.1,
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


def test_clear_preview_removes_both_files(tmp_path: Path) -> None:
    (tmp_path / ".clusters.json").write_text("{}")
    (tmp_path / OVERRIDES_FILENAME).write_text("{}")
    clear_preview(tmp_path)
    assert not (tmp_path / ".clusters.json").exists()
    assert not (tmp_path / OVERRIDES_FILENAME).exists()


def test_load_preview_invalid_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / ".clusters.json").write_text("{not json")
    assert load_preview(tmp_path) is None


def test_build_preview_groups_and_costs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = tmp_path / "raw"
    (raw / "chatgpt").mkdir(parents=True)
    for name, body in [
        ("a.md", '---\ntitle: "Alpha"\n---\nalpha body'),
        ("b.md", '---\ntitle: "Beta"\n---\nbeta body'),
        ("c.md", "no frontmatter here"),
    ]:
        (raw / "chatgpt" / name).write_text(body, encoding="utf-8")

    # Group a+b together, c alone, without touching Ollama.
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
        search=SimpleNamespace(),
    )

    monkeypatch.setattr(preview_mod, "get_clusterer", lambda config: object())
    monkeypatch.setattr(preview_mod, "_staged_sources", lambda config, manifest: [
        "chatgpt/a.md", "chatgpt/b.md", "chatgpt/c.md"
    ])

    artifact = preview_mod.build_preview(stub_config, manifest=None)

    assert artifact["source_count"] == 3
    assert artifact["group_count"] == 2
    assert artifact["enabled"] is True
    assert artifact["estimated_cost_usd"] > 0
    # Largest group first; its representative title comes from front matter.
    assert artifact["groups"][0]["title"] in {"Alpha", "Beta"}
    assert len(artifact["groups"][0]["members"]) == 2
