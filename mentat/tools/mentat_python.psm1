# The one place a Mentat launcher chooses its Python interpreter.
#
# Order:
#   1. an explicit path the caller was given (-PythonPath, or MENTAT_PYTHON_PATH where
#      the caller already honours it);
#   2. Mentat's own venv, %LOCALAPPDATA%\Mentat\venv, when its python.exe exists and the
#      base Python named in its pyvenv.cfg is still installed;
#   3. the Codex app's runtime Python. The Codex app resets that environment: on
#      2026-09-25 a reset removed jinja2, geopy and geopandas, and "/" fell back to the
#      golden index.html;
#   4. python on PATH.
# The venv is built from mentat\requirements-console.txt. Get-MentatPythonLaunch starts a
# venv's python.exe as one process (see there).
#
# Nothing here prints. Each caller reports the choice where its own output is read, so
# a script whose stdout is parsed (a -PlanOnly plan) stays clean.

function Get-MentatVenvPath {
  $localData = $env:LOCALAPPDATA
  if ([string]::IsNullOrWhiteSpace($localData)) {
    $localData = [Environment]::GetFolderPath('LocalApplicationData')
  }
  return (Join-Path $localData 'Mentat\venv')
}

function Read-MentatVenvHome([string]$VenvPath) {
  # The base-interpreter folder `home` names in a venv's pyvenv.cfg: $null without a
  # pyvenv.cfg, '' when it names none.
  $config = Join-Path $VenvPath 'pyvenv.cfg'
  if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { return $null }
  $homeLines = @(Get-Content -LiteralPath $config | Where-Object { $_ -match '^\s*home\s*=' })
  if ($homeLines.Count -eq 0) { return '' }
  return ($homeLines[0] -split '=', 2)[1].Trim()
}

function Get-MentatVenvState([string]$VenvPath) {
  # A venv's Scripts\python.exe is a small launcher that starts the base interpreter
  # named by `home` in pyvenv.cfg. If that base is gone the venv cannot start at all.
  if (-not (Test-Path -LiteralPath (Join-Path $VenvPath 'Scripts\python.exe') -PathType Leaf)) { return 'missing' }
  $baseFolder = Read-MentatVenvHome $VenvPath
  if ($null -eq $baseFolder) { return 'pyvenv.cfg is missing' }
  if (-not $baseFolder) { return 'pyvenv.cfg names no base Python' }
  if (-not (Test-Path -LiteralPath (Join-Path $baseFolder 'python.exe') -PathType Leaf)) {
    return ('its base Python is gone from ' + $baseFolder)
  }
  return 'ok'
}

function Resolve-MentatPython {
  # Returns @{ Path; Source ('explicit' | 'venv' | 'codex-runtime' | 'PATH'); Venv; Warning },
  # or $null when no interpreter exists at all (the caller throws its own message).
  # Warning is set whenever the choice fell back past the venv.
  [CmdletBinding()]
  param([string]$ExplicitPath = '')
  $venv = Get-MentatVenvPath
  $venvPython = Join-Path $venv 'Scripts\python.exe'
  if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
    return [pscustomobject]@{ Path = $ExplicitPath; Source = 'explicit'; Venv = $venv; Warning = $null }
  }
  $state = Get-MentatVenvState $venv
  if ($state -eq 'ok') {
    return [pscustomobject]@{ Path = $venvPython; Source = 'venv'; Venv = $venv; Warning = $null }
  }
  $requirements = Join-Path (Split-Path -Parent $PSScriptRoot) 'requirements-console.txt'
  $reason = if ($state -eq 'missing') { "Mentat's venv was not found at $venv" } else { "Mentat's venv at $venv cannot start: $state" }
  $advice = 'To restore it: python -m venv "' + $venv + '" (Python 3.12), then "' + $venvPython +
    '" -m pip install --only-binary=:all: -r "' + $requirements + '".'
  $codex = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
  if (Test-Path -LiteralPath $codex -PathType Leaf) {
    return [pscustomobject]@{ Path = $codex; Source = 'codex-runtime'; Venv = $venv
      Warning = ($reason + '; using the Codex app runtime Python, which the Codex app resets (packages installed there can disappear). ' + $advice) }
  }
  $onPath = @(Get-Command python -CommandType Application -ErrorAction SilentlyContinue)
  if ($onPath.Count -gt 0) {
    return [pscustomobject]@{ Path = $onPath[0].Source; Source = 'PATH'; Venv = $venv
      Warning = ($reason + ' and the Codex app runtime Python is absent; using python on PATH. ' + $advice) }
  }
  return $null
}

function Get-MentatPythonLaunch {
  # How to start an interpreter as ONE process. A venv's Scripts\python.exe is CPython's venv
  # launcher: a second process that starts the base interpreter named in pyvenv.cfg with
  # __PYVENV_LAUNCHER__ naming itself, then waits. Starting that base with the variable set - as
  # multiprocessing does on Windows (bpo-35797) and app\python_child.py does for the console's own
  # children - gives the same venv (sys.executable, sys.prefix, site-packages) in one process: the
  # pid a caller holds is the interpreter's, and its log files close the moment it exits.
  # Returns @{ FilePath; Launcher }: Launcher is the value for __PYVENV_LAUNCHER__, or $null when
  # PythonPath is not a venv's python.exe and FilePath is PythonPath as given.
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$PythonPath)
  $asGiven = [pscustomobject]@{ FilePath = $PythonPath; Launcher = $null }
  if ([IO.Path]::GetFileName($PythonPath) -ne 'python.exe') { return $asGiven }
  $scripts = Split-Path -Parent $PythonPath
  if (-not $scripts) { return $asGiven }
  $venv = Split-Path -Parent $scripts
  if (-not $venv -or (Get-MentatVenvState $venv) -ne 'ok') { return $asGiven }
  return [pscustomobject]@{ FilePath = (Join-Path (Read-MentatVenvHome $venv) 'python.exe')
    Launcher = [IO.Path]::GetFullPath($PythonPath) }
}

Export-ModuleMember -Function Resolve-MentatPython, Get-MentatVenvPath, Get-MentatPythonLaunch
