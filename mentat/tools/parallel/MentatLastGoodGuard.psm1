Set-StrictMode -Version 2.0

function Write-MentatAtomicJson {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )

  $directory = Split-Path -Parent $Path
  [IO.Directory]::CreateDirectory($directory) | Out-Null
  $temporary = Join-Path $directory (
    '.{0}.{1}.{2}.tmp' -f
      [IO.Path]::GetFileName($Path),
      $PID,
      [Guid]::NewGuid().ToString('N')
  )
  $encoding = New-Object Text.UTF8Encoding($false)
  [IO.File]::WriteAllText(
    $temporary,
    ($Value | ConvertTo-Json -Depth 12),
    $encoding
  )
  try {
    for ($attempt = 1; $attempt -le 10; $attempt++) {
      $backup = $null
      try {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
          $backup = "$Path.backup-$PID-$([Guid]::NewGuid().ToString('N'))"
          [IO.File]::Replace($temporary, $Path, $backup, $true)
        } else {
          [IO.File]::Move($temporary, $Path)
        }
        return
      } catch {
        $exception = $_.Exception
        $isIoException = (
          $exception -is [IO.IOException] -or
          $exception.InnerException -is [IO.IOException]
        )
        if (-not $isIoException -or $attempt -eq 10) { throw }
        $delayMilliseconds = [int][math]::Min(
          200,
          5 * [math]::Pow(2, $attempt - 1)
        )
        Start-Sleep -Milliseconds $delayMilliseconds
      } finally {
        if ($backup -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
          try { [IO.File]::Delete($backup) } catch {}
        }
      }
    }
  } finally {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) {
      try { [IO.File]::Delete($temporary) } catch {}
    }
  }
}

function ConvertTo-MentatGuardSlug {
  param([Parameter(Mandatory = $true)][string]$Value)

  $slug = [Text.RegularExpressions.Regex]::Replace(
    $Value.ToLowerInvariant(),
    '[^a-z0-9]+',
    '-'
  ).Trim('-')
  if ([string]::IsNullOrWhiteSpace($slug)) {
    throw "Industry name cannot be converted to a safe last-good slug: $Value"
  }
  return $slug
}

function Get-MentatFileEvidence {
  param([Parameter(Mandatory = $true)][string]$Path)

  $item = Get-Item -LiteralPath $Path -ErrorAction Stop
  return [ordered]@{
    path = $item.FullName
    bytes = [int64]$item.Length
    sha256 = (
      Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    last_write_utc = $item.LastWriteTimeUtc.ToString('o')
  }
}

function Test-MentatPathUnderRoot {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Root
  )

  $fullPath = [IO.Path]::GetFullPath($Path)
  $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
  return $fullPath.StartsWith(
    $fullRoot,
    [StringComparison]::OrdinalIgnoreCase
  )
}

function Get-MentatGuardMutexName {
  param(
    [Parameter(Mandatory = $true)][string]$LastGoodRoot,
    [Parameter(Mandatory = $true)][string]$Industry
  )

  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $identity = (
      [IO.Path]::GetFullPath($LastGoodRoot).ToLowerInvariant() + '|' +
      $Industry.ToLowerInvariant()
    )
    $bytes = [Text.Encoding]::UTF8.GetBytes($identity)
    $hex = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '')
    return "Local\MentatLastGood_$($hex.Substring(0, 24))"
  } finally {
    $sha.Dispose()
  }
}

