"""Windows-owned loopback request pool for one collecting container.

Only the exact descriptor is mounted. The enclosing existing secret directory
contains owner control and remains private; keys come from the standard loader.

D075 adds a second scope, `--scope spending-evidence-v2` (descriptor schema mentat-evidence-pool-v2, scope fec_spending_evidence_v2): one broker per collector run
that runs the D067/R1b accounted transport HERE, on the host, so original bytes are archived and fsynced before they cross to a container that holds no key.
`api_host_pool.py quarantine` moves the unpaired or truncated files of a crashed run aside before any recovery. The default scope is unchanged.
"""
from pathlib import Path
import argparse,base64,collections,hashlib,json,os,re,secrets,sys,threading,time
import http.client
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.error import URLError
sys.path.append(str(Path(__file__).resolve().parents[1]/'app'))
import proclock
from api_request_budget import budget_id,budget_path,legacy_budget_paths
from api_request_pool import (RequestPool,ENDPOINT,EVIDENCE_SCHEMA,EVIDENCE_SCOPE,EVIDENCE_ORIGIN,FAMILY_ENDPOINTS,ALWAYS_FAMILIES,invalid_key_code)
from fec_accounted_client import AccountedTransport,FECAdmissionRefused,FECArchiveFailed,contains_secret,public_request
from fec_key_store import load_keys


def host_job_database(value, project_root, environment=None):
    environment=os.environ if environment is None else environment
    mappings={'/collect':environment.get('COLLECT_HOST',''),'/mentat':str(Path(project_root).parent)}
    for prefix,host in mappings.items():
        if host and str(value).startswith(prefix+'/'):
            root=Path(host).resolve();target=root.joinpath(str(value)[len(prefix)+1:]).resolve()
            if not target.is_relative_to(root) or not target.is_file():break
            return target
    raise ValueError('Job source database cannot be mapped to an exact existing host path.')


def descriptor(pool,url):
    value=dict(schema='mentat-request-pool-v1',scope='schedule_a_batch_only',endpoint=ENDPOINT,
               url=url,token=pool.token,width=len(pool.keys),key_ids=pool.ids)
    encoded=json.dumps(value)
    if any(key and key in encoded for key in pool.keys):
        raise ValueError('Host pool descriptor refused credential content.')
    return encoded


def serve(directory,watch_pid,source_database,project_root,*,key_loader=load_keys,
          owner_started=proclock.process_started,owner_alive=proclock.alive,transport=None,cap=1000):
    directory=Path(directory)
    if not directory.is_dir() or not (directory/'fec_api_keys').is_file():
        raise ValueError('An existing owner-only compute secret directory is required.')
    started=owner_started(watch_pid)
    if not isinstance(started,int) or started<=0:
        raise ValueError('Wrapper process creation identity is unavailable.')
    keys=key_loader()
    if not keys:raise ValueError('No enabled API keys are available.')
    shared=budget_path(project_root);shared.parent.mkdir(parents=True,exist_ok=True)
    kwargs={} if transport is None else {'transport':transport,'quarantine':False}
    pool=RequestPool(keys,shared,len(keys),legacy_paths=legacy_budget_paths(project_root,extra=(source_database,)),cap=cap,**kwargs)
    config=directory/'request-pool.json'
    if config.exists():
        pool.close();raise ValueError('A host pool descriptor already exists.')
    try:
        encoded=descriptor(pool,pool.start())
        temporary=directory/'request-pool.pending'
        temporary.write_text(encoded,encoding='utf-8');temporary.replace(config)
        while not (directory/'owner.stop').exists() and owner_alive(watch_pid,started):time.sleep(.2)
    finally:
        pool.close()
        config.unlink(missing_ok=True)


