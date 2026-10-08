"""Text embeddings via Ollama for semantic retrieval and source clustering."""

from __future__ import annotations

import logging
from typing import Literal

import httpx

from second_brain.config import SearchConfig
from second_brain.ollama import post_to_ollama

logger = logging.getLogger(__name__)

EMBED_TIMEOUT_SECONDS = 30
# nomic-embed-text returns HTTP 500 above ~2k tokens; ~4000 chars per
# chunk stays in range even for dense LaTeX or code.
EMBED_CHUNK_CHARS = 4000
# Bound work on pathological inputs (some sources exceed 2M chars).
EMBED_MAX_CHUNKS = 12


EmbeddingRole = Literal["search_document", "search_query", "clustering"]


def _embed_chunk(
    text: str,
    config: SearchConfig,
    role: EmbeddingRole,
) -> list[float] | None:
    """
    Embed a single in-range chunk via Ollama.

    Parameters
    ----------
    text: str
        A chunk small enough to fit the embedder's context window.
    config: SearchConfig
        Embedding model and Ollama host settings.
    role: EmbeddingRole
        Intended role of the vector in downstream comparisons.

    Returns
    -------
    list[float] | None
        The embedding vector, or ``None`` when this text has no usable
        embedding (including an oversized chunk, which the embedder
        rejects on its own).

    Raises
    ------
    OllamaUnavailableError
        When the server cannot be reached or the model is not installed.
        Later chunks would fail the same way.
    """
    try:
        body = post_to_ollama(
            config.ollama_host,
            "/api/embeddings",
            {
                "model": config.embedding_model,
                "prompt": f"{role}: {text}",
            },
            EMBED_TIMEOUT_SECONDS,
        )
        embedding = body.get("embedding")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.debug("Embedding unavailable (%s)", exc)
        return None

    if not embedding:
        return None
    try:
        return [float(value) for value in embedding]
    except (TypeError, ValueError):
        return None


def embed_text(
    text: str,
    config: SearchConfig,
    role: EmbeddingRole,
) -> list[float] | None:
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
    role: EmbeddingRole
        Intended role of the resulting vector in downstream comparisons.
        Use "search_document" for indexed wiki pages,
        "search_query" for retrieval queries,
        "clustering" for grouping topically related sources

    Returns
    -------
    list[float] | None
        The embedding vector, or ``None`` when every chunk fails for this
        text alone.

    Raises
    ------
    OllamaUnavailableError
        When Ollama cannot serve the request. A dead server is not a
        per-text failure, so it propagates.
    """
    chunks = [text[i : i + EMBED_CHUNK_CHARS] for i in range(0, len(text), EMBED_CHUNK_CHARS)][
        :EMBED_MAX_CHUNKS
    ] or [""]

    vectors = [
        vector for chunk in chunks if (vector := _embed_chunk(chunk, config, role)) is not None
    ]
    if not vectors:
        return None
    if len(vectors) == 1:
        return vectors[0]

    dimensions = len(vectors[0])
    return [sum(vec[i] for vec in vectors) / len(vectors) for i in range(dimensions)]
