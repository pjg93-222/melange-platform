#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Produce the hearing display feed the desk and dashboard contract requires -- or refuse, by name.

WHY THIS EXISTS. ``members/hearing_display_contract.load_optional`` is fail-closed: with no
hash-bound artifact and receipt on disk the hearing surface is simply OMITTED. Nothing wrote them,
so the surface has been omitted since the contract landed. This is the missing producer.

WHAT IT REFUSES TO DO. It will not launder the pre-fix corpus. The shipped sequence database was
collected on 2026-07-11, before the chamber-scope fix (2026-08-18), and 9.7% of its attributed
turns sit in the wrong chamber -- Jerry Moran holding 4,571 turns while Nathaniel Moran holds 0.
A receipt that named the corrected attribution contract over those rows would be a false
certificate, so the producer will not write one. It consumes only a corpus whose own receipt
(``<seq-db>.receipt.json``, written by collect/fetch_hearings_seq.py) claims the corrected contract
and still hashes to what that receipt recorded, whose window is completely accounted for, and whose
antagonism artifact is bound to that same corpus by sha256. Every other case exits 2 with a named
reason and writes nothing.

The production feed therefore cannot be built today: it needs a rebuilt corpus, which is a
multi-hour govinfo collection and Paul's call. What is built and tested here is the producer and
the provenance chain that would let it declare the contract honestly.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from members.hearing_display_contract import (
    ARTIFACT_SCHEMA, ATTRIBUTION_CONTRACT, DEFAULT_ARTIFACT, DEFAULT_RECEIPT,
    RECEIPT_SCHEMA, sha256_file,
)
from paths import ROOT

DEFAULT_SEQ_DB = Path(ROOT) / "hearings_seq.db"            # collect/fetch_hearings_seq.py SEQDB
DEFAULT_META_DB = Path(ROOT) / "social_media.db"           # ...maindb(), hearing_meta lives here
DEFAULT_ANTAGONISM = Path(ROOT) / "data" / "stance" / "hearing_antagonism.json"
DEFAULT_TTL_DAYS = 10
COVERAGE_STATUS = "COMPLETE_TO_DECLARED_CEILING"
NON_MEMBER_KINDS = ("W", "C", "O")   # parse_seq kinds that are not a named member


class HearingFeedRejected(Exception):
    """The feed was refused, with a stable machine-readable reason."""

    def __init__(self, reason, detail=""):
        super().__init__("%s: %s" % (reason, detail) if detail else reason)
        self.reason = reason
        self.detail = detail


