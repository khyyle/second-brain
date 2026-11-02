"""Download pinned model files and verify them before native code reads them."""

import hashlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from huggingface_hub import constants, hf_hub_download, try_to_load_from_cache
from huggingface_hub.utils import tqdm

from second_brain.apps.inference.catalog import LocalModel
from second_brain.apps.inference.errors import InferenceUnavailableError

logger = logging.getLogger(__name__)
PROGRESS_INTERVAL_SECONDS = 0.5
ModelProgress = Callable[[str, int, int], None]


class ModelFiles:
    """
    Verified model artifacts in a managed Hugging Face cache.

    Parameters
    ----------
    directory: Path | None
        Cache directory override. None uses Hugging Face's configured Hub cache.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory if directory is not None else Path(constants.HF_HUB_CACHE)
        self._verified: dict[Path, tuple[str, int, int, int]] = {}

    def cached_path(self, model: LocalModel) -> Path | None:
        """
        Locate a cached artifact without making a network request.

        Parameters
        ----------
        model: LocalModel
            Pinned model artifact to locate.

        Returns
        -------
        Path | None
            Cached file, or None.
        """
        cached = try_to_load_from_cache(
            model.repository,
            model.filename,
            revision=model.revision,
            cache_dir=self.directory,
        )
        return Path(cached) if isinstance(cached, str) else None

    def ensure(self, model: LocalModel, progress: ModelProgress | None = None) -> Path:
        """
        Prepare a verified model file, downloading one replacement if the cached file
        is corrupt. Replacing a file requires a connection.

        Parameters
        ----------
        model: LocalModel
            Pinned model artifact.
        progress: ModelProgress | None
            Receives phase (downloading or verifying), completed bytes, and total bytes.
            None uses Hugging Face's standard download progress display.

        Returns
        -------
        Path
            Verified model file.

        Raises
        ------
        InferenceUnavailableError
            Storage or download failed, or the replacement also failed verification.
        """
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.cached_path(model)
        except OSError as exc:
            raise InferenceUnavailableError("The local model storage is not accessible.") from exc
        if path is None:
            path = self._download(model, progress, force=False)
        if self._is_verified(path, model, progress):
            return path

        logger.warning("Cached %s failed verification, downloading a replacement", model.title)
        path = self._download(model, progress, force=True)
        if not self._is_verified(path, model, progress):
            raise InferenceUnavailableError(
                f"{model.title} failed verification after downloading a replacement. "
                "The model cannot be loaded."
            )
        return path

    def _download(self, model: LocalModel, progress: ModelProgress | None, *, force: bool) -> Path:
        if progress is not None:
            progress("downloading", 0, model.size)
        try:
            return Path(
                hf_hub_download(
                    model.repository,
                    model.filename,
                    revision=model.revision,
                    cache_dir=self.directory,
                    force_download=force,
                    token=False,
                    tqdm_class=(
                        _download_progress(progress, model.size) if progress is not None else None
                    ),
                )
            )
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            logger.exception("Could not download %s", model.title)
            raise InferenceUnavailableError(
                f"Couldn't download {model.title}. "
                "Check the connection and available storage, then retry."
            ) from exc

    def _is_verified(self, path: Path, model: LocalModel, progress: ModelProgress | None) -> bool:
        if progress is not None:
            progress("verifying", 0, model.size)
        try:
            stat = path.stat()
            if stat.st_size != model.size:
                return False
            signature = (model.sha256, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if self._verified.get(path) != signature:
                with path.open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != model.sha256:
                        return False
        except OSError as exc:
            raise InferenceUnavailableError(
                f"Couldn't read {model.title}. Check its storage location and retry."
            ) from exc
        self._verified[path] = signature
        if progress is not None:
            progress("verifying", model.size, model.size)
        return True


def _download_progress(progress: ModelProgress, total: int) -> type[tqdm]:
    class DownloadProgress(tqdm):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)
            self.completed = int(kwargs.get("initial", 0))
            self.last_report = 0.0

        def update(self, amount: int = 1) -> None:
            self.completed += amount
            now = time.monotonic()
            if now - self.last_report >= PROGRESS_INTERVAL_SECONDS or self.completed >= total:
                progress("downloading", min(self.completed, total), total)
                self.last_report = now

    return DownloadProgress
