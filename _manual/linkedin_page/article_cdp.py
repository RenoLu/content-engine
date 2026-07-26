"""Publish and repair Yan Lu's LinkedIn Articles over the CDP debug Chrome.

Replaces the Kimi-driven lane in post_personal.ps1. Two reasons:
  1. Formatting. The Kimi bridge can only fire synthetic events, and the article editor's
     Style dropdown renders its items only for a real pointer interaction, so every article
     published through it came out as a wall of same-size paragraphs: section headings were
     indistinguishable from body text, bullet lists were flattened into prose.
  2. Cover images. File attach is refused over the Kimi bridge (-32000). Over CDP the
     editor's own file chooser works, so articles finally carry the same cover image the
     dev.to post uses.

Editor facts this depends on (learned by probing, see linkedin_page/AGENTS.md):
  - title = the page's single <textarea>; body = the single [contenteditable=true].
  - Every typed paragraph is <p class="article-editor-paragraph">. Structure only arrives
    through the toolbar.
  - Heading / Subheading live in the "Style" dropdown; the items exist in the DOM only
    while the menu is open. Put the caret in the block first, then pick the item.
  - The other controls are icon buttons identified by their svg data-test-icon:
    text-bold-medium, text-italic-medium, text-bulleted-list-medium,
    text-numbered-list-medium, quote-medium, curly-braces-medium (code block),
    subtract-medium, link-medium, embed-medium, image-medium.
  - A heading applied to a block, then Enter, returns to a normal paragraph on its own.
  - Bullet lists: toggle the button on, type the items separated by Enter, toggle off.
  - Code blocks: type the lines as paragraphs, select the run, then toggle - toggling
    first and typing into an empty code block loses the text.
  - Cover image: "Upload from computer" opens a file chooser, then the modal's "Next"
    commits it.
  - The slug is frozen at first publish, so the title must be set before publishing.

Usage:
  python article_cdp.py --launch                 launch the debug Chrome and stop
  python article_cdp.py --next [--max N] [--dry] publish the next queued piece
  python article_cdp.py --next --from-published  ... and treat every published/ article as
                                                 queue material once queue.json runs out
  python article_cdp.py --fix 0011 0010          repair published articles (reformat + cover)
  python article_cdp.py --fix-all                repair every article found on the profile
  python article_cdp.py --map                    refresh prefix -> article URL map only
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT.parent / "published"
ASSETS = ROOT.parent.parent / "assets" / "posts"
QUEUE = ROOT / "queue.json"
STATE = ROOT / "posted_personal.json"
MAP_FILE = ROOT / "articles_map.json"
REPO = ROOT.parent.parent            # content-engine checkout root
CACHE = ROOT / ".covers"             # covers fetched from image_url
LOG = ROOT / "post_cdp_log.txt"
SHOTS = ROOT / "shots"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\luyan\.jobapp-chrome"
CDP = "http://127.0.0.1:9222"
VANITY = "renolu"
ACTIVITY = f"https://www.linkedin.com/in/{VANITY}/recent-activity/articles/"
NEW_ARTICLE = "https://www.linkedin.com/article/new/"

ICON = "button.scaffold-formatted-text-editor-icon-button:has(svg[data-test-icon=\"%s\"])"


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- markdown -> blocks

INLINE = [
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),          # bold
    (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), r"\1"),  # italic
    (re.compile(r"`([^`]+)`"), r"\1"),                # inline code
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),    # links keep the label
]


def inline_plain(s: str) -> str:
    for rx, rep in INLINE:
        s = rx.sub(rep, s)
    return s.strip()


def blocks_from_markdown(md: str) -> list[dict]:
    """Split body_markdown into the blocks the editor can represent."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    blocks: list[dict] = []
    para: list[str] = []
    bullets: list[str] = []
    code: list[str] = []
    in_code = False

    def flush_para() -> None:
        if para:
            blocks.append({"kind": "para", "text": inline_plain(" ".join(para))})
            para.clear()

    def flush_bullets() -> None:
        if bullets:
            blocks.append({"kind": "bullets", "items": [inline_plain(b) for b in bullets]})
            bullets.clear()

    for raw in lines:
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                if code:
                    blocks.append({"kind": "code", "lines": list(code)})
                code.clear()
                in_code = False
            else:
                flush_para()
                flush_bullets()
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if h:
            flush_para()
            flush_bullets()
            level = min(max(len(h.group(1)), 2), 3)
            blocks.append({"kind": f"h{level}", "text": inline_plain(h.group(2))})
            continue
        b = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if b:
            flush_para()
            bullets.append(b.group(1))
            continue
        if not line.strip():
            flush_para()
            flush_bullets()
            continue
        flush_bullets()
        para.append(line.strip())

    flush_para()
    flush_bullets()
    if code:
        blocks.append({"kind": "code", "lines": code})
    return [b for b in blocks if b.get("text") or b.get("items") or b.get("lines")]


