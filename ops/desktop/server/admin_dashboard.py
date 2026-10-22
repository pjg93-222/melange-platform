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
from apps_admin import AppsAdmin, AppsError, UnavailableApps
from desktop_apps_admin import DesktopApps, ROUTES as DESKTOP_ROUTES

STATIC = Path(__file__).with_name('admin_ui_v2')
CLIENT = Path(__file__).resolve().parent.parent / 'client'


APPS_ROUTES = {'/apps', '/apps/detail', '/apps/logs', '/apps/action', '/apps/invite', '/apps/docker-start'} | DESKTOP_ROUTES
# How long the dashboard process serves after its newest open: one session (8 h) plus the 60 s its bootstrap link may wait to be
# exchanged, so no session outlives it. Every reopen starts the time again (an old window reopened late in the day gets a whole session).
LIFETIME = 8*3600+60
# A browser opens several asset connections together. Keep that burst within the bounded worker budget; four
# connections can otherwise drop app.js before it runs, leaving the page waiting forever for a local session.
CONNECTION_LIMIT = 16


class SessionCapacityReached(Refused):
    pass


class NoSession(Refused):
    """No valid bearer: the Apps routes answer 401. A valid bearer with a bad CSRF value is a plain Refused (403)."""


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
            raise NoSession('local session required')
        try: token_hash = secret_hash(authorization[7:])
        except Refused: raise NoSession('local session required') from None
        with self.lock:
            if self.clock() >= self.expires or not self.token_hash or not hmac.compare_digest(self.token_hash, token_hash):
                raise NoSession('local session required')
            try: csrf_hash = secret_hash(csrf)
            except Refused: raise Refused('local session required') from None
            if not self.csrf_hash or not hmac.compare_digest(self.csrf_hash, csrf_hash):
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
            self.end = self.clock()+LIFETIME   # when the server stops, as far as sessions are concerned (the server's timer starts later)
            return session.bootstrap

    def exchange(self, value):
        with self.lock:
            for session in self.sessions:
                try: result = session.exchange(value)
                except Refused: continue
                session.expires = min(session.expires, self.end)   # never a session that outlives the server
                return result
        raise Refused('bootstrap unavailable')

    def authenticate(self, authorization, csrf):
        bad_csrf = False
        with self.lock:
            for session in self.sessions:
                try: return session.authenticate(authorization, csrf)
                except NoSession: pass
                except Refused: bad_csrf = True
        raise Refused('local session required') if bad_csrf else NoSession('local session required')

    def close_session(self, authorization, csrf):
        with self.lock:
            for session in self.sessions:
                try: session.authenticate(authorization, csrf)
                except Refused: continue
                self.sessions.remove(session)
                return
        raise Refused('local session required')


def create_server(access, port=0, apps=None, desktop=None):
    if type(port) is not int or (port and not 1024 <= port <= 65535):
        raise Refused('unprivileged local dashboard port required')
    session, limiter = AdminSessions(), RateLimit()
    desktop = desktop if desktop is not None else DesktopApps()
    try:
        apps = apps if apps is not None else AppsAdmin(audit=access)
    except ValueError:   # bad MELANGE_ADMIN_COMPOSE_* settings must not stop access approvals from working
        apps = UnavailableApps('The Apps settings (MELANGE_ADMIN_COMPOSE_PROJECT and MELANGE_ADMIN_COMPOSE_FILE) are not valid. Correct them, then open the dashboard again.')

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
            if not isinstance(value, dict): raise ValueError('JSON object required')
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
                if self.path not in {'/dashboard', '/approve', '/replace-key', '/deny', '/revoke', '/session-close', '/invite-issue', '/invite-revoke'} | APPS_ROUTES:
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
                elif self.path in DESKTOP_ROUTES:
                    result = desktop.handle(self.path, value)
                elif self.path in APPS_ROUTES:
                    result = apps.handle(self.path, value)
                elif self.path == '/invite-issue':
                    fields(value, {'label', 'uses', 'days'})
                    result = access.issue_invite(value['label'], value['uses'], value['days'])
                elif self.path == '/invite-revoke':
                    fields(value, {'inviteId'})
                    access.revoke_invite(value['inviteId']); result = {'ok':True}
                else:
                    fields(value, {'grantId'})
                    access.revoke(value['grantId']); result = {'ok':True}
                self.reply(200, result)
            except NoSession:
                self.reply(401 if self.path in APPS_ROUTES else 403, {'error':'session_required' if self.path in APPS_ROUTES else 'access_denied'})
            except AppsError as error:
                self.reply(error.status, {'error':error.code, **({'message':error.message} if error.message else {})})
            except (Refused, ValueError, TypeError, KeyError) as error:
                # On the Apps routes a malformed body or a bad value is the caller's mistake (400); a Refused is still a refusal.
                bad = self.path in APPS_ROUTES and isinstance(error, ValueError) and not isinstance(error, Refused)
                self.reply(400 if bad else 403, {'error':'invalid_request' if bad else 'access_denied'})
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
        request_queue_size = CONNECTION_LIMIT

        make_timer = threading.Timer   # tests swap in a recording timer

        def __init__(self):
            self.slots = threading.BoundedSemaphore(CONNECTION_LIMIT)
            self.lifetime, self.lifetime_lock, self.closed = None, threading.Lock(), False   # closed: the shutdown timer fired or the server closed
            super().__init__(('127.0.0.1', port), Handler)

        def arm(self):
            """Stop serving LIFETIME from now, replacing any earlier time: when serving starts and on every reopen, so a window
            that is reopened late in the day is not cut off by the timer of the first open. Expired sessions must not leave a
            permanent listener. Refused once it has ended, so a reopen racing the shutdown is answered BUSY rather than given a closed port
            (a plain Refused: SessionCapacityReached would tell the reopen that four sessions are open)."""
            with self.lifetime_lock:
                if self.closed: raise Refused('the dashboard has ended')
                if self.lifetime: self.lifetime.cancel()
                timer = self.make_timer(LIFETIME, lambda: self.expire(timer))
                timer.daemon = True
                self.lifetime = timer
                timer.start()

        def expire(self, timer=None):
            """Stop serving. From a timer, only while it is still the current one: cancel() cannot stop a timer that has already
            fired and is waiting for the lock a reopen holds, and that reopen's window must keep its dashboard."""
            with self.lifetime_lock:
                if timer is not None and timer is not self.lifetime: return
                self.closed = True
            self.shutdown()

        def serve_forever(self, *args, **kwargs):
            self.arm()
            super().serve_forever(*args, **kwargs)

        def process_request(self, request, address):
            if address[0] != '127.0.0.1' or not self.slots.acquire(blocking=False):
                self.shutdown_request(request); return
            try: super().process_request(request, address)
            except Exception:
                self.slots.release(); raise

        def process_request_thread(self, request, address):
            try: super().process_request_thread(request, address)
            finally: self.slots.release()

        def server_close(self):
            try: apps.shutdown()   # actions still running are written to the activity log as interrupted
            finally:
                with self.lifetime_lock:
                    self.closed = True
                    if self.lifetime: self.lifetime.cancel()
                super().server_close()

    server = LocalServer()

    def fresh_bootstrap_url():
        bootstrap = session.new_bootstrap()   # refuses at four open sessions, before anything is re-armed
        server.arm()
        return f'http://127.0.0.1:{server.server_port}/#bootstrap={bootstrap}'
    server.fresh_bootstrap_url = fresh_bootstrap_url
    return server, f'http://127.0.0.1:{server.server_port}/#bootstrap={session.bootstrap}'
