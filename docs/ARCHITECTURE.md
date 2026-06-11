# Architecture

The content engine is a daily batch pipeline that turns a trending GitHub
repository into a reviewed, technically-grounded post and (optionally) publishes
it to one or more platforms via their official APIs.

It is built around three interfaces so the moving parts are swappable:

| Concern        | Interface                         | MVP implementation                 |
|----------------|-----------------------------------|------------------------------------|
| Trend source   | `sources.base.Source`             | `GitHubTrendingSource` (Search API)|
| AI model       | `agents.model_client.ModelClient` | `mock` / `openai` / `anthropic`    |
| Publisher      | `publishers.base.BasePublisher`   | `dryrun` + 8 real platforms        |

Everything else (ranking, research, agents, storage, orchestration) is plain,
dependency-light Python (httpx + stdlib).

## High-level system design

```
                ┌─────────────────────────────────────────────────────────┐
                │                      Pipeline (orchestrator)             │
                │                                                          │
  GitHub  ─────▶│ 1. collect ─▶ 2. rank/filter ─▶ 3. select ─▶ 4. research │
 Search API     │                                              (README)    │
                │      │                                            │       │
                │      ▼                                            ▼       │
                │ 5. write (Writer agent) ─▶ 6. review (Reviewer agent)     │
                │            ▲                        │                     │
                │            └──── 7. revise ◀────────┘ (if needed)         │
                │                                                          │
                │ 8. quality gate (deterministic) ─▶ 9. publish/simulate    │
                │                                            │             │
                └────────────────────────────────────────────┼─────────────┘
                                                              ▼
                            ┌──────────────┬──────────────┬───────────────┐
                            │ dryrun (file)│ DEV.to/Ghost │ Bluesky/...    │
                            └──────────────┴──────────────┴───────────────┘
                                              │
                                              ▼
                                     SQLite (runs, candidates,
                                     repo_history, publish_results)
```

## Pipeline flow

Implemented in `pipeline.Pipeline.run()`:

1. **Collect** — `Source.fetch_candidates()` returns candidate `Repository`
   objects. GitHub source issues two Search API queries ("rising" = recently
   created + starred, "active" = popular + recently pushed) and de-dupes them.
2. **Rank / filter** — `RepoRanker.prefilter_and_score()` applies metadata hard
   filters (archived, fork, min stars, description, blocklist, **already
   featured**) and computes a transparent weighted score. Skip reasons are
   recorded for every rejected candidate.
3. **Select** — iterate eligible repos by score; for each, fetch the README
   (`RepoResearcher.enrich`) and apply the thin-README filter. The first repo
   that passes is selected (caps README fetches at `MAX_SELECTION_ATTEMPTS`).
4. **Research** — README markdown, length, and extracted links are attached to
   the selected repo (already done during selection).
5. **Write** — `WriterAgent` builds a grounded prompt (repo fact sheet) and asks
   the model for a JSON draft `{title, summary, tags, angle, body_markdown}`.
6. **Review** — `ReviewerAgent` scores the draft against the fact sheet and
   returns structured JSON (approved, score, severity, issues[], action).
7. **Revise** — if the reviewer doesn't approve and didn't say "reject",
   `ReviserAgent` rewrites the draft (up to `review.max_revisions` rounds) and
   the reviewer re-scores.
8. **Quality gate** — `quality.run_quality_checks()` runs deterministic checks
   (banned phrases, repo grounding, headings, placeholders, length). The content
   is *approved to publish* only if **reviewer approves AND quality passes**.
9. **Publish / simulate** — `publishers` run. In `dry_run` mode (or when the gate
   fails) nothing is posted externally; the `dryrun` publisher always writes a
   local markdown+JSON artifact. In `live` mode, approved content is posted to
   each configured real publisher, with per-publisher failure isolation and
   duplicate-post prevention.
10. **Persist** — run status, draft, review, quality, candidates, publish results
    and the repo→history dedup key are written to SQLite at each step.

## Major components

```
src/content_engine/
  config.py            # TOML + env -> typed Settings (frozen dataclasses)
  models.py            # Repository, Draft, ReviewResult, Post, PublishResult, ...
  pipeline.py          # the orchestrator + PipelineSummary
  cli.py / __main__.py # `content-engine run|list|show|publishers|init-db`
  logging_setup.py     # stderr logging
  quality.py           # deterministic final-quality gate

  sources/             # Source interface + GitHub (Search API) + GitHubClient
  ranking/             # hard filters + weighted scoring
  research/            # README enrichment
  agents/              # ModelClient (mock/openai/anthropic), prompts,
                       #   writer / reviewer / reviser, JSON parsing
  publishers/          # BasePublisher (dry-run + failure containment),
                       #   registry, dryrun, devto, ghost, wordpress, hashnode,
                       #   bluesky, mastodon, linkedin, threads, util
  storage/             # SQLite Store (DAO) + schema
```

