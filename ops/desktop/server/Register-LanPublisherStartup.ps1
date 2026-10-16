[CmdletBinding()]
param(
    [ValidateSet('Install', 'Remove', 'Status')][string]$Action = 'Status',
    [string]$ListenIPAddress,
    [string]$ExpectedNetworkName,
    [string]$PythonExecutable,
    [string]$PowerShellExecutable,
    [string]$StateDirectory = (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents\MelangeDesktopAdmin'),
    [ValidateRange(1024, 65535)][int]$Port = 38443
)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Register publisher startup from the ordinary user account, without elevation.'
}
$stateDirectory = [IO.Path]::GetFullPath($StateDirectory)
$recordPath = Join-Path $stateDirectory 'startup-registration.json'
$supervisor = Join-Path $PSScriptRoot 'Start-LanPublisherAtLogin.ps1'
$entryName = 'MelangeDesktopPublisher'
$runPath = 'Software\Microsoft\Windows\CurrentVersion\Run'

function Quote-NativeArgument([string]$Value) {
    if ($Value -match '[\x00\r\n]') { throw 'Invalid startup argument.' }
    # CommandLineToArgvW escaping; -File receives arguments, never executable source.
    return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($runPath, $Action -ne 'Status')
if (-not $key -and $Action -eq 'Install') { $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($runPath) }
try {
    $current = if ($key) { $key.GetValue($entryName, $null) } else { $null }
    if ($Action -eq 'Status') {
        [ordered]@{ registered = [bool]$current; entryName = $entryName; scope = 'current-user'; metadataPresent = (Test-Path -LiteralPath $recordPath) } | ConvertTo-Json
        return
    }
    if ($Action -eq 'Remove') {
        if (-not $current) { Write-Output 'Publisher login startup is already absent.'; return }
        if (-not (Test-Path -LiteralPath $recordPath)) { throw 'Registration metadata missing. Inspect the existing startup entry before removing it.' }
        $saved = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        if ($saved.entryName -cne $entryName -or $saved.supervisor -cne $supervisor -or $saved.command -cne $current) {
            throw 'Startup registration differs from the owned entry. It was not removed.'
        }
        $key.DeleteValue($entryName, $false)
        Write-Output 'Removed only the current-user publisher login entry. Running processes, firewall, keys, releases and client data were not changed.'
        return
    }
    if (-not $ListenIPAddress -or -not $ExpectedNetworkName -or -not $PythonExecutable) {
        throw 'Install requires the approved LAN address/name and explicit Python executable.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $stateDirectory 'trust.json'))) {
        throw 'Initialize the protected publisher state first.'
    }
    if (-not $PowerShellExecutable) { $PowerShellExecutable = (Get-Process -Id $PID).Path }
    $PowerShellExecutable = (Get-Item -LiteralPath $PowerShellExecutable).FullName
    if ([Diagnostics.FileVersionInfo]::GetVersionInfo($PowerShellExecutable).FileMajorPart -lt 7) {
        throw 'Publisher startup requires a PowerShell 7 executable.'
    }
    $PythonExecutable = (Get-Item -LiteralPath $PythonExecutable).FullName
    & (Join-Path $PSScriptRoot 'Start-LanPublisher.ps1') -ListenIPAddress $ListenIPAddress `
        -ExpectedNetworkName $ExpectedNetworkName -PythonExecutable $PythonExecutable -StateDirectory $stateDirectory -Port $Port -ConfigureOnly | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Approved LAN/firewall validation failed; startup was not registered.' }
    $arguments = @($PowerShellExecutable, '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-File', $supervisor,
        '-ListenIPAddress', $ListenIPAddress, '-ExpectedNetworkName', $ExpectedNetworkName,
        '-PythonExecutable', $PythonExecutable, '-StateDirectory', $stateDirectory, '-Port', [string]$Port)
    $command = ($arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' '
    if ($current -and $current -cne $command) { throw 'A different startup entry already uses this name. It was not overwritten.' }
    $record = [ordered]@{ schemaVersion = 1; entryName = $entryName; supervisor = $supervisor; command = $command; registeredAt = [DateTime]::UtcNow.ToString('o') }
    $temporary = $recordPath + '.tmp'
    [IO.File]::WriteAllText($temporary, ($record | ConvertTo-Json))
    [IO.File]::Move($temporary, $recordPath, $true)
    $key.SetValue($entryName, $command, [Microsoft.Win32.RegistryValueKind]::String)
    Write-Output 'Registered publisher startup for the current user only. Approved LAN and firewall checks run before every listener start.'
} finally {
    if ($key) { $key.Dispose() }
}
