"""Chain step-6 tolerance contract for fec/run_pipeline.py.

Every assertion here is TEXTUAL (ast / regex over the source), exactly as
fec/weekly_refresh.py's _chain_tail_is_audit_full does. ``run_pipeline`` is never
imported: its module-scope code walks the whole producer tree and resolves every
CHAIN entry, which is not something a unit test may trigger. The pure helpers
(_last_good_ok, _last_good_receipt_ok, _record_tolerated, _prune_tolerated) are
extracted by ast and exec'd into a namespace that holds only ``os`` and ``json`` --
that is the entire surface they may use.

Runs under pytest (plain functions, ``tmp_path`` supplied positionally, no
monkeypatch).
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re

RUN_PIPELINE = Path(__file__).resolve().parents[2] / "run_pipeline.py"

# Same block/entry regexes weekly_refresh.py uses for its chain-tail guard.
CHAIN_BLOCK_RE = re.compile(r"^CHAIN\s*=\s*\[(.*?)\n\]", re.M | re.S)
CHAIN_ENTRY_RE = re.compile(r"\(\s*'([^']+)'\s*,\s*\[[^\]]*\]\s*\)")

RECEIPT_NOTE = "note: tolerated-failure receipt not written"


def _source() -> str:
    with open(RUN_PIPELINE, encoding="utf-8") as handle:
        return handle.read()


def _module_assignment(name: str):
    """Return the literal value of the module-level ``name = <literal>`` assignment."""
    tree = ast.parse(_source())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"no module-level assignment to {name!r} in {RUN_PIPELINE}")


def _function_source(name: str) -> str:
    """Return the exact source of the module-level function ``name`` (nothing else)."""
    source = _source()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment, f"empty source segment for {name}"
            return segment
    raise AssertionError(f"no module-level function {name!r} in {RUN_PIPELINE}")


def _exec_helper(name: str):
    """Exec ONE function's source into a fresh namespace holding only os and json."""
    namespace = {"os": os, "json": json, "__name__": f"_helper_{name}"}
    exec(compile(_function_source(name), str(RUN_PIPELINE), "exec"), namespace)
    return namespace[name]


def _call_site_is_guarded(source: str, call_text: str) -> None:
    """The runner line holding ``call_text`` must sit directly under ``try:`` and be followed by an
    ``except Exception`` that prints the receipt note -- the receipt is a report, never a gate."""
    lines = source.splitlines()
    hits = [i for i, line in enumerate(lines) if call_text in line]
    assert len(hits) == 1, f"{call_text!r} must appear exactly once in main(); found {len(hits)}"
    i = hits[0]
    assert lines[i - 1].strip() == "try:", f"line before {call_text!r} is {lines[i - 1]!r}, not try:"
    following = "\n".join(lines[i + 1:i + 5])
    assert "except Exception as exc:" in following, following
    assert RECEIPT_NOTE in following, following
    assert "str(exc)[:120]" in following, following


# ---------------------------------------------------------------- (i) TOLERANT_STEPS

def test_tolerant_steps_is_exactly_build_descriptive_speech():
    tolerant = _module_assignment("TOLERANT_STEPS")
    assert set(tolerant) == {"build_descriptive_speech.py"}, tolerant


def test_tolerant_step_is_a_real_chain_entry():
    block = CHAIN_BLOCK_RE.search(_source())
    assert block, "no CHAIN=[...] block"
    entries = CHAIN_ENTRY_RE.findall(block.group(1))
    for name in _module_assignment("TOLERANT_STEPS"):
        assert name in entries, f"{name} is tolerant but not in CHAIN"


# ---------------------------------------------------------------- (ii) chain tail

def test_last_chain_entry_is_audit_full():
    """weekly_refresh's tolerance and this one both rest on audit_full.py being LAST."""
    block = CHAIN_BLOCK_RE.search(_source())
    assert block, "no CHAIN=[...] block parsed from run_pipeline.py"
    entries = CHAIN_ENTRY_RE.findall(block.group(1))
    assert entries, "no ('name',[...]) tuples parsed from the CHAIN block"
    assert entries[-1] == "audit_full.py", entries[-1]


# ---------------------------------------------------------------- (iii) the guard