def expected_text(blocks: list[dict]) -> str:
    parts: list[str] = []
    for b in blocks:
        if b["kind"] in ("para", "h2", "h3"):
            parts.append(b["text"])
        elif b["kind"] == "bullets":
            parts.extend(b["items"])
        else:
            parts.extend(b["lines"])
    return norm(" ".join(parts))


# ---------------------------------------------------------------- browser plumbing


def cdp_up() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP}/json/version", timeout=3):
            return True
    except Exception:
        return False


def launch_chrome() -> None:
    if cdp_up():
        print("debug Chrome already on :9222")
        return
    subprocess.Popen([CHROME, "--remote-debugging-port=9222", f"--user-data-dir={PROFILE}",
                      "--no-first-run", "--no-default-browser-check", "about:blank"])
    for _ in range(20):
        if cdp_up():
            print("debug Chrome launched on :9222")
            return
        time.sleep(1)
    raise SystemExit("Chrome did not expose CDP on :9222")


JS_CARET_IN = r"""(txt) => {
  const ed=document.querySelector('[contenteditable=true]');
  const n=[...ed.children].find(x=>(x.innerText||'').replace(/\s+/g,' ').trim()===txt);
  if(!n) return false;
  ed.focus();
  const r=document.createRange(); r.selectNodeContents(n); r.collapse(false);
  const s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
  return true;
}"""

JS_SELECT_RUN = r"""(texts) => {
  const ed=document.querySelector('[contenteditable=true]');
  const key=t=>t.replace(/\s+/g,' ').trim();
  const kids=[...ed.children];
  const first=kids.find(x=>key(x.innerText||'')===texts[0]);
  const last=kids.find(x=>key(x.innerText||'')===texts[texts.length-1]);
  if(!first||!last) return false;
  ed.focus();
  const r=document.createRange(); r.setStartBefore(first); r.setEndAfter(last);
  const s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
  return true;
}"""

JS_SHAPE = r"""() => {
  const ed=document.querySelector('[contenteditable=true]');
  return {blocks:ed.children.length, h2:ed.querySelectorAll('h2').length,
          h3:ed.querySelectorAll('h3').length, ul:ed.querySelectorAll('ul').length,
          li:ed.querySelectorAll('li').length, pre:ed.querySelectorAll('pre').length,
          text:(ed.innerText||'').replace(/\s+/g,' ').trim()};
}"""

JS_HAS_COVER = r"""() => {
  const imgs=[...document.querySelectorAll('img')];
  return imgs.some(i=>/article-editor|cover/i.test(i.className.toString()) && (i.src||'').length>40);
}"""


def open_page(browser, url: str):
    ctx = browser.contexts[0]
    page = None
    for p in ctx.pages:
        if "linkedin.com/article" in p.url or "linkedin.com/in/" in p.url:
            page = p
            break
    if page is None:
        page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(6000)
    return page


def wait_editor(page) -> None:
    page.wait_for_selector("[contenteditable=true]", timeout=45_000)
    page.wait_for_timeout(1500)


