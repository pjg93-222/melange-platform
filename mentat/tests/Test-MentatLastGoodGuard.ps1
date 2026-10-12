[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$modulePath = Join-Path $repoRoot (
  'mentat\tools\parallel\MentatLastGoodGuard.psm1'
)
$launcherPath = Join-Path $repoRoot (
  'mentat\tools\parallel\Start-MentatIndustryReports.ps1'
)
$workerPath = Join-Path $repoRoot (
  'mentat\tools\parallel\Invoke-MentatIndustryWorker.ps1'
)

$passed = 0
function Assert-Guard {
  param(
    [Parameter(Mandatory = $true)][bool]$Condition,
    [Parameter(Mandatory = $true)][string]$Message
  )
  if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
  $script:passed++
  Write-Host "PASS: $Message"
}

foreach ($path in @($modulePath, $launcherPath, $workerPath)) {
  $tokens = $null
  $parseErrors = $null
  [void][Management.Automation.Language.Parser]::ParseFile(
    $path,
    [ref]$tokens,
    [ref]$parseErrors
  )
  Assert-Guard ($parseErrors.Count -eq 0) (
    "PowerShell parser accepts $([IO.Path]::GetFileName($path))"
  )
}

Import-Module -Name $modulePath -Force -ErrorAction Stop

$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
$testRoot = Join-Path $tempBase (
  'mentat-last-good-test-{0}' -f [Guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($testRoot) | Out-Null

try {
  $industry = 'Test Industry'
  $fileName = "$industry Industry Spending Overview.xlsx"
  $lastGoodRoot = Join-Path $testRoot 'last-good'
  $reportsRoot = Join-Path $testRoot 'reports'
  $statesRoot = Join-Path $testRoot 'states'
  $sourcesRoot = Join-Path $testRoot 'sources'
  foreach ($directory in @($reportsRoot, $statesRoot, $sourcesRoot)) {
    [IO.Directory]::CreateDirectory($directory) | Out-Null
  }
  $target = Join-Path $reportsRoot $fileName
  $state1 = Join-Path $statesRoot 'no-fallback.json'

  $noFallback = Initialize-MentatReportAvailability `
    -Industry $industry `
    -TargetPath $target `
    -LastGoodRoot $lastGoodRoot `
    -StatePath $state1
  Assert-Guard ($noFallback.availability -eq 'absent') (
    'missing target stays absent when there is no audited last-good pointer'
  )
  Assert-Guard (-not (Test-Path -LiteralPath $target)) (
    'guard never publishes an invented or partial workbook'
  )

  $source1Root = Join-Path $sourcesRoot 'first'
  [IO.Directory]::CreateDirectory($source1Root) | Out-Null
  $source1 = Join-Path $source1Root $fileName
  [IO.File]::WriteAllText(
    $source1,
    'audited-workbook-v1',
    [Text.UTF8Encoding]::new($false)
  )
  (Get-Item -LiteralPath $source1).LastWriteTimeUtc =
    [DateTime]::UtcNow.AddMinutes(-10)
  $saved1 = Save-MentatAuditedLastGood `
    -Industry $industry `
    -SourcePath $source1 `
    -ExpectedOutputName $fileName `
    -LastGoodRoot $lastGoodRoot `
    -GeneratorCompletedAt ([DateTimeOffset]::Now.AddMinutes(-10).ToString('o'))
  Assert-Guard ($saved1.status -eq 'stored_audited_last_good') (
    'audited report is stored as an immutable last-good payload'
  )
  Assert-Guard (
    [IO.Path]::GetFileName([string]$saved1.payload_path) -ceq $fileName
  ) 'immutable payload preserves the exact report filename'

  $state2 = Join-Path $statesRoot 'restored.json'
  $restored = Initialize-MentatReportAvailability `
    -Industry $industry `
    -TargetPath $target `
    -LastGoodRoot $lastGoodRoot `
    -StatePath $state2
  Assert-Guard (
    $restored.action -eq 'restored_audited_last_good' -and
    (Test-Path -LiteralPath $target -PathType Leaf)
  ) 'missing report is restored immediately from audited last-good'
  Assert-Guard (
    (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq
      (Get-FileHash -LiteralPath $source1 -Algorithm SHA256).Hash
  ) 'restored workbook is byte-identical to its recorded source'

  [IO.File]::WriteAllText(
    $target,
    'newer-user-facing-report',
    [Text.UTF8Encoding]::new($false)
  )
  (Get-Item -LiteralPath $target).LastWriteTimeUtc =
    [DateTime]::UtcNow.AddMinutes(10)
  $targetHashBefore = (
    Get-FileHash -LiteralPath $target -Algorithm SHA256
  ).Hash
  $state3 = Join-Path $statesRoot 'existing.json'
  $existing = Initialize-MentatReportAvailability `
    -Industry $industry `
    -TargetPath $target `
    -LastGoodRoot $lastGoodRoot `
    -StatePath $state3
  Assert-Guard ($existing.action -eq 'preserved_existing_target') (
    'existing user-facing report is never overwritten by fallback'
  )
  Assert-Guard (
    (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq
      $targetHashBefore
  ) 'newer existing report remains byte-identical'

  $source2Root = Join-Path $sourcesRoot 'older'
  [IO.Directory]::CreateDirectory($source2Root) | Out-Null
  $source2 = Join-Path $source2Root $fileName
  [IO.File]::WriteAllText(
    $source2,
    'older-candidate',
    [Text.UTF8Encoding]::new($false)
  )
  (Get-Item -LiteralPath $source2).LastWriteTimeUtc =
    [DateTime]::UtcNow.AddMinutes(-20)
  $pointerPath = [string]$saved1.pointer_path
  $pointerHashBefore = (
    Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
  ).sha256
  $olderResult = Save-MentatAuditedLastGood `
    -Industry $industry `
    -SourcePath $source2 `
    -ExpectedOutputName $fileName `
    -LastGoodRoot $lastGoodRoot `
    -GeneratorCompletedAt ([DateTimeOffset]::Now.ToString('o'))
  $pointerHashAfter = (
    Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
  ).sha256
  Assert-Guard (
    $olderResult.status -eq 'preserved_newer_last_good' -and
    $pointerHashAfter -eq $pointerHashBefore
  ) 'older candidate cannot replace a newer last-good pointer'

  [IO.File]::Delete($target)
  $currentPointer = Get-Content -LiteralPath $pointerPath -Raw |
    ConvertFrom-Json
  $corruptPayload = Join-Path $lastGoodRoot (
    [string]$currentPointer.payload_relative_path
  )
  [IO.File]::AppendAllText(
    $corruptPayload,
    '-corrupt',
    [Text.UTF8Encoding]::new($false)
  )
  $state4 = Join-Path $statesRoot 'corrupt.json'
  $corrupt = Initialize-MentatReportAvailability `
    -Industry $industry `
    -TargetPath $target `
    -LastGoodRoot $lastGoodRoot `
    -StatePath $state4
  Assert-Guard (
    $corrupt.action -eq 'last_good_rejected' -and
    -not (Test-Path -LiteralPath $target)
  ) 'corrupt last-good payload is rejected without publishing a partial output'
  Assert-Guard (
    (Get-Content -LiteralPath $state4 -Raw | ConvertFrom-Json).error -match
      'hash/length'
  ) 'fallback rejection and provenance are recorded in JSON state'

  $pendingFiles = @(
    Get-ChildItem -LiteralPath $testRoot -Recurse -File |
      Where-Object { $_.Name -like '*.pending' }
  )
  Assert-Guard ($pendingFiles.Count -eq 0) (
    'guard leaves no pending publication files after success or rejection'
  )

  Write-Host ''
  Write-Host "MENTAT LAST-GOOD GUARD TESTS: $passed passed, 0 failed"
} finally {
  $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
  if (
    $resolvedTestRoot.StartsWith(
      $tempBase,
      [StringComparison]::OrdinalIgnoreCase
    ) -and
    [IO.Path]::GetFileName($resolvedTestRoot).StartsWith(
      'mentat-last-good-test-',
      [StringComparison]::Ordinal
    )
  ) {
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force `
      -ErrorAction SilentlyContinue
  }
}
