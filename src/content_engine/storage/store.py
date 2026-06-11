"""SQLite persistence for pipeline runs, candidates, and publish results.

We deliberately use the stdlib ``sqlite3`` module (no ORM) to keep dependencies
minimal. JSON blobs hold the richer nested structures (draft, review, repo
snapshot); first-class columns hold anything we want to query or enforce
uniqueness on.

Idempotency / duplicate-prevention is enforced at the schema level:
  * ``runs.run_date`` is UNIQUE              -> one run per day
  * ``repo_history.full_name`` is PRIMARY KEY -> a repo is never featured twice
  * ``publish_results`` UNIQUE(run_date, publisher) -> no double-posting
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..logging_setup import get_logger
from ..models import (
    Draft,
    PublishResult,
    Repository,
    ReviewResult,
    RunStatus,
    utcnow_iso,
)

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL,
    mode            TEXT NOT NULL,
    repo_full_name  TEXT,
    repo_json       TEXT,
    draft_json      TEXT,
    review_json     TEXT,
    final_json      TEXT,
    skip_reason     TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_history (
    full_name   TEXT PRIMARY KEY,
    run_date    TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT NOT NULL,
    full_name   TEXT NOT NULL,
    score       REAL,
    selected    INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT,
    repo_json   TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date     TEXT NOT NULL,
    publisher    TEXT NOT NULL,
    status       TEXT NOT NULL,
    url          TEXT,
    external_id  TEXT,
    error        TEXT,
    dry_run      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    UNIQUE(run_date, publisher)
);

CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(run_date);
CREATE INDEX IF NOT EXISTS idx_publish_run ON publish_results(run_date);
"""


class Store:
    """Thin data-access object over a SQLite database."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.init_db()

    def init_db(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ runs
    def get_run(self, run_date: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE run_date = ?", (run_date,)
        ).fetchone()
        return dict(row) if row else None

    def create_run(self, run_date: str, mode: str) -> dict[str, Any]:
        """Create a run row for the date if absent; return the (existing or new) row."""
        now = utcnow_iso()
        self.conn.execute(
            """INSERT INTO runs (run_date, status, mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_date) DO NOTHING""",
            (run_date, RunStatus.PENDING.value, mode, now, now),
        )
        self.conn.commit()
        run = self.get_run(run_date)
        assert run is not None
        return run

    def update_run(self, run_date: str, **fields: Any) -> None:
        if not fields:
            return
        # Serialize dataclasses / dicts passed for *_json columns.
        json_cols = {"repo_json", "draft_json", "review_json", "final_json"}
        clean: dict[str, Any] = {}
        for k, v in fields.items():
            if k in json_cols and not isinstance(v, (str, type(None))):
                clean[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, RunStatus):
                clean[k] = v.value
            else:
                clean[k] = v
        clean["updated_at"] = utcnow_iso()
        cols = ", ".join(f"{k} = ?" for k in clean)
        self.conn.execute(
            f"UPDATE runs SET {cols} WHERE run_date = ?",
            (*clean.values(), run_date),
        )
        self.conn.commit()

    def set_status(self, run_date: str, status: RunStatus, error: str | None = None) -> None:
        self.update_run(run_date, status=status.value, error=error)

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY run_date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------- repo history
    def used_repo_names(self) -> set[str]:
        rows = self.conn.execute("SELECT full_name FROM repo_history").fetchall()
        return {r["full_name"] for r in rows}

    def has_used_repo(self, full_name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM repo_history WHERE full_name = ?", (full_name,)
        ).fetchone()
        return row is not None

    def mark_repo_used(self, full_name: str, run_date: str, status: str) -> None:
        self.conn.execute(
            """INSERT INTO repo_history (full_name, run_date, status, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(full_name) DO UPDATE SET
                   run_date=excluded.run_date, status=excluded.status""",
            (full_name, run_date, status, utcnow_iso()),
        )
        self.conn.commit()

    # ------------------------------------------------------------- candidates
    def record_candidates(self, run_date: str, repos: Iterable[Repository],
                          selected_full_name: str | None = None) -> None:
        """Persist every candidate we considered (with score + skip reason)."""
        self.conn.execute("DELETE FROM candidates WHERE run_date = ?", (run_date,))
        now = utcnow_iso()
        rows = []
        for r in repos:
            rows.append(
                (
                    run_date,
                    r.full_name,
                    r.score,
                    1 if r.full_name == selected_full_name else 0,
                    r.skip_reason,
                    json.dumps(r.to_dict(), ensure_ascii=False),
                    now,
                )
            )
        self.conn.executemany(
            """INSERT INTO candidates
                 (run_date, full_name, score, selected, skip_reason, repo_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()

    def get_candidates(self, run_date: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE run_date = ? ORDER BY score DESC",
            (run_date,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------- publish results
    def already_published(self, run_date: str, publisher: str) -> bool:
        """True only if this publisher has a genuine LIVE post for this date.

        A prior *dry-run* row must NOT count — otherwise dry-running a date would
        permanently block a later live post for the same publisher. Only a real
        post (status='published' AND dry_run=0) blocks, which is exactly the
        double-post guard we want, even under ``--force``.
        """
        row = self.conn.execute(
            """SELECT status, dry_run FROM publish_results
               WHERE run_date = ? AND publisher = ?""",
            (run_date, publisher),
        ).fetchone()
        return bool(row) and row["status"] == "published" and row["dry_run"] == 0

    def record_publish_result(self, run_date: str, result: PublishResult) -> None:
        self.conn.execute(
            """INSERT INTO publish_results
                 (run_date, publisher, status, url, external_id, error, dry_run, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_date, publisher) DO UPDATE SET
                   status=excluded.status, url=excluded.url,
                   external_id=excluded.external_id, error=excluded.error,
                   dry_run=excluded.dry_run, created_at=excluded.created_at""",
            (
                run_date,
                result.publisher,
                result.status,
                result.url,
                result.external_id,
                result.error,
                1 if result.dry_run else 0,
                utcnow_iso(),
            ),
        )
        self.conn.commit()

    def get_publish_results(self, run_date: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM publish_results WHERE run_date = ? ORDER BY publisher",
            (run_date,),
        ).fetchall()
        return [dict(r) for r in rows]
