"""HTTP entry points for source identities, provenance, and triage decisions."""

from django.http import HttpRequest, StreamingHttpResponse
from ninja import Query, Router, Status

from second_brain.apps.library.desktop_schemas import DesktopState
from second_brain.apps.library.models import Source, TriageDecision
from second_brain.apps.library.schemas import (
    LibraryState,
    SourceContent,
    SourceListQuery,
    SourceOut,
    SourcePage,
    SourceSelection,
    TriageOut,
    TriageUpdate,
    TriageVerdict,
)
from second_brain.apps.library.services import (
    library_state,
    manual_triage,
    source_actions,
    source_files,
    source_queries,
)
from second_brain.apps.library.services.desktop_events import stream_desktop_state
from second_brain.apps.library.services.desktop_state import get_desktop_state
from second_brain.apps.preferences.services.user_settings import get_pipeline_config
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.schemas import ErrorResponse

router = Router(tags=["Library"])


@router.get("/desktop-state", response=DesktopState)
def desktop_state(request: HttpRequest) -> dict:
    """Read the same complete screen snapshot supplied by the event stream."""
    return get_desktop_state()


@router.get(
    "/events",
    response=None,
    openapi_extra={
        "responses": {
            200: {  # Match Ninja's integer response keys before JSON serialization.
                "description": "SSE DesktopState snapshots; comments are keepalives.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        }
    },
)
async def desktop_events(request: HttpRequest) -> StreamingHttpResponse:
    """Keep one authenticated HTTP connection open for desktop updates."""
    return StreamingHttpResponse(
        stream_desktop_state(get_vault_paths().data_dir),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/sources", response=SourcePage)
def list_sources(
    request: HttpRequest,
    filters: Query[SourceListQuery],
) -> dict[str, object]:
    """List source records with pagination and an optional ingestion-status filter."""
    items, total = source_queries.list_sources(
        status=filters.status,
        limit=filters.limit,
        offset=filters.offset,
    )
    return {"items": items, "total": total, "limit": filters.limit, "offset": filters.offset}


@router.get("/sources/detail", response={200: SourceOut, 404: ErrorResponse})
def get_source(request: HttpRequest, file_path: str = Query(..., min_length=1)) -> Source:
    """Read a source record by its stored input path."""
    return source_queries.get_source(file_path)


@router.get(
    "/sources/content",
    response={
        200: SourceContent,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        500: ErrorResponse,
    },
)
def source_content(
    request: HttpRequest, raw_path: str = Query(..., min_length=1)
) -> dict[str, str]:
    """Read a raw Markdown copy. Use the same relative path even when it is skipped."""
    return {
        "raw_path": raw_path,
        "markdown": source_files.read_content(get_vault_paths(), raw_path),
    }


@router.delete(
    "/sources", response={204: None, 400: ErrorResponse, 409: ErrorResponse, 500: ErrorResponse}
)
def delete_source(request: HttpRequest, raw_path: str = Query(..., min_length=1)) -> Status[None]:
    """Trash the vault copy and forget its records. Repeated deletion is a successful no-op."""
    source_actions.delete_source(get_vault_paths(), raw_path)
    return Status(204, None)


@router.post(
    "/sources/recompile",
    response={
        204: None,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        500: ErrorResponse,
    },
)
def stage_recompile(request: HttpRequest, payload: SourceSelection) -> Status[None]:
    """Make selected versions eligible for the next manual build; triage still applies."""
    source_actions.stage_recompile(get_vault_paths(), payload.raw_paths)
    return Status(204, None)


@router.get("/triage", response=list[TriageOut])
def list_triage(
    request: HttpRequest, decision: TriageVerdict | None = None
) -> list[TriageDecision]:
    """List recorded verdicts, including history for skipped chats already purged by a build."""
    return manual_triage.list_decisions(decision)


@router.patch(
    "/triage",
    response={
        200: list[TriageOut],
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        500: ErrorResponse,
    },
)
def update_triage(request: HttpRequest, payload: TriageUpdate) -> list[TriageDecision]:
    """Apply manual verdicts and skip/restore movement as one service operation."""
    return manual_triage.update_decisions(
        get_pipeline_config(), payload.raw_paths, payload.decision
    )


@router.get(
    "/state",
    response={
        200: LibraryState,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        500: ErrorResponse,
    },
)
def get_library_state(request: HttpRequest) -> dict:
    """Summarize current sources, staging, and build outcomes."""
    return library_state.get_state(get_pipeline_config())
