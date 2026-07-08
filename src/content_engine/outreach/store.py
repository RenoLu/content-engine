"""SQLite persistence for outreach: the engagement log.

Two guarantees live here, both enforced at the schema/query level rather than in
the engine, so they cannot be bypassed by a bug in the orchestration:

  * **Dedupe** — ``UNIQUE(platform, target_key, action_type)`` means we never
    like/follow/reply to the same target twice, ever, across all runs.
  * **Daily caps** — ``count_today()`` backs the per-platform daily limits.

Uses stdlib sqlite3 to match the rest of the project (no ORM).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import ActionResult, utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS engagement_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT NOT NULL,
    target_key   TEXT NOT NULL,
    action_type  TEXT NOT NULL,
    status       TEXT NOT NULL,
    url          TEXT,
    comment      TEXT,
    author       TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    day          TEXT NOT NULL,
    UNIQUE(platform, target_key, action_type)
);
CREATE INDEX IF NOT EXISTS idx_eng_day ON engagement_log(platform, action_type, day);
"""


class OutreachStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "OutreachStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- dedupe ----------------------------------------------------------
    def already_done(self, platform: str, target_key: str, action_type: str) -> bool:
        """True if this exact action was already *executed* (not just dry-run).

        A dry-run or pending_approval row must NOT block a later real action, so
        only ``executed`` counts as done — same reasoning as the publishers'
        ``already_published``.
        """
        row = self.conn.execute(
            """SELECT status FROM engagement_log
               WHERE platform=? AND target_key=? AND action_type=?""",
            (platform, target_key, action_type),
        ).fetchone()
        return bool(row) and row["status"] == "executed"

    # ---- daily caps ------------------------------------------------------
    def count_today(self, platform: str, action_type: str, day: str | None = None) -> int:
        """How many actions of this type were *executed* on this platform today."""
        day = day or utcnow_iso()[:10]
        row = self.conn.execute(
            """SELECT COUNT(*) AS n FROM engagement_log
               WHERE platform=? AND action_type=? AND day=? AND status='executed'""",
            (platform, action_type, day),
        ).fetchone()
        return int(row["n"]) if row else 0

    # ---- recording -------------------------------------------------------
    def record(self, result: ActionResult, comment: str = "", author: str = "") -> None:
        """Upsert an action outcome. A later executed row overwrites an earlier
        dry-run/pending row for the same (platform, target, action)."""
        self.conn.execute(
            """INSERT INTO engagement_log
                 (platform, target_key, action_type, status, url, comment, author, error, created_at, day)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(platform, target_key, action_type) DO UPDATE SET
                   status=excluded.status, url=excluded.url, comment=excluded.comment,
                   author=excluded.author, error=excluded.error,
                   created_at=excluded.created_at, day=excluded.day""",
            (
                result.platform,
                result.target_key,
                result.action_type.value,
                result.status,
                result.url,
                comment,
                author,
                result.error,
                utcnow_iso(),
                utcnow_iso()[:10],
            ),
        )
        self.conn.commit()

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM engagement_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
