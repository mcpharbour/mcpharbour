$ErrorActionPreference = "Stop"

$Repo = "mcpharbour/mcpharbour"
$TaskName = "MCP Harbour Daemon"
$Platform = "windows-x64"

function Info($msg)  { Write-Host "[+] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "[x] $msg" -ForegroundColor Red; exit 1 }

# ── 1. Download latest release ─────────────────────────────────────

Info "Fetching latest release..."
$release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
$tag = $release.tag_name
$asset = $release.assets | Where-Object { $_.name -eq "mcp-harbour-$Platform.zip" }

if (-not $asset) { Fail "No release found for $Platform" }

$tmpDir = Join-Path $env:TEMP "mcp-harbour-install"
if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
New-Item -ItemType Directory -Path $tmpDir | Out-Null

Info "Downloading $tag..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile "$tmpDir\release.zip"
Expand-Archive -Path "$tmpDir\release.zip" -DestinationPath $tmpDir -Force

# ── 2. Install binaries ───────────────────────────────────────────

$installDir = Join-Path $env:LOCALAPPDATA "mcp-harbour\bin"
if (-not (Test-Path $installDir)) { New-Item -ItemType Directory -Path $installDir | Out-Null }

Copy-Item "$tmpDir\harbour.exe" "$installDir\" -Force
Copy-Item "$tmpDir\harbour-bridge.exe" "$installDir\" -Force

# Add to user PATH if not already there
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$installDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$installDir;$userPath", "User")
    $env:Path = "$installDir;$env:Path"
    Info "Added $installDir to PATH"
}

$HarbourBin = Join-Path $installDir "harbour.exe"
Info "Installed harbour at $HarbourBin"

# ── 3. Register scheduled task ─────────────────────────────────────

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Warn "Removing existing scheduled task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$logDir = Join-Path $env:APPDATA "mcp-harbour"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$action = New-ScheduledTaskAction `
    -Execute $HarbourBin `
    -Argument "serve" `
    -WorkingDirectory $logDir

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Seconds 10) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "MCP Harbour daemon" | Out-Null

Start-ScheduledTask -TaskName $TaskName

Info "Registered scheduled task: $TaskName"
Info "Daemon started on 127.0.0.1:4767"

# ── 4. Cleanup ─────────────────────────────────────────────────────

Remove-Item $tmpDir -Recurse -Force

Write-Host ""
Info "Manage with:"
Write-Host "  harbour status"
Write-Host "  harbour stop"
Write-Host "  harbour start"

Write-Host ""
Info "Installation complete."
