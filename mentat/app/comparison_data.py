"""Report-bound subjects; never sum across overlapping industries or fact families.

/api/comparison-flows is served from response_cache (flows_entry): built once per report
version and code version, then answered from memory, else from disk, with the exact bytes
server.py Handler.send would write for the freshly built answer.
"""
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from graph_data import report_facts, report_activity, money
from report_insights import digest_stamp, stamp
import response_cache
try:
    from view_cache import graph_code_extra
except ImportError:  # a console that loaded view_cache before this build: memory only until it restarts
    graph_code_extra = None

FLOWS_NAMESPACE = 'comparison-flows'


def subjects(paths, industry):
    stamps = [stamp(path) for path in paths]
    reports = [report_facts(version) for version in stamps]
    years = [year for report in reports for year in report['cycles']]
    if len(years) != len(set(years)):
        raise ValueError('Reports overlap cycles. Choose one report per industry/cycle.')
    groups = {}

    def add(kind, identifier, label, row, person=False):
        identity = json.dumps([kind, identifier], ensure_ascii=False)
        entry = groups.setdefault(identity, {'id':identity, 'kind':kind, 'label':label,
            'company':row.get('company', '') if kind == 'Person' else '', 'periods':{}})
        values = entry['periods'].setdefault(row['cycle'], defaultdict(Decimal))
        values['giving'] += row['amount']
        if not person:
            values['direct'] += row['amount'] if row.get('candidate_id') else 0
            values['organizations'] += row['amount'] if row['category'] != 'Candidate' else 0
            values['corporate'] += row['amount'] if row['tier'] == 'Corporate' else 0
            values['personal'] += row['amount'] if row['tier'] != 'Corporate' else 0

    for report in reports:
        for row in report['facts']:
            add('Industry', industry, industry, row)
            add('Company', row['company'], row['company'], row)
            add('Recipient', row['recipient_id'], row['recipient'], row)
            if row['candidate_id']:
                add('Candidate', row['candidate_id'], row['candidate'], row)
        for row in report['people']:
            add('Person', [row['company'],row['person_id']], row['person'], row, True)
    for entry in groups.values():
        entry['periods'] = {year:{key:money(value) for key,value in values.items()}
                            for year,values in entry['periods'].items()}
    if any(stamp(path) != version for path,version in zip(paths,stamps)):
        raise ValueError('A report changed while building the comparison. Reload the comparison.')
    return {'industry':industry, 'subjects':sorted(groups.values(),key=lambda e:(e['kind'],e['label'])),
            'cycles':sorted(set(years)), 'reports':[{'name':r['report'],'sha256':r['sha256']} for r in reports],
            'note':'Election-cycle totals, not annual totals. Subjects may overlap; columns are independent and are never added together. Person totals use the saved employee fact family; recipient detail is unavailable for those aggregates.'}


def flows(paths, industry):
    """Separate company receipts from committee activity; never attribute PAC dollars upstream.

    The answer is flows_entry()'s; each caller gets its own object."""
    entry = flows_entry(paths, industry)
    return entry.value if entry.hit == 'built' else json.loads(entry.body)


def flows_entry(paths, industry):
    """/api/comparison-flows as a response_cache entry: exactly the bytes Handler.send writes
    for the freshly built answer (response_cache.json_body), and their gzip.

    Key: the industry label and the report paths in order (order and repetition belong to
    the question: [A, A] is refused as overlapping). Dependencies: each report's stamp and
    content SHA-256, and the code identity of this module and every application module it
    imports, with the native scanner and openpyxl (view_cache.graph_code_extra). Stored only
    when the build reported no issues (a failed committee-activity read may be a passing
    one, so it is recomputed next time, as before) and the reports still carry the stamps
    the key was taken under. A report that moves while it is hashed, or code that changed
    under this process, leaves the answer uncached or memory-only; nothing is refused that
    was answered before."""
    paths = [Path(path) for path in paths]
    stamps = [stamp(path) for path in paths]
    key_parts = {'industry': industry, 'reports': [version[0] for version in stamps]}
    try:
        # report_facts records the same digest_stamp as each report's sha256: one hash per stamp.
        content = [digest_stamp(version) for version in stamps]
        steady = [stamp(path) for path in paths] == stamps
        code = (response_cache.code_identity(__name__, extra=graph_code_extra())
                if steady and graph_code_extra is not None else None)
    except OSError:
        content, steady, code = None, False, None
    deps = {'stamps': [list(version) for version in stamps], 'content_sha256': content,
            'code': code or 'this process only'}
    moved = []

    def compute():
        value = _flows(paths, industry)
        moved.append([stamp(path) for path in paths] != stamps)
        return value

    def persist(value):
        return not moved[-1] and not value.get('issues')

    return response_cache.get_or_compute(FLOWS_NAMESPACE, key_parts, deps, compute, persist=persist,
                                         memory=steady, disk=code is not None)


