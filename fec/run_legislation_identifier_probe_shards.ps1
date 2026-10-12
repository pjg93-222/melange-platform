param(
    [string]$Plan = "audits\legfix5\identifier_probe_parallel_20260726\shard_plan.json",
    [int]$MaxAttempts = 8,
    [int]$RetryDelaySeconds = 300,
    [switch]$SkipConsolidation
)

$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
$Node = "C:\Users\pgoff\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$Python = "C:\Users\pgoff\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:NODE_PATH = "C:\Users\pgoff\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
Set-Location -LiteralPath $Workspace

function Get-WorkspacePath {
    param([string]$Value)
    if ([System.IO.Path]::IsPathRooted($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $Workspace $Value))
}

function Assert-PlanBinding {
    param([string]$Path)
    $sidecar = "${Path}.sha256"
    if (-not (Test-Path -LiteralPath $Path) -or -not (Test-Path -LiteralPath $sidecar)) {
        throw "Shard plan or SHA-256 sidecar is missing"
    }
    $expected = ((Get-Content -LiteralPath $sidecar -Raw).Trim() -split "\s+")[0]
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -ne $actual) {
        throw "Shard plan SHA-256 binding failed"
    }
}

function Get-NotBefore {
    param($PlanDocument)
    $notBefore = $null
    if ($PlanDocument.global_cooldown.next_retry_not_before) {
        $notBefore = [DateTimeOffset]::Parse(
            $PlanDocument.global_cooldown.next_retry_not_before
        )
    }
    $cooldownPath = Get-WorkspacePath $PlanDocument.global_cooldown.path
    if (Test-Path -LiteralPath $cooldownPath) {
        $live = Get-Content -LiteralPath $cooldownPath -Raw | ConvertFrom-Json
        if (
            $live.schema -ne "congress-automatic-verification-cooldown-v1" -or
            $live.status -ne "active" -or
            -not $live.next_retry_not_before -or
            $live.policy.automatic_refresh_or_reload_during_cooldown -ne $false -or
            $live.policy.captcha_bypass_allowed -ne $false
        ) {
            throw "Live global cooldown state is invalid"
        }
        $liveNotBefore = [DateTimeOffset]::Parse($live.next_retry_not_before)
        if ($null -eq $notBefore -or $liveNotBefore -gt $notBefore) {
            $notBefore = $liveNotBefore
        }
    }
    return $notBefore
}

function Wait-GlobalCooldown {
    param($PlanDocument)
    $notBefore = Get-NotBefore $PlanDocument
    while ($null -ne $notBefore -and [DateTimeOffset]::UtcNow -lt $notBefore) {
        $remaining = [Math]::Ceiling(
            ($notBefore - [DateTimeOffset]::UtcNow).TotalSeconds
        )
        Write-Output "GLOBAL COOLDOWN: no request before $($notBefore.ToString('o')); ${remaining}s remain"
        Start-Sleep -Seconds ([Math]::Min(30, [Math]::Max(1, $remaining)))
        $notBefore = Get-NotBefore $PlanDocument
    }
}

function Assert-NoLegacyCollector {
    param($PlanDocument)
    $sourceRun = Get-WorkspacePath $PlanDocument.preserved.source_run
    $lockPath = Join-Path $sourceRun "collector.lock.json"
    if (-not (Test-Path -LiteralPath $lockPath)) {
        return
    }
    $lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
    $process = Get-Process -Id $lock.pid -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        throw "Legacy collector PID $($lock.pid) is still active; refusing duplicate harvest"
    }
}

function Test-ShardComplete {
    param($Shard, $PlanDocument)
    $manifestPath = Join-Path (Get-WorkspacePath $Shard.output_dir) "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        return $false
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $observed = @($manifest.selected_query_ids)
        $expected = @($Shard.query_ids)
        if (
            $manifest.status -ne "complete" -or
            $manifest.registry_sha256 -ne $PlanDocument.registry.sha256 -or
            $observed.Count -ne $expected.Count
        ) {
            return $false
        }
        for ($index = 0; $index -lt $expected.Count; $index += 1) {
            if ($observed[$index] -ne $expected[$index]) {
                return $false
            }
        }
        return $true
    }
    catch {
        return $false
    }
}

