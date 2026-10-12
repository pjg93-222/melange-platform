"""Offline FEC coverage evidence, independent of file and collection timestamps.

A dated assertion must cover each company's employer and corporate searches.
Assertions come from fanout ``fec_date_evidence`` or the optional SQLite table
``fec_date_evidence`` (same columns). They describe source coverage, not the
latest transaction encountered. Legacy databases without them remain unknown.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

VERSION = 1
STALE_AFTER_DAYS = 30
KINDS = ('employer', 'corporate')
_inspection_failures = {}


def day(value):
    """Strict calendar date; timestamps are never silently treated as cutoffs."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def cycles_from(value):
    if isinstance(value, str):
        value = value.split(',')
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({int(c) for c in value if re.fullmatch(r'20\d[02468]', str(c))})


def file_stamp(path):
    path = Path(path)
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=64)
def file_digest(stamp):
    with Path(stamp[0]).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def freshness(row, today=None):
    today = today or datetime.now(timezone.utc).date()
    result = dict(row)
    through = day(row.get('verified_data_through'))
    cycle = row['cycle']
    target = min(today, date(cycle, 12, 31))
    result.update(target_date=target.isoformat(), stale_after_days=STALE_AFTER_DAYS,
                  lag_days=None, freshness='unverifiable')
    if through and date(cycle - 1, 1, 1) <= through <= target:
        lag = (target - through).days
        # Historical cycles require coverage through cycle end, not a rolling
        # age allowance that would conceal their permanently missing December.
        stale = lag > (STALE_AFTER_DAYS if today <= date(cycle, 12, 31) else 0)
        result.update(lag_days=lag, freshness='stale' if stale else 'current')
    elif through:
        result.update(verified_data_through=None, reason='Coverage date is outside the cycle or in the future.')
    return result


def unknown(industry, cycles, reason):
    return [freshness({'industry': industry, 'cycle': cycle, 'verified_data_through': None,
                      'latest_observed_transaction_date': None, 'reason': reason,
                      'scope': 'FEC employer and corporate searches', 'evidence': []})
            for cycle in cycles]


