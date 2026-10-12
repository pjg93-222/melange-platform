param(
    [Parameter(Mandatory = $true)][string]$BatchRoot,
    [ValidateRange(1, 4)][int]$CpuThreads = 2,
    [int]$WaitForCollectionProcessId = 0
)

# Finite isolated repair worker: it cannot write the original collection
# manifest, fabricate review records, or publish profiles.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$RepairWorkspace = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems'
Set-Location -LiteralPath $RepairWorkspace
$RepairRoot = (Resolve-Path -LiteralPath $BatchRoot).Path
if ($WaitForCollectionProcessId -gt 0) {
    $CollectionProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$WaitForCollectionProcessId"
    if ($CollectionProcess) {
        if (-not $CollectionProcess.CommandLine.Contains('continue_member_longform_collection_20260904.ps1')) {
            throw 'Wait target is not the expected collection launcher.'
        }
        Write-Output ('Waiting for collection launcher ' + $WaitForCollectionProcessId + '; repair manifests are not being written yet.')
        Wait-Process -Id $WaitForCollectionProcessId -ErrorAction SilentlyContinue
    }
}
$RepairLock = [System.IO.File]::Open((Join-Path $RepairRoot 'repair-writer.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    & python -u -m fec.speech.transcribe_member_longform_local_asr `
        --acquisition (Join-Path $RepairRoot 'acquisitions.jsonl') `
        --transcripts (Join-Path $RepairRoot 'transcripts.jsonl') `
        --receipt (Join-Path $RepairRoot 'transcription.receipt.json') `
        --transcript-root (Join-Path $RepairRoot 'words') `
        --cpu-threads $CpuThreads --language auto --no-condition-on-previous-text --shortest-first --retry-errors --bounded-feature-memory
    if ($LASTEXITCODE -ne 0) { throw "Isolated transcription repair failed: $LASTEXITCODE" }
} finally {
    $RepairLock.Dispose()
}
