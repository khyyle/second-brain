"""Read wiki Markdown and derived search data through a contained vault boundary."""

from dataclasses import asdict
from pathlib import Path

from second_brain.apps.library.services.source_files import checked_path
from second_brain.apps.preferences.services.user_settings import get_pipeline_config
from second_brain.apps.wiki.services import health, wiki_structure
from second_brain.apps.wiki.services.duplicate_dismissals import DuplicateStore
from second_brain.django_config.errors import NotFoundError, ServiceError
from second_brain.django_config.paths import get_vault_paths
from second_brain.mcp_server.search import SearchIndex
from second_brain.pipeline_config import PipelineConfig


def _page_info(path: str, frontmatter: dict) -> dict:
    domains = frontmatter.get("domains", [])
    return {
        "path": path,
        "title": str(frontmatter.get("title") or Path(path).stem),
        "content_type": str(frontmatter.get("type") or ""),
        "domains": [str(domain) for domain in domains] if isinstance(domains, list) else [],
    }


def list_pages(domain: str | None = None) -> list[dict]:
    """List content pages, optionally filtered by domain; omit generated metadata pages.

    Parameters
    ----------
    domain: str | None
        Optional domain name to require in page metadata.

    Returns
    -------
    list[dict]
        Page paths, titles, content types, and domains, ordered by relative path.
    """
    pages = wiki_structure.discover_all_pages(get_vault_paths().wiki_dir)
    items = [_page_info(page.rel_path, page.frontmatter) for page in pages.values()]
    return sorted(
        [item for item in items if domain is None or domain in item["domains"]],
        key=lambda item: item["path"],
    )


def read_page(relative: str) -> dict:
    """Return one contained Markdown page and parsed metadata.

    Parameters
    ----------
    relative: str
        Path relative to wiki/, including .md. Traversal and symlinks are rejected.

    Returns
    -------
    dict
        Public path, title, content type, domains, and complete Markdown.

    Raises
    ------
    ServiceError
        Not a visible Markdown path inside the wiki.
    NotFoundError
        The page is absent.
    """
    path = Path(relative)
    if path.is_absolute() or path.suffix != ".md" or any(p.startswith(".") for p in path.parts):
        raise ServiceError("Use a relative Markdown path inside wiki/.")
    target = checked_path(get_vault_paths().data_dir, "wiki", relative)
    if not target.is_file():
        raise NotFoundError("This wiki page does not exist.")
    markdown = target.read_text(encoding="utf-8")
    return {
        **_page_info(relative, wiki_structure._parse_frontmatter(markdown)),
        "markdown": markdown,
    }


def search_pages(query: str) -> list[dict]:
    """Search the existing keyword index without rebuilding it or requesting embeddings.

    Parameters
    ----------
    query: str
        Keyword search text.

    Returns
    -------
    list[dict]
        Matching page metadata and snippets. Missing indexes return an empty list; stale
        hits without a wiki page are omitted.
    """
    paths = get_vault_paths()
    if not paths.search_db_path.exists():
        return []
    hits = SearchIndex(paths.search_db_path).search(query)
    pages = wiki_structure.discover_all_pages(paths.wiki_dir)
    return [
        {
            **_page_info(pages[hit.stem].rel_path, pages[hit.stem].frontmatter),
            "snippet": hit.snippet,
        }
        for hit in hits
        if hit.stem in pages
    ]


def wiki_health() -> dict:
    """Compute content checks and filter duplicate suggestions dismissed by the user.

    Returns
    -------
    dict
        Health findings, including undismissed near-duplicate pairs when a search index
        exists.
    """
    config = get_pipeline_config()
    duplicates = []
    if config.search_db_path.exists():
        dismissed = DuplicateStore().get_dismissed()
        duplicates = [
            pair
            for pair in SearchIndex(config.search_db_path, config.search).near_duplicate_pairs()
            if tuple(sorted(pair[:2])) not in dismissed
        ]
    return asdict(health.run_health_check(config.wiki_dir, duplicates))


def refresh_index(config: PipelineConfig) -> None:
    """Reconcile keyword and link indexes, then queue missing embedding work if enabled.

    Does not request embeddings directly. Leaves an existing queued index job in place.

    Parameters
    ----------
    config: PipelineConfig
        Wiki and index locations plus search settings.

    Returns
    -------
    None
    """
    SearchIndex(config.search_db_path, config.search).sync_from_wiki(config.wiki_dir)
    from second_brain.apps.jobs.models import Job
    from second_brain.apps.jobs.services.job_queue import enqueue_job

    if (
        config.search.semantic_enabled
        and not Job.objects.filter(kind="index", status="queued").exists()
    ):
        enqueue_job("index", {})
