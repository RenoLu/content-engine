# API Spikes

Small scripts to validate external APIs (auth, payload shape, automation
feasibility) **before** trusting a publisher in live mode. Findings are written up
in [`../docs/API_FINDINGS.md`](../docs/API_FINDINGS.md).

Run with the package on the path:

```bash
# PowerShell:  $env:PYTHONPATH="src"
# bash:        export PYTHONPATH=src
python spikes/github_search_spike.py
python spikes/publisher_smoke.py <publisher> [--live]
```

(The scripts also self-insert `src/` onto `sys.path`, so they run from the repo
root without installing the package.)

## `github_search_spike.py`

Hits the real GitHub Search API with the same "rising"/"active" queries the source
uses, prints the top results, and reports the search rate-limit headers. Works
unauthenticated; set `GITHUB_TOKEN` to raise the limit (10→30 req/min).

## `publisher_smoke.py <name> [--live]`

Builds a publisher from your `.env`, constructs a sample `Post`, and:

- **default (dry preview)**: prints whether the publisher is configured and the
  exact request payload it *would* send — no network call.
- **`--live`**: actually performs the publish via the official API (needs the
  publisher's credentials in `.env`). Start with the platform's *draft* option
  (`DEVTO_PUBLISHED=false`, `GHOST_POST_STATUS=draft`, …).

`<name>` is one of: `dryrun devto ghost wordpress hashnode bluesky mastodon
linkedin threads`.

## What each integration needs

| Publisher | Required `.env`                                              | Notes                              |
|-----------|-------------------------------------------------------------|------------------------------------|
| devto     | `DEVTO_API_KEY`                                             | draft via `DEVTO_PUBLISHED=false`  |
| ghost     | `GHOST_ADMIN_API_URL`, `GHOST_ADMIN_API_KEY`               | key is `id:secret`                 |
| wordpress | `WORDPRESS_BASE_URL/USERNAME/APP_PASSWORD`                  | self-hosted, HTTPS                 |
| hashnode  | `HASHNODE_API_KEY`, `HASHNODE_PUBLICATION_ID`              | PAT raw (no Bearer)                |
| bluesky   | `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD`                    | app password, not main password    |
| mastodon  | `MASTODON_BASE_URL`, `MASTODON_ACCESS_TOKEN`               | token from Prefs → Development     |
| linkedin  | `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN`             | needs verified app (see findings)  |
| threads   | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID`                  | needs approved Meta app            |

See `../docs/API_FINDINGS.md` for endpoints, scopes, rate limits, and gotchas.
