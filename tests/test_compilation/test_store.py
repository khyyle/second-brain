"""Tests for build records: compiled and set-aside sources."""

from __future__ import annotations

from second_brain.compilation.store import BuildStore, DeferredSource


def test_unmark_compiled_requeues_only_compiled_sources(build_store: BuildStore) -> None:
    """Unmarking clears only the compiled record, and reports how many it hit."""
    build_store.mark_compiled({"chatgpt/a.md": "hash-a", "chatgpt/b.md": "hash-b"})

    cleared = build_store.unmark_compiled(["chatgpt/a.md", "chatgpt/never-compiled.md"])

    assert cleared == 1
    assert build_store.get_compiled() == {"chatgpt/b.md": "hash-b"}


def test_unmark_compiled_empty_list_is_noop(build_store: BuildStore) -> None:
    assert build_store.unmark_compiled([]) == 0


def test_defer_sources_round_trip_and_release(build_store: BuildStore) -> None:
    build_store.defer_sources(
        {"papers/a.md": "hash-a", "papers/b.md": "hash-b"}, "did not finish", retryable=True
    )
    # Re-deferring updates the record instead of duplicating the row.
    build_store.defer_sources({"papers/a.md": "hash-a2"}, "too large", retryable=False)

    assert build_store.get_deferred() == {
        "papers/a.md": DeferredSource("too large", retryable=False, content_hash="hash-a2"),
        "papers/b.md": DeferredSource("did not finish", retryable=True, content_hash="hash-b"),
    }

    assert build_store.clear_deferred(["papers/a.md", "papers/missing.md"]) == 1
    assert set(build_store.get_deferred()) == {"papers/b.md"}
    assert build_store.clear_deferred([]) == 0


def test_compiling_again_records_the_newer_version(build_store: BuildStore) -> None:
    build_store.mark_compiled({"a.md": "hash-1", "b.md": "hash-1"})
    build_store.mark_compiled({"a.md": "hash-2"})

    assert build_store.get_compiled() == {"a.md": "hash-2", "b.md": "hash-1"}


def test_mark_compiled_empty_is_noop(build_store: BuildStore) -> None:
    build_store.mark_compiled({})
    assert build_store.get_compiled() == {}
