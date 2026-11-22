"""Django ORM manifest for tracking file ingestion state and deduplication."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from second_brain.apps.ingestion.models import PageCache
from second_brain.apps.ingestion.services.triage.skipped import SKIPPED_DIRNAME
from second_brain.apps.library.models import Source
from second_brain.file_hashing import hash_file

logger = logging.getLogger(__name__)


def raw_output_exists(raw_dir: Path, raw_output: str | None) -> bool:
    """Check for a parsed output, including sources held in the skipped folder.

    Parameters
    ----------
    raw_dir: Path
        Root of the parsed source tree.
    raw_output: str | None
        Output path recorded in the manifest.

    Returns
    -------
    bool
        Whether the parsed source is still available.
    """
    if not raw_output:
        return False
    return (raw_dir / raw_output).exists() or (raw_dir / SKIPPED_DIRNAME / raw_output).exists()


@dataclass(frozen=True)
class ManifestEntry:
    """Read-only snapshot of a manifest row for external consumption."""

    file_path: str
    sha256: str
    source_type: str
    status: str  # pending | processing | complete | failed
    parse_lane: str | None
    raw_output_path: str | None
    ingested_at: str
    updated_at: str
    content_hash: str | None = None
    origin_path: str | None = None
    origin_sha256: str | None = None


@dataclass(frozen=True)
class CachedPage:
    """A previously OCR'd page, keyed by the SHA-256 of its rendered PNG."""

    page_hash: str
    parse_lane: str
    raw_markdown: str
    parsed_at: str


