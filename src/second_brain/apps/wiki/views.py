"""HTTP entry points for wiki content, domains, history, and repair."""

from django.http import HttpRequest
from ninja import Query, Router, Status

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
from second_brain.apps.wiki.services import (
    domain_actions,
    git_history,
    page_maintenance,
    wiki_actions,
    wiki_queries,
)
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.schemas import ErrorResponse

router = Router(tags=["Wiki"])


@router.get(
    "/wiki/pages",
    response={200: list[WikiPage], 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def list_pages(request: HttpRequest, domain: str | None = None) -> list[dict]:
    """List wiki pages, optionally restricted to a domain."""
    return wiki_queries.list_pages(domain)


@router.get(
    "/wiki/content",
    response={200: WikiContent, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def read_page(request: HttpRequest, path: str) -> dict:
    """Read a wiki page and its Markdown content."""
    return wiki_queries.read_page(path)


@router.get(
    "/wiki/search",
    response={200: list[SearchHit], 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def search_pages(request: HttpRequest, q: str = Query(..., min_length=1)) -> list[dict]:
    """Search wiki content and return matching page snippets."""
    return wiki_queries.search_pages(q)


@router.get(
    "/wiki/health",
    response={200: WikiHealth, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def wiki_health(request: HttpRequest) -> dict:
    """Report broken links, disconnected pages, and possible duplicates."""
    return wiki_queries.wiki_health()


@router.get(
    "/domains",
    response={200: list[DomainOut], 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def list_domains(request: HttpRequest) -> list[domain_actions.DomainInfo]:
    """List domain tags and their page counts."""
    return domain_actions.list_domains(get_vault_paths().wiki_dir)


@router.post(
    "/domains/rename",
    response={200: ChangeResult, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def rename_domain(request: HttpRequest, payload: RenameDomain) -> dict:
    """Rename a domain in the topic schema and page metadata."""
    return {
        "changed_pages": wiki_actions.apply_wiki_edit(
            domain_actions.rename_domain, payload.old, payload.new
        )
    }


@router.post(
    "/domains/merge",
    response={200: ChangeResult, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def merge_domains(request: HttpRequest, payload: MergeRequest) -> dict:
    """Replace selected domain tags with a destination tag."""
    return {
        "changed_pages": wiki_actions.apply_wiki_edit(
            domain_actions.merge_domains, payload.sources, payload.dest
        )
    }


@router.delete(
    "/domains",
    response={200: ChangeResult, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def delete_domain(request: HttpRequest, name: str) -> dict:
    """Remove a domain tag from the schema and pages, preserving page content."""
    return {"changed_pages": wiki_actions.apply_wiki_edit(domain_actions.delete_domain, name)}


@router.get(
    "/wiki/history",
    response={200: list[WikiCommit], 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def wiki_history(
    request: HttpRequest,
    limit: int = Query(20, ge=1, le=200),
) -> list[git_history.WikiCommit]:
    """List recent wiki commits and their change counts."""
    return git_history.list_commits(get_vault_paths().wiki_dir, limit)


@router.post(
    "/wiki/rollback",
    response={200: RollbackResult, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def rollback_wiki(request: HttpRequest, payload: RollbackRequest) -> dict:
    """Revert recent wiki commits and restage sources for compilation."""
    return wiki_actions.rollback(payload.count)


@router.post(
    "/wiki/repair-links",
    response={200: list[PageRepair], 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def repair_links(
    request: HttpRequest, payload: RepairRequest
) -> list[page_maintenance.PageRepair]:
    """Repoint uniquely resolvable broken links, optionally without saving."""
    return wiki_actions.repair_links(payload.dry_run)


@router.post(
    "/wiki/merge",
    response={200: list[PageRepair], 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def merge_pages(request: HttpRequest, payload: MergeRequest) -> list[page_maintenance.PageRepair]:
    """Retire source pages, combine their metadata, and repoint inbound links."""
    return wiki_actions.apply_wiki_edit(
        page_maintenance.merge_pages, payload.dest, payload.sources
    )


@router.post(
    "/wiki/dismissed-duplicates",
    response={204: None, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def dismiss_duplicate(request: HttpRequest, payload: DuplicatePair) -> Status[None]:
    """Save a dismissal for a pair of possible duplicate pages."""
    wiki_actions.dismiss_duplicate(payload.page_a, payload.page_b)
    return Status(204, None)
