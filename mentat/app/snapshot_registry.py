"""The published industry snapshots this console can address, as one registry.

DECISION: industry is a request parameter, never a console global. One console
serves every published snapshot; every industry-scoped route takes the label (or
its slug) on the request and echoes the label back with the snapshot's stamp.

build_stamp = sha256("\\n".join([workbook_sha256, companion_sha256 or "",
                                 database_sha256 or "", payload_sha256 or ""]))

Deterministic, recomputable by anyone holding those four hashes, changes when
any one of them changes, and independent of the console's own code stamp.

Every fact here comes from contribution_data._sources(): dataset ids,
availability, hydration and the lineage check stay that reader's single source
of truth. This module adds no second glob and no second hash. No entry ever
carries an absolute path: workbook and companion are names inside the report
directory, and database.path is relative to the project root.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

import contribution_data
from report_identity import identity

SCHEMA_VERSION = 1
PROJECT = Path(__file__).resolve().parents[1]
WORKBOOK_SUFFIX = ' Industry Spending Overview.xlsx'
COMPANION_KEYS = ('name', 'sha256', 'bytes', 'modified_at', 'lineage_matches')


def slug(label):
    """The console's one slug rule, reused - never a second spelling.

    contribution_data._slug names the .cache/industry-report/<slug> directory,
    so a second rule here would silently address a different folder.
    """
    return contribution_data._slug(label)


def build_stamp(workbook_sha256, companion_sha256, database_sha256, payload_sha256):
    parts = [workbook_sha256, companion_sha256 or '', database_sha256 or '', payload_sha256 or '']
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()


def _moment(mtime):
    """A file mtime as UTC ISO 8601 to the second, the form the contract shows."""
    return datetime.fromtimestamp(mtime, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _file(path, digest, extra=None):
    """Name, hash and on-disk facts for one published workbook.

    A file that vanished between the scan and this call reports its size and
    time as None rather than raising. The exception would otherwise reach the
    route's generic handler, where str(FileNotFoundError) carries the absolute
    path into the JSON error body - the one thing this reader never publishes.
    """
    path = Path(path)
    try:
        stat = path.stat()
        measured = {'bytes': stat.st_size, 'modified_at': _moment(stat.st_mtime)}
    except OSError:
        measured = {'bytes': None, 'modified_at': None}
    value = {'name': path.name, 'sha256': digest, **measured}
    if extra:
        value.update(extra)
    return value


def _database(source):
    database = source.get('database')
    if not database:
        return None
    database = Path(database)
    outside = False
    try:
        path = database.resolve().relative_to(PROJECT).as_posix()
    except ValueError:
        # A database outside the project keeps its name only. Publishing the
        # absolute path would leak this machine's layout for no gain.
        path, outside = database.name, True
    value = {'path': path, 'sha256': source.get('database_sha256'),
             'hydrated': bool(source.get('hydration_manifest'))}
    if outside:
        value['outside_project'] = True
    return value


def entry(source):
    """One registry entry from one contribution_data._sources() item."""
    companion = source.get('data_workbook')
    workbook = _file(source['workbook'], source['workbook_sha256'])
    companion_entry = (_file(companion, source.get('data_workbook_sha256'),
                             {'lineage_matches': source.get('data_lineage_matches')})
                       if companion else None)
    database = _database(source)
    payload_sha256 = source.get('payload_sha256')
    industry = source.get('snapshot_label', source['industry'])
    # A workbook that could not be hashed and one that is no longer on disk
    # are the same fact to a reader: this entry cannot be read. Both report
    # it the same way, and a hash measured before the file went away is
    # dropped - it describes bytes nobody can produce any more.
    unreadable = workbook['sha256'] is None or workbook['modified_at'] is None
    if unreadable:
        workbook['sha256'] = None
    return {'industry': industry, 'slug': slug(industry), 'dataset': source['id'],
            'available': False if unreadable else source.get('available'),
            'message': ('Report workbook is unreadable.' if unreadable
                        else source.get('message')),
            'workbook': workbook, 'companion': companion_entry, 'database': database,
            'payload_sha256': payload_sha256,
            'destination_schema': source.get('destination_schema'),
            # A workbook this process could not open has no hash, and a stamp
            # built from the three remaining fields would be the same string
            # for every unreadable book. There is no identity to report, so
            # none is reported: null, beside available False and its message.
            'build_stamp': (build_stamp(workbook['sha256'],
                                        companion_entry['sha256'] if companion_entry else None,
                                        database['sha256'] if database else None,
                                        payload_sha256) if workbook['sha256'] else None)}


def registry(report_directory, sources=None):
    """Every published snapshot in one envelope, sorted by label.

    The producer validates its own output before returning it: a registry that
    cannot pass validate() is a defect here, not something a reader has to
    discover.
    """
    if sources is None:
        sources = contribution_data._sources(report_directory)
    snapshots = sorted((entry(source) for source in sources),
                       key=lambda item: item['industry'])
    value = {'schema_version': SCHEMA_VERSION, 'report_directory': str(report_directory),
             'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'snapshots': snapshots}
    validate(value)
    return value


def find(report_directory_or_registry, value):
    """The entry whose label or slug is exactly `value`, or None.

    Matching is exact and case-sensitive: 'credit_union' is the slug of
    'Credit Union', and the R collector's 'credit-unions' is neither.
    """
    payload = (report_directory_or_registry
               if isinstance(report_directory_or_registry, dict)
               and 'snapshots' in report_directory_or_registry
               else registry(report_directory_or_registry))
    if not isinstance(value, str) or not value:
        return None
    snapshots = payload['snapshots']
    # A label is unique: it is a published file name. A slug is not guaranteed
    # to be - an Excel lock file (~$<name>.xlsx) reaches _sources() and slugs to
    # the same string as the book it locks - so a label match wins outright, and
    # a slug match prefers the snapshot that can actually be read.
    return (next((item for item in snapshots if item['industry'] == value), None)
            or next((item for item in snapshots if item['slug'] == value and item['available']), None)
            or next((item for item in snapshots if item['slug'] == value), None))


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and all(
        character in '0123456789abcdef' for character in value)


def validate_entry(entry_value):
    """Raise ValueError, in plain English, for any entry a reader must not trust."""
    if not isinstance(entry_value, dict):
        raise ValueError('A snapshot entry must be an object.')
    industry = entry_value.get('industry')
    if not isinstance(industry, str) or not industry:
        raise ValueError('A snapshot entry needs a published industry label.')
    workbook = entry_value.get('workbook')
    if not isinstance(workbook, dict):
        raise ValueError('A snapshot entry needs its workbook details.')
    name = workbook.get('name')
    if not isinstance(name, str) or not name:
        raise ValueError('A snapshot workbook needs its published file name.')
    if any(character in name for character in ('/', '\\', ':')) or Path(name).is_absolute():
        raise ValueError('A snapshot workbook is recorded by name only, never by path.')
    info = identity(name)
    if info is None or info['kind'] != 'overview' or industry != info['label']:
        raise ValueError('A snapshot industry must be the label its workbook name carries.')
    if entry_value.get('slug') != slug(industry):
        raise ValueError('A snapshot slug must be the console slug of its industry label.')
    if not isinstance(entry_value.get('available'), bool):
        raise ValueError('A snapshot entry must say whether it is available.')
    # An unavailable entry whose workbook could not be opened has no hash and
    # therefore no build stamp. Every other entry must carry both: an
    # AVAILABLE entry missing either one is the defect this rule exists for.
    unreadable = entry_value['available'] is False and workbook.get('sha256') is None
    if not unreadable and not _hex64(workbook.get('sha256')):
        raise ValueError('A snapshot workbook needs a 64-character SHA-256.')
    # Size and time are measured from the file, so an entry that could not be
    # read may report neither. An AVAILABLE entry must still carry both.
    if not (unreadable and workbook.get('bytes') is None):
        if not isinstance(workbook.get('bytes'), int) or isinstance(workbook.get('bytes'), bool):
            raise ValueError('A snapshot workbook needs its size in bytes.')
    if not (unreadable and workbook.get('modified_at') is None):
        if not isinstance(workbook.get('modified_at'), str) or not workbook.get('modified_at'):
            raise ValueError('A snapshot workbook needs its modification time.')
    companion = entry_value.get('companion')
    if companion is not None:
        if not isinstance(companion, dict) or any(key not in companion for key in COMPANION_KEYS):
            raise ValueError('A companion workbook is recorded as an object with its own name, '
                             'SHA-256, size, modification time and lineage result.')
    database = entry_value.get('database')
    if database is not None:
        if not isinstance(database, dict):
            raise ValueError('A snapshot database is recorded as an object.')
        path = database.get('path')
        if not isinstance(path, str) or not path:
            raise ValueError('A snapshot database needs its path.')
        if Path(path).is_absolute() or path.startswith(('/', '\\')) or ':' in path:
            raise ValueError('A snapshot database path is relative to the project, never absolute.')
        if not _hex64(database.get('sha256')):
            raise ValueError('A snapshot database needs a 64-character SHA-256.')
        if not isinstance(database.get('hydrated'), bool):
            raise ValueError('A snapshot database must say whether it was hydrated from a receipt.')
    if not (unreadable and entry_value.get('build_stamp') is None) and not _hex64(entry_value.get('build_stamp')):
        raise ValueError('A snapshot entry needs a 64-character build stamp.')
    return entry_value


def validate(payload):
    """Validate the envelope and every entry in it."""
    if not isinstance(payload, dict):
        raise ValueError('A snapshot registry must be an object.')
    if payload.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('This snapshot registry was written by a different schema version.')
    if not isinstance(payload.get('report_directory'), str) or not payload.get('report_directory'):
        raise ValueError('A snapshot registry names the report directory it read.')
    if not isinstance(payload.get('generated_at'), str) or not payload.get('generated_at'):
        raise ValueError('A snapshot registry records when it was generated.')
    snapshots = payload.get('snapshots')
    if not isinstance(snapshots, list):
        raise ValueError('A snapshot registry carries a list of snapshots.')
    for item in snapshots:
        validate_entry(item)
    return payload
