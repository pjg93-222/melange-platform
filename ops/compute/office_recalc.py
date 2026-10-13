#!/usr/bin/env python3
"""LibreOffice headless as a CALCULATOR for .xlsx workbooks (D-174; runs inside ops/docker/office-calc.Dockerfile).

    office_recalc.py --in book.xlsx --expect-sha <sha256 of book.xlsx> --out book.view.xlsx [--receipt r.json]

What it does, in order:
  1. refuses unless the input hashes to --expect-sha and passes a static OOXML safety preflight (a port of
     fec/recalculate_formula_cache_copy.ps1 Get-WorkbookSafety: allowed functions, no macros, links, connections, OLE);
  2. loads a COPY of the input in LibreOffice (macros off, links off, read-only, hidden, fresh /tmp profile), forces
     Excel semantics on the document, hard-recalculates (calculateAll) and reads the result of every formula cell;
     the document is closed WITHOUT storing;
  3. writes those results as cached values (<v>, t=) into a copy of the ORIGINAL package. Every other byte of the
     package is carried over unchanged. The output is a VIEW COPY: the input and the canonical workbook are never
     written (the output path may not be the input path);
  4. re-reads the output, checks it against the results, and writes the receipt
     isolated-libreoffice-formula-cache-recalculation-v1 (same shape as the Excel receipt where a field applies).

Why not openpyxl or `soffice --convert-to xlsx`: openpyxl cannot write a cached value next to a formula, and either
route rewrites the whole package (styles, tables, structured references, LET encoding). A direct edit of the cell
elements changes nothing but the <v>/t of formula cells, so the Excel-era static-semantics gate keeps holding.
Stdlib only, except `uno` (python3-uno) which is imported lazily by the LibreOffice step.

Exit codes: 0 ok | 2 refused (usage, hash, safety, array formulas) | 3 a result cannot be written as an Excel value
(LibreOffice-only Err:5xx) | 4 LibreOffice failed or is older than 24.8 | 5 timeout | 6 output failed its own verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "isolated-libreoffice-formula-cache-recalculation-v1"
STATUS = "PASS_TEMP_COPY_RECALCULATED"
OK, REFUSED, UNMAPPABLE, ENGINE, TIMEOUT, VERIFY = 0, 2, 3, 4, 5, 6
MIN_VERSION = (24, 8)                      # LET and XLOOKUP arrive in LibreOffice 24.8
MAX_PART = 2 << 30                         # one package part, uncompressed (zip-bomb cap)
MAX_TOTAL = 8 << 30

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCREL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Policy = the function allowlist of the Excel-era preflight (fec/recalculate_formula_cache_copy.ps1:25-44), extended
# per workbook family. Melange: the .ps1's list, kept in lockstep (test_office_recalc.py); AVERAGE ('Coalition Profiles'!C29) and
# TRUNC (PERCENTRANK form A, ops/compute/rewrite_percentrank.py) were added together on 2026-10-12.
# Mentat's list is the census of the formula literals in mentat/tools/finalize_crypto_excel.ps1 (2026-10-12; SEARCH is in
# the FEC Candidate URL column), plus SUM and IFERROR, which it builds by string interpolation.
POLICIES = {
    "melange": ("_XLFN.LET", "_XLFN.MINIFS", "ABS", "AVERAGE", "AVERAGEIF", "COUNTIF", "COUNTIFS", "IF", "IFERROR", "INDEX",
                "MATCH", "MAX", "MIN", "N", "PERCENTRANK", "RANK", "SUM", "SUMIFS", "SUMPRODUCT", "TRUNC"),
    "mentat": ("ABS", "AND", "COUNTIFS", "IF", "IFERROR", "ISNUMBER", "OR", "SEARCH", "SUM", "SUMIFS", "_XLFN.XLOOKUP"),
}
FORBIDDEN_PARTS = tuple(re.compile(p) for p in (
    r"^xl/vbaProject\.bin$", r"^xl/macrosheets/", r"^xl/dialogsheets/", r"^xl/externalLinks/", r"^xl/connections\.xml$",
    r"^xl/queryTables/", r"^xl/pivotCache/", r"^xl/model/", r"^xl/activeX/", r"^xl/embeddings/", r"^xl/oleObjects/",
    r"^customUI/"))
FUNC_RE = re.compile(r"(?i)(?<![A-Z0-9_.])([A-Z_][A-Z0-9_.]*)\s*\(")
EXT_REF_RES = (re.compile(r"(?i)\[[^\[\]]+\.(?:xlsx|xlsm|xlsb|xls|xlam|xla|csv)\][^!]*!"),
               re.compile(r"(?i)(?:https?|file)://"), re.compile(r"(?i)'[^']+'\|'[^']+'!"))
NAME_RISK_RE = re.compile(r"(?i)Auto_Open|Auto_Close|Workbook_Open")
DEFINITION_RISK_RE = re.compile(r"(?i)\b(?:CALL|EXEC|REGISTER\.ID)\s*\(")

# Byte-level cell editing. Cells are <c .../> or <c ...>...</c>; text content never holds a raw '<'.
CELL_RE = re.compile(rb"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.S)
F_RE = re.compile(rb"<f\b([^>]*?)(?:/>|>(.*?)</f>)", re.S)
V_RE = re.compile(rb"<v\b[^>]*?(?:/>|>.*?</v>)", re.S)
ATTR_RE = re.compile(rb'([\w:]+)="([^"]*)"')
T_ATTR_RE = re.compile(rb'\s+t="[^"]*"')
CELL_T = {"str": b"str", "b": b"b", "e": b"e"}

# LibreOffice FormulaError codes that mean exactly one Excel error. 502/504 (invalid argument) are #NUM! for some
# functions and #VALUE! for others in Excel, so they are not mapped: such a run fails instead of guessing.
ERR_MAP = {532: "#DIV/0!", 524: "#REF!", 525: "#NAME?", 32767: "#N/A", 519: "#VALUE!", 503: "#NUM!"}


class RecalcError(Exception):
    code = REFUSED


class Refused(RecalcError):
    code = REFUSED


class Unmappable(RecalcError):
    code = UNMAPPABLE


class EngineError(RecalcError):
    code = ENGINE


class RecalcTimeout(RecalcError):
    code = TIMEOUT


class VerifyError(RecalcError):
    code = VERIFY


def log(msg):
    print(f"[office_recalc {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def file_item(path):
    return {"path": str(path), "bytes": os.path.getsize(path), "sha256": sha256_file(path)}


def now(epoch=None):
    return datetime.fromtimestamp(float(epoch), timezone.utc) if epoch else datetime.now(timezone.utc)


def xml_text(raw: bytes) -> str:
    """Element/attribute text as written in the package (entities decoded), without a full parse in the common case."""
    if b"&" not in raw:
        return raw.decode("utf-8")
    return ET.fromstring(b"<x>" + raw + b"</x>").text or ""


def parse_attrs(raw: bytes) -> dict:
    return {k.decode(): v.decode("utf-8") for k, v in ATTR_RE.findall(raw)}


def a1_to_rc(addr: str):
    m = re.fullmatch(r"([A-Z]{1,3})([0-9]+)", addr)
    if not m:
        raise VerifyError(f"cell address is not A1 style: {addr!r}")
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return col - 1, int(m.group(2)) - 1


# ------------------------------------------------------------------------------------------ package reading

def check_zip_limits(zf):
    total = 0
    for info in zf.infolist():
        total += info.file_size
        if info.file_size > MAX_PART or total > MAX_TOTAL:
            raise Refused(f"package too large to process safely ({info.filename})")


def sheet_parts(zf):
    """[(sheet name, part name)] of the worksheets, in workbook order."""
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): (r.get("Target"), r.get("Type") or "") for r in rels.iter(f"{{{REL_NS}}}Relationship")}
    out = []
    for sh in wb.iter(f"{{{MAIN_NS}}}sheet"):
        tgt, typ = target[sh.get(f"{{{DOCREL_NS}}}id")]
        if not typ.endswith("/worksheet"):
            continue
        part = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
        out.append((sh.get("name"), part))
    return out


def scan_package(path, allowed):
    """(safety dict, {sheet: {"part", "addrs"}}). The safety dict mirrors the Excel receipt's safety_preflight."""
    allowed = {a.upper() for a in allowed}
    with zipfile.ZipFile(path) as zf:
        check_zip_limits(zf)
        names = zf.namelist()
        forbidden = sorted({n for n in names if any(p.search(n) for p in FORBIDDEN_PARTS)})
        external = []
        for n in names:
            if n.endswith(".rels"):
                for rel in ET.fromstring(zf.read(n)).iter(f"{{{REL_NS}}}Relationship"):
                    if rel.get("TargetMode") == "External":
                        external.append({"part": n, "type": rel.get("Type") or "", "target": rel.get("Target") or ""})
        unsafe_external = [r for r in external if not r["type"].endswith("/hyperlink")]
        functions, unsafe_formulas, extrefs = Counter(), [], []
        cells, formula_count, arrays = {}, 0, 0
        for name, part in sheet_parts(zf):
            data = zf.read(part)
            addrs = []
            for m in CELL_RE.finditer(data):
                inner = m.group(2)
                if not inner or b"<f" not in inner:
                    continue
                fm = F_RE.search(inner)
                if not fm:
                    continue
                formula_count += 1
                addr = parse_attrs(m.group(1)).get("r")
                if not addr:
                    raise Refused(f"{part}: a formula cell has no r= address")
                addrs.append(addr)
                if parse_attrs(fm.group(1)).get("t") == "array":
                    arrays += 1
                text = xml_text(fm.group(2)) if fm.group(2) else ""
                for fn in FUNC_RE.findall(text):
                    fn = fn.upper()
                    functions[fn] += 1
                    if fn not in allowed and len(unsafe_formulas) < 25:
                        unsafe_formulas.append({"part": part, "function": fn, "formula": text[:240]})
                if any(r.search(text) for r in EXT_REF_RES) and len(extrefs) < 25:
                    extrefs.append({"part": part, "formula": text[:240]})
            cells[name] = {"part": part, "addrs": addrs}
        risks = []
        for dn in ET.fromstring(zf.read("xl/workbook.xml")).iter(f"{{{MAIN_NS}}}definedName"):
            text = dn.text or ""
            if NAME_RISK_RE.search(dn.get("name") or "") or DEFINITION_RISK_RE.search(text):
                risks.append({"name": dn.get("name"), "definition": text})
    safe = not (forbidden or unsafe_external or unsafe_formulas or extrefs or risks or arrays)
    return {
        "safe_for_isolated_calculation": safe,
        "package_part_count": len(names),
        "formula_count": formula_count,
        "formula_functions": [{"function": k, "occurrences": v} for k, v in sorted(functions.items())],
        "allowed_external_hyperlinks": [r for r in external if r["type"].endswith("/hyperlink")],
        "forbidden_package_parts": forbidden,
        "unsafe_external_relationships": unsafe_external,
        "unsafe_formula_examples": unsafe_formulas,
        "external_formula_reference_examples": extrefs,
        "defined_name_runtime_risks": risks,
        # ponytail: array/spilled results are cells without an <f>; not injected, so refused (Melange: none)
        "array_formula_count": arrays,
    }, cells


