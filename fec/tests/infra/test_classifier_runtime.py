import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import classifier_runtime as runtime


class FakePopen:
    """Stands in for Popen: reads the stdin file, writes canned stdout/stderr to the real temp files."""
    seen={}
    response=SimpleNamespace(returncode=0,stdout="",stderr="")
    hang=False
    def __init__(self,command,stdin,stdout,stderr,cwd=None,**kw):
        FakePopen.seen=dict(command=command,input=stdin.read().decode("utf-8"),kw=kw)
        stdout.write(self.response.stdout.encode("utf-8")); stderr.write(self.response.stderr.encode("utf-8"))
        self.pid=1
    def wait(self,timeout=None):
        FakePopen.seen["timeout"]=timeout
        if self.hang and not FakePopen.seen.get("killed"): raise subprocess.TimeoutExpired("claude",timeout)
        return self.response.returncode


def _fake(monkeypatch,response,hang=False):
    FakePopen.response=response; FakePopen.hang=hang; FakePopen.seen={}
    monkeypatch.setattr(runtime.subprocess,"Popen",FakePopen)
    monkeypatch.setattr(runtime,"kill_tree",lambda p:FakePopen.seen.update(killed=True))


def test_tool_free_structured_inference(monkeypatch):
    _fake(monkeypatch,SimpleNamespace(returncode=0,stdout=json.dumps({"structured_output":{"cls":"negative"},"modelUsage":{},"uuid":"receipt-id"}),stderr=""))
    result,proof=runtime.classify_json("Policy","Untrusted evidence",{"type":"object"})
    assert result=={"cls":"negative"}
    command=FakePopen.seen["command"]
    assert command[command.index("--tools")+1]==""
    assert json.loads(command[command.index("--mcp-config")+1])=={"mcpServers":{}}
    assert "--safe-mode" in command and "--no-chrome" in command
    assert FakePopen.seen["input"]=="Untrusted evidence" and FakePopen.seen["timeout"]==180
    assert proof["response_id"]=="receipt-id" and proof["tools_enabled"] is False


@pytest.mark.parametrize("response",[
    SimpleNamespace(returncode=1,stdout="",stderr="provider unavailable"),
    SimpleNamespace(returncode=0,stdout='{"is_error":true,"result":"limit"}',stderr=""),
    SimpleNamespace(returncode=0,stdout='{"result":"[]"}',stderr=""),
])
def test_provider_failure_never_returns_classification(monkeypatch,response):
    _fake(monkeypatch,response)
    with pytest.raises((RuntimeError,ValueError)):
        runtime.classify_json("Policy","Evidence",{"type":"object"})


def test_timeout_kills_tree_and_raises(monkeypatch):
    _fake(monkeypatch,SimpleNamespace(returncode=0,stdout="",stderr=""),hang=True)
    with pytest.raises(RuntimeError,match="timed out"):
        runtime.classify_json("Policy","Evidence",{"type":"object"})
    assert FakePopen.seen["killed"] is True
