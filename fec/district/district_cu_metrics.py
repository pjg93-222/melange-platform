# -*- coding: utf-8 -*-
"""Credit-union-specific district metrics and the declared longitudinal proxy."""

from __future__ import annotations

import math
import re
from typing import Mapping


CU_NAICS = "522130"
LONGITUDINAL_PROXY = "CNS10"


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sum_values(rows, section, key):
    total = 0.0
    found = False
    for row in rows:
        value = _number((row.get(section) or {}).get(key))
        if value is not None:
            total += value
            found = True
    return total if found else None


def _sum_four_digit(rows, key):
    total = 0.0
    found = False
    for row in rows:
        for sector in (row.get("four_digit") or {}).values():
            if not isinstance(sector, Mapping):
                continue
            value = _number(sector.get(key))
            if value is not None:
                total += value
                found = True
    return total if found else None


def _ratio_lq(local_value, local_total, national_value, national_total):
    denominators = (local_total, national_value, national_total)
    if any(value is None or value <= 0 for value in denominators):
        return None
    # A measured zero is an honest zero, not a missing value: only the
    # denominators can make the quotient undefined.
    if local_value is None or local_value < 0:
        return None
    return (local_value / local_total) / (national_value / national_total)


class CreditUnionMetrics:
    """Cache CU snapshot denominators and pack per-district/state displays."""

    def __init__(self, industry_4digit):
        self.artifact = (
            industry_4digit if isinstance(industry_4digit, Mapping) else {}
        )
        self.districts = self.artifact.get("districts") or {}
        all_rows = [
            row for row in self.districts.values()
            if isinstance(row, Mapping)
        ]
        self.national = {
            "cu_emp": _sum_values(
                all_rows, "cu_benchmark", "emp_" + CU_NAICS
            ),
            "cu_est": _sum_values(
                all_rows, "cu_benchmark", "est_" + CU_NAICS
            ),
            "all_emp": _sum_four_digit(all_rows, "emp"),
            "all_est": _sum_four_digit(all_rows, "est"),
        }
        self.cache = {}

    def _rows_for_geo(self, geo):
        exact = self.districts.get(geo)
        if isinstance(exact, Mapping):
            return [exact], "district"
        if not re.fullmatch(r"[A-Z]{2}", str(geo or "")):
            return [], None
        rows = [
            row for district, row in self.districts.items()
            if str(district).startswith(str(geo) + "-")
            and isinstance(row, Mapping)
        ]
        return rows, "state aggregate"

    def for_geo(self, geo, industry_row, sector_meta=None):
        if not geo or not isinstance(industry_row, Mapping):
            return None
        cache_key = str(geo)
        snapshot = self.cache.get(cache_key)
        if snapshot is None:
            rows, scope = self._rows_for_geo(cache_key)
            if not rows:
                return None
            local = {
                "cu_emp": _sum_values(
                    rows, "cu_benchmark", "emp_" + CU_NAICS
                ),
                "cu_est": _sum_values(
                    rows, "cu_benchmark", "est_" + CU_NAICS
                ),
                "all_emp": _sum_four_digit(rows, "emp"),
                "all_est": _sum_four_digit(rows, "est"),
            }
            employment_lq = _ratio_lq(
                local["cu_emp"], local["all_emp"],
                self.national["cu_emp"], self.national["all_emp"])
            establishment_lq = _ratio_lq(
                local["cu_est"], local["all_est"],
                self.national["cu_est"], self.national["all_est"])
            snapshot = {
                "year": self.artifact.get("cbp_year"),
                "scope": scope,
                "naics": CU_NAICS,
                "name": "Credit Unions",
                "employment": (
                    None if local["cu_emp"] is None
                    else round(local["cu_emp"], 1)
                ),
                "establishments": (
                    None if local["cu_est"] is None
                    else round(local["cu_est"], 1)
                ),
                "employment_lq": (
                    None if employment_lq is None
                    else round(employment_lq, 3)
                ),
                "establishment_lq": (
                    None if establishment_lq is None
                    else round(establishment_lq, 3)
                ),
                "allocation_method": (
                    (self.artifact.get("weights") or {}).get("method")
                ),
            }
            self.cache[cache_key] = snapshot

        shift = (
            (industry_row.get("shift_share") or {}).get(
                LONGITUDINAL_PROXY
            )
            or {}
        )
        national_growth = _number(shift.get("national_growth"))
        industry_mix = _number(shift.get("industry_mix"))
        competitive_shift = _number(shift.get("competitive_shift"))
        components = (national_growth, industry_mix, competitive_shift)
        delta = (
            sum(components)
            if all(value is not None for value in components)
            else None
        )
        proxy_meta = (sector_meta or {}).get(LONGITUDINAL_PROXY) or {}
        sources = self.artifact.get("sources") or {}
        return {
            "schema": "credit-union-demographic-metrics-v1",
            "snapshot": dict(snapshot),
            "longitudinal_proxy": {
                "sector": LONGITUDINAL_PROXY,
                "naics": proxy_meta.get("naics") or "52",
                "name": proxy_meta.get("name") or "Finance and Insurance",
                "window": list(industry_row.get("window") or []),
                "location_quotient": _number(
                    (industry_row.get("lq_national") or {}).get(
                        LONGITUDINAL_PROXY
                    )
                ),
                "growth_quotient": _number(
                    (industry_row.get("growth_quotient") or {}).get(
                        LONGITUDINAL_PROXY
                    )
                ),
                "shift_share": {
                    "delta": None if delta is None else round(delta, 1),
                    "national_growth": national_growth,
                    "industry_mix": industry_mix,
                    "competitive_shift": competitive_shift,
                },
                "reason": (
                    "The CU-specific CBP district artifact is a single-year "
                    "snapshot. NAICS 52 is the declared longitudinal proxy "
                    "for GQ and shift-share; no CU-only history is fabricated."
                ),
            },
            "source": {
                "program": "U.S. Census County Business Patterns",
                "official_url": (
                    (sources.get("us") or {}).get("url")
                ),
            },
        }

