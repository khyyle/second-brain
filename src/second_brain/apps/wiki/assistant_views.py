"""Backend entry point for the named, validated assistant tools."""

import inspect
from typing import Any, Literal

from django.http import HttpRequest
from ninja import Router
from pydantic import Field, ValidationError, validate_call

from second_brain.apps.jobs.services.job_queue import enqueue_job
from second_brain.apps.preferences.services.user_settings import get_pipeline_config
from second_brain.django_config.errors import DependencyError, ServiceError
from second_brain.django_config.schemas import ErrorResponse, RequestSchema
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import WikiTools
from second_brain.ollama.client import OllamaUnavailableError

router = Router(tags=["Assistant tools"])
ToolName = Literal[
    "search_wiki",
    "semantic_search",
    "read_page",
    "list_pages",
    "read_index",
    "capture_note",
    "get_sources",
    "get_sources_summary",
    "find_related",
    "prerequisite_closure",
    "dependents",
    "list_domains",
    "list_gaps",
]


class ToolRequest(RequestSchema):
    args: list[Any] = Field(default_factory=list, max_length=6)
    kwargs: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/assistant-tools/{name}", response={200: str, 400: ErrorResponse, 503: ErrorResponse}
)
def invoke_tool(request: HttpRequest, name: ToolName, payload: ToolRequest) -> str:
    """Validate arguments and invoke an allowlisted assistant tool.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    name: ToolName
        Allowlisted assistant tool name.
    payload: ToolRequest
        Positional and keyword arguments to validate against the tool signature.

    Returns
    -------
    str
        Tool output formatted as text.
    """
    config = get_pipeline_config()
    tools = WikiTools(
        config.wiki_dir,
        config.raw_dir,
        SearchIndex(config.search_db_path, config.search),
        ingest_trigger=lambda path: enqueue_job(
            "ingest", {"paths": [str(path)], "drops_only": True}
        ),
    )
    method = getattr(tools, name)
    try:
        bound = inspect.signature(method).bind(*payload.args, **payload.kwargs)
        for key, value in bound.arguments.items():
            if key in {"limit", "pages_per_domain"} and (
                not isinstance(value, int) or not 1 <= value <= 200
            ):
                raise ServiceError("Limits must be between 1 and 200.")
            if key in {"depth", "max_depth"} and (
                not isinstance(value, int) or not 1 <= value <= 10
            ):
                raise ServiceError("Depth must be between 1 and 10.")
            if key == "offset" and (not isinstance(value, int) or value < 0):
                raise ServiceError("Offset must be nonnegative.")
        return validate_call(method)(*payload.args, **payload.kwargs)
    except (ValidationError, TypeError) as exc:
        raise ServiceError("Invalid arguments for this assistant tool.") from exc
    except OllamaUnavailableError as exc:
        raise DependencyError(
            "Ollama is unavailable. Use keyword search until it is running."
        ) from exc
