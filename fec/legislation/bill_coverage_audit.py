# -*- coding: utf-8 -*-
"""Read-only coverage / orphan audit for the legislative plane (2026-07-19).

Answers: is every bill sorted into a slot, and is any bill missing or orphaned?

Nothing here mutates state. The script reads the routing ledger, the archive
workbook, and the config CSVs, then prints an ASCII report. It writes a file
only when --json <path> is passed explicitly.

Import-safe (unlike most of fec/) -- the body is under a __main__ guard.
Only bill_rules and paths are imported; both are pure. moneyball_core is
parsed with ast rather than imported, because importing it executes it.

Run against live data from a worktree:
    set MONEYBALL_ROOT=C:\\Users\\pgoff\\OneDrive\\Desktop\\Melange Systems
    python fec/legislation/bill_coverage_audit.py
"""
import argparse
import ast
import collections
import csv
import glob
import json
import math
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bill_rules as BR  # noqa: E402  (pure module: constants + defs only)
from legislation import bill_archive_provenance as BAP  # noqa: E402  (import-safe pure helpers)
import paths as P        # noqa: E402

PASS, FAIL, WARN = [], [], []
ROLE_RANK = {'other': 0, 'cosponsor': 1, 'original-cosponsor': 2, 'sponsor': 3}
# CU Tax Status measured zero archive rows on 2026-07-24. CDFI Fund was removed
# from this allowlist because 18 archive rows reach it through BUILTIN_OVERRIDES.
ARCHIVE_ZERO_ALLOWLIST = frozenset({'CU Tax Status'})
# Query-set absence is distinct from effective archive routing. CDFI has no
# configured API-browser query even though overrides route archive rows there;
# CU Tax Status has neither a numbered sheet nor a query set.
QUERY_SET_ZERO_ALLOWLIST = frozenset({'CDFI Fund', 'CU Tax Status'})
NON_INTEGRAL_CONGRESS = 'non-integral congress'
INFORMAL_SENATE_STYLE_RE = re.compile(r'^\s*S\.?\s*R\.?\s*\d', re.I)
C14_DIRECTION_SOURCES = (
    'data/legislation/bill_directions.csv',
    'data/legislation/cu_bill_directions.json',
    'data/stance/cu_valence_registry.json',
)


class ArchiveAbort(Exception):
    """Labeled archive-structure/read failure that exits the audit with 2."""


def emit(status, name, detail=''):
    (PASS if status == 'PASS' else FAIL if status == 'FAIL' else WARN).append(name)
    print('  [%s] %s%s' % (status, name, (' -- ' + detail) if detail else ''))


def head(title):
    print('')
    print(title)
    print('-' * len(title))


def clip(text, n=120):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text if len(text) <= n else text[:n - 3] + '...'


