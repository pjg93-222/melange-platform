"""History-free, byte-faithful source export into the bare platform repo.

Nothing is ever checked out, so autocrlf and eol attributes never run: a blob id in the platform repo
IS the blob id in the source repo. Method: ls-tree -> filter -> pack-objects | index-pack -> temp index
-> write-tree -> commit-tree with a fixed date -> prove the new commit's tree equals the expected set.
"""
from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path

from .core import GIT_ZERO, ReleaseError, git, git_text

MAX_BYTES = 5 * 1024 * 1024
CODE_EXT = {".py", ".ps1", ".psm1", ".cs", ".r", ".js", ".mjs", ".cjs", ".css", ".html", ".md", ".json",
            ".geojson", ".webmanifest", ".png", ".ico", ".svg", ".toml", ".lock", ".yml", ".yaml",
            ".tpl", ".sh", ".service", ".timer", ".path"}
MELANGE_ROOTS = ("fec", "requirements.txt", "ops")
# fec/docs/: working notes and audit write-ups, not source. Same for the Mentat memo fixture below: real contributor names.
# G8 (2026-10-16): synthetic tests of repairs whose sources live under the excluded fec/research/. Exported
# alone they could never resolve, so these exact paths stay out; every other fec/tests path is still exported.
MELANGE_RESEARCH_TESTS = (
    "fec/tests/infra/test_cohort_output_bindings.py",
    "fec/tests/infra/test_research_optimized_guards.py",
    "fec/tests/research/test_research_io_regression.py",
    "fec/tests/research/test_research_assertion_boundaries.py",
    "fec/tests/research/test_institutions_reference_report.py",
)
# 2026-10-19: tests that read gitignored local build products (root lobby-*.html / cu-member-desk.html pages and
# fec/surfaces/staffer_live/approved/), never in git, so an exported copy could never run either.
MELANGE_LOCAL_ARTIFACT_TESTS = (
    "fec/tests/surfaces/test_original_workspace.py",
)
MELANGE_EXCLUDE = re.compile(r"^fec/(_quarantine_[^/]*/|attic/|research/|docs/|pas224\.zip$)"
                             r"|^(?:" + "|".join(re.escape(p) for p in MELANGE_RESEARCH_TESTS + MELANGE_LOCAL_ARTIFACT_TESTS)
                             + r")$")
# mentat-pocket.html is the Pocket TEMPLATE with a stale 2026-09-14 data snapshot (~119.6k chars) baked in between its
# DATA markers. The release rebuilds Pocket from live data (manifest.build_pocket reads this template from the live
# Mentat checkout, not from the export), so the old snapshot must not ride the source-only export.
# mentat/inputs/ is data, except the two small rule files Mentat's CI reads (fec_receipt_rules.json, fec_transaction_types.json).
MENTAT_EXCLUDE = re.compile(r"^mentat/(inputs/(?!fec_receipt_rules\.json$|fec_transaction_types\.json$)|state/|audits/|\.cache/)"
                            r"|^mentat/tools/pocket/mentat-pocket\.html$|^mentat/tests/fixtures/memo_redisclosure_cases\.json$")
AUTHOR_ENV = {"GIT_AUTHOR_NAME": "Melange Release", "GIT_AUTHOR_EMAIL": "release@melange",
              "GIT_COMMITTER_NAME": "Melange Release", "GIT_COMMITTER_EMAIL": "release@melange"}


def remap(path: str) -> str:
    if path.startswith("ops/github/"):
        return ".github/" + path[len("ops/github/"):]
    return ".gitattributes" if path == "ops/export.gitattributes" else path


def allowed_name(path: str, export_cfg: dict | None = None) -> bool:
    export_cfg = export_cfg or {}
    # The desktop builder includes this NSIS hook. Keep the exception path-bound:
    # arbitrary installer scripts and inert workflow templates are not release inputs.
    if path == "ops/desktop/client/installer.nsh":
        return True
    low = posixpath.basename(path).lower()
    if low == ".gitattributes" or re.fullmatch(r"requirements.*\.txt", low):
        return True
    if low in {n.lower() for n in export_cfg.get("extra_names", [])}:
        return True
    return posixpath.splitext(low)[1] in CODE_EXT | {e.lower() for e in export_cfg.get("extra_extensions", [])}


