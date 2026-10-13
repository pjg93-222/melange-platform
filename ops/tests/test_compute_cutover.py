"""Tests for ops/compute/cutover.py: the one way an engine moves to its container (R-6 Option A; the companion ruling).

A switch to `container` needs the phase's parity evidence to say PASS; the evidence here is produced by the real tools where they
exist (chain_parity compare, xl_equivalence compare), so a change in their report shape fails these tests instead of a night's
cut-over. Synthetic trees under tmp_path only.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ops" / "compute"))
sys.path.insert(0, str(REPO / "fec"))
import cutover  # noqa: E402
import compute_engine as CE  # noqa: E402
import xl_equivalence as X  # noqa: E402
from ops.tests import test_chain_parity as tcp  # noqa: E402

WINDOWS = {"chain": "windows", "collect": "windows", "office": "windows"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A tree holding engines.json (all windows, in cutover's own format: not the committed file, which cutover.py moves) and a copy
    of the Glass Box emitter; cutover's REPO points at it."""
    root = tmp_path / "repo"
    (root / "ops" / "compute").mkdir(parents=True)
    (root / "ops" / "compute" / "engines.json").write_bytes((json.dumps(WINDOWS, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    (root / "fec" / "surfaces").mkdir(parents=True)
    shutil.copyfile(REPO / "fec" / "surfaces" / "glassbox_emit.py", root / "fec" / "surfaces" / "glassbox_emit.py")
    monkeypatch.setattr(cutover, "REPO", root)
    monkeypatch.delenv(cutover.PARITY_DIR_ENV, raising=False)
    for key in CE.KEYS:
        monkeypatch.delenv(CE.ENV_PREFIX + key.upper(), raising=False)
    return root


def config(root) -> dict:
    return json.loads((root / "ops" / "compute" / "engines.json").read_text(encoding="utf-8"))


def config_bytes(root) -> bytes:
    return (root / "ops" / "compute" / "engines.json").read_bytes()


class Events(list):
    def __call__(self, kind, status, summary, **kw):
        self.append((kind, status, summary, kw))


def write(path: Path, doc) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return path


def real_chain_report(tmp_path, passing=True) -> Path:
    """The report chain_parity.py itself writes, for a faithful trio (PASS) or for a run in which nothing ran (FAIL)."""
    case = tcp.make_case(tmp_path / ("good" if passing else "bad"), **({} if passing else {"frozen_extra": tcp.base_outputs()}))
    rc, report = tcp.compare(case)
    assert (rc, report["result"]) == ((0, "PASS") if passing else (1, "FAIL"))
    return case.tmp / "report.json"


def real_equivalence_report(tmp_path, equivalent=True) -> Path:
    ref = {"S": {"D1": ("n", 1.0, "SUM(A1:A3)*2"), "C1": ("n", 1.0, "IF(D1>0,1,0)")}}
    cand = ref if equivalent else {"S": {"D1": ("n", 2.0, "SUM(A1:A3)*2"), "C1": ("n", 1.0, "IF(D1>0,1,0)")}}
    report = X.compare(ref, cand, X.make_tier0(ref, {}))
    assert report["equivalent"] is equivalent
    return write(tmp_path / ("eq-good.json" if equivalent else "eq-bad.json"), json.dumps(report, sort_keys=True))


RANKS = {"result": "PASS", "members": 533, "rank_exact": 533}
REPLAY = {"result": "PASS"}


# ------------------------------------------------------------------------------------------------ container: needs PASS

def test_container_is_refused_without_evidence_and_nothing_changes(repo):
    events, before = Events(), config_bytes(repo)
    for phase in ("chain", "collect", "office"):
        with pytest.raises(cutover.Refused, match="does not exist"):
            cutover.set_engine(phase, "container", emit=events)
    assert config_bytes(repo) == before
    assert [e[:2] for e in events] == [("compute.cutover", "refused")] * 3
    assert not [p for p in (repo / "ops" / "compute").iterdir() if p.name != "engines.json"]          # no temp file left behind


def test_a_failing_real_chain_report_is_refused(repo, tmp_path):
    report = real_chain_report(tmp_path, passing=False)
    with pytest.raises(cutover.Refused, match="chain evidence report .*not a clean PASS"):
        cutover.set_engine("chain", "container", evidence={"report": str(report)}, emit=Events())
    assert config(repo) == WINDOWS


@pytest.mark.parametrize("doc,match", [
    ({"result": "PASS", "exit_code": 3}, "not a clean PASS"),
    ({"result": "PASS", "exit_code": 0, "failed_gates": ["G3"]}, "not a clean PASS"),
    ({"result": "NEEDS_ACCEPTANCE", "exit_code": 3}, "not a clean PASS"),
    ({"exit_code": 0}, "not a clean PASS"),
    (["PASS"], "not a JSON object"),
    ("{not json", "unreadable"),
    ("", "unreadable"),
])
def test_a_chain_report_that_is_anything_but_a_clean_pass_is_refused(repo, tmp_path, doc, match):
    with pytest.raises(cutover.Refused, match=match):
        cutover.set_engine("chain", "container", evidence={"report": str(write(tmp_path / "r.json", doc))}, emit=Events())
    assert config(repo) == WINDOWS


@pytest.mark.parametrize("doc", [{"result": "FAIL"}, {"result": "pass"}, {}, {"ok": True}])
def test_a_collectors_replay_report_must_say_pass(repo, tmp_path, doc):
    with pytest.raises(cutover.Refused, match="not PASS"):
        cutover.set_engine("collect", "container", evidence={"report": str(write(tmp_path / "r.json", doc))}, emit=Events())
    assert config(repo) == WINDOWS


def test_office_needs_both_the_equivalence_report_and_the_533_rank_check(repo, tmp_path):
    good_eq, good_rank = real_equivalence_report(tmp_path), write(tmp_path / "ranks.json", RANKS)
    with pytest.raises(cutover.Refused, match="rank_check .*does not exist"):
        cutover.set_engine("office", "container", evidence={"equivalence": str(good_eq), "rank_check": str(tmp_path / "none.json")}, emit=Events())
    with pytest.raises(cutover.Refused, match="equivalence .*does not exist"):
        cutover.set_engine("office", "container", evidence={"equivalence": str(tmp_path / "none.json"), "rank_check": str(good_rank)}, emit=Events())
    bad_eq = real_equivalence_report(tmp_path, equivalent=False)
    with pytest.raises(cutover.Refused, match="equivalent is False"):
        cutover.set_engine("office", "container", evidence={"equivalence": str(bad_eq), "rank_check": str(good_rank)}, emit=Events())
    assert config(repo) == WINDOWS


@pytest.mark.parametrize("doc", [{**RANKS, "rank_exact": 532}, {**RANKS, "members": 532, "rank_exact": 532}, {**RANKS, "result": "FAIL"},
                                 {"result": "PASS"}])
def test_the_rank_check_must_be_pass_with_all_533_exact(repo, tmp_path, doc):
    with pytest.raises(cutover.Refused, match="need PASS with 533/533"):
        cutover.set_engine("office", "container", emit=Events(),
                           evidence={"equivalence": str(real_equivalence_report(tmp_path)), "rank_check": str(write(tmp_path / "ranks.json", doc))})


def test_an_equivalence_that_compared_nothing_is_refused(repo, tmp_path):
    empty = write(tmp_path / "empty.json", {"equivalent": True, "totals": {}})
    with pytest.raises(cutover.Refused, match="non-empty comparison"):
        cutover.set_engine("office", "container", evidence={"equivalence": str(empty), "rank_check": str(write(tmp_path / "r.json", RANKS))}, emit=Events())


def test_every_problem_is_named_not_just_the_first(repo):
    with pytest.raises(cutover.Refused) as error:
        cutover.set_engine("office", "container", emit=Events())
    assert "equivalence" in str(error.value) and "rank_check" in str(error.value)


# ------------------------------------------------------------------------------------------------ a switch that goes through

def test_a_real_passing_chain_report_switches_only_the_chain(repo, tmp_path):
    report, events = real_chain_report(tmp_path), Events()
    message = cutover.set_engine("chain", "container", evidence={"report": str(report)}, emit=events)
    assert config(repo) == {"chain": "container", "collect": "windows", "office": "windows"} and "windows -> container" in message
    raw = config_bytes(repo)
    assert raw.endswith(b"\n") and b"\r" not in raw and raw == (json.dumps(config(repo), indent=2, sort_keys=True) + "\n").encode()
    (kind, status, summary, kw), = events
    assert (kind, status, kw["stage"]) == ("compute.cutover", "completed", "chain")
    assert kw["metadata"]["evidence"]["report"] == {"path": str(report), "sha256": __import__("hashlib").sha256(report.read_bytes()).hexdigest()}
    assert (kw["metadata"]["from"], kw["metadata"]["to"]) == ("windows", "container")
    assert CE.load(repo, environ={}) == {"collect": "windows", "chain": "container", "office": "windows"}      # the refresh reads what was written
    assert not [p for p in (repo / "ops" / "compute").iterdir() if p.name != "engines.json"]


def test_collect_and_office_switch_with_their_evidence(repo, tmp_path):
    cutover.set_engine("collect", "container", evidence={"report": str(write(tmp_path / "replay.json", REPLAY))}, emit=Events())
    cutover.set_engine("publish", "container", emit=Events(),         # `publish` is the phase name of the office engine
                       evidence={"equivalence": str(real_equivalence_report(tmp_path)), "rank_check": str(write(tmp_path / "ranks.json", RANKS))})
    assert config(repo) == {"chain": "windows", "collect": "container", "office": "container"}


def test_the_default_evidence_files_are_found_in_the_parity_directory(repo, tmp_path, monkeypatch):
    monkeypatch.setenv(cutover.PARITY_DIR_ENV, str(tmp_path / "parity"))
    write(tmp_path / "parity" / "collectors_replay.json", REPLAY)
    cutover.set_engine("collect", "container", emit=Events())
    assert config(repo)["collect"] == "container"
    shutil.copyfile(real_chain_report(tmp_path), tmp_path / "elsewhere.json")
    cutover.set_engine("chain", "container", directory=str(tmp_path), evidence={"report": str(tmp_path / "elsewhere.json")}, emit=Events())
    assert config(repo)["chain"] == "container"


def test_windows_is_always_allowed_and_is_the_one_flag_fallback(repo):
    events = Events()
    (repo / "ops" / "compute" / "engines.json").write_text(json.dumps({"chain": "container", "collect": "container", "office": "windows"}), encoding="utf-8")
    cutover.set_engine("chain", "windows", emit=events)               # no evidence asked, none needed
    assert config(repo) == {"chain": "windows", "collect": "container", "office": "windows"}
    assert [e[:2] for e in events] == [("compute.cutover", "completed")] and events[0][3]["metadata"]["evidence"] == {}


def test_a_switch_that_changes_nothing_writes_and_emits_nothing(repo):
    events, before = Events(), config_bytes(repo)
    assert "already windows" in cutover.set_engine("chain", "windows", emit=events)
    assert events == [] and config_bytes(repo) == before


def test_an_unreadable_engine_file_is_never_overwritten(repo):
    (repo / "ops" / "compute" / "engines.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(cutover.Refused, match="unreadable"):
        cutover.set_engine("chain", "windows", emit=Events())
    assert config_bytes(repo) == b"{not json"


def test_an_unknown_phase_or_engine_is_a_usage_error(repo):
    for phase, engine in (("bogus", "container"), ("chain", "docker")):
        with pytest.raises(ValueError):
            cutover.set_engine(phase, engine, emit=Events())


def test_the_event_lands_in_the_glass_box_stream(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSBOX_EVENTS_DIR", str(tmp_path / "events"))
    cutover.set_engine("collect", "container", evidence={"report": str(write(tmp_path / "replay.json", REPLAY))})
    with pytest.raises(cutover.Refused):
        cutover.set_engine("chain", "container", evidence={"report": str(tmp_path / "none.json")})
    events = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((tmp_path / "events").glob("*.json"))]
    assert [(e["kind"], e["status"], e["stage"], e["source"]) for e in events] == [
        ("compute.cutover", "completed", "collect", "melange-compute"), ("compute.cutover", "refused", "chain", "melange-compute")]


# ------------------------------------------------------------------------------------------------------------ the CLI

def test_cli_set_and_show(repo, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GLASSBOX_EVENTS_DIR", str(tmp_path / "events"))
    report = write(tmp_path / "replay.json", REPLAY)
    assert cutover.main(["set", "collect", "container", "--evidence", f"report={report}"]) == 0
    assert "collect: windows -> container" in capsys.readouterr().out
    assert cutover.main(["show"]) == 0
    assert capsys.readouterr().out.split() == ["collect", "container", "chain", "windows", "office", "windows"]
    assert cutover.main(["set", "collect", "windows"]) == 0 and config(repo) == WINDOWS


def test_cli_refusal_exits_1_and_says_why(repo, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GLASSBOX_EVENTS_DIR", str(tmp_path / "events"))
    assert cutover.main(["set", "chain", "container", "--evidence-dir", str(tmp_path / "empty")]) == 1
    assert "REFUSED" in capsys.readouterr().err and config(repo) == WINDOWS


def test_cli_notes_an_environment_override(repo, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GLASSBOX_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv("MELANGE_ENGINE_COLLECT", "windows")
    assert cutover.main(["set", "collect", "container", "--evidence", f"report={write(tmp_path / 'r.json', REPLAY)}"]) == 0
    assert "overrides the file" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["set", "bogus", "container"], ["set", "chain", "docker"], ["set", "chain", "container", "--evidence", "nope=x"],
                                  ["set", "chain", "container", "--evidence", "report"], []])
def test_cli_usage_errors_exit_2(repo, argv):
    with pytest.raises(SystemExit) as error:
        cutover.main(argv)
    assert error.value.code == 2
    assert config(repo) == WINDOWS
