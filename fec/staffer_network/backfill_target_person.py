"""Fetch and reconcile a bounded, exact source-person LDA filing history."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time
from urllib.parse import urlparse, parse_qs

import requests

from .compact_existing_review import packed, sha


def fetch(person_id, output, reason, max_filings=500, year=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Immutable output exists')
    output.mkdir(parents=True)
    root = 'https://lda.gov/api/v1/filings/'
    counts = []
    twins = []
    sources = []
    for twin in ('A', 'B'):
        folder = output / twin
        folder.mkdir()
        url = root + '?lobbyist_id=' + str(person_id) + '&page_size=25'
        if year is not None:
            url += '&filing_year=' + str(year)
        filings = {}
        pages = set()
        while url:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            if parsed.scheme != 'https' or parsed.netloc != 'lda.gov' or parsed.path != '/api/v1/filings/' or query.get('lobbyist_id') != [str(person_id)]:
                raise ValueError('Pagination escaped exact-person scope')
            if year is not None and query.get('filing_year') != [str(year)]:
                raise ValueError('Pagination escaped target year')
            if url in pages:
                raise ValueError('Pagination cycle')
            pages.add(url)
            response = requests.get(url, timeout=40)
            response.raise_for_status()
            data = response.json()
            count = data['count']
            if count > max_filings:
                raise ValueError('Target exceeds bounded filing limit')
            path = folder / ('page_%05d.json' % len(pages))
            path.write_bytes(response.content)
            sources.append({'path': str(path.resolve()), 'sha256': sha(path), 'url': url})
            for filing in data['results']:
                if year is not None and filing['filing_year'] != year:
                    raise ValueError('Filing outside target year')
                uuid = filing['filing_uuid']
                if uuid in filings:
                    raise ValueError('Duplicate filing UUID')
                filings[uuid] = filing
            url = data.get('next')
            time.sleep(4.2)
        if len(filings) != count:
            raise ValueError('Source count and collected UUIDs differ')
        counts.append(count)
        twins.append(filings)
    if twins[0] != twins[1] or counts[0] != counts[1]:
        raise ValueError('Independent fetches disagree; retained for review')
    year_counts = Counter(filing['filing_year'] for filing in twins[0].values())
    held = [uuid for uuid, filing in twins[0].items() if not any(
        item.get('lobbyist', {}).get('id') == person_id
        for activity in filing.get('lobbying_activities', [])
        for item in activity.get('lobbyists', []))]
    report = {'status': 'PASS', 'person_id': 'lda:lobbyist:' + str(person_id),
              'reason': reason, 'filings': counts[0], 'filing_years': dict(sorted(year_counts.items())),
              'exact_uuid_and_payload_twins': True, 'sources': sources,
              'explicitly_named_filings': counts[0] - len(held),
              'held_not_named_in_filing': held,
              'scope': 'Exact source ID only; other same-name IDs are not assumed identical.',
              'employment_promoted': False, 'production_activated': False}
    report['target_year'] = year
    (output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--person-id', required=True, type=int)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--reason', required=True)
    a = p.parse_args()
    print(packed(fetch(a.person_id, a.output, a.reason)))
