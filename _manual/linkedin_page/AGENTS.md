# Agent Palisade — LinkedIn page daily post (Codex runbook)

You are running headless, once per day, to publish ONE queued article to the Agent
Palisade LinkedIn company page. The posting itself is done by a deterministic
PowerShell script; your job is to run it, confirm the result, and report. Do NOT
re-implement the browser/DOM logic yourself.

## Do exactly this

1. Run the poster (posts the next unposted article, oldest first, max 1):

   powershell -ExecutionPolicy Bypass -File post_next.ps1 -Max 1

2. Read the tail of `post_log.txt` (the script appends a timestamped line per run).

3. Report ONE short line as your final message, based on the log:
   - If a line `POSTED NNNN (...)` appeared this run  -> "posted NNNN, <remaining> remaining"
   - If `BRIDGE DOWN`                                  -> "skipped: Kimi bridge down (Chrome/extension not connected)"
   - If `ABORT` / order-guard / card WARN             -> "skipped NNNN: <reason from log>, left unposted"
   - If `queue empty`                                 -> "done: all articles posted"

## Hard rules

- Post ONLY through `post_next.ps1`. Never open the composer or type into LinkedIn yourself.
- Never click "Continue" (reshares to a personal profile) or "Redeem"/"Claim" (paid trial).
  The script already dismisses those safely; do not second-guess it.
- Do not edit `queue.json` or `posted.json` by hand. The script owns that state.
- If the bridge is down or the script reports an abort, STOP. Do not retry more than once,
  and never fall back to posting manually. A missed day is fine; the next run continues.
- Post at most 1 article per run.

## The queue tops itself up before you run

`codex_run.ps1` runs `company_top_up.py` ahead of your step. It appends a queue entry for
every published article that has a dev.to URL and a blurb in `company_blurbs.json`, so the
queue you read is already current. Write blurbs into `company_blurbs.json` for future
articles; never hand-edit `queue.json`.

## The personal-profile lane is not yours to run

`codex_run.ps1` runs it itself, right after your step, as `article_cdp.py --next --max 1`
on the debug Chrome (:9222). Do not invoke it, and do not touch `posted_personal.json`.

## Files here

- `post_next.ps1` : the company-page poster (health check, page-identity guard, order guard, card guard)
- `queue.json`    : the 12 drafts (commentary + dev.to url; url unfurls the cover)
- `posted.json`   : company-page state (which prefixes are done) — script-owned
- `post_log.txt`  : per-run log
- `shots/`        : screenshots per run for auditing
- `article_cdp.py`: personal-profile lane (formatted native Articles + cover image, over CDP)
- `company_top_up.py`, `company_blurbs.json`, `devto_urls.json` : company-queue refill (blurb + dev.to URL pairing)
- `post_cdp_log.txt`, `articles_map.json`, `posted_personal.json` : that lane's log, prefix→URL map, state
- `post_personal.ps1` : the retired Kimi version of the personal lane; kept for its notes only

## Article formatting (personal lane)

Articles published before 2026-07-26 went out as a flat wall of paragraphs: the Kimi
bridge's synthetic clicks cannot open the editor's Style dropdown, so `##` headings became
ordinary body text and bullet lists became prose. `article_cdp.py` fixes both directions:

    python article_cdp.py --next --max 1     # publish the next queued piece, formatted
    python article_cdp.py --fix 0011 0010    # reformat already-published articles + cover
    python article_cdp.py --fix-all          # every article found on the profile

All 8 articles published up to 2026-07-26 have been repaired (headings, lists, cover image).
