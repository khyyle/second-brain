#!/usr/bin/env python3
"""Build the GUI and run it against the local Django backend using one startup environment."""

import hashlib
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType

import httpx

from second_brain.backend_client import BackendClient
from second_brain.django_config.environment import load_environment

ROOT = Path(__file__).resolve().parent


def main() -> None:
    """Build a debug app bundle and run it against the selected local backend.

    Loads repository startup settings, reuses a responsive backend or starts one,
    and waits for the GUI to exit. On exit or interruption, terminates only the
    processes started here.

    Returns
    -------
    None
    """
    load_environment()
    client = BackendClient()
    environment = os.environ.copy()
    environment.update(
        SECOND_BRAIN_VAULT=str(client.vault),
        SECOND_BRAIN_PORT=str(client.port),
        SECOND_BRAIN_BACKEND_SCRIPT=str(ROOT / "run.sh"),
    )
    package = str(ROOT / "gui" / "SecondBrainBar")
    subprocess.run(["swift", "build", "--package-path", package], check=True)
    binary_directory = subprocess.check_output(
        ["swift", "build", "--package-path", package, "--show-bin-path"], text=True
    ).strip()
    # A real debug bundle gives macOS a distinct app identity, menus, and accessibility.
    identity = hashlib.sha256(f"{client.vault}:{client.port}".encode()).hexdigest()[:12]
    bundle = (
        ROOT / "gui" / "SecondBrainBar" / ".build" / "dev-apps" / identity / "Second Brain Dev.app"
    )
    contents = bundle / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(exist_ok=True)
    shutil.copy2(Path(binary_directory) / "SecondBrainBar", contents / "MacOS" / "SecondBrainBar")
    resources = ROOT / "gui" / "SecondBrainBar" / "Resources"
    info = plistlib.loads((resources / "Info.plist").read_bytes())
    info.update(
        CFBundleIdentifier=f"com.secondbrain.development.{identity}",
        CFBundleName="Second Brain Dev",
    )
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    for name in ("MenuBarIcon.png",):
        if (resources / name).exists():
            shutil.copy2(resources / name, contents / "Resources" / name)
    backend = None
    gui = None

    def ready() -> bool:
        try:
            client.request("GET", "/jobs", params={"limit": 1})
            return True
        except (OSError, ValueError, RuntimeError, httpx.HTTPError):
            return False

    def stop(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if not ready():
            backend = subprocess.Popen([sys.executable, str(ROOT / "start.py")], env=environment)
            deadline = time.monotonic() + 30
            while not ready():
                if backend.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("The backend did not become ready. See its output above.")
                time.sleep(0.2)
        print(f"GUI: http://127.0.0.1:{client.port}/v1 | vault: {client.vault}", flush=True)
        gui = subprocess.Popen([str(contents / "MacOS" / "SecondBrainBar")], env=environment)
        gui.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for process in (gui, backend):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
