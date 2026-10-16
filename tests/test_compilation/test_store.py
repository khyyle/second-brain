"""Tests for build records: compiled and set-aside sources."""

from __future__ import annotations

from second_brain.compilation.store import BuildStore


def test_unmark_compiled_requeues_only_compiled_sources(build_store: BuildStore) -> None:
    """Unmarking clears only the compiled record, and reports how many it hit."""
    build_store.mark_compiled(["chatgpt/a.md", "chatgpt/b.md"])

    cleared = build_store.unmark_compiled(["chatgpt/a.md", "chatgpt/never-compiled.md"])

    assert cleared == 1
    assert build_store.get_compiled_raw_paths() == {"chatgpt/b.md"}


def test_unmark_compiled_empty_list_is_noop(build_store: BuildStore) -> None:
    assert build_store.unmark_compiled([]) == 0


def test_defer_sources_round_trip_and_release(build_store: BuildStore) -> None:
    build_store.defer_sources(["papers/a.md", "papers/b.md"], "did not finish", retryable=True)
    # Re-deferring updates the reason instead of duplicating the row.
    build_store.defer_sources(["papers/a.md"], "too large", retryable=False)

    assert build_store.get_deferred_sources() == {
        "papers/a.md": "too large",
        "papers/b.md": "did not finish",
    }
    assert build_store.get_unretryable_sources() == {"papers/a.md"}

    assert build_store.clear_deferred(["papers/a.md", "papers/missing.md"]) == 1
    assert build_store.get_deferred_sources() == {"papers/b.md": "did not finish"}
    assert build_store.get_unretryable_sources() == set()
    assert build_store.clear_deferred([]) == 0


def test_mark_compiled_is_idempotent(build_store: BuildStore) -> None:
    build_store.mark_compiled(["a.md", "b.md"])
    build_store.mark_compiled(["a.md"])
    assert build_store.get_compiled_raw_paths() == {"a.md", "b.md"}


def test_mark_compiled_empty_is_noop(build_store: BuildStore) -> None:
    build_store.mark_compiled([])
    assert build_store.get_compiled_raw_paths() == set()
