param(
    [int]$WaitForProcessId = 0,
    [int]$RetryDelaySeconds = 60
)

$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
$Pipeline = Join-Path $PSScriptRoot "run_legislation_coverage_pipeline.ps1"
$Python = "C:\Users\pgoff\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$TimeoutRollover = Join-Path $PSScriptRoot "legislation\run_legislation_timeout_rollover.py"
$AuditDir = Join-Path $Workspace "audits\legfix5"
$Log = Join-Path $AuditDir "specificity_followup_pipeline.log"
$Receipt = Join-Path $AuditDir "specificity_followup_receipt.json"
$script:LastTimeoutRolloverExit = $null

Set-Location -LiteralPath $Workspace

function Invoke-ValidatedTimeoutRollover {
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        # The rollover helper is local and zero-network. A blocked pre-sync
        # checkpoint is its expected safe state; only its process exit is
        # authoritative here.
        $ErrorActionPreference = "Continue"
        & $Python $TimeoutRollover "--check" 2>&1 |
            ForEach-Object { Write-Output $_ }
        $script:LastTimeoutRolloverExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($script:LastTimeoutRolloverExit -ne 0) {
        Write-Output (
            "TIMEOUT ROLLOVER BLOCKED: exit=" +
            $script:LastTimeoutRolloverExit
        )
    }
}

$waitStartedAt = [DateTimeOffset]::UtcNow
$target = if ($WaitForProcessId -gt 0) {
    Get-Process -Id $WaitForProcessId -ErrorAction SilentlyContinue
}
else {
    $null
}
$targetStartTime = if ($target) {
    $target.StartTime.ToUniversalTime().ToString("o")
}
else {
    $null
}

while ($WaitForProcessId -gt 0) {
    $current = Get-Process -Id $WaitForProcessId -ErrorAction SilentlyContinue
    if ($null -eq $current) {
        break
    }
    if (
        $targetStartTime -and
        $current.StartTime.ToUniversalTime().ToString("o") -ne $targetStartTime
    ) {
        break
    }
    # Poll the validated strict-manifest selector while the long-running
    # coverage supervisor is alive. Receipt-identical polls are idempotent;
    # each new capped timeout rolls current staged audit bindings immediately.
    Invoke-ValidatedTimeoutRollover *>> $Log
    Start-Sleep -Seconds 30
}

# Close a timeout that may have landed between the last poll and process exit.
Invoke-ValidatedTimeoutRollover *>> $Log
if ($script:LastTimeoutRolloverExit -ne 0) {
    throw "Validated timeout rollover failed closed"
}

$pipelineStartedAt = [DateTimeOffset]::UtcNow
$priorErrorActionPreference = $ErrorActionPreference
try {
    # The child pipeline intentionally emits failed native-collector attempts
    # before retrying them. Windows PowerShell promotes redirected native
    # stderr to ErrorRecord objects; with the wrapper's fail-fast preference
    # those records used to terminate this wrapper before the child's retry
    # loop could run. Keep the child exit code authoritative instead.
    $ErrorActionPreference = "Continue"
    & "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $Pipeline `
        -MaxAttempts 240 `
        -RetryDelaySeconds $RetryDelaySeconds *>> $Log
    $pipelineExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $priorErrorActionPreference
}

$receiptBody = [ordered]@{
    schema = "acu-legislation-specificity-followup-v1"
    waited_for_process_id = $WaitForProcessId
    waited_for_process_start_time = $targetStartTime
    wait_started_at = $waitStartedAt.ToString("o")
    pipeline_started_at = $pipelineStartedAt.ToString("o")
    completed_at = [DateTimeOffset]::UtcNow.ToString("o")
    pipeline_exit_code = $pipelineExitCode
    pipeline_log = "audits/legfix5/specificity_followup_pipeline.log"
    timeout_rollover_helper = "fec/legislation/run_legislation_timeout_rollover.py"
    timeout_rollover_last_exit_code = $script:LastTimeoutRolloverExit
    timeout_rollover_polling_while_waiting = $true
    purpose = (
        "Rebuild the Act-specific registry, collect isolated title-alias " +
        "probes, invalidate stale text-matching artifacts, and resume the " +
        "complete Congress.gov coverage pipeline after the active run exits."
    )
}
$receiptBody |
    ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $Receipt -Encoding UTF8

exit $pipelineExitCode