function Initialize-MentatReportAvailability {
  <#
  .SYNOPSIS
  Make a previously audited report immediately available without replacing an
  existing target.

  .DESCRIPTION
  Existing targets always win. When the target is absent, the function restores
  only an immutable payload selected by an atomic current.json pointer whose
  hash, length, industry, and exact output name all validate. The final move
  does not allow overwrite, so a concurrently published report also wins.
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Industry,
    [Parameter(Mandatory = $true)][string]$TargetPath,
    [Parameter(Mandatory = $true)][string]$LastGoodRoot,
    [Parameter(Mandatory = $true)][string]$StatePath
  )

  $TargetPath = [IO.Path]::GetFullPath($TargetPath)
  $LastGoodRoot = [IO.Path]::GetFullPath($LastGoodRoot)
  $StatePath = [IO.Path]::GetFullPath($StatePath)
  $expectedName = [IO.Path]::GetFileName($TargetPath)
  $slug = ConvertTo-MentatGuardSlug $Industry
  $pointerPath = Join-Path (Join-Path $LastGoodRoot $slug) 'current.json'
  $checkedAt = [DateTimeOffset]::Now.ToString('o')

  $state = [ordered]@{
    schema_version = 1
    industry = $Industry
    expected_output_name = $expectedName
    target_path = $TargetPath
    checked_at = $checkedAt
    availability = 'absent'
    action = ''
    provenance = $null
    error = ''
  }

  try {
    if (Test-Path -LiteralPath $TargetPath -PathType Leaf) {
      $state.availability = 'present'
      $state.action = 'preserved_existing_target'
      $state.provenance = [ordered]@{
        kind = 'existing_target'
        evidence = Get-MentatFileEvidence $TargetPath
      }
      Write-MentatAtomicJson $StatePath $state
      return [pscustomobject]$state
    }

    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
      $state.action = 'no_last_good_pointer'
      Write-MentatAtomicJson $StatePath $state
      return [pscustomobject]$state
    }

    $pointer = Get-Content -LiteralPath $pointerPath -Raw |
      ConvertFrom-Json -ErrorAction Stop
    foreach ($field in @(
      'kind', 'industry', 'expected_output_name', 'payload_relative_path',
      'sha256', 'bytes', 'source_last_write_utc', 'generator_completed_at',
      'audited'
    )) {
      if ($null -eq $pointer.PSObject.Properties[$field]) {
        throw "Last-good pointer is missing required field: $field"
      }
    }
    if ([string]$pointer.kind -ne 'mentat_audited_last_good') {
      throw "Last-good pointer has an unsupported kind: $($pointer.kind)"
    }
    if (-not [bool]$pointer.audited) {
      throw 'Last-good pointer is not marked audited.'
    }
    if (
      [string]$pointer.industry -ne $Industry -or
      [string]$pointer.expected_output_name -cne $expectedName
    ) {
      throw 'Last-good pointer does not match the requested industry/output.'
    }

    $payloadPath = [IO.Path]::GetFullPath(
      (Join-Path $LastGoodRoot ([string]$pointer.payload_relative_path))
    )
    if (-not (Test-MentatPathUnderRoot $payloadPath $LastGoodRoot)) {
      throw 'Last-good payload resolves outside the configured root.'
    }
    if ([IO.Path]::GetFileName($payloadPath) -cne $expectedName) {
      throw 'Last-good payload does not preserve the exact output name.'
    }
    if (-not (Test-Path -LiteralPath $payloadPath -PathType Leaf)) {
      throw "Last-good payload is missing: $payloadPath"
    }
    $payloadEvidence = Get-MentatFileEvidence $payloadPath
    if (
      [string]$payloadEvidence.sha256 -ne
        ([string]$pointer.sha256).ToLowerInvariant() -or
      [int64]$payloadEvidence.bytes -ne [int64]$pointer.bytes
    ) {
      throw 'Last-good payload failed its recorded hash/length check.'
    }

    $targetDirectory = Split-Path -Parent $TargetPath
    [IO.Directory]::CreateDirectory($targetDirectory) | Out-Null
    $pending = Join-Path $targetDirectory (
      '.{0}.last-good-{1}-{2}.pending' -f
        $expectedName,
        $PID,
        [Guid]::NewGuid().ToString('N')
    )
    try {
      [IO.File]::Copy($payloadPath, $pending, $false)
      $pendingEvidence = Get-MentatFileEvidence $pending
      if (
        [string]$pendingEvidence.sha256 -ne [string]$payloadEvidence.sha256 -or
        [int64]$pendingEvidence.bytes -ne [int64]$payloadEvidence.bytes
      ) {
        throw 'Copied last-good payload failed verification.'
      }
      if (Test-Path -LiteralPath $TargetPath -PathType Leaf) {
        $state.availability = 'present'
        $state.action = 'preserved_concurrent_target'
        $state.provenance = [ordered]@{
          kind = 'concurrent_target'
          evidence = Get-MentatFileEvidence $TargetPath
        }
      } else {
        try {
          [IO.File]::Move($pending, $TargetPath)
        } catch [IO.IOException] {
          if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) {
            throw
          }
        }
        if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) {
          throw 'Last-good atomic publication did not create the target.'
        }
        $targetEvidence = Get-MentatFileEvidence $TargetPath
        if (
          [string]$targetEvidence.sha256 -eq [string]$payloadEvidence.sha256 -and
          [int64]$targetEvidence.bytes -eq [int64]$payloadEvidence.bytes
        ) {
          $state.availability = 'present'
          $state.action = 'restored_audited_last_good'
          $state.provenance = [ordered]@{
            kind = 'audited_last_good'
            pointer_path = $pointerPath
            payload_path = $payloadPath
            source_last_write_utc = [string]$pointer.source_last_write_utc
            generator_completed_at = [string]$pointer.generator_completed_at
            evidence = $targetEvidence
          }
        } else {
          # A different file won the race. Preserve it and record that fact.
          $state.availability = 'present'
          $state.action = 'preserved_concurrent_target'
          $state.provenance = [ordered]@{
            kind = 'concurrent_target'
            evidence = $targetEvidence
          }
        }
      }
    } finally {
      if (Test-Path -LiteralPath $pending -PathType Leaf) {
        [IO.File]::Delete($pending)
      }
    }
  } catch {
    $state.action = 'last_good_rejected'
    $state.error = $_.Exception.Message
    if (Test-Path -LiteralPath $TargetPath -PathType Leaf) {
      $state.availability = 'present'
      $state.provenance = [ordered]@{
        kind = 'existing_or_concurrent_target'
        evidence = Get-MentatFileEvidence $TargetPath
      }
    }
  }

  Write-MentatAtomicJson $StatePath $state
  return [pscustomobject]$state
}

