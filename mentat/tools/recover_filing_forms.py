"""Apply cached FEC headers across a pinned source, keeping originals immutable.

The operation changes only missing form metadata. It does not change receipt
amounts, choose amendments, or certify unique dollars. Recovery is transactional
per filing and resumable; indexed lookups avoid rescanning the entire warehouse
for each header. Conflicts are recorded without applying that filing.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from fec_filing_headers import apply_header, fetch_header
from hydrate_receipt_metadata import prepare_copy
from report_source_guard import atomic, source_sha


def gaps(connection):
    return {table: connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE company_match_status='accepted' "
        "AND (coalesce(filing_form,'')='' OR coalesce(line_number,'')='')").fetchone()[0]
        for table in ('contributions','corporate_giving')}


def recover(database, output, cache):
    prepared = prepare_copy(database, output)
    changes, review = [], []
    with closing(sqlite3.connect(prepared['output'])) as connection:
        before = gaps(connection)
        for table in before:
            connection.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_filing_recovery ON {table}(file_num,company_match_status)')
        connection.commit()
        files = set()
        for table in before:
            files.update(str(r[0]) for r in connection.execute(
                f"SELECT DISTINCT file_num FROM {table} WHERE company_match_status='accepted' AND coalesce(filing_form,'')='' AND file_num IS NOT NULL"))
        for number in sorted(files, key=int):
            path = Path(cache)/f'{number}.json'
            if not path.exists() and not (Path(cache)/f'{number}.review.json').exists():
                review.append({'file_num':number, 'reason':'No supported cached filing header'}); continue
            try:
                evidence = fetch_header(number, cache, lambda _: (_ for _ in ()).throw(ValueError('Recovery must be offline')))
                with connection:
                    count = apply_header(connection, evidence)
                if count: changes.append({'file_num':number, 'forms_filled':count})
            except (ValueError, sqlite3.Error) as error:
                review.append({'file_num':number, 'reason':str(error)})
        after = gaps(connection)
    if source_sha(prepared['database']) != prepared['source_sha256']:
        raise ValueError('Original source changed during recovery')
    result = {'source':str(prepared['database']), 'source_sha256':prepared['source_sha256'],
              'output':str(prepared['output']), 'before_missing_form_or_line':before,
              'after_missing_form_or_line':after, 'forms_filled':sum(c['forms_filled'] for c in changes),
              'changes':changes, 'review':review, 'scope':'Missing filing forms only; no transaction linkage or dollar changes',
              'complete':not any(after.values())}
    atomic(prepared['output'].with_suffix('.filing-forms.json'),result)
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('database',type=Path);p.add_argument('output',type=Path);p.add_argument('cache',type=Path)
    a=p.parse_args();r=recover(a.database,a.output,a.cache)
    print(json.dumps({k:v for k,v in r.items() if k not in ('changes','review')},indent=2))
