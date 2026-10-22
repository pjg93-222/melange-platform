[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PythonExecutable,
    [Parameter(Mandatory=$true)][string]$StateDirectory,
    [ValidateRange(0,60)][int]$ExitAfterSeconds = 0,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7 -or [Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') { throw 'PowerShell 7 STA is required.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run the administrator notifier as the ordinary Windows user.' }
$sid = $identity.User.Value
$state = (Get-Item -LiteralPath $StateDirectory).FullName
$python = (Get-Item -LiteralPath $PythonExecutable).FullName
$helper = Join-Path $PSScriptRoot 'desktop_admin.py'
function Assert-ProtectedState {
    foreach ($path in @($state, (Join-Path $state 'access.sqlite3'), (Join-Path $state 'access-trust.json'), (Join-Path $state 'keys'))) {
        $item = Get-Item -LiteralPath $path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Protected state cannot redirect.' }
        $acl = Get-Acl -LiteralPath $path
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @($sid,'S-1-5-18')) { throw 'Protected state owner mismatch.' }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and $rule.IdentityReference.Value -notin @($sid,'S-1-5-18')) { throw 'Protected state grants access to another identity.' }
        }
    }
}
Assert-ProtectedState
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if ($ValidateOnly) { '{"validated":true,"channel":"windows-desktop","ordinaryUser":true,"protectedState":true}'; return }
$mutex = [Threading.Mutex]::new($false, ('Local\MelangeAccessNotifier-' + $sid))
$owned = $false
$script:pending = $null
$script:pendingSince = $null
$script:pendingAction = $null
$script:nextClaim = [DateTime]::UtcNow
$started = [DateTime]::UtcNow
$tray = [Windows.Forms.NotifyIcon]::new()
$timer = [Windows.Forms.Timer]::new()
$menu = [Windows.Forms.ContextMenuStrip]::new()
$script:handoffs = 0
function Start-FixedHelper([string[]]$Arguments, [bool]$Capture = $true) {
    Assert-ProtectedState
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $python
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $Capture
    $info.RedirectStandardError = $Capture
    foreach ($argument in @($helper,'--state',$state) + $Arguments) { $info.ArgumentList.Add($argument) }
    return [Diagnostics.Process]::Start($info)
}
function Open-LocalDashboard {
    # This action has no data-derived URL, argument or authority. A fresh local
    # process creates the one-use bootstrap and opens it without logging it.
    # Every click runs the helper; it reopens an already-running dashboard itself.
    $dashboard = Start-FixedHelper @('access-dashboard') $false
    $dashboard.Dispose()
}
function Start-Acknowledge([string]$Delivery, [string]$Outcome) {
    $script:pending = Start-FixedHelper @('notification-ack','--delivery',$Delivery,'--outcome',$Outcome)
    $script:pendingSince = [DateTime]::UtcNow
    $script:pendingAction = 'ack'
}
try {
    try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { exit 0 }
    $tray.Icon = [Drawing.SystemIcons]::Information
    $tray.Text = 'Application access requests'
    $tray.Visible = $true
    $open = $menu.Items.Add('Open access dashboard')
    $open.Add_Click({ try { Open-LocalDashboard } catch {} })
    $tray.ContextMenuStrip = $menu
    $tray.Add_DoubleClick({ try { Open-LocalDashboard } catch {} })
    $tray.Add_BalloonTipClicked({ try { Open-LocalDashboard } catch {} })
    $timer.Interval = 250
    $timer.Add_Tick({
        try {
            if ($ExitAfterSeconds -and ([DateTime]::UtcNow-$started).TotalSeconds -ge $ExitAfterSeconds) { [Windows.Forms.Application]::ExitThread(); return }
            if ($script:pending) {
                if (-not $script:pending.HasExited) {
                    if (([DateTime]::UtcNow-$script:pendingSince).TotalSeconds -ge 10) {
                        $script:pending.Kill($true); $script:pending.WaitForExit(); $script:pending.Dispose(); $script:pending=$null
                        $script:nextClaim=[DateTime]::UtcNow.AddSeconds(30)
                    }
                    return
                }
                $process=$script:pending; $action=$script:pendingAction; $script:pending=$null
                try {
                    $output=$process.StandardOutput.ReadToEnd(); $null=$process.StandardError.ReadToEnd()
                    if ($process.ExitCode -ne 0 -or $output.Length -gt 2048) { return }
                    $value=$output | ConvertFrom-Json
                    if ($action -eq 'claim' -and $value.state -ceq 'claimed') {
                        if (($value.PSObject.Properties.Name | Sort-Object) -join ',' -cne 'deliveryId,requestCount,state' -or $value.deliveryId -cnotmatch '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' -or $value.requestCount -isnot [long] -and $value.requestCount -isnot [int] -or $value.requestCount -lt 1 -or $value.requestCount -gt 100) { return }
                        $outcome='deferred'
                        try {
                            $tray.ShowBalloonTip(8000,'Application access review',([string]$value.requestCount+' request(s) await your review. Open the access dashboard to approve or deny.'),[Windows.Forms.ToolTipIcon]::Info)
                            $outcome='handed_to_windows'; $script:handoffs++
                        } catch {}
                        Start-Acknowledge $value.deliveryId $outcome
                    }
                } finally { $process.Dispose() }
            } elseif ([DateTime]::UtcNow -ge $script:nextClaim) {
                $script:nextClaim=[DateTime]::UtcNow.AddSeconds(30)
                $script:pending=Start-FixedHelper @('notification-claim')
                $script:pendingSince=[DateTime]::UtcNow; $script:pendingAction='claim'
            }
        } catch { $script:nextClaim=[DateTime]::UtcNow.AddSeconds(30) }
    })
    $timer.Start()
    [Windows.Forms.Application]::Run()
    if ($ExitAfterSeconds) { @{ channel='windows-desktop'; handedToWindows=$script:handoffs; seen=$false } | ConvertTo-Json -Compress }
} finally {
    $timer.Stop(); $timer.Dispose(); $tray.Visible=$false; $tray.Dispose(); $menu.Dispose()
    if ($script:pending) { if (-not $script:pending.HasExited) { $script:pending.Kill($true); $script:pending.WaitForExit() }; $script:pending.Dispose() }
    if ($owned) { $mutex.ReleaseMutex() }; $mutex.Dispose()
}
