import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import classifier_runtime as runtime


def test_tool_free_structured_inference(monkeypatch):
    seen={}
    def run(command,**kw):
        seen.update(command=command,**kw)
        return SimpleNamespace(returncode=0,stdout=json.dumps({"structured_output":{"cls":"negative"},"modelUsage":{},"uuid":"receipt-id"}),stderr="")
    monkeypatch.setattr(runtime.subprocess,"run",run)
    result,proof=runtime.classify_json("Policy","Untrusted evidence",{"type":"object"})
    assert result=={"cls":"negative"}
    command=seen["command"]
    assert command[command.index("--tools")+1]==""
    assert json.loads(command[command.index("--mcp-config")+1])=={"mcpServers":{}}
    assert "--safe-mode" in command and "--no-chrome" in command
    assert seen["input"]=="Untrusted evidence" and seen["timeout"]==180
    assert proof["response_id"]=="receipt-id" and proof["tools_enabled"] is False


@pytest.mark.parametrize("response",[
    SimpleNamespace(returncode=1,stdout="",stderr="provider unavailable"),
    SimpleNamespace(returncode=0,stdout='{"is_error":true,"result":"limit"}',stderr=""),
    SimpleNamespace(returncode=0,stdout='{"result":"[]"}',stderr=""),
])
def test_provider_failure_never_returns_classification(monkeypatch,response):
    monkeypatch.setattr(runtime.subprocess,"run",lambda *a,**kw:response)
    with pytest.raises((RuntimeError,ValueError)):
        runtime.classify_json("Policy","Evidence",{"type":"object"})
