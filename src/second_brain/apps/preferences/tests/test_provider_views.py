"""Provider discovery through the authenticated ASGI API, without the user's Keychain."""

from unittest.mock import AsyncMock

import pytest
from django.conf import settings
from django.test import AsyncClient

from second_brain.cloud_llm.pi import pi_helper


async def test_picker_discovers_providers_and_lists_the_selected_models(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def helper(*args: str) -> object:
        return {
            ("list-providers",): [
                {"id": "new-vendor", "name": "New Vendor", "loginMethods": ["api_key"]},
                {"id": "other", "name": "Other", "loginMethods": []},
            ],
            ("list-models", "new-vendor"): [
                {
                    "id": "group/model",
                    "name": "Model",
                    "contextWindow": 200_000,
                    "price": {"input": 3, "output": 15},
                }
            ],
            ("list-models", "other"): [],
        }[args]

    monkeypatch.setattr(pi_helper, "run_json", helper)
    headers = {"Authorization": f"Bearer {settings.API_TOKEN}"}
    response = await async_client.get("/v1/providers", headers=headers)
    assert response.status_code == 200
    providers = response.json()
    assert providers == [
        {"id": "new-vendor", "name": "New Vendor", "login_methods": ["api_key"]},
        {"id": "other", "name": "Other", "login_methods": []},
    ]
    connected = await async_client.get("/v1/providers/new-vendor/models", headers=headers)
    assert connected.status_code == 200
    assert connected.json() == [{"id": "group/model", "name": "Model"}]
    disconnected = await async_client.get("/v1/providers/other/models", headers=headers)
    assert disconnected.status_code == 200
    assert disconnected.json() == []


async def test_provider_discovery_rejects_malformed_helper_data(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed = [{"id": "a", "name": "A", "loginMethods": "private diagnostic"}]
    monkeypatch.setattr(pi_helper, "run_json", AsyncMock(return_value=malformed))
    response = await async_client.get(
        "/v1/providers", headers={"Authorization": f"Bearer {settings.API_TOKEN}"}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Invalid provider list from the provider helper."}


async def test_model_choices_require_local_auth(async_client: AsyncClient) -> None:
    response = await async_client.get("/v1/providers/new-vendor/models")
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/providers/vendor/connection"),
        ("delete", "/providers/vendor/connection"),
        ("post", "/providers/vendor/logins"),
        ("post", "/logins/session/next-event"),
        ("post", "/logins/session/answers"),
        ("delete", "/logins/session"),
    ],
)
async def test_connect_routes_require_local_auth(
    async_client: AsyncClient, method: str, path: str
) -> None:
    response = await getattr(async_client, method)(f"/v1{path}")
    assert response.status_code == 401
    assert "no-store" in response["Cache-Control"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/providers/vendor/logins", {"method": "private-input"}),
        ("/logins/session/answers", {"prompt_id": False, "answer": "private-input"}),
        ("/logins/session/answers", {"prompt_id": 1, "answer": {"private-input": True}}),
    ],
)
async def test_invalid_login_request_does_not_reflect_input(
    async_client: AsyncClient, path: str, body: dict, caplog: pytest.LogCaptureFixture
) -> None:
    response = await async_client.post(
        f"/v1{path}",
        body,
        content_type="application/json",
        headers={"Authorization": f"Bearer {settings.API_TOKEN}"},
    )
    assert response.status_code == 422
    assert "no-store" in response["Cache-Control"]
    assert b"private-input" not in response.content
    assert "private-input" not in caplog.text


async def test_malformed_connection_status_is_not_coerced_or_echoed(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pi_helper, "run_json", AsyncMock(return_value={"connected": "private"}))
    response = await async_client.get(
        "/v1/providers/vendor/connection",
        headers={"Authorization": f"Bearer {settings.API_TOKEN}"},
    )
    assert response.status_code == 503
    assert "no-store" in response["Cache-Control"]
    assert response.json() == {"detail": "Invalid connection status from the provider helper."}
