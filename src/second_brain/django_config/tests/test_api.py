"""Exercise the public HTTP contract through Django's actual URL configuration."""

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
