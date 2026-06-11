# Implementation Plan

## MVP scope (target)

A daily, dry-run-by-default pipeline that:

- collects trending repos from **GitHub only** (Search API, compliant),
- ranks/filters and picks **one repo per day** (never reused),
- researches the repo (README + metadata + links),
- writes a technical post with a **writer agent**,
- reviews it with a **reviewer agent** (structured JSON) + **revision** step,
- runs a **deterministic quality gate**,
- publishes via a **publisher abstraction** (dry-run always; real publishers when
  credentialed), defaulting to **dry-run** and never posting unless approved,
- stores everything in SQLite, idempotent per date,
- ships with docs, tests, `.env.example`, and a scheduler.

## Phased roadmap

### Phase 0 — Foundations ✅ (done)
- Project skeleton, `pyproject.toml`, `.env.example`, config loader (TOML + env).
- Data model (dataclasses) + SQLite store with idempotency/dedup constraints.
- Logging, CLI scaffold.

### Phase 1 — Source + ranking ✅ (done)
- `GitHubClient` (search / repo / readme) with injectable httpx client.
- `GitHubTrendingSource` (rising + active queries, de-dupe).
- Hard filters (archived, fork, min stars, description, blocklist, already-used)
  with stored skip reasons; transparent weighted scoring.

### Phase 2 — Agents ✅ (done)
- `ModelClient` abstraction: `mock` (offline/deterministic), `openai`, `anthropic`.
- Prompt builders (grounded fact sheet, banned-phrase injection, task markers).
- Writer / Reviewer / Reviser agents; robust JSON extraction.

### Phase 3 — Quality + publishing ✅ (done)
- Deterministic quality gate (banned phrases, grounding, structure, placeholders).
- `BasePublisher` (central dry-run + failure containment), registry.
- Publishers: `dryrun` (always) + DEV.to, Ghost, WordPress, Hashnode, Bluesky,
  Mastodon, LinkedIn, Threads.

### Phase 4 — Orchestration + idempotency ✅ (done)
- `Pipeline` wiring all stages with DI for testability.
- One-per-day, no-reuse, skip-with-reasons, publish-only-if-approved, no
  double-post, `--force` re-run.

### Phase 5 — Docs, tests, deploy ✅ (done)
- 5 design docs + README; API research → `API_FINDINGS.md`.
- Test suite (ranking, source, reviewer parsing, publisher dry-run, dedup,
  quality, prompts, full pipeline).
- API spikes in `spikes/`; GitHub Actions daily cron.

### Phase 6 — Hardening (partly done)
- ✅ Retry/backoff wrapper honoring `Retry-After` + GitHub `x-ratelimit-*`
  (`http_util.request_with_retry`, wired into the GitHub client and both model
  clients).
- Real OpenAI/Anthropic run validation with live keys + cost/latency tuning.
- Per-publisher integration tests against sandbox accounts.
- Optional featured-image upload per platform (currently URL/text only).
- **Partial-failure retry**: a run that posts to publisher A then crashes on B is
  marked FAILED (non-terminal) and retried on the next invocation. Double-posting
  to A is already prevented (`already_published` only blocks genuine live posts),
  but the retry currently re-selects a fresh repo/draft rather than resuming the
  original. Future: persist-and-reuse the approved draft on a FAILED retry.

### Phase 7 — Extensions (future)
- Additional sources behind `Source` (Hacker News, RSS, awesome-lists) with a
  multi-source merge/ranker. (Explicitly out of MVP scope.)
- Multiple posts/day or per-platform content variants.
- Scheduling/queueing of drafts; human-in-the-loop approval UI.
- Postgres backend (swap `Store`) for multi-runner deployments.

## Currently completed work

Everything in Phases 0–5. The pipeline runs end-to-end against live GitHub in
dry-run with the `mock` provider (no API keys needed) and writes a local artifact.
All publishers implement the interface; `dryrun`, DEV.to, Ghost, WordPress,
Hashnode, Bluesky, and Mastodon are "implement-real" and automatable today (see
`API_FINDINGS.md`). 96 unit/integration tests pass.

## Remaining work / next steps

- Validate a live LLM run (set `AI_PROVIDER=openai|anthropic` + key).
- Add a small retry/backoff layer for GitHub + publisher 429s.
- Wire real publisher credentials and do a single live post per platform behind a
  sandbox/test account before enabling in production.

## Known blockers / risks

- **LinkedIn**: posting needs an app verified against a Company Page + 3-legged
  OAuth and token refresh for unattended use → ships as implement-real but
  effectively dry-run until a token is provided. `partial` automation.
- **Threads**: requires an approved Meta app with `threads_content_publish` →
  dry-run until approved.
- **GitHub trending is an approximation** (no star-velocity sort via Search API).
- **Search rate limit** is 30/min authenticated (10/min anon) — fine for the
  2 queries/day MVP, but batch if you broaden queries.
- **Markdown→HTML** for Ghost/WordPress uses a minimal converter (headings, code,
  lists, links, emphasis) — not full CommonMark. Complex markdown may render
  imperfectly; documented in `API_FINDINGS.md`.
