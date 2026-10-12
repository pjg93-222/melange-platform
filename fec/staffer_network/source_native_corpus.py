# -*- coding: utf-8 -*-
"""Build an immutable, restricted, source-native staffer research corpus."""

from __future__ import annotations

from collections import Counter
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover
    from fec.lane_file_io import atomic_write_bytes

from .littlesis_contract import CATEGORY_NAMES, _record_id, stream_json_array
from .source_manifest import canonical_json_bytes, sha256_file
from .schema_validation import SchemaValidationError, validate_instance
from .validate import assert_public_safe, find_prohibited_values


RECEIPT_SCHEMA_ID = "staffer-network-source-native-corpus-receipt-v1"
ENTITY_SCHEMA_ID = "staffer-network-littlesis-entity-assertion-v1"
RELATIONSHIP_SCHEMA_ID = "staffer-network-littlesis-relationship-assertion-v1"
LEGACY_SCHEMA_ID = "staffer-network-legacy-source-native-record-v1"
IN_SCOPE_CATEGORY_IDS = {1, 2, 7}
PRODUCTION_INPUT_PINS = {
    "entities": {
        "sha256": "5cc0039aa43e650b46bfa9e9bfc1813c67969cae1bdb9c9e8770fc117cd29d3c",
        "bytes": 76691985,
    },
    "relationships": {
        "sha256": "c11d7058cb13d264854f703310fddf30c1391ef604c0164847945939fd38d489",
        "bytes": 110381587,
    },
}
PRODUCTION_PROFILE_SHA256 = (
    "7c7eb1e306a858e6a799124d2636488c6a7e57cfa091f0828826f93ef0199d0a"
)
PRODUCTION_LEGACY_PIN = {
    "sha256": "439bb67f7093f28cd67010cdc12e9f7a86eeee9e8fd36c81709073260a17ff48",
    "bytes": 1144203,
    "records": 5773,
}
_FULL_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_BARE_YEAR = re.compile(r"^\d{4}$")


class SourceNativeCorpusError(RuntimeError):
    pass


def _require_restricted_output(output: Path, synced_roots: Iterable[str | Path]) -> None:
    for value in synced_roots:
        root = Path(value).resolve()
        if output == root or output.is_relative_to(root):
            raise SourceNativeCorpusError("source-native corpus must remain outside synced roots")


def _default_corpus_root() -> Path:
    try:
        from paths import STAFFER_NETWORK_CORPUS
    except ImportError:  # pragma: no cover - namespace package invocation
        from fec.paths import STAFFER_NETWORK_CORPUS
    return Path(STAFFER_NETWORK_CORPUS).resolve()


def _default_synced_roots() -> tuple[Path, ...]:
    roots: set[Path] = set()
    for key in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        value = os.environ.get(key)
        if value:
            roots.add(Path(value).resolve())
    conventional = Path.home() / "OneDrive"
    if conventional.exists():
        roots.add(conventional.resolve())
    return tuple(sorted(roots, key=str))


def _file_descriptor(path: Path, *, role: str) -> dict[str, Any]:
    return {"role": role, "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _assert_pin(path: Path, pin: Mapping[str, Any], role: str) -> None:
    if path.stat().st_size != pin["bytes"] or sha256_file(path) != pin["sha256"]:
        raise SourceNativeCorpusError(f"{role} input does not match its sealed byte pin")


def _profile_files(profile: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["role"]): dict(row) for row in profile.get("files", [])}


