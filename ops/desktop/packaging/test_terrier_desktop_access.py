"""Synthetic Django sessions through the real native WSGI wrapper."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from http.cookies import SimpleCookie


class DesktopAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='terrier-desktop-access-')
        cls.root = Path(cls.temporary.name)
        sys.path.insert(0, os.environ['TERRIER_TEST_SOURCE'])
        os.environ.update(DJANGO_SETTINGS_MODULE='server.settings', CU_DEPLOYMENT='desktop', CU_LOCAL_ADMIN='0',
                          CU_DATA_DIR=str(cls.root/'data'), CU_STATE_DIR=str(cls.root/'state'), CU_DISTRICT='co-05',
                          CU_SECRET_KEY='synthetic-test-only-not-distributed', CU_ALLOWED_HOSTS='127.0.0.1')
        import django
        django.setup()
        from django.core.management import call_command
        call_command('migrate', interactive=False, verbosity=0)
        from django.core.wsgi import get_wsgi_application
        from server.research.desktop_access import DesktopBoundary
        from desktop_backend import GuardedWSGI
        cls.boundary = DesktopBoundary('co-05', 12345)
        cls.application = GuardedWSGI(get_wsgi_application(), 't'*40, 'terrier', desktop_boundary=cls.boundary)

    @classmethod
    def tearDownClass(cls):
        from django.db import connections
        connections.close_all()
        cls.temporary.cleanup()

    def setUp(self):
        from django.contrib.auth.models import User
        from server.research.models import LoginAttempt, DesktopPrincipalBinding
        DesktopPrincipalBinding.objects.all().delete()
        User.objects.all().delete()
        LoginAttempt.objects.all().delete()
        self.cookies = SimpleCookie()
        self.password = 'Synthetic-Desktop-Account-Password-427!'
        self.user = User.objects.create_user('recipient_'+'3'*32, password=self.password, is_staff=True)
        from server.research.desktop_principal import parse_principal
        self.principal = parse_principal({'schemaVersion':1,'serverId':'11111111-1111-1111-1111-111111111111',
            'deviceId':'22222222-2222-2222-2222-222222222222','principalId':'33333333-3333-3333-3333-333333333333',
            'accountIdentity':self.user.username,'localAccountIdentity':self.user.username,'localUserId':None,
            'districtId':'co-05','role':'local_administrator','mode':'recipient'}, 'co-05')
        DesktopPrincipalBinding.objects.create(server_id=self.principal.server_id,device_id=self.principal.device_id,
            principal_id=self.principal.principal_id,account_identity=self.principal.account_identity,
            local_username=self.user.username,district_id='co-05',mode='recipient',user=self.user)
        self.boundary.principal = self.principal

    def request(self, path, *, method='GET', data=None, headers=None, application=None):
        from django.test import RequestFactory
        environment = RequestFactory().generic(method, path, data=json.dumps(data or {}) if method=='POST' else '',
            content_type='application/json', HTTP_HOST='127.0.0.1:12345', REMOTE_ADDR='127.0.0.1').environ
        environment.update(HTTP_X_DESKTOP_TOKEN='t'*40, HTTP_COOKIE=self.cookies.output(header='', sep=';').strip())
        environment.update(headers or {})
        captured = {}
        def start(status, fields, exc_info=None):
            captured.update(status=int(status.split()[0]), fields=fields)
        result = (application or self.application)(environment, start)
        try:
            body = b''.join(result)
        finally:
            if hasattr(result, 'close'): result.close()
        for name, value in captured['fields']:
            if name.lower()=='set-cookie': self.cookies.load(value)
        try: payload=json.loads(body)
        except ValueError: payload=None
        return captured['status'], payload

    def sign_in(self):
        status, session = self.request('/api/auth/session')
        self.assertEqual(status, 200)
        self.assertFalse(session['authenticated'])
        status, session = self.request('/api/auth/login', method='POST',
            data={'username':self.user.username,'password':self.password}, headers={'HTTP_X_CSRFTOKEN':session['csrf']})
        self.assertEqual(status, 200)
        return session

    def test_fresh_and_legacy_administrators_keep_roles_and_gain_only_authenticated_access(self):
        from django.conf import settings
        self.assertFalse(settings.LOCAL_ADMIN_ACCESS)
        self.sign_in()
        status, session = self.request('/api/auth/session')
        self.assertEqual(status, 200)
        self.assertTrue(session['authenticated'] and session['local_admin_access'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        # Invalid query reaches the real person endpoint after its authorization gate.
        self.assertEqual(self.request('/api/v3/people?unexpected=1')[0], 400)
        self.user.is_superuser=True; self.user.save(update_fields=['is_superuser'])
        self.assertEqual(self.request('/api/v3/people?unexpected=1')[0], 400)

    def test_anonymous_and_viewer_cannot_read_person_data(self):
        self.assertEqual(self.request('/api/v3/people')[0], 401)
        self.sign_in()
        self.user.is_staff=False; self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.request('/api/v3/people')[0], 401)
        self.assertFalse(self.request('/api/auth/session')[1]['local_admin_access'])

    def test_inactive_or_unusable_password_accounts_cannot_gain_access(self):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory
        from server.research.desktop_access import administrator_authorized
        request=RequestFactory().get('/api/v3/people',HTTP_HOST='127.0.0.1:12345',REMOTE_ADDR='127.0.0.1')
        self.assertTrue(self.boundary.authorize_environment(request.META))
        for user in (AnonymousUser(), self.user):
            if user is self.user: user.is_active=False
            request.user=user
            self.assertFalse(administrator_authorized(request))
        self.user.is_active=True;self.user.set_unusable_password();request.user=self.user
        self.assertFalse(administrator_authorized(request))

    def test_token_host_origin_peer_and_forwarded_headers_fail_closed(self):
        self.sign_in()
        for headers in ({'HTTP_X_DESKTOP_TOKEN':''}, {'HTTP_HOST':'evil.invalid:12345'},
                        {'HTTP_HOST':'127.0.0.1:12346'}, {'HTTP_ORIGIN':'http://evil.invalid'},
                        {'REMOTE_ADDR':'192.168.1.20'}, {'HTTP_X_FORWARDED_FOR':'127.0.0.1'},
                        {'HTTP_SEC_FETCH_SITE':'cross-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/v3/people',headers=headers)[0], 403)

    def test_district_and_hosted_modes_cannot_reuse_desktop_boundary(self):
        from django.test import override_settings
        from server.research.desktop_access import DesktopBoundary
        from desktop_backend import GuardedWSGI
        from django.core.wsgi import get_wsgi_application
        self.sign_in()
        wrong=GuardedWSGI(get_wsgi_application(),'t'*40,'terrier',desktop_boundary=DesktopBoundary('or-05',12345))
        self.assertEqual(self.request('/api/v3/people',application=wrong)[0],403)
        for changes in ({'HOSTED':True},{'DESKTOP_BOUNDARY':False}):
            with override_settings(**changes):
                self.assertEqual(self.request('/api/v3/people')[0],403)

    def test_http_headers_and_lookalike_marker_cannot_replace_verified_wrapper(self):
        from django.core.wsgi import get_wsgi_application
        self.sign_in()
        fake={'HTTP_TERRIER_DESKTOP_BOUNDARY':'true','HTTP_X_DESKTOP_TOKEN':'t'*40,
              'terrier.desktop.boundary':(object(),'co-05','http://127.0.0.1:12345')}
        self.assertEqual(self.request('/api/v3/people',headers=fake,application=get_wsgi_application())[0],403)

    def test_authenticated_desktop_write_still_requires_csrf(self):
        self.sign_in()
        self.assertEqual(self.request('/api/v3/walk/scores',method='POST',data={'ids':[]})[0],403)
        csrf=self.request('/api/auth/session')[1]['csrf']
        self.assertEqual(self.request('/api/v3/walk/scores',method='POST',data={'ids':[]},
                                      headers={'HTTP_X_CSRFTOKEN':csrf})[0],400)

    def test_logout_removes_detail_access(self):
        self.sign_in()
        csrf=self.request('/api/auth/session')[1]['csrf']
        self.assertEqual(self.request('/api/auth/logout',method='POST',headers={'HTTP_X_CSRFTOKEN':csrf})[0],200)
        self.assertEqual(self.request('/api/v3/people')[0],401)


if __name__ == '__main__':
    unittest.main()
