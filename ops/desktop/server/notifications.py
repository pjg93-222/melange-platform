"""Retired, unscheduled SMS boundary retained for historical recovery tests only.

The active flow is local_notifications.py. No production route, CLI or startup
imports this module. No provider SDK, network call or remote recipient exists.

A future verified provider receives only the fixed protected administrator
destination and a generic count. A timeout/unknown outcome is never blindly retried.
The local dashboard remains independent of this optional notification channel.
"""
import hashlib
import hmac
import json
import math
import re
import threading
import uuid

from desktop_admin import Refused, atomic_write, canonical, protect

# The administrator-approved destination is stored only in protected local state.
APPROVED_DESTINATION_SHA256 = '5cc27c800dec36f9808f96a2029781eb1a6f7d8cf29d4cb7ce31d104a3818261'


class DisabledSMSProvider:
    configured = False

    def send(self, *, destination, message, delivery_id):
        raise Refused('SMS provider and verified sender are not configured')


def configure_destination(store, destination):
    """Local setup only; never exposed by an HTTP route or recipient payload."""
    if (not isinstance(destination, str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}', destination)
            or not hmac.compare_digest(hashlib.sha256(destination.encode('ascii')).hexdigest(), APPROVED_DESTINATION_SHA256)):
        raise Refused('only the approved administrator destination is permitted')
    value = {'schemaVersion':1, 'destination':destination, 'provider':'disabled'}
    atomic_write(store.root/'notification-settings.secret', protect(canonical(value)), secret=True)


class NotificationOutbox:
    def __init__(self, store, provider=None, timeout_seconds=10):
        if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or not .05 <= timeout_seconds <= 15:
            raise Refused('bounded notification deadline required')
        self.store = store
        self.provider = provider or DisabledSMSProvider()
        self.timeout_seconds = timeout_seconds
        self.lock = threading.Lock()

    def destination(self):
        path = self.store.root/'notification-settings.secret'
        if not path.exists(): return None
        value = json.loads(protect(path.read_bytes(), decrypt=True))
        if set(value) != {'schemaVersion','destination','provider'} or value['schemaVersion'] != 1 or value['provider'] != 'disabled':
            raise Refused('unsupported local notification settings')
        destination = value['destination']
        if not isinstance(destination, str) or not hmac.compare_digest(hashlib.sha256(destination.encode('ascii')).hexdigest(), APPROVED_DESTINATION_SHA256):
            raise Refused('invalid administrator destination')
        return destination

    def dispatch_once(self):
        # Only an explicit future provider integration can supply configured=True.
        if not self.provider.configured:
            return {'state':'pending_provider'}
        with self.lock:
            destination = self.destination()
            if destination is None: return {'state':'pending_provider'}
            now = self.store.now()
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM notification_deliveries WHERE state IN ('sending','unknown')").fetchone():
                    return {'state':'delivery_review_required'}
                recent = db.execute('SELECT COUNT(*),MAX(created) FROM notification_deliveries WHERE created>?', (now-3600,)).fetchone()
                if recent[0] >= 3 or (recent[1] is not None and now-recent[1] < 600):
                    return {'state':'throttled'}
                db.execute("UPDATE notification_outbox SET state='not_required' WHERE state='pending_provider' AND request_id IN (SELECT request_id FROM requests WHERE state!='pending' OR expires<=?)", (now,))
                rows = db.execute("SELECT id,created FROM notification_outbox WHERE kind='access_requested' AND state='pending_provider' ORDER BY created LIMIT 100").fetchall()
                if not rows: return {'state':'idle'}
                if now-max(row['created'] for row in rows) < 120: return {'state':'debouncing'}
                delivery_id = str(uuid.uuid4())
                db.execute('INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?)', (delivery_id,'sending',now,None,len(rows),None))
                db.executemany("UPDATE notification_outbox SET state='sending',delivery_id=?,attempts=attempts+1 WHERE id=?", [(delivery_id,row['id']) for row in rows])
            message = f'Application access review: {len(rows)} request(s) are pending on your administrator laptop. Open the local dashboard to review.'
            # The local ID is correlation only, not a claimed provider dedup key.
            # The adapter should also bound its transport. An over-deadline daemon
            # cannot update durable state later; unknown blocks any blind retry.
            state, reference = 'sent', None
            finished, outcome = threading.Event(), []
            def send():
                try:
                    outcome.append(self.provider.send(destination=destination, message=message, delivery_id=delivery_id))
                except Exception:
                    outcome.append(None)
                finally:
                    finished.set()
            threading.Thread(target=send,daemon=True,name='optional-sms-delivery').start()
            try:
                if not finished.wait(self.timeout_seconds): raise TimeoutError('unknown delivery outcome')
                reference = outcome[0]
                if not isinstance(reference, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', reference):
                    raise ValueError('unverified provider receipt')
            except Exception:
                state, reference = 'unknown', None
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE notification_deliveries SET state=?,completed=?,provider_reference=? WHERE id=?', (state,self.store.now(),reference,delivery_id))
                db.execute('UPDATE notification_outbox SET state=? WHERE delivery_id=?', (state,delivery_id))
            return {'state':state, 'requestCount':len(rows)}
