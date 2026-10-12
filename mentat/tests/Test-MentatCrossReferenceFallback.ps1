$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildPath = Join-Path $projectRoot 'tools\build_industry_report.ps1'
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($buildPath, [ref]$null, [ref]$errors)
if ($errors.Count) { throw $errors[0].Message }
# Load only the retry decision. This test starts no Python, no build, no FEC request.
$definition = @($ast.FindAll({ param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
  $node.Name -eq 'Invoke-MentatCrossReference'
}, $true))
if ($definition.Count -ne 1) { throw 'Expected exactly one Invoke-MentatCrossReference definition.' }
. ([scriptblock]::Create($definition[0].Extent.Text))

$script:calls = $null
$script:plannedExits = @()
# Stands in for `& $PythonPath @CommandArguments`: it records the command line the
# builder would have run, WRITES ON THE SUCCESS STREAM the way the real tool does
# (run_cross_reference.py:595 prints a JSON line per step and :599 a closing
# summary), and sets the exit code the case under test needs. A stub that printed
# nothing would hide a mode that had captured those lines instead of returning.
$script:emitted = @(
  '{"run_id":"stub","step":"define","detail":"Pinning companies, cycles and source database"}',
  '{"run_id":"stub","summary":{"input_records":1},"link_count":0}'
)
$runner = {
  param([object[]]$CommandArguments)
  $script:calls.Add(@($CommandArguments | ForEach-Object { [string]$_ }))
  $script:emitted
  $global:LASTEXITCODE = [int]($script:plannedExits[$script:calls.Count - 1])
}
function Reset-Stub {
  param([int[]]$Exits)
  $script:calls = New-Object Collections.Generic.List[object]
  $script:plannedExits = $Exits
  $global:LASTEXITCODE = 0
}
$base = @('run_cross_reference.py', '--database', 'C:\fixture\mentat.db',
  '--industry', 'Crypto', '--cycles', '2022,2024,2026',
  '--baseline-root', 'C:\fixture\state', '--max-requests', 100)

function Invoke-Case {
  param([int[]]$Exits, [bool]$Offline)
  Reset-Stub $Exits
  # The mode arrives by reference; everything the runner writes must stay on the
  # success stream and flow out to the caller, where the builder's own callers
  # tee or redirect it into the build log.
  $mode = 'NOT-SET'
  $captured = & {
    Invoke-MentatCrossReference -Arguments $base -Offline $Offline -Runner $runner `
      -Mode ([ref]$mode)
  } 3>&1
  $warnings = @($captured | Where-Object {
    $_ -is [Management.Automation.WarningRecord]
  })
  $passed = @($captured | Where-Object {
    $_ -isnot [Management.Automation.WarningRecord]
  } | ForEach-Object { [string]$_ })
  return [pscustomobject]@{
    mode = $mode
    mode_is_string = ($mode -is [string])
    mode_item_count = @($mode).Count
    passed = ($passed -join "`n")
    passed_count = $passed.Count
    warnings = ($warnings | ForEach-Object { [string]$_ }) -join ' '
    warning_count = $warnings.Count
  }
}

# Every case asserts the mode is exactly one string and that nothing the runner
# printed leaked into it.
function Assert-Mode {
  param([pscustomobject]$Case, [string]$Expected, [int]$Invocations)
  if (-not $Case.mode_is_string) {
    throw "The mode must be a string; saw [$($Case.mode.GetType().FullName)] with $($Case.mode_item_count) items: $(@($Case.mode) -join ' | ')"
  }
  if ($Case.mode_item_count -ne 1) { throw "The mode must be a single value; saw $($Case.mode_item_count)." }
  if ($Case.mode -cne $Expected) { throw "Expected mode '$Expected'; saw '$($Case.mode)'." }
  # Two lines per invocation, all of them handed onward, none of them captured.
  $expectedLines = 2 * $Invocations
  if ($Case.passed_count -ne $expectedLines) {
    throw "The runner's output must reach the build log: expected $expectedLines lines, saw $($Case.passed_count)."
  }
  foreach ($line in $script:emitted) {
    if (-not $Case.passed.Contains($line)) { throw "The runner's output was swallowed: $line" }
  }
  if ($Case.passed.Contains($Expected)) { throw 'The mode must not be written onto the success stream.' }
}

$failureMessage = 'Automatic FEC cross-referencing failed. Review the saved process evidence before exporting.'

# 1. Online success: one invocation, no --offline, no warning, mode `online`.
$case = Invoke-Case -Exits @(0) -Offline $false
if ($script:calls.Count -ne 1) { throw "Online success must invoke the tool once; saw $($script:calls.Count)." }
Assert-Mode -Case $case -Expected 'online' -Invocations 1
if ($script:calls[0] -contains '--offline') { throw 'An online run must not pass --offline.' }
if ($case.warning_count -ne 0) { throw 'A successful online run must not warn.' }

