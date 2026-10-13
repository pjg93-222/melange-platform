"""Tests for ops/compute/check_longform_inputs.py (the read-only pre-flight for the turn-features receipt).

A synthetic receipt over files in tmp_path only: no live tree, no data, no chain. The script must print counts and nothing else.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

COMPUTE = Path(__file__).resolve().parents[1] / "compute"
sys.path.insert(0, str(COMPUTE))
import check_longform_inputs as cli  # noqa: E402

PRODUCER_REL = "fec/speech/build_member_longform_turn_features.py"
SECRET = "SECRET-CLIP-BYTES-do-not-print"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def tree(tmp_path):
    """A tree with the producer, two inputs and a receipt naming all three by absolute path."""
    files = {PRODUCER_REL: b"producer-v2", "data/speech/turns_a.jsonl": SECRET.encode(), "clips/one.wav": b"RIFF-one" * 5000}
    for rel, data in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    inputs = [{"path": str(tmp_path / rel), "sha256": sha(data)} for rel, data in sorted(files.items())]
    # the receipt was written for the OLD producer: a landing changes exactly this one
    for item in inputs:
        if item["path"].replace("\\", "/").endswith(PRODUCER_REL):
            item["sha256"] = sha(b"producer-v1")
    receipt = tmp_path / "data" / "speech" / "member_longform_turn_features_v1.receipt.json"
    receipt.write_text(json.dumps({"schema": "member-longform-turn-features-receipt-v1", "inputs": inputs}), encoding="utf-8")
    return tmp_path, receipt, inputs


def run(tmp_path, capsys, *extra):
    code = cli.main(["--root", str(tmp_path), *extra])
    return code, capsys.readouterr().out


def test_all_inputs_match_and_the_changed_producer_is_ignored(tree, capsys):
    root, _, _ = tree
    code, out = run(root, capsys)
    assert code == 0
    assert out.strip() == "longform inputs: PASS 2 checked, 2 match, 0 missing, 0 changed (producer entries skipped: 1)"


def test_output_is_counts_only_never_a_path_or_a_byte(tree, capsys):
    root, _, _ = tree
    _, out = run(root, capsys)
    assert SECRET not in out and "turns_a" not in out and "one.wav" not in out and str(root) not in out
    assert len(out.strip().splitlines()) == 1


def test_a_missing_input_fails_closed(tree, capsys):
    root, _, _ = tree
    (root / "clips" / "one.wav").unlink()
    code, out = run(root, capsys)
    assert code == 1
    assert "FAIL 2 checked, 1 match, 1 missing, 0 changed" in out and "one.wav" not in out


def test_a_changed_input_fails_closed(tree, capsys):
    root, _, _ = tree
    (root / "data" / "speech" / "turns_a.jsonl").write_bytes(b"edited")
    code, out = run(root, capsys)
    assert code == 1
    assert "FAIL 2 checked, 1 match, 0 missing, 1 changed" in out and SECRET not in out


def test_a_receipt_with_only_the_producer_proves_nothing(tree, capsys):
    root, receipt, inputs = tree
    only = [item for item in inputs if item["path"].replace("\\", "/").endswith(PRODUCER_REL)]
    receipt.write_text(json.dumps({"inputs": only}), encoding="utf-8")
    code, out = run(root, capsys)
    assert code == 1 and "0 checked" in out
    receipt.write_text(json.dumps({"inputs": []}), encoding="utf-8")
    assert run(root, capsys)[0] == 1


def test_producer_is_recognised_by_either_separator_and_any_case():
    for stored in ("C:\\Users\\x\\Melange Systems\\fec\\speech\\build_member_longform_turn_features.py",
                   "/melange/fec/speech/build_member_longform_turn_features.py",
                   "C:/X/FEC/Speech/Build_Member_Longform_Turn_Features.py"):
        assert cli.is_producer(stored), stored
    for stored in ("C:\\x\\fec\\speech\\build_member_longform_review_queue.py", "C:\\x\\speech\\build_member_longform_turn_features.py",
                   "C:\\x\\clips\\build_member_longform_turn_features.py.bak"):
        assert not cli.is_producer(stored), stored


@pytest.mark.parametrize("body", ["not json", "[]", "{}", '{"inputs": [{"path": "x"}]}', '{"inputs": 3}'])
def test_an_unreadable_or_malformed_receipt_is_exit_2_without_content(tree, capsys, body):
    root, receipt, _ = tree
    receipt.write_text(body, encoding="utf-8")
    code, out = run(root, capsys)
    assert code == 2 and out.strip() == "longform inputs: FAIL receipt absent or unreadable"


def test_an_absent_receipt_is_exit_2(tmp_path, capsys):
    assert run(tmp_path, capsys)[0] == 2


def test_it_writes_nothing(tree, capsys):
    root, receipt, _ = tree
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file()}
    run(root, capsys)
    after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file()}
    assert before == after and set(before) == set(after)


def test_command_line_exit_code_and_single_line(tree):
    root, _, _ = tree
    done = subprocess.run([sys.executable, str(COMPUTE / "check_longform_inputs.py"), "--root", str(root)],
                          capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.count("\n") == 1 and done.stderr == ""
    (root / "clips" / "one.wav").write_bytes(b"x")
    done = subprocess.run([sys.executable, str(COMPUTE / "check_longform_inputs.py"), "--root", str(root)],
                          capture_output=True, text=True)
    assert done.returncode == 1 and "1 changed" in done.stdout
