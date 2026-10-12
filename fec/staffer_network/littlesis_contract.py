# -*- coding: utf-8 -*-
"""Streaming census and endpoint-integrity contract for LittleSis bulk data."""

from __future__ import annotations

from collections import Counter
import gzip
import json
from pathlib import Path
from typing import Any, Iterator, Mapping

from .source_manifest import sha256_file
from .validate import assert_public_safe


RECEIPT_SCHEMA_ID = "staffer-network-littlesis-profile-receipt-v1"
CATEGORY_NAMES = {
    1: "Position",
    2: "Education",
    3: "Membership",
    4: "Family",
    5: "Donation",
    6: "Transaction",
    7: "Lobbying",
    8: "Social",
    9: "Professional",
    10: "Ownership",
    11: "Hierarchy",
    12: "Generic",
}


class LittleSisContractError(RuntimeError):
    pass


def stream_json_array(path: str | Path, *, chunk_size: int = 1024 * 1024) -> Iterator[Any]:
    """Yield a top-level JSON array without retaining the full dump in memory."""

    decoder = json.JSONDecoder()
    with gzip.open(Path(path), "rt", encoding="utf-8") as stream:
        buffer = ""
        position = 0
        eof = False
        started = False
        finished = False
        while not finished:
            if position >= len(buffer) and not eof:
                buffer = stream.read(chunk_size)
                position = 0
                eof = not buffer
            while True:
                while position < len(buffer) and buffer[position].isspace():
                    position += 1
                if position < len(buffer):
                    break
                if eof:
                    break
                buffer = stream.read(chunk_size)
                position = 0
                eof = not buffer
            if not started:
                if position >= len(buffer) or buffer[position] != "[":
                    raise LittleSisContractError("bulk file root is not a JSON array")
                position += 1
                started = True
                continue
            while True:
                while position < len(buffer) and (
                    buffer[position].isspace() or buffer[position] == ","
                ):
                    position += 1
                if position < len(buffer):
                    break
                if eof:
                    raise LittleSisContractError("bulk JSON array is truncated")
                chunk = stream.read(chunk_size)
                buffer = buffer[position:] + chunk
                position = 0
                eof = not chunk
            if buffer[position] == "]":
                position += 1
                finished = True
                continue
            while True:
                try:
                    value, end = decoder.raw_decode(buffer, position)
                    position = end
                    yield value
                    if position > chunk_size:
                        buffer = buffer[position:]
                        position = 0
                    break
                except json.JSONDecodeError as exc:
                    if eof:
                        raise LittleSisContractError(
                            "bulk JSON item is malformed or truncated"
                        ) from exc
                    chunk = stream.read(chunk_size)
                    buffer = buffer[position:] + chunk
                    position = 0
                    if len(buffer) > chunk_size * 16:
                        raise LittleSisContractError(
                            "bulk JSON item exceeds the bounded parser window"
                        ) from exc
                    eof = not chunk
        trailing = buffer[position:] + stream.read()
        if trailing.strip():
            raise LittleSisContractError("bulk JSON has trailing content")


def _record_id(record: Any) -> int | None:
    if not isinstance(record, Mapping):
        return None
    attributes = record.get("attributes")
    if not isinstance(attributes, Mapping):
        return None
    outer = record.get("id")
    inner = attributes.get("id")
    if isinstance(outer, bool) or isinstance(inner, bool):
        return None
    try:
        outer_id = int(outer)
        inner_id = int(inner)
    except (TypeError, ValueError):
        return None
    return outer_id if outer_id > 0 and outer_id == inner_id else None


def profile_littlesis_pair(
    entities_path: str | Path,
    relationships_path: str | Path,
    *,
    generated_at: str,
    collected_at: str,
    relationship_prior_sha256: str,
) -> dict[str, Any]:
    """Profile a paired download and reject invalid/dangling relationships."""

    entities_file = Path(entities_path)
    relationships_file = Path(relationships_path)
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

    relationship_ids: set[int] = set()
    relationship_observed = 0
    relationship_invalid = 0
    relationship_duplicates = 0
    relationship_dangling = 0
    category_observed: Counter[int] = Counter()
    category_accepted: Counter[int] = Counter()
    undated_position_accepted = 0
    accepted = 0
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
        category_observed[category_id] += 1
        if relationship_id in relationship_ids:
            relationship_duplicates += 1
            continue
        relationship_ids.add(relationship_id)
        if endpoint_1 not in entity_ids or endpoint_2 not in entity_ids:
            relationship_dangling += 1
            continue
        accepted += 1
        category_accepted[category_id] += 1
        if (
            category_id == 1
            and not attributes.get("start_date")
            and not attributes.get("end_date")
        ):
            undated_position_accepted += 1

    entity_accepted = len(entity_ids)
    relationship_rejected = (
        relationship_invalid + relationship_duplicates + relationship_dangling
    )
    if entity_observed != entity_accepted + entity_invalid + entity_duplicates:
        raise LittleSisContractError("entity conservation failed")
    if relationship_observed != accepted + relationship_rejected:
        raise LittleSisContractError("relationship conservation failed")

    named_observed = {
        CATEGORY_NAMES.get(key, f"category-{key}"): category_observed[key]
        for key in sorted(category_observed)
    }
    named_accepted = {
        CATEGORY_NAMES.get(key, f"category-{key}"): category_accepted[key]
        for key in sorted(category_accepted)
    }
    entity_sha = sha256_file(entities_file)
    relationship_sha = sha256_file(relationships_file)
    failures = []
    if entity_invalid or entity_duplicates or relationship_invalid or relationship_duplicates:
        failures.append("INVALID_OR_DUPLICATE_SOURCE_RECORDS")
    receipt = {
        "schema_id": RECEIPT_SCHEMA_ID,
        "generated_at": generated_at,
        "status": "PASS" if not failures else "QUARANTINED",
        "source_system_id": "littlesis-bulk",
        "license": "Creative Commons Attribution-ShareAlike",
        "terms_uri": "https://littlesis.org/database/bulk_data",
        "terms_checked_at": collected_at,
        "collected_at": collected_at,
        "pairing_basis": (
            "Both official bulk URLs retrieved in one acquisition session; the "
            "relationship bytes equal the independently audited prior dump. This "
            "is observed pairing evidence, not a publisher-declared release vintage."
        ),
        "files": [
            {
                "role": "entities",
                "sha256": entity_sha,
                "bytes": entities_file.stat().st_size,
            },
            {
                "role": "relationships",
                "sha256": relationship_sha,
                "bytes": relationships_file.stat().st_size,
            },
        ],
        "relationship_prior_sha256": relationship_prior_sha256,
        "relationship_matches_prior_dump": relationship_sha == relationship_prior_sha256,
        "entity_observed": entity_observed,
        "entity_accepted": entity_accepted,
        "entity_invalid": entity_invalid,
        "entity_duplicates": entity_duplicates,
        "relationship_observed": relationship_observed,
        "relationship_accepted": accepted,
        "relationship_rejected": relationship_rejected,
        "relationship_invalid": relationship_invalid,
        "relationship_duplicates": relationship_duplicates,
        "relationship_dangling_rejected": relationship_dangling,
        "accepted_dangling_relationships": 0,
        "category_observed": named_observed,
        "category_accepted": named_accepted,
        "undated_position_accepted": undated_position_accepted,
        "raw_storage": "restricted-machine-local",
        "source_native_ready": not failures,
        "source_enabled": False,
        "canonical_entity_merges": 0,
        "share_alike_publication_review_deferred": True,
        "failures": failures,
    }
    assert_public_safe(receipt)
    return receipt
