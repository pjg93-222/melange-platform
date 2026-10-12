[CmdletBinding()]
param([switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
$desktopApp = Join-Path $PSScriptRoot 'app\desktop'
$userFolders = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
$effectiveLocalData = [Environment]::ExpandEnvironmentVariables([string]$userFolders.'Local AppData')
if (-not [IO.Path]::IsPathRooted($effectiveLocalData)) { throw 'Windows did not provide a valid local application-data folder.' }
[IO.Directory]::CreateDirectory($effectiveLocalData) | Out-Null
[IO.File]::WriteAllText((Join-Path $desktopApp 'local-appdata.path'), $effectiveLocalData)
& (Join-Path $desktopApp 'Build-Desktop.ps1')
$target = Join-Path $desktopApp 'MENTAT.exe'
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  $shortcut = $shell.CreateShortcut((Join-Path $folder 'MENTAT.lnk'))
  $shortcut.TargetPath = $target
  $shortcut.WorkingDirectory = $PSScriptRoot
  $shortcut.IconLocation = (Join-Path $desktopApp 'MENTAT.ico') + ',0'
  $shortcut.Description = 'MENTAT - local political-finance console'
  $shortcut.Save()
}
Write-Host 'MENTAT is ready in your Desktop and Start menu.'
if (-not $NoLaunch) { Start-Process -FilePath $target }
