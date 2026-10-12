"""Deterministic, read-only analysis of scoped contribution observations.

The adapter owns accounting classification and source selection. This module never
deduplicates evidence, changes an amount, fetches data, or treats observed activity
as proof of complete coverage. Integer cents remain additive; money channels are
always separate. Dates are event dates, not evidence of when a filing was known.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from functools import lru_cache
from itertools import combinations
from statistics import median
import calendar
import re


PANEL_ORDER = ("favorites", "trends", "changes", "concentration",
               "relationships", "overlap", "timing", "forecast")
MAX_TOP_N = 100
MAX_OVERLAP_COMPANIES = 100
MAX_COMPACT_GROUPS = 250_000
MAX_DONOR_IDENTITIES = 500_000
UNKNOWN = "Unknown"


def _date(value):
    return _date_text(str(value or ""))


@lru_cache(maxsize=4096)
def _date_text(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _cycle(value):
    """Return canonical even cycle-end year; reject malformed two-year ranges."""
    return _cycle_text(str(value or "").strip())


@lru_cache(maxsize=512)
def _cycle_text(text):
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return year + year % 2 if 1800 <= year <= 2199 else None
    match = re.fullmatch(r"(\d{4})[-\u2013](\d{4})", text)
    if match:
        start, end = map(int, match.groups())
        if end == start + 1 and end % 2 == 0 and 1800 <= end <= 2200:
            return end
    return None


def _label(cycle):
    return f"{cycle - 1}-{cycle}" if cycle else UNKNOWN


def _ratio(numerator, denominator):
    return round(numerator / denominator, 8) if denominator > 0 else None


def _rounded_ratio(numerator, denominator):
    sign = -1 if numerator < 0 else 1
    return sign * ((abs(numerator) * 2 + denominator) // (2 * denominator))


def _columns(*specs):
    return [{"key": key, "label": label, "type": kind} for key, label, kind in specs]


def _panel(title, summary, columns, rows=None, notices=None, **extra):
    rows = rows or []
    return dict(status="ready" if rows else "unavailable", title=title,
                summary=summary, columns=columns, rows=rows, notices=notices or [], **extra)


def _bound(panel, top_n):
    panel["total_rows"] = len(panel["rows"])
    panel["truncated"] = len(panel["rows"]) > top_n
    if panel["truncated"]:
        panel["notices"].append(f"Showing {top_n} of {len(panel['rows'])} rows. Calculations use every selected observation.")
        panel["rows"] = panel["rows"][:top_n]
    return panel


def _filters(**values):
    return [{"field": key, "op": "in", "value": ["", UNKNOWN]} if value == UNKNOWN and key in ("company", "funding_source") else
            {"field": key, "op": "eq", "value": value}
            for key, value in values.items() if value is not None and value != ""]


def _money(rows):
    gross, adjustments, records = 0, 0, 0
    for row in rows:
        gross += max(0, row["amount_cents"])
        adjustments += min(0, row["amount_cents"])
        records += row.get("record_count", 1)
    return {"amount_cents": gross + adjustments, "gross_cents": gross,
            "adjustment_cents": adjustments, "records": records}


def _groups(rows, *keys):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    return groups


def _selector(request):
    filters = {}
    for singular, plural, field in (("company", "companies", "company"),
                                   ("committee_id", "committee_ids", "committee_id"),
                                   ("funding_source", "funding_sources", "funding_source"),
                                   ("industry", "industries", "industry")):
        values = request.get(plural) or ([request[singular]] if request.get(singular) else [])
        if isinstance(values, str):
            values = [values]
        if values:
            filters[field] = set(map(str, values))
    if request.get("cycles"):
        values = request["cycles"]
        if isinstance(values, (str, int)):
            values = [values]
        filters["cycle"] = {_cycle(value) for value in values} - {None}
    return lambda row: all(row[field] in values for field, values in filters.items())


def _normalize(rows, request, as_of, count_donors):
    """Stream into exact monthly/company/recipient/sign cells, never source-row lists.

    The sign is part of a cell's key so netting never destroys positive-dollar
    denominators. Each month is split at the matched historical cutoff, preserving
    exact same-stage amounts. No source facts or donor IDs enter the payload.
    """
    facts, donors = {}, defaultdict(set)
    invalid_amounts = invalid_dates = invalid_cycles = mismatches = 0
    input_records = after_cutoff = selected_records = donor_entries = 0
    accepts = _selector(request)
    capacity_exceeded = False
    for source in rows:
        input_records += 1
        amount = source.get("amount_cents")
        if isinstance(amount, bool) or not isinstance(amount, int):
            invalid_amounts += 1
            continue
        event_date = _date(source.get("date"))
        cycle = _cycle(source.get("cycle"))
        if not cycle and not source.get("cycle") and event_date:
            cycle = event_date.year + event_date.year % 2
        if not event_date:
            invalid_dates += 1
        if not cycle:
            invalid_cycles += 1
        mismatch = bool(event_date and cycle and event_date.year not in (cycle - 1, cycle))
        if mismatch:
            mismatches += 1
        fact = {key: str(source.get(key) or "") for key in (
            "company", "committee_id", "recipient", "funding_source", "industry")}
        fact["company"] = fact["company"] or UNKNOWN
        fact["funding_source"] = fact["funding_source"] or UNKNOWN
        fact["recipient"] = fact["recipient"] or fact["committee_id"] or UNKNOWN
        # Names without an ID remain names; they are never asserted to be resolved IDs.
        fact["recipient_key"] = fact["committee_id"] or "name:" + fact["recipient"]
        fact.update(amount_cents=amount, cycle=cycle, date=event_date,
                    temporal_date=event_date if cycle and not mismatch else None, record_count=1)
        if not accepts(fact):
            continue
        if as_of and event_date and event_date > as_of:
            after_cutoff += 1
            continue
        selected_records += 1
        temporal = fact["temporal_date"]
        period = temporal.strftime("%Y-%m") if temporal else None
        before_stage = bool(temporal and as_of and temporal <= _cutoff(cycle, as_of))
        key = (fact["company"], fact["recipient_key"], fact["funding_source"], cycle,
               period, before_stage, (amount > 0) - (amount < 0))
        if key in facts:
            facts[key]["amount_cents"] += amount
            facts[key]["record_count"] += 1
            facts[key]["recipient"] = min(facts[key]["recipient"], fact["recipient"])
            if temporal:
                facts[key]["temporal_date"] = min(facts[key]["temporal_date"], temporal)
        else:
            if len(facts) >= MAX_COMPACT_GROUPS:
                capacity_exceeded = True
                break
            # Industry and source IDs have already served their filtering/provenance
            # purpose; they are not stored again for every compact cell.
            fact.pop("industry", None)
            facts[key] = fact
        donor = str(source.get("contributor_key") or "")
        if count_donors and amount > 0 and donor:
            donor_key = (fact["funding_source"], fact["recipient_key"])
            if donor not in donors[donor_key]:
                if donor_entries >= MAX_DONOR_IDENTITIES:
                    # Exact donor breadth can be withheld while all money amounts
                    # remain exact; partial identity counts are never published.
                    count_donors = False
                    donors.clear()
                else:
                    donors[donor_key].add(donor)
                    donor_entries += 1
    quality = {"invalid_amount_rows": invalid_amounts, "undated_rows": invalid_dates,
               "invalid_cycle_rows": invalid_cycles, "date_cycle_conflicts": mismatches}
    footprint = {"input_records": input_records, "selected_records": selected_records,
                 "after_as_of_records": after_cutoff, "compact_groups": len(facts),
                 "max_compact_groups": MAX_COMPACT_GROUPS, "complete_input_scan": not capacity_exceeded,
                 "donor_breadth_available": count_donors, "identity_entries_retained": donor_entries if count_donors else 0}
    counts = {key: len(values) for key, values in donors.items()} if count_donors else None
    return list(facts.values()), quality, footprint, counts


def _comparison(rows, request):
    cycles = sorted({row["cycle"] for row in rows if row["cycle"]})
    comparison = request.get("comparison_cycles") or []
    if isinstance(comparison, (str, int)):
        comparison = [comparison]
    comparison = sorted({_cycle(value) for value in comparison} - {None})
    before = _cycle(request.get("baseline_cycle"))
    after = _cycle(request.get("current_cycle"))
    if len(comparison) >= 2:
        before, after = comparison[0], comparison[-1]
    after = after or (cycles[-1] if cycles else None)
    before = before or next((value for value in reversed(cycles) if after and value < after), None)
    return before, after


def _complete_cycles(context):
    values = context.get("complete_cycles")
    return {_cycle(value) for value in ([] if values is None else values)} - {None}


def _company_coverage(context, cycle, company):
    """Only explicit company coverage declarations establish comparability."""
    coverage = context.get("company_coverage_by_cycle") or {}
    entry = coverage.get(_label(cycle), coverage.get(str(cycle), coverage.get(cycle)))
    if isinstance(entry, list):
        return company in entry
    if isinstance(entry, dict):
        status = entry.get(company)
        if isinstance(status, dict):
            return status.get("complete") is True or status.get("status") == "complete"
        return status is True or status == "complete"
    return False


@lru_cache(maxsize=1024)
def _cutoff(cycle, as_of):
    phase_cycle = as_of.year + as_of.year % 2
    year = cycle - (phase_cycle - as_of.year)
    return date(year, as_of.month, min(as_of.day, calendar.monthrange(year, as_of.month)[1]))


def _recipient_name(rows):
    names = sorted({row["recipient"] for row in rows if row["recipient"] != UNKNOWN})
    return names[0] if names else UNKNOWN


def _recipient_filters(rows):
    committee = rows[0]["committee_id"]
    if committee:
        return _filters(committee_id=committee)
    # Name-only groups must not open every recipient in a channel or merge into a
    # resolved committee with the same reported name.
    return [{"field": "committee_id", "op": "eq", "value": ""},
            ({"field": "recipient", "op": "in", "value": ["", UNKNOWN]} if _recipient_name(rows) == UNKNOWN else
             {"field": "recipient", "op": "eq", "value": _recipient_name(rows)})]


def _comparison_scopes(before, after, as_of, **values):
    scopes = {}
    for name, cycle in (("baseline", before), ("current", after)):
        filters = _filters(**values, cycle=_label(cycle))
        if as_of and cycle:
            filters += [{"field": "date", "op": "gte", "value": f"{cycle - 1}-01-01"},
                        {"field": "date", "op": "lte", "value": _cutoff(cycle, as_of).isoformat()}]
        scopes[name] = filters
    return scopes


def _comparison_rows(rows, before, after, as_of):
    return [row for row in rows if row["cycle"] in (before, after)
            and (not as_of or row["temporal_date"] and row["temporal_date"] <= _cutoff(row["cycle"], as_of))]


RECIPIENT_COMPONENTS = ("Continuing observed recipients", "Newly observed recipients", "No longer observed recipients")
UNATTRIBUTED_RECIPIENTS = "Unattributed recipient observations"
PERIOD_LEADERS = 10


def _recipient_scopes(before, after, as_of, channel, groups):
    """Exact evidence scopes for a set of recipient groups, or None.

    Filters are conjunctive, so one scope holds either committee IDs or name-only
    recipients (never both), and at most 1,000 choices; otherwise no scope is
    offered rather than a broader one.
    """
    scopes = _comparison_scopes(before, after, as_of, funding_source=channel)
    committees = sorted({members[0]["committee_id"] for members in groups if members[0]["committee_id"]})
    names = set()
    for members in groups:
        if not members[0]["committee_id"]:
            name = _recipient_name(members)
            names |= {"", UNKNOWN} if name == UNKNOWN else {name}
    if not groups:
        extra = [{"field": "amount", "op": "eq", "value": 0}]
    elif (committees and names) or len(committees) > 1000 or len(names) > 1000:
        return None
    elif committees:
        extra = [{"field": "committee_id", "op": "in", "value": committees}]
    else:
        extra = [{"field": "committee_id", "op": "eq", "value": ""},
                 {"field": "recipient", "op": "in", "value": sorted(names)}]
    for filters in scopes.values():
        filters.extend(extra)
    return scopes


def _favorites(rows, context, request=None, as_of=None, **_):
    totals = {channel: sum(max(0, row["amount_cents"]) for row in group)
              for (channel,), group in _groups(rows, "funding_source").items()}
    reference = context.get("industry_reference") or {}
    # Same-stage amounts for the selected pair (board I1's Amount and Change),
    # kept beside the all-cycle favorite measures, never substituted for them.
    before, after = _comparison(rows, request or {})
    paired = bool(before and after and before < after)
    staged = defaultdict(lambda: [0, 0])
    if paired:
        for row in _comparison_rows(rows, before, after, as_of):
            staged[(row["funding_source"], row["recipient_key"])][row["cycle"] == after] += row["amount_cents"]
    result, leaders = [], []
    for (channel, recipient_key), group in _groups(rows, "funding_source", "recipient_key").items():
        money = _money(group)
        positive = [row for row in group if row["amount_cents"] > 0]
        cycles = sorted({row["cycle"] for row in positive if row["cycle"]})
        committee = next((row["committee_id"] for row in group if row["committee_id"]), "")
        share = _ratio(money["gross_cents"], totals[channel])
        comparison = (reference.get("by_channel") or {}).get(channel, {})
        reference_share = _ratio((comparison.get("recipient_positive_cents") or {}).get(committee, 0),
                                 comparison.get("total_positive_cents", 0)) if comparison and committee else None
        first, second = staged[(channel, recipient_key)] if paired else (None, None)
        result.append(dict(funding_source=channel, committee_id=committee,
                           recipient=_recipient_name(group), **money,
                           positive_share=share,
                           companies=len({row["company"] for row in positive if row["company"] != UNKNOWN}),
                           contributors=(context["_recipient_contributor_counts"].get((channel, recipient_key), 0)
                                         if context.get("_recipient_contributor_counts") is not None else None),
                           observed_cycles=len(cycles), cycles=[_label(cycle) for cycle in cycles],
                           relative_preference=_ratio(share, reference_share) if share is not None and reference_share else None,
                           reference_share=reference_share,
                           baseline_cents=first, current_cents=second,
                           change_cents=second - first if paired else None,
                           filters=_filters(funding_source=channel) + _recipient_filters(group)))
        if paired and second > 0:
            scopes = _comparison_scopes(before, after, as_of, funding_source=channel)
            for filters in scopes.values():
                filters.extend(_recipient_filters(group))
            leaders.append(dict(funding_source=channel, committee_id=committee, recipient=_recipient_name(group),
                                baseline_cycle=_label(before), current_cycle=_label(after),
                                baseline_cents=first, current_cents=second, change_cents=second - first,
                                filters=scopes["current"], comparison_scopes=scopes))
    result.sort(key=lambda row: (-row["gross_cents"], row["funding_source"], row["recipient"], row["committee_id"]))
    leaders.sort(key=lambda row: (row["funding_source"], -row["current_cents"], row["recipient"], row["committee_id"]))
    leaders = [row for (channel,), group in sorted(_groups(leaders, "funding_source").items()) for row in group[:PERIOD_LEADERS]]
    notices = ["Shares and breadth use positive observations; signed adjustments remain visible in net amounts.",
               "Persistence counts cycles with observed positive activity, not proven uninterrupted support. Employee-associated activity does not establish institutional preference.",
               "Reported donor identities are source identifiers, not independently verified unique people."]
    if reference:
        notices += [f"Relative preference compares positive-dollar shares with {reference.get('label', 'the supplied reference scope')}. Zero or absent reference shares are unavailable."]
        if reference.get("coverage_note"):
            notices.append(str(reference["coverage_note"]))
    else:
        notices.append("Relative preference is unavailable without a comparable other-industry reference scope.")
    if any(not row["committee_id"] for row in rows):
        notices.append("Recipients without committee IDs are grouped by their reported names; identity matching remains unresolved.")
    if paired:
        notices.append(f"Baseline and current amounts are same-stage net amounts for {_label(before)} and {_label(after)}"
                       + (" through matching event-date cutoffs" if as_of else "") + "; net amount and shares use every observed cycle. "
                       f"Current-period leaders rank the {PERIOD_LEADERS} recipients with the largest positive current same-stage net amount in each channel, from every selected observation.")
    return _panel("Industry favorites", "Recipient amounts, breadth and observed persistence within each money channel.",
                  _columns(("recipient", "Recipient", "text"), ("funding_source", "Channel", "text"),
                           ("amount_cents", "Net amount", "money"), ("gross_cents", "Positive amount", "money"),
                           ("adjustment_cents", "Negative adjustments", "money"), ("positive_share", "Positive-dollar share", "percent"),
                           ("companies", "Companies", "number"), ("observed_cycles", "Observed cycles", "number"),
                           ("relative_preference", "Relative preference", "number"),
                           ("baseline_cents", "Baseline same-stage net", "money"), ("current_cents", "Current same-stage net", "money"),
                           ("change_cents", "Change", "money")), result, notices,
                  current_period_leaders=leaders)


def _trends(rows, context, as_of, **_):
    result, months = [], []
    complete = _complete_cycles(context)
    for (channel, cycle), group in _groups(rows, "funding_source", "cycle").items():
        cutoff = _cutoff(cycle, as_of) if cycle and as_of else None
        dated = [row for row in group if row["temporal_date"]]
        matched = [row for row in dated if cutoff and row["temporal_date"] <= cutoff]
        known_companies = {row["company"] for row in group if row["company"] != UNKNOWN}
        result.append(dict(funding_source=channel, cycle=_label(cycle), cycle_end=cycle,
                           **_money(group), matched_amount_cents=_money(matched)["amount_cents"] if cutoff else None,
                           matched_through=cutoff.isoformat() if cutoff else None,
                           companies=len(known_companies), temporal_excluded_records=sum(row["record_count"] for row in group if not row["temporal_date"]),
                           coverage="Explicitly complete" if cycle in complete else "Completeness unverified",
                           matched_filters=(_filters(funding_source=channel, cycle=_label(cycle)) +
                                            [{"field": "date", "op": "gte", "value": f"{cycle - 1}-01-01"},
                                             {"field": "date", "op": "lte", "value": cutoff.isoformat()}]) if cutoff else None,
                           filters=_filters(funding_source=channel, cycle=_label(cycle)) if cycle else None))
    for (channel, month), group in _groups([dict(row, month=row["temporal_date"].strftime("%Y-%m"))
                                           for row in rows if row["temporal_date"]], "funding_source", "month").items():
        months.append(dict(funding_source=channel, month=month, **_money(group)))
    result.sort(key=lambda row: (-(row["cycle_end"] or 0), row["funding_source"]))
    months.sort(key=lambda row: (row["month"], row["funding_source"]))
    return _panel("Comparable-cycle trends", "Full observed amounts and same-election-stage amounts are displayed separately.",
                  _columns(("cycle", "Cycle", "text"), ("funding_source", "Channel", "text"),
                           ("amount_cents", "Full observed net", "money"), ("matched_amount_cents", "Same-stage net", "money"),
                           ("matched_through", "Matched cutoff", "text"), ("companies", "Observed companies", "number"),
                           ("coverage", "Coverage", "text")), result,
                  ["Matching event-date cutoffs does not establish matching filing coverage or reconstruct what was known at that historical cutoff.",
                   "Missing months are omitted, not filled with zero. Undated or cycle-conflicting records remain in full amounts and are excluded from temporal comparisons.",
                   "Rows with invalid cycle labels have no cycle drill-down; their records remain traceable through the recipient views."],
                  series=months)


def _changes(rows, request, context, as_of, **_):
    before, after = _comparison(rows, request)
    full_rows = rows
    rows = _comparison_rows(rows, before, after, as_of) if before and after else []
    notices = ["The three components reconcile exactly to observed net change. Newly/no-longer observed does not prove entry, exit, collection coverage, or a change in behavior.",
               "Continuing-company comparisons hold observed company membership constant; an explicitly covered comparison additionally needs coverage evidence for both cycles.",
               "Without versioned snapshots, filing revisions, alias changes and new collection cannot be separated from actual activity changes.",
               "Change uses matching event-date cutoffs in each cycle. Full observed totals are kept separately in the reconciliation details; undated and cycle-conflicting records cannot enter matched amounts." if as_of else "No as-of cutoff supplied; comparison uses full observed amounts.",
               "The chart decomposes the change by recipient (committee ID, else reported name); the table decomposes the same change by company. A recipient or company is observed in a period when it has any same-stage record there. Both decompositions reconcile exactly to the observed net change."]
    result, cards, detail, recipient_rows = [], [], [], []
    if before and after and before < after:
        for (channel,), group in sorted(_groups(rows, "funding_source").items()):
            past = _groups([row for row in group if row["cycle"] == before], "company")
            current = _groups([row for row in group if row["cycle"] == after], "company")
            components = {"Continuing observed companies": 0, "Newly observed companies": 0, "No longer observed companies": 0}
            continuing_before = continuing_after = covered_before = covered_after = 0
            for key in sorted(set(past) | set(current)):
                company = key[0]
                first = _money(past.get(key, []))["amount_cents"]
                second = _money(current.get(key, []))["amount_cents"]
                category = "Continuing observed companies" if key in past and key in current else "Newly observed companies" if key in current else "No longer observed companies"
                if company == UNKNOWN:
                    category = "Unattributed company observations"
                    components.setdefault(category, 0)
                components[category] += second - first
                covered = company != UNKNOWN and _company_coverage(context, before, company) and _company_coverage(context, after, company)
                if key in past and key in current and company != UNKNOWN:
                    continuing_before += first
                    continuing_after += second
                if covered:
                    covered_before += first
                    covered_after += second
                scopes = _comparison_scopes(before, after, as_of, company=company, funding_source=channel)
                detail.append(dict(company=company, funding_source=channel, component=category,
                                   baseline_cents=first, current_cents=second, change_cents=second - first,
                                   coverage_comparable=covered,
                                   filters=scopes["current"], comparison_scopes=scopes))
            total_before = sum(row["amount_cents"] for row in group if row["cycle"] == before)
            total_after = sum(row["amount_cents"] for row in group if row["cycle"] == after)
            for component, change in components.items():
                members = [value for item in detail if item["funding_source"] == channel and item["component"] == component
                           for value in (["", UNKNOWN] if item["company"] == UNKNOWN else [item["company"]])]
                scopes = _comparison_scopes(before, after, as_of, funding_source=channel)
                for filters in scopes.values():
                    filters.append({"field": "company", "op": "in", "value": members} if members else
                                   {"field": "amount", "op": "eq", "value": 0})
                # The source endpoint accepts at most 1,000 choices per filter.
                # Never broaden a drill-down when a component exceeds that bound.
                if len(members) > 1000:
                    scopes = None
                    notices.append(f"{channel}: the {component.lower()} evidence scope exceeds 1,000 companies. Inspect the individual company changes instead.")
                result.append(dict(funding_source=channel, component=component, baseline_cycle=_label(before),
                                   current_cycle=_label(after), change_cents=change,
                                   filters=scopes["current"] if scopes else None, comparison_scopes=scopes))
            # Board I1's waterfall and Recipients cell: the same same-stage change
            # regrouped by recipient (committee ID, else the reported name). A
            # recipient is observed in a period when it has any same-stage record
            # there, adjustments included, so the parts reconcile exactly.
            past_recipients = _groups([row for row in group if row["cycle"] == before], "recipient_key")
            current_recipients = _groups([row for row in group if row["cycle"] == after], "recipient_key")
            parts = {component: [0, []] for component in RECIPIENT_COMPONENTS}
            for key in sorted(set(past_recipients) | set(current_recipients)):
                first = _money(past_recipients.get(key, []))["amount_cents"]
                second = _money(current_recipients.get(key, []))["amount_cents"]
                component = (UNATTRIBUTED_RECIPIENTS if key[0] == "name:" + UNKNOWN else
                             RECIPIENT_COMPONENTS[0] if key in past_recipients and key in current_recipients else
                             RECIPIENT_COMPONENTS[1] if key in current_recipients else RECIPIENT_COMPONENTS[2])
                part = parts.setdefault(component, [0, []])
                part[0] += second - first
                part[1].append(past_recipients.get(key, []) + current_recipients.get(key, []))
            for component, (change, groups) in parts.items():
                scopes = _recipient_scopes(before, after, as_of, channel, groups)
                if scopes is None:
                    notices.append(f"{channel}: {component.lower()} cannot open as one evidence scope (more than 1,000 recipients, or committee IDs mixed with name-only recipients). Top recipients and Relationships open individual recipients.")
                recipient_rows.append(dict(funding_source=channel, component=component, baseline_cycle=_label(before),
                                           current_cycle=_label(after), change_cents=change,
                                           recipients=None if component == UNATTRIBUTED_RECIPIENTS else len(groups),
                                           filters=scopes["current"] if scopes else None, comparison_scopes=scopes))
            recipient_sum = sum(change for change, _ in parts.values())
            cards.append(dict(funding_source=channel, baseline_cents=total_before, current_cents=total_after,
                              continuing_recipients=len(parts[RECIPIENT_COMPONENTS[0]][1]),
                              newly_observed_recipients=len(parts[RECIPIENT_COMPONENTS[1]][1]),
                              no_longer_observed_recipients=len(parts[RECIPIENT_COMPONENTS[2]][1]),
                              recipient_component_sum_cents=recipient_sum,
                              recipients_reconciled=recipient_sum == total_after - total_before,
                              change_cents=total_after - total_before, component_sum_cents=sum(components.values()),
                              reconciled=sum(components.values()) == total_after - total_before,
                              baseline_through=_cutoff(before, as_of).isoformat() if as_of else None,
                              current_through=_cutoff(after, as_of).isoformat() if as_of else None,
                              full_observed_baseline_cents=sum(row["amount_cents"] for row in full_rows if row["cycle"] == before and row["funding_source"] == channel),
                              full_observed_current_cents=sum(row["amount_cents"] for row in full_rows if row["cycle"] == after and row["funding_source"] == channel),
                              continuing_baseline_cents=continuing_before, continuing_current_cents=continuing_after,
                              explicitly_covered_baseline_cents=covered_before if any(item["coverage_comparable"] for item in detail if item["funding_source"] == channel) else None,
                              explicitly_covered_current_cents=covered_after if any(item["coverage_comparable"] for item in detail if item["funding_source"] == channel) else None))
    else:
        notices.append("Select two distinct cycles with a baseline preceding the current cycle.")
    detail.sort(key=lambda row: (-abs(row["change_cents"]), row["funding_source"], row["company"]))
    return _panel("What changed", "An exact decomposition of same-stage observed net change between the selected cycles.",
                  _columns(("component", "Component", "text"), ("funding_source", "Channel", "text"),
                           ("baseline_cycle", "Baseline cycle", "text"), ("current_cycle", "Current cycle", "text"),
                           ("change_cents", "Change", "money")), result, notices,
                  cards=cards, company_changes=detail[:MAX_TOP_N], company_changes_total=len(detail),
                  recipient_components=recipient_rows)


def _concentration(rows, **_):
    result, cards = [], []
    for (channel,), group in sorted(_groups(rows, "funding_source").items()):
        total = sum(max(0, row["amount_cents"]) for row in group)
        for dimension, field in (("Company", "company"), ("Recipient", "recipient_key")):
            grouped = _groups(group, field)
            ranked = sorted(((sum(max(0, row["amount_cents"]) for row in members), key[0], members)
                             for key, members in grouped.items()), key=lambda item: (-item[0], item[1]))
            ranked = [item for item in ranked if item[0] > 0]
            cards.append(dict(funding_source=channel, dimension=dimension,
                              positive_cents=total, top_one_share=_ratio(sum(item[0] for item in ranked[:1]), total),
                              top_five_share=_ratio(sum(item[0] for item in ranked[:5]), total),
                              top_ten_share=_ratio(sum(item[0] for item in ranked[:10]), total),
                              hhi=sum((item[0] / total) ** 2 for item in ranked) if total else None,
                              excluding_largest_cents=total - (ranked[0][0] if ranked else 0)))
            cumulative = 0
            for rank, (amount, key, members) in enumerate(ranked, 1):
                cumulative += amount
                company = key if dimension == "Company" else None
                committee = members[0]["committee_id"] if dimension == "Recipient" else None
                result.append(dict(funding_source=channel, dimension=dimension, rank=rank,
                                   name=company or _recipient_name(members), gross_cents=amount,
                                   positive_share=_ratio(amount, total), cumulative_share=_ratio(cumulative, total),
                                   filters=_filters(funding_source=channel, company=company) + (_recipient_filters(members) if dimension == "Recipient" else [])
                                           + [{"field": "amount", "op": "gt", "value": 0}]))
    result.sort(key=lambda row: (row["rank"], row["funding_source"], row["dimension"], row["name"]))
    return _panel("Concentration and breadth", "How positive observed amounts are distributed across companies and recipients.",
                  _columns(("dimension", "Group", "text"), ("name", "Name", "text"), ("funding_source", "Channel", "text"),
                           ("rank", "Rank", "number"), ("gross_cents", "Positive amount", "money"),
                           ("positive_share", "Share", "percent"), ("cumulative_share", "Cumulative share", "percent")), result,
                  ["Concentration uses positive amounts. Negative adjustments are excluded from these denominators and remain in the net views.",
                   "HHI is the sum of squared positive-dollar shares (0 to 1); it describes this selected source scope."], cards=cards)


def _relationships(rows, request, context, as_of, **_):
    before, after = _comparison(rows, request)
    rows = _comparison_rows(rows, before, after, as_of) if before and after else []
    result, cards = [], []
    if before and after and before < after:
        for (channel,), group in sorted(_groups(rows, "funding_source").items()):
            previous, current = set(), set()
            for (company, recipient_key), members in _groups(group, "company", "recipient_key").items():
                if company == UNKNOWN or recipient_key == "name:" + UNKNOWN:
                    continue
                first = _money([row for row in members if row["cycle"] == before])
                second = _money([row for row in members if row["cycle"] == after])
                if not first["records"] and not second["records"]:
                    continue
                key = (company, recipient_key)
                if first["gross_cents"] > 0:
                    previous.add(key)
                if second["gross_cents"] > 0:
                    current.add(key)
                state = "Continuing observed relationship" if first["gross_cents"] > 0 and second["gross_cents"] > 0 else "Newly observed positive activity" if second["gross_cents"] > 0 else "No positive activity observed in current cycle" if first["gross_cents"] > 0 else "Adjustments only"
                committee = members[0]["committee_id"]
                covered = _company_coverage(context, before, company) and _company_coverage(context, after, company)
                scopes = _comparison_scopes(before, after, as_of, company=company, committee_id=committee, funding_source=channel)
                if not committee:
                    for filters in scopes.values():
                        filters.extend(_recipient_filters(members))
                result.append(dict(company=company, committee_id=committee, recipient=_recipient_name(members),
                                   funding_source=channel, baseline_cycle=_label(before), current_cycle=_label(after),
                                   baseline_cents=first["amount_cents"], current_cents=second["amount_cents"],
                                   change_cents=second["amount_cents"] - first["amount_cents"], state=state,
                                   coverage="Company coverage explicitly comparable" if covered else "Coverage comparability unverified",
                                   filters=scopes["current"], comparison_scopes=scopes))
            cards.append(dict(funding_source=channel, prior_relationships=len(previous), current_relationships=len(current),
                              continued_relationships=len(previous & current), observed_retention=_ratio(len(previous & current), len(previous)),
                              newly_observed=len(current - previous), no_longer_observed=len(previous - current)))
    result.sort(key=lambda row: (-abs(row["change_cents"]), row["funding_source"], row["company"], row["recipient"]))
    return _panel("Relationship persistence", "Observed company–recipient relationships across the two selected cycles.",
                  _columns(("company", "Company", "text"), ("recipient", "Recipient", "text"), ("funding_source", "Channel", "text"),
                           ("baseline_cents", "Baseline net", "money"), ("current_cents", "Current net", "money"),
                           ("change_cents", "Change", "money"), ("state", "Observed relationship", "text"),
                           ("coverage", "Coverage", "text")), result,
                  ["Observed retention is a descriptive overlap of positive-activity relationships. Missing activity does not establish a terminated relationship.",
                   "Unattributed companies and unknown recipients do not establish relationships and are excluded from relationship counts.",
                   "Comparison uses matching event-date cutoffs when an as-of date is supplied. Differences in filing coverage can still lower apparent retention."], cards=cards)


def _overlap(rows, **_):
    result, notices = [], ["Portfolio overlap is the sum of minimum positive-dollar shares across recipients (0 to 1). A value of 1 means identical share distributions, independent of total amount.",
                            "Only recipients with resolved committee IDs enter identity-matched overlap. Similar portfolios do not establish coordination or causation."]
    for (channel,), group in sorted(_groups(rows, "funding_source").items()):
        companies = defaultdict(lambda: defaultdict(int))
        for row in group:
            if row["amount_cents"] > 0 and row["company"] != UNKNOWN and row["committee_id"]:
                companies[row["company"]][row["committee_id"]] += row["amount_cents"]
        totals = {company: sum(values.values()) for company, values in companies.items()}
        chosen = sorted(companies, key=lambda company: (-totals[company], company))[:MAX_OVERLAP_COMPANIES]
        if len(companies) > MAX_OVERLAP_COMPANIES:
            notices.append(f"{channel}: pair comparisons are bounded to the {MAX_OVERLAP_COMPANIES} companies with the largest resolved-recipient positive amounts, from {len(companies)} companies.")
        for first, second in combinations(sorted(chosen), 2):
            common = set(companies[first]) & set(companies[second])
            overlap = sum(min(companies[first][key] / totals[first], companies[second][key] / totals[second]) for key in sorted(common))
            result.append(dict(funding_source=channel, company=first, other_company=second,
                               overlap=round(overlap, 8), shared_recipients=len(common),
                               company_positive_cents=totals[first], other_positive_cents=totals[second],
                               filters=[{"field": "company", "op": "in", "value": [first, second]},
                                        {"field": "amount", "op": "gt", "value": 0},
                                        {"field": "committee_id", "op": "ne", "value": ""}] + _filters(funding_source=channel)))
    result.sort(key=lambda row: (-row["overlap"], -row["shared_recipients"], row["funding_source"], row["company"], row["other_company"]))
    return _panel("Company portfolio overlap", "Compare distributions of observed recipient support within each channel.",
                  _columns(("company", "Company", "text"), ("other_company", "Compared company", "text"),
                           ("funding_source", "Channel", "text"), ("overlap", "Portfolio overlap", "percent"),
                           ("shared_recipients", "Shared recipients", "number")), result, notices)


def _timing(rows, **_):
    dated = [dict(row, month=row["temporal_date"].strftime("%Y-%m")) for row in rows if row["temporal_date"]]
    by_month = _groups(dated, "funding_source", "month")
    month_totals = {key: _money(group) for key, group in by_month.items()}
    cycle_totals = {key: sum(max(0, row["amount_cents"]) for row in group)
                    for key, group in _groups(dated, "funding_source", "cycle").items()}
    result, quarters = [], []
    for (channel, month), group in sorted(by_month.items()):
        event_date = group[0]["temporal_date"]
        cycle = event_date.year + event_date.year % 2
        prior = [value["gross_cents"] for (other_channel, other_month), value in month_totals.items()
                 if other_channel == channel and other_month < month and other_month[-2:] == month[-2:]
                 and int(other_month[:4]) % 4 == event_date.year % 4]
        reference = median(prior) if len(prior) >= 2 else None
        result.append(dict(month=month, funding_source=channel, cycle=_label(cycle),
                           quarter=f"{event_date.year}-Q{(event_date.month - 1) // 3 + 1}",
                           phase="Presidential-cycle year" if event_date.year % 4 == 0 else "Midterm-cycle year" if event_date.year % 4 == 2 else "Year before presidential election" if event_date.year % 4 == 3 else "Year before midterm election",
                           cycle_month=event_date.month + (12 if event_date.year % 2 == 0 else 0),
                           **month_totals[(channel, month)], positive_cycle_share=_ratio(month_totals[(channel, month)]["gross_cents"], cycle_totals[(channel, cycle)]),
                           prior_same_phase_months=len(prior), historical_positive_median_cents=reference,
                           relative_to_prior_months=_ratio(month_totals[(channel, month)]["gross_cents"], reference) if reference else None,
                           filters=_filters(funding_source=channel, cycle=_label(cycle)) + [{"field": "date", "op": "gte", "value": month + "-01"}, {"field": "date", "op": "lte", "value": f"{month}-{calendar.monthrange(event_date.year, event_date.month)[1]:02d}"}]))
    for (channel, quarter), group in _groups([dict(row, quarter=f"{row['temporal_date'].year}-Q{(row['temporal_date'].month - 1) // 3 + 1}") for row in dated], "funding_source", "quarter").items():
        quarters.append(dict(funding_source=channel, quarter=quarter, **_money(group)))
    result.sort(key=lambda row: (row["month"], row["funding_source"]), reverse=True)
    quarters.sort(key=lambda row: (row["quarter"], row["funding_source"]))
    return _panel("Timing and spending patterns", "Monthly and quarterly event-date amounts, election phase and historical same-phase context.",
                  _columns(("month", "Month", "text"), ("funding_source", "Channel", "text"),
                           ("amount_cents", "Net amount", "money"), ("positive_cycle_share", "Cycle positive share", "percent"),
                           ("phase", "Election phase", "text"), ("relative_to_prior_months", "Vs. historical matching months", "number")), result,
                  ["Historical month comparisons require at least two earlier observations from the same month and four-year election phase. They are descriptive ratios, not statistical anomaly tests.",
                   "Missing months and invalid dates are not replaced with zeros. Monthly or quarterly observations may be partial."], quarterly_series=quarters)


def _excluded_reason(cycle, complete, bad_cycles, as_of):
    """First rule that keeps an observed cycle out of forecast history; None if it stays eligible."""
    if not cycle:
        return "No valid cycle on the selected records."
    if cycle not in complete:
        return "Not listed as explicitly complete."
    if cycle in bad_cycles:
        return "Records include a missing, invalid or cycle-inconsistent event date."
    if not as_of:
        return "No valid as-of date is set to establish a closed cycle."
    if date(cycle, 12, 31) >= as_of:
        return "Cycle has not closed by the as-of date."
    return None


def _forecast(rows, request, context, as_of, **_):
    result, diagnostics, notices = [], [], ["This is a seasonal-naive historical baseline, not a validated prediction. It repeats the latest explicitly complete cycle from the same four-year election phase.",
                                           "At least four consecutive explicitly complete cycles are required; missing and partial periods are never filled with zero.",
                                           "No calibrated prediction interval is available. Backtests use the current source revision, not reconstructed historical filing vintages."]
    complete = _complete_cycles(context)
    flags = []
    current_cycle = as_of.year + as_of.year % 2 if as_of else None
    try:
        horizon = max(1, min(4, int(request.get("horizon", 2))))
    except (TypeError, ValueError, OverflowError):
        horizon = 2
    for (channel,), group in sorted(_groups(rows, "funding_source").items()):
        cycles = _groups(group, "cycle")
        bad_cycles = {row["cycle"] for row in group if not row["temporal_date"]}
        history = sorted(cycle for (cycle,) in cycles if cycle in complete and cycle not in bad_cycles
                         and cycle and as_of and date(cycle, 12, 31) < as_of)
        # A recent consecutive suffix is usable; older gaps are explicitly disclosed.
        suffix = []
        for cycle in reversed(history):
            if suffix and suffix[-1] - cycle != 2:
                break
            suffix.append(cycle)
        history = list(reversed(suffix))
        # With a completeness record, every observed cycle outside the history is listed once, under the
        # first rule that removed it. Without one, the withheld reason below already says why.
        excluded = defaultdict(list)
        for (cycle,) in sorted(cycles, key=lambda key: key[0] or 0):
            if complete and cycle not in history:
                reason = _excluded_reason(cycle, complete, bad_cycles, as_of) or "Older than a break in consecutive eligible cycles."
                excluded[reason].append(_label(cycle))
        flags += [dict(funding_source=channel, status="excluded", excluded_cycles=labels, reason=reason) for reason, labels in excluded.items()]
        if len(history) < 4:
            reason = ("Need four consecutive explicitly complete cycles with valid event dates." if complete
                      else "No completeness record is connected, so the forecast is withheld.")
            diagnostics.append(dict(funding_source=channel, status="unavailable", training_cycles=[_label(cycle) for cycle in history],
                                    reason=reason, backtest_count=0))
            continue
        totals = {cycle: _money(cycles[(cycle,)])["amount_cents"] for cycle in history}
        backtests = []
        for index in range(4, len(history)):
            target = history[index]
            basis = history[index - 2]
            backtests.append(dict(cycle=_label(target), forecast_basis_cycle=_label(basis),
                                  predicted_cents=totals[basis], observed_cents=totals[target],
                                  error_cents=totals[target] - totals[basis]))
        diagnostic = dict(funding_source=channel, status="ready", training_cycles=[_label(cycle) for cycle in history],
                          backtest_count=len(backtests), backtests=backtests,
                          mae_cents=_rounded_ratio(sum(abs(row["error_cents"]) for row in backtests), len(backtests)) if backtests else None,
                          interval_available=False, validation="Current-revision descriptive backtest" if backtests else "No eligible held-out cycles")
        diagnostics.append(diagnostic)
        # Forecast begins at the actual current cycle, never at a fake historical future.
        for offset in range(horizon):
            target = current_cycle + offset * 2
            basis = next((cycle for cycle in reversed(history) if cycle % 4 == target % 4), None)
            result.append(dict(funding_source=channel, product="Cycle baseline", period=_label(target),
                               amount_cents=totals[basis], forecast_basis_cycle=_label(basis),
                               method="Same-election-phase seasonal naive", backtest_count=len(backtests),
                               filters=_filters(funding_source=channel, cycle=_label(basis))))
            if offset == 0:
                current_baseline = totals[basis]
            if totals[basis] <= 0:
                flags.append(dict(funding_source=channel, status="warning", product="Cycle baseline", period=_label(target),
                                  forecast_basis_cycle=_label(basis), baseline_cents=totals[basis],
                                  warning="Historical basis amount is zero or negative."))
        # A same-stage projection requires explicit current-through coverage in addition
        # to complete history. A maximum observed date is insufficient evidence.
        through = _date(context.get("verified_data_through"))
        current = cycles.get((current_cycle,), [])
        same_phase = [cycle for cycle in history if cycle % 4 == current_cycle % 4]
        fractions, used_cycles = [], []
        for cycle in same_phase:
            full = totals[cycle]
            partial = sum(row["amount_cents"] for row in cycles[(cycle,)] if row["temporal_date"] <= _cutoff(cycle, as_of))
            if full > 0 and 0 < partial <= full:
                fractions.append((partial, full))
                used_cycles.append(cycle)
        observed = _money(current)["amount_cents"]
        if observed > max(current_baseline, 0):
            flags.append(dict(funding_source=channel, status="warning", product="Cycle baseline", period=_label(current_cycle),
                              baseline_cents=current_baseline, observed_cents=observed,
                              warning="Observed amount to date in the current cycle exceeds the baseline."))
        if through and through >= as_of and len(fractions) >= 2 and observed > 0 and current_cycle not in bad_cycles:
            partial_sum, total_sum = map(sum, zip(*fractions))
            result.append(dict(funding_source=channel, product="Current-cycle pace projection", period=_label(current_cycle),
                               amount_cents=_rounded_ratio(observed * total_sum, partial_sum),
                               observed_cents=observed, historical_completion_share=_ratio(partial_sum, total_sum),
                               forecast_basis_cycle=", ".join(_label(cycle) for cycle in used_cycles),
                               method="Pooled same-phase historical completion share", backtest_count=0,
                               filters=_filters(funding_source=channel, cycle=_label(current_cycle))))
        else:
            diagnostic["pace_projection_status"] = "Withheld: needs verified current-through coverage, positive current activity and two usable same-phase completion curves."
        # The next quarter repeats its counterpart four calendar years earlier.
        next_year = as_of.year + (1 if as_of.month >= 10 else 0)
        next_quarter = 1 if as_of.month >= 10 else (as_of.month - 1) // 3 + 2
        basis_year = next_year - 4
        basis_cycle = basis_year + basis_year % 2
        basis_rows = [row for row in group if row["temporal_date"] and row["temporal_date"].year == basis_year
                      and (row["temporal_date"].month - 1) // 3 + 1 == next_quarter]
        if basis_cycle in history and basis_rows:
            first_month = (next_quarter - 1) * 3 + 1
            last_month = first_month + 2
            result.append(dict(funding_source=channel, product="Next-quarter baseline", period=f"{next_year}-Q{next_quarter}",
                               amount_cents=sum(row["amount_cents"] for row in basis_rows),
                               forecast_basis_cycle=f"{basis_year}-Q{next_quarter}",
                               method="Same-quarter four-year seasonal naive", backtest_count=0,
                               filters=_filters(funding_source=channel, cycle=_label(basis_cycle)) +
                                       [{"field": "date", "op": "gte", "value": f"{basis_year}-{first_month:02d}-01"},
                                        {"field": "date", "op": "lte", "value": f"{basis_year}-{last_month:02d}-{calendar.monthrange(basis_year, last_month)[1]:02d}"}]))
            if result[-1]["amount_cents"] <= 0:
                flags.append(dict(funding_source=channel, status="warning", product="Next-quarter baseline", period=f"{next_year}-Q{next_quarter}",
                                  baseline_cents=result[-1]["amount_cents"], warning="Historical basis amount is zero or negative."))
    diagnostics.extend(flags)
    if not complete:
        notices.append("The source has no explicit complete-cycle evidence, so forecasts are withheld.")
    if not as_of:
        notices.append("A valid as-of date is required to establish closed training periods.")
    return _panel("Forecasts and historical baselines", "Cycle and next-quarter baselines with explicit history requirements and error accounting.",
                  _columns(("product", "Product", "text"), ("period", "Period", "text"), ("funding_source", "Channel", "text"),
                           ("amount_cents", "Baseline amount", "money"), ("forecast_basis_cycle", "Historical basis", "text"),
                           ("backtest_count", "Cycle backtests", "number")), result, notices, diagnostics=diagnostics)


def analyze(rows, request=None, context=None):
    """Compute named panels over all scoped facts with bounded presentation tables.

    Required input amount_cents is a signed integer. context.complete_cycles is
    explicit collection evidence; calendar closure or an observed row does not
    create it. as_of filters dated future events; undated facts remain disclosed.
    """
    request, context = request or {}, dict(context or {})
    as_of_value = request.get("as_of") or context.get("as_of")
    as_of = _date(as_of_value)
    if as_of_value and not as_of:
        raise ValueError("as_of must be an ISO calendar date (YYYY-MM-DD).")
    try:
        top_n = max(1, min(MAX_TOP_N, int(request.get("top_n", 25))))
    except (TypeError, ValueError, OverflowError):
        top_n = 25
    chosen = request.get("panels")
    if isinstance(chosen, dict):
        chosen = [name for name, enabled in chosen.items() if enabled]
    if isinstance(chosen, str):
        chosen = [chosen]
    chosen = set(PANEL_ORDER if chosen is None else chosen)
    unknown_panels = chosen - set(PANEL_ORDER)
    if unknown_panels:
        raise ValueError("Unknown analysis panels: " + ", ".join(sorted(unknown_panels)))
    facts, quality, footprint, donor_counts = _normalize(rows, request, as_of, "favorites" in chosen)
    context["_recipient_contributor_counts"] = donor_counts
    after_cutoff = footprint["after_as_of_records"]
    basis = request.get("basis") or context.get("basis") or "selected source basis"
    shared_notices = ["Money channels are calculated separately; totals from different stages must not be added together."]
    if basis == "raw":
        shared_notices.append("Raw observations are source records, not a measure of giving. Duplicates, transfers and adjustments may be present under this basis.")
    if quality["invalid_amount_rows"]:
        shared_notices.append(f"Excluded {quality['invalid_amount_rows']} rows with non-integer amounts; no conversion or rounding was guessed.")
    temporal_excluded = sum(row["record_count"] for row in facts if not row["temporal_date"])
    if temporal_excluded:
        shared_notices.append(f"{temporal_excluded} selected records lack a valid date consistent with their cycle; temporal views exclude them.")
    if after_cutoff:
        shared_notices.append(f"Excluded {after_cutoff} event records after the as-of date.")
    if "favorites" in chosen and not footprint["donor_breadth_available"]:
        shared_notices.append("Exact donor breadth exceeds the in-memory identity budget and is unavailable. All monetary aggregates still use every selected observation.")
    functions = {"favorites": _favorites, "trends": _trends, "changes": _changes,
                 "concentration": _concentration, "relationships": _relationships,
                 "overlap": _overlap, "timing": _timing, "forecast": _forecast}
    panels = {}
    for name in PANEL_ORDER:
        if name in chosen:
            if not footprint["complete_input_scan"]:
                panel = _panel(name.replace("_", " ").title(), "Full-scope analysis exceeds the compact-cell memory budget.", [],
                               notices=[f"The selected scope exceeds {MAX_COMPACT_GROUPS:,} distinct company/recipient/month/channel/sign cells (split at matched cutoffs). Narrow the scope. No partial amounts or sampled metrics are shown."])
            else:
                panel = functions[name](facts, request=request, context=context, as_of=as_of)
            panel["notices"] = shared_notices + panel["notices"]
            panels[name] = _bound(panel, top_n)
    totals = [dict(funding_source=channel, **_money(group))
              for (channel,), group in sorted(_groups(facts, "funding_source").items())] if footprint["complete_input_scan"] else []
    return {"panels": panels, "metadata": dict(**footprint,
            temporal_excluded_records=temporal_excluded, basis=basis,
            as_of=as_of.isoformat() if as_of else None, top_n=top_n, quality=quality, totals_by_channel=totals,
            provenance=context.get("provenance") or {}, point_in_time_vintage_available=False,
            notices=shared_notices)}
