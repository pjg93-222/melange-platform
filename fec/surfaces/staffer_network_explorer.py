"""Compact whole-inventory map. Relationship evidence stays in the person/route APIs."""
from collections import Counter
import gzip
import hashlib
import json
from fec.surfaces.staffer_network_scope import intern_record, intern_roles, intern_title, VERSION as SCOPE_VERSION


FIELDS = ['id', 'label', 'source_summary', 'member', 'committee', 'policy', 'source', 'profile_pending', 'kind', 'current_staff', 'employment_status', 'name_aliases', 'contacts', 'in_connection_network', 'current', 'lda_status']

#: Paul, 2026-09-06 (D-022 item 2): the people index is PEOPLE. Offices,
#: organisations and policy areas moved to the Groups view; they can never enter
#: here again, whether from the graph or from a source catalog.
PERSON_NAMESPACES = ('spine', 'lda', 'quorum', 'politico')


def person_source(ident):
    """Which person source an id belongs to. '' for anything that is not one.

    The spine test is the `hill:` namespace rather than the current
    `hill:person:` key shape, because the spine is being re-keyed under the name
    standard (D-022 item 1) and a new key shape must not silently empty the
    people list. `payroll-unresolved-record:` is a source row, not a person, and
    does not match.
    """
    text = str(ident or '')
    bare = text[len('person:'):] if text.startswith('person:') else text
    if bare.startswith('hill:'):
        return 'spine'
    if bare.startswith('lda:lobbyist:'):
        return 'lda'
    if text.startswith('quorum:'):
        return 'quorum'
    if text.startswith('politico:'):
        return 'politico'
    if text.startswith('littlesis:'):
        return 'littlesis'
    return ''


def node_person_sources(ident, node):
    """Every person-source namespace a display node carries, aliases included."""
    values = [ident, node.get('lda_source_person_id'), node.get('source_profile_uuid') and
              'politico:' + str(node.get('source_profile_uuid')),
              node.get('quorum_staff_id') and 'quorum:' + str(node.get('quorum_staff_id')),
              *(node.get('source_person_ids') or ()), *(node.get('identity_alias_ids') or ())]
    for record in node.get('source_identity_records') or ():
        if isinstance(record, dict):
            values.extend([record.get('id'), record.get('lda_source_person_id')])
    return {source for source in (person_source(value) for value in values if value) if source}


def is_current(node, person):
    """Current as the surface means it: a source says so today, or payroll does.

    Quorum/Politico current positions and a currently listed directory role are
    explicit source statements; a payroll trajectory marked `current` is the
    spine's own status field. Nothing here is an assertion of continuing
    employment -- it is the sort key for `current people first` (D-022 item 7).
    """
    if person.get('is_current_staff') or person.get('employment_status') == 'currently_listed':
        return True
    if node.get('current_positions'):
        return True
    return any(stint.get('status') == 'current' for stint in node.get('stints', []) or [])


def reconcile_counts(rows, aliases, facts, inventory=None, catalog=None, contacts=None):
    """Conserve source identities separately from evidence mentions and display rows."""
    inventory = inventory or {}
    coverage, counts = inventory.get('sourcecoverage', {}), inventory.get('counts', {})
    sources = []
    for key, label in [('payroll', 'Payroll'), ('lda', 'LDA'), ('politico', 'Politico Pro')]:
        sources.append({'source': label, 'source_people': coverage.get(key, {}).get('source_ids'),
                        'scope': 'Current and historical records' if key != 'politico' else 'Held directory profiles'})
    known = all(s['source_people'] is not None for s in sources)
    raw = sum(s['source_people'] for s in sources) if known else None
    network = sum(row[8] == 'person' and row[13] for row in rows)
    joins = counts.get('lda_unique_verified_spine_joins')
    # Only aliases whose final destination is in this index reduce its population.
    network_ids = {row[0] for row in rows if row[13]}
    applied = 0
    for alias in aliases:
        target, seen = alias, set()
        while target in aliases and target not in seen:
            seen.add(target)
            target = aliases[target]
        applied += target in network_ids and alias not in network_ids
    catalog_counts = catalog.receipt.get('counts', {}) if catalog else {}
    extra = dict(Counter(row[6] for row in rows if row[8] == 'person' and not row[13]))
    return {'sources': sources, 'source_people_before_matching': raw,
            'verified_lda_payroll_joins': joins, 'accepted_alias_reductions': applied,
            'network_people': network,
            'balance_holds': raw - joins - applied == network if known and joins is not None else None,
            'additional_source_person_records': extra,
            'all_source_person_records': sum(row[8] == 'person' for row in rows),
            'current_congressional_staff': (facts.coverage.get('current_staff') if facts else None),
            'quorum_directory_ids': catalog_counts.get('Quorum_person'),
            'quorum_contact_ids': len(contacts.records) if contacts else None,
            'quorum_contact_matches': contacts.coverage.get('matched_people') if contacts else None,
            'note': 'Network profiles combine accepted identity matches. Unresolved identities remain separate. Source counts include historical people, not just current staff. Contact matches do not establish identity merges.'}


