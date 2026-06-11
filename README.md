# Content Engine

An automated AI content engine. Every day it:

1. finds a **trending GitHub repository** (via the official Search API — no scraping),
2. researches it (README, metadata, topics, stars, activity, links),
3. writes a **technical post** with an AI **writer** agent,
4. has a second AI **reviewer** agent fact-check it (structured JSON) and a
   **reviser** fix issues,
5. runs a deterministic **quality gate**, and
6. **publishes** via official platform APIs — but only if the content passes
   review, and **dry-run by default** so nothing is posted by accident.

Built to be practical, testable, and easy to extend with new sources, models, and
publishers. Runtime deps: just `httpx` + `python-dotenv` (stdlib `sqlite3` for
storage). Runs fully offline with the bundled `mock` model provider.

## Quick start (zero setup, dry-run)

```bash
cd content-engine
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # macOS/Linux: source .venv/bin/activate
pip install -e .

content-engine run --dry-run
content-engine show $(date -u +%F)     # inspect the generated + reviewed content
```

This queries live GitHub, picks a repo, generates and reviews a post with the
offline `mock` model, and writes a markdown + JSON artifact to `output/`. No API
keys required.

To use a real model and/or publish, see **[docs/RUNBOOK.md](docs/RUNBOOK.md)**.

## How it works

```
GitHub Search API ─▶ rank/filter ─▶ pick 1 repo/day ─▶ fetch README
   ─▶ Writer agent ─▶ Reviewer agent ⇄ Reviser ─▶ quality gate
   ─▶ publish (dry-run by default; live only if approved)  ─▶ SQLite
```

Three swappable interfaces keep it modular:

- **Source** (`sources/`) — MVP: GitHub only. Add Hacker News/RSS later without
  touching the pipeline.
- **ModelClient** (`agents/`) — `mock` | `openai` | `anthropic` (direct HTTP, easy
  to add more).
- **Publisher** (`publishers/`) — `dryrun` (always) + DEV.to, Ghost, WordPress,
  Hashnode, Bluesky, Mastodon, LinkedIn, Threads. Dry-run and failure-containment
  are handled once in the base class.

## Configuration

- **Secrets** → `.env` (copy from `.env.example`).
- **Tunables** (queries, thresholds, scoring weights, banned phrases) →
  `config/config.toml`.

Key switches:

| Variable             | Default    | Meaning                                  |
|----------------------|------------|------------------------------------------|
| `PUBLISH_MODE`       | `dry_run`  | `dry_run` (safe) or `live`               |
| `AI_PROVIDER`        | `mock`     | `mock` / `openai` / `anthropic`          |
| `ENABLED_PUBLISHERS` | `dryrun`   | comma-separated publisher names          |
| `GITHUB_TOKEN`       | *(empty)*  | optional; raises Search rate limit       |

## CLI

```bash
# a global --log-level applies to every command:
content-engine [--log-level DEBUG|INFO|WARNING|ERROR] <command> ...

content-engine run [--date YYYY-MM-DD] [--mode dry_run|live] [--dry-run] [--force]
content-engine list                 # recent runs
content-engine show <date>          # full detail for a run
content-engine publishers           # enabled + configured status per publisher
content-engine init-db              # create the SQLite schema
```

## Behavior guarantees

- **Dry-run by default** — never posts unless `PUBLISH_MODE=live` *and* the content
  passes review.
- **One post per day**, idempotent per date (re-runs are safe; `--force` to redo).
- **A repo is never featured twice.**
- Skips archived/fork/thin-README/low-quality repos — with **stored reasons**.
- Publish only if the **reviewer approves AND the quality gate passes**.
- **No double-posting**; per-publisher failures are isolated.

## Tests

```bash
pip install -e ".[dev]"
pytest            # 96 tests: ranking, source, http retry, model client,
                  # config, logging, reviewer parsing, dry-run,
                  # dedup/idempotency, quality gate, prompts, full pipeline
```

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — design, data model, tradeoffs.
- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — roadmap & status.
- **[docs/API_FINDINGS.md](docs/API_FINDINGS.md)** — per-platform API research.
- **[docs/RUNBOOK.md](docs/RUNBOOK.md)** — run, configure, schedule, debug.
- **[docs/CONTENT_STRATEGY.md](docs/CONTENT_STRATEGY.md)** — audience, style, reviewer rules.
- **[spikes/README.md](spikes/README.md)** — API validation scripts.

## Project layout

```
src/content_engine/   sources/ ranking/ research/ agents/ publishers/ storage/
                      config.py models.py pipeline.py quality.py cli.py
config/config.toml    tunables (non-secret)
spikes/               API validation scripts
tests/                unit + integration tests
docs/                 design + ops documentation
.github/workflows/    daily.yml (scheduled run)
```

## License

MIT (see `LICENSE`). Uses only official platform APIs; no scraping or browser
automation.
