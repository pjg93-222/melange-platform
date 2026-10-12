$ErrorActionPreference = 'Stop'
$path = Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\finalize_crypto_excel.ps1'
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
if ($errors.Count) { throw $errors[0].Message }
# Load only pure formula helpers. This test never starts Excel or edits a book.
foreach ($name in @('Category-Label','Scope-Expression','Master-Expression')) {
  $definition = $ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
  }, $true)
  if ($definition.Count -ne 1) { throw "Missing helper $name" }
  Invoke-Expression $definition[0].Extent.Text
}
$formulaFacts = $true
$scope = Scope-Expression 'Cycle2024' 'D'
if (-not $scope.Contains('IF([@Scope]="Company PAC receipts","Company PAC",[@Scope])')) {
  throw 'Displayed PAC receipt scope must map to the canonical source category.'
}
$script:captured = @{}
function Set-TableColumnFormula($Table, [string]$ColumnName, [string]$Formula) {
  $script:captured[$ColumnName] = $Formula
}
$categoryLoop = $ast.FindAll({ param($node)
  $node -is [Management.Automation.Language.ForEachStatementAst] -and
  $node.Variable.VariablePath.UserPath -eq 'category' -and
  $node.Body.Extent.Text.Contains('Set-TableColumnFormula')
}, $true)
if ($categoryLoop.Count -ne 1) { throw 'Expected one master category formula loop.' }
$masterCategories = @('Company PAC','Conduit','Other PAC')
$sourceTableName = 'Cycle2024'
$table = $null
Invoke-Expression $categoryLoop[0].Extent.Text
foreach ($label in @('Company PAC receipts','Conduit','Other PAC')) {
  foreach ($suffix in @('',' %',' D',' R',' R %')) {
    if (-not $captured.ContainsKey($label + $suffix)) { throw "Missing formula for $label$suffix" }
  }
}
if ($captured.ContainsKey('Company PAC')) { throw 'Formula writer used the obsolete display header.' }
if (-not $captured['Company PAC receipts'].Contains('[Recipient Category],"Company PAC"')) {
  throw 'Receipt display wording leaked into the source SUMIFS criterion.'
}
if (-not $captured['Company PAC receipts R %'].Contains('[@[Company PAC receipts D]]')) {
  throw 'PAC partisan formulas must reference the renamed columns.'
}
# Conduit (ActBlue, WinRed) is its own channel: its column is headed Conduit and
# sums the Conduit recipient category, with its own D/R split.
if (-not $captured['Conduit'].Contains('[Recipient Category],"Conduit"')) {
  throw 'The Conduit channel must sum the Conduit recipient category.'
}
if (-not $captured['Conduit R'].Contains('[Recipient Category],"Conduit"') -or -not $captured['Conduit R'].Contains('[Party],"R"')) {
  throw 'The Conduit R column must sum Conduit receipts to Republican recipients.'
}
if ($captured['Conduit'].Contains('"Carey"') -or $captured['Other PAC'].Contains('"Conduit"')) {
  throw 'Category formulas must not bleed into each other.'
}
Write-Host 'Test-MentatFundingLabels: PASS'
