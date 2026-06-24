"""Tests for bulk domain edits — list, rename, merge, delete."""

from __future__ import annotations

from pathlib import Path

import yaml

from second_brain.wiki.domain_ops import (
    delete_domain,
    list_domains,
    merge_domains,
    rename_domain,
)
from second_brain.wiki.schema import register_domains, write_default_schema


def _wiki(tmp_path: Path) -> Path:
    write_default_schema(tmp_path)
    return tmp_path


def _write_page(
    wiki: Path,
    content_dir: str,
    stem: str,
    domains: list[str],
    body: str = "Body with a [[link]].",
) -> Path:
    frontmatter = {"title": stem.replace("-", " ").title(), "type": "concept", "domains": domains}
    path = wiki / content_dir / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n\n" + body + "\n",
        encoding="utf-8",
    )
    return path


def _page_domains(path: Path) -> list[str]:
    frontmatter = yaml.safe_load(path.read_text().split("---", 2)[1])
    return frontmatter.get("domains") or []


def _schema_domains(wiki: Path) -> set[str]:
    raw = yaml.safe_load((wiki / "_meta" / "topic_schema.yaml").read_text())
    return set(raw.get("domains") or {})


def _domain_view(wiki: Path, name: str) -> Path:
    return wiki / "_views" / "domains" / f"{name}.md"


def test_list_counts_pages_and_includes_empty_registered(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    _write_page(wiki, "concepts", "vectors", ["math", "physics"])
    _write_page(wiki, "problems", "drill", ["math"])
    register_domains(wiki, {"math", "physics", "history"})  # history registered but unused

    infos = {d.name: d for d in list_domains(wiki)}

    assert infos["math"].page_count == 2
    assert infos["physics"].page_count == 1
    assert infos["history"].page_count == 0
    assert infos["history"].in_schema is True


def test_list_surfaces_unregistered_used_domain(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    _write_page(wiki, "concepts", "rogue", ["uncharted"])

    infos = {d.name: d for d in list_domains(wiki)}

    assert infos["uncharted"].page_count == 1
    assert infos["uncharted"].in_schema is False


def test_rename_updates_pages_schema_and_views(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    page_a = _write_page(wiki, "concepts", "vectors", ["math", "physics"])
    page_b = _write_page(wiki, "problems", "drill", ["math"])
    register_domains(wiki, {"math", "physics"})

    changed = rename_domain(wiki, "math", "mathematics")

    assert changed == 2
    assert _page_domains(page_a) == ["mathematics", "physics"]
    assert _page_domains(page_b) == ["mathematics"]
    assert "math" not in _schema_domains(wiki)
    assert "mathematics" in _schema_domains(wiki)
    assert not _domain_view(wiki, "math").exists()
    assert _domain_view(wiki, "mathematics").exists()


def test_merge_dedupes_when_target_already_present(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    page = _write_page(wiki, "concepts", "vectors", ["math", "physics"])
    register_domains(wiki, {"math", "physics"})

    changed = merge_domains(wiki, ["physics"], "math")

    assert changed == 1
    assert _page_domains(page) == ["math"]
    assert "physics" not in _schema_domains(wiki)
    assert "math" in _schema_domains(wiki)
    assert not _domain_view(wiki, "physics").exists()


def test_delete_leaves_page_without_domains(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    page = _write_page(wiki, "concepts", "lonely", ["math"])
    register_domains(wiki, {"math"})

    changed = delete_domain(wiki, "math")

    assert changed == 1
    assert _page_domains(page) == []
    assert page.exists()
    assert "math" not in _schema_domains(wiki)
    assert not _domain_view(wiki, "math").exists()


def test_rename_missing_domain_is_noop(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    page = _write_page(wiki, "concepts", "vectors", ["math"])
    register_domains(wiki, {"math"})

    changed = rename_domain(wiki, "nonexistent", "whatever")

    assert changed == 0
    assert _page_domains(page) == ["math"]


def test_rewrite_preserves_body(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    body = "Body with $x^2$ and a [[related-page]] link.\n\nSecond paragraph."
    page = _write_page(wiki, "concepts", "vectors", ["math"], body=body)
    register_domains(wiki, {"math"})

    rename_domain(wiki, "math", "mathematics")

    assert body in page.read_text()
    assert _page_domains(page) == ["mathematics"]
