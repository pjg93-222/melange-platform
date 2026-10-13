#!/usr/bin/env python3
"""Laptop-side release command for Melange / Mentat / Terrier. See ops/RELEASE.md.

    python ops/release.py plan   [--data-only] [--no-netlify] [--skip-kernel]
    python ops/release.py run    [--data-only] [--no-netlify] [--skip-kernel] [--confirm-outward git,data,manifest,netlify,undraft]
    python ops/release.py resume <id> [same flags]
    python ops/release.py images [--confirm-outward git,geo] [--id <CalVer>]    # code only: build the app images, no data/VPS/Netlify
    python ops/release.py images --init-remote --confirm-outward repo         # create the private platform repo, set the bare origin
    python ops/release.py auto   [--plan] [--force-retry] [--pause | --unpause] [--confirm-outward git,geo,data,manifest,netlify,undraft]
    python ops/release.py current [--use <id>]                                  # the manifest compute jobs resolve their images through
    python ops/release.py status [<id>]
    python ops/release.py verify <manifest.json> [--allowed-signers P] [--current ID] [--snapshot P]
    python ops/release.py init-signing | store-netlify-token

Every step checks its external fact first and skips if done, so `resume` is just `run` with the same id.
Anything that leaves the laptop needs config present AND its step named in --confirm-outward (a comma list of
git, data, manifest, netlify, undraft; every step the run reaches must be listed); otherwise the run stops cleanly there.
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.release_lib import auto, core, current, data, export, gates, inputs, manifest, web    # noqa: E402
from ops.release_lib.core import NotConfigured, Pending, ReleaseError, State   # noqa: E402

SERVE_SETS = core.OPS / "serve_sets.json"
CONFIRM_STEPS = ("git", "data", "manifest", "netlify", "undraft")           # the outward steps --confirm-outward can name
IMAGE_STEPS = ("git", "geo", "repo")                                           # what `images --confirm-outward` can name
AUTO_STEPS = ("git", "geo", "data", "manifest", "netlify", "undraft")         # what `auto --confirm-outward` can name
CI_TIMEOUT = 45 * 60                                                          # one `gh run watch`; the run itself is bounded by GitHub
REPO_DESCRIPTION = "Melange platform (source-only release exports)"
CODE_ONLY = {"gates_evaluated": ["tag_collision", "security_gate"],
             "gates_not_evaluated": ["pipeline_lock", "weekly_lock", "mentat_lane_writers", "repos_clean", "kernel_tests", "weekly_refresh",
                                     "invited_build", "census", "receipt_hash", "disk", "security_packages", "security_farm"],
             "why": "code-only image build: the images are built by CI from committed objects and no data leaves the laptop, so the "
                    "live-tree, refresh, invited-build and data gates do not apply"}
EXIT = {"ok": 0, "failed": 1, "usage": 2, "pending": 3, "not_configured": 4}


class Ctx:
    def __init__(self, cfg, *, confirm=frozenset(), emit=None, cred=core.read_credential, sleep=time.sleep, say=print,
                 now=time.time, hooks=None):
        self.cfg, self.confirm, self.cred, self.sleep, self.say, self.now = cfg, confirm, cred, sleep, say, now
        self.hooks = hooks or auto.Hooks()
        self.emit = emit or core.Emitter(cfg)
        self.root = core.releases_root(cfg)

    def dir(self, rid) -> Path:
        return self.root / rid

    def outward(self, key, name, *cfg_keys):
        """Config present -> else NotConfigured; `key` named in --confirm-outward -> else Pending (a clean stop)."""
        core.need(self.cfg, *cfg_keys)
        if key not in self.confirm:
            raise Pending(name)


# ------------------------------------------------------------------ ids and history
def known_ids(ctx) -> list[str]:
    ids = {p.name for p in ctx.root.glob("*") if p.is_dir() and core.ID_RE.fullmatch(p.name)} if ctx.root.is_dir() else set()
    bare = Path(ctx.cfg["platform_bare"])
    if (bare / "HEAD").is_file():
        ids |= {t[1:] for t in core.git_text("tag", "-l", "v*", git_dir=bare).split() if core.ID_RE.fullmatch(t[1:])}
    return sorted(ids, key=core.parse_id)


def completed(ctx, before=None) -> list[str]:
    done = [i for i in known_ids(ctx) if (ctx.dir(i) / "manifest.json.sig").is_file()]
    return [i for i in done if before is None or core.parse_id(i) < core.parse_id(before)]


def load_manifest(ctx, rid):
    path = ctx.dir(rid) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def run_json(*argv):
    out = core.run(list(argv)).stdout
    return json.loads(out) if out.strip() else None


# ------------------------------------------------------------------ plan
def check_outward(ctx) -> dict:
    """What is still missing for each outward step (never raises, never touches the network)."""
    cfg, ready = ctx.cfg, {}
    def probe(name, keys, tools=(), creds=()):
        missing = core.require(cfg, *keys)
        missing += [f"tool:{t}" for t in tools if not shutil.which(t)]
        for c in creds:
            try:
                ctx.cred(cfg["credentials"][c])
            except NotConfigured:
                missing.append(f"credential:{cfg['credentials'][c]}")
        ready[name] = {"ready": not missing, "missing": missing}
    probe("git push", ["platform_repo", "terrier_repo"], ["git"])
    probe("data push", ["domain", "hosts.ssh", "vps.ssh_user"], ["restic", "cloudflared", "sftp"], ["restic", "svc_push"])
    probe("netlify", ["netlify.site_id"])
    if not Path(cfg["netlify"]["token_file"]).is_file():
        ready["netlify"]["ready"] = False
        ready["netlify"]["missing"].append("netlify token (store-netlify-token)")
    probe("signing", ["signing.key"], ["ssh-keygen"])
    return ready


def cmd_plan(ctx, args) -> int:
    cfg, report, problems = ctx.cfg, {"generated_utc": core.utc_now(), "kind": "data-only" if args.data_only else "full"}, []
    serve = json.loads(SERVE_SETS.read_text(encoding="utf-8"))
    try:
        files = data.resolve_files(cfg, serve)
        staged = sum(f["size"] for f in files if data.needs_staging(f["src"]))
        report["serve_sets"] = {"census_pending": serve.get("census_pending"), "systems": data.summarize(files), "staged_bytes": staged}
    except ReleaseError as error:
        staged, report["serve_sets"] = 0, {"error": str(error)}
        problems.append("serve sets")
    report["gates"] = gates.run_gates(cfg, skip_kernel=args.skip_kernel, netlify=not args.no_netlify, need_bytes=staged,
                                      governance_override=args.governance_override, serve_sets=serve, census_override=args.census_override)
    if not args.data_only:
        try:
            plan = export.plan_export(cfg)
            diff = export.diff_against(cfg["platform_bare"], plan) if Path(cfg["platform_bare"]).is_dir() else {"files": len(plan["entries"])}
            report["export"] = {**{k: (v if k in ("previous_commit", "files") else len(v)) for k, v in diff.items()},
                                "heads": plan["heads"], "dropped": plan["dropped"]}
        except ReleaseError as error:
            report["export"] = {"error": str(error)}
            problems.append("export")
    report["security_gate"] = {"detail": "runs against the materialized export during `run`, not in plan"}
    report["outward"] = check_outward(ctx)
    for name, gate in report["gates"].items():
        ctx.say(f"  {'ok  ' if gate['ok'] else 'FAIL'} {name}: {gate['detail']}")
    for key in ("serve_sets", "export"):
        if key in report:
            ctx.say(f"  {key}: " + json.dumps({k: v for k, v in report[key].items() if k != 'dropped'}, default=str)[:400])
    dropped = report.get("export", {}).get("dropped", [])
    if dropped:
        ctx.say(f"  export drops {len(dropped)} file(s) (full list in the report):")
        for path, why in dropped[:10]:
            ctx.say(f"    drop {path}  ({why})")
    for name, row in report["outward"].items():
        ctx.say(f"  outward {name}: " + ("ready" if row["ready"] else "not configured: " + ", ".join(row["missing"])))
    ok = gates.all_ok(report["gates"]) and not problems
    path = ctx.root / f"plan-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    core.atomic_write(path, json.dumps(report, indent=1, default=str).encode("utf-8"))
    ctx.emit("release.plan", "completed" if ok else "failed", f"plan {'ready' if ok else 'blocked'}", metadata={"report": path.name})
    ctx.say(f"plan report: {path}")
    return EXIT["ok"] if ok else EXIT["failed"]


# ------------------------------------------------------------------ run steps
def step_export(ctx, st, rid):
    """Local only: blobs -> tree -> commit -> proof -> security gate on the materialized tree -> refs + tag."""
    cfg = ctx.cfg
    bare = export.ensure_bare(cfg["platform_bare"], cfg["platform_repo"], cfg.get("platform_remote_url"))
    plan, tag = export.plan_export(cfg), f"v{rid}"
    existing = export.rev(bare, f"refs/tags/{tag}")
    if existing:
        try:
            export.prove(bare, existing, plan)
        except ReleaseError as error:
            raise ReleaseError("TAG_CONFLICT", f"{tag} exists and does not match the current sources ({error.message[:120]})")
        st.update(platform_commit=existing, heads=plan["heads"], dropped=len(plan["dropped"]))
        return
    if "previous_commit" not in st:
        st["previous_commit"] = export.rev(bare, "refs/heads/main")
    message = f"Release {rid}\n\nmelange {plan['heads']['melange']}\nmentat {plan['heads']['mentat']}\n"
    commit, _tree = export.build_commit(bare, plan, cfg, parent=st["previous_commit"], date_unix=st["commit_unix"], message=message)
    export.prove(bare, commit, plan)
    scratch = ctx.dir(rid) / "export"
    shutil.rmtree(scratch, ignore_errors=True)
    export.materialize(bare, plan, scratch)
    st["gates"]["security_gate"] = gate = gates.security_gate(core.OPS, scratch)
    shutil.rmtree(scratch, ignore_errors=True)
    if not gate["ok"]:
        raise ReleaseError("SECURITY_GATE", gate["detail"])
    old = export.rev(bare, "refs/heads/main")
    if old not in (commit, st["previous_commit"]):
        raise ReleaseError("MAIN_MOVED", "platform main moved since this release started; start a new release")
    if old != commit:
        export.set_ref(bare, "refs/heads/main", commit, old)
        landed = export.rev(bare, "refs/heads/main")
        if landed != commit:                                                    # belt and braces: roll back to what it was
            export.restore_ref(bare, "refs/heads/main", landed, old)
            raise ReleaseError("REF_UPDATE", "main did not move to the proved commit; rolled back")
    export.tag_release(bare, tag, commit)
    st.update(platform_commit=commit, heads=plan["heads"], dropped=len(plan["dropped"]))


def remote_tag(repo_args, tag):
    out = core.git_text("ls-remote", "origin", f"refs/tags/{tag}", **repo_args)
    return out.split()[0] if out else None


def push_platform(ctx, st, rid):
    """Platform repo main and this release's tag. Never --force; a tag on another commit is a hard stop."""
    tag, bare = f"v{rid}", {"git_dir": ctx.cfg["platform_bare"]}
    remote = remote_tag(bare, tag)
    if remote and remote != st["platform_commit"]:
        raise ReleaseError("TAG_CONFLICT", f"origin already has {tag} on {remote[:12]}")
    if remote != st["platform_commit"]:
        core.git("push", "origin", "main", f"refs/tags/{tag}", **bare)


