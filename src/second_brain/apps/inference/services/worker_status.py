"""Publish inference startup progress and discover its local HTTP endpoint."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from second_brain.django_config.vault_storage import atomic_write


class WorkerStatus(BaseModel):
    """
    Last reported inference phase, progress, and process identifiers.

    The port is published after the HTTP server is launched. Ready means its
    authenticated model-list endpoint responded during startup. Error records
    remain available after exit until the next launch replaces them.
    """

    model_config = ConfigDict(extra="forbid")

    pid: int = Field(gt=0)
    server_pid: int | None = Field(default=None, gt=0)
    port: int | None = Field(default=None, gt=0, le=65535)
    phase: Literal["starting", "downloading", "verifying", "loading", "ready", "error"]
    model: str | None = None
    completed: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def validate_ready_endpoint(self) -> "WorkerStatus":
        """Validate the endpoint information required by a ready worker.

        Returns
        -------
        WorkerStatus
            This status after validation.

        Raises
        ------
        ValueError
            Ready status is missing the server PID or port.
        """
        if self.phase == "ready" and (self.server_pid is None or self.port is None):
            raise ValueError("Ready inference requires a server PID and port.")
        return self


def write_worker_status(directory: Path, status: WorkerStatus) -> None:
    """Atomically publish an owner-only status file.

    Parameters
    ----------
    directory: Path
        Vault backend directory.
    status: WorkerStatus
        Current process identifiers, preparation progress, or terminal failure.

    Returns
    -------
    None
    """
    # Readers see either complete snapshot, never a partially written JSON document.
    atomic_write(directory / "inference.json", status.model_dump_json())


def read_worker_status(directory: Path) -> WorkerStatus | None:
    """Read live inference status or the most recent worker failure.

    Parameters
    ----------
    directory: Path
        Vault backend directory containing inference.json.

    Returns
    -------
    WorkerStatus | None
        Valid status, or None if missing, unreadable, invalid, or abandoned by a
        dead process. Error records survive process exit. This does not make an
        HTTP request or guarantee the server can complete the next inference.
    """
    try:
        status = WorkerStatus.model_validate_json((directory / "inference.json").read_text())
        if status.phase == "error":
            return status
        os.kill(status.pid, 0)
        if status.server_pid is not None:
            os.kill(status.server_pid, 0)
        return status
    except (OSError, ValidationError):
        return None
