"""Publish inference startup progress and discover its local HTTP endpoint."""

import os
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from second_brain.apps.inference.catalog import ModelPurpose
from second_brain.django_config.vault_storage import atomic_write


class WorkerStatus(BaseModel):
    """
    Last reported inference phase, progress, and process identifiers.

    The port is published after the HTTP server is launched. Ready means its
    authenticated model-list endpoint responded during startup. Error records
    remain available after exit until the next launch replaces them.

    Parameters
    ----------
    purpose: ModelPurpose
        Generation or embedding worker whose status is recorded.
    model: str
        Catalog model identifier, also used as the HTTP server's model alias.
    pid: int
        Process ID of the worker that prepares the model and supervises its server.
    server_pid: int | None
        Process ID of the child HTTP server that loads the model. None before launch.
    port: int | None
        Automatically assigned loopback HTTP port, published during loading
    phase: Literal["starting", "downloading", "verifying", "loading", "ready", "error"]
        Starting initializes model preparation; downloading retrieves model bytes;
        verifying checks their digest; loading waits for the launched server;
        ready records successful startup; error records a preparation or runtime failure.
    completed: int
        Bytes downloaded or verified in the current preparation phase. Verification
        reports zero and the final byte count. Zero outside preparation.
    total: int
        Expected model file size in bytes during preparation. Zero when no byte
        progress is reported, including loading and ready phases.
    error: str | None
        User-facing failure reason for the error phase, or None when no failure
        is reported. Does not contain a traceback.
    """

    model_config = ConfigDict(extra="forbid")

    purpose: ModelPurpose
    model: str
    pid: int = Field(gt=0)
    server_pid: int | None = Field(default=None, gt=0)
    port: int | None = Field(default=None, gt=0, le=65535)
    phase: Literal["starting", "downloading", "verifying", "loading", "ready", "error"]
    completed: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def validate_ready_endpoint(self) -> "WorkerStatus":
        """
        Require endpoint fields on records that claim the worker is ready.

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


def worker_status_path(directory: Path, purpose: ModelPurpose) -> Path:
    """Locate the status file for one model process.

    Parameters
    ----------
    directory: Path
        Vault backend directory.
    purpose: ModelPurpose
        Generation or embedding worker.

    Returns
    -------
    Path
        Status file owned exclusively by that worker.

    Raises
    ------
    ValueError
        The purpose is not supported.
    """
    if purpose not in get_args(ModelPurpose):
        raise ValueError(f"Unsupported inference purpose: {purpose}")
    return directory / f"inference-{purpose}.json"


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
    atomic_write(worker_status_path(directory, status.purpose), status.model_dump_json())


def read_worker_status(directory: Path, purpose: ModelPurpose) -> WorkerStatus | None:
    """Read live inference status or the most recent worker failure.

    Parameters
    ----------
    directory: Path
        Vault backend directory.
    purpose: ModelPurpose
        Generation or embedding worker to inspect.

    Returns
    -------
    WorkerStatus | None
        Valid status, or None if missing, unreadable, invalid, or abandoned by a
        dead process. Error records survive process exit. This does not make an
        HTTP request or guarantee the server can complete the next inference.
    """
    try:
        status: WorkerStatus = WorkerStatus.model_validate_json(
            worker_status_path(directory, purpose).read_text()
        )
        if status.purpose != purpose:
            return None
        if status.phase == "error":
            return status
        os.kill(status.pid, 0)
        if status.server_pid is not None:
            os.kill(status.server_pid, 0)
        return status
    except (OSError, ValidationError):
        return None