def push_geo(ctx, st, rid, ref):
    """Geo branch `ref` (the same name there) and this release's tag on its tip. Never --force; a tag elsewhere is a hard stop."""
    cfg, tag = ctx.cfg, f"v{rid}"
    geo = {"cwd": cfg["terrier_checkout"]}
    url = core.git_text("remote", "get-url", "origin", **geo)
    if cfg.get("terrier_remote_url") and url != cfg["terrier_remote_url"]:
        raise ReleaseError("GEO_REMOTE", f"origin of {cfg['terrier_checkout']} is not {cfg['terrier_remote_url']}")
    head = core.git_text("rev-parse", f"refs/heads/{ref}^{{commit}}", **geo)
    local = core.git("rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}", check=False, **geo).decode().strip()
    if local and local != head:
        raise ReleaseError("TAG_CONFLICT", f"Geo tag {tag} is on {local[:12]}, {ref} is {head[:12]}")
    if not local:
        core.git("tag", tag, head, **geo)
    remote = remote_tag(geo, tag)
    if remote and remote != head:
        raise ReleaseError("TAG_CONFLICT", f"Geo origin already has {tag} on {remote[:12]}")
    if not remote:
        core.git("push", "origin", ref, **geo)
        core.git("push", "origin", tag, **geo)
    st["terrier_commit"] = head


def step_push_git(ctx, st, rid):
    """Platform repo (tags with it) and Geo."""
    ctx.outward("git", "git push", "platform_repo", "terrier_repo")
    push_platform(ctx, st, rid)
    push_geo(ctx, st, rid, ctx.cfg["terrier_ref"])


def wait_for(ctx, fn, what, limit=900, every=60):
    deadline = time.monotonic() + limit
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise ReleaseError("TIMEOUT", f"timed out waiting for {what}")
        ctx.sleep(every)


def gh_release(repo, tag):
    pages = run_json("gh", "api", f"repos/{repo}/releases?per_page=100") or []
    return next((r for r in pages if r.get("tag_name") == tag), None)


def asset_table(repo, release, workdir: Path) -> dict:
    table = {}
    for asset in release.get("assets", []):
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:"):
            sha = digest[7:]
        else:
            target = workdir / asset["name"]
            target.write_bytes(core.run(["gh", "api", "-H", "Accept: application/octet-stream",
                                         f"repos/{repo}/releases/assets/{asset['id']}"]).stdout)
            sha = core.sha256_file(target)
        table[asset["name"]] = {"sha256": sha, "bytes": int(asset["size"]), "id": asset["id"]}
    return table


TOKEN_RE = re.compile(r"(?:gh[pousr]_|github_pat_)\w+|(?i:bearer|token|password|secret)[=:\s]+\S+")


def failed_log_tail(repo, run_id, lines=40) -> str:
    """Last lines of `gh run view --log-failed`, anything token-shaped masked (Actions already masks its own secrets)."""
    out = core.run(["gh", "run", "view", str(run_id), "--repo", repo, "--log-failed"], check=False).stdout.decode("utf-8", "replace")
    out = out.replace("﻿", "")                                   # Actions logs open with a BOM; it only garbles the tail
    return "\n".join(TOKEN_RE.sub("****", line[:300]) for line in out.splitlines()[-lines:])


def step_ci(ctx, st, rid):
    """Wait for both repos' tag workflows, then read digests.json + asset hashes from the (draft) releases."""
    cfg, tag = ctx.cfg, f"v{rid}"
    work = ctx.dir(rid) / "ci"
    work.mkdir(parents=True, exist_ok=True)
    digests, packages, releases, run_urls, extras = {}, {}, {}, {}, {}
    for label, repo, commit in (("platform", cfg["platform_repo"], st["platform_commit"]),
                                ("geo", cfg["terrier_repo"], st["terrier_commit"])):
        if label == "geo" and st.get("geo_reused"):
            continue
        runs = wait_for(ctx, lambda: run_json("gh", "run", "list", "--repo", repo, "--commit", commit,
                                              "--json", "databaseId,status,conclusion", "-L", "20"), f"a {label} workflow run")
        for run in runs:
            if run["status"] != "completed":
                try:
                    core.run(["gh", "run", "watch", str(run["databaseId"]), "--repo", repo, "--exit-status"], check=False, timeout=CI_TIMEOUT)
                except subprocess.TimeoutExpired:
                    raise ReleaseError("TIMEOUT", f"{label} run {run['databaseId']} not finished after {CI_TIMEOUT // 60} min")
        runs = run_json("gh", "run", "list", "--repo", repo, "--commit", commit, "--json", "databaseId,status,conclusion", "-L", "20")
        run_urls[label] = [f"https://github.com/{repo}/actions/runs/{r['databaseId']}" for r in runs]
        bad = [r for r in runs if r["status"] != "completed" or r["conclusion"] not in ("success", "skipped")]
        if bad:
            link = f"https://github.com/{repo}/actions/runs/{bad[0]['databaseId']}"
            raise ReleaseError("CI_FAILED", f"{label} workflow did not finish green for {commit[:9]}: {link}\n"
                                            + failed_log_tail(repo, bad[0]["databaseId"]))
        release = wait_for(ctx, lambda: release_with_digests(repo, tag), f"{label} release {tag} with digests.json")
        table = asset_table(repo, release, work)
        digest_file = work / f"{label}-digests.json"
        digest_file.write_bytes(core.run(["gh", "api", "-H", "Accept: application/octet-stream",
                                          f"repos/{repo}/releases/assets/{table['digests.json']['id']}"]).stdout)
        found = json.loads(digest_file.read_text(encoding="utf-8"))
        verify_provenance(repo, commit, tag, release, found)
        digests.update(parse_digests(cfg, found))
        merge_images(extras, extra_images(cfg, found))
        if label == "platform":
            merge_images(extras, run_artifact_images(cfg, repo, commit, tag, work))
        packages.update({(name if label == "platform" else f"{label}-{name}"): {"sha256": a["sha256"], "bytes": a["bytes"]}
                         for name, a in table.items()})
        releases[label] = {"repo": repo, "id": release["id"], "draft": bool(release.get("draft"))}
    digests.update(st.get("carried_digests", {}))
    merge_images(extras, st.get("carried_images", {}))
    if set(digests) != set(manifest.IMAGE_NAMES):
        raise ReleaseError("DIGESTS_MISSING", f"images missing from digests.json: {sorted(set(manifest.IMAGE_NAMES) - set(digests))}")
    st.update(digests=digests, extra_images=extras, packages=packages, releases=releases, runs=run_urls)


BOT = "github-actions[bot]"


