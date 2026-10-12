param(
    [string]$SourceRoot = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),
    [int]$Port = 8841,
    [int]$ProbeTimeoutSec = 60,
    [int]$MaxRuntimeSec = 120,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# This wrapper runs under scheduled task MelangeStafferLiveSidecar, whose
# Settings.Priority is 7 (= BELOW_NORMAL_PRIORITY_CLASS) and whose
# ExecutionTimeLimit is PT10M. On a loaded box a below-normal PowerShell 5.1
# process can spend tens of seconds to minutes in ENGINE STARTUP, before the
# first line of this script runs; the task then dies with 0x41306
# (SCHED_S_TASK_TERMINATED) having logged nothing at all.
#
# Nothing in this file can shorten that pre-main startup stall - that needs the
# task's Priority raised to 4-6. What this file does guarantee is:
#   * a 'start' line is written the instant the script gets control, so the
#     startup stall becomes measurable instead of invisible;
#   * the process re-raises itself to NORMAL priority immediately;
#   * every blocking step is individually bounded, and no step uses WMI/CIM or
#     Get-NetTCPConnection on the happy path;
#   * a hard self-watchdog force-exits with code 9 if the script ever exceeds
#     MaxRuntimeSec, so the task can never sit in the Running state.
#
# Exit codes: 0 healthy or launcher invoked, 1 unexpected exception,
#             2 port held by a foreign process (nothing touched),
#             3 sidecar listening but unresponsive, 9 self-watchdog timeout.
# This script never stops anything.
# ---------------------------------------------------------------------------

$runtimeDirectory = Join-Path $SourceRoot 'audits\staffer_live_runtime'
$logPath = Join-Path $runtimeDirectory 'watchdog.log'

# --- logging: pure .NET, so it works from any thread and needs no runspace ---
function Write-WatchdogLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f ([DateTime]::UtcNow.ToString('o')), $Message
    try {
        [System.IO.File]::AppendAllText($logPath, ($line + "`r`n"), [System.Text.Encoding]::UTF8)
    } catch { }
}

# Step 0: make the log writable and announce ourselves BEFORE anything else,
# so a startup stall shows up as a late timestamp instead of total silence.
try { [void][System.IO.Directory]::CreateDirectory($runtimeDirectory) } catch { }

