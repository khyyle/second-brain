from pathlib import Path

import pytest
from django.utils import timezone

from second_brain.apps.library.models import Source
from second_brain.pipeline_config import PipelineConfig


@pytest.fixture
def raw_file(config: PipelineConfig):
    def create(rel: str, *, skipped: bool = False, text: str = "# Notes\n") -> Path:
        root = config.raw_dir / ".skipped" if skipped else config.raw_dir
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    return create


@pytest.fixture
def source_record():
    def create(rel: str, **fields) -> Source:
        values = {
            "file_path": f"/watched/{rel}",
            "raw_output": rel,
            "sha256": "hash",
            "source_type": rel.split("/", 1)[0],
            "status": "complete",
            "ingested_at": timezone.now(),
            "updated_at": timezone.now(),
        }
        values.update(fields)
        return Source.objects.create(**values)

    return create