def first(node, *keys):
    for key in keys:
        value = node.get(key)
        if value:
            return str(value[0] if isinstance(value, (list, tuple)) else value)
    return ''


def confirmed_source_memberships(nodes, aliases, inventory):
    """Evidence mentions can be ambiguous; source membership needs an accepted identity link."""
    memberships = {}
    def canonical(ident):
        seen = set()
        while ident in aliases and ident not in seen:
            seen.add(ident)
            ident = aliases[ident]
        return ident
    def attach(source_id, target):
        source = ('Payroll' if source_id.startswith(('person:hill:', 'hill:', 'payroll:')) else
                  'Lobbying' if source_id.removeprefix('person:').startswith('lda:lobbyist:') else
                  'Politico' if source_id.startswith('politico:') else None)
        if source:
            memberships.setdefault(canonical(target), set()).add(source)
    for ident in nodes:
        attach(ident, ident)
    for alias, target in aliases.items():
        attach(alias, target)
    for source_id, target in (inventory or {}).get('verified_source_links', {}).items():
        attach(source_id, target)
    return memberships


def linked_littlesis_people(nodes, aliases, inventory=None):
    """LittleSis person records an identity decision has attached to a real source.

    A LittleSis record enters the people index only when it is the surviving id
    of an identity group that also holds a spine, LDA, Quorum or Politico record,
    or when a verified source link points at it. A name alone never qualifies.
    """
    def canonical(ident):
        seen = set()
        while ident in aliases and ident not in seen:
            seen.add(ident)
            ident = aliases[ident]
        return ident
    linked = set()
    for alias, primary in aliases.items():
        if person_source(alias) in PERSON_NAMESPACES:
            target = canonical(primary)
            if person_source(target) == 'littlesis':
                linked.add(target)
    for source_id, target in (inventory or {}).get('verified_source_links', {}).items():
        target = canonical(target)
        if person_source(source_id) in PERSON_NAMESPACES and person_source(target) == 'littlesis':
            linked.add(target)
    for ident, node in nodes.items():
        if person_source(ident) != 'littlesis' or ident in aliases:
            continue
        if node_person_sources(ident, node) & set(PERSON_NAMESPACES):
            linked.add(ident)
    return linked


