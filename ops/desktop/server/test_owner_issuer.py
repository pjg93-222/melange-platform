import base64
import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from desktop_admin import Store, Refused, protect, canonical
from access_store import AccessStore
from owner_issuer import provision, protected_state, native_credentials


class OwnerIssuerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        publisher = Store(self.root/'admin'); publisher.init()
        self.access = AccessStore(publisher); self.access.initialize()
        self.tokens = {}
        self.addCleanup(patch.stopall)
        patch.object(native_credentials,'read',side_effect=lambda app,device,scope: self.tokens.get((app,device,scope))).start()
        patch.object(native_credentials,'write',side_effect=lambda app,device,token,scope: self.tokens.__setitem__((app,device,scope),token)).start()

    def value(self, app='melange'):
        profile=self.root/str(uuid.uuid4()); profile.mkdir()
        data=self.root/str(uuid.uuid4()); data.mkdir()
        return {'appId':app,'deviceId':str(uuid.uuid4()),'profile':str(profile),'url':'https://127.0.0.1:38443',
                'displayName':'Synthetic local owner','dataBindings':[{'districtId':None,'dataRoot':str(data),'localUserId':None,'localUsername':None}]}

    def test_signed_owner_and_ordinary_grant_account_scope_and_dpapi_receipt(self):
        value=self.value(); receipt=provision(self.access,value)
        self.assertTrue(receipt['provisioned'])
        state=protected_state.read(value['profile'],'state'); owner=protected_state.read(value['profile'],'owner')
        grant=json.loads(base64.b64decode(state['grantEnvelope']['payload']))
        attestation=json.loads(base64.b64decode(owner['attestation']['payload']))
        self.assertEqual(attestation['purpose'],'melange-desktop-owner')
        self.assertEqual(attestation['grantId'],grant['grantId'])
        self.assertEqual(attestation['accountIdentity'],grant['accountIdentity'])
        self.assertEqual(attestation['profileHash'],protected_state.path_hash(value['profile']))
        self.assertEqual(attestation['windowsSid'],protected_state.windows_sid())
        self.assertEqual(attestation['dataBindings'][0]['localUsername'],None)
        token=next(iter(self.tokens.values()))
        self.assertNotIn(token.encode(),self.access.db.read_bytes())
        self.assertEqual(provision(self.access,value),receipt)
        self.assertEqual(self.access.dashboard()['grants'][0]['display_name'],'Synthetic local owner')

    def test_interruption_at_every_boundary_reuses_exact_grant_token_and_marker(self):
        for stage in ('before-server-commit','server-committed','identity','trust','state','credential','owner'):
            with self.subTest(stage=stage):
                value=self.value()
                def fail(name):
                    if name==stage: raise RuntimeError('synthetic interruption')
                with self.assertRaisesRegex(RuntimeError,'synthetic interruption'):
                    provision(self.access,value,checkpoint=fail)
                with self.access.connect() as db:
                    before=db.execute('SELECT receipt FROM owner_provisioning WHERE profile_hash=?',(protected_state.path_hash(value['profile']),)).fetchone()
                    expected=json.loads(protect(bytes(before[0]),decrypt=True)) if before else None
                result=provision(self.access,value)
                with self.access.connect() as db:
                    count=db.execute('SELECT COUNT(*) FROM grants WHERE device_id=?',(value['deviceId'],)).fetchone()[0]
                self.assertEqual(count,1)
                if expected:
                    self.assertEqual(result['grantId'],expected['grantId'])
                    self.assertEqual(protected_state.read(value['profile'],'owner'),expected['records']['owner'])
                    self.assertEqual(self.tokens[(value['appId'],value['deviceId'],'access-token')],expected['credential'])

    def test_unrelated_enrollment_and_invalid_account_preflight_do_not_mutate_server(self):
        value=self.value(); protected_state.write(value['profile'],'identity',{'unrelated':True})
        before=self.access.db.read_bytes()
        with self.assertRaises(Refused): provision(self.access,value)
        self.assertEqual(self.access.db.read_bytes(),before)
        self.assertEqual(protected_state.read(value['profile'],'identity'),{'unrelated':True})
        terrier=self.value('terrier'); binding=terrier['dataBindings'][0]
        binding.update(districtId='co-05',localUserId='1',localUsername='admin')
        with self.assertRaises(Refused): provision(self.access,terrier)
        self.assertEqual(self.access.db.read_bytes(),before)

    def test_terrier_reads_existing_staff_without_password_or_role_changes(self):
        value=self.value('terrier'); binding=value['dataBindings'][0]
        binding.update(districtId='co-05',localUserId='1',localUsername='admin')
        path=Path(binding['dataRoot'])/'accounts.sqlite3'
        with contextlib.closing(sqlite3.connect(path)) as db:
            with db:
                db.execute('CREATE TABLE auth_user(id INTEGER PRIMARY KEY,username TEXT,is_active INTEGER,is_staff INTEGER,password TEXT,is_superuser INTEGER)')
                db.execute("INSERT INTO auth_user VALUES(1,'admin',1,1,'unchanged-password',1)")
        before=path.read_bytes()
        provision(self.access,value)
        self.assertEqual(path.read_bytes(),before)
        owner=json.loads(base64.b64decode(protected_state.read(value['profile'],'owner')['attestation']['payload']))
        self.assertEqual(owner['dataBindings'][0]['localUserId'],'1')
        self.assertEqual(owner['dataBindings'][0]['localUsername'],'admin')

    def test_resume_conflict_and_revocation_preserve_profile(self):
        value=self.value(); result=provision(self.access,value)
        before={p.name:p.read_bytes() for p in Path(value['profile']).iterdir()}
        changed={**value,'displayName':'Another mapping'}
        with self.assertRaises(Refused): provision(self.access,changed)
        self.access.revoke(result['grantId'])
        with self.assertRaises(Refused): provision(self.access,value)
        self.assertEqual({p.name:p.read_bytes() for p in Path(value['profile']).iterdir()},before)

    def test_owner_account_validation_matches_native_recipient_verifier(self):
        from owner_issuer import _account
        for user_id,username in [('01','admin'),('9223372036854775808','admin'),('1','name with spaces'),('1','<admin>')]:
            with self.subTest(user_id=user_id,username=username),self.assertRaises(Refused):
                _account(self.root,user_id,username)


class InitializationRecoveryTests(unittest.TestCase):
    def test_resume_preserves_identity_across_each_boundary(self):
        for stage in ('receipt','key','config','database'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                publisher=Store(Path(tmp)/'admin'); publisher.init(); access=AccessStore(publisher)
                def fail(name):
                    if name==stage: raise RuntimeError('synthetic interruption')
                with self.assertRaises(RuntimeError): access.initialize(fail)
                receipt=json.loads(protect((access.root/'access-initialization.dpapi').read_bytes(),decrypt=True))
                config=access.initialize()
                self.assertEqual(config,receipt['config'])
                self.assertEqual(protect(access.key_path.read_bytes(),decrypt=True),base64.b64decode(receipt['privateKey']))
                with self.assertRaises(Refused): access.initialize()

    def test_unrecognized_or_changed_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            publisher=Store(Path(tmp)/'admin'); publisher.init(); access=AccessStore(publisher)
            access.key_path.write_bytes(b'unrecognized-existing-key')
            with self.assertRaises(Refused): access.initialize()
            self.assertEqual(access.key_path.read_bytes(),b'unrecognized-existing-key')
