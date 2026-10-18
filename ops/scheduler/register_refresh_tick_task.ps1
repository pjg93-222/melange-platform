<#
Register the ONE scheduled refresh orchestrator: task 'MelangeRefreshTick' runs fec\refresh_tick.py.

Owner ruling 2026-10-15: fec\refresh_tick.py is the single scheduled orchestrator, fired hourly,
at logon and on wake. fec\daily_refresh.py and fec\weekly_refresh.py stay in the repo as MANUAL
wrappers; the tasks that used to launch them are disabled here, never deleted, and their previous
state is reported so it can be put back by hand.

Nothing changes on this machine unless -Register or -Unregister is given. Without either, the
script only writes the task XML next to its output directory and validates it against Windows Task
Scheduler (TASK_VALIDATE_ONLY: nothing is created or updated).

  .\ops\scheduler\register_refresh_tick_task.ps1                    emit + validate only
  .\ops\scheduler\register_refresh_tick_task.ps1 -Register          create or update MelangeRefreshTick
  .\ops\scheduler\register_refresh_tick_task.ps1 -Register -DisableCongressRefresh -DisableWeeklyRefresh -DisableDailyRetry
                                                                   ... and make those wrappers manual
  .\ops\scheduler\register_refresh_tick_task.ps1 -Unregister       remove MelangeRefreshTick

Idempotent: -Register is CREATE_OR_UPDATE (the previous definition is saved as XML first), a task
that is already disabled or already absent is reported and left alone, and -Unregister of a task
that does not exist is not an error. The disable switches act only together with -Register and
never re-enable anything: -Unregister does not touch the wrappers.

The task: three triggers -- hourly (a TimeTrigger whose repetition has no Duration, so it repeats
indefinitely), at logon, and on resume from sleep (System/Kernel-Power 507, the same lid-open
selection register_daily_refresh_task.ps1 validated on this machine). MultipleInstancesPolicy
IgnoreNew, StartWhenAvailable, ExecutionTimeLimit PT0S (no limit: a tick bounds itself with its
own 18 h budget and the single-instance lock). It runs pythonw.exe so no console window appears;
refresh_tick.py starts its producers under the sibling python.exe.

Primary Microsoft references:
https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-repetition-triggerbasetype-element
https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertaskdefinition
https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-startwhenavailable
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    [string]$UserSid = ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value),
    [string]$TaskName = 'MelangeRefreshTick',
    [string]$OutputDirectory,
    [datetime]$FirstRun = (Get-Date).AddMinutes(5),
    [switch]$SkipSchedulerValidation,
    [switch]$Register,
    [switch]$Unregister,
    [switch]$DisableCongressRefresh,
    [switch]$DisableWeeklyRefresh,
    [switch]$DisableDailyRetry
)

$ErrorActionPreference = 'Stop'

# The wrapper tasks that become manual, by the switch that disables each.
$legacyTasks = @(
    [pscustomobject]@{ Name = 'MoneyballCongressRefresh'; Disable = [bool]$DisableCongressRefresh },
    [pscustomobject]@{ Name = 'MoneyballWeeklyRefresh'; Disable = [bool]$DisableWeeklyRefresh },
    [pscustomobject]@{ Name = 'MoneyballDailyRefreshRetry'; Disable = [bool]$DisableDailyRetry }
)

if ($Register -and $Unregister) { throw '-Register and -Unregister are mutually exclusive.' }
if (($Register -or $Unregister) -and $SkipSchedulerValidation) {
    throw 'Registration or removal requires Windows Task Scheduler validation.'
}
if (-not $Register) {
    foreach ($legacy in $legacyTasks) {
        if ($legacy.Disable) { throw "Disabling $($legacy.Name) needs -Register as well; nothing was changed." }
    }
}
if ([string]::IsNullOrWhiteSpace($TaskName) -or $TaskName -match '[\\/\r\n]') {
    throw 'Use a nonempty task name in the root Task Scheduler folder.'
}
if ($legacyTasks.Name -contains $TaskName) { throw 'The tick task may not share a name with a wrapper task.' }
if ($UserSid -notmatch '^S-1-\d+(?:-\d+)+$') { throw 'A Windows user SID is required.' }

if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$tickRoot = [IO.Path]::GetFullPath($Root)
$tickPython = [IO.Path]::GetFullPath($PythonPath)
$tickPythonWindowless = Join-Path (Split-Path -Parent $tickPython) 'pythonw.exe'
$tickEntry = Join-Path $tickRoot 'fec\refresh_tick.py'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $tickRoot 'audits\refresh_tick_scheduler' }
$tickOutput = [IO.Path]::GetFullPath($OutputDirectory)

function Escape-Xml([string]$Value) {
    return [Security.SecurityElement]::Escape($Value)
}

function Quote-Argument([string]$Value) {
    if ($Value -match '["\r\n\x00]') { throw 'Invalid character in a task action argument.' }
    # These arguments are file paths, never shell programs.
    return '"' + $Value + '"'
}

