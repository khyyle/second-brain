"""Configuration management — loads YAML config with Pydantic validation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"


def _resolve_path(v: str | Path) -> Path:
    return Path(v).expanduser().resolve()


class SourceConfig(BaseModel):
    """
    A single watched source directory and its ingestion settings.

    Fields:
    -------
    path: Path
        Directory to watch for this source's files.
    enabled: bool, default=True
        Whether the watcher and batch scan process this source.
    file_types: tuple[str, ...], default=("pdf",)
        File extensions (without the dot) accepted from this source.
    force_parse_lane: str | None, default=None
        Pin every PDF to "chandra" or "docling" instead of per-page
        routing; None uses automatic routing.
    """
    model_config = ConfigDict(frozen=True)

    path: Path
    enabled: bool = True
    file_types: tuple[str, ...] = ("pdf",)
    force_parse_lane: str | None = None

    @field_validator("path", mode="before")
    @classmethod
    def _resolve(cls, v: str | Path) -> Path:
        return _resolve_path(v)

    @field_validator("force_parse_lane")
    @classmethod
    def _validate_lane(cls, v: str | None) -> str | None:
        if v is not None and v not in ("chandra", "docling"):
            raise ValueError(
                f"force_parse_lane must be 'chandra', 'docling', or null — got '{v}'"
            )
        return v


class ParsingConfig(BaseModel):
    """
    Tuning knobs for the document-parsing stage.

    Typed pages always go to Docling (local, free). Only handwritten /
    scanned pages use the parser named below.

    Fields:
    -------
    handwriting_parser: str, default="chandra"
        Parser for handwritten / scanned pages. Either "chandra" (local,
        free, runs on MLX 4-bit at ~38s/page on Apple Silicon) or a Claude
        vision model id such as "claude-sonnet-4-6" (~13s/page, ~$0.015/page).
    chandra_precision: str, default="4bit"
        Quantization for the local Chandra MLX model: "4bit" (fastest,
        smallest, accuracy within noise) or "8bit".
    """
    model_config = ConfigDict(frozen=True)

    handwriting_parser: str = "chandra"
    chandra_precision: str = "4bit"

    @field_validator("chandra_precision")
    @classmethod
    def _validate_precision(cls, v: str) -> str:
        if v not in ("4bit", "8bit"):
            raise ValueError(f"chandra_precision must be '4bit' or '8bit', got '{v}'")
        return v

    @field_validator("handwriting_parser")
    @classmethod
    def _validate_handwriting(cls, v: str) -> str:
        if v != "chandra" and not v.startswith("claude"):
            raise ValueError(
                "handwriting_parser must be 'chandra' or a Claude model id "
                f"(e.g. 'claude-sonnet-4-6'), got '{v}'"
            )
        return v


class CompilationConfig(BaseModel):
    """
    Settings for the LLM-driven wiki compilation stage.

    Fields:
    -------
    model: str, default="claude-sonnet-4-6"
        Claude model the compilation agent runs on.
    max_tokens_per_page: int, default=4000
        Soft target for the length of a generated wiki page.
    max_iterations: int, default=20
        Hard cap on agent tool-use turns per source.
    token_budget_per_run: int, default=150000
        Hard cap on cumulative tokens (input + output) per source, so a
        confused agent can't spend unbounded API money on one document.
    max_cost_per_build_usd: float, default=0.0
        Ceiling on estimated spend for a whole build. Once cumulative cost
        crosses it the build stops before the next source; finished pages
        are kept and the rest stay staged for the next run. 0 disables it.
    """
    model_config = ConfigDict(frozen=True)

    model: str = "claude-sonnet-4-6"
    max_tokens_per_page: int = Field(default=4000, gt=0)
    max_iterations: int = Field(default=20, gt=0)
    token_budget_per_run: int = Field(default=150_000, gt=0)
    max_cost_per_build_usd: float = Field(default=0.0, ge=0.0)


_CLUSTERING_ALGORITHMS = ("threshold", "hdbscan")


class ClusteringConfig(BaseModel):
    """
    Group related sources so one topic compiles in a single agent run.

    Clustering collapses redundancy (many chats on one topic become one
    page) and amortizes per-run agent overhead. ``threshold`` groups by
    connected components of a cosine-similarity graph; ``hdbscan`` clusters
    by density and isolates sparse sources as singletons.

    Fields:
    -------
    enabled: bool, default=False
        Master switch. When False, each source compiles in its own run.
    algorithm: str, default="threshold"
        Clusterer implementation; one of ``threshold`` or ``hdbscan``.
    threshold: float, default=0.82
        Cosine similarity at/above which two sources join a cluster
        (used by the threshold clusterer).
    hdbscan_min_cluster_size: int, default=2
        Smallest grouping the hdbscan clusterer treats as a cluster.
    max_sources_per_run: int, default=5
        Cap on sources handed to a single agent run; larger clusters are
        split into batches so one run stays within the token budget.
    signature_chars: int, default=8000
        How much of each source's opening to embed as its topical
        fingerprint for clustering.
    sources: tuple[str, ...], default=("chatgpt",)
        Which source lanes are clustered. Lanes outside this list compile
        one source per run, since deliberately dropped material is already
        curated and carries little redundancy.
    """
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    algorithm: str = "threshold"
    threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    hdbscan_min_cluster_size: int = Field(default=2, ge=2)
    max_sources_per_run: int = Field(default=5, gt=0)
    signature_chars: int = Field(default=8000, gt=0)
    sources: tuple[str, ...] = ("chatgpt",)

    @field_validator("algorithm")
    @classmethod
    def _validate_algorithm(cls, v: str) -> str:
        if v not in _CLUSTERING_ALGORITHMS:
            raise ValueError(
                f"clustering.algorithm must be one of {_CLUSTERING_ALGORITHMS}, got '{v}'"
            )
        return v


class SearchConfig(BaseModel):
    """
    Embedding model and Ollama connection details for semantic search.

    Fields:
    -------
    embedding_model: str, default="nomic-embed-text"
        Ollama model used to embed wiki pages and queries.
    embedding_dimensions: int, default=768
        Dimensionality of the embedding vectors.
    ollama_host: str, default="http://localhost:11434"
        Base URL of the local Ollama server.
    semantic_enabled: bool, default=True
        When True the MCP exposes a semantic_search tool and the index
        builds embeddings; both degrade gracefully if Ollama is down.
    """
    model_config = ConfigDict(frozen=True)

    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = Field(default=768, gt=0)
    ollama_host: str = "http://localhost:11434"
    semantic_enabled: bool = True


# Kept in sync with TRIAGE_PROMPTS in second_brain/triage/prompts.py
# (asserted by a test). Hardcoded here to avoid an import cycle, since
# the triage package imports this module.
_TRIAGE_PROFILES = ("balanced", "technical", "skip_heavy", "project_heavy", "lenient")


class TriageConfig(BaseModel):
    """
    Cheap local-model triage that filters sources before Claude compilation.

    Fields:
    -------
    enabled: bool, default=True
        Master switch for the triage stage.
    model: str, default="gemma3:4b"
        Ollama model used to classify sources.
    ollama_host: str, default="http://localhost:11434"
        Base URL of the local Ollama server.
    min_word_count: int, default=300
        Free heuristic floor; shorter sources are skipped without a model call.
    worthwhile_threshold: float, default=0.6
        Confidence below which a "worthwhile" verdict is demoted to "review".
    profile: str, default="balanced"
        Triage personality; one of the names in second_brain/triage/prompts.py.
    sources: tuple[str, ...], default=("chatgpt",)
        Which source folders are model-triaged. Others pass through as
        worthwhile (dropping them is the curation).
    """
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    model: str = "gemma3:4b"
    ollama_host: str = "http://localhost:11434"
    min_word_count: int = Field(default=300, ge=0)
    worthwhile_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    profile: str = "balanced"
    sources: tuple[str, ...] = ("chatgpt",)

    @field_validator("profile")
    @classmethod
    def _validate_profile(cls, v: str) -> str:
        if v not in _TRIAGE_PROFILES:
            raise ValueError(
                f"triage.profile must be one of {_TRIAGE_PROFILES}, got '{v}'"
            )
        return v


class ScheduleConfig(BaseModel):
    """Hours of the day (24h) at which the pipeline should run."""
    model_config = ConfigDict(frozen=True)

    hours: tuple[int, ...] = (8, 14, 20)

    @field_validator("hours")
    @classmethod
    def _validate_hours(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        for hour in v:
            if not 0 <= hour <= 23:
                raise ValueError(f"Hour must be 0-23, got {hour}")
        return v


class Config(BaseModel):
    """
    Top-level application configuration.

    Resolves all derived paths (drops, raw, wiki, logs, databases)
    from a single ``data_dir`` root. Frozen to prevent accidental
    mutation after loading.
    """
    model_config = ConfigDict(frozen=True)

    data_dir: Path = Field(default_factory=lambda: Path.home() / "second-brain")
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    compilation: CompilationConfig = Field(default_factory=CompilationConfig)
    clustering: ClusteringConfig = Field(default_factory=ClusteringConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    triage: TriageConfig = Field(default_factory=TriageConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    @field_validator("data_dir", mode="before")
    @classmethod
    def _resolve_data_dir(cls, v: str | Path) -> Path:
        return _resolve_path(v)

    @property
    def drops_dir(self) -> Path:
        return self.data_dir / "drops"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def wiki_dir(self) -> Path:
        return self.data_dir / "wiki"

    @property
    def manifest_db_path(self) -> Path:
        return self.data_dir / "manifest.db"

    @property
    def search_db_path(self) -> Path:
        return self.data_dir / "search.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def inbox_dir(self) -> Path:
        """Holds triage 'review'-tier sources awaiting a manual decision."""
        return self.data_dir / "inbox"

    def ensure_directories(self) -> None:
        """Create all required data directories if they don't exist."""
        for directory in (
            self.drops_dir / "chatgpt",
            self.drops_dir / "documents",
            self.raw_dir,
            self.wiki_dir / "_meta",
            self.wiki_dir / "_views" / "domains",
            self.wiki_dir / "concepts",
            self.wiki_dir / "problems",
            self.wiki_dir / "projects",
            self.wiki_dir / "insights",
            self.logs_dir,
            self.inbox_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


class ConfigError(Exception):
    """Raised when config.yaml contains invalid or out-of-range values."""


def _merge_user_sources(raw: dict) -> dict:
    """
    Fold separately-managed watched folders into the ``sources`` map.

    ``<data_dir>/sources.json`` is a flat list of
    ``{name, path, enabled, file_types}`` objects that can be edited
    without touching the nested YAML. Entries are added only when their
    name doesn't already exist in config.yaml, so the built-in drop
    folders always win.
    """
    data_dir = Path(str(raw.get("data_dir", "~/second-brain"))).expanduser()
    sources_file = data_dir / "sources.json"
    if not sources_file.exists():
        return raw

    try:
        entries = json.loads(sources_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Ignoring invalid %s: %s", sources_file, e)
        return raw
    if not isinstance(entries, list):
        return raw

    sources = dict(raw.get("sources") or {})
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name, src_path = entry.get("name"), entry.get("path")
        if not name or not src_path or name in sources:
            continue
        sources[name] = {
            "path": src_path,
            "enabled": entry.get("enabled", True),
            "file_types": entry.get("file_types", ["pdf", "md", "txt"]),
        }

    merged = dict(raw)
    merged["sources"] = sources
    return merged


def load_config(path: Path | None = None) -> Config:
    """
    Load configuration from a YAML file, falling back to defaults.

    Parameters
    ----------
    path: Path | None
        Explicit config file path, or ``None`` to use the built-in
        default at ``config/config.yaml``.

    Returns
    -------
    Config
        Fully resolved and validated configuration instance.

    Raises
    ------
    ConfigError
        If the YAML file contains invalid or out-of-range values.
    """
    config_path = path or _DEFAULT_CONFIG_PATH
    if config_path.exists():
        with open(config_path) as config_file:
            raw = yaml.safe_load(config_file) or {}
    else:
        logger.info("No config file at %s — using defaults", config_path)
        raw = {}

    raw = _merge_user_sources(raw)

    try:
        return Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"Invalid configuration in {config_path}:\n{e}") from e
