"""Tests for ChatGPT export parsing and its rejection of non-exports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from second_brain.ingestion.chatgpt_parser import process_chatgpt_export


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _conversation(title: str, text: str) -> dict:
    """A minimal conversation in the OpenAI export shape."""
    return {
        "title": title,
        "id": "abc12345",
        "create_time": 1_700_000_000,
        "mapping": {
            "node-1": {
                "message": {
                    "author": {"role": "user"},
                    "create_time": 1_700_000_000,
                    "content": {"parts": [text]},
                }
            }
        },
    }


def test_parses_a_valid_export(tmp_path: Path) -> None:
    export = _write_json(tmp_path / "conversations.json", [_conversation("Vectors", "hi")])
    out_dir = tmp_path / "out"

    paths = process_chatgpt_export(export, out_dir)

    assert len(paths) == 1
    assert paths[0].suffix == ".md"
    assert "Vectors" in paths[0].read_text(encoding="utf-8")


def test_non_export_json_is_rejected(tmp_path: Path) -> None:
    """A JSON array that isn't conversations should fail, not no-op silently."""
    export = _write_json(tmp_path / "conversations.json", [{"foo": "bar"}, {"baz": 1}])

    with pytest.raises(ValueError, match="ChatGPT"):
        process_chatgpt_export(export, tmp_path / "out")


def test_json_object_is_rejected(tmp_path: Path) -> None:
    export = _write_json(tmp_path / "conversations.json", {"not": "an array"})

    with pytest.raises(ValueError):
        process_chatgpt_export(export, tmp_path / "out")


def test_array_of_non_objects_is_rejected(tmp_path: Path) -> None:
    """Non-dict entries must not crash; they yield no conversations -> error."""
    export = _write_json(tmp_path / "conversations.json", [1, 2, 3])

    with pytest.raises(ValueError, match="ChatGPT"):
        process_chatgpt_export(export, tmp_path / "out")
