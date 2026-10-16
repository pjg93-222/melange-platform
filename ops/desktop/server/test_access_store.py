import base64
import contextlib
import concurrent.futures
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from desktop_admin import Store, Refused
from access_store import AccessStore


class AccessStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.publisher = Store(Path(self.temp.name) / 'state')
        self.publisher.init()
        self.now = 1800000000
        self.store = AccessStore(self.publisher, clock=lambda: self.now)
        self.store.initialize()

    def request(self, app='melange', device=None):
        value = dict(requestId=str(uuid.uuid4()), deviceId=device or str(uuid.uuid4()),
                     appId=app, platform='win32', arch='x64', displayName='Synthetic recipient',
                     requestSecret=secrets.token_urlsafe(32))
        self.store.request(value, '192.168.2.3')
        return value

    def approval(self, app='melange', device=None):
        request = self.request(app, device)
        role = 'local_administrator' if app == 'terrier' else 'local_user'
        approved = self.store.approve(request['requestId'], role, ['co-05', 'or-05'] if app == 'terrier' else [])
        activation = {key: request[key] for key in ('requestId', 'deviceId', 'appId', 'platform', 'arch')}
        activation['activationKey'] = approved['activationKey']
        return request, activation

    def activated(self, app='melange', device=None):
        request, activation = self.approval(app, device)
        return request, self.store.activate(activation, request['requestSecret'])

    def payload(self, envelope):
        raw = base64.b64decode(envelope['payload'], validate=True)
        public = self.store.config()['publicKeys'][envelope['keyId']]
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(base64.b64decode(envelope['signature']), raw)
        return json.loads(raw)

    def test_binding_signature_hash_only_storage_and_atomic_retry(self):
        request, activation = self.approval('terrier')
        with self.assertRaises(Refused):
            self.store.activate({**activation, 'deviceId': str(uuid.uuid4())}, request['requestSecret'])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: self.store.activate(activation, request['requestSecret']), range(2)))
        self.assertEqual(responses[0], responses[1])
        payload = self.payload(responses[0]['grant'])
        self.assertEqual(payload['districtIds'], ['co-05', 'or-05'])
        self.assertEqual(payload['role'], 'local_administrator')
        self.assertEqual(payload['accountIdentity'], 'recipient_' + uuid.UUID(payload['principalId']).hex)
        self.assertEqual(payload['serverId'], self.store.descriptor('https://192.168.2.1:38443')['serverId'])
        raw = self.store.db.read_bytes()
        for value in (request['requestSecret'], activation['activationKey'], responses[0]['accessToken']):
            self.assertNotIn(value.encode(), raw)
        self.now += 601
        with self.assertRaises(Refused):
            self.store.activate(activation, request['requestSecret'])

    def test_failed_activation_rolls_back_key_and_grant(self):
        request, activation = self.approval()
        with patch('access_store.protect', side_effect=OSError('fixture protect failure')):
            with self.assertRaises(OSError):
                self.store.activate(activation, request['requestSecret'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM grants').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT state FROM requests').fetchone()[0], 'approved')
        self.assertTrue(self.store.activate(activation, request['requestSecret'])['accessToken'])

    def test_scope_target_poll_and_quota_rejections(self):
        request = self.request('terrier')
        for role, districts in [('superuser', ['co-05']), ('local_administrator', []), ('local_administrator', ['xx-99']), ('local_administrator', ['or-05', 'co-05'])]:
            with self.assertRaises(Refused):
                self.store.approve(request['requestId'], role, districts)
        with self.assertRaises(Refused):
            self.store.poll(request['requestId'], secrets.token_urlsafe(32))
        with self.assertRaises(Refused):
            self.store.request({**request, 'requestId': str(uuid.uuid4())}, '192.168.2.3')
        with self.assertRaises(Refused):
            self.store.request({**request, 'requestId': str(uuid.uuid4()), 'platform': 'darwin'}, '192.168.2.3')
        for _ in range(9): self.request()
        with self.assertRaises(Refused): self.request()

    def test_signed_revocation_sticky_status_and_token_privilege_separation(self):
        request, response = self.activated()
        token = response['accessToken']; grant = self.payload(response['grant'])
        with self.assertRaises(Refused): self.publisher.authenticate('Bearer ' + token)
        self.store.revoke(grant['grantId'])
        status = self.payload(self.store.status(token))
        self.assertTrue(status['revoked'])
        self.assertGreater(status['sequence'], grant['sequence'])
        with self.assertRaises(Refused): self.store.authenticate(token)
        newer_request, newer = self.activated(device=request['deviceId'])
        new_grant = self.payload(newer['grant'])
        self.assertEqual(new_grant['accountIdentity'], grant['accountIdentity'])
        self.assertGreater(new_grant['sequence'], status['sequence'])

    def test_typed_scoped_activity_dedup_and_presence(self):
        _, response = self.activated('terrier'); token = response['accessToken']
        presence = dict(sessionId=str(uuid.uuid4()), state='active', version='1.0.0', districtId='co-05')
        self.store.presence(token, presence)
        self.assertTrue(self.store.dashboard()['grants'][0]['online'])
        self.now += 181
        self.assertFalse(self.store.dashboard()['grants'][0]['online'])
        event = dict(eventId=str(uuid.uuid4()), type='walk_saved', occurredAt=self.now, status='succeeded', districtId='co-05', counts={'items': 3}, revisionId='a'*64)
        self.assertEqual(self.store.events(token, {'events': [event]})['acknowledged'], [event['eventId']])
        self.store.events(token, {'events': [event]})
        with self.store.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM activity').fetchone()[0], 1)
        for changed in ({'districtId': 'ct-01'}, {'counts': {'items': 4}}, {'recordName': 'forbidden'}, {'revisionId': 'C:/private/path'}):
            with self.assertRaises((Refused, ValueError)):
                self.store.events(token, {'events': [{**event, **changed}]})

    def test_update_exchange_atomic_rollback_retry_and_linked_revocation(self):
        _, response = self.activated(); token = response['accessToken']
        with patch.object(self.store, 'audit', side_effect=OSError('fixture failure after inserts')):
            with self.assertRaises(OSError):
                self.store.update_enrollment(token, {'currentVersion': '1.0.0'}, 'https://192.168.2.1:38443')
        with self.publisher.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM devices').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pairings').fetchone()[0], 0)
        result = self.store.update_enrollment(token, {'currentVersion': '1.0.0'}, 'https://192.168.2.1:38443')
        self.assertEqual(result, self.store.update_enrollment(token, {'currentVersion': '1.0.0'}, 'https://192.168.2.1:38443'))
        self.publisher.authenticate('Bearer ' + result['deviceToken'])
        with self.assertRaises(Refused): self.store.authenticate(result['deviceToken'])
        self.store.revoke(self.payload(response['grant'])['grantId'])
        with self.assertRaises(Refused): self.publisher.authenticate('Bearer ' + result['deviceToken'])

    def test_wal_exchange_fails_closed_without_changing_journal_mode(self):
        _, response = self.activated()
        with self.publisher.connect() as db: self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        with self.assertRaises(Refused):
            self.store.update_enrollment(response['accessToken'], {'currentVersion': '1.0.0'}, 'https://192.168.2.1:38443')
        with self.publisher.connect() as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM devices').fetchone()[0], 0)

    def test_every_enrollment_insert_rolls_back_both_databases(self):
        _, response = self.activated()
        original = self.store.connect
        for database, table in [('publisher','pairings'), ('publisher','devices'), ('publisher','audit'), ('main','update_exchanges'), ('main','access_audit')]:
            with self.subTest(database=database, table=table):
                @contextlib.contextmanager
                def failing_connection():
                    with original() as db:
                        db.set_authorizer(lambda action, name, _column, db_name, _source:
                            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and (db_name, name) == (database, table) else sqlite3.SQLITE_OK)
                        yield db
                with patch.object(self.store, 'connect', failing_connection):
                    with self.assertRaises(sqlite3.DatabaseError):
                        self.store.update_enrollment(response['accessToken'], {'currentVersion':'1.0.0'}, 'https://192.168.2.1:38443')
                with self.publisher.connect() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM devices').fetchone()[0], 0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM pairings').fetchone()[0], 0)
                with original() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM update_exchanges').fetchone()[0], 0)

    def test_superseding_grant_rebinds_own_download_credential_without_rotation(self):
        request, response = self.activated()
        first = self.store.update_enrollment(response['accessToken'], {'currentVersion':'1.0.0'}, 'https://192.168.2.1:38443')
        _, newer = self.activated(device=request['deviceId'])
        second = self.store.update_enrollment(newer['accessToken'], {'currentVersion':'1.0.0'}, 'https://192.168.2.1:38443')
        self.assertEqual(first, second)
        self.store.revoke(self.payload(newer['grant'])['grantId'])
        with self.assertRaises(Refused): self.publisher.authenticate('Bearer '+first['deviceToken'])

    def test_existing_unrelated_enrollment_is_neither_replaced_nor_revoked(self):
        request, response = self.activated()
        pairing = self.publisher.pairing('melange', 'https://192.168.2.1:38443')
        import base64
        bundle = json.loads(base64.urlsafe_b64decode(pairing+'='*(-len(pairing)%4)))
        existing = self.publisher.enroll(dict(appId='melange', deviceId=request['deviceId'], platform='win32', arch='x64', currentVersion='1.0.0', enrollmentCode=bundle['code']))
        with self.assertRaises(Refused):
            self.store.update_enrollment(response['accessToken'], {'currentVersion':'1.0.0'}, 'https://192.168.2.1:38443')
        self.store.revoke(self.payload(response['grant'])['grantId'])
        self.publisher.authenticate('Bearer '+existing['deviceToken'])

    def test_expired_approval_can_be_replaced_by_a_fresh_request(self):
        request, activation = self.approval()
        self.now += 601
        self.assertEqual(self.store.poll(request['requestId'], request['requestSecret'])['status'], 'expired')
        with self.assertRaises(Refused): self.store.activate(activation, request['requestSecret'])
        self.request(device=request['deviceId'])

    def test_explicit_key_replacement_and_effective_dashboard_expiry(self):
        request, activation = self.approval()
        self.now += 601
        dashboard = self.store.dashboard()
        self.assertEqual(dashboard['requests'][0]['state'], 'expired')
        self.assertTrue(dashboard['requests'][0]['can_replace_key'])
        replacement = self.store.replace_key(request['requestId'])
        with self.assertRaises(Refused): self.store.activate(activation, request['requestSecret'])
        activated = self.store.activate({**activation,'activationKey':replacement['activationKey']}, request['requestSecret'])
        self.assertEqual(self.store.poll(request['requestId'],request['requestSecret'])['expiresAt'], self.now+600)
        with self.assertRaises(Refused): self.store.replace_key(request['requestId'])
        self.assertEqual(self.store.dashboard()['grants'][0]['display_name'], request['displayName'])

    def test_event_dedup_survives_higher_grant_and_retains_original_actor(self):
        request, response = self.activated()
        event = dict(eventId=str(uuid.uuid4()),type='notes_updated',occurredAt=self.now,status='succeeded',districtId=None,counts={'items':1})
        self.store.events(response['accessToken'], {'events':[event]})
        _, renewed = self.activated(device=request['deviceId'])
        self.assertEqual(self.store.events(renewed['accessToken'], {'events':[event]})['acknowledged'], [event['eventId']])
        with self.store.connect() as db:
            rows = db.execute('SELECT grant_id FROM activity').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['grant_id'], self.payload(response['grant'])['grantId'])
        with self.assertRaises(Refused): self.store.events(renewed['accessToken'], {'events':[{**event,'counts':{'items':2}}]})
        self.assertEqual(self.store.dashboard()['activity'][0]['display_name'], request['displayName'])


if __name__ == '__main__': unittest.main()
