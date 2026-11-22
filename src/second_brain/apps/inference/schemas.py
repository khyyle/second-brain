"""Public status of the local models managed by the backend."""

from ninja import Schema

from second_brain.apps.inference.catalog import ModelPurpose
from second_brain.apps.inference.services.worker_status import WorkerStatus


class InferenceStatusOut(Schema):
    """Status of every configured model worker, including workers that are not running."""

    worker_statuses: dict[ModelPurpose, WorkerStatus | None]
