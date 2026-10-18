"""Storage of the D075 container runtime for the downstream spending collector: preflight, export and import. Standard library only, Linux-safe, no key, no network.

The live frontier ledger sits in a Docker named volume (/frontier), never in a bind mount: SQLite locks are blind across the Docker Desktop VM (D-062 evidence). So that
the host's validator and the chain of accepted batches keep working, every invocation ENDS with an export of the ledger and its records to a host bind (/export/<run id>/),
written exclusively, fsynced and hash-checked; the host then verifies it through compute_job.py verify --map and promotes it after acceptance.

    python downstream_volume.py import --source /import/frontier.sqlite --expect-sha256 HEX --dest /frontier/frontier.sqlite [--record FILE]

preflight() and export_run() are the library calls compute_job.py makes before and after the collector child.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stored_paths  # noqa: E402

SIDECARS = ('-journal', '-wal', '-shm')
LEDGER = 'frontier.sqlite'
HEX64 = re.compile(r'[0-9a-f]{64}')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sidecars(ledger):
    return [Path(str(ledger) + suffix) for suffix in SIDECARS if Path(str(ledger) + suffix).exists()]


def utc():
    return datetime.now(timezone.utc).isoformat()


def preflight(frontier, hoststate, *, expect_base_sha256, descriptor=None, tools=None):
    """Problems that forbid starting the collector, before any admission; empty means go. Hashes the ledger once (it is the base the chain names)."""
    frontier, hoststate = Path(frontier), Path(hoststate)
    problems, ledger = [], Path(frontier) / LEDGER
    if not HEX64.fullmatch(str(expect_base_sha256)):
        problems.append('--expect-base-sha256 must be 64 lowercase hex digits')
    if not ledger.is_file():
        problems.append('no ledger in the frontier volume; this job never creates one (a new ledger forks the frontier) - import a schema-3 snapshot first')
    else:
        left = sidecars(ledger)
        if left:
            problems.append('journal/WAL sidecar(s) beside the ledger (' + ', '.join(item.name for item in left) + '): a crash left them; run collect-downstream-recover first')
        elif sha256_file(ledger) != expect_base_sha256:
            problems.append('the ledger in the volume is not the base the caller expects (sha256 differs from --expect-base-sha256)')
    import collect_committee_activity as driver
    crashed, legacy = driver.unresolved_runs(frontier, hoststate if hoststate.is_dir() else None)
    if crashed or legacy:
        problems.append('unresolved collector run(s) ' + ', '.join(crashed + legacy) + ': recover them before another live run')
    if descriptor is not None:
        tools = Path(tools) if tools is not None else HERE
        try:
            value = json.loads(Path(descriptor).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            value = None
        if not isinstance(value, dict):
            problems.append('the host evidence-pool descriptor is missing or unreadable')
        else:
            for name, field in (('fec_accounted_client.py', 'adapter_sha256'), ('spending_endpoint_contracts.py', 'contracts_sha256')):
                if value.get(field) != sha256_file(tools / name):
                    problems.append('the descriptor ' + field + ' differs from the mounted ' + name + ': the tree changed between broker start and now')
    return problems


def _copy_exclusive(source, target):
    """Copy to a file that must not exist, fsync it, and return (sha256, bytes) of what was WRITTEN; the caller compares it with the source."""
    digest, size = hashlib.sha256(), 0
    with open(source, 'rb') as reader, open(target, 'xb') as writer:
        for chunk in iter(lambda: reader.read(1 << 20), b''):
            writer.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest(), size


def export_run(frontier, export_root, run_id, *, image_digest=None, volume=None):
    """The in-job export (design 4.5): the ledger and the records the host needs, copied once to <export_root>/<run id>/, hash-checked, EXPORT.json last.

    Refuses (returns problems, writes nothing) when a journal/WAL sidecar exists (a crash: recover first), when no receipt or abandon marker names this run,
    when the receipt's ledger_sha256 is not the ledger's hash, or when the export folder already exists. Returns {'files': [...], 'problems': [...]}."""
    frontier, export_root = Path(frontier), Path(export_root)
    ledger, problems = frontier / LEDGER, []
    if not re.fullmatch(r'[0-9a-f]{32}', str(run_id)):
        return {'files': [], 'problems': ['the collector run id is not 32 hex digits']}
    if not ledger.is_file():
        return {'files': [], 'problems': ['no ledger to export']}
    left = sidecars(ledger)
    if left:
        return {'files': [], 'problems': ['not exported: journal/WAL sidecar(s) ' + ', '.join(item.name for item in left) + ' mean a crash; run collect-downstream-recover']}
    receipt, abandoned, start = frontier / ('RECEIPT-' + run_id + '.json'), frontier / ('RUN-' + run_id + '.ABANDONED.json'), frontier / ('RUN-' + run_id + '.START.json')
    record = receipt if receipt.is_file() else abandoned if abandoned.is_file() else None
    if record is None:
        return {'files': [], 'problems': ['not exported: no receipt or abandon marker names run ' + run_id + '; the run is unresolved, recover it first']}
    before = sha256_file(ledger)
    if record is receipt:
        try:
            bound = json.loads(receipt.read_bytes()).get('ledger_sha256')
        except (OSError, ValueError):
            bound = None
        if bound != before:
            return {'files': [], 'problems': ['not exported: the ledger hash differs from the receipt it should bind (' + str(bound) + ' vs ' + before + ')']}
    sources = [ledger, start, record]
    if start.is_file():
        try:
            copy = Path(json.loads(start.read_bytes()).get('seed_receipt', {}).get('captured_copy', '')).name
        except (OSError, ValueError):
            copy = ''
        if re.fullmatch(r'seeds-[0-9a-f]{64}\.json', copy) and (frontier / copy).is_file():
            sources.append(frontier / copy)
    sources += sorted(frontier.glob('frontier.endpoint-contracts*.json'))
    target = export_root / run_id
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return {'files': [], 'problems': ['not exported: ' + str(target) + ' already exists (an export is never overwritten)']}
    files, copy_sha = [], None
    for source in sources:
        if not source.is_file():
            continue
        expected = before if source == ledger else sha256_file(source)
        written, size = _copy_exclusive(source, target / source.name)
        again = sha256_file(target / source.name)
        if not (written == expected == again and size == source.stat().st_size):
            problems.append('export of ' + source.name + ' does not match its source (written ' + written + ', re-read ' + again + ', expected ' + expected + ')')
        files.append({'name': source.name, 'sha256': again, 'bytes': size})
        if source == ledger:
            copy_sha = again
    after = sha256_file(ledger)
    if after != before:
        problems.append('the ledger changed while it was exported (' + before + ' -> ' + after + '); the export is not a freeze')
    document = {'schema_version': 1, 'purpose': 'downstream-frontier-export', 'collector_run_id': run_id, 'exported_utc': utc(),
                'original_ledger': stored_paths.to_stored(ledger), 'export_directory': stored_paths.to_stored(target), 'volume': volume, 'image_digest': image_digest,
                'ledger_sha256': {'before': before, 'copy': copy_sha, 'after': after},
                'record': {'name': record.name, 'sha256': next((item['sha256'] for item in files if item['name'] == record.name), None)}, 'files': files, 'problems': problems}
    with open(target / 'EXPORT.json', 'xb') as stream:
        stream.write((json.dumps(document, indent=2) + '\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    files.append({'name': 'EXPORT.json', 'sha256': sha256_file(target / 'EXPORT.json'), 'bytes': (target / 'EXPORT.json').stat().st_size})
    return {'files': files, 'problems': problems}


def import_ledger(source, dest, expect_sha256, record=None):
    """Seed an EMPTY frontier volume from a host snapshot: exclusive create, fsync, hash equality with the caller's expectation before and after. Never overwrites."""
    source, dest = Path(source), Path(dest)
    if not HEX64.fullmatch(str(expect_sha256)):
        raise ValueError('--expect-sha256 must be 64 lowercase hex digits')
    if dest.exists() or sidecars(dest):
        raise FileExistsError('the volume already holds a ledger (or its sidecars); an import never overwrites')
    if not source.is_file() or sha256_file(source) != expect_sha256:
        raise ValueError('the import source is missing or is not the snapshot the caller named')
    try:
        written, size = _copy_exclusive(source, dest)
        if written != expect_sha256 or sha256_file(dest) != expect_sha256:
            raise ValueError('the imported ledger does not hash to the expected snapshot')
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    result = {'schema_version': 1, 'purpose': 'downstream-frontier-import', 'imported_utc': utc(), 'source': str(source), 'dest': str(dest), 'dest_stored_name': stored_paths.to_stored(dest),
              'sha256': expect_sha256, 'bytes': size}
    if record is not None:
        with open(record, 'xb') as stream:
            stream.write((json.dumps(result, indent=2) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
    return result


def _json_file(path):
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def reconcile_broker(state, run_id):
    """Host side, once the broker has exited (review F3): what it CHARGED to the shared ledger, what it JOURNALLED and what the collector's receipt counts must agree.

    physical_attempts_charged == attempts_journalled == (attempt lines in the journal file) == receipt http_attempts + len(undelivered_attempts). Any gap is an attempt that
    reached the provider and no record holds (or a record with no attempt), so the run is not a completion. Reads <state>/broker/BROKER-<id>.json, the journal beside it and
    the exported <state>/exports/<id>/RECEIPT-<id>.json (an abandoned run counts 0). Returns {'ok', 'problems', 'charged', 'journalled', 'http_attempts', 'undelivered'}."""
    state, problems = Path(state), []
    out = {'ok': False, 'problems': problems, 'charged': None, 'journalled': None, 'http_attempts': None, 'undelivered': None}
    if not re.fullmatch(r'[0-9a-f]{32}', str(run_id)):
        problems.append('the collector run id is not 32 hex digits')
        return out
    broker = _json_file(state / 'broker' / ('BROKER-' + run_id + '.json'))
    exported = state / 'exports' / run_id
    receipt = _json_file(exported / ('RECEIPT-' + run_id + '.json'))
    abandoned = receipt is None and (exported / ('RUN-' + run_id + '.ABANDONED.json')).is_file()
    if broker is None:
        problems.append('no readable broker receipt BROKER-' + run_id + '.json: the broker never opened or did not stop cleanly')
    if receipt is None and not abandoned:
        problems.append('no exported collector receipt RECEIPT-' + run_id + '.json (and no abandon marker) to reconcile against')
    if problems:
        return out
    charged, journalled, undelivered = broker.get('physical_attempts_charged'), broker.get('attempts_journalled'), broker.get('undelivered_attempts')
    counted = 0 if abandoned else receipt.get('http_attempts')
    if not all(type(value) is int and value >= 0 for value in (charged, journalled, counted)) or not isinstance(undelivered, list):
        problems.append('the broker receipt or the collector receipt does not carry the counts to reconcile (physical_attempts_charged, attempts_journalled, undelivered_attempts, http_attempts)')
        return out
    out.update(charged=charged, journalled=journalled, http_attempts=counted, undelivered=len(undelivered))
    try:
        lines = [json.loads(line) for line in (state / 'broker' / (run_id + '.attempts.ndjson')).read_bytes().splitlines() if line.strip()]
        in_file = sum(1 for line in lines if isinstance(line, dict) and line.get('event') == 'attempt')
    except (OSError, ValueError):
        in_file = None
    if in_file != journalled:
        problems.append('the broker journal file holds %s attempt line(s) but the broker receipt says %d were journalled' % (in_file, journalled))
    if charged != journalled:
        problems.append('the broker charged %d physical attempt(s) to the shared ledger but journalled %d: %d attempt(s) have no record' % (charged, journalled, charged - journalled))
    if journalled != counted + len(undelivered):
        problems.append('the broker journalled %d attempt(s) but the collector receipt counts %d plus %d undelivered' % (journalled, counted, len(undelivered)))
    out['ok'] = not problems
    return out


def _verified_export(folder):
    """-> (entry, None) for an export that verifies, else (None, reason). entry = (exported_utc, run id, record name, record sha256, ledger-copy sha256)."""
    run_id = folder.name
    document = _json_file(folder / 'EXPORT.json')
    if document is None or document.get('collector_run_id') != run_id or document.get('purpose') != 'downstream-frontier-export':
        return None, 'EXPORT.json is missing or does not name this run'
    if document.get('problems'):
        return None, 'EXPORT.json records problems'
    record = document.get('record')
    if not isinstance(record, dict) or record.get('name') not in ('RECEIPT-' + run_id + '.json', 'RUN-' + run_id + '.ABANDONED.json'):
        return None, 'EXPORT.json names no receipt or abandon marker for this run'
    listed = {item.get('name'): item.get('sha256') for item in document.get('files', []) if isinstance(item, dict)}
    if not (folder / record['name']).is_file() or sha256_file(folder / record['name']) != record.get('sha256') or listed.get(record['name']) != record.get('sha256'):
        return None, 'the exported record does not hash to what EXPORT.json lists'
    copy = (document.get('ledger_sha256') or {}).get('copy') if isinstance(document.get('ledger_sha256'), dict) else None
    if not (folder / LEDGER).is_file() or sha256_file(folder / LEDGER) != copy:
        return None, 'the exported ledger does not hash to what EXPORT.json lists'
    return (str(document.get('exported_utc')), run_id, record['name'], record['sha256'], copy), None


def _runs_with_pages(ledger):
    """The run ids whose response folders hold a page that the ledger records (pages.archive_path names responses-<32 hex>). Read-only and immutable; raises OSError / sqlite3.Error."""
    with closing(sqlite3.connect(Path(ledger).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        rows = db.execute("SELECT DISTINCT substr(archive_path, instr(archive_path, 'responses-'), 42) FROM pages WHERE instr(archive_path, 'responses-') > 0").fetchall()
    return {found.group(1) for (text,) in rows for found in [re.fullmatch(r'responses-([0-9a-f]{32})', text)] if found}


def _declares_no_new_pages(record):
    document = _json_file(record)
    return document is not None and type(document.get('new_pages_this_invocation')) is int and document['new_pages_this_invocation'] == 0


def restore_receipts(state, expect_sha256):
    """Host side, after a volume import (review F4): give the state folder the RECEIPT (or abandon marker) of every earlier container run.

    Run records live in the volume and in <state>/exports/<id>/. After the volume is lost and the ledger re-imported, the earlier runs' response folders have no START and no
    receipt beside them, so the collector and the preflight call them legacy crashes and refuse every live run. unresolved_runs() looks for the receipt in the state folder, so each
    export that VERIFIES (EXPORT.json clean, the record file hashing to what it lists) is copied there, exclusively and hash-checked, never over a different file. Only exports
    taken at or before the one holding the imported ledger are restored: a later run's pages are not in that ledger, so its folder must stay unresolved for a human. Time alone is right
    only while the volume's lineage is linear (importing an older export starts a branch), so a RECEIPT that declares new pages is restored only when the imported ledger itself holds
    pages of that run's response folder, and a run the state folder already resolves the other way (an abandon marker for a receipt, or the reverse) is left alone (delta review D3).
    A lost exclusive-create race reports a clash and deletes nothing: the file that won is not ours (D2).
    Returns {'restored', 'already_present', 'not_restored', 'problems', 'anchor'}."""
    state = Path(state)
    out = {'restored': [], 'already_present': [], 'not_restored': [], 'problems': [], 'anchor': None}
    if not HEX64.fullmatch(str(expect_sha256)):
        out['problems'].append('--expect-sha256 must be 64 lowercase hex digits')
        return out
    exports, verified = state / 'exports', []
    for folder in sorted(exports.iterdir()) if exports.is_dir() else []:
        if not folder.is_dir() or not re.fullmatch(r'[0-9a-f]{32}', folder.name):
            continue
        entry, why = _verified_export(folder)
        if entry is None:
            out['not_restored'].append({'run_id': folder.name, 'reason': why})
        else:
            verified.append(entry)
    holders = [entry for entry in verified if entry[4] == expect_sha256]
    if not holders:
        out['not_restored'] += [{'run_id': entry[1], 'reason': "the imported ledger is not this export's ledger"} for entry in verified]
        return out
    anchor = max(holders)
    out['anchor'] = anchor[1]
    pages, unreadable = None, ''                              # the anchor ledger's page runs, read at most once and only when a receipt needs them
    for stamp, run_id, name, digest, _ in verified:
        if stamp > anchor[0]:
            out['not_restored'].append({'run_id': run_id, 'reason': 'exported after the imported ledger; its pages are not in it, so its response folder stays unresolved'})
            continue
        target = state / name
        if target.exists():
            if target.is_file() and sha256_file(target) == digest:
                out['already_present'].append(run_id)
            else:
                out['problems'].append(name + ' already exists in the state folder and differs from the export; nothing was overwritten')
            continue
        other = state / (('RUN-' + run_id + '.ABANDONED.json') if name.startswith('RECEIPT-') else ('RECEIPT-' + run_id + '.json'))
        if other.exists():
            out['not_restored'].append({'run_id': run_id, 'reason': other.name + ' already resolves this run in the state folder; nothing was written'})
            continue
        if name.startswith('RECEIPT-') and not _declares_no_new_pages(exports / run_id / name):
            if pages is None and not unreadable:
                try:
                    pages = _runs_with_pages(exports / anchor[1] / LEDGER)
                except (OSError, sqlite3.Error) as error:
                    unreadable = str(error) or type(error).__name__
            if pages is None or run_id not in pages:
                out['not_restored'].append({'run_id': run_id, 'reason': ('the imported ledger could not be read for its pages (' + unreadable + '); its response folder stays unresolved') if pages is None
                                            else "its pages are not in the imported ledger (another branch of the lineage), so its response folder stays unresolved"})
                continue
        try:
            written, _size = _copy_exclusive(exports / run_id / name, target)
            if written != digest or sha256_file(target) != digest:
                raise ValueError('the copy does not hash like its source')
        except FileExistsError:                                    # another writer won the exclusive create after the exists() check: that file is not ours to delete (delta review D2)
            out['problems'].append(name + ' appeared in the state folder while it was being restored; nothing was overwritten or deleted')
            continue
        except (OSError, ValueError) as error:
            target.unlink(missing_ok=True)
            out['problems'].append('could not restore ' + name + ': ' + str(error))
            continue
        out['restored'].append(run_id)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    imported = commands.add_parser('import')
    imported.add_argument('--source', type=Path, required=True)
    imported.add_argument('--dest', type=Path, required=True)
    imported.add_argument('--expect-sha256', required=True)
    imported.add_argument('--record', type=Path)
    reconciled = commands.add_parser('reconcile', help='host side: broker charged == journalled == receipt http_attempts + undelivered (exit 1 on any gap)')
    reconciled.add_argument('--state', type=Path, required=True)
    reconciled.add_argument('--run-id', required=True)
    restored = commands.add_parser('restore-receipts', help='host side, after an import: copy the verified exported receipts of earlier runs into the state folder (exit 1 on a problem)')
    restored.add_argument('--state', type=Path, required=True)
    restored.add_argument('--expect-sha256', required=True)
    args = parser.parse_args(argv)
    if args.command in ('reconcile', 'restore-receipts'):
        result = reconcile_broker(args.state, args.run_id) if args.command == 'reconcile' else restore_receipts(args.state, args.expect_sha256)
        print(json.dumps(result))
        return 0 if result.get('ok', not result['problems']) else 1
    try:
        print(json.dumps(import_ledger(args.source, args.dest, args.expect_sha256, args.record)))
        return 0
    except (OSError, ValueError) as error:
        print('downstream_volume: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
