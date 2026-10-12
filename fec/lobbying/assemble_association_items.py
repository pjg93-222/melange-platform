# -*- coding: utf-8 -*-
"""Mechanically assemble 2023--2024 association bill-position work items."""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import csv
import hashlib
import json
import os
import sys

from lobbying import build_network_2021_rebuild as builder
from lobbying import classify_bill_positions as classifier
from lobbying import client_identity_harvest as identity_harvest


YEARS = (2023, 2024)
STRATA = ("credit_union", "bank")
DIRECTIONAL_LABELS = frozenset({"Support", "Oppose"})
KIM_LABELS = frozenset({"Support", "Oppose", "Amend", "Monitor", "Engage"})
CLASSIFIED_FIELDS = frozenset({"lob_id", "client_name", "side"})
KIM_FIELDS = frozenset(
    {
        "lob_id",
        "client_name",
        "bill_id",
        "label_3_class",
        "label_5_class",
        "label",
        "annotation_source",
    }
)

# These four fields are added by lobbying.classify_bill_positions.calibration_join from
# the Kim reference row.  They are not source work-item fields and therefore
# cannot be populated for new 2023--2024 bill pairs.
GOLD_FIELDS = frozenset(
    {
        "their_client_name",
        "their_label_3_class",
        "their_label_5_class",
        "annotation_source",
    }
)
BASE_WORK_FIELDS = frozenset(
    {
        "lob_id",
        "client_name",
        "bill_key",
        "bill_no",
        "short_title",
        "official_title",
        "official_title_tier",
        "paragraph",
        "report_uuid",
        "filing_year",
        "quarter",
    }
)
SAMPLE_LIMIT = 5
NAME_PLAUSIBILITY_STOPWORDS = identity_harvest.NAME_PLAUSIBILITY_STOPWORDS
NAME_PLAUSIBILITY_FAILED_RULE = (
    identity_harvest.NAME_PLAUSIBILITY_FAILED_RULE
)
ID_REJECTION_FIELDS = identity_harvest.ID_REJECTION_FIELDS
ID_CROSSWALK_FIELDS = frozenset(
    {
        "stratum",
        "lobbyview_name",
        "client_ids",
        "accepted_client_ids",
        "rejected_client_ids",
        "filing_names_seen",
        "uuids_present",
        "uuids_missing",
    }
)


def normalize_association_name(value):
    """Apply the fixed association-to-sweep name normalization."""
    if not isinstance(value, str):
        raise builder.IngestError("association name must be a string")
    collapsed = " ".join(value.upper().split()).strip()
    return collapsed.rstrip(".,")


def tokenize_identity_name(value):
    """Return the fixed non-generic token set for ID plausibility checks."""
    return identity_harvest.tokenize_identity_name(
        value, error_type=builder.IngestError
    )


def _name_plausibility(lobbyview_name, filing_client_name):
    return identity_harvest.name_plausibility(
        lobbyview_name,
        filing_client_name,
        error_type=builder.IngestError,
    )


def _require_file(path, label):
    if not os.path.isfile(path):
        raise builder.IngestError(f"missing {label}: {path}")


def _require_directory(path, label):
    if not os.path.isdir(path):
        raise builder.IngestError(f"missing {label}: {path}")


def _require_columns(reader, required, label):
    observed = set(reader.fieldnames or ())
    missing = sorted(required - observed)
    if missing:
        raise builder.IngestError(
            f"{label} is missing required columns: {', '.join(missing)}"
        )


def _required_cell(row, field, label, row_number):
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise builder.IngestError(
            f"{label} row {row_number} has blank {field}"
        )
    return value.strip()


def _read_classified(path):
    names_by_lob = collections.defaultdict(set)
    strata_by_lob = collections.defaultdict(set)
    row_count = 0
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise builder.IngestError(
            f"cannot read classified clients {path}: {exc}"
        ) from exc
    with handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, CLASSIFIED_FIELDS, "classified clients")
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise builder.IngestError(
                    f"classified clients row {row_number} has extra columns"
                )
            row_count += 1
            lob_id = _required_cell(
                row, "lob_id", "classified clients", row_number
            )
            client_name = _required_cell(
                row, "client_name", "classified clients", row_number
            )
            side = _required_cell(
                row, "side", "classified clients", row_number
            ).casefold()
            if side in STRATA:
                names_by_lob[lob_id].add(client_name)
                strata_by_lob[lob_id].add(side)
    ambiguous = sorted(
        lob_id for lob_id, values in strata_by_lob.items() if len(values) != 1
    )
    if ambiguous:
        raise builder.IngestError(
            "classified clients assign a lob_id to multiple association "
            f"strata: first={ambiguous[0]}"
        )
    return names_by_lob, strata_by_lob, row_count


def _read_kim_counts(path):
    total = collections.Counter()
    directional = collections.Counter()
    seen_pairs = set()
    row_count = 0
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise builder.IngestError(
            f"cannot read Kim position dataset {path}: {exc}"
        ) from exc
    with handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, KIM_FIELDS, "Kim position dataset")
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise builder.IngestError(
                    f"Kim position dataset row {row_number} has extra columns"
                )
            row_count += 1
            lob_id = _required_cell(
                row, "lob_id", "Kim position dataset", row_number
            )
            bill_id = _required_cell(
                row, "bill_id", "Kim position dataset", row_number
            )
            label = _required_cell(
                row, "label_5_class", "Kim position dataset", row_number
            )
            canonical = {value.casefold(): value for value in KIM_LABELS}.get(
                label.casefold()
            )
            if canonical is None:
                raise builder.IngestError(
                    "Kim position dataset row "
                    f"{row_number} has invalid label_5_class {label!r}"
                )
            pair = (lob_id, bill_id.casefold())
            if pair in seen_pairs:
                raise builder.IngestError(
                    "duplicate (lob_id, bill_id) in Kim position dataset at "
                    f"row {row_number}"
                )
            seen_pairs.add(pair)
            total[lob_id] += 1
            if canonical in DIRECTIONAL_LABELS:
                directional[lob_id] += 1
    return total, directional, row_count


