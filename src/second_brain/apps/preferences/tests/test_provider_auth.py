"""Exercise the real stdio boundary without a network login, Keychain, or Node install."""

import asyncio
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.test import AsyncClient

from second_brain.apps.preferences.services.agent_provider import model_selection
from second_brain.apps.preferences.services.agent_provider import provider_auth as auth
from second_brain.cloud_llm.pi import pi_helper
from second_brain.django_config.errors import (
    ConflictError,
    DependencyError,
    NotFoundError,
    ServiceError,
)


@pytest.fixture
def helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "helper with spaces.py"
    script.write_text("""\
import json, sys, time
from pathlib import Path
command, provider, *method = sys.argv[1:]
saved = Path(__file__).with_name('saved-' + provider)
if provider == 'unknown':
    sys.exit(2)
if command == 'login' and provider == 'silent':
    sys.stdin.read()  # No events. Simulate closing Settings without cancelling the login.
    sys.exit(1)
if provider == 'broken':
    print('private diagnostic', file=sys.stderr)
    print('not JSON: private data')
    sys.exit(0)  # Successful exit is not enough when stdout is malformed.
if command == 'login':
    prompt = {'type': 'prompt', 'kind': 'secret', 'id': 1, 'message': provider}
    if provider == 'interactive':
        if method != ['oauth']:
            sys.exit(2)
        events = [
            {'type': 'auth_url', 'url': 'https://example.test', 'instructions': 'Open'},
            {'type': 'device_code', 'userCode': 'test-code', 'verificationUri': 'https://example.test',
             'openBrowserOnConfirm': False, 'copyCodeOnConfirm': True, 'intervalSeconds': 1,
             'expiresInSeconds': 60},
            {'type': 'progress', 'message': 'Waiting'},
            {'type': 'info', 'message': 'Help',
             'links': [{'url': 'https://example.test', 'label': 'Help'}]},
        ]
        for event in events:
            print(json.dumps({**event, 'internal': 'private diagnostic'}), flush=True)
        prompt.update(kind='select', options=[{
            'id': 'option', 'label': 'Choice', 'description': 'Detail', 'internal': 'private'
        }])
    print(json.dumps(prompt), flush=True)
    reply = json.loads(input())
    if reply == {'type': 'cancel'}:
        sys.exit(1 if provider == 'failing' else 3)
    expected = 'option' if provider == 'interactive' else provider + '\\nanswer'
    if reply != {'type': 'answer', 'id': 1, 'value': expected}:
        sys.exit(1)
    saved.touch()
elif command == 'connection-status':
    print(json.dumps({'connected': saved.exists()}))
    sys.exit(0)
elif command == 'list-models':
    choices = {
        'connected': [{'id': 'model/a', 'name': 'A', 'contextWindow': 1000, 'extra': 'unused'}],
        'disconnected': [],
        'bad-list': {'error': 'private diagnostic'},
        'bad-choice': [{'id': 'missing-name'}],
    }
    print(json.dumps(choices[provider]))
    sys.exit(0)
elif command == 'logout' and provider == 'unconfirmed':
    print('{}')
    sys.exit(0)
elif command == 'logout':
    saved.unlink(missing_ok=True)
else:
    sys.exit(2)
print('{"type":"done"}', flush=True)
if provider == 'delayed-exit':
    Path(__file__).with_name('saved').touch()
    while not Path(__file__).with_name('exit').exists():
        time.sleep(0.01)
""")
    monkeypatch.setattr(pi_helper, "NODE", Path(sys.executable))
    monkeypatch.setattr(pi_helper, "PI_HELPER", script)


