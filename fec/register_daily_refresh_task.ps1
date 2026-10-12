<#
Emit and validate two native Windows tasks; change registration only with -Register.

MoneyballCongressRefresh: first-use logon, unlock and observed Modern Standby
lid-open events. There is no clock trigger or scheduler retry that could turn a
background wake or a next-day retry into a new first-use date.
MoneyballDailyRefreshRetry: hourly/network-connected attempts; Python's retry
mode can only resume a day already recorded by a real first-use event.

Primary Microsoft references:
https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-logontrigger-triggergroup-element
https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-sessionstatechangetype-simpletype
https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertaskdefinition
https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-startwhenavailable

Machine evidence: PaulComputer System/Kernel-Power 507 on 2026-09-10 19:31
reports Reason=Lid, LidOpenState=true, IsCsSessionInProgressOnExit=false.
The selected fields exclude observed maintenance/background Modern Standby exits.
NetworkProfile/Operational 10000 on 2026-09-11 reports Network Connected.
Power-Troubleshooter 1 is deliberately excluded: it also sees unattended wakes.
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    [string]$UserSid = ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value),
    [string]$TaskName = 'MoneyballCongressRefresh',
    [string]$RetryTaskName = 'MoneyballDailyRefreshRetry',
    [string]$OutputDirectory,
    [datetime]$RetryStart = (Get-Date).AddHours(1),
    [switch]$SkipSchedulerValidation,
    [switch]$Register
)

$ErrorActionPreference = 'Stop'
if ($Register -and $SkipSchedulerValidation) {
    throw 'Registration requires Windows Task Scheduler validation.'
}
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if ($TaskName -eq $RetryTaskName) { throw 'First-use and retry task names must differ.' }
foreach ($refreshTaskName in @($TaskName, $RetryTaskName)) {
    if ([string]::IsNullOrWhiteSpace($refreshTaskName) -or $refreshTaskName -match '[\\/\r\n]') {
        throw 'Use a nonempty task name in the root Task Scheduler folder.'
    }
}
if ($UserSid -notmatch '^S-1-\d+(?:-\d+)+$') { throw 'A Windows user SID is required.' }
$refreshRoot = [IO.Path]::GetFullPath($Root)
$refreshPython = [IO.Path]::GetFullPath($PythonPath)
$refreshPythonWindowless = Join-Path (Split-Path -Parent $refreshPython) 'pythonw.exe'
$refreshEntry = Join-Path $refreshRoot 'fec\daily_refresh.py'
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $refreshRoot 'audits\daily_refresh_scheduler'
}
$refreshOutput = [IO.Path]::GetFullPath($OutputDirectory)

function Escape-Xml([string]$Value) {
    return [Security.SecurityElement]::Escape($Value)
}

function Quote-Argument([string]$Value) {
    if ($Value -match '["\r\n\x00]') { throw 'Invalid character in a task action argument.' }
    # These arguments are file paths or fixed enum values, never shell programs.
    return '"' + $Value + '"'
}

$lidSubscription = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name="Microsoft-Windows-Kernel-Power"] and (EventID=507)]] and *[EventData[Data[@Name="LidOpenState"]="true" and Data[@Name="IsCsSessionInProgressOnExit"]="false"]]</Select></Query></QueryList>'
$networkSubscription = '<QueryList><Query Id="0" Path="Microsoft-Windows-NetworkProfile/Operational"><Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[Provider[@Name="Microsoft-Windows-NetworkProfile"] and (EventID=10000)]]</Select></Query></QueryList>'
$sidXml = Escape-Xml $UserSid
$firstUseTriggers = @"
    <LogonTrigger id="first-sign-in">
      <Enabled>true</Enabled><UserId>$sidXml</UserId><Delay>PT30S</Delay>
    </LogonTrigger>
    <SessionStateChangeTrigger id="first-unlock">
      <Enabled>true</Enabled><Delay>PT30S</Delay><UserId>$sidXml</UserId><StateChange>SessionUnlock</StateChange>
    </SessionStateChangeTrigger>
    <EventTrigger id="first-lid-open">
      <Enabled>true</Enabled><Subscription>$(Escape-Xml $lidSubscription)</Subscription><Delay>PT30S</Delay>
    </EventTrigger>