def load_association_sets(classified_csv, kim_csv, min_directional=5):
    """Return fixed CU/bank association strata and the selection receipt."""
    if isinstance(min_directional, bool) or not isinstance(min_directional, int):
        raise builder.IngestError("min_directional must be an integer")
    if min_directional < 1:
        raise builder.IngestError("min_directional must be at least 1")
    _require_file(classified_csv, "classified clients CSV")
    _require_file(kim_csv, "Kim position CSV")
    names_by_lob, strata_by_lob, classified_rows = _read_classified(
        classified_csv
    )
    total, directional, kim_rows = _read_kim_counts(kim_csv)

    association_sets = {stratum: [] for stratum in STRATA}
    excluded = {stratum: [] for stratum in STRATA}
    for lob_id in sorted(strata_by_lob):
        stratum = next(iter(strata_by_lob[lob_id]))
        entry = {
            "lob_id": lob_id,
            "client_names": sorted(names_by_lob[lob_id]),
            "directional_count": directional[lob_id],
            "total_position_count": total[lob_id],
        }
        if directional[lob_id] >= min_directional:
            association_sets[stratum].append(entry)
        else:
            excluded[stratum].append(entry)

    classified_lob_ids = set(strata_by_lob)
    kim_without_relevant_classification = sorted(set(total) - classified_lob_ids)
    receipt = {
        "classified_rows": classified_rows,
        "kim_rows": kim_rows,
        "min_directional": min_directional,
        "relevant_classified_lob_ids": len(classified_lob_ids),
        "excluded_below_min_directional": excluded,
        "kim_lob_ids_without_cu_or_bank_classification": {
            "count": len(kim_without_relevant_classification),
            "lob_id_samples": kim_without_relevant_classification[:SAMPLE_LIMIT],
        },
    }
    return association_sets, receipt


def _association_indexes(association_sets):
    by_key = {}
    by_name = {}
    for stratum in STRATA:
        rows = association_sets.get(stratum)
        if not isinstance(rows, list):
            raise builder.IngestError(
                f"association set {stratum} must be a list"
            )
        for entry in rows:
            if not isinstance(entry, dict):
                raise builder.IngestError(
                    f"association set {stratum} contains a non-object"
                )
            lob_id = entry.get("lob_id")
            names = entry.get("client_names")
            if not isinstance(lob_id, str) or not lob_id.strip():
                raise builder.IngestError(
                    f"association set {stratum} contains an invalid lob_id"
                )
            if not isinstance(names, list) or not names:
                raise builder.IngestError(
                    f"association {lob_id} has no client_names"
                )
            key = (stratum, lob_id)
            if key in by_key:
                raise builder.IngestError(
                    f"duplicate association set entry {stratum}/{lob_id}"
                )
            by_key[key] = entry
            for name in names:
                normalized = normalize_association_name(name)
                if not normalized:
                    raise builder.IngestError(
                        f"association {lob_id} has an empty normalized name"
                    )
                prior = by_name.get(normalized)
                if prior is not None and prior != key:
                    raise builder.IngestError(
                        "normalized association name maps to multiple lob_ids: "
                        f"{normalized!r}"
                    )
                by_name[normalized] = key
    return by_key, by_name


def load_harvest_seed(path):
    """Validate the data-derived LobbyView filing UUID seed."""
    _require_file(path, "identity harvest seed")
    seed = builder._decode_json(
        builder._read_bytes(path, "identity harvest seed"),
        f"identity harvest seed {path}",
    )
    if not isinstance(seed, dict) or not seed:
        raise builder.IngestError("identity harvest seed must be a nonempty object")
    normalized = {}
    uuid_owners = {}
    for lob_id in sorted(seed):
        entry = seed[lob_id]
        if not isinstance(lob_id, str) or not lob_id.strip():
            raise builder.IngestError("identity harvest seed has an invalid lob_id")
        if not isinstance(entry, dict):
            raise builder.IngestError(
                f"identity harvest seed {lob_id} is not an object"
            )
        required = {"stratum", "name", "uuids", "total"}
        if set(entry) != required:
            raise builder.IngestError(
                f"identity harvest seed {lob_id} fields are not {sorted(required)}"
            )
        stratum = entry["stratum"]
        name = entry["name"]
        uuids = entry["uuids"]
        total = entry["total"]
        if stratum not in STRATA:
            raise builder.IngestError(
                f"identity harvest seed {lob_id} has invalid stratum"
            )
        if not isinstance(name, str) or not name.strip():
            raise builder.IngestError(
                f"identity harvest seed {lob_id} has invalid name"
            )
        if not isinstance(uuids, list) or len(uuids) > 6:
            raise builder.IngestError(
                f"identity harvest seed {lob_id} uuids must be a list of at most 6"
            )
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < len(uuids)
        ):
            raise builder.IngestError(
                f"identity harvest seed {lob_id} has invalid total"
            )
        clean_uuids = []
        for filing_uuid in uuids:
            if (
                not isinstance(filing_uuid, str)
                or builder.UUID_RE.fullmatch(filing_uuid) is None
            ):
                raise builder.IngestError(
                    f"identity harvest seed {lob_id} has invalid filing UUID"
                )
            if filing_uuid in clean_uuids:
                raise builder.IngestError(
                    f"identity harvest seed {lob_id} repeats {filing_uuid}"
                )
            prior = uuid_owners.get(filing_uuid)
            if prior is not None and prior != lob_id:
                raise builder.IngestError(
                    "identity harvest seed filing UUID maps to multiple lob_ids: "
                    f"{filing_uuid} -> {prior}, {lob_id}"
                )
            uuid_owners[filing_uuid] = lob_id
            clean_uuids.append(filing_uuid)
        normalized[lob_id] = {
            "stratum": stratum,
            "name": name.strip(),
            "uuids": tuple(clean_uuids),
            "total": total,
        }
    return normalized


