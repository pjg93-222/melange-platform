"""A pickled model is checked against its .sha256 sidecar BEFORE it is unpickled (unpickling runs code).

Match loads, mismatch is refused without unpickling, absent sidecar warns and loads as before.
A Tripwire object stands in for a malicious pickle: its __reduce__ records that it ran.
"""
from __future__ import annotations

import ast
import json
import os
import pickle
import re
import sqlite3
import sys
from pathlib import Path

import joblib
import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import lane_file_io as L
import stance.own_source_stance_model as OSS
from collect import tweet_collector as T

TRIPPED = []


def _trip():
    TRIPPED.append(True)
    return {"tripped": True}


class Tripwire:
    def __reduce__(self):
        return (_trip, ())


@pytest.fixture(autouse=True)
def reset_tripwire():
    TRIPPED.clear()
    yield
    TRIPPED.clear()


def test_tripwire_runs_when_nothing_checks_it(tmp_path):
    """Control: without the guard the payload does execute, so the refusals below mean something."""
    path = tmp_path / "m.joblib"
    joblib.dump(Tripwire(), path)
    joblib.load(path)
    assert TRIPPED == [True]


# -- the shared helper ---------------------------------------------------------------------------

def test_matching_sidecar_is_accepted_and_nothing_is_unpickled(tmp_path, capsys):
    path = tmp_path / "m.pkl"
    path.write_bytes(pickle.dumps(Tripwire()))
    digest = L.write_sha256_sidecar(path)
    assert (tmp_path / "m.pkl.sha256").read_text().strip() == digest == L.sha256_file(path)
    assert L.safe_to_unpickle(path) is True
    assert TRIPPED == [] and capsys.readouterr().err == ""


def test_tampered_file_is_refused_loudly_without_unpickling(tmp_path, capsys):
    path = tmp_path / "m.pkl"
    path.write_bytes(pickle.dumps({"good": 1}))
    L.write_sha256_sidecar(path)
    path.write_bytes(pickle.dumps(Tripwire()))
    assert L.safe_to_unpickle(path) is False
    assert "SECURITY" in capsys.readouterr().err and TRIPPED == []


@pytest.mark.parametrize("content", ["", "not-a-digest\n", "0" * 64 + "\n"])
def test_empty_or_wrong_sidecar_is_refused(tmp_path, content):
    path = tmp_path / "m.pkl"
    path.write_bytes(pickle.dumps({"good": 1}))
    (tmp_path / "m.pkl.sha256").write_text(content)
    assert L.safe_to_unpickle(path) is False


def test_sha256sum_format_and_uppercase_digest_are_tolerated(tmp_path):
    path = tmp_path / "m.pkl"
    path.write_bytes(b"abc")
    (tmp_path / "m.pkl.sha256").write_text(L.sha256_file(path).upper() + " *m.pkl\n")
    assert L.safe_to_unpickle(path) is True


def test_absent_sidecar_warns_and_allows_load(tmp_path, capsys):
    path = tmp_path / "m.pkl"
    path.write_bytes(pickle.dumps({"good": 1}))
    assert L.safe_to_unpickle(path) is True
    assert "WARNING" in capsys.readouterr().err


# -- own_source_stance_model.load_or_train -------------------------------------------------------

