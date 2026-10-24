"""Local import requests; the backend copies selected files into the vault."""

from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field

from second_brain.apps.jobs.schemas import JobOut
from second_brain.django_config.schemas import RequestSchema


class ImportRequest(RequestSchema):
    paths: list[str] = Field(min_length=1)
    source_type: Literal["documents", "chatgpt", "auto"] = "documents"


class IngestRequest(RequestSchema):
    drops_only: bool = False
    file_path: str | None = None


class ImportFile(Schema):
    file_path: str
    status: str
    updated_at: datetime
    error: str | None = None


class ImportSubmission(Schema):
    job: JobOut
    files: list[ImportFile]
