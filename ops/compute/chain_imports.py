"""Third-party import census and smoke test for the Melange chain (stdlib only; no network, no step is executed).

    python ops/compute/chain_imports.py            table of every third-party package the chain reaches
    python ops/compute/chain_imports.py --json     the same census as JSON {top-level name: first importing file}
    python ops/compute/chain_imports.py --import   import each package the image must carry; OK/FAIL per package, exit 1 on any FAIL
    python ops/compute/chain_imports.py --compile  compile (never run) every step and project module reached; exit 1 on a syntax error

The census starts from the CHAIN literal of fec/run_pipeline.py (read with ast, never imported), resolves every step with the
same walk as run_pipeline's producer resolver, and follows project imports transitively (importing file's directory, fec/, the
repo root, so `from fec.x import y` is followed too). Lazy imports inside functions and try blocks count, and each import site is
classed hard (runs when its module is imported) or soft (inside a def, or in a try that tolerates a missing import). A
spec_from_file_location call that names its .py file in a string constant also pulls that file in; any other dynamic import is
listed, not followed.
Steps do their work at import time, so --import imports the PACKAGES the steps need, never the steps: it proves the image carries
every dependency without running the chain.
Used by ops/tests/test_compute_melange.py and by .github/workflows/compute-melange.yml (inside the built image).
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import re
import sys
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP_DIRS = ("tests", "attic", "__pycache__")          # run_pipeline's producer resolver skips these and _quarantine*
DIST_NAMES = {"sklearn": "scikit-learn", "yaml": "pyyaml", "dateutil": "python-dateutil", "PIL": "pillow",
              "bs4": "beautifulsoup4", "cv2": "opencv-python"}   # import name -> distribution name, where they differ
# Statically reachable, deliberately NOT in the image: every import site is soft and sits in a CLI entry point the chain never
# runs (ASR/alignment in speech/source_media_recovery.py, ECAPA/wav2vec2 embeddings in members/b5_audio_embed.py, which refuses
# cleanly with BackendRefused when the package is missing). torch alone is gigabytes. test_compute_melange.py keeps each entry
# soft-only and out of the lock, so an import that becomes hard fails CI instead of failing mid-run.
NOT_IN_IMAGE = {
    "faster_whisper": "ASR in speech/source_media_recovery.py (alignment and local-reuse commands)",
    "imageio_ffmpeg": "ffmpeg path lookup in speech/source_media_recovery.py, falls back to PATH",
    "speechbrain": "ECAPA speaker embeddings, members/b5_audio_embed.py (BackendRefused when absent)",
    "torch": "members/b5_audio_embed.py embedding backends (BackendRefused when absent)",
    "transformers": "wav2vec2 embeddings, members/b5_audio_embed.py (BackendRefused when absent)",
}
_DYNAMIC_IMPORTS = {"import_module", "__import__", "find_spec", "spec_from_file_location", "SourceFileLoader"}
_TOLERATED = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
_SCRIPT = re.compile(r"[A-Za-z0-9_./\\%:-]+\.py")      # a string constant that names a .py file (no spaces)


def dist_name(top):
    return DIST_NAMES.get(top, top)


def chain_steps(root):
    """The step script names, in order, from the first literal `CHAIN = [(name, args), ...]` of fec/run_pipeline.py."""
    with open(os.path.join(root, "fec", "run_pipeline.py"), "rb") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.List)
                and any(isinstance(t, ast.Name) and t.id == "CHAIN" for t in node.targets)):
            names = [e.elts[0].value for e in node.value.elts
                     if isinstance(e, ast.Tuple) and e.elts and isinstance(e.elts[0], ast.Constant)
                     and isinstance(e.elts[0].value, str)]
            if names:
                return names
    raise SystemExit("fec/run_pipeline.py has no literal CHAIN list")


def py_index(root):
    """{basename: [paths]} of every .py under fec/, skipping the directories run_pipeline's producer resolver skips."""
    index = {}
    for d, dirs, files in os.walk(os.path.join(root, "fec")):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("_quarantine"))
        for f in sorted(files):
            if f.endswith(".py"):
                index.setdefault(f, []).append(os.path.join(d, f))
    return index


