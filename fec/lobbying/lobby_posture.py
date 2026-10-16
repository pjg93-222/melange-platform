# -*- coding: utf-8 -*-
"""Posture: is an industry cultivating, maintaining, or disengaging a member?

WHAT THIS IS.  A reading of the six-year attention trajectory between one
organization (or one sector) and one member.  Nothing here observes intent.  It
observes how many distinct bills the organization lobbied that the member
sponsored or cosponsored, year by year, and reports whether that count is going
up, holding, or going down.

WHY THE WORDING IS A TOGGLE.  "Cultivating" is a claim about what a lobbying
shop is TRYING to do; "Rising" is a claim about a number.  This module computes
one thing and names it twice, so the page can offer both vocabularies over an
identical computation and a reader can see they are the same measurement.

WHY 2026 IS NOT IN THE TREND.  It is a partial year through Q2.  Comparing a
half year against full years manufactures a decline for every member on the
board.  So the trend runs on complete years only, and the partial year enters
in exactly one place: it can rescue a pair from ``dormant`` into ``returning``,
because an organization filing on you right now is not disengaging from you.

The rule is computed HERE, in Python, at build time.  The page ships the labels,
not the arithmetic -- there is no JavaScript copy of this to drift out of sync.
"""

import os


# Complete-year windows.  Recent versus prior, two years each, so a single noisy
# quarter cannot flip a badge on its own.
RECENT_YEARS = (2024, 2025)
PRIOR_YEARS = (2022, 2023)
PARTIAL_YEAR = 2026
CONTEXT_YEAR = 2021

# Dials.  BAND is the dead zone around parity that counts as holding steady:
# recent must beat prior by more than 25% to read as rising.  MIN is the
# evidence floor -- below it the honest badge is "too little to say", not a
# direction inferred from one or two filings.
DEFAULT_BAND = 0.25
DEFAULT_MIN_EVIDENCE = 3
MAX_DISPLAY_DELTA_PERCENT = 999

POSTURE_CULTIVATING = "cultivating"
POSTURE_MAINTAINING = "maintaining"
POSTURE_DISENGAGING = "disengaging"
POSTURE_UNKNOWN = "unknown"
ADVERSARIAL = "adversarial"
ADVERSARIAL_CORROBORATION_FLOOR = 2

ADVERSARIAL_NOTE = (
    "Adversarial: the organization's signed lobbying around this member's "
    "bills is opposition-dominated (oppose > support, corroborated signs); "
    "trajectory badges are suppressed - a deepening opposition relationship "
    "must never read as Cultivating."
)

# state -> (default posture bucket, posture wording, neutral wording, meaning).
# ``new_member`` overrides the default to UNKNOWN when its recent window is quiet.
STATES = {
    "new": (
        POSTURE_CULTIVATING,
        "Cultivating",
        "New",
        "No attention in 2022–2023, attention since.",
    ),
    "new_member": (
        POSTURE_CULTIVATING,
        "New member",
        "New member",
        "The trend needs a full 2022–2023 prior window of congressional service.",
    ),
    "returning": (
        POSTURE_CULTIVATING,
        "Cultivating",
        "Returning",
        "Quiet through 2024–2025, filing again in the 2026 partial year.",
    ),
    "rising": (
        POSTURE_CULTIVATING,
        "Cultivating",
        "Rising",
        "2024–2025 attention is more than 25% above 2022–2023.",
    ),
    "steady": (
        POSTURE_MAINTAINING,
        "Maintaining",
        "Steady",
        "2024–2025 attention is within 25% of 2022–2023.",
    ),
    "falling": (
        POSTURE_DISENGAGING,
        "Disengaging",
        "Falling",
        "2024–2025 attention is more than 25% below 2022–2023.",
    ),
    "dormant": (
        POSTURE_DISENGAGING,
        "Disengaging",
        "Dormant",
        "Attention before 2024, none in 2024–2025 and none so far in 2026.",
    ),
    ADVERSARIAL: (
        POSTURE_DISENGAGING,
        "Adversarial",
        "Adversarial",
        ADVERSARIAL_NOTE,
    ),
    "insufficient": (
        POSTURE_UNKNOWN,
        "Too little to say",
        "Too little to say",
        "Fewer bills across all six years than the evidence floor.",
    ),
}

