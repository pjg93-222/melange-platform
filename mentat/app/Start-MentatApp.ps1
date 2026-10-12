[CmdletBinding()]
param(
  [ValidateRange(1024,65526)][int]$Port = 8802,
  [string]$PythonPath = '',
  [string]$KeysFile = '',
  [string]$RScriptPath = '',
  [switch]$NoBrowser,
  [switch]$AppWindow,
  [string]$DataRoot = '',
  [string]$ErrorLog = ''
)
$ErrorActionPreference = 'Stop'
trap {
  if ($ErrorLog) { [IO.File]::WriteAllText($ErrorLog, $_.Exception.Message) }
  Write-Error $_ -ErrorAction Continue
  exit 1
}
$localDataSetting = Join-Path $PSScriptRoot 'desktop\local-appdata.path'
if (Test-Path -LiteralPath $localDataSetting) {
  $savedLocalData = (Get-Content -LiteralPath $localDataSetting -Raw).Trim()
  if ([IO.Path]::IsPathRooted($savedLocalData) -and (Test-Path -LiteralPath $savedLocalData)) {
    $env:LOCALAPPDATA = $savedLocalData
  }
}
# A configured pod console owns all backend work. Failure must never fall through to local Python.
$remoteProfile = Join-Path $env:LOCALAPPDATA 'Mentat\remote-console.json'
if (Test-Path -LiteralPath $remoteProfile) {
  if (-not (Test-Path -LiteralPath $remoteProfile -PathType Leaf)) { throw 'The remote Mentat profile is not a file. No local backend was started.' }
  $remoteModule = Join-Path $PSScriptRoot 'remote-console.psm1'
  if (-not (Test-Path -LiteralPath $remoteModule -PathType Leaf)) { throw 'The remote Mentat launcher module is missing. No local backend was started.' }
  Import-Module $remoteModule -Force
  Start-MentatRemoteConsole -ProfilePath $remoteProfile -NoBrowser:$NoBrowser -AppWindow:$AppWindow
  exit 0
}
$projectPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
function Get-MentatCodeTreeHash([string]$Folder) {
  # Must match server.py tree_sha256(code_files()): every *.py beside server.py,
  # sorted by ordinal name, folded in as name + NUL + SHA-256 bytes + NUL.
  $names = [string[]]@(Get-ChildItem -LiteralPath $Folder -File |
    Where-Object { [IO.Path]::GetExtension($_.Name) -eq '.py' } | ForEach-Object { $_.Name })
  [Array]::Sort($names, [StringComparer]::Ordinal)
  $stream = New-Object IO.MemoryStream
  $fileHasher = [Security.Cryptography.SHA256]::Create()
  try {
    foreach ($name in $names) {
      $label = [Text.Encoding]::UTF8.GetBytes($name)
      $stream.Write($label, 0, $label.Length)
      $stream.WriteByte(0)
      $digest = $fileHasher.ComputeHash([IO.File]::ReadAllBytes((Join-Path $Folder $name)))
      $stream.Write($digest, 0, $digest.Length)
      $stream.WriteByte(0)
    }
  } finally { $fileHasher.Dispose() }
  $treeHasher = [Security.Cryptography.SHA256]::Create()
  try {
    return [BitConverter]::ToString($treeHasher.ComputeHash($stream.ToArray())).Replace('-', '').ToLowerInvariant()
  } finally { $treeHasher.Dispose(); $stream.Dispose() }
}
$codeTreeHash = Get-MentatCodeTreeHash $PSScriptRoot
$reportDirectory = $env:MENTAT_REPORT_DIRECTORY
if (-not $reportDirectory) {
  $reportConfiguration = Get-Content -Raw -LiteralPath (Join-Path $projectPath 'config\report-directory.json') | ConvertFrom-Json
  if ($reportConfiguration.schema_version -ne 1) { throw 'Unsupported report-directory configuration.' }
  $reportDirectory = [string]$reportConfiguration.directory
}
if (-not [IO.Path]::IsPathRooted($reportDirectory) -or -not (Test-Path -LiteralPath $reportDirectory -PathType Container)) {
  throw 'The authoritative report folder is unavailable. No alternate folder will be used.'
}
$reportDirectory = (Resolve-Path -LiteralPath $reportDirectory).ProviderPath
$env:MENTAT_REPORT_DIRECTORY = $reportDirectory
function Test-MentatServerIdentity($health) {
  return ($health.application -eq 'MENTAT' -and $health.project -eq $projectPath -and
    $health.data_root -eq $DataRoot -and $health.report_directory -eq $reportDirectory)
}
function Test-MentatBuildCurrent($health) {
  # A process latches its build identity at import time (server.py's
  # CODE_TREE_SHA256 / ASSET_TREE_SHA256 module constants), while
  # on_disk_*_now is recomputed fresh from disk on every health call no
  # matter how old the process is - so this same pair of comparisons is
  # accurate whichever process answers it. Current means BOTH pairs still
  # agree: an interface-only edit (JS/CSS, no *.py change) leaves
  # code_sha256 fresh but assets_sha256 stale, and that must count as stale
  # too, or a reload can never clear the banner because the frozen fields
  # never change without a restart. This mirrors the same two-pair
  # comparison server.py's own /api/restart handler already makes.
  $fields = @($health.PSObject.Properties.Name)
  foreach ($field in @('code_sha256', 'on_disk_code_sha256_now', 'assets_sha256', 'on_disk_assets_sha256_now')) {
    if ($fields -notcontains $field) { return $false }
  }
  return ($health.code_sha256 -eq $health.on_disk_code_sha256_now -and
    $health.assets_sha256 -eq $health.on_disk_assets_sha256_now)
}
function Test-CurrentMentatServer($health) {
  return ((Test-MentatServerIdentity $health) -and
    (Test-MentatBuildCurrent $health) -and $health.read_only -eq $false)
}
function Test-ReplaceableMentatServer($health) {
  # Never stop a busy server, a read-only preview, or a server that does not
  # state in its own health block that it is idle. A server too old to answer
  # with busy and the build-identity fields keeps its port and gets a
  # printed notice instead.
  if (-not (Test-MentatServerIdentity $health)) { return $false }
  $fields = @($health.PSObject.Properties.Name)
  foreach ($field in @('busy', 'code_sha256', 'on_disk_code_sha256_now', 'assets_sha256', 'on_disk_assets_sha256_now', 'pid', 'read_only')) {
    if ($fields -notcontains $field) { return $false }
  }
  if ($health.read_only -ne $false) { return $false }
  if ($health.busy -ne $false) { return $false }
  if (Test-MentatBuildCurrent $health) { return $false }
  try { return ([int]$health.pid -gt 0) } catch { return $false }
}
function Get-MentatHealth([string]$Url, [int]$TimeoutMs) {
  # HttpWebRequest with no proxy: Invoke-RestMethod pays ~1 s of .NET and proxy
  # discovery on its first call here, and Invoke-WebRequest far more. A local
  # console answers in milliseconds; the client should not add seconds.
  try {
    $request = [Net.WebRequest]::Create($Url)
    $request.Proxy = $null
    $request.Timeout = $TimeoutMs
    $request.ReadWriteTimeout = $TimeoutMs
    $response = $request.GetResponse()
    try {
      $reader = New-Object IO.StreamReader($response.GetResponseStream())
      try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
  } catch { return $null }
}
function Get-MentatListeningPorts {
  # One census of every TCP listener replaces a bind probe per candidate port.
  try {
    return @([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
      ForEach-Object { $_.Port })
  } catch { return $null }
}
function Test-MentatPortListening([int]$Candidate) {
  # Windows HTTP connection retries can consume seconds on an unused port.
  # Only ask for health after a bounded loopback TCP connection succeeds.
  $client = New-Object Net.Sockets.TcpClient
  try {
    $connection = $client.ConnectAsync('127.0.0.1', $Candidate)
    return ($connection.Wait(150) -and $client.Connected)
  } catch { return $false } finally { $client.Dispose() }
}
function Get-MentatPrimeStatus([string]$Url, [int]$TimeoutMs) {
  # /api/prime-status (server.py -> prewarm.prime_status). A console that predates the route answers 404
  # and is reported as phase 'unsupported', so the window is not held for it; no answer at all is $null.
  try {
    $request = [Net.WebRequest]::Create($Url)
    $request.Proxy = $null
    $request.Timeout = $TimeoutMs
    $request.ReadWriteTimeout = $TimeoutMs
    $response = $request.GetResponse()
    try {
      $reader = New-Object IO.StreamReader($response.GetResponseStream())
      try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
  } catch [Net.WebException] {
    $failed = $_.Exception.Response
    if ($null -ne $failed -and [int]$failed.StatusCode -eq 404) {
      $failed.Dispose()
      return [pscustomobject]@{ primed = $false; phase = 'unsupported'; hot_ready = 0; hot_total = 0 }
    }
    return $null
  } catch { return $null }
}
function Wait-MentatPrimed([string]$BaseUrl, [int]$TimeoutSeconds = 120, [int]$PollMilliseconds = 1000) {
  # Hold the app window until the console has primed its hot set into memory (RACF: the most valuable
  # views first), polling once per PollMilliseconds and printing one progress line per change. It stops
  # early when the hot phase has ended without every item ready (a later poll cannot add one: the disk
  # warmer builds the rest in the background) or the console has no prime; after TimeoutSeconds the
  # window opens anyway and the priming continues behind it.
  $watch = [Diagnostics.Stopwatch]::StartNew()
  $last = ''
  $status = $null
  while ($true) {
    $status = Get-MentatPrimeStatus "$BaseUrl/api/prime-status" 2000
    $seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
    if ($null -ne $status) {
      $line = 'Priming ' + [string]$status.hot_ready + '/' + [string]$status.hot_total + ' (' + [string]$status.phase + ')'
      if ($line -ne $last) { Write-Host $line; $last = $line }
      if ($status.primed -eq $true) {
        return [pscustomobject]@{ Primed = $true; Reason = 'primed'; Seconds = $seconds; Status = $status }
      }
      if (@('background', 'done', 'failed', 'disabled', 'unsupported') -contains [string]$status.phase) {
        return [pscustomobject]@{ Primed = $false; Reason = ('phase ' + [string]$status.phase); Seconds = $seconds; Status = $status }
      }
    }
    $left = [int]($TimeoutSeconds * 1000 - $watch.Elapsed.TotalMilliseconds)
    if ($left -le 0) {
      return [pscustomobject]@{ Primed = $false; Reason = 'timeout'; Seconds = $seconds; Status = $status }
    }
    Start-Sleep -Milliseconds ([Math]::Max(1, [Math]::Min($PollMilliseconds, $left)))
  }
}
if (-not $DataRoot) { $DataRoot = Join-Path $env:LOCALAPPDATA 'Mentat\console' }
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$hash = [Security.Cryptography.SHA256]::Create()
$identity = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectPath + $DataRoot))).Replace('-', '')
$hash.Dispose()
$mutex = New-Object Threading.Mutex($false, ('Local\MENTAT-Startup-' + $identity))
$ownsMutex = $false
try {
  try { $ownsMutex = $mutex.WaitOne(45000) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
  if (-not $ownsMutex) { throw 'Another MENTAT launch is still starting. Please try again shortly.' }
  $available = $false
  $portReceipt = Join-Path $DataRoot ("desktop-$identity.port")
  $ports = @($Port..($Port + 9))
  if (Test-Path -LiteralPath $portReceipt) {
    $savedPort = 0
    if ([int]::TryParse((Get-Content -LiteralPath $portReceipt -Raw).Trim(), [ref]$savedPort) -and $savedPort -in $ports) {
      $ports = @($savedPort) + @($ports | Where-Object { $_ -ne $savedPort })
    }
  }
  $freePort = $false
  $freeCandidate = 0
  $stalePort = 0
  $staleHealth = $null
  $reclaimed = $false
  $listening = Get-MentatListeningPorts
  foreach ($candidate in $ports) {
    $url = "http://127.0.0.1:$candidate"
    if ($null -ne $listening) {
      $isFree = ($listening -notcontains $candidate)
    } else {
      $probe = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $candidate)
      $isFree = $false
      try { $probe.Start(); $isFree = $true } catch { } finally { $probe.Stop() }
    }
    if ($isFree) {
      if (-not $freePort) {
        # One bind probe confirms the first free candidate before it is chosen.
        $probe = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $candidate)
        $confirmed = $false
        try { $probe.Start(); $confirmed = $true } catch { } finally { $probe.Stop() }
        if ($confirmed) { $freePort = $true; $freeCandidate = $candidate }
      }
      continue
    }
    if ($null -eq $listening -and -not (Test-MentatPortListening $candidate)) { continue }
    try {
      $health = Get-MentatHealth "$url/api/health" 2000
      if ($null -eq $health) { continue }
      if (Test-CurrentMentatServer $health) {
        $available = $true; $Port = $candidate; break
      }
      if (-not $staleHealth -and (Test-ReplaceableMentatServer $health)) {
        $stalePort = $candidate; $staleHealth = $health
      }
    } catch { }
  }
  if (-not $available -and $staleHealth) {
    # An idle console of this identity running older code gives its own port
    # back, so the window a user already has open stays the current one.
    $stalePid = 0
    try { $stalePid = [int]$staleHealth.pid } catch { $stalePid = 0 }
    $owner = $null
    if ($stalePid -gt 0) {
      try { $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $stalePid" -ErrorAction Stop } catch { $owner = $null }
    }
    if ($owner -and $owner.CommandLine -and $owner.CommandLine.Contains('server.py')) {
      Write-Host ("Stopping stale MENTAT server pid " + $stalePid + " on port " + $stalePort)
      try { Stop-Process -Id $stalePid -Force -ErrorAction Stop }
      catch { Write-Host ("Could not stop pid " + $stalePid + ": " + $_.Exception.Message) }
      $stopWatch = [Diagnostics.Stopwatch]::StartNew()
      while ($stopWatch.Elapsed.TotalSeconds -lt 5 -and (Test-MentatPortListening $stalePort)) { Start-Sleep -Milliseconds 100 }
      if (Test-MentatPortListening $stalePort) {
        Write-Host ("Port " + $stalePort + " is still in use; opening another local port.")
      } else { $Port = $stalePort; $freePort = $true; $reclaimed = $true }
    } else {
      Write-Host ("Leaving the server on port " + $stalePort + " alone; no matching server.py process owns it.")
    }
  }
  if (-not $available -and -not $freePort) { throw 'No local MENTAT port is available. Close an unused local app and try again.' }
  if (-not $available) {
    if (-not $reclaimed) { $Port = $freeCandidate }
# One interpreter order for every Mentat launcher (tools\mentat_python.psm1): an explicit
# -PythonPath first (a D-029 restart passes the one its server runs on), then Mentat's own
# venv (%LOCALAPPDATA%\Mentat\venv), then the Codex app's runtime Python, then python on
# PATH. A copy of this launcher without the tools folder still starts an explicit
# interpreter, as given.
$pythonModule = Join-Path $projectPath 'tools\mentat_python.psm1'
$pythonSource = 'explicit -PythonPath'
$pythonWarning = $null
$pythonLaunch = $null
if (Test-Path -LiteralPath $pythonModule -PathType Leaf) {
  Import-Module $pythonModule -Force
  if (-not $PythonPath) {
    $pythonChoice = Resolve-MentatPython
    if ($null -eq $pythonChoice) { throw 'Python 3.11 or newer is required. Create the Mentat venv described in mentat\requirements-console.txt.' }
    $PythonPath = $pythonChoice.Path
    $pythonSource = $pythonChoice.Source
    $pythonWarning = $pythonChoice.Warning
  }
  $pythonLaunch = Get-MentatPythonLaunch $PythonPath
} elseif (-not $PythonPath) {
  throw ('The interpreter chooser is missing: ' + $pythonModule)
}
Write-Host ('MENTAT Python: ' + $PythonPath + ' (' + $pythonSource + ')')
if ($pythonWarning) { Write-Warning $pythonWarning }
# The server imports openpyxl itself. A second Python process just to check for
# it cost a full interpreter start (2.8 s on a loaded host). The package folder
# beside the interpreter is checked instead; the process check remains as the
# fallback for a layout this does not recognise.
$pythonHome = Split-Path -Parent $PythonPath
# A venv keeps its packages one level above Scripts\python.exe, in <venv>\Lib\site-packages.
if ($pythonHome -and (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $pythonHome) 'pyvenv.cfg'))) {
  $pythonHome = Split-Path -Parent $pythonHome
}
$openpyxlPresent = (Test-Path -LiteralPath (Join-Path $pythonHome 'Lib\site-packages\openpyxl')) -or
  (Test-Path -LiteralPath (Join-Path $pythonHome 'lib\site-packages\openpyxl'))
