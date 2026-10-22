[CmdletBinding()]
param(
    [ValidateSet('Install', 'Remove', 'Status')][string]$Action = 'Status',
    [string]$ListenIPAddress,
    [string]$ExpectedNetworkName,
    # Internet mode behind a local TCP pass-through relay (Tailscale Funnel); replaces the LAN address/name.
    [string]$PublicOrigin,
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
        $configuredOrigin = if (Test-Path -LiteralPath $recordPath) { (Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json).publicOrigin } else { $null }
        [ordered]@{ registered = [bool]$current; entryName = $entryName; scope = 'current-user'; metadataPresent = (Test-Path -LiteralPath $recordPath); publicOrigin = $configuredOrigin } | ConvertTo-Json
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
    if ($PublicOrigin) {
        if ($ListenIPAddress -or $ExpectedNetworkName -or -not $PythonExecutable) {
            throw 'PublicOrigin startup listens on 127.0.0.1 only: pass -PublicOrigin and -PythonExecutable without -ListenIPAddress or -ExpectedNetworkName.'
        }
        if ($Port -ne 38443) { throw 'PublicOrigin startup listens on 127.0.0.1:38443, the only local port relay_tunnel.py forwards to.' }
    } elseif (-not $ListenIPAddress -or -not $ExpectedNetworkName -or -not $PythonExecutable) {
        throw 'Install requires the approved LAN address/name and explicit Python executable.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $stateDirectory 'trust.json'))) {
        throw 'Initialize the protected publisher state first.'
    }
    if (-not $PowerShellExecutable) { $PowerShellExecutable = (Get-Process -Id $PID).Path }
    $PowerShellExecutable = (Get-Item -LiteralPath $PowerShellExecutable).FullName
    # Ask the executable itself: MSIX app-execution aliases (the stable path across updates) carry no file version.
    if ([int](& $PowerShellExecutable -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.Major') -lt 7) {
        throw 'Publisher startup requires a PowerShell 7 executable.'
    }
    $PythonExecutable = (Get-Item -LiteralPath $PythonExecutable).FullName
    if ($PublicOrigin) {
        # desktop_admin.py applies the https_origin rules; the origin must already be canonical and a DNS name.
        $descriptor = (& $PythonExecutable (Join-Path $PSScriptRoot 'desktop_admin.py') --state $stateDirectory --public-origin $PublicOrigin access-trust --url $PublicOrigin) -join "`n"
        if ($LASTEXITCODE -ne 0 -or ($descriptor | ConvertFrom-Json).url -cne $PublicOrigin -or ([Uri]$PublicOrigin).HostNameType -ne 'Dns') {
            throw 'PublicOrigin must be a canonical https DNS origin accepted by desktop_admin.py (lowercase, no path, :443 omitted), with application access initialized.'
        }
        $mode = @('-PublicOrigin', $PublicOrigin)
    } else {
        & (Join-Path $PSScriptRoot 'Start-LanPublisher.ps1') -ListenIPAddress $ListenIPAddress `
            -ExpectedNetworkName $ExpectedNetworkName -PythonExecutable $PythonExecutable -StateDirectory $stateDirectory -Port $Port -ConfigureOnly | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Approved LAN/firewall validation failed; startup was not registered.' }
        $mode = @('-ListenIPAddress', $ListenIPAddress, '-ExpectedNetworkName', $ExpectedNetworkName)
    }
    $arguments = @($PowerShellExecutable, '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-File', $supervisor) + $mode +
        @('-PythonExecutable', $PythonExecutable, '-StateDirectory', $stateDirectory, '-Port', [string]$Port)
    $command = ($arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' '
    if ($current -and $current -cne $command) { throw 'A different startup entry already uses this name. It was not overwritten.' }
    $record = [ordered]@{ schemaVersion = 1; entryName = $entryName; supervisor = $supervisor; command = $command; registeredAt = [DateTime]::UtcNow.ToString('o') }
    if ($PublicOrigin) { $record.publicOrigin = $PublicOrigin }
    $temporary = $recordPath + '.tmp'
    [IO.File]::WriteAllText($temporary, ($record | ConvertTo-Json))
    [IO.File]::Move($temporary, $recordPath, $true)
    $key.SetValue($entryName, $command, [Microsoft.Win32.RegistryValueKind]::String)
    Write-Output $(if ($PublicOrigin) { 'Registered publisher startup for the current user only. The listener binds 127.0.0.1 for the local relay; no LAN or firewall rule is used.' }
        else { 'Registered publisher startup for the current user only. Approved LAN and firewall checks run before every listener start.' })
} finally {
    if ($key) { $key.Dispose() }
}
