<#
  codex_run.ps1 — daily Codex-driven trigger for the LinkedIn page poster.
  Invoked by the Windows Scheduled Task "AgentPalisade-LinkedIn-Daily". Runs
  `codex exec` headless; Codex follows AGENTS.md (runs post_next.ps1, verifies the
  log, reports one line). The deterministic script does the real posting.

  Manual test:  powershell -ExecutionPolicy Bypass -File codex_run.ps1
#>
$ErrorActionPreference = "Continue"
$Dir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log  = Join-Path $Dir "codex_run_log.txt"
$Last = Join-Path $Dir "codex_last_message.txt"

$prompt = "Publish the next queued LinkedIn article to the Agent Palisade company page. Follow AGENTS.md in this directory exactly."

Add-Content -Path $Log -Value ("`n==== {0} : codex exec start ====" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -Encoding utf8

# -m gpt-5.5           : gpt-5.6-sol (CLI default) errors on 0.133.0
# -c mcp_servers={}    : skip MCP servers (Robinhood OAuth noise / slowness)
# --dangerously-bypass : unattended; no approval prompts (externally trusted machine)
# -C $Dir              : working root so AGENTS.md + the scripts resolve
# piping $null closes stdin so codex never blocks waiting on a tty
$codexArgs = @(
  'exec', $prompt,
  '-m', 'gpt-5.5',
  '-c', 'mcp_servers={}',
  '--dangerously-bypass-approvals-and-sandbox',
  '--skip-git-repo-check',
  '-C', $Dir,
  '-o', $Last
)

$null | & codex @codexArgs *>&1 | Tee-Object -FilePath $Log -Append
$code = $LASTEXITCODE
Add-Content -Path $Log -Value ("==== {0} : codex exec end (exit {1}) ====" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $code) -Encoding utf8
exit $code
