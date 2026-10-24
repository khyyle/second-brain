"""Tests for deciding which raw sources a build should compile."""

from __future__ import annotations

from pathlib import Path

from second_brain.apps.compilation.services.build_records import BuildStore
from second_brain.apps.compilation.services.staging import (
    find_new_sources,
    find_set_aside_sources,
)
from second_brain.file_hashing import hash_file
from second_brain.pipeline_config import PipelineConfig


def _write_source(config: PipelineConfig, rel: str, body: str) -> Path:
    raw_file = config.raw_dir / rel
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(body, encoding="utf-8")
    return raw_file


def test_a_built_source_is_staged_again_once_its_file_changes(
    config: PipelineConfig, build_store: BuildStore
) -> None:
    unchanged = _write_source(config, "documents/unchanged.md", "first version")
    edited = _write_source(config, "documents/edited.md", "first version")
    build_store.mark_compiled(
        {"documents/unchanged.md": hash_file(unchanged), "documents/edited.md": hash_file(edited)}
    )

    edited.write_text("second version", encoding="utf-8")

    assert find_new_sources(config) == ["documents/edited.md"]


def test_a_set_aside_source_is_staged_again_once_its_file_changes(
    config: PipelineConfig, build_store: BuildStore
) -> None:
    raw_file = _write_source(config, "documents/long.md", "first version")
    build_store.defer_sources(
        {"documents/long.md": hash_file(raw_file)}, "The model didn't finish.", retryable=True
    )
    assert find_new_sources(config) == []
    assert set(find_set_aside_sources(config)) == {"documents/long.md"}

    raw_file.write_text("second version", encoding="utf-8")

    assert find_new_sources(config) == ["documents/long.md"]
    assert find_set_aside_sources(config) == {}


def test_setting_aside_one_source_leaves_other_sources_staged(
    config: PipelineConfig, build_store: BuildStore
) -> None:
    _write_source(config, "documents/ready.md", "alpha")
    deferred = _write_source(config, "documents/deferred.md", "beta")
    build_store.defer_sources(
        {"documents/deferred.md": hash_file(deferred)},
        "The model didn't finish.",
        retryable=True,
    )

    assert find_new_sources(config) == ["documents/ready.md"]


def test_a_build_record_without_a_hash_counts_as_current(config: PipelineConfig) -> None:
    _write_source(config, "documents/old.md", "built before hashes were kept")
    from django.utils import timezone

    from second_brain.apps.compilation.models import CompiledSource

    CompiledSource.objects.create(raw_path="documents/old.md", compiled_at=timezone.now())

    assert find_new_sources(config) == []
