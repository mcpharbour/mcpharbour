$ErrorActionPreference = "Stop"

$TaskName = "MCP Harbour Daemon"

function Info($msg) { Write-Host "[+] $msg" -ForegroundColor Green }

# ── 1. Stop and remove scheduled task ──────────────────────────────

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Info "Removed scheduled task."
}

# ── 2. Remove binaries ────────────────────────────────────────────

$installDir = Join-Path $env:LOCALAPPDATA "mcp-harbour\bin"
if (Test-Path $installDir) {
    Remove-Item "$installDir\harbour.exe" -Force -ErrorAction SilentlyContinue
    Remove-Item "$installDir\harbour-bridge.exe" -Force -ErrorAction SilentlyContinue
    Info "Removed binaries."
}

Info "Uninstall complete."
Info "Config files remain at $env:APPDATA\mcp-harbour — delete manually if desired."
