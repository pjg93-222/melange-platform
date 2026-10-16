import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
import urllib.request
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('desktop_backend', Path(__file__).with_name('desktop_backend.py'))
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class BackendBoundaryTests(unittest.TestCase):
    def test_invitation_is_only_in_authenticated_health_and_no_referrer(self):
        guarded = backend.GuardedWSGI(lambda environ, start: [b'app'], 'x'*32, 'terrier', launch_path='/?invite=private')
        capture = []
        start = lambda status, headers: capture.append((status, dict(headers)))
        denied = b''.join(guarded({'PATH_INFO':backend.HEALTH_PATH, 'REQUEST_METHOD':'GET'}, start))
        self.assertNotIn(b'private', denied)
        answer = b''.join(guarded({'PATH_INFO':backend.HEALTH_PATH, 'REQUEST_METHOD':'GET', 'HTTP_X_DESKTOP_TOKEN':'x'*32}, start))
        self.assertEqual(json.loads(answer)['launchPath'], '/?invite=private')
        self.assertEqual(capture[-1][1]['Referrer-Policy'], 'no-referrer')
        self.assertEqual(capture[-1][1]['Cache-Control'], 'no-store')

    def test_mentat_activity_reports_real_busy_without_data(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), backend.guarded_handler(Handler, 'x'*32, 'mentat'))
        working = [True]
        server.app = SimpleNamespace(busy=lambda: working[0])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://127.0.0.1:' + str(server.server_port) + backend.ACTIVITY_PATH
        try:
            with self.assertRaises(HTTPError) as denied:
                urllib.request.urlopen(url)
            self.assertEqual(denied.exception.code, 403)
            for busy in (True, False):
                working[0] = busy
                with urllib.request.urlopen(urllib.request.Request(url, headers={backend.TOKEN_HEADER: 'x'*32})) as response:
                    self.assertEqual(json.load(response), {'appId':'mentat', 'busy':busy})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_stdlib_boundary_protects_every_verb_without_replacing_existing_routes(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'existing app')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), backend.guarded_handler(Handler, 'x'*32, 'melange'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        try:
            for method in ('GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'):
                with self.assertRaises(HTTPError) as result:
                    urllib.request.urlopen(urllib.request.Request(origin+'/', method=method))
                self.assertEqual(result.exception.code, 403)
            request = urllib.request.Request(origin+'/', headers={backend.TOKEN_HEADER:'x'*32})
            self.assertEqual(urllib.request.urlopen(request).read(), b'existing app')
            request = urllib.request.Request(origin+backend.HEALTH_PATH, headers={backend.TOKEN_HEADER:'x'*32})
            self.assertEqual(json.load(urllib.request.urlopen(request))['appId'], 'melange')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_django_boundary_authenticates_before_existing_application(self):
        calls=[]
        def app(environ,start):
            calls.append(environ)
            start('200 OK', [])
            return [b'existing django']
        guarded=backend.GuardedWSGI(app,'x'*32,'terrier')
        status=[]
        self.assertEqual(guarded({}, lambda code, headers: status.append(code)), [b'Desktop authorization required'])
        self.assertEqual(status, ['403 Forbidden'])
        self.assertEqual(calls, [])
        self.assertEqual(guarded({'HTTP_X_DESKTOP_TOKEN':'x'*32}, lambda *args: None), [b'existing django'])


if __name__ == '__main__':
    unittest.main()
