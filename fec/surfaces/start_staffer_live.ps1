param([string]$SourceRoot = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent), [int]$Port = 8841, [string]$DashboardFile = '')
$ErrorActionPreference = 'Stop'
$pythonPath = (Get-Command python -ErrorAction Stop).Source
$runtimeDirectory = Join-Path $SourceRoot 'audits\staffer_live_runtime'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$serverMetadataPath = Join-Path $runtimeDirectory 'server.json'
if (-not $DashboardFile -and (Test-Path -LiteralPath $serverMetadataPath)) {
    $priorServerMetadata = Get-Content -LiteralPath $serverMetadataPath -Raw | ConvertFrom-Json
    $DashboardFile = [string]$priorServerMetadata.dashboard_file
}
# The coordinated v8 release serves this verified artifact; explicit/runtime choices persist.
if (-not $DashboardFile) { $DashboardFile = 'Moneyball_Redesign.html' }
if ($DashboardFile -notin @('Moneyball.html', 'Moneyball_Redesign.html')) {
    throw "Unsupported dashboard filename: $DashboardFile"
}
$existing = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($existing) {
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/staffer-live?limit=50" -TimeoutSec 45
        if ($health.schema -in @('melange-staffer-live-v1','melange-staffer-live-v2')) {
            Write-Output "Live staffer dashboard already running: http://127.0.0.1:$Port/staffer-live/"
            exit 0
        }
    } catch {}
    throw "Port $Port is in use by another service; no process was stopped."
}
$serverScript = Join-Path $PSScriptRoot 'staffer_live_server.py'
$taskArguments = @(('"' + $serverScript + '"'), '--source-root', ('"' + $SourceRoot + '"'), '--port', [string]$Port, '--dashboard-file', $DashboardFile)
$process = Start-Process -FilePath $pythonPath -ArgumentList $taskArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDirectory 'server.stdout.log') -RedirectStandardError (Join-Path $runtimeDirectory 'server.stderr.log')
[pscustomobject]@{pid=$process.Id;port=$Port;source_root=$SourceRoot;dashboard_file=$DashboardFile;started_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $serverMetadataPath -Encoding utf8
Write-Output "Started live staffer dashboard: http://127.0.0.1:$Port/staffer-live/ (PID $($process.Id))"
