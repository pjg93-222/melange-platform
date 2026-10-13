"""Read-only checks of source preservation in current display/feed formats."""
from __future__ import annotations
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import to_host  # noqa: E402

import hashlib
import json
import csv
from pathlib import Path
from datetime import datetime, timezone, timedelta


def personal_source_errors(source: dict, published: dict) -> list[str]:
    errors = []
    if {name for name, value in published.items() if value} != set(source):
        errors.append('personal member coverage differs')
    for name, raw in source.items():
        value = published.get(name)
        if not isinstance(value, dict):
            errors.append(f'{name}: personal payload missing')
            continue
        if {k: v for k, v in value.items() if k != 'display_dedupe'} != raw:
            errors.append(f'{name}: source payload changed')
        receipt = value.get('display_dedupe')
        if receipt is None:
            continue
        items = raw.get('items') or []
        suppressed = receipt.get('suppressed_indices') or []
        clusters = receipt.get('clusters') or []
        valid_index = lambda i: type(i) is int and 0 <= i < len(items) and items[i].get('display_eligible') is True
        if (receipt.get('schema') != 'personal-display-dedupe-v1'
                or receipt.get('suppressed_count') != len(suppressed)
                or any(not valid_index(i) for i in suppressed)
                or len(set(suppressed)) != len(suppressed)):
            errors.append(f'{name}: invalid display suppression receipt')
            continue
        assigned, representatives = [], []
        for cluster in clusters:
            representative = cluster.get('representative_index')
            members = cluster.get('suppressed_indices') or []
            if (not valid_index(representative) or not members
                    or any(not valid_index(i) for i in members)
                    or representative in members):
                errors.append(f'{name}: invalid display cluster')
                continue
            if any(items[i].get('category') != items[representative].get('category') for i in members):
                errors.append(f'{name}: cross-category display suppression')
            representatives.append(representative)
            assigned.extend(members)
        if (sorted(assigned) != sorted(suppressed)
                or len(set(representatives)) != len(representatives)
                or set(representatives) & set(suppressed)):
            errors.append(f'{name}: display cluster partition differs')
    return errors


def vote_source_errors(root: Path) -> list[str]:
    root = Path(root)
    path = root / 'data/legislation/vote_refresh_receipt.json'
    try:
        receipt = json.loads(path.read_text(encoding='utf-8-sig'))
        if receipt.get('schema') != 'vote-category-refresh-v1' or receipt.get('status') != 'PASS':
            return ['vote refresh receipt is not PASS']
        archives = sorted(root.glob('congress_bills_archive_*.xlsx'))
        archive = Path(receipt['archive'])
        if not archives or archive.resolve() != archives[-1].resolve():
            return ['vote refresh does not use the current bill archive']
        errors = []
        inputs = receipt.get('inputs_sha256') or {}
        config = json.loads((root / 'data/legislation/vote_source_snapshot.json').read_text(encoding='utf-8-sig'))
        source_dir = Path(config['source_dir'])
        required = [archive, source_dir / 'receipt.json', Path(config['historical_exclusions']),
                    Path(config['names']), root / 'data/legislation/cfpb_bill_directions.csv',
                    root / 'legislation_exclusion_ledger.ndjson', root / 'legislation_hold_ledger.ndjson',
                    root / 'fec/legislation/vote_by_category.py', root / 'fec/legislation/build_vote_category.py']
        from legislation import sentinel_admissions as SA
        if (root / SA.RELATIVE).is_file():
            required.append(root / SA.RELATIVE)
        required.extend(source_dir / f'HS{c}_{kind}.csv'
                        for c in (117, 118, 119) for kind in ('members', 'rollcalls', 'votes'))
        if set(inputs) != {str(p.resolve()) for p in required}:
            errors.append('incomplete vote input hash coverage')
        for name, expected in inputs.items():
            source = Path(name)
            if not source.is_file() or file_sha256(source) != expected:
                errors.append(f'vote input bytes differ: {source.name}')
        outputs = {'vote_category_lean.json': root / 'vote_category_lean.json',
                   'cu_rollcall_votes_clean.csv': root / 'votes/cu_rollcall_votes_clean.csv'}
        hashes = receipt.get('outputs_sha256') or {}
        if set(hashes) != set(outputs):
            errors.append('incomplete vote output hash coverage')
        for name, source in outputs.items():
            if not source.is_file() or file_sha256(source) != hashes.get(name):
                errors.append(f'vote output bytes differ: {name}')
        floor = datetime.fromtimestamp(archive.stat().st_mtime, timezone.utc).date() - timedelta(days=30)
        latest = receipt.get('latest_source_vote_dates') or {}
        observed = {}
        with (source_dir / 'HS119_rollcalls.csv').open(encoding='utf-8-sig', newline='') as handle:
            for row in csv.DictReader(handle):
                if row['chamber'] in {'House', 'Senate'}:
                    observed[row['chamber']] = max(observed.get(row['chamber'], ''), row['date'])
        if latest != observed:
            errors.append('declared vote coverage dates differ from source rollcalls')
        if set(observed) != {'House', 'Senate'} or any(datetime.fromisoformat(date).date() < floor for date in observed.values()):
            errors.append('vote evidence is stale relative to bill archive')
        return errors
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return [f'vote refresh provenance missing or invalid: {exc}']