def verify_provenance(repo, commit, tag, release, doc):
    """Before anything is signed (PROVENANCE, fail closed): these digests are the release workflow's own for exactly `commit`.
    digests.json names the commit and tag; a successful push run of that tag has head_sha == commit; the release was made by
    github-actions[bot] and every one of its assets was uploaded by it inside that run's window, so a hand-made release or a
    swapped asset fails. The window is tested on the assets' created_at: a release's own created_at is the date of the commit
    its tag points at (GitHub's documented behaviour), which is always before the run. A compromised CI run itself is NOT
    detected (ops/RELEASE.md). Anything missing is unverifiable, hence refused."""
    def refuse(why):
        raise ReleaseError("PROVENANCE", f"{repo} {tag}: {why}")
    if not isinstance(doc, dict) or doc.get("commit") != commit or doc.get("tag") != tag:
        refuse("digests.json does not name this commit and tag")
    try:
        runs = run_json("gh", "api", f"repos/{repo}/actions/runs?head_sha={commit}")["workflow_runs"]
        windows = [(r.get("run_started_at") or r.get("created_at"), r["updated_at"]) for r in runs
                   if r.get("head_sha") == commit and r.get("head_branch") == tag and r.get("event") == "push"
                   and r.get("conclusion") == "success"]
        assets = release["assets"]
        made = [a["created_at"] for a in assets]
        actors = [release["author"]["login"], *(a["uploader"]["login"] for a in assets)]
    except (ReleaseError, KeyError, TypeError, ValueError) as error:
        refuse(f"the workflow runs, release author or asset uploaders cannot be read ({type(error).__name__}: {str(error)[:80]})")
    if not windows:
        refuse(f"no successful push run of this tag has head_sha {commit[:9]}")
    inside = lambda t: isinstance(t, str) and any(isinstance(a, str) and isinstance(b, str) and a <= t <= b for a, b in windows)
    if not all(inside(t) for t in made):
        refuse("an asset of the release was not uploaded inside a successful run for this commit")
    if not assets or any(actor != BOT for actor in actors):
        refuse(f"the release or one of its assets was not created by {BOT}")


def parse_digests(cfg, doc) -> dict:
    """{system: 'sha256:<hex>'} for the images named in one repo's digests.json (each repo carries only its own)."""
    doc = doc.get("images", doc) if isinstance(doc, dict) else {}
    out = {}
    for system, name in manifest.IMAGE_NAMES.items():
        for key in (name, system, cfg["images"][system]):
            if key in doc:
                value = doc[key].get("digest", "") if isinstance(doc[key], dict) else doc[key]
                match = re.search(r"sha256:[0-9a-f]{64}", str(value))
                if match:
                    out[system] = match.group(0)
                    break
    return out


def merge_images(into: dict, new: dict) -> None:
    for name, ref in new.items():
        if into.setdefault(name, ref) != ref:
            raise ReleaseError("DIGEST_CONFLICT", f"two builds of {name} disagree: {into[name]} and {ref}")


def extra_images(cfg, doc) -> dict:
    """Images a digests.json names beyond the three app images ({name: repo@sha256:...}), compute and Terrier build images among
    them. Each must sit in our own registry namespace and be named like its repository; anything else is refused."""
    prefix = cfg["images"]["melange"].rsplit("/", 1)[0] + "/"
    known = {*manifest.IMAGE_NAMES.values(), *manifest.IMAGE_NAMES, *cfg["images"].values()}
    named = doc.get("images") if isinstance(doc, dict) else None
    out = {}
    for name, value in (named.items() if isinstance(named, dict) else ()):
        if name in known:
            continue
        ref = value.get("digest", "") if isinstance(value, dict) else value
        if not (isinstance(ref, str) and manifest.IMAGE_RE.fullmatch(ref) and ref.startswith(prefix)
                and manifest.IMAGE_NAME_RE.fullmatch(name) and ref.split("@")[0].rsplit("/", 1)[-1] == name):
            raise ReleaseError("DIGESTS_BAD", f"digests.json names {name!r} as {str(ref)[:90]!r}: not {prefix}{name}@sha256:<digest>")
        out[name] = ref
    return out


def run_artifact_images(cfg, repo, commit, tag, work: Path) -> dict:
    """Images that workflows other than release.yml build for this tag (the compute-*.yml workflows): each such run uploads a
    workflow artifact named *-digests holding digests.json. Only runs verify_provenance would accept (push of this tag at this
    commit, success) are read, and the file must name this commit and tag."""
    listing = run_json("gh", "api", f"repos/{repo}/actions/runs?head_sha={commit}")
    found = {}
    for item in listing.get("workflow_runs", []) if isinstance(listing, dict) else []:
        if not (item.get("head_sha") == commit and item.get("head_branch") == tag and item.get("event") == "push"
                and item.get("conclusion") == "success"):
            continue
        arts = run_json("gh", "api", f"repos/{repo}/actions/runs/{item['id']}/artifacts")
        for art in arts.get("artifacts", []) if isinstance(arts, dict) else []:
            if str(art.get("name", "")).endswith("-digests") and not art.get("expired"):
                folder = work / f"artifact-{item['id']}-{art['name']}"
                core.run(["gh", "run", "download", str(item["id"]), "--repo", repo, "--name", art["name"], "--dir", str(folder)])
                doc = json.loads((folder / "digests.json").read_text(encoding="utf-8"))
                if not isinstance(doc, dict) or doc.get("commit") != commit or doc.get("tag") != tag:
                    raise ReleaseError("PROVENANCE", f"{repo} {tag}: {art['name']} does not name this commit and tag")
                merge_images(found, extra_images(cfg, doc))
    return found


def release_with_digests(repo, tag):
    release = gh_release(repo, tag)
    return release if release and any(a["name"] == "digests.json" for a in release.get("assets", [])) else None


def step_capture(ctx, st, rid, files, stage: Path):
    """Hash + build the file farm + write snapshot_manifest.json. Local only; the caller holds the pipeline lock."""
    cache = data.HashCache(ctx.root / "hash_cache.sqlite")
    try:
        shutil.rmtree(stage, ignore_errors=True)
        rows = data.build_farm(files, stage, cache, progress=lambda i, n: ctx.emit("release.step", "running", f"hashed {i}/{n}", stage="capture", current=i, total=n))
    finally:
        cache.close()
    invited = st["gates"]["invited_build"]                                      # the gate that just passed named both
    body = data.snapshot_manifest(rid, rows, invited["governance"], invited["profile_sha256"])
    core.atomic_write(ctx.dir(rid) / "snapshot_manifest.json", body)
    st["snapshot"] = {"manifest_sha256": core.sha256_bytes(body), "files": len(rows), "bytes": sum(r["size"] for r in rows)}
    st.save()
    ctx.say(f"  captured {len(rows)} files, {st['snapshot']['bytes'] / 1024 ** 3:.2f} GB (cache hits {cache.hits}, misses {cache.misses})")


def step_package_gate(ctx, st, rid):
    """Everything that leaves the laptop outside the data farm (reader downloads, Netlify staging) passes the packages gate."""
    package, netlify = ctx.dir(rid) / "package", netlify_dir(ctx, rid).parent
    targets = [package, netlify, package / "downloads"]
    for target in targets:
        target.mkdir(parents=True, exist_ok=True)                               # the gate fails closed on a missing target
    st["gates"]["security_packages"] = gate = gates.security_gate(core.OPS, targets[0], "packages", targets[1:])
    if not gate["ok"]:
        raise ReleaseError("SECURITY_GATE", f"packages: {gate['detail']}")


def step_farm_gate(ctx, st, stage: Path):
    """The data farm is data by design, so no data_leak; but no secret value and no secret-named file may be in it."""
    st["gates"]["security_farm"] = gate = gates.security_gate(core.OPS, stage, "farm", scan_cache=ctx.root / "scan_cache.sqlite")
    if not gate["ok"]:
        raise ReleaseError("SECURITY_GATE", f"farm: {gate['detail']}")


def step_data_push(ctx, st, rid, files, stage: Path):
    ctx.outward("data", "data push", "domain", "hosts.ssh", "vps.ssh_user", "credentials.restic", "credentials.svc_push")
    snap = data.restic_backup(ctx.cfg, rid, stage, ctx.dir(rid), ctx.cred)
    data.verify_unchanged(files)
    st["snapshot"]["restic_snapshot_id"] = snap["snapshot_id"]
    st["snapshot"]["restic_path"] = snap["restic_path"]


def image_commits(images: dict, base, platform_commit: str, terrier_commit: str) -> dict:
    """{image: the commit it was built from}. A digest the previous manifest already had keeps that manifest's commit: CI reused it."""
    before = current.images_of(base) if base else {}
    out = {}
    for name, ref in images.items():
        geo = name.startswith(auto.GEO_PREFIX)
        out[name] = terrier_commit if geo else platform_commit
        if before.get(name) == ref:
            out[name] = (base.get("image_commits") or {}).get(name) or (base["systems"]["terrier"]["source"]["commit"] if geo else base["platform_repo"]["commit"])
    return out


