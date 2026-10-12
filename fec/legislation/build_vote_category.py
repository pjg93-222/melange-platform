"""Rebuild the existing vote signal from a hash-bound Voteview snapshot.

Network-free: source acquisition is separate. Retains the 17 existing categories,
sign rules, final-passage filter and paired/announced vote treatment. Measure
type and Congress are explicit, and all prior hold/exclusion decisions survive.
"""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import argparse
import ast
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re

import pandas as pd
from paths import ROOT
from moneyball_core import dump_atomic
from legislation.collect_official_rollcall_events import canonical_category


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def measure(value, congress):
    token = re.sub(r'[.\s]', '', str(value)).lower()
    match = re.fullmatch(r'(hr|s|hjres|sjres|hconres|sconres|hres|sres)([1-9]\d*)', token)
    return f'{int(congress)}:{match[1]}:{int(match[2])}' if match else None


def passage(question):
    return bool(re.search(r'passage|suspend the rules and pass|concur', question, re.I)
                and not re.search(r'recommit|amendment', question, re.I))


def rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        yield from csv.DictReader(handle)


def retained_rules(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    rules = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {'CATLAB', 'ANTI', 'SECKW'}:
                    rules[target.id] = ast.literal_eval(node.value)
    if set(rules) != {'CATLAB', 'ANTI', 'SECKW'}:
        raise ValueError('Existing vote-category rule constants are missing')
    return rules


def build(root, source, archive, historical_exclusions, names_path, output):
    root, source, archive, output = map(Path, (root, source, archive, output))
    source_receipt = load(source / 'receipt.json')
    from legislation import sentinel_admissions as SA
    admissions=SA.load(root)
    required = {f'HS{c}_{kind}.csv' for c in (117, 118, 119) for kind in ('members', 'rollcalls', 'votes')}
    if source_receipt.get('status') != 'PASS' or {Path(v['path']).name for v in source_receipt['files']} != required:
        raise ValueError('Incomplete Voteview source snapshot')
    dependencies = [source / 'receipt.json', archive, Path(historical_exclusions), Path(names_path),
                    root / 'data/legislation/cfpb_bill_directions.csv',
                    root / 'legislation_exclusion_ledger.ndjson', root / 'legislation_hold_ledger.ndjson',
                    root / 'fec/legislation/vote_by_category.py', Path(__file__).resolve()]
    if (root/SA.RELATIVE).exists(): dependencies.append(root/SA.RELATIVE)
    for record in source_receipt['files']:
        path = source / Path(record['path']).name
        if sha(path) != record['sha256']:
            raise ValueError(f'Source snapshot bytes changed: {path}')
        dependencies.append(path)
    before = {str(path.resolve()): sha(path) for path in dependencies}
    rules = retained_rules(root / 'fec/legislation/vote_by_category.py')
    labels, anti, keywords = rules['CATLAB'], rules['ANTI'], rules['SECKW']
    category_ids = {label: category for category, label in labels.items()}
    archived = pd.read_excel(archive, sheet_name='All Bills')
    bill2cat = defaultdict(set)
    rejected = Counter()
    for row in archived.to_dict('records'):
        key = measure(row['Name'], row['Congress'])
        category = next((i for i, word in keywords if word in str(row['Section']).lower()), None)
        if key and category:
            bill2cat[key].add(category)
        else:
            rejected['archive_rows_without_supported_measure_or_existing_category'] += 1
    excluded = {measure(row['bill'], row['congress']) for row in rows(historical_exclusions)}
    excluded.discard(None)
    category_exclusions = set()
    for relative in ('legislation_exclusion_ledger.ndjson', 'legislation_hold_ledger.ndjson'):
        for line in (root / relative).read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = row['canonical_key']
            if row.get('decision_scope') == 'candidate':
                excluded.add(key)
            else:
                values = row.get('category_ids') or [row.get('query_id')]
                for value in values:
                    category = category_ids.get(canonical_category(value))
                    if category:
                        category_exclusions.add((key, category))
    for key in excluded:
        bill2cat.pop(key, None)
    admitted_directions={}
    for key,bill in admissions['bills'].items():
        if key in excluded: raise ValueError('automatic admission conflicts with historical vote exclusion')
        for row in admissions['decisions'][key]['categories']:
            category=category_ids.get(canonical_category(row['category']))
            if category is None:
                raise ValueError('admitted category has no vote measurement mapping: '+row['category'])
            bill2cat[key].add(category)
            admitted_directions[(key,category)]=row['direction']
    for key, category in category_exclusions:
        bill2cat[key].discard(category)
    # The legacy direction table has no Congress column. Bind a direction only
    # where its exact typed measure occurs in one Congress in the CFPB universe.
    cfpb = {}
    for row in rows(root / 'data/legislation/cfpb_bill_directions.csv'):
        token = measure(row['Bill'], 119)
        candidates = [key for key, cats in bill2cat.items() if 13 in cats and token and key.split(':', 1)[1] == token.split(':', 1)[1]]
        if len(candidates) == 1 and row['Direction'].strip() in {'1', '-1'}:
            value = int(row['Direction'])
            if candidates[0] in cfpb and cfpb[candidates[0]] != value:
                raise ValueError('Conflicting CFPB directions')
            cfpb[candidates[0]] = value
        elif len(candidates) > 1:
            rejected['cfpb_directions_ambiguous_across_congresses'] += 1
    names = load(names_path)
    identities, rollcalls, latest, non_legislators = {}, {}, {}, set()
    for congress in (117, 118, 119):
        for row in rows(source / f'HS{congress}_members.csv'):
            if row['chamber'] not in {'House', 'Senate'}:
                non_legislators.add((congress, row['icpsr']))
                continue
            key = (congress, row['chamber'], row['icpsr'])
            bio = row.get('bioguide_id') or ''
            if key in identities and identities[key] != bio:
                raise ValueError(f'Ambiguous member identity: {key}')
            identities[key] = bio
            if bio:
                names.setdefault(bio, row['bioname'])
        for row in rows(source / f'HS{congress}_rollcalls.csv'):
            chamber = row['chamber']
            if congress == 119:
                latest[chamber] = max(latest.get(chamber, ''), row['date'])
            key = measure(row['bill_number'], congress)
            if not key or not bill2cat.get(key) or not passage(row['vote_question']):
                rejected['rollcalls_outside_accepted_final_passage'] += 1
                continue
            event = (congress, chamber, row['rollnumber'])
            if event in rollcalls:
                raise ValueError(f'Duplicate rollcall event: {event}')
            rollcalls[event] = (key, row)
    freshness_floor = datetime.fromtimestamp(archive.stat().st_mtime, timezone.utc).date() - timedelta(days=30)
    if set(latest) != {'House', 'Senate'} or any(datetime.fromisoformat(date).date() < freshness_floor for date in latest.values()):
        raise ValueError(f'Vote source is stale relative to archive: {latest}; floor={freshness_floor}')
    aggregate, vote_rows, seen = defaultdict(lambda: [0, 0]), [], set()
    member_names = {}
    for congress in (117, 118, 119):
        for row in rows(source / f'HS{congress}_votes.csv'):
            event = (congress, row['chamber'], row['rollnumber'])
            if event not in rollcalls:
                continue
            key, roll = rollcalls[event]
            if (congress, row['icpsr']) in non_legislators:
                rejected['non_legislator_positions'] += 1
                continue
            bio = identities.get((congress, row['chamber'], row['icpsr']))
            code = int(row['cast_code'])
            if code == 0:
                rejected['not_member_at_vote'] += 1
                continue
            if not bio or not re.fullmatch(r'[A-Z]\d{6}', bio):
                raise ValueError(f'Unresolved voting member: {event}, {row["icpsr"]}')
            identity = (*event, bio)
            if identity in seen:
                raise ValueError(f'Duplicate member vote: {identity}')
            seen.add(identity)
            vote = 'Yea' if code in (1, 2, 3) else 'Nay' if code in (4, 5, 6) else 'Present' if code in (7, 8) else 'NotVoting'
            vote_rows.append({'member': names[bio], 'bioguide': bio, 'congress': congress,
                'chamber': row['chamber'], 'rollnumber': row['rollnumber'], 'bill_number': roll['bill_number'],
                'canonical_key': key, 'vote_question': roll['vote_question'], 'date': roll['date'], 'vote': vote,
                'cast_code': code, 'session': roll['session'], 'clerk_rollnumber': roll['clerk_rollnumber']})
            if vote not in {'Yea', 'Nay'}:
                rejected['non_yea_nay_member_votes'] += 1
                continue
            for category in bill2cat[key]:
                direction = admitted_directions.get((key,category), cfpb.get(key, 0) if category == 13 else -1 if category in anti else 1)
                if not direction:
                    rejected['cfpb_vote_contributions_without_unambiguous_direction'] += 1
                    continue
                cell = aggregate[(bio, category)]
                cell[0] += (1 if vote == 'Yea' else -1) * direction
                cell[1] += 1
                member_names[bio] = names[bio]
    votes = defaultdict(dict)
    for (bio, category), (total, count) in sorted(aggregate.items()):
        votes[bio][labels[category]] = {'lean': total / count, 'n': count}
    if not votes or not vote_rows:
        raise ValueError('No accepted vote evidence')
    if any(sha(Path(path)) != digest for path, digest in before.items()):
        raise ValueError('An input changed while votes were built')
    output.mkdir(parents=True, exist_ok=True)
    publishing = output.resolve() == root.resolve()
    csv_path = output / ('votes/cu_rollcall_votes_clean.csv' if publishing else 'cu_rollcall_votes_clean.csv')
    vote_path = output / 'vote_category_lean.json'
    receipt_path = output / ('data/legislation/vote_refresh_receipt.json' if publishing else 'vote_refresh_receipt.json')
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = csv_path.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(vote_rows[0]))
        writer.writeheader()
        writer.writerows(sorted(vote_rows, key=lambda r: (r['congress'], r['chamber'], int(r['rollnumber']), r['bioguide'])))
    os.replace(temporary, csv_path)
    dump_atomic({'name_of': member_names, 'votes': dict(votes)}, str(vote_path))
    receipt = {'schema': 'vote-category-refresh-v1', 'status': 'PASS',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'Voteview Congressional Roll-Call Votes Database',
        'source_documentation': 'https://voteview.com/articles/data_help_votes',
        'latest_source_vote_dates': latest, 'freshness_floor': str(freshness_floor),
        'archive': str(archive.resolve()), 'inputs_sha256': before,
        'counts': {'accepted_rollcalls': len(rollcalls), 'member_votes': len(vote_rows),
                   'members_with_categorized_votes': len(votes), 'category_cells': len(aggregate),
                   'excluded_or_held_measures': len(excluded)},
        'rejections': dict(rejected), 'retained_model_rules': {**rules, 'ANTI': sorted(anti)},
        'outputs_sha256': {vote_path.name: sha(vote_path), csv_path.name: sha(csv_path)}}
    dump_atomic(receipt, str(receipt_path))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--historical-exclusions', type=Path)
    parser.add_argument('--names', type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument('--output-dir', type=Path)
    destination.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    root = Path(ROOT)
    config_path = root / 'data/legislation/vote_source_snapshot.json'
    config = load(config_path) if config_path.is_file() else {}
    source = args.source_dir or config.get('source_dir')
    exclusions = args.historical_exclusions or config.get('historical_exclusions')
    names = args.names or config.get('names')
    if not all((source, exclusions, names)):
        parser.error('source-dir, historical-exclusions and names are required until a source snapshot is configured')
    archive = args.archive or sorted(root.glob('congress_bills_archive_*.xlsx'))[-1]
    receipt = build(root, source, archive, exclusions, names, root if args.publish else args.output_dir)
    print(json.dumps({'status': receipt['status'], 'counts': receipt['counts'],
                      'latest_source_vote_dates': receipt['latest_source_vote_dates'], 'rejections': receipt['rejections']}, indent=2))


if __name__ == '__main__':
    main()
