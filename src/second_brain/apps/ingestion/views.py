"""HTTP entry points for imports, capture, parsing, and ingestion."""

from django.http import HttpRequest
from ninja import Router

from second_brain.apps.ingestion.schemas import ImportRequest, IngestRequest
from second_brain.apps.jobs.schemas import JobOut
from second_brain.django_config.scaffold import ErrorResponse, pending

router = Router(tags=["Ingestion"])


@router.post("/imports", response={202: JobOut, 501: ErrorResponse})
def import_files(request: HttpRequest, payload: ImportRequest) -> tuple[int, JobOut]:
    """TODO: Port DropStaging and cli.ingest's --chatgpt/--path branches into a service.

    Validate local files, copy into drops, deduplicate, create an ingest Job. Workers
    call services.dispatch.ingest_file or services.chatgpt_parser.process_chatgpt_export.
    Return (202, job) after enqueueing; no OCR or model calls in the HTTP request.
    """
    pending("Import files")


@router.post("/ingestion/runs", response={202: JobOut, 501: ErrorResponse})
def request_ingestion(request: HttpRequest, payload: IngestRequest) -> tuple[int, JobOut]:
    """TODO: Create an ingest Job; move cli.ingest orchestration into a worker service.

    Reuse services.watcher.batch_scan, services.intake and services.triage.pipeline.
    Supplying file_path is an explicit retry; omitted means scan pending inputs.
    """
    pending("Request ingestion")


@router.delete("/imports", response={204: None, 501: ErrorResponse})
def delete_import(request: HttpRequest, file_path: str) -> None:
    """TODO: Port cli.forget_drop plus ManifestMutator.removeDroppedFile.

    Only remove vault-owned drops and records; reject active jobs and external paths.
    """
    pending("Delete queued import")