def ascii_only(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def load_archive(root):
    """Return {source_row: {col: value}} for the newest bills archive."""
    import openpyxl
    hits = sorted(glob.glob(os.path.join(root, 'congress_bills_archive_*.xlsx')))
    if not hits:
        return None, {}
    path = hits[-1]
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ArchiveAbort(
            'archive unreadable: %s (%s)' % (
                os.path.basename(path),
                clip(exc, 160),
            )
        )
    try:
        if 'All Bills' not in book.sheetnames:
            raise ArchiveAbort(
                'archive missing required All Bills sheet: %s'
                % os.path.basename(path)
            )
        sheet = book['All Bills']
        rows, header = {}, None
        for idx, raw in enumerate(sheet.iter_rows(values_only=True), start=1):
            if idx == 1:
                header = [str(c or '').strip() for c in raw]
                continue
            if not any(
                value is not None and str(value).strip()
                for value in raw
            ):
                continue
            rows[idx] = dict(zip(header, raw))
    finally:
        book.close()
    return os.path.basename(path), rows


def parse_block_table(fec_dir):
    """Extract `blocks` and `BLOCKCAT` from moneyball_core.py WITHOUT importing it."""
    with open(os.path.join(fec_dir, 'moneyball_core.py'), encoding='utf-8') as handle:
        tree = ast.parse(handle.read())
    blocks, blockcat = None, None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            name = getattr(target, 'id', None)
            if name == 'blocks' and blocks is None:
                try:
                    blocks = ast.literal_eval(node.value)
                except Exception:
                    pass
            elif name == 'BLOCKCAT' and blockcat is None:
                try:
                    blockcat = ast.literal_eval(node.value)
                except Exception:
                    pass
    return blocks, blockcat


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def normalized_title(value):
    """Normalize act titles without punctuation/Unicode apostrophe artifacts."""
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    text = text.translate(str.maketrans({
        '\u2018': "'",
        '\u2019': "'",
        '\u201b': "'",
        '\u02bc': "'",
        '\u2032': "'",
        '\uff07': "'",
    }))
    text = ''.join(
        ' ' if unicodedata.category(character).startswith('P') else character
        for character in text
    )
    return re.sub(r'\s+', ' ', text).strip()


def congress_text(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return NON_INTEGRAL_CONGRESS
    if not math.isfinite(numeric) or not numeric.is_integer():
        return NON_INTEGRAL_CONGRESS
    return str(int(numeric))


def _iter_archive_rows(archive_rows):
    if isinstance(archive_rows, dict):
        for locator, row in archive_rows.items():
            yield locator, row
    else:
        for locator, row in enumerate(archive_rows, start=2):
            yield locator, row


def archive_instances_by_type(archive_rows):
    """Return typed bill keys mapped to distinct Congress/title instances."""
    grouped = collections.defaultdict(dict)
    for locator, row in _iter_archive_rows(archive_rows):
        typed = BR.bill_type_key(row.get('Name'))
        if typed is None:
            continue
        congress = congress_text(row.get('Congress'))
        title = re.sub(r'\s+', ' ', str(row.get('Title') or '')).strip()
        instance_key = (congress, normalized_title(title))
        grouped[typed].setdefault(instance_key, {
            'congress': congress,
            'normalized_title': instance_key[1],
            'title': title,
            'first_archive_row': locator,
        })
    return {
        key: sorted(
            instances.values(),
            key=lambda item: (
                int(item['congress']) if item['congress'].isdigit() else 999999,
                item['congress'],
                item['normalized_title'],
            ),
        )
        for key, instances in grouped.items()
    }


def _direction_value(row, names):
    for name in names:
        if name not in row:
            continue
        value = row.get(name)
        if value is not None and str(value).strip() != '':
            return True, value
    return False, None


def direction_rows_from_documents(bill_rows, valence_document, cu_document):
    """Normalize bill-bearing direction rows from the three configured sources."""
    out = []
    stats = {}

    csv_count = 0
    for index, row in enumerate(bill_rows or [], start=2):
        present, value = _direction_value(row, ('Direction', 'direction'))
        bill = row.get('Bill') or row.get('bill')
        if present and bill:
            csv_count += 1
            out.append({
                'source': 'data/legislation/bill_directions.csv',
                'locator': index,
                'bill': str(bill).strip(),
                'congress': BR._congress(
                    row.get('Congress') or row.get('congress')),
                'direction': value,
            })
    stats['data/legislation/bill_directions.csv'] = {
        'records_scanned': len(bill_rows or []),
        'direction_rows': csv_count,
        'status': 'PRESENT',
    }

    entries = (
        valence_document.get('entries', [])
        if isinstance(valence_document, dict) else []
    )
    valence_count = 0
    for index, row in enumerate(entries, start=1):
        if not isinstance(row, dict):
            continue
        present, value = _direction_value(
            row, ('Direction', 'direction', 'valence'))
        bill = (
            row.get('Bill') or row.get('bill') or row.get('bill_id')
            or row.get('BillId')
        )
        identifier_field = 'bill'
        if not bill:
            bill = row.get('key')
            identifier_field = 'key'
        if present and bill:
            valence_count += 1
            out.append({
                'source': 'data/stance/cu_valence_registry.json',
                'locator': index,
                'bill': str(bill).strip(),
                'direction': value,
                'identifier_field': identifier_field,
            })
    stats['data/stance/cu_valence_registry.json'] = {
        'records_scanned': len(entries),
        'direction_rows': valence_count,
        'status': 'PRESENT',
    }

    bills = (
        cu_document.get('bills', {})
        if isinstance(cu_document, dict) else {}
    )
    cu_count = 0
    if isinstance(bills, dict):
        for bill, row in sorted(bills.items()):
            if not isinstance(row, dict):
                continue
            present, value = _direction_value(row, ('Direction', 'direction'))
            if present:
                cu_count += 1
                out.append({
                    'source': 'data/legislation/cu_bill_directions.json',
                    'locator': bill,
                    'bill': str(bill).strip(),
                    'direction': value,
                })
    stats['data/legislation/cu_bill_directions.json'] = {
        'records_scanned': len(bills) if isinstance(bills, dict) else 0,
        'direction_rows': cu_count,
        'status': 'PRESENT',
    }
    return out, stats


def load_direction_rows(root):
    bill_path = os.path.join(root, 'data', 'legislation', 'bill_directions.csv')
    valence_path = os.path.join(root, 'data/stance/cu_valence_registry.json')
    cu_path = os.path.join(root, 'data', 'legislation', 'cu_bill_directions.json')

    def optional_json(path):
        if not os.path.isfile(path):
            return {}, 'MISSING'
        try:
            with open(path, encoding='utf-8') as handle:
                return json.load(handle), 'PRESENT'
        except (OSError, ValueError) as exc:
            return {}, 'UNREADABLE: %s' % clip(exc, 120)

    def optional_csv(path):
        if not os.path.isfile(path):
            return [], 'MISSING'
        try:
            return read_csv(path), 'PRESENT'
        except (OSError, csv.Error, UnicodeError) as exc:
            return [], 'UNREADABLE: %s' % clip(exc, 120)

    bill_rows, bill_status = optional_csv(bill_path)
    valence_document, valence_status = optional_json(valence_path)
    cu_document, cu_status = optional_json(cu_path)
    direction_rows, stats = direction_rows_from_documents(
        bill_rows,
        valence_document,
        cu_document,
    )
    stats['data/legislation/bill_directions.csv']['status'] = bill_status
    stats['data/stance/cu_valence_registry.json']['status'] = valence_status
    stats['data/legislation/cu_bill_directions.json']['status'] = cu_status
    return direction_rows, stats


def evaluate_c14(direction_rows, archive_rows):
    """Return collision and legacy-unkeyable findings without file I/O."""
    instances_by_type = archive_instances_by_type(archive_rows)
    collisions = []
    unkeyable_directions = []
    unparsed_directions = []
    legacy_keyable_unparsed = []
    informal_senate_style_directions = []
    for row in direction_rows:
        bill = row.get('bill')
        if INFORMAL_SENATE_STYLE_RE.match(str(bill or '')):
            informal_senate_style_directions.append(dict(row))
        typed = BR.bill_type_key(bill)
        if typed is None:
            finding = dict(row)
            legacy = BR.bill_key(bill)
            if legacy is not None:
                finding['legacy_bill_key'] = list(legacy)
                finding['risk'] = (
                    'legacy consumers key this identifier while the '
                    'type-aware guard cannot classify its cross-Congress '
                    'collision risk'
                )
                legacy_keyable_unparsed.append(finding)
            unparsed_directions.append(finding)
            continue
        if BR.bill_key(bill) is None:
            finding = dict(row)
            finding['bill_type_key'] = list(typed)
            finding['archive_instances'] = instances_by_type.get(typed, [])
            unkeyable_directions.append(finding)
        instances = instances_by_type.get(typed, [])
        congresses = {item['congress'] for item in instances}
        titles = {item['normalized_title'] for item in instances}
        direction_congress = BR._congress(row.get('congress'))
        if len(congresses) >= 2 and not direction_congress:
            finding = dict(row)
            finding['bill_type_key'] = list(typed)
            finding['instance_count'] = len(instances)
            finding['instances'] = instances
            finding['same_title'] = len(titles) <= 1
            finding['titles_empty'] = all(not title for title in titles)
            collisions.append(finding)

    archive_unkeyable = collections.defaultdict(
        lambda: {'rows': 0, 'bills': set()})
    archive_unkeyable_rows = 0
    archive_non_integral_congress_rows = 0
    for _, row in _iter_archive_rows(archive_rows):
        bill = row.get('Name')
        typed = BR.bill_type_key(bill)
        if congress_text(row.get('Congress')) == NON_INTEGRAL_CONGRESS:
            archive_non_integral_congress_rows += 1
        if typed is not None and BR.bill_key(bill) is None:
            archive_unkeyable_rows += 1
            archive_unkeyable[typed]['rows'] += 1
            archive_unkeyable[typed]['bills'].add(str(bill or '').strip())
    archive_unkeyable_ids = [
        {
            'bill_type_key': list(typed),
            'bills': sorted(value['bills']),
            'archive_rows': value['rows'],
        }
        for typed, value in sorted(archive_unkeyable.items())
    ]
    return {
        'direction_rows_scanned': len(direction_rows),
        'collisions': collisions,
        'unkeyable_directions': unkeyable_directions,
        'unparsed_directions': unparsed_directions,
        'legacy_keyable_unparsed': legacy_keyable_unparsed,
        'informal_senate_style_directions': informal_senate_style_directions,
        'archive_rows_bill_key_unkeyable': archive_unkeyable_rows,
        'archive_identifiers_bill_key_unkeyable': archive_unkeyable_ids,
        'archive_rows_non_integral_congress': (
            archive_non_integral_congress_rows),
    }


def c14_clean_state(c14, direction_source_counts):
    """Return the non-collision console decision, guarding against vacuous PASS."""
    unavailable = []
    for source_name in sorted(C14_DIRECTION_SOURCES):
        source = direction_source_counts.get(source_name, {})
        status = source.get('status', 'MISSING')
        direction_rows = source.get('direction_rows', 0)
        if status != 'PRESENT' or not direction_rows:
            unavailable.append({
                'source': source_name,
                'status': status if status != 'PRESENT' else 'EMPTY',
            })
    if unavailable:
        return {
            'status': 'WARN',
            'name': (
                'C14 no collisions found BUT %d direction source(s) '
                'unavailable \u2014 PASS is vacuous'
                % len(unavailable)
            ),
            'detail': ', '.join(
                '%s (%s)' % (item['source'], item['status'])
                for item in unavailable
            ),
            'unavailable_sources': unavailable,
        }
    return {
        'status': 'PASS',
        'name': 'C14 no direction row spans multiple Congress instances',
        'detail': '%d direction rows scanned' % c14['direction_rows_scanned'],
        'unavailable_sources': [],
    }


def evaluate_c15(archive_rows):
    """Group acts with corrected Unicode/punctuation title normalization."""
    acts = collections.defaultdict(dict)
    row_count_by_title = collections.Counter()
    all_rows = 0
    blank_title_rows = 0
    non_integral_congress_rows = 0
    for _, row in _iter_archive_rows(archive_rows):
        all_rows += 1
        title = normalized_title(row.get('Title'))
        congress = congress_text(row.get('Congress'))
        if congress == NON_INTEGRAL_CONGRESS:
            non_integral_congress_rows += 1
        if not title:
            blank_title_rows += 1
            continue
        instance = (
            re.sub(r'\s+', ' ', str(row.get('Name') or '')).strip()
            + '/C' + congress
        )
        category = BR.canonical_category(row.get('Section'))
        acts[title].setdefault(instance, set()).add(category)
        row_count_by_title[title] += 1
    multi = {
        title: instances
        for title, instances in acts.items()
        if len(instances) > 1
    }
    disagreeing = {}
    for title, instances in multi.items():
        category_sets = list(instances.values())
        if any(value != category_sets[0] for value in category_sets[1:]):
            disagreeing[title] = instances
    rows_involved = sum(row_count_by_title[title] for title in disagreeing)
    examples = []
    for title in sorted(
        disagreeing,
        key=lambda value: (-row_count_by_title[value], value),
    )[:5]:
        examples.append({
            'title': title,
            'rows': row_count_by_title[title],
            'instances': {
                instance: sorted(
                    '' if category is None else category
                    for category in categories
                )
                for instance, categories in sorted(disagreeing[title].items())
            },
        })
    return {
        'disagreeing_acts': len(disagreeing),
        'multi_number_acts': len(multi),
        'disagreeing_pct': (
            round(100.0 * len(disagreeing) / len(multi), 4)
            if multi else 0.0
        ),
        'rows_involved': rows_involved,
        'all_rows': all_rows,
        'blank_title_rows': blank_title_rows,
        'non_integral_congress_rows': non_integral_congress_rows,
        'rows_involved_pct': (
            round(100.0 * rows_involved / all_rows, 4)
            if all_rows else 0.0
        ),
        'worst_examples': examples,
    }


def main():
    del PASS[:]
    del FAIL[:]
    del WARN[:]
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', dest='json_out', default=None,
                    help='optional explicit path for a machine-readable report')
    args = ap.parse_args()

    root = P.ROOT
    print('BILL COVERAGE / ORPHAN AUDIT')
    print('resolved ROOT: %s' % root)

    ledger_path = os.path.join(root, 'legislation_routing_audit.json')
    required = [ledger_path, os.path.join(root, 'archive_leg.json'),
                os.path.join(root, 'data', 'engine', 'congestion.json')]
    c14_optional = [
        os.path.join(root, 'data', 'legislation', 'bill_directions.csv'),
        os.path.join(root, 'data/stance/cu_valence_registry.json'),
        os.path.join(root, 'data', 'legislation', 'cu_bill_directions.json'),
    ]
    # the archive workbook is required too: without it C6 flags every config row dead and C8
    # degrades into a vacuous PASS derived from zero rows
    if not glob.glob(os.path.join(root, 'congress_bills_archive_*.xlsx')):
        required.append(os.path.join(root, 'congress_bills_archive_*.xlsx'))
    missing = [p for p in required if not os.path.exists(p)]
    if missing:
        print('')
        print('ABORT: inputs not found under the resolved ROOT:')
        for p in missing:
            print('  missing: %s' % p)
        print('Set MONEYBALL_ROOT to the live tree -- worktrees do not carry data.')
        return 2
    missing_c14_optional = [
        path for path in c14_optional if not os.path.isfile(path)
    ]
    if missing_c14_optional:
        print('')
        print('OPTIONAL C14 INPUT NOTICE:')
        for path in missing_c14_optional:
            print('  WARN/SKIP when C14 runs: %s' % path)

    ledger = json.load(open(ledger_path, encoding='utf-8'))
    rows = ledger['rows']
    summary = ledger['summary']
    dispositions = summary['dispositions']
    print('ledger source: %s   schema: %s' % (ledger.get('source'), ledger.get('schema')))

    try:
        archive_name, archive = load_archive(root)
    except ArchiveAbort as exc:
        print('')
        print('ABORT: %s' % ascii_only(exc))
        return 2
    print('archive read : %s (%d data rows)' % (archive_name, len(archive)))

    report = {'root': root, 'source': ledger.get('source')}
    if not archive:
        head('ARCHIVE PREFLIGHT')
        emit(
            'FAIL',
            'All Bills sheet contains zero data rows',
            'archive=%s; downstream archive checks were not run'
            % archive_name,
        )
        report['archive_preflight'] = {
            'archive_name': archive_name,
            'all_bills_data_rows': 0,
            'status': 'FAIL',
        }
        print('')
        print('=' * 66)
        print(
            'COVERAGE AUDIT - %d passed, %d FAILED, %d WARN'
            % (len(PASS), len(FAIL), len(WARN))
        )
        if args.json_out:
            with open(args.json_out, 'w', encoding='utf-8') as handle:
                json.dump(report, handle, indent=1)
            print('  report written: %s' % args.json_out)
        return 1

    # ---------------------------------------------------------------- C1
    head('C1  Row conservation')
    n_src, n_rows = ledger['source_rows'], len(rows)
    disp_sum = sum(dispositions.values())
    if n_src == n_rows == disp_sum:
        emit('PASS', 'every source row appears exactly once in the ledger',
             '%d rows, dispositions sum %d' % (n_rows, disp_sum))
    else:
        emit('FAIL', 'row conservation broken',
             'source_rows=%d len(rows)=%d disposition_sum=%d' % (n_src, n_rows, disp_sum))
    for key in sorted(dispositions, key=lambda k: -dispositions[k]):
        print('        %-24s %6d' % (key, dispositions[key]))
    report['dispositions'] = dispositions

    # ---------------------------------------------------------------- C2
    head('C2  Orphaned bills (routed to no slot)')
    orphans = [r for r in rows if r['disposition'] == 'unknown-section']
    if not orphans:
        emit('PASS', 'no bill failed to resolve a category', 'unknown-section = 0')
    else:
        emit('FAIL', 'bills with no canonical slot', '%d rows' % len(orphans))
        seen = {}
        for r in orphans:
            seen.setdefault((r['bill'], r['congress'], r.get('source_section')), r)
        for (bill, cong, sec), r in sorted(seen.items()):
            print('        %-12s %-4s section=%-40s %s' %
                  (bill, cong, ascii_only(clip(sec, 38)), ascii_only(clip(r.get('title'), 50))))
    report['orphan_rows'] = len(orphans)

    # ---------------------------------------------------------------- C3
    head('C3  All 18 slots occupied')
    eff = summary.get('effective_categories', {})
    canon = set(BR.CATEGORIES)
    absent = sorted(canon - set(eff))
    stray = sorted(set(eff) - canon)
    if not absent and not stray:
        emit('PASS', 'all 18 canonical categories present, none stray',
             '%d categories' % len(eff))
    else:
        emit('FAIL', 'category set mismatch',
             'absent=%s stray=%s' % (absent or 'none', stray or 'none'))
    sel_by_cat = collections.Counter(
        r['effective_category'] for r in rows if r['disposition'] == 'selected')
    print('        %-28s %8s %8s' % ('slot', 'rows', 'scored'))
    for cat in sorted(eff, key=lambda c: -eff[c]):
        # NOTE: summary.effective_categories counts every row that RESOLVED to the
        # slot, including deduped duplicates -- it is not the scored-event count.
        print('        %-28s %8d %8d' % (cat, eff[cat], sel_by_cat[cat]))
    report['effective_categories'] = eff
    report['selected_by_category'] = dict(sel_by_cat)

    # ---------------------------------------------------------------- C4
    head('C4  Unmatched members (evidence dropped on the floor)')
    unmatched = summary.get('unmatched_members', {})
    known = {'Stacey Plaskett', 'James Moylan'}
    lost = collections.defaultdict(set)
    for r in rows:
        if r['disposition'] == 'unmatched-member':
            lost[r['member_source']].add('%s (%s)' % (r['bill'], r['congress']))
    total_lost = sum(unmatched.values())
    unexpected = sorted(set(unmatched) - known)
    if unexpected:
        emit('FAIL', 'member outside the known non-voting-delegate set',
             ', '.join(unexpected))
    elif total_lost > 10:
        emit('FAIL', 'unmatched row count grew past the known baseline',
             '%d rows > 10' % total_lost)
    else:
        emit('WARN', 'known non-voting delegates only', '%d rows dropped' % total_lost)
    for name in sorted(unmatched):
        print('        %-22s %2d rows   %s' %
              (name, unmatched[name], ascii_only(clip(', '.join(sorted(lost[name])), 70))))
    report['unmatched_members'] = unmatched

    # ---------------------------------------------------------------- C5
    head('C5  Members with zero bill evidence')
    arch_leg = json.load(open(os.path.join(root, 'archive_leg.json'), encoding='utf-8'))
    roster = [m['member'] for m in json.load(open(os.path.join(root, 'data', 'engine', 'congestion.json'),
                                                 encoding='utf-8'))]
    zero = sorted(set(roster) - set(arch_leg))
    emit('WARN' if zero else 'PASS', 'roster coverage',
         '%d of %d members carry >=1 bill; %d carry none' %
         (len(set(roster) & set(arch_leg)), len(roster), len(zero)))
    for i in range(0, len(zero), 3):
        print('        ' + '  '.join('%-24s' % ascii_only(n) for n in zero[i:i + 3]))
    report['members_zero_evidence'] = zero

    # ---------------------------------------------------------------- C6
    head('C6  Dead config rows (match no archive bill)')
    archive_keys = set()
    for r in archive.values():
        key = BR.bill_key(r.get('Name'))
        if key:
            archive_keys.add(key)
    dead = []
    for fname, billcol in (('data/legislation/bill_overrides.csv', 'Bill'),
                           ('data/legislation/bill_directions.csv', 'Bill'),
                           ('manual_bill_evidence.csv', 'Bill')):
        for row in read_csv(os.path.join(root, fname)):
            bill = row.get(billcol)
            if BR.is_letter_evidence(bill):
                continue
            key = BR.bill_key(bill)
            if key is None or key not in archive_keys:
                dead.append((fname, bill, row.get('Category', ''), clip(row.get('Note', ''), 44)))
    if not dead:
        emit('PASS', 'every config row matches a live archive bill')
    else:
        emit('WARN', 'config rows matching no archive bill', '%d rows' % len(dead))
        for fname, bill, cat, note in dead:
            print('        %-26s %-12s %-24s %s' %
                  (fname, ascii_only(str(bill)), ascii_only(cat), ascii_only(note)))
    report['dead_config_rows'] = [list(d) for d in dead]

    # ---------------------------------------------------------------- C7
    head('C7  Exclusion forensics -- title match vs summary-only match')
    print('  Exclusions are deliberate and user-directed (bill_rules.py:59-60).')
    print('  This REPORTS them for adjudication; nothing is changed.')
    excluded = [r for r in rows if r['disposition'] == 'excluded-title']
    per_bill, summary_only = {}, []
    for r in excluded:
        src = archive.get(r['source_row'], {})
        title = str(src.get('Title') or r.get('title') or '')
        blurb = str(src.get('bill_summary') or '')
        phrase = r.get('reason', '')
        in_title = phrase and phrase.lower() in title.lower()
        in_blurb = phrase and phrase.lower() in blurb.lower()
        key = (r['bill'], r['congress'])
        if key in per_bill:
            continue
        would_be = BR.canonical_category(r.get('source_section'))
        per_bill[key] = {'phrase': phrase, 'title': title, 'in_title': bool(in_title),
                         'in_summary_only': bool(in_blurb and not in_title),
                         'would_be_slot': would_be, 'section': r.get('source_section'),
                         'summary': blurb}
        if in_blurb and not in_title:
            summary_only.append((key, per_bill[key]))

    by_phrase = collections.Counter(v['phrase'] for v in per_bill.values())
    print('')
    print('  distinct bills excluded, by phrase:')
    for phrase, n in by_phrase.most_common():
        print('        %-38s %3d bills' % (ascii_only(phrase), n))

    by_slot = collections.Counter(v['would_be_slot'] or '(no slot)' for v in per_bill.values())
    print('')
    print('  slot those excluded bills WOULD have occupied:')
    for slot, n in by_slot.most_common():
        print('        %-28s %3d bills' % (slot, n))

    if not summary_only:
        emit('PASS', 'no exclusion fired on summary text alone',
             '%d distinct bills excluded, all matched on title' % len(per_bill))
    else:
        emit('WARN', 'exclusions fired on SUMMARY text only (over-exclusion risk)',
             '%d bills' % len(summary_only))
        for (bill, cong), v in summary_only:
            print('')
            print('        %s (%s) -> would be %s' % (bill, cong, v['would_be_slot']))
            print('          title  : %s' % ascii_only(clip(v['title'], 90)))
            print('          phrase : "%s" (summary only)' % ascii_only(v['phrase']))
            low = v['summary'].lower()
            at = low.find(v['phrase'].lower())
            ctx = v['summary'][max(0, at - 60):at + len(v['phrase']) + 60]
            print('          context: ...%s...' % ascii_only(clip(ctx, 130)))

    privacy = [(k, v) for k, v in per_bill.items() if v['would_be_slot'] == 'Data Privacy & Cyber']
    print('')
    if privacy:
        emit('WARN', 'excluded bills whose slot would be Data Privacy & Cyber',
             '%d bills' % len(privacy))
        for (bill, cong), v in sorted(privacy):
            print('        %-12s %-4s %-34s %s' %
                  (bill, cong, ascii_only(clip(v['phrase'], 32)),
                   ascii_only(clip(v['title'], 46))))
    else:
        emit('PASS', 'no excluded bill would have landed in Data Privacy & Cyber')
    report['excluded_by_phrase'] = dict(by_phrase)
    report['excluded_by_would_be_slot'] = dict(by_slot)
    report['summary_only_exclusions'] = [
        {'bill': k[0], 'congress': k[1], 'phrase': v['phrase'],
         'title': v['title'], 'would_be_slot': v['would_be_slot']}
        for k, v in summary_only]

    # ---------------------------------------------------------------- C8
    head('C8  bill_key collisions (H.R.N vs H.Res.N vs H.J.Res.N)')
    # BR.key_collisions() implements a related type-aware rule; the two are deliberately separate today.
    print('  bill_key() keeps only (chamber, number) and discards the R/J/Res segment')
    print('  (bill_rules.py:129-132), so distinct bill types can collapse to one key.')
    groups = collections.defaultdict(set)
    for r in archive.values():
        name = str(r.get('Name') or '').strip()
        key = BR.bill_key(name)
        if key:
            groups[(key, str(r.get('Congress') or '').strip())].add(name)
    collisions = {k: v for k, v in groups.items() if len(v) > 1}
    selected_names = set()
    for r in rows:
        if r['disposition'] == 'selected':
            selected_names.add((BR.bill_key(r['bill']), str(r['congress']), r['bill']))
    hot = []
    for (key, cong), names in collisions.items():
        involved = {n for n in names if (key, cong, n) in selected_names}
        if len(involved) > 1:
            hot.append(((key, cong), names))
    if not collisions:
        emit('PASS', 'no two distinct bill ids share a key within a Congress')
    elif hot:
        emit('FAIL', 'colliding ids BOTH reached the selected set (evidence merged)',
             '%d keys' % len(hot))
        for (key, cong), names in hot:
            print('        %s/%s  <-  %s' % (key, cong, ', '.join(sorted(names))))
    else:
        emit('WARN', 'collisions exist but not among selected rows',
             '%d keys' % len(collisions))
        for (key, cong), names in sorted(collisions.items())[:20]:
            print('        %s/%s  <-  %s' % (key, cong, ', '.join(sorted(names))))
    report['bill_key_collisions'] = {'total': len(collisions), 'in_selected': len(hot)}

    # ---------------------------------------------------------------- C9
    head('C9  Slot -> workbook block integrity')
    blocks, blockcat = parse_block_table(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not blocks or not blockcat:
        emit('FAIL', 'could not parse blocks/BLOCKCAT from moneyball_core.py')
    else:
        blockcat = {int(k): v for k, v in blockcat.items()}
        no_entry = [b for b in blocks if b not in blockcat]
        bad_cat = sorted({v for v in blockcat.values()} - canon)
        covered = {v for v in blockcat.values()}
        uncovered = sorted(canon - covered)
        if no_entry or bad_cat or uncovered:
            emit('FAIL', 'block table does not cleanly cover the 18 slots',
                 'no_entry=%s not_a_category=%s uncovered=%s' % (no_entry, bad_cat, uncovered))
        else:
            emit('PASS', 'every block maps to a canonical slot; all 18 slots covered',
                 '%d blocks -> %d categories' % (len(blocks), len(covered)))
        folded = collections.defaultdict(list)
        for b in blocks:
            folded[blockcat[b]].append(b)
        for cat in sorted(folded):
            cols = folded[cat]
            print('        %-28s cols %s%s' % (cat, cols, '   <- folded' if len(cols) > 1 else ''))
    report['blocks'] = blocks

    # --------------------------------------------------------------- C10
    head('C10 Dedup kept the strongest role')
    best_selected = {}
    for r in rows:
        if r['disposition'] == 'selected':
            key = (r['member'], r['bill'], r['congress'], r['effective_category'])
            best_selected[key] = ROLE_RANK.get(BR.canonical_role(r['effective_role']), 0)
    violations = []
    for r in rows:
        if not r['disposition'].startswith('duplicate'):
            continue
        key = (r.get('member') or r.get('member_source'), r['bill'], r['congress'],
               r.get('effective_category'))
        dropped = ROLE_RANK.get(BR.canonical_role(r['source_role']), 0)
        kept = best_selected.get(key)
        if kept is not None and dropped > kept:
            violations.append((key, r['source_role'], kept))
    if not violations:
        emit('PASS', 'no duplicate group kept a weaker role than one it dropped',
             '%d duplicate rows checked' %
             sum(1 for r in rows if r['disposition'].startswith('duplicate')))
    else:
        emit('FAIL', 'weaker role survived dedup', '%d groups' % len(violations))
        for key, role, kept in violations[:20]:
            print('        %s  dropped=%s kept_rank=%s' % (key, role, kept))

    # --------------------------------------------------------------- C11
    head('C11 Slot-vs-title signal mismatch (soft heuristic, for adjudication)')
    print('  Flags SELECTED bills whose title carries another slot\'s keyword.')
    print('  The Section comes from the upstream archive export, so a hit here is')
    print('  an upstream filing question, not a routing bug. Reported, not changed.')
    kw = {cat: k for cat, k in BR.SECTION_KEYWORDS}
    privacy_kw = ('privacy', 'cyber', 'data security', 'data breach', 'surveillance')
    mismatch = collections.defaultdict(list)
    seen_bill = set()
    for r in rows:
        if r['disposition'] != 'selected':
            continue
        key = (r['bill'], r['congress'])
        if key in seen_bill:
            continue
        seen_bill.add(key)
        title = str(r.get('title') or '').lower()
        slot = r['effective_category']
        if slot != 'Data Privacy & Cyber' and any(p in title for p in privacy_kw):
            mismatch[slot].append((r['bill'], r['congress'], r.get('title')))
    total_mm = sum(len(v) for v in mismatch.values())
    if not total_mm:
        emit('PASS', 'no privacy-titled bill sits outside Data Privacy & Cyber')
    else:
        emit('WARN', 'privacy/cyber-titled bills filed under another slot',
             '%d bills' % total_mm)
        for slot in sorted(mismatch, key=lambda s: -len(mismatch[s])):
            print('')
            print('        %s  (%d)' % (slot, len(mismatch[slot])))
            for bill, cong, title in sorted(mismatch[slot])[:12]:
                print('          %-12s %-4s %s' % (bill, cong, ascii_only(clip(title, 66))))
            if len(mismatch[slot]) > 12:
                print('          ... and %d more' % (len(mismatch[slot]) - 12))
    report['slot_title_mismatch'] = {k: len(v) for k, v in mismatch.items()}

    # --------------------------------------------------------------- C12
    head('C12 Archive-vs-manual category provenance')
    archive_by_cat = collections.Counter()
    overrides = BR.load_overrides(os.path.join(root, 'data', 'legislation', 'bill_overrides.csv'))
    for row in archive.values():
        category = BR.canonical_category(row.get('Section'))
        if BR.excluded_bill(row.get('Name'), row.get('Congress'), row.get('Title'),
                            row.get('bill_summary')):
            continue
        override = BR.override_for(overrides, row.get('Name'), row.get('Congress'))
        if override and override.get('Category'):
            category = BR.canonical_category(override['Category'])
        if category:
            archive_by_cat[category] += 1
    manual_by_cat = collections.Counter()
    for row in read_csv(os.path.join(root, 'manual_bill_evidence.csv')):
        category = BR.canonical_category(row.get('Category'))
        if category:
            manual_by_cat[category] += 1
    zero_archive = sorted(category for category in BR.CATEGORIES
                          if archive_by_cat[category] == 0)
    documented_zero = sorted(set(zero_archive) & ARCHIVE_ZERO_ALLOWLIST)
    unexpected_zero = sorted(set(zero_archive) - ARCHIVE_ZERO_ALLOWLIST)
    stale_allowlist = sorted(category for category in ARCHIVE_ZERO_ALLOWLIST
                             if archive_by_cat[category] > 0)
    print('        %-28s %8s %8s  %s' % ('slot', 'archive', 'manual', 'state'))
    for category in BR.CATEGORIES:
        state = ''
        if category in documented_zero:
            state = 'documented known zero'
        elif category in unexpected_zero:
            state = 'UNDOCUMENTED ZERO'
        print('        %-28s %8d %8d  %s' %
              (category, archive_by_cat[category], manual_by_cat[category], state))
    if unexpected_zero:
        emit('FAIL', 'canonical slots with undocumented zero archive rows',
             ', '.join(unexpected_zero))
    else:
        emit('PASS', 'no undocumented zero-archive slots',
             'documented known zero: %s' % (', '.join(documented_zero) or 'none'))
    for category in stale_allowlist:
        emit('WARN', 'archive-zero allowlist entry is stale and should be removed', category)
    report['archive_category_provenance'] = {
        category: {'archive': archive_by_cat[category], 'manual': manual_by_cat[category]}
        for category in BR.CATEGORIES
    }
    report['archive_zero_slots'] = {
        'documented': documented_zero, 'unexpected': unexpected_zero}

    # --------------------------------------------------------------- C13
    head('C13 Congress.gov API-browser query provenance')
    provenance_manifest = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'audits', 'legfix2', BAP.MANIFEST_NAME)
    try:
        if not archive_name:
            raise BAP.ProvenanceError(
                'no congress_bills_archive_*.xlsx file under the data root')
        archive_path = os.path.join(root, archive_name)
        if not os.path.isfile(archive_path):
            raise BAP.ProvenanceError(
                'data-root archive missing: %s' % archive_path)
        query_analysis = BAP.analyze_workbook(archive_path)
        manifest_check = BAP.optional_manifest_cross_check(
            archive_path, provenance_manifest)
        c13 = BAP.evaluate_c13(
            query_analysis,
            canonical_categories=BR.CATEGORIES,
            known_missing=QUERY_SET_ZERO_ALLOWLIST)
        if manifest_check['status'] == 'PASS':
            emit('PASS', 'C13 bill archive matches the optional source manifest',
                 manifest_check['detail'])
        elif manifest_check['status'] == 'MISMATCH':
            emit('WARN', 'C13 optional source manifest cross-check',
                 manifest_check['detail'])
        else:
            print('  [INFO] %s' % manifest_check['detail'])
        print('        %-33s %7s %9s %8s %5s %8s' %
              ('sheet', 'rows', 'decodable', 'missing', 'empty', 'malform'))
        for sheet_name in query_analysis['sheet_order']:
            sheet = query_analysis['sheets'][sheet_name]
            print('        %-33s %7d %9d %8d %5d %8d' %
                  (ascii_only(sheet_name)[:33],
                   sheet['rows_total'],
                   sheet['rows_decodable_hl'],
                   sheet['rows_missing_parameter_hl'],
                   sheet['rows_empty_hl'],
                   sheet['rows_malformed_hl']))
        for event in c13['events']:
            emit(event['status'], event['name'], event['detail'])
        report['archive_query_provenance'] = {
            'source_verification': {
                'status': (
                    'MISMATCH'
                    if manifest_check['status'] == 'MISMATCH'
                    else 'PASS'
                ),
                'method': 'direct data-root analysis',
                'archive_name': archive_name,
                'manifest_cross_check': manifest_check,
            },
            'totals': query_analysis['totals'],
            'per_sheet': {
                sheet_name: {
                    'category': query_analysis['sheets'][sheet_name]['category'],
                    'rows_total': query_analysis['sheets'][sheet_name]['rows_total'],
                    'rows_decodable_hl': (
                        query_analysis['sheets'][sheet_name]['rows_decodable_hl']),
                    'rows_missing_parameter_hl': (
                        query_analysis['sheets'][sheet_name][
                            'rows_missing_parameter_hl']),
                    'rows_empty_hl': (
                        query_analysis['sheets'][sheet_name]['rows_empty_hl']),
                    'rows_malformed_hl': (
                        query_analysis['sheets'][sheet_name]['rows_malformed_hl']),
                    'rows_truncated_hl': (
                        query_analysis['sheets'][sheet_name][
                            'rows_truncated_hl']),
                    'query_catalog_status': (
                        query_analysis['sheets'][sheet_name][
                            'query_catalog_status']),
                    'query_catalog_overflow_rows': (
                        query_analysis['sheets'][sheet_name][
                            'query_catalog_overflow_rows']),
                    'distinct_full_hl_query_count': (
                        query_analysis['sheets'][sheet_name][
                            'distinct_full_hl_query_count']),
                    'query_atom_counts': (
                        query_analysis['sheets'][sheet_name]['query_atom_counts']),
                    'whitespace_token_counts': (
                        query_analysis['sheets'][sheet_name][
                            'whitespace_token_counts']),
                    'query_list': [
                        {
                            'decoded_query': query['decoded_query'],
                            'catalog_status': query['catalog_status'],
                            'row_count': query['row_count'],
                            'query_atom_count': query['query_atom_count'],
                            'whitespace_token_count': (
                                query['whitespace_token_count']),
                        }
                        for query in query_analysis['sheets'][sheet_name][
                            'query_list']
                    ],
                    'coverage_status': c13['coverage_by_sheet'][sheet_name][
                        'status'],
                }
                for sheet_name in query_analysis['sheet_order']
            },
        }
        report['archive_query_gaps'] = {
            'sheet_numbering': c13['sheet_numbering'],
            'single_atom_queries': c13['single_atom_queries'],
            'query_sets': c13['query_sets'],
            'coverage_by_sheet': c13['coverage_by_sheet'],
        }
    except Exception as exc:
        detail = ascii_only(clip(exc, 180))
        emit('FAIL', 'C13 data-root archive query provenance unavailable',
             detail)
        report['archive_query_provenance'] = {
            'source_verification': {
                'status': 'FAIL',
                'method': 'direct data-root analysis',
                'archive_name': archive_name,
                'error': detail,
            },
            'totals': None,
            'per_sheet': {},
        }
        report['archive_query_gaps'] = {
            'sheet_numbering': None,
            'single_atom_queries': [],
            'query_sets': None,
            'coverage_by_sheet': {},
        }

    # --------------------------------------------------------------- C14
    head('C14 Cross-Congress bill-number collisions')
    direction_rows, direction_source_counts = load_direction_rows(root)
    c14 = evaluate_c14(direction_rows, archive)
    source_presence = {}
    for source_name in sorted(direction_source_counts):
        source = direction_source_counts[source_name]
        source_status = source['status']
        source_presence[source_name] = {
            'status': source_status,
            'rows': source['direction_rows'],
        }
        if source_status != 'PRESENT':
            emit(
                'WARN',
                'C14 direction source skipped',
                '%s status=%s' % (source_name, source_status),
            )
        else:
            print(
                '  [INFO] C14 direction source PRESENT -- %s rows=%d'
                % (source_name, source['direction_rows'])
            )
    if c14['direction_rows_scanned'] < 50:
        emit(
            'WARN',
            'C14 direction sources appear degraded',
            '%d total direction rows scanned; floor=50'
            % c14['direction_rows_scanned'],
        )
    clean_state = None
    if c14['collisions']:
        for finding in c14['collisions']:
            instances = '; '.join(
                'C%s %s' % (item['congress'], item['title'])
                for item in finding['instances']
            )
            detail = '%s[%s] %s -> %s' % (
                finding['source'],
                finding['locator'],
                finding['bill'],
                ascii_only(instances),
            )
            if finding['same_title']:
                print(
                    '  [INFO] C14 reintroduction-class -- %s'
                    % detail
                )
            else:
                emit(
                    'WARN',
                    'C14 direction row crosses Congress/title instances',
                    detail,
                )
    else:
        clean_state = c14_clean_state(c14, direction_source_counts)
        emit(
            clean_state['status'],
            clean_state['name'],
            clean_state['detail'],
        )
    for finding in c14['informal_senate_style_directions']:
        emit(
            'WARN',
            'C14 informal Senate direction identifier is ambiguous',
            '%s[%s] %s may mean S.N or S.Res.N' % (
                finding['source'],
                finding['locator'],
                finding['bill'],
            ),
        )
    if c14['unkeyable_directions']:
        for finding in c14['unkeyable_directions']:
            emit(
                'WARN',
                'C14 typed direction is invisible to legacy bill_key consumers',
                '%s[%s] %s typed=%s' % (
                    finding['source'],
                    finding['locator'],
                    finding['bill'],
                    tuple(finding['bill_type_key']),
                ),
            )
    else:
        emit(
            'PASS',
            'C14 no typed direction is invisible to legacy bill_key consumers',
        )
    if c14['legacy_keyable_unparsed']:
        for finding in c14['legacy_keyable_unparsed']:
            emit(
                'WARN',
                'C14 legacy-keyable direction is unparsed by bill_type_key',
                '%s[%s] %s legacy=%s -- collision risk: %s' % (
                    finding['source'],
                    finding['locator'],
                    finding['bill'],
                    tuple(finding['legacy_bill_key']),
                    finding['risk'],
                ),
            )
    else:
        emit(
            'PASS',
            'C14 no legacy-keyable direction escapes bill_type_key',
        )
    print(
        '        archive bill_key-unkeyable class: %d rows / %d typed ids'
        % (
            c14['archive_rows_bill_key_unkeyable'],
            len(c14['archive_identifiers_bill_key_unkeyable']),
        )
    )
    report['direction_congress_collisions'] = {
        'direction_rows_scanned': c14['direction_rows_scanned'],
        'source_counts': direction_source_counts,
        'source_presence': source_presence,
        'clean_state': clean_state,
        'degraded_source_floor': {
            'minimum_rows': 50,
            'degraded': c14['direction_rows_scanned'] < 50,
        },
        'archive_rows_non_integral_congress': (
            c14['archive_rows_non_integral_congress']),
        'rows': c14['collisions'],
    }
    report['informal_senate_style_directions'] = (
        c14['informal_senate_style_directions'])
    report['bill_key_unkeyable_directions'] = {
        'direction_rows': c14['unkeyable_directions'],
        'legacy_keyable_unparsed': c14['legacy_keyable_unparsed'],
        'unparsed_direction_rows': c14['unparsed_directions'],
        'archive_rows_in_class': c14['archive_rows_bill_key_unkeyable'],
        'archive_identifiers_in_class': (
            c14['archive_identifiers_bill_key_unkeyable']),
    }

    # --------------------------------------------------------------- C15
    head('C15 Vendor multi-number act category disagreement')
    c15 = evaluate_c15(archive)
    if c15['disagreeing_acts']:
        emit(
            'WARN',
            'C15 multi-number acts disagree on canonical category sets',
            '%d/%d acts (%.1f%%); %d/%d rows (%.1f%%)' % (
                c15['disagreeing_acts'],
                c15['multi_number_acts'],
                c15['disagreeing_pct'],
                c15['rows_involved'],
                c15['all_rows'],
                c15['rows_involved_pct'],
            ),
        )
        for example in c15['worst_examples']:
            print(
                '        %-45s rows=%d %s'
                % (
                    ascii_only(example['title'])[:45],
                    example['rows'],
                    ascii_only(json.dumps(
                        example['instances'], sort_keys=True)),
                )
            )
    else:
        emit(
            'PASS',
            'C15 no multi-number act category-set disagreement',
            '%d multi-number acts' % c15['multi_number_acts'],
        )
    print(
        '        blank-title rows excluded from act grouping: %d'
        % c15['blank_title_rows']
    )
    report['vendor_category_disagreement'] = c15

    # ------------------------------------------------------------ proposals
    head('PROPOSED AUDIT CHECKS (NOT APPLIED)')
    print('  audit_full.py currently asserts only row conservation and the 5 pinned')
    print('  bills. These would close real gaps -- a human decides whether to add them:')
    print('    C2  assert dispositions.get("unknown-section", 0) == 0')
    print('    C3  assert set(effective_categories) == set(bill_rules.CATEGORIES)')
    print('    C4  assert set(unmatched_members) <= {"Stacey Plaskett","James Moylan"}')
    print('    C5  assert len(archive_leg) >= 516   (floor, so silent coverage loss trips)')
    print('    C8  assert no bill_key collision among selected rows')

    # ---------------------------------------------------------------- tail
    print('')
    print('=' * 66)
    print('COVERAGE AUDIT - %d passed, %d FAILED, %d WARN' % (len(PASS), len(FAIL), len(WARN)))
    if FAIL:
        print('  FAILURES: %s' % FAIL)
    else:
        print('  NO STRUCTURAL FAILURES. Warnings above are for adjudication.')

    if args.json_out:
        with open(args.json_out, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, indent=1)
        print('  report written: %s' % args.json_out)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
