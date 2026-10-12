"""Overall labels on the authoritative primacy board, capped by adverse conduct.

Signed action totals and tensor coverage remain audit diagnostics. They never
create a competing overall rank or cancel sustained opposing leadership.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from stance.rank_honors_policy import number, literal

OVERALL_POLICY = {
    'id': 'overall_primacy_standing_v2',
    'primary': 'active_primacy_board',
    'ally_pct': 15, 'highly_persuadable_pct': 20, 'persuadable_pct': 50,
    'positive_denominator': 'active_board_members',
    'opposition_denominator': 'not_percentile_based',
    'positive_order': 'active_primacy_board',
    'opposition_order': 'active_primacy_board',
    'deduplication': 'overall_event_id_with_consistent_action_metadata',
    'representative': 'lowest_signed_evidence_id_else_lowest_neutral_evidence_id',
    'boundary_rule': 'active_board_rank_with_evidence_caps',
    'tensor_rule': 'retain_authoritative_board_and_report_coverage',
    'zero_record': 'True Neutral', 'no_recorded_action': 'Insufficient evidence',
    'priority_tiers': 'active_overall_board_tier_map',
    'champion': 'separate_active_board_top13_recognition',
    'lean_persuadable_pct': 75,
    'opposition_rule': 'persistent_leadership_then_current_leadership_then_repeated_actions',
    'opponent_min_congresses': 2,
    'opponent_min_lead_measures': 2,
    'highly_skeptical_min_lead_measures': 2,
    'skeptic_min_measures': 2,
    'adverse_scope': 'explicit_negative_beneficiary_or_tensor_position',
}
STANCES = frozenset(('Champion', 'Uncommitted', 'Ally', 'Highly Persuadable', 'Persuadable', 'Lean Persuadable',
                     'True Neutral', 'Lean Skeptic', 'Skeptic', 'Highly Skeptical',
                     'Opponent', 'Insufficient evidence'))
_METADATA = ('member_id', 'measure_id', 'congress', 'kind', 'role',
             'position_on_measure', 'tensor_position_scoped')


LEADERSHIP_KINDS = ('legislative', 'letter')


def opposition_record(events, measure_primacy=None, tensor=None):
    """Explicit adverse acts survive unrelated positive activity.

    Distinct measures and Congresses establish persistence; multicategory
    copies are already deduplicated. No member names or cohort quotas enter.
    Leadership means sponsoring or original cosponsoring adverse legislation.

    Only leadership-capable acts establish a standing label. Every adverse act,
    floor votes included, still reports in `measures`/`evidence_ids` and carries
    its own sign in the field ladder; a Nay is opposition evidence, not a claim
    of sustained opposing leadership. Before D7 admitted Nay cast codes no vote
    action was ever adverse, so this restriction leaves the historical ledger's
    output unchanged and only bounds the newly signed votes.
    """
    adverse = [e for e in events if e['alignment'] < 0]
    axes = {**(measure_primacy or {}), **(tensor or {})}
    leadership = [e for e in adverse if e['kind'] in LEADERSHIP_KINDS]
    core = leadership if measure_primacy is None else [e for e in leadership
        if e['measure_id'] in axes and e['position_on_measure'] * number(axes[e['measure_id']]) < 0]
    leads = [e for e in core if e['kind'] == 'legislative'
             and e['role'] in ('sponsor', 'original-cosponsor')]
    measures = sorted({e['measure_id'] for e in adverse})
    core_measures = sorted({e['measure_id'] for e in core})
    lead_measures = sorted({e['measure_id'] for e in leads})
    terms = sorted({e['congress'] for e in leads})
    label = None
    reason = None
    if len(lead_measures) >= OVERALL_POLICY['opponent_min_lead_measures'] and len(terms) >= OVERALL_POLICY['opponent_min_congresses']:
        label, reason = 'Opponent', 'persistent_opposing_leadership'
    elif len(lead_measures) >= OVERALL_POLICY['highly_skeptical_min_lead_measures']:
        label, reason = 'Highly Skeptical', 'repeated_opposing_leadership'
    elif len(core_measures) >= OVERALL_POLICY['skeptic_min_measures'] or any(e['role'] == 'sponsor' for e in leads):
        label, reason = 'Skeptic', 'opposing_leadership_or_repeated_opposition'
    return {'label': label, 'reason': reason, 'measures': measures, 'core_measures': core_measures,
            'lead_measures': lead_measures, 'congresses': terms,
            'evidence_ids': sorted(e['evidence_id'] for e in adverse)}


def deduplicate_overall_actions(actions):
    """Count one action once across fields, refusing contradictory identity data."""
    grouped, seen = defaultdict(list), set()
    for action in actions:
        for field in ('overall_event_id', 'evidence_id', 'weight', 'decay', 'alignment', *_METADATA):
            if field not in action or action[field] is None:
                raise ValueError('Overall action lacks required metadata: ' + field)
        if not action['overall_event_id'] or not action['evidence_id']:
            raise ValueError('Overall action has no stable event/evidence identity')
        if action['evidence_id'] in seen:
            raise ValueError('Duplicate normalized evidence ID')
        seen.add(action['evidence_id'])
        if type(action['alignment']) is not int or action['alignment'] not in (-1, 0, 1):
            raise ValueError('Invalid overall action alignment')
        if type(action['congress']) is not int or action['congress'] not in (117, 118, 119):
            raise ValueError('Overall action outside the accepted Congress window')
        if str(action['measure_id']).split(':', 1)[0] != str(action['congress']):
            raise ValueError('Overall measure identity conflicts with its Congress')
        if action['kind'] not in ('legislative', 'letter', 'vote'):
            raise ValueError('Unknown overall action type')
        if action['role'] not in ('sponsor', 'original-cosponsor', 'cosponsor', 'vote'):
            raise ValueError('Unknown overall action role')
        if (action['kind'] == 'vote') != (action['role'] == 'vote'):
            raise ValueError('Overall action kind and role disagree')
        if type(action['position_on_measure']) is not int or action['position_on_measure'] not in (-1, 1):
            raise ValueError('Overall action lacks an explicit measure position')
        if type(action['tensor_position_scoped']) is not bool:
            raise ValueError('Overall action tensor scope must be explicit')
        if number(action['weight']) <= 0 or number(action['decay']) <= 0:
            raise ValueError('Overall action weight and decay must be positive')
        grouped[action['overall_event_id']].append(action)
    result = []
    for event_id, copies in sorted(grouped.items()):
        first = copies[0]
        for copy in copies[1:]:
            if any(copy[field] != first[field] for field in _METADATA) or any(
                    number(copy[field]) != number(first[field]) for field in ('weight', 'decay')):
                raise ValueError('Conflicting overall action metadata: ' + event_id)
        signs = {copy['alignment'] for copy in copies if copy['alignment']}
        if len(signs) > 1:
            raise ValueError('Conflicting accepted overall direction: ' + event_id)
        signed = [copy for copy in copies if copy['alignment']]
        result.append(min(signed or copies, key=lambda row: row['evidence_id']))
    return result


def classify_overall(roster, actions, tensor, board_order, priority_tiers, *, policy=None, measure_primacy=None):
    """Return full overall records without reading mutable files or other fields."""
    policy = policy or OVERALL_POLICY
    if policy != OVERALL_POLICY:
        raise ValueError('Unknown or modified overall behavioral standing policy')
    names = list(roster)
    ids = [roster[name]['member_id'] for name in names]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate overall roster identities')
    if len(board_order) < 13 or len(board_order) != len(set(board_order)) or any(name not in roster for name in board_order):
        raise ValueError('Invalid active overall board')
    if set(priority_tiers) != set(roster) or any(type(tier) is not int or tier not in (0, 1, 2, 3) for tier in priority_tiers.values()):
        raise ValueError('Priority tier map must exactly cover the active roster')
    by_member = defaultdict(list)
    for action in deduplicate_overall_actions(actions):
        if action['member_id'] not in ids:
            raise ValueError('Overall action lies outside the frozen roster')
        by_member[action['member_id']].append(action)
    scores, tensors, missing = {}, {}, {}
    for mid, events in by_member.items():
        scores[mid] = sum((e['alignment'] * number(e['weight']) * number(e['decay']) for e in events), Decimal(0))
        missing[mid] = sorted({e['measure_id'] for e in events if e['measure_id'] not in tensor or not e['tensor_position_scoped']})
        tensors[mid] = None if missing[mid] else sum((e['position_on_measure'] * number(e['weight'])
            * number(e['decay']) * number(tensor[e['measure_id']]) for e in events), Decimal(0))
    intervals = {roster[name]['member_id']: (i + 1, i + 1)
                 for i, name in enumerate(board_order) if roster[name]['member_id'] in by_member}
    opposing = [mid for mid in by_member if any(e['alignment'] < 0 for e in by_member[mid])]
    opposition_intervals = {}
    cutoffs = {key: len(board_order) * policy[key + '_pct'] // 100
               for key in ('ally', 'highly_persuadable', 'persuadable', 'lean_persuadable')}
    opposition_cutoffs = {'opponent': None, 'highly_skeptical': None, 'skeptic': None}
    board_ranks = {name: index + 1 for index, name in enumerate(board_order)}
    champions = set(board_order[:13])
    result = {}
    for name in names:
        mid = roster[name]['member_id']
        events = by_member.get(mid, [])
        positive = sum(e['alignment'] > 0 for e in events)
        negative = sum(e['alignment'] < 0 for e in events)
        neutral = len(events) - positive - negative
        mixed = bool(positive and negative)
        observed = bool(events)
        if name in champions and not observed:
            raise ValueError('Overall Champion lacks qualifying action: ' + name)
        score = scores.get(mid)
        lo, hi = intervals.get(mid, (None, None))
        opp_lo, opp_hi = opposition_intervals.get(mid, (None, None))
        boundary_tie = False
        opposition = opposition_record(events, measure_primacy, tensor)
        if not observed:
            stance, reason, pattern = 'Insufficient evidence', 'no_recorded_action', 'no_recorded_action'
        else:
            pattern = 'balanced' if score == 0 and mixed else 'mixed' if mixed else 'supportive' if positive else 'opposing' if negative else 'neutral'
            stance, reason = 'Uncommitted', 'below_positive_primacy_bands'
            for key, label in (('ally', 'Ally'), ('highly_persuadable', 'Highly Persuadable'),
                               ('persuadable', 'Persuadable'), ('lean_persuadable', 'Lean Persuadable')):
                if positive and hi is not None and hi <= cutoffs[key]:
                    stance, reason = label, 'within_' + key + '_rank_cutoff'
                    break
            if not positive and not negative:
                stance, reason = 'True Neutral', 'neutral_record'
            elif opposition['label']:
                stance, reason = opposition['label'], opposition['reason']
            elif negative and (score <= 0 or hi is None or hi > cutoffs['persuadable']):
                stance, reason = 'Lean Skeptic', 'opposition_with_weak_primacy'
            if name in champions:
                if opposition['label']:
                    raise ValueError('Champion conflicts with opposing leadership: ' + name)
                stance, reason = 'Champion', 'active_overall_board_top13'
        alignment = ('No recorded action' if not observed else 'Mixed actions' if mixed else
                     'Supportive actions' if positive else 'Opposing actions' if negative else 'Neutral actions')
        tensor_value = tensors.get(mid)
        result[name] = {
            'recognition': 'Champion' if name in champions else None,
            'reason': 'active_overall_board_top13' if name in champions else 'outside_overall_champion_cutoff',
            'rank': board_ranks.get(name), 'priority_tier': priority_tiers[name],
            'alignment': alignment, 'evidence_status': 'observed' if observed else 'no_recorded_action',
            'action_count': len(events), 'positive_action_count': positive, 'negative_action_count': negative,
            'neutral_action_count': neutral, 'evidence_ids': sorted(e['evidence_id'] for e in events),
            'stance': stance, 'stance_reason': reason, 'stance_rank_start': lo, 'stance_rank_end': hi,
            'stance_cohort_size': len(board_order), 'raw_overall_score': literal(score) if observed else None,
            'tensor_tiebreak': literal(tensor_value) if tensor_value is not None else None,
            'tensor_status': 'not_applicable' if not observed else 'complete' if tensor_value is not None else 'incomplete',
            'missing_tensor_measures': missing.get(mid, []),
            'mixed_record': mixed, 'record_pattern': pattern, 'stance_cutoffs': dict(cutoffs),
            'opposition_rank_start': opp_lo, 'opposition_rank_end': opp_hi,
            'opposition_cohort_size': len(opposing), 'opposition_cutoffs': dict(opposition_cutoffs),
            'stance_boundary_tie': boundary_tie,
            'opposition_evidence': opposition,
            'classification_basis': 'primacy_rank_with_explicit_opposition',
        }
    return result
