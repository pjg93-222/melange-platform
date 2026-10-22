import base64
import contextlib
import concurrent.futures
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from desktop_admin import Store, Refused
import access_store
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

    def request(self, app='melange', device=None, source='192.168.2.3'):
        value = dict(requestId=str(uuid.uuid4()), deviceId=device or str(uuid.uuid4()),
                     appId=app, platform='win32', arch='x64', displayName='Synthetic recipient',
                     requestSecret=secrets.token_urlsafe(32))
        self.store.request(value, source)
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

    def test_no_per_source_quota_only_the_global_waiting_and_hourly_bounds(self):
        for _ in range(100): self.request(source='192.168.2.3')  # Formerly 10/hour per source.
        with self.assertRaises(Refused) as caught: self.request(source='10.0.0.9')
        self.assertIn('capacity', str(caught.exception))  # 100 new requests/hour from everyone together.
        self.now += 3601
        with self.assertRaises(Refused): self.request()  # 100 still waiting for a decision.
        with self.store.connect() as db:
            waiting = [r[0] for r in db.execute("SELECT request_id FROM requests WHERE state='pending'")]
        for request_id in waiting:
            self.store.approve(request_id, 'local_user', [])
        for _ in range(99): self.request()  # Approved people never count against either bound.
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 199)

    def test_full_activity_table_prunes_the_oldest_rows_instead_of_refusing(self):
        _request, response = self.activated()
        def event(n):
            return {'eventId': str(uuid.uuid4()), 'type': 'notes_updated', 'occurredAt': self.now, 'districtId': None,
                    'status': 'succeeded', 'counts': {'items': n}}
        sent = [event(n) for n in range(5)]
        with patch('access_store.MAX_ACTIVITY_PER_DEVICE', 3):
            self.store.events(response['accessToken'], {'events': sent[:2]})
            self.store.events(response['accessToken'], {'events': sent[2:]})
        with self.store.connect() as db:
            kept = [r[0] for r in db.execute('SELECT event_id FROM activity ORDER BY rowid')]
        self.assertEqual(kept, [e['eventId'] for e in sent[2:]])

    def event(self, n=0):
        return {'eventId': str(uuid.uuid4()), 'type': 'notes_updated', 'occurredAt': self.now, 'districtId': None,
                'status': 'succeeded', 'counts': {'items': n}}

    def test_one_device_filling_its_history_never_prunes_another_devices_rows(self):
        _quiet, quiet = self.activated()
        _busy, busy = self.activated()
        kept = [self.event(), self.event()]
        self.store.events(quiet['accessToken'], {'events': kept})
        spam = [self.event(n) for n in range(10)]
        with patch('access_store.MAX_ACTIVITY_PER_DEVICE', 3):
            for start in range(0, 10, 2):
                self.store.events(busy['accessToken'], {'events': spam[start:start+2]})
        with self.store.connect() as db:
            rows = [(r[0], r[1]) for r in db.execute('SELECT principal_id,event_id FROM activity ORDER BY rowid')]
        quiet_principal, busy_principal = (self.payload(r['grant'])['principalId'] for r in (quiet, busy))
        self.assertEqual([e for p, e in rows if p == quiet_principal], [e['eventId'] for e in kept])
        self.assertEqual([e for p, e in rows if p == busy_principal], [e['eventId'] for e in spam[-3:]])

    def seed_requests(self, db, rows):
        db.executemany('INSERT INTO requests(request_id,app,device_id,platform,arch,display_name,secret_hash,source_hash,state,created,expires,receipt,receipt_expires) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       [(str(uuid.uuid4()), 'melange', str(uuid.uuid4()), 'win32', 'x64', 'Seeded', 'x', 'x', state, created, created+86400, receipt, receipt_expires)
                        for state, created, receipt, receipt_expires in rows])

    def test_a_new_request_stays_fast_with_100k_rows_and_prunes_only_old_expired_rows(self):
        granted, response = self.activated()
        grant_request = self.payload(response['grant'])['requestId']
        old, recent = self.now - 40*86400, self.now - 3600
        with contextlib.closing(sqlite3.connect(self.store.db)) as db, db:
            # A database deployed before these indexes existed: the first connect below must add them.
            for index in ('requests_state_expires', 'requests_created', 'requests_receipt', 'notification_outbox_request'):
                db.execute('DROP INDEX ' + index)
            self.store = AccessStore(self.publisher, clock=lambda: self.now)
            self.seed_requests(db, [('expired', old - n, None, None) for n in range(1000)]
                                   + [('used', old - n, None, old) for n in range(89000)]
                                   + [('used', old, b'stale receipt', old) for _ in range(500)]
                                   + [('denied', old - n, None, None) for n in range(9000)]
                                   + [('pending', recent, None, None) for _ in range(50)])
            # An expired row that somehow backs a grant, and one whose notification is mid-delivery, are never deleted.
            db.execute("UPDATE requests SET state='expired',created=?,expires=? WHERE request_id=?", (old, old+86400, grant_request))
            in_flight = db.execute("SELECT request_id FROM requests WHERE state='expired' AND request_id!=? ORDER BY rowid LIMIT 1", (grant_request,)).fetchone()[0]
            db.execute('INSERT INTO notification_outbox(id,request_id,kind,state,created) VALUES (?,?,?,?,?)', (str(uuid.uuid4()), in_flight, 'access_requested', 'sending', old))
            db.execute("INSERT INTO notification_outbox(id,request_id,kind,state,created) SELECT request_id||'-n',request_id,'access_requested','not_required',created FROM requests WHERE state='expired' AND request_id NOT IN (?,?)", (in_flight, grant_request))
        def count(where):
            with self.store.connect() as db:
                return db.execute('SELECT COUNT(*) FROM requests WHERE ' + where).fetchone()[0]
        before = count("state='expired'")  # this first connect also adds the indexes to the existing table
        with self.store.connect() as db:
            self.assertEqual(len(db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name IN ('requests_state_expires','requests_created','requests_receipt','notification_outbox_request')").fetchall()), 4)
        started = time.perf_counter()
        self.request()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.5)
        self.assertEqual(count("state='expired'"), before - access_store.PRUNE_BATCH)
        self.assertEqual(count('receipt IS NOT NULL'), 1)  # only the live grant's unexpired retry receipt
        with patch('access_store.PRUNE_BATCH', 2000):
            self.request()
        self.assertEqual(count("state='expired'"), 2)  # the grant-backed and the mid-delivery rows
        self.assertEqual(count("state='used'"), 89500)
        self.assertEqual(count("state='denied'"), 9000)
        self.assertEqual(count("state='pending'"), 52)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notification_outbox o WHERE NOT EXISTS (SELECT 1 FROM requests r WHERE r.request_id=o.request_id)').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM grants JOIN requests USING(request_id)').fetchone()[0], 1)
        self.assertEqual(self.store.poll(granted['requestId'], granted['requestSecret'])['status'], 'expired')

    def test_dashboard_lists_every_open_request_and_active_grant_however_many(self):
        old = self.now - 3600
        with contextlib.closing(sqlite3.connect(self.store.db)) as db, db:
            self.seed_requests(db, [('pending', old, None, None) for _ in range(150)] + [('expired', self.now - n, None, None) for n in range(150)])
            for n in range(1200):
                request_id, device, principal = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
                db.execute('INSERT INTO requests(request_id,app,device_id,platform,arch,display_name,secret_hash,source_hash,state,created,expires) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (request_id, 'melange', device, 'win32', 'x64', 'Seeded', 'x', 'x', 'used', n, n+86400))
                db.execute('INSERT INTO principals VALUES (?,?,?,?,?)', ('melange', device, principal, 'recipient_' + uuid.UUID(principal).hex, 1))
                # The 600 active grants are the oldest, so a newest-first cap alone would hide every one of them.
                db.execute('INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()), request_id, principal, 'melange', device, 'win32', 'x64', 'recipient_' + uuid.UUID(principal).hex,
                            'local_user', '[]', 1, 1, n, int(n >= 600), secrets.token_hex(32), '{}'))
        snapshot = self.store.dashboard()
        states = [r['state'] for r in snapshot['requests']]
        self.assertEqual(states.count('pending'), 150)
        self.assertEqual(len(states), 250)
        active = [g for g in snapshot['grants'] if not g['revoked']]
        self.assertEqual((len(active), len(snapshot['grants'])), (600, 1100))
        oldest = min(active, key=lambda g: g['issued'])
        self.store.revoke(oldest['grant_id'])  # what the dashboard's Revoke button sends
        self.assertEqual(len([g for g in self.store.dashboard()['grants'] if not g['revoked']]), 599)

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
