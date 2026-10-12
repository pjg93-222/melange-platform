"""Pure behavior-qualified honors. No data or environment reads at import.

Scores retain canonical 12-place source precision; percentages operate on
ordinal rank intervals and never normalize or weight the underlying score.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from itertools import groupby

SCHEMA = 'rank-honors-v1'
SCHEMA_LADDER_G = 'rank-honors-v2'
POLICY_ID = 'field_rank_honors_v1'
PRECISION = Decimal('0.000000000001')
CONVERSION_TOLERANCE = Decimal('0.000000001')
POLICY = {
    'id': POLICY_ID, 'primary': 'field_action_sum_v1',
    'champion_pct': 5, 'ally_pct': 15, 'highly_persuadable_pct': 20,
    'vote_weight': '1', 'congresses': [117, 118, 119],
    'role_weights': {'sponsor': '3', 'original-cosponsor': '2', 'cosponsor': '1'},
    'congress_decay': {'117': '0.64', '118': '0.8', '119': '1'},
    'decimal_places': 12, 'archive_conversion_tolerance': str(CONVERSION_TOLERANCE),
    'boundary_rule': 'entire_residual_tie_must_fit_floor_cutoff',
    'tensor_rule': 'complete_equal_primary_block_only',
    'positive_support_required': True, 'overall_champion_count': 13,
}
# Rule G (HONORS_LADDER=g). Held OUT of POLICY so the legacy dial keeps
# publishing today's policy dict, generation ID and documents byte for byte;
# build_rank_honors composes POLICY | LADDER_G when the dial selects G.
LADDER_G = {
    'ladder': 'g',
    'persuadable_pct': 50, 'lean_persuadable_pct': 100,
    'opponent_pct': 5, 'highly_skeptical_pct': 15, 'skeptic_pct': 30, 'lean_skeptic_pct': 100,
    'zero_record_label': 'True Neutral',
    'defensive_fields': ['Interchange', 'Credit Card Mandates', 'CU Tax Status'],
    'defensive_top_band_label': 'Defender',
    'vote_weight': '0.4',
    'score_form': 'signed_per_service_year_rate_v1',
    'score_window_start': '2021-01-03',
    'years_min': '0.5', 'years_cap': '6', 'congress_decay_applied': False,
    'sign_split': 'rank_positive_and_negative_records_in_their_own_sub_cohort',
}
LEGACY_BANDS = (('champion', 'Champion'), ('ally', 'Ally'),
                ('highly_persuadable', 'Highly Persuadable'))
POSITIVE_BANDS = LEGACY_BANDS + (('persuadable', 'Persuadable'),)
NEGATIVE_BANDS = (('opponent', 'Opponent'), ('highly_skeptical', 'Highly Skeptical'),
                  ('skeptic', 'Skeptic'))
CATCH_ALL_POSITIVE = ('Lean Persuadable', 'catch_all_positive_band')
CATCH_ALL_NEGATIVE = ('Lean Skeptic', 'catch_all_negative_band')
CUTOFF_KEYS = {
    'legacy': tuple(key for key, _ in LEGACY_BANDS),
    'positive': tuple(key for key, _ in POSITIVE_BANDS) + ('lean_persuadable',),
    'negative': tuple(key for key, _ in NEGATIVE_BANDS) + ('lean_skeptic',),
}


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Expected a finite decimal literal')
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError('Nonfinite action or tensor score')
        return result.quantize(PRECISION, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('Invalid decimal literal: ' + repr(value)) from exc


def literal(value):
    value = number(value)
    return '0' if not value else format(value.normalize(), 'f')


def alignment(actions):
    signs = {a['alignment'] for a in actions}
    if not signs:
        return 'No recorded action'
    if 1 in signs and -1 in signs:
        return 'Mixed actions'
    if 1 in signs:
        return 'Supportive actions'
    if -1 in signs:
        return 'Opposing actions'
    return 'Neutral actions'


def ladder_of(policy=None):
    return (policy or POLICY).get('ladder', 'legacy')


def rank_cutoffs(size, policy=None, *, side=None):
    policy = policy or POLICY
    if type(size) is not int or size < 0:
        raise ValueError('Invalid cohort size')
    if side is None:
        side = 'positive' if ladder_of(policy) == 'g' else 'legacy'
    if side not in CUTOFF_KEYS:
        raise ValueError('Unknown honors cutoff side')
    keys = CUTOFF_KEYS[side]
    pcts = [policy[k + '_pct'] for k in keys]
    if any(type(p) is not int for p in pcts) or not (1 <= pcts[0] <= 5 < pcts[1]) or pcts[-1] > 100:
        raise ValueError('Invalid honors cutoffs')
    if any(pcts[i] >= pcts[i + 1] for i in range(len(pcts) - 1)):
        raise ValueError('Invalid honors cutoffs')
    return {k: size * policy[k + '_pct'] // 100 for k in keys}


def field_cutoffs(policy, positives, negatives, cohort):
    """One cutoff record per row: G splits by sign, legacy keeps one cohort."""
    if ladder_of(policy) != 'g':
        return rank_cutoffs(cohort, policy, side='legacy')
    positive = rank_cutoffs(positives, policy, side='positive')
    negative = rank_cutoffs(negatives, policy, side='negative')
    # Flat keys stay the positive cutoffs so published/rendered consumers that
    # read cuts.champion/ally/highly_persuadable keep reading the same thing.
    return {**positive, 'positive': positive, 'negative': negative}


def scored_weight(action, policy=None):
    """The weight actually scored. G prices votes on its own dial."""
    policy = policy or POLICY
    if ladder_of(policy) == 'g' and action['kind'] == 'vote':
        return number(policy['vote_weight'])
    return number(action['weight'])


def action_contribution(action, policy=None, years=None):
    """One action's signed contribution to its field score."""
    policy = policy or POLICY
    weight = scored_weight(action, policy)
    if ladder_of(policy) != 'g':
        return weight * number(action['decay']) * action['alignment']
    if years is None:
        raise ValueError('Rule G scoring requires a service-year denominator')
    return number(weight * action['alignment'] / number(years))


