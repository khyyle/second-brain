"""HTTP entry points for source identities, provenance, and triage decisions."""

from django.http import HttpRequest
from ninja import Query, Router

from second_brain.apps.library.schemas import (
    LibraryState,
    SourceContent,
    SourceOut,
    SourcePage,
    SourceSelection,
    TriageOut,
    TriageUpdate,
    TriageVerdict,
)
from second_brain.django_config.scaffold import ErrorResponse, pending

router = Router(tags=["Library"])


@router.get("/sources", response={200: SourcePage, 501: ErrorResponse})
def list_sources(
    request: HttpRequest,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> SourcePage:
    """List imported sources with pagination.

    TODO: Query Source, filter status, order by -updated_at/file_path, count and slice.
    Replaces ManifestReader.recent/statusByPath. Serialize with SourceOut.
    """
    pending("List sources")


@router.get("/sources/detail", response={200: SourceOut, 501: ErrorResponse})
def get_source(request: HttpRequest, file_path: str) -> SourceOut:
    """TODO: Look up Source by its file_path primary key; return 404 if missing."""
    pending("Read source")


@router.get("/sources/content", response={200: SourceContent, 501: ErrorResponse})
def source_content(request: HttpRequest, raw_path: str) -> SourceContent:
    """TODO: Read raw/ or raw/.skipped/ Markdown after resolving within the vault.

    Reject traversal and symlinks escaping the raw tree. Replaces GUI file reads.
    """
    pending("Read source content")


@router.delete("/sources", response={204: None, 501: ErrorResponse})
def delete_source(request: HttpRequest, raw_path: str) -> None:
    """TODO: Combine services.sources.forget_source with the GUI's raw-file removal.

    Validate containment; delete only the vault copy, never a watched original.
    Convert coordinated record deletion to a short transaction.atomic() block.
    """
    pending("Delete source")


@router.post("/sources/recompile", response={204: None, 501: ErrorResponse})
def stage_recompile(request: HttpRequest, payload: SourceSelection) -> None:
    """TODO: Port cli.recompile: clear selected build/defer records, leaving raw intact.

    This stages sources for the next manual build; it does not start one.
    """
    pending("Stage sources for recompilation")


@router.get("/triage", response={200: list[TriageOut], 501: ErrorResponse})
def list_triage(request: HttpRequest, decision: TriageVerdict | None = None) -> list[TriageOut]:
    """TODO: Query TriageDecision; filter verdict; order by -triaged_at/raw_path."""
    pending("List triage decisions")


@router.patch("/triage", response={200: list[TriageOut], 501: ErrorResponse})
def update_triage(request: HttpRequest, payload: TriageUpdate) -> list[TriageOut]:
    """TODO: Port cli.triage_set and ManifestMutator's skip/restore file movement.

    TriageStore.record is the existing DB operation. Only chats need this step.
    """
    pending("Update triage decisions")


@router.get("/state", response={200: LibraryState, 501: ErrorResponse})
def library_state(request: HttpRequest) -> LibraryState:
    """TODO: Adapt jobs.services.state.compute_state using the selected vault's config.

    Return its data directly; HTTP callers need not read a .state.json file.
    """
    pending("Read library state")