# System/Kernel-Power 507 with the lid open and no Connected Standby session: a real resume, not an
# unattended maintenance wake. Same selection fec\register_daily_refresh_task.ps1 validated.
$resumeSubscription = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name="Microsoft-Windows-Kernel-Power"] and (EventID=507)]] and *[EventData[Data[@Name="LidOpenState"]="true" and Data[@Name="IsCsSessionInProgressOnExit"]="false"]]</Select></Query></QueryList>'
$sidXml = Escape-Xml $UserSid
$firstBoundary = $FirstRun.ToString('yyyy-MM-ddTHH:mm:ss')
$triggersXml = @"
    <TimeTrigger id="hourly">
      <StartBoundary>$firstBoundary</StartBoundary><Enabled>true</Enabled>
      <Repetition><Interval>PT1H</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </TimeTrigger>
    <LogonTrigger id="at-logon">
      <Enabled>true</Enabled><UserId>$sidXml</UserId><Delay>PT1M</Delay>
    </LogonTrigger>
    <EventTrigger id="on-resume">
      <Enabled>true</Enabled><Subscription>$(Escape-Xml $resumeSubscription)</Subscription><Delay>PT30S</Delay>
    </EventTrigger>
"@
$tickArguments = '-u ' + (Quote-Argument $tickEntry)
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$sidXml</Author>
    <Description>The single scheduled refresh orchestrator (hourly, at logon, on wake): runs what is due, degrades with labels, publishes when the contract passes. fec/refresh_tick.py holds the single-instance lock and the shared writer lease.</Description>
  </RegistrationInfo>
  <Triggers>
$triggersXml
  </Triggers>
  <Principals><Principal id="Author"><UserId>$sidXml</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>$(Escape-Xml $tickPythonWindowless)</Command><Arguments>$(Escape-Xml $tickArguments)</Arguments><WorkingDirectory>$(Escape-Xml $tickRoot)</WorkingDirectory></Exec></Actions>
</Task>
"@

[IO.Directory]::CreateDirectory($tickOutput) | Out-Null
[xml]$parsed = $taskXml
$xmlPath = Join-Path $tickOutput ($TaskName + '.xml')
[IO.File]::WriteAllText($xmlPath, $parsed.OuterXml, [Text.Encoding]::Unicode)

$schedulerFolder = $null
if (-not $SkipSchedulerValidation) {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $schedulerFolder = $scheduler.GetFolder('\')
    # TASK_VALIDATE_ONLY=1: checks the full schema without creating or updating anything.
    $schedulerFolder.RegisterTask($TaskName, $taskXml, 1, $UserSid, $null, 3, $null) | Out-Null
}

function Get-TaskOrNull($Folder, [string]$Name) {
    try { return $Folder.GetTask($Name) } catch { return $null }
}

$report = [ordered]@{
    registered = $false
    unregistered = $false
    task = $TaskName
    xml = $xmlPath
    scheduler_validated = -not [bool]$SkipSchedulerValidation
    legacy = @()
}

if ($Register) {
    foreach ($requiredPath in @($tickEntry, $tickPython, $tickPythonWindowless)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Required runtime file missing: $requiredPath" }
    }
    $previous = Get-TaskOrNull $schedulerFolder $TaskName
    if ($null -ne $previous) {
        $backupPath = Join-Path $tickOutput ($TaskName + '.before.' + (Get-Date -Format 'yyyyMMddTHHmmssfff') + '.xml')
        [IO.File]::WriteAllText($backupPath, $previous.Xml, [Text.Encoding]::Unicode)
    }
    # TASK_CREATE_OR_UPDATE=6, interactive current-user logon=3; no password.
    $schedulerFolder.RegisterTask($TaskName, $taskXml, 6, $UserSid, $null, 3, $null) | Out-Null
    $report.registered = $true
}

if ($Unregister) {
    $existing = Get-TaskOrNull $schedulerFolder $TaskName
    if ($null -ne $existing) {
        $backupPath = Join-Path $tickOutput ($TaskName + '.removed.' + (Get-Date -Format 'yyyyMMddTHHmmssfff') + '.xml')
        [IO.File]::WriteAllText($backupPath, $existing.Xml, [Text.Encoding]::Unicode)
        $schedulerFolder.DeleteTask($TaskName, 0)
        $report.unregistered = $true
    }
}

if ($Register) {
    foreach ($legacy in $legacyTasks) {
        $entry = [ordered]@{ name = $legacy.Name; requested = $legacy.Disable; found = $false; was_enabled = $null; now_enabled = $null }
        $existing = Get-TaskOrNull $schedulerFolder $legacy.Name
        if ($null -ne $existing) {
            $entry.found = $true
            $entry.was_enabled = [bool]$existing.Enabled
            $entry.now_enabled = [bool]$existing.Enabled
            if ($legacy.Disable -and $existing.Enabled) {
                $backupPath = Join-Path $tickOutput ($legacy.Name + '.before-disable.' + (Get-Date -Format 'yyyyMMddTHHmmssfff') + '.xml')
                [IO.File]::WriteAllText($backupPath, $existing.Xml, [Text.Encoding]::Unicode)
                $existing.Enabled = $false
                $entry.now_enabled = $false
            }
        }
        $report.legacy += [pscustomobject]$entry
    }
}

[pscustomobject]$report | ConvertTo-Json -Depth 5
