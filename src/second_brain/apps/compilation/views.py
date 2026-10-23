"""HTTP entry points for build planning and compilation."""

from django.http import HttpRequest
from ninja import Router

from second_brain.apps.compilation.schemas import BuildRequest, GroupingOverrides, GroupingPlan
from second_brain.apps.jobs.schemas import JobOut
from second_brain.django_config.scaffold import ErrorResponse, pending

router = Router(tags=["Compilation"])


@router.post("/builds", response={202: JobOut, 501: ErrorResponse})
def request_build(request: HttpRequest, payload: BuildRequest) -> tuple[int, JobOut]:
    """TODO: Create a build Job and immutable input/config/grouping snapshot, then enqueue.

    Port cli.compile; workers call services.compiler.run_compilation. Capture source
    hashes with the bytes being compiled. Keep model calls outside DB transactions.
    """
    pending("Request build")


@router.post("/grouping-plan/preview", response={202: JobOut, 501: ErrorResponse})
def preview_grouping(request: HttpRequest) -> tuple[int, JobOut]:
    """TODO: Port cli.preview_clusters; enqueue services.clustering.preview.build_preview.

    Optional grouping applies to chats; individual documents stay separate inputs.
    """
    pending("Preview grouping")


@router.get("/grouping-plan", response={200: GroupingPlan | None, 501: ErrorResponse})
def get_grouping(request: HttpRequest) -> GroupingPlan | None:
    """TODO: Combine services.clustering.preview.load_preview and saved overrides.

    Return null when no preview exists; library /state reports when a plan is stale.
    """
    pending("Read grouping plan")


@router.put("/grouping-plan/overrides", response={200: GroupingOverrides, 501: ErrorResponse})
def update_grouping(request: HttpRequest, payload: GroupingOverrides) -> GroupingOverrides:
    """TODO: Port ClusterPlan's override writes; validate IDs against the current preview.

    Persist atomically. 'excluded' means remove from a group and compile individually.
    """
    pending("Update grouping overrides")


@router.delete("/grouping-plan", response={204: None, 501: ErrorResponse})
def clear_grouping(request: HttpRequest) -> None:
    """TODO: Call services.clustering.preview.clear_preview through a mutation service."""
    pending("Clear grouping plan")
