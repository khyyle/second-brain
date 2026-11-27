"""User-editable settings and public catalog entries; secrets are write-only."""

from typing import Literal

from ninja import Schema
from pydantic import ConfigDict, Field, SecretStr

from second_brain.pipeline_config import (
    ClusteringConfig,
    ModelID,
    ParsingConfig,
    ProviderID,
    SourceConfig,
    TriageConfig,
)


class CompilationUpdate(Schema):
    """Partial fields. Settings saves the provider; the Build tab saves that provider's model."""

    model_config = ConfigDict(extra="forbid")
    provider: ProviderID | None = None
    model: ModelID | None = None
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
    triage: TriageConfig | None = None


class WatchedFolder(Schema):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[\w-]+$", max_length=100)
    path: str
    enabled: bool = True
    file_types: list[str] = Field(default_factory=lambda: ["pdf", "md", "txt"])
    force_parse_lane: str | None = None


class ModelChoiceResponse(Schema):
    id: str
    name: str


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


class ConnectionStatus(Schema):
    connected: bool


class LoginRequest(Schema):
    method: Literal["api_key", "oauth"]


class LoginStarted(Schema):
    login_id: str


class LoginAnswer(Schema):
    prompt_id: int = Field(strict=True, gt=0)
    answer: SecretStr
