# -*- coding: utf-8 -*-
"""Deterministic per-member DEMOGRAPHIC evidence packet.

Pure read -> join -> band -> write. No API calls. No writes into the live tree.

Reads READ-ONLY from the live tree:
  data/district/district_community.json          (ACS 2024 1-year)
  data/lobbying/district_industry.json           (LODES 20-sector)
  data/lobbying/district_industry_4digit.json    (CBP 4-digit + cu_benchmark)
  data/district/district_financial.json          (FDIC SOD 2025 + NCUA 2026-Q1)
  data/district/district_employers.json          (USASpending FY2025 + CBP size classes)
  data/district/district_hmda.json               (HMDA 2024)
  data/lobbying/district_industry_validation.json (QCEW external-validation receipt)
  member_desk_bundle.json                        (roster + name->geo resolution ONLY)

It does NOT read data/members/* (another session is writing there) and it does NOT
read m.demo (no schema, no vintage, no source, no MOE -- scope report 2.3).

Every band is decided in Python. The model never judges a threshold.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import math
import os
import sys

LIVE = r"C:\Users\pgoff\OneDrive\Desktop\Melange Systems"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

# The four conventional location-quotient bands, verbatim cutpoints from
# fec/surfaces/templates/make_member_desk_00.tpl:483. DISPLAY bands: the prose and
# the tile the reader sees underneath it must never disagree.
LQ_CUTS = ((1.00, "Not Specialized"), (1.25, "Slightly Specialized"),
           (2.00, "Specialized"), (None, "Highly Specialized"))

CU_NAICS = "522130"
BANK_NAICS = "522110"
PROXY_SECTOR = "CNS10"          # NAICS 52, the declared longitudinal proxy
CURRENT_VINTAGE_YEAR = 2023     # anything older is a stale industry window

# Pilot defect 4 (Moran, "employs more thickly than its office count alone would
# show" on a 1.09-vs-1.05 gap): a gap this small is noise in an apportioned
# estimate, not a finding. 0.10 location-quotient points is the cut: it separates
# every measured "nothing here" case (Moran/Marshall 0.04, Crawford 0.00) from every
# measured real divergence (Harris 0.16, Pfluger 0.14, Owens 0.23, Magaziner 0.38,
# Scalise 0.75, Begich 1.45) in the pilot's own nine members, with clean air on both
# sides of the line. Below it the model gets an explicit equality flag instead of
# two raw numbers to compare and cannot manufacture a contrast from noise.
JOBS_OFFICES_GAP_THRESHOLD = 0.10


def P(*a):
    return os.path.join(LIVE, *a)


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def r(value, places=1):
    value = finite(value)
    return None if value is None else round(value, places)


# --------------------------------------------------------------------- banding
def desk_rank(values):
    """The exact percentile convention used by make_member_desk.py."""
    pool = sorted(v for v in (finite(x) for x in values) if v is not None)
    denominator = max(1, len(pool) - 1)

    def rank(value):
        value = finite(value)
        if value is None or not pool:
            return None
        return round(bisect.bisect_left(pool, value) / denominator, 3)

    rank.n = len(pool)
    return rank


def band(percentile, pool_label):
    """Percentile -> {label, neutral, pct, pool}. Cutpoints copied verbatim from
    build_ledger.py:63-81 / build_member_narrative_ledger.py:80-105; only the
    'in the chamber' suffix is dropped, because this lane's pool is geographies."""
    percentile = finite(percentile)
    if percentile is None:
        return None
    if percentile >= 0.90:
        label, neutral = "among the highest", False
    elif percentile >= 0.78:
        label, neutral = "well above most", False
    elif percentile >= 0.58:
        label, neutral = "above most", False
    elif percentile >= 0.42:
        label, neutral = "middling", True
    elif percentile >= 0.22:
        label, neutral = "below most", False
    elif percentile >= 0.10:
        label, neutral = "well below most", False
    else:
        label, neutral = "among the lowest", False
    return {"pct": round(percentile, 3), "label": label, "neutral": neutral,
            "compared_with": pool_label}


def lq_band(value):
    """make_member_desk_00.tpl:483 cutpoints, unchanged."""
    value = finite(value)
    if value is None:
        return None
    for cut, label in LQ_CUTS:
        if cut is None or value < cut:
            return label
    return LQ_CUTS[-1][1]


