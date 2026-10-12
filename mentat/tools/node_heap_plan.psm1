function Get-MentatNodeHeapPlan {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [int]$TotalPhysicalMb,
    [Parameter(Mandatory = $true)]
    [int]$AvailableMb,
    [ValidateRange(1,32768)]
    [int]$MinimumMb = 8192
  )

  if ($TotalPhysicalMb -le 0 -or $AvailableMb -le 0) {
    return [pscustomobject]@{
      ready = $false
      heap_mb = 0
      total_cap_mb = 0
      available_cap_mb = 0
      safe_cap_mb = 0
      reason = 'memory_detection_unavailable'
    }
  }

  $totalCapMb = [int][math]::Floor($TotalPhysicalMb * 0.75)
  $availableCapMb = [int][math]::Floor($AvailableMb * 0.70)
  $safeCapMb = [int][math]::Min(
    32768,
    [math]::Min($totalCapMb, $availableCapMb)
  )
  $ready = $safeCapMb -ge $MinimumMb

  [pscustomobject]@{
    ready = $ready
    heap_mb = if ($ready) { $safeCapMb } else { 0 }
    total_cap_mb = $totalCapMb
    available_cap_mb = $availableCapMb
    safe_cap_mb = $safeCapMb
    reason = if ($ready) { 'ready' } else { 'safe_cap_below_minimum' }
  }
}

Export-ModuleMember -Function Get-MentatNodeHeapPlan
