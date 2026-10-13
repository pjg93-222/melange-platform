$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$builderPath = Join-Path $projectRoot 'tools\crypto_workbook_builder.mjs'
$builderText = Get-Content -Raw -LiteralPath $builderPath

$requiredFragments = @(
  'function emptyRawTablePlaceholder(headers, sheetName, exceptionSheet)',
  '["Source ID", "NO_SOURCE_ROWS"]',
  '["Fact ID", "NO_SOURCE_ROWS"]',
  '["Include in Analysis", "No"]',
  'exceptionSheet ? "Outside requested cycles" : "OK"',
  ': [emptyRawTablePlaceholder(headers, sheetName, exceptionSheet)]',
  '4 + tableData.length'
)

foreach ($fragment in $requiredFragments) {
  if (-not $builderText.Contains($fragment)) {
    throw "Workbook builder is missing the empty-table guard fragment: $fragment"
  }
}

if ($builderText.Contains('4 + data.length')) {
  throw 'Workbook tables must be sized from guarded tableData, not raw data.'
}

$buildPath = Join-Path $projectRoot 'tools\build_industry_report.ps1'
$finalizerPath = Join-Path $projectRoot 'tools\finalize_crypto_excel.ps1'
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
  $buildPath,
  [ref]$null,
  [ref]$parseErrors
)
if ($parseErrors.Count) {
  throw "build_industry_report.ps1 has parse errors: $($parseErrors[0].Message)"
}
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
  $finalizerPath,
  [ref]$null,
  [ref]$parseErrors
)
if ($parseErrors.Count) {
  throw "finalize_crypto_excel.ps1 has parse errors: $($parseErrors[0].Message)"
}

$buildText = Get-Content -Raw -LiteralPath $buildPath
$finalizerText = Get-Content -Raw -LiteralPath $finalizerPath
foreach ($fragment in @(
  'data_sha256',
  'cycle_label',
  'source_data_sha256',
  'nonempty WAL sidecar',
  'Get-QuiescentDatabaseSha256',
  'databaseHashAfterExport',
  'changed during the $Industry export',
  'token=$workbookLockToken'
)) {
  if (-not $buildText.Contains($fragment)) {
    throw "Report builder is missing cache/lease guard fragment: $fragment"
  }
}
if ($buildText.Contains('Reusing fresh export after report-builder-only changes.')) {
  throw 'Content-fingerprint mismatches must never fall back to mtimes.'
}
if ($finalizerText.Contains('$compactJson')) {
  throw 'Finalizer must honor the explicit JsonPath without sibling override.'
}
$targetMove = $buildText.LastIndexOf(
  'Move-Item -LiteralPath $pendingWorkbook -Destination $targetWorkbook -Force'
)
$ownedRelease = $buildText.LastIndexOf(
  'Remove-Item -LiteralPath $WorkbookLockPath -Force'
)
if ($targetMove -lt 0 -or $ownedRelease -le $targetMove) {
  throw 'Workbook lease must be released only after atomic target publication.'
}

# Companion Data workbook publication order.
#
# The console globs the published folder and pairs the two books by name, and
# `sourceGuard accept` advances the accepted source baseline for the NEXT run.
# If acceptance ran between the two publications, a failure in the Data copy
# would leave the baseline advanced past an Overview whose companion never
# landed, and the following build would treat the half-published pair as the
# accepted state. The Data book therefore goes out after the Overview and
# before acceptance, under the same copy-then-atomic-move.
foreach ($fragment in @(
  'stage_sha256',
  '$pendingStage = "$stagePath.pending-$PID"',
  '--staging $pendingStage',
  'PRAGMA integrity_check',
  'Move-Item -LiteralPath $pendingStage -Destination $stagePath -Force',
  '--overview-receipt $overviewReceiptPath',
  'Assert-WorkbookPackage $stageDataWorkbook $dataWorksheetCount',
  'data_workbook_sha256 = $dataWorkbookSha256'
)) {
  if (-not $buildText.Contains($fragment)) {
    throw "Report builder is missing a tidy data-workbook fragment: $fragment"
  }
}

