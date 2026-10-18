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
import tempfile
try:
    from .source_capture import SourceCapture
except ImportError:
    from source_capture import SourceCapture  # stale-ok: script-execution fallback, fec/surfaces is sys.path[0]

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


def _validate_press_lineage(path, output_paths=None):
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
        declared = str(record.get("path") or "")
        output = Path((output_paths or {}).get(declared, declared))
        if not output.is_file() or sha256_file(output) != record.get("sha256"):
            raise RuntimeError("dashboard publication found a changed press %s output" % name)
    return document


def publish(source, output, password_file, receipt_output, press_receipt):
    source = Path(source); output = Path(output); receipt_output = Path(receipt_output)
    if not source.is_file():
        raise RuntimeError("combined dashboard is missing: %s" % source)
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt_output.parent.mkdir(parents=True, exist_ok=True)
    with SourceCapture(output.parent) as captured:
        source_copy = captured.capture(source, 'source.html')
        press_copy = captured.capture(press_receipt, 'press-receipt.json')
        try:
            press_document = json.loads(press_copy.read_text(encoding='utf-8'))
        except ValueError as exc:
            raise RuntimeError('active press feed receipt is unreadable') from exc
        output_paths = {}
        for name in ('tone', 'fulltext', 'mentions'):
            record = (press_document.get('outputs') or {}).get(name) or {}
            declared = str(record.get('path') or '')
            output_paths[declared] = captured.capture(declared, 'press/' + name,
                                                      expected_sha256=record.get('sha256'))
        press = _validate_press_lineage(press_copy, output_paths)
        staged_output = captured.root / 'index.html'
        locked = lock_dashboard(source_copy, staged_output, _read_password(password_file))
        source_record = captured.records[str(source)]
        if locked['source_sha256'] != source_record['sha256'] or locked['output_sha256'] != sha256_file(staged_output):
            raise RuntimeError('dashboard encryption receipt differs from captured bytes')
        result = {
            'schema': 'dashboard-deployment-receipt-v1',
            'status': 'LOCAL_DEPLOY_ARTIFACT_READY_NOT_REMOTE',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'source': {'path': str(source.resolve()), 'sha256': source_record['sha256'],
                       'size': source_record['bytes'], 'mtime_ns': source_record['mtime_ns']},
            'local_deploy_artifact': {'path': str(output.resolve()), 'sha256': locked['output_sha256'],
                'size': staged_output.stat().st_size, 'encryption': 'AES-256-GCM+PBKDF2-SHA256',
                'pbkdf2_iterations': locked['iterations']},
            'press_lineage': {'receipt_path': str(Path(press_receipt).resolve()),
                'receipt_sha256': captured.records[str(Path(press_receipt))]['sha256'],
                'generated_at': press.get('generated_at'), 'promotion_mode': press.get('promotion_mode'),
                'accepted_runs': len(press.get('accepted_runs') or []),
                'press_coverage': press.get('coverage')},
            'contract': {'remote_deploy_confirmed': False,
                'local_artifact_may_be_uploaded_only_when_this_receipt_matches': True,
                'password_material_recorded': False},
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                    dir=receipt_output.parent, prefix='.dashboard-receipt-', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(result, indent=2, sort_keys=True) + '\n')
                handle.flush(); os.fsync(handle.fileno())
            captured.verify_unchanged()
            # Each replace is atomic; a process/power failure between them is not.
            # Consumers must still match artifact hash to receipt before upload.
            staged_output.replace(output)
            temporary.replace(receipt_output)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
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
