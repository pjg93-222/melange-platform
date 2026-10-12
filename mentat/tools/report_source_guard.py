"""Bind report publication to accepted FEC facts and explain every dollar change.

Snapshots and ledgers are append-only. The current pointer advances only after
successful publication. No source database is modified and no missing row is
silently explained away as a duplicate.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_sha(path):
    wal = Path(str(path) + '-wal')
    def check_wal():
        if wal.exists() and wal.stat().st_size:
            raise ValueError('Use a quiescent source snapshot, not an active WAL database')
    check_wal()
    result = sha(path)
    check_wal()
    return result


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2), encoding='utf-8')
    # Windows indexers/AV can briefly hold a reader on an otherwise closed
    # receipt. Retry the atomic replace, never fall back to a partial write.
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if os.name != 'nt' or attempt == 7:
                raise
            time.sleep(min(0.5, 0.025 * 2 ** attempt))


def cents(value):
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal('1')))


def cycle_of(value):
    match = re.match(r'^(\d{4})-\d\d-\d\d', str(value or ''))
    if not match:
        raise ValueError('A source receipt has no valid ISO date')
    year = int(match[1])
    return year + year % 2


def location(state_dir, industry, cycles):
    slug = re.sub('[^a-z0-9]+', '_', industry.lower()).strip('_')
    if not slug:
        raise ValueError('Industry is required')
    return Path(state_dir) / slug / '-'.join(map(str, sorted(cycles)))


def pointer(directory):
    path = Path(directory) / 'current.json'
    if not path.exists():
        return None, None
    value = json.loads(path.read_text(encoding='utf-8'))
    if sha(value['snapshot']) != value['snapshot_sha256']:
        raise ValueError('Accepted source snapshot hash does not match')
    return value, sha(path)


def accepted_scopes(state_dir, industry, cycles):
    """Select the newest accepted source for each overlapping election cycle.

    Widening or splitting a report cannot discard a protected cycle. Each cycle
    has one authority, so historical superseded decisions are never added twice.
    """
    base = location(state_dir, industry, cycles).parent
    remaining = set(cycles)
    selected = []
    paths = sorted(base.glob('*/current.json'), key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)
    for path in paths:
        saved, token = pointer(path.parent)
        overlap = remaining.intersection(saved['cycles'])
        if not overlap:
            continue
        if saved.get('industry', industry) != industry:
            raise ValueError('Accepted baseline industry differs from the requested scope')
        selected.append({'directory':str(path.parent.resolve()), 'token':token,
                         'cycles':sorted(overlap), 'snapshot':saved['snapshot'],
                         'snapshot_sha256':saved['snapshot_sha256']})
        remaining.difference_update(overlap)
    return selected


def accepted_facts(scopes):
    for scope in scopes:
        with closing(sqlite3.connect(Path(scope['snapshot']).resolve().as_uri()+'?mode=ro', uri=True)) as db:
            for identifier, raw, analysis in db.execute('SELECT id,raw,analysis FROM facts'):
                if json.loads(raw)['cycle'] in scope['cycles']:
                    yield identifier, raw, analysis


def source_facts(database, industry, cycles):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        for table in ('contributions', 'corporate_giving'):
            for item in connection.execute(f'SELECT * FROM {table} WHERE industry=? ORDER BY sub_id', (industry,)):
                row = dict(item)
                cycle = cycle_of(row['contribution_receipt_date'])
                if cycle not in cycles:
                    continue
                # Company classification may change, but financial source facts
                # attached to a FEC sub-ID must not silently change.
                facts = {k: row.get(k) or '' for k in ('sub_id', 'committee_id', 'contributor_name')}
                facts.update(date=str(row['contribution_receipt_date'])[:10],
                             amount_cents=cents(row['contribution_receipt_amount']), cycle=cycle)
                yield str(row['sub_id']), json.dumps(facts, sort_keys=True)


def prepare(database, industry, cycles, state_dir, receipt):
    directory = location(state_dir, industry, cycles)
    directory.mkdir(parents=True, exist_ok=True)
    _, previous_token = pointer(directory)
    scopes = accepted_scopes(state_dir, industry, cycles)
    previous = None
    if scopes:
        baseline = directory / (uuid.uuid4().hex + '.baseline.db')
        with closing(sqlite3.connect(baseline)) as db:
            db.execute('CREATE TABLE facts (id TEXT PRIMARY KEY, raw TEXT NOT NULL, analysis TEXT)')
            db.executemany('INSERT INTO facts VALUES(?,?,?)', accepted_facts(scopes))
            db.commit()
        previous = {'snapshot':str(baseline.resolve()), 'snapshot_sha256':sha(baseline)}
    database = Path(database).resolve()
    before = source_sha(database)
    snapshot = directory / (uuid.uuid4().hex + '.db')
    ledger = snapshot.with_suffix('.source-changes.jsonl')
    counts = {'added': 0, 'missing': 0, 'changed': 0, 'unchanged': 0}
    with closing(sqlite3.connect(snapshot)) as connection, ledger.open('w', encoding='utf-8') as output:
        connection.execute('CREATE TABLE facts (id TEXT PRIMARY KEY, raw TEXT NOT NULL, analysis TEXT)')
        for identifier, facts in source_facts(database, industry, cycles):
            prior = connection.execute('SELECT raw FROM facts WHERE id=?', (identifier,)).fetchone()
            if prior and prior[0] != facts:
                raise ValueError(f'Conflicting copies of FEC source ID {identifier}')
            connection.execute('INSERT OR IGNORE INTO facts VALUES(?,?,NULL)', (identifier, facts))
        if previous:
            connection.execute('ATTACH DATABASE ? AS accepted', (previous['snapshot'],))
            cycle_sql = ','.join(str(int(cycle)) for cycle in cycles)
            query = f'''SELECT a.id, a.raw, b.raw FROM facts a LEFT JOIN accepted.facts b ON a.id=b.id
                       UNION ALL SELECT b.id,NULL,b.raw FROM accepted.facts b LEFT JOIN facts a ON a.id=b.id
                       WHERE a.id IS NULL AND json_extract(b.raw,'$.cycle') IN ({cycle_sql})'''
        else:
            query = 'SELECT id,raw,NULL FROM facts'
        for identifier, current, old in connection.execute(query):
            kind = 'added' if old is None else 'missing' if current is None else 'changed' if current != old else 'unchanged'
            counts[kind] += 1
            if kind != 'unchanged':
                output.write(json.dumps({'source_id': identifier, 'change': kind,
                    'before': json.loads(old) if old else None, 'after': json.loads(current) if current else None}) + '\n')
        connection.commit()
    after = source_sha(database)
    passed = before == after and counts['missing'] == counts['changed'] == 0
    result = dict(version=1, status='source_verified' if passed else 'blocked', directory=str(directory.resolve()),
                  industry=industry, cycles=list(cycles), database=str(database), database_sha256=before,
                  previous_token=previous_token,
                  baseline_directory=str(directory.resolve()), baseline_token=previous_token,
                  baseline_scopes=scopes, state_root=str(Path(state_dir).resolve()),
                  previous={k: previous[k] for k in ('snapshot', 'snapshot_sha256')} if previous else None,
                  snapshot=str(snapshot.resolve()),
                  source_ledger=str(ledger.resolve()), counts=counts)
    atomic(receipt, result)
    if not passed:
        raise ValueError(f'Source regression or source mutation; see {ledger}. Recover missing records or reconcile changed FEC facts before publication.')
    return result


def unchanged_state(state):
    _, token = pointer(state['directory'])
    if token != state['previous_token']:
        raise ValueError('Accepted report changed during this build; compare again before publishing')
    if 'baseline_scopes' in state:
        if accepted_scopes(state['state_root'], state['industry'], state['cycles']) != state['baseline_scopes']:
            raise ValueError('Accepted baseline changed during this build; compare again before publishing')
        if state.get('previous') and sha(state['previous']['snapshot']) != state['previous']['snapshot_sha256']:
            raise ValueError('Accepted baseline snapshot changed during this build')
    if state.get('baseline_directory') != state['directory']:
        _, baseline_token = pointer(state['baseline_directory'])
        if baseline_token != state['baseline_token']:
            raise ValueError('Accepted baseline changed during this build; compare again before publishing')


def check_payload(receipt, payload_path):
    state = json.loads(Path(receipt).read_text(encoding='utf-8'))
    if state['status'] not in ('source_verified', 'verified'):
        raise ValueError('Source gate has not passed')
    unchanged_state(state)
    if source_sha(state['database']) != state['database_sha256']:
        raise ValueError('Source database changed since its comparison')
    payload = json.loads(Path(payload_path).read_text(encoding='utf-8-sig'))
    if payload['metadata']['industry'] != state['industry']:
        raise ValueError('Payload industry disagrees with source gate')
    ledger = Path(state['snapshot']).with_suffix('.dollar-changes.jsonl')
    delta = changed = unexplained = 0
    with closing(sqlite3.connect(state['snapshot'])) as connection, ledger.open('w', encoding='utf-8') as output:
        connection.execute('UPDATE facts SET analysis=NULL')
        seen = set()
        for row in payload['raw_rows']:
            if int(row['cycle']) not in state['cycles']:
                continue
            identifier = str(row['source_id'])
            if identifier in seen:
                raise ValueError(f'Duplicate payload source ID {identifier}')
            seen.add(identifier)
            raw = connection.execute('SELECT raw FROM facts WHERE id=?', (identifier,)).fetchone()
            if not raw or cents(row['amount']) != json.loads(raw[0])['amount_cents']:
                raise ValueError(f'Payload amount or ID disagrees with source: {identifier}')
            if int(row['cycle']) != json.loads(raw[0])['cycle']:
                raise ValueError(f'Payload cycle disagrees with source: {identifier}')
            source = json.loads(raw[0])
            for payload_field, source_field in (('committee_id', 'committee_id'),
                                                 ('contributor', 'contributor_name'), ('date', 'date')):
                if payload_field not in row or str(row.get(payload_field) or '').strip() != str(source[source_field]).strip():
                    raise ValueError(f'Payload financial identity disagrees with source ({payload_field}): {identifier}')
            if row.get('include_in_analysis') not in ('Yes', 'No'):
                raise ValueError(f'Invalid inclusion decision; do not convert an unknown decision to zero: {identifier}')
            # This guard also runs for saved payloads. The R classifier owns
            # form/line interpretation. Missing evidence remains provisional;
            # only established non-giving or contradictory roles refuse it.
            if row['include_in_analysis'] == 'Yes' and row.get('receipt_role') in (
                'other_receipt', 'asset_liquidation', 'disclosure_memo', 'unverified_contradicted_receipt'
            ):
                raise ValueError(f'Receipt evidence is contradictory or ineligible for counted source: {identifier}')
            memo_exclusion = (str(row.get('duplicate_memo', '')).lower() in ('1', 'true') or
                              row.get('duplicate_of_sub_id') or row.get('duplicate_reason') or
                              str(row.get('inclusion_reason') or '').startswith('Memo duplicate'))
            if memo_exclusion and (row['include_in_analysis'] != 'No' or
                                   not row.get('duplicate_of_sub_id') or not row.get('duplicate_reason') or
                                   str(row['duplicate_of_sub_id']) == identifier):
                raise ValueError(f'Memo exclusion requires a distinct retained source and evidence rule: {identifier}')
            item = {key: row.get(key, '') for key in ('company', 'employee_tier', 'party', 'recipient_category',
                'funding_source_type', 'funding_direction',
                'company_match_reason', 'category_source', 'party_source', 'identity_basis',
                'filing_form', 'line_number', 'memo_text', 'source_url',
                'include_in_analysis', 'inclusion_reason', 'receipt_role', 'receipt_role_unverified',
                'receipt_role_reason', 'company_attribution_status', 'company_attribution_reason', 'duplicate_memo',
                'duplicate_of_sub_id', 'duplicate_reason')}
            item['counted_cents'] = cents(row['amount']) if row['include_in_analysis'] == 'Yes' else 0
            if row['include_in_analysis'] == 'Yes' and (row.get('receipt_role') not in
                    ('contribution', 'in_kind_contribution', 'transfer_receipt') or
                    not row.get('filing_form') or not row.get('line_number')):
                item['receipt_role_unverified'] = 1
                item['receipt_role_reason'] = row.get('receipt_role_reason') or 'receipt_metadata_unresolved'
            connection.execute('UPDATE facts SET analysis=? WHERE id=?', (json.dumps(item, sort_keys=True), identifier))
        # A query match status cannot supply an omitted row's zero-dollar
        # decision. Every scoped source now requires a payload decision.
        if connection.execute('SELECT count(*) FROM facts WHERE analysis IS NULL').fetchone()[0]:
            raise ValueError('Payload omitted scoped raw source rows, including excluded evidence')
        for identifier, analysis in connection.execute('SELECT id,analysis FROM facts'):
            item = json.loads(analysis)
            parent_id = item.get('duplicate_of_sub_id')
            if parent_id:
                parent = connection.execute('SELECT analysis FROM facts WHERE id=?', (str(parent_id),)).fetchone()
                if not item.get('duplicate_reason') or not parent or json.loads(parent[0])['include_in_analysis'] != 'Yes':
                    raise ValueError(f'Memo exclusion has no counted counterpart: {identifier}')
        if state['previous']:
            connection.execute('ATTACH DATABASE ? AS accepted', (state['previous']['snapshot'],))
            query = 'SELECT a.id,a.analysis,b.analysis FROM facts a LEFT JOIN accepted.facts b ON a.id=b.id'
        else:
            query = 'SELECT id,analysis,NULL FROM facts'
        for identifier, current, old in connection.execute(query):
            new = json.loads(current)
            prior = json.loads(old) if old else None
            difference = new['counted_cents'] - (prior['counted_cents'] if prior else 0)
            if current != old:
                changed += 1
                reason = new['inclusion_reason']
                # An initial source ID is explained by its source-addition
                # ledger. Only a changed prior decision needs a change reason;
                # historical payloads may predate inclusion-reason columns.
                invalid = prior is not None and difference != 0 and (not reason or (new['include_in_analysis'] != 'Yes' and
                    reason in ('Included', 'Company match: accepted')))
                unexplained += int(invalid)
                explanation = ('New source record' if prior is None else
                    'Classification or evidence changed; see before/after source fields' if difference == 0 else reason)
                output.write(json.dumps({'source_id': identifier, 'before': prior, 'after': new,
                    'delta_cents': difference, 'explanation': explanation}) + '\n')
            delta += difference
        connection.commit()
    if source_sha(state['database']) != state['database_sha256']:
        raise ValueError('Source database changed during dollar reconciliation')
    state.update(status='verified' if not unexplained else 'blocked', payload=str(Path(payload_path).resolve()),
                 payload_sha256=sha(payload_path), dollar_ledger=str(ledger.resolve()), delta_cents=delta,
                 changed_decisions=changed, unexplained_changes=unexplained, snapshot_sha256=sha(state['snapshot']))
    atomic(receipt, state)
    if unexplained:
        raise ValueError('Unexplained changes in counted dollars; publication refused')
    return state


def accept(receipt):
    state = json.loads(Path(receipt).read_text(encoding='utf-8'))
    if state['status'] != 'verified':
        raise ValueError('Source and dollar checks must pass before acceptance')
    unchanged_state(state)
    if source_sha(state['database']) != state['database_sha256']:
        raise ValueError('Source database changed before acceptance')
    if sha(state['snapshot']) != state['snapshot_sha256'] or sha(state['payload']) != state['payload_sha256']:
        raise ValueError('Verified evidence changed before acceptance')
    atomic(Path(state['directory']) / 'current.json', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check', 'accept'))
    parser.add_argument('--database', type=Path)
    parser.add_argument('--industry')
    parser.add_argument('--cycles', default='2022,2024,2026')
    parser.add_argument('--state-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'state/accepted-report-sources')
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--payload', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            if not args.database or not args.industry:
                parser.error('prepare requires --database and --industry')
            result = prepare(args.database, args.industry, tuple(map(int, args.cycles.split(','))), args.state_dir, args.receipt)
        elif args.action == 'check':
            if not args.payload:
                parser.error('check requires --payload')
            result = check_payload(args.receipt, args.payload)
        else:
            result = accept(args.receipt)
        print(json.dumps({key: result.get(key) for key in ('status', 'counts', 'delta_cents', 'source_ledger', 'dollar_ledger')}))
    except (ValueError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
