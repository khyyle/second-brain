"""Explicit job retries recover failed work without repeating successful parsing."""

import json
from pathlib import Path

import pytest

from second_brain.apps.ingestion.services.parsing import text_handler
from second_brain.apps.ingestion.services.source_manifest import Manifest
from second_brain.apps.ingestion.services.triage.gemma import TriageDecision, TriageResult
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services import job_queue
from second_brain.apps.jobs.worker import run_attempt
from second_brain.apps.library.models import Source
from second_brain.apps.library.models import TriageDecision as StoredTriageDecision
from second_brain.errors import SecondBrainError
from second_brain.ollama.client import OllamaUnavailableError


def _run_next_ingestion() -> Job:
    job = job_queue.claim_next_job("ingest")
    assert job is not None
    run_attempt(job)
    job.refresh_from_db()
    return job


@pytest.mark.parametrize(
    "selection,remove_copy",
    [
        ("selected", False),
        ("selected", True),
        ("drops", False),
        ("watched", False),
        ("watched", True),
    ],
)
def test_job_retry_recovers_unchanged_failure_and_preserves_success(
    api_client, config, tmp_path, monkeypatch, selection, remove_copy
):
    originals = tmp_path / "originals"
    originals.mkdir()
    for name in ("good.md", "bad.md"):
        (originals / name).write_text(f"# {name}\n\nDistinct content for {name}.")
    parsed = []
    process_text = text_handler.process_text_file

    def fail_once(path, output_dir):
        parsed.append(path.name)
        if path.name == "bad.md" and parsed.count("bad.md") == 1:
            raise SecondBrainError("Storage is temporarily unavailable.")
        return process_text(path, output_dir)

    monkeypatch.setattr(text_handler, "process_text_file", fail_once)
    if selection == "selected":
        response = api_client.post(
            "/v1/imports",
            data={"paths": [str(path) for path in sorted(originals.iterdir())]},
            content_type="application/json",
        )
        assert response.status_code == 202, response.content
    else:
        if selection == "watched":
            response = api_client.put(
                "/v1/watched-folders",
                data=[
                    {
                        "name": "research",
                        "path": str(originals),
                        "file_types": ["md"],
                        "enabled": True,
                    }
                ],
                content_type="application/json",
            )
            assert response.status_code == 200, response.content
        else:
            for path in originals.iterdir():
                (config.drops_dir / "documents" / path.name).write_bytes(path.read_bytes())
        job_queue.enqueue_job("ingest", {"drops_only": selection == "drops"})

    failed = _run_next_ingestion()
    original_payload = dict(failed.payload)
    assert failed.status == "failed"
    assert failed.result["completed"] == 1
    failed_source = Source.objects.get(status="failed")
    assert failed_source.error == "Storage is temporarily unavailable."
    completed = Source.objects.get(status="complete")
    completed_output = config.raw_dir / completed.raw_output
    original_output = completed_output.read_bytes()
    if remove_copy:
        Path(failed_source.file_path).unlink()
    unrelated = None
    if selection == "selected":
        unrelated_path = config.drops_dir / "documents" / "unrelated.md"
        unrelated_path.write_text("A failure outside this job's selected files.")
        manifest = Manifest()
        manifest.mark_processing(unrelated_path, "documents")
        manifest.mark_failed(unrelated_path, "Unrelated failure.")
        unrelated = Source.objects.get(pk=str(unrelated_path))

    job_queue.enqueue_job("ingest", {"drops_only": selection != "watched"})
    automatic_scan = _run_next_ingestion()
    assert automatic_scan.result == {"completed": 0, "failed": 0}
    failed_source.refresh_from_db()
    assert failed_source.status == "failed"
    assert parsed.count("bad.md") == 1

    response = api_client.post(
        f"/v1/jobs/{failed.pk}/retry", data={}, content_type="application/json"
    )
    assert response.status_code == 202, response.content
    retried = _run_next_ingestion()

    assert retried.pk != failed.pk
    assert retried.retry_of_id == failed.pk
    assert retried.status == "succeeded", retried.error
    assert retried.result == {"completed": 1, "failed": 0}
    assert parsed.count("good.md") == 1
    assert parsed.count("bad.md") == 2
    assert completed_output.read_bytes() == original_output
    failed_source.refresh_from_db()
    assert failed_source.status == "complete"
    assert failed_source.error is None
    if unrelated is not None:
        unrelated.refresh_from_db()
        assert unrelated.status == "failed"
        assert unrelated.error == "Unrelated failure."
        assert "unrelated.md" not in parsed
    failed.refresh_from_db()
    assert failed.status == "failed"
    assert failed.payload == original_payload


def test_triage_retry_does_not_reparse_completed_export(api_client, config, tmp_path, monkeypatch):
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "title": "Useful conversation",
                    "id": "retry123",
                    "mapping": {
                        "node": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["A conversation about gradient descent."]},
                            }
                        }
                    },
                }
            ]
        )
    )
    attempts = []

    def triage(path, preferences):
        attempts.append(path)
        if len(attempts) == 1:
            raise OllamaUnavailableError("Ollama isn't running.")
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=1.0)

    monkeypatch.setattr("second_brain.apps.ingestion.services.triage.pipeline.triage_file", triage)
    response = api_client.post(
        "/v1/imports",
        data={"paths": [str(export)], "source_type": "chatgpt"},
        content_type="application/json",
    )
    assert response.status_code == 202, response.content
    failed = _run_next_ingestion()
    source = Source.objects.get()
    assert failed.status == "failed"
    assert source.status == "complete"
    assert not Path(source.file_path).exists()
    assert not StoredTriageDecision.objects.exists()

    def unexpected_parse(*args, **kwargs):
        pytest.fail("Completed export should not be parsed again during triage retry")

    monkeypatch.setattr(
        "second_brain.apps.ingestion.services.ingestion_runs.ingest_file", unexpected_parse
    )
    job_queue.retry(failed.pk)
    retried = _run_next_ingestion()

    assert retried.status == "succeeded", retried.error
    assert retried.result == {"completed": 0, "failed": 0}
    assert StoredTriageDecision.objects.get().decision == "worthwhile"
    assert len(attempts) == 2
