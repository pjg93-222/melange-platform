"""Pure, auditable five-stage legislative results; candidate policy, never activation.

All inputs are JSON-compatible records from the canonical evidence layer. The
official universe, not CU selections, defines denominators. ``signals`` accept
measure_id, category, standard_direction, tensor_cu, and optional bioguide_id
restricting the accepted selection to that member. Missing signs are unknown.
Confidence is metadata and never a multiplier. No CLI or data is read on import.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
import math
from typing import Any, Iterable, Mapping

STAGES = ("BILL", "AIC", "ABC", "PASS", "LAW")
ROLE_WEIGHTS = {"standard": {"sponsor": 3., "original": 2., "cosponsor": 1.},
                "tensor": {"sponsor": 1., "original": .5, "cosponsor": .25}}
SCHEMA_VERSION = "legislative_effectiveness_v1"
LAWFUL_TYPES = {"hr", "s", "hjres", "sjres"}


@dataclass(frozen=True)
class ScoringPolicy:
    """Versioned illustrative shadow parameters, requiring calibration to adopt."""
    cosponsor_half_life_days: float = 180.
    category_weights: Mapping[str, float] = field(default_factory=dict)
    congress_decay: float = .8
    policy_id: str = "melange_effectiveness_shadow_v1"
    policy_status: str = "shadow_candidate"

    def __post_init__(self):
        if not math.isfinite(self.cosponsor_half_life_days) or self.cosponsor_half_life_days <= 0:
            raise ValueError("cosponsor_half_life_days must be positive and finite")
        if not 0 < self.congress_decay <= 1:
            raise ValueError("congress_decay must be in (0, 1]")
        if any(not math.isfinite(v) or v < 0 for v in self.category_weights.values()):
            raise ValueError("category weights must be finite and nonnegative")


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _unique(rows: Iterable[Mapping], key: str) -> dict:
    result = {}
    for raw in rows:
        row = dict(raw)
        identity = row[key]
        if identity in result and row != result[identity]:
            raise ValueError(f"Conflicting duplicate {key}: {identity}")
        result[identity] = row
    return result


def _bucket() -> dict:
    return {"positive": 0., "negative": 0., "observed_net": 0.,
            "known_count": 0, "unknown_count": 0, "by_role": {}, "_measure_ids": set()}


def _add(bucket: dict, amount: float | None, role: str, measure_id: str | None = None):
    if amount is None:
        bucket["unknown_count"] += 1
        return
    bucket["known_count"] += 1
    if measure_id:
        bucket["_measure_ids"].add(measure_id)
    bucket["positive"] += max(amount, 0.)
    bucket["negative"] += max(-amount, 0.)
    bucket["observed_net"] += amount
    bucket["by_role"][role] = bucket["by_role"].get(role, 0.) + amount


def _finish(bucket: dict, denominators_available: bool = True):
    bucket["measure_ids"] = sorted(bucket.pop("_measure_ids", set()))
    bucket["distinct_measures"] = len(bucket["measure_ids"])
    bucket["available"] = bool(denominators_available and bucket["known_count"]
                               and not bucket["unknown_count"])
    bucket["net"] = bucket["observed_net"] if bucket["available"] else None
    bucket["status"] = ("available_within_accepted_scope" if bucket["available"] else
                        "unavailable_denominator" if not denominators_available else
                        "unknown_signal" if bucket["unknown_count"] else "no_verified_selected_activity")


def _signal_value(rows: list[dict], field_name: str) -> float | None:
    # Missing and contradictory cells are unknown; no assumed opposition/support.
    values = set()
    for row in rows:
        value = row.get(field_name)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric) or not -1 <= numeric <= 1:
            return None
        values.add(numeric)
    return values.pop() if len(values) == 1 else None


def _role_episode(raw: Mapping, bill: Mapping, cutoff: date,
                  policy: ScoringPolicy) -> tuple[dict | None, str | None]:
    role = str(raw.get("role", "")).lower()
    if role not in ROLE_WEIGHTS["standard"]:
        return None, "unknown_role"
    intro = _date(bill.get("introduced_date"))
    joined = _date(raw.get("joined_date"))
    withdrawn = _date(raw.get("withdrawn_date"))
    if intro is None or joined is None:
        return None, "missing_chronology"
    status = str(raw.get("chronology_status", "")).lower()
    if any(token in status for token in ("unknown", "invalid", "inconsistent", "quarantine", "conflict")):
        return None, "unverified_chronology"
    if joined < intro or (withdrawn and withdrawn < joined):
        return None, "inconsistent_chronology"
    if role in {"sponsor", "original"} and joined != intro:
        return None, "inconsistent_original_or_sponsor_date"
    if joined > cutoff:
        return None, "future_join"
    delay = (joined - intro).days
    return {"role": role, "joined": joined, "withdrawn": withdrawn,
            "delay_days": delay,
            "timing_multiplier": 2 ** (-delay / policy.cosponsor_half_life_days)
            if role == "cosponsor" else 1., "evidence": raw.get("evidence", [])}, None


def _episode_at(episodes: list[dict], event_date: date) -> dict | None:
    eligible = [episode for episode in episodes if episode["joined"] <= event_date
                and (episode["withdrawn"] is None or event_date < episode["withdrawn"])]
    if not eligible:
        return None
    # Duplicate/rejoining episodes earn a milestone once. A later rejoin cannot
    # reset introduction-to-first-join delay or override the strongest true role.
    return max(eligible, key=lambda episode: (ROLE_WEIGHTS["standard"][episode["role"]],
                                             episode["timing_multiplier"]))


def _model() -> dict:
    return {"results": _bucket(), "advocacy": _bucket(),
            "categories": {}, "advocacy_categories": {}}


def compute_effectiveness(bills: Iterable[Mapping], roles: Iterable[Mapping],
                          impacts: Iterable[Mapping], roster: Iterable[Mapping],
                          signals: Iterable[Mapping] = (), *, as_of: str,
                          policy: ScoringPolicy | None = None,
                          institutional_context: Iterable[Mapping] = (),
                          include_explanations: bool = True) -> dict:
    """Compute normalized term results and only then a decayed historical view.

    Population = distinct Bioguide members serving in originating chamber/term,
    including replacements. Same-day join qualifies; withdrawal day does not.
    ``reached=False`` is an observed absence, ``reached=None`` is unknown. A
    dated observed/inferred milestone is required; no downward stage inference.
    Positive/negative fields report observed beneficial/harmful magnitudes. A
    nullable net/full reference distinguishes incomplete evidence from zero.
    """
    policy = policy or ScoringPolicy()
    cutoff = _date(as_of)
    if cutoff is None:
        raise ValueError("as_of must be an ISO date")
    bill_map = _unique(bills, "measure_id")
    impact_map = _unique(impacts, "measure_id")
    members, populations = {}, defaultdict(set)
    diagnostics = defaultdict(int)
    for raw in roster:
        row = dict(raw)
        service_start = _date(row.get("service_start") or row.get("start_date"))
        if service_start and service_start > cutoff:
            diagnostics["future_roster_service"] += 1
            continue
        key = (int(row["congress"]), row["chamber"], row["bioguide_id"])
        if key in members:
            continue
        populations[key[:2]].add(key[2])
        members[key] = {"congress": key[0], "chamber": key[1], "bioguide_id": key[2],
                        "name": row.get("name", key[2]),
                        "service_start": service_start.isoformat() if service_start else None,
                        "service_end": row.get("service_end"),
                        "service_periods": list(row.get("service_periods") or []),
                        "sponsor_reference": {"observed_total": 0., "by_stage": dict.fromkeys(STAGES, 0.)},
                        "standard": _model(), "tensor": _model(),
                        "institutional_context": [],
                        "coverage": {"selected_measures": set(), "credited_measures": set(),
                                     "quarantined_roles": 0, "entered_after_introduction": 0}}
    for context in institutional_context:
        for key, member in members.items():
            if (key[2] == context.get("bioguide_id")
                    and (context.get("congress") is None or key[0] == int(context["congress"]))
                    and (context.get("chamber") is None or key[1] == context["chamber"])):
                member["institutional_context"].append(dict(context))

    terms = {}
    for key, population in populations.items():
        terms[key] = {"congress": key[0], "chamber": key[1], "population": len(population),
                      "population_convention": "distinct_bioguide_in_term_including_replacements",
                      "denominators": dict.fromkeys(STAGES, 0.),
                      "coverage": {"eligible_measures": 0, "missing_impact": 0,
                                   "unknown_milestones": dict.fromkeys(STAGES, 0),
                                   "pre_introduction_milestones": dict.fromkeys(STAGES, 0),
                                   "unattributed_stage_credit": dict.fromkeys(STAGES, 0),
                                   "missing_sponsor_attribution": 0}}
    dated_stages, weights, bill_terms = {}, {}, {}
    for measure, bill in bill_map.items():
        intro = _date(bill.get("introduced_date"))
        if intro is None or intro > cutoff:
            diagnostics["missing_or_future_introduction"] += 1
            continue
        key = (int(bill["congress"]), bill["chamber"])
        if key not in terms:
            diagnostics["bills_without_population"] += 1
            continue
        bill_terms[measure] = key
        impact = impact_map.get(measure, {})
        impact_date = _date(impact.get("effective_date"))
        weight = impact.get("weight")
        # Letters' substantive weight is fixed, independent of alignment.
        measure_type = str(bill.get("measure_type", "")).lower()
        if measure_type in {"letter", "congressional_letter"}:
            weight = 5
        if impact_date and impact_date > cutoff:
            weight = None
        if weight not in (1, 5, 10):
            weight = None
        weights[measure] = weight
        eligible = bill.get("eligible") is True and measure_type in LAWFUL_TYPES
        if not eligible:
            dated_stages[measure] = {}
            continue
        term = terms[key]
        term["coverage"]["eligible_measures"] += 1
        if weight is None:
            term["coverage"]["missing_impact"] += 1
        reached = {}
        for stage in STAGES:
            evidence = bill.get("milestones", {}).get(stage, {})
            event_date = _date(evidence.get("date"))
            state = evidence.get("state", "unknown")
            reached_flag = evidence.get("reached", event_date is not None)
            if reached_flag is None or state in {"unknown", "not_applicable"}:
                term["coverage"]["unknown_milestones"][stage] += 1
                continue
            if reached_flag is False:
                continue
            if event_date is None:
                term["coverage"]["unknown_milestones"][stage] += 1
                continue
            if event_date <= cutoff:
                if event_date < intro:
                    term["coverage"]["pre_introduction_milestones"][stage] += 1
                reached[stage] = event_date
                if weight is not None:
                    term["denominators"][stage] += weight
        dated_stages[measure] = reached

    signal_map = defaultdict(list)
    for raw in signals:
        signal = dict(raw)
        signal_date = _date(signal.get("effective_date"))
        if signal_date and signal_date > cutoff:
            diagnostics["future_signals"] += 1
            continue
        signal_map[signal.get("measure_id")].append(signal)

    role_groups = defaultdict(list)
    primary_sponsors = defaultdict(set)
    sponsor_episodes = defaultdict(list)
    raw_role_seen = set()
    for raw in roles:
        measure, member_id = raw.get("measure_id"), raw.get("bioguide_id")
        if measure not in bill_terms or not member_id:
            diagnostics["roles_outside_available_universe"] += 1
            continue
        term_key = bill_terms[measure]
        key = (*term_key, member_id)
        if key not in members:
            diagnostics["unrostered_roles"] += 1
            continue
        episode, reason = _role_episode(raw, bill_map[measure], cutoff, policy)
        if reason:
            diagnostics[reason] += 1
            if reason != "future_join":
                members[key]["coverage"]["quarantined_roles"] += 1
            continue
        identity = (measure, member_id, episode["role"], episode["joined"], episode["withdrawn"])
        if identity in raw_role_seen:
            diagnostics["duplicate_roles"] += 1
            continue
        raw_role_seen.add(identity)
        # Full official sponsors define the reference. Only selected measures
        # need participation episodes materialized a second time in memory.
        if measure in signal_map:
            role_groups[(measure, member_id)].append(episode)
        service_start = _date(members[key].get("service_start"))
        if service_start and service_start > _date(bill_map[measure].get("introduced_date")):
            members[key]["coverage"]["entered_after_introduction"] += 1
        if episode["role"] == "sponsor":
            primary_sponsors[measure].add(member_id)
            sponsor_episodes[(measure, member_id)].append(episode)
    # Fix a late participant's clock across withdrawal/rejoin episodes.
    for episodes in role_groups.values():
        late = [episode for episode in episodes if episode["role"] == "cosponsor"]
        if late:
            first = min(late, key=lambda episode: episode["joined"])
            for episode in late:
                episode["timing_multiplier"] = first["timing_multiplier"]
                episode["delay_days"] = first["delay_days"]

    # Explicit accepted memberships with no usable official role are a coverage
    # gap, including when the same member has other perfectly observed bills.
    missing_scopes = set()
    for measure, accepted_rows in signal_map.items():
        if measure not in bill_terms:
            diagnostics["selected_measure_outside_available_universe"] += 1
            continue
        for signal in accepted_rows:
            identity = signal.get("bioguide_id")
            if identity and (measure, identity) not in role_groups:
                key = (*bill_terms[measure], identity)
                if key in members and (measure, identity) not in missing_scopes:
                    missing_scopes.add((measure, identity))
                    members[key]["coverage"]["selected_measures"].add(measure)
                    for model in ("standard", "tensor"):
                        _add(members[key][model]["results"], None, "unknown")
                        _add(members[key][model]["advocacy"], None, "unknown")
    diagnostics["accepted_selections_without_verified_role"] += len(missing_scopes)

    contributions = []
    for measure, reached in dated_stages.items():
        bill, term_key, weight = bill_map[measure], bill_terms[measure], weights[measure]
        term = terms[term_key]
        if weight is None:
            continue
        if bill.get("eligible") is True and str(bill.get("measure_type", "")).lower() in LAWFUL_TYPES:
            sponsors = primary_sponsors.get(measure, set())
            if len(sponsors) != 1:
                term["coverage"]["missing_sponsor_attribution"] += 1
            else:
                sponsor_id = next(iter(sponsors))
                member = members[(*term_key, sponsor_id)]
                for stage, event_date in reached.items():
                    if not _episode_at(sponsor_episodes[(measure, sponsor_id)], event_date):
                        term["coverage"]["unattributed_stage_credit"][stage] += 1
                        continue
                    unit = term["population"] / 5 * weight / term["denominators"][stage]
                    member["sponsor_reference"]["observed_total"] += unit
                    member["sponsor_reference"]["by_stage"][stage] += unit
                    if include_explanations:
                        contributions.append({"kind": "sponsor_reference", "measure_id": measure,
                                              "bioguide_id": member["bioguide_id"], "stage": stage,
                                              "date": event_date.isoformat(), "weight": weight,
                                              "denominator": term["denominators"][stage], "unit": unit,
                                              "milestone_evidence": bill.get("milestones", {}).get(stage, {}).get("evidence", [])})

    for (measure, member_id), episodes in sorted(role_groups.items()):
        term_key, bill, weight = bill_terms[measure], bill_map[measure], weights[measure]
        member, term = members[(*term_key, member_id)], terms[term_key]
        accepted = [signal for signal in signal_map.get(measure, [])
                    if not signal.get("bioguide_id") or signal["bioguide_id"] == member_id]
        if not accepted:
            continue
        member["coverage"]["selected_measures"].add(measure)
        if weight is None:
            diagnostics["selected_measures_missing_impact"] += 1
            for model in ("standard", "tensor"):
                _add(member[model]["results"], None, "unknown")
                _add(member[model]["advocacy"], None, "unknown")
            continue
        category_rows = defaultdict(list)
        for signal in accepted:
            category_rows[str(signal.get("category") or "uncategorized")].append(signal)
        cells = {category: {model: _signal_value(rows, "standard_direction" if model == "standard" else "tensor_cu")
                           for model in ("standard", "tensor")}
                 for category, rows in category_rows.items()}
        tensor_cell = _signal_value(accepted, "tensor_cu")
        category_count = len(cells)
        accepted_discounts = {float(row["advocacy_discount"]) for row in accepted if row.get("advocacy_discount") is not None}
        if len(accepted_discounts) > 1:
            raise ValueError(f"Conflicting advocacy measure discounts: {measure}")
        advocacy_discount = float(bill.get("advocacy_discount", next(iter(accepted_discounts), 1.)))
        # One advocacy act per member/measure, retained after withdrawal as a
        # historical action. Actual results separately require active episodes.
        advocacy_episode = max(episodes, key=lambda episode: (ROLE_WEIGHTS["standard"][episode["role"]],
                                                               episode["timing_multiplier"]))
        events = [("ADVOCACY", advocacy_episode["joined"], weight / 5., advocacy_episode)]
        for stage, event_date in dated_stages.get(measure, {}).items():
            episode = _episode_at(episodes, event_date)
            if episode:
                events.append((stage, event_date, term["population"] / 5 * weight / term["denominators"][stage], episode))
        for stage, event_date, unit, episode in events:
            is_advocacy = stage == "ADVOCACY"
            bucket_name = "advocacy" if is_advocacy else "results"
            category_name = "advocacy_categories" if is_advocacy else "categories"
            member["coverage"]["credited_measures"].add(measure)
            for model in ("standard", "tensor"):
                base_role = ROLE_WEIGHTS[model][episode["role"]]
                role_credit = base_role * episode["timing_multiplier"]
                # A separately declared advocacy measure convention (e.g.
                # amendment .5) never changes impact or bill-stage results.
                discount = advocacy_discount if is_advocacy else 1.
                if not math.isfinite(discount) or discount < 0:
                    raise ValueError(f"Invalid advocacy discount: {measure}")
                scalar = unit * role_credit * discount
                aggregate_signal = tensor_cell if model == "tensor" else (
                    sum(values[model] * policy.category_weights.get(category, 1.) / category_count
                        for category, values in cells.items())
                    if all(values[model] is not None for values in cells.values()) else None)
                if model == "tensor":
                    _add(member[model][bucket_name], scalar * aggregate_signal if aggregate_signal is not None else None,
                         episode["role"], measure)
                for category, values in cells.items():
                    bucket = member[model][category_name].setdefault(category, _bucket())
                    _add(bucket, scalar * values[model] if values[model] is not None else None, episode["role"], measure)
                    if model == "standard":
                        allocated = scalar * values[model] * policy.category_weights.get(category, 1.) / category_count \
                            if values[model] is not None else None
                        _add(member[model][bucket_name], allocated, episode["role"], measure)
                if include_explanations:
                    contributions.append({"kind": bucket_name, "model": model,
                                          "measure_id": measure, "bioguide_id": member_id,
                                          "congress": term_key[0], "chamber": term_key[1],
                                          "stage": stage, "date": event_date.isoformat(),
                                          "impact_weight": weight, "unit": unit,
                                          "denominator": None if is_advocacy else term["denominators"][stage],
                                          "population": term["population"], "role": episode["role"],
                                          "joined_date": episode["joined"].isoformat(),
                                          "withdrawn_date": episode["withdrawn"].isoformat() if episode["withdrawn"] else None,
                                          "delay_days": episode["delay_days"], "base_role_credit": base_role,
                                          "timing_multiplier": episode["timing_multiplier"],
                                          "advocacy_discount": discount, "signal": aggregate_signal,
                                          "category_signals": cells,
                                          "category_allocation": {category: policy.category_weights.get(category, 1.) / category_count
                                                                  for category in cells} if model == "standard" else None,
                                          "contribution": scalar * aggregate_signal if aggregate_signal is not None else None,
                                          "evidence": episode["evidence"],
                                          "milestone_evidence": bill.get("milestones", {}).get(stage, {}).get("evidence", [])})

    for key, term in terms.items():
        coverage = term["coverage"]
        term["zero_denominator_stages"] = [stage for stage in STAGES if not term["denominators"][stage]]
        term["denominators_available"] = not (term["zero_denominator_stages"] or coverage["missing_impact"]
                                              or any(coverage["unknown_milestones"].values()))
        term["reference_status"] = "available" if (term["denominators_available"]
                                                     and not coverage["missing_sponsor_attribution"]
                                                     and not any(coverage["unattributed_stage_credit"].values())) else "provisional"
        term["sponsor_mean_one_expected"] = term["reference_status"] == "available"
        term["sponsor_observed_mean"] = sum(member["sponsor_reference"]["observed_total"]
                                             for member_key, member in members.items() if member_key[:2] == key) / term["population"]
    for key, member in members.items():
        term = terms[key[:2]]
        reference = member["sponsor_reference"]
        reference["status"] = term["reference_status"]
        reference["total"] = reference["observed_total"] if term["reference_status"] == "available" else None
        reference["unavailable_stages"] = list(term["zero_denominator_stages"])
        for model in ("standard", "tensor"):
            _finish(member[model]["results"], term["denominators_available"])
            _finish(member[model]["advocacy"])
            for bucket in member[model]["categories"].values():
                _finish(bucket, term["denominators_available"])
            for bucket in member[model]["advocacy_categories"].values():
                _finish(bucket)
        for field_name in ("selected_measures", "credited_measures"):
            member["coverage"][field_name] = sorted(member["coverage"][field_name])
        member["coverage"]["signal_scope"] = "accepted_selection_only; unselected_measures_not_assumed_neutral"

    historical = {}
    latest_congress = max((key[0] for key in terms), default=0)
    for key, member in sorted(members.items()):
        history_key = (key[1], key[2])
        historical_row = historical.setdefault(history_key, {"chamber": key[1], "bioguide_id": key[2],
                                                             "name": member["name"], "term_weights": {},
                                                             "standard": _model(), "tensor": _model()})
        decay = policy.congress_decay ** (latest_congress - key[0])
        historical_row["term_weights"][str(key[0])] = decay
        for model in ("standard", "tensor"):
            for track in ("results", "advocacy"):
                source, destination = member[model][track], historical_row[model][track]
                destination["positive"] += source["positive"] * decay
                destination["negative"] += source["negative"] * decay
                destination["observed_net"] += source["observed_net"] * decay
                destination["known_count"] += source["known_count"]
                destination["unknown_count"] += source["unknown_count"] + int(not source["available"])
                destination["_measure_ids"].update(source["measure_ids"])
                for role, value in source["by_role"].items():
                    destination["by_role"][role] = destination["by_role"].get(role, 0.) + value * decay
    for row in historical.values():
        for model in ("standard", "tensor"):
            for track in ("results", "advocacy"):
                _finish(row[model][track])
    return {"schema_version": SCHEMA_VERSION, "as_of": cutoff.isoformat(), "policy": asdict(policy),
            "method": {"stages": list(STAGES), "normalization": "N/5 per originating chamber and Congress",
                       "role_weights": ROLE_WEIGHTS, "same_day_convention": "join inclusive; withdrawal exclusive",
                       "standard_aggregation": "equal bill allocation across accepted categories, priority applied once",
                       "tensor_aggregation": "one accepted CU relevance-times-valence cell per unique bill",
                       "historical_aggregation": "normalize each chamber-term first, then Congress decay",
                       "institutional_power": "actual results retained; context descriptive only"},
            "terms": {f"{key[0]}:{key[1]}": value for key, value in sorted(terms.items())},
            "members": [members[key] for key in sorted(members)],
            "historical_members": [historical[key] for key in sorted(historical)],
            "coverage": dict(sorted(diagnostics.items())), "contributions": contributions}
