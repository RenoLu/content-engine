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

## Files here

- `post_next.ps1` : the poster (health check, page-identity guard, order guard, card guard)
- `queue.json`    : the 12 drafts (commentary + dev.to url; url unfurls the cover)
- `posted.json`   : state (which prefixes are done) — script-owned
- `post_log.txt`  : per-run log
- `shots/`        : screenshots per run for auditing
