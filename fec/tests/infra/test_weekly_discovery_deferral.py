"""Congress.gov bill discovery is a soft step: a failure never hard-aborts the weekly refresh.

weekly_refresh.py cannot be imported (module scope takes locks and runs the refresh), so the
real step(), _soft_fail() and the module-level discovery and abort statements are executed from
the source text over a stubbed child process. Discovery yields pending-only candidates that
cannot score, so downstream keeps using the last-good discovery state; the failure is recorded
in the status marker's soft_failures and the completion summary, never in the abort list.
"""
from __future__ import annotations

import ast
import datetime
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

FEC = Path(__file__).resolve().parents[2]
SOURCE = (FEC / "weekly_refresh.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SOURCE)
DISCOVERY = "Congress.gov bill discovery"


class HardAbort(Exception):
    pass


def function_source(name):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(SOURCE, node)


def statement(predicate):
    return next(node for node in TREE.body if predicate(node))


def is_step_guard(node, step_name):
    test = getattr(node, "test", None)
    return (isinstance(node, ast.If) and isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Call) and test.operand.args
            and isinstance(test.operand.args[0], ast.Constant) and test.operand.args[0].value == step_name)


def is_abort_guard(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
            and node.test.id == "_deferred_collection_failures")


def run_statement(node, namespace):
    exec(compile(ast.Module(body=[node], type_ignores=[]), "weekly_refresh.py", "exec"), namespace)


def weekly_world(*, returncode=1, timed_out=False):
    logs, statuses, events, aborts = [], [], [], []

    def run_bounded(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="boom", returncode=returncode, timed_out=timed_out,
                               timeout_note="timed out")

    def hard_abort(name, failures, why):
        aborts.append((name, list(failures), why))
        raise HardAbort(why)

    spec = SimpleNamespace(argv=[sys.executable], env=None, phase="collect", container=None)
    namespace = {
        "bounded_subprocess": SimpleNamespace(run_bounded=run_bounded),
        "compute_engine": SimpleNamespace(launch=lambda *args, **kwargs: spec, reap=lambda s: None),
        "subprocess": subprocess, "sys": sys, "os": os, "time": time, "datetime": datetime,
        "FE": str(FEC), "_LIVE_CHILD": [None], "_tolerated": [], "_soft_failures": [],
        "_deferred_collection_failures": [],
        "_producer_path": lambda name: os.path.join(str(FEC), *name.split("/")),
        "_chain_tail_is_audit_full": lambda: True, "log": logs.append,
        "_write_status": lambda **kw: statuses.append(kw),
        "_emit": lambda *args, **kwargs: events.append((args, kwargs)), "_hard_abort": hard_abort,
        "_lease_handoff_out": lambda: None, "_lease_handoff_in": lambda: None,
    }
    for name in ("_adopt_live_child", "_soft_fail", "step"):
        exec(function_source(name), namespace)
    return SimpleNamespace(ns=namespace, logs=logs, statuses=statuses, events=events, aborts=aborts)


def run_discovery(world):
    run_statement(statement(lambda node: is_step_guard(node, DISCOVERY)), world.ns)


@pytest.mark.parametrize("failure", [{"returncode": 1}, {"returncode": 124, "timed_out": True}])
def test_a_failed_discovery_is_a_soft_failure_not_a_deferred_collection_prerequisite(failure):
    world = weekly_world(**failure)
    run_discovery(world)
    assert world.ns["_deferred_collection_failures"] == []
    assert world.ns["_soft_failures"] == [DISCOVERY]
    assert world.aborts == []
    assert {"soft_failures": [DISCOVERY]} in world.statuses
    failed = [(args, kwargs) for args, kwargs in world.events if args[:2] == ("refresh.step", "failed")]
    assert len(failed) == 1 and failed[0][1]["metadata"]["hard"] is False
    assert any("last-good candidate set retained" in line for line in world.logs)


def test_discovery_alone_does_not_trip_the_deferred_prerequisites_abort():
    world = weekly_world()
    run_discovery(world)
    run_statement(statement(is_abort_guard), world.ns)  # the hard abort that follows press acceptance
    assert world.aborts == []


def test_any_other_deferred_collection_failure_still_aborts_after_press_acceptance():
    world = weekly_world()
    run_discovery(world)
    world.ns["_deferred_collection_failures"].append("interviews: CNN (14d)")
    with pytest.raises(HardAbort):
        run_statement(statement(is_abort_guard), world.ns)
    assert world.aborts[0][0] == "deferred collection prerequisites"
    assert world.aborts[0][1] == ["interviews: CNN (14d)"]


def test_the_completion_status_and_summary_report_soft_failures():
    final = next(node for node in ast.walk(TREE) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "_write_status"
                 and any(k.arg == "last_outcome" and isinstance(k.value, ast.IfExp) for k in node.keywords))
    assert "soft_failures" in {k.arg for k in final.keywords}
    assert "non-hard step(s) failed" in SOURCE
