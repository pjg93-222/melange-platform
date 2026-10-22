#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit the score-based primacy adjudication with the Opus-5 protocol.

This is a local deterministic replay. It makes no provider or network calls.
The Opus-5 method contributes the evidence standard: complete supplied
evidence, explicit uncertainty, no inference from absence, and a terminal
receipt. The production board score follows the active basis: factor mode
ranks on ``factor_ranked`` (the uncertainty-discounted shrunk factor,
user-ratified 2026-07-29); the ``PRIMACY_BOARD=legxfactor`` revert ranks on
``Leg% * factor_shrunk``.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import statistics

from engine import primacy_factors as PF
import tier_util as TU
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)
# Audit basis: factor board, or the legxfactor revert. Never the live per_area_v1 default.
BOARD = 'legxfactor' if TU.PRIMACY_BOARD == 'legxfactor' else 'factor'
OUTPUT = (
    ROOT / 'audits' / 'legfix5' / 'production_promotion'
    / 'greenlight-20260728a-primacy-v1'
    / 'primacy_math_opus5_review.json'
)
# Board-seating contract for the five adjudicated members. All five keep their
# uniform sponsor-equivalent cu_primary credit in the FACTOR math (unchanged).
# Their SEATING targets were re-ratified 2026-07-29 by the factor-only
# directive: with the Legislative percentile no longer multiplying the score,
# each member's +1.0 credit is diluted against their own agenda denominator
# (Huizenga 15.25, Kim 16.00), and neither reaches Tier 1 any more. That
# outcome was presented and accepted explicitly, so these are the post-directive
# limits. PRIMACY_BOARD=legxfactor restores the original <=13 / <=12 seating.
TARGET_LIMITS = {
    'Brad Sherman': 13,
    'Bill Huizenga': 37,
    # Young Kim: Tier 1 under the uncertainty penalty by a 0.002 margin, then
    # back to Tier 2 (slot 14) once amendments took measure weight 0.5 the
    # same day. Her adjudication credit is unchanged throughout; only seating
    # moved. PRIMACY_AMENDMENT_W=1.0 restores the Tier 1 seat.
    'Young Kim': 37,
    'Catherine Cortez Masto': 13,
    'Tim Scott': 12,
}
# The seating the same five members held under the pre-directive board, kept so
# the revert switch has an assertable contract and the supersession stays legible.
TARGET_LIMITS_LEGXFACTOR = {
    'Brad Sherman': 13,
    'Bill Huizenga': 13,
    'Young Kim': 13,
    'Catherine Cortez Masto': 13,
    'Tim Scott': 12,
}


def read_json(path):
    with Path(path).open(encoding='utf-8') as handle:
        return json.load(handle)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def board(names, pcts, factors):
    # D-031: pinned to the 2026-07-29 factor board this audit was written against;
    # the live default ('per_area_v1') needs a grade map this script does not load.
    scores = TU.primacy_scores(pcts, factors, mode=BOARD)
    order = sorted(
        names, key=lambda member: (-scores[member], member)
    )
    # Mirror legislative_press_tiers exactly: the evidence floor sits between
    # placements (empty here) and the conduct ladder, factor-mode only.
    if BOARD == 'factor':
        order = TU.apply_evidence_floor(
            order,
            TU.load_primacy_cu_credit(str(ROOT)),
            exempt=set(TU.load_tier_overrides()),
        )
    final, detail = TU.apply_conduct_ladder(
        order,
        TU.load_open_breaches(str(ROOT)),
        TU.load_delivery_credit(str(ROOT)),
        buckets=TU.BUCKETS,
    )
    return (
        scores,
        order,
        final,
        {member: index + 1 for index, member in enumerate(final)},
        detail,
    )


def source_from_reference(reference):
    text = str(reference or '').strip()
    if not text:
        return ''
    return text.split(':', 1)[0]


