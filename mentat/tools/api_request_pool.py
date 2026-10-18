"""Shared HTTP workers. R coordinators remain the sole owners of their databases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import sqlite3
import sys
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError, URLError
from email.utils import parsedate_to_datetime
from api_request_budget import budget_aliases, budget_id, ensure_schema, legacy_budget_paths, reserve

ENDPOINT = 'https://api.open.fec.gov/v1/schedules/schedule_a/'
# A 403 carrying one of these codes condemns the key itself, never the query,
# so the pool must retire that key instead of failing the collection task.
INVALID_KEY_CODES = {'API_KEY_INVALID','API_KEY_DISABLED','API_KEY_UNAUTHORIZED','API_KEY_MISSING','API_KEY_UNVERIFIED'}
POOL_EXHAUSTED_BODY = json.dumps({'error':{'code':'API_KEY_POOL_EXHAUSTED','message':'Every configured FEC API key was quarantined as invalid.'}})


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(spec, key):
    started = time.monotonic()
    url = ENDPOINT + '?' + urlencode(spec['params'], doseq=True)
    try:
        response = build_opener(NoRedirect()).open(Request(url, headers={'X-Api-Key':key}), timeout=120)
    except HTTPError as error:
        response = error
    except (OSError, ValueError):
        return {'status':0, 'headers':{}, 'body':'', 'error':'FEC connection failed', 'elapsed_seconds':time.monotonic()-started}
    with response:
        return {'status':response.status, 'headers':dict(response.headers),
                'body':response.read().decode('utf-8'), 'elapsed_seconds':time.monotonic()-started}


def invalid_key_code(result):
    """Return the FEC error code when a 403 blames the key itself, else None."""
    if result.get('status')!=403:return None
    body=result.get('body') or ''
    try:code=str(json.loads(body).get('error',{}).get('code',''))
    except (ValueError,TypeError,AttributeError):code=''
    if code in INVALID_KEY_CODES:return code
    return 'API_KEY_INVALID' if 'API_KEY_INVALID' in body else None


def cooldown_seconds(headers, fallback=60):
    """Retry-After in seconds; accepts the numeric and the HTTP-date form."""
    value=headers.get('retry-after')
    if value is None:return fallback
    try:return max(1,float(value))
    except (ValueError,TypeError):
        try:return max(1,parsedate_to_datetime(value).timestamp()-time.time())
        except (ValueError,TypeError,AttributeError,OverflowError):return fallback


class RequestPool:
    def __init__(self, keys, budget_path, workers, *, transport=request, cap=1000, window=3600, quarantine=True, legacy_paths=()):
        self.keys = list(dict.fromkeys(keys))
        self.ids = [budget_id(key) for key in self.keys]
        self.aliases = [budget_aliases(key) for key in self.keys]
        self.legacy_paths = tuple(legacy_paths)
        self.budget_path = str(budget_path)
        self.cap, self.window = cap, window
        self.transport = transport
        self.condition = threading.Condition()
        self.busy, self.cooldown = set(), {}
        self.quarantine = quarantine
        self.quarantined = {}
        self.reissued = 0
        self.requested = max(1,int(workers))
        self.limit = max(1,min(self.requested,len(self.keys)))
        self.stopped = False
        self.waiting = self.completed = 0
        self.peak = 0
        self.assisted = set()
        self.last_used = [0]*len(self.keys)
        self.executor = ThreadPoolExecutor(max_workers=max(1,len(self.keys)), thread_name_prefix='mentat-fetch')
        with closing(sqlite3.connect(self.budget_path)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            ensure_schema(db)
        self.server = None
        self.token = secrets.token_urlsafe(32)

    def healthy(self):
        """Key indexes the API has not rejected. Quarantine is one-way per run."""
        return [index for index in range(len(self.keys)) if index not in self.quarantined]

    def snapshot(self):
        with self.condition:
            return {'active_workers':len(self.busy), 'requested_workers':self.limit,
                    'queued_requests':self.waiting, 'completed_requests':self.completed,
                    'peak_active_workers':self.peak, 'assisted_lanes':sorted(self.assisted),
                    'quarantined_keys':[self.ids[i][-12:] for i in sorted(self.quarantined)],
                    'healthy_keys':len(self.healthy()), 'reissued_requests':self.reissued,
                    'scheduling':'shared_request_pool'}

    def rescale(self):
        # Caller holds self.condition. Width follows the keys that still work.
        self.limit=max(1,min(self.requested,max(1,len(self.healthy()))))

    def resize(self, count):
        with self.condition:
            self.requested=max(1,int(count));self.rescale();self.condition.notify_all()

    def condemn(self, index, code):
        """Retire a rejected key. One stderr line per key, redacted to its id."""
        with self.condition:
            first=index not in self.quarantined
            self.quarantined[index]=code
            self.rescale();self.condition.notify_all()
        if first:print(f'[request-pool] quarantined key {self.ids[index][-12:]}: {code}',file=sys.stderr,flush=True)

    def stop(self):
        with self.condition:
            self.stopped=True;self.condition.notify_all()

    def reserve(self, index, now):
        # Imports and admission share one durable write lease before HTTP.
        result = reserve(self.budget_path, self.ids[index], aliases=self.aliases[index],
                         legacy_paths=self.legacy_paths, now=now, cap=self.cap, window=self.window)
        if not result['admitted']:
            self.cooldown[index] = result['window_started_epoch'] + self.window
        return result['admitted']

    def acquire(self, lane):
        """Reserve a healthy key, or None once the pool stops or runs out of keys."""
        index=None
        with self.condition:
            self.waiting+=1
            try:
                while index is None:
                    if self.stopped:return None
                    healthy=self.healthy()
                    if not healthy:return None
                    now=time.time()
                    if len(self.busy)<self.limit:
                        for candidate in sorted(healthy,key=lambda i:self.last_used[i]):
                            if candidate not in self.busy and self.cooldown.get(candidate,0)<=now and self.reserve(candidate,now):
                                index=candidate;break
                    if index is None:self.condition.wait(.25)
                self.busy.add(index);self.last_used[index]=time.monotonic()
                self.peak=max(self.peak,len(self.busy));self.assisted.add(str(lane))
                return index
            finally:self.waiting-=1

    def release(self, index):
        with self.condition:
            self.busy.discard(index);self.completed+=1;self.condition.notify_all()

    def depleted(self, headers):
        """True when the API reports no requests left in this key's window."""
        remaining=headers.get('x-ratelimit-remaining')
        if remaining is None:return False
        try:return int(str(remaining).strip())<=0
        except (ValueError,TypeError):return False

    def execute(self, spec, lane):
        # One bad key must cost one request, not the whole collection task: the
        # 403 retires that key and the same spec is re-issued on a healthy one.
        result=None
        for attempt in range(len(self.keys)):
            index=self.acquire(lane)
            if index is None:
                if self.stopped:return {'status':429,'headers':{'Retry-After':'1'},'body':'','pool_stopped':True}
                if result is not None:return result
                return {'status':403,'headers':{},'body':POOL_EXHAUSTED_BODY,'pool_exhausted':True}
            if attempt:
                with self.condition:self.reissued+=1
            code=None
            try:
                result=self.transport(spec,self.keys[index])
                headers={str(k).lower():v for k,v in (result.get('headers') or {}).items()}
                if result.get('status')==429 or self.depleted(headers):
                    with self.condition:self.cooldown[index]=time.time()+cooldown_seconds(headers)
                if self.quarantine:
                    code=invalid_key_code(result)
                    if code:self.condemn(index,code)
            finally:
                self.release(index)
            if code is None:return result
        return result

    def batch(self, specs, lane):
        if not isinstance(specs,list) or not 1<=len(specs)<=128:raise ValueError('Invalid batch size')
        for spec in specs:
            if spec.get('url')!=ENDPOINT or not isinstance(spec.get('params'),dict):raise ValueError('Only FEC Schedule A requests are allowed')
            if any('key' in k.lower() for k in spec['params']):raise ValueError('Credentials must not appear in request parameters')
        futures=[self.executor.submit(self.execute,spec,lane) for spec in specs]
        return [f.result() for f in futures]

    def start(self):
        pool=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                if self.path!='/batch' or self.headers.get('Origin') or not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+pool.token):
                    self.send_error(403);return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=2_000_000:raise ValueError('Invalid payload size')
                    data=json.loads(self.rfile.read(length))
                    result={'responses':pool.batch(data['specs'],data.get('lane','unknown'))}
                    payload=json.dumps(result).encode()
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                except (ValueError,KeyError,TypeError):self.send_error(400,'Invalid batch')
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        return f'http://127.0.0.1:{self.server.server_port}/batch'

    def close(self):
        self.stop()
        if self.server:self.server.shutdown();self.server.server_close();self.thread.join()
        self.executor.shutdown(wait=True)


