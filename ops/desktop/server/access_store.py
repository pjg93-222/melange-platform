"""Application access approvals, independent of package publishing and passwords.

Only local administrator code calls approve/revoke. Network callers receive no
publishing authority, password material, arbitrary command or file operations.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519
from desktop_admin import (APPS, VERSION, Refused, atomic_write, b64,
                           canonical, digest, protect)

ROLES = {'melange': 'local_user', 'mentat': 'local_user', 'terrier': 'local_administrator'}
DISTRICTS = {'co-05', 'or-05', 'ct-01'}
TARGETS = {('win32', 'x64')}
EVENT_TYPES = {
    'melange': {'assertion_saved', 'notes_updated', 'collection_completed', 'dataset_imported'},
    'mentat': {'collection_completed', 'report_created', 'dataset_imported'},
    'terrier': {'record_updated', 'walk_saved', 'snapshot_activated', 'dataset_imported'},
}
SECRET = re.compile(r'^[A-Za-z0-9_-]{43,86}$')
REQUEST_LIFETIME = 86400
KEY_LIFETIME = 600
RETRY_LIFETIME = 600
PRESENCE_LIFETIME = 180
MAX_PENDING = 100
MAX_ACTIVITY = 1000000


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise Refused('invalid access request fields')


def identifier(value):
    if not isinstance(value, str):
        raise Refused('invalid identifier')
    result = str(uuid.UUID(value))
    if result != value or uuid.UUID(result).int == 0:
        raise Refused('canonical UUID required')
    return result


def secret_hash(value):
    if not isinstance(value, str) or not SECRET.fullmatch(value):
        raise Refused('invalid access secret')
    return digest(value.encode('ascii'))


def scope(app, role, districts):
    if app not in ROLES or role != ROLES[app]:
        raise Refused('unsupported local role')
    if (not isinstance(districts, list) or any(not isinstance(d, str) for d in districts)
            or districts != sorted(set(districts)) or not set(districts) <= DISTRICTS
            or (app == 'terrier' and not districts) or (app != 'terrier' and districts)):
        raise Refused('invalid district scope')


def event_scope(grant, district, allow_none=False):
    approved = json.loads(grant['district_ids'])
    if grant['app'] == 'terrier':
        if district is None and allow_none:
            return
        if not isinstance(district, str) or district not in approved:
            raise Refused('district access required')
    elif district is not None:
        raise Refused('unexpected district')


def data_identifier(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value):
        return value
    return identifier(value)


class AccessStore:
    def __init__(self, publisher, clock=time.time):
        self.publisher = publisher
        self.root = Path(publisher.root)
        self.db = self.root / 'access.sqlite3'
        self.key_path = self.root / 'keys' / 'application-access.key'
        self.config_path = self.root / 'access-trust.json'
        self.clock = clock

    def now(self):
        return int(self.clock())

    @contextlib.contextmanager
    def connect(self):
        if not self.db.is_file():
            raise Refused('application access service is not initialized')
        connection = sqlite3.connect(self.db, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self, checkpoint=lambda _stage: None):
        self.publisher.config()
        with (self.root / 'access-initialization.lock').open('a+b') as lock:
            if os.name == 'nt':
                import msvcrt
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise Refused('application access initialization is already running') from None
                try:
                    return self._initialize(checkpoint)
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self._initialize(checkpoint)

    def _initialize(self, checkpoint):
        # A protected recovery receipt preserves both signing identity and
        # server UUID across every interrupted key/config/database boundary.
        self.publisher.config()
        receipt_path = self.root / 'access-initialization.dpapi'
        if receipt_path.exists():
            receipt = json.loads(protect(receipt_path.read_bytes(), decrypt=True))
            fields(receipt, {'schemaVersion','config','privateKey','completed'})
            if receipt['schemaVersion'] != 1 or receipt['completed']:
                raise Refused('application access is already initialized')
            key = ed25519.Ed25519PrivateKey.from_private_bytes(__import__('base64').b64decode(receipt['privateKey'], validate=True))
            config = receipt['config']
        else:
            if any(p.exists() for p in (self.db, self.key_path, self.config_path)):
                raise Refused('unrecognized partial access state requires local recovery; no identity will be overwritten')
            key = ed25519.Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes_raw()
            config = {'schemaVersion':1, 'serverId':str(uuid.uuid4()), 'keyId':digest(public)[:24], 'publicKeys':{digest(public)[:24]:b64(public)}}
            receipt = {'schemaVersion':1,'config':config,'privateKey':b64(key.private_bytes_raw()),'completed':False}
            atomic_write(receipt_path, protect(canonical(receipt)), secret=True)
        public = key.public_key().public_bytes_raw()
        if config['publicKeys'] != {digest(public)[:24]:b64(public)} or config['keyId'] != digest(public)[:24]:
            raise Refused('initialization identity does not match')
        identifier(config['serverId'])
        checkpoint('receipt')
        if self.key_path.exists():
            if not hmac.compare_digest(protect(self.key_path.read_bytes(), decrypt=True), key.private_bytes_raw()):
                raise Refused('existing application key differs; preserved')
        else:
            atomic_write(self.key_path, protect(key.private_bytes_raw()), secret=True)
        checkpoint('key')
        if self.config_path.exists():
            if self.config_path.read_bytes() != canonical(config):
                raise Refused('existing application identity differs; preserved')
        else:
            atomic_write(self.config_path, canonical(config), secret=True)
        checkpoint('config')
        if not self.db.exists():
            temporary = self.root / ('access-' + config['serverId'] + '.initializing.sqlite3')
            # This exact generated staging path belongs to the durable receipt.
            # A prior interrupted SQLite creation may be resumed only if empty.
            if temporary.exists():
                with contextlib.closing(sqlite3.connect(temporary)) as previous:
                    tables = {r[0] for r in previous.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables:
                    self._verify_initialized_database(temporary)
                else:
                    self._create_access_database(temporary)
            else:
                self._create_access_database(temporary)
            self._verify_initialized_database(temporary)
            with temporary.open('r+b') as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, self.db)
        self._verify_initialized_database(self.db)
        checkpoint('database')
        receipt['completed'] = True
        atomic_write(receipt_path, protect(canonical(receipt)), secret=True)
        return config

    @staticmethod
    def _verify_initialized_database(path):
        expected = {'requests','principals','grants','presence','activity','notification_outbox','notification_deliveries','update_exchanges','access_audit','owner_provisioning'}
        with contextlib.closing(sqlite3.connect(path)) as connection:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != expected or connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise Refused('incomplete or unexpected access database schema; preserved for recovery')

    @staticmethod
    def _create_access_database(path):
        with contextlib.closing(sqlite3.connect(path)) as connection:
            connection.executescript('''
              PRAGMA foreign_keys=ON;
              BEGIN IMMEDIATE;
              CREATE TABLE requests (
                request_id TEXT PRIMARY KEY, app TEXT NOT NULL, device_id TEXT NOT NULL,
                platform TEXT NOT NULL, arch TEXT NOT NULL, display_name TEXT NOT NULL,
                secret_hash TEXT NOT NULL, source_hash TEXT NOT NULL,
                state TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL,
                role TEXT, district_ids TEXT, key_hash TEXT, key_expires INTEGER,
                receipt BLOB, receipt_expires INTEGER);
              CREATE INDEX requests_source_time ON requests(source_hash,created);
              CREATE INDEX requests_device ON requests(app,device_id);
              CREATE TABLE principals (
                app TEXT NOT NULL, device_id TEXT NOT NULL, principal_id TEXT NOT NULL UNIQUE,
                account_identity TEXT NOT NULL UNIQUE, sequence INTEGER NOT NULL,
                PRIMARY KEY(app,device_id));
              CREATE TABLE grants (
                grant_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE REFERENCES requests(request_id),
                principal_id TEXT NOT NULL REFERENCES principals(principal_id), app TEXT NOT NULL,
                device_id TEXT NOT NULL, platform TEXT NOT NULL, arch TEXT NOT NULL,
                account_identity TEXT NOT NULL, role TEXT NOT NULL, district_ids TEXT NOT NULL,
                sequence INTEGER NOT NULL, status_sequence INTEGER NOT NULL,
                issued INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                bearer_hash TEXT NOT NULL UNIQUE, envelope TEXT NOT NULL);
              CREATE TABLE presence (
                grant_id TEXT PRIMARY KEY REFERENCES grants(grant_id), session_id TEXT NOT NULL,
                state TEXT NOT NULL, version TEXT NOT NULL, last_seen INTEGER NOT NULL, district_id TEXT);
              CREATE TABLE activity (
                grant_id TEXT NOT NULL REFERENCES grants(grant_id), event_id TEXT NOT NULL,
                principal_id TEXT NOT NULL REFERENCES principals(principal_id),
                occurred INTEGER NOT NULL, received INTEGER NOT NULL, kind TEXT NOT NULL,
                counts TEXT NOT NULL, payload_hash TEXT NOT NULL, district_id TEXT, references_json TEXT NOT NULL,
                PRIMARY KEY(principal_id,event_id));
              CREATE TABLE notification_outbox (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(request_id),
                kind TEXT NOT NULL, state TEXT NOT NULL, created INTEGER NOT NULL,
                delivery_id TEXT, attempts INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE notification_deliveries (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, created INTEGER NOT NULL,
                completed INTEGER, request_count INTEGER NOT NULL, provider_reference TEXT);
              CREATE TABLE update_exchanges (
                grant_id TEXT PRIMARY KEY REFERENCES grants(grant_id), receipt BLOB NOT NULL);
              CREATE TABLE owner_provisioning (
                profile_hash TEXT PRIMARY KEY, config_hash TEXT NOT NULL,
                grant_id TEXT NOT NULL UNIQUE REFERENCES grants(grant_id),
                receipt BLOB NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE access_audit (
                id INTEGER PRIMARY KEY, at INTEGER NOT NULL, action TEXT NOT NULL,
                request_id TEXT, grant_id TEXT);
              COMMIT;
            ''')

    def config(self):
        value = json.loads(self.config_path.read_text(encoding='utf-8'))
        if value.get('schemaVersion') != 1 or not 1 <= len(value.get('publicKeys', {})) <= 4:
            raise Refused('invalid access trust configuration')
        return value

    def descriptor(self, url):
        origin = self.publisher.origin(url)
        config, update = self.config(), self.publisher.config()
        return {'schemaVersion': 1, 'purpose': 'melange-desktop-trust',
                'serverId': config['serverId'], 'url': origin,
                'tlsSpkiSha256': update['tlsSpkiSha256'],
                'accessPublicKeys': config['publicKeys'],
                'updatePublicKeysByApp': {app: update['apps'][app]['publicKeys'] for app in sorted(APPS)}}

    def sign(self, payload):
        config = self.config()
        raw = canonical(payload)
        key = ed25519.Ed25519PrivateKey.from_private_bytes(protect(self.key_path.read_bytes(), decrypt=True))
        return {'schemaVersion': 1, 'keyId': config['keyId'], 'payload': b64(raw),
                'signature': b64(key.sign(raw))}

    def audit(self, connection, action, request=None, grant=None):
        connection.execute('INSERT INTO access_audit(at,action,request_id,grant_id) VALUES (?,?,?,?)',
                           (self.now(), action, request, grant))

    def request(self, value, source_address):
        fields(value, {'requestId', 'deviceId', 'appId', 'platform', 'arch', 'displayName', 'requestSecret'})
        request_id, device_id = identifier(value['requestId']), identifier(value['deviceId'])
        app, platform, arch = value['appId'], value['platform'], value['arch']
        if app not in APPS or (platform, arch) not in TARGETS:
            raise Refused('invalid access target')
        display = value['displayName']
        if not isinstance(display, str) or not 1 <= len(display) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in display):
            raise Refused('invalid requester name')
        hashed, now = secret_hash(value['requestSecret']), self.now()
        # Hash the source for quota accounting; no browsing or workstation data is recorded.
        source_hash = digest((self.config()['serverId'] + ':' + source_address).encode())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                if (existing['app'], existing['device_id'], existing['platform'], existing['arch'], existing['display_name'], existing['secret_hash']) != (app, device_id, platform, arch, display, hashed):
                    raise Refused('request identity conflict')
                return self._request_status(existing)
            db.execute("UPDATE requests SET state='expired' WHERE (expires<=? OR (state='approved' AND key_expires<=?)) AND state IN ('pending','approved')", (now, now))
            db.execute('UPDATE requests SET receipt=NULL WHERE receipt_expires<=?', (now,))
            if (db.execute("SELECT COUNT(*) FROM requests WHERE state IN ('pending','approved')").fetchone()[0] >= MAX_PENDING
                    or db.execute('SELECT COUNT(*) FROM requests WHERE source_hash=? AND created>?', (source_hash, now-3600)).fetchone()[0] >= 10
                    or db.execute('SELECT COUNT(*) FROM requests WHERE created>?', (now-3600,)).fetchone()[0] >= 100
                    or db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] >= 10000
                    or db.execute("SELECT 1 FROM requests WHERE app=? AND device_id=? AND state IN ('pending','approved')", (app, device_id)).fetchone()):
                raise Refused('access request capacity reached')
            db.execute('INSERT INTO requests(request_id,app,device_id,platform,arch,display_name,secret_hash,source_hash,state,created,expires) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, app, device_id, platform, arch, display, hashed, source_hash, 'pending', now, now+REQUEST_LIFETIME))
            db.execute('INSERT INTO notification_outbox(id,request_id,kind,state,created) VALUES (?,?,?,?,?)',
                       (str(uuid.uuid4()), request_id, 'access_requested', 'pending_local', now))
            self.audit(db, 'access-requested', request_id)
        return {'requestId': request_id, 'status': 'pending', 'expiresAt': now+REQUEST_LIFETIME}

    def _request_status(self, row):
        status = row['state']
        if status in ('pending', 'approved') and row['expires'] <= self.now():
            status = 'expired'
        if status == 'approved' and row['key_expires'] <= self.now():
            status = 'expired'
        expires = min(row['expires'], row['key_expires']) if row['state'] == 'approved' else row['expires']
        if row['state'] == 'used':
            expires = row['receipt_expires']
        return {'requestId': row['request_id'], 'status': status, 'expiresAt': expires}

    def poll(self, request_id, secret):
        identifier(request_id)
        hashed = secret_hash(secret)
        with self.connect() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=? AND secret_hash=?', (request_id, hashed)).fetchone()
        if not row:
            raise Refused('request authentication required')
        return self._request_status(row)

    def approve(self, request_id, role, district_ids):
        identifier(request_id)
        key = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if not row or row['state'] != 'pending' or row['expires'] <= self.now():
                raise Refused('request is not pending')
            scope(row['app'], role, district_ids)
            db.execute("UPDATE requests SET state='approved',role=?,district_ids=?,key_hash=?,key_expires=? WHERE request_id=?",
                       (role, canonical(district_ids).decode(), secret_hash(key), self.now()+KEY_LIFETIME, request_id))
            self.audit(db, 'access-approved', request_id)
        return {'requestId': request_id, 'activationKey': key, 'expiresAt': self.now()+KEY_LIFETIME}

    def deny(self, request_id):
        identifier(request_id)
        with self.connect() as db:
            count = db.execute("UPDATE requests SET state='denied' WHERE request_id=? AND state IN ('pending','approved')", (request_id,)).rowcount
            if count != 1:
                raise Refused('request is not pending')
            self.audit(db, 'access-denied', request_id)

    def replace_key(self, request_id):
        """Explicit local action; a lost response never silently retrieves plaintext."""
        identifier(request_id)
        key = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if not row or row['state'] != 'approved' or row['expires'] <= self.now():
                raise Refused('request has no replaceable unredeemed key')
            db.execute('UPDATE requests SET key_hash=?,key_expires=? WHERE request_id=?',
                       (secret_hash(key), self.now()+KEY_LIFETIME, request_id))
            self.audit(db, 'access-key-replaced', request_id)
        return {'requestId':request_id, 'activationKey':key, 'expiresAt':min(row['expires'], self.now()+KEY_LIFETIME)}

    def activate(self, value, request_secret):
        fields(value, {'requestId', 'deviceId', 'appId', 'platform', 'arch', 'activationKey'})
        request_id, device_id = identifier(value['requestId']), identifier(value['deviceId'])
        hashed, key_hash = secret_hash(request_secret), secret_hash(value['activationKey'])
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if (not row or not hmac.compare_digest(row['secret_hash'], hashed) or not row['key_hash'] or not hmac.compare_digest(row['key_hash'], key_hash)
                    or (row['app'], row['device_id'], row['platform'], row['arch']) != (value['appId'], device_id, value['platform'], value['arch'])):
                raise Refused('activation binding failed')
            if row['state'] == 'used':
                issued = db.execute('SELECT revoked FROM grants WHERE request_id=?', (request_id,)).fetchone()
                if not row['receipt'] or row['receipt_expires'] <= self.now() or not issued or issued['revoked']:
                    raise Refused('activation retry expired; request fresh administrator approval')
                return json.loads(protect(bytes(row['receipt']), decrypt=True))
            if row['state'] != 'approved' or row['expires'] <= self.now() or row['key_expires'] <= self.now():
                raise Refused('activation key expired or unavailable')
            return self._issue_grant(db, row)

    def _issue_grant(self, db, row):
        """Internal transaction helper; caller validates approved request binding."""
        request_id, device_id = row['request_id'], row['device_id']
        scope(row['app'], row['role'], json.loads(row['district_ids']))
        principal = db.execute('SELECT * FROM principals WHERE app=? AND device_id=?', (row['app'], device_id)).fetchone()
        if principal:
            principal_id, account, sequence = principal['principal_id'], principal['account_identity'], principal['sequence']+1
            db.execute('UPDATE principals SET sequence=? WHERE app=? AND device_id=?', (sequence, row['app'], device_id))
            db.execute('UPDATE grants SET revoked=1,status_sequence=? WHERE app=? AND device_id=?', (sequence, row['app'], device_id))
        else:
            principal_id = str(uuid.uuid4()); account = 'recipient_' + uuid.UUID(principal_id).hex; sequence = 1
            db.execute('INSERT INTO principals VALUES (?,?,?,?,?)', (row['app'], device_id, principal_id, account, sequence))
        grant_id, bearer = str(uuid.uuid4()), secrets.token_urlsafe(48)
        payload = {'schemaVersion': 1, 'purpose': 'melange-desktop-access', 'serverId': self.config()['serverId'],
                   'grantId': grant_id, 'principalId': principal_id, 'accountIdentity': account,
                   'requestId': request_id, 'appId': row['app'], 'deviceId': device_id,
                   'platform': row['platform'], 'arch': row['arch'], 'role': row['role'], 'districtIds': json.loads(row['district_ids']),
                   'sequence': sequence, 'issuedAt': self.now(), 'offlineAllowed': True}
        envelope = self.sign(payload)
        db.execute('INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (grant_id, request_id, principal_id, row['app'], device_id, row['platform'], row['arch'], account,
                    row['role'], row['district_ids'], sequence, sequence, self.now(), 0, secret_hash(bearer), canonical(envelope).decode()))
        response = {'grant': envelope, 'accessToken': bearer}
        db.execute("UPDATE requests SET state='used',receipt=?,receipt_expires=? WHERE request_id=?",
                   (protect(canonical(response)), self.now()+RETRY_LIFETIME, request_id))
        self.audit(db, 'access-activated', request_id, grant_id)
        return response

    def authenticate(self, bearer, allow_revoked=False):
        hashed = secret_hash(bearer)
        with self.connect() as db:
            row = db.execute('SELECT * FROM grants WHERE bearer_hash=?', (hashed,)).fetchone()
        if not row or (row['revoked'] and not allow_revoked):
            raise Refused('application access required')
        return dict(row)

    def status(self, bearer):
        row = self.authenticate(bearer, allow_revoked=True)
        return self.sign({'schemaVersion': 1, 'purpose': 'melange-desktop-access-status',
                          'serverId': self.config()['serverId'], 'grantId': row['grant_id'],
                          'principalId': row['principal_id'], 'accountIdentity': row['account_identity'],
                          'appId': row['app'], 'deviceId': row['device_id'], 'platform': row['platform'],
                          'arch': row['arch'], 'role': row['role'], 'districtIds': json.loads(row['district_ids']),
                          'sequence': row['status_sequence'], 'revoked': bool(row['revoked']), 'issuedAt': self.now()})

    def revoke(self, grant_id):
        identifier(grant_id)
        with self.connect() as db:
            self.attach_publisher(db)
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM grants WHERE grant_id=?', (grant_id,)).fetchone()
            if not row:
                raise Refused('unknown grant')
            if row['revoked']:
                return
            principal = db.execute('SELECT sequence FROM principals WHERE principal_id=?', (row['principal_id'],)).fetchone()
            sequence = principal['sequence']+1
            db.execute('UPDATE principals SET sequence=? WHERE principal_id=?', (sequence, row['principal_id']))
            db.execute('UPDATE grants SET revoked=1,status_sequence=? WHERE grant_id=?', (sequence, grant_id))
            db.execute("UPDATE presence SET state='signed_out' WHERE grant_id=?", (grant_id,))
            exchange = db.execute('SELECT x.receipt FROM update_exchanges x JOIN grants g ON x.grant_id=g.grant_id WHERE g.principal_id=? AND g.sequence<=? ORDER BY g.sequence DESC LIMIT 1',
                                  (row['principal_id'], row['sequence'])).fetchone()
            if exchange:
                credential = json.loads(protect(bytes(exchange['receipt']), decrypt=True))
                # Only this exchange's token may be revoked, never unrelated enrollment.
                db.execute('UPDATE publisher.devices SET revoked=1 WHERE app=? AND device_id=? AND token_hash=?',
                           (row['app'], row['device_id'], secret_hash(credential['deviceToken'])))
            self.audit(db, 'access-revoked', row['request_id'], grant_id)

    def presence(self, bearer, value):
        fields(value, {'sessionId', 'state', 'version', 'districtId'})
        row = self.authenticate(bearer)
        session = identifier(value['sessionId'])
        if value['state'] not in {'active', 'locked', 'signed_out'} or not isinstance(value['version'], str) or not VERSION.fullmatch(value['version']):
            raise Refused('invalid presence metadata')
        event_scope(row, value['districtId'], allow_none=value['state'] != 'active')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT revoked FROM grants WHERE grant_id=?', (row['grant_id'],)).fetchone()[0]:
                raise Refused('application access revoked')
            db.execute('INSERT INTO presence VALUES (?,?,?,?,?,?) ON CONFLICT(grant_id) DO UPDATE SET session_id=excluded.session_id,state=excluded.state,version=excluded.version,last_seen=excluded.last_seen,district_id=excluded.district_id',
                       (row['grant_id'], session, value['state'], value['version'], self.now(), value['districtId']))
        return {'ok': True, 'receivedAt': self.now()}

    def events(self, bearer, value):
        fields(value, {'events'})
        grant = self.authenticate(bearer)
        events = value['events']
        if not isinstance(events, list) or not 1 <= len(events) <= 100:
            raise Refused('bounded event batch required')
        checked, seen = [], set()
        for event in events:
            fields(event, {'eventId', 'type', 'occurredAt', 'districtId', 'status', 'counts'}, {'datasetId', 'revisionId', 'entityId'})
            event_id = identifier(event['eventId'])
            counts = event['counts']
            event_scope(grant, event['districtId'])
            for key in ('datasetId', 'revisionId', 'entityId'):
                if key in event:
                    data_identifier(event[key])
            if (event_id in seen or event['type'] not in EVENT_TYPES[grant['app']] or event['status'] != 'succeeded'
                    or type(event['occurredAt']) is not int or not 946684800 <= event['occurredAt'] <= self.now()+300
                    or not isinstance(counts, dict) or set(counts) - {'items', 'files', 'records'}
                    or any(type(x) is not int or not 0 <= x <= 1000000000 for x in counts.values())):
                raise Refused('invalid typed activity metadata')
            seen.add(event_id); checked.append((event, digest(canonical(event))))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT revoked FROM grants WHERE grant_id=?', (grant['grant_id'],)).fetchone()[0]:
                raise Refused('application access revoked')
            new = []
            for event, hashed in checked:
                previous = db.execute('SELECT payload_hash FROM activity WHERE principal_id=? AND event_id=?', (grant['principal_id'], event['eventId'])).fetchone()
                if previous and previous['payload_hash'] != hashed:
                    raise Refused('event identity conflict')
                if not previous:
                    new.append((event, hashed))
            if db.execute('SELECT COUNT(*) FROM activity').fetchone()[0] + len(new) > MAX_ACTIVITY:
                raise Refused('activity capacity reached; preserve the local outbox')
            for event, hashed in new:
                refs = {key: event[key] for key in ('datasetId', 'revisionId', 'entityId') if key in event}
                db.execute('INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?,?)', (grant['grant_id'], event['eventId'], grant['principal_id'], event['occurredAt'], self.now(), event['type'], canonical(event['counts']).decode(), hashed, event['districtId'], canonical(refs).decode()))
        return {'acknowledged': [event['eventId'] for event, _ in checked], 'receivedAt': self.now()}

    def dashboard(self):
        with self.connect() as db:
            requests = [dict(r) for r in db.execute('SELECT request_id,app,device_id,platform,arch,display_name,state,created,expires,key_expires,receipt_expires,role,district_ids FROM requests ORDER BY created DESC LIMIT 100')]
            grants = [dict(r) for r in db.execute('SELECT g.grant_id,g.app,g.device_id,g.principal_id,g.account_identity,g.role,g.district_ids,g.revoked,p.state,p.version,p.last_seen,p.district_id,r.display_name FROM grants g JOIN requests r ON g.request_id=r.request_id LEFT JOIN presence p ON p.grant_id=g.grant_id ORDER BY g.issued DESC LIMIT 500')]
            events = [dict(r) for r in db.execute('SELECT a.event_id,a.kind,a.occurred,a.received,a.counts,a.district_id,a.references_json,g.app,g.device_id,g.principal_id,r.display_name FROM activity a JOIN grants g ON g.grant_id=a.grant_id JOIN requests r ON g.request_id=r.request_id ORDER BY received DESC LIMIT 100')]
            pending = db.execute("SELECT COUNT(*) FROM requests WHERE state='pending' AND expires>?", (self.now(),)).fetchone()[0]
            handed = db.execute("SELECT COUNT(*) FROM notification_deliveries WHERE state='local_handed_to_windows'").fetchone()[0]
            unknown = db.execute("SELECT COUNT(*) FROM notification_deliveries WHERE state='local_unknown'").fetchone()[0]
        for grant in grants:
            grant['online'] = not grant['revoked'] and grant['state'] == 'active' and (grant['last_seen'] or 0) > self.now()-PRESENCE_LIFETIME
        for request in requests:
            request['can_replace_key'] = request['state'] == 'approved' and request['expires'] > self.now()
            request['state'] = self._request_status(request)['status']
        return {'requests': requests, 'grants': grants, 'activity': events,
                'notifications': {'channel': 'windows-desktop', 'pendingRequests': pending, 'handedToWindows': handed, 'unknown': unknown}, 'serverTime': self.now()}

    def update_enrollment(self, bearer, value, service_url):
        """One approval, separate download token; atomic across the two local stores."""
        fields(value, {'currentVersion'})
        if not isinstance(value['currentVersion'], str) or not VERSION.fullmatch(value['currentVersion']):
            raise Refused('invalid application version')
        grant = self.authenticate(bearer)
        trust = self.descriptor(service_url)
        current_trust = {'schemaVersion': 1, 'url': trust['url'], 'appId': grant['app'],
                         'tlsSpkiSha256': trust['tlsSpkiSha256'],
                         'publicKeys': trust['updatePublicKeysByApp'][grant['app']]}
        with self.connect() as db:
            self.attach_publisher(db)
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT revoked FROM grants WHERE grant_id=?', (grant['grant_id'],)).fetchone()[0]:
                raise Refused('application access revoked')
            previous = db.execute('SELECT receipt FROM update_exchanges WHERE grant_id=?', (grant['grant_id'],)).fetchone()
            if previous:
                device = db.execute('SELECT revoked FROM publisher.devices WHERE app=? AND device_id=?', (grant['app'], grant['device_id'])).fetchone()
                if not device or device['revoked']:
                    raise Refused('download access revoked')
                response = json.loads(protect(bytes(previous['receipt']), decrypt=True))
                # Credentials are idempotent; trust reflects the independently
                # distributed current descriptor, including authorized key rotation.
                response['trust'] = current_trust
                return response
            device = db.execute('SELECT * FROM publisher.devices WHERE app=? AND device_id=?', (grant['app'], grant['device_id'])).fetchone()
            if device:
                # A higher-sequence approval may explicitly rebind its own active
                # download credential. Existing unrelated credentials remain opaque.
                older = db.execute('SELECT x.receipt FROM update_exchanges x JOIN grants g ON x.grant_id=g.grant_id WHERE g.principal_id=? AND g.sequence<? ORDER BY g.sequence DESC LIMIT 1',
                                   (grant['principal_id'], grant['sequence'])).fetchone()
                if older and not device['revoked']:
                    response = json.loads(protect(bytes(older['receipt']), decrypt=True))
                    if hmac.compare_digest(device['token_hash'], secret_hash(response['deviceToken'])):
                        db.execute('INSERT INTO update_exchanges VALUES (?,?)', (grant['grant_id'], older['receipt']))
                        self.audit(db, 'update-enrollment-rebound', grant=grant['grant_id'])
                        response['trust'] = current_trust
                        return response
                raise Refused('device already enrolled; preserve existing download credential')
            code, token = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            db.execute('INSERT INTO publisher.pairings VALUES (?,?,?,1)', (secret_hash(code), grant['app'], self.now()+60))
            db.execute('INSERT INTO publisher.devices(device_id,app,token_hash,platform,arch,version) VALUES (?,?,?,?,?,?)',
                       (grant['device_id'], grant['app'], secret_hash(token), grant['platform'], grant['arch'], value['currentVersion']))
            db.execute('INSERT INTO publisher.audit VALUES (?,?,?,?,?)', (self.now(), 'device-enrolled', grant['app'], grant['device_id'], None))
            response = {'deviceToken': token, 'deviceId': grant['device_id'], 'appId': grant['app'],
                        'trust': current_trust}
            db.execute('INSERT INTO update_exchanges VALUES (?,?)', (grant['grant_id'], protect(canonical(response))))
            self.audit(db, 'update-enrolled', grant=grant['grant_id'])
            return response

    def attach_publisher(self, db):
        publisher_db = Path(self.publisher.db).resolve(strict=True)
        if publisher_db.parent != self.root.resolve(strict=True) or self.db.resolve(strict=True).parent != publisher_db.parent:
            raise Refused('publisher state must share protected directory and volume')
        db.execute('ATTACH DATABASE ? AS publisher', (str(publisher_db),))
        # Never silently change either live journal mode.
        for database in ('main', 'publisher'):
            if db.execute(f'PRAGMA {database}.journal_mode').fetchone()[0].lower() not in {'delete', 'truncate', 'persist'}:
                raise Refused('atomic enrollment requires rollback journal mode')
