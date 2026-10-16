# -*- coding: utf-8 -*-
"""Interactive, source-first console for auditing bill-to-stance wiring.

Run with no arguments for a REPL, or use ``export-data``/``export-csv`` to
materialize the same evidence for the audit workbook. Production changes are
limited to explicit ``override`` and ``rebuild`` commands.
"""
import os as _bootstrap_os, sys as _bootstrap_sys
_bootstrap_sys.path.insert(
    0,
    _bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
    ),
)

import argparse
import collections
import csv
import glob
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime

import openpyxl

from paths import ROOT, WORKBOOK
import bill_rules as BR

ARCHIVE_GLOB = os.path.join(ROOT, 'congress_bills_archive_*.xlsx')
OVERRIDES = os.path.join(ROOT, 'data', 'legislation', 'bill_overrides.csv')
DIRECTIONS = os.path.join(ROOT, 'data', 'legislation', 'bill_directions.csv')
STANCE = os.path.join(ROOT, 'category_stance.json')
ARCHIVE_LEG = os.path.join(ROOT, 'archive_leg.json')
ROUTING_AUDIT = os.path.join(ROOT, 'legislation_routing_audit.json')
PRODUCTION_DROPS = frozenset((
    'official-membership-withdrawn-or-role-superseded',
    'identity-unverified-by-billstatus',
    'unmatched-member',
))
MANUAL = os.path.join(ROOT, 'manual_bill_evidence.csv')
FIELDS = ['Bill', 'Congress', 'Category', 'Direction', 'Include', 'Note']
MANUAL_FIELDS = ['Member','Role','Bill','Title','Sponsor','Congress','Category','Direction',
                 'DecayWeight','LastAction','DetailLink','Summary','Note']


def latest_archive():
    files = sorted(glob.glob(ARCHIVE_GLOB))
    if not files:
        raise FileNotFoundError(ARCHIVE_GLOB)
    return files[-1]


def _text(value):
    return '' if value is None else str(value)


def load_rows():
    path = latest_archive()
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = book['All Bills']
    iterator = sheet.iter_rows(values_only=True)
    header = [_text(value).strip() for value in next(iterator)]
    rows = []
    for values in iterator:
        row = {header[i]: values[i] for i in range(min(len(header), len(values)))}
        row['_source_category'] = BR.canonical_category(row.get('Section'))
        rows.append(row)
    book.close()
    if os.path.exists(MANUAL):
        with open(MANUAL, encoding='utf-8-sig', newline='') as handle:
            for manual in csv.DictReader(handle):
                rows.append({
                    'Section': manual.get('Category'), 'Role': manual.get('Role'),
                    'Member': manual.get('Member'), 'Name': manual.get('Bill'),
                    'Title': manual.get('Title'), 'Sponsor': manual.get('Sponsor'),
                    'CosponsorCount': 0, 'Committee': '', 'LastAction': manual.get('LastAction'),
                    'Congress': manual.get('Congress'),
                    'DecayWeight': BR.evidence_decay(
                        manual.get('Bill'), manual.get('Congress'), manual.get('DecayWeight') or 1.0),
                    'DetailLink': manual.get('DetailLink'), 'bill_summary': manual.get('Summary'),
                    'Direction': manual.get('Direction'),
                    '_source_category': BR.canonical_category(manual.get('Category')), '_manual': True,
                })
    return path, rows


def load_json(path, fallback):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


def load_directions():
    # Mirrors build_archive_legislative.load_direction_registry (typed mode); that
    # module executes on import, so it cannot be imported here.
    out = {'exact': {}, 'blank': {}}
    try:
        with open(DIRECTIONS, encoding='utf-8-sig', newline='') as handle:
            for row in csv.DictReader(handle):
                key = BR.bill_type_key(row.get('Bill'))
                if not key:
                    continue
                direction = _text(row.get('Direction')).strip()
                congress = BR._congress(row.get('Congress'))
                if congress:
                    out['exact'][(key, congress)] = direction
                else:
                    out['blank'][key] = direction
    except OSError:
        pass
    return out


def default_direction(category, title):
    if category == 'Credit Card Mandates':
        return 1 if BR.is_disapproval(title) else -1
    if category == 'Interchange':
        return -1
    return 1


