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

from api_request_budget import budget_path, legacy_budget_paths
from api_request_pool import NoRedirect, RequestPool

ORIGIN = 'https://api.open.fec.gov/v1/'
ENDPOINT = re.compile(r'(?:schedules/schedule_(?:[abef]|h4)/(?:[0-9]+/)?|'
                      r'committee/C[0-9]{8}/totals/|communication_costs/|electioneering/)')


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
    """Use the existing atomic per-key admission without waiting for quota."""
    def acquire(self, lane):
        with self.condition:
            if self.stopped:
                raise FECAdmissionRefused('FEC request pool is stopped; remaining coverage is unknown.')
            now = time.time()
            for index in sorted(self.healthy(), key=lambda value:self.last_used[value]):
                if index in self.busy or self.cooldown.get(index, 0) > now:
                    continue
                if not self.reserve(index, now):
                    continue
                self.busy.add(index)
                self.last_used[index] = time.monotonic()
                self.peak = max(self.peak, len(self.busy))
                self.assisted.add(str(lane))
                return index
        raise FECAdmissionRefused('Shared FEC quota is unavailable; remaining coverage is unknown.')


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
    def __init__(self, keys, ledger, archive_directory, max_requests, *, legacy_paths=(),
                 opener=None, cap=1000, window=3600):
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
        self.records = []
        self.lock = threading.Lock()
        self.opener = opener or build_opener(NoRedirect())
        ledger = Path(ledger)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        # No inner bad-key reissue: original Client retries must pass this
        # callable and its exact physical-attempt cap again.
        self.pool = ImmediateRequestPool(self.keys, ledger, 1, transport=self._request,
            quarantine=False, cap=cap, window=window, legacy_paths=legacy_paths)

    @classmethod
    def live(cls, archive_directory, max_requests, *, project_root=None, known_legacy_paths=()):
        from fec_key_store import load_keys
        keys = load_keys()  # Standard credential path; values never logged or passed as arguments.
        return cls(keys, budget_path(project_root), archive_directory, max_requests,
                   legacy_paths=legacy_budget_paths(project_root, extra=known_legacy_paths))

    def _archive_response(self, spec, status, headers, body):
        signature = hashlib.sha256(body).hexdigest()
        name = f'attempt-{self.http_attempts:06d}-{signature[:12]}'
        record = {'schema_version':1, 'source_kind':'fec-api-original-http-body',
            'attempt':self.http_attempts, 'captured_utc':datetime.now(timezone.utc).isoformat(),
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
            self.records.append(record)
            raise FECArchiveFailed('Response evidence echoed a credential; original bytes were not persisted.')
        path = self.archive/(name+'.body.bin')
        durable_new(path, body)
        record.update(path=str(path),raw_body_persisted=True)
        metadata = self.archive/(name+'.json')
        durable_new(metadata, (json.dumps(record,indent=2)+'\n').encode('utf-8'))
        record['receipt_path'] = str(metadata)
        self.records.append(record)
        return record

    def _request(self, spec, key):
        # Called only after inherited shared admission. The outer lock prevents
        # concurrent callers from racing the physical-attempt bound.
        self.http_attempts += 1
        try:
            response = self.opener.open(Request(spec['url'], headers={'X-Api-Key':key,
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
        try:
            record = self._archive_response(spec, status, headers, body)
        except FECArchiveFailed:
            raise
        except (OSError, ValueError, TypeError):
            raise FECArchiveFailed('Original FEC response evidence could not be stored.') from None
        return {'status':status,'headers':headers,'body':body,'evidence':record}

    def fetch(self, endpoint, params, *, exact_decimal=False):
        url = public_request(endpoint, params)
        if contains_secret(params,self.keys) or any(key in url for key in self.keys):
            raise ValueError('Credentials must not appear in public FEC selectors.')
        with self.lock:
            if self.http_attempts >= self.maximum:
                raise FECAdmissionRefused('Physical HTTP request cap reached; remaining coverage is unknown.')
            response = self.pool.execute({'url':url,'endpoint':endpoint,'params':dict(params)},'preserved-evidence')
            if response['status'] != 200:
                raise HTTPError(url, response['status'], 'FEC evidence response was not successful.', response['headers'], None)
            options = {'parse_float':Decimal} if exact_decimal else {}
            return json.loads(response['body'], **options), response['evidence']

    def __call__(self, endpoint, params):
        return self.fetch(endpoint, params)[0]

    def close(self):
        self.pool.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
