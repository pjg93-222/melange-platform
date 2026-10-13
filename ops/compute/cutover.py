#!/usr/bin/env python3
"""Switch a compute phase to its container, or back to Windows (R-6 Option A; the companion ruling: a system moves to
containers automatically once its parity gate passes, and keeps a one-flag Windows fallback).

    python ops/compute/cutover.py show
    python ops/compute/cutover.py set <collect|chain|office> container|windows [--evidence SLOT=PATH ...] [--evidence-dir DIR]

`set X container` is refused unless the phase's parity evidence says PASS.  `set X windows` is the fallback and is never
refused.  Either way the engine file ops/compute/engines.json is replaced atomically and one Glass Box event is emitted
(`compute.cutover`, completed or refused).  A switch that changes nothing writes and emits nothing.  The refresh reads the file
once when it starts, so a running refresh keeps its engines; MELANGE_ENGINE_<PHASE> in the environment overrides the file.

Evidence (all JSON; the files live in --evidence-dir, default $MELANGE_PARITY_DIR or audits/compute_parity, unless a slot is
named with --evidence SLOT=PATH):

    phase    slot         default file             must say
    chain    report       chain_parity.json        what `chain_parity.py compare --report` writes: result PASS, exit_code 0, no failed gates
    collect  report       collectors_replay.json   result PASS   (no tool writes this report yet; the replay harness's job)
    office   equivalence  office_equivalence.json  what `xl_equivalence.py compare --report` writes: equivalent true, cells compared > 0
    office   rank_check   office_rank_check.json   result PASS, members 533, rank_exact 533   (D-174's 533-rank check; also nothing writes
                                                   it yet: compute_pillars audit mode prints `rank EXACT 533/533`)

A report that is missing, unreadable, not an object, or says anything but PASS refuses the switch; the sha256 of every file read is
recorded in the event.  Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "fec"))        # compute_engine, the one reader of engines.json
from ops.release_lib import core             # noqa: E402
import compute_engine as CE                  # noqa: E402

RANK_MEMBERS = 533
PARITY_DIR_ENV = "MELANGE_PARITY_DIR"


class Refused(Exception):
    """The switch was not made.  The message names every reason."""


def _chain(doc):
    if doc.get("result") != "PASS" or doc.get("exit_code") != 0 or doc.get("failed_gates"):
        return "result %r, exit_code %r, failed_gates %r: not a clean PASS" % (doc.get("result"), doc.get("exit_code"), doc.get("failed_gates"))


def _replay(doc):
    if doc.get("result") != "PASS":
        return "result is %r, not PASS" % (doc.get("result"),)


def _equivalence(doc):
    compared = (doc.get("totals") or {}).get("compared", 0)
    if doc.get("equivalent") is not True or not compared:
        return "equivalent is %r with %r cells compared: not an equivalence of a non-empty comparison" % (doc.get("equivalent"), compared)


def _ranks(doc):
    if doc.get("result") != "PASS" or doc.get("members") != RANK_MEMBERS or doc.get("rank_exact") != RANK_MEMBERS:
        return "result %r, members %r, rank_exact %r: need PASS with %d/%d" % (
            doc.get("result"), doc.get("members"), doc.get("rank_exact"), RANK_MEMBERS, RANK_MEMBERS)


EVIDENCE = {          # engine key -> {slot: (default file name, check)}; a check returns a problem string or None
    "chain": {"report": ("chain_parity.json", _chain)},
    "collect": {"report": ("collectors_replay.json", _replay)},
    "office": {"equivalence": ("office_equivalence.json", _equivalence), "rank_check": ("office_rank_check.json", _ranks)},
}


def parity_dir(environ=None):
    environ = os.environ if environ is None else environ
    return Path(environ.get(PARITY_DIR_ENV) or REPO / "audits" / "compute_parity")


def check_evidence(key, paths=None, directory=None):
    """{slot: {'path', 'sha256'}} for a phase whose evidence is all PASS, else Refused naming every problem."""
    proof, problems = {}, []
    for slot, (name, check) in EVIDENCE[key].items():
        path = Path((paths or {}).get(slot) or Path(directory or parity_dir()) / name)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            problem = check(doc) if isinstance(doc, dict) else "is not a JSON object"
        except FileNotFoundError:
            problem = "does not exist"
        except (OSError, ValueError) as error:
            problem = "is unreadable: %s" % error
        if problem:
            problems.append("%s evidence %s %s: %s" % (key, slot, path, problem))
        else:
            proof[slot] = {"path": str(path), "sha256": core.sha256_file(path)}
    if problems:
        raise Refused("; ".join(problems))
    return proof


def _emitter(repo):
    def emit(kind, status, summary, **kw):
        try:
            module = core.load_live_module(Path(repo) / "fec" / "surfaces" / "glassbox_emit.py", "glassbox_cutover")
            module.emit(kind, status, summary, source="melange-compute", lane="release", **kw)
        except Exception:
            pass          # observation never blocks a switch (the Glass Box rule: emit, but cannot be the thing that fails)
    return emit


def set_engine(key, engine, *, repo=None, evidence=None, directory=None, emit=None):
    """Make `engine` the engine of phase `key` in <repo>/ops/compute/engines.json.  -> a one-line summary; raises Refused."""
    repo = Path(repo or REPO)
    key = CE.PHASE_KEY.get(key, key)
    if key not in CE.KEYS or engine not in CE.ENGINES:
        raise ValueError("phase must be one of %s and engine one of %s" % (", ".join(CE.KEYS), ", ".join(CE.ENGINES)))
    emit = _emitter(repo) if emit is None else emit
    path = repo.joinpath(*CE.CONFIG)
    try:
        current = CE.load(repo, environ={})
    except CE.EngineError as error:
        raise Refused(str(error))
    old = current[key]
    proof = {}
    if engine == "container":
        try:
            proof = check_evidence(key, evidence, directory)
        except Refused as error:
            emit("compute.cutover", "refused", "engine %s: %s -> %s refused" % (key, old, engine), stage=key, detail=str(error)[:1900],
                 metadata={"phase": key, "from": old, "to": engine})
            raise
    if old == engine:
        return "engine %s is already %s: nothing changed" % (key, engine)
    document = json.dumps({**current, key: engine}, indent=2, sort_keys=True) + "\n"
    core.atomic_write(path, document.encode("utf-8"))
    emit("compute.cutover", "completed", "engine %s: %s -> %s" % (key, old, engine), stage=key,
         metadata={"phase": key, "from": old, "to": engine, "config": str(path), "evidence": proof})
    return "engine %s: %s -> %s (%s)" % (key, old, engine, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="print the engine of every phase")
    put = sub.add_parser("set", help="switch one phase")
    put.add_argument("phase", help="collect, chain or office (publish is accepted for office)")
    put.add_argument("engine", choices=CE.ENGINES)
    put.add_argument("--evidence", action="append", default=[], metavar="SLOT=PATH", help="use this file for an evidence slot")
    put.add_argument("--evidence-dir", help="where the default evidence files are (default: $%s or audits/compute_parity)" % PARITY_DIR_ENV)
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            for key, engine in CE.load(REPO).items():
                print("%-8s %s" % (key, engine))
            return 0
        key = CE.PHASE_KEY.get(args.phase, args.phase)
        if key not in CE.KEYS:
            parser.error("phase must be one of %s" % ", ".join(CE.KEYS))
        slots = {}
        for item in args.evidence:
            slot, _, value = item.partition("=")
            if slot not in EVIDENCE[key] or not value:
                parser.error("%s has the evidence slots %s; use SLOT=PATH" % (key, ", ".join(EVIDENCE[key])))
            slots[slot] = value
        print(set_engine(key, args.engine, evidence=slots, directory=args.evidence_dir))
        override = os.environ.get(CE.ENV_PREFIX + key.upper())
        if override:
            print("note: %s%s=%s in this environment overrides the file" % (CE.ENV_PREFIX, key.upper(), override))
        return 0
    except (Refused, CE.EngineError) as error:
        print("cutover: REFUSED: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
