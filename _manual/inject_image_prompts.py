"""Copy each writer's image_prompt from _manual/batch/<NN>/meta.json into the queue item
that batch_enqueue.py just wrote.

batch_enqueue builds the queue item without image fields, so the prompt the writer chose
for the piece is dropped and the publisher falls back to deriving one from the title. Run
this straight after batch_enqueue. Matching is by repo full_name, so it is safe to re-run.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
BATCH = HERE / "batch"
QUEUE = HERE / "queue"

by_repo: dict[str, str] = {}
for d in sorted(p for p in BATCH.glob("*") if p.is_dir()):
    meta_f, repo_f = d / "meta.json", d / "repo.json"
    if not (meta_f.exists() and repo_f.exists()):
        continue
    prompt = json.loads(meta_f.read_text(encoding="utf-8")).get("image_prompt", "").strip()
    if prompt:
        by_repo[json.loads(repo_f.read_text(encoding="utf-8"))["full_name"]] = prompt

patched, skipped = [], []
for f in sorted(QUEUE.glob("*.json")):
    item = json.loads(f.read_text(encoding="utf-8"))
    prompt = by_repo.get(item.get("repo", ""))
    if not prompt:
        continue
    if item.get("image_prompt") == prompt:
        skipped.append(f.name)
        continue
    item["image_prompt"] = prompt
    f.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
    patched.append(f.name)

print(f"patched {len(patched)}: {patched}")
if skipped:
    print(f"already current {len(skipped)}: {skipped}")
missing = [r for r in by_repo if not any(json.loads(f.read_text(encoding='utf-8')).get('repo') == r
                                         for f in QUEUE.glob('*.json'))]
if missing:
    print(f"not in queue (gate failure?): {missing}")