if (-not $openpyxlPresent) {
  & $PythonPath -c "import sys, importlib.util; assert sys.version_info >= (3,11), 'Python 3.11 or newer is required'; assert importlib.util.find_spec('openpyxl') is not None, 'openpyxl is required'" 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'MENTAT requires Python 3.11 or newer with openpyxl. Run: python -m pip install openpyxl' }
}
# Jinja2 renders the console page from app/templates/. Checked the same cheap
# way, for the same reason. A miss is a warning and not a throw: console_page.py
# falls back to the golden app/index.html, so every route still answers.
$jinja2Present = (Test-Path -LiteralPath (Join-Path $pythonHome 'Lib\site-packages\jinja2')) -or
  (Test-Path -LiteralPath (Join-Path $pythonHome 'lib\site-packages\jinja2'))
if (-not $jinja2Present) {
  & $PythonPath -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('jinja2') else 1)" 2>&1 | Out-Null
  $jinja2Present = ($LASTEXITCODE -eq 0)
}
if (-not $jinja2Present) {
  Write-Warning 'Jinja2 is missing, so MENTAT will serve the last generated app/index.html instead of rendering app/templates/. Run: python -m pip install jinja2'
}
$arguments = @((Join-Path $PSScriptRoot 'server.py'), '--port', [string]$Port, '--data-root', $DataRoot)
if ($KeysFile) { $arguments += @('--keys-file', $KeysFile) }
if ($RScriptPath) { $arguments += @('--rscript', $RScriptPath) }
$url = "http://127.0.0.1:$Port"
  $logDirectory = $DataRoot
  [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
  $quotedArguments = ($arguments | ForEach-Object {
    '"' + $_.Replace('"', '\"') + '"'
  }) -join ' '
  # A venv's python.exe is CPython's venv launcher, a second process in front of the server.
  # Started as the venv's base interpreter with __PYVENV_LAUNCHER__ naming that python.exe
  # (Get-MentatPythonLaunch), the server runs in the same venv as ONE process: the pid here is
  # the pid /api/health reports, and its log files close the moment it exits.
  $serverExecutable = $PythonPath
  $pyvenvLauncher = $null
  if ($pythonLaunch -and $pythonLaunch.Launcher) {
    $serverExecutable = $pythonLaunch.FilePath
    $pyvenvLauncher = $pythonLaunch.Launcher
    $env:__PYVENV_LAUNCHER__ = $pyvenvLauncher
  }
  try {
    $process = Start-Process -FilePath $serverExecutable -ArgumentList $quotedArguments `
      -WindowStyle Hidden -PassThru -WorkingDirectory $reportDirectory `
      -RedirectStandardOutput (Join-Path $logDirectory "server-$Port.stdout.log") `
      -RedirectStandardError (Join-Path $logDirectory "server-$Port.stderr.log")
  } finally {
    if ($pyvenvLauncher) { Remove-Item -LiteralPath 'Env:\__PYVENV_LAUNCHER__' -ErrorAction SilentlyContinue }
  }
  $null = $process.Handle
  $startupWatch = [Diagnostics.Stopwatch]::StartNew()
  while ($startupWatch.Elapsed.TotalSeconds -lt 30) {
    if ($process.HasExited) { throw "MENTAT could not start. See $logDirectory\server-$Port.stderr.log." }
    if (Test-MentatPortListening $Port) {
      $health = Get-MentatHealth "$url/api/health" 1000
      if ($null -ne $health -and (Test-CurrentMentatServer $health)) { $available = $true; break }
    }
    Start-Sleep -Milliseconds 100
  }
  if (-not $available) { throw 'Mentat app has not responded yet. Check its local server log.' }
}
[IO.Directory]::CreateDirectory($DataRoot) | Out-Null
[IO.File]::WriteAllText($portReceipt, [string]$Port)
} finally {
  if ($ownsMutex) { $mutex.ReleaseMutex() }
  $mutex.Dispose()
}
if (-not $NoBrowser) {
  # Primed before anyone views anything: the window opens once the console's hot set is in memory, or
  # after 120 s at the latest. A -NoBrowser restart (D-029) never waits here.
  $prime = Wait-MentatPrimed "http://127.0.0.1:$Port" 120 1000
  if ($prime.Primed) { Write-Host ('Primed in ' + $prime.Seconds + ' s') }
  else { Write-Host ('Opening after ' + $prime.Seconds + ' s (' + $prime.Reason + '); priming continues in the background') }
  if ($AppWindow) {
    $browserCandidates = @(
      "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
      "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
      "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
      "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
      "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
    $browserPath = $browserCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $browserPath) { throw 'Install Microsoft Edge or Google Chrome to use the MENTAT app window.' }
    $profilePath = Join-Path $env:LOCALAPPDATA 'Mentat\browser'
    # The app profile is Mentat's own: no extensions, sync, background networking
    # or component updates, all of which delay the first paint of the window.
    Start-Process -FilePath $browserPath -ArgumentList @("--app=$url", ('--user-data-dir="' + $profilePath + '"'), '--no-first-run', '--no-default-browser-check',
      '--disable-extensions', '--disable-sync', '--disable-background-networking', '--disable-component-update')
  } else { Start-Process $url }
}
Write-Host "Mentat app: $url"
