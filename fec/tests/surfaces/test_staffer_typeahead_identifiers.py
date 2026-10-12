"""Typeahead and machine identifiers (ruling of 2026-10-03).

Names, labels, aliases, titles and office names are searched by their pieces. A machine identifier is never searched by its
pieces: not a node id, not the `politico:<uuid>` spelling, not a bare uuid, not a source id. A query that is one of them in
full (trimmed, any case) still finds the person it names and ranks that person first, so pasting an id keeps working.

The sweeps against the previous implementation are in test_staffer_search_differential.py. This file holds the behaviour
the ruling states in words: the exact-id cases, the pieces that must find nothing, the text the refresh builds, the HTTP
endpoints, and the audit of the two other typeahead paths (the static people search and the office search).
"""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading

import pytest

from fec.surfaces.staffer_graph_snapshot import StaticGraph
from fec.surfaces.staffer_live_server import Handler
from fec.surfaces.staffer_live_service import LiveService
from fec.surfaces.staffer_office_routes import search_offices
from fec.surfaces.staffer_search_index import ProfileSearchIndex, id_forms, search_people
from fec.tests.surfaces.staffer_latency_fixtures import MemoProfiles, memo_service, office_graph, profile_node

# Real-shaped identifiers. The names below are chosen so that no piece of an id appears in what a person reads.
UUID = '3f9c2d1e-7a64-4b58-9e0d-81c5a62f4b77'
UUID_B = 'b81e5a07-26cd-4f3a-a9d4-0c7e93d1f582'
MERGED = 'person:hill:person:000078a3c8293728a7b23414dff12b27ead74dc1e24a0b13fe370492e307fa11'
STATIC_ONLY = 'person:lda:lobbyist:154321'
TARGET = 'politico:' + UUID
TARGET_NAME, TARGET_POSITION = 'Quintus Xylo', 'Zymurgy Wuvwx'


def fragments(text, low=2, high=8):
    """Every piece of `text` of low..high characters."""
    return {text[start:start + size] for size in range(low, high + 1) for start in range(len(text) - size + 1)}


def person(ident, label, positions=(), **extra):
    return {'id': ident, 'kind': 'person', 'label': label, 'member_ids': [], 'aliases': [], 'current_positions': list(positions),
            'name_status': 'SOURCE_PROFILE_NAME_VERIFIED', **extra}


def world(extra_profiles=()):
    """A static graph with three people (one of them the static side of a merged profile), and the profiles: a Politico
    profile, a second one, and the merged one. Returns the graph, the profiles, their (id, text) entries and the index."""
    graph = StaticGraph('fixture')
    graph._loaded = True
    graph.nodes[STATIC_ONLY] = person(STATIC_ONLY, 'Rosalind Venturi')
    graph.nodes['person:gordon'] = person('person:gordon', 'Gordon Holzberg')
    graph.nodes[MERGED] = person(MERGED, TARGET_NAME + ' Jr', [TARGET_POSITION])
    graph.reindex()
    profiles = {
        TARGET: person(TARGET, TARGET_NAME, [TARGET_POSITION], last_reported_at='2026-09-01'),
        'politico:' + UUID_B: person('politico:' + UUID_B, 'Ophelia Brandt', ['Counsel for Rep. Example'], last_reported_at='2026-08-01'),
        MERGED: person(MERGED, TARGET_NAME + ' Jr', [TARGET_POSITION], last_reported_at='2026-07-01'),
    }
    for node in extra_profiles:
        profiles[node['id']] = node
    entries = [(n['id'], ProfileSearchIndex.haystack(n)) for n in profiles.values() if n['kind'] == 'person']
    return graph, profiles, entries, ProfileSearchIndex(profiles, entries, frozenset())


def ids(nodes):
    return [n['id'] for n in nodes]


def run(graph, profiles, index, query, limit=20):
    return search_people(graph, profiles, index, frozenset(), query, limit)


# ---------------------------------------------------------------- what is searched