def main():
    ranks_path = ROOT / 'ranks_python.json'
    rank_hash_before = sha256(ranks_path)
    ranks = read_json(ranks_path)
    bundle = read_json(ROOT / 'member_desk_bundle.json')
    tags = PF.load_tags(ROOT / 'bill_beneficiary_tags.ndjson')
    committed = read_json(ROOT / 'data' / 'engine' / 'primacy_factors.json')
    credits = PF.load_adjudicated_credits(
        ROOT / PF.ADJUDICATION_LEDGER
    )
    cu_cells = {}
    if os.environ.get('PRIMACY_CU_TENSOR', '1') != '0':
        cu_cells = PF.load_cu_tensor_cells(ROOT / PF.CU_TENSOR)
    names = [row['name'] for row in bundle['members']]

    control_rows, control_baseline = PF.compute(
        bundle, tags, {}, cu_cells=cu_cells
    )
    treatment_rows, treatment_baseline = PF.compute(
        bundle, tags, credits, cu_cells=cu_cells
    )
    factor_drift = {
        member: abs(
            treatment_rows[member]['factor_shrunk']
            - committed['members'][member]['factor_shrunk']
        )
        for member in names
    }
    # Factor mode ranks on the uncertainty-discounted factor_ranked
    # (user-ratified 2026-07-29, "certainty outranks estimation"); the
    # legxfactor revert ranks on factor_shrunk. Mirror tier_util exactly.
    rank_field = (
        'factor_ranked' if BOARD == 'factor'
        else 'factor_shrunk'
    )
    control = board(
        names,
        ranks['pcts'],
        {
            member: row[rank_field]
            for member, row in control_rows.items()
        },
    )
    treatment = board(
        names,
        ranks['pcts'],
        {
            member: row[rank_field]
            for member, row in treatment_rows.items()
        },
    )
    control_scores, _control_raw, control_final, control_rank, _ = (
        control
    )
    treatment_scores, treatment_raw, treatment_final, treatment_rank, _ = (
        treatment
    )

    ledger_rows = [
        json.loads(line)
        for line in (
            ROOT / PF.ADJUDICATION_LEDGER
        ).read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    evidence_files = sorted({
        source_from_reference(reference)
        for row in ledger_rows
        for reference in row.get('evidence') or []
    })
    evidence_file_status = {
        name: (ROOT / name).is_file()
        for name in evidence_files
    }

    with (ROOT / 'data' / 'legislation' / 'cfpb_bill_directions.csv').open(
        encoding='utf-8-sig', newline=''
    ) as handle:
        cfpb_rows = list(csv.DictReader(handle))
    cfpb_contract_ok = bool(cfpb_rows) and all(
        (
            row['Category'] == 'Reduce'
            and int(row['Direction']) == 1
        )
        or (
            row['Category'] == 'Expand'
            and int(row['Direction']) == -1
        )
        or (
            row['Category'] == 'Neutral'
            and int(row['Direction']) == 0
        )
        for row in cfpb_rows
    )
    readiness = read_json(ROOT / 'legislation_shadow_readiness.json')
    route_checks = readiness.get('cfpb_route_checks') or {}

    deltas = {
        member: treatment_rank[member] - control_rank[member]
        for member in names
    }
    moved = [abs(value) for value in deltas.values() if value]
    target_receipt = {
        member: {
            'maximum_rank': maximum,
            'control_rank': control_rank[member],
            'treatment_rank': treatment_rank[member],
            'rank_delta': deltas[member],
            'control_score': control_scores[member],
            'treatment_score': treatment_scores[member],
            'score_delta': (
                treatment_scores[member] - control_scores[member]
            ),
            'factor_raw': treatment_rows[member]['factor_raw'],
            'factor_shrunk': treatment_rows[member]['factor_shrunk'],
            'adjudicated_credit': (
                treatment_rows[member]['adjudicated_credit']
            ),
            'adjudicated_events': (
                treatment_rows[member]['adjudicated_events']
            ),
        }
        for member, maximum in TARGET_LIMITS.items()
    }
    gates = {
        'local_replay_made_no_provider_or_api_calls': True,
        'official_rank_authority_is_byte_identical': (
            sha256(ranks_path) == rank_hash_before
        ),
        'display_placements_are_empty': (
            TU.load_tier_overrides() == {}
            and TU.load_tier_positions() == {}
        ),
        'five_uniform_cu_primary_credits': (
            set(credits) == set(TARGET_LIMITS)
            and len(ledger_rows) == 5
            and all(
                row.get('status') == 'active'
                and row.get('beneficiary_class') == 'cu_primary'
                and float(row.get('role_weight', 0)) == 1.0
                and row.get('evidence')
                and 'Opus-5 method' in str(row.get('authority') or '')
                for row in ledger_rows
            )
        ),
        'producer_recomputes_committed_factors': (
            max(factor_drift.values() or [0]) <= 5e-7
        ),
        # 2026-07-29 factor-only directive + same-day uncertainty penalty:
        # the factor-mode board score IS factor_ranked (factor_shrunk minus
        # K*(1-lambda)*|raw-baseline|). Under the PRIMACY_BOARD=legxfactor
        # revert switch it is the old Leg% x factor_shrunk product. This gate
        # follows the active basis rather than hard-coding either one.
        'score_order_matches_active_board_basis': all(
            abs(
                treatment_scores[member]
                - (
                    treatment_rows[member]['factor_ranked']
                    if BOARD == 'factor'
                    else float(ranks['pcts'][member]['leg'])
                    * treatment_rows[member]['factor_shrunk']
                )
            ) <= 1e-12
            for member in names
        ),
        # Ratified constraint recorded with K=0.25 ("Vargas needs to be in"):
        # the penalty strength was chosen specifically so Juan Vargas keeps a
        # Tier 1 seat. Guards against future K drift evicting him silently.
        'vargas_holds_tier_one_under_factor_board': (
            BOARD != 'factor'
            or treatment_rank.get('Juan Vargas', 999) <= 13
        ),
        'all_referenced_local_evidence_files_exist': (
            bool(evidence_file_status)
            and all(evidence_file_status.values())
        ),
        'cfpb_restrain_positive_expand_negative': (
            cfpb_contract_ok
            and bool(route_checks)
            and all(route_checks.values())
        ),
        # Limit-driven so the contract follows the active board basis. Under
        # 'factor' these are the post-directive seats; the revert switch is
        # asserted against the original pre-directive seats.
        'adjudicated_members_meet_ratified_seating': all(
            treatment_rank[member] <= maximum
            for member, maximum in (
                TARGET_LIMITS
                if BOARD == 'factor'
                else TARGET_LIMITS_LEGXFACTOR
            ).items()
        ),
        'tim_scott_is_mathematically_top_12': (
            treatment_rank['Tim Scott'] <= 12
        ),
        'conduct_ladder_preserves_roster': (
            len(treatment_final) == len(names)
            and sorted(treatment_final) == sorted(names)
        ),
    }
    result = {
        'schema': 'primacy-math-opus5-local-review-v1',
        'status': (
            'PASS_SCORE_BASED_ADJUDICATION'
            if all(gates.values())
            else 'FAIL_SCORE_BASED_ADJUDICATION'
        ),
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'execution': {
            'executor': 'Codex',
            'adjudication_method': (
                'Opus-5 candidate-adjudication evidence standard'
            ),
            'provider_call_performed': False,
            'network_call_performed': False,
            'paid_api_cost_usd': 0.0,
        },
        'formula': {
            'board': BOARD,
            'role_weights': PF.ROLE_W,
            'act_weights': PF.ACT_W,
            'letter_weight': PF.LETTER_W,
            'shrink_anchor': PF.SHRINK_ANCHOR,
            'uncertainty_penalty_k': PF.UNCERT_K,
            'control_baseline': control_baseline,
            'treatment_baseline': treatment_baseline,
            'factor_raw': (
                'sum(role_weight * act_weight) / '
                'sum(role_weight)'
            ),
            'factor_shrunk': (
                'baseline + min(1, agenda_credit / 15) '
                '* (factor_raw - baseline)'
            ),
            'tier_score': (
                'factor_ranked = factor_shrunk - K*(1-lambda)'
                '*|factor_raw - baseline|'
                if BOARD == 'factor'
                else 'leg_pct * factor_shrunk'
            ),
            'adjudicated_credit_rule': (
                'each active ledger row is one classified role; '
                'the five current rows are uniformly '
                'role_weight=1.0, beneficiary_class=cu_primary'
            ),
        },
        'targets': target_receipt,
        'top_13': [
            {
                'rank': index + 1,
                'member': member,
                'score': treatment_scores[member],
            }
            for index, member in enumerate(treatment_final[:13])
        ],
        'delta_summary': {
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
            'official_rank_delta': 0,
            'official_score_delta': 0,
        },
        'control_top_13': control_final[:13],
        'treatment_raw_score_top_13': treatment_raw[:13],
        'evidence_file_status': evidence_file_status,
        'cfpb_direction_counts': {
            category: sum(
                row['Category'] == category for row in cfpb_rows
            )
            for category in ('Reduce', 'Neutral', 'Expand')
        },
        'gates': gates,
        'official_rank_sha256_before': rank_hash_before,
        'official_rank_sha256_after': sha256(ranks_path),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_name('.%s.%d.tmp' % (
        OUTPUT.name, os.getpid()
    ))
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(
            result, handle, ensure_ascii=False,
            indent=2, sort_keys=True,
        )
        handle.write('\n')
    os.replace(temporary, OUTPUT)
    print(json.dumps({
        'status': result['status'],
        'targets': {
            member: row['treatment_rank']
            for member, row in target_receipt.items()
        },
        'delta_summary': result['delta_summary'],
        'failed_gates': [
            name for name, passed in gates.items() if not passed
        ],
    }, sort_keys=True))
    return 0 if all(gates.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
