"""Triage verdicts, keyed by raw source path so they persist across runs."""

from __future__ import annotations

from django.utils import timezone

from second_brain.apps.library.models import TriageDecision


class TriageStore:
    """
    Reads and writes the ``triage`` table.

    Uses the database selected in Django settings.
    """

    def record(self, raw_path: str, decision: str, confidence: float, reason: str = "") -> None:
        """Persist a triage verdict for a raw source path (upsert).

        Parameters
        ----------
        raw_path: str
            Raw source path (relative to the raw directory).
        decision: str
            One of ``"worthwhile"``, ``"review"``, ``"skip"``.
        confidence: float
            Triage model confidence for the decision.
        reason: str
            Short human-readable rationale for logs/digests.
        """
        TriageDecision.objects.update_or_create(
            pk=raw_path,
            defaults={
                "decision": decision,
                "confidence": confidence,
                "reason": reason,
                "triaged_at": timezone.now(),
            },
        )

    def get_decisions(self) -> dict[str, str]:
        """Return the recorded triage decision for every triaged raw path.

        Returns
        -------
        dict[str, str]
            Mapping of raw source path to decision label.
        """
        return dict(TriageDecision.objects.values_list("raw_path", "decision"))
