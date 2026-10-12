# -*- coding: utf-8 -*-
r"""Collect Senate LDA filings from a validated worklist or year sweep.

Worklist collection fetches only listed UUIDs. Sweep collection stores raw API
pages. Both modes write idempotent MD5 manifests beneath
``%LOCALAPPDATA%\Melange\lda_rebuild`` and never touch the live data tree.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import datetime
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


API_BASE = "https://lda.gov/api/v1/filings/"
USER_AGENT = "Melange-research/1.0 (contact: goff0070@umn.edu)"
REQUEST_TIMEOUT = 60
SWEEP_PAGE_SIZE = 25
MIN_FILING_YEAR = 2021
MAX_FILING_YEAR = 2026
WORKLIST_FIELDS = (
    "report_uuid",
    "lob_id",
    "filing_period_code",
    "is_amendment",
    "is_no_activity",
    "filing_url",
)
UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


class IngestError(Exception):
    """Expected ingest failure that must exit 2."""


def dump_atomic(obj, path, **kwargs):
    """Write JSON through a temporary sibling and replace atomically."""
    temp_path = path + ".tmp." + str(os.getpid())
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, **kwargs)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _write_bytes_atomic(data, path):
    temp_path = path + ".tmp." + str(os.getpid())
    try:
        with open(temp_path, "wb") as handle:
            handle.write(data)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def file_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def bytes_md5(data):
    return hashlib.md5(data).hexdigest()


def normalize_worklist_boolean(value, field, record_name):
    """Normalize only JSON booleans or the generator's exact string literals."""
    if isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise IngestError(
        f"worklist record {record_name} field {field} must be a boolean "
        'or exact lowercase string "true"/"false"'
    )


def validate_report_uuid(value, record_name):
    if not isinstance(value, str) or UUID_RE.fullmatch(value) is None:
        raise IngestError(
            f"worklist record {record_name} field report_uuid is not a "
            "strict 8-4-4-4-12 hexadecimal UUID"
        )
    return value


def validate_filing_year(value, record_name):
    if isinstance(value, bool):
        filing_year = None
    elif isinstance(value, int):
        filing_year = value
    elif isinstance(value, str) and value in {
        str(year) for year in range(MIN_FILING_YEAR, MAX_FILING_YEAR + 1)
    }:
        filing_year = int(value)
    else:
        filing_year = None
    if filing_year is None or not (
        MIN_FILING_YEAR <= filing_year <= MAX_FILING_YEAR
    ):
        raise IngestError(
            f"worklist record {record_name} field filing_year must be an "
            f"integer or exact four-digit string in "
            f"{MIN_FILING_YEAR}..{MAX_FILING_YEAR}"
        )
    return filing_year


def default_paths(local_app_data=None):
    """Return rebuild paths without touching the filesystem."""
    base = local_app_data or os.environ.get("LOCALAPPDATA")
    if not base:
        raise IngestError("LOCALAPPDATA is missing")
    rebuild_root = os.path.join(base, "Melange", "lda_rebuild")
    raw_dir = os.path.join(rebuild_root, "raw")
    return {
        "raw_dir": raw_dir,
        "worklist": os.path.join(
            raw_dir, "pinned_2021_filings_worklist.json"
        ),
        "filings_dir": os.path.join(raw_dir, "filings_2021"),
        "partitioned_filings_dir": os.path.join(raw_dir, "filings"),
        "sweeps_dir": os.path.join(raw_dir, "sweeps"),
        "manifest": os.path.join(raw_dir, "fec_lda_filings_manifest.json"),
    }


def _read_bytes(path, label):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise IngestError(f"cannot read {label} {path}: {exc}") from exc


def _decode_json(data, label):
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IngestError(f"invalid JSON in {label}: {exc}") from exc