def focus_body(page) -> None:
    """Click into the body and confirm ProseMirror actually took focus. On a fresh
    /article/new/ the editor mounts late, and typing before it does is silently lost."""
    body = page.locator("[contenteditable=true]").first
    for _ in range(8):
        body.click()
        page.wait_for_timeout(400)
        focused = page.evaluate(
            "() => /ProseMirror/.test((document.activeElement.className||'').toString())")
        if focused:
            return
        page.wait_for_timeout(900)
    raise RuntimeError("article body never took focus")


def style_as(page, which: str) -> bool:
    """which: normal | heading-1 (Heading) | heading-2 (Subheading).
    The menu renders its items only when the editor has a live block selection, so an
    empty menu means the caret was lost: close it and try again."""
    item = f'[class*="heading-dropdown-item--{which}"]'
    trigger = page.get_by_role("button", name="Style")
    for _ in range(3):
        trigger.click()
        page.wait_for_timeout(600)
        loc = page.locator(item)
        if loc.count():
            loc.first.click()
            page.wait_for_timeout(450)
            return True
        trigger.click()   # close the empty menu before retrying
        page.wait_for_timeout(400)
    return False


def icon_click(page, icon: str) -> None:
    page.locator(ICON % icon).first.click()
    page.wait_for_timeout(400)


def clear_body(page) -> None:
    focus_body(page)
    page.keyboard.press("Control+a")
    page.wait_for_timeout(200)
    page.keyboard.press("Backspace")
    page.wait_for_timeout(600)
    shape = page.evaluate(JS_SHAPE)
    if len(shape["text"]) > 40:
        raise RuntimeError(f"body not cleared ({len(shape['text'])} chars left)")


def compose(page, blocks: list[dict]) -> dict:
    """Type the blocks into an empty body, applying structure as it goes."""
    focus_body(page)
    code_runs: list[list[str]] = []
    need_break = False

    for b in blocks:
        if need_break:
            page.keyboard.press("Enter")
        need_break = True
        if b["kind"] == "para":
            page.keyboard.insert_text(b["text"])
        elif b["kind"] in ("h2", "h3"):
            page.keyboard.insert_text(b["text"])
            if not style_as(page, "heading-1" if b["kind"] == "h2" else "heading-2"):
                log(f"  WARN: heading not applied: {b['text'][:40]}")
            # the caret can land at the start of the converted block; go back to its end
            page.evaluate(JS_CARET_IN, norm(b["text"]))
        elif b["kind"] == "bullets":
            icon_click(page, "text-bulleted-list-medium")
            for j, item in enumerate(b["items"]):
                if j:
                    page.keyboard.press("Enter")
                page.keyboard.insert_text(item)
            # leave the list from an empty item: toggling off while the caret sits on the
            # last item pulls that item back out of the list and it loses its bullet
            page.keyboard.press("Enter")
            icon_click(page, "text-bulleted-list-medium")
            need_break = False
        else:  # code
            for j, line in enumerate(b["lines"]):
                if j:
                    page.keyboard.press("Enter")
                page.keyboard.insert_text(line)
            code_runs.append([norm(x) for x in b["lines"] if norm(x)])
        page.wait_for_timeout(120)

    # code blocks are converted after the fact: a run of paragraphs selected, then toggled
    for run in code_runs:
        if not run:
            continue
        if page.evaluate(JS_SELECT_RUN, run):
            icon_click(page, "curly-braces-medium")
        else:
            log(f"  WARN: code run not found: {run[0][:40]}")

    page.wait_for_timeout(800)
    return page.evaluate(JS_SHAPE)


def set_cover(page, image: Path) -> bool:
    if not image.exists():
        log(f"  no cover file: {image.name}")
        return False
    if page.evaluate(JS_HAS_COVER):
        log("  cover already set")
        return True
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(400)
    btn = page.get_by_role("button", name="Upload from computer")
    if not btn.count():
        log("  WARN: no cover upload button")
        return False
    with page.expect_file_chooser(timeout=20_000) as fc:
        btn.first.click()
    fc.value.set_files(str(image))
    page.wait_for_timeout(3500)
    nxt = page.locator('.artdeco-modal button:has-text("Next")')
    if nxt.count():
        nxt.first.click()
        page.wait_for_timeout(2500)
    ok = bool(page.evaluate(JS_HAS_COVER))
    log(f"  cover {'uploaded' if ok else 'FAILED'}: {image.name}")
    return ok


