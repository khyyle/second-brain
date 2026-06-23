"""Tests for the compile-stage window guard that defers oversized sources."""

from __future__ import annotations

from pathlib import Path

from second_brain.compilation.compiler import _defer_oversized
from second_brain.config import CompilationConfig, Config
from second_brain.ingestion.manifest import DEFERRED_DECISION, Manifest
from second_brain.llm_providers import resolve_profile


def _config(tmp_path: Path, *, window_reserve: float) -> Config:
    cfg = Config(
        data_dir=tmp_path / "sb",
        compilation=CompilationConfig(window_reserve=window_reserve),
    )
    cfg.ensure_directories()
    return cfg


def _write_source(config: Config, rel: str, size_bytes: int) -> str:
    path = config.raw_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * size_bytes, encoding="utf-8")
    return rel


def test_defers_oversized_source(tmp_path: Path) -> None:
    # A tiny reserve makes the usable window only a few tokens, so any real
    # source is "too large" and gets deferred.
    config = _config(tmp_path, window_reserve=0.00005)  # 1M * 5e-5 = 50 tokens
    manifest = Manifest(config.manifest_db_path)
    rel = _write_source(config, "chatgpt/big.md", 4000)  # ~1000 tokens
    profile = resolve_profile("anthropic", "claude-sonnet-4-6")

    fitting, deferred = _defer_oversized(
        config, manifest, profile, [rel], config.raw_dir, dry_run=False
    )

    assert fitting == []
    assert deferred == 1
    assert manifest.get_triage_decisions()[rel] == DEFERRED_DECISION


def test_self_heals_when_window_grows(tmp_path: Path) -> None:
    config_tight = _config(tmp_path, window_reserve=0.00005)
    manifest = Manifest(config_tight.manifest_db_path)
    rel = _write_source(config_tight, "chatgpt/big.md", 4000)
    profile = resolve_profile("anthropic", "claude-sonnet-4-6")

    _defer_oversized(config_tight, manifest, profile, [rel], config_tight.raw_dir, dry_run=False)
    assert manifest.get_triage_decisions()[rel] == DEFERRED_DECISION

    # A roomier window (e.g. switching to a larger model) restores it.
    config_loose = _config(tmp_path, window_reserve=1.0)
    fitting, deferred = _defer_oversized(
        config_loose, manifest, profile, [rel], config_loose.raw_dir, dry_run=False
    )

    assert fitting == [rel]
    assert deferred == 0
    assert manifest.get_triage_decisions()[rel] == "worthwhile"


def test_dry_run_does_not_record(tmp_path: Path) -> None:
    config = _config(tmp_path, window_reserve=0.00005)
    manifest = Manifest(config.manifest_db_path)
    rel = _write_source(config, "chatgpt/big.md", 4000)
    profile = resolve_profile("anthropic", "claude-sonnet-4-6")

    fitting, deferred = _defer_oversized(
        config, manifest, profile, [rel], config.raw_dir, dry_run=True
    )

    assert fitting == []
    assert deferred == 1
    # Nothing persisted in a dry run.
    assert rel not in manifest.get_triage_decisions()