def _load_json_object(path, missing_reason, unreadable_reason):
    path = Path(path)
    if not path.is_file():
        raise HearingFeedRejected(missing_reason, "absent: %s" % path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HearingFeedRejected(unreadable_reason, "%s (%s)" % (path, exc)) from exc
    if not isinstance(document, dict):
        raise HearingFeedRejected(unreadable_reason, "%s is not a JSON object" % path)
    return document


def inspect_corpus(seq_db, seq_receipt_path):
    """Return the sequence receipt, or raise a named rejection.

    This is the whole certification: the corpus must SAY it was collected under the corrected
    attribution contract, and must still BE the file that said so."""
    receipt = _load_json_object(seq_receipt_path, "SEQ_RECEIPT_MISSING", "SEQ_RECEIPT_UNREADABLE")
    declared = receipt.get("attribution_contract")
    if declared != ATTRIBUTION_CONTRACT:
        raise HearingFeedRejected(
            "ATTRIBUTION_CONTRACT_NOT_V2",
            "corpus receipt declares %r, not %r; the display surface may not show hearing conduct "
            "attributed under the pre-fix chamber rule" % (declared, ATTRIBUTION_CONTRACT))
    try:
        actual = sha256_file(seq_db)
    except OSError as exc:
        raise HearingFeedRejected("SEQ_DB_UNREADABLE", "%s (%s)" % (seq_db, exc)) from exc
    if actual != receipt.get("db_sha256"):
        raise HearingFeedRejected(
            "SEQ_DB_HASH_MISMATCH",
            "receipt=%r actual=%r; the database changed after it was certified"
            % (receipt.get("db_sha256"), actual))
    packages = receipt.get("packages")
    failed = packages.get("failed") if isinstance(packages, dict) else None
    if not isinstance(failed, int) or isinstance(failed, bool):
        raise HearingFeedRejected(
            "COVERAGE_INCOMPLETE", "corpus receipt has no integer packages.failed: %r" % packages)
    if failed > 0:
        raise HearingFeedRejected(
            "COVERAGE_INCOMPLETE",
            "%d of %r listed packages are unaccounted for; coverage cannot be declared complete "
            "to the ceiling" % (failed, packages.get("listed")))
    return receipt, actual


def inspect_antagonism(antagonism_path, seq_db_sha256):
    """Return the antagonism document, or raise a named rejection.

    Co-location on disk is not provenance. The artifact must name the corpus hash it was derived
    from, and that hash must be the certified one."""
    document = _load_json_object(
        antagonism_path, "ANTAGONISM_MISSING", "ANTAGONISM_UNREADABLE")
    members = document.get("members")
    if not isinstance(members, dict):
        raise HearingFeedRejected(
            "ANTAGONISM_UNREADABLE", "%s has no members object" % Path(antagonism_path))
    bound = document.get("seq_db_sha256")
    if bound != seq_db_sha256:
        raise HearingFeedRejected(
            "ANTAGONISM_UNBOUND",
            "artifact seq_db_sha256=%r, certified corpus=%r; these member scores are not provably "
            "derived from the certified corpus" % (bound, seq_db_sha256))
    return document


def _titles(meta_db):
    """package -> hearing title. A missing database or table yields no titles, never a new file."""
    meta_db = Path(meta_db)
    if not meta_db.is_file():
        return {}
    connection = sqlite3.connect(str(meta_db), timeout=120)
    try:
        connection.execute("PRAGMA query_only=1")
        return {package: (title or "") for package, title in connection.execute(
            "SELECT package, COALESCE(title,'') FROM hearing_meta")}
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


def appearances(seq_db, meta_db):
    """One row per (package, member): who spoke in which hearing, and how many turns.

    parse_seq stores a member turn as ``[member, party, dash, text, 'M'|'C']`` and a non-member turn
    as ``['W'|'C'|'O', ...]``, so a turn is a member's exactly when its kind is not one of those
    three codes -- including a member presiding as chair, who is stored under their own name."""
    titles = _titles(meta_db)
    connection = sqlite3.connect(str(seq_db), timeout=120)
    rows = []
    try:
        connection.execute("PRAGMA query_only=1")
        for package, date, payload in connection.execute(
                "SELECT package, date, seq FROM hearing_seq"):
            try:
                sequence = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if not isinstance(sequence, list):
                continue
            turns = {}
            for turn in sequence:
                if not isinstance(turn, list) or not turn:
                    continue
                kind = turn[0]
                if kind in NON_MEMBER_KINDS or not isinstance(kind, str):
                    continue
                turns[kind] = turns.get(kind, 0) + 1
            for member in sorted(turns):
                rows.append({"member": member, "date": date, "package": package,
                             "title": titles.get(package, ""), "turns": turns[member]})
    finally:
        connection.close()
    rows.sort(key=lambda row: (str(row["package"]), str(row["member"])))
    return rows


def serialize(document):
    """The exact bytes written for an artifact or receipt (LF, sorted keys).

    The receipt's artifact_sha256 is taken over THESE bytes, and main writes exactly these bytes,
    so the hash in the receipt is the hash of the file the contract will read back."""
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def build_feed(seq_db=DEFAULT_SEQ_DB, meta_db=DEFAULT_META_DB, seq_receipt_path=None,
               antagonism_path=DEFAULT_ANTAGONISM, artifact_path=DEFAULT_ARTIFACT,
               now=None, ttl_days=DEFAULT_TTL_DAYS):
    """Return ``(artifact, receipt)``, or raise ``HearingFeedRejected``. Writes nothing."""
    seq_db = Path(seq_db)
    seq_receipt_path = Path(seq_receipt_path) if seq_receipt_path is not None \
        else Path(str(seq_db) + ".receipt.json")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    corpus, seq_db_sha256 = inspect_corpus(seq_db, seq_receipt_path)
    antagonism = inspect_antagonism(antagonism_path, seq_db_sha256)

    artifact = {
        "schema": ARTIFACT_SCHEMA,
        "members": antagonism["members"],
        "appearances": appearances(seq_db, meta_db),
    }
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "ACCEPTED",
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=ttl_days)).isoformat(),
        "attribution_contract": corpus["attribution_contract"],
        "coverage": {"status": COVERAGE_STATUS,
                     "ceiling": corpus.get("until"),
                     "max_date": corpus.get("max_date")},
        "artifact_path": str(Path(artifact_path).resolve()),
        "artifact_sha256": _sha256_bytes(serialize(artifact)),
        "seq_receipt_sha256": sha256_file(seq_receipt_path),
        "seq_db_sha256": seq_db_sha256,
        "antagonism_sha256": sha256_file(antagonism_path),
        "counts": {"members": len(artifact["members"]),
                   "appearances": len(artifact["appearances"])},
    }
    return artifact, receipt


def write_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return path


def parse_now(text):
    if not text:
        return datetime.now(timezone.utc)
    moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seq-db", default=str(DEFAULT_SEQ_DB))
    parser.add_argument("--meta-db", default=str(DEFAULT_META_DB))
    parser.add_argument("--seq-receipt", default=None,
                        help="default: <seq-db>.receipt.json")
    parser.add_argument("--antagonism", default=str(DEFAULT_ANTAGONISM))
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT))
    parser.add_argument("--receipt", default=str(DEFAULT_RECEIPT))
    parser.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS)
    parser.add_argument("--now", default=None, help="ISO timestamp (test hook)")
    args = parser.parse_args(argv)
    try:
        artifact, receipt = build_feed(
            seq_db=args.seq_db, meta_db=args.meta_db, seq_receipt_path=args.seq_receipt,
            antagonism_path=args.antagonism, artifact_path=args.artifact,
            now=parse_now(args.now), ttl_days=args.ttl_days)
    except HearingFeedRejected as rejected:
        print("HEARING DISPLAY FEED REFUSED -- %s: %s" % (rejected.reason, rejected.detail),
              file=sys.stderr)
        return 2
    write_atomic(args.artifact, serialize(artifact))
    write_atomic(args.receipt, serialize(receipt))
    print("hearing_display_feed: %d members | %d appearances | %s | ceiling %s | expires %s"
          % (receipt["counts"]["members"], receipt["counts"]["appearances"],
             receipt["attribution_contract"], receipt["coverage"]["ceiling"],
             receipt["expires_at"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
