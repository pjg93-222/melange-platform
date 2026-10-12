# -*- coding: utf-8 -*-
"""Ratified rank and press-to-label policy.

Press sentiment is active at a deliberately diminished weight.  Ten points of
the original 25-point press share remain in the blended score and the other 15
points stay with legislative action.  Media may also affect issue labels, but
its nominal evidence weight is capped at 0.10 in every category.  Keeping both
policies here prevents the workbook, rank authority, and labels from drifting.
"""

PILLAR_WEIGHTS = {
    2: 0.45,  # Legislative-action pillar
    3: 0.10,  # Press sentiment (active, deliberately diminished)
    4: 0.40,  # Influence
    5: 0.05,  # Demographic
}

# ``combine_evidence`` normalizes over the evidence sources present for a
# member/category.  These are nominal source weights, not another score that
# must sum to one.  Defensive categories retain a larger recorded-vote weight,
# while press remains diminished at the same 0.10 everywhere.
LABEL_EVIDENCE_WEIGHTS = {
    "standard": {"legislation": 0.55, "media": 0.10, "vote": 0.10},
    "defensive": {"legislation": 0.55, "media": 0.10, "vote": 0.20},
}

assert abs(sum(PILLAR_WEIGHTS.values()) - 1.0) < 1e-12
assert 0.0 < PILLAR_WEIGHTS[3] < PILLAR_WEIGHTS[2]
assert all(
    0.0 < weights["media"] < weights["legislation"]
    for weights in LABEL_EVIDENCE_WEIGHTS.values()
)


def serialized_weights():
    """Return the stable JSON/calibration representation."""
    return tuple(
        (str(key), round(float(value), 9))
        for key, value in sorted(PILLAR_WEIGHTS.items())
    )


def scoring_policy():
    """Return the stable JSON/audit representation."""
    return {
        "press_sentiment": "active_diminished",
        "press_sentiment_weight": float(PILLAR_WEIGHTS[3]),
        "legacy_press_sentiment_weight": 0.25,
        "reallocated_to": "legislative_action_pillar",
        "legislative_weight": float(PILLAR_WEIGHTS[2]),
        "label_media_weight": float(
            LABEL_EVIDENCE_WEIGHTS["standard"]["media"]
        ),
        "label_policy": "press_may_change_issue_and_overall_labels",
    }
