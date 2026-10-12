param([string]$Config = (Join-Path $PSScriptRoot 'moneyball-edge.json'))
$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$sourceRoot = [IO.Path]::GetFullPath([string]$settings.source_root)
$port = [int]$settings.port
$dashboard = [string]$settings.dashboard_file
$healthUrl = "http://127.0.0.1:$port/api/staffer-live?limit=1"
$ready = $false
try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 8
    $ready = $health.schema -in @('melange-staffer-live-v1','melange-staffer-live-v2')
} catch {}
if (-not $ready) {
    $starter = Join-Path $sourceRoot 'fec\surfaces\start_staffer_live.ps1'
    & $starter -SourceRoot $sourceRoot -Port $port -DashboardFile $dashboard | Out-Null
}
$deadline = (Get-Date).AddSeconds(150)
do {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 8
        # Open while the network index is still assembling; the dashboard has a
        # first-class loading state and should not look like a failed launcher.
        $ready = $health.schema -in @('melange-staffer-live-v1','melange-staffer-live-v2')
    } catch { $ready = $false }
    if (-not $ready) { Start-Sleep -Milliseconds 600 }
} while (-not $ready -and (Get-Date) -lt $deadline)
if (-not $ready) { throw 'Moneyball data service did not respond.' }
$edge = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge is not installed.' }
$profile = Join-Path $env:LOCALAPPDATA 'Melange\MoneyballApp\edge-profile'
New-Item -ItemType Directory -Path $profile -Force | Out-Null
$url = "http://127.0.0.1:$port/?v=desktop-app"
Start-Process -FilePath $edge -ArgumentList @("--app=$url", "--user-data-dir=$profile", '--no-first-run')