function Save-MentatAuditedLastGood {
  <#
  .SYNOPSIS
  Store an audited report as an immutable last-good payload.

  .DESCRIPTION
  The payload is fully copied and hash-verified before current.json is
  atomically advanced. A candidate with an older or equal source timestamp
  cannot replace a different current payload.
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Industry,
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][string]$ExpectedOutputName,
    [Parameter(Mandatory = $true)][string]$LastGoodRoot,
    [Parameter(Mandatory = $true)][string]$GeneratorCompletedAt,
    [string]$AuditBasis = 'generator_exit_zero_refresh_and_package_audit'
  )

  $SourcePath = [IO.Path]::GetFullPath($SourcePath)
  $LastGoodRoot = [IO.Path]::GetFullPath($LastGoodRoot)
  if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
    throw "Audited source workbook is missing: $SourcePath"
  }
  if ([IO.Path]::GetFileName($SourcePath) -cne $ExpectedOutputName) {
    throw 'Audited source does not have the exact expected output name.'
  }

  $slug = ConvertTo-MentatGuardSlug $Industry
  $industryRoot = Join-Path $LastGoodRoot $slug
  $pointerPath = Join-Path $industryRoot 'current.json'
  [IO.Directory]::CreateDirectory($industryRoot) | Out-Null
  $sourceEvidence = Get-MentatFileEvidence $SourcePath
  $sourceTime = [DateTimeOffset]::Parse([string]$sourceEvidence.last_write_utc)
  $guardMutex = [Threading.Mutex]::new(
    $false,
    (Get-MentatGuardMutexName $LastGoodRoot $Industry)
  )
  $guardMutexOwned = $false

  try {
    try {
      $guardMutexOwned = $guardMutex.WaitOne([TimeSpan]::FromSeconds(30))
    } catch [Threading.AbandonedMutexException] {
      $guardMutexOwned = $true
    }
    if (-not $guardMutexOwned) {
      throw 'Timed out waiting for the per-industry last-good update mutex.'
    }

    if (Test-Path -LiteralPath $pointerPath -PathType Leaf) {
      try {
        $current = Get-Content -LiteralPath $pointerPath -Raw |
          ConvertFrom-Json -ErrorAction Stop
        $currentTime = [DateTimeOffset]::Parse(
          [string]$current.source_last_write_utc
        )
        $currentHash = ([string]$current.sha256).ToLowerInvariant()
        if (
          $currentTime -gt $sourceTime -or
          ($currentTime -eq $sourceTime -and
            $currentHash -ne [string]$sourceEvidence.sha256)
        ) {
          return [pscustomobject][ordered]@{
            status = 'preserved_newer_last_good'
            pointer_path = $pointerPath
            candidate = $sourceEvidence
            current_sha256 = $currentHash
            current_source_last_write_utc = $currentTime.ToString('o')
          }
        }
        if ($currentHash -eq [string]$sourceEvidence.sha256) {
          return [pscustomobject][ordered]@{
            status = 'already_current'
            pointer_path = $pointerPath
            candidate = $sourceEvidence
            current_sha256 = $currentHash
            current_source_last_write_utc = $currentTime.ToString('o')
          }
        }
      } catch {
        throw (
          'Existing last-good pointer is unreadable; refusing to replace it ' +
          "without freshness evidence. $($_.Exception.Message)"
        )
      }
    }

    $versionId = '{0}-{1}-{2}' -f
      ([DateTimeOffset]::Now.ToString('yyyyMMddTHHmmssfffffffZ')),
      ([string]$sourceEvidence.sha256).Substring(0, 16),
      ([Guid]::NewGuid().ToString('N'))
    $versionRoot = Join-Path $industryRoot $versionId
    [IO.Directory]::CreateDirectory($versionRoot) | Out-Null
    $payloadPath = Join-Path $versionRoot $ExpectedOutputName
    $pending = Join-Path $versionRoot (
      '.{0}.{1}.pending' -f $ExpectedOutputName, $PID
    )
    try {
      [IO.File]::Copy($SourcePath, $pending, $false)
      $pendingEvidence = Get-MentatFileEvidence $pending
      if (
        [string]$pendingEvidence.sha256 -ne [string]$sourceEvidence.sha256 -or
        [int64]$pendingEvidence.bytes -ne [int64]$sourceEvidence.bytes
      ) {
        throw 'Last-good archive copy failed its hash/length check.'
      }
      [IO.File]::Move($pending, $payloadPath)
    } finally {
      if (Test-Path -LiteralPath $pending -PathType Leaf) {
        [IO.File]::Delete($pending)
      }
    }

    $payloadRelativePath = $payloadPath.Substring(
      $LastGoodRoot.TrimEnd('\').Length
    ).TrimStart('\')
    $pointer = [ordered]@{
      schema_version = 1
      kind = 'mentat_audited_last_good'
      audited = $true
      audit_basis = $AuditBasis
      industry = $Industry
      expected_output_name = $ExpectedOutputName
      payload_relative_path = $payloadRelativePath
      sha256 = [string]$sourceEvidence.sha256
      bytes = [int64]$sourceEvidence.bytes
      source_last_write_utc = [string]$sourceEvidence.last_write_utc
      generator_completed_at = $GeneratorCompletedAt
      stored_at = [DateTimeOffset]::Now.ToString('o')
    }
    Write-MentatAtomicJson $pointerPath $pointer

    return [pscustomobject][ordered]@{
      status = 'stored_audited_last_good'
      pointer_path = $pointerPath
      payload_path = $payloadPath
      candidate = $sourceEvidence
    }
  } finally {
    if ($guardMutexOwned) {
      try { $guardMutex.ReleaseMutex() } catch {}
    }
    $guardMutex.Dispose()
  }
}

Export-ModuleMember -Function @(
  'Initialize-MentatReportAvailability',
  'Save-MentatAuditedLastGood'
)
