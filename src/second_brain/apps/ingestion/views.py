"""HTTP entry points for imports, capture, parsing, and ingestion."""

from django.http import HttpRequest
from ninja import Router, Status

from second_brain.apps.ingestion.schemas import (
    ImportFile,
    ImportRequest,
    ImportSubmission,
    IngestRequest,
)
from second_brain.apps.ingestion.services import file_imports
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.schemas import JobOut
from second_brain.django_config.schemas import ErrorResponse

router = Router(tags=["Ingestion"])


@router.post(
    "/imports",
    response={202: ImportSubmission, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def import_files(
    request: HttpRequest, payload: ImportRequest
) -> Status[file_imports.ImportSubmission]:
    """Copy selected inputs into the vault and queue their ingestion."""
    return Status(202, file_imports.import_files(payload.paths, payload.source_type))


@router.post(
    "/ingestion/runs",
    response={202: JobOut, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def request_ingestion(request: HttpRequest, payload: IngestRequest) -> Status[Job]:
    """Queue a scan of captured inputs and optionally watched folders."""
    return Status(202, file_imports.request_ingestion(**payload.model_dump()))


@router.delete(
    "/imports", response={204: None, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse}
)
def delete_import(request: HttpRequest, file_path: str) -> Status[None]:
    """Remove a captured input and its associated source records."""
    file_imports.delete_import(file_path)
    return Status(204, None)


@router.get("/imports", response=list[ImportFile])
def list_imports(request: HttpRequest) -> list[file_imports.ImportedFile]:
    """List the import queue and recorded outcomes."""
    return file_imports.list_imports()
