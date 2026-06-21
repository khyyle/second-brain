"""macOS launchd integration — install, uninstall, and status for scheduled runs."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from second_brain.config import Config

logger = logging.getLogger(__name__)

PLIST_LABEL = "com.secondbrain.pipeline"
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"


def _plist_path() -> Path:
    return PLIST_DIR / f"{PLIST_LABEL}.plist"


def _generate_plist(config: Config, project_dir: Path) -> str:
    """
    Generate the launchd plist XML from configuration.

    Parameters
    ----------
    config: Config
        Application config containing schedule hours and log paths.
    project_dir: Path
        Project root where ``run.sh`` lives.

    Returns
    -------
    str
        Complete plist XML string.
    """
    run_script = project_dir / "run.sh"
    logs_dir = config.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)

    intervals = "\n".join(
        f"        <dict>"
        f"<key>Hour</key><integer>{h}</integer>"
        f"<key>Minute</key><integer>0</integer>"
        f"</dict>"
        for h in config.schedule.hours
    )

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{run_script}</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
{intervals}
    </array>
    <key>StandardOutPath</key>
    <string>{logs_dir / "pipeline.log"}</string>
    <key>StandardErrorPath</key>
    <string>{logs_dir / "pipeline.err"}</string>
    <key>WorkingDirectory</key>
    <string>{project_dir}</string>
</dict>
</plist>"""


def install(config: Config, project_dir: Path) -> str:
    """
    Install the launchd plist and load it via launchctl.

    Parameters
    ----------
    config: Config
        Application config with schedule and log directory settings.
    project_dir: Path
        Project root containing ``run.sh``.

    Returns
    -------
    str
        Human-readable status message.
    """
    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    plist = _plist_path()

    run_script = project_dir / "run.sh"
    if not run_script.exists():
        return f"run.sh not found at {run_script} — create it first"

    plist.write_text(_generate_plist(config, project_dir), encoding="utf-8")

    try:
        subprocess.run(
            ["launchctl", "unload", str(plist)],
            capture_output=True,
        )
    except FileNotFoundError:
        pass

    try:
        subprocess.run(
            ["launchctl", "load", str(plist)],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        return f"Failed to load plist: {e.stderr.decode()}"

    logger.info("Installed launchd schedule: %s", plist)
    return f"Installed: {plist}\nSchedule: hours={config.schedule.hours}"


def uninstall() -> str:
    """
    Unload and remove the launchd plist.

    Returns
    -------
    str
        Human-readable status message.
    """
    plist = _plist_path()
    if not plist.exists():
        return "No plist found — nothing to uninstall"

    try:
        subprocess.run(
            ["launchctl", "unload", str(plist)],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        pass

    plist.unlink()
    logger.info("Uninstalled launchd schedule")
    return f"Uninstalled: {plist}"


def status() -> str:
    """
    Check whether the launchd job is loaded and its last run status.

    Returns
    -------
    str
        Human-readable status including launchctl output when active.
    """
    plist = _plist_path()
    if not plist.exists():
        return "Not installed"

    try:
        result = subprocess.run(
            ["launchctl", "list", PLIST_LABEL],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return f"Active\n{result.stdout.strip()}"
        return "Installed but not loaded"
    except FileNotFoundError:
        return "launchctl not available (not macOS?)"
