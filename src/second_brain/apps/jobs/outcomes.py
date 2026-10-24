"""Outcome vocabulary shared by ingestion, compilation, and durable job results."""

from enum import Enum

CRASH_REASON = "An unexpected error occurred. For details, see the backend logs."


class StageOutcome(Enum):
    """How a single stage ended; distinct from whether its worker is still running."""

    OK = "ok"
    PARTIAL = "partial"
    CAPPED = "capped"
    FAILED = "failed"
    STOPPED = "stopped"