def test_tolerated_branch_requires_last_good_artifact_and_pass_receipt():
    """The single line that decides tolerance must AND the step set with BOTH artifact checks."""
    guard_lines = [
        line for line in _source().splitlines()
        if "tolerated=" in line and "TOLERANT_STEPS" in line
    ]
    assert len(guard_lines) == 1, guard_lines
    guard = guard_lines[0]
    assert "script in TOLERANT_STEPS" in guard, guard
    assert "_last_good_ok(last_good)" in guard, guard
    assert "_last_good_receipt_ok(last_good_receipt)" in guard, guard
    # Both paths are the LAST_GOOD / LAST_GOOD_RECEIPT entries for the step, resolved against MB_ROOT.
    source = _source()
    assert "last_good=LAST_GOOD.get(script)" in source
    assert "last_good=os.path.join(MB_ROOT,last_good)" in source
    assert "last_good_receipt=LAST_GOOD_RECEIPT.get(script)" in source
    assert "last_good_receipt=os.path.join(MB_ROOT,last_good_receipt)" in source


def test_tolerance_guard_requires_exit_code_2_only():
    """Only the producer's own contract refusal (exit 2) is tolerated; a crash still aborts."""
    guard_lines = [
        line for line in _source().splitlines()
        if "tolerated=" in line and "TOLERANT_STEPS" in line
    ]
    assert len(guard_lines) == 1, guard_lines
    guard = guard_lines[0]
    assert "run.returncode==2" in guard, guard
    assert "run.returncode!=0" not in guard, guard
    # The comment above TOLERANT_STEPS documents the same contract.
    source = _source()
    comment_end = source.index("TOLERANT_STEPS={")
    comment = source[max(0, comment_end - 900):comment_end]
    assert "exit code 2" in comment, comment
    assert "crash" in comment.lower(), comment


def test_tolerated_branch_continues_and_hard_failures_still_return_1():
    source = _source()
    branch = source.index("if tolerated:")
    halt = source.index("_gb('pipeline.run','failed','chain halted at %s'%script")
    assert branch < halt, "tolerated branch must be decided before the hard-abort path"
    body = source[branch:halt]
    assert "_record_tolerated(receipt_path,entry)" in body
    assert "tolerated_steps.append(script)" in body
    assert body.rstrip().endswith("continue"), body[-200:]
    assert "return 1" in source[halt:halt + 400]


def test_receipt_is_pruned_once_before_the_first_step_resume_aware():
    """A full run resets the receipt; a --from resume drops only this run's slice."""
    source = _source()
    assert "_record_tolerated(receipt_path,reset=True)" not in source, \
        "the unconditional per-run reset was replaced by the resume-aware prune"
    prune_lines = [line for line in source.splitlines()
                   if "_prune_tolerated(receipt_path," in line and not line.lstrip().startswith("def ")]
    assert len(prune_lines) == 1, prune_lines
    prune = prune_lines[0]
    assert "CHAIN[first:]" in prune, prune
    assert "first==0" in prune, prune
    assert source.index("_prune_tolerated(receipt_path,") < source.index(
        "for script,script_args in CHAIN[first:]:"), "the prune must precede the step loop"
    # Still gated on the receipt existing: a clean chain writes nothing it did not write before.
    lines = source.splitlines()
    i = next(k for k, line in enumerate(lines)
             if "_prune_tolerated(receipt_path," in line and not line.lstrip().startswith("def "))
    assert lines[i - 2].strip() == "if os.path.exists(receipt_path):", lines[i - 2]


def test_both_receipt_writes_in_the_runner_are_best_effort():
    """(iii) A directory at the receipt path makes the helper raise OSError (below); the runner
    survives because BOTH call sites are wrapped and print the note instead of aborting."""
    source = _source()
    main_body = source[source.index("def main(argv=None):"):]
    _call_site_is_guarded(main_body, "_prune_tolerated(receipt_path,")
    _call_site_is_guarded(main_body, "_record_tolerated(receipt_path,entry)")
    assert main_body.count(RECEIPT_NOTE) == 2, main_body.count(RECEIPT_NOTE)


def test_completion_line_unchanged_when_nothing_tolerated():
    source = _source()
    assert "done='PIPELINE COMPLETE in %.1f min'%((time.time()-t0)/60)" in source
    assert "tolerated display-only failure(s): %s" in source
    assert "if tolerated_steps:" in source


# ---------------------------------------------------------------- (iv) constants

