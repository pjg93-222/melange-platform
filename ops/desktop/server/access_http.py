"""Bounded native HTTPS access routes; administrator operations are never LAN routes."""
import json
import os
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from desktop_admin import Refused, canonical, private_address, request_origin
from access_store import secret_hash
import district_packs

# Exactly the read routes of client dataset-transport.cjs route(); every other /v1/data/ path is 404.
_NUMBER, _UUID = '(0|[1-9][0-9]{0,15})', '([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})'
DATA_CATALOG = re.compile(r'/v1/data/catalog\?after=' + _NUMBER + r'&limit=([1-9]|[1-9][0-9]|100)(?:&districtId=(co-05|ct-01|or-05))?')
DATA_OBJECT = re.compile(r'/v1/data/objects/([a-f0-9]{64})\?offset=' + _NUMBER + r'&length=([1-9][0-9]{0,6})&revisionId=' + _UUID)
DATA_CHUNK, DATA_READ = 4 * 1024 * 1024, 65536
# At most DATA_SLOTS of the listener's 32 workers serve data, so /v1/access/* always keeps the rest; each object response
# must finish within length/DATA_RATE + DATA_GRACE seconds or its connection is dropped.
DATA_SLOTS, DATA_RATE, DATA_GRACE = 16, 32 * 1024, 30


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
    """Per-minute buckets for the loopback-only admin dashboard. The recipient access routes have none:
    their load is bounded by the listener's worker slots, not by refusing addresses or people."""
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


def handler_for(access, fallback, packs=None):
    try:  # checked once at startup and cached: a pack store in OneDrive or a git worktree is never served
        packs_root = district_packs.refuse_synced(packs or district_packs.default_packs())
    except Refused:
        packs_root = None

    class Handler(HeaderDeadlineMixin, fallback):
        data_slots = threading.BoundedSemaphore(DATA_SLOTS)

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
                if status in (429, 503):
                    self.send_header('Retry-After', '60' if status == 429 else '5')
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
            # Only dataset reads carry a query (an object range is 168 characters); every other route keeps 160 and no '?'.
            data = self.path.startswith('/v1/data/')
            if (len(self.path) > (200 if data else 160) or any(char in self.path for char in ('%', '\\', '#') + (() if data else ('?',)))
                    or '..' in self.path):
                raise HTTPRefused(400, 'invalid_request')
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

        def data_get(self):
            catalog, item = DATA_CATALOG.fullmatch(self.path), DATA_OBJECT.fullmatch(self.path)
            if not catalog and not item:
                raise HTTPRefused(404, 'not_found')
            if packs_root is None or not self.data_slots.acquire(blocking=False):
                raise HTTPRefused(503, 'service_unavailable')  # busy: refuse at once, never wait for or take an access slot
            try:
                grant = access.authenticate(self.access_secret('Bearer'))  # revoked approvals are refused here
                if catalog:
                    after, limit, district = catalog.groups()
                    return self.access_response(200, district_packs.catalog(packs_root, grant, int(after), int(limit), district))
                sha256, offset, length, revision = item.groups()
                offset, length = int(offset), int(length)
                if length > DATA_CHUNK:
                    raise HTTPRefused(400, 'invalid_request')
                path, size = district_packs.published_object(packs_root, grant, sha256, revision)
                if offset + length > size:
                    raise HTTPRefused(400, 'invalid_request')
                with open(path, 'rb') as stream:
                    if os.fstat(stream.fileno()).st_size != size:
                        raise HTTPRefused(503, 'service_unavailable')
                    stream.seek(offset)
                    deadline = time.monotonic() + length / DATA_RATE + DATA_GRACE
                    self.close_connection = True
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(length))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Connection', 'close')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    while length:  # bounded reads; the whole object is never in memory
                        chunk = stream.read(min(DATA_READ, length))
                        if not chunk:
                            raise ConnectionAbortedError()  # shrank after the size check: drop the connection, never pad
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise TimeoutError()  # a slow or stalled reader: drop the connection mid-body
                        self.connection.settimeout(left)  # a blocked write ends at the deadline too
                        self.wfile.write(chunk)
                        length -= len(chunk)
            finally:
                self.data_slots.release()

        def access_dispatch(self):
            try:
                self.access_preflight()
                if self.command == 'GET':
                    if self.headers.get('Content-Length') not in (None, '0'):
                        raise HTTPRefused(400, 'invalid_request')
                    if self.path.startswith('/v1/data/'):
                        return self.data_get()
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
                elif 'invite' in message:
                    self.access_response(403, {'error': 'invite_invalid'})
                else:
                    self.access_response(403, {'error': 'access_denied'})
            except (KeyError, TypeError, ValueError):
                self.access_response(400, {'error': 'invalid_request'})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                self.close_connection = True
            except Exception:
                self.access_response(503, {'error': 'service_unavailable'})

        def do_GET(self):
            return self.access_dispatch() if self.path.startswith(('/v1/access', '/v1/data/')) else super().do_GET()

        def do_POST(self):
            return self.access_dispatch() if self.path.startswith(('/v1/access', '/v1/data/')) else super().do_POST()

        def do_OPTIONS(self):
            self.access_response(405, {'error': 'method_not_allowed'})

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS

    Handler.packs_root = packs_root
    return Handler
