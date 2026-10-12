"""Projection of earned honors for the dashboard; never changes engine inputs."""

from copy import deepcopy
from html import escape
import os
from surfaces.honors_publication import compact_honor_cell

# The dashboard already ships one overall label per member in its `gen` column,
# letter-coded by build_dashboard_bundle's TC map. Decoding it here lets the
# public honors column carry the same label the page renders, and lets the
# verifier re-derive it from the bundle instead of trusting the producer's call.
STANDING_BY_LETTER = {'C': 'Champion', 'F': 'Defender', 'A': 'Ally', 'H': 'Highly Persuadable',
                      'P': 'Persuadable', 'G': 'Lean Persuadable', 'T': 'True Neutral',
                      'L': 'Lean Skeptic', 'N': 'Neutral', 'S': 'Skeptic',
                      'K': 'Highly Skeptical', 'O': 'Opponent', '-': 'Insufficient evidence'}


def overall_label_mode():
    mode = os.environ.get('OVERALL_LABEL', 'analytic').strip().lower()
    if mode not in ('analytic', 'legacy'):
        raise ValueError('Unknown OVERALL_LABEL: ' + mode)
    return mode


def overall_stances_from_gen(names, gen):
    """Decode the dashboard's own overall-label column into the standing vocabulary."""
    if overall_label_mode() == 'legacy':
        return None
    if len(names) != len(gen):
        raise ValueError('dashboard overall label column does not cover the roster')
    stances = {}
    for name, letter in zip(names, gen):
        if letter not in STANDING_BY_LETTER:
            raise ValueError('unknown overall label code: ' + str(letter))
        stances[name] = STANDING_BY_LETTER[letter]
    return stances


def honors_generation_marker(document):
    if document is None:
        return ''
    generation = document.get('generation_id')
    if not isinstance(generation, str) or not generation:
        raise ValueError('honors HTML marker requires a generation ID')
    return '<meta name="melange-honors-generation" content="' + escape(generation, quote=True) + '">'


def dashboard_honors_payload(document, names, categories, overall_stances=None):
    """Return separate, roster-aligned public columns, or the exact legacy shape."""
    if document is None:
        return {}
    if document.get('schema') not in ('rank-honors-v1', 'rank-honors-v2') or not document.get('generation_id'):
        raise ValueError('dashboard requires a versioned rank-honors generation')
    if (document.get('overall_policy') or {}).get('id') != 'overall_primacy_standing_v2':
        raise ValueError('dashboard requires the versioned overall behavioral standing policy')
    records = document.get('members') or {}
    if len(names) != len(set(names)) or set(names) != set(records):
        raise ValueError('dashboard roster does not match the honors generation')
    if len(categories) != len(set(categories)) or set(categories) != set(document.get('cats') or []):
        raise ValueError('dashboard categories do not match the honors generation')
    fields, overall = [], []
    for name in names:
        record = records[name]
        if not record.get('member_id'):
            raise ValueError(f'honors member has no stable ID: {name}')
        member_fields = record.get('fields') or {}
        if set(member_fields) != set(categories):
            raise ValueError(f'incomplete honors fields for {name}')
        if not isinstance(record.get('overall'), dict):
            raise ValueError(f'missing overall honors for {name}')
        priority = record['overall'].get('priority_tier')
        if type(priority) is not int or priority not in (0, 1, 2, 3) or not record['overall'].get('stance'):
            raise ValueError(f'missing separate priority tier or behavioral standing for {name}')
        fields.append([compact_honor_cell(member_fields[category]) for category in categories])
        cell = compact_honor_cell(record['overall'])
        if overall_stances is not None:
            # Same single overall label the member desk renders (D1.c).
            cell['stance'] = overall_stances[name]
            cell['stance_reason'] = 'analytic_overall_band'
        overall.append(cell)
    return {
        'honors': fields,
        'overall_honors': overall,
        'honors_meta': {
            key: deepcopy(document.get(key))
            for key in ('schema', 'generation_id', 'policy', 'as_of', 'evidence_window',
                        'overall_board_mode', 'overall_board', 'overall_policy')
        },
    }


def verify_dashboard_honors(data, document, overall_stances=None):
    """Reject stale or mixed public columns before HTML publication."""
    if document is None:
        if any(key in data for key in ('honors', 'overall_honors', 'honors_meta')):
            raise ValueError('legacy dashboard rendering requires a legacy bundle')
        return
    categories = (data.get('engine') or {}).get('categories') or []
    if not categories or categories[-1] is not None:
        raise ValueError('dashboard needs canonical fields followed by its overall layer')
    if overall_stances is None:
        overall_stances = overall_stances_from_gen(data.get('name') or [], data.get('gen') or '')
    expected = dashboard_honors_payload(document, data.get('name') or [], categories[:-1], overall_stances)
    for key, value in expected.items():
        if data.get(key) != value:
            raise ValueError(f'dashboard {key} is missing, stale, or inconsistent; rebuild its bundle')