def action_tensor_term(action, tensor, policy=None, years=None):
    policy = policy or POLICY
    weight = scored_weight(action, policy)
    value = weight * action['position_on_measure'] * number(tensor[action['measure_id']])
    if ladder_of(policy) != 'g':
        return value * number(action['decay'])
    if years is None:
        raise ValueError('Rule G scoring requires a service-year denominator')
    return number(value / number(years))


def field_score(actions, policy=None, years=None):
    return sum((action_contribution(a, policy, years) for a in actions), Decimal(0))


def field_action_rows(actions, policy=None, years=None):
    """Itemized public ledger: one row per accepted action, summing to the score.

    Titles are not carried per row; the generation publishes one shared
    measure_titles dictionary instead.
    """
    rows = []
    for action in actions:
        rows.append({'m': action['measure_id'], 'c': action['congress'], 'k': action['kind'],
                     'r': ('Yea' if action['position_on_measure'] == 1 else 'Nay')
                          if action['kind'] == 'vote' else action['role'],
                     'd': action['alignment'],
                     'v': literal(action_contribution(action, policy, years))})
    rows.sort(key=lambda row: (-row['c'], row['m'], row['k'], row['r']))
    return rows


def _recognition(score, positives, start, end, cutoffs):
    if score <= 0 or not positives:
        return None, 'nonpositive_record'
    for key, label in LEGACY_BANDS:
        if end <= cutoffs[key]:
            return label, 'within_' + key + '_rank_cutoff'
    reason = 'unresolved_boundary_tie' if any(start <= c < end for c in cutoffs.values()) else 'below_rank_cutoff'
    return None, reason


def _recognition_g(score, action_count, end, cutoffs, *, defensive=False, policy=None):
    """Rule G: every record with evidence lands on the full Champion-Opponent ladder.

    No label is returned only when the member has no accepted action at all.
    """
    policy = policy or POLICY
    if not action_count:
        return None, 'no_recorded_action'
    if score == 0:
        return policy['zero_record_label'], 'zero_net_with_evidence'
    if score > 0:
        for key, label in POSITIVE_BANDS:
            if end <= cutoffs['positive'][key]:
                if key == 'champion' and defensive:
                    label = policy['defensive_top_band_label']
                return label, 'within_' + key + '_rank_cutoff'
        return CATCH_ALL_POSITIVE
    for key, label in NEGATIVE_BANDS:
        if end <= cutoffs['negative'][key]:
            return label, 'within_' + key + '_rank_cutoff'
    return CATCH_ALL_NEGATIVE


