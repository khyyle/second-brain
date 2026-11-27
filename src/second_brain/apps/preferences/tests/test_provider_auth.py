"""Exercise the real stdio boundary without a network login, Keychain, or Node install."""

import asyncio
import sys
from pathlib import Path

import pytest

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
    print(json.dumps(prompt), flush=True)
    reply = json.loads(input())
    if reply == {'type': 'cancel'}:
        sys.exit(1 if provider == 'failing' else 3)
    if reply != {'type': 'answer', 'id': 1, 'value': provider + '\\nanswer'}:
        sys.exit(1)
elif command == 'list-models':
    choices = {
        'connected': [{'id': 'model/a', 'name': 'A', 'extra': 'not picker data'}],
        'disconnected': [],
        'bad-list': {'error': 'private diagnostic'},
        'bad-choice': [{'id': 'missing-name'}],
    }
    print(json.dumps(choices[provider]))
    sys.exit(0)
elif command == 'logout' and provider == 'unconfirmed':
    print('{}')
    sys.exit(0)
elif command != 'logout':
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
        assert await model_selection.list_models("connected") == [{"id": "model/a", "name": "A"}]
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