def resolved(row, overrides, directions):
    category = row.get('_source_category')
    phrase = BR.excluded_bill(row.get('Name'), row.get('Congress'), row.get('Title'), row.get('bill_summary'))
    override = BR.override_for(overrides, row.get('Name'), row.get('Congress'))
    include = phrase is None
    reason = (('excluded: %s; exclusion precedes override in build_archive_legislative.py' % phrase)
              if phrase else 'included')
    if override:
        if override.get('Category'):
            category = BR.canonical_category(override['Category']) or category
        if phrase:
            include = False
        elif not BR.override_included(override):
            include = False
            reason = 'excluded by override'
    direction = None
    if override and override.get('Direction'):
        direction = {'1': 1, '+1': 1, '-1': -1}.get(override['Direction'], 0)
        reason += '; override direction'
    elif row.get('_manual') and row.get('Direction'):
        direction = {'1': 1, '+1': 1, '-1': -1}.get(_text(row.get('Direction')).strip(), 0)
        reason += '; manual evidence direction'
    else:
        type_key = BR.bill_type_key(row.get('Name'))
        raw = directions.get('exact', {}).get((type_key, BR._congress(row.get('Congress'))))
        if raw is None and type_key:
            raw = directions.get('blank', {}).get(type_key)
        if raw is not None:
            direction = {'1': 1, '-1': -1}.get(raw, 0)
            reason += '; direction registry'
        else:
            direction = default_direction(category, row.get('Title'))
            reason += '; category default'
    if direction == 0:
        include = False
        reason += '; neutral'
    decay = BR.evidence_decay(row.get('Name'), row.get('Congress'), row.get('DecayWeight') or 1.0)
    contribution = direction * BR.role_weight(row.get('Role')) * decay if include else 0.0
    return {
        'category': category or '', 'include': include, 'direction': direction,
        'reason': reason, 'contribution': contribution,
        'override_note': (override or {}).get('Note', ''),
    }


def production_ledger():
    # Read-only index of the production routing ledger by archive source_row.
    try:
        with open(ROUTING_AUDIT, encoding='utf-8') as handle:
            return {entry.get('source_row'): entry for entry in json.load(handle).get('rows', [])}
    except (OSError, ValueError, AttributeError):
        return None


def apply_production_drops(rows):
    ledger = production_ledger()
    if ledger is None:
        print('warning: %s unreadable; console include/exclude may overstate production evidence'
              % ROUTING_AUDIT)
        return
    mismatched = 0
    for index, row in enumerate(rows):
        if row.get('_manual'):
            continue
        entry = ledger.get(index + 2)
        if entry is None or _text(entry.get('bill')).strip() != _text(row.get('Name')).strip():
            mismatched += 1
            continue
        disposition = entry.get('disposition')
        if disposition in PRODUCTION_DROPS:
            result = row['_resolved']
            result['include'] = False
            result['contribution'] = 0.0
            result['reason'] += '; production: %s' % disposition
    if mismatched:
        print('warning: %d archive rows not aligned with %s; left unchanged'
              % (mismatched, os.path.basename(ROUTING_AUDIT)))


def state():
    source, rows = load_rows()
    overrides = BR.load_overrides(OVERRIDES)
    directions = load_directions()
    stance = load_json(STANCE, {'matrix': {}, 'scores': {}, 'gate': {}})
    legislative = load_json(ARCHIVE_LEG, {})
    by_bill_categories = collections.defaultdict(set)
    for row in rows:
        key = (BR.evidence_key(row.get('Name')), _text(row.get('Congress')))
        if row.get('_source_category'):
            by_bill_categories[key].add(row['_source_category'])
    for row in rows:
        row['_resolved'] = resolved(row, overrides, directions)
        key = (BR.evidence_key(row.get('Name')), _text(row.get('Congress')))
        row['_all_categories'] = sorted(by_bill_categories[key])
    apply_production_drops(rows)
    return {
        'source': source, 'rows': rows, 'overrides': overrides,
        'directions': directions, 'stance': stance, 'legislative': legislative,
    }