def sign_and_stage_manifest(ctx, st, rid) -> dict:
    """full: everything is this release's own. data-only: images and platform commit are the previous release's. code-only: the
    images and platform commit are this release's, the data snapshot (and its snapshot_manifest.json) the previous release's."""
    cfg, d = ctx.cfg, ctx.dir(rid)
    allowed = Path(cfg["signing"].get("allowed_signers") or "")
    allowed = allowed if allowed.is_absolute() else core.OPS.parent / allowed
    if not allowed.is_file():             # the signature is checked against the VPS trust file before it is sent; no file, no manifest
        raise NotConfigured(f"signing.allowed_signers {cfg['signing'].get('allowed_signers')!r} is not a file; the manifest is "
                            "self-checked against the VPS trust file before it is signed and pushed (ops/vps/allowed_signers)")
    kind = st["kind"]
    fresh = kind in ("full", "code-only")                                       # this release built the images
    earlier = completed(ctx, rid)
    previous = earlier[-1] if earlier else None
    base = load_manifest(ctx, previous) if previous else None
    if kind != "full" and not base:
        raise ReleaseError("NO_BASE_RELEASE", f"a {kind} release needs a completed earlier release to carry its {'data' if kind == 'code-only' else 'images'}")
    st.setdefault("manifest_created_utc", core.utc_now())
    commits = {k: (st["heads"][k] if fresh else base["systems"][k]["source"]["commit"]) for k in ("melange", "mentat")}
    commits["terrier"] = st["terrier_commit"] if fresh else base["systems"]["terrier"]["source"]["commit"]
    digests = st["digests"] if fresh else {k: v["image"].split("@")[1] for k, v in base["systems"].items()}
    platform = ({"commit": st["platform_commit"], "tag": f"v{rid}"} if fresh else base["platform_repo"])
    gates_doc = st["gates"]
    if kind == "code-only":
        snapshot = dict(base["data_snapshot"])
        shutil.copyfile(ctx.dir(previous) / "snapshot_manifest.json", d / "snapshot_manifest.json")
        if core.sha256_file(d / "snapshot_manifest.json") != snapshot["manifest_sha256"]:
            raise ReleaseError("SNAPSHOT_MISMATCH", f"{previous}/snapshot_manifest.json does not match the data snapshot its manifest signed")
        carried = base["gates"].get("invited_build")                          # the VPS reads governance from it; the snapshot is unchanged
        gates_doc = {**({"invited_build": {**carried, "carried_from": previous}} if carried else {}), **gates_doc,
                     "code_only": {"ok": True, "detail": CODE_ONLY["why"], "not_evaluated": CODE_ONLY["gates_not_evaluated"]}}
    else:
        snapshot = {"restic_snapshot_id": st["snapshot"]["restic_snapshot_id"], "restic_path": st["snapshot"]["restic_path"],
                    "manifest_sha256": st["snapshot"]["manifest_sha256"], "files": st["snapshot"]["files"], "bytes": st["snapshot"]["bytes"]}
    extra = st.get("extra_images", {}) if fresh else {n: r for n, r in (base.get("images") or {}).items() if n not in manifest.IMAGE_NAMES.values()}
    images = {**{manifest.IMAGE_NAMES[k]: f"{cfg['images'][k]}@{v}" for k, v in digests.items()}, **extra}
    labels = dict(cfg, terrier_ref=cfg["terrier_images_ref"]) if kind == "code-only" else cfg      # Geo's image branch is what built terrier-web
    doc = manifest.build_manifest(labels, release_id=rid, previous=previous, kind=kind, created_utc=st["manifest_created_utc"],
                                  platform_commit=platform["commit"], tag=platform["tag"], commits=commits, digests=digests,
                                  snapshot=snapshot, packages=st.get("packages", base["packages"] if base else {}), gates=gates_doc,
                                  extra_images=extra, image_commits=image_commits(images, base, platform["commit"], commits["terrier"]))
    path = d / "manifest.json"
    core.atomic_write(path, core.canonical_json(doc))
    manifest.sign_manifest(cfg, path)
    manifest.verify_manifest(path, allowed, principal=cfg["signing"]["principal"], namespace=cfg["signing"]["namespace"],
                             snapshot_path=d / "snapshot_manifest.json")
    return doc


def step_current(ctx, rid):
    """The verified manifest becomes this laptop's current one (what compute jobs resolve their images through)."""
    current.publish(ctx.cfg, ctx.dir(rid))


def step_inbox(ctx, rid):
    ctx.outward("manifest", "manifest push", "domain", "hosts.ssh", "vps.ssh_user", "credentials.svc_push")
    d = ctx.dir(rid)
    try:
        if data.fetch_status(ctx.cfg, d, ctx.cred).get("release_id") == rid:
            return                                                          # the VPS already applied it
    except (ReleaseError, ValueError, OSError):
        pass
    data.push_release_files(ctx.cfg, {n: d / n for n in ("snapshot_manifest.json", "manifest.json.sig", "manifest.json")}, d, ctx.cred)


def netlify_client(ctx):
    n = ctx.cfg["netlify"]
    return web.Netlify(core.load_dpapi_secret(n["token_file"]), n["site_id"], api=n.get("api") or web.API, sleep=ctx.sleep)


def netlify_dir(ctx, rid) -> Path:
    """Staging for everything Netlify and the reader downloads get: deploy/index.html (the pinned invited desk),
    deploy/pocket/index.html and version.json (security_gate's envelope check keys on a `deploy` path part)."""
    return ctx.dir(rid) / "netlify" / "deploy"


def netlify_files(ctx, rid) -> dict | None:
    """Netlify path -> staged file, or None when no invited-desk lock was pinned this release (never the full desk)."""
    folder = netlify_dir(ctx, rid)
    index = folder / "index.html"
    if not manifest.is_lock_envelope(index):
        return None
    files = {"/index.html": index}
    pocket = folder / "pocket" / "index.html"
    if pocket.is_file() and manifest.is_lock_envelope(pocket):
        files["/pocket/index.html"] = pocket
    version = {"release_id": rid, "index_sha256": core.sha256_file(index),
               "pocket_sha256": core.sha256_file(pocket) if "/pocket/index.html" in files else None}
    core.atomic_write(folder.parent / "version.json", core.canonical_json(version))
    files["/version.json"] = folder.parent / "version.json"
    return files


def step_netlify(ctx, st, rid):
    files = netlify_files(ctx, rid)
    if files is None:
        ctx.say("  netlify skipped: no lock of the invited desk was pinned this release; the full desk is never uploaded")
        return
    ctx.outward("netlify", "netlify publish", "netlify.site_id")
    api = netlify_client(ctx)
    if st.get("netlify"):                                                       # external fact first: is it already live?
        site = api.site_url()
        code, body = web.http_get(site + "/version.json")
        if code == 200 and web.served_release_id(body) == rid and not api.check(site, files):
            return
    result = api.release(files, publish=True, release_id=rid)
    live = Path(ctx.cfg["melange_live"])
    web.confirm_receipt(live / "data" / "engine" / "dashboard_deployment_receipt.json", files["/index.html"],
                        result["deploy_id"], result["published_utc"])
    st["netlify"] = {"deploy_id": result["deploy_id"], "index_sha256": core.sha256_file(files["/index.html"]),
                     "pocket_sha256": core.sha256_file(files["/pocket/index.html"]) if "/pocket/index.html" in files else None}


def step_undraft(ctx, st):
    ctx.outward("undraft", "publish GitHub releases")
    for item in (st.get("releases") or {}).values():
        release = run_json("gh", "api", f"repos/{item['repo']}/releases/{item['id']}")
        if release.get("draft"):
            core.run(["gh", "api", "-X", "PATCH", f"repos/{item['repo']}/releases/{item['id']}", "-F", "draft=false"])


