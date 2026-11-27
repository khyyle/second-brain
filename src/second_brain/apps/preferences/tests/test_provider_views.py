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
            ("list-models", "new-vendor"): [{"id": "group/model", "name": "Model"}],
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