def load_worklist(path, legacy_default_year=None):
    data = _read_bytes(path, "worklist")
    payload = _decode_json(data, f"worklist {path}")
    if not isinstance(payload, list):
        raise IngestError("worklist must be a JSON list")
    seen = set()
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            raise IngestError(f"worklist record {index} is not an object")
        for key in WORKLIST_FIELDS:
            if key not in record:
                raise IngestError(
                    f"worklist record {index} missing key {key}"
                )
        if "filing_year" not in record:
            if legacy_default_year is None:
                raise IngestError(
                    f"worklist record {index} missing key filing_year"
                )
            record["filing_year"] = legacy_default_year
        record["filing_year"] = validate_filing_year(
            record["filing_year"], index
        )
        report_uuid = validate_report_uuid(record["report_uuid"], index)
        if report_uuid in seen:
            raise IngestError(f"duplicate worklist UUID {report_uuid}")
        seen.add(report_uuid)
        if not isinstance(record["lob_id"], str) or not record["lob_id"]:
            raise IngestError(
                f"worklist UUID {report_uuid} has invalid lob_id"
            )
        if not isinstance(record["filing_period_code"], str):
            raise IngestError(
                f"worklist UUID {report_uuid} has invalid filing_period_code"
            )
        for key in ("is_amendment", "is_no_activity"):
            record[key] = normalize_worklist_boolean(
                record[key], key, f"{index} ({report_uuid})"
            )
        if not isinstance(record["filing_url"], str):
            raise IngestError(
                f"worklist UUID {report_uuid} has invalid filing_url"
            )
    return payload, bytes_md5(data)


def load_manifest(path):
    if not os.path.exists(path):
        return {"md5": {}}
    payload = _decode_json(
        _read_bytes(path, "manifest"), f"manifest {path}"
    )
    if not isinstance(payload, dict):
        raise IngestError("existing filing manifest must be an object")
    md5_map = payload.get("md5")
    if not isinstance(md5_map, dict):
        raise IngestError("existing filing manifest missing object key md5")
    return payload


def _same_path(left, right):
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def worklist_manifest_path(paths, worklist_path):
    if _same_path(worklist_path, paths["worklist"]):
        return paths["manifest"]
    stem = os.path.splitext(os.path.basename(worklist_path))[0]
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    if not safe_stem:
        raise IngestError(
            f"cannot derive manifest name from worklist {worklist_path}"
        )
    return os.path.join(
        paths["raw_dir"], f"fec_lda_filings_manifest_{safe_stem}.json"
    )


def filing_paths_for_record(paths, record, legacy_output=False):
    report_uuid = record["report_uuid"]
    filing_year = record["filing_year"]
    partitioned_path = os.path.join(
        paths["partitioned_filings_dir"],
        str(filing_year),
        report_uuid + ".json",
    )
    candidates = [partitioned_path]
    if filing_year == 2021:
        legacy_path = os.path.join(
            paths["filings_dir"], report_uuid + ".json"
        )
        if legacy_output:
            candidates = [legacy_path, partitioned_path]
        else:
            candidates.append(legacy_path)
    return candidates[0], tuple(dict.fromkeys(candidates))


def merged_resume_manifest(primary_manifest, legacy_manifest=None):
    md5_map = {}
    for manifest in (legacy_manifest, primary_manifest):
        if not isinstance(manifest, dict):
            continue
        for key, digest in manifest.get("md5", {}).items():
            if isinstance(key, str) and isinstance(digest, str):
                md5_map[key] = digest
    return {"md5": md5_map}


def can_skip_existing(path, report_uuid, manifest):
    """Return true only when the existing file matches its manifest MD5."""
    if not os.path.isfile(path):
        return False
    expected = manifest.get("md5", {}).get(report_uuid)
    if not isinstance(expected, str) or not expected:
        return False
    try:
        return file_md5(path) == expected.lower()
    except OSError:
        return False


def parse_rpm(value):
    try:
        rpm = int(value)
    except (TypeError, ValueError) as exc:
        raise IngestError(f"LDA_RPM must be a positive integer, got {value!r}") from exc
    if rpm <= 0:
        raise IngestError(f"LDA_RPM must be a positive integer, got {value!r}")
    return rpm