@pytest.fixture
def oss_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(OSS, "CACHE", str(tmp_path / "own_source.joblib"))
    monkeypatch.setattr(OSS, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(OSS, "input_fingerprint", lambda: "fp")
    trained = []

    def train_bundle():
        trained.append(True)
        return {"schema": OSS.SCHEMA, "fingerprint": "fp", "report": {"retrained": True}}

    monkeypatch.setattr(OSS, "train_bundle", train_bundle)
    return trained


def good_bundle():
    return {"schema": OSS.SCHEMA, "fingerprint": "fp", "report": {"cached": True}}


def test_stance_cache_with_matching_sidecar_loads_without_retraining(oss_cache):
    joblib.dump(good_bundle(), OSS.CACHE)
    L.write_sha256_sidecar(OSS.CACHE)
    assert OSS.load_or_train()["report"] == {"cached": True}
    assert oss_cache == []


def test_stance_cache_with_tampered_file_is_never_unpickled_and_is_retrained(oss_cache, capsys):
    joblib.dump(good_bundle(), OSS.CACHE)
    L.write_sha256_sidecar(OSS.CACHE)
    joblib.dump(Tripwire(), OSS.CACHE)
    bundle = OSS.load_or_train()
    assert TRIPPED == []
    assert oss_cache == [True] and bundle["report"] == {"retrained": True}
    assert "SECURITY" in capsys.readouterr().err
    assert L.safe_to_unpickle(OSS.CACHE) is True       # the retrain replaced the file and its sidecar
    assert joblib.load(OSS.CACHE)["report"] == {"retrained": True}


def test_stance_cache_without_sidecar_loads_as_before_with_a_warning(oss_cache, capsys):
    joblib.dump(good_bundle(), OSS.CACHE)
    assert OSS.load_or_train()["report"] == {"cached": True}
    assert oss_cache == [] and "WARNING" in capsys.readouterr().err
    assert not os.path.exists(OSS.CACHE + ".sha256")   # loading never invents a sidecar


def test_retrain_writes_the_sidecar(oss_cache):
    assert OSS.load_or_train(force=True)["report"] == {"retrained": True}
    assert open(OSS.CACHE + ".sha256").read().strip() == L.sha256_file(OSS.CACHE)


# -- tweet_collector.cmd_classify ----------------------------------------------------------------

@pytest.fixture
def classify_root(tmp_path, monkeypatch):
    (tmp_path / "data" / "stance").mkdir(parents=True)
    database = tmp_path / "social_media.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(T.SCHEMA)
    monkeypatch.setattr(T, "B", str(tmp_path))
    monkeypatch.setattr(T, "DB", str(database))
    return tmp_path / "data" / "stance" / "stance_clf_composed.pkl"


def test_classify_refuses_a_tampered_classifier(classify_root, capsys):
    classify_root.write_bytes(pickle.dumps({"good": 1}))
    L.write_sha256_sidecar(classify_root)
    classify_root.write_bytes(pickle.dumps(Tripwire()))
    T.cmd_classify()
    out = capsys.readouterr()
    assert TRIPPED == [] and "refusing to load" in out.out and "SECURITY" in out.err


def test_classify_loads_a_classifier_with_a_matching_sidecar(classify_root):
    classify_root.write_bytes(pickle.dumps(Tripwire()))
    L.write_sha256_sidecar(classify_root)
    T.cmd_classify()
    assert TRIPPED == [True]


def test_classify_loads_a_classifier_with_no_sidecar_as_before(classify_root, capsys):
    classify_root.write_bytes(pickle.dumps(Tripwire()))
    T.cmd_classify()
    assert TRIPPED == [True] and "WARNING" in capsys.readouterr().err


# -- build_social_master (a script: run it, as the existing Bluesky refresh test does) -----------

def test_build_social_master_refuses_a_tampered_classifier(tmp_path, monkeypatch):
    import runpy
    import paths
    monkeypatch.setattr(paths, "ROOT", str(tmp_path))
    quorum = type(sys)("network.process_quorum")
    quorum.CU_TERMS = re.compile("credit union", re.I)
    monkeypatch.setitem(sys.modules, "network.process_quorum", quorum)
    stance = tmp_path / "data" / "stance"
    stance.mkdir(parents=True)
    (tmp_path / "data" / "engine").mkdir(parents=True)
    (tmp_path / "data" / "engine" / "congestion.json").write_text(json.dumps([]))
    import pandas as pd
    pd.DataFrame(columns=["Document Type", "member", "dt", "Content", "Title", "is_rt", "stance",
                          "stance_margin", "Source URL"]).to_csv(stance / "quorum_member_tweets.csv", index=False)
    with sqlite3.connect(tmp_path / "social_media.db") as connection:
        connection.executescript(T.SCHEMA)
    pkl = stance / "stance_clf_composed.pkl"
    pkl.write_bytes(pickle.dumps({"good": 1}))
    L.write_sha256_sidecar(pkl)
    pkl.write_bytes(pickle.dumps(Tripwire()))
    with pytest.raises(SystemExit, match="refusing to unpickle"):
        runpy.run_path(str(FEC / "network" / "build_social_master.py"), run_name="__main__")
    assert TRIPPED == []


# -- process_quorum is a script over licensed production data, so assert the gate's order instead --

@pytest.mark.parametrize("relative, loader", [
    ("network/process_quorum.py", "pickle.load("),
    ("network/build_social_master.py", "pickle.load("),
    ("collect/tweet_collector.py", "pickle.load("),
    ("stance/own_source_stance_model.py", "joblib.load("),
])
def test_every_model_load_is_preceded_by_the_integrity_check(relative, loader):
    source = (FEC / relative).read_text(encoding="utf-8")
    first_load = source.index(loader)
    assert "safe_to_unpickle(" in source[:first_load], relative


# -- fec/research: every unpickle of a frozen model is gated, and the gate refuses a tampered file -------------------
# The cosponsor scripts load licensed multi-GB panels, so they are not run here: the tests read their source, pull out
# each `if not safe_to_unpickle(X): raise SystemExit(...)` guard and run that very statement against a Tripwire file.

RESEARCH = FEC / "research"
# Hash-bound by cosponsor_institutions/institutions_verify.py ('prior_verifier_helper_code'): editing this file would
# fail that check on the sealed universe receipt, so its load stays unguarded until the experiments are re-sealed.
HASH_BOUND = {"cosponsor_universe/universe_verify.py"}


def _own_calls(statement):
    """Calls in a statement's own expressions, not in the statements nested inside it."""
    pending = [n for n in ast.iter_child_nodes(statement) if not isinstance(n, ast.stmt)]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Call):
            yield node
        pending.extend(n for n in ast.iter_child_nodes(node) if not isinstance(n, ast.stmt))