def resolve_topic(value):
    raw = _text(value).strip()
    if raw.isdigit() and 1 <= int(raw) <= len(BR.CATEGORIES):
        return BR.CATEGORIES[int(raw) - 1]
    low = raw.lower()
    exact = [topic for topic in BR.CATEGORIES if topic.lower() == low]
    if exact:
        return exact[0]
    hits = [topic for topic in BR.CATEGORIES if low in topic.lower()]
    if len(hits) == 1:
        return hits[0]
    raise ValueError('Topic must be a unique name or number 1-%d' % len(BR.CATEGORIES))


def topic_rows(data, topic):
    return [row for row in data['rows'] if row['_resolved']['category'] == topic]


def topic_summary(data, topic):
    rows = topic_rows(data, topic)
    active = [row for row in rows if row['_resolved']['include']]
    members = {row.get('Member') for row in active if row.get('Member')}
    bills = {(row.get('Name'), _text(row.get('Congress'))) for row in active}
    role_counts = collections.Counter(_text(row.get('Role')).lower() for row in active)
    matrix = data['stance'].get('matrix', {})
    scores = data['stance'].get('scores', {})
    labels = collections.Counter((categories or {}).get(topic, '—') for categories in matrix.values())
    positive_lean = sum(
        1 for member, categories in matrix.items()
        if (categories or {}).get(topic) == 'Lean Skeptic'
        and float((scores.get(member) or {}).get(topic, 0.0)) > 0
    )
    return {
        'topic': topic, 'archive_rows': len(rows), 'active_rows': len(active),
        'excluded_rows': len(rows) - len(active), 'unique_bills': len(bills),
        'members': len(members), 'sponsor_rows': role_counts['sponsor'],
        'original_rows': role_counts['original-cosponsor'] + role_counts['original'],
        'cosponsor_rows': role_counts['cosponsor'],
        'champions': labels['Champion'] + labels['Defender'],
        'allies': labels['Ally'], 'lean_skeptics': labels['Lean Skeptic'],
        'positive_lean_skeptics': positive_lean,
    }


def print_topics(data):
    print('\n #  Topic                         Bills Rows Spon Orig  LS +LS')
    for index, topic in enumerate(BR.CATEGORIES, 1):
        s = topic_summary(data, topic)
        print('%2d  %-29s %5d %4d %4d %4d %3d %3d' % (
            index, topic, s['unique_bills'], s['active_rows'], s['sponsor_rows'],
            s['original_rows'], s['lean_skeptics'], s['positive_lean_skeptics']))


def print_topic(data, topic, sponsored_only=False, limit=80):
    summary = topic_summary(data, topic)
    print('\n[%s]' % topic)
    print('  %(unique_bills)d active bills | %(active_rows)d active rows | %(excluded_rows)d excluded rows | '
          '%(sponsor_rows)d sponsor | %(original_rows)d original | %(cosponsor_rows)d cosponsor' % summary)
    print('  Current labels: %d Champion/Defender | %d Ally | %d Lean Skeptic (%d with positive scores)' % (
        summary['champions'], summary['allies'], summary['lean_skeptics'], summary['positive_lean_skeptics']))
    rows = topic_rows(data, topic)
    if sponsored_only:
        rows = [row for row in rows if 'sponsor' in _text(row.get('Role')).lower()]
    role_order = {'sponsor': 0, 'original-cosponsor': 1, 'original': 1, 'cosponsor': 2}
    rows.sort(key=lambda row: (
        0 if row['_resolved']['include'] else 1,
        role_order.get(_text(row.get('Role')).lower(), 9),
        -int(float(row.get('Congress') or 0)), _text(row.get('Name')),
    ))
    print('  Status Role                 Member                    Bill       Cg  Dir  Contribution  Title')
    for row in rows[:limit]:
        result = row['_resolved']
        print('  %-6s %-20s %-25s %-10s %3s %+3d %12.3f  %s' % (
            'IN' if result['include'] else 'OUT', _text(row.get('Role'))[:20],
            _text(row.get('Member'))[:25], _text(row.get('Name'))[:10],
            _text(row.get('Congress'))[:3], result['direction'], result['contribution'],
            _text(row.get('Title'))[:80]))
    if len(rows) > limit:
        print('  ... %d more rows; use export-csv for the complete domain.' % (len(rows) - limit))


