import http.client
import json
from pathlib import Path
import secrets
import sys
import threading
import unittest
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admin_dashboard import AdminSession, create_server, STATIC, CLIENT
from desktop_admin import Refused
import test_access_store as store_tests


class AdminDashboardTests(unittest.TestCase):
    def setUp(self):
        store_tests.AccessStoreTests.setUp(self)
        self.server, self.url = create_server(self.store)
        self.addCleanup(self.server.server_close)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True); worker.start()
        self.addCleanup(worker.join, 3); self.addCleanup(self.server.shutdown)
        parsed = urlsplit(self.url)
        self.origin = 'http://' + parsed.netloc
        self.bootstrap = parse_qs(parsed.fragment)['bootstrap'][0]

    def call(self, route, value, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('POST', route, json.dumps(value), {'Content-Type':'application/json', 'Origin':self.origin, **(headers or {})})
            response = connection.getresponse()
            self.assertIsNone(response.getheader('Set-Cookie'))
            self.assertIn("frame-ancestors 'none'", response.getheader('Content-Security-Policy'))
            return response.status, json.loads(response.read())
        finally: connection.close()

    def session(self):
        code, value = self.call('/session', {'bootstrap':self.bootstrap})
        self.assertEqual(code, 200)
        return {'Authorization':'Bearer '+value['bearer'], 'X-Admin-CSRF':value['csrf']}

    def test_one_use_bootstrap_memory_bearer_csrf_and_exact_origin(self):
        headers = self.session()
        self.assertEqual(self.call('/session', {'bootstrap':self.bootstrap})[0], 403)
        self.assertEqual(self.call('/dashboard', {}, headers)[0], 200)
        for override in ({'Origin':'http://127.0.0.1:1'}, {'Host':'rebound.invalid'}, {'X-Admin-CSRF':secrets.token_urlsafe(32)}, {'Authorization':'Bearer '+secrets.token_urlsafe(32)}, {'Cookie':'admin=anything','Authorization':''}):
            self.assertEqual(self.call('/dashboard', {}, {**headers, **override})[0], 403)
        self.assertEqual(self.call('/dashboard', {})[0], 403)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_explicit_role_scope_approve_deny_and_revoke(self):
        request = store_tests.AccessStoreTests.request(self, 'terrier')
        headers = self.session()
        self.assertEqual(self.call('/approve', {'requestId':request['requestId'],'role':'superuser','districtIds':['co-05']}, headers)[0], 403)
        status, approved = self.call('/approve', {'requestId':request['requestId'],'role':'local_administrator','districtIds':['co-05']}, headers)
        self.assertEqual(status, 200)
        activation = {key:request[key] for key in ('requestId','deviceId','appId','platform','arch')}
        activation['activationKey'] = approved['activationKey']
        result = self.store.activate(activation, request['requestSecret'])
        grant = store_tests.AccessStoreTests.payload(self, result['grant'])
        self.assertEqual(self.call('/revoke', {'grantId':grant['grantId']}, headers)[0], 200)
        self.assertTrue(store_tests.AccessStoreTests.payload(self, self.store.status(result['accessToken']))['revoked'])
        request2 = store_tests.AccessStoreTests.request(self, 'mentat')
        self.assertEqual(self.call('/deny', {'requestId':request2['requestId']}, headers)[0], 200)
        self.assertEqual(self.store.poll(request2['requestId'], request2['requestSecret'])['status'], 'denied')

    def test_session_expiry_and_static_ui_use_no_ambient_storage(self):
        clock = [0]; session = AdminSession(clock=lambda:clock[0]); bootstrap = session.bootstrap
        clock[0] = 61
        with self.assertRaises(Refused): session.exchange({'bootstrap':bootstrap})
        script = (STATIC/'app.js').read_text(encoding='utf-8')
        for dangerous in ('innerHTML', 'localStorage', 'sessionStorage', 'document.cookie', 'eval('):
            self.assertNotIn(dangerous, script)
        self.assertIn('textContent', script)
        self.assertIn('history.replaceState', script)
        html = (STATIC/'index.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', html)

    def test_fixed_shared_theme_and_original_icons_only(self):
        for route, path in [('/desktop-chrome.css',CLIENT/'desktop-chrome.css'),
                            *[('/icons/'+app+'.ico', CLIENT/'icons'/(app+'.ico')) for app in ('melange','terrier','mentat')]]:
            with self.subTest(route=route):
                connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
                try:
                    connection.request('GET',route)
                    response=connection.getresponse()
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.read(),path.read_bytes())
                    self.assertIn("img-src 'self'",response.getheader('Content-Security-Policy'))
                finally: connection.close()
        for route in ('/icons/../desktop_admin.py','/desktop-chrome.css?file=../keys','/icons/other.ico'):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
            try:
                connection.request('GET',route)
                response=connection.getresponse()
                self.assertEqual(response.status,404)
                response.read()
            finally: connection.close()

    def test_session_close_releases_only_authenticated_session(self):
        first=self.session()
        fresh=self.server.fresh_bootstrap_url()
        second_bootstrap=parse_qs(urlsplit(fresh).fragment)['bootstrap'][0]
        _,session=self.call('/session',{'bootstrap':second_bootstrap})
        second={'Authorization':'Bearer '+session['bearer'],'X-Admin-CSRF':session['csrf']}
        self.assertEqual(self.call('/session-close',{}, {'Authorization':first['Authorization'],'X-Admin-CSRF':second['X-Admin-CSRF']})[0],403)
        self.assertEqual(self.call('/session-close',{},first)[0],200)
        self.assertEqual(self.call('/dashboard',{},first)[0],403)
        self.assertEqual(self.call('/dashboard',{},second)[0],200)


if __name__ == '__main__': unittest.main()