class RequestPacer:
    """Sleep the configured interval between every outbound request."""

    def __init__(self, rpm, sleep_fn=time.sleep):
        self.interval = 60.0 / rpm
        self.sleep_fn = sleep_fn
        self.started = False

    def wait(self):
        if self.started:
            self.sleep_fn(self.interval)
        self.started = True


def validate_filing_payload(payload, record):
    """Fail closed on the activity schema used by the rebuild."""
    report_uuid = record["report_uuid"]
    if not isinstance(payload, dict):
        raise IngestError(
            f"filing {report_uuid} response is not a JSON object"
        )
    if "filing_uuid" not in payload:
        raise IngestError(f"filing {report_uuid} missing key filing_uuid")
    filing_uuid = payload["filing_uuid"]
    if not isinstance(filing_uuid, str):
        raise IngestError(
            f"filing {report_uuid} key filing_uuid is not a string"
        )
    if filing_uuid.lower() != report_uuid.lower():
        raise IngestError(
            f"filing {report_uuid} identity mismatch: payload filing_uuid "
            f"is {filing_uuid}"
        )
    if "lobbying_activities" not in payload:
        raise IngestError(
            f"filing {report_uuid} missing key lobbying_activities"
        )
    activities = payload["lobbying_activities"]
    if not isinstance(activities, list):
        raise IngestError(
            f"filing {report_uuid} key lobbying_activities is not a list"
        )
    if record["is_no_activity"] and activities:
        raise IngestError(
            f"filing {report_uuid} is_no_activity=true but "
            "lobbying_activities is nonempty"
        )
    for index, activity in enumerate(activities):
        if not isinstance(activity, dict):
            raise IngestError(
                f"filing {report_uuid} lobbying_activities[{index}] "
                "is not an object"
            )
        if "description" not in activity:
            raise IngestError(
                f"filing {report_uuid} lobbying_activities[{index}] "
                "missing key description"
            )
        if not isinstance(activity["description"], str):
            raise IngestError(
                f"filing {report_uuid} lobbying_activities[{index}].description "
                "is not a string"
            )


def validate_existing_filing(path, record):
    report_uuid = record["report_uuid"]
    data = _read_bytes(path, f"existing filing {report_uuid}")
    payload = _decode_json(data, f"existing filing {report_uuid}")
    validate_filing_payload(payload, record)
    return data


def _headers(environ):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    api_key = environ.get("LDA_API_KEY")
    if api_key:
        headers["Authorization"] = "Token " + api_key
    return headers


class RedirectRefusalHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from following redirects with request credentials."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_url_opener():
    return urllib.request.build_opener(RedirectRefusalHandler()).open


def _fetch_url(
    url,
    label,
    headers,
    pacer,
    opener=None,
    sleep_fn=time.sleep,
    max_tries=5,
    max_backoff_seconds=60,
):
    """Fetch one URL with redirect refusal and bounded transient retries."""
    if opener is None:
        opener = build_url_opener()
    request = urllib.request.Request(url, headers=headers, method="GET")
    for attempt in range(max_tries):
        pacer.wait()
        try:
            with opener(request, timeout=REQUEST_TIMEOUT) as response:
                response_url = response.geturl()
                if response_url != url:
                    raise IngestError(
                        f"{label} redirect refused: "
                        f"requested {url}, received {response_url}"
                    )
                return response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            if 300 <= status <= 399:
                raise IngestError(
                    f"{label} redirect refused (HTTP {status})"
                ) from exc
            if status == 404:
                raise IngestError(
                    f"{label} returned HTTP 404"
                ) from exc
            retryable = status == 429 or 500 <= status <= 599
            if not retryable:
                raise IngestError(
                    f"{label} returned HTTP {status}"
                ) from exc
            if attempt + 1 == max_tries:
                raise IngestError(
                    f"{label} returned HTTP {status} after "
                    f"{max_tries} tries"
                ) from exc
            retry_after = None
            if exc.headers is not None:
                raw_retry_after = exc.headers.get("Retry-After")
                if raw_retry_after is not None:
                    try:
                        retry_after = max(0.0, float(raw_retry_after))
                    except (TypeError, ValueError):
                        retry_after = None
            delay = min(2 ** attempt, max_backoff_seconds)
            if retry_after is not None:
                delay = max(delay, retry_after)
            sleep_fn(delay)
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
            OSError,
        ) as exc:
            if attempt + 1 == max_tries:
                raise IngestError(
                    f"{label} transient request/read failure "
                    f"after {max_tries} tries: {exc}"
                ) from exc
            sleep_fn(min(2 ** attempt, max_backoff_seconds))
    raise IngestError(f"{label} exhausted request attempts")


