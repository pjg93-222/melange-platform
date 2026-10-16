"""Explicit local owner provisioning; no network endpoint imports this issuer.

An atomic server transaction creates the ordinary grant and a DPAPI recovery
receipt. Profile/credential writes can then resume the exact same issuance.
"""
import base64
import contextlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import uuid

from access_store import fields, identifier, scope, ROLES, secret_hash
from desktop_admin import canonical, digest, protect, Refused

PACKAGING = Path(__file__).resolve().parents[1] / 'packaging'
if str(PACKAGING) not in sys.path:
    sys.path.insert(0, str(PACKAGING))
import native_access_state as protected_state
import native_credentials


def trust_hash(value):
    return digest(canonical({key: value[key] for key in ('schemaVersion','purpose','serverId','tlsSpkiSha256','accessPublicKeys')}))


def _payload(envelope):
    return json.loads(base64.b64decode(envelope['payload'], validate=True))


def _account(data_root, user_id, username):
    if not isinstance(user_id, str) or not re.fullmatch(r'[1-9][0-9]{0,18}', user_id) or int(user_id)>9223372036854775807:
        raise Refused('explicit canonical existing account ID required')
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_@.+-]{1,150}', username):
        raise Refused('explicit existing account name required')
    root = Path(data_root).resolve(strict=True)
    path = root / 'accounts.sqlite3'
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
        raise Refused('existing district account database required')
    with contextlib.closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
        row = db.execute('SELECT id,username,is_active,is_staff FROM auth_user WHERE id=?', (int(user_id),)).fetchone()
    if row != (int(user_id), username, 1, 1):
        raise Refused('existing active staff account mapping does not match')


def preflight(access, value):
    fields(value, {'appId','deviceId','profile','url','displayName','dataBindings'})
    app, device = value['appId'], identifier(value['deviceId'])
    if app not in ROLES:
        raise Refused('unsupported owner application')
    display = value['displayName']
    if not isinstance(display, str) or not 1 <= len(display) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in display):
        raise Refused('bounded local display name required')
    local = protected_state.describe(value['profile'])
    profile = str(Path(value['profile']).resolve(strict=True))
    bindings = value['dataBindings']
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= 3:
        raise Refused('explicit local data bindings required')
    approved, normalized = [], []
    for binding in bindings:
        fields(binding, {'districtId','dataRoot','localUserId','localUsername'})
        data_hash = protected_state.path_hash(binding['dataRoot'])
        if app == 'terrier':
            approved.append(binding['districtId'])
            _account(binding['dataRoot'], binding['localUserId'], binding['localUsername'])
        elif len(bindings) != 1 or any(binding[key] is not None for key in ('districtId','localUserId','localUsername')):
            raise Refused('unexpected local account or district binding')
        normalized.append({'districtId':binding['districtId'], 'dataRootHash':data_hash,
                           'localUserId':binding['localUserId'], 'localUsername':binding['localUsername']})
    scope(app, ROLES[app], approved)
    descriptor = access.descriptor(value['url'])
    identity = {'appId':app,'deviceId':device,'platform':'win32','arch':'x64'}
    config = {'schemaVersion':1, 'profileHash':local['profileHash'], 'windowsSid':local['windowsSid'],
              'identity':identity, 'trustHash':trust_hash(descriptor), 'displayName':display, 'dataBindings':normalized}
    records = {kind: protected_state.read(profile, kind) for kind in ('identity','trust','state','owner')}
    credential = native_credentials.read(app, device, 'access-token')
    return profile, config, descriptor, records, credential


def _matches(existing, expected):
    return existing is None or canonical(existing) == canonical(expected)


