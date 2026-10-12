"""Group saved partial-window career observations against existing exact claims."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re
import sqlite3

from .compact_existing_review import packed, ranges, sha
from .complete_existing_career_histories import write_gzip
from .evaluate_historical_evidence import assess_clause
from .reconcile_held_evidence import covered_state


def run(receipt_path, ledger_path, historical_path, output):
    receipt_path, ledger_path, historical_path, output = map(
        Path, (receipt_path, ledger_path, historical_path, output))
    if output.exists():
        raise ValueError('Immutable output already exists')
    receipt = json.loads(receipt_path.read_bytes())
    if receipt['status'] != 'PASS':
        raise ValueError('Source authentication failed')
    entry = next(x for x in receipt['files'] if Path(x['path']).name == 'authenticated_lobbyist_evidence.ndjson.gz')
    if sha(entry['path']) != entry['sha256']:
        raise ValueError('Source changed')
    known = {}
    bindings = []
    for path, query, kind in (
        (ledger_path, "SELECT id,person,payload FROM claim WHERE source='lda_covered_position'", 'ledger'),
        (historical_path, 'SELECT id,person,raw_text FROM claim', 'historical'),
    ):
        bindings.append({'path': str(path.resolve()), 'sha256': sha(path)})
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            for ident, person, payload in db.execute(query):
                raw = json.loads(payload)['raw_text'] if kind == 'ledger' else payload
                known.setdefault((person, raw), []).append({'store': kind, 'claim_id': ident})
    cases = {}
    with gzip.open(entry['path'], 'rt', encoding='utf8') as stream:
        for ordinal, line in enumerate(stream, 1):
            row = json.loads(line)
            key = (row['person'], row['covered_position'])
            case = cases.setdefault(key, {'person': key[0], 'raw_text': key[1], 'source_rows': []})
            case['source_rows'].append(ordinal)
    counts = Counter()
    clause_counts = Counter()
    clauses = []
    result = []
    for key, case in sorted(cases.items(), key=lambda item: packed(item[0])):
        case['references'] = known.get(key, [])
        case['status'] = ('NO_COVERED_POSITION_TEXT' if not str(key[1] or '').strip()
                          else 'EXACT_EXISTING_CLAIM' if case['references']
                          else 'UNSEEN_TEXT_REVIEW')
        case['observations'] = len(case['source_rows'])
        counts[case['status']] += 1
        case['source_rows'] = ranges(case['source_rows'])
        result.append(case)
        if case['status'] == 'UNSEEN_TEXT_REVIEW':
            state = covered_state(case['raw_text'])
            case['text_classification'] = state
            if state == 'SUBSTANTIVE_CLAIM_REQUIRES_RECONCILIATION':
                for ordinal, text in enumerate(re.split(r'[;\n]+', case['raw_text'])):
                    if not text.strip():
                        continue
                    assessment = assess_clause(text, '2026-09-08')
                    clause_counts[assessment['status']] += 1
                    clauses.append({'person': case['person'], 'parent_raw_text': case['raw_text'],
                                    'clause_ordinal': ordinal, 'raw_text': text,
                                    'source_rows': case['source_rows'], 'assessment': assessment})
    # Prove every authenticated observation is preserved exactly once.
    seen = bytearray(entry['records'] + 1)
    for case in result:
        for low, high in case['source_rows']:
            for index in range(low, high + 1):
                if index < 1 or index >= len(seen) or seen[index]:
                    raise ValueError('Invalid source partition')
                seen[index] = 1
    if sum(seen) != entry['records']:
        raise ValueError('Source rows missing')
    output.mkdir(parents=True)
    file = write_gzip(output / 'career_cases.ndjson.gz', result)
    clause_file = write_gzip(output / 'unseen_career_clauses.ndjson.gz', clauses)
    report = {'status': 'PASS', 'source_receipt': {'path': str(receipt_path.resolve()), 'sha256': sha(receipt_path)},
              'source_observations': entry['records'], 'source_file': entry,
              'existing_claim_stores': bindings, 'cases': len(result), 'counts': dict(counts),
              'exact_partition': True, 'file': file, 'new_collection': False,
              'unseen_clauses': clause_file, 'clause_counts': dict(clause_counts),
              'employment_promoted': False, 'window_complete': False,
              'policy': 'Exact person ID and literal text only; all original source row references retained.'}
    (output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ('receipt', 'ledger', 'historical', 'output'):
        p.add_argument('--' + arg, required=True, type=Path)
    a = p.parse_args()
    print(json.dumps(run(a.receipt, a.ledger, a.historical, a.output), indent=2))
