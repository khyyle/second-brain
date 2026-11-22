"""HTTP status of the backend's local inference workers."""

from django.http import HttpRequest
from ninja import Router

from second_brain.apps.inference.schemas import InferenceStatusOut
from second_brain.apps.inference.services.worker_status import read_worker_statuses
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

    return InferenceStatusOut(worker_statuses=read_worker_statuses(vault_paths.backend_dir))