# ------------------------------------------------------- credit-union metrics
# Replicated from fec/district/district_cu_metrics.py rather than imported, so no
# `fec` module is executed. build_report() cross-checks every value against the
# already-built desk bundle; a single mismatch is a hard failure.
def _sum_values(rows, section, key):
    total, found = 0.0, False
    for row in rows:
        value = finite((row.get(section) or {}).get(key))
        if value is not None:
            total += value
            found = True
    return total if found else None


def _sum_four_digit(rows, key):
    total, found = 0.0, False
    for row in rows:
        for sector in (row.get("four_digit") or {}).values():
            if not isinstance(sector, dict):
                continue
            value = finite(sector.get(key))
            if value is not None:
                total += value
                found = True
    return total if found else None


def _ratio_lq(local_value, local_total, national_value, national_total):
    denominators = (local_total, national_value, national_total)
    if any(v is None or v <= 0 for v in denominators):
        return None
    # A measured zero is an honest zero, not a missing value: only the
    # denominators can make the quotient undefined. Kept in sync with
    # fec/district/district_cu_metrics.py's _ratio_lq -- this replica feeds
    # the hard cross-check in build_report() below (~:721) and a mismatch
    # from a measured-zero numerator would sys.exit() the ledger build.
    if local_value is None or local_value < 0:
        return None
    return (local_value / local_total) / (national_value / national_total)


class CreditUnionMetrics:
    def __init__(self, artifact):
        self.artifact = artifact or {}
        self.districts = self.artifact.get("districts") or {}
        rows = [row for row in self.districts.values() if isinstance(row, dict)]
        self.national = {
            "cu_emp": _sum_values(rows, "cu_benchmark", "emp_" + CU_NAICS),
            "cu_est": _sum_values(rows, "cu_benchmark", "est_" + CU_NAICS),
            "bank_emp": _sum_values(rows, "cu_benchmark", "emp_" + BANK_NAICS),
            "bank_est": _sum_values(rows, "cu_benchmark", "est_" + BANK_NAICS),
            "all_emp": _sum_four_digit(rows, "emp"),
            "all_est": _sum_four_digit(rows, "est"),
        }
        self.cache = {}

    def _rows_for_geo(self, geo):
        exact = self.districts.get(geo)
        if isinstance(exact, dict):
            return [exact], "district"
        if len(str(geo or "")) != 2 or not str(geo).isalpha():
            return [], None
        rows = [row for key, row in self.districts.items()
                if str(key).startswith(str(geo) + "-") and isinstance(row, dict)]
        return rows, "state aggregate"

    def snapshot(self, geo):
        if geo in self.cache:
            return self.cache[geo]
        rows, scope = self._rows_for_geo(geo)
        if not rows:
            self.cache[geo] = None
            return None
        local = {
            "cu_emp": _sum_values(rows, "cu_benchmark", "emp_" + CU_NAICS),
            "cu_est": _sum_values(rows, "cu_benchmark", "est_" + CU_NAICS),
            "bank_emp": _sum_values(rows, "cu_benchmark", "emp_" + BANK_NAICS),
            "bank_est": _sum_values(rows, "cu_benchmark", "est_" + BANK_NAICS),
            "all_emp": _sum_four_digit(rows, "emp"),
            "all_est": _sum_four_digit(rows, "est"),
        }
        snap = {
            "scope": scope,
            "year": self.artifact.get("cbp_year"),
            "employment": r(local["cu_emp"], 1),
            "establishments": r(local["cu_est"], 1),
            "bank_employment": r(local["bank_emp"], 1),
            "bank_establishments": r(local["bank_est"], 1),
            "employment_lq": r(_ratio_lq(local["cu_emp"], local["all_emp"],
                                         self.national["cu_emp"], self.national["all_emp"]), 3),
            "establishment_lq": r(_ratio_lq(local["cu_est"], local["all_est"],
                                            self.national["cu_est"], self.national["all_est"]), 3),
            "allocation_method": (self.artifact.get("weights") or {}).get("method"),
        }
        self.cache[geo] = snap
        return snap


# ------------------------------------------------------------------- roster
def build_roster(bundle):
    rows = []
    for m in bundle.get("members") or []:
        com = m.get("community") or {}
        ind = m.get("industry") or {}
        rows.append({
            "name": m.get("name"),
            "chamber": m.get("chamber"),
            "party": m.get("party"),
            "state_name": m.get("state"),
            "district_text": m.get("district"),
            "geo": com.get("geo") or ind.get("geo"),
            "geo_kind": com.get("kind") or ind.get("kind"),
            "industry_geo": ind.get("geo"),
            "bundle_cu_metrics": (ind.get("cu_metrics") or {}).get("snapshot") or {},
        })
    return rows