$planPath = Get-WorkspacePath $Plan
Assert-PlanBinding $planPath
$document = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if (
    $document.schema -ne "acu-legislation-identifier-shard-plan-v1" -or
    $document.partition.registry_query_count -ne 210 -or
    $document.partition.disjoint -ne $true -or
    $document.partition.complete -ne $true
) {
    throw "Shard plan does not certify an exact disjoint 210-query partition"
}
$registryPath = Get-WorkspacePath $document.registry.path
$registryHash = (Get-FileHash -LiteralPath $registryPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($registryHash -ne $document.registry.sha256) {
    throw "Identifier registry changed after shard planning"
}
$profiles = @($document.shards | ForEach-Object { $_.profile_dir })
$outputs = @($document.shards | ForEach-Object { $_.output_dir })
if (
    @($profiles | Select-Object -Unique).Count -ne $profiles.Count -or
    @($outputs | Select-Object -Unique).Count -ne $outputs.Count
) {
    throw "Every shard must use a distinct output directory and browser profile"
}

Assert-NoLegacyCollector $document
Wait-GlobalCooldown $document
Assert-NoLegacyCollector $document

for ($attempt = 1; $attempt -le $MaxAttempts; $attempt += 1) {
    $running = @()
    foreach ($shard in $document.shards) {
        if (Test-ShardComplete $shard $document) {
            Write-Output "SKIP COMPLETE: $($shard.shard_id)"
            continue
        }
        $out = Get-WorkspacePath $shard.output_dir
        $logDir = Join-Path $out "launcher_logs"
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $categories = (@($shard.query_ids) -join ",")
        $arguments = @(
            "fec\collect\collect_congress_advanced_pages.mjs",
            "--registry", $registryPath,
            "--out", $out,
            "--profile", $shard.profile_dir,
            "--categories", $categories,
            "--delay-ms", [string]$shard.delay_ms,
            "--headless",
            "--resume"
        )
        # Windows PowerShell joins ArgumentList values into one command line.
        # Quote every token so workspace paths containing spaces remain single
        # Node arguments; registry query IDs cannot contain quote characters.
        $quotedArguments = @(
            $arguments | ForEach-Object {
                if ([string]$_ -match '"') {
                    throw "Collector argument contains an unsupported quote"
                }
                '"' + [string]$_ + '"'
            }
        )
        $stdout = Join-Path $logDir ("attempt-{0:00}.stdout.log" -f $attempt)
        $stderr = Join-Path $logDir ("attempt-{0:00}.stderr.log" -f $attempt)
        Write-Output "START: $($shard.shard_id) attempt $attempt/$MaxAttempts"
        $process = Start-Process -FilePath $Node -ArgumentList $quotedArguments `
            -WorkingDirectory $Workspace -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $running += [pscustomobject]@{
            Shard = $shard
            Process = $process
        }
        Start-Sleep -Milliseconds 1500
    }
    foreach ($item in $running) {
        $item.Process.WaitForExit()
        Write-Output "EXIT: $($item.Shard.shard_id) code=$($item.Process.ExitCode)"
    }
    $incomplete = @(
        $document.shards | Where-Object {
            -not (Test-ShardComplete $_ $document)
        }
    )
    if ($incomplete.Count -eq 0) {
        break
    }
    if ($attempt -eq $MaxAttempts) {
        throw "Identifier shards remain incomplete after $MaxAttempts attempts"
    }
    Write-Output "RETRY WAIT: $($incomplete.Count) shard(s), ${RetryDelaySeconds}s"
    Start-Sleep -Seconds $RetryDelaySeconds
    Wait-GlobalCooldown $document
}

if (-not $SkipConsolidation) {
    & $Python "fec\legislation\build_legislation_identifier_probe_shards.py" `
        "consolidate" "--plan" $planPath
    if ($LASTEXITCODE -ne 0) {
        throw "Exact 210-query shard consolidation failed"
    }
}
