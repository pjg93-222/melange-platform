"""Explicit local approval dashboard with no ambient cookies or LAN admin routes."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time

from desktop_admin import Refused, canonical
from access_http import unique_object, RateLimit, HTTPRefused, HeaderDeadlineMixin
from access_store import fields, secret_hash

STATIC = Path(__file__).with_name('admin_ui_v2')
CLIENT = Path(__file__).resolve().parent.parent / 'client'


class SessionCapacityReached(Refused):
    pass


class AdminSession:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.bootstrap = secrets.token_urlsafe(32)
        self.bootstrap_hash = secret_hash(self.bootstrap)
        self.bootstrap_expires = clock()+60
        self.token_hash = self.csrf_hash = None
        self.expires = 0

    def exchange(self, value):
        fields(value, {'bootstrap'})
        hashed = secret_hash(value['bootstrap'])
        with self.lock:
            if self.clock() >= self.bootstrap_expires or not self.bootstrap_hash or not hmac.compare_digest(hashed, self.bootstrap_hash):
                raise Refused('bootstrap unavailable')
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.token_hash, self.csrf_hash = secret_hash(token), secret_hash(csrf)
            self.bootstrap_hash = self.bootstrap = None
            self.expires = self.clock()+8*3600
            return {'bearer': token, 'csrf': csrf}

    def authenticate(self, authorization, csrf):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            raise Refused('local session required')
        token_hash, csrf_hash = secret_hash(authorization[7:]), secret_hash(csrf)
        with self.lock:
            if (self.clock() >= self.expires or not self.token_hash or not self.csrf_hash
                    or not hmac.compare_digest(self.token_hash, token_hash) or not hmac.compare_digest(self.csrf_hash, csrf_hash)):
                raise Refused('local session required')


class AdminSessions:
    """Independent memory sessions: reopening never revokes another tab."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock, self.sessions = clock, threading.Lock(), []
        self.bootstrap = self.new_bootstrap()

    def new_bootstrap(self):
        with self.lock:
            self.sessions = [s for s in self.sessions if self.clock() < (s.expires if s.token_hash else s.bootstrap_expires)]
            if len(self.sessions) >= 4:
                raise SessionCapacityReached('four protected dashboard sessions are already open')
            session = AdminSession(self.clock)
            self.sessions.append(session)
            return session.bootstrap

    def exchange(self, value):
        with self.lock:
            for session in self.sessions:
                try: return session.exchange(value)
                except Refused: pass
        raise Refused('bootstrap unavailable')

    def authenticate(self, authorization, csrf):
        with self.lock:
            for session in self.sessions:
                try: return session.authenticate(authorization, csrf)
                except Refused: pass
        raise Refused('local session required')

    def close_session(self, authorization, csrf):
        with self.lock:
            for session in self.sessions:
                try: session.authenticate(authorization, csrf)
                except Refused: continue
                self.sessions.remove(session)
                return
        raise Refused('local session required')


