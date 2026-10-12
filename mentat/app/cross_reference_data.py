"""Bounded, read-only public view of the latest local reconciliation run.

The browser cannot select a filesystem path. The run pointer and every artifact
must resolve under the fixed state directory, and embedded run IDs must agree.
Only explicitly allowed aggregate/evidence fields are returned; raw donor data
and filesystem paths stay in the local ledger.

The published report workbook is the accounting of record, so this view carries
no totals at all: it returns the run's scope and the run's digest of clear
refunds, evidence-backed contradictions and unusually large counted receipts
(``digest.json``, written by
``tools/cross_reference_digest.py``). A run without a digest says so.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

STEPS = ("define", "collect", "identify", "connect", "count", "verify")
STEP_STATUSES = {"pending", "running", "completed", "review", "failed"}
RUN_STATUSES = {"running", "completed", "failed"}
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}\Z")
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "state" / "cross-reference"
DIGEST_MISSING = ("This run has no contradiction/refund/large-receipt digest. Build it with "
                  "tools/cross_reference_digest.py <run_dir>.")
URL_FIELDS = {"source_url", "refund_source_url", "original_source_url"}
AMOUNT_FIELDS = {"amount", "before", "after", "delta", "refund_amount", "original_amount"}
COUNT_FIELDS = {"records"}
ID_FIELDS = {"record_id", "refund_record_id", "original_record_id"}


class EvidenceError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("The saved evidence contains duplicate fields.")
        result[key] = value
    return result


def _load(root, relative, limit, optional=False):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise EvidenceError("The saved evidence points outside its state directory.")
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        if optional:
            return None, None
        raise EvidenceError("A required saved evidence file is missing.") from None
    if len(raw) > limit:
        raise EvidenceError("The saved evidence exceeds the display size limit.")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError):
        raise EvidenceError("The saved evidence is not valid JSON.") from None
    if not isinstance(value, dict):
        raise EvidenceError("The saved evidence must contain an object.")
    return value, raw


def _text(value, limit=500):
    return value[:limit] if isinstance(value, str) else ""


def _number(value, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if integer and (value < 0 or value != int(value)):
        return None
    return int(value) if integer else value


def _source_url(value):
    """Allow only public FEC filing links; never pass API credentials to a link."""
    if not isinstance(value, str) or len(value) > 2000:
        return ""
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or parts.hostname not in {"docquery.fec.gov", "www.fec.gov"}
                or parts.username or parts.password or parts.port not in (None, 443)
                or (parts.query and not re.fullmatch(r"\d{5,30}", parts.query))):
            return ""
        # Some legacy fecimg links use the numeric image ID as their bare query.
        # Other query parameters and fragments are unnecessary in this view.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return ""


def _items(value):
    return value if isinstance(value, list) else []


def _identity(value, run_id):
    if value.get("schema_version") != 1 or value.get("run_id") != run_id:
        raise EvidenceError("The saved report and run pointer do not match.")


def _steps(value):
    supplied = {row.get("id"): row for row in _items(value) if isinstance(row, dict)}
    result = []
    for name in STEPS:
        row = supplied.get(name, {})
        state = row.get("status", "pending")
        if state not in STEP_STATUSES:
            raise EvidenceError("A saved process step has an unsupported status.")
        result.append({"id": name, "status": state, "detail": _text(row.get("detail"), 1000)})
    return result


def _rows(value, fields, limit):
    result = []
    for row in _items(value)[:limit]:
        if not isinstance(row, dict):
            continue
        item = {}
        for key in fields:
            item[key] = (_source_url(row.get(key)) if key in URL_FIELDS else
                         _number(row.get(key), integer=key in COUNT_FIELDS)
                         if key in AMOUNT_FIELDS | COUNT_FIELDS
                         else _text(str(row.get(key, ""))) if key == "cycle"
                         else _text(row.get(key), 120) if key in ID_FIELDS
                         else _text(row.get(key), 300) if key == "description"
                         else _text(row.get(key)))
        result.append(item)
    return result


def _digest(root, run_id):
    """The run's refunds, contradictions and large counted rows, if saved.

    Nothing here is a total of giving: each count and amount describes only the
    rows listed beside it, and the basis sentence states the sizes that chose
    them.
    """
    value, _ = _load(root, Path(run_id) / "digest.json", 4_000_000, optional=True)
    if value is None:
        return None
    basis = value.get("basis") if isinstance(value.get("basis"), dict) else {}
    refunds = value.get("refunds") if isinstance(value.get("refunds"), dict) else {}
    outliers = value.get("outliers") if isinstance(value.get("outliers"), dict) else {}
    return {
        "basis": _text(basis.get("text"), 500),
        "refunds": {
            "linked": _rows(refunds.get("linked"), ("refund_record_id", "original_record_id", "company",
                                                    "cycle", "refund_amount", "original_amount",
                                                    "refund_source_url", "original_source_url"), 200),
            "linked_count": _number(refunds.get("linked_count"), integer=True),
            "linked_amount": _number(refunds.get("linked_amount")),
            "unlinked": _rows(refunds.get("unlinked"), ("record_id", "company", "cycle", "amount",
                                                        "description", "source_url"), 200),
            "unlinked_count": _number(refunds.get("unlinked_count"), integer=True),
            "unlinked_amount": _number(refunds.get("unlinked_amount")),
        },
        "outliers": {
            "non_giving": _rows(outliers.get("non_giving"), ("record_id", "company", "cycle", "amount",
                                                             "role", "description", "source_url"), 200),
            "non_giving_count": _number(outliers.get("non_giving_count"), integer=True),
            "non_giving_amount": _number(outliers.get("non_giving_amount")),
            "large": _rows(outliers.get("large"), ("record_id", "company", "cycle", "amount",
                                                   "entity", "role", "source_url"), 200),
            "large_count": _number(outliers.get("large_count"), integer=True),
            "large_amount": _number(outliers.get("large_amount")),
        },
    }


def _report(value, root, run_id):
    """Scope, and the digest. No total of any kind leaves this function."""
    if value.get("status") != "completed":
        raise EvidenceError("The saved report has not completed computation.")
    scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    digest = _digest(root, run_id)
    return {
        "run_id": value["run_id"], "status": "completed",
        "generated_at": _text(value.get("generated_at")),
        "scope": {
            "industries": [_text(x) for x in _items(scope.get("industries"))[:100] if isinstance(x, str)],
            "companies": [_text(x) for x in _items(scope.get("companies"))[:1000] if isinstance(x, str)],
            "cycles": [str(x)[:20] for x in _items(scope.get("cycles"))[:20] if isinstance(x, (str, int))],
            "company_count": len([x for x in _items(scope.get("companies")) if isinstance(x, str)]),
        },
        "digest_available": digest is not None, "digest": digest,
        "digest_message": "" if digest is not None else DIGEST_MISSING,
        "limitations": [_text(x, 1000) for x in _items(value.get("limitations"))[:30] if isinstance(x, str)],
    }


def _age_hours(value, now):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp > now:
            return None
        return (now - stamp).total_seconds() / 3600
    except (TypeError, ValueError, AttributeError):
        return None


def _run_view(root, run_id, now, expected_digest=None):
    """One saved run, read once and rendered display-safe.

    Both entry points below share this reader. The pointer path passes the
    fingerprint the pointer claims; the industry path passes none and
    therefore claims none. `report_sha256` is always the digest of the bytes
    this call actually read, never a claim copied from a pointer.
    """
    report, raw_report = _load(root, Path(run_id) / "report.json", 2_000_000, optional=True)
    progress, _ = _load(root, Path(run_id) / "progress.json", 256_000, optional=True)
    if report is None and progress is None:
        raise EvidenceError("The selected run has no saved report or progress evidence.")
    if expected_digest is not None:
        if not isinstance(expected_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
            raise EvidenceError("The saved report fingerprint is invalid.")
        if raw_report is None or hashlib.sha256(raw_report).hexdigest() != expected_digest:
            raise EvidenceError("The saved report no longer matches its recorded fingerprint.")
    result = {"available": True, "run_id": run_id, "status": "running", "notices": [],
              "report_sha256": hashlib.sha256(raw_report).hexdigest() if raw_report is not None else None}
    if progress is not None:
        _identity(progress, run_id)
        status = progress.get("status")
        if status not in RUN_STATUSES:
            raise EvidenceError("The saved run has an unsupported status.")
        result["status"] = status
        result["progress"] = {"run_id": run_id, "status": status,
                              "updated_at": _text(progress.get("updated_at")),
                              "steps": _steps(progress.get("steps")),
                              "message": _text(progress.get("message"), 1000)}
        age = _age_hours(progress.get("updated_at"), now)
        if status == "running" and (age is None or age > 1):
            result["notices"].append("Progress is stale or undated. The saved status does not establish that a worker is still running.")
    if report is not None:
        _identity(report, run_id)
        result["report"] = _report(report, root, run_id)
        result["status"] = "completed" if result["status"] != "failed" else "failed"
        age = _age_hours(report.get("generated_at"), now)
        result["report_age_hours"] = round(age, 1) if age is not None else None
        if age is None:
            result["notices"].append("The report generation time is missing or invalid; report age is unknown.")
        elif age > 24 * 30:
            result["notices"].append("This reconciliation was generated more than 30 days ago. Review its source scope before reusing the totals.")
    elif result["status"] == "completed":
        raise EvidenceError("The run is marked completed but its report is missing.")
    result["industry"] = next(iter(result.get("report", {}).get("scope", {}).get("industries", [])), None)
    return result


def _unreadable(error, industry=None):
    # File errors may disclose local filenames; expose only our own messages.
    message = str(error) if isinstance(error, EvidenceError) else "The saved evidence could not be read safely."
    return {"available": False, "status": "error", "industry": industry, "message": message}


def latest_cross_reference(state_root=DEFAULT_ROOT, *, now=None):
    """Return a display-safe snapshot, never collect, mutate, or run a process."""
    root = Path(state_root).resolve()
    now = now or datetime.now(timezone.utc)
    try:
        pointer, _ = _load(root, "latest.json", 16_384, optional=True)
        if pointer is None:
            return {"available": False, "status": "empty", "industry": None,
                    "message": "No cross-reference run has been saved yet."}
        run_id = pointer.get("run_id")
        if pointer.get("schema_version") != 1 or not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise EvidenceError("The saved run pointer is invalid.")
        result = _run_view(root, run_id, now, pointer.get("report_sha256"))
        result["selected_by"] = "pointer"
        return result
    except (EvidenceError, OSError, RuntimeError) as error:
        return _unreadable(error)


def cross_reference_for_industry(state_root, industry, *, now=None):
    """The newest saved run whose scope is exactly this one industry.

    Run directories are scanned newest-first by id, which the runner mints in
    lexicographic time order. The latest.json pointer is deliberately not
    consulted: it names one run for the whole console, and this view is asked
    for one industry.
    """
    root = Path(state_root).resolve()
    now = now or datetime.now(timezone.utc)
    try:
        if not isinstance(industry, str) or not industry:
            raise EvidenceError("Choose a registered industry.")
        names = sorted((entry.name for entry in root.iterdir()
                        if entry.is_dir() and RUN_ID.fullmatch(entry.name)),
                       reverse=True) if root.is_dir() else []
        for run_id in names:
            report, _ = _load(root, Path(run_id) / "report.json", 2_000_000, optional=True)
            if report is None:
                continue
            scope = report.get("scope") if isinstance(report.get("scope"), dict) else {}
            if scope.get("industries") != [industry]:
                continue
            result = _run_view(root, run_id, now)
            result["selected_by"] = "industry"
            result["industry"] = industry
            return result
        return {"available": False, "status": "empty", "industry": industry,
                "message": "No cross-reference run has been saved for this industry yet."}
    except (EvidenceError, OSError, RuntimeError) as error:
        return _unreadable(error, industry)
