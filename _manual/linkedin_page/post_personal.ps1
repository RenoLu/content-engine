<#
  DEPRECATED 2026-07-26 — use article_cdp.py (--next) instead.

  Everything this script published came out unformatted: the Kimi bridge can only fire
  synthetic events, and the article editor's Style dropdown renders its items only for a
  real pointer interaction, so section headings stayed the same size as body text and
  bullet lists were flattened into prose. Cover images were impossible here too (file
  attach is refused, -32000). article_cdp.py drives the debug Chrome over CDP, where the
  clicks are real, and does both. This file is kept only for the Kimi-side notes below.
  It no longer posts; running it prints a pointer and exits.

  post_personal.ps1 — publish the next queued piece to Yan Lu's PERSONAL LinkedIn profile
  as a native long-form ARTICLE (linkedin.com/article/new/), via the Kimi WebBridge daemon.

  NOT a feed post, and NOT a link to dev.to. LinkedIn demotes feed posts carrying an
  external link, and this profile exists for reach: a commentary-free link post got 76
  impressions while a native long-form post got 780. The whole article ships on-platform.
  The COMPANY page lane (post_next.ps1) is unaffected and still posts links with an
  unfurl card.

  The body comes from the source article in _manual/published/<prefix>-*.json, the same
  file the dev.to publisher used, so the two carry identical content.

  Editor quirks this depends on (the Article editor is NOT the feed composer, and unlike
  it is reachable by plain querySelector):
    - title is the page's single `textarea`. The [role=textbox] is the BODY; targeting it
      for the title silently leaves the title empty.
    - body is the single [contenteditable=true].
    - NEVER chunk key_type. Typing a 4k body in chunks drops characters at the seams and
      duplicates elsewhere. One unchunked key_type comes out byte-identical.
    - The URL slug is frozen at first publish, so the title must be set BEFORE publishing.
    - Image upload is impossible through Kimi (-32000), so articles go out text-only.

  Preconditions:
    - Kimi WebBridge daemon up at 127.0.0.1:10086 AND the browser extension connected
    - That Chrome is logged into LinkedIn as Yan Lu

  Exit codes: 0 ok/nothing-to-do, 2 abort (wrong identity / editor unreachable), 3 bridge down.

  Usage:
    powershell -ExecutionPolicy Bypass -File post_personal.ps1            # publish 1
    powershell -ExecutionPolicy Bypass -File post_personal.ps1 -Max 2     # publish up to 2
    powershell -ExecutionPolicy Bypass -File post_personal.ps1 -DryRun    # compose, do not publish
#>
param([int]$Max = 1, [switch]$DryRun)

Write-Host "post_personal.ps1 is deprecated (unformatted output, no cover image)."
Write-Host "Use: python article_cdp.py --next --max 1   (debug Chrome on :9222)"
exit 0

$ErrorActionPreference = "Stop"
$Root      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Base      = "http://127.0.0.1:10086/command"
$Sess      = "li-post-personal"
$FeedUrl   = "https://www.linkedin.com/feed/"
$NewArtUrl = "https://www.linkedin.com/article/new/"
$Vanity    = "renolu"
$ActivityUrl = "https://www.linkedin.com/in/$Vanity/recent-activity/all/"
$SrcDir    = Join-Path (Split-Path -Parent $Root) "published"
$StateFile = Join-Path $Root "posted_personal.json"
$ShotDir   = Join-Path $Root "shots"; if (-not (Test-Path $ShotDir)) { New-Item -ItemType Directory -Force $ShotDir | Out-Null }
$LogFile   = Join-Path $Root "post_personal_log.txt"

function Log($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $LogFile -Value $line -Encoding utf8
  Write-Host $line
}
function Cmd($action, $a) {
  Invoke-RestMethod -Uri $Base -Method Post -Body (@{action=$action; args=$a; session=$Sess} | ConvertTo-Json -Depth 8) -ContentType "application/json" -TimeoutSec 180
}
function Ev($code) { (Cmd "evaluate" @{code=$code}).data.value }
function Shot($name) {
  try { $ss = Cmd "screenshot" @{}; $b64 = $ss.data.image; if (-not $b64) { $b64 = $ss.data.data }
    [IO.File]::WriteAllBytes((Join-Path $ShotDir $name), [Convert]::FromBase64String(($b64 -replace '^data:image/\w+;base64,',''))) } catch {}
}
function JsStr($s) {
  # single-quoted JS literal
  ($s -replace '\\','\\' -replace "'","\'" -replace "`r","" -replace "`n",'\n')
}

