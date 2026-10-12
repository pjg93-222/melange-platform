[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WorkbookPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedSourceSha256,

    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,

    [ValidateSet('CalculateFull', 'CalculateFullRebuild')]
    [string]$CalculationMethod = 'CalculateFull',

    [ValidateRange(30, 1800)]
    [int]$CalculationTimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:MainNamespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
$script:PackageRelationshipNamespace = 'http://schemas.openxmlformats.org/package/2006/relationships'
$script:AllowedFormulaFunctions = @(
    '_XLFN.LET',
    '_XLFN.MINIFS',
    'ABS',
    'AVERAGEIF',
    'COUNTIF',
    'COUNTIFS',
    'IF',
    'IFERROR',
    'INDEX',
    'MATCH',
    'MAX',
    'MIN',
    'N',
    'PERCENTRANK',
    'RANK',
    'SUM',
    'SUMIFS',
    'SUMPRODUCT'
)
$script:ForbiddenPackagePartPatterns = @(
    '^xl/vbaProject\.bin$',
    '^xl/macrosheets/',
    '^xl/dialogsheets/',
    '^xl/externalLinks/',
    '^xl/connections\.xml$',
    '^xl/queryTables/',
    '^xl/pivotCache/',
    '^xl/model/',
    '^xl/activeX/',
    '^xl/embeddings/',
    '^xl/oleObjects/',
    '^customUI/'
)

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-WorkbookSafety {
    param([Parameter(Mandatory = $true)][string]$Path)

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $package = [System.IO.Compression.ZipFile]::OpenRead($Path)
    try {
        $entryNames = @($package.Entries | ForEach-Object FullName)
        $forbiddenParts = @(
            $entryNames |
                Where-Object {
                    $partName = $_
                    @(
                        $script:ForbiddenPackagePartPatterns |
                            Where-Object { $partName -match $_ }
                    ).Count -gt 0
                } |
                Sort-Object -Unique
        )

        $externalRelationships = @()
        foreach ($entry in $package.Entries | Where-Object { $_.FullName -like '*.rels' }) {
            $reader = [System.IO.StreamReader]::new($entry.Open())
            try {
                $document = [xml]$reader.ReadToEnd()
            }
            finally {
                $reader.Dispose()
            }
            $manager = [System.Xml.XmlNamespaceManager]::new($document.NameTable)
            $manager.AddNamespace('r', $script:PackageRelationshipNamespace)
            foreach ($relationship in $document.SelectNodes(
                '//r:Relationship[@TargetMode="External"]',
                $manager
            )) {
                $externalRelationships += [ordered]@{
                    part = $entry.FullName
                    type = [string]$relationship.Type
                    target = [string]$relationship.Target
                }
            }
        }
        $unsafeExternalRelationships = @(
            $externalRelationships |
                Where-Object { -not ([string]$_.type).EndsWith('/hyperlink') }
        )

        $formulaCount = 0
        $formulaFunctionCounts = @{}
        $unsafeFormulaExamples = @()
        $externalReferenceExamples = @()
        foreach (
            $entry in $package.Entries |
                Where-Object { $_.FullName -match '^xl/worksheets/.*\.xml$' }
        ) {
            $reader = [System.IO.StreamReader]::new($entry.Open())
            try {
                $document = [xml]$reader.ReadToEnd()
            }
            finally {
                $reader.Dispose()
            }
            $manager = [System.Xml.XmlNamespaceManager]::new($document.NameTable)
            $manager.AddNamespace('m', $script:MainNamespace)
            foreach ($formulaNode in $document.SelectNodes('//m:f', $manager)) {
                $formulaCount += 1
                $formula = [string]$formulaNode.InnerText
                foreach (
                    $match in [regex]::Matches(
                        $formula,
                        '(?i)(?<![A-Z0-9_.])([A-Z_][A-Z0-9_.]*)\s*\('
                    )
                ) {
                    $function = $match.Groups[1].Value.ToUpperInvariant()
                    if (-not $formulaFunctionCounts.ContainsKey($function)) {
                        $formulaFunctionCounts[$function] = 0
                    }
                    $formulaFunctionCounts[$function] += 1
                    if (
                        $script:AllowedFormulaFunctions -notcontains $function -and
                        $unsafeFormulaExamples.Count -lt 25
                    ) {
                        $unsafeFormulaExamples += [ordered]@{
                            part = $entry.FullName
                            function = $function
                            formula = $formula.Substring(
                                0,
                                [Math]::Min(240, $formula.Length)
                            )
                        }
                    }
                }
                if (
                    (
                        $formula -match (
                            '(?i)\[[^\[\]]+\.' +
                            '(?:xlsx|xlsm|xlsb|xls|xlam|xla|csv)\]' +
                            '[^!]*!'
                        ) -or
                        $formula -match '(?i)(?:https?|file)://' -or
                        $formula -match '(?i)''[^'']+''\|''[^'']+''!'
                    ) -and
                    $externalReferenceExamples.Count -lt 25
                ) {
                    $externalReferenceExamples += [ordered]@{
                        part = $entry.FullName
                        formula = $formula.Substring(
                            0,
                            [Math]::Min(240, $formula.Length)
                        )
                    }
                }
            }
        }

        $definedNameRisks = @()
        $workbookEntry = $package.GetEntry('xl/workbook.xml')
        if ($null -eq $workbookEntry) {
            throw 'The package has no xl/workbook.xml part.'
        }
        $reader = [System.IO.StreamReader]::new($workbookEntry.Open())
        try {
            $workbookDocument = [xml]$reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }
        $manager = [System.Xml.XmlNamespaceManager]::new($workbookDocument.NameTable)
        $manager.AddNamespace('m', $script:MainNamespace)
        foreach ($definedName in $workbookDocument.SelectNodes('//m:definedName', $manager)) {
            $name = [string]$definedName.name
            $definition = [string]$definedName.InnerText
            if (
                $name -match '(?i)Auto_Open|Auto_Close|Workbook_Open' -or
                $definition -match '(?i)\b(?:CALL|EXEC|REGISTER\.ID)\s*\('
            ) {
                $definedNameRisks += [ordered]@{
                    name = $name
                    definition = $definition
                }
            }
        }

        $functionRows = @(
            $formulaFunctionCounts.GetEnumerator() |
                Sort-Object Name |
                ForEach-Object {
                    [ordered]@{
                        function = $_.Name
                        occurrences = $_.Value
                    }
                }
        )
        $safe = (
            $forbiddenParts.Count -eq 0 -and
            $unsafeExternalRelationships.Count -eq 0 -and
            $unsafeFormulaExamples.Count -eq 0 -and
            $externalReferenceExamples.Count -eq 0 -and
            $definedNameRisks.Count -eq 0
        )
        return [ordered]@{
            safe_for_isolated_excel_calculation = $safe
            package_part_count = $entryNames.Count
            formula_count = $formulaCount
            formula_functions = $functionRows
            allowed_external_hyperlinks = @(
                $externalRelationships |
                    Where-Object { ([string]$_.type).EndsWith('/hyperlink') }
            )
            forbidden_package_parts = $forbiddenParts
            unsafe_external_relationships = $unsafeExternalRelationships
            unsafe_formula_examples = $unsafeFormulaExamples
            external_formula_reference_examples = $externalReferenceExamples
            defined_name_runtime_risks = $definedNameRisks
        }
    }
    finally {
        $package.Dispose()
    }
}

function Release-ComObject {
    param([object]$Value)
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

$resolvedWorkbook = (Resolve-Path -LiteralPath $WorkbookPath).Path
$resolvedReceiptParent = (
    Resolve-Path -LiteralPath (Split-Path -Parent $ReceiptPath)
).Path
$resolvedReceipt = Join-Path $resolvedReceiptParent (Split-Path -Leaf $ReceiptPath)
$tempRoot = [System.IO.Path]::GetFullPath('C:\tmp\')
$workbookFullPath = [System.IO.Path]::GetFullPath($resolvedWorkbook)
$receiptFullPath = [System.IO.Path]::GetFullPath($resolvedReceipt)
if (-not $workbookFullPath.StartsWith(
    $tempRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Workbook must be a temporary copy under C:\tmp: $workbookFullPath"
}
if (-not $receiptFullPath.StartsWith(
    $tempRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Receipt must stay under C:\tmp: $receiptFullPath"
}
if ([System.IO.Path]::GetExtension($workbookFullPath) -ne '.xlsx') {
    throw 'Only macro-free .xlsx inputs are allowed.'
}

$before = [ordered]@{
    path = $workbookFullPath
    bytes = (Get-Item -LiteralPath $workbookFullPath).Length
    sha256 = Get-Sha256 -Path $workbookFullPath
}
if ($before.sha256 -ne $ExpectedSourceSha256.ToLowerInvariant()) {
    throw (
        "Temporary copy hash does not match expected source hash: " +
        "$($before.sha256) != $($ExpectedSourceSha256.ToLowerInvariant())"
    )
}
$safety = Get-WorkbookSafety -Path $workbookFullPath
if (-not $safety.safe_for_isolated_excel_calculation) {
    $failureSummary = [ordered]@{
        forbidden_package_parts = $safety.forbidden_package_parts
        unsafe_external_relationships = $safety.unsafe_external_relationships
        unsafe_formula_examples = $safety.unsafe_formula_examples
        external_formula_reference_examples = (
            $safety.external_formula_reference_examples
        )
        defined_name_runtime_risks = $safety.defined_name_runtime_risks
    } | ConvertTo-Json -Depth 8 -Compress
    throw (
        'Workbook safety preflight failed closed; Excel was not started. ' +
        $failureSummary
    )
}

$excelType = [Type]::GetTypeFromProgID('Excel.Application')
if ($null -eq $excelType) {
    throw 'Excel.Application COM registration is unavailable.'
}

$excel = $null
$workbook = $null
$bootstrapWorkbook = $null
$opened = $false
$saved = $false
$startedAt = [DateTimeOffset]::UtcNow
$engine = [ordered]@{}
try {
    $excel = [Activator]::CreateInstance($excelType)
    $engine.version = [string]$excel.Version
    $engine.build = [string]$excel.Build
    $engine.operating_system = [string]$excel.OperatingSystem
    $engine.automation_security = 'msoAutomationSecurityForceDisable'
    $engine.update_links = 0
    $engine.enable_events = $false
    $engine.display_alerts = $false
    $engine.ask_to_update_links = $false
    $engine.visible = $false
    $engine.calculation_request = $CalculationMethod

    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.EnableEvents = $false
    $excel.AskToUpdateLinks = $false
    $excel.ScreenUpdating = $false
    $excel.AutomationSecurity = 3
    $bootstrapWorkbook = $excel.Workbooks.Add()
    $excel.Calculation = -4135
    $bootstrapWorkbook.Close($false)
    Release-ComObject -Value $bootstrapWorkbook
    $bootstrapWorkbook = $null

    $workbook = $excel.Workbooks.Open(
        $workbookFullPath,
        0,
        $false
    )
    $opened = $true
    if (
        -not ([string]$workbook.FullName).Equals(
            $workbookFullPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Excel opened an unexpected path: $($workbook.FullName)"
    }
    if ($workbook.ReadOnly) {
        throw 'Excel opened the temporary copy read-only.'
    }
    if ($workbook.Connections.Count -ne 0) {
        throw 'Excel reported workbook connections after the static preflight.'
    }
    $linkSources = $workbook.LinkSources(1)
    if ($null -ne $linkSources) {
        throw 'Excel reported external workbook or DDE link sources.'
    }

    $engine.worksheet_count = [int]$workbook.Worksheets.Count
    $engine.calculation_version_before = [int]$workbook.CalculationVersion
    if ($CalculationMethod -eq 'CalculateFullRebuild') {
        $excel.CalculateFullRebuild()
    }
    else {
        $excel.CalculateFull()
    }

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($CalculationTimeoutSeconds)
    while (
        [int]$excel.CalculationState -ne 0 -and
        [DateTimeOffset]::UtcNow -lt $deadline
    ) {
        Start-Sleep -Milliseconds 100
    }
    if ([int]$excel.CalculationState -ne 0) {
        throw "Excel calculation did not finish within $CalculationTimeoutSeconds seconds."
    }
    $engine.calculation_state_after = [int]$excel.CalculationState
    $engine.ready_after = [bool]$excel.Ready
    $engine.calculation_version_after = [int]$workbook.CalculationVersion
    $engine.calculate_before_save = $false
    $excel.CalculateBeforeSave = $false
    $workbook.Save()
    $saved = $true
    $workbook.Close($false)
    $opened = $false
    Release-ComObject -Value $workbook
    $workbook = $null
}
finally {
    if ($null -ne $bootstrapWorkbook) {
        try {
            $bootstrapWorkbook.Close($false)
        }
        catch {
        }
    }
    Release-ComObject -Value $bootstrapWorkbook
    $bootstrapWorkbook = $null
    if ($opened -and $null -ne $workbook) {
        try {
            $workbook.Close($false)
        }
        catch {
        }
    }
    Release-ComObject -Value $workbook
    $workbook = $null
    if ($null -ne $excel) {
        try {
            $excel.Quit()
        }
        catch {
        }
    }
    Release-ComObject -Value $excel
    $excel = $null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if (-not $saved) {
    throw 'The temporary workbook was not saved after calculation.'
}
$after = [ordered]@{
    path = $workbookFullPath
    bytes = (Get-Item -LiteralPath $workbookFullPath).Length
    sha256 = Get-Sha256 -Path $workbookFullPath
}
$receipt = [ordered]@{
    schema = 'isolated-excel-formula-cache-recalculation-v1'
    status = 'PASS_TEMP_COPY_RECALCULATED'
    generated_at = [DateTimeOffset]::UtcNow.ToString('o')
    started_at = $startedAt.ToString('o')
    source_expected_sha256 = $ExpectedSourceSha256.ToLowerInvariant()
    temporary_copy_before = $before
    temporary_copy_after = $after
    safety_preflight = $safety
    engine = $engine
    live_or_production_workbook_opened = $false
    live_or_production_workbook_written = $false
    macro_execution_allowed = $false
    external_link_updates_allowed = $false
    external_connections_allowed = $false
    sync_or_promotion_performed = $false
}
$receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $receiptFullPath -Encoding UTF8
$receipt | ConvertTo-Json -Depth 10
