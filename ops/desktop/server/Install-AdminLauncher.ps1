# Creates Desktop and Start-menu "Melange Admin" shortcuts. Each runs pythonw.exe (no console window) ->
# desktop_admin.py access-dashboard, which opens the loopback dashboard in an Edge/Chrome app window on a one-time
# link; a second click while it runs asks the running dashboard for a fresh link. The shortcut holds no secret.
# -Uninstall removes both shortcuts.
[CmdletBinding()]
param(
    [string]$PythonExecutable,
    [string]$StateDirectory = (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents\MelangeDesktopAdmin'),
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
# An unavailable shell folder yields '' and is skipped, so -Uninstall still cleans the other one.
$links = @(foreach ($folder in 'Desktop', 'Programs') { $dir = [Environment]::GetFolderPath($folder); if ($dir) { Join-Path $dir 'Melange Admin.lnk' } })

if ($Uninstall) {
    foreach ($link in $links) {
        if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link; Write-Host "Removed $link" }
    }
    return
}
if ($links.Count -ne 2) { throw 'The Desktop or Start-menu folder is unavailable for this user.' }

if (-not $PythonExecutable) {
    $found = Get-Command python.exe -All -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1
    if (-not $found) { throw 'Python 3.12+ was not found. Pass -PythonExecutable.' }
    $PythonExecutable = $found.Source
}
$python = (Resolve-Path -LiteralPath $PythonExecutable).ProviderPath
if ($python -match '\\WindowsApps\\') { throw 'Use a real Python install, not a WindowsApps alias.' }
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw 'pythonw.exe must exist beside python.exe.' }
& $python -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }

# access-dashboard needs what access-init writes (same set Start-AccessDashboard.ps1 checks); pythonw would fail silently.
foreach ($name in 'access-trust.json', 'access.sqlite3', 'keys') {
    if (-not (Test-Path -LiteralPath (Join-Path $StateDirectory $name))) { throw 'Initialize the admin state first: python desktop_admin.py init, then access-init.' }
}
$state = (Resolve-Path -LiteralPath $StateDirectory).ProviderPath.TrimEnd('\')

$admin = Join-Path $PSScriptRoot 'desktop_admin.py'
$icon = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\client\icons\melange.ico'))
foreach ($path in $admin, $icon) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing $path" } }

$shell = New-Object -ComObject WScript.Shell
foreach ($link in $links) {
    $lnk = $shell.CreateShortcut($link)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = ('"{0}" --state "{1}" access-dashboard' -f $admin, $state)
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation = "$icon,0"
    $lnk.Description = 'Melange Admin - local approval console'
    $lnk.Save()
    Write-Host "Added $link"
}
