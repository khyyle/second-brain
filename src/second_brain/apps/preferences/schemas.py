"""User-editable settings and public catalog entries; secrets are write-only."""

from ninja import Schema
from pydantic import ConfigDict, Field

from second_brain.pipeline_config import (
    ClusteringConfig,
    ParsingConfig,
    SearchConfig,
    SourceConfig,
    TriageConfig,
)


class CompilationUpdate(Schema):
    """Partial fields; provider/model compatibility is checked after merging settings."""

    model_config = ConfigDict(extra="forbid")
    provider: str | None = None
    model: str | None = None
    max_tokens_per_page: int | None = None
    max_iterations: int | None = None
    max_cost_per_build_usd: float | None = None
    explore_tools: bool | None = None


class SettingsUpdate(Schema):
    model_config = ConfigDict(extra="forbid")
    sources: dict[str, SourceConfig] | None = None
    parsing: ParsingConfig | None = None
    compilation: CompilationUpdate | None = None
    clustering: ClusteringConfig | None = None
    search: SearchConfig | None = None
    triage: TriageConfig | None = None


class WatchedFolder(Schema):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[\w-]+$", max_length=100)
    path: str
    enabled: bool = True
    file_types: list[str] = Field(default_factory=lambda: ["pdf", "md", "txt"])
    force_parse_lane: str | None = None


class CredentialStatus(Schema):
    provider: str
    configured: bool


class ModelInfo(Schema):
    id: str
    input_price_per_mtok: float
    output_price_per_mtok: float
    context_window_tokens: int


class ProviderInfo(Schema):
    id: str
    name: str
    models: list[ModelInfo]


class Catalog(Schema):
    providers: list[ProviderInfo]
    file_types: list[str]
    triage_profiles: list[str]
    clustering_algorithms: list[str]


class Diagnostics(Schema):
    healthy: bool
    reachable: bool
    host: str
    required_models: list[str]
    missing_models: list[str]
    message: str
