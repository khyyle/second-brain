"""HTTP status of the backend's local inference workers."""

from typing import get_args

from django.http import HttpRequest
from ninja import Router

from second_brain.apps.inference.catalog import ModelPurpose
from second_brain.apps.inference.schemas import InferenceStatusOut
from second_brain.apps.inference.services.worker_status import WorkerStatus, read_worker_status
from second_brain.django_config.paths import VaultPaths, get_vault_paths

router = Router(tags=["Inference"])


@router.get("/inference/status", response=InferenceStatusOut)
def inference_status(request: HttpRequest) -> InferenceStatusOut:
    """Report all configured model workers without starting or loading models.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    InferenceStatusOut
        A status entry for each configured worker. Worker failures are included
        in the HTTP 200 response.
    """
    vault_paths: VaultPaths = get_vault_paths()

    worker_statuses: dict[ModelPurpose, WorkerStatus | None] = {}
    for purpose in get_args(ModelPurpose):
        worker_status: WorkerStatus | None = read_worker_status(
            directory=vault_paths.backend_dir,
            purpose=purpose,
        )

        worker_statuses[purpose] = worker_status

    return InferenceStatusOut(worker_statuses=worker_statuses)
