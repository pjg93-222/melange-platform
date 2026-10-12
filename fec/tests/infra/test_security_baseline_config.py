"""Security-baseline config guards: loopback-only preview servers, secret/db ignore rules."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_launch_json_http_servers_bind_loopback_only():
    launch = json.loads((ROOT / ".claude" / "launch.json").read_text(encoding="utf-8"))
    servers = [c for c in launch["configurations"] if "http.server" in c["runtimeArgs"]]
    assert servers, "expected at least one http.server preview configuration"
    for config in servers:
        args = config["runtimeArgs"]
        assert "--bind" in args, f"{config['name']}: http.server without --bind listens on all interfaces"
        assert args[args.index("--bind") + 1] == "127.0.0.1", config["name"]


def _ignored(path):
    # --no-index: judge the pattern itself, even for files that are already tracked.
    return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", path]).returncode == 0


@pytest.mark.parametrize("path", [
    "data/money/Moneyball_email.zip", "data/money/other.zip",
    "fec/data.sqlite", ".env", "fec/sub/.env", "creds.pw",
])
def test_secret_and_database_paths_are_ignored(path):
    assert _ignored(path), path


@pytest.mark.parametrize("path", ["fec/cn24.zip", "fec/weball24.zip", ".env.example", "fec/refresh_email.py"])
def test_ignore_rules_stay_narrow(path):
    # The FEC bulk zips are tracked deliberately: a blanket *.zip rule would be wrong.
    assert not _ignored(path), path
