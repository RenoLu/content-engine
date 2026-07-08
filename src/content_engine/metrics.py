"""Brand-presence metrics: weekly snapshots of the numbers that show whether the
personal-brand work is moving.

Collects, per run, a small set of counts and appends a dated snapshot to
``data/metrics.sqlite3`` so trends are queryable over time:

  * GitHub  — follower count + stars on the flagship repos
  * Bluesky — follower count (public API, no auth)
  * Mastodon— follower count (from verify_credentials; posting token is enough)
  * Outreach— actions taken so far (from the engagement log)

Every collector is best-effort: a failure records nothing for that source rather
than aborting the run. Run: ``python -m content_engine.metrics``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

_UA = {"User-Agent": "content-engine-metrics"}

FLAGSHIP_REPOS = ["realtime-alpha", "investment-lakehouse", "content-engine", "crypto-lakehouse"]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class MetricsStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS snapshots (
        day        TEXT NOT NULL,
        source     TEXT NOT NULL,
        metric     TEXT NOT NULL,
        value      REAL NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(day, source, metric)
    );
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    def record(self, day: str, source: str, metrics: dict[str, float]) -> None:
        for metric, value in metrics.items():
            self.conn.execute(
                """INSERT INTO snapshots (day, source, metric, value, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(day, source, metric) DO UPDATE SET
                       value=excluded.value, created_at=excluded.created_at""",
                (day, source, metric, float(value), _now()),
            )
        self.conn.commit()

    def latest(self, limit: int = 40) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM snapshots ORDER BY day DESC, source, metric LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()


# --------------------------------------------------------------------------- #
# Collectors (each best-effort: returns {} on any failure)
# --------------------------------------------------------------------------- #
def collect_github(client: httpx.Client, user: str, repos: list[str], token: str = "") -> dict[str, float]:
    headers = dict(_UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out: dict[str, float] = {}
    try:
        u = client.get(f"https://api.github.com/users/{user}", headers=headers)
        u.raise_for_status()
        out["followers"] = u.json().get("followers", 0)
    except Exception:
        pass
    total = 0
    for repo in repos:
        try:
            r = client.get(f"https://api.github.com/repos/{user}/{repo}", headers=headers)
            r.raise_for_status()
            stars = r.json().get("stargazers_count", 0)
            out[f"stars.{repo}"] = stars
            total += stars
        except Exception:
            continue
    if any(k.startswith("stars.") for k in out):
        out["stars.total"] = total
    return out


def collect_bluesky(client: httpx.Client, handle: str) -> dict[str, float]:
    if not handle:
        return {}
    try:
        r = client.get(
            "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
            params={"actor": handle}, headers=_UA,
        )
        r.raise_for_status()
        d = r.json()
        return {"followers": d.get("followersCount", 0),
                "following": d.get("followsCount", 0),
                "posts": d.get("postsCount", 0)}
    except Exception:
        return {}


def collect_mastodon(client: httpx.Client, base: str, token: str) -> dict[str, float]:
    if not (base and token):
        return {}
    try:
        r = client.get(f"{base.rstrip('/')}/api/v1/accounts/verify_credentials",
                       headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        d = r.json()
        return {"followers": d.get("followers_count", 0),
                "following": d.get("following_count", 0),
                "posts": d.get("statuses_count", 0)}
    except Exception:
        return {}


def collect_outreach(db_path: str | Path) -> dict[str, float]:
    p = Path(db_path)
    if not p.exists():
        return {}
    try:
        conn = sqlite3.connect(str(p))
        rows = conn.execute(
            """SELECT action_type, COUNT(*) n FROM engagement_log
               WHERE status='executed' GROUP BY action_type"""
        ).fetchall()
        conn.close()
        return {f"executed.{a}": n for a, n in rows}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(env: dict | None = None, db_path: str | Path | None = None) -> dict:
    env = env if env is not None else dict(os.environ)
    root = Path(__file__).resolve().parents[2]
    db_path = db_path or (root / "data" / "metrics.sqlite3")

    gh_user = env.get("METRICS_GITHUB_USER", "RenoLu")
    bsky = env.get("METRICS_BLUESKY_HANDLE", env.get("BLUESKY_HANDLE", ""))
    mast_base = env.get("MASTODON_BASE_URL", "")
    mast_tok = env.get("MASTODON_ACCESS_TOKEN", "")
    gh_token = env.get("GITHUB_TOKEN", "")
    outreach_db = env.get("OUTREACH_DB", str(root / "data" / "outreach.sqlite3"))

    day = _today()
    collected: dict[str, dict] = {}
    with httpx.Client(timeout=30.0) as client:
        collected["github"] = collect_github(client, gh_user, FLAGSHIP_REPOS, gh_token)
        collected["bluesky"] = collect_bluesky(client, bsky)
        collected["mastodon"] = collect_mastodon(client, mast_base, mast_tok)
    collected["outreach"] = collect_outreach(outreach_db)

    store = MetricsStore(db_path)
    try:
        for source, metrics in collected.items():
            if metrics:
                store.record(day, source, metrics)
    finally:
        store.close()

    return {"day": day, "sources": collected}


def main(argv: list[str] | None = None) -> int:
    summary = run()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
