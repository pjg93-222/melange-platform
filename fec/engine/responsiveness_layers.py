#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the display-only credit-union responsiveness surface.

The artifact describes ask take-up and capacity-adjusted CU delivery.  It is a
presentation layer only: no score, tier, rank, or allocation consumes it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys


FEC_BOOTSTRAP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FEC_BOOTSTRAP not in sys.path:
    sys.path.insert(0, FEC_BOOTSTRAP)

from moneyball_core import dump_atomic
from paths import ROOT as MB_ROOT


CONGRESSES = (117, 118, 119)
PILOT_INDUSTRIES = {"credit_union"}
STATUSES = {"active", "unscored", "inactive"}
SCOPE_KEYS = {"chamber", "party", "committee"}
CHAMBERS = {"House", "Senate"}
PARTIES = {"Republican", "Democrat"}
IN_GROUP_DEFINITION = (
    "same chamber + same party + finance-committee "
    "(self included; n = peers with measurable rates)"
)
SERVICE_SOURCE = (
    "votes/HSall_members.csv "
    "(voteview; cosponsor_selectivity-equivalent derivation)"
)
LINEAR_COLUMNS = (
    "intercept",
    "fin_member",
    "fin_lead_grade",
    "fin_mem_grade",
    "agenda_credit",
    "senate",
)
LOG_COLUMNS = (
    "intercept",
    "fin_member",
    "fin_lead_grade",
    "fin_mem_grade",
    "log1p_agenda_credit",
    "senate",
)


def _member_name(row):
    return str(row.get("name") or row.get("member") or "").strip()


def _vehicle_key(row, vehicle_field="vehicle"):
    return (
        str(row.get(vehicle_field) or "").strip(),
        str(row.get("congress") or "").strip(),
    )


def _registry_error(row_number, row, message):
    vehicle, congress = _vehicle_key(row)
    label = "%s|%s" % (vehicle or "?", congress or "?")
    return ValueError(
        "registry row %d (%s): %s" % (row_number, label, message)
    )


def load_tag_keys(path):
    """Return the authoritative beneficiary-tag vehicle universe."""
    keys = set()
    with open(path, encoding="utf-8") as handle:
        for row_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = _vehicle_key(row, "bill")
            if not all(key):
                raise ValueError(
                    "beneficiary tag row %d requires bill and congress"
                    % row_number
                )
            if key in keys:
                raise ValueError(
                    "duplicate beneficiary tag row %d (%s|%s)"
                    % ((row_number,) + key)
                )
            keys.add(key)
    if not keys:
        raise ValueError("beneficiary tag file is empty")
    return keys


def load_registry(path, tag_keys):
    """Load and fully validate the CU ask registry before any computation."""
    rows = []
    seen = set()
    with open(path, encoding="utf-8") as handle:
        for row_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "registry row %d: invalid JSON: %s" % (row_number, exc)
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    "registry row %d: expected a JSON object" % row_number
                )
            vehicle, congress = _vehicle_key(row)
            if not vehicle or not congress:
                raise _registry_error(
                    row_number, row, "vehicle and congress are required"
                )
            if (vehicle, congress) not in tag_keys:
                raise _registry_error(
                    row_number,
                    row,
                    "vehicle+congress is absent from beneficiary tags",
                )
            industry = str(row.get("industry") or "").strip()
            if industry not in PILOT_INDUSTRIES:
                raise _registry_error(
                    row_number,
                    row,
                    "industry %r is outside the credit_union pilot"
                    % industry,
                )
            duplicate_key = (vehicle, congress, industry)
            if duplicate_key in seen:
                raise _registry_error(
                    row_number,
                    row,
                    "duplicate (vehicle, congress, industry)",
                )
            seen.add(duplicate_key)
            status = str(row.get("status") or "").strip()
            if status not in STATUSES:
                raise _registry_error(
                    row_number, row, "invalid status %r" % status
                )
            scope = row.get("scope")
            if not isinstance(scope, dict):
                raise _registry_error(
                    row_number, row, "scope must be an object"
                )
            bad_scope_keys = sorted(set(scope) - SCOPE_KEYS)
            if bad_scope_keys:
                raise _registry_error(
                    row_number,
                    row,
                    "invalid scope key(s) %s" % bad_scope_keys,
                )
            if "chamber" in scope and scope["chamber"] not in CHAMBERS:
                raise _registry_error(
                    row_number,
                    row,
                    "invalid chamber %r" % scope["chamber"],
                )
            if "party" in scope and scope["party"] not in PARTIES:
                raise _registry_error(
                    row_number,
                    row,
                    "invalid party %r" % scope["party"],
                )
            rows.append(row)
    return rows


