Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-MentatRemote([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw ('MENTAT remote connection: ' + $Message + ' No local backend was started.') }
}

function ConvertTo-MentatRemoteProfile($Value) {
    $names=@($Value.PSObject.Properties.Name)
    foreach ($name in @('SchemaVersion','Host','SshPort','User','KeyPath','LocalPort','RemotePort','RemoteStartScript','ExpectedProject')) {
        Assert-MentatRemote ($names -contains $name) ('Missing profile field ' + $name + '.')
    }
    Assert-MentatRemote ($Value.SchemaVersion -eq 1) 'Unsupported profile schema.'
    Assert-MentatRemote ([string]$Value.Host -cmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$') 'Invalid SSH host.'
    Assert-MentatRemote ([string]$Value.User -cmatch '^[a-z_][a-z0-9_-]*$') 'Invalid SSH user.'
    foreach ($name in @('SshPort','LocalPort','RemotePort')) {
        $number=0
        Assert-MentatRemote ([int]::TryParse([string]$Value.$name,[ref]$number) -and $number -ge 1024 -and $number -le 65535) ('Invalid ' + $name + '.')
        $Value.$name=$number
    }
    Assert-MentatRemote ($Value.LocalPort -eq $Value.RemotePort) 'Local and remote ports must match the console Host/Origin guards.'
    Assert-MentatRemote ([string]$Value.KeyPath -cmatch '^[A-Za-z]:[\\/][^"\r\n]+$') 'SSH key must name an absolute Windows file.'
    foreach ($name in @('RemoteStartScript','ExpectedProject')) {
        $path=[string]$Value.$name
        Assert-MentatRemote ($path -cmatch '^/[A-Za-z0-9_./-]+$' -and $path -notmatch '(^|/)\.\.?(/|$)') ('Invalid ' + $name + '.')
    }
    return $Value
}

function Get-MentatRemoteIdentity($Profile) {
    $text=@($Profile.Host,$Profile.SshPort,$Profile.User,$Profile.KeyPath,$Profile.LocalPort,$Profile.RemotePort,$Profile.RemoteStartScript,$Profile.ExpectedProject) -join "`n"
    $hash=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
}

function Get-MentatSshArguments($Profile) {
    return @('-p',[string]$Profile.SshPort,'-i',[string]$Profile.KeyPath,
        '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
        '-o','ConnectTimeout=12','-o','ConnectionAttempts=1','-o','ServerAliveInterval=30',
        '-o','ServerAliveCountMax=3','-o','LogLevel=ERROR')
}

function Join-MentatProcessArguments([string[]]$Arguments) {
    # All arguments are validated paths, generated switches, or generated shell text.
    # Double embedded quotes and trailing slashes according to Windows argv quoting.
    return (($Arguments | ForEach-Object {
        '"' + [regex]::Replace([regex]::Replace($_,'(\\*)"','$1$1\"'),'(\\+)$','$1$1') + '"'
    }) -join ' ')
}

function Get-MentatRemoteJson([string]$Url) {
    $request=[Net.WebRequest]::Create($Url)
    $request.Proxy=$null; $request.Timeout=3000; $request.ReadWriteTimeout=3000
    $response=$request.GetResponse()
    try {
        $reader=[IO.StreamReader]::new($response.GetResponseStream())
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}

function Test-MentatRemoteState($Profile, $Health, $Catalog) {
    foreach ($name in @('application','project','read_only','code_sha256','on_disk_code_sha256_now','assets_sha256','on_disk_assets_sha256_now')) {
        Assert-MentatRemote (@($Health.PSObject.Properties.Name) -contains $name) ('Remote health omitted ' + $name + '.')
    }
    Assert-MentatRemote ($Health.application -ceq 'MENTAT' -and $Health.project -ceq $Profile.ExpectedProject) 'The tunnel reaches a different application/project.'
    Assert-MentatRemote ($Health.read_only -eq $true) 'The pod console must run read-only.'
    foreach ($name in @('code_sha256','on_disk_code_sha256_now','assets_sha256','on_disk_assets_sha256_now')) {
        Assert-MentatRemote ([string]$Health.$name -cmatch '^[0-9a-f]{64}$') ('Invalid remote build hash ' + $name + '.')
    }
    Assert-MentatRemote ($Health.code_sha256 -ceq $Health.on_disk_code_sha256_now -and $Health.assets_sha256 -ceq $Health.on_disk_assets_sha256_now) 'The pod process has stale code or interface assets.'
    $catalogNames=@($Catalog.PSObject.Properties.Name)
    foreach ($name in @('model_version','model_sha256')) {
        Assert-MentatRemote ($catalogNames -contains $name) ('Forecast catalog omitted ' + $name + '.')
    }
    Assert-MentatRemote ([string]$Catalog.model_version -match '^[^\r\n]{1,160}$' -and [string]$Catalog.model_sha256 -cmatch '^[0-9a-f]{64}$') 'A valid published forecast identity is unavailable.'
    # The production v1 rollback catalog predates the schema_version field.
    $schema='1'
    if ($catalogNames -contains 'schema_version') { $schema=[string]$Catalog.schema_version }
    Assert-MentatRemote ($schema -cmatch '^[12]$') 'Unsupported forecast catalog schema.'
    if ($schema -eq '1') {
        foreach ($name in @('built_on','currency','industries','share_models','pac_model','lobby_model','evidence','industry_slopes','validation_metrics','sources')) {
            Assert-MentatRemote ($catalogNames -contains $name) ('Legacy forecast catalog omitted ' + $name + '.')
            Assert-MentatRemote ($null -ne $Catalog.$name) ('Legacy forecast catalog has no ' + $name + '.')
        }
    }
}

function Get-MentatTunnelListeners([int]$Port) {
    return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object LocalAddress,OwningProcess)
}

function Get-MentatOwnedTunnel([string]$ReceiptPath, [string]$Identity, [string]$SshPath) {
    if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) { return $null }
    try {
        $receipt=Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json
        if ($receipt.identity -cne $Identity) { return $null }
        $process=Get-Process -Id ([int]$receipt.pid) -ErrorAction Stop
        if ($process.ProcessName -ine 'ssh' -or $process.StartTime.ToUniversalTime().ToString('o') -cne $receipt.started_utc -or
            $process.Path -ine $SshPath) { return $null }
        return $process
    } catch { return $null }
}