def _seeded_cache_paths(filings_root, filing_uuids):
    """Locate seed UUIDs using the collector/builder year partition layout."""
    _require_directory(filings_root, "partitioned filings cache")
    try:
        year_names = sorted(
            name
            for name in os.listdir(filings_root)
            if name.isdigit()
            and os.path.isdir(os.path.join(filings_root, name))
        )
    except OSError as exc:
        raise builder.IngestError(
            f"cannot list partitioned filings cache {filings_root}: {exc}"
        ) from exc
    if not year_names:
        raise builder.IngestError(
            f"partitioned filings cache has no year directories: {filings_root}"
        )
    cache_paths = {
        "partitioned_filings_dir": filings_root,
        "filings_dir": os.path.join(filings_root, "__unused_legacy__"),
    }
    located = {}
    for year_name in year_names:
        filing_year = int(year_name)
        for filing_uuid in filing_uuids:
            record = {
                "report_uuid": filing_uuid,
                "filing_year": filing_year,
            }
            partitioned_path = builder.filing_paths_for_record(
                cache_paths, record
            )[0]
            if not os.path.isfile(partitioned_path):
                continue
            prior = located.get(filing_uuid)
            if prior is not None:
                raise builder.IngestError(
                    "seed filing UUID appears in multiple cache partitions: "
                    f"{filing_uuid}"
                )
            located[filing_uuid] = (filing_year, partitioned_path)
    return located


def _integer_global_client_id(value, filing_uuid):
    if not isinstance(value, str) or not value.isdecimal():
        raise builder.IngestError(
            f"filing {filing_uuid} client.id is not a decimal integer"
        )
    result = int(value)
    if result <= 0:
        raise builder.IngestError(
            f"filing {filing_uuid} client.id must be positive"
        )
    return result


def harvest_id_crosswalk(seed_path, filings_root):
    """Harvest and name-guard global IDs from cached seed filings only."""
    seed = load_harvest_seed(seed_path)
    all_uuids = {
        filing_uuid
        for entry in seed.values()
        for filing_uuid in entry["uuids"]
    }
    cached = _seeded_cache_paths(filings_root, all_uuids)

    def load_identity(_lob_id, filing_uuid):
        located = cached.get(filing_uuid)
        if located is None:
            return None
        filing_year, filing_path = located
        if filing_year >= min(YEARS):
            raise builder.IngestError(
                f"identity seed filing {filing_uuid} is not pre-2023"
            )
        payload = builder._decode_json(
            builder._read_bytes(filing_path, f"filing {filing_uuid}"),
            f"filing {filing_uuid}",
        )
        observed_uuid = builder._validate_sweep_filing(
            payload, filing_year, os.path.basename(filing_path), 0
        )
        if observed_uuid != filing_uuid:
            raise builder.IngestError(
                f"cached filing {filing_path} UUID does not match its filename"
            )
        # _validated_client returns both identifiers.  Only its first value,
        # global Senate client.id, is harvested.  The second is registrant-
        # scoped and is never used for identity attribution.
        senate_client_id, _registrant_scoped_id, client_name = (
            builder._validated_client(payload, filing_uuid)
        )
        return (
            _integer_global_client_id(senate_client_id, filing_uuid),
            client_name,
        )

    return identity_harvest.harvest_guarded_client_ids(
        seed,
        load_identity,
        error_type=builder.IngestError,
        owner_label="associations",
    )


