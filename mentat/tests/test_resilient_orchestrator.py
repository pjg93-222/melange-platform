from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    PROJECT_ROOT
    / "tools"
    / "parallel"
    / "Start-MentatResilientIndustryReports.ps1"
)
POWERSHELL_ENTRYPOINTS = (
    SCRIPT,
    PROJECT_ROOT / "tools" / "parallel" / "Start-MentatIndustryReports.ps1",
    PROJECT_ROOT / "tools" / "generate_industry_report.ps1",
    PROJECT_ROOT / "tools" / "generate_crypto_report.ps1",
    PROJECT_ROOT / "tools" / "build_industry_report.ps1",
)
POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)


@unittest.skipUnless(POWERSHELL.is_file(), "Windows PowerShell is unavailable")
class ResilientOrchestratorTests(unittest.TestCase):
    def test_managed_wait_wakes_on_exit_without_sleeping(self) -> None:
        # A deterministic fake exits when its wait handle is signalled. Status
        # and null-exit guards still run on the next loop iteration.
        command = """
$ErrorActionPreference='Stop'
$text=Get-Content -LiteralPath '%s' -Raw
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
$definition=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Wait-ManagedProcess'},$true))
. ([scriptblock]::Create($definition[0].Extent.Text))
$managedProcessNullExitCode=-9999
$wallWatch=[Diagnostics.Stopwatch]::StartNew()
$script:statusCount=0
function Update-GeneratorJobs {}
function Write-RunStatus {param($Stage,$Industry,$Live) $script:statusCount++}
function Write-RunLog {param($Message)}
function Start-Sleep {throw 'A completed process must wake its wait handle.'}
$process=[pscustomobject]@{HasExited=$false;ExitCode=37;WaitMilliseconds=0}
$process|Add-Member ScriptMethod Refresh {}
$process|Add-Member ScriptMethod WaitForExit {param($Milliseconds)
  if ($Milliseconds) {$this.WaitMilliseconds=$Milliseconds;$this.HasExited=$true}
  return $true
}
$result=Wait-ManagedProcess $process 'test' ([pscustomobject]@{industry='Fixture'})
[pscustomobject]@{exit_code=@($result)[-1];wait_ms=$process.WaitMilliseconds;statuses=$script:statusCount}|ConvertTo-Json -Compress
""" % str(SCRIPT).replace("'", "''")
        result = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, text=True, env=os.environ.copy(), timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        values = json.loads(result.stdout)
        self.assertEqual(values, {'exit_code': 37, 'wait_ms': 2000, 'statuses': 2})

    def test_resumed_attempt_gets_its_own_managed_log_pair(self) -> None:
        # Start-ManagedPowerShell refuses to reuse a log path and a resume keeps
        # the run folder, so every stage the stopped attempt logged would throw
        # "Managed process log already exists" before it collected anything.
        with tempfile.TemporaryDirectory(prefix="mentat-resume-logs-") as temporary:
            directory = Path(temporary)
            (directory / "fanout.stdout.log").write_text("stopped attempt")
            (directory / "fanout.resume-1.stderr.log").write_text("first resume")
            command = """
$ErrorActionPreference='Stop'
$text=Get-Content -LiteralPath '%s' -Raw
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
$definition=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Resolve-ManagedLogBase'},$true))
. ([scriptblock]::Create($definition[0].Extent.Text))
$root='%s'
[pscustomobject]@{
  fresh=(Split-Path -Leaf (Resolve-ManagedLogBase $root 'initialize'))
  taken=(Split-Path -Leaf (Resolve-ManagedLogBase $root 'fanout'))
}|ConvertTo-Json -Compress
""" % (
                str(SCRIPT).replace("'", "''"),
                str(directory).replace("'", "''"),
            )
            result = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                env=os.environ.copy(),
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            values = json.loads(result.stdout)
        # A stage with no prior log keeps the plain name; a stage the stopped
        # attempt already wrote skips past every attempt already on disk, so the
        # logs being resumed from are never truncated.
        self.assertEqual(values, {"fresh": "initialize", "taken": "fanout.resume-2"})

    def test_powershell_parser_accepts_script(self) -> None:
        for script in POWERSHELL_ENTRYPOINTS:
            with self.subTest(script=script.name):
                command = (
                    "$text=Get-Content -LiteralPath "
                    f"'{str(script).replace(chr(39), chr(39) * 2)}' -Raw;"
                    "$tokens=$null;$errors=$null;"
                    "[void][Management.Automation.Language.Parser]::ParseInput("
                    "$text,[ref]$tokens,[ref]$errors);"
                    "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}"
                )
                result = subprocess.run(
                    [
                        str(POWERSHELL),
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        command,
                    ],
                    cwd=PROJECT_ROOT.parent,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stderr + result.stdout,
                )

    def test_managed_fast_process_exit_codes_are_never_null(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="mentat-managed-process-"
        ) as temporary:
            script_literal = str(SCRIPT).replace("'", "''")
            temporary_literal = temporary.replace("'", "''")
            command = (
                f"$text=Get-Content -LiteralPath '{script_literal}' -Raw;"
                "$tokens=$null;$errors=$null;"
                "$ast=[Management.Automation.Language.Parser]::ParseInput("
                "$text,[ref]$tokens,[ref]$errors);"
                "if($errors.Count){exit 1};"
                "foreach($name in @('Start-ManagedPowerShell',"
                "'Wait-ManagedProcess','Format-ManagedExitCode')){"
                "$functionAst=@($ast.FindAll({param($node)"
                "$node -is "
                "[Management.Automation.Language.FunctionDefinitionAst] "
                "-and $node.Name -eq $name},$true));"
                "if($functionAst.Count -ne 1){exit 2};"
                ". ([scriptblock]::Create($functionAst[0].Extent.Text))"
                "};"
                "$managedProcessNullExitCode=-9999;"
                "$powerShellPath=(Get-Command powershell.exe "
                "-ErrorAction Stop).Source;"
                "$wallWatch=[Diagnostics.Stopwatch]::StartNew();"
                "function Update-GeneratorJobs {};"
                "function Write-RunStatus {param($Stage,$Industry,$Live)};"
                "function Write-RunLog {param($Message)};"
                "$state=[pscustomobject]@{industry='Fixture'};"
                f"$root='{temporary_literal}';"
                "$success=Start-ManagedPowerShell -Command 'exit 0' "
                "-StdoutPath (Join-Path $root 'success.stdout.log') "
                "-StderrPath (Join-Path $root 'success.stderr.log');"
                "Start-Sleep -Milliseconds 250;"
                "$successExit=Wait-ManagedProcess $success 'test' $state;"
                "$failure=Start-ManagedPowerShell -Command 'exit 37' "
                "-StdoutPath (Join-Path $root 'failure.stdout.log') "
                "-StderrPath (Join-Path $root 'failure.stderr.log');"
                "Start-Sleep -Milliseconds 250;"
                "$failureExit=Wait-ManagedProcess $failure 'test' $state;"
                "$fake=[pscustomobject]@{HasExited=$true;ExitCode=$null};"
                "$fake|Add-Member ScriptMethod Refresh {};"
                "$fake|Add-Member ScriptMethod WaitForExit {};"
                "$nullExit=Wait-ManagedProcess $fake 'test' $state;"
                "[pscustomobject]@{"
                "success_exit=$successExit;"
                "success_type=$successExit.GetType().FullName;"
                "failure_exit=$failureExit;"
                "failure_type=$failureExit.GetType().FullName;"
                "null_exit=$nullExit;"
                "null_type=$nullExit.GetType().FullName;"
                "null_description=Format-ManagedExitCode $nullExit"
                "}|ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=PROJECT_ROOT.parent,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        values = json.loads(result.stdout)
        self.assertEqual(values["success_exit"], 0)
        self.assertEqual(values["failure_exit"], 37)
        self.assertEqual(values["null_exit"], -9999)
        self.assertEqual(values["success_type"], "System.Int32")
        self.assertEqual(values["failure_type"], "System.Int32")
        self.assertEqual(values["null_type"], "System.Int32")
        self.assertIn("ExitCode unavailable", values["null_description"])
        self.assertIn("sentinel=-9999", values["null_description"])

    def test_plan_only_counts_numbered_keys_and_comma_industries(self) -> None:
        environment = os.environ.copy()
        environment.pop("FEC_API_KEY", None)
        environment.pop("FEC_API_KEYS", None)
        for name in list(environment):
            if name.startswith("FEC_API_KEY_"):
                environment.pop(name)
        environment["FEC_API_KEY_1"] = "synthetic-key-one"
        environment["FEC_API_KEY_2"] = "synthetic-key-two"
        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-Industries",
                "Fintech,Crypto",
                "-PlanOnly",
            ],
            cwd=PROJECT_ROOT.parent,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["industries"], ["Fintech", "Crypto"])
        self.assertEqual(plan["recovery_workers"], 0)
        self.assertEqual(plan["resources"]["key_count"], 2)
        self.assertGreaterEqual(plan["resources"]["effective_api_lanes"], 1)
        self.assertLessEqual(plan["resources"]["effective_api_lanes"], 2)
        self.assertEqual(
            plan["resources"]["fanout_max_parallel"],
            plan["resources"]["effective_api_lanes"],
        )
        self.assertEqual(
            plan["resources"]["allocation_mode"],
            "live_recomputed_enforced",
        )
        self.assertEqual(plan["resources"]["fanout_memory_per_lane_mb"], 640)
        self.assertIn("workbook generators overlap", plan["strategy"])
        self.assertTrue(
            plan["raw_data_directory"].endswith(
                str(Path("outputs") / "raw-contributions")
            )
        )
        self.assertTrue(
            plan["raw_data_export"].endswith(
                str(Path("tools") / "export_raw_contribution_warehouse.py")
            )
        )

    def test_raw_data_gate_precedes_generator_release(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        export_index = text.index("$state.stage = 'raw_data_gate'")
        raw_pass_index = text.index(
            "Write-RunEvent 'raw_data_export_passed'"
        )
        generator_index = text.index("$state.stage = 'generator_queued'")
        self.assertLess(export_index, raw_pass_index)
        self.assertLess(raw_pass_index, generator_index)
        self.assertIn(
            "$rawContributionRows -eq $coverageContributionRows",
            text,
        )
        self.assertIn(
            "$rawCorporateRows -eq $coverageCorporateRows",
            text,
        )

    def test_live_fanout_status_reads_allow_atomic_replacement(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("[IO.FileShare]::ReadWrite", text)
        self.assertIn("[IO.FileShare]::Delete", text)
        start = text.index("function Get-FanoutLiveState")
        end = text.index("function Get-GeneratorView", start)
        live_reader = text[start:end]
        self.assertIn("Read-LiveTextFile", live_reader)
        self.assertNotIn("Get-Content", live_reader)
        with tempfile.TemporaryDirectory(
            prefix="mentat-live-status-reader-"
        ) as temporary:
            status = Path(temporary) / "status.json"
            status.write_text(
                '{"status":"collecting","completed":3}\n',
                encoding="utf-8",
            )
            script_literal = str(SCRIPT).replace("'", "''")
            temporary_literal = temporary.replace("'", "''")
            command = (
                f"$text=Get-Content -LiteralPath '{script_literal}' -Raw;"
                "$tokens=$null;$errors=$null;"
                "$ast=[Management.Automation.Language.Parser]::ParseInput("
                "$text,[ref]$tokens,[ref]$errors);"
                "if($errors.Count){exit 1};"
                "foreach($name in @('Read-LiveTextFile',"
                "'Get-FanoutLiveState')){"
                "$functionAst=@($ast.FindAll({param($node)"
                "$node -is "
                "[Management.Automation.Language.FunctionDefinitionAst] "
                "-and $node.Name -eq $name},$true));"
                "if($functionAst.Count -ne 1){exit 2};"
                ". ([scriptblock]::Create($functionAst[0].Extent.Text))"
                "};"
                f"$value=Get-FanoutLiveState '{temporary_literal}';"
                "if($null -eq $value){exit 3};"
                "$value|ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=PROJECT_ROOT.parent,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "collecting")
        self.assertEqual(value["completed"], 3)

    def test_locked_status_write_is_nonfatal_counted_and_load_writes_raise(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="mentat-locked-status-writer-"
        ) as temporary:
            root = Path(temporary)
            status = root / "status.json"
            status.write_text('{"stage":"original"}\n', encoding="utf-8")
            status_literal = str(status).replace("'", "''")
            root_literal = str(root).replace("'", "''")
            script_literal = str(SCRIPT).replace("'", "''")
            holder_command = (
                "$stream=[IO.File]::Open("
                f"'{status_literal}',[IO.FileMode]::Open,"
                "[IO.FileAccess]::Read,[IO.FileShare]::Read);"
                "try{[Console]::Out.WriteLine('READY');"
                "[Console]::Out.Flush();"
                "[void][Console]::In.ReadLine()}"
                "finally{$stream.Dispose()}"
            )
            holder = subprocess.Popen(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    holder_command,
                ],
                cwd=PROJECT_ROOT.parent,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "READY")
                command = (
                    "$ErrorActionPreference='Stop';"
                    "$WarningPreference='SilentlyContinue';"
                    f"$text=Get-Content -LiteralPath '{script_literal}' -Raw;"
                    "$tokens=$null;$errors=$null;"
                    "$ast=[Management.Automation.Language.Parser]::ParseInput("
                    "$text,[ref]$tokens,[ref]$errors);"
                    "if($errors.Count){exit 1};"
                    "foreach($name in @("
                    "'Write-AtomicJson','Write-RunStatus',"
                    "'Write-TelemetryAppend','Write-RunEvent')){"
                    "$functionAst=@($ast.FindAll({param($node)"
                    "$node -is "
                    "[Management.Automation.Language.FunctionDefinitionAst] "
                    "-and $node.Name -eq $name},$true));"
                    "if($functionAst.Count -ne 1){exit 2};"
                    ". ([scriptblock]::Create($functionAst[0].Extent.Text))"
                    "};"
                    "$script:statusWriteFailures=0;"
                    "$script:eventWriteFailures=0;"
                    "$script:logWriteFailures=0;"
                    "$collectionDurations=New-Object "
                    "'Collections.Generic.List[double]';"
                    "$generatorDurations=New-Object "
                    "'Collections.Generic.List[double]';"
                    "$industryStates=@();$pendingGenerators=@();"
                    "$runningGenerators=@();$recoveryQueue=@();"
                    "$activeRecovery=$null;$MaxGeneratorWorkers=1;"
                    "$runId='fixture';"
                    "$wallWatch=[Diagnostics.Stopwatch]::StartNew();"
                    "$resourcePlan=[ordered]@{};"
                    f"$statusPath='{status_literal}';"
                    "$eventsPath=$statusPath;"
                    "function Get-GeneratorView { @() };"
                    "Write-RunStatus 'collecting';"
                    "Write-RunEvent 'locked_fixture';"
                    "$loadBearingRaised=$false;"
                    "try{Write-AtomicJson $statusPath "
                    "([ordered]@{stage='load-bearing'})}"
                    "catch{$loadBearingRaised=$true};"
                    "[pscustomobject]@{"
                    "survived=$true;"
                    "status_write_failures=$statusWriteFailures;"
                    "event_write_failures=$eventWriteFailures;"
                    "load_bearing_raised=$loadBearingRaised;"
                    f"pending_count=@(Get-ChildItem -LiteralPath '{root_literal}' "
                    "-Filter '*.pending' -File -Force).Count"
                    "}|ConvertTo-Json -Compress"
                )
                result = subprocess.run(
                    [
                        str(POWERSHELL),
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        command,
                    ],
                    cwd=PROJECT_ROOT.parent,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
            finally:
                if holder.stdin:
                    holder.stdin.write("\n")
                    holder.stdin.flush()
                try:
                    holder.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    holder.kill()
                    holder.communicate()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        values = json.loads(result.stdout)
        self.assertTrue(values["survived"])
        self.assertEqual(values["status_write_failures"], 1)
        self.assertEqual(values["event_write_failures"], 1)
        self.assertTrue(values["load_bearing_raised"])
        self.assertEqual(values["pending_count"], 0)
        text = SCRIPT.read_text(encoding="utf-8")
        summary_start = text.index("$summary = [ordered]@{")
        summary_end = text.index(
            "Write-AtomicJson $summaryPath",
            summary_start,
        )
        self.assertIn(
            "status_write_failures = $statusWriteFailures",
            text[summary_start:summary_end],
        )

    def test_phase_timing_receipt_and_summary_line_are_always_wired(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("'phase-timings.json'", text)
        for field in (
            "collection_seconds",
            "merge_seconds",
            "warehouse_seconds",
            "export_seconds",
            "workbook_build_seconds",
            "formula_seconds",
            "finalize_seconds",
        ):
            self.assertIn(field, text)
        self.assertIn(
            "'industry={0} total={1:n3}s collection={2:n3}s merge={3:n3}s '",
            text,
        )

    def test_phase_timing_summary_formats_the_complete_expression(self) -> None:
        script_literal = str(SCRIPT).replace("'", "''")
        command = (
            f"$text=Get-Content -LiteralPath '{script_literal}' -Raw;"
            "$tokens=$null;$errors=$null;"
            "$ast=[Management.Automation.Language.Parser]::ParseInput("
            "$text,[ref]$tokens,[ref]$errors);"
            "$nodes=@($ast.FindAll({param($node)"
            "$node -is "
            "[Management.Automation.Language.BinaryExpressionAst] -and "
            "$node.Operator -eq "
            "[Management.Automation.Language.TokenKind]::Format -and "
            "$node.Extent.Text.Contains('industry={0}')},$true));"
            "if($errors.Count -or $nodes.Count -ne 1){exit 1};"
            "$timing=[pscustomobject]@{"
            "slug='fintech';total_seconds=10;collection_seconds=1;"
            "merge_seconds=2;warehouse_seconds=3;export_seconds=4;"
            "workbook_build_seconds=5;formula_seconds=6;"
            "finalize_seconds=7;status='ok'};"
            "$line=& ([scriptblock]::Create($nodes[0].Extent.Text));"
            "[Console]::Out.Write($line)"
        )
        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=PROJECT_ROOT.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("{", result.stdout)
        self.assertIn("industry=fintech", result.stdout)
        self.assertIn("collection=1.000s", result.stdout)
        self.assertIn("finalize=7.000s status=ok", result.stdout)

    def test_python_runtime_override_is_recorded_in_plan(self) -> None:
        environment = os.environ.copy()
        environment["FEC_API_KEY"] = "synthetic-key"
        environment["MENTAT_PYTHON_PATH"] = sys.executable
        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-Industries",
                "Fintech",
                "-PlanOnly",
            ],
            cwd=PROJECT_ROOT.parent,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        plan = json.loads(result.stdout)
        self.assertEqual(
            Path(plan["runtimes"]["python_path"]).resolve(),
            Path(sys.executable).resolve(),
        )

    def test_plan_only_accepts_roster_discovered_industry_count(self) -> None:
        environment = os.environ.copy()
        environment["FEC_API_KEY"] = "synthetic-key"
        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-Industries",
                "Crypto,Credit Union,Community Banks,"
                "Property & Casualty Insurance,Credit Arms,Telecom,Fintech",
                "-PlanOnly",
            ],
            cwd=PROJECT_ROOT.parent,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        plan = json.loads(result.stdout)
        self.assertEqual(len(plan["industries"]), 7)
        self.assertEqual(plan["industries"], plan["discovered_industries"])


if __name__ == "__main__":
    unittest.main()
