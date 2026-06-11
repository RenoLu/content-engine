# API Findings

Findings from researching each external API (official docs, 2026) plus the spike
scripts in `spikes/`. Secrets are redacted. "MVP action" classifies how each
integration ships:

- **implement-real** — official API, an individual dev can self-serve credentials,
  fully automatable today.
- **interface-dryrun** — implemented to the same interface but needs app approval /
  complex OAuth / paid access, so it runs in dry-run/skipped mode until credentials exist.

## Summary

| Platform   | Category  | Auth                         | Draft | Media | Automate | Approval | MVP action        |
|------------|-----------|------------------------------|-------|-------|----------|----------|-------------------|
| GitHub     | source    | Bearer PAT (optional)        | n/a   | n/a   | yes      | no       | implement-real    |
| DEV.to     | publisher | `api-key` header             | yes   | URL   | yes      | no       | implement-real    |
| Ghost      | publisher | Admin key → HS256 JWT        | yes   | yes   | yes      | no       | implement-real    |
| WordPress  | publisher | App Password (Basic)         | yes   | yes   | yes      | no       | implement-real    |
| Hashnode   | publisher | PAT (raw, no Bearer)         | yes   | URL   | yes      | no       | implement-real    |
| Bluesky    | publisher | App password → session JWT   | n/a   | yes   | yes      | no       | implement-real    |
| Mastodon   | publisher | Bearer access token          | n/a   | yes   | yes      | no       | implement-real    |
| LinkedIn   | publisher | OAuth2 3-legged (`w_member_social`) | yes | yes | partial | app verify | interface-dryrun* |
| Threads    | publisher | OAuth2 (`threads_content_publish`)  | no  | yes | yes     | yes      | interface-dryrun  |

\* LinkedIn member posting is an official self-serve path, but it needs one-time
app verification against a Company Page and token refresh for unattended use, so
we ship it implemented-but-dry-run until a token is supplied.

Required environment variables for each integration are listed in `.env.example`.

---

## GitHub (source) — implement-real

- **Base URL**: `https://api.github.com`
- **Auth**: `Authorization: Bearer <PAT>` (optional but recommended). Also send
  `X-GitHub-Api-Version: 2022-11-28`. A fine-grained PAT needs only public-repo
  read (Metadata + Contents); a token with *no* scopes still raises your limits.
- **Endpoints used**:
  - `GET /search/repositories?q=...&sort=stars&order=desc&per_page=50` — approximate trending.
  - `GET /repos/{owner}/{repo}` — full metadata.
  - `GET /repos/{owner}/{repo}/readme` with `Accept: application/vnd.github.raw+json` — raw README.
- **Trending approximation** (two merged queries):
  - rising: `stars:>=75 created:>=<recent>`
  - active: `stars:>=150 pushed:>=<recent>`
- **Rate limits**: Search = **30 req/min** authenticated (10/min anon). Core =
  5,000/hr authenticated (60/hr anon). Search caps at **1,000 results/query**.
  Honor `x-ratelimit-*` and `Retry-After`.
- **Gotchas**: no official trending API (Search is an approximation — it can't
  sort by star-velocity); multiple `language:`/`topic:` qualifiers AND together
  (one language per query); `incomplete_results: true` means a server-side timeout.
- **Why not scrape `github.com/trending`?** No API, fragile HTML, discouraged by
  GitHub's acceptable-use policy. Isolated behind `Source` if ever needed.

```
GET /search/repositories?q=stars:>=150 pushed:>=2026-05-17&sort=stars&order=desc&per_page=50
-> { "total_count": 412, "incomplete_results": false,
     "items": [ { "full_name": "owner/x", "stargazers_count": 1280, "topics": [...], ... } ] }
```

---

## DEV.to / Forem (publisher) — implement-real

- **Base URL**: `https://dev.to/api` · **Endpoint**: `POST /api/articles`
- **Auth**: `api-key: <key>` header. Generate at Settings → Extensions → DEV API Keys.
- **Body** (note the `article` wrapper): `{"article": {title, body_markdown,
  published, tags[≤4], canonical_url, description, main_image}}`. `published`:
  `false`=draft, `true`=publish. **Markdown is native.**
- **Draft**: yes (`published:false`). **Media**: no upload endpoint — `main_image`
  must be a pre-hosted URL.
- **Rate limits**: ~9–10 article creations / 30s; brand-new accounts throttled to
  ~1/30s (anti-spam). 429 + `Retry-After`.
