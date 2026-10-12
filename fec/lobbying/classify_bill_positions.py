# -*- coding: utf-8 -*-
"""Build and classify LDA bill-position work items with Anthropic Haiku."""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import concurrent.futures
import csv
import hashlib
import json
import os
import re
import string
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from lobbying import build_network_2021_rebuild as builder


MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OUR_LABELS = ("Support", "Oppose", "Amend", "Monitor", "Mention")
THEIR_FIVE_CLASS_LABELS = (
    "Support",
    "Oppose",
    "Amend",
    "Monitor",
    "Engage",
)
THREE_CLASS_LABELS = ("Support", "Oppose", "Engage")
LABELS = OUR_LABELS
OFFICIAL_TITLE_TIERS = (
    "official_as_introduced",
    "official_other",
    "display_title",
    "first_available",
)
PROMPT_TEMPLATE = (
    "Classify the given text that explicitly describes lobbying activities "
    "for bill {bill_no} into one of the five types: 'Support', 'Oppose', "
    "'Amend', 'Monitor', or 'Mention' without explanation.\n"
    "{bill_no} short title: {short_title}\n"
    "{bill_no} official title: {official_title}\n"
    "Text:{paragraph}\n"
    "Answer:"
)
PROMPT_INSTRUCTION = (
    "Classify the given text that explicitly describes lobbying activities "
    "for bill {bill_no} into one of the five types: 'Support', 'Oppose', "
    "'Amend', 'Monitor', or 'Mention' without explanation."
)
PROMPT_TARGET_TEMPLATE = (
    "{bill_no} short title: {short_title}\n"
    "{bill_no} official title: {official_title}\n"
    "Text:{paragraph}\n"
    "Answer:"
)
FLAT_RESULT_FIELDS = [
    "lob_id",
    "client_name",
    "bill_type",
    "bill_number",
    "congress",
    "bill_no",
    "our_position",
    "their_label_3_class",
    "their_label_5_class",
    "annotation_source",
    "report_uuid",
    "filing_year",
    "quarter",
    "classification_status",
    "model",
    "prompt_sha256",
]
DATASET_FIELDS = {
    "lob_id",
    "client_name",
    "bill_id",
    "label_3_class",
    "label_5_class",
    "annotation_source",
}
BILL_ID_RE = re.compile(r"^([a-z]+)([0-9]+)-([0-9]+)$")
BILL_ID_TYPES = {
    "hr": "HR",
    "s": "S",
    "hjres": "HJRES",
    "sjres": "SJRES",
    "hconres": "HCONRES",
    "sconres": "SCONRES",
    "hres": "HRES",
    "sres": "SRES",
}
BILL_TYPE_IDS = {type_token: bill_id for bill_id, type_token in BILL_ID_TYPES.items()}
BILL_DISPLAY_TYPES = {
    "HR": "H.R.",
    "S": "S.",
    "HJRES": "H.J.Res.",
    "SJRES": "S.J.Res.",
    "HCONRES": "H.Con.Res.",
    "SCONRES": "S.Con.Res.",
    "HRES": "H.Res.",
    "SRES": "S.Res.",
}
THEIR_TO_OUR_LABEL = {
    "Support": "Support",
    "Oppose": "Oppose",
    "Amend": "Amend",
    "Monitor": "Monitor",
    "Engage": "Mention",
}
FEWSHOT_GROUPS = (
    ("Support", frozenset({"Support"})),
    ("Oppose", frozenset({"Oppose"})),
    ("Monitor/Amend", frozenset({"Monitor", "Amend"})),
    ("Engage/Mention", frozenset({"Engage"})),
)
FEWSHOT_PER_GROUP = 3


def render_exemplar(exemplar):
    return PROMPT_TARGET_TEMPLATE.format(
        bill_no=exemplar["bill_no"],
        short_title=exemplar["short_title"],
        official_title=exemplar["official_title"],
        paragraph=exemplar["paragraph"],
    ) + exemplar["mapped_label"]


def render_fewshot_prefix(exemplars):
    rendered = "\n\n".join(render_exemplar(item) for item in exemplars)
    return rendered + ("\n\n" if rendered else "")


def build_prompt(work_item, prompt_variant="zeroshot", exemplars=None):
    if prompt_variant == "zeroshot":
        return PROMPT_TEMPLATE.format(
            bill_no=work_item["bill_no"],
            short_title=work_item["short_title"],
            official_title=work_item["official_title"],
            paragraph=work_item["paragraph"],
        )
    if prompt_variant != "fewshot":
        raise builder.IngestError(
            f"unsupported prompt variant {prompt_variant!r}"
        )
    exemplars = [] if exemplars is None else exemplars
    instruction = PROMPT_INSTRUCTION.format(bill_no=work_item["bill_no"])
    prefix = render_fewshot_prefix(exemplars)
    target = PROMPT_TARGET_TEMPLATE.format(
        bill_no=work_item["bill_no"],
        short_title=work_item["short_title"],
        official_title=work_item["official_title"],
        paragraph=work_item["paragraph"],
    )
    return instruction + "\n\n" + prefix + target


def prompt_sha256(work_item, prompt_variant="zeroshot", exemplars=None):
    return hashlib.sha256(
        build_prompt(work_item, prompt_variant, exemplars).encode("utf-8")
    ).hexdigest()


def parse_model_reply(reply):
    if not isinstance(reply, str):
        return None
    normalized = reply.strip().strip(string.punctuation).strip().casefold()
    return {label.casefold(): label for label in LABELS}.get(normalized)


def reply_receipt(reply):
    position = parse_model_reply(reply)
    return {
        "status": "ok" if position is not None else "unparseable",
        "position": position,
        "raw_reply": reply,
    }


def collapse_our_to_three(label):
    canonical = _canonical_label(label, OUR_LABELS)
    if canonical in {"Support", "Oppose"}:
        return canonical
    return "Engage"


def collapse_their_five_to_three(label):
    canonical = _canonical_label(label, THEIR_FIVE_CLASS_LABELS)
    if canonical in {"Support", "Oppose"}:
        return canonical
    return "Engage"


def collapse_three_class(label):
    return collapse_our_to_three(label)


def _canonical_label(value, allowed):
    if not isinstance(value, str):
        raise builder.IngestError(f"invalid label {value!r}")
    canonical = {label.casefold(): label for label in allowed}.get(
        value.strip().casefold()
    )
    if canonical is None:
        raise builder.IngestError(f"invalid label {value!r}")
    return canonical


