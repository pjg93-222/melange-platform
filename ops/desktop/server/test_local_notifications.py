import concurrent.futures
import unittest
from desktop_admin import Refused
import test_access_store as fixtures
from local_notifications import LocalNotifications, REMIND_AFTER_SECONDS


class LocalNotificationTests(unittest.TestCase):
    setUp = fixtures.AccessStoreTests.setUp
    request = fixtures.AccessStoreTests.request
    def test_local_claim_is_generic_once_and_does_not_approve(self):
        request = self.request()
        notifications = LocalNotifications(self.store)
        self.assertEqual(notifications.claim(), {'state': 'debouncing'})
        self.now += 31
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            claims = list(pool.map(lambda _: notifications.claim(), range(2)))
        claimed = [r for r in claims if r['state'] == 'claimed']
        self.assertEqual(len(claimed), 1)
        self.assertEqual(set(claimed[0]), {'state','deliveryId','requestCount'})
        self.assertEqual(claimed[0]['requestCount'], 1)
        notifications.acknowledge(claimed[0]['deliveryId'], 'handed_to_windows')
        self.assertEqual(self.store.poll(request['requestId'], request['requestSecret'])['status'], 'pending')
        self.now += 301
        self.assertEqual(notifications.claim(), {'state':'idle'})
        dashboard = self.store.dashboard()
        self.assertEqual(dashboard['notifications']['pendingRequests'], 1)
        self.assertEqual(dashboard['notifications']['handedToWindows'], 1)
        self.assertNotIn('sms', dashboard)

    def test_interruption_never_blindly_retries_and_late_ack_rejected(self):
        self.request(); self.now += 31
        notifications = LocalNotifications(self.store)
        claim = notifications.claim()
        self.now += 301
        self.assertEqual(notifications.claim(), {'state':'idle'})
        with self.assertRaises(Refused):
            notifications.acknowledge(claim['deliveryId'], 'handed_to_windows')
        self.assertEqual(self.store.dashboard()['notifications']['unknown'], 1)

    def test_deferred_retries_bounded_and_handled_requests_disappear(self):
        request = self.request(); self.now += 31
        notifications = LocalNotifications(self.store)
        claim = notifications.claim()
        notifications.acknowledge(claim['deliveryId'], 'deferred')
        self.assertEqual(notifications.claim(), {'state':'throttled'})
        self.now += 301
        claim2 = notifications.claim()
        self.assertNotEqual(claim['deliveryId'], claim2['deliveryId'])
        notifications.acknowledge(claim2['deliveryId'], 'deferred')
        self.store.deny(request['requestId']); self.now += 301
        self.assertEqual(notifications.claim(), {'state':'idle'})
        self.assertEqual(self.store.dashboard()['notifications']['pendingRequests'], 0)

    def test_outcome_cannot_include_commands_or_seen_claim(self):
        self.request(); self.now += 31
        notifications = LocalNotifications(self.store)
        claim = notifications.claim()
        for value in ['seen', 'approved', 'powershell.exe', {'url':'https://example.test'}]:
            with self.assertRaises((Refused,TypeError)):
                notifications.acknowledge(claim['deliveryId'], value)

    def delivered(self, notifications, outcome='handed_to_windows'):
        self.now += 31
        claim = notifications.claim()
        self.assertEqual(claim['state'], 'claimed')
        notifications.acknowledge(claim['deliveryId'], outcome)
        return claim

    def test_still_pending_request_is_reminded_once_per_window(self):
        self.request()
        notifications = LocalNotifications(self.store)
        first = self.delivered(notifications)
        self.now += REMIND_AFTER_SECONDS - 60
        self.assertEqual(notifications.claim(), {'state': 'idle'})
        self.now += 60
        reminder = notifications.claim()
        self.assertEqual((reminder['state'], reminder['requestCount']), ('claimed', 1))
        self.assertNotEqual(first['deliveryId'], reminder['deliveryId'])
        notifications.acknowledge(reminder['deliveryId'], 'handed_to_windows')
        self.now += 301
        self.assertEqual(notifications.claim(), {'state': 'idle'})
        self.now += REMIND_AFTER_SECONDS
        self.assertEqual(notifications.claim()['state'], 'claimed')

    def test_resolved_requests_are_never_reminded(self):
        notifications = LocalNotifications(self.store)
        denied, approved = self.request(), self.request()
        self.delivered(notifications)
        self.store.deny(denied['requestId'])
        self.store.approve(approved['requestId'], 'local_user', [])
        self.now += REMIND_AFTER_SECONDS + 1
        self.assertEqual(notifications.claim(), {'state': 'idle'})

    def test_reminders_obey_spacing_and_hourly_cap(self):
        self.request()
        notifications = LocalNotifications(self.store)
        self.delivered(notifications)
        self.now += REMIND_AFTER_SECONDS
        with self.store.connect() as db:
            for n in range(6):
                db.execute('INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?)', (f'cap-{n}', 'local_deferred', self.now-60, self.now-60, 1, None))
        self.assertEqual(notifications.claim(), {'state': 'throttled'})
        self.now += 3600
        reminder = notifications.claim()
        self.assertEqual(reminder['state'], 'claimed')
        self.assertEqual(notifications.claim(), {'state': 'throttled'})