def state_abbr(geo):
    return (geo or "")[:2] if geo else None


def geo_label(kind, state_name, geo):
    """Plain label. Deliberately avoids the words the validator bans in prose --
    'Congress', a chamber name, a party -- so the packet cannot seed them."""
    if kind == "state":
        return state_name
    parts = str(geo or "").split("-")
    cd = parts[1] if len(parts) > 1 else ""
    if cd == "00":
        return "the at-large congressional seat covering all of %s" % state_name
    try:
        n = int(cd)
    except ValueError:
        return state_name
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "the %d%s congressional seat of %s" % (n, suffix, state_name)


# ------------------------------------------------------------------ assembly
def sector_rows(industry_row, sector_meta):
    """20 sectors with share, LQ, GQ. Small sectors flagged, never silently kept:
    district_industry.small_sector_min = 500 jobs. The desk's top-specialization
    tile excludes them (make_member_desk_00.tpl:1392-1395); so do we."""
    if not industry_row:
        return []
    flags = industry_row.get("flags") or {}
    small = set(flags.get("small_sectors") or []) | set(flags.get("small_base_sectors") or [])
    zero = set(flags.get("zero_base_sectors") or [])
    makeup = industry_row.get("makeup_pct") or {}
    lq = industry_row.get("lq_national") or {}
    gq = industry_row.get("growth_quotient") or {}
    jobs = (industry_row.get("employment") or {}).get("by_sector") or {}
    out = []
    for code, meta in sorted(sector_meta.items()):
        share = finite(makeup.get(code))
        quotient = finite(lq.get(code))
        if share is None and quotient is None:
            continue
        out.append({
            "code": code,
            "name": meta.get("name"),
            "share_pct": r((share or 0) * 100.0, 1) if share is not None else None,
            "lq": r(quotient, 2),
            "gq": r(finite(gq.get(code)), 2),
            "jobs": finite(jobs.get(code)),
            "small": code in small or code in zero,
        })
    return out


