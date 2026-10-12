#!/usr/bin/env python3
"""Deploy the locked invited desk to the existing Netlify site (D-171). Standard library only.

    python ops/deploy_desk.py --site-id SITE [--root LIVE] [--dry-run] [--allow-pending-governance]

Order: preflight (deploy/index.html is a lock envelope, publish_dashboard.py's receipt matches it, and the
receipt's source is the invited build's Moneyball.html) -> stage index.html + version.json -> security gate
(D-169, `ops/security_gate.py export` on the staged folder) -> prompt for the Netlify token (hidden; never
argv, env, disk or logs) -> draft deploy -> upload -> download and sha256-check every file -> publish ->
re-verify production -> extend the receipt with the deploy id (web.confirm_receipt).

--dry-run stops after the gate and never asks for the token or touches the network.
Old deploys are never deleted here; that is Paul's step after this one verifies.
Exit codes: 0 done, 1 failed, 2 usage, 5 Netlify rejected the file (compress-before-encrypt is D-171's fallback).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.release_lib import core, manifest, web  # noqa: E402
from ops.release_lib.core import ReleaseError  # noqa: E402

GATE = Path(__file__).resolve().parent / "security_gate.py"


def preflight(root: Path, allow_pending: bool) -> dict:
    """Facts the upload depends on; any mismatch is a named ReleaseError."""
    index = root / "deploy" / "index.html"
    receipt_path = root / "data" / "engine" / "dashboard_deployment_receipt.json"
    invited_path = core.invited_dir({"melange_live": root}) / "INVITED_BUILD.json"      # the builder's, the release's and serve_sets' one path
    if not manifest.is_lock_envelope(index):
        raise ReleaseError("NOT_LOCKED", f"{index} does not parse as a lock envelope")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    index_sha = core.sha256_file(index)
    if receipt.get("local_deploy_artifact", {}).get("sha256") != index_sha:
        raise ReleaseError("RECEIPT_MISMATCH", "publish_dashboard.py's receipt does not match deploy/index.html")
    invited = json.loads(invited_path.read_text(encoding="utf-8"))
    if receipt.get("source", {}).get("sha256") != invited["outputs"]["Moneyball.html"]["sha256"]:
        raise ReleaseError("NOT_INVITED", "the locked source is not the invited build's Moneyball.html")
    if invited.get("governance") != "ruled" and not allow_pending:
        raise ReleaseError("GOVERNANCE_PENDING", "the invited profile is unruled; pass --allow-pending-governance "
                                                 "only on Paul's say-so")
    return {"index": index, "index_sha256": index_sha, "receipt": receipt_path,
            "governance": invited.get("governance"), "release_id": "desk-" + index_sha[:16]}


def stage(facts: dict, folder: Path) -> dict:
    """<folder>/deploy/index.html (hard link, else copy) + <folder>/version.json -> Netlify path map."""
    target = folder / "deploy" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or core.sha256_file(target) != facts["index_sha256"]:
        target.unlink(missing_ok=True)
        try:
            os.link(facts["index"], target)
        except OSError:
            shutil.copyfile(facts["index"], target)
    core.atomic_write(folder / "version.json", core.canonical_json(
        {"release_id": facts["release_id"], "index_sha256": facts["index_sha256"]}))
    return {"/index.html": target, "/version.json": folder / "version.json"}


def run_gate(folder: Path) -> None:
    """D-169: fail closed. The gate prints PASS/FAIL lines itself and never prints secret values."""
    if subprocess.run([sys.executable, str(GATE), "export", "--target", str(folder)]).returncode != 0:
        raise ReleaseError("SECURITY_GATE", "ops/security_gate.py export failed on the staged desk; nothing uploaded")


def deploy(files: dict, facts: dict, site_id: str, token: core.Secret, api_url: str = web.API) -> dict:
    api = web.Netlify(token, site_id, api=api_url)
    info = api._call("GET", f"/sites/{site_id}")
    site = (info.get("ssl_url") or info.get("url") or "").rstrip("/")
    code, body = web.http_get(site + "/version.json")      # a re-run after publish: verify and confirm only
    if code == 200 and json.loads(body or b"{}").get("release_id") == facts["release_id"] and not api.check(site, files):
        deploy_id = (info.get("published_deploy") or {}).get("id")
        if not deploy_id:
            raise ReleaseError("ALREADY_LIVE", "production serves this desk but Netlify reports no published deploy id")
        result = {"deploy_id": deploy_id, "production_url": site, "published_utc": core.utc_now(), "already_live": True}
        web.confirm_receipt(facts["receipt"], facts["index"], deploy_id, result["published_utc"])
        return result
    result = api.release(files, publish=True, release_id=facts["release_id"])
    web.confirm_receipt(facts["receipt"], facts["index"], result["deploy_id"], result["published_utc"])
    return result


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site-id", required=True, help="Netlify site id or name (not secret)")
    ap.add_argument("--root", type=Path, default=Path(core.load_config()["melange_live"]))
    ap.add_argument("--stage-dir", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-pending-governance", action="store_true")
    return ap.parse_args(argv)


def main(argv=None, prompt=getpass.getpass, api_url=web.API) -> int:
    args = parse_args(argv)
    try:
        facts = preflight(args.root, args.allow_pending_governance)
        folder = args.stage_dir or core.releases_root({}) / "desk" / facts["release_id"]
        files = stage(facts, folder)
        print(f"desk {facts['release_id']} governance={facts['governance']} site={args.site_id}")
        for path, src in files.items():
            print(f"  {path:14} {src.stat().st_size:>11} B  sha256 {core.sha256_file(src)}")
        run_gate(folder)
        if args.dry_run:
            print("DRY RUN: preflight, staging and security gate passed; no token asked, nothing uploaded")
            return 0
        token = prompt("Netlify personal access token (input hidden): ").strip()
        if not token:
            raise ReleaseError("NO_TOKEN", "no token entered")
        result = deploy(files, facts, args.site_id, core.Secret(token), api_url)
        del token
    except web.Rejected as error:
        print(f"{error}\nNetlify would not take the file. D-171 fallback: compress before encrypting in "
              "lock_dashboard.py (architect review first). Nothing was published.", file=sys.stderr)
        return 5
    except (ReleaseError, OSError, KeyError, ValueError) as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    print(f"PUBLISHED deploy {result['deploy_id']} -> {result['production_url']} at {result['published_utc']}"
          f"{' (was already live)' if result.get('already_live') else ''}")
    print("receipt: remote_deploy_confirmed=true. Old deploys are untouched; deleting them is Paul's step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
