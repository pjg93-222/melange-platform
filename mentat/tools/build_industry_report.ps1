<#
.SYNOPSIS
Build an audited Mentat industry spending workbook in one command.

.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\generate_industry_report.ps1 `
  -Industry Fintech -DatabasePath C:\path\to\mentat.db `
  -OutputDirectory C:\path\to\reports
#>
[CmdletBinding()]
param(
  [string]$Industry = 'Crypto',
  [string]$DatabasePath = '',
  [string]$OutputDirectory = '',
  [string]$CommitteeMasterDirectory = '',
  [string]$BeneficiaryDirectory = '',
  [string]$RScriptPath = 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe',
  [string]$NodePath = '',
  [string]$NodeModulesPath = '',
  [string]$PythonPath = '',
  [ValidateRange(0,32768)]
  [int]$NodeHeapMb = 0,
  [ValidateRange(1,45)]
  [double]$PerformanceBudgetMinutes = 15,
  [string]$WorkbookLockPath = '',
  [string]$ExcelMutexName = 'Global\MentatIndustryExcelFinalize',
  [ValidateSet('','2021-2022','2023-2024','2025-2026')]
  [string]$CycleLabel = '',
  [switch]$NoCache,
  [ValidateRange(0,10000)]
  [int]$CrossReferenceMaxRequests = 100,
  [switch]$CrossReferenceOffline,
  [switch]$RenderPreviews
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$heapPlanner = Join-Path $scriptRoot 'node_heap_plan.psm1'
if (-not (Test-Path -LiteralPath $heapPlanner -PathType Leaf)) {
  throw "Node heap planner is missing: $heapPlanner"
}
Import-Module $heapPlanner -Force
$templateContract = 'mentat-industry-overview-v1'
$Industry = $Industry.Trim()
# One interpreter order for every Mentat launcher (mentat_python.psm1): -PythonPath, then
# Mentat's own venv, then the Codex app's runtime Python, then python on PATH.
Import-Module (Join-Path $scriptRoot 'mentat_python.psm1') -Force
$pythonChoice = Resolve-MentatPython -ExplicitPath $PythonPath
if ($null -eq $pythonChoice) { throw 'No Python runtime was found. Pass -PythonPath or create the Mentat venv (mentat\requirements-console.txt).' }
$PythonPath = $pythonChoice.Path
Write-Host ('Python runtime: ' + $PythonPath + ' (' + $pythonChoice.Source + ')')
if ($pythonChoice.Warning) { Write-Warning $pythonChoice.Warning }
if (-not $Industry) { throw 'Industry cannot be empty.' }

$reportBaseName = if ($CycleLabel) {
  "${Industry} Industry Spending Overview ${CycleLabel}"
} else {
  "${Industry} Industry Spending Overview"
}
$industryToken = ($Industry -replace '[^A-Za-z0-9]+','_').Trim('_').ToLowerInvariant()
if (-not $industryToken) { throw "Industry cannot be converted to a safe cache token: $Industry" }
if (-not $WorkbookLockPath) {
  $WorkbookLockPath = Join-Path $projectRoot '.cache\industry-report\workbook-builder.lock'
}

if (-not $DatabasePath) {
  throw 'Pass -DatabasePath explicitly. Report sources are never selected from an ambient default database.'
}
Import-Module (Join-Path $scriptRoot 'report_directory.psm1') -Force
$OutputDirectory = Resolve-MentatReportDirectory -ProjectRoot $projectRoot -OutputDirectory $OutputDirectory
$env:MENTAT_REPORT_DIRECTORY = $OutputDirectory
$sourceRoot = Join-Path $projectRoot 'inputs'
if ($env:MENTAT_FEC_SOURCE_ROOT) { $sourceRoot = $env:MENTAT_FEC_SOURCE_ROOT }
elseif (Test-Path -LiteralPath (Join-Path $sourceRoot 'active_fec_sources.json')) {
  $sourceRoot = (Get-Content -Raw -LiteralPath (Join-Path $sourceRoot 'active_fec_sources.json') | ConvertFrom-Json).path
}
if (-not $CommitteeMasterDirectory) { $CommitteeMasterDirectory = Join-Path $sourceRoot 'fec_committee_master' }
if (-not $BeneficiaryDirectory) { $BeneficiaryDirectory = Join-Path $sourceRoot 'fec_beneficiary' }
# Pin the inputs directory for the R side. mentat_input_dir() otherwise walks up
# from the working directory, which resolves wrongly (and fatally) whenever this
# script is invoked from outside the project - a git worktree, a scheduled task,
# another drive. The report path needs inputs\ only for the trade-association
# committee-ID overrides, so an unpinned cwd silently emptied that category.
$env:MENTAT_INPUT_DIR = Join-Path $projectRoot 'inputs'
if (-not $NodePath -and $env:MENTAT_NODE_PATH) {
  $NodePath = $env:MENTAT_NODE_PATH
}
if (-not $NodePath) {
  $nodeCommand = Get-Command node -CommandType Application `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($null -ne $nodeCommand) {
    $NodePath = $nodeCommand.Source
  }
}
if (-not $NodePath) {
  $cachedNode = Join-Path $env:USERPROFILE (
    '.cache\codex-runtimes\codex-primary-runtime\dependencies\' +
    'node\bin\node.exe'
  )
  if (Test-Path -LiteralPath $cachedNode -PathType Leaf) {
    $NodePath = $cachedNode
  }
}
if (-not $NodePath) {
  throw (
    'Node.js runtime was not found. Pass -NodePath, set MENTAT_NODE_PATH, ' +
    'or install Node.js with node on PATH.'
  )
}
$NodePath = [IO.Path]::GetFullPath($NodePath)
if (-not (Test-Path -LiteralPath $NodePath -PathType Leaf)) {
  throw (
    "Node.js runtime is missing at '$NodePath'. Pass -NodePath, set " +
    'MENTAT_NODE_PATH, or install Node.js with node on PATH.'
  )
}
if (-not $NodeModulesPath -and $env:MENTAT_NODE_MODULES) {
  $NodeModulesPath = $env:MENTAT_NODE_MODULES
}
if (-not $NodeModulesPath) {
  $moduleCandidates = New-Object Collections.Generic.List[string]
  $moduleCandidates.Add((Join-Path $projectRoot 'node_modules'))
  if ($env:NODE_PATH) {
    foreach ($candidate in (
      $env:NODE_PATH -split [regex]::Escape([string][IO.Path]::PathSeparator)
    )) {
      if (-not [string]::IsNullOrWhiteSpace($candidate)) {
        $moduleCandidates.Add($candidate)
      }
    }
  }
  foreach ($candidate in $moduleCandidates) {
    if (Test-Path -LiteralPath (
      Join-Path $candidate '@oai\artifact-tool'
    ) -PathType Container) {
      $NodeModulesPath = $candidate
      break
    }
  }
}
if (-not $NodeModulesPath) {
  $cachedNodeModules = Join-Path $env:USERPROFILE (
    '.cache\codex-runtimes\codex-primary-runtime\dependencies\' +
    'node\node_modules'
  )
  if (Test-Path -LiteralPath (
    Join-Path $cachedNodeModules '@oai\artifact-tool'
  ) -PathType Container) {
    $NodeModulesPath = $cachedNodeModules
  }
}
if (-not $NodeModulesPath) {
  throw (
    'The @oai/artifact-tool Node module was not found. Pass ' +
    '-NodeModulesPath, set MENTAT_NODE_MODULES, or install ' +
    '@oai/artifact-tool in the project node_modules directory.'
  )
}
$NodeModulesPath = [IO.Path]::GetFullPath($NodeModulesPath)
if (-not (Test-Path -LiteralPath (
  Join-Path $NodeModulesPath '@oai\artifact-tool'
) -PathType Container)) {
  throw (
    "@oai/artifact-tool is missing under '$NodeModulesPath'. Pass " +
    '-NodeModulesPath, set MENTAT_NODE_MODULES, or install ' +
    '@oai/artifact-tool in that node_modules directory.'
  )
}

$DatabasePath = [IO.Path]::GetFullPath($DatabasePath)
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$CommitteeMasterDirectory = [IO.Path]::GetFullPath($CommitteeMasterDirectory)
$BeneficiaryDirectory = [IO.Path]::GetFullPath($BeneficiaryDirectory)
$WorkbookLockPath = [IO.Path]::GetFullPath($WorkbookLockPath)
$exporter = Join-Path $scriptRoot 'export_crypto_spending_data.R'
$builder = Join-Path $scriptRoot 'crypto_workbook_builder.mjs'
$cycleFilterScript = Join-Path $scriptRoot 'filter_industry_cycle_data.mjs'
$finalizer = Join-Path $scriptRoot 'finalize_crypto_excel.ps1'
$beneficiaryModule = Join-Path $projectRoot 'R\fec_beneficiary.R'
$inputCycles = if ($CycleLabel) { @([int]$CycleLabel.Substring(5,4)) } else { @(2022,2024,2026) }
$committeeFiles = @(foreach ($cycle in $inputCycles) {
  $suffix = ([string]$cycle).Substring(2,2)
  Join-Path $CommitteeMasterDirectory "cm$suffix\cm.txt"
})
$beneficiaryFiles = @(foreach ($cycle in $inputCycles) {
  $suffix = ([string]$cycle).Substring(2,2)
  Join-Path $BeneficiaryDirectory "cn$suffix\cn.txt"
  Join-Path $BeneficiaryDirectory "ccl$suffix\ccl.txt"
  Join-Path $BeneficiaryDirectory "leadership$cycle.csv"
  Join-Path $BeneficiaryDirectory "pas2$suffix\itpas2.txt"
})
foreach ($requiredPath in @($DatabasePath,$RScriptPath,$NodePath,$NodeModulesPath,$exporter,$builder,$cycleFilterScript,$finalizer,$beneficiaryModule,$heapPlanner) + $committeeFiles + $beneficiaryFiles) {
  if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Required report input or runtime is missing: $requiredPath" }
}

function Test-JsonFile([string]$Path) {
  $validationScript = "const fs=require('node:fs');JSON.parse(fs.readFileSync(process.argv[1],'utf8'));"
  $priorErrorAction = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    if ((Get-Item -LiteralPath $Path).Length -ge 480MB) {
      & $PythonPath (Join-Path $scriptRoot 'validate_report_json.py') $Path | Out-Null
    } else {
      & $NodePath '--max-old-space-size=8192' '-e' $validationScript $Path 2>$null | Out-Null
    }
    $valid = $LASTEXITCODE -eq 0
  } finally {
    $ErrorActionPreference = $priorErrorAction
  }
  $valid
}

function Get-CombinedFingerprint([string[]]$Paths, [string]$Salt = '') {
  $lines = @("salt|$Salt") + @(foreach ($path in $Paths) {
    $item = Get-Item -LiteralPath $path
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    "$($item.Name)|$($item.Length)|$hash"
  })
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
  } finally { $sha.Dispose() }
}

function Write-AtomicText([string]$Path, [string]$Text) {
  $temporary = "$Path.pending-$PID"
  [IO.File]::WriteAllText($temporary, $Text, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Assert-WorkbookPackage([string]$Path, [int]$ExpectedWorksheetCount) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $archive = [IO.Compression.ZipFile]::OpenRead($Path)
  try {
    $names = @($archive.Entries | ForEach-Object { $_.FullName })
    if ('[Content_Types].xml' -notin $names -or 'xl/workbook.xml' -notin $names) {
      throw 'Workbook ZIP package is missing required Open XML parts.'
    }
    $worksheetCount = @($names | Where-Object { $_ -match '^xl/worksheets/sheet\d+\.xml$' }).Count
    if ($worksheetCount -ne $ExpectedWorksheetCount) { throw "Workbook contains $worksheetCount worksheets; expected $ExpectedWorksheetCount." }

    $tableIds = [Collections.Generic.HashSet[int]]::new()
    $tableNames = [Collections.Generic.HashSet[string]]::new(
      [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($entry in @($archive.Entries | Where-Object {
      $_.FullName -match '^xl/tables/table\d+\.xml$'
    })) {
      $reader = [IO.StreamReader]::new($entry.Open())
      try { [xml]$tableXml = $reader.ReadToEnd() } finally { $reader.Dispose() }
      $table = $tableXml.DocumentElement
      $tableId = [int]$table.GetAttribute('id')
      $tableName = $table.GetAttribute('displayName')
      $tableRef = $table.GetAttribute('ref')
      if (-not $tableIds.Add($tableId)) {
        throw "Workbook has duplicate Excel table id $tableId."
      }
      if (-not $tableName -or -not $tableNames.Add($tableName)) {
        throw "Workbook has a blank or duplicate Excel table name '$tableName'."
      }
      if ($tableRef -notmatch '^[A-Z]+(\d+):[A-Z]+(\d+)$') {
        throw "Excel table '$tableName' has invalid range '$tableRef'."
      }
      if ([int]$Matches[2] -le [int]$Matches[1]) {
        throw "Excel table '$tableName' has no data row."
      }
      $namespace = [Xml.XmlNamespaceManager]::new($tableXml.NameTable)
      $namespace.AddNamespace('m', 'http://schemas.openxmlformats.org/spreadsheetml/2006/main')
      $columns = @($tableXml.SelectNodes('/m:table/m:tableColumns/m:tableColumn', $namespace))
      $declaredCount = [int]$tableXml.SelectSingleNode(
        '/m:table/m:tableColumns',
        $namespace
      ).GetAttribute('count')
      if ($columns.Count -ne $declaredCount) {
        throw "Excel table '$tableName' has a table-column count mismatch."
      }
      $columnNames = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
      )
      for ($index = 0; $index -lt $columns.Count; $index++) {
        $columnId = [int]$columns[$index].GetAttribute('id')
        $columnName = $columns[$index].GetAttribute('name')
        if ($columnId -ne $index + 1) {
          throw "Excel table '$tableName' has non-sequential column ids."
        }
        if (-not $columnName -or -not $columnNames.Add($columnName)) {
          throw "Excel table '$tableName' has a blank or duplicate column name '$columnName'."
        }
      }
    }

    foreach ($entry in @($archive.Entries | Where-Object {
      $_.FullName -match '^xl/worksheets/sheet\d+\.xml$'
    })) {
      # Streamed, never loaded whole: a worksheet part can be hundreds of MB (a
      # Community Banks Beneficiary Bridge was 659 MB), past what an in-memory [xml]
      # cast accepts ("exceeded a limit set by MaxCharactersInDocument"). Only the
      # worksheet's own children are read, and each one - sheetData included - is
      # skipped whole (lane W, 2026-09-26).
      $sheetSettings = [Xml.XmlReaderSettings]::new()
      $sheetSettings.DtdProcessing = [Xml.DtdProcessing]::Prohibit
      $sheetSettings.IgnoreComments = $true
      $sheetSettings.IgnoreWhitespace = $true
      $hasSheetFilter = $false
      $hasTables = $false
      $sheetStream = $entry.Open()
      try {
        $sheetReader = [Xml.XmlReader]::Create($sheetStream, $sheetSettings)
        try {
          [void]$sheetReader.MoveToContent()
          if (-not $sheetReader.IsEmptyElement) {
            [void]$sheetReader.Read()
            while (-not $sheetReader.EOF -and $sheetReader.Depth -ge 1) {
              if ($sheetReader.NodeType -ne [Xml.XmlNodeType]::Element) {
                [void]$sheetReader.Read()
                continue
              }
              if ($sheetReader.NamespaceURI -eq 'http://schemas.openxmlformats.org/spreadsheetml/2006/main') {
                if ($sheetReader.LocalName -eq 'autoFilter') { $hasSheetFilter = $true }
                if ($sheetReader.LocalName -eq 'tableParts') { $hasTables = $true }
              }
              $sheetReader.Skip()
            }
          }
        } finally { $sheetReader.Dispose() }
      } finally { $sheetStream.Dispose() }
      if ($hasSheetFilter -and $hasTables) {
        throw "Worksheet part '$($entry.FullName)' has an overlapping worksheet AutoFilter and Excel table."
      }
    }
  } finally { $archive.Dispose() }
}

function Get-QuiescentDatabaseSha256([string]$Path) {
  $walPath = "$Path-wal"
  foreach ($phase in @('before', 'after')) {
    if (
      (Test-Path -LiteralPath $walPath -PathType Leaf) -and
      (Get-Item -LiteralPath $walPath).Length -gt 0
    ) {
      throw (
        "Source database has a nonempty WAL sidecar $phase fingerprinting " +
        "and is not a quiescent snapshot: $walPath. Stop writers and " +
        "checkpoint SQLite, or build from a private snapshot."
      )
    }
    if ($phase -eq 'before') {
      $hash = (
        Get-FileHash -LiteralPath $Path -Algorithm SHA256
      ).Hash.ToLowerInvariant()
    }
  }
  $hash
}

# The cross-reference is an auditable side computation and the published
# workbook is the accounting of record (rulings of 2026-09-17), so losing the
# network must not block publication: an ONLINE run that exits non-zero is
# retried once against saved evidence with --offline, and the build continues
# if that succeeds. run_cross_reference.py has no exit code separating a
# network failure from a logic one - its main() lets every error raise, so the
# interpreter exits 1 for all of them - so the retry itself decides the class:
# a genuine tool failure (a lost source record, a broken engine invariant) is
# computed identically offline and still stops the build. An operator who
# asked for -CrossReferenceOffline has no network to lose and is never retried.
# The mode travels by REFERENCE, never as the return value: run_cross_reference.py
# streams a JSON line per step and a closing summary to stdout, so a returned
# mode would ride the same success stream - making the receipt field an array of
# those lines and stealing them from the log the direct report tees in-process.
function Invoke-MentatCrossReference {
  param(
    [Parameter(Mandatory = $true)][object[]]$Arguments,
    [Parameter(Mandatory = $true)][bool]$Offline,
    [Parameter(Mandatory = $true)][scriptblock]$Runner,
    [Parameter(Mandatory = $true)][ref]$Mode
  )
  $failure = 'Automatic FEC cross-referencing failed. Review the saved process evidence before exporting.'
  if ($Offline) {
    & $Runner ($Arguments + '--offline')
    if ($LASTEXITCODE -ne 0) { throw $failure }
    $Mode.Value = 'offline'
    return
  }
  & $Runner $Arguments
  if ($LASTEXITCODE -eq 0) { $Mode.Value = 'online'; return }
  Write-Warning (
    "Automatic FEC cross-referencing failed online with exit code $LASTEXITCODE; " +
    'retrying it once against saved evidence with --offline.'
  )
  & $Runner ($Arguments + '--offline')
  if ($LASTEXITCODE -ne 0) { throw $failure }
  Write-Warning (
    'Cross-reference completed OFFLINE after an online failure. No new FEC ' +
    'evidence was recovered for this build; review the saved process evidence.'
  )
  $Mode.Value = 'offline_fallback'
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$cacheRoot = Join-Path $projectRoot ('.cache\industry-report\' + $industryToken)
$databaseHash = Get-QuiescentDatabaseSha256 $DatabasePath
$sourceGuard = Join-Path $scriptRoot 'report_source_guard.py'
$sourceGuardDirectory = Join-Path $projectRoot 'state\accepted-report-sources'
if ($env:MENTAT_ACCEPTED_SOURCE_ROOT) { $sourceGuardDirectory = $env:MENTAT_ACCEPTED_SOURCE_ROOT }
$sourceGuardReceipt = Join-Path $sourceGuardDirectory ("runs\$industryToken-" + [Guid]::NewGuid().ToString('N') + '.json')
$sourceGuardCycles = if ($CycleLabel) { $CycleLabel.Substring(5,4) } else { '2022,2024,2026' }
# Reconciliation runs on every build, including workbook cache hits. Its own
# decision ledger explains source gaps before the publication gate can reject
# them. An unresolved relationship is reported; a broken invariant fails.
$crossReference = Join-Path $scriptRoot 'run_cross_reference.py'
$crossReferenceArguments = @($crossReference, '--database', $DatabasePath,
  '--industry', $Industry, '--cycles', $sourceGuardCycles,
  '--baseline-root', $sourceGuardDirectory, '--max-requests', $CrossReferenceMaxRequests)
$crossReferenceMode = ''
Invoke-MentatCrossReference `
  -Arguments $crossReferenceArguments `
  -Offline ([bool]$CrossReferenceOffline) `
  -Runner { param([object[]]$CommandArguments) & $PythonPath @CommandArguments } `
  -Mode ([ref]$crossReferenceMode)
& $PythonPath $sourceGuard prepare --database $DatabasePath --industry $Industry --cycles $sourceGuardCycles --state-dir $sourceGuardDirectory --receipt $sourceGuardReceipt
if ($LASTEXITCODE -ne 0) { throw "Source comparison failed. See $sourceGuardReceipt" }
$databaseToken = $databaseHash.Substring(0, 16)
# A per-cycle build exports only that cycle, so it needs its own payload and
# manifest - sharing one industry_data.json across three -CycleLabel runs would
# make each run a cache hit on the previous cycle's data.
$cycleToken = if ($CycleLabel) { '-' + ($CycleLabel -replace '[^0-9]', '') } else { '' }
$cacheVariantToken = if ($CycleLabel) {
  $CycleLabel -replace '[^0-9]+','_'
} else {
  'all_cycles'
}
$stageDirectory = Join-Path $cacheRoot ("stage-" + $cacheVariantToken)
New-Item -ItemType Directory -Path $cacheRoot,$stageDirectory -Force | Out-Null
$dataPath = Join-Path $cacheRoot (
  "industry_data$cycleToken-$databaseToken.json"
)
$manifestPath = Join-Path $cacheRoot (
  "manifest$cycleToken-$databaseToken.json"
)
# Tidy staging store: the typed, tall SQLite the R exporter writes beside the
# JSON payload and the companion Data workbook is built from. Named from the
# SAME tokens as $dataPath so a per-cycle build gets its own store and never
# reads the all-cycles one. Declared HERE, before $cycleToken is reassigned for
# the per-cycle projection below.
$stagePath = Join-Path $cacheRoot (
  "tidy$cycleToken-$databaseToken.sqlite"
)
$tidyWriter = Join-Path $projectRoot 'tools\build_tidy_workbook.py'
$tidyStageModule = Join-Path $projectRoot 'R\tidy_stage.R'
# Set BEFORE the export, not just around the workbook step: the exporter reads it
# to narrow the payload, which is what keeps a large industry under V8's
# 536,870,888-byte JSON string limit. Telecom's full payload is 591,338,574 and
# cannot be parsed by the validator or the builder at all.
$env:MENTAT_CYCLE_FILTER = $CycleLabel
# Every file whose contents can change export output must be fingerprinted, or a
# code fix silently republishes the pre-fix artifact as a cache hit. This list
# previously held only the exporter and fec_beneficiary.R, so edits to report.R
# (recipient classification), identity_components.R (person grouping) or the
# trade-association override CSV did not invalidate the cache.
$dataInputPaths = @(
  $DatabasePath,
  $exporter,
  $beneficiaryModule,
  $MyInvocation.MyCommand.Path,
  (Join-Path $projectRoot 'R\report.R'),
  (Join-Path $projectRoot 'R\identity_components.R'),
  (Join-Path $projectRoot 'R\state_destinations.R'),
  (Join-Path $projectRoot 'R\ingest.R'),
  (Join-Path $projectRoot 'R\collect.R'),
  (Join-Path $projectRoot 'inputs\fec_receipt_rules.json'),
  (Join-Path $projectRoot 'config\attribution-policy.json'),
  (Join-Path $projectRoot 'inputs\verified_company_affiliations.json'),
  (Join-Path $projectRoot 'R\paths.R'),
  (Join-Path $projectRoot 'R\schema.R'),
  (Join-Path $projectRoot 'R\reconcile.R'),
  # export_guards.R gates publication rather than mutating the payload, so on a
  # cache hit the export never runs and a newly added or tightened guard is
  # silently skipped. Fingerprinting it forces the re-run that lets it fire.
  (Join-Path $projectRoot 'R\export_guards.R'),
  # tidy_stage.R shapes the staging store the companion Data workbook is built
  # from. It produces no JSON-payload output at all, so without this a change to
  # a staging column would be a cache hit and republish the old store.
  $tidyStageModule,
  (Join-Path $projectRoot 'R\fec_transfer_graph.R'),
  (Join-Path $projectRoot 'tools\fec_transfer_graph.py'),
  (Join-Path $projectRoot 'tools\fec_filing_headers.py'),
  $sourceGuard,
  (Join-Path $projectRoot 'tools\enrich_downstream.py'),
  (Join-Path $projectRoot 'tools\crypto_workbook_builder.mjs'),
  (Join-Path $projectRoot 'tools\finalize_crypto_excel.ps1'),
  (Join-Path $projectRoot 'inputs\trade_committee_overrides.csv')
) + $committeeFiles + $beneficiaryFiles + @(Get-ChildItem -LiteralPath $BeneficiaryDirectory -Recurse -File | Where-Object { $_.Name -in @('itoth.txt','transfer-current.json','transfer-info.json','transfer-edges.csv') } | ForEach-Object FullName) + @(Get-ChildItem -LiteralPath $sourceRoot -Recurse -Filter 'source-receipt.json' | ForEach-Object FullName)
$dataInputPaths = $dataInputPaths | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$inputFingerprint = Get-CombinedFingerprint $dataInputPaths (
  "industry=$Industry|cycle=$CycleLabel"
)
$cacheHit = $false
if (-not $NoCache -and (Test-Path -LiteralPath $dataPath) -and (Test-Path -LiteralPath $manifestPath)) {
  try {
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $actualDataHash = (
      Get-FileHash -LiteralPath $dataPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $cacheHit = (
      [string]$manifest.input_fingerprint -eq $inputFingerprint -and
      [string]$manifest.database_sha256 -eq $databaseHash -and
      [string]$manifest.cycle_label -eq $CycleLabel -and
      [string]$manifest.data_sha256 -eq $actualDataHash -and
      [long]$manifest.data_bytes -eq (Get-Item -LiteralPath $dataPath).Length
    )
    if ($cacheHit) { $cacheHit = Test-JsonFile $dataPath }
    if ($cacheHit) {
      # A data cache hit skips the export entirely, and the export is the only
      # thing that writes the staging store. Without this the first build after
      # tidy staging landed would hit the cache, find no store, and publish an
      # Overview with no companion Data workbook - or, worse, republish a store
      # produced under different code.
      $cacheHit = (Test-Path -LiteralPath $stagePath -PathType Leaf) -and
        ([string]$manifest.stage_sha256 -eq (
          Get-FileHash -LiteralPath $stagePath -Algorithm SHA256
        ).Hash.ToLowerInvariant())
    }
  } catch { $cacheHit = $false }
}

$overallWatch = [Diagnostics.Stopwatch]::StartNew()
$exportSeconds = 0.0
if (-not $cacheHit) {
  $exportWatch = [Diagnostics.Stopwatch]::StartNew()
  $pendingData = "$dataPath.pending-$PID"
  # Sixth positional argument. The exporter treats an absent one as "skip
  # staging", so this script and the exporter can be applied in either order.
  $pendingStage = "$stagePath.pending-$PID"
  if (Test-Path -LiteralPath $pendingStage) {
    Remove-Item -LiteralPath $pendingStage -Force
  }
  & $RScriptPath $exporter $DatabasePath $pendingData $CommitteeMasterDirectory $BeneficiaryDirectory $Industry $pendingStage
  if ($LASTEXITCODE -ne 0) { throw "$Industry data export failed with exit code $LASTEXITCODE" }
  & $PythonPath (Join-Path $projectRoot 'tools\enrich_downstream.py') $pendingData $BeneficiaryDirectory --staging $pendingStage
  if ($LASTEXITCODE -ne 0) { throw 'Transfer allocation and coverage enrichment failed.' }
  if (-not (Test-JsonFile $pendingData)) {
    throw "$Industry data export did not produce valid JSON."
  }
  # Integrity-check the store the same way the payload is checked, and for the
  # same reason: a truncated SQLite file opens without error and answers most
  # queries. PRAGMA integrity_check walks the b-trees; the receipt count proves
  # the R side actually ran rather than the file holding only the seven
  # enrichment tables.
  if (-not (Test-Path -LiteralPath $pendingStage -PathType Leaf)) {
    throw "$Industry data export did not produce a tidy staging store: $pendingStage"
  }
  # Single quotes inside the program, never double: Windows PowerShell 5.1, the host this
  # script documents, passes a native argument's embedded double quotes unescaped, so
  # python received the program cut at the first one and every uncached build failed
  # here (lane W, 2026-09-26; Test-MentatWorkbookBuilderGuards.ps1 runs this line).
  $stageProbe = @(& $PythonPath -c 'import sqlite3,sys;connection=sqlite3.connect(sys.argv[1]);print(connection.execute(''PRAGMA integrity_check'').fetchone()[0]);print(connection.execute(''SELECT COUNT(*) FROM contributions'').fetchone()[0])' $pendingStage)
  if ($LASTEXITCODE -ne 0) {
    throw "Tidy staging store could not be read: $pendingStage"
  }
  if ($stageProbe.Count -lt 2 -or $stageProbe[0].Trim() -ne 'ok') {
    throw "Tidy staging store failed its integrity check: $pendingStage"
  }
  if ([long]$stageProbe[1].Trim() -le 0) {
    throw "Tidy staging store holds no receipt rows; refusing to publish an empty companion workbook."
  }
  $databaseHashAfterExport = Get-QuiescentDatabaseSha256 $DatabasePath
  if ($databaseHashAfterExport -ne $databaseHash) {
    throw (
      "Source database changed during the $Industry export; refusing to " +
      "publish data under a stale database fingerprint."
    )
  }
  $afterFingerprint = Get-CombinedFingerprint $dataInputPaths ("industry=$Industry|cycle=$CycleLabel")
  if ($afterFingerprint -ne $inputFingerprint) {
    throw 'Report code or source inputs changed during export; refusing to publish mixed inputs.'
  }
  Move-Item -LiteralPath $pendingData -Destination $dataPath -Force
  Move-Item -LiteralPath $pendingStage -Destination $stagePath -Force
  $dataItem = Get-Item -LiteralPath $dataPath
  $dataHash = (
    Get-FileHash -LiteralPath $dataPath -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  $stageHash = (
    Get-FileHash -LiteralPath $stagePath -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  Write-AtomicText $manifestPath ([pscustomobject]@{
    schema_version = 2
    input_fingerprint = $inputFingerprint
    database_sha256 = $databaseHash
    cycle_label = $CycleLabel
    data_sha256 = $dataHash
    data_bytes = [long]$dataItem.Length
    stage_sha256 = $stageHash
    stage_bytes = [long](Get-Item -LiteralPath $stagePath).Length
    guard_receipt = 'embedded:metadata.export_guards'
    generated_at = [DateTimeOffset]::Now.ToString('o')
  } | ConvertTo-Json)
  $exportWatch.Stop(); $exportSeconds = $exportWatch.Elapsed.TotalSeconds
}

$buildDataPath = $dataPath
if ($CycleLabel) {
  $cycleToken = $CycleLabel -replace '[^0-9]+','_'
  $cycleDataPath = Join-Path $cacheRoot (
    "industry_data_cycle_${cycleToken}-$databaseToken.json"
  )
  $cycleManifestPath = Join-Path $cacheRoot (
    "industry_data_cycle_${cycleToken}-$databaseToken.manifest.json"
  )
  $sourceDataHash = (
    Get-FileHash -LiteralPath $dataPath -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  $filterScriptHash = (
    Get-FileHash -LiteralPath $cycleFilterScript -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  $cycleCacheHit = $false
  if (
    -not $NoCache -and
    (Test-Path -LiteralPath $cycleDataPath -PathType Leaf) -and
    (Test-Path -LiteralPath $cycleManifestPath -PathType Leaf)
  ) {
    try {
      $cycleManifest = Get-Content -Raw -LiteralPath (
        $cycleManifestPath
      ) | ConvertFrom-Json
      $actualCycleHash = (
        Get-FileHash -LiteralPath $cycleDataPath -Algorithm SHA256
      ).Hash.ToLowerInvariant()
      $cycleCacheHit = (
        [string]$cycleManifest.source_data_sha256 -eq $sourceDataHash -and
        [string]$cycleManifest.filter_script_sha256 -eq $filterScriptHash -and
        [string]$cycleManifest.cycle_label -eq $CycleLabel -and
        [string]$cycleManifest.output_sha256 -eq $actualCycleHash -and
        (Test-JsonFile $cycleDataPath)
      )
    } catch { $cycleCacheHit = $false }
  }
  if (
    -not $cycleCacheHit
  ) {
    $pendingCycleData = "$cycleDataPath.pending-$PID"
    & $NodePath '--max-old-space-size=4096' $cycleFilterScript $dataPath $pendingCycleData $CycleLabel
    if ($LASTEXITCODE -ne 0) {
      throw "Cycle cache build failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-JsonFile $pendingCycleData)) {
      throw "Cycle cache builder produced invalid JSON: $pendingCycleData"
    }
    Move-Item -LiteralPath $pendingCycleData -Destination $cycleDataPath -Force
    $cycleOutputHash = (
      Get-FileHash -LiteralPath $cycleDataPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    Write-AtomicText $cycleManifestPath ([pscustomobject]@{
      schema_version = 1
      source_data_sha256 = $sourceDataHash
      filter_script_sha256 = $filterScriptHash
      cycle_label = $CycleLabel
      output_sha256 = $cycleOutputHash
      generated_at = [DateTimeOffset]::Now.ToString('o')
    } | ConvertTo-Json)
  } else {
    Write-Host "Cycle cache hit: $cycleDataPath"
  }
  if (-not (Test-JsonFile $cycleDataPath)) {
    throw "Cycle cache is not valid JSON: $cycleDataPath"
  }
  $buildDataPath = $cycleDataPath
}

$priorDefer = $env:MENTAT_DEFER_FORMULAS
$priorModules = $env:MENTAT_NODE_MODULES
$priorRender = $env:MENTAT_RENDER_PREVIEWS
$priorIndustry = $env:MENTAT_INDUSTRY
$priorCompactRaw = $env:MENTAT_COMPACT_RAW
$priorFormulaFacts = $env:MENTAT_FORMULA_FACTS
$priorCycleFilter = $env:MENTAT_CYCLE_FILTER
$largePayloadThresholdBytes = 120MB
$compactRawMode = if (
  $priorCompactRaw -in @('0', '1')
) {
  $priorCompactRaw
} elseif (
  (Get-Item -LiteralPath $dataPath).Length -ge
    $largePayloadThresholdBytes
) {
  '1'
} else {
  '0'
}
$formulaFactsMode = if (
  $priorFormulaFacts -in @('0', '1')
) {
  $priorFormulaFacts
} elseif (
  (Get-Item -LiteralPath $dataPath).Length -ge
    $largePayloadThresholdBytes
) {
  '1'
} else {
  '0'
}

$stageWorkbook = Join-Path $stageDirectory "${reportBaseName}.xlsx"
if ((Get-Item -LiteralPath $buildDataPath).Length -ge 256MB -and $compactRawMode -eq '1' -and $formulaFactsMode -eq '1') {
  $workbookDataPath = Join-Path $cacheRoot ("workbook_data${cycleToken}-$databaseToken.json")
  & $PythonPath (Join-Path $projectRoot 'tools\prepare_workbook_payload.py') $buildDataPath $workbookDataPath
  if ($LASTEXITCODE -ne 0) { throw 'Large workbook payload preparation failed.' }
  $buildDataPath = $workbookDataPath
}
$finalizerDataPath = Join-Path $stageDirectory 'finalizer_data.json'
$workbookCacheManifestPath = Join-Path $cacheRoot (
  "workbook_manifest_${cacheVariantToken}.json"
)
$artifactToolModule = Join-Path $NodeModulesPath (
  '@oai\artifact-tool\dist\artifact_tool.mjs'
)
$workbookFingerprintInputs = @(
  $buildDataPath,
  $builder,
  $finalizer,
  (Join-Path $projectRoot 'tools\read_report_payload.mjs'),
  (Join-Path $projectRoot 'tools\prepare_workbook_payload.py'),
  $MyInvocation.MyCommand.Path
)
if (Test-Path -LiteralPath $artifactToolModule -PathType Leaf) {
  $workbookFingerprintInputs += $artifactToolModule
}
# The tidy writer decides the companion Data workbook's sheets, types and
# receipt. Guarded the same way as the artifact tool because the two lanes that
# produce this feature can land in either order; an absent writer simply means
# no Data workbook is published on this build.
if (Test-Path -LiteralPath $tidyWriter -PathType Leaf) {
  $workbookFingerprintInputs += $tidyWriter
}
$workbookFingerprintSalt = (
  (
    'template={0}|industry={1}|cycle={2}|compact_raw={3}|' +
    'formula_facts={4}|render_previews={5}|source_db={6}'
  ) -f
      $templateContract,
      $Industry,
      $CycleLabel,
      $compactRawMode,
      $formulaFactsMode,
      [int][bool]$RenderPreviews,
      $databaseHash
)
$workbookFingerprint = Get-CombinedFingerprint `
  -Paths $workbookFingerprintInputs `
  -Salt $workbookFingerprintSalt
$workbookCacheHit = $false
$formulaReceiptObject = $null
$expectedWorksheetCount = 0
$buildWatch = New-Object Diagnostics.Stopwatch
$formulaWatch = New-Object Diagnostics.Stopwatch
$finalizeWatch = New-Object Diagnostics.Stopwatch
$workbookLockOwned = $false
$workbookLockStream = $null
$workbookLockToken = [Guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path (
  Split-Path -Parent $WorkbookLockPath
) -Force | Out-Null
Write-Host "Waiting for workbook build slot: $WorkbookLockPath"
while (-not $workbookLockOwned) {
  try {
    $workbookLockStream = [IO.File]::Open(
      $WorkbookLockPath,
      [IO.FileMode]::CreateNew,
      [IO.FileAccess]::Write,
      [IO.FileShare]::None
    )
    try {
      $lockText = (
        "pid=$PID`r`ntoken=$workbookLockToken`r`n" +
        "created=$([DateTimeOffset]::Now.ToString('o'))`r`n"
      )
      $lockBytes = [Text.Encoding]::UTF8.GetBytes($lockText)
      $workbookLockStream.Write($lockBytes, 0, $lockBytes.Length)
      $workbookLockStream.Flush()
      $workbookLockOwned = $true
    } catch {
      $workbookLockStream.Dispose()
      $workbookLockStream = $null
      throw
    }
  } catch [IO.IOException] {
    if (
      (Test-Path -LiteralPath $WorkbookLockPath) -and
      (Get-Item -LiteralPath $WorkbookLockPath).LastWriteTimeUtc -lt
      [DateTime]::UtcNow.AddMinutes(-90)
    ) {
      $observedLock = ''
      try {
        $observedLock = Get-Content -Raw -LiteralPath $WorkbookLockPath
      } catch { $observedLock = '' }
      $ownerPid = 0
      if ($observedLock -match '(?m)^pid=(\d+)\s*$') {
        $ownerPid = [int]$Matches[1]
      }
      $ownerAlive = $false
      if ($ownerPid -gt 0) {
        $ownerAlive = $null -ne (
          Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        )
      }
      if (-not $ownerAlive -and $observedLock) {
        $confirmedLock = ''
        try {
          $confirmedLock = Get-Content -Raw -LiteralPath $WorkbookLockPath
        } catch { $confirmedLock = '' }
        if ($confirmedLock -eq $observedLock) {
          try {
            Write-Warning "Reclaiming abandoned workbook build lock: $WorkbookLockPath"
            Remove-Item -LiteralPath $WorkbookLockPath -Force -ErrorAction Stop
            continue
          } catch {
            # A contender acquired and opened the lease after our confirmation.
            # Its FileShare.None handle is the authority; wait rather than steal.
          }
        }
      }
    }
    Start-Sleep -Seconds 1
  }
}
Write-Host "Workbook build slot acquired: $WorkbookLockPath"

try {
  if (
    -not $NoCache -and
    (Test-Path -LiteralPath $stageWorkbook -PathType Leaf) -and
    (Test-Path -LiteralPath $workbookCacheManifestPath -PathType Leaf)
  ) {
    $finalizeWatch.Start()
    try {
      $workbookManifest = Get-Content -Raw -LiteralPath (
        $workbookCacheManifestPath
      ) | ConvertFrom-Json
      $cachedWorkbookSha256 = (
        Get-FileHash -LiteralPath $stageWorkbook -Algorithm SHA256
      ).Hash.ToLowerInvariant()
      $workbookCacheHit = (
        [string]$workbookManifest.workbook_fingerprint -eq
          $workbookFingerprint -and
        [string]$workbookManifest.workbook_sha256 -eq
          $cachedWorkbookSha256
      )
      if ($workbookCacheHit) {
        $expectedWorksheetCount = [int](
          $workbookManifest.expected_worksheet_count
        )
        $formulaReceiptObject = $workbookManifest.formula_receipt
        if ($expectedWorksheetCount -lt 16 -or $null -eq $formulaReceiptObject) {
          $workbookCacheHit = $false
        } else {
          Assert-WorkbookPackage $stageWorkbook $expectedWorksheetCount
          # Carried out of the cache-hit branch so the manifest rewrite that
          # records data_workbook_sha256 below does not have to re-hash the
          # Overview, and cannot record an empty hash on a cache hit.
          $workbookSha256 = $cachedWorkbookSha256
        }
      }
    } catch {
      $workbookCacheHit = $false
      $formulaReceiptObject = $null
      $expectedWorksheetCount = 0
    } finally {
      $finalizeWatch.Stop()
    }
  }

  if (-not $workbookCacheHit) {
    try {
    if ($NodeHeapMb -eq 0) {
      # Measure immediately before Node starts, after any arbitrarily long lock
      # wait. Cache hits never need a heap plan at all.
      $detectedPhysicalMb = 0
      try {
        Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
        $computerInfo = [Microsoft.VisualBasic.Devices.ComputerInfo]::new()
        $detectedPhysicalMb = [int][math]::Floor(
          [double]$computerInfo.TotalPhysicalMemory / 1MB
        )
      } catch { $detectedPhysicalMb = 0 }
      $availableMb = 0
      try {
        $osInfo = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        # FreePhysicalMemory is in KB.
        $availableMb = [int][math]::Floor(
          [double]$osInfo.FreePhysicalMemory / 1KB
        )
      } catch { $availableMb = 0 }
      # Chunked input avoids a second full JSON string in V8. The Banking
      # end-to-end build (933 MB projection) passes with a 6 GB heap.
      $minimumHeapMb = if (
        (Get-Item -LiteralPath $buildDataPath).Length -ge 480MB -and
        (Test-Path -LiteralPath "$buildDataPath.parts\index.json" -PathType Leaf)
      ) { 6144 } else { 8192 }
      $heapPlan = Get-MentatNodeHeapPlan `
        -TotalPhysicalMb $detectedPhysicalMb `
        -AvailableMb $availableMb `
        -MinimumMb $minimumHeapMb
      Write-Host (
        'Node heap sizing: total={0}MB available={1}MB safe_cap={2}MB status={3}' -f
          $detectedPhysicalMb,
          $availableMb,
          $heapPlan.safe_cap_mb,
          $heapPlan.reason
      )
      if (-not $heapPlan.ready) {
        throw (
          "Automatic Node heap sizing cannot safely provide the $minimumHeapMb MB " +
          "minimum (total=$detectedPhysicalMb MB; available=$availableMb MB; " +
          "safe cap=$($heapPlan.safe_cap_mb) MB). Wait for collection/build " +
          'processes to release memory, or pass -NodeHeapMb explicitly only ' +
          'after verifying that capacity is available.'
        )
      }
      $NodeHeapMb = [int]$heapPlan.heap_mb
    }
    Write-Host (
      'Workbook RAM plan: node_heap_mb={0} performance_budget_minutes={1:n2}' -f
        $NodeHeapMb,
        $PerformanceBudgetMinutes
    )
    $env:MENTAT_INDUSTRY = $Industry
    $env:MENTAT_DEFER_FORMULAS = '1'
    $env:MENTAT_NODE_MODULES = $NodeModulesPath
    $env:MENTAT_RENDER_PREVIEWS = if ($RenderPreviews) { '1' } else { '0' }
    # artifact-tool uses an in-memory managed workbook. Preserve every accepted
    # row but omit audit-only raw columns from large Excel payloads; the complete
    # audit trail remains in the source database and export JSON.
    $env:MENTAT_COMPACT_RAW = $compactRawMode
    $env:MENTAT_FORMULA_FACTS = $formulaFactsMode
    $env:MENTAT_CYCLE_FILTER = $CycleLabel
    $buildWatch.Start()
    & $NodePath "--max-old-space-size=$NodeHeapMb" $builder $buildDataPath $stageDirectory $databaseHash
    if ($LASTEXITCODE -ne 0) { throw "Workbook build failed with exit code $LASTEXITCODE" }
    $buildWatch.Stop()
    } finally {
      $env:MENTAT_DEFER_FORMULAS = $priorDefer
      $env:MENTAT_NODE_MODULES = $priorModules
      $env:MENTAT_RENDER_PREVIEWS = $priorRender
      $env:MENTAT_INDUSTRY = $priorIndustry
      $env:MENTAT_COMPACT_RAW = $priorCompactRaw
      $env:MENTAT_FORMULA_FACTS = $priorFormulaFacts
      $env:MENTAT_CYCLE_FILTER = $priorCycleFilter
    }

    if (-not (Test-Path -LiteralPath $stageWorkbook)) { throw "Workbook builder did not create $stageWorkbook" }
    if (-not (Test-Path -LiteralPath $finalizerDataPath)) {
      throw "Workbook builder did not create $finalizerDataPath"
    }
    if (-not (Test-JsonFile $finalizerDataPath)) {
      throw "Workbook builder created invalid finalizer data."
    }
    $formulaWatch.Start()
    $formulaReceipt = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $finalizer -WorkbookPath $stageWorkbook -JsonPath $finalizerDataPath -ExcelMutexName $ExcelMutexName
    if ($LASTEXITCODE -ne 0) { throw "Excel formula finalization failed with exit code $LASTEXITCODE" }
    $formulaWatch.Stop()
    $formulaReceiptObject = (($formulaReceipt -join "`n") | ConvertFrom-Json)
    $expectedWorksheetCount = [int]$formulaReceiptObject.expected_worksheet_count
    if ($expectedWorksheetCount -lt 16) {
      throw "Excel formula finalizer returned an invalid worksheet count."
    }
    $finalizeWatch.Start()
    try {
      Assert-WorkbookPackage $stageWorkbook $expectedWorksheetCount
      $workbookSha256 = (
        Get-FileHash -LiteralPath $stageWorkbook -Algorithm SHA256
      ).Hash.ToLowerInvariant()
      Write-AtomicText $workbookCacheManifestPath ([pscustomobject]@{
        workbook_fingerprint = $workbookFingerprint
        workbook_sha256 = $workbookSha256
        template_contract = $templateContract
        expected_worksheet_count = $expectedWorksheetCount
        generated_at = [DateTimeOffset]::Now.ToString('o')
        formula_receipt = $formulaReceiptObject
      } | ConvertTo-Json -Depth 10)
    } finally {
      $finalizeWatch.Stop()
    }
  } else {
    Write-Host (
      'Workbook cache hit: verified package and SHA-256; skipping Node build ' +
      'and Excel formula finalization.'
    )
  }

  $targetWorkbook = Join-Path $OutputDirectory "${reportBaseName}.xlsx"
  $pendingWorkbook = "$targetWorkbook.pending-$PID"
  $dataReportBaseName = if ($CycleLabel) {
    "${Industry} Industry Spending Data ${CycleLabel}"
  } else {
    "${Industry} Industry Spending Data"
  }
  $stageDataWorkbook = Join-Path $stageDirectory "${dataReportBaseName}.xlsx"
  $targetDataWorkbook = $null
  $dataWorkbookSha256 = $null
  $dataWorksheetCount = 0
  $finalizeWatch.Start()
  try {
    # Run on cache hits too, against the full payload under the publication lock.
    & $PythonPath $sourceGuard check --receipt $sourceGuardReceipt --payload $dataPath
    if ($LASTEXITCODE -ne 0) { throw "Dollar reconciliation failed. See $sourceGuardReceipt" }
    # Companion Data workbook. Built AFTER the dollar reconciliation and BEFORE
    # anything is copied into the output folder, so a writer failure leaves the
    # previously published pair untouched rather than publishing an Overview
    # whose companion is stale or missing.
    if (Test-Path -LiteralPath $tidyWriter -PathType Leaf) {
      # The Excel finalizer emits its receipt on STDOUT, so there is no file to
      # hand the writer. Persist the object that was captured (fresh build) or
      # replayed from the workbook cache manifest (cache hit): either way it
      # carries the Excel-computed cycle_totals the writer reconciles against.
      $overviewReceiptPath = Join-Path $stageDirectory 'formula_receipt.json'
      Write-AtomicText $overviewReceiptPath (
        $formulaReceiptObject | ConvertTo-Json -Depth 10
      )
      & $PythonPath $tidyWriter --staging $stagePath --out $stageDataWorkbook --database $DatabasePath --database-sha256 $databaseHash --payload $dataPath --overview $stageWorkbook --overview-receipt $overviewReceiptPath
      if ($LASTEXITCODE -ne 0) {
        throw "Tidy data workbook build failed with exit code $LASTEXITCODE"
      }
      if (-not (Test-Path -LiteralPath $stageDataWorkbook -PathType Leaf)) {
        throw "Tidy data workbook writer did not create $stageDataWorkbook"
      }
      # The writer's OWN receipt, not the Overview's: the Data book has its own
      # sheet count, so the Overview's `-lt 16` floor would be meaningless here.
      $dataReceiptPath = "$stageDataWorkbook.receipt.json"
      if (-not (Test-Path -LiteralPath $dataReceiptPath -PathType Leaf)) {
        throw "Tidy data workbook writer did not create $dataReceiptPath"
      }
      if (-not (Test-JsonFile $dataReceiptPath)) {
        throw 'Tidy data workbook writer produced an invalid receipt.'
      }
      $dataReceiptObject = Get-Content -Raw -LiteralPath $dataReceiptPath |
        ConvertFrom-Json
      $dataWorksheetCount = [int]$dataReceiptObject.expected_worksheet_count
      if ($dataWorksheetCount -lt 1) {
        throw 'Tidy data workbook receipt reported no worksheets.'
      }
      Assert-WorkbookPackage $stageDataWorkbook $dataWorksheetCount
      $dataWorkbookSha256 = (
        Get-FileHash -LiteralPath $stageDataWorkbook -Algorithm SHA256
      ).Hash.ToLowerInvariant()
      if ([string]$dataReceiptObject.sha256 -and
          [string]$dataReceiptObject.sha256 -ne $dataWorkbookSha256) {
        throw 'Tidy data workbook does not match the SHA-256 in its own receipt.'
      }
      $targetDataWorkbook = Join-Path $OutputDirectory "${dataReportBaseName}.xlsx"
    } else {
      Write-Warning (
        'Tidy data workbook writer is not installed; publishing the Overview ' +
        "only. Expected: $tidyWriter"
      )
    }
    # Publish companions on cache hits too, binding every CSV to the validated
    # workbook and exact payload. A bundle reader can reject partial updates.
    & $PythonPath (Join-Path $projectRoot 'tools\enrich_downstream.py') $buildDataPath $BeneficiaryDirectory --audit-only --workbook $stageWorkbook --audit-directory (Join-Path $OutputDirectory ($reportBaseName + '.audit'))
    if ($LASTEXITCODE -ne 0) { throw 'Audit companion publication failed.' }
    Copy-Item -LiteralPath $stageWorkbook -Destination $pendingWorkbook -Force
    Move-Item -LiteralPath $pendingWorkbook -Destination $targetWorkbook -Force
    # Immediately after the Overview and BEFORE `sourceGuard accept`, so the
    # accepted source advances only once BOTH books are on disk. The console
    # globs this folder, so the pair must never be half-published: the Data book
    # goes out under the same copy-then-atomic-move as the Overview.
    if ($targetDataWorkbook) {
      $pendingDataWorkbook = "$targetDataWorkbook.pending-$PID"
      Copy-Item -LiteralPath $stageDataWorkbook -Destination $pendingDataWorkbook -Force
      Move-Item -LiteralPath $pendingDataWorkbook -Destination $targetDataWorkbook -Force
    }
    Copy-Item -LiteralPath $sourceGuardReceipt -Destination (Join-Path $OutputDirectory ($reportBaseName + '.source-changes.json')) -Force
    & $PythonPath $sourceGuard accept --receipt $sourceGuardReceipt
    if ($LASTEXITCODE -ne 0) { throw 'Could not advance the accepted report source.' }
    # Recorded after publication so the manifest never claims a companion that
    # was not published. Rewritten on cache hits too - the Overview fields are
    # replayed verbatim from the values already in scope.
    Write-AtomicText $workbookCacheManifestPath ([pscustomobject]@{
      workbook_fingerprint = $workbookFingerprint
      workbook_sha256 = $workbookSha256
      data_workbook_sha256 = $dataWorkbookSha256
      template_contract = $templateContract
      expected_worksheet_count = $expectedWorksheetCount
      data_expected_worksheet_count = $dataWorksheetCount
      generated_at = [DateTimeOffset]::Now.ToString('o')
      formula_receipt = $formulaReceiptObject
    } | ConvertTo-Json -Depth 10)
  } finally {
    $finalizeWatch.Stop()
  }
} finally {
  if ($null -ne $workbookLockStream) {
    $workbookLockStream.Dispose()
    $workbookLockStream = $null
  }
  if ($workbookLockOwned -and (Test-Path -LiteralPath $WorkbookLockPath)) {
    $currentLock = ''
    try {
      $currentLock = Get-Content -Raw -LiteralPath $WorkbookLockPath
    } catch { $currentLock = '' }
    if ($currentLock -match (
      '(?m)^token=' + [regex]::Escape($workbookLockToken) + '\s*$'
    )) {
      Remove-Item -LiteralPath $WorkbookLockPath -Force
    } else {
      Write-Warning (
        'Workbook lock ownership changed; refusing to remove another ' +
        "process lease: $WorkbookLockPath"
      )
    }
  }
}
$overallWatch.Stop()
$under45Minutes = $overallWatch.Elapsed.TotalMinutes -lt 45
$underPerformanceBudget = (
  $overallWatch.Elapsed.TotalMinutes -lt $PerformanceBudgetMinutes
)