def registry_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_service(path):
    """Return ``bioguide -> {congress: chamber}`` from Voteview.

    This mirrors ``fec/lobbying/cosponsor_selectivity.py:149-167`` exactly:
    read ``votes/HSall_members.csv`` with ``utf-8-sig`` and replacement,
    strip ``bioguide_id``/``chamber``, require House or Senate, parse the
    Congress as an integer, and retain only Congresses 117-119.
    """
    service = {}
    with open(
        path,
        encoding="utf-8-sig",
        errors="replace",
        newline="",
    ) as handle:
        for row in csv.DictReader(handle):
            bioguide = (row.get("bioguide_id") or "").strip()
            chamber = (row.get("chamber") or "").strip()
            if not bioguide or chamber not in CHAMBERS:
                continue
            try:
                congress = int(row["congress"])
            except (TypeError, ValueError, KeyError):
                continue
            if congress in CONGRESSES:
                service.setdefault(bioguide, {})[congress] = chamber
    return service


def derive_member_service(bundle, service_by_bioguide):
    """Match Voteview Congress-time chamber service by bioguide."""
    out = {}
    for member in bundle.get("members") or []:
        name = _member_name(member)
        bio = member.get("bioguide")
        if not bio and isinstance(member.get("bio"), dict):
            bio = member["bio"].get("bioguide")
        bioguide = str(bio or "").strip()
        out[name] = {
            str(congress): chamber
            for congress, chamber in (
                service_by_bioguide.get(bioguide) or {}
            ).items()
        }
    return out


def _membership_roles(member):
    roles = {}
    for bill in member.get("bills") or []:
        key = _vehicle_key(bill, "bill")
        if not all(key) or key in roles:
            continue
        role = str(bill.get("role") or "").strip() or None
        roles[key] = role
    return roles


def compute_take_up(bundle, registry, member_service):
    """Compute per-member ask eligibility and answered counts."""
    rows = []
    members = sorted(
        bundle.get("members") or [], key=lambda row: _member_name(row)
    )
    for member in members:
        name = _member_name(member)
        chamber = str(member.get("chamber") or "").strip()
        party = str(member.get("party") or "").strip()
        served = {
            str(congress): served_chamber
            for congress, served_chamber in (
                member_service.get(name) or {}
            ).items()
        }
        membership_roles = _membership_roles(member)
        answered_count = 0
        eligible_count = 0
        unscored_count = 0
        vehicles = []
        for ask in registry:
            status = str(ask.get("status") or "").strip()
            if status == "inactive":
                continue
            vehicle, congress = _vehicle_key(ask)
            scope = ask.get("scope") or {}
            key = (vehicle, congress)
            answered = key in membership_roles
            role = membership_roles.get(key)
            is_unscored = status == "unscored" or "committee" in scope
            eligible = False
            if is_unscored:
                unscored_count += 1
            else:
                eligible = True
                served_chamber = served.get(congress)
                if served_chamber is None:
                    eligible = False
                if (
                    scope.get("chamber")
                    and served_chamber != scope["chamber"]
                ):
                    eligible = False
                if scope.get("party") and party != scope["party"]:
                    eligible = False
                if eligible:
                    eligible_count += 1
                    if answered:
                        answered_count += 1
            vehicles.append(
                {
                    "vehicle": vehicle,
                    "congress": congress,
                    "ask_type": ask.get("ask_type"),
                    "scope": scope,
                    "eligible": eligible,
                    "answered": answered,
                    "role": role,
                }
            )
        rows.append(
            {
                "member": name,
                "chamber": chamber,
                "party": party,
                "take_up": {
                    "answered": answered_count,
                    "eligible": eligible_count,
                    "rate": (
                        round(answered_count / eligible_count, 6)
                        if eligible_count
                        else None
                    ),
                    "unscored": unscored_count,
                    "vehicles": vehicles,
                },
            }
        )
    return rows


def _number(value, member, field):
    try:
        return float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "member %s has non-numeric %s %r" % (member, field, value)
        ) from exc


def finance_features(bundle, member_desk_src):
    """Return the exact six capacity-model inputs for each member."""
    source_rows = {}
    for row in member_desk_src.get("members") or []:
        name = _member_name(row)
        if name:
            source_rows[name] = row
    features = {}
    for member in bundle.get("members") or []:
        name = _member_name(member)
        if name not in source_rows:
            raise ValueError("member_desk_src is missing member %s" % name)
        committee = source_rows[name].get("committee") or {}
        if not isinstance(committee, dict):
            raise ValueError("member %s committee must be an object" % name)
        on = committee.get("on") or ""
        if isinstance(on, (list, tuple)):
            on = " ".join(str(value) for value in on)
        on = str(on)
        fin_member = float(
            "Financial Services" in on or "Banking" in on
        )
        features[name] = {
            "fin_member": fin_member,
            "fin_lead_grade": (
                _number(committee.get("lead"), name, "committee.lead")
                if fin_member
                else 0.0
            ),
            "fin_mem_grade": (
                _number(committee.get("mem"), name, "committee.mem")
                if fin_member
                else 0.0
            ),
            "senate": float(member.get("chamber") == "Senate"),
        }
    return features