def fetch_filing(
    report_uuid,
    headers,
    pacer,
    opener=None,
    sleep_fn=time.sleep,
    max_tries=5,
):
    """Fetch one filing with redirect refusal and bounded transient retries."""
    validate_report_uuid(report_uuid, "fetch")
    url = API_BASE + report_uuid + "/"
    return _fetch_url(
        url,
        f"filing {report_uuid}",
        headers,
        pacer,
        opener=opener,
        sleep_fn=sleep_fn,
        max_tries=max_tries,
    )


def sweep_page_url(filing_year, page_number):
    return (
        API_BASE
        + f"?filing_year={filing_year}&page={page_number}"
        + f"&page_size={SWEEP_PAGE_SIZE}"
    )


def fetch_sweep_page(
    filing_year,
    page_number,
    headers,
    pacer,
    opener=None,
    sleep_fn=time.sleep,
    max_tries=5,
    max_backoff_seconds=60,
):
    url = sweep_page_url(filing_year, page_number)
    return _fetch_url(
        url,
        f"sweep {filing_year} page {page_number}",
        headers,
        pacer,
        opener=opener,
        sleep_fn=sleep_fn,
        max_tries=max_tries,
        max_backoff_seconds=max_backoff_seconds,
    )


def _generated():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def validate_sweep_page(payload, filing_year, page_number):
    label = f"sweep {filing_year} page {page_number}"
    if not isinstance(payload, dict):
        raise IngestError(f"{label} response is not a JSON object")
    if "results" not in payload:
        raise IngestError(f"{label} missing key results")
    results = payload["results"]
    if not isinstance(results, list):
        raise IngestError(f"{label} key results is not a list")
    if "next" not in payload:
        raise IngestError(f"{label} missing key next")
    next_value = payload["next"]
    if next_value is not None and (
        not isinstance(next_value, str) or not next_value
    ):
        raise IngestError(f"{label} key next is not null or a nonempty string")
    if not results and next_value is not None:
        raise IngestError(f"{label} has empty results before the final page")
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise IngestError(f"{label} results[{index}] is not an object")
        if "filing_year" not in result:
            raise IngestError(
                f"{label} results[{index}] missing key filing_year"
            )
        raw_result_year = result["filing_year"]
        try:
            result_year = validate_filing_year(
                raw_result_year, f"sweep {filing_year} page {page_number}"
            )
        except IngestError as exc:
            raise IngestError(
                f"{label} results[{index}] has invalid filing_year "
                f"{raw_result_year!r}"
            ) from exc
        if result_year != filing_year:
            raise IngestError(
                f"{label} results[{index}] filing_year is "
                f"{raw_result_year!r}; expected {filing_year}"
            )
    return next_value is not None


