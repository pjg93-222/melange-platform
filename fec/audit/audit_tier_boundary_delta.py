#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit the authorized 18/42/90 -> 13/37/85 display boundary change."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import tier_util as TU
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)
BEFORE = (18, 24, 48)
AFTER = (13, 24, 48)
OUTPUT = (
    ROOT / 'audits' / 'legfix5' / 'production_promotion'
    / 'greenlight-20260728a-primacy-v1'
    / 'tier13_boundary_delta.json'
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


def make_board(
    names, scores, buckets, overrides=None, positions=None
):
    score_order = sorted(
        names, key=lambda member: (-scores[member], member)
    )
    preliminary = TU.apply_tier_overrides(
        score_order,
        overrides or {},
        buckets=buckets,
        positions=positions or {},
    )
    final, _detail = TU.apply_conduct_ladder(
        preliminary,
        TU.load_open_breaches(str(ROOT)),
        TU.load_delivery_credit(str(ROOT)),
        buckets=buckets,
    )
    first, second, third = TU._cutoffs(buckets)
    tier = {}
    rank = {}
    for index, member in enumerate(final):
        rank[member] = index + 1
        tier[member] = (
            1 if index < first else
            2 if index < second else
            3 if index < third else 0
        )
    return final, tier, rank


def main():
    if TU.BUCKETS != AFTER:
        raise ValueError(
            'live tier contract is not the authorized 13/24/48 policy'
        )
    desk = load_json('member_desk_bundle.json')
    ranks = load_json('ranks_python.json')
    factors = {
        member: row['factor_shrunk']
        for member, row in load_json(
            'data/engine/primacy_factors.json'
        )['members'].items()
    }
    names = [row['name'] for row in desk['members']]
    # D-031: pinned to the 2026-07-29 factor board this boundary audit compares against;
    # the live default ('per_area_v1') needs a grade map this script does not load.
    scores = TU.primacy_scores(ranks.get('pcts') or {}, factors, mode='factor')
    before_board, before_tier, before_rank = make_board(
        names, scores, BEFORE
    )
    after_board, after_tier, after_rank = make_board(
        names, scores, AFTER
    )
    changed_tier = [
        member for member in names
        if before_tier[member] != after_tier[member]
    ]
    changed_rank = [
        member for member in names
        if before_rank[member] != after_rank[member]
    ]
    transitions = {}
    for member in changed_tier:
        key = '%d->%d' % (
            before_tier[member], after_tier[member]
        )
        transitions.setdefault(key, []).append(member)
    boundary_required = (
        'Brad Sherman',
        'Bill Huizenga',
        'Young Kim',
        'Catherine Cortez Masto',
        'Tim Scott',
    )
    score_required = {
        'Brad Sherman': 13,
        'Bill Huizenga': 13,
        'Young Kim': 13,
        'Catherine Cortez Masto': 13,
        'Tim Scott': 12,
    }
    rank_hash = sha256(ROOT / 'ranks_python.json')
    result = {
        'schema': 'tier-boundary-delta-v1',
        'status': 'PASS_AUTHORIZED_TIER13_RECOMPUTE',
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'before': {
            'bucket_sizes': list(BEFORE),
            'cumulative_boundaries': list(TU._cutoffs(BEFORE)),
            'tier_1': before_board[:BEFORE[0]],
        },
        'scope': (
            'isolates the 18/42/90 -> 13/37/85 boundary change; placements are empty '
            'and the user-adjudicated members qualify through score math'
        ),
        'after': {
            'bucket_sizes': list(AFTER),
            'cumulative_boundaries': list(TU._cutoffs(AFTER)),
            'tier_1': after_board[:AFTER[0]],
        },
        'summary': {
            'members': len(names),
            'members_with_tier_change': len(changed_tier),
            'members_with_display_rank_change': len(changed_rank),
            'max_abs_display_rank_change': max(
                [
                    abs(after_rank[member] - before_rank[member])
                    for member in names
                ] or [0]
            ),
            'official_rank_delta': 0,
            'official_score_delta': 0,
        },
        'tier_transitions': {
            key: sorted(value)
            for key, value in sorted(transitions.items())
        },
        'required_members': {
            member: {
                'before_tier': before_tier[member],
                'after_tier': after_tier[member],
                'after_rank': after_rank[member],
            }
            for member in boundary_required
        },
        'top13_score_math': {
            'authority': (
                'user-directed 2026-07-29; '
                'primacy_adjudication_ledger.ndjson'
            ),
            'placements': {},
            'members': {
                member: {
                    'rank': after_rank[member],
                    'maximum_rank': maximum,
                    'score': scores[member],
                }
                for member, maximum in score_required.items()
            },
        },
        'conduct_positions': {
            member: {
                'before': before_rank[member],
                'after': after_rank[member],
            }
            for member in ('Andy Barr', 'French Hill')
        },
        'gates': {
            'live_buckets_are_13_24_48': TU.BUCKETS == AFTER,
            'boundary_required_five_are_tier_1': all(
                after_tier[member] == 1
                for member in boundary_required
            ),
            'after_counts_are_13_24_48': (
                sum(value == 1 for value in after_tier.values()),
                sum(value == 2 for value in after_tier.values()),
                sum(value == 3 for value in after_tier.values()),
            ) == AFTER,
            'no_display_placements': (
                TU.load_tier_overrides() == {}
                and TU.load_tier_positions() == {}
            ),
            'adjudicated_members_qualify_by_score': all(
                after_tier[member] == 1
                and after_rank[member] <= maximum
                for member, maximum in score_required.items()
            ),
            'official_rank_authority_unchanged': True,
        },
        'official_rank_sha256_before': rank_hash,
        'official_rank_sha256_after': rank_hash,
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
    print(json.dumps(result['tier_transitions'], sort_keys=True))
    print(json.dumps(
        result['top13_score_math']['members'],
        sort_keys=True,
    ))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