def ols(y, design):
    """Closed-form normal-equation OLS with partial pivoting."""
    if not y or not design or len(y) != len(design):
        raise ValueError("OLS requires equally sized non-empty y and design")
    width = len(design[0])
    if any(len(row) != width for row in design):
        raise ValueError("OLS design rows have inconsistent widths")
    count = len(y)
    xtx = [
        [
            sum(design[i][a] * design[i][b] for i in range(count))
            for b in range(width)
        ]
        for a in range(width)
    ]
    xty = [
        sum(design[i][a] * y[i] for i in range(count))
        for a in range(width)
    ]
    matrix = [
        row[:] + [xty[index]] for index, row in enumerate(xtx)
    ]
    for column in range(width):
        pivot = max(
            range(column, width),
            key=lambda row: abs(matrix[row][column]),
        )
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        if abs(matrix[column][column]) < 1e-12:
            raise ValueError("capacity OLS normal equations are singular")
        for row in range(width):
            if row == column:
                continue
            factor = matrix[row][column] / matrix[column][column]
            matrix[row] = [
                matrix[row][j] - factor * matrix[column][j]
                for j in range(width + 1)
            ]
    return [
        matrix[column][width] / matrix[column][column]
        for column in range(width)
    ]


def _clean_float(value):
    if abs(value) < 0.0000005:
        value = 0.0
    return round(float(value), 6)


def _midrank_percentiles(values):
    if not values:
        return []
    if len(values) == 1:
        return [0.5]
    order = sorted(range(len(values)), key=lambda index: values[index])
    percentiles = [0.0] * len(values)
    start = 0
    denominator = float(len(values) - 1)
    while start < len(order):
        stop = start
        while (
            stop + 1 < len(order)
            and values[order[stop + 1]] == values[order[start]]
        ):
            stop += 1
        percentile = ((start + stop) / 2.0) / denominator
        for position in range(start, stop + 1):
            percentiles[order[position]] = percentile
        start = stop + 1
    return percentiles


def compute_capacity(bundle, primacy, member_desk_src, features=None):
    """Fit linear/log capacity models and return rows plus receipted betas."""
    members = sorted(
        bundle.get("members") or [], key=lambda row: _member_name(row)
    )
    features = features or finance_features(bundle, member_desk_src)
    primacy_members = primacy.get("members") or {}
    names = []
    actuals = []
    agendas = []
    for member in members:
        name = _member_name(member)
        if name not in primacy_members:
            raise ValueError("primacy_factors is missing member %s" % name)
        primary = primacy_members[name]
        actual = _number(
            (primary.get("mix") or {}).get("cu_primary"),
            name,
            "mix.cu_primary",
        )
        agenda = _number(
            primary.get("agenda_credit"), name, "agenda_credit"
        )
        if agenda < 0:
            raise ValueError("member %s has negative agenda_credit" % name)
        names.append(name)
        actuals.append(actual)
        agendas.append(agenda)
    linear_design = []
    log_design = []
    for index, name in enumerate(names):
        feature = features[name]
        prefix = [
            1.0,
            feature["fin_member"],
            feature["fin_lead_grade"],
            feature["fin_mem_grade"],
        ]
        linear_design.append(
            prefix + [agendas[index], feature["senate"]]
        )
        log_design.append(
            prefix + [math.log1p(agendas[index]), feature["senate"]]
        )
    beta_linear = ols(actuals, linear_design)
    beta_log = ols(actuals, log_design)
    pred_linear = [
        sum(value * beta_linear[j] for j, value in enumerate(row))
        for row in linear_design
    ]
    pred_log = [
        sum(value * beta_log[j] for j, value in enumerate(row))
        for row in log_design
    ]
    resid_linear = [
        actuals[index] - pred_linear[index]
        for index in range(len(names))
    ]
    resid_log = [
        actuals[index] - pred_log[index]
        for index in range(len(names))
    ]
    pctile_linear = _midrank_percentiles(resid_linear)
    pctile_log = _midrank_percentiles(resid_log)
    capacity = {}
    for index, name in enumerate(names):
        capacity[name] = {
            "actual_cu_primary": _clean_float(actuals[index]),
            "pred_linear": _clean_float(pred_linear[index]),
            "resid_linear": _clean_float(resid_linear[index]),
            "pctile_linear": _clean_float(pctile_linear[index]),
            "pred_log": _clean_float(pred_log[index]),
            "resid_log": _clean_float(resid_log[index]),
            "pctile_log": _clean_float(pctile_log[index]),
        }
    betas = {
        "linear": {
            column: _clean_float(beta_linear[index])
            for index, column in enumerate(LINEAR_COLUMNS)
        },
        "log": {
            column: _clean_float(beta_log[index])
            for index, column in enumerate(LOG_COLUMNS)
        },
    }
    return capacity, betas


