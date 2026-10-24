"""HTTP entry points for build planning and compilation."""

from django.http import HttpRequest
from ninja import Router, Status

from second_brain.apps.compilation.schemas import (
    BuildAction,
    BuildRequest,
    GroupingOverrides,
    GroupingPlan,
)
from second_brain.apps.compilation.services import build_log, build_requests
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.schemas import JobOut
from second_brain.django_config.schemas import ErrorResponse

router = Router(tags=["Compilation"])


@router.post(
    "/builds",
    response={
        202: JobOut,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        500: ErrorResponse,
        503: ErrorResponse,
    },
)
def request_build(request: HttpRequest, payload: BuildRequest) -> Status[Job]:
    """Capture eligible sources and preferences, then queue a manual build.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    payload: BuildRequest
        Full-build and dry-run options.

    Returns
    -------
    Status[Job]
        HTTP 202 containing the queued job with captured build inputs.
    """
    return Status(202, build_requests.request_build(**payload.model_dump()))


@router.post(
    "/grouping-plan/preview",
    response={202: JobOut, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def preview_grouping(request: HttpRequest) -> Status[Job]:
    """Queue an optional chat-grouping preview from the current eligible sources.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    Status[Job]
        HTTP 202 containing the queued grouping-preview job.
    """
    return Status(202, build_requests.request_build(preview=True))


@router.get(
    "/grouping-plan",
    response={
        200: GroupingPlan | None,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
    },
)
def get_grouping(request: HttpRequest) -> dict | None:
    """Read the saved grouping preview and its user overrides.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    dict | None
        Saved preview and overrides, or None if no preview exists.
    """
    return build_requests.get_grouping()


@router.put(
    "/grouping-plan/overrides",
    response={200: GroupingOverrides, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def update_grouping(request: HttpRequest, payload: GroupingOverrides) -> dict:
    """Validate and save exclusions and split groups for the current preview.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    payload: GroupingOverrides
        Excluded source paths and groups to split in the current preview.

    Returns
    -------
    dict
        Validated, persisted overrides.
    """
    return build_requests.update_grouping(payload.model_dump())


@router.delete(
    "/grouping-plan",
    response={204: None, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def clear_grouping(request: HttpRequest) -> Status[None]:
    """Discard the saved preview and overrides without changing source eligibility.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    Status[None]
        HTTP 204 with no body after removing the preview and overrides.
    """
    build_requests.clear_grouping()
    return Status(204, None)


@router.get("/builds/actions", response=list[BuildAction])
def list_build_actions(request: HttpRequest) -> list[dict]:
    """List recent committed page creations and updates.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    list[dict]
        Recorded page creations and updates, newest first.
    """
    return build_log.list_build_actions()
