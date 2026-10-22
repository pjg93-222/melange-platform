"""Private district data packs for approved Terrier desktops. Local administrator commands; recipients only read.

pack-build copies an explicit allowlist of one district's Terrier stores (never accounts, secrets, salts, logs, audit,
backups, migration or snapshot folders) into a new directory outside OneDrive and every git worktree, plus a
deterministic bundle.zip of the active summary snapshot. pack-publish gzips each file into <packs>/objects/<sha256> and
signs one terrier.district-data.v1 revision per changed district into <packs>/revisions/<catalogSequence>.json, where
<packs> is %LOCALAPPDATA%\\MelangeDesktopAdmin\\packs unless --packs names another directory.
"""
from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time
import uuid
import zipfile

from desktop_admin import Refused, atomic_write, canonical, protect_directory
from access_store import event_scope

REQUIRED = ('contacts.sqlite3', 'roads.sqlite3', 'scores.sqlite3', 'street.sqlite3', 'walk.sqlite3', 'density.json')
OPTIONAL = ('credit-quality.json',)
BUNDLE = ('bundle.zip', 'bundle.zip.sha256')
FORBIDDEN = {'accounts.sqlite3', '.secret', '.street-salt', '.walk-salt', '.desktop-password-lock-disabled', '.demo-key'}
FORBIDDEN_PREFIXES = ('migration-', 'audit', 'logs', 'backups', 'snapshots')
# Terrier server/research/bundles.py:17-20 FILES_FOR: import_bundle accepts exactly these names per schema.
_SUMMARY = ('district.geojson', 'geometry.json', 'manifest.json', 'rows.json', 'streets.geojson')
FILES_FOR = {'cu-summary-v1': _SUMMARY, 'cu-summary-v2': _SUMMARY, 'cu-summary-v3': _SUMMARY,
             'cu-summary-v4': tuple(sorted(_SUMMARY + ('adjacency.json', 'area_indices.json')))}
SNAPSHOT_PREFIX = {'co-05': 'co-cd005-', 'or-05': 'or-cd005-', 'ct-01': 'ct-cd001-'}  # tools/districts.py snapshot_prefix
# Mirrors Terrier tools/districts.py DISTRICTS[id]['sha256'] (cu-geography-v3 @ a0238c3, another repository, so not imported):
# the voter file each district's stores are built from, which check_data_dir compares with street.sqlite3 metadata.source_sha256.
SOURCE_SHA256 = {'co-05': '4c48800b8c21870e6e2b6242810bbbe917d8169e529b440efb662f71c464239f',
                 'or-05': 'd75d13ccfea891eb252b7609017e338a70263ddee5a27ced5dda17abda91a598',
                 'ct-01': '6e6c7822c44bf04712cf67c25721e6a180281a603af41721df32c9ab7074129a'}
KIND = 'terrier.district-data.v1'
REVISION_FILE = re.compile(r'[1-9][0-9]{0,15}\.json')
log = logging.getLogger(__name__)
_REVISIONS = {}  # revisions folder -> ((mtime_ns, names), parsed revisions)


def default_packs():
    """Outside the Documents-based state root, which Known Folder Move can put in OneDrive."""
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'MelangeDesktopAdmin' / 'packs'


def refuse_synced(path):
    """Packs hold voter data: never inside OneDrive (synced off this machine) or a git worktree (committable)."""
    path = Path(path).resolve()
    synced = [Path(os.environ[name]).resolve() for name in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial') if os.environ.get(name)]
    for folder in (path, *path.parents):
        if folder.name.lower().startswith('onedrive') or folder in synced or (folder / '.git').exists():
            raise Refused('pack output must be outside OneDrive and every git worktree')
    return path


def read_only(path):
    return sqlite3.connect(Path(path).resolve(strict=True).as_uri() + '?mode=ro', uri=True)


def check_source(district, folder):
    """Terrier check_data_dir, plus contacts (contact_views holds it to street's source): stores built from another
    district's voter file are refused, so a wrong --data never becomes this district's pack."""
    for name in ('street.sqlite3', 'contacts.sqlite3'):
        try:
            with contextlib.closing(read_only(Path(folder) / name)) as db:
                row = db.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()
            same = row is not None and json.loads(row[0]) == SOURCE_SHA256[district]
        except (sqlite3.Error, ValueError):
            same = False
        if not same:
            raise Refused(name + ' was not built from the ' + district + ' voter file')


def copy_store(source, target):
    """Backup-API copy of a read-only source, converted to journal_mode DELETE and verified (container_start.wal_stores)."""
    with contextlib.closing(read_only(source)) as origin, contextlib.closing(sqlite3.connect(target)) as copy:
        origin.backup(copy)
        mode = copy.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
        check = copy.execute('PRAGMA quick_check').fetchone()[0]
    with open(target, 'rb') as stream:
        header = stream.read(20)
    if mode != 'delete' or check != 'ok' or header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01':
        raise Refused(target.name + ' copy is not a verified rollback-journal store')


def active_snapshot(terrier_state, district):
    """The active summary key, read from accounts.sqlite3 opened read-only; the accounts store itself is never copied."""
    with contextlib.closing(read_only(Path(terrier_state) / 'accounts.sqlite3')) as db:
        rows = db.execute('SELECT key FROM research_snapshot WHERE active=1').fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str) or not re.fullmatch(re.escape(SNAPSHOT_PREFIX[district]) + '[a-f0-9]{12}', rows[0][0]):
        raise Refused('exactly one active summary snapshot of this district is required')
    return rows[0][0]