def _rank_cohort(order, raw, records, *, descending, recognize, side):
    """Shared tie/rank mechanic. G runs it once per signed sub-cohort."""
    sign = -1 if descending else 1
    ordered = sorted(order, key=lambda m: (sign * raw[m][0], m))
    next_rank = 1
    for _, primary_members in groupby(ordered, key=lambda m: raw[m][0]):
        block = list(primary_members)
        complete = all(raw[m][1] is not None for m in block)
        if len(block) > 1 and complete:
            block.sort(key=lambda m: (sign * raw[m][1], m))
            groups = [list(g) for _, g in groupby(block, key=lambda m: raw[m][1])]
        else:
            groups = [block]
            if len(block) > 1 and not complete:
                for m in block:
                    records[m]['tensor_status'] = 'tensor_unavailable_for_tie'
        for merit_group in groups:
            end = next_rank + len(merit_group) - 1
            for m in merit_group:
                row = records[m]
                row['rank_start'], row['rank_end'] = next_rank, end
                row['recognition'], row['reason'] = recognize(m, next_rank, end)
                cuts = row['cutoffs'][side] if side else row['cutoffs']
                row['boundary_tie'] = len(merit_group) > 1 and any(next_rank <= c < end for c in cuts.values())
            next_rank = end + 1