def print_member(data, name):
    matrix = data['stance'].get('matrix', {})
    matches = [member for member in matrix if name.lower() in member.lower()]
    if len(matches) != 1:
        raise ValueError('Member search matched: %s' % ', '.join(matches[:20]))
    member = matches[0]
    scores = data['stance'].get('scores', {}).get(member, {})
    capped = (data['stance'].get('gate', {}).get('capped', {}) or {}).get(member, {})
    print('\n[%s]' % member)
    for index, topic in enumerate(BR.CATEGORIES, 1):
        label = matrix[member].get(topic, '—')
        score = float(scores.get(topic, 0.0))
        raw = capped.get(topic, '')
        flag = '  ! positive skeptic' if label == 'Lean Skeptic' and score > 0 else ''
        gate = ('  raw=' + raw + ' (capped)') if raw else ''
        print('%2d %-29s %-19s %+7.3f%s%s' % (index, topic, label, score, gate, flag))
    print('\nSponsored/original bills:')
    rows = [row for row in data['rows'] if _text(row.get('Member')).lower() == member.lower()
            and ('sponsor' in _text(row.get('Role')).lower())]
    for row in sorted(rows, key=lambda row: (-int(float(row.get('Congress') or 0)), _text(row.get('Name')))):
        result = row['_resolved']
        print('  %-20s %-10s %-29s %-3s %s' % (
            _text(row.get('Role')), _text(row.get('Name')), result['category'],
            'IN' if result['include'] else 'OUT', _text(row.get('Title'))))


def print_bill(data, query):
    key = BR.evidence_key(query)
    rows = [row for row in data['rows'] if BR.evidence_key(row.get('Name')) == key]
    if not rows:
        print('No archive rows found for', query)
        return
    for row in rows:
        result = row['_resolved']
        print('%-10s Cg %-3s %-20s %-26s %-29s %-3s %+d %s' % (
            _text(row.get('Name')), _text(row.get('Congress')), _text(row.get('Role')),
            _text(row.get('Member'))[:26], result['category'],
            'IN' if result['include'] else 'OUT', result['direction'], _text(row.get('Title'))))


