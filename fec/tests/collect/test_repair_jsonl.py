"""repair_jsonl drops invalid records only when the count matches, keeps what it keeps verbatim, and never races."""
import os
import sys
import types
from pathlib import Path

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from collect import repair_jsonl  # noqa: E402


def repair(monkeypatch, path, expected):
    monkeypatch.setattr(sys, "argv", ["repair_jsonl.py", str(path), "--expected-invalid", str(expected)])
    return repair_jsonl.main()


def test_a_clean_file_is_not_rewritten(tmp_path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    content = b'{"a": 1}\r\n\r\n   \n{"b": 2}'              # blank lines and a missing final newline are left alone
    path.write_bytes(content)
    os.utime(path, ns=(10**18, 10**18))
    assert repair(monkeypatch, path, 0) == 0
    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == 10**18
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rows.jsonl"]


def test_a_bad_line_is_removed_keeping_crlf_and_saved_to_rejected(tmp_path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a": 1}\r\n{"a": \r\n\r\n{"b": 2}\r\n')
    assert repair(monkeypatch, path, 1) == 0
    assert path.read_bytes() == b'{"a": 1}\r\n\r\n{"b": 2}\r\n'
    assert (tmp_path / "rows.jsonl.rejected").read_bytes() == b'{"a": \r\n'
    assert not (tmp_path / "rows.jsonl.repair.tmp").exists()


def test_a_record_that_is_not_an_object_is_invalid(tmp_path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a": 1}\n[1, 2]\n42\n')
    assert repair(monkeypatch, path, 2) == 0
    assert path.read_bytes() == b'{"a": 1}\n'
    assert (tmp_path / "rows.jsonl.rejected").read_bytes() == b'[1, 2]\n42\n'


def test_the_wrong_expected_count_refuses_and_leaves_the_file_alone(tmp_path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    content = b'{"a": 1}\nnot json\n'
    path.write_bytes(content)
    with pytest.raises(RuntimeError, match="expected 2 invalid records, found 1"):
        repair(monkeypatch, path, 2)
    assert path.read_bytes() == content
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rows.jsonl"]


def test_a_file_that_changes_during_the_repair_is_not_replaced(tmp_path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a": 1}\nnot json\n')
    real_loads = repair_jsonl.json.loads
    appended = []

    def loads_then_a_writer_appends(raw):
        if not appended:                                   # a collector appends while the repair is mid-read
            appended.append(True)
            with path.open("ab") as handle:
                handle.write(b'{"late": 1}\n')
        return real_loads(raw)

    monkeypatch.setattr(repair_jsonl, "json", types.SimpleNamespace(loads=loads_then_a_writer_appends))
    with pytest.raises(RuntimeError, match="changed while it was being repaired"):
        repair(monkeypatch, path, 1)
    assert path.read_bytes() == b'{"a": 1}\nnot json\n{"late": 1}\n'
    assert not (tmp_path / "rows.jsonl.repair.tmp").exists()