def write_bundle(snapshot, target):
    """Exactly FILES_FOR[schema] in a fixed order with fixed metadata: the same snapshot gives the same bytes."""
    schema = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8')).get('schema')
    if schema not in FILES_FOR:
        raise Refused('unsupported summary snapshot schema')
    with zipfile.ZipFile(target, 'x') as archive:  # stored: every pack object is gzipped anyway
        for name in FILES_FOR[schema]:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system, info.external_attr = 3, 0o644 << 16  # fixed, not the build host's
            with (snapshot / name).open('rb') as source, archive.open(info, 'w') as stream:
                shutil.copyfileobj(source, stream, 1 << 20)
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    with target.with_name('bundle.zip.sha256').open('x', encoding='ascii') as stream:
        stream.write(digest + '\n')


def build(district, data, out, terrier_state=None):
    if district not in SNAPSHOT_PREFIX:
        raise Refused('unknown district')
    data = Path(data).resolve(strict=True)
    state = Path(terrier_state).resolve(strict=True) if terrier_state else data
    out = refuse_synced(out)
    missing = [name for name in REQUIRED if not (data / name).is_file()]
    if missing:
        raise Refused('district data is missing ' + ', '.join(missing))
    if out.exists() and any(out.iterdir()):
        raise Refused('pack output directory must be new or empty')
    check_source(district, data)  # before anything is copied
    names = REQUIRED + tuple(name for name in OPTIONAL if (data / name).is_file())
    out.mkdir(parents=True, exist_ok=True)
    try:
        protect_directory(out)  # plaintext voter data: owner and SYSTEM only, before the first copy
        for name in names:
            if name.endswith('.sqlite3'):
                copy_store(data / name, out / name)
            else:
                shutil.copyfile(data / name, out / name)
        write_bundle(state / 'snapshots' / active_snapshot(state, district), out / 'bundle.zip')
        produced = set(os.listdir(out))
        if produced != set(names + BUNDLE) or any(n in FORBIDDEN or n.startswith(FORBIDDEN_PREFIXES) for n in produced):
            raise Refused('pack output differs from the allowlist')
    except sqlite3.Error as error:
        shutil.rmtree(out, ignore_errors=True)
        raise Refused('district store could not be copied: ' + str(error)) from error
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)  # never leave a partial voter-data copy behind
        raise
    return sorted(produced)


def store_object(source, objects):
    """gzip level 6 with a fixed header (no name, mtime 0), named by the sha256 of the gzip bytes; rawSize is the bytes gzipped."""
    temporary = objects / ('stage-' + secrets.token_hex(8) + '.tmp')
    try:
        with source.open('rb') as origin, temporary.open('xb') as target:
            with gzip.GzipFile(filename='', mode='wb', compresslevel=6, fileobj=target, mtime=0) as stream:
                shutil.copyfileobj(origin, stream, 1 << 20)
            raw_size = origin.tell()
            target.flush()
            os.fsync(target.fileno())
        with temporary.open('rb') as stream:
            sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        size = temporary.stat().st_size
        if not (objects / sha256).exists():  # content-addressed; an existing object may be streaming right now
            os.replace(temporary, objects / sha256)
        return {'sha256': sha256, 'size': size, 'rawSize': raw_size}
    finally:
        temporary.unlink(missing_ok=True)