def step_paths(root, names, index=None):
    """{basename: path} for the chain steps, as run_pipeline.py resolves them (a duplicate or missing basename is an error)."""
    index = py_index(root) if index is None else index
    dup = {n: index[n] for n in names if len(index.get(n, ())) > 1}
    if dup:
        raise RuntimeError(f"duplicate producer basename(s): {dup}")
    missing = sorted(n for n in set(names) if n not in index)
    if missing:
        raise RuntimeError(f"chain producer script(s) not found: {missing}")
    return {n: index[n][0] for n in sorted(set(names))}


def _project_files(dotted, bases):
    """(is_project, files executed by importing `dotted`) against the directories in `bases`."""
    parts = dotted.split(".")
    for base in bases:
        head = os.path.join(base, parts[0])
        if not (os.path.isfile(head + ".py") or os.path.isdir(head)):
            continue
        files, cur = [], base
        for part in parts:
            cur = os.path.join(cur, part)
            if os.path.isfile(cur + ".py"):
                files.append(cur + ".py")
                break
            if os.path.isfile(os.path.join(cur, "__init__.py")):
                files.append(os.path.join(cur, "__init__.py"))
            elif not os.path.isdir(cur):
                break
        return True, files
    return False, []


def _call_name(func):
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _tolerates_import_error(node):
    """True for a try whose handlers catch a missing import (ImportError, Exception, a bare except...)."""
    for handler in node.handlers:
        kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        if handler.type is None or any(_call_name(k) in _TOLERATED for k in kinds if k is not None):
            return True
    return False


def _imports(tree):
    """([(dotted module, relative level, imported names, soft)], [(line, call source)], [.py names]) for one parsed file.

    soft: the site runs only when a function is called, under `if TYPE_CHECKING`, or inside a try that tolerates a missing import.
    The .py names are the files spec_from_file_location calls load by path."""
    static, dynamic, scripts = [], [], []
    stack = [(tree, False)]
    while stack:
        node, soft = stack.pop()
        if isinstance(node, ast.Import):
            static += [(a.name, 0, (), soft) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            static.append((node.module or "", node.level, tuple(a.name for a in node.names), soft))
        elif isinstance(node, ast.Call) and _call_name(node.func) in _DYNAMIC_IMPORTS:
            arg = node.args[0] if node.args else None
            named = [os.path.basename(c.value.replace("\\", "/")) for c in ast.walk(node)
                     if isinstance(c, ast.Constant) and isinstance(c.value, str) and _SCRIPT.fullmatch(c.value)]
            if (_call_name(node.func) in ("import_module", "__import__", "find_spec") and isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str) and not arg.value.startswith(".")):
                static.append((arg.value, 0, (), soft))
            elif named and _call_name(node.func) in ("spec_from_file_location", "SourceFileLoader"):
                scripts += named
            else:
                dynamic.append((node.lineno, ast.unparse(node)[:110]))
        children = [(c, soft) for c in ast.iter_child_nodes(node)]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            children = [(c, True) for c, _ in children]
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))) and _tolerates_import_error(node):
            children = [(c, soft or c in node.body) for c, _ in children]
        elif isinstance(node, ast.If) and _call_name(node.test) == "TYPE_CHECKING":
            children = [(c, soft or c in node.body) for c, _ in children]
        stack.extend(reversed(children))
    return static, dynamic, scripts


def scan(root=ROOT):
    """{'steps', 'modules', 'third_party': {top: first importing file}, 'soft_only': [top], 'via_script': [file],
    'dynamic': {file: [(line, call)]}, 'unparsed': [file]}. via_script: modules reached only through a spec_from_file_location path."""
    root = os.path.abspath(root)
    fec = os.path.join(root, "fec")
    index = py_index(root)
    steps = step_paths(root, chain_steps(root), index)
    stdlib = set(sys.stdlib_module_names)
    third, hard, dynamic, unparsed, seen, imported = {}, set(), {}, [], set(), set(steps.values())
    queue = deque(steps[n] for n in sorted(steps))
    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        try:
            with open(path, "rb") as fh:
                static, dyn, scripts = _imports(ast.parse(fh.read()))
        except (SyntaxError, ValueError):
            unparsed.append(rel)
            continue
        if dyn:
            dynamic[rel] = dyn
        queue.extend(other for name in scripts for other in index.get(name, ()) if other not in seen)
        here = os.path.dirname(path)
        for module, level, names, soft in static:
            if level:
                base = here
                for _ in range(level - 1):
                    base = os.path.dirname(base)
                bases, mods = [base], [m for m in [module] if m] + [f"{module}.{n}".strip(".") for n in names]
            else:
                top = module.split(".")[0]
                if not top or top in stdlib:
                    continue
                bases, mods = [here, fec, root], [module] + [f"{module}.{n}" for n in names]
                if not _project_files(module, bases)[0]:
                    third.setdefault(top, rel)
                    if not soft:
                        hard.add(top)
                    continue
            for mod in mods:
                found = _project_files(mod, bases)[1]
                imported.update(found)
                queue.extend(f for f in found if f not in seen)
    return {"steps": len(steps), "modules": len(seen), "third_party": dict(sorted(third.items(), key=lambda kv: kv[0].lower())),
            "files": sorted(seen),
            "soft_only": sorted(set(third) - hard),
            "via_script": sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in seen - imported),
            "dynamic": dynamic, "unparsed": sorted(unparsed)}


