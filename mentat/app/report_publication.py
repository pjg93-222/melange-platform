"""Committed report generations. Readers refuse intermediate file replacements.

A report with neither intent nor manifest remains explicitly legacy. Once a
publisher writes intent, absence/corruption cannot silently restore legacy mode.
The output lease spans every replacement and the last manifest commit.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from report_identity import identity, companion_name
import proclock

VERSION = 1
MESSAGE = 'Report publication is incomplete or changed. Retry after its Overview and Data generation is committed.'


def _stamp(path):
    path = Path(path)
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    except FileNotFoundError:
        return (str(path.absolute()), None)


@lru_cache(maxsize=256)
def _hash(stamp):
    with Path(stamp[0]).open('rb') as stream:
        result = hashlib.file_digest(stream, 'sha256').hexdigest()
    if _stamp(stamp[0]) != stamp:
        raise ValueError(MESSAGE)
    return result


def _json(path):
    if Path(path).stat().st_size > 65536:
        raise ValueError(MESSAGE)
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(MESSAGE)
    return value


def locations(workbook):
    """Fixed names only; no manifest-controlled paths enter the reader."""
    workbook = Path(workbook)
    info = identity(workbook.name)
    if info is None:
        return None
    suffix = ' ' + info['cycle_label'] if info['cycle_label'] else ''
    overview = workbook.with_name(info['industry'] + ' Industry Spending Overview' + suffix + '.xlsx')
    return {'overview': overview, 'data': overview.with_name(companion_name(info)),
            'manifest': overview.with_suffix('.publication.json'),
            'intent': overview.with_suffix('.publication.pending.json'),
            'required': overview.with_suffix('.publication.required.json'),
            'lock': overview.with_suffix('.publication.lock'),
            'audit': overview.with_suffix('.audit') / 'audit-manifest.json'}


def dependencies(workbook):
    value = locations(workbook)
    return ([value[name] for name in ('overview', 'data', 'manifest', 'intent', 'required', 'audit')]
            if value is not None else [Path(workbook)])


def _contained(path, directory):
    if not Path(path).resolve().is_relative_to(directory.resolve()):
        raise ValueError(MESSAGE)


def capture(workbook):
    """Validate one complete generation, including on every cache hit."""
    paths = locations(workbook)
    if paths is None:
        return {'state': 'legacy', 'generation': None}
    directory = paths['overview'].parent
    try:
        for name in ('overview', 'data', 'manifest', 'intent', 'required', 'audit'):
            _contained(paths[name], directory)
        before = tuple(_stamp(path) for path in dependencies(workbook))
        has_manifest, has_intent = paths['manifest'].is_file(), paths['intent'].is_file()
        required = paths['required'].is_file()
        if not has_manifest and not has_intent and not required:
            return {'state': 'legacy', 'generation': None, 'identity': hashlib.sha256(repr(before).encode()).hexdigest()}
        if (not has_manifest or not required
                or _json(paths['required']) != {'schema_version': VERSION, 'requires_commit': True}):
            raise ValueError(MESSAGE)
        manifest = _json(paths['manifest'])
        generation = manifest.get('generation')
        if (manifest.get('schema_version') != VERSION or manifest.get('state') != 'committed'
                or not isinstance(generation, str) or not re.fullmatch(r'[a-f0-9]{32}', generation)):
            raise ValueError(MESSAGE)
        if has_intent:
            intent = _json(paths['intent'])
            if intent.get('schema_version') != VERSION or intent.get('generation') != generation:
                raise ValueError(MESSAGE)
        for kind in ('overview', 'data'):
            item = manifest.get(kind)
            if kind == 'data' and item is None:
                # An overview-only generation must not silently expose an old companion.
                if Path(workbook).name == paths['data'].name:
                    raise ValueError(MESSAGE)
                continue
            if (not isinstance(item, dict) or set(item) != {'name', 'sha256', 'bytes'}
                    or item['name'] != paths[kind].name or type(item['bytes']) is not int or item['bytes'] < 0
                    or not re.fullmatch(r'[a-f0-9]{64}', str(item['sha256']))):
                raise ValueError(MESSAGE)
            stamp = _stamp(paths[kind])
            if stamp[1] != item['bytes'] or _hash(stamp) != item['sha256']:
                raise ValueError(MESSAGE)
        audit = manifest.get('audit_manifest_sha256')
        if audit is not None and (not re.fullmatch(r'[a-f0-9]{64}', str(audit))
                                  or _hash(_stamp(paths['audit'])) != audit):
            raise ValueError(MESSAGE)
        if tuple(_stamp(path) for path in dependencies(workbook)) != before:
            raise ValueError(MESSAGE)
        return {'state': 'committed', 'generation': generation,
                'data_published': manifest.get('data') is not None,
                'manifest_sha256': _hash(_stamp(paths['manifest'])),
                'identity': hashlib.sha256(repr(before).encode()).hexdigest()}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(MESSAGE) from error


def read(paths, build):
    """Keep a cache hit or an actual read inside one validated reader boundary."""
    paths = tuple(map(Path, paths))
    before = tuple(capture(path) for path in paths)
    value = build()
    if tuple(capture(path) for path in paths) != before:
        raise ValueError(MESSAGE)
    return value


def _atomic_json(path, value):
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    try:
        with pending.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


@contextmanager
def _lease(path, generation, wait_seconds):
    deadline = time.monotonic() + max(0, wait_seconds)
    body = None
    while body is None:
        body = proclock.claim(path, key=generation, extra={'publication_generation': generation})
        if body is None:
            if time.monotonic() >= deadline:
                raise ValueError('Another publisher owns this report output. Retry after it finishes.')
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
    try:
        yield json.loads(body)
    finally:
        proclock.release(path, body)


def publish(overview, data, directory, audit_directory=None, *, wait_seconds=30, checkpoint=None):
    """Publish staged files under one output lease; commit the manifest last.

    checkpoint is an injectable test observer, not a bypass: exceptions/death
    leave intent and readers keep refusing until a later owned retry commits.
    """
    overview = Path(overview).resolve()
    directory = Path(directory).resolve()
    info = identity(overview.name)
    if not directory.is_dir() or not info or info['kind'] != 'overview':
        raise ValueError('Choose an existing report directory and a staged Overview workbook.')
    paths = locations(directory / overview.name)
    staged = {'overview': overview, 'data': Path(data).resolve() if data is not None else None}
    if staged['data'] is not None and staged['data'].name != paths['data'].name:
        raise ValueError('The staged Data workbook must match this industry and cycle.')
    expected = {}
    for kind, path in staged.items():
        if path is None:
            expected[kind] = None
            continue
        if not path.is_file() or path == paths[kind].resolve():
            raise ValueError('Publication needs distinct staged workbook files.')
        stamp = _stamp(path)
        expected[kind] = {'name': path.name, 'sha256': _hash(stamp), 'bytes': stamp[1]}
    audit_files = []
    if audit_directory is not None:
        audit_directory = Path(audit_directory).resolve()
        if audit_directory.name != paths['audit'].parent.name or audit_directory.parent != overview.parent:
            raise ValueError('The staged audit directory must belong to the staged Overview.')
        audit_manifest = _json(audit_directory / 'audit-manifest.json')
        if (audit_manifest.get('workbook_name') != overview.name
                or audit_manifest.get('workbook_sha256') != expected['overview']['sha256']
                or not isinstance(audit_manifest.get('csv_sha256'), dict)):
            raise ValueError('The staged audit manifest does not match this Overview.')
        for name, digest in audit_manifest['csv_sha256'].items():
            if (not isinstance(name, str) or Path(name).name != name or not name.endswith('.csv')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(digest))):
                raise ValueError('The staged audit manifest contains an invalid CSV identity.')
            source = audit_directory / name
            _contained(source, audit_directory)
            if _hash(_stamp(source)) != digest:
                raise ValueError('A staged audit CSV differs from its recorded fingerprint.')
            audit_files.append((source, digest))
        audit_files.append((audit_directory / 'audit-manifest.json', _hash(_stamp(audit_directory / 'audit-manifest.json'))))
    generation = uuid.uuid4().hex
    checkpoint = checkpoint or (lambda phase, manifest: None)
    manifest = {'schema_version': VERSION, 'state': 'committed', 'generation': generation,
                'published_at': datetime.now(timezone.utc).isoformat(), **expected,
                'audit_manifest_sha256': audit_files[-1][1] if audit_files else None}
    with _lease(paths['lock'], generation, wait_seconds) as owner:
        for name in ('overview', 'data', 'manifest', 'intent', 'required', 'audit'):
            _contained(paths[name], directory)
        requirement = {'schema_version': VERSION, 'requires_commit': True}
        if paths['required'].is_file() and _json(paths['required']) != requirement:
            raise ValueError('Report generation requirement marker is invalid; reconcile it before publishing.')
        # Permanent anti-downgrade marker. Losing the committed manifest can
        # never make a previously managed report become an accepted legacy pair.
        _atomic_json(paths['required'], requirement)
        previous = _json(paths['intent']) if paths['intent'].is_file() else None
        intent = {'schema_version': VERSION, 'generation': generation, 'owner': owner,
                  'replaces_incomplete_generation': previous.get('generation') if previous else None}
        if previous is not None:
            history = paths['intent'].with_name(paths['intent'].name + '.recovered-' + generation + '.json')
            _atomic_json(history, {'previous_intent': previous, 'new_intent': intent,
                                   'recovery_basis': 'exclusive output lease acquired after previous owner released or died'})
        _atomic_json(paths['intent'], intent)
        checkpoint('intent', manifest)
        for source, digest in audit_files:
            target = paths['audit'].parent / source.name
            _contained(target, directory)
            target.parent.mkdir(exist_ok=True)
            _copy_verified(source, target, digest, generation)
        for kind in ('overview', 'data'):
            if staged[kind] is not None:
                _copy_verified(staged[kind], paths[kind], expected[kind]['sha256'], generation)
            checkpoint(kind, manifest)
        for kind in ('overview', 'data'):
            if expected[kind] is not None and _hash(_stamp(paths[kind])) != expected[kind]['sha256']:
                raise ValueError('Published workbook changed before its generation could commit.')
        if audit_files and _hash(_stamp(paths['audit'])) != manifest['audit_manifest_sha256']:
            raise ValueError('Published audit manifest changed before generation commit.')
        if _json(paths['intent']).get('generation') != generation:
            raise ValueError('Publication intent no longer belongs to this output owner.')
        _atomic_json(paths['manifest'], manifest)
        checkpoint('committed', manifest)
        # Exact generation ownership, checked while this process owns output lease.
        if _json(paths['intent']).get('generation') == generation:
            paths['intent'].unlink()
        capture(paths['overview'])
    return manifest


def _copy_verified(source, target, expected, generation):
    pending = target.with_name(target.name + '.pending-' + generation)
    try:
        shutil.copyfile(source, pending)
        if _hash(_stamp(pending)) != expected:
            raise ValueError('Staged publication artifact changed while being copied.')
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)
