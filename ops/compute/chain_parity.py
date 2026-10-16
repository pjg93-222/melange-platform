#!/usr/bin/env python3
"""Parity harness for the Melange chain (compute lane MEL): prove a container run of fec/run_pipeline.py
equals the Windows run, from frozen, byte-identical inputs.

Method (audits/deploy_20261011/COMPUTE_CONTAINERS_DESIGN.md section 4, the "3a chain" row; ruling D-177):
  A1, A2 = two Windows runs from byte-identical copies of the frozen inputs (A1 vs A2 is the noise floor);
  B      = the container run from a third identical copy;  C = an optional second container run.
Byte gates are exactly three artifacts in the tree root: ranks_python.json, dashboard_data.json,
bipartisan.json. Every other changed path is judged at content level (class table in `compare`).

Subcommands (each returns an int exit code from main(argv)):
  freeze    copy a read set into DIR and write its sha256 manifest      (refuses a non-empty dest)
  verify    prove a root is byte-identical to a manifest                (run before A1, A2 and B)
  snapshot  hash a tree to a manifest
  census    list the paths added, removed and changed between two manifests
  command   PRINT the exact run command for a side (executes only with --execute)
  compare   gates G0-G4 over frozen + A1 + A2 + B [+ C]
Any argument may be `@FILE` (one argument per line, # comments), e.g. `freeze --src <local> --dest D @ops/compute/parity_readset_local_extra.args`.
`command --side windows` is a relocated, clean-environment run: it refuses without --local-dir and --glassbox-dir, sets MELANGE_REBASE_STORED=1, and inherits only ENV_ALLOW.

Exit codes: 0 pass; 1 a gate failed (or verify/census found a difference); 2 usage or I/O error;
3 every gate passed but EOL_ONLY / PATH_ONLY / ZIP_CONTENT paths still need Paul's acceptance.

JSON_CANONICAL mask: the leaf VALUE paths that differ between A1 and A2 (never taken from B). A difference in
shape (key set, list length, scalar vs container) is never maskable, so such a path is DIFFERENT unless B equals A1.

Glob patterns (--include/--exclude): matched against the POSIX relative path with fnmatch (so `*` crosses
`/`); a pattern without `/` matches any single path segment (`__pycache__`, `*.pyc`). Run the roots with
PYTHONDONTWRITEBYTECODE=1 or pass --exclude __pycache__ to compare, or the .pyc files are DIFFERENT paths.

Stdlib only; openpyxl (via xlsx_content_diff, and compare_workbooks) is imported lazily and only when a workbook is classified.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BYTE_ARTIFACTS = ("ranks_python.json", "dashboard_data.json", "bipartisan.json")
NEEDS_ACCEPTANCE = ("EOL_ONLY", "PATH_ONLY", "ZIP_CONTENT")
SCORE_TOL = 1e-12
BLOCK = 1 << 20  # ponytail: whole-file hashing reads each file in 1 MiB blocks, one file at a time
COMPOSE_FILE = "ops/compute/melange.compose.yaml"
PARITY_COMPOSE_FILE = "ops/compute/melange.parity.compose.yaml"   # merged over COMPOSE_FILE when --audio-pilot-dir is given
REPO_ROOT = Path(__file__).resolve().parents[2]
# container env names passed through from the caller's environment, each overridable by a flag
PASSTHROUGH = (("MELANGE_DRAFT_DIR", "draft_dir"), ("MELANGE_DATA_DIR", "data_dir"),
               ("MELANGE_LOCAL_DIR", "local_dir"), ("MELANGE_GLASSBOX_DIR", "glassbox_dir"),
               ("MELANGE_AUDIO_PILOT_DIR", "audio_pilot_dir"), ("IMAGE_COMPUTE_MELANGE", "image"))
# The only names the Windows side inherits from the caller; everything else the chain reads (dials, keys, tokens) is absent.
ENV_ALLOW = ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATH", "PATHEXT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
             "TEMP", "TMP", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "ALLUSERSPROFILE", "PUBLIC",
             "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS", "USERNAME", "USERDOMAIN", "COMPUTERNAME")
sys.path.insert(0, str(Path(__file__).resolve().parent))   # xlsx_content_diff, imported lazily by classify
IMAGE_ENV, IMAGE_NAME = "IMAGE_COMPUTE_MELANGE", "compute-melange"


class Usage(Exception):
    """Bad arguments or unreadable input: main() prints it and returns 2."""


# --------------------------------------------------------------------------------------- hashing, trees

def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(BLOCK), b""):
            h.update(block)
    return h.hexdigest()


def _match(rel: str, pat: str) -> bool:
    pat = pat.replace("\\", "/")
    if "/" not in pat:
        return any(fnmatch.fnmatchcase(seg, pat) for seg in rel.split("/"))
    pat = pat.rstrip("/")
    return fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(rel, pat + "/*")


def excluded(rel: str, patterns) -> bool:
    return any(_match(rel, p) for p in patterns)


def walk_files(root, exclude=()) -> list[str]:
    """Sorted POSIX relative paths of every regular file under root (symlinked dirs and NTFS junctions are not entered:
    a run root links the read-only audio_pilot, and hashing 18 GB of the live directory would prove nothing)."""
    root = Path(root)
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames if not excluded(prefix + d, exclude)
                             and not os.path.islink(os.path.join(dirpath, d)) and not os.path.isjunction(os.path.join(dirpath, d)))
        out.extend(prefix + f for f in filenames if not excluded(prefix + f, exclude))
    return sorted(out)


def manifest_of(root, rels) -> dict:
    root = Path(root)
    return {rel: {"sha256": sha256_file(root / rel), "size": (root / rel).stat().st_size} for rel in sorted(rels)}


def snapshot_tree(root, exclude=()) -> dict:
    # ponytail: hashes every file under root on every call; compare hashes each root once
    return manifest_of(root, walk_files(root, exclude))


def diff_manifests(before: dict, after: dict) -> dict:
    b, a = set(before), set(after)
    return {"added": sorted(a - b), "removed": sorted(b - a),
            "changed": sorted(p for p in a & b if before[p]["sha256"] != after[p]["sha256"])}


def changed_paths(frozen: dict, post: dict) -> dict:
    d = diff_manifests(frozen, post)
    return {**{p: "added" for p in d["added"]}, **{p: "removed" for p in d["removed"]},
            **{p: "modified" for p in d["changed"]}}


def write_json(path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, sort_keys=True, indent=1)
        f.write("\n")


def load_manifest(path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Usage(f"cannot read manifest {path}: {e}") from e
    if not isinstance(doc, dict) or not all(
            isinstance(v, dict) and isinstance(v.get("sha256"), str) and isinstance(v.get("size"), int)
            for v in doc.values()):
        raise Usage(f"{path} is not a manifest ({{path: {{sha256, size}}}})")
    return doc


def _head(items, n=10) -> str:
    items = list(items)
    return ", ".join(map(str, items[:n])) + (f" (+{len(items) - n} more)" if len(items) > n else "")


# ------------------------------------------------------------------------------------------ json diffing

def parse_json_bytes(data: bytes):
    """-> ("json", doc) | ("ndjson", [docs]) | None.  ponytail: loads the whole document into memory."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    try:
        return "json", json.loads(text)
    except (ValueError, RecursionError):
        pass
    docs = []
    for line in text.split("\n"):  # not splitlines(): U+2028 may sit inside a JSON string
        if line.strip():
            try:
                docs.append(json.loads(line))
            except (ValueError, RecursionError):
                return None
    return ("ndjson", docs) if docs else None