def _dataset_label(value, allowed, row_number, field):
    try:
        return _canonical_label(value, allowed)
    except builder.IngestError as exc:
        raise builder.IngestError(
            f"position dataset row {row_number} has invalid {field} "
            f"{value!r}"
        ) from exc


def bill_display(bill_key):
    type_token, number, _congress = bill_key
    try:
        prefix = BILL_DISPLAY_TYPES[type_token]
    except KeyError as exc:
        raise builder.IngestError(
            f"unsupported bill type for display {type_token!r}"
        ) from exc
    return f"{prefix} {number}"


def work_item_content_hash(work_item):
    encoded = json.dumps(
        work_item,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bill_key_tuple(work_item):
    bill_key = work_item["bill_key"]
    return (
        bill_key["type"],
        int(bill_key["number"]),
        int(bill_key["congress"]),
    )


def work_item_sort_key(work_item):
    return (
        work_item["report_uuid"],
        _bill_key_tuple(work_item),
        work_item_content_hash(work_item),
    )


def validate_work_item(work_item, source="work item"):
    if not isinstance(work_item, dict):
        raise builder.IngestError(f"{source} is not an object")
    required_strings = (
        "lob_id",
        "client_name",
        "bill_no",
        "short_title",
        "official_title",
        "paragraph",
        "report_uuid",
    )
    for field in required_strings:
        if not isinstance(work_item.get(field), str):
            raise builder.IngestError(
                f"{source} field {field} is not a string"
            )
        if field not in {"paragraph", "short_title"} and not work_item[
            field
        ].strip():
            raise builder.IngestError(f"{source} field {field} is blank")
    bill_key = work_item.get("bill_key")
    if not isinstance(bill_key, dict) or set(bill_key) != {
        "type",
        "number",
        "congress",
    }:
        raise builder.IngestError(f"{source} has malformed bill_key")
    type_token = bill_key["type"]
    if type_token not in BILL_DISPLAY_TYPES:
        raise builder.IngestError(
            f"{source} has unsupported bill type {type_token!r}"
        )
    if not isinstance(bill_key["number"], int) or bill_key["number"] <= 0:
        raise builder.IngestError(f"{source} has invalid bill number")
    if not isinstance(bill_key["congress"], int) or bill_key["congress"] <= 0:
        raise builder.IngestError(f"{source} has invalid Congress")
    if not isinstance(work_item.get("filing_year"), int):
        raise builder.IngestError(f"{source} has invalid filing_year")
    if work_item.get("quarter") not in {1, 2, 3, 4}:
        raise builder.IngestError(f"{source} has invalid quarter")
    if work_item["bill_no"] != bill_display(_bill_key_tuple(work_item)):
        raise builder.IngestError(f"{source} bill_no does not match bill_key")
    if work_item.get("official_title_tier") not in OFFICIAL_TITLE_TIERS:
        raise builder.IngestError(
            f"{source} has invalid official_title_tier"
        )
    return work_item


def _atomic_text(path, writer):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    temp_path = path + ".tmp." + str(os.getpid())
    try:
        with open(temp_path, "w", encoding="utf-8", newline="") as handle:
            writer(handle)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def write_jsonl_atomic(path, rows):
    ordered = list(rows)

    def writer(handle):
        for row in ordered:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )

    _atomic_text(path, writer)


