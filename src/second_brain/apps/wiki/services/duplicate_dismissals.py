"""Page pairs a person marked as not duplicates of each other."""

from __future__ import annotations

from django.utils import timezone

from second_brain.apps.wiki.models import DismissedDuplicate


class DuplicateStore:
    """
    Reads and writes the ``dismissed_duplicates`` table.

    Uses the database selected in Django settings.
    """

    def dismiss(self, stem_a: str, stem_b: str) -> None:
        """Record that two pages are not duplicates of each other.

        The pair is stored order-independently, so dismissing (a, b) also
        covers (b, a). Dismissing an already-dismissed pair is a no-op.

        Parameters
        ----------
        stem_a: str
            One page's stem.
        stem_b: str
            The other page's stem.
        """
        first, second = sorted((stem_a, stem_b))
        DismissedDuplicate.objects.get_or_create(
            stem_a=first, stem_b=second, defaults={"dismissed_at": timezone.now()}
        )

    def get_dismissed(self) -> set[tuple[str, str]]:
        """Return every dismissed pair, each as a sorted stem tuple.

        Returns
        -------
        set[tuple[str, str]]
            Pairs a human marked as not duplicates.
        """
        return set(DismissedDuplicate.objects.values_list("stem_a", "stem_b"))
