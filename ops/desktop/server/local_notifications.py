"""Local-only notification claims. Windows acceptance is not proof of human sight."""
import uuid

from access_store import identifier
from desktop_admin import Refused

DEBOUNCE = 30
LEASE = 90
INTERVAL = 300
REMIND_AFTER_SECONDS = 4 * 3600


class LocalNotifications:
    def __init__(self, access):
        self.access = access

    def claim(self):
        now = self.access.now()
        with self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # An interrupted native handoff has unknown outcome. Do not announce
            # it repeatedly; the durable access request remains on the dashboard.
            stale = [r[0] for r in db.execute("SELECT id FROM notification_deliveries WHERE state='local_claimed' AND created<=?", (now-LEASE,))]
            for delivery in stale:
                db.execute("UPDATE notification_deliveries SET state='local_unknown',completed=? WHERE id=?", (now, delivery))
                db.execute("UPDATE notification_outbox SET state='local_unknown' WHERE delivery_id=?", (delivery,))
            db.execute("UPDATE notification_outbox SET state='not_required' WHERE state IN ('pending_local','pending_provider','local_deferred') AND request_id IN (SELECT request_id FROM requests WHERE state!='pending' OR expires<=?)", (now,))
            recent = db.execute("SELECT COUNT(*),MAX(created) FROM notification_deliveries WHERE state LIKE 'local_%' AND created>?", (now-3600,)).fetchone()
            if recent[0] >= 6 or recent[1] is not None and now-recent[1] < INTERVAL:
                return {'state': 'throttled'}
            # A request still awaiting review is announced again once its last
            # delivery is older than REMIND_AFTER_SECONDS (same throttle applies).
            rows = db.execute("SELECT id,created FROM notification_outbox WHERE kind='access_requested' AND (state IN ('pending_local','pending_provider','local_deferred') OR state IN ('local_handed_to_windows','local_unknown') AND request_id IN (SELECT request_id FROM requests WHERE state='pending' AND expires>?) AND delivery_id IN (SELECT id FROM notification_deliveries WHERE created<=?)) ORDER BY created LIMIT 100", (now, now-REMIND_AFTER_SECONDS)).fetchall()
            if not rows:
                return {'state': 'idle'}
            if now-max(r['created'] for r in rows) < DEBOUNCE:
                return {'state': 'debouncing'}
            delivery = str(uuid.uuid4())
            db.execute('INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?)', (delivery, 'local_claimed', now, None, len(rows), None))
            db.executemany("UPDATE notification_outbox SET state='local_claimed',delivery_id=?,attempts=attempts+1 WHERE id=?", [(delivery, r['id']) for r in rows])
            return {'state': 'claimed', 'deliveryId': delivery, 'requestCount': len(rows)}

    def acknowledge(self, delivery, outcome):
        identifier(delivery)
        if outcome not in {'handed_to_windows', 'deferred', 'unknown'}:
            raise Refused('invalid local notification outcome')
        state = 'local_' + outcome
        with self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM notification_deliveries WHERE id=?', (delivery,)).fetchone()
            if not row or row['state'] not in {'local_claimed', state}:
                raise Refused('local notification claim unavailable')
            if row['state'] == 'local_claimed' and row['created']+LEASE <= self.access.now():
                raise Refused('local notification claim expired')
            db.execute('UPDATE notification_deliveries SET state=?,completed=? WHERE id=?', (state, self.access.now(), delivery))
            db.execute('UPDATE notification_outbox SET state=? WHERE delivery_id=?', (state, delivery))
        return {'state': outcome}
