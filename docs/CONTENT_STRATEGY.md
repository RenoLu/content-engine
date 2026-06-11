# Content Strategy

## Target audience

Experienced engineers who evaluate tools for real use:

- senior software engineers
- fintech / quant developers
- AI engineers
- cloud / data-platform engineers
- developer-productivity / tooling engineers

They are skeptical, time-poor, and allergic to marketing. They want to know
*what a thing does, whether it's worth their attention, and where it breaks.*

## Content style

Practical engineering commentary — the voice of a staff engineer who just read
the README and is telling a colleague whether it's worth a look.

- Specific and grounded: every claim traceable to the repo's README/metadata.
- Skeptical where warranted: name limitations, risks, and unknowns.
- Opinionated: end with one clear takeaway, not a shrug.
- Honest about thin sources: if the README doesn't support a claim, omit it or
  say the source is thin — do not pad.

## Post structure (long form, 450–900 words)

Enforced loosely by the writer prompt; structure verified by the quality gate
(≥2 headings, repo grounded, length floor).

1. **What it does** — the concrete problem it solves and its core primitives.
2. **Why engineers are paying attention** — the real signal behind the stars.
3. **How it might be used in production** — a pragmatic adoption path.
4. **Architecture / implementation insight** — one specific observation *if the
   source supports it*.
5. **Limitations, risks, tradeoffs** — maintenance risk, API churn, scale unknowns.
6. **Takeaway** — one clear, defensible opinion.

## Microblog template (Bluesky / Mastodon / Threads, ≤300–500 chars)

```
<one-sentence grounded hook about what <repo> does and why it matters>
<repo url>
```

Composed from the draft `summary`; the URL is appended as a clickable facet
(Bluesky) or plain link, truncated to the platform limit.

## Reviewer rules

The reviewer agent returns structured JSON and checks:

- **factual accuracy** vs the repo fact sheet / README
- **unsupported claims** (anything not in the source material)
- **exaggeration / overhype**
- **technical correctness** of explanations
- **clarity, structure, tone**
- **"does this read as generic AI output?"**
- **usefulness** to the target audience

Policy (in `config.toml [review]`, applied by `ReviewerAgent.is_approved`):

- `min_overall_score = 7.0` (0–10 scale)
- any **high-severity** issue blocks publishing
- `recommended_action = "reject"` blocks publishing
- up to `max_revisions` writer↔reviewer rounds before giving up

## Banned phrases (hard block in live mode)

Configured in `config.toml [quality].banned_phrases`. The deterministic gate
blocks publishing if any appear:

> game changer · revolutionary · this changes everything · unlocks the future ·
> AI is transforming everything · groundbreaking · next big thing ·
> supercharge your workflow · 10x productivity · in today's fast-paced world ·
> delve into · dive in · in conclusion · as an AI · seamlessly integrate · …

(Extend the list freely — it's just config.)

## Quality bar (deterministic gate, `quality.py`)

Blocking: missing title/body/summary, banned phrase over limit, fewer than
`min_headings` headings, repo name absent from body, placeholder text
(`TODO:`, `lorem ipsum`, `[insert`, leftover `owner/repo`), body below the word
floor. Warnings (non-blocking): body over target length, summary over the
microblog budget, no tags.

To publish, content must pass **both** the reviewer policy and this gate.

## Examples

### Good (grounded, specific, opinionated)

> ## What it does
> `acme/widget` is a Rust CLI that batches filesystem watches into a single
> inotify subscription. The README documents a debounce window and a JSON event
> stream, which is the interesting part: it lets you pipe file events into
> existing tooling without a daemon.
>
> ## Limitations
> The README shows Linux examples only; there's no mention of macOS `fsevents`
> or Windows `ReadDirectoryChangesW`, so cross-platform use is unproven. At ~1.2k
> stars and a three-month history, treat the API as unstable.
>
> ## Takeaway
> Worth a look if you're on Linux and already shelling out to a file watcher —
> but pin the version and test the debounce semantics before trusting it in CI.

Why it's good: every claim maps to the README; it states what's *not* covered;
it gives a concrete, qualified recommendation.

### Bad (hype, ungrounded, generic)

> 🚀 `acme/widget` is a **revolutionary game changer** that will **supercharge
> your workflow** and unlock 10x productivity! In today's fast-paced world,
> this groundbreaking tool seamlessly integrates with everything and is the next
> big thing every developer needs. It's blazingly fast and infinitely scalable!

Why it's bad: banned phrases (multiple → hard block), zero grounding,
unverifiable performance/scale claims, emoji-marketing voice, no limitations, no
real takeaway. The reviewer flags it (unsupported + overhype + AI-sounding) and
the deterministic gate blocks it on banned phrases.
