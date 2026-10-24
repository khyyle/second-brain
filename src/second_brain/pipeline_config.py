"""Typed pipeline preferences and immutable per-job configuration snapshots."""

from __future__ import annotations

from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from second_brain.errors import SecondBrainError
from second_brain.llm import SUPPORTED_MODELS


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

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    enabled: bool = True
    file_types: tuple[str, ...] = ("pdf",)
    force_parse_lane: str | None = None

    @field_validator("path", mode="before")
    @classmethod
    def _resolve(cls, v: str | Path) -> Path:
        if not Path(v).expanduser().is_absolute():
            raise ValueError("must be an absolute folder path")
        return _resolve_path(v)

    @field_validator("force_parse_lane")
    @classmethod
    def _validate_lane(cls, v: str | None) -> str | None:
        if v is not None and v not in ("chandra", "docling"):
            raise ValueError(f"must be 'chandra' or 'docling', or left out, not '{v}'")
        return v


class ParsingConfig(BaseModel):
    """
    Tuning knobs for the document-parsing stage.

    Fields:
    -------
    chandra_precision: str, default="4bit"
        Quantization for the local Chandra MLX model: "4bit" (fastest,
        smallest, accuracy within noise) or "8bit".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chandra_precision: str = "4bit"

    @field_validator("chandra_precision")
    @classmethod
    def _validate_precision(cls, v: str) -> str:
        if v not in ("4bit", "8bit"):
            raise ValueError(f"must be '4bit' or '8bit', not '{v}'")
        return v


class CompilationConfig(BaseModel):
    """
    Settings for the LLM-driven wiki compilation stage.

    Fields:
    -------
    provider: str, default="anthropic"
        Which LLM provider runs the compilation agent
    model: str, default="claude-sonnet-4-6"
        Model the compilation agent runs on. Must be in a supported provider.
    max_tokens_per_page: int, default=4000
        Soft target for the length of a generated wiki page.
    max_iterations: int, default=100
        Runaway guard on agent tool-use turns per source, not a work
        budget. Sized so real work never hits it (a paper against a mature
        wiki runs 20+ turns); spend is governed by the cost cap and the
        user can stop a build at any time.
    max_cost_per_build_usd: float, default=0.0
        Ceiling on estimated spend for a whole build. Once cumulative cost
        crosses it the build stops; finished pages are kept, the
        interrupted source's partial work is rolled back, and everything
        unfinished stays staged for the next run. 0 disables it.
    explore_tools: bool, default=True
        When True, the compilation agent also gets read-only wiki exploration
        tools (keyword/semantic search and graph traversal) for finding existing
        pages to link or update. Set False to reduce build cost.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    max_tokens_per_page: int = Field(default=4000, gt=0)
    max_iterations: int = Field(default=100, gt=0)
    max_cost_per_build_usd: float = Field(default=0.0, ge=0.0)
    explore_tools: bool = True

    @model_validator(mode="after")
    def _validate_provider_model(self) -> CompilationConfig:
        if self.provider not in SUPPORTED_MODELS:
            raise ValueError(
                f"compilation.provider must be one of {', '.join(SUPPORTED_MODELS)}, "
                f"not '{self.provider}'"
            )
        if self.model not in SUPPORTED_MODELS[self.provider]:
            raise ValueError(
                f"compilation.model must be one of {', '.join(SUPPORTED_MODELS[self.provider])} "
                f"for {self.provider}, not '{self.model}'"
            )
        return self


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
    hdbscan_min_cluster_size: int, default=2
        Smallest grouping the hdbscan clusterer treats as a cluster.
    max_sources_per_run: int, default=5
        Cap on sources handed to a single agent run; larger clusters are
        split into batches so one run stays a bounded, coherent unit.
    signature_chars: int, default=8000
        How much of each source's opening to embed as its topical
        fingerprint for clustering.
    sources: tuple[str, ...], default=("chatgpt",)
        Which source lanes are clustered. Lanes outside this list compile
        one source per run, since deliberately dropped material is already
        curated and carries little redundancy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

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
            raise ValueError(f"must be one of {', '.join(_CLUSTERING_ALGORITHMS)}, not '{v}'")
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

    model_config = ConfigDict(frozen=True, extra="forbid")

    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = Field(default=768, gt=0)
    ollama_host: str = "http://localhost:11434"
    semantic_enabled: bool = True


# Kept in sync with TRIAGE_PROMPTS in second_brain/apps/ingestion/services/triage/prompts.py
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
    model: str, default="gemma4:12b"
        Ollama model used to classify sources.
    ollama_host: str, default="http://localhost:11434"
        Base URL of the local Ollama server.
    min_word_count: int, default=300
        Free heuristic floor; shorter sources are skipped without a model call.
    worthwhile_threshold: float, default=0.6
        Confidence below which a "worthwhile" verdict is demoted to "review".
    profile: str, default="balanced"
        Triage personality; one of the names in
        second_brain/apps/ingestion/services/triage/prompts.py.
    sources: tuple[str, ...], default=("chatgpt",)
        Which source folders are model-triaged. Others pass through as
        worthwhile (dropping them is the curation).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    model: str = "gemma4:12b"
    ollama_host: str = "http://localhost:11434"
    min_word_count: int = Field(default=300, ge=0)
    worthwhile_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    profile: str = "balanced"
    sources: tuple[str, ...] = ("chatgpt",)

    @field_validator("profile")
    @classmethod
    def _validate_profile(cls, v: str) -> str:
        if v not in _TRIAGE_PROFILES:
            raise ValueError(f"must be one of {', '.join(_TRIAGE_PROFILES)}, not '{v}'")
        return v


class Preferences(BaseModel):
    """Editable non-secret preferences persisted in the selected vault's config.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    compilation: CompilationConfig = Field(default_factory=CompilationConfig)
    clustering: ClusteringConfig = Field(default_factory=ClusteringConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    triage: TriageConfig = Field(default_factory=TriageConfig)


class PipelineConfig(Preferences):
    """Internal pipeline context: a preference snapshot plus startup-owned paths.

    Constructed by the backend, never accepted as a request or saved as settings.
    Algorithms receive it explicitly so edits cannot change a running job.
    """

    data_dir: Path
    raw_input_dir: Path | None = Field(default=None, exclude=True)

    @field_validator("data_dir", mode="before")
    @classmethod
    def _resolve_data_dir(cls, v: str | Path) -> Path:
        return _resolve_path(v)

    @property
    def drops_dir(self) -> Path:
        return self.data_dir / "drops"

    @property
    def raw_dir(self) -> Path:
        return self.raw_input_dir or self.data_dir / "raw"

    @property
    def wiki_dir(self) -> Path:
        return self.data_dir / "wiki"

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


class ConfigError(SecondBrainError):
    """Raised when config.yaml can't be read or holds an invalid setting."""
