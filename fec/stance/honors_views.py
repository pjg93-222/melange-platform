"""Shared fail-closed loader for immutable public honors generations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from stance.rank_honors_policy import (SCHEMA, SCHEMA_LADDER_G, POLICY_ID, _recognition_g,
                                       field_cutoffs, field_score, ladder_of, number,
                                       rank_cutoffs, classify_field)
from stance.overall_stance_policy import OVERALL_POLICY, STANCES, classify_overall

POINTER_SCHEMA = 'rank-honors-active-v1'
ALIGNMENTS = {'Supportive actions', 'Opposing actions', 'Mixed actions',
              'Neutral actions', 'No recorded action', 'Evidence unavailable'}


def validate_honors(doc, evidence=None):
    ladder = ladder_of(doc.get('policy') or {})
    if doc.get('schema') != (SCHEMA_LADDER_G if ladder == 'g' else SCHEMA) or not re.fullmatch(
            r'[a-f0-9]{24}', str(doc.get('generation_id'))):
        raise ValueError('Invalid honors schema/generation')
    if doc.get('policy', {}).get('id') != POLICY_ID:
        raise ValueError('Unknown honors policy')
    years = doc.get('years_served') or {}
    if ladder == 'g':
        defensive = set(doc['policy'].get('defensive_fields') or ())
        if not isinstance(years, dict) or set(years) != {row.get('member_id') for row in (doc.get('members') or {}).values()}:
            raise ValueError('Rule G generation lacks its published service-year denominators')
        if not isinstance(doc.get('measure_titles'), dict):
            raise ValueError('Rule G generation lacks its shared measure title dictionary')
    if doc.get('overall_policy') != OVERALL_POLICY:
        raise ValueError('Unknown or missing overall behavioral standing policy; rebuild the generation')
    cats, members = doc.get('cats'), doc.get('members')
    if not isinstance(cats, list) or len(set(cats)) != len(cats) or not cats or not isinstance(members, dict) or not members:
        raise ValueError('Missing honors roster/categories')
    ids = [row.get('member_id') for row in members.values()]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[A-Z]\d{6}', str(i)) for i in ids):
        raise ValueError('Invalid honors member identities')
    table = None
    if evidence is not None:
        if evidence.get('generation_id') != doc['generation_id'] or evidence.get('schema') != 'field-action-evidence-v1':
            raise ValueError('Evidence and honors generation mismatch')
        actions = evidence['actions']
        table = {a['evidence_id']: a for a in actions}
        if len(table) != len(actions):
            raise ValueError('Duplicate evidence IDs')
    for name, member in members.items():
        if set(member.get('fields', {})) != set(cats):
            raise ValueError('Incomplete honors member fields: ' + name)
        for cat, row in member['fields'].items():
            if row.get('member_id') != member['member_id'] or row.get('category_id') != cat:
                raise ValueError('Field identity differs from its member/category')
            if row.get('alignment') not in ALIGNMENTS or row.get('evidence_status') not in ('observed', 'no_recorded_action'):
                raise ValueError('Invalid published evidence status/alignment')
            count = row.get('action_count')
            refs = row.get('evidence_ids', [])
            if type(count) is not int or count != len(refs) or len(set(refs)) != count:
                raise ValueError('Action count differs from distinct evidence IDs')
            if any(type(row.get(k)) is not int or row[k] < 0 for k in ('positive_action_count', 'negative_action_count')):
                raise ValueError('Invalid signed action counts')
            if row['positive_action_count'] + row['negative_action_count'] > count:
                raise ValueError('Signed action counts exceed action count')
            if table is not None:
                for ref in refs:
                    if ref not in table or table[ref]['member_id'] != member['member_id'] or table[ref]['category_id'] != cat:
                        raise ValueError('Evidence reference belongs to another field/member')
                if sum(table[r]['alignment'] > 0 for r in refs) != row['positive_action_count'] or sum(table[r]['alignment'] < 0 for r in refs) != row['negative_action_count']:
                    raise ValueError('Signed counts differ from evidence')
                score = field_score([table[ref] for ref in refs], doc['policy'], years.get(member['member_id']))
                if count and score != number(row['raw_field_score']):
                    raise ValueError('Field score differs from evidence')
            if ladder == 'g':
                flat = {key: value for key, value in row['cutoffs'].items() if key not in ('positive', 'negative')}
                if flat != row['cutoffs'].get('positive'):
                    raise ValueError('Field cutoff does not match its denominator')
                if row['sub_cohort'] in ('positive', 'negative') and row['cutoffs'][row['sub_cohort']] != rank_cutoffs(
                        row['sub_cohort_size'], doc['policy'], side=row['sub_cohort']):
                    raise ValueError('Field cutoff does not match its denominator')
            elif row['cutoffs'] != rank_cutoffs(row['cohort_size'], doc['policy']):
                raise ValueError('Field cutoff does not match its denominator')
            if not count:
                if row['evidence_status'] != 'no_recorded_action' or any(row.get(k) is not None for k in ('rank_start', 'rank_end', 'recognition', 'raw_field_score')):
                    raise ValueError('A member without action has a rank or honor')
                if ladder == 'g' and (row['sub_cohort'] is not None or row['sub_cohort_size']
                                      or row['years_served'] is not None or row['field_actions']):
                    raise ValueError('A member without action has a rank or honor')
                continue
            lo, hi = row['rank_start'], row['rank_end']
            if row['evidence_status'] != 'observed':
                raise ValueError('Invalid observed rank interval')
            score = number(row['raw_field_score'])
            if ladder == 'g' and row['sub_cohort'] == 'zero':
                if score or lo is not None or hi is not None:
                    raise ValueError('A zero-net record is ranked in a signed cohort')
            else:
                bound = row['sub_cohort_size'] if ladder == 'g' else row['cohort_size']
                if type(lo) is not int or type(hi) is not int or not 1 <= lo <= hi <= bound:
                    raise ValueError('Invalid observed rank interval')
            if ladder == 'g':
                expected, reason = _recognition_g(score, count, hi, row['cutoffs'],
                                                  defensive=cat in defensive, policy=doc['policy'])
                if row['reason'] != reason:
                    raise ValueError('Recognition contradicts its action/rank qualification')
                if sum(number(action['v']) for action in row['field_actions']) != score or len(row['field_actions']) != count:
                    raise ValueError('Itemized field ledger does not total its published score')
            else:
                expected = None
                if score > 0 and row['positive_action_count'] > 0:
                    for key, label in (('champion', 'Champion'), ('ally', 'Ally'), ('highly_persuadable', 'Highly Persuadable')):
                        if hi <= row['cutoffs'][key]:
                            expected = label
                            break
            if row['recognition'] != expected:
                raise ValueError('Recognition contradicts its action/rank qualification')
        overall = member['overall']
        if overall.get('recognition') not in (None, 'Champion') or overall.get('alignment') not in ALIGNMENTS:
            raise ValueError('Invalid overall recognition')
        if overall.get('stance') not in STANCES or type(overall.get('priority_tier')) is not int or overall['priority_tier'] not in (0, 1, 2, 3):
            raise ValueError('Invalid overall behavioral stance or priority tier')
        if type(overall.get('mixed_record')) is not bool or overall.get('record_pattern') not in {
                'supportive', 'mixed', 'opposing', 'neutral', 'balanced', 'no_recorded_action'}:
            raise ValueError('Invalid overall record pattern')
        for key in ('action_count', 'positive_action_count', 'negative_action_count', 'neutral_action_count',
                    'stance_cohort_size', 'opposition_cohort_size'):
            if type(overall.get(key)) is not int or overall[key] < 0:
                raise ValueError('Invalid overall evidence/cohort count')
        if overall['action_count'] != sum(overall[key] for key in ('positive_action_count', 'negative_action_count', 'neutral_action_count')):
            raise ValueError('Overall signed/neutral counts do not sum to action count')
        if overall['action_count'] != len(overall.get('evidence_ids', [])) or len(set(overall['evidence_ids'])) != overall['action_count']:
            raise ValueError('Overall evidence references differ from action count')
        if overall['action_count']:
            score = number(overall['raw_overall_score'])
            lo, hi = overall.get('stance_rank_start'), overall.get('stance_rank_end')
            if type(lo) is not int or type(hi) is not int or not 1 <= lo <= hi <= overall['stance_cohort_size']:
                raise ValueError('Invalid overall behavioral rank interval')
            if lo != overall.get('rank') or hi != lo:
                raise ValueError('Overall label rank must equal the authoritative primacy rank')
            if overall.get('opposition_rank_start') is not None or overall.get('opposition_rank_end') is not None:
                raise ValueError('Opposition is evidence-based, not a separate percentile rank')
        elif any(overall.get(key) is not None for key in ('stance_rank_start', 'stance_rank_end',
                'raw_overall_score', 'tensor_tiebreak', 'opposition_rank_start', 'opposition_rank_end')):
            raise ValueError('A member without overall action has a behavioral rank or score')
        if overall.get('recognition') and not overall.get('action_count'):
            raise ValueError('Overall crown without action')
    for cat in cats:
        rows = [m['fields'][cat] for m in members.values()]
        actors = [r for r in rows if r['action_count']]
        if any(r['cohort_size'] != len(actors) for r in rows):
            raise ValueError('Field denominator differs from observed cohort')
        # Rule G ranks positive and negative records in their own sub-cohort, so
        # each side partitions its own 1..n and zero-net records stay unranked.
        sides = {'positive': [], 'negative': [], 'zero': []}
        for row in actors:
            score = number(row['raw_field_score'])
            sides['positive' if score > 0 else 'negative' if score < 0 else 'zero'].append(row)
        if ladder == 'g':
            expected_cutoffs = field_cutoffs(doc['policy'], len(sides['positive']), len(sides['negative']), len(actors))
            for row in rows:
                if row['cutoffs'] != expected_cutoffs:
                    raise ValueError('Field cutoff does not match its denominator')
            for side, group in sides.items():
                for row in group:
                    if row['sub_cohort'] != side or row['sub_cohort_size'] != len(group):
                        raise ValueError('Field sub-cohort differs from its signed denominator')
        for side, group in (sides.items() if ladder == 'g' else (('all', actors),)):
            if side == 'zero':
                continue
            intervals = {}
            for row in group:
                interval = (row['rank_start'], row['rank_end'])
                intervals.setdefault(interval, []).append(row)
            next_rank = 1
            for (lo, hi), tied in sorted(intervals.items()):
                if lo != next_rank or hi - lo + 1 != len(tied):
                    raise ValueError('Rank intervals do not partition field cohort')
                if len({r['raw_field_score'] for r in tied}) != 1 or len({r['recognition'] for r in tied}) != 1:
                    raise ValueError('Residual ties do not receive equal recognition')
                next_rank = hi + 1
    crowns = {n for n, m in members.items() if m['overall']['recognition'] == 'Champion'}
    board = doc.get('overall_board', {})
    if set(board.get('priority_tiers', {})) != set(members) or any(
            type(value) is not int or value not in (0, 1, 2, 3) for value in board['priority_tiers'].values()):
        raise ValueError('Overall board lacks its authoritative priority tier map')
    # 'per_area_v1' (D-031) reseats the board but keeps the same 13/24/48
    # buckets, so the invariant must keep running under it. A mode admitted here
    # that did not bucket this way would fail loudly; a mode LEFT OUT silently
    # skips the check, which is the worse failure.
    if board.get('mode') in ('factor', 'legxfactor', 'fixture', 'per_area_v1'):
        expected_tiers = dict.fromkeys(members, 0)
        for index, name in enumerate(board['order']):
            expected_tiers[name] = 1 if index < 13 else 2 if index < 37 else 3 if index < 85 else 0
        if board['priority_tiers'] != expected_tiers:
            raise ValueError('Overall priority tiers contradict the fixed active board buckets')
    if len(crowns) != 13 or crowns != set(board.get('top13', [])) or len(board.get('top13', [])) != 13:
        raise ValueError('Overall crown differs from canonical top 13')
    if {members[n]['overall']['rank'] for n in crowns} != set(range(1, 14)):
        raise ValueError('Overall crown rank mismatch')
    if table is not None:
        # Hashes bind files; a semantic recomputation also rejects a producer
        # defect that emits consistent-looking but incorrectly ordered ranks.
        for cat in cats:
            expected = classify_field(ids, [a for a in actions if a['category_id'] == cat],
                                      evidence['tensor'], policy=doc['policy'],
                                      years=years or None, category=cat)
            for member in members.values():
                row = member['fields'][cat]
                if any(row.get(k) != v for k, v in expected[member['member_id']].items()):
                    raise ValueError('Field record differs from evidence rank/tensor policy')
        expected_overall = classify_overall(members, actions, evidence['tensor'], board['order'],
                                           board['priority_tiers'], policy=doc['overall_policy'],
                                           measure_primacy=board.get('measure_primacy'))
        for name, row in expected_overall.items():
            if members[name]['overall'] != row:
                raise ValueError('Overall record differs from behavioral evidence/tensor/priority policy')
    return doc


def load_generation(directory, expected_generation=None, expected_hashes=None):
    directory = Path(directory)
    payloads = {}
    for name in ('rank_honors.json', 'field_action_evidence.json', 'transition_report.json'):
        raw = (directory / name).read_bytes()
        if expected_hashes is not None and hashlib.sha256(raw).hexdigest() != expected_hashes.get(name):
            raise ValueError('Honors generation hash mismatch: ' + name)
        payloads[name] = json.loads(raw.decode('utf-8'))
    doc = payloads['rank_honors.json']
    if expected_generation is not None and doc.get('generation_id') != expected_generation:
        raise ValueError('Active pointer and document generation differ')
    if payloads['transition_report.json'].get('generation_id') != doc['generation_id']:
        raise ValueError('Transition report generation mismatch')
    return validate_honors(doc, payloads['field_action_evidence.json'])


def load_honors(root):
    """Default is active; only explicit HONORS_MODE=legacy permits fallback."""
    mode = os.environ.get('HONORS_MODE', 'active').strip().lower()
    if mode == 'legacy':
        return None
    if mode not in ('active', POLICY_ID):
        raise ValueError('Unknown HONORS_MODE: ' + mode)
    base = Path(root) / 'data/stance/rank_honors'
    pointer = json.loads((base / 'active.json').read_text(encoding='utf-8'))
    generation = pointer.get('generation_id')
    if pointer.get('schema') != POINTER_SCHEMA or not re.fullmatch(r'[a-f0-9]{24}', str(generation)):
        raise ValueError('Invalid active honors pointer')
    doc = load_generation(base / generation, generation, pointer['files_sha256'])
    # Same single source as the producer (build_rank_honors.load_board): the
    # staleness gate must compare against tier_util's ruled default, never a
    # local one. A second copy of the fallback is what let generation
    # 4f2b24fec711a3920df0a6e7 load as current while the surfaces seated on a
    # different board. Imported lazily: honors_views is imported by producers
    # that have already put fec/ on sys.path, and the attribute is read per call
    # so an explicitly set dial is still honoured.
    import tier_util as TU
    current_mode = (os.environ.get('PRIMACY_BOARD') or TU.PRIMACY_BOARD).strip().lower()
    if doc['overall_board']['mode'] != current_mode:
        raise ValueError('Honors overall board mode is stale; rebuild the generation')
    minimum = number(os.environ.get('PRIMACY_MIN_CUPRIM') or '0.75')
    if number(doc['overall_board'].get('minimum_cu_primary_credit', '0.75')) != minimum:
        raise ValueError('Honors overall evidence-floor policy is stale')
    if not isinstance(doc.get('inputs_sha256'), dict) or not doc['inputs_sha256']:
        raise ValueError('Honors generation lacks its required source manifest')
    for path, expected in doc['inputs_sha256'].items():
        source = Path(path)
        if not source.is_absolute():
            source = Path(root) / source
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('Honors source changed; rebuild before rendering: ' + str(source))
    return doc


def field_cell(doc, name, category):
    if doc is None:
        raise ValueError('No active honors document')
    return doc['members'][name]['fields'][category]


def overall_cell(doc, name):
    if doc is None:
        raise ValueError('No active honors document')
    return doc['members'][name]['overall']
