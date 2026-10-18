#!/usr/bin/env python3
"""The switch between Excel and LibreOffice recalculation, and the map of every Excel COM site (D-174).

    MELANGE_XL_ENGINE=excel          (default) the existing path, unchanged: fec/recalculate_formula_cache_copy.ps1
    MELANGE_XL_ENGINE=libreoffice    ops/compute/office_recalc.py, in the office-calc container (or locally when
                                     MELANGE_OFFICE_CALC_LOCAL=1 and soffice is installed, e.g. inside that container)

    python ops/compute/xl_engine.py sites                 # the map below, one line per site
    python ops/compute/xl_engine.py recalc --in COPY.xlsx --expect-sha SHA [--out VIEW.xlsx] [--receipt R.json]

recalc_copy() is the one replacement call. It always works on a COPY, and the LibreOffice engine never writes the
workbook it was given: it writes a view copy. SITES says, for every Excel site in the tree, what replaces it and what
state that replacement is in; ops/tests/test_office_recalc.py fails when a new COM site appears without an entry.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ENGINE_ENV = "MELANGE_XL_ENGINE"
IMAGE_ENV = "IMAGE_OFFICE_CALC"            # ghcr.io/pjg93-222/office-calc@sha256:... (by digest, from ops/compute/images.lock)
LOCAL_ENV = "MELANGE_OFFICE_CALC_LOCAL"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PS1 = REPO / "fec" / "recalculate_formula_cache_copy.ps1"
COMPOSE = HERE / "office-calc.compose.yaml"
ENGINES = ("excel", "libreoffice")


def engine(name=None) -> str:
    value = (name or os.environ.get(ENGINE_ENV) or "excel").strip().lower()
    if value not in ENGINES:
        raise ValueError(f"{ENGINE_ENV} must be one of {ENGINES}, got {value!r}")
    return value


def recalc_copy(workbook, *, expected_sha256, receipt=None, out=None, engine_name=None, method="CalculateFull",
                timeout=600, policy="melange", allow_functions=(), run=subprocess.run) -> Path:
    """Recalculate a COPY of a workbook and return the path that holds the new cached values.

    excel: the copy is recalculated and saved in place (it must sit under C:\\tmp, as the .ps1 insists); returns `workbook`.
    libreoffice: `workbook` is only read; the results go into a view copy at `out`; returns `out`.
    Both leave a receipt (`receipt`, default next to the result for LibreOffice)."""
    workbook = Path(workbook)
    if engine(engine_name) == "excel":
        if sys.platform != "win32":
            raise RuntimeError("the Excel engine needs Windows with Excel; use MELANGE_XL_ENGINE=libreoffice")
        if receipt is None:
            raise ValueError("the Excel engine needs a receipt path")
        run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1), "-WorkbookPath", str(workbook),
             "-ExpectedSourceSha256", expected_sha256, "-ReceiptPath", str(receipt), "-CalculationMethod", method,
             "-CalculationTimeoutSeconds", str(timeout)], check=True)
        return workbook
    if out is None:
        raise ValueError("the LibreOffice engine writes a view copy: pass out=")
    out = Path(out)
    receipt = Path(receipt) if receipt else out.with_name(out.name + ".receipt.json")
    flags = ["--expect-sha", expected_sha256, "--timeout", str(timeout), "--policy", policy]
    for fn in allow_functions:
        flags += ["--allow-function", fn]
    if os.environ.get(LOCAL_ENV) == "1":
        configured_python = os.environ.get("MELANGE_OFFICE_PYTHON")
        interpreter = Path(configured_python) if configured_python else Path(sys.executable)
        if not interpreter.is_absolute() or not interpreter.is_file():
            raise RuntimeError("MELANGE_OFFICE_PYTHON must name an existing absolute interpreter path")
        # LibreOffice's private interpreter keeps UNO beside its launcher, outside
        # the normal Python search path. Add that exact configured directory only.
        launcher = ([str(interpreter), "-E", "-s", "-c",
                     "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv.pop(1),run_name='__main__')",
                     str(interpreter.parent)] if configured_python else [str(interpreter)])
        run([*launcher, str(HERE / "office_recalc.py"), "--in", str(workbook), "--out", str(out), "--receipt", str(receipt),
             *flags], check=True)
        return out
    image = os.environ.get(IMAGE_ENV, "")
    if "@sha256:" not in image:
        raise RuntimeError(f"set {IMAGE_ENV} to the office-calc image BY DIGEST (ops/compute/images.lock); tags are refused")
    if receipt.parent != out.parent:
        raise ValueError("the receipt must be written next to the view copy (one writable mount)")
    env = {**os.environ, "OFFICE_IN_DIR": str(workbook.parent.resolve()), "OFFICE_OUT_DIR": str(out.parent.resolve())}
    run(["docker", "compose", "-f", str(COMPOSE), "--profile", "office", "run", "--rm", "office-calc",
         "--in", f"/in/{workbook.name}", "--out", f"/out/{out.name}", "--receipt", f"/out/{receipt.name}", *flags],
        check=True, env=env)
    return out


# state: switch = implemented behind MELANGE_XL_ENGINE (default Excel, behaviour unchanged) | gate = receipt validator |
#        gate-open = needs the same change, NOT made here (Paul's per-change OK) | no-port = manual one-off that writes the
#        canonical workbook through COM; a LibreOffice engine only writes view copies, so no switch (see `replacement`) |
#        dead = no caller | docstring = mentions Excel only in text | mentat = outside this repo, not edited here
_ONE_OFF = ("manual one-off repair that edits the canonical workbook through COM (June-July 2026); if it is ever re-run: "
            "write the cells with the byte-preserving merge adapter on a copy, then recalc_copy(engine='libreoffice') for the view")
SITES = {
    "fec/recalculate_formula_cache_copy.ps1": {
        "state": "switch", "replacement": "xl_engine.recalc_copy(engine_name='libreoffice') -> ops/compute/office_recalc.py "
        "(same safety preflight, receipt isolated-libreoffice-formula-cache-recalculation-v1); the .ps1 stays the default"},
    "fec/audit/audit_formula_cache_freshness.py": {
        "state": "gate", "replacement": "validate_engine_receipt dispatches on the receipt schema; Excel receipts validate as before"},
    "fec/legislation/run_legislation_presync_audit.py": {
        "state": "gate", "replacement": "validate_formula_cache_freshness_observation branches on the receipt schema (2026-10-12, Paul's "
        "OK): Excel receipts keep their rules; LibreOffice receipts need two byte-identical calculated copies (deterministic runs "
        "produce them), two distinct receipt files, each copy different from the source, and the LibreOffice isolation fields "
        "(ops/tests/test_presync_engine_kinds.py). FORMULA_CACHE_EXPECTED_FORMULA_COUNT is the current workbook's 98,269. "
        "audit_legislation_downstream_readiness.py, run_legislation_final_regression.py and run_legislation_timeout_rollover.py "
        "consume its verdict and need nothing of their own"},
    "fec/legislation/run_greenlit_legislation_rank_shadow.py": {
        "state": "no-port", "replacement": "research shadow run on a copy (l.647-694: write shadow cells, CalculateFullRebuild, "
        "read ranks): openpyxl/merge write of the treatment cells, then recalc_copy(libreoffice), read the view copy"},
    "fec/legislation/apply_leg_stance.py": {"state": "no-port", "replacement": _ONE_OFF},
    "fec/engine/apply_weights.py": {"state": "no-port", "replacement": _ONE_OFF},
    "fec/engine/apply_raw_max_norm.py": {"state": "no-port", "replacement": _ONE_OFF},
    "fec/engine/apply_committee_fix.py": {"state": "no-port", "replacement": _ONE_OFF},
    "fec/engine/apply_alloc_cap.py": {"state": "no-port", "replacement": _ONE_OFF},
    "fec/engine/apply_committee_reweight.py": {"state": "no-port", "replacement": _ONE_OFF},
    "fec/engine/add_judiciary_seats.py": {"state": "docstring", "replacement": "text only (instructs a COM CalculateFull)"},
    "fec/stance/apply_press_dedup.py": {"state": "no-port", "replacement": _ONE_OFF},
    "fec/stance/apply_press_overrides.py": {"state": "no-port", "replacement": _ONE_OFF + "; never run (memory: press-fix incidents)"},
    "fec/stance/apply_stance_guard.py": {"state": "dead", "replacement": "sys.exit('SUPERSEDED') at line 4"},
    "fec/stance/sync_stance_clean.py": {"state": "dead", "replacement": "sys.exit('SUPERSEDED') at line 4"},
    "fec/surfaces/write_im_sheet.py": {"state": "no-port", "replacement": "adds the IM Allocation sheet with IF formulas on a C:\\Temp "
                                       "copy; docstring-only reference from fec/engine/build_im_allocation.py. " + _ONE_OFF},
    "fec/attic/write_congestion_sheet.py": {"state": "dead", "replacement": "attic, no caller"},
    "fec/attic/sync_stance.py": {"state": "dead", "replacement": "attic, no caller"},
    "(Mentat checkout) fec/apply_*.py, fec/write_im_sheet.py, fec/add_judiciary_seats.py, fec/sync_stance_clean.py, fec/attic/*.py": {
        "state": "mentat", "replacement": "legacy copies of the Melange one-offs above, kept in the Mentat checkout's fec/ (read-only "
        "here, not Mentat's own code): same disposition as their Melange twins, none on a recurring path"},
    "mentat/tools/finalize_crypto_excel.ps1": {
        "state": "mentat", "replacement": "authoring (bulk table rows, table-column formulas with structured references, resize/add "
        "tables) -> Python openpyxl builder (task M2-d); CalculateFullRebuild -> office_recalc.py --policy mentat; the read-back "
        "gates (HasFormula, every check column 'OK', cycle totals within 0.01) -> Python over office_recalc.read_cached_formula_values"},
    "mentat/tools/build_industry_report.ps1": {
        "state": "mentat", "replacement": "caller: sets MENTAT_DEFER_FORMULAS=1 and runs the finalizer (l.180, 493, 930); "
        "follows the finalizer port (M2-d)"},
}

# What marks a file as an Excel site: COM automation, an Excel-engine receipt schema, or the Excel isolation fields.
CENSUS_PATTERN = r"win32com|Excel\.Application|\.CalculateFull|msoAutomationSecurityForceDisable|isolated-excel-formula-cache"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sites")
    r = sub.add_parser("recalc")
    r.add_argument("--in", dest="src", required=True, type=Path)
    r.add_argument("--expect-sha", required=True)
    r.add_argument("--out", type=Path, help="view copy (LibreOffice engine)")
    r.add_argument("--receipt", type=Path)
    r.add_argument("--engine", choices=ENGINES, help=f"default: ${ENGINE_ENV}, else excel")
    r.add_argument("--method", choices=["CalculateFull", "CalculateFullRebuild"], default="CalculateFull")
    r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--policy", default="melange")
    r.add_argument("--allow-function", action="append", default=[])
    a = ap.parse_args(argv)
    if a.cmd == "sites":
        for site, v in SITES.items():
            print(f"{v['state']:<10}{site}")
            print(f"{'':<10}-> {v['replacement']}")
        return 0
    try:
        got = recalc_copy(a.src, expected_sha256=a.expect_sha, receipt=a.receipt, out=a.out, engine_name=a.engine,
                          method=a.method, timeout=a.timeout, policy=a.policy, allow_functions=a.allow_function)
    except subprocess.CalledProcessError as exc:                    # the engine said no: its own message is above, keep its code
        print(f"recalculation failed (exit {exc.returncode}): {exc.cmd[0]}", file=sys.stderr)
        return exc.returncode or 1
    except (ValueError, RuntimeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"engine": engine(a.engine), "result": str(got)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
