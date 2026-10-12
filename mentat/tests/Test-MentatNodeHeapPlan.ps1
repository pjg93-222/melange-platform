$ErrorActionPreference = 'Stop'
$module = Join-Path (
  Split-Path -Parent $PSScriptRoot
) 'tools\node_heap_plan.psm1'
Import-Module $module -Force

$blocked = Get-MentatNodeHeapPlan -TotalPhysicalMb 32294 -AvailableMb 7672
if ($blocked.ready) {
  throw 'A heap plan above the available-memory safety cap was accepted.'
}
if ($blocked.safe_cap_mb -ne [math]::Floor(7672 * 0.70)) {
  throw 'The low-memory safety cap is not derived from available RAM.'
}
if ($blocked.heap_mb -ne 0) {
  throw 'A blocked heap plan must not return an executable heap size.'
}

$ready = Get-MentatNodeHeapPlan -TotalPhysicalMb 32294 -AvailableMb 16000
if (-not $ready.ready -or $ready.heap_mb -ne 11200) {
  throw 'A safe available-memory-limited heap plan was not returned.'
}
if ($ready.heap_mb -gt $ready.available_cap_mb) {
  throw 'The planned heap exceeds its available-memory cap.'
}

$unknown = Get-MentatNodeHeapPlan -TotalPhysicalMb 0 -AvailableMb 16000
if ($unknown.ready -or $unknown.reason -ne 'memory_detection_unavailable') {
  throw 'Missing memory telemetry must fail closed.'
}

$chunked = Get-MentatNodeHeapPlan -TotalPhysicalMb 32294 -AvailableMb 9745 -MinimumMb 6144
if (-not $chunked.ready -or $chunked.heap_mb -ne 6821) {
  throw 'The verified chunked build must fit within the available-memory cap.'
}
$chunkedBlocked = Get-MentatNodeHeapPlan -TotalPhysicalMb 32294 -AvailableMb 8000 -MinimumMb 6144
if ($chunkedBlocked.ready -or $chunkedBlocked.heap_mb -ne 0) {
  throw 'Chunked builds must still refuse insufficient available memory.'
}

$buildScript = Join-Path (
  Split-Path -Parent $PSScriptRoot
) 'tools\build_industry_report.ps1'
$buildText = Get-Content -Raw -LiteralPath $buildScript
$cacheGateIndex = $buildText.IndexOf('if (-not $workbookCacheHit)')
$lockAcquiredIndex = $buildText.IndexOf(
  'Write-Host "Workbook build slot acquired: $WorkbookLockPath"'
)
$planIndex = $buildText.IndexOf('$heapPlan = Get-MentatNodeHeapPlan')
$nodeIndex = $buildText.IndexOf(
  '& $NodePath "--max-old-space-size=$NodeHeapMb"'
)
if (
  $cacheGateIndex -lt 0 -or
  $lockAcquiredIndex -lt 0 -or
  $planIndex -lt 0 -or
  $nodeIndex -lt 0 -or
  $planIndex -le $cacheGateIndex -or
  $planIndex -le $lockAcquiredIndex -or
  $planIndex -ge $nodeIndex
) {
  throw (
    'Automatic heap planning must occur only on a workbook cache miss, ' +
    'after the build lock is acquired, and immediately before Node starts.'
  )
}

$largePayloadDeclarationCount = [regex]::Matches(
  $buildText,
  '(?m)^\$largePayloadThresholdBytes = 120MB\r?$'
).Count
$largePayloadGuardCount = [regex]::Matches(
  $buildText,
  (
    '\(Get-Item -LiteralPath \$dataPath\)\.Length -ge\s+' +
    '\$largePayloadThresholdBytes'
  )
).Count
if (
  $largePayloadDeclarationCount -ne 1 -or
  $largePayloadGuardCount -ne 2 -or
  $buildText.Contains('150MB')
) {
  throw (
    'Compact-raw and formula-facts modes must share the named 120MB ' +
    'large-payload threshold.'
  )
}

Write-Host 'Test-MentatNodeHeapPlan: PASS'
