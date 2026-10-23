"""Exercise the public HTTP contract through Django's actual URL configuration."""

import pytest
from django.conf import settings
from django.test import AsyncClient, Client


def test_health_is_available_without_database_access(client: Client) -> None:
    # No django_db marker: pytest-django will reject any accidental database access.
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_through_asgi(async_client: AsyncClient) -> None:
    response = await async_client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_describes_the_health_response(client: Client) -> None:
    response = client.get("/v1/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    operation = schema["paths"]["/v1/health"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }
    assert (
        schema["components"]["schemas"]["HealthResponse"]["properties"]["status"]["const"] == "ok"
    )


def test_interactive_docs_render(client: Client) -> None:
    response = client.get("/v1/docs")

    assert response.status_code == 200
    assert b"/v1/openapi.json" in response.content


def test_unexpected_host_is_rejected(client: Client) -> None:
    response = client.get("/v1/health", HTTP_HOST="untrusted.example")

    assert response.status_code == 400


def test_feature_contracts_are_authenticated_and_explicitly_pending(client: Client) -> None:
    schema = client.get("/v1/openapi.json").json()
    operations = [
        operation
        for path, methods in schema["paths"].items()
        if path != "/v1/health"
        for operation in methods.values()
    ]
    assert len(operations) == 42
    for operation in operations:
        assert "501" in operation["responses"]
        assert operation["security"] == [{"LocalTokenAuth": []}]


@pytest.mark.parametrize("token", [None, "invalid"])
def test_feature_requests_require_the_local_token(client: Client, token: str | None) -> None:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    assert client.get("/v1/sources", **headers).status_code == 401


@pytest.mark.parametrize(
    ("method", "url", "payload", "expected"),
    [
        ("get", "/v1/sources", None, 501),
        ("get", "/v1/sources?limit=0", None, 422),
        ("post", "/v1/imports", {"paths": []}, 422),
        ("post", "/v1/imports", {"paths": ["/tmp/notes.md"]}, 501),
        ("patch", "/v1/triage", {"raw_paths": ["chatgpt/a.md"], "decision": "bad"}, 422),
        ("patch", "/v1/triage", {"raw_paths": ["chatgpt/a.md"], "decision": "skip"}, 501),
        ("post", "/v1/builds", {"typo": True}, 422),
        ("post", "/v1/builds", {"dry_run": True}, 501),
        ("get", "/v1/jobs/not-a-uuid", None, 422),
        ("get", "/v1/wiki/history", None, 501),
        ("get", "/v1/settings", None, 501),
    ],
)
def test_scaffolds_validate_input_without_running_services(
    client: Client,
    method: str,
    url: str,
    payload: dict | None,
    expected: int,
) -> None:
    # Deliberately no django_db fixture: a placeholder must never read or mutate records.
    kwargs = {"HTTP_AUTHORIZATION": f"Bearer {settings.API_TOKEN}"}
    if payload is not None:
        kwargs.update(data=payload, content_type="application/json")
    response = getattr(client, method)(url, **kwargs)
    assert response.status_code == expected
    if expected == 501:
        assert "scaffolded but not implemented" in response.json()["detail"]