def warm_flows(paths, industry):
    """The warmer's hook: build and store one industry's /api/comparison-flows answer (memory,
    and disk when this process configured it) without decoding it."""
    entry = flows_entry(paths, industry)
    return {'hit': entry.hit, 'body_bytes': entry.body_bytes, 'gzip_bytes': len(entry.gzip), 'etag': entry.etag}


def _flows(paths, industry):
    """Separate company receipts from committee activity; never attribute PAC dollars upstream."""
    stamps = [stamp(path) for path in paths]
    reports = [report_facts(version) for version in stamps]
    cycles = [year for report in reports for year in report['cycles']]
    if len(cycles) != len(set(cycles)):
        raise ValueError('Reports overlap cycles. Choose one report per industry/cycle.')
    sources, destinations, groups, issues = {}, {}, defaultdict(Decimal), []
    unavailable_activity_cycles = []

    def destination(kind, identifier, label, category):
        key = kind + ':' + identifier
        destinations.setdefault(key, {'id':key, 'label':label, 'kind':kind, 'category':category})
        return key

    for path, report, version in zip(paths, reports, stamps):
        committee_names = {row['id']:(row['label'],row['category']) for row in report.get('committees',[])}
        for identifier,(label,category) in committee_names.items():
            if category != 'Candidate':
                key = 'committee:' + identifier
                sources.setdefault(key, {'id':key, 'label':label, 'kind':'Committee', 'category':category})
        for company in report['companies']:
            sources.setdefault('company:'+company, {'id':'company:'+company, 'label':company, 'kind':'Company', 'category':'Company'})
        for row in report['facts']:
            source = 'company:' + row['company']
            committee_names[row['recipient_id']] = (row['recipient'], row['category'])
            if row['category'] != 'Candidate':
                key = 'committee:' + row['recipient_id']
                sources.setdefault(key, {'id':key, 'label':row['recipient'], 'kind':'Committee', 'category':row['category']})
            recipient = destination('Recipient', row['recipient_id'], row['recipient'], row['category'])
            channel = 'Corporate receipts' if row['tier'] == 'Corporate' else 'Company-associated personal receipts'
            groups[source, recipient, row['cycle'], channel] += row['amount']
            if row['candidate_id']:
                candidate = destination('Candidate', row['candidate_id'], row['candidate'], 'Candidate')
                groups[source, candidate, row['cycle'], channel] += row['amount']
        try:
            activity = report_activity(version)
        except (OSError, ValueError, KeyError, AttributeError) as error:
            issues.append(f"{report['report']}: committee activity unavailable: {error}")
            unavailable_activity_cycles.extend(report['cycles'])
            activity = []
        for row in activity:
            if row['cycle'] not in report['cycles']:
                continue
            source = 'committee:' + row['committee']
            label, category = committee_names.get(row['committee'], (row['committee'], 'Unclassified'))
            sources.setdefault(source, {'id':source, 'label':label, 'kind':'Committee', 'category':category})
            candidate = destination('Candidate', row['candidate_id'], row['candidate'], 'Candidate')
            channel = {'Support':'Independent spending · support', 'Oppose':'Independent spending · opposition',
                       'Contribution / Other':'Committee contribution / other', 'Unknown':'Committee activity · unknown'}.get(row['direction'], 'Committee activity · '+row['direction'])
            groups[source, candidate, row['cycle'], channel] += row['amount']
    if any(stamp(path) != version for path,version in zip(paths,stamps)):
        raise ValueError('A report changed while building the comparison. Reload the comparison.')
    return {'industry':industry, 'cycles':sorted(set(cycles)),
            'sources':sorted(sources.values(), key=lambda s:(s['kind'] != 'Company', s['label'], s['id'])),
            'destinations':sorted(destinations.values(), key=lambda s:(s['label'], s['id'])),
            'rows':[{'source':s, 'destination':d, 'cycle':c, 'channel':ch, 'amount':money(v)} for (s,d,c,ch),v in groups.items()],
            'reports':[{'name':r['report'], 'sha256':r['sha256']} for r in reports], 'issues':issues,
            'unavailable_activity_cycles':sorted(set(unavailable_activity_cycles)),
            'note':'Saved report observations only; absent source/recipient pairs are unavailable, not zero. Company-associated personal receipts include employees and do not mean company treasury donations. Independent spending supports or opposes a candidate; it is not money received by the candidate. Each committee is counted once per cycle, even when several companies fund it. Channels are kept separate; selecting a company does not attribute downstream PAC spending to that company. Election cycles cover two years.'}
