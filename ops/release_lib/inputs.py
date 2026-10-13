"""Per-image input hash: what an image is built from, so an unchanged image is reused instead of rebuilt.

The hash covers the Dockerfile, every file its `COPY`/`ADD` lines name (stage-to-stage `--from` copies and URLs are not
inputs), the `common` files (ops/images.lock: the base images arrive as build args) and the image's own `extra` paths
(things a wrapper mounts at run time), all taken from ops/image_inputs.json. Files are identified by git blob id, so
the laptop (the exported tree it is about to push) and CI (`git ls-tree` of the tagged commit) compute the same value.

Standard library only and importable by path: CI runs `python3 ops/release_lib/inputs.py hash <image>`.
    inputs.py hash <image> [--rev HEAD]     the hash CI looks up as the image tag inputs-<hash>
    inputs.py list [--rev HEAD]             every image whose Dockerfile is in the tree, with its hash
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parents[1] / "image_inputs.json"
SCHEMA = 1                                                     # bump to invalidate every reuse tag
COPY_LINE = re.compile(r"^\s*(?:COPY|ADD)\s+(.*)$", re.IGNORECASE)


class InputsError(ValueError):
    """The inputs cannot be determined; callers stop (a build that cannot be hashed is never silently reused)."""


def load_spec(path=SPEC_PATH) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if spec.get("schema") != SCHEMA or not isinstance(spec.get("images"), dict):
        raise InputsError(f"{path}: not an image_inputs schema {SCHEMA} file")
    return spec


def copy_sources(dockerfile: str) -> list[str]:
    """Context paths named by COPY/ADD lines. Skips `--from=`, URLs and heredocs; joins `\\` continuations."""
    sources = []
    for line in re.sub(r"\\\r?\n", " ", dockerfile).splitlines():
        match = COPY_LINE.match(line)
        if not match or "<<" in match.group(1):
            continue
        try:
            words = shlex.split(match.group(1))
        except ValueError:
            raise InputsError(f"cannot parse Dockerfile line: {line.strip()[:120]}")
        flags = [w for w in words if w.startswith("--")]
        args = [w for w in words if not w.startswith("--")]
        if any(f.startswith("--from") for f in flags) or len(args) < 2:
            continue
        sources += [a for a in args[:-1] if "://" not in a]
    return sources


def match(entries, pattern: str) -> list[str]:
    """Tree paths a context pattern covers: a file, a directory (everything below it), `.`, or a glob (`*` also crosses `/`,
    which can only widen the inputs)."""
    pattern = pattern.strip().removeprefix("./").rstrip("/")
    if pattern in ("", "."):
        return sorted(entries)
    if any(c in pattern for c in "*?["):
        return sorted(p for p in entries if fnmatch.fnmatchcase(p, pattern) or fnmatch.fnmatchcase(p, pattern + "/*"))
    return sorted(p for p in entries if p == pattern or p.startswith(pattern + "/"))


def input_files(entries, name: str, spec: dict, read) -> list[str]:
    """Sorted tree paths that make up image `name`. `read(path) -> str` gives the Dockerfile's text."""
    row = spec["images"].get(name)
    if row is None:
        raise InputsError(f"{name}: not in image_inputs.json")
    dockerfile = row["dockerfile"]
    if dockerfile not in entries:
        raise InputsError(f"{name}: {dockerfile} is not in the tree")
    files = {dockerfile}
    for pattern in [*copy_sources(read(dockerfile)), *spec.get("common", []), *row.get("extra", [])]:
        found = match(entries, pattern)
        if not found:
            raise InputsError(f"{name}: {pattern!r} (COPY, common or extra) matches nothing in the tree")
        files.update(found)
    return sorted(files)


def image_hash(entries, name: str, spec: dict, read) -> str:
    """sha256 over the image name, the schema and each input's (path, git blob id). Order and line endings cannot change it."""
    digest = hashlib.sha256(f"{name}\n{SCHEMA}\n".encode())
    for path in input_files(entries, name, spec, read):
        digest.update(f"{path}\0{entries[path][1]}\n".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def all_hashes(entries, spec: dict, read) -> dict[str, str]:
    """{image: hash} for the images whose Dockerfile is present (compute images join as their lanes land)."""
    return {name: image_hash(entries, name, spec, read) for name, row in sorted(spec["images"].items())
            if row["dockerfile"] in entries}


def git_entries(rev: str = "HEAD", cwd=None) -> dict[str, tuple[str, str]]:
    """{path: (mode, blob id)} of a commit, as export.ls_tree gives it."""
    out = subprocess.run(["git", "ls-tree", "-r", "-z", "--full-tree", rev], cwd=cwd, capture_output=True, check=True).stdout
    entries = {}
    for record in out.split(b"\0"):
        if record:
            meta, _, raw = record.partition(b"\t")
            mode, kind, oid = meta.decode().split()
            if kind == "blob":
                entries[raw.decode("utf-8", "surrogateescape")] = (mode, oid)
    return entries


def git_reader(entries, cwd=None):
    def read(path: str) -> str:
        return subprocess.run(["git", "cat-file", "blob", entries[path][1]], cwd=cwd, capture_output=True,
                              check=True).stdout.decode("utf-8", "replace")
    return read


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("hash", "list"):
        p = sub.add_parser(cmd)
        if cmd == "hash":
            p.add_argument("image")
        p.add_argument("--rev", default="HEAD")
    args = parser.parse_args(argv)
    try:
        spec, entries = load_spec(), git_entries(args.rev)
        read = git_reader(entries)
        if args.cmd == "hash":
            print(image_hash(entries, args.image, spec, read))
        else:
            for name, value in all_hashes(entries, spec, read).items():
                print(f"{name} {value}")
    except (InputsError, subprocess.CalledProcessError, OSError) as error:
        print(f"inputs: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