def file_sha256(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def press_source_errors(feed: dict, root: Path, current_intake: dict) -> list[str]:
    """Verify every cumulative run, including its immutable acceptance receipt.

    The rolling intake is bound by its input identities; each historical intake
    is separately bound by its full file hash. A newer rolling check must not
    erase earlier accepted runs or replace their recorded evidence.
    """
    root = Path(root)
    errors = []
    runs = feed.get('accepted_runs') or []
    if (feed.get('promotion_mode') != 'CUMULATIVE_ACCEPTED_RUN_LEDGER'
            or not runs or (feed.get('counts') or {}).get('accepted_runs') != len(runs)
            or (feed.get('contract') or {}).get('accepted_runs_are_cumulative') is not True):
        return ['missing or inconsistent cumulative accepted-run ledger']
    source_names = {'press_delta_scored.csv', 'press_delta_host_articles.csv',
                    'press_delta_article_mentions.csv'}
    all_names = source_names | {'press_network_retry_receipt.json'}
    roots, bindings = set(), {}
    for run in runs:
        source = Path(to_host(run.get('source_run') or '')).resolve()
        hashes = run.get('source_sha256') or {}
        if str(source) in roots:
            errors.append(f'{source.name}: duplicate accepted source run')
        roots.add(str(source))
        if set(hashes) != all_names:
            errors.append(f'{source.name}: incomplete source hash set')
        for name in all_names:
            path = source / name
            if not path.is_file() or file_sha256(path) != hashes.get(name):
                errors.append(f'{source.name}: source bytes differ: {name}')
        intake_binding = run.get('collection_intake') or {}
        intake_path = Path(to_host(intake_binding.get('path') or ''))
        if not intake_path.is_file() or file_sha256(intake_path) != intake_binding.get('sha256'):
            errors.append(f'{source.name}: acceptance receipt bytes differ')
            continue
        try:
            intake = json.loads(intake_path.read_text(encoding='utf-8-sig'))
        except (ValueError, OSError):
            errors.append(f'{source.name}: invalid acceptance receipt')
            continue
        inputs = intake.get('inputs') or {}
        expected = {'press_delta_root': str(source),
                    'press_retry_receipt_sha256': hashes.get('press_network_retry_receipt.json'),
                    'press_source_sha256': {name: hashes.get(name) for name in source_names}}
        if (intake.get('schema') != 'collection-intake-receipt-v1'
                or intake.get('status') != 'ACCEPTED'
                or Path(to_host(inputs.get('press_delta_root') or '')).resolve() != source
                or inputs.get('press_retry_receipt_sha256') != expected['press_retry_receipt_sha256']
                or inputs.get('press_source_sha256') != expected['press_source_sha256']):
            errors.append(f'{source.name}: acceptance/source bindings differ')
        bindings[str(source)] = expected
    current = current_intake.get('inputs') or {}
    current_root = str(Path(to_host(current.get('press_delta_root') or '')).resolve())
    expected = bindings.get(current_root)
    if (current_intake.get('schema') != 'collection-intake-receipt-v1'
            or current_intake.get('status') != 'ACCEPTED' or not expected
            or any(current.get(k) != expected[k] for k in ('press_retry_receipt_sha256', 'press_source_sha256'))):
        errors.append('current accepted intake is absent or differs from cumulative ledger')
    for path in (root / 'data/engine/press_collection_receipts').glob('press_refresh_*.json'):
        try:
            document = json.loads(path.read_text(encoding='utf-8-sig'))
            archived_root = str(Path(to_host(document['inputs']['press_delta_root'])).resolve())
            if document.get('status') != 'ACCEPTED' or archived_root not in roots:
                errors.append(f'{path.name}: archived accepted run omitted')
        except (ValueError, KeyError, OSError, TypeError):
            errors.append(f'{path.name}: invalid archived acceptance receipt')
    return errors