@pytest.mark.usefixtures("helper")
async def test_answers_resume_the_right_login_and_cannot_be_replayed() -> None:
    first = await auth.start_login("first")
    second = await auth.start_login("second", "api_key")
    async with asyncio.timeout(5):
        assert (await auth.read_login_event(first))["message"] == "first"
        assert (await auth.read_login_event(second))["message"] == "second"
        with pytest.raises(ConflictError):
            await auth.answer_login(first, 2, "wrong prompt")
        await auth.answer_login(second, 1, "second\nanswer")
        with pytest.raises(ConflictError):
            await auth.answer_login(second, 1, "duplicate")
        await auth.answer_login(first, 1, "first\nanswer")
        for login_id in (second, first):
            assert await auth.read_login_event(login_id) == {"type": "done"}
            with pytest.raises(NotFoundError):
                await auth.read_login_event(login_id)


@pytest.mark.usefixtures("helper")
async def test_cancel_releases_process_and_unblocks_reader() -> None:
    login_id = await auth.start_login("provider")
    assert (await auth.read_login_event(login_id))["type"] == "prompt"
    async with asyncio.timeout(5):
        event, _ = await asyncio.gather(
            auth.read_login_event(login_id), auth.cancel_login(login_id)
        )
    assert event == {"type": "cancelled"}
    with pytest.raises(NotFoundError):
        await auth.answer_login(login_id, 1, "too late")


@pytest.mark.usefixtures("helper")
async def test_failure_racing_cancel_is_not_a_cancellation() -> None:
    login_id = await auth.start_login("failing")
    await auth.read_login_event(login_id)
    async with asyncio.timeout(5):
        results = await asyncio.gather(
            auth.read_login_event(login_id), auth.cancel_login(login_id), return_exceptions=True
        )
    assert all(isinstance(result, DependencyError) for result in results)


@pytest.mark.usefixtures("helper")
async def test_interrupted_read_does_not_lose_a_confirmed_save(tmp_path: Path) -> None:
    async with asyncio.timeout(5):
        login_id = await auth.start_login("delayed-exit")
        await auth.read_login_event(login_id)
        read = asyncio.create_task(auth.read_login_event(login_id))
        try:
            await auth.answer_login(login_id, 1, "delayed-exit\nanswer")
            while not (tmp_path / "saved").exists():
                await asyncio.sleep(0.01)
            read.cancel()
            with pytest.raises(asyncio.CancelledError):
                await read
        finally:
            (tmp_path / "exit").touch()
        assert await auth.read_login_event(login_id) == {"type": "done"}


@pytest.mark.usefixtures("helper")
async def test_abandoned_login_expires_and_unblocks_a_waiting_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_LOGIN_LIFETIME_SECONDS", 0)
    async with asyncio.timeout(5):
        login_id = await auth.start_login("silent")
        with pytest.raises(DependencyError):
            await auth.read_login_event(login_id)
    with pytest.raises(NotFoundError):
        await auth.cancel_login(login_id)


@pytest.mark.usefixtures("helper")
async def test_helper_rejecting_login_reports_bad_request_and_releases_id() -> None:
    login_id = await auth.start_login("unknown")
    with pytest.raises(ServiceError) as usage:
        await auth.read_login_event(login_id)
    assert usage.value.status_code == 400
    with pytest.raises(NotFoundError):
        await auth.read_login_event(login_id)


@pytest.mark.usefixtures("helper")
async def test_logout_requires_confirmation_even_when_the_helper_exits_successfully() -> None:
    await auth.logout("provider")
    with pytest.raises(DependencyError):
        await auth.logout("unconfirmed")


@pytest.mark.usefixtures("helper")
async def test_malformed_login_event_ends_login_without_exposing_contents() -> None:
    login_id = await auth.start_login("broken")
    with pytest.raises(DependencyError) as invalid:
        await auth.read_login_event(login_id)
    assert "private" not in str(invalid.value)
    with pytest.raises(NotFoundError):
        await auth.read_login_event(login_id)


@pytest.mark.usefixtures("helper")
async def test_model_picker_runs_without_interrupting_a_pending_login() -> None:
    login_id = await auth.start_login("provider")
    try:
        assert (await auth.read_login_event(login_id))["type"] == "prompt"
        assert await model_selection.list_models("connected") == [
            {"id": "model/a", "name": "A", "context_window": 1000}
        ]
        assert await model_selection.list_models("disconnected") == []
    finally:
        await auth.cancel_login(login_id)


