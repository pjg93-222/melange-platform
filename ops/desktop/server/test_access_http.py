import base64
import hashlib
import http.client
import io
import json
from pathlib import Path
import secrets
import socket
import ssl
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desktop_admin import BoundedServer, handler_for
from access_http import HTTPRefused, RateLimit
import district_packs
import test_access_store as store_tests
from test_district_packs import stamp


class AccessHTTPTests(unittest.TestCase):
    def setUp(self):
        store_tests.AccessStoreTests.setUp(self)
        self.store.clock = time.time
        self.serve(require_invite=False)
        self.context = ssl._create_unverified_context()  # Fixture server; pin validation belongs to native-client tests.

    def serve(self, require_invite):
        self.packs = Path(self.temp.name) / 'packs'
        self.server = BoundedServer(('127.0.0.1', 0), handler_for(self.publisher, require_invite, self.packs), self.publisher.tls_context())
        self.addCleanup(self.server.server_close)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 3)
        self.addCleanup(self.server.shutdown)

    def count(self, sql):
        with self.store.connect() as db:
            return db.execute(sql).fetchone()[0]

    def call(self, method, path, value=None, headers=None, raw=None):
        connection = http.client.HTTPSConnection('127.0.0.1', self.server.server_port, context=self.context, timeout=3)
        try:
            combined = {'Content-Type': 'application/json', **(headers or {})}
            body = raw if raw is not None else json.dumps(value) if value is not None else None
            connection.request(method, path, body=body, headers=combined)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def request_value(self):
        return dict(requestId=str(uuid.uuid4()), deviceId=str(uuid.uuid4()), appId='melange',
                    platform='win32', arch='x64', displayName='Synthetic request', requestSecret=secrets.token_urlsafe(32))

    def post_request(self, value):
        connection = http.client.HTTPSConnection('127.0.0.1', self.server.server_port, context=self.context, timeout=3)
        try:
            connection.request('POST', '/v1/access/requests', json.dumps(value), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read()), response.getheader('Retry-After')
        finally:
            connection.close()

    def test_invites_off_by_default_requests_without_a_code_work_and_supplied_codes_are_not_consumed(self):
        self.assertEqual(self.post_request(self.request_value())[0], 201)  # A 1.0.9 client sends no inviteCode.
        invite = self.store.issue_invite('Unspent')
        self.assertEqual(self.post_request({**self.request_value(), 'inviteCode': invite['code']})[0], 201)
        self.assertEqual(self.post_request({**self.request_value(), 'inviteCode': '0000-0000-0000-0000'})[0], 201)
        for junk in (5, None, ['x'], {'a': 1}):
            with self.subTest(junk=junk):
                self.assertEqual(self.post_request({**self.request_value(), 'inviteCode': junk})[:2], (400, {'error': 'invalid_request'}))
        self.assertEqual(self.count('SELECT COUNT(*) FROM requests'), 3)
        self.assertEqual(self.count('SELECT uses FROM invites'), 0)

    def test_bad_invite_codes_get_a_distinct_non_retryable_403_and_leave_no_request(self):
        self.serve(require_invite=True)
        request = {**self.request_value(), 'inviteCode': self.store.issue_invite('Tester')['code']}
        missing = {key: value for key, value in request.items() if key != 'inviteCode'}
        for value in [missing] + [{**request, 'inviteCode': code} for code in ('0000-0000-0000-0000', 'short', 5, None, '', 'U' * 16, ['x'])]:
            with self.subTest(code=value.get('inviteCode', 'missing')):
                self.assertEqual(self.post_request(value), (403, {'error': 'invite_invalid'}, None))
        self.assertEqual(self.count('SELECT COUNT(*) FROM requests'), 0)
        self.assertEqual(self.post_request(request)[0], 201)
        status, repeated, _ = self.post_request(missing)  # Re-POST of an existing request (e.g. made before enforcement) skips the check.
        self.assertEqual((status, repeated['requestId'], repeated['status']), (201, request['requestId'], 'pending'))
        self.assertEqual(self.count('SELECT COUNT(*) FROM requests'), 1)

    def test_used_up_code_is_invalid_and_a_second_open_request_per_device_is_a_conflict(self):
        self.serve(require_invite=True)
        code = self.store.issue_invite('Single')['code']
        first = {**self.request_value(), 'inviteCode': code}
        self.assertEqual(self.post_request(first)[0], 201)
        status, repeated, _ = self.post_request(first)  # A lost-response retry still reads its status.
        self.assertEqual((status, repeated['requestId'], repeated['status']), (201, first['requestId'], 'pending'))
        self.assertEqual(self.post_request({**self.request_value(), 'inviteCode': code}), (403, {'error': 'invite_invalid'}, None))
        spare = self.store.issue_invite('Spare')
        self.assertEqual(self.post_request({**self.request_value(), 'deviceId': first['deviceId'], 'inviteCode': spare['code']}), (409, {'error': 'state_conflict'}, None))
        self.assertEqual([i['uses'] for i in self.store.dashboard()['invites'] if i['inviteId'] == spare['inviteId']], [0])  # Conflict refusal spends nothing.
        self.assertEqual(self.post_request({**first, 'displayName': 'Someone else'}), (409, {'error': 'state_conflict'}, None))

    def test_request_activate_status_and_separate_enrollment_over_real_tls(self):
        request = self.request_value()
        status, result = self.call('POST', '/v1/access/requests', request)
        self.assertEqual(status, 201)
        self.assertEqual(result['status'], 'pending')
        authorization = {'Authorization': 'Request ' + request['requestSecret']}
        self.assertEqual(self.call('GET', '/v1/access/requests/'+request['requestId'], headers=authorization)[0], 200)
        approved = self.store.approve(request['requestId'], 'local_user', [])
        activate = {key: request[key] for key in ('requestId', 'deviceId', 'appId', 'platform', 'arch')}
        activate['activationKey'] = approved['activationKey']
        status, activated = self.call('POST', '/v1/access/activate', activate, authorization)
        self.assertEqual(status, 200)
        bearer = {'Authorization': 'Bearer ' + activated['accessToken']}
        self.assertEqual(self.call('GET', '/v1/access/status', headers=bearer)[0], 200)
        status, enrolled = self.call('POST', '/v1/access/update-enrollment', {'currentVersion': '1.0.0'}, bearer)
        self.assertEqual(status, 200)
        self.assertNotEqual(activated['accessToken'], enrolled['deviceToken'])
        self.assertEqual(self.call('GET', '/v1/access/status', headers={'Authorization': 'Bearer '+enrolled['deviceToken']})[0], 403)
        self.assertEqual(self.call('POST', '/v1/status', {'version': '1.0.0', 'status': 'current'}, bearer)[0], 403)
        self.assertEqual(self.call('POST', '/v1/access/approve', {}, bearer)[0], 404)

    def test_browser_host_json_and_target_rejections(self):
        request = self.request_value()
        for headers in ({'Host': 'evil.invalid'}, {'Origin': 'http://127.0.0.1'}, {'Sec-Fetch-Mode': 'cors'}, {'Sec-Fetch-Dest': 'empty'}):
            self.assertEqual(self.call('POST', '/v1/access/requests', request, headers)[0], 403)
        for raw in ('{"requestId":"a","requestId":"b"}', '{"value":NaN}', '[]'):
            self.assertEqual(self.call('POST', '/v1/access/requests', raw=raw)[0], 400)
        self.assertEqual(self.call('POST', '/v1/access/requests', {**request, 'platform': 'darwin'})[0], 403)
        self.assertEqual(self.call('GET', '/v1/access/requests/'+request['requestId'], headers={'Authorization':'Request '+request['requestSecret']})[0], 403)
        self.assertEqual(self.call('OPTIONS', '/v1/access/requests')[0], 405)

    def test_rejects_unauthorized_and_oversized_before_body(self):
        for route, headers, expected in (
            ('/v1/access/events', {}, 403),
            ('/v1/access/requests', {}, 413),
            ('/v1/access/requests', {'Transfer-Encoding':'chunked'}, 400),
        ):
            connection = http.client.HTTPSConnection('127.0.0.1', self.server.server_port, context=self.context, timeout=3)
            try:
                connection.putrequest('POST', route)
                connection.putheader('Content-Type', 'application/json')
                connection.putheader('Content-Length', '1000000')
                for key, value in headers.items(): connection.putheader(key, value)
                connection.endheaders()  # No body sent: rejection must not wait for it.
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                response.read()
            finally:
                connection.close()

    def test_duplicate_headers_and_truncated_body_do_not_mutate(self):
        connection = http.client.HTTPSConnection('127.0.0.1', self.server.server_port, context=self.context, timeout=3)
        try:
            connection.putrequest('POST', '/v1/access/requests')
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', '2')
            connection.putheader('Content-Length', '3')
            connection.endheaders()
            response = connection.getresponse(); self.assertEqual(response.status, 400); response.read()
        finally:
            connection.close()
        raw = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3)
        wrapped = self.context.wrap_socket(raw, server_hostname='127.0.0.1')
        wrapped.sendall(f'POST /v1/access/requests HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{{'.encode())
        wrapped.close()
        self.assertEqual(self.call('POST', '/v1/access/requests', self.request_value())[0], 201)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 1)

    def test_rate_limit_is_bounded_and_recovers_after_window(self):
        clock = [0]
        limiter = RateLimit(clock=lambda: clock[0])
        for _ in range(60): limiter.check('192.168.1.1')
        with self.assertRaises(HTTPRefused) as caught: limiter.check('192.168.1.1')
        self.assertEqual(caught.exception.status, 429)
        clock[0] = 61
        limiter.check('192.168.1.1')

    def test_access_routes_have_no_per_address_rate_limit(self):
        for _ in range(65):  # Formerly 60/min per peer.
            self.assertEqual(self.call('GET', '/v1/access/status')[0], 403)
        self.assertEqual(self.call('POST', '/v1/access/requests', self.request_value())[0], 201)

    request, approval, activated, payload = (store_tests.AccessStoreTests.request, store_tests.AccessStoreTests.approval,
                                             store_tests.AccessStoreTests.activated, store_tests.AccessStoreTests.payload)

    def get(self, path, token=None):
        connection = http.client.HTTPSConnection('127.0.0.1', self.server.server_port, context=self.context, timeout=3)
        try:
            connection.request('GET', path, headers={'Authorization': 'Bearer ' + token} if token else {})
            response = connection.getresponse()
            return response.status, response.getheader('Content-Type'), response.getheader('Content-Length'), response.read()
        finally:
            connection.close()

    def pack(self, district):
        """A synthetic published pack: random bytes (the roads object is larger than several 64 KiB reads), except the
        street and contacts stores, which carry the district's voter-file hash that publish checks."""
        build = Path(self.temp.name) / ('build-' + secrets.token_hex(4))
        build.mkdir()
        for name in district_packs.REQUIRED + ('bundle.zip.sha256',):
            if name in ('street.sqlite3', 'contacts.sqlite3'):
                stamp(build / name, district)
            else:
                (build / name).write_bytes(secrets.token_bytes(300000 if name == 'roads.sqlite3' else 64))
        with zipfile.ZipFile(build / 'bundle.zip', 'x') as archive:
            archive.writestr('manifest.json', json.dumps({'snapshot': district_packs.SNAPSHOT_PREFIX[district] + '0' * 12}))
        return json.loads(base64.b64decode(district_packs.publish(self.store, district, build, packs=self.packs)['payload']))

    def raw_get(self, path, token):
        connection = http.client.HTTPSConnection('127.0.0.1', self.server.server_port, context=self.context, timeout=3)
        self.addCleanup(connection.close)
        connection.request('GET', path, headers={'Authorization': 'Bearer ' + token})
        return connection.getresponse()

    @staticmethod
    def object_url(revision, item, offset=0, length=16):
        return '/v1/data/objects/%s?offset=%d&length=%d&revisionId=%s' % (item['sha256'], offset, length, revision['revisionId'])

    def test_data_routes_require_an_active_approval_for_that_district(self):
        co, ct = self.pack('co-05'), self.pack('ct-01')
        later = self.pack('co-05')
        _, activated = self.activated('terrier')  # approved for co-05 and or-05
        token, catalog = activated['accessToken'], '/v1/data/catalog?after=%d&limit=%d&districtId=%s'
        self.assertEqual(self.get(catalog % (0, 25, 'co-05'))[0], 403)
        self.assertEqual(self.get(self.object_url(co, co['objects'][0]))[0], 403)
        status, kind, _, body = self.get(catalog % (0, 1, 'co-05'), token)
        page = json.loads(body)
        self.assertEqual((status, kind, sorted(page), page['nextCursor'], page['hasMore']), (200, 'application/json', ['hasMore', 'nextCursor', 'revisions'], 1, True))
        self.assertEqual(self.payload(page['revisions'][0]), co)
        page = json.loads(self.get(catalog % (1, 25, 'co-05'), token)[3])
        self.assertEqual(([self.payload(e)['revisionId'] for e in page['revisions']], page['nextCursor'], page['hasMore']),
                         ([later['revisionId']], 3, False))
        self.assertEqual(json.loads(self.get(catalog % (3, 25, 'co-05'), token)[3]), {'revisions': [], 'nextCursor': 3, 'hasMore': False})
        self.assertEqual(self.get(catalog % (0, 25, 'ct-01'), token)[0], 403)  # not approved for ct-01
        self.assertEqual(self.get(self.object_url(ct, ct['objects'][0]), token)[0], 403)
        self.assertEqual(self.get('/v1/data/catalog?after=0&limit=25', token)[0], 403)  # Terrier data is always district-scoped
        self.assertEqual(self.get(self.object_url(co, co['objects'][0]), token)[0], 200)
        self.store.revoke(self.payload(activated['grant'])['grantId'])
        self.assertEqual(self.get(catalog % (0, 25, 'co-05'), token)[0], 403)
        self.assertEqual(self.get(self.object_url(co, co['objects'][0]), token)[0], 403)

    def test_object_ranges_are_exact_bounded_listed_and_streamed_in_small_reads(self):
        revision = self.pack('co-05')
        token = self.activated('terrier')[1]['accessToken']
        item = max(revision['objects'], key=lambda value: value['size'])
        data = (self.packs / 'objects' / item['sha256']).read_bytes()
        self.assertEqual(len(data), item['size'])
        reads = []
        class Recording(io.BufferedReader):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)
        with patch('access_http.open', create=True, side_effect=lambda path, mode: Recording(io.FileIO(path, mode))):
            answer = self.get(self.object_url(revision, item, 10, 200000), token)
        self.assertEqual(answer, (200, 'application/octet-stream', '200000', data[10:200010]))
        self.assertTrue(len(reads) >= 4 and max(reads) <= 65536, reads)
        tail = item['size'] - 5
        self.assertEqual(self.get(self.object_url(revision, item, tail, 5), token)[2:], ('5', data[tail:]))
        for offset, length in ((tail, 6), (item['size'], 1), (0, 4 * 1024 * 1024 + 1)):
            self.assertEqual(self.get(self.object_url(revision, item, offset, length), token)[0], 400)
        self.assertEqual(self.get(self.object_url(revision, {'sha256': hashlib.sha256(b'unlisted').hexdigest()}), token)[0], 403)
        self.assertEqual(self.get(self.object_url({'revisionId': str(uuid.uuid4())}, item), token)[0], 403)
        self.assertEqual(self.get('/v1/data/catalog?after=0&limit=25&districtId=co-05&extra=1', token)[0], 404)
        self.assertEqual(self.get('/v1/data/catalog?after=0&limit=' + '9' * 190, token)[0], 400)  # over 200 characters
        self.assertEqual(self.get('/v1/access/status?x=1', token)[0], 400)  # '?' stays refused outside /v1/data/
        self.assertEqual(self.get('/v1/access/status', token)[0], 200)

    def test_data_has_its_own_slots_and_is_refused_at_once_when_they_are_full(self):
        revision = self.pack('co-05')
        token = self.activated('terrier')[1]['accessToken']
        url = self.object_url(revision, revision['objects'][0])
        self.assertEqual(self.server.RequestHandlerClass.data_slots._initial_value, 16)
        slots = self.server.RequestHandlerClass.data_slots = threading.BoundedSemaphore(1)
        slots.acquire()  # every data slot busy
        started = time.monotonic()
        response = self.raw_get(url, token)
        self.assertEqual((response.status, json.loads(response.read()), response.getheader('Retry-After')), (503, {'error': 'service_unavailable'}, '5'))
        self.assertLess(time.monotonic() - started, 2)  # refused at once, not queued
        self.assertEqual(self.get('/v1/access/status', token)[0], 200)  # the access routes keep their slots
        slots.release()
        self.assertEqual(self.get(url, token)[0], 200)
        self.assertTrue(slots.acquire(timeout=2))  # released after the response

    def test_an_object_response_past_its_deadline_drops_the_connection_and_frees_its_slot(self):
        revision = self.pack('co-05')
        token = self.activated('terrier')[1]['accessToken']
        item = max(revision['objects'], key=lambda value: value['size'])
        self.server.RequestHandlerClass.data_slots = threading.BoundedSemaphore(1)
        with patch.multiple('access_http', DATA_RATE=10 ** 12, DATA_GRACE=-1):  # already past due when the body starts
            response = self.raw_get(self.object_url(revision, item, 0, item['size']), token)
            self.assertEqual((response.status, response.getheader('Content-Length')), (200, str(item['size'])))
            with self.assertRaises(http.client.IncompleteRead):
                response.read()
        deadline = time.monotonic() + 2
        while self.get(self.object_url(revision, item), token)[0] != 200:  # the only slot came back
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)

    def test_a_pack_store_in_onedrive_or_git_is_never_served(self):
        revision = self.pack('co-05')
        token = self.activated('terrier')[1]['accessToken']
        self.assertEqual(self.server.RequestHandlerClass.packs_root, self.packs.resolve())
        synced = handler_for(self.publisher, False, Path(self.temp.name) / 'OneDrive - Org' / 'packs')
        self.assertIsNone(synced.packs_root)
        self.server.RequestHandlerClass = synced
        self.assertEqual(self.get(self.object_url(revision, revision['objects'][0]), token)[0], 503)
        self.assertEqual(self.get('/v1/data/catalog?after=0&limit=25&districtId=co-05', token)[0], 503)
        self.assertEqual(self.get('/v1/access/status', token)[0], 200)

    def test_header_drip_has_total_deadline_and_releases_connection(self):
        self.server.RequestHandlerClass.header_deadline = 0.15
        raw = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3)
        wrapped = self.context.wrap_socket(raw, server_hostname='127.0.0.1')
        try:
            wrapped.sendall(b'POST /v1/access/requests HTTP/1.1\r\nHost: ')
            time.sleep(0.3)
            self.assertEqual(wrapped.recv(1), b'')
        finally: wrapped.close()
        self.assertEqual(self.call('POST', '/v1/access/requests', self.request_value())[0], 201)


if __name__ == '__main__': unittest.main()