def read_cached_formula_values(path):
    """{sheet: {addr: (kind, value, formula text)}} of every formula cell's cached value. kind: n | str | b | e | missing."""
    out = {}
    with zipfile.ZipFile(path) as zf:
        for name, part in sheet_parts(zf):
            rows = out[name] = {}
            for m in CELL_RE.finditer(zf.read(part)):
                inner = m.group(2)
                if not inner or b"<f" not in inner:
                    continue
                fm = F_RE.search(inner)
                if not fm:
                    continue
                attrs = parse_attrs(m.group(1))
                vm = re.search(rb"<v\b[^>]*?(?:/>|>(.*?)</v>)", inner, re.S)
                text = xml_text(vm.group(1) or b"") if vm else None
                t = attrs.get("t", "n")
                if text is None:
                    val = ("missing", None)
                elif t == "n" or t == "":
                    val = ("n", float(text)) if text.strip() else ("missing", None)
                elif t == "str":
                    val = ("str", unescape_x(text))
                elif t == "b":
                    val = ("b", text.strip() == "1")
                elif t == "e":
                    val = ("e", text)
                else:
                    raise VerifyError(f"formula cell {name}!{attrs.get('r')} has t={t!r}; only n/str/b/e are produced")
                rows[attrs.get("r")] = (*val, xml_text(fm.group(2)) if fm.group(2) else "")
    return out


