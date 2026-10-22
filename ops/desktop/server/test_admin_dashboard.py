import http.client
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit, parse_qs
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admin_dashboard import AdminSession, AdminSessions, LIFETIME, SessionCapacityReached, create_server, STATIC, CLIENT
from desktop_admin import Refused
import test_access_store as store_tests
import test_apps_admin as apps_tests
from desktop_apps_admin import DesktopApps


class AdminDashboardTests(unittest.TestCase):
    def setUp(self):
        store_tests.AccessStoreTests.setUp(self)
        self.apps, self.runner = apps_tests.make_apps(self.temp.name, self.store)
        self.desktop_launches = []
        self.desktop_root = Path(self.temp.name) / 'installed-desktop'
        self.desktop = DesktopApps(self.desktop_root, launch=lambda *args, **kwargs: self.desktop_launches.append((args, kwargs)), version=lambda _: '1.0.16')
        self.server, self.url = create_server(self.store, apps=self.apps, desktop=self.desktop)
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

    def test_desktop_launch_requires_protected_session_and_ignores_no_guards(self):
        exe = self.desktop_root / 'Melange' / 'Melange.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'fixture; never run')
        headers = self.session()
        for route, value in (('/desktop-apps', {}), ('/desktop-apps/open', {'appId': 'melange'})):
            self.assertEqual(self.call(route, value)[0], 401)
            for changed in ({'Origin': 'http://127.0.0.1:1'}, {'X-Admin-CSRF': secrets.token_urlsafe(32)}, {'Host': 'foreign.invalid'}):
                self.assertEqual(self.call(route, value, {**headers, **changed})[0], 403)
        self.assertEqual(self.desktop_launches, [])
        self.assertTrue(self.call('/desktop-apps', {}, headers)[1]['applications'][0]['installed'])
        self.assertEqual(self.call('/desktop-apps/open', {'appId': 'melange', 'args': ['--bad']}, headers)[0], 403)
        self.assertEqual(self.call('/desktop-apps/open', {'appId': '../anything'}, headers)[0], 400)
        self.assertEqual(self.desktop_launches, [])
        self.assertEqual(self.call('/desktop-apps/open', {'appId': 'melange'}, headers)[0], 200)
        self.assertEqual(self.desktop_launches[0][0], ([str(exe)],))

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

    def test_invite_issue_list_and_revoke_routes_share_the_mutation_guard(self):
        headers = self.session()
        status, issued = self.call('/invite-issue', {'label': 'Tester', 'uses': 2, 'days': 3}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(set(issued), {'inviteId', 'code', 'expiresAt', 'maxUses'})
        self.assertEqual(issued['maxUses'], 2)
        self.store.require_invite = True  # The issued code is spent only while enforcement is on.
        self.store.request(dict(requestId=str(uuid.uuid4()), deviceId=str(uuid.uuid4()), appId='melange', platform='win32', arch='x64',
                                displayName='Synthetic recipient', requestSecret=secrets.token_urlsafe(32), inviteCode=issued['code']), '203.0.113.5')
        status, snapshot = self.call('/dashboard', {}, headers)
        listed = [invite for invite in snapshot['invites'] if invite['inviteId'] == issued['inviteId']]
        self.assertEqual((status, [(i['label'], i['uses'], i['maxUses'], i['state']) for i in listed]), (200, [('Tester', 1, 2, 'active')]))
        for secret in (issued['code'], issued['code'].replace('-', ''), 'code_hash'):
            self.assertNotIn(secret, json.dumps(snapshot))
        for bad in ({'label': 'x', 'uses': 1}, {'label': 'x', 'uses': 1, 'days': 1, 'extra': 1}, {'label': '', 'uses': 1, 'days': 7},
                    {'label': 'x', 'uses': True, 'days': 7}, {'label': 'x', 'uses': 0, 'days': 7}, {'label': 'x', 'uses': 1, 'days': 31}):
            self.assertEqual(self.call('/invite-issue', bad, headers)[0], 403)
        for bad in ({}, {'inviteId': str(uuid.uuid4())}, {'inviteId': 'x'}, {'inviteId': issued['inviteId'], 'extra': 1}):
            self.assertEqual(self.call('/invite-revoke', bad, headers)[0], 403)
        for route, value in (('/invite-issue', {'label': 'Refused', 'uses': 1, 'days': 7}), ('/invite-revoke', {'inviteId': issued['inviteId']})):
            for override in ({'Origin': 'http://127.0.0.1:1'}, {'Host': 'rebound.invalid'}, {'X-Admin-CSRF': secrets.token_urlsafe(32)},
                             {'Authorization': 'Bearer ' + secrets.token_urlsafe(32)}):
                self.assertEqual(self.call(route, value, {**headers, **override})[0], 403)
            self.assertEqual(self.call(route, value)[0], 403)
        self.assertEqual(self.call('/invite-revoke', {'inviteId': issued['inviteId']}, headers), (200, {'ok': True}))
        self.assertEqual(self.call('/invite-revoke', {'inviteId': issued['inviteId']}, headers)[0], 403)
        self.assertEqual([i['state'] for i in self.store.dashboard()['invites'] if i['inviteId'] == issued['inviteId']], ['revoked'])
        self.assertEqual([i['label'] for i in self.store.dashboard()['invites']], ['Tester'])

    def test_invites_view_is_wired_and_the_code_is_never_kept(self):
        html = (STATIC/'index.html').read_text(encoding='utf-8')
        script = (STATIC/'app.js').read_text(encoding='utf-8')
        # The Invites view lives in the legacy (access) half of the Melange Admin shell. The Terrier "Invite someone" form
        # already owns #invite-form, so the access invite form must not reuse that id.
        for fragment in ('data-view="invites"', 'id="invites"', 'id="access-invite-form"', 'id="access-invite-label"',
                         'id="access-invite-uses" type="number" min="1" max="100" value="1"',
                         'id="access-invite-days" type="number" min="1" max="30" value="7"', 'id="key-note"'):
            self.assertIn(fragment, html)
        self.assertNotIn('id="invite-form"', html)
        legacy = html[html.index('<div id="legacy-view"'):]
        for fragment in ('id="access-invite-form"', 'id="invites"'):
            self.assertIn(fragment, legacy)
        for fragment in ('/invite-issue', '/invite-revoke', "'invites'", "legacyViews=['requests','grants','activity','invites']",
                         "showKey(await call('/invite-issue',{label,uses,days}),'invite')", "field:'code'"):
            self.assertIn(fragment, script)
        # The one-time code goes through the shared key dialog, which is emptied on close and when the session ends.
        self.assertIn("byId('key-close').addEventListener('click',()=>{byId('key-value').textContent='';", script)
        self.assertIn("byId('key').addEventListener('close',()=>{byId('key-value').textContent='';});", script)
        self.assertIn("bearer=csrf=undefined;byId('key-value').textContent='';byId('key').close();", script)
        self.assertIn("byId('access-invite-form').hidden=true;", script)

    def test_access_views_refresh_themselves_without_clobbering_an_action(self):
        script = (STATIC/'app.js').read_text(encoding='utf-8')
        self.assertIn("setInterval(()=>{if(['requests','grants','activity'].includes(view)&&bearer&&!busy&&!byId('key').open&&"
                      "!document.activeElement?.closest('#legacy-view select, #legacy-view input'))refresh().catch(()=>{});},30000);", script)

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
        # say() writes #apps-message and the detail view hides #apps-list: the message must sit outside the list.
        self.assertLess(html.index('<div id="apps-view">'), html.index('id="apps-message"'))
        self.assertLess(html.index('id="apps-message"'), html.index('<div id="apps-list">'))

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

    @contextmanager
    def held_gets(self, routes):
        """Wait until every connection has a worker, keeping headers incomplete until the test releases them."""
        connections, entered = [], threading.Condition()
        count = 0
        handler = self.server.RequestHandlerClass
        setup = handler.setup

        def observed_setup(request):
            nonlocal count
            setup(request)
            with entered:
                count += 1
                entered.notify_all()

        with mock.patch.object(handler, 'setup', observed_setup):
            try:
                for route in routes:
                    connection = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3)
                    connections.append(connection)
                    connection.sendall(f'GET {route} HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'.encode('ascii'))
                with entered:
                    self.assertTrue(entered.wait_for(lambda: count == len(routes), timeout=3),
                                    'parallel asset connections did not all get a worker')
                yield connections
            finally:
                for connection in connections:
                    connection.close()

    def test_initial_browser_asset_connections_are_all_served(self):
        assets = [('/style.css', STATIC/'style.css'), ('/desktop-chrome.css', CLIENT/'desktop-chrome.css'),
                  *[('/icons/'+app+'.ico', CLIENT/'icons'/(app+'.ico')) for app in ('melange', 'terrier', 'mentat')],
                  ('/app.js', STATIC/'app.js')]
        with self.held_gets([route for route, _ in assets]) as connections:
            # All six are accepted before any request can finish: a fast machine cannot hide a four-worker limit.
            for connection in connections:
                connection.sendall(b'\r\n')
            for connection, (route, path) in zip(connections, assets):
                with self.subTest(route=route):
                    response = http.client.HTTPResponse(connection)
                    try:
                        response.begin()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.read(), path.read_bytes())
                    finally:
                        response.close()

    def test_dashboard_connection_workers_remain_bounded(self):
        with self.held_gets(['/'] * 16):
            connection = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3)
            try:
                connection.sendall(f'GET /app.js HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n\r\n'.encode('ascii'))
                try:
                    response = connection.recv(1)
                except (ConnectionResetError, ConnectionAbortedError):
                    response = b''
                self.assertEqual(response, b'', 'a seventeenth connection exceeded the worker limit')
            finally:
                connection.close()

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
                  '/apps/action': {'service': 'terrier', 'action': 'restart'}, '/apps/invite': {'email': 'a@b.org', 'role': 'canvasser'},
                  '/apps/docker-start': {}}
        calls, launched = len(self.runner.calls), []
        self.apps.docker.launch = lambda argv, **kwargs: launched.append(argv)
        for route, value in routes.items():
            with self.subTest(route=route):
                self.assertEqual(self.call(route, value)[0], 401)
                self.assertEqual(self.call(route, value, {'Authorization': 'Bearer ' + secrets.token_urlsafe(32), 'X-Admin-CSRF': headers['X-Admin-CSRF']})[0], 401)
                self.assertEqual(self.call(route, value, {'Authorization': headers['Authorization']})[0], 403)
                self.assertEqual(self.call(route, value, {**headers, 'X-Admin-CSRF': secrets.token_urlsafe(32)})[0], 403)
                self.assertEqual(self.call(route, value, {**headers, 'Origin': 'http://127.0.0.1:1'})[0], 403)
                self.assertEqual(self.call(route, value, {**headers, 'Host': 'rebound.invalid'})[0], 403)
        self.assertEqual(len(self.runner.calls), calls)     # nothing reached docker
        self.assertEqual((launched, self.store.app_actions()), ([], []))
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

    def test_apps_docker_start_goes_through_the_session_and_launches_only_the_fixed_exe(self):
        headers, launched = self.session(), []
        self.apps.docker.launch = lambda argv, **kwargs: launched.append(argv)
        with tempfile.TemporaryDirectory() as local, mock.patch.dict(os.environ, {'LOCALAPPDATA': local, 'ProgramFiles': local}):
            self.assertEqual(self.call('/apps/docker-start', {}, headers), (409, {'error': 'not_set_up', 'message': 'Docker Desktop was not found on this computer.'}))
            exe = Path(local) / 'Programs' / 'DockerDesktop' / 'Docker Desktop.exe'
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'')
            self.assertEqual(self.call('/apps/docker-start', {'path': 'x.exe'}, headers)[0], 400)
            self.assertEqual(launched, [])
            self.assertEqual(self.call('/apps/docker-start', {}, headers), (200, {'ok': True, 'accepted': True}))
            self.assertEqual(launched, [[str(exe)]])
        self.assertEqual([(a['service'], a['action'], a['outcome']) for a in self.call('/dashboard', {}, headers)[1]['appActions']],
                         [('docker-desktop', 'start', 'succeeded')])

    def test_a_reopen_late_in_the_day_moves_the_end_out_and_no_session_outlives_it(self):
        clock = [0]
        sessions = AdminSessions(clock=lambda: clock[0])
        self.assertEqual(sessions.end, LIFETIME)                      # one session (8 h) plus the 60 s its link may wait
        sessions.exchange({'bootstrap': sessions.bootstrap})
        clock[0] = LIFETIME - 100                                     # near the end of the process's life
        bootstrap = sessions.new_bootstrap()
        self.assertEqual(sessions.end, clock[0] + LIFETIME)           # a reopen gives the process a whole session again
        clock[0] += 59                                                # the link is used at the last moment it works
        second = sessions.exchange({'bootstrap': bootstrap})
        self.assertEqual(sessions.sessions[-1].expires, clock[0] + 8 * 3600)
        self.assertLessEqual(sessions.sessions[-1].expires, sessions.end)
        clock[0] = LIFETIME + 10                                      # past the first end: the reopened session is still good
        sessions.authenticate('Bearer ' + second['bearer'], second['csrf'])
        third = sessions.new_bootstrap()
        sessions.end = clock[0] + 100                                 # a session is never given more than is left of the process
        sessions.exchange({'bootstrap': third})
        self.assertEqual(sessions.sessions[-1].expires, sessions.end)

    def test_a_reopen_after_the_shutdown_timer_fired_or_the_server_closed_is_refused(self):
        self.server.expire()                                         # what the timer does at the end of the 8 h: serving stops
        with self.assertRaises(Refused) as ended:                    # answered BUSY by the reopen, never a window on a closed port
            self.server.fresh_bootstrap_url()
        self.assertNotIsInstance(ended.exception, SessionCapacityReached)   # which the reopen would show as "four sessions are open"
        quiet, _ = create_server(self.store, apps=self.apps)
        self.addCleanup(quiet.server_close)
        self.assertTrue(quiet.fresh_bootstrap_url())
        quiet.server_close()                                         # closing the dashboard closes it for reopens too
        with self.assertRaises(Refused) as ended:
            quiet.fresh_bootstrap_url()
        self.assertNotIsInstance(ended.exception, SessionCapacityReached)

    def test_serving_arms_the_shutdown_and_each_reopen_re_arms_it(self):
        deadline = time.monotonic() + 5
        while self.server.lifetime is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.lifetime.interval, LIFETIME)    # armed when serve_forever started
        made = []

        class Recorded:
            def __init__(self, seconds, stop):
                self.seconds, self.stop, self.started, self.cancelled, self.daemon = seconds, stop, False, False, False
                made.append(self)

            def start(self):
                self.started = True

            def cancel(self):
                self.cancelled = True
        quiet, _ = create_server(self.store, apps=self.apps)         # not serving: nothing but the reopens arms its timer
        self.addCleanup(quiet.server_close)
        quiet.make_timer = Recorded
        for _ in range(3):                                           # with the one create_server issued: four sessions
            quiet.fresh_bootstrap_url()
        self.assertEqual([(t.seconds, t.started, t.daemon, t.cancelled) for t in made],   # what each timer's stop does: the test below
                         [(LIFETIME, True, True, True)] * 2 + [(LIFETIME, True, True, False)])
        with self.assertRaises(SessionCapacityReached):
            quiet.fresh_bootstrap_url()
        self.assertEqual((len(made), made[-1].cancelled), (3, False))   # the cap of four holds, and a refused reopen moves nothing
        self.server.shutdown()
        self.server.server_close()
        self.assertTrue(self.server.lifetime.finished.is_set())      # closing the dashboard cancels the timer

    def test_a_shutdown_timer_replaced_while_it_fired_does_not_end_the_reopened_dashboard(self):
        made, stopped = [], []

        class Recorded:
            def __init__(self, seconds, stop):
                self.stop, self.daemon = stop, False
                made.append(self)

            def start(self): pass

            def cancel(self): pass   # too late for a timer that has already fired
        quiet, _ = create_server(self.store, apps=self.apps)
        self.addCleanup(quiet.server_close)
        quiet.make_timer, quiet.shutdown = Recorded, lambda: stopped.append(True)   # not serving: record the stop
        quiet.fresh_bootstrap_url()
        quiet.fresh_bootstrap_url()                                  # a reopen replaces the first timer under the lock ...
        made[0].stop()                                               # ... which had fired and was waiting for that lock
        self.assertEqual((quiet.closed, stopped), (False, []))
        self.assertTrue(quiet.fresh_bootstrap_url())                 # the reopened window keeps its dashboard
        made[-1].stop()                                              # the timer that is current still ends it
        self.assertEqual((quiet.closed, stopped), (True, [True]))