def test_the_profile_text_is_what_a_person_reads_and_never_an_identifier():
    node = person(TARGET, 'Quintus Xylo', ['Zymurgy Wuvwx', 'Counsel'], aliases=['Q. Xylo'])
    text = ProfileSearchIndex.haystack(node)
    assert text == 'quintus xylo zymurgy wuvwx counsel q. xylo'
    assert 'politico' not in text and UUID not in text


def test_the_refresh_builds_the_profile_text_without_the_id_and_the_view_serves_it():
    service, _ = memo_service()
    assert len(service.profile_search) == 4, 'the fixture has four profiles'
    for ident, text in service.profile_search:
        node = service.profile_nodes[ident]
        assert text == ' '.join([node['label'], *node.get('current_positions', []), *node.get('aliases', [])]).casefold()
        assert ident.casefold() not in text and 'politico:' not in text
    assert service._current_view().index.texts == [text for _, text in service.profile_search]


def test_every_form_a_query_can_name_a_person_by_in_full():
    assert id_forms('politico:' + UUID.upper()) == (TARGET, UUID)
    assert id_forms(MERGED) == (MERGED,) and id_forms('politico:') == ('politico:',) and id_forms('Mixed:Case') == ('mixed:case',)
    assert id_forms('person:politico:abc') == ('person:politico:abc',), 'only a leading politico: makes a Politico profile'


# ---------------------------------------------------------------- exact ids still work, and rank first

SPELLINGS = [lambda s: s, str.upper, str.lower, str.title, lambda s: '  ' + s + '  ', lambda s: '\t' + s.upper() + '\n']


@pytest.mark.parametrize('ident, bare', [(TARGET, UUID), ('politico:' + UUID_B, UUID_B)])
def test_a_politico_id_and_its_bare_uuid_each_return_that_person_first(ident, bare):
    graph, profiles, _, index = world()
    for spell in SPELLINGS:
        for query in (spell(ident), spell(bare)):
            found = ids(run(graph, profiles, index, query))
            assert found == [ident], (query, found)


def test_a_person_id_returns_that_person_whether_static_or_merged_into_a_profile():
    graph, profiles, _, index = world()
    for ident in (MERGED, STATIC_ONLY, 'person:gordon'):
        for spell in SPELLINGS:
            found = ids(run(graph, profiles, index, spell(ident)))
            assert found == [ident], (spell(ident), found)


def test_an_exact_id_ranks_first_even_when_another_person_matches_by_name_better():
    """A decoy whose name is the id string matches that query exactly by name, and is newer; the person the id names is first."""
    decoy = person('politico:decoy-0001', TARGET, last_reported_at='2026-09-30')
    graph, profiles, _, index = world(extra_profiles=[decoy])
    for query in (TARGET, TARGET.upper(), UUID):
        found = ids(run(graph, profiles, index, query))
        assert found == [TARGET, 'politico:decoy-0001'], (query, found)   # the decoy matches by its name, a human field


def test_a_pasted_id_that_is_not_whole_finds_nothing_by_way_of_the_id():
    graph, profiles, _, index = world()
    for ident, target in ((TARGET, TARGET), (UUID, TARGET), (MERGED, MERGED)):
        for query in (ident + '0', ident + 'x', '0' + ident, ident[:-1], ident[1:], ident[2:-2]):
            assert target not in ids(run(graph, profiles, index, query)), query


@pytest.mark.parametrize('ident, bare, target', [(TARGET, UUID, TARGET), (MERGED, None, MERGED)])
def test_a_piece_of_two_to_eight_characters_of_an_id_finds_nothing_by_way_of_the_id(ident, bare, target):
    graph, profiles, entries, index = world()
    pieces = fragments(ident) | (fragments(bare) if bare else set())
    assert len(pieces) > 250
    # What a person reads of the target holds no piece of its id, so it could be found for a piece only by way of the id.
    reads = dict(entries)[target]
    assert not [piece for piece in pieces if piece in reads]
    for piece in sorted(pieces):
        assert target not in ids(run(graph, profiles, index, piece)), piece


