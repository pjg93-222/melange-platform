"""Build an evidence-only candidate from authenticated career adjudications."""
import argparse
from copy import deepcopy
import gzip
import json
from pathlib import Path
import sqlite3

from .compact_existing_review import sha


def build(prior, completion, evaluation, output):
    prior, completion, evaluation, output = map(Path, (prior, completion, evaluation, output))
    if output.exists():
        raise ValueError('Immutable output exists')
    base = json.loads(prior.read_bytes())
    receipt = json.loads(completion.read_bytes())
    checked = json.loads(evaluation.read_bytes())
    if base['status'] != 'PASS' or receipt['status'] != 'PASS' or checked['status'] != 'PASS':
        raise ValueError('Inputs must pass')
    if checked['completion_receipt']['sha256'] != sha(completion):
        raise ValueError('Evaluation does not match completion')
    if receipt['inputs']['release']['sha256'] != sha(prior):
        raise ValueError('Wrong base release')
    ledger = Path(receipt['inputs']['ledger']['path'])
    if sha(ledger) != receipt['inputs']['ledger']['sha256']:
        raise ValueError('Ledger changed')
    with sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True) as db:
        people = {row[0]: (row[1], row[2]) for row in db.execute('SELECT id,label,canonical_id FROM person')}
    release = deepcopy(base)
    release['status'] = 'CANDIDATE'
    release.pop('validation', None)
    nodes = {node['id']: node for node in release['nodes']}
    added = 0
    held = []
    entry = next(item for item in receipt['files'] if Path(item['path']).name == 'career_date_decisions.ndjson.gz')
    if sha(entry['path']) != entry['sha256']:
        raise ValueError('Adjudication bytes changed')
    with gzip.open(entry['path'], 'rt', encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['status'] != 'STRUCTURED_EXACT_HELD_OFFICE_TITLE':
                continue
            ident = 'person:' + row['person']
            if ident not in people:
                held.append({'clause_id': row['clause_id'], 'reason': 'SOURCE_ID_NOT_IN_ACCEPTED_POPULATION'})
                continue
            label, target = people[ident]
            if target not in people:
                target = ident
            if len(row['employer_ids']) != 1:
                held.append({'clause_id': row['clause_id'], 'reason': 'AMBIGUOUS_OFFICE_ID'})
                continue
            support = row['filing_support']
            if not support:
                raise ValueError('Historical observation lacks filing support')
            assessment = row['assessment']
            date_text = assessment.get('source_date_text') or assessment.get('matched_date_text')
            if not date_text:
                raise ValueError('Original source date precision missing')
            nodes.setdefault(target, {'id': target, 'kind': 'person', 'label': people[target][0],
                                      'ecosystem_only': True, 'operational_staffer_qualification': False,
                                      'source_person_ids': [ident]})
            claim = {'title': row['title_labels'][0], 'organization': row['employer_labels'][0],
                     'date_description': 'LDA covered-position claim: ' + date_text +
                         '. Source precision retained; this does not establish confirmed employment.',
                     'status': 'SOURCE_REPORTED_CAREER_CLAIM', 'start': None, 'end': None,
                     'continuous_employment_asserted': False, 'raw_text': row['raw_text'],
                     'source_date_assessment': assessment,
                     'source': {'url': 'https://lda.gov/filings/public/filing/' + support[0]['filing_uuid'] + '/print/',
                                'label': 'LDA filing', 'path': entry['path'], 'sha256': entry['sha256'],
                                'clause_id': row['clause_id'], 'filing_support': support}}
            existing = release['person_evidence'].setdefault(target, [])
            duplicate = next((item for item in existing if item.get('raw_text') == claim['raw_text']
                              and item.get('status') == claim['status']), None)
            if duplicate:
                prior_support = duplicate['source']['filing_support']
                prior_support.extend(s for s in support if s not in prior_support)
            else:
                existing.append(claim)
                added += 1
    release['nodes'] = list(nodes.values())
    release['career_adjudication'] = {
        'completion': {'path': str(completion.resolve()), 'sha256': sha(completion)},
        'evaluation': {'path': str(evaluation.resolve()), 'sha256': sha(evaluation)},
        'base_release': {'path': str(prior.resolve()), 'sha256': sha(prior)},
        'source_observations_added': added, 'held': held,
        'employment_promoted': False,
        'duplicate_directory_policy': 'Retained as supporting references; not additional work episodes or stronger ties.',
    }
    # This integration changes only person evidence. No new staffer qualification,
    # identity binding, route association, or work-duration score is introduced.
    for key in ('identity_bindings', 'client_groups', 'official_associations'):
        if release[key] != base[key]:
            raise ValueError('Unexpected graph mutation')
    output.write_text(json.dumps(release, indent=2) + '\n', encoding='utf8')
    return {'status': 'CANDIDATE', 'source_observations_added': added, 'held': len(held),
            'path': str(output.resolve()), 'sha256': sha(output)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ('prior', 'completion', 'evaluation', 'output'):
        p.add_argument('--' + arg, required=True, type=Path)
    a = p.parse_args()
    print(json.dumps(build(a.prior, a.completion, a.evaluation, a.output)))