# 2. Online failure then offline success: retried exactly once, same arguments plus
#    --offline, the build continues, the fallback is visible and recorded.
$case = Invoke-Case -Exits @(1, 0) -Offline $false
if ($script:calls.Count -ne 2) { throw "An online failure must be retried exactly once; saw $($script:calls.Count)." }
if ($script:calls[0] -contains '--offline') { throw 'The first attempt must be the online one.' }
if ($script:calls[1] -notcontains '--offline') { throw 'The offline retry must carry --offline.' }
$retryPrefix = ($script:calls[1][0..($script:calls[1].Count - 2)] -join '|')
if ($retryPrefix -ne ($script:calls[0] -join '|')) {
  throw 'The offline retry must repeat the same other arguments in the same order.'
}
Assert-Mode -Case $case -Expected 'offline_fallback' -Invocations 2
if ($case.warning_count -lt 1) { throw 'The offline fallback must warn on the build output.' }
if (-not $case.warnings.Contains('--offline')) {
  throw 'The fallback warning must name the offline retry.'
}
if (-not $case.warnings.Contains('OFFLINE')) {
  throw 'The fallback warning must say the run completed offline.'
}

# 3. Both attempts fail: the original message, unchanged, after exactly two tries.
Reset-Stub @(1, 1)
$thrown = ''
$doomedMode = 'NOT-SET'
try {
  Invoke-MentatCrossReference -Arguments $base -Offline $false -Runner $runner `
    -Mode ([ref]$doomedMode) 3>$null | Out-Null
} catch {
  $thrown = $_.Exception.Message
}
if ($thrown -ne $failureMessage) { throw "A failure offline too must throw the existing message; saw '$thrown'." }
if ($script:calls.Count -ne 2) { throw "A doomed run must still try exactly twice; saw $($script:calls.Count)." }
if ($doomedMode -cne 'NOT-SET') { throw "A thrown run must leave the mode unset; saw '$doomedMode'." }

# 4. The operator asked for offline: one invocation with --offline, never retried.
$case = Invoke-Case -Exits @(0) -Offline $true
if ($script:calls.Count -ne 1) { throw "-CrossReferenceOffline must invoke the tool once; saw $($script:calls.Count)." }
if ($script:calls[0] -notcontains '--offline') { throw 'A requested offline run must pass --offline.' }
Assert-Mode -Case $case -Expected 'offline' -Invocations 1
if ($case.warning_count -ne 0) { throw 'A successful requested offline run must not warn.' }

Reset-Stub @(1)
$thrown = ''
$doomedMode = 'NOT-SET'
try {
  Invoke-MentatCrossReference -Arguments $base -Offline $true -Runner $runner `
    -Mode ([ref]$doomedMode) 3>$null | Out-Null
} catch {
  $thrown = $_.Exception.Message
}
if ($thrown -ne $failureMessage) { throw "A failed requested offline run must throw the existing message; saw '$thrown'." }
if ($script:calls.Count -ne 1) { throw "A requested offline run must never be retried; saw $($script:calls.Count) calls." }
if ($doomedMode -cne 'NOT-SET') { throw "A thrown offline run must leave the mode unset; saw '$doomedMode'." }

# The builder reaches the retry through the function and records its answer.
$buildText = Get-Content -Raw -LiteralPath $buildPath
foreach ($fragment in @(
  "`$crossReferenceMode = ''",
  'Invoke-MentatCrossReference `',
  '  -Offline ([bool]$CrossReferenceOffline) `',
  '  -Runner { param([object[]]$CommandArguments) & $PythonPath @CommandArguments } `',
  '  -Mode ([ref]$crossReferenceMode)',
  'cross_reference_mode = $crossReferenceMode'
)) {
  if (-not $buildText.Contains($fragment)) {
    throw "Report builder is missing a cross-reference fallback fragment: $fragment"
  }
}
if ($buildText.Contains("if (`$CrossReferenceOffline) { `$crossReferenceArguments += '--offline' }")) {
  throw 'The cross-reference switch must reach the tool through the retry, not around it.'
}
# Assigning the call would capture run_cross_reference.py's stdout into the
# receipt field and strip it from the build log.
if ($buildText.Contains("`$crossReferenceMode = Invoke-MentatCrossReference")) {
  throw 'The cross-reference call must not be assigned; the mode travels by reference.'
}

