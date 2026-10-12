[CmdletBinding()]
param([string]$ModulePath=(Join-Path $PSScriptRoot '../app/remote-console.psm1'), [switch]$LivePod, [string]$LegacyCatalogPath)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Import-Module $ModulePath -Force
$module=Get-Module remote-console
$work=Join-Path ([IO.Path]::GetTempPath()) ('mentat-remote-tests-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($work)
$script:Passed=0
function Check([bool]$Value,[string]$Name) { if(-not $Value){throw ('FAIL '+$Name)}; $script:Passed++; Write-Output ('PASS '+$Name) }
function Must-Fail([scriptblock]$Action,[string]$Pattern,[string]$Name) { $message='';try{& $Action}catch{$message=$_.Exception.Message}; Check ($message -match $Pattern) $Name }
$profile=[pscustomobject]@{SchemaVersion=1;Host='81.27.69.177';SshPort=36669;User='root';KeyPath='C:\Users\pgoff\.ssh\id_ed25519';LocalPort=8808;RemotePort=8808;RemoteStartScript='/workspace/forecast-audit-5603f2/deployment/start-mentat-console.sh';ExpectedProject='/workspace/forecast-audit-5603f2/repo/mentat'}
try {
 $validated=& $module {param($p) ConvertTo-MentatRemoteProfile $p} $profile
 Check ($validated.LocalPort -eq 8808) 'profile accepts scoped pod configuration'
 foreach($case in @(@('Host','host;touch','Invalid SSH host'),@('RemoteStartScript','/tmp/a;touch','Invalid RemoteStartScript'),@('RemoteStartScript','/tmp/../other','Invalid RemoteStartScript'),@('KeyPath','relative.key','absolute Windows'),@('RemotePort',8809,'must match'))) {
  $copy=$profile|ConvertTo-Json|ConvertFrom-Json;$copy.($case[0])=$case[1]
  Must-Fail {& $module {param($p) ConvertTo-MentatRemoteProfile $p} $copy} $case[2] ('profile rejects '+$case[0]+' '+$case[1])
 }
 $args=& $module {param($p) Get-MentatSshArguments $p} $profile
 Check ($args -contains 'BatchMode=yes' -and $args -contains 'StrictHostKeyChecking=yes') 'SSH never prompts or silently accepts a changed host'
 $quoted=& $module {Join-MentatProcessArguments @('C:\Space Folder\key','bash /safe/script','trailing\')}
 Check ($quoted -ceq '"C:\Space Folder\key" "bash /safe/script" "trailing\\"') 'Windows argument quoting preserves spaces and trailing backslashes'
 $health=[pscustomobject]@{application='MENTAT';project=$profile.ExpectedProject;read_only=$true;code_sha256=('a'*64);on_disk_code_sha256_now=('a'*64);assets_sha256=('b'*64);on_disk_assets_sha256_now=('b'*64)}
 $catalog=[pscustomobject]@{schema_version=2;model_version='refit-approved';model_sha256=('c'*64)}
 & $module {param($p,$h,$c) Test-MentatRemoteState $p $h $c} $profile $health $catalog
 Check $true 'healthy remote project and forecast v2 accepted'
 foreach($case in @(@('project','/wrong','different application'),@('read_only',$false,'read-only'),@('on_disk_code_sha256_now',('d'*64),'stale code'),@('code_sha256','','Invalid remote build hash'))) {
  $copy=$health|ConvertTo-Json|ConvertFrom-Json;$copy.($case[0])=$case[1]
  Must-Fail {& $module {param($p,$h,$c) Test-MentatRemoteState $p $h $c} $profile $copy $catalog} $case[2] ('health rejects '+$case[0])
 }
 $legacy=[pscustomobject]@{model_version='giving-v1';model_sha256=('d'*64);built_on='2026-09-30';currency='USD';industries=@();share_models=[pscustomobject]@{};pac_model=[pscustomobject]@{};lobby_model=[pscustomobject]@{};evidence=@();industry_slopes=@();validation_metrics=@();sources=@()}
 & $module {param($p,$h,$c) Test-MentatRemoteState $p $h $c} $profile $health $legacy
 Check $true 'legacy v1 catalog without schema accepts authoritative-pointer rollback'
 $explicitLegacy=$legacy|ConvertTo-Json -Depth 5|ConvertFrom-Json;$explicitLegacy|Add-Member NoteProperty schema_version 1
 & $module {param($p,$h,$c) Test-MentatRemoteState $p $h $c} $profile $health $explicitLegacy
 Check $true 'explicit supported v1 catalog accepted'
 $badCatalog=$catalog|ConvertTo-Json|ConvertFrom-Json;$badCatalog.schema_version=3
 Must-Fail {& $module {param($p,$h,$c) Test-MentatRemoteState $p $h $c} $profile $health $badCatalog} 'Unsupported forecast catalog schema' 'unknown forecast schema fails closed'
 $badLegacy=$legacy|ConvertTo-Json -Depth 5|ConvertFrom-Json;$badLegacy.PSObject.Properties.Remove('pac_model')
 Must-Fail {& $module {param($p,$h,$c) Test-MentatRemoteState $p $h $c} $profile $health $badLegacy} 'omitted pac_model' 'legacy catalog requires production model blocks'
 $badLegacy=$legacy|ConvertTo-Json -Depth 5|ConvertFrom-Json;$badLegacy.model_sha256=''
 Must-Fail {& $module {param($p,$h,$c) Test-MentatRemoteState $p $h $c} $profile $health $badLegacy} 'forecast identity' 'legacy catalog requires valid model hash'
 if($LegacyCatalogPath) {
  $actualLegacy=Get-Content -LiteralPath $LegacyCatalogPath -Raw|ConvertFrom-Json
  & $module {param($p,$h,$c) Test-MentatRemoteState $p $h $c} $profile $health $actualLegacy
  Check $true 'actual consumer-generated v1 rollback catalog passes without pointer changes'
 }
 if($LivePod) {
  & $module {param($p) $h=Get-MentatRemoteJson 'http://127.0.0.1:8808/api/health';$c=Get-MentatRemoteJson 'http://127.0.0.1:8808/api/forecast/catalog';Test-MentatRemoteState $p $h $c} $profile
  Check $true 'real full pod server health and published catalog pass transport contract'
 }
 $profileFile=Join-Path $work 'remote-console.json';$profile|ConvertTo-Json|Set-Content -LiteralPath $profileFile
 $env:SystemRoot='/fake-windows'
 & $module {
  param($p,$h,$c)
  $script:TestProfile=$p;$script:TestHealth=$h;$script:TestCatalog=$c
  function script:Test-Path {param([string]$LiteralPath,[string]$PathType) if($LiteralPath -eq $script:TestProfile.KeyPath -or $LiteralPath.EndsWith('ssh.exe')){return $true};Microsoft.PowerShell.Management\Test-Path @PSBoundParameters}
  function script:Invoke-MentatPodStart {param($SshPath,$Profile) $script:Starts++;if($script:FailStart){throw 'pod unavailable; no local backend'}}
  function script:Get-MentatOwnedTunnel {param($ReceiptPath,$Identity,$SshPath) if($script:Reuse){return $script:Tunnel};return $null}
  function script:Get-MentatTunnelListeners {param($Port) return $script:Listeners}
  function script:Start-MentatSshTunnel {param($SshPath,$Profile,$LogPrefix) $script:Creates++;$script:Listeners=@([pscustomobject]@{LocalAddress='127.0.0.1';OwningProcess=$script:Tunnel.Id});return $script:Tunnel}
  function script:Get-MentatRemoteJson {param($Url) if($Url.EndsWith('/health')){return $script:TestHealth};return $script:TestCatalog}
  function script:Open-MentatRemoteWindow {param($Url,[switch]$AppWindow) $script:Windows++}
 } $profile $health $catalog
 function Reset-Transport([bool]$Reuse=$false,[bool]$FailStart=$false,[bool]$Collision=$false,[bool]$Exit=$false) {
  & $module {
   param($reuse,$fail,$collision,$exit)
   $script:Starts=0;$script:Creates=0;$script:Windows=0;$script:Reuse=$reuse;$script:FailStart=$fail
   $script:Tunnel=[pscustomobject]@{Id=32123;HasExited=$exit;StartTime=[DateTime]::UtcNow;Killed=$false}
   $script:Tunnel|Add-Member ScriptMethod Refresh {}
   $script:Tunnel|Add-Member ScriptMethod Kill {$this.Killed=$true;$this.HasExited=$true}
   $script:Listeners=@()
   if($reuse){$script:Listeners=@([pscustomobject]@{LocalAddress='127.0.0.1';OwningProcess=32123})}
   if($collision){$script:Listeners=@([pscustomobject]@{LocalAddress='127.0.0.1';OwningProcess=99999})}
  } $Reuse $FailStart $Collision $Exit
 }
 function Snapshot {& $module { [pscustomobject]@{Starts=$script:Starts;Creates=$script:Creates;Windows=$script:Windows;Killed=$script:Tunnel.Killed} }}
 Reset-Transport
 Start-MentatRemoteConsole -ProfilePath $profileFile -NoBrowser
 $state=Snapshot;Check ($state.Starts -eq 1 -and $state.Creates -eq 1 -and $state.Windows -eq 0) 'new connection ensures pod and owns one tunnel without opening a browser'
 Reset-Transport -Reuse $true
 Start-MentatRemoteConsole -ProfilePath $profileFile -NoBrowser
 $state=Snapshot;Check ($state.Starts -eq 1 -and $state.Creates -eq 0 -and -not $state.Killed) 'existing owned tunnel reused after fresh remote verification'
 Reset-Transport -Collision $true
 Must-Fail {Start-MentatRemoteConsole -ProfilePath $profileFile -NoBrowser} 'another process' 'unowned occupied port fails closed'
 $state=Snapshot;Check ($state.Starts -eq 0 -and $state.Creates -eq 0 -and -not $state.Killed) 'collision does not start backend or kill any process'
 Reset-Transport -FailStart $true
 Must-Fail {Start-MentatRemoteConsole -ProfilePath $profileFile -NoBrowser} 'pod unavailable' 'unavailable pod is a remote error'
 $state=Snapshot;Check ($state.Creates -eq 0 -and $state.Windows -eq 0) 'unavailable pod has no tunnel/browser/local fallback'
 Reset-Transport -Exit $true
 Must-Fail {Start-MentatRemoteConsole -ProfilePath $profileFile -NoBrowser} 'SSH tunnel exited' 'exited tunnel is not reported healthy'
 Reset-Transport
 Start-MentatRemoteConsole -ProfilePath $profileFile -AppWindow
 $state=Snapshot;Check ($state.Windows -eq 1) 'user app launch opens exactly one window after remote readiness'
 Write-Output ('TOTAL '+$script:Passed+' assertions passed')
} finally {
 Remove-Module remote-console -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $work -Recurse -Force
}
