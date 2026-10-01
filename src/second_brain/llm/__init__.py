"""Model provider profiles, the Messages API client, and classified provider errors."""

from second_brain.llm.client import create_client, request_turn, require_api_key
from second_brain.llm.errors import ModelError, ModelErrorKind, classify_status
from second_brain.llm.providers import SUPPORTED_MODELS, ProviderProfile, resolve_profile

__all__ = [
    "SUPPORTED_MODELS",
    "ModelError",
    "ModelErrorKind",
    "ProviderProfile",
    "classify_status",
    "create_client",
    "request_turn",
    "require_api_key",
    "resolve_profile",
]
