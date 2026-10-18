$ErrorActionPreference = 'Stop'
$collectionRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$collectionPython = (Get-Command python).Source
$collectionPythonW = Join-Path (Split-Path $collectionPython -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $collectionPythonW)) { throw 'pythonw.exe was not found.' }
$collectionLauncher = Join-Path $PSScriptRoot 'launch_collection_monitor.pyw'
$collectionDesktop = [Environment]::GetFolderPath('Desktop')
$collectionLink = Join-Path $collectionDesktop 'Melange Collection Monitor.lnk'
$collectionShell = New-Object -ComObject WScript.Shell
$collectionShortcut = $collectionShell.CreateShortcut($collectionLink)
$collectionShortcut.TargetPath = $collectionPythonW
$collectionShortcut.Arguments = '"' + $collectionLauncher + '"'
$collectionShortcut.WorkingDirectory = $collectionRoot
$collectionShortcut.Description = 'Live press recovery and Politico collection status'
$collectionShortcut.IconLocation = 'C:\Program Files\Google\Chrome\Application\chrome.exe,0'
$collectionShortcut.Save()
Write-Output $collectionLink
