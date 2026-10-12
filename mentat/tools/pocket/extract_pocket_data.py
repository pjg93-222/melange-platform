"""Build the Mentat Pocket data snapshot from a read-only Mentat console.

Read-only. Talks to the console's contribution endpoints only:
  GET  /api/contributions/session   -> {token, read_only}
  GET  /api/contributions/catalog   -> {datasets:[{id,...}], fields:[...]}
  POST /api/contributions/query     -> grouped selection + honest totals

Python standard library only (urllib, json).

Query shape is the Chart Studio's own request shape, compiled in
chart-studio-contributions.js:compile ->
  {dataset, basis, dimensions, measures, filters:[{field,op,value}], sort, limit, offset}
Basis 'receipt' is the studio default: "Published included receipts".
Amounts come back as INTEGER CENTS (catalog amount_unit == 'cents').

Per-company result sets are obtained by GROUPING on company rather than by
issuing one filtered request per company. That is the same SQL grouping the
filtered form would produce, and it is only used when the server reports the
complete group set (has_more false after paging), so no ranking is inferred
from a truncated page. A directly-filtered spot check per industry proves the
grouped form against an independent request (see --spot-checks).

The integrity block compares, per cycle, the sum of the per-company totals
against the INDUSTRY total fetched by its own separate request. The two sides
come from different queries, so the check can actually fail.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASIS = 'receipt'
# The studio's label for this basis (chart-studio-contributions.js:6). The
# server's data-layer label for the same basis is "Published included receipt
# view" (contribution_data.py:66); the studio string is what the console shows.
BASIS_LABEL = 'Published included receipts'
BASIS_CAVEAT = ('Matches the workbook’s included receipts. Economic uniqueness remains '
                'unresolved; intermediary stages may overlap.')
PAGE = 100000
MAX_ROWS = 2_000_000


class Console:
    def __init__(self, host, port, timeout=1800):
        self.base = 'http://{}:{}'.format(host, port)
        self.host_header = '{}:{}'.format(host, port)
        self.timeout = timeout
        self.requests = 0
        self.polls = 0
        self.token = None

    def _get(self, path):
        self.requests += 1
        request = urllib.request.Request(self.base + path, headers={'Host': self.host_header})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode('utf-8'))

    def _post(self, path, payload):
        self.requests += 1
        body = json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(self.base + path, data=body, method='POST', headers={
            'Host': self.host_header, 'Content-Type': 'application/json',
            'X-Mentat-Token': self.token})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf-8', 'replace')
            raise SystemExit('HTTP {} from {}: {}\nrequest: {}'.format(
                error.code, path, detail, json.dumps(payload)))

    def open(self):
        session = self._get('/api/contributions/session')
        self.token = session['token']
        return session

    def catalog(self):
        return self._get('/api/contributions/catalog')

    def query(self, dataset, dimensions, measures, filters=None, sort=None, limit=PAGE, offset=0):
        """One page. Handles the async 'preparing' -> 'ready' poll the studio uses."""
        payload = {'dataset': dataset, 'basis': BASIS, 'dimensions': list(dimensions),
                   'measures': list(measures), 'filters': list(filters or []),
                   'sort': list(sort or [{'field': 'amount', 'direction': 'desc'}]),
                   'limit': limit, 'offset': offset}
        deadline = time.time() + 7200
        while True:
            result = self._post('/api/contributions/query', payload)
            if result.get('status') == 'ready':
                return result
            wait = max(0.25, float(result.get('retry_after_ms', 1500)) / 1000.0)
            self.polls += 1
            if time.time() > deadline:
                raise SystemExit('Timed out preparing {}: {}'.format(dataset, result.get('message')))
            print('      preparing {} ({} records)...'.format(
                dataset, result.get('processed_records')), flush=True)
            time.sleep(wait)

    def pages(self, dataset, dimensions, measures, filters=None, sort=None):
        """Every group, paged. Returns (rows, totals, group_count, complete)."""
        rows, offset, totals, count = [], 0, None, None
        while True:
            result = self.query(dataset, dimensions, measures, filters, sort, PAGE, offset)
            if totals is None:
                totals, count = result['totals'], result['group_count']
            rows.extend(result['rows'])
            if not result.get('has_more') or len(rows) >= MAX_ROWS:
                return rows, totals, count, not result.get('has_more')
            offset += len(result['rows'])


def cycle_label(value):
    """'2021-2022' -> '2021-22'."""
    text = str(value)
    if len(text) == 9 and text[4] == '-':
        return text[:4] + '-' + text[7:]
    return text


class Table:
    """Interns strings so the snapshot stores small integer references."""

    def __init__(self):
        self.values, self.index = [], {}

    def id(self, value):
        text = '' if value is None else str(value)
        if text not in self.index:
            self.index[text] = len(self.values)
            self.values.append(text)
        return self.index[text]


def top_rows(rows, key, table, limit=8):
    """Top `limit` rows by amount desc, as [nameId, amountCents, records]."""
    ordered = sorted(rows, key=lambda r: (-(r['amount'] or 0), str(r[key])))[:limit]
    return [[table.id(r[key]), r['amount'] or 0, r['records'] or 0] for r in ordered]


def scope_block(totals_rows, category_rows, party_rows, all_recipients, cycle_recipients,
                cycles, cat_table, party_table, recipient_table):
    """One scope's four result sets, keyed by cycle index."""
    order = {value: position for position, value in enumerate(cycles)}
    block = {
        't': sorted([[order[r['cycle']], r['amount'] or 0, r['records'] or 0]
                     for r in totals_rows if r['cycle'] in order]),
        'k': sorted([[order[r['cycle']], cat_table.id(r['recipient_category']),
                      r['amount'] or 0, r['records'] or 0]
                     for r in category_rows if r['cycle'] in order]),
        'p': sorted([[order[r['cycle']], party_table.id(r['party']),
                      r['amount'] or 0, r['records'] or 0]
                     for r in party_rows if r['cycle'] in order]),
        'r': {'a': top_rows(all_recipients, 'recipient', recipient_table)},
    }
    for value, position in order.items():
        block['r'][str(position)] = top_rows(cycle_recipients.get(value, []),
                                             'recipient', recipient_table)
    return block


