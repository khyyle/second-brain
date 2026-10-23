"""Page pairs a person marked as not duplicates of each other."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from second_brain.db import connect, migrate


class DuplicateStore:
    """
    Reads and writes the ``dismissed_duplicates`` table.

    Parameters
    ----------
    db_path: Path
        Path to the pipeline database. Created if it does not exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        migrate(db_path)

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
        now = datetime.now(UTC).isoformat()
        with connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO dismissed_duplicates "
                "(stem_a, stem_b, dismissed_at) VALUES (?, ?, ?)",
                (first, second, now),
            )

    def get_dismissed(self) -> set[tuple[str, str]]:
        """Return every dismissed pair, each as a sorted stem tuple.

        Returns
        -------
        set[tuple[str, str]]
            Pairs a human marked as not duplicates.
        """
        with connect(self._db_path) as conn:
            rows = conn.execute("SELECT stem_a, stem_b FROM dismissed_duplicates").fetchall()
        return {(r["stem_a"], r["stem_b"]) for r in rows}