def build_member(row, art, pools, cu, qcew_fail, sibling_map):
    geo = row["geo"]
    kind = row["geo_kind"]
    noun = "state" if kind == "state" else "district"
    st = state_abbr(geo)

    com_src = art["community"]["districts"] if kind == "district" else art["community"]["states"]
    com = com_src.get(geo) or {}
    est = com.get("estimates") or {}
    der = com.get("derived") or {}
    moe = com.get("moe_90") or {}

    ind_row = (art["industry"].get("geos") or {}).get(row["industry_geo"] or geo) or None
    # state-scope entries are the synthetic aggregates patched in by main(); the
    # source artifact itself is districts-only.
    fin = (art["financial"].get("districts") or {}).get(geo)
    emp = (art["employers"].get("districts") or {}).get(geo) if kind == "district" else None
    hmda = (art["hmda"].get("districts") or {}).get(geo) if kind == "district" else None
    snap = cu.snapshot(geo) if geo else None

    pool_key = "district" if kind == "district" else "state"
    pool_label = "other congressional districts" if kind == "district" else "other states"

    def pband(metric, value):
        rk = pools[pool_key].get(metric)
        return band(rk(value), pool_label) if rk else None

    # ---------------------------------------------------------- constraints
    latest_year = (ind_row or {}).get("latest_year")
    window = list((ind_row or {}).get("window") or [])
    stale = latest_year is not None and int(latest_year) < CURRENT_VINTAGE_YEAR
    fail = qcew_fail.get(st)

    constraints = {
        "geography_is": noun,
        "employment_levels_unreliable": bool(fail),
        "industry_vintage_year": latest_year,
        "industry_window": window or None,
        "industry_vintage_is_current": (not stale) if latest_year is not None else None,
        "credit_union_offices_are_apportioned": bool(fin),
        "credit_union_deposits_are_split_evenly": bool(fin),
        "bank_deposits_follow_booking_office": bool(fin),
        "no_formal_learning_data_exists": True,
        "nothing_measures_who_belongs_to_a_credit_union": True,
        "no_credit_union_history_exists": True,
        "no_trustworthy_place_rating_exists": True,
        "large_workplace_counts_incomplete": (
            None if not emp else not bool(((emp.get("large") or {}).get("complete")))),
        "legacy_block_not_used": True,
    }
    if fail:
        constraints["employment_shortfall"] = {
            "state": st,
            "shortfall_pct": r(fail["gap"] * 100.0, 1),
            "note": ("The workplace employment base for this state runs below the "
                     "independent state employment count by this much. The mix of work "
                     "and the comparisons against the national norm stay usable; "
                     "absolute job counts do not."),
        }
    if stale:
        constraints["vintage_note"] = (
            "The industry picture for this %s is from %s, not the current year. "
            "Anything said about its industry mix describes %s."
            % (noun, latest_year, latest_year))

    # ------------------------------------------------------------ community
    community = None
    if com:
        rent_income_pct = finite(der.get("annual_rent_income_ratio"))
        rent_income_pct = None if rent_income_pct is None else rent_income_pct * 100.0
        community = {
            "source": "American Community Survey, 2024 one-year estimates",
            "population": est.get("population"),
            "median_age_years": r(est.get("median_age"), 1),
            "median_household_income_usd": est.get("median_household_income"),
            "median_household_income_vs_peers": pband("income", est.get("median_household_income")),
            "median_home_value_usd": est.get("median_home_value"),
            "median_home_value_vs_peers": pband("home_value", est.get("median_home_value")),
            "median_monthly_rent_usd": est.get("median_gross_rent"),
            "home_value_to_income_ratio": r(der.get("home_value_income_ratio"), 1),
            "rent_as_share_of_median_income_pct": r(rent_income_pct, 1),
            "housing_cost_vs_peers": pband("rent_income", der.get("annual_rent_income_ratio")),
            "homeowner_share_pct": r(der.get("owner_occupied_share"), 1),
            "homeownership_vs_peers": pband("owner_share", der.get("owner_occupied_share")),
            "poverty_rate_pct": r(der.get("poverty_rate"), 1),
            "poverty_vs_peers": pband("poverty", der.get("poverty_rate")),
            "unemployment_rate_pct": r(der.get("unemployment_rate"), 1),
            "unemployment_vs_peers": pband("unemployment", der.get("unemployment_rate")),
            "people_per_square_mile": r(der.get("population_density_sq_mile"), 1),
            "density_vs_peers": pband("density", der.get("population_density_sq_mile")),
            "census_region": com.get("region"),
            "margin_of_error_90pct": {
                "median_household_income_usd": moe.get("median_household_income"),
                "median_home_value_usd": moe.get("median_home_value"),
            },
            "caveat": ("These housing ratios compare one published middle value against "
                       "another. They do not measure what any individual pays."),
        }

    # -------------------------------------------------------- credit unions
    credit_unions = None
    if fin or snap:
        ratio = ((fin or {}).get("ratio") or {})
        cus = ((fin or {}).get("cus") or {})
        banks = ((fin or {}).get("banks") or {})
        share = finite(ratio.get("cu_branch_share"))
        share_pct = None if share is None else share * 100.0
        cu_lq = (snap or {}).get("employment_lq")
        est_lq = (snap or {}).get("establishment_lq")
        proxy_lq = finite(((ind_row or {}).get("lq_national") or {}).get(PROXY_SECTOR))
        proxy_gq = finite(((ind_row or {}).get("growth_quotient") or {}).get(PROXY_SECTOR))

        # Pilot defect 1 (Owens, "a single named bank competitor, Synchrony Bank"):
        # this field has always been a deposit-ranking, not a competitor count -- UT-04
        # alone carries 89 separate bank branches, and Synchrony Bank's own FDIC filing
        # books its entire nationwide deposit base to a Utah head office, so it can lead
        # a deposit ranking here while running almost no local branch network. Renamed so
        # the field's own name says what it is, and paired with the actual branch count
        # so "single bank" is never the only fact the model has to reason from. Considered
        # dropping the name outright; kept it because the underlying deposit figure is
        # real and grounded, and a hard denylist of head-office-booking institutions
        # would be a guess this lane has no data to justify. The caveat plus the four new
        # single-bank bans below carry the weight the name-only field used to leave to
        # the model's judgement.
        top_bank = (banks.get("top5") or [{}])[0].get("name") if banks.get("top5") else None
        bank_branch_count = banks.get("n_branches")
        bank_branch_count = None if bank_branch_count is None else int(round(bank_branch_count))

        # Pilot defect 4 (Moran, "employs more thickly than its office count alone
        # would show" on a 1.09-vs-1.05 gap): the contrast is a ledger field now, gated
        # on JOBS_OFFICES_GAP_THRESHOLD, not a comparison the model draws itself from
        # two raw numbers.
        jobs_offices_gap = None
        if cu_lq is not None and est_lq is not None:
            jobs_offices_gap = round(cu_lq - est_lq, 3)
        if jobs_offices_gap is None:
            jobs_offices_relationship = None
        elif abs(jobs_offices_gap) < JOBS_OFFICES_GAP_THRESHOLD:
            jobs_offices_relationship = "about_equal"
        elif jobs_offices_gap > 0:
            jobs_offices_relationship = "jobs_outpace_offices"
        else:
            jobs_offices_relationship = "offices_outpace_jobs"

        credit_unions = {
            "source": ("NCUA call report 2026-Q1 and FDIC Summary of Deposits 2025, "
                       "matched to street addresses and summed across every seat in "
                       "the state" if kind == "state" else
                       "NCUA call report 2026-Q1 and FDIC Summary of Deposits 2025, "
                       "matched to street addresses in this geography"),
            "credit_union_share_of_retail_offices_pct": r(share_pct, 1),
            "credit_union_office_share_vs_peers": pband("cu_share", share),
            "largest_credit_unions_by_name": [
                str(x.get("name")) for x in (cus.get("top5") or [])[:3] if x.get("name")],
            "largest_bank_by_deposits_name": top_bank,
            "bank_branch_count": bank_branch_count,
            "largest_bank_by_deposits_caveat": (
                None if not top_bank else
                "Chosen by deposits booked to addresses in this geography under federal "
                "reporting rules, not by branch presence or local customers. A bank with "
                "almost no branch network here can still come out ahead on that figure "
                "if its nationwide deposits are booked to a single office in this "
                "geography. This geography has %s bank branches in total, run by many "
                "separate banks: write about this name only as the largest by deposits, "
                "and never write about it as if it has no local rivals."
                % (bank_branch_count if bank_branch_count is not None
                   else "more than one")),
            "credit_union_jobs_against_national_norm": r(cu_lq, 2),
            "credit_union_jobs_specialization": lq_band(cu_lq),
            "credit_union_offices_against_national_norm": r(est_lq, 2),
            "credit_union_offices_specialization": lq_band(est_lq),
            "credit_union_jobs_vs_offices_relationship": jobs_offices_relationship,
            "all_finance_jobs_against_national_norm": r(proxy_lq, 2),
            "all_finance_growth_against_national_norm": r(proxy_gq, 2),
            "growth_caveat": ("That growth number covers finance and insurance together. "
                              "Nothing here follows credit unions on their own across "
                              "the years."),
            "scale_caveat": ("Office totals and deposit sums here were apportioned from "
                             "wider areas and then divided evenly. Only share and scale "
                             "are safe to write about."),
        }
        if credit_unions["credit_union_jobs_against_national_norm"] is None:
            credit_unions.pop("credit_union_jobs_specialization", None)
        if credit_unions["credit_union_offices_against_national_norm"] is None:
            credit_unions.pop("credit_union_offices_specialization", None)

    # ------------------------------------------------------------- industry
    industry = None
    if ind_row:
        rows_all = sector_rows(ind_row, art["industry"].get("sector_meta") or {})
        usable = [s for s in rows_all if not s["small"] and s["lq"] is not None]
        by_lq = sorted(usable, key=lambda s: -s["lq"])[:3]
        by_size = sorted([s for s in rows_all if s["share_pct"] is not None],
                         key=lambda s: -s["share_pct"])[:3]
        total_jobs = finite((ind_row.get("employment") or {}).get("C000"))
        industry = {
            "source": "workplace employment counts by sector, twenty sectors",
            "measured_year": latest_year,
            "comparison_window": window or None,
            "most_specialized_sectors": [
                {"sector": s["name"], "share_of_jobs_pct": s["share_pct"],
                 "against_national_norm": s["lq"], "specialization": lq_band(s["lq"])}
                for s in by_lq],
            "biggest_employing_sectors": [
                {"sector": s["name"], "share_of_jobs_pct": s["share_pct"],
                 "against_national_norm": s["lq"], "specialization": lq_band(s["lq"])}
                for s in by_size],
            "top_specialization": lq_band(by_lq[0]["lq"]) if by_lq else None,
        }
        # Constraint 3: absolute employment levels are withheld wherever the LODES
        # base fails its own external-validation bar, so no such figure can be quoted.
        if total_jobs is not None and not fail:
            industry["total_jobs_approx"] = int(round(total_jobs, -2))
        if fail:
            industry["total_jobs_withheld_reason"] = (
                "No total number of jobs is given for this state because the count is "
                "not dependable. Write about the mix of work and how it compares "
                "nationally, and nothing about how many jobs there are.")

    # ------------------------------------------------------------ borrowing
    lending = None
    if hmda:
        lending = {
            "source": "Home Mortgage Disclosure Act, 2024, all lenders",
            "home_purchase_share_of_originations_pct": r(
                (finite(hmda.get("home_purchase_share")) or 0) * 100.0, 1)
            if hmda.get("home_purchase_share") is not None else None,
            "lower_income_neighbourhood_share_of_lending_pct": r(
                (finite(hmda.get("lmi_tract_share")) or 0) * 100.0, 1)
            if hmda.get("lmi_tract_share") is not None else None,
            "caveat": "Every lender, not credit unions alone.",
        }

    record = {
        "name": row["name"],
        "chamber": row["chamber"],
        "party": row["party"],
        "geography": {
            "noun": noun,
            "state_name": row["state_name"],
            "label": geo_label(kind, row["state_name"], geo),
        },
        "community": community,
        "credit_unions": credit_unions,
        "industry": industry,
        "borrowing": lending,
        "constraints": constraints,
    }
    if kind == "district":
        record["constraints"]["boundaries_redrawn_since_118th"] = bool(
            (ind_row or {}).get("redrawn_since_118"))
    if row["chamber"] == "Senate":
        sib = sibling_map.get((st, row["name"]))
        record["geography"]["shared_with_other_senator"] = sib
        record["geography"]["seat_slot"] = sibling_map.get(("slot", row["name"]))
        # Deliberately phrased without the word this member's brief may not use.
        record["constraints"]["evidence_covers_the_whole_state"] = True
        record["constraints"]["identical_evidence_to_the_other_senator"] = bool(sib)
    return record


