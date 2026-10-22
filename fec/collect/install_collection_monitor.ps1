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
# Same browser order as launch_collection_monitor.pyw; pythonw.exe (checked above) when neither is installed.
$collectionIcon = @('C:\Program Files\Google\Chrome\Application\chrome.exe', 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', $collectionPythonW) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$collectionShortcut.IconLocation = "$collectionIcon,0"
$collectionShortcut.Save()
Write-Output $collectionLink
