import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
import sqlite3
from contextlib import closing
from unittest.mock import patch


class OwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='terrier-owner-test-')
        root = Path(cls.temporary.name)
        cls.state = root/'state'
        sys.path.insert(0, os.environ['TERRIER_TEST_SOURCE'])
        os.environ.update(DJANGO_SETTINGS_MODULE='server.settings', CU_LOCAL_ADMIN='0', CU_STATE_DIR=str(root/'state'),
                          TERRIER_LOCAL_DIR=str(root/'data'), DJANGO_SECRET_KEY='test-only-not-a-runtime-secret')
        (root/'state').mkdir()
        import django
        django.setup()
        from django.core.management import call_command
        call_command('migrate', interactive=False, verbosity=0)

    @classmethod
    def tearDownClass(cls):
        from django.db import connections
        connections.close_all()
        cls.temporary.cleanup()

    def setUp(self):
        from django.contrib.auth.models import User, Group
        from server.research.models import Invitation, AuditEvent
        User.objects.all().delete()
        Group.objects.all().delete()
        Invitation.objects.all().delete()
        AuditEvent.objects.all().delete()

    def test_invitation_only_empty_accounts_and_hash_at_rest(self):
        from django.conf import settings
        from django.contrib.auth.models import User
        from server.research.models import Invitation
        from terrier_owner import first_owner_invitation
        self.assertFalse(settings.LOCAL_ADMIN_ACCESS)
        first = first_owner_invitation()
        self.assertTrue(first.startswith('/?invite='))
        self.assertEqual(User.objects.count(), 0)
        token = first.partition('=')[2]
        invitation = Invitation.objects.get(token_hash=hashlib.sha256(token.encode()).hexdigest())
        self.assertTrue(invitation.administrator)
        self.assertFalse(invitation.reset)
        self.assertNotIn(token, repr(invitation.__dict__))
        second = first_owner_invitation()
        self.assertNotEqual(first, second)
        invitation.refresh_from_db()
        from django.utils import timezone
        self.assertLessEqual(invitation.expires_at, timezone.now())
        User.objects.create_user(username='existing-disabled', password='test-not-distributed', is_active=False)
        count = Invitation.objects.count()
        self.assertIsNone(first_owner_invitation())
        self.assertEqual(Invitation.objects.count(), count)

    def legacy_owner(self, password=None):
        from django.contrib.auth.models import User
        return User.objects.create_user(username='admin', password=password, is_active=True, is_staff=True, is_superuser=True)

    def test_legacy_first_password_preserves_identity_roles_and_saved_views(self):
        from datetime import timedelta
        from django.contrib.auth.models import User, Group
        from django.test import Client
        from django.utils import timezone
        from server.research.models import Invitation, SavedView, AuditEvent
        from terrier_owner import legacy_owner_invitation
        user = self.legacy_owner()
        group = Group.objects.create(name='retained-role')
        user.groups.add(group)
        view = SavedView.objects.create(owner=user, name='retained-view', config={'fixture': True})
        original_pk, original_password = user.pk, user.password
        before_backups = set((self.state/'migration-backups').glob('*.sqlite3'))
        first = legacy_owner_invitation(self.state, 'admin')
        token = first.partition('=')[2]
        invite = Invitation.objects.get(token_hash=hashlib.sha256(token.encode()).hexdigest())
        self.assertTrue(invite.reset)
        self.assertLessEqual(invite.expires_at, timezone.now()+timedelta(minutes=16))
        self.assertNotIn(token, repr(invite.__dict__))
        event = AuditEvent.objects.get(action='account.desktop_first_password_invited')
        self.assertEqual(event.actor, 'admin')
        self.assertEqual(event.detail, {})
        self.assertNotIn(token, repr(event.__dict__))
        backup, = set((self.state/'migration-backups').glob('*.sqlite3')) - before_backups
        with closing(sqlite3.connect(backup)) as database:
            self.assertEqual(database.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            self.assertEqual(database.execute('SELECT id,password FROM auth_user').fetchone(), (original_pk, original_password))
            self.assertEqual(database.execute('SELECT COUNT(*) FROM research_invitation').fetchone(), (0,))
        second = legacy_owner_invitation(self.state, 'admin')
        self.assertNotEqual(first, second)
        invite.refresh_from_db()
        self.assertLessEqual(invite.expires_at, timezone.now())
        client = Client()
        password = 'Synthetic-Only-Owner-Password-7624!'
        response = client.post('/api/auth/accept', {'token':second.partition('=')[2], 'password':password}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        view.refresh_from_db()
        self.assertEqual(user.pk, original_pk)
        self.assertTrue(user.is_active and user.is_staff and user.is_superuser)
        self.assertEqual(list(user.groups.values_list('pk', flat=True)), [group.pk])
        self.assertEqual(view.owner_id, original_pk)
        self.assertTrue(user.check_password(password))
        count = Invitation.objects.count()
        final_password = user.password
        self.assertIsNone(legacy_owner_invitation(self.state, 'admin'))
        user.refresh_from_db()
        self.assertEqual(user.password, final_password)
        self.assertEqual(Invitation.objects.count(), count)
        client.logout()
        login = client.post('/api/auth/login', {'username':'admin','password':password}, content_type='application/json')
        self.assertEqual(login.status_code, 200, login.content)

    def test_legacy_usable_password_never_gets_reset(self):
        from server.research.models import Invitation
        from terrier_owner import legacy_owner_invitation
        user = self.legacy_owner('Existing-Synthetic-Password-8624!')
        previous = user.password
        self.assertIsNone(legacy_owner_invitation(self.state, 'admin'))
        user.refresh_from_db()
        self.assertEqual(previous, user.password)
        self.assertFalse(Invitation.objects.exists())

    def test_legacy_ambiguous_or_restricted_accounts_are_refused(self):
        from django.contrib.auth.models import User
        from terrier_owner import legacy_owner_invitation
        user = self.legacy_owner()
        other = User.objects.create_user(username='other', password=None)
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            legacy_owner_invitation(self.state, 'admin')
        other.delete()
        user.is_active = False
        user.save()
        with self.assertRaisesRegex(RuntimeError, 'restricted'):
            legacy_owner_invitation(self.state, 'admin')

    def test_legacy_failed_invitation_restores_verified_backup(self):
        from server.research.models import Invitation
        from server.research import views
        from terrier_owner import legacy_owner_invitation
        user = self.legacy_owner()
        original_password = user.password
        original = views.create_invitation
        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('synthetic interruption')
        with patch.object(views, 'create_invitation', fail_after_write):
            with self.assertRaisesRegex(RuntimeError, 'synthetic interruption'):
                legacy_owner_invitation(self.state, 'admin')
        user.refresh_from_db()
        self.assertEqual(user.password, original_password)
        self.assertFalse(Invitation.objects.exists())

    def test_legacy_secret_is_reused_and_wrong_roots_refused(self):
        from terrier_owner import legacy_secret_path
        secret = self.state/'.secret'
        secret.write_text('synthetic-existing-secret-do-not-distribute', encoding='utf-8')
        self.assertEqual(legacy_secret_path(self.state, self.state, 'admin'), secret)
        self.assertEqual(secret.read_text(), 'synthetic-existing-secret-do-not-distribute')
        with self.assertRaisesRegex(ValueError, 'same district'):
            legacy_secret_path(self.state, self.state/'other', 'admin')


if __name__ == '__main__':
    unittest.main()