def do_run(ctx, args, resume=None) -> int:
    cfg, kind = ctx.cfg, "data-only" if args.data_only else "full"
    ctx.root.mkdir(parents=True, exist_ok=True)
    rid = resume or core.next_release_id(known_ids(ctx))
    st = State(ctx.dir(rid) / "state.json")
    if st.get("kind") and st["kind"] != kind:
        raise ReleaseError("KIND_MISMATCH", f"{rid} was started as {st['kind']}")
    st.setdefault("id", rid)
    st.setdefault("kind", kind)
    st.setdefault("commit_unix", int(time.time()))
    st["gates"] = st.get("gates", {})
    ctx.emit.run_id = rid
    ctx.emit("release.run", "started", f"release {rid} ({kind})")
    full, say = kind == "full", ctx.say
    say(f"release {rid} ({kind})")
    try:
        serve = json.loads(SERVE_SETS.read_text(encoding="utf-8"))
        # locally buildable artifacts first: Pocket + reader downloads become part of the data snapshot
        pre = core.step(ctx.emit, "gates", lambda: gates.run_gates(
            cfg, skip_kernel=args.skip_kernel, netlify=not args.no_netlify, governance_override=args.governance_override,
            serve_sets=serve, census_override=args.census_override,
            need_bytes=sum(f["size"] for f in data.resolve_files(cfg, serve) if data.needs_staging(f["src"]))))
        st["gates"].update(pre)
        st.save()
        if not gates.all_ok(pre):
            raise ReleaseError("GATES_FAILED", ", ".join(k for k, g in pre.items() if not g["ok"]))
        if full:
            core.step(ctx.emit, "export", step_export, ctx, st, rid)
            st.save()
        pocket_out = desk_out = None
        stage_dir = netlify_dir(ctx, rid)                                         # deploy/ so the gate's envelope check applies
        try:
            core.step(ctx.emit, "pocket", manifest.build_pocket, cfg, stage_dir / "pocket" / "index.html", manifest.dashboard_password(cfg))
            pocket_out = stage_dir / "pocket" / "index.html"
        except NotConfigured as note:
            say(f"  pocket skipped: {note.message}")
        try:                                                                      # D-171/R3: only the invited desk ever leaves
            core.step(ctx.emit, "desk", manifest.pin_invited_desk, cfg, stage_dir / "index.html")
            desk_out = stage_dir / "index.html"
        except manifest.NoInvitedLock as note:
            say(f"  desk omitted from downloads and Netlify: {note.message}")
        extras = manifest.build_downloads(cfg, rid, ctx.dir(rid) / "package", desk_out, pocket_out,
                                          serve["systems"]["melange"]["data_subdir"])
        for note in extras["notes"]:
            say(f"  {note}")
        core.step(ctx.emit, "package gate", step_package_gate, ctx, st, rid)
        st.save()
        files = data.resolve_files(cfg, serve, extra=extras["extra"])
        stage = ctx.root / "staging" / rid / "farm"
        lock = core.load_live_module(Path(cfg["melange_live"]) / "fec" / "pipeline_lock.py", "pipeline_lock")
        lease = Path(cfg["melange_live"]) / ".pipeline_run.lock"
        with lock.pipeline_run_lock(path=lease, owner_kind="release"):          # no pipeline or weekly writer can start
            core.step(ctx.emit, "capture", step_capture, ctx, st, rid, files, stage)
            core.step(ctx.emit, "farm gate", step_farm_gate, ctx, st, stage)
            st.save()
            if full:
                core.step(ctx.emit, "git push", step_push_git, ctx, st, rid)
                st.save()
            core.step(ctx.emit, "data push", step_data_push, ctx, st, rid, files, stage)
            st.save()
        if full:
            core.step(ctx.emit, "ci", step_ci, ctx, st, rid)
            st.save()
        doc = core.step(ctx.emit, "manifest", sign_and_stage_manifest, ctx, st, rid)
        core.step(ctx.emit, "current", step_current, ctx, rid)
        core.step(ctx.emit, "inbox", step_inbox, ctx, rid)
        if not args.no_netlify:
            core.step(ctx.emit, "netlify", step_netlify, ctx, st, rid)
            st.save()
        if full:
            core.step(ctx.emit, "undraft", step_undraft, ctx, st)
        shutil.rmtree(ctx.root / "staging" / rid, ignore_errors=True)
        st.save()
        ctx.emit("release.run", "completed", f"release {rid} published ({doc['kind']})")
        say(f"release {rid} complete; run `status` to watch the VPS apply it")
        return EXIT["ok"]
    except RuntimeError as error:
        if "already running" not in str(error):                               # the pipeline lease refused us
            raise
        st["last_error"] = {"code": "PIPELINE_BUSY", "message": str(error)[:300]}
        st.save()
        ctx.emit("release.run", "failed", f"{rid}: PIPELINE_BUSY")
        say(f"FAILED PIPELINE_BUSY: {error}")
        return EXIT["failed"]
    except Pending as stop:
        st.save()
        ctx.emit("release.run", "blocked", f"{rid}: pending confirmation of {stop.step}")
        say(f"pending confirmation: {stop.step}. Local steps are done; rerun with --confirm-outward naming every step "
            f"({','.join(CONFIRM_STEPS)}) (`resume {rid} --confirm-outward ...`).")
        return EXIT["pending"]
    except NotConfigured as stop:
        st.save()
        ctx.emit("release.run", "blocked", f"{rid}: not configured")
        say(f"not configured: {stop.message}")
        return EXIT["not_configured"]
    except ReleaseError as error:
        st["last_error"] = {"code": error.code, "message": error.message[:300]}
        st.save()
        ctx.emit("release.run", "failed", f"{rid}: {error.code}", detail=error.message)
        say(f"FAILED {error.code}: {error.message}")
        return EXIT["failed"]


# ------------------------------------------------------------------ images (code only)
def init_remote(ctx) -> int:
    """Create the private platform repo on GitHub unless `gh repo view` already sees it, and point the bare repo's origin at it."""
    ctx.outward("repo", "create the platform GitHub repo", "platform_repo", "platform_remote_url")
    cfg = ctx.cfg
    if core.run(["gh", "repo", "view", cfg["platform_repo"]], check=False).returncode == 0:
        ctx.say(f"{cfg['platform_repo']} already exists")
    else:
        core.run(["gh", "repo", "create", cfg["platform_repo"], "--private", "--disable-wiki", "--description", REPO_DESCRIPTION])
        ctx.say(f"created private repo {cfg['platform_repo']}")
    export.ensure_bare(cfg["platform_bare"], cfg["platform_repo"], cfg["platform_remote_url"])
    ctx.say(f"origin of {cfg['platform_bare']} is {cfg['platform_remote_url']}")
    return EXIT["ok"]


def step_images_push(ctx, st, rid):
    """`git` = platform main + tag; `geo` = Geo's image branch (same name there) + tag. Each needs its own confirmation."""
    if ctx.cfg.get("terrier_images_ref") == ctx.cfg["terrier_ref"]:                  # config error: stop before anything is pushed
        raise ReleaseError("GEO_REF", f"terrier_images_ref is the full release's branch ({ctx.cfg['terrier_ref']}); the image build has its own")
    ctx.outward("git", "git push", "platform_repo")
    push_platform(ctx, st, rid)
    if st.get("geo_reused"):                                                        # auto: Geo's branch has not moved, its images stand
        return
    ctx.outward("geo", "geo push", "terrier_repo", "terrier_images_ref")
    push_geo(ctx, st, rid, ctx.cfg["terrier_images_ref"])


def code_steps(ctx, st, rid):
    """export + security gate, push, CI digests: what `images` and the automatic code release both do. State and events as ever."""
    try:
        for name, fn in (("export", step_export), ("git push", step_images_push), ("ci", step_ci)):
            core.step(ctx.emit, name, fn, ctx, st, rid)
            st.save()
    except Pending as stop:
        st.save()
        ctx.emit("release.run", "blocked", f"{rid}: pending confirmation of {stop.step}")
        raise
    except ReleaseError as error:
        st["last_error"] = {"code": error.code, "message": error.message[:300]}
        st.save()
        ctx.emit("release.run", "failed", f"{rid}: {error.code}", detail=error.message)
        raise


def build_images(ctx, args) -> int:
    cfg, say = ctx.cfg, ctx.say
    rid = args.id or core.next_release_id(known_ids(ctx))
    core.parse_id(rid)
    st = State(ctx.dir(rid) / "state.json")
    if st.get("kind") and st["kind"] != "images":
        raise ReleaseError("KIND_MISMATCH", f"{rid} was started as {st['kind']}")
    st.update(id=rid, kind="images", code_only=CODE_ONLY, gates=st.get("gates", {}))
    st.setdefault("commit_unix", int(time.time()))
    ctx.emit.run_id = rid
    ctx.emit("release.run", "started", f"images {rid}")
    say(f"images {rid}: {CODE_ONLY['why']}")
    code_steps(ctx, st, rid)
    for system in manifest.IMAGE_NAMES:
        say(f"  {cfg['images'][system]}@{st['digests'][system]}")
    for label, urls in st["runs"].items():
        for url in urls:
            say(f"  run {label}: {url}")
    ctx.emit("release.run", "completed", f"images {rid} built")
    say(f"images {rid} built; the GitHub releases stay drafts, nothing was signed or sent to the VPS or Netlify")
    return EXIT["ok"]


def cmd_images(ctx, args) -> int:
    """Code only: export + security gate, push the platform repo and Geo's image branch, wait for CI, print image@digest."""
    ctx.root.mkdir(parents=True, exist_ok=True)
    try:
        return init_remote(ctx) if args.init_remote else build_images(ctx, args)
    except Pending as stop:
        ctx.say(f"pending confirmation: {stop.step}. Rerun with --confirm-outward {'repo' if args.init_remote else 'git,geo'}.")
        return EXIT["pending"]


# ------------------------------------------------------------------ auto (automatic propagation)
def last_completed(ctx):
    done = completed(ctx)
    return (done[-1], load_manifest(ctx, done[-1])) if done else (None, None)


def baseline(ctx, st):
    """(heads, platform commit) last propagated: auto_state.json, else what the newest signed manifest was built from."""
    if st.get("propagated"):
        return st["propagated"], st.get("platform_commit")
    _rid, doc = last_completed(ctx)
    if not doc:
        return {}, None
    return {k: doc["systems"][k]["source"]["commit"] for k in ("melange", "mentat", "terrier")}, doc["platform_repo"]["commit"]


def alert(ctx, st, key, message):
    """Say it, put it on the Glass Box and ping Healthchecks /fail, once per distinct problem (every 15 minutes would be noise)."""
    ctx.say(f"ALERT {key}: {message}")
    mark = f"{key}: {message}"[:300]
    if st.get("last_alert") != mark:
        ctx.emit("release.auto", "failed", mark)
        url = auto.settings(ctx.cfg)["healthchecks_url"]
        if url:
            ctx.hooks.ping(url.rstrip("/") + "/fail")
        st["last_alert"] = mark
    st.save()


def blob_reader(ctx, plan):
    """Text of an exported file, read from the source repo it came from (no checkout, no eol conversion)."""
    repos = {"melange": ctx.cfg["melange_live"], "mentat": ctx.cfg["mentat_live"]}
    return lambda path: core.git_text("cat-file", "blob", plan["entries"][path][1], cwd=repos[plan["source_of"][path]])