def classify_field(member_ids, actions, tensor, *, policy=None, years=None, category=None):
    """Return each roster member's record from accepted normalized actions.

    A tensor key is a canonical measure ID. Letter actions must explicitly mark
    their tensor position as scoped; otherwise coverage is incomplete.
    """
    policy = policy or POLICY
    ladder = ladder_of(policy)
    ids = list(member_ids)
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate roster identities')
    grouped = defaultdict(list)
    seen = set()
    for action in actions:
        if action['member_id'] not in ids:
            raise ValueError('Action outside frozen roster')
        if action['evidence_id'] in seen:
            raise ValueError('Duplicate normalized action')
        seen.add(action['evidence_id'])
        if type(action['alignment']) is not int or action['alignment'] not in (-1, 0, 1):
            raise ValueError('Invalid action alignment')
        w, decay = number(action['weight']), number(action['decay'])
        if w <= 0 or decay <= 0:
            raise ValueError('Action weights and decay must be positive')
        grouped[action['member_id']].append(action)
    spans = {}
    if ladder == 'g':
        if not isinstance(years, dict) or any(m not in years for m in grouped):
            raise ValueError('Rule G scoring requires service years for every acting member')
        floor_, cap = number(policy['years_min']), number(policy['years_cap'])
        for member_id in grouped:
            spans[member_id] = number(years[member_id])
            if not floor_ <= spans[member_id] <= cap:
                raise ValueError('Service years outside the accepted clamp: ' + member_id)
    records, raw = {}, {}
    for member_id in ids:
        acts = sorted(grouped[member_id], key=lambda a: a['evidence_id']) if member_id in grouped else []
        span = spans.get(member_id)
        score = field_score(acts, policy, span)
        missing = sorted({a['measure_id'] for a in acts if a['measure_id'] not in tensor
                          or a.get('position_on_measure') not in (-1, 1)
                          or not a.get('tensor_position_scoped', True)})
        tie_score = None if missing or not acts else sum(
            (action_tensor_term(a, tensor, policy, span) for a in acts), Decimal(0))
        raw[member_id] = (score, tie_score)
        records[member_id] = {
            'member_id': member_id, 'evidence_status': 'observed' if acts else 'no_recorded_action',
            'action_count': len(acts), 'positive_action_count': sum(a['alignment'] > 0 for a in acts),
            'negative_action_count': sum(a['alignment'] < 0 for a in acts),
            'raw_field_score': literal(score) if acts else None,
            'tensor_tiebreak': literal(tie_score) if tie_score is not None else None,
            'tensor_status': 'complete' if tie_score is not None else 'incomplete' if acts else 'not_applicable',
            'missing_tensor_measures': missing, 'rank_start': None, 'rank_end': None,
            'cohort_size': len(grouped), 'cutoffs': None, 'recognition': None,
            'reason': 'no_recorded_action', 'evidence_ids': [a['evidence_id'] for a in acts],
            'policy_id': policy['id'], 'alignment': alignment(acts),
        }
        if ladder == 'g':
            records[member_id]['sub_cohort'] = (None if not acts else
                'positive' if score > 0 else 'negative' if score < 0 else 'zero')
            records[member_id]['sub_cohort_size'] = 0
            records[member_id]['years_served'] = literal(span) if acts else None
            records[member_id]['field_actions'] = field_action_rows(acts, policy, span)
    if ladder != 'g':
        cutoffs = rank_cutoffs(len(grouped), policy)
        for record in records.values():
            record['cutoffs'] = dict(cutoffs)
        _rank_cohort(list(grouped), raw, records, descending=True, side=None,
                     recognize=lambda m, start, end: _recognition(
                         raw[m][0], records[m]['positive_action_count'], start, end, cutoffs))
        return records
    sides = {'positive': [], 'negative': [], 'zero': []}
    for member_id in grouped:
        sides[records[member_id]['sub_cohort']].append(member_id)
    cutoffs = field_cutoffs(policy, len(sides['positive']), len(sides['negative']), len(grouped))
    defensive = category in (policy.get('defensive_fields') or ())
    for record in records.values():
        record['cutoffs'] = {**cutoffs, 'positive': dict(cutoffs['positive']),
                             'negative': dict(cutoffs['negative'])}
        if record['sub_cohort']:
            record['sub_cohort_size'] = len(sides[record['sub_cohort']])
    for side in ('positive', 'negative'):
        _rank_cohort(sides[side], raw, records, descending=(side == 'positive'), side=side,
                     recognize=lambda m, start, end: _recognition_g(
                         raw[m][0], records[m]['action_count'], end, records[m]['cutoffs'],
                         defensive=defensive, policy=policy))
    for member_id in sides['zero']:
        row = records[member_id]
        row['recognition'], row['reason'] = _recognition_g(
            raw[member_id][0], row['action_count'], None, row['cutoffs'],
            defensive=defensive, policy=policy)
        row['boundary_tie'] = False
    return records


def overall_records(roster, actions, board):
    """Project the active board unchanged, requiring action for every crown."""
    if len(board) != len(set(board)) or any(m not in roster for m in board):
        raise ValueError('Invalid active overall board roster')
    if len(board) < 13:
        raise ValueError('Overall board has fewer than 13 members')
    by_id = defaultdict(dict)
    for a in actions:
        bucket = by_id[a['member_id']]
        # Multicategory copies of one role or vote are one overall action.
        key = a['overall_event_id']
        if key in bucket:
            prior = bucket[key]['alignment']
            if prior and a['alignment'] and prior != a['alignment']:
                raise ValueError('Conflicting accepted overall direction: ' + key)
            # Neutral in one field does not negate an accepted signed overall
            # direction in another. Opposing nonzero directions remain fatal.
            if prior:
                continue
        bucket[key] = a
    top = set(board[:13])
    rank = {m: i + 1 for i, m in enumerate(board)}
    result = {}
    for name, member in roster.items():
        acts = list(by_id[member['member_id']].values())
        if name in top and not acts:
            raise ValueError('Overall Champion lacks qualifying action: ' + name)
        result[name] = {
            'recognition': 'Champion' if name in top else None,
            'alignment': alignment(acts), 'rank': rank.get(name),
            'evidence_status': 'observed' if acts else 'no_recorded_action',
            'action_count': len(acts), 'positive_action_count': sum(a['alignment'] > 0 for a in acts),
            'negative_action_count': sum(a['alignment'] < 0 for a in acts),
            'evidence_ids': sorted(a['evidence_id'] for a in acts),
        }
    return result
