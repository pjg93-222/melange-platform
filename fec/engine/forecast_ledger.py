# -*- coding: utf-8 -*-
"""Freeze prospective forecasts and append their once-only score receipts."""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # noqa: E501,E702

import argparse
import collections
import csv
import hashlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FREEZE_SCHEMA = "forecast-freeze-v1"
SCORE_SCHEMA = "forecast-score-v1"
FORECASTERS = ("cosponsor_join", "dyad_persistence")
DYAD_PERSISTENCE_PROBABILITY = 0.82
QUARTERLY_RE = re.compile(r"^our_network_fullscope_q(\d{4})_strict\.csv$")
ANNUAL_RE = re.compile(r"^our_network_fullscope_(\d{4})_strict\.csv$")
VINTAGE_RE = re.compile(r"^(\d{4})Q([1-4])$")


class ForecastLedgerError(RuntimeError):
    """An input or ledger integrity contract failed."""


def _derived_dir():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ForecastLedgerError("LOCALAPPDATA is not set")
    return os.path.join(local, "Melange", "lda_rebuild", "derived")


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise ForecastLedgerError(f"cannot hash input {path}: {exc}") from exc
    return digest.hexdigest()


def _iter_csv(path, required, label):
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ForecastLedgerError(f"cannot read {label} {path}: {exc}") from exc
    try:
        with handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            missing = sorted(set(required) - fields)
            if missing:
                raise ForecastLedgerError(
                    f"{label} {path} missing columns: {', '.join(missing)}"
                )
            for line_number, row in enumerate(reader, 2):
                yield line_number, row
    except ForecastLedgerError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ForecastLedgerError(f"cannot parse {label} {path}: {exc}") from exc


def _validate_csv_header(path, required, label):
    iterator = _iter_csv(path, required, label)
    try:
        next(iterator)
    except StopIteration:
        return
    finally:
        iterator.close()


def _parse_int(value, field, path, line_number):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ForecastLedgerError(
            f"invalid {field} in {path} line {line_number}: {value!r}"
        ) from exc


def _parse_probability(value, field, path, line_number):
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastLedgerError(
            f"invalid {field} in {path} line {line_number}: {value!r}"
        ) from exc
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ForecastLedgerError(
            f"{field} outside [0, 1] in {path} line {line_number}: {value!r}"
        )
    return probability


def _required_text(value, field, path, line_number):
    text = str(value or "").strip()
    if not text:
        raise ForecastLedgerError(
            f"blank {field} in {path} line {line_number}"
        )
    return text


def _latest_quarter(data_root):
    try:
        names = sorted(os.listdir(data_root))
    except OSError as exc:
        raise ForecastLedgerError(f"cannot list data root {data_root}: {exc}") from exc
    latest = None
    for name in names:
        match = QUARTERLY_RE.fullmatch(name)
        if not match:
            continue
        filename_year = int(match.group(1))
        path = os.path.join(data_root, name)
        for line_number, row in _iter_csv(
            path,
            ("lob_id", "bioguide_id", "year", "quarter", "n_bills"),
            "quarterly strict network",
        ):
            year = _parse_int(row.get("year"), "year", path, line_number)
            quarter = _parse_int(row.get("quarter"), "quarter", path, line_number)
            if year != filename_year:
                raise ForecastLedgerError(
                    f"quarterly network filename year {filename_year} disagrees "
                    f"with row year {year} in {path} line {line_number}"
                )
            if quarter not in (1, 2, 3, 4):
                raise ForecastLedgerError(
                    f"invalid quarter in {path} line {line_number}: {quarter}"
                )
            candidate = (year, quarter, os.path.abspath(path))
            if latest is None or candidate[:2] > latest[:2]:
                latest = candidate
    if latest is None:
        raise ForecastLedgerError(
            f"no quarterly strict-network rows found under {data_root}"
        )
    year, quarter, path = latest
    return {
        "year": year,
        "quarter": quarter,
        "vintage": f"{year}Q{quarter}",
        "path": path,
    }