# ------------------------------------------------------------------------------------------ injection

def fmt_number(x: float) -> str:
    if x == 0:
        return "0"
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return repr(float(x))


def escape_x(text: str) -> str:
    text = re.sub(r"_(x[0-9A-Fa-f]{4}_)", r"_x005F_\1", text)
    return re.sub("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", lambda m: "_x%04X_" % ord(m.group()), text)


def unescape_x(text: str) -> str:
    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), text)


def encode_value(kind, value) -> bytes:
    if kind == "n":
        return fmt_number(value).encode()
    if kind == "b":
        return b"1" if value else b"0"
    if kind == "e":
        if value not in ERR_MAP.values():
            raise Unmappable(f"not an Excel error value: {value!r}")
        return value.encode()
    return escape_x(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").encode("utf-8")


def inject_sheet(data: bytes, results: dict, seen: set) -> bytes:
    def repl(m):
        inner = m.group(2)
        if not inner or b"<f" not in inner:
            return m.group(0)
        fm = F_RE.search(inner)
        if not fm:
            return m.group(0)
        addr = parse_attrs(m.group(1)).get("r")
        if addr not in results:
            raise VerifyError(f"no calculated result for formula cell {addr}")
        seen.add(addr)
        kind, value = results[addr]
        attrs = T_ATTR_RE.sub(b"", m.group(1)) + (b' t="' + CELL_T[kind] + b'"' if kind in CELL_T else b"")
        body = inner[:fm.end()] + b"<v>" + encode_value(kind, value) + b"</v>" + V_RE.sub(b"", inner[fm.end():])
        return b"<c" + attrs + b">" + body + b"</c>"
    return CELL_RE.sub(repl, data)


def inject_values(src, dst, cells, results):
    """Copy the package src -> dst, replacing only the cached value of formula cells. Entry order, names, dates,
    compression types and attributes are kept, so two runs on one input are byte-identical."""
    part_sheet = {v["part"]: k for k, v in cells.items()}
    seen = {k: set() for k in cells}
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            sheet = part_sheet.get(info.filename)
            if sheet is not None:
                data = inject_sheet(data, results[sheet], seen[sheet])
            zi = zipfile.ZipInfo(info.filename, info.date_time)
            zi.compress_type, zi.external_attr, zi.create_system = info.compress_type, info.external_attr, info.create_system
            zout.writestr(zi, data, compress_type=info.compress_type)
    missing = {s: len(set(c["addrs"]) - seen[s]) for s, c in cells.items() if set(c["addrs"]) - seen[s]}
    if missing:
        raise VerifyError(f"formula cells not rewritten: {missing}")


def verify_output(src, dst, cells, results):
    """The view copy has every result, and every part that is not a worksheet equals the input's (CRC + size)."""
    changed = {v["part"] for v in cells.values()}
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
        ia, ib = {i.filename: i for i in a.infolist()}, {i.filename: i for i in b.infolist()}
        if list(ia) != list(ib):
            raise VerifyError("view copy has a different part list")
        for n, i in ia.items():
            if n not in changed and (i.CRC != ib[n].CRC or i.file_size != ib[n].file_size):
                raise VerifyError(f"unchanged part differs: {n}")
    back = read_cached_formula_values(dst)
    counts = Counter()
    for sheet, c in cells.items():
        for addr in c["addrs"]:
            kind, value = results[sheet][addr]
            got = back[sheet][addr]
            if got[0] != kind or (got[1] != value and not (kind == "n" and fmt_number(value) == fmt_number(got[1]))):
                raise VerifyError(f"{sheet}!{addr}: wrote {kind} {value!r}, read back {got[0]} {got[1]!r}")
            counts[kind] += 1
    return {"changed_part_count": len(changed), "unchanged_part_count": len(ia) - len(changed),
            "cached_by_type": dict(sorted(counts.items())), "cached_count": sum(counts.values())}


# ------------------------------------------------------------------------------------------ LibreOffice

def profile_xcu() -> str:
    """Fresh-profile registry: recalculation is requested through calculateAll, not on load; macros off; no OpenCL, no
    threaded formula groups (summation order stays fixed); en-US."""
    def item(path, name, value):
        return f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse"><value>{value}</value></prop></item>\n'
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            + item("/org.openoffice.Office.Calc/Formula/Load", "OOXMLRecalcMode", 1)
            + item("/org.openoffice.Office.Calc/Formula/Load", "ODFRecalcMode", 1)
            + item("/org.openoffice.Office.Calc/Formula/Calculation", "UseThreadedCalculationForFormulaGroups", "false")
            + item("/org.openoffice.Office.Common/Misc", "UseOpenCL", "false")
            + item("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", 3)
            + item("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "true")
            + item("/org.openoffice.Setup/L10N", "ooLocale", "en-US")
            + item("/org.openoffice.Setup/L10N", "ooSetupSystemLocale", "en-US")
            + "</oor:items>\n")


# Excel semantics on the document (set through SpreadsheetDocumentSettings; read back into the receipt).
DOC_SETTINGS = {"IsIterationEnabled": False, "Wildcards": True, "RegularExpressions": False, "MatchWholeCell": True,
                "IgnoreCase": True, "CalcAsShown": False, "LookUpLabels": False}
_SOFFICE_ARGS = ["--headless", "--invisible", "--nologo", "--norestore", "--nolockcheck", "--nodefault",
                 "--nofirststartwizard"]


def parse_version(text):
    m = re.search(r"LibreOffice\s+(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        raise EngineError(f"cannot read the LibreOffice version from {text!r}")
    return tuple(int(x) for x in m.groups(default="0"))


def libreoffice_calculate(copy, cells, *, timeout, work, soffice=None):
    """Load `copy` in LibreOffice, hard-recalculate, read every formula cell. Returns (results, engine facts).
    results[sheet][addr] = (kind, value); kind n | str | b | e."""
    soffice = soffice or os.environ.get("SOFFICE") or shutil.which("soffice")
    if not soffice:
        raise EngineError("soffice is not installed")
    profile = Path(work) / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(profile_xcu(), encoding="utf-8")
    env = {**os.environ, "HOME": str(work), "SAL_USE_VCLPLUGIN": "svp"}
    base = [soffice, f"-env:UserInstallation={profile.as_uri()}"]
    try:
        ver = subprocess.run([*base, "--version"], capture_output=True, text=True, timeout=120, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(f"cannot run {soffice}: {exc}") from exc
    version = parse_version(ver.stdout)
    if version[:2] < MIN_VERSION:
        raise EngineError(f"LibreOffice {'.'.join(map(str, version))} is older than 24.8 (LET, XLOOKUP)")
    pipe = f"office_recalc_{os.getpid()}"
    logfile = open(Path(work) / "soffice.log", "wb")
    proc = subprocess.Popen([*base, *_SOFFICE_ARGS, f"--accept=pipe,name={pipe};urp;"], stdin=subprocess.DEVNULL,
                            stdout=logfile, stderr=logfile, env=env, start_new_session=True)
    timed_out = threading.Event()

    def kill():
        try:
            if hasattr(os, "killpg"):                       # soffice.bin is a child of the soffice wrapper: take the group
                os.killpg(proc.pid, signal.SIGKILL)
            else:                                           # Windows: only the tests run the engine here (a fake soffice)
                proc.kill()
        except OSError:                                     # already gone (ProcessLookupError), or not ours (PermissionError)
            pass

    def expire():
        timed_out.set()
        kill()
    timer = threading.Timer(timeout, expire)
    timer.start()
    doc = None
    try:
        import uno
        from com.sun.star.beans import PropertyValue
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        no_connect = uno.getClass("com.sun.star.connection.NoConnectException")
        ctx = None
        while ctx is None:
            if timed_out.is_set():
                raise RecalcTimeout(f"LibreOffice did not accept a connection within {timeout} s")
            if proc.poll() is not None:
                raise EngineError("soffice exited before it accepted a connection: " + _tail(work))
            try:
                ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
            except no_connect:
                time.sleep(0.5)
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)

        def pv(name, value):
            return PropertyValue(Name=name, Value=value)
        props = (pv("Hidden", True), pv("ReadOnly", True), pv("MacroExecutionMode", uno.Any("short", 0)),
                 pv("UpdateDocMode", uno.Any("short", 0)))     # NEVER_EXECUTE, NO_UPDATE
        doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(str(copy)), "_blank", 0, props)
        if doc is None:
            raise EngineError("LibreOffice could not load the workbook")
        for k, v in DOC_SETTINGS.items():
            doc.setPropertyValue(k, v)
        settings = {k: doc.getPropertyValue(k) for k in DOC_SETTINGS}
        log("calculateAll")
        doc.calculateAll()
        results, bad = _read_results(doc, cells)
        if bad:
            raise Unmappable(f"{len(bad)} result(s) have no Excel equivalent, first: {bad[:10]}")
        doc.close(True)
        doc = None
        try:
            desktop.terminate()
        except Exception:                                   # noqa: BLE001  the process is killed below anyway
            pass
    except RecalcError:
        raise
    except Exception as exc:                                # noqa: BLE001  UNO raises its own exception classes
        if timed_out.is_set():
            raise RecalcTimeout(f"LibreOffice did not finish within {timeout} s") from exc
        raise EngineError(f"LibreOffice step failed: {type(exc).__name__}: {exc}") from exc
    finally:
        timer.cancel()
        kill()
        proc.wait()
        logfile.close()
    if timed_out.is_set():
        raise RecalcTimeout(f"LibreOffice did not finish within {timeout} s")
    return results, {
        "product": "LibreOffice", "version": ".".join(map(str, version)), "version_string": ver.stdout.strip(),
        "automation_security": "MacroExecutionMode.NEVER_EXECUTE", "update_links": 0, "enable_events": False,
        "display_alerts": False, "ask_to_update_links": False, "visible": False, "read_only": True,
        "load_recalc_mode": "never", "calculation_request": "calculateAll", "calculation_completed": True,
        "calculate_before_save": False, "document_stored": False, "threaded_formula_groups": False, "opencl": False,
        "document_settings": settings}


def _tail(work):
    try:
        return (Path(work) / "soffice.log").read_text(errors="replace")[-400:]
    except OSError:
        return ""


def _read_results(doc, cells):
    """ponytail: one UNO round trip per property per cell (~10-20k cells/s). Bulk getDataArray + queryFormulaCells is the
    upgrade if the Mentat-sized (1.0-1.3 M formula) timing in task X-2b needs it."""
    formats = doc.getNumberFormats()
    logical = {}
    results, bad = {}, []
    for sheet, c in cells.items():
        sh = doc.Sheets.getByName(sheet)
        out = results[sheet] = {}
        for addr in c["addrs"]:
            col, row = a1_to_rc(addr)
            cell = sh.getCellByPosition(col, row)
            err = cell.getError()
            if err:
                if err in ERR_MAP:
                    out[addr] = ("e", ERR_MAP[err])
                else:
                    bad.append(f"{sheet}!{addr} Err:{err}")
            elif cell.FormulaResultType2 == 2:              # com.sun.star.sheet.FormulaResult.STRING
                out[addr] = ("str", cell.getString())
            else:
                v = cell.getValue()
                if v in (0.0, 1.0):                         # a logical result is a number with a BOOLEAN number format
                    key = cell.NumberFormat
                    if key not in logical:
                        logical[key] = bool(formats.getByKey(key).Type & 1024)   # com.sun.star.util.NumberFormat.LOGICAL
                    if logical[key]:
                        out[addr] = ("b", v == 1.0)
                        continue
                out[addr] = ("n", v)
    return results, bad


# ------------------------------------------------------------------------------------------ orchestration

def run(src, expect_sha, out, receipt=None, *, timeout=1800, allow=(), policy="melange", soffice=None,
        calculator=libreoffice_calculate):
    """Returns the receipt dict. `calculator` is the seam the tests replace; it must return (results, engine facts)."""
    src, out = Path(src), Path(out)
    started = now(os.environ.get("SOURCE_DATE_EPOCH"))
    if not src.is_file() or src.suffix.lower() != ".xlsx":
        raise Refused(f"input must be an existing macro-free .xlsx: {src}")
    if out.suffix.lower() != ".xlsx" or not out.parent.is_dir():
        raise Refused(f"output must be an .xlsx in an existing directory: {out}")
    if out.exists() and os.path.samefile(src, out):
        raise Refused("output is the input: the canonical workbook is never overwritten")
    before = file_item(src)
    if before["sha256"] != expect_sha.lower():
        raise Refused(f"input hash does not match the expected source hash: {before['sha256']} != {expect_sha.lower()}")
    allowed = set(POLICIES[policy]) | {a.upper() for a in allow}
    safety, cells = scan_package(src, allowed)
    if not safety["safe_for_isolated_calculation"]:
        brief = {k: safety[k] for k in ("forbidden_package_parts", "unsafe_external_relationships", "unsafe_formula_examples",
                                       "external_formula_reference_examples", "defined_name_runtime_risks",
                                       "array_formula_count")}
        raise Refused("safety preflight failed closed; LibreOffice was not started: " + json.dumps(brief))
    log(f"{safety['formula_count']} formulas in {len(cells)} sheets; calculating")
    work = Path(tempfile.mkdtemp(prefix="office-calc-"))
    try:
        copy = work / "calc-copy.xlsx"
        shutil.copyfile(src, copy)
        results, engine = calculator(copy, cells, timeout=timeout, work=work / "lo", soffice=soffice)
        tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
        try:
            inject_values(src, tmp, cells, results)
            verified = verify_output(src, tmp, cells, results)
            os.replace(tmp, out)
        finally:
            if tmp.exists():
                tmp.unlink()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if file_item(src) != before:
        raise VerifyError("the input changed during the run")
    errors = sum(1 for s in results.values() for k, _ in s.values() if k == "e")
    doc = {
        "schema": SCHEMA, "status": STATUS, "generated_at": now(os.environ.get("SOURCE_DATE_EPOCH")).isoformat(),
        "started_at": started.isoformat(), "source_expected_sha256": expect_sha.lower(),
        "temporary_copy_before": before, "temporary_copy_after": file_item(out),
        "safety_preflight": safety, "engine": engine,
        "view_copy": True, "canonical_workbook_written": False, "input_unchanged": True,
        "formula_count": safety["formula_count"], "formula_cached_error_count": errors, **verified,
        "container": {"python": platform.python_version(), "platform": platform.platform()},
        "live_or_production_workbook_opened": False, "live_or_production_workbook_written": False,
        "macro_execution_allowed": False, "external_link_updates_allowed": False,
        "external_connections_allowed": False, "sync_or_promotion_performed": False}
    if receipt:
        r = Path(receipt)
        r.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", required=True, type=Path, help="workbook copy to calculate (never modified)")
    ap.add_argument("--expect-sha", required=True, help="sha256 the input must have")
    ap.add_argument("--out", required=True, type=Path, help="view copy to write")
    ap.add_argument("--receipt", type=Path, help="receipt path (default: <out>.receipt.json)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds for the LibreOffice step (30-86400)")
    ap.add_argument("--policy", choices=sorted(POLICIES), default="melange", help="allowed-function list")
    ap.add_argument("--allow-function", action="append", default=[], metavar="NAME",
                    help="extend the allowlist, e.g. _XLFN.XLOOKUP (repeatable)")
    ap.add_argument("--soffice", help="soffice binary (default: $SOFFICE or PATH)")
    a = ap.parse_args(argv)
    try:
        if not 30 <= a.timeout <= 86400 or not re.fullmatch(r"[0-9a-fA-F]{64}", a.expect_sha):
            raise Refused("--timeout must be 30-86400 and --expect-sha a 64-hex sha256")
        doc = run(a.src, a.expect_sha, a.out, a.receipt or a.out.with_name(a.out.name + ".receipt.json"),
                  timeout=a.timeout, allow=a.allow_function, policy=a.policy, soffice=a.soffice)
    except RecalcError as exc:
        print(json.dumps({"status": "FAILED", "exit_code": exc.code, "reason": str(exc)}), file=sys.stderr)
        return exc.code
    print(json.dumps({"status": doc["status"], "formula_count": doc["formula_count"], "cached_by_type": doc["cached_by_type"],
                      "engine": doc["engine"]["version_string"], "view_copy": str(a.out)}, sort_keys=True))
    return OK


if __name__ == "__main__":
    sys.exit(main())
