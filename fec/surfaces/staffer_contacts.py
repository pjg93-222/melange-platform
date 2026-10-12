"""Dated contact evidence for the local people view, with conservative matching."""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import os
import re

try:
    from .staffer_connection_facts import normalized, office_key
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_connection_facts import normalized, office_key

CONTACT_PATH = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Melange/staffer_network/live_catalog/contact_overlay_20260905.json'


def contact_office(value):
    value = re.sub(r'"[^"]*"', '', str(value))
    value = re.sub(r'\s*\([DRI][^)]*\)\s*$', '', value)
    return office_key(value)


def combine_contacts(entries):
    result = {}
    for item in entries:
        kind, raw = item.get('kind'), str(item.get('value') or '').strip()
        if kind not in ('email', 'phone') or not raw:
            continue
        for value in re.split(r'[;\n]', raw):
            value = value.strip()
            if kind == 'email' and not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', value):
                continue
            if kind == 'phone' and not 7 <= len(re.sub(r'\D', '', value)) <= 18:
                continue
            key = (kind, value.casefold() if kind == 'email' else re.sub(r'\D', '', value))
            if key not in result:
                result[key] = {**item, 'value': value, 'sources': [item.get('provenance', {})]}
            else:
                prior = result[key]
                if item.get('provenance') not in prior['sources']:
                    prior['sources'].append(item.get('provenance', {}))
                if item.get('label') == 'Office phone':
                    prior['label'] = 'Office phone'
    return sorted(result.values(), key=lambda c: (c['kind'], c['value'].casefold()))


class ContactIndex:
    def __init__(self, path=CONTACT_PATH, records=None):
        self.revision = None
        if records is None:
            path = Path(path)
            if path.exists():
                raw = path.read_bytes()
                receipt = json.loads(path.with_suffix('.receipt.json').read_text(encoding='utf-8'))
                self.revision = hashlib.sha256(raw).hexdigest()
                if self.revision != receipt['sha256']:
                    raise ValueError('Contact overlay does not match its receipt')
                records = json.loads(raw)['records']
            else:
                records = []
        self.records, self.by_source, self.by_person = {}, {}, {}
        self.source_to_person, self.source_match_basis = {}, {}
        grouped = defaultdict(list)
        for record in records:
            grouped[record['source_id']].extend(record.get('contacts', []))
            self.records.setdefault(record['source_id'], record)
        self.by_source = {ident: combine_contacts(entries) for ident, entries in grouped.items()}
        self.coverage = {'source_people': len(self.records), 'matched_people': 0}

    def bind(self, nodes, facts):
        """A full name/alias and one unique reported-current member office.

        This attaches a sourced contact observation; it neither merges source
        identities nor creates social-network edges. Ambiguous matches stay on
        the Quorum record. A changed job does not inherit its old office contact.
        """
        lookup = defaultdict(set)
        for ident, node in nodes.items():
            if node.get('kind') != 'person':
                continue
            offices = {contact_office(r.get('employer')) for r in facts.people.get(ident, {}).get('current_roles', [])}
            offices.discard(None)
            for name in [node.get('label', ''), *node.get('aliases', [])]:
                if len(normalized(name).split()) >= 2:
                    for office in offices:
                        lookup[(normalized(name), office)].add(ident)
        linked = defaultdict(list)
        source_to_person = {}
        source_match_basis = {}
        match_basis = 'Exact full name or retained alias and unique current member office'
        for source_id, record in self.records.items():
            hits = lookup.get((normalized(record['name']), contact_office(record.get('office'))), set())
            if len(hits) == 1:
                ident = next(iter(hits))
                source_to_person[source_id] = ident
                source_match_basis[source_id] = match_basis
                linked[ident].extend({**c, 'match_basis': match_basis}
                                    for c in self.by_source[source_id])
        self.by_person = {ident: combine_contacts(entries) for ident, entries in linked.items()}
        self.source_to_person = source_to_person
        self.source_match_basis = source_match_basis
        for ident, node in nodes.items():
            if node.get('kind') == 'person':
                node['contacts'] = self.by_person.get(ident, self.by_source.get(ident, []))
        self.coverage = {'source_people': len(self.records), 'matched_people': len(self.by_person),
                         'email_people': sum(any(c['kind'] == 'email' for c in cs) for cs in self.by_source.values()),
                         'phone_people': sum(any(c['kind'] == 'phone' for c in cs) for cs in self.by_source.values()),
                         'revision': self.revision}

    def get(self, ident):
        ident = self._source_key(ident)
        return self.by_person.get(ident, self.by_source.get(ident, []))

    def _source_key(self, ident):
        """Accept either a source-native id or its person-node spelling.

        LDA and other source records can be presented by the graph as
        ``person:<source-id>`` while their evidence catalog keeps the original
        source id.  Normalize only when the reciprocal id actually exists so a
        legitimate id beginning with ``person:`` is never rewritten by guess.
        """
        ident = str(ident or '').strip()
        if ident in self.records:
            return ident
        if ident.startswith('person:') and ident.removeprefix('person:') in self.records:
            return ident.removeprefix('person:')
        person_form = 'person:' + ident
        if person_form in self.records:
            return person_form
        return ident

    def resolve_source_person(self, ident):
        ident = self._source_key(ident)
        if ident not in self.records:
            return {'source_id': ident, 'person_id': None, 'route_available': False,
                    'status': 'unknown_source', 'match_basis': None}
        person_id = self.source_to_person.get(ident)
        return {'source_id': ident, 'person_id': person_id,
                'route_available': person_id is not None,
                'status': 'matched_current_office' if person_id else 'source_only',
                'match_basis': self.source_match_basis.get(ident)}

    def source_record(self, ident):
        ident = self._source_key(ident)
        row = self.records.get(ident)
        if not row:
            return None
        return {'status': 'source_evidence', 'node': {'id': ident, 'label': row['name'], 'kind': 'person',
                'source_system': 'Quorum', 'source_summary': row['title'] + ' · ' + row['office'], 'contacts': self.get(ident)},
                'record': row, 'relationships': [], 'total': 0, 'offset': 0, 'limit': 50, 'has_more': False,
                'note': 'Staff export observation. Current employment and contact validity have not been independently confirmed.',
                'provenance': {'contact_overlay_sha256': self.revision}}