def _date_precision(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    if _BARE_YEAR.fullmatch(text):
        return "year"
    match = _FULL_DATE.fullmatch(text)
    if match is None:
        raise SourceNativeCorpusError(f"unsupported LittleSis date precision: {text!r}")
    _, month, day = match.groups()
    if month == "00":
        # Native data occasionally carries values such as 2008-00-10.  The
        # literal is preserved, but a day without a month cannot support day
        # precision; degrade to the only defensible precision, the year.
        return "year"
    if day == "00":
        return "month"
    return "day"


def relationship_precision(start_date: Any, end_date: Any) -> str:
    values = [
        precision for precision in (
            _date_precision(start_date), _date_precision(end_date)
        ) if precision is not None
    ]
    if not values:
        return "undated-only"
    rank = {"year": 0, "month": 1, "day": 2}
    return min(values, key=rank.__getitem__)


def _restricted_text(value: Any, redactions: list[int]) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if find_prohibited_values(text):
        redactions[0] += 1
        return None
    return text


def _entity_assertion(
    record: Mapping[str, Any], entity_ids: set[int], redactions: list[int]
) -> tuple[dict[str, Any], bool]:
    attributes = record["attributes"]
    entity_id = int(attributes["id"])
    name = _restricted_text(attributes.get("name"), redactions)
    if name is None:
        name = "[source-name-unavailable]"
    aliases = []
    for value in attributes.get("aliases") or []:
        text = _restricted_text(value, redactions)
        if text is not None:
            aliases.append(text)
    aliases = sorted(set(aliases), key=lambda value: (value.casefold(), value))
    types = sorted({str(value).strip() for value in attributes.get("types") or [] if str(value).strip()})
    parent = attributes.get("parent_id")
    parent_missing = False
    parent_id = None
    if parent not in (None, ""):
        try:
            parent_number = int(parent)
        except (TypeError, ValueError):
            parent_missing = True
        else:
            if parent_number in entity_ids:
                parent_id = f"littlesis:entity:{parent_number}"
            else:
                parent_missing = True
    source_url = None
    links = record.get("links")
    if isinstance(links, Mapping):
        source_url = _restricted_text(links.get("self"), redactions)
    assertion = {
        "schema_id": ENTITY_SCHEMA_ID,
        "source_system_id": "littlesis-bulk",
        "source_entity_id": f"littlesis:entity:{entity_id}",
        "source_numeric_id": entity_id,
        "name": name,
        "aliases": aliases,
        "primary_ext": _restricted_text(attributes.get("primary_ext"), redactions),
        "types": types,
        "blurb": _restricted_text(attributes.get("blurb"), redactions),
        "parent_source_entity_id": parent_id,
        "raw_start_date": attributes.get("start_date"),
        "raw_end_date": attributes.get("end_date"),
        "source_updated_at": attributes.get("updated_at"),
        "source_url": source_url,
    }
    assert_public_safe(assertion)
    return assertion, parent_missing


def _category_attributes(
    category_id: int,
    attributes: Mapping[str, Any],
    redactions: list[int],
    entity_ids: set[int],
    boss_redactions: list[int],
) -> dict[str, Any]:
    native = attributes.get("category_attributes")
    native = native if isinstance(native, Mapping) else {}
    if category_id == 1:
        boss = native.get("boss_id")
        boss_id = None
        if boss not in (None, ""):
            boss_number = None
            if isinstance(boss, int) and not isinstance(boss, bool):
                boss_number = boss
            elif isinstance(boss, str) and re.fullmatch(r"[1-9][0-9]*", boss):
                boss_number = int(boss)
            else:
                boss_redactions[0] += 1
            if boss_number is not None:
                if boss_number in entity_ids:
                    boss_id = f"littlesis:entity:{boss_number}"
                else:
                    boss_redactions[0] += 1
        return {
            "is_board": native.get("is_board"),
            "is_executive": native.get("is_executive"),
            "is_employee": native.get("is_employee"),
            "compensation": native.get("compensation"),
            "boss_source_entity_id": boss_id,
        }
    if category_id == 2:
        degree = native.get("degree_id")
        return {
            "degree_source_id": (
                f"littlesis:degree:{int(degree)}" if degree not in (None, "") else None
            ),
            "field": _restricted_text(native.get("field"), redactions),
            "is_dropout": native.get("is_dropout"),
        }
    return {"filings": attributes.get("filings")}


def _relationship_assertion(
    record: Mapping[str, Any],
    redactions: list[int],
    entity_ids: set[int],
    boss_redactions: list[int],
) -> dict[str, Any]:
    attributes = record["attributes"]
    relationship_id = int(attributes["id"])
    category_id = int(attributes["category_id"])
    start_date = attributes.get("start_date")
    end_date = attributes.get("end_date")
    assertion = {
        "schema_id": RELATIONSHIP_SCHEMA_ID,
        "source_system_id": "littlesis-bulk",
        "source_relationship_id": f"littlesis:relationship:{relationship_id}",
        "source_numeric_id": relationship_id,
        "subject_source_entity_id": f"littlesis:entity:{int(attributes['entity1_id'])}",
        "object_source_entity_id": f"littlesis:entity:{int(attributes['entity2_id'])}",
        "category_id": category_id,
        "category_name": CATEGORY_NAMES[category_id],
        "description1": _restricted_text(attributes.get("description1"), redactions),
        "description2": _restricted_text(attributes.get("description2"), redactions),
        "description": _restricted_text(attributes.get("description"), redactions),
        "raw_start_date": start_date,
        "raw_end_date": end_date,
        "precision_bucket": relationship_precision(start_date, end_date),
        "temporal_weight": 0,
        "source_is_current": attributes.get("is_current"),
        "source_updated_at": attributes.get("updated_at"),
        "source_url": _restricted_text(record.get("self"), redactions),
        "source_category_attributes": _category_attributes(
            category_id, attributes, redactions, entity_ids, boss_redactions
        ),
    }
    assert_public_safe(assertion)
    return assertion


def _open_deterministic_gzip(path: Path):
    raw = path.open("wb")
    stream = gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0)
    return raw, stream


