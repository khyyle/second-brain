"""Manual curation owns both the verdict and the matching file movement."""

import pytest
from django.db import OperationalError
from django.db.models.query import QuerySet
from django.test import Client
from django.utils import timezone

from second_brain.apps.library.models import TriageDecision
from second_brain.pipeline_config import PipelineConfig

pytestmark = pytest.mark.django_db


def _update(client: Client, rels: list[str], decision: str):
    return client.patch(
        "/v1/triage", {"raw_paths": rels, "decision": decision}, content_type="application/json"
    )


def test_skip_restore_and_filter_manual_decisions(
    api_client: Client, config: PipelineConfig, raw_file
) -> None:
    rels = ["chatgpt/b.md", "chatgpt/a.md"]
    for rel in rels:
        raw_file(rel)
    response = _update(api_client, rels + [rels[0]], "skip")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert all(
        row["confidence"] == 1.0 and row["reason"] == "manual override" for row in response.json()
    )
    for rel in rels:
        assert not (config.raw_dir / rel).exists()
        assert (config.raw_dir / ".skipped" / rel).is_file()
    listed = api_client.get("/v1/triage", {"decision": "skip"}).json()
    assert [row["raw_path"] for row in listed] == sorted(rels)

    response = _update(api_client, [rels[0]], "worthwhile")
    assert response.status_code == 200
    assert (config.raw_dir / rels[0]).is_file()
    assert not (config.raw_dir / ".skipped" / rels[0]).exists()
    assert len(api_client.get("/v1/triage", {"decision": "skip"}).json()) == 1
    assert TriageDecision.objects.count() == 2


def test_review_restores_a_skipped_chat(
    api_client: Client, config: PipelineConfig, raw_file
) -> None:
    rel = "chatgpt/a.md"
    raw_file(rel, skipped=True)
    assert _update(api_client, [rel], "review").status_code == 200
    assert (config.raw_dir / rel).is_file()
    assert TriageDecision.objects.get(pk=rel).decision == "review"


def test_unknown_source_or_non_chat_rejects_whole_batch(api_client: Client, raw_file) -> None:
    raw = raw_file("chatgpt/a.md")
    response = _update(api_client, ["chatgpt/a.md", "chatgpt/missing.md"], "skip")
    assert response.status_code == 404
    assert raw.exists()
    assert not TriageDecision.objects.exists()
    raw_file("documents/a.md")
    assert _update(api_client, ["documents/a.md"], "skip").status_code == 400
    assert not TriageDecision.objects.exists()


def test_conflicting_files_are_never_overwritten(api_client: Client, raw_file) -> None:
    active = raw_file("chatgpt/a.md", text="active")
    skipped = raw_file("chatgpt/a.md", skipped=True, text="held")
    assert _update(api_client, ["chatgpt/a.md"], "skip").status_code == 409
    assert active.read_text() == "active"
    assert skipped.read_text() == "held"
    assert not TriageDecision.objects.exists()


def test_purged_chat_can_change_verdict_without_recreating_a_file(
    api_client: Client, config: PipelineConfig
) -> None:
    rel = "chatgpt/purged.md"
    TriageDecision.objects.create(
        raw_path=rel, decision="skip", confidence=1, triaged_at=timezone.now()
    )
    assert _update(api_client, [rel], "worthwhile").status_code == 200
    assert TriageDecision.objects.get(pk=rel).decision == "worthwhile"
    assert not (config.raw_dir / rel).exists()


def test_failed_upsert_restores_every_file_and_previous_verdict(
    api_client: Client, raw_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = raw_file("chatgpt/a.md")
    second = raw_file("chatgpt/b.md")
    TriageDecision.objects.create(
        raw_path="chatgpt/a.md", decision="review", confidence=0.5, triaged_at=timezone.now()
    )

    def fail_upsert(*args, **kwargs):
        raise OperationalError("upsert failed")

    monkeypatch.setattr(QuerySet, "bulk_create", fail_upsert)
    api_client.raise_request_exception = False
    assert _update(api_client, ["chatgpt/a.md", "chatgpt/b.md"], "skip").status_code == 500
    assert first.is_file() and second.is_file()
    assert TriageDecision.objects.count() == 1
    assert TriageDecision.objects.get(pk="chatgpt/a.md").decision == "review"


@pytest.mark.parametrize(
    "payload",
    [
        {"raw_paths": [], "decision": "skip"},
        {"raw_paths": ["chatgpt/a.md"], "decision": "typo"},
        {"raw_paths": ["chatgpt/a.md"], "decision": "skip", "confidence": 0.1},
        {"raw_paths": [f"chatgpt/{n}.md" for n in range(201)], "decision": "skip"},
    ],
)
def test_invalid_body_is_rejected_before_service_queries(
    api_client: Client, payload: dict, django_assert_num_queries
) -> None:
    with django_assert_num_queries(0):
        response = api_client.patch("/v1/triage", payload, content_type="application/json")
    assert response.status_code == 422
