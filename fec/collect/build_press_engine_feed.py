#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Promote the newest audited press refresh into stable, typed engine inputs.

Only CU-scored host-article/member rows become tone inputs.  Expanded
``OFFICIAL_MENTION`` relationships are written separately for reference discovery and can
never become stance rows or attributed speech turns.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect.press_receipt import ReceiptTimestampError, parse_receipt_timestamp
from paths import ROOT
from press_stream import canonical_url


TONE_FIELDS = [
    "member", "bioguide", "chamber", "state", "party", "date", "title", "url",
    "categories", "relevance", "score", "score_graded", "score_label", "stance",
    "stance_sign", "p_support", "p_oppose", "p_neutral", "stance_conf", "n_words",
    "lsd_pos", "lsd_neg", "lsd_net", "lsd_net_norm", "lsd_charge", "stance_src",
    "press_value", "recency_weight", "source_run", "source",
]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path):
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def atomic_csv(path, fields, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with io.open(tmp, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    os.replace(tmp, path)


def atomic_json(path, payload):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with io.open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def atomic_jsonl(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with io.open(tmp, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


REQUIRED_RUN_FILES = (
    "press_network_retry_receipt.json", "press_delta_fulltext.csv",
    "press_delta_fulltext.rds", "press_delta_host_articles.csv",
    "press_delta_article_mentions.csv", "press_delta_scored.csv",
)


class ReceiptRejected(Exception):
    """A press run was refused, with a stable machine-readable reason."""

    def __init__(self, reason, detail=""):
        super().__init__("%s: %s" % (reason, detail) if detail else reason)
        self.reason = reason
        self.detail = detail


def validate_body_completion(path, receipt, *, require_complete=False):
    """Validate new coverage proof while retaining historical receipt readability.

    Returns the completion proof this run rests on: empty when the walk proved
    the backlog complete the usual way, and the tombstone counts when it was
    accepted with deferred rows.  Callers that only need accept/refuse can keep
    ignoring the return -- a refusal is still an exception.
    """
    completion = {}
    if "pending_body_urls" not in receipt and "status" not in receipt:
        if require_complete:
            raise ReceiptRejected("MISSING_BODY_COMPLETION_PROOF", "fresh intake requires explicit completed coverage")
        return completion
    if (receipt.get("status") != "PASS" or type(receipt.get("pending_body_urls")) is not int
            or receipt["pending_body_urls"] != 0):
        # Paul 2026-09-19: a body URL the walk lists pending without ever attempting it must
        # stop refusing the whole generation after three consecutive runs.  The walk still
        # lists and retries it every run -- only its veto over intake is withdrawn, and only
        # once the ledger can prove the streak.  Imported here because the tombstone module
        # reaches this one through press_baseline_fold.
        from collect.press_pending_tombstones import pending_is_tombstoned_only
        accepted, detail = pending_is_tombstoned_only(path, receipt)
        if not accepted:
            raise ReceiptRejected("PENDING_BODY_ATTEMPTS", detail)
        completion = dict(detail)
    pending = Path(path) / "press_delta_pending.csv"
    try:
        if receipt.get("pending_body_urls_sha256") != sha256_file(pending):
            raise ValueError("pending artifact hash does not match")
        with pending.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"bioguide", "url", "date", "collection_status"}.issubset(reader.fieldnames or []):
                raise ValueError("pending artifact lacks coverage columns")
            if not completion and any(reader):
                raise ValueError("pending artifact contains uncompleted URLs")
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        raise ReceiptRejected("PENDING_BODY_PROOF_MISMATCH", str(exc)) from exc
    if "cached_date_rejoins" in receipt:
        cached_dates = Path(path) / "press_delta_cached_dates.csv"
        try:
            count = receipt["cached_date_rejoins"]
            if type(count) is not int or count < 0:
                raise ValueError("invalid cached-date evidence count")
            if receipt.get("cached_date_rejoins_sha256") != sha256_file(cached_dates):
                raise ValueError("cached-date evidence hash does not match")
            with cached_dates.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not {"bioguide", "url", "date_detail", "evidence"}.issubset(reader.fieldnames or []):
                    raise ValueError("cached-date evidence lacks required columns")
                if sum(1 for _ in reader) != count:
                    raise ValueError("cached-date evidence count does not match")
        except (OSError, UnicodeError, ValueError, csv.Error) as exc:
            raise ReceiptRejected("CACHED_DATE_PROOF_MISMATCH", str(exc)) from exc
    if "body_attempt_ledger_sha256" in receipt:
        try:
            if receipt["body_attempt_ledger_sha256"] != sha256_file(Path(path) / "press_body_attempt_ledger.rds"):
                raise ValueError("body-attempt ledger hash does not match")
            names = ("revisit_min_days", "revisit_url_cap", "revisit_due_urls", "revisit_selected_urls",
                     "revisit_attempted_urls", "revisit_recovered_urls")
            if any(type(receipt.get(name)) is not int or receipt[name] < 0 for name in names):
                raise ValueError("invalid revisit counts")
            if (receipt["revisit_min_days"] < 7 or receipt["revisit_url_cap"] > 10000 or
                    receipt["revisit_selected_urls"] > min(receipt["revisit_due_urls"], receipt["revisit_url_cap"]) or
                    receipt["revisit_attempted_urls"] > receipt["revisit_selected_urls"] or
                    receipt["revisit_recovered_urls"] > receipt["revisit_attempted_urls"]):
                raise ValueError("inconsistent revisit counts or bounds")
        except (OSError, ValueError) as exc:
            raise ReceiptRejected("BODY_ATTEMPT_LEDGER_MISMATCH", str(exc)) from exc
    if receipt.get("schema_version", 0) >= 2:
        coverage_path = Path(path) / "press_official_coverage.json"
        historical_path = Path(path) / "press_historical_maintenance.csv"
        try:
            if receipt.get("official_coverage_sha256") != sha256_file(coverage_path):
                raise ValueError("official coverage artifact hash does not match")
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            if (coverage.get("schema") != "official-current-inventory-snapshot-v1"
                    or type(coverage.get("member_count")) is not int
                    or type(coverage.get("current_url_count")) is not int
                    or not isinstance(coverage.get("members"), list)
                    or coverage["member_count"] != len(coverage["members"])):
                raise ValueError("invalid official coverage snapshot")
            current_count = 0
            members = set()
            for proof in coverage["members"]:
                if (not isinstance(proof, dict)
                        or proof.get("schema") != "official-current-inventory-proof-v1"
                        or proof.get("coverage_mode") not in {
                            "fully_paginated_after_floor",
                            "sequential_dated_listing_terminal_prefloor",
                            "sequential_dated_listing_terminal_empty",
                        }
                        or not isinstance(proof.get("current_urls"), list)
                        or type(proof.get("current_count")) is not int
                        or proof["current_count"] != len(proof["current_urls"])
                        or len(set(proof["current_urls"])) != len(proof["current_urls"])
                        or not isinstance(proof.get("requests"), list)
                        or not proof["requests"]):
                    raise ValueError("invalid official member coverage proof")
                if proof.get("bioguide") in members:
                    raise ValueError("duplicate official member coverage proof")
                members.add(proof.get("bioguide"))
                if (not isinstance(proof.get("current_url_set_sha256"), str)
                        or len(proof["current_url_set_sha256"]) != 64):
                    raise ValueError("invalid official current URL-set hash")
                complete = proof.get("completeness") or {}
                if not all(complete.get(name) is True for name in (
                        "pagination_complete", "date_order_verified", "identity_complete")):
                    raise ValueError("official coverage completeness flag is false")
                if (proof["coverage_mode"] == "fully_paginated_after_floor"
                        and complete.get("after_floor_query_verified") is not True):
                    raise ValueError("official after-floor proof is incomplete")
                if (proof["coverage_mode"] == "sequential_dated_listing_terminal_prefloor"
                        and complete.get("terminal_page_wholly_prefloor") is not True):
                    raise ValueError("official dated-listing terminal proof is incomplete")
                if proof["coverage_mode"] == "sequential_dated_listing_terminal_empty":
                    boundary = complete.get("boundary_page")
                    inventory = [item for item in proof["requests"]
                                 if isinstance(item, dict) and item.get("role") == "inventory_page"]
                    if (proof.get("source_kind") != "houlahan_official_documentquery"
                            or proof.get("bioguide") != "H001085"
                            or complete.get("terminal_empty_page_verified") is not True
                            or type(boundary) is not int or boundary < 2
                            or len(inventory) != boundary):
                        raise ValueError("official empty terminal proof is incomplete")
                    for number, item in enumerate(inventory, 1):
                        endpoint = ("https://houlahan.house.gov/press/documentquery.aspx"
                                    f"?DocumentTypeID=27&Page={number}")
                        if (item.get("page") != number
                                or item.get("item_count") != (0 if number == boundary else 10)
                                or str(item.get("endpoint")).lower() != endpoint.lower()
                                or str(item.get("final_url")).lower() != endpoint.lower()):
                            raise ValueError("official empty terminal inventory is not contiguous")
                for number, request in enumerate(proof["requests"], 1):
                    if (not isinstance(request, dict)
                            or request.get("request_index") != number
                            or type(request.get("item_count")) is not int
                            or request["item_count"] < 0
                            or not isinstance(request.get("endpoint"), str)
                            or not request["endpoint"].startswith(("http://", "https://"))
                            or not isinstance(request.get("raw_response_sha256"), str)
                            or len(request["raw_response_sha256"]) != 64):
                        raise ValueError("invalid official coverage request evidence")
                current_count += proof["current_count"]
            if (coverage["current_url_count"] != current_count
                    or receipt.get("official_coverage_members") != coverage["member_count"]
                    or receipt.get("official_current_urls") != current_count):
                raise ValueError("official coverage counts do not match")
            if receipt.get("historical_maintenance_sha256") != sha256_file(historical_path):
                raise ValueError("historical maintenance artifact hash does not match")
            with historical_path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                required = {"bioguide", "url", "official_coverage_status", "collection_status"}
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError("historical maintenance artifact lacks required columns")
                historical = list(reader)
            if (type(receipt.get("historical_maintenance_urls")) is not int
                    or receipt["historical_maintenance_urls"] != len(historical)
                    or type(receipt.get("historical_maintenance_selected")) is not int
                    or receipt["historical_maintenance_selected"] < 0
                    or receipt["historical_maintenance_selected"] > len(historical)
                    or receipt["historical_maintenance_selected"] > receipt.get("revisit_selected_urls", -1)):
                raise ValueError("historical maintenance counts are inconsistent")
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, csv.Error) as exc:
            raise ReceiptRejected("OFFICIAL_COVERAGE_PROOF_MISMATCH", str(exc)) from exc
    return completion


def inspect_run(path):
    """Return ``(completed_at, receipt)`` or raise a named rejection."""
    path = Path(path)
    if not path.is_dir():
        raise ReceiptRejected("MISSING_RUN_FILES", "not a directory: %s" % path)
    missing = [name for name in REQUIRED_RUN_FILES if not (path / name).is_file()]
    if missing:
        raise ReceiptRejected("MISSING_RUN_FILES", "absent: %s" % ", ".join(missing))
    receipt_path = path / REQUIRED_RUN_FILES[0]
    try:
        raw = receipt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReceiptRejected("RECEIPT_UNREADABLE", "%s (%s)" % (receipt_path, exc)) from exc
    try:
        receipt = json.loads(raw)
    except ValueError as exc:
        raise ReceiptRejected("RECEIPT_NOT_JSON", "%s (%s)" % (receipt_path, exc)) from exc
    if not isinstance(receipt, dict):
        raise ReceiptRejected("RECEIPT_NOT_JSON", "receipt is not an object")
    try:
        completed = parse_receipt_timestamp(receipt.get("completed_at"))
    except ReceiptTimestampError as exc:
        reason = ("COMPLETED_AT_MISSING" if exc.reason == "TIMESTAMP_MISSING"
                  else "COMPLETED_AT_UNPARSABLE")
        raise ReceiptRejected(reason, str(exc)) from exc
    if receipt.get("mode") != "live_network_retry":
        raise ReceiptRejected("MODE_NOT_LIVE_NETWORK_RETRY", "mode=%r" % receipt.get("mode"))
    validate_body_completion(path, receipt)
    for key, name, reason in (
        ("fulltext_csv_sha256", "press_delta_fulltext.csv", "FULLTEXT_CSV_SHA_MISMATCH"),
        ("fulltext_rds_sha256", "press_delta_fulltext.rds", "FULLTEXT_RDS_SHA_MISMATCH"),
    ):
        try:
            actual = sha256_file(path / name)
        except OSError as exc:
            raise ReceiptRejected("SOURCE_UNREADABLE", "%s (%s)" % (name, exc)) from exc
        if receipt.get(key) != actual:
            raise ReceiptRejected(reason, "receipt=%r actual=%r" % (receipt.get(key), actual))
    return completed, receipt


def valid_run(path, on_reject=None):
    """Compatibility wrapper retaining the historical ``None`` refusal result."""
    try:
        return inspect_run(path)
    except ReceiptRejected as exc:
        if on_reject is not None:
            on_reject(exc)
        elif exc.reason != "MISSING_RUN_FILES":
            sys.stderr.write("press receipt refused [%s] %s: %s\n" % (
                exc.reason, Path(path).name, exc.detail))
        return None


def discover_run(outputs_root):
    candidates = []
    refusals = []
    for path in sorted(Path(outputs_root).glob("press_refresh_*")):
        try:
            completed, receipt = inspect_run(path)
        except ReceiptRejected as exc:
            if exc.reason != "MISSING_RUN_FILES":
                refusals.append("%s [%s] %s" % (path.name, exc.reason, exc.detail))
            continue
        candidates.append((completed, path, receipt))
    if not candidates:
        raise RuntimeError(
            "no audited press_refresh_* run with a valid live-network receipt"
            + ("; refused: " + " | ".join(refusals) if refusals else ""))
    candidates.sort(key=lambda item: (item[0], str(item[1])))
    return candidates[-1][1], candidates[-1][2]


def turn_key(row):
    return (canonical_url(row.get("url"), keep_fragment=False),
            (row.get("bioguide") or "").strip().upper())


def stance_for(row):
    try:
        polarity = float(row.get("polarity") or 0)
    except ValueError:
        polarity = 0.0
    if polarity > 0:
        return "support", "1"
    if polarity < 0:
        return "oppose", "-1"
    return "neutral", "0"


def tone_row(row, run_name):
    stance, sign = stance_for(row)
    try:
        intensity = abs(float(row.get("score_graded") or 0.0))
    except ValueError:
        intensity = 0.0
    return {
        "member": row.get("member") or "", "bioguide": row.get("bioguide") or "",
        "chamber": row.get("chamber") or "", "state": row.get("state") or "",
        "party": row.get("party") or "", "date": row.get("date") or "",
        "title": row.get("title") or "", "url": row.get("url") or "",
        "categories": row.get("categories") or "", "relevance": row.get("relevance") or "",
        "score": row.get("score") or "", "score_graded": row.get("score_graded") or "",
        "score_label": row.get("score_label") or "", "stance": stance,
        "stance_sign": sign, "p_support": "", "p_oppose": "", "p_neutral": "",
        "stance_conf": "", "n_words": row.get("word_count") or "",
        "lsd_pos": "", "lsd_neg": "", "lsd_net": "", "lsd_net_norm": "",
        "lsd_charge": "", "stance_src": "press_refresh_rules_v1",
        "press_value": ("%.12g" % intensity),
        "recency_weight": row.get("recency_weight") or "", "source_run": run_name,
        "source": "PRESS_REFRESH_HOST_ARTICLE",
    }


def build(source_root, tone_output, fulltext_output, mentions_output, receipt_output,
          intake_receipt_path):
    root = Path(source_root)
    intake_receipt_path = Path(intake_receipt_path)
    if not intake_receipt_path.is_file():
        raise RuntimeError("collection intake receipt is required before press feed publication")
    intake = json.loads(intake_receipt_path.read_text(encoding="utf-8"))
    if (intake.get("schema") != "collection-intake-receipt-v1" or
            intake.get("status") != "ACCEPTED"):
        raise RuntimeError("collection intake receipt is not an accepted gate receipt")
    intake_inputs = intake.get("inputs") or {}
    retry_path = root / "press_network_retry_receipt.json"
    retry_sha256 = sha256_file(retry_path)
    try:
        audited_root = Path(intake_inputs["press_delta_root"]).resolve()
    except (KeyError, TypeError):
        raise RuntimeError("collection intake receipt lacks the audited press delta root")
    if audited_root != root.resolve():
        raise RuntimeError("press feed source does not match the collection intake audit")
    if intake_inputs.get("press_retry_receipt_sha256") != retry_sha256:
        raise RuntimeError("press retry receipt is not hash-bound to the collection intake audit")
    if (intake.get("press_delta") or {}).get("live_network_retry_receipt_valid") is not True:
        raise RuntimeError("collection intake did not accept the press retry receipt")
    source_names = (
        "press_delta_scored.csv", "press_delta_host_articles.csv",
        "press_delta_article_mentions.csv",
    )
    source_sha256 = {name: sha256_file(root / name) for name in source_names}
    if intake_inputs.get("press_source_sha256") != source_sha256:
        raise RuntimeError("press feed sources are not hash-bound to the collection intake audit")
    retry = json.loads(retry_path.read_text(encoding="utf-8"))
    hosts = read_csv(root / "press_delta_host_articles.csv")
    mentions = read_csv(root / "press_delta_article_mentions.csv")
    scored = read_csv(root / "press_delta_scored.csv")
    if any((row.get("source") or "").upper() == "OFFICIAL_MENTION" for row in scored):
        raise RuntimeError("OFFICIAL_MENTION relationship leaked into press_delta_scored.csv")

    host_by_turn = {turn_key(row): row for row in hosts}
    hosts_by_url = {}
    for row in hosts:
        hosts_by_url.setdefault(canonical_url(row.get("url"), keep_fragment=False), []).append(row)

    tone = [tone_row(row, root.name) for row in scored]
    fulltext = []
    missing_text = []
    for row in scored:
        host = host_by_turn.get(turn_key(row))
        if host is None:
            choices = hosts_by_url.get(canonical_url(row.get("url"), keep_fragment=False), [])
            host = choices[0] if len(choices) == 1 else None
        body = (host or {}).get("body") or ""
        if len(body.split()) < 20:
            missing_text.append((row.get("url") or "")[:160])
            continue
        fulltext.append({
            "url": row.get("url") or "", "title": row.get("title") or "", "text": body,
            "member": row.get("member") or "", "bioguide": row.get("bioguide") or "",
            "source": "PRESS_REFRESH_HOST_ARTICLE", "source_run": root.name,
            "body_sha256": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
        })
    if missing_text:
        raise RuntimeError("%d scored press rows lack a >=20-word host body" % len(missing_text))

    typed_mentions = []
    host_resolved = 0
    for row in mentions:
        base = canonical_url(row.get("url"), keep_fragment=False)
        choices = hosts_by_url.get(base, [])
        host_members = sorted({r.get("member") for r in choices if r.get("member")})
        host_member = host_members[0] if len(host_members) == 1 else None
        if host_member:
            host_resolved += 1
        body = row.get("body") or ""
        typed_mentions.append({
            "schema": "official-press-mention-v1", "source_run": root.name,
            "article_url": base, "relationship_url": row.get("url") or "",
            "host_member": host_member, "mentioned_member": row.get("member") or "",
            "mentioned_bioguide": row.get("bioguide") or "", "date": row.get("date_final") or "",
            "title": row.get("title") or "", "evidence": " ".join(body.split())[:1200],
            "body_sha256": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
            "disposition": "REFERENCE_DISCOVERY_ONLY_NOT_STANCE_OR_SPEECH",
        })

    if {name: sha256_file(root / name) for name in source_names} != source_sha256:
        raise RuntimeError("press feed sources changed while the feed was being built")

    atomic_csv(tone_output, TONE_FIELDS, tone)
    atomic_jsonl(fulltext_output, fulltext)
    atomic_jsonl(mentions_output, typed_mentions)
    receipt = {
        "schema": "press-refresh-engine-feed-v1", "status": "ACTIVE_TYPED_SUPPLEMENT",
        "generated_at": datetime.now(timezone.utc).isoformat(), "source_run": str(root.resolve()),
        "source_retry_completed_at": retry.get("completed_at"),
        "collection_intake": {
            "path": str(intake_receipt_path.resolve()),
            "sha256": sha256_file(intake_receipt_path),
            "generated_at": intake.get("generated_at"),
        },
        "contract": {
            "scored_host_articles_enter_press_stance": True,
            "official_mentions_enter_press_stance": False,
            "official_mentions_enter_speech_turns": False,
            "official_mentions_are_reference_discovery_only": True,
            "historical_corpus_precedence_on_exact_collision": True,
        },
        "counts": {"tone_rows": len(tone), "fulltext_rows": len(fulltext),
                   "official_mentions": len(typed_mentions),
                   "official_mentions_with_unique_host_member": host_resolved},
        "source_sha256": {
            **source_sha256,
            "press_network_retry_receipt.json": retry_sha256,
        },
        "outputs_sha256": {
            os.path.relpath(tone_output, ROOT).replace("\\", "/"): sha256_file(tone_output),
            os.path.relpath(fulltext_output, ROOT).replace("\\", "/"): sha256_file(fulltext_output),
            os.path.relpath(mentions_output, ROOT).replace("\\", "/"): sha256_file(mentions_output),
        },
    }
    atomic_json(receipt_output, receipt)
    return receipt


def _accepted_intake_receipts(current_receipt, receipt_dir):
    """Load the immutable accepted-run ledger, including the current gate receipt."""
    paths = []
    receipt_dir = Path(receipt_dir)
    if receipt_dir.is_dir():
        paths.extend(sorted(receipt_dir.glob("press_refresh_*.json")))
    current_receipt = Path(current_receipt)
    if current_receipt.is_file():
        paths.append(current_receipt)
    accepted = {}
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("accepted press receipt is unreadable: %s" % path) from exc
        if (document.get("schema") != "collection-intake-receipt-v1"
                or document.get("status") != "ACCEPTED"):
            if path == current_receipt:
                raise RuntimeError("collection intake receipt is not an accepted gate receipt")
            raise RuntimeError("archived press receipt is not accepted: %s" % path)
        try:
            root = Path(document["inputs"]["press_delta_root"]).resolve()
        except (KeyError, TypeError) as exc:
            raise RuntimeError("accepted press receipt lacks a source root: %s" % path) from exc
        prior = accepted.get(root)
        if prior:
            prior_inputs = prior[1].get("inputs") or {}
            current_inputs = document.get("inputs") or {}
            binding_names = ("press_delta_root", "press_retry_receipt_sha256", "press_source_sha256")
            if any(prior_inputs.get(name) != current_inputs.get(name) for name in binding_names):
                raise RuntimeError("conflicting accepted receipts for %s" % root)
        if prior:
            continue  # prefer the immutable archived path over the rolling current receipt
        accepted[root] = (path, document)
    if not accepted:
        raise RuntimeError("no accepted press collection receipts are available")

    ordered = []
    for root, (path, document) in accepted.items():
        retry_path = root / "press_network_retry_receipt.json"
        try:
            retry = json.loads(retry_path.read_text(encoding="utf-8"))
            completed = parse_receipt_timestamp(retry.get("completed_at"))
        except (OSError, ValueError, ReceiptTimestampError) as exc:
            raise RuntimeError("accepted press run has an unreadable completion receipt: %s" % root) from exc
        ordered.append((completed, str(root), root, path, document))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [(root, path, document) for _completed, _text, root, path, document in ordered]


def _read_jsonl(path):
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise RuntimeError("non-object JSONL row %d in %s" % (number, path))
            rows.append(row)
    return rows


def build_cumulative(tone_output, fulltext_output, mentions_output, receipt_output,
                     current_intake_receipt, accepted_receipts_dir):
    """Rebuild the active supplement from every accepted run, oldest evidence first.

    The baseline fold may make future collection incremental, so a one-run overwrite
    is not a valid promotion model.  This function reconstructs the complete accepted
    supplement and records every intake/source hash in the output receipt.
    """
    accepted = _accepted_intake_receipts(current_intake_receipt, accepted_receipts_dir)
    tone_by_key = {}
    fulltext_by_key = {}
    mentions_by_key = {}
    ledger = []

    with tempfile.TemporaryDirectory(prefix="press-feed-stage-") as temporary:
        stage = Path(temporary)
        for ordinal, (root, intake_path, _intake) in enumerate(accepted):
            run_stage = stage / ("run-%03d" % ordinal)
            run_stage.mkdir()
            tone_path = run_stage / "tone.csv"
            fulltext_path = run_stage / "fulltext.jsonl"
            mentions_path = run_stage / "mentions.jsonl"
            receipt_path = run_stage / "receipt.json"
            run_receipt = build(
                root, tone_path, fulltext_path, mentions_path, receipt_path, intake_path)

            tone_rows = read_csv(tone_path)
            fulltext_rows = _read_jsonl(fulltext_path)
            mention_rows = _read_jsonl(mentions_path)
            for row in tone_rows:
                tone_by_key.setdefault(turn_key(row), row)
            for row in fulltext_rows:
                key = (canonical_url(row.get("url"), keep_fragment=False),
                       (row.get("bioguide") or "").strip().upper())
                fulltext_by_key.setdefault(key, row)
            for row in mention_rows:
                key = (canonical_url(row.get("article_url"), keep_fragment=False),
                       (row.get("mentioned_bioguide") or "").strip().upper())
                mentions_by_key.setdefault(key, row)
            ledger.append({
                "source_run": str(root.resolve()),
                "source_retry_completed_at": run_receipt["source_retry_completed_at"],
                "collection_intake": run_receipt["collection_intake"],
                "source_sha256": run_receipt["source_sha256"],
                "counts_before_deduplication": run_receipt["counts"],
            })

    tone = list(tone_by_key.values())
    fulltext = list(fulltext_by_key.values())
    mentions = list(mentions_by_key.values())
    atomic_csv(tone_output, TONE_FIELDS, tone)
    atomic_jsonl(fulltext_output, fulltext)
    atomic_jsonl(mentions_output, mentions)
    receipt = {
        "schema": "press-refresh-engine-feed-v1",
        "status": "ACTIVE_TYPED_SUPPLEMENT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "promotion_mode": "CUMULATIVE_ACCEPTED_RUN_LEDGER",
        "accepted_runs": ledger,
        "contract": {
            "scored_host_articles_enter_press_stance": True,
            "official_mentions_enter_press_stance": False,
            "official_mentions_enter_speech_turns": False,
            "official_mentions_are_reference_discovery_only": True,
            "historical_corpus_precedence_on_exact_collision": True,
            "accepted_runs_are_cumulative": True,
            "earliest_accepted_run_wins_exact_refresh_collision": True,
        },
        "counts": {
            "accepted_runs": len(ledger),
            "tone_rows": len(tone),
            "fulltext_rows": len(fulltext),
            "official_mentions": len(mentions),
        },
        "outputs": {
            "tone": {"path": str(Path(tone_output).resolve()),
                     "sha256": sha256_file(tone_output)},
            "fulltext": {"path": str(Path(fulltext_output).resolve()),
                         "sha256": sha256_file(fulltext_output)},
            "mentions": {"path": str(Path(mentions_output).resolve()),
                         "sha256": sha256_file(mentions_output)},
        },
        "outputs_sha256": {
            os.path.relpath(tone_output, ROOT).replace("\\", "/"): sha256_file(tone_output),
            os.path.relpath(fulltext_output, ROOT).replace("\\", "/"): sha256_file(fulltext_output),
            os.path.relpath(mentions_output, ROOT).replace("\\", "/"): sha256_file(mentions_output),
        },
    }
    atomic_json(receipt_output, receipt)
    return receipt


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--outputs-root", type=Path, default=Path(ROOT) / "outputs")
    parser.add_argument("--tone-output", type=Path, default=Path(ROOT) / "data/stance/press_refresh_tone.csv")
    parser.add_argument("--fulltext-output", type=Path, default=Path(ROOT) / "data/stance/press_refresh_fulltext.jsonl")
    parser.add_argument("--mentions-output", type=Path, default=Path(ROOT) / "data/press/official_mentions.jsonl")
    parser.add_argument("--receipt-output", type=Path, default=Path(ROOT) / "data/stance/press_refresh_feed_receipt.json")
    parser.add_argument("--intake-receipt", type=Path,
                        default=Path(ROOT) / "data/engine/collection_intake_receipt.json")
    parser.add_argument("--accepted-receipts-dir", type=Path,
                        default=Path(ROOT) / "data/engine/press_collection_receipts")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.source_root is not None:
        source_root = args.source_root
        if valid_run(source_root) is None:
            raise RuntimeError("specified source root lacks a valid live-network retry receipt")
        receipt = build(source_root, args.tone_output, args.fulltext_output,
                        args.mentions_output, args.receipt_output, args.intake_receipt)
        source_label = Path(receipt["source_run"]).name
    else:
        receipt = build_cumulative(
            args.tone_output, args.fulltext_output, args.mentions_output,
            args.receipt_output, args.intake_receipt, args.accepted_receipts_dir)
        source_label = "%d accepted runs" % receipt["counts"]["accepted_runs"]
    print("press refresh feed: %d stance rows + %d typed official mentions from %s" % (
        receipt["counts"]["tone_rows"], receipt["counts"]["official_mentions"], source_label))
    return 0


if __name__ == "__main__":
    sys.exit(main())
