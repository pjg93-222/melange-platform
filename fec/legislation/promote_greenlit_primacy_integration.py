#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Promote the audited legislation + primacy package into production.

This command is intentionally explicit and reversible: it requires the literal
Greenlight token, snapshots every existing target, copies through verified
temporary siblings, and writes a hash-bound receipt.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from legislation.legislation_culling_status import write_surface_status

ROOT = Path(__file__).resolve().parents[2]
SHADOW = (
    ROOT / 'audits' / 'legfix5' / 'shadow_recompute'
    / 'greenlight-20260728a' / 'cfpb-route-utf8-fixed-v6'
)
TREATMENT = SHADOW / 'treatment'
STAGED = (
    ROOT / 'audits' / 'legfix5' / 'staged_integration'
    / 'greenlight-20260728a'
)
PRIMACY = (
    ROOT / 'audits' / 'legfix5' / 'primacy_merged'
    / 'greenlight-20260728a-v1'
)
SHIP = ROOT / 'audits' / 'primacy_shadow_20260728' / 'ship'
OUT = (
    ROOT / 'audits' / 'legfix5' / 'production_promotion'
    / 'greenlight-20260728a-primacy-v1'
)

FILES = {
    'Moneyball Main Version - Blended.xlsx': (
        TREATMENT / 'Moneyball Main Version - Blended.xlsx'
    ),
    'archive_leg.json': TREATMENT / 'archive_leg.json',
    'manual_bill_evidence.csv': TREATMENT / 'manual_bill_evidence.csv',
    'category_stance.json': TREATMENT / 'category_stance.json',
    'data/engine/pillar_inputs_snapshot.json': (
        TREATMENT / 'data' / 'engine' / 'pillar_inputs_snapshot.json'
    ),
    'ranks_python.json': TREATMENT / 'ranks_python.json',
    'bill_beneficiary_tags.ndjson': (
        PRIMACY / 'bill_beneficiary_tags.primacy_merged.ndjson'
    ),
    'grievance_ledger.ndjson': SHIP / 'grievance_ledger.ndjson',
    'delivery_ledger.ndjson': SHIP / 'delivery_ledger.ndjson',
    'tier_overrides.json': SHIP / 'tier_overrides.proposed.json',
    'legislation_adjudication_ledger.ndjson': (
        STAGED / 'integrated_decisions.ndjson'
    ),
    'legislation_hold_ledger.ndjson': (
        STAGED / 'held_visible_non_scoring.ndjson'
    ),
    'legislation_exclusion_ledger.ndjson': (
        STAGED / 'ratified_exclusions.ndjson'
    ),
    'legislation_membership_edges.ndjson': (
        STAGED / 'integrated_membership_edges.ndjson'
    ),
    'legislation_source_supersessions.ndjson': (
        STAGED / 'source_supersessions.ndjson'
    ),
    'legislation_culling_receipt.json': (
        STAGED / 'integration_receipt.json'
    ),
    'legislation_shadow_readiness.json': (
        SHADOW / 'readiness_reassessment.json'
    ),
    'legislation_rank_delta.json': SHADOW / 'rank_delta.json',
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('JSON root is not an object: %s' % path)
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.%s.%d.tmp' % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
        + '\n',
        encoding='utf-8',
    )
    os.replace(temporary, path)


def verified_copy(source: Path, target: Path) -> str:
    expected = sha256(source)
    temporary = target.with_name('.%s.%d.tmp' % (target.name, os.getpid()))
    shutil.copy2(source, temporary)
    observed = sha256(temporary)
    if observed != expected:
        temporary.unlink(missing_ok=True)
        raise IOError('copy hash mismatch for %s' % target)
    try:
        os.replace(temporary, target)
    except PermissionError:
        # OneDrive Files-on-Demand can expose a hydrated file as a reparse
        # point and reject replace-on-target even when the file is unlocked.
        # The pre-promotion backup makes a verified direct overwrite safely
        # recoverable; retain the atomic path everywhere it is supported.
        temporary.unlink(missing_ok=True)
        shutil.copy2(source, target)
    if sha256(target) != expected:
        raise IOError('installed target hash mismatch for %s' % target)
    return expected


def validate_inputs() -> dict[str, Any]:
    missing = [str(path) for path in FILES.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError('missing promotion inputs: %s' % missing)
    shadow = read_json(SHADOW / 'paired_shadow_receipt.json')
    readiness = read_json(SHADOW / 'readiness_reassessment.json')
    integration = read_json(STAGED / 'integration_receipt.json')
    if shadow.get('status') != 'PASS_ISOLATED_PAIRED_RANK_SHADOW':
        raise ValueError('paired shadow has not passed')
    if readiness.get('status') != 'PASS_ISOLATED_PAIRED_SHADOW_COMPLETE':
        raise ValueError('readiness audit has not passed')
    if integration.get('status') != (
        'PASS_STAGED_INTEGRATION_WITH_HELD_NONSCORING'
    ):
        raise ValueError('staged culling integration has not passed')
    route = readiness.get('cfpb_route_checks') or {}
    if not route or not all(route.values()):
        raise ValueError('CFPB route audit is not fully green')
    return {
        'shadow': shadow,
        'readiness': readiness,
        'integration': integration,
    }


def promote(greenlight: str) -> dict[str, Any]:
    if greenlight != 'GREENLIGHT_INTEGRATION':
        raise ValueError(
            'promotion requires --greenlight GREENLIGHT_INTEGRATION'
        )
    evidence = validate_inputs()
    timestamp = dt.datetime.now(dt.timezone.utc)
    stamp = timestamp.strftime('%Y%m%dT%H%M%SZ')
    backup = ROOT / 'backups' / ('pre-greenlight-primacy-' + stamp)
    if backup.exists():
        raise FileExistsError('backup target already exists: %s' % backup)
    backup.mkdir(parents=True)
    before = {}
    for relative in FILES:
        target = ROOT / relative
        if target.is_file():
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
            before[relative] = {
                'sha256': sha256(target),
                'backup': str(destination.resolve()),
            }
    installed = {}
    for relative, source in FILES.items():
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = verified_copy(source, target)
        installed[relative] = {
            'source': str(source.resolve()),
            'target': str(target.resolve()),
            'sha256': digest,
            'bytes': target.stat().st_size,
        }
    delta = read_json(SHADOW / 'rank_delta.json')
    receipt = {
        'schema': 'greenlit-legislation-primacy-promotion-v1',
        'status': 'PASS_PRODUCTION_INPUTS_PROMOTED',
        'promoted_at': timestamp.isoformat(),
        'authorization': greenlight,
        'backup_root': str(backup.resolve()),
        'previous_targets': before,
        'installed': installed,
        'counts': evidence['integration'].get('counts') or {},
        'rank_delta_vs_frozen_control': delta.get('summary') or {},
        'cfpb_route_checks': (
            evidence['readiness'].get('cfpb_route_checks') or {}
        ),
        'next_required_step': (
            'run build_primacy_surfaces.py, then full tests and audit'
        ),
    }
    atomic_json(OUT / 'promotion_receipt.json', receipt)
    write_surface_status(ROOT)
    print(
        'promoted %d artifacts | backup %s'
        % (len(installed), backup)
    )
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--greenlight', required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    promote(args.greenlight)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
