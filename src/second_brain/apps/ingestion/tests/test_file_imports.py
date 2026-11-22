"""The GUI import contract includes durable copy paths and queue outcomes."""

from second_brain.apps.jobs.services.job_queue import claim_next_job
from second_brain.apps.jobs.worker import run_attempt
from second_brain.apps.library.models import Source


def test_import_receipt_and_queue_track_the_worker(api_client, config, tmp_path):
    original = tmp_path / "notes.md"
    original.write_text("# Notes\n\nA document that needs no provider.")
    response = api_client.post(
        "/v1/imports",
        {"paths": [str(original)], "source_type": "auto"},
        content_type="application/json",
    )
    assert response.status_code == 202
    receipt = response.json()
    managed_path = receipt["files"][0]["file_path"]
    assert managed_path != str(original)
    assert receipt["files"][0]["status"] == "pending"
    assert receipt["job"]["status"] == "queued"
    run_attempt(claim_next_job("ingest"))
    queue = api_client.get("/v1/imports").json()
    assert next(row for row in queue if row["file_path"] == managed_path)["status"] == "complete"
    assert original.exists()


def test_mixed_selection_uses_one_job_and_preserves_same_named_files(api_client, tmp_path):
    first = tmp_path / "a" / "notes.md"
    second = tmp_path / "b" / "notes.md"
    for path in (first, second):
        path.parent.mkdir()
        path.write_text(str(path))
    export = tmp_path / "conversations.json"
    export.write_text("[]")
    response = api_client.post(
        "/v1/imports",
        {"paths": [str(first), str(second), str(export)], "source_type": "auto"},
        content_type="application/json",
    )
    assert response.status_code == 202
    files = response.json()["files"]
    assert len({row["file_path"] for row in files}) == 3
    assert Source.objects.filter(source_type="documents").count() == 2
    assert Source.objects.filter(source_type="chatgpt").count() == 1


def test_import_validation_does_not_partially_copy_a_bad_selection(api_client, config, tmp_path):
    original = tmp_path / "notes.md"
    original.write_text("notes")
    response = api_client.post(
        "/v1/imports",
        {"paths": [str(original), str(tmp_path / "missing.md")]},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert Source.objects.count() == 0
    assert not list(config.drops_dir.rglob("*.md"))
