"""Tests for ops/compute/restamp_stance_fingerprint.py: the one-shot that moves the cached stance model to the root-relative fingerprint.

A temp ROOT with a tiny dummy bundle; the live cache is never read or written.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ops" / "compute"))
sys.path.insert(0, str(REPO / "fec"))
pytest.importorskip("sklearn")
import restamp_stance_fingerprint as rs  # noqa: E402
from stance import own_source_stance_model as M  # noqa: E402


@pytest.fixture
def cache(tmp_path, monkeypatch):
    for name in ("data/stance/cu_golden_labels.csv", "data/stance/tweet_gold_queue.csv"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"a,b\n1,2\n")
    monkeypatch.setattr(M, "ROOT", str(tmp_path))
    monkeypatch.setattr(M, "CACHE", str(tmp_path / "data" / "stance" / "own_source_stance_model.joblib"))
    return Path(M.CACHE)


def save(path: Path, fingerprint: str) -> dict:
    bundle = {"schema": M.SCHEMA, "fingerprint": fingerprint, "classes": ["a", "b"], "local": [1, 2, 3], "report": {"n": 5}}
    M.joblib.dump(bundle, str(path))
    M.write_sha256_sidecar(str(path))
    return bundle


def test_the_three_fingerprints_differ_only_by_the_path_scheme(cache):
    assert rs.legacy_fingerprint(M) != M.input_fingerprint()      # the absolute path left the hash


def test_restamp_rewrites_only_the_fingerprint_and_the_sidecar(cache, capsys):
    before = save(cache, rs.legacy_fingerprint(M))
    assert rs.restamp(M, dry_run=True) == 0
    assert M.joblib.load(str(cache))["fingerprint"] == rs.legacy_fingerprint(M)           # a dry run writes nothing
    assert rs.restamp(M) == 0
    after = M.joblib.load(str(cache))
    assert after["fingerprint"] == M.input_fingerprint()
    assert {k: v for k, v in after.items() if k != "fingerprint"} == {k: v for k, v in before.items() if k != "fingerprint"}
    assert M.safe_to_unpickle(str(cache))                                                  # the sidecar matches the new bytes
    assert not os.path.exists(str(cache) + ".tmp")
    assert rs.restamp(M) == 0 and "already re-stamped" in capsys.readouterr().out          # idempotent


def test_restamp_refuses_when_the_legacy_fingerprint_does_not_match(cache, capsys):
    save(cache, "0" * 64)                                                                  # trained on other inputs
    raw = cache.read_bytes()
    assert rs.restamp(M) == 1
    assert cache.read_bytes() == raw and "inputs changed" in capsys.readouterr().err


def test_restamp_refuses_a_cache_that_fails_its_sidecar(cache):
    save(cache, rs.legacy_fingerprint(M))
    cache.with_name(cache.name + ".sha256").write_text("0" * 64 + "\n", encoding="ascii")
    assert rs.restamp(M) == 2


def test_restamp_with_no_cache_is_an_error(cache):
    assert rs.restamp(M) == 2


def test_accept_stale_restamps_a_model_trained_on_other_inputs_and_changes_only_the_fingerprint(cache, capsys):
    before = save(cache, "0" * 64)                                                         # trained on other inputs
    assert rs.restamp(M, dry_run=True, accept_stale=True) == 0
    assert M.joblib.load(str(cache))["fingerprint"] == "0" * 64                            # a dry run writes nothing
    assert rs.restamp(M, accept_stale=True) == 0
    assert "OLDER inputs" in capsys.readouterr().out                                       # the loud note
    after = M.joblib.load(str(cache))
    assert after["fingerprint"] == M.input_fingerprint()
    assert {k: v for k, v in after.items() if k != "fingerprint"} == {k: v for k, v in before.items() if k != "fingerprint"}
    assert M.safe_to_unpickle(str(cache))


def test_restamp_refuses_while_the_pipeline_lock_exists(cache, tmp_path, capsys):
    save(cache, rs.legacy_fingerprint(M))
    (tmp_path / ".pipeline_run.lock").write_text("held", encoding="ascii")
    raw = cache.read_bytes()
    assert rs.restamp(M, accept_stale=True) == 3
    assert cache.read_bytes() == raw and ".pipeline_run.lock" in capsys.readouterr().err
