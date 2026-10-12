param(
    [int]$MaxAttempts = 240,
    [int]$RetryDelaySeconds = 300
)

$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
$Node = "C:\Users\pgoff\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$Python = "C:\Users\pgoff\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:NODE_PATH = "C:\Users\pgoff\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
$Profile = "C:\tmp\congress-pages-interchange-profile-2"
$CategoryRun = Join-Path $Workspace "audits\legfix5\congress_advanced_pages_20260725"
$IdentifierRun = Join-Path $Workspace "audits\legfix5\identifier_probe_pages_parallel_20260726"
$IdentifierRegistry = Join-Path $Workspace "source_data\legislation\acu_legislation_identifier_probe_registry.json"
$HistoricalRun = Join-Path $Workspace "audits\legfix5\historical_probe_pages_20260725"
$HistoricalRegistry = Join-Path $Workspace "source_data\legislation\acu_legislation_historical_probe_registry.json"
$FinalRun = Join-Path $Workspace "audits\legfix5\congress_advanced_final_20260725"

function Test-CompleteManifest {
    param(
        [string]$Path,
        [string]$RegistryPath,
        [string]$RequiredProperty,
        [string]$RequiredValue
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    try {
        $manifest = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        if ($manifest.status -ne "complete") {
            return $false
        }
        if ($RegistryPath) {
            $expectedRegistryHash = (
                Get-FileHash -Algorithm SHA256 -LiteralPath $RegistryPath
            ).Hash.ToLowerInvariant()
            if ($manifest.registry_sha256 -ne $expectedRegistryHash) {
                return $false
            }
        }
        if ($RequiredProperty) {
            $property = $manifest.PSObject.Properties[$RequiredProperty]
            if (
                $null -eq $property -or
                [string]$property.Value -ne $RequiredValue
            ) {
                return $false
            }
        }
        return $true
    }
    catch {
        return $false
    }
}

function Invoke-NodeUntilComplete {
    param(
        [string]$Label,
        [string[]]$Arguments,
        [string]$ManifestPath,
        [string]$RegistryPath,
        [string]$RequiredProperty,
        [string]$RequiredValue
    )
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt += 1) {
        if (
            Test-CompleteManifest `
                -Path $ManifestPath `
                -RegistryPath $RegistryPath `
                -RequiredProperty $RequiredProperty `
                -RequiredValue $RequiredValue
        ) {
            Write-Output "SKIP COMPLETE: $Label"
            return
        }
        Write-Output "START: $Label attempt $attempt/$MaxAttempts"
        $priorErrorActionPreference = $ErrorActionPreference
        try {
            # Windows PowerShell wraps native stderr as ErrorRecord objects.
            # A failed network attempt must reach the explicit retry loop
            # instead of being converted into a terminating pipeline error.
            $ErrorActionPreference = "Continue"
            & $Node @Arguments 2>&1 |
                ForEach-Object { Write-Output $_ }
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $priorErrorActionPreference
        }
        if (
            Test-CompleteManifest `
                -Path $ManifestPath `
                -RegistryPath $RegistryPath `
                -RequiredProperty $RequiredProperty `
                -RequiredValue $RequiredValue
        ) {
            Write-Output "COMPLETE: $Label"
            return
        }
        Write-Output "RETRY: $Label exit=$exitCode after ${RetryDelaySeconds}s"
        Start-Sleep -Seconds $RetryDelaySeconds
    }
    throw "$Label did not complete after $MaxAttempts attempts"
}

Set-Location -LiteralPath $Workspace

& $Python "fec\legislation\build_legislation_search_registry.py"
if ($LASTEXITCODE -ne 0) {
    throw "Search registry build failed"
}
& $Python "fec\legislation\build_legislation_identifier_probe_registry.py"
if ($LASTEXITCODE -ne 0) {
    throw "Identifier registry build failed"
}
& $Python "fec\legislation\build_legislation_historical_probe_registry.py"
if ($LASTEXITCODE -ne 0) {
    throw "Historical archive probe registry build failed"
}

Invoke-NodeUntilComplete `
    -Label "Congress.gov category and recall collection" `
    -Arguments @(
        "fec\collect\collect_congress_advanced_pages.mjs",
        "--out", "audits\legfix5\congress_advanced_pages_20260725",
        "--profile", $Profile,
        "--delay-ms", "1500",
        "--headless",
        "--resume"
    ) `
    -ManifestPath (Join-Path $CategoryRun "manifest.json") `
    -RegistryPath (
        Join-Path $Workspace `
            "source_data\legislation\acu_legislation_search_registry.json"
    )

& $Python "fec\audit\audit_quorum_recall_floor.py" `
    "--primary-dir" $CategoryRun `
    "--output" (
        Join-Path $Workspace `
            "audits\legfix5\quorum_floor_20260726\category_primary_reconciliation_receipt.json"
    )
if ($LASTEXITCODE -ne 0) {
    throw "Quorum recall floor is not closed by the Congress.gov primary union"
}

