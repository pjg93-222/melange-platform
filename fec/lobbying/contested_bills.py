# -*- coding: utf-8 -*-
"""Per-bill contested/advisory registry.

Ratified by Paul 2026-07-31 ("Apply"). Standard mode is the structural
default: an absent registry or record means no second-industry term. Advisory
records are display-only beside the standard ranking and never reorder it.
Contested records supply a per-bill explicit counterparty set, never a global
one. A future consumer dial, CONTESTED_V1, will use 0 to ignore the registry
entirely; no consumer exists today. ``active_from`` defaults to
``ratified_date`` when omitted: ratification activates the record unless a
later start is written. The schema is closed-world by design, so unknown keys
hard-fail and free-text annotations belong in the ``note`` field. Registry
loading is strict about bill spelling while ``mode_for`` lookups are
permissive.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import datetime
import json
import math
import os
import re
import sys

import bill_rules as BR
from lobbying.build_opposition_prior import INDUSTRY_SIDE

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_FILENAME = "contested_bills.json"
MODES = ("advisory", "contested")
ALLOWED_TOP_KEYS = {"_README", "records"}
ALLOWED_RECORD_KEYS = {
    "bill", "congress", "mode", "counterparty_industries", "active_from",
    "active_to", "ratified_by", "ratified_date", "note",
}
REQUIRED_RECORD_KEYS = {
    "bill", "congress", "mode", "counterparty_industries", "ratified_by",
    "ratified_date",
}
STRICT_BILL_RE = re.compile(
    r"\s*[HS]\.?\s*(?:(?:R|J\.?\s*Res|Con\.?\s*Res|Res)\.?\s*)?\d+\s*\Z",
    re.I,
)
AMBIGUOUS_SR_RE = re.compile(r"\s*S\.?\s*R(?!ES)\.?\s*\d", re.I)


class ContestedBillsError(Exception):
    """Registry validation failure."""


def registry_path():
    return os.environ.get("CONTESTED_BILLS_PATH") or os.path.join(
        MB_ROOT, DEFAULT_FILENAME
    )


def _record_error(path, index, message):
    raise ContestedBillsError(f"{path} record {index}: {message}")


def _no_duplicate_keys(pairs):
    seen = set()
    result = {}
    for key, value in pairs:
        if key in seen:
            raise ContestedBillsError(f"duplicate JSON key {key!r}")
        seen.add(key)
        result[key] = value
    return result


def _congress_key(value, context):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContestedBillsError(
            f"{context}: invalid congress {value!r}"
        ) from exc
    if not math.isfinite(number) or number != int(number):
        raise ContestedBillsError(
            f"{context}: congress must be an integral number, got {value!r}"
        )
    return str(int(number))


def _iso_date(value, path, label, index=None):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        prefix = f"{path} record {index}" if index is not None else path
        raise ContestedBillsError(
            f"{prefix}: invalid {label} ISO date {value!r}"
        ) from exc


def load_contested_bills(path=None, today=None):
    path = registry_path() if path is None else path
    today = datetime.date.today() if today is None else today
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle, object_pairs_hook=_no_duplicate_keys)
    except FileNotFoundError:
        return {
            "status": "absent", "path": path, "records": {},
            "active": [], "dormant": [],
        }
    except ContestedBillsError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContestedBillsError(
            f"{path}: registry present but unreadable or malformed: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ContestedBillsError(f"{path}: top level must be an object")
    unknown_top = set(payload) - ALLOWED_TOP_KEYS
    if unknown_top:
        raise ContestedBillsError(
            f"{path}: unknown top-level keys {sorted(unknown_top)!r}"
        )
    source_records = payload.get("records")
    if not isinstance(source_records, list):
        raise ContestedBillsError(f"{path}: records must be a list")

    records = {}
    active = []
    dormant = []
    for index, source in enumerate(source_records):
        if not isinstance(source, dict):
            _record_error(path, index, "must be an object")
        unknown = set(source) - ALLOWED_RECORD_KEYS
        if unknown:
            _record_error(path, index, f"unknown keys {sorted(unknown)!r}")
        missing = REQUIRED_RECORD_KEYS - set(source)
        if missing:
            _record_error(path, index, f"missing required keys {sorted(missing)!r}")

        bill = source["bill"]
        # These guards mirror bill_rules.bill_type_key, but are end-anchored.
        if not isinstance(bill, str) or not STRICT_BILL_RE.match(bill):
            _record_error(
                path, index, f"bill {bill!r} has trailing or unparseable text"
            )
        if AMBIGUOUS_SR_RE.match(bill):
            _record_error(
                path,
                index,
                f"ambiguous spelling {bill!r}: write S.NNN for a bill or "
                "S.Res.NNN for a resolution",
            )
        bill_key = BR.bill_type_key(bill)
        if bill_key is None:
            _record_error(path, index, f"unparseable bill {bill!r}")
        congress = _congress_key(
            source["congress"], f"{path} record {index}"
        )
        congress_number = int(congress)
        if not 100 <= congress_number <= 150:
            _record_error(path, index, f"congress out of range {congress_number!r}")
        mode = source["mode"]
        if mode not in MODES:
            _record_error(path, index, f"invalid mode {mode!r}")

        industries = source["counterparty_industries"]
        if not isinstance(industries, list) or not industries:
            _record_error(
                path, index, "counterparty_industries must be a non-empty list"
            )
        seen_industries = set()
        for industry in industries:
            if not isinstance(industry, str) or industry not in INDUSTRY_SIDE:
                _record_error(path, index, f"unknown industry {industry!r}")
            if INDUSTRY_SIDE[industry] == "ours":
                _record_error(path, index, f"ours-side industry {industry!r}")
            if industry in seen_industries:
                _record_error(path, index, f"duplicate industry {industry!r}")
            seen_industries.add(industry)

        ratified_by = source["ratified_by"]
        if not isinstance(ratified_by, str) or not ratified_by.strip():
            _record_error(path, index, "ratified_by must be a non-empty string")
        ratified_by = ratified_by.strip()
        ratified_date = _iso_date(
            source["ratified_date"], path, "ratified_date", index
        )
        active_from_value = source.get("active_from")
        active_from = (
            ratified_date
            if active_from_value is None
            else _iso_date(active_from_value, path, "active_from", index)
        )
        active_to_value = source.get("active_to")
        active_to = (
            None
            if active_to_value is None
            else _iso_date(active_to_value, path, "active_to", index)
        )
        if active_to is not None and active_to < active_from:
            _record_error(path, index, "active_to is earlier than active_from")

        key = (bill_key, congress)
        if key in records:
            _record_error(path, index, f"duplicate bill/congress key {key!r}")
        is_active = active_from <= today and (
            active_to is None or today <= active_to
        )
        record = {
            "key": key,
            "bill": bill,
            "congress": congress,
            "mode": mode,
            "counterparty_industries": list(industries),
            "active_from": active_from,
            "active_to": active_to,
            "ratified_by": ratified_by,
            "ratified_date": ratified_date,
            "note": str(source.get("note") or ""),
            "active": is_active,
        }
        records[key] = record
        (active if is_active else dormant).append(record)
    return {
        "status": "present", "path": path, "records": records,
        "active": active, "dormant": dormant,
    }


def mode_for(registry, bill, congress):
    """Return mode; manual/letter identifiers are valid and never contested."""
    congress_key = _congress_key(congress, "mode_for")
    bill_key = BR.bill_type_key(bill)
    if bill_key is None:
        return "standard", None
    record = registry.get("records", {}).get((bill_key, congress_key))
    if registry.get("status") == "absent" or record is None:
        return "standard", None
    if not record["active"]:
        return "standard", record
    return record["mode"], record


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect the per-bill contested/advisory registry."
    )
    parser.add_argument("--path", default=registry_path())
    parser.add_argument("--today", default=datetime.date.today().isoformat())
    args = parser.parse_args(argv)
    try:
        today = _iso_date(args.today, args.path, "today")
        registry = load_contested_bills(args.path, today=today)
    except ContestedBillsError as exc:
        print(f"ERROR: {exc}")
        return 2
    if registry["status"] == "absent":
        print(
            "contested registry: absent (standard mode everywhere) | "
            f"path={registry['path']}"
        )
        return 0
    print(
        f"contested registry: {len(registry['records'])} records | "
        f"{len(registry['active'])} active | "
        f"{len(registry['dormant'])} dormant | path={registry['path']}"
    )
    for record in registry["records"].values():
        print(
            f"  {record['bill']} ({record['congress']}) "
            f"mode={record['mode']} "
            f"industries={','.join(record['counterparty_industries'])} "
            f"active={record['active']} ratified={record['ratified_by']} "
            f"{record['ratified_date']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
