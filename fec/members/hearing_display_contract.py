#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed contract for hearing conduct shown on dashboard surfaces.

Legacy ``hearing_antag`` rows were stale and the old sequence corpus contains
known pre-fix chamber attribution.  Surfaces may now read hearing conduct only
from a hash-bound artifact whose receipt names the corrected attribution
contract and has not expired.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from paths import ROOT


ARTIFACT_SCHEMA = "hearing-display-feed-v1"
RECEIPT_SCHEMA = "hearing-display-receipt-v1"
ATTRIBUTION_CONTRACT = "chamber-scoped-speaker-attribution-v2"
DEFAULT_ARTIFACT = Path(ROOT) / "data/stance/hearing_display_feed.json"
DEFAULT_RECEIPT = Path(ROOT) / "data/stance/hearing_display_receipt.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_optional(artifact_path=DEFAULT_ARTIFACT, receipt_path=DEFAULT_RECEIPT, now=None):
    artifact_path = Path(artifact_path); receipt_path = Path(receipt_path)
    if not artifact_path.is_file() or not receipt_path.is_file():
        return None
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(str(receipt["expires_at"]).replace("Z", "+00:00"))
        artifact_sha = sha256_file(artifact_path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (not isinstance(artifact, dict) or not isinstance(receipt, dict)
            or not isinstance(receipt.get("coverage"), dict)):
        return None
    now = now or datetime.now(timezone.utc)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if (artifact.get("schema") != ARTIFACT_SCHEMA
            or receipt.get("schema") != RECEIPT_SCHEMA
            or receipt.get("status") != "ACCEPTED"
            or receipt.get("attribution_contract") != ATTRIBUTION_CONTRACT
            or receipt["coverage"].get("status") != "COMPLETE_TO_DECLARED_CEILING"
            or artifact_sha != receipt.get("artifact_sha256")
            or expires.astimezone(timezone.utc) <= now.astimezone(timezone.utc)):
        return None
    if not isinstance(artifact.get("members"), dict) or not isinstance(artifact.get("appearances"), list):
        return None
    return artifact


def write_fixture_feed(artifact_path, receipt_path, members, appearances,
                       ceiling, expires_at, generated_at=None):
    """Write a small accepted feed; used by neutral fixtures and controlled builders."""
    artifact_path = Path(artifact_path); receipt_path = Path(receipt_path)
    artifact = {
        "schema": ARTIFACT_SCHEMA,
        "members": members,
        "appearances": appearances,
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact_path.with_name(artifact_path.name + ".tmp")
    temporary.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(artifact_path)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "ACCEPTED",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "attribution_contract": ATTRIBUTION_CONTRACT,
        "coverage": {"status": "COMPLETE_TO_DECLARED_CEILING", "ceiling": ceiling},
        "artifact_path": str(artifact_path.resolve()),
        "artifact_sha256": sha256_file(artifact_path),
        "counts": {"members": len(members), "appearances": len(appearances)},
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt_path.with_name(receipt_path.name + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(receipt_path)
    return artifact, receipt
