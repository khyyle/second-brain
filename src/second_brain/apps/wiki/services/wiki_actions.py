"""Coordinate wiki writers and keep Git, build eligibility, and search in agreement."""

from collections.abc import Callable
from typing import Any

from second_brain.apps.compilation.services.build_records import BuildStore
from second_brain.apps.preferences.services.user_settings import get_pipeline_config
from second_brain.apps.wiki.services import git_history, page_maintenance, wiki_structure
from second_brain.apps.wiki.services.duplicate_dismissals import DuplicateStore
from second_brain.apps.wiki.services.wiki_queries import refresh_index
from second_brain.django_config.errors import ServiceError
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.vault_storage import vault_lock


def apply_wiki_edit(operation: Callable[..., Any], *args: Any) -> Any:
    """Run a wiki edit exclusively, rebuild navigation/search, and checkpoint in Git.

    Parameters
    ----------
    operation: Callable
        Domain service taking wiki_dir followed by validated operation arguments.
    args
        Domain names or page stems. Never arbitrary filesystem destinations.

    Returns
    -------
    object
        The domain operation's result, unchanged for the view to serialize.

    Raises
    ------
    ConflictError
        A build or another wiki edit currently owns the wiki.
    """
    config = get_pipeline_config()
    with vault_lock("wiki", blocking=False):
        result = operation(config.wiki_dir, *args)
        wiki_structure.rebuild_structure(config.wiki_dir)
        refresh_index(config)
        git_history.commit_all(config.wiki_dir, "manual: wiki edit")
        return result


def rollback(count: int) -> dict:
    """Roll back commits under exclusive ownership and release affected build records."""
    config = get_pipeline_config()
    with vault_lock("wiki", blocking=False):
        commits = git_history.list_commits(config.wiki_dir, count)
        sources = git_history.rollback(config.wiki_dir, count)
        raw_paths = [source.removeprefix("raw/") for source in sources]
        BuildStore().unmark_compiled(raw_paths)
        BuildStore().clear_deferred(raw_paths)
        refresh_index(config)
        return {"reverted_commits": [commit.short_hash for commit in commits]}


def repair_links(dry_run: bool) -> list:
    """Detect renamed pages and preview or commit their repaired links under one lock."""
    config = get_pipeline_config()
    with vault_lock("wiki", blocking=False):
        mapping = git_history.detect_renames(config.wiki_dir)
        repairs = page_maintenance.repair_links(config.wiki_dir, mapping, dry_run=dry_run)
        if not dry_run:
            wiki_structure.rebuild_structure(config.wiki_dir)
            refresh_index(config)
            git_history.commit_all(config.wiki_dir, "manual: repair renamed links")
        return repairs


def dismiss_duplicate(page_a: str, page_b: str) -> None:
    """Persist a symmetric dismissed pair after checking both page identities."""
    pages = wiki_structure.discover_all_pages(get_vault_paths().wiki_dir)
    if page_a == page_b or page_a not in pages or page_b not in pages:
        raise ServiceError("Choose two different existing page stems.")
    DuplicateStore().dismiss(page_a, page_b)