# --- D075 scope fec_spending_evidence_v2: the host evidence broker ---------------------------------------------------------------------------
# One broker process per collector run. It holds the FEC keys, the shared quota ledger and the archive; the container holds only a key-free descriptor with
# a per-run bearer token bound to one run id, one family set and one physical cap. Every /v2/fetch is ONE attempt of the D067/R1b AccountedTransport run here:
# charged to the shared ledger before HTTP, original bytes archived and fsynced on the host before they cross to the container, then journalled. Per-key
# concurrency and the 429 cooldown are the transport's own (R1b): a 429 cools only the key that received it, there is no pacing, and a refusal (cap,
# quota, stopped) is an answer with attempts 0, never an error.
SUBSET = ('content-type', 'etag', 'last-modified', 'date', 'retry-after', 'x-ratelimit-remaining')   # the headers D067 archives
MAX_BODY = 65536
PAIR_FIELDS = ('sha256', 'bytes', 'original_response_bytes', 'raw_body_persisted', 'status', 'attempt', 'endpoint', 'query', 'path')


def parse_families(text):
    """'B,E,totals' -> the canonical list of family names. B and E are always admitted; A, F, H4, totals and candidate_committees only when named."""
    names = [item.strip() for item in str(text or '').split(',') if item.strip()]
    if len(set(names)) != len(names) or any(name not in FAMILY_ENDPOINTS for name in names):
        raise ValueError('Unknown or repeated endpoint family.')
    return [name for name in FAMILY_ENDPOINTS if name in names or name in ALWAYS_FAMILIES]


def evidence_descriptor_text(broker, url, reach='host.docker.internal'):
    value = dict(schema=EVIDENCE_SCHEMA, scope=EVIDENCE_SCOPE, url=url, reach=reach, token=broker.token, run_id=broker.run_id, max_attempts=broker.max_attempts,
                 width=len(broker.keys), per_key_concurrency=broker.per_key_concurrency, key_ids=broker.key_ids,
                 families=[{'family': name, 'endpoint_regex': FAMILY_ENDPOINTS[name]} for name in broker.families], origin=EVIDENCE_ORIGIN,
                 adapter_sha256=broker.adapter_sha256, contracts_sha256=broker.contracts_sha256, stored_root=broker.stored_root)
    encoded = json.dumps(value)
    if any(key and key in encoded for key in broker.keys):
        raise ValueError('Host pool descriptor refused credential content.')
    return encoded


