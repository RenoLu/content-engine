"""Generate article cover images through ChatGPT in the user's own browser (Kimi WebBridge).

Why not the engine's Pollinations path: flux renders these prompts as glossy photoreal
3D on a saturated background and sprinkles garbled pseudo-text over every paper and
screen, which fails the house look (flat vector, isometric, teal and slate-blue on soft
near-white, no lettering anywhere). Every cover from 0001 to 0034 came from ChatGPT, so
this keeps the set consistent. Pollinations stays the CI-safe fallback for anything
published without a committed jpg.

Requires: Kimi daemon at 127.0.0.1:10086, the extension connected, and that browser
logged into chatgpt.com. One tab at a time, so this runs strictly sequentially.

Usage:
  python _manual/make_covers_chatgpt.py 0035 0036 ...     # prefixes to generate
  python _manual/make_covers_chatgpt.py --all             # every queue item missing a jpg
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
QUEUE = HERE / "queue"
ASSETS = HERE.parent / "assets" / "posts"
RAW = "https://raw.githubusercontent.com/RenoLu/content-engine/main/assets/posts/"
BASE = "http://127.0.0.1:10086/command"
SESSION = "cover-gen"

# The house look, stated positively then fenced with an explicit ban on lettering.
# gpt-image respects the ban; flux does not.
STYLE = (
    "Style: flat vector editorial illustration, isometric, clean crisp shapes, matte "
    "surfaces, teal and slate-blue palette on a soft near-white background, subtle "
    "dot-grid and faint circle accents, generous negative space. Not photorealistic, "
    "not a glossy 3D product render, no depth-of-field blur. "
    "Absolutely no text anywhere in the image: no letters, words, numbers, logos, "
    "brand marks, signage or captions. Every document, screen, label and card face is "
    "blank or carries plain grey placeholder bars. Readable as a small thumbnail: one "
    "clear subject, no clutter."
)


def cmd(action: str, args: dict, timeout: int = 180) -> dict:
    body = json.dumps({"action": action, "args": args, "session": SESSION}).encode()
    req = urllib.request.Request(BASE, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ev(code: str, timeout: int = 180):
    r = cmd("evaluate", {"code": code}, timeout)
    if not r.get("ok"):
        raise RuntimeError(f"evaluate failed: {r.get('error')}")
    return r["data"]["value"]


JS_FILL = r"""((prompt) => {
  const ta = document.querySelector('#prompt-textarea, div[contenteditable="true"]');
  if (!ta) return 'no composer';
  ta.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('insertText', false, prompt);
  return (ta.innerText || ta.value || '').length;
})"""

JS_CLICK_SEND = r"""(() => {
  const btn = document.querySelector('[data-testid="send-button"]')
           || [...document.querySelectorAll('button')].find(b => /^(send|submit)$/i.test(b.getAttribute('aria-label') || ''));
  if (!btn) return 'no send button';
  if (btn.disabled) return 'send disabled';
  btn.click();
  return 'sent';
})()"""

# A generated image is a large <img> served from oaiusercontent; avatars and icons are small.
JS_FIND_IMAGE = r"""(() => {
  const imgs = [...document.querySelectorAll('img')].filter(i =>
    (i.naturalWidth || 0) >= 512 &&
    /oaiusercontent|blob:|\/backend-api\/(estuary\/)?content/.test(i.src || ''));
  const last = imgs[imgs.length - 1];
  return last ? last.src : '';
})()"""

JS_STAGE = r"""(async (url) => {
  const r = await fetch(url);
  const buf = await r.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = '';
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  window.__cover = btoa(bin);
  return window.__cover.length;
})"""


def send_prompt(prompt: str) -> None:
    """Two steps with a pause: ChatGPT's composer re-renders after the insert, and an
    async evaluate spanning that re-render loses its promise ("Promise was collected")."""
    n = ev(f"({JS_FILL})({json.dumps(prompt)})")
    if not isinstance(n, int) or n < 50:
        raise RuntimeError(f"prompt did not land in the composer: {n}")
    time.sleep(2)
    out = ev(JS_CLICK_SEND)
    if out != "sent":
        raise RuntimeError(f"could not send the prompt: {out}")


def wait_for_image(timeout_s: int = 720, poll: int = 15) -> str:
    """Generation runs 4 to 8 minutes for these prompts, and the <img> only appears when
    it is done, so poll patiently rather than declaring failure at five minutes."""
    deadline = time.time() + timeout_s
    seen = ""
    while time.time() < deadline:
        time.sleep(poll)
        src = ev(JS_FIND_IMAGE)
        if src and src != seen:
            # let the full-resolution swap settle, then re-read
            time.sleep(6)
            return ev(JS_FIND_IMAGE) or src
    return ""


def pull_bytes(url: str) -> bytes:
    """Read the image through the page, so signed URLs and blob: both work."""
    total = int(ev(f"({JS_STAGE})({json.dumps(url)})", timeout=180))
    chunk = 120_000
    parts: list[str] = []
    for start in range(0, total, chunk):
        parts.append(ev(f"window.__cover.slice({start},{start + chunk})"))
    ev("window.__cover = ''; 1")
    return base64.b64decode("".join(parts))


def to_jpg(data: bytes, dest: Path) -> int:
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if img.width != 1280:
        img = img.resize((1280, round(img.height * 1280 / img.width)), Image.LANCZOS)
    img.save(dest, "JPEG", quality=88, optimize=True)
    return dest.stat().st_size


def new_chat() -> None:
    cmd("navigate", {"url": "https://chatgpt.com/", "newTab": False, "group_title": "Cover gen"})
    time.sleep(7)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_all = "--all" in sys.argv
    items = []
    for f in sorted(QUEUE.glob("*.json")):
        prefix = f.stem.split("-")[0]
        if args and prefix not in args:
            continue
        if do_all and (ASSETS / f"{f.stem}.jpg").exists():
            continue
        items.append(f)
    if not items:
        print("nothing to generate")
        return

    for f in items:
        item = json.loads(f.read_text(encoding="utf-8"))
        subject = (item.get("image_prompt") or "").strip()
        if not subject:
            print(f"SKIP {f.stem}: no image_prompt")
            continue
        prompt = (f"Create a 16:9 article cover illustration. Subject: {subject} {STYLE}")
        print(f"{f.stem}: sending prompt")
        new_chat()
        send_prompt(prompt)
        url = wait_for_image()
        if not url:
            print(f"  FAILED {f.stem}: no image appeared")
            continue
        data = pull_bytes(url)
        dest = ASSETS / f"{f.stem}.jpg"
        size = to_jpg(data, dest)
        item["image_url"] = RAW + dest.name
        f.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  wrote {dest.name} ({size // 1024} KB)")


if __name__ == "__main__":
    main()
