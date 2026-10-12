param(
    [Parameter(Mandatory=$true)][string]$ReleaseReceipt,
    [string]$AppRoot = (Join-Path $env:LOCALAPPDATA 'Melange\MoneyballApp')
)
$ErrorActionPreference = 'Stop'
$receipt = Get-Content -LiteralPath $ReleaseReceipt -Raw | ConvertFrom-Json
foreach ($gate in @('status', 'data_validation', 'dashboard_validation', 'app_validation', 'design_approval')) {
    if ($receipt.$gate -ne 'PASS') { throw "Coordinated release gate is not PASS: $gate" }
}
foreach ($binding in $receipt.files) {
    if ((Get-FileHash -LiteralPath $binding.path -Algorithm SHA256).Hash.ToLower() -ne $binding.sha256) {
        throw "Coordinated release file changed: $($binding.path)"
    }
}
$launcherSource = Join-Path $PSScriptRoot 'launch_moneyball_edge.py'
$iconSource = Join-Path $PSScriptRoot 'melange.ico'
if (-not ($receipt.files | Where-Object { $_.path -eq $launcherSource })) {
    throw 'The Edge launcher source must be bound by the coordinated release receipt.'
}
if (-not ($receipt.files | Where-Object { $_.path -eq $iconSource })) {
    throw 'The Melange icon must be bound by the coordinated release receipt.'
}
$installDirectory = Join-Path $AppRoot 'edge'
New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
Copy-Item -LiteralPath $launcherSource -Destination (Join-Path $installDirectory 'launch_moneyball_edge.py') -Force
Copy-Item -LiteralPath $iconSource -Destination (Join-Path $installDirectory 'melange.ico') -Force
[pscustomobject]@{
    source_root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    port = 8841
    dashboard_file = 'Moneyball_Redesign.html'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $installDirectory 'moneyball-edge.json') -Encoding utf8
$edge = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge is not installed.' }
$desktop = [Environment]::GetFolderPath('Desktop')
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Melange Systems'
New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
$shell = New-Object -ComObject WScript.Shell
$python = (Get-Command python.exe -ErrorAction Stop).Source
$pythonWindowless = Join-Path (Split-Path $python -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Windowless Python is required for the desktop launcher.' }
$launcher = Join-Path $installDirectory 'launch_moneyball_edge.py'
$icon = Join-Path $installDirectory 'melange.ico'
$launcherPaths = @(
    (Join-Path $desktop 'Melange.lnk'),
    (Join-Path $desktop 'Moneyball.lnk'),
    (Join-Path $startMenu 'Melange.lnk'),
    (Join-Path $startMenu 'Moneyball.lnk')
)
foreach ($path in $launcherPaths) {
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination (Join-Path $AppRoot ('previous-' + [IO.Path]::GetFileName($path))) -Force
    }
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $pythonWindowless
    $shortcut.Arguments = '"' + $launcher + '"'
    $shortcut.WorkingDirectory = $installDirectory
    $shortcut.Description = 'Melange research workspace · Moneyball dashboard'
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Save()
}
Copy-Item -LiteralPath $ReleaseReceipt -Destination (Join-Path $installDirectory 'release.json') -Force
Write-Output "Installed Melange desktop launcher: $(Join-Path $desktop 'Melange.lnk')"
