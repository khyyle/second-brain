"""Recoverable local-model setup and execution failures."""

from second_brain.django_config.errors import DependencyError


class InferenceUnavailableError(DependencyError):
    """The local model cannot serve requests until setup or runtime recovery succeeds."""
