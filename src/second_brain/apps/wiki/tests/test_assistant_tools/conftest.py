from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from second_brain.apps.inference.catalog import EMBEDDING_MODEL
from second_brain.apps.wiki.services.assistant_tools import WikiTools
from second_brain.apps.wiki.services.search_index import SearchIndex


def padded_vector(*coordinates: float) -> NDArray[np.float32]:
    """Preserve toy cosine distances in catalog-sized vec0 rows."""
    vector = np.zeros(EMBEDDING_MODEL.dimensions, dtype=np.float32)
    vector[: len(coordinates)] = coordinates
    return vector


@pytest.fixture
def search_index(tmp_path: Path) -> SearchIndex:
    """Fresh SearchIndex in a temporary directory."""
    return SearchIndex(tmp_path / "search.db")


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
    ingest_trigger: Callable[[Path], None] | None = None,
) -> WikiToolsHarness:
    """Construct an isolated WikiTools sandbox with raw and wiki directories."""
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    wiki_dir = data_dir / "wiki"
    raw_dir.mkdir(parents=True, exist_ok=True)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    index = SearchIndex(data_dir / "search.db")
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
