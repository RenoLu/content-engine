"""Manual, supervised LinkedIn engagement pass via Kimi WebBridge.

Two stages so the agent can author comments in-context from the real post text:

  discover  navigate to a content search, extract posts, drop excluded/known
            authors + already-done targets, print the KEPT candidates as JSON.
  act       on the SAME already-open tab (NO reload, ordinals stay valid),
            like + comment the chosen posts and record each to the store.

Comments are authored by the agent (passed in via --actions JSON). No model API
is called here. Live actions on Yan's real account: stay tiny, stop if odd.

Usage (PYTHONPATH=src):
  python _manual/outreach_linkedin_kimi_run.py discover --query "data engineering"
  python _manual/outreach_linkedin_kimi_run.py act --actions path/to/actions.json

actions.json shape:
  [{"ordinal": 0, "author": "...", "text_key_src": "<cleaned text>",
    "like": true, "comment": "..."}]
  (comment optional; text_key_src must be the exact cleaned text from discover
   so the dedupe key matches.)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

sys.path.insert(0, "src")

from content_engine.outreach.linkedin_kimi import (  # noqa: E402
    KimiBridge, _EXTRACT_JS, _action_js, _clean_post_text, _exclusion_reason, _key,
)
from content_engine.outreach.models import ActionResult, ActionType  # noqa: E402
from content_engine.outreach.store import OutreachStore  # noqa: E402
from content_engine.config import load_settings  # noqa: E402

_SUBMIT_JS = ("(()=>{const b=[...document.querySelectorAll('button')]"
              ".find(x=>/^(post|comment)$/i.test((x.innerText||'').trim())&&!x.disabled);"
              "if(b){b.click();return 'sent';}return 'no_submit';})()")


def _out(obj) -> None:
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()


def _store() -> OutreachStore:
    settings = load_settings()
    return OutreachStore(settings.project_root / "data" / "outreach.sqlite3")


def _search_url(query: str) -> str:
    from urllib.parse import quote
    return ("https://www.linkedin.com/search/results/content/"
            f"?keywords={quote(query)}&sortBy=%22date_posted%22")


def discover(query: str) -> None:
    kimi = KimiBridge()
    kimi.navigate(_search_url(query), new_tab=True)
    time.sleep(7.0)
    raw = kimi.evaluate(_EXTRACT_JS) or "[]"
    try:
        rows = json.loads(raw)
    except Exception as exc:
        _out({"error": f"parse extract failed: {exc}", "raw": raw[:400]})
        return
    store = _store()
    extracted = len(rows)
    kept, excluded = [], []
    for r in rows:
        author = (r.get("author") or "").strip()
        text = _clean_post_text(r.get("text") or "")
        reason = _exclusion_reason(r)
        if reason:
            excluded.append({"author": author or "?", "reason": reason})
            continue
        if len(text) < 40:
            excluded.append({"author": author or "?", "reason": "empty/too short text"})
            continue
        k = _key(author, text)
        if store.already_done("linkedin", k, "like"):
            excluded.append({"author": author or "?", "reason": "already liked (store)"})
            continue
        kept.append({"ordinal": r.get("i"), "author": author, "key": k, "text": text})
    store.close()
    _out({"query": query, "extracted": extracted,
          "excluded_count": len(excluded), "excluded": excluded,
          "kept_count": len(kept), "kept": kept})


def act(actions_path: str) -> None:
    with open(actions_path, "r", encoding="utf-8") as f:
        actions = json.load(f)
    kimi = KimiBridge()
    store = _store()
    results = []
    likes = comments = 0
    for a in actions:
        ordinal = a["ordinal"]
        author = a.get("author", "")
        key = a.get("key") or _key(author, a.get("text_key_src", ""))
        rec = {"ordinal": ordinal, "author": author}
        # LIKE
        if a.get("like"):
            if likes >= 5:
                rec["like"] = "skipped_cap"
            else:
                outcome = kimi.evaluate(_action_js(ordinal, "like"))
                rec["like"] = str(outcome)
                if outcome == "no_post":
                    _out({"STOP": "like returned no_post; ordinals stale or page changed",
                          "at": rec, "done": results})
                    store.close()
                    return
                if outcome == "ok":
                    likes += 1
                    res = ActionResult("linkedin", ActionType.LIKE, key, "executed")
                    store.record(res, author=author)
                else:
                    res = ActionResult("linkedin", ActionType.LIKE, key, "failed", error=str(outcome))
                    store.record(res, author=author)
                time.sleep(6.0)
        # COMMENT
        comment = a.get("comment", "").strip()
        if comment:
            if comments >= 3:
                rec["comment"] = "skipped_cap"
            else:
                opened = kimi.evaluate(_action_js(ordinal, "reply"))
                if opened != "opened":
                    rec["comment"] = f"open_failed:{opened}"
                    _out({"STOP": "reply box did not open", "at": rec, "done": results})
                    store.close()
                    return
                time.sleep(1.5)
                kimi._cmd("fill", {"selector": "div.ql-editor[contenteditable=true], div[role=textbox][contenteditable=true]",
                                   "value": comment})
                time.sleep(1.2)
                sent = kimi.evaluate(_SUBMIT_JS)
                rec["comment"] = str(sent)
                rec["comment_text"] = comment
                if sent == "sent":
                    comments += 1
                    res = ActionResult("linkedin", ActionType.REPLY, key, "executed")
                    store.record(res, comment=comment, author=author)
                else:
                    res = ActionResult("linkedin", ActionType.REPLY, key, "failed", error=str(sent))
                    store.record(res, comment=comment, author=author)
                    _out({"STOP": "comment submit button not found", "at": rec, "done": results})
                    store.close()
                    return
                time.sleep(8.0)
        results.append(rec)
    store.close()
    _out({"totals": {"likes": likes, "comments": comments}, "results": results})


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--query", required=True)
    ac = sub.add_parser("act")
    ac.add_argument("--actions", required=True)
    args = p.parse_args()
    if args.cmd == "discover":
        discover(args.query)
    else:
        act(args.actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
