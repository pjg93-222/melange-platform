param(
    [Parameter(Mandatory=$true)][string]$ReleaseReceipt,
    [string]$AppRoot = (Join-Path $env:LOCALAPPDATA 'Melange\MoneyballApp')
)
$ErrorActionPreference = 'Stop'
$receipt = Get-Content -LiteralPath $ReleaseReceipt -Raw | ConvertFrom-Json
foreach ($gate in @('status', 'data_validation', 'dashboard_validation', 'app_validation', 'design_approval')) {
    if ($receipt.$gate -ne 'PASS') { throw "Coordinated release gate is not PASS: $gate" }
}
$candidate = Join-Path $AppRoot 'staging\Moneyball'
foreach ($binding in $receipt.files) {
    if ((Get-FileHash -LiteralPath $binding.path -Algorithm SHA256).Hash.ToLower() -ne $binding.sha256) {
        throw "Coordinated release file changed: $($binding.path)"
    }
}
if (-not $receipt.files -or -not ($receipt.files | Where-Object { $_.path -eq (Join-Path $candidate 'Moneyball.exe') })) {
    throw 'The executable must be bound by the release receipt.'
}
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
$releaseDirectory = Join-Path $AppRoot "releases\$tag"
New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
Copy-Item -LiteralPath $candidate -Destination $releaseDirectory -Recurse
$exe = Join-Path $releaseDirectory 'Moneyball\Moneyball.exe'
$desktop = [Environment]::GetFolderPath('Desktop')
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Melange Systems'
New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
$shell = New-Object -ComObject WScript.Shell
foreach ($path in @((Join-Path $desktop 'Moneyball.lnk'), (Join-Path $startMenu 'Moneyball.lnk'))) {
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination (Join-Path $AppRoot ('previous-' + [IO.Path]::GetFileName($path)))
    }
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = Split-Path $exe
    $shortcut.Description = 'Moneyball research workspace · Melange Systems'
    $shortcut.IconLocation = "$exe,0"
    $shortcut.Save()
}
Copy-Item -LiteralPath $ReleaseReceipt -Destination (Join-Path $releaseDirectory 'release.json')
Write-Output "Installed Moneyball: $exe"
Write-Output "Desktop launcher: $(Join-Path $desktop 'Moneyball.lnk')"
