param(
    [string]$SourceRoot = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),
    [string]$AppRoot = (Join-Path $env:LOCALAPPDATA 'Melange\MoneyballApp')
)
$ErrorActionPreference = 'Stop'
$candidate = Join-Path $AppRoot 'staging\Moneyball'
$exe = Join-Path $candidate 'Moneyball.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Build the Moneyball executable before staging.' }
$config = [ordered]@{
    source_root = (Resolve-Path -LiteralPath $SourceRoot).Path
    service_python = (Get-Command python -ErrorAction Stop).Source
    dashboard_file = 'Moneyball_Redesign.html'
    port = 8841
}
$config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $candidate 'moneyball-app.json') -Encoding utf8
[ordered]@{
    status = 'STAGED_NOT_RELEASED'
    executable = $exe
    executable_sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLower()
    source_root = $config.source_root
    pending = @('Approved design implementation', 'Coordinated data and dashboard validation', 'Desktop installation')
    updated_at = [DateTime]::UtcNow.ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $AppRoot 'candidate.json') -Encoding utf8
Write-Output $exe
