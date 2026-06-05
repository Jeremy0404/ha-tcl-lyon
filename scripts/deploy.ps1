<#
.SYNOPSIS
  Mirror the local tcl_lyon integration onto the Home Assistant config share for live testing.

.DESCRIPTION
  Copies custom_components/tcl_lyon/ to \\192.168.1.177\config\custom_components\tcl_lyon\
  with robocopy /MIR, so the deployed copy is an exact mirror (stale files removed,
  __pycache__/*.pyc excluded). Python caches imported modules, so a copy alone does
  nothing until HA restarts — the script prints that reminder. Pass -Restart to trigger
  the restart over the REST API instead (needs HA_TOKEN in .env; see below).

.PARAMETER Watch
  Stay running and re-mirror on every file save under custom_components/tcl_lyon/.

.PARAMETER Restart
  After mirroring, ask HA to restart via POST /api/services/homeassistant/restart.
  Requires HA_TOKEN (a long-lived access token) in .env. Default flow is manual restart.

.EXAMPLE
  pwsh scripts/deploy.ps1            # one-shot mirror, then restart HA yourself
  pwsh scripts/deploy.ps1 -Watch     # re-mirror on each save
  pwsh scripts/deploy.ps1 -Restart   # mirror + restart HA over the API (needs token)

.NOTES
  Override the destination with $env:HA_CONFIG_SHARE, or HA_URL/HA_TOKEN via .env.
#>
[CmdletBinding()]
param(
    [switch]$Watch,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Source   = Join-Path $RepoRoot 'custom_components\tcl_lyon'

# Destination defaults to the SMB-mounted HAOS config share; override per host if needed.
$Dest = if ($env:HA_CONFIG_SHARE) {
    Join-Path $env:HA_CONFIG_SHARE 'custom_components\tcl_lyon'
} else {
    '\\192.168.1.177\config\custom_components\tcl_lyon'
}

if (-not (Test-Path -LiteralPath $Source)) {
    throw "Source not found: $Source"
}

# Read KEY=VALUE pairs from .env (quotes stripped) so -Restart can find HA_URL / HA_TOKEN.
function Read-DotEnv {
    $envFile = Join-Path $RepoRoot '.env'
    $map = @{}
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in Get-Content -LiteralPath $envFile) {
            if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
            $key, $val = $line -split '=', 2
            $map[$key.Trim()] = $val.Trim().Trim('"').Trim("'")
        }
    }
    return $map
}

function Invoke-Mirror {
    # /MIR mirrors (incl. deletions); /XD,/XF drop Python build artifacts that HA regenerates.
    $null = robocopy $Source $Dest /MIR `
        /XD '__pycache__' '.pytest_cache' `
        /XF '*.pyc' '*.pyo' `
        /R:2 /W:2 /NFL /NDL /NJH /NJS /NP
    # robocopy exit codes <8 are success (0 = nothing to copy, 1-7 = files copied/extra/etc.).
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE (is the share reachable?)"
    }
    $stamp = Get-Date -Format 'HH:mm:ss'
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$stamp] synced (no changes) -> $Dest" -ForegroundColor DarkGray
    } else {
        Write-Host "[$stamp] synced -> $Dest" -ForegroundColor Green
    }
}

function Invoke-HARestart {
    $cfg   = Read-DotEnv
    $url   = if ($cfg.HA_URL) { $cfg.HA_URL } else { 'http://192.168.1.177:8123' }
    $token = $cfg.HA_TOKEN
    if (-not $token) {
        Write-Host "HA_TOKEN not set in .env -> restart HA manually (Developer Tools -> YAML -> Restart)." -ForegroundColor Yellow
        Write-Host "  To enable -Restart: HA profile -> Long-lived access tokens -> create, then add HA_TOKEN=... to .env" -ForegroundColor DarkGray
        return
    }
    Write-Host "Restarting HA at $url ..." -ForegroundColor Cyan
    Invoke-RestMethod -Method Post -Uri "$url/api/services/homeassistant/restart" `
        -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 15 | Out-Null
    Write-Host "Restart requested (HA is back in ~30-60 s)." -ForegroundColor Green
}

Write-Host "tcl_lyon deploy" -ForegroundColor Cyan
Write-Host "  from $Source"
Write-Host "  to   $Dest"

if ($Watch) {
    Invoke-Mirror
    Write-Host "Watching for changes (Ctrl+C to stop)..." -ForegroundColor Cyan
    $fsw = New-Object System.IO.FileSystemWatcher $Source
    $fsw.IncludeSubdirectories = $true
    try {
        while ($true) {
            $change = $fsw.WaitForChanged([System.IO.WatcherChangeTypes]::All, 1000)
            if (-not $change.TimedOut) {
                Start-Sleep -Milliseconds 250  # let a burst of saves settle before mirroring
                Invoke-Mirror
                if ($Restart) { Invoke-HARestart }
                elseif (-not $script:warnedRestart) {
                    Write-Host "  (restart HA to load Python changes)" -ForegroundColor DarkGray
                    $script:warnedRestart = $true
                }
            }
        }
    } finally {
        $fsw.Dispose()
    }
} else {
    Invoke-Mirror
    if ($Restart) {
        Invoke-HARestart
    } else {
        Write-Host "Restart HA to load the changes (Developer Tools -> YAML -> Restart)." -ForegroundColor Yellow
    }
}