Invoke-NodeUntilComplete `
    -Label "Congress.gov individual identifier probes" `
    -Arguments @(
        "fec\collect\collect_congress_advanced_pages.mjs",
        "--registry", $IdentifierRegistry,
        "--out", "audits\legfix5\identifier_probe_pages_parallel_20260726",
        "--profile", $Profile,
        "--delay-ms", "1500",
        "--headless",
        "--resume"
    ) `
    -ManifestPath (Join-Path $IdentifierRun "manifest.json") `
    -RegistryPath $IdentifierRegistry

& $Python "fec\legislation\reconcile_legislation_identifier_probes.py" `
    "--category-run" $CategoryRun `
    "--probe-run" $IdentifierRun
if ($LASTEXITCODE -ne 0) {
    throw "Identifier-to-category containment audit failed"
}

Invoke-NodeUntilComplete `
    -Label "Congress.gov historical archive recall probes" `
    -Arguments @(
        "fec\collect\collect_congress_advanced_pages.mjs",
        "--registry", $HistoricalRegistry,
        "--out", "audits\legfix5\historical_probe_pages_20260725",
        "--profile", $Profile,
        "--delay-ms", "1500",
        "--headless",
        "--resume"
    ) `
    -ManifestPath (Join-Path $HistoricalRun "manifest.json") `
    -RegistryPath $HistoricalRegistry

& $Python "fec\legislation\merge_congress_advanced_coverage_runs.py" `
    "--base-run" $CategoryRun `
    "--historical-run" $HistoricalRun `
    "--historical-registry" $HistoricalRegistry `
    "--out" $FinalRun
if ($LASTEXITCODE -ne 0) {
    throw "Historical recall merge failed"
}

& $Python "fec\audit\audit_quorum_recall_floor.py" `
    "--primary-dir" $FinalRun `
    "--output" (
        Join-Path $Workspace `
            "audits\legfix5\quorum_floor_20260726\final_union_reconciliation_receipt.json"
    )
if ($LASTEXITCODE -ne 0) {
    throw "Quorum recall floor is not closed by the final Congress.gov union"
}

Invoke-NodeUntilComplete `
    -Label "Congress.gov sponsorship and official metadata enrichment" `
    -Arguments @(
        "fec\enrich_congress_sponsorship_pages.mjs",
        "--run", $FinalRun,
        "--profile", $Profile,
        "--delay-ms", "750",
        "--headless",
        "--resume"
    ) `
    -ManifestPath (Join-Path $FinalRun "relationships\manifest.json")

Invoke-NodeUntilComplete `
    -Label "Congress.gov official-text relevance evidence" `
    -Arguments @(
        "fec\enrich_congress_text_relevance_pages.mjs",
        "--run", $FinalRun,
        "--registry", "source_data\legislation\acu_legislation_search_registry.json",
        "--profile", $Profile,
        "--delay-ms", "750",
        "--headless",
        "--resume"
    ) `
    -ManifestPath (Join-Path $FinalRun "text_relevance\manifest.json") `
    -RequiredProperty "text_match_policy_version" `
    -RequiredValue "ordered-token-bounded-act-action-required-v2"

& $Python "fec\legislation\cross_reference_legislation_press_articles.py" `
    "--run" $FinalRun
if ($LASTEXITCODE -ne 0) {
    throw "Trusted press-article cross-reference failed"
}

$AcuTitleQueryPlan = Join-Path (
    Join-Path $FinalRun "article_cross_reference"
) "acu_title_query_plan"
& $Python "fec\legislation\build_acu_bill_title_corroboration_query_plan.py" `
    "--run" $FinalRun `
    "--out" $AcuTitleQueryPlan `
    "--scope" "all" `
    "--resume"
$acuTitleQueryPlanExit = $LASTEXITCODE
Write-Output "ACU TITLE CORROBORATION QUERY PLAN EXIT: $acuTitleQueryPlanExit"
if ($acuTitleQueryPlanExit -notin @(0, 3)) {
    throw "ACU title-corroboration query plan failed"
}

& $Python "fec\legislation\review_legislation_candidate_memberships.py" `
    "--run" $FinalRun `
    "--identifier-audit" (
        Join-Path $IdentifierRun "reconciliation\identifier_record_memberships.ndjson"
    ) `
    "--decisions" "source_data\legislation\acu_legislation_membership_decisions.ndjson"
$reviewExit = $LASTEXITCODE
Write-Output "MEMBERSHIP REVIEW EXIT: $reviewExit"

& $Python "fec\legislation\reconcile_congress_advanced_pages.py" "--run" $FinalRun
$reconciliationExit = $LASTEXITCODE
Write-Output "RECONCILIATION EXIT: $reconciliationExit"

if ($reconciliationExit -ne 0 -or $reviewExit -ne 0) {
    Write-Output "PIPELINE COMPLETE WITH REVIEW GATES OPEN"
    exit 2
}

Write-Output "PIPELINE COMPLETE"