def test_negative_control_text_that_begins_with_the_id_is_found_by_every_one_of_those_pieces():
    """The same pieces over the text the refresh built before the ruling (the id first) do find the target: the test above has teeth."""
    graph, profiles, entries, _ = world()
    old_index = ProfileSearchIndex(profiles, [(i, (i + ' ' + text).casefold()) for i, text in entries], frozenset())
    pieces = fragments(TARGET) | fragments(UUID)
    assert len(pieces) > 250
    assert all(TARGET in ids(run(graph, profiles, old_index, piece)) for piece in pieces)


def test_the_two_character_queries_inside_politico_match_names_and_nothing_else():
    """co, po and li sit inside politico: and matched every profile. Here co is in the position "Counsel" and li in the name
    Ophelia, both of the second profile; no other piece of politico: is in any name, so nothing else is found."""
    graph, profiles, entries, index = world()
    found = {query: ids(run(graph, profiles, index, query)) for query in ('co', 'po', 'li', 'ol', 'ti', 'ic', 'it', 'o:', 'tic', 'poli', 'polit', 'politico')}
    assert found == {'co': ['politico:' + UUID_B], 'li': ['politico:' + UUID_B], 'po': [], 'ol': [], 'ti': [], 'ic': [], 'it': [], 'o:': [],
                     'tic': [], 'poli': [], 'polit': [], 'politico': []}


# ---------------------------------------------------------------- the same rule through the service and over HTTP

def uuid_service(rows, aliases=None):
    """A ready service whose profiles carry real-shaped uuids. rows: (uuid, label, positions)."""
    provider = MemoProfiles(labels=())
    provider.nodes = [profile_node(u, label, current_positions=list(positions), last_reported_at='2026-09-0%d' % (i + 1)) for i, (u, label, positions) in enumerate(rows)]
    provider.accepted = [{'profile_uuid': u} for u, _, _ in rows]
    service = LiveService('fixture', office_graph())
    service.profiles = provider
    service.identity_aliases = dict(aliases or {})
    service._refresh_profiles(force=True)
    service.ready, service.stage = True, 'Ready'
    service._refresh_profiles()
    return service


def test_through_the_service_a_pasted_id_finds_its_person_and_pieces_find_nothing():
    service = uuid_service([(UUID, TARGET_NAME, [TARGET_POSITION]), (UUID_B, 'Ophelia Brandt', [])])
    for query in (TARGET, TARGET.upper(), '  ' + UUID + ' ', UUID.upper()):
        assert [n['id'] for n in service.search(query)['nodes']] == [TARGET], query
    assert not [piece for piece in fragments(TARGET) | fragments(UUID) if piece in 'quintus xylo zymurgy wuvwx']
    for piece in sorted(fragments(TARGET) | fragments(UUID)):
        assert TARGET not in [n['id'] for n in service.search(piece)['nodes']], piece
    assert [n['id'] for n in service.search('quin')['nodes']] == [TARGET], 'the name still finds the person'
    assert [n['id'] for n in service.search('wuvwx')['nodes']] == [TARGET], 'so does the position'


def test_through_the_service_a_politico_id_of_a_merged_profile_still_resolves_to_the_person():
    """A profile merged into a static identity is pasted as its politico:<uuid>: the alias resolves it before the search."""
    service = uuid_service([(UUID, TARGET_NAME, [TARGET_POSITION])], aliases={TARGET: 'person:gordon'})
    assert 'person:gordon' in service.profile_nodes and TARGET not in service.profile_nodes
    for query in (TARGET, '  ' + TARGET + ' ', 'person:gordon', ' PERSON:GORDON '):
        assert [n['id'] for n in service.search(query)['nodes']][:1] == ['person:gordon'], query
    assert [n['id'] for n in service.search('Quintus Xylo')['nodes']] == ['person:gordon'], 'its name is the profile name now'


@pytest.fixture
def served():
    service, _ = memo_service()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.dashboard = service
    thread = threading.Thread(target=server.serve_forever, daemon=True, name='fixture-http-identifiers')
    thread.start()
    yield service, server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(5)


