# -*- coding: utf-8 -*-
"""One preregistered FEC relational-exposure x LobbyView-attention look.

No-arg mode prints gate status without constructing a pairing.  ``--simulate``
runs the locked synthetic battery only.  ``--look`` is the one real-look path:
gates, coverage check, battery, independent re-derivation, and a stdout-only
RESULT-BRIDGE block.  This script never modifies the locked preregistration.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import random
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


PREREG_PATH = os.path.join(MB_ROOT, "fec_lda_bridge_prereg.md")
NETWORK_SHA256 = "628F82DFA8EFD1010BEA378CF0C91DDF65AA6BC8EBCBF68A21498ADDEAFE7776"
PRIOR_SHA256 = "CE3136A896CFA2E86CF80B68227825F863EAD1C605C6A1C1B189DDAFAA9EDED4"
NETWORK_DATA_ROWS = 10937756
PRIOR_MEMBER_COUNT = 363
NETWORK_HEADER = ["lob_id", "bioguide_id", "year", "n_bills"]
INDUSTRIES = ("credit_union", "fintech", "crypto")
PERMUTATIONS = 5000
PERMUTATION_SEED = 87001
CONSEQUENCE_WORDING = {
    "STRONG": (
        "FEC relational exposure is STRONGLY CORROBORATED (2021–22, "
        "org-exact) as a cross-sectional proxy for LDA lobbying attention."
    ),
    "CORROBORATED": (
        "same candidate permitted; every display label must read "
        '"directionally corroborated (weak)"; the unqualified word '
        '"predictor" is barred.'
    ),
    "UNCORROBORATED": (
        "FEC exposure remains a target-side money prior ONLY and must NOT be "
        "presented as a lobbying-relationship predictor or projection."
    ),
}

CROSSWALK = {
    "credit_union": {
        "BOEING EMPLOYEES' CREDIT UNION": "d039e7e5-f821-55d5-9e38-f93c0b71aecb",
        "NAVY FEDERAL CREDIT UNION": "69c5a9e9-7d79-557e-8ff5-f07df772534d",
        "PENTAGON FEDERAL CREDIT UNION": "c27f7c24-43ec-52f7-80d6-ebb74cc526b1",
    },
    "fintech": {
        "BLOCK, INC.": "0f549ea5-9fdb-5cb9-9a9f-82682d4ef353",
        "SQUARE, INC.": "f2f8dae7-b696-5e01-8067-31b357170ec2",
        "CHIME": "9a33b530-6a10-52df-9d55-a2f4e0168316",
        "DAVE, INC.": "5f438304-3b42-5241-9821-874137fdc0ce",
        "KALSHI, INC.": "18cf487e-9e89-52ec-93dc-d91de45c0b84",
        "KLARNA INC.": "c3dc5b6d-38b1-5331-bec4-6cf081fa6cf1",
        "PLAID INC.": "3f7a52db-9be0-5222-84bd-693a1ccc38ae",
        "ROBINHOOD": "4f43f95f-f6f0-5b47-9f74-08d6b885ac55",
        "SOFI TECHNOLOGIES, INC.": "03b1f6cd-0546-59d2-97d3-9a31da3063b0",
        "STRIPE": "b849b9da-67fa-5eae-914b-92ac41c7ad31",
        "STRIPE, INC.": "cb4ac22a-dd82-5a42-a289-83fd13ba07d0",
    },
    "crypto": {
        "BAKKT HOLDINGS, INC.": "dbdd9f97-b360-53f0-adc8-9a7e5e4c967c",
        "BAM TRADING SERVICES, INC./BINANCE.US": "1c3314d2-e20e-5484-8084-97e58c4eca81",
        "BINANCE HOLDINGS LIMITED": "75d2c2aa-fb94-5005-acff-1479c56d41b5",
        "COINBASE, INC.": "67e35f81-afa7-5258-998e-7f08a3512e20",
        "COINBASE GLOBAL, INC.": "7ddb0913-d306-57d7-b42d-febd24a314cf",
        "FORIS DAX, INC. D/B/A CRYPTO.COM": "6904853b-ba69-5493-85b0-6699c0f7e33b",
        "GEMINI TRUST COMPANY, LLC": "4317e970-8485-55be-af60-d59762a1c8d7",
        "PAYWARD INC D/B/A KRAKEN": "8b517298-a4d4-5e93-bf5a-86af84076c66",
    },
}

EXPECTED_PREFIXES = {
    "BOEING EMPLOYEES' CREDIT UNION": "d039e7e5-f821",
    "NAVY FEDERAL CREDIT UNION": "69c5a9e9-7d79",
    "PENTAGON FEDERAL CREDIT UNION": "c27f7c24-43ec",
    "BLOCK, INC.": "0f549ea5-9fdb",
    "SQUARE, INC.": "f2f8dae7-b696",
    "CHIME": "9a33b530-6a10",
    "DAVE, INC.": "5f438304-3b42",
    "KALSHI, INC.": "18cf487e-9e89",
    "KLARNA INC.": "c3dc5b6d-38b1",
    "PLAID INC.": "3f7a52db-9be0",
    "ROBINHOOD": "4f43f95f-f6f0",
    "SOFI TECHNOLOGIES, INC.": "03b1f6cd-0546",
    "STRIPE": "b849b9da-67fa",
    "STRIPE, INC.": "cb4ac22a-dd82",
    "BAKKT HOLDINGS, INC.": "dbdd9f97-b360",
    "BAM TRADING SERVICES, INC./BINANCE.US": "1c3314d2-e20e",
    "BINANCE HOLDINGS LIMITED": "75d2c2aa-fb94",
    "COINBASE, INC.": "67e35f81-afa7",
    "COINBASE GLOBAL, INC.": "7ddb0913-d306",
    "FORIS DAX, INC. D/B/A CRYPTO.COM": "6904853b-ba69",
    "GEMINI TRUST COMPANY, LLC": "4317e970-8485",
    "PAYWARD INC D/B/A KRAKEN": "8b517298-a4d4",
}


class IngestError(Exception):
    """Expected gate or input failure that must exit 2 without a look."""


class DefectError(Exception):
    """Independent implementations disagreed; nothing may be recorded."""


def _input_paths():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise IngestError("LOOK REFUSED (not consumed): LOCALAPPDATA is unset")
    network_path = os.path.join(
        local_app_data, "Melange", "lobbyview", "raw", "network.csv"
    )
    prior_path = os.path.join(
        local_app_data,
        "Melange",
        "fec_spending",
        "derived",
        "fec_opposition_prior.json",
    )
    return network_path, prior_path


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _verify_crosswalk():
    names = set()
    lob_ids = set()
    for industry in INDUSTRIES:
        for client_name, lob_id in CROSSWALK[industry].items():
            if client_name in names or lob_id in lob_ids:
                raise DefectError("duplicate name or lob_id in hardcoded crosswalk")
            names.add(client_name)
            lob_ids.add(lob_id)
            if lob_id[:13] != EXPECTED_PREFIXES.get(client_name):
                raise DefectError(
                    "hardcoded crosswalk prefix mismatch for " + client_name
                )
    if len(names) != 22 or names != set(EXPECTED_PREFIXES):
        raise DefectError("hardcoded crosswalk is not the locked 22-lob_id set")


def _read_prereg():
    if not os.path.isfile(PREREG_PATH):
        raise IngestError(
            "LOOK REFUSED (not consumed): prereg absent at " + PREREG_PATH
        )
    with open(PREREG_PATH, "r", encoding="utf-8") as handle:
        text = handle.read()
    if "STATUS: LOCKED" not in text:
        raise IngestError(
            "LOOK REFUSED (not consumed): STATUS: LOCKED substring absent"
        )
    if re.search(r"\n## RESULT-BRIDGE", text):
        raise IngestError(
            "LOOK REFUSED (not consumed): RESULT-BRIDGE already recorded"
        )
    return text


def _status():
    present = os.path.isfile(PREREG_PATH)
    text = ""
    if present:
        try:
            with open(PREREG_PATH, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            text = ""
    locked = "STATUS: LOCKED" in text
    consumed = bool(re.search(r"\n## RESULT-BRIDGE", text))
    fingerprints_match = False
    network_path = ""
    prior_path = ""
    try:
        network_path, prior_path = _input_paths()
        fingerprints_match = (
            os.path.isfile(network_path)
            and os.path.isfile(prior_path)
            and _sha256(network_path) == NETWORK_SHA256
            and _sha256(prior_path) == PRIOR_SHA256
        )
    except (IngestError, OSError):
        fingerprints_match = False
    print("FEC x LDA bridge preregistered look status")
    print("  prereg present: " + ("YES" if present else "NO"))
    print("  prereg locked: " + ("LOCKED" if locked else "NOT LOCKED"))
    print("  RESULT-BRIDGE consumed: " + ("YES" if consumed else "NO"))
    print("  input fingerprints: " + ("MATCH" if fingerprints_match else "DRIFT"))
    if network_path:
        print("  network: " + network_path)
    if prior_path:
        print("  prior: " + prior_path)
    return 0


def _load_prior(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): prior JSON unreadable: " + str(exc)
        )
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, dict):
        raise IngestError(
            "LOOK REFUSED (not consumed): prior members map absent"
        )
    return payload, members


def _stream_network(path, collect_real_y):
    lob_to_industry = {}
    for industry in INDUSTRIES:
        for lob_id in CROSSWALK[industry].values():
            lob_to_industry[lob_id] = industry

    rows_read = 0
    total_attention = {}
    active_lob_ids = {industry: set() for industry in INDUSTRIES}
    pinned_attention = {industry: {} for industry in INDUSTRIES}
    pinned_by_year = {
        industry: {2021: {}, 2022: {}} for industry in INDUSTRIES
    }

    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header != NETWORK_HEADER:
                raise IngestError(
                    "LOOK REFUSED (not consumed): network header mismatch"
                )
            for source in reader:
                rows_read += 1
                if len(source) != len(NETWORK_HEADER):
                    raise IngestError(
                        "LOOK REFUSED (not consumed): network data row "
                        + str(rows_read)
                        + " has wrong field count"
                    )
                year_text = source[2].strip()
                if year_text not in {"2021", "2022"}:
                    continue
                try:
                    n_bills = int(source[3])
                except ValueError:
                    raise IngestError(
                        "LOOK REFUSED (not consumed): invalid n_bills at network data row "
                        + str(rows_read)
                    )
                bioguide = source[1].strip()
                lob_id = source[0].strip()
                total_attention[bioguide] = (
                    total_attention.get(bioguide, 0) + n_bills
                )
                industry = lob_to_industry.get(lob_id)
                if industry is None:
                    continue
                active_lob_ids[industry].add(lob_id)
                if not collect_real_y:
                    continue
                industry_values = pinned_attention[industry]
                industry_values[bioguide] = (
                    industry_values.get(bioguide, 0) + n_bills
                )
                year_values = pinned_by_year[industry][int(year_text)]
                year_values[bioguide] = year_values.get(bioguide, 0) + n_bills
    except OSError as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): network unreadable: " + str(exc)
        )

    return {
        "rows_read": rows_read,
        "total_attention": total_attention,
        "active_lob_ids": active_lob_ids,
        "pinned_attention": pinned_attention,
        "pinned_by_year": pinned_by_year,
    }


def _gated_sources(collect_real_y):
    _verify_crosswalk()
    _read_prereg()
    network_path, prior_path = _input_paths()
    if not os.path.isfile(network_path):
        raise IngestError(
            "LOOK REFUSED (not consumed): network.csv absent at " + network_path
        )
    if not os.path.isfile(prior_path):
        raise IngestError(
            "LOOK REFUSED (not consumed): fec_opposition_prior.json absent at "
            + prior_path
        )
    if _sha256(network_path) != NETWORK_SHA256:
        raise IngestError(
            "LOOK REFUSED (not consumed): network.csv SHA-256 drift"
        )
    if _sha256(prior_path) != PRIOR_SHA256:
        raise IngestError(
            "LOOK REFUSED (not consumed): fec_opposition_prior.json SHA-256 drift"
        )

    prior, members = _load_prior(prior_path)
    network = _stream_network(network_path, collect_real_y)
    if (
        _sha256(network_path) != NETWORK_SHA256
        or _sha256(prior_path) != PRIOR_SHA256
    ):
        raise IngestError(
            "input drift between hash and read; look NOT consumed"
        )
    if network["rows_read"] != NETWORK_DATA_ROWS:
        raise IngestError(
            "LOOK REFUSED (not consumed): network.csv data-row count drift "
            f"({network['rows_read']} != {NETWORK_DATA_ROWS})"
        )
    if len(members) != PRIOR_MEMBER_COUNT:
        raise IngestError(
            "LOOK REFUSED (not consumed): prior member count drift "
            f"({len(members)} != {PRIOR_MEMBER_COUNT})"
        )
    return prior, members, network


def _member_x(member, industry):
    try:
        value = member["associated_by_industry"][industry]["2021-2022"][
            "assoc_relational_used"
        ]
        number = float(value)
    except (KeyError, TypeError, ValueError):
        raise IngestError(
            "prior member has invalid assoc_relational_used for " + industry
        )
    if not math.isfinite(number):
        raise IngestError(
            "prior member has non-finite assoc_relational_used for " + industry
        )
    return number


def _build_records(members, network, entering, include_real_y):
    primary = []
    sensitivity = []
    for industry in entering:
        for bioguide in sorted(members):
            member = members[bioguide]
            if not member.get("in_roster"):
                continue
            x_value = _member_x(member, industry)
            if include_real_y:
                y_value = float(
                    network["pinned_attention"][industry].get(bioguide, 0)
                )
                y_2021 = float(
                    network["pinned_by_year"][industry][2021].get(bioguide, 0)
                )
                y_2022 = float(
                    network["pinned_by_year"][industry][2022].get(bioguide, 0)
                )
            else:
                y_value = 0.0
                y_2021 = 0.0
                y_2022 = 0.0
            record = {
                "industry": industry,
                "member": bioguide,
                "x": x_value,
                "y": y_value,
                "control": float(
                    network["total_attention"].get(bioguide, 0)
                ),
                "y_2021": y_2021,
                "y_2022": y_2022,
            }
            sensitivity.append(record)
            if x_value > 0:
                primary.append(record)
    return primary, sensitivity


def statistics_implementation_one(records, sensitivity_records=None):
    """First complete implementation; all numerical helpers are local."""

    def average_ranks(values):
        order = sorted(range(len(values)), key=lambda index: values[index])
        ranks = [0.0] * len(values)
        start = 0
        while start < len(order):
            stop = start + 1
            while (
                stop < len(order)
                and values[order[stop]] == values[order[start]]
            ):
                stop += 1
            rank = ((start + 1) + stop) / 2.0
            for position in range(start, stop):
                ranks[order[position]] = rank
            start = stop
        return ranks

    def pearson(left, right):
        if len(left) != len(right) or not left:
            raise IngestError("statistic received empty or unequal vectors")
        left_mean = sum(left) / len(left)
        right_mean = sum(right) / len(right)
        cross = sum(
            (a - left_mean) * (b - right_mean)
            for a, b in zip(left, right)
        )
        left_ss = sum((a - left_mean) ** 2 for a in left)
        right_ss = sum((b - right_mean) ** 2 for b in right)
        if left_ss == 0.0 or right_ss == 0.0:
            return 0.0
        return cross / math.sqrt(left_ss * right_ss)

    def residual(values, control):
        values_mean = sum(values) / len(values)
        control_mean = sum(control) / len(control)
        denominator = sum((item - control_mean) ** 2 for item in control)
        if denominator == 0.0:
            slope = 0.0
        else:
            slope = sum(
                (item - control_mean) * (value - values_mean)
                for item, value in zip(control, values)
            ) / denominator
        intercept = values_mean - slope * control_mean
        return [
            value - intercept - slope * item
            for value, item in zip(values, control)
        ]

    def ranked_groups(source_records, y_field):
        grouped = {}
        for record in source_records:
            grouped.setdefault(record["industry"], []).append(record)
        output = {}
        for industry in sorted(grouped):
            rows = sorted(grouped[industry], key=lambda row: row["member"])
            output[industry] = {
                "ids": [industry + ":" + row["member"] for row in rows],
                "x": average_ranks([row["x"] for row in rows]),
                "y": average_ranks([row[y_field] for row in rows]),
                "control": average_ranks(
                    [row["control"] for row in rows]
                ),
            }
        return output

    def pooled_rho(source_records, y_field):
        groups = ranked_groups(source_records, y_field)
        pooled_x = []
        pooled_y = []
        for industry in sorted(groups):
            pooled_x.extend(groups[industry]["x"])
            pooled_y.extend(groups[industry]["y"])
        return pearson(pooled_x, pooled_y)

    groups = ranked_groups(records, "y")
    pooled_x = []
    pooled_y = []
    pooled_control = []
    pooled_x2 = []
    pooled_y2 = []
    for industry in sorted(groups):
        pooled_x.extend(groups[industry]["x"])
        pooled_y.extend(groups[industry]["y"])
        pooled_control.extend(groups[industry]["control"])
        pooled_x2.extend(
            int(round(2 * value)) for value in groups[industry]["x"]
        )
        pooled_y2.extend(
            int(round(2 * value)) for value in groups[industry]["y"]
        )

    observed = pearson(pooled_x, pooled_y)
    observed_dot = sum(
        x_value * y_value for x_value, y_value in zip(pooled_x2, pooled_y2)
    )
    rng = random.Random(PERMUTATION_SEED)
    exceed = 0
    for _ in range(PERMUTATIONS):
        permuted_dot = 0
        offset = 0
        for industry in sorted(groups):
            values = [
                int(round(2 * value)) for value in groups[industry]["y"]
            ]
            rng.shuffle(values)
            for position, y_value in enumerate(values):
                permuted_dot += pooled_x2[offset + position] * y_value
            offset += len(values)
        if permuted_dot >= observed_dot:
            exceed += 1
    permutation_p = (exceed + 1) / (PERMUTATIONS + 1)
    partial = pearson(
        residual(pooled_x, pooled_control),
        residual(pooled_y, pooled_control),
    )
    result = {
        "n": len(records),
        "rho": observed,
        "p": permutation_p,
        "partial": partial,
    }

    if sensitivity_records is None:
        return result

    per_industry = {}
    for industry in sorted(groups):
        group = groups[industry]
        industry_rho = pearson(group["x"], group["y"])
        industry_x2 = [int(round(2 * value)) for value in group["x"]]
        industry_y2 = [int(round(2 * value)) for value in group["y"]]
        industry_observed_dot = sum(
            x_value * y_value
            for x_value, y_value in zip(industry_x2, industry_y2)
        )
        industry_rng = random.Random(PERMUTATION_SEED)
        industry_exceed = 0
        for _ in range(PERMUTATIONS):
            values = list(industry_y2)
            industry_rng.shuffle(values)
            industry_permuted_dot = sum(
                industry_x2[index] * values[index]
                for index in range(len(values))
            )
            if industry_permuted_dot >= industry_observed_dot:
                industry_exceed += 1
        per_industry[industry] = {
            "n": len(group["x"]),
            "rho": industry_rho,
            "p": (industry_exceed + 1) / (PERMUTATIONS + 1),
        }

    ranked_pairs = []
    for industry in sorted(groups):
        group = groups[industry]
        ranked_pairs.extend(
            zip(group["ids"], group["x"], group["y"])
        )
    top_x = {
        item[0]
        for item in sorted(ranked_pairs, key=lambda item: (-item[1], item[0]))[
            :25
        ]
    }
    top_y = {
        item[0]
        for item in sorted(ranked_pairs, key=lambda item: (-item[2], item[0]))[
            :25
        ]
    }
    union = top_x | top_y
    result.update(
        {
            "per_industry": per_industry,
            "top25_jaccard": len(top_x & top_y) / len(union) if union else 0.0,
            "x0_inclusive_rho": pooled_rho(sensitivity_records, "y"),
            "rho_2021": pooled_rho(records, "y_2021"),
            "rho_2022": pooled_rho(records, "y_2022"),
        }
    )
    return result


def statistics_implementation_two(records, sensitivity_records=None):
    """Independent re-derivation; no numerical helper is shared with impl one."""

    def rank_by_counts(values):
        counts = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        rank_for_value = {}
        below = 0
        for value in sorted(counts):
            count = counts[value]
            rank_for_value[value] = below + (count + 1) / 2.0
            below += count
        return [rank_for_value[value] for value in values]

    def product_moment(left, right):
        if len(left) != len(right) or not left:
            raise IngestError("re-derivation received empty or unequal vectors")
        count = len(left)
        sum_left = sum(left)
        sum_right = sum(right)
        numerator = count * sum(
            a * b for a, b in zip(left, right)
        ) - sum_left * sum_right
        left_term = count * sum(a * a for a in left) - sum_left * sum_left
        right_term = (
            count * sum(b * b for b in right) - sum_right * sum_right
        )
        if left_term == 0.0 or right_term == 0.0:
            return 0.0
        return numerator / math.sqrt(left_term * right_term)

    def ols_residuals(values, control):
        count = len(values)
        sum_control = sum(control)
        sum_values = sum(values)
        denominator = count * sum(item * item for item in control) - (
            sum_control * sum_control
        )
        if denominator == 0.0:
            slope = 0.0
        else:
            slope = (
                count
                * sum(
                    item * value for item, value in zip(control, values)
                )
                - sum_control * sum_values
            ) / denominator
        intercept = (sum_values - slope * sum_control) / count
        return [
            value - (intercept + slope * item)
            for value, item in zip(values, control)
        ]

    def make_strata(source_records, y_field):
        industries = sorted(
            {record["industry"] for record in source_records}
        )
        strata = {}
        for industry in industries:
            rows = sorted(
                (
                    record
                    for record in source_records
                    if record["industry"] == industry
                ),
                key=lambda row: row["member"],
            )
            strata[industry] = {
                "ids": [industry + ":" + row["member"] for row in rows],
                "x": rank_by_counts([row["x"] for row in rows]),
                "y": rank_by_counts([row[y_field] for row in rows]),
                "control": rank_by_counts(
                    [row["control"] for row in rows]
                ),
            }
        return strata

    def derive_pooled(source_records, y_field):
        strata = make_strata(source_records, y_field)
        left = []
        right = []
        for industry in sorted(strata):
            left += strata[industry]["x"]
            right += strata[industry]["y"]
        return product_moment(left, right)

    strata = make_strata(records, "y")
    x_values = []
    y_values = []
    control_values = []
    x_values2 = []
    y_values2 = []
    for industry in sorted(strata):
        x_values += strata[industry]["x"]
        y_values += strata[industry]["y"]
        control_values += strata[industry]["control"]
        x_values2 += [
            int(round(2 * value)) for value in strata[industry]["x"]
        ]
        y_values2 += [
            int(round(2 * value)) for value in strata[industry]["y"]
        ]

    rho = product_moment(x_values, y_values)
    observed_integer_product = 0
    for index in range(len(x_values2)):
        observed_integer_product += x_values2[index] * y_values2[index]
    generator = random.Random(PERMUTATION_SEED)
    at_least_observed = 0
    for _ in range(PERMUTATIONS):
        shuffled_integer_product = 0
        offset = 0
        for industry in sorted(strata):
            one_shuffle = [
                int(round(2 * value)) for value in strata[industry]["y"]
            ]
            generator.shuffle(one_shuffle)
            for position in range(len(one_shuffle)):
                shuffled_integer_product += (
                    x_values2[offset + position] * one_shuffle[position]
                )
            offset += len(one_shuffle)
        if shuffled_integer_product >= observed_integer_product:
            at_least_observed += 1
    p_value = (at_least_observed + 1.0) / (PERMUTATIONS + 1.0)
    x_residual = ols_residuals(x_values, control_values)
    y_residual = ols_residuals(y_values, control_values)
    output = {
        "n": len(records),
        "rho": rho,
        "p": p_value,
        "partial": product_moment(x_residual, y_residual),
    }

    if sensitivity_records is None:
        return output

    industry_results = {}
    for industry in sorted(strata):
        stratum = strata[industry]
        one_rho = product_moment(stratum["x"], stratum["y"])
        one_x2 = [int(round(2 * value)) for value in stratum["x"]]
        one_y2 = [int(round(2 * value)) for value in stratum["y"]]
        one_observed_product = 0
        for index in range(len(one_x2)):
            one_observed_product += one_x2[index] * one_y2[index]
        one_generator = random.Random(PERMUTATION_SEED)
        one_count = 0
        for _ in range(PERMUTATIONS):
            one_y = list(one_y2)
            one_generator.shuffle(one_y)
            one_permuted_product = 0
            for index in range(len(one_y)):
                one_permuted_product += one_x2[index] * one_y[index]
            if one_permuted_product >= one_observed_product:
                one_count += 1
        industry_results[industry] = {
            "n": len(stratum["x"]),
            "rho": one_rho,
            "p": (one_count + 1.0) / (PERMUTATIONS + 1.0),
        }

    all_pairs = []
    for industry in sorted(strata):
        stratum = strata[industry]
        for index, identifier in enumerate(stratum["ids"]):
            all_pairs.append(
                (identifier, stratum["x"][index], stratum["y"][index])
            )
    x_order = sorted(all_pairs, key=lambda item: (0.0 - item[1], item[0]))
    y_order = sorted(all_pairs, key=lambda item: (0.0 - item[2], item[0]))
    x_top = {item[0] for item in x_order[0:25]}
    y_top = {item[0] for item in y_order[0:25]}
    combined = x_top.union(y_top)
    output.update(
        {
            "per_industry": industry_results,
            "top25_jaccard": (
                len(x_top.intersection(y_top)) / len(combined)
                if combined
                else 0.0
            ),
            "x0_inclusive_rho": derive_pooled(sensitivity_records, "y"),
            "rho_2021": derive_pooled(records, "y_2021"),
            "rho_2022": derive_pooled(records, "y_2022"),
        }
    )
    return output


def _assert_agreement(first, second, label):
    def compare(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            if set(left) != set(right):
                raise DefectError(path + " key mismatch")
            for key in sorted(left):
                compare(left[key], right[key], path + "." + str(key))
            return
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if not math.isfinite(float(left)) or not math.isfinite(float(right)):
                raise DefectError(path + " non-finite value")
            if abs(float(left) - float(right)) > 1e-9:
                raise DefectError(
                    path
                    + " differs: "
                    + repr(left)
                    + " vs "
                    + repr(right)
                )
            return
        if left != right:
            raise DefectError(path + " differs")

    compare(first, second, label)


def _battery_rank_z(records):
    def average_ranks(values):
        ordered = sorted(range(len(values)), key=lambda index: values[index])
        ranks = [0.0] * len(values)
        first = 0
        while first < len(ordered):
            last = first + 1
            while (
                last < len(ordered)
                and values[ordered[last]] == values[ordered[first]]
            ):
                last += 1
            tied_rank = ((first + 1) + last) / 2.0
            for position in range(first, last):
                ranks[ordered[position]] = tied_rank
            first = last
        return ranks

    def standardize(values):
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        if variance == 0.0:
            raise IngestError("battery rank-z vector has zero variance")
        scale = math.sqrt(variance)
        return [(value - mean) / scale for value in values]

    by_industry = {}
    for record in records:
        by_industry.setdefault(record["industry"], []).append(record)
    ordered_records = []
    z_x = []
    z_control = []
    for industry in sorted(by_industry):
        rows = sorted(by_industry[industry], key=lambda row: row["member"])
        ordered_records.extend(rows)
        z_x.extend(standardize(average_ranks([row["x"] for row in rows])))
        z_control.extend(
            standardize(
                average_ranks([row["control"] for row in rows])
            )
        )
    return ordered_records, z_x, z_control


def _synthetic_records(base_records, y_values):
    output = []
    for record, y_value in zip(base_records, y_values):
        synthetic = dict(record)
        synthetic["y"] = float(y_value)
        output.append(synthetic)
    return output


def _synthetic_rho(records):
    def ranks(values):
        order = sorted(range(len(values)), key=lambda index: values[index])
        result = [0.0] * len(values)
        start = 0
        while start < len(order):
            stop = start + 1
            while (
                stop < len(order)
                and values[order[stop]] == values[order[start]]
            ):
                stop += 1
            value = ((start + 1) + stop) / 2.0
            for position in range(start, stop):
                result[order[position]] = value
            start = stop
        return result

    pooled_x = []
    pooled_y = []
    industries = sorted({record["industry"] for record in records})
    for industry in industries:
        rows = [record for record in records if record["industry"] == industry]
        pooled_x.extend(ranks([row["x"] for row in rows]))
        pooled_y.extend(ranks([row["y"] for row in rows]))
    mean_x = sum(pooled_x) / len(pooled_x)
    mean_y = sum(pooled_y) / len(pooled_y)
    numerator = sum(
        (x_value - mean_x) * (y_value - mean_y)
        for x_value, y_value in zip(pooled_x, pooled_y)
    )
    denominator = math.sqrt(
        sum((value - mean_x) ** 2 for value in pooled_x)
        * sum((value - mean_y) ** 2 for value in pooled_y)
    )
    return numerator / denominator if denominator else 0.0


def _evaluate_synthetic(label, records):
    first = statistics_implementation_one(records)
    second = statistics_implementation_two(records)
    _assert_agreement(first, second, "battery." + label)
    return {"implementation_one": first, "implementation_two": second}


def _run_battery(primary_records):
    ordered, z_x, z_control = _battery_rank_z(primary_records)

    planted_rng = random.Random(424242)
    planted_epsilon = [planted_rng.gauss(0.0, 1.0) for _ in ordered]
    low = 0.5
    high = 8.0
    for _ in range(60):
        midpoint = (low + high) / 2.0
        midpoint_y = [
            x_value + midpoint * error
            for x_value, error in zip(z_x, planted_epsilon)
        ]
        midpoint_records = _synthetic_records(ordered, midpoint_y)
        midpoint_rho = _synthetic_rho(midpoint_records)
        if midpoint_rho > 0.35:
            low = midpoint
        else:
            high = midpoint
    planted_lambda = (low + high) / 2.0
    planted_y = [
        x_value + planted_lambda * error
        for x_value, error in zip(z_x, planted_epsilon)
    ]
    planted_records = _synthetic_records(ordered, planted_y)
    planted = _evaluate_synthetic("planted", planted_records)

    null_rng = random.Random(535353)
    null_y = [0.0] * len(ordered)
    industries = sorted({record["industry"] for record in ordered})
    for industry in industries:
        indices = [
            index
            for index, record in enumerate(ordered)
            if record["industry"] == industry
        ]
        values = [planted_y[index] for index in indices]
        null_rng.shuffle(values)
        for index, value in zip(indices, values):
            null_y[index] = value
    null = _evaluate_synthetic(
        "null", _synthetic_records(ordered, null_y)
    )

    confound_rng = random.Random(646464)
    confound_epsilon = [confound_rng.gauss(0.0, 1.0) for _ in ordered]
    confound_y = [
        control_value + 0.15 * error
        for control_value, error in zip(z_control, confound_epsilon)
    ]
    confound = _evaluate_synthetic(
        "confound_only", _synthetic_records(ordered, confound_y)
    )

    true_rng = random.Random(757575)
    true_epsilon = [true_rng.gauss(0.0, 1.0) for _ in ordered]
    true_y = [
        x_value
        + planted_lambda * error
        + 0.75 * control_value
        + 0.15 * error
        for x_value, control_value, error in zip(
            z_x, z_control, true_epsilon
        )
    ]
    true_confound = _evaluate_synthetic(
        "true_confound", _synthetic_records(ordered, true_y)
    )

    planted_values = planted["implementation_one"]
    null_values = null["implementation_one"]
    confound_values = confound["implementation_one"]
    true_values = true_confound["implementation_one"]
    planted_pass = (
        0.32 <= planted_values["rho"] <= 0.38
        and planted_values["rho"] >= 0.20
        and planted_values["p"] < 0.05
    )
    null_pass = not (
        null_values["rho"] >= 0.20 and null_values["p"] < 0.05
    )
    confound_pass = confound_values["partial"] < 0.10
    true_pass = (
        true_values["rho"] >= 0.20
        and true_values["p"] < 0.05
        and true_values["partial"] >= 0.10
    )
    return {
        "status": (
            "PASS"
            if planted_pass and null_pass and confound_pass and true_pass
            else "FAILURE"
        ),
        "lambda": planted_lambda,
        "planted": planted,
        "null": null,
        "confound_only": confound,
        "true_confound": true_confound,
        "checks": {
            "planted": planted_pass,
            "null": null_pass,
            "confound_only": confound_pass,
            "true_confound": true_pass,
        },
    }


def _format_battery_block(battery):
    lines = [
        "## BATTERY-BRIDGE",
        "- battery " + battery["status"],
        f"- planted lambda: {battery['lambda']:.12f}",
    ]
    for key, label in (
        ("planted", "PLANTED seed 424242"),
        ("null", "NULL seed 535353"),
        ("confound_only", "CONFOUND-ONLY seed 646464"),
        ("true_confound", "TRUE+CONFOUND seed 757575"),
    ):
        first = battery[key]["implementation_one"]
        second = battery[key]["implementation_two"]
        lines.append(
            f"- {label}: check={'PASS' if battery['checks'][key] else 'FAILURE'}; "
            f"impl1 rho={first['rho']:+.12f}, p={first['p']:.12f}, "
            f"partial={first['partial']:+.12f}; "
            f"impl2 rho={second['rho']:+.12f}, p={second['p']:.12f}, "
            f"partial={second['partial']:+.12f}"
        )
    if battery["status"] == "FAILURE":
        lines.append("- look: NOT CONSUMED; round RETIRED WITHOUT A LOOK")
    else:
        lines.append("- look eligibility: battery admitted")
    return "\n".join(lines)


def _tier(result):
    if (
        result["rho"] >= 0.40
        and result["p"] < 0.01
        and result["partial"] >= 0.20
    ):
        return "STRONG"
    if (
        result["rho"] >= 0.20
        and result["p"] < 0.05
        and result["partial"] >= 0.10
    ):
        return "CORROBORATED"
    return "UNCORROBORATED"


def _format_result(
    first, second, battery, entering, active_counts, entry_status
):
    tier = _tier(first)
    pair_counts = {
        industry: first["per_industry"][industry]["n"]
        for industry in entering
    }
    lines = [
        "## RESULT-BRIDGE",
        "- date: " + datetime.date.today().isoformat(),
        f"- pooled N: {first['n']}",
        "- entering industries:",
    ]
    for industry in INDUSTRIES:
        if entry_status[industry]["entered"]:
            lines.append(
                f"  - {industry}: entered; pairs={pair_counts[industry]}, "
                f"active_orgs={active_counts[industry]}"
            )
        else:
            lines.append(
                f"  - {industry}: coverage-limited: "
                + entry_status[industry]["reason"]
            )
    lines.extend(
        [
            f"- pooled Spearman rho: {first['rho']:+.12f}",
            f"- exact within-industry permutation p: {first['p']:.12f} "
            f"(K={PERMUTATIONS}, seed={PERMUTATION_SEED})",
            f"- volume-controlled partial Spearman: {first['partial']:+.12f}",
            "- tier: " + tier,
            "- consequence (fixed wording): " + CONSEQUENCE_WORDING[tier],
            f"- implementation 1: rho={first['rho']:+.12f}, "
            f"p={first['p']:.12f}, partial={first['partial']:+.12f}",
            f"- implementation 2: rho={second['rho']:+.12f}, "
            f"p={second['p']:.12f}, partial={second['partial']:+.12f}",
            "- battery summary: "
            + battery["status"]
            + f"; lambda={battery['lambda']:.12f}; all four locked checks PASS",
            "- secondaries (firewalled; ground nothing):",
        ]
    )
    for industry in entering:
        one = first["per_industry"][industry]
        two = second["per_industry"][industry]
        lines.append(
            f"  - {industry}: impl1 rho={one['rho']:+.12f}, p={one['p']:.12f}; "
            f"impl2 rho={two['rho']:+.12f}, p={two['p']:.12f}"
        )
    lines.extend(
        [
            f"  - top-25 Jaccard X-rank vs Y-rank: "
            f"impl1={first['top25_jaccard']:.12f}; "
            f"impl2={second['top25_jaccard']:.12f}",
            f"  - X=0-inclusive sensitivity rho: "
            f"impl1={first['x0_inclusive_rho']:+.12f}; "
            f"impl2={second['x0_inclusive_rho']:+.12f}",
            "  - ours-vs-opposition asymmetry note: credit_union is the ours "
            "stratum; fintech and crypto are opposition strata; their "
            "firewalled industry statistics ground no claim",
            f"  - 2021 split rho: impl1={first['rho_2021']:+.12f}; "
            f"impl2={second['rho_2021']:+.12f}",
            f"  - 2022 split rho: impl1={first['rho_2022']:+.12f}; "
            f"impl2={second['rho_2022']:+.12f}",
        ]
    )
    return "\n".join(lines)


def _run(mode):
    collect_real_y = mode == "look"
    _prior, members, network = _gated_sources(collect_real_y)
    active_counts = {
        industry: len(network["active_lob_ids"][industry])
        for industry in INDUSTRIES
    }
    x_positive_counts = {}
    for industry in INDUSTRIES:
        x_positive_counts[industry] = sum(
            1
            for member in members.values()
            if member.get("in_roster") and _member_x(member, industry) > 0
        )
    entry_status = {}
    entering = []
    for industry in INDUSTRIES:
        if active_counts[industry] < 3:
            entry_status[industry] = {
                "entered": False,
                "reason": "active_orgs<3",
            }
        elif x_positive_counts[industry] < 1:
            entry_status[industry] = {
                "entered": False,
                "reason": "no X>0 members",
            }
        else:
            entry_status[industry] = {"entered": True, "reason": ""}
            entering.append(industry)
    battery_records, _unused_sensitivity = _build_records(
        members, network, entering, False
    )
    if len(battery_records) < 40 or len(entering) < 2:
        print(
            "VOID-FOR-COVERAGE: pooled pairs="
            + str(len(battery_records))
            + ", entering industries="
            + str(len(entering))
            + "; look NOT consumed"
        )
        return 0

    battery = _run_battery(battery_records)
    print(_format_battery_block(battery))
    if battery["status"] != "PASS":
        return 4
    if mode == "simulate":
        return 0

    primary, sensitivity = _build_records(members, network, entering, True)
    first = statistics_implementation_one(primary, sensitivity)
    second = statistics_implementation_two(primary, sensitivity)
    _assert_agreement(first, second, "real_look")
    print()
    print(
        _format_result(
            first,
            second,
            battery,
            entering,
            active_counts,
            entry_status,
        )
    )
    return 0


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Locked FEC x LDA bridge corroboration look",
        allow_abbrev=False,
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--simulate", action="store_true", help="battery only")
    modes.add_argument("--look", action="store_true", help="consume the one look")
    return parser


def main():
    args = _build_parser().parse_args()
    if not args.simulate and not args.look:
        return _status()
    mode = "look" if args.look else "simulate"
    try:
        return _run(mode)
    except DefectError as exc:
        print("DEFECT HALT: " + str(exc) + "; nothing recorded")
        return 3
    except IngestError as exc:
        print("ERROR: " + str(exc))
        return 2
    except Exception as exc:
        print("ERROR: FEC x LDA bridge look failed: " + str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
