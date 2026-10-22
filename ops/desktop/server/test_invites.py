import concurrent.futures
import contextlib
import hmac
import itertools
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desktop_admin import Refused, digest
from access_store import AccessStore
import test_access_store as fixtures

ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
FORMAT = re.compile(r'^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$')


class InviteTests(unittest.TestCase):
    def setUp(self):
        fixtures.AccessStoreTests.setUp(self)
        # Public relay mode: every client is 127.0.0.1, so only the shared caps apply (the case invites protect).
        self.publisher.public_origin = 'https://updates.example.com'
        self.store.require_invite = True

    def value(self, code, **changes):
        return {**dict(requestId=str(uuid.uuid4()), deviceId=str(uuid.uuid4()), appId='melange', platform='win32', arch='x64',
                       displayName='Synthetic recipient', requestSecret=secrets.token_urlsafe(32), inviteCode=code), **changes}

    def submit(self, code, **changes):
        return self.store.request(self.value(code, **changes), '127.0.0.1')

    def unissued(self):
        return '-'.join(''.join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(4))

    def invites(self):
        return self.store.dashboard()['invites']

    def count(self, table):
        with self.store.connect() as db:
            return db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def tables(self, path=None):
        with contextlib.closing(sqlite3.connect(path or self.store.db)) as db:
            return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_enforcement_is_off_by_default_so_requests_without_a_code_work_and_codes_are_not_consumed(self):
        off = AccessStore(self.publisher, clock=lambda: self.now)
        self.assertFalse(off.require_invite)
        invite = off.issue_invite('Unspent', 2)
        missing = self.value(None)
        del missing['inviteCode']  # Exactly what a 1.0.9 client sends.
        self.assertEqual(off.request(missing, '127.0.0.1')['status'], 'pending')
        self.assertEqual(off.request(missing, '127.0.0.1')['status'], 'pending')  # Poll re-POST.
        for code in (invite['code'], self.unissued(), '', 'x' * 65):  # Ignored, not even format-checked.
            self.assertEqual(off.request(self.value(code), '127.0.0.1')['status'], 'pending')
        for junk in (5, None, True, ['x'], {'a': 1}):
            with self.subTest(junk=junk), self.assertRaises(TypeError):  # The HTTP layer maps this to 400 invalid_request.
                off.request(self.value(junk), '127.0.0.1')
        self.assertEqual(self.count('requests'), 5)
        self.assertEqual(off.dashboard()['invites'][0]['uses'], 0)
        self.assertEqual(self.submit(invite['code'])['status'], 'pending')  # Turned on later, the unspent code still works once.
        self.assertEqual(self.invites()[0]['uses'], 1)

    def test_turning_enforcement_on_keeps_pending_requests_made_without_a_code_pollable(self):
        self.store.require_invite = False
        value = self.value(None)
        del value['inviteCode']
        first = self.store.request(value, '127.0.0.1')
        self.store.require_invite = True
        self.assertEqual(self.store.request(value, '127.0.0.1'), first)
        with self.assertRaises(Refused) as caught:
            self.store.request({**value, 'requestId': str(uuid.uuid4()), 'deviceId': str(uuid.uuid4())}, '127.0.0.1')
        self.assertEqual(str(caught.exception), 'invite code not accepted')
        self.assertEqual(self.count('requests'), 1)

    def test_issue_returns_the_code_once_in_crockford_groups_and_validates_inputs(self):
        result = self.store.issue_invite('Tester one', 3, 2)
        self.assertEqual(set(result), {'inviteId', 'code', 'expiresAt', 'maxUses'})
        self.assertRegex(result['code'], FORMAT)
        self.assertEqual((result['expiresAt'], result['maxUses']), (self.now + 2*86400, 3))
        self.assertEqual(str(uuid.UUID(result['inviteId'])), result['inviteId'])
        default = self.store.issue_invite('Defaults')
        self.assertEqual((default['maxUses'], default['expiresAt']), (1, self.now + 7*86400))
        self.assertNotEqual(result['code'], default['code'])
        self.assertEqual(self.store.issue_invite('a' * 80, 100, 30)['maxUses'], 100)
        for label in ('', 'x' * 81, 'bad\nlabel', 'bad\x7flabel', None, 5):
            with self.subTest(label=label), self.assertRaises(Refused):
                self.store.issue_invite(label)
        for uses in (0, 101, -1, True, 1.0, '1', None):
            with self.subTest(uses=uses), self.assertRaises(Refused):
                self.store.issue_invite('Tester', uses=uses)
        for days in (0, 31, -1, True, 7.0, '7', None):
            with self.subTest(days=days), self.assertRaises(Refused):
                self.store.issue_invite('Tester', days=days)
        self.assertEqual(self.count('invites'), 3)

    def test_at_most_fifty_usable_invites_exist(self):
        first = [self.store.issue_invite('Tester') for _ in range(50)]
        with self.assertRaises(Refused):
            self.store.issue_invite('One too many')
        self.store.revoke_invite(first[0]['inviteId'])  # Revoked, used-up and expired invites are not usable.
        self.store.issue_invite('After revoke')
        with self.assertRaises(Refused):
            self.store.issue_invite('Still full')
        self.submit(first[1]['code'])
        self.store.issue_invite('After use')
        with self.assertRaises(Refused):
            self.store.issue_invite('Still full')
        self.now += 7*86400
        for _ in range(50):
            self.store.issue_invite('After expiry')
        with self.assertRaises(Refused):
            self.store.issue_invite('Full again')

    def test_missing_malformed_and_unknown_codes_are_refused_without_a_trace(self):
        good = self.store.issue_invite('Tester', 5)['code']
        missing = self.value(good)
        del missing['inviteCode']
        with self.assertRaises(Refused):
            self.store.request(missing, '127.0.0.1')
        bad = [self.unissued(), '0000-0000-0000-0000', 'ABCD', good[:-1], good + 'A', 'U' * 16, '', ' ', 5, None, True,
               ['x'], {'a': 1}, 'x' * 65, ' ' * 60 + good, good.replace('-', '*')]
        for code in bad:
            with self.subTest(code=code), self.assertRaises(Refused) as caught:
                self.submit(code)
            self.assertEqual(str(caught.exception), 'invite code not accepted')
        for table in ('requests', 'notification_outbox'):
            self.assertEqual(self.count(table), 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM access_audit WHERE action='access-requested'").fetchone()[0], 0)
        self.assertEqual(self.invites()[0]['uses'], 0)

    def test_bad_codes_cannot_fill_the_shared_caps(self):
        good = self.store.issue_invite('Tester', 100)['code']
        for _ in range(150):
            with self.assertRaises(Refused) as caught:
                self.submit(self.unissued())
            self.assertNotIn('capacity', str(caught.exception))
        self.assertEqual(self.count('requests'), 0)
        self.assertEqual(self.submit(good)['status'], 'pending')

    def test_full_caps_refuse_bad_codes_as_invite_problems_and_keep_good_codes_unconsumed(self):
        filler, other = self.store.issue_invite('Filler', 100)['code'], self.store.issue_invite('Other', 5)
        for _ in range(100):
            self.submit(filler)
        with self.assertRaises(Refused) as caught:
            self.submit(other['code'])
        self.assertIn('capacity', str(caught.exception))
        with self.assertRaises(Refused) as caught:
            self.submit(self.unissued())
        self.assertEqual(str(caught.exception), 'invite code not accepted')
        self.assertEqual({i['inviteId']: i['uses'] for i in self.invites()}[other['inviteId']], 0)

    def test_expired_revoked_and_exhausted_codes_are_refused(self):
        expiring = self.store.issue_invite('Expiring', 5, 1)['code']
        revoked = self.store.issue_invite('Revoked', 5)
        single = self.store.issue_invite('Single')['code']
        self.store.revoke_invite(revoked['inviteId'])
        self.submit(single)
        for code in (revoked['code'], single):
            with self.assertRaises(Refused) as caught:
                self.submit(code)
            self.assertEqual(str(caught.exception), 'invite code not accepted')
        self.submit(expiring)
        self.now += 86400
        with self.assertRaises(Refused):
            self.submit(expiring)
        self.assertEqual(self.count('requests'), 2)

    def test_valid_code_consumes_one_use_until_the_limit(self):
        invite = self.store.issue_invite('Three testers', 3)
        for used in range(1, 4):
            self.assertEqual(self.submit(invite['code'])['status'], 'pending')
            self.assertEqual(self.invites()[0]['uses'], used)
        with self.assertRaises(Refused):
            self.submit(invite['code'])
        self.assertEqual((self.invites()[0]['uses'], self.invites()[0]['state']), (3, 'used'))
        self.assertEqual(self.count('requests'), 3)

    def test_single_use_code_is_consumed_once_under_concurrency(self):
        invite = self.store.issue_invite('Single')
        def attempt(_):
            try:
                self.submit(invite['code'])
                return True
            except Refused:
                return False
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.count('requests'), 1)

    def test_device_conflict_refusal_does_not_consume_the_code(self):
        invite = self.store.issue_invite('Two uses', 2)
        first = self.value(invite['code'])
        self.store.request(first, '127.0.0.1')
        with self.assertRaises(Refused) as caught:  # The device already has a pending request.
            self.submit(invite['code'], deviceId=first['deviceId'])
        self.assertIn('conflict', str(caught.exception))
        self.assertEqual(self.invites()[0]['uses'], 1)
        self.submit(invite['code'])
        self.assertEqual(self.invites()[0]['uses'], 2)

    def test_repost_with_the_original_well_formed_code_skips_the_usable_invite_check_and_consumes_no_use(self):
        invite = self.store.issue_invite('Single')
        value = self.value(invite['code'])
        first = self.store.request(value, '127.0.0.1')
        self.store.revoke_invite(invite['inviteId'])
        self.assertEqual(self.store.request(value, '127.0.0.1'), first)  # Lost-response recovery.
        self.assertEqual(self.invites()[0]['uses'], 1)
        self.assertEqual(self.count('requests'), 1)
        self.assertEqual(self.store.request({**value, 'inviteCode': ''}, '127.0.0.1'), first)  # The format check is for new requests only.
        with self.assertRaises(Refused) as caught:
            self.store.request({**value, 'displayName': 'Someone else'}, '127.0.0.1')
        self.assertIn('conflict', str(caught.exception))

    def test_repost_of_an_existing_request_with_a_malformed_or_missing_code_returns_it_but_a_new_request_is_still_refused(self):
        invite = self.store.issue_invite('Single')
        value = self.value(invite['code'])
        first = self.store.request(value, '127.0.0.1')
        before = self.count('notification_outbox')
        for code in ('', 'ABCD', 'x' * 65, 5, None, ['x'], self.unissued()):
            with self.subTest(code=code):
                self.assertEqual(self.store.request({**value, 'inviteCode': code}, '127.0.0.1'), first)
        missing = dict(value); del missing['inviteCode']
        self.assertEqual(self.store.request(missing, '127.0.0.1'), first)
        self.assertEqual((self.count('requests'), self.count('notification_outbox'), self.invites()[0]['uses']), (1, before, 1))
        for code in ('', 'ABCD', 'x' * 65, 5):  # A different request id is new: format is judged, in the same transaction, before the caps.
            with self.subTest(new=code), self.assertRaises(Refused) as caught:
                self.submit(code)
            self.assertEqual(str(caught.exception), 'invite code not accepted')
        with self.assertRaises(Refused) as caught:  # An existing id with a changed identity is still a conflict, not an invite problem.
            self.store.request({**value, 'displayName': 'Someone else', 'inviteCode': ''}, '127.0.0.1')
        self.assertIn('conflict', str(caught.exception))
        self.assertEqual(self.count('requests'), 1)

    def test_submitted_codes_are_normalized(self):
        with patch('access_store.secrets.choice', side_effect=itertools.cycle('01AZ')):
            invite = self.store.issue_invite('Tester', 20)
        self.assertEqual(invite['code'], '01AZ-01AZ-01AZ-01AZ')
        variants = ('01AZ-01AZ-01AZ-01AZ', '01az-01az-01az-01az', '01AZ01AZ01AZ01AZ', ' 01az 01az 01az 01az ',
                    'OIAZ-OIAZ-OIAZ-OIAZ', 'olaz-oLaz-oiaz-OLAZ', '0lAZ-0IAZ-0lAZ-0IAZ')
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual(self.submit(variant)['status'], 'pending')
        self.assertEqual(self.invites()[0]['uses'], len(variants))

    def test_only_a_domain_separated_hash_is_stored_or_shown(self):
        invite = self.store.issue_invite('Tester', 2)
        self.submit(invite['code'])
        normalized = invite['code'].replace('-', '')
        raw = self.store.db.read_bytes()
        for text in (invite['code'], normalized, invite['code'].lower(), normalized.lower()):
            self.assertNotIn(text.encode(), raw)
        with self.store.connect() as db:
            stored = db.execute('SELECT code_hash FROM invites').fetchone()[0]
            audit = json.dumps([tuple(r) for r in db.execute('SELECT * FROM access_audit')])
        self.assertEqual(stored, digest(b'invite:' + normalized.encode()))
        shown = json.dumps(self.store.dashboard())
        for text in (invite['code'], normalized, stored):
            self.assertNotIn(text, shown)
            self.assertNotIn(text, audit)
        self.assertNotIn('code_hash', shown)
        self.assertNotIn('code', json.loads(shown)['invites'][0])

    def test_dashboard_lists_usable_invites_first_then_newest_first_with_states_and_no_secrets(self):
        lapsed = self.store.issue_invite('lapsed', 1, 1); self.now += 5
        first = self.store.issue_invite('first', 1, 7); self.now += 5
        created = self.now
        second = self.store.issue_invite('second', 2, 3); self.now += 5
        third = self.store.issue_invite('third'); fourth = self.store.issue_invite('fourth')  # Same second: rowid breaks the tie.
        self.submit(first['code'])
        self.store.revoke_invite(third['inviteId'])
        self.now += 2*86400
        listed = self.invites()
        self.assertEqual([(i['label'], i['state']) for i in listed],
                         [('fourth', 'active'), ('second', 'active'), ('third', 'revoked'), ('first', 'used'), ('lapsed', 'expired')])
        self.assertEqual(listed[1], {'inviteId': second['inviteId'], 'label': 'second', 'created': created,
                                     'expires': created + 3*86400, 'maxUses': 2, 'uses': 0, 'state': 'active'})
        self.assertEqual(listed[3]['uses'], 1)
        self.assertEqual(listed[4]['inviteId'], lapsed['inviteId'])

    def test_dashboard_keeps_usable_invites_listed_behind_many_newer_closed_ones(self):
        long_lived = self.store.issue_invite('Long lived', 100, 30)
        for _ in range(110):  # Newer invites that are revoked as we go, so the usable cap is never hit.
            self.now += 1
            self.store.revoke_invite(self.store.issue_invite('Closed')['inviteId'])
        listed = self.invites()
        self.assertEqual(len(listed), 100)
        self.assertEqual({i['inviteId']: i['state'] for i in listed}[long_lived['inviteId']], 'active')
        self.assertEqual(listed[0]['inviteId'], long_lived['inviteId'])  # Usable first, then newest first.
        self.assertEqual({i['state'] for i in listed[1:]}, {'revoked'})

    def test_revoke_invite(self):
        invite = self.store.issue_invite('Tester', 5)
        with self.assertRaises(Refused):
            self.store.revoke_invite(str(uuid.uuid4()))
        with self.assertRaises(ValueError):
            self.store.revoke_invite('not-a-uuid')
        self.assertIsNone(self.store.revoke_invite(invite['inviteId']))
        with self.assertRaises(Refused):
            self.store.revoke_invite(invite['inviteId'])
        with self.assertRaises(Refused):
            self.submit(invite['code'])
        self.assertEqual(self.invites()[0]['state'], 'revoked')

    def test_issue_and_revoke_are_audited_without_code_material(self):
        invite = self.store.issue_invite('Tester')
        with self.assertRaises(Refused):
            self.store.issue_invite('')
        self.store.revoke_invite(invite['inviteId'])
        with self.store.connect() as db:
            rows = [tuple(r) for r in db.execute('SELECT action,request_id,grant_id FROM access_audit ORDER BY id')]
        self.assertEqual(rows, [('invite-issued', None, None), ('invite-revoked', None, None)])

    def test_candidate_is_compared_with_every_usable_invite_only(self):
        self.store.issue_invite('Expired', 1, 1); self.now += 86400
        codes = [self.store.issue_invite(f'Tester {i}', 5)['code'] for i in range(3)]
        self.store.revoke_invite(self.store.issue_invite('Revoked')['inviteId'])
        with patch('access_store.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
            self.submit(codes[0])
            self.assertEqual(compare.call_count, 3)  # No early exit on a match.
            compare.reset_mock()
            self.submit(codes[2])
            self.assertEqual(compare.call_count, 3)
            compare.reset_mock()
            with self.assertRaises(Refused):
                self.submit(self.unissued())
            self.assertEqual(compare.call_count, 3)

    def test_pre_invite_database_is_migrated_in_place_then_works(self):
        self.assertEqual(len(self.tables()), 11)
        self.assertIn('invites', self.tables())  # Fresh databases create the table.
        existing = self.value(self.store.issue_invite('Before upgrade', 5)['code'])
        self.store.request(existing, '127.0.0.1')
        with contextlib.closing(sqlite3.connect(self.store.db)) as db:
            db.execute('DROP TABLE invites')
            db.commit()
        self.assertEqual(len(self.tables()), 10)  # The shape deployed 1.0.8 databases have.
        AccessStore._verify_initialized_database(self.store.db)  # initialize() accepts it as is.
        first = AccessStore(self.publisher, clock=lambda: self.now, require_invite=True)  # A new process: migrates on first use.
        self.assertEqual(first.dashboard()['invites'], [])
        self.assertIn('invites', self.tables())
        self.assertEqual(len(first.dashboard()['requests']), 1)  # Existing rows are untouched.
        with contextlib.closing(sqlite3.connect(self.store.db)) as db:
            db.execute('DROP TABLE invites')
            db.commit()
        second = AccessStore(self.publisher, clock=lambda: self.now, require_invite=True)  # Migration also precedes a first write.
        invite = second.issue_invite('After upgrade', 2)
        self.assertEqual(second.request(self.value(invite['code']), '127.0.0.1')['status'], 'pending')
        self.assertEqual(second.dashboard()['invites'][0]['uses'], 1)
        self.assertEqual(AccessStore(self.publisher, clock=lambda: self.now, require_invite=True).dashboard()['invites'][0]['uses'], 1)  # Idempotent.
        with contextlib.closing(sqlite3.connect(self.store.db)) as db:
            db.execute('CREATE TABLE unexpected (x)')
            db.commit()
        with self.assertRaises(Refused):
            AccessStore._verify_initialized_database(self.store.db)


if __name__ == '__main__': unittest.main()
