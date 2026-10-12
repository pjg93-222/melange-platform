"""Inspect a sealed candidate spine without starting or changing the live service."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def inspect(directory, search='', person_id=None, population='staffer_current_reported', limit=25):
    directory = Path(directory).resolve()
    receipt = json.loads((directory / 'spine_receipt.json').read_text(encoding='utf-8'))
    verification = json.loads((directory / 'spine_verification.json').read_text(encoding='utf-8'))
    if verification['status'] != 'PASS':
        raise ValueError('Candidate verification has not passed')
    if hashlib.sha256((directory / 'spine_receipt.json').read_bytes()).hexdigest() != verification['receipt_sha256']:
        raise ValueError('Candidate receipt changed after verification')
    dbpath = Path(receipt['database']['path'])
    with dbpath.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != receipt['database']['sha256']:
            raise ValueError('Candidate database changed after sealing')
    db = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    limit = max(1, min(100, int(limit)))
    if person_id:
        alias = db.execute('SELECT person_id FROM aliases WHERE alias_id=?', (person_id,)).fetchone()
        person_id = alias[0] if alias else person_id
        selected = db.execute('SELECT * FROM people WHERE id=?', (person_id,)).fetchall()
    else:
        escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        selected = db.execute("SELECT DISTINCT p.* FROM people p JOIN memberships m ON p.id=m.person_id WHERE m.population=? AND p.label LIKE ? ESCAPE '\\' ORDER BY p.label,p.id LIMIT ?",
                              (population, '%' + escaped + '%', limit)).fetchall()
    result = []
    for item in selected:
        ident = item['id']
        result.append({**dict(item),
            'populations': [r[0] for r in db.execute('SELECT DISTINCT population FROM memberships WHERE person_id=? ORDER BY population', (ident,))],
            'source_records': [dict(r) for r in db.execute('SELECT id,source,label,disposition FROM source_records WHERE person_id=? ORDER BY source,id', (ident,))],
            'preferred_roles': [dict(r) for r in db.execute('SELECT r.title,c.label AS office,r.observed_at,p.preference_reason FROM preferred_roles p JOIN roles r ON p.role_id=r.id LEFT JOIN containers c ON r.container_id=c.id WHERE p.person_id=?', (ident,))],
            'role_counts': dict(db.execute('SELECT role_state,count(*) FROM roles WHERE person_id=? GROUP BY role_state', (ident,))),
            'review_items': db.execute('SELECT count(*) FROM review_queue WHERE source_record_id IN (SELECT id FROM source_records WHERE person_id=?)', (ident,)).fetchone()[0]})
    db.close()
    return {'generation': directory.name, 'candidate_only': True, 'population_filter': population,
            'results': result, 'limit': limit}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--search', default='')
    parser.add_argument('--person-id')
    parser.add_argument('--population', default='staffer_current_reported', choices=[
        'staffer_current_reported', 'staffer_historical', 'staffer_undated', 'ecosystem_lobbyist', 'review', 'excluded'])
    parser.add_argument('--limit', type=int, default=25)
    args = parser.parse_args()
    print(json.dumps(inspect(args.directory, args.search, args.person_id, args.population, args.limit), indent=2))
