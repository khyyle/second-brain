"""Wiki content remains Markdown and Git; these API shapes are not database tables."""

from ninja import Schema
from pydantic import Field

from second_brain.django_config.scaffold import RequestSchema


class WikiPage(Schema):
    path: str
    title: str
    content_type: str
    domains: list[str]


class WikiContent(WikiPage):
    markdown: str


class SearchHit(WikiPage):
    snippet: str


class DomainOut(Schema):
    name: str
    page_count: int
    in_schema: bool


class RenameDomain(RequestSchema):
    old: str = Field(min_length=1)
    new: str = Field(min_length=1)


class MergeRequest(RequestSchema):
    dest: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1)


class ChangeResult(Schema):
    changed_pages: int


class WikiCommit(Schema):
    short_hash: str
    date: str
    subject: str
    files_changed: int


class RollbackRequest(RequestSchema):
    count: int = Field(default=1, ge=1)


class RollbackResult(Schema):
    reverted_commits: list[str]


class RepairRequest(RequestSchema):
    dry_run: bool = False


class PageRepair(Schema):
    rel_path: str
    links_repointed: int


class DuplicatePair(RequestSchema):
    page_a: str = Field(min_length=1)
    page_b: str = Field(min_length=1)


class WikiHealth(Schema):
    orphan_pages: list[str]
    gap_links: list[tuple[str, int]]
    possible_duplicates: list[tuple[str, str, float]]
    oversized_pages: list[tuple[str, int]]
    undersized_pages: list[tuple[str, int]]
    missing_frontmatter: list[str]
