"""Reject mixed-generation recognition pages before combining or packaging."""
from pathlib import Path
from copy import deepcopy
import re

MARKER = re.compile(r'<meta\s+name="melange-honors-generation"\s+content="([^"]+)"\s*/?>')

PUBLIC_HONOR_FIELDS = frozenset((
    'evidence_status', 'action_count', 'positive_action_count', 'negative_action_count',
    'raw_field_score', 'tensor_tiebreak', 'tensor_status', 'rank_start', 'rank_end',
    'cohort_size', 'cutoffs', 'recognition', 'reason', 'alignment', 'rank',
    # Rule G publishes the itemized qualification proof itself: the per-action
    # rows that sum to raw_field_score, the signed sub-cohort they were ranked
    # in, and the service-year denominator. Absent under the legacy ladder, so
    # a legacy cell compacts to exactly the same keys it always did.
    'field_actions', 'sub_cohort', 'sub_cohort_size', 'years_served',
    'priority_tier', 'stance', 'stance_reason', 'stance_rank_start', 'stance_rank_end',
    'stance_cohort_size', 'raw_overall_score', 'mixed_record', 'record_pattern',
    'neutral_action_count', 'stance_cutoffs', 'opposition_rank_start', 'opposition_rank_end',
    'opposition_cohort_size', 'opposition_cutoffs', 'stance_boundary_tie',
    'opposition_evidence', 'classification_basis',
))


def compact_honor_cell(row):
    """Keep the public qualification proof without copying its full event ledger.

    The immutable generation retains IDs, rejected/missing measures and source
    provenance. A surface already knows the member/category and generation.
    """
    return {key: deepcopy(value) for key, value in row.items() if key in PUBLIC_HONOR_FIELDS}


def validate_pages(root, names, captured=None):
    from stance.honors_views import load_honors
    doc = load_honors(root)
    expected = doc['generation_id'] if doc is not None else None
    for name in names:
        text = captured[name] if captured is not None else (Path(root) / name).read_text(encoding='utf-8')
        if MARKER.findall(text) != ([expected] if expected else []):
            raise ValueError('Missing or mismatched recognition generation: ' + name)
    return expected


def mark_page(html, generation):
    if generation is None:
        return html
    if not re.fullmatch(r'[A-Za-z0-9_-]+', generation):
        raise ValueError('Unsafe recognition generation ID')
    if '</head>' not in html:
        raise ValueError('No head in recognition page')
    return html.replace('</head>', '<meta name="melange-honors-generation" content="' + generation + '"></head>', 1)
