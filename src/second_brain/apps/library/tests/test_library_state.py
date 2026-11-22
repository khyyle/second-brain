"""Library summaries combine source records, triage, and job outcomes."""

import json

import pytest
from django.test import Client
from django.utils import timezone

from second_brain.apps.compilation.models import CompiledSource, DeferredSource
from second_brain.apps.compilation.services.clustering.preview import CLUSTERS_FILENAME
from second_brain.apps.library.models import TriageDecision
from second_brain.apps.library.services.library_state import get_state
from second_brain.file_hashing import hash_file
from second_brain.pipeline_config import PipelineConfig

pytestmark = pytest.mark.django_db


def _verdict(rel: str, decision: str) -> None:
    TriageDecision.objects.create(
        raw_path=rel, decision=decision, confidence=1, triaged_at=timezone.now()
    )


def test_state_reads_orm_in_bulk_and_does_not_write_artifacts(
    api_client: Client,
    config: PipelineConfig,
    raw_file,
    source_record,
    django_assert_num_queries,
) -> None:
    raw_file("documents/a.md")
    raw_file("documents/b.md")
    raw_file("chatgpt/pending.md")
    source_record("documents/failed.md", status="failed", error="OCR failed")
    with django_assert_num_queries(4):
        response = api_client.get("/v1/state")
    assert response.status_code == 200
    state = response.json()
    assert [row["rel"] for row in state["staged"]] == ["documents/a.md", "documents/b.md"]
    assert all(row["bytes"] > 0 and row["costs"] for row in state["staged"])
    assert state["pending_triage"] == ["chatgpt/pending.md"]
    assert state["ingest_failures"] == {"/watched/documents/failed.md": "OCR failed"}
    assert state["built_count"] == 0 and state["stale"] is False
    assert state["costs"] and all(cost >= 0 for cost in state["costs"].values())
    assert not (config.data_dir / ".state.json").exists()
    # The Django test database is separate; the service must not open a raw-SQL store.
    assert not (config.data_dir / "manifest.db").exists()


def test_triage_rules_and_counts(config: PipelineConfig, raw_file) -> None:
    raw_file("chatgpt/kept.md")
    raw_file("chatgpt/review.md")
    raw_file("chatgpt/pending.md")
    raw_file("chatgpt/skipped.md", skipped=True)
    raw_file("documents/ready.md")
    _verdict("chatgpt/kept.md", "worthwhile")
    _verdict("chatgpt/review.md", "review")
    _verdict("chatgpt/skipped.md", "skip")
    _verdict("chatgpt/gone.md", "skip")
    state = get_state(config)
    assert [row["rel"] for row in state["staged"]] == ["chatgpt/kept.md", "documents/ready.md"]
    assert state["needs_review"] == state["skipped"] == 1
    assert state["pending_triage"] == ["chatgpt/pending.md"]
    disabled = config.model_copy(
        update={"triage": config.triage.model_copy(update={"enabled": False})}
    )
    state = get_state(disabled)
    assert state["pending_triage"] == []
    assert "chatgpt/pending.md" in [row["rel"] for row in state["staged"]]
    assert "chatgpt/review.md" not in [row["rel"] for row in state["staged"]]


def test_current_compiled_and_deferred_versions_and_changed_bytes(
    config: PipelineConfig, raw_file
) -> None:
    compiled = raw_file("documents/compiled.md")
    changed = raw_file("documents/changed.md", text="new bytes")
    deferred = raw_file("documents/deferred.md")
    raw_file("documents/old-mark.md")
    CompiledSource.objects.create(
        raw_path="documents/compiled.md",
        compiled_at=timezone.now(),
        content_hash=hash_file(compiled),
    )
    CompiledSource.objects.create(
        raw_path="documents/changed.md", compiled_at=timezone.now(), content_hash="outdated"
    )
    CompiledSource.objects.create(
        raw_path="documents/old-mark.md", compiled_at=timezone.now(), content_hash=None
    )
    DeferredSource.objects.create(
        raw_path="documents/deferred.md",
        reason="Too large",
        retryable=False,
        deferred_at=timezone.now(),
        content_hash=hash_file(deferred),
    )
    state = get_state(config)
    assert [row["rel"] for row in state["staged"]] == [
        "documents/changed.md",
        "documents/deferred.md",
    ]
    assert state["staged"][1]["defer_reason"] == "Too large"
    assert state["staged"][1]["retryable"] is False
    deferred.write_text("edited", encoding="utf-8")
    state = get_state(config)
    assert all("defer_reason" not in row for row in state["staged"])
    assert changed.exists()


def test_grouping_staleness_and_built_pages(config: PipelineConfig, raw_file) -> None:
    raw_file("documents/a.md")
    raw_file("documents/b.md")
    (config.wiki_dir / "concepts/page.md").write_text("# Page", encoding="utf-8")
    preview = {
        "groups": [{"id": "g", "title": "a", "members": [{"rel": "documents/a.md", "bytes": 1}]}]
    }
    (config.data_dir / CLUSTERS_FILENAME).write_text(json.dumps(preview), encoding="utf-8")
    assert get_state(config)["stale"] is True
    preview["groups"][0]["members"].append({"rel": "documents/b.md", "bytes": 1})
    (config.data_dir / CLUSTERS_FILENAME).write_text(json.dumps(preview), encoding="utf-8")
    CompiledSource.objects.create(raw_path="documents/a.md", compiled_at=timezone.now())
    state = get_state(config)
    assert state["stale"] is False
    assert state["built_count"] == 1
    assert [row["rel"] for row in state["staged"]] == ["documents/b.md"]


def test_state_loads_selected_configuration(
    api_client: Client, config: PipelineConfig, raw_file
) -> None:
    raw_file("chatgpt/a.md")
    (config.data_dir / "config.yaml").write_text("triage:\n  enabled: false\n", encoding="utf-8")
    response = api_client.get("/v1/state")
    assert response.status_code == 200
    assert [row["rel"] for row in response.json()["staged"]] == ["chatgpt/a.md"]
    (config.data_dir / "config.yaml").write_text("triage: [invalid\n", encoding="utf-8")
    response = api_client.get("/v1/state")
    assert response.status_code == 500
    assert "valid YAML" in response.json()["detail"]
