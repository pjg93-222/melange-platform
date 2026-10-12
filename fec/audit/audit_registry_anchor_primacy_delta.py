#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure display-board change caused only by the six registry repairs."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import statistics
from pathlib import Path

from engine import primacy_factors as PF
import tier_util as TU
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)
OUTPUT = (
    ROOT / 'audits' / 'legfix5' / 'production_promotion'
    / 'greenlight-20260728a-primacy-v1'
    / 'registry_anchor_primacy_delta.json'
)


def load_json(name):
    with (ROOT / name).open(encoding='utf-8') as handle:
        return json.load(handle)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def board(names, ranks, factors):
    # D-031: pinned to the 2026-07-29 factor board this delta audit compares against;
    # the live default ('per_area_v1') needs a grade map this script does not load.
    scores = TU.primacy_scores(ranks.get('pcts') or {}, factors, mode='factor')
    score_order = sorted(
        [member for member in names if member in scores],
        key=lambda member: (-scores[member], member),
    )
    preliminary = TU.apply_tier_overrides(
        score_order,
        TU.load_tier_overrides(),
        positions=TU.load_tier_positions(),
    )
    final, _detail = TU.apply_conduct_ladder(
        preliminary,
        TU.load_open_breaches(str(ROOT)),
        TU.load_delivery_credit(str(ROOT)),
        buckets=TU.BUCKETS,
    )
    return final, scores


def main():
    receipt = load_json('data/legislation/registry_anchor_reconciliation.json')
    anchor_keys = set(receipt.get('missing_before') or [])
    if len(anchor_keys) != 6:
        raise ValueError('expected six recovered registry anchors')
    bundle = load_json('member_desk_bundle.json')
    ranks = load_json('ranks_python.json')
    tags = PF.load_tags(ROOT / 'bill_beneficiary_tags.ndjson')
    adjudicated_credits = PF.load_adjudicated_credits(
        ROOT / PF.ADJUDICATION_LEDGER
    )
    cu_cells = {}
    if os.environ.get('PRIMACY_CU_TENSOR', '1') != '0':
        cu_cells = PF.load_cu_tensor_cells(ROOT / PF.CU_TENSOR)
    current_factors_doc = load_json('data/engine/primacy_factors.json')
    current_factors = {
        member: row['factor_shrunk']
        for member, row in current_factors_doc['members'].items()
    }
    prior_bundle = copy.deepcopy(bundle)
    for member in prior_bundle['members']:
        member['bills'] = [
            bill for bill in member.get('bills') or []
            if '%s|%s' % (
                str(bill.get('bill') or '').strip(),
                str(bill.get('congress') or '').strip(),
            ) not in anchor_keys
        ]
    prior_tags = {
        key: row for key, row in tags.items()
        if key not in anchor_keys
    }
    prior_factor_rows, prior_baseline = PF.compute(
        prior_bundle, prior_tags, adjudicated_credits, cu_cells=cu_cells
    )
    prior_factors = {
        member: row['factor_shrunk']
        for member, row in prior_factor_rows.items()
    }
    names = [row['name'] for row in bundle['members']]
    prior_board, prior_scores = board(names, ranks, prior_factors)
    current_board, current_scores = board(
        names, ranks, current_factors
    )
    prior_rank = {
        member: index + 1
        for index, member in enumerate(prior_board)
    }
    current_rank = {
        member: index + 1
        for index, member in enumerate(current_board)
    }
    deltas = {
        member: current_rank[member] - prior_rank[member]
        for member in names
    }
    moved = [abs(value) for value in deltas.values() if value]
    score_deltas = {
        member: current_scores[member] - prior_scores[member]
        for member in names
    }
    prior_tier_one = prior_board[:TU.BUCKETS[0]]
    current_tier_one = current_board[:TU.BUCKETS[0]]
    official_hash = sha256(ROOT / 'ranks_python.json')
    result = {
        'schema': 'registry-anchor-primacy-delta-v1',
        'status': 'PASS_DISPLAY_ONLY_DELTA_MEASURED',
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'comparison': (
            'counterfactual 627-act primacy board versus integrated '
            '633-act board; official rank authority is not recomputed'
        ),
        'anchor_keys': sorted(anchor_keys),
        'prior_classified_acts': len(prior_tags),
        'current_classified_acts': len(tags),
        'prior_roster_baseline': prior_baseline,
        'current_roster_baseline': (
            current_factors_doc['_constants']['roster_baseline']
        ),
        'summary': {
            'member_count': len(names),
            'members_with_display_rank_change': len(moved),
            'members_unchanged': len(names) - len(moved),
            'median_abs_display_rank_delta_all': statistics.median(
                abs(value) for value in deltas.values()
            ),
            'median_abs_display_rank_delta_moved': (
                statistics.median(moved) if moved else 0
            ),
            'mean_abs_display_rank_delta_moved': (
                statistics.mean(moved) if moved else 0
            ),
            'max_abs_display_rank_delta': max(moved or [0]),
            'members_moving_more_than_10': sum(
                value > 10 for value in moved
            ),
            'max_abs_display_score_delta': max(
                abs(value) for value in score_deltas.values()
            ),
            'official_rank_delta': 0,
        },
        'tier_1_before': prior_tier_one,
        'tier_1_after': current_tier_one,
        'tier_1_added': sorted(
            set(current_tier_one) - set(prior_tier_one)
        ),
        'tier_1_removed': sorted(
            set(prior_tier_one) - set(current_tier_one)
        ),
        'largest_display_rank_moves': [
            {
                'member': member,
                'before': prior_rank[member],
                'after': current_rank[member],
                'delta': deltas[member],
            }
            for member in sorted(
                names,
                key=lambda member: (
                    -abs(deltas[member]), member
                ),
            )[:20]
        ],
        'official_rank_sha256_before': official_hash,
        'official_rank_sha256_after': official_hash,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_name('.%s.%d.tmp' % (
        OUTPUT.name, os.getpid()
    ))
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(
            result, handle, indent=2, sort_keys=True,
            ensure_ascii=False,
        )
        handle.write('\n')
    os.replace(temporary, OUTPUT)
    print(json.dumps(result['summary'], sort_keys=True))
    print(
        'Tier 1: +%s / -%s'
        % (result['tier_1_added'], result['tier_1_removed'])
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