def ls_tree(repo, commit: str, paths=(), *, git_dir=None) -> dict[str, tuple[str, str]]:
    """{path: (mode, oid)} for a commit, straight from the object database."""
    args = ["ls-tree", "-r", "-z", "--full-tree", commit] + (["--", *paths] if paths else [])
    out = git(*args, cwd=None if git_dir else repo, git_dir=git_dir)
    result = {}
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, raw = record.partition(b"\t")
        mode, kind, oid = meta.decode().split()
        if kind == "blob":
            result[raw.decode("utf-8", "surrogateescape")] = (mode, oid)
        elif kind == "commit":
            result[raw.decode("utf-8", "surrogateescape")] = (mode, oid)      # gitlink: dropped by the mode filter
    return result


def blob_sizes(repo, oids, *, git_dir=None) -> dict[str, int]:
    out = git("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
              cwd=None if git_dir else repo, git_dir=git_dir, input="".join(o + "\n" for o in oids).encode())
    sizes = {}
    for line in out.decode().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob":
            sizes[parts[0]] = int(parts[2])
    return sizes


def plan_export(cfg) -> dict:
    """The expected platform tree and everything dropped on the way. Reads the source repos only."""
    live, export_cfg = Path(cfg["melange_live"]), cfg.get("export", {})
    heads = {"melange": git_text("rev-parse", f"{cfg.get('melange_ref', 'main')}^{{commit}}", cwd=live),
             "mentat": git_text("rev-parse", f"{cfg['mentat_ref']}^{{commit}}", cwd=cfg["mentat_live"])}
    sources = [("melange", live, heads["melange"], list(MELANGE_ROOTS), MELANGE_EXCLUDE, True),
               ("mentat", Path(cfg["mentat_live"]), heads["mentat"], ["mentat"], MENTAT_EXCLUDE, False)]
    candidates, dropped = {}, []
    for name, repo, commit, roots, exclude, do_remap in sources:
        for src_path, (mode, oid) in ls_tree(repo, commit, roots).items():
            path = remap(src_path) if do_remap else src_path
            if mode not in ("100644", "100755"):
                dropped.append((path, f"mode {mode} (symlink or submodule)"))
            elif exclude.match(src_path):
                dropped.append((path, "excluded path"))
            elif not allowed_name(path, export_cfg):
                dropped.append((path, "extension not allowed"))
            elif path in candidates:
                raise ReleaseError("EXPORT_COLLISION", f"two sources map to {path}")
            else:
                candidates[path] = (mode, oid, name)
    for name, repo, _c, *_ in sources:
        oids = [v[1] for v in candidates.values() if v[2] == name]
        sizes = blob_sizes(repo, oids)
        for path, (mode, oid, src) in list(candidates.items()):
            if src == name and sizes.get(oid, 0) > export_cfg.get("max_bytes", MAX_BYTES):
                dropped.append((path, f"larger than {export_cfg.get('max_bytes', MAX_BYTES) // 1048576} MB"))
                del candidates[path]
    return {"entries": {p: (m, o) for p, (m, o, _s) in candidates.items()},
            "source_of": {p: s for p, (_m, _o, s) in candidates.items()},
            "dropped": sorted(dropped), "heads": heads}


# ------------------------------------------------------------------ bare repo
def ensure_bare(bare, repo: str | None = None, url: str | None = None) -> Path:
    bare = Path(bare)
    if not (bare / "HEAD").is_file():
        bare.parent.mkdir(parents=True, exist_ok=True)
        git("init", "--bare", "--template=", str(bare))
        git("symbolic-ref", "HEAD", "refs/heads/main", git_dir=bare)
    git("config", "core.autocrlf", "false", git_dir=bare)
    hooks = bare / "hooks"
    if hooks.is_dir() and any(hooks.iterdir()):
        raise ReleaseError("BARE_HOOKS", f"{hooks} must be empty: the platform repo has no hooks")
    if repo:
        url = url or f"https://github.com/{repo}.git"
        remotes = git_text("remote", git_dir=bare).split()
        if "origin" not in remotes:
            git("remote", "add", "origin", url, git_dir=bare)
        elif git_text("remote", "get-url", "origin", git_dir=bare) != url:
            raise ReleaseError("BARE_REMOTE", f"origin of {bare} is not {url}")
    return bare


def rev(bare, ref: str) -> str | None:
    out = git("rev-parse", "--verify", "-q", ref + "^{commit}", git_dir=bare, check=False).decode().strip()
    return out or None


def _missing(bare, oids) -> list[str]:
    out = git("cat-file", "--batch-check", git_dir=bare, input="".join(o + "\n" for o in oids).encode())
    return [line.split()[0] for line in out.decode().splitlines() if line.endswith(" missing")]


