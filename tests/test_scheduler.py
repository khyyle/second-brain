"""Automatic entry points stage sources without starting a wiki build."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from second_brain.config import Config
from second_brain.scheduler.launchd import _generate_plist


def test_scheduled_job_explicitly_runs_ingest(config: Config, tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"

    plist = plistlib.loads(_generate_plist(config, project).encode())

    assert plist["ProgramArguments"] == [str(project / "run.sh"), "ingest"]
    assert plist["StartCalendarInterval"] == [
        {"Hour": hour, "Minute": 0} for hour in config.schedule.hours
    ]


@pytest.mark.parametrize(
    ("stage", "commands"),
    [
        (None, ["ingest"]),
        ("ingest", ["ingest"]),
        ("compile", ["compile"]),
        ("all", ["ingest", "compile"]),
    ],
)
def test_launcher_only_builds_when_explicitly_requested(
    tmp_path: Path, stage: str | None, commands: list[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    script = project / "run.sh"
    shutil.copy2(Path(__file__).parents[1] / "run.sh", script)
    executables = tmp_path / "bin"
    executables.mkdir()
    command_log = tmp_path / "commands"
    uv = executables / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SECOND_BRAIN_COMMAND_LOG"\n', encoding="utf-8"
    )
    uv.chmod(0o755)
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "isolated-home"),
        "PATH": f"{executables}{os.pathsep}{os.environ['PATH']}",
        "SECOND_BRAIN_COMMAND_LOG": str(command_log),
    }

    run = subprocess.run(
        ["/bin/bash", str(script), *([stage] if stage is not None else [])],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert command_log.read_text().splitlines() == [
        f"run second-brain {command}" for command in commands
    ]
