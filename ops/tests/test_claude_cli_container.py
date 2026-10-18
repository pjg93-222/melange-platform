"""The claude-CLI callers run in the compute container (D-179): binary path from the environment, no APPDATA, the login
directory (CLAUDE_CONFIG_DIR) inherited by the child, Windows behaviour unchanged. A fake `claude` stands in for the CLI:
a real child process on both platforms (a shebang script on POSIX; on Windows a .cmd that does not forward its arguments,
because cmd.exe re-parses the JSON ones, so the flag set is asserted with a stubbed subprocess.run / Popen instead).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "fec"))
if not (ROOT / "fec" / "classifier_runtime.py").is_file():
    pytest.skip("fec/ is not in this tree", allow_module_level=True)
import classifier_runtime as CR  # noqa: E402
from legislation import run_legislation_claude_ambiguity_review as REVIEW  # noqa: E402

_spec = importlib.util.spec_from_file_location("http_replay", ROOT / "ops" / "compute" / "http_replay.py")
HR = importlib.util.module_from_spec(_spec)
sys.modules["http_replay"] = HR
_spec.loader.exec_module(HR)

FAKE = '''
import json, os, sys, time
log = {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd(), "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
       "appdata": os.environ.get("APPDATA")}
with open(os.environ["FAKE_CLAUDE_LOG"], "w", encoding="utf-8") as fh:
    json.dump(log, fh)
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
print(json.dumps({"structured_output": {"cls": "negative"}, "uuid": "u-1", "modelUsage": {}, "session_id": "s-1",
                  "usage": {"input_tokens": 3, "output_tokens": 2}, "total_cost_usd": 0.001}))
'''
LOCKDOWN = ["--safe-mode", "--disable-slash-commands", "--tools", "--strict-mcp-config", "--mcp-config", "--no-session-persistence",
            "--no-chrome", "--permission-mode", "dontAsk"]


@pytest.fixture()
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE, encoding="utf-8")
    if os.name == "nt":
        launcher = tmp_path / "claude.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}"\r\n', encoding="ascii")
    else:
        launcher = tmp_path / "claude"
        launcher.write_text(f"#!{sys.executable}\n{FAKE}", encoding="utf-8")
        launcher.chmod(0o755)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("MELANGE_CLASSIFIER_COMMAND", str(launcher))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "login"))
    monkeypatch.delenv("APPDATA", raising=False)          # the container has no APPDATA: this used to be a KeyError
    return SimpleNamespace(launcher=launcher, log=lambda: json.loads(log.read_text(encoding="utf-8")))


def test_classify_json_runs_the_env_binary_with_the_login_dir_and_no_appdata(fake_claude, tmp_path):
    value, proof = CR.classify_json("Policy", "Untrusted evidence", {"type": "object"})
    seen = fake_claude.log()
    assert value == {"cls": "negative"} and proof["response_id"] == "u-1" and proof["tools_enabled"] is False
    assert seen["stdin"] == "Untrusted evidence"                              # evidence goes over stdin, never argv
    assert seen["config_dir"] == str(tmp_path / "login")                      # the child inherits the CLI's own login-dir variable
    assert Path(seen["cwd"]).resolve() == Path(tempfile.gettempdir()).resolve()       # outside the repo: no CLAUDE.md, settings or files picked up
    assert seen["appdata"] is None


def test_the_ambiguity_review_transport_defaults_to_the_same_env_binary(fake_claude):
    args = REVIEW.parse_args(["--input", "i.ndjson", "--out", "o", "--run-id", "r1", "--provider", "claude-cli", "--max-cost-usd", "1"])
    assert args.claude_command == str(fake_claude.launcher)
    result = REVIEW.build_transport(args).review(system_prompt="SYSTEM", user_prompt="EVIDENCE", schema={"type": "object"}, model="sonnet",
                                                 effort="low", max_output_tokens=100, remaining_budget_usd=Decimal("0.5"))
    seen = fake_claude.log()
    assert result.output == {"cls": "negative"} and result.cost_usd == Decimal("0.001") and (result.input_tokens, result.output_tokens) == (3, 2)
    assert seen["stdin"] == "EVIDENCE" and seen["config_dir"].endswith("login")


def test_command_resolution_order_and_windows_behaviour_unchanged(monkeypatch):
    monkeypatch.delenv("MELANGE_CLASSIFIER_COMMAND", raising=False)
    monkeypatch.setenv("APPDATA", str(Path("appdata-root")))
    assert Path(CR.claude_command()) == Path("appdata-root") / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"   # as before
    monkeypatch.setenv("MELANGE_CLASSIFIER_COMMAND", "/opt/claude/bin/claude")
    assert CR.claude_command() == "/opt/claude/bin/claude"                      # an explicit binary beats APPDATA
    monkeypatch.delenv("MELANGE_CLASSIFIER_COMMAND")
    monkeypatch.delenv("APPDATA")
    assert CR.claude_command() == "claude"                                        # container: PATH
    args = REVIEW.parse_args(["--input", "i", "--out", "o", "--run-id", "r", "--max-cost-usd", "1"])
    assert args.claude_command == "claude"                                        # the review's default is unchanged without the env var


@pytest.mark.parametrize("module", ["classifier_runtime", "refresh_sentinel_admissions"])
def test_lockdown_flags_and_stdin_prompt_survive_with_the_container_command(monkeypatch, module):
    """Whatever the binary is called, every caller still sends the same tool-free lockdown and keeps evidence off argv."""
    if module == "classifier_runtime":
        target, call = CR, lambda: CR.classify_json("Policy", "Evidence", {"type": "object"})
    else:
        sentinel = pytest.importorskip("legislation.refresh_sentinel_admissions")
        target = sentinel
        call = lambda: sentinel.classify("119:hr:1", {"https://www.govinfo.gov/x": "Complete official text."}, ["Payments"],
                                         model="m", key_path=None, transport="claude-cli")
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return SimpleNamespace(returncode=1, stdout="stop here", stderr="")

    def popen(command, **kwargs):          # classifier_runtime (D-211): a bounded Popen whose stdin is a temp file holding the prompt
        seen.update(command=command, kwargs={**kwargs, "input": kwargs["stdin"].read()})
        return SimpleNamespace(wait=lambda timeout=None: 1)

    monkeypatch.setenv("MELANGE_CLASSIFIER_COMMAND", "/usr/local/bin/claude")
    monkeypatch.delenv("APPDATA", raising=False)
    if module == "classifier_runtime":
        monkeypatch.setattr(target.subprocess, "Popen", popen)
    else:
        monkeypatch.setattr(target.subprocess, "run", run)
    with pytest.raises((RuntimeError, ValueError)):
        call()
    command = seen["command"]
    assert command[0] == "/usr/local/bin/claude" and all(flag in command for flag in LOCKDOWN)
    assert command[command.index("--tools") + 1] == "" and command[command.index("--permission-mode") + 1] == "dontAsk"
    assert "Evidence" not in command and "Complete official text." not in " ".join(command)
    env = seen["kwargs"].get("env", os.environ)                                 # inherits the parent env (CLAUDE_CONFIG_DIR rides along)
    assert all(env.get(k) == v for k, v in os.environ.items()) and seen["kwargs"]["input"]


def test_the_cli_step_replays_from_a_cassette_with_the_binary_gone(fake_claude, tmp_path):
    with HR.Harness("record", tmp_path / "cas"):
        first = CR.classify_json("Policy", "Evidence one", {"type": "object"})
    os.remove(fake_claude.launcher)                                          # no binary, no login: replay must not need either
    with HR.Harness("replay", tmp_path / "cas") as replay:
        assert CR.classify_json("Policy", "Evidence one", {"type": "object"}) == first
        with pytest.raises(OSError):
            CR.classify_json("Policy", "Evidence two", {"type": "object"})   # never recorded: the real (absent) binary is not a fallback
    assert len(replay.report()["misses"]) == 1


def test_a_classifier_timeout_replays_as_a_timeout_not_as_a_failure(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setattr(CR, "TIMEOUT_SECONDS", 1)
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "60")                           # overruns the bound: classify_json kills it
    with HR.Harness("record", tmp_path / "cas"):
        with pytest.raises(RuntimeError, match="timed out"):
            CR.classify_json("Policy", "Evidence", {"type": "object"})
    os.remove(fake_claude.launcher)
    with HR.Harness("replay", tmp_path / "cas") as replay:
        with pytest.raises(RuntimeError, match="timed out"):                 # was 'Classifier failed': the kill code came back at once
            CR.classify_json("Policy", "Evidence", {"type": "object"})
    assert replay.report()["misses"] == [] == replay.report()["unused"]