# Markdown to the plain text an Article body should carry. LinkedIn's editor takes no
# markdown, so leftover syntax would publish literally as "## " and "**".
function ToArticleText($md) {
  $t = $md -replace "`r`n", "`n"
  $t = $t -replace '(?m)^\s*#{1,6}\s*', ''        # heading markers, keep the heading line
  $t = $t -replace '\*\*([^*]+)\*\*', '$1'        # bold
  $t = $t -replace '(?<!\*)\*([^*\n]+)\*(?!\*)', '$1'  # italic
  $t = $t -replace '`([^`]+)`', '$1'              # inline code
  $t = $t -replace '(?m)^\s*```.*$', ''           # fenced code markers
  $t = $t -replace '\[([^\]]+)\]\([^)]+\)', '$1'  # links keep the label only
  $t = $t -replace '(?m)^\s*[-*+]\s+', ''         # list bullets
  $t = $t -replace '(?m)^\s*>\s?', ''             # blockquotes
  $t = $t -replace '(?m)^\s*---+\s*$', ''         # horizontal rules
  # body_markdown separates paragraphs with a blank line; typed verbatim that yields two
  # paragraph breaks and huge visual gaps in the editor.
  $t = $t -replace '\n\s*\n+', "`n"
  return $t.Trim()
}

# ---- 1. health check ----
# A session with no bound tab 502s on the first evaluate even though the daemon and
# extension are both fine, so bind a tab and re-check before declaring the bridge down.
$healthy = $false
try { $h = Cmd "evaluate" @{code="1"}; $healthy = [bool]$h.ok } catch { $healthy = $false }
if (-not $healthy) {
  try {
    Cmd "navigate" @{url=$FeedUrl; newTab=$false; group_title="Yan LinkedIn"} | Out-Null
    Start-Sleep -Seconds 4
    $h = Cmd "evaluate" @{code="1"}; $healthy = [bool]$h.ok
  } catch { $healthy = $false }
}
if (-not $healthy) { Log "BRIDGE DOWN (daemon or extension not reachable) - skipping this run"; exit 3 }

# ---- 2. load queue + state ----
$queue = Get-Content (Join-Path $Root "queue.json") -Raw | ConvertFrom-Json
if (-not (Test-Path $StateFile)) {
  [pscustomobject]@{ posted=@(); history=@() } | ConvertTo-Json -Depth 6 | Set-Content -Path $StateFile -Encoding utf8
  Log "created posted_personal.json (fresh personal-lane state)"
}
$state = Get-Content $StateFile -Raw | ConvertFrom-Json
$done  = @($state.posted)
$todo  = @($queue | Where-Object { $done -notcontains $_.prefix } | Sort-Object prefix)
if ($todo.Count -eq 0) { Log "queue empty - all $($queue.Count) pieces published to the personal profile"; exit 0 }

# ---- 3. duplicate guard ----
# The profile's own activity feed is the second opinion on what is already up, because Yan
# publishes by hand too and a duplicate on a real profile is public and awkward to undo.
# The feed is VIRTUALIZED: rows leave the DOM once scrolled past, so text is accumulated
# into a Set while stepping down the page rather than read once at the bottom.
# Two signals: the article title, and the product name from the queue entry. A hand-written
# post carries no link card, so a slug or title match alone misses it (Omnigent and
# loop-engineering were both missed that way); the product name catches those.
# Deliberately biased toward over-matching: a false hit needs one manual publish, a miss
# puts a duplicate on the profile.
function NormText($s) { ($s -replace '[^a-zA-Z0-9]', '').ToLower() }

Cmd "navigate" @{url=$ActivityUrl; newTab=$false; group_title="Yan LinkedIn"} | Out-Null
Start-Sleep -Seconds 8
Ev "window.__acc=new Set(); window.__addAcc=()=>{document.body.innerText.split('\n').forEach(l=>{const s=l.trim(); if(s.length>20) window.__acc.add(s);}); return window.__acc.size;}; window.__addAcc();" | Out-Null
for ($s = 0; $s -lt 20; $s++) {
  Ev "window.scrollBy(0,700);1" | Out-Null
  Start-Sleep -Milliseconds 700
  Ev "window.__addAcc()" | Out-Null
}
$liveText = NormText (Ev "[...window.__acc].join(' ')")
if (-not $liveText) { Log "WARN: could not read the activity feed; duplicate guard is blind this run" }
else { Log "duplicate guard: read $($liveText.Length) chars from the profile" }

