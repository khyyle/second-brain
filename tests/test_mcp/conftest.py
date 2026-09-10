from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from second_brain.config import SearchConfig
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import WikiTools


@pytest.fixture
def semantic_config() -> SearchConfig:
    """3-dimensional semantic search config for deterministic testing."""
    return SearchConfig(embedding_dimensions=3, semantic_enabled=True)


@pytest.fixture
def search_index(tmp_path: Path, semantic_config: SearchConfig) -> SearchIndex:
    """Fresh SearchIndex with 3-d vectors in a temporary directory."""
    return SearchIndex(tmp_path / "search.db", semantic_config)


@dataclass(frozen=True)
class WikiToolsHarness:
    """Test harness packaging WikiTools and its backing directories and index."""

    tools: WikiTools
    data_dir: Path
    wiki_dir: Path
    raw_dir: Path
    index: SearchIndex
    ingested: list[Path]


def make_wiki_tools(
    tmp_path: Path,
    *,
    semantic: bool = False,
    ingest_trigger: Callable[[Path], None] | None = None,
) -> WikiToolsHarness:
    """Construct an isolated WikiTools sandbox with raw and wiki directories."""
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    wiki_dir = data_dir / "wiki"
    raw_dir.mkdir(parents=True, exist_ok=True)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    index = SearchIndex(
        data_dir / "search.db",
        SearchConfig(embedding_dimensions=3, semantic_enabled=semantic),
    )
    ingested: list[Path] = []
    trigger = ingest_trigger or ingested.append
    tools = WikiTools(wiki_dir, raw_dir, index, ingest_trigger=trigger)
    return WikiToolsHarness(
        tools=tools,
        data_dir=data_dir,
        wiki_dir=wiki_dir,
        raw_dir=raw_dir,
        index=index,
        ingested=ingested,
    )


@pytest.fixture
def wiki_harness(tmp_path: Path) -> WikiToolsHarness:
    """Fixture providing a clean WikiToolsHarness."""
    return make_wiki_tools(tmp_path)