# ------------------------------------------------------------------- audit
AUDIT_KEYS = ("pct", "margin_of_error_90pct")


def strip_audit(node):
    """Remove keys the model must never see as quotable figures (percentile
    fractions). Also drops empty branches so the model is not invited to
    hallucinate around gaps (gen_briefs.py:445-456)."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in AUDIT_KEYS:
                continue
            value = strip_audit(value)
            if value not in (None, {}, [], ""):
                out[key] = value
        return out
    if isinstance(node, list):
        return [strip_audit(v) for v in node]
    return node


def numeric_leaves(node, acc):
    if isinstance(node, dict):
        for value in node.values():
            numeric_leaves(value, acc)
    elif isinstance(node, list):
        for value in node:
            numeric_leaves(value, acc)
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        value = finite(node)
        if value is not None:
            acc.append(value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    art = {
        "community": load(P("data", "district", "district_community.json")),
        "industry": load(P("data", "lobbying", "district_industry.json")),
        "four_digit": load(P("data", "lobbying", "district_industry_4digit.json")),
        "financial": load(P("data", "district", "district_financial.json")),
        "employers": load(P("data", "district", "district_employers.json")),
        "hmda": load(P("data", "district", "district_hmda.json")),
        "validation": load(P("data", "lobbying", "district_industry_validation.json")),
    }
    bundle = load(P("member_desk_bundle.json"))
    roster = build_roster(bundle)
    print("roster: %d members (%s)" % (
        len(roster), dict(collections.Counter(x["chamber"] for x in roster))))

    qcew_fail = {}
    for r_ in (((art["validation"].get("bars") or {}).get("state_totals") or {}).get("rows") or []):
        if r_.get("verdict") == "fail":
            qcew_fail[r_["state"]] = r_
    print("QCEW-FAIL states (%d): %s" % (len(qcew_fail), ", ".join(sorted(qcew_fail))))

    cu = CreditUnionMetrics(art["four_digit"])

    # senator sibling map
    by_state = collections.defaultdict(list)
    for row in roster:
        if row["chamber"] == "Senate":
            by_state[state_abbr(row["geo"])].append(row["name"])
    sibling_map = {}
    for st, names in by_state.items():
        names = sorted(names)
        for i, nm in enumerate(names):
            others = [x for x in names if x != nm]
            sibling_map[(st, nm)] = others[0] if others else None
            sibling_map[("slot", nm)] = "first of two by name" if i == 0 else "second of two by name"

    # ------------------------------------------------------------- pools
    def collect(kind):
        geos, seen = [], set()
        for row in roster:
            if row["geo_kind"] != kind or not row["geo"] or row["geo"] in seen:
                continue
            seen.add(row["geo"])
            geos.append(row["geo"])
        return geos

    pools = {}
    for kind in ("district", "state"):
        geos = collect(kind)
        src = art["community"]["districts"] if kind == "district" else art["community"]["states"]
        income, home, rent_inc, owner, pov, unemp, dens, cushare = [], [], [], [], [], [], [], []
        for geo in geos:
            com = src.get(geo) or {}
            est, der = com.get("estimates") or {}, com.get("derived") or {}
            income.append(est.get("median_household_income"))
            home.append(est.get("median_home_value"))
            rent_inc.append(der.get("annual_rent_income_ratio"))
            owner.append(der.get("owner_occupied_share"))
            pov.append(der.get("poverty_rate"))
            unemp.append(der.get("unemployment_rate"))
            dens.append(der.get("population_density_sq_mile"))
            fin = (art["financial"].get("districts") or {}).get(geo) or {}
            cushare.append(((fin.get("ratio") or {}).get("cu_branch_share")))
        pools[kind] = {
            "income": desk_rank(income), "home_value": desk_rank(home),
            "rent_income": desk_rank(rent_inc), "owner_share": desk_rank(owner),
            "poverty": desk_rank(pov), "unemployment": desk_rank(unemp),
            "density": desk_rank(dens), "cu_share": desk_rank(cushare),
        }
        print("pool %-8s geos=%3d  income_n=%3d  cu_share_n=%3d"
              % (kind, len(geos), pools[kind]["income"].n, pools[kind]["cu_share"].n))

    # State-scope CU branch share: the financial artifact is districts-only, so a
    # senator's state share has to be aggregated. Build it, then rank states.
    state_fin = {}
    for geo, rec in (art["financial"].get("districts") or {}).items():
        st = geo[:2]
        acc = state_fin.setdefault(st, {"cu": 0.0, "bank": 0.0, "cu_names": {}, "bank_names": {}})
        acc["cu"] += finite((rec.get("cus") or {}).get("n_branches")) or 0.0
        acc["bank"] += finite((rec.get("banks") or {}).get("n_branches")) or 0.0
        for x in (rec.get("cus") or {}).get("top5") or []:
            acc["cu_names"][x.get("name")] = acc["cu_names"].get(x.get("name"), 0.0) + (
                finite(x.get("est_shares_kusd")) or 0.0)
        for x in (rec.get("banks") or {}).get("top5") or []:
            acc["bank_names"][x.get("name")] = acc["bank_names"].get(x.get("name"), 0.0) + (
                finite(x.get("deposits_kusd")) or 0.0)
    for st, acc in state_fin.items():
        total = acc["cu"] + acc["bank"]
        acc["share"] = (acc["cu"] / total) if total > 0 else None
    state_geos = {row["geo"] for row in roster if row["geo_kind"] == "state"}
    pools["state"]["cu_share"] = desk_rank(
        [state_fin.get(g, {}).get("share") for g in sorted(state_geos)])
    print("pool state    cu_share rebuilt from district aggregation, n=%d"
          % pools["state"]["cu_share"].n)

    # Patch the state-scope financial record in as a synthetic 'districts' entry so
    # build_member's district lookup finds it. Marked so nothing mistakes it for source.
    for st, acc in state_fin.items():
        if st in state_geos:
            art["financial"]["districts"][st] = {
                "banks": {"n_branches": round(acc["bank"], 1),
                          "top5": [{"name": n} for n, _ in sorted(
                              acc["bank_names"].items(), key=lambda kv: -kv[1])[:5]]},
                "cus": {"n_branches": round(acc["cu"], 1),
                        "top5": [{"name": n} for n, _ in sorted(
                            acc["cu_names"].items(), key=lambda kv: -kv[1])[:5]]},
                "ratio": {"cu_branch_share": (None if acc["share"] is None
                                              else round(acc["share"], 4))},
                "_aggregated_from_districts": True,
            }

    # ------------------------------------------------------------ assemble
    ledger, coverage = {}, collections.Counter()
    cu_check_ok, cu_check_bad = 0, []
    for row in roster:
        rec = build_member(row, art, pools, cu, qcew_fail, sibling_map)
        # cross-check the replicated CU snapshot against the already-built desk bundle
        bundled = row["bundle_cu_metrics"] or {}
        mine = cu.snapshot(row["geo"]) if row["geo"] else None
        if bundled and mine:
            same = (bundled.get("employment_lq") == mine["employment_lq"]
                    and bundled.get("establishment_lq") == mine["establishment_lq"]
                    and bundled.get("employment") == mine["employment"]
                    and bundled.get("establishments") == mine["establishments"])
            if same:
                cu_check_ok += 1
            else:
                cu_check_bad.append((row["name"], bundled, mine))
        ledger[row["name"]] = rec
        coverage["members"] += 1
        coverage["community"] += 1 if rec["community"] else 0
        coverage["credit_unions"] += 1 if rec["credit_unions"] else 0
        coverage["industry"] += 1 if rec["industry"] else 0
        coverage["borrowing"] += 1 if rec["borrowing"] else 0
        coverage["qcew_fail_state"] += 1 if rec["constraints"]["employment_levels_unreliable"] else 0
        if rec["constraints"].get("industry_vintage_is_current") is False:
            coverage["stale_vintage"] += 1
        coverage["redrawn"] += 1 if rec["constraints"].get("boundaries_redrawn_since_118th") else 0
        coverage["cu_branch_share"] += 1 if (rec.get("credit_unions") or {}).get("credit_union_share_of_retail_offices_pct") is not None else 0
        coverage["senate_state_geo"] += 1 if row["chamber"] == "Senate" else 0
        if rec["industry"]:
            b = rec["industry"]["top_specialization"]
            coverage["topband_" + (b or "none").replace(" ", "_")] += 1

    print()
    print("CU snapshot replication vs desk bundle: %d agree, %d disagree"
          % (cu_check_ok, len(cu_check_bad)))
    if cu_check_bad:
        for nm, b, m in cu_check_bad[:5]:
            print("  MISMATCH", nm, b, m)
        sys.exit("CU metric replication does not reproduce the built bundle. Stopping.")

    # allowed-figure sets are computed over the SENT ledger, not this one
    sent = {name: strip_audit(rec) for name, rec in ledger.items()}
    figs = collections.Counter()
    for name, rec in sent.items():
        acc = []
        numeric_leaves(rec, acc)
        figs[name] = len(acc)

    out_path = os.path.join(args.out_dir, "demo_ledger.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"schema": "district-demographic-evidence-v1",
                   "generated_from": {
                       "community": "data/district/district_community.json",
                       "industry": "data/lobbying/district_industry.json",
                       "four_digit": "data/lobbying/district_industry_4digit.json",
                       "financial": "data/district/district_financial.json",
                       "employers": "data/district/district_employers.json",
                       "hmda": "data/district/district_hmda.json",
                       "validation": "data/lobbying/district_industry_validation.json",
                       "roster": "member_desk_bundle.json"},
                   "lq_bands": "tpl:483  <1.00 Not / <1.25 Slightly / <2.00 Specialized / else Highly",
                   "members": ledger}, fh, ensure_ascii=False, indent=1)

    summary = {
        "members": coverage["members"],
        "community": coverage["community"],
        "credit_unions": coverage["credit_unions"],
        "cu_branch_share_present": coverage["cu_branch_share"],
        "industry": coverage["industry"],
        "borrowing": coverage["borrowing"],
        "qcew_fail_state_members": coverage["qcew_fail_state"],
        "stale_vintage_members": coverage["stale_vintage"],
        "redrawn_members": coverage["redrawn"],
        "senate_state_geo_members": coverage["senate_state_geo"],
        "top_specialization": {k[8:].replace("_", " "): v
                                    for k, v in coverage.items() if k.startswith("topband_")},
        "numeric_leaves_per_member": {
            "min": min(figs.values()), "max": max(figs.values()),
            "median": sorted(figs.values())[len(figs) // 2]},
        "cu_snapshot_agrees_with_desk_bundle": cu_check_ok,
    }
    with open(os.path.join(args.out_dir, "demo_ledger_coverage.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print()
    print(json.dumps(summary, indent=1))
    print()
    print("wrote", out_path)


if __name__ == "__main__":
    main()