def _write_new(path, document):
    """Exclusive and atomic: a partial file, fsynced, hard-linked to the final name (never replacing one), then removed."""
    path = Path(path)
    partial = path.with_name(path.name + '.partial-' + secrets.token_hex(8))
    try:
        with partial.open('x', encoding='utf-8') as stream:
            json.dump(document, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(partial, path)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass


class EvidenceBroker:
    def __init__(self, *, keys, ledger, archive_root, run_id, families, max_attempts, legacy_paths=(), opener=None, cap=1000, window=3600, per_key_concurrency=2, tools=None, stored_root=None):
        if not re.fullmatch(r'[0-9a-f]{32}', str(run_id)) or type(max_attempts) is not int or max_attempts < 1 or not keys:
            raise ValueError('A 32-hex run id, a positive physical cap and at least one key are required.')
        if sorted(families, key=list(FAMILY_ENDPOINTS).index) != list(families) or any(name not in FAMILY_ENDPOINTS for name in families) or any(name not in families for name in ALWAYS_FAMILIES):
            raise ValueError('Families must be canonical and include B and E.')
        tools = Path(tools) if tools is not None else Path(__file__).resolve().parent
        self.keys, self.run_id, self.families, self.max_attempts = list(dict.fromkeys(keys)), run_id, list(families), max_attempts
        self.key_ids, self.per_key_concurrency = [budget_id(key) for key in self.keys], per_key_concurrency
        self.patterns = [re.compile(FAMILY_ENDPOINTS[name]) for name in self.families]
        self.archive_root = Path(archive_root)
        # The spelling recorded in evidence paths and named in the descriptor. It IS the real path in production (a Windows broker writes the Windows spelling the chain of
        # receipts already uses); a different one is only for the tests of the path mapping.
        self.stored_root = str(stored_root) if stored_root is not None else str(archive_root)
        self.adapter_sha256 = hashlib.sha256((tools / 'fec_accounted_client.py').read_bytes()).hexdigest()
        self.contracts_sha256 = hashlib.sha256((tools / 'spending_endpoint_contracts.py').read_bytes()).hexdigest()
        self._factory = lambda archive: AccountedTransport(self.keys, ledger, archive, max_attempts, legacy_paths=legacy_paths, opener=opener, cap=cap, window=window,
                                                           per_key_concurrency=per_key_concurrency)
        self.token = secrets.token_urlsafe(32)
        self.state, self.transport, self.lock, self.journal_lock = 'new', None, threading.RLock(), threading.RLock()
        self.seen, self.inflight, self.number, self.peak = set(), set(), 0, 0
        self.by_status, self.by_key, self.refusals, self.levels = {}, {}, {}, {}
        self.undelivered, self.delivered, self.bytes_archived, self.started = [], 0, 0, datetime.now(timezone.utc).isoformat()
        self.stamps = collections.deque()
        self.descriptor_sha256, self.journal_path, self._journal, self.receipt_path = None, None, None, None

    # -- journal: append-only NDJSON, fsynced per line, outside responses-* (the validator lists those folders and flags extra files)
    def _log(self, record):
        line = (json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n').encode()
        with self.journal_lock:
            self._journal.write(line)
            self._journal.flush()
            os.fsync(self._journal.fileno())

    def mark_delivery(self, number, delivered):
        if number is None or self._journal is None:
            return
        with self.lock:
            if delivered:
                self.delivered += 1
            else:
                self.undelivered.append(number)
        self._log({'event': 'delivery', 'n': number, 'delivered': bool(delivered)})

    def _respell(self, value):
        real = str(self.archive_root)
        return self.stored_root + value[len(real):] if isinstance(value, str) and self.stored_root != real and value.startswith(real) else value

    def _respelled(self, record):
        return {name: self._respell(value) if name in ('path', 'receipt_path') else value for name, value in record.items()} if isinstance(record, dict) else record

    # -- the wire
    def handle(self, method, path, headers, body):
        """(HTTP status, JSON-able dict). Pure of sockets: the HTTP server and the in-process tests both call this."""
        headers = {str(name).lower(): value for name, value in (headers or {}).items()}
        if path not in ('/v2/open', '/v2/fetch', '/v2/close'):
            return 404, {'error': 'unknown_path'}
        if method != 'POST':
            return 405, {'error': 'post_only'}
        if headers.get('origin') or not secrets.compare_digest(str(headers.get('authorization', '')), 'Bearer ' + self.token):
            return 403, {'error': 'forbidden'}
        if len(body) > MAX_BODY:
            return 413, {'error': 'too_large'}
        try:
            data = json.loads(body)
        except ValueError:
            return 400, {'error': 'invalid_json'}
        if not isinstance(data, dict):
            return 400, {'error': 'invalid_json'}
        try:
            return {'/v2/open': self._open, '/v2/fetch': self._fetch, '/v2/close': self._close}[path](data)
        except Exception:
            # No exception text: it could carry a URL, a header or a key.
            return 500, {'error': 'internal'}

    def _open(self, data):
        if data.get('run_id') != self.run_id:
            return 403, {'error': 'run_id'}
        if not all(isinstance(data.get(name), str) and re.fullmatch(r'[0-9a-f]{64}', data[name]) for name in ('start_sha256', 'collector_sha256')):
            return 400, {'error': 'invalid_open'}
        with self.lock:
            if self.state != 'new':
                return 409, {'error': 'already_opened'}
            archive = self.archive_root / ('responses-' + self.run_id)
            try:
                self.transport = self._factory(archive)              # creates the folder (exist_ok=False): a run id is never reused
            except FileExistsError:
                return 409, {'error': 'archive_exists'}
            journal_dir = self.archive_root / 'broker'
            journal_dir.mkdir(parents=True, exist_ok=True)
            self.journal_path = journal_dir / (self.run_id + '.attempts.ndjson')
            self._journal = open(self.journal_path, 'xb', buffering=0)
            self.state = 'open'
        self._log({'event': 'open', 'run_id': self.run_id, 'start_sha256': data['start_sha256'], 'collector_sha256': data['collector_sha256'], 'families': self.families,
                   'max_attempts': self.max_attempts, 'at': datetime.now(timezone.utc).isoformat()})
        return 200, {'opened': True, 'archive_directory_stored': self._respell(str(archive))}

    def _pool_state(self):
        pool, now = self.transport.pool, time.time()
        with pool.condition:
            return {'healthy': len(pool.healthy()), 'cooling': sum(1 for index in range(len(pool.keys)) if pool.cooldown.get(index, 0) > now),
                    'in_flight': sum(pool.inflight.values())}

    def _fetch(self, data):
        seq, endpoint, params = data.get('seq'), data.get('endpoint'), data.get('params')
        if type(seq) is not int or seq < 1 or not isinstance(endpoint, str) or not isinstance(params, dict):
            return 400, {'error': 'invalid_fetch'}
        if not any(pattern.fullmatch(endpoint) for pattern in self.patterns):
            return 403, {'error': 'family_not_admitted'}
        try:
            public_request(endpoint, params)                          # credential-free names, strict JSON
            if contains_secret(params, self.keys):
                raise ValueError('key value in a selector')
            if 'committee_id' in params and not (isinstance(params['committee_id'], str) and re.fullmatch(r'C[0-9]{8}', params['committee_id'])):
                raise ValueError('committee_id')
            if 'per_page' in params and not (type(params['per_page']) is int and 1 <= params['per_page'] <= 100):
                raise ValueError('per_page')
        except (ValueError, TypeError):
            return 400, {'error': 'invalid_selector'}
        identity = (endpoint, json.dumps(params, sort_keys=True, separators=(',', ':')))
        with self.lock:
            if self.state == 'new':
                return 409, {'error': 'not_open'}
            if seq in self.seen:
                return 409, {'error': 'duplicate_seq'}
            if identity in self.inflight:
                return 409, {'error': 'duplicate_in_flight'}
            self.seen.add(seq)
            if self.state == 'closed':
                return self._refused('stopped')
            self.inflight.add(identity)
            self.peak = max(self.peak, len(self.inflight))
            transport = self.transport
        started, call = datetime.now(timezone.utc).isoformat(), {}
        try:
            try:
                status, headers, body, evidence = transport.fetch_raw(endpoint, params, call=call)
            except FECAdmissionRefused as error:
                text = str(error)
                return self._refused('cap' if 'cap reached' in text else 'stopped' if 'pool is stopped' in text else 'quota')
            except FECArchiveFailed:
                record = self._respelled(call.get('evidence'))
                echoed = isinstance(record, dict) and record.get('refused_secret_echo') is True
                return self._attempt(seq, endpoint, params, call, started, {'attempts': 1, 'refusal': 'secret_echo' if echoed else 'archive_failed', 'status': record.get('status') if echoed else None,
                                                                              'evidence': record if echoed else None}, archived=False)
            except (OSError, http.client.HTTPException):                   # URLError is an OSError; a read that fails after admission is a charged attempt (R1b: per-endpoint)
                if call.get('attempts') != 1:                              # a fault BEFORE the charge (the budget file, the key pool) charged nothing: an internal error and a global stop, never a phantom attempt (delta review D4)
                    return 500, {'error': 'internal'}
                return self._attempt(seq, endpoint, params, call, started, {'attempts': 1, 'status': 0, 'error': 'connection'}, archived=False)
            except ValueError:
                return 400, {'error': 'invalid_selector'}                 # refused before admission (a key value inside a selector); nothing was charged
        finally:
            with self.lock:
                self.inflight.discard(identity)
        lowered = {str(name).lower(): value for name, value in headers.items()}
        remaining = lowered.get('x-ratelimit-remaining')
        try:
            depleted = remaining is not None and int(str(remaining).strip()) <= 0
        except (ValueError, TypeError):
            depleted = False
        level = 'throttled' if status == 429 else 'depleted' if depleted else 'invalid_key' if invalid_key_code({'status': status, 'body': body.decode('utf-8', 'replace')}) else None
        answer = {'attempts': 1, 'status': status, 'headers': {name: value for name, value in headers.items() if str(name).lower() in SUBSET}, 'body_b64': base64.b64encode(body).decode('ascii'),
                  'sha256': evidence['sha256'], 'bytes': evidence['bytes'], 'evidence': self._respelled(evidence), 'key_level': level, 'retry_after': lowered.get('retry-after')}
        return self._attempt(seq, endpoint, params, call, started, answer, archived=True)

    def _refused(self, reason):
        with self.lock:
            self.refusals[reason] = self.refusals.get(reason, 0) + 1
        return 200, {'attempts': 0, 'refusal': reason}

    def _attempt(self, seq, endpoint, params, call, started, answer, *, archived):
        index = call.get('key_index')
        key_id = self.key_ids[index] if isinstance(index, int) and 0 <= index < len(self.key_ids) else None
        with self.journal_lock:                                           # numbering and file order agree: n is increasing down the file
            with self.lock:
                self.number += 1
                number = self.number
                status = answer.get('status')
                self.by_status[str(status)] = self.by_status.get(str(status), 0) + 1
                if key_id:
                    self.by_key[key_id[-12:]] = self.by_key.get(key_id[-12:], 0) + 1
                if answer.get('key_level'):
                    self.levels[answer['key_level']] = self.levels.get(answer['key_level'], 0) + 1
                if archived:
                    self.bytes_archived += answer['bytes']
                self.stamps.append(time.monotonic())
                while self.stamps and self.stamps[0] < time.monotonic() - 3600:
                    self.stamps.popleft()
            self._log({'event': 'attempt', 'n': number, 'seq': seq, 'endpoint': endpoint, 'params_sha256': hashlib.sha256(json.dumps(params, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                       'key_id': key_id, 't_admit': started, 't_done': datetime.now(timezone.utc).isoformat(), 'status': status, 'sha256': answer.get('sha256'), 'bytes': answer.get('bytes'),
                       'archived': archived, 'key_level': answer.get('key_level'), 'retry_after': answer.get('retry_after'), 'refusal': answer.get('refusal'), 'error': answer.get('error')})
        answer.update(n=number, pool=self._pool_state())
        return 200, answer

    def _close(self, data):
        if data.get('run_id') != self.run_id:
            return 403, {'error': 'run_id'}
        with self.lock:
            if self.state == 'new':
                return 409, {'error': 'not_open'}
            first, self.state = self.state != 'closed', 'closed'
        if first:
            self.transport.pool.stop()                                # admission ends; a waiting caller is refused, an attempt in flight finishes
            self._log({'event': 'close', 'at': datetime.now(timezone.utc).isoformat()})
        return 200, {'closed': True}

    def rate_snapshot(self):
        """What the Glass Box heartbeat reports: physical attempts in the last 60 s and hour (this run), in-flight, healthy and cooling keys, 429s so far."""
        now = time.monotonic()
        with self.lock:
            last_minute, last_hour = sum(1 for stamp in self.stamps if stamp >= now - 60), len(self.stamps)
            state, total, limited, refusals = self.state, self.number, self.by_status.get('429', 0), dict(self.refusals)
        snapshot = {'state': state, 'attempts': total, 'attempts_last_60s': last_minute, 'attempts_last_3600s': last_hour, 'requests_per_hour_60s': last_minute * 60, 'rate_limited_429': limited, 'refusals': refusals}
        if self.transport is not None and state == 'open':
            snapshot.update(self._pool_state())
        return snapshot

    def shutdown(self):
        """Stop admitting, close the transport and the journal, and publish BROKER-<run>.json exclusively (once)."""
        with self.lock:
            opened, was = self.state != 'new', self.state
            self.state = 'closed' if opened else 'new'
        if not opened or self.receipt_path is not None:
            return None
        self.transport.pool.stop()
        self.transport.close()
        with self.journal_lock:
            self._journal.close()
        receipt = {'schema_version': 1, 'purpose': 'evidence-broker-receipt', 'run_id': self.run_id, 'started_utc': self.started, 'stopped_utc': datetime.now(timezone.utc).isoformat(),
                   'state_when_stopped': was, 'scope': EVIDENCE_SCOPE, 'families': self.families, 'max_attempts': self.max_attempts, 'descriptor_sha256': self.descriptor_sha256,
                   'adapter_sha256': self.adapter_sha256, 'contracts_sha256': self.contracts_sha256, 'physical_attempts_charged': self.transport.http_attempts,
                   'attempts_journalled': self.number, 'by_status': dict(sorted(self.by_status.items())), 'by_key_id': dict(sorted(self.by_key.items())),
                   'key_levels': dict(sorted(self.levels.items())), 'rate_limited_429': self.by_status.get('429', 0), 'refusals': dict(sorted(self.refusals.items())),
                   'delivered': self.delivered, 'undelivered_attempts': sorted(self.undelivered), 'peak_in_flight': self.peak, 'bytes_archived': self.bytes_archived,
                   'journal': {'path': str(self.journal_path), 'sha256': hashlib.sha256(self.journal_path.read_bytes()).hexdigest(), 'lines': self.journal_path.read_bytes().count(b'\n')},
                   'note': 'attempts_journalled equals the collector receipt http_attempts plus undelivered_attempts; a delivery line missing for an attempt means the broker died before it could confirm.'}
        self.receipt_path = self.journal_path.with_name('BROKER-' + self.run_id + '.json')
        _write_new(self.receipt_path, receipt)
        return receipt


def evidence_http_server(broker):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            body = self.rfile.read(min(length, MAX_BODY + 1)) if length > 0 else b''
            status, payload = broker.handle('POST', self.path, self.headers, body)
            data = json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                broker.mark_delivery(payload.get('n'), False)
                return
            broker.mark_delivery(payload.get('n'), True)

        def do_GET(self):
            self.send_error(405)
    Handler.timeout = 30
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = False                     # server_close() joins the handlers, so nothing is journalled after the journal closes
    server.request_queue_size = 128
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, 'http://127.0.0.1:%d/v2' % server.server_port


def serve_evidence(directory, watch_pid, project_root, *, run_id, archive_root, max_attempts, families, key_loader=load_keys, owner_started=proclock.process_started,
                   owner_alive=proclock.alive, opener=None, cap=1000, per_key_concurrency=2, reach='host.docker.internal', tools=None, ready=None, emit=None, heartbeat_seconds=60):
    """Own one run's broker until the wrapper stops or dies. No pacing, no fallback; the descriptor is the only thing a container sees."""
    directory = Path(directory)
    if not directory.is_dir() or not (directory / 'fec_api_keys').is_file():
        raise ValueError('An existing owner-only compute secret directory is required.')
    started = owner_started(watch_pid)
    if not isinstance(started, int) or started <= 0:
        raise ValueError('Wrapper process creation identity is unavailable.')
    keys = key_loader()
    if not keys:
        raise ValueError('No enabled API keys are available.')
    shared = budget_path(project_root)
    shared.parent.mkdir(parents=True, exist_ok=True)
    broker = EvidenceBroker(keys=keys, ledger=shared, archive_root=archive_root, run_id=run_id, families=families, max_attempts=max_attempts,
                            legacy_paths=legacy_budget_paths(project_root), opener=opener, cap=cap, per_key_concurrency=per_key_concurrency, tools=tools)
    config = directory / 'request-pool.json'
    if config.exists():
        raise ValueError('A host pool descriptor already exists.')
    server = thread = None
    emit = emit if emit is not None else _glassbox
    beat = lambda kind, status, summary, detail='': _safely(emit, kind, status, summary, run_id=run_id, stage='broker', detail=detail, lane='system', source='mentat-evidence-broker')
    try:
        server, thread, url = evidence_http_server(broker)
        encoded = evidence_descriptor_text(broker, url, reach)
        broker.descriptor_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
        temporary = directory / 'request-pool.pending'
        temporary.write_text(encoded, encoding='utf-8')
        temporary.replace(config)
        beat('fec.broker.start', 'running', 'Evidence broker for run ' + run_id + ' ready: families ' + ','.join(families) + ', cap ' + str(max_attempts), json.dumps({'families': list(families), 'max_attempts': max_attempts, 'width': len(keys)}))
        if ready:
            ready(broker, config)
        following = time.monotonic() + heartbeat_seconds
        while not (directory / 'owner.stop').exists() and owner_alive(watch_pid, started):
            time.sleep(.2)
            if time.monotonic() >= following:
                following += heartbeat_seconds
                snapshot = broker.rate_snapshot()
                beat('fec.broker.rate', 'running', 'Evidence broker: %d attempts in the last 60 s, %d in the last hour, %d in total' % (snapshot['attempts_last_60s'], snapshot['attempts_last_3600s'], snapshot['attempts']), json.dumps(snapshot))
    finally:
        config.unlink(missing_ok=True)
        if broker.transport is not None:
            broker.transport.pool.stop()                  # admission ends first; what is in flight finishes and is journalled
        if server:
            server.shutdown()
            server.server_close()
            thread.join()
        broker.shutdown()
    final = broker.rate_snapshot()
    beat('fec.broker.stop', 'completed', 'Evidence broker for run ' + run_id + ' stopped after %d attempts' % final['attempts'], json.dumps({**final, 'receipt': str(broker.receipt_path) if broker.receipt_path else None}))
    return broker


def _glassbox(*arguments, **keywords):
    """The Glass Box emitter of the app tree (imported on first use, so importing this module costs nothing); it swallows its own failures."""
    import glassbox_emit
    return glassbox_emit.emit(*arguments, **keywords)


def _safely(emit, *arguments, **keywords):
    try:
        emit(*arguments, **keywords)
    except Exception:
        pass                                              # an event never breaks the broker


def _bound(body, meta):
    """The metadata record when it binds this exact body (D067 shape), else None."""
    try:
        raw = body.read_bytes()
        record = json.loads(meta.read_bytes())
    except (OSError, ValueError):
        return None
    ok = (isinstance(record, dict) and all(name in record for name in PAIR_FIELDS) and record['sha256'] == hashlib.sha256(raw).hexdigest() and record['bytes'] == len(raw)
          and record['original_response_bytes'] is True and record['raw_body_persisted'] is True and type(record['status']) is int and type(record['attempt']) is int
          and isinstance(record['endpoint'], str) and isinstance(record['query'], dict) and isinstance(record['path'], str) and Path(record['path'].replace('\\', '/')).name == body.name)
    return record if ok else None


def quarantine_run(archive_root, run_id):
    """Host side, after a broker or container crash and before any recovery: move the unpaired or truncated files of responses-<run> into
    crash-quarantine/<run>/ (with a manifest) and leave every complete, binding pair and every sanitized secret-echo refusal record where they are.
    Idempotent: a folder with nothing to move is left untouched and writes nothing. Makes no network call and loads no key."""
    archive_root = Path(archive_root)
    if not re.fullmatch(r'[0-9a-f]{32}', str(run_id)):
        raise ValueError('A 32-hex run id is required.')
    folder = archive_root / ('responses-' + run_id)
    if not folder.is_dir():
        raise ValueError('No response folder for this run: ' + str(folder))
    stems = {}
    for name in sorted(item.name for item in folder.iterdir()):
        if name.endswith('.body.bin'):
            stems.setdefault(name[:-9], {})['body'] = name
        elif name.endswith('.json'):
            stems.setdefault(name[:-5], {})['meta'] = name
        else:
            stems.setdefault(name, {})['other'] = name
    move, kept_pairs, kept_refusals = [], 0, 0
    for stem, part in sorted(stems.items()):
        body, meta, other = part.get('body'), part.get('meta'), part.get('other')
        if body and meta:
            if _bound(folder / body, folder / meta) is not None:
                kept_pairs += 1
            else:
                move += [(body, 'archive pair does not bind (truncated or inconsistent)'), (meta, 'archive pair does not bind (truncated or inconsistent)')]
        elif meta:
            try:
                record = json.loads((folder / meta).read_bytes())
            except (OSError, ValueError):
                record = None
            if isinstance(record, dict) and record.get('refused_secret_echo') is True and record.get('raw_body_persisted') is False:
                kept_refusals += 1
            else:
                move.append((meta, 'metadata without a body'))
        elif body:
            move.append((body, 'body without metadata'))
        else:
            move.append((other, 'unexpected file'))
    if not move:
        return {'run_id': run_id, 'quarantined': 0, 'kept_pairs': kept_pairs, 'kept_refusal_records': kept_refusals, 'manifest': None}
    target = archive_root / 'crash-quarantine' / run_id
    target.mkdir(parents=True, exist_ok=True)
    files = []
    for name, reason in move:
        source = folder / name
        raw = source.read_bytes()
        entry = {'name': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'reason': reason}
        for number in range(5):
            try:
                os.replace(source, target / name)
                break
            except PermissionError:
                if number == 4:
                    raise
                time.sleep(.2 * (number + 1))
        files.append(entry)
    manifest = target / ('QUARANTINE-' + secrets.token_hex(4) + '.json')
    _write_new(manifest, {'schema_version': 1, 'purpose': 'host-crash-quarantine', 'run_id': run_id, 'quarantined_utc': datetime.now(timezone.utc).isoformat(), 'files': files,
                          'kept_pairs': kept_pairs, 'kept_refusal_records': kept_refusals, 'never_replayed_here': True})
    return {'run_id': run_id, 'quarantined': len(files), 'kept_pairs': kept_pairs, 'kept_refusal_records': kept_refusals, 'manifest': str(manifest)}


def quarantine_main(argv):
    parser = argparse.ArgumentParser(prog='api_host_pool.py quarantine', description=quarantine_run.__doc__)
    parser.add_argument('--archive-root', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(quarantine_run(args.archive_root, args.run_id)))
        return 0
    except Exception:
        print('Host quarantine refused or failed; nothing was recovered.', file=sys.stderr)
        return 2


def main():
    if len(sys.argv)>1 and sys.argv[1]=='quarantine':return quarantine_main(sys.argv[2:])
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-directory',type=Path,required=True)
    parser.add_argument('--watch-pid',type=int,required=True)
    parser.add_argument('--source-database')
    parser.add_argument('--project-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--scope',choices=('schedule-a','spending-evidence-v2'),default='schedule-a')
    parser.add_argument('--run-id');parser.add_argument('--archive-root',type=Path);parser.add_argument('--max-attempts',type=int);parser.add_argument('--families',default='B,E')
    args=parser.parse_args()
    if args.scope=='schedule-a' and (not args.source_database or args.run_id or args.archive_root or args.max_attempts):parser.error('--scope schedule-a needs --source-database and takes no --run-id, --archive-root or --max-attempts')
    if args.scope!='schedule-a' and (args.source_database or not (args.run_id and args.archive_root and args.max_attempts)):parser.error('--scope spending-evidence-v2 needs --run-id, --archive-root and --max-attempts and takes no --source-database')
    try:
        if args.scope=='schedule-a':
            source=host_job_database(args.source_database,args.project_root)
            serve(args.run_directory,args.watch_pid,source,args.project_root)
        else:
            serve_evidence(args.run_directory,args.watch_pid,args.project_root,run_id=args.run_id,archive_root=args.archive_root,max_attempts=args.max_attempts,families=parse_families(args.families))
        return 0
    except Exception:
        # No descriptor, token, key, transport exception or credential-bearing URL.
        print('Host request pool refused or failed; collection cannot use a local fallback.',file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