def _assert_profile_matches(
    observed: Mapping[str, Any], profile: Mapping[str, Any]
) -> None:
    keys = (
        "entity_observed", "entity_accepted", "entity_invalid", "entity_duplicates",
        "relationship_observed", "relationship_accepted", "relationship_rejected",
        "relationship_invalid", "relationship_duplicates", "relationship_dangling_rejected",
        "category_observed", "category_accepted", "undated_position_accepted",
    )
    mismatches = [key for key in keys if observed.get(key) != profile.get(key)]
    if mismatches:
        raise SourceNativeCorpusError(
            "emit-time census does not match the sealed profile: " + ", ".join(mismatches)
        )


def _validate_legacy_records(
    path: Path,
    expected_records: int,
    *,
    schema_path: str | Path | None = None,
) -> None:
    schema_file = Path(
        schema_path
        or Path(__file__).with_name("schemas") / "legacy_source_native_record.schema.json"
    )
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    count = 0
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise SourceNativeCorpusError(
                    f"blank legacy collection row: {line_number}"
                )
            record = json.loads(line)
            assert_public_safe(record)
            try:
                validate_instance(record, schema)
            except SchemaValidationError as exc:
                raise SourceNativeCorpusError(
                    f"legacy collection schema failure at row {line_number}: {exc}"
                ) from exc
            count += 1
    if count != expected_records:
        raise SourceNativeCorpusError("legacy collection record count does not match its seal")