- **Gotchas**: max 4 tags, lowercase/alphanumeric (the publisher normalizes them);
  set `canonical_url` when cross-posting; duplicate titles can be rejected.
- **Env**: `DEVTO_API_KEY`, `DEVTO_PUBLISHED`.

```
POST /api/articles  (api-key: REDACTED)
{"article":{"title":"...","body_markdown":"...","published":false,"tags":["rust","cli"]}}
-> 201 {"id":145911,"url":"https://dev.to/user/slug-1a2b","slug":"...","published":false}
```

---

## Ghost (publisher) — implement-real

- **Base URL**: `{admin_api_url}/ghost/api/admin/` · **Endpoint**:
  `POST /ghost/api/admin/posts/?source=html`
- **Auth**: Admin API key `id:secret` → short-lived **HS256 JWT**. Header
  `{alg:HS256, typ:JWT, kid:id}`; payload `{iat, exp:iat+300, aud:"/admin/"}`;
  **sign with `bytes.fromhex(secret)`**. Send `Authorization: Ghost <jwt>` (the
  literal word "Ghost", not Bearer). We sign this with stdlib hmac (no PyJWT).
- **Body**: `{"posts":[{title, html, status, tags:[{name}], canonical_url}]}`. With
  `?source=html`, Ghost converts `html`→Lexical. `status`: draft|published|scheduled.
- **Draft**: yes. **Media**: yes (`POST /images/upload/`; we send `feature_image`
  URLs / text only for MVP).
- **Gotchas**: hex-decode the secret (raw hex string → 401); JWT must expire ≤5
  min; body is a `posts` array; HTML→Lexical is lossy for exotic markup; include
  `Accept-Version: v5.0`.
- **Env**: `GHOST_ADMIN_API_URL`, `GHOST_ADMIN_API_KEY`, `GHOST_POST_STATUS`.

---

## WordPress self-hosted (publisher) — implement-real

- **Base URL**: `https://{site}/wp-json/wp/v2` · **Endpoint**: `POST /posts`
- **Auth**: **Application Passwords** (core since 5.6) → HTTP Basic
  `base64(user:app_password)` over **HTTPS**. Generate at Users → Profile →
  Application Passwords.
- **Body**: `{title, content (HTML), excerpt, status}`. `status`:
  draft|publish|future|pending|private.
- **Draft**: yes. **Media**: yes (`POST /media` with raw bytes →
  `featured_media` ID).
- **Gotchas**: `content` expects **HTML** (we convert markdown); `categories`/
  `tags` must be integer term **IDs** (we omit by default — assign IDs as an
  enhancement); some hosts strip the `Authorization` header (needs an .htaccess
  rule); publishing needs Author+ capability. **WordPress.com is a different API**
  (OAuth2, `/rest/v1.1`) — not supported by this publisher.
- **Env**: `WORDPRESS_BASE_URL`, `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD`,
  `WORDPRESS_POST_STATUS`.

---

## Hashnode (publisher) — implement-real

- **Endpoint**: `POST https://gql.hashnode.com` (GraphQL, POST-only).
- **Auth**: Personal Access Token in `Authorization` header — **raw value, no
  "Bearer" prefix**. Generate at hashnode.com/settings/developer.
- **Mutation**: `publishPost(input: PublishPostInput!)` with `{title,
  contentMarkdown, publicationId, tags:[{slug,name}]}`. **Markdown is native.**
- **publicationId**: a 24-char ObjectId — fetch once via `query { me {
  publications(first:10){edges{node{id title url}}} } }` (not shown in the UI).
- **Draft**: yes (`createDraft` → `publishDraft`). **Media**: no upload — cover/
  inline images must be hosted URLs. **Rate limit**: ~20,000 req/min.
- **Gotchas**: `tags` is required (provide ≥1, existing slug+name); raw PAT (no
  Bearer); GET → 405.
- **Env**: `HASHNODE_API_KEY`, `HASHNODE_PUBLICATION_ID`.

---

## Bluesky / AT Protocol (publisher) — implement-real

- **Host**: `https://bsky.social` (account PDS) · XRPC under `/xrpc/`.
- **Auth**: `POST /xrpc/com.atproto.server.createSession` with `{identifier:
  handle, password: app_password}` → `{accessJwt, did}`. Use an **App Password**
  (Settings → App Passwords), not the main password. Send `Authorization: Bearer
  <accessJwt>`.
