#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the production-facing culling status from immutable receipts.

The staged culling receipt remains the detailed disposition authority.  Once
promotion succeeds, surfaces must not mistake its intentionally conservative
pre-promotion gates for the live production state.  This module binds the
staged receipt to a promotion receipt through installed-file hashes and emits a
small production status document while retaining the staged receipt as
provenance.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import hashlib
import json
import os
from pathlib import Path
from typing import Any


STATUS_NAME = 'data/legislation/legislation_culling_status.json'
STAGED_NAME = 'legislation_culling_receipt.json'
RANK_NAME = 'ranks_python.json'
PROMOTION_GLOB = (
    'audits/legfix5/production_promotion/*/promotion_receipt.json'
)
# These are the installed culling decisions and their disposition evidence.
# Routine rank/press refreshes change ranks_python.json without undoing them.
CULLING_CONTINUITY_FILES = (
    STAGED_NAME, 'legislation_adjudication_ledger.ndjson',
    'legislation_exclusion_ledger.ndjson', 'legislation_hold_ledger.ndjson',
    'legislation_membership_edges.ndjson', 'legislation_source_supersessions.ndjson',
    'legislation_shadow_readiness.json', 'legislation_rank_delta.json',
    'manual_bill_evidence.csv', 'delivery_ledger.ndjson', 'grievance_ledger.ndjson',
)


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
        encoding='utf-8', newline='\r\n',
    )
    os.replace(temporary, path)


def _matching_promotion(root: Path, staged_hash: str, rank_hash: str):
    candidates = []
    for path in root.glob(PROMOTION_GLOB):
        try:
            receipt = read_json(path)
        except (OSError, TypeError, ValueError):
            continue
        installed = receipt.get('installed') or {}
        if not isinstance(installed, dict) or any(
            not isinstance(installed.get(name) or {}, dict)
            for name in (RANK_NAME, STAGED_NAME) + CULLING_CONTINUITY_FILES
        ):
            continue
        original_rank_matches = (installed.get(RANK_NAME) or {}).get('sha256') == rank_hash
        continuity = {}
        if not original_rank_matches:
            for name in CULLING_CONTINUITY_FILES:
                current = root / name
                expected = (installed.get(name) or {}).get('sha256')
                try:
                    intact = bool(expected) and current.is_file() and sha256(current) == expected
                except OSError:
                    intact = False
                if not intact:
                    break
                continuity[name] = expected
        continued = len(continuity) == len(CULLING_CONTINUITY_FILES)
        if (
            receipt.get('status') == 'PASS_PRODUCTION_INPUTS_PROMOTED'
            and (installed.get(STAGED_NAME) or {}).get('sha256')
            == staged_hash
            and (original_rank_matches or continued)
        ):
            candidates.append(
                (str(receipt.get('promoted_at') or ''), path, receipt, continuity)
            )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], str(item[1])))


def build_surface_status(root: str | os.PathLike[str]) -> dict[str, Any]:
    root_path = Path(root)
    staged_path = root_path / STAGED_NAME
    rank_path = root_path / RANK_NAME
    staged = read_json(staged_path)
    staged_hash = sha256(staged_path)
    rank_hash = sha256(rank_path)
    matched = _matching_promotion(root_path, staged_hash, rank_hash)
    if matched is None:
        return {
            'schema': 'legislation-culling-surface-status-v1',
            'status': staged.get('status'),
            'phase': 'staged',
            'promotion_performed': False,
            'production_workbook_written': False,
            'rank_written': False,
            'generation_id': staged.get('generation_id'),
            'counts': staged.get('counts') or {},
            'staged_receipt': {
                'path': STAGED_NAME,
                'sha256': staged_hash,
                'status': staged.get('status'),
            },
            'promotion_receipt': None,
        }

    _, promotion_path, promotion, continuity = matched
    promotion_relative = promotion_path.relative_to(root_path).as_posix()
    return {
        'schema': 'legislation-culling-surface-status-v1',
        'status': 'PASS_PRODUCTION_CULLING_INTEGRATED',
        'phase': 'production',
        'promotion_performed': True,
        'production_workbook_written': True,
        'rank_written': True,
        'generation_id': staged.get('generation_id'),
        'counts': promotion.get('counts') or staged.get('counts') or {},
        'promoted_at': promotion.get('promoted_at'),
        'authorization': promotion.get('authorization'),
        'cfpb_route_checks': promotion.get('cfpb_route_checks') or {},
        'rank_delta_vs_frozen_control': (
            promotion.get('rank_delta_vs_frozen_control') or {}
        ),
        'official_rank_sha256': rank_hash,
        'rank_relation_to_promotion': (
            'refreshed_after_promotion' if continuity else 'identical_to_promotion'
        ),
        'original_promoted_rank_sha256': (
            (promotion.get('installed') or {}).get(RANK_NAME) or {}
        ).get('sha256'),
        'culling_continuity_sha256': continuity,
        'rank_verification_scope': (
            'This status verifies installed culling decisions. Current score/rank '
            'correctness is independently checked by compute_pillars audit mode.'
        ),
        'staged_receipt': {
            'path': STAGED_NAME,
            'sha256': staged_hash,
            'status': staged.get('status'),
        },
        'promotion_receipt': {
            'path': promotion_relative,
            'sha256': sha256(promotion_path),
            'status': promotion.get('status'),
        },
    }


def load_surface_status(root: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(root) / STATUS_NAME
    if path.is_file():
        return read_json(path)
    return build_surface_status(root)


def write_surface_status(root: str | os.PathLike[str]) -> dict[str, Any]:
    root_path = Path(root)
    status = build_surface_status(root_path)
    atomic_json(root_path / STATUS_NAME, status)
    return status


def main() -> int:
    from paths import ROOT

    status = write_surface_status(ROOT)
    print(
        '%s | phase=%s | status=%s'
        % (STATUS_NAME, status.get('phase'), status.get('status'))
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
