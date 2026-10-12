"""Publish authenticated, verbatim career observations without inventing work edges.

Catalog normalization is optional for an evidence display. Identity and provenance
are not: unmatched people remain in review and every displayed statement retains
its original clause, date precision, and filing references.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import json
from pathlib import Path
import sqlite3

from .complete_existing_career_histories import norm, sha


def read_rows(entry):
    if sha(entry['path']) != entry['sha256']:
        raise ValueError('Evidence bytes changed')
    with gzip.open(entry['path'], 'rt', encoding='utf8') as stream:
        return [json.loads(line) for line in stream]


def unique_identity(names, texts, people_by_name, claims_by_text):
    """Require a unique exact full-name AND exact bounded career-clause match."""
    candidates = set().union(*(people_by_name.get(norm(n), set()) for n in names))
    matches = set().union(*(claims_by_text.get(norm(t), set()) for t in texts)) & candidates
    return next(iter(matches)) if len(matches) == 1 else None


def compatible_name(left, right):
    """Allow honorifics and expanded initials, never conflicting names/suffixes."""
    def parts(value):
        words = norm(value).split()
        if words and words[0] in {'mr', 'mrs', 'ms', 'dr'}:
            words.pop(0)
        suffix = words.pop() if words and words[-1] in {'jr', 'sr', 'ii', 'iii', 'iv'} else None
        return words, suffix
    a, sa = parts(left)
    b, sb = parts(right)
    if len(a) < 2 or len(b) < 2 or a[0] != b[0] or a[-1] != b[-1] or (sa and sb and sa != sb):
        return False
    am, bm = a[1:-1], b[1:-1]
    return not am or not bm or (len(am) == len(bm) and all(
        x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
        for x, y in zip(am, bm)))


def compatible_career_identity(names, texts, labels, claims_by_text):
    # The bounded career clause must still match exactly. Name normalization
    # cannot independently promote an identity or choose between homonyms.
    candidates = set().union(*(claims_by_text.get(norm(t), set()) for t in texts))
    matches = {p for p in candidates if any(compatible_name(n, labels[p][0]) for n in names)}
    return next(iter(matches)) if len(matches) == 1 else None


def selected_target_statements(manifest):
    support = defaultdict(list)
    labels = defaultdict(set)
    inputs = []
    for completed in manifest['completed']:
        path = Path(completed['receipt'])
        receipt = json.loads(path.read_bytes())
        if receipt['status'] != 'PASS' or not receipt['exact_uuid_and_payload_twins']:
            raise ValueError('Unverified target receipt')
        inputs.append({'path': str(path), 'sha256': sha(path)})
        windows = defaultdict(dict)
        for source in receipt['sources']:
            if sha(source['path']) != source['sha256']:
                raise ValueError('Target filing changed')
            if Path(source['path']).parent.name != 'A':
                continue
            for filing in json.loads(Path(source['path']).read_bytes())['results']:
                key = tuple([filing['registrant']['id'], filing['client']['id'],
                             filing['filing_year'], filing['filing_period']])
                windows[key][filing['filing_uuid']] = (filing, source)
        for versions in windows.values():
            latest = max(datetime.fromisoformat(f['dt_posted']) for f, _ in versions.values())
            selected = [(f, s) for f, s in versions.values() if datetime.fromisoformat(f['dt_posted']) == latest]
            if len(selected) != 1:
                continue
            filing, source = selected[0]
            for ordinal, activity in enumerate(filing['lobbying_activities']):
                for item in activity['lobbyists']:
                    raw = item['lobbyist']
                    person = 'lda:lobbyist:' + str(raw['id'])
                    if person != receipt['person_id']:
                        continue
                    labels[person].add(' '.join(str(raw.get(k) or '') for k in ('first_name', 'middle_name', 'last_name', 'suffix')))
                    import re
                    for clause in re.split(r'[;\n]+', item.get('covered_position') or ''):
                        support[person, clause.strip()].append({**source,
                            'filing_uuid': filing['filing_uuid'], 'activity_ordinal': ordinal})
    return support, labels, inputs


def build(base, root, output, manifest_path=None, identity_adjudications=None):
    base, root, output = map(Path, (base, root, output))
    if output.exists():
        raise ValueError('Immutable candidate exists')
    release = json.loads(base.read_bytes())
    if release['status'] != 'PASS':
        raise ValueError('Verified release required')
    ledger = Path(release['inputs']['ledger'])
    if sha(ledger) != release['inputs']['ledger_sha256']:
        raise ValueError('Ledger changed')
    completion_path = root / 'existing-career-completion-20260908-v2/receipt.json'
    completion = json.loads(completion_path.read_bytes())
    partial_path = root / 'partial-2018-adjudications-v1/receipt.json'
    partial = json.loads(partial_path.read_bytes())
    if completion['status'] != 'PASS' or partial['status'] != 'PASS':
        raise ValueError('Verified source adjudications required')
    if (completion['inputs']['ledger']['sha256'] != release['inputs']['ledger_sha256']
            or partial['inputs']['ledger_sha256'] != release['inputs']['ledger_sha256']):
        raise ValueError('Adjudications use a different ledger')
    historical_spec = completion['inputs']['historical_holdings']
    if sha(historical_spec['path']) != historical_spec['sha256']:
        raise ValueError('Historical holdings changed')
    with sqlite3.connect(Path(historical_spec['path']).resolve().as_uri() + '?mode=ro', uri=True) as h:
        labels = defaultdict(set)
        for person, names in h.execute('SELECT person,labels FROM source_person'):
            labels[person].update(json.loads(names))
    manifest_path = Path(manifest_path) if manifest_path else root / 'targeted-2018-career-backfill-v1/manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    if manifest['status'] not in {'PASS', 'PARTIAL'}:
        raise ValueError('Target batch is still running or unverified')
    verified_targets = {'lda:lobbyist:' + str(item['person']) for item in manifest['completed']}
    support, target_labels, inputs = selected_target_statements(manifest)
    for person, names in target_labels.items():
        labels[person].update(names)
    rows = []
    for origin, receipt in [('completed_history', completion), ('targeted_2018', partial)]:
        for entry in receipt['files']:
            for row in read_rows(entry):
                if row.get('assessment', {}).get('status') == 'BOUNDED_SOURCE_INTERVAL':
                    rows.append({**row, 'origin': origin, 'evidence_file': entry})
    with sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True) as db:
        people = {p: (label, canonical) for p, label, canonical in db.execute('SELECT id,label,canonical_id FROM person')}
        people_by_name = defaultdict(set)
        for p, source, label in db.execute("SELECT id,source,label FROM person WHERE source='lda'"):
            people_by_name[norm(label)].add(p)
        claims_by_text = defaultdict(set)
        identity_claim_proofs = defaultdict(list)
        for claim_id, person, payload in db.execute("SELECT id,person,payload FROM claim WHERE source='lda_covered_clause' AND status='BOUNDED_SOURCE_INTERVAL'"):
            raw_text = json.loads(payload)['raw_text']
            claims_by_text[norm(raw_text)].add('person:' + person)
            identity_claim_proofs[norm(raw_text)].append({'claim_id': claim_id, 'person': 'person:' + person, 'raw_text': raw_text})
    texts = defaultdict(list)
    for row in rows:
        if row['origin'] == 'completed_history' or support.get((row['person'], row['raw_text'].strip())):
            texts[row['person']].append(row['raw_text'])
    mappings = {p: unique_identity(labels[p], ts, people_by_name, claims_by_text)
                for p, ts in texts.items() if 'person:' + p not in people}
    compatible_mappings = {}
    for person, target in mappings.items():
        if target is None:
            match = compatible_career_identity(labels[person], texts[person], people, claims_by_text)
            if match:
                compatible_mappings[person] = match
    mappings.update(compatible_mappings)
    reviewed_mappings = {}
    if identity_adjudications:
        identity_adjudications = Path(identity_adjudications)
        reviewed = json.loads(identity_adjudications.read_bytes())
        if reviewed['status'] != 'PASS' or reviewed['historical_database']['sha256'] != historical_spec['sha256']:
            raise ValueError('Reviewed identities do not match historical holdings')
        with sqlite3.connect(Path(historical_spec['path']).resolve().as_uri() + '?mode=ro', uri=True) as db:
            for item in reviewed['accepted']:
                person, target = item['source'], item['target']
                if target not in people or item['employment_dates_transferred'] or item['identity_graph_changed']:
                    raise ValueError('Invalid evidence-only identity adjudication')
                if mappings.get(person) and mappings[person] != target:
                    raise ValueError('Identity adjudication conflicts with exact-clause evidence')
                same_name = set().union(*(people_by_name.get(norm(n), set()) for n in labels[person]))
                if same_name != {target}:
                    raise ValueError('Reviewed identity is not an unambiguous exact full-name counterpart')
                if not item['counterpart_claims']:
                    raise ValueError('No counterpart claims')
                for claim in item['counterpart_claims']:
                    actual = db.execute('SELECT person,raw_text FROM claim WHERE id=?', (claim['claim_id'],)).fetchone()
                    if actual != (target.removeprefix('person:'), claim['raw_text']):
                        raise ValueError('Reviewed counterpart claim differs from archive')
                reviewed_mappings[person] = item
                mappings[person] = target
        inputs.append({'path': str(identity_adjudications.resolve()), 'sha256': sha(identity_adjudications)})
    nodes = {n['id']: n for n in release['nodes']}
    decisions = []
    added = 0
    for row in rows:
        person, raw = row['person'], row['raw_text'].strip()
        decision = {'clause_id': row['clause_id'], 'person': person, 'origin': row['origin']}
        proofs = (support.get((person, raw), []) if row['origin'] == 'targeted_2018' else row['filing_support'])
        if not proofs:
            state = ('HELD_NO_EXACT_STATEMENT_IN_VERIFIED_LATEST_TARGET_FILINGS' if person in verified_targets
                     else 'HELD_TARGET_HISTORY_NOT_YET_VERIFIED')
            decisions.append({**decision, 'status': state})
            continue
        pid = 'person:' + person
        identity_method = 'EXACT_SOURCE_PERSON_ID'
        if pid not in people:
            pid = mappings.get(person)
            identity_method = 'UNIQUE_EXACT_FULL_NAME_AND_BOUNDED_CAREER_CLAUSE'
            if person in compatible_mappings:
                identity_method = 'UNIQUE_COMPATIBLE_FULL_NAME_AND_EXACT_BOUNDED_CAREER_CLAUSE'
            if person in reviewed_mappings:
                identity_method = reviewed_mappings[person]['method']
        if not pid:
            decisions.append({**decision, 'status': 'HELD_UNRESOLVED_SOURCE_IDENTITY', 'source_names': sorted(labels[person])})
            continue
        label, canonical = people[pid]
        nodes.setdefault(canonical, {'id': canonical, 'label': label, 'kind': 'person',
            'ecosystem_only': True, 'operational_staffer_qualification': False, 'source_person_ids': [person]})
        observations = release['person_evidence'].setdefault(canonical, [])
        previous = next((o for o in observations if norm(o.get('raw_text')) == norm(raw)), None)
        provenance = {'clause_id': row['clause_id'], 'source_person_id': person,
            'identity_method': identity_method, 'matched_person_id': pid,
            'filing_support': proofs, 'path': row['evidence_file']['path'], 'sha256': row['evidence_file']['sha256']}
        if person in reviewed_mappings:
            provenance['identity_adjudication'] = reviewed_mappings[person]
        if previous:
            references = previous.setdefault('additional_source_evidence', [])
            if provenance not in references:
                references.append(provenance)
            state = 'INTEGRATED_SUPPORT_FOR_EXISTING_STATEMENT'
        else:
            assessment = row['assessment']
            date_text = assessment.get('source_date_text') or assessment.get('matched_date_text')
            if not date_text:
                raise ValueError('Source date text missing')
            observations.append({'title': 'Reported career history', 'organization': '', 'raw_text': raw,
                'status': 'SOURCE_REPORTED_CAREER_CLAIM', 'start': None, 'end': None,
                'continuous_employment_asserted': False, 'structured_employer_title_confirmed': False,
                'source_date_assessment': assessment,
                'date_description': 'Source-reported: ' + date_text + '. Historical filing statement; role and employer wording retained verbatim. Employment is not independently confirmed.',
                'source': {**provenance, 'label': 'LDA filing',
                    'url': 'https://lda.gov/filings/public/filing/' + proofs[0]['filing_uuid'] + '/print/'}})
            added += 1
            state = 'INTEGRATED_VERBATIM_SOURCE_STATEMENT'
        decisions.append({**decision, 'status': state, 'canonical_person': canonical, 'identity_method': identity_method})
    receipt = {'status': 'PASS', 'scope': 'All previously adjudicated bounded historical clauses',
        'clauses_evaluated': len(rows), 'observations_added': added,
        'counts': dict(Counter(d['status'] for d in decisions)), 'decisions': decisions,
        'identity_evidence_attributions': {p: target for p, target in mappings.items() if target},
        'identity_attribution_proofs': {p: {'source_names': sorted(labels[p]), 'target_name': people[target][0],
            'matched_bounded_claims': [proof for text in sorted(set(texts[p]))
                for proof in identity_claim_proofs[norm(text)] if proof['person'] == target]}
            for p, target in mappings.items() if target and p not in reviewed_mappings},
        'reviewed_identity_attributions': reviewed_mappings,
        'identity_graph_changed': False, 'employment_promoted': False,
        'inputs': [{'path': str(p), 'sha256': sha(p)} for p in [base, completion_path, partial_path, manifest_path]] + inputs}
    receipt_path = output.with_suffix('.review.json')
    if receipt_path.exists():
        raise ValueError('Immutable review exists')
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf8')
    release['nodes'] = list(nodes.values())
    release['reviewed_statement_integration'] = {'path': str(receipt_path.resolve()), 'sha256': sha(receipt_path),
        'counts': receipt['counts'], 'observations_added': added}
    release['status'] = 'CANDIDATE'
    release.pop('validation', None)
    output.write_text(json.dumps(release, indent=2) + '\n', encoding='utf8')
    return {k: v for k, v in receipt.items() if k not in ('decisions', 'inputs')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('base', 'root', 'output'):
        parser.add_argument('--' + key, required=True, type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--identity-adjudications', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.root, args.output, args.manifest, args.identity_adjudications), indent=2))