# Every script that reaches the builder declares both switches, with the builder's
# own defaults, and forwards them. A caller that declares but drops one would put
# the operator's budget back to the builder default without saying so.
$forwarding = [ordered]@{
  'tools\build_industry_report.ps1' = @()
  'tools\generate_industry_report.ps1' = @(
    '$arguments.CrossReferenceMaxRequests = $CrossReferenceMaxRequests',
    'if ($CrossReferenceOffline) { $arguments.CrossReferenceOffline = $true }'
  )
  'tools\generate_crypto_report.ps1' = @(
    'CrossReferenceMaxRequests = $CrossReferenceMaxRequests',
    'if ($CrossReferenceOffline) { $arguments.CrossReferenceOffline = $true }'
  )
  'tools\parallel\Invoke-MentatDirectReport.ps1' = @(
    'CrossReferenceMaxRequests = $CrossReferenceMaxRequests',
    'if ($CrossReferenceOffline) { $generatorArguments.CrossReferenceOffline = $true }'
  )
  'tools\parallel\Start-MentatIndustryReports.ps1' = @(
    '$resilientArguments.CrossReferenceMaxRequests = $CrossReferenceMaxRequests',
    '$resilientArguments.CrossReferenceOffline = $true',
    'cross_reference_max_requests = [int]$CrossReferenceMaxRequests',
    'cross_reference_offline = [bool]$CrossReferenceOffline'
  )
  'tools\parallel\Start-MentatResilientIndustryReports.ps1' = @(
    'CrossReferenceMaxRequests = $CrossReferenceMaxRequests',
    'CrossReferenceOffline = [bool]$CrossReferenceOffline'
  )
}
foreach ($relative in $forwarding.Keys) {
  $path = Join-Path $projectRoot $relative
  $callerErrors = $null
  $callerAst = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$callerErrors)
  if ($callerErrors.Count) { throw "PowerShell parse failure in ${relative}: $($callerErrors[0].Message)" }
  $parameters = @{}
  foreach ($parameter in $callerAst.ParamBlock.Parameters) {
    $parameters[$parameter.Name.VariablePath.UserPath] = $parameter
  }
  foreach ($name in @('CrossReferenceMaxRequests', 'CrossReferenceOffline')) {
    if (-not $parameters.ContainsKey($name)) {
      throw "${relative} does not declare -$name."
    }
  }
  $budget = $parameters['CrossReferenceMaxRequests']
  if ([string]$budget.DefaultValue.Extent.Text -ne '100') {
    throw "${relative} declares -CrossReferenceMaxRequests with default '$($budget.DefaultValue.Extent.Text)', not 100."
  }
  if (-not $budget.StaticType.Equals([int])) {
    throw "${relative} must declare -CrossReferenceMaxRequests as [int]."
  }
  if (-not $parameters['CrossReferenceOffline'].StaticType.Equals([switch])) {
    throw "${relative} must declare -CrossReferenceOffline as [switch]."
  }
  $callerText = Get-Content -Raw -LiteralPath $path
  foreach ($fragment in $forwarding[$relative]) {
    if (-not $callerText.Contains($fragment)) {
      throw "${relative} does not forward the cross-reference settings: $fragment"
    }
  }
}

# The worker is the one hop that travels as JSON rather than as parameters, so it
# reads the spec fields the launcher writes and passes them on as switches.
$workerPath = Join-Path $projectRoot 'tools\parallel\Invoke-MentatIndustryWorker.ps1'
$workerErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile($workerPath, [ref]$null, [ref]$workerErrors)
if ($workerErrors.Count) { throw "PowerShell parse failure in the worker: $($workerErrors[0].Message)" }
$workerText = Get-Content -Raw -LiteralPath $workerPath
foreach ($fragment in @(
  "`$spec.PSObject.Properties['cross_reference_max_requests']",
  "`$generatorCommand.Parameters.ContainsKey('CrossReferenceMaxRequests')",
  "`$generatorArguments.Add('-CrossReferenceMaxRequests')",
  "`$spec.PSObject.Properties['cross_reference_offline']",
  "`$generatorCommand.Parameters.ContainsKey('CrossReferenceOffline')",
  "`$generatorArguments.Add('-CrossReferenceOffline')"
)) {
  if (-not $workerText.Contains($fragment)) {
    throw "The industry worker does not carry the cross-reference settings: $fragment"
  }
}
# Both fields are optional, exactly like resume_after and slot_capacity: an older
# spec written before this change must still run, on the builder's own defaults.
foreach ($field in @('cross_reference_max_requests', 'cross_reference_offline')) {
  if ($workerText.Contains("'$field', ") -or $workerText.Contains(", '$field'")) {
    throw "The worker must not require the optional spec field $field."
  }
}

Write-Host 'Test-MentatCrossReferenceFallback: PASS'
