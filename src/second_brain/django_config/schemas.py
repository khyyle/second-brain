"""Shared HTTP shapes"""

from ninja import Schema
from pydantic import ConfigDict


class RequestSchema(Schema):
    """Reject unsupported input fields instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class ErrorResponse(Schema):
    """Public JSON body for expected service failures."""

    detail: str
