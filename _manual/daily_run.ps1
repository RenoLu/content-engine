# Agent Palisade — unattended daily content run (local agent-as-model).
#
# Deterministic steps run here in PowerShell; the one creative step (authoring
# the article) is delegated to a headless `claude` session that writes
# _manual/article.json per _manual/RUNBOOK.md. The pipeline's quality + engagement
# gates are the safety net: a weak article is rejected and nothing is posted.
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

$mode = if ($DryRun) { "dry_run" } else { "live" }
$py   = Join-Path $root ".venv\Scripts\python.exe"
Log "=== daily content run (mode=$mode) ==="

# --- load publisher tokens from settings.json + set pipeline env ---
try {
  $cfg = Get-Content "$env:USERPROFILE\.claude\settings.json" -Raw | ConvertFrom-Json
  foreach ($p in $cfg.env.PSObject.Properties) {
    if ($p.Name -notmatch '^_' -and $p.Value -is [string]) { Set-Item -Path "env:$($p.Name)" -Value $p.Value }
  }
} catch { Log "WARN: could not load settings.json env: $_" }
$env:PYTHONPATH        = "src"
$env:REQUIRE_AI_TOPIC  = "true"
$env:MAX_REPO_AGE_DAYS = "60"
$env:AI_PROVIDER       = "mock"   # discovery only; the agent is the real model
$env:ENABLED_PUBLISHERS = "dryrun,devto,bluesky,mastodon"
$env:DEVTO_PUBLISHED   = "true"

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

# --- 3. headless agent authors article.json ---
Remove-Item (Join-Path $man "article.json") -ErrorAction SilentlyContinue
Log "invoking headless claude to author article.json..."
$prompt = Get-Content (Join-Path $man "RUNBOOK.md") -Raw
$prompt | & claude -p --dangerously-skip-permissions *>> $log

$artPath = Join-Path $man "article.json"
if (-not (Test-Path $artPath)) {
  Log "agent produced no article.json. Aborting (nothing published)."
  "no_article  $stamp" | Set-Content (Join-Path $man "LATEST_RUN.txt")
  exit 1
}

# --- 4. run the real pipeline (gates + publishers) ---
$env:PUBLISH_MODE = $mode
Log "running pipeline (mode=$mode)..."
$summaryFile = Join-Path $man "last_summary.json"
& $py (Join-Path $man "publish_manual.py") 1> $summaryFile 2>> $log

# --- 5. record + notify ---
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
