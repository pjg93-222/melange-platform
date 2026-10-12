"""Resolve bounded residuals as source-native ecosystem evidence.

Historical LDA person IDs remain separate unless a cross-source identity merge is
independently proven. Archived filing statements remain evidence even when the
wording is absent from the selected latest filing version; neither case creates
structured employment or operational coworker edges.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sqlite3

from .reconcile_held_evidence import sha


IDENTITY_HOLD = 'HELD_UNRESOLVED_SOURCE_IDENTITY'
LATEST_HOLD = 'HELD_NO_EXACT_STATEMENT_IN_VERIFIED_LATEST_TARGET_FILINGS'
IDENTITY_RESOLVED = 'INTEGRATED_HISTORICAL_LDA_SOURCE_IDENTITY_STATEMENT'
ARCHIVE_RESOLVED = 'INTEGRATED_ARCHIVED_FILING_STATEMENT_NOT_IN_SELECTED_LATEST'


def receipt_rows(receipt_path: Path):
    receipt = json.loads(receipt_path.read_bytes())
    if receipt.get('status') != 'PASS':
        raise ValueError('Evidence receipt did not pass: ' + str(receipt_path))
    for entry in receipt['files']:
        path = Path(entry['path'])
        if sha(path) != entry['sha256']:
            raise ValueError('Evidence bytes changed: ' + str(path))
        with gzip.open(path, 'rt', encoding='utf8') as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line), entry


def load_evidence(*receipts):
    evidence = {}
    for receipt in receipts:
        for row, entry in receipt_rows(Path(receipt)):
            clause_id = row.get('clause_id')
            if not clause_id:
                continue
            existing = evidence.get(clause_id)
            if existing and (existing[0].get('person'), existing[0].get('raw_text')) != (
                    row.get('person'), row.get('raw_text')):
                raise ValueError('Conflicting source rows for clause ' + clause_id)
            evidence[clause_id] = (row, entry)
    return evidence


def add_observation(release, person, label, row, entry, resolution, description):
    nodes = {node['id']: node for node in release['nodes']}
    node = nodes.get(person)
    if node is None:
        node = {
            'id': person,
            'label': label,
            'kind': 'person',
            'ecosystem_only': True,
            'historical_source_identity': True,
            'operational_staffer_qualification': False,
            'source_person_ids': [row['person']],
        }
        release['nodes'].append(node)
    observations = release.setdefault('person_evidence', {}).setdefault(person, [])
    if any(observation.get('source', {}).get('clause_id') == row['clause_id']
            for observation in observations):
        return False
    support = row.get('filing_support') or []
    if not support:
        raise ValueError('Clause lacks filing support: ' + row['clause_id'])
    assessment = row.get('assessment') or {}
    date_text = assessment.get('source_date_text') or assessment.get('matched_date_text')
    observations.append({
        'title': 'Reported career history',
        'organization': '',
        'raw_text': row['raw_text'].strip(),
        'status': 'SOURCE_REPORTED_CAREER_CLAIM',
        'start': None,
        'end': None,
        'continuous_employment_asserted': False,
        'structured_employer_title_confirmed': False,
        'operational_coworker_edge_eligible': False,
        'source_date_assessment': assessment,
        'date_description': description + ((' Source-reported date: ' + date_text + '.') if date_text else ''),
        'source': {
            'label': 'LDA filing archive',
            'url': 'https://lda.gov/filings/public/filing/' + support[0]['filing_uuid'] + '/print/',
            'clause_id': row['clause_id'],
            'source_person_id': row['person'],
            'identity_method': resolution,
            'filing_support': support,
            'path': entry['path'],
            'sha256': entry['sha256'],
        },
    })
    return True


def build(base, review, completion, partial, output):
    base, review, completion, partial, output = map(Path, (base, review, completion, partial, output))
    receipt_path = output.with_suffix('.review.json')
    if output.exists() or receipt_path.exists():
        raise ValueError('Immutable output already exists')
    release = json.loads(base.read_bytes())
    prior = json.loads(review.read_bytes())
    if release.get('status') != 'PASS' or prior.get('status') != 'PASS':
        raise ValueError('Passing release and review are required')
    evidence = load_evidence(completion, partial)
    ledger = Path(release['inputs']['ledger'])
    if sha(ledger) != release['inputs']['ledger_sha256']:
        raise ValueError('Ledger changed')
    with sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True) as database:
        canonical = {'person:' + person: target for person, target in
            database.execute('SELECT id,canonical_id FROM person')}

    decisions = []
    added = 0
    resolved = Counter()
    for decision in prior['decisions']:
        status = decision['status']
        if status not in {IDENTITY_HOLD, LATEST_HOLD}:
            decisions.append(decision)
            continue
        clause_id = decision['clause_id']
        if clause_id not in evidence:
            raise ValueError('No authenticated evidence row for clause ' + clause_id)
        row, entry = evidence[clause_id]
        if row.get('person') != decision.get('person'):
            raise ValueError('Source person mismatch for clause ' + clause_id)
        source_person = 'person:' + row['person']
        if status == IDENTITY_HOLD:
            person = source_person
            names = decision.get('source_names') or []
            label = names[0].strip() if names else row['person']
            new_status = IDENTITY_RESOLVED
            method = 'EXACT_HISTORICAL_LDA_SOURCE_PERSON_ID_RETAINED_SEPARATELY'
            description = ('Authenticated historical LDA statement. The source person ID is retained as a '
                'separate ecosystem identity until a cross-source merge is independently proven.')
        else:
            person = canonical.get(source_person, source_person)
            node = next((item for item in release['nodes'] if item['id'] == person), None)
            label = node['label'] if node else row['person']
            new_status = ARCHIVE_RESOLVED
            method = 'EXACT_ARCHIVED_FILING_UUID_AND_SOURCE_PERSON_ID'
            description = ('Authenticated archived filing statement. The wording is absent from the selected '
                'latest target filing, so it remains evidence-only and does not create a work-history edge.')
        if add_observation(release, person, label, row, entry, method, description):
            added += 1
        decisions.append({**decision, 'status': new_status, 'canonical_person': person,
            'identity_method': method, 'operational_coworker_edge_eligible': False})
        resolved[new_status] += 1

    counts = dict(Counter(decision['status'] for decision in decisions))
    held = sum(count for status, count in counts.items() if status.startswith('HELD_'))
    if held:
        raise ValueError('Residual holds remain after source-native resolution')
    receipt = {
        'status': 'PASS',
        'scope': 'All bounded historical evidence gaps',
        'clauses_evaluated': len(decisions),
        'observations_added': added,
        'resolved_counts': dict(resolved),
        'counts': counts,
        'held_count': held,
        'identity_graph_changed': False,
        'historical_source_people_merged': False,
        'employment_promoted': False,
        'operational_coworker_edges_added': 0,
        'decisions': decisions,
        'inputs': [{'path': str(path.resolve()), 'sha256': sha(path)}
            for path in (base, review, completion, partial)],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf8')
    release['reviewed_statement_integration'] = {
        'path': str(receipt_path.resolve()),
        'sha256': sha(receipt_path),
        'counts': counts,
        'observations_added': added,
        'all_bounded_residuals_resolved': True,
        'operational_coworker_edges_added': 0,
    }
    release['status'] = 'CANDIDATE'
    release.pop('validation', None)
    output.write_text(json.dumps(release, indent=2) + '\n', encoding='utf8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'review', 'completion', 'partial', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    arguments = parser.parse_args()
    result = build(arguments.base, arguments.review, arguments.completion,
        arguments.partial, arguments.output)
    print(json.dumps({key: value for key, value in result.items() if key != 'decisions'}, indent=2))
