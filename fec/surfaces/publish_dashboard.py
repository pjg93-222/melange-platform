#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the password-locked deploy artifact and write a hash-bound lineage receipt.

This prepares ``deploy/index.html`` but deliberately does not claim a remote
Netlify deployment.  A future remote publisher must extend the receipt with a
provider deployment id before setting ``remote_deploy_confirmed`` true.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT  # noqa: E402
from surfaces.lock_dashboard import lock_dashboard  # noqa: E402


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_password(path):
    if not path:
        raise RuntimeError("DASHBOARD_PASSWORD_FILE or --password-file is required")
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("dashboard password file is empty")
    return value


def _validate_press_lineage(path):
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("active press feed receipt is unreadable") from exc
    if (document.get("schema") != "press-refresh-engine-feed-v1"
            or document.get("status") != "ACTIVE_TYPED_SUPPLEMENT"
            or document.get("promotion_mode") != "CUMULATIVE_ACCEPTED_RUN_LEDGER"):
        raise RuntimeError("dashboard publication requires the cumulative press feed")
    outputs = document.get("outputs") or {}
    for name in ("tone", "fulltext", "mentions"):
        record = outputs.get(name) or {}
        output = Path(str(record.get("path") or ""))
        if not output.is_file() or sha256_file(output) != record.get("sha256"):
            raise RuntimeError("dashboard publication found a changed press %s output" % name)
    return document


def publish(source, output, password_file, receipt_output, press_receipt):
    source = Path(source); output = Path(output); receipt_output = Path(receipt_output)
    if not source.is_file():
        raise RuntimeError("combined dashboard is missing: %s" % source)
    press = _validate_press_lineage(press_receipt)
    locked = lock_dashboard(source, output, _read_password(password_file))
    result = {
        "schema": "dashboard-deployment-receipt-v1",
        "status": "LOCAL_DEPLOY_ARTIFACT_READY_NOT_REMOTE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(source.resolve()),
            "sha256": locked["source_sha256"],
            "size": source.stat().st_size,
            "mtime_ns": source.stat().st_mtime_ns,
        },
        "local_deploy_artifact": {
            "path": str(output.resolve()),
            "sha256": locked["output_sha256"],
            "size": output.stat().st_size,
            "encryption": "AES-256-GCM+PBKDF2-SHA256",
            "pbkdf2_iterations": locked["iterations"],
        },
        "press_lineage": {
            "receipt_path": str(Path(press_receipt).resolve()),
            "receipt_sha256": sha256_file(press_receipt),
            "generated_at": press.get("generated_at"),
            "promotion_mode": press.get("promotion_mode"),
            "accepted_runs": len(press.get("accepted_runs") or []),
        },
        "contract": {
            "remote_deploy_confirmed": False,
            "local_artifact_may_be_uploaded_only_when_this_receipt_matches": True,
            "password_material_recorded": False,
        },
    }
    receipt_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt_output.with_name(receipt_output.name + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(receipt_output)
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(ROOT) / "Moneyball.html")
    parser.add_argument("--output", type=Path, default=Path(ROOT) / "deploy/index.html")
    parser.add_argument("--password-file", type=Path,
                        default=os.environ.get("DASHBOARD_PASSWORD_FILE"))
    parser.add_argument("--press-receipt", type=Path,
                        default=Path(ROOT) / "data/stance/press_refresh_feed_receipt.json")
    parser.add_argument("--receipt-output", type=Path,
                        default=Path(ROOT) / "data/engine/dashboard_deployment_receipt.json")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = publish(args.source, args.output, args.password_file,
                     args.receipt_output, args.press_receipt)
    print("dashboard deploy artifact ready: %s (remote deployment not claimed)" %
          result["local_deploy_artifact"]["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
