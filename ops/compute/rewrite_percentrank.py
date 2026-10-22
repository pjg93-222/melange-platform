#!/usr/bin/env python3
"""PERCENTRANK form A: PERCENTRANK(range,x,5) -> TRUNC(PERCENTRANK(range,x,15),5) in the workbook's XML (D-174).

    rewrite_percentrank.py book.xlsx                          # dry run (default): counts per sheet, range column, pattern
    rewrite_percentrank.py book.xlsx --apply                  # PRE-backup, then the workbook is replaced atomically
    rewrite_percentrank.py book.xlsx --apply --out new.xlsx   # PRE-backup, then a rewritten copy; book.xlsx is not replaced

Why: LibreOffice rounds the 5-digit result where Excel truncates (tdf#104981); Excel gives identical values for both
spellings (ops/compute/lo_probe_record_20261012.json, Excel oracle), and LibreOffice agrees with Excel on form A. The
IFERROR(PERCENTRANK(...),fallback) shape keeps its wrapper. Only the text of the matching <f> elements changes.

Package-level rewrite, stdlib only: every other byte of every other part is carried over unchanged (entry order, names,
dates, compression types kept), and the output is verified against the input part by part before it is put in place.
The dry run must find exactly EXPECTED (3,159) formulas in the old form and nothing PERCENTRANK-shaped it does not
recognise, else it refuses. A workbook that is already all form A is a no-op, so a second run changes nothing.
--apply also refuses while .pipeline_run.lock is live (fec/pipeline_lock.py's own liveness test), while the workbook is open
in Excel (an owner lock file ~$..., or the file cannot be opened exclusively), and if the PRE-backup, its manifest or --out
already exists. The PRE-backup is named like the existing ones (<stem>.PRE-<TAG>-YYYYMMDD_HHMMSS.xlsx, beside the workbook)
and has a sha256 manifest (<backup>.manifest.json) with the hash of every package part before and after.

Exit codes: 0 ok / nothing to do | 2 refused | 6 the output failed its own verification (nothing is put in place).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import office_recalc as OR  # noqa: E402

SCHEMA = "percentrank-form-a-rewrite-v1"
EXPECTED = 3159                    # 'Blended Model': 533 each over D, G, E, F, O; 494 IFERROR(...) over H. The census of the one-time D-174 migration at 533 members (frozen on purpose): the 494 is the members that have a centrality formula, so it is not a function of roster.count()
DEFAULT_TAG = "PERCENTRANK-TRUNC"
LOCK_NAME = ".pipeline_run.lock"
LOCK_AT = 1 << 30

_RANGE_X = rb"(\$[A-Z]{1,3}\$[0-9]+:\$[A-Z]{1,3}\$[0-9]+),([A-Z]{1,3}[0-9]+)"
OLD_CALL = re.compile(rb"PERCENTRANK\(" + _RANGE_X + rb",5\)")
NEW_CALL = re.compile(rb"TRUNC\(PERCENTRANK\(" + _RANGE_X + rb",15\),5\)")
IFERROR_SHAPE = re.compile(rb"IFERROR\(@,\$?[A-Z]{1,3}\$?[0-9]+\)")
F_ELEM = re.compile(rb"<f\b([^>]*)>([^<]*)</f>")        # formula text never holds a raw '<'


def shape(text, call):
    """'bare', 'iferror' or None for a formula that is exactly the call, or IFERROR(call,cell); plus the call's range column."""
    skeleton, n = call.subn(b"@", text)
    kind = "bare" if skeleton == b"@" else "iferror" if IFERROR_SHAPE.fullmatch(skeleton) else None
    if n != 1 or kind is None:
        return None, None
    return kind, re.match(rb"\$([A-Z]+)", call.search(text).group(1)).group(1).decode()