$dataMove = $buildText.LastIndexOf(
  'Move-Item -LiteralPath $pendingDataWorkbook -Destination $targetDataWorkbook -Force'
)
$sourceAccept = $buildText.LastIndexOf(
  '& $PythonPath $sourceGuard accept --receipt $sourceGuardReceipt'
)
if ($dataMove -lt 0) {
  throw 'The companion Data workbook must be published by an atomic move.'
}
if ($dataMove -le $targetMove) {
  throw 'The companion Data workbook must be published after the Overview.'
}
if ($sourceAccept -lt 0 -or $sourceAccept -le $dataMove) {
  throw 'Accepted-source advance must follow BOTH workbook publications.'
}
if ($ownedRelease -le $dataMove) {
  throw 'Workbook lease must be released only after both books are published.'
}

$writerCall = $buildText.IndexOf('& $PythonPath $tidyWriter --staging $stagePath')
$overviewCopy = $buildText.LastIndexOf(
  'Copy-Item -LiteralPath $stageWorkbook -Destination $pendingWorkbook -Force'
)
if ($writerCall -lt 0 -or $overviewCopy -lt 0 -or $writerCall -ge $overviewCopy) {
  throw 'The tidy writer must run before anything is copied into the output folder.'
}
if ($buildText.Contains('Assert-WorkbookPackage $stageDataWorkbook $expectedWorksheetCount')) {
  throw 'The Data book must be checked against its OWN receipt, not the Overview count.'
}

