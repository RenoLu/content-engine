# LinkedIn personal-profile posting lane

Date: 2026-07-18
Status: approved, not yet implemented

## Problem

The Agent Palisade company page has been drip-posting queued articles since
2026-07-15 via `_manual/linkedin_page/`. As of today 8 of 12 are posted and the
page has 0 followers, so the drip reaches nobody. Yan's personal profile has an
actual network and has posted none of these articles.

We want the same articles going out on the personal profile, on a schedule, at a
pace slow enough not to trip LinkedIn velocity heuristics.

## Decisions

Three decisions were settled before design:

1. Copy is identical on both lanes. A reshare or separately-authored variant was
   considered and rejected in favour of the simplest build.
2. The personal lane backfills all 12 articles from 0001, rather than joining the
   page lane at 0009.
3. The page lane does not accelerate to close its one-day deficit.

Decision 2 largely defuses the risk in decision 1. With the profile starting at
0001 while the page sits at 0009, the two lanes publish different articles on any
given day. They only converge on the same article after the page queue is
finished, at which point the page lane is idle.

## Architecture

Two independent lanes over one shared queue:

    linkedin_page/queue.json            12 articles, single source of truth
      linkedin_page/posted.json         page lane state
      linkedin_personal/posted_personal.json   profile lane state

`queue.json` stays owned by `linkedin_page/` and is read-only to the personal
lane. Separate state files mean neither lane can corrupt the other's progress and
either can be paused without touching the other.

## The new poster

`_manual/linkedin_personal/post_next_personal.ps1` is structurally a clone of
`post_next.ps1`: same Kimi WebBridge health check, same order guard, same
per-run screenshot into `shots/`, same `-Max` and `-DryRun` parameters, same
append-only `post_log.txt`. Three things differ.

**Composer target.** Navigates to the feed composer at `/feed/` rather than the
page admin composer.

**Inverted actor guard.** `post_next.ps1` carries a hard rule never to click
"Continue", because in the page composer that reshares to the personal profile.
The personal poster needs the opposite assertion: confirm the composer actor
resolves to Yan Lu and abort if it resolves to Agent Palisade. Posting under the
wrong identity is not silently recoverable, so the script aborts rather than
guessing.

**State.** Reads `../linkedin_page/queue.json`, writes only `posted_personal.json`.

No image handling. Posts carry the dev.to URL and let LinkedIn unfurl the cover,
which is what the page lane already does. This sidesteps the known limitation
that image attach cannot be automated through Kimi.

## Schedule

New scheduled task `YanLu-LinkedIn-Personal-Daily`, daily at 10:00 ET, one
article per run.

The existing `AgentPalisade-LinkedIn-Daily` task is unchanged at 17:00 ET.

Seven hours apart, different articles, no bursts. The personal lane finishes in
12 days and the page lane in 4.

## Error handling

Bridge down: log and skip the run, same as the page lane. A missed day is
recoverable and a double-post is not, so every failure mode exits without
posting.

Actor guard fails: abort, screenshot, log, do not retry.

Post button absent or disabled: abort, screenshot, log, do not retry.

State is written only after a post is confirmed sent, so an interrupted run
leaves the article unposted rather than falsely marked done.

## Verification

Before the lane goes live:

1. Dry run on 0001. Confirms the profile is authenticated in the Kimi-driven
   browser and that the composer is reachable.
2. Inspect the composed screenshot and confirm the actor guard resolved to Yan
   Lu, not Agent Palisade.
3. One supervised live post of 0001.
4. Enable the scheduled task only after that post is confirmed on the profile.

## Out of scope

Engagement (likes, comments) on the personal profile. That is already handled
separately by `outreach_linkedin_kimi_run.py` and is not touched here.

Per-article copy variants for the personal profile. Revisit if identical copy
turns out to suppress reach.

Growing the company page's follower count. Noted as the reason the page lane is
low value right now, but not addressed by this work.
