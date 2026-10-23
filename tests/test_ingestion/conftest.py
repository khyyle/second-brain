from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from second_brain.config import Config, SourceConfig
from second_brain.ingestion import pdf_handler


@pytest.fixture
def watched_config(config: Config, tmp_path: Path) -> Config:
    watched = tmp_path / "watched"
    watched.mkdir()
    return config.model_copy(
        update={
            "sources": {
                "documents": SourceConfig(
                    path=config.drops_dir / "documents", file_types=("md", "txt")
                ),
                "research": SourceConfig(path=watched, file_types=("md", "txt", "pdf", "json")),
            }
        }
    )


@pytest.fixture(autouse=True)
def _reset_pdf_parser_singletons() -> Generator[None, None, None]:
    """Ensure no parser singleton leaks across tests."""
    # setup a test by setting to None
    pdf_handler._chandra_parser = None
    pdf_handler._docling_parser = None

    yield  # pytest pauses here and then runs the test function

    # on teardown of a test set the globals to none in case they were mutated
    pdf_handler._chandra_parser = None
    pdf_handler._docling_parser = None


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    """Empty file standing in for a PDF"""
    p = tmp_path / "notebook.pdf"
    p.write_bytes(b"%PDF-1.4\nstub\n")
    return p