"@
$retryBoundary = $RetryStart.ToString('yyyy-MM-ddTHH:mm:ss')
$retryTriggers = @"
    <CalendarTrigger id="hourly-retry">
      <StartBoundary>$retryBoundary</StartBoundary><Enabled>true</Enabled>
      <Repetition><Interval>PT1H</Interval><Duration>PT12H</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
    <EventTrigger id="network-retry">
      <Enabled>true</Enabled><Subscription>$(Escape-Xml $networkSubscription)</Subscription><Delay>PT30S</Delay>
    </EventTrigger>
"@

function New-RefreshXml([string]$Mode, [string]$TriggersXml) {
    # pythonw runs the standard-library daily gate without creating a console or
    # a PowerShell host. The gate starts sibling python.exe for logged children.
    $refreshArguments = '-u ' + (Quote-Argument $refreshEntry) + ' --trigger ' + $Mode +
        ' --root ' + (Quote-Argument $refreshRoot)
    # Catch-up belongs only to retry mode. It may not create a first-use date.
    $catchUp = if ($Mode -eq 'retry') { 'true' } else { 'false' }
    # BK-DR-e (2026-10-11): the retry task is bounded - 6 h per run, hourly for 12 h a day.
    $timeLimit = if ($Mode -eq 'retry') { 'PT6H' } else { 'PT0S' }
    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$sidXml</Author>
    <Description>Native local Python refresh ($Mode): bills, memberships, media, Excel and dashboard. Daily gate and shared writer lease are enforced by fec/daily_refresh.py.</Description>
  </RegistrationInfo>
  <Triggers>
$TriggersXml
  </Triggers>
  <Principals><Principal id="Author"><UserId>$sidXml</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>$catchUp</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>$timeLimit</ExecutionTimeLimit><Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>$(Escape-Xml $refreshPythonWindowless)</Command><Arguments>$(Escape-Xml $refreshArguments)</Arguments><WorkingDirectory>$(Escape-Xml $refreshRoot)</WorkingDirectory></Exec></Actions>
</Task>
"@
}

$definitions = @(
    [pscustomobject]@{ Name = $TaskName; Mode = 'first-use'; Xml = (New-RefreshXml 'first-use' $firstUseTriggers) },
    [pscustomobject]@{ Name = $RetryTaskName; Mode = 'retry'; Xml = (New-RefreshXml 'retry' $retryTriggers) }
)
[IO.Directory]::CreateDirectory($refreshOutput) | Out-Null
foreach ($definition in $definitions) {
    [xml]$parsed = $definition.Xml
    $xmlPath = Join-Path $refreshOutput ($definition.Name + '.xml')
    [IO.File]::WriteAllText($xmlPath, $parsed.OuterXml, [Text.Encoding]::Unicode)
    $definition | Add-Member -NotePropertyName XmlPath -NotePropertyValue $xmlPath
}

$schedulerFolder = $null
if (-not $SkipSchedulerValidation) {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $schedulerFolder = $scheduler.GetFolder('\')
    foreach ($definition in $definitions) {
        # TASK_VALIDATE_ONLY=1: checks full schema without creating/updating tasks.
        $schedulerFolder.RegisterTask($definition.Name, $definition.Xml, 1, $UserSid, $null, 3, $null) | Out-Null
    }
}

if ($Register) {
    foreach ($requiredPath in @($refreshEntry, $refreshPython, $refreshPythonWindowless)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Required runtime file missing: $requiredPath" }
    }
    foreach ($definition in $definitions) {
        try { $previous = $schedulerFolder.GetTask($definition.Name) } catch { $previous = $null }
        if ($null -ne $previous) {
            $backupPath = Join-Path $refreshOutput ($definition.Name + '.before.' + (Get-Date -Format 'yyyyMMddTHHmmssfff') + '.xml')
            [IO.File]::WriteAllText($backupPath, $previous.Xml, [Text.Encoding]::Unicode)
        }
    }
    foreach ($definition in $definitions) {
        # TASK_CREATE_OR_UPDATE=6, interactive current-user logon=3; no password.
        $schedulerFolder.RegisterTask($definition.Name, $definition.Xml, 6, $UserSid, $null, 3, $null) | Out-Null
    }
}

[pscustomobject]@{
    registered = [bool]$Register
    scheduler_validated = -not [bool]$SkipSchedulerValidation
    user_sid = $UserSid
    tasks = @($definitions | Select-Object Name, Mode, XmlPath)
} | ConvertTo-Json -Depth 4
