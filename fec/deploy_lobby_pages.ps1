# Rebuild and deploy the industry tab and its filings drill-down.
#
# Run this from ANYWHERE. It resolves the worktree that holds the source and
# points MONEYBALL_ROOT at the live tree, because bills_audit.csv is gitignored
# and therefore absent from worktree checkouts -- the single most common reason
# a hand-typed build fails.
#
#   powershell -File fec\deploy_lobby_pages.ps1
#   powershell -File fec\deploy_lobby_pages.ps1 -SkipLedger      # pages only
#
# Ledger and positions are SEPARATE, slower steps and are skipped unless asked
# for, because rebuilding the ledger walks every sweep page (~25 min) and
# classifying positions spends Anthropic credit.
#
#   -RebuildLedger      rebuild the interaction ledger first
#   -SkipLedger         never rebuild the ledger, even if -RebuildLedger is also given
#   -ClassifyPositions  assemble work items, classify (SPENDS API CREDIT), join onto the ledger

param(
    [string]$LiveTree = "C:\Users\pgoff\OneDrive\Desktop\Melange Systems",
    [switch]$RebuildLedger,
    [switch]$ClassifyPositions,
    [switch]$SkipLedger
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot   # the tree this script lives in
$env:MONEYBALL_ROOT = $LiveTree
$derived = Join-Path $env:LOCALAPPDATA "Melange\lda_rebuild\derived\interactions"
$doLedger = $RebuildLedger -and -not $SkipLedger

Write-Host "source tree : $Source"
Write-Host "live tree   : $LiveTree"
if ($RebuildLedger -and $SkipLedger) {
    Write-Host "ledger      : -SkipLedger overrides -RebuildLedger; ledger NOT rebuilt"
}
Write-Host ""

if ($doLedger) {
    Write-Host "== interaction ledger (walks every sweep page; several minutes) =="
    & python (Join-Path $Source "fec\lobbying\lobby_interactions.py")
    if ($LASTEXITCODE -ne 0) { throw "ledger build failed ($LASTEXITCODE)" }
    Write-Host ""
}

if ($ClassifyPositions) {
    Write-Host "== position work items =="
    & python (Join-Path $Source "fec\lobbying\lobby_position_items.py")
    if ($LASTEXITCODE -ne 0) { throw "work-item assembly failed ($LASTEXITCODE)" }

    Write-Host "== Haiku classification (SPENDS API CREDIT) =="
    $key = Join-Path $env:LOCALAPPDATA "Melange\lda_rebuild\anthropic_key.txt"
    & python (Join-Path $Source "fec\lobbying\classify_bill_positions.py") --classify `
        --input (Join-Path $derived "position_work_items.jsonl") `
        --output (Join-Path $derived "bill_positions.jsonl") `
        --report (Join-Path $derived "bill_positions_report.json") `
        --prompt-variant zeroshot `
        --api-key-file $key --workers 8
    if ($LASTEXITCODE -ne 0) { throw "classification failed ($LASTEXITCODE)" }

    Write-Host "== join positions onto the ledger =="
    & python (Join-Path $Source "fec\lobbying\lobby_position_join.py")
    if ($LASTEXITCODE -ne 0) { throw "position join failed ($LASTEXITCODE)" }
    Write-Host ""
}

Write-Host "== industry page =="
& python (Join-Path $Source "fec\surfaces\make_lobby_network_html.py") --out (Join-Path $LiveTree "lobby-network.html")
if ($LASTEXITCODE -ne 0) { throw "industry page build failed ($LASTEXITCODE)" }

Write-Host "== filings page =="
& python (Join-Path $Source "fec\surfaces\make_lobby_interactions_html.py") --out (Join-Path $LiveTree "lobby-interactions.html")
if ($LASTEXITCODE -ne 0) { throw "filings page build failed ($LASTEXITCODE)" }

Write-Host ""
Write-Host "DEPLOYED:"
foreach ($name in @("lobby-network.html", "lobby-interactions.html")) {
    $path = Join-Path $LiveTree $name
    $info = Get-Item $path
    Write-Host ("  {0,-26} {1,12:N0} bytes  {2}" -f $info.Name, $info.Length, $info.LastWriteTime)
}
