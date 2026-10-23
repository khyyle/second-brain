"""Manual build requests and optional chat grouping previews."""

from datetime import datetime

from ninja import Schema
from pydantic import Field

from second_brain.django_config.scaffold import RequestSchema


class BuildRequest(RequestSchema):
    full: bool = False
    dry_run: bool = False


class GroupMember(Schema):
    rel: str
    bytes: int


class Group(Schema):
    id: str
    title: str
    members: list[GroupMember]
    estimated_cost_usd: float
    costs: dict[str, float]


class GroupingOverrides(RequestSchema):
    excluded: list[str] = Field(default_factory=list)
    split_groups: list[str] = Field(default_factory=list)


class GroupingPlan(Schema):
    generated_at: datetime
    algorithm: str
    enabled: bool
    source_count: int
    group_count: int
    estimated_cost_usd: float
    costs: dict[str, float]
    groups: list[Group]
    overrides: GroupingOverrides
