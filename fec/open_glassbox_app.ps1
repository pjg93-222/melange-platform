<#
.SYNOPSIS
  Launch the GLASS BOX app -- a local web view of the shared Glass Box event
  stream -- and open it in the default browser.

.DESCRIPTION
  Starts fec\surfaces\glassbox_app_server.py (Python 3.12, stdlib only) bound to
  127.0.0.1, waits until it answers, then opens the browser at its URL. If the
  server is already running it is reused: this never spawns a duplicate.

  The app is READ-ONLY with respect to the event stream. It never deletes,
  moves, or rewrites an event file, and it never appends abandoned markers --
  that stays the job of reap_abandoned_glassbox_tasks.ps1. It does append its
  own lifecycle events (started / heartbeat / stopped) so the viewer is itself
  visible in the stream it observes.

.PARAMETER Port
  Preferred TCP port on 127.0.0.1. If busy, the next free port is used.

.PARAMETER Stop
  Stop a running server instead of starting one.

.PARAMETER Restart
  Stop then start, picking up code changes.

.PARAMETER NoBrowser
  Start (or verify) the server and print the URL without opening a browser.

.PARAMETER NoShortcut
  Skip creating/refreshing the desktop shortcut.

.EXAMPLE
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\fec\open_glassbox_app.ps1
#>
param(
    [int]$Port = 8823,
    [string]$EventsDirectory = "C:\Users\pgoff\.claude\glassbox\events",
    [string]$StateDirectory = "C:\Users\pgoff\.claude\glassbox\state",
    [string]$Python = "C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe",
    [double]$ScanIntervalSeconds = 2.0,
    [double]$StaleMinutes = 20,
    [int]$WaitSeconds = 40,
    [switch]$Stop,
    [switch]$Restart,
    [switch]$NoBrowser,
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ServerScript = Join-Path $ScriptDir 'surfaces\glassbox_app_server.py'
$FrontEnd     = Join-Path $ScriptDir 'surfaces\glassbox_app.html'
$ShimScript   = Join-Path $ScriptDir 'glassbox_app.vbs'
$StateFile    = Join-Path $StateDirectory 'glassbox_app.json'
$LogFile      = Join-Path $StateDirectory 'glassbox_app.log'

foreach ($required in @($ServerScript, $FrontEnd)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "missing app file: $required" }
}
if (-not (Test-Path -LiteralPath $Python)) {
    $fallback = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $fallback) { throw "python not found at $Python and no python.exe on PATH" }
    $Python = $fallback
}
if (-not (Test-Path -LiteralPath $StateDirectory)) {
    New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

function Get-AppHealth {
    param([int]$ProbePort)
    try {
        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/health" -f $ProbePort) `
            -UseBasicParsing -TimeoutSec 3
        $payload = $response.Content | ConvertFrom-Json
        if ($payload.app -eq 'glassbox-app') { return $payload }
    }
    catch { }
    return $null
}

function Get-RecordedState {
    if (-not (Test-Path -LiteralPath $StateFile)) { return $null }
    try { return (Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json) } catch { return $null }
}

function Test-PortFree {
    param([int]$Candidate)
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Candidate)
        $listener.Start()
        return $true
    }
    catch { return $false }
    finally { if ($listener) { try { $listener.Stop() } catch { } } }
}

function Find-RunningApp {
    # A recorded port first, then the preferred port, then a short sweep.
    $recorded = Get-RecordedState
    if ($recorded -and $recorded.port) {
        $health = Get-AppHealth -ProbePort ([int]$recorded.port)
        if ($health) { return $health }
    }
    $health = Get-AppHealth -ProbePort $Port
    if ($health) { return $health }
    foreach ($offset in 1..8) {
        $health = Get-AppHealth -ProbePort ($Port + $offset)
        if ($health) { return $health }
    }
    return $null
}

function Stop-App {
    # Status goes to the host, not the output stream: the caller consumes the
    # boolean return, which would otherwise swallow these messages.
    $health = Find-RunningApp
    if (-not $health) {
        Write-Host "GLASS BOX app is not running."
        return $false
    }
    Write-Host ("Stopping GLASS BOX app (pid {0}, port {1})..." -f $health.pid, $health.port)

    # Prefer a graceful stop: the server then writes its own terminal event
    # instead of leaving a phantom "running" row on the stream.
    $recorded = Get-RecordedState
    if ($recorded -and $recorded.shutdown_token) {
        try {
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri (
                "http://127.0.0.1:{0}/api/shutdown?token={1}" -f $health.port, $recorded.shutdown_token
            ) | Out-Null
        }
        catch { }
        foreach ($attempt in 1..20) {
            Start-Sleep -Milliseconds 250
            if (-not (Get-AppHealth -ProbePort ([int]$health.port))) { return $true }
        }
    }

    Write-Host "Graceful stop did not take; forcing."
    try { Stop-Process -Id ([int]$health.pid) -Force -ErrorAction Stop } catch {
        Write-Warning ("could not stop pid {0}: {1}" -f $health.pid, $_.Exception.Message)
        return $false
    }
    Start-Sleep -Milliseconds 600
    # A forced stop cannot write its own terminal event, so the state file it
    # left behind would point at a dead pid. Clear it here.
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    return $true
}