def derive(connection, industry, cycles, companies, grid, collection_passed,
           assertions=(), today=None):
    """Take the earliest supported cutoff across the entire requested grid."""
    today = today or datetime.now(timezone.utc).date()
    assertions = list(assertions) if isinstance(assertions, (list, tuple)) else []
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'fec_date_evidence' in tables:
        columns = {r[1] for r in connection.execute('PRAGMA table_info(fec_date_evidence)')}
        if {'industry', 'company', 'cycle', 'kind', 'coverage_start', 'verified_data_through', 'verification_evidence'} <= columns:
            cursor = connection.execute('SELECT * FROM fec_date_evidence WHERE industry=?', (industry,))
            names = [c[0] for c in cursor.description]
            assertions += [dict(zip(names, r)) for r in cursor]
    # Index once per inspection; large rosters and their dated evidence must
    # not be rescanned for every company/search/cycle combination.
    company_set = set(companies)
    complete = {(g.get('company'), g.get('cycle')) for g in grid if g.get('complete') is True}
    assertions_by_scope = {}
    for item in assertions:
        if not isinstance(item, dict):
            continue
        scope = (item.get('industry'), item.get('company'), str(item.get('cycle')), item.get('kind'))
        try:
            assertions_by_scope.setdefault(scope, []).append(item)
        except TypeError:
            # Malformed JSON identities (lists/objects) cannot match a scope.
            continue
    observed_dates = {}
    result = []
    for cycle in cycles:
        row = unknown(industry, [cycle], 'No dated FEC coverage evidence for every company and search.')[0]
        row['company_count'] = len(companies)
        row['evidence'] = []
        # Transaction maxima are observations only. Scan valid dates rather than
        # SQL MAX(text), which can select malformed strings and hide real dates.
        observed = None
        invalid = 0
        for table, cycle_column in (('contributions', 'two_year_transaction_period'), ('corporate_giving', 'cycle')):
            columns = {r[1] for r in connection.execute(f'PRAGMA table_info({table})')}
            if not {'industry', 'company', cycle_column, 'contribution_receipt_date'} <= columns:
                continue
            for company, value in connection.execute(
                    f'SELECT DISTINCT company, contribution_receipt_date FROM {table} WHERE industry=? AND {cycle_column}=?',
                    (industry, cycle)):
                if company not in company_set:
                    continue
                # FEC transaction dates can include an ISO timestamp; their own
                # calendar day is preserved, not converted via the host timezone.
                raw_date = str(value)
                if raw_date not in observed_dates:
                    observed_dates[raw_date] = day(raw_date[:10]) if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T[0-9:.+Z-]+)?', raw_date) else None
                parsed = observed_dates[raw_date]
                if parsed and date(cycle - 1, 1, 1) <= parsed <= min(today, date(cycle, 12, 31)):
                    observed = max(observed, parsed) if observed else parsed
                else:
                    invalid += 1
        row['latest_observed_transaction_date'] = observed.isoformat() if observed else None
        row['invalid_transaction_dates'] = invalid
        expected = {(c, cycle) for c in companies}
        if not collection_passed or not companies or not expected <= complete:
            row['reason'] = 'Collection checks are missing, failed, or incomplete for this sector/cycle.'
            result.append(freshness(row, today))
            continue
        dates, missing = [], []
        for company in companies:
            for kind in KINDS:
                matches = assertions_by_scope.get((industry, company, str(cycle), kind), ())
                valid = []
                for item in matches:
                    start = day(item.get('coverage_start'))
                    end = day(item.get('verified_data_through'))
                    evidence = item.get('verification_evidence')
                    if (start and end and start <= date(cycle - 1, 1, 1) <= end <= min(today, date(cycle, 12, 31))
                            and isinstance(evidence, str) and evidence.strip()):
                        valid.append((end, evidence))
                # Ambiguous or invalid duplicate evidence must not select the
                # newest assertion and silently improve the displayed coverage.
                if not valid or len(valid) != len(matches):
                    missing.append(f'{company} / {kind}')
                else:
                    cutoff = min(d for d, _ in valid)
                    dates.append(cutoff)
                    row['evidence'].append({'company': company, 'kind': kind,
                                            'verified_data_through': cutoff.isoformat(),
                                            'verification_evidence': sorted({e for _, e in valid})})
        if not missing and dates:
            row['verified_data_through'] = min(dates).isoformat()
            row['reason'] = 'Earliest evidenced cutoff across all companies and both searches; later filings and unlisted aliases may still add records.'
        else:
            row['missing_evidence'] = missing
        result.append(freshness(row, today))
    return result


def receipt_passed(receipt):
    checks = receipt.get('checks')
    return receipt.get('status') == 'succeeded' and isinstance(checks, dict) and bool(checks) and all(v is True for v in checks.values())


def persistable(database, rows, before=None):
    """Stored by the existing coverage-receipt producer, never by a UI GET."""
    stamp = file_stamp(database)
    wal = Path(str(database) + '-wal')
    # A main-file hash alone cannot bind uncheckpointed WAL contents.
    digest = file_digest(stamp) if not wal.exists() or wal.stat().st_size == 0 else None
    if file_stamp(database) != stamp or (before is not None and before != stamp):
        digest = None
    if digest is None:
        rows = [{**r, 'verified_data_through': None, 'freshness': 'unverifiable',
                 'reason': 'Database changed or has uncheckpointed writes; coverage cannot be bound to this file.'} for r in rows]
    return {'version': VERSION, 'evaluated_at': datetime.now(timezone.utc).isoformat(),
            'database_sha256': digest, 'rows': rows}