def get(port, path):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_the_typeahead_endpoint_does_not_match_politico_and_still_takes_a_pasted_id(served):
    _, port = served
    # The fixture profiles are politico:u0 .. u3 named Ada Lovelace, Alan Turing, Grace Hopper and Alma Adams Jr, each a
    # "Counsel for Rep. Example". The old text began with the id, so every piece of "politico:u0" matched all four.
    for query in ('po', 'li', 'ol', 'ti', 'ic', 'it', 'tic', 'polit', 'politico', 'o:', '0'):
        status, body = get(port, '/api/staffer-search?q=%s&limit=20' % query)
        assert status == 200 and body['ready'] and body['nodes'] == [], (query, body['nodes'])
    status, body = get(port, '/api/staffer-search?q=co&limit=20')
    assert status == 200 and len(body['nodes']) == 4, 'the position "Counsel" is searched by its pieces'
    for query in ('politico:u1', 'POLITICO:U1', '%20politico:u1%20', 'u1', 'U1'):
        status, body = get(port, '/api/staffer-search?q=%s&limit=20' % query)
        assert status == 200 and [n['label'] for n in body['nodes']] == ['Alan Turing'], (query, body['nodes'])


# ---------------------------------------------------------------- the other typeahead paths: audited

def test_the_static_people_search_never_matched_a_node_id_and_matches_a_bioguide_id_only_whole():
    graph = StaticGraph('fixture')
    graph._loaded = True
    staff = [f'person:hill:person:{number:064x}' for number in range(40)]
    for number, ident in enumerate(staff):
        graph.nodes[ident] = {'id': ident, 'kind': 'person', 'label': f'Quintus Xylo{number:02d}', 'member_ids': ['M001204'], 'aliases': []}
    graph.nodes['member:M001204'] = {'id': 'member:M001204', 'kind': 'member', 'label': 'Mary Murray', 'member_ids': ['M001204'], 'member_id': 'M001204', 'aliases': []}
    graph.reindex()
    # A node id: whole, one exact hit in either case; in pieces of 2 to 8 characters, nothing.
    assert graph.search_ids(staff[3]) == [staff[3]] == graph.search_ids(staff[3].upper()) == graph.search_ids('  ' + staff[3] + ' ')
    assert not [piece for piece in fragments(staff[3]) if graph.search_ids(piece)]
    # The member's bioguide id: whole, in any case, it finds the member and the staff attached to it; in pieces, nothing.
    members = {'member:M001204', *staff}
    assert set(graph.search_ids('m001204')) == members and graph.search_ids('m001204') == graph.search_ids('M001204') == graph.search_ids(' m001204 ')
    for piece in ('m0', 'm00', 'm001', 'm0012', 'm00120', '001204', '1204', 'm001204x'):
        assert graph.search_ids(piece) == [], piece
    assert graph.search_ids('m') == ['member:M001204'], 'the letter m starts the member name Mary and nothing else'
    # The map search is the same search.
    shown = graph.view('M001204', 400)
    assert {n['id'] for n in shown['nodes']} == members
    assert graph.view('M0012', 400)['nodes'] == []


def test_the_office_search_never_carried_an_id_in_its_text():
    """Audit result, pinned: offices are matched by the words of their label and by an exact id, nothing else."""
    first, second = 'container:office:house:HON:A000370', 'container:committee:senate:BANKING'
    index = {first: {'id': first, 'kind': 'office', 'label': 'Alma Adams', 'staff_count': 3, 'roster_basis': 'Latest payroll records', 'roster': {}},
             second: {'id': second, 'kind': 'office', 'label': 'Banking Committee', 'staff_count': 5, 'roster_basis': 'Latest payroll records', 'roster': {}}}
    assert [n['id'] for n in search_offices(index, 'alma')] == [first] and [n['id'] for n in search_offices(index, 'bank')] == [second]
    for piece in ('cont', 'container', 'container:office', 'office', 'house', 'hon', 'a0', 'a000', 'a000370', 'sen', 'senate'):
        assert search_offices(index, piece) == [], piece
    # The one identifier it accepts is a whole id, spelled as it is. This path had no defect and is unchanged.
    assert [n['id'] for n in search_offices(index, first)] == [first]
