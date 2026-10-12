"""Resume only reviewed historical targets with already-established source IDs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3

from .backfill_target_person import fetch


def run(root, review, output):
    root, review, output = map(Path, (root, review, output))
    decisions = json.loads(review.read_bytes())['decisions']
    with sqlite3.connect((root / 'exhaustive-20260908-v2/evidence.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as db:
        people = {p for p, in db.execute('SELECT id FROM person')}
    counts = Counter(int(d['person'].rsplit(':', 1)[-1]) for d in decisions
        if d['status'] == 'HELD_TARGET_HISTORY_NOT_YET_VERIFIED' and 'person:' + d['person'] in people)
    prior = json.loads((root / 'targeted-2018-career-backfill-v1/manifest.json').read_bytes())
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'status': 'RUNNING', 'scope': 'Exact existing source identities; 2018 only',
        'review': str(review), 'targets': len(counts), 'clauses': sum(counts.values()),
        'completed': list(prior['completed']), 'new_completed': 0, 'failures': []}
    def save():
        temp = output / 'manifest.tmp'
        temp.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf8')
        temp.replace(output / 'manifest.json')
    save()
    for person, clauses in counts.most_common():
        folder = output / str(person)
        try:
            if (folder / 'receipt.json').exists():
                result = json.loads((folder / 'receipt.json').read_bytes())
                if result['status'] != 'PASS':
                    raise ValueError('Prior target failed')
            elif folder.exists():
                raise ValueError('Incomplete prior attempt retained for review')
            else:
                result = fetch(person, folder,
                    'Resolve %d held bounded career clauses for an existing exact source identity' % clauses,
                    year=2018)
            manifest['completed'].append({'person': person, 'filings': result['filings'],
                'receipt': str((folder / 'receipt.json').resolve())})
            manifest['new_completed'] += 1
            print(json.dumps({'person': person, 'filings': result['filings'],
                'completed': manifest['new_completed'], 'targets': len(counts)}), flush=True)
        except Exception as error:
            manifest['failures'].append({'person': person, 'error': str(error)})
            print(json.dumps(manifest['failures'][-1]), flush=True)
        save()
    manifest['status'] = 'PASS' if not manifest['failures'] else 'PARTIAL'
    save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('root', 'review', 'output'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    run(args.root, args.review, args.output)