def write_json_atomic(path, value):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    builder.dump_atomic(
        value,
        path,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def read_jsonl(path):
    rows = []
    try:
        handle = open(path, "r", encoding="utf-8-sig")
    except OSError as exc:
        raise builder.IngestError(f"cannot read work items {path}: {exc}") from exc
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
            validate_work_item(row, f"{path} line {line_number}")
            rows.append(row)
    return rows


def resolve_prompt_titles(title_items, bill_key):
    available = [
        item
        for item in title_items
        if isinstance(item, dict)
        and isinstance(item.get("title"), str)
        and item["title"].strip()
    ]
    if not available:
        raise builder.IngestError(
            "no BILLSTATUS title items for "
            f"{bill_display(bill_key)} in Congress {bill_key[2]}"
        )

    def type_text(item):
        value = item.get("title_type", "")
        return value.upper() if isinstance(value, str) else ""

    short = next(
        (
            item["title"].strip()
            for item in available
            if "SHORT TITLE" in type_text(item)
        ),
        "",
    )
    official_as_introduced = [
        item
        for item in available
        if "OFFICIAL TITLE" in type_text(item)
        and "AS INTRODUCED" in type_text(item)
    ]
    official_other = [
        item
        for item in available
        if "OFFICIAL TITLE" in type_text(item)
        and "AS INTRODUCED" not in type_text(item)
    ]
    display = [
        item for item in available if "DISPLAY TITLE" in type_text(item)
    ]
    for tier, candidates in (
        ("official_as_introduced", official_as_introduced),
        ("official_other", official_other),
        ("display_title", display),
        ("first_available", available),
    ):
        if candidates:
            return short, candidates[0]["title"].strip(), tier
    raise AssertionError("available BILLSTATUS titles were not resolved")


def _title_items_for_member(member_stream, archive_name, member_name):
    try:
        root = ET.parse(member_stream).getroot()
    except ET.ParseError as exc:
        raise builder.IngestError(
            f"malformed XML in {archive_name}:{member_name}: {exc}"
        ) from exc
    title_items = []
    for titles_element in root.iter():
        if builder._local_name(titles_element.tag) != "titles":
            continue
        for item in list(titles_element):
            if builder._local_name(item.tag) != "item":
                continue
            title_types = []
            titles = []
            for child in list(item):
                value = (child.text or "").strip()
                if not value:
                    continue
                name = builder._local_name(child.tag)
                if name == "titleType":
                    title_types.append(value)
                elif name == "title":
                    titles.append(value)
            title_type = " | ".join(title_types)
            for title in titles:
                title_items.append(
                    {"title_type": title_type, "title": title}
                )
    return tuple(title_items)


def load_prompt_title_items(billstatus_dir, congress, zip_types):
    title_items_by_bill = {}
    for archive_token, type_token in zip_types:
        archive_name = f"BILLSTATUS-{congress}-{archive_token}.zip"
        archive_path = os.path.join(billstatus_dir, archive_name)
        member_re = re.compile(
            r"(?:.*/)?BILLSTATUS-"
            + str(congress)
            + re.escape(archive_token)
            + r"([0-9]+)\.xml$",
            re.IGNORECASE,
        )
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise builder.IngestError(
                f"cannot open BILLSTATUS archive {archive_path}: {exc}"
            ) from exc
        with archive:
            xml_infos = sorted(
                (
                    info
                    for info in archive.infolist()
                    if info.filename.lower().endswith(".xml")
                ),
                key=lambda info: info.filename.lower(),
            )
            for info in xml_infos:
                match = member_re.fullmatch(info.filename)
                if match is None:
                    raise builder.IngestError(
                        f"unexpected XML member name "
                        f"{archive_name}:{info.filename}"
                    )
                bill_key = (type_token, int(match.group(1)), congress)
                if bill_key in title_items_by_bill:
                    raise builder.IngestError(
                        "duplicate BILLSTATUS member for "
                        f"{bill_display(bill_key)} in Congress {congress}"
                    )
                try:
                    with archive.open(info, "r") as member_stream:
                        title_items_by_bill[bill_key] = (
                            _title_items_for_member(
                                member_stream, archive_name, info.filename
                            )
                        )
                except builder.IngestError:
                    raise
                except Exception as exc:
                    raise builder.IngestError(
                        f"cannot parse {archive_name}:{info.filename}: {exc}"
                    ) from exc
    return title_items_by_bill


def title_availability_receipt(work_items):
    tiers = collections.Counter(
        item["official_title_tier"] for item in work_items
    )
    return {
        "work_items_without_short_title": sum(
            not item["short_title"].strip() for item in work_items
        ),
        "official_title_resolution_tiers": {
            tier: tiers[tier] for tier in OFFICIAL_TITLE_TIERS
        },
    }


def verbatim_source_segment(paragraph, mention, limit=200):
    if not isinstance(paragraph, str):
        raise builder.IngestError("source paragraph is not a string")
    if len(paragraph) <= limit:
        return paragraph
    start = mention.get("start")
    end = mention.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        raise builder.IngestError("bill mention lacks source offsets")
    mention_length = max(0, end - start)
    context_before = max(0, (limit - mention_length) // 2)
    segment_start = max(0, start - context_before)
    segment_end = min(len(paragraph), segment_start + limit)
    segment_start = max(0, segment_end - limit)
    return paragraph[segment_start:segment_end]


def record_bill_not_in_billstatus(
    skipped_by_bill, bill_key, report_uuid, paragraph, mention
):
    entry = skipped_by_bill.setdefault(
        bill_key,
        {
            "count": 0,
            "samples": [],
        },
    )
    entry["count"] += 1
    sample = {
        "report_uuid": report_uuid,
        "verbatim_source_segment": verbatim_source_segment(
            paragraph, mention
        ),
    }
    if sample not in entry["samples"] and len(entry["samples"]) < 3:
        entry["samples"].append(sample)


def bill_not_in_billstatus_receipt(skipped_by_bill):
    by_bill = []
    for bill_key in sorted(skipped_by_bill):
        entry = skipped_by_bill[bill_key]
        by_bill.append(
            {
                "bill_no": bill_display(bill_key),
                "bill_key": {
                    "type": bill_key[0],
                    "number": bill_key[1],
                },
                "congress": bill_key[2],
                "count": entry["count"],
                "samples": list(entry["samples"]),
            }
        )
    return {
        "work_items_skipped_bill_not_in_billstatus": {
            "count": sum(entry["count"] for entry in skipped_by_bill.values()),
            "distinct_bill_keys": len(by_bill),
            "by_bill": by_bill,
        }
    }


def work_items_from_payload(
    payload,
    report_uuid,
    lob_id,
    filing_year,
    filing_period,
    title_index,
    prompt_title_items,
    bill_people_maps,
    skipped_not_in_billstatus=None,
):
    _senate_id, _scoped_id, client_name = builder._validated_client(
        payload, report_uuid
    )
    quarter = builder.filing_period_to_quarter(filing_period)
    if skipped_not_in_billstatus is None:
        skipped_not_in_billstatus = {}
    items = []
    for activity in builder._validated_activities(payload, report_uuid):
        paragraph = activity["description"]
        resolved = builder.resolve_activity_mentions(
            paragraph, title_index, filing_year, bill_people_maps
        )
        for mention in resolved["parsed"]:
            bill_key = (
                mention["type"],
                mention["number"],
                mention["congress"],
            )
            people_map = bill_people_maps.get(bill_key[2], {})
            if (
                bill_key not in people_map
                and bill_key not in prompt_title_items
            ):
                record_bill_not_in_billstatus(
                    skipped_not_in_billstatus,
                    bill_key,
                    report_uuid,
                    paragraph,
                    mention,
                )
                continue
            short_title, official_title, official_title_tier = (
                resolve_prompt_titles(
                    prompt_title_items.get(bill_key, ()), bill_key
                )
            )
            item = {
                "lob_id": str(lob_id),
                "client_name": client_name.strip(),
                "bill_key": {
                    "type": bill_key[0],
                    "number": bill_key[1],
                    "congress": bill_key[2],
                },
                "bill_no": bill_display(bill_key),
                "short_title": short_title,
                "official_title": official_title,
                "official_title_tier": official_title_tier,
                "paragraph": paragraph,
                "report_uuid": report_uuid,
                "filing_year": filing_year,
                "quarter": quarter,
            }
            validate_work_item(item)
            items.append(item)
    return items


def assemble_work_items(
    environ, worklist_path, years, return_receipt=False
):
    paths = builder.default_paths(environ.get("LOCALAPPDATA"))
    bridge3_mode = any(year in builder.BRIDGE3_YEARS for year in years)
    if bridge3_mode and any(year not in builder.BRIDGE3_YEARS for year in years):
        raise builder.IngestError(
            "bridge3 --years cannot mix 2023..2026 with legacy years"
        )
    if worklist_path is None:
        worklist_path = (
            paths["bridge3_worklist"] if bridge3_mode else paths["worklist"]
        )
    legacy_default = builder._same_path(worklist_path, paths["worklist"])
    worklist, worklist_md5 = builder.load_worklist(
        worklist_path,
        legacy_default_year=2021 if legacy_default else None,
    )
    requested_records = [
        record for record in worklist if record["filing_year"] in years
    ]
    for filing_year in years:
        if filing_year not in builder.POSITION_ITEM_SWEEP_YEARS and not any(
            record["filing_year"] == filing_year
            for record in requested_records
        ):
            raise builder.IngestError(
                f"worklist has no records for requested year {filing_year}"
            )
    manifest_path = builder.worklist_manifest_path(paths, worklist_path)
    manifest = builder.load_worklist_manifest(manifest_path, worklist_md5)
    needed_records = (
        [record for record in worklist if record["filing_year"] <= 2023]
        if bridge3_mode
        else requested_records
    )
    filing_paths, _missing = builder.discover_filing_paths(
        paths,
        needed_records,
        manifest,
        legacy_first=legacy_default,
        require_complete=False,
    )
    available_records = [
        record
        for record in needed_records
        if record["report_uuid"] in filing_paths
    ]

    filing_congresses = {builder.congress_for_year(year) for year in years}
    congress_maps = {}
    for congress in range(115, max(filing_congresses) + 1):
        zip_types = (
            builder.HISTORICAL_ZIP_TYPES
            if congress in {115, 116}
            else builder.ZIP_TYPES
        )
        congress_maps[congress] = builder.load_sponsor_map(
            builder.billstatus_dir_for_congress(paths, congress),
            congress=congress,
            zip_types=zip_types,
        )
    bill_people_maps = {
        congress: congress_map[0]
        for congress, congress_map in congress_maps.items()
    }
    title_indexes = {
        congress: congress_map[3]
        for congress, congress_map in congress_maps.items()
    }
    prompt_title_items = {}
    for congress in congress_maps:
        zip_types = (
            builder.HISTORICAL_ZIP_TYPES
            if congress in {115, 116}
            else builder.ZIP_TYPES
        )
        prompt_title_items.update(
            load_prompt_title_items(
                builder.billstatus_dir_for_congress(paths, congress),
                congress,
                zip_types,
            )
        )

    sweep_scans = {}
    if bridge3_mode and any(
        year in builder.POSITION_ITEM_SWEEP_YEARS for year in years
    ):
        harvested_client_ids, _receipt = builder.harvest_client_ids(
            worklist, filing_paths
        )
        for filing_year in years:
            if filing_year not in builder.POSITION_ITEM_SWEEP_YEARS:
                continue
            sweep_scans[filing_year] = builder.scan_sweep_pages(
                os.path.join(paths["sweeps_dir"], str(filing_year)),
                filing_year,
                harvested_client_ids=harvested_client_ids,
                require_manifest=True,
            )

    items = []
    skipped_not_in_billstatus = {}
    records_by_uuid = {
        record["report_uuid"]: record for record in available_records
    }
    for filing_year in years:
        congress = builder.congress_for_year(filing_year)
        title_index = title_indexes[congress]
        if filing_year in builder.POSITION_ITEM_SWEEP_YEARS:
            sweep = sweep_scans[filing_year]
            attribution = sweep["attribution_by_uuid"]
            for payload in builder.iter_sweep_filings(
                sweep["page_paths"], filing_year
            ):
                report_uuid = payload["filing_uuid"]
                lob_id = attribution.get(report_uuid)
                if lob_id is None:
                    continue
                items.extend(
                    work_items_from_payload(
                        payload,
                        report_uuid,
                        lob_id,
                        filing_year,
                        payload.get("filing_period"),
                        title_index,
                        prompt_title_items,
                        bill_people_maps,
                        skipped_not_in_billstatus,
                    )
                )
        else:
            year_records = [
                record
                for record in available_records
                if record["filing_year"] == filing_year
            ]
            for report_uuid, payload in builder.iter_worklist_payloads(
                year_records, filing_paths
            ):
                record = records_by_uuid[report_uuid]
                items.extend(
                    work_items_from_payload(
                        payload,
                        report_uuid,
                        record["lob_id"],
                        filing_year,
                        record["filing_period_code"],
                        title_index,
                        prompt_title_items,
                        bill_people_maps,
                        skipped_not_in_billstatus,
                    )
                )
    items = sorted(items, key=work_item_sort_key)
    if return_receipt:
        return items, bill_not_in_billstatus_receipt(
            skipped_not_in_billstatus
        )
    return items


def parse_dataset_bill_id(value, row_number):
    if not isinstance(value, str):
        raise builder.IngestError(
            f"position dataset row {row_number} has invalid bill_id"
        )
    match = BILL_ID_RE.fullmatch(value.strip().lower())
    if match is None or match.group(1) not in BILL_ID_TYPES:
        raise builder.IngestError(
            f"position dataset row {row_number} has malformed bill_id {value!r}"
        )
    return (
        BILL_ID_TYPES[match.group(1)],
        int(match.group(2)),
        int(match.group(3)),
    )


def load_position_dataset(path):
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise builder.IngestError(
            f"cannot read position dataset {path}: {exc}"
        ) from exc
    rows = []
    seen_pairs = set()
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not DATASET_FIELDS.issubset(
            reader.fieldnames
        ):
            raise builder.IngestError(
                "position dataset is missing required columns: "
                + ", ".join(sorted(DATASET_FIELDS))
            )
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise builder.IngestError(
                    f"position dataset row {row_number} has extra columns"
                )
            for field in DATASET_FIELDS:
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise builder.IngestError(
                        f"position dataset row {row_number} has blank {field}"
                    )
            bill_key = parse_dataset_bill_id(row["bill_id"], row_number)
            label_5 = _dataset_label(
                row["label_5_class"],
                THEIR_FIVE_CLASS_LABELS,
                row_number,
                "label_5_class",
            )
            label_3 = _dataset_label(
                row["label_3_class"],
                THREE_CLASS_LABELS,
                row_number,
                "label_3_class",
            )
            normalized = {
                "lob_id": row["lob_id"].strip(),
                "client_name": row["client_name"].strip(),
                "bill_key": bill_key,
                "label_3_class": label_3,
                "label_5_class": label_5,
                "annotation_source": row["annotation_source"].strip(),
            }
            pair = (normalized["lob_id"], bill_key)
            if pair in seen_pairs:
                raise builder.IngestError(
                    "duplicate (lob_id, bill_id) in position dataset at row "
                    f"{row_number}"
                )
            seen_pairs.add(pair)
            rows.append(normalized)
    return rows


def calibration_join(work_items, dataset_rows, lob_ids=None):
    subset = None if lob_ids is None else {str(value) for value in lob_ids}
    eligible_items = [
        item for item in work_items if item["filing_year"] <= 2022
    ]
    our_pairs = {
        (item["lob_id"], _bill_key_tuple(item)) for item in eligible_items
    }
    dataset_by_pair = {
        (row["lob_id"], row["bill_key"]): row for row in dataset_rows
    }
    overlap = our_pairs & set(dataset_by_pair)
    subset_overlap = (
        overlap
        if subset is None
        else {pair for pair in overlap if pair[0] in subset}
    )
    joined = []
    for item in eligible_items:
        pair = (item["lob_id"], _bill_key_tuple(item))
        if pair not in subset_overlap:
            continue
        reference = dataset_by_pair[pair]
        joined_item = dict(item)
        joined_item.update(
            {
                "their_client_name": reference["client_name"],
                "their_label_3_class": reference["label_3_class"],
                "their_label_5_class": reference["label_5_class"],
                "annotation_source": reference["annotation_source"],
            }
        )
        joined.append(joined_item)
    report = {
        "our_extractable_pairs_through_2022": len(our_pairs),
        "dataset_pairs": len(dataset_by_pair),
        "overlapping_pairs_overall": len(overlap),
        "lob_ids_subset": None if subset is None else sorted(subset),
        "overlapping_pairs_subset": len(subset_overlap),
        "joinable_work_items": len(joined),
    }
    return sorted(joined, key=work_item_sort_key), report


def dataset_bill_id(bill_key):
    type_token, number, congress = bill_key
    try:
        type_id = BILL_TYPE_IDS[type_token]
    except KeyError as exc:
        raise builder.IngestError(
            f"unsupported bill type for dataset id {type_token!r}"
        ) from exc
    return f"{type_id}{int(number)}-{int(congress)}"


def work_item_source_pair(work_item):
    return work_item["lob_id"], dataset_bill_id(_bill_key_tuple(work_item))


def map_their_label_to_ours(label):
    canonical = _canonical_label(label, THEIR_FIVE_CLASS_LABELS)
    return THEIR_TO_OUR_LABEL[canonical]


def _source_pair_hash(pair):
    lob_id, bill_id = pair
    return hashlib.sha256((lob_id + bill_id).encode("utf-8")).hexdigest()


def select_fewshot_exemplars(
    dataset_rows, source_work_items, calibration_work_items
):
    calibration_pairs = {
        work_item_source_pair(item) for item in calibration_work_items
    }
    source_by_pair = {}
    for item in sorted(source_work_items, key=work_item_sort_key):
        pair = work_item_source_pair(item)
        if pair in calibration_pairs or not item["paragraph"].strip():
            continue
        source_by_pair.setdefault(pair, item)

    candidates = []
    dataset_pairs_without_paragraph = 0
    dataset_pairs_excluded = 0
    for row in dataset_rows:
        pair = (row["lob_id"], dataset_bill_id(row["bill_key"]))
        if pair in calibration_pairs:
            dataset_pairs_excluded += 1
            continue
        source_item = source_by_pair.get(pair)
        if source_item is None:
            dataset_pairs_without_paragraph += 1
            continue
        their_label = _canonical_label(
            row["label_5_class"], THEIR_FIVE_CLASS_LABELS
        )
        candidates.append(
            {
                "bill_no": source_item["bill_no"],
                "short_title": source_item["short_title"],
                "official_title": source_item["official_title"],
                "paragraph": source_item["paragraph"],
                "their_label_5_class": their_label,
                "mapped_label": map_their_label_to_ours(their_label),
                "report_uuid": source_item["report_uuid"],
                "source_pair": {
                    "lob_id": pair[0],
                    "bill_id": pair[1],
                },
                "source_pair_sha256": _source_pair_hash(pair),
            }
        )
    candidates.sort(
        key=lambda item: (
            item["their_label_5_class"],
            item["source_pair_sha256"],
        )
    )

    exemplars = []
    selected_by_group = {}
    shortfall_by_group = {}
    for group_name, labels in FEWSHOT_GROUPS:
        selected = [
            item
            for item in candidates
            if item["their_label_5_class"] in labels
        ][:FEWSHOT_PER_GROUP]
        exemplars.extend(selected)
        selected_by_group[group_name] = len(selected)
        shortfall_by_group[group_name] = FEWSHOT_PER_GROUP - len(selected)
    receipt = {
        "requested_total": FEWSHOT_PER_GROUP * len(FEWSHOT_GROUPS),
        "selected_total": len(exemplars),
        "requested_per_group": FEWSHOT_PER_GROUP,
        "selected_by_group": selected_by_group,
        "shortfall_by_group": shortfall_by_group,
        "candidate_pairs_after_leakage_guard": len(candidates),
        "dataset_pairs_excluded_by_calibration_set": dataset_pairs_excluded,
        "dataset_pairs_without_available_paragraph": (
            dataset_pairs_without_paragraph
        ),
    }
    return exemplars, receipt


def _validate_cached_exemplars(value, path):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise builder.IngestError(
            f"exemplar cache {path} must be a version 1 object"
        )
    exemplars = value.get("exemplars")
    receipt = value.get("selection_receipt")
    if not isinstance(exemplars, list) or not isinstance(receipt, dict):
        raise builder.IngestError(
            f"exemplar cache {path} has malformed exemplar data"
        )
    required_strings = {
        "bill_no",
        "short_title",
        "official_title",
        "paragraph",
        "their_label_5_class",
        "mapped_label",
        "report_uuid",
        "source_pair_sha256",
    }
    for index, exemplar in enumerate(exemplars, start=1):
        if not isinstance(exemplar, dict):
            raise builder.IngestError(
                f"exemplar cache {path} exemplar {index} is not an object"
            )
        if any(not isinstance(exemplar.get(field), str) for field in required_strings):
            raise builder.IngestError(
                f"exemplar cache {path} exemplar {index} has malformed fields"
            )
        source_pair = exemplar.get("source_pair")
        if (
            not isinstance(source_pair, dict)
            or set(source_pair) != {"lob_id", "bill_id"}
            or not all(isinstance(value, str) and value for value in source_pair.values())
        ):
            raise builder.IngestError(
                f"exemplar cache {path} exemplar {index} has malformed source_pair"
            )
        pair = (source_pair["lob_id"], source_pair["bill_id"])
        if exemplar["source_pair_sha256"] != _source_pair_hash(pair):
            raise builder.IngestError(
                f"exemplar cache {path} exemplar {index} has wrong source pair hash"
            )
        their_label = _canonical_label(
            exemplar["their_label_5_class"], THEIR_FIVE_CLASS_LABELS
        )
        if exemplar["mapped_label"] != map_their_label_to_ours(their_label):
            raise builder.IngestError(
                f"exemplar cache {path} exemplar {index} has wrong label mapping"
            )
    return exemplars, receipt


def _read_exemplar_cache(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise builder.IngestError(
            f"cannot read exemplar cache {path}: {exc}"
        ) from exc
    exemplars, receipt = _validate_cached_exemplars(value, path)
    return exemplars, receipt, hashlib.sha256(raw).hexdigest()


def prepare_prompt_variant(
    prompt_variant,
    calibration_work_items,
    dataset_rows=None,
    source_work_items=None,
    exemplar_cache=None,
):
    if prompt_variant == "zeroshot":
        return [], {"prompt_variant": "zeroshot"}
    if prompt_variant != "fewshot":
        raise builder.IngestError(
            f"unsupported prompt variant {prompt_variant!r}"
        )

    cache_used = False
    cache_sha256 = None
    if exemplar_cache and os.path.isfile(exemplar_cache):
        exemplars, selection_receipt, cache_sha256 = _read_exemplar_cache(
            exemplar_cache
        )
        cache_used = True
    else:
        if dataset_rows is None or source_work_items is None:
            raise builder.IngestError(
                "fewshot prompt requires the position dataset and assembled "
                "source work items when no exemplar cache exists"
            )
        exemplars, selection_receipt = select_fewshot_exemplars(
            dataset_rows, source_work_items, calibration_work_items
        )
        if exemplar_cache:
            write_json_atomic(
                exemplar_cache,
                {
                    "version": 1,
                    "exemplars": exemplars,
                    "selection_receipt": selection_receipt,
                },
            )
            exemplars, selection_receipt, cache_sha256 = _read_exemplar_cache(
                exemplar_cache
            )

    calibration_pairs = {
        work_item_source_pair(item) for item in calibration_work_items
    }
    leaked_pairs = []
    for exemplar in exemplars:
        source_pair = exemplar["source_pair"]
        pair = (source_pair["lob_id"], source_pair["bill_id"])
        if pair in calibration_pairs:
            leaked_pairs.append(pair)
    if leaked_pairs:
        raise builder.IngestError(
            "fewshot exemplar cache contains a calibration work-item pair: "
            f"{leaked_pairs[0][0]} {leaked_pairs[0][1]}"
        )

    rendered_prefix = render_fewshot_prefix(exemplars)
    audit = {
        "prompt_variant": "fewshot",
        "requested_exemplars": FEWSHOT_PER_GROUP * len(FEWSHOT_GROUPS),
        "selected_exemplars": len(exemplars),
        "exemplars": exemplars,
        "selection_receipt": selection_receipt,
        "rendered_prompt_prefix_sha256": hashlib.sha256(
            rendered_prefix.encode("utf-8")
        ).hexdigest(),
        "exemplar_cache_path": (
            None if exemplar_cache is None else os.path.abspath(exemplar_cache)
        ),
        "exemplar_cache_sha256": cache_sha256,
        "exemplar_cache_used": cache_used,
    }
    return exemplars, audit


def _read_api_key(environ, api_key_file):
    value = environ.get("ANTHROPIC_API_KEY")
    if value is not None and value.strip():
        return value.strip()
    if api_key_file:
        try:
            with open(api_key_file, "r", encoding="utf-8-sig") as handle:
                value = handle.read().strip()
        except OSError as exc:
            raise builder.IngestError(
                f"cannot read --api-key-file: {exc}"
            ) from exc
        if value:
            return value
        raise builder.IngestError("--api-key-file is empty")
    raise builder.IngestError(
        "live classification requires ANTHROPIC_API_KEY or --api-key-file; "
        "--dry-run and --calibrate-coverage do not require a key"
    )


def anthropic_reply(
    work_item,
    api_key,
    prompt_variant="zeroshot",
    exemplars=None,
    opener=urllib.request.urlopen,
    sleeper=time.sleep,
):
    prompt = build_prompt(work_item, prompt_variant, exemplars)
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 16,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    last_error = None
    for attempt in range(6):
        request = urllib.request.Request(
            ANTHROPIC_URL,
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "user-agent": "lda-bill-position-classifier/1.0",
            },
            method="POST",
        )
        try:
            with opener(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload.get("content")
            if not isinstance(content, list):
                raise ValueError("response content is not a list")
            texts = [
                block.get("text")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if len(texts) != 1 or not isinstance(texts[0], str):
                raise ValueError("response does not contain exactly one text block")
            return texts[0]
        except Exception as exc:
            last_error = exc
            if attempt < 5:
                sleeper(2 ** attempt)
    if isinstance(last_error, urllib.error.HTTPError):
        detail = f"HTTP {last_error.code}"
    else:
        detail = type(last_error).__name__
    raise builder.IngestError(
        f"Anthropic API failed after 5 retries ({detail})"
    ) from last_error


def classification_receipt(
    work_item,
    api_key,
    prompt_variant="zeroshot",
    exemplars=None,
    **kwargs,
):
    receipt = reply_receipt(
        anthropic_reply(
            work_item,
            api_key,
            prompt_variant=prompt_variant,
            exemplars=exemplars,
            **kwargs,
        )
    )
    receipt.update(
        {
            "model": MODEL,
            "prompt_sha256": prompt_sha256(
                work_item, prompt_variant, exemplars
            ),
        }
    )
    return receipt


def load_resume_manifest(path):
    if not os.path.isfile(path):
        return {"model": MODEL, "completed": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise builder.IngestError(
            f"cannot read classification manifest {path}: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("model") != MODEL:
        raise builder.IngestError("classification manifest model mismatch")
    completed = manifest.get("completed")
    if not isinstance(completed, dict):
        raise builder.IngestError(
            "classification manifest completed must be an object"
        )
    for digest, receipt in completed.items():
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise builder.IngestError(
                "classification manifest has malformed content hash"
            )
        if not isinstance(receipt, dict) or receipt.get("status") not in {
            "ok",
            "unparseable",
        }:
            raise builder.IngestError(
                "classification manifest has malformed receipt"
            )
    return manifest


def pending_work_items(work_items, manifest):
    pending_by_hash = {}
    completed = manifest.get("completed", {})
    for item in sorted(work_items, key=work_item_sort_key):
        digest = work_item_content_hash(item)
        if digest not in completed:
            pending_by_hash.setdefault(digest, item)
    return [pending_by_hash[digest] for digest in sorted(pending_by_hash)]


def classify_work_items(
    work_items,
    api_key,
    workers,
    max_unparseable,
    manifest_path,
    classify_func=classification_receipt,
    prompt_variant="zeroshot",
    exemplars=None,
    prompt_audit=None,
):
    if workers <= 0:
        raise builder.IngestError("--workers must be positive")
    if not 0 <= max_unparseable <= 1:
        raise builder.IngestError("--max-unparseable must be between 0 and 1")
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
    manifest = load_resume_manifest(manifest_path)
    prompt_audit = (
        {"prompt_variant": prompt_variant}
        if prompt_audit is None
        else prompt_audit
    )
    expected_prefix_hash = prompt_audit.get("rendered_prompt_prefix_sha256")
    stored_variant = manifest.get("prompt_variant", "zeroshot")
    stored_prefix_hash = manifest.get("rendered_prompt_prefix_sha256")
    if manifest["completed"] and (
        stored_variant != prompt_variant
        or stored_prefix_hash != expected_prefix_hash
    ):
        raise builder.IngestError(
            "classification manifest prompt configuration mismatch"
        )
    manifest["prompt_variant"] = prompt_variant
    manifest["rendered_prompt_prefix_sha256"] = expected_prefix_hash
    manifest["prompt_audit"] = prompt_audit
    manifest.update(title_availability_receipt(work_items))
    completed = manifest["completed"]
    pending = pending_work_items(work_items, manifest)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                classify_func,
                item,
                api_key,
                prompt_variant=prompt_variant,
                exemplars=exemplars,
            ): item
            for item in pending
        }
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            receipt = future.result()
            digest = work_item_content_hash(item)
            completed[digest] = receipt
            write_json_atomic(manifest_path, manifest)
    write_json_atomic(manifest_path, manifest)
    results = []
    for item in sorted(work_items, key=work_item_sort_key):
        digest = work_item_content_hash(item)
        if digest not in completed:
            raise builder.IngestError(
                f"classification manifest lost work item {digest}"
            )
        results.append((item, completed[digest]))
    unparseable = sum(
        receipt["status"] == "unparseable" for _item, receipt in results
    )
    rate = unparseable / len(results) if results else 0.0
    if rate > max_unparseable:
        raise builder.IngestError(
            f"unparseable model replies {unparseable}/{len(results)} "
            f"({rate:.6f}) exceed threshold {max_unparseable:.6f}"
        )
    return results


def classification_result_rows(results):
    rows = []
    for item, receipt in sorted(
        results, key=lambda pair: work_item_sort_key(pair[0])
    ):
        type_token, number, congress = _bill_key_tuple(item)
        row = dict(item)
        row.update(
            {
                "bill_type": type_token,
                "bill_number": number,
                "congress": congress,
                "our_position": receipt["position"],
                "classification_status": receipt["status"],
                "model": receipt["model"],
                "prompt_sha256": receipt["prompt_sha256"],
            }
        )
        rows.append(row)
    return rows


def flat_csv_sibling_path(path):
    if not path.lower().endswith(".csv"):
        return None
    return os.path.splitext(path)[0] + ".flat.csv"


def write_flat_result_csv(path, rows):
    flattened = []
    for row in rows:
        flattened.append(
            {
                field: row.get(field, "")
                for field in FLAT_RESULT_FIELDS
            }
        )

    def writer(handle):
        output = csv.DictWriter(handle, fieldnames=FLAT_RESULT_FIELDS)
        output.writeheader()
        output.writerows(flattened)

    _atomic_text(path, writer)


def write_classification_outputs(path, results):
    rows = classification_result_rows(results)
    write_jsonl_atomic(path, rows)
    sibling = flat_csv_sibling_path(path)
    if sibling is not None:
        write_flat_result_csv(sibling, rows)
    return rows, sibling


def classification_metrics(truth, predicted, classes):
    if len(truth) != len(predicted):
        raise builder.IngestError("metric inputs have different lengths")
    confusion = {
        actual: {guess: 0 for guess in classes} for actual in classes
    }
    for actual, guess in zip(truth, predicted):
        if actual not in confusion or guess not in confusion[actual]:
            raise builder.IngestError("metric input contains an invalid label")
        confusion[actual][guess] += 1
    per_class = {}
    for label in classes:
        true_positive = confusion[label][label]
        false_positive = sum(
            confusion[actual][label] for actual in classes if actual != label
        )
        false_negative = sum(
            confusion[label][guess] for guess in classes if guess != label
        )
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(confusion[label].values()),
        }
    correct = sum(confusion[label][label] for label in classes)
    return {
        "n": len(truth),
        "items": len(truth),
        "accuracy": correct / len(truth) if truth else None,
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def _source_group(annotation_source):
    normalized = annotation_source.strip().casefold()
    if "llm" in normalized:
        return "llm"
    if "gnn" in normalized:
        return "gnn"
    return "other"


def agreement_report(results, prompt_audit=None):
    rows = []
    unparseable = 0
    for item, receipt in results:
        if receipt["status"] != "ok":
            unparseable += 1
            continue
        their_five = _canonical_label(
            item["their_label_5_class"], THEIR_FIVE_CLASS_LABELS
        )
        _dataset_three = _canonical_label(
            item["their_label_3_class"], THREE_CLASS_LABELS
        )
        ours_five = _canonical_label(receipt["position"], OUR_LABELS)
        rows.append(
            {
                "source_group": _source_group(item["annotation_source"]),
                "their_five": their_five,
                "ours_five": ours_five,
                "their_three": collapse_their_five_to_three(their_five),
                "ours_three": collapse_our_to_three(ours_five),
            }
        )
    groups = {}
    for name in ("overall", "llm", "gnn"):
        selected = (
            rows
            if name == "overall"
            else [row for row in rows if row["source_group"] == name]
        )
        restricted_five = [
            row
            for row in selected
            if row["their_five"] in OUR_LABELS
        ]
        primary = classification_metrics(
            [row["their_three"] for row in selected],
            [row["ours_three"] for row in selected],
            THREE_CLASS_LABELS,
        )
        restricted_secondary = classification_metrics(
            [row["their_five"] for row in restricted_five],
            [row["ours_five"] for row in restricted_five],
            OUR_LABELS,
        )
        restricted_secondary["restriction"] = (
            "their label_5_class is in our five-class vocabulary; "
            "Engage rows excluded"
        )
        groups[name] = {
            "primary_three_class": primary,
            "restricted_secondary_five_class": restricted_secondary,
        }
    report = {
        "model": MODEL,
        "classified_items": len(results),
        "parseable_items": len(rows),
        "unparseable_items": unparseable,
        "primary_agreement": "three_class",
        "groups": groups,
        "prompt": (
            {"prompt_variant": "zeroshot"}
            if prompt_audit is None
            else prompt_audit
        ),
    }
    report.update(
        title_availability_receipt([item for item, _receipt in results])
    )
    return report


def _parse_lob_ids(value):
    if value is None:
        return None
    values = [item.strip() for item in value.split(",")]
    if not values or any(not item for item in values):
        raise builder.IngestError("--lob-ids must be a comma-separated list")
    if len(values) != len(set(values)):
        raise builder.IngestError("--lob-ids contains a duplicate")
    return set(values)


def _default_classifier_paths(environ):
    paths = builder.default_paths(environ.get("LOCALAPPDATA"))
    dataset = os.path.join(
        paths["raw_dir"],
        "positions",
        "bill_position_dataset_input",
        "bill_position_dataset.csv",
    )
    return paths, dataset


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Assemble or classify LDA bill-position work items."
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--calibrate-coverage", action="store_true")
    modes.add_argument("--calibrate", action="store_true")
    modes.add_argument("--classify", action="store_true")
    parser.add_argument("--worklist", metavar="PATH")
    parser.add_argument("--years", default="2021")
    parser.add_argument("--input", metavar="JSONL")
    parser.add_argument("--output", metavar="PATH")
    parser.add_argument("--report", metavar="PATH")
    parser.add_argument("--dataset", metavar="CSV")
    parser.add_argument("--lob-ids")
    parser.add_argument("--api-key-file", metavar="PATH")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-unparseable", type=float, default=0.02)
    parser.add_argument(
        "--prompt-variant",
        choices=("zeroshot", "fewshot"),
        default="zeroshot",
    )
    parser.add_argument("--exemplar-cache", metavar="PATH")
    return parser.parse_args(list(argv))


def _run(environ=None, argv=()):
    environ = os.environ if environ is None else environ
    args = _parse_args(argv)
    paths, default_dataset = _default_classifier_paths(environ)
    years = builder.parse_years(args.years)
    year_token = "_".join(str(year) for year in years)
    os.makedirs(paths["derived_dir"], exist_ok=True)

    if args.dry_run or args.calibrate_coverage:
        if args.input:
            raise builder.IngestError(
                "--input is only valid with --calibrate or --classify"
            )
        work_items, assembly_receipt = assemble_work_items(
            environ, args.worklist, years, return_receipt=True
        )
        title_receipt = title_availability_receipt(work_items)
        title_receipt.update(assembly_receipt)
        if args.dry_run:
            output = args.output or os.path.join(
                paths["derived_dir"],
                f"bill_position_work_items_{year_token}.jsonl",
            )
            write_jsonl_atomic(output, work_items)
            report_path = args.report or output + ".receipt.json"
            write_json_atomic(report_path, title_receipt)
            print(
                f"bill position dry run: {len(work_items)} work items | {output}"
            )
            return 0
        dataset = load_position_dataset(args.dataset or default_dataset)
        joined, report = calibration_join(
            work_items, dataset, _parse_lob_ids(args.lob_ids)
        )
        report.update(title_availability_receipt(joined))
        report.update(assembly_receipt)
        output = args.output or os.path.join(
            paths["derived_dir"],
            f"bill_position_calibration_work_items_{year_token}.jsonl",
        )
        report_path = args.report or output + ".coverage.json"
        write_jsonl_atomic(output, joined)
        write_json_atomic(report_path, report)
        print(
            "bill position calibration coverage: "
            f"overall={report['overlapping_pairs_overall']} "
            f"subset={report['overlapping_pairs_subset']} | {output}"
        )
        return 0

    if not args.input:
        raise builder.IngestError("--calibrate and --classify require --input")
    work_items = read_jsonl(args.input)
    if args.calibrate:
        for index, item in enumerate(work_items, start=1):
            for field in (
                "their_label_3_class",
                "their_label_5_class",
                "annotation_source",
            ):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise builder.IngestError(
                        f"calibration work item {index} missing {field}"
                    )
        output = args.output or os.path.join(
            paths["derived_dir"], "bill_position_calibration_results.jsonl"
        )
    else:
        output = args.output or os.path.join(
            paths["derived_dir"], "bill_positions.jsonl"
        )
    source_assembly_receipt = None
    if (
        args.prompt_variant == "fewshot"
        and not (
            args.exemplar_cache and os.path.isfile(args.exemplar_cache)
        )
    ):
        exemplar_dataset = load_position_dataset(
            args.dataset or default_dataset
        )
        source_work_items, source_assembly_receipt = assemble_work_items(
            environ, args.worklist, years, return_receipt=True
        )
    else:
        exemplar_dataset = None
        source_work_items = None
    exemplars, prompt_audit = prepare_prompt_variant(
        args.prompt_variant,
        work_items,
        dataset_rows=exemplar_dataset,
        source_work_items=source_work_items,
        exemplar_cache=args.exemplar_cache,
    )
    if source_assembly_receipt is not None:
        prompt_audit["source_assembly_receipt"] = source_assembly_receipt

    api_key = _read_api_key(environ, args.api_key_file)
    manifest_path = output + ".manifest.json"
    results = classify_work_items(
        work_items,
        api_key,
        args.workers,
        args.max_unparseable,
        manifest_path,
        prompt_variant=args.prompt_variant,
        exemplars=exemplars,
        prompt_audit=prompt_audit,
    )
    _rows, flat_sibling = write_classification_outputs(output, results)
    if args.classify:
        if args.report or args.prompt_variant == "fewshot":
            report_path = args.report or output + ".prompt.json"
            classification_report = {
                "model": MODEL,
                "classified_items": len(results),
                "prompt": prompt_audit,
            }
            classification_report.update(title_availability_receipt(work_items))
            write_json_atomic(report_path, classification_report)
        print(f"bill positions: {len(results)} work items | {output}")
        if flat_sibling is not None:
            print(f"bill positions flat CSV: {flat_sibling}")
        return 0

    report = agreement_report(results, prompt_audit)
    report_path = args.report or output + ".agreement.json"
    write_json_atomic(report_path, report)
    print(f"bill position calibration: {len(results)} work items | {output}")
    if flat_sibling is not None:
        print(f"bill position calibration flat CSV: {flat_sibling}")
    return 0


def main(argv=None):
    try:
        return _run(argv=sys.argv[1:] if argv is None else argv)
    except builder.IngestError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: bill position classifier failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
