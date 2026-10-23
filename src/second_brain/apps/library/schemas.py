"""Library API shapes; database models and HTTP payloads have separate responsibilities."""

from datetime import datetime
from typing import Literal

from ninja import ModelSchema, Schema
from pydantic import Field

from second_brain.apps.library.models import Source, TriageDecision
from second_brain.django_config.scaffold import RequestSchema

TriageVerdict = Literal["worthwhile", "review", "skip"]


class SourceOut(ModelSchema):
    class Meta:
        model = Source
        fields = [
            "file_path",
            "source_type",
            "status",
            "parse_lane",
            "raw_output",
            "ingested_at",
            "updated_at",
            "error",
            "origin_path",
        ]


class SourcePage(Schema):
    items: list[SourceOut]
    total: int
    limit: int
    offset: int


class TriageOut(ModelSchema):
    class Meta:
        model = TriageDecision
        fields = ["raw_path", "decision", "confidence", "reason", "triaged_at"]


class TriageUpdate(RequestSchema):
    raw_paths: list[str] = Field(min_length=1)
    decision: TriageVerdict


class SourceSelection(RequestSchema):
    raw_paths: list[str] = Field(min_length=1)


class SourceContent(Schema):
    raw_path: str
    markdown: str


class StagedSource(Schema):
    rel: str
    bytes: int
    costs: dict[str, float]
    defer_reason: str | None = None
    retryable: bool | None = None


class LibraryState(Schema):
    generated_at: datetime
    staged: list[StagedSource]
    built_count: int
    costs: dict[str, float]
    stale: bool
    needs_review: int
    skipped: int
    ingest_failures: dict[str, str]
    pending_triage: list[str]
