"""Sign-in operations for Settings, on the desktop backend's single ASGI event loop."""

from __future__ import annotations

import asyncio
import json
from asyncio.subprocess import Process
from contextlib import suppress
from uuid import uuid4

from second_brain.apps.preferences.services.agent_provider._errors import settings_errors
from second_brain.cloud_llm.pi import pi_helper
from second_brain.django_config.errors import (
    ConflictError,
    DependencyError,
    NotFoundError,
    ServiceError,
)

# Pi times out after five minutes. Release the pipes even if Settings never reads the result.
_LOGIN_LIFETIME_SECONDS = 6 * 60
_sessions: dict[str, _LoginSession] = {}


async def start_login(provider_id: str, auth_method: str | None = None) -> str:
    """
    Start a provider login without waiting for the user to sign in.

    Parameters
    ----------
    provider_id : str
        Provider ID from the Pi catalog.
    auth_method : str or None, optional
        ``"oauth"`` or ``"api_key"``. The default is OAuth if supported, otherwise
        API-key login if available. A failed login does not switch methods.

    Returns
    -------
    str
        Opaque ID for this login. Provider and method checks may still be pending;
        ``read_login_event`` reports their outcome.

    Raises
    ------
    ServiceError
        A supplied argument is empty or contains a null character.
    DependencyError
        The backend could not start the sign-in helper.
    """
    args = (provider_id,) if auth_method is None else (provider_id, auth_method)
    with settings_errors():
        process = await pi_helper.spawn("login", *args, interactive=True)
    login_id = uuid4().hex
    _sessions[login_id] = _LoginSession(login_id, process)
    return login_id


async def read_login_event(login_id: str) -> dict[str, object]:
    """Wait for and consume the next event for a login.

    Parameters
    ----------
    login_id : str
        ID returned by ``start_login``. Only one read may be pending per login.

    Returns
    -------
    dict[str, object]
        ``type`` identifies the event. ``auth_url``, ``device_code``, ``info`` and
        ``progress`` carry provider instructions or status. URLs and codes are sensitive.
        A ``prompt`` includes a ``kind`` and the integer ``id`` accepted by ``answer_login``.
        Browser sign-in may complete without a prompt answer. ``done`` confirms a Keychain
        save; ``cancelled`` reports cancellation to a pending reader. Both are terminal.

    Raises
    ------
    NotFoundError
        The login ID is unknown or no longer active.
    ConflictError
        Another caller is already waiting for an event on this login.
    ServiceError
        Pi rejected the provider or login method.
    DependencyError
        Sign-in failed or timed out, or the helper did not return a valid event.
    """
    with settings_errors():
        return await _session(login_id).read_event()


async def answer_login(login_id: str, prompt_id: int, answer: str) -> None:
    """Submit an answer to a pending prompt without waiting for login to finish.

    Parameters
    ----------
    login_id : str
        Login ID returned by ``start_login``.
    prompt_id : int
        The ``id`` from a ``prompt`` event for this login. Each prompt accepts one reply.
    answer : str
        Prompt input, or the option ID for a selection prompt. May contain an API key
        or authorization code.

    Raises
    ------
    NotFoundError
        The login ID is unknown or no longer active.
    ConflictError
        The prompt is not pending, or the login is no longer accepting answers.
    """
    await _session(login_id).answer(prompt_id, answer)


async def cancel_login(login_id: str) -> None:
    """
    Stop a pending sign-in without removing a saved credential.

    Parameters
    ----------
    login_id : str
        ID of the pending login, not the provider ID.

    Raises
    ------
    NotFoundError
        The login ID is unknown or no longer active.
    ConflictError
        Cancellation is already in progress, or the helper is no longer accepting it.
    ServiceError
        Pi rejected the provider or login method before cancellation completed.
    DependencyError
        The helper failed or timed out before confirming cancellation.
    """
    with settings_errors():
        await _session(login_id).cancel()


