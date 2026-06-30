"""Validate each staged batch article through the deterministic quality gate +
a secret scan, then enqueue the passing ones into _manual/queue/.

Run after the writer has produced body.md + meta.json in each _manual/batch/<NN>/
(repo.json is written there by discover_batch.py).
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from content_engine.config import load_settings
from content_engine.models import Draft, Repository
from content_engine.quality import run_quality_checks

HERE = Path(__file__).parent
BATCH = HERE / "batch"
QUEUE = HERE / "queue"
PUBLISHED = HERE / "published"
QUEUE.mkdir(exist_ok=True)

s = load_settings()

# Secret values to scan article bodies against (defense in depth).
_secrets: list[str] = []
_sp = Path(os.path.expanduser("~")) / ".claude" / "settings.json"
if _sp.exists():
    _cfg = json.loads(_sp.read_text(encoding="utf-8"))
    _secrets = [v for k, v in _cfg.get("env", {}).items()
                if isinstance(v, str) and len(v) >= 12 and not k.startswith("_")]


def _next_seq() -> int:
    n = len(list(QUEUE.glob("*.json")))
    if PUBLISHED.exists():
        n += len(list(PUBLISHED.glob("*.json")))
    return n + 1


enqueued, failed = [], []
for d in sorted(p for p in BATCH.glob("*") if p.is_dir()):
    body_f, meta_f, repo_f = d / "body.md", d / "meta.json", d / "repo.json"
    if not (body_f.exists() and meta_f.exists() and repo_f.exists()):
        continue
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    body = body_f.read_text(encoding="utf-8")
    repo = Repository.from_dict(json.loads(repo_f.read_text(encoding="utf-8")))

    secret_hit = next((True for sec in _secrets if sec in body or sec in meta.get("summary", "")), False)
    if secret_hit:
        failed.append((repo.full_name, ["secret_in_article"]))
        print(f"FAIL {repo.full_name}: possible secret in article — NOT enqueued")
        continue

    draft = Draft(title=meta["title"], body_markdown=body,
                  summary=meta["summary"], tags=meta.get("tags", []))
    q = run_quality_checks(draft, repo, s)
    if not q.passed:
        failed.append((repo.full_name, q.blocking))
        print(f"FAIL {repo.full_name}: {q.blocking}")
        continue

    seq = _next_seq()
    name = repo.full_name.split("/")[-1].lower()
    item = {
        "repo": repo.full_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "title": meta["title"],
        "summary": meta["summary"],
        "tags": meta.get("tags", []),
        "body_markdown": body,
        "repo_url": repo.html_url,
        "canonical_url": None,
    }
    out = QUEUE / f"{seq:04d}-{name}.json"
    out.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    enqueued.append(out.name)
    print(f"OK   {repo.full_name} -> {out.name} "
          f"({q.stats['word_count']} words, {q.stats['heading_count']} headings, "
          f"summary {q.stats['summary_chars']} chars)")

print(f"\nenqueued {len(enqueued)}, failed {len(failed)}")