@pytest.mark.usefixtures("helper")
@pytest.mark.parametrize("provider", ["bad-list", "bad-choice", "broken"])
async def test_model_picker_rejects_malformed_replies_without_echoing_them(provider: str) -> None:
    with pytest.raises(DependencyError) as invalid:
        await model_selection.list_models(provider)
    assert "private" not in str(invalid.value)


async def test_missing_private_runtime_is_a_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(pi_helper, "NODE", tmp_path / "missing-node")
    with pytest.raises(DependencyError):
        await auth.start_login("provider")


@pytest.mark.usefixtures("helper")
async def test_connect_through_settings_api(
    async_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    headers = {"Authorization": f"Bearer {settings.API_TOKEN}"}
    provider = "/v1/providers/interactive"
    status_path = f"{provider}/connection"
    assert (await async_client.get(status_path, headers=headers)).json() == {"connected": False}

    start = await async_client.post(
        f"{provider}/logins", {"method": "oauth"}, content_type="application/json", headers=headers
    )
    assert start.status_code == 200
    assert "no-store" in start["Cache-Control"]
    login = f"/v1/logins/{start.json()['login_id']}"
    event_path = f"{login}/next-event"
    expected = [
        {"type": "auth_url", "url": "https://example.test", "instructions": "Open"},
        {
            "type": "device_code",
            "userCode": "test-code",
            "verificationUri": "https://example.test",
            "openBrowserOnConfirm": False,
            "copyCodeOnConfirm": True,
            "intervalSeconds": 1,
            "expiresInSeconds": 60,
        },
        {"type": "progress", "message": "Waiting"},
        {
            "type": "info",
            "message": "Help",
            "links": [{"url": "https://example.test", "label": "Help"}],
        },
        {
            "type": "prompt",
            "id": 1,
            "kind": "select",
            "message": "interactive",
            "options": [{"id": "option", "label": "Choice", "description": "Detail"}],
        },
    ]
    async with asyncio.timeout(5):
        for event in expected:
            response = await async_client.post(event_path, headers=headers)
            assert response.status_code == 200
            assert "no-store" in response["Cache-Control"]
            assert response.json() == event
        for prompt_id, answer, status in ((2, "private-answer", 409), (1, "option", 204)):
            response = await async_client.post(
                f"{login}/answers",
                {"prompt_id": prompt_id, "answer": answer},
                content_type="application/json",
                headers=headers,
            )
            assert response.status_code == status
            assert "no-store" in response["Cache-Control"]
            assert answer.encode() not in response.content
        assert (await async_client.post(event_path, headers=headers)).json() == {"type": "done"}
        assert (await async_client.post(event_path, headers=headers)).status_code == 404
    assert (await async_client.get(status_path, headers=headers)).json() == {"connected": True}
    assert (await async_client.delete(status_path, headers=headers)).status_code == 204
    assert (await async_client.get(status_path, headers=headers)).json() == {"connected": False}
    assert "test-code" not in caplog.text
    assert "private diagnostic" not in caplog.text
    assert "private-answer" not in caplog.text


@pytest.mark.usefixtures("helper")
async def test_cancel_through_settings_api(async_client: AsyncClient) -> None:
    headers = {"Authorization": f"Bearer {settings.API_TOKEN}"}
    start = await async_client.post(
        "/v1/providers/vendor/logins",
        {"method": "api_key"},
        content_type="application/json",
        headers=headers,
    )
    login = f"/v1/logins/{start.json()['login_id']}"
    async with asyncio.timeout(5):
        prompt = await async_client.post(f"{login}/next-event", headers=headers)
        assert prompt.json() == {"type": "prompt", "id": 1, "kind": "secret", "message": "vendor"}
        cancelled = await async_client.delete(login, headers=headers)
        assert cancelled.status_code == 204
        assert not cancelled.content
        assert "no-store" in cancelled["Cache-Control"]
        assert (await async_client.post(f"{login}/next-event", headers=headers)).status_code == 404
