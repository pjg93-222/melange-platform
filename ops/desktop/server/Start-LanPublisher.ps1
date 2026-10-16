[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ListenIPAddress,
    [ValidateRange(1024, 65535)][int]$Port = 38443,
    [string]$PythonExecutable,
    [string]$StateDirectory = (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents\MelangeDesktopAdmin'),
    [string]$ExpectedNetworkName,
    [switch]$InstallFirewallRule,
    [switch]$ConfigureOnly
)
$ErrorActionPreference = 'Stop'

# Only the designated adapter is considered. Never turn a Public network Private.
$adapterAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ListenIPAddress
$profile = Get-NetConnectionProfile -InterfaceIndex $adapterAddress.InterfaceIndex
if ($ExpectedNetworkName -and $profile.Name -cne $ExpectedNetworkName) {
    throw 'The active network does not match the administrator-approved LAN.'
}
if ($profile.NetworkCategory -ne 'Private') {
    throw 'LAN publishing requires a trusted network already marked Private in Windows. Network settings were not changed.'
}
if ($ListenIPAddress -notmatch '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') {
    throw 'Only a private IPv4 LAN address is allowed.'
}
$pythonCommand = if ($PythonExecutable) {
    (Get-Item -LiteralPath $PythonExecutable -ErrorAction Stop).FullName
} else {
    (Get-Command python.exe -ErrorAction Stop).Source
}
$serverScript = Join-Path $PSScriptRoot 'desktop_admin.py'
$ruleName = 'MelangeDesktopUpdates-LAN-' + $Port
if ($InstallFirewallRule) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Installing the narrowly scoped firewall rule requires an elevated PowerShell window.'
    }
    $existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
        throw 'A rule with this name already exists. Inspect it rather than silently broadening or replacing it.'
    }
    New-NetFirewallRule -Name $ruleName -DisplayName $ruleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -LocalAddress $ListenIPAddress -RemoteAddress LocalSubnet `
        -Profile Private -InterfaceAlias $profile.InterfaceAlias -Program $pythonCommand | Out-Null
}
$rule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if (-not $rule) {
    throw 'Private-LAN firewall rule not installed. Review this script and run once with -InstallFirewallRule from elevated PowerShell.'
}
# A matching name alone does not establish a safe firewall policy.
$addressFilter = $rule | Get-NetFirewallAddressFilter
$portFilter = $rule | Get-NetFirewallPortFilter
$applicationFilter = $rule | Get-NetFirewallApplicationFilter
$interfaceFilter = $rule | Get-NetFirewallInterfaceFilter
if (@($rule).Count -ne 1 -or $rule.Enabled -ne 'True' -or
    $rule.Direction -ne 'Inbound' -or $rule.Action -ne 'Allow' -or
    $rule.Profile -ne 'Private' -or
    (@($addressFilter.LocalAddress) -join ',') -ne $ListenIPAddress -or
    (@($addressFilter.RemoteAddress) -join ',') -ne 'LocalSubnet' -or
    $portFilter.Protocol -ne 'TCP' -or
    (@($portFilter.LocalPort) -join ',') -ne [string]$Port -or
    $applicationFilter.Program -ne $pythonCommand -or
    (@($interfaceFilter.InterfaceAlias) -join ',') -ne $profile.InterfaceAlias) {
    throw 'The existing firewall rule does not match the required private-LAN scope. It was not changed; inspect and correct it before starting the publisher.'
}
if ($ConfigureOnly) {
    Write-Output 'Private LAN firewall configuration verified. Publisher was not started.'
    exit 0
}
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run the publisher as the ordinary user. Elevation is only for firewall configuration.'
}
& $pythonCommand $serverScript --state $StateDirectory serve --listen $ListenIPAddress --port $Port
exit $LASTEXITCODE