def inspect(database, receipt_path, industry, cycles):
    """Read only pinned collection evidence; changing files invalidate the cache."""
    cycles = tuple(cycles_from(cycles))
    try:
        if not database or str(database).startswith(('\\\\', '//')):
            raise ValueError('No local collection database is recorded.')
        db_stamp = file_stamp(database)
        receipt_stamp = file_stamp(receipt_path)
        wal = Path(str(database) + '-wal')
        wal_stamp = file_stamp(wal) if wal.exists() else None
        cache_key = (db_stamp, receipt_stamp, wal_stamp, industry, cycles)
        failure = _inspection_failures.get(cache_key)
        if failure and time.monotonic() < failure[0]:
            return [freshness(row) for row in failure[1]]
        try:
            rows = _inspect(*cache_key)
            _inspection_failures.pop(cache_key, None)
        except (OSError, ValueError, sqlite3.Error, TypeError, KeyError, RuntimeError, AttributeError):
            rows = unknown(industry, cycles, 'Collection evidence is unreadable, incomplete, or could not be verified within the inspection time limit.')
            if len(_inspection_failures) >= 64:
                _inspection_failures.clear()
            # Lock release does not necessarily change a file timestamp. Retry
            # transient failures, while avoiding a repeated scan on every poll.
            _inspection_failures[cache_key] = (time.monotonic() + 30, rows)
        # Re-check both inputs after reading/hashing; never bind an older query
        # result to a newer database that arrived during inspection.
        current_wal = file_stamp(wal) if wal.exists() else None
        if file_stamp(database) != db_stamp or file_stamp(receipt_path) != receipt_stamp or current_wal != wal_stamp:
            raise ValueError('Collection evidence changed during inspection')
        modified = datetime.fromtimestamp(db_stamp[2] / 1e9, timezone.utc).isoformat()
        return [{**freshness(row), 'database_modified_at': modified} for row in rows]
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError, RuntimeError, AttributeError):
        return unknown(industry, cycles, 'Collection database or coverage receipt is missing, unreadable, or unverifiable.')


@lru_cache(maxsize=64)
def _inspect(db_stamp, receipt_stamp, wal_stamp, industry, cycles):
    return _read_coverage(db_stamp, receipt_stamp, wal_stamp, industry, cycles)


def _read_coverage(db_stamp, receipt_stamp, wal_stamp, industry, cycles):
    receipt = json.loads(Path(receipt_stamp[0]).read_text(encoding='utf-8-sig'))
    if not isinstance(receipt, dict):
        raise ValueError('Invalid receipt')
    if (receipt.get('industry') != industry or not set(cycles) <= set(cycles_from(receipt.get('cycles')))
            or Path(receipt.get('database', '')).resolve() != Path(db_stamp[0])):
        raise ValueError('Receipt belongs to another collection scope')
    stored = receipt.get('fec_coverage')
    if isinstance(stored, dict) and stored.get('version') == VERSION and stored.get('database_sha256'):
        if (wal_stamp and wal_stamp[1]) or file_digest(db_stamp) != stored['database_sha256']:
            return unknown(industry, cycles, 'Database changed since its dated coverage receipt; verification is required.')
        rows = stored.get('rows', [])
        if not receipt_passed(receipt) or not isinstance(rows, list):
            return unknown(industry, cycles, 'Collection coverage checks did not pass.')
        selected = [r for r in rows if isinstance(r, dict) and r.get('industry') == industry and r.get('cycle') in cycles]
        if sorted(r['cycle'] for r in selected) != list(cycles):
            raise ValueError('Incomplete dated receipt')
        companies = receipt.get('companies', [])
        for row in selected:
            if not row.get('verified_data_through'):
                continue
            evidence = row.get('evidence', [])
            expected = {(company, kind) for company in companies for kind in KINDS}
            actual = {(e['company'], e['kind']) for e in evidence}
            dates = [day(e.get('verified_data_through')) for e in evidence]
            complete = {g['company'] for g in receipt.get('company_cycle_grid', [])
                        if g.get('cycle') == row['cycle'] and g.get('complete') is True}
            if (not expected or actual != expected or len(evidence) != len(expected)
                    or set(companies) != complete or not all(dates)
                    or any(not isinstance(e.get('verification_evidence'), list) or
                           not e['verification_evidence'] or not all(isinstance(v, str) and v.strip() for v in e['verification_evidence']) for e in evidence)
                    or row['verified_data_through'] != min(dates).isoformat()):
                raise ValueError('Stored cutoff does not match its company/search evidence')
        return selected
    from validate_industry_coverage import expected_companies, api_progress_grid
    with closing(sqlite3.connect(Path(db_stamp[0]).as_uri() + '?mode=ro', uri=True, timeout=0.15)) as connection:
        connection.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 0.75
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        companies = expected_companies(connection, industry)
        grid, _, _ = api_progress_grid(connection, companies, cycles)
        rows = derive(connection, industry, cycles, companies, grid, receipt_passed(receipt), receipt.get('fec_date_evidence', []))
    if file_stamp(db_stamp[0]) != db_stamp:
        raise ValueError('Database changed during verification')
    return rows
