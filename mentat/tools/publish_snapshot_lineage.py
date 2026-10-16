"""Register preserved cycle payloads against an unchanged published workbook."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'app'))
import contribution_data as data
from contribution_workbook_guard import cycle_totals
from report_identity import identity, lineage_paths
from report_directory import report_directory, require_report_path
from runtime_paths import resolve_receipt, portable_receipt, report_cache_root


def publish(workbook, payload, project=PROJECT, reports=None):
    workbook = require_report_path(Path(workbook), reports or report_directory(project))
    payload = Path(payload).resolve()
    info = identity(workbook.name)
    if not info or info['kind'] != 'overview' or not info['cycle_label']:
        raise ValueError('Choose a published cycle-specific Overview workbook.')
    metadata = data._payload_metadata(payload)
    expected = {k: data._cents(v) for k, v in metadata.get('expected_cycle_totals', {}).items()}
    if metadata.get('industry') != info['industry'] or set(expected) != {info['cycle_label']}:
        raise ValueError('Payload industry and cycle must match the workbook.')
    if cycle_totals(data._stamp(workbook)) != expected:
        raise ValueError('Payload totals disagree with the published workbook.')
    database = resolve_receipt(metadata['database_path']).resolve()
    database_hash = data._workbook_source(data._stamp(workbook))
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('The preserved database has uncheckpointed writes.')
    if data._hash(database) != database_hash:
        raise ValueError('The preserved database differs from the workbook source fingerprint.')
    for row in data._payload_rows(payload):
        if row.get('cycle_label') != info['cycle_label']:
            raise ValueError('Payload contains a contribution outside the selected cycle.')
    workbook_hash, payload_hash = data._hash(workbook), data._hash(payload)
    cache = (report_cache_root() if os.environ.get('MENTAT_REPORT_CACHE_ROOT') else Path(project) / '.cache/industry-report') / data._slug(info['industry'])
    manifest_path, target = lineage_paths(cache, database_hash, info['cycle_label'])
    cache.mkdir(parents=True, exist_ok=True)
    manifest = {'schema_version': 2, 'database_sha256': database_hash,
                'cycle_label': info['cycle_label'], 'data_sha256': payload_hash,
                'data_bytes': payload.stat().st_size, 'workbook_sha256': workbook_hash,
                'published_workbook': workbook.name, 'source_payload': portable_receipt(payload),
                'publication_basis': 'preserved_payload_verified_against_published_workbook'}
    owner_fields = ('database_sha256', 'cycle_label', 'data_sha256',
                    'workbook_sha256', 'published_workbook')
    # Do not replace another publisher's lineage or reuse its mutable staging file.
    for path, digest in ((target, payload_hash),):
        if path.exists() and data._hash(path) != digest:
            raise ValueError('A different payload already owns this database and cycle lineage.')
    if manifest_path.exists():
        previous = data._json(manifest_path)
        if any(previous.get(k) != manifest[k] for k in owner_fields):
            raise ValueError('A different manifest already owns this database and cycle lineage.')
    pending = target.with_suffix('.' + uuid.uuid4().hex + '.pending')
    pending_manifest = manifest_path.with_suffix('.' + uuid.uuid4().hex + '.pending')
    try:
        if not target.exists():
            shutil.copyfile(payload, pending)
            if data._hash(pending) != payload_hash:
                raise ValueError('Payload changed during publication.')
            try:
                os.link(pending, target)
            except FileExistsError:
                if data._hash(target) != payload_hash:
                    raise ValueError('Another publisher owns a different payload for this lineage.')
        if data._hash(workbook) != workbook_hash or data._hash(database) != database_hash:
            raise ValueError('Workbook or database changed during publication.')
        pending_manifest.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        try:
            os.link(pending_manifest, manifest_path)
        except FileExistsError:
            previous = data._json(manifest_path)
            if any(previous.get(k) != manifest[k] for k in owner_fields):
                raise ValueError('Another publisher owns a different manifest for this lineage.')
    finally:
        pending.unlink(missing_ok=True)
        pending_manifest.unlink(missing_ok=True)
    return {'dataset': info['label'], 'manifest': str(manifest_path), 'payload': str(target), **manifest}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', required=True, type=Path)
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = publish(args.workbook, args.payload)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text)