def transfer_blobs(bare, plan, cfg) -> int:
    """Copy the blobs the platform repo lacks, as a pack from the source repo. Returns how many."""
    repos = {"melange": cfg["melange_live"], "mentat": cfg["mentat_live"]}
    moved = 0
    for name, repo in repos.items():
        wanted = sorted({plan["entries"][p][1] for p, s in plan["source_of"].items() if s == name})
        lacking = _missing(bare, wanted)
        if not lacking:
            continue
        pack = git("pack-objects", "--stdout", "-q", cwd=repo, input="".join(o + "\n" for o in lacking).encode())
        git("index-pack", "--stdin", git_dir=bare, input=pack)
        moved += len(lacking)
    return moved


def build_commit(bare, plan, cfg, *, parent: str | None, date_unix: int, message: str) -> tuple[str, str]:
    """Blobs -> tree -> commit, no ref touched. Identical inputs give an identical commit id."""
    transfer_blobs(bare, plan, cfg)
    index = Path(bare) / f"index.release-{os.getpid()}"
    env = {"GIT_INDEX_FILE": str(index)}
    try:
        index.unlink(missing_ok=True)
        lines = b"".join(f"{mode} {oid}\t{path}\0".encode("utf-8", "surrogateescape")
                         for path, (mode, oid) in sorted(plan["entries"].items()))
        git("update-index", "-z", "--add", "--index-info", git_dir=bare, env=env, input=lines)
        tree = git_text("write-tree", git_dir=bare, env=env)
    finally:
        index.unlink(missing_ok=True)
    when = f"{int(date_unix)} +0000"
    cenv = {**AUTHOR_ENV, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    commit = git_text("commit-tree", tree, *(["-p", parent] if parent else []), "-F", "-",
                      git_dir=bare, env=cenv, input=message.encode("utf-8"))
    return commit, tree


def prove(bare, commit: str, plan) -> None:
    """D-048: the new commit's (mode, blob, path) set equals the expected set exactly, or fail."""
    got = ls_tree(None, commit, git_dir=bare)
    want = plan["entries"]
    if got != want:
        extra, absent = sorted(set(got) - set(want)), sorted(set(want) - set(got))
        changed = sorted(p for p in set(got) & set(want) if got[p] != want[p])
        raise ReleaseError("PROOF_FAILED", f"tree differs: {len(extra)} extra {extra[:3]}, "
                                           f"{len(absent)} missing {absent[:3]}, {len(changed)} changed {changed[:3]}")


def set_ref(bare, ref: str, new: str, old: str | None) -> None:
    git("update-ref", ref, new, old or GIT_ZERO, git_dir=bare)


def restore_ref(bare, ref: str, current: str, previous: str | None) -> None:
    if previous:
        git("update-ref", ref, previous, current, git_dir=bare)
    else:
        git("update-ref", "-d", ref, current, git_dir=bare)


def tag_release(bare, tag: str, commit: str) -> str:
    """Lightweight tag. Existing on the same commit is fine; on another commit is a hard stop."""
    existing = rev(bare, f"refs/tags/{tag}")
    if existing and existing != commit:
        raise ReleaseError("TAG_CONFLICT", f"{tag} already points at {existing[:12]}, not {commit[:12]}")
    if not existing:
        git("update-ref", f"refs/tags/{tag}", commit, GIT_ZERO, git_dir=bare)
    return commit


def materialize(bare, plan, dest) -> Path:
    """Write the exported blobs byte-for-byte under dest (for the security gate to scan)."""
    dest = Path(dest)
    items = sorted(plan["entries"].items())
    for start in range(0, len(items), 400):
        chunk = items[start:start + 400]
        out = git("cat-file", "--batch", git_dir=bare, input="".join(o + "\n" for _p, (_m, o) in chunk).encode())
        cursor = 0
        for path, (_mode, oid) in chunk:
            end = out.index(b"\n", cursor)
            header = out[cursor:end].split()
            size = int(header[2])
            target = dest / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(out[end + 1:end + 1 + size])
            cursor = end + 1 + size + 1
    return dest


def diff_against(bare, plan) -> dict:
    """added/changed/removed paths versus the current platform main (plan mode; read-only)."""
    tip = rev(bare, "refs/heads/main") if Path(bare).is_dir() else None
    old = ls_tree(None, tip, git_dir=bare) if tip else {}
    new = plan["entries"]
    return {"previous_commit": tip, "added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
            "changed": sorted(p for p in set(new) & set(old) if new[p] != old[p]), "files": len(new)}
