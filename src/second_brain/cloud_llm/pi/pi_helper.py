"""Python wrapper for the adjacent Pi CLI over private stdio.

The process returned by ``spawn`` belongs to the caller. ``run_json`` and ``run_turn``
own their process.
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from second_brain.errors import SecondBrainError

NODE = Path(sys.executable).parent / "node"  # Beside the backend Python, not on PATH.
PI_HELPER = Path(__file__).parent / "pi-helper.mjs"
LOGIN_CANCELLED = 3  # Only an acknowledged abort, not a sign-in or Keychain failure.

# Provider SDKs read keys, headers and endpoints from the environment on their own, and Node
# reads its options there. Only the saved login may decide those, so the helper inherits
# none of the backend's variables.
HELPER_ENV = {
    "HOME": str(Path.home()),  # Locates the login Keychain.
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    # A saved AWS profile that has since been deleted must fail, not become a machine role.
    "AWS_EC2_METADATA_DISABLED": "true",
}
# The helper asks for at most 16,384 output tokens, which a slow model can take minutes to write.
TURN_TIMEOUT_SECONDS = 600
_STOP_POLL_SECONDS = 0.5


class PiHelperError(SecondBrainError):
    """The helper could not complete a command or return a valid response."""


class PiUsageError(PiHelperError):
    """The caller supplied arguments that the helper does not accept."""


async def spawn(*args: str, interactive: bool = False) -> Process:
    """Start a helper with piped stdout and discarded stderr; interactive commands get stdin."""
    if any(not arg or "\0" in arg for arg in args):
        raise PiUsageError("Invalid provider or login method.")
    try:
        return await asyncio.create_subprocess_exec(
            NODE,
            PI_HELPER,
            *args,
            stdin=PIPE if interactive else DEVNULL,
            stdout=PIPE,
            stderr=DEVNULL,
            env=HELPER_ENV,
        )
    except OSError as exc:
        raise PiHelperError("Could not start the provider helper. Check installation.") from exc


def check_exit(code: int) -> None:
    if code == 2:
        raise PiUsageError("Unsupported provider or login method.")
    if code != 0:
        raise PiHelperError("The provider helper could not complete the operation.")


async def run_json(*args: str) -> object:
    """Return decoded JSON from a successful one-shot command without validating its schema."""
    process = await spawn(*args)
    try:
        async with asyncio.timeout(30):
            stdout, _ = await process.communicate()
        assert process.returncode is not None
        check_exit(process.returncode)
        return json.loads(stdout)
    except TimeoutError:
        raise PiHelperError("The provider helper timed out. Check macOS Keychain.") from None
    except ValueError:
        raise PiHelperError("Invalid response from the provider helper.") from None
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.communicate()


def run_turn(
    provider: str, model: str, context: dict, stop_requested: Callable[[], bool]
) -> dict | None:
    """Send one turn of a build conversation to the chosen model and wait for the reply.

    The build worker is ordinary blocking code, so this does not use the event loop.

    Parameters
    ----------
    provider: str
        Pi catalog ID of the provider whose saved login makes the request.
    model: str
        That provider's ID for the chosen model.
    context: dict
        Pi context: ``systemPrompt``, ``tools`` and the ``messages`` so far.
    stop_requested: Callable[[], bool]
        Polled while waiting. Once true, the request in flight is abandoned.

    Returns
    -------
    dict | None
        The helper's result: ``outcome``, and with it the assistant ``message`` to append
        or the reason there is none, plus ``cost_usd`` (None when unknown). None if the
        turn was stopped.

    Raises
    ------
    PiHelperError
        The helper could not start, failed, returned invalid JSON, or the provider took
        longer than ``TURN_TIMEOUT_SECONDS``.
    """
    # Unnamed files instead of pipes: a context can be megabytes, and a pipe would need
    # both ends serviced while this thread also watches for Stop.
    with tempfile.TemporaryFile() as request, tempfile.TemporaryFile() as reply:
        request.write(json.dumps(context).encode())
        request.seek(0)
        try:
            process = subprocess.Popen(
                [NODE, PI_HELPER, "build-turn", provider, model],
                stdin=request,
                stdout=reply,
                stderr=subprocess.DEVNULL,
                env=HELPER_ENV,
            )
        except OSError as exc:
            raise PiHelperError(
                "Could not start the provider helper. Check installation."
            ) from exc
        deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
        try:
            while True:
                try:
                    process.wait(_STOP_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    if stop_requested():
                        return None
                    if time.monotonic() > deadline:
                        raise PiHelperError(
                            "The model provider took too long to answer. Build again."
                        ) from None
        finally:
            if process.poll() is None:
                # The only way to end a request the helper has already sent.
                process.kill()
                process.wait()
        check_exit(process.returncode)
        reply.seek(0)
        try:
            return json.load(reply)
        except ValueError:
            raise PiHelperError("Invalid response from the provider helper.") from None
