"""Publish the verified directory-led population for the live staffer view."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def activate(spine_directory, output):
    spine_directory, output = Path(spine_directory).resolve(), Path(output).resolve()
    receipt_path = spine_directory / 'spine_receipt.json'
    verification_path = spine_directory / 'spine_verification.json'
    receipt = json.loads(receipt_path.read_bytes())
    verification = json.loads(verification_path.read_bytes())
    database = Path(receipt['database']['path'])
    if verification.get('status') != 'PASS' or verification.get('receipt_sha256') != sha(receipt_path):
        raise ValueError('Operational spine verification does not bind this receipt')
    if receipt.get('policy', {}).get('preferred_role_source') != 'politico':
        raise ValueError('Politico precedence is not established')
    if receipt['policy'].get('payroll_mode') != 'TARGETED_REQUESTS_ONLY':
        raise ValueError('Payroll scope is broader than targeted requests')
    if receipt['policy'].get('internship_scored_days') != 0 or not verification['checks'].get('no_intern_only_current_population'):
        raise ValueError('Intern exclusion did not pass')
    if sha(database) != receipt['database']['sha256']:
        raise ValueError('Operational spine database changed')
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    populations = ('staffer_current_reported', 'staffer_historical', 'staffer_undated')
    placeholders = ','.join('?' for _ in populations)
    ids = [row[0] for row in connection.execute(
        f'SELECT DISTINCT person_id FROM memberships WHERE population IN ({placeholders}) ORDER BY person_id', populations)]
    source_counts = dict(connection.execute(
        f'''SELECT s.source,count(DISTINCT m.person_id) FROM memberships m
            JOIN source_records s ON s.id=m.source_record_id
            WHERE m.population IN ({placeholders}) GROUP BY s.source''', populations))
    bad = connection.execute(
        f'''SELECT count(*) FROM memberships m JOIN source_records s ON s.id=m.source_record_id
            WHERE m.population IN ({placeholders}) AND s.source NOT IN ('politico','quorum')''', populations).fetchone()[0]
    interns = connection.execute(
        f'''SELECT count(*) FROM memberships m JOIN source_records s ON s.id=m.source_record_id
            WHERE m.population IN ({placeholders}) AND s.disposition='INTERN_ONLY_CURRENT_EXCLUDED' ''', populations).fetchone()[0]
    connection.close()
    if bad or interns or len(ids) != receipt['population_distinct_ids']['staffer_current_reported'] + receipt['population_distinct_ids']['staffer_historical'] + receipt['population_distinct_ids']['staffer_undated']:
        raise ValueError('Operational population accounting failed')
    result = {'schema': 'melange-operational-staffer-population-v1', 'status': 'PASS',
        'activated_at': datetime.now(timezone.utc).isoformat(), 'staffer_ids': ids,
        'staffer_count': len(ids), 'source_counts': source_counts,
        'policy': {'politico_role_date_authority': True, 'quorum_unresolved_separate': True,
                   'payroll_population_source': False, 'interns_excluded': True,
                   'lda_staffer_population_source': False, 'ecosystem_separate': True},
        'spine': {'database': str(database), 'database_sha256': receipt['database']['sha256'],
                  'receipt': str(receipt_path), 'receipt_sha256': sha(receipt_path),
                  'verification': str(verification_path), 'verification_sha256': sha(verification_path)},
        'scope': 'Population activation only; no identity decisions, career dates, or relationship edges changed.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=output.name + '.', suffix='.candidate', dir=output.parent)
    os.close(fd)
    try:
        Path(name).write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
        os.replace(name, output)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print(json.dumps({'status': 'PASS', 'staffer_count': len(ids), 'output': str(output), 'sha256': sha(output)}))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spine-directory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    activate(args.spine_directory, args.output)