def json_diff(a, b, path=()):
    """Yield (path, kind) per difference. kind "value": two scalars differ (type-exact, so 1 != 1.0 != true);
    kind "structure": key set, list length or scalar-vs-container differs (never maskable)."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                yield path + (k,), "structure"
            else:
                yield from json_diff(a[k], b[k], path + (k,))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield path, "structure"
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                yield from json_diff(x, y, path + (i,))
    elif isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        yield path, "structure"
    elif json.dumps(a) != json.dumps(b):
        yield path, "value"


def json_diffs(a, b) -> list:
    # C-speed canonical dump first: the recursive walk only runs when something really differs
    if json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True):
        return []
    return list(json_diff(a, b))


def pointer(path) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def _mask(ctx, rel) -> set:
    """The mask for rel = value paths that differ between A1 and A2 - derived ONLY from those two, never from B."""
    if rel not in ctx["mask_sets"]:
        e1, e2 = ctx["snaps"]["A1"].get(rel), ctx["snaps"]["A2"].get(rel)
        mask: set = set()
        if e1 and e2 and e1["sha256"] != e2["sha256"]:
            ja = parse_json_bytes((Path(ctx["roots"]["A1"]) / rel).read_bytes())
            jp = parse_json_bytes((Path(ctx["roots"]["A2"]) / rel).read_bytes())
            if ja and jp and ja[0] == jp[0]:
                mask = {p for p, kind in json_diffs(ja[1], jp[1]) if kind == "value"}
        ctx["mask_sets"][rel] = mask
        if mask:
            ctx["mask_report"][rel] = sorted(pointer(p) for p in mask)
    return ctx["mask_sets"][rel]


# ----------------------------------------------------------------------------------- workbook comparator

def _cell_sig(c) -> tuple:
    v = c.value
    if isinstance(v, (bool, int, float, str)):
        body = repr(v)
    elif hasattr(v, "isoformat"):
        body = v.isoformat()
    else:
        try:
            body = repr(sorted(vars(v).items()))  # ArrayFormula / DataTableFormula carry ref + text
        except TypeError:
            body = repr(v)
    return (c.data_type, type(v).__name__, body)


def _grid(ws) -> dict:
    # ponytail: iter_rows walks the whole used rectangle of each sheet; a huge sparse sheet is slow
    return {c.coordinate: _cell_sig(c) for row in ws.iter_rows() for c in row if c.value is not None}


def _structure(wb) -> dict:
    return {
        "sheet names (in order)": list(wb.sheetnames),
        "defined names": sorted((n, d.attr_text) for n, d in wb.defined_names.items()),
        **{f"{ws.title}: merged ranges": sorted(str(r) for r in ws.merged_cells.ranges) for ws in wb},
        **{f"{ws.title}: sheet-scope names": sorted((n, d.attr_text) for n, d in ws.defined_names.items()) for ws in wb},
        **{f"{ws.title}: tables": sorted(ws.tables.items()) for ws in wb},
        **{f"{ws.title}: data validation count": len(ws.data_validations.dataValidation) for ws in wb},
    }


def compare_workbooks(path_a, path_b, max_cells: int = 20) -> dict:
    """Content-level equality of two .xlsx/.xlsm files. Returns
    {"equal", "differences": [str], "cell_diff_count", "cell_diffs": [{"sheet", "cell", "mode", "a", "b"}]}
    with at most max_cells cells listed.

    Equal iff: same sheet names in the same order; per sheet the same cell grid (coordinate -> data type,
    value type, value or formula text) loaded both with data_only=False (formulas) and data_only=True
    (cached values); same merged ranges, defined names (workbook and sheet scope), table names/refs and
    data-validation count. IGNORES docProps (created/modified), zip member order and ZipInfo metadata,
    and calcChain: openpyxl does not expose them.

    ponytail: styles, number formats, column widths, conditional formats, charts and images are NOT
    compared, and each file is loaded fully twice. If ops/compute/xlsx_content_diff.py later exists it
    supersedes this comparator (do not depend on it from here).
    """
    import openpyxl  # lazy: the module imports without openpyxl; only workbook comparison needs it

    diffs: list[str] = []
    cells: list[dict] = []
    n_cells = 0
    struct = {}
    grids: dict[str, dict] = {}
    for mode, data_only in (("formula", False), ("value", True)):
        for side, path in (("a", path_a), ("b", path_b)):
            wb = openpyxl.load_workbook(path, data_only=data_only)
            try:
                if not data_only:
                    struct[side] = _structure(wb)
                grids[(mode, side)] = {ws.title: _grid(ws) for ws in wb}
            finally:
                wb.close()
    for key in sorted(set(struct["a"]) | set(struct["b"])):
        if struct["a"].get(key) != struct["b"].get(key):
            diffs.append(f"{key} differ: {struct['a'].get(key)!r} vs {struct['b'].get(key)!r}")
    for mode in ("formula", "value"):
        ga, gb = grids[(mode, "a")], grids[(mode, "b")]
        for sheet in (s for s in ga if s in gb):
            for coord in sorted(set(ga[sheet]) | set(gb[sheet])):
                sa, sb = ga[sheet].get(coord), gb[sheet].get(coord)
                if sa != sb:
                    n_cells += 1
                    if len(cells) < max_cells:
                        cells.append({"sheet": sheet, "cell": coord, "mode": mode,
                                      "a": ":".join(sa) if sa else None, "b": ":".join(sb) if sb else None})
    return {"equal": not diffs and not n_cells, "differences": diffs, "cell_diff_count": n_cells, "cell_diffs": cells}


# ------------------------------------------------------------------------------ zip and path comparators

def zip_members(path):
    """Sorted [(member name, sha256 of decompressed bytes)], or None if not a readable zip."""
    try:
        out = []
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                h = hashlib.sha256()
                with z.open(info) as f:
                    for block in iter(lambda: f.read(BLOCK), b""):  # ponytail: no zip-bomb cap
                        h.update(block)
                out.append((info.filename, h.hexdigest()))
        return sorted(out)
    except (zipfile.BadZipFile, OSError):
        return None


def parse_maps(items) -> list[tuple[str, str]]:
    maps = []
    for item in items:
        a, sep, b = item.partition("=")
        a, b = a.rstrip("\\/") or a, b.rstrip("\\/") or b
        if not sep or not a or not b:
            raise Usage(f"--map expects WINDOWS_PREFIX=CONTAINER_PREFIX, got {item!r}")
        maps.append((a, b))
    return maps


def _prefix_re(prefix: str):
    variants = set()
    for v in (prefix, prefix.replace("\\", "/"), prefix.replace("/", "\\")):
        variants |= {v, v.replace("\\", "\\\\")}  # the second form is the JSON-escaped spelling
    alt = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
    return re.compile(r"(?<![\w.\-/\\:])(?:" + alt + r")(?![\w.\-])"
                      r"(?P<tail>(?:[\\/]+[^\s\"'<>|*?,;()\[\]{}\\/:]+)*[\\/]*)")


def normalise_paths(data: bytes, maps, side: int) -> str:
    """Replace each mapped root prefix (either slash style, raw or JSON-escaped) with a token and make the
    path tail after it slash-uniform. side 0 = the Windows prefix, 1 = the container prefix.
    ponytail: regex text rewrite, not a parser; a stray `\\n` right after a path is also read as a segment."""
    text = data.decode("utf-8", "surrogateescape")
    for i, pair in enumerate(maps):
        text = _prefix_re(pair[side]).sub(lambda m, i=i: f"<MAP{i}>" + re.sub(r"[\\/]+", "/", m.group("tail")), text)
    return text


# ------------------------------------------------------------------------------------------ rank delta

def load_ranks(path) -> dict:
    try:
        doc = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path}: {e}") from e
    if not (isinstance(doc, dict) and isinstance(doc.get("rank"), dict) and isinstance(doc.get("score"), dict)):
        raise ValueError(f"{path}: expected {{'score': {{member: float}}, 'rank': {{member: int}}}}")
    for key in ("rank", "score"):
        for m, v in doc[key].items():
            if isinstance(v, bool) or not (isinstance(v, (int, float)) or (key == "score" and v is None)):
                raise ValueError(f"{path}: {key}[{m!r}] is not a number")
    return doc


def _score_moved(x, y, tol) -> bool:
    if x is None or y is None:
        return (x is None) != (y is None)
    return x != y and not abs(x - y) <= tol  # `not <=` so NaN counts as a delta


def rank_delta(a: dict, b: dict, tol: float = SCORE_TOL) -> dict:
    """Import-free reimplementation of rank_delta.py: members added/dropped, rank deltas, score deltas > tol."""
    ra, rb, sa, sb = a["rank"], b["rank"], a["score"], b["score"]
    added, dropped = sorted(set(rb) - set(ra)), sorted(set(ra) - set(rb))
    common = sorted(set(ra) & set(rb))
    rank_moved = sorted(({"member": m, "before": ra[m], "after": rb[m], "delta": rb[m] - ra[m]}
                         for m in common if ra[m] != rb[m]), key=lambda d: (-abs(d["delta"]), d["member"]))
    score_moved = [{"member": m, "before": sa.get(m), "after": sb.get(m)}
                   for m in common if _score_moved(sa.get(m), sb.get(m), tol)]
    return {"members_a": len(ra), "members_b": len(rb), "common": len(common), "added": added, "dropped": dropped,
            "rank_movers": rank_moved, "score_movers": score_moved,
            "zero": not (added or dropped or rank_moved or score_moved)}


# ------------------------------------------------------------------------------------------------ gates

def _gate(name, title, problems, notes=(), data=None) -> dict:
    return {"name": name, "title": title, "status": "FAIL" if problems else "PASS",
            "detail": list(problems) + list(notes), "data": data or {}}


def gate0(ctx) -> dict:
    p = []
    if not ctx["frozen"]:
        p.append("the frozen manifest is empty (nothing was frozen)")
    real = {lbl: os.path.normcase(os.path.realpath(r)) for lbl, r in ctx["roots"].items()}
    if len(set(real.values())) != len(real):
        p.append(f"roots are not distinct directories: {real}")
    for lbl, ch in ctx["changed"].items():
        if not ch:
            p.append(f"{lbl}: no path changed versus the frozen manifest (nothing ran)")
    a1, a2 = set(ctx["changed"]["A1"]), set(ctx["changed"]["A2"])
    if a1 != a2:
        p.append(f"noise floor: A1 and A2 changed different path sets; only A1: {_head(sorted(a1 - a2))}; "
                 f"only A2: {_head(sorted(a2 - a1))}")
    return _gate("G0", "preconditions: frozen non-empty, roots distinct, every run changed paths, A1/A2 path sets equal", p,
                 data={"changed_counts": {l: len(c) for l, c in ctx["changed"].items()}})


def gate1(ctx) -> dict:
    p, data = [], {}
    a1 = set(ctx["changed"]["A1"])
    for lbl in ctx["others"]:
        other = set(ctx["changed"][lbl])
        only_a, only_o = sorted(a1 - other), sorted(other - a1)
        data[lbl] = {"only_A1": only_a, f"only_{lbl}": only_o}
        if only_a:
            p.append(f"{len(only_a)} path(s) changed on A1 but not on {lbl}: {_head(only_a)}")
        if only_o:
            p.append(f"{len(only_o)} path(s) changed on {lbl} but not on A1: {_head(only_o)}")
    return _gate("G1", "changed-path sets of A1 and B (and C) are equal", p, data=data)


def gate2(ctx) -> dict:
    p, notes, table = [], [], {}
    for art in BYTE_ARTIFACTS:
        ent = {lbl: ctx["snaps"][lbl].get(art) for lbl in ctx["roots"]}
        table[art] = {lbl: e["sha256"] if e else None for lbl, e in ent.items()}
        missing = [lbl for lbl, e in ent.items() if e is None]
        if missing:
            p.append(f"{art}: missing in {', '.join(missing)} (a missing byte artifact is a failure, never a skip)")
            continue
        empty = [lbl for lbl, e in ent.items() if e["size"] == 0]
        if empty:
            p.append(f"{art}: empty in {', '.join(empty)} (an empty file proves nothing)")
        sha = table[art]
        if sha["A1"] != sha["A2"]:
            p.append(f"{art}: noise floor violated: A1 != A2 ({sha['A1'][:12]} vs {sha['A2'][:12]}); "
                     f"no mask can rescue a byte gate")
        for lbl in ctx["others"]:
            if sha[lbl] != sha["A1"]:
                p.append(f"{art}: {lbl} != A1 ({sha[lbl][:12]} vs {sha['A1'][:12]})")
        stale = [lbl for lbl in ctx["roots"] if art not in ctx["changed"][lbl]]
        if stale:
            notes.append(f"note: {art} is byte-identical to the frozen copy on {', '.join(stale)}; "
                         f"the equality above does not show that the run rewrote it")
    return _gate("G2", "byte gates: ranks_python.json, dashboard_data.json, bipartisan.json (A1==A2, B==A1)", p, notes,
                 data={"sha256": table})


def gate3(ctx, expect: int) -> dict:
    p, docs, movers = [], {}, {}
    for lbl, root in ctx["roots"].items():
        try:
            docs[lbl] = load_ranks(Path(root) / "ranks_python.json")
        except ValueError as e:
            p.append(f"{lbl}: {e}")
            continue
        n = len(docs[lbl]["rank"])
        if n != expect:
            p.append(f"{lbl}: {n} members, expected {expect}")
    for lbl in (l for l in ctx["roots"] if l != "A1"):
        if "A1" in docs and lbl in docs:
            d = movers[f"A1_vs_{lbl}"] = rank_delta(docs["A1"], docs[lbl])
            if not d["zero"]:
                p.append(f"A1 vs {lbl}: {len(d['added'])} added, {len(d['dropped'])} dropped, "
                         f"{len(d['rank_movers'])} rank delta(s), {len(d['score_movers'])} score delta(s) > {SCORE_TOL:g}; "
                         f"top movers: {_head(f"{m['member']} {m['delta']:+g}" for m in d['rank_movers'][:5])}")
    return _gate("G3", f"rank delta: zero added/dropped/rank/score deltas, {expect} members", p, data={"movers": movers})


def classify(rel: str, ctx, lbl: str) -> tuple[str, str]:
    sa, sb = ctx["snaps"]["A1"].get(rel), ctx["snaps"][lbl].get(rel)
    if sa is None and sb is None:
        return "BYTE_EQUAL", "absent on both sides"
    if sa is None or sb is None:
        return "DIFFERENT", f"present only on {lbl if sa is None else 'A1'}"
    if sa["sha256"] == sb["sha256"]:
        return "BYTE_EQUAL", ""
    pa, pb = Path(ctx["roots"]["A1"]) / rel, Path(ctx["roots"][lbl]) / rel
    da, db = pa.read_bytes(), pb.read_bytes()
    if da.replace(b"\r\n", b"\n") == db.replace(b"\r\n", b"\n"):
        return "EOL_ONLY", "equal after CRLF -> LF"
    note = ""
    if all(d[:64].lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"[") for d in (da, db)) \
            or rel.lower().endswith((".json", ".jsonl", ".ndjson", ".geojson")):
        ja, jb = parse_json_bytes(da), parse_json_bytes(db)
        if ja and jb and ja[0] == jb[0]:
            diffs = json_diffs(ja[1], jb[1])
            mask = _mask(ctx, rel)
            bad = [pth for pth, kind in diffs if kind != "value" or pth not in mask]
            if not bad:
                return "JSON_CANONICAL", f"{len(diffs)} differing value(s), all inside the A1/A2 mask ({len(mask)} path(s))"
            note = f"JSON differs at {len(bad)} path(s) outside the A1/A2 mask: {_head(pointer(x) or '/' for x in bad)}"
    if rel.lower().endswith((".xlsx", ".xlsm")):
        try:
            import xlsx_content_diff as XD  # D-177: content, not bytes; D-178 makes artifact-tool vs openpyxl authors differ in bytes
            res = XD.compare_files(str(pa), str(pb), examples=3, styles=False)
        except ImportError:
            note = "openpyxl is not installed; workbook content was not compared"
        except Exception as e:  # noqa: BLE001 - the reader raises many types on a corrupt package
            note = f"workbook unreadable: {type(e).__name__}: {e}"
        else:
            if XD.verdict(res, gate_cached=True) == "IDENTICAL":
                return "OOXML_CANONICAL", "workbooks equal (content and cached values; styles not gated)"
            gated = XD.CONTENT_KINDS + XD.CACHED_KINDS
            note = ("workbook differs: " + ", ".join(f"{k}={n}" for k, n in sorted(res["counts"].items()) if n and k in gated)
                    + "; e.g. " + _head((x for k in gated for x in res["examples"].get(k, ())), 5))
    za = zip_members(pa)
    if za is not None and za == zip_members(pb):
        return "ZIP_CONTENT", "same member names and decompressed bytes; zip metadata differs"
    if ctx["maps"] and normalise_paths(da, ctx["maps"], 0) == normalise_paths(db, ctx["maps"], 1):
        return "PATH_ONLY", "equal after replacing the mapped root prefixes"
    return "DIFFERENT", note or f"bytes differ (A1 {sa['size']} B, {lbl} {sb['size']} B)"


def gate4(ctx) -> dict:
    ctx["classes"], p = {}, []
    for lbl in ctx["others"]:
        paths = sorted((set(ctx["changed"]["A1"]) | set(ctx["changed"][lbl])) - set(BYTE_ARTIFACTS))
        ctx["classes"][lbl] = {rel: classify(rel, ctx, lbl) for rel in paths}
        bad = [rel for rel, (cls, _) in ctx["classes"][lbl].items() if cls == "DIFFERENT"]
        if bad:
            p.append(f"{len(bad)} path(s) DIFFERENT on {lbl}: " + "; ".join(
                f"{rel} ({ctx['classes'][lbl][rel][1]})" for rel in bad[:10]) + (f" (+{len(bad) - 10} more)" if len(bad) > 10 else ""))
    counts = {lbl: {} for lbl in ctx["classes"]}
    for lbl, table in ctx["classes"].items():
        for cls, _ in table.values():
            counts[lbl][cls] = counts[lbl].get(cls, 0) + 1
    return _gate("G4", "every other changed path classified; none DIFFERENT", p,
                 [f"{lbl}: " + (", ".join(f"{k}={v}" for k, v in sorted(c.items())) or "no paths") for lbl, c in counts.items()],
                 data={"class_counts": counts})


# ---------------------------------------------------------------------------------------------- commands

def cmd_freeze(a) -> int:
    src, dest = Path(a.src).resolve(), Path(a.dest).resolve()
    if not src.is_dir():
        raise Usage(f"--src {a.src!r} is not a directory")
    if dest == src or src in dest.parents or dest in src.parents:
        raise Usage("--dest and --src must not contain each other")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise Usage(f"refusing to overwrite: {dest} exists and is not empty")
    manifest = Path(a.manifest) if a.manifest else dest.with_name(dest.name + ".manifest.json")
    if manifest.exists():
        raise Usage(f"refusing to overwrite the existing manifest {manifest}")
    if dest in manifest.resolve().parents:
        raise Usage("the manifest must live outside --dest")
    excl, rels = a.exclude or [], set()
    for listing in a.list or []:
        for raw in Path(listing).read_text(encoding="utf-8").splitlines():
            line = raw.split("  #")[0].strip().replace("\\", "/")  # "path  # note" is allowed (ops/required_inputs.txt)
            if not line or line.startswith("#"):
                continue
            if line.startswith("/") or re.match(r"^[A-Za-z]:", line) or ".." in line.split("/"):
                raise Usage(f"--list entry must be a relative path inside --src: {line!r}")
            if line.endswith("/"):  # "dir/" or "glob*/": every file under each matching directory; none matching fails
                dirs = sorted(d for d in src.glob(line.rstrip("/")) if d.is_dir() and not d.is_symlink() and not os.path.isjunction(d))
                if not dirs:
                    raise Usage(f"--list directory entry matches no directory under --src: {line!r}")
                for d in dirs:
                    base = d.relative_to(src).as_posix() + "/"
                    rels |= {base + r for r in walk_files(d) if not excluded(base + r, excl)}
                continue
            if not (src / line).is_file():
                raise Usage(f"--list entry is not a file under --src: {line!r}")
            if not excluded(line, excl):
                rels.add(line)
    if a.include:
        # ponytail: --include globs walk the whole src tree; use --list for a traced read set
        rels |= {r for r in walk_files(src, excl) if excluded(r, a.include)}
    elif not a.list:
        raise Usage("give --include GLOB... and/or --list FILE")
    if not rels:
        raise Usage("the read set is empty (no --include/--list entry matched a file)")
    for rel in sorted(rels):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dest / rel)  # preserves mtimes (directory mtimes are not preserved)
    man = manifest_of(dest, rels)  # hashed from the copy: the manifest describes what is in dest
    write_json(manifest, man)
    print(f"froze {len(man)} file(s), {sum(e['size'] for e in man.values())} byte(s) -> {dest}; manifest {manifest}")
    return 0


def cmd_verify(a) -> int:
    if not os.path.isdir(a.root):
        raise Usage(f"--root {a.root!r} is not a directory")
    d = diff_manifests(load_manifest(a.manifest), snapshot_tree(a.root, a.exclude or []))
    for key, word in (("added", "extra"), ("removed", "missing"), ("changed", "modified")):
        for rel in d[key]:
            print(f"{word}: {rel}")
    if any(d.values()):
        print(f"FAIL: {a.root} is not byte-identical to {a.manifest}")
        return 1
    print(f"OK: {a.root} is byte-identical to {a.manifest}")
    return 0


def cmd_snapshot(a) -> int:
    if not os.path.isdir(a.root):
        raise Usage(f"--root {a.root!r} is not a directory")
    man = snapshot_tree(a.root, a.exclude or [])
    write_json(a.out, man)
    print(f"snapshot: {len(man)} file(s) -> {a.out}")
    return 0


def cmd_census(a) -> int:
    d = diff_manifests(load_manifest(a.before), load_manifest(a.after))
    for key, mark in (("added", "+"), ("removed", "-"), ("changed", "~")):
        for rel in d[key]:
            print(f"{mark} {rel}")
    print(f"added {len(d['added'])}, removed {len(d['removed'])}, changed {len(d['changed'])}")
    return 0


def current_image(name: str = IMAGE_NAME) -> str:
    """The image the laptop's verified current release manifest names (ops/RELEASE.md, "Compute jobs follow the manifest").
    No manifest, a bad signature or no such image is a refusal here, never a guess: the container run does not start."""
    done = subprocess.run([sys.executable, str(REPO_ROOT / "ops" / "release_lib" / "current.py"), "image", name],
                          capture_output=True, text=True, timeout=60)
    image = done.stdout.strip()
    if done.returncode != 0 or not image:
        raise Usage(f"no verified image for {name}: not running ({done.stderr.strip() or 'current.py printed nothing'}). "
                    f"To run a build that is in no signed release yet (the first parity runs), pass --image or set {IMAGE_ENV} to its digest.")
    return image


def require_scratch_dirs(local_dir, glassbox_dir) -> str:
    """The guard every relocated Windows run shares (the chain here, the collectors replay in collectors_replay.py): the machine-local
    store must be a COPY whose folder is named Melange, never the live one, and the Glass Box folder must be named explicitly.
    -> the absolute local dir."""
    if not local_dir:
        raise Usage("--local-dir is required for the windows side: without it the chain writes the live machine-local Melange store")
    local = os.path.abspath(local_dir)
    # LOCALAPPDATA becomes local's parent, and staffer_contacts / staffer_source_catalog / forecast_ledger / refresh_billstatus_bulk
    # read LOCALAPPDATA\Melange directly: any other leaf name sends them to a sibling of the copy (the live store, if that is it).
    live = os.path.join(os.environ.get("LOCALAPPDATA") or "", "Melange")
    if os.path.basename(local).lower() != "melange" or os.path.normcase(local) == os.path.normcase(os.path.abspath(live)):
        raise Usage("--local-dir must be a COPY of the local store whose folder is named Melange, not the live %s" % live)
    if not glassbox_dir:
        raise Usage("--glassbox-dir is required for the windows side: without it the chain writes events into the live ~/.claude/glassbox/events")
    live_events = os.path.normcase(os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "glassbox", "events")))
    given = os.path.normcase(os.path.realpath(glassbox_dir))
    if given == live_events or given.startswith(live_events + os.sep):
        raise Usage("--glassbox-dir must be a scratch folder, not the live %s" % live_events)
    return local


def build_command(side: str, root: str, today: str, flags: dict | None = None):
    """-> (argv, env overrides, cwd) for one side of the parity run. The container image is, in this order: --image, the caller's
    IMAGE_COMPUTE_MELANGE (an explicit choice, for a build no release names yet), else the verified current manifest's."""
    root, flags = os.path.abspath(root), flags or {}
    start = ["--from", flags["start"]] if flags.get("start") else []
    if side == "windows":
        local = require_scratch_dirs(flags.get("local_dir"), flags.get("glassbox_dir"))
        env = {"MONEYBALL_ROOT": root, "MELANGE_TODAY": today, "MELANGE_REBASE_STORED": "1", "MELANGE_LOCAL_ROOT": local,
               "LOCALAPPDATA": os.path.dirname(local), "MELANGE_XLSX_WRITER": "openpyxl", "PYTHONUTF8": "1",
               "PYTHONIOENCODING": "utf-8:surrogateescape", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
        for var, flag in (("MONEYBALL_DRAFT", "draft_dir"), ("MELANGE_DATA_ROOT", "data_dir"), ("GLASSBOX_EVENTS_DIR", "glassbox_dir")):
            if flags.get(flag):
                env[var] = os.path.abspath(flags[flag])
        # build_staffer_network_section shells out to ~/.claude/glassbox/emit.ps1, which writes the LIVE events folder whatever
        # GLASSBOX_EVENTS_DIR says; an absent GLASSBOX_EMIT makes that producer skip the emit (non-fatal, no output byte depends on it).
        env["GLASSBOX_EMIT"] = os.path.join(env["GLASSBOX_EVENTS_DIR"], "emit.ps1.absent")
        return [sys.executable, os.path.join(root, "fec", "run_pipeline.py")] + start, env, root
    env = {"MELANGE_TREE_DIR": root, "MELANGE_TODAY": today}
    for name, flag in PASSTHROUGH:
        value = flags.get(flag) or os.environ.get(name)
        if not value and name == IMAGE_ENV:
            value = current_image()
        if value:
            env[name] = value
    files = ["-f", COMPOSE_FILE] + (["-f", PARITY_COMPOSE_FILE] if "MELANGE_AUDIO_PILOT_DIR" in env else [])
    argv = ["docker", "compose", *files, "--profile", "chain", "run", "--rm", "chain"]
    return argv + (["python", "/melange/fec/run_pipeline.py"] + start if start else []), env, str(REPO_ROOT)


def cmd_command(a) -> int:
    if not a.today:
        raise Usage("--today YYYY-MM-DD is required: both sides must carry the same MELANGE_TODAY "
                    "(build_media_blend is date dependent by design)")
    try:
        dt.date.fromisoformat(a.today)
    except ValueError as e:
        raise Usage(f"--today must be YYYY-MM-DD: {e}") from e
    argv, env, cwd = build_command(a.side, a.root, a.today, vars(a))
    print(f"# chain_parity command, side={a.side}; " + ("running it now" if a.execute else "printed only, nothing executed"))
    for k, v in env.items():
        print(f"{k}={v}")
    print(f"cwd={cwd}")
    print("run=" + (subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)), flush=True)
    if a.start:   # DE-045: a resumed chain never runs the steps before STEP, the intake gate audit_collection (step 3) among them
        print(f"WARNING: --from {a.start} skips every step before it; such a run resumes a failed one but is not a parity or acceptance "
              "proof (DE-045). Prove parity with a full run.", file=sys.stderr, flush=True)
    if not a.execute:
        return 0
    if a.side == "windows":   # a clean environment: only ENV_ALLOW is inherited, so no ambient dial or key reaches the chain
        run_env = {**{k: os.environ[k] for k in ENV_ALLOW if k in os.environ}, **env}
    else:
        run_env = {**os.environ, **env}
        if "MELANGE_AUDIO_PILOT_DIR" in env:   # the mount point of the read-only nested bind
            os.makedirs(os.path.join(env["MELANGE_TREE_DIR"], "audio_pilot"), exist_ok=True)
    try:
        return subprocess.run(argv, cwd=cwd, env=run_env, timeout=a.timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"error: timed out after {a.timeout} s", file=sys.stderr)  # ponytail: kills the child, not its grandchildren
        return 124


