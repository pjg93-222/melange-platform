<#
.SYNOPSIS
  Launch the codex split-pane GLASS BOX process matrix. This is the permanent
  Glass Box view.

.DESCRIPTION
  Wraps fec\open_glassbox_process_matrix.ps1 with the correct bindings so it
  never has to be reconstructed by hand:

    * the SHARED event stream, so every lane and every emitter appears
    * overall_progress.json FIRST, so the progress bar reads true corpus
      coverage (collected / 18,557) rather than the collector's filtered
      remaining-work counter, which understates it badly
    * the live generation's receipts and accepted_html as artifact paths

  Two traps this exists to avoid, both of which have bitten before:
    * array parameters (-ProgressJson, -ArtifactPath) do NOT bind under
      powershell -File; they must be passed via -Command
    * binding the viewer to a dead lane's own event directory makes it show
      nothing while the lane is working fine

  Idempotent: a healthy existing matrix window is reused rather than duplicated.
#>
param(
    [string]$Workspace = "C:\Users\pgoff\OneDrive\Desktop\Melange Systems",
    [string]$Generation = "audits\legfix5\congress_advanced_pages_20260725\text_relevance_httr_full_20260727z",
    [string]$EventsDirectory = "C:\Users\pgoff\.claude\glassbox\events",
    [string]$WindowTitle = "GLASS BOX - CODEX",
    [switch]$Force,
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'
$matrix = Join-Path $Workspace 'fec\open_glassbox_process_matrix.ps1'
if (-not (Test-Path $matrix)) { throw "process matrix not found: $matrix" }

$gen = Join-Path $Workspace $Generation
$overall = Join-Path $gen 'overall_progress.json'
$runProgress = Join-Path $gen 'progress.json'
$receipts = Join-Path $gen 'receipts'
$acceptedHtml = Join-Path $gen 'accepted_html'

# Reuse a healthy window unless -Force.
$existing = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { ([string]$_.CommandLine).Contains('open_glassbox_process_matrix') })
if ($existing.Count -gt 0 -and -not $Force) {
    Write-Host ("GLASS BOX codex view already running (PID {0}); reusing it." -f $existing[0].ProcessId)
    Write-Host "Use -Force to replace it."
    return
}
if ($Force) {
    foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

# overall_progress.json is listed FIRST so the headline bar is corpus coverage.
$progressArgs = @()
if (Test-Path $overall) { $progressArgs += $overall }
if (Test-Path $runProgress) { $progressArgs += $runProgress }
$artifactArgs = @()
foreach ($p in @($receipts, $acceptedHtml)) { if (Test-Path $p) { $artifactArgs += $p } }

function Quote-List { param([string[]]$Items) ($Items | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ',' }

$command = "& '{0}' -EventsDirectory '{1}'" -f $matrix.Replace("'", "''"), $EventsDirectory.Replace("'", "''")
if ($progressArgs.Count) { $command += " -ProgressJson @({0})" -f (Quote-List $progressArgs) }
if ($artifactArgs.Count) { $command += " -ArtifactPath @({0})" -f (Quote-List $artifactArgs) }
$command += " -WindowTitle '{0}'" -f $WindowTitle.Replace("'", "''")

# -Command, never -File: array params silently fail to bind under -File and the
# window exits immediately.
$proc = Start-Process powershell.exe -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command
) -WorkingDirectory $Workspace -PassThru

Start-Sleep -Seconds 8
$alive = [bool](Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)
Write-Host ("GLASS BOX codex view: PID {0}  alive={1}" -f $proc.Id, $alive)
if (Test-Path $overall) {
    try {
        $j = Get-Content $overall -Raw | ConvertFrom-Json
        Write-Host ("  bound to corpus coverage: {0}" -f $j.summary)
    } catch { }
}

# Desktop shortcut so this is the one that gets opened, permanently.
if (-not $NoShortcut) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'GLASS BOX (codex).lnk'
    try {
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($lnk)
        $sc.TargetPath = 'powershell.exe'
        $sc.Arguments = ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $Workspace 'fec\open_glassbox_codex.ps1'))
        $sc.WorkingDirectory = $Workspace
        $sc.WindowStyle = 7            # minimised launcher; the matrix opens its own window
        $sc.Description = 'Codex split-pane GLASS BOX process matrix'
        $sc.Save()
        Write-Host ("  desktop shortcut: {0}" -f $lnk)
    } catch {
        Write-Host ("  shortcut not created: {0}" -f $_.Exception.Message)
    }
}