## Data model

SQLite (`stdlib sqlite3`, no ORM). JSON blobs hold nested structures; first-class
columns hold anything queried or constrained.

- **runs** — one row per day. `run_date` is **UNIQUE** → one post per day,
  idempotent per date. Columns: status, mode, repo_full_name, repo_json,
  draft_json, review_json, final_json, skip_reason, error, timestamps.
- **repo_history** — `full_name` PRIMARY KEY → a repo is **never featured twice**.
- **candidates** — every candidate considered for a date, with score + skip
  reason (debuggability / "store skipped reasons").
- **publish_results** — `UNIQUE(run_date, publisher)` → **no double-posting**;
  upserted so re-runs update rather than duplicate.

See `storage/store.py` for the DDL.

## Publisher abstraction

`BasePublisher` centralizes the two safety guarantees so individual publishers
can't get them wrong:

1. **Dry-run is handled once, in the base class.** In dry-run mode `publish()`
   returns a `dry_run` result with a rendered payload preview and never touches
   the network.
2. **Failures are contained.** A live publish that raises becomes a `failed`
   `PublishResult`; one broken publisher never aborts the run or other publishers.

A concrete publisher implements only `is_configured()`, `render_payload(post)`
(used for the dry-run preview and the live call), and `_publish_live(post)`.
`build_publishers()` instantiates the set named in `ENABLED_PUBLISHERS` and always
prepends `dryrun` so a local artifact is written on every run.

Publishers use **official APIs only** — no scraping, browser automation, or
session hijacking. Platforms requiring app approval (LinkedIn, Threads) are fully
implemented but will run in skipped/dry-run mode until credentials exist.

## Agent responsibilities

- **Writer** — produces a grounded draft from the repo fact sheet. Told to use
  only the supplied facts and to avoid banned marketing phrases.
- **Reviewer** — fact-checks against the fact sheet, flags unsupported/exaggerated
  claims, weak technical explanations, hallucinations, and AI-sounding filler.
  Returns structured JSON. The *policy* (min score, block-on-high-severity) lives
  in config (`ReviewerAgent.is_approved`), not in the model.
- **Reviser** — rewrites to address every reviewer issue without adding
  unsupported claims.

All three share one `ModelClient`, so switching providers (or to a local model)
is a one-line config change. The `mock` provider runs the entire pipeline offline
with deterministic output — used by tests and the zero-setup first run.

## Scheduler / deployment

MVP target: **GitHub Actions cron** (`.github/workflows/daily.yml`) — zero infra,
secrets via repo settings, runs `content-engine run` once daily. The pipeline is
idempotent per date, so an accidental double-trigger is safe. Alternatives
(Render cron, a scheduled Docker container, AWS EventBridge→ECS) are drop-in
because the entrypoint is a single CLI command. See `docs/RUNBOOK.md`.

## Important design tradeoffs

- **Search API instead of scraping `github.com/trending`.** There is no official
  trending API. Scraping the HTML page is brittle and discouraged by GitHub's
  acceptable-use policy. The Search API is an *approximation* (it can't sort by
  star-velocity over a window) but it is documented, stable, and compliant. The
  source interface isolates this choice. See `docs/API_FINDINGS.md`.
- **Direct HTTP calls instead of vendor SDKs.** Calling OpenAI/Anthropic/GitHub
  REST endpoints via httpx keeps the dependency tree tiny (httpx + python-dotenv),
  dodges Python-3.14 wheel issues, and makes everything trivially mockable with
  `httpx.MockTransport`.
- **dataclasses + sqlite3 instead of pydantic + an ORM.** Less magic, fewer
  dependencies, faster cold start; validation that matters is explicit.
- **Two gates (AI reviewer + deterministic quality).** The model catches nuance;
  the code catches objective failures it might wave through (banned phrases,
  missing grounding). Both must pass to publish.
- **Dry-run is the default and is enforced centrally.** The system cannot post
  externally unless `PUBLISH_MODE=live` *and* the content passes review — and even
  then only to explicitly enabled, credentialed publishers.
- **One repo, one day, never reused.** Simplicity and a clean content cadence over
  throughput. Easily relaxed later (the dedup key is per-repo, not per-day).