def _annual_network_files(data_root):
    try:
        names = sorted(os.listdir(data_root))
    except OSError as exc:
        raise ForecastLedgerError(f"cannot list data root {data_root}: {exc}") from exc
    paths = {}
    for name in names:
        match = ANNUAL_RE.fullmatch(name)
        if not match:
            continue
        year = int(match.group(1))
        path = os.path.abspath(os.path.join(data_root, name))
        _validate_csv_header(
            path,
            ("lob_id", "bioguide_id", "year", "n_bills"),
            "annual strict network",
        )
        paths[year] = path
    return paths


def _load_dyads(path, expected_year):
    dyads = set()
    for line_number, row in _iter_csv(
        path,
        ("lob_id", "bioguide_id", "year", "n_bills"),
        "annual strict network",
    ):
        year = _parse_int(row.get("year"), "year", path, line_number)
        if year != expected_year:
            raise ForecastLedgerError(
                f"annual network {path} contains year {year} on line "
                f"{line_number}, expected {expected_year}"
            )
        lob_id = _required_text(row.get("lob_id"), "lob_id", path, line_number)
        member = _required_text(
            row.get("bioguide_id"), "bioguide_id", path, line_number
        )
        dyads.add((lob_id, member))
    return dyads


def _input_receipt(path):
    absolute = os.path.abspath(path)
    return {"path": absolute, "sha256": _sha256(absolute)}


def _canonical_bill(congress, display_bill, path, line_number):
    parts = display_bill.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        raise ForecastLedgerError(
            f"invalid score bill in {path} line {line_number}: {display_bill!r}"
        )
    bill_type = parts[0].replace(" ", "").lower()
    return f"{congress}:{bill_type}:{parts[1]}"


