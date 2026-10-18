"""A campaign's operating expenditures from the FEC bulk file (oppexp), for committees whose API Schedule B is not complete.

Read-only and separate from candidate_spending's API figures (D-074; Paul's rulings Q1/Q4, 2026-10-15):
  * Bulk rows are their own coverage class, 'bulk_vintage_operating_spend'. They are never added to the API ring, totals,
    coverage or peer shares, and they never change an endpoint's status.
  * They are shown only for committees whose API Schedule B endpoint is not 'complete'. Once it is complete, the API rows
    are the record and no bulk row is returned for that committee.
  * Simplified rows: date, payee, amount, purpose. Memo rows (MEMO_CD 'X') are hidden by default and counted.
  * Join: the bulk FILE's cycle (oppexp22 holds cycle 2022), which matched the API's two_year_transaction_period on
    2,397 of 2,397 rows in parity. The transaction-date cycle does not (99.7%). Parity covered cycle 2022 only.
  * Label: "FEC bulk file, as of <the file's HTTP Last-Modified>", as recorded in MANIFEST.json vintage_asof. When the
    manifest has no vintage the label says the date is unavailable; it never substitutes the download time.
  * Operating expenditures only (Form 3 line 17, 3P line 23, 3X line 21B): not the whole of Schedule B.

Source: <mentat>/state/fec-bulk/oppexp/<yy>/<YYYYMMDD>/{MANIFEST.json, oppexp<yy>.parquet}, written by the D-074 ingest.
The newest YYYYMMDD directory per cycle is used. MENTAT_BULK_OPPEXP_DIR points elsewhere (tests, previews).
"""
import datetime as dt
import hashlib
import json
import os
import threading
from pathlib import Path

DIR_ENV = 'MENTAT_BULK_OPPEXP_DIR'
DEFAULT_DIR = Path(__file__).resolve().parents[1] / 'state' / 'fec-bulk' / 'oppexp'
COVERAGE_CLASS = 'bulk_vintage_operating_spend'
SOURCE_KIND = 'fec-bulk-oppexp'
ROWS_SHOWN = 100            # newest first; the total is reported
COLUMNS = ['CMTE_ID', 'NAME', 'PURPOSE', 'MEMO_CD', 'SUB_ID', 'transaction_date', 'amount', 'file_cycle', 'source_kind', 'coverage_class']
SCOPE = 'Operating expenditures only (Form 3 line 17, Form 3P line 23, Form 3X line 21B)'
_VERIFIED = {}              # parquet path -> sha256 matched its manifest (checked once per process)
_LOCK = threading.Lock()


def label(vintage_asof):
    """'2026-10-12T17:36:09Z' -> 'FEC bulk file, as of Oct 12, 2026'. No usable date: say so, never guess one."""
    try:
        when = dt.datetime.strptime(str(vintage_asof or ''), '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return 'FEC bulk file, source date unavailable'
    return f'FEC bulk file, as of {when:%b} {when.day}, {when.year}'


def _root():
    return Path(os.environ.get(DIR_ENV, '').strip() or DEFAULT_DIR)


def _vintage(cycle):
    """(manifest, parquet path) of the newest vintage for the cycle's file, or None."""
    folder = _root() / f'{int(cycle) % 100:02d}'
    dated = sorted((item for item in folder.glob('*') if item.is_dir() and item.name.isdigit() and len(item.name) == 8), reverse=True)
    if not dated:
        return None
    manifest = json.loads((dated[0] / 'MANIFEST.json').read_text(encoding='utf-8'))
    if manifest.get('source_kind') != SOURCE_KIND or int(manifest.get('file_cycle') or 0) != int(cycle):
        raise ValueError(f'{dated[0]} is not the oppexp file for {cycle}')
    return manifest, dated[0] / manifest['parquet']['file']


def _verify(path, manifest):
    with _LOCK:
        if _VERIFIED.get(path):
            return
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    if digest.hexdigest() != manifest['parquet']['sha256']:
        raise ValueError(f'{path.name} does not match its MANIFEST.json')
    with _LOCK:
        _VERIFIED[path] = True


def read_rows(committee_ids, cycle, include_memo=False):
    """Bulk rows for the committees and the file of `cycle`: {'status', 'vintage_asof', 'rows', 'memo_rows_hidden', ...}.
    status 'unavailable' when there is no file for the cycle or no Parquet reader; never an empty answer passed off as $0."""
    ids = sorted({str(value) for value in committee_ids if value})
    found = _vintage(cycle)
    if found is None:
        return {'status': 'unavailable', 'message': f'No FEC bulk file on hand for {int(cycle) - 1}–{str(cycle)[2:]}'}
    manifest, path = found
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        return {'status': 'unavailable', 'message': 'Bulk file reader (pyarrow) not installed'}
    _verify(path, manifest)
    table = pq.read_table(path, columns=COLUMNS, filters=[('CMTE_ID', 'in', ids), ('file_cycle', '=', int(cycle))]) if ids else None
    records = table.to_pylist() if table is not None else []
    if any(row['source_kind'] != SOURCE_KIND or row['coverage_class'] != COVERAGE_CLASS for row in records):
        raise ValueError('Bulk rows carry a foreign source_kind or coverage_class')
    is_memo = lambda row: str(row['MEMO_CD'] or '').strip().upper() == 'X'
    kept = records if include_memo else [row for row in records if not is_memo(row)]
    kept.sort(key=lambda row: (row['transaction_date'] or dt.date.min, row['SUB_ID']), reverse=True)
    return {'status': 'ok' if kept else 'empty', 'vintage_asof': manifest.get('vintage_asof'), 'bulk_file': manifest.get('bulk_file'),
            'parquet_sha256': manifest['parquet']['sha256'], 'total': len(kept),
            'memo_rows_hidden': 0 if include_memo else sum(1 for row in records if is_memo(row)),
            'rows': [{'date': row['transaction_date'].isoformat() if row['transaction_date'] else '', 'payee': row['NAME'] or '',
                      'amount': float(row['amount']) if row['amount'] is not None else None, 'purpose': row['PURPOSE'] or '',
                      'committee_id': row['CMTE_ID'], 'sub_id': row['SUB_ID']} for row in kept[:ROWS_SHOWN]]}


def section(committees, mapped_ids, cycle):
    """The panel's bulk section for one campaign. committees: the reader's per-committee entries (endpoint_status['B']);
    mapped_ids: the campaign's committee ids. Bulk is read only for the ids whose API Schedule B is not complete."""
    complete = {item.get('committee_id') for item in committees if (item.get('endpoint_status') or {}).get('B') == 'complete'}
    wanted = [value for value in dict.fromkeys(mapped_ids) if value and value not in complete]
    base = {'coverage_class': COVERAGE_CLASS, 'scope': SCOPE, 'committee_ids': wanted,
            'note': 'Not API-collected; not added to the spending figures above'}
    if not mapped_ids:
        return {**base, 'shown': False, 'reason': 'no_committee'}
    if not wanted:
        return {**base, 'shown': False, 'reason': 'api_complete',
                'message': 'API spending is complete for every committee; FEC bulk rows are not shown'}
    try:
        read = read_rows(wanted, cycle)
    except Exception as error:      # the bulk section never takes the API answer down with it
        return {**base, 'shown': False, 'reason': 'bulk_error', 'message': f'The FEC bulk file could not be read: {error}'}
    if read['status'] == 'unavailable':
        return {**base, 'shown': False, 'reason': 'unavailable', 'message': read['message']}
    return {**base, 'shown': True, 'label': label(read['vintage_asof']), **read}