- **Post**: `POST /xrpc/com.atproto.repo.createRecord` with `{repo: did,
  collection: "app.bsky.feed.post", record: {$type, text, createdAt, langs,
  facets}}`.
- **Limits**: **300 graphemes**. Links aren't auto-detected — we attach a
  `app.bsky.richtext.facet#link` facet computed from the URL's **UTF-8 byte
  offsets** so it's clickable. Images via `uploadBlob` → `app.bsky.embed.images`
  (text-only for MVP).
- **Gotchas**: byte offsets (not char offsets) for facets; accessJwt is short-lived
  (refresh via `refreshSession`).
- **Env**: `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD`, `BLUESKY_PDS_URL`.

---

## Mastodon (publisher) — implement-real

- **Base URL**: `https://{instance}` · **Endpoint**: `POST /api/v1/statuses`
- **Auth**: `Authorization: Bearer <token>`. Easiest token: create an application
  in Preferences → Development on your instance (gives an access token directly;
  no OAuth round-trip needed).
- **Body**: `{status, visibility (public|unlisted|private|direct), language}`.
  Send an **`Idempotency-Key`** header (we hash repo+title) so retries don't
  double-post.
- **Limits**: default 500 chars (instance-configurable); ~300 req / 5 min. Media
  via `POST /api/v2/media` → `media_ids` (text-only for MVP).
- **Env**: `MASTODON_BASE_URL`, `MASTODON_ACCESS_TOKEN`, `MASTODON_VISIBILITY`.

---

## LinkedIn (publisher) — interface-dryrun (member posting is real but gated)

- **Endpoint**: `POST https://api.linkedin.com/rest/posts`
- **Auth**: OAuth 2.0 3-legged access token with **`w_member_social`** (+ `openid
  profile`). Headers: `Authorization: Bearer`, `LinkedIn-Version: 202506`,
  `X-Restli-Protocol-Version: 2.0.0`.
- **Author URN**: `urn:li:person:{sub}` where `sub` comes from
  `GET /v2/userinfo` (no profile-URL→URN lookup).
- **Body**: `{author, commentary, visibility:"PUBLIC", distribution{...},
  lifecycleState:"PUBLISHED", isReshareDisabledByAuthor:false}`. The created post
  URN comes back in the **`x-restli-id` response header** (empty body).
- **Why gated**: the app must be verified against a Company Page (one-time), and
  member tokens expire (~60 days) so unattended use needs refresh-token handling
  → `can_fully_automate = partial`. Company-page posting is a *different*,
  approval-gated API (Community Management).
- **Env**: `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN`, `LINKEDIN_API_VERSION`.

---

## Threads / Meta (publisher) — interface-dryrun

- **Base**: `https://graph.threads.net/v1.0`
- **Auth**: OAuth2 token with **`threads_basic` + `threads_content_publish`** from
  an **approved Meta app** (app review required → `approval_required: yes`).
- **Two-step publish**: `POST /{user-id}/threads?media_type=TEXT&text=...` →
  `creation_id`, then `POST /{user-id}/threads_publish?creation_id=...` →
  media id; permalink via `GET /{media-id}?fields=permalink`.
- **Limits**: 250 posts / 24h. **Draft**: no.
- **Env**: `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID`.

---

## Cross-cutting notes

- **No scraping / browser automation anywhere.** Official APIs only.
- **Markdown handling**: DEV.to and Hashnode accept markdown natively. Ghost and
  WordPress require HTML, produced by `publishers/util.markdown_to_html`
  (headings, fenced code, ordered/unordered lists, blockquotes, horizontal rules,
  GitHub-flavored tables, images, links, inline emphasis/code — with HTML- and
  attribute-escaping so a crafted URL can't break out of an `href`/`src`). It is
  *not* full CommonMark (no nested lists or reference-style links). Microblogs
  (Bluesky/Mastodon/Threads/LinkedIn) get plain text from the draft `summary` via
  `microblog_text`.
- **Resilience**: all outbound GitHub and model-provider calls go through
  `http_util.request_with_retry`, which retries 429/502/503/504 and GitHub 403
  rate-limit responses with bounded exponential backoff, honoring `Retry-After`
  and `x-ratelimit-reset`.
- **Secrets** never live in config files — only `.env` / deployment env.
- **Validation**: run the spikes in `spikes/` to confirm auth/payloads against your
  own accounts before enabling a publisher in live mode.
