"""Exercise terminal commands through HTTP encoding, Django views, and isolated vaults."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner
from django.utils import timezone

from second_brain.apps.compilation.models import CompiledSource, DeferredSource
from second_brain.apps.jobs.models import Job
from second_brain.apps.library.models import TriageDecision
from second_brain.apps.wiki.services.git_history import commit_all
from second_brain.cli import main


@pytest.fixture
def cli_backend(config, client, settings, monkeypatch):
    """Replace the socket transport with Django while retaining HTTP request encoding."""
    token = config.data_dir / ".backend" / "api-token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(settings.API_TOKEN)
    requests = []

    def dispatch(request):
        assert request.url.host == "127.0.0.1"
        assert request.headers["authorization"] == f"Bearer {settings.API_TOKEN}"
        requests.append(request)
        response = client.generic(
            request.method,
            request.url.raw_path.decode("ascii"),
            data=request.content,
            content_type=request.headers.get("content-type", "application/json"),
            HTTP_AUTHORIZATION=request.headers["authorization"],
        )
        return httpx.Response(response.status_code, content=response.content, request=request)

    @contextmanager
    def connection(**options):
        with httpx.MockTransport(dispatch) as transport:

            def request(method, url, **kwargs):
                return transport.handle_request(httpx.Request(method, url, **kwargs))

            yield SimpleNamespace(request=request)

    monkeypatch.setattr("second_brain.backend_client.httpx.Client", connection)

    def invoke(*args, input=None):
        return CliRunner().invoke(main, ["--vault", str(config.data_dir), *args], input=input)

    return SimpleNamespace(invoke=invoke, requests=requests)


def test_forget_accepts_literal_reserved_characters_and_preserves_other_sources(
    cli_backend, config
):
    relative = "documents/a & b #1?.md"
    source = config.raw_dir / relative
    source.parent.mkdir(parents=True)
    source.write_text("Remove this vault copy")
    other = source.parent / "a.md"
    other.write_text("Keep this")
    CompiledSource.objects.create(raw_path=relative, compiled_at=timezone.now())

    result = cli_backend.invoke("forget", relative)

    assert result.exit_code == 0, result.output
    assert f"Removed {relative}" in result.output
    assert not source.exists()
    assert other.read_text() == "Keep this"
    assert not CompiledSource.objects.filter(pk=relative).exists()
    trashed = next((config.data_dir / ".trash").rglob(source.name))
    assert trashed.read_text() == "Remove this vault copy"
    assert cli_backend.requests[0].url.params["raw_path"] == relative


def test_import_then_forget_drop_preserves_original(cli_backend, config, tmp_path):
    original = tmp_path / "notes & tasks #1.md"
    original.write_text("Original notes")
    submitted = cli_backend.invoke("ingest", "--path", str(original), "--json")
    assert submitted.exit_code == 0, submitted.output
    result = json.loads(submitted.output)
    job = Job.objects.get(pk=result["job"]["id"])
    assert job.kind == "ingest"
    copied = result["files"][0]["file_path"]
    assert copied != str(original)
    assert cli_backend.invoke("jobs", "cancel", str(job.pk)).exit_code == 0

    removed = cli_backend.invoke("forget-drop", copied)

    assert removed.exit_code == 0, removed.output
    assert original.read_text() == "Original notes"
    assert not Path(copied).exists()
    assert list((config.data_dir / ".trash").rglob("*.md"))


def test_chatgpt_option_takes_the_export_path(cli_backend, tmp_path):
    export = tmp_path / "conversations.json"
    export.write_text("[]")
    result = cli_backend.invoke("ingest", "--chatgpt", str(export), "--json")
    assert result.exit_code == 0, result.output
    job = Job.objects.get(pk=json.loads(result.output)["job"]["id"])
    assert len(job.payload["paths"]) == 1
    assert "/drops/chatgpt/" in job.payload["paths"][0]
    assert export.read_text() == "[]"


def test_recompile_restages_only_selected_versions(cli_backend, config):
    relatives = ["documents/first.md", "documents/second & third.md", "documents/keep.md"]
    for relative in relatives:
        file = config.raw_dir / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("Source")
        CompiledSource.objects.create(raw_path=relative, compiled_at=timezone.now())
        DeferredSource.objects.create(
            raw_path=relative, reason="Retry", deferred_at=timezone.now()
        )

    result = cli_backend.invoke("recompile", *relatives[:2])

    assert result.exit_code == 0, result.output
    assert list(CompiledSource.objects.values_list("raw_path", flat=True)) == relatives[2:]
    assert list(DeferredSource.objects.values_list("raw_path", flat=True)) == relatives[2:]
    assert not Job.objects.exists()


def test_triage_set_moves_the_copy_and_records_the_decision(cli_backend, config):
    relative = "chatgpt/a & b.md"
    source = config.raw_dir / relative
    source.parent.mkdir(parents=True)
    source.write_text("Chat")

    skipped = cli_backend.invoke("triage", "set", relative, "skip")
    assert skipped.exit_code == 0, skipped.output
    assert not source.exists()
    assert (config.raw_dir / ".skipped" / relative).read_text() == "Chat"
    assert TriageDecision.objects.get(pk=relative).decision == "skip"

    restored = cli_backend.invoke("triage", "set", relative, "worthwhile")
    assert restored.exit_code == 0, restored.output
    assert source.read_text() == "Chat"
    verdicts = cli_backend.invoke("triage", "list", "--decision", "worthwhile", "--json")
    assert json.loads(verdicts.output)[0]["raw_path"] == relative


def test_job_commands_submit_inspect_cancel_and_retry(cli_backend):
    submitted = cli_backend.invoke("compile", "--dry-run", "--json")
    assert submitted.exit_code == 0, submitted.output
    job_id = json.loads(submitted.output)["id"]
    assert Job.objects.get(pk=job_id).payload["dry_run"] is True

    shown = cli_backend.invoke("jobs", "show", job_id, "--json")
    assert json.loads(shown.output)["job"]["status"] == "queued"
    listed = cli_backend.invoke("jobs", "list", "--lane", "build", "--status", "queued", "--json")
    assert [job["id"] for job in json.loads(listed.output)["items"]] == [job_id]
    cancelled = cli_backend.invoke("jobs", "cancel", job_id)
    assert cancelled.exit_code == 0, cancelled.output
    assert "cancelled" in cancelled.output
    retried = cli_backend.invoke("jobs", "retry", job_id, "--json")
    assert retried.exit_code == 0, retried.output
    retry = json.loads(retried.output)
    assert retry["id"] != job_id
    assert retry["retry_of"] == job_id
    assert retry["status"] == "queued"


@pytest.mark.parametrize(
    "command",
    [
        ["status"],
        ["status", "--sources"],
        ["state"],
        ["query", "state & serialization"],
        ["health"],
        ["domain", "list"],
        ["wiki", "log", "-n", "3"],
        ["triage", "list"],
    ],
)
def test_read_commands_support_text_and_json(cli_backend, config, command):
    page = config.wiki_dir / "concepts" / "example.md"
    page.write_text("---\ntitle: Example\ntype: concept\ndomains: [testing]\n---\nBody\n")
    commit_all(config.wiki_dir, "Test wiki")
    plain = cli_backend.invoke(*command)
    assert plain.exit_code == 0, plain.output
    assert plain.output.strip()
    structured = cli_backend.invoke(*command, "--json")
    assert structured.exit_code == 0, structured.output
    assert isinstance(json.loads(structured.output), (dict, list))


def test_domain_commands_update_wiki_metadata(cli_backend, config):
    page = config.wiki_dir / "concepts" / "example.md"
    page.write_text("---\ntitle: Example\ntype: concept\ndomains: [old, spare]\n---\nBody\n")

    renamed = cli_backend.invoke("domain", "rename", "old", "research & development")
    assert renamed.exit_code == 0, renamed.output
    assert "research & development" in page.read_text()
    merged = cli_backend.invoke("domain", "merge", "combined", "research & development", "spare")
    assert merged.exit_code == 0, merged.output
    assert "combined" in page.read_text()
    deleted = cli_backend.invoke("domain", "delete", "combined")
    assert deleted.exit_code == 0, deleted.output
    assert "combined" not in page.read_text()
    assert "Body" in page.read_text()


def test_wiki_rollback_confirmation_stops_before_request(cli_backend):
    result = cli_backend.invoke("wiki", "rollback", "2", input="n\n")
    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert not cli_backend.requests


def test_wiki_rollback_yes_restores_the_previous_checkpoint(cli_backend, config):
    page = config.wiki_dir / "concepts" / "example.md"
    original = "---\ntitle: Example\ntype: concept\ndomains: [testing]\n---\nOriginal\n"
    page.write_text(original)
    commit_all(config.wiki_dir, "Original")
    page.write_text(original.replace("Original", "Changed"))
    commit_all(config.wiki_dir, "Changed")

    result = cli_backend.invoke("wiki", "rollback", "1", "--yes")

    assert result.exit_code == 0, result.output
    assert "Rolled back 1 commit(s)." in result.output
    assert page.read_text() == original


def test_preview_submits_a_job_without_building(cli_backend):
    result = cli_backend.invoke("preview-clusters", "--json")
    assert result.exit_code == 0, result.output
    job = Job.objects.get(pk=json.loads(result.output)["id"])
    assert job.kind == "preview"
    assert job.status == "queued"


def test_wiki_merge_and_dismiss_use_the_backend(cli_backend, config):
    from second_brain.apps.wiki.services.duplicate_dismissals import DuplicateStore

    for slug in ("kept", "duplicate", "third"):
        (config.wiki_dir / "concepts" / f"{slug}.md").write_text(
            f"---\ntitle: {slug}\ntype: concept\ndomains: [testing]\n---\n[[duplicate]]\n"
        )
    commit_all(config.wiki_dir, "Initial pages")
    dismissed = cli_backend.invoke("wiki", "dismiss", "kept", "third")
    assert dismissed.exit_code == 0, dismissed.output
    assert ("kept", "third") in DuplicateStore().get_dismissed()

    merged = cli_backend.invoke("wiki", "merge", "kept", "duplicate")
    assert merged.exit_code == 0, merged.output
    assert not (config.wiki_dir / "concepts" / "duplicate.md").exists()
    assert "[[kept]]" in (config.wiki_dir / "concepts" / "third.md").read_text()


def test_repair_links_dry_run_preserves_content(cli_backend, config):
    moved = config.wiki_dir / "concepts" / "before.md"
    moved.write_text("---\ntitle: Before\ntype: concept\ndomains: [testing]\n---\nContent\n")
    referrer = config.wiki_dir / "concepts" / "referrer.md"
    referrer.write_text(
        "---\ntitle: Referrer\ntype: concept\ndomains: [testing]\n---\n[[before]]\n"
    )
    commit_all(config.wiki_dir, "Before rename")
    moved.rename(moved.with_name("after.md"))

    dry_run = cli_backend.invoke("wiki", "repair-links", "--dry-run", "--json")
    assert dry_run.exit_code == 0, dry_run.output
    assert sum(item["links_repointed"] for item in json.loads(dry_run.output)) == 1
    assert "[[before]]" in referrer.read_text()
    applied = cli_backend.invoke("wiki", "repair-links")
    assert applied.exit_code == 0, applied.output
    assert "[[after]]" in referrer.read_text()


def test_mcp_install_retains_the_target_option(cli_backend, monkeypatch):
    targets = []

    def install(target):
        targets.append(target)
        return {
            "target": target,
            "installed": True,
            "configured": True,
            "message": "Configured Cursor. Restart it to connect.",
        }

    monkeypatch.setattr("second_brain.apps.preferences.services.mcp_integrations.install", install)
    result = cli_backend.invoke("mcp", "install", "--target", "cursor")
    assert result.exit_code == 0, result.output
    assert targets == ["cursor"]
    assert "Configured Cursor. Restart it to connect." in result.output


def test_server_rejection_is_a_readable_nonzero_cli_error(cli_backend, job_factory, config):
    relative = "documents/active.md"
    source = config.raw_dir / relative
    source.parent.mkdir(parents=True)
    source.write_text("Captured")
    job_factory(payload={"sources": [relative]})

    result = cli_backend.invoke("forget", relative)

    assert result.exit_code == 1
    assert "These sources belong to an active job. Finish or cancel it first." in result.output
    assert "Traceback" not in result.output
    assert source.exists()


@pytest.mark.parametrize(
    "command",
    [
        ["ingest", "--path", "notes.md", "--chatgpt", "conversations.json"],
        ["ingest", "--chatgpt", "conversations.json", "--drops-only"],
        ["jobs", "cancel", "not-a-job-id"],
        ["recompile"],
        ["triage", "set", "chatgpt/a.md", "invalid"],
        ["wiki", "log", "-n", "0"],
    ],
)
def test_invalid_arguments_do_not_send_requests(cli_backend, command):
    result = cli_backend.invoke(*command)
    assert result.exit_code == 2
    assert not cli_backend.requests
