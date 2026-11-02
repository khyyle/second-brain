"""Recoverable local-model setup and execution failures."""

from second_brain.errors import SecondBrainError


class InferenceUnavailableError(SecondBrainError):
    """The local model cannot serve requests until setup or runtime recovery succeeds."""