$posted = 0
foreach ($p in ($todo | Select-Object -First $Max)) {
  # locate the source article that dev.to published, so both carry the same text
  $src = Get-ChildItem (Join-Path $SrcDir ("{0}-*.json" -f $p.prefix)) -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $src) { Log "  SKIP $($p.prefix) - no source article in published/"; continue }
  $art = Get-Content $src.FullName -Raw | ConvertFrom-Json
  $title = "$($art.title)".Trim()
  $body  = ToArticleText "$($art.body_markdown)"
  if (-not $title -or $body.Length -lt 200) { Log "  SKIP $($p.prefix) - source article looks empty (title=$($title.Length), body=$($body.Length))"; continue }

  $nameKey  = NormText $p.title
  $titleKey = NormText $title
  if ($liveText -and (($titleKey.Length -ge 12 -and $liveText.Contains($titleKey)) -or ($nameKey.Length -ge 5 -and $liveText.Contains($nameKey)))) {
    Log "  SKIP $($p.prefix) ($($p.title)) - already on the profile, recording as published"
    $done += $p.prefix
    $state.posted = $done
    $state.history = @($state.history) + [pscustomobject]@{ prefix=$p.prefix; at=(Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"); note="found on profile; published by hand" }
    $state | ConvertTo-Json -Depth 6 | Set-Content -Path $StateFile -Encoding utf8
    continue
  }

  Log "composing article $($p.prefix): $title  [$($body.Length) chars]"

  Cmd "navigate" @{url=$NewArtUrl; newTab=$false; group_title="Yan LinkedIn"} | Out-Null
  Start-Sleep -Seconds 10

  # The editor here is plain-DOM reachable, unlike the feed composer.
  $ready = Ev "(()=>{const t=document.querySelectorAll('textarea');const b=document.querySelectorAll('[contenteditable=true]');return JSON.stringify({t:t.length,b:b.length,url:location.href});})()"
  Log "  editor probe: $ready"
  $probe = $ready | ConvertFrom-Json
  if ($probe.t -lt 1 -or $probe.b -lt 1) { Log "  ABORT: article editor not found"; Shot "article_no_editor_$($p.prefix).png"; exit 2 }
  if ($probe.url -notmatch "/article/") { Log "  ABORT: not on the article editor ($($probe.url))"; Shot "article_wrong_url_$($p.prefix).png"; exit 2 }

  # TITLE: focus the textarea, then type. Must be set before the first publish or the slug
  # is derived from the body's first sentence and frozen that way permanently.
  Ev "(()=>{const t=document.querySelector('textarea');t.focus();return 1;})()" | Out-Null
  Start-Sleep -Seconds 1
  Cmd "key_type" @{text=$title} | Out-Null
  Start-Sleep -Seconds 2

  # BODY: one unchunked key_type. Chunking corrupts the text at the seams.
  Ev "(()=>{const b=document.querySelector('[contenteditable=true]');b.focus();return 1;})()" | Out-Null
  Start-Sleep -Seconds 1
  Cmd "key_type" @{text=$body} | Out-Null
  Start-Sleep -Seconds 5

  # verify what actually landed before publishing anything
  $gotTitle = Ev "(()=>{const t=document.querySelector('textarea');return t? t.value : '';})()"
  $gotLen   = [int](Ev "(()=>{const b=document.querySelector('[contenteditable=true]');return b? (b.innerText||'').replace(/\s+/g,' ').trim().length : 0;})()")
  $wantLen  = ($body -replace '\s+',' ').Trim().Length
  Log "  title in editor: '$gotTitle'"
  Log "  body chars: $gotLen of $wantLen expected"
  Shot "article_compose_$($p.prefix).png"

  if ("$gotTitle".Trim() -ne $title) { Log "  WARN: title mismatch, not publishing $($p.prefix)"; break }
  # allow a small delta for whitespace normalization differences, but catch dropped chunks
  if ($gotLen -lt [int]($wantLen * 0.97)) { Log "  WARN: body is short by $($wantLen - $gotLen) chars (chunk loss?), not publishing $($p.prefix)"; break }

  if ($DryRun) { Log "  DRYRUN: $($p.prefix) composed in the editor (not published)"; break }

  # PUBLISH: "Next" opens the settings modal, then "Publish" commits. Refs renumber when a
  # modal opens, so each button is found by name at the moment it is needed.
  $n1 = Ev "(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^(next|publish)$/i.test((x.innerText||'').trim())&&!x.disabled);if(!b)return'none';b.click();return (b.innerText||'').trim();})()"
  Log "  clicked '$n1'"
  Start-Sleep -Seconds 5
  $n2 = Ev "(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^publish$/i.test((x.innerText||'').trim())&&!x.disabled);if(!b)return'none';b.click();return'publish';})()"
  Log "  publish click: $n2"
  Start-Sleep -Seconds 10
  Shot "article_published_$($p.prefix).png"

  # VERIFY on the profile. A clicked button is not a published article: the company lane
  # recorded two clean successes while the bridge was down, so confirm before recording.
  Cmd "navigate" @{url=$ActivityUrl; newTab=$false; group_title="Yan LinkedIn"} | Out-Null
  Start-Sleep -Seconds 9
  $seen = Ev "(()=>{return document.body.innerText.indexOf('$(JsStr $title)')>=0?'present':'absent';})()"
  if ($seen -ne "present") {
    Log "  WARN: clicked publish but '$title' is not on the profile - NOT recording as published"
    Shot "article_unverified_$($p.prefix).png"; break
  }

  $done += $p.prefix
  $state.posted = $done
  $state.history = @($state.history) + [pscustomobject]@{ prefix=$p.prefix; at=(Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"); kind="article"; title=$title }
  $state | ConvertTo-Json -Depth 6 | Set-Content -Path $StateFile -Encoding utf8
  Log "  PUBLISHED ARTICLE $($p.prefix): $title"
  $posted++
  Start-Sleep -Seconds 5
}

$remaining = @($queue | Where-Object { $done -notcontains $_.prefix }).Count
Log "run complete: published $posted this run, $remaining remaining"
exit 0