$selfProcess = [System.Diagnostics.Process]::GetCurrentProcess()
$startupPriority = 'unknown'
try { $startupPriority = $selfProcess.PriorityClass.ToString() } catch { }
Write-WatchdogLog ("start pid={0} priority={1} port={2} dryrun={3} maxruntime={4}s" -f `
    $PID, $startupPriority, $Port, [bool]$DryRun, $MaxRuntimeSec)

# Undo the scheduler's below-normal priority for the rest of this run. Raising
# to NORMAL needs no privilege and also clears process background mode, which
# is what starves the I/O this script does.
if ($startupPriority -ne 'Normal') {
    try {
        $selfProcess.PriorityClass = [System.Diagnostics.ProcessPriorityClass]::Normal
        Write-WatchdogLog "raised own priority $startupPriority -> Normal"
    } catch {
        Write-WatchdogLog "could not raise priority from ${startupPriority}: $($_.Exception.Message)"
    }
}

# --- hard self-watchdog ------------------------------------------------------
# A ScriptBlock cast to a delegate does NOT work here: PowerShell 5.1 runs it on
# a threadpool thread with no runspace, where it silently never fires (and an
# unhandled throw there kills the process with no log line). A compiled type is
# the only reliable option.
$watchdogArmed = $false
try {
    if (-not ('MelangeSelfWatchdog' -as [type])) {
        Add-Type -Language CSharp -ErrorAction Stop -TypeDefinition @'
using System;
using System.IO;
using System.Threading;
public static class MelangeSelfWatchdog {
    private static Timer _timer;
    public static void Arm(string logPath, int milliseconds, int exitCode) {
        _timer = new Timer(delegate(object ignored) {
            try {
                File.AppendAllText(logPath, "[" + DateTime.UtcNow.ToString("o")
                    + "] self-watchdog: exceeded " + (milliseconds / 1000)
                    + "s; forcing exit " + exitCode + "\r\n");
            } catch { }
            Environment.Exit(exitCode);
        }, null, milliseconds, Timeout.Infinite);
    }
}
'@
    }
    [MelangeSelfWatchdog]::Arm($logPath, ($MaxRuntimeSec * 1000), 9)
    $watchdogArmed = $true
} catch {
    Write-WatchdogLog "self-watchdog could not be armed: $($_.Exception.Message)"
}

$deadlineUtc = [DateTime]::UtcNow.AddSeconds($MaxRuntimeSec)
function Get-RemainingSec { [int][Math]::Max(0, ($deadlineUtc - [DateTime]::UtcNow).TotalSeconds) }

function Trim-WatchdogLog {
    try {
        if ([System.IO.File]::Exists($logPath)) {
            $lines = [System.IO.File]::ReadAllLines($logPath)
            if ($lines.Length -gt 2000) {
                $tail = $lines[($lines.Length - 400)..($lines.Length - 1)]
                [System.IO.File]::WriteAllLines($logPath, $tail, [System.Text.Encoding]::UTF8)
            }
        }
    } catch { }
}

# --- bounded helpers ---------------------------------------------------------

# Is anything listening on the port? Pure .NET: no CIM, no WMI, no child
# process, no module auto-load. This replaces Get-NetTCPConnection.
function Test-PortListening {
    param([int]$Port)
    try {
        $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        foreach ($l in $listeners) { if ($l.Port -eq $Port) { return $true } }
        return $false
    } catch {
        Write-WatchdogLog "GetActiveTcpListeners failed: $($_.Exception.Message); falling back to connect probe"
    }
    # Fallback: bounded loopback connect.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(5000, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch { return $false } finally { try { $client.Close() } catch { } }
}

# Owning PID via netstat, bounded. Only needed for the log line and for the
# foreign-vs-sidecar decision. Never kills the child: on timeout it is
# abandoned, so this function cannot stop anything.
function Get-PortOwningPid {
    param([int]$Port, [int]$TimeoutSec = 20)
    $stdout = Join-Path $env:TEMP ("melange_watchdog_netstat_{0}_{1}.txt" -f $PID, [DateTime]::UtcNow.Ticks)
    $stderr = "$stdout.err"
    try {
        $netstat = Join-Path $env:SystemRoot 'System32\NETSTAT.EXE'
        $proc = Start-Process -FilePath $netstat -ArgumentList '-ano', '-p', 'tcp' `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
            Write-WatchdogLog "netstat exceeded ${TimeoutSec}s; abandoning it (not killed)"
            return $null
        }
        foreach ($line in [System.IO.File]::ReadAllLines($stdout)) {
            if ($line -match '^\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
                if ([int]$Matches[2] -eq $Port) { return [int]$Matches[3] }
            }
        }
        return $null
    } catch {
        Write-WatchdogLog "netstat lookup failed: $($_.Exception.Message)"
        return $null
    } finally {
        try { [System.IO.File]::Delete($stdout) } catch { }
        try { [System.IO.File]::Delete($stderr) } catch { }
    }
}

# Run a scriptblock in its own runspace with a timeout. On timeout the runspace
# is deliberately abandoned rather than Stop()ped, because Stop() can itself
# block on a wedged native call; it dies with the process moments later.
function Invoke-BoundedScalar {
    param([scriptblock]$Script, [object[]]$Arguments = @(), [int]$TimeoutSec = 20, [string]$Label = 'op')
    try {
        $shell = [PowerShell]::Create()
        $null = $shell.AddScript($Script)
        foreach ($a in $Arguments) { $null = $shell.AddArgument($a) }
        $handle = $shell.BeginInvoke()
        if ($handle.AsyncWaitHandle.WaitOne($TimeoutSec * 1000, $false)) {
            $result = $shell.EndInvoke($handle)
            try { $shell.Dispose() } catch { }
            if ($result -and $result.Count -gt 0) { return $result[0] }
            return $null
        }
        Write-WatchdogLog "bounded op '$Label' exceeded ${TimeoutSec}s; abandoning"
        return $null
    } catch {
        Write-WatchdogLog "bounded op '$Label' threw: $($_.Exception.Message)"
        return $null
    }
}

# ---------------------------------------------------------------------------

