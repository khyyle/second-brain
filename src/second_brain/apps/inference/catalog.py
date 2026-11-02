"""Pinned model artifacts and their runtime requirements."""

from dataclasses import dataclass
from typing import Literal

from second_brain.apps.inference.errors import InferenceUnavailableError


@dataclass(frozen=True)
class LocalModel:
    """An immutable model artifact and its execution settings.

    Parameters
    ----------
    name: str
        Identifier stored in preferences and job snapshots.
    title: str
        Display name.
    purpose: Literal["generation", "embedding"]
        Supported operation.
    repository: str
        Public Hugging Face repository.
    revision: str
        Repository commit containing the artifact.
    filename: str
        GGUF filename within the repository.
    sha256: str
        Expected content digest.
    size: int
        Expected file size in bytes.
    context_tokens: int
        Maximum context allocated by the runtime.
    dimensions: int | None
        Expected embedding length, or None for generation.
    """

    name: str
    title: str
    purpose: Literal["generation", "embedding"]
    repository: str
    revision: str
    filename: str
    sha256: str
    size: int
    context_tokens: int
    dimensions: int | None = None


MODELS = (
    LocalModel(
        name="gemma4:12b",
        title="Gemma 4 12B",
        purpose="generation",
        repository="ggml-org/gemma-4-12B-it-GGUF",
        revision="202658970530b31541cd0b645a9c198bed2a615e",
        filename="gemma-4-12B-it-Q4_0.gguf",
        sha256="3712b9bd32cae83a22f67ee7a4466d8d7a4f21646ac8a07d19bf9418e8767a70",
        size=7219673216,
        context_tokens=8192,
    ),
    LocalModel(
        name="nomic-embed-text",
        title="Nomic Embed Text v1.5",
        purpose="embedding",
        repository="nomic-ai/nomic-embed-text-v1.5-GGUF",
        revision="0188c9bf409793f810680a5a431e7b899c46104c",
        filename="nomic-embed-text-v1.5.f16.gguf",
        sha256="f7af6f66802f4df86eda10fe9bbcfc75c39562bed48ef6ace719a251cf1c2fdb",
        size=274290560,
        context_tokens=2048,
        dimensions=768,
    ),
)


def get_model(name: str) -> LocalModel:
    """Resolve a supported model identifier.

    Parameters
    ----------
    name: str
        Model identifier from preferences or a job snapshot.

    Returns
    -------
    LocalModel
        Pinned artifact and execution settings.

    Raises
    ------
    InferenceUnavailableError
        The identifier has no supported local artifact.
    """
    for model in MODELS:
        if model.name == name:
            return model
    raise InferenceUnavailableError(
        f"Local model '{name}' is not supported. Choose one of: "
        + ", ".join(model.name for model in MODELS)
        + "."
    )
