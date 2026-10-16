"""Recover FEC receipt evidence into a private, resumable database copy.

Saved API checkpoints are tried first. --fetch enables exact-sub-ID FEC reads,
bounded by --max-requests. Financial facts never change. Original databases and
every changed metadata value remain available for review.
"""
from __future__ import annotations

import argparse
from contextlib import closing, ExitStack
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from report_source_guard import atomic, cents, sha, source_sha

FIELDS = ('filing_form', 'line_number', 'line_number_label', 'receipt_type',
          'receipt_type_full', 'memo_text', 'memo_code', 'memoed_subtotal',
          'back_reference_transaction_id', 'back_reference_schedule_name',
          'file_num', 'report_year', 'report_type')


def fec_bool(value):
    return value is True or str(value).strip().upper() in ('1', 'TRUE', 'X')


def validate_identity(stored, evidence):
    if str(stored['sub_id']) != str(evidence.get('sub_id')):
        raise ValueError('Hydration returned the wrong FEC source ID')
    for field in ('committee_id', 'contributor_name'):
        if str(stored.get(field) or '').strip().upper() != str(evidence.get(field) or '').strip().upper():
            raise ValueError(f'FEC identity conflict for {stored["sub_id"]}: {field}')
    if cents(stored['contribution_receipt_amount']) != cents(evidence.get('contribution_receipt_amount')):
        raise ValueError(f'FEC amount conflict for {stored["sub_id"]}')
    if str(stored['contribution_receipt_date'])[:10] != str(evidence.get('contribution_receipt_date'))[:10]:
        raise ValueError(f'FEC date conflict for {stored["sub_id"]}')


def apply_evidence(connection, table, stored, evidence, source):
    validate_identity(stored, evidence)
    changed = {}
    for field in FIELDS:
        if field not in evidence or evidence[field] is None:
            continue
        value = evidence[field]
        if field == 'memoed_subtotal':
            value = int(fec_bool(value))
        if stored.get(field) is None or str(stored[field]) != str(value):
            changed[field] = {'before': stored.get(field), 'after': value}
    # Recompute even when the saved memo metadata itself was already present.
    if 'memo_code' in evidence or 'memoed_subtotal' in evidence:
        memo = int(fec_bool(evidence.get('memo_code', stored.get('memo_code'))) or
                   fec_bool(evidence.get('memoed_subtotal', stored.get('memoed_subtotal'))))
        if stored.get('is_memo_leg') != memo:
            changed['is_memo_leg'] = {'before': stored.get('is_memo_leg'), 'after': memo}
    if changed:
        fields = list(changed)
        connection.execute(f'UPDATE {table} SET ' + ','.join(f'"{f}"=?' for f in fields) + ' WHERE sub_id=?',
                           tuple(changed[f]['after'] for f in fields) + (stored['sub_id'],))
        connection.execute('INSERT INTO receipt_metadata_history(source_id, source_table, evidence_source, changes_json) VALUES(?,?,?,?)',
                           (str(stored['sub_id']), table, source, json.dumps(changed, sort_keys=True)))
    return bool(changed)


