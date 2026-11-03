"""The status endpoint reports both workers, including absent and failed workers."""

import os

from second_brain.apps.inference.services.worker_status import WorkerStatus, write_worker_status


def test_status_includes_unavailable_workers(api_client):
    response = api_client.get("/v1/inference/status")

    assert response.status_code == 200
    assert response.json() == {"worker_statuses": {"generation": None, "embedding": None}}


def test_worker_failure_is_status_data_not_an_http_error(api_client, config):
    generation = WorkerStatus(
        purpose="generation",
        model="gemma4:12b",
        pid=os.getpid(),
        server_pid=os.getpid(),
        port=51234,
        phase="ready",
    )
    embedding = WorkerStatus(
        purpose="embedding",
        model="nomic-embed-text",
        pid=123,
        phase="error",
        error="Model download failed.",
    )
    directory = config.data_dir / ".backend"
    directory.mkdir(exist_ok=True)
    write_worker_status(directory, generation)
    write_worker_status(directory, embedding)

    response = api_client.get("/v1/inference/status")

    assert response.status_code == 200
    assert response.json() == {
        "worker_statuses": {
            "generation": generation.model_dump(mode="json"),
            "embedding": embedding.model_dump(mode="json"),
        }
    }


def test_status_requires_authentication(client):
    assert client.get("/v1/inference/status").status_code == 401
