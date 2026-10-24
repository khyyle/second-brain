"""Application-owned choices, independent of editable preferences."""

from second_brain.apps.ingestion.services.source_staging import SUPPORTED_EXTENSIONS
from second_brain.llm import SUPPORTED_MODELS, resolve_profile
from second_brain.pipeline_config import _CLUSTERING_ALGORITHMS, _TRIAGE_PROFILES


def get_catalog() -> dict:
    """Return model pricing and supported parser, triage, and grouping choices.

    Returns
    -------
    dict
        Providers and models with prices and context limits, accepted file types,
        triage profiles, and clustering algorithms.
    """
    providers = []
    for provider, models in SUPPORTED_MODELS.items():
        entries = []
        for model in models:
            profile = resolve_profile(provider, model)
            entries.append(
                {
                    "id": model,
                    "input_price_per_mtok": profile.input_price_per_mtok,
                    "output_price_per_mtok": profile.output_price_per_mtok,
                    "context_window_tokens": profile.context_window_tokens,
                }
            )
        providers.append({"id": provider, "name": provider.title(), "models": entries})
    return {
        "providers": providers,
        "file_types": sorted(e.lstrip(".") for e in SUPPORTED_EXTENSIONS),
        "triage_profiles": list(_TRIAGE_PROFILES),
        "clustering_algorithms": list(_CLUSTERING_ALGORITHMS),
    }
