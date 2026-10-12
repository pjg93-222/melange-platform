"""Durable, indexed FEC evidence for automatic cross-referencing.

Source responses and competing observations are retained. Loading evidence never
updates the source collection database or treats an absent response as zero.
"""
from __future__ import annotations

from contextlib import closing
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlparse, parse_qs, urlencode

from fec_filing_headers import parse_header, fetch_header, PREFIX_BYTES
from report_source_guard import cents


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def filing_number(value):
    """FEC zero/blank sentinels do not establish a filing identity."""
    text = str(value or '').strip()
    if re.fullmatch(r'[0-9]+(?:\.0+)?', text):
        number = int(text.split('.')[0])
        return str(number) if number > 0 else None
    return None


ACCOUNTING_FIELDS = ('back_reference_transaction_id', 'back_reference_schedule_name',
    'contributor_id', 'filing_form', 'line_number', 'memo_code', 'memoed_subtotal',
    'memo_text', 'receipt_type_full', 'entity_type')


def incomplete_receipt_evidence(row):
    return any(row.get(field) in (None, '') for field in ('filing_form', 'line_number', 'entity_type'))


def public_fec_url(url):
    parsed = urlparse(str(url))
    if parsed.scheme != 'https' or parsed.hostname not in ('api.open.fec.gov', 'docquery.fec.gov', 'www.fec.gov'):
        raise ValueError('Evidence must identify a public FEC source')
    if parsed.username or parsed.password or any('key' in k.lower() or 'token' in k.lower() for k in parse_qs(parsed.query)):
        raise ValueError('Credentials must not be archived in source URLs')
    return str(url)


class EvidenceStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=60)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS documents(sha256 TEXT PRIMARY KEY, source_url TEXT NOT NULL,
            observed_at TEXT NOT NULL, payload_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS records(source_id TEXT NOT NULL, document_sha256 TEXT NOT NULL,
            row_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
            PRIMARY KEY(source_id,document_sha256,row_sha256));
          CREATE INDEX IF NOT EXISTS record_lookup ON records(source_id);
          CREATE TABLE IF NOT EXISTS headers(file_num TEXT PRIMARY KEY, document_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS imported_files(path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER,
            sha256 TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS document_observations(observation_id TEXT PRIMARY KEY,
            document_sha256 TEXT NOT NULL, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
            last_observed_at TEXT NOT NULL, http_status INTEGER, query_json TEXT,
            collector_archive_sha256 TEXT, payload_kind TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS document_observation_lookup ON document_observations(document_sha256);
          CREATE TABLE IF NOT EXISTS recovery_attempts(target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL, last_attempt_at REAL NOT NULL, status TEXT NOT NULL,
            PRIMARY KEY(target_kind,target_id,source_fingerprint));
        ''')
        self.db.commit()
        self._verified_documents = OrderedDict()
        self._verification_stamp = None

    def close(self):
        self.db.commit()
        self.db.close()

    def archive(self, response, source_url, observed_at=None):
        public_fec_url(source_url)
        payload = canonical(response); sha = digest(payload)
        self.db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?)', (sha, source_url, observed_at or now(), payload))
        rows = response.get('results', response.get('rows', [])) if isinstance(response, dict) else []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get('sub_id') or '').strip():
                continue
            item = canonical(row)
            self.db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?,?)', (str(row['sub_id']), sha, digest(item), item))
        return sha

    def observe(self, document, source_url, observed_at, *, last_observed_at=None, http_status=None,
                query_json=None, collector_archive_sha256=None, payload_kind='json_response'):
        public_fec_url(source_url)
        value = dict(document_sha256=document, source_url=source_url, observed_at=observed_at,
                     http_status=http_status, query_json=query_json,
                     collector_archive_sha256=collector_archive_sha256, payload_kind=payload_kind)
        self.db.execute('''INSERT INTO document_observations VALUES(?,?,?,?,?,?,?,?,?)
          ON CONFLICT(observation_id) DO UPDATE SET last_observed_at=MAX(last_observed_at,excluded.last_observed_at)''',
          (digest(canonical(value)), document, source_url, observed_at, last_observed_at or observed_at,
           http_status, query_json, collector_archive_sha256, payload_kind))

    def attempted_recently(self, kind, identifier, fingerprint, retry_after_seconds):
        result = self.db.execute('''SELECT last_attempt_at FROM recovery_attempts
          WHERE target_kind=? AND target_id=? AND source_fingerprint=?''', (kind, str(identifier), fingerprint)).fetchone()
        return bool(result and time.time() - result[0] < retry_after_seconds)

    def attempted(self, kind, identifier, fingerprint, status):
        self.db.execute('''INSERT INTO recovery_attempts VALUES(?,?,?,?,?)
          ON CONFLICT(target_kind,target_id,source_fingerprint) DO UPDATE SET
          last_attempt_at=excluded.last_attempt_at,status=excluded.status''',
          (kind, str(identifier), fingerprint, time.time(), status))

    def _verify_document(self, sha):
        if sha in self._verified_documents:
            self._verified_documents.move_to_end(sha)
            return self._verified_documents[sha]
        found = self.db.execute('SELECT payload_json FROM documents WHERE sha256=?',(sha,)).fetchone()
        if not found or digest(found[0]) != sha:
            raise ValueError('Evidence document hash mismatch or missing document')
        value = json.loads(found[0])
        rows = value.get('results',value.get('rows',[])) if isinstance(value,dict) else []
        membership = {(str(r.get('sub_id')),digest(canonical(r))) for r in rows if isinstance(r,dict)}
        header = canonical(value['header']) if isinstance(value,dict) and 'header' in value else None
        self._verified_documents[sha] = (membership,header)
        if len(self._verified_documents)>2048:
            self._verified_documents.popitem(last=False)
        return membership,header

    def import_header(self, value):
        header = value['header']; prefix = bytes.fromhex(value['prefix_hex'])
        if hashlib.sha256(prefix).hexdigest() != value['prefix_sha256'] or parse_header(prefix, header['file_num']) != header:
            raise ValueError('Filing header evidence failed integrity verification')
        if value['url'] != f'https://docquery.fec.gov/dcdev/posted/{header["file_num"]}.fec':
            raise ValueError('Filing identity disagrees with source URL')
        sha = self.archive(value, value['url'], value.get('retrieved_at'))
        self.db.execute('INSERT OR REPLACE INTO headers VALUES(?,?,?)', (str(header['file_num']), sha, canonical(header)))
        return sha

    def import_directory(self, directory):
        """Import only documented cache envelopes; never interpret arbitrary JSON as decisions."""
        imported = skipped = 0; failures = []
        directory = Path(directory)
        for path in sorted(directory.glob('*.json')):
            if path.name.endswith('.review.json'):
                continue
            stat = path.stat()
            old = self.db.execute('SELECT size,mtime_ns,sha256 FROM imported_files WHERE path=?', (str(path.resolve()),)).fetchone()
            # The immutable copy in documents remains authoritative on a cache hit.
            if old and (stat.st_size, stat.st_mtime_ns) == old[:2]:
                skipped += 1; continue
            try:
                raw = path.read_bytes(); value = json.loads(raw)
                if not isinstance(value, dict):
                    continue
                if 'header' in value and 'prefix_hex' in value:
                    self.import_header(value)
                elif 'response' in value and 'url' in value and 'sha256' in value:
                    # Existing pac_evidence cache uses the default JSON separators.
                    actual = hashlib.sha256(json.dumps(value['response'], sort_keys=True).encode()).hexdigest()
                    if actual != value['sha256']:
                        raise ValueError('FEC response cache hash mismatch')
                    self.archive(value['response'], value['url'], value.get('retrieved_at'))
                else:
                    continue
                self.db.execute('INSERT OR REPLACE INTO imported_files VALUES(?,?,?,?)',
                                (str(path.resolve()), stat.st_size, stat.st_mtime_ns, hashlib.sha256(raw).hexdigest()))
                imported += 1
                if imported % 100 == 0:
                    self.db.commit()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                failures.append({'file':path.name, 'reason':str(exc)})
        self.db.commit()
        return {'imported':imported, 'cached':skipped, 'failures':failures}

    def import_database(self, connection):
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        imported = 0
        if 'fec_response_archive' in tables:
            for sha, payload, kind, created in connection.execute(
                    'SELECT archive_sha256,payload_json,payload_kind,created_at FROM fec_response_archive'):
                if digest(payload) != sha:
                    raise ValueError('Collector archive hash mismatch')
                observations = []
                if 'fec_response_observations' in tables:
                    cursor = connection.execute('SELECT * FROM fec_response_observations WHERE archive_sha256=?', (sha,))
                    observations = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor]
                try:
                    value = json.loads(payload)
                except (ValueError, TypeError):
                    value = {'evidence_kind':'non_json_http_body', 'body':payload, 'collector_archive_sha256':sha}
                if observations and not any(o.get('http_status') in (None, 200) for o in observations):
                    value = {'evidence_kind':'unsuccessful_http_response', 'response':value, 'collector_archive_sha256':sha}
                # Legacy parsed checkpoints retain a distinct evidence label.
                if not observations:
                    observations = [dict(observed_at=created, http_status=None)]
                document = None
                for observation in observations:
                    query_json = observation.get('query_json')
                    source_url = 'https://api.open.fec.gov/v1/schedules/schedule_a/'
                    if query_json:
                        if digest(query_json) != observation.get('query_sha256'):
                            raise ValueError('Collector observation query hash mismatch')
                        request = json.loads(query_json).get('request', {})
                        source_url = request.get('url') or source_url
                        if request.get('params'):
                            source_url += '?' + urlencode(request['params'], doseq=True)
                    observed = observation.get('observed_at') or created
                    document = self.archive(value, source_url, observed)
                    self.observe(document, source_url, observed,
                        last_observed_at=observation.get('last_observed_at'),
                        http_status=observation.get('http_status'), query_json=query_json,
                        collector_archive_sha256=sha, payload_kind=kind)
                imported += 1
        if 'api_page_checkpoints' in tables:
            for (payload,) in connection.execute('SELECT payload_json FROM api_page_checkpoints'):
                try:
                    value = json.loads(payload)
                except (ValueError, TypeError):
                    value = {'evidence_kind':'invalid_legacy_checkpoint','body':payload}
                self.archive(value, 'https://api.open.fec.gov/v1/schedules/schedule_a/')
                imported += 1
        self.db.commit()
        return imported

    def resolve(self, rows):
        stamp = (self.db.total_changes,self.db.execute('PRAGMA data_version').fetchone()[0])
        if stamp != self._verification_stamp:
            self._verified_documents.clear()
            self._verification_stamp = stamp
        evidence = {}; conflicts = []
        for row in rows:
            sid = str(row['sub_id'])
            matches = self.db.execute('''SELECT r.payload_json,r.document_sha256,r.row_sha256,
                COALESCE((SELECT MAX(o.last_observed_at) FROM document_observations o
                    WHERE o.document_sha256=d.sha256 AND (o.http_status=200 OR o.http_status IS NULL)),d.observed_at) AS evidence_time
                FROM records r JOIN documents d ON d.sha256=r.document_sha256
                WHERE source_id=? ORDER BY evidence_time DESC,r.row_sha256''', (sid,)).fetchall()
            valid = []
            for payload, document, row_hash, observed in matches:
                if digest(payload) != row_hash:
                    raise ValueError('Indexed FEC record hash mismatch')
                membership,_ = self._verify_document(document)
                if (sid,row_hash) not in membership:
                    raise ValueError('Indexed FEC record is absent from its referenced document')
                item = json.loads(payload)
                differing = []
                for key in ('committee_id','contributor_name'):
                    if str(item.get(key) or '').strip().upper() != str(row.get(key) or '').strip().upper():
                        differing.append(key)
                if cents(item.get('contribution_receipt_amount')) != cents(row.get('contribution_receipt_amount')):
                    differing.append('contribution_receipt_amount')
                if str(item.get('contribution_receipt_date') or '')[:10] != str(row.get('contribution_receipt_date') or '')[:10]:
                    differing.append('contribution_receipt_date')
                for local, remote in (('file_num','file_number'),('transaction_id','transaction_id')):
                    local_value = filing_number(row.get(local)) if local == 'file_num' else row.get(local)
                    remote_value = filing_number(item.get(remote) or item.get(local)) if local == 'file_num' else item.get(remote)
                    if local_value and str(local_value) != str(remote_value or ''):
                        differing.append(local)
                if differing:
                    conflicts.append({'source_id':sid,'reason':'source_observation_conflict','fields':differing,
                                      'evidence_id':document})
                else:
                    item['_evidence_id'] = document; item['_observed_at'] = observed
                    valid.append(item)
            if valid:
                # Identical source facts with competing relationship metadata need review.
                explicit_blank = {'memo_code', 'memo_text', 'memoed_subtotal'}
                def metadata_value(field, value):
                    if field == 'memoed_subtotal':
                        return str(value).upper() in ('TRUE', '1')
                    return str(value).strip().upper()
                inconsistent = [k for k in ACCOUNTING_FIELDS if len({metadata_value(k, i[k]) for i in valid
                    if k in i and i[k] is not None and (k in explicit_blank or i[k] != '')}) > 1]
                if inconsistent:
                    conflicts.append({'source_id':sid,'reason':'relationship_observation_conflict','fields':inconsistent,
                                      'evidence_ids':sorted({i['_evidence_id'] for i in valid})})
                else:
                    combined = dict(valid[0])
                    provenance = {key:[valid[0]['_evidence_id']] for key,value in valid[0].items()
                                  if not key.startswith('_') and value is not None}
                    for item in valid[1:]:
                        for key,value in item.items():
                            if key.startswith('_'):
                                continue
                            if combined.get(key) in (None,'') and value not in (None,''):
                                combined[key] = value
                                provenance[key] = [item['_evidence_id']]
                            elif key in combined and combined[key] == value:
                                provenance.setdefault(key, []).append(item['_evidence_id'])
                    combined['_evidence_ids'] = sorted({i['_evidence_id'] for i in valid})
                    combined['_field_evidence_ids'] = {k:sorted(set(v)) for k,v in provenance.items()}
                    evidence[sid] = combined
        files = {filing_number(r.get('file_num')) for r in rows} - {None}
        headers = {}
        for file in files:
            found = self.db.execute('SELECT payload_json,document_sha256 FROM headers WHERE file_num=?',(file,)).fetchone()
            if found:
                _,header = self._verify_document(found[1])
                if header != found[0]:
                    raise ValueError('Indexed filing header disagrees with its referenced document')
                headers[file] = {**json.loads(found[0]),'_evidence_id':found[1]}
        return evidence, headers, conflicts


class _PoolWaitGuard:
    """Pause the local evidence lane instead of waiting for an hourly reset."""
    def __init__(self, pool, max_budget_wait_seconds, max_seconds):
        self.pool = pool
        self.stop_event = threading.Event()
        self.reason = None
        self.deadline = time.monotonic() + max(0.01, max_seconds)
        self.wait_limit = max(0.01, max_budget_wait_seconds)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        blocked_since = None
        while not self.stop_event.wait(0.05):
            moment = time.monotonic()
            with self.pool.condition:
                blocked = self.pool.waiting > 0 and not self.pool.busy
            blocked_since = (blocked_since or moment) if blocked else None
            if moment >= self.deadline:
                self.reason = 'Evidence recovery time limit reached'
            elif blocked_since is not None and moment - blocked_since >= self.wait_limit:
                self.reason = 'FEC request budget unavailable; remaining evidence deferred'
            if self.reason:
                self.pool.stop()
                return

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1)


def _row_fingerprint(row):
    return digest(canonical({k:row.get(k) for k in ('sub_id', 'committee_id', 'contributor_name',
        'contribution_receipt_amount', 'contribution_receipt_date', 'file_num', 'transaction_id',
        'source_query_cycle', 'cycle')}))


def fetch_exact_evidence(store, rows, max_requests, cache_directory, budget_path, progress=None, transport=None,
                         *, max_budget_wait_seconds=5, max_seconds=120, retry_after_seconds=86400,
                         known_evidence=None):
    """Budgeted automatic exact-ID recovery, highest dollar uncertainty first."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from urllib.request import Request, build_opener
    from urllib.error import HTTPError
    from api_request_pool import RequestPool, NoRedirect
    from fec_key_store import load_keys
    from pac_evidence import Client

    if max_requests <= 0:
        return {'requested':0,'recovered':0,'reason':'Network recovery disabled','failures':[]}
    cache_directory = Path(cache_directory)
    store.import_directory(cache_directory)
    # New observations may refresh stale metadata without overwriting any older
    # cache. Repeated runs today reuse this snapshot and the attempt ledger.
    refresh_directory = cache_directory / ('refresh-' + datetime.now(timezone.utc).date().isoformat())
    store.import_directory(refresh_directory)
    known = known_evidence if known_evidence is not None else store.resolve(rows)[0]
    unique = {str(r['sub_id']):r for r in rows if re.fullmatch(r'[0-9]+', str(r['sub_id']))}
    wanted = sorted((r for sid,r in unique.items()
        if (sid not in known or incomplete_receipt_evidence(known[sid]))
        and not store.attempted_recently('exact', sid, _row_fingerprint(r), retry_after_seconds)),
        key=lambda r:(-abs(cents(r.get('contribution_receipt_amount'))),str(r['sub_id'])))[:max_requests]
    if not wanted:
        return {'requested':0,'recovered':0,'reason':'Available evidence or recent attempts cover this queue','failures':[]}
    keys = ['test-transport'] if transport else load_keys()
    if not keys:
        return {'requested':0,'recovered':0,'reason':'No FEC credentials available','failures':[]}
    def public_transport(spec,key):
        url = spec['url']
        if not re.fullmatch(r'https://api\.open\.fec\.gov/v1/schedules/schedule_a/[0-9]+/',url):
            raise ValueError('Unexpected evidence endpoint')
        try:
            with build_opener(NoRedirect()).open(Request(url+'?'+urlencode(spec['params']),headers={'X-Api-Key':key}),timeout=30) as response:
                return {'status':response.status,'headers':dict(response.headers),'body':response.read().decode('utf-8')}
        except HTTPError as error:
            return {'status':error.code,'headers':dict(error.headers),'body':''}
        except OSError:
            return {'status':0,'headers':{},'body':''}
    Path(budget_path).parent.mkdir(parents=True, exist_ok=True)
    pool = RequestPool(keys,budget_path,4,transport=transport or public_transport)
    guard = _PoolWaitGuard(pool, max_budget_wait_seconds, max_seconds)
    def request(endpoint,params):
        response=pool.execute({'url':'https://api.open.fec.gov/v1/'+endpoint,'params':params},'cross-reference')
        if response['status']!=200:
            raise ValueError(f'FEC evidence unavailable: HTTP {response["status"]}')
        return json.loads(response['body'])
    client=Client(refresh_directory,key='transport-owned',transport=request)
    failures=[];recovered=0;done=0;still_partial=0
    def fetch(row):
        sid=str(row['sub_id']);endpoint=f'schedules/schedule_a/{sid}/'
        try:
            response=client.page(endpoint,{'two_year_transaction_period':row.get('source_query_cycle') or row['cycle']})
            values=response.get('results',[])
            if len(values)!=1 or str(values[0].get('sub_id'))!=str(sid):
                raise ValueError('Exact source ID unavailable')
            return row,response,None
        except (OSError,ValueError) as error:
            return row,None,str(error)
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            for future in as_completed([executor.submit(fetch,row) for row in wanted]):
                row,response,error=future.result();sid=str(row['sub_id']);done+=1
                if error:
                    failures.append({'source_id':sid,'reason':error})
                else:
                    params = {'two_year_transaction_period':row.get('source_query_cycle') or row['cycle']}
                    url = f'https://api.open.fec.gov/v1/schedules/schedule_a/{sid}/?' + urlencode(sorted(params.items()), doseq=True)
                    saved = json.loads((refresh_directory/(digest(url)+'.json')).read_text(encoding='utf-8'))
                    observed_at = saved.get('retrieved_at') or now()
                    document = store.archive(response,url,observed_at)
                    store.observe(document,url,observed_at,http_status=200,
                        query_json=canonical({'params':params}),payload_kind='exact_id_response')
                    verified,_,disagreements = store.resolve([row])
                    if sid in verified:
                        recovered += 1
                        still_partial += int(incomplete_receipt_evidence(verified[sid]))
                    else:
                        error = 'Recovered source requires review: ' + canonical(disagreements)
                        failures.append({'source_id':sid,'reason':error})
                if not (guard.reason and response is None):
                    store.attempted('exact', sid, _row_fingerprint(row), 'unresolved' if error else 'recovered')
                store.db.commit()
                if progress:progress(done,len(wanted),recovered)
    finally:
        guard.close()
        pool.close()
    return {'selected':len(wanted),'requested':pool.completed,'recovered':recovered,
            'still_partial':still_partial,'deferred_reason':guard.reason,
            'failures':sorted(failures,key=lambda r:r['source_id'])}


def _reserve_header_request(budget_path, wait_seconds, *, cap=1000):
    """Separate public-document budget; never consume or impersonate an API key."""
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        with closing(sqlite3.connect(budget_path, timeout=max(0.01, min(1, wait_seconds)))) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS api_request_budget (budget_id TEXT PRIMARY KEY, window_started_epoch REAL NOT NULL, requests_used INTEGER NOT NULL DEFAULT 0)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT window_started_epoch,requests_used FROM api_request_budget WHERE budget_id='docquery:filing-headers:v1'").fetchone()
            stamp = time.time()
            start,used = row if row and stamp-row[0]<3600 else (stamp,0)
            if used < cap:
                db.execute("INSERT INTO api_request_budget VALUES('docquery:filing-headers:v1',?,?) ON CONFLICT(budget_id) DO UPDATE SET window_started_epoch=excluded.window_started_epoch,requests_used=excluded.requests_used", (start,used+1))
                return
        if time.monotonic() >= deadline:
            raise TimeoutError('Public filing request budget unavailable; header deferred')
        time.sleep(min(0.05, max(0, deadline-time.monotonic())))


def fetch_filing_header_evidence(store, rows, max_requests, cache_directory, budget_path, progress=None,
                                 transport=None, *, max_budget_wait_seconds=5, max_seconds=120,
                                 retry_after_seconds=86400):
    """Reuse cached filing headers, then recover a bounded set of missing files.

    Header evidence establishes form/committee and supported report relationships;
    it does not assert any individual transaction exists or is unique.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from urllib.request import Request, build_opener
    from api_request_pool import NoRedirect

    directory = Path(cache_directory); directory.mkdir(parents=True, exist_ok=True)
    Path(budget_path).parent.mkdir(parents=True, exist_ok=True)
    known = {str(r[0]) for r in store.db.execute('SELECT file_num FROM headers')}
    amounts = {}
    for row in rows:
        file = filing_number(row.get('file_num'))
        if file and file not in known:
            amounts[file] = amounts.get(file, 0) + abs(cents(row.get('contribution_receipt_amount')))
    candidates = sorted(amounts, key=lambda f:(-amounts[f],f))
    cached = [f for f in candidates if (directory/f'{f}.json').exists() or (directory/f'{f}.review.json').exists()]
    cached_set = set(cached)
    network = [f for f in candidates if f not in cached_set
        and not store.attempted_recently('header', f, f, retry_after_seconds)][:max(0,max_requests)]
    wanted = cached + network
    deadline = time.monotonic() + max(0.01,max_seconds)
    requested = 0; lock = threading.Lock(); failures=[]; recovered=0; done=0

    def read_prefix(url):
        nonlocal requested
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Filing evidence recovery time limit reached')
        _reserve_header_request(budget_path, min(max_budget_wait_seconds,remaining))
        with lock:
            requested += 1
        if transport:
            return transport(url)
        request = Request(url,headers={'Range':f'bytes=0-{PREFIX_BYTES-1}', 'User-Agent':'Mentat FEC evidence recovery'})
        with build_opener(NoRedirect()).open(request,timeout=max(1,min(30,remaining))) as response:
            if response.status == 206 and not response.headers.get('Content-Range','').startswith('bytes 0-'):
                raise ValueError('FEC returned the wrong byte range')
            return response.read(PREFIX_BYTES)

    def fetch(file):
        try:
            return file,fetch_header(file,directory,transport=read_prefix),None
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
            return file,None,str(error)

    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(fetch,file) for file in wanted]):
            file,value,error=future.result();done+=1
            if error:
                failures.append({'file_num':file,'reason':error})
            else:
                store.import_header(value);recovered+=1
            if not error or ('budget unavailable' not in error and 'time limit' not in error):
                store.attempted('header',file,file,'unresolved' if error else 'recovered')
            store.db.commit()
            if progress:progress(done,len(wanted),recovered)
    return {'selected':len(wanted),'requested':requested,'cached':len(cached),'recovered':recovered,
            'failures':sorted(failures,key=lambda r:r['file_num'])}