def set_title(page, title: str) -> str:
    ta = page.locator("textarea").first
    ta.click()
    page.keyboard.press("Control+a")
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(title)
    page.wait_for_timeout(500)
    return page.evaluate("() => document.querySelector('textarea').value")


def verify(page, blocks: list[dict]) -> tuple[bool, dict]:
    shape = page.evaluate(JS_SHAPE)
    want = expected_text(blocks)
    got = shape["text"]
    want_h2 = sum(1 for b in blocks if b["kind"] == "h2")
    want_h3 = sum(1 for b in blocks if b["kind"] == "h3")
    want_li = sum(len(b["items"]) for b in blocks if b["kind"] == "bullets")
    ok = (
        len(got) >= int(len(want) * 0.97)
        and shape["h2"] >= want_h2
        and shape["h3"] >= want_h3
        and shape["li"] >= want_li
    )
    shape["want"] = {"chars": len(want), "h2": want_h2, "h3": want_h3, "li": want_li}
    shape["got_chars"] = len(got)
    shape.pop("text", None)
    return ok, shape


# ---------------------------------------------------------------- article map


def load_sources() -> dict[str, dict]:
    out = {}
    for f in sorted(SRC_DIR.glob("0*.json")):
        prefix = f.name.split("-")[0]
        out[prefix] = json.loads(f.read_text(encoding="utf-8-sig"))
    out.update(_sources_from_origin(out))
    return out


def _git(*args: str, binary: bool = False):
    """Run git inside the content-engine checkout. Returns None on any failure."""
    try:
        r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True,
                           timeout=90, text=not binary,
                           encoding=None if binary else "utf-8")
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _sources_from_origin(have: dict[str, dict]) -> dict[str, dict]:
    """Read published articles straight out of origin/main.

    The dev.to publisher is a GitHub Action: it commits each new _manual/published/*.json
    to main from CI, so this checkout is usually several publishes behind and the drip
    would silently see nothing new. Reading the blobs from origin/main keeps the LinkedIn
    lane independent of whether anyone remembered to pull, and touches no working file.
    """
    _git("fetch", "-q", "origin")
    listing = _git("ls-tree", "--name-only", "origin/main:_manual/published")
    if not listing:
        return {}
    extra: dict[str, dict] = {}
    for name in listing.split():
        if not name.endswith(".json"):
            continue
        prefix = name.split("-")[0]
        if prefix in have:
            continue
        blob = _git("show", f"origin/main:_manual/published/{name}")
        if not blob:
            continue
        try:
            extra[prefix] = json.loads(blob)
        except json.JSONDecodeError:
            continue
    if extra:
        log(f"read {len(extra)} published article(s) from origin/main: {sorted(extra)}")
    return extra


