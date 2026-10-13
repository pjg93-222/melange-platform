#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build beneficiary-primacy factors for the tier board and allocation engine.

Reads the member desk act universe, one beneficiary tag per act, optional
human-ratified credits from ``primacy_adjudication_ledger.ndjson``, and the
optional credit-union column of ``industry_axes.ndjson``. Writes
``primacy_factors.json`` without touching the official rank authority.
``PRIMACY_CU_TENSOR=0`` skips the CU tensor loader and restores legacy output;
the tensor is enabled by default.
"""
from __future__ import annotations

import collections
import json
import math
import os
import sys

FEC_BOOTSTRAP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FEC_BOOTSTRAP not in sys.path:
    sys.path.insert(0, FEC_BOOTSTRAP)

import legislation_measure_key as MK
from engine.primacy_method import DIAGNOSTIC


ROLE_W = {'sponsor': 1.0, 'original': 0.5, 'cosponsor': 0.25}
ACT_W = {
    'cu_primary': 1.00,
    'shared': 0.50,
    'bank_primary': 0.15,
    'crypto_primary': 0.15,
    'fintech': 0.15,
    'telecom_primary': 0.15,
    'offtopic': 0.00,
    'oppose': -1.00,
}
ACU_RELEVANCE = {'SUPPORT': 0.5, 'OPPOSE': -1.0}
ACU_RELEVANCE_ACTIVE = True
LETTER_W = float(os.environ.get('PRIMACY_LETTER_W', '0.60'))
LETTER_OVERRIDE_FIELD = 'letter_weight_override'
SHRINK_ANCHOR = float(os.environ.get('PRIMACY_SHRINK', '15.0'))
# Uncertainty penalty ("certainty outranks estimation", user-ratified
# 2026-07-29): the board ranks on factor_ranked = factor_shrunk -
# K*(1-lambda)*|factor_raw - baseline|, discounting exactly the UNVERIFIED
# portion of a member's distance from baseline. A full-evidence member
# (lambda=1) is mathematically untouched; a thin-evidence point estimate can
# no longer outrank a fully-measured value at a tier boundary. K=0.25 was
# ratified as the smallest value expressing the principle, chosen over
# 0.5-1.0 explicitly so Juan Vargas keeps his Tier 1 seat ("Vargas needs to
# be in") -- his 6.25-credit portfolio is thin in volume, not in CU purity
# (4.0 cu_primary credits). PRIMACY_UNCERT_K=0 removes the penalty and
# restores the pure factor_shrunk board.
# 2026-09-20: user adopted CLASS_DOCK_PRESS_GRADE_ALL. Ordinary shrinkage
# remains; the additional uncertainty dock is off. PRIMACY_METHOD=legacy
# restores the prior default alongside the prior grade and press statistic.
UNCERT_K = float(os.environ.get('PRIMACY_UNCERT_K', '0' if DIAGNOSTIC else '0.25'))
if UNCERT_K < 0:
    raise ValueError(
        'PRIMACY_UNCERT_K must be >= 0; got %r' % UNCERT_K
    )
# Measure weight: bills 1.0, amendments 0.5 (user-ratified 2026-07-29,
# "0.5 applies broad, same handling"). Sourced from legislation_measure_key so
# the bill/amendment distinction has ONE definition in the tree. It multiplies
# the ROLE weight, so it enters the numerator AND the denominator -- an
# amendment is half an act, not a full act worth half. Applying it to the act
# weight instead would have shrunk only the numerator, which perversely
# rewarded members holding off-topic amendments.
#
# THE DEFECT THIS CLOSES: AMENDMENT_MEASURE_WEIGHT=0.5 is enforced elsewhere in
# the tree (run_greenlit_legislation_rank_shadow raises if a scoring amendment
# is not 0.5), but the desk bundle carries no measure type and primacy_factors
# read only `role`. All 173 amendments arrived as role='sponsor' and collected
# the full 1.0, so sponsoring a one-line floor amendment counted exactly as
# much as sponsoring an entire bill. PRIMACY_AMENDMENT_W=1.0 restores that.
AMENDMENT_W = float(
    os.environ.get('PRIMACY_AMENDMENT_W', MK.AMENDMENT_MEASURE_WEIGHT)
)
if not 0.0 <= AMENDMENT_W <= 1.0:
    raise ValueError(
        'PRIMACY_AMENDMENT_W must be between 0 and 1; got %r' % AMENDMENT_W
    )
DEFAULT_ROLE_W = 0.25
AFFIRMATIVE_PRIMARY_POLICY = 'positive-cu-primary-contribution-v1'
AFFIRMATIVE_PRIMARY_FIELD = 'cu_primary_affirmative_credit'
# Eligibility only; zero restores the prior factor artifact as well as seating.
PRIMACY_T1_MIN_AFFIRMATIVE = float(
    os.environ.get('PRIMACY_T1_MIN_AFFIRMATIVE') or '0.75')
if not math.isfinite(PRIMACY_T1_MIN_AFFIRMATIVE) or PRIMACY_T1_MIN_AFFIRMATIVE < 0:
    raise ValueError('PRIMACY_T1_MIN_AFFIRMATIVE must be finite and >= 0')
ADJUDICATION_LEDGER = 'data/engine/primacy_adjudication_ledger.ndjson'
CU_TENSOR = 'data/lobbying/industry_axes.ndjson'
SYNTHETIC_CELL_PREFIXES = (
    'credit_union_cell_', 'bank_cell_', 'crypto_cell_', 'fintech_cell_',
    'telecom_cell_',
)


def load_tags(path):
    """Load exactly one beneficiary classification for each act key."""
    out = {}
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = '%s|%s' % (
                str(row['bill']).strip(),
                str(row['congress']).strip(),
            )
            if key in out:
                raise ValueError('duplicate beneficiary tag: %s' % key)
            out[key] = row
    if not out:
        raise ValueError(
            'beneficiary tag file is empty; refusing to score an '
            'unclassified universe'
        )
    return out


def load_cu_tensor_cells(path):
    """Load active credit-union tensor cells as exact effective weights."""
    if not os.path.exists(path):
        print(
            'WARNING: CU tensor %s is missing; declared-position floor '
            'remains in force' % path,
            file=sys.stderr,
        )
        return {}
    out = {}
    seen = set()
    required = (
        'bill', 'congress', 'industry', 'basis', 'rationale',
        'adjudicated_by', 'adjudicated', 'status',
    )
    with open(path, encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if str(row.get('industry') or '').strip() != 'credit_union':
                continue

            normalized = {}
            for field in required:
                value = str(row.get(field) or '').strip()
                if not value:
                    raise ValueError(
                        'industry axes line %d field %s must be non-empty'
                        % (line_number, field)
                    )
                normalized[field] = value

            status = normalized['status']
            if status not in ('active', 'inactive'):
                raise ValueError(
                    'industry axes line %d status must be active or '
                    'inactive; got %r' % (line_number, status)
                )

            relevance = _cu_tensor_number(
                row.get('relevance'),
                'industry axes line %d relevance' % line_number,
            )
            if not 0.0 <= relevance <= 1.0:
                raise ValueError(
                    'industry axes line %d relevance must be in [0, 1]; '
                    'got %r' % (line_number, relevance)
                )

            valence = _cu_tensor_number(
                row.get('valence'),
                'industry axes line %d valence' % line_number,
            )
            if not -1.0 <= valence <= 1.0:
                raise ValueError(
                    'industry axes line %d valence must be in [-1, 1]; '
                    'got %r' % (line_number, valence)
                )

            confidence = _cu_tensor_number(
                row.get('confidence'),
                'industry axes line %d confidence' % line_number,
            )
            if not 0.0 < confidence <= 1.0:
                raise ValueError(
                    'industry axes line %d confidence must be in '
                    '(0, 1]; got %r' % (line_number, confidence)
                )

            key = (normalized['bill'], normalized['congress'])
            duplicate_key = key + (normalized['industry'],)
            if duplicate_key in seen:
                raise ValueError(
                    'duplicate industry axes cell at line %d: %r'
                    % (line_number, duplicate_key)
                )
            seen.add(duplicate_key)

            if status != 'active':
                continue
            out[key] = round(relevance * valence, 6)
    return out


def _cu_tensor_number(value, label):
    if isinstance(value, bool):
        raise ValueError('%s must be numeric' % label)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('%s must be numeric' % label) from exc
    if not math.isfinite(number):
        raise ValueError('%s must be finite' % label)
    return number


def load_adjudicated_credits(path):
    """Load transparent, human-ratified score inputs.

    Each active row is processed exactly like one additional classified role:
    ``role_weight`` enters the denominator and
    ``role_weight * ACT_W[beneficiary_class]`` enters the numerator.  The
    ledger therefore changes the factor math, never a display position.
    """
    if not os.path.exists(path):
        return {}
    out = {}
    event_ids = set()
    with open(path, encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            event_id = str(row.get('event_id') or '').strip()
            member = str(row.get('member') or '').strip()
            status = str(row.get('status') or 'active').strip()
            beneficiary_class = str(
                row.get('beneficiary_class') or ''
            ).strip()
            if not event_id or not member:
                raise ValueError(
                    'adjudication row %d requires event_id and member'
                    % line_number
                )
            if event_id in event_ids:
                raise ValueError(
                    'duplicate adjudication event_id: %s' % event_id
                )
            event_ids.add(event_id)
            if status != 'active':
                continue
            if beneficiary_class not in ACT_W:
                raise ValueError(
                    'unknown adjudicated beneficiary class %r on %s'
                    % (beneficiary_class, event_id)
                )
            raw_role_weight = row.get('role_weight', 0)
            if isinstance(raw_role_weight, bool):
                raise ValueError('adjudication %s role_weight must not be boolean' % event_id)
            role_weight = float(raw_role_weight or 0)
            if not math.isfinite(role_weight) or role_weight <= 0:
                raise ValueError(
                    'adjudication %s requires positive role_weight'
                    % event_id
                )
            record = out.setdefault(
                member,
                {
                    'denominator_delta': 0.0,
                    'numerator_delta': 0.0,
                    'mix': collections.defaultdict(float),
                    'events': [],
                },
            )
            record['denominator_delta'] += role_weight
            record['numerator_delta'] += (
                role_weight * ACT_W[beneficiary_class]
            )
            record['mix'][beneficiary_class] += role_weight
            record['events'].append(event_id)
    return out


def is_letter(bill_key):
    return bill_key.split('|')[0].startswith('LTR-')


def _is_synthetic_cell_class(beneficiary_class):
    return any(
        beneficiary_class.startswith(prefix)
        and beneficiary_class[len(prefix):].isdigit()
        for prefix in SYNTHETIC_CELL_PREFIXES
    )


def _cu_tensor_key(bill_key):
    parts = bill_key.rsplit('|', 1)
    return tuple(parts) if len(parts) == 2 else None


def act_weight(tag, bill_key, cu_cells=None):
    if is_letter(bill_key):
        override = tag.get(LETTER_OVERRIDE_FIELD)
        if override is not None:
            try:
                override = float(override)
            except (TypeError, ValueError):
                raise ValueError(
                    'letter weight override must be numeric on %s'
                    % bill_key
                )
            if not -1.0 <= override <= 1.0:
                raise ValueError(
                    'letter weight override must be between -1 and 1 '
                    'on %s' % bill_key
                )
            return override
        return LETTER_W
    beneficiary_class = tag['class']
    if beneficiary_class not in ACT_W:
        raise ValueError(
            'unknown beneficiary class %r on %s'
            % (beneficiary_class, bill_key)
        )
    if _is_synthetic_cell_class(beneficiary_class):
        return ACT_W[beneficiary_class]
    tensor_key = _cu_tensor_key(bill_key)
    # An adjudicated operative-text cell takes precedence over a declared
    # position wherever that position would have set the weight, and never
    # reaches acts the declared-position floor would not have touched.
    if (
        ACU_RELEVANCE_ACTIVE
        and cu_cells is not None
        and tensor_key in cu_cells
        and str(tag.get('acu_position') or '').strip().upper()
        in ('SUPPORT', 'OPPOSE')
    ):
        return cu_cells[tensor_key]
    weight = ACT_W[beneficiary_class]
    if ACU_RELEVANCE_ACTIVE and tag.get('authority') == 'automated-official-evidence-policy':
        directions = tag['automated_category_directions']
        if not directions or any(type(d) is not int or d not in (-1,0,1) for d in directions):
            raise ValueError('invalid automated CU directions on ' + bill_key)
        # Category contributions are equally weighted, as the category engine
        # does. A beneficiary label alone must never turn a harmful bill positive.
        return abs(weight) * sum(directions) / len(directions)
    if ACU_RELEVANCE_ACTIVE:
        pos = str(tag.get('acu_position') or '').strip().upper()
        if pos == 'SUPPORT':
            weight = max(weight, ACU_RELEVANCE['SUPPORT'])
        elif pos == 'OPPOSE':
            weight = min(weight, ACU_RELEVANCE['OPPOSE'])
    return weight


def compute(bundle, tags, adjudicated_credits=None, cu_cells=None):
    """Return member factors and the agenda-weighted roster baseline."""
    adjudicated_credits = adjudicated_credits or {}
    roster = {
        str(record.get('name') or '').strip()
        for record in bundle.get('members') or []
    }
    if len(roster) != len(bundle.get('members') or []) or '' in roster:
        raise ValueError('duplicate or empty primacy roster member')
    unknown = sorted(set(adjudicated_credits) - roster)
    if unknown:
        raise ValueError(
            'adjudication members are not in the rankable roster: %s'
            % unknown
        )
    members = {}
    for record in bundle['members']:
        numerator = denominator = 0.0
        affirmative_credit = 0.0
        amendment_credit = 0.0
        mix = collections.defaultdict(float)
        seen_acts = set()
        for bill in record.get('bills') or []:
            display_id = str(bill.get('bill') or '').strip()
            key = '%s|%s' % (
                display_id,
                str(bill.get('congress') or '').strip(),
            )
            if key in seen_acts:
                raise ValueError('duplicate primacy act for %s: %s' % (record['name'],key))
            seen_acts.add(key)
            if bill.get('role') not in ROLE_W:
                raise ValueError('invalid primacy role for %s: %r' % (key,bill.get('role')))
            tag = tags.get(key)
            if tag is None:
                raise KeyError(
                    'act %s is in the bundle but not in the tag file; '
                    'classification and universe are out of sync' % key
                )
            role_weight = ROLE_W.get(
                bill.get('role'), DEFAULT_ROLE_W
            )
            # Measure weight scales the ROLE contribution, so an amendment is
            # half an act in both the numerator and the denominator.
            if MK.is_display_amendment(display_id):
                role_weight *= AMENDMENT_W
                amendment_credit += role_weight
            weight = act_weight(tag, key, cu_cells=cu_cells)
            denominator += role_weight
            numerator += role_weight * weight
            if tag['class'] == 'cu_primary' and not is_letter(key):
                affirmative_credit += role_weight * max(weight, 0.0)
            mix[
                'letter' if is_letter(key) else tag['class']
            ] += role_weight
        adjudicated = adjudicated_credits.get(record['name']) or {}
        denominator += float(
            adjudicated.get('denominator_delta', 0) or 0
        )
        numerator += float(
            adjudicated.get('numerator_delta', 0) or 0
        )
        for beneficiary_class, role_credit in (
            adjudicated.get('mix') or {}
        ).items():
            mix[beneficiary_class] += float(role_credit)
        if PRIMACY_T1_MIN_AFFIRMATIVE > 0:
            raw_primary = (adjudicated.get('mix') or {}).get('cu_primary', 0.0)
            if isinstance(raw_primary, bool):
                raise ValueError('adjudicated CU-primary credit must not be boolean')
            primary = float(raw_primary)
            if not math.isfinite(primary) or primary < 0:
                raise ValueError('adjudicated CU-primary credit must be finite and nonnegative')
            affirmative_credit += primary * max(ACT_W['cu_primary'], 0.0)
            if not math.isfinite(affirmative_credit) or affirmative_credit < 0:
                raise ValueError('affirmative CU-primary credit must be finite and nonnegative')
        members[record['name']] = {
            'factor_raw': (
                numerator / denominator if denominator else 1.0
            ),
            'agenda_credit': round(denominator, 3),
            # Visible so the amendment share of a portfolio is auditable
            # rather than buried inside agenda_credit (2026-07-29).
            'amendment_credit': round(amendment_credit, 3),
            'adjudicated_credit': round(
                float(adjudicated.get('denominator_delta', 0) or 0),
                3,
            ),
            'adjudicated_score_credit': round(
                float(adjudicated.get('numerator_delta', 0) or 0),
                3,
            ),
            'adjudicated_events': list(
                adjudicated.get('events') or []
            ),
            'mix': {
                key: round(value, 2)
                for key, value in mix.items()
            },
        }
        if PRIMACY_T1_MIN_AFFIRMATIVE > 0:
            members[record['name']][AFFIRMATIVE_PRIMARY_FIELD] = affirmative_credit
    total_credit = sum(
        values['agenda_credit'] for values in members.values()
    )
    if total_credit <= 0:
        raise ValueError('roster has zero total agenda credit')
    baseline = (
        sum(
            values['factor_raw'] * values['agenda_credit']
            for values in members.values()
        )
        / total_credit
    )
    for values in members.values():
        shrink = min(1.0, values['agenda_credit'] / SHRINK_ANCHOR)
        values['lambda'] = round(shrink, 6)
        values['factor_shrunk'] = (
            baseline
            + shrink * (values['factor_raw'] - baseline)
        )
        # Ranking value: discount the unverified share of the member's
        # distance from baseline. lambda=1 members are untouched.
        values['factor_ranked'] = (
            values['factor_shrunk']
            - UNCERT_K * (1.0 - shrink)
            * abs(values['factor_raw'] - baseline)
        )
    return members, baseline


def with_realized_results(document, comparison):
    """Opt-in shadow artifact; orientation values remain independently inspectable."""
    from copy import deepcopy
    from engine.effectiveness_integration import SCHEMA, BOARD_MODE
    if comparison.get('schema') != SCHEMA or comparison.get('status') != 'shadow_candidate':
        raise ValueError('Expected versioned shadow effectiveness comparison')
    result = deepcopy(document)
    result['effectiveness_shadow'] = {
        'schema': SCHEMA, 'status': 'shadow_candidate', 'board_mode': BOARD_MODE,
        'source_sha256': comparison['source_sha256'], 'congress': comparison['congress'],
        'as_of': comparison.get('as_of'), 'evidence_gate': comparison['results_evidence_gate'],
    }
    for name, values in result['members'].items():
        candidate = comparison['members'].get(name, {})
        values['realized_results'] = candidate.get('realized_results')
        values['advocacy_impact'] = deepcopy(candidate.get('advocacy_impact'))
        values['results_distinct_measures'] = candidate.get('distinct_signed_measures')
        values['results_coverage'] = deepcopy(candidate.get('coverage', {'member_term': 'not_covered'}))
    return result


def main(root='.'):
    path = lambda *parts: os.path.join(root, *parts)
    with open(path('member_desk_bundle.json'), encoding='utf-8') as handle:
        bundle = json.load(handle)
    tags = load_tags(path('bill_beneficiary_tags.ndjson'))
    from legislation import sentinel_admissions as SA
    tags = SA.effective_tags(tags, SA.load(root))
    adjudicated_credits = load_adjudicated_credits(
        path(ADJUDICATION_LEDGER)
    )
    cu_cells = {}
    if os.environ.get('PRIMACY_CU_TENSOR', '1') != '0':
        cu_cells = load_cu_tensor_cells(path(CU_TENSOR))
    bundle_keys = {
        '%s|%s' % (
            str(bill.get('bill') or '').strip(),
            str(bill.get('congress') or '').strip(),
        )
        for member in bundle.get('members') or []
        for bill in member.get('bills') or []
    }
    if bundle_keys != set(tags):
        missing = sorted(bundle_keys - set(tags))
        extra = sorted(set(tags) - bundle_keys)
        raise ValueError(
            'act universe and beneficiary tags are not closed: '
            'missing=%s extra=%s' % (missing[:10], extra[:10])
        )
    members, baseline = compute(
        bundle, tags, adjudicated_credits, cu_cells=cu_cells
    )
    cu_tensor_cells_applied = (
        sum(
            1
            for key in bundle_keys
            if not is_letter(key)
            and not _is_synthetic_cell_class(tags[key]['class'])
            and _cu_tensor_key(key) in cu_cells
            and str(tags[key].get('acu_position') or '').strip()
        )
        if ACU_RELEVANCE_ACTIVE else 0
    )
    constants = {
        'role_weights': ROLE_W,
        'act_weights': ACT_W,
        'acu_position_relevance': {
            k: float(v) for k, v in sorted(ACU_RELEVANCE.items())
        },
        'letter_weight': LETTER_W,
        'uncertainty_penalty_k': UNCERT_K,
        'amendment_measure_weight': AMENDMENT_W,
        'measure_weight_policy': MK.measure_weight_policy(),
        'letter_weight_overrides': {
            key: float(tag[LETTER_OVERRIDE_FIELD])
            for key, tag in sorted(tags.items())
            if tag.get(LETTER_OVERRIDE_FIELD) is not None
        },
        'shrink_anchor': SHRINK_ANCHOR,
        'roster_baseline': round(baseline, 6),
        'classified_act_count': len(tags),
        'adjudication_ledger': ADJUDICATION_LEDGER,
        'active_adjudication_events': sum(
            len(row.get('events') or [])
            for row in adjudicated_credits.values()
        ),
    }
    if PRIMACY_T1_MIN_AFFIRMATIVE > 0:
        constants['affirmative_primary_credit_policy'] = AFFIRMATIVE_PRIMARY_POLICY
    if cu_tensor_cells_applied:
        constants['cu_tensor_cells_applied'] = cu_tensor_cells_applied
    document = {
        '_doc': (
            'Primacy factors for the tier board and allocation engine. '
            'Producer: fec/engine/primacy_factors.py. Active adjudication '
            'credits enter the same numerator/denominator math as '
            'classified roles; they are not display positions.'
        ),
        '_constants': constants,
        'members': {
            member: {
                key: (
                    round(value, 6)
                    if isinstance(value, float) else value
                )
                for key, value in values.items()
            }
            for member, values in sorted(members.items())
        },
    }
    output_path = path('data/engine/primacy_factors.json')
    comparison_path = os.environ.get('EFFECTIVENESS_COMPARISON_PATH')
    if comparison_path:
        from engine.effectiveness_integration import load_shadow_comparison, require_shadow_path
        shadow_root = os.environ.get('EFFECTIVENESS_SHADOW_ROOT')
        output_override = os.environ.get('PRIMACY_SHADOW_OUT')
        if not output_override:
            raise ValueError('Effectiveness sidecar requires explicit PRIMACY_SHADOW_OUT')
        output_path = str(require_shadow_path(output_override, shadow_root))
        document = with_realized_results(document, load_shadow_comparison(
            comparison_path, shadow_root=shadow_root))
    try:
        from moneyball_core import dump_atomic
        dump_atomic(document, output_path)
    except ImportError:
        with open(
            output_path, 'w', encoding='utf-8', newline='\r\n'
        ) as handle:
            json.dump(document, handle, indent=1, ensure_ascii=False)
    print(
        'primacy_factors: %d members | baseline %.6f | '
        'letter_w %.2f | shrink %.1f | adjudications %d'
        % (
            len(members), baseline, LETTER_W, SHRINK_ANCHOR,
            sum(
                len(row.get('events') or [])
                for row in adjudicated_credits.values()
            ),
        )
    )
    return document


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