def load_id_crosswalk(path, association_sets=None):
    """Load and validate harvested global client.id ownership."""
    _require_file(path, "identity client.id crosswalk")
    value = builder._decode_json(
        builder._read_bytes(path, "identity client.id crosswalk"),
        f"identity client.id crosswalk {path}",
    )
    if not isinstance(value, dict) or not value:
        raise builder.IngestError(
            "identity client.id crosswalk must be a nonempty object"
        )
    normalized = {}
    client_id_owners = {}
    for lob_id in sorted(value):
        entry = value[lob_id]
        if not isinstance(lob_id, str) or not lob_id.strip():
            raise builder.IngestError(
                "identity client.id crosswalk has an invalid lob_id"
            )
        if not isinstance(entry, dict) or set(entry) != ID_CROSSWALK_FIELDS:
            raise builder.IngestError(
                f"identity client.id crosswalk {lob_id} has invalid fields"
            )
        if entry["stratum"] not in STRATA:
            raise builder.IngestError(
                f"identity client.id crosswalk {lob_id} has invalid stratum"
            )
        if (
            not isinstance(entry["lobbyview_name"], str)
            or not entry["lobbyview_name"].strip()
        ):
            raise builder.IngestError(
                f"identity client.id crosswalk {lob_id} has invalid lobbyview_name"
            )
        client_ids = entry["client_ids"]
        accepted_client_ids = entry["accepted_client_ids"]
        rejected_client_ids = entry["rejected_client_ids"]
        names = entry["filing_names_seen"]
        if (
            not isinstance(client_ids, list)
            or any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or item <= 0
                for item in client_ids
            )
            or client_ids != sorted(set(client_ids))
        ):
            raise builder.IngestError(
                f"identity client.id crosswalk {lob_id} has invalid client_ids"
            )
        if (
            not isinstance(accepted_client_ids, list)
            or any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or item <= 0
                for item in accepted_client_ids
            )
            or accepted_client_ids != sorted(set(accepted_client_ids))
            or client_ids != accepted_client_ids
        ):
            raise builder.IngestError(
                "identity client.id crosswalk "
                f"{lob_id} has invalid accepted_client_ids"
            )
        if (
            not isinstance(names, list)
            or any(not isinstance(item, str) or not item.strip() for item in names)
            or names != sorted(set(names))
        ):
            raise builder.IngestError(
                f"identity client.id crosswalk {lob_id} has invalid filing_names_seen"
            )
        if not isinstance(rejected_client_ids, list):
            raise builder.IngestError(
                "identity client.id crosswalk "
                f"{lob_id} rejected_client_ids is not a list"
            )
        rejection_keys = []
        for rejection in rejected_client_ids:
            if (
                not isinstance(rejection, dict)
                or set(rejection) != ID_REJECTION_FIELDS
            ):
                raise builder.IngestError(
                    "identity client.id crosswalk "
                    f"{lob_id} has an invalid rejection receipt"
                )
            rejected_id = rejection["client_id"]
            filing_client_name = rejection["filing_client_name"]
            if (
                isinstance(rejected_id, bool)
                or not isinstance(rejected_id, int)
                or rejected_id <= 0
                or rejected_id in accepted_client_ids
                or rejection["lobbyview_name"] != entry["lobbyview_name"]
                or not isinstance(filing_client_name, str)
                or not filing_client_name.strip()
                or filing_client_name not in names
            ):
                raise builder.IngestError(
                    "identity client.id crosswalk "
                    f"{lob_id} has an invalid rejected client.id receipt"
                )
            (
                plausible,
                failed_rule,
                lobbyview_tokens,
                filing_name_tokens,
            ) = _name_plausibility(entry["lobbyview_name"], filing_client_name)
            if (
                plausible
                or failed_rule != NAME_PLAUSIBILITY_FAILED_RULE
                or rejection["failed_rule"] != failed_rule
                or rejection["lobbyview_tokens"] != sorted(lobbyview_tokens)
                or rejection["filing_name_tokens"]
                != sorted(filing_name_tokens)
            ):
                raise builder.IngestError(
                    "identity client.id crosswalk "
                    f"{lob_id} has an invalid name-plausibility receipt"
                )
            rejection_keys.append((rejected_id, filing_client_name))
        if rejection_keys != sorted(set(rejection_keys)):
            raise builder.IngestError(
                "identity client.id crosswalk "
                f"{lob_id} has unsorted or duplicate rejection receipts"
            )
        for count_field in ("uuids_present", "uuids_missing"):
            count = entry[count_field]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise builder.IngestError(
                    f"identity client.id crosswalk {lob_id} has invalid {count_field}"
                )
        for client_id in client_ids:
            prior = client_id_owners.get(client_id)
            if prior is not None and prior != lob_id:
                raise builder.IngestError(
                    "identity client.id maps to multiple associations: "
                    f"{client_id} -> {prior}, {lob_id}"
                )
            client_id_owners[client_id] = lob_id
        normalized[lob_id] = {
            "stratum": entry["stratum"],
            "lobbyview_name": entry["lobbyview_name"].strip(),
            "client_ids": list(client_ids),
            "accepted_client_ids": list(accepted_client_ids),
            "rejected_client_ids": [
                dict(rejection) for rejection in rejected_client_ids
            ],
            "filing_names_seen": list(names),
            "uuids_present": entry["uuids_present"],
            "uuids_missing": entry["uuids_missing"],
        }

    if association_sets is not None:
        expected = {
            entry["lob_id"]: stratum
            for stratum in STRATA
            for entry in association_sets[stratum]
        }
        missing = sorted(set(expected) - set(normalized))
        extra = sorted(set(normalized) - set(expected))
        if missing or extra:
            raise builder.IngestError(
                "identity client.id crosswalk association scope mismatch: "
                f"missing={missing[:SAMPLE_LIMIT]}, extra={extra[:SAMPLE_LIMIT]}"
            )
        for lob_id, stratum in expected.items():
            if normalized[lob_id]["stratum"] != stratum:
                raise builder.IngestError(
                    f"identity client.id crosswalk stratum mismatch for {lob_id}"
                )
    return normalized


def _read_sweep_page(page_path, filing_year):
    page_name = os.path.basename(page_path)
    page = builder._decode_json(
        builder._read_bytes(page_path, f"sweep {filing_year} page"),
        f"sweep {filing_year} {page_name}",
    )
    if not isinstance(page, dict):
        raise builder.IngestError(
            f"sweep {filing_year} {page_name} is not an object"
        )
    if "results" not in page or not isinstance(page["results"], list):
        raise builder.IngestError(
            f"sweep {filing_year} {page_name} missing results list"
        )
    return page_name, page["results"]