def compact_gaps(gaps):
    """The catalogue's `collection_gaps` for one dataset, as the snapshot keeps it, or None.

    The console parses the report's own Data Quality check (collection_gaps.py): the company x
    cycle cells whose stored rows are materially fewer than FEC reported, and how many more short
    cells the check did not itemize. Only a failed check is carried; a console that predates the
    field, or a passing check, adds no block. Nothing is recounted here: the cells are copied as
    the console states them, [company, cycle, destination, held, reported, percent], and the
    page words them (mentat-pocket.html gapNotice, held to coverage-view.js by a test).
    """
    if not isinstance(gaps, dict) or not gaps.get('failed'):
        return None
    return {'f': 1, 'u': int(gaps.get('unitemized') or 0), 's': str(gaps.get('source') or ''),
            'c': [[c['company'], c['cycle'], c['destination'], c['held'], c['reported'], c['percent']]
                  for c in gaps.get('cells') or []]}


def group(rows, key):
    out = {}
    for row in rows:
        out.setdefault(row[key], []).append(row)
    return out


def industry_snapshot(console, dataset, spot_checks, log):
    name = dataset['id']
    log('  [{}] industry scope'.format(name))
    # --- industry scope: five independent requests -------------------------
    totals_rows, totals, _, _ = console.pages(
        name, ['cycle'], ['amount', 'records'], sort=[{'field': 'cycle', 'direction': 'asc'}])
    cycles = [r['cycle'] for r in sorted(totals_rows, key=lambda r: str(r['cycle']))]
    cat_rows, _, _, cat_ok = console.pages(name, ['cycle', 'recipient_category'], ['amount', 'records'])
    party_rows, _, _, party_ok = console.pages(name, ['cycle', 'party'], ['amount', 'records'])
    cycle_recipient_rows, _, cycle_recipient_count, cycle_recipient_ok = console.pages(
        name, ['cycle', 'recipient'], ['amount', 'records'])
    industry_top = console.query(name, ['recipient'], ['amount', 'records'], limit=8)['rows']

    # --- company scopes: grouped on company, complete group sets -----------
    log('  [{}] company scopes'.format(name))
    company_rows, _, company_count, company_ok = console.pages(name, ['company'], ['amount', 'records'])
    co_totals, _, _, co_totals_ok = console.pages(name, ['company', 'cycle'], ['amount', 'records'])
    co_cats, _, _, co_cats_ok = console.pages(name, ['company', 'cycle', 'recipient_category'], ['amount', 'records'])
    co_party, _, _, co_party_ok = console.pages(name, ['company', 'cycle', 'party'], ['amount', 'records'])
    co_cycle_recipients, _, co_cr_count, co_cr_ok = console.pages(
        name, ['company', 'cycle', 'recipient'], ['amount', 'records'])
    co_recipients, _, co_r_count, co_r_ok = console.pages(
        name, ['company', 'recipient'], ['amount', 'records'])

    complete = all([cat_ok, party_ok, cycle_recipient_ok, company_ok, co_totals_ok,
                    co_cats_ok, co_party_ok, co_cr_ok, co_r_ok])

    cat_table, party_table, recipient_table, company_table = Table(), Table(), Table(), Table()
    companies = [r['company'] for r in sorted(company_rows, key=lambda r: (-(r['amount'] or 0), str(r['company'])))]
    for company in companies:
        company_table.id(company)

    industry_block = scope_block(
        totals_rows, cat_rows, party_rows, industry_top,
        group(cycle_recipient_rows, 'cycle'), cycles,
        cat_table, party_table, recipient_table)

    by_company = {
        't': group(co_totals, 'company'), 'k': group(co_cats, 'company'),
        'p': group(co_party, 'company'), 'cr': group(co_cycle_recipients, 'company'),
        'r': group(co_recipients, 'company')}
    company_blocks = []
    for company in companies:
        company_blocks.append(scope_block(
            by_company['t'].get(company, []), by_company['k'].get(company, []),
            by_company['p'].get(company, []), by_company['r'].get(company, []),
            group(by_company['cr'].get(company, []), 'cycle'), cycles,
            cat_table, party_table, recipient_table))

    # --- integrity: two independent sources, compared to the cent ----------
    industry_by_cycle = {r['cycle']: (r['amount'] or 0, r['records'] or 0) for r in totals_rows}
    summed = {}
    for row in co_totals:
        amount, records = summed.get(row['cycle'], (0, 0))
        summed[row['cycle']] = (amount + (row['amount'] or 0), records + (row['records'] or 0))
    integrity_cycles = []
    for cycle in cycles:
        industry_amount, industry_records = industry_by_cycle.get(cycle, (0, 0))
        company_amount, company_records = summed.get(cycle, (0, 0))
        integrity_cycles.append({
            'cycle': cycle,
            'industry_amount_cents': industry_amount, 'company_sum_amount_cents': company_amount,
            'amount_delta_cents': industry_amount - company_amount,
            'industry_records': industry_records, 'company_sum_records': company_records,
            'records_delta': industry_records - company_records})

    # --- spot check: one directly-filtered request per industry ------------
    spot = None
    if spot_checks and companies:
        target = companies[0]
        filtered = console.pages(name, ['cycle'], ['amount', 'records'],
                                 filters=[{'field': 'company', 'op': 'eq', 'value': target}],
                                 sort=[{'field': 'cycle', 'direction': 'asc'}])[0]
        direct = {r['cycle']: (r['amount'] or 0, r['records'] or 0) for r in filtered}
        grouped = {r['cycle']: (r['amount'] or 0, r['records'] or 0)
                   for r in by_company['t'].get(target, [])}
        spot = {'company': target, 'filtered_request': direct, 'grouped_request': grouped,
                'match': direct == grouped}

    snapshot = {
        'n': name,
        'cyc': [cycle_label(c) for c in cycles],
        'cyc_raw': list(cycles),
        'cat': cat_table.values, 'par': party_table.values,
        'rec': recipient_table.values, 'co': company_table.values,
        'S': industry_block, 'C': company_blocks,
        'tot': [totals.get('amount', 0), totals.get('records', 0)]}
    gaps = compact_gaps(dataset.get('collection_gaps'))
    if gaps:
        snapshot['gp'] = gaps
    audit = {
        'industry': name, 'companies': len(companies), 'cycles': list(cycles),
        'complete_group_sets': complete,
        'group_counts': {'company': company_count, 'cycle_recipient': cycle_recipient_count,
                         'company_cycle_recipient': co_cr_count, 'company_recipient': co_r_count},
        'per_cycle': integrity_cycles,
        'all_cycle_industry_amount_cents': totals.get('amount', 0),
        'all_cycle_company_sum_amount_cents': sum(a for a, _ in summed.values()),
        'spot_check': spot,
        'distinct_recipients_in_snapshot': len(recipient_table.values)}
    return snapshot, audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8814)
    parser.add_argument('--out-dir', default='',
                        help='Folder for pocket-data.json and pocket-integrity.json')
    parser.add_argument('--out', default='')
    parser.add_argument('--audit', default='')
    parser.add_argument('--datasets', default='', help='Comma-separated catalog ids; default all')
    parser.add_argument('--no-spot-checks', action='store_true')
    options = parser.parse_args()
    # --out and --audit still win when given; --out-dir only moves their
    # defaults, so a caller can redirect both by naming one folder.
    directory = Path(options.out_dir) if options.out_dir else Path(__file__).parent
    directory.mkdir(parents=True, exist_ok=True)
    options.out = options.out or str(directory / 'pocket-data.json')
    options.audit = options.audit or str(directory / 'pocket-integrity.json')

    started = time.time()
    log = lambda message: print(message, flush=True)
    console = Console(options.host, options.port)
    session = console.open()
    log('session: read_only={}'.format(session.get('read_only')))
    catalog = console.catalog()
    if catalog.get('amount_unit') != 'cents':
        raise SystemExit('Unexpected amount unit: ' + str(catalog.get('amount_unit')))
    field_ids = {f['id'] for f in catalog['fields']}
    required = {'cycle', 'recipient_category', 'party', 'recipient', 'company', 'amount', 'records'}
    missing = sorted(required - field_ids)
    if missing:
        raise SystemExit('Catalog is missing expected field ids: ' + ', '.join(missing))
    counts = [f for f in catalog['fields'] if f['id'] == 'records']
    log('records field: label={!r} unit={!r} role={!r}'.format(
        counts[0]['label'], counts[0]['unit'], counts[0]['role']))

    wanted = [d.strip() for d in options.datasets.split(',') if d.strip()]
    datasets = [d for d in catalog['datasets'] if not wanted or d['id'] in wanted]
    unavailable = [d['id'] for d in datasets if not d.get('available')]
    if unavailable:
        raise SystemExit('Unavailable datasets: ' + ', '.join(unavailable))

    industries, audits = [], []
    for dataset in datasets:
        snapshot, audit = industry_snapshot(console, dataset, not options.no_spot_checks, log)
        industries.append(snapshot)
        audits.append(audit)
        log('  [{}] {} companies, {} recipients interned, complete={}'.format(
            dataset['id'], audit['companies'], audit['distinct_recipients_in_snapshot'],
            audit['complete_group_sets']))

    provenance = {d['id']: d.get('provenance', {}) for d in datasets}
    payload = {'g': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
               'b': BASIS_LABEL, 'bc': BASIS_CAVEAT, 'u': 'cents', 'ind': industries}
    Path(options.out).write_text(json.dumps(payload, separators=(',', ':'), ensure_ascii=False),
                                 encoding='utf-8')
    elapsed = time.time() - started
    Path(options.audit).write_text(json.dumps({
        'generated_at': payload['g'], 'basis': BASIS, 'basis_label': BASIS_LABEL,
        'requests': console.requests, 'polls': console.polls,
        'elapsed_seconds': round(elapsed, 1),
        'snapshot_bytes': Path(options.out).stat().st_size,
        'provenance': provenance, 'industries': audits},
        indent=2, ensure_ascii=False), encoding='utf-8')
    log('wrote {} ({} bytes) in {:.1f}s over {} requests ({} polls)'.format(
        options.out, Path(options.out).stat().st_size, elapsed, console.requests, console.polls))


if __name__ == '__main__':
    main()
