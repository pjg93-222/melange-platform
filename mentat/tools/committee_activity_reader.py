"""Typed read-only consumer of the accepted committee-activity snapshots (campaign spending).

Standard library only. Opens the newest accepted batch (accepted-batches/batch-NN/frontier.sqlite beside a
SNAPSHOT.json whose snapshot_sha256 matches the file) with mode=ro&immutable=1 and never touches the live
canonical ledger. Importing this module makes no calls and opens nothing.

Coverage is stated per committee and cycle and never implies zero: a pending, partial, unsupported or unseeded
endpoint is unknown. Schedule B rows are the provider's ORIGINAL objects, read from the hash-verified archived
response bodies (the ledger's own observations.raw_json is a canonicalised copy and is not used). Numbers are
parsed with the standard json defaults (floats), which is what any JSON client of the provider sees.

CONSUMER EXCLUSION RULE (D070/D072/R1b; a rule, not code: neither the collector nor the validator enforces it). Origins whose metadata says
discovery_source campaign_priority (D072, `campaign-priority-<candidate>-<cycle>`) or fec_bulk_oth (D070, `bulk-oth-hint-...`) carry
giving_or_payment_evidence = false and hint_not_coverage = true: they record why a unit was NOMINATED for collection, never a giving, receipt or
payment relationship. This reader returns Schedule B rows and totals only and never reads the origin tables, so nothing here depends on them; any
consumer that reads origins (schema 2 `origins` rows, or schema 3 `origin_catalog` + `origin_seeds` + the derived closure) must drop every origin
with giving_or_payment_evidence is False (test `is False`: older origins do not carry the key) before any giving, receipt, per-company, per-industry
or per-donor view, count or denominator. A unit whose only origins are hint-tagged is discovery-only, never covered, collected or zero.
"""
import hashlib
import json
import re
import sqlite3
from pathlib import Path

DEFAULT_STATE = Path(__file__).resolve().parents[1] / 'state' / 'committee-activity'
COMMITTEE = re.compile(r'C[0-9]{8}')
CANDIDATE = re.compile(r'[HSP][A-Z0-9]{8}')
_SHA = {}


class SnapshotUnavailable(RuntimeError):
    """No usable accepted snapshot (none present, unreadable SNAPSHOT.json, or its recorded hash disagrees)."""


class ArchiveError(RuntimeError):
    """An archived page body is missing, changed, or does not match the ledger; nothing is guessed in its place."""


def _file_sha(path):
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _SHA:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1 << 20), b''):
                digest.update(block)
        _SHA[key] = digest.hexdigest()
    return _SHA[key]


def _latest_snapshot(state):
    best = None
    for folder in (state / 'accepted-batches').glob('batch-*'):
        match = re.fullmatch(r'batch-([0-9]+)', folder.name)
        if not match or not (folder / 'frontier.sqlite').is_file():
            continue
        try:
            declared = json.loads((folder / 'SNAPSHOT.json').read_text(encoding='utf-8')).get('snapshot_sha256')
        except (OSError, ValueError, AttributeError):
            continue  # not (yet) accepted: no readable SNAPSHOT.json beside the copy
        if best is None or int(match.group(1)) > best[0]:
            best = (int(match.group(1)), folder / 'frontier.sqlite', declared)
    if best is None:
        raise SnapshotUnavailable('No accepted batch snapshot under ' + str(state))
    batch, path, declared = best
    actual = _file_sha(path)
    if declared != actual:
        raise SnapshotUnavailable('Snapshot hash differs from its SNAPSHOT.json: ' + str(path))
    return batch, path, actual


def _resolve(state, recorded):
    path = Path(recorded)
    # A relocated state tree keeps the responses-<run>/<file> layout; the recorded absolute path may no longer exist.
    return path if path.is_file() else state / path.parent.name / path.name


def _load_page(state, page):
    path = _resolve(state, page['archive_path'])
    try:
        body = path.read_bytes()
    except OSError as error:
        raise ArchiveError('Archived page body unavailable: ' + str(path)) from error
    if len(body) != page['archive_bytes'] or hashlib.sha256(body).hexdigest() != page['archive_sha256']:
        raise ArchiveError('Archived page body does not match the ledger: ' + str(path))
    results = json.loads(body).get('results')
    if not isinstance(results, list) or len(results) != page['records_count']:
        raise ArchiveError('Archived page result array does not match the ledger: ' + str(path))
    return results, path


def _retrieved_at(db, kind, committee, cycle, pages, paths):
    if not pages:
        return None
    try:
        row = db.execute('SELECT retrieved_at FROM endpoint_retrievals WHERE committee_id=? AND cycle=? AND kind=? AND page_no=?',
                         (committee, cycle, kind, pages[-1]['page_no'])).fetchone()
    except sqlite3.OperationalError:  # snapshot predates the extension tables
        row = None
    if row:
        return row[0]
    name = paths[-1].name  # older pages: the adapter's metadata record beside the body
    if name.endswith('.body.bin'):
        try:
            return json.loads(paths[-1].with_name(name[:-len('.body.bin')] + '.json').read_text(encoding='utf-8')).get('captured_utc')
        except (OSError, ValueError, AttributeError):
            return None
    return None


