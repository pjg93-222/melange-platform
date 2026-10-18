"""Read-only, receipt-gated live staffer dashboard. Never runs or modifies a crawler."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


try:
    from .staffer_assertions import load_coworker_assertions, record_coworker_decision, local_date
    from .melange_dashboard import resolve as dashboard_request, prepared as prepared_dashboard
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_assertions import load_coworker_assertions, record_coworker_decision, local_date
    from fec.surfaces.melange_dashboard import resolve as dashboard_request, prepared as prepared_dashboard

DEFAULT_ROOT = Path(r"C:\Users\pgoff\OneDrive\Desktop\Melange Systems")
ASSETS = Path(__file__).parent / "staffer_live"
POLL_SECONDS = 30
STALE_AFTER_SECONDS = 180

# Large generated workspace assets (the frame documents are 1-19 MB) are gzip-compressed once per file version,
# not on every request; entries are keyed by (path, mtime_ns, size), so a rebuild is picked up at once.
APPROVED_CACHE_BYTES = 64 * 1024 * 1024
_approved_cache = OrderedDict()
_approved_cache_lock = threading.Lock()

# The desktop persistence prefix of mlk-boot.js ships with the application (runtime-manifest sourceFiles).
# A selected workspace generated before it existed still gets it: prepended at serve time, never written back.
BOOT_PREFIX_SOURCE = Path(__file__).parent / 'approved' / 'clean' / 'mlk-boot.js'
BOOT_PREFIX_MARKER = b'__melangeDesktopStorage'


def boot_prefix():
    """The app-owned prefix (bytes before '/* mlk-boot.js:'), or b'' when the app copy is absent or has none."""
    try:
        head, separator, _ = BOOT_PREFIX_SOURCE.read_bytes().partition(b'/* mlk-boot.js:')
    except OSError:
        return b''
    return head if separator and BOOT_PREFIX_MARKER in head else b''


def approved_payload(path, prefix=b''):
    """(gzip body, ETag, raw size) of one approved asset. The ETag is the content hash it always was.
    A non-empty prefix is prepended only when the file does not already carry the desktop prefix."""
    import gzip
    before = path.stat()
    key = (str(path), before.st_mtime_ns, before.st_size, hashlib.sha256(prefix).hexdigest() if prefix else '')
    with _approved_cache_lock:
        entry = _approved_cache.get(key)
        if entry is not None:
            _approved_cache.move_to_end(key)
            return entry
    original = path.read_bytes()
    raw = prefix + original if prefix and BOOT_PREFIX_MARKER not in original else original
    entry = (gzip.compress(raw, compresslevel=3), '"clean-' + hashlib.sha256(raw).hexdigest()[:24] + '"', len(raw))
    after = path.stat()
    if (after.st_mtime_ns, after.st_size) == key[1:3] and len(original) == before.st_size:  # not rewritten while read
        with _approved_cache_lock:
            _approved_cache[key] = entry
            total = sum(len(value[0]) for value in _approved_cache.values())
            while total > APPROVED_CACHE_BYTES and len(_approved_cache) > 1:
                total -= len(_approved_cache.popitem(last=False)[1][0])
    return entry


class WorkspaceServer(ThreadingHTTPServer):
    """The live server's listener, sized for a browser's burst of parallel requests.

    socketserver listens with a backlog of 5. On Windows a connection that arrives while that queue is full is
    reset, which the browser reports as net::ERR_CONNECTION_REFUSED; a workspace frame requests about ten overlay
    files at once while the staffer service holds the interpreter lock, so scripts were refused and the legacy
    layout stayed on screen (audit r4 mel-a F1: 28 of 80 connections refused in two 40-connection bursts). A deep
    queue holds such connections until they are accepted; the operating system caps what it grants.
    """
    request_queue_size = 1024
    daemon_threads = True


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def age(timestamp):
    try:
        return max(0, time.time() - datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError, TypeError):
        return None


def freshness_now(freshness):
    """Separate data/verification age from inactivity in a completed collection."""
    result = dict(freshness or {})
    if 'verified_at' in result:
        verified_age = age(result['verified_at'])
        source_age = age(result.get('source_updated_at'))
        completed = result.get('collection_status') in {'COMPLETE', 'COMPLETE_WITH_EXCEPTIONS'}
        result.update(age_seconds=verified_age, verification_age_seconds=verified_age,
                      source_age_seconds=source_age,
                      stale=False if completed else verified_age is None or verified_age > STALE_AFTER_SECONDS,
                      collection_inactive=False if completed else source_age is None or source_age > STALE_AFTER_SECONDS)
    return result


class IncrementalProfiles:
    """Read complete lines once; accept only a stable, successful receipt's prefix.

    v2 receipts attest counts, not bytes. We make that limitation explicit and bind
    each accepted prefix to our own digest. Atomic replacement/truncation invalidates
    the candidate generation and cannot silently reuse the old receipt.
    """

    def __init__(self, raw_dir):
        self.raw_dir = Path(raw_dir)
        self.path = self.raw_dir / "profiles.ndjson"
        self.rows = []
        self.offset = 0
        self.identity = None
        self.accepted = []
        self.accepted_receipt = None
        self.accepted_receipt_digest = None
        self.generation = 0
        self.generation_needs_receipt = False
        self.generation_receipt_digest = None
        self.line_end_offsets = []
        self.row_digests = []
        self.verified_byte_binding = None
        self.state = {}
        self.receipt = {}
        self.errors = []
        self.partial_line = False
        self.raw_updated_at = None
        self.prefix_hasher = hashlib.sha256()
        self.accepted_prefix_sha256 = None
        self.exceptions = 0
        self.profile_digests = {}
        self.eligible_profile_digests = {}
        self.profile_contracts = {}
        self.profile_revision = digest({})
        self.profile_changes = {"added": [], "changed": [], "removed": [], "unchanged_count": 0}
        self._derived_key = None  # names the generation, receipt and prefix the derived digests were computed for

    @staticmethod
    def content_digest(row):
        """Stable parsed source content; observation timing has separate provenance."""
        return digest({k: v for k, v in row.items() if k not in {"observed_at", "fetched_at", "retrieved_at"}})

    @staticmethod
    def eligible(row):
        # Collection integrity is distinct from identity/content reconciliation.
        name = str(row.get("verified_name") or "").strip()
        identity = row.get("identity_status", "")
        return bool(name and name.lower() not in {"my notes", "notes"}
                    and identity not in {"PROFILE_UNAVAILABLE_404", "LIVE_NAME_CHANGED_SAME_UUID"}
                    and "EXCEPTION" not in row.get("review_status", "")
                    and (row.get("current_header_positions") or row.get("employment_history")))

    def refresh(self):
        self.errors = []
        try:
            receipt_path = self.raw_dir / "crawl_verification.json"
            before = receipt_path.read_bytes()
            receipt_digest = hashlib.sha256(before).hexdigest()
            receipt = json.loads(before)
            state = read_json(self.raw_dir / "state.json")
            stat = self.path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if self.identity is not None and (identity != self.identity or stat.st_size < self.offset):
                self.rows, self.offset = [], 0
                self.line_end_offsets = []
                self.row_digests = []
                self.verified_byte_binding = None
                self.prefix_hasher = hashlib.sha256()
                self.generation += 1
                self.generation_needs_receipt = True
                self.generation_receipt_digest = receipt_digest
            self.identity = identity
            self.raw_updated_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            # Bound work to the size seen before opening; a writer may append meanwhile.
            with self.path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != identity:
                    raise ValueError("Profile generation changed while opening; preserving last good snapshot")
                handle.seek(self.offset)
                remaining = stat.st_size - self.offset
                while remaining > 0:
                    line = handle.readline(remaining)
                    if not line:
                        break
                    remaining -= len(line)
                    if not line.endswith(b"\n"):
                        self.partial_line = True
                        break
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("Profile row is not an object")
                    self.rows.append(row)
                    self.row_digests.append({"profile_content_sha256": self.content_digest(row),
                        "profile_row_sha256": digest(row), "profile_raw_line_sha256": hashlib.sha256(line).hexdigest()})
                    self.offset += len(line)
                    self.line_end_offsets.append(self.offset)
                    self.prefix_hasher.update(line)
                    self.partial_line = False
            after_stat = self.path.stat()
            if (after_stat.st_dev, after_stat.st_ino) != identity or after_stat.st_size < self.offset:
                raise ValueError("Profile generation changed during read; preserving last good snapshot")
            self.state, self.receipt = state, receipt
            if receipt_path.read_bytes() != before:
                raise ValueError("Verification receipt changed during read; awaiting a consistent checkpoint")
            if receipt.get("integrity_status", receipt.get("status")) != "PASS":
                raise ValueError("Latest collection verification has not passed; preserving last good snapshot")
            if (receipt.get("verification_failure_count", 0) or receipt.get("failure_count", 0)
                    or any(receipt.get("verification_failures", {}).values())):
                raise ValueError("Latest verification reports failures; preserving last good snapshot")
            count = receipt.get("counts", {}).get("profile_rows")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("Verification receipt has no valid profile prefix count")
            if receipt.get("counts", {}).get("unique_profiles_collected") != count:
                raise ValueError("Receipt profile/unique counts disagree")
            if receipt.get("crawl_state", {}).get("total_succeeded") != count:
                raise ValueError("Receipt and its embedded checkpoint disagree")
            if len(self.rows) < count or state.get("total_succeeded", -1) < count:
                raise ValueError("Receipt is ahead of complete rows/checkpoint; preserving last good snapshot")
            source = receipt.get("inputs", {}).get("profiles")
            if isinstance(source, dict):
                source = source.get("path")
            if source and Path(source).resolve() != self.path.resolve():
                raise ValueError("Receipt identifies a different profile source")
            prefix = receipt.get("profile_prefix") or {}
            prefix_bytes = receipt.get("profile_prefix_bytes", prefix.get("bytes"))
            prefix_sha256 = receipt.get("profile_prefix_sha256", prefix.get("sha256"))
            hash_bound = prefix_bytes is not None or prefix_sha256 is not None
            byte_binding = (identity, receipt_digest, count, prefix_bytes, prefix_sha256)
            if hash_bound and byte_binding != self.verified_byte_binding:
                expected_bytes = self.line_end_offsets[count - 1] if count else 0
                if (not isinstance(prefix_bytes, int) or isinstance(prefix_bytes, bool)
                        or prefix_bytes != expected_bytes or not isinstance(prefix_sha256, str)):
                    raise ValueError("Receipt byte prefix does not match its complete profile count")
                hasher = hashlib.sha256()
                with self.path.open("rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev, opened.st_ino) != identity:
                        raise ValueError("Profile generation changed before hash verification")
                    remaining = prefix_bytes
                    while remaining:
                        chunk = handle.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Profile byte prefix truncated during hash verification")
                        hasher.update(chunk)
                        remaining -= len(chunk)
                if hasher.hexdigest() != prefix_sha256.lower():
                    raise ValueError("Receipt profile prefix SHA-256 mismatch; preserving last good snapshot")
                final_stat = self.path.stat()
                if ((final_stat.st_dev, final_stat.st_ino) != identity
                        or final_stat.st_size < self.offset or receipt_path.read_bytes() != before):
                    raise ValueError("Source or receipt changed during hash verification")
            if (self.generation_needs_receipt and not hash_bound
                    and receipt_digest == self.generation_receipt_digest):
                raise ValueError("Raw profiles replaced; awaiting verification for the replacement generation")
            if not self.generation_needs_receipt and count < len(self.accepted):
                raise ValueError("Verification prefix regressed without a generation change")
            candidate = self.rows[:count]
            ids = [row.get("profile_uuid") for row in candidate]
            if any(not value for value in ids) or len(set(ids)) != count:
                raise ValueError("Verified prefix contains duplicate or missing profile UUIDs")
            for row in candidate:
                if not str(row.get("source_url", "")).rstrip("/").endswith("/" + row["profile_uuid"]):
                    raise ValueError("Profile UUID and provenance URL disagree")
            # The digests, contracts and change list below depend only on the generation, the receipt and the
            # verified prefix. When they are those of the cycle that derived them, the derived values still hold and
            # are kept (the rows and receipt were still read and checked above, on this cycle): nothing was added,
            # changed or removed, and the 0.3 s digest of every row is not computed again.
            derived_key = (self.generation, receipt_digest, count, prefix_bytes, prefix_sha256, hash_bound)
            if derived_key == self._derived_key:
                self.accepted = candidate
                self.accepted_receipt = receipt
                self.generation_needs_receipt = False
                self.verified_byte_binding = byte_binding if hash_bound else None
                self.profile_changes = {"added": [], "changed": [], "removed": [], "unchanged_count": len(self.profile_digests)}
            else:
                next_digests = {row["profile_uuid"]: self.row_digests[index]["profile_content_sha256"]
                                for index, row in enumerate(candidate)}
                changes = {"added": sorted(set(next_digests) - set(self.profile_digests)),
                    "changed": sorted(i for i in next_digests if i in self.profile_digests and next_digests[i] != self.profile_digests[i]),
                    "removed": sorted(set(self.profile_digests) - set(next_digests)),
                    "unchanged_count": sum(self.profile_digests.get(i) == value for i, value in next_digests.items())}
                contracts = {}
                for index, row in enumerate(candidate):
                    eligible = self.eligible(row)
                    contracts[row["profile_uuid"]] = {**self.row_digests[index],
                        "schema_id": "staffer-live-profile-content-v1", "source_profile_uuid": row["profile_uuid"],
                        "display_name": (str(row.get("verified_name", "")).strip() if eligible else row.get("expected_name")),
                        "expected_name": row.get("expected_name"), "verified_name": row.get("verified_name"),
                        "eligible": eligible, "identity_status": row.get("identity_status"),
                        "name_verification": "SOURCE_PROFILE_NAME_COLLECTION_VERIFIED_REVIEW_ONLY" if eligible else "IDENTITY_OR_CONTENT_REVIEW_REQUIRED",
                        "canonical_identity_promoted": False, "source_url": row.get("source_url"),
                        "source_path": str(self.path), "observed_at": row.get("observed_at"),
                        "path": str(self.path), "sha256": self.row_digests[index]["profile_content_sha256"],
                        "sha256_scope": "CANONICAL_PROFILE_CONTENT",
                        "raw_line_sha256": self.row_digests[index]["profile_raw_line_sha256"],
                        "byte_start": self.line_end_offsets[index - 1] if index else 0,
                        "byte_end": self.line_end_offsets[index], "receipt_sha256": receipt_digest,
                        "verification_receipt": str(receipt_path), "verified_at": receipt.get("generated_at"),
                        "prefix_binding": "SHA256_BYTE_PREFIX" if hash_bound else "COUNT_ONLY_PREFIX",
                        "verified_prefix_bytes": prefix_bytes if hash_bound else None,
                        "verified_prefix_sha256": prefix_sha256 if hash_bound else None,
                        "digest_basis": "Content: sorted-key compact JSON excluding observed_at/fetched_at/retrieved_at; row: all parsed fields; raw line: exact bytes including newline"}
                self.accepted = candidate
                self.accepted_receipt = receipt
                self.accepted_receipt_digest = receipt_digest
                self.accepted_prefix_sha256 = digest(candidate)
                self.generation_needs_receipt = False
                self.verified_byte_binding = byte_binding if hash_bound else None
                self.exceptions = sum(not self.eligible(row) for row in candidate)
                self.profile_digests, self.profile_contracts = next_digests, contracts
                self.eligible_profile_digests = {i: value for i, value in next_digests.items() if contracts[i]["eligible"]}
                self.profile_revision = digest(self.eligible_profile_digests)
                self.profile_changes = changes
                self._derived_key = derived_key
        except (OSError, ValueError, TypeError) as exc:
            self.errors.append(str(exc))
        return self.summary()

    def summary(self):
        expected = self.state.get("queue_total") or self.receipt.get("counts", {}).get("sealed_directory_unique_profiles") or 18618
        verified = len(self.accepted)
        collected = len(self.rows)
        verified_at = (self.accepted_receipt or {}).get("generated_at")
        return {
            "profile_ingestion": {"revision": self.profile_revision, **self.profile_changes,
                "contract_schema": "staffer-live-profile-content-v1", "accepted_profiles": len(self.profile_digests)},
            "coverage": {"collected": collected, "verified": verified,
                         "pending": max(0, collected - verified), "expected": expected,
                         "unresolved": max(0, expected - verified), "exceptions": self.exceptions,
                         "graph_eligible_profiles": verified - self.exceptions,
                         "content_bearing_profiles": (self.accepted_receipt or {}).get('counts', {}).get('content_bearing_profiles'),
                         "source_incomplete_profiles": (self.accepted_receipt or {}).get('counts', {}).get('source_incomplete_profiles', 0),
                         "unavailable_profiles": (self.accepted_receipt or {}).get('counts', {}).get('allowed_profile_unavailable_404', 0),
                         "acquisition_status": (self.accepted_receipt or {}).get('acquisition_status', 'IN_PROGRESS'),
                         'accepted_source_exceptions': (self.accepted_receipt or {}).get('counts', {}).get('accepted_source_exceptions', 0),
                         'completion_status': (self.accepted_receipt or {}).get('completion_status', 'INCOMPLETE'),
                         "failed": self.state.get("total_failed", 0)},
            "freshness": freshness_now({"source_updated_at": self.state.get("last_updated", self.raw_updated_at),
                                        "verified_at": verified_at, "poll_seconds": POLL_SECONDS,
                                        "collection_status": (self.accepted_receipt or {}).get("completion_status", "INCOMPLETE")}),
            "errors": list(self.errors),
            "crawl": {"halted": self.state.get("halted", False), "partial_final_line": self.partial_line,
                      "generation": self.generation, "last_good_preserved": bool(self.errors and self.accepted),
                      "completion_status": self.receipt.get("completion_status", "INCOMPLETE"),
                      "verification_basis": ("SHA-256 and complete-line byte prefix bound to successful receipt"
                                             if self.verified_byte_binding else
                                             "Stable receipt count-covered prefix; receipt does not provide a content hash"),
                      "accepted_prefix_sha256": self.accepted_prefix_sha256,
                      "receipt_sha256": self.accepted_receipt_digest},
        }

    def graph(self, query="", limit=220):
        query = query.casefold().strip()
        selected = [r for r in self.accepted if self.eligible(r)
                    and (not query or query in json.dumps(r, ensure_ascii=False).casefold())]
        # New additions are visible in the default live view. Search covers the whole prefix.
        rows = selected[-limit:]
        nodes, edges = {}, []
        for row in rows:
            pid = "politico:" + row["profile_uuid"]
            contract = self.profile_contracts.get(row["profile_uuid"], {})
            provenance = {"source_url": row["source_url"], "observed_at": row.get("observed_at"),
                          "verification_receipt": str(self.raw_dir / "crawl_verification.json"),
                          "profile_uuid": row["profile_uuid"], "review_status": "REVIEW_REQUIRED",
                          "identity_merge": "NONE; source profile remains a separate identity", **contract}
            node = {"id": pid, "label": str(row.get("verified_name") or row.get("expected_name") or "").strip(), "kind": "person",
                    "name_status": "SOURCE_PROFILE_NAME_VERIFIED", "source_profile_uuid": row["profile_uuid"],
                    "aliases": [row["expected_name"]] if row.get("expected_name") and row.get("expected_name") != row.get("verified_name") else [],
                    "profile_content_sha256": contract.get("profile_content_sha256"),
                    "profile_row_sha256": contract.get("profile_row_sha256"),
                    "expected_name": row.get("expected_name"), "verified_name": row.get("verified_name"),
                    "source": "Politico profile", "verification": "COLLECTION_VERIFIED_REVIEW_ONLY",
                    "member_ids": [], "committee_ids": [], "policy_ids": [], "provenance": provenance,
                    "current_positions": row.get("current_header_positions", []),
                    "employment_history": row.get("employment_history", []),
                    "education": row.get("education", []), "canonical_eligible": False}
            nodes[pid] = node
            affiliations = []
            for position in row.get("current_header_positions", []):
                if " for " in position:
                    affiliations.append((position.rsplit(" for ", 1)[-1], "politico_current_position", position))
            for stint in row.get("employment_history", []):
                affiliations.append((stint.get("employer", ""), "politico_employment_history", stint))
            for policy in row.get("profile_issue_areas", []):
                if isinstance(policy, str):
                    affiliations.append((policy, "politico_policy_area", policy))
            for employer, layer, evidence in affiliations:
                if not employer:
                    continue
                kind = "policy_area" if layer == "politico_policy_area" else ("container" if "committee" in employer.casefold() else "organization")
                target = "politico-affiliation:" + digest([kind, employer.casefold()])[:24]
                nodes[target] = {"id": target, "label": employer, "kind": kind, "provenance": provenance,
                                 "verification": "SOURCE_AFFILIATION_REVIEW_ONLY"}
                key = "policy_ids" if kind == "policy_area" else "committee_ids" if kind == "container" else "member_ids"
                # Employer-text clusters are source affiliations, never resolved Congress identities.
                node[key].append(target)
                edges.append({"id": "politico-evidence:" + digest([pid, target, layer, evidence]),
                              "source": pid, "target": target, "layer": layer, "weight": 0,
                              "operator_weight": 0, "operator_included": False, "duration_weight": 0,
                              "w_tiered_lower": 0, "canonical_eligible": False,
                              "canonical_employment_promoted": False, "primary": False,
                              "provenance": {**provenance, "assertion": evidence}, "review_status": "REVIEW_REQUIRED"})
        return {"nodes": list(nodes.values()), "edges": edges,
                "matching_profiles": len(selected), "displayed_profiles": len(rows)}


LIVE_LAYERS = [
    {"id": "politico_current_position", "label": "Politico current affiliation", "kind": "evidence", "default_enabled": True},
    {"id": "politico_employment_history", "label": "Politico employment history", "kind": "evidence", "default_enabled": False},
    {"id": "politico_policy_area", "label": "Politico policy evidence", "kind": "evidence", "default_enabled": True},
]


class LiveDashboard:
    def __init__(self, source_root, static_graph=None):
        self.source_root = Path(source_root)
        self.profiles = IncrementalProfiles(self.source_root / "audits/staffer_network/politico_profile_crawl_20260903_raw")
        self.static = static_graph
        self.lock = threading.RLock()
        self.last_refresh = 0

    def refresh(self):
        with self.lock:
            if time.monotonic() - self.last_refresh >= POLL_SECONDS:
                self.profiles.refresh()
                self.last_refresh = time.monotonic()

    def snapshot(self, query="", limit=700):
        self.refresh()
        with self.lock:
            base = self.static.view(query=query, limit=limit) if self.static else {"nodes": [], "edges": [], "layers": [], "sources": [], "errors": ["Verified model loading"]}
            live = self.profiles.graph(query, min(220, limit))
            summary = self.profiles.summary()
            result = {**base, **summary, "schema": "melange-staffer-live-v1", "status": "IN_PROGRESS",
                      "terminal_verified": False, "generated_at": utcnow(),
                      "nodes": base.get("nodes", []) + live["nodes"], "edges": base.get("edges", []) + live["edges"],
                      "layers": base.get("layers", []) + LIVE_LAYERS,
                      "errors": base.get("errors", []) + summary["errors"],
                      "sources": base.get("sources", []) + [{"label": "Politico crawl verification", "path": str(self.profiles.raw_dir / "crawl_verification.json"), "sha256": self.profiles.accepted_receipt_digest}],
                      "live_bounds": {"matching_profiles": live["matching_profiles"], "displayed_profiles": live["displayed_profiles"]},
                      "notice": "IN PROGRESS. Collection verification is not identity adjudication or final reconciliation. Politico evidence is review-only and carries zero model weight."}
            result["revision"] = digest({"nodes": result["nodes"], "edges": result["edges"], "coverage": result["coverage"], "errors": result["errors"]})[:20]
            return result


class Handler(BaseHTTPRequestHandler):
    def valid_host(self):
        """DNS-rebinding guard: a rebound hostname still arrives with its own name in Host."""
        port = self.server.server_port
        return (self.headers.get('Host') or '').lower() in {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}

    def discard_body(self):
        # Consume a small, declared request body before closing. Otherwise
        # Windows may reset the socket with unread bytes and hide the response.
        previous_timeout = self.connection.gettimeout()
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if 0 < length <= 16384:
                self.connection.settimeout(1.0)
                self.rfile.read(length)
        except (ValueError, OSError):
            pass
        finally:
            self.connection.settimeout(previous_timeout)

    def do_POST(self):
        """Identity decisions, relationship statements and route search cancellation.

        All three change server state, so they share one local-origin guard.
        /api/staffer-route-cancel takes {"id": <search_id>, "holder": <page token>}: that page lets go of the search,
        which stops when no page still holds it. The holder is optional; a cancel without one lets go of the shared
        anonymous holder that every request without a token uses.
        """
        if not self.valid_host():
            self.discard_body()
            self.send_bytes(b'{"error":"Local access only."}', 'application/json', 403)
            return
        endpoint = urlsplit(self.path).path
        if endpoint not in {'/api/staffer-identity', '/api/staffer-assertions', '/api/staffer-route-cancel'}:
            self.discard_body()
            self.send_error(404)
            return
        cancel = endpoint == '/api/staffer-route-cancel'
        subject, noun, kind = (('Route search changes', 'route search change', 'route search') if cancel else
                               ('Identity decisions', 'identity decision', 'identity'))
        try:
            origin = self.headers.get('Origin')
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.port != self.server.server_address[1]:
                    raise ValueError(subject + ' must come from this local viewer')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise ValueError('Cross-site ' + kind + ' changes are not accepted')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError(subject + ' require JSON')
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= 16384:
                raise ValueError('Invalid ' + noun + ' size')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Invalid ' + noun)
            if cancel:
                search_id = payload.get('id')
                if not isinstance(search_id, str) or not 1 <= len(search_id) <= 64:
                    raise ValueError('Choose a search to cancel')
                holder = payload.get('holder')
                if holder is not None and (not isinstance(holder, str) or not 1 <= len(holder) <= 64):
                    raise ValueError('Invalid search holder')
                service = self.server.dashboard
                if not hasattr(service, 'cancel_route_search'):
                    self.send_bytes(b'{"error":"Route cancellation unavailable"}', 'application/json', 503)
                    return
                answer = service.cancel_route_search(search_id, **({'holder': holder} if holder else {}))
                self.send_bytes(json.dumps(answer).encode(), 'application/json')
                return
            result = (self.server.dashboard.assertion_decision(payload) if endpoint == '/api/staffer-assertions' else
                      self.server.dashboard.identity_decision(payload.get('primary_id'), payload.get('alias_ids'), payload.get('decision')))
        except (ValueError, TypeError) as exc:
            self.send_bytes(json.dumps({'error': str(exc)}).encode(), 'application/json', 400)
            return
        except OSError as exc:
            self.send_bytes(json.dumps({'error': 'Could not save identity decision: ' + str(exc)}).encode(), 'application/json', 503)
            return
        # Persistence has completed. A lost response cannot undo or retry it.
        self.send_bytes(json.dumps(result).encode(), 'application/json', 202)

    def routing_options(self, query):
        options = {}
        if 'objective' in query:
            options['objective'] = query['objective'][0]
        if 'route_count' in query:
            options['route_count'] = query['route_count'][0]
        for key in ('alternative_strategy', 'avoid_people', 'work_sources'):
            if key in query:
                options[key] = query[key][0]
        for key in ('include_review', 'include_policy', 'include_weak', 'include_historical', 'include_unresolved', 'include_user_asserted', 'prefer_current'):
            if key in query:
                options[key] = query[key][0].lower() in {'1', 'true', 'yes'}
        for key in ('current_cost', 'historical_cost', 'policy_cost', 'filing_cost', 'reported_activity_cost', 'timeout_ms', 'limit', 'coworker_offset', 'collaborator_offset', 'uncertainty_weight'):
            if key in query:
                options[key] = float(query[key][0])
                if not math.isfinite(options[key]):
                    raise ValueError(key + ' must be finite')
        return options

    def do_GET(self):
        if not self.valid_host():
            self.send_bytes(b'{"error":"Local access only."}', 'application/json', 403)
            return
        parts = urlsplit(self.path)
        if parts.path == '/api/staffer-person-activities':
            query = parse_qs(parts.query)
            service = self.server.dashboard
            provider = getattr(service, 'activity_provider', None)
            try:
                payload = provider.records(service._resolve_id(query.get('id', [''])[0]), query.get('offset', [0])[0]) if provider else {'records': [], 'status':'activity_index_unavailable'}
            except (ValueError, TypeError):
                payload = {'records': [], 'status':'invalid_pagination'}
            self.send_bytes(json.dumps(payload).encode(), 'application/json')
        elif parts.path == '/api/staffer-source-record':
            query = parse_qs(parts.query)
            service = getattr(self.server, 'dashboard', None)
            catalog = getattr(service, 'source_catalog', None)
            try:
                if hasattr(service, 'source_record'):
                    payload = service.source_record(query.get('id', [''])[0], query.get('offset', [0])[0])
                else:
                    payload = catalog.record(query.get('id', [''])[0], query.get('offset', [0])[0]) if catalog else {'status':'catalog_unavailable', 'record':None}
                node = getattr(getattr(service, 'static', None), 'nodes', {}).get(query.get('id', [''])[0])
                if not payload.get('record') and node and node.get('kind') == 'payroll_record':
                    payload = {'status':'unresolved_source_record','node':node,'record':node,'relationships':[],
                               'total':0,'offset':0,'limit':50,'has_more':False,'provenance':node.get('provenance'),
                               'note':'Unresolved payroll observation. This source row does not assert a distinct staffer identity.'}
            except (ValueError, TypeError):
                payload = {'status':'invalid_pagination', 'record':None}
            self.send_bytes(json.dumps(payload).encode(), 'application/json')
        elif parts.path == '/api/staffer-person-link':
            query = parse_qs(parts.query)
            service = self.server.dashboard
            source_id = query.get('id', [''])[0]
            if not hasattr(service, 'staffer_person_link'):
                self.send_bytes(b'{"error":"Staffer identity link unavailable"}',
                                'application/json', 503)
            else:
                payload = service.staffer_person_link(source_id)
                self.send_bytes(json.dumps(payload, ensure_ascii=False).encode(),
                                'application/json; charset=utf-8')
        elif parts.path == '/api/staffer-assertions':
            self.send_bytes(json.dumps({'assertions': load_coworker_assertions(), 'local_date': local_date(), 'timezone': 'America/New_York'}).encode(), 'application/json')
        elif parts.path == '/api/staffer-network-index':
            if getattr(self.server.dashboard, 'startup_error', None) and not getattr(self.server.dashboard, 'ready', False):
                self.send_bytes(b'{"error":"The verified people model could not be loaded. Check the selected workspace and restart the app.","code":"model_unavailable"}', 'application/json', 503)
                return
            compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
            scope = parse_qs(parts.query).get('scope', ['sources'])[0]
            if scope not in ('network', 'sources'):
                self.send_bytes(b'{"error":"Unknown network scope"}', 'application/json', 400)
                return
            body, etag = self.server.dashboard.network_index(compressed, scope=scope)
            if etag and self.headers.get('If-None-Match') == etag:
                self.send_response(304)
                self.send_header('ETag', etag)
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Vary', 'Accept-Encoding')
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                self.send_bytes(body, 'application/json; charset=utf-8', etag=etag, compressed=compressed)
        elif parts.path == '/api/staffer-groups-index':
            if getattr(self.server.dashboard, 'startup_error', None) and not getattr(self.server.dashboard, 'ready', False):
                self.send_bytes(b'{"error":"The verified people model could not be loaded. Check the selected workspace and restart the app.","code":"model_unavailable"}', 'application/json', 503)
                return
            compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
            service = self.server.dashboard
            if not hasattr(service, 'groups_index'):
                self.send_bytes(b'{"error":"Groups index unavailable"}', 'application/json', 503)
                return
            body, etag = service.groups_index(compressed)
            if etag and self.headers.get('If-None-Match') == etag:
                self.send_response(304)
                self.send_header('ETag', etag)
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Vary', 'Accept-Encoding')
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                self.send_bytes(body, 'application/json; charset=utf-8', etag=etag, compressed=compressed)
        elif parts.path == '/api/staffer-overview':
            payload = self.server.dashboard.overview_status()
            self.send_bytes(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode(),
                            'application/json; charset=utf-8')
        elif parts.path == "/api/staffer-network-full":
            try:
                payload = self.server.dashboard.full_network()
                if parse_qs(parts.query).get('summary', ['0'])[0] == '1':
                    payload = {key: payload.get(key) for key in
                               ('schema', 'ready', 'stage', 'status', 'revision', 'full_network', 'bounds', 'coverage')}
                raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
                compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
                if compressed:
                    import gzip
                    raw = gzip.compress(raw, compresslevel=3)
                self.send_bytes(raw, 'application/json; charset=utf-8', compressed=compressed)
            except Exception as exc:
                self.send_bytes(json.dumps({'error': str(exc), 'status': 'IN_PROGRESS'}).encode(), 'application/json', 503)
        elif parts.path == "/api/staffer-live":
            query = parse_qs(parts.query)
            try:
                limit = max(50, min(1800, int(query.get("limit", [700])[0])))
            except (ValueError, TypeError):
                self.send_bytes(json.dumps({"error": "limit must be a whole number"}).encode(), "application/json", 400)
                return
            try:
                if hasattr(self.server.dashboard, 'encoded_snapshot'):
                    compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
                    body, etag = self.server.dashboard.encoded_snapshot(query.get('q', [''])[0][:200], limit,
                                                                        query.get('since', [None])[0], compressed)
                    if self.headers.get('If-None-Match') == etag:
                        self.send_response(304)
                        self.send_header('ETag', etag)
                        self.send_header('Cache-Control', 'no-cache')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                    else:
                        self.send_bytes(body, 'application/json; charset=utf-8', etag=etag, compressed=compressed)
                else:
                    payload = self.server.dashboard.snapshot(query.get("q", [""])[0][:200], limit)
                    self.send_bytes(json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            except Exception as exc:
                self.send_bytes(json.dumps({"error": str(exc), "status": "IN_PROGRESS"}).encode(), "application/json", 503)
        elif parts.path in {'/api/staffer-search', '/api/staffer-person', '/api/staffer-route', '/api/staffer-offices', '/api/staffer-office-route', '/api/staffer-route-result', '/api/staffer-performance'}:
            query = parse_qs(parts.query)
            try:
                service = self.server.dashboard
                if parts.path.endswith('search'):
                    result = service.search(query.get('q', [''])[0][:200], max(1, min(100, int(query.get('limit', [20])[0]))))
                elif parts.path.endswith('person'):
                    result = service.person(query.get('id', [''])[0], self.routing_options(query))
                elif parts.path == '/api/staffer-offices':
                    result = service.offices(query.get('q', [''])[0][:200], query.get('limit', [20])[0])
                elif parts.path == '/api/staffer-route-result':
                    result = service.route_search_result(query.get('id', [''])[0])
                elif parts.path.endswith('route'):
                    office = parts.path == '/api/staffer-office-route'
                    origin, target = query.get('origin', [''])[0], query.get('office' if office else 'target', [''])[0]
                    if query.get('async', ['0'])[0] == '1':
                        # holder=<page token> names the page asking. supersedes=<search_id> makes that page let go of the
                        # search this request replaces. A GET carries none of the origin checks of the POST routes, so
                        # a supersede is honoured only when the browser reports the request as same-origin or typed
                        # in (Sec-Fetch-Site same-origin or none), or sends no such header (not a browser fetch).
                        extra = {}
                        fetch_site = (self.headers.get('Sec-Fetch-Site') or '').strip().lower()
                        supersedes = query.get('supersedes', [''])[0][:64]
                        if supersedes and fetch_site in ('', 'same-origin', 'none'):
                            extra['supersedes'] = supersedes
                        holder = query.get('holder', [''])[0][:64]
                        if holder:
                            extra['holder'] = holder
                        result = service.start_route_search(origin, target, self.routing_options(query), office=office, **extra)
                    else:
                        result = (service.office_route if office else service.route)(origin, target, self.routing_options(query))
                else:
                    result = {'ready': service.ready, 'stage': service.stage, 'metrics': service.metrics, 'errors': service.errors}
                raw = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()
                compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
                if compressed:
                    import gzip
                    raw = gzip.compress(raw, compresslevel=3)
                self.send_bytes(raw, 'application/json; charset=utf-8', compressed=compressed)
            except (ValueError, TypeError) as exc:
                self.send_bytes(json.dumps({'error': str(exc)}).encode(), 'application/json', 400)
        elif request := dashboard_request(parts.path, parts.query):
            if request.get('redirect'):
                self.send_response(302)
                self.send_header('Location', request['redirect'])
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                dashboard_file = getattr(self.server, 'dashboard_file', 'Moneyball.html')
                file_name = dashboard_file if request['file'] == 'Moneyball.html' else request['file']
                # The selected local preview owns every workspace route. Published
                # offline artifacts keep the existing resolver and integration.
                if dashboard_file == 'Moneyball_Redesign.html' and request['html']:
                    preview = self.server.dashboard.source_root / dashboard_file
                    if preview.is_file():
                        with preview.open('rb') as preview_handle:
                            if b'<!-- melange-clean-workspace-v2 -->' in preview_handle.read(128):
                                file_name = dashboard_file
                source = self.server.dashboard.source_root / file_name
                if not source.is_file():
                    self.send_error(404, "Dashboard artifact is missing")
                else:
                    import gzip
                    stat = source.stat()
                    profiles = source.with_name('cu-member-desk-profiles.js')
                    profiles_stat = profiles.stat() if source.name in {'Moneyball.html', 'Moneyball_Redesign.html'} and profiles.is_file() else None
                    stamp = (profiles_stat.st_mtime_ns, profiles_stat.st_size) if profiles_stat else None
                    zipped, etag = prepared_dashboard(source, stat.st_mtime_ns, stat.st_size, stamp)
                    mime = ('text/html' if request['html'] else 'application/javascript') + '; charset=utf-8'
                    if self.headers.get('If-None-Match') == etag:
                        self.send_bytes(b'', mime, 304, etag=etag)
                    else:
                        compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
                        self.send_bytes(zipped if compressed else gzip.decompress(zipped), mime, etag=etag, compressed=compressed)
        elif parts.path in {"/staffer-live", "/staffer-live/", "/staffer-live/index.html"}:
            self.send_asset("index.html")
        elif parts.path.startswith("/staffer-live/"):
            name = parts.path.removeprefix("/staffer-live/")
            if name.startswith('approved/'):
                self.send_approved_asset(name.removeprefix('approved/'))
            elif name in {"staffer-live.js", "staffer-live.css", "staffer-route.js", "staffer-person-facts.js", "staffer-assertions.js", "staffer-map-worker.js", "staffer-inventory-worker.js", "staffer-explorer.js", "staffer-groups.js", "launch.js", "melange-dashboard.js", "melange-dashboard.css"}:
                self.send_asset(name)
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def send_approved_asset(self, name):
        """Serve only generated presentation assets within the approved directory."""
        import gzip
        base = (ASSETS / 'approved').resolve()
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file() or path.suffix not in {'.json', '.html', '.js', '.css'}:
            self.send_error(404)
            return
        try:
            zipped, etag, _size = approved_payload(path, boot_prefix() if path == base / 'mlk-boot.js' else b'')
        except OSError:
            self.send_error(404)
            return
        mime = {'.json':'application/json','.html':'text/html','.js':'application/javascript','.css':'text/css'}[path.suffix] + '; charset=utf-8'
        if self.headers.get('If-None-Match') == etag:
            self.send_bytes(b'', mime, 304, etag=etag)
            return
        compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
        self.send_bytes(zipped if compressed else gzip.decompress(zipped), mime, etag=etag, compressed=compressed)

    def send_asset(self, name):
        path = ASSETS / name
        if not path.is_file():
            self.send_error(404)
            return
        mime = {".js": "application/javascript", ".css": "text/css", ".html": "text/html"}[path.suffix]
        self.send_bytes(path.read_bytes(), mime + "; charset=utf-8")

    def send_bytes(self, data, mime, status=200, etag=None, compressed=False):
        try:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-cache" if etag else "no-store")
            if etag:
                self.send_header('ETag', etag)
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
                self.send_header('Vary', 'Accept-Encoding')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A closed client can interrupt either the headers or response body.
            # Do not retry the response or mistake this for a persistence error.
            self.close_connection = True

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--port", type=int, default=8841)
    parser.add_argument("--dashboard-file", default="Moneyball.html",
                        choices=("Moneyball.html", "Moneyball_Redesign.html"))
    args = parser.parse_args()
    try:
        from .staffer_graph_snapshot import StaticGraph
        from .staffer_live_service import LiveService
    except ImportError:  # direct script execution: no package context
        import os as _bootstrap_os, sys as _bootstrap_sys
        _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
            _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
        if _bootstrap_root not in _bootstrap_sys.path:
            _bootstrap_sys.path.insert(0, _bootstrap_root)
        from fec.surfaces.staffer_graph_snapshot import StaticGraph
        from fec.surfaces.staffer_live_service import LiveService
    graph = StaticGraph(args.source_root)
    dashboard = LiveService(args.source_root, graph)
    server = WorkspaceServer(("127.0.0.1", args.port), Handler)
    server.dashboard = dashboard
    server.dashboard_file = args.dashboard_file
    dashboard.start()
    print(f"Live staffer dashboard http://127.0.0.1:{args.port}/staffer-live/ — IN PROGRESS", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
