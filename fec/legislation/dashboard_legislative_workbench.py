# -*- coding: utf-8 -*-
"""Shared display-workbench policy for issue-adaptive legislative influence.

This module deliberately does not feed the calibrated Python allocation chain.
It gives the interactive dashboard one transparent, bounded policy for:

* reconfiguring edge sensitivity by legislative subdomain;
* raising a jurisdiction chair/ranking member's target resistance; and
* raising the same leader's source-side influence shock.

The coefficients are decision-layer priors, exposed in the generated bundle,
so they can be reviewed and ratified before any production-engine promotion.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

from typing import Iterable, Mapping, Sequence


SCHEMA = "dashboard-influence-policy-v1"

CENTRALITY_POLICY = {
    "method": "weighted Katz-Bonacich outbound reach",
    "attenuation_fraction": 0.82,
    "iterations": 80,
    "rank_method": "tie-aware midrank percentile",
    "recompute": "every legislative-area change",
}

# Canonical dashboard/member-desk category -> fragments of committees with
# plausible primary jurisdiction.  A leadership role only receives the chair
# modifiers when its committee matches the selected policy area.
AREA_COMMITTEE_HINTS = {
    "Examination & Supervision": (
        "Financial Services",
        "Banking",
    ),
    "CU Capital Requirements": (
        "Financial Services",
        "Banking",
    ),
    "Credit Union Capital Requirements": (
        "Financial Services",
        "Banking",
    ),
    "NCUA Budget & Insurance": (
        "Financial Services",
        "Banking",
        "Appropriations",
    ),
    "Interchange": (
        "Financial Services",
        "Banking",
    ),
    "Payments": (
        "Financial Services",
        "Banking",
    ),
    "Technology & Emerging": (
        "Financial Services",
        "Banking",
        "Energy & Commerce",
    ),
    "Fraud Prevention": (
        "Financial Services",
        "Banking",
        "Judiciary",
        "Small Business",
    ),
    "Data Privacy & Cyber": (
        "Financial Services",
        "Banking",
        "Energy & Commerce",
        "Judiciary",
    ),
    "Charter Modernization": (
        "Financial Services",
        "Banking",
    ),
    "Defense CU Issues": (
        "Armed Services",
        "Veterans",
    ),
    "Field of Membership": (
        "Financial Services",
        "Banking",
    ),
    "CU Tax Status": (
        "Ways & Means",
        "Finance",
        "Joint Committee on Tax",
    ),
    "CDFI Fund": (
        "Financial Services",
        "Banking",
        "Appropriations",
        "Small Business",
    ),
    "CFPB": (
        "Financial Services",
        "Banking",
    ),
    "Credit Card Mandates": (
        "Financial Services",
        "Banking",
    ),
    "TCPA": (
        "Energy & Commerce",
        "Commerce",
        "Judiciary",
    ),
    "Member Business Lending": (
        "Financial Services",
        "Banking",
        "Small Business",
    ),
    "Mortgage & Housing": (
        "Financial Services",
        "Banking",
    ),
}


# CHAIR RESISTANCE - RAISED AND WIDENED, Paul ratified 2026-08-03.
#
# WHAT WAS WRONG.  Two separate defects, both measured 2026-08-03:
#   (1) SCOPE.  ``jurisdiction_role_code`` returns "-" whenever no policy area is selected, and
#       the default coalition-builder view selects none.  ``adaptive_edge_probability`` in the
#       template likewise short-circuits on ``if(!area)``.  So in the view the user actually
#       lands on, a committee gavel was worth exactly nothing.  French Hill - the sitting House
#       Financial Services chairman and the 4th-highest-degree member of the whole graph
#       (weighted degree 47.2, 90 ties) - paid no chair premium at all and sat top-10 on every
#       inefficiency list.  The guardrail existed and did not bite.
#   (2) MAGNITUDE.  1.40 was small next to the terms it competes with.  The domain scales it
#       multiplies against (edge_scale 0.72-1.22 x ic_edge_scale 0.76-1.26) swing the edge factor
#       by ~2.8x on their own, so a single stance rung moving the domain profile swamped the
#       chair term entirely.
#
# CALIBRATION (see lobbying/coalition_pricing.py for the price model these feed).
#   The brief's literal rule - "a full-committee chair of the relevant jurisdiction must not
#   price below the median member of that jurisdiction pool, all else equal" - turns out to be
#   satisfied at ANY value above 1.0 once the cost is read on its raw scale, because a chair
#   carries the role multiplier and the median pool member does not.  At 1.75 the six
#   jurisdiction chairs price at a median 1.92 against a jurisdiction-pool median of 1.43: PASS.
#   That rule was therefore NOT the binding constraint; the class-level gap was.  Measured
#   median price ratio, each of the 45 full-committee chairs against non-chairs matched on
#   chamber and +-2 congresses of seniority:
#       CHAIR_K   1.40 -> 1.167x     1.60 -> 1.256x     1.75 -> 1.321x
#                 1.90 -> 1.389x     2.10 -> 1.479x
#   A 17% premium for holding a gavel (1.40) is not a meaningful guardrail.  1.75 gives a ~1/3
#   premium, which is a real gap without being a doubling.
#
#   MODERATION, NOT DELETION.  Paul, 2026-08-03: "its not that Emmer should be NOWHERE".
#   Chair share of top-20 slots across 1,500 simulated coalitions (45 of 533 members = 8.4%
#   uniform baseline):  1.40 -> 10.24%   1.60 -> 9.00%   1.75 -> 8.38%   1.90 -> 7.79%
#   2.10 -> 7.14%.  1.75 lands chairs almost exactly at their uniform share - moderated to
#   parity rather than deleted - and still surfaces 22 of the 45 distinct chairs.  Above ~1.9
#   chairs fall below baseline, which is overcorrection.  1.75 is the conservative choice.
ROLE_PRIORS = {
    "-": {
        "label": "Member",
        "source_shock": 1.0,
        "target_resistance": 1.0,
        "access_cost": 1.0,
    },
    "v": {
        "label": "Vice chair",
        "source_shock": 1.10,
        "target_resistance": 1.12,
        "access_cost": 1.12,
    },
    "r": {
        "label": "Ranking member",
        "source_shock": 1.20,
        "target_resistance": 1.25,
        "access_cost": 1.25,
    },
    "c": {
        "label": "Chair",
        "source_shock": 1.30,
        "target_resistance": 1.75,
        "access_cost": 1.75,
    },
}

# Set False to restore the pre-2026-08-03 behaviour in which a chair's resistance applied only
# inside a jurisdiction-matched issue area (defect (1) above).  Kept as a named revert dial.
ROLE_PRIORS_UNCONDITIONAL = True


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def role_modifiers(code: str) -> dict[str, float | str]:
    """Return a copy so callers cannot mutate the shared prior table."""
    row = ROLE_PRIORS.get(str(code or "-"), ROLE_PRIORS["-"])
    return dict(row)


def _role_code(role: object) -> str:
    text = str(role or "").strip().casefold()
    if not text:
        return "-"
    if "vice" in text and ("chair" in text or text == "vice"):
        return "v"
    if "ranking" in text:
        return "r"
    if "chair" in text:
        return "c"
    return "-"


def _matches_jurisdiction(committee: object, area: str) -> bool:
    name = str(committee or "").casefold()
    hints = AREA_COMMITTEE_HINTS.get(str(area or ""), ())
    return any(str(hint).casefold() in name for hint in hints)


def jurisdiction_role_code(
    seats: Iterable[Mapping[str, object]] | None,
    area: str,
) -> str:
    """Return the strongest leadership role with jurisdiction over ``area``."""
    strongest = "-"
    strength = {"-": 0, "v": 1, "r": 2, "c": 3}
    for seat in seats or ():
        if not isinstance(seat, Mapping):
            continue
        if not _matches_jurisdiction(seat.get("name"), area):
            continue
        code = _role_code(seat.get("role"))
        if strength[code] > strength[strongest]:
            strongest = code
    return strongest


def any_role_code(seats: Iterable[Mapping[str, object]] | None) -> str:
    """Strongest leadership role held on ANY full committee, with no jurisdiction test.

    This is the widened scope Paul ratified on 2026-08-03.  ``jurisdiction_role_code`` answers
    "does this member chair something relevant to the area the user picked", which is the right
    question for issue-adaptive DIFFUSION but the wrong one for ACCESS COST: a chair's diary is
    hard to get into whether or not the user has selected an issue area, and the default view
    selects none.  Consumers that price access read this; consumers that model issue-specific
    edge sensitivity keep reading ``jurisdiction_role_code``.
    """
    strongest = "-"
    strength = {"-": 0, "v": 1, "r": 2, "c": 3}
    for seat in seats or ():
        if not isinstance(seat, Mapping):
            continue
        code = _role_code(seat.get("role"))
        if strength[code] > strength[strongest]:
            strongest = code
    return strongest


def domain_profile(codes: Sequence[str]) -> dict[str, float | str]:
    """Derive bounded IC/LT sensitivity from the observed posture distribution.

    Missing postures increase LT resistance; an established pro coalition
    increases IC transmissibility; anti share and pro/anti imbalance increase
    the threshold burden.  These are workbench priors, not validated causal
    estimates.
    """
    values = [str(code or "-")[:1] for code in codes]
    total = max(1, len(values))
    observed = [code for code in values if code != "-"]
    n_observed = len(observed)
    coverage = n_observed / total
    denom = max(1, n_observed)
    pro_share = sum(code in "CFA" for code in observed) / denom
    anti_share = sum(code in "OSK" for code in observed) / denom
    polarization = abs(pro_share - anti_share)

    edge_scale = _bounded(
        0.82
        + 0.28 * coverage
        + 0.12 * pro_share
        - 0.10 * anti_share,
        0.72,
        1.22,
    )
    ic_edge_scale = _bounded(
        0.86
        + 0.22 * coverage
        + 0.18 * pro_share
        - 0.08 * anti_share,
        0.76,
        1.26,
    )
    lt_threshold_scale = _bounded(
        0.90
        + 0.25 * anti_share
        + 0.18 * (1.0 - coverage)
        + 0.12 * polarization,
        0.88,
        1.38,
    )
    return {
        "coverage": round(coverage, 4),
        "pro_share": round(pro_share, 4),
        "anti_share": round(anti_share, 4),
        "polarization": round(polarization, 4),
        "edge_scale": round(edge_scale, 4),
        "ic_edge_scale": round(ic_edge_scale, 4),
        "lt_threshold_scale": round(lt_threshold_scale, 4),
        "status": "bounded decision-layer prior",
    }


def policy_bundle(
    categories: Sequence[str],
    members: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build aligned domain profiles and member-by-domain role codes."""
    cats = [str(category) for category in categories]
    domains = []
    for index, category in enumerate(cats):
        codes = []
        for member in members:
            stance = str(member.get("stances") or "")
            codes.append(stance[index] if index < len(stance) else "-")
        domains.append(
            {
                "area": category,
                **domain_profile(codes),
            }
        )
    roles = [
        "".join(
            jurisdiction_role_code(member.get("committees") or (), category)
            for category in cats
        )
        for member in members
    ]
    # Widened scope (Paul ratified 2026-08-03): the role a member holds regardless of which
    # issue area is selected.  ``roles`` stays as it was so the issue-adaptive diffusion path is
    # unchanged; ``roles_any`` is the unconditional companion the access-cost path reads.
    roles_any = "".join(
        any_role_code(member.get("committees") or ()) for member in members
    )
    return {
        "schema": SCHEMA,
        "scope": "interactive dashboard only",
        "production_engine_changed": False,
        "domains": domains,
        "roles": roles,
        "roles_any": roles_any,
        "role_priors_unconditional": ROLE_PRIORS_UNCONDITIONAL,
        "role_priors": {
            code: dict(values)
            for code, values in ROLE_PRIORS.items()
        },
        "centrality": dict(CENTRALITY_POLICY),
        "jurisdiction_hints": {
            category: list(AREA_COMMITTEE_HINTS.get(category, ()))
            for category in cats
        },
    }
