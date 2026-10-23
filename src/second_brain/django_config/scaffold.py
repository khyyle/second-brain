"""Explicit placeholder responses while feature endpoints are being implemented."""

from typing import NoReturn

from ninja import Schema
from ninja.errors import HttpError
from pydantic import ConfigDict


class RequestSchema(Schema):
    """Reject misspelled or unsupported input fields instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class ErrorResponse(Schema):
    detail: str


def pending(operation: str) -> NoReturn:
    raise HttpError(501, f"{operation} is scaffolded but not implemented. See the view's TODO.")
