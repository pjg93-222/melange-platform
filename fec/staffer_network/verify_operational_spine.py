"""Read-only independent conservation and eligibility checks for a candidate spine."""
import hashlib
import json
from pathlib import Path
import sqlite3


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(directory):
    directory = Path(directory).resolve()
    receipt_path = directory / 'spine_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    dbpath = Path(receipt['database']['path'])
    failures, checks = [], {}
    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            failures.append(name)
    check('database_hash', digest(dbpath) == receipt['database']['sha256'])
    for source in receipt['inputs']:
        check('snapshot_hash:' + source['id'], digest(source['snapshot']) == source['sha256'])
    db = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True)
    check('sqlite_integrity', db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
    check('foreign_keys', not db.execute('PRAGMA foreign_key_check').fetchall())
    check('source_record_conservation', db.execute('SELECT sum(records) FROM sources').fetchone()[0] == db.execute('SELECT count(*) FROM source_records').fetchone()[0])
    for source, expected in db.execute('SELECT id,records FROM sources').fetchall():
        check('source_count:' + source, db.execute('SELECT count(*) FROM source_records WHERE source=?', (source,)).fetchone()[0] == expected)
    check('core_has_directory_support', db.execute("SELECT count(*) FROM memberships m JOIN source_records s ON m.source_record_id=s.id WHERE m.population LIKE 'staffer_%' AND s.source NOT IN ('politico','quorum')").fetchone()[0] == 0)
    check('no_payroll_seed_records', db.execute("SELECT count(*) FROM source_records WHERE source NOT IN ('politico','quorum','lda')").fetchone()[0] == 0)
    check('no_intern_only_current_population', db.execute("SELECT count(*) FROM memberships m JOIN source_records s ON s.id=m.source_record_id WHERE m.population LIKE 'staffer_%' AND s.disposition='INTERN_ONLY_CURRENT_EXCLUDED'").fetchone()[0] == 0)
    check('intern_and_mixed_duration_excluded', db.execute("SELECT count(*) FROM roles WHERE role_state IN ('INTERN','MIXED_ROLE_REVIEW','UNKNOWN_ROLE') AND temporal_candidate!=0").fetchone()[0] == 0)
    check('qualified_intervals_bounded', db.execute("SELECT count(*) FROM roles WHERE temporal_candidate=1 AND (start IS NULL OR end_exclusive IS NULL OR start>=end_exclusive OR role_kind!='history')").fetchone()[0] == 0)
    check('quorum_no_fabricated_dates', db.execute("SELECT count(*) FROM roles r JOIN source_records s ON r.source_record_id=s.id WHERE s.source='quorum' AND (r.start IS NOT NULL OR r.end_exclusive IS NOT NULL OR r.temporal_candidate!=0)").fetchone()[0] == 0)
    check('no_canonical_promotions', db.execute('SELECT sum(canonical_eligible) FROM roles').fetchone()[0] == 0)
    check('preferred_roles_nonintern', db.execute("SELECT count(*) FROM preferred_roles p JOIN roles r ON r.id=p.role_id WHERE r.role_state!='SUBSTANTIVE'").fetchone()[0] == 0)
    check('politico_precedence', db.execute("SELECT count(*) FROM preferred_roles p JOIN roles r ON r.id=p.role_id JOIN source_records s ON s.id=r.source_record_id WHERE s.source='quorum' AND EXISTS (SELECT 1 FROM preferred_roles p2 JOIN roles r2 ON r2.id=p2.role_id JOIN source_records s2 ON s2.id=r2.source_record_id WHERE p2.person_id=p.person_id AND s2.source='politico')").fetchone()[0] == 0)
    check('payroll_rows_have_requests', db.execute('SELECT count(*) FROM payroll_evidence e LEFT JOIN payroll_requests r ON e.request_id=r.id WHERE r.id IS NULL').fetchone()[0] == 0)
    check('new_candidates_unpromoted', db.execute("SELECT count(*) FROM review_queue WHERE id LIKE 'candidate:%' AND status!='REVIEW_REQUIRED'").fetchone()[0] == 0)
    aliases = dict(db.execute('SELECT alias_id,person_id FROM aliases'))
    policy = json.loads(db.execute("SELECT value FROM metadata WHERE key='policy'").fetchone()[0])
    groups = [json.loads(r[0]) for r in db.execute('SELECT payload FROM identity_decisions')]
    check('existing_aliases_preserved', all(aliases.get(a) == aliases.get(g['primary_id'], g['primary_id']) for g in groups for a in g.get('alias_ids', [])))
    pairs = json.loads(db.execute("SELECT value FROM metadata WHERE key='keep_separate_pairs'").fetchone()[0])
    check('keep_separate_preserved', all(aliases.get(a, a) != aliases.get(b, b) for a, b in pairs))
    check('no_math_change_or_live_activation', policy['math_changed'] is False and policy['production_consumer_enabled'] is False and policy['new_identity_merges'] == 0)
    for row in db.execute('SELECT id,payload,payload_sha256 FROM source_records'):
        if hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
            failures.append('source_payload_hash:' + row[0])
    check('source_payload_hashes', not any(f.startswith('source_payload_hash:') for f in failures))
    check('unique_membership_for_each_source', db.execute('SELECT count(*) FROM memberships').fetchone()[0] == db.execute('SELECT count(*) FROM source_records').fetchone()[0])
    db.close()
    result = {'status': 'PASS' if not failures else 'FAIL', 'scope': 'CANDIDATE_SPINE_INTEGRITY_AND_ELIGIBILITY_ONLY',
              'receipt_sha256': digest(receipt_path), 'checks': checks, 'failures': failures,
              'identity_accuracy_calibrated': False, 'new_relationships_validated': False,
              'release_ready': False, 'verifier_sha256': digest(__file__)}
    (directory / 'spine_verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
