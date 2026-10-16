"""Bounded native HTTPS access routes; administrator operations are never LAN routes."""
import json
import socket
import threading
import time
from urllib.parse import urlsplit

from desktop_admin import Refused, canonical, private_address, request_origin
from access_store import secret_hash


class HeaderDeadlineMixin:
    """A total header deadline, including a peer drip-feeding the request line."""
    header_deadline = 10

    def setup(self):
        super().setup()
        def expire():
            try: self.connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self._header_timer = threading.Timer(self.header_deadline, expire)
        self._header_timer.daemon = True
        self._header_timer.start()

    def parse_request(self):
        try: return super().parse_request()
        finally: self._header_timer.cancel()

    def finish(self):
        self._header_timer.cancel()
        super().finish()


class HTTPRefused(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


class RateLimit:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock, self.entries = clock, threading.Lock(), {}

    def check(self, address):
        now = self.clock()
        with self.lock:
            self.entries = {key: [stamp for stamp in stamps if now-stamp < 60]
                            for key, stamps in self.entries.items() if any(now-stamp < 60 for stamp in stamps)}
            peer = self.entries.get(address, [])
            total = self.entries.get('*', [])
            if len(peer) >= 60 or len(total) >= 600 or (address not in self.entries and len(self.entries) >= 201):
                raise HTTPRefused(429, 'rate_limited')
            self.entries[address] = peer + [now]
            self.entries['*'] = total + [now]


def handler_for(access, fallback):
    limiter = RateLimit()

    class Handler(HeaderDeadlineMixin, fallback):
        def access_response(self, status, value):
            data = canonical(value)
            self.close_connection = True
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
                if status == 429:
                    self.send_header('Retry-After', '60')
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def access_preflight(self):
            if not self.server.public_origin and not private_address(self.client_address[0]):
                raise HTTPRefused(403, 'access_denied')
            self.access_origin = request_origin(self.server)
            expected = urlsplit(self.access_origin).netloc
            if self.headers.get_all('Host', []) != [expected]:
                raise HTTPRefused(403, 'access_denied')
            if any(key.lower() == 'origin' or key.lower().startswith('sec-fetch-') for key in self.headers):
                raise HTTPRefused(403, 'access_denied')
            if any(len(self.headers.get_all(name, [])) > 1 for name in ('Authorization', 'Content-Length', 'Content-Type')) or self.headers.get_all('Transfer-Encoding'):
                raise HTTPRefused(400, 'invalid_request')
            if len(self.path) > 160 or any(char in self.path for char in ('%', '\\', '?', '#')) or '..' in self.path:
                raise HTTPRefused(400, 'invalid_request')
            limiter.check(self.client_address[0])
            if not access.db.is_file():
                raise HTTPRefused(503, 'service_unavailable')

        def access_secret(self, kind):
            header = self.headers.get('Authorization', '')
            prefix = kind + ' '
            if not header.startswith(prefix):
                raise HTTPRefused(403, 'access_denied')
            secret = header[len(prefix):]
            try:
                secret_hash(secret)
            except Refused:
                raise HTTPRefused(403, 'access_denied') from None
            return secret

        def access_body(self):
            if self.headers.get('Content-Type', '').lower() != 'application/json':
                raise HTTPRefused(400, 'invalid_request')
            raw_length = self.headers.get('Content-Length', '')
            if not raw_length.isascii() or not raw_length.isdecimal():
                raise HTTPRefused(400, 'invalid_request')
            length = int(raw_length)
            bound = 65536 if self.path == '/v1/access/events' else 16384
            if not 0 < length <= bound:
                raise HTTPRefused(413, 'request_too_large')
            deadline, chunks, remaining = time.monotonic()+10, [], length
            while remaining:
                left = deadline-time.monotonic()
                if left <= 0:
                    raise TimeoutError()
                self.connection.settimeout(left)
                chunk = self.rfile.read1(min(remaining, 8192))
                if not chunk:
                    raise HTTPRefused(400, 'invalid_request')
                chunks.append(chunk); remaining -= len(chunk)
            try:
                value = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=unique_object,
                                   parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value, dict):
                    raise ValueError()
                return value
            except (ValueError, UnicodeError):
                raise HTTPRefused(400, 'invalid_request') from None

        def access_dispatch(self):
            try:
                self.access_preflight()
                if self.command == 'GET':
                    if self.headers.get('Content-Length') not in (None, '0'):
                        raise HTTPRefused(400, 'invalid_request')
                    if self.path.startswith('/v1/access/requests/'):
                        value = access.poll(self.path.removeprefix('/v1/access/requests/'), self.access_secret('Request'))
                    elif self.path == '/v1/access/status':
                        value = access.status(self.access_secret('Bearer'))
                    else:
                        raise HTTPRefused(404, 'not_found')
                    return self.access_response(200, value)
                if self.command != 'POST':
                    raise HTTPRefused(405, 'method_not_allowed')
                if self.path == '/v1/access/requests':
                    if self.headers.get_all('Authorization'):
                        raise HTTPRefused(403, 'access_denied')
                    return self.access_response(201, access.request(self.access_body(), self.client_address[0]))
                if self.path == '/v1/access/activate':
                    secret = self.access_secret('Request')
                    return self.access_response(200, access.activate(self.access_body(), secret))
                if self.path not in {'/v1/access/presence', '/v1/access/events', '/v1/access/update-enrollment'}:
                    raise HTTPRefused(404, 'not_found')
                bearer = self.access_secret('Bearer')
                access.authenticate(bearer)  # Reject before reading a bearer-protected body.
                value = self.access_body()
                if self.path == '/v1/access/presence':
                    response = access.presence(bearer, value)
                elif self.path == '/v1/access/events':
                    response = access.events(bearer, value)
                else:
                    response = access.update_enrollment(bearer, value, self.access_origin)
                self.access_response(200, response)
            except HTTPRefused as error:
                self.access_response(error.status, {'error': error.code})
            except Refused as error:
                message = str(error)
                if 'capacity' in message:
                    self.access_response(429, {'error': 'rate_limited'})
                elif any(word in message for word in ('conflict', 'already enrolled')):
                    self.access_response(409, {'error': 'state_conflict'})
                else:
                    self.access_response(403, {'error': 'access_denied'})
            except (KeyError, TypeError, ValueError):
                self.access_response(400, {'error': 'invalid_request'})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                self.close_connection = True
            except Exception:
                self.access_response(503, {'error': 'service_unavailable'})

        def do_GET(self):
            return self.access_dispatch() if self.path.startswith('/v1/access') else super().do_GET()

        def do_POST(self):
            return self.access_dispatch() if self.path.startswith('/v1/access') else super().do_POST()

        def do_OPTIONS(self):
            self.access_response(405, {'error': 'method_not_allowed'})

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS

    return Handler