class RemoteRequestPool:
    """Read-only descriptor for one Windows-owned pool; never opens a ledger."""
    def __init__(self, path, keys):
        from pathlib import Path
        import re
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        expected = [budget_id(key) for key in dict.fromkeys(keys)]
        match = re.fullmatch(r'http://127[.]0[.]0[.]1:([0-9]+)/batch', str(value.get('url', '')))
        token = value.get('token')
        if (value.get('schema') != 'mentat-request-pool-v1' or value.get('scope') != 'schedule_a_batch_only'
                or value.get('endpoint') != ENDPOINT or not match or not 0 < int(match[1]) <= 65535
                or not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,}', token)
                or value.get('key_ids') != expected or type(value.get('width')) is not int
                or value['width'] != len(expected) or not expected):
            raise ValueError('Host request-pool descriptor or key-slot order is invalid; collection refused.')
        self.keys = list(dict.fromkeys(keys))
        self.url = value['url'].replace('127.0.0.1', 'host.docker.internal', 1)
        self.token = token
        self.width = value['width']

    def start(self):
        return self.url

    def snapshot(self):
        return dict(active_workers=None, requested_workers=self.width, queued_requests=None,
                    completed_requests=None, peak_active_workers=None, assisted_lanes=None,
                    quarantined_keys=None, healthy_keys=None, reissued_requests=None,
                    scheduling='windows_host_request_pool', counters_available=False)

    def stop(self):
        # The container owns dispatch only. Owner stopfile/PID controls the host.
        return None

    def close(self):
        return None