def rewrite_part(data):
    """-> (new bytes, old Counter[(column, shape)], new Counter, unrecognised [formula text], PERCENTRANK mentions inside <f>)."""
    old, new, other, inside = Counter(), Counter(), [], [0]

    def repl(m):
        attrs, text = m.group(1), m.group(2)
        mentions = text.upper().count(b"PERCENTRANK")
        if not mentions:
            return m.group(0)
        inside[0] += mentions
        kind, col = shape(text, OLD_CALL)
        if kind and not attrs.strip():
            old[(col, kind)] += 1
            return b"<f" + attrs + b">" + OLD_CALL.sub(rb"TRUNC(PERCENTRANK(\1,\2,15),5)", text) + b"</f>"
        kind, col = shape(text, NEW_CALL)
        if kind and not attrs.strip():
            new[(col, kind)] += 1
        else:
            other.append(text.decode("utf-8", "replace")[:160] + (f"  [attributes: {attrs.decode()}]" if attrs.strip() else ""))
        return m.group(0)
    return F_ELEM.sub(repl, data), old, new, other, inside[0]


def scan(path):
    """Read-only census of the package. Returns a dict; 'rewritten' maps part name -> new bytes for the parts that change."""
    old, new, other, mentions_total, rewritten, per_sheet = Counter(), Counter(), [], 0, {}, Counter()
    with zipfile.ZipFile(path) as zf:
        OR.check_zip_limits(zf)
        sheets = dict((part, name) for name, part in OR.sheet_parts(zf))
        inside = 0
        for info in zf.infolist():
            data = zf.read(info)
            mentions_total += data.upper().count(b"PERCENTRANK")
            if info.filename not in sheets:
                continue
            body, o, n, x, ins = rewrite_part(data)
            inside += ins
            for (col, kind), c in o.items():
                per_sheet[(sheets[info.filename], col, kind)] += c
            old.update(o)
            new.update(n)
            other += [f"{sheets[info.filename]}: {t}" for t in x]
            if body != data:
                rewritten[info.filename] = body
    return {"old": sum(old.values()), "already_form_a": sum(new.values()), "unrecognised": other,
            "outside_f_elements": mentions_total - inside, "by_sheet_column_shape": dict(sorted(per_sheet.items())),
            "rewritten": rewritten}


def decide(found):
    """'rewrite' | 'nothing' (already form A) | raises Refused. Exact counts or nothing."""
    if found["unrecognised"]:
        raise OR.Refused(f"{len(found['unrecognised'])} PERCENTRANK formula(s) in a shape this tool does not recognise, "
                         f"first: {found['unrecognised'][0]!r}")
    if found["old"] == EXPECTED and not found["already_form_a"]:
        return "rewrite"
    if found["old"] == 0 and found["already_form_a"] == EXPECTED:
        return "nothing"
    raise OR.Refused(f"expected exactly {EXPECTED} PERCENTRANK(...,5) formulas (or {EXPECTED} already in form A), found "
                     f"{found['old']} old and {found['already_form_a']} already form A")


# ------------------------------------------------------------------------------------------ preconditions

def pipeline_lock_is_live(lock):
    """fec/pipeline_lock.py's own test: a lease whose holder is not provably gone counts as live (it fails closed)."""
    lock = Path(lock)
    if not lock.exists():
        return False
    sys.path.insert(0, str(REPO / "fec"))
    try:
        import pipeline_lock as PL
        return not PL._stale_lock_may_be_removed(lock, PL._read_lock(lock))
    except Exception as exc:                     # noqa: BLE001  an unreadable verdict is not "not live"
        raise OR.Refused(f"cannot tell whether {lock} is live ({type(exc).__name__}: {exc})") from exc


def excel_owner_files(workbook):
    """Excel's ~$ owner files for this workbook (Excel drops up to two leading characters of a long name)."""
    name = workbook.name
    return [p for p in workbook.parent.glob("~$*") if len(p.name[2:]) >= len(name) - 3 and name.endswith(p.name[2:])]


def openable_exclusively(path):
    """Excel holds a workbook deny-write, so opening it for update fails; any other holder shows up as a lock. The Windows
    byte lock sits beyond the end of any workbook: it conflicts with other lockers and never blocks a reader."""
    try:
        with open(path, "r+b") as f:
            if os.name == "nt":
                import msvcrt
                f.seek(LOCK_AT)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                f.seek(LOCK_AT)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def require_writable_moment(workbook, lock):
    if pipeline_lock_is_live(lock):
        raise OR.Refused(f"{lock} is live: the pipeline owns the workbook")
    if excel_owner_files(workbook):
        raise OR.Refused(f"the workbook is open in Excel (owner file {excel_owner_files(workbook)[0].name})")
    if not openable_exclusively(workbook):
        raise OR.Refused("the workbook cannot be opened exclusively: another process has it open")