def _research_loads():
    """(file, statement before the load, load call) for every joblib/pickle load under fec/research."""
    for path in sorted(RESEARCH.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not (isinstance(block, list) and block and isinstance(block[0], ast.stmt)):
                    continue
                for index, statement in enumerate(block):
                    for call in _own_calls(statement):
                        func = call.func
                        if (isinstance(func, ast.Attribute) and func.attr == "load"
                                and isinstance(func.value, ast.Name) and func.value.id in ("joblib", "pickle")):
                            yield (path.relative_to(RESEARCH).as_posix(), block[index - 1] if index else None, call)


def _guarded_path(guard):
    """X from `if not safe_to_unpickle(X): raise ...`, else None."""
    if (isinstance(guard, ast.If) and isinstance(guard.test, ast.UnaryOp) and isinstance(guard.test.op, ast.Not)
            and isinstance(guard.test.operand, ast.Call) and getattr(guard.test.operand.func, "id", "") == "safe_to_unpickle"
            and len(guard.body) == 1 and isinstance(guard.body[0], ast.Raise)):
        return guard.test.operand.args[0]
    return None


LOADS = list(_research_loads())
GATED = [(f, g, c) for f, g, c in LOADS if f not in HASH_BOUND]
SITE_IDS = [f"{f}:{c.lineno}" for f, _, c in GATED]


def test_every_research_load_site_is_known():
    assert len(LOADS) == 7 and {f for f, _, _ in LOADS if f in HASH_BOUND} == HASH_BOUND


@pytest.mark.parametrize("relative, guard, call", GATED, ids=SITE_IDS)
def test_research_load_follows_a_guard_on_the_same_path(relative, guard, call):
    guarded = _guarded_path(guard)
    assert guarded is not None, f"{relative}:{call.lineno} unpickles without the integrity check right before it"
    assert ast.dump(guarded) == ast.dump(call.args[0]), f"{relative}:{call.lineno} guards a different path than it loads"


def _run_site(guard, call, path):
    """Execute the site's own guard statement and then its load, with its path variable bound to `path`."""
    variable = _guarded_path(guard)
    assert isinstance(variable, ast.Name)
    module = ast.fix_missing_locations(ast.Module(body=[guard, ast.Expr(call)], type_ignores=[]))
    exec(compile(module, "<research site>", "exec"),
         {"joblib": joblib, "safe_to_unpickle": L.safe_to_unpickle, variable.id: path})


@pytest.mark.parametrize("relative, guard, call", GATED, ids=SITE_IDS)
def test_research_site_refuses_a_tampered_model_without_unpickling_it(relative, guard, call, tmp_path, capsys):
    path = tmp_path / "m.joblib"
    joblib.dump({"good": 1}, path)
    L.write_sha256_sidecar(path)
    joblib.dump(Tripwire(), path)
    with pytest.raises(SystemExit, match="refusing to unpickle"):
        _run_site(guard, call, path)
    assert TRIPPED == [] and "SECURITY" in capsys.readouterr().err


@pytest.mark.parametrize("relative, guard, call", GATED, ids=SITE_IDS)
def test_research_site_loads_a_verified_model_and_an_unsidecarred_one_as_before(relative, guard, call, tmp_path, capsys):
    path = tmp_path / "m.joblib"
    joblib.dump(Tripwire(), path)
    _run_site(guard, call, path)                       # absent sidecar: warns, loads
    assert TRIPPED == [True] and "WARNING" in capsys.readouterr().err
    TRIPPED.clear()
    L.write_sha256_sidecar(path)
    _run_site(guard, call, path)                       # matching sidecar: loads silently
    assert TRIPPED == [True] and capsys.readouterr().err == ""


def test_the_refit_model_is_saved_with_its_sidecar():
    source = (RESEARCH / "cosponsor_refinement" / "prospective_network.py").read_text(encoding="utf-8")
    assert source.index("joblib.dump(artifact,modelpath)") < source.index("write_sha256_sidecar(modelpath)")
