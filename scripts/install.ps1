$ErrorActionPreference = "Stop"

$Repo = "mcpharbour/mcpharbour"
$ServiceName = "MCPHarbour"
$Platform = "windows-x64"
$installDir = Join-Path $env:LOCALAPPDATA "mcp-harbour\bin"

function Info($msg)  { Write-Host "[+] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "[x] $msg" -ForegroundColor Red; exit 1 }

if ($HarbourBinaryPath) {
    # ── Local mode: copy from provided path ────────────────────────
    $sourceDir = Split-Path -Parent (Resolve-Path $HarbourBinaryPath).Path
    Info "Copying binaries from: $sourceDir"
} elseif ($env:MCP_HARBOUR_LOCAL_ARCHIVE) {
    # ── Local-archive mode (testing): extract a provided .zip ──────
    if (-not (Test-Path $env:MCP_HARBOUR_LOCAL_ARCHIVE)) {
        Fail "Local archive not found: $($env:MCP_HARBOUR_LOCAL_ARCHIVE)"
    }
    Info "Installing from local archive: $($env:MCP_HARBOUR_LOCAL_ARCHIVE)"
    $tmpDir = Join-Path $env:TEMP "mcp-harbour-install"
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    New-Item -ItemType Directory -Path $tmpDir | Out-Null
    Expand-Archive -Path $env:MCP_HARBOUR_LOCAL_ARCHIVE -DestinationPath $tmpDir -Force
    $sourceDir = $tmpDir
} else {
    # ── Download release (pinned or latest) ────────────────────────
    if ($env:MCP_HARBOUR_VERSION) {
        Info "Fetching release $env:MCP_HARBOUR_VERSION..."
        $release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/tags/$($env:MCP_HARBOUR_VERSION)"
    } else {
        Info "Fetching latest release..."
        $release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
    }
    $tag = $release.tag_name
    $asset = $release.assets | Where-Object { $_.name -eq "mcp-harbour-$Platform.zip" }

    if (-not $asset) { Fail "No release found for $Platform" }

    $tmpDir = Join-Path $env:TEMP "mcp-harbour-install"
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    New-Item -ItemType Directory -Path $tmpDir | Out-Null

    Info "Downloading $tag..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile "$tmpDir\release.zip"

    # ── Verify checksum ────────────────────────────────────────────
    $checksumUrl = "https://github.com/$Repo/releases/download/$tag/checksums.txt"
    $checksums = $null
    try {
        $checksums = (Invoke-WebRequest -Uri $checksumUrl -UseBasicParsing).Content
    } catch {
        Warn "checksums.txt not available for $tag; skipping verification"
    }
    if ($checksums) {
        $assetName = "mcp-harbour-$Platform.zip"
        $line = $checksums -split "`n" | Where-Object { $_ -match [regex]::Escape($assetName) } | Select-Object -First 1
        if (-not $line) { Fail "checksums.txt has no entry for $assetName" }
        $expected = (($line -split '\s+') | Where-Object { $_ })[0].ToLower()
        $actual = (Get-FileHash -Algorithm SHA256 "$tmpDir\release.zip").Hash.ToLower()
        if ($expected -ne $actual) { Fail "Checksum verification failed for $assetName" }
        Info "Checksum verified"
    }

    Expand-Archive -Path "$tmpDir\release.zip" -DestinationPath $tmpDir -Force

    $sourceDir = $tmpDir
}

# ── Install binaries to standard location ──────────────────────────

if (-not (Test-Path $installDir)) { New-Item -ItemType Directory -Path $installDir | Out-Null }

Copy-Item (Join-Path $sourceDir "harbour.exe") "$installDir\" -Force

$svcDir = Join-Path $env:LOCALAPPDATA "mcp-harbour\svc"
if (-not (Test-Path $svcDir)) { New-Item -ItemType Directory -Path $svcDir | Out-Null }
Copy-Item (Join-Path $sourceDir "harbour-service.exe") "$svcDir\" -Force

if ($tmpDir -and (Test-Path $tmpDir)) { Remove-Item $tmpDir -Recurse -Force }

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$installDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$installDir;$userPath", "User")
    $env:Path = "$installDir;$env:Path"
    Info "Added $installDir to PATH"
}

$HarbourBin = Join-Path $installDir "harbour.exe"
$ServiceBin = Join-Path $env:LOCALAPPDATA "mcp-harbour\svc\harbour-service.exe"
Info "Installed binaries to $installDir"

# ── Install and start Windows service ──────────────────────────────

if ($env:MCP_HARBOUR_NO_SERVICE) {
    Info "Skipping service registration (MCP_HARBOUR_NO_SERVICE set)."
    Info "Run the daemon manually with: harbour serve"
    Write-Host ""
    Info "Installation complete."
    exit 0
}

$logDir = Join-Path $env:APPDATA "mcp-harbour"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$serviceScript = @"
`$ErrorActionPreference = 'Stop'
`$svc = Get-Service -Name '$ServiceName' -ErrorAction SilentlyContinue
if (`$svc) {
    Stop-Service -Name '$ServiceName' -Force -ErrorAction SilentlyContinue
    sc.exe delete '$ServiceName'
    Start-Sleep -Seconds 2
}
& '$ServiceBin' install
Start-Service -Name '$ServiceName'
"@

if ($isAdmin) {
    Invoke-Expression $serviceScript
} else {
    Info "Requesting administrator permission to install the service..."
    $scriptPath = Join-Path $env:TEMP "mcp-harbour-install-service.ps1"
    $serviceScript | Out-File -FilePath $scriptPath -Encoding UTF8
    Start-Process powershell -Verb RunAs `
        -ArgumentList "-ExecutionPolicy Bypass -File `"$scriptPath`"" `
        -Wait
    Remove-Item $scriptPath -Force -ErrorAction SilentlyContinue
}

# Verify
$check = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($check -and $check.Status -eq "Running") {
    Info "Service installed and running on 127.0.0.1:4767"
} elseif ($check) {
    Warn "Service installed but not running. Try: harbour start"
} else {
    Warn "Service installation failed. You can start the daemon manually: harbour serve"
}

Write-Host ""
Info "Manage with:"
Write-Host "  harbour status"
Write-Host "  harbour stop"
Write-Host "  harbour start"

Write-Host ""
Info "Installation complete."