def build_index(nodes, aliases, revision, catalog=None, lda_catalog=None, facts=None, contacts=None, inventory=None):
    """The PEOPLE index: persons only, source-scoped, current people first.

    Rows are display records, not claims of distinct real-world identities. A
    compact column contract avoids repeating property names and provenance.
    Offices, organisations and policy areas are not people and are served by the
    Groups index instead (D-022 item 2); nothing institutional enters here, and
    an unlinked LittleSis person record is source browsing, not a person in this
    list.
    """
    rows, counts, excluded = [], Counter(), Counter()
    memberships = confirmed_source_memberships(nodes, aliases, inventory)
    linked = linked_littlesis_people(nodes, aliases, inventory)
    for ident, node in nodes.items():
        if ident in aliases or node.get('layout_only') or node.get('anchor_only'):
            continue
        if node.get('kind') != 'person' or node.get('node_kind') in ('container', 'organization', 'policy_area'):
            excluded[node.get('node_kind') or node.get('kind') or 'unknown'] += 1
            continue
        namespace = person_source(ident)
        if namespace not in PERSON_NAMESPACES and not (namespace == 'littlesis' and ident in linked):
            excluded['person_outside_source_scope'] += 1
            continue
        label = str(node.get('label') or 'Name not available')
        source_ids = ' '.join([ident, *node.get('source_person_ids', []), *node.get('identity_alias_ids', []), node.get('lda_source_person_id') or ''])
        sources = [name for name, present in [('Payroll', 'hill' in source_ids or 'payroll' in source_ids),
                    ('Lobbying', 'lda:' in source_ids), ('Politico', 'politico:' in source_ids or bool(node.get('source_profile_uuid'))),
                    ('Quorum', bool(node.get('quorum_staff_id')))] if present] or ['Other']
        source = '|'.join(sources)
        if inventory is not None:
            accepted = memberships.get(ident, set())
            sources = [s for s in ('Payroll', 'Lobbying', 'Politico') if s in accepted]
            if contacts and contacts.get(ident):
                sources.append('Quorum')
            sources = sources or ['Other']
            source = '|'.join(sources)
        counts.update(sources)
        person = facts.people.get(ident, {}) if facts else {}
        roles = person.get('latest_roles', person.get('current_roles', []))
        summary = '; '.join(' · '.join(filter(None, [r.get('title'), r.get('employer'),
            r.get('evidence_date') or r.get('observed_at'), r.get('status_label')])) for r in roles)
        rows.append([ident, label, str(summary or node.get('source_summary') or '')[:240],
                     first(node, 'employer_ids', 'employer_id', 'member_ids', 'member_id', 'office_id'),
                     first(node, 'committee_ids', 'committee_id'), first(node, 'policy_ids', 'policy_areas'),
                     source, bool(node.get('profile_pending')), 'person',
                     bool(person.get('is_current_staff')), person.get('employment_status', 'unknown'),
                     ' '.join(node.get('aliases', [])), None, None,
                     is_current(node, person), str((node.get('lda_status') or {}).get('label') or '')])
    if catalog:
        for row in catalog.index_rows():
            keep = row[8] == 'person' and (row[6] == 'Quorum' or row[0] in linked)
            if not keep:
                excluded['source_catalog_' + ('person' if row[8] == 'person' else str(row[8]))] += 1
                continue
            if row[0] not in nodes and row[0] not in aliases:
                rows.append(row)
                counts[row[6]] += 1
    # The LDA catalog indexes organisations by design; they belong to the Groups
    # index now. Its receipt still travels for the coverage note.
    if contacts:
        present = {row[0] for row in rows}
        for ident, record in contacts.records.items():
            if ident not in present and ident not in aliases:
                rows.append([ident, record['name'], record['title'] + ' · ' + record['office'], '', '', '', 'Quorum', False, 'person'])
                counts['Quorum'] += 1
    rows = [row + [False, 'unknown', '', None, None, False, ''] if len(row) == 9 else row for row in rows]
    rows = [row[:12] + [[{k: c.get(k) for k in ('kind', 'label', 'value', 'source_date', 'source')}
                         for c in contacts.get(row[0])] if contacts else []] + row[13:] for row in rows]
    rows = [row[:13] + [row[0] in facts.people if facts else False] + row[14:] for row in rows]
    # Current people first, then name: the surface is read for today's roster.
    rows.sort(key=lambda row: (not row[14], row[1].casefold(), row[0]))
    reconciliation = reconcile_counts(rows, aliases, facts, inventory, catalog, contacts)
    excluded_ids = set(getattr(facts, 'network_excluded', ()))
    for row in rows:
        ident = row[0]
        if ident in nodes:
            if intern_record(nodes[ident], facts.people.get(ident, {}) if facts else {}):
                excluded_ids.add(ident)
        elif contacts and ident in contacts.records:
            if intern_title(contacts.records[ident].get('title')):
                excluded_ids.add(ident)
        elif intern_roles([{'title': role.split(' · ', 1)[0]} for role in str(row[2]).split('; ')]):
            excluded_ids.add(ident)
    removed = sum(row[0] in excluded_ids for row in rows)
    rows = [row for row in rows if row[0] not in excluded_ids]
    counts = Counter(source for row in rows for source in row[6].split('|'))
    if removed:
        excluded['intern_role'] = removed
    reconciliation['network_scope'] = {'policy': SCOPE_VERSION, 'excluded_intern_records': removed,
        'visible_records': len(rows), 'source_records_retained': True}
    payload = {'schema': 'melange-network-index-v3', 'ready': True, 'revision': revision,
               'fields': FIELDS, 'people': rows, 'total': len(rows), 'source_counts': dict(counts),
               'people_total': sum(row[8] == 'person' for row in rows),
               'kind_counts': dict(Counter(row[8] for row in rows)),
               'identity_aliases': aliases,
               'routing_people_total': len(set(facts.people) - excluded_ids) if facts else None,
               'source_catalog': catalog.receipt if catalog else None,
               'lda_source_catalog': lda_catalog.receipt if lda_catalog else None,
               'complete_inventory': bool(catalog and lda_catalog), 'relationships': 'on_selection',
               'headcount_note': 'Source records; possible duplicates stay separate until matched. A record can belong to multiple sources; source counts overlap.',
               'reconciliation': reconciliation, 'scope': 'sources',
               'person_scope': {'namespaces': list(PERSON_NAMESPACES),
                                'littlesis_people_linked': len(linked),
                                'excluded_records': dict(excluded),
                                'current_people': sum(bool(row[14]) for row in rows),
                                'note': 'People only. Offices, organisations and policy areas are in the Groups index. '
                                        'A LittleSis person record appears only when an identity decision attaches it to a '
                                        'payroll, lobbying, Quorum or Politico record; the rest stay browsable as source records.'}}
    payload['person_facts'] = facts.coverage if facts else None
    payload['contact_coverage'] = contacts.coverage if contacts else None
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    compressed = gzip.compress(raw, compresslevel=3)
    etag = '"index-' + hashlib.sha256(raw).hexdigest()[:24] + '"'
    operational = set((inventory or {}).get('operational_staffer_ids', ()))
    network_rows = [row for row in rows if row[0] in operational] if operational else [row for row in rows if row[13]]
    network_payload = {**payload, 'scope': 'network', 'people': network_rows, 'total': len(network_rows),
                       'people_total': len(network_rows), 'kind_counts': {'person': len(network_rows)},
                       'source_counts': dict(Counter(source for row in network_rows for source in row[6].split('|'))),
                       'operational_population_active': bool(operational),
                       'headcount_note': 'Operational staffer records admitted by the verified Politico/Quorum population policy.' if operational else payload['headcount_note']}
    network_raw = json.dumps(network_payload, ensure_ascii=False, separators=(',', ':')).encode()
    return {'revision': revision, 'payload': payload, 'raw': raw, 'gzip': compressed, 'etag': etag,
            'network_raw': network_raw, 'network_gzip': gzip.compress(network_raw, compresslevel=3),
            'network_etag': '"network-' + hashlib.sha256(network_raw).hexdigest()[:24] + '"'}