# ------------------------------------------------------------------------------------------ rewrite

def write_package(src, dst, rewritten):
    """Copy the package src -> dst; the parts in `rewritten` get their new bytes. Entry order, names, dates, compression
    types, attributes and the archive comment are kept."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        zout.comment = zin.comment
        for info in zin.infolist():
            zi = zipfile.ZipInfo(info.filename, info.date_time)
            zi.compress_type, zi.external_attr, zi.create_system, zi.comment = (
                info.compress_type, info.external_attr, info.create_system, info.comment)
            zout.writestr(zi, rewritten.get(info.filename, zin.read(info)), compress_type=info.compress_type)


def part_hashes(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: sha256(zf.read(i)).hexdigest() for i in zf.infolist()}


def strip_formulas(data):
    return F_ELEM.sub(lambda m: b"<f" + m.group(1) + b"></f>", data)


def verify(src, dst, rewritten):
    """dst has the same parts in the same order; every part outside `rewritten` equals the input's byte for byte; each
    rewritten part differs from the input only inside <f> elements; the census of dst is all form A."""
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
        if [i.filename for i in a.infolist()] != [i.filename for i in b.infolist()]:
            raise OR.VerifyError("output has a different part list")
        for info in a.infolist():
            was, now = a.read(info), b.read(info.filename)
            if info.filename not in rewritten:
                if was != now:
                    raise OR.VerifyError(f"unchanged part differs: {info.filename}")
            elif now == was or strip_formulas(was) != strip_formulas(now):
                raise OR.VerifyError(f"{info.filename} changed outside the <f> elements, or not at all")
    after = scan(dst)
    if after["old"] or after["unrecognised"] or after["already_form_a"] != EXPECTED or after["rewritten"]:
        raise OR.VerifyError(f"output census is not {EXPECTED} form A formulas: {after['old']} old, "
                             f"{after['already_form_a']} form A, {len(after['unrecognised'])} unrecognised")


def apply(workbook, *, out=None, tag=DEFAULT_TAG, lock=None, now=None):
    """Returns the manifest dict, or None when there is nothing to do (already form A). Raises RecalcError subclasses."""
    workbook = Path(workbook)
    lock = Path(lock) if lock else workbook.parent / LOCK_NAME
    if not re.fullmatch(r"[A-Z0-9-]+", tag):
        raise OR.Refused(f"--tag must be upper-case letters, digits and '-': {tag!r}")
    try:
        found = scan(workbook)
    except zipfile.BadZipFile as exc:
        raise OR.Refused(f"not a readable package: {exc}") from exc
    if decide(found) == "nothing":
        return None
    require_writable_moment(workbook, lock)
    target = Path(out) if out else workbook
    if out and (Path(out).suffix.lower() != ".xlsx" or Path(out).exists() or Path(out).resolve() == workbook.resolve()):
        raise OR.Refused(f"--out must be a new .xlsx path that does not exist: {out}")
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    backup = workbook.with_name(f"{workbook.stem}.PRE-{tag}-{stamp}{workbook.suffix}")
    manifest_path = backup.with_name(backup.name + ".manifest.json")
    if backup.exists() or manifest_path.exists():
        raise OR.Refused(f"PRE-backup already exists: {backup}")
    before = OR.file_item(workbook)
    shutil.copy2(workbook, backup)
    if OR.file_item(backup)["sha256"] != before["sha256"]:
        backup.unlink()
        raise OR.VerifyError("PRE-backup does not match the workbook it copies")
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        write_package(workbook, temp, found["rewritten"])
        verify(workbook, temp, found["rewritten"])
        if OR.file_item(workbook) != before:
            raise OR.VerifyError("the workbook changed during the run")
        hashes_before, hashes_after = part_hashes(workbook), part_hashes(temp)
        manifest = {
            "schema": SCHEMA, "generated_at": (now or datetime.now()).astimezone().isoformat(), "tag": tag,
            "rewritten_formula_count": found["old"],
            "by_sheet_column_shape": {"|".join(k): v for k, v in found["by_sheet_column_shape"].items()},
            "source": before, "backup": OR.file_item(backup), "output": OR.file_item(temp) | {"path": str(target)},
            "rewritten_parts": sorted(found["rewritten"]),
            "parts": {n: {"before": hashes_before[n], "after": hashes_after[n]} for n in hashes_before}}
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            require_writable_moment(workbook, lock)          # nothing took the workbook while the copy was being made
            if out and target.exists():
                raise OR.Refused(f"--out appeared while the rewrite ran: {target}")
            os.replace(temp, target)
        except (OSError, OR.Refused) as exc:
            manifest_path.unlink(missing_ok=True)
            if isinstance(exc, OR.Refused):
                raise
            raise OR.Refused(f"could not put the rewritten workbook in place: {exc}") from exc
    finally:
        temp.unlink(missing_ok=True)
    return manifest


# ------------------------------------------------------------------------------------------ CLI

def report(found, workbook, lock):
    print(f"{'sheet':<28}{'range col':<11}{'pattern':<9}{'cells':>6}")
    for (sheet, col, kind), n in found["by_sheet_column_shape"].items():
        print(f"{sheet:<28}{col:<11}{kind:<9}{n:>6}")
    print(f"to rewrite: {found['old']} (expected {EXPECTED}) | already form A: {found['already_form_a']} | "
          f"unrecognised PERCENTRANK formulas: {len(found['unrecognised'])} | mentions outside <f>: {found['outside_f_elements']}")
    for text in found["unrecognised"][:5]:
        print(f"  unrecognised: {text}")
    try:
        live = "LIVE" if pipeline_lock_is_live(lock) else "not live"
    except OR.Refused as exc:
        live = f"UNKNOWN ({exc})"
    opened = bool(excel_owner_files(workbook)) or not openable_exclusively(workbook)
    print(f"pipeline lock {lock}: {live} | workbook open: {'YES' if opened else 'no'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workbook", type=Path)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run, nothing is written)")
    ap.add_argument("--out", type=Path, help="write the rewritten workbook here instead of replacing the input (must not exist)")
    ap.add_argument("--tag", default=DEFAULT_TAG, help="PRE-backup tag (default %(default)s)")
    ap.add_argument("--lock", type=Path, help=f"pipeline lock file (default: {LOCK_NAME} beside the workbook)")
    a = ap.parse_args(argv)
    lock = a.lock or a.workbook.parent / LOCK_NAME
    try:
        if not a.workbook.is_file() or a.workbook.suffix.lower() != ".xlsx":
            raise OR.Refused(f"not an existing .xlsx: {a.workbook}")
        if not a.apply:
            if a.out:
                raise OR.Refused("--out needs --apply")
            found = scan(a.workbook)
            report(found, a.workbook, lock)
            verdict = decide(found)
            print("DRY RUN: nothing written. " + ("Already form A: nothing to do." if verdict == "nothing"
                                                  else "Re-run with --apply to rewrite."))
            return OR.OK
        manifest = apply(a.workbook, out=a.out, tag=a.tag, lock=lock)
    except OR.RecalcError as exc:
        print(json.dumps({"status": "REFUSED" if exc.code == OR.REFUSED else "FAILED", "exit_code": exc.code, "reason": str(exc)}),
              file=sys.stderr)
        return exc.code
    except zipfile.BadZipFile as exc:
        print(json.dumps({"status": "REFUSED", "exit_code": OR.REFUSED, "reason": f"not a readable package: {exc}"}), file=sys.stderr)
        return OR.REFUSED
    if manifest is None:
        print("already form A: nothing to do, nothing written")
    else:
        print(json.dumps({"status": "REWRITTEN", "formulas": manifest["rewritten_formula_count"], "output": manifest["output"]["path"],
                          "output_sha256": manifest["output"]["sha256"], "backup": manifest["backup"]["path"]}, sort_keys=True))
    return OR.OK


if __name__ == "__main__":
    sys.exit(main())
