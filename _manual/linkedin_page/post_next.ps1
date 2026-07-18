<#
  post_next.ps1  —  Publish the next queued article to the Agent Palisade LinkedIn
  COMPANY PAGE via the Kimi WebBridge daemon (trusted key_type -> dev.to link unfurls
  with the cover). Deterministic, self-contained, safe to run unattended on a schedule.

  Preconditions (the schedule only works when these hold):
    - Kimi WebBridge daemon up at 127.0.0.1:10086 AND the browser extension connected
    - That Chrome is logged into LinkedIn as an ADMIN of the Agent Palisade page

  Behavior:
    - Health-checks the bridge. If down, logs and exits 3 (no harm, retries next run).
    - Navigates to the page admin composer; ABORTS if not posting as "Agent Palisade".
    - Posts at most -Max articles (default 1), oldest unposted first, updates posted.json.
    - Every run appends to post_log.txt and drops a screenshot in shots/.

  Exit codes: 0 ok/nothing-to-do, 2 abort (wrong identity / redirect), 3 bridge down.

  Usage:
    powershell -ExecutionPolicy Bypass -File post_next.ps1            # post 1
    powershell -ExecutionPolicy Bypass -File post_next.ps1 -Max 2     # post up to 2
    powershell -ExecutionPolicy Bypass -File post_next.ps1 -DryRun    # compose, do not click Post
#>
param([int]$Max = 1, [switch]$DryRun)

$ErrorActionPreference = "Stop"
$Root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Base  = "http://127.0.0.1:10086/command"
$Sess  = "li-post"
$OrgId = "130364965"
$AdminUrl = "https://www.linkedin.com/company/$OrgId/admin/page-posts/published/"
$ShotDir = Join-Path $Root "shots"; if (-not (Test-Path $ShotDir)) { New-Item -ItemType Directory -Force $ShotDir | Out-Null }
$LogFile = Join-Path $Root "post_log.txt"

function Log($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $LogFile -Value $line -Encoding utf8
  Write-Host $line
}
function Cmd($action, $a) {
  Invoke-RestMethod -Uri $Base -Method Post -Body (@{action=$action; args=$a; session=$Sess} | ConvertTo-Json -Depth 8) -ContentType "application/json" -TimeoutSec 90
}
function Ev($code) { (Cmd "evaluate" @{code=$code}).data.value }
function Shot($name) {
  try { $ss = Cmd "screenshot" @{}; $b64 = $ss.data.image; if (-not $b64) { $b64 = $ss.data.data }
    [IO.File]::WriteAllBytes((Join-Path $ShotDir $name), [Convert]::FromBase64String(($b64 -replace '^data:image/\w+;base64,',''))) } catch {}
}

# ---- 1. health check ----
# A session with no bound tab 502s on the first evaluate even though the daemon
# and extension are both fine. Bind a tab with a navigate, then re-check, so a
# stale session is not misreported as a dead bridge.
$healthy = $false
try { $h = Cmd "evaluate" @{code="1"}; $healthy = [bool]$h.ok } catch { $healthy = $false }
if (-not $healthy) {
  try {
    Cmd "navigate" @{url=$AdminUrl; newTab=$false; group_title="AP LinkedIn"} | Out-Null
    Start-Sleep -Seconds 4
    $h = Cmd "evaluate" @{code="1"}; $healthy = [bool]$h.ok
  } catch { $healthy = $false }
}
if (-not $healthy) { Log "BRIDGE DOWN (daemon or extension not reachable) - skipping this run"; exit 3 }

# ---- 2. load queue + state ----
$queue  = Get-Content (Join-Path $Root "queue.json")  -Raw | ConvertFrom-Json
$state  = Get-Content (Join-Path $Root "posted.json") -Raw | ConvertFrom-Json
$done   = @($state.posted)
$todo   = @($queue | Where-Object { $done -notcontains $_.prefix } | Sort-Object prefix)
if ($todo.Count -eq 0) { Log "queue empty - all $($queue.Count) articles posted"; exit 0 }

# ---- 3. navigate to admin composer, verify identity ----
Cmd "navigate" @{url=$AdminUrl; newTab=$false; group_title="Agent Palisade LinkedIn"} | Out-Null
Start-Sleep -Seconds 6
$cur = Ev "location.href"
if ($cur -match "unavailable") { Log "ABORT: redirected to $cur (not an admin of the page / not logged in)"; Shot "abort_identity.png"; exit 2 }

