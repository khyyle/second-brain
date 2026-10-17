"""Triage verdicts, keyed by raw source path so they persist across runs."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from second_brain.db import connect, migrate


class TriageStore:
    """
    Reads and writes the ``triage`` table.

    Parameters
    ----------
    db_path: Path
        Path to the pipeline database. Created if it does not exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        migrate(db_path)

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
        now = datetime.now(UTC).isoformat()
        with connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO triage (raw_path, decision, confidence, reason, triaged_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(raw_path) DO UPDATE SET
                     decision = excluded.decision,
                     confidence = excluded.confidence,
                     reason = excluded.reason,
                     triaged_at = excluded.triaged_at""",
                (raw_path, decision, confidence, reason, now),
            )

    def get_decisions(self) -> dict[str, str]:
        """Return the recorded triage decision for every triaged raw path.

        Returns
        -------
        dict[str, str]
            Mapping of raw source path to decision label.
        """
        with connect(self._db_path) as conn:
            rows = conn.execute("SELECT raw_path, decision FROM triage").fetchall()
        return {r["raw_path"]: r["decision"] for r in rows}


def forget_verdict(conn: sqlite3.Connection, raw_path: str) -> None:
    """
    Delete a source's triage verdict on the caller's connection.

    Parameters
    ----------
    conn: sqlite3.Connection
        Open connection whose transaction the caller commits.
    raw_path: str
        Path relative to the raw directory.
    """
    conn.execute("DELETE FROM triage WHERE raw_path = ?", (raw_path,))
