# Runbook

Operational guide: run locally, configure, dry-run vs live, schedule, and debug.

## Prerequisites

- Python 3.11+ (developed/tested on 3.14).
- That's it for a dry-run with the `mock` provider — no API keys required.

## 1. Install

```bash
cd content-engine
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
# or, to get the `content-engine` console command:
pip install -e .
```

## 2. Configure

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

Minimum to run offline (already the defaults in `.env.example`):

```
PUBLISH_MODE=dry_run
AI_PROVIDER=mock
ENABLED_PUBLISHERS=dryrun
```

- **Secrets** (API keys/tokens) → `.env`. **Tunables** (queries, thresholds,
  banned phrases) → `config/config.toml`.
- `GITHUB_TOKEN` is optional but recommended (raises Search rate limit 10→30/min).
- To use a real model: `AI_PROVIDER=openai` + `OPENAI_API_KEY` (or `anthropic` +
  `ANTHROPIC_API_KEY`). Models default to `gpt-4o-mini` / `claude-sonnet-4-6`;
  override with `OPENAI_MODEL` / `ANTHROPIC_MODEL`.

## 3. Run in dry-run mode (default, safe)

```bash
# via module (no install needed; set PYTHONPATH to src)
#   PowerShell:  $env:PYTHONPATH="src"; python -m content_engine run
#   bash:        PYTHONPATH=src python -m content_engine run
python -m content_engine run --dry-run

# or, if installed with `pip install -e .`:
content-engine run --dry-run
```

What happens: collect GitHub candidates → rank/filter → pick one repo → fetch
README → write → review → (revise) → quality gate → **simulate** publishing. A
markdown + JSON artifact is written to `OUTPUT_DIR` (default `output/`). Nothing
is posted externally. Everything is recorded in SQLite (`data/content_engine.sqlite3`).

Useful commands:

```bash
content-engine publishers          # which publishers are enabled + configured
content-engine list                # recent runs
content-engine show 2026-05-31     # full detail (draft, review, quality, publishes)
content-engine init-db             # create the DB schema explicitly
content-engine run --date 2026-05-31 --force   # re-run a date (re-picks same repo)
```

## 4. Run in live publishing mode

Live mode posts **only if** the reviewer approves **and** the deterministic
quality gate passes, and **only** to publishers you've enabled *and* credentialed.

1. Pick a real model: `AI_PROVIDER=openai` (or `anthropic`) + key.
2. Configure ≥1 publisher in `.env` (see `docs/API_FINDINGS.md` for each), e.g.
   DEV.to:
   ```
   ENABLED_PUBLISHERS=dryrun,devto
   DEVTO_API_KEY=...           # from Settings > Extensions
   DEVTO_PUBLISHED=false       # start with drafts!
   ```
3. **Validate first** with a spike (see §6) and/or `content-engine publishers`.
4. Go live:
   ```bash
   content-engine run --mode live
   #   or set PUBLISH_MODE=live in .env and run `content-engine run`
   ```

Safety properties:
- Default is `dry_run`; live requires an explicit flag/env.
- A publisher with missing credentials is **skipped**, never errors the run.
- One publisher failing does not abort the others (results stored per publisher).
- `publish_results` is `UNIQUE(run_date, publisher)` → no double-posting on re-run.
- The `dryrun` publisher always writes a local artifact, even in live mode.

> Tip: start every new publisher with its **draft** option (`DEVTO_PUBLISHED=false`,
> `GHOST_POST_STATUS=draft`, `WORDPRESS_POST_STATUS=draft`) and flip to publish
> once you trust the output.

## 5. Schedule daily execution

### GitHub Actions (recommended, included)

`.github/workflows/daily.yml` runs the pipeline daily.

1. Push this repo to GitHub.
2. Add repo secrets (Settings → Secrets and variables → Actions): `GITHUB_TOKEN`
   is provided automatically; add `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) and
   any publisher secrets.
3. Add repo variables for non-secrets: `AI_PROVIDER`, `PUBLISH_MODE`,
   `ENABLED_PUBLISHERS`.
4. The workflow commits the updated SQLite DB + `output/` back to the repo (so the
   no-reuse history persists between runs). Adjust if you prefer external storage.

Edit the `cron:` line to change the time (it's UTC).

### Alternatives (all run the same single command)

- **Render cron job**: command `python -m content_engine run`, set env vars,
  attach a persistent disk for `data/`.
- **Docker + host cron**: build an image, `CMD ["content-engine","run"]`, schedule
  with the host scheduler; mount a volume for `data/`.
- **AWS EventBridge → ECS/Lambda**: daily rule → run the container/command; back
  `data/` with EFS or swap `Store` for Postgres/RDS.

The pipeline is idempotent per date, so a duplicate trigger is harmless.

## 6. Validate an API before going live (spikes)

```bash
PYTHONPATH=src python spikes/github_search_spike.py            # no creds needed
PYTHONPATH=src python spikes/publisher_smoke.py devto          # dry preview of payload
PYTHONPATH=src python spikes/publisher_smoke.py devto --live   # real post (needs creds)
```

See `spikes/README.md`.

## 7. Debugging failures

- **More logs**: `--log-level DEBUG` (or `LOG_LEVEL=DEBUG`). Logs go to stderr.
- **Inspect a run**: `content-engine show <date>` prints the stored repo, draft,
  review, quality report, and per-publisher results as JSON.
- **`status: no_candidate`** — all candidates were filtered. Check `candidates`
  table / loosen `config.toml [ranking]` (e.g. `min_stars`, `min_readme_chars`) or
  widen the source queries.
- **`status: rejected`** (live) — content failed the gate. The `message` and the
  stored `final_json.quality` / `review` show why (banned phrase, high-severity
  issue, thin grounding). The repo is marked used so it isn't retried.
- **`status: failed`** — an exception; `error` column has the type/message. Common
  causes: GitHub 403 rate-limit (add `GITHUB_TOKEN`), model auth error (check key/
  provider), publisher 4xx (check credentials with the spike).
- **GitHub 403 / rate limit** — Search is 30/min auth, 10/min anon. Add a token.
- **Publisher `skipped`** — missing credentials; `content-engine publishers` shows
  which are configured.
- **Re-run a date** — `content-engine run --date <d> --force` (won't double-post
  where `publish_results` already records success).
- **Reset local state** — delete `data/*.sqlite3` (clears run + no-reuse history)
  and `output/`. Or point `DB_PATH` at a throwaway file for experiments.
