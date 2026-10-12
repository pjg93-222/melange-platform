# -*- coding: utf-8 -*-
"""Build contest-adjusted investment and lobbying efficiency decision layers.

DECISION LAYER LAW: this module reads ratified artifacts, writes exactly one
JSON decision-layer artifact, and moves no rank.  It does not write or alter
``ranks_python.json`` and it is not an allocation-engine consumer.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT  # noqa: E501,E702

import csv
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

from lobbying import org_stance_priors as OSP
from moneyball_core import dump_atomic, pressure_field


SCHEMA = "efficiency-layers-v2"
OUTPUT_PATH = os.path.join(MB_ROOT, "data", "engine", "efficiency_layers.json")
DERIVED = os.path.join(
    os.environ.get("LOCALAPPDATA") or "",
    "Melange", "lda_rebuild", "derived",
)
PANEL_PATH = os.path.join(DERIVED, "member_congress_panel", "panel.csv")
DYAD_PATH = os.path.join(DERIVED, "stance_priors", "dyad_signs.csv")
BOARDS_PATH = os.path.join(DERIVED, "champions_v2", "boards.json")
PRICING_PATH = os.path.join(MB_ROOT, "data", "lobbying", "coalition_pricing.json")
CONGESTION_PATH = os.path.join(MB_ROOT, "data", "engine", "congestion.json")
NON_CU_CONTEST_SECTORS = frozenset(("bank", "crypto", "fintech", "telecom"))


class EfficiencyLayersError(RuntimeError):
    """An input or structural contract failed; publish no partial artifact."""


def _number(value, label, minimum=None):
    if isinstance(value, bool):
        raise EfficiencyLayersError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EfficiencyLayersError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise EfficiencyLayersError(f"{label} must be finite")
    if minimum is not None and number < minimum:
        raise EfficiencyLayersError(f"{label} must be at least {minimum}")
    return number


def _integer(value, label, minimum=None):
    number = _number(value, label, minimum=minimum)
    integer = int(number)
    if number != integer:
        raise EfficiencyLayersError(f"{label} must be an integer")
    return integer


def _round(value):
    return None if value is None else round(float(value), 6)


def population_z(values):
    """Return population z-scores, excluding missing values from the population."""
    raw = list(values)
    present = [
        (index, _number(value, "z-score input"))
        for index, value in enumerate(raw)
        if value is not None
    ]
    numeric = [value for _index, value in present]
    if not numeric:
        return [None for _value in raw]
    mean = sum(numeric) / len(numeric)
    sd = (sum((value - mean) ** 2 for value in numeric) / len(numeric)) ** 0.5
    result = [None for _value in raw]
    if sd == 0.0:
        for index, _value in present:
            result[index] = 0.0
        return result
    for index, value in present:
        result[index] = (value - mean) / sd
    return result


def contest_index(component_z):
    """Average the available, already-normalized contest components."""
    values = [
        _number(value, f"contest component {name}")
        for name, value in component_z.items()
        if value is not None
    ]
    if not values:
        raise EfficiencyLayersError("contest index has no available components")
    return sum(values) / len(values)


def headroom_components(rows):
    """Return headroom and its two pooled component z-score vectors."""
    portfolio_z = population_z([
        math.log1p(_number(row["portfolio_bills"], "portfolio_bills", minimum=0.0))
        for row in rows
    ])
    cu_attention_z = population_z([
        math.log1p(_number(row["att_credit_union"], "att_credit_union", minimum=0.0))
        for row in rows
    ])
    return (
        [portfolio_z[index] - cu_attention_z[index] for index in range(len(rows))],
        portfolio_z,
        cu_attention_z,
    )


def _quantile(values, probability):
    ordered = sorted(_number(value, "quantile input") for value in values)
    if not ordered:
        raise EfficiencyLayersError("cannot take a quantile of an empty vector")
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def rank_lobbying_members(members, limit=None):
    """Rank only eligible non-adversaries by lobbying-efficiency score."""
    ranked = []
    for name, member in members.items():
        lobbying = member.get("lobbying") or {}
        if not lobbying.get("eligible") or lobbying.get("adversary"):
            continue
        if lobbying.get("score") is None:
            continue
        ranked.append({"name": name, **lobbying})
    ranked.sort(key=lambda row: (-row["score"], row["name"].casefold(), row["name"]))
    return ranked if limit is None else ranked[:limit]


def build_payload(
    member_rows,
    pricing_present,
    allocation_field_present=True,
    generated_utc=None,
    source_receipts=None,
):
    """Pure assembly function used by the producer and synthetic tests."""
    rows = sorted(member_rows, key=lambda row: (row["name"].casefold(), row["name"]))
    if not rows:
        raise EfficiencyLayersError("no current members with a 119th-Congress panel row")
    names = [row["name"] for row in rows]
    if len(names) != len(set(names)):
        raise EfficiencyLayersError("member inputs contain duplicate names")

    opposing_attention = [
        _number(row["att_bank"], f"{row['name']} att_bank", minimum=0.0)
        + _number(row["att_crypto"], f"{row['name']} att_crypto", minimum=0.0)
        + _number(row["att_fintech"], f"{row['name']} att_fintech", minimum=0.0)
        + _number(row["att_telecom"], f"{row['name']} att_telecom", minimum=0.0)
        for row in rows
    ]
    cu_pac = [
        _number(row["cu_pac_dollars"], f"{row['name']} CU PAC dollars", minimum=0.0)
        for row in rows
    ]
    opposing_pac = [
        _number(row["opposing_pac_dollars"], f"{row['name']} opposing PAC dollars", minimum=0.0)
        for row in rows
    ]
    signed_opposition = [
        _number(
            row["signed_non_cu_opposition_count"],
            f"{row['name']} signed non-CU opposition count",
            minimum=0.0,
        )
        for row in rows
    ]
    opposing_attention_z = population_z([math.log1p(value) for value in opposing_attention])
    opposing_pac_z = population_z([math.log1p(value) for value in opposing_pac])
    signed_opposition_z = population_z(signed_opposition)

    pricing_z = None
    if pricing_present:
        pricing_raw = []
        for row in rows:
            if row.get("pricing_crowding") is None:
                raise EfficiencyLayersError(
                    f"pricing artifact is present but {row['name']} lacks attention_pct"
                )
            pricing_raw.append(
                _number(row["pricing_crowding"], f"{row['name']} pricing attention_pct")
            )
        pricing_z = population_z(pricing_raw)

    contest_values = []
    contest_components = []
    for index, _row in enumerate(rows):
        components = {
            "opposing_lobby_attention_z": opposing_attention_z[index],
            "opposing_pac_z": opposing_pac_z[index],
            "signed_non_cu_opposition_z": signed_opposition_z[index],
        }
        if pricing_z is not None:
            components["pricing_crowding_z"] = pricing_z[index]
        contest_components.append(components)
        contest_values.append(contest_index(components))

    use_allocation = allocation_field_present and all(
        row.get("engine_allocation_dollars") is not None for row in rows
    ) and any(row.get("engine_allocation_dollars") for row in rows)   # QME 2026-10-01: all-zero over the joined rows is absent too
    allocation_dollars = None
    allocation_shares = None
    if use_allocation:
        allocation_dollars = [
            _number(
                row["engine_allocation_dollars"],
                f"{row['name']} engine allocation dollars",
                minimum=0.0,
            )
            for row in rows
        ]
        total_allocation = sum(allocation_dollars)
        allocation_shares = [
            value / total_allocation if total_allocation else 0.0
            for value in allocation_dollars
        ]
        engine_value_z = population_z(allocation_shares)
        allocation_basis = "dashboard_data.opt.alloc member allocation share"
        allocation_fallback = False
    else:
        engine_value_z = population_z(cu_pac)
        allocation_basis = "dashboard_data.engine.cu PAC dollars"
        allocation_fallback = True

    investment_scores = [
        engine_value_z[index] - contest_values[index] for index in range(len(rows))
    ]
    investment_q25 = _quantile(investment_scores, 0.25)
    investment_q75 = _quantile(investment_scores, 0.75)

    headroom, portfolio_z, cu_attention_z = headroom_components(rows)
    alignment_raw = [
        _number(row.get("cu_composite"), f"{row['name']} CU composite")
        if row.get("cu_board_present") else None
        for row in rows
    ]
    receptivity_raw = [
        _number(row["receptivity"], f"{row['name']} receptivity") for row in rows
    ]
    alignment_z = population_z(alignment_raw)
    receptivity_z = population_z(receptivity_raw)
    lobbying_scores = []
    for index, row in enumerate(rows):
        if row.get("threat_board"):
            lobbying_scores.append(None)
        else:
            available_signals = [
                value
                for value in (receptivity_z[index], alignment_z[index], headroom[index])
                if value is not None
            ]
            signal_mean = sum(available_signals) / len(available_signals)
            lobbying_scores.append(signal_mean - contest_values[index])
    eligible_lobbying_scores = [score for score in lobbying_scores if score is not None]
    lobbying_q25 = _quantile(eligible_lobbying_scores, 0.25)
    lobbying_q75 = _quantile(eligible_lobbying_scores, 0.75)

    members = {}
    for index, row in enumerate(rows):
        investment_score = investment_scores[index]
        investment_label = (
            "UNDERPRICED" if investment_score >= investment_q75 else
            "CONTESTED" if investment_score <= investment_q25 else
            "FAIR"
        )
        adversary = bool(row.get("threat_board"))
        lobbying_score = lobbying_scores[index]
        if adversary:
            lobbying_label = "ADVERSARY"
        elif lobbying_score >= lobbying_q75:
            lobbying_label = "EASY TARGET"
        elif lobbying_score <= lobbying_q25:
            lobbying_label = "CROWDED/HARD"
        else:
            lobbying_label = "NORMAL"

        raw_components = {
            "opposing_lobby_attention": _round(opposing_attention[index]),
            "cu_pac_dollars": _round(cu_pac[index]),
            "opposing_pac_dollars": _round(opposing_pac[index]),
            "signed_non_cu_opposition_count": _round(signed_opposition[index]),
            "portfolio_bills": _round(row["portfolio_bills"]),
            "cu_lobby_attention": _round(row["att_credit_union"]),
            "cu_composite": _round(alignment_raw[index]),
            "receptivity": _round(receptivity_raw[index]),
            "finance_committee": row.get("finance_committee"),
        }
        z_components = {
            **{name: _round(value) for name, value in contest_components[index].items()},
            "engine_value_z": _round(engine_value_z[index]),
            "portfolio_bills_z": _round(portfolio_z[index]),
            "cu_lobby_attention_z": _round(cu_attention_z[index]),
            "headroom_z": _round(headroom[index]),
            "alignment_z": _round(alignment_z[index]),
            "receptivity_z": _round(receptivity_z[index]),
        }
        if pricing_z is not None:
            raw_components["pricing_crowding"] = _round(row["pricing_crowding"])
        if use_allocation:
            raw_components["engine_allocation_dollars"] = _round(
                allocation_dollars[index]
            )
            raw_components["engine_allocation_share"] = _round(
                allocation_shares[index]
            )

        members[row["name"]] = {
            "bioguide": row.get("bioguide") or "",
            "party": row.get("party") or "",
            "chamber": row.get("chamber") or "",
            "contest_index": _round(contest_values[index]),
            "investment": {
                "engine_value_z": _round(engine_value_z[index]),
                "score": _round(investment_score),
                "label": investment_label,
            },
            "lobbying": {
                "eligible": not adversary,
                "adversary": adversary,
                "receptivity_z": _round(receptivity_z[index]),
                "alignment_z": _round(alignment_z[index]),
                "headroom_z": _round(headroom[index]),
                "score": _round(lobbying_score),
                "label": lobbying_label,
            },
            "components": {"raw": raw_components, "z": z_components},
        }

    receipts = {
        "decision_layer_law": (
            "Reads ratified artifacts, writes one JSON, moves no rank, and does not "
            "modify ranks_python.json or the calibrated allocation chain."
        ),
        "moves_no_rank": True,
        "production_engine_changed": False,
        "pac_vs_lda": (
            "dashboard_data.engine.cu and engine.ind are PAC dollar series from the "
            "historical committee list, not lobbying spend. PAC dollars and lobbying "
            "attention are different instruments and are never summed; only their "
            "separately normalized z-scores enter the contest mean."
        ),
        "normalization": {
            "method": "population z-score (ddof=0)",
            "pool": "House and Senate pooled across current members with a Congress 119 panel row",
            "reason": "cross-member decision aid; scores are intentionally cross-chamber comparable",
        },
        "allocation": {
            "basis": allocation_basis,
            "fallback_used": allocation_fallback,
            "fallback_rule": (
                "If no per-member allocation exists (field absent, empty, or all $0), use z(engine.cu PAC dollars) "
                "and state the fallback."
            ),
        },
        "pricing_crowding": {
            "status": "included" if pricing_present else "omitted",
            "component": "coalition_pricing.members.<name>.attention_pct" if pricing_present else None,
            "reason": (
                None if pricing_present else
                "coalition-pricing artifact absent; contest_index averages the three available components"
            ),
            "ratified_constants_recomputed": False,
        },
        "alignment_coverage": {
            "measured": sum(1 for value in alignment_raw if value is not None),
            "unmeasured": sum(1 for value in alignment_raw if value is None),
            "source": "champions_v2 credit-union board composite",
            "unmeasured_rule": (
                "no CU-board composite exists; excluded from the z population and from "
                "the lobbying score mean; displayed as em-dash"
            ),
        },
        "crowding_variant_test": {
            "note": (
                "contest_rate_eb variant tested 2026-08-07; component skew "
                "0.936 vs 0.001; max component z +4.75 vs +1.73 "
                "(single-member dominance: Arrington); contest_index Spearman "
                "0.9976; label churn 18/533 investment + 24/508 lobbying; zero "
                "extreme-to-extreme flips; attention_pct retained for bounded "
                "influence and pool-shape consistency with the log1p'd components."
            ),
            "tested_date": "2026-08-07",
            "variants": ["contest_rate_eb", "attention_pct"],
            "ordinal_content": (
                "attention_pct = pct_rank(contest_rate_eb); the variants carry "
                "identical ordinal information"
            ),
            "component_skew": {
                "contest_rate_eb": 0.936,
                "attention_pct": 0.001,
            },
            "maximum_component_z": {
                "contest_rate_eb": 4.75,
                "attention_pct": 1.73,
                "contest_rate_eb_single_member_dominance": "Arrington",
            },
            "contest_index_spearman": 0.9976,
            "label_churn": {
                "investment": "18/533",
                "lobbying": "24/508",
                "extreme_to_extreme_flips": 0,
            },
            "retained": "attention_pct",
            "retention_reason": (
                "bounded component influence and pool-shape consistency with the "
                "log1p-transformed contest components"
            ),
        },
        "coverage": {
            "members": len(rows),
            "lobbying_eligible": len(eligible_lobbying_scores),
            "threat_board_adversaries_excluded": len(rows) - len(eligible_lobbying_scores),
        },
        "quartiles": {
            "method": "linear interpolation on unrounded scores",
            "investment_q25": _round(investment_q25),
            "investment_q75": _round(investment_q75),
            "lobbying_q25_non_adversaries": _round(lobbying_q25),
            "lobbying_q75_non_adversaries": _round(lobbying_q75),
        },
    }
    if source_receipts:
        for key, value in source_receipts.items():
            if key in receipts:
                raise EfficiencyLayersError(f"source receipt would overwrite {key}")
            receipts[key] = value

    return {
        "schema": SCHEMA,
        "generated_utc": generated_utc or datetime.now(timezone.utc).isoformat(),
        "method": {
            "scope": "descriptive decision aid; moves no rank",
            "contest_index": (
                "mean z[log1p(bank+crypto+fintech+telecom lobbying attention), "
                "log1p(opposing PAC dollars), signed non-CU opposition count, "
                "pricing attention_pct when the ratified artifact is present]"
            ),
            "investment_efficiency": "z(engine allocation share) - contest_index",
            "lobbying_headroom": "z(log1p portfolio_bills) - z(log1p CU lobbying attention)",
            "lobbying_efficiency": (
                "mean of available [receptivity_z, CU-alignment_z, headroom_z] - "
                "contest_index; CU-alignment exists only for CU-board members; "
                "CU threat-board members excluded as adversaries"
            ),
        },
        "receipts": receipts,
        "members": members,
    }


def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise EfficiencyLayersError(f"cannot read {label} {path}: {exc}") from exc


def _vintage(path, document=None):
    if not os.path.exists(path):
        return {"path": path, "exists": False}
    stat = os.stat(path)
    receipt = {
        "path": path,
        "exists": True,
        "bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
    }
    if isinstance(document, dict):
        if document.get("schema") is not None:
            receipt["schema"] = document.get("schema")
        if document.get("generated_utc") is not None:
            receipt["declared_generated_utc"] = document.get("generated_utc")
    return receipt


def _load_dashboard(path):
    document = _load_json(path, "dashboard data")
    names = document.get("name")
    engine = document.get("engine")
    if not isinstance(names, list) or not names or len(names) != len(set(names)):
        raise EfficiencyLayersError("dashboard_data.name must be a nonempty unique list")
    if not isinstance(engine, dict):
        raise EfficiencyLayersError("dashboard_data.engine must be an object")
    cu = engine.get("cu")
    opposing = engine.get("ind")
    if not isinstance(cu, list) or not isinstance(opposing, list):
        raise EfficiencyLayersError("dashboard_data.engine must contain cu and ind arrays")
    if len(cu) != len(names) or len(opposing) != len(names):
        raise EfficiencyLayersError("dashboard PAC arrays do not match dashboard name length")
    party = document.get("party")
    if not isinstance(party, (str, list)) or len(party) != len(names):
        raise EfficiencyLayersError("dashboard_data.party must align one-to-one with names")

    allocation_field_present = False
    allocations = {name: None for name in names}
    opt = document.get("opt")
    # QME 2026-10-01: an EMPTY alloc list (or one that sums to $0) is no allocation at all, not an all-zero real
    # one: it is treated as absent so the existing CU-PAC fallback basis runs and fallback_used is recorded True.
    if isinstance(opt, dict) and isinstance(opt.get("alloc"), list) and opt["alloc"]:
        allocation_field_present = True
        allocations = {name: 0.0 for name in names}
        seen = set()
        for row_number, pair in enumerate(opt["alloc"], start=1):
            if not isinstance(pair, list) or len(pair) != 2:
                raise EfficiencyLayersError(
                    f"dashboard_data.opt.alloc row {row_number} must be [member_index, thousands]"
                )
            index = _integer(pair[0], f"dashboard allocation row {row_number} index", minimum=0)
            if index >= len(names) or index in seen:
                raise EfficiencyLayersError(
                    f"dashboard allocation row {row_number} has invalid/duplicate index {index}"
                )
            seen.add(index)
            allocations[names[index]] = 1000.0 * _number(
                pair[1], f"dashboard allocation row {row_number} thousands", minimum=0.0
            )
        if not any(value for value in allocations.values()):
            allocation_field_present = False
            allocations = {name: None for name in names}

    records = {}
    for index, name in enumerate(names):
        records[name] = {
            "name": name,
            "party": party[index],
            "cu_pac_dollars": _number(cu[index], f"{name} engine.cu", minimum=0.0),
            "opposing_pac_dollars": _number(
                opposing[index], f"{name} engine.ind", minimum=0.0
            ),
            "engine_allocation_dollars": allocations[name],
        }
    return document, records, allocation_field_present


def _load_panel(path):
    required = {
        "bioguide", "congress", "chamber", "party_code", "portfolio_bills",
        "att_bank", "att_credit_union", "att_crypto", "att_fintech",
        "finance_committee",
    }
    records = {}
    total_119 = 0
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise EfficiencyLayersError(f"cannot read member panel {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise EfficiencyLayersError(f"member panel lacks columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            if _integer(row["congress"], f"panel row {row_number} congress") != 119:
                continue
            total_119 += 1
            bioguide = (row.get("bioguide") or "").strip()
            if not bioguide or bioguide in records:
                raise EfficiencyLayersError(
                    f"panel Congress 119 row {row_number} has blank/duplicate bioguide"
                )
            finance = _integer(
                row["finance_committee"],
                f"panel row {row_number} finance_committee",
                minimum=0,
            )
            if finance not in (0, 1):
                raise EfficiencyLayersError("finance_committee must be 0 or 1")
            records[bioguide] = {
                "chamber": (row.get("chamber") or "").strip(),
                "party_code": (row.get("party_code") or "").strip(),
                "portfolio_bills": _number(
                    row["portfolio_bills"], f"panel row {row_number} portfolio_bills", minimum=0.0
                ),
                "att_bank": _number(row["att_bank"], f"panel row {row_number} att_bank", minimum=0.0),
                "att_credit_union": _number(
                    row["att_credit_union"],
                    f"panel row {row_number} att_credit_union",
                    minimum=0.0,
                ),
                "att_crypto": _number(
                    row["att_crypto"], f"panel row {row_number} att_crypto", minimum=0.0
                ),
                "att_fintech": _number(
                    row["att_fintech"], f"panel row {row_number} att_fintech", minimum=0.0
                ),
                # A panel written before telecom existed has no att_telecom column;
                # that is zero telecom attention, not a reason to halt the chain.
                "att_telecom": _number(
                    row.get("att_telecom") or 0, f"panel row {row_number} att_telecom", minimum=0.0
                ),
                "finance_committee": bool(finance),
            }
    return records, total_119


def _load_boards(path):
    document = _load_json(path, "champions v2 boards")
    if document.get("schema") != "champions-v2-v1":
        raise EfficiencyLayersError("champions_v2 boards schema is not champions-v2-v1")
    cu = ((document.get("sectors") or {}).get("credit_union") or {})
    composites = {}
    threat_names = set()
    board_counts = {}
    for board_name in ("champions", "threats", "mixed"):
        rows = cu.get(board_name)
        if not isinstance(rows, list):
            raise EfficiencyLayersError(f"credit_union.{board_name} must be an array")
        board_counts[board_name] = len(rows)
        for row in rows:
            name = row.get("name")
            if not name or name in composites:
                raise EfficiencyLayersError(f"CU boards contain blank/duplicate member {name!r}")
            composites[name] = _number(row.get("composite"), f"{name} CU composite")
            if board_name == "threats":
                threat_names.add(name)
    return document, composites, threat_names, board_counts


def _load_receptivity(path):
    document = _load_json(path, "member receptivity")
    members = document.get("members")
    if not isinstance(members, dict):
        raise EfficiencyLayersError("member_receptivity.members must be an object")
    values = {}
    field = pressure_field()   # R1 (Paul ruling 2026-10-07): the engines' pressure field for the current COMPET_MULT position
    for name, row in members.items():
        if not isinstance(row, dict) or row.get(field) is None:
            raise EfficiencyLayersError(f"{name} lacks headline {field} receptivity")
        values[name] = _number(row[field], f"{name} {field}")
    return document, values


def _load_pricing(path):
    if not os.path.exists(path):
        return None, {}, {}
    document = _load_json(path, "coalition pricing")
    if document.get("schema") != "coalition-pricing-v1":
        raise EfficiencyLayersError("coalition pricing schema is not coalition-pricing-v1")
    members = document.get("members")
    if not isinstance(members, dict):
        raise EfficiencyLayersError("coalition pricing members must be an object")
    crowding = {}
    identity = {}
    for name, row in members.items():
        if not isinstance(row, dict) or row.get("attention_pct") is None:
            raise EfficiencyLayersError(f"coalition pricing member {name} lacks attention_pct")
        bioguide = (row.get("bioguide") or "").strip()
        if not bioguide:
            raise EfficiencyLayersError(f"coalition pricing member {name} lacks bioguide")
        crowding[name] = _number(row["attention_pct"], f"{name} pricing attention_pct")
        identity[name] = bioguide
    return document, crowding, identity


def _load_congestion_identity(path):
    document = _load_json(path, "congestion identity fallback")
    if not isinstance(document, list):
        raise EfficiencyLayersError("congestion identity fallback must be an array")
    identity = {}
    for row in document:
        name = row.get("member")
        bioguide = (row.get("bioguide") or "").strip()
        if not name or not bioguide or name in identity:
            raise EfficiencyLayersError("congestion identity fallback has blank/duplicate member")
        identity[name] = bioguide
    return document, identity


def _load_signed_opposition(derived, dyad_path):
    strict_rows, strict_receipt = OSP.load_strict_universe(derived)
    org_sectors = defaultdict(set)
    for row in strict_rows:
        sectors = {
            token.strip()
            for token in (row.get("org_sector") or "").split("|")
            if token.strip()
        }
        org_sectors[row["lob_id"]].update(sectors & NON_CU_CONTEST_SECTORS)
    contest_orgs = {lob_id for lob_id, sectors in org_sectors.items() if sectors}

    required = {"lob_id", "bioguide_id", "support_n", "oppose_n"}
    opposition = Counter()
    dyad_rows = 0
    matched_rows = 0
    try:
        handle = open(dyad_path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise EfficiencyLayersError(f"cannot read dyad signs {dyad_path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise EfficiencyLayersError(f"dyad signs lacks columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            dyad_rows += 1
            oppose_n = _integer(
                row["oppose_n"], f"dyad row {row_number} oppose_n", minimum=0
            )
            if row.get("lob_id") not in contest_orgs:
                continue
            matched_rows += 1
            bioguide = (row.get("bioguide_id") or "").strip()
            if not bioguide:
                raise EfficiencyLayersError(f"dyad row {row_number} has blank bioguide_id")
            opposition[bioguide] += oppose_n
    receipt = {
        "sectors": sorted(NON_CU_CONTEST_SECTORS),
        "strict_channels": strict_receipt["strict_channels"],
        "strict_input_rows": strict_receipt["input_rows"],
        "strict_rows": strict_receipt["strict_rows"],
        "distinct_strict_org_bill_pairs": strict_receipt["distinct_strict_org_bill_pairs"],
        "non_cu_sector_orgs": len(contest_orgs),
        "dyad_rows": dyad_rows,
        "non_cu_sector_dyad_rows": matched_rows,
        "members_with_positive_opposition": sum(1 for value in opposition.values() if value > 0),
        "opposition_count_sum": sum(opposition.values()),
        "count_rule": (
            "sum dyad_signs.oppose_n once per org-member row when the org has at "
            "least one bank, crypto, fintech, or telecom token in the six annual strict indexes"
        ),
    }
    return opposition, receipt


def build_from_files():
    if not os.environ.get("LOCALAPPDATA"):
        raise EfficiencyLayersError("LOCALAPPDATA is not set")
    dashboard_path = os.path.join(MB_ROOT, "dashboard_data.json")
    receptivity_path = os.path.join(MB_ROOT, "member_receptivity.json")
    dashboard_doc, dashboard, allocation_present = _load_dashboard(dashboard_path)
    panel, panel_119_rows = _load_panel(PANEL_PATH)
    boards_doc, composites, threat_names, board_counts = _load_boards(BOARDS_PATH)
    receptivity_doc, receptivity = _load_receptivity(receptivity_path)
    pricing_doc, pricing_crowding, identity = _load_pricing(PRICING_PATH)

    identity_fallback_doc = None
    missing_identity = sorted(set(dashboard) - set(identity))
    if missing_identity:
        identity_fallback_doc, fallback_identity = _load_congestion_identity(CONGESTION_PATH)
        for name in missing_identity:
            if name in fallback_identity:
                identity[name] = fallback_identity[name]
    still_missing_identity = sorted(set(dashboard) - set(identity))
    if still_missing_identity:
        raise EfficiencyLayersError(
            "cannot join dashboard names to panel bioguides: "
            + ", ".join(still_missing_identity[:10])
        )

    signed_opposition, opposition_receipt = _load_signed_opposition(DERIVED, DYAD_PATH)
    rows = []
    missing_panel = []
    missing_receptivity = []
    missing_pricing = []
    for name, dashboard_row in dashboard.items():
        bioguide = identity[name]
        if bioguide not in panel:
            missing_panel.append(name)
            continue
        if name not in receptivity:
            missing_receptivity.append(name)
            continue
        if pricing_doc is not None and name not in pricing_crowding:
            missing_pricing.append(name)
            continue
        panel_row = panel[bioguide]
        rows.append({
            **dashboard_row,
            **panel_row,
            "bioguide": bioguide,
            "cu_composite": composites.get(name),
            "cu_board_present": name in composites,
            "threat_board": name in threat_names,
            "receptivity": receptivity[name],
            "signed_non_cu_opposition_count": signed_opposition.get(bioguide, 0),
            "pricing_crowding": pricing_crowding.get(name),
        })
    if missing_panel or missing_receptivity or missing_pricing:
        raise EfficiencyLayersError(
            "current-member input coverage failure: panel=%d receptivity=%d pricing=%d"
            % (len(missing_panel), len(missing_receptivity), len(missing_pricing))
        )

    strict_vintages = [
        _vintage(os.path.join(DERIVED, f"our_bill_lobby_index_{year}.csv"))
        for year in OSP.YEARS
    ]
    artifact_vintages = {
        "dashboard_data": _vintage(dashboard_path, dashboard_doc),
        "member_congress_panel": _vintage(PANEL_PATH),
        "dyad_signs": _vintage(DYAD_PATH),
        "strict_bill_lobby_indexes": strict_vintages,
        "champions_v2_boards": _vintage(BOARDS_PATH, boards_doc),
        "member_receptivity": _vintage(receptivity_path, receptivity_doc),
        "coalition_pricing": _vintage(PRICING_PATH, pricing_doc),
    }
    if identity_fallback_doc is not None:
        artifact_vintages["identity_fallback_congestion"] = _vintage(CONGESTION_PATH)

    source_receipts = {
        "artifact_vintages": artifact_vintages,
        "dashboard_schema": {
            "members": len(dashboard),
            "pac_fields": ["engine.cu", "engine.ind"],
            "allocation_field": (
                "opt.alloc[index, thousands]" if allocation_present else None
            ),
            "allocation_quantization": (
                "bundle values are thousands exact to the dollar (a $500 step is 0.5); "
                "dollars reconstructed as value * 1000"
                if allocation_present else None
            ),
            "chamber_field": None,
            "chamber_fallback": "member_congress_panel.chamber",
        },
        "panel_coverage": {
            "congress_119_rows": panel_119_rows,
            "current_dashboard_members_joined": len(rows),
            "noncurrent_panel_rows_excluded": panel_119_rows - len(rows),
        },
        "receptivity_source": {
            "structure": "member_receptivity.members.<name>",
            "headline_scalar": pressure_field(),
            "covered_current_members": len(rows),
        },
        "champions_v2_source": {
            "structure": "sectors.credit_union.{champions,threats,mixed}",
            "board_counts": board_counts,
            "composite_field": "composite",
            # QME 2026-10-01: was "absent_from_all_boards_fill": 0.0, which claimed a 0.0 composite was filled in; the
            # code (alignment_raw -> None, `cu_board_present`) EXCLUDES them from the z population and the lobbying mean.
            "absent_from_all_boards": "excluded",
            "absent_from_all_boards_rule": (
                "members on no champions_v2 board are excluded (no composite, no fill value): left out of the "
                "alignment z population and the lobbying-score mean, displayed as em-dash"
            ),
            "threat_board_members_excluded_from_lobbying_ranking": len(threat_names),
        },
        "signed_opposition_source": opposition_receipt,
        "identity_join": {
            "primary": (
                "coalition_pricing.members.<name>.bioguide"
                if pricing_doc is not None else None
            ),
            "fallback": (
                "data/engine/congestion.json member->bioguide"
                if identity_fallback_doc is not None else None
            ),
            "fallback_members": len(missing_identity),
        },
    }
    return build_payload(
        rows,
        pricing_present=pricing_doc is not None,
        allocation_field_present=allocation_present,
        source_receipts=source_receipts,
    )


def main():
    payload = build_from_files()
    if not os.path.isdir(os.path.dirname(OUTPUT_PATH)):
        raise EfficiencyLayersError(
            f"declared output directory does not exist: {os.path.dirname(OUTPUT_PATH)}"
        )
    dump_atomic(
        payload,
        OUTPUT_PATH,
        ensure_ascii=False,
        sort_keys=True,
        indent=1,
    )
    print(
        "efficiency layers: %d members | %d lobbying adversaries | moves no rank -> %s"
        % (
            len(payload["members"]),
            payload["receipts"]["coverage"]["threat_board_adversaries_excluded"],
            OUTPUT_PATH,
        )
    )
    return 0


if __name__ == "__main__":
    _s.exit(main())
