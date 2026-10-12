# -*- coding: utf-8 -*-
"""Stance bands, declared domain curves, and bounded cross-domain rounding.

Most domains use the fixed absolute scale. A small, explicitly declared set of
saturated domains uses a sign-preserving empirical curve for its *labels*:
positive evidence stays non-negative, negative evidence stays non-positive,
ties share a midrank, and empty domains remain empty. Raw and adjusted evidence
scores are unchanged and remain available for audit.
"""

FIXED_BANDS = (
    (0.70, 'top'),
    (0.35, 'Ally'),
    (0.25, 'Highly Persuadable'),
    (0.15, 'Persuadable'),
    (0.05, 'Lean Persuadable'),
    (-0.05, 'True Neutral'),
    (-0.15, 'Lean Skeptic'),
    (-0.35, 'Skeptic'),
    (-0.70, 'Highly Skeptical'),
)
EXCEPTIONAL_CUT = 0.70
ROUNDING_STRENGTH = 0.05
ROUNDING_CAP = 0.05
LEGISLATION_SQRT_SCALE = 2.0
MEDIA_SCALE = 1.0
STANCE_LABELS = {'support', 'oppose', 'neutral', 'unrelated'}
CURVED_CATEGORIES = frozenset({
    'Member Business Lending',
    'Mortgage & Housing',
    'Charter Modernization',
    'Technology & Emerging',
})


def clip(value, lo=-1.0, hi=1.0):
    return max(lo, min(hi, float(value)))


def enforce_reader_unanimity(meta_label, reader_labels):
    """A unanimous set of valid base readers beats a contradictory meta prediction."""
    readers = [str(value or '').strip().lower() for value in reader_labels]
    if readers and all(value in STANCE_LABELS for value in readers) and len(set(readers)) == 1:
        return readers[0]
    return str(meta_label or '').strip().lower()


def classify(score, has_signal=True, defensive=False):
    """Classify one score using the shared fixed scale."""
    if not has_signal:
        return '—'
    x = clip(score)
    for cut, label in FIXED_BANDS:
        if x >= cut:
            return ('Defender' if defensive else 'Champion') if label == 'top' else label
    return 'Opponent'


def combine_evidence(components, weights):
    """Weighted mean over present sources only; ``None`` means uncollected/absent."""
    present=[k for k,v in components.items() if v is not None]
    denom=sum(float(weights[k]) for k in present)
    if not denom:
        return 0.0
    return clip(sum(float(weights[k])*float(components[k]) for k in present)/denom)


def exceptional_rounding(scores, has_signal):
    """Return adjusted scores and factors keyed like ``scores``.

    For a scored domain, the factor is five percent of the mean of the member's
    *other* exceptional scores (absolute score >= .70), capped at +/-.05.  Empty
    domains remain empty and never inherit a stance from unrelated activity.
    """
    adjusted, factors = {}, {}
    for category, raw in scores.items():
        if not has_signal.get(category, False):
            adjusted[category] = float(raw)
            factors[category] = 0.0
            continue
        exceptional = [float(v) for other, v in scores.items()
                       if other != category and has_signal.get(other, False)
                       and abs(float(v)) >= EXCEPTIONAL_CUT]
        factor = 0.0 if not exceptional else clip(
            ROUNDING_STRENGTH * sum(exceptional) / len(exceptional),
            -ROUNDING_CAP, ROUNDING_CAP)
        adjusted[category] = clip(float(raw) + factor)
        factors[category] = factor
    return adjusted, factors


def _midrank_percentiles(values):
    """Return tie-aware empirical CDF values in (0, 1], keyed by raw value."""
    ordered = sorted(float(value) for value in values)
    out = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end] == ordered[index]:
            end += 1
        out[ordered[index]] = ((index + 1) + end) / (2.0 * len(ordered))
        index = end
    return out


def curve_label_scores(scores_by_member, signal_by_member, categories=None):
    """Apply a sign-preserving, tie-aware empirical curve to label scores.

    Scores outside ``categories`` are copied unchanged. Within a curved domain,
    positive and negative observations are curved separately by magnitude.
    Therefore a positive observation can never become a skeptical/opponent
    label, and a negative observation can never become an ally/champion label.
    """
    selected = set(CURVED_CATEGORIES if categories is None else categories)
    result = {
        member: dict(scores)
        for member, scores in scores_by_member.items()
    }
    for category in selected:
        positive = []
        negative_magnitudes = []
        for member, scores in scores_by_member.items():
            if not signal_by_member.get(member, {}).get(category):
                continue
            value = float(scores.get(category, 0.0))
            if value > 0:
                positive.append(value)
            elif value < 0:
                negative_magnitudes.append(abs(value))
        positive_curve = _midrank_percentiles(positive)
        negative_curve = _midrank_percentiles(negative_magnitudes)
        for member, scores in scores_by_member.items():
            if not signal_by_member.get(member, {}).get(category):
                continue
            value = float(scores.get(category, 0.0))
            if value > 0:
                result[member][category] = positive_curve[value]
            elif value < 0:
                result[member][category] = -negative_curve[abs(value)]
            else:
                result[member][category] = 0.0
    return result


def thresholds():
    """Serializable description embedded in category_stance.json."""
    return {
        'scale': 'fixed-bands-with-declared-signed-empirical-curves-v3',
        'champion_or_defender': 0.70,
        'ally': 0.35,
        'highly_persuadable': 0.25,
        'persuadable': 0.15,
        'lean_persuadable': 0.05,
        'true_neutral': [-0.05, 0.05],
        'lean_skeptic': -0.05,
        'skeptic': -0.15,
        'highly_skeptical': -0.35,
        'opponent': -0.70,
        'exceptional_rounding': {
            'other_domain_abs_score_at_least': EXCEPTIONAL_CUT,
            'strength': ROUNDING_STRENGTH,
            'cap': ROUNDING_CAP,
            'empty_domains_inherit': False,
        },
        'evidence_scales': {
            'legislation_sqrt_units': LEGISLATION_SQRT_SCALE,
            'media_blend_units': MEDIA_SCALE,
            'normalization': 'fixed-not-domain-percentile',
            'missing_source_weight_in_denominator': False,
        },
        'domain_label_curves': {
            'categories': sorted(CURVED_CATEGORIES),
            'method': (
                'sign-preserving empirical CDF of adjusted scores; positive '
                'and negative evidence curved separately; tie-aware midranks'
            ),
            'changes_raw_scores': False,
            'changes_adjusted_scores': False,
            'empty_domains_inherit': False,
        },
    }