# --- D075: the key-free client of the host evidence broker (scope fec_spending_evidence_v2) -----------------------------------------------------
# The Windows broker (api_host_pool.py --scope spending-evidence-v2) holds the keys, the shared quota ledger and the archive; a container holds only this
# client and a per-run bearer token bound to one run id, one family set and one physical cap. The wire is JSON over loopback: /v2/open, /v2/fetch, /v2/close.
EVIDENCE_SCHEMA = 'mentat-evidence-pool-v2'
EVIDENCE_SCOPE = 'fec_spending_evidence_v2'
EVIDENCE_ORIGIN = 'https://api.open.fec.gov/v1/'
FAMILY_ENDPOINTS = {'A': 'schedules/schedule_a/', 'B': 'schedules/schedule_b/', 'E': 'schedules/schedule_e/', 'F': 'schedules/schedule_f/', 'H4': 'schedules/schedule_h4/',
                    'totals': 'committee/C[0-9]{8}/totals/', 'candidate_committees': 'candidate/[HSP][A-Z0-9]{8}/committees/'}
ALWAYS_FAMILIES = ('B', 'E')
# Why a request was refused before any attempt; the client re-raises FECAdmissionRefused with the SAME text the local transport uses, so the collector's
# classification (cap, pool stopped, quota) and its recorded failure rows are identical in both runtimes.
REFUSALS = {'cap': 'Physical HTTP request cap reached; remaining coverage is unknown.',
            'quota': 'Shared FEC quota is unavailable; remaining coverage is unknown.',
            'stopped': 'FEC request pool is stopped; remaining coverage is unknown.'}
KEY_LEVELS = (None, 'throttled', 'depleted', 'invalid_key')


class BrokerUnavailable(RuntimeError):
    """The evidence broker could not be reached, refused this process, or its answer was lost. An attempt may have been charged and archived on the
    host without this process knowing; the broker journal holds the truth. A global stop, never retried (a retry could charge twice)."""


