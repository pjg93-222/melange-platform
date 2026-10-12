param(
  [Parameter(Mandatory = $true)][string]$WorkbookPath,
  [Parameter(Mandatory = $true)][string]$JsonPath,
  [string]$ExcelMutexName = 'Global\MentatIndustryExcelFinalize'
)
$ErrorActionPreference = 'Stop'

function Release-ComObject($Object) {
  if ($null -ne $Object -and [Runtime.InteropServices.Marshal]::IsComObject($Object)) {
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Object)
  }
}

function Set-TableColumnFormula($Table, [string]$ColumnName, [string]$Formula) {
  $column = $Table.ListColumns.Item($ColumnName)
  $range = $column.DataBodyRange
  try { if ($null -ne $range) { $range.Formula = $Formula } } finally { Release-ComObject $range; Release-ComObject $column }
}

function Cycle-Dispatch([scriptblock]$Builder) {
  $expression = '0'
  $entries = @($payload.cycles | Sort-Object { [int]$_.cycle } -Descending)
  foreach ($entry in $entries) {
    $value = & $Builder ([string]$entry.table_name)
    $label = [string]$entry.label
    $expression = "IF([@Cycle]=`"${label}`",${value},${expression})"
  }
  $expression
}

function Category-Label([string]$Category) {
  if ($Category -eq 'Company PAC') { return 'Company PAC receipts' }
  $Category
}

function Scope-Expression([string]$TableName, [string]$Party = '') {
  $partyArgs = if ($Party) { ",${TableName}[Party],`"${Party}`"" } else { '' }
  $factArgs = if ($formulaFacts) { ",${TableName}[Fact Type],`"Master`"" } else { '' }
  $total = if ($Party -or $formulaFacts) { "SUMIFS(${TableName}[Analysis Amount]${partyArgs}${factArgs})" } else { "SUM(${TableName}[Analysis Amount])" }
  $role = "SUMIFS(${TableName}[Analysis Amount],${TableName}[Employee Tier],[@Scope]${partyArgs}${factArgs})"
  $categoryKey = 'IF([@Scope]="Company PAC receipts","Company PAC",[@Scope])'
  $category = "SUMIFS(${TableName}[Analysis Amount],${TableName}[Recipient Category],${categoryKey}${partyArgs}${factArgs})"
  "IF([@Scope]=`"Total Giving`",${total},IF(OR([@Scope]=`"Corporate`",[@Scope]=`"Chief`",[@Scope]=`"Middle Management`",[@Scope]=`"Employee`"),${role},${category}))"
}

function Company-Expression([string]$TableName, [string]$Party = '', [string]$Tier = '') {
  $extra = if ($formulaFacts) { ",${TableName}[Fact Type],`"Master`"" } else { '' }
  if ($Party) { $extra += ",${TableName}[Party],`"${Party}`"" }
  if ($Tier) { $extra += ",${TableName}[Employee Tier],`"${Tier}`"" }
  "SUMIFS(${TableName}[Analysis Amount],${TableName}[Company],[@Company]${extra})"
}

function Master-Expression([string]$TableName, [string]$Party = '', [string]$Category = '', [string]$Tier = '') {
  $extra = if ($formulaFacts) { ",${TableName}[Fact Type],`"Master`"" } else { '' }
  if ($Party) { $extra += ",${TableName}[Party],`"${Party}`"" }
  if ($Category) { $extra += ",${TableName}[Recipient Category],`"${Category}`"" }
  if ($Tier) { $extra += ",${TableName}[Employee Tier],`"${Tier}`"" }
  $industry = if ($extra) { "SUMIFS(${TableName}[Analysis Amount]${extra})" } else { "SUM(${TableName}[Analysis Amount])" }
  $company = "SUMIFS(${TableName}[Analysis Amount],${TableName}[Company],[@Company]${extra})"
  "IF([@Company]=`"Industry Total`",${industry},${company})"
}

function Master-Prior-Expression([string]$PriorCycle, [string]$Metric) {
  if (-not $PriorCycle) { return '0' }
  $industry = "SUMIFS(CompanyListTable[${Metric}],CompanyListTable[Cycle],`"${PriorCycle}`")"
  $company = "SUMIFS(CompanyListTable[${Metric}],CompanyListTable[Cycle],`"${PriorCycle}`",CompanyListTable[Company],[@Company])"
  "IF([@Company]=`"Industry Total`",${industry},${company})"
}

function Employee-Expression([string]$TableName, [string]$Party = '', [switch]$Count) {
  $partyArgs = if ($Party) { ",${TableName}[Party],`"${Party}`"" } else { '' }
  $factArgs = if ($formulaFacts) { ",${TableName}[Fact Type],`"Employee`"" } else { '' }
  if ($Count) {
    if ($formulaFacts) {
      return "SUMIFS(${TableName}[Gift Count],${TableName}[Person Key],[@[Person Key]],${TableName}[Include in Analysis],`"Yes`"${factArgs})"
    }
    return "COUNTIFS(${TableName}[Person Key],[@[Person Key]],${TableName}[Include in Analysis],`"Yes`")"
  }
  "SUMIFS(${TableName}[Analysis Amount],${TableName}[Person Key],[@[Person Key]]${partyArgs}${factArgs})"
}

function Recipient-Expression([string]$TableName, [string]$Party = '', [string]$Tier = '', [switch]$Count) {
  $extra = if ($formulaFacts) { ",${TableName}[Fact Type],`"Recipient`"" } else { '' }
  if ($Party) { $extra += ",${TableName}[Party],`"${Party}`"" }
  if ($Tier) { $extra += ",${TableName}[Employee Tier],`"${Tier}`"" }
  if ($Count) {
    if ($formulaFacts) {
      $industry = "SUMIFS(${TableName}[Gift Count],${TableName}[Recipient Key],[@[Recipient Key]],${TableName}[Include in Analysis],`"Yes`"${extra})"
      $company = "SUMIFS(${TableName}[Gift Count],${TableName}[Recipient Key],[@[Recipient Key]],${TableName}[Company],[@Company],${TableName}[Include in Analysis],`"Yes`"${extra})"
    } else {
      $industry = "COUNTIFS(${TableName}[Recipient Key],[@[Recipient Key]],${TableName}[Include in Analysis],`"Yes`")"
      $company = "COUNTIFS(${TableName}[Recipient Key],[@[Recipient Key]],${TableName}[Company],[@Company],${TableName}[Include in Analysis],`"Yes`")"
    }
  } else {
    $industry = "SUMIFS(${TableName}[Analysis Amount],${TableName}[Recipient Key],[@[Recipient Key]]${extra})"
    $company = "SUMIFS(${TableName}[Analysis Amount],${TableName}[Recipient Key],[@[Recipient Key]],${TableName}[Company],[@Company]${extra})"
  }
  "IF([@View]=`"Industry`",${industry},${company})"
}

function Bridge-Funding-Expression([string]$TableName) {
  $factArgs = if ($formulaFacts) { ",${TableName}[Fact Type],`"Recipient`"" } else { '' }
  $industry = "SUMIFS(${TableName}[Analysis Amount],${TableName}[Recipient Key],`"FEC:`"&[@[Source Committee ID]]${factArgs})"
  $company = "SUMIFS(${TableName}[Analysis Amount],${TableName}[Recipient Key],`"FEC:`"&[@[Source Committee ID]],${TableName}[Company],[@Company]${factArgs})"
  "IF([@View]=`"Industry`",${industry},${company})"
}

function Focused-Expression([string]$Metric) {
  $beneficiaryMetric = if ($Metric -eq 'Total Giving') { 'Downstream Activity' } elseif ($Metric -eq 'Other / Unclassified') { 'Other / Unclassified Activity' } else { "$Metric Activity" }
  $company = "SUMIFS(CompanyListTable[${Metric}],CompanyListTable[Cycle],[@Cycle],CompanyListTable[Company],[@[Reference Company]])"
  $employee = "SUMIFS(EmployeeListTable[${Metric}],EmployeeListTable[Cycle],[@Cycle],EmployeeListTable[Person Key],[@[Reference Key]])"
  $recipient = "SUMIFS(RecipientListTable[${Metric}],RecipientListTable[Cycle],[@Cycle],RecipientListTable[View],[@[Reference View]],RecipientListTable[Company],[@[Reference Company]],RecipientListTable[Recipient Key],[@[Reference Key]])"
  $beneficiary = "SUMIFS(BeneficiarySummaryTable[${beneficiaryMetric}],BeneficiarySummaryTable[Cycle],[@Cycle],BeneficiarySummaryTable[View],[@[Reference View]],BeneficiarySummaryTable[Company],[@[Reference Company]],BeneficiarySummaryTable[Candidate ID],[@[Reference Key]])"
  "IF([@Section]=`"Company Total`",${company},IF([@Section]=`"Top Contributor`",${employee},IF([@Section]=`"Top Direct Recipient`",${recipient},${beneficiary})))"
}

function Count-NonOk($Range) {
  $values = $Range.Value2; $bad = 0
  if ($values -is [Array]) {
    for ($row = $values.GetLowerBound(0); $row -le $values.GetUpperBound(0); $row++) {
      if ([string]$values.GetValue($row, 1) -ne 'OK') { $bad++ }
    }
  } elseif ([string]$values -ne 'OK') { $bad++ }
  $bad
}

function Import-LargeTableSidecar($Book, $Spec) {
  $sidecarPath = [string]$Spec.path
  if (-not (Test-Path -LiteralPath $sidecarPath)) {
    throw "Large-table sidecar is missing: $sidecarPath"
  }
  $rowCount = [int]$Spec.row_count
  $columnCount = [int]$Spec.column_count
  if ($rowCount -le 0 -or $columnCount -le 0) {
    throw "Invalid large-table dimensions for $($Spec.sheet): ${rowCount}x${columnCount}"
  }

  $numericColumns = [Collections.Generic.HashSet[int]]::new()
  foreach ($columnIndex in @($Spec.numeric_columns)) {
    [void]$numericColumns.Add([int]$columnIndex)
  }

  $sheet = $Book.Worksheets.Item([string]$Spec.sheet)
  $table = $sheet.ListObjects.Item([string]$Spec.table)
  $resizeRange = $null
  $reader = $null
  try {
    $resizeRange = $sheet.Range(
      $sheet.Cells.Item(5, 1),
      $sheet.Cells.Item(5 + $rowCount, $columnCount)
    )
    $table.Resize($resizeRange)
    Release-ComObject $resizeRange
    $resizeRange = $null

    $reader = [IO.StreamReader]::new(
      $sidecarPath,
      [Text.UTF8Encoding]::new($false),
      $true,
      1MB
    )
    $batchSize = 1000
    $written = 0
    while (-not $reader.EndOfStream) {
      $lines = [Collections.Generic.List[string]]::new($batchSize)
      while ($lines.Count -lt $batchSize -and -not $reader.EndOfStream) {
        $line = $reader.ReadLine()
        if ($null -ne $line) { $lines.Add($line) }
      }
      if ($lines.Count -eq 0) { break }
      $matrix = [Array]::CreateInstance([object], @($lines.Count, $columnCount))
      for ($rowIndex = 0; $rowIndex -lt $lines.Count; $rowIndex++) {
        $fields = $lines[$rowIndex].Split([char]31)
        if ($fields.Count -ne $columnCount) {
          throw (
            "Large-table sidecar width mismatch for $($Spec.sheet) at row " +
            "$($written + $rowIndex + 1): expected=$columnCount actual=$($fields.Count)"
          )
        }
        for ($columnIndex = 0; $columnIndex -lt $columnCount; $columnIndex++) {
          $value = $fields[$columnIndex]
          if ($numericColumns.Contains($columnIndex)) {
            if ([string]::IsNullOrWhiteSpace($value)) {
              $matrix.SetValue(0.0, $rowIndex, $columnIndex)
            } else {
              $number = 0.0
              if (-not [double]::TryParse(
                $value,
                [Globalization.NumberStyles]::Float,
                [Globalization.CultureInfo]::InvariantCulture,
                [ref]$number
              )) {
                throw (
                  "Large-table numeric parse failure for $($Spec.sheet) at " +
                  "row $($written + $rowIndex + 1), column $($columnIndex + 1)"
                )
              }
              $matrix.SetValue($number, $rowIndex, $columnIndex)
            }
          } elseif ($value.Length -gt 0) {
            $matrix.SetValue($value, $rowIndex, $columnIndex)
          }
        }
      }
      $targetRange = $sheet.Range(
        $sheet.Cells.Item(6 + $written, 1),
        $sheet.Cells.Item(5 + $written + $lines.Count, $columnCount)
      )
      try { $targetRange.Value2 = $matrix } finally { Release-ComObject $targetRange }
      $written += $lines.Count
    }
    if ($written -ne $rowCount) {
      throw "Large-table sidecar row mismatch for $($Spec.sheet): expected=$rowCount actual=$written"
    }
  } finally {
    if ($null -ne $reader) { $reader.Dispose() }
    if ($null -ne $resizeRange) { Release-ComObject $resizeRange }
    Release-ComObject $table
    Release-ComObject $sheet
  }
}

$resolvedWorkbook = (Resolve-Path -LiteralPath $WorkbookPath).Path
$resolvedJson = (Resolve-Path -LiteralPath $JsonPath).Path
$payload = Get-Content -Raw -LiteralPath $resolvedJson | ConvertFrom-Json
$industry = [string]$payload.metadata.industry
if ([string]::IsNullOrWhiteSpace($industry)) { $industry = 'Crypto' }
$formulaFacts = [bool]$payload.metadata.formula_facts
$reportBaseName = [string]$payload.metadata.report_base_name
if ([string]::IsNullOrWhiteSpace($reportBaseName)) {
  $reportBaseName = "${industry} Industry Spending Overview"
}
$cycleSheetNames = @(
  $payload.cycles |
    Sort-Object { [int]$_.cycle } |
    ForEach-Object { [string]$_.label }
)
$overviewSheetName = if ($industry -eq 'Crypto') { 'Crypto Spending Overview' } else { 'Spending Overview' }
$associatedFundingHeader = "Associated ${industry} Funding"
$workingPath = Join-Path (Split-Path $resolvedWorkbook -Parent) "${reportBaseName}.excel-final-$PID.xlsx"
Copy-Item -LiteralPath $resolvedWorkbook -Destination $workingPath -Force
$excel = $null; $book = $null; $watch = [Diagnostics.Stopwatch]::StartNew()
$excelMutex = [Threading.Mutex]::new($false, $ExcelMutexName)
$hasExcelMutex = $false
try {
  $hasExcelMutex = $excelMutex.WaitOne([TimeSpan]::FromMinutes(45))
} catch [Threading.AbandonedMutexException] {
  # WaitOne transfers ownership before raising this exception. Continuing is
  # safe because the prior Excel process is gone and this run uses its own
  # isolated working copy.
  $hasExcelMutex = $true
  Write-Warning 'Recovered an abandoned Excel finalization mutex.'
}
if (-not $hasExcelMutex) {
  $excelMutex.Dispose()
  throw 'Timed out waiting for the shared Excel finalization mutex.'
}
try {
  $excel = New-Object -ComObject Excel.Application
  $excel.Visible = $false; $excel.DisplayAlerts = $false; $excel.EnableEvents = $false
  $book = $excel.Workbooks.Open($workingPath, 0, $false)
  $excel.Calculation = -4135

  if ($null -ne $payload.metadata.large_table_sidecars) {
    foreach ($property in $payload.metadata.large_table_sidecars.PSObject.Properties) {
      Import-LargeTableSidecar $book $property.Value
    }
  }

  foreach ($sheetName in $cycleSheetNames) {
    $sheet = $book.Worksheets.Item($sheetName); $table = $sheet.ListObjects.Item(1)
    try {
      if ($formulaFacts) {
        Set-TableColumnFormula $table 'Analysis Amount' '=IF([@[Include in Analysis]]="Yes",[@Amount],0)'
        Set-TableColumnFormula $table 'Logic Check' '=IF(AND([@[Include in Analysis]]="Yes",[@[Analysis Amount]]=[@Amount]),"OK","CHECK")'
      } else {
        Set-TableColumnFormula $table 'Analysis Amount' '=IF([@[Include in Analysis]]="Yes",[@Amount],0)'
        Set-TableColumnFormula $table 'Logic Check' '=IF(AND([@[Analysis Amount]]=IF([@[Include in Analysis]]="Yes",[@Amount],0),OR([@[Cycle Alignment]]="OK",[@[Cycle Alignment]]="Reassigned by contribution date")),"OK","CHECK")'
      }
    } finally { Release-ComObject $table; Release-ComObject $sheet }
  }
  $sheet = $book.Worksheets.Item('Cycle Exceptions'); $table = $sheet.ListObjects.Item(1)
  try {
    Set-TableColumnFormula $table 'Analysis Amount' '=IF([@[Include in Analysis]]="Yes",[@Amount],0)'
    if ($formulaFacts) {
      Set-TableColumnFormula $table 'Logic Check' '=IF(AND([@[Include in Analysis]]="No",[@[Analysis Amount]]=0),"OK","CHECK")'
    } else {
      Set-TableColumnFormula $table 'Logic Check' '=IF(AND([@[Include in Analysis]]="No",[@[Cycle Alignment]]="Outside requested cycles",[@[Analysis Amount]]=0),"OK","CHECK")'
    }
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  $sheet = $book.Worksheets.Item($overviewSheetName); $table = $sheet.ListObjects.Item(1)
  try {
    Set-TableColumnFormula $table 'Total Giving' ('=' + (Cycle-Dispatch { param($t) Scope-Expression $t }))
    Set-TableColumnFormula $table 'Democratic' ('=' + (Cycle-Dispatch { param($t) Scope-Expression $t 'D' }))
    Set-TableColumnFormula $table 'Republican' ('=' + (Cycle-Dispatch { param($t) Scope-Expression $t 'R' }))
    Set-TableColumnFormula $table 'Other / Unclassified' '=[@[Total Giving]]-[@Democratic]-[@Republican]'
    Set-TableColumnFormula $table 'Net Democratic %' '=IF([@Democratic]+[@Republican]=0,0,[@Democratic]/([@Democratic]+[@Republican]))'
    Set-TableColumnFormula $table 'Net Republican %' '=IF([@Democratic]+[@Republican]=0,0,[@Republican]/([@Democratic]+[@Republican]))'
    Set-TableColumnFormula $table 'Party Coverage' '=IF([@[Total Giving]]=0,0,([@Democratic]+[@Republican])/[@[Total Giving]])'
    Set-TableColumnFormula $table 'Integrity Check' '=IF(ABS([@[Total Giving]]-[@Democratic]-[@Republican]-[@[Other / Unclassified]])<0.01,"OK","CHECK")'
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  # Must match masterCategories in crypto_workbook_builder.mjs, which creates
  # these columns (Conduit = ActBlue and WinRed, directly after Carey).
  $masterCategories = @('Super PAC','Carey','Conduit','Other PAC','Company PAC','Leadership PAC','Trade Association','Candidate','Party Committee','JFC','Unclassified')
  $masterTiers = @('Corporate','Chief','Middle Management','Employee')
  $cycleEntries = @($payload.cycles | Sort-Object { [int]$_.cycle })
  $masterSpecs = @()
  for ($cycleIndex = 0; $cycleIndex -lt $cycleEntries.Count; $cycleIndex++) {
    $cycleEntry = $cycleEntries[$cycleIndex]
    $masterSpecs += [pscustomobject]@{
      Cycle = [string]$cycleEntry.label
      SourceTable = [string]$cycleEntry.table_name
      MasterTable = 'MasterOverview_' + [string]$cycleEntry.cycle
      PriorCycle = if ($cycleIndex -gt 0) { [string]$cycleEntries[$cycleIndex - 1].label } else { '' }
    }
  }
  $sheet = $book.Worksheets.Item('Master Overview')
  try {
    foreach ($spec in $masterSpecs) {
      $table = $sheet.ListObjects.Item([string]$spec.MasterTable)
      try {
        $masterTableName = [string]$spec.MasterTable
        $sourceTableName = [string]$spec.SourceTable
        $priorCycle = [string]$spec.PriorCycle
        $priorTotal = Master-Prior-Expression $priorCycle 'Total Giving'
        $priorDemocratic = Master-Prior-Expression $priorCycle 'Democratic'
        $priorRepublican = Master-Prior-Expression $priorCycle 'Republican'
        Set-TableColumnFormula $table 'Rank' ("=IF([@Company]=`"Industry Total`",0,1+COUNTIFS(${masterTableName}[Company],`"<>Industry Total`",${masterTableName}[Total Giving],`">`"&[@[Total Giving]]))")
        Set-TableColumnFormula $table 'Total Giving' ('=' + (Master-Expression $sourceTableName))
        Set-TableColumnFormula $table '% of Industry' ("=IFERROR([@[Total Giving]]/SUMIFS(${masterTableName}[Total Giving],${masterTableName}[Company],`"Industry Total`"),0)")
        Set-TableColumnFormula $table 'Democratic' ('=' + (Master-Expression $sourceTableName 'D'))
        Set-TableColumnFormula $table 'Republican' ('=' + (Master-Expression $sourceTableName 'R'))
        Set-TableColumnFormula $table 'Other / Unclassified' '=[@[Total Giving]]-[@Democratic]-[@Republican]'
        Set-TableColumnFormula $table 'Net Democratic %' '=IF([@Democratic]+[@Republican]=0,0,[@Democratic]/([@Democratic]+[@Republican]))'
        Set-TableColumnFormula $table 'Net Republican %' '=IF([@Democratic]+[@Republican]=0,0,[@Republican]/([@Democratic]+[@Republican]))'
        Set-TableColumnFormula $table 'Party Coverage' '=IF([@[Total Giving]]=0,0,([@Democratic]+[@Republican])/[@[Total Giving]])'
        Set-TableColumnFormula $table 'Prior Cycle' ('=' + $priorTotal)
        if ($priorCycle) {
          Set-TableColumnFormula $table '$ Change' '=[@[Total Giving]]-[@[Prior Cycle]]'
          Set-TableColumnFormula $table '% Change' '=IF([@[Prior Cycle]]=0,0,[@[$ Change]]/ABS([@[Prior Cycle]]))'
          Set-TableColumnFormula $table 'D $ Change' ("=[@Democratic]-(${priorDemocratic})")
          Set-TableColumnFormula $table 'R $ Change' ("=[@Republican]-(${priorRepublican})")
          Set-TableColumnFormula $table 'D Share Change' ("=[@[Net Democratic %]]-IF((${priorDemocratic})+(${priorRepublican})=0,0,(${priorDemocratic})/((${priorDemocratic})+(${priorRepublican})))")
          Set-TableColumnFormula $table 'R Share Change' ("=[@[Net Republican %]]-IF((${priorDemocratic})+(${priorRepublican})=0,0,(${priorRepublican})/((${priorDemocratic})+(${priorRepublican})))")
        } else {
          Set-TableColumnFormula $table '$ Change' '=0'
          Set-TableColumnFormula $table '% Change' '=0'
          Set-TableColumnFormula $table 'D $ Change' '=0'
          Set-TableColumnFormula $table 'R $ Change' '=0'
          Set-TableColumnFormula $table 'D Share Change' '=0'
          Set-TableColumnFormula $table 'R Share Change' '=0'
        }
        foreach ($category in $masterCategories) {
          $categoryLabel = Category-Label $category
          Set-TableColumnFormula $table $categoryLabel ('=' + (Master-Expression $sourceTableName '' $category))
          Set-TableColumnFormula $table "${categoryLabel} %" ("=IF([@[Total Giving]]=0,0,[@[${categoryLabel}]]/[@[Total Giving]])")
          Set-TableColumnFormula $table "${categoryLabel} D" ('=' + (Master-Expression $sourceTableName 'D' $category))
          Set-TableColumnFormula $table "${categoryLabel} R" ('=' + (Master-Expression $sourceTableName 'R' $category))
          Set-TableColumnFormula $table "${categoryLabel} R %" ("=IF([@[${categoryLabel} D]]+[@[${categoryLabel} R]]=0,0,[@[${categoryLabel} R]]/([@[${categoryLabel} D]]+[@[${categoryLabel} R]]))")
        }
        foreach ($tier in $masterTiers) {
          Set-TableColumnFormula $table $tier ('=' + (Master-Expression $sourceTableName '' '' $tier))
          Set-TableColumnFormula $table "${tier} %" ("=IF([@[Total Giving]]=0,0,[@[${tier}]]/[@[Total Giving]])")
          Set-TableColumnFormula $table "${tier} D" ('=' + (Master-Expression $sourceTableName 'D' '' $tier))
          Set-TableColumnFormula $table "${tier} R" ('=' + (Master-Expression $sourceTableName 'R' '' $tier))
          Set-TableColumnFormula $table "${tier} R %" ("=IF([@[${tier} D]]+[@[${tier} R]]=0,0,[@[${tier} R]]/([@[${tier} D]]+[@[${tier} R]]))")
        }
        $categorySum = ($masterCategories | ForEach-Object { "[@[$(Category-Label $_)]]" }) -join '+'
        $tierSum = ($masterTiers | ForEach-Object { "[@[${_}]]" }) -join '+'
        Set-TableColumnFormula $table 'Integrity Check' ("=IF(AND(ABS([@[Total Giving]]-[@Democratic]-[@Republican]-[@[Other / Unclassified]])<0.01,ABS([@[Total Giving]]-(${categorySum}))<0.01,ABS([@[Total Giving]]-(${tierSum}))<0.01),`"OK`",`"CHECK`")")
      } finally { Release-ComObject $table }
    }
  } finally { Release-ComObject $sheet }

  $sheet = $book.Worksheets.Item('Company List'); $table = $sheet.ListObjects.Item(1)
  try {
    Set-TableColumnFormula $table 'Rank' '=1+COUNTIFS(CompanyListTable[Cycle],[@Cycle],CompanyListTable[Total Giving],">"&[@[Total Giving]])'
    Set-TableColumnFormula $table 'Total Giving' ('=' + (Cycle-Dispatch { param($t) Company-Expression $t }))
    Set-TableColumnFormula $table 'Democratic' ('=' + (Cycle-Dispatch { param($t) Company-Expression $t 'D' }))
    Set-TableColumnFormula $table 'Republican' ('=' + (Cycle-Dispatch { param($t) Company-Expression $t 'R' }))
    Set-TableColumnFormula $table 'Other / Unclassified' '=[@[Total Giving]]-[@Democratic]-[@Republican]'
    Set-TableColumnFormula $table 'Net Democratic %' '=IF([@Democratic]+[@Republican]=0,0,[@Democratic]/([@Democratic]+[@Republican]))'
    Set-TableColumnFormula $table 'Net Republican %' '=IF([@Democratic]+[@Republican]=0,0,[@Republican]/([@Democratic]+[@Republican]))'
    Set-TableColumnFormula $table 'Party Coverage' '=IF([@[Total Giving]]=0,0,([@Democratic]+[@Republican])/[@[Total Giving]])'
    foreach ($tier in @('Corporate','Chief','Middle Management','Employee')) {
      Set-TableColumnFormula $table $tier ('=' + (Cycle-Dispatch { param($t) Company-Expression $t '' $tier }))
    }
    Set-TableColumnFormula $table 'Integrity Check' '=IF(AND(ABS([@[Total Giving]]-[@Democratic]-[@Republican]-[@[Other / Unclassified]])<0.01,ABS([@[Total Giving]]-[@Corporate]-[@Chief]-[@[Middle Management]]-[@Employee])<0.01),"OK","CHECK")'
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  if ($formulaFacts) {
    $sheet = $book.Worksheets.Item('Employee Rank Facts')
    try {
      $rankFacts = @($payload.employee_rank_facts)
      if ($rankFacts.Count -eq 0) {
        throw 'Formula-fact mode requires a non-empty employee rank basis.'
      }
      $lastRankRow = 5 + $rankFacts.Count
      $rankValues = New-Object 'object[,]' $rankFacts.Count, 4
      for ($rankIndex = 0; $rankIndex -lt $rankFacts.Count; $rankIndex++) {
        $rankFact = @($rankFacts[$rankIndex])
        $rankValues[$rankIndex, 0] = [string]$rankFact[0]
        $rankValues[$rankIndex, 1] = [string]$rankFact[1]
        $rankValues[$rankIndex, 2] = [string]$rankFact[2]
        $rankValues[$rankIndex, 3] = [double]$rankFact[3]
      }
      $rankRange = $sheet.Range('A6').Resize($rankFacts.Count, 4)
      try {
        $rankRange.Value2 = $rankValues
      } finally { Release-ComObject $rankRange }
      if ($sheet.ListObjects.Count -eq 0) {
        $tableRange = $sheet.Range("A5:D${lastRankRow}")
        try {
          $table = $sheet.ListObjects.Add(1, $tableRange, $null, 1)
          $table.Name = 'EmployeeRankFactsTable'
          $table.TableStyle = 'TableStyleMedium2'
        } finally { Release-ComObject $tableRange }
      } else {
        $table = $sheet.ListObjects.Item(1)
        $resizeRange = $sheet.Range("A5:D${lastRankRow}")
        try { $table.Resize($resizeRange) } finally { Release-ComObject $resizeRange }
        $table.Name = 'EmployeeRankFactsTable'
      }
    } finally { Release-ComObject $table; Release-ComObject $sheet }
  }

  $sheet = $book.Worksheets.Item('Employee List'); $table = $sheet.ListObjects.Item(1)
  try {
    if ($formulaFacts) {
      Set-TableColumnFormula $table 'Industry Rank' '=1+COUNTIFS(EmployeeRankFactsTable[Cycle],[@Cycle],EmployeeRankFactsTable[Total Giving],">"&[@[Total Giving]])'
      Set-TableColumnFormula $table 'Company Rank' '=1+COUNTIFS(EmployeeRankFactsTable[Cycle],[@Cycle],EmployeeRankFactsTable[Company],[@Company],EmployeeRankFactsTable[Total Giving],">"&[@[Total Giving]])'
    } else {
      Set-TableColumnFormula $table 'Industry Rank' '=1+COUNTIFS(EmployeeListTable[Cycle],[@Cycle],EmployeeListTable[Total Giving],">"&[@[Total Giving]])'
      Set-TableColumnFormula $table 'Company Rank' '=1+COUNTIFS(EmployeeListTable[Cycle],[@Cycle],EmployeeListTable[Company],[@Company],EmployeeListTable[Total Giving],">"&[@[Total Giving]])'
    }
    Set-TableColumnFormula $table 'Total Giving' ('=' + (Cycle-Dispatch { param($t) Employee-Expression $t }))
    Set-TableColumnFormula $table 'Democratic' ('=' + (Cycle-Dispatch { param($t) Employee-Expression $t 'D' }))
    Set-TableColumnFormula $table 'Republican' ('=' + (Cycle-Dispatch { param($t) Employee-Expression $t 'R' }))
    Set-TableColumnFormula $table 'Other / Unclassified' '=[@[Total Giving]]-[@Democratic]-[@Republican]'
    Set-TableColumnFormula $table 'Net Democratic %' '=IF([@Democratic]+[@Republican]=0,0,[@Democratic]/([@Democratic]+[@Republican]))'
    Set-TableColumnFormula $table 'Net Republican %' '=IF([@Democratic]+[@Republican]=0,0,[@Republican]/([@Democratic]+[@Republican]))'
    Set-TableColumnFormula $table 'Party Coverage' '=IF([@[Total Giving]]=0,0,([@Democratic]+[@Republican])/[@[Total Giving]])'
    Set-TableColumnFormula $table 'Contribution Count' ('=' + (Cycle-Dispatch { param($t) Employee-Expression $t '' -Count }))
    Set-TableColumnFormula $table 'Integrity Check' '=IF(ABS([@[Total Giving]]-[@Democratic]-[@Republican]-[@[Other / Unclassified]])<0.01,"OK","CHECK")'
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  $sheet = $book.Worksheets.Item('Recipient List'); $table = $sheet.ListObjects.Item(1)
  try {
    Set-TableColumnFormula $table 'Rank' '=1+COUNTIFS(RecipientListTable[Cycle],[@Cycle],RecipientListTable[View],[@View],RecipientListTable[Company],[@Company],RecipientListTable[Total Giving],">"&[@[Total Giving]])'
    Set-TableColumnFormula $table 'Direct Candidate / Sponsor' '=XLOOKUP([@Cycle]&"|"&[@[Committee ID]],PACMapTable[Lookup Key],PACMapTable[Direct Candidate / Sponsor],"")'
    Set-TableColumnFormula $table 'Committee Relationship' '=XLOOKUP([@Cycle]&"|"&[@[Committee ID]],PACMapTable[Lookup Key],PACMapTable[Relationship],"")'
    Set-TableColumnFormula $table 'Ownership Status' '=XLOOKUP([@Cycle]&"|"&[@[Committee ID]],PACMapTable[Lookup Key],PACMapTable[Ownership Status],"")'
    Set-TableColumnFormula $table 'Top Downstream Congressional Beneficiary' '=XLOOKUP([@Cycle]&"|"&[@[Committee ID]],PACMapTable[Lookup Key],PACMapTable[Top Downstream Congressional Beneficiary],"")'
    Set-TableColumnFormula $table 'Top Downstream Candidate ID' '=XLOOKUP([@Cycle]&"|"&[@[Committee ID]],PACMapTable[Lookup Key],PACMapTable[Top Downstream Candidate ID],"")'
    Set-TableColumnFormula $table 'Top Downstream Activity' '=IF([@[Top Downstream Candidate ID]]="",0,SUMIFS(DownstreamActivityTable[Net Included Amount],DownstreamActivityTable[Cycle],[@Cycle],DownstreamActivityTable[Source Committee ID],[@[Committee ID]],DownstreamActivityTable[Candidate ID],[@[Top Downstream Candidate ID]]))'
    Set-TableColumnFormula $table 'Total Giving' ('=' + (Cycle-Dispatch { param($t) Recipient-Expression $t }))
    Set-TableColumnFormula $table 'Democratic' ('=' + (Cycle-Dispatch { param($t) Recipient-Expression $t 'D' }))
    Set-TableColumnFormula $table 'Republican' ('=' + (Cycle-Dispatch { param($t) Recipient-Expression $t 'R' }))
    Set-TableColumnFormula $table 'Other / Unclassified' '=[@[Total Giving]]-[@Democratic]-[@Republican]'
    Set-TableColumnFormula $table 'Net Democratic %' '=IF([@Democratic]+[@Republican]=0,0,[@Democratic]/([@Democratic]+[@Republican]))'
    Set-TableColumnFormula $table 'Net Republican %' '=IF([@Democratic]+[@Republican]=0,0,[@Republican]/([@Democratic]+[@Republican]))'
    Set-TableColumnFormula $table 'Party Coverage' '=IF([@[Total Giving]]=0,0,([@Democratic]+[@Republican])/[@[Total Giving]])'
    foreach ($tier in @('Corporate','Chief','Middle Management','Employee')) {
      Set-TableColumnFormula $table $tier ('=' + (Cycle-Dispatch { param($t) Recipient-Expression $t '' $tier }))
    }
    Set-TableColumnFormula $table 'Contribution Count' ('=' + (Cycle-Dispatch { param($t) Recipient-Expression $t '' '' -Count }))
    Set-TableColumnFormula $table 'FEC Committee URL' '=IF([@[Committee ID]]="","","https://www.fec.gov/data/committee/"&[@[Committee ID]]&"/")'
    Set-TableColumnFormula $table 'Integrity Check' '=IF(AND(ABS([@[Total Giving]]-[@Democratic]-[@Republican]-[@[Other / Unclassified]])<0.01,ABS([@[Total Giving]]-[@Corporate]-[@Chief]-[@[Middle Management]]-[@Employee])<0.01),"OK","CHECK")'
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  $sheet = $book.Worksheets.Item('PAC & Candidate Map'); $table = $sheet.ListObjects.Item(1)
  try {
    Set-TableColumnFormula $table 'Top Downstream Activity' '=IF([@[Top Downstream Candidate ID]]="",0,SUMIFS(DownstreamActivityTable[Net Included Amount],DownstreamActivityTable[Cycle],[@Cycle],DownstreamActivityTable[Source Committee ID],[@[Committee ID]],DownstreamActivityTable[Candidate ID],[@[Top Downstream Candidate ID]]))'
    Set-TableColumnFormula $table 'FEC Committee URL' '=IF([@[Committee ID]]="","","https://www.fec.gov/data/committee/"&[@[Committee ID]]&"/")'
    Set-TableColumnFormula $table 'FEC Candidate URL' '=IF(OR([@[Candidate ID(s)]]="",ISNUMBER(SEARCH(";",[@[Candidate ID(s)]]))),"","https://www.fec.gov/data/candidate/"&[@[Candidate ID(s)]]&"/")'
    Set-TableColumnFormula $table 'Integrity Check' '=IF(AND([@[Committee ID]]<>"",OR([@[Ownership Status]]="Organization/committee; no individual ownership link in FEC files",[@[Direct Candidate / Sponsor]]<>"")),"OK","CHECK")'
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  $sheet = $book.Worksheets.Item('Downstream Activity'); $table = $sheet.ListObjects.Item(1)
  try {
    Set-TableColumnFormula $table 'Net Included Amount' '=[@[Reported Amount]]-[@[Excluded Amount]]'
    Set-TableColumnFormula $table 'FEC Committee URL' '=IF([@[Source Committee ID]]="","","https://www.fec.gov/data/committee/"&[@[Source Committee ID]]&"/")'
    Set-TableColumnFormula $table 'FEC Candidate URL' '=IF([@[Candidate ID]]="","","https://www.fec.gov/data/candidate/"&[@[Candidate ID]]&"/")'
    Set-TableColumnFormula $table 'Logic Check' '=IF(AND([@[Source Rows]]=[@[Included Rows]]+[@[Excluded Rows]],ABS([@[Net Included Amount]]-([@[Reported Amount]]-[@[Excluded Amount]]))<0.01),"OK","CHECK")'
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  $sheet = $book.Worksheets.Item('Beneficiary Bridge'); $table = $sheet.ListObjects.Item(1)
  try {
    Set-TableColumnFormula $table 'Downstream Activity' '=SUMIFS(DownstreamActivityTable[Net Included Amount],DownstreamActivityTable[Cycle],[@Cycle],DownstreamActivityTable[Source Committee ID],[@[Source Committee ID]],DownstreamActivityTable[Candidate ID],[@[Candidate ID]])'
    Set-TableColumnFormula $table $associatedFundingHeader ('=' + (Cycle-Dispatch { param($t) Bridge-Funding-Expression $t }))
    Set-TableColumnFormula $table 'FEC Committee URL' '=IF([@[Source Committee ID]]="","","https://www.fec.gov/data/committee/"&[@[Source Committee ID]]&"/")'
    Set-TableColumnFormula $table 'FEC Candidate URL' '=IF([@[Candidate ID]]="","","https://www.fec.gov/data/candidate/"&[@[Candidate ID]]&"/")'
    Set-TableColumnFormula $table 'Integrity Check' ("=IF(AND([@[Source Committee ID]]<>`"`",[@[Candidate ID]]<>`"`",ISNUMBER([@[Downstream Activity]]),ISNUMBER([@[${associatedFundingHeader}]])),`"OK`",`"CHECK`")")
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  $sheet = $book.Worksheets.Item('Beneficiary Summary'); $table = $sheet.ListObjects.Item(1)
  try {
    Set-TableColumnFormula $table 'Rank' '=1+COUNTIFS(BeneficiarySummaryTable[Cycle],[@Cycle],BeneficiarySummaryTable[View],[@View],BeneficiarySummaryTable[Company],[@Company],BeneficiarySummaryTable[Downstream Activity],">"&[@[Downstream Activity]])'
    Set-TableColumnFormula $table 'Downstream Activity' '=SUMIFS(BeneficiaryBridgeTable[Downstream Activity],BeneficiaryBridgeTable[Cycle],[@Cycle],BeneficiaryBridgeTable[View],[@View],BeneficiaryBridgeTable[Company],[@Company],BeneficiaryBridgeTable[Candidate ID],[@[Candidate ID]])'
    Set-TableColumnFormula $table $associatedFundingHeader ("=SUMIFS(BeneficiaryBridgeTable[${associatedFundingHeader}],BeneficiaryBridgeTable[Cycle],[@Cycle],BeneficiaryBridgeTable[View],[@View],BeneficiaryBridgeTable[Company],[@Company],BeneficiaryBridgeTable[Candidate ID],[@[Candidate ID]])")
    # Money spent AGAINST a candidate must not be booked as activity favouring
    # their party. An independent expenditure opposing a Republican used to land
    # in "Republican Activity": $1,868,481,242 of anti-Republican spending in the
    # credit-union book alone, out of a sheet that is 89% oppose dollars.
    # The party columns now count [Support Activity], a real dollar component
    # split by direction in fec_beneficiary.R, rather than testing the joined
    # [Directions] text. Reading that text could only ever be all-or-nothing: a
    # candidate with both support and oppose legs had their SUPPORT dollars
    # discarded too, because the string contained the word "Oppose".
    # Opposition keeps its own column and is never folded into a party total.
    # Four buckets that sum to the total: money FOR a D candidate, money FOR an R
    # candidate, money for anyone else, and money spent AGAINST whoever the row
    # names. "For the candidate" is everything that is not opposition, NOT just
    # the 24E/24F independent expenditures: 24K contributions to a nonaffiliated
    # committee, 24Z in-kind, 24C coordinated party expenditures, 24P, 24R and
    # 24H are all unambiguously pro-candidate and are the ORDINARY case for a
    # corporate PAC. Counting Support alone stripped $151.4M of them out of the
    # party columns - David Perdue's $2,995,566 of "Contribution / Other" money
    # in the 2022 Bakkt view read as $0 Republican Activity at 0% coverage.
    # Opposition keeps its own column and is never folded into a party total,
    # and it no longer lands in Other / Unclassified either: that column means
    # "the candidate's party is neither D nor R" on every other sheet, and
    # $1.99B of anti-D/anti-R spending was being filed under it.
    Set-TableColumnFormula $table 'Democratic Activity' '=IF([@Party]="D",[@[Downstream Activity]]-[@[Oppose Activity]],0)'
    Set-TableColumnFormula $table 'Republican Activity' '=IF([@Party]="R",[@[Downstream Activity]]-[@[Oppose Activity]],0)'
    Set-TableColumnFormula $table 'Other / Unclassified Activity' '=IF(OR([@Party]="D",[@Party]="R"),0,[@[Downstream Activity]]-[@[Oppose Activity]])'
    # Denominator is the mixed total, matching the focused sheets (Party Coverage
    # at the bottom of this file). Using [Support Activity] here made the same
    # candidate report two different coverage numbers on two sheets of one
    # workbook - Sherrod Brown 100.0% here against 0.6% on the cycle summary -
    # and exceeded 100% wherever a negative correction pushed support above the
    # unsigned total.
    Set-TableColumnFormula $table 'Party Coverage' '=IF([@[Downstream Activity]]=0,0,([@[Democratic Activity]]+[@[Republican Activity]])/[@[Downstream Activity]])'
    Set-TableColumnFormula $table 'FEC Candidate URL' '=IF([@[Candidate ID]]="","","https://www.fec.gov/data/candidate/"&[@[Candidate ID]]&"/")'
    # Only the party-split identity is asserted here. "Support + Oppose must not
    # exceed Downstream Activity" looks like an invariant and is NOT one: FEC
    # correction and refund rows are negative and carry neither a support nor an
    # oppose transaction code, so they sit in the third direction bucket and drag
    # the unsigned total BELOW support + oppose. Measured on the credit-union
    # rebuild: 272 of 2,979 candidate rows, every residual negative, down to
    # -$735,710 (Herschel Walker: $4,913,993 total against $5,649,703 opposition).
    # Asserting the inequality failed all 272 and blocked the build on correct data.
    # The four buckets must account for the whole total. Unlike the version this
    # replaces, it is not an algebraic tautology: Oppose Activity is computed in
    # R from per-row transaction codes while Downstream Activity is an Excel
    # SUMIFS over the bridge, so the identity closes only if those two
    # independent paths agree about the same rows.
    Set-TableColumnFormula $table 'Integrity Check' '=IF(ABS([@[Downstream Activity]]-[@[Democratic Activity]]-[@[Republican Activity]]-[@[Other / Unclassified Activity]]-[@[Oppose Activity]])<0.01,"OK","CHECK")'
  } finally { Release-ComObject $table; Release-ComObject $sheet }

  $focusedSheets = @()
  foreach ($cycle in $payload.cycles) { $focusedSheets += ([string]$cycle.label + ' Summary') }
  foreach ($company in $payload.companies) {
    $companyViewName = ([string]$company).Trim() + ' View'
    $focusedSheets += $companyViewName.Substring(
      0,
      [math]::Min(31, $companyViewName.Length)
    ).TrimEnd()
  }
  foreach ($sheetName in $focusedSheets) {
    $sheet = $book.Worksheets.Item($sheetName); $table = $sheet.ListObjects.Item(1)
    try {
      $tableName = [string]$table.Name
      Set-TableColumnFormula $table 'Rank' ("=1+COUNTIFS(${tableName}[Cycle],[@Cycle],${tableName}[Section],[@Section],${tableName}[Total / Activity],`">`"&[@[Total / Activity]])")
      Set-TableColumnFormula $table 'Total / Activity' ('=' + (Focused-Expression 'Total Giving'))
      Set-TableColumnFormula $table 'Democratic' ('=' + (Focused-Expression 'Democratic'))
      Set-TableColumnFormula $table 'Republican' ('=' + (Focused-Expression 'Republican'))
      Set-TableColumnFormula $table 'Other / Unclassified' ('=' + (Focused-Expression 'Other / Unclassified'))
      Set-TableColumnFormula $table 'Party Coverage' '=IF([@[Total / Activity]]=0,0,([@Democratic]+[@Republican])/[@[Total / Activity]])'
      # Beneficiary rows carry a FOURTH bucket the other three sections do not:
      # opposition. Total / Activity is the mixed downstream figure, while
      # Democratic/Republican/Other now count only money FOR a candidate, so the
      # three-way identity leaves the opposition behind and every beneficiary row
      # reports CHECK (74 of them on the Crypto rebuild). Only the beneficiary
      # section has an Oppose column to subtract, hence the Section guard - the
      # company/contributor/recipient tables have no such column.
      $focusedOppose = (
        "IF([@Section]=`"Top Downstream Beneficiary`"," +
        "SUMIFS(BeneficiarySummaryTable[Oppose Activity]," +
        "BeneficiarySummaryTable[Cycle],[@Cycle]," +
        "BeneficiarySummaryTable[View],[@[Reference View]]," +
        "BeneficiarySummaryTable[Company],[@[Reference Company]]," +
        "BeneficiarySummaryTable[Candidate ID],[@[Reference Key]]),0)"
      )
      Set-TableColumnFormula $table 'Integrity Check' (
        "=IF(ABS([@[Total / Activity]]-[@Democratic]-[@Republican]-" +
        "[@[Other / Unclassified]]-(${focusedOppose}))<0.01,`"OK`",`"CHECK`")"
      )
    } finally { Release-ComObject $table; Release-ComObject $sheet }
  }

  $headerRows = @{
    'Master Overview'=6;'Company List'=5;'Employee List'=5;'Recipient List'=5
    'Beneficiary Summary'=5;'PAC & Candidate Map'=5;'Downstream Activity'=5;'Beneficiary Bridge'=5
    'Cycle Exceptions'=4
  }
  foreach ($cycleSheetName in $cycleSheetNames) { $headerRows[$cycleSheetName] = 4 }
  if ($formulaFacts) { $headerRows['Employee Rank Facts'] = 5 }
  $headerRows[$overviewSheetName] = 5
  foreach ($sheetName in $focusedSheets) { $headerRows[$sheetName] = 5 }
  foreach ($sheetName in $headerRows.Keys) {
    $sheet = $book.Worksheets.Item($sheetName); $sheet.Activate(); $excel.ActiveWindow.FreezePanes = $false
    $excel.ActiveWindow.SplitColumn = 0; $excel.ActiveWindow.SplitRow = $headerRows[$sheetName]; $excel.ActiveWindow.FreezePanes = $true
    Release-ComObject $sheet
  }
  $excel.Calculation = -4105; $excel.CalculateFullRebuild()
  while ($excel.CalculationState -ne 0) { Start-Sleep -Milliseconds 100 }

  $formulaColumnsBySheet = @{
    'Company List'=@('Rank','Total Giving','Democratic','Republican','Other / Unclassified','Net Democratic %','Net Republican %','Party Coverage','Corporate','Chief','Middle Management','Employee','Integrity Check')
    'Employee List'=@('Industry Rank','Company Rank','Total Giving','Democratic','Republican','Other / Unclassified','Net Democratic %','Net Republican %','Party Coverage','Contribution Count','Integrity Check')
    'Recipient List'=@('Rank','Direct Candidate / Sponsor','Committee Relationship','Ownership Status','Top Downstream Congressional Beneficiary','Top Downstream Candidate ID','Top Downstream Activity','Total Giving','Democratic','Republican','Other / Unclassified','Net Democratic %','Net Republican %','Party Coverage','Corporate','Chief','Middle Management','Employee','Contribution Count','FEC Committee URL','Integrity Check')
    'PAC & Candidate Map'=@('Top Downstream Activity','FEC Committee URL','FEC Candidate URL','Integrity Check')
    'Downstream Activity'=@('Net Included Amount','FEC Committee URL','FEC Candidate URL','Logic Check')
    'Beneficiary Bridge'=@('Downstream Activity',$associatedFundingHeader,'FEC Committee URL','FEC Candidate URL','Integrity Check')
    'Beneficiary Summary'=@('Rank','Downstream Activity',$associatedFundingHeader,'Democratic Activity','Republican Activity','Other / Unclassified Activity','Party Coverage','FEC Candidate URL','Integrity Check')
    'Cycle Exceptions'=@('Analysis Amount','Logic Check')
  }
  foreach ($cycleSheetName in $cycleSheetNames) {
    $formulaColumnsBySheet[$cycleSheetName] = @('Analysis Amount','Logic Check')
  }
  $formulaColumnsBySheet[$overviewSheetName] = @('Total Giving','Democratic','Republican','Other / Unclassified','Net Democratic %','Net Republican %','Party Coverage','Integrity Check')
  foreach ($sheetName in $focusedSheets) {
    $formulaColumnsBySheet[$sheetName] = @('Rank','Total / Activity','Democratic','Republican','Other / Unclassified','Party Coverage','Integrity Check')
  }
  $formulaCount = 0L; $formulaColumnFailures = 0L; $integrityFailures = 0L
  foreach ($sheetName in $formulaColumnsBySheet.Keys) {
    $sheet = $book.Worksheets.Item($sheetName); $table = $sheet.ListObjects.Item(1)
    try {
      foreach ($columnName in $formulaColumnsBySheet[$sheetName]) {
        $column = $table.ListColumns.Item($columnName); $range = $column.DataBodyRange
        try { $formulaCount += [long]$range.Rows.Count; if ($range.HasFormula -ne $true) { $formulaColumnFailures++ } }
        finally { Release-ComObject $range; Release-ComObject $column }
      }
      if ($sheetName -ne 'Employee Rank Facts') {
        $checkName = if ($sheetName -in @($cycleSheetNames + @('Cycle Exceptions','Downstream Activity'))) {'Logic Check'} else {'Integrity Check'}
        $column = $table.ListColumns.Item($checkName); $range = $column.DataBodyRange
        try { $integrityFailures += Count-NonOk $range } finally { Release-ComObject $range; Release-ComObject $column }
      }
    } finally { Release-ComObject $table; Release-ComObject $sheet }
  }

  $masterFormulaColumns = @(
    'Rank','Total Giving','% of Industry','Democratic','Republican','Other / Unclassified',
    'Net Democratic %','Net Republican %','Party Coverage','Prior Cycle',
    '$ Change','% Change','D $ Change','R $ Change','D Share Change','R Share Change'
  )
  foreach ($category in $masterCategories) {
    $categoryLabel = Category-Label $category
    $masterFormulaColumns += @($categoryLabel,"${categoryLabel} %","${categoryLabel} D","${categoryLabel} R","${categoryLabel} R %")
  }
  foreach ($tier in $masterTiers) {
    $masterFormulaColumns += @($tier,"${tier} %","${tier} D","${tier} R","${tier} R %")
  }
  $masterFormulaColumns += 'Integrity Check'
  $sheet = $book.Worksheets.Item('Master Overview')
  try {
    foreach ($spec in $masterSpecs) {
      $table = $sheet.ListObjects.Item([string]$spec.MasterTable)
      try {
        foreach ($columnName in $masterFormulaColumns) {
          $column = $table.ListColumns.Item($columnName); $range = $column.DataBodyRange
          try { $formulaCount += [long]$range.Rows.Count; if ($range.HasFormula -ne $true) { $formulaColumnFailures++ } }
          finally { Release-ComObject $range; Release-ComObject $column }
        }
        $column = $table.ListColumns.Item('Integrity Check'); $range = $column.DataBodyRange
        try { $integrityFailures += Count-NonOk $range } finally { Release-ComObject $range; Release-ComObject $column }
      } finally { Release-ComObject $table }
    }
  } finally { Release-ComObject $sheet }

  $expectedTotals = @{}
  foreach ($cycle in $cycleSheetNames) { $expectedTotals[$cycle] = 0.0 }
  if ($null -ne $payload.PSObject.Properties['expected_cycle_totals']) {
    foreach ($property in $payload.expected_cycle_totals.PSObject.Properties) {
      $expectedTotals[[string]$property.Name] = [double]$property.Value
    }
  } else {
    foreach ($row in $payload.raw_rows) {
      if ($row.include_in_analysis -eq 'Yes') {
        $expectedTotals[[string]$row.cycle_label] += [double]$row.amount
      }
    }
  }
  $sheet = $book.Worksheets.Item($overviewSheetName); $table = $sheet.ListObjects.Item(1); $rows = $table.DataBodyRange
  try {
    for ($index=1; $index -le $rows.Rows.Count; $index++) {
      if ([string]$rows.Cells.Item($index,2).Value2 -eq 'Total Giving') {
        $cycle=[string]$rows.Cells.Item($index,1).Value2; $actual=[double]$rows.Cells.Item($index,3).Value2
        if ([math]::Abs($actual-$expectedTotals[$cycle]) -ge 0.01) { throw "Cycle total mismatch for ${cycle}: actual=$actual expected=$($expectedTotals[$cycle])" }
      }
    }
  } finally { Release-ComObject $rows; Release-ComObject $table; Release-ComObject $sheet }
  $sheet = $book.Worksheets.Item('Master Overview')
  try {
    foreach ($spec in $masterSpecs) {
      $table = $sheet.ListObjects.Item([string]$spec.MasterTable); $rows = $table.DataBodyRange
      try {
        $industryTotal = $null; $companyTotal = 0.0
        for ($index=1; $index -le $rows.Rows.Count; $index++) {
          $company = [string]$rows.Cells.Item($index,1).Value2
          $total = [double]$rows.Cells.Item($index,3).Value2
          $prior = [double]$rows.Cells.Item($index,11).Value2
          $change = [double]$rows.Cells.Item($index,12).Value2
          if ([string]$spec.PriorCycle) {
            if ([math]::Abs(($total-$prior)-$change) -ge 0.01) {
              throw "Master Overview prior-cycle change mismatch for $($spec.Cycle) / ${company}"
            }
          } else {
            foreach ($columnIndex in 12..17) {
              if ([math]::Abs([double]$rows.Cells.Item($index,$columnIndex).Value2) -ge 0.000001) {
                throw "Master Overview oldest-cycle change should be blank/zero for $($spec.Cycle) / ${company}"
              }
            }
          }
          if ($company -eq 'Industry Total') { $industryTotal = $total } else { $companyTotal += $total }
        }
        $cycle = [string]$spec.Cycle
        if ($null -eq $industryTotal -or [math]::Abs($industryTotal-$expectedTotals[$cycle]) -ge 0.01) {
          throw "Master Overview industry total mismatch for ${cycle}: actual=$industryTotal expected=$($expectedTotals[$cycle])"
        }
        if ([math]::Abs($companyTotal-$industryTotal) -ge 0.01) {
          throw "Master Overview company sum mismatch for ${cycle}: companies=$companyTotal industry=$industryTotal"
        }
      } finally { Release-ComObject $rows; Release-ComObject $table }
    }
  } finally { Release-ComObject $sheet }
  if ($formulaColumnFailures -ne 0 -or $integrityFailures -ne 0 -or $formulaCount -le 0) {
    throw "Verification failed: formulas=$formulaCount formula_column_failures=$formulaColumnFailures integrity_failures=$integrityFailures"
  }

  # The workbook builder omits empty company-detail sheets in cycle-filtered
  # reports. Report the actual post-finalization worksheet count instead of
  # deriving it from the full roster, which falsely marked valid packages as
  # corrupt when a company had no rows in the selected cycle.
  $expectedWorksheetCount = [int]$book.Worksheets.Count
  $book.Save(); $book.Close($false); Release-ComObject $book; $book = $null
  $excel.Quit(); Release-ComObject $excel; $excel = $null
  Copy-Item -LiteralPath $workingPath -Destination $resolvedWorkbook -Force
  $watch.Stop()
  [pscustomobject]@{workbook=$resolvedWorkbook;bytes=(Get-Item -LiteralPath $resolvedWorkbook).Length;expected_worksheet_count=$expectedWorksheetCount;formulas=$formulaCount;formula_column_failures=$formulaColumnFailures;integrity_failures=$integrityFailures;cycle_totals=$expectedTotals;seconds=[math]::Round($watch.Elapsed.TotalSeconds,3)} | ConvertTo-Json -Depth 3
} finally {
  if ($null -ne $book) { try {$book.Close($false)} catch {}; Release-ComObject $book }
  if ($null -ne $excel) { try {$excel.Quit()} catch {}; Release-ComObject $excel }
  if ($hasExcelMutex) { try { [void]$excelMutex.ReleaseMutex() } catch {} }
  $excelMutex.Dispose()
  if (Test-Path -LiteralPath $workingPath) { Remove-Item -LiteralPath $workingPath -Force -ErrorAction SilentlyContinue }
}
