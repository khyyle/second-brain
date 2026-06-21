"""
Text embeddings via Ollama, for an optional semantic search layer.
"""

from __future__ import annotations

import logging

import httpx

from second_brain.config import SearchConfig

logger = logging.getLogger(__name__)

EMBED_TIMEOUT_SECONDS = 30
# nomic-embed-text returns HTTP 500 above ~2k tokens; ~4000 chars per
# chunk stays in range even for dense LaTeX or code.
EMBED_CHUNK_CHARS = 4000
# Bound work on pathological inputs (some sources exceed 2M chars).
EMBED_MAX_CHUNKS = 12


def _embed_chunk(text: str, config: SearchConfig) -> list[float] | None:
    """
    Embed a single in-range chunk via Ollama.

    Parameters
    ----------
    text: str
        A chunk small enough to fit the embedder's context window.
    config: SearchConfig
        Embedding model and Ollama host settings.

    Returns
    -------
    list[float] | None
        The embedding vector, or ``None`` if Ollama is unavailable or
        returns no embedding.
    """
    try:
        response = httpx.post(
            f"{config.ollama_host}/api/embeddings",
            json={"model": config.embedding_model, "prompt": text},
            timeout=EMBED_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        embedding = response.json().get("embedding")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.debug("Embedding unavailable (%s)", exc)
        return None

    if not embedding:
        return None
    return [float(value) for value in embedding]


def embed_text(text: str, config: SearchConfig) -> list[float] | None:
    """
    Embed text with the configured Ollama model, chunking long input.

    Input that exceeds one chunk is split, each chunk embedded
    independently, and the chunk vectors mean-pooled into one vector.

    Parameters
    ----------
    text: str
        Text to embed. May exceed the model's context window.
    config: SearchConfig
        Embedding model and Ollama host settings.

    Returns
    -------
    list[float] | None
        The embedding vector, or ``None`` only when every chunk fails
        (e.g. Ollama is unavailable).
    """
    chunks = [
        text[i : i + EMBED_CHUNK_CHARS]
        for i in range(0, len(text), EMBED_CHUNK_CHARS)
    ][:EMBED_MAX_CHUNKS] or [""]

    vectors = [vec for chunk in chunks if (vec := _embed_chunk(chunk, config)) is not None]
    if not vectors:
        return None
    if len(vectors) == 1:
        return vectors[0]

    dimensions = len(vectors[0])
    return [sum(vec[i] for vec in vectors) / len(vectors) for i in range(dimensions)]


def embeddings_available(config: SearchConfig) -> bool:
    """Return ``True`` if the Ollama embedding endpoint responds.

    Parameters
    ----------
    config: SearchConfig
        Embedding model and Ollama host settings.

    Returns
    -------
    bool
        Whether a probe embedding succeeded.
    """
    return embed_text("ping", config) is not None