def census(root=ROOT):
    """{third-party top-level import name: repo-relative path of the first chain module that imports it}."""
    return scan(root)["third_party"]


def import_all(names, out=print):
    """Import each name for real; print OK/FAIL per name; return the failures {name: error}."""
    failed = {}
    for name in sorted(names):
        try:
            mod = importlib.import_module(name)
        except Exception as exc:                                       # noqa: BLE001 - any failure to import is a FAIL
            failed[name] = f"{type(exc).__name__}: {exc}"
            out(f"FAIL {name}: {failed[name]}")
        else:
            out(f"OK   {name} {getattr(mod, '__version__', '')}".rstrip())
    return failed


def compile_all(paths):
    """Compile each file (nothing is executed, no .pyc is written); return the failures {path: error}."""
    failed = {}
    for path in paths:
        try:
            with open(path, "rb") as fh:
                compile(fh.read(), path, "exec", dont_inherit=True)
        except (SyntaxError, ValueError, OSError) as exc:             # noqa: BLE001 - any file that does not compile is a FAIL
            failed[path] = f"{type(exc).__name__}: {exc}"
    return failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT, help="repo root holding fec/run_pipeline.py (default: this checkout)")
    ap.add_argument("--json", action="store_true", help="print the census as JSON")
    ap.add_argument("--import", dest="do_import", action="store_true", help="import every package the image carries; exit 1 on a failure")
    ap.add_argument("--compile", dest="do_compile", action="store_true",
                    help="compile every chain step and project module the census reaches under THIS interpreter; exit 1 on a failure")
    args = ap.parse_args(argv)
    result = scan(args.root)
    third = result["third_party"]
    if args.json:
        print(json.dumps(third, indent=2, sort_keys=True))
        return 0
    if args.do_compile:
        failed = compile_all(result["files"])
        for path, err in sorted(failed.items()):
            print(f"FAIL {os.path.relpath(path, args.root)}: {err}")
        print(f"compiled {len(result['files']) - len(failed)} of {len(result['files'])} modules"
              + (f"; FAILED: {len(failed)}" if failed else ""))
        return 1 if failed or not result["files"] else 0
    print(f"{result['steps']} chain steps, {result['modules']} project modules reached ({len(result['via_script'])} loaded "
          f"by path), {len(third)} third-party packages")
    if args.do_import:
        for name in sorted(set(third) & set(NOT_IN_IMAGE)):
            print(f"SKIP {name}: not in the image ({NOT_IN_IMAGE[name]})")
        wanted = set(third) - set(NOT_IN_IMAGE)
        failed = import_all(wanted)
        print(f"{len(wanted) - len(failed)} of {len(wanted)} imported" + (f"; FAILED: {', '.join(sorted(failed))}" if failed else ""))
        return 1 if failed or not wanted else 0
    for name, first in third.items():
        note = " (not in the image)" if name in NOT_IN_IMAGE else " (soft)" if name in result["soft_only"] else ""
        print(f"{name:<16} {dist_name(name):<16} first imported by {first}{note}")
    for rel, calls in result["dynamic"].items():
        for line, call in calls:
            print(f"dynamic import not followed: {rel}:{line}: {call}")
    for rel in result["unparsed"]:
        print(f"not parsed: {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