def evidence_descriptor(value):
    """Validate a descriptor (a dict, or the path of its JSON file) and return it. It never carries a key value; ValueError on anything unexpected."""
    from pathlib import Path
    import re
    refused = ValueError('Host evidence-pool descriptor is invalid; collection refused.')
    try:
        document = value if isinstance(value, dict) else json.loads(Path(value).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise refused from None
    if not isinstance(document, dict):
        raise refused
    match = re.fullmatch(r'http://127[.]0[.]0[.]1:([0-9]+)/v2', str(document.get('url', '')))
    families = document.get('families')
    names = [item.get('family') for item in families if isinstance(item, dict)] if isinstance(families, list) else []
    ok = (document.get('schema') == EVIDENCE_SCHEMA and document.get('scope') == EVIDENCE_SCOPE and document.get('origin') == EVIDENCE_ORIGIN
          and match is not None and 0 < int(match[1]) <= 65535 and document.get('reach') in ('host.docker.internal', 'loopback')
          and isinstance(document.get('token'), str) and re.fullmatch(r'[A-Za-z0-9_-]{32,}', document['token']) is not None
          and isinstance(document.get('run_id'), str) and re.fullmatch(r'[0-9a-f]{32}', document['run_id']) is not None
          and all(type(document.get(name)) is int and document[name] >= 1 for name in ('max_attempts', 'width', 'per_key_concurrency'))
          and isinstance(document.get('key_ids'), list) and len(document['key_ids']) == document['width'] and document['width'] >= 1
          and isinstance(families, list) and len(names) == len(families) and len(set(names)) == len(names)
          and all(name in FAMILY_ENDPOINTS and families[index].get('endpoint_regex') == FAMILY_ENDPOINTS[name] for index, name in enumerate(names))
          and all(name in names for name in ALWAYS_FAMILIES)
          and all(isinstance(document.get(name), str) and re.fullmatch(r'[0-9a-f]{64}', document[name]) for name in ('adapter_sha256', 'contracts_sha256'))
          and isinstance(document.get('stored_root'), str) and document['stored_root'])
    if not ok:
        raise refused
    return document


class _ClientPool:
    """What the collector's writer calls after a global stop (Frontier.run -> transport.pool.stop()): later fetches of this process are refused locally."""
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class RemoteEvidenceTransport:
    """The transport interface of fec_accounted_client.AccountedTransport (fetch, http_attempts, maximum, records, telemetry, pool.stop), served by the broker.

    fetch() returns (parsed data, evidence) for a 200 and raises HTTPError for any other status, exactly as the local transport does; the evidence is the
    D067 metadata record the broker wrote beside the original bytes on the host (paths in the stored Windows spelling). It never retries: a request whose
    answer is lost is a BrokerUnavailable (attempts unknown), not a second request."""
    supports_concurrent_fetch = True

    def __init__(self, descriptor, *, run_id, max_requests, workers=1, post=None, descriptor_sha256=None, tools=None):
        from pathlib import Path
        self.descriptor = evidence_descriptor(descriptor)
        if run_id != self.descriptor['run_id']:
            raise ValueError('This descriptor belongs to another collector run; collection refused.')
        if type(max_requests) is not int or not 0 <= max_requests <= self.descriptor['max_attempts']:
            raise ValueError('The physical request cap exceeds the cap this run token carries; collection refused.')
        if type(workers) is not int or not 1 <= workers <= self.descriptor['width'] * self.descriptor['per_key_concurrency']:
            raise ValueError('The worker count exceeds the broker width (keys x per-key concurrency); collection refused.')
        tools = Path(tools) if tools is not None else Path(__file__).resolve().parent
        for name, field in (('fec_accounted_client.py', 'adapter_sha256'), ('spending_endpoint_contracts.py', 'contracts_sha256')):
            if hashlib.sha256((tools / name).read_bytes()).hexdigest() != self.descriptor[field]:
                raise ValueError('The mounted ' + name + ' differs from the one the broker imported; the tree changed between broker start and now. Collection refused.')
        import re
        self.run_id, self.maximum, self.workers = run_id, max_requests, workers
        self.admitted = [re.compile(item['endpoint_regex']) for item in self.descriptor['families']]
        self.families = [item['family'] for item in self.descriptor['families']]
        self.descriptor_sha256 = descriptor_sha256
        self.http_attempts, self.reserved, self.inflight, self.seq = 0, 0, 0, 0
        self.records, self.key_levels, self.broker_pool = [], {}, {}
        self.lock = threading.Lock()
        self.pool = _ClientPool()
        self.archive_stored = None
        self._post = post if post is not None else self.default_post()
        self._session, self._opened, self._closed = None, False, False

    @classmethod
    def from_environment(cls, *, run_id, max_requests, workers=1, environment=None, post=None):
        import os
        from pathlib import Path
        environment = os.environ if environment is None else environment
        path = environment.get('MENTAT_REQUEST_POOL_CONFIG', '')
        if not path:
            raise ValueError('MENTAT_REQUEST_POOL_CONFIG names no host evidence-pool descriptor; collection refused.')
        raw = Path(path).read_bytes()
        try:
            document = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise ValueError('Host evidence-pool descriptor is invalid; collection refused.') from None
        return cls(document, run_id=run_id, max_requests=max_requests, workers=workers, post=post, descriptor_sha256=hashlib.sha256(raw).hexdigest())

    def default_post(self):
        """post(operation, payload) -> (http status, parsed JSON) over loopback to the broker; never a proxy, never a redirect, errors as BrokerUnavailable."""
        import http.client
        base = self.descriptor['url'].replace('127.0.0.1', 'host.docker.internal', 1) if self.descriptor['reach'] == 'host.docker.internal' else self.descriptor['url']
        token = self.descriptor['token']
        opener = build_opener(ProxyHandler({}), NoRedirect())

        def post(operation, payload):
            request = Request(base + '/' + operation, data=json.dumps(payload, allow_nan=False).encode(), method='POST',
                              headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            try:
                with opener.open(request, timeout=240) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                try:
                    return error.code, json.loads(error.read())
                except (OSError, ValueError):
                    return error.code, {}
            except (OSError, ValueError, http.client.HTTPException):
                raise BrokerUnavailable('The evidence broker could not be reached or its answer was lost; the attempt count is unknown.') from None
        return post

    def _call(self, operation, payload):
        try:
            return self._post(operation, payload)
        except BrokerUnavailable:
            raise
        except Exception:
            raise BrokerUnavailable('The evidence broker could not be reached or its answer was lost; the attempt count is unknown.') from None

    def session(self, start_path, collector_sha256):
        """Name the START record the broker journals at /v2/open; the context manager then opens on entry and closes on exit."""
        self._session = (start_path, collector_sha256)
        return self

    def open(self, start_path, collector_sha256):
        from pathlib import Path
        status, answer = self._call('open', {'run_id': self.run_id, 'start_sha256': hashlib.sha256(Path(start_path).read_bytes()).hexdigest(), 'collector_sha256': collector_sha256})
        if status != 200 or not isinstance(answer, dict) or answer.get('opened') is not True or not isinstance(answer.get('archive_directory_stored'), str):
            raise BrokerUnavailable('The evidence broker refused to open this run (HTTP ' + str(status) + ').')
        self.archive_stored, self._opened = answer['archive_directory_stored'], True

    def close(self):
        if self._opened and not self._closed:
            self._closed = True
            try:
                self._call('close', {'run_id': self.run_id})
            except BrokerUnavailable:
                pass            # the broker is already gone; its journal and receipt are the record

    def __enter__(self):
        if self._session is not None and not self._opened:
            self.open(*self._session)
        return self

    def __exit__(self, *_):
        self.close()

    def telemetry(self):
        return {'in_flight': self.inflight, 'broker': dict(self.broker_pool)}

    def receipt_info(self):
        return {'kind': 'broker', 'scope': EVIDENCE_SCOPE, 'run_id': self.run_id, 'families': list(self.families), 'max_attempts': self.descriptor['max_attempts'],
                'width': self.descriptor['width'], 'per_key_concurrency': self.descriptor['per_key_concurrency'], 'descriptor_sha256': self.descriptor_sha256,
                'adapter_sha256': self.descriptor['adapter_sha256'], 'contracts_sha256': self.descriptor['contracts_sha256'], 'stored_root': self.descriptor['stored_root'],
                'archive_directory': self.archive_stored, 'key_levels': dict(sorted((str(k), v) for k, v in self.key_levels.items())), 'broker_pool_last': dict(self.broker_pool),
                'keys_held_by_this_process': 0}

    def fetch(self, endpoint, params, *, exact_decimal=False, call=None):
        from decimal import Decimal
        from fec_accounted_client import FECAdmissionRefused, FECArchiveFailed, public_request
        call = {} if call is None else call
        url = public_request(endpoint, params)
        if not any(pattern.fullmatch(endpoint) for pattern in self.admitted):
            raise ValueError('This FEC evidence endpoint is outside the families this run admits.')
        with self.lock:
            if self.pool.stopped:
                raise FECAdmissionRefused(REFUSALS['stopped'])
            if self.http_attempts + self.reserved >= self.maximum:
                raise FECAdmissionRefused(REFUSALS['cap'])
            self.reserved += 1
            self.inflight += 1
            self.seq += 1
            seq = self.seq
        settled = False
        try:
            status, answer = self._call('fetch', {'seq': seq, 'endpoint': endpoint, 'params': dict(params)})
            if status != 200 or not isinstance(answer, dict) or answer.get('attempts') not in (0, 1):
                raise BrokerUnavailable('The evidence broker answered HTTP ' + str(status) + ' without a usable result; the attempt count is unknown.')
            if isinstance(answer.get('pool'), dict):
                self.broker_pool = answer['pool']
            if answer['attempts'] == 0:
                if answer.get('refusal') not in REFUSALS:
                    raise BrokerUnavailable('The evidence broker refused a request for an unknown reason (' + str(answer.get('refusal')) + ').')
                raise FECAdmissionRefused(REFUSALS[answer['refusal']])
            with self.lock:
                self.reserved -= 1
                self.http_attempts += 1
                settled = True
                if answer.get('key_level') in KEY_LEVELS[1:]:
                    self.key_levels[answer['key_level']] = self.key_levels.get(answer['key_level'], 0) + 1
            call['attempts'] = 1
            if answer.get('error') == 'connection':
                raise URLError('FEC connection attempt failed.')
            if answer.get('refusal') == 'archive_failed':
                raise FECArchiveFailed('Original FEC response evidence could not be stored.')
            evidence = answer.get('evidence')
            if not isinstance(evidence, dict):
                raise BrokerUnavailable('The evidence broker answered an attempt without its evidence record.')
            if answer.get('refusal') == 'secret_echo':
                with self.lock:
                    self.records.append(evidence)
                call['evidence'] = evidence
                raise FECArchiveFailed('Response evidence echoed a credential; original bytes were not persisted.')
            import base64
            try:
                body = base64.b64decode(answer['body_b64'], validate=True)
            except (KeyError, TypeError, ValueError):
                raise FECArchiveFailed('The evidence broker returned no decodable body for an archived attempt.') from None
            digest = hashlib.sha256(body).hexdigest()
            if not (digest == answer.get('sha256') == evidence.get('sha256') and len(body) == answer.get('bytes') == evidence.get('bytes')
                    and answer.get('status') == evidence.get('status') and evidence.get('original_response_bytes') is True):
                raise FECArchiveFailed('The evidence broker returned bytes that do not match the evidence it attested; they were not admitted.')
            with self.lock:
                self.records.append(evidence)
            call['evidence'] = evidence
            if answer['status'] != 200:
                raise HTTPError(url, answer['status'], 'FEC evidence response was not successful.', answer.get('headers') or {}, None)
            return json.loads(body, **({'parse_float': Decimal} if exact_decimal else {})), evidence
        finally:
            with self.lock:
                self.inflight -= 1
                if not settled:
                    self.reserved -= 1

    def __call__(self, endpoint, params):
        return self.fetch(endpoint, params)[0]


def main():
    import argparse
    import ctypes
    from pathlib import Path
    from fec_key_store import load_keys
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-directory',type=Path,required=True)
    parser.add_argument('--budget-database',type=Path,required=True)
    parser.add_argument('--workers',type=int,required=True)
    parser.add_argument('--watch-pid',type=int,required=True)
    args=parser.parse_args()
    def alive():
        kernel=ctypes.windll.kernel32
        kernel.OpenProcess.restype=ctypes.c_void_p
        handle=kernel.OpenProcess(0x1000,False,args.watch_pid)
        if not handle:return False
        code=ctypes.c_ulong()
        try:return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(code))) and code.value==259
        finally:kernel.CloseHandle(ctypes.c_void_p(handle))
    keys=load_keys()
    if not keys:raise ValueError('No enabled API keys available.')
    config=args.run_directory/'request-pool.json'
    if config.exists():raise ValueError('A request pool configuration already exists; verify its owner before replacing it.')
    def write(path,value):
        temporary=path.with_suffix('.pending')
        temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(path)
    pool=RequestPool(keys,args.budget_database,args.workers,legacy_paths=legacy_budget_paths())
    try:
        url=pool.start()
        write(config,{'schema':'mentat-request-pool-v1','url':url,'token':pool.token,'width':len(keys)})
        print('Shared request pool ready',flush=True)
        while alive():
            try:control=json.loads((args.run_directory/'control.json').read_text(encoding='utf-8-sig'))
            except (OSError,ValueError):control={}
            if control.get('stop'):pool.stop()
            if isinstance(control.get('desired_workers'),int) and control['desired_workers']>0:pool.resize(control['desired_workers'])
            # snapshot() carries quarantined_keys/healthy_keys/reissued_requests.
            write(args.run_directory/'request-workers.json',{**pool.snapshot(),'updated_at_epoch':time.time()})
            time.sleep(.5)
    finally:
        pool.close()
        config.unlink(missing_ok=True)
        write(args.run_directory/'request-workers.json',{**pool.snapshot(),'stopped':True,'updated_at_epoch':time.time()})


if __name__=='__main__':main()