def image_hashes(ctx, plan) -> dict:
    """{image: input hash} for the exported tree: what CI will look for as the tag inputs-<hash>. {} before image_inputs.json is exported."""
    if "ops/image_inputs.json" not in plan["entries"]:
        return {}
    read = blob_reader(ctx, plan)
    try:
        return inputs.all_hashes(plan["entries"], json.loads(read("ops/image_inputs.json")), read)
    except (inputs.InputsError, ValueError) as error:       # report-only here: CI hashes on its own and fails its step if it cannot
        ctx.say(f"image inputs not tracked this pass: {error}")
        return {}


def tree_changed(ctx, plan, platform_commit) -> bool:
    """Is the exported tree different from the platform commit that was last propagated? Unknown means changed."""
    bare = Path(ctx.cfg["platform_bare"])
    if not platform_commit or not (bare / "HEAD").is_file() or not export.rev(bare, platform_commit):
        return True
    return export.ls_tree(None, platform_commit, git_dir=bare) != plan["entries"]


def vps_configured(cfg) -> bool:
    return not core.require(cfg, "domain", "hosts.ssh", "vps.ssh_user", "credentials.svc_push")


def auto_code(ctx, st, heads, propagated, platform_commit) -> int:
    """A code release: export + gate, push, CI (images that did not change are reused there), sign, publish. Idempotent per id."""
    cfg, say, now = ctx.cfg, ctx.say, ctx.now()
    why = auto.signing_unavailable(cfg, ctx.hooks)                          # before an id, a tag or a push is spent
    if why:
        alert(ctx, st, "signing", why)
        return EXIT["not_configured"]
    prev_id, base = last_completed(ctx)
    if not base:
        alert(ctx, st, "base", "no completed release to carry the data snapshot from: run one full release by hand first (release.py run)")
        return EXIT["not_configured"]
    plan = export.plan_export(cfg)
    resuming = st.get("inflight") if (st.get("inflight") or {}).get("heads") == heads else None
    geo_changed = heads["terrier"] != propagated.get("terrier")
    if not (resuming or geo_changed or tree_changed(ctx, plan, platform_commit)):
        st["propagated"] = {**plan["heads"], "terrier": heads["terrier"]}
        st.save()
        say("no exported change (the commits touch nothing that is released): marked propagated")
        return EXIT["ok"]
    hashes = image_hashes(ctx, plan)
    same = sorted(n for n, h in hashes.items() if st.get("image_inputs", {}).get(n) == h)
    say("images: " + (", ".join(f"{n} {'reuse' if n in same else 'build'}" for n in sorted(hashes)) or "inputs not tracked yet")
        + ("; terrier unchanged, reused" if not geo_changed else "; terrier changed"))
    rid = resuming["id"] if resuming else core.next_release_id(known_ids(ctx))
    if not resuming:
        auto.record_start(st, now)
    st["inflight"] = {"id": rid, "heads": heads}
    st.save()
    rel = State(ctx.dir(rid) / "state.json")
    if rel.get("kind") and rel["kind"] != "code-only":
        raise ReleaseError("KIND_MISMATCH", f"{rid} was started as {rel['kind']}")
    rel.update(id=rid, kind="code-only", code_only=CODE_ONLY, gates=rel.get("gates", {}))
    rel.setdefault("commit_unix", int(now))
    if geo_changed:
        rel.pop("geo_reused", None)
    else:                                                                 # Geo's branch has not moved: its images stand as signed before
        old = base["systems"]["terrier"]
        rel.update(geo_reused=True, terrier_commit=old["source"]["commit"], carried_digests={"terrier": old["image"].split("@")[1]},
                   carried_images={n: r for n, r in current.images_of(base).items() if n.startswith(auto.GEO_PREFIX) and n != manifest.IMAGE_NAMES["terrier"]})
    ctx.emit.run_id = rid
    ctx.emit("release.run", "started", f"auto code release {rid}")
    say(f"auto code release {rid}: {CODE_ONLY['why']}")
    try:
        code_steps(ctx, rel, rid)
        why = auto.signing_unavailable(cfg, ctx.hooks)                    # CI took a while: the agent may have gone
        if why:
            raise NotConfigured(why)
        core.step(ctx.emit, "manifest", sign_and_stage_manifest, ctx, rel, rid)
        core.step(ctx.emit, "current", step_current, ctx, rid)
        if vps_configured(cfg):
            core.step(ctx.emit, "inbox", step_inbox, ctx, rid)
        else:
            say("  VPS push skipped: not configured (domain, hosts.ssh, vps.ssh_user, credentials.svc_push)")
        core.step(ctx.emit, "undraft", step_undraft, ctx, rel)
        rel.save()
    except Pending as stop:
        ctx.emit("release.run", "blocked", f"{rid}: pending confirmation of {stop.step}")
        say(f"pending confirmation: {stop.step}. The registered task passes --confirm-outward {','.join(AUTO_STEPS)}.")
        return EXIT["pending"]
    except NotConfigured as stop:                                         # nothing is wrong with the heads: try again next run
        rel.save()
        ctx.emit("release.run", "blocked", f"{rid}: not configured")
        alert(ctx, st, "blocked", stop.message)
        return EXIT["not_configured"]
    except (ReleaseError, OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        code, message = (error.code, error.message) if isinstance(error, ReleaseError) else (type(error).__name__, str(error))
        st["failed"] = {"heads": heads, "code": code, "message": message[:300], "at": core.utc_now(), "id": rid}
        st.pop("inflight", None)                                          # these heads are not tried again until a new commit
        alert(ctx, st, code, f"{rid}: {message[:240]}")
        return EXIT["failed"]
    st["propagated"] = {"melange": rel["heads"]["melange"], "mentat": rel["heads"]["mentat"],
                        "terrier": heads["terrier"] if rel.get("geo_reused") else rel["terrier_commit"]}
    st.update(platform_commit=rel["platform_commit"], image_inputs=hashes or st.get("image_inputs", {}),
              last_release={"id": rid, "kind": "code-only", "at": now})
    for key in ("inflight", "failed", "last_alert"):
        st.pop(key, None)
    st.save()
    ctx.emit("release.run", "completed", f"auto code release {rid} published")
    say(f"auto code release {rid} published")
    if heads["melange"] != propagated.get("melange"):
        ctx.emit("release.restart_needed", "blocked", "Melange console (8841) is running older code: restart it when idle")
    if heads["terrier"] != propagated.get("terrier"):
        ctx.emit("release.restart_needed", "blocked", "Terrier local servers are running older code: restart them when idle")
    if heads["mentat"] != propagated.get("mentat"):
        st["mentat_restart"] = True
        st.save()
    return EXIT["ok"]


def mentat_restart(ctx, st) -> None:
    """After a release that changed Mentat, and again every run until it has happened: replace idle stale consoles only."""
    if not (st.get("mentat_restart") and auto.settings(ctx.cfg)["mentat_restart"]):
        return
    done = auto.restart_idle_mentat(ctx.cfg, ctx.hooks, ctx.emit)
    if not done["deferred"]:
        st.pop("mentat_restart")
        st.save()


def read_refresh(live):
    """(outcome, last_update) of the weekly refresh, as weekly_refresh_status.json says."""
    try:
        doc = json.loads((Path(live) / "data" / "engine" / "weekly_refresh_status.json").read_text(encoding="utf-8"))
        return doc.get("last_outcome"), doc.get("last_update")
    except (OSError, ValueError, AttributeError):
        return None, None


def data_tokens(ctx):
    """(refresh token, invited-build token, age of the newest of them in seconds or None). Tokens change when a refresh
    completes or the invited build is rebuilt."""
    live, now = Path(ctx.cfg["melange_live"]), ctx.now()
    outcome, updated = read_refresh(live)
    refresh = updated if outcome in gates.COMPLETE and updated else None
    invited_file = core.invited_dir(ctx.cfg) / "INVITED_BUILD.json"
    invited = core.sha256_file(invited_file) if invited_file.is_file() else None
    stamps = []
    if refresh:
        try:
            stamps.append(time.mktime(time.strptime(str(refresh)[:19], "%Y-%m-%d %H:%M:%S")))
        except ValueError:
            stamps.append(now)                                            # unreadable time: treat as just changed, wait
    if invited:
        stamps.append(invited_file.stat().st_mtime)
    return refresh, invited, (now - max(stamps) if stamps else None)


def auto_data(ctx, st, args) -> int:
    """A data-only release when a refresh finished COMPLETE or the invited build changed since the last data release. The gates
    (governance ruled, census pinned, kernel tests, locks) decide; a refusal is a note and the next run looks again."""
    cfg, say, now = ctx.cfg, ctx.say, ctx.now()
    _rid, base = last_completed(ctx)
    refresh, invited, age = data_tokens(ctx)
    seen = st.get("data")
    if seen is None:
        st["data"] = {"refresh": refresh, "invited": invited}
        st.save()
        say("data baseline recorded: the next completed refresh or invited build starts a data release")
        return EXIT["ok"]
    if not base or not ((refresh and refresh != seen.get("refresh")) or (invited and invited != seen.get("invited"))):
        return EXIT["ok"]
    tokens = {"refresh": refresh, "invited": invited}
    quiet = auto.settings(cfg)["quiet_minutes"] * 60
    failed = st.get("data_failed")
    if failed and failed.get("tokens") == tokens and not args.force_retry:
        say(f"data release: these inputs already failed ({failed.get('code')}); a new refresh, a new invited build or --force-retry is needed")
        return EXIT["ok"]
    if age is not None and age < quiet:
        say(f"data release: inputs changed {int(age // 60)} min ago; waiting for {quiet // 60} quiet minutes")
        return EXIT["ok"]
    if len([t for t in st.get("releases", []) if now - t < auto.DAY]) >= auto.settings(cfg)["max_per_day"]:
        say("data release: daily release cap reached; not today")
        return EXIT["ok"]
    netlify_ready = bool(cfg["netlify"].get("site_id")) and Path(cfg["netlify"]["token_file"]).is_file()
    want_netlify = netlify_ready and invited is not None and invited != seen.get("invited")
    if not want_netlify:
        say("  netlify not in this data release: " + ("configured, but the invited build did not change" if netlify_ready else "not configured"))
    serve = json.loads(SERVE_SETS.read_text(encoding="utf-8"))
    need = sum(f["size"] for f in data.resolve_files(cfg, serve) if data.needs_staging(f["src"]))
    cheap = gates.run_gates(cfg, skip_kernel=True, netlify=want_netlify, need_bytes=need, serve_sets=serve)      # the kernel tests wait for the rest
    if not gates.all_ok(cheap):
        say("data release not started: " + "; ".join(f"{k}: {g['detail'][:100]}" for k, g in cheap.items() if not g["ok"]))
        return EXIT["ok"]
    resume = (st.get("data_inflight") or {}).get("id") if (st.get("data_inflight") or {}).get("tokens") == tokens else None
    flags = argparse.Namespace(data_only=True, no_netlify=not want_netlify, skip_kernel=False, governance_override=False, census_override=False)
    if not resume:
        auto.record_start(st, now)
    crash = None
    try:
        code = do_run(ctx, flags, resume)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        code, crash = EXIT["failed"], {"code": type(error).__name__, "message": str(error)[:300]}
    rid = resume or max(known_ids(ctx), key=core.parse_id)
    if code == EXIT["ok"]:
        for key in ("data_inflight", "data_failed", "last_alert"):
            st.pop(key, None)
        st.update(data=tokens, last_release={"id": rid, "kind": "data-only", "at": now})
    elif code in (EXIT["pending"], EXIT["not_configured"]):
        st["data_inflight"] = {"id": rid, "tokens": tokens}                # try this id again next run
    else:
        error = crash or State(ctx.dir(rid) / "state.json").get("last_error") or {}
        st["data_failed"] = {"tokens": tokens, "code": error.get("code", "FAILED"), "id": rid, "at": core.utc_now()}
        st.pop("data_inflight", None)
        alert(ctx, st, error.get("code", "FAILED"), f"data release {rid}: {error.get('message', '')[:240]}")
    st.save()
    return code


def cmd_auto(ctx, args) -> int:
    cfg, say, root = ctx.cfg, ctx.say, ctx.root
    root.mkdir(parents=True, exist_ok=True)
    if args.pause or args.unpause:
        auto.set_pause(root, args.pause, ctx.now())
        say("auto is paused: `release.py auto --unpause` resumes it" if args.pause else "auto is running again")
        ctx.emit("release.auto", "completed", "auto " + ("paused" if args.pause else "resumed"))
        return EXIT["ok"]
    with auto.single_instance(root / auto.LOCK) as mine:
        if not mine:
            say("another auto run is active")
            return EXIT["ok"]
        st = State(root / auto.STATE)
        url = auto.settings(cfg)["healthchecks_url"]
        flag = auto.paused(root)
        if flag:
            say(f"auto is paused ({flag}); `release.py auto --unpause` resumes it")
            code = EXIT["ok"]
        else:
            code = auto_run(ctx, st, args)
        stuck = st.get("failed") or st.get("data_failed")                       # unresolved failures must not read as healthy
        if code == EXIT["ok"] and url and not args.plan and not stuck:
            ctx.hooks.ping(url)
        return code


def auto_run(ctx, st, args) -> int:
    cfg, say, now = ctx.cfg, ctx.say, ctx.now()
    ctx.emit.run_id = ""
    try:
        heads = auto.head_commits(cfg)
    except ReleaseError as error:
        alert(ctx, st, "heads", f"cannot read the three heads: {error.message[:200]}")
        return EXIT["failed"]
    propagated, platform_commit = baseline(ctx, st)
    settings = auto.settings(cfg)
    newest = auto.newest_change(cfg, heads, propagated) if heads != propagated else None
    action, why = auto.decide(heads, propagated, st, now=now, newest=newest, quiet_s=settings["quiet_minutes"] * 60,
                              cap=settings["max_per_day"], force_retry=args.force_retry)
    say(f"auto: {action}: {why}")
    if args.plan:
        problem = auto.signing_unavailable(cfg, ctx.hooks)
        say("  signing: " + (problem or "ready"))
        refresh, invited, _age = data_tokens(ctx)
        seen = st.get("data") or {}
        say(f"  data: refresh {'new' if refresh and refresh != seen.get('refresh') else 'unchanged'}, "
            f"invited build {'new' if invited and invited != seen.get('invited') else 'unchanged'}"
            + ("" if st.get("data") is not None else " (no baseline yet)"))
        return EXIT["ok"]
    code = EXIT["ok"]
    if action == "go":
        code = auto_code(ctx, st, heads, propagated, platform_commit)
    if code == EXIT["ok"]:
        code = auto_data(ctx, st, args)
    if code == EXIT["ok"]:
        mentat_restart(ctx, st)
        st.pop("last_alert", None)
        st.save()
    ctx.emit("release.auto", "completed" if code == EXIT["ok"] else "blocked" if code in (EXIT["pending"], EXIT["not_configured"]) else "failed",
             f"auto {action}, exit {code}")
    return code


def cmd_current(ctx, args) -> int:
    if args.use:
        core.parse_id(args.use)
        if args.use not in completed(ctx):
            ctx.say(f"{args.use} has no signed manifest here: nothing to point at")
            return EXIT["failed"]
        doc = current.publish(ctx.cfg, ctx.dir(args.use))
        ctx.emit("release.current", "completed", f"current manifest is now {doc['release_id']} ({doc['kind']})")
        ctx.say(f"current is now {doc['release_id']}. `release.py auto --pause` first if you do not want the next change to replace it.")
    else:
        doc = current.load(ctx.cfg)
        ctx.say(f"current: {doc['release_id']} ({doc['kind']}), platform {doc['platform_repo']['tag']}")
        for name, ref in sorted(current.images_of(doc).items()):
            ctx.say(f"  {name}: {ref}")
    return EXIT["ok"]


# ------------------------------------------------------------------ status
def status_rows(ctx, rid=None) -> list[tuple[str, str, str]]:
    cfg, rows = ctx.cfg, []
    rid = rid or (completed(ctx) or [None])[-1]
    if not rid:
        return [("release", "fail", "no completed release found")]
    doc, drid = load_manifest(ctx, rid), rid
    while (load_manifest(ctx, drid) or {}).get("kind") == "code-only" and load_manifest(ctx, drid).get("previous"):
        drid = load_manifest(ctx, drid)["previous"]             # Netlify and Pocket carry data: the newest release that did
    st = State(ctx.dir(drid) / "state.json")

    def row(name, fn):
        try:
            rows.append((name, *fn()))
        except NotConfigured as note:
            rows.append((name, "not configured", note.message[:120]))
        except Exception as error:                                          # a status row must never raise
            rows.append((name, "fail", f"{type(error).__name__}: {str(error)[:100]}"))

    def github():
        release = gh_release(cfg["platform_repo"], f"v{rid}")
        if not release:
            return "fail", f"no release v{rid}"
        return ("stale", "still a draft") if release.get("draft") else ("ok", f"{len(release.get('assets', []))} assets")

    def vps():
        got = data.fetch_status(cfg, ctx.dir(rid), ctx.cred)
        return ("ok", f"applied {got.get('release_id')}") if got.get("release_id") == rid else ("stale", f"VPS reports {got.get('release_id')}")

    def app(host_key, system):
        def check():
            core.need(cfg, "domain", f"hosts.{host_key}", "credentials.svc_status")
            ident, secret = ctx.cred(cfg["credentials"]["svc_status"])
            status, body = web.http_get(f"https://{cfg['hosts'][host_key]}.{cfg['domain']}/version",
                                        {"CF-Access-Client-Id": ident, "CF-Access-Client-Secret": secret.reveal()})
            if status != 200:
                return "fail", f"HTTP {status}"
            version, want = json.loads(body), (doc or {}).get("systems", {}).get(system, {}).get("source", {}).get("commit", "")
            if system != "terrier":         # the melange and mentat images are built from the platform commit (release.yml GIT_SHA)
                want = ((doc or {}).get("image_commits") or {}).get(manifest.IMAGE_NAMES[system]) or (doc or {}).get("platform_repo", {}).get("commit", "")
            if want and version.get("git_sha") and not want.startswith(str(version["git_sha"])[:12]):
                return "stale", f"serving {str(version['git_sha'])[:9]}, release has {want[:9]}"
            return "ok", f"git {str(version.get('git_sha', '?'))[:9]}"
        return check

    def netlify():
        core.need(cfg, "netlify.site_id")
        site = netlify_client(ctx).site_url()
        code, body = web.http_get(site + "/version.json")
        if code != 200:
            return "fail", f"/version.json HTTP {code}"
        if web.served_release_id(body) != drid:
            return "stale", f"serving {web.served_release_id(body)}, want {drid}"
        want = (st.get("netlify") or {}).get("index_sha256")
        return ("ok", "version and served hash match") if want and web.sha256_url(site + "/") == want else ("fail", "served hash differs or unknown")

    def pocket():
        core.need(cfg, "netlify.site_id")
        want = (st.get("netlify") or {}).get("pocket_sha256")
        if not want:
            return "not configured", "no Pocket in this release"
        site = netlify_client(ctx).site_url()
        return ("ok", "served hash matches") if web.sha256_url(site + "/pocket/") == want else ("fail", "served hash differs")

    def launcher():
        core.need(cfg, "launchers.script")
        kit = (doc or {}).get("packages", {}).get("Install-MelangeApps.ps1")
        if not kit:
            return "not configured", "release has no Install-MelangeApps.ps1"
        return ("ok", "matches the kit") if core.sha256_file(cfg["launchers"]["script"]) == kit["sha256"] else ("stale", "launcher script drifted from the kit")

    row("github release", github)
    row("vps status", vps)
    for host_key, system in (("melange", "melange"), ("mentat", "mentat"), ("co05", "terrier"), ("or05", "terrier"), ("ct01", "terrier")):
        row(f"app {host_key}", app(host_key, system))
    row("netlify desk", netlify)
    row("netlify pocket", pocket)
    row("launcher", launcher)
    return rows


def cmd_status(ctx, args) -> int:
    rows = status_rows(ctx, args.id)
    for name, state, detail in rows:
        ctx.say(f"  {state:<14} {name}: {detail}")
    allok = all(state == "ok" for _n, state, _d in rows)
    ctx.emit("release.status", "completed" if allok else "failed", "status " + ("all ok" if allok else "not all ok"),
             metadata={"rows": {n: s for n, s, _d in rows}})
    return EXIT["ok"] if allok else EXIT["failed"]


# ------------------------------------------------------------------ small commands
def cmd_verify(ctx, args) -> int:
    allowed = args.allowed_signers or ctx.cfg["signing"].get("allowed_signers", "")
    allowed = Path(allowed) if Path(allowed).is_absolute() else core.OPS.parent / allowed
    try:
        result = manifest.verify_manifest(args.manifest, allowed, principal=ctx.cfg["signing"]["principal"],
                                          namespace=ctx.cfg["signing"]["namespace"], current_id=args.current, snapshot_path=args.snapshot)
    except ReleaseError as error:
        ctx.say(f"REFUSED {error.code}: {error.message}")
        return EXIT["failed"]
    ctx.say(f"{result['action'].upper()}: {result['release_id']} verifies")
    return EXIT["ok"]


def cmd_init_signing(ctx, _args) -> int:
    s = ctx.cfg["signing"]
    key = s["key"].replace("/", "\\")
    ctx.say(f"""This command never generates keys. Run these yourself, in order:

  1. ssh-keygen -t ed25519 -f "{key}" -C {s['principal']}
  2. (once per boot) ssh-add "{key}"          # the release command signs through ssh-agent
  3. Add the public key to ops/vps/allowed_signers as ONE line:
       {s['principal']} namespaces="{s['namespace']}" <contents of {key}.pub>
     e.g. {s['principal']} namespaces="{s['namespace']}" ssh-ed25519 AAAA...
  4. Commit that file on main, and put the same line in the VPS cloud-init trust file.
  5. Back up the private key through Carbonite; it is the root of trust and must never leave the laptop.""")
    pub = Path(s["key"] + ".pub")
    if pub.is_file():
        ctx.say(f"\nThe public key already exists; your allowed_signers line is:\n  {s['principal']} namespaces=\"{s['namespace']}\" {pub.read_text(encoding='utf-8').strip()}")
    return EXIT["ok"]


def cmd_store_netlify_token(ctx, _args) -> int:
    value = getpass.getpass("Netlify personal access token (input hidden): ").strip()
    if not value:
        ctx.say("nothing entered; nothing stored")
        return EXIT["failed"]
    core.store_dpapi_secret(ctx.cfg["netlify"]["token_file"], value)
    ctx.say(f"stored, encrypted for this Windows user, at {ctx.cfg['netlify']['token_file']}")
    return EXIT["ok"]


def confirm_steps(text, allowed=CONFIRM_STEPS):
    steps = [part.strip() for part in text.split(",") if part.strip()]
    unknown = [step for step in steps if step not in allowed]
    if not steps or unknown:
        raise argparse.ArgumentTypeError(f"{', '.join(unknown) or text!r}: name steps from {','.join(allowed)}")
    return frozenset(steps)


def build_parser():
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    def flags(p, confirm=True):
        p.add_argument("--data-only", action="store_true")
        p.add_argument("--no-netlify", action="store_true")
        p.add_argument("--skip-kernel", action="store_true", help="skip fec/tests/run_tests.py (recorded in the gates)")
        p.add_argument("--census-override", action="store_true",
                       help="ship serve sets whose census is still pending (serve_sets.json census_pending; recorded in the gates)")
        p.add_argument("--governance-override", action="store_true",
                       help="accept an invited build whose governance is not 'ruled' (Paul-only testing; recorded in the gates)")
        if confirm:
            p.add_argument("--confirm-outward", type=confirm_steps, default=frozenset(), metavar="STEPS",
                           help="allow these steps that leave the laptop: a comma list of " + ",".join(CONFIRM_STEPS))
    flags(sub.add_parser("plan"), confirm=False)
    flags(sub.add_parser("run"))
    p = sub.add_parser("resume")
    p.add_argument("id")
    flags(p)
    p = sub.add_parser("status")
    p.add_argument("id", nargs="?")
    p = sub.add_parser("verify")
    p.add_argument("manifest")
    p.add_argument("--allowed-signers")
    p.add_argument("--current")
    p.add_argument("--snapshot")
    p = sub.add_parser("images", help="code-only build of the three app images (no data, VPS or Netlify)")
    p.add_argument("--id", help="CalVer id to (re)use; default is the next one")
    p.add_argument("--init-remote", action="store_true", help="create the private platform GitHub repo (if absent) and set the bare repo's origin; needs --confirm-outward repo")
    p.add_argument("--confirm-outward", type=lambda text: confirm_steps(text, IMAGE_STEPS), default=frozenset(), metavar="STEPS",
                   help="allow these steps that leave the laptop: a comma list of " + ",".join(IMAGE_STEPS))
    p = sub.add_parser("auto", help="propagate whatever changed to every platform (the registered task runs this)")
    p.add_argument("--plan", action="store_true", help="say what would happen; change nothing")
    p.add_argument("--force-retry", action="store_true", help="try heads or data inputs that already failed (the cap and the gates still apply)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--pause", action="store_true", help="write the pause flag: auto does nothing until --unpause")
    group.add_argument("--unpause", action="store_true")
    p.add_argument("--confirm-outward", type=lambda text: confirm_steps(text, AUTO_STEPS), default=frozenset(), metavar="STEPS",
                   help="allow these steps that leave the laptop: a comma list of " + ",".join(AUTO_STEPS))
    p = sub.add_parser("current", help="show the manifest compute jobs resolve their images through, or point it at an earlier release")
    p.add_argument("--use", metavar="ID", help="make this completed release current (rollback; verifies its signature)")
    sub.add_parser("init-signing")
    sub.add_parser("store-netlify-token")
    return parser


def main(argv=None, *, cfg=None, emit=None, cred=core.read_credential, sleep=time.sleep, say=print, now=time.time, hooks=None) -> int:
    for stream in (sys.stdout, sys.stderr):    # a CI log tail can hold characters a cp1252 console cannot encode: never crash on print
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    cfg = cfg or core.load_config()
    ctx = Ctx(cfg, confirm=getattr(args, "confirm_outward", frozenset()), emit=emit, cred=cred, sleep=sleep, say=say, now=now, hooks=hooks)
    try:
        if args.cmd == "plan":
            return cmd_plan(ctx, args)
        if args.cmd in ("run", "resume"):
            if args.cmd == "resume":
                state = ctx.dir(args.id) / "state.json"
                if not state.is_file():
                    say(f"no such release: {args.id}")
                    return EXIT["failed"]
                args.data_only = json.loads(state.read_text(encoding="utf-8")).get("kind") == "data-only"
            return do_run(ctx, args, getattr(args, "id", None) if args.cmd == "resume" else None)
        return {"images": cmd_images, "auto": cmd_auto, "current": cmd_current, "status": cmd_status, "verify": cmd_verify,
                "init-signing": cmd_init_signing, "store-netlify-token": cmd_store_netlify_token}[args.cmd](ctx, args)
    except ReleaseError as error:
        say(f"FAILED {error.code}: {error.message}")
        return EXIT["not_configured"] if isinstance(error, NotConfigured) else EXIT["failed"]


if __name__ == "__main__":
    raise SystemExit(main())
