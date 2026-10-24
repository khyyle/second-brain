"""Manual curation of chats. Automatic model-based triage stays in ingestion."""

from django.db import transaction
from django.utils import timezone

from second_brain.apps.library.errors import InvalidTriageError, SourceNotFoundError
from second_brain.apps.library.models import TriageDecision
from second_brain.apps.library.services.source_files import reversible_moves, source_locations
from second_brain.apps.library.services.source_locks import require_sources_idle
from second_brain.django_config.vault_storage import vault_lock
from second_brain.pipeline_config import PipelineConfig


def list_decisions(decision: str | None = None) -> list[TriageDecision]:
    """Read recorded triage verdicts, including history for already-purged chats.

    Parameters
    ----------
    decision: str | None
        Filter by "worthwhile", "review", or "skip"; None returns every verdict.
        The API validates this choice before calling the service.

    Returns
    -------
    list[TriageDecision]
        Records ordered by newest triage time, then raw_path to break ties.

    """
    decisions = TriageDecision.objects.all()
    if decision is not None:
        decisions = decisions.filter(decision=decision)
    return list(decisions.order_by("-triaged_at", "raw_path"))


def update_decisions(
    config: PipelineConfig, raw_paths: list[str], decision: str
) -> list[TriageDecision]:
    """Validate a batch, move skip/restore files, then upsert manual verdicts together.

    Parameters
    ----------
    config: PipelineConfig
        Active vault paths and triage.sources, the lanes eligible for manual triage.
    raw_paths: list[str]
        Logical Markdown paths relative to raw/, without .skipped/ prefixes.
        Duplicates are processed once; the API requires 1 through 200 entries.
    decision: str
        "skip" moves active copies into raw/.skipped/. "worthwhile" and "review"
        restore held copies to raw/. Only worthwhile chats become build-eligible.

    Returns
    -------
    list[TriageDecision]
        Persisted verdicts in first-occurrence input order, with confidence 1.0,
        reason "manual override", and a shared timestamp for this batch.

    Raises
    ------
    InvalidTriageError
        The verdict is unsupported or a source lane is outside config.triage.sources.
    InvalidSourcePathError
        A selected path is unsafe, follows a symlink, or identifies a directory.
    SourceNotFoundError
        A source has neither a raw file nor an existing triage record.
    SourceConflictError
        A move conflicts with existing content or both active/skipped copies exist.

    Notes
    -----
    The complete selection is validated before moving files. Database writes are
    atomic; moved files are restored if the operation or commit fails. This service
    owns the outer transaction. Unexpected filesystem/database failures propagate.
    A previously purged chat may retain/change its verdict, but no missing content is
    recreated. Filesystem conflicts never silently overwrite either copy.
    """
    if decision not in {"worthwhile", "review", "skip"}:
        raise InvalidTriageError("Choose worthwhile, review, or skip.")
    selected = list(dict.fromkeys(raw_paths))
    with (
        vault_lock("raw-sources", blocking=False),
        reversible_moves() as files,
        transaction.atomic(durable=True),
    ):
        require_sources_idle(selected)
        existing = set(
            TriageDecision.objects.filter(raw_path__in=selected).values_list("raw_path", flat=True)
        )
        locations = []
        for raw_path in selected:
            active, skipped = source_locations(config.data_dir, raw_path)
            if raw_path.split("/", 1)[0] not in config.triage.sources:
                raise InvalidTriageError(
                    "Manual triage is only available for configured chat lanes."
                )
            if not active.exists() and not skipped.exists() and raw_path not in existing:
                raise SourceNotFoundError(f"The raw file for {raw_path} is no longer available.")
            locations.append((active, skipped))

        for active, skipped in locations:
            if decision == "skip" and active.exists():
                files.move(active, skipped)
            elif decision != "skip" and skipped.exists():
                files.move(skipped, active)

        now = timezone.now()
        records = [
            TriageDecision(
                raw_path=raw_path,
                decision=decision,
                confidence=1.0,
                reason="manual override",
                triaged_at=now,
            )
            for raw_path in selected
        ]
        # One upsert for the batch, instead of an update_or_create query per chat.
        TriageDecision.objects.bulk_create(
            records,
            update_conflicts=True,
            unique_fields=["raw_path"],
            update_fields=["decision", "confidence", "reason", "triaged_at"],
        )
    return records