$posted = 0
foreach ($p in ($todo | Select-Object -First $Max)) {
  Log "composing $($p.prefix) ($($p.title))"
  # open composer
  $o = Ev "(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^start a post$/i.test((x.innerText||'').trim()));if(!b)return'no_btn';b.click();return'clicked';})()"
  Start-Sleep -Seconds 5
  $ed = Ev "(()=>{const e=document.querySelector('.ql-editor[contenteditable=true]');return e?'editor':'no_editor';})()"
  if ($ed -ne "editor") { Log "  composer did not open ($o/$ed) - stopping"; Shot "no_composer_$($p.prefix).png"; break }

  # SAFETY: confirm the modal is posting AS the page, not a person
  $ident = Ev "(()=>{const d=document.querySelector('div[role=dialog]');return d?(d.innerText||'').slice(0,60):'';})()"
  if ($ident -notmatch "Agent Palisade") { Log "  ABORT: composer identity not Agent Palisade ('$ident')"; Shot "wrong_identity_$($p.prefix).png"; exit 2 }

  # clear + focus at start
  Ev "(()=>{const e=document.querySelector('.ql-editor[contenteditable=true]');e.innerHTML='';e.focus();const r=document.createRange();r.selectNodeContents(e);r.collapse(true);const s=getSelection();s.removeAllRanges();s.addRange(r);return'ready';})()" | Out-Null

  # trusted typing, ONE inline key_type: "commentary <url> ". The trailing space commits the
  # URL so LinkedIn unfurls the dev.to card (with the cover). Keep the URL inline (a space,
  # not a newline, before it): a standalone URL on its own line gets promoted ABOVE the
  # commentary by LinkedIn. Synthetic fill/execCommand does not unfurl at all; trusted
  # key_type is required.
  Cmd "key_type" @{text=($p.text + " " + $p.url + " ")} | Out-Null

  # poll for the link card
  $chk = "(()=>{const d=document.querySelector('div[role=dialog]');const n=d?[...d.querySelectorAll('a[href*=`"dev.to`"]')].length:0;return n;})()"
  $carded = $false
  for ($i=0; $i -lt 8; $i++) { Start-Sleep -Seconds 3; if ([int](Ev $chk) -ge 1) { $carded = $true; break } }
  Shot "compose_$($p.prefix).png"
  if (-not $carded) { Log "  WARN: link card not detected - stopping before posting $($p.prefix)"; break }

  # GUARD: never publish an inverted post (URL promoted above the commentary).
  $head = Ev "(()=>{const e=document.querySelector('.ql-editor[contenteditable=true]');return (e.innerText||'').slice(0,20);})()"
  $expect = $p.text.Substring(0, [Math]::Min(12, $p.text.Length))
  if ($head -notlike "$expect*") { Log "  WARN: order guard failed (head='$head') - not posting $($p.prefix)"; break }

  if ($DryRun) { Log "  DRYRUN: $($p.prefix) composed (not posted)"; break }

  # publish
  $pr = Ev "(()=>{const d=document.querySelector('div[role=dialog]');const b=[...(d||document).querySelectorAll('button')].find(x=>/^post$/i.test((x.innerText||'').trim())&&!x.disabled);if(!b)return'no_post';b.click();return'posted';})()"
  Start-Sleep -Seconds 6
  if ($pr -ne "posted") { Log "  FAILED to click Post ($pr)"; Shot "post_fail_$($p.prefix).png"; break }
  # dismiss whatever post-publish dialog appears: Premium upsell ("No thanks") or the
  # "Share this post on your profile" prompt ("Not now"). NEVER click Continue/Redeem
  # (Continue reshares to Yan's PERSONAL profile; Redeem starts a paid trial).
  Ev "(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^(no thanks|not now|dismiss)$/i.test((x.innerText||'').trim()));if(b){b.click();return'dismissed';}const x=document.querySelector('button[aria-label*=Dismiss],button[aria-label*=Close]');if(x){x.click();return'closed';}return'none';})()" | Out-Null
  Start-Sleep -Seconds 2
  Shot "posted_$($p.prefix).png"

  # record success
  $done += $p.prefix
  $state.posted = $done
  $entry = [pscustomobject]@{ prefix=$p.prefix; at=(Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz") }
  $state.history = @($state.history) + $entry
  $state | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $Root "posted.json") -Encoding utf8
  Log "  POSTED $($p.prefix) ($($p.url))"
  $posted++
  Start-Sleep -Seconds 4
}

$remaining = @($queue | Where-Object { $done -notcontains $_.prefix }).Count
Log "run complete: posted $posted this run, $remaining remaining"
exit 0
