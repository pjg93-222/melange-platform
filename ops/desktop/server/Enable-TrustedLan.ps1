[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$NetworkName,
    [Parameter(Mandatory = $true)][string]$InterfaceAlias,
    [Parameter(Mandatory = $true)][string]$ListenIPAddress,
    [ValidateRange(1024, 65535)][int]$Port = 38443
)
$ErrorActionPreference = 'Stop'
$profile = Get-NetConnectionProfile -InterfaceAlias $InterfaceAlias
if (@($profile).Count -ne 1 -or $profile.Name -cne $NetworkName) {
    throw 'The connected network does not match the specifically approved trusted network.'
}
$address = Get-NetIPAddress -InterfaceIndex $profile.InterfaceIndex -AddressFamily IPv4 -IPAddress $ListenIPAddress
if (-not $address) { throw 'The approved LAN address is not assigned to this interface.' }
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Windows administrator elevation is required to configure the network and firewall.'
}
if ($profile.NetworkCategory -eq 'Public') {
    Set-NetConnectionProfile -InterfaceIndex $profile.InterfaceIndex -NetworkCategory Private
} elseif ($profile.NetworkCategory -ne 'Private') {
    throw 'Managed network profiles must be configured by their administrator.'
}
# Configure only. The update server must be started separately as the ordinary user.
& (Join-Path $PSScriptRoot 'Start-LanPublisher.ps1') -ListenIPAddress $ListenIPAddress -Port $Port -InstallFirewallRule -ConfigureOnly
exit $LASTEXITCODE