class Manifest:
    """Ingestion persistence through Django's configured database.

    No database path is accepted: the launcher's settings select the one vault.
    Multi-row transitions use short transactions; parsing runs outside them.
    """

    def needs_processing(self, file_path: Path, raw_dir: Path | None = None) -> bool:
        """Check whether a file is new, changed, or missing its parsed output.

        An unchanged failure waits for an explicit retry rather than repeating
        the same attempt on every scan.

        Parameters
        ----------
        file_path: Path
            File to check.
        raw_dir: Path | None
            Raw output root. When provided, a previously-completed file
            whose raw output no longer exists on disk is treated as needing
            re-processing, so deleting raw outputs (e.g. pruning in Finder)
            self-heals on the next re-upload.

        Returns
        -------
        bool
            ``True`` if the file should be (re-)processed.
        """
        current_hash = hash_file(file_path)
        row = Source.objects.filter(pk=str(file_path)).first()
        if row is None:
            return not self._content_already_complete(current_hash, raw_dir)
        if row.sha256 != current_hash:
            return True
        if row.status == "failed":
            return False
        if row.status == "pending":
            return not self._content_already_complete(current_hash, raw_dir)
        if row.status not in ("complete", "duplicate"):
            return True
        return raw_dir is not None and not raw_output_exists(raw_dir, row.raw_output)

    def _content_already_complete(self, sha256: str, raw_dir: Path | None = None) -> bool:
        """Return ``True`` if a completed entry with this content hash still
        has its raw output on disk.

        Parameters
        ----------
        sha256: str
            Content hash to look for.
        raw_dir: Path | None
            Raw output root for existence checks. When None, any completed
            row counts (existence is not verified).

        Returns
        -------
        bool
            Whether identical, still-present content has already been ingested.
        """
        outputs = Source.objects.filter(sha256=sha256, status="complete").values_list(
            "raw_output", flat=True
        )
        if raw_dir is None:
            return outputs.exists()
        return any(raw_output_exists(raw_dir, output) for output in outputs)

    def mark_processing(self, file_path: Path, source_type: str) -> None:
        """Record that a file is being processed (upsert on file_path).

        Uses INSERT ... ON CONFLICT to atomically handle both first-time
        ingestion and re-processing of changed or failed files.

        Parameters
        ----------
        file_path: Path
            File being processed.
        source_type: str
            Label for the source (e.g., `"goodnotes"`).

        Returns
        -------
        None
        """
        sha = hash_file(file_path)
        now = timezone.now()
        Source.objects.update_or_create(
            pk=str(file_path),
            defaults={
                "sha256": sha,
                "source_type": source_type,
                "status": "processing",
                "error": None,
                "updated_at": now,
            },
            create_defaults={
                "sha256": sha,
                "source_type": source_type,
                "status": "processing",
                "ingested_at": now,
                "updated_at": now,
            },
        )

    def mark_complete(
        self,
        file_path: Path,
        parse_lane: str | None = None,
        raw_output: str | None = None,
        content_hash: str | None = None,
    ) -> None:
        """Mark a file as successfully ingested and record its output.

        Parameters
        ----------
        file_path: Path
            File that was ingested.
        parse_lane: str | None
            Parser lane used (e.g., `"docling"`).
        raw_output: str | None
            Path to the raw markdown output file.
        content_hash: str | None
            SHA-256 of the assembled Markdown, recorded separately from the original
            file hash to identify unchanged parsed content.

        Returns
        -------
        None
        """
        values = {
            "status": "complete",
            "parse_lane": parse_lane,
            "raw_output": raw_output,
            "updated_at": timezone.now(),
        }
        if content_hash is not None:
            values["content_hash"] = content_hash
        Source.objects.filter(pk=str(file_path)).update(**values)

    def mark_duplicate(self, file_path: Path, source_type: str) -> None:
        """Record a file whose content was already ingested under another path.

        Existing terminal records are preserved. A pending managed copy can
        become a duplicate while retaining its original-file association.

        Parameters
        ----------
        file_path: Path
            The duplicate file.
        source_type: str
            Label for the source lane it arrived through.

        Returns
        -------
        None
        """
        sha = hash_file(file_path)
        now = timezone.now()
        with transaction.atomic():
            existing = Source.objects.filter(pk=str(file_path)).first()
            if existing is not None and existing.status != "pending":
                return
            output = (
                Source.objects.filter(sha256=sha, status="complete")
                .values_list("raw_output", flat=True)
                .first()
            )
            Source.objects.update_or_create(
                pk=str(file_path),
                defaults={
                    "status": "duplicate",
                    "raw_output": output,
                    "updated_at": now,
                },
                create_defaults={
                    "sha256": sha,
                    "source_type": source_type,
                    "status": "duplicate",
                    "raw_output": output,
                    "updated_at": now,
                    "ingested_at": now,
                },
            )

    def mark_failed(self, file_path: Path, error: str | None = None) -> None:
        """Record a failure that waits for a retry or changed file content.

        Parameters
        ----------
        file_path: Path
            File that failed processing.
        error: str | None
            One-sentence reason shown to the user, or None if unknown.

        Returns
        -------
        None
        """
        Source.objects.filter(pk=str(file_path)).update(
            status="failed", error=error, updated_at=timezone.now()
        )
        if error:
            logger.error("Ingestion failed for %s: %s", file_path, error)

    def get_ingest_failures(self) -> dict[str, str]:
        """Return each failed file that has a recorded reason.

        Returns
        -------
        dict[str, str]
            Absolute file path mapped to why its last ingest failed.
        """
        return dict(
            Source.objects.filter(status="failed", error__isnull=False).values_list(
                "file_path", "error"
            )
        )

    def get_entry(self, file_path: Path) -> ManifestEntry | None:
        """Look up a single file's manifest record.

        Parameters
        ----------
        file_path: Path
            File to look up.

        Returns
        -------
        ManifestEntry | None
            The entry if tracked, otherwise ``None``.
        """
        row = Source.objects.filter(pk=str(file_path)).first()
        return _row_to_entry(row) if row is not None else None

    def get_origin_copy(self, origin_path: Path) -> ManifestEntry | None:
        """Find the managed copy of a watched original.

        Parameters
        ----------
        origin_path: Path
            Absolute path of the watched original.

        Returns
        -------
        ManifestEntry | None
            The copy's current ingestion record, if one exists.
        """
        row = Source.objects.filter(origin_path=str(origin_path)).first()
        return _row_to_entry(row) if row is not None else None

    def record_pending_copy(self, file_path: Path, origin_path: Path, source_type: str) -> None:
        """Record a copied source before it enters parsing.

        The original's captured hash remains independent of later edits to the
        managed copy, so repairing that copy does not trigger a stale recopy.

        Parameters
        ----------
        file_path: Path
            The managed input under drops/.
        origin_path: Path
            Original file from which this version was copied.
        source_type: str
            Source folder's name and parsing settings.

        Returns
        -------
        None
        """
        sha = hash_file(file_path)
        now = timezone.now()
        with transaction.atomic():
            previous = self.get_entry(file_path) or self.get_entry(origin_path)
            Source.objects.filter(pk=str(origin_path)).exclude(pk=str(file_path)).delete()
            values = {
                "sha256": sha,
                "source_type": source_type,
                "status": "pending",
                "error": None,
                "updated_at": now,
                "origin_path": str(origin_path),
                "origin_sha256": sha,
            }
            created = {
                **values,
                "ingested_at": now,
                "parse_lane": previous.parse_lane if previous else None,
                "raw_output": previous.raw_output_path if previous else None,
                "content_hash": previous.content_hash if previous else None,
            }
            Source.objects.update_or_create(
                pk=str(file_path), defaults=values, create_defaults=created
            )

    def source_stems(self, source_type: str) -> set[str]:
        """Return source names reserved by manifest records.

        Parameters
        ----------
        source_type: str
            Source lane whose input names must not be reused.

        Returns
        -------
        set[str]
            Case-insensitive stems, including copies whose parsed output is gone.
        """
        return {
            Path(path).stem.casefold()
            for path in Source.objects.filter(source_type=source_type).values_list(
                "file_path", flat=True
            )
        }

    def get_content_hash(self, file_path: Path) -> str | None:
        """Return the post-OCR content hash recorded for a file, if any.

        Parameters
        ----------
        file_path: Path
            File to look up.

        Returns
        -------
        str | None
            The previously stored assembled-markdown SHA-256, or
            ``None`` when the file has never been processed or was
            stored before content-hash tracking existed.
        """
        return (
            Source.objects.filter(pk=str(file_path)).values_list("content_hash", flat=True).first()
        )

    def remove_entries(self, file_paths: list[Path]) -> int:
        """Delete manifest rows for the given source paths.

        Used when source files are removed (e.g. de-duplication cleanup)
        so the manifest does not retain rows for files that no longer
        exist.

        Parameters
        ----------
        file_paths: list[Path]
            Source paths whose rows should be deleted.

        Returns
        -------
        int
            Number of rows deleted.
        """
        return Source.objects.filter(pk__in=[str(path) for path in file_paths]).delete()[0]

    def count_by_status(self) -> dict[str, int]:
        """Aggregate manifest entries by status.

        Returns
        -------
        dict[str, int]
            Mapping of status label to count.
        """
        return dict(
            Source.objects.values("status")
            .annotate(total=Count("pk"))
            .values_list("status", "total")
        )

    def get_cached_page(self, page_hash: str) -> CachedPage | None:
        """Look up a previously OCR'd page by its rendered-PNG hash.

        Parameters
        ----------
        page_hash: str
            SHA-256 of the page's rendered PNG bytes at ``RENDER_DPI``.

        Returns
        -------
        CachedPage | None
            The cached parse output for this page, or ``None`` on miss.
        """
        row = PageCache.objects.filter(pk=page_hash).first()
        if row is None:
            return None
        return CachedPage(
            row.page_hash, row.parse_lane, row.raw_markdown, row.parsed_at.isoformat()
        )

    def put_cached_page(
        self,
        page_hash: str,
        parse_lane: str,
        raw_markdown: str,
    ) -> None:
        """Store a freshly OCR'd page so future occurrences hit the cache.

        Parameters
        ----------
        page_hash: str
            SHA-256 of the page's rendered PNG bytes.
        parse_lane: str
            Parser used for this page (e.g. `"chandra"`).
        raw_markdown: str
            Markdown extracted by the parser for this single page.

        Returns
        -------
        None
        """
        PageCache.objects.update_or_create(
            pk=page_hash,
            defaults={
                "parse_lane": parse_lane,
                "raw_markdown": raw_markdown,
                "parsed_at": timezone.now(),
            },
        )

    def page_cache_size(self) -> int:
        """Count cached page results.

        Returns
        -------
        int
            Number of rows in the page cache.
        """
        return PageCache.objects.count()


def _row_to_entry(row: Source) -> ManifestEntry:
    """Convert a Django ORM manifest row to a :class:`ManifestEntry`."""
    return ManifestEntry(
        file_path=row.file_path,
        sha256=row.sha256,
        source_type=row.source_type,
        status=row.status,
        parse_lane=row.parse_lane,
        raw_output_path=row.raw_output,
        ingested_at=row.ingested_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        content_hash=row.content_hash,
        origin_path=row.origin_path,
        origin_sha256=row.origin_sha256,
    )