def create_server(access, port=0):
    if type(port) is not int or (port and not 1024 <= port <= 65535):
        raise Refused('unprivileged local dashboard port required')
    session, limiter = AdminSessions(), RateLimit()

    class Handler(HeaderDeadlineMixin, BaseHTTPRequestHandler):
        header_deadline = 5
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *_args):
            pass

        def reply(self, status, body, mime='application/json'):
            raw = body if isinstance(body, bytes) else canonical(body)
            self.close_connection = True
            try:
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Connection', 'close')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def guard(self, mutation=False):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.client_address[0] != '127.0.0.1' or self.headers.get_all('Host', []) != [expected]:
                raise Refused('local origin required')
            for name in ('Origin', 'Content-Length', 'Content-Type', 'Authorization', 'X-Admin-CSRF'):
                if len(self.headers.get_all(name, [])) > 1:
                    raise Refused('duplicate header')
            if self.headers.get_all('Transfer-Encoding') or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise Refused('cross-origin or streaming body refused')
            if mutation and self.headers.get('Origin') != 'http://' + expected:
                raise Refused('exact origin required')
            limiter.check(self.client_address[0])

        def body(self):
            if self.headers.get('Content-Type') != 'application/json':
                raise Refused('JSON required')
            size = self.headers.get('Content-Length', '')
            if not size.isascii() or not size.isdecimal() or not 0 < int(size) <= 4096:
                raise Refused('bounded body required')
            deadline, remaining, chunks = time.monotonic()+5, int(size), []
            while remaining:
                left = deadline-time.monotonic()
                if left <= 0: raise TimeoutError()
                self.connection.settimeout(left)
                chunk = self.rfile.read1(remaining)
                if not chunk: raise Refused('incomplete body')
                chunks.append(chunk); remaining -= len(chunk)
            value = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=unique_object,
                               parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict): raise Refused('JSON object required')
            return value

        def do_GET(self):
            try:
                self.guard()
                target = {'/': (STATIC/'index.html','text/html; charset=utf-8'),
                          '/app.js': (STATIC/'app.js','text/javascript; charset=utf-8'),
                          '/style.css': (STATIC/'style.css','text/css; charset=utf-8'),
                          '/desktop-chrome.css': (CLIENT/'desktop-chrome.css','text/css; charset=utf-8'),
                          **{'/icons/'+app+'.ico': (CLIENT/'icons'/(app+'.ico'),'image/x-icon')
                             for app in ('melange','terrier','mentat')}}.get(self.path)
                if not target:
                    return self.reply(404, {'error':'not_found'})
                self.reply(200, target[0].read_bytes(), target[1])
            except (Refused, ValueError, TypeError):
                self.reply(403, {'error':'access_denied'})
            except HTTPRefused as error:
                self.reply(error.status, {'error':error.code})

        def do_POST(self):
            try:
                self.guard(mutation=True)
                if self.path == '/session':
                    return self.reply(200, session.exchange(self.body()))
                if self.path not in {'/dashboard', '/approve', '/replace-key', '/deny', '/revoke', '/session-close'}:
                    return self.reply(404, {'error':'not_found'})
                session.authenticate(self.headers.get('Authorization'), self.headers.get('X-Admin-CSRF'))
                value = self.body()
                if self.path == '/session-close':
                    fields(value, set())
                    session.close_session(self.headers.get('Authorization'), self.headers.get('X-Admin-CSRF'))
                    result = {'ok':True}
                elif self.path == '/dashboard':
                    fields(value, set())
                    result = access.dashboard()
                elif self.path == '/approve':
                    fields(value, {'requestId', 'role', 'districtIds'})
                    result = access.approve(value['requestId'], value['role'], value['districtIds'])
                elif self.path == '/replace-key':
                    fields(value, {'requestId'})
                    result = access.replace_key(value['requestId'])
                elif self.path == '/deny':
                    fields(value, {'requestId'})
                    access.deny(value['requestId']); result = {'ok':True}
                else:
                    fields(value, {'grantId'})
                    access.revoke(value['grantId']); result = {'ok':True}
                self.reply(200, result)
            except (Refused, ValueError, TypeError, KeyError):
                self.reply(403, {'error':'access_denied'})
            except HTTPRefused as error:
                self.reply(error.status, {'error':error.code})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                self.close_connection = True
            except Exception:
                self.reply(503, {'error':'service_unavailable'})

        def denied(self):
            self.reply(405, {'error':'method_not_allowed'})
        do_OPTIONS = do_PUT = do_DELETE = do_PATCH = do_HEAD = denied

    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 4

        def __init__(self):
            self.slots = threading.BoundedSemaphore(4)
            super().__init__(('127.0.0.1', port), Handler)

        def process_request(self, request, address):
            if address[0] != '127.0.0.1' or not self.slots.acquire(blocking=False):
                self.shutdown_request(request); return
            try: super().process_request(request, address)
            except Exception:
                self.slots.release(); raise

        def process_request_thread(self, request, address):
            try: super().process_request_thread(request, address)
            finally: self.slots.release()

    server = LocalServer()
    server.fresh_bootstrap_url = lambda: f'http://127.0.0.1:{server.server_port}/#bootstrap={session.new_bootstrap()}'
    return server, f'http://127.0.0.1:{server.server_port}/#bootstrap={session.bootstrap}'
