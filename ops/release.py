#!/usr/bin/env python3
"""Laptop-side release command for Melange / Mentat / Terrier. See ops/RELEASE.md.

    python ops/release.py plan   [--data-only] [--no-netlify] [--skip-kernel]
    python ops/release.py run    [--data-only] [--no-netlify] [--skip-kernel] [--confirm-outward git,data,manifest,netlify,undraft]
    python ops/release.py resume <id> [same flags]
    python ops/release.py images [--confirm-outward git,geo] [--id <CalVer>]    # code only: build the app images, no data/VPS/Netlify
    python ops/release.py images --init-remote --confirm-outward repo         # create the private platform repo, set the bare origin
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
from ops.release_lib import core, data, export, gates, manifest, web    # noqa: E402
from ops.release_lib.core import NotConfigured, Pending, ReleaseError, State   # noqa: E402

SERVE_SETS = core.OPS / "serve_sets.json"
CONFIRM_STEPS = ("git", "data", "manifest", "netlify", "undraft")           # the outward steps --confirm-outward can name
IMAGE_STEPS = ("git", "geo", "repo")                                           # what `images --confirm-outward` can name
CI_TIMEOUT = 45 * 60                                                          # one `gh run watch`; the run itself is bounded by GitHub
REPO_DESCRIPTION = "Melange platform (source-only release exports)"
CODE_ONLY = {"gates_evaluated": ["tag_collision", "security_gate"],
             "gates_not_evaluated": ["pipeline_lock", "weekly_lock", "mentat_lane_writers", "repos_clean", "kernel_tests", "weekly_refresh",
                                     "invited_build", "census", "receipt_hash", "disk", "security_packages", "security_farm"],
             "why": "code-only image build: the images are built by CI from committed objects and no data leaves the laptop, so the "
                    "live-tree, refresh, invited-build and data gates do not apply"}
EXIT = {"ok": 0, "failed": 1, "usage": 2, "pending": 3, "not_configured": 4}


class Ctx:
    def __init__(self, cfg, *, confirm=frozenset(), emit=None, cred=core.read_credential, sleep=time.sleep, say=print):
        self.cfg, self.confirm, self.cred, self.sleep, self.say = cfg, confirm, cred, sleep, say
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
    digests, packages, releases, run_urls = {}, {}, {}, {}
    for label, repo, commit in (("platform", cfg["platform_repo"], st["platform_commit"]),
                                ("geo", cfg["terrier_repo"], st["terrier_commit"])):
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
        packages.update({(name if label == "platform" else f"{label}-{name}"): {"sha256": a["sha256"], "bytes": a["bytes"]}
                         for name, a in table.items()})
        releases[label] = {"repo": repo, "id": release["id"], "draft": bool(release.get("draft"))}
    if set(digests) != set(manifest.IMAGE_NAMES):
        raise ReleaseError("DIGESTS_MISSING", f"images missing from digests.json: {sorted(set(manifest.IMAGE_NAMES) - set(digests))}")
    st.update(digests=digests, packages=packages, releases=releases, runs=run_urls)


BOT = "github-actions[bot]"


def verify_provenance(repo, commit, tag, release, doc):
    """Before anything is signed (PROVENANCE, fail closed): these digests are the release workflow's own for exactly `commit`.
    digests.json names the commit and tag; a successful push run of that tag has head_sha == commit; the release and every
    one of its assets were created by github-actions[bot] inside that run's window, so a hand-made release or a swapped
    asset fails. A compromised CI run itself is NOT detected (ops/RELEASE.md). Anything missing is unverifiable, hence refused."""
    def refuse(why):
        raise ReleaseError("PROVENANCE", f"{repo} {tag}: {why}")
    if not isinstance(doc, dict) or doc.get("commit") != commit or doc.get("tag") != tag:
        refuse("digests.json does not name this commit and tag")
    try:
        runs = run_json("gh", "api", f"repos/{repo}/actions/runs?head_sha={commit}")["workflow_runs"]
        windows = [(r.get("run_started_at") or r.get("created_at"), r["updated_at"]) for r in runs
                   if r.get("head_sha") == commit and r.get("head_branch") == tag and r.get("event") == "push"
                   and r.get("conclusion") == "success"]
        made, assets = release["created_at"], release["assets"]
        actors = [release["author"]["login"], *(a["uploader"]["login"] for a in assets)]
    except (ReleaseError, KeyError, TypeError, ValueError) as error:
        refuse(f"the workflow runs, release author or asset uploaders cannot be read ({type(error).__name__}: {str(error)[:80]})")
    if not windows:
        refuse(f"no successful push run of this tag has head_sha {commit[:9]}")
    if not isinstance(made, str) or not any(isinstance(a, str) and isinstance(b, str) and a <= made <= b for a, b in windows):
        refuse("the release was not created inside a successful run for this commit")
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


def sign_and_stage_manifest(ctx, st, rid) -> dict:
    cfg, d = ctx.cfg, ctx.dir(rid)
    allowed = Path(cfg["signing"].get("allowed_signers") or "")
    allowed = allowed if allowed.is_absolute() else core.OPS.parent / allowed
    if not allowed.is_file():             # the signature is checked against the VPS trust file before it is sent; no file, no manifest
        raise NotConfigured(f"signing.allowed_signers {cfg['signing'].get('allowed_signers')!r} is not a file; the manifest is "
                            "self-checked against the VPS trust file before it is signed and pushed (ops/vps/allowed_signers)")
    full = st["kind"] == "full"
    earlier = completed(ctx, rid)
    previous = earlier[-1] if earlier else None
    base = load_manifest(ctx, previous) if previous else None
    if not full and not base:
        raise ReleaseError("NO_BASE_RELEASE", "a data-only release needs a completed earlier release to carry its images")
    st.setdefault("manifest_created_utc", core.utc_now())
    commits = {k: (st["heads"][k] if full else base["systems"][k]["source"]["commit"]) for k in ("melange", "mentat")}
    commits["terrier"] = st["terrier_commit"] if full else base["systems"]["terrier"]["source"]["commit"]
    digests = st["digests"] if full else {k: v["image"].split("@")[1] for k, v in base["systems"].items()}
    platform = ({"commit": st["platform_commit"], "tag": f"v{rid}"} if full else base["platform_repo"])
    snapshot = {"restic_snapshot_id": st["snapshot"]["restic_snapshot_id"], "restic_path": st["snapshot"]["restic_path"],
                "manifest_sha256": st["snapshot"]["manifest_sha256"], "files": st["snapshot"]["files"], "bytes": st["snapshot"]["bytes"]}
    doc = manifest.build_manifest(cfg, release_id=rid, previous=previous, kind=st["kind"], created_utc=st["manifest_created_utc"],
                                  platform_commit=platform["commit"], tag=platform["tag"], commits=commits, digests=digests,
                                  snapshot=snapshot, packages=st.get("packages", base["packages"] if base else {}), gates=st["gates"])
    path = d / "manifest.json"
    core.atomic_write(path, core.canonical_json(doc))
    manifest.sign_manifest(cfg, path)
    manifest.verify_manifest(path, allowed, principal=cfg["signing"]["principal"], namespace=cfg["signing"]["namespace"],
                             snapshot_path=d / "snapshot_manifest.json")
    return doc


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
    ctx.outward("geo", "geo push", "terrier_repo", "terrier_images_ref")
    push_geo(ctx, st, rid, ctx.cfg["terrier_images_ref"])


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
    try:
        for name, fn in (("export", step_export), ("git push", step_images_push), ("ci", step_ci)):
            core.step(ctx.emit, name, fn, ctx, st, rid)
            st.save()
    except Pending as stop:
        st.save()
        ctx.emit("release.run", "blocked", f"{rid}: pending confirmation of {stop.step}")
        raise
    except ReleaseError as error:
        st.save()
        ctx.emit("release.run", "failed", f"{rid}: {error.code}", detail=error.message)
        raise
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


# ------------------------------------------------------------------ status
def status_rows(ctx, rid=None) -> list[tuple[str, str, str]]:
    cfg, rows = ctx.cfg, []
    rid = rid or (completed(ctx) or [None])[-1]
    if not rid:
        return [("release", "fail", "no completed release found")]
    doc, st = load_manifest(ctx, rid), State(ctx.dir(rid) / "state.json")

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
                want = (doc or {}).get("platform_repo", {}).get("commit", "")
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
        if web.served_release_id(body) != rid:
            return "stale", f"serving {web.served_release_id(body)}"
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
    sub.add_parser("init-signing")
    sub.add_parser("store-netlify-token")
    return parser


def main(argv=None, *, cfg=None, emit=None, cred=core.read_credential, sleep=time.sleep, say=print) -> int:
    for stream in (sys.stdout, sys.stderr):    # a CI log tail can hold characters a cp1252 console cannot encode: never crash on print
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    cfg = cfg or core.load_config()
    ctx = Ctx(cfg, confirm=getattr(args, "confirm_outward", frozenset()), emit=emit, cred=cred, sleep=sleep, say=say)
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
        return {"images": cmd_images, "status": cmd_status, "verify": cmd_verify, "init-signing": cmd_init_signing,
                "store-netlify-token": cmd_store_netlify_token}[args.cmd](ctx, args)
    except ReleaseError as error:
        say(f"FAILED {error.code}: {error.message}")
        return EXIT["not_configured"] if isinstance(error, NotConfigured) else EXIT["failed"]


if __name__ == "__main__":
    raise SystemExit(main())