def refresh_map(page, sources: dict[str, dict]) -> dict[str, dict]:
    """Match the profile's published articles to queue prefixes by title."""
    page.goto(ACTIVITY, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(7000)
    for _ in range(6):
        page.evaluate("window.scrollBy(0,900)")
        page.wait_for_timeout(600)
    found = page.evaluate(r"""() => {
      const out=[];
      document.querySelectorAll('a[href*="/pulse/"]').forEach(a=>{
        const t=(a.innerText||'').trim().split('\n')[0];
        if(t) out.push([t, a.href.split('?')[0]]);
      });
      return out;
    }""")
    mapping = json.loads(MAP_FILE.read_text(encoding="utf-8-sig")) if MAP_FILE.exists() else {}
    for title, url in found:
        key = norm(title).lower()
        for prefix, art in sources.items():
            src = norm(art["title"]).lower()
            if key and (src.startswith(key[:40]) or key.startswith(src[:40])):
                entry = mapping.setdefault(prefix, {})
                entry["title"] = art["title"]
                entry["pulse"] = url
                break
    MAP_FILE.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return mapping


def edit_url(page, pulse: str) -> str | None:
    page.goto(pulse, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(5000)
    return page.evaluate(r"""() => {
      const a=document.querySelector('a[href*="/article/edit/"]');
      return a ? a.href.split('?')[0] : null;
    }""")


# ---------------------------------------------------------------- state


def read_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8-sig"))
    return {"posted": [], "history": []}


def write_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=4), encoding="utf-8")


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def cover_for(prefix: str, art: dict | None = None) -> Path:
    """Local asset first, then whatever image_url the published article recorded.

    Newer pieces get their cover generated at publish time (Pollinations) rather than
    committed as a jpg, so image_url is the only copy that exists.
    """
    hits = sorted(ASSETS.glob(f"{prefix}-*.jpg")) + sorted(ASSETS.glob(f"{prefix}-*.png"))
    if hits:
        return hits[0]
    url = (art or {}).get("image_url") or ""
    if url.startswith("http"):
        CACHE.mkdir(exist_ok=True)
        dest = CACHE / f"{prefix}.jpg"
        if dest.exists() and dest.stat().st_size > 1000:
            return dest
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "content-engine/linkedin"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            if len(data) > 1000:
                dest.write_bytes(data)
                log(f"  fetched cover for {prefix} from image_url ({len(data)} bytes)")
                return dest
            log(f"  cover download for {prefix} was {len(data)} bytes, ignoring")
        except Exception as exc:
            log(f"  cover download failed for {prefix}: {exc}")
    return ASSETS / f"{prefix}-missing.jpg"


# ---------------------------------------------------------------- commands


def fix_articles(prefixes: list[str], dry: bool) -> int:
    from playwright.sync_api import sync_playwright

    sources = load_sources()
    fixed = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        page = open_page(browser, ACTIVITY)
        mapping = refresh_map(page, sources)
        if not prefixes:
            prefixes = sorted(mapping)
        for prefix in prefixes:
            entry = mapping.get(prefix)
            art = sources.get(prefix)
            if not entry or not art:
                log(f"{prefix}: not published or no source, skipping")
                continue
            url = entry.get("edit") or edit_url(page, entry["pulse"])
            if not url:
                log(f"{prefix}: no edit link found")
                continue
            entry["edit"] = url
            MAP_FILE.write_text(json.dumps(mapping, indent=2), encoding="utf-8")

            blocks = blocks_from_markdown(art["body_markdown"])
            log(f"{prefix}: repairing {art['title'][:60]} ({len(blocks)} blocks)")
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            wait_editor(page)
            clear_body(page)
            compose(page, blocks)
            got_title = set_title(page, art["title"].strip())
            ok, shape = verify(page, blocks)
            log(f"  shape {shape}")
            if norm(got_title) != norm(art["title"]):
                log(f"  WARN: title mismatch '{got_title[:40]}' - not updating")
                continue
            set_cover(page, cover_for(prefix, art))
            SHOTS.mkdir(exist_ok=True)
            page.screenshot(path=str(SHOTS / f"fix_compose_{prefix}.png"))
            if not ok:
                log(f"  WARN: verification failed for {prefix} - not updating")
                continue
            if dry:
                log(f"  DRY: {prefix} composed, not updated")
                continue
            btn = page.get_by_role("button", name=re.compile(r"^(Update|Publish)$"))
            if not btn.count():
                log("  WARN: no Update button")
                continue
            btn.first.click()
            page.wait_for_timeout(4000)
            confirm = page.locator('.artdeco-modal button:has-text("Publish"), .artdeco-modal button:has-text("Update")')
            if confirm.count():
                confirm.first.click()
                page.wait_for_timeout(4000)
            page.wait_for_timeout(4000)
            page.screenshot(path=str(SHOTS / f"fix_done_{prefix}.png"))
            log(f"  UPDATED {prefix}")
            fixed += 1
    return fixed


