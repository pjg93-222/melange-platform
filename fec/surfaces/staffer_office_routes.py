"""Office destinations backed by the latest dated payroll roster.

An office is a target set of named staff, never an extra person-to-person edge.
The roster date remains visible: a payroll snapshot is not proof of employment
on the day the user runs the search. No office or person name joins are made.
"""
from collections import defaultdict
from datetime import date, timedelta
import re


def office_index(nodes, facts=None):
    excluded = getattr(facts, 'network_excluded', set())
    offices = {i: n for i, n in nodes.items()
               if n.get('kind') == 'container' and n.get('container_kind') in ('member_office', 'committee')}
    stints = defaultdict(list)
    for person, node in nodes.items():
        if node.get('kind') != 'person' or person in excluded:
            continue
        for stint in node.get('stints', []):
            office = stint.get('container_id')
            if office in offices and stint.get('core_end_exclusive'):
                stints[office].append((person, stint))
    result = {}
    for ident, rows in stints.items():
        latest = max(s['core_end_exclusive'] for _, s in rows)
        # "Current" is the source's classification at its latest reporting
        # boundary. A former employee must never win just because they are close.
        roster = {person: {'person_id': person, 'label': nodes[person]['label'],
                           'title': s.get('title_last'), 'stint': s,
                           'provenance': nodes[person].get('provenance', {})}
                  for person, s in rows if s.get('status') == 'current' and s['core_end_exclusive'] == latest}
        label = offices[ident].get('label', ident)
        label = re.sub(r'^\d{4}\s+', '', label)
        label = re.sub(r'^HON\.?\s+', '', label, flags=re.I)
        through = (date.fromisoformat(latest) - timedelta(days=1)).isoformat()
        result[ident] = {'id': ident, 'kind': 'office', 'label': label,
                         'staff_count': len(roster), 'records_through': through,
                         'roster_basis': 'Latest payroll records; current at the source reporting cutoff',
                         'roster': roster}
    if facts:
        current = defaultdict(dict)
        for person, record in facts.people.items():
            if person in excluded:
                continue
            for role in record['current_roles']:
                office = role.get('office_id')
                if office:
                    current[office][person] = {'person_id': person, 'label': nodes[person]['label'],
                        'title': role.get('title'), 'observed_at': role.get('observed_at'),
                        'basis': 'current_profile', 'office_label': role.get('employer'), 'provenance': role.get('provenance')}
        for office, roster in current.items():
            label = re.sub(r'^\d{4}\s+', '', nodes.get(office, {}).get('label') or next(iter(roster.values()))['office_label'])
            result[office] = {'id': office, 'kind': 'office', 'label': label,
                'staff_count': len(roster), 'records_through': max(r.get('observed_at') or '' for r in roster.values()),
                'roster_basis': 'Currently listed directory staff; observation dates attached to each role',
                'roster': roster}
    return result


def search_offices(index, query, limit=20):
    tokens = re.findall(r'[\w]+', query.casefold())
    values = [n for n in index.values() if n['id'] == query or
              (tokens and all(any(word.startswith(t) for word in re.findall(r'[\w]+', n['label'].casefold())) for t in tokens))]
    values.sort(key=lambda n: (not n['roster_basis'].startswith('Currently listed'), not n['staff_count'], 'INTERN COMPENSATION' in n['label'].upper(), n['label'], n['id']))
    return [{k: v for k, v in n.items() if k != 'roster'} for n in values[:limit]]
