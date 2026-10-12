"""Compact existing review queues into source-bound cases; never re-adjudicate them."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def case_key(role, row, ordinal):
    if role == 'career_claim_review':
        # Source IDs remain separate: grouping is a review view, not an identity merge.
        return ('career', row['person'])
    if role == 'lda_career_coverage':
        return ('coverage', row['source_person_id'])
    if row.get('kind') == 'client_identity':
        record = row.get('evidence', {}).get('source_record', {})
        name = record.get('display_name')
        if name:
            # Exact spelling only. Jurisdictions/conflicts remain in referenced evidence.
            return ('organization_comparison_only', name)
    return ('identity', row.get('subject') or row.get('source') or f'row:{ordinal}')


def ranges(numbers):
    result = []
    for number in numbers:
        if result and number == result[-1][1] + 1:
            result[-1][1] = number
        else:
            result.append([number, number])
    return result


def compact(manifest_path, release_path, output):
    manifest_path, release_path, output = map(Path, (manifest_path, release_path, output))
    if output.exists():
        raise ValueError('Immutable output already exists')
    manifest = json.loads(manifest_path.read_bytes())
    release = json.loads(release_path.read_bytes())
    if manifest.get('status') != 'PASS' or release.get('status') != 'PASS':
        raise ValueError('Prior review and release must pass')
    validation = release['validation']
    if manifest['candidate_sha256'] != validation['candidate_sha256']:
        raise ValueError('Queue does not match released candidate')
    if manifest['ledger_sha256'] != release['inputs']['ledger_sha256']:
        raise ValueError('Queue and release bind different ledgers')
    # Authenticate the accepted result through existing receipts, without replaying sources.
    for path_key, hash_key in [('evaluation_path', 'evaluation_sha256'),
                               ('test_receipt_path', 'test_receipt_sha256')]:
        if sha(validation[path_key]) != validation[hash_key]:
            raise ValueError('Accepted validation receipt changed')
        if json.loads(Path(validation[path_key]).read_bytes()).get('status') != 'PASS':
            raise ValueError('Accepted validation receipt did not pass')
    for key in ('date_refinements', 'date_scope_adjudications'):
        binding = manifest.get(key)
        if binding and sha(binding['path']) != binding['sha256']:
            raise ValueError(f'{key} changed')
    output.mkdir(parents=True)
    receipt = {'status': 'INCOMPLETE', 'policy': 'Source-bound review views; no identity, date, or employment decisions changed.',
               'authentication_scope': 'Existing queue bytes, accepted evaluation/test receipts, date decisions, and their shared candidate/ledger bindings. Not independent truth certification.',
               'input_manifest': {'path': str(manifest_path.resolve()), 'sha256': sha(manifest_path)},
               'accepted_release': {'path': str(release_path.resolve()), 'sha256': sha(release_path)},
               'files': [], 'source_records_removed': 0, 'identities_merged': 0,
               'dates_invented': 0, 'new_collection': False, 'live_activated': False}
    for entry in manifest['files']:
        source = Path(entry['path'])
        digest = sha(source)
        if digest != entry['sha256']:
            raise ValueError('Review input changed: ' + str(source))
        cases = {}
        statuses = Counter()
        identity = hashlib.sha256()
        count = 0
        with gzip.open(source, 'rt', encoding='utf8') as stream:
            for count, line in enumerate(stream, 1):
                row = json.loads(line)
                key = case_key(entry['role'], row, count)
                case = cases.setdefault(key, {'kind': key[0], 'subject': key[1],
                    'source_rows': [], 'statuses': Counter(), 'sources': Counter()})
                case['source_rows'].append(count)
                state = row.get('status', 'REVIEW')
                case['statuses'][state] += 1
                case['sources'][row.get('source', row.get('kind', entry['role']))] += 1
                statuses[state] += 1
                # Ordered row digest verifies reconstruction without copying source evidence.
                identity.update(line.encode('utf8'))
        if count != entry['records'] or dict(statuses) != entry['statuses']:
            raise ValueError('Source queue accounting mismatch')
        target = output / (entry['role'] + '.cases.ndjson.gz')
        with gzip.open(target, 'wt', encoding='utf8', compresslevel=6) as stream:
            for key, case in sorted(cases.items()):
                case['id'] = hashlib.sha256(packed(key).encode()).hexdigest()
                case['evidence_rows'] = len(case['source_rows'])
                case['source_rows'] = ranges(case['source_rows'])
                stream.write(packed(case) + '\n')
        # Independently decode output and prove an exact partition of input rows.
        seen = bytearray(count + 1)
        actual_statuses = Counter()
        actual_cases = 0
        with gzip.open(target, 'rt', encoding='utf8') as stream:
            for line in stream:
                case = json.loads(line)
                actual_cases += 1
                actual_statuses.update(case['statuses'])
                assigned = 0
                for low, high in case['source_rows']:
                    if not 1 <= low <= high <= count:
                        raise ValueError('Invalid reference range')
                    for index in range(low, high + 1):
                        if seen[index]:
                            raise ValueError('Evidence row assigned twice')
                        seen[index] = 1
                        assigned += 1
                if assigned != case['evidence_rows']:
                    raise ValueError('Case evidence count differs')
        if sum(seen) != count or actual_statuses != statuses or actual_cases != len(cases):
            raise ValueError('Output partition failed')
        if sha(source) != digest:
            raise ValueError('Source changed during compaction')
        item = {'role': entry['role'], 'source_path': str(source), 'source_sha256': digest,
                'source_rows': count, 'cases': len(cases), 'source_row_numbering': 'One-based inclusive ranges',
                'source_uncompressed_sha256': identity.hexdigest(),
                'path': str(target.resolve()), 'sha256': sha(target), 'bytes': target.stat().st_size,
                'exact_partition': 'PASS', 'statuses_unchanged': True}
        receipt['files'].append(item)
        print(packed({'role': entry['role'], 'rows': count, 'cases': len(cases), 'verification': 'PASS'}), flush=True)
    receipt['status'] = 'PASS'
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--release', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    compact(args.manifest, args.release, args.output)