def post_next(max_n: int, dry: bool, from_published: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    sources = load_sources()
    queue = json.loads(QUEUE.read_text(encoding="utf-8-sig"))
    if from_published:
        # queue.json only ever held the first 12 pieces. With --from-published every
        # article in _manual/published/ is fair game, so the drip keeps going as the
        # content engine adds new ones.
        known = {q["prefix"] for q in queue}
        queue = queue + [{"prefix": p, "title": a["title"]}
                         for p, a in sorted(sources.items()) if p not in known]
    state = read_state()
    done = list(state.get("posted", []))
    todo = [q for q in sorted(queue, key=lambda x: x["prefix"]) if q["prefix"] not in done]
    if not todo:
        log(f"queue empty - all {len(queue)} pieces published"
            f"{'' if from_published else ' (add --from-published to drip the newer ones)'}")
        return 0

    published = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        page = open_page(browser, ACTIVITY)
        mapping = refresh_map(page, sources)
        for q in todo[:max_n]:
            prefix = q["prefix"]
            art = sources.get(prefix)
            if not art:
                log(f"{prefix}: no source article in published/, skipping")
                continue
            if prefix in mapping and mapping[prefix].get("pulse"):
                log(f"{prefix}: already on the profile, recording as published")
                done.append(prefix)
                state["posted"] = done
                state.setdefault("history", []).append(
                    {"prefix": prefix, "at": stamp(), "note": "found on profile"})
                write_state(state)
                continue

            blocks = blocks_from_markdown(art["body_markdown"])
            log(f"{prefix}: composing {art['title'][:60]} ({len(blocks)} blocks)")
            page.goto(NEW_ARTICLE, wait_until="domcontentloaded", timeout=90_000)
            wait_editor(page)
            got_title = set_title(page, art["title"].strip())
            compose(page, blocks)
            ok, shape = verify(page, blocks)
            log(f"  shape {shape}")
            if norm(got_title) != norm(art["title"]):
                log(f"  WARN: title mismatch '{got_title[:40]}' - not publishing")
                break
            set_cover(page, cover_for(prefix, art))
            SHOTS.mkdir(exist_ok=True)
            page.screenshot(path=str(SHOTS / f"cdp_compose_{prefix}.png"))
            if not ok:
                log(f"  WARN: verification failed - not publishing {prefix}")
                break
            if dry:
                log(f"  DRY: {prefix} composed, not published")
                break

            page.get_by_role("button", name="Next").first.click()
            page.wait_for_timeout(4000)
            pub = page.locator('button:has-text("Publish")')
            if not pub.count():
                log("  WARN: no Publish button after Next")
                break
            pub.first.click()
            page.wait_for_timeout(9000)
            page.screenshot(path=str(SHOTS / f"cdp_published_{prefix}.png"))

            # a clicked button is not a published article: confirm on the profile
            page.goto(ACTIVITY, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(7000)
            seen = page.evaluate("(t) => document.body.innerText.indexOf(t) >= 0",
                                 art["title"].strip()[:60])
            if not seen:
                log(f"  WARN: '{art['title'][:40]}' not on the profile - not recording")
                page.screenshot(path=str(SHOTS / f"cdp_unverified_{prefix}.png"))
                break
            done.append(prefix)
            state["posted"] = done
            state.setdefault("history", []).append(
                {"prefix": prefix, "at": stamp(), "kind": "article", "title": art["title"]})
            write_state(state)
            log(f"  PUBLISHED ARTICLE {prefix}: {art['title']}")
            published += 1

    remaining = len([q for q in queue if q["prefix"] not in done])
    log(f"run complete: published {published} this run, {remaining} remaining")
    return published


def main() -> None:
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        raise SystemExit(__doc__)
    dry = "--dry" in args
    max_n = 1
    if "--max" in args:
        max_n = int(args[args.index("--max") + 1])

    if "--launch" in args:
        launch_chrome()
        return
    launch_chrome()

    if "--map" in args:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP)
            page = open_page(browser, ACTIVITY)
            mapping = refresh_map(page, load_sources())
        print(json.dumps(mapping, indent=2))
        return

    if "--fix-all" in args:
        fix_articles([], dry)
        return
    if "--fix" in args:
        prefixes = [a for a in args[args.index("--fix") + 1:] if re.fullmatch(r"\d{4}", a)]
        fix_articles(prefixes, dry)
        return
    if "--next" in args:
        post_next(max_n, dry, from_published="--from-published" in args)
        return
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
