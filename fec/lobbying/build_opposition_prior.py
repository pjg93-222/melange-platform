# -*- coding: utf-8 -*-
"""Build the candidate FEC opposition-surge prior from member attribution.

The input attribution CSV is read-only. The optional congestion roster and
LobbyView adversary-spend artifact are also deliberate read-only inputs used
only for display metadata, roster coverage, and the LDA bridge receipt.
Decision log (2026-07-31): the output is a target-side money prior, never a
stance or vote predictor, and remains unconsumed pending user ratification.
Decision log (2026-07-31): only --out is constrained by the MB_ROOT isolation
boundary; every other CLI path is a read-only input.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT, LOCAL_MELANGE, to_host

import argparse
import csv
import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# MB_ROOT is imported for repository convention and as an isolation boundary.
try:
    from moneyball_core import dump_atomic
except Exception:
    # Worktree data planes can be empty, so retain temp-sibling + os.replace
    # semantics when the kernel import is unavailable.
    def dump_atomic(obj, path, **kw):
        temp_path = path + ".tmp." + str(os.getpid())
        try:
            with open(temp_path, "w", encoding="utf-8", newline='\r\n') as handle:
                json.dump(obj, handle, **kw)
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise


VINTAGE = "2026-06-30"
RULE_VERSION = "v1"
SHRINK_K = 250000.0
ASSOCIATED_TOTAL_BASIS_SHRINK_K = 2500.0
DIRECT_BASIS_SHRINK_K = 500.0
TARGETING_MIN_CURRENT = 1500000.0
CONFIDENCE_HIGH_CURRENT = 3000000.0
CONFIDENCE_MEDIUM_CURRENT = 500000.0
MATERIAL_TOTAL = 500000.0
COMPLETED_CYCLES = ("2021-2022", "2023-2024")
CURRENT_CYCLE = "2025-2026"
MODEL_CYCLES = COMPLETED_CYCLES + (CURRENT_CYCLE,)
INDUSTRY_DATE_STATUS = {
    "credit_union": "dated",
    "community_banks": "dated",
    "fintech": "dated",
    "crypto": "dated",
    "telecom": "cycle_only",
    "credit_arms": "cycle_only",
}
INDUSTRY_SIDE = {
    "credit_union": "ours",
    "community_banks": "opposition",
    "fintech": "opposition",
    "crypto": "opposition",
    "telecom": "opposition",
    "credit_arms": "opposition",
}
ATTRIBUTION_FIELDS = {
    "bioguide",
    "candidate_ids",
    "industry",
    "side",
    "cycle",
    "month",
    "direct_dollars",
    "sponsor_dollars",
    "n_contributions",
    "first_date",
    "last_date",
}
ASSOCIATED_INPUT_FIELDS = {
    "bioguide",
    "candidate_id",
    "industry",
    "side",
    "cycle",
    "associated_dollars",
    "assoc_support_dollars",
    "assoc_oppose_dollars",
    "assoc_mixed_dollars",
    "assoc_conduit_dollars",
    "assoc_relational_dollars",
    "assoc_noncond_trade_dollars",
}
MONTH_RE = re.compile(r"\d{4}-\d{2}\Z")
CENT = Decimal("0.01")
ZERO = Decimal("0")

CAVEATS = [
    "Sample basis: 10 companies per industry, employee/executive itemized individual giving plus limited corporate rows - NOT industry-complete. Trade-association PAC money (CULAC, BankPAC) appears only as a recipient, not as industry outflow. The associated basis adds committee-path exposure, including conduit and Super PAC paths.",
    "Member attribution covers direct candidate-committee and leadership-PAC money only; conduit money (ActBlue/WinRed) and Super PAC/JFC money are not member-attributable and are excluded from member totals.",
    "credit_arms and telecom have no transaction dates and ship three stacked views (Employee/Recipient/Master) of the same dollars; only the Recipient view is consumed, at cycle grain, and both are absent from all monthly series.",
    "2025-2026 is a partial cycle through the recorded data_max_date; current-window figures are annualized and are projections, not observed totals.",
    "This is a target-side money prior, never a stance or vote predictor.",
    "Not comparable to congestion.json: that artifact measures industry PAC money (CULAC, BankPAC and peers) while this prior measures itemized individual giving by employees and executives of ten sample companies per industry. Cross-checked lean signs agree only 57% (157/276) - the two are different instruments measuring different money streams, and this prior is not a refresh or replacement of congestion.json.",
    "Accepted residuals (reviewed, no live exposure): outputs are written atomically per file rather than as one transactional snapshot - the manifest outputs md5 map is the integrity check; sub-cent rounding was verified exact to the penny against receipts; and detail-vs-map recipient-category disagreement was measured at zero occurrences.",
    "Associated funding is committee-path EXPOSURE, not receipts: the industry's money into a committee is stamped on every candidate that committee was active on. Measured fan-out vs true industry totals: credit_union 7.1x, community_banks 6.7x, fintech 6.8x, crypto 7.8x, credit_arms 16.1x. Never sum associated dollars across members. Beneficiary rows are top-100 per cycle per view, so small exposures are truncated.",
    "Oppose-direction exposure means industry money sits in committees that OPPOSED the member. Use opposition_assoc_oppose_total (and its support/mixed/conduit siblings) for the opposition-industries signal - members those industries attack, a potential-ally marker. The unprefixed assoc_support_total/assoc_oppose_total/assoc_mixed_total/assoc_conduit_total fields sum ALL SIX industries INCLUDING credit_union (ours) and are retained for continuity, not for direction-of-threat reads. Per-industry directional splits live in associated_by_industry.",
    "Headline metrics and w_b use RELATIONAL exposure only - independent expenditures and communication costs and ActBlue/WinRed conduit pass-through are removed. Independent-expenditure and communication-cost money is legally non-coordinated with the candidate and is typically directed by a different decision-maker than the government-affairs shop that directs lobbying, so it is the wrong proxy for a lobbying channel; it is carried separately as electoral_pressure. See coverage.global_exposure_decomposition for the measured directional-electoral/conduit/relational split computed from this run's data.",
    "Beneficiary rows are the top 100 per cycle per view, so members with small industry exposure are truncated to zero rather than measured - a zero here means 'not in the top 100', not 'no money'. Senate Banking members with modest sample-company exposure (e.g. Crapo, Tim Scott) legitimately show zero on this basis.",
    "Interpretation (user-ratified 2026-07-31): a member's relational exposure is industry money one hop upstream - dollars the industry placed into committees that give directly to that member. It is a funding-channel intensity measure, which is why it may exceed what the member's own committee received.",
]


class IngestError(Exception):
    """Expected prior-build failure that must exit 2 without an output."""


def _outside_mb_root(path):
    resolved_path = os.path.realpath(os.path.abspath(path))
    root_real = os.path.realpath(MB_ROOT)
    try:
        under_root = os.path.normcase(
            os.path.commonpath([resolved_path, root_real])
        ) == os.path.normcase(root_real)
    except ValueError:
        under_root = False
    if under_root:
        raise IngestError(
            f"out path {resolved_path} is inside MB_ROOT; this builder is "
            "deliberately data-plane-independent"
        )
    return resolved_path


def _text(value):
    return "" if value is None else str(value).strip()


def _file_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _amount(value, path, row_number, column):
    text = _text(value)
    if not text:
        return ZERO
    try:
        amount = Decimal(text.replace(",", "").replace("$", ""))
    except InvalidOperation as exc:
        raise IngestError(
            f"{path} row {row_number}: invalid {column} value {text!r}"
        ) from exc
    if not amount.is_finite():
        raise IngestError(
            f"{path} row {row_number}: non-finite {column} value {text!r}"
        )
    return amount


def _date(value, path, row_number, column):
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise IngestError(
            f"{path} row {row_number}: invalid {column} value {text!r}"
        ) from exc


def _dollar(value):
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if not amount.is_finite():
        raise IngestError("non-finite dollar value generated")
    rounded = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    return 0.0 if rounded == 0 else float(rounded)


def _finite(value, label):
    number = float(value)
    if not math.isfinite(number):
        raise IngestError(f"non-finite {label} generated")
    return number


def _percent(part, total):
    if total == 0:
        return None
    value = (part / total * Decimal("100")).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )
    return _finite(value, "exposure decomposition percentage")


def _new_member_source():
    return {
        industry: {
            cycle: {"direct": ZERO, "sponsor": ZERO}
            for cycle in MODEL_CYCLES
        }
        for industry in INDUSTRY_DATE_STATUS
    }


def _new_associated_source():
    return {
        industry: {
            cycle: {
                "associated": ZERO,
                "support": ZERO,
                "oppose": ZERO,
                "mixed": ZERO,
                "conduit": ZERO,
                "relational": ZERO,
                "relational_used": ZERO,
                "noncond_trade": ZERO,
            }
            for cycle in MODEL_CYCLES
        }
        for industry in INDUSTRY_DATE_STATUS
    }


def _read_attribution(path):
    if not os.path.isfile(path):
        raise IngestError(f"attribution CSV not found: {path}")
    members = {}
    observed_cycles = set()
    data_max_date = None
    seen_keys = set()
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        for required in sorted(ATTRIBUTION_FIELDS):
            if required not in fields:
                raise IngestError(
                    f"{path}: required column {required!r} missing"
                )
        for row_number, row in enumerate(reader, start=2):
            bioguide = _text(row.get("bioguide"))
            if not bioguide:
                raise IngestError(f"{path} row {row_number}: bioguide is blank")
            industry = _text(row.get("industry"))
            if industry not in INDUSTRY_DATE_STATUS:
                raise IngestError(
                    f"{path} row {row_number}: unknown industry {industry!r}"
                )
            side = _text(row.get("side"))
            if side != INDUSTRY_SIDE[industry]:
                raise IngestError(
                    f"{path} row {row_number}: side {side!r} conflicts with "
                    f"industry {industry!r}"
                )
            cycle = _text(row.get("cycle"))
            if cycle not in MODEL_CYCLES:
                raise IngestError(
                    f"{path} row {row_number}: unsupported cycle {cycle!r}"
                )
            month = _text(row.get("month"))
            if INDUSTRY_DATE_STATUS[industry] == "cycle_only" and month:
                raise IngestError(
                    f"{path} row {row_number}: {industry} is cycle_only and must not have a month"
                )
            if month and not MONTH_RE.fullmatch(month):
                raise IngestError(
                    f"{path} row {row_number}: invalid month {month!r}"
                )
            attribution_key = (bioguide, industry, cycle, month)
            if attribution_key in seen_keys:
                raise IngestError(
                    f"{path} row {row_number}: duplicate attribution key "
                    f"{attribution_key!r}"
                )
            seen_keys.add(attribution_key)

            direct = _amount(
                row.get("direct_dollars"), path, row_number, "direct_dollars"
            )
            sponsor = _amount(
                row.get("sponsor_dollars"), path, row_number, "sponsor_dollars"
            )
            member = members.get(bioguide)
            if member is None:
                member = _new_member_source()
                members[bioguide] = member
            member[industry][cycle]["direct"] += direct
            member[industry][cycle]["sponsor"] += sponsor
            observed_cycles.add(cycle)

            for column in ("first_date", "last_date"):
                parsed = _date(row.get(column), path, row_number, column)
                if parsed is not None and (
                    data_max_date is None or parsed > data_max_date
                ):
                    data_max_date = parsed
    return members, sorted(observed_cycles), data_max_date


def _read_associated(path):
    if not os.path.isfile(path):
        raise IngestError(f"associated CSV not found: {path}")
    members = {}
    nonmember_candidate_ids = set()
    global_opposition_exposure = {
        "total": ZERO,
        "electoral": ZERO,
        "conduit": ZERO,
        "relational": ZERO,
    }
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        for required in sorted(ASSOCIATED_INPUT_FIELDS):
            if required not in fields:
                raise IngestError(
                    f"{path}: required column {required!r} missing"
                )
        for row_number, row in enumerate(reader, start=2):
            candidate_id = _text(row.get("candidate_id"))
            if not candidate_id:
                raise IngestError(
                    f"{path} row {row_number}: candidate_id is blank"
                )
            industry = _text(row.get("industry"))
            if industry not in INDUSTRY_DATE_STATUS:
                raise IngestError(
                    f"{path} row {row_number}: unknown industry {industry!r}"
                )
            side = _text(row.get("side"))
            if side != INDUSTRY_SIDE[industry]:
                raise IngestError(
                    f"{path} row {row_number}: side {side!r} conflicts with "
                    f"industry {industry!r}"
                )
            cycle = _text(row.get("cycle"))
            if cycle not in MODEL_CYCLES:
                raise IngestError(
                    f"{path} row {row_number}: unsupported cycle {cycle!r}"
                )
            values = {
                "associated": _amount(
                    row.get("associated_dollars"),
                    path,
                    row_number,
                    "associated_dollars",
                ),
                "support": _amount(
                    row.get("assoc_support_dollars"),
                    path,
                    row_number,
                    "assoc_support_dollars",
                ),
                "oppose": _amount(
                    row.get("assoc_oppose_dollars"),
                    path,
                    row_number,
                    "assoc_oppose_dollars",
                ),
                "mixed": _amount(
                    row.get("assoc_mixed_dollars"),
                    path,
                    row_number,
                    "assoc_mixed_dollars",
                ),
                "conduit": _amount(
                    row.get("assoc_conduit_dollars"),
                    path,
                    row_number,
                    "assoc_conduit_dollars",
                ),
                "relational": _amount(
                    row.get("assoc_relational_dollars"),
                    path,
                    row_number,
                    "assoc_relational_dollars",
                ),
                "noncond_trade": _amount(
                    row.get("assoc_noncond_trade_dollars"),
                    path,
                    row_number,
                    "assoc_noncond_trade_dollars",
                ),
            }
            electoral = (
                values["support"] + values["oppose"] + values["mixed"]
            )
            relational = values["relational"]
            relational_used = max(relational, ZERO)
            component_total = relational + electoral + values["conduit"]
            component_delta = component_total - values["associated"]
            if abs(component_delta) > CENT:
                raise IngestError(
                    f"{path} row {row_number}: exclusive associated buckets "
                    f"sum {_dollar(component_total):.2f}, associated_dollars "
                    f"is {_dollar(values['associated']):.2f}, delta "
                    f"{_dollar(component_delta):.2f}"
                )
            if side == "opposition":
                global_opposition_exposure["total"] += values["associated"]
                global_opposition_exposure["electoral"] += electoral
                global_opposition_exposure["conduit"] += values["conduit"]
                global_opposition_exposure["relational"] += relational
            bioguide = _text(row.get("bioguide"))
            if not bioguide:
                nonmember_candidate_ids.add(candidate_id)
                continue
            member = members.get(bioguide)
            if member is None:
                member = _new_associated_source()
                members[bioguide] = member
            aggregate = member[industry][cycle]
            for key, amount in values.items():
                aggregate[key] += amount
            aggregate["relational_used"] += relational_used
    return (
        members,
        len(members),
        len(nonmember_candidate_ids),
        global_opposition_exposure,
    )


def _load_collector_manifest(
    attribution_path,
    associated_path,
    parsed_md5s=None,
):
    manifest_path = os.path.join(
        os.path.dirname(attribution_path),
        "fec_spending_manifest.json",
    )
    if not os.path.isfile(manifest_path):
        print(f"WAITING: collector manifest not present: {manifest_path}")
        return False, None
    with open(manifest_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise IngestError(f"collector manifest must be an object: {manifest_path}")
    outputs = payload.get("outputs")
    if not isinstance(outputs, dict):
        raise IngestError(f"collector manifest lacks outputs map: {manifest_path}")
    for filename, path in (
        ("fec_member_attribution.csv", attribution_path),
        ("fec_member_associated.csv", associated_path),
    ):
        expected_md5 = outputs.get(filename)
        if not isinstance(expected_md5, str) or not expected_md5:
            raise IngestError(
                f"collector manifest lacks outputs/{filename} md5: "
                f"{manifest_path}"
            )
        actual_md5 = _file_md5(path)
        if actual_md5.casefold() != expected_md5.casefold():
            raise IngestError(
                f"{filename} md5 mismatch: manifest {expected_md5}, actual "
                f"{actual_md5}, file {path}"
            )
        if parsed_md5s is not None:
            parsed_md5 = parsed_md5s.get(filename)
            if parsed_md5 is None or parsed_md5.casefold() != actual_md5.casefold():
                raise IngestError(
                    f"{filename} changed between parse and manifest check: "
                    f"parsed md5 {parsed_md5}, now {actual_md5}, file {path}"
                )
    receipts = payload.get("receipts")
    global_receipts = (
        receipts.get("global") if isinstance(receipts, dict) else None
    )
    date_text = (
        _text(global_receipts.get("date_max"))
        if isinstance(global_receipts, dict)
        else ""
    )
    if not date_text:
        raise IngestError(
            f"collector manifest lacks receipts/global/date_max: {manifest_path}"
        )
    try:
        data_max_date = datetime.date.fromisoformat(date_text)
    except ValueError as exc:
        raise IngestError(
            f"collector manifest has invalid receipts/global/date_max "
            f"{date_text!r}: {manifest_path}"
        ) from exc
    return True, data_max_date


def _load_roster(path):
    if not os.path.isfile(path):
        print(f"WAITING: roster not present: {path}")
        return "absent", {}, 0
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise IngestError(f"roster must be a JSON list: {path}")
    roster = {}
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise IngestError(f"roster item {index} is not an object: {path}")
        for required in ("member", "bioguide", "chamber"):
            if required not in item:
                raise IngestError(
                    f"roster item {index} missing key {required!r}: {path}"
                )
        bioguide = _text(item.get("bioguide"))
        if bioguide:
            roster[bioguide] = {
                "display_name": _text(item.get("member")),
                "chamber": _text(item.get("chamber")),
            }
    return "present", roster, len(payload)


def _load_lobbyview(path):
    if not os.path.isfile(path):
        print(f"WAITING: LobbyView bridge input not present: {path}")
        return "absent", None
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise IngestError(f"LobbyView bridge input must be an object: {path}")
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        coverage = {}
    value = coverage.get("max_finance_joined_filing_year")
    if value in (None, ""):
        maximum_year = None
    else:
        try:
            maximum_year = int(value)
        except (TypeError, ValueError) as exc:
            raise IngestError(
                "LobbyView max_finance_joined_filing_year is not an integer: "
                f"{value!r}"
            ) from exc
    return "present", maximum_year


def _months_elapsed(data_max_date):
    if data_max_date is None:
        return 0
    months = (
        (data_max_date.year - 2025) * 12
        + data_max_date.month
    )
    return min(24, max(0, months))


def _annualization_factor(months_elapsed):
    if months_elapsed >= 24:
        return 1.0
    if months_elapsed <= 0:
        return 1.0
    return _finite(24.0 / months_elapsed, "annualization_factor")


def _lead_months(data_max_date, lobbyview_year):
    if data_max_date is None or lobbyview_year is None:
        return None
    return max(
        0,
        (data_max_date.year - lobbyview_year) * 12
        + data_max_date.month
        - 12,
    )


def _method():
    return {
        "basis": "associated relational committee-path exposure",
        "measure_interpretation": "Relational exposure measures how much industry money flows into the committees that give directly to the member - the one-hop upstream funding channel (JFCs, leadership PACs, party committees, trade and company PACs). Ratified by the user 2026-07-31 as the preferred member-level measure, superior to direct-only attribution because sophisticated money routes through intermediary committees rather than maxing out directly.",
        "exposure_bases": {
            "assoc_total": "associated_dollars",
            "assoc_electoral": (
                "assoc_support_dollars + assoc_oppose_dollars + "
                "assoc_mixed_dollars"
            ),
            "assoc_relational": "assoc_relational_dollars",
            "directional_electoral_scope": (
                "support, oppose, and mixed directional paths, including "
                "independent expenditures and communication costs"
            ),
        },
        "member_directional_totals": {
            "all_industry": (
                "assoc_support_total / assoc_oppose_total / "
                "assoc_mixed_total / assoc_conduit_total sum ALL SIX "
                "industries including credit_union (ours) - retained for "
                "continuity"
            ),
            "opposition_scoped": (
                "opposition_assoc_*_total restrict the same buckets to "
                "opposition-side industries - the correctly-scoped "
                "directional read"
            ),
        },
        "by_industry_associated_fields": {
            "directional": (
                "assoc_support / assoc_oppose / assoc_mixed / "
                "assoc_conduit per industry per cycle are refund-netted and "
                "MAY be negative; clamp only at point of use"
            ),
            "assoc_relational_used": (
                "sum of ROW-level max(assoc_relational_dollars, 0) clamps - "
                "the exact basis used by headline aggregates and w_b; "
                "written so industry-subset recomputation is exact "
                "(sum-of-row-clamps != clamp-of-sum)"
            ),
        },
        "SHRINK_K": SHRINK_K,
        "ASSOCIATED_TOTAL_BASIS_SHRINK_K": (
            ASSOCIATED_TOTAL_BASIS_SHRINK_K
        ),
        "DIRECT_BASIS_SHRINK_K": DIRECT_BASIS_SHRINK_K,
        "TARGETING_MIN_CURRENT": TARGETING_MIN_CURRENT,
        "CONFIDENCE_HIGH_CURRENT": CONFIDENCE_HIGH_CURRENT,
        "CONFIDENCE_MEDIUM_CURRENT": CONFIDENCE_MEDIUM_CURRENT,
        "MATERIAL_TOTAL": MATERIAL_TOTAL,
        "distribution_calibration": {
            "SHRINK_K": "250000.0 (~p25-p50 scale)",
            "TARGETING_MIN_CURRENT": "1500000.0 (~p70 current annualized)",
            "CONFIDENCE_HIGH_CURRENT": "3000000.0 (~p80 current annualized)",
            "CONFIDENCE_MEDIUM_CURRENT": "500000.0 (~p50 current annualized)",
            "MATERIAL_TOTAL": "500000.0 (~p50 all-cycle relational)",
        },
        "baseline_X": (
            'mean(relational cycle dollars for cycles in ("2021-2022",'
            '"2023-2024"), missing counted as 0.0)'
        ),
        "negative_value_clamp": {
            "baseline_X_clamped": "max(baseline_X, 0.0)",
            "current_X_clamped": (
                "max(current_X_raw, 0.0), applied before annualizing"
            ),
            "all_cycle_X_clamped": (
                "max(raw all-cycle X total, 0.0), used for lean only"
            ),
            "final_lean_clamp": "max(-1.0, min(1.0, lean))",
            "rationale": (
                "refunds can drive an aggregate negative; clamping keeps "
                "log2 and lean in-domain while raw values remain visible"
            ),
        },
        "associated_component_point_of_use_clamp": (
            "assoc_relational_used = max(assoc_relational_dollars, 0.0); "
            "electoral totals used for electoral_pressure and "
            "battleground_flag = max(raw summed electoral total, 0.0); raw "
            "refund-netted negative bucket values remain visible in emitted "
            "by-cycle dictionaries"
        ),
        "months_elapsed": (
            "months from 2025-01 through data_max_date inclusive (cap at 24)"
        ),
        "annualization_factor": (
            "24.0 / months_elapsed (1.0 if months_elapsed >= 24; "
            "division guard returns 1.0 when months_elapsed is 0)"
        ),
        "current_X_annualized": (
            "current_X_clamped * annualization_factor"
        ),
        "opposition_surge_ratio": (
            "(current_opposition_annualized + SHRINK_K) / "
            "(baseline_opposition_clamped + SHRINK_K)"
        ),
        "opposition_surge_log2": "log2(opposition_surge_ratio)",
        "targeting_flag": (
            "(opposition_surge_log2 >= 1.0) and "
            "(current_opposition_annualized >= TARGETING_MIN_CURRENT)"
        ),
        "confidence": (
            '"high" if nonzero opposition relational exposure occurs in >= '
            "2 cycles and current_opposition_annualized >= "
            'CONFIDENCE_HIGH_CURRENT; "medium" if '
            "current_opposition_annualized >= CONFIDENCE_MEDIUM_CURRENT; "
            'else "low"'
        ),
        "lean": (
            "defensively clamp to [-1.0, 1.0] after "
            "(ours_total_clamped - opposition_total_clamped) / "
            "(ours_total_clamped + opposition_total_clamped), or null when "
            "the denominator is 0"
        ),
        "ranking_materiality": (
            "(ours_total + opposition_total) >= MATERIAL_TOTAL using raw "
            "all-cycle relational sums; members with null lean are excluded"
        ),
        "direct_basis": (
            "continuity metrics use direct candidate plus leadership-PAC "
            "dollars with DIRECT_BASIS_SHRINK_K"
        ),
        "associated_total_basis": (
            "v3 continuity metrics use total associated exposure with "
            "ASSOCIATED_TOTAL_BASIS_SHRINK_K"
        ),
        "electoral_pressure": {
            "current_electoral_annualized": (
                "max(current opposition assoc_electoral, 0.0) * "
                "annualization_factor"
            ),
            "ie_share_of_exposure": (
                "opposition_electoral_total / opposition assoc_total, or "
                "null when the denominator is 0"
            ),
            "ie_share_of_exposure_field_name": (
                "retained for continuity but measures all directional "
                "electoral paths, including independent expenditures and "
                "communication costs"
            ),
            "battleground_flag": (
                "ie_share_of_exposure >= 0.5 and "
                "opposition_electoral_total >= 1000000.0"
            ),
        },
        "w_b": (
            "member share of current annualized opposition assoc_relational "
            "exposure in the active rankings universe, clamped before "
            "annualizing"
        ),
        "attack_ranking_mixed_direction_policy": (
            "mixed-direction paths are excluded from the attack ranking "
            "because the source does not apportion them; they remain counted "
            "in electoral exposure"
        ),
        "ranking_order": {
            "most_captured": (
                "lean ascending, opposition_total descending, then "
                "bioguide ascending"
            ),
            "most_ours": (
                "lean descending, ours_total descending, then bioguide "
                "ascending"
            ),
            "top_opposition_surge": (
                "targeting_flag true; opposition_surge_log2 descending, "
                "then bioguide ascending"
            ),
            "top_opposition_dollars": (
                "current_opposition_annualized descending, then bioguide "
                "ascending"
            ),
            "most_attacked_by_opposition": (
                "eligible-universe members with opposition-industry exclusive "
                "assoc_oppose_total > 0; mixed paths excluded; value "
                "descending, then bioguide ascending"
            ),
            "top_electoral_pressure": (
                "eligible-universe members with opposition_electoral_total > "
                "0; value descending, then bioguide ascending"
            ),
        },
    }


def _member_metrics(
    direct_source,
    associated_source,
    annualization_factor,
    roster_entry,
    in_roster,
):
    by_industry = {}
    direct_ours_by_cycle = {cycle: ZERO for cycle in MODEL_CYCLES}
    direct_opposition_by_cycle = {cycle: ZERO for cycle in MODEL_CYCLES}
    associated_by_industry = {}
    ours_associated_by_cycle = {cycle: ZERO for cycle in MODEL_CYCLES}
    opposition_associated_by_cycle = {cycle: ZERO for cycle in MODEL_CYCLES}
    ours_relational_by_cycle = {cycle: ZERO for cycle in MODEL_CYCLES}
    opposition_relational_by_cycle = {cycle: ZERO for cycle in MODEL_CYCLES}
    ours_relational_used_by_cycle = {
        cycle: ZERO for cycle in MODEL_CYCLES
    }
    opposition_relational_used_by_cycle = {
        cycle: ZERO for cycle in MODEL_CYCLES
    }
    ours_electoral_by_cycle = {cycle: ZERO for cycle in MODEL_CYCLES}
    opposition_electoral_by_cycle = {cycle: ZERO for cycle in MODEL_CYCLES}
    negative_cycle_values = 0
    assoc_oppose_total = ZERO
    assoc_support_total = ZERO
    assoc_mixed_total = ZERO
    assoc_conduit_total = ZERO
    opposition_assoc_oppose_total = ZERO
    opposition_assoc_support_total = ZERO
    opposition_assoc_mixed_total = ZERO
    opposition_assoc_conduit_total = ZERO

    for industry in INDUSTRY_DATE_STATUS:
        direct_cycle_dollars = {}
        direct_total = ZERO
        sponsor_total = ZERO
        associated_cycle_bases = {}
        for cycle in MODEL_CYCLES:
            direct = direct_source[industry][cycle]["direct"]
            sponsor = direct_source[industry][cycle]["sponsor"]
            direct_cycle_total = direct + sponsor
            if direct_cycle_total < 0:
                negative_cycle_values += 1
            direct_cycle_dollars[cycle] = _dollar(direct_cycle_total)
            direct_total += direct
            sponsor_total += sponsor
            if INDUSTRY_SIDE[industry] == "ours":
                direct_ours_by_cycle[cycle] += direct_cycle_total
            else:
                direct_opposition_by_cycle[cycle] += direct_cycle_total

            associated = associated_source[industry][cycle]
            assoc_total = associated["associated"]
            assoc_electoral = (
                associated["support"]
                + associated["oppose"]
                + associated["mixed"]
            )
            assoc_relational = associated["relational"]
            assoc_relational_used = associated["relational_used"]
            associated_cycle_bases[cycle] = {
                "assoc_total": _dollar(assoc_total),
                "assoc_electoral": _dollar(assoc_electoral),
                "assoc_relational": _dollar(assoc_relational),
                "assoc_support": _dollar(associated["support"]),
                "assoc_oppose": _dollar(associated["oppose"]),
                "assoc_mixed": _dollar(associated["mixed"]),
                "assoc_conduit": _dollar(associated["conduit"]),
                "assoc_relational_used": _dollar(assoc_relational_used),
            }
            if INDUSTRY_SIDE[industry] == "ours":
                ours_associated_by_cycle[cycle] += assoc_total
                ours_relational_by_cycle[cycle] += assoc_relational
                ours_relational_used_by_cycle[
                    cycle
                ] += assoc_relational_used
                ours_electoral_by_cycle[cycle] += assoc_electoral
            else:
                opposition_associated_by_cycle[cycle] += assoc_total
                opposition_relational_by_cycle[cycle] += assoc_relational
                opposition_relational_used_by_cycle[
                    cycle
                ] += assoc_relational_used
                opposition_electoral_by_cycle[cycle] += assoc_electoral
                opposition_assoc_oppose_total += associated["oppose"]
                opposition_assoc_support_total += associated["support"]
                opposition_assoc_mixed_total += associated["mixed"]
                opposition_assoc_conduit_total += associated["conduit"]
            assoc_oppose_total += associated["oppose"]
            assoc_support_total += associated["support"]
            assoc_mixed_total += associated["mixed"]
            assoc_conduit_total += associated["conduit"]

        by_industry[industry] = {
            "cycle_dollars": direct_cycle_dollars,
            "direct": _dollar(direct_total),
            "sponsor": _dollar(sponsor_total),
        }
        associated_by_industry[industry] = associated_cycle_bases

    direct_baseline_opposition = sum(
        (
            float(direct_opposition_by_cycle[cycle])
            for cycle in COMPLETED_CYCLES
        ),
        0.0,
    ) / len(COMPLETED_CYCLES)
    direct_current_opposition_raw = float(
        direct_opposition_by_cycle[CURRENT_CYCLE]
    )
    direct_current_opposition_annualized = _finite(
        max(direct_current_opposition_raw, 0.0) * annualization_factor,
        "direct_basis.current_opposition_annualized",
    )
    direct_surge_ratio = _finite(
        (
            direct_current_opposition_annualized
            + DIRECT_BASIS_SHRINK_K
        )
        / (max(direct_baseline_opposition, 0.0) + DIRECT_BASIS_SHRINK_K),
        "direct_basis.surge_ratio",
    )
    direct_surge_log2 = _finite(
        math.log2(direct_surge_ratio),
        "direct_basis.surge_log2",
    )
    direct_ours_total = max(
        float(sum(direct_ours_by_cycle.values(), ZERO)), 0.0
    )
    direct_opposition_total = max(
        float(sum(direct_opposition_by_cycle.values(), ZERO)), 0.0
    )
    direct_combined_total = direct_ours_total + direct_opposition_total
    direct_lean = None
    if direct_combined_total > 0:
        direct_lean = max(
            -1.0,
            min(
                1.0,
                _finite(
                    (direct_ours_total - direct_opposition_total)
                    / direct_combined_total,
                    "direct_basis.lean",
                ),
            ),
        )

    associated_total_baseline_opposition = sum(
        (
            float(opposition_associated_by_cycle[cycle])
            for cycle in COMPLETED_CYCLES
        ),
        0.0,
    ) / len(COMPLETED_CYCLES)
    associated_total_current_opposition_raw = float(
        opposition_associated_by_cycle[CURRENT_CYCLE]
    )
    associated_total_current_opposition_annualized = _finite(
        max(associated_total_current_opposition_raw, 0.0)
        * annualization_factor,
        "associated_total_basis.current_opposition_annualized",
    )
    associated_total_surge_ratio = _finite(
        (
            associated_total_current_opposition_annualized
            + ASSOCIATED_TOTAL_BASIS_SHRINK_K
        )
        / (
            max(associated_total_baseline_opposition, 0.0)
            + ASSOCIATED_TOTAL_BASIS_SHRINK_K
        ),
        "associated_total_basis.surge_ratio",
    )
    associated_total_surge_log2 = _finite(
        math.log2(associated_total_surge_ratio),
        "associated_total_basis.surge_log2",
    )
    associated_total_ours = max(
        float(sum(ours_associated_by_cycle.values(), ZERO)), 0.0
    )
    associated_total_opposition = max(
        float(sum(opposition_associated_by_cycle.values(), ZERO)), 0.0
    )
    associated_total_combined = (
        associated_total_ours + associated_total_opposition
    )
    associated_total_lean = None
    if associated_total_combined > 0:
        associated_total_lean = max(
            -1.0,
            min(
                1.0,
                _finite(
                    (associated_total_ours - associated_total_opposition)
                    / associated_total_combined,
                    "associated_total_basis.lean",
                ),
            ),
        )

    baseline_ours = sum(
        (
            float(ours_relational_used_by_cycle[cycle])
            for cycle in COMPLETED_CYCLES
        ),
        0.0,
    ) / len(COMPLETED_CYCLES)
    baseline_opposition = sum(
        (
            float(opposition_relational_used_by_cycle[cycle])
            for cycle in COMPLETED_CYCLES
        ),
        0.0,
    ) / len(COMPLETED_CYCLES)
    baseline_opposition_clamped = max(baseline_opposition, 0.0)
    current_ours_raw = float(
        ours_relational_used_by_cycle[CURRENT_CYCLE]
    )
    current_opposition_raw = float(
        opposition_relational_used_by_cycle[CURRENT_CYCLE]
    )
    current_ours_annualized = _finite(
        max(current_ours_raw, 0.0) * annualization_factor,
        "current_ours_annualized",
    )
    current_opposition_annualized = _finite(
        max(current_opposition_raw, 0.0) * annualization_factor,
        "current_opposition_annualized",
    )
    opposition_surge_ratio = _finite(
        (current_opposition_annualized + SHRINK_K)
        / (baseline_opposition_clamped + SHRINK_K),
        "opposition_surge_ratio",
    )
    opposition_surge_log2 = _finite(
        math.log2(opposition_surge_ratio),
        "opposition_surge_log2",
    )
    targeting_flag = (
        opposition_surge_log2 >= 1.0
        and current_opposition_annualized >= TARGETING_MIN_CURRENT
    )
    opposition_nonzero_cycles = sum(
        opposition_relational_used_by_cycle[cycle] != 0
        for cycle in MODEL_CYCLES
    )
    if (
        opposition_nonzero_cycles >= 2
        and current_opposition_annualized >= CONFIDENCE_HIGH_CURRENT
    ):
        confidence = "high"
    elif current_opposition_annualized >= CONFIDENCE_MEDIUM_CURRENT:
        confidence = "medium"
    else:
        confidence = "low"

    ours_total = sum(ours_relational_used_by_cycle.values(), ZERO)
    opposition_total = sum(
        opposition_relational_used_by_cycle.values(), ZERO
    )
    combined_total = ours_total + opposition_total
    ours_total_clamped = max(float(ours_total), 0.0)
    opposition_total_clamped = max(float(opposition_total), 0.0)
    combined_total_clamped = ours_total_clamped + opposition_total_clamped
    lean = None
    if combined_total_clamped > 0:
        lean = max(
            -1.0,
            min(
                1.0,
                _finite(
                    (ours_total_clamped - opposition_total_clamped)
                    / combined_total_clamped,
                    "lean",
                ),
            ),
        )

    opposition_relational_total = sum(
        opposition_relational_used_by_cycle.values(), ZERO
    )
    opposition_electoral_total_raw = sum(
        opposition_electoral_by_cycle.values(), ZERO
    )
    opposition_electoral_total = max(
        opposition_electoral_total_raw,
        ZERO,
    )
    opposition_associated_total = sum(
        opposition_associated_by_cycle.values(), ZERO
    )
    current_electoral_annualized = _finite(
        max(float(opposition_electoral_by_cycle[CURRENT_CYCLE]), 0.0)
        * annualization_factor,
        "electoral_pressure.current_electoral_annualized",
    )
    ie_share_of_exposure = None
    if opposition_associated_total != 0:
        ie_share_of_exposure = _finite(
            opposition_electoral_total / opposition_associated_total,
            "electoral_pressure.ie_share_of_exposure",
        )
    battleground_flag = (
        ie_share_of_exposure is not None
        and ie_share_of_exposure >= 0.5
        and opposition_electoral_total >= Decimal("1000000")
    )

    payload = {
        "display_name": roster_entry.get("display_name", ""),
        "chamber": roster_entry.get("chamber", ""),
        "in_roster": in_roster,
        "negative_cycle_values": negative_cycle_values,
        "by_industry": by_industry,
        "ours_dollars_by_cycle": {
            cycle: _dollar(direct_ours_by_cycle[cycle])
            for cycle in MODEL_CYCLES
        },
        "opposition_dollars_by_cycle": {
            cycle: _dollar(direct_opposition_by_cycle[cycle])
            for cycle in MODEL_CYCLES
        },
        "associated_by_industry": associated_by_industry,
        "ours_associated_by_cycle": {
            cycle: _dollar(ours_associated_by_cycle[cycle])
            for cycle in MODEL_CYCLES
        },
        "opposition_associated_by_cycle": {
            cycle: _dollar(opposition_associated_by_cycle[cycle])
            for cycle in MODEL_CYCLES
        },
        "ours_relational_by_cycle": {
            cycle: _dollar(ours_relational_by_cycle[cycle])
            for cycle in MODEL_CYCLES
        },
        "opposition_relational_by_cycle": {
            cycle: _dollar(opposition_relational_by_cycle[cycle])
            for cycle in MODEL_CYCLES
        },
        "ours_electoral_by_cycle": {
            cycle: _dollar(ours_electoral_by_cycle[cycle])
            for cycle in MODEL_CYCLES
        },
        "opposition_electoral_by_cycle": {
            cycle: _dollar(opposition_electoral_by_cycle[cycle])
            for cycle in MODEL_CYCLES
        },
        "opposition_relational_total": _dollar(
            opposition_relational_total
        ),
        "opposition_electoral_total": _dollar(
            opposition_electoral_total
        ),
        "assoc_oppose_total": _dollar(assoc_oppose_total),
        "assoc_support_total": _dollar(assoc_support_total),
        "assoc_mixed_total": _dollar(assoc_mixed_total),
        "assoc_conduit_total": _dollar(assoc_conduit_total),
        "opposition_assoc_oppose_total": _dollar(
            opposition_assoc_oppose_total
        ),
        "opposition_assoc_support_total": _dollar(
            opposition_assoc_support_total
        ),
        "opposition_assoc_mixed_total": _dollar(
            opposition_assoc_mixed_total
        ),
        "opposition_assoc_conduit_total": _dollar(
            opposition_assoc_conduit_total
        ),
        "baseline_ours": _dollar(baseline_ours),
        "baseline_opposition": _dollar(baseline_opposition),
        "current_ours_raw": _dollar(current_ours_raw),
        "current_opposition_raw": _dollar(current_opposition_raw),
        "current_ours_annualized": _dollar(current_ours_annualized),
        "current_opposition_annualized": _dollar(
            current_opposition_annualized
        ),
        "opposition_surge_ratio": opposition_surge_ratio,
        "opposition_surge_log2": opposition_surge_log2,
        "lean": lean,
        "targeting_flag": targeting_flag,
        "confidence": confidence,
        "direct_basis": {
            "baseline_opposition": _dollar(direct_baseline_opposition),
            "current_opposition_annualized": _dollar(
                direct_current_opposition_annualized
            ),
            "surge_log2": direct_surge_log2,
            "lean": direct_lean,
        },
        "associated_total_basis": {
            "baseline_opposition": _dollar(
                associated_total_baseline_opposition
            ),
            "current_opposition_annualized": _dollar(
                associated_total_current_opposition_annualized
            ),
            "surge_log2": associated_total_surge_log2,
            "lean": associated_total_lean,
        },
        "electoral_pressure": {
            "opposition_electoral_total": _dollar(
                opposition_electoral_total
            ),
            "current_electoral_annualized": _dollar(
                current_electoral_annualized
            ),
            "ie_share_of_exposure": ie_share_of_exposure,
            "battleground_flag": battleground_flag,
        },
    }
    ranking_values = {
        "ours_total": float(ours_total),
        "opposition_total": float(opposition_total),
        "combined_total": float(combined_total),
        "current_ours_annualized": current_ours_annualized,
        "current_opposition_annualized": current_opposition_annualized,
        "opposition_surge_log2": opposition_surge_log2,
        "lean": lean,
        "targeting_flag": targeting_flag,
        "in_roster": in_roster,
        "opposition_assoc_oppose_total": float(
            opposition_assoc_oppose_total
        ),
        "opposition_electoral_total": float(
            opposition_electoral_total
        ),
        "ie_share_of_exposure": ie_share_of_exposure,
    }
    return payload, ranking_values


def _ranking_entry(bioguide, member, value, totals=None):
    entry = {
        "bioguide": bioguide,
        "display_name": member["display_name"],
        "value": value,
        "current_opposition_annualized": member[
            "current_opposition_annualized"
        ],
        "current_ours_annualized": member["current_ours_annualized"],
    }
    if totals is not None:
        entry["opposition_total"] = _dollar(totals["opposition_total"])
        entry["ours_total"] = _dollar(totals["ours_total"])
    return entry


def _rankings(members, ranking_values, roster_only):
    eligible_values = {
        bioguide: values
        for bioguide, values in ranking_values.items()
        if not roster_only or values["in_roster"]
    }
    surge = sorted(
        (
            (bioguide, values)
            for bioguide, values in eligible_values.items()
            if values["targeting_flag"]
        ),
        key=lambda item: (-item[1]["opposition_surge_log2"], item[0]),
    )[:25]
    opposition_dollars = sorted(
        eligible_values.items(),
        key=lambda item: (
            -item[1]["current_opposition_annualized"],
            item[0],
        ),
    )[:25]
    material = [
        (bioguide, values)
        for bioguide, values in eligible_values.items()
        if values["combined_total"] >= MATERIAL_TOTAL
        and values["lean"] is not None
    ]
    captured = sorted(
        material,
        key=lambda item: (
            item[1]["lean"],
            -item[1]["opposition_total"],
            item[0],
        ),
    )[:25]
    ours = sorted(
        material,
        key=lambda item: (
            -item[1]["lean"],
            -item[1]["ours_total"],
            item[0],
        ),
    )[:25]
    attacked = sorted(
        (
            (bioguide, values)
            for bioguide, values in eligible_values.items()
            if values["opposition_assoc_oppose_total"] > 0
        ),
        key=lambda item: (
            -item[1]["opposition_assoc_oppose_total"],
            item[0],
        ),
    )[:25]
    electoral_pressure = sorted(
        (
            (bioguide, values)
            for bioguide, values in eligible_values.items()
            if values["opposition_electoral_total"] > 0
        ),
        key=lambda item: (
            -item[1]["opposition_electoral_total"],
            item[0],
        ),
    )[:25]

    return {
        "top_opposition_surge": [
            _ranking_entry(
                bioguide,
                members[bioguide],
                values["opposition_surge_log2"],
            )
            for bioguide, values in surge
        ],
        "top_opposition_dollars": [
            _ranking_entry(
                bioguide,
                members[bioguide],
                _dollar(values["current_opposition_annualized"]),
            )
            for bioguide, values in opposition_dollars
        ],
        "most_captured": [
            _ranking_entry(
                bioguide,
                members[bioguide],
                values["lean"],
                totals=values,
            )
            for bioguide, values in captured
        ],
        "most_ours": [
            _ranking_entry(
                bioguide,
                members[bioguide],
                values["lean"],
                totals=values,
            )
            for bioguide, values in ours
        ],
        "most_attacked_by_opposition": [
            {
                "bioguide": bioguide,
                "display_name": members[bioguide]["display_name"],
                "value": _dollar(
                    values["opposition_assoc_oppose_total"]
                ),
                "current_opposition_annualized": members[bioguide][
                    "current_opposition_annualized"
                ],
            }
            for bioguide, values in attacked
        ],
        "top_electoral_pressure": [
            {
                "bioguide": bioguide,
                "display_name": members[bioguide]["display_name"],
                "value": _dollar(values["opposition_electoral_total"]),
                "ie_share_of_exposure": values["ie_share_of_exposure"],
            }
            for bioguide, values in electoral_pressure
        ],
    }


def _build_parser():
    default_out = os.environ.get(
        "FEC_SPENDING_OUT",
        os.path.join(LOCAL_MELANGE, "fec_spending", "derived"),
    )
    default_roster = to_host(r"C:\Users\pgoff\OneDrive\Desktop\Melange Systems\data\engine\congestion.json")   # identity on Windows; the container's mount there
    default_lobbyview = os.path.join(
        LOCAL_MELANGE,
        "lobbyview",
        "derived",
        "lobbyview_adversary_spend.json",
    )
    parser = argparse.ArgumentParser(
        description="Build the candidate FEC opposition prior."
    )
    parser.add_argument(
        "--attribution",
        default=os.path.join(default_out, "fec_member_attribution.csv"),
        help="Read-only member-attribution CSV",
    )
    parser.add_argument("--out", default=default_out, help="Derived output directory")
    parser.add_argument(
        "--roster", default=default_roster, help="Optional read-only member roster"
    )
    parser.add_argument(
        "--lobbyview",
        default=default_lobbyview,
        help="Optional read-only LobbyView bridge artifact",
    )
    return parser


def _run(args):
    out_dir = _outside_mb_root(args.out)
    attribution_path = os.path.abspath(args.attribution)
    associated_path = os.path.join(
        os.path.dirname(attribution_path),
        "fec_member_associated.csv",
    )
    roster_path = os.path.abspath(args.roster)
    lobbyview_path = os.path.abspath(args.lobbyview)

    parsed_md5s = {}
    for filename, path in (
        ("fec_member_attribution.csv", attribution_path),
        ("fec_member_associated.csv", associated_path),
    ):
        if os.path.isfile(path):
            parsed_md5s[filename] = _file_md5(path)
    (
        source_members,
        observed_cycles,
        attributed_data_max_date,
    ) = _read_attribution(
        attribution_path
    )
    (
        associated_members,
        n_members_with_associated,
        n_nonmember_candidates_carried,
        global_opposition_exposure,
    ) = _read_associated(associated_path)
    snapshot_verified, manifest_data_max_date = _load_collector_manifest(
        attribution_path,
        associated_path,
        parsed_md5s,
    )
    if snapshot_verified:
        data_max_date = manifest_data_max_date
        data_max_date_source = "manifest_receipts"
    else:
        data_max_date = attributed_data_max_date
        data_max_date_source = "attributed_rows"
    roster_status, roster, roster_total = _load_roster(roster_path)
    lobbyview_status, lobbyview_year = _load_lobbyview(lobbyview_path)

    months_elapsed = _months_elapsed(data_max_date)
    annualization_factor = _annualization_factor(months_elapsed)
    members = {}
    ranking_values = {}
    all_bioguides = set(source_members) | set(associated_members)
    for bioguide in sorted(all_bioguides):
        in_roster = roster_status == "present" and bioguide in roster
        member, values = _member_metrics(
            source_members.get(bioguide, _new_member_source()),
            associated_members.get(bioguide, _new_associated_source()),
            annualization_factor,
            roster.get(bioguide, {}),
            in_roster,
        )
        members[bioguide] = member
        ranking_values[bioguide] = values
    roster_present = roster_status == "present"
    w_b_denominator = sum(
        values["current_opposition_annualized"]
        for values in ranking_values.values()
        if (not roster_present or values["in_roster"])
        and values["current_opposition_annualized"] > 0
    )
    w_b_status = (
        "normalized"
        if w_b_denominator > 0
        else "degenerate - zero denominator"
    )
    for bioguide, member in members.items():
        values = ranking_values[bioguide]
        in_w_b_universe = not roster_present or values["in_roster"]
        if in_w_b_universe and w_b_denominator > 0:
            member["w_b"] = _finite(
                values["current_opposition_annualized"]
                / w_b_denominator,
                "w_b",
            )
        else:
            member["w_b"] = 0.0

    if roster_status == "present":
        n_roster_members_zero_exposure = sum(
            ranking_values.get(bioguide, {}).get("opposition_total", 0.0)
            <= 0
            for bioguide in roster
        )
    else:
        n_roster_members_zero_exposure = 0
    global_exposure_decomposition = {
        "ie_pct": _percent(
            global_opposition_exposure["electoral"],
            global_opposition_exposure["total"],
        ),
        "conduit_pct": _percent(
            global_opposition_exposure["conduit"],
            global_opposition_exposure["total"],
        ),
        "relational_pct": _percent(
            global_opposition_exposure["relational"],
            global_opposition_exposure["total"],
        ),
    }

    generated = datetime.datetime.now(datetime.timezone.utc).isoformat()
    run_id = hashlib.sha1(
        (generated + attribution_path + associated_path + out_dir).encode("utf-8")
    ).hexdigest()[:12]
    data_max_text = data_max_date.isoformat() if data_max_date else None
    payload = {
        "provenance": {
            "vintage": VINTAGE,
            "generated": generated,
            "run_id": run_id,
            "rule_version": RULE_VERSION,
            "status": (
                "ratified for substrate consumption - user directive "
                "2026-07-31"
            ),
            "source": "Mentat FEC industry spending workbooks",
            "consumed_by": (
                "coalition-builder adversary substrate (pending that build's "
                "own APPLY)"
            ),
        },
        "method": _method(),
        "coverage": {
            "basis": "associated_relational",
            "cycles": observed_cycles,
            "data_max_date": data_max_text,
            "data_max_date_source": data_max_date_source,
            "months_elapsed_current_cycle": months_elapsed,
            "annualization_factor": annualization_factor,
            "industries": dict(INDUSTRY_DATE_STATUS),
            "roster_status": roster_status,
            "roster_matched": sum(
                bioguide in roster for bioguide in all_bioguides
            ),
            "roster_total": roster_total,
            "rankings_universe": (
                "roster members only"
                if roster_status == "present"
                else "all attributed members"
            ),
            "snapshot_verified": snapshot_verified,
            "members_with_negative_values": sum(
                member["negative_cycle_values"] > 0
                for member in members.values()
            ),
            "n_members_with_associated": n_members_with_associated,
            "n_nonmember_candidates_carried": (
                n_nonmember_candidates_carried
            ),
            "n_roster_members_zero_exposure": (
                n_roster_members_zero_exposure
            ),
            "global_exposure_decomposition": (
                global_exposure_decomposition
            ),
            "w_b_status": w_b_status,
        },
        "wiring": {
            "status": (
                "ratified - user directive 2026-07-31 ('Ratify wiring')"
            ),
            "consumer": (
                "coalition-builder adversary substrate / shadowing layer"
            ),
            "w_b_definition": (
                "members[bioguide].w_b = member share of total opposition "
                "RELATIONAL associated exposure in the active rankings "
                "universe (current annualized, clamped, independent "
                "expenditures and communication costs and conduit money "
                "removed), sums to 1.0 over eligible members with nonzero "
                "exposure"
            ),
            "w_b_status": w_b_status,
            "basis": "associated_relational",
            "excluded_from_basis": [
                "independent expenditures and communication costs (legally non-coordinated with the candidate)",
                "ActBlue/WinRed conduit pass-through",
            ],
        },
        "caveats": list(CAVEATS),
        "members": members,
        "rankings": _rankings(
            members,
            ranking_values,
            roster_status == "present",
        ),
        "lda_bridge": {
            "status": lobbyview_status,
            "note": (
                "FEC leads the processed LDA lane; LobbyView issue codes end "
                "2021 and spend ends 2023 while FEC runs to data_max_date, so "
                "the FEC window is the only member-level money signal after "
                "2023."
            ),
            "lobbyview_max_finance_joined_filing_year": lobbyview_year,
            "fec_data_max_date": data_max_text,
            "lead_months": _lead_months(data_max_date, lobbyview_year),
        },
    }

    # This is intentionally the first filesystem mutation in the build.
    os.makedirs(out_dir, exist_ok=True)
    output_path = os.path.join(out_dir, "fec_opposition_prior.json")
    dump_atomic(
        payload,
        output_path,
        indent=1,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    )
    print(
        f"FEC opposition prior: {len(members)} members | data through "
        f"{data_max_text or 'unknown'} | annualization "
        f"{annualization_factor:.6f}"
    )
    return 0


def main():
    # Windows limits csv.field_size_limit to a 32-bit C long.
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            break
        except OverflowError:
            limit //= 2
    args = _build_parser().parse_args()
    try:
        return _run(args)
    except IngestError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: FEC opposition prior build failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
