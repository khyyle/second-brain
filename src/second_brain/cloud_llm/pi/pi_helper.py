"""Python wrapper for the adjacent Pi CLI over private stdio.

The process returned by ``spawn`` belongs to the caller. ``run_json`` owns its process.
"""

import asyncio
import json
import sys
from asyncio.subprocess import DEVNULL, PIPE, Process
from contextlib import suppress
from pathlib import Path

from second_brain.errors import SecondBrainError

NODE = Path(sys.executable).parent / "node"  # Beside the backend Python, not on PATH.
PI_HELPER = Path(__file__).parent / "pi-helper.mjs"
LOGIN_CANCELLED = 3  # Only an acknowledged abort, not a sign-in or Keychain failure.


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