def _family(db, state, committee, cycle, kind):
    endpoint = db.execute('SELECT status,rows_stored FROM endpoints WHERE committee_id=? AND cycle=? AND kind=?',
                          (committee, cycle, kind)).fetchone()
    pages = db.execute('SELECT page_no,archive_path,archive_sha256,archive_bytes,records_count FROM pages '
                       'WHERE committee_id=? AND cycle=? AND kind=? ORDER BY page_no', (committee, cycle, kind)).fetchall()
    rows, paths = [], []
    for page in pages:
        results, path = _load_page(state, page)
        rows.extend(results)
        paths.append(path)
    if endpoint is not None and len(rows) != endpoint['rows_stored']:
        raise ArchiveError('Archived row count differs from the ledger for ' + committee + ' ' + kind)
    status = endpoint['status'] if endpoint is not None else 'unsupported'
    return status, rows, _retrieved_at(db, kind, committee, cycle, pages, paths), len(pages)


def _committee(db, state, committee, cycle):
    if db.execute('SELECT 1 FROM units WHERE committee_id=? AND cycle=?', (committee, cycle)).fetchone() is None:
        return _empty(committee, cycle, 'not_seeded', 'not_seeded')
    b_status, rows, b_at, b_pages = _family(db, state, committee, cycle, 'B')
    t_status, totals, t_at, t_pages = _family(db, state, committee, cycle, 'totals')
    boundaries = db.execute("SELECT COUNT(*) FROM leaf_coverage WHERE committee_id=? AND cycle=? AND kind='B'", (committee, cycle)).fetchone()[0]
    if b_status == 'complete' and t_status == 'complete' and totals:
        coverage = 'complete'  # an empty totals answer is retrieved but states nothing, so it cannot complete coverage
    elif b_pages or t_pages:
        coverage = 'partial'
    else:
        coverage = 'pending'
    name = next((row['committee']['name'] for row in rows if isinstance(row, dict) and isinstance(row.get('committee'), dict) and row['committee'].get('name')), None)
    if name is None:
        name = next((row.get('committee_name') for row in totals if isinstance(row, dict) and row.get('committee_name')), None)
    return {'committee_id': committee, 'committee_name': name, 'cycle': cycle, 'coverage_status': coverage,
            'endpoint_status': {'B': b_status, 'totals': t_status}, 'retrieved_at': {'B_latest': b_at, 'totals': t_at},
            'totals': totals, 'rows': rows, 'row_occurrences': len(rows), 'disclosure_boundaries': boundaries}


def _empty(committee, cycle, status, endpoint_status):
    return {'committee_id': committee, 'committee_name': None, 'cycle': cycle, 'coverage_status': status,
            'endpoint_status': {'B': endpoint_status, 'totals': endpoint_status}, 'retrieved_at': {'B_latest': None, 'totals': None},
            'totals': [], 'rows': [], 'row_occurrences': 0, 'disclosure_boundaries': 0}


def _mapping(db, candidate, cycle):
    try:
        found = db.execute('SELECT committee_id,source,source_sha256,vintage_or_retrieved_at,detail_json FROM candidate_committee_map '
                           'WHERE candidate_id=? AND cycle=? ORDER BY committee_id', (candidate, cycle)).fetchall()
    except sqlite3.OperationalError:  # snapshot predates the extension tables
        return None
    if not found:
        return None
    labels = sorted({(row['source'] + ':' + json.loads(row['detail_json']).get('file', '') + ':' if row['source'] == 'local_ccl' else row['source'] + ':')
                     + 'sha256=' + row['source_sha256'] for row in found})
    vintages = sorted({row['vintage_or_retrieved_at'] for row in found if row['vintage_or_retrieved_at']})
    return {'candidate_id': candidate, 'source': '; '.join(labels), 'vintage_or_retrieved_at': '; '.join(vintages) or None,
            'committee_ids': [row['committee_id'] for row in found]}


def read_campaign_spending(*, cycle, candidate_id=None, committee_id=None, state_dir=None):
    """Campaign spending for one candidate's principal committees, or for one committee, from the newest accepted snapshot."""
    if type(cycle) is not int:
        raise TypeError('cycle must be an int')
    if candidate_id is None and committee_id is None:
        raise ValueError('candidate_id or committee_id is required')
    if candidate_id is not None and not (isinstance(candidate_id, str) and CANDIDATE.fullmatch(candidate_id)):
        raise ValueError('candidate_id must look like H0AA00001')
    if committee_id is not None and not (isinstance(committee_id, str) and COMMITTEE.fullmatch(committee_id)):
        raise ValueError('committee_id must look like C00000001')
    state = Path(state_dir) if state_dir is not None else DEFAULT_STATE
    batch, path, sha = _latest_snapshot(state)
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        mapping = _mapping(db, candidate_id, cycle) if candidate_id is not None else None
        if committee_id is not None:
            if mapping is not None and committee_id not in mapping['committee_ids']:
                raise ValueError('committee_id is not a stored principal committee of candidate_id for this cycle')
            committees = [_committee(db, state, committee_id, cycle)]
        elif mapping is None:
            committees = [_empty(None, cycle, 'mapping_unknown', 'unknown')]
        else:
            committees = [_committee(db, state, cid, cycle) for cid in mapping['committee_ids']]
    finally:
        db.close()
    return {'query': {'cycle': cycle, 'candidate_id': candidate_id, 'committee_id': committee_id},
            'snapshot': {'batch': batch, 'sha256': sha}, 'mapping': mapping, 'committees': committees}
