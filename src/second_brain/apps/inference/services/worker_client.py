"""Authenticated generation and embedding requests to local model workers."""

import atexit
from typing import Any, Literal

import httpx
import numpy as np
from django.core.exceptions import ImproperlyConfigured
from numpy.typing import NDArray

from second_brain.apps.inference import INFERENCE_TOKEN_NAME
from second_brain.apps.inference.catalog import LocalModel, get_model
from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.inference.services.worker_status import WorkerStatus, read_worker_status
from second_brain.django_config.identity import private_token
from second_brain.django_config.paths import VaultPaths, get_vault_paths

GENERATION_TIMEOUT_SECONDS = 60

EMBED_TIMEOUT_SECONDS = 30
EMBED_CHUNK_CHARS = 4000
EMBED_MAX_CHUNKS = 12

EmbeddingRole = Literal["search_document", "search_query", "clustering"]

_http_client = httpx.Client(trust_env=False)
atexit.register(_http_client.close)


def generate_json(
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    timeout: float = GENERATION_TIMEOUT_SECONDS,
) -> str:
    """Request JSON-formatted generation from a supported generation model.

    Parameters
    ----------
    model: str
        Model identifier to resolve through the catalog; must support generation.
    prompt: str
        Complete user prompt to send to `/v1/chat/completions` in JSON mode.
    max_tokens: int
        Positive maximum number of output tokens to request.
    timeout: float
        HTTPX network-operation timeout in seconds.

    Returns
    -------
    str
        Generated message content. JSON parsing and triage-field validation are
        separate from extracting the HTTP response's message content.

    Raises
    ------
    InferenceUnavailableError
        The model cannot serve the request or the response envelope is invalid.
    ValueError
        max_tokens is not positive.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive.")
    local_model = get_model(model)
    if local_model.purpose != "generation":
        raise InferenceUnavailableError(f"Local model '{model}' does not support generation.")
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    body = _post(local_model, "/v1/chat/completions", payload, timeout=timeout)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise InferenceUnavailableError("Local inference returned an invalid completion.") from exc
    if not isinstance(content, str):
        raise InferenceUnavailableError("Local inference returned an invalid completion.")
    return content


def embed_text(
    model: str,
    text: str,
    role: EmbeddingRole,
    *,
    timeout: float = EMBED_TIMEOUT_SECONDS,
) -> NDArray[np.float32] | None:
    """Embed bounded text chunks and average their usable vectors.

    Parameters
    ----------
    model: str
        Model identifier to resolve through the catalog; must support embeddings.
    text: str
        Unprefixed text to split into at most 12 chunks of 4,000 characters each.
    role: EmbeddingRole
        Nomic task: search_document for searchable content, search_query for a
        retrieval query, or clustering for grouping related texts. Each chunk's
        input starts with the literal string "{role}: ", followed by its text.
    timeout: float
        HTTPX network-operation timeout in seconds, applied to each chunk request.

    Returns
    -------
    NDArray[np.float32] | None
        One-dimensional, finite float32 vector: the coordinate-wise mean of
        usable chunks, without normalization. Returns None when every chunk
        reaches the model's context limit and may have been truncated. An empty
        input still produces one request containing the task prefix.

    Raises
    ------
    InferenceUnavailableError
        The model is unsupported or unavailable. Runtime failures propagate;
        they are not treated as individual unusable chunks.

    Examples
    --------
    Nomic input strings for the three roles::

        search_document: SQLite allows one writer at a time.
        search_query: How many writers can SQLite support?
        clustering: SQLite allows one writer at a time.

    These prefixes are part of the text sent to Nomic and they follow its task
    instructions:
    https://huggingface.co/nomic-ai/nomic-embed-text-v1.5#task-instruction-prefixes
    """
    local_model = get_model(model)
    if local_model.purpose != "embedding":
        raise InferenceUnavailableError(f"Local model '{model}' does not support embeddings.")
    # Bound both per-request context and total work for unusually long documents.
    end = min(len(text), EMBED_CHUNK_CHARS * EMBED_MAX_CHUNKS)
    chunks = [text[i : i + EMBED_CHUNK_CHARS] for i in range(0, end, EMBED_CHUNK_CHARS)] or [""]

    vectors = [
        vector
        for chunk in chunks
        if (vector := _embed_chunk(local_model, chunk, role, timeout=timeout)) is not None
    ]
    if not vectors:
        return None

    # Accumulate in float64 so averaging finite float32 values cannot overflow.
    return np.mean(vectors, axis=0, dtype=np.float64).astype(np.float32)


def _embed_chunk(
    model: LocalModel,
    text: str,
    role: EmbeddingRole,
    *,
    timeout: float = EMBED_TIMEOUT_SECONDS,
) -> NDArray[np.float32] | None:
    """Request and validate one embedding through `/v1/embeddings`.

    Parameters
    ----------
    model: LocalModel
        Embedding catalog entry with context limit and expected dimensions.
    text: str
        One unprefixed text chunk.
    role: EmbeddingRole
        Nomic task to prepend as "{role}: " before the chunk.
    timeout: float
        HTTPX network-operation timeout in seconds.

    Returns
    -------
    NDArray[np.float32] | None
        Finite vector of the configured dimension, without normalization.
        Returns None when the single-input response reports token usage at or
        above the context limit.

    Raises
    ------
    InferenceUnavailableError
        The worker cannot serve the request or returns an invalid response,
        including missing token usage or an invalid vector.
    """
    body = _post(model, "/v1/embeddings", {"input": f"{role}: {text}"}, timeout=timeout)
    try:
        prompt_tokens = body["usage"]["prompt_tokens"]
        entries = body["data"]
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError("Expected exactly one embedding.")
        embedding = np.asarray(entries[0]["embedding"], dtype=np.float32)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise InferenceUnavailableError("Local inference returned an invalid embedding.") from exc
    if (
        type(prompt_tokens) is not int
        or prompt_tokens < 0
        or embedding.shape != (model.dimensions,)
        or not np.isfinite(embedding).all()
    ):
        raise InferenceUnavailableError("Local inference returned an invalid embedding.")
    # The server reports token usage after truncation - exact fits are indistinguishable
    if prompt_tokens >= model.context_tokens:
        return None
    return embedding


def _post(
    model: LocalModel,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """Send JSON to a local model and return its JSON response.

    Parameters
    ----------
    model: LocalModel
        Catalog entry for the model to call.
    path: str
        Endpoint path beginning with /, such as /v1/chat/completions or /v1/embeddings.
    payload: dict[str, Any]
        Request fields for the endpoint. Omit "model"; it is supplied from model.name.
    timeout: float
        Timeout in seconds for each HTTP network operation.

    Returns
    -------
    dict[str, Any]
        Decoded JSON object from a successful HTTP response.

    Raises
    ------
    InferenceUnavailableError
        The model is unavailable, the request fails, or the response is not a JSON object.

    Examples
    --------
    Example payload for /v1/chat/completions::

        {
            "messages": [
                {"role": "user", "content": "Return a JSON object summarizing SQLite."}
            ],
            "max_tokens": 256,
            "response_format": {"type": "json_object"}
        }

    Example payload for /v1/embeddings::

        {"input": "<text prepared for the selected model>"}
    """
    vault_paths: VaultPaths = get_vault_paths()

    model_status: WorkerStatus | None = read_worker_status(
        directory=vault_paths.backend_dir,
        purpose=model.purpose,
    )
    if model_status is None:
        raise InferenceUnavailableError(
            "Unable to confirm model status. Check if the local inference server is running."
        )
    if model_status.phase != "ready":
        raise InferenceUnavailableError(
            model_status.error
            or f"Local inference is {model_status.phase}. Try again when it is ready."
        )
    if model_status.model != model.name:
        raise InferenceUnavailableError(
            f"Local inference is serving {model_status.model}, "
            f"but this request requires {model.name}."
        )

    try:
        token = private_token(vault_paths.data_dir, INFERENCE_TOKEN_NAME)
    except (OSError, ImproperlyConfigured) as exc:
        raise InferenceUnavailableError(
            "Local inference credentials could not be loaded. Restart Second Brain."
        ) from exc
    try:
        response = _http_client.post(
            f"http://127.0.0.1:{model_status.port}{path}",
            json={**payload, "model": model.name},
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise InferenceUnavailableError("Local inference timed out. Try again.") from exc
    except httpx.RequestError as exc:
        raise InferenceUnavailableError(
            "The connection to local inference failed. Try again."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise InferenceUnavailableError(
            f"Local inference could not complete the request (HTTP {exc.response.status_code}). "
            "Check backend logs."
        ) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise InferenceUnavailableError("Local inference returned invalid JSON.") from exc
    if not isinstance(body, dict):
        raise InferenceUnavailableError(
            "Local inference returned a response that is not a JSON object."
        )
    return body
