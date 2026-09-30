"""Tests for the skipped-source holding folder."""

from __future__ import annotations

from second_brain.config import Config
from second_brain.triage.skipped import move_skips_to_holding, purge_skipped


def test_purge_skipped_removes_folder(config: Config) -> None:
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")

    purge_skipped(config.raw_dir)

    assert not (config.raw_dir / ".skipped").exists()


def test_purge_skipped_noop_when_absent(config: Config) -> None:
    purge_skipped(config.raw_dir)  # must not raise


def test_move_skips_to_holding_moves_only_skips(config: Config) -> None:
    raw = config.raw_dir
    for rel in ("chatgpt/skip-me.md", "chatgpt/keep.md", "chatgpt/review-me.md"):
        path = raw / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("body", encoding="utf-8")

    decisions = {
        "chatgpt/skip-me.md": "skip",
        "chatgpt/keep.md": "worthwhile",
        "chatgpt/review-me.md": "review",
        "chatgpt/already-gone.md": "skip",
    }

    moved = move_skips_to_holding(raw, decisions)

    assert moved == 1
    assert (raw / ".skipped" / "chatgpt" / "skip-me.md").is_file()
    assert not (raw / "chatgpt" / "skip-me.md").exists()
    assert (raw / "chatgpt" / "keep.md").is_file()
    assert (raw / "chatgpt" / "review-me.md").is_file()
