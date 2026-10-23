"""HTTP entry points for wiki content, domains, history, and repair."""

from django.http import HttpRequest
from ninja import Query, Router

from second_brain.apps.wiki.schemas import (
    ChangeResult,
    DomainOut,
    DuplicatePair,
    MergeRequest,
    PageRepair,
    RenameDomain,
    RepairRequest,
    RollbackRequest,
    RollbackResult,
    SearchHit,
    WikiCommit,
    WikiContent,
    WikiHealth,
    WikiPage,
)
from second_brain.django_config.scaffold import ErrorResponse, pending

router = Router(tags=["Wiki"])


@router.get("/wiki/pages", response={200: list[WikiPage], 501: ErrorResponse})
def list_pages(request: HttpRequest, domain: str | None = None) -> list[WikiPage]:
    """TODO: Adapt services.structure.discover_all_pages and frontmatter parsing."""
    pending("List wiki pages")


@router.get("/wiki/content", response={200: WikiContent, 501: ErrorResponse})
def read_page(request: HttpRequest, path: str) -> WikiContent:
    """TODO: Read Markdown after resolving path inside wiki/; reject escaping symlinks.

    Reuse mcp_server.tools page-reading logic; return 404 for an absent page.
    """
    pending("Read wiki page")


@router.get("/wiki/search", response={200: list[SearchHit], 501: ErrorResponse})
def search_pages(request: HttpRequest, q: str = Query(..., min_length=1)) -> list[SearchHit]:
    """TODO: Adapt cli.query / mcp_server.search.SearchIndex; serialize hits explicitly.

    Keep index updates in worker operations instead of rebuilding on every request.
    """
    pending("Search wiki")


@router.get("/wiki/health", response={200: WikiHealth, 501: ErrorResponse})
def wiki_health(request: HttpRequest) -> WikiHealth:
    """TODO: Adapt cli.health and services.health.run_health_check.

    Use DismissedDuplicate ORM records to filter near_duplicate_pairs from SearchIndex.
    """
    pending("Read wiki health")


@router.get("/domains", response={200: list[DomainOut], 501: ErrorResponse})
def list_domains(request: HttpRequest) -> list[DomainOut]:
    """TODO: Call services.domain_ops.list_domains with the selected vault's wiki_dir."""
    pending("List domains")


@router.post("/domains/rename", response={200: ChangeResult, 501: ErrorResponse})
def rename_domain(request: HttpRequest, payload: RenameDomain) -> ChangeResult:
    """TODO: Adapt cli.domain_rename / services.domain_ops.rename_domain.

    Coordinate wiki mutations with builds; return 409 while another writer owns it.
    """
    pending("Rename domain")


@router.post("/domains/merge", response={200: ChangeResult, 501: ErrorResponse})
def merge_domains(request: HttpRequest, payload: MergeRequest) -> ChangeResult:
    """TODO: Adapt cli.domain_merge / services.domain_ops.merge_domains; coordinate writers."""
    pending("Merge domains")


@router.delete("/domains", response={200: ChangeResult, 501: ErrorResponse})
def delete_domain(request: HttpRequest, name: str) -> ChangeResult:
    """TODO: Adapt services.domain_ops.delete_domain; removes tags, not source/page content."""
    pending("Delete domain")


@router.get("/wiki/history", response={200: list[WikiCommit], 501: ErrorResponse})
def wiki_history(
    request: HttpRequest,
    limit: int = Query(20, ge=1, le=200),
) -> list[WikiCommit]:
    """TODO: Serialize services.repo.list_commits; Git remains the history store."""
    pending("Read wiki history")


@router.post("/wiki/rollback", response={200: RollbackResult, 501: ErrorResponse})
def rollback_wiki(request: HttpRequest, payload: RollbackRequest) -> RollbackResult:
    """TODO: Adapt cli.wiki_rollback / services.repo.rollback, then refresh derived state/index.

    Require exclusive wiki ownership and explicit GUI confirmation before this request.
    """
    pending("Roll back wiki")


@router.post("/wiki/repair-links", response={200: list[PageRepair], 501: ErrorResponse})
def repair_links(request: HttpRequest, payload: RepairRequest) -> list[PageRepair]:
    """TODO: Adapt cli.wiki_repair_links / services.repo.repair_links, honoring dry_run."""
    pending("Repair wiki links")


@router.post("/wiki/merge", response={200: list[PageRepair], 501: ErrorResponse})
def merge_pages(request: HttpRequest, payload: MergeRequest) -> list[PageRepair]:
    """TODO: Adapt cli.wiki_merge / services.repo.merge_pages; coordinate wiki writes."""
    pending("Merge wiki pages")


@router.post("/wiki/dismissed-duplicates", response={204: None, 501: ErrorResponse})
def dismiss_duplicate(request: HttpRequest, payload: DuplicatePair) -> None:
    """TODO: Sort the two stems and get_or_create DismissedDuplicate with a timestamp.

    Matches services.store.DuplicateStore.dismiss and cli.wiki_dismiss.
    """
    pending("Dismiss duplicate suggestion")