def _read_headline_configuration(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ForecastLedgerError(
            f"cannot read cosponsor forecast report {path}: {exc}"
        ) from exc
    headline = report.get("headline_candidate_configuration")
    if not isinstance(headline, str) or not headline.strip():
        raise ForecastLedgerError(
            f"cosponsor forecast report {path} has no headline configuration"
        )
    return headline.strip()


def _cosponsor_freeze(data_root, quarter):
    scores_path = os.path.join(data_root, "cosponsor_sc", "cosponsor_scores.csv")
    report_path = os.path.join(
        data_root, "cosponsor_sc", "cosponsor_forecast_report.json"
    )
    membership_path = os.path.join(
        data_root, "interactions", "interaction_bill_members.csv"
    )
    headline = _read_headline_configuration(report_path)
    by_congress = collections.defaultdict(list)
    seen = set()
    current_congress = None
    for line_number, row in _iter_csv(
        scores_path,
        (
            "candidate_configuration",
            "congress",
            "bill",
            "member",
            "predicted_probability",
            "actual",
        ),
        "cosponsor scores",
    ):
        congress = _parse_int(row.get("congress"), "congress", scores_path, line_number)
        current_congress = (
            congress if current_congress is None else max(current_congress, congress)
        )
        if (row.get("candidate_configuration") or "").strip() != headline:
            continue
        actual = _parse_probability(row.get("actual"), "actual", scores_path, line_number)
        if actual not in (0.0, 1.0):
            raise ForecastLedgerError(
                f"nonbinary actual in {scores_path} line {line_number}: {actual}"
            )
        if actual != 0.0:
            continue
        display_bill = _required_text(row.get("bill"), "bill", scores_path, line_number)
        member = _required_text(row.get("member"), "member", scores_path, line_number)
        bill = _canonical_bill(congress, display_bill, scores_path, line_number)
        probability = _parse_probability(
            row.get("predicted_probability"),
            "predicted_probability",
            scores_path,
            line_number,
        )
        key = (congress, bill, member)
        if key in seen:
            raise ForecastLedgerError(
                f"duplicate headline cosponsor prediction in {scores_path}: {key}"
            )
        seen.add(key)
        by_congress[congress].append(
            {
                "congress": congress,
                "bill": bill,
                "member": member,
                "predicted_probability": probability,
            }
        )
    if current_congress is None:
        raise ForecastLedgerError(f"cosponsor scores are empty: {scores_path}")
    predictions = by_congress.get(current_congress, [])
    if not predictions:
        raise ForecastLedgerError(
            f"no open headline predictions for current Congress {current_congress}"
        )
    predictions.sort(key=lambda row: (row["bill"], row["member"]))
    _validate_csv_header(
        membership_path,
        ("bill_key", "bioguide_id"),
        "interaction bill memberships",
    )
    inputs = {
        "cosponsor_scores": _input_receipt(scores_path),
        "cosponsor_forecast_report": _input_receipt(report_path),
        "resolution_memberships_at_freeze": _input_receipt(membership_path),
        "vintage_quarterly_network": _input_receipt(quarter["path"]),
    }
    return {
        "schema": FREEZE_SCHEMA,
        "forecaster": "cosponsor_join",
        "vintage": quarter["vintage"],
        "frozen_utc": _utc_now(),
        "headline_candidate_configuration": headline,
        "congress": current_congress,
        "inputs": inputs,
        "predictions": predictions,
    }


def _dyad_freeze(data_root, quarter):
    annual = _annual_network_files(data_root)
    source_year = quarter["year"]
    if source_year not in annual:
        raise ForecastLedgerError(
            f"annual strict network for forecast year {source_year} is missing"
        )
    source_path = annual[source_year]
    dyads = _load_dyads(source_path, source_year)
    if not dyads:
        raise ForecastLedgerError(f"annual strict network has no dyads: {source_path}")
    predictions = [
        {
            "lob_id": lob_id,
            "bioguide_id": member,
            "predicted_probability": DYAD_PERSISTENCE_PROBABILITY,
        }
        for lob_id, member in sorted(dyads)
    ]
    inputs = {
        "source_annual_network": _input_receipt(source_path),
        "vintage_quarterly_network": _input_receipt(quarter["path"]),
    }
    return {
        "schema": FREEZE_SCHEMA,
        "forecaster": "dyad_persistence",
        "vintage": quarter["vintage"],
        "frozen_utc": _utc_now(),
        "source_year": source_year,
        "target_year": source_year + 1,
        "inputs": inputs,
        "predictions": predictions,
    }


def _freeze_path(data_root, forecaster, vintage):
    return os.path.join(
        data_root, "forecast_ledger", "freezes", forecaster, f"{vintage}.json"
    )


def _write_freeze_atomic(payload, path):
    if os.path.exists(path):
        return False
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    temporary = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            handle.write(body)
        if os.path.exists(path):
            return False
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return True


def _load_freeze(path, expected_forecaster):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ForecastLedgerError(f"cannot read freeze {path}: {exc}") from exc
    if payload.get("schema") != FREEZE_SCHEMA:
        raise ForecastLedgerError(f"unexpected freeze schema in {path}")
    if payload.get("forecaster") != expected_forecaster:
        raise ForecastLedgerError(f"freeze forecaster/path mismatch in {path}")
    vintage = payload.get("vintage")
    if not isinstance(vintage, str) or not VINTAGE_RE.fullmatch(vintage):
        raise ForecastLedgerError(f"invalid freeze vintage in {path}: {vintage!r}")
    if os.path.basename(path) != f"{vintage}.json":
        raise ForecastLedgerError(f"freeze vintage/filename mismatch in {path}")
    if not isinstance(payload.get("frozen_utc"), str):
        raise ForecastLedgerError(f"freeze has no frozen_utc in {path}")
    if not isinstance(payload.get("inputs"), dict):
        raise ForecastLedgerError(f"freeze has no input receipts in {path}")
    for label, receipt in payload["inputs"].items():
        if (
            not isinstance(label, str)
            or not isinstance(receipt, dict)
            or not isinstance(receipt.get("path"), str)
            or not isinstance(receipt.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"])
        ):
            raise ForecastLedgerError(
                f"invalid input receipt {label!r} in freeze {path}"
            )
    if not isinstance(payload.get("predictions"), list):
        raise ForecastLedgerError(f"freeze has no predictions list in {path}")
    if any(not isinstance(row, dict) for row in payload["predictions"]):
        raise ForecastLedgerError(f"invalid prediction row in freeze {path}")
    return payload


def _ledger_keys(path):
    keys = set()
    if not os.path.exists(path):
        return keys
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    raise ForecastLedgerError(
                        f"blank line in append-only ledger {path} line {line_number}"
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ForecastLedgerError(
                        f"invalid JSON in ledger {path} line {line_number}: {exc}"
                    ) from exc
                if not isinstance(record, dict) or record.get("schema") != SCORE_SCHEMA:
                    raise ForecastLedgerError(
                        f"invalid scoring schema in ledger {path} line {line_number}"
                    )
                key = (record.get("forecaster"), record.get("vintage"))
                if (
                    key[0] not in FORECASTERS
                    or not isinstance(key[1], str)
                    or not VINTAGE_RE.fullmatch(key[1])
                ):
                    raise ForecastLedgerError(
                        f"invalid scoring key in ledger {path} line {line_number}"
                    )
                if key in keys:
                    raise ForecastLedgerError(
                        f"duplicate scoring key in ledger {path}: {key}"
                    )
                keys.add(key)
    except ForecastLedgerError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ForecastLedgerError(f"cannot read ledger {path}: {exc}") from exc
    return keys


def _prediction_probability(prediction, freeze_path, index):
    return _parse_probability(
        prediction.get("predicted_probability"),
        "predicted_probability",
        freeze_path,
        index + 1,
    )


def _strict_date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ForecastLedgerError(f"{label} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ForecastLedgerError(f"invalid {label}: {value!r}") from exc


def _resolve_cosponsor(freeze, freeze_path, data_root, resolution_after=None):
    # Refresh diagnostics must not consume a once-only score before its horizon
    # has been chosen explicitly. Existing freezes and receipts remain immutable.
    if resolution_after is None:
        return None, None, "cosponsor scoring deferred: no explicit resolution date"
    resolution_date = _strict_date(resolution_after, "cosponsor resolution date")
    try:
        frozen = datetime.fromisoformat(freeze["frozen_utc"].replace("Z", "+00:00"))
        if frozen.tzinfo is None or frozen.utcoffset() is None:
            raise ValueError("freeze timestamp is not timezone-aware")
        frozen_date = frozen.astimezone(timezone.utc).date()
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ForecastLedgerError(f"invalid cosponsor freeze timestamp: {freeze_path}") from exc
    if resolution_date <= frozen_date:
        # Non-prospective for THIS freeze: skip it (never score it) and let the loop continue, so one
        # later freeze cannot abort scoring of the others or of the dyad forecaster.
        return None, None, (
            f"cosponsor freeze skipped: earliest execution date {resolution_after} is not after "
            f"its freeze date {frozen_date.isoformat()} (non-prospective)"
        )
    today = datetime.fromisoformat(_utc_now()).astimezone(timezone.utc).date()
    if today < resolution_date:
        return None, None, f"cosponsor scoring deferred until {resolution_after} UTC"
    try:
        frozen_hash = freeze["inputs"]["resolution_memberships_at_freeze"][
            "sha256"
        ]
    except (KeyError, TypeError) as exc:
        raise ForecastLedgerError(
            f"cosponsor freeze lacks membership snapshot hash: {freeze_path}"
        ) from exc
    membership_path = os.path.join(
        data_root, "interactions", "interaction_bill_members.csv"
    )
    current_hash = _sha256(membership_path)
    if current_hash == frozen_hash:
        return None, None, "no new information has arrived"
    memberships = set()
    for line_number, row in _iter_csv(
        membership_path,
        ("bill_key", "bioguide_id", "sponsorship_date"),
        "interaction bill memberships",
    ):
        bill = _required_text(row.get("bill_key"), "bill_key", membership_path, line_number)
        member = _required_text(row.get("bioguide_id"), "bioguide_id", membership_path, line_number)
        joined = _strict_date(
            row.get("sponsorship_date"),
            f"sponsorship_date in {membership_path} line {line_number}",
        )
        # Source dates have day precision. Same-day joins cannot establish that
        # they occurred after the frozen timestamp, so exclude them conservatively.
        if joined > frozen_date:
            memberships.add((bill, member))
    outcomes = []
    for index, prediction in enumerate(freeze["predictions"]):
        bill = _required_text(
            prediction.get("bill"), "bill", freeze_path, index + 1
        )
        member = _required_text(
            prediction.get("member"), "member", freeze_path, index + 1
        )
        congress = _parse_int(
            prediction.get("congress"), "congress", freeze_path, index + 1
        )
        if not bill.startswith(f"{congress}:") or len(bill.split(":")) != 3:
            raise ForecastLedgerError(
                f"noncanonical bill in {freeze_path} prediction {index + 1}: {bill}"
            )
        _prediction_probability(prediction, freeze_path, index)
        outcomes.append(1 if (bill, member) in memberships else 0)
    resolution = {
        "path": os.path.abspath(membership_path),
        "sha256": current_hash,
        "resolution_after": resolution_after,
        "membership_date_rule": "strictly_after_freeze_utc_date",
    }
    return outcomes, resolution, None


def _resolve_dyad(freeze, freeze_path, data_root):
    source_year = _parse_int(
        freeze.get("source_year"), "source_year", freeze_path, 1
    )
    target_year = _parse_int(
        freeze.get("target_year"), "target_year", freeze_path, 1
    )
    if target_year != source_year + 1:
        raise ForecastLedgerError(f"invalid dyad resolution horizon in {freeze_path}")
    annual = _annual_network_files(data_root)
    if target_year not in annual:
        return None, None, f"resolution year {target_year} is not present"
    current_year = max(annual) if annual else None
    if current_year is None or target_year >= current_year:
        return (
            None,
            None,
            f"partial-year guard: {target_year} is not older than newest year {current_year}",
        )
    resolution_path = annual[target_year]
    resolution_dyads = _load_dyads(resolution_path, target_year)
    outcomes = []
    for index, prediction in enumerate(freeze["predictions"]):
        lob_id = _required_text(
            prediction.get("lob_id"), "lob_id", freeze_path, index + 1
        )
        member = _required_text(
            prediction.get("bioguide_id"),
            "bioguide_id",
            freeze_path,
            index + 1,
        )
        _prediction_probability(prediction, freeze_path, index)
        outcomes.append(1 if (lob_id, member) in resolution_dyads else 0)
    resolution = _input_receipt(resolution_path)
    resolution["current_network_year"] = current_year
    return outcomes, resolution, None


def _calibration_deciles(probabilities, outcomes):
    buckets = collections.defaultdict(list)
    for probability, outcome in zip(probabilities, outcomes):
        decile = min(9, int(probability * 10.0))
        buckets[decile].append(outcome)
    return {
        str(decile): [len(values), sum(values) / len(values)]
        for decile, values in sorted(buckets.items())
    }


def _score_record(freeze, freeze_path, outcomes, resolution):
    probabilities = [
        _prediction_probability(prediction, freeze_path, index)
        for index, prediction in enumerate(freeze["predictions"])
    ]
    if not probabilities:
        raise ForecastLedgerError(f"cannot score an empty freeze: {freeze_path}")
    if len(probabilities) != len(outcomes):
        raise ForecastLedgerError(f"outcome count mismatch for freeze {freeze_path}")
    n = len(outcomes)
    resolved_true = sum(outcomes)
    return {
        "schema": SCORE_SCHEMA,
        "forecaster": freeze["forecaster"],
        "vintage": freeze["vintage"],
        "n": n,
        "resolved_true": resolved_true,
        "brier": sum(
            (probability - outcome) ** 2
            for probability, outcome in zip(probabilities, outcomes)
        )
        / n,
        "base_rate": resolved_true / n,
        "calibration_deciles": _calibration_deciles(probabilities, outcomes),
        "scored_utc": _utc_now(),
        "freeze_hash": _sha256(freeze_path),
        "resolution_source": resolution,
    }


def _append_ledger_record(path, record):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    line = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    try:
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(line)
    except OSError as exc:
        raise ForecastLedgerError(f"cannot append ledger {path}: {exc}") from exc


def _score_unscored_freezes(data_root, cosponsor_resolution_after=None):
    ledger_path = os.path.join(data_root, "forecast_ledger", "ledger.jsonl")
    scored_keys = _ledger_keys(ledger_path)
    scored = []
    notes = []
    for forecaster in FORECASTERS:
        freeze_dir = os.path.join(
            data_root, "forecast_ledger", "freezes", forecaster
        )
        if not os.path.isdir(freeze_dir):
            continue
        for name in sorted(os.listdir(freeze_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(freeze_dir, name)
            freeze = _load_freeze(path, forecaster)
            key = (forecaster, freeze["vintage"])
            if key in scored_keys:
                continue
            if forecaster == "cosponsor_join":
                outcomes, resolution, reason = _resolve_cosponsor(
                    freeze, path, data_root, cosponsor_resolution_after
                )
            else:
                outcomes, resolution, reason = _resolve_dyad(
                    freeze, path, data_root
                )
            if reason is not None:
                notes.append(f"{forecaster} {freeze['vintage']}: {reason}")
                continue
            record = _score_record(freeze, path, outcomes, resolution)
            _append_ledger_record(ledger_path, record)
            scored_keys.add(key)
            scored.append(record)
    return scored, notes


def run(data_root, cosponsor_resolution_after=None):
    data_root = os.path.abspath(os.fspath(data_root))
    if cosponsor_resolution_after is not None:
        _strict_date(cosponsor_resolution_after, "cosponsor resolution date")
    quarter = _latest_quarter(data_root)
    created = 0
    notes = []
    builders = (
        ("cosponsor_join", _cosponsor_freeze),
        ("dyad_persistence", _dyad_freeze),
    )
    for forecaster, builder in builders:
        path = _freeze_path(data_root, forecaster, quarter["vintage"])
        if os.path.exists(path):
            notes.append(f"freeze exists: {forecaster} {quarter['vintage']}")
            continue
        payload = builder(data_root, quarter)
        if _write_freeze_atomic(payload, path):
            created += 1
        else:
            notes.append(f"freeze exists: {forecaster} {quarter['vintage']}")
    scored, score_notes = _score_unscored_freezes(data_root, cosponsor_resolution_after)
    return created, scored, notes + score_notes


def _brier_text(value):
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0.") else text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        default=None,
        help="derived-data root (defaults to LOCALAPPDATA Melange data)",
    )
    parser.add_argument(
        "--cosponsor-resolution-after",
        default=None,
        metavar="YYYY-MM-DD",
        help="explicit earliest UTC scoring date; unset defers cosponsor scores without changing other forecasters",
    )
    args = parser.parse_args(argv)
    created, scored, notes = run(args.data_root or _derived_dir(), args.cosponsor_resolution_after)
    details = " ".join(
        f"{record['forecaster']} {record['vintage']} Brier "
        f"{_brier_text(record['brier'])} (n={record['n']})"
        for record in scored
    ) or "none"
    summary = (
        f"forecast ledger: froze {created} vintages | "
        f"scored {len(scored)}: {details}"
    )
    if notes:
        summary += " | " + "; ".join(notes)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
