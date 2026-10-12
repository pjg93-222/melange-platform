"""Check a complete reviewed-statement candidate against its immutable sources."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import subprocess
import sqlite3
import sys

from .complete_existing_career_histories import norm, sha


def run(candidate, output):
    candidate, output = Path(candidate), Path(output)
    if output.exists():
        raise ValueError('Immutable evaluation exists')
    release = json.loads(candidate.read_bytes())
    spec = release['reviewed_statement_integration']
    assert sha(spec['path']) == spec['sha256']
    review = json.loads(Path(spec['path']).read_bytes())
    for item in review['inputs']:
        assert sha(item['path']) == item['sha256']
    base = json.loads(Path(review['inputs'][0]['path']).read_bytes())
    allowed = {'nodes', 'person_evidence', 'reviewed_statement_integration', 'status', 'validation'}
    for key in set(base) | set(release):
        if key not in allowed:
            assert base.get(key) == release.get(key), key
    decisions = review['decisions']
    assert len(decisions) == review['clauses_evaluated'] == 1999
    assert len({(d['origin'], d['clause_id']) for d in decisions}) == len(decisions)
    assert dict(Counter(d['status'] for d in decisions)) == review['counts']
    with sqlite3.connect(Path(release['inputs']['ledger']).resolve().as_uri() + '?mode=ro', uri=True) as db:
        for source_id, target in review.get('identity_evidence_attributions', {}).items():
            if source_id in review.get('reviewed_identity_attributions', {}):
                assert review['reviewed_identity_attributions'][source_id]['target'] == target
                continue
            proofs = review['identity_attribution_proofs'][source_id]['matched_bounded_claims']
            assert proofs
            for proof in proofs:
                claim = db.execute('SELECT person,status,payload FROM claim WHERE id=?', (proof['claim_id'],)).fetchone()
                assert claim and 'person:' + claim[0] == target
                assert claim[1] == 'BOUNDED_SOURCE_INTERVAL'
                assert proof['raw_text'] == json.loads(claim[2])['raw_text']
    sources = {}
    source_files = {}
    for item in review['inputs'][1:3]:
        for entry in json.loads(Path(item['path']).read_bytes())['files']:
            assert sha(entry['path']) == entry['sha256']
            source_files[entry['path']] = entry['sha256']
            with gzip.open(entry['path'], 'rt', encoding='utf8') as stream:
                for line in stream:
                    row = json.loads(line)
                    if row.get('assessment', {}).get('status') == 'BOUNDED_SOURCE_INTERVAL':
                        sources[row['clause_id']] = row
    actual_added = 0
    for person, observations in release['person_evidence'].items():
        prior = base['person_evidence'].get(person, [])
        for old in prior:
            found = next(o for o in observations if all(o.get(k) == v for k, v in old.items() if k != 'additional_source_evidence'))
            assert found
            assert all(ref in found.get('additional_source_evidence', []) for ref in old.get('additional_source_evidence', []))
        for observation in observations[len(prior):]:
            actual_added += 1
            source = observation['source']
            row = sources[source['clause_id']]
            assert observation['raw_text'] == row['raw_text'].strip()
            assert observation['source_date_assessment'] == row['assessment']
            assert observation['start'] is None and observation['end'] is None
            assert observation['continuous_employment_asserted'] is False
            assert source['filing_support']
            assert source_files[source['path']] == source['sha256']
    assert actual_added == review['observations_added']
    for d in decisions:
        if d['status'].startswith('INTEGRATED_'):
            assert any(norm(o.get('raw_text')) == norm(sources[d['clause_id']]['raw_text'])
                       for o in release['person_evidence'][d['canonical_person']])
    tests = ['fec/tests/staffer_network/test_integrate_reviewed_statements.py',
             'fec/tests/staffer_network/test_integrate_backfilled_careers.py',
             'fec/tests/staffer_network/test_integrate_target_history.py',
             'fec/tests/surfaces/test_staffer_reconciled_evidence.py']
    result = subprocess.run([sys.executable, '-m', 'pytest', *tests, '-q'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    code = ['fec/staffer_network/integrate_reviewed_statements.py', __file__,
            'fec/surfaces/staffer_reconciled_evidence.py', *tests]
    report = {'status': 'PASS', 'candidate_sha256': sha(candidate),
        'clauses_evaluated': len(decisions), 'observations_added': actual_added,
        'source_partition_checked': True, 'verbatim_and_date_precision_checked': True,
        'routing_and_identity_configuration_unchanged': True,
        'tests': result.stdout, 'code_sha256': {str(Path(p).resolve()): sha(p) for p in code}}
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    run(args.candidate, args.output)