def prepare_copy(database, output):
    """Create/validate a private recovery copy without scanning every receipt."""
    database, output = Path(database).resolve(), Path(output).resolve()
    if database == output:
        raise ValueError('Hydration requires a private copy; never overwrite the source')
    receipt = output.with_suffix(output.suffix + '.hydration.json')
    original_sha = source_sha(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if not receipt.exists() or json.loads(receipt.read_text())['source_sha256'] != original_sha:
            raise ValueError('Existing hydration output belongs to a different source')
    else:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(output)) as dest:
            source.backup(dest)
        if source_sha(database) != original_sha:
            raise ValueError('Source changed during snapshot creation')
        atomic(receipt, {'source_sha256': original_sha, 'source': str(database), 'output': str(output), 'status': 'started'})
    with closing(sqlite3.connect(output)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE IF NOT EXISTS receipt_metadata_history(id INTEGER PRIMARY KEY, source_id TEXT, source_table TEXT, evidence_source TEXT, changes_json TEXT)')
        for table in ('contributions', 'corporate_giving'):
            present = {r['name'] for r in connection.execute(f'PRAGMA table_info({table})')}
            for field in FIELDS:
                if field not in present:
                    kind = 'INTEGER' if field in ('file_num', 'report_year', 'memoed_subtotal') else 'TEXT'
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN "{field}" {kind}')
        connection.commit()
    return {'database': database, 'output': output, 'receipt': receipt, 'source_sha256': original_sha}


def hydrate(database, output, company=None, cycle=None, fetch=False, max_requests=100, client=None, source_ids=None):
    prepared = prepare_copy(database, output)
    database, output, receipt = prepared['database'], prepared['output'], prepared['receipt']
    original_sha = prepared['source_sha256']
    updated = requests = 0
    accounted = None
    refusal = None
    with ExitStack() as resources, closing(sqlite3.connect(output)) as connection:
        connection.row_factory = sqlite3.Row
        wanted = {}
        for table, cycle_field in (('contributions', 'two_year_transaction_period'), ('corporate_giving', 'cycle')):
            where = ["company_match_status='accepted'", "(coalesce(filing_form,'')='' OR coalesce(line_number,'')='')"]
            parameters = []
            if company:
                where.append('company=?'); parameters.append(company)
            if cycle:
                where.append(f'{cycle_field}=?'); parameters.append(cycle)
            if source_ids:
                where.append('sub_id IN (' + ','.join('?' for _ in source_ids) + ')')
                parameters.extend(map(str, source_ids))
            for item in connection.execute(f'SELECT * FROM {table} WHERE ' + ' AND '.join(where), parameters):
                wanted.setdefault(str(item['sub_id']), []).append((table, dict(item)))
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'api_page_checkpoints' in tables:
            for (payload,) in connection.execute('SELECT payload_json FROM api_page_checkpoints'):
                page = json.loads(payload)
                for row in page.get('rows', page.get('results', [])):
                    identifier = str(row.get('sub_id'))
                    if identifier in wanted and row.get('filing_form') and row.get('line_number'):
                        for table, stored in wanted.pop(identifier):
                            updated += apply_evidence(connection, table, stored, row, 'saved_fec_api_checkpoint')
            connection.commit()
        if fetch and wanted:
            if client is None:
                from pac_evidence import Client
                from fec_accounted_client import AccountedTransport, FECAdmissionRefused
                cache = output.with_suffix('.fec-evidence')
                accounted = resources.enter_context(AccountedTransport.live(
                    cache / 'original-http' / uuid4().hex, max_requests,
                    known_legacy_paths=(database,)))
                client = Client(cache, key='transport-owned', transport=accounted)
            for identifier, items in list(wanted.items()):
                if requests >= max_requests:
                    break
                if not re.fullmatch(r'\d+', identifier):
                    continue
                stored = items[0][1]
                period = stored.get('two_year_transaction_period') or stored.get('cycle')
                endpoint = f'schedules/schedule_a/{identifier}/'
                try:
                    data = client.page(endpoint, {'two_year_transaction_period': period})
                except ValueError as error:
                    # Only our finite admission refusal is a resumable stop.
                    # Storage/parse/identity failures retain their hard refusal.
                    if accounted is None or not isinstance(error, FECAdmissionRefused):
                        raise
                    refusal = str(error)
                    break
                requests += 1
                rows = data.get('results', [])
                if len(rows) != 1 or str(rows[0].get('sub_id')) != identifier:
                    continue
                evidence = rows[0]
                for table, record in items:
                    updated += apply_evidence(connection, table, record, evidence, 'https://api.open.fec.gov/v1/' + endpoint)
                connection.commit()
                if evidence.get('filing_form') and evidence.get('line_number'):
                    wanted.pop(identifier)
        result = dict(status='complete' if not wanted else 'needs_evidence', source=str(database), output=str(output),
                      source_sha256=original_sha, updated_rows=updated, requests=requests,
                      scope=dict(company=company, cycle=cycle, source_ids=source_ids),
                      unresolved_source_ids=sorted(wanted), unresolved_count=len(wanted),
                      physical_requests=accounted.http_attempts if accounted is not None else None,
                      original_http_archive=str(accounted.archive) if accounted is not None else None,
                      fetch_admission_refusal=refusal)
        connection.commit()
    if source_sha(database) != original_sha:
        raise ValueError('Source changed during metadata recovery')
    result['output_sha256'] = sha(output)
    atomic(receipt, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--company')
    parser.add_argument('--cycle', type=int)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--max-requests', type=int, default=100)
    parser.add_argument('--source-id', action='append', help='Limit recovery to an exact FEC source ID (repeatable)')
    args = parser.parse_args()
    if args.max_requests < 0:
        parser.error('--max-requests must be nonnegative')
    result = hydrate(args.database, args.output, args.company, args.cycle, args.fetch, args.max_requests, source_ids=args.source_id)
    print(json.dumps({k: v for k, v in result.items() if k != 'unresolved_source_ids'}))


if __name__ == '__main__':
    main()