def published(packs):
    """[(payload, envelope)] by catalogSequence, parsed again only when the revisions folder changes. A corrupt revision file
    is logged and skipped, never fatal to every route. Callers must not mutate the shared result."""
    folder = Path(packs) / 'revisions'
    try:
        key = (folder.stat().st_mtime_ns, tuple(sorted(os.listdir(folder))))
    except FileNotFoundError:
        return []
    cached = _REVISIONS.get(folder)
    if cached and cached[0] == key:
        return cached[1]
    result = []
    for name in key[1]:
        if REVISION_FILE.fullmatch(name):
            try:
                envelope = json.loads((folder / name).read_bytes())
                payload = json.loads(base64.b64decode(envelope['payload'], validate=True))
                if name != str(payload['catalogSequence']) + '.json':
                    raise ValueError('revision file does not match its catalog sequence')
                result.append((payload, envelope))
            except (OSError, ValueError, KeyError, TypeError) as error:
                log.warning('skipped corrupt district pack revision %s: %s', folder / name, error)
    result = sorted(result, key=lambda item: item[0]['catalogSequence'])
    _REVISIONS[folder] = (key, result)
    return result


def publish(access, district, build_dir, clock=time.time, keep_build=False, packs=None):
    """Sign one revision when the district's objects changed; None when they did not. Once the objects are stored in
    the protected pack store the plaintext build is deleted, unless keep_build; a refused publish keeps it for a retry."""
    envelope = _publish(access, district, build_dir, clock, packs)
    if not keep_build:
        shutil.rmtree(build_dir)
    return envelope


def _publish(access, district, build_dir, clock, packs):
    if district not in SNAPSHOT_PREFIX:
        raise Refused('unknown district')
    build_dir = Path(build_dir).resolve(strict=True)
    names = sorted(os.listdir(build_dir))
    expected = set(REQUIRED + BUNDLE)
    if not expected <= set(names) <= expected | set(OPTIONAL):
        raise Refused('build is not a complete district pack')
    with zipfile.ZipFile(build_dir / 'bundle.zip') as archive:  # a co-05 build must never reach or-05 recipients
        if not json.loads(archive.read('manifest.json')).get('snapshot', '').startswith(SNAPSHOT_PREFIX[district]):
            raise Refused('build belongs to another district')
    check_source(district, build_dir)  # the stores that are signed, not only the --data they were copied from
    packs = refuse_synced(packs or default_packs())
    packs.mkdir(parents=True, exist_ok=True)
    protect_directory(packs)
    (packs / 'objects').mkdir(exist_ok=True)
    (packs / 'revisions').mkdir(exist_ok=True)
    objects = [{'role': name, **store_object(build_dir / name, packs / 'objects')} for name in names]
    revisions = published(packs)
    head = next((payload for payload, _ in reversed(revisions) if payload['districtId'] == district), None)
    if head and head['objects'] == objects:
        return None
    server = access.config()['serverId']
    # Past every revision file, a skipped corrupt one included, so the exclusive create below cannot collide with it.
    sequence = max([0] + [int(name[:-5]) for name in os.listdir(packs / 'revisions') if REVISION_FILE.fullmatch(name)]) + 1
    payload = {'schemaVersion': 1, 'purpose': 'melange-desktop-dataset', 'serverId': server, 'appId': 'terrier',
               'datasetId': str(uuid.uuid5(uuid.UUID(server), district)), 'revisionId': str(uuid.uuid4()),
               'parentRevisionIds': [head['revisionId']] if head else [], 'districtId': district, 'kind': KIND,
               'adapterVersion': 1, 'objects': objects, 'counts': {'files': len(objects)},
               'originPrincipalId': server, 'originDeviceId': server, 'committedAt': int(clock()), 'catalogSequence': sequence}
    envelope = access.sign(payload)
    temporary = packs / 'revisions' / ('stage-' + secrets.token_hex(8) + '.tmp')
    try:
        atomic_write(temporary, canonical(envelope))
        # Exclusive create: a concurrent publish read the same head and sequence, so it must fail, never fork the chain.
        os.link(temporary, packs / 'revisions' / (str(sequence) + '.json'))
    except FileExistsError:
        raise Refused('another pack was published concurrently; publish again') from None
    finally:
        temporary.unlink(missing_ok=True)
    return envelope


def catalog(packs, grant, after, limit, district):
    event_scope(grant, district)
    rows = [(payload, envelope) for payload, envelope in published(packs)
            if payload['appId'] == grant['app'] and payload['districtId'] == district and payload['catalogSequence'] > after]
    page = rows[:limit]
    return {'revisions': [envelope for _, envelope in page], 'nextCursor': page[-1][0]['catalogSequence'] if page else after,
            'hasMore': len(rows) > limit}


def published_object(packs, grant, sha256, revision_id):
    """(path, size) of an object listed in a published revision of a district this approval covers."""
    for payload, _ in published(packs):
        if payload['revisionId'] == revision_id and payload['appId'] == grant['app']:
            event_scope(grant, payload['districtId'])
            for item in payload['objects']:
                if item['sha256'] == sha256:
                    return Path(packs) / 'objects' / sha256, item['size']
    raise Refused('dataset object is not published for this approval')
