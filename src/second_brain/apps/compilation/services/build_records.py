"""Build records: which raw sources are compiled into the wiki, and which were set aside."""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from second_brain.apps.compilation.models import CompiledSource
from second_brain.apps.compilation.models import DeferredSource as DeferredRecord


@dataclass(frozen=True)
class DeferredSource:
    """A source a build set aside, and the version of it that was set aside."""

    reason: str
    retryable: bool
    content_hash: str | None


class BuildStore:
    """
    Reads and writes the ``compiled`` and ``deferred_sources`` tables.

    Uses the database selected in Django settings.
    """

    def mark_compiled(self, source_hashes: dict[str, str]) -> None:
        """Record that the given raw files were compiled into the wiki.

        Parameters
        ----------
        source_hashes: dict[str, str]
            Raw file path (relative to the raw directory) mapped to the
            content hash of the version the build read.

        Returns
        -------
        None
        """
        now = timezone.now()
        CompiledSource.objects.bulk_create(
            [
                CompiledSource(raw_path=path, content_hash=digest, compiled_at=now)
                for path, digest in source_hashes.items()
            ],
            update_conflicts=True,
            unique_fields=["raw_path"],
            update_fields=["content_hash", "compiled_at"],
        )

    def unmark_compiled(self, raw_paths: list[str]) -> int:
        """
        Clear the compiled record for the given raw files.

        These sources become eligible for the next normal build again, while
        their ingestion and triage records stay intact.

        Parameters
        ----------
        raw_paths: list[str]
            Raw file paths (relative to the raw directory) to unmark.

        Returns
        -------
        int
            Number of compiled records actually cleared.
        """
        return CompiledSource.objects.filter(pk__in=raw_paths).delete()[0]

    def get_compiled(self) -> dict[str, str | None]:
        """Return every compiled raw file and the content hash it was built from.

        Returns
        -------
        dict[str, str | None]
            Relative raw path mapped to its content hash, or ``None`` for a
            record made before hashes were kept.
        """
        return dict(CompiledSource.objects.values_list("raw_path", "content_hash"))

    def defer_sources(
        self, source_hashes: dict[str, str], reason: str, *, retryable: bool
    ) -> None:
        """Record sources as set aside. Re-deferring updates the reason.

        Parameters
        ----------
        source_hashes: dict[str, str]
            Raw file path (relative to the raw directory) mapped to the
            content hash of the version being set aside.
        reason: str
            Human-readable cause.
        retryable: bool
            Whether building the same source again could succeed.

        Returns
        -------
        None
        """
        now = timezone.now()
        DeferredRecord.objects.bulk_create(
            [
                DeferredRecord(
                    raw_path=path,
                    content_hash=digest,
                    reason=reason,
                    retryable=retryable,
                    deferred_at=now,
                )
                for path, digest in source_hashes.items()
            ],
            update_conflicts=True,
            unique_fields=["raw_path"],
            update_fields=["content_hash", "reason", "retryable", "deferred_at"],
        )

    def get_deferred(self) -> dict[str, DeferredSource]:
        """Return every set-aside source.

        Returns
        -------
        dict[str, DeferredSource]
            Relative raw path mapped to why it was set aside, whether a retry
            could succeed, and which version was set aside.
        """
        return {
            row.raw_path: DeferredSource(row.reason, row.retryable, row.content_hash)
            for row in DeferredRecord.objects.all()
        }

    def clear_deferred(self, raw_paths: list[str]) -> int:
        """Release deferred sources back into the compile queue.

        Parameters
        ----------
        raw_paths: list[str]
            Paths relative to the raw directory.

        Returns
        -------
        int
            Number of rows removed.
        """
        return DeferredRecord.objects.filter(pk__in=raw_paths).delete()[0]