# The tidy stage probe, run exactly as the builder runs it, under this host. Windows
# PowerShell 5.1 (the host the builder documents) passes a native argument's embedded
# double quotes unescaped, so a python -c program holding "..." arrives cut at the first
# quote; every uncached build then failed after its export with "Tidy staging store could
# not be read" (lane W, 2026-09-26).
$probePattern = '\$stageProbe = @\(& \$PythonPath -c ''((?:[^'']|'''')*)'' \$pendingStage\)'
$probeMatch = [regex]::Match($buildText, $probePattern)
if (-not $probeMatch.Success) {
  throw 'The tidy stage probe line was not found in build_industry_report.ps1.'
}
$probeProgram = $probeMatch.Groups[1].Value.Replace("''", "'")
if ($probeProgram.Contains('"')) {
  throw 'The tidy stage probe must not hold a double quote: Windows PowerShell 5.1 strips it from a native argument.'
}
Import-Module (Join-Path $projectRoot 'tools\mentat_python.psm1') -Force
$probePython = (Resolve-MentatPython -ExplicitPath '').Path
$probeStore = Join-Path ([IO.Path]::GetTempPath()) ('mentat-stage-probe-' + [Guid]::NewGuid().ToString('N') + '.sqlite')
try {
  & $probePython -c 'import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute(''CREATE TABLE contributions(source_id TEXT)'');c.executemany(''INSERT INTO contributions VALUES(?)'',[(''a'',),(''b'',)]);c.commit();c.close()' $probeStore
  if ($LASTEXITCODE -ne 0) { throw 'Could not create the stage probe test store.' }
  $probeAnswer = @(& $probePython -c $probeProgram $probeStore)
  if ($LASTEXITCODE -ne 0 -or $probeAnswer.Count -lt 2 -or $probeAnswer[0].Trim() -ne 'ok' -or $probeAnswer[1].Trim() -ne '2') {
    throw "The tidy stage probe did not read a valid two-row store under PowerShell $($PSVersionTable.PSVersion): $($probeAnswer -join ' | ')"
  }
} finally {
  if (Test-Path -LiteralPath $probeStore) { Remove-Item -LiteralPath $probeStore -Force }
}

# Assert-WorkbookPackage streams each worksheet part (lane W, 2026-09-26: an in-memory
# [xml] cast refused a 659 MB Beneficiary Bridge part and failed a finished build). Run
# the function itself, taken from the script, on small packages.
if ($buildText -match '\[xml\]\$sheetXml') {
  throw 'Assert-WorkbookPackage must stream worksheet parts, not load them into an XmlDocument.'
}
$buildAst = [System.Management.Automation.Language.Parser]::ParseFile($buildPath, [ref]$null, [ref]$null)
$packageCheck = $buildAst.Find({ param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-WorkbookPackage'
}, $true)
if ($null -eq $packageCheck) { throw 'Assert-WorkbookPackage was not found in build_industry_report.ps1.' }
Invoke-Expression $packageCheck.Extent.Text
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
function New-TestPackage([string]$Path, [string[]]$SheetBodies) {
  $zip = [IO.Compression.ZipFile]::Open($Path, [IO.Compression.ZipArchiveMode]::Create)
  try {
    $parts = @{ '[Content_Types].xml' = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>';
                'xl/workbook.xml' = '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>' }
    for ($i = 0; $i -lt $SheetBodies.Count; $i++) {
      $parts["xl/worksheets/sheet$($i + 1).xml"] = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' +
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData>' +
        $SheetBodies[$i] + '</worksheet>'
    }
    foreach ($name in $parts.Keys) {
      $writer = [IO.StreamWriter]::new($zip.CreateEntry($name).Open())
      try { $writer.Write($parts[$name]) } finally { $writer.Dispose() }
    }
  } finally { $zip.Dispose() }
}
$packageDir = Join-Path ([IO.Path]::GetTempPath()) ('mentat-package-check-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $packageDir | Out-Null
try {
  $plain = Join-Path $packageDir 'plain.xlsx'
  New-TestPackage $plain @('')
  Assert-WorkbookPackage $plain 1
  $tableOnly = Join-Path $packageDir 'table.xlsx'
  New-TestPackage $tableOnly @('<tableParts count="1"><tablePart r:id="rId1"/></tableParts>')
  Assert-WorkbookPackage $tableOnly 1
  $overlap = Join-Path $packageDir 'overlap.xlsx'
  New-TestPackage $overlap @('', '<autoFilter ref="A1:A1"/><tableParts count="1"><tablePart r:id="rId1"/></tableParts>')
  $refused = $false
  try { Assert-WorkbookPackage $overlap 2 } catch { $refused = $_.Exception.Message -like '*overlapping worksheet AutoFilter*' }
  if (-not $refused) { throw 'Assert-WorkbookPackage must refuse a worksheet AutoFilter beside an Excel table.' }
  $refused = $false
  try { Assert-WorkbookPackage $plain 2 } catch { $refused = $_.Exception.Message -like '*expected 2*' }
  if (-not $refused) { throw 'Assert-WorkbookPackage must refuse a wrong worksheet count.' }
} finally {
  Remove-Item -LiteralPath $packageDir -Recurse -Force
}

# Recipient categories. The builder creates one Master Overview channel and one
# Spending Overview row per category, the finalizer fills each channel by column
# NAME, and the export guard reports a rendered category nothing populates. The
# lists must agree, in order, or a channel is built without formulas or formulas
# are written to a column that does not exist. Conduit (ActBlue, WinRed) is its
# own category directly after Carey and may legitimately be empty.
function Get-QuotedItems([string]$Text, [string]$Quote) {
  @([regex]::Matches($Text, "$Quote([^$Quote]+)$Quote") | ForEach-Object { $_.Groups[1].Value })
}
$exportPath = Join-Path $projectRoot 'tools\export_crypto_spending_data.R'
$exportText = Get-Content -Raw -LiteralPath $exportPath
$builderChannels = Get-QuotedItems ([regex]::Match($builderText, 'const masterCategories = \[(.*?)\];', 'Singleline').Groups[1].Value) '"'
$builderScopes = Get-QuotedItems ([regex]::Match($builderText, 'const scopes = \[(.*?)\];', 'Singleline').Groups[1].Value) '"'
$finalizerChannels = Get-QuotedItems ([regex]::Match($finalizerText, '\$masterCategories = @\((.*?)\)').Groups[1].Value) "'"
$guardCategories = Get-QuotedItems ([regex]::Match($exportText, 'mentat_guard_category_population\(\s*c\((.*?)\),', 'Singleline').Groups[1].Value) '"'
if ($builderChannels.Count -lt 10) { throw 'Could not read masterCategories from the workbook builder.' }
if (($builderChannels -join '|') -ne ($finalizerChannels -join '|')) {
  throw "Finalizer masterCategories do not match the builder's: builder=[$($builderChannels -join ', ')] finalizer=[$($finalizerChannels -join ', ')]"
}
if ((@($builderScopes | Select-Object -Skip 5) -join '|') -ne ($builderChannels -join '|')) {
  throw "Spending Overview scopes are not the five role scopes followed by the Master Overview channels: [$($builderScopes -join ', ')]"
}
if ((@($guardCategories | Sort-Object) -join '|') -ne (@($builderChannels | Sort-Object) -join '|')) {
  throw "The export category_population guard does not declare the categories the workbook renders: [$($guardCategories -join ', ')]"
}
$carey = [array]::IndexOf($builderChannels, 'Carey')
if ($carey -lt 0 -or $builderChannels[$carey + 1] -ne 'Conduit') {
  throw 'Conduit (ActBlue, WinRed) must be its own channel directly after Carey in the workbook builder.'
}
if (-not $builderText.Contains('Conduit: ["Conduit"],')) {
  throw 'The builder scope formulas must map the Conduit scope to the Conduit recipient category.'
}
if (-not $exportText.Contains('optional = "Conduit"')) {
  throw 'An industry or cycle with no earmark-platform receipts is a real zero: Conduit must be an optional category in the population guard.'
}

# MENTAT_XLSX_WRITER (D-178): the writer is part of the workbook cache key, and only the artifact
# writer may fail a build for @oai/artifact-tool's absence.
#
# Cache key. Run the script's own salt statement for each writer: the salt must differ, and the
# fingerprinted inputs must hold the openpyxl writer's two files beside the builder, so a workbook
# cached by one writer is never reused for the other.
$assignments = $buildAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)
$saltStatement = @($assignments | Where-Object { $_.Left.Extent.Text -eq '$workbookFingerprintSalt' -and $_.Operator -eq 'Equals' })
$inputsStatement = @($assignments | Where-Object { $_.Left.Extent.Text -eq '$workbookFingerprintInputs' -and $_.Operator -eq 'Equals' })
if ($saltStatement.Count -ne 1 -or $inputsStatement.Count -ne 1) {
  throw 'build_industry_report.ps1 must assign $workbookFingerprintSalt and $workbookFingerprintInputs exactly once.'
}
$templateContract = 'contract'; $Industry = 'Crypto'; $CycleLabel = ''; $compactRawMode = '0'
$formulaFactsMode = '0'; $RenderPreviews = $false; $databaseHash = 'db'
$xlsxWriter = 'artifact'
Invoke-Expression $saltStatement[0].Extent.Text
$artifactSalt = $workbookFingerprintSalt
$xlsxWriter = 'openpyxl'
Invoke-Expression $saltStatement[0].Extent.Text
if ($workbookFingerprintSalt -eq $artifactSalt -or -not $workbookFingerprintSalt.EndsWith('xlsx_writer=openpyxl')) {
  throw "The workbook cache salt must change with MENTAT_XLSX_WRITER: [$artifactSalt] vs [$workbookFingerprintSalt]"
}
foreach ($writerFile in @('openpyxl_workbook_shim.mjs', 'xlsx_spec_writer.py')) {
  if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "tools\$writerFile") -PathType Leaf)) {
    throw "The openpyxl writer file is missing: tools\$writerFile"
  }
  if (-not $inputsStatement[0].Extent.Text.Contains("'$writerFile'")) {
    throw "The workbook cache fingerprint must include $writerFile."
  }
}
if (-not $inputsStatement[0].Extent.Text.Contains('$builder,')) {
  throw 'The workbook cache fingerprint must still include the builder.'
}

# Artifact-tool requirement. Run a copy of the build script, with its three modules, in a throwaway
# project (no node_modules, USERPROFILE pointed away from the Codex runtime) against a database that
# does not exist: whatever the writer, the build ends in an error, and the error says which check
# stopped it.
$gateRoot = Join-Path ([IO.Path]::GetTempPath()) ('mentat-writer-gate-' + [Guid]::NewGuid().ToString('N'))
$gateTools = Join-Path $gateRoot 'tools'
$gateReports = Join-Path $gateRoot 'reports'
$gateEmptyModules = Join-Path $gateRoot 'empty_modules'
New-Item -ItemType Directory -Path $gateTools, $gateReports, $gateEmptyModules | Out-Null
try {
  foreach ($gateFile in @('build_industry_report.ps1', 'node_heap_plan.psm1', 'mentat_python.psm1', 'report_directory.psm1')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "tools\$gateFile") -Destination $gateTools
  }
  function Invoke-WriterGateProbe([string]$Writer, [string]$ModulesArgument) {
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = (Get-Command powershell.exe).Source
    $argumentLine = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -DatabasePath "{1}" -OutputDirectory "{2}" -NodePath "{3}" -PythonPath "{3}"' -f `
      (Join-Path $gateTools 'build_industry_report.ps1'), (Join-Path $gateRoot 'no-such.db'), $gateReports, $probePython
    if ($ModulesArgument) { $argumentLine += ' -NodeModulesPath "' + $ModulesArgument + '"' }
    $psi.Arguments = $argumentLine
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.EnvironmentVariables['MENTAT_XLSX_WRITER'] = $Writer
    $psi.EnvironmentVariables['MENTAT_REPORT_DIRECTORY'] = $gateReports
    $psi.EnvironmentVariables['USERPROFILE'] = $gateRoot
    foreach ($ambient in @('NODE_PATH', 'MENTAT_NODE_MODULES', 'MENTAT_NODE_PATH')) {
      [void]$psi.EnvironmentVariables.Remove($ambient)
    }
    $process = [Diagnostics.Process]::Start($psi)
    $errorText = $process.StandardError.ReadToEndAsync()
    $outText = $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()
    [pscustomobject]@{
      ExitCode = $process.ExitCode
      Text = (($outText + ' ' + $errorText.Result) -replace '\s+', ' ')
    }
  }
  $gateCases = @(
    @{ Writer = 'artifact'; Modules = '';                Must = 'The @oai/artifact-tool Node module was not found' },
    @{ Writer = 'artifact'; Modules = $gateEmptyModules; Must = '@oai/artifact-tool is missing under' },
    @{ Writer = 'openpyxl'; Modules = '';                Must = 'Required report input or runtime is missing' },
    @{ Writer = 'openpyxl'; Modules = $gateEmptyModules; Must = 'Required report input or runtime is missing' },
    @{ Writer = 'bogus';    Modules = '';                Must = 'MENTAT_XLSX_WRITER must be artifact or openpyxl, got bogus' }
  )
  foreach ($case in $gateCases) {
    $probe = Invoke-WriterGateProbe $case.Writer $case.Modules
    if ($probe.ExitCode -eq 0 -or -not $probe.Text.Contains($case.Must)) {
      throw "Writer gate: MENTAT_XLSX_WRITER=$($case.Writer) modules=[$($case.Modules)] expected an error holding '$($case.Must)' (exit $($probe.ExitCode)): $($probe.Text)"
    }
    # Past the gate the only failure left is the missing database; a writer other than artifact must
    # never be stopped by the artifact-tool module.
    if ($case.Writer -eq 'openpyxl' -and $probe.Text.Contains('artifact-tool')) {
      throw "The openpyxl writer must not require @oai/artifact-tool: $($probe.Text)"
    }
  }
} finally {
  Remove-Item -LiteralPath $gateRoot -Recurse -Force
}

Write-Host 'Test-MentatWorkbookBuilderGuards: PASS'
