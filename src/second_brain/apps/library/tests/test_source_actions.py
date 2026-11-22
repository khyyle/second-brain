"""Library operations exercised through the HTTP boundary and real ORM transactions."""

from pathlib import Path

import pytest
from django.db import OperationalError, connection
from django.db.models.query import QuerySet
from django.test import Client
from django.utils import timezone

from second_brain.apps.compilation.models import CompiledSource, DeferredSource
from second_brain.apps.ingestion.models import PageCache
from second_brain.apps.library.models import Source, TriageDecision
from second_brain.pipeline_config import PipelineConfig

pytestmark = pytest.mark.django_db


def _records(rel: str) -> None:
    TriageDecision.objects.create(
        raw_path=rel, decision="worthwhile", confidence=1.0, triaged_at=timezone.now()
    )
    CompiledSource.objects.create(raw_path=rel, compiled_at=timezone.now(), content_hash="hash")
    DeferredSource.objects.create(raw_path=rel, reason="retry", deferred_at=timezone.now())


def test_content_reads_active_and_skipped_with_the_same_logical_path(
    api_client: Client, raw_file
) -> None:
    raw_file("documents/a.md", text="# Active\n")
    raw_file("chatgpt/b.md", skipped=True, text="# Skipped\n")
    for rel, body in [("documents/a.md", "# Active\n"), ("chatgpt/b.md", "# Skipped\n")]:
        response = api_client.get("/v1/sources/content", {"raw_path": rel})
        assert response.status_code == 200
        assert response.json() == {"raw_path": rel, "markdown": body}
    assert (
        api_client.get("/v1/sources/content", {"raw_path": "documents/gone.md"}).status_code == 404
    )


@pytest.mark.parametrize(
    "rel",
    [
        "../outside.md",
        "/tmp/outside.md",
        ".skipped/a.md",
        "documents/../a.md",
        "documents//a.md",
        "documents/a.pdf",
        "documents/./a.md",
        "documents\\a.md",
    ],
)
def test_unsafe_paths_are_rejected_for_reads_and_mutations(
    api_client: Client, config: PipelineConfig, rel: str
) -> None:
    assert api_client.get("/v1/sources/content", {"raw_path": rel}).status_code == 400
    assert (
        api_client.delete(
            "/v1/sources", {"raw_path": rel}, QUERY_STRING=f"raw_path={rel}"
        ).status_code
        == 400
    )
    response = api_client.post(
        "/v1/sources/recompile", {"raw_paths": [rel]}, content_type="application/json"
    )
    assert response.status_code == 400


@pytest.mark.parametrize("directory_link", [False, True])
def test_symlinks_cannot_read_or_remove_an_external_file(
    api_client: Client, config: PipelineConfig, tmp_path: Path, directory_link: bool
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    original = outside / "notes.md"
    original.write_text("original", encoding="utf-8")
    if directory_link:
        (config.raw_dir / "documents").symlink_to(outside, target_is_directory=True)
    else:
        (config.raw_dir / "documents").mkdir()
        (config.raw_dir / "documents/notes.md").symlink_to(original)
    assert (
        api_client.get("/v1/sources/content", {"raw_path": "documents/notes.md"}).status_code
        == 400
    )
    assert api_client.delete("/v1/sources?raw_path=documents/notes.md").status_code == 400
    assert original.read_text() == "original"


@pytest.mark.parametrize("skipped", [False, True])
def test_delete_trashes_only_vault_copy_and_clears_related_records(
    api_client: Client,
    config: PipelineConfig,
    tmp_path: Path,
    raw_file,
    source_record,
    skipped: bool,
) -> None:
    rel = "chatgpt/chat.md"
    raw = raw_file(rel, skipped=skipped)
    original = tmp_path / "original.md"
    original.write_text("keep original", encoding="utf-8")
    source_record(rel, file_path=str(original), origin_path=str(original))
    source_record("documents/other.md")
    _records(rel)
    PageCache.objects.create(
        page_hash="shared", parse_lane="ocr", raw_markdown="cached", parsed_at=timezone.now()
    )

    response = api_client.delete(f"/v1/sources?raw_path={rel}")

    assert response.status_code == 204
    assert response.content == b""
    assert not raw.exists()
    assert original.read_text() == "keep original"
    assert len(list((config.data_dir / ".trash").glob(f"*/{rel}"))) == 1
    assert not Source.objects.filter(raw_output=rel).exists()
    assert not TriageDecision.objects.exists()
    assert not CompiledSource.objects.exists()
    assert not DeferredSource.objects.exists()
    assert Source.objects.filter(raw_output="documents/other.md").exists()
    assert PageCache.objects.count() == 1
    assert api_client.delete(f"/v1/sources?raw_path={rel}").status_code == 204


def test_delete_rolls_back_records_and_file_on_database_failure(
    api_client: Client, raw_file, source_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "chatgpt/chat.md"
    raw = raw_file(rel)
    source_record(rel)
    _records(rel)
    original_delete = QuerySet.delete

    def failing_delete(queryset):
        if queryset.model is CompiledSource:
            raise OperationalError("injected failure after other rows were deleted")
        return original_delete(queryset)

    monkeypatch.setattr(QuerySet, "delete", failing_delete)
    api_client.raise_request_exception = False
    response = api_client.delete(f"/v1/sources?raw_path={rel}")
    assert response.status_code == 500
    assert raw.exists()
    assert Source.objects.filter(raw_output=rel).exists()
    assert TriageDecision.objects.filter(raw_path=rel).exists()


@pytest.mark.django_db(transaction=True)
def test_failed_commit_restores_the_raw_file(
    api_client: Client, raw_file, source_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "documents/chat.md"
    raw = raw_file(rel)
    source_record(rel)

    def fail_commit():
        raise OperationalError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(connection, "commit", fail_commit)
        api_client.raise_request_exception = False
        assert api_client.delete(f"/v1/sources?raw_path={rel}").status_code == 500
    assert raw.exists()
    assert Source.objects.filter(raw_output=rel).exists()


def test_recompile_clears_only_selected_build_records(
    api_client: Client, raw_file, source_record
) -> None:
    rel = "chatgpt/chat.md"
    raw = raw_file(rel)
    source_record(rel)
    _records(rel)
    CompiledSource.objects.create(raw_path="documents/other.md", compiled_at=timezone.now())
    response = api_client.post(
        "/v1/sources/recompile", {"raw_paths": [rel, rel]}, content_type="application/json"
    )
    assert response.status_code == 204
    assert raw.exists()
    assert Source.objects.count() == 1
    assert TriageDecision.objects.count() == 1
    assert list(CompiledSource.objects.values_list("raw_path", flat=True)) == [
        "documents/other.md"
    ]
    assert not DeferredSource.objects.exists()


def test_recompile_validates_entire_batch_before_changing_records(
    api_client: Client, raw_file
) -> None:
    rel = "documents/a.md"
    raw_file(rel)
    _records(rel)
    response = api_client.post(
        "/v1/sources/recompile",
        {"raw_paths": [rel, "documents/missing.md"]},
        content_type="application/json",
    )
    assert response.status_code == 404
    assert CompiledSource.objects.filter(raw_path=rel).exists()
    assert DeferredSource.objects.filter(raw_path=rel).exists()
    raw_file("chatgpt/skipped.md", skipped=True)
    response = api_client.post(
        "/v1/sources/recompile",
        {"raw_paths": ["chatgpt/skipped.md"]},
        content_type="application/json",
    )
    assert response.status_code == 409