function New-HiddenShim {
    $vbs = @'
' Hidden launch shim for the GLASS BOX app. Runs open_glassbox_app.ps1 with no
' console window so the desktop shortcut behaves like an application launcher.
Option Explicit
Dim shell, fso, base, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & base & "\open_glassbox_app.ps1"" -NoShortcut"
shell.Run cmd, 0, False
'@
    Set-Content -LiteralPath $ShimScript -Value $vbs -Encoding ASCII
}

function New-DesktopShortcut {
    $desktop = [Environment]::GetFolderPath('Desktop')
    if (-not $desktop) { return $null }
    $linkPath = Join-Path $desktop 'GLASS BOX.lnk'
    New-HiddenShim
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = (Join-Path $env:SystemRoot 'System32\wscript.exe')
    $link.Arguments = ('"{0}"' -f $ShimScript)
    $link.WorkingDirectory = $ScriptDir
    $link.IconLocation = (Join-Path $env:SystemRoot 'System32\imageres.dll') + ',109'
    $link.Description = 'GLASS BOX - live view of the shared orchestration event stream'
    $link.WindowStyle = 7   # minimized; the shim itself runs fully hidden
    $link.Save()
    return $linkPath
}

# ---------------------------------------------------------------------------
# stop / restart
# ---------------------------------------------------------------------------

if ($Stop) { $null = Stop-App; return }
if ($Restart) { $null = Stop-App }

# ---------------------------------------------------------------------------
# start (single instance)
# ---------------------------------------------------------------------------

# Serialize concurrent launches so two shortcut double-clicks cannot both start
# a server. Whichever wins the mutex starts it; the other reuses it.
$mutex = New-Object System.Threading.Mutex($false, 'Global\MelangeGlassBoxAppLauncher')
$held = $false
try {
    try { $held = $mutex.WaitOne(20000) } catch [System.Threading.AbandonedMutexException] { $held = $true }

    $health = Find-RunningApp
    if ($health) {
        $url = "http://127.0.0.1:{0}/" -f $health.port
        Write-Output ("GLASS BOX app already running (pid {0}); reusing it." -f $health.pid)
    }
    else {
        $chosen = $Port
        while ($chosen -lt ($Port + 40) -and -not (Test-PortFree -Candidate $chosen)) { $chosen++ }
        if ($chosen -ge ($Port + 40)) { throw "no free loopback port near $Port" }

        $arguments = @(
            ('"{0}"' -f $ServerScript),
            '--host', '127.0.0.1',
            '--port', $chosen,
            ('--events "{0}"' -f $EventsDirectory),
            ('--state-dir "{0}"' -f $StateDirectory),
            ('--front-end "{0}"' -f $FrontEnd),
            '--scan-interval', $ScanIntervalSeconds,
            '--stale-minutes', $StaleMinutes
        ) -join ' '

        Write-Output ("Starting GLASS BOX app on 127.0.0.1:{0} ..." -f $chosen)
        Start-Process -FilePath $Python -ArgumentList $arguments `
            -WorkingDirectory $ScriptDir -WindowStyle Hidden `
            -RedirectStandardOutput $LogFile -RedirectStandardError ($LogFile + '.err') | Out-Null

        $deadline = (Get-Date).AddSeconds($WaitSeconds)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 400
            $health = Get-AppHealth -ProbePort $chosen
            if ($health) { break }
        }
        if (-not $health) {
            $tail = ''
            foreach ($candidate in @(($LogFile + '.err'), $LogFile)) {
                if (Test-Path -LiteralPath $candidate) {
                    $tail += (Get-Content -LiteralPath $candidate -Tail 20 -ErrorAction SilentlyContinue) -join "`n"
                }
            }
            throw ("GLASS BOX app did not answer on port {0} within {1}s.`n{2}" -f $chosen, $WaitSeconds, $tail)
        }
        $url = "http://127.0.0.1:{0}/" -f $health.port
        Write-Output ("GLASS BOX app started (pid {0})." -f $health.pid)
    }
}
finally {
    if ($held) { try { $mutex.ReleaseMutex() } catch { } }
    $mutex.Dispose()
}

if (-not $NoShortcut) {
    try {
        $link = New-DesktopShortcut
        if ($link) { Write-Output ("Desktop shortcut : {0}" -f $link) }
    }
    catch { Write-Warning ("could not create desktop shortcut: {0}" -f $_.Exception.Message) }
}

Write-Output ""
Write-Output ("GLASS BOX  ->  {0}" -f $url)
Write-Output ("events     :  {0}" -f $EventsDirectory)
Write-Output ("api        :  {0}api/rows | {0}api/events | {0}api/run?key=lane|source|run_id | {0}api/health" -f $url)
Write-Output ("log        :  {0}" -f $LogFile)
Write-Output ("stop with  :  .\fec\open_glassbox_app.ps1 -Stop")

if (-not $NoBrowser) { Start-Process $url | Out-Null }
