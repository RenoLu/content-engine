# Agent Palisade — unattended daily content run (local agent-as-model).
#
# Deterministic steps run here in PowerShell; the one creative step (authoring
# the article) is delegated to a headless `claude` session that writes
# _manual/article.json per _manual/RUNBOOK.md.
#
# SECURITY MODEL — the article is grounded in an UNTRUSTED GitHub README (anyone
# can publish a trending repo), so the authoring agent is treated as hostile-input
# processing and runs least-privilege:
#   * --tools Read Write Edit only (NO Bash / network / env access) and no
#     permission bypass, so a prompt-injected README cannot run commands,
#     reach the internet, or read process env vars (where secrets would live).
#   * --disallowedTools denies reading .env / settings.json / *.sqlite3.
#   * publisher tokens are loaded into THIS launcher's env only AFTER authoring,
#     and a secret-scan refuses to publish any article containing a local secret.
# The pipeline's quality + engagement gates are a further net: a weak or
# ungrounded article is rejected and nothing is posted.
#
#   .\_manual\daily_run.ps1            # live (publishes)
#   .\_manual\daily_run.ps1 -DryRun    # simulate, post nothing
#
param([switch]$DryRun)

$ErrorActionPreference = "Continue"
$root = "C:\Coding Space\content-engine"
$man  = Join-Path $root "_manual"
Set-Location $root

$logdir = Join-Path $man "logs"
New-Item -ItemType Directory -Force -Path $logdir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$log   = Join-Path $logdir "run-$stamp.log"
function Log($m) { "$((Get-Date).ToString('HH:mm:ss')) $m" | Tee-Object -FilePath $log -Append }

function Show-Toast($title, $body) {
  try {
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $n = $t.GetElementsByTagName("text")
    $n.Item(0).AppendChild($t.CreateTextNode($title)) | Out-Null
    $n.Item(1).AppendChild($t.CreateTextNode($body))  | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($t)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Agent Palisade").Show($toast)
  } catch { }
}

$mode = if ($DryRun) { "dry_run" } else { "live" }
$py   = Join-Path $root ".venv\Scripts\python.exe"
$settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
Log "=== daily content run (mode=$mode) ==="

# --- non-secret pipeline config for discovery. NO publisher tokens are set here:
#     the authoring agent must not have secrets reachable in its environment. ---
$env:PYTHONPATH        = "src"
$env:REQUIRE_AI_TOPIC  = "true"
$env:MAX_REPO_AGE_DAYS = "60"
$env:AI_PROVIDER       = "mock"   # discovery only; the agent is the real model

# --- 1. discover today's repo (AI + created <= 60 days) ---
Log "discovering a fresh AI repo (<=60 days)..."
& $py (Join-Path $man "discover.py") *>> $log
$disc = $LASTEXITCODE
if ($disc -eq 2) {
  Log "NO_CANDIDATE -- nothing to write today. Done."
  "no_candidate  $stamp" | Set-Content (Join-Path $man "LATEST_RUN.txt")
  exit 0
}
if ($disc -ne 0) { Log "discover.py failed (exit $disc). Aborting."; exit 1 }

# --- 2. extract facts + README for the writer ---
& $py (Join-Path $man "_extract.py") *>> $log

# --- 3. least-privilege headless agent authors article.json ---
$artPath = Join-Path $man "article.json"
Remove-Item $artPath -ErrorAction SilentlyContinue
Log "invoking headless claude (Read/Write/Edit only, no bypass) to author article.json..."
$deny = @(
  "Read(**/.env)", "Read(**/.env.*)", "Read(**/settings.json)",
  "Read(**/settings.local.json)", "Read(**/*.sqlite3)", "Read(**/credentials*)"
)
$prompt = Get-Content (Join-Path $man "RUNBOOK.md") -Raw
$prompt | & claude -p --tools Read Write Edit --permission-mode acceptEdits --disallowedTools $deny *>> $log

if (-not (Test-Path $artPath)) {
  Log "agent produced no article.json. Aborting (nothing published)."
  "no_article  $stamp" | Set-Content (Join-Path $man "LATEST_RUN.txt")
  exit 1
}

# --- 4. SECRET SCAN: never publish an article that contains a local secret value
#        (defends against prompt-injection exfil through the published post). ---
$cfg = $null
try { $cfg = Get-Content $settingsPath -Raw | ConvertFrom-Json } catch { Log "WARN: could not read settings.json for scan: $_" }
if ($cfg) {
  $art = Get-Content $artPath -Raw
  $leak = $false
  foreach ($p in $cfg.env.PSObject.Properties) {
    if ($p.Name -notmatch '^_' -and $p.Value -is [string] -and $p.Value.Length -ge 12 -and $art.Contains($p.Value)) {
      Log "SECURITY: article.json contains the value of $($p.Name) -- refusing to publish."
      $leak = $true
    }
  }
  if ($leak) {
    "security_abort  $stamp  (possible secret in generated article)" | Set-Content (Join-Path $man "LATEST_RUN.txt")
    Show-Toast "Content run BLOCKED" "Possible secret in the generated article -- nothing published."
    exit 1
  }
}

# --- 5. load publisher tokens (now, for the publish step only) + run pipeline ---
if ($cfg) {
  foreach ($p in $cfg.env.PSObject.Properties) {
    if ($p.Name -notmatch '^_' -and $p.Value -is [string]) { Set-Item -Path "env:$($p.Name)" -Value $p.Value }
  }
}
$env:ENABLED_PUBLISHERS = "dryrun,devto,bluesky,mastodon"
$env:DEVTO_PUBLISHED    = "true"
$env:PUBLISH_MODE       = $mode
Log "running pipeline (mode=$mode)..."
$summaryFile = Join-Path $man "last_summary.json"
& $py (Join-Path $man "publish_manual.py") 1> $summaryFile 2>> $log

# --- 6. record + notify ---
try {
  $sum = Get-Content $summaryFile -Raw | ConvertFrom-Json
  $status = $sum.status
  $repo = $sum.repo
  $links = @()
  foreach ($r in $sum.publish_results) { if ($r.status -eq 'published' -and $r.url) { $links += "$($r.publisher): $($r.url)" } }
  $line = "$stamp  status=$status  repo=$repo  attention=$($sum.attention_score) voice=$($sum.voice_score)"
  Log $line
  (@($line) + $links) | Set-Content (Join-Path $man "LATEST_RUN.txt")
  if ($status -eq 'published') {
    Add-Content (Join-Path $man "published_log.md") ("- $stamp  [$repo]  " + ($links -join "  |  "))
    Show-Toast "Content published: $repo" (($links | ForEach-Object { ($_ -split ': ')[0] }) -join ', ')
  } else {
    Show-Toast "Content run: $status" "repo=$repo (nothing posted)"
  }
} catch { Log "WARN: could not parse summary: $_" }

Log "=== done (mode=$mode) ==="
