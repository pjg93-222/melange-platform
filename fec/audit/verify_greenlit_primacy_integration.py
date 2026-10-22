#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the merged 633-act primacy system in its production format."""
from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

FEC_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(FEC_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(FEC_BOOTSTRAP))

from openpyxl import load_workbook

from engine import primacy_factors as PF
import tier_util as TU
import moneyball_core as MC
import roster
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)
OUT = (
    ROOT / 'audits' / 'legfix5' / 'production_promotion'
    / 'greenlight-20260728a-primacy-v1'
    / 'integration_verification.json'
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(name):
    with open(ROOT / name, encoding='utf-8') as handle:
        return json.load(handle)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.%s.%d.tmp' % (path.name, os.getpid()))
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write('\n')
    os.replace(temporary, path)


def check(checks, name, condition, detail=''):
    checks[name] = {'passed': bool(condition), 'detail': detail}
    print(
        '%-68s %s%s'
        % (name, 'PASS' if condition else 'FAIL',
           ('  ' + detail) if detail else '')
    )


def main():
    checks = {}
    ranks_path = ROOT / 'ranks_python.json'
    rank_hash_before = sha256(ranks_path)
    ranks = load_json('ranks_python.json')
    stance = load_json('category_stance.json')
    bundle = load_json('member_desk_bundle.json')
    committed = load_json('data/engine/primacy_factors.json')
    readiness = load_json('legislation_shadow_readiness.json')
    culling = load_json('legislation_culling_receipt.json')
    culling_status = load_json('data/legislation/legislation_culling_status.json')
    rank_delta = load_json('legislation_rank_delta.json')
    dashboard = load_json('dashboard_data.json')
    tags = PF.load_tags(ROOT / 'bill_beneficiary_tags.ndjson')
    adjudicated_credits = PF.load_adjudicated_credits(
        ROOT / PF.ADJUDICATION_LEDGER
    )
    cu_cells = {}
    if os.environ.get('PRIMACY_CU_TENSOR', '1') != '0':
        cu_cells = PF.load_cu_tensor_cells(ROOT / PF.CU_TENSOR)
    promotion = json.loads(
        (OUT.parent / 'promotion_receipt.json').read_text(encoding='utf-8')
    )
    sidecar = [
        json.loads(line)
        for line in (
            ROOT / 'data' / 'members' / 'primacy_act_memberships.ndjson'
        ).read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    registry_receipt = load_json('data/legislation/registry_anchor_reconciliation.json')
    boundary_delta = json.loads(
        (
            OUT.parent / 'tier13_boundary_delta.json'
        ).read_text(encoding='utf-8')
    )
    math_review = json.loads(
        (
            OUT.parent / 'primacy_math_opus5_review.json'
        ).read_text(encoding='utf-8')
    )
    registry_sidecar = [
        json.loads(line)
        for line in (
            ROOT / 'data' / 'members' / 'registry_anchor_memberships.ndjson'
        ).read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]

    acts = {
        '%s|%s' % (
            str(bill.get('bill') or '').strip(),
            str(bill.get('congress') or '').strip(),
        )
        for member in bundle.get('members') or []
        for bill in member.get('bills') or []
    }
    check(
        checks, '633-act classification universe is exactly closed',
        len(acts) == len(tags) == 633 and acts == set(tags),
        '%d bundle acts / %d tags' % (len(acts), len(tags)),
    )
    check(
        checks, '%d-member roster is shared by bundle, factors, and ranks' % roster.count(),
        len(bundle.get('members') or []) == roster.count()
        and len(committed.get('members') or {}) == roster.count()
        and len(ranks.get('rank') or {}) == roster.count(),
    )
    sidecar_keys = {
        '%s|%s' % (
            str(row.get('bill') or '').strip(),
            str(row.get('congress') or '').strip(),
        )
        for row in sidecar
    }
    check(
        checks, '117 restored amendment memberships are unique sponsors',
        len(sidecar) == len(sidecar_keys) == 117
        and all(
            'Amdt.' in str(row.get('bill') or '')
            and row.get('role') == 'sponsor'
            for row in sidecar
        ),
        '%d rows / %d unique acts'
        % (len(sidecar), len(sidecar_keys)),
    )
    registry_keys = {
        '%s|%s' % (
            str(row.get('bill') or '').strip(),
            str(row.get('congress') or '').strip(),
        )
        for row in registry_sidecar
    }
    registry_counts = registry_receipt.get('counts') or {}
    registry_gates = registry_receipt.get('gates') or {}
    check(
        checks, 'all 55 declared registry anchors are classified',
        registry_counts.get('declared_registry_anchors') == 55
        and registry_counts.get('missing_after') == 0
        and registry_counts.get('classified_act_universe') == 633
        and registry_receipt.get('missing_after') == [],
        str(registry_counts),
    )
    check(
        checks, 'six collection gaps are recovered from official BillStatus',
        registry_counts.get('missing_before') == 6
        and registry_counts.get('recovered_anchors') == 6
        and registry_counts.get('recovered_member_links') == 73
        and len(registry_sidecar) == 73
        and len(registry_keys) == 6,
        '%d rows / %d acts' % (len(registry_sidecar), len(registry_keys)),
    )
    check(
        checks, 'registry reconciliation gates are all closed',
        bool(registry_gates) and all(registry_gates.values()),
        str(registry_gates),
    )

    recomputed, baseline = PF.compute(
        bundle, tags, adjudicated_credits, cu_cells=cu_cells
    )
    drift = [
        member
        for member, values in recomputed.items()
        if abs(
            values['factor_shrunk']
            - committed['members'][member]['factor_shrunk']
        ) > 5e-7
    ]
    check(
        checks, 'producer exactly reproduces committed primacy factors',
        not drift,
        '%d mismatches; baseline %.6f' % (len(drift), baseline),
    )
    role_counts = collections.Counter()
    for member in bundle.get('members') or []:
        for bill in member.get('bills') or []:
            role_counts[(
                'letter'
                if str(bill.get('bill') or '').startswith('LTR-')
                else 'bill',
                bill.get('role'),
            )] += 1
    check(
        checks, 'bill and letter participation roles preserve 4:2:1 mass',
        PF.ROLE_W == {
            'sponsor': 1.0,
            'original': 0.5,
            'cosponsor': 0.25,
        }
        and role_counts[('bill', 'sponsor')] == 576
        and role_counts[('bill', 'original')] == 1953
        and role_counts[('bill', 'cosponsor')] == 2943
        and role_counts[('letter', 'sponsor')] == 11
        and role_counts[('letter', 'original')] == 276
        and role_counts[('letter', 'cosponsor')] == 0,
        str(dict(role_counts)),
    )
    tax_letter = tags.get('LTR-119-CUTAX-CISCOMANI|119') or {}
    check(
        checks, 'ratified CU-tax letter alone carries full class weight',
        tax_letter.get('class') == 'cu_primary'
        and tax_letter.get(PF.LETTER_OVERRIDE_FIELD) == 1.0
        and PF.act_weight(
            tax_letter, 'LTR-119-CUTAX-CISCOMANI|119'
        ) == 1.0,
    )
    check(
        checks, 'display placements are empty; adjudication is in math',
        TU.load_tier_overrides() == {}
        and TU.load_tier_positions() == {},
        'overrides=%s positions=%s' % (
            TU.load_tier_overrides(), TU.load_tier_positions()
        ),
    )
    expected_credit_members = {
        'Brad Sherman',
        'Bill Huizenga',
        'Young Kim',
        'Catherine Cortez Masto',
        'Tim Scott',
    }
    check(
        checks, 'five ratified leadership credits are uniform and active',
        set(adjudicated_credits) == expected_credit_members
        and all(
            row.get('denominator_delta') == 1.0
            and row.get('numerator_delta') == 1.0
            and len(row.get('events') or []) == 1
            for row in adjudicated_credits.values()
        )
        and committed.get('_constants', {}).get(
            'active_adjudication_events'
        ) == 5,
        str({
            member: {
                'denominator_delta': row.get('denominator_delta'),
                'numerator_delta': row.get('numerator_delta'),
                'events': row.get('events'),
            }
            for member, row in adjudicated_credits.items()
        }),
    )

    members = [row['name'] for row in bundle['members']]
    tier, tier_rank, detail = TU.legislative_press_tiers(
        members, with_detail=True
    )
    check(
        checks, 'tier bucket sizes remain exactly 13 / 24 / 48',
        (
            sum(value == 1 for value in tier.values()),
            sum(value == 2 for value in tier.values()),
            sum(value == 3 for value in tier.values()),
        ) == TU.BUCKETS,
    )
    engine_tier, engine_tier_rank, engine_priority = (
        MC.primacy_engine_state(members)
    )
    expected_engine_priority = {
        member: MC.primacy_engine_value(tier[member])
        for member in members
    }
    check(
        checks, 'allocation engine consumes the computed primacy tier',
        engine_tier == tier
        and engine_tier_rank == tier_rank
        and engine_priority == expected_engine_priority,
        str({
            'levels': MC.PRIMACY_ENGINE_LEVELS,
            'members': len(engine_priority),
        }),
    )
    dashboard_engine = dashboard.get('engine') or {}
    dashboard_constants = dashboard_engine.get('const') or {}
    expected_vector = [
        round(expected_engine_priority[member], 6)
        for member in members
    ]
    check(
        checks, 'dashboard scenario engine preserves raw tier anchor, not rank score',
        dashboard_engine.get('tierp') == expected_vector
        and 'sco' not in dashboard_engine
        and 'smax' not in dashboard_constants
        and dashboard_constants.get('PRIMACY_LEVELS') == {
            str(key): round(value, 6)
            for key, value in MC.PRIMACY_ENGINE_LEVELS.items()
        },
        str({
            'tierp': len(dashboard_engine.get('tierp') or []),
            'has_rank_score': 'sco' in dashboard_engine,
            'levels': dashboard_constants.get('PRIMACY_LEVELS'),
        }),
    )
    engine_categories = dashboard_engine.get('categories') or []
    expected_orientation = [
        [
            round(MC.engine_orientation(
                (stance.get('scores') or {}).get(member) or {},
                category,
            ), 6)
            for member in members
        ]
        for category in engine_categories
    ]
    expected_blended = [
        [
            round(MC.blended_engine_priority(
                tier[member],
                (stance.get('scores') or {}).get(member) or {},
                category,
                'flip',
            ), 6)
            for member in members
        ]
        for category in engine_categories
    ]
    check(
        checks, 'dashboard ships exact leave-one-topic-out blended priority',
        len(engine_categories) == len(dashboard_engine.get('eng') or [])
        and dashboard_engine.get('orientation') == expected_orientation
        and dashboard_engine.get('priority') == expected_blended
        and dashboard_constants.get(
            'PRIORITY_ORIENTATION_ALPHA'
        ) == MC.PRIORITY_ORIENTATION_ALPHA,
        str({
            'categories': len(engine_categories),
            'orientation_rows': len(
                dashboard_engine.get('orientation') or []
            ),
            'priority_rows': len(
                dashboard_engine.get('priority') or []
            ),
        }),
    )
    engine_names = (
            'flip_model.py',
            'per_topic_flip.py',
            'per_topic_coalition.py',
            'budget_frontier.py',
    )
    engine_sources = {
        name: (ROOT / 'fec' / 'engine' / name).read_text(encoding='utf-8')
        for name in engine_names
    }
    engine_sources['make_member_desk.py'] = (
        ROOT / 'fec' / 'surfaces' / 'make_member_desk.py'
    ).read_text(encoding='utf-8')
    engine_tokens = {
        'flip_model.py': 'blended_engine_priority',
        'per_topic_flip.py': 'blended_engine_priority',
        'per_topic_coalition.py': 'blended_engine_priority',
        'budget_frontier.py': 'blended_engine_priority',
        'make_member_desk.py': "eng.get('priority')",
    }
    check(
        checks, 'all production allocation paths use blended tier priority',
        all(
            engine_tokens[name] in source
            and 'score[n]/smax' not in source
            and "eng['sco']" not in source
            for name, source in engine_sources.items()
        ),
        str({
            name: {
                'tier': engine_tokens[name] in source,
                'rank_score': (
                    'score[n]/smax' in source
                    or "eng['sco']" in source
                ),
            }
            for name, source in engine_sources.items()
        }),
    )
    # RE-PINNED 2026-07-29 to the ratified seating under the current board
    # basis. Three same-day directives moved these seats: factor-only
    # (@89b6219) dropped Huizenga and Kim out of Tier 1; the evidence floor
    # (@cae6c58) left them; the uncertainty penalty (@99739ec) returned Kim
    # to Tier 1 at 13 on her own full-evidence measurement. Huizenga stays at
    # 16 -- his gap is structural bank/crypto ballast, which the purity law
    # prices, not measurement noise. All five keep their +1.0 adjudicated
    # credit in the FACTOR math, which is still asserted below; only the
    # SEATING changed. Seats are pinned exactly, not relaxed. The displayed
    # score follows the active basis so the evidence string stays truthful.
    _seat = (
        {'Brad Sherman': 1, 'Catherine Cortez Masto': 1, 'Tim Scott': 1,
         'Young Kim': 2, 'Bill Huizenga': 2}
        if TU.PRIMACY_BOARD == 'factor'
        else {member: 1 for member in expected_credit_members}
    )
    _limit = (
        {'Brad Sherman': 13, 'Catherine Cortez Masto': 13, 'Tim Scott': 12,
         'Young Kim': 14, 'Bill Huizenga': 16}
        if TU.PRIMACY_BOARD == 'factor'
        else {
            member: (12 if member == 'Tim Scott' else 13)
            for member in expected_credit_members
        }
    )
    check(
        checks,
        'five user-adjudicated members hold their ratified seats',
        all(
            tier.get(member) == _seat[member]
            and tier_rank.get(member) <= _limit[member]
            for member in expected_credit_members
        )
        and all(
            (committed['members'][member].get(
                'adjudicated_credit'
            ) == 1.0)
            for member in expected_credit_members
        ),
        str({
            member: {
                'tier': tier.get(member),
                'rank': tier_rank.get(member),
                'score': (
                    committed['members'][member].get(
                        'factor_ranked',
                        committed['members'][member]['factor_shrunk'],
                    )
                    if TU.PRIMACY_BOARD == 'factor'
                    else ranks['pcts'][member]['leg']
                    * committed['members'][member]['factor_shrunk']
                ),
            }
            for member in sorted(expected_credit_members)
        }),
    )
    math_gates = math_review.get('gates') or {}
    check(
        checks, 'local Opus-5-method math review closes every gate',
        math_review.get('status') == 'PASS_SCORE_BASED_ADJUDICATION'
        and bool(math_gates)
        and all(math_gates.values())
        and (
            math_review.get('execution') or {}
        ).get('provider_call_performed') is False
        and (
            math_review.get('delta_summary') or {}
        ).get('official_rank_delta') == 0,
        str({
            'status': math_review.get('status'),
            'failed': [
                name for name, passed in math_gates.items()
                if not passed
            ],
        }),
    )
    boundary_gates = boundary_delta.get('gates') or {}
    boundary_summary = boundary_delta.get('summary') or {}
    boundary_transitions = boundary_delta.get(
        'tier_transitions'
    ) or {}
    check(
        checks, '13/37/85 boundary delta is explicitly reconciled',
        bool(boundary_gates)
        and all(boundary_gates.values())
        and boundary_summary.get('members_with_tier_change') == 15
        and boundary_summary.get('official_rank_delta') == 0
        and boundary_summary.get('official_score_delta') == 0
        and all(
            len(boundary_transitions.get(key) or []) == 5
            for key in ('1->2', '2->3', '3->0')
        ),
        str(boundary_summary),
    )
    barr = detail.get('Andy Barr') or {}
    hill = detail.get('French Hill') or {}
    check(
        checks, 'conduct ledger reproduces Barr gross/delivery/net/ceiling',
        barr.get('gross_severity') == 7.25
        and barr.get('delivered_credit') == 0.0
        and barr.get('net_severity') == 7.25
        and barr.get('tier_ceiling') == 3,
        str(barr),
    )
    check(
        checks, 'conduct ledger reproduces Hill gross/delivery/net/ceiling',
        hill.get('gross_severity') == 8.0
        and hill.get('delivered_credit') == 3.0
        and hill.get('net_severity') == 5.0
        and hill.get('tier_ceiling') == 2,
        str(hill),
    )
    grievance_detail = (
        (barr.get('grievances') or [])
        + (hill.get('grievances') or [])
    )
    check(
        checks, 'every displayed grievance carries reason and cure',
        len(grievance_detail) == 4
        and all(
            str(row.get('reason') or '').strip()
            and str(row.get('cure') or '').strip()
            for row in grievance_detail
        ),
        '%d detailed grievance events' % len(grievance_detail),
    )
    hill_delivery_detail = hill.get('deliveries') or []
    check(
        checks, 'Hill delivery evidence identifies bill and instrument',
        {row.get('bill') for row in hill_delivery_detail}
        >= {'H.R.975', 'H.R.4437', 'H.R.4544'}
        and all(
            str(row.get('instrument') or '').strip()
            and float(row.get('credit') or 0) > 0
            for row in hill_delivery_detail
        ),
        str(hill_delivery_detail),
    )
    check(
        checks, 'conduct cannot crown either constrained member',
        tier.get('Andy Barr') not in (1, 2)
        and tier.get('French Hill') != 1,
        'Barr #%s T%s; Hill #%s T%s'
        % (
            tier_rank.get('Andy Barr'), tier.get('Andy Barr'),
            tier_rank.get('French Hill'), tier.get('French Hill'),
        ),
    )

    member_detail = {
        row['name']: row.get('tier_detail')
        for row in bundle['members']
    }
    dashboard_detail = dict(zip(
        dashboard.get('name') or [],
        dashboard.get('tierdetail') or [],
    ))
    check(
        checks, 'member desk exposes grievance and delivery evidence',
        member_detail.get('Andy Barr') == barr
        and member_detail.get('French Hill') == hill,
    )
    check(
        checks, 'dashboard exposes the same aligned conduct evidence',
        dashboard_detail.get('Andy Barr') == barr
        and dashboard_detail.get('French Hill') == hill,
    )
    dashboard_html = (
        ROOT / 'Moneyball Dashboard.html'
    ).read_text(encoding='utf-8')
    member_desk_html = (
        ROOT / 'cu-member-desk.html'
    ).read_text(encoding='utf-8')
    check(
        checks, 'dashboard HTML renders grievance reason and cure',
        'Cure:' in dashboard_html
        and 'Refused credit-union asks' in dashboard_html,
    )
    check(
        checks, 'member desk HTML renders cure and delivery receipts',
        'Cure:' in member_desk_html
        and all(
            bill in member_desk_html
            for bill in ('H.R.975', 'H.R.4437', 'H.R.4544')
        ),
    )

    routes = readiness.get('cfpb_route_checks') or {}
    check(
        checks, 'CFPB keeps the ratified restrain-positive/reach-negative route',
        bool(routes) and all(routes.values()),
        str(routes),
    )
    workbook = load_workbook(
        ROOT / 'Moneyball Main Version - Blended.xlsx',
        read_only=True,
        data_only=False,
    )
    _raw_first = roster.first_row('Legislative Score - Raw')
    _raw_last = roster.last_row('Legislative Score - Raw')
    try:
        raw = workbook['Legislative Score - Raw']
        rank_formula = str(
            workbook['Rank & Allocation']['D3'].value or ''
        )
        live_routes = {
            'restrain_header': (
                raw['CU1'].value == 'CFPB Restrain - Sponsor'
            ),
            'reach_header': (
                raw['EO1'].value == 'CFPB Reach (-) - Sponsor'
            ),
            'restrain_positive': '$CU$%d:$CY$%d' % (_raw_first, _raw_last) in rank_formula,
            'reach_negative': (
                '$EO$%d:$ES$%d' % (_raw_first, _raw_last) in rank_formula
                and '*(-1)' in rank_formula
            ),
        }
    finally:
        workbook.close()
    check(
        checks, 'production workbook still carries the CFPB route directly',
        all(live_routes.values()),
        str(live_routes),
    )
    counts = culling.get('counts') or {}
    check(
        checks, 'all culling dispositions are canonically accounted for',
        counts.get('scoring_ready_candidates') == 60
        and counts.get('scoring_ready_category_edges') == 74
        and counts.get('held_decision_units') == 1184
        and (
            counts.get('integration_state_counts') or {}
        ).get('excluded_false_positive') == 2908,
        str({
            'scoring_candidates': counts.get('scoring_ready_candidates'),
            'scoring_edges': counts.get('scoring_ready_category_edges'),
            'held': counts.get('held_decision_units'),
            'excluded': (
                counts.get('integration_state_counts') or {}
            ).get('excluded_false_positive'),
        }),
    )
    check(
        checks, 'surfaces report the hash-bound production culling state',
        culling_status.get('status')
        == 'PASS_PRODUCTION_CULLING_INTEGRATED'
        and culling_status.get('phase') == 'production'
        and culling_status.get('promotion_performed') is True
        and culling_status.get('production_workbook_written') is True
        and culling_status.get('rank_written') is True
        and (
            culling_status.get('promotion_receipt') or {}
        ).get('status') == 'PASS_PRODUCTION_INPUTS_PROMOTED',
        str({
            'status': culling_status.get('status'),
            'phase': culling_status.get('phase'),
            'promotion': culling_status.get('promotion_receipt'),
        }),
    )

    class_counts = collections.Counter(
        row['class'] for row in tags.values()
    )
    check(
        checks, 'every beneficiary class is recognized by the engine',
        not (set(class_counts) - set(PF.ACT_W)),
        str(dict(sorted(class_counts.items()))),
    )
    tier_one = {
        member for member, value in tier.items() if value == 1
    }
    bundle_by_name = {
        row['name']: row for row in bundle['members']
    }
    opposed_tier_one = []
    for member in tier_one:
        for bill in bundle_by_name[member].get('bills') or []:
            key = '%s|%s' % (
                str(bill.get('bill') or '').strip(),
                str(bill.get('congress') or '').strip(),
            )
            if tags[key]['class'] == 'oppose':
                opposed_tier_one.append((member, key))
    check(
        checks, 'no primacy Tier-1 member carries an OPPOSE act',
        not opposed_tier_one,
        str(opposed_tier_one[:10]),
    )
    fixture_path = (
        ROOT / 'fec' / 'tests' / 'fixtures'
        / 'FIXTURE_expected_board.csv'
    )
    with fixture_path.open(encoding='utf-8', newline='') as handle:
        fixture = list(csv.DictReader(handle))
    fixture_members = [row['member'] for row in fixture]
    actual_board = [
        member for member, _rank in sorted(
            tier_rank.items(), key=lambda item: item[1]
        )
    ]
    check(
        checks, '533-row primacy board matches the frozen fixture',
        len(fixture_members) == 533 and fixture_members == actual_board,
        '%d fixture rows / %d board rows'
        % (len(fixture_members), len(actual_board)),
    )

    rank_hash_after = sha256(ranks_path)
    check(
        checks, 'primacy verification leaves official ranks byte-identical',
        rank_hash_after == rank_hash_before,
        rank_hash_after,
    )
    promoted_rank_hash = (
        promotion.get('installed', {})
        .get('ranks_python.json', {})
        .get('sha256')
    )
    check(
        checks, 'official ranks still equal the greenlit treatment authority',
        rank_hash_after == promoted_rank_hash,
        rank_hash_after,
    )
    rank_summary = rank_delta.get('summary') or {}
    check(
        checks, 'paired rank delta and exact control replay remain attached',
        (rank_delta.get('control_replay') or {}).get(
            'exact_rank_replay'
        ) is True
        and rank_summary.get('member_count') == 533
        and rank_summary.get('members_with_rank_change') == 475
        and rank_summary.get('max_abs_rank_delta') == 65,
        str(rank_summary),
    )
    failed = [
        name for name, result in checks.items()
        if not result['passed']
    ]
    receipt = {
        'schema': 'greenlit-primacy-production-verification-v1',
        'status': (
            'PASS_PRODUCTION_PRIMACY_INTEGRATION'
            if not failed else 'FAIL_PRODUCTION_PRIMACY_INTEGRATION'
        ),
        'verified_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'checks': checks,
        'failed_checks': failed,
        'counts': {
            'members': len(members),
            'classified_acts': len(tags),
            'restored_amendment_memberships': len(sidecar),
            'registry_anchor_memberships': len(registry_sidecar),
            'registry_anchors': len(registry_keys),
            'beneficiary_classes': dict(sorted(class_counts.items())),
            'tier_1': sum(value == 1 for value in tier.values()),
            'tier_2': sum(value == 2 for value in tier.values()),
            'tier_3': sum(value == 3 for value in tier.values()),
        },
        'tier_1_members': sorted(tier_one, key=tier_rank.get),
        'engine_priority_levels': MC.PRIMACY_ENGINE_LEVELS,
        'legislation_rank_delta': rank_summary,
        'official_rank_sha256': rank_hash_after,
    }
    atomic_json(OUT, receipt)
    print('%d checks, %d failed' % (len(checks), len(failed)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
