"""Local-only live display and file-backed telemetry for the independent worker."""
from __future__ import annotations
import argparse, csv, ctypes, json, os, threading, time
from collections import Counter, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]

def atomic(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    for attempt in range(10):
        try: os.replace(temp, path); return
        except PermissionError:
            if attempt == 9: raise
            time.sleep(.05)

class Telemetry:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.state = dict(pid=os.getpid(), stage='preparing', active={}, total=0, processed=0, recovered=0)
        threading.Thread(target=self.heartbeat, daemon=True).start()

    def heartbeat(self):
        while not self.stop.is_set():
            with self.lock:
                self.state['heartbeat_at'] = datetime.now(timezone.utc).isoformat()
                atomic(self.directory/'live_state.json', self.state)
            self.stop.wait(2)

    def update(self, **values):
        with self.lock: self.state.update(values)

    def event(self, kind, row=None, **values):
        row = row or {}
        event = dict(at=datetime.now(timezone.utc).isoformat(), kind=kind,
                     url=row.get('url',''), member=row.get('member',''),
                     title=row.get('title',''), bioguide=row.get('bioguide',''), **values)
        with self.lock:
            with (self.directory/'live_events.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(event, ensure_ascii=False)+'\n')
            if event['url']:
                if kind == 'result': self.state['active'].pop(event['url'], None)
                else: self.state['active'][event['url']] = event

    def close(self, status):
        self.update(stage=status, active={})
        self.stop.set()
        with self.lock:
            self.state['heartbeat_at'] = datetime.now(timezone.utc).isoformat()
            atomic(self.directory/'live_state.json', self.state)

def alive(pid):
    if not pid: return False
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle: return False
        code = ctypes.c_ulong()
        try: return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally: kernel.CloseHandle(handle)
    try: os.kill(int(pid), 0); return True
    except OSError: return False

class Store:
    def __init__(self, directory):
        self.directory=directory; self.lock=threading.Lock(); self.offsets={}
        self.results={}; self.events=deque(maxlen=120); self.queue={}
    def read_json(self, name):
        try: return json.loads((self.directory/name).read_text(encoding='utf-8'))
        except (OSError, ValueError): return {}
    def ingest(self, name, consume):
        path=self.directory/name
        if not path.exists(): return
        offset=self.offsets.get(name,0)
        if path.stat().st_size<offset: offset=0
        with path.open('rb') as f:
            f.seek(offset)
            while True:
                start=f.tell(); line=f.readline()
                if not line or not line.endswith(b'\n'): f.seek(start); break
                try: consume(json.loads(line))
                except (ValueError,KeyError): pass
            self.offsets[name]=f.tell()
    def snapshot(self):
        with self.lock:
            if not self.queue and (self.directory/'priority_queue.csv').exists():
                with (self.directory/'priority_queue.csv').open(encoding='utf-8-sig',newline='') as f:
                    self.queue={r['url']:r for r in csv.DictReader(f)}
            def record_result(row):
                self.results.pop(row['url'],None)
                self.results[row['url']]=row
            self.ingest('article_results.jsonl',record_result)
            self.ingest('live_events.jsonl',self.events.append)
            receipt=self.read_json('recovery_receipt.json'); live=self.read_json('live_state.json')
            counts=Counter(r['status'] for r in self.results.values())
            recent=[]
            for r in list(self.results.values())[-150:][::-1]:
                meta=self.queue.get(r['url'],{})
                recent.append(dict(url=r['url'], title=r.get('title_detail') or meta.get('title') or r['url'], member=meta.get('member',''), status=r['status'], words=len(r.get('body','').split()), date=r.get('date_detail',''), extractor=r.get('extractor',''), outcome=r.get('attempts',[{}])[-1] if r.get('attempts') else {}))
            log=''
            if (self.directory/'refresh.log').exists():
                with (self.directory/'refresh.log').open('rb') as f:
                    f.seek(max(0,(self.directory/'refresh.log').stat().st_size-6000));log=f.read().decode('utf-8','replace')
            return dict(receipt=receipt,live=live,worker_alive=alive(receipt.get('pid')),counts=dict(counts),total=live.get('total') or len(self.queue),processed=len(self.results),events=list(self.events)[::-1],recent=recent,diagnosis=self.read_json('diagnosis.json'),refresh_log=log,server_time=datetime.now(timezone.utc).isoformat())

def politico_snapshot():
    """News collector only. Unknown source state must not become zero or live."""
    return dict(kind='politico_news',status='NOT CONNECTED',live=False,
                message='Politico news collector is not connected yet. Its process and output folder have not been located.',
                needed='Provide the collector folder, script name, or Codex task name to connect its live article stream.')

def valid_host(host,port):
    """DNS-rebinding guard: a rebound hostname still arrives with its own name in Host."""
    return (host or '').lower() in (f'127.0.0.1:{port}',f'localhost:{port}',f'[::1]:{port}')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=8876)
    ap.add_argument('--output',type=Path,default=ROOT/'outputs/press_recovery_20260920')
    args=ap.parse_args();store=Store(args.output)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            if not valid_host(self.headers.get('Host'),self.server.server_port): self.send_error(403,'Local access only.');return
            path=urlsplit(self.path)
            if path.path=='/': data=Path(__file__).with_suffix('.html').read_bytes();kind='text/html; charset=utf-8'
            elif path.path=='/health': data=b'{"service":"melange-collection-monitor","version":2}';kind='application/json'
            elif path.path=='/api/politico': data=json.dumps(politico_snapshot(),ensure_ascii=False).encode();kind='application/json'
            elif path.path=='/politico-receipt': data=json.dumps(politico_snapshot().get('receipt',{}),indent=2).encode();kind='application/json'
            elif path.path=='/api/state': data=json.dumps(store.snapshot(),ensure_ascii=False).encode();kind='application/json'
            elif path.path=='/api/article':
                url=parse_qs(path.query).get('url',[''])[0]
                with store.lock: result=store.results.get(url)
                if result is None: self.send_error(404);return
                data=json.dumps(result,ensure_ascii=False).encode();kind='application/json'
            elif path.path=='/receipt': data=json.dumps(store.read_json('recovery_receipt.json'),indent=2).encode();kind='application/json'
            else: self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Press recovery display: http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()

if __name__=='__main__': main()