def build_source_native_corpus(
    entities_path: str | Path,
    relationships_path: str | Path,
    profile_receipt_path: str | Path,
    legacy_seed_path: str | Path,
    legacy_seed_receipt_path: str | Path,
    output_root: str | Path,
    *,
    generated_at: str,
    synced_roots: Iterable[str | Path] | None = None,
    configured_corpus_root: str | Path | None = None,
    input_pins: Mapping[str, Mapping[str, Any]] = PRODUCTION_INPUT_PINS,
    expected_profile_sha256: str = PRODUCTION_PROFILE_SHA256,
    legacy_pin: Mapping[str, Any] = PRODUCTION_LEGACY_PIN,
) -> dict[str, Any]:
    """Build one frozen local corpus and return its public-safe aggregate receipt."""

    entities_file = Path(entities_path).resolve()
    relationships_file = Path(relationships_path).resolve()
    profile_file = Path(profile_receipt_path).resolve()
    legacy_file = Path(legacy_seed_path).resolve()
    legacy_receipt_file = Path(legacy_seed_receipt_path).resolve()
    output = Path(output_root).resolve()
    corpus_root = Path(configured_corpus_root or _default_corpus_root()).resolve()
    if output != corpus_root and not output.is_relative_to(corpus_root):
        raise SourceNativeCorpusError(
            "source-native corpus output is outside the configured machine-local root"
        )
    _require_restricted_output(
        output, _default_synced_roots() if synced_roots is None else synced_roots
    )
    if output.exists():
        raise SourceNativeCorpusError(f"source-native corpus generation already exists: {output}")
    _assert_pin(entities_file, input_pins["entities"], "LittleSis entities")
    _assert_pin(relationships_file, input_pins["relationships"], "LittleSis relationships")
    if sha256_file(profile_file) != expected_profile_sha256:
        raise SourceNativeCorpusError("LittleSis profile receipt hash mismatch")
    profile = json.loads(profile_file.read_text(encoding="utf-8"))
    if profile.get("status") != "PASS" or profile.get("source_native_ready") is not True:
        raise SourceNativeCorpusError("LittleSis profile is not source-native ready")
    if _profile_files(profile) != {
        role: {"role": role, **dict(pin)} for role, pin in input_pins.items()
    }:
        raise SourceNativeCorpusError("LittleSis profile file pins do not match build inputs")
    _assert_pin(legacy_file, legacy_pin, "legacy source-native seed")
    legacy_receipt = json.loads(legacy_receipt_file.read_text(encoding="utf-8"))
    if legacy_receipt.get("record_file") != {
        "records": legacy_pin["records"],
        "sha256": legacy_pin["sha256"],
        "bytes": legacy_pin["bytes"],
    }:
        raise SourceNativeCorpusError("legacy receipt does not bind the supplied seed")
    _validate_legacy_records(legacy_file, int(legacy_pin["records"]))

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        entity_ids: set[int] = set()
        entity_observed = 0
        entity_invalid = 0
        entity_duplicates = 0
        for record in stream_json_array(entities_file):
            entity_observed += 1
            entity_id = _record_id(record)
            if entity_id is None:
                entity_invalid += 1
            elif entity_id in entity_ids:
                entity_duplicates += 1
            else:
                entity_ids.add(entity_id)

        redactions = [0]
        boss_redactions = [0]
        parent_references_redacted = 0
        entities_output = staging / "littlesis_entities.ndjson.gz"
        raw, compressed = _open_deterministic_gzip(entities_output)
        try:
            entities_emitted = 0
            emitted_entity_ids: set[int] = set()
            for record in stream_json_array(entities_file):
                entity_id = _record_id(record)
                if (
                    entity_id is None
                    or entity_id not in entity_ids
                    or entity_id in emitted_entity_ids
                ):
                    continue
                assertion, parent_missing = _entity_assertion(record, entity_ids, redactions)
                compressed.write(canonical_json_bytes(assertion))
                emitted_entity_ids.add(entity_id)
                entities_emitted += 1
                parent_references_redacted += int(parent_missing)
        finally:
            compressed.close()
            raw.close()

        relationships_output = staging / "littlesis_relationship_assertions.ndjson.gz"
        raw, compressed = _open_deterministic_gzip(relationships_output)
        relationship_ids: set[int] = set()
        relationship_observed = 0
        relationship_invalid = 0
        relationship_duplicates = 0
        relationship_dangling_rejected = 0
        relationship_emitted = 0
        relationship_out_of_scope = 0
        emitted_endpoint_missing = 0
        undated_position_emitted = 0
        category_observed: Counter[str] = Counter()
        category_accepted: Counter[str] = Counter()
        category_emitted: Counter[str] = Counter()
        category_out_of_scope: Counter[str] = Counter()
        dangling_ids: list[int] = []
        try:
            for record in stream_json_array(relationships_file):
                relationship_observed += 1
                relationship_id = _record_id(record)
                attributes = record.get("attributes") if isinstance(record, Mapping) else None
                if relationship_id is None or not isinstance(attributes, Mapping):
                    relationship_invalid += 1
                    continue
                try:
                    endpoint_1 = int(attributes["entity1_id"])
                    endpoint_2 = int(attributes["entity2_id"])
                    category_id = int(attributes["category_id"])
                except (KeyError, TypeError, ValueError):
                    relationship_invalid += 1
                    continue
                category_name = CATEGORY_NAMES.get(category_id, f"category-{category_id}")
                category_observed[category_name] += 1
                if relationship_id in relationship_ids:
                    relationship_duplicates += 1
                    continue
                relationship_ids.add(relationship_id)
                if endpoint_1 not in entity_ids or endpoint_2 not in entity_ids:
                    relationship_dangling_rejected += 1
                    dangling_ids.append(relationship_id)
                    continue
                category_accepted[category_name] += 1
                if category_id not in IN_SCOPE_CATEGORY_IDS:
                    relationship_out_of_scope += 1
                    category_out_of_scope[category_name] += 1
                    continue
                assertion = _relationship_assertion(
                    record, redactions, entity_ids, boss_redactions
                )
                if (
                    int(assertion["subject_source_entity_id"].rsplit(":", 1)[1]) not in entity_ids
                    or int(assertion["object_source_entity_id"].rsplit(":", 1)[1]) not in entity_ids
                ):
                    emitted_endpoint_missing += 1
                    continue
                compressed.write(canonical_json_bytes(assertion))
                relationship_emitted += 1
                category_emitted[category_name] += 1
                if category_id == 1 and assertion["precision_bucket"] == "undated-only":
                    undated_position_emitted += 1
        finally:
            compressed.close()
            raw.close()

        relationship_accepted = relationship_emitted + relationship_out_of_scope
        relationship_rejected = (
            relationship_invalid + relationship_duplicates + relationship_dangling_rejected
        )
        observed = {
            "entity_observed": entity_observed,
            "entity_accepted": len(entity_ids),
            "entity_invalid": entity_invalid,
            "entity_duplicates": entity_duplicates,
            "relationship_observed": relationship_observed,
            "relationship_accepted": relationship_accepted,
            "relationship_rejected": relationship_rejected,
            "relationship_invalid": relationship_invalid,
            "relationship_duplicates": relationship_duplicates,
            "relationship_dangling_rejected": relationship_dangling_rejected,
            "category_observed": dict(sorted(category_observed.items())),
            "category_accepted": dict(sorted(category_accepted.items())),
            "undated_position_accepted": undated_position_emitted,
        }
        _assert_profile_matches(observed, profile)
        if emitted_endpoint_missing:
            raise SourceNativeCorpusError("emitted LittleSis assertion has a missing endpoint")
        if relationship_observed != relationship_accepted + relationship_rejected:
            raise SourceNativeCorpusError("LittleSis relationship conservation failed at emit time")

        legacy_output = staging / "quorum_legacy_source_native_seed.ndjson"
        shutil.copyfile(legacy_file, legacy_output)
        if sha256_file(legacy_output) != legacy_pin["sha256"]:
            raise SourceNativeCorpusError("legacy seed bytes changed during corpus assembly")

        record_files = [
            {
                "role": "littlesis-entities",
                "source_system_id": "littlesis-bulk",
                "schema_id": ENTITY_SCHEMA_ID,
                "records": entities_emitted,
                "sha256": sha256_file(entities_output),
                "bytes": entities_output.stat().st_size,
                "encoding": "canonical-ndjson-gzip-mtime-0",
                "privacy_class": "restricted-machine-local",
            },
            {
                "role": "littlesis-relationship-assertions",
                "source_system_id": "littlesis-bulk",
                "schema_id": RELATIONSHIP_SCHEMA_ID,
                "records": relationship_emitted,
                "sha256": sha256_file(relationships_output),
                "bytes": relationships_output.stat().st_size,
                "encoding": "canonical-ndjson-gzip-mtime-0",
                "privacy_class": "restricted-machine-local",
            },
            {
                "role": "quorum-legacy-source-native-seed",
                "source_system_id": "quorum-legacy-derived",
                "schema_id": LEGACY_SCHEMA_ID,
                "records": legacy_pin["records"],
                "sha256": sha256_file(legacy_output),
                "bytes": legacy_output.stat().st_size,
                "encoding": "canonical-ndjson",
                "privacy_class": "restricted-local-only",
            },
        ]
        receipt = {
            "schema_id": RECEIPT_SCHEMA_ID,
            "generated_at": generated_at,
            "status": "PASS",
            "source_systems": ["littlesis-bulk", "quorum-legacy-derived"],
            "input_files": [
                _file_descriptor(entities_file, role="entities"),
                _file_descriptor(relationships_file, role="relationships"),
            ],
            "littlesis_profile_receipt_sha256": sha256_file(profile_file),
            "legacy_seed_receipt_sha256": sha256_file(legacy_receipt_file),
            "category_vocabulary_basis": "LittleSis native relationship category_id",
            "category_vocabulary": {
                str(key): value for key, value in sorted(CATEGORY_NAMES.items())
            },
            "entities_emitted": entities_emitted,
            "entity_observed": entity_observed,
            "entity_invalid": entity_invalid,
            "entity_duplicates": entity_duplicates,
            "relationship_observed": relationship_observed,
            "relationship_emitted_in_scope": relationship_emitted,
            "relationship_out_of_scope_category": relationship_out_of_scope,
            "relationship_dangling_rejected": relationship_dangling_rejected,
            "relationship_invalid": relationship_invalid,
            "relationship_duplicates": relationship_duplicates,
            "category_emitted": dict(sorted(category_emitted.items())),
            "category_out_of_scope": dict(sorted(category_out_of_scope.items())),
            "dangling_relationship_ids": sorted(dangling_ids),
            "emitted_endpoint_missing": emitted_endpoint_missing,
            "undated_position_emitted": undated_position_emitted,
            "temporal_weight_nonzero": 0,
            "contact_like_values_redacted": redactions[0],
            "parent_references_redacted": parent_references_redacted,
            "boss_references_redacted": boss_redactions[0],
            "record_files": record_files,
            "staff_net_required_state": "off",
            "source_enabled": False,
            "analysis_enabled": False,
            "canonical_entity_merges": 0,
            "cross_source_identity_assertions": 0,
            "identity_resolution_advanced": False,
            "politico_adjudication_queue_touched": False,
            "lda_gate_status": "CLOSED",
            "politico_gate_status": "CLOSED",
            "loadable_standalone": True,
            "share_alike_publication_review_deferred": True,
            "repository_payload": "aggregate-receipt-only",
            "failures": [],
        }
        assert_public_safe(receipt)
        atomic_write_bytes(
            staging / "source_native_corpus_receipt.json", canonical_json_bytes(receipt)
        )
        os.replace(staging, output)
        return receipt
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_local_corpus_generation(
    generation_root: str | Path,
    *,
    generated_at: str,
    schema_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Stream every local record through its schema and semantic contracts."""

    from .publication import _validate_record_semantics
    from .schema_validation import validate_instance

    root = Path(generation_root).resolve()
    schemas = Path(schema_directory or Path(__file__).with_name("schemas"))
    receipt_path = root / "source_native_corpus_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_schema = json.loads(
        (schemas / "source_native_corpus_receipt.schema.json").read_text(encoding="utf-8")
    )
    validate_instance(receipt, receipt_schema)
    _validate_record_semantics(receipt, receipt["schema_id"])
    descriptors = {row["role"]: row for row in receipt["record_files"]}
    file_specs = (
        (
            "littlesis-entities", "littlesis_entities.ndjson.gz",
            "littlesis_entity_assertion.schema.json",
        ),
        (
            "littlesis-relationship-assertions",
            "littlesis_relationship_assertions.ndjson.gz",
            "littlesis_relationship_assertion.schema.json",
        ),
    )
    verified_files: list[dict[str, Any]] = []
    total_records = 0
    for role, filename, schema_name in file_specs:
        path = root / filename
        descriptor = descriptors[role]
        if path.stat().st_size != descriptor["bytes"] or sha256_file(path) != descriptor["sha256"]:
            raise SourceNativeCorpusError(f"local validation hash mismatch: {role}")
        schema = json.loads((schemas / schema_name).read_text(encoding="utf-8"))
        count = 0
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise SourceNativeCorpusError(
                        f"blank local corpus row: {role}:{line_number}"
                    )
                record = json.loads(line)
                assert_public_safe(record)
                validate_instance(record, schema)
                _validate_record_semantics(record, record["schema_id"])
                count += 1
        if count != descriptor["records"]:
            raise SourceNativeCorpusError(f"local validation count mismatch: {role}")
        total_records += count
        verified_files.append({
            "role": role,
            "records_validated": count,
            "sha256": descriptor["sha256"],
            "bytes": descriptor["bytes"],
            "schema_validation_pass": True,
            "semantic_validation_pass": True,
        })

    legacy_path = root / "quorum_legacy_source_native_seed.ndjson"
    legacy_descriptor = descriptors["quorum-legacy-source-native-seed"]
    if (
        legacy_path.stat().st_size != legacy_descriptor["bytes"]
        or sha256_file(legacy_path) != legacy_descriptor["sha256"]
    ):
        raise SourceNativeCorpusError("local validation hash mismatch: legacy seed")
    _validate_legacy_records(
        legacy_path,
        legacy_descriptor["records"],
        schema_path=schemas / "legacy_source_native_record.schema.json",
    )
    total_records += legacy_descriptor["records"]
    verified_files.append({
        "role": "quorum-legacy-source-native-seed",
        "records_validated": legacy_descriptor["records"],
        "sha256": legacy_descriptor["sha256"],
        "bytes": legacy_descriptor["bytes"],
        "schema_validation_pass": True,
        "semantic_validation_pass": True,
    })
    validation_receipt = {
        "schema_id": "staffer-network-source-native-corpus-validation-receipt-v1",
        "generated_at": generated_at,
        "status": "PASS",
        "corpus_receipt_sha256": sha256_file(receipt_path),
        "files": verified_files,
        "total_records_validated": total_records,
        "all_schema_validation_pass": True,
        "all_semantic_validation_pass": True,
        "failures": [],
    }
    assert_public_safe(validation_receipt)
    return validation_receipt
