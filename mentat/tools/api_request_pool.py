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
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
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