def test_tolerated_receipt_path_constant():
    assert _module_assignment("TOLERATED_RECEIPT") == "data/engine/chain_tolerated_failures.json"
    # resolved against the runner's own root, like every other relative data path in the file
    assert "receipt_path=os.path.join(MB_ROOT,TOLERATED_RECEIPT)" in _source()


def test_last_good_maps_the_tolerant_step_to_its_artifact():
    last_good = _module_assignment("LAST_GOOD")
    assert last_good == {"build_descriptive_speech.py": "data/speech/speech_descriptive_v1.json"}
    assert set(last_good) == set(_module_assignment("TOLERANT_STEPS"))


def test_last_good_receipt_maps_the_tolerant_step_to_the_producers_receipt():
    receipt = _module_assignment("LAST_GOOD_RECEIPT")
    assert receipt == {
        "build_descriptive_speech.py": "data/speech/speech_descriptive_v1.receipt.json"
    }
    assert set(receipt) == set(_module_assignment("TOLERANT_STEPS"))
    assert set(receipt) == set(_module_assignment("LAST_GOOD"))


# ---------------------------------------------------------------- (v) pure helpers

def test_last_good_ok_false_for_missing_empty_and_true_for_nonempty(tmp_path):
    last_good_ok = _exec_helper("_last_good_ok")
    missing = tmp_path / "missing.json"
    assert last_good_ok(str(missing)) is False
    assert last_good_ok(None) is False
    assert last_good_ok("") is False
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    assert last_good_ok(str(empty)) is False
    directory = tmp_path / "a_dir"
    directory.mkdir()
    assert last_good_ok(str(directory)) is False
    good = tmp_path / "good.json"
    good.write_text("{}", encoding="utf-8")
    assert last_good_ok(str(good)) is True


def test_last_good_receipt_ok_true_only_for_a_parseable_pass_receipt(tmp_path):
    """(i) missing / malformed / status != PASS -> False; a PASS receipt -> True."""
    receipt_ok = _exec_helper("_last_good_receipt_ok")
    assert receipt_ok(None) is False
    assert receipt_ok("") is False
    assert receipt_ok(str(tmp_path / "missing.receipt.json")) is False

    malformed = tmp_path / "malformed.receipt.json"
    malformed.write_text("{not json", encoding="utf-8")
    assert receipt_ok(str(malformed)) is False

    not_an_object = tmp_path / "list.receipt.json"
    not_an_object.write_text(json.dumps(["PASS"]), encoding="utf-8")
    assert receipt_ok(str(not_an_object)) is False

    no_status = tmp_path / "nostatus.receipt.json"
    no_status.write_text(json.dumps({"artifact": "x"}), encoding="utf-8")
    assert receipt_ok(str(no_status)) is False

    failed = tmp_path / "fail.receipt.json"
    failed.write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")
    assert receipt_ok(str(failed)) is False

    wrong_case = tmp_path / "pass_lower.receipt.json"
    wrong_case.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    assert receipt_ok(str(wrong_case)) is False

    directory = tmp_path / "a_dir.receipt.json"
    directory.mkdir()
    assert receipt_ok(str(directory)) is False

    passed = tmp_path / "pass.receipt.json"
    passed.write_text(json.dumps({"status": "PASS", "artifact_sha256": "abc"}), encoding="utf-8")
    assert receipt_ok(str(passed)) is True


def test_record_tolerated_writes_appends_and_resets(tmp_path):
    record = _exec_helper("_record_tolerated")
    receipt = tmp_path / "engine" / "chain_tolerated_failures.json"   # parent does not exist yet

    first = {"step": "build_descriptive_speech.py", "returncode": 1, "tail": "stale receipt"}
    written = record(str(receipt), first)
    assert written == [first]
    assert json.loads(receipt.read_text(encoding="utf-8")) == [first]

    second = {"step": "build_descriptive_speech.py", "returncode": 2, "tail": "again"}
    written = record(str(receipt), second)
    assert written == [first, second]
    assert json.loads(receipt.read_text(encoding="utf-8")) == [first, second]

    # reset: the list is emptied even when an entry is not supplied
    assert record(str(receipt), reset=True) == []
    assert json.loads(receipt.read_text(encoding="utf-8")) == []

    # reset + entry: starts from [] and appends
    assert record(str(receipt), second, reset=True) == [second]
    assert json.loads(receipt.read_text(encoding="utf-8")) == [second]

    # atomic write leaves no temp file behind
    assert sorted(p.name for p in receipt.parent.iterdir()) == [receipt.name]