async def logout(provider_id: str) -> None:
    """
    Remove a provider's saved credential from macOS Keychain.

    This does not revoke access at the provider or cancel an in-progress login.
    A login that finishes later can save a new credential. If no credential is
    saved, the call succeeds without changing Keychain.

    Parameters
    ----------
    provider_id : str
        ID of the provider to disconnect, not a login ID.

    Raises
    ------
    ServiceError
        The provider ID is invalid or unknown.
    DependencyError
        The helper could not start or confirm removal of the local credential.
    """
    with settings_errors():
        result = await pi_helper.run_json("logout", provider_id)
    if result != {"type": "done"}:
        raise DependencyError("Disconnect was not confirmed by the provider helper.")


def _session(login_id: str) -> _LoginSession:
    session = _sessions.get(login_id)
    if session is None:
        raise NotFoundError("Login has finished or expired. Start a new login.")
    return session


class _LoginSession:
    def __init__(self, login_id: str, process: Process) -> None:
        self._id = login_id
        self._process = process
        assert process.stdout is not None and process.stdin is not None
        self._stdout = process.stdout
        self._stdin = process.stdin
        self._prompt_id: int | None = None
        self._reading = False
        self._cancelling = False
        self._save_reported = False
        self._expiry = asyncio.get_running_loop().call_later(_LOGIN_LIFETIME_SECONDS, self.close)

    async def read_event(self) -> dict[str, object]:
        if self._reading:
            raise ConflictError("Already waiting for the next login event.")
        self._reading = True
        try:
            if not self._save_reported:
                line = await self._stdout.readline()
                if line:
                    event = self._decode_event(line)
                    if event["type"] != "done":
                        return event
                    # A request can disconnect during wait(). The next reader still owns 'done'.
                    self._save_reported = True
                    self._prompt_id = None
                    self._stdin.close()

            code = await self._process.wait()
            self.close()
            if self._cancelling and code == pi_helper.LOGIN_CANCELLED:
                return {"type": "cancelled"}
            pi_helper.check_exit(code)
            if not self._save_reported:
                raise DependencyError("Sign-in ended without confirmation.")
            return {"type": "done"}
        except ValueError:
            self.close()
            raise DependencyError("Invalid response from the provider sign-in helper.") from None
        except (ServiceError, pi_helper.PiHelperError):
            self.close()
            raise
        finally:
            self._reading = False

    def _decode_event(self, line: bytes) -> dict[str, object]:
        event = json.loads(line)
        if not isinstance(event, dict) or event.get("type") not in (
            "auth_url",
            "device_code",
            "info",
            "progress",
            "prompt",
            "done",
        ):
            raise ValueError
        if event["type"] == "prompt":
            prompt_id = event.get("id")
            if type(prompt_id) is not int or prompt_id < 1:
                raise ValueError
            self._prompt_id = prompt_id
        return event

    async def answer(self, prompt_id: int, value: str) -> None:
        if self._cancelling or self._prompt_id is None or self._prompt_id != prompt_id:
            raise ConflictError("That login prompt is no longer pending.")
        self._prompt_id = None  # Consume before yielding; reject a concurrent duplicate answer.
        await self._send({"type": "answer", "id": prompt_id, "value": value})

    async def cancel(self) -> None:
        if self._cancelling:
            raise ConflictError("Cancellation is already in progress.")
        self._cancelling = True
        try:
            async with asyncio.timeout(3):
                await self._send({"type": "cancel"})
                code = await self._process.wait()
            if code == 0:
                raise ConflictError("Sign-in already completed. Disconnect to remove it.")
            if code != pi_helper.LOGIN_CANCELLED:
                pi_helper.check_exit(code)
        except TimeoutError:
            raise DependencyError("The sign-in helper did not confirm cancellation.") from None
        finally:
            self.close()

    async def _send(self, message: dict[str, object]) -> None:
        try:
            self._stdin.write(json.dumps(message).encode() + b"\n")
            await self._stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise ConflictError("The sign-in helper is no longer accepting replies.") from None

    def close(self) -> None:
        """Forget the ID and release the child on success, failure, cancellation, or expiry."""
        if _sessions.pop(self._id, None) is None:
            return
        self._expiry.cancel()
        self._stdin.close()
        if self._process.returncode is None:
            with suppress(ProcessLookupError):
                self._process.kill()
