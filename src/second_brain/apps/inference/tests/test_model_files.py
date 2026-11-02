import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from second_brain.apps.inference.catalog import MODELS, LocalModel
from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.inference.services import model_files
from second_brain.apps.inference.services.model_files import ModelFiles

Artifact = tuple[LocalModel, ModelFiles, Path, Path, bytes]


@pytest.fixture
def artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Artifact:
    content = b"A small model artifact for integrity tests."
    model = replace(MODELS[0], size=len(content), sha256=hashlib.sha256(content).hexdigest())
    cache = tmp_path / "models"
    blob = cache / "blobs" / model.sha256
    blob.parent.mkdir(parents=True)
    blob.write_bytes(content)
    snapshot = cache / "snapshots" / model.revision / model.filename
    snapshot.parent.mkdir(parents=True)
    snapshot.symlink_to(blob)
    monkeypatch.setattr(
        model_files,
        "try_to_load_from_cache",
        lambda *args, **kwargs: str(snapshot) if snapshot.exists() else None,
    )
    return model, ModelFiles(cache), snapshot, blob, content


def test_verified_cache_is_available_offline(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, files, snapshot, _, _ = artifact
    download = Mock(side_effect=AssertionError("Cached model must not use the network"))
    monkeypatch.setattr(model_files, "hf_hub_download", download)
    progress = Mock()

    assert files.ensure(model, progress) == snapshot
    monkeypatch.setattr(
        model_files.hashlib,
        "file_digest",
        Mock(side_effect=AssertionError("Unchanged model must not be hashed again")),
    )
    assert files.ensure(model, progress) == snapshot

    download.assert_not_called()
    progress.assert_called_with("verifying", model.size, model.size)


@pytest.mark.parametrize("cached", [False, True])
def test_progress_callback_is_optional(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    model, files, snapshot, blob, content = artifact
    if not cached:
        blob.unlink()

    def download(*args: object, **kwargs: object) -> str:
        blob.write_bytes(content)
        return str(snapshot)

    download_mock = Mock(side_effect=download)
    monkeypatch.setattr(model_files, "hf_hub_download", download_mock)

    assert files.ensure(model) == snapshot
    if cached:
        download_mock.assert_not_called()
    else:
        assert download_mock.call_args.kwargs["tqdm_class"] is None


@pytest.mark.parametrize("truncated", [False, True])
def test_corrupt_cache_is_replaced_automatically(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch, truncated: bool
) -> None:
    model, files, snapshot, blob, content = artifact
    blob.write_bytes(b"x" if truncated else b"x" * len(content))
    progress = Mock()

    def replace_blob(*args: object, **kwargs: object) -> str:
        assert blob.exists()
        assert snapshot.is_symlink()
        blob.write_bytes(content)
        return str(snapshot)

    download = Mock(side_effect=replace_blob)
    monkeypatch.setattr(model_files, "hf_hub_download", download)

    assert files.ensure(model, progress) == snapshot
    assert blob.read_bytes() == content
    download.assert_called_once()
    assert download.call_args.kwargs["force_download"] is True
    progress.assert_called_with("verifying", model.size, model.size)


def test_changed_file_is_reverified_and_repaired(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, files, snapshot, blob, content = artifact
    files.ensure(model, Mock())
    blob.write_bytes(b"z" * len(content))

    def replace_blob(*args: object, **kwargs: object) -> str:
        blob.write_bytes(content)
        return str(snapshot)

    download = Mock(side_effect=replace_blob)
    monkeypatch.setattr(model_files, "hf_hub_download", download)

    assert files.ensure(model, Mock()) == snapshot
    download.assert_called_once()
    assert download.call_args.kwargs["force_download"] is True


def test_missing_model_is_downloaded_and_verified(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, files, snapshot, blob, content = artifact
    blob.unlink()

    def write_blob(*args: object, **kwargs: object) -> str:
        blob.write_bytes(content)
        return str(snapshot)

    download = Mock(side_effect=write_blob)
    monkeypatch.setattr(model_files, "hf_hub_download", download)
    progress = Mock()

    assert files.ensure(model, progress) == snapshot
    download.assert_called_once()
    assert download.call_args.args == (model.repository, model.filename)
    assert download.call_args.kwargs["revision"] == model.revision
    assert download.call_args.kwargs["token"] is False
    assert download.call_args.kwargs["force_download"] is False
    progress.assert_called_with("verifying", model.size, model.size)


@pytest.mark.parametrize("cached", [False, True])
def test_failed_replacement_stops_without_deleting_cache_files(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    model, files, snapshot, blob, content = artifact
    if not cached:
        blob.unlink()
    else:
        blob.write_bytes(b"x" * len(content))

    def write_corrupt_blob(*args: object, **kwargs: object) -> str:
        blob.write_bytes(b"x" * len(content))
        return str(snapshot)

    download = Mock(side_effect=write_corrupt_blob)
    monkeypatch.setattr(model_files, "hf_hub_download", download)

    with pytest.raises(
        InferenceUnavailableError, match="failed verification after downloading a replacement"
    ):
        files.ensure(model, Mock())
    assert download.call_count == (1 if cached else 2)
    assert download.call_args.kwargs["force_download"] is True
    assert snapshot.exists()
    assert blob.exists()


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("Offline"),
        OSError("Disk full"),
        RuntimeError("Xet download failed"),
        ValueError("Cannot force a download when offline mode is enabled"),
    ],
)
def test_download_failure_has_a_recoverable_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monkeypatch.setattr(model_files, "try_to_load_from_cache", lambda *args, **kwargs: None)
    download = Mock(side_effect=failure)
    monkeypatch.setattr(model_files, "hf_hub_download", download)

    with pytest.raises(
        InferenceUnavailableError, match="Check the connection and available storage"
    ):
        ModelFiles(tmp_path).ensure(MODELS[0], Mock())

    assert download.call_args.kwargs["revision"] == MODELS[0].revision
    assert download.call_args.kwargs["token"] is False


def test_unreadable_model_does_not_trigger_a_download(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, files, _, _, _ = artifact
    monkeypatch.setattr(
        model_files.hashlib, "file_digest", Mock(side_effect=PermissionError("Not readable"))
    )
    download = Mock()
    monkeypatch.setattr(model_files, "hf_hub_download", download)

    with pytest.raises(InferenceUnavailableError, match="Couldn't read.*storage location"):
        files.ensure(model, Mock())
    download.assert_not_called()


def test_failed_repair_can_succeed_on_a_later_attempt(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, files, snapshot, blob, content = artifact
    blob.write_bytes(b"x" * len(content))
    download = Mock(side_effect=httpx.ConnectError("Offline"))
    monkeypatch.setattr(model_files, "hf_hub_download", download)

    with pytest.raises(InferenceUnavailableError, match="Couldn't download"):
        files.ensure(model, Mock())
    download.assert_called_once()
    assert download.call_args.kwargs["force_download"] is True
    assert snapshot.exists()
    assert blob.exists()

    def replace_blob(*args: object, **kwargs: object) -> str:
        blob.write_bytes(content)
        return str(snapshot)

    download.side_effect = replace_blob
    assert files.ensure(model, Mock()) == snapshot
    assert blob.read_bytes() == content


def test_download_progress_reports_bytes_without_terminal_output() -> None:
    progress = Mock()
    bar_class = model_files._download_progress(progress, 100)
    with bar_class(total=100, initial=25) as bar:
        bar.update(75)
    progress.assert_called_with("downloading", 100, 100)
