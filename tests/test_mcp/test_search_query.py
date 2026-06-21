"""Tests for FTS query handling: operators, sanitization, and resilience."""

from __future__ import annotations

from pathlib import Path

from second_brain.config import SearchConfig
from second_brain.mcp_server.search import SearchIndex, _sanitize_fts_query


def _index(tmp_path: Path) -> SearchIndex:
    index = SearchIndex(tmp_path / "s.db", SearchConfig(semantic_enabled=False))
    index.index_page(
        stem="central-limit-theorem",
        title="Central Limit Theorem",
        content="the sampling distribution of the mean tends to normal",
        content_type="concept",
        domains=["stats"],
        tags=["probability"],
        word_count=9,
        path="concepts/central-limit-theorem.md",
    )
    return index


def test_sanitize_quotes_terms_and_drops_punctuation() -> None:
    assert _sanitize_fts_query('the "best') == '"the" "best"'
    assert _sanitize_fts_query("a-b c") == '"a" "b" "c"'
    assert _sanitize_fts_query("   ") == ""


def test_plain_text_query_matches(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert any(h.stem == "central-limit-theorem" for h in index.search("sampling mean"))


def test_invalid_fts_syntax_is_recovered(tmp_path: Path) -> None:
    index = _index(tmp_path)
    # An unbalanced quote is invalid FTS5; sanitization should recover it
    # rather than raising or silently returning nothing for a real match.
    hits = index.search('sampling "mean')
    assert any(h.stem == "central-limit-theorem" for h in hits)


def test_unmatchable_query_returns_empty(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert index.search("!!!") == []
    assert index.search("nonexistentterm") == []


def test_fts_operators_still_work(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert any(h.stem == "central-limit-theorem" for h in index.search("sampling OR nope"))
