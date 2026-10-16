from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notifications import NotificationOutbox, configure_destination
from desktop_admin import Refused
import test_access_store as store_tests


class NotificationTests(unittest.TestCase):
    def setUp(self): store_tests.AccessStoreTests.setUp(self)

    def legacy_request(self):
        value = store_tests.AccessStoreTests.request(self)
        # The active application never emits this retired SMS state anymore.
        with self.store.connect() as db:
            db.execute("UPDATE notification_outbox SET state='pending_provider'")
        return value

    def test_disabled_provider_keeps_durable_pending_and_destination_protected(self):
        request = self.legacy_request()
        self.assertEqual(NotificationOutbox(self.store).dispatch_once()['state'], 'pending_provider')
        self.assertEqual(self.store.dashboard()['notifications']['pendingRequests'], 1)
        with self.assertRaises(Refused): configure_destination(self.store, '+15555550123')
        configure_destination(self.store, '+16512953251')
        self.assertNotIn(b'+16512953251', (self.store.root/'notification-settings.secret').read_bytes())

    def test_debounce_generic_notice_and_no_recipient_input(self):
        request = self.legacy_request()
        configure_destination(self.store, '+16512953251')
        calls = []
        class Provider:
            configured = True
            def send(self, **kwargs): calls.append(kwargs); return 'fixture_receipt'
        outbox = NotificationOutbox(self.store, Provider())
        self.assertEqual(outbox.dispatch_once()['state'], 'debouncing')
        self.now += 121
        self.assertEqual(outbox.dispatch_once()['state'], 'sent')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['destination'], '+16512953251')
        for value in (request['displayName'],request['requestSecret'],request['requestId']):
            self.assertNotIn(value, calls[0]['message'])
        self.assertEqual(outbox.dispatch_once()['state'], 'throttled')

    def test_timeout_marks_unknown_and_never_blindly_retries(self):
        self.legacy_request()
        configure_destination(self.store, '+16512953251')
        calls = []
        class Provider:
            configured = True
            def send(self, **kwargs): calls.append(1); raise TimeoutError('unknown delivery outcome')
        self.now += 121
        self.assertEqual(NotificationOutbox(self.store, Provider()).dispatch_once()['state'], 'unknown')
        self.now += 7200
        self.assertEqual(NotificationOutbox(self.store, Provider()).dispatch_once()['state'], 'delivery_review_required')
        self.assertEqual(len(calls), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notification_deliveries WHERE state='unknown'").fetchone()[0], 1)

    def test_nonresponsive_provider_is_bounded_and_late_result_is_not_success(self):
        self.legacy_request()
        configure_destination(self.store, '+16512953251')
        release, finished = threading.Event(), threading.Event()
        class Provider:
            configured = True
            def send(self, **_kwargs):
                release.wait(5)
                finished.set()
                return 'late_receipt'
        self.now += 121
        started = time.monotonic()
        try:
            self.assertEqual(NotificationOutbox(self.store, Provider(), timeout_seconds=.05).dispatch_once()['state'], 'unknown')
            # Include real database overhead while proving dispatch never waits for
            # the held provider. A 180ms total bound was flaky on loaded machines.
            self.assertLess(time.monotonic()-started, 3)
            self.assertFalse(finished.is_set())
        finally:
            release.set()
        self.assertTrue(finished.wait(3))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM notification_deliveries').fetchone()[0], 'unknown')


if __name__ == '__main__': unittest.main()