function Invoke-MentatPodStart([string]$SshPath, $Profile) {
    $arguments=@(Get-MentatSshArguments $Profile) + @(($Profile.User+'@'+$Profile.Host),('bash '+$Profile.RemoteStartScript))
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=$SshPath; $start.Arguments=Join-MentatProcessArguments $arguments
    $start.UseShellExecute=$false; $start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
    $process=[Diagnostics.Process]::new(); $process.StartInfo=$start
    try {
        Assert-MentatRemote ($process.Start()) 'Could not start the SSH control connection.'
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(45000)) {
            $process.Kill()
            throw 'MENTAT remote connection: pod startup timed out. No local backend was started.'
        }
        $errorText=$stderr.GetAwaiter().GetResult()
        Assert-MentatRemote ($process.ExitCode -eq 0) ('Pod startup failed: '+$errorText.Substring(0,[Math]::Min(2000,$errorText.Length)))
        [void]$stdout.GetAwaiter().GetResult()
    } finally { $process.Dispose() }
}

function Start-MentatSshTunnel([string]$SshPath, $Profile, [string]$LogPrefix) {
    $forward='127.0.0.1:'+$Profile.LocalPort+':127.0.0.1:'+$Profile.RemotePort
    $arguments=@(Get-MentatSshArguments $Profile) + @('-o','ExitOnForwardFailure=yes','-N','-L',$forward,($Profile.User+'@'+$Profile.Host))
    return Start-Process -FilePath $SshPath -ArgumentList (Join-MentatProcessArguments $arguments) -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput ($LogPrefix+'.stdout.log') -RedirectStandardError ($LogPrefix+'.stderr.log')
}

