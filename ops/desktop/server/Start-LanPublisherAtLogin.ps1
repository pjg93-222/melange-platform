[CmdletBinding(DefaultParameterSetName = 'Lan')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Lan')][string]$ListenIPAddress,
    [Parameter(Mandatory = $true, ParameterSetName = 'Lan')][string]$ExpectedNetworkName,
    # Internet mode behind a local TCP pass-through relay (Tailscale Funnel): listens on 127.0.0.1 only.
    [Parameter(Mandatory = $true, ParameterSetName = 'Public')][string]$PublicOrigin,
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$StateDirectory,
    [ValidateRange(1024, 65535)][int]$Port = 38443
)
$ErrorActionPreference = 'Stop'
if ($PublicOrigin -and $Port -ne 38443) {
    throw 'PublicOrigin mode listens on 127.0.0.1:38443, the only local port relay_tunnel.py forwards to.'
}
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'The publisher supervisor requires PowerShell 7 or later.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'The login publisher must run without elevation.'
}
$ownedMutex = $false
$mutex = [Threading.Mutex]::new($false, ('Local\MelangeDesktopPublisher-' + $identity.User.Value))
$publisherProcess = $null
$notifierProcess = $null
$publisherJob = $null
$stateDirectory = (Get-Item -LiteralPath $StateDirectory -ErrorAction Stop).FullName
$statusFile = Join-Path $stateDirectory 'listener-status.json'
$launchScript = Join-Path $PSScriptRoot 'Start-LanPublisher.ps1'
$serverScript = Join-Path $PSScriptRoot 'desktop_admin.py'
$notifierScript = Join-Path $PSScriptRoot 'Start-AccessNotifier.ps1'
if (-not (Test-Path -LiteralPath (Join-Path $stateDirectory 'trust.json'))) {
    throw 'Initialize the protected publisher state before enabling login startup.'
}
$serverArguments = @($serverScript, '--state', $stateDirectory)
if ($PublicOrigin) {
    # desktop_admin.py applies the https_origin rules; the origin must already be canonical and a DNS name.
    $descriptor = (& $PythonExecutable $serverScript --state $stateDirectory --public-origin $PublicOrigin access-trust --url $PublicOrigin) -join "`n"
    if ($LASTEXITCODE -ne 0 -or ($descriptor | ConvertFrom-Json).url -cne $PublicOrigin -or ([Uri]$PublicOrigin).HostNameType -ne 'Dns') {
        throw 'PublicOrigin must be a canonical https DNS origin accepted by desktop_admin.py, with application access initialized.'
    }
    $ListenIPAddress = '127.0.0.1'
    $serverArguments += @('--public-origin', $PublicOrigin)
}
$serverArguments += @('serve', '--listen', $ListenIPAddress, '--port', [string]$Port)
function Get-InviteRequired {
    # Same values as desktop_admin.py invite_env(); the publisher child inherits this environment. $null = unreadable value, so the publisher refuses to start.
    $raw = ([string]$env:MELANGE_ACCESS_REQUIRE_INVITE).Trim().ToLowerInvariant()
    if ($raw -in '1', 'true', 'yes', 'on') { return $true }
    if ($raw -in '', '0', 'false', 'no', 'off') { return $false }
    return $null
}
function Write-ListenerStatus([string]$Status) {
    # Fixed status values only: never write enrollment codes, credentials or request data.
    # Start-AccessNotifier.ps1 writes no status file of its own; this is its only liveness record.
    $record = [ordered]@{ status = $Status; checkedAt = [DateTime]::UtcNow.ToString('o'); supervisorPid = $PID; publisherPid = if ($publisherProcess) { $publisherProcess.Id } else { $null }
        notifierPid = if ($notifierProcess) { $notifierProcess.Id } else { $null }; inviteRequired = Get-InviteRequired }
    $temporary = $statusFile + '.' + $PID + '.tmp'
    [IO.File]::WriteAllText($temporary, ($record | ConvertTo-Json))
    [IO.File]::Move($temporary, $statusFile, $true)
}
function Start-AccessNotifierProcess([string]$Executable) {
    # A failed tray must not end this loop: the supervisor's exit closes the job and takes the
    # publisher down with it. Return $null so the status records notifierPid null and the next pass retries.
    $process = $null
    try {
        $notifierStart = [Diagnostics.ProcessStartInfo]::new()
        $notifierStart.FileName = $Executable
        $notifierStart.UseShellExecute = $false
        $notifierStart.CreateNoWindow = $true
        foreach ($argument in @('-NoProfile','-NonInteractive','-STA','-WindowStyle','Hidden','-File', $notifierScript, '-PythonExecutable',$PythonExecutable,'-StateDirectory',$stateDirectory)) {
            $notifierStart.ArgumentList.Add($argument)
        }
        $process = [Diagnostics.Process]::Start($notifierStart)
        [MelangePublisherJob]::Assign($publisherJob, $process)
        return $process
    } catch {
        # A started notifier outside the job would outlive the supervisor; stop it before retrying.
        if ($process) {
            try { $process.Kill($true) } catch { }
            $process.Dispose()
        }
        return $null
    }
}
try {
    try { $ownedMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownedMutex = $true }
    if (-not $ownedMutex) { exit 0 }
    # Closing this OS handle kills only children explicitly assigned to this job,
    # including when the supervisor crashes or is terminated without its finally block.
    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class MelangePublisherJob {
    [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters {
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetInformationJobObject(SafeFileHandle job, int informationClass, ref ExtendedLimits limits, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);
    public static SafeFileHandle Create() {
        var job=CreateJobObject(IntPtr.Zero,null);
        if(job.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
        var limits=new ExtendedLimits(); limits.Basic.Flags=0x2000;
        if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf<ExtendedLimits>())) {
            var error=Marshal.GetLastWin32Error(); job.Dispose(); throw new Win32Exception(error);
        }
        return job;
    }
    public static void Assign(SafeFileHandle job, Process process) {
        if(!AssignProcessToJobObject(job,process.Handle)) throw new Win32Exception(Marshal.GetLastWin32Error());
    }
}
'@
    $publisherJob = [MelangePublisherJob]::Create()
    while ($true) {
        # The local tray works independently of LAN reachability and app windows.
        # Its own SID mutex prevents duplicate notifiers after supervisor restart.
        if ($notifierProcess -and $notifierProcess.HasExited) {
            $notifierProcess.Dispose(); $notifierProcess = $null
        }
        if (-not $notifierProcess -and (Test-Path -LiteralPath (Join-Path $stateDirectory 'access.sqlite3'))) {
            $notifierProcess = Start-AccessNotifierProcess (Get-Process -Id $PID).Path
        }
        $networkReady = $false
        try {
            # Loopback relay mode has no LAN adapter or inbound firewall rule to validate.
            if (-not $PublicOrigin) {
                & $launchScript -ListenIPAddress $ListenIPAddress -ExpectedNetworkName $ExpectedNetworkName `
                    -PythonExecutable $PythonExecutable -StateDirectory $stateDirectory -Port $Port -ConfigureOnly | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'Network validation failed.' }
            }
            $networkReady = $true
        } catch {
            $networkReady = $false
        }
        if ($publisherProcess -and $publisherProcess.HasExited) {
            $publisherProcess.Dispose()
            $publisherProcess = $null
        }
        if (-not $networkReady) {
            if ($publisherProcess) {
                $publisherProcess.Kill($true)
                $publisherProcess.WaitForExit()
                $publisherProcess.Dispose()
                $publisherProcess = $null
            }
            Write-ListenerStatus 'waiting-for-approved-lan'
        } elseif (-not $publisherProcess) {
            $start = [Diagnostics.ProcessStartInfo]::new()
            $start.FileName = $PythonExecutable
            $start.UseShellExecute = $false
            $start.CreateNoWindow = $true
            foreach ($argument in $serverArguments) {
                $start.ArgumentList.Add($argument)
            }
            $publisherProcess = [Diagnostics.Process]::Start($start)
            [MelangePublisherJob]::Assign($publisherJob, $publisherProcess)
            Write-ListenerStatus 'starting'
        } else {
            Write-ListenerStatus 'running'
        }
        Start-Sleep -Seconds 30
    }
} finally {
    if ($publisherJob) { $publisherJob.Dispose() }
    if ($publisherProcess -and -not $publisherProcess.HasExited) {
        $publisherProcess.Kill($true)
        $publisherProcess.WaitForExit()
    }
    if ($publisherProcess) { $publisherProcess.Dispose() }
    if ($notifierProcess) { $notifierProcess.Dispose() }
    if ($ownedMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
