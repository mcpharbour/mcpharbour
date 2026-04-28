$ErrorActionPreference = "Stop"

$ServiceName = "MCPHarbour"
$installDir = Join-Path $env:LOCALAPPDATA "mcp-harbour\bin"
$HarbourBin = Join-Path $installDir "harbour.exe"

function Info($msg) { Write-Host "[+] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

# ── 1. Stop and remove service ─────────────────────────────────────

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    $removeScript = @"
`$ErrorActionPreference = 'Stop'
Stop-Service -Name '$ServiceName' -Force -ErrorAction SilentlyContinue

# Wait for the service process to fully exit
`$timeout = 30
for (`$i = 0; `$i -lt `$timeout; `$i++) {
    `$proc = Get-Process -Name 'harbour','harbour-service' -ErrorAction SilentlyContinue
    if (-not `$proc) { break }
    Start-Sleep -Seconds 1
}

sc.exe delete '$ServiceName'
"@

    if ($isAdmin) {
        Invoke-Expression $removeScript
    } else {
        Info "Requesting administrator permission to remove the service..."
        $scriptPath = Join-Path $env:TEMP "mcp-harbour-remove-service.ps1"
        $removeScript | Out-File -FilePath $scriptPath -Encoding UTF8
        Start-Process powershell -Verb RunAs `
            -ArgumentList "-ExecutionPolicy Bypass -File `"$scriptPath`"" `
            -Wait
        Remove-Item $scriptPath -Force -ErrorAction SilentlyContinue
    }

    # Verify removal
    $check = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($check) {
        Warn "Service could not be removed. Try running as administrator."
    } else {
        Info "Removed service."
    }
}

# ── 2. Remove binaries ────────────────────────────────────────────

$svcDir = Join-Path $env:LOCALAPPDATA "mcp-harbour\svc"
if (Test-Path $svcDir) {
    Remove-Item $svcDir -Recurse -Force -ErrorAction SilentlyContinue
    Info "Removed service binary."
}

if (Test-Path $installDir) {
    # Wait for process to fully release files
    $timeout = 15
    for ($i = 0; $i -lt $timeout; $i++) {
        $proc = Get-Process -Name 'harbour','harbour-service' -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        Start-Sleep -Seconds 1
    }
    Remove-Item $installDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $installDir) {
        Warn "Some files still locked. They will be removed on next reboot."
    } else {
        Info "Removed binaries."
    }
}

# ── 3. Remove from PATH ───────────────────────────────────────────

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -and $userPath -like "*$installDir*") {
    $newPath = ($userPath -split ";" | Where-Object { $_ -ne $installDir }) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Info "Removed from PATH."
}

Info "Uninstall complete."
Info "Config files remain at $env:APPDATA\mcp-harbour - delete manually if desired."