def test_record_tolerated_treats_garbage_receipt_as_empty(tmp_path):
    record = _exec_helper("_record_tolerated")
    receipt = tmp_path / "chain_tolerated_failures.json"
    receipt.write_text("not json", encoding="utf-8")
    entry = {"step": "x"}
    assert record(str(receipt), entry) == [entry]
    receipt.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert record(str(receipt), entry) == [entry]


def test_record_tolerated_raises_oserror_when_a_directory_squats_on_the_receipt(tmp_path):
    """(iii) The helper itself does NOT swallow the failure -- the runner's wrapped call sites do
    (test_both_receipt_writes_in_the_runner_are_best_effort). A silent helper would hide a squatting
    directory from the chain log."""
    record = _exec_helper("_record_tolerated")
    receipt_dir = tmp_path / "chain_tolerated_failures.json"
    receipt_dir.mkdir()
    try:
        record(str(receipt_dir), {"step": "build_descriptive_speech.py"})
    except OSError:
        pass
    else:
        raise AssertionError("expected OSError when a directory squats on the receipt path")
    assert receipt_dir.is_dir(), "the squatting directory must be left untouched"


def test_prune_tolerated_keeps_entries_outside_the_slice_and_drops_those_inside(tmp_path):
    """(iv) resume: only the re-run steps are cleared; full_reset empties the receipt."""
    prune = _exec_helper("_prune_tolerated")
    receipt = tmp_path / "engine" / "chain_tolerated_failures.json"
    receipt.parent.mkdir()
    a = {"step": "build_descriptive_speech.py", "returncode": 2}
    b = {"step": "later_step.py", "returncode": 2}
    c = {"step": "last_step.py", "returncode": 2}
    receipt.write_text(json.dumps([a, b, c, "not an object", {"no_step": True}]), encoding="utf-8")

    # --from later_step: the slice re-runs b and c; a (outside the slice) survives; junk is dropped.
    kept = prune(str(receipt), ["later_step.py", "last_step.py"], False)
    assert kept == [a]
    assert json.loads(receipt.read_text(encoding="utf-8")) == [a]

    # a slice touching nothing on the record keeps everything well-formed
    assert prune(str(receipt), ["unrelated.py"], False) == [a]

    # a slice covering the remaining entry empties the record
    assert prune(str(receipt), ["build_descriptive_speech.py"], False) == []

    # full_reset empties regardless of the slice
    receipt.write_text(json.dumps([a, b]), encoding="utf-8")
    assert prune(str(receipt), [], True) == []
    assert json.loads(receipt.read_text(encoding="utf-8")) == []
    receipt.write_text(json.dumps([a, b]), encoding="utf-8")
    assert prune(str(receipt), ["later_step.py"], True) == []

    # unparseable receipt is [] under both modes
    receipt.write_text("not json", encoding="utf-8")
    assert prune(str(receipt), ["later_step.py"], False) == []

    # atomic write leaves no temp file behind
    assert sorted(p.name for p in receipt.parent.iterdir()) == [receipt.name]


def test_prune_tolerated_raises_oserror_when_a_directory_squats_on_the_receipt(tmp_path):
    prune = _exec_helper("_prune_tolerated")
    receipt_dir = tmp_path / "chain_tolerated_failures.json"
    receipt_dir.mkdir()
    try:
        prune(str(receipt_dir), ["build_descriptive_speech.py"], True)
    except OSError:
        pass
    else:
        raise AssertionError("expected OSError when a directory squats on the receipt path")


def test_helpers_use_only_os_and_json():
    """The exec'd namespace holds only os and json; any other global would NameError here."""
    builtins_ok = {"isinstance", "list", "dict", "set", "open", "OSError", "ValueError", "True", "False"}
    locals_ok = {
        "path", "receipt_path", "entry", "reset", "entries", "f", "parent", "tmp",   # round 1
        "receipt", "steps_in_slice", "full_reset", "drop",                          # round 2
    }
    for name in ("_last_good_ok", "_last_good_receipt_ok", "_record_tolerated", "_prune_tolerated"):
        tree = ast.parse(_function_source(name))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        unexpected = names - {"os", "json"} - builtins_ok - locals_ok
        assert not unexpected, f"{name} references {sorted(unexpected)}"