def save_override(args):
    if len(args) < 5:
        raise ValueError('override BILL CONGRESS CATEGORY DIRECTION INCLUDE [NOTE]')
    bill, congress, category, direction, include = args[:5]
    note = ' '.join(args[5:])
    if category != '-':
        category = resolve_topic(category)
    else:
        category = ''
    if direction.lower() in {'default', '-'}:
        direction = ''
    if direction not in {'', '1', '+1', '-1', '0'}:
        raise ValueError('Direction must be +1, -1, 0, or default')
    existing = []
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, encoding='utf-8-sig', newline='') as handle:
            existing = list(csv.DictReader(handle))
    key = (BR.bill_key(bill), congress)
    replacement = dict(zip(FIELDS, [bill, congress, category, direction, include, note]))
    output = []
    replaced = False
    for row in existing:
        if (BR.bill_key(row.get('Bill')), _text(row.get('Congress')).strip()) == key:
            output.append(replacement); replaced = True
        else:
            output.append({field: row.get(field, '') for field in FIELDS})
    if not replaced:
        output.append(replacement)
    with open(OVERRIDES, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(output)
    print(('Updated' if replaced else 'Added'), 'override:', replacement)


def save_manual(args):
    if len(args) < 7:
        raise ValueError('manual MEMBER ROLE BILL CONGRESS CATEGORY DIRECTION TITLE [URL] [NOTE]')
    member, role, bill, congress, category, direction, title = args[:7]
    url = args[7] if len(args) > 7 else ''
    note = ' '.join(args[8:]) if len(args) > 8 else ''
    category = resolve_topic(category)
    if role.lower() not in {'sponsor','original','original-cosponsor'}:
        raise ValueError('Role must be sponsor or original-cosponsor')
    if direction not in {'1','+1','-1','0'}:
        raise ValueError('Direction must be +1, -1, or 0')
    replacement = {
        'Member':member, 'Role':'original-cosponsor' if role.lower().startswith('original') else 'sponsor',
        'Bill':bill, 'Title':title, 'Sponsor':'', 'Congress':congress, 'Category':category,
        'Direction':direction, 'DecayWeight':str(BR.evidence_decay(bill, congress, 1.0)),
        'DetailLink':url, 'Summary':'', 'Note':note,
    }
    existing=[]
    if os.path.exists(MANUAL):
        with open(MANUAL,encoding='utf-8-sig',newline='') as handle: existing=list(csv.DictReader(handle))
    key=(member.lower(),BR.evidence_key(bill),congress)
    output=[]; replaced=False
    for row in existing:
        if (_text(row.get('Member')).lower(),BR.evidence_key(row.get('Bill')),_text(row.get('Congress'))) == key:
            output.append(replacement); replaced=True
        else: output.append({field:row.get(field,'') for field in MANUAL_FIELDS})
    if not replaced: output.append(replacement)
    with open(MANUAL,'w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=MANUAL_FIELDS); writer.writeheader(); writer.writerows(output)
    print(('Updated' if replaced else 'Added'),'manual evidence:',replacement)


def export_csv(data, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    fields = ['Category', 'Status', 'Reason', 'Role', 'Member', 'Bill', 'Title', 'Congress',
              'Direction', 'Contribution', 'Sponsor', 'CosponsorCount', 'Committee',
              'LastAction', 'DecayWeight', 'OtherCategories', 'DetailLink', 'Summary']
    for index, topic in enumerate(BR.CATEGORIES, 1):
        slug = ''.join(char.lower() if char.isalnum() else '_' for char in topic).strip('_')
        path = os.path.join(output_dir, '%02d_%s.csv' % (index, slug))
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in topic_rows(data, topic):
                result = row['_resolved']
                writer.writerow({
                    'Category': topic, 'Status': 'Include' if result['include'] else 'Exclude',
                    'Reason': result['reason'], 'Role': row.get('Role'), 'Member': row.get('Member'),
                    'Bill': row.get('Name'), 'Title': row.get('Title'), 'Congress': row.get('Congress'),
                    'Direction': result['direction'], 'Contribution': round(result['contribution'], 6),
                    'Sponsor': row.get('Sponsor'), 'CosponsorCount': row.get('CosponsorCount'),
                    'Committee': row.get('Committee'), 'LastAction': row.get('LastAction'),
                    'DecayWeight': row.get('DecayWeight'),
                    'OtherCategories': '; '.join(row.get('_all_categories', [])),
                    'DetailLink': row.get('DetailLink'), 'Summary': row.get('bill_summary'),
                })
    print('Exported 18 topic CSV files to', output_dir)


def workbook_payload(data):
    matrix = data['stance'].get('matrix', {})
    scores = data['stance'].get('scores', {})
    capped_all = data['stance'].get('gate', {}).get('capped', {}) or {}
    topics = []
    for index, topic in enumerate(BR.CATEGORIES, 1):
        member_rows = []
        for member in sorted(matrix):
            label = (matrix.get(member) or {}).get(topic, '—')
            score = float((scores.get(member) or {}).get(topic, 0.0))
            raw = (capped_all.get(member) or {}).get(topic, '')
            member_rows.append({
                'member': member, 'label': label, 'score': score, 'raw_tier': raw,
                'gate_capped': bool(raw),
                'sign_flag': 'Positive score labeled Lean Skeptic' if label == 'Lean Skeptic' and score > 0 else '',
                'legislative_score': float((data['legislative'].get(member) or {}).get(topic, 0.0)),
            })
        bill_rows = []
        for row in topic_rows(data, topic):
            result = row['_resolved']
            bill_rows.append({
                'status': 'Include' if result['include'] else 'Exclude', 'reason': result['reason'],
                'role': _text(row.get('Role')), 'member': _text(row.get('Member')),
                'bill': _text(row.get('Name')), 'title': _text(row.get('Title')),
                'congress': _text(row.get('Congress')), 'direction': result['direction'],
                'contribution': result['contribution'], 'sponsor': _text(row.get('Sponsor')),
                'cosponsors': row.get('CosponsorCount') or 0, 'committee': _text(row.get('Committee')),
                'last_action': _text(row.get('LastAction')), 'decay': row.get('DecayWeight') or 1.0,
                'other_categories': '; '.join(row.get('_all_categories', [])),
                'url': _text(row.get('DetailLink')), 'summary': _text(row.get('bill_summary')),
            })
        topics.append({'index': index, 'name': topic, 'summary': topic_summary(data, topic),
                       'members': member_rows, 'bills': bill_rows})
    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'sources': {
            'legislation_archive': data['source'], 'main_workbook': WORKBOOK,
            'category_stance': STANCE, 'archive_leg': ARCHIVE_LEG,
            'bill_directions': DIRECTIONS, 'bill_overrides': OVERRIDES,
            'manual_bill_evidence': MANUAL,
        },
        'topics': topics,
    }


def write_payload(data, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(workbook_payload(data), handle, ensure_ascii=False, indent=2)
    print('Wrote workbook payload to', path)


def rebuild():
    scripts = ['legislation/build_archive_legislative.py', 'stance/per_category_stance.py',
               'stance/sync_stance_g.py', 'make_member_desk.py']
    fec_dir = os.path.dirname(os.path.dirname(__file__))
    for script in scripts:
        print('\n>>>', script)
        subprocess.run([sys.executable, os.path.join(fec_dir, script)],
                       cwd=ROOT, check=True)


HELP = """Commands:
  topics                              overview of all 18 domains
  topic NUMBER|NAME                   inspect one domain (all bill rows)
  sponsored NUMBER|NAME               inspect sponsor/original rows first
  member NAME                         stance + sponsored-bill trace for one member
  bill BILL_ID                        every archive row for a bill
  override BILL CONGRESS CATEGORY DIRECTION INCLUDE [NOTE]
                                      write bill_overrides.csv; quote category/note
  manual MEMBER ROLE BILL CONGRESS CATEGORY DIRECTION TITLE [URL] [NOTE]
                                      add a missing sponsor/original-cosponsor bill
  overrides                           print current override rows
  export DIRECTORY                    write 18 editable topic CSV files
  rebuild                             rebuild legislation, stances, Excel tier, and desk
  reload                              reload source files after edits
  help | quit

Example:
  override H.R.1181 119 "Data Privacy & Cyber" +1 include "Barr original cosponsor"
  manual "Andy Barr" original H.R.1181 119 "Data Privacy & Cyber" +1 "Protecting Privacy in Purchases Act"
"""


def repl():
    data = state()
    print('Moneyball bill/stance audit console')
    print('Source:', data['source'])
    print(HELP)
    print_topic(data, BR.CATEGORIES[0], sponsored_only=True, limit=35)
    while True:
        try:
            parts = shlex.split(input('\nbill-audit> ').strip())
            if not parts:
                continue
            command, args = parts[0].lower(), parts[1:]
            if command in {'quit', 'exit'}:
                return
            if command == 'help': print(HELP)
            elif command == 'topics': print_topics(data)
            elif command == 'topic': print_topic(data, resolve_topic(' '.join(args)))
            elif command == 'sponsored': print_topic(data, resolve_topic(' '.join(args)), sponsored_only=True)
            elif command == 'member': print_member(data, ' '.join(args))
            elif command == 'bill': print_bill(data, ' '.join(args))
            elif command == 'override': save_override(args); data = state()
            elif command == 'manual': save_manual(args); data = state()
            elif command == 'overrides':
                with open(OVERRIDES, encoding='utf-8-sig') as handle: print(handle.read())
            elif command == 'export': export_csv(data, os.path.abspath(' '.join(args)))
            elif command == 'rebuild': rebuild(); data = state(); print('Reloaded rebuilt artifacts.')
            elif command == 'reload': data = state(); print('Reloaded source artifacts.')
            else: print('Unknown command. Type help.')
        except (EOFError, KeyboardInterrupt):
            print(); return
        except Exception as exc:
            print('ERROR:', exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('topics')
    topic = sub.add_parser('topic'); topic.add_argument('topic')
    member = sub.add_parser('member'); member.add_argument('name')
    bill = sub.add_parser('bill'); bill.add_argument('bill')
    export = sub.add_parser('export-csv'); export.add_argument('directory')
    payload = sub.add_parser('export-data'); payload.add_argument('path')
    args = parser.parse_args()
    if not args.command:
        repl(); return
    data = state()
    if args.command == 'topics': print_topics(data)
    elif args.command == 'topic': print_topic(data, resolve_topic(args.topic))
    elif args.command == 'member': print_member(data, args.name)
    elif args.command == 'bill': print_bill(data, args.bill)
    elif args.command == 'export-csv': export_csv(data, os.path.abspath(args.directory))
    elif args.command == 'export-data': write_payload(data, os.path.abspath(args.path))


if __name__ == '__main__':
    main()
