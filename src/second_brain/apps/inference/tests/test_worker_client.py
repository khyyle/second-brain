import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import numpy as np
import pytest
from django.core.exceptions import ImproperlyConfigured

from second_brain.apps.inference import INFERENCE_TOKEN_NAME
from second_brain.apps.inference.catalog import DEFAULT_MODELS, get_model
from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.inference.services import worker_client
from second_brain.apps.inference.services.worker_status import WorkerStatus, write_worker_status
from second_brain.django_config.identity import private_token


@pytest.fixture
def worker_http(tmp_path: Path, settings, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Provide live status records, a private test token, and intercepted HTTP calls."""
    settings.VAULT_DIR = tmp_path
    private_token(tmp_path, INFERENCE_TOKEN_NAME)
    for purpose, name in DEFAULT_MODELS.items():
        write_worker_status(
            tmp_path / ".backend",
            WorkerStatus(
                purpose=purpose,
                model=name,
                pid=os.getpid(),
                server_pid=os.getpid(),
                port=50111 if purpose == "generation" else 50112,
                phase="ready",
            ),
        )
    client = Mock(spec=httpx.Client)
    monkeypatch.setattr(worker_client, "_http_client", client)
    return client


@pytest.fixture
def embedding_body() -> dict[str, Any]:
    """A complete single-input Nomic embedding response."""
    return {
        "data": [{"embedding": [1.0] * 768, "index": 0}],
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }


def _response(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(body),
        request=httpx.Request("POST", "http://127.0.0.1:50111/v1/test"),
    )


@pytest.mark.parametrize("content", ['{"decision": "review"}', "malformed model output"])
def test_generation_sends_chat_payload_and_leaves_content_parsing_to_caller(
    worker_http: Mock, tmp_path: Path, content: str
) -> None:
    worker_http.post.return_value = _response({"choices": [{"message": {"content": content}}]})

    result = worker_client.generate_json(
        "gemma4:12b", "Classify this source.", max_tokens=80, timeout=7
    )

    assert result == content
    worker_http.post.assert_called_once_with(
        "http://127.0.0.1:50111/v1/chat/completions",
        json={
            "model": "gemma4:12b",
            "messages": [{"role": "user", "content": "Classify this source."}],
            "stream": False,
            "response_format": {"type": "json_object"},
            "max_tokens": 80,
        },
        timeout=7,
        headers={"Authorization": f"Bearer {private_token(tmp_path, INFERENCE_TOKEN_NAME)}"},
    )


@pytest.mark.parametrize(
    "body",
    [{}, {"choices": []}, {"choices": None}, {"choices": [{"message": {"content": None}}]}],
)
def test_invalid_completion_envelope_is_a_runtime_failure(worker_http: Mock, body: Any) -> None:
    worker_http.post.return_value = _response(body)
    with pytest.raises(InferenceUnavailableError, match="invalid completion"):
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)


@pytest.mark.parametrize("max_tokens", [0, -1])
def test_generation_requires_a_positive_output_limit(worker_http: Mock, max_tokens: int) -> None:
    with pytest.raises(ValueError, match="max_tokens must be positive"):
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=max_tokens)
    worker_http.post.assert_not_called()


@pytest.mark.parametrize("model", ["nomic-embed-text", "unknown-model"])
def test_generation_rejects_unsupported_models_before_http(worker_http: Mock, model: str) -> None:
    with pytest.raises(InferenceUnavailableError, match="does not support|not supported"):
        worker_client.generate_json(model, "Prompt", max_tokens=80)
    worker_http.post.assert_not_called()


@pytest.mark.parametrize("model", ["gemma4:12b", "unknown-model"])
def test_embedding_rejects_unsupported_models_before_http(worker_http: Mock, model: str) -> None:
    with pytest.raises(InferenceUnavailableError, match="does not support|not supported"):
        worker_client.embed_text(model, "Text", role="search_document")
    worker_http.post.assert_not_called()


@pytest.mark.parametrize("role", ["search_document", "search_query", "clustering"])
@pytest.mark.parametrize("text", ["SQLite transactions", ""])
def test_embedding_sends_nomic_prefix_and_returns_float32_vector(
    worker_http: Mock, embedding_body: dict[str, Any], role: worker_client.EmbeddingRole, text: str
) -> None:
    worker_http.post.return_value = _response(embedding_body)

    vector = worker_client.embed_text("nomic-embed-text", text, role=role, timeout=9)

    assert isinstance(vector, np.ndarray)
    assert vector.dtype == np.float32
    assert vector.shape == (768,)
    np.testing.assert_array_equal(vector, np.ones(768))
    assert worker_http.post.call_args.args == ("http://127.0.0.1:50112/v1/embeddings",)
    assert worker_http.post.call_args.kwargs["json"] == {
        "model": "nomic-embed-text",
        "input": f"{role}: {text}",
    }
    assert worker_http.post.call_args.kwargs["timeout"] == 9
    assert worker_http.post.call_count == 1


def test_embedding_bounds_chunks_and_averages_without_normalizing(
    worker_http: Mock, embedding_body: dict[str, Any]
) -> None:
    responses = []
    for value in range(1, 13):
        responses.append(
            _response({**embedding_body, "data": [{"embedding": [float(value)] * 768}]})
        )
    worker_http.post.side_effect = responses

    vector = worker_client.embed_text(
        "nomic-embed-text", "x" * (4000 * 13), "clustering", timeout=11
    )

    np.testing.assert_array_equal(vector, np.full(768, 6.5, dtype=np.float32))
    assert worker_http.post.call_count == 12
    for request in worker_http.post.call_args_list:
        assert request.kwargs["json"]["input"] == "clustering: " + "x" * 4000
        assert request.kwargs["timeout"] == 11


def test_averaging_finite_float32_vectors_does_not_overflow(
    worker_http: Mock, embedding_body: dict[str, Any]
) -> None:
    maximum = float(np.finfo(np.float32).max)
    embedding_body["data"] = [{"embedding": [maximum] * 768}]
    worker_http.post.return_value = _response(embedding_body)

    vector = worker_client.embed_text("nomic-embed-text", "x" * 4001, "clustering")

    assert vector.dtype == np.float32
    assert np.isfinite(vector).all()
    np.testing.assert_array_equal(vector, np.full(768, maximum, dtype=np.float32))


@pytest.mark.parametrize("prompt_tokens", [2047, 2048, 2049])
def test_context_limit_is_conservatively_treated_as_truncation(
    worker_http: Mock, embedding_body: dict[str, Any], prompt_tokens: int
) -> None:
    embedding_body["usage"]["prompt_tokens"] = prompt_tokens
    worker_http.post.return_value = _response(embedding_body)

    vector = worker_client.embed_text("nomic-embed-text", "Text", "search_query")

    if prompt_tokens >= 2048:
        assert vector is None
    else:
        np.testing.assert_array_equal(vector, np.ones(768))


def test_truncated_chunks_are_excluded_from_the_mean(
    worker_http: Mock, embedding_body: dict[str, Any]
) -> None:
    worker_http.post.side_effect = [
        _response({**embedding_body, "usage": {"prompt_tokens": 2048}}),
        _response({**embedding_body, "data": [{"embedding": [3.0] * 768}]}),
    ]

    vector = worker_client.embed_text("nomic-embed-text", "x" * 4001, "search_document")

    np.testing.assert_array_equal(vector, np.full(768, 3.0, dtype=np.float32))


def test_failure_after_a_successful_chunk_propagates_instead_of_returning_partial_vector(
    worker_http: Mock, embedding_body: dict[str, Any]
) -> None:
    failure = httpx.ReadTimeout("Private request details")
    worker_http.post.side_effect = [_response(embedding_body), failure]

    with pytest.raises(InferenceUnavailableError, match="timed out") as error:
        worker_client.embed_text("nomic-embed-text", "x" * 8001, "search_document")

    assert error.value.__cause__ is failure
    assert worker_http.post.call_count == 2


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"usage": {}},
        {"usage": {"prompt_tokens": -1}},
        {"usage": {"prompt_tokens": True}},
        {"usage": {"prompt_tokens": "10"}},
        {"data": []},
        {"data": [{}, {}]},
        {"data": [{}]},
        {"data": [{"embedding": []}]},
        {"data": [{"embedding": [1.0, 2.0]}]},
        {"data": [{"embedding": [[1.0] * 768]}]},
        {"data": [{"embedding": ["invalid"] * 768}]},
        {"data": [{"embedding": [float("nan")] * 768}]},
        {"data": [{"embedding": [float("inf")] * 768}]},
    ],
)
def test_invalid_embeddings_are_failures_rather_than_skippable_chunks(
    worker_http: Mock, embedding_body: dict[str, Any], invalid_fields: dict[str, Any]
) -> None:
    worker_http.post.return_value = _response({**embedding_body, **invalid_fields})
    with pytest.raises(InferenceUnavailableError, match="invalid embedding"):
        worker_client.embed_text("nomic-embed-text", "Text", "search_document")


@pytest.mark.parametrize(
    "phase,reason",
    [("downloading", None), ("loading", None), ("error", "Model download failed.")],
)
def test_non_ready_status_preserves_worker_failure_reason(
    worker_http: Mock, tmp_path: Path, phase: str, reason: str | None
) -> None:
    write_worker_status(
        tmp_path / ".backend",
        WorkerStatus(
            purpose="generation", model="gemma4:12b", pid=os.getpid(), phase=phase, error=reason
        ),
    )
    with pytest.raises(InferenceUnavailableError, match=reason or f"is {phase}"):
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)
    worker_http.post.assert_not_called()


def test_missing_status_prevents_http(worker_http: Mock, tmp_path: Path) -> None:
    (tmp_path / ".backend" / "inference-generation.json").unlink()
    with pytest.raises(InferenceUnavailableError, match="Unable to confirm model status"):
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)
    worker_http.post.assert_not_called()


def test_wrong_loaded_model_prevents_http(worker_http: Mock, tmp_path: Path) -> None:
    write_worker_status(
        tmp_path / ".backend",
        WorkerStatus(
            purpose="generation",
            model="another-model",
            pid=os.getpid(),
            server_pid=os.getpid(),
            port=50111,
            phase="ready",
        ),
    )
    with pytest.raises(
        InferenceUnavailableError, match="serving another-model.*requires gemma4:12b"
    ):
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)
    worker_http.post.assert_not_called()


@pytest.mark.parametrize(
    "failure", [PermissionError("Private path"), ImproperlyConfigured("Empty token")]
)
def test_credential_failures_have_a_public_reason(
    worker_http: Mock, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monkeypatch.setattr(worker_client, "private_token", Mock(side_effect=failure))
    with pytest.raises(
        InferenceUnavailableError, match="credentials could not be loaded"
    ) as error:
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)
    assert error.value.__cause__ is failure
    worker_http.post.assert_not_called()


@pytest.mark.parametrize("status", [302, 401, 422, 500])
def test_http_errors_are_not_retried_or_exposed_as_completion_data(
    worker_http: Mock, status: int
) -> None:
    worker_http.post.return_value = _response({"detail": "Private request details"}, status)
    with pytest.raises(InferenceUnavailableError, match=f"HTTP {status}") as error:
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)
    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)
    assert "Private request details" not in error.value.reason
    assert worker_http.post.call_count == 1


@pytest.mark.parametrize(
    "failure,reason",
    [(httpx.ConnectError("Details"), "connection"), (httpx.ReadTimeout("Details"), "timed out")],
)
def test_transport_failures_preserve_cause(
    worker_http: Mock, failure: Exception, reason: str
) -> None:
    worker_http.post.side_effect = failure
    with pytest.raises(InferenceUnavailableError, match=reason) as error:
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)
    assert error.value.__cause__ is failure
    assert worker_http.post.call_count == 1


@pytest.mark.parametrize("content", [b"not json", b"null", b"[]", b'"text"'])
def test_response_must_be_a_json_object(worker_http: Mock, content: bytes) -> None:
    worker_http.post.return_value = httpx.Response(
        200, content=content, request=httpx.Request("POST", "http://127.0.0.1:50111/v1/test")
    )
    with pytest.raises(InferenceUnavailableError, match="JSON"):
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)


def test_each_request_reads_current_port_and_credentials(
    worker_http: Mock, tmp_path: Path
) -> None:
    worker_http.post.return_value = _response({"choices": [{"message": {"content": "{}"}}]})
    worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)
    write_worker_status(
        tmp_path / ".backend",
        WorkerStatus(
            purpose="generation",
            model="gemma4:12b",
            pid=os.getpid(),
            server_pid=os.getpid(),
            port=50144,
            phase="ready",
        ),
    )
    (tmp_path / ".backend" / INFERENCE_TOKEN_NAME).write_text("replacement-test-token")

    worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)

    assert worker_http.post.call_args.args == ("http://127.0.0.1:50144/v1/chat/completions",)
    assert worker_http.post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer replacement-test-token"
    }


def test_expected_inference_failure_is_saved_as_the_job_error(
    worker_http: Mock, job_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from second_brain.apps.jobs import worker as job_worker
    from second_brain.apps.jobs.models import Job
    from second_brain.apps.jobs.services import job_execution

    worker_http.post.side_effect = httpx.ConnectError("Private connection details")

    def execute(job: Job) -> dict:
        worker_client.generate_json("gemma4:12b", "Prompt", max_tokens=80)
        return {}

    monkeypatch.setattr(job_execution, "execute_job", execute)
    job = job_factory(status=Job.Status.RUNNING)
    job_worker.run_attempt(job)
    job.refresh_from_db()

    assert job.status == Job.Status.FAILED
    assert job.error == "The connection to local inference failed. Try again."


def test_post_does_not_mutate_the_payload(worker_http: Mock) -> None:
    worker_http.post.return_value = _response({})
    payload = {"input": "Prepared text"}

    worker_client._post(get_model("nomic-embed-text"), "/v1/embeddings", payload, timeout=1)

    assert payload == {"input": "Prepared text"}
