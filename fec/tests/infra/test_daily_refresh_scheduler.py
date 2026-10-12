"""Offline scheduler XML and launcher contract tests; never register a task."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[3]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell launcher")
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
SID = "S-1-5-21-3682052144-415171328-844708176-1001"


@pytest.fixture
def definitions(tmp_path):
    directory = tmp_path / "definitions"
    workspace = tmp_path / "workspace & daily"
    command = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
               str(ROOT / "fec/register_daily_refresh_task.ps1"),
               "-Root", str(workspace), "-OutputDirectory", str(directory), "-PythonPath", sys.executable,
               "-UserSid", SID, "-RetryStart", "2026-09-13T12:00:00", "-SkipSchedulerValidation"]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    receipt = json.loads(result.stdout)
    assert receipt["registered"] is False
    assert receipt["scheduler_validated"] is False
    return workspace, {item["Mode"]: ET.parse(item["XmlPath"]).getroot() for item in receipt["tasks"]}


def text(tree, path):
    return tree.find(path, NS).text


def test_first_use_has_only_signin_unlock_and_user_lid_events(definitions):
    workspace, tasks = definitions
    first = tasks["first-use"]
    triggers = first.find("t:Triggers", NS)
    assert [node.tag.rsplit("}", 1)[1] for node in triggers] == [
        "LogonTrigger", "SessionStateChangeTrigger", "EventTrigger"]
    assert text(first, "t:Triggers/t:LogonTrigger/t:UserId") == SID
    assert text(first, "t:Triggers/t:SessionStateChangeTrigger/t:UserId") == SID
    assert text(first, "t:Triggers/t:SessionStateChangeTrigger/t:StateChange") == "SessionUnlock"
    query = text(first, "t:Triggers/t:EventTrigger/t:Subscription")
    assert "EventID=507" in query
    assert 'Data[@Name="LidOpenState"]="true"' in query
    assert 'Data[@Name="IsCsSessionInProgressOnExit"]="false"' in query
    assert "Power-Troubleshooter" not in query
    assert first.find("t:Settings/t:RestartOnFailure", NS) is None
    assert text(first, "t:Settings/t:StartWhenAvailable") == "false"


def test_retry_is_hourly_and_network_event_with_no_new_day_trigger(definitions):
    workspace, tasks = definitions
    retry = tasks["retry"]
    # BK-DR-e: hourly for a bounded 12 h window each day, never an unbounded repetition.
    assert text(retry, "t:Triggers/t:CalendarTrigger/t:Repetition/t:Interval") == "PT1H"
    assert text(retry, "t:Triggers/t:CalendarTrigger/t:Repetition/t:Duration") == "PT12H"
    assert text(retry, "t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval") == "1"
    assert text(retry, "t:Triggers/t:CalendarTrigger/t:StartBoundary") == "2026-09-13T12:00:00"
    assert text(retry, "t:Settings/t:ExecutionTimeLimit") == "PT6H"
    assert retry.find("t:Triggers/t:LogonTrigger", NS) is None
    assert retry.find("t:Triggers/t:SessionStateChangeTrigger", NS) is None
    assert "EventID=10000" in text(retry, "t:Triggers/t:EventTrigger/t:Subscription")
    assert text(retry, "t:Settings/t:StartWhenAvailable") == "true"


def test_tasks_have_native_hidden_least_privilege_actions(definitions):
    workspace, tasks = definitions
    for mode, tree in tasks.items():
        assert text(tree, "t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
        assert text(tree, "t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
        assert text(tree, "t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
        for field in ("WakeToRun", "DisallowStartIfOnBatteries", "StopIfGoingOnBatteries", "RunOnlyIfIdle"):
            assert text(tree, f"t:Settings/t:{field}") == "false"
        assert text(tree, "t:Settings/t:ExecutionTimeLimit") == ("PT6H" if mode == "retry" else "PT0S")
        assert text(tree, "t:Settings/t:Hidden") == "true"
        assert text(tree, "t:Actions/t:Exec/t:WorkingDirectory") == str(workspace)
        assert text(tree, "t:Actions/t:Exec/t:Command") == str(Path(sys.executable).with_name("pythonw.exe"))
        arguments = text(tree, "t:Actions/t:Exec/t:Arguments")
        assert f'-u "{workspace / "fec/daily_refresh.py"}"' in arguments
        assert f"--trigger {mode}" in arguments
        assert f'--root "{workspace}"' in arguments
        assert "powershell" not in arguments.casefold()
        assert "-Command" not in arguments
        assert "codex" not in arguments.casefold()


@pytest.mark.parametrize("mode,exitcode", [("first-use", 0), ("retry", 75), ("retry", 9)])
def test_launcher_preserves_python_exitcode_arguments_and_root(tmp_path, mode, exitcode):
    workspace = tmp_path / "workspace & daily"
    (workspace / "fec").mkdir(parents=True)
    (workspace / "fec/daily_refresh.py").write_text(
        "import json,os,pathlib,sys\n"
        "pathlib.Path('observed.json').write_text(json.dumps({'cwd':os.getcwd(),'root':os.environ['MONEYBALL_ROOT'],'args':sys.argv[1:]}))\n"
        f"sys.exit({exitcode})\n", encoding="utf-8")
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                             str(ROOT / "fec/run_daily_refresh.ps1"), "-Trigger", mode, "-Root", str(workspace),
                             "-PythonPath", sys.executable], capture_output=True, text=True)
    assert result.returncode == exitcode, result.stderr
    observed = json.loads((workspace / "observed.json").read_text())
    assert observed == {"cwd": str(workspace), "root": str(workspace), "args": ["--trigger", mode]}


def test_registration_cannot_bypass_scheduler_validation(tmp_path):
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                             str(ROOT / "fec/register_daily_refresh_task.ps1"), "-Register", "-SkipSchedulerValidation"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "Registration requires Windows Task Scheduler validation" in result.stderr
