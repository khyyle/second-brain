"""Topic schema management — the wiki's content types and domain vocabulary."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_SCHEMA_HEADER = (
    "# Topic Schema — the compilation agent MUST read this before writing.\n"
    "# Domains live in frontmatter metadata, NOT in folder paths.\n"
    "# Folders are typed buckets (concepts/, problems/, projects/, insights/).\n\n"
)

DEFAULT_SCHEMA: dict = {
    "content_types": {
        "concept": {
            "description": "What things ARE — definitions, theory, mathematical relationships",
            "directory": "concepts/",
            "frontmatter": [
                "title",
                "type",
                "domains",
                "tags",
                "prerequisites",
                "related",
                "sources",
            ],
        },
        "problem": {
            "description": "Practice problems, exercises, drills, worked examples",
            "directory": "problems/",
            "frontmatter": [
                "title",
                "type",
                "domains",
                "tags",
                "difficulty",
                "concepts_tested",
                "sources",
            ],
        },
        "project": {
            "description": "Things you BUILD — systems, experiments, applications",
            "directory": "projects/",
            "frontmatter": [
                "title",
                "type",
                "domains",
                "tags",
                "status",
                "concepts_used",
                "sources",
            ],
        },
        "insight": {
            "description": "Distilled knowledge from conversations, lectures, readings",
            "directory": "insights/",
            "frontmatter": [
                "title",
                "type",
                "domains",
                "tags",
                "key_takeaways",
                "sources",
            ],
        },
    },
    # Empty by default so the agent grows this vocab from the user's content over time
    "domains": {},
    "page_rules": {
        "target_length": "500-3000 words",
        "split_threshold": 4000,
        "merge_threshold": 150,
        "naming": "kebab-case",
        "multi_domain": True,
    },
}


@dataclass
class TopicSchema:
    """Loaded representation of the wiki's structural rules.

    Content types define folder placement (concepts/, problems/, etc.) while
    domains are metadata tags in frontmatter — this decouples physical layout
    from topic classification, allowing pages to span multiple domains.
    """

    content_types: dict[str, dict] = field(default_factory=dict)
    domains: dict[str, dict] = field(default_factory=dict)
    page_rules: dict = field(default_factory=dict)

    @property
    def valid_types(self) -> set[str]:
        return set(self.content_types.keys())

    @property
    def valid_domains(self) -> set[str]:
        return set(self.domains.keys())

    def directory_for_type(self, content_type: str) -> str:
        """
        Map a content type to its wiki subdirectory.

        Parameters
        ----------
        content_type: str
            A registered content type key (e.g., ``"concept"``).

        Returns
        -------
        str
            Directory path for the content type
            (e.g., ``"concepts/"``).

        Raises
        ------
        ValueError
            If `content_type` is not in the schema.
        """
        ct = self.content_types.get(content_type)
        if ct is None:
            raise ValueError(f"Unknown content type: {content_type}")
        return ct["directory"]


def load_schema(wiki_dir: Path) -> TopicSchema:
    """
    Load the topic schema from the wiki _meta directory.

    If no schema file exists on disk, writes the default schema
    before loading.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.

    Returns
    -------
    TopicSchema
        Parsed schema loaded from ``_meta/topic_schema.yaml``.
    """
    schema_path = wiki_dir / "_meta" / "topic_schema.yaml"
    if not schema_path.exists():
        logger.info("No schema found — writing defaults to %s", schema_path)
        write_default_schema(wiki_dir)

    with open(schema_path) as f:
        raw = yaml.safe_load(f) or {}

    return TopicSchema(
        content_types=raw.get("content_types", {}),
        domains=raw.get("domains", {}),
        page_rules=raw.get("page_rules", {}),
    )


def write_default_schema(wiki_dir: Path) -> Path:
    """
    Write the default topic schema to disk.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.

    Returns
    -------
    Path
        Absolute path to the written schema file.
    """
    schema_path = wiki_dir / "_meta" / "topic_schema.yaml"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(
        _SCHEMA_HEADER + yaml.dump(DEFAULT_SCHEMA, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Wrote default schema to %s", schema_path)
    return schema_path


def register_domains(wiki_dir: Path, domains: set[str]) -> list[str]:
    """Add domains the agent has used to the schema's vocabulary.

    The agent creates domains as it compiles; recording the ones it used keeps
    ``topic_schema.yaml`` the canonical list it reuses on later runs (and that
    the GUI edits), so the vocabulary converges instead of fragmenting. Only
    writes when there is something new to add.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    domains: set[str]
        Domain names found in page frontmatter.

    Returns
    -------
    list[str]
        The newly registered domain names.
    """
    schema_path = wiki_dir / "_meta" / "topic_schema.yaml"
    if not schema_path.exists():
        return []
    raw = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
    existing = raw.get("domains") or {}
    new = sorted(name for name in domains if name and name not in existing)
    if not new:
        return []
    for name in new:
        existing[name] = {"description": ""}
    raw["domains"] = existing
    schema_path.write_text(
        _SCHEMA_HEADER + yaml.dump(raw, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Registered %d new domain(s): %s", len(new), ", ".join(new))
    return new


def remap_schema_domains(wiki_dir: Path, mapping: dict[str, str | None]) -> None:
    """Rename, merge, or delete domain keys in the schema vocabulary.

    ``mapping`` maps an existing domain name to its new name, or to ``None`` to
    delete it; several sources mapping to one target express a merge. A target
    that does not yet exist is created so the vocabulary stays in sync with page
    frontmatter after a bulk edit. A renamed/merged source keeps the target's
    own metadata when the target already existed, otherwise carries the source's.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    mapping: dict[str, str | None]
        Old domain name to new name, or ``None`` to delete.
    """
    schema_path = wiki_dir / "_meta" / "topic_schema.yaml"
    if not schema_path.exists():
        return
    raw = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
    domains = raw.get("domains") or {}

    result: dict = {}
    for name, meta in domains.items():
        if name in mapping and mapping[name] != name:
            continue  # merged, renamed, or deleted away
        result[name] = meta
    for source, target in mapping.items():
        if target is not None and target not in result:
            result[target] = domains.get(source) or {"description": ""}

    if result == domains:
        return
    raw["domains"] = result
    schema_path.write_text(
        _SCHEMA_HEADER + yaml.dump(raw, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