# enrich_downstream.py names each published cycle's transfer index status in the
# audit manifest (current | stale | missing | unresolved; absent for a payload
# enriched before the status existed). The receipt carries it so a stale index is
# a named per-cycle fact rather than a `warn` nobody reads.
$transferIndexStatus = $null
$auditManifestPath = Join-Path (Join-Path $OutputDirectory ($reportBaseName + '.audit')) 'audit-manifest.json'
if (Test-Path -LiteralPath $auditManifestPath -PathType Leaf) {
  $transferIndexStatus = (
    Get-Content -Raw -Encoding UTF8 -LiteralPath $auditManifestPath | ConvertFrom-Json
  ).transfer_index_status
}

[pscustomobject]@{
  industry = $Industry
  workbook = $targetWorkbook
  bytes = (Get-Item -LiteralPath $targetWorkbook).Length
  data_workbook = $targetDataWorkbook
  data_workbook_bytes = if ($targetDataWorkbook) {
    (Get-Item -LiteralPath $targetDataWorkbook).Length
  } else { 0 }
  data_workbook_sha256 = $dataWorkbookSha256
  data_expected_worksheet_count = $dataWorksheetCount
  stage_store = $stagePath
  stage_sha256 = if (Test-Path -LiteralPath $stagePath -PathType Leaf) {
    (Get-FileHash -LiteralPath $stagePath -Algorithm SHA256).Hash.ToLowerInvariant()
  } else { $null }
  database_sha256 = $databaseHash
  data_sha256 = (
    Get-FileHash -LiteralPath $dataPath -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  data_manifest = $manifestPath
  export_guard_receipt = 'embedded:metadata.export_guards'
  cross_reference_mode = $crossReferenceMode
  transfer_index_status = $transferIndexStatus
  template_contract = $templateContract
  cache_hit = $cacheHit
  data_cache_hit = $cacheHit
  workbook_cache_hit = $workbookCacheHit
  export_seconds = [math]::Round($exportSeconds,3)
  build_seconds = [math]::Round($buildWatch.Elapsed.TotalSeconds,3)
  formula_seconds = [math]::Round($formulaWatch.Elapsed.TotalSeconds,3)
  finalize_seconds = [math]::Round($finalizeWatch.Elapsed.TotalSeconds,3)
  total_seconds = [math]::Round($overallWatch.Elapsed.TotalSeconds,3)
  node_heap_mb = $NodeHeapMb
  runtimes = [ordered]@{
    node_path = $NodePath
    node_modules_path = $NodeModulesPath
    rscript_path = [IO.Path]::GetFullPath($RScriptPath)
  }
  performance_budget_minutes = $PerformanceBudgetMinutes
  performance_budget_pass = $underPerformanceBudget
  under_15_minutes = $overallWatch.Elapsed.TotalMinutes -lt 15
  under_45_minutes = $under45Minutes
  formula_receipt = $formulaReceiptObject
} | ConvertTo-Json -Depth 5
