import http.client
import json
from pathlib import Path
import secrets
import socket
import ssl
import sys
import threading
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desktop_admin import BoundedServer, handler_for
from access_http import HTTPRefused, RateLimit
import test_access_store as store_tests


class AccessHTTPTests(unittest.TestCase):
    def setUp(self):
        store_tests.AccessStoreTests.setUp(self)
        self.store.clock = time.time
        self.server = BoundedServer(('127.0.0.1', 0), handler_for(self.publisher), self.publisher.tls_context())
        self.addCleanup(self.server.server_close)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.shutdown)
        self.context = ssl._create_unverified_context()  # Fixture server; pin validation belongs to native-client tests.

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
