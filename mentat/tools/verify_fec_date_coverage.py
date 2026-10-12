"""Preserve dated, fully paginated FEC queries and compare them to a saved run.

Proves only the exact roster search scope as observed now. Later filings and
unlisted aliases remain outside this claim. Never edits the collected database.
"""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from api_company_cycle_fanout import discover_keys
from api_request_pool import ENDPOINT, RequestPool
from validate_industry_coverage import validate, parse_cycles, atomic_write_json


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def financial(row):
    return (str(row['sub_id']), str(row.get('committee_id') or ''),
            str(row.get('contributor_name') or '').strip().upper(),
            str(row.get('contribution_receipt_date') or '')[:10],
            str(Decimal(str(row['contribution_receipt_amount'])).normalize()))


def verify(database, fanout, industry, cycles, output, budget):
    output.mkdir(parents=True, exist_ok=False)
    before = digest(database)
    base = validate(database, industry, cycles, fanout)
    if base['status'] != 'succeeded':
        raise ValueError('Original collection coverage checks must pass before dated verification.')
    queries = []
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute('SELECT * FROM api_work_progress ORDER BY unit_id'):
            if row['company'] not in base['companies'] or row['cycle'] not in cycles:
                continue
            if '::window::' in row['unit_id'] or '::decomposed::' in row['unit_id']:
                continue
            query = json.loads(row['query_contract'])['request']
            start, end = date(row['cycle'] - 1, 1, 1), min(date(row['cycle'], 12, 31), datetime.now(timezone.utc).date())
            params = {**query['params'], 'min_date': start.isoformat(), 'max_date': end.isoformat(), 'per_page': 100}
            queries.append({'unit': dict(row), 'spec': {'url': ENDPOINT, 'params': params}, 'start': start, 'end': end})
        if not queries:
            raise ValueError('No eligible original FEC query contracts were found for dated verification.')
        keys = discover_keys(None, dict(os.environ))
        pool = RequestPool(keys, budget, min(2, len(keys)))
        evidence, observations = [], []
        try:
            first = []
            for offset in range(0, len(queries), 128):
                first.extend(pool.batch(
                    [q['spec'] for q in queries[offset:offset + 128]],
                    'dated-coverage',
                ))
            if len(first) != len(queries):
                raise ValueError('Shared FEC request pool returned an incomplete dated batch.')
            for index, (query, response) in enumerate(zip(queries, first)):
                rows, pages, seen_cursors = {}, [], set()
                expected_count = None
                for page in range(1, 201):
                    if response.get('status') != 200:
                        raise ValueError(f'FEC dated query returned HTTP {response.get("status")}.')
                    body = json.loads(response['body'])
                    observed = datetime.now(timezone.utc).isoformat()
                    raw_path = output / f'query-{index + 1}-page-{page}.json'
                    raw_path.write_text(response['body'], encoding='utf-8')
                    pages.append({'file': raw_path.name, 'sha256': digest(raw_path), 'observed_at': observed,
                                  'http_date': response['headers'].get('Date', response['headers'].get('date'))})
                    pagination = body['pagination']
                    count = pagination['count']
                    if (pagination.get('is_count_exact') is not True
                            or isinstance(count, bool) or not isinstance(count, int)
                            or count < 0):
                        raise ValueError('FEC did not provide an exact dated result count.')
                    if expected_count is None:
                        expected_count = count
                    elif count != expected_count:
                        raise ValueError('FEC dated result count changed during pagination.')
                    for record in body['results']:
                        sid = str(record['sub_id'])
                        if sid in rows:
                            raise ValueError('FEC returned duplicate source IDs across dated pages.')
                        day = date.fromisoformat(str(record['contribution_receipt_date'])[:10])
                        if not query['start'] <= day <= query['end']:
                            raise ValueError('FEC returned a transaction outside the requested dates.')
                        rows[sid] = financial(record)
                    if len(rows) == count:
                        break
                    cursor = pagination.get('last_indexes')
                    if cursor and (not isinstance(cursor, dict) or
                                   set(cursor) - {'last_index', 'last_contribution_receipt_date'}):
                        raise ValueError('FEC returned an invalid dated pagination cursor.')
                    token = json.dumps(cursor, sort_keys=True)
                    if not cursor or token in seen_cursors or not body['results'] or len(rows) > count:
                        raise ValueError('FEC pagination did not establish complete dated coverage.')
                    seen_cursors.add(token)
                    query['spec']['params'].update(cursor)
                    response = pool.batch([query['spec']], 'dated-coverage')[0]
                else:
                    raise ValueError('Dated verification exceeded its page limit.')
                unit = query['unit']
                table = 'contributions' if unit['kind'] == 'employer' else 'corporate_giving'
                # Union across literal aliases is checked below, not against one
                # alias at a time (a company may have several accepted aliases).
                observations.append({'company': unit['company'], 'cycle': unit['cycle'], 'kind': unit['kind'],
                                     'rows': rows, 'table': table, 'start': query['start'], 'end': query['end']})
                details = {'query': json.loads(unit['query_contract'])['request'],
                           'date_bounds': [query['start'].isoformat(), query['end'].isoformat()],
                           'pages': pages, 'rows': len(rows), 'api_count': count,
                           'database_sha256': before, 'scope': 'Exact configured search; not all nationwide giving.'}
                detail_path = output / f'query-{index + 1}-evidence.json'
                atomic_write_json(detail_path, details)
                evidence.append({'industry': industry, 'company': unit['company'], 'cycle': unit['cycle'], 'kind': unit['kind'],
                                 'coverage_start': query['start'].isoformat(), 'verified_data_through': query['end'].isoformat(),
                                 'verification_evidence': str(detail_path.resolve()) + '#sha256=' + digest(detail_path)})
            for company, cycle, kind in {(r['company'], r['cycle'], r['kind']) for r in observations}:
                group = [r for r in observations if (r['company'], r['cycle'], r['kind']) == (company, cycle, kind)]
                union = {}
                for item in group:
                    for sid, value in item['rows'].items():
                        if sid in union and union[sid] != value:
                            raise ValueError('FEC aliases returned conflicting financial observations.')
                        union[sid] = value
                g = group[0]
                actual = {str(r['sub_id']): financial(dict(r)) for r in db.execute(
                    'SELECT * FROM ' + g['table'] + ' WHERE industry=? AND company=? AND substr(contribution_receipt_date,1,10)>=? AND substr(contribution_receipt_date,1,10)<=?',
                    (industry, company, g['start'].isoformat(), g['end'].isoformat()))}
                if union != actual:
                    raise ValueError(f'Dated FEC results differ from preserved {company} / {kind}; recollection required.')
        finally:
            telemetry = pool.snapshot()
            pool.close()
    if digest(database) != before:
        raise ValueError('Source database changed during dated verification.')
    receipt = json.loads(fanout.read_text(encoding='utf-8-sig'))
    receipt.update(fec_date_evidence=evidence, dated_verification_of={'path': str(fanout.resolve()), 'sha256': digest(fanout)})
    enriched = output / 'fanout-with-dated-evidence.json'
    atomic_write_json(enriched, receipt)
    result = validate(database, industry, cycles, enriched)
    result['dated_verification'] = {'request_pool': telemetry, 'source_unchanged': True, 'evidence_directory': str(output.resolve())}
    atomic_write_json(output / 'coverage-receipt.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('database', 'fanout', 'output', 'budget'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--industry', required=True)
    parser.add_argument('--cycles', required=True)
    args = parser.parse_args()
    result = verify(args.database, args.fanout, args.industry, parse_cycles(args.cycles), args.output, args.budget)
    print(json.dumps({'status': result['status'], 'fec_coverage': result['fec_coverage'], 'dated_verification': result['dated_verification']}, indent=2))
