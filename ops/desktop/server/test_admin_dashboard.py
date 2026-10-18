import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admin_dashboard import AdminSession, create_server, STATIC, CLIENT
from desktop_admin import Refused
import test_access_store as store_tests
import test_apps_admin as apps_tests


class AdminDashboardTests(unittest.TestCase):
    def setUp(self):
        store_tests.AccessStoreTests.setUp(self)
        self.apps, self.runner = apps_tests.make_apps(self.temp.name, self.store)
        self.server, self.url = create_server(self.store, apps=self.apps)
        self.addCleanup(self.server.server_close)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True); worker.start()
        self.addCleanup(worker.join, 3); self.addCleanup(self.server.shutdown)
        parsed = urlsplit(self.url)
        self.origin = 'http://' + parsed.netloc
        self.bootstrap = parse_qs(parsed.fragment)['bootstrap'][0]

    def call(self, route, value, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('POST', route, value if isinstance(value, str) else json.dumps(value), {'Content-Type':'application/json', 'Origin':self.origin, **(headers or {})})
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

    def test_apps_routes_need_a_session_and_csrf(self):
        headers = self.session()
        routes = {'/apps': {}, '/apps/detail': {'service': 'terrier'}, '/apps/logs': {'service': 'terrier'},
                  '/apps/action': {'service': 'terrier', 'action': 'restart'}, '/apps/invite': {'email': 'a@b.org', 'role': 'canvasser'}}
        calls = len(self.runner.calls)
        for route, value in routes.items():
            with self.subTest(route=route):
                self.assertEqual(self.call(route, value)[0], 401)
                self.assertEqual(self.call(route, value, {'Authorization': 'Bearer ' + secrets.token_urlsafe(32), 'X-Admin-CSRF': headers['X-Admin-CSRF']})[0], 401)
                self.assertEqual(self.call(route, value, {'Authorization': headers['Authorization']})[0], 403)
                self.assertEqual(self.call(route, value, {**headers, 'X-Admin-CSRF': secrets.token_urlsafe(32)})[0], 403)
                self.assertEqual(self.call(route, value, {**headers, 'Origin': 'http://127.0.0.1:1'})[0], 403)
                self.assertEqual(self.call(route, value, {**headers, 'Host': 'rebound.invalid'})[0], 403)
        self.assertEqual(len(self.runner.calls), calls)     # nothing reached docker
        self.assertEqual(self.store.app_actions(), [])
        self.assertEqual(self.call('/apps/other', {}, headers)[0], 404)
        self.assertEqual(self.call('/dashboard', {})[0], 403)    # the earlier routes keep their answer

    def test_apps_overview_detail_logs_action_and_activity_log(self):
        headers = self.session()
        status, view = self.call('/apps', {}, headers)
        self.assertEqual(status, 200)
        self.assertEqual([r['name'] for r in view['services']], ['Melange desk', 'Mentat console', 'Terrier'])
        self.assertEqual(self.call('/apps/detail', {'service': 'terrier'}, headers)[1]['service']['state'], 'running')
        self.assertEqual(self.call('/apps/logs', {'service': 'terrier'}, headers)[1]['lines'][-1]['text'], 'SECRET_KEY=[redacted]')
        self.assertEqual(self.call('/apps/action', {'service': 'melange-desk', 'action': 'restart'}, headers), (200, {'ok': True, 'accepted': True}))
        self.assertEqual(len(self.store.app_actions()), 1)
        dashboard = self.call('/dashboard', {}, headers)[1]
        self.assertEqual([(a['service'], a['action'], a['outcome']) for a in dashboard['appActions']], [('melange-desk', 'restart', 'succeeded')])
        status, invited = self.call('/apps/invite', {'email': 'maya@example.org', 'role': 'organizer'}, headers)
        self.assertEqual((status, invited['link']), (200, 'http://127.0.0.1:18766/?invite=TESTTOKEN'))
        self.assertEqual(len(self.store.app_actions()), 2)

    def test_apps_unknown_service_action_and_fields_are_400_and_unlogged(self):
        headers = self.session()
        for route, value in (('/apps/detail', {'service': 'nope'}), ('/apps/logs', {'service': 'nope'}),
                             ('/apps/action', {'service': 'nope', 'action': 'restart'}), ('/apps/action', {'service': 'terrier', 'action': 'rm -rf'}),
                             ('/apps/action', {'service': 'terrier'}), ('/apps/invite', {'email': 'bad', 'role': 'canvasser'}),
                             ('/apps/invite', {'email': 'a@b.org', 'role': 'root'}), ('/apps', {'service': 'terrier'})):
            with self.subTest(route=route, value=value):
                self.assertEqual(self.call(route, value, headers)[0], 400)
        self.assertEqual(self.store.app_actions(), [])

    def test_dark_theme_tokens_for_the_switch_checkbox_and_focus_ring_have_enough_contrast(self):
        css = (STATIC/'style.css').read_text(encoding='utf-8')
        dark = re.search(r'@media\(prefers-color-scheme:dark\)\{:root\{(.*?)\}', css, re.S).group(1)
        token = dict(re.findall(r'(--[\w-]+):(#[0-9A-Fa-f]{6})', dark))
        def luminance(color):
            r, g, b = (int(color[i:i+2], 16)/255 for i in (1, 3, 5))
            f = lambda c: c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4
            return 0.2126*f(r) + 0.7152*f(g) + 0.0722*f(b)
        def contrast(a, b):
            high, low = sorted((luminance(a), luminance(b)), reverse=True)
            return (high+0.05)/(low+0.05)
        self.assertEqual(token['--desktop-bg'], '#17131D')
        for name in ('--desktop-accent', '--desktop-control-border', '--primary-bg'):   # focus ring + checkbox / switch track + switch on
            for ground in ('--desktop-bg', '--desktop-panel'):
                with self.subTest(token=name, ground=ground):
                    self.assertGreaterEqual(contrast(token[name], token[ground]), 3, name)
        self.assertGreaterEqual(contrast('#FFFFFF', token['--desktop-control-border']), 3)      # the knob on the off track
        self.assertGreaterEqual(contrast(token['--primary-fg'], token['--desktop-accent']), 4.5)  # text on the plain buttons
        self.assertIn('button{color:var(--primary-fg)}', css)

    def test_apps_malformed_json_is_400_not_403_and_other_routes_keep_403(self):
        headers = self.session()
        for route in ('/apps', '/apps/detail', '/apps/action', '/apps/invite'):
            for body in ('{not json', '{"service":"terrier","service":"x"}', '[]', '"text"'):
                with self.subTest(route=route, body=body):
                    self.assertEqual(self.call(route, body, headers), (400, {'error': 'invalid_request'}))
        self.assertEqual(self.call('/dashboard', '{not json', headers)[0], 403)
        self.assertEqual(self.call('/approve', '[]', headers)[0], 403)
        self.assertEqual(self.store.app_actions(), [])

    def test_bad_compose_settings_leave_approvals_working_and_apps_answer_not_set_up(self):
        with mock.patch.dict(os.environ, {'MELANGE_ADMIN_COMPOSE_FILE': 'relative.yaml'}):
            self.server, url = create_server(self.store)
        self.addCleanup(self.server.server_close)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True); worker.start()
        self.addCleanup(worker.join, 3); self.addCleanup(self.server.shutdown)
        parsed = urlsplit(url)
        self.origin, self.bootstrap = 'http://' + parsed.netloc, parse_qs(parsed.fragment)['bootstrap'][0]
        headers = self.session()
        self.assertEqual(self.call('/dashboard', {}, headers)[0], 200)             # approvals still work
        request = store_tests.AccessStoreTests.request(self, 'terrier')
        self.assertEqual(self.call('/deny', {'requestId': request['requestId']}, headers)[0], 200)
        for route, value in (('/apps', {}), ('/apps/detail', {'service': 'terrier'}), ('/apps/logs', {'service': 'terrier'}),
                             ('/apps/action', {'service': 'terrier', 'action': 'restart'}), ('/apps/invite', {'email': 'a@b.org', 'role': 'canvasser'})):
            with self.subTest(route=route):
                status, body = self.call(route, value, headers)
                self.assertEqual((status, body['error']), (409, 'not_set_up'))
                self.assertIn('MELANGE_ADMIN_COMPOSE', body['message'])

    def test_closing_the_dashboard_records_running_actions_as_interrupted(self):
        self.apps.busy['terrier'] = {'action': 'update', 'since': 1}
        self.server.shutdown()      # what desktop_admin does when the dashboard ends: stop serving, then close
        self.server.server_close()
        self.assertEqual([(e['service'], e['action'], e['outcome']) for e in self.store.app_actions()], [('terrier', 'update', 'interrupted')])

    def test_apps_busy_service_answers_409_and_docker_down_is_reported_not_raised(self):
        headers = self.session()
        self.apps.busy['terrier'] = {'action': 'update', 'since': 1}
        self.assertEqual(self.call('/apps/action', {'service': 'terrier', 'action': 'restart'}, headers)[0], 409)
        self.apps.docker.exe, self.apps.docker.which = None, lambda name: None
        patched = mock.patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name, 'ProgramFiles': self.temp.name})   # no Docker Desktop folder
        patched.start(); self.addCleanup(patched.stop)
        status, view = self.call('/apps', {}, headers)
        self.assertEqual((status, view['docker']['ok']), (200, False))
        self.assertEqual({r['service']: (r['state'], bool(r['busy'])) for r in view['services']},
                         {'melange-desk': ('unknown', False), 'mentat-console': ('unknown', False), 'terrier': ('unknown', True)})   # the running update is still reported
        self.assertEqual(self.call('/apps/logs', {'service': 'terrier'}, headers)[0], 503)


if __name__ == '__main__': unittest.main()