def cmd_compare(a) -> int:
    excl = a.exclude or []
    frozen = {k: v for k, v in load_manifest(a.frozen).items() if not excluded(k, excl)}
    roots = {"A1": a.a1, "A2": a.a2, "B": a.b, **({"C": a.c} if a.c else {})}
    for lbl, r in roots.items():
        if not os.path.isdir(r):
            raise Usage(f"{lbl} root {r!r} is not a directory")
    roots = {lbl: os.path.abspath(r) for lbl, r in roots.items()}
    ctx = {"frozen": frozen, "roots": roots, "others": [l for l in roots if l not in ("A1", "A2")],
           "maps": parse_maps(a.map or []), "mask_sets": {}, "mask_report": {},
           "snaps": {lbl: snapshot_tree(r, excl) for lbl, r in roots.items()}}
    if a.store:   # keep each root's snapshot beside the report: the evidence can be audited and `census`ed later without rehashing
        for lbl, snap in ctx["snaps"].items():
            write_json(Path(a.store) / f"{lbl}.manifest.json", snap)
    ctx["changed"] = {lbl: changed_paths(frozen, s) for lbl, s in ctx["snaps"].items()}
    gates = [gate0(ctx), gate1(ctx), gate2(ctx), gate3(ctx, a.expect_members), gate4(ctx)]

    needs: dict[str, dict] = {}
    for lbl, table in ctx["classes"].items():
        for rel, (cls, _) in table.items():
            if cls in NEEDS_ACCEPTANCE:
                needs.setdefault(rel, {})[lbl] = cls
    accept = {x.replace("\\", "/") for x in (a.accept or [])}
    unaccepted = sorted(set(needs) - accept)
    failed = [g["name"] for g in gates if g["status"] == "FAIL"]
    result, code = ("FAIL", 1) if failed else ("NEEDS_ACCEPTANCE", 3) if unaccepted else ("PASS", 0)

    for g in gates:
        print(f"{g['name']} {g['status']}  {g['title']}")
        for line in g["detail"]:
            print(f"    {line}")
    if needs:
        print(f"needs Paul's acceptance ({len(needs)}; {len(needs) - len(unaccepted)} accepted via --accept):")
        for rel in sorted(needs):
            print(f"    {'accepted' if rel in accept else 'PENDING '} {rel}  {needs[rel]}")
    print(f"RESULT: {result} (exit {code})")

    if a.report:
        write_json(a.report, {
            "schema": 1, "result": result, "exit_code": code, "failed_gates": failed,
            "inputs": {"frozen": os.path.abspath(a.frozen), "roots": roots, "maps": ctx["maps"],
                       "expect_members": a.expect_members, "exclude": excl},
            "gates": gates,
            "classes": {lbl: {rel: {"class": c, "detail": d} for rel, (c, d) in t.items()} for lbl, t in ctx["classes"].items()},
            "needs_acceptance": {"pending": unaccepted, "accepted": sorted(set(needs) & accept), "by_path": needs,
                                 "unused_accepts": sorted(accept - set(needs))},
            "masks": ctx["mask_report"],
            "movers": gates[3]["data"]["movers"],
            "counts": {"changed": {lbl: {k: sum(1 for v in c.values() if v == k) for k in ("added", "removed", "modified")}
                                   for lbl, c in ctx["changed"].items()},
                       "classes": gates[4]["data"]["class_counts"]},
            "byte_artifacts": gates[2]["data"]["sha256"],
        })
    return code


