"""One desktop read model, shared by the JSON endpoint and SSE snapshots."""

from ninja import Schema

from second_brain.apps.compilation.schemas import BuildAction, GroupingPlan
from second_brain.apps.ingestion.schemas import ImportFile
from second_brain.apps.jobs.schemas import JobProgress
from second_brain.apps.library.schemas import LibraryState, TriageOut


class DesktopState(Schema):
    state: LibraryState
    jobs: list[JobProgress]
    imports: list[ImportFile]
    triage: list[TriageOut]
    grouping_plan: GroupingPlan | None
    build_actions: list[BuildAction]
    compilation_model: str
