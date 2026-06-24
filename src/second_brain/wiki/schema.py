"""Topic schema management — load, validate, and propose changes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

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
    "domains": {
        "mathematics": {
            "description": "Pure mathematical foundations",
            "common_tags": [
                "linear-algebra",
                "calculus",
                "probability",
                "optimization",
                "discrete-math",
                "differential-equations",
                "number-theory",
                "real-analysis",
                "abstract-algebra",
            ],
        },
        "physics": {
            "description": "Physical sciences",
            "common_tags": [
                "classical-mechanics",
                "thermodynamics",
                "electromagnetism",
                "quantum-mechanics",
                "statistical-mechanics",
                "optics",
            ],
        },
        "computer-science": {
            "description": "CS coursework and applied CS",
            "common_tags": [
                "algorithms",
                "data-structures",
                "machine-learning",
                "deep-learning",
                "systems",
                "programming",
                "nlp",
                "computer-vision",
            ],
        },
        "chemistry": {
            "description": "Chemistry",
            "common_tags": ["general", "organic", "physical", "spectroscopy"],
        },
        "engineering": {
            "description": "Engineering courses",
            "common_tags": ["electrical", "environmental", "bioengineering", "signals"],
        },
        "economics": {
            "description": "Economics and finance",
            "common_tags": ["microeconomics", "game-theory", "finance", "market-design"],
        },
        "humanities": {
            "description": "Humanities",
            "common_tags": ["writing", "history", "philosophy", "rhetoric"],
        },
    },
    "page_rules": {
        "target_length": "500-3000 words",
        "split_threshold": 4000,
        "merge_threshold": 150,
        "naming": "kebab-case",
        "multi_domain": True,
    },
    "agent_permissions": {
        "can_create_tags": True,
        "can_create_domains": False,
        "can_split_pages": True,
        "can_merge_pages": True,
        "can_retag": True,
        "can_change_content_type": True,
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
    agent_permissions: dict = field(default_factory=dict)

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
        agent_permissions=raw.get("agent_permissions", {}),
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

    header = (
        "# Topic Schema — the compilation agent MUST read this before writing.\n"
        "# Domains live in frontmatter metadata, NOT in folder paths.\n"
        "# Folders are typed buckets (concepts/, problems/, projects/, insights/).\n\n"
    )
    schema_path.write_text(
        header + yaml.dump(DEFAULT_SCHEMA, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Wrote default schema to %s", schema_path)
    return schema_path


def append_schema_proposal(wiki_dir: Path, proposal: dict) -> None:
    """
    Append a schema change proposal for human review.

    The agent cannot create new domains directly -- it writes
    proposals here for the user to approve and merge into
    ``topic_schema.yaml``.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    proposal: dict
        Proposal payload; should include a ``"description"`` key.
    """
    proposals_path = wiki_dir / "_meta" / "schema_proposals.yaml"
    existing: list = []
    if proposals_path.exists():
        with open(proposals_path) as f:
            existing = yaml.safe_load(f) or []
        if not isinstance(existing, list):
            existing = [existing]

    existing.append(proposal)
    proposals_path.write_text(
        yaml.dump(existing, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Appended schema proposal: %s", proposal.get("description", ""))
