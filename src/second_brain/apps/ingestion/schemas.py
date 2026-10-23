"""Local import requests; the backend copies selected files into the vault."""

from typing import Literal

from pydantic import Field

from second_brain.django_config.scaffold import RequestSchema


class ImportRequest(RequestSchema):
    paths: list[str] = Field(min_length=1)
    source_type: Literal["documents", "chatgpt"] = "documents"


class IngestRequest(RequestSchema):
    drops_only: bool = False
    file_path: str | None = None