POSTURE_RULE_NOTE = (
    "Posture compares complete-year windows: distinct bills in 2024–2025 against "
    "2022–2023. More than 25% above reads as cultivating, more than 25% below as "
    "disengaging, and anything between as maintaining. A pair with fewer than 3 "
    "bills across all six years is badged “too little to say” rather than given a "
    "direction. The 2026 partial year is excluded from the comparison — comparing "
    "half a year against full years would manufacture a decline everywhere — and "
    "enters only to move a quiet pair from dormant to returning. Members whose "
    "2022–2023 prior window began before their congressional service are badged "
    "“New member” instead of given a percentage. Displayed changes whose magnitude "
    "exceeds 999% are bounded at >+999% or >-999%. Where signed evidence exists, "
    "an opposition-dominated organization–member dyad overrides the trajectory as "
    "Adversarial only when oppose > support and oppose_n >= 2; otherwise the "
    "trajectory result is unchanged."
)

POSTURE_HONESTY_NOTE = (
    "Posture is computed from filing counts, not from any disclosed intent. LDA "
    "filings record that an organization lobbied a bill, never why, and never "
    "whether it spoke to the member at all. “Cultivating” is shorthand for a "
    "rising bill count and nothing more; the neutral wording toggle shows the "
    "same measurement named after the number instead of the motive. The "
    "Adversarial override uses coverage-limited inferred signs around the member's "
    "bills, not a disclosed relationship or proof of contact."
)


def coverage_note(receipt):
    """Render the receipt's signed-dyad coverage as an on-page honesty note."""
    override = receipt.get("sign_override") or {}
    dyads = int(override.get("dyads", 0) or 0)
    signed = int(override.get("signed_dyads", 0) or 0)
    rate = (signed / dyads) if dyads else 0.0
    return (
        "The signed-evidence override was available for "
        f"{signed:,} of {dyads:,} organization–member dyads ({rate:.1%} dyad "
        "coverage). It only fires where signed evidence exists, requires "
        "oppose > support with oppose_n >= 2, and leaves dyads with no signed "
        "rows on their existing trajectory badge."
    )


def band():
    """Dead-zone half-width; LOBBY_POSTURE_BAND overrides."""
    raw = os.environ.get("LOBBY_POSTURE_BAND")
    if raw is None or not raw.strip():
        return DEFAULT_BAND
    value = float(raw)
    if value < 0:
        raise ValueError("LOBBY_POSTURE_BAND must be >= 0")
    return value


def min_evidence():
    """Evidence floor in distinct bills; LOBBY_POSTURE_MIN overrides."""
    raw = os.environ.get("LOBBY_POSTURE_MIN")
    if raw is None or not raw.strip():
        return DEFAULT_MIN_EVIDENCE
    value = int(raw)
    if value < 0:
        raise ValueError("LOBBY_POSTURE_MIN must be >= 0")
    return value


def _window(series, years, first_service_year=None):
    return sum(
        int(series.get(year, 0) or 0)
        for year in years
        if first_service_year is None or year >= first_service_year
    )


def _display_delta(change):
    limit = MAX_DISPLAY_DELTA_PERCENT / 100.0
    if abs(change) > limit:
        bounded = limit if change > 0 else -limit
        return f">{bounded:+.0%}"
    return f"{change:+.0%}"


