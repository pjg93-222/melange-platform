[CmdletBinding()]
param(
    [switch]$PlanOnly,
    [switch]$CanaryOnly,
    [switch]$Full,
    [ValidateRange(1, 1000)]
    [int]$CanarySize = 12,
    [string]$StateDir,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$selectedModes = (@($PlanOnly, $CanaryOnly, $Full) | Where-Object { $_ }).Count
if ($selectedModes -ne 1) {
    throw 'Select exactly one of -PlanOnly, -CanaryOnly, or -Full. Full collection is never implicit.'
}

$scriptPath = $MyInvocation.MyCommand.Path
$fecDir = Split-Path -Parent $scriptPath
$root = Split-Path -Parent $fecDir
$service = Join-Path $fecDir 'legislation\run_legislation_acceptance_service.py'
if (-not (Test-Path -LiteralPath $service -PathType Leaf)) {
    throw "Standalone service script is missing: $service"
}

$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
$pythonArgs = @()
if ($null -ne $launcher) {
    $pythonExe = $launcher.Source
    $pythonArgs += '-3'
} else {
    $launcher = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $launcher) {
        $launcher = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($null -eq $launcher) {
        throw 'No Python 3 executable or py launcher was found on PATH.'
    }
    $pythonExe = $launcher.Source
}

$pythonArgs += $service
if ($PlanOnly) {
    $pythonArgs += '--plan-only'
} elseif ($CanaryOnly) {
    $pythonArgs += '--canary-only'
    $pythonArgs += '--canary-size'
    $pythonArgs += [string]$CanarySize
} else {
    $pythonArgs += '--full'
}
if ($StateDir) {
    $pythonArgs += '--state-dir'
    $pythonArgs += [System.IO.Path]::GetFullPath($StateDir)
}

if ($Foreground) {
    & $pythonExe @pythonArgs
    exit $LASTEXITCODE
}

$effectiveStateDir = if ($StateDir) {
    [System.IO.Path]::GetFullPath($StateDir)
} else {
    Join-Path $root 'audits\legfix5\legislation_acceptance_service'
}
New-Item -ItemType Directory -Path $effectiveStateDir -Force | Out-Null
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$stdout = Join-Path $effectiveStateDir "launcher-$stamp.stdout.log"
$stderr = Join-Path $effectiveStateDir "launcher-$stamp.stderr.log"
$process = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList $pythonArgs `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

[pscustomobject]@{
    status = 'started'
    pid = $process.Id
    mode = if ($PlanOnly) { 'plan' } elseif ($CanaryOnly) { 'canary' } else { 'full' }
    state_dir = $effectiveStateDir
    progress = (Join-Path $effectiveStateDir 'progress.json')
    stdout = $stdout
    stderr = $stderr
}
