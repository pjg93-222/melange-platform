<#
.SYNOPSIS
  Adds Start Menu shortcuts that open the Melange apps you were invited to in their own Edge window.

.DESCRIPTION
  The apps run on the server; this installs nothing but shortcuts to https://<app>.<domain>/.
  There is no code, no token and no password here: you sign in through Cloudflare Access in the
  browser window, with the email address you were invited on. Nothing needs administrator rights.

.PARAMETER Domain
  The domain Paul gave you, for example example.org.

.PARAMETER Apps
  One or more of: melange, mentat, co05, or05, ct01. Only add the ones you were invited to.

.PARAMETER Remove
  Delete the shortcuts instead of creating them.

.EXAMPLE
  .\Install-MelangeApps.ps1 -Domain example.org -Apps melange,co05
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^(?=.{4,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$')]
    [string]$Domain,

    [Parameter(Mandatory)]
    [ValidateSet('melange', 'mentat', 'co05', 'or05', 'ct01')]
    [string[]]$Apps,

    [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$titles = @{
    melange = 'Melange Desk'
    mentat  = 'Mentat'
    co05    = 'Terrier CO-05'
    or05    = 'Terrier OR-05'
    ct01    = 'Terrier CT-01'
}

$folder = Join-Path ([Environment]::GetFolderPath('Programs')) 'Melange'

if ($Remove) {
    foreach ($app in ($Apps | Select-Object -Unique)) {
        $lnk = Join-Path $folder ($titles[$app] + '.lnk')
        if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk; Write-Host "Removed $($titles[$app])" }
    }
    return
}

$edge = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge was not found. Install Edge, then run this again.' }

New-Item -ItemType Directory -Force -Path $folder | Out-Null
$shell = New-Object -ComObject WScript.Shell
foreach ($app in ($Apps | Select-Object -Unique)) {
    $url = 'https://{0}.{1}/' -f $app, $Domain.ToLowerInvariant()
    $lnk = $shell.CreateShortcut((Join-Path $folder ($titles[$app] + '.lnk')))
    $lnk.TargetPath = $edge
    $lnk.Arguments = '--app=' + $url
    $lnk.Description = $titles[$app] + ' (' + $url + ')'
    $lnk.IconLocation = $edge + ',0'
    $lnk.Save()
    Write-Host ("Added {0} -> {1}" -f $titles[$app], $url)
}
Write-Host "Shortcuts are in Start > Melange. Sign in with the email address you were invited on."