def scan_sweeps(sweeps_root, association_sets, id_crosswalk):
    """Union guarded global-client-ID and exact-name attribution."""
    _require_directory(sweeps_root, "sweeps root")
    by_key, by_name = _association_indexes(association_sets)
    if not isinstance(id_crosswalk, dict):
        raise builder.IngestError("identity client.id crosswalk is not an object")
    expected_lob_ids = {lob_id for _stratum, lob_id in by_key}
    if set(id_crosswalk) != expected_lob_ids:
        raise builder.IngestError(
            "identity client.id crosswalk does not match association scope"
        )
    client_id_owner = {}
    for association_key in sorted(by_key):
        stratum, lob_id = association_key
        entry = id_crosswalk[lob_id]
        if not isinstance(entry, dict) or entry.get("stratum") != stratum:
            raise builder.IngestError(
                f"identity client.id crosswalk is invalid for {lob_id}"
            )
        client_ids_for_lob = entry.get("accepted_client_ids")
        if not isinstance(client_ids_for_lob, list):
            raise builder.IngestError(
                "identity client.id crosswalk "
                f"{lob_id} accepted_client_ids is not a list"
            )
        for client_id in client_ids_for_lob:
            if (
                isinstance(client_id, bool)
                or not isinstance(client_id, int)
                or client_id <= 0
            ):
                raise builder.IngestError(
                    f"identity client.id crosswalk {lob_id} has invalid client_id"
                )
            prior = client_id_owner.get(client_id)
            if prior is not None and prior != association_key:
                raise builder.IngestError(
                    "identity client.id maps to multiple associations: "
                    f"{client_id}"
                )
            client_id_owner[client_id] = association_key
    client_ids = collections.defaultdict(set)
    matched_names = collections.defaultdict(set)
    id_matched_names = collections.defaultdict(set)
    id_only_names = collections.defaultdict(set)
    channel_counts = collections.defaultdict(collections.Counter)
    matched_counts = collections.defaultdict(collections.Counter)
    matched_filings = []
    seen_uuids = set()
    first_body_digest_by_uuid = {}
    duplicate_uuids = set()
    duplicate_body_mismatches = set()
    duplicate_occurrences_by_year = collections.Counter()
    raw_occurrences_by_year = collections.Counter()
    pages_by_year = collections.Counter()
    unmatched_by_year = collections.Counter()
    unmatched_samples = []

    for filing_year in YEARS:
        year_dir = os.path.join(sweeps_root, str(filing_year))
        _require_directory(year_dir, f"{filing_year} sweep directory")
        page_paths = builder._sweep_page_paths(year_dir, filing_year)
        pages_by_year[filing_year] = len(page_paths)
        for page_path in page_paths:
            page_name, results = _read_sweep_page(page_path, filing_year)
            for result_index, payload in enumerate(results):
                filing_uuid = builder._validate_sweep_filing(
                    payload, filing_year, page_name, result_index
                )
                raw_occurrences_by_year[filing_year] += 1
                body_digest = hashlib.sha256(
                    json.dumps(
                        payload,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).digest()
                if filing_uuid in seen_uuids:
                    duplicate_uuids.add(filing_uuid)
                    duplicate_occurrences_by_year[filing_year] += 1
                    if first_body_digest_by_uuid[filing_uuid] != body_digest:
                        duplicate_body_mismatches.add(filing_uuid)
                    continue
                seen_uuids.add(filing_uuid)
                first_body_digest_by_uuid[filing_uuid] = body_digest
                senate_client_id, _registrant_scoped_id, client_name = (
                    builder._validated_client(payload, filing_uuid)
                )
                lda_client_id = _integer_global_client_id(
                    senate_client_id, filing_uuid
                )
                normalized_name = normalize_association_name(client_name)
                id_key = client_id_owner.get(lda_client_id)
                name_key = by_name.get(normalized_name)
                hits = {key for key in (id_key, name_key) if key is not None}
                if len(hits) > 1:
                    raise builder.IngestError(
                        f"filing {filing_uuid} has conflicting client.id and "
                        "exact-name association attribution"
                    )
                if not hits:
                    unmatched_by_year[filing_year] += 1
                    if len(unmatched_samples) < SAMPLE_LIMIT:
                        unmatched_samples.append(
                            {
                                "filing_uuid": filing_uuid,
                                "filing_year": filing_year,
                                "client_name": client_name,
                                "normalized_client_name": normalized_name,
                                "client_id": lda_client_id,
                            }
                        )
                    continue
                association_key = next(iter(hits))
                stratum, lob_id = association_key
                client_ids[association_key].add(lda_client_id)
                matched_names[association_key].add(normalized_name)
                id_match = id_key == association_key
                name_match = name_key == association_key
                if id_match:
                    channel_counts[association_key]["id"] += 1
                    id_matched_names[association_key].add(client_name)
                if name_match:
                    channel_counts[association_key]["name"] += 1
                if id_match and name_match:
                    channel_counts[association_key]["both"] += 1
                elif id_match:
                    id_only_names[association_key].add(client_name)
                matched_counts[association_key][filing_year] += 1
                matched_filings.append(
                    {
                        "stratum": stratum,
                        "lob_id": lob_id,
                        "association_name": client_name.strip(),
                        "filing_uuid": filing_uuid,
                        "filing_year": filing_year,
                        "matched_by_id": id_match,
                        "matched_by_name": name_match,
                        "payload": payload,
                    }
                )

    crosswalk = {stratum: [] for stratum in STRATA}
    coverage_gaps = {stratum: [] for stratum in STRATA}
    for stratum in STRATA:
        for entry in association_sets[stratum]:
            key = (stratum, entry["lob_id"])
            counts = matched_counts[key]
            channels = channel_counts[key]
            harvested = id_crosswalk[entry["lob_id"]]
            row = {
                "lob_id": entry["lob_id"],
                "client_names": list(entry["client_names"]),
                "normalized_client_names": sorted(
                    {normalize_association_name(name) for name in entry["client_names"]}
                ),
                "matched_normalized_client_names": sorted(matched_names[key]),
                "lda_client_ids": sorted(client_ids[key]),
                "harvested_client_ids": list(
                    harvested["accepted_client_ids"]
                ),
                "accepted_client_ids": list(
                    harvested["accepted_client_ids"]
                ),
                "rejected_client_ids": [
                    dict(rejection)
                    for rejection in harvested["rejected_client_ids"]
                ],
                "harvest_filing_names_seen": list(
                    harvested.get("filing_names_seen", [])
                ),
                "matched_by_id": channels["id"],
                "matched_by_name": channels["name"],
                "matched_by_both": channels["both"],
                "client_names_matched_by_id": sorted(id_matched_names[key]),
                "client_names_matched_by_id_only": sorted(id_only_names[key]),
                "matched_filings": {
                    str(year): counts[year] for year in YEARS
                },
            }
            crosswalk[stratum].append(row)
            if not sum(counts.values()):
                coverage_gaps[stratum].append(
                    {
                        "lob_id": entry["lob_id"],
                        "client_names": list(entry["client_names"]),
                        "reason": "zero_id_and_exact_name_matches_2023_2024",
                    }
                )

    unmatched_receipt = {
        "count": sum(unmatched_by_year.values()),
        "by_year": {
            str(year): unmatched_by_year[year] for year in YEARS
        },
        "samples": unmatched_samples,
    }
    receipt = {
        "pages": {str(year): pages_by_year[year] for year in YEARS},
        "raw_filing_occurrences": {
            str(year): raw_occurrences_by_year[year] for year in YEARS
        },
        "unique_filing_uuids": len(seen_uuids),
        "duplicate_filings": {
            "occurrence_count": sum(duplicate_occurrences_by_year.values()),
            "distinct_uuid_count": len(duplicate_uuids),
            "filing_uuid_samples": sorted(duplicate_uuids)[:SAMPLE_LIMIT],
            "occurrences_by_year": {
                str(year): duplicate_occurrences_by_year[year]
                for year in YEARS
            },
            "body_mismatch": {
                "count": len(duplicate_body_mismatches),
                "filing_uuid_samples": sorted(duplicate_body_mismatches)[
                    :SAMPLE_LIMIT
                ],
            },
        },
        "unmatched_both_channels": unmatched_receipt,
        "unmatched_exact_name": unmatched_receipt,
        "attribution": {
            "primary_identifying_field": "client.id",
            "id_channel_guard": "fixed_name_token_plausibility",
            "secondary_rule": "exact_normalized_client.name",
            "registrant_scoped_non_identifying_field": (
                "client.client_id (never used for attribution)"
            ),
            "matched_by_id": sum(
                counts["id"] for counts in channel_counts.values()
            ),
            "matched_by_name": sum(
                counts["name"] for counts in channel_counts.values()
            ),
            "matched_by_both": sum(
                counts["both"] for counts in channel_counts.values()
            ),
            "accepted_client_ids": sum(
                len(entry["accepted_client_ids"])
                for entry in id_crosswalk.values()
            ),
            "rejected_client_ids": sum(
                len(
                    {
                        rejection["client_id"]
                        for rejection in entry["rejected_client_ids"]
                    }
                )
                for entry in id_crosswalk.values()
            ),
        },
    }
    return {
        "matched_filings": matched_filings,
        "crosswalk": crosswalk,
        "coverage_gaps": coverage_gaps,
        "receipt": receipt,
    }


def load_calibration_schema(path, sample_lines=3):
    """Read multiple calibration rows and return the unjoined work schema."""
    _require_file(path, "calibration items JSONL")
    samples = []
    try:
        handle = open(path, "r", encoding="utf-8-sig")
    except OSError as exc:
        raise builder.IngestError(
            f"cannot read calibration items {path}: {exc}"
        ) from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise builder.IngestError(
                    f"blank JSONL row in {path} at line {line_number}"
                )
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise builder.IngestError(
                    f"malformed JSONL in {path} at line {line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise builder.IngestError(
                    f"calibration item {line_number} is not an object"
                )
            samples.append(row)
            if len(samples) >= sample_lines:
                break
    if not samples:
        raise builder.IngestError(f"calibration items are empty: {path}")
    reference_fields = tuple(samples[0])
    for index, row in enumerate(samples[1:], start=2):
        if tuple(row) != reference_fields:
            raise builder.IngestError(
                f"calibration schema or field order differs in sampled row {index}"
            )
    work_fields = tuple(
        field for field in reference_fields if field not in GOLD_FIELDS
    )
    if set(work_fields) != BASE_WORK_FIELDS:
        missing = sorted(BASE_WORK_FIELDS - set(work_fields))
        extra = sorted(set(work_fields) - BASE_WORK_FIELDS)
        raise builder.IngestError(
            "calibration work-item schema mismatch: "
            f"missing={missing}, extra={extra}"
        )
    bill_key = samples[0].get("bill_key")
    if not isinstance(bill_key, dict) or set(bill_key) != {
        "type",
        "number",
        "congress",
    }:
        raise builder.IngestError("calibration sample has malformed bill_key")
    return {
        "calibration_fields": reference_fields,
        "work_fields": work_fields,
        "bill_key_fields": tuple(bill_key),
        "sampled_rows": len(samples),
    }


def load_extraction_context(local_app_data=None):
    """Load the same BILLSTATUS maps used by the existing classifier path."""
    paths = builder.default_paths(local_app_data)
    final_congress = builder.congress_for_year(max(YEARS))
    congress_maps = {}
    prompt_title_items = {}
    for congress in range(115, final_congress + 1):
        zip_types = (
            builder.HISTORICAL_ZIP_TYPES
            if congress in {115, 116}
            else builder.ZIP_TYPES
        )
        billstatus_dir = builder.billstatus_dir_for_congress(paths, congress)
        congress_maps[congress] = builder.load_sponsor_map(
            billstatus_dir,
            congress=congress,
            zip_types=zip_types,
        )
        prompt_title_items.update(
            classifier.load_prompt_title_items(
                billstatus_dir, congress, zip_types
            )
        )
    return {
        "bill_people_maps": {
            congress: value[0] for congress, value in congress_maps.items()
        },
        "title_indexes": {
            congress: value[3] for congress, value in congress_maps.items()
        },
        "prompt_title_items": prompt_title_items,
    }


def _record_sample(receipt, sample):
    receipt["count"] += 1
    if sample not in receipt["samples"] and len(receipt["samples"]) < SAMPLE_LIMIT:
        receipt["samples"].append(sample)


def _bill_receipt(skipped_by_bill):
    rows = []
    for bill_key in sorted(skipped_by_bill):
        value = skipped_by_bill[bill_key]
        rows.append(
            {
                "bill_key": {
                    "type": bill_key[0],
                    "number": bill_key[1],
                    "congress": bill_key[2],
                },
                "bill_no": classifier.bill_display(bill_key),
                "count": value["count"],
                "filing_uuid_samples": value["filing_uuid_samples"],
            }
        )
    return {
        "count": sum(value["count"] for value in skipped_by_bill.values()),
        "distinct_bill_keys": len(rows),
        "by_bill": rows,
    }


def _item_sort_key(item):
    key = item["bill_key"]
    return (
        item["stratum"],
        item["lob_id"],
        item["report_uuid"],
        (key["type"], int(key["number"]), int(key["congress"])),
    )


def assemble_association_work_items(matched_filings, schema, context):
    """Extract strict bill keys and mirror calibration paragraph construction."""
    required_context = {
        "bill_people_maps",
        "title_indexes",
        "prompt_title_items",
    }
    if not isinstance(context, dict) or not required_context.issubset(context):
        raise builder.IngestError("extraction context is incomplete")
    bill_people_maps = context["bill_people_maps"]
    title_indexes = context["title_indexes"]
    prompt_title_items = context["prompt_title_items"]
    items = []
    zero_bill = {
        "count": 0,
        "filing_uuid_samples": [],
        "by_stratum": {
            stratum: {"count": 0, "filing_uuid_samples": []}
            for stratum in STRATA
        },
    }
    diagnostics = {
        "unparsed_bill_like": {"count": 0, "samples": []},
        "ambiguous_title_matches": {"count": 0, "samples": []},
        "stale_citations_resolved": {"count": 0, "samples": []},
        "stale_citations_unresolvable": {"count": 0, "samples": []},
    }
    skipped_by_bill = {}

    for filing in matched_filings:
        if not isinstance(filing, dict):
            raise builder.IngestError("matched filing is not an object")
        stratum = filing.get("stratum")
        lob_id = filing.get("lob_id")
        report_uuid = filing.get("filing_uuid")
        filing_year = filing.get("filing_year")
        payload = filing.get("payload")
        if stratum not in STRATA:
            raise builder.IngestError(
                f"matched filing {report_uuid} has invalid stratum"
            )
        if not isinstance(lob_id, str) or not lob_id.strip():
            raise builder.IngestError(
                f"matched filing {report_uuid} has invalid lob_id"
            )
        builder._validate_sweep_filing(
            payload, filing_year, "matched", 0
        )
        _client_id, _scoped_id, client_name = builder._validated_client(
            payload, report_uuid
        )
        congress = builder.congress_for_year(filing_year)
        if congress not in title_indexes:
            raise builder.IngestError(
                f"missing title index for Congress {congress}"
            )
        quarter = builder.filing_period_to_quarter(
            payload.get("filing_period")
        )
        filing_item_count = 0
        for activity in builder._validated_activities(payload, report_uuid):
            paragraph = activity["description"]
            resolved = builder.resolve_activity_mentions(
                paragraph,
                title_indexes[congress],
                filing_year,
                bill_people_maps,
            )
            for unparsed in resolved["unparsed"]:
                _record_sample(
                    diagnostics["unparsed_bill_like"],
                    {
                        "filing_uuid": report_uuid,
                        "verbatim": unparsed["verbatim"],
                    },
                )
            for ambiguous in resolved["title_result"]["ambiguous_matches"]:
                _record_sample(
                    diagnostics["ambiguous_title_matches"],
                    {
                        "filing_uuid": report_uuid,
                        "verbatim": ambiguous["verbatim"],
                        "bill_keys": ambiguous["bill_keys"],
                    },
                )
            for unresolved in resolved["stale_unresolvable"]:
                _record_sample(
                    diagnostics["stale_citations_unresolvable"],
                    {"filing_uuid": report_uuid, **unresolved},
                )
            for stale in resolved["stale_citations"]:
                _record_sample(
                    diagnostics["stale_citations_resolved"],
                    {"filing_uuid": report_uuid, **stale},
                )

            for bill_key in sorted(resolved["strict_bill_keys"]):
                people_map = bill_people_maps.get(bill_key[2], {})
                if (
                    bill_key not in people_map
                    and bill_key not in prompt_title_items
                ):
                    value = skipped_by_bill.setdefault(
                        bill_key,
                        {"count": 0, "filing_uuid_samples": []},
                    )
                    value["count"] += 1
                    if (
                        report_uuid not in value["filing_uuid_samples"]
                        and len(value["filing_uuid_samples"]) < SAMPLE_LIMIT
                    ):
                        value["filing_uuid_samples"].append(report_uuid)
                    continue
                short_title, official_title, title_tier = (
                    classifier.resolve_prompt_titles(
                        prompt_title_items.get(bill_key, ()), bill_key
                    )
                )
                raw_bill_key = {
                    "type": bill_key[0],
                    "number": bill_key[1],
                    "congress": bill_key[2],
                }
                ordered_bill_key = {
                    field: raw_bill_key[field]
                    for field in schema["bill_key_fields"]
                }
                values = {
                    "lob_id": lob_id,
                    "client_name": client_name.strip(),
                    "bill_key": ordered_bill_key,
                    "bill_no": classifier.bill_display(bill_key),
                    "short_title": short_title,
                    "official_title": official_title,
                    "official_title_tier": title_tier,
                    "paragraph": paragraph,
                    "report_uuid": report_uuid,
                    "filing_year": filing_year,
                    "quarter": quarter,
                }
                item = {
                    field: values[field] for field in schema["work_fields"]
                }
                # Fixed task metadata.  All classifier-consumed fields above
                # remain an exact calibration-schema mirror.
                item["stratum"] = stratum
                classifier.validate_work_item(item)
                items.append(item)
                filing_item_count += 1
        if filing_item_count == 0:
            zero_bill["count"] += 1
            if (
                report_uuid not in zero_bill["filing_uuid_samples"]
                and len(zero_bill["filing_uuid_samples"]) < SAMPLE_LIMIT
            ):
                zero_bill["filing_uuid_samples"].append(report_uuid)
            stratum_receipt = zero_bill["by_stratum"][stratum]
            stratum_receipt["count"] += 1
            if (
                report_uuid not in stratum_receipt["filing_uuid_samples"]
                and len(stratum_receipt["filing_uuid_samples"]) < SAMPLE_LIMIT
            ):
                stratum_receipt["filing_uuid_samples"].append(report_uuid)

    items.sort(key=_item_sort_key)
    receipt = {
        "title_mode": "strict",
        "matched_filings_with_zero_extractable_bills": zero_bill,
        "work_items_skipped_bill_not_in_billstatus": _bill_receipt(
            skipped_by_bill
        ),
        **diagnostics,
    }
    return items, receipt


def _association_document(
    association_sets,
    selection_receipt,
    sweep_result,
    extraction_receipt,
    min_directional,
):
    return {
        "filing_years": list(YEARS),
        "min_directional": min_directional,
        "association_sets": association_sets,
        "crosswalk": sweep_result["crosswalk"],
        "coverage_gaps": sweep_result["coverage_gaps"],
        "receipts": {
            "association_selection": selection_receipt,
            "sweep_scan": sweep_result["receipt"],
            "bill_extraction": extraction_receipt,
        },
    }


def write_outputs(out_dir, association_document, work_items):
    """Atomically write the only two assembly artifacts."""
    os.makedirs(out_dir, exist_ok=True)
    sets_path = os.path.join(out_dir, "association_sets.json")
    items_path = os.path.join(out_dir, "assoc_work_items.jsonl")
    classifier.write_json_atomic(sets_path, association_document)
    classifier.write_jsonl_atomic(items_path, work_items)
    return sets_path, items_path


def _id_crosswalk_path(args):
    return args.id_crosswalk or os.path.join(args.out_dir, "id_crosswalk.json")


def _rejected_client_id_count(id_crosswalk):
    return sum(
        len(
            {
                rejection["client_id"]
                for rejection in entry["rejected_client_ids"]
            }
        )
        for entry in id_crosswalk.values()
    )


def run_id_harvest(args):
    """Run the standalone cache-only, name-guarded client.id harvest."""
    _require_file(args.id_harvest_seed, "identity harvest seed")
    _require_directory(args.filings_root, "partitioned filings cache")
    output_path = _id_crosswalk_path(args)
    crosswalk = harvest_id_crosswalk(
        args.id_harvest_seed, args.filings_root
    )
    classifier.write_json_atomic(output_path, crosswalk)
    print(
        "association client.id harvest: "
        f"associations={len(crosswalk)} "
        f"uuids_present={sum(row['uuids_present'] for row in crosswalk.values())} "
        f"uuids_missing={sum(row['uuids_missing'] for row in crosswalk.values())} "
        f"| {output_path}"
    )
    print(
        "name-plausibility rejected client.ids: "
        f"{_rejected_client_id_count(crosswalk)}"
    )
    return 0


def _default_paths(environ):
    local_app_data = environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise builder.IngestError("LOCALAPPDATA is missing")
    melange = os.path.join(local_app_data, "Melange")
    rebuild = os.path.join(melange, "lda_rebuild")
    positions = os.path.join(rebuild, "raw", "positions")
    return {
        "classified_csv": os.path.join(
            melange,
            "lobbyview",
            "derived",
            "lobbyview_clients_classified.csv",
        ),
        "kim_csv": os.path.join(
            positions,
            "bill_position_dataset_input",
            "bill_position_dataset.csv",
        ),
        "sweeps_root": os.path.join(rebuild, "raw", "sweeps"),
        "calib_items": os.path.join(positions, "calib_items.jsonl"),
        "out_dir": os.path.join(positions, "assoc_2023_2024"),
        "id_harvest_seed": os.path.join(
            positions, "assoc_2023_2024", "id_harvest_seed.json"
        ),
        "filings_root": os.path.join(rebuild, "raw", "filings"),
    }


def _parser(defaults):
    parser = argparse.ArgumentParser(
        description=(
            "Harvest guarded identities or assemble guarded global-client-ID "
            "plus exact-name 2023--2024 CU/bank association work items"
        )
    )
    parser.add_argument("--classified-csv", default=defaults["classified_csv"])
    parser.add_argument("--kim-csv", default=defaults["kim_csv"])
    parser.add_argument("--sweeps-root", default=defaults["sweeps_root"])
    parser.add_argument("--calib-items", default=defaults["calib_items"])
    parser.add_argument("--out-dir", default=defaults["out_dir"])
    parser.add_argument("--min-directional", type=int, default=5)
    parser.add_argument(
        "--harvest-ids",
        action="store_true",
        help=(
            "harvest name-plausible global client.id values from cached seed "
            "filings and exit"
        ),
    )
    parser.add_argument(
        "--id-harvest-seed", default=defaults["id_harvest_seed"]
    )
    parser.add_argument("--filings-root", default=defaults["filings_root"])
    parser.add_argument(
        "--id-crosswalk",
        default=None,
        help="id_crosswalk.json path; defaults to --out-dir/id_crosswalk.json",
    )
    return parser


def run(args, environ=None):
    environ = os.environ if environ is None else environ
    if args.harvest_ids:
        return run_id_harvest(args)
    id_crosswalk_path = _id_crosswalk_path(args)
    _require_file(id_crosswalk_path, "identity client.id crosswalk")
    for path, label in (
        (args.classified_csv, "classified clients CSV"),
        (args.kim_csv, "Kim position CSV"),
        (args.calib_items, "calibration items JSONL"),
    ):
        _require_file(path, label)
    _require_directory(args.sweeps_root, "sweeps root")

    association_sets, selection_receipt = load_association_sets(
        args.classified_csv,
        args.kim_csv,
        min_directional=args.min_directional,
    )
    id_crosswalk = load_id_crosswalk(
        id_crosswalk_path, association_sets=association_sets
    )
    sweep_result = scan_sweeps(
        args.sweeps_root, association_sets, id_crosswalk
    )
    schema = load_calibration_schema(args.calib_items)
    context = load_extraction_context(environ.get("LOCALAPPDATA"))
    work_items, extraction_receipt = assemble_association_work_items(
        sweep_result["matched_filings"], schema, context
    )
    document = _association_document(
        association_sets,
        selection_receipt,
        sweep_result,
        extraction_receipt,
        args.min_directional,
    )
    write_outputs(args.out_dir, document, work_items)

    item_counts = collections.Counter(item["stratum"] for item in work_items)
    print("association bill-position assembly 2023--2024")
    for stratum in STRATA:
        crosswalk_rows = sweep_result["crosswalk"][stratum]
        matched = {
            year: sum(row["matched_filings"][str(year)] for row in crosswalk_rows)
            for year in YEARS
        }
        print(
            f"{stratum}: associations={len(association_sets[stratum])} "
            f"matched_filings_2023={matched[2023]} "
            f"matched_filings_2024={matched[2024]} "
            f"work_items={item_counts[stratum]}"
        )
    print(
        "name-plausibility rejected client.ids: "
        f"{_rejected_client_id_count(id_crosswalk)}"
    )
    return 0


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    try:
        defaults = _default_paths(environ)
        args = _parser(defaults).parse_args(
            sys.argv[1:] if argv is None else argv
        )
        return run(args, environ=environ)
    except builder.IngestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
