"""Single-attempt FEC transport with shared quota and original response evidence.

The original PAC Client can own its existing retry/cache policy. Every transport
invocation admits exactly one HTTP attempt; quota, run-cap and storage failures
are nonretryable. This module never starts an HTTP server or loads credentials
at import. Collection coverage remains the caller's separate responsibility.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from api_request_budget import budget_path, legacy_budget_paths, reserve as budget_reserve
from api_request_pool import NoRedirect, RequestPool

ORIGIN = 'https://api.open.fec.gov/v1/'
ENDPOINT = re.compile(r'(?:schedules/schedule_(?:[abef]|h4)/(?:[0-9]+/)?|'
                      r'committee/C[0-9]{8}/totals/|candidate/[HSP][A-Z0-9]{8}/committees/|communication_costs/|electioneering/)')


class FECAdmissionRefused(ValueError):
    pass


class FECArchiveFailed(ValueError):
    pass


def durable_new(path, body):
    """Create one immutable evidence file; failure prevents parsed/cache use."""
    with Path(path).open('xb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def contains_secret(value, keys):
    if isinstance(value, dict):
        return any(contains_secret(part, keys) for pair in value.items() for part in pair)
    if isinstance(value, (list, tuple)):
        return any(contains_secret(part, keys) for part in value)
    return any(key in str(value) for key in keys)


class ImmediateRequestPool(RequestPool):
    """Use the existing atomic per-key admission without waiting for quota.

    In-flight requests are tracked per key (at most per_key_concurrency each) and a request goes to the least loaded, then least
    recently used, key that still has allowance in its current window. A key that is merely at its concurrency limit is waited
    for; admission is refused only when no key can still be admitted (every key cooling down or out of allowance)."""
    def __init__(self, *args, per_key_concurrency=1, **kwargs):
        super().__init__(*args, **kwargs)
        if type(per_key_concurrency) is not int or per_key_concurrency < 1:
            raise ValueError('Invalid per-key concurrency.')
        self.per_key = per_key_concurrency
        self.inflight, self.window_started, self.remaining = {}, {}, {}

    def reserve(self, index, now):
        # Same atomic charge as RequestPool.reserve, keeping each key's window start and remaining allowance for scheduling/telemetry.
        result = budget_reserve(self.budget_path, self.ids[index], aliases=self.aliases[index],
                                legacy_paths=self.legacy_paths, now=now, cap=self.cap, window=self.window)
        self.window_started[index] = result['window_started_epoch']
        self.remaining[index] = result['requests_remaining']
        if not result['admitted']:
            self.cooldown[index] = result['window_started_epoch'] + self.window
        return result['admitted']

    def acquire(self, lane):
        with self.condition:
            while True:
                if self.stopped:
                    raise FECAdmissionRefused('FEC request pool is stopped; remaining coverage is unknown.')
                now = time.time()
                busy_wait = False
                for index in sorted(self.healthy(), key=lambda value:(self.inflight.get(value, 0), self.last_used[value])):
                    if self.cooldown.get(index, 0) > now:
                        continue
                    if self.inflight.get(index, 0) >= self.per_key:
                        busy_wait = True
                        continue
                    if not self.reserve(index, now):
                        continue
                    self.inflight[index] = self.inflight.get(index, 0) + 1
                    self.busy.add(index)
                    self.last_used[index] = time.monotonic()
                    self.peak = max(self.peak, sum(self.inflight.values()))
                    self.assisted.add(str(lane))
                    return index
                if not busy_wait:
                    raise FECAdmissionRefused('Shared FEC quota is unavailable; remaining coverage is unknown.')
                # A key's slot is always released in execute()'s finally, which notifies this condition.
                self.condition.wait(.25)

    def release(self, index):
        with self.condition:
            left = self.inflight.get(index, 1) - 1
            if left > 0:
                self.inflight[index] = left
            else:
                self.inflight.pop(index, None)
                self.busy.discard(index)
            self.completed += 1
            self.condition.notify_all()

    def execute(self, spec, lane):
        result = super().execute(spec, lane)
        index = spec.get('key_index')
        if index is not None:
            headers = {str(name).lower():value for name, value in (result.get('headers') or {}).items()}
            if result.get('status') == 429 or self.depleted(headers):
                # Only this key backs off, until its window resets; every other key keeps going.
                with self.condition:
                    self.cooldown[index] = max(self.cooldown.get(index, 0), self.window_started.get(index, time.time()) + self.window)
                    self.condition.notify_all()
        return result


def public_request(endpoint, params):
    if not isinstance(endpoint, str) or ENDPOINT.fullmatch(endpoint) is None:
        raise ValueError('Unsupported FEC evidence endpoint.')
    if not isinstance(params, dict) or any(not isinstance(key, str) or
            any(word in key.lower() for word in ('key', 'token', 'auth', 'signature')) for key in params):
        raise ValueError('FEC selectors must be public and credential-free.')
    # JSON serialization rejects unexpected objects before quota admission.
    json.dumps(params, allow_nan=False)
    return ORIGIN + endpoint + '?' + urlencode(sorted(params.items()), doseq=True)


class AccountedTransport:
    # fetch() may run on several threads at once: it takes a per-call dict and reports exactly
    # 0 or 1 physical attempt (and its own archive record) there instead of in shared counters.
    supports_concurrent_fetch = True

    def __init__(self, keys, ledger, archive_directory, max_requests, *, legacy_paths=(),
                 opener=None, cap=1000, window=3600, per_key_concurrency=2):
        if type(max_requests) is not int or max_requests < 0:
            raise ValueError('An explicit nonnegative physical HTTP request cap is required.')
        if type(cap) is not int or cap < 1 or type(window) not in (int,float) or not math.isfinite(window) or window <= 0:
            raise ValueError('Invalid shared FEC quota policy.')
        self.keys = list(dict.fromkeys(keys))
        if not self.keys or any(not isinstance(key, str) or not key for key in self.keys):
            raise ValueError('Enabled FEC credentials are unavailable.')
        self.archive = Path(archive_directory)
        self.archive.mkdir(parents=True, exist_ok=False)
        self.maximum, self.http_attempts = max_requests, 0
        self.reserved = 0
        self.records = []
        self.lock = threading.Lock()
        self.opener = opener
        self._local = threading.local()
        ledger = Path(ledger)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        # No inner bad-key reissue: original Client retries must pass this
        # callable and its exact physical-attempt cap again.
        self.pool = ImmediateRequestPool(self.keys, ledger, 1, transport=self._request,
            quarantine=False, cap=cap, window=window, legacy_paths=legacy_paths, per_key_concurrency=per_key_concurrency)

    @classmethod
    def live(cls, archive_directory, max_requests, *, project_root=None, known_legacy_paths=()):
        from fec_key_store import load_keys
        keys = load_keys()  # Standard credential path; values never logged or passed as arguments.
        return cls(keys, budget_path(project_root), archive_directory, max_requests,
                   legacy_paths=legacy_budget_paths(project_root, extra=known_legacy_paths))

    def telemetry(self):
        """Per-key in-flight count and remaining allowance. Keys are shown by their ledger hash only, never by value."""
        pool, now = self.pool, time.time()
        with pool.condition:
            keys = []
            for index in range(len(pool.keys)):
                started = pool.window_started.get(index)
                fresh = started is None or now >= started + pool.window
                keys.append({'key': pool.ids[index].rsplit(':', 1)[-1], 'in_flight': pool.inflight.get(index, 0),
                             'remaining': pool.cap if fresh else pool.remaining.get(index),
                             'cooling_for_s': round(max(0.0, pool.cooldown.get(index, 0) - now), 1),
                             'window_resets_in_s': None if started is None else round(max(0.0, started + pool.window - now), 1)})
        return {'in_flight': sum(entry['in_flight'] for entry in keys), 'keys': keys}

    def _thread_opener(self):
        # Opener handlers hold no per-request state; one per thread removes any doubt. Injected openers are shared.
        if getattr(self._local, 'opener', None) is None:
            self._local.opener = build_opener(NoRedirect())
        return self._local.opener

    def _archive_response(self, spec, status, headers, body, number):
        signature = hashlib.sha256(body).hexdigest()
        name = f'attempt-{number:06d}-{signature[:12]}'
        record = {'schema_version':1, 'source_kind':'fec-api-original-http-body',
            'attempt':number, 'captured_utc':datetime.now(timezone.utc).isoformat(),
            'endpoint':spec['endpoint'], 'query':spec['params'], 'status':status,
            'sha256':signature, 'bytes':len(body), 'original_response_bytes':True,
            'headers':{key:value for key,value in headers.items() if key.lower() in
                ('content-type','etag','last-modified','date','retry-after','x-ratelimit-remaining')}}
        # Even public response headers could echo a credential. Keep only a
        # sanitized status/hash refusal receipt; never redact and call it raw.
        secret_echo = any(key.encode('utf-8') in body for key in self.keys) or contains_secret(record['headers'],self.keys)
        if secret_echo:
            record['headers'] = {}
            record.update(original_response_bytes=False, raw_body_persisted=False, refused_secret_echo=True)
            durable_new(self.archive/(name+'.json'), (json.dumps(record,indent=2)+'\n').encode('utf-8'))
            with self.lock:
                self.records.append(record)
            spec['call']['evidence'] = record
            raise FECArchiveFailed('Response evidence echoed a credential; original bytes were not persisted.')
        path = self.archive/(name+'.body.bin')
        durable_new(path, body)
        record.update(path=str(path),raw_body_persisted=True)
        metadata = self.archive/(name+'.json')
        durable_new(metadata, (json.dumps(record,indent=2)+'\n').encode('utf-8'))
        record['receipt_path'] = str(metadata)
        with self.lock:
            self.records.append(record)
        spec['call']['evidence'] = record
        return record

    def _request(self, spec, key):
        # Called only after inherited shared admission. The reserved slot becomes exactly one
        # physical attempt with a unique number, so concurrent callers cannot race the bound.
        with self.lock:
            self.reserved -= 1
            spec['reserved'] = False
            spec['key_index'] = self.keys.index(key)
            self.http_attempts += 1
            number = self.http_attempts
            spec['call']['attempts'] = 1
            spec['call']['key_index'] = spec['key_index']      # D075: the broker reports which ledger key served the attempt (by its hash only)
        try:
            response = (self.opener or self._thread_opener()).open(Request(spec['url'], headers={'X-Api-Key':key,
                'User-Agent':'Mentat preserved FEC evidence'}), timeout=60)
        except HTTPError as error:
            response = error
        except (OSError, TimeoutError) as error:
            # A connection attempt spends one admitted request; never refund.
            raise URLError('FEC connection attempt failed.') from None
        with response:
            status = response.status
            headers = dict(response.headers)
            body = response.read()
        if status in (401, 403):
            # A credential refusal is a global stop. Stopping the pool HERE, while this call still holds its key slot, means the release that follows cannot
            # admit a caller that is already waiting: it is refused instead of sending a request (M1).
            self.pool.stop()
        try:
            record = self._archive_response(spec, status, headers, body, number)
        except FECArchiveFailed:
            self.pool.stop()      # so is a failed or credential-echoing archive
            raise
        except (OSError, ValueError, TypeError):
            self.pool.stop()
            raise FECArchiveFailed('Original FEC response evidence could not be stored.') from None
        return {'status':status,'headers':headers,'body':body,'evidence':record}

    def fetch_raw(self, endpoint, params, *, call=None):
        """The original bytes of one admitted attempt, unparsed and with no status check: (status, headers, body, evidence).

        Admission, the physical cap, the archive and the credential-echo refusal are exactly fetch()'s (fetch() is this plus the status check and the parse),
        so a non-200 answer is a charged, archived attempt returned like any other. Refusals raise as in fetch(). The D075 host broker calls this."""
        return self._fetch(endpoint, params, call)[1:]

    def fetch(self, endpoint, params, *, exact_decimal=False, call=None):
        url, status, headers, body, evidence = self._fetch(endpoint, params, call)
        if status != 200:
            raise HTTPError(url, status, 'FEC evidence response was not successful.', headers, None)
        options = {'parse_float':Decimal} if exact_decimal else {}
        return json.loads(body, **options), evidence

    def _fetch(self, endpoint, params, call):
        call = {} if call is None else call
        url = public_request(endpoint, params)
        if contains_secret(params,self.keys) or any(key in url for key in self.keys):
            raise ValueError('Credentials must not appear in public FEC selectors.')
        with self.lock:
            # spent + reserved never exceeds the cap, so the physical bound is exact under contention.
            if self.http_attempts + self.reserved >= self.maximum:
                raise FECAdmissionRefused('Physical HTTP request cap reached; remaining coverage is unknown.')
            self.reserved += 1
        spec = {'url':url, 'endpoint':endpoint, 'params':dict(params), 'call':call, 'reserved':True}
        try:
            response = self.pool.execute(spec, 'preserved-evidence')
        finally:
            if spec['reserved']:
                # Admission was refused (or failed): the slot never became a physical attempt.
                with self.lock:
                    self.reserved -= 1
        return url, response['status'], response['headers'], response['body'], response['evidence']

    def __call__(self, endpoint, params):
        return self.fetch(endpoint, params)[0]

    def close(self):
        self.pool.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