def graph_context_record(graph, ident, offset=0, limit=50):
    """Page receipt-gated institution affiliations; no new relationship inference."""
    offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
    node = graph.nodes.get(ident)
    if (not node or node.get('node_kind') not in ('container', 'organization', 'policy_area')
            or graph.verification.get('status') != 'PASS' or graph.errors):
        return {'status': 'unknown_source_record', 'record': None}
    edges = [graph.edges[i] for i in graph.adjacency.get(ident, [])
             if graph.edges[i].get('layer') in ('membership', 'policy_area', 'revolving')
             and not graph.edges[i].get('layout_only')]
    edges.sort(key=lambda edge: edge['id'])
    relationships = []
    for edge in edges[offset:offset+limit]:
        other_id = edge['target'] if edge['source'] == ident else edge['source']
        other = graph.nodes.get(other_id, {})
        relationships.append({**edge, 'source_relationship_id': edge['id'],
            'category_name': edge['layer'].replace('_', ' ').title(), 'other_id': other_id,
            'other_label': other.get('label', other_id), 'other_kind': other.get('kind'),
            'description': 'Verified graph affiliation; inspect the source fields and person history for its scope.'})
    source = {'container': 'Payroll', 'organization': 'Lobbying', 'policy_area': 'Politico'}[node['node_kind']]
    return {'status': 'verified_graph_context',
            'node': {**node, 'source_system': source, 'source_summary': 'Receipt-verified graph context'},
            'record': node, 'relationships': relationships, 'total': len(edges), 'offset': offset,
            'limit': limit, 'has_more': offset+limit < len(edges),
            'relationship_label': 'verified graph affiliations',
            'native_source_id': ident.removeprefix('organization:') if ident.startswith('organization:lda:') else None,
            'provenance': {'node': node.get('provenance'), 'graph_sources': graph.sources},
            'note': 'An office, organization or policy record is context, not a person or route endpoint. Affiliation alone does not establish acquaintance or overlapping employment.'}
