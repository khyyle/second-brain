"""Public job records omit internal payloads and captured configuration."""

from typing import Any

from ninja import ModelSchema, Schema

from second_brain.apps.jobs.models import Job


class JobOut(ModelSchema):
    class Meta:
        model = Job
        fields = [
            "id",
            "kind",
            "lane",
            "status",
            "result",
            "error",
            "cancel_requested",
            "created_at",
            "started_at",
            "finished_at",
            "retry_of",
        ]


class JobPage(Schema):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class JobProgress(Schema):
    """Flexible details until ingestion and compilation progress events are unified."""

    job: JobOut
    progress: dict[str, Any]