def build_document(
    bundle,
    registry,
    service_by_bioguide,
    primacy,
    member_desk_src,
    registry_digest,
):
    """Assemble a deterministic, display-only responsiveness document."""
    member_service = derive_member_service(bundle, service_by_bioguide)
    service_unmatched_members = sorted(
        name for name, served in member_service.items() if not served
    )
    take_up_rows = compute_take_up(bundle, registry, member_service)
    features = finance_features(bundle, member_desk_src)
    capacity, betas = compute_capacity(
        bundle, primacy, member_desk_src, features=features
    )
    by_name = {row["member"]: row for row in take_up_rows}
    for row in take_up_rows:
        peers = [
            other
            for other in take_up_rows
            if other["chamber"] == row["chamber"]
            and other["party"] == row["party"]
            and features[other["member"]]["fin_member"] == 1.0
        ]
        rates = [
            by_name[peer["member"]]["take_up"]["rate"]
            for peer in peers
            if by_name[peer["member"]]["take_up"]["rate"] is not None
        ]
        row["in_group"] = {
            "definition": IN_GROUP_DEFINITION,
            "n": len(rates),
            "mean_rate": (
                round(sum(rates) / len(rates), 6) if rates else None
            ),
        }
        row["capacity"] = capacity[row["member"]]
    return {
        "schema": "responsiveness-layers-v1",
        "_doc": (
            "Credit-union ask take-up and capacity context. Display-only: "
            "no score, tier, rank, or allocation consumes this artifact."
        ),
        "members": take_up_rows,
        "receipts": {
            "registry_sha256": registry_digest,
            "registry_rows": len(registry),
            "active_rows": sum(
                1 for row in registry if row.get("status") == "active"
            ),
            "unscored_rows": sum(
                1 for row in registry if row.get("status") == "unscored"
            ),
            "betas": betas,
            "service_source": SERVICE_SOURCE,
            "service_unmatched_members": service_unmatched_members,
            "service_unmatched_count": len(service_unmatched_members),
            "capacity_n": len(capacity),
            "percentile_definition": (
                "midrank residual percentile over all capacity_n members "
                "(roster-wide)"
            ),
            "in_group_definition": IN_GROUP_DEFINITION,
            "display_only": True,
        },
    }


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(root=MB_ROOT, out_path=None):
    """Load inputs, validate the registry first, and atomically write output."""
    path = lambda *parts: os.path.join(os.fspath(root), *parts)
    tag_keys = load_tag_keys(path("bill_beneficiary_tags.ndjson"))
    registry_path = path(
        "data", "lobbying", "industry_ask_registry.ndjson"
    )
    registry = load_registry(registry_path, tag_keys)
    registry_digest = registry_sha256(registry_path)

    # Registry guards above are deliberately complete before computation or
    # before any remaining analytical input is opened.
    bundle = _load_json(path("member_desk_bundle.json"))
    primacy = _load_json(
        path("data", "engine", "primacy_factors.json")
    )
    member_desk_src = _load_json(
        path("data", "members", "member_desk_src.json")
    )
    service = load_service(path("votes", "HSall_members.csv"))
    document = build_document(
        bundle,
        registry,
        service,
        primacy,
        member_desk_src,
        registry_digest,
    )
    destination = out_path or path(
        "data", "engine", "responsiveness_layers.json"
    )
    dump_atomic(
        document,
        os.fspath(destination),
        separators=(",", ":"),
        ensure_ascii=False,
    )
    print(
        "responsiveness_layers: %d members | %d active asks | wrote %s"
        % (
            len(document["members"]),
            document["receipts"]["active_rows"],
            destination,
        )
    )
    return document


def _cli(argv=None):
    parser = argparse.ArgumentParser(
        description="Build display-only CU responsiveness layers."
    )
    parser.add_argument(
        "--out",
        dest="out_path",
        help=(
            "output override; default is "
            "data/engine/responsiveness_layers.json under paths.ROOT"
        ),
    )
    args = parser.parse_args(argv)
    main(MB_ROOT, args.out_path)


if __name__ == "__main__":
    _cli()