def load_sweep_manifest(path, filing_year):
    if not os.path.exists(path):
        return {
            "filing_year": filing_year,
            "page_count": 0,
            "complete": False,
            "md5": {},
        }
    payload = _decode_json(
        _read_bytes(path, "sweep manifest"), f"sweep manifest {path}"
    )
    if not isinstance(payload, dict):
        raise IngestError("existing sweep manifest must be an object")
    if payload.get("filing_year") != filing_year:
        raise IngestError(
            f"sweep manifest filing_year is {payload.get('filing_year')!r}; "
            f"expected {filing_year}"
        )
    page_count = payload.get("page_count")
    if (
        isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or page_count < 0
    ):
        raise IngestError("existing sweep manifest has invalid page_count")
    if not isinstance(payload.get("complete"), bool):
        raise IngestError("existing sweep manifest has invalid complete flag")
    if not isinstance(payload.get("md5"), dict):
        raise IngestError("existing sweep manifest missing object key md5")
    return payload


def _write_sweep_manifest(
    path, filing_year, page_count, complete, md5_map, metadata=None
):
    payload = {
        "filing_year": filing_year,
        "page_count": page_count,
        "complete": bool(complete),
        "md5": dict(sorted(md5_map.items())),
        "generated": _generated(),
    }
    if metadata:
        payload.update(metadata)
    dump_atomic(
        payload,
        path,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    return payload


def _manifest_payload(
    fetched, skipped_existing, failures, md5_map, worklist_md5
):
    return {
        "fetched": fetched,
        "skipped_existing": skipped_existing,
        "failures": list(failures),
        "md5": dict(sorted(md5_map.items())),
        "generated": _generated(),
        "worklist_md5": worklist_md5,
    }


def _write_manifest(
    path, fetched, skipped_existing, failures, md5_map, worklist_md5
):
    manifest = _manifest_payload(
        fetched,
        skipped_existing,
        failures,
        md5_map,
        worklist_md5,
    )
    dump_atomic(
        manifest,
        path,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    return manifest


def _run_worklist(paths, worklist_path, environ):
    legacy_default = _same_path(worklist_path, paths["worklist"])
    worklist, worklist_md5 = load_worklist(
        worklist_path, legacy_default_year=2021 if legacy_default else None
    )
    rpm = parse_rpm(environ.get("LDA_RPM", "15"))
    manifest_path = worklist_manifest_path(paths, worklist_path)
    previous_manifest = load_manifest(manifest_path)
    legacy_manifest = None
    if (
        not _same_path(manifest_path, paths["manifest"])
        and any(record["filing_year"] == 2021 for record in worklist)
    ):
        legacy_manifest = load_manifest(paths["manifest"])
    resume_manifest = merged_resume_manifest(
        previous_manifest, legacy_manifest=legacy_manifest
    )
    pacer = RequestPacer(rpm)
    headers = _headers(environ)
    opener = build_url_opener()

    fetched = 0
    skipped_existing = 0
    failures = []
    worklist_uuids = {record["report_uuid"] for record in worklist}
    md5_map = {
        report_uuid: digest
        for report_uuid, digest in previous_manifest.get("md5", {}).items()
        if report_uuid in worklist_uuids and isinstance(digest, str)
    }

    for record in worklist:
        report_uuid = record["report_uuid"]
        md5_map.pop(report_uuid, None)
        output_path, resume_paths = filing_paths_for_record(
            paths, record, legacy_output=legacy_default
        )
        paths_to_check = resume_paths
        if os.path.isfile(output_path):
            paths_to_check = (output_path,)
        existing_path = next(
            (
                candidate
                for candidate in paths_to_check
                if can_skip_existing(
                    candidate, report_uuid, resume_manifest
                )
            ),
            None,
        )
        if existing_path is not None:
            try:
                existing_data = validate_existing_filing(
                    existing_path, record
                )
                skipped_existing += 1
                md5_map[report_uuid] = bytes_md5(existing_data)
            except IngestError as exc:
                failures.append(
                    {"report_uuid": report_uuid, "error": str(exc)}
                )
            continue
        try:
            data = fetch_filing(report_uuid, headers, pacer, opener=opener)
            payload = _decode_json(data, f"filing {report_uuid}")
            validate_filing_payload(payload, record)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            _write_bytes_atomic(data, output_path)
            md5_map[report_uuid] = bytes_md5(data)
            fetched += 1
            _write_manifest(
                manifest_path,
                fetched,
                skipped_existing,
                failures,
                md5_map,
                worklist_md5,
            )
        except IngestError as exc:
            failures.append(
                {"report_uuid": report_uuid, "error": str(exc)}
            )
        except Exception as exc:
            failures.append(
                {
                    "report_uuid": report_uuid,
                    "error": f"unexpected fetch failure: {exc}",
                }
            )

    _write_manifest(
        manifest_path,
        fetched,
        skipped_existing,
        failures,
        md5_map,
        worklist_md5,
    )
    print(
        "lda filings: "
        f"fetched {fetched} | skipped_existing {skipped_existing} | "
        f"failures {len(failures)} | manifest {manifest_path}"
    )
    if failures:
        raise IngestError(
            f"{len(failures)} worklist filing(s) failed; see manifest"
        )
    if len(md5_map) != len(worklist):
        raise IngestError(
            f"manifest certifies {len(md5_map)} filings; expected {len(worklist)}"
        )
    return 0


def _sweep_directory(paths, filing_year, generation_root=None):
    if generation_root is None:
        return os.path.join(paths["sweeps_dir"], str(filing_year)), False
    root = os.path.abspath(generation_root)
    if _same_path(root, paths["sweeps_dir"]):
        raise IngestError("immutable generation root cannot be the legacy sweep root")
    directory = os.path.join(root, str(filing_year))
    if os.path.exists(directory) and os.listdir(directory):
        raise IngestError(
            f"immutable LDA year directory already exists and is nonempty: {directory}"
        )
    return directory, True


def _run_sweep(paths, filing_year, environ, generation_root=None):
    rpm = parse_rpm(environ.get("LDA_RPM", "15"))
    authentication_mode = "registered" if environ.get("LDA_API_KEY") else "anonymous"
    official_limit = 120 if authentication_mode == "registered" else 15
    if rpm > official_limit:
        raise IngestError(
            f"LDA_RPM {rpm} exceeds the official {authentication_mode} limit "
            f"of {official_limit}/minute"
        )
    pacer = RequestPacer(rpm)
    headers = _headers(environ)
    opener = build_url_opener()
    sweep_dir, immutable_generation = _sweep_directory(
        paths, filing_year, generation_root=generation_root
    )
    terms_checked_at = environ.get("LDA_TERMS_CHECKED_AT")
    if immutable_generation and not terms_checked_at:
        raise IngestError("immutable LDA generation requires LDA_TERMS_CHECKED_AT")
    try:
        max_tries = int(environ.get("LDA_MAX_TRIES", "12" if immutable_generation else "5"))
        max_backoff_seconds = int(environ.get("LDA_MAX_BACKOFF_SECONDS", "60"))
    except ValueError as exc:
        raise IngestError("LDA retry controls must be positive integers") from exc
    if max_tries <= 0 or max_backoff_seconds <= 0:
        raise IngestError("LDA retry controls must be positive integers")
    manifest_path = os.path.join(sweep_dir, "sweep_manifest.json")
    os.makedirs(sweep_dir, exist_ok=True)
    previous_manifest = (
        {"filing_year": filing_year, "page_count": 0, "complete": False, "md5": {}}
        if immutable_generation
        else load_sweep_manifest(manifest_path, filing_year)
    )
    resume_manifest = {"md5": previous_manifest.get("md5", {})}

    fetched = 0
    skipped_existing = 0
    md5_map = {}
    source_counts = []
    started_at = _generated()
    page_number = 1
    while True:
        page_name = f"page_{page_number:05d}.json"
        page_path = os.path.join(sweep_dir, page_name)
        if can_skip_existing(page_path, page_name, resume_manifest):
            data = _read_bytes(
                page_path, f"existing sweep {filing_year} page {page_number}"
            )
            payload = _decode_json(
                data, f"existing sweep {filing_year} page {page_number}"
            )
            has_next = validate_sweep_page(
                payload, filing_year, page_number
            )
            skipped_existing += 1
        else:
            data = fetch_sweep_page(
                filing_year,
                page_number,
                headers,
                pacer,
                opener=opener,
                max_tries=max_tries,
                max_backoff_seconds=max_backoff_seconds,
            )
            payload = _decode_json(
                data, f"sweep {filing_year} page {page_number}"
            )
            has_next = validate_sweep_page(
                payload, filing_year, page_number
            )
            _write_bytes_atomic(data, page_path)
            fetched += 1
        source_counts.append(payload["count"])
        md5_map[page_name] = bytes_md5(data)
        complete = not has_next
        metadata = {
            "immutable_generation": immutable_generation,
            "generation_id": (
                os.path.basename(os.path.abspath(generation_root))
                if immutable_generation else None
            ),
            "query": f"filing_year={filing_year}&page_size={SWEEP_PAGE_SIZE}",
            "started_at": started_at,
            "completed_at": _generated() if complete else None,
            "source_count_first": source_counts[0],
            "source_count_final": source_counts[-1],
            "source_count_min": min(source_counts),
            "source_count_max": max(source_counts),
            "authentication_mode": authentication_mode,
            "rate_limit_rpm": rpm,
            "max_request_tries": max_tries,
            "max_backoff_seconds": max_backoff_seconds,
            "terms_uri": "https://lda.gov/api/tos/",
            "terms_checked_at": terms_checked_at,
        }
        _write_sweep_manifest(
            manifest_path,
            filing_year,
            page_number,
            complete,
            md5_map,
            metadata=metadata,
        )
        if complete:
            break
        page_number += 1

    print(
        f"lda sweep {filing_year}: pages {page_number} | "
        f"fetched {fetched} | skipped_existing {skipped_existing} | "
        f"manifest {manifest_path}"
    )
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Collect Senate LDA filings or raw year-sweep pages."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--worklist",
        metavar="PATH",
        help="filing worklist JSON (default: pinned 2021 worklist)",
    )
    mode.add_argument(
        "--sweep-year",
        metavar="YYYY",
        help="collect paginated filing-list responses for one year",
    )
    parser.add_argument(
        "--sweep-generation-root",
        metavar="PATH",
        help="write a fresh immutable year under PATH instead of resuming legacy sweeps",
    )
    return parser.parse_args(list(argv))


def _parse_sweep_year(value):
    try:
        filing_year = int(value)
    except (TypeError, ValueError) as exc:
        raise IngestError(
            f"--sweep-year must be an integer in "
            f"{MIN_FILING_YEAR}..{MAX_FILING_YEAR}"
        ) from exc
    if str(filing_year) != str(value) or not (
        MIN_FILING_YEAR <= filing_year <= MAX_FILING_YEAR
    ):
        raise IngestError(
            f"--sweep-year must be an integer in "
            f"{MIN_FILING_YEAR}..{MAX_FILING_YEAR}"
        )
    return filing_year


def _run(environ=None, argv=()):
    environ = os.environ if environ is None else environ
    paths = default_paths(environ.get("LOCALAPPDATA"))
    args = _parse_args(argv)
    if args.sweep_generation_root and args.sweep_year is None:
        raise IngestError("--sweep-generation-root requires --sweep-year")
    if args.sweep_year is not None:
        return _run_sweep(
            paths,
            _parse_sweep_year(args.sweep_year),
            environ,
            generation_root=args.sweep_generation_root,
        )
    worklist_path = args.worklist or paths["worklist"]
    return _run_worklist(paths, worklist_path, environ)


def main(argv=None):
    try:
        return _run(argv=sys.argv[1:] if argv is None else argv)
    except IngestError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: LDA filing ingest failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
