<#
  codex_run.ps1 — daily Codex-driven trigger for the LinkedIn page poster.
  Invoked by the Windows Scheduled Task "AgentPalisade-LinkedIn-Daily". Runs
  `codex exec` headless; Codex follows AGENTS.md (runs post_next.ps1, verifies the
  log, reports one line). The deterministic script does the real posting.

  Manual test:  powershell -ExecutionPolicy Bypass -File codex_run.ps1
#>
$ErrorActionPreference = "Continue"
$Dir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log  = Join-Path $Dir "codex_run_log.txt"     # our own markers, never shared
$Out  = Join-Path $Dir "codex_stdout.txt"      # codex transcript
$Err  = Join-Path $Dir "codex_stderr.txt"      # codex chatter + warnings
$Last = Join-Path $Dir "codex_last_message.txt"

$prompt = "Publish the next queued LinkedIn article to the Agent Palisade company page. Follow AGENTS.md in this directory exactly."

# codex's `notify` hook (see ~/.codex/config.toml) outlives the run and inherits
# the redirected handles, so a file cmd wrote to can still be locked afterwards.
# Our log is a separate file for that reason; retry anyway so a stray lock can
# never take down the run itself.
function Write-Log($msg) {
  foreach ($i in 1..5) {
    try { Add-Content -Path $Log -Value $msg -Encoding utf8 -ErrorAction Stop; return }
    catch { Start-Sleep -Milliseconds 200 }
  }
  Write-Host $msg
}

Write-Log ("`n==== {0} : codex exec start ====" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))

# --dangerously-bypass : unattended; no approval prompts (externally trusted machine)
# -C $Dir              : working root so AGENTS.md + the scripts resolve
#
# Two workarounds were dropped here on 2026-07-18 after upgrading the CLI from
# 0.133.0 to 0.144.6, both of which had outlived their reason:
#   -m gpt-5.5        : pinned only because gpt-5.6-sol (the default) errored on
#                       0.133.0. It works now, so run the default.
#   -c mcp_servers={} : claimed to skip MCP servers, but never did. Config tables
#                       merge rather than replace, so the servers loaded anyway.

# Count what is actually posted before and after. Codex exits 0 whenever it
# successfully REPORTS an outcome, including "skipped: bridge down", so its exit
# code says nothing about whether an article went out. On 07-16 and 07-17 the
# bridge was down and the task still recorded a clean success. Check the state
# file instead of trusting the agent's self-report.
# Both counters wrap the parenthesised result, NOT the raw pipeline. PowerShell
# 5.1's ConvertFrom-Json emits a JSON array as ONE object, so
# `@(Get-Content x | ConvertFrom-Json).Count` is 1 no matter how long the array
# is. That made the queue look empty and turned the no-op guard below into the
# success report it was written to prevent.
function Get-PostedCount {
  try { @((Get-Content (Join-Path $Dir "posted.json") -Raw | ConvertFrom-Json).posted).Count }
  catch { -1 }
}
function Get-QueuedCount {
  try { @((Get-Content (Join-Path $Dir "queue.json") -Raw | ConvertFrom-Json)).Count }
  catch { 0 }
}
$before = Get-PostedCount

# Run through cmd so PowerShell never touches the child's streams.
#
# npm's codex.ps1 shim does `$input | & node ...`, so node's stderr comes back as
# a PowerShell error record: routine chatter ("Reading additional input from
# stdin...", the models-cache warning) got rendered as a red NativeCommandError
# with a stack-looking header, which buried the one line that matters. Letting
# cmd do the redirection keeps stderr as plain text, and <NUL closes stdin so
# codex never blocks waiting on a tty. $LASTEXITCODE still comes back intact.
$cmdLine = 'codex.cmd exec "{0}" --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C "{1}" -o "{2}" 1>>"{3}" 2>>"{4}" <NUL' `
  -f $prompt, $Dir, $Last, $Out, $Err
& cmd /c $cmdLine
$code = $LASTEXITCODE
Write-Log ("==== {0} : codex exec end (exit {1}) ====" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $code)

$after = Get-PostedCount
$fail = $false
if ($code -eq 0 -and $after -le $before) {
  $queued = Get-QueuedCount
  if ($queued -gt 0 -and $after -ge $queued) {
    Write-Log "queue empty - nothing left to post"
  } else {
    Write-Log "NOTHING POSTED (still $after of $queued) - failing so the run is not recorded as a success"
    $fail = $true
  }
}

# Personal-profile lane, same queue and same daily cadence, run SEQUENTIALLY here rather
# than as its own scheduled task so the two lanes never drive a browser at the same time.
# It keeps its own state file (posted_personal.json) and its own duplicate guard, so a
# failure on either side leaves the other untouched.
#
# Since 2026-07-26 this lane runs article_cdp.py on the debug Chrome (:9222) instead of
# post_personal.ps1 on the Kimi bridge. Kimi can only fire synthetic events, and the
# article editor's Style dropdown ignores those, so every article it published came out
# as one flat wall of paragraphs with no headings and no cover image. Playwright-over-CDP
# clicks are real, so headings, bullet lists, code blocks and the cover upload all work.
# Python comes from the job-app venv, the only env here with Playwright installed.
$Py = "C:\Coding Space\job-app\job_app_agent\.venv\Scripts\python.exe"
Write-Log ("---- {0} : personal lane start ----" -f (Get-Date -Format "HH:mm:ss"))
if (Test-Path $Py) {
  & $Py (Join-Path $Dir "article_cdp.py") --next --max 1
  Write-Log ("---- {0} : personal lane end (exit {1}) ----" -f (Get-Date -Format "HH:mm:ss"), $LASTEXITCODE)
} else {
  Write-Log ("---- {0} : personal lane SKIPPED (no python at {1}) ----" -f (Get-Date -Format "HH:mm:ss"), $Py)
}

if ($fail) { exit 4 }
exit $code