# ------------------------------------------------------------------------------------------------- cli

class _Parser(argparse.ArgumentParser):
    def convert_arg_line_to_args(self, arg_line):
        """@FILE arguments: one argument per line, blank lines and # comments skipped."""
        line = arg_line.strip()
        return [line] if line and not line.startswith("#") else []


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="chain_parity", description="Melange chain parity harness (see module docstring).", fromfile_prefix_chars="@")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(func=fn)
        return sp

    s = add("freeze", cmd_freeze, "copy a read set to --dest (mtimes preserved) and write its sha256 manifest")
    s.add_argument("--src", required=True, help="tree to copy from (read only)")
    s.add_argument("--dest", required=True, help="new or empty directory to copy into")
    s.add_argument("--include", action="extend", nargs="+", metavar="GLOB", help="files to copy (glob rules in the module docstring)")
    s.add_argument("--exclude", action="extend", nargs="+", metavar="GLOB", help="files to leave out (wins over include and list)")
    s.add_argument("--list", action="append", metavar="FILE",
                   help="file of exact relative paths, one per line (# comments allowed); a line ending in / takes every file "
                        "under each directory it names or globs (outputs/press_refresh_*/); repeatable")
    s.add_argument("--manifest", metavar="M", help="manifest path (default <dest>.manifest.json)")

    s = add("verify", cmd_verify, "prove --root is byte-identical to --manifest (no extra, missing or modified file)")
    s.add_argument("--root", required=True)
    s.add_argument("--manifest", required=True)
    s.add_argument("--exclude", action="extend", nargs="+", metavar="GLOB")

    s = add("snapshot", cmd_snapshot, "hash a tree to a manifest {rel_posix_path: {sha256, size}}")
    s.add_argument("--root", required=True)
    s.add_argument("--out", required=True, metavar="M")
    s.add_argument("--exclude", action="extend", nargs="+", metavar="GLOB")

    s = add("census", cmd_census, "list the paths added, removed and changed between two manifests")
    s.add_argument("--before", required=True, metavar="M")
    s.add_argument("--after", required=True, metavar="M2")

    s = add("command", cmd_command, "print (or with --execute run) the exact chain command for one side")
    s.add_argument("--side", required=True, choices=("windows", "container"))
    s.add_argument("--root", required=True, metavar="DIR", help="the frozen copy this run starts from")
    s.add_argument("--today", metavar="YYYY-MM-DD", help="MELANGE_TODAY, required, identical on both sides")
    s.add_argument("--execute", action="store_true", help="run the command instead of only printing it")
    s.add_argument("--timeout", type=float, metavar="SECONDS", help="with --execute: kill the run after this long")
    s.add_argument("--from", dest="start", metavar="STEP", help="resume the chain at this step (run_pipeline.py --from STEP)")
    for name, flag in PASSTHROUGH:
        s.add_argument("--" + flag.replace("_", "-"), dest=flag, metavar="V", help=f"container side: {name} (default: caller's environment)")

    s = add("compare", cmd_compare, "gates G0-G4 over the frozen manifest and the A1, A2, B (and C) roots")
    s.add_argument("--frozen", required=True, metavar="M", help="manifest of the pre-run state (from freeze or snapshot)")
    s.add_argument("--a1", required=True, metavar="DIR")
    s.add_argument("--a2", required=True, metavar="DIR")
    s.add_argument("--b", required=True, metavar="DIR")
    s.add_argument("--c", metavar="DIR", help="optional second container run")
    s.add_argument("--map", action="extend", nargs="+", metavar="WIN=CONTAINER", help="root prefix pair for PATH_ONLY, e.g. C:\\melange-data=/melange-data")
    s.add_argument("--expect-members", type=int, default=533, metavar="N")
    s.add_argument("--report", metavar="OUT.json")
    s.add_argument("--store", metavar="DIR", help="also write each root's snapshot to DIR/<A1|A2|B|C>.manifest.json")
    s.add_argument("--accept", action="extend", nargs="+", metavar="PATH", help="relative paths Paul accepted (EOL_ONLY, PATH_ONLY, ZIP_CONTENT)")
    s.add_argument("--exclude", action="extend", nargs="+", metavar="GLOB", help="leave these paths out of every snapshot")
    return p


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:  # argparse exits on --help and on usage errors; tests call main() in-process
        return e.code if isinstance(e.code, int) else 2
    try:
        return args.func(args)
    except (Usage, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except AttributeError:
        pass
    sys.exit(main())