def provision(access, value, *, checkpoint=lambda _stage: None):
    # All canonical directories and read-only existing-account checks happen
    # before creating a grant, receipt, credential or profile record.
    profile, config, descriptor, records, credential = preflight(access, value)
    identity, profile_hash = config['identity'], config['profileHash']
    fingerprint = digest(canonical(config))
    with access.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM owner_provisioning WHERE profile_hash=?', (profile_hash,)).fetchone()
        if existing:
            if existing['config_hash'] != fingerprint:
                raise Refused('owner provisioning configuration conflict')
            receipt = json.loads(protect(bytes(existing['receipt']), decrypt=True))
            current = db.execute('SELECT revoked FROM grants WHERE grant_id=?', (existing['grant_id'],)).fetchone()
            if not current or current['revoked']:
                raise Refused('owner grant was revoked; explicit new approval is required')
        else:
            if any(record is not None for record in records.values()) or credential is not None:
                raise Refused('existing access enrollment will not be replaced')
            if db.execute('SELECT 1 FROM principals WHERE app=? AND device_id=?', (identity['appId'],identity['deviceId'])).fetchone():
                raise Refused('existing server principal will not be reassigned')
            request_id = str(uuid.uuid4())
            now = access.now()
            districts = [b['districtId'] for b in config['dataBindings']] if identity['appId']=='terrier' else []
            db.execute('INSERT INTO requests(request_id,app,device_id,platform,arch,display_name,secret_hash,source_hash,state,created,expires,role,district_ids,key_hash,key_expires) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id,identity['appId'],identity['deviceId'],'win32','x64',config['displayName'],secret_hash(secrets.token_urlsafe(32)),digest(b'explicit-local-owner'),'approved',now,now+600,ROLES[identity['appId']],canonical(districts).decode(),secret_hash(secrets.token_urlsafe(32)),now+600))
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            activation = access._issue_grant(db, row)
            grant = _payload(activation['grant'])
            owner = {'schemaVersion':1,'purpose':'melange-desktop-owner',
                     **{key:grant[key] for key in ('serverId','appId','deviceId','grantId','principalId','accountIdentity')},
                     'grantSequence':grant['sequence'],'issuedAt':now,'profileHash':profile_hash,
                     'windowsSid':config['windowsSid'],'dataBindings':config['dataBindings']}
            expected = {'identity':{'schemaVersion':1,**identity}, 'trust':descriptor,
                        'state':{'schemaVersion':1,'identity':identity,'trustHash':config['trustHash'],'pending':None,
                                 'grantEnvelope':activation['grant'],'statusEnvelope':None,'highestSequence':grant['sequence'],'revoked':False},
                        'owner':{'schemaVersion':1,'attestation':access.sign(owner)}}
            token = base64.b64encode(canonical({'tokens':{grant['grantId']:activation['accessToken']}})).decode('ascii')
            receipt = {'schemaVersion':1,'records':expected,'credential':token,'grantId':grant['grantId'],'profileHash':profile_hash}
            db.execute('INSERT INTO owner_provisioning(profile_hash,config_hash,grant_id,receipt) VALUES (?,?,?,?)',
                       (profile_hash,fingerprint,grant['grantId'],protect(canonical(receipt))))
            access.audit(db, 'owner-provisioning-issued', request_id, grant['grantId'])
            checkpoint('before-server-commit')
    checkpoint('server-committed')
    # Never overwrite an unrelated or subsequently modified profile. Check every
    # record before writing any; a retry after activation preserves live status.
    expected = receipt['records']
    if not all(_matches(records[kind], expected[kind]) for kind in expected) or credential not in (None, receipt['credential']):
        raise Refused('protected access state changed; local review required')
    for kind in ('identity','trust','state'):
        if protected_state.read(profile, kind) is None:
            protected_state.write(profile, kind, expected[kind])
        elif canonical(protected_state.read(profile, kind)) != canonical(expected[kind]):
            raise Refused('protected access state changed during provisioning')
        checkpoint(kind)
    current_credential = native_credentials.read(identity['appId'], identity['deviceId'], 'access-token')
    if current_credential is None:
        native_credentials.write(identity['appId'], identity['deviceId'], receipt['credential'], 'access-token')
    elif current_credential != receipt['credential']:
        raise Refused('application credential changed during provisioning')
    checkpoint('credential')
    # Marker last: a partial sequence never grants an automatic owner bypass.
    if protected_state.read(profile, 'owner') is None:
        protected_state.write(profile, 'owner', expected['owner'])
    elif canonical(protected_state.read(profile, 'owner')) != canonical(expected['owner']):
        raise Refused('owner attestation changed during provisioning')
    checkpoint('owner')
    with access.connect() as db:
        db.execute('UPDATE owner_provisioning SET completed=1 WHERE profile_hash=?', (profile_hash,))
    return {'provisioned':True,'appId':identity['appId'],'deviceId':identity['deviceId'],
            'grantId':receipt['grantId'],'profileHash':profile_hash,'dataBindings':len(config['dataBindings'])}
