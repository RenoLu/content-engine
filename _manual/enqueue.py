"""Promote the current _manual/article.json (+ repo.json) into the publish queue.

Run after the agent has authored article.json for the repo in repo.json. Writes a
ready-to-publish queue item with a monotonic numeric prefix (FIFO publish order).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
QUEUE = HERE / "queue"
PUBLISHED = HERE / "published"
QUEUE.mkdir(exist_ok=True)

repo = json.loads((HERE / "repo.json").read_text(encoding="utf-8"))
art = json.loads((HERE / "article.json").read_text(encoding="utf-8"))
d = art["draft"]

name = repo["full_name"].split("/")[-1].lower()
seq = len(list(QUEUE.glob("*.json")))
if PUBLISHED.exists():
    seq += len(list(PUBLISHED.glob("*.json")))
seq += 1

item = {
    "repo": repo["full_name"],
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "title": d["title"],
    "summary": d["summary"],
    "tags": d.get("tags", []),
    "body_markdown": d["body_markdown"],
    "repo_url": repo["html_url"],
    "canonical_url": None,
}
out = QUEUE / f"{seq:04d}-{name}.json"
out.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"queued {out.name} ({repo['full_name']})")
