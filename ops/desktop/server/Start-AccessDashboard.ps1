[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PythonExecutable,[Parameter(Mandatory=$true)][string]$StateDirectory)
$ErrorActionPreference='Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'PowerShell 7 is required.' }
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Use the ordinary Windows user account.' }
$state=(Get-Item -LiteralPath $StateDirectory).FullName
foreach ($path in @($state,(Join-Path $state 'access.sqlite3'),(Join-Path $state 'access-trust.json'),(Join-Path $state 'keys'))) {
    $item=Get-Item -LiteralPath $path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Protected state cannot redirect.' }
    $acl=Get-Acl -LiteralPath $path
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @($identity.User.Value,'S-1-5-18')) { throw 'Protected state owner mismatch.' }
    foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and $rule.IdentityReference.Value -notin @($identity.User.Value,'S-1-5-18')) { throw 'Protected state is accessible by another identity.' }
    }
}
$info=[Diagnostics.ProcessStartInfo]::new()
$info.FileName=(Get-Item -LiteralPath $PythonExecutable).FullName
$info.UseShellExecute=$false; $info.CreateNoWindow=$true
foreach ($argument in @((Join-Path $PSScriptRoot 'desktop_admin.py'),'--state',$state,'access-dashboard')) { $info.ArgumentList.Add($argument) }
$process=[Diagnostics.Process]::Start($info)
$process.Dispose()
