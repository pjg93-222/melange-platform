"""Approved recipient/owner identity tests through the real guarded Django stack."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_terrier_desktop_access as boundary_tests


class PrincipalTests(boundary_tests.DesktopAccessTests):
    def fresh(self):
        from django.contrib.auth.models import User
        from server.research.models import DesktopPrincipalBinding
        from server.research.desktop_principal import prepare_principal
        DesktopPrincipalBinding.objects.all().delete()
        User.objects.all().delete()
        return prepare_principal(self.principal, self.root/'state')

    def accept(self, token, password=None):
        csrf = self.request('/api/auth/session')[1]['csrf']
        return self.request('/api/auth/accept',method='POST',data={'token':token,'password':password or self.password},
                            headers={'HTTP_X_CSRFTOKEN':csrf})

    def test_no_grant_stops_backend_before_creating_state_or_owner(self):
        from desktop_backend import run
        data = self.root/'refused-data'; state = self.root/'refused-state'
        options = SimpleNamespace(app='terrier',source_root=Path(os.environ['TERRIER_TEST_SOURCE']),
                                  data_root=data,state_root=state,district='co-05',legacy_owner=None)
        with patch.dict(os.environ, {'DESKTOP_ACCESS_PRINCIPAL':''}):
            with self.assertRaises(ValueError): run(options, 't'*40)
        self.assertFalse(data.exists()); self.assertFalse(state.exists())

    def test_fresh_invitation_is_exact_and_password_transaction_binds_actual_pk(self):
        from django.contrib.auth.models import User
        from server.research.models import DesktopPrincipalBinding, Invitation
        path = self.fresh()
        self.assertTrue(path.startswith('/#invite=')); self.assertNotIn('?',path)
        token=path.split('=',1)[1]
        binding=DesktopPrincipalBinding.objects.get()
        self.assertIsNone(binding.user_id); self.assertEqual(User.objects.count(),0)
        invite=Invitation.objects.get(token_hash=binding.invitation_hash)
        self.assertEqual(invite.username,self.principal.account_identity)
        self.assertTrue(invite.administrator); self.assertFalse(invite.reset)
        self.assertEqual(self.request('/api/v3/meta')[0],401)
        self.assertEqual(self.request('/api/auth/session')[1]['account_identity'],self.principal.account_identity)
        self.assertEqual(self.accept(token,'password1234')[0],400)
        binding.refresh_from_db(); invite.refresh_from_db()
        self.assertIsNone(binding.user_id); self.assertIsNone(invite.used_at); self.assertEqual(User.objects.count(),0)
        self.assertEqual(self.accept(token)[0],200)
        binding.refresh_from_db(); user=User.objects.get()
        self.assertEqual(binding.user_id,user.pk); self.assertTrue(user.is_staff); self.assertFalse(user.is_superuser)
        self.assertEqual(binding.invitation_hash,''); self.assertTrue(user.check_password(self.password))
        self.assertTrue(self.request('/api/auth/session')[1]['authenticated'])
        self.assertEqual(self.accept(token)[0],400)
        from server.research.desktop_principal import prepare_principal
        self.assertIsNone(prepare_principal(self.principal,self.root/'state'))

    def test_unrelated_invitation_account_and_existing_session_are_refused(self):
        from django.contrib.auth.models import User
        from server.research.views import create_invitation
        from django.test import Client
        from django.conf import settings
        self.fresh()
        wrong=create_invitation('unrelated',administrator=True).partition('=')[2]
        self.assertEqual(self.accept(wrong)[0],403); self.assertFalse(User.objects.exists())
        other=User.objects.create_user('unrelated',password=self.password,is_staff=True)
        client=Client(); client.force_login(other)
        self.cookies[settings.SESSION_COOKIE_NAME]=client.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertFalse(self.request('/api/auth/session')[1]['authenticated'])
        self.assertEqual(self.request('/api/v3/meta')[0],401)
        csrf=self.request('/api/auth/session')[1]['csrf']
        self.assertEqual(self.request('/api/auth/login',method='POST',data={'username':'unrelated','password':self.password},headers={'HTTP_X_CSRFTOKEN':csrf})[0],401)

    def test_existing_username_is_never_adopted_or_upgraded(self):
        from server.research.models import DesktopPrincipalBinding
        from server.research.desktop_principal import prepare_principal
        DesktopPrincipalBinding.objects.all().delete()
        original=(self.user.pk,self.user.password,self.user.is_staff,self.user.is_superuser)
        with self.assertRaises(ValueError): prepare_principal(self.principal,self.root/'state')
        self.user.refresh_from_db()
        self.assertEqual(original,(self.user.pk,self.user.password,self.user.is_staff,self.user.is_superuser))
        self.assertFalse(DesktopPrincipalBinding.objects.exists())

    def test_password_acceptance_binding_failure_rolls_back_account_and_receipt(self):
        from server.research.models import DesktopPrincipalBinding, Invitation
        from django.contrib.auth.models import User
        token=self.fresh().split('=',1)[1]
        with patch('server.research.desktop_principal.bind_accepted_user',side_effect=ValueError('synthetic binding failure')):
            self.assertEqual(self.accept(token)[0],400)
        self.assertFalse(User.objects.exists()); self.assertIsNone(DesktopPrincipalBinding.objects.get().user_id)
        self.assertIsNone(Invitation.objects.get(token_hash=hashlib.sha256(token.encode()).hexdigest()).used_at)

    def test_explicit_owner_preserves_existing_identity_password_roles_and_saved_views(self):
        from django.contrib.auth.models import User,Group
        from server.research.models import DesktopPrincipalBinding,SavedView
        from server.research.desktop_principal import parse_principal,prepare_principal
        DesktopPrincipalBinding.objects.all().delete(); User.objects.all().delete()
        user=User.objects.create_user('admin',password=self.password,is_staff=True,is_superuser=True)
        group=Group.objects.get_or_create(name='synthetic-existing')[0]; user.groups.add(group)
        SavedView.objects.create(owner=user,name='fixture',config={'kept':True})
        data={'schemaVersion':1,'serverId':self.principal.server_id,'deviceId':self.principal.device_id,
              'principalId':self.principal.principal_id,'accountIdentity':self.principal.account_identity,
              'localAccountIdentity':'admin','localUserId':str(user.pk),'districtId':'co-05',
              'role':'local_administrator','mode':'existing-owner'}
        principal=parse_principal(data,'co-05'); before=(user.pk,user.password,user.is_staff,user.is_superuser)
        self.assertIsNone(prepare_principal(principal,self.root/'state'))
        self.assertEqual(DesktopPrincipalBinding.objects.get().user_id,user.pk)
        user.refresh_from_db();self.assertEqual(before,(user.pk,user.password,user.is_staff,user.is_superuser))
        self.assertEqual(list(user.groups.values_list('pk',flat=True)),[group.pk]); self.assertEqual(SavedView.objects.get().config,{'kept':True})
        DesktopPrincipalBinding.objects.all().delete()
        with self.assertRaises(ValueError):prepare_principal(parse_principal({**data,'localUserId':str(user.pk+100)},'co-05'),self.root/'state')
        self.assertFalse(DesktopPrincipalBinding.objects.exists())

    def test_passwordless_owner_is_never_asked_for_a_password(self):
        from django.contrib.auth.models import User
        from server.research.models import DesktopPrincipalBinding,Invitation
        from server.research.desktop_principal import parse_principal,prepare_principal
        DesktopPrincipalBinding.objects.all().delete(); User.objects.all().delete()
        user=User.objects.create_user('admin',is_staff=True)   # no usable password, like the migrated owner
        User.objects.create_user('recipient_'+'4'*32,password=self.password,is_staff=True)   # beside another account
        principal=parse_principal({'schemaVersion':1,'serverId':self.principal.server_id,'deviceId':self.principal.device_id,
            'principalId':self.principal.principal_id,'accountIdentity':self.principal.account_identity,
            'localAccountIdentity':'admin','localUserId':str(user.pk),'districtId':'co-05',
            'role':'local_administrator','mode':'existing-owner'},'co-05')
        self.boundary.principal=principal; before=user.password; invitations=Invitation.objects.count()
        self.assertIsNone(prepare_principal(principal,self.root/'state'))   # no invitation launch path
        self.assertEqual(Invitation.objects.count(),invitations)
        status,session=self.request('/api/auth/session')
        self.assertEqual((status,session['authenticated'],session['username'],session['password_lock_disabled']),(200,True,'admin',True))
        self.assertEqual(self.request('/api/accounts')[0],200)   # an administrator-only route, no data needed
        self.assertEqual(self.request('/api/v3/meta',headers={'HTTP_X_DESKTOP_TOKEN':'u'*40})[0],403)   # the launch token still gates
        self.assertEqual(self.request('/api/v3/meta',headers={'REMOTE_ADDR':'192.168.1.20'})[0],403)    # and the loopback boundary
        self.assertIsNone(prepare_principal(principal,self.root/'state'))
        user.refresh_from_db(); self.assertEqual(user.password,before)   # the account is unchanged

    def test_prelogin_static_interface_and_minimal_health_remain_available(self):
        self.fresh()
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/brand/terrier-icon-hq.png')[0],200)
        self.assertEqual(self.request('/brand/terrier-loading-hq.mp4')[0],200)
        self.assertEqual(self.request('/api/health'),(200,{'status':'ok'}))
        for path in ('/version','/_terrier/health','/api/accounts','/api/v3/meta','/api/v3/features','/unlisted-route'):
            self.assertEqual(self.request(path)[0],401)


if __name__=='__main__':
    import unittest
    unittest.main()