# Runs the page's own app.js under node against the page's own index.html, with a scripted fetch and a minimal DOM (the
# page keeps no state outside it): {click, routes: {route or '*': {reject, times?} | {status, body}}, overview, pagehide} -> what it shows.
PAGE = r'''
const fs=require('fs'),vm=require('vm'),[appPath,htmlPath,raw]=process.argv.slice(-3),scenario=JSON.parse(raw),html=fs.readFileSync(htmlPath,'utf8');
class El{
  constructor(id){this.id=id;this.hidden=false;this.disabled=false;this.textContent='';this.dataset={};this.children=[];this.listeners={};this.inserted=[];this.className='';this.value='all';this.style={};this.classList={add(){},remove(){},toggle(){return false;}};}
  get firstChild(){return this.children[0]||null;}
  append(...kids){this.children.push(...kids);}
  replaceChildren(...kids){this.children=kids;}
  before(node){this.inserted.push(node);}
  addEventListener(type,fn){(this.listeners[type]=this.listeners[type]||[]).push(fn);}
  setAttribute(){}removeAttribute(){}close(){}showModal(){}focus(){}
  querySelector(){return null;}querySelectorAll(){return [];}closest(){return null;}
}
const ids=new Map();
for(const m of html.matchAll(/<[a-z0-9]+\b[^>]*>/g)){const id=/ id="([^"]+)"/.exec(m[0]);if(id){const e=new El(id[1]);e.hidden=/\shidden[\s>]/.test(m[0]);e.disabled=/\sdisabled[\s>]/.test(m[0]);ids.set(id[1],e);}}
const row={service:'melange-desk',name:'Melange desk',state:'stopped',terrier:false,backup:'not_set_up',lastBackup:null,busy:null,last:null,actions:['start','logs'],since:1800000000,exitCode:0,update:false,version:null,readyVersion:null,disk:null,openUrl:null,note:null};
const answers={'/session':{bearer:'B',csrf:'C'},
  '/desktop-apps':{applications:[{appId:'melange',name:'Melange',installed:true,version:'1.0.16',path:'C:/installed/Melange/Melange.exe'}]},'/desktop-apps/open':{ok:true},
  '/dashboard':{serverTime:1,notifications:{pendingRequests:0},requests:[],grants:[],activity:[],invites:[],appActions:[]},
  '/apps':Object.assign({checkedAt:1800000000,docker:{ok:true,message:null},notice:null,stale:false,services:[row],activity:[]},scenario.overview),
  '/apps/detail':{checkedAt:1800000000,notice:null,stale:false,service:row,accounts:{supported:false,listing:false,roles:[]},health:{status:'none',checkedAt:null},started:null,uptime:null,image:{update:false,pinnedByDigest:true,version:null,readyVersion:null},storage:[]},
  '/apps/logs':{service:row.service,lines:[]},'/apps/action':{ok:true,accepted:true},'/apps/docker-start':{ok:true,accepted:true}};
let live=false;const calls=[],startupCalls=[],held=[];
const realTimeout=setTimeout;let rechecks=0,rejected=false;const closes=[],pagehide=[];
globalThis.setTimeout=(fn,ms,...rest)=>{if(live&&ms===10000&&rejected&&!rechecks){rechecks++;ms=0;}return realTimeout(fn,ms,...rest);};   // the first 10 s timer started right after a status-less rejection (the page's re-check) runs at once
globalThis.fetch=async (route,init)=>{
  const initial=!live,routes=(initial?scenario.startupRoutes:scenario.routes)||{};let spec=routes[route]||routes['*'];
  if(initial){startupCalls.push(route);if(scenario.hold===route)await new Promise(resolve=>held.push(resolve));}
  if(live)calls.push(route);
  if(route==='/session-close')closes.push(init.headers.Authorization);
  rejected=false;
  if(spec&&spec.times!==undefined&&spec.times--<=0)spec=undefined;   // {times:n}: only the first n requests are scripted
  if(spec&&spec.reject){rejected=spec.reject!=='AbortError';throw spec.reject==='AbortError'?new DOMException('aborted','AbortError'):new TypeError('Failed to fetch');}
  const status=spec?spec.status:200,body=spec?spec.body:answers[route];
  return {ok:status<400,status,json:async()=>body};
};
Object.assign(globalThis,{document:{getElementById:id=>ids.get(id)||null,createElement:t=>new El(t),createElementNS:()=>new El('ns'),createTextNode:t=>t,querySelectorAll:()=>[],querySelector:()=>null,activeElement:null,body:new El('body'),title:''},
  location:{hash:'#bootstrap=boot'},history:{replaceState(){}},addEventListener(type,fn){if(type==='pagehide')pagehide.push(fn);},close(){},scrollTo(){},scrollX:0,scrollY:0,CSS:{escape:s=>s},setInterval:()=>0});
vm.runInThisContext(fs.readFileSync(appPath,'utf8'),{filename:'app.js'});
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const find=(node,key)=>{if(node.dataset&&node.dataset.key===key)return node;for(const c of node.children||[]){const f=find(c,key);if(f)return f;}return null;};
const snap=()=>{const text=id=>ids.get(id).textContent,line=ids.get('apps-table').inserted.slice(-1)[0],b=ids.get('start-docker');
  return {expired:!ids.get('expired').hidden,shell:!ids.get('shell').hidden,title:text('expired-title'),notice:text('expired-text'),table:!ids.get('apps-table').hidden,
    message:ids.get('apps-message').hidden?'':text('apps-message'),stale:line&&!line.hidden?line.textContent:'',button:{present:!!b,shown:!!b&&!b.hidden,disabled:!!b&&b.disabled},
    banner:{shown:!ids.get('unanswered').hidden,text:text('unanswered')},subtitle:text('apps-sub'),accessStatus:text('status'),serviceRows:ids.get('apps-body').children.length,
    desktopRows:ids.get('desktop-apps-body').children.length,desktopStatus:text('desktop-status'),
    controls:{refreshDisabled:ids.get('refresh').disabled,endDisabled:ids.get('end-session').disabled,checkDisabled:ids.get('check-now').disabled}};};
(async()=>{
  await wait(150);const startup=snap();if(scenario.endWhileLoading)await ids.get('end-session').listeners.click[0]();for(const resume of held)resume();await wait(50);live=true;const before=snap();
  const target=['check-now','start-docker','desktop-refresh'].includes(scenario.click)?ids.get(scenario.click):find(ids.get('apps-body'),scenario.click)||find(ids.get('desktop-apps-body'),scenario.click);
  if(!target)throw new Error('no control '+scenario.click+'; the page says: '+before.message);
  await target.listeners.click[0]();await wait(150);
  const seen=[...calls],shown=snap();
  if(scenario.pagehide){for(const fn of pagehide)fn();await wait(50);}   // the window closing
  process.stdout.write(JSON.stringify({...shown,startup,startupCalls,before,calls:seen,rechecks,closes}),()=>process.exit(0));
})().catch(e=>{process.stderr.write(String(e&&e.stack||e)+'\n');process.exit(1);});
'''
NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'node is required to run the page')
class AdminPageTests(unittest.TestCase):
    def page(self, click, routes=None, overview=None, pagehide=False, hold=None, startup_routes=None, end_while_loading=False):
        scenario = json.dumps({'click': click, 'routes': routes or {}, 'overview': overview or {}, 'pagehide': pagehide,
                               'hold': hold, 'startupRoutes': startup_routes or {}, 'endWhileLoading': end_while_loading})
        done = subprocess.run([NODE, '-e', PAGE, str(STATIC/'app.js'), str(STATIC/'index.html'), scenario], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    NOT_ANSWERING = 'Melange Admin is not answering. Trying again\u2026 If this keeps up, open Melange Admin again from its icon.'

    def test_desktop_apps_load_while_docker_status_is_pending(self):
        page = self.page('desktop-melange', hold='/apps')
        self.assertEqual(page['startup']['desktopRows'], 1)
        self.assertEqual(page['calls'], ['/desktop-apps/open'])
        self.assertEqual(page['desktopStatus'], 'Melange launch requested. Check its desktop window.')

    def test_desktop_open_failure_does_not_claim_success_or_open_a_web_service(self):
        page = self.page('desktop-melange', routes={'/desktop-apps/open': {'status':503,'body':{'message':'Windows blocked this app'}}})
        self.assertEqual(page['calls'], ['/desktop-apps/open'])
        self.assertEqual(page['desktopStatus'], 'Windows blocked this app')

    def test_session_controls_unlock_while_either_initial_view_is_still_loading(self):
        for route in ('/apps', '/dashboard'):
            with self.subTest(route=route):
                page = self.page('check-now', hold=route)
                startup = page['startup']
                self.assertEqual((startup['expired'], startup['shell']), (False, True))
                self.assertEqual(startup['controls'], {'refreshDisabled': False, 'endDisabled': False, 'checkDisabled': False})
                self.assertEqual(page['startupCalls'], ['/session', '/dashboard', '/desktop-apps', '/apps'])
                if route == '/apps':
                    self.assertEqual((startup['subtitle'], startup['serviceRows']), ('Checking local services…', 0))
                    self.assertTrue(startup['accessStatus'].startswith('Updated '))   # access requests finished without Docker
                else:
                    self.assertEqual((startup['accessStatus'], startup['serviceRows']), ('Loading access requests…', 1))
                    self.assertIn('Last checked', startup['subtitle'])               # Docker finished without the access store

    def test_first_services_failure_replaces_the_opening_session_message(self):
        for response, message in (({'reject': 'AbortError'}, 'Could not read the services. Choose Check now to try again.'),
                                  ({'status': 503, 'body': {'error': 'docker_unavailable', 'message': 'Docker took too long to answer.'}},
                                   'Docker took too long to answer.')):
            with self.subTest(response=response):
                startup = self.page('check-now', startup_routes={'/apps': response})['startup']
                self.assertEqual((startup['expired'], startup['shell']), (False, True))
                self.assertEqual((startup['subtitle'], startup['message']), ('Local services have not been checked yet.', message))
                self.assertFalse(startup['controls']['endDisabled'])

    def test_ending_session_while_initial_view_loads_ignores_its_late_response(self):
        for route in ('/apps', '/dashboard'):
            with self.subTest(route=route):
                page = self.page('check-now', hold=route, end_while_loading=True)
                self.assertEqual(page['closes'], ['Bearer B'])
                self.assertEqual(page['before']['serviceRows'], 0)
                self.assertTrue(page['before']['subtitle'].startswith('Session ended.'))
                self.assertTrue(page['before']['accessStatus'].startswith('Session ended.'))
                self.assertEqual(page['before']['controls']['endDisabled'], True)

    def test_ending_session_while_initial_view_loads_ignores_its_late_rejection(self):
        for route in ('/apps', '/dashboard'):
            with self.subTest(route=route):
                page = self.page('check-now', hold=route, startup_routes={route: {'reject': 'TypeError'}}, end_while_loading=True)
                self.assertEqual(page['closes'], ['Bearer B'])
                self.assertFalse(page['banner']['shown'])
                self.assertEqual(page['rechecks'], 0)
                self.assertEqual(page['serviceRows'], 0)
                self.assertTrue(page['subtitle'].startswith('Session ended.'))
                self.assertTrue(page['accessStatus'].startswith('Session ended.'))

    def test_a_dashboard_that_does_not_answer_keeps_the_session_and_says_so(self):
        dead = {'*': {'reject': 'TypeError'}}   # fetch rejects with no HTTP status, every time: busy for good, or ended
        for click in ('start-melange-desk', 'check-now', 'name-melange-desk', 'logs-melange-desk'):
            with self.subTest(click=click):
                page = self.page(click, dead, pagehide=True)
                self.assertEqual((page['expired'], page['shell']), (False, True))                  # never the ended page
                self.assertEqual(page['banner'], {'shown': True, 'text': self.NOT_ANSWERING})
                for text in (page['message'], page['stale']):
                    self.assertNotIn('Check Docker', text)                                           # and never a Docker text
                    self.assertNotIn('Docker is slow', text)
                self.assertEqual(page['rechecks'], 1)                                                # it asks again on its own
                self.assertEqual(page['closes'], ['Bearer B'])                                       # the bearer is kept: closing the window still ends the session

    def test_a_busy_dashboard_that_answers_again_clears_the_banner(self):
        busy = {'reject': 'TypeError', 'times': 1}   # one request gets no answer, the next one does
        for click, route, again in (('check-now', '/apps', '/apps'), ('start-melange-desk', '/apps/action', '/apps'), ('name-melange-desk', '/apps/detail', '/apps/detail')):
            with self.subTest(click=click):
                page = self.page(click, {route: busy}, pagehide=True)
                self.assertEqual((page['expired'], page['shell']), (False, True))
                self.assertEqual(page['banner']['shown'], False)                                     # cleared by the first answer
                self.assertEqual((page['message'], page['stale'], page['rechecks']), ('', '', 1))
                self.assertEqual(page['calls'][:2], [route, again])                                  # not asked twice in a row: the re-check is what answered
                self.assertEqual(page['closes'], ['Bearer B'])

    def test_only_a_401_ends_the_session(self):
        for click, route in (('check-now', '/apps'), ('start-melange-desk', '/apps/action'), ('name-melange-desk', '/apps/detail')):
            with self.subTest(click=click):
                page = self.page(click, {route: {'status': 401, 'body': {'error': 'session_required'}}}, pagehide=True)
                self.assertEqual((page['expired'], page['shell'], page['title']), (True, False, 'This session has ended'))
                self.assertIn('Open Melange Admin again', page['notice'])
                self.assertEqual((page['banner']['shown'], page['closes']), (False, []))             # nothing left to close

    def test_our_own_timeout_is_not_a_dashboard_that_has_ended(self):
        page = self.page('check-now', {'/apps': {'reject': 'AbortError'}})
        self.assertEqual((page['expired'], page['banner']['shown'], page['rechecks'], page['calls']), (False, False, 0, ['/apps']))   # an abort is our own timeout, not silence
        self.assertIn('Docker is slow to answer', page['stale'])     # the list stays and the page tries again
        page = self.page('start-melange-desk', {'/apps/action': {'reject': 'AbortError'}})
        self.assertEqual((page['expired'], page['message']), (False, 'The action could not start. Check Docker, then try again.'))

    def test_the_page_shows_what_the_server_said_and_busy_only_for_busy(self):
        for name, response, expected in (
                ('not set up', {'status': 409, 'body': {'error': 'not_set_up', 'message': 'Not set up in Docker'}}, 'Not set up in Docker'),
                ('busy', {'status': 409, 'body': {'error': 'busy', 'message': 'another action is already running for this service'}},
                 'Another action is already running for this service.'),
                ('docker slow', {'status': 503, 'body': {'error': 'docker_unavailable', 'message': 'Docker took too long to answer.'}}, 'Docker took too long to answer.'),
                ('no message', {'status': 500, 'body': {}}, 'The action could not start. Check Docker, then try again.')):
            with self.subTest(name):
                page = self.page('start-melange-desk', {'/apps/action': response})
                self.assertEqual((page['expired'], page['message']), (False, expected))

    def test_docker_going_down_under_a_kept_list_says_so_and_offers_to_start_it(self):
        message = 'Docker is not running. Start Docker Desktop, then check again.'
        kept = {'stale': True, 'staleReason': message, 'services': [], 'docker': {'ok': True, 'message': message, 'canStart': True}}
        page = self.page('start-docker', overview=kept)
        before = page['before']
        self.assertEqual((before['table'], before['button']), (True, {'present': True, 'shown': True, 'disabled': False}))   # the list stays, with the button
        self.assertIn(message, before['stale'])
        self.assertNotIn('slow', before['stale'])
        self.assertEqual(page['calls'], ['/apps/docker-start'])
        self.assertIn('Starting Docker Desktop', page['stale'])
        self.assertEqual(page['button'], {'present': True, 'shown': True, 'disabled': True})      # waiting for Docker, though the kept list says everything runs
        slow = {'stale': True, 'staleReason': 'Docker took too long to answer.', 'services': [], 'docker': {'ok': True, 'message': None, 'canStart': False}}
        before = self.page('check-now', overview=slow)['before']
        self.assertIn('Docker is slow to answer', before['stale'])                                # a timeout is not "not running"
        self.assertEqual(before['button']['shown'], False)

    def test_docker_down_page_offers_start_docker_desktop_and_waits_for_docker(self):
        message = 'Docker is not running. Start Docker Desktop, then check again.'
        down = {'docker': {'ok': False, 'message': message, 'canStart': True}, 'services': []}
        page = self.page('start-docker', overview=down)
        self.assertEqual((page['before']['table'], page['before']['message'], page['before']['button']), (False, message, {'present': True, 'shown': True, 'disabled': False}))
        self.assertEqual(page['calls'], ['/apps/docker-start'])
        self.assertTrue(page['message'].startswith('Starting Docker Desktop'))
        self.assertEqual(page['button'], {'present': True, 'shown': True, 'disabled': True})     # once, then the page polls until Docker answers
        refused = self.page('start-docker', {'/apps/docker-start': {'status': 409, 'body': {'error': 'not_set_up', 'message': 'Docker Desktop was not found on this computer.'}}}, down)
        self.assertEqual((refused['message'], refused['button']['disabled']), ('Docker Desktop was not found on this computer.', False))
        for overview in ({'docker': {'ok': False, 'message': message, 'canStart': False}, 'services': []}, {}):   # no button when it cannot work, or Docker is up
            with self.subTest(overview=overview):
                self.assertFalse(self.page('check-now', overview=overview)['before']['button']['shown'])


if __name__ == '__main__': unittest.main()
