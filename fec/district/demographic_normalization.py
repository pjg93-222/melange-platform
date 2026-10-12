# -*- coding: utf-8 -*-
"""Comparable House/Senate population inputs for demographic percentiles."""

from __future__ import annotations

import math


BRITANNICA_SOURCE = (
    "https://www.britannica.com/topic/"
    "United-States-House-of-Representatives-Seats-by-State-1787120"
)
CENSUS_CORROBORATION = (
    "https://www.census.gov/data/tables/2020/dec/"
    "2020-apportionment-data.html"
)
APPORTIONMENT_CENSUS = 2020

# Voting House seats used by the 118th and 119th Congresses, based on the
# 2020 Census apportionment. The requested Britannica table is the display
# source; the Census table is retained as an official cross-check.
HOUSE_SEATS_BY_STATE = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52,
    "CO": 8, "CT": 5, "DE": 1, "FL": 28, "GA": 14,
    "HI": 2, "ID": 2, "IL": 17, "IN": 9, "IA": 4,
    "KS": 4, "KY": 6, "LA": 6, "ME": 2, "MD": 8,
    "MA": 9, "MI": 13, "MN": 8, "MS": 4, "MO": 8,
    "MT": 2, "NE": 3, "NV": 4, "NH": 2, "NJ": 12,
    "NM": 3, "NY": 26, "NC": 14, "ND": 1, "OH": 15,
    "OK": 5, "OR": 6, "PA": 17, "RI": 2, "SC": 7,
    "SD": 1, "TN": 9, "TX": 38, "UT": 4, "VT": 1,
    "VA": 11, "WA": 10, "WV": 2, "WI": 8, "WY": 1,
}

if len(HOUSE_SEATS_BY_STATE) != 50:
    raise RuntimeError("House apportionment table must cover 50 states")
if sum(HOUSE_SEATS_BY_STATE.values()) != 435:
    raise RuntimeError("House apportionment table must sum to 435 seats")


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def senate_population_basis(population, chamber, state_code):
    """Return the senator's equivalent-district population and provenance."""
    raw = _number(population)
    state = str(state_code or "").strip().upper()
    if (
        str(chamber or "").strip().lower() != "senate"
        or raw is None
        or raw < 0
        or state not in HOUSE_SEATS_BY_STATE
    ):
        return None
    seats = HOUSE_SEATS_BY_STATE[state]
    adjusted = raw / seats
    return {
        "method": "state_population_divided_by_house_seats",
        "state": state,
        "raw_state_population": raw,
        "house_seats": seats,
        "normalization_population": adjusted,
        "apportionment_census": APPORTIONMENT_CENSUS,
        "source": BRITANNICA_SOURCE,
        "official_crosscheck": CENSUS_CORROBORATION,
    }


def midrank_percentiles(indexed_values):
    """Return 0..1 percentiles with identical values assigned one midrank."""
    ordered = sorted(
        (int(index), float(value))
        for index, value in indexed_values
    )
    ordered.sort(key=lambda row: (row[1], row[0]))
    count = len(ordered)
    if not count:
        return {}
    if count == 1:
        return {ordered[0][0]: 0.5}
    result = {}
    start = 0
    while start < count:
        end = start + 1
        while end < count and ordered[end][1] == ordered[start][1]:
            end += 1
        percentile = ((start + end - 1) / 2.0) / (count - 1)
        for position in range(start, end):
            result[ordered[position][0]] = percentile
        start = end
    return result
