"""A complete read path: request validation, ORM queries, output, and error mapping."""

from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError
from django.test import Client, override_settings

from second_brain.apps.library.errors import SourceNotFoundError
from second_brain.apps.library.models import Source
from second_brain.apps.library.services import source_queries
from second_brain.errors import SecondBrainError

pytestmark = pytest.mark.django_db
RECENT = datetime(2026, 10, 23, 12, tzinfo=UTC)


def _source(
    file_path: str,
    *,
    status: str = "complete",
    updated_at: datetime = RECENT,
) -> Source:
    return Source.objects.create(
        file_path=file_path,
        sha256="internal-file-hash",
        source_type="documents",
        status=status,
        ingested_at=RECENT,
        updated_at=updated_at,
    )


def test_list_filters_orders_and_paginates_without_exposing_internal_fields(
    api_client: Client,
) -> None:
    _source("/drops/b.md")
    _source("/drops/a.md")  # Same timestamp: file_path decides the order, not insert order.
    _source("/drops/older.md", updated_at=RECENT - timedelta(days=1))
    _source("/drops/failed.md", status="failed")

    response = api_client.get("/v1/sources", {"status": "complete", "limit": 1, "offset": 1})

    assert response.status_code == 200
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    assert [item["file_path"] for item in page["items"]] == ["/drops/b.md"]
    assert "sha256" not in page["items"][0]
    assert page["items"][0]["raw_output"] is None
    assert datetime.fromisoformat(page["items"][0]["updated_at"]) == RECENT


def test_list_defaults_and_empty_page(api_client: Client) -> None:
    _source("/drops/a.md")
    _source("/drops/b.md", status="failed")
    page = api_client.get("/v1/sources").json()
    assert (page["total"], page["limit"], page["offset"]) == (2, 50, 0)
    assert len(page["items"]) == 2

    beyond_end = api_client.get("/v1/sources", {"offset": 99}).json()
    assert beyond_end == {"items": [], "total": 2, "limit": 50, "offset": 99}


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"limit": "bad"},
        {"offset": -1},
        {"status": "complet"},
    ],
)
def test_invalid_list_input_is_rejected_before_queries(
    api_client: Client,
    django_assert_num_queries,
    params: dict,
) -> None:
    with django_assert_num_queries(0):
        response = api_client.get("/v1/sources", params)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)  # Ninja's validation error format.


def test_detail_returns_the_source_through_its_output_schema(api_client: Client) -> None:
    source = _source("/drops/my notes.md")
    assert source_queries.get_source(source.pk) == source  # Services return ORM objects.

    response = api_client.get("/v1/sources/detail", {"file_path": source.pk})
    assert response.status_code == 200
    result = response.json()
    assert result["file_path"] == source.pk
    assert result["status"] == "complete"
    assert set(result) == {
        "file_path",
        "source_type",
        "status",
        "parse_lane",
        "raw_output",
        "ingested_at",
        "updated_at",
        "error",
        "origin_path",
    }


def test_missing_source_is_an_expected_domain_error_then_a_404(api_client: Client) -> None:
    with pytest.raises(SourceNotFoundError) as failure:
        source_queries.get_source("/missing.md")
    assert isinstance(failure.value, SecondBrainError)
    assert isinstance(failure.value.__cause__, Source.DoesNotExist)

    response = api_client.get("/v1/sources/detail", {"file_path": "/missing.md"})
    assert response.status_code == 404
    assert response.json() == {"detail": failure.value.reason}


@pytest.mark.parametrize("params", [{}, {"file_path": ""}])
def test_missing_or_empty_lookup_parameter_is_validation_error(
    api_client: Client,
    django_assert_num_queries,
    params: dict,
) -> None:
    with django_assert_num_queries(0):
        response = api_client.get("/v1/sources/detail", params)
    assert response.status_code == 422


@pytest.mark.parametrize("error_type", [ValueError, FileNotFoundError, OperationalError])
@override_settings(DEBUG=False)
def test_unexpected_service_failures_remain_server_errors(
    api_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    def broken_lookup(file_path: str) -> Source:
        raise error_type("internal failure details must not be returned")

    monkeypatch.setattr(source_queries, "get_source", broken_lookup)
    api_client.raise_request_exception = False
    response = api_client.get("/v1/sources/detail", {"file_path": "/notes.md"})
    assert response.status_code == 500
    assert b"internal failure details" not in response.content