function Open-MentatRemoteWindow([string]$Url, [switch]$AppWindow) {
    if (-not $AppWindow) { Start-Process $Url; return }
    $candidates=@("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
    $browser=$candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    Assert-MentatRemote (-not [string]::IsNullOrEmpty($browser)) 'Install Edge or Chrome to display the Mentat window.'
    $profilePath=Join-Path $env:LOCALAPPDATA 'Mentat\browser'
    Start-Process -FilePath $browser -ArgumentList @('--app='+$Url,('--user-data-dir="'+$profilePath+'"'),
        '--no-first-run','--no-default-browser-check','--disable-extensions','--disable-sync','--disable-background-networking','--disable-component-update')
}

function Start-MentatRemoteConsole {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$ProfilePath, [switch]$NoBrowser, [switch]$AppWindow)
    $profile=ConvertTo-MentatRemoteProfile (Get-Content -LiteralPath $ProfilePath -Raw | ConvertFrom-Json)
    Assert-MentatRemote (Test-Path -LiteralPath $profile.KeyPath -PathType Leaf) 'The configured SSH key file is unavailable.'
    $sshPath=Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
    Assert-MentatRemote (Test-Path -LiteralPath $sshPath -PathType Leaf) 'Windows OpenSSH Client is unavailable.'
    $identity=Get-MentatRemoteIdentity $profile
    $state=Split-Path -Parent ([IO.Path]::GetFullPath($ProfilePath))
    $receiptPath=Join-Path $state ('remote-console-'+$identity+'.json')
    $logPrefix=Join-Path $state ('remote-console-'+$identity)
    $url='http://127.0.0.1:'+$profile.LocalPort
    $mutex=[Threading.Mutex]::new($false,('Local\MENTAT-Remote-'+$identity))
    $held=$false; $created=$null; $tunnel=$null
    try {
        try { $held=$mutex.WaitOne(55000) } catch [Threading.AbandonedMutexException] { $held=$true }
        Assert-MentatRemote $held 'Another remote Mentat launch is still in progress.'
        $tunnel=Get-MentatOwnedTunnel $receiptPath $identity $sshPath
        $listeners=@(Get-MentatTunnelListeners $profile.LocalPort)
        if ($listeners.Count -gt 0) {
            Assert-MentatRemote ($null -ne $tunnel -and @($listeners | Where-Object { $_.OwningProcess -ne $tunnel.Id -or $_.LocalAddress -ne '127.0.0.1' }).Count -eq 0) ('Port '+$profile.LocalPort+' belongs to another process. Close or reconfigure that port owner; it was left untouched.')
        } elseif ($null -ne $tunnel) {
            throw 'MENTAT remote connection: the recorded SSH process has no listener. It was left untouched; wait for its keepalive timeout before retrying. No local backend was started.'
        }
        Invoke-MentatPodStart $sshPath $profile
        if ($null -eq $tunnel) {
            $created=Start-MentatSshTunnel $sshPath $profile $logPrefix
            $tunnel=$created
        }
        $watch=[Diagnostics.Stopwatch]::StartNew(); $ready=$false; $lastError='Tunnel not ready.'
        while ($watch.Elapsed.TotalSeconds -lt 30) {
            $tunnel.Refresh()
            Assert-MentatRemote (-not $tunnel.HasExited) ('SSH tunnel exited. Check '+$logPrefix+'.stderr.log.')
            try {
                $listeners=@(Get-MentatTunnelListeners $profile.LocalPort)
                Assert-MentatRemote ($listeners.Count -gt 0 -and @($listeners | Where-Object { $_.OwningProcess -ne $tunnel.Id -or $_.LocalAddress -ne '127.0.0.1' }).Count -eq 0) 'The loopback listener is not owned by this SSH tunnel.'
                $health=Get-MentatRemoteJson ($url+'/api/health')
                $catalog=Get-MentatRemoteJson ($url+'/api/forecast/catalog')
                Test-MentatRemoteState $profile $health $catalog
                $ready=$true; break
            } catch { $lastError=$_.Exception.Message; Start-Sleep -Milliseconds 300 }
        }
        Assert-MentatRemote $ready ('The pod console did not become healthy: '+$lastError)
        $receipt=[ordered]@{schema_version=1;identity=$identity;pid=$tunnel.Id;started_utc=$tunnel.StartTime.ToUniversalTime().ToString('o');url=$url;
            host=$profile.Host;ssh_port=$profile.SshPort;project=$health.project;read_only=$health.read_only;
            model_version=$catalog.model_version;model_sha256=$catalog.model_sha256;code_sha256=$health.code_sha256;assets_sha256=$health.assets_sha256;checked_utc=[DateTime]::UtcNow.ToString('o')}
        $temporary=$receiptPath+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
        [IO.File]::WriteAllText($temporary,($receipt | ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $receiptPath -Force
        if (-not $NoBrowser) { Open-MentatRemoteWindow $url -AppWindow:$AppWindow }
        Write-Host ('Mentat app: '+$url+' (RunPod; '+$catalog.model_version+')')
    } catch {
        if ($null -ne $created) { try { if (-not $created.HasExited) { $created.Kill() } } catch {} }
        throw
    } finally {
        if ($held) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
Export-ModuleMember -Function Start-MentatRemoteConsole
