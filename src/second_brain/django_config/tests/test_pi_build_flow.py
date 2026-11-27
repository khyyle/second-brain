"""The build worker uses the login saved in Settings and the model chosen on the Build tab."""

import json
import subprocess
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.test import AsyncClient

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import claim_next_job
from second_brain.apps.jobs.worker import run_attempt
from second_brain.cloud_llm.pi import pi_helper
from second_brain.pipeline_config import PipelineConfig

MODEL = "deepseek-v4-pro"


async def test_sign_in_choose_a_model_build_and_find_the_page_committed(
    async_client: AsyncClient,
    config: PipelineConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pi_dir = Path(pi_helper.__file__).parent
    if not (pi_helper.NODE.is_file() and (pi_dir / "node_modules").is_dir()):
        pytest.skip("Run `make setup-pi` to exercise the real provider adapter.")
    # The real helper commands, with a file in tmp_path for Keychain and a scripted provider.
    fixture = pi_dir / "tests" / "build-flow-fixture.mjs"
    helper = tmp_path / "helper.mjs"
    helper.write_text(f"import {json.dumps(fixture.as_uri())};\n")
    monkeypatch.setattr(pi_helper, "PI_HELPER", helper)
    headers = {"Authorization": f"Bearer {settings.API_TOKEN}"}
    options = {"content_type": "application/json", "headers": headers}

    async def sign_in(api_key: str) -> None:
        start = await async_client.post(
            "/v1/providers/deepseek/logins", {"method": "api_key"}, **options
        )
        login = f"/v1/logins/{start.json()['login_id']}"
        prompt = (await async_client.post(f"{login}/next-event", headers=headers)).json()
        assert prompt["kind"] == "secret"
        answer = {"prompt_id": prompt["id"], "answer": api_key}
        assert (await async_client.post(f"{login}/answers", answer, **options)).status_code == 204
        done = await async_client.post(f"{login}/next-event", headers=headers)
        assert done.json() == {"type": "done"}

    async def choose(**compilation: object) -> None:
        saved = await async_client.patch("/v1/settings", {"compilation": compilation}, **options)
        assert saved.status_code == 200, saved.content

    # Settings: sign in to the provider with an API key.
    await sign_in("first-key")
    await choose(provider="deepseek")
    await choose(max_cost_per_build_usd=1.0)

    # Build tab: no model is chosen yet, so a build says so. Then choose one the login offers.
    source = config.raw_dir / "notebook.md"
    source.write_text("The red notebook contains my notes.")
    refused = await async_client.post("/v1/builds", {}, **options)
    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": "Choose a model on the Build tab."},
    )
    offered = (await async_client.get("/v1/providers/deepseek/models", headers=headers)).json()
    other_model = next(model["id"] for model in offered if model["id"] != MODEL)
    assert MODEL in [model["id"] for model in offered]
    await choose(model=MODEL)

    # Build wiki queues the build with that model. Before the worker starts, the person
    # signs in again with another key and picks another model for next time.
    queued = await async_client.post("/v1/builds", {}, **options)
    assert queued.status_code == 202, queued.content
    job = await Job.objects.aget(pk=queued.json()["id"])
    assert "first-key" not in json.dumps(job.config_snapshot)
    await sign_in("replacement-key")
    await choose(model=other_model)

    # The worker asks the queued model with the login saved now, runs the tool call the
    # model makes, and commits the page.
    claimed = await sync_to_async(claim_next_job)("build")
    assert claimed is not None and claimed.pk == job.pk
    await sync_to_async(run_attempt)(claimed)
    await job.arefresh_from_db()
    assert job.status == "succeeded", job.error
    assert job.result["sources_compiled"] == 1
    requests = (tmp_path / "fixture-requests.jsonl").read_text().splitlines()
    assert [json.loads(request) for request in requests] == [
        {"authorization": "Bearer replacement-key", "model": MODEL}
    ] * 2
    committed = subprocess.run(
        ["git", "show", "HEAD:concepts/notebook.md"],
        cwd=config.wiki_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Notes from the red notebook." in committed.stdout
    assert job.progress["cost_usd"] > 0

    # Disconnecting in Settings is all it takes for the next build to be refused.
    disconnected = await async_client.delete("/v1/providers/deepseek/connection", headers=headers)
    assert disconnected.status_code == 204
    source.write_text("The red notebook has a new page.")
    refused = await async_client.post("/v1/builds", {}, **options)
    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": "Connect a provider in Settings."},
    )
