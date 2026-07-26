"""Generate the cover jpg for queued articles that do not have one yet.

The publisher can build a Pollinations URL at publish time, but a committed jpg is
better: the bytes stop depending on a third-party service still answering months
later, and the same file feeds DEV.to, the LinkedIn article lane, and anything else
that wants a local upload.

Uses the engine's own imagegen so the style suffix (and therefore the look) matches
every earlier cover. Writes assets/posts/<prefix>-<slug>.jpg and sets image_url on
the queue item to the raw.githubusercontent path the other items use.

Usage:
  PYTHONPATH=src python _manual/make_covers.py            # every queue item missing a cover
  PYTHONPATH=src python _manual/make_covers.py 0035 0036  # only these prefixes
  PYTHONPATH=src python _manual/make_covers.py --force 0035
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

from content_engine.imagegen import build_image_url

HERE = Path(__file__).parent
QUEUE = HERE / "queue"
ASSETS = HERE.parent / "assets" / "posts"
RAW = "https://raw.githubusercontent.com/RenoLu/content-engine/main/assets/posts/"


def fetch(url: str, tries: int = 3) -> bytes:
    """Pollinations renders on first request, so the first call can be slow or 5xx."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "content-engine/covers"})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            if len(data) > 20_000:
                return data
            last = f"only {len(data)} bytes"
        except Exception as exc:
            last = str(exc)
        if attempt < tries - 1:
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"image fetch failed: {last}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    ASSETS.mkdir(parents=True, exist_ok=True)

    for f in sorted(QUEUE.glob("*.json")):
        prefix = f.stem.split("-")[0]
        if args and prefix not in args:
            continue
        item = json.loads(f.read_text(encoding="utf-8"))
        dest = ASSETS / f"{f.stem}.jpg"
        if dest.exists() and not force:
            continue
        prompt = (item.get("image_prompt") or "").strip()
        if not prompt:
            print(f"SKIP {f.stem}: no image_prompt")
            continue
        url = build_image_url(prompt)
        print(f"{f.stem}: generating...")
        data = fetch(url)
        dest.write_bytes(data)
        item["image_url"] = RAW + dest.name
        f.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  wrote {dest.name} ({len(data) // 1024} KB) and set image_url")


if __name__ == "__main__":
    main()