try {
    # Step 1: is the port listening at all?
    $listening = Test-PortListening -Port $Port

    if (-not $listening) {
        Write-WatchdogLog "not listening; launching"
        $launcherPath = Join-Path $PSScriptRoot 'start_staffer_live.ps1'
        if ($DryRun) {
            Write-WatchdogLog "DRYRUN would run: & `"$launcherPath`" -SourceRoot `"$SourceRoot`" -Port $Port"
        } else {
            try {
                $output = & $launcherPath -SourceRoot $SourceRoot -Port $Port 2>&1 | Out-String
                Write-WatchdogLog "launcher output: $($output.Trim())"
            } catch {
                Write-WatchdogLog "launcher threw: $($_.Exception.Message)"
            }
        }
        Trim-WatchdogLog
        exit 0
    }

    # Step 2: something is listening; probe it directly, bypassing any proxy.
    $owningPid = Get-PortOwningPid -Port $Port -TimeoutSec ([Math]::Min(20, [Math]::Max(5, (Get-RemainingSec) - 30)))
    $owningPidText = if ($null -ne $owningPid) { $owningPid } else { 'unknown' }

    $healthy = $false
    $probeErrorMessage = $null

    # Keep the HTTP probe inside the overall budget so it cannot outlive us.
    $httpBudgetSec = [Math]::Min($ProbeTimeoutSec, [Math]::Max(5, (Get-RemainingSec) - 20))

    try {
        $url = "http://127.0.0.1:$Port/api/staffer-live?limit=5"
        $request = [System.Net.HttpWebRequest]::Create($url)
        $request.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy()
        $request.Timeout = $httpBudgetSec * 1000
        $request.ReadWriteTimeout = $httpBudgetSec * 1000
        $request.KeepAlive = $false
        $request.UserAgent = 'MelangeStafferLiveWatchdog/1.0'

        $response = $request.GetResponse()
        try {
            $stream = $response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $body = $reader.ReadToEnd()
            $reader.Close()
        } finally {
            $response.Close()
        }

        $json = $body | ConvertFrom-Json
        if ($json.schema -in @('melange-staffer-live-v1', 'melange-staffer-live-v2')) {
            Write-WatchdogLog "healthy pid=$owningPidText schema=$($json.schema) ready=$($json.ready) stage=$($json.stage)"
            $healthy = $true
        } else {
            $probeErrorMessage = "unexpected schema: $($json.schema)"
        }
    } catch {
        $probeErrorMessage = $_.Exception.Message
    }

    if ($healthy) {
        Trim-WatchdogLog
        exit 0
    }

    # Step 3: probe failed or returned an unrecognized schema.
    Write-WatchdogLog "probe error: $probeErrorMessage"

    if ($null -eq $owningPid) {
        Write-WatchdogLog "port $Port listening but owner unknown; not touching"
        Trim-WatchdogLog
        exit 2
    }

    $cmdLine = Invoke-BoundedScalar -Label 'Win32_Process cmdline' -TimeoutSec 20 -Arguments @($owningPid) -Script {
        param($targetPid)
        (Get-CimInstance Win32_Process -Filter "ProcessId = $targetPid" -ErrorAction SilentlyContinue).CommandLine
    }

    if (-not $cmdLine) {
        # Fall back to the launcher's own receipt rather than guessing.
        try {
            $serverJson = Join-Path $runtimeDirectory 'server.json'
            if ([System.IO.File]::Exists($serverJson)) {
                $receipt = [System.IO.File]::ReadAllText($serverJson) | ConvertFrom-Json
                if ($receipt.pid -eq $owningPid -and $receipt.port -eq $Port) {
                    Write-WatchdogLog "sidecar unresponsive (identified via server.json)"
                    Trim-WatchdogLog
                    exit 3
                }
            }
        } catch {
            Write-WatchdogLog "server.json fallback failed: $($_.Exception.Message)"
        }
    }

    if ($cmdLine -and $cmdLine -match 'staffer_live_server\.py') {
        Write-WatchdogLog "sidecar unresponsive"
        Trim-WatchdogLog
        exit 3
    } else {
        Write-WatchdogLog "port held by foreign process $owningPid; not touching"
        Trim-WatchdogLog
        exit 2
    }
} catch {
    try {
        Write-WatchdogLog "unexpected exception: $($_.Exception.Message)"
    } catch { }
    exit 1
}