def classify(
    series,
    band_value=None,
    minimum=None,
    first_service_year=None,
    signs=None,
):
    """Classify one attention series into a posture state.

    ``series`` maps year -> distinct-bill count. Missing years count as zero.
    When supplied, ``first_service_year`` excludes structurally unavailable
    pre-service years and prevents an incomplete prior window from becoming a
    percentage. Optional ``signs`` is ``(support_n, oppose_n)``; it changes the
    state only when opposition is dominant and clears the corroboration floor.
    Returns a dict carrying the state, both vocabularies, the windows the call
    rested on, and a human-readable derivation.
    """
    band_value = band() if band_value is None else band_value
    minimum = min_evidence() if minimum is None else minimum

    if first_service_year is not None:
        first_service_year = int(first_service_year)

    recent = _window(series, RECENT_YEARS, first_service_year)
    prior = _window(series, PRIOR_YEARS, first_service_year)
    partial = (
        int(series.get(PARTIAL_YEAR, 0) or 0)
        if first_service_year is None or PARTIAL_YEAR >= first_service_year
        else 0
    )
    context = (
        int(series.get(CONTEXT_YEAR, 0) or 0)
        if first_service_year is None or CONTEXT_YEAR >= first_service_year
        else 0
    )
    total = recent + prior + partial + context
    tenure_limited = (
        first_service_year is not None
        and any(year < first_service_year for year in PRIOR_YEARS)
    )

    if tenure_limited:
        state = "new_member"
    elif total < minimum:
        state = "insufficient"
    elif recent == 0 and prior == 0:
        # All of it sits in 2021 or in the partial year.
        state = "returning" if partial > 0 else "dormant"
    elif prior == 0:
        state = "new"
    elif recent == 0:
        state = "returning" if partial > 0 else "dormant"
    elif recent >= prior * (1.0 + band_value):
        state = "rising"
    elif recent <= prior * (1.0 - band_value):
        state = "falling"
    else:
        state = "steady"

    bucket, posture_label, trend_label, meaning = STATES[state]
    if state == "new_member":
        # This is one semantic state, but the broad bucket distinguishes current
        # attention from a tenure-limited series that is also quiet.
        bucket = POSTURE_CULTIVATING if recent > 0 else POSTURE_UNKNOWN
        delta = "—"
    elif state == "returning":
        # The complete-year change for a returning pair is always -100%, which
        # reads as the opposite of what happened. The partial year is the fact
        # that moved it, so the partial year is the number shown.
        delta = f"{partial} in 2026"
    elif state == "insufficient":
        delta = "—"
    elif prior:
        change = (recent - prior) / prior
        delta = _display_delta(change)
    elif recent:
        delta = "new"
    else:
        delta = "—"

    if signs is not None:
        support_n, oppose_n = signs
        if (
            oppose_n > support_n
            and oppose_n >= ADVERSARIAL_CORROBORATION_FLOOR
        ):
            state = ADVERSARIAL
            bucket, posture_label, trend_label, meaning = STATES[state]
    return {
        "state": state,
        "posture": bucket,
        "posture_label": posture_label,
        "trend_label": trend_label,
        "meaning": meaning,
        "recent": recent,
        "prior": prior,
        "partial": partial,
        "total": total,
        "delta": delta,
        "detail": (
            f"2024–2025: {recent} bill{'' if recent == 1 else 's'} vs "
            f"2022–2023: {prior} bill{'' if prior == 1 else 's'} ({delta}); "
            f"2026 partial year: {partial}. {meaning}"
        ),
    }


def classify_values(
    values,
    first_year=CONTEXT_YEAR,
    band_value=None,
    minimum=None,
    first_service_year=None,
    signs=None,
):
    """Classify a positional series, e.g. the page's 2021..2026 value list."""
    series = {first_year + index: value for index, value in enumerate(values)}
    return classify(
        series,
        band_value=band_value,
        minimum=minimum,
        first_service_year=first_service_year,
        signs=signs,
    )


def summarize(rows):
    """Count postures across many classifications, for a receipt."""
    counts = {}
    posture_counts = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
        bucket = row["posture"]
        posture_counts[bucket] = posture_counts.get(bucket, 0) + 1
    return summarize_counts(counts, posture_counts=posture_counts)


def summarize_counts(counts, posture_counts=None):
    """Same receipt, from a state -> occurrences mapping already in hand."""
    if posture_counts is None:
        posture_counts = {
            bucket: sum(
                count
                for state, count in counts.items()
                if STATES[state][0] == bucket
            )
            for bucket in (
                POSTURE_CULTIVATING,
                POSTURE_MAINTAINING,
                POSTURE_DISENGAGING,
                POSTURE_UNKNOWN,
            )
        }
    return {
        "by_state": dict(sorted(counts.items())),
        "by_posture": {
            bucket: posture_counts.get(bucket, 0)
            for bucket in (
                POSTURE_CULTIVATING,
                POSTURE_MAINTAINING,
                POSTURE_DISENGAGING,
                POSTURE_UNKNOWN,
            )
        },
        "band": band(),
        "min_evidence": min_evidence(),
    }
