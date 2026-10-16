# Bounded syntax/PlanOnly contract: no Docker, credential loader, events or job execution.
param([string]$ProjectRoot,[string]$PythonPath)
$ErrorActionPreference='Stop'
$wrapper=Join-Path $ProjectRoot 'tools/run_job.ps1'
$tokens=$null;$parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($wrapper,[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw 'Wrapper AST parse failed.' }
$starts=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Start-Process' -and $node.Extent.Text.Contains('$brokerArguments')},$true))
if ($starts.Count -ne 1 -or -not $starts[0].Extent.Text.Contains('-WindowStyle Hidden') -or -not $starts[0].Extent.Text.Contains('-PassThru')) { throw 'Owned broker start contract failed.' }
$text=[IO.File]::ReadAllText($wrapper)
if ($text.IndexOf("'owner.stop'") -gt $text.IndexOf('$wiped = Invoke-Native')) { throw 'Broker must stop before secret wipe.' }
$plan=(& $wrapper -Job 'collect-fanout' -RunId 'fixture-host-broker-plan' -PythonPath $PythonPath -ImageLock (Join-Path $ProjectRoot 'compute.images.lock') -PlanOnly) | ConvertFrom-Json
@{ast_parse_errors=0;plan_only=$true;collect_host=$plan.environment.COLLECT_HOST;requires_keys=$plan.fec_keys;network_plan=$plan.network;broker_ready_wait_finite=$text.Contains('.AddSeconds(15)');broker_stop_wait_finite=$text.Contains('$hostPool.WaitForExit(150000)')} | ConvertTo-Json -Compress
