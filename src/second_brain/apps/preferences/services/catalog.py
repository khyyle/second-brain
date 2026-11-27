"""Application-owned choices, independent of editable preferences."""

from second_brain.apps.ingestion.services.source_staging import SUPPORTED_EXTENSIONS
from second_brain.pipeline_config import _CLUSTERING_ALGORITHMS, _TRIAGE_PROFILES


def get_catalog() -> dict:
    """Return supported parser, triage, and grouping choices.

    Returns
    -------
    dict
        Accepted file types, triage profiles, and clustering algorithms.
    """
    return {
        "file_types": sorted(e.lstrip(".") for e in SUPPORTED_EXTENSIONS),
        "triage_profiles": list(_TRIAGE_PROFILES),
        "clustering_algorithms": list(_CLUSTERING_ALGORITHMS),
    }
