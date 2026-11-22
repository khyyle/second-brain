"""The adopted schema supports normal ORM writes and explicit API serialization."""

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.schemas import JobOut
from second_brain.apps.library.models import Source
from second_brain.apps.library.schemas import SourceOut


@pytest.mark.django_db
def test_orm_writes_enforce_unique_origins_and_serialize_public_fields() -> None:
    now = timezone.now()
    fields = dict(
        sha256="hash",
        source_type="documents",
        ingested_at=now,
        updated_at=now,
        origin_path="/watched/notes.md",
    )
    source = Source.objects.create(file_path="/drops/notes.md", **fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        Source.objects.create(file_path="/drops/duplicate.md", **fields)
    assert Source.objects.count() == 1
    serialized = SourceOut.from_orm(source).model_dump(mode="json")
    assert serialized["file_path"] == source.pk
    assert serialized["status"] == "pending"
    assert "sha256" not in serialized


@pytest.mark.django_db
def test_job_serialization_omits_internal_payload_and_config() -> None:
    job = Job.objects.create(kind="build", lane="build", config_snapshot={"model": "example"})
    serialized = JobOut.from_orm(job).model_dump(mode="json")
    assert serialized["status"] == "queued"
    assert "payload" not in serialized
    assert "config_snapshot" not in serialized
