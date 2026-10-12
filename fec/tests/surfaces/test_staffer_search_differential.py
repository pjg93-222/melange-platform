"""Differential tests for typeahead: an identifier is searched only whole, and nothing else about the answers changes.

Before the ruling of 2026-10-03 typeahead searched the machine identifiers along with the names. The profile text began
with the node id, so `politico:<uuid>` made "co", "po" and "li" match every profile, and the static graph's text ended with
the member ids, so a bioguide id was a word of every attached staffer. Now names, labels, aliases and titles are searched
by their pieces, and an identifier only when the whole query is that identifier (trimmed, any case): a node id or, for a
Politico profile, its bare uuid; for the static graph also one member's whole bioguide id. A person named that way ranks first.

The old_* functions are the previous implementation, copied verbatim from 970e8c6d7 (StaticGraph.reindex and search_ids, the
body of LiveService._search after the id is resolved, and the refresh's profile text). The new answers are compared with
three independent statements of the ruling, each exactly: the same nodes (the same objects), in the same order, at the same
limit.

  1. spec_*: the ruling written out by brute force, the old algorithm over names only plus the whole-identifier rule.
  2. old_minus_identifier_only: the old code's own answer with every match that stood only on an identifier taken out. The
     static candidates are the first max(3 * limit, 60) of what is left, so a match the old cut had pushed out comes back:
     removed matches are refilled from below the limit before the cut.
  3. old_full_minus_identifier_only: with no limit at all, the new answer is the old answer with those matches removed,
     in the old order.
"""
import random
import re
import uuid
from collections import defaultdict

import pytest

from fec.surfaces import staffer_search_index as index_module
from fec.surfaces.staffer_graph_snapshot import StaticGraph
from fec.surfaces.staffer_search_index import ProfileSearchIndex, search_people, search_people_legacy

FIRST = ['Al', 'Bo', 'Ed', 'Jo', 'Li', 'Mo', 'Vi', 'Alan', 'Alice', 'Alma', 'Bob', 'Bobby', 'Carla', 'Chris', 'Daniel', 'Elena',
         'Frank', 'Gordon', 'Grace', 'Greg', 'Holly', 'Ian', 'James', 'Jose', 'Kathleen', 'Liam', 'Maria', 'Nina', 'Omar', 'Paul',
         'Quinn', 'Rosa', 'Stephanie', 'Tom', 'Uma', 'Victor', 'Wendy', 'Xavier', 'Yusuf', 'Zoe', 'Jos\u00e9', 'Zo\u00eb',
         '\u0130smail', 'Stra\u00dfe', '\u041b\u044e\u0434\u043c\u0438\u043b\u0430', '\u4e2d\u6587', 'Mc', "O'Neil", 'Jean-Luc', 'A', 'J',
         'D.', 'Co', 'Po']
LAST = ['Adams', 'Belk', 'Casca', 'Coulombe', 'Holzberg', 'Mesack', 'Ryan', 'Smith', 'Smith-Jones', "O'Brien", 'McDonald', 'Mac Donald',
        'DeLuca', 'De Luca', 'Lee', 'Li', 'Co', 'Po', 'Poe', 'Cole', 'Cohen', 'Colin', 'Collins', 'Polito', 'Politi', 'Lin', 'Lind',
        'Linden', 'M\u00fcller', '\u00c5ngstr\u00f6m', '\u0141ukasz', '\u03a3\u0391\u03a3', 'Ng', 'Oz', 'X', '007', 'Jr.', 'III']
TINY = ['A', 'Al', 'Bo', 'Co', 'Jo', 'Li', 'Po', 'Ed', 'X', 'Oz', 'Ng', 'Mo']
POSITIONS = ['Chief of Staff for Rep. Alma Adams', 'Legislative Director for Sen. Lee', 'Press Secretary', 'Counsel for Committee on Policy',
             'Scheduler for Rep. Collins']
ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'
BIOGUIDE_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'   # a bioguide id is one letter and six digits
HUGE = 10 ** 9   # a limit that never cuts: the static candidate cap, max(3 * limit, 60), is then every static match
POLITICO = 'politico:'


# ---------------------------------------------------------------- the previous implementation, verbatim

def old_reindex(nodes):
    prefix_nodes, search_text = defaultdict(set), {}
    for node_id, node in nodes.items():
        text = " ".join([node["label"], *node.get("aliases", []), *node.get("member_ids", [])]).casefold()
        search_text[node_id] = text
        for token in set(re.findall(r"[\w]+", text)):
            for length in (1, 2, 3):
                if len(token) >= length:
                    prefix_nodes[token[:length]].add(node_id)
    return prefix_nodes, search_text


def old_search_ids(nodes, prefix_nodes, search_text, query, limit=None):
    original = str(query).strip()
    if original in nodes:
        return [original]
    query = original.casefold()
    if not query:
        return []
    if query in nodes:
        return [query]
    tokens = re.findall(r"[\w]+", query)
    buckets = [prefix_nodes.get(token[:3], set()) for token in tokens]
    candidates = set.intersection(*buckets) if buckets else set()
    matched = [node_id for node_id in candidates if all(any(word.startswith(token) for word in re.findall(r"[\w]+", search_text[node_id])) for token in tokens)]
    matched.sort(key=lambda node_id: (nodes[node_id].get("name_status") == "UNRESOLVED_SOURCE_NAME", nodes[node_id]["label"].casefold() != query, nodes[node_id]["label"], node_id))
    return matched[:limit] if limit is not None else matched


def old_service_search(static_nodes, ids_of, profile_nodes, profile_search, excluded, query, limit):
    ids = ids_of(query, max(limit * 3, 60))
    nodes = {node_id: profile_nodes.get(node_id, static_nodes[node_id]) for node_id in ids}
    tokens = query.casefold().split()
    nodes.update((node_id, profile_nodes[node_id]) for node_id, text in profile_search if node_id == query or (tokens and all(t in text for t in tokens)))
    nodes = [n for n in nodes.values() if n.get('kind') == 'person' and n['id'] not in excluded]
    nodes.sort(key=lambda n: (n.get('last_reported_at') or n.get('source_as_of') or '', n['label']), reverse=True)
    nodes.sort(key=lambda n: (n['kind'] != 'person', n.get('source_only_identity', False)))
    def name_rank(node):
        words = re.findall(r'[\w]+', node.get('label', '').casefold())
        terms = re.findall(r'[\w]+', query.casefold())
        exact = words == terms
        all_names = bool(terms) and all(any(w.startswith(t) for w in words) for t in terms)
        return (not exact, not all_names)
    nodes.sort(key=name_rank)
    nodes.sort(key=lambda n: n['id'] != query)
    return nodes[:limit]


def old_profile_search(profile_nodes):
    """profile_search as the refresh built it before the ruling: the node id led every profile's text."""
    return [(n['id'], ' '.join([n['id'], n['label'], *n.get('current_positions', []), *n.get('aliases', [])]).casefold())
            for n in profile_nodes.values() if n['kind'] == 'person']


# ---------------------------------------------------------------- the ruling, by brute force

def spec_forms(node_id):
    """Every way a query can name a node in full, casefolded: its id and, for a Politico profile, its bare uuid."""
    folded = node_id.casefold()
    found = {folded}
    if folded.startswith(POLITICO) and len(folded) > len(POLITICO):
        found.add(folded[len(POLITICO):])
    return found


def human_text(node):
    """What a person reads of a profile: its name, current positions and aliases. Never its id."""
    return ' '.join([node['label'], *node.get('current_positions', []), *node.get('aliases', [])]).casefold()


def spec_reindex(nodes):
    """The old index over names only: the label and the aliases, no member ids."""
    prefix_nodes, search_text = defaultdict(set), {}
    for node_id, node in nodes.items():
        text = " ".join([node["label"], *node.get("aliases", [])]).casefold()
        search_text[node_id] = text
        for token in set(re.findall(r"[\w]+", text)):
            for length in (1, 2, 3):
                if len(token) >= length:
                    prefix_nodes[token[:length]].add(node_id)
    return prefix_nodes, search_text


class StaticSpec:
    """The static search as the ruling describes it, next to the previous one, over the same nodes. Answers are kept per query."""

    def __init__(self, graph):
        self.nodes = graph.nodes
        self.old_prefix, self.old_text = old_reindex(self.nodes)
        self.prefix, self.text = spec_reindex(self.nodes)
        self.words = {node_id: re.findall(r"[\w]+", text) for node_id, text in self.text.items()}   # the names alone
        self.bios, self.by_fold = defaultdict(set), defaultdict(list)
        for node_id, node in self.nodes.items():
            self.by_fold[node_id.casefold()].append(node_id)
            for bio in node.get('member_ids', []):
                self.bios[str(bio).casefold()].add(node_id)
        self._old, self._new = {}, {}

    def old_full(self, query):
        found = self._old.get(query)
        if found is None:
            found = self._old[query] = old_search_ids(self.nodes, self.old_prefix, self.old_text, query)
        return found

    def old_ids_of(self, query, limit=None):
        """The previous search with a limit (its early return, a whole id, ignores the limit, as it did)."""
        found = self.old_full(query)
        return found if limit is None or len(found) == 1 else found[:limit]

    def _answer(self, query):
        """(ids, whole): every match in order, and whether the query named a node in full, which the limit does not cut."""
        original = str(query).strip()
        if original in self.nodes:
            return [original], True
        folded = original.casefold()
        if not folded:
            return [], True
        if folded in self.nodes:
            return [folded], True
        if self.by_fold.get(folded):                       # the same id in other capitals: node order
            return list(self.by_fold[folded]), True
        matched = old_search_ids(self.nodes, self.prefix, self.text, query)
        tokens = re.findall(r'[\w]+', folded)
        if len(tokens) == 1 and tokens[0] == folded and folded in self.bios:   # one member's whole bioguide id
            matched = sorted({*matched, *self.bios[folded]}, key=lambda i: (self.nodes[i].get('name_status') == 'UNRESOLVED_SOURCE_NAME',
                                                                             self.nodes[i]['label'].casefold() != folded, self.nodes[i]['label'], i))
        return matched, False

    def search(self, query, limit=None):
        found = self._new.get(query)
        if found is None:
            found = self._new[query] = self._answer(query)
        ids, whole = found
        return ids if whole or limit is None else ids[:limit]


def spec_service_search(spec, profile_nodes, entries, excluded, query, limit, matched=None):
    """old_service_search with the two changes of the ruling: profiles are matched by `entries` (names only) or by naming
    them in full, and the person the query names in full ranks first. `matched`, the ids of the entries that match, may be
    given when the caller already has them."""
    ids = spec.search(query, max(limit * 3, 60))
    nodes = {node_id: profile_nodes.get(node_id, spec.nodes[node_id]) for node_id in ids}
    tokens = query.casefold().split()
    want = query.strip().casefold()
    if matched is None:
        matched = [node_id for node_id, text in entries if want in spec_forms(node_id) or (tokens and all(t in text for t in tokens))]
    nodes.update((node_id, profile_nodes[node_id]) for node_id in matched)
    nodes = [n for n in nodes.values() if n.get('kind') == 'person' and n['id'] not in excluded]
    nodes.sort(key=lambda n: (n.get('last_reported_at') or n.get('source_as_of') or '', n['label']), reverse=True)
    nodes.sort(key=lambda n: (n['kind'] != 'person', n.get('source_only_identity', False)))
    def name_rank(node):
        words = re.findall(r'[\w]+', node.get('label', '').casefold())
        terms = re.findall(r'[\w]+', query.casefold())
        exact = words == terms
        all_names = bool(terms) and all(any(w.startswith(t) for w in words) for t in terms)
        return (not exact, not all_names)
    nodes.sort(key=name_rank)
    nodes.sort(key=lambda n: want not in spec_forms(n['id']))
    return nodes[:limit]


def static_identifier_only(spec, node_id, query):
    """True when the previous static search matched this node only through an identifier: every query word is a word prefix
    of its text with the member ids, but not of its names alone. A node the query names in full is not such a match."""
    original = str(query).strip()
    if original in spec.nodes or original.casefold() in spec.nodes:
        return False
    names = spec.words[node_id]
    return not all(any(word.startswith(token) for word in names) for token in re.findall(r"[\w]+", original.casefold()))


def entry_matches(query, node_id, old_text, human):
    """(old match, new match) of one profile entry: the previous scan over the text that began with the id, and the scan
    over the names, or the query naming the entry in full. The new matches are always among the old ones."""
    tokens, want = query.casefold().split(), query.strip().casefold()
    old_match = node_id == query or bool(tokens and all(t in old_text for t in tokens))
    new_match = want in spec_forms(node_id) or bool(tokens and all(t in human for t in tokens))
    assert old_match or not new_match, ('a match that is new, not removed', query, node_id)
    return old_match, new_match


# ---------------------------------------------------------------- fixtures

def label_from(rng):
    kind = rng.random()
    first, last = rng.choice(FIRST), rng.choice(LAST)
    if kind < .12:
        return rng.choice(TINY)
    if kind < .22:
        return first
    if kind < .30:
        return f'{last}, {first}'
    if kind < .45:
        return f'{first} {rng.choice(ALPHABET.upper())}. {last}'
    return f'{first} {last}'


def build_static(seed, size):
    rng = random.Random(seed)
    graph = StaticGraph('fixture')
    graph._loaded = True
    for number in range(size):
        roll = rng.random()
        kind = 'person' if roll < .7 else rng.choice(['organization', 'container', 'member', 'policy_area'])
        ident = f'{kind}:{number}' if kind != 'person' else f'person:{number}'
        label = label_from(rng)
        if rng.random() < .12:
            label = ident                                    # a raw id: reindex turns it into an unresolved name
        node = {'id': ident, 'kind': kind, 'label': label, 'member_ids': [], 'aliases': []}
        if rng.random() < .15:
            node['aliases'] = [label_from(rng) for _ in range(rng.randint(1, 2))]
        if kind == 'member' or rng.random() < .05:
            node['member_ids'] = ['%s%06d' % (rng.choice(BIOGUIDE_LETTERS), rng.randint(0, 40))]
        if rng.random() < .1:
            node['last_reported_at'] = '2026-0%d-1%d' % (rng.randint(1, 9), rng.randint(0, 9))
        graph.nodes[ident] = node
    graph.nodes['Mixed:Case-ID'] = {'id': 'Mixed:Case-ID', 'kind': 'person', 'label': 'Mixed Case', 'member_ids': [], 'aliases': []}
    # Unresolved names that match a query exactly while resolved names also match it: the order of the four groups
    # (resolved exact, resolved rest, unresolved exact, unresolved rest) is only visible with both present.
    for number, last in enumerate(['Jones', 'Smith', 'Adams']):
        ident = f'person:placeholder-like{number}'
        graph.nodes[ident] = {'id': ident, 'kind': 'person', 'label': f'Name Unavailable Person {last}', 'member_ids': [], 'aliases': []}
    for number, label in enumerate(['Al', 'Al', 'Bo', 'Co']):
        ident = f'person:unresolved-tiny{number}'
        graph.nodes[ident] = {'id': ident, 'kind': 'person', 'label': label, 'name_status': 'UNRESOLVED_SOURCE_NAME', 'member_ids': [], 'aliases': []}
    graph.reindex()
    return graph


def build_profiles(graph, seed, size, overlap=.1, decoys=0):
    """profile_nodes as the refresh builds them: person entries (searched) and affiliation nodes (only held)."""
    rng = random.Random(seed)
    profile_nodes = {}
    statics = [i for i, n in graph.nodes.items() if n['kind'] == 'person']
    for number in range(size):
        ident = POLITICO + str(uuid.UUID(int=rng.getrandbits(128), version=4))
        if statics and rng.random() < overlap:
            ident = rng.choice(statics)                      # a profile merged into a static identity
        label = label_from(rng)
        if rng.random() < .1 and graph.nodes:
            label = graph.nodes[rng.choice(statics)]['label'] if statics else label
        node = {'id': ident, 'kind': 'person', 'label': label, 'member_ids': [], 'aliases': [], 'name_status': 'SOURCE_PROFILE_NAME_VERIFIED',
                'current_positions': rng.sample(POSITIONS, rng.randint(0, 2))}
        if rng.random() < .2:
            node['aliases'] = [label_from(rng)]
        roll = rng.random()
        if roll < .5:
            node['last_reported_at'] = rng.choice(['2026-09-01', '2026-09-01', '2025-01-15', '', None])
        elif roll < .7:
            node['source_as_of'] = rng.choice(['2026-09-01', '2024-03-03'])
        if rng.random() < .2:
            node['source_only_identity'] = rng.random() < .5
        profile_nodes[ident] = node
    for number in range(40):
        ident = f'politico-affiliation:{number}'
        profile_nodes[ident] = {'id': ident, 'kind': rng.choice(['container', 'organization', 'policy_area']), 'label': label_from(rng)}
    # Decoys: the newest profiles, whose name or position is the id of another person, whole or as a bare uuid. A query that is
    # that id matches the decoy by its name, so the person the id names is first only because an exact id ranks first.
    named = [i for i, n in profile_nodes.items() if n['kind'] == 'person'] + statics[:200]
    for number in range(decoys):
        target = rng.choice(named)
        text = rng.choice([target, target.upper(), target[len(POLITICO):] if target.startswith(POLITICO) else target])
        ident = f'politico:decoy-{number}'
        profile_nodes[ident] = {'id': ident, 'kind': 'person', 'label': text if number % 2 else label_from(rng), 'member_ids': [], 'aliases': [],
                                'name_status': 'SOURCE_PROFILE_NAME_VERIFIED', 'current_positions': [] if number % 2 else [text],
                                'last_reported_at': '2026-12-31'}
    return profile_nodes


def build_excluded(graph, profile_nodes, seed):
    rng = random.Random(seed)
    people = [i for i, n in graph.nodes.items() if n['kind'] == 'person'] + [i for i, n in profile_nodes.items() if n['kind'] == 'person']
    return frozenset(rng.sample(people, max(1, len(people) // 30)))


def words_of(graph, profile_nodes):
    words = set()
    for node in [*graph.nodes.values(), *profile_nodes.values()]:
        words.update(re.findall(r'[\w]+', node['label'].casefold()))
    return sorted(words)


def every_short_query():
    """Every query of one or two characters over letters, digits and the punctuation of ids and names."""
    alphabet = ALPHABET + ".-:'"
    return {*alphabet, *(a + b for a in alphabet for b in alphabet)}


def short_queries(graph, profile_nodes):
    """Every 1- and 2-character query, every 2-character prefix of every word, the pieces of ids, and some neighbours."""
    words = words_of(graph, profile_nodes)
    queries = every_short_query() | {'\u00df', '\u0130', '\u00e9', '\u0438', '\u4e2d'}
    queries |= {w[:2] for w in words if len(w) >= 2}
    queries |= {'x ', 'a.', "o'", 'name unavailable \u2014 person', 'Name Unavailable \u2014 Person', 'unavailable person',
                'name unavailable person jones', 'name', 'person', 'name unavailable'}
    # Pieces of identifiers of every length: the old search matched all of them. Every bioguide id in the graph, whole and cut short.
    queries |= {'polit', 'politico', 'ico:', 'tico', 'politico:', 'politico:0', 'person:', 'person:1', '4', '40', 'organization:', 'member:',
                'container:1', 'mixed', 'mixed:case'}
    for bio in {b for n in graph.nodes.values() for b in n.get('member_ids', [])}:
        queries |= {bio.lower()[:length] for length in range(1, len(bio) + 1)} | {bio, bio.lower() + '0'}
    return sorted(q.strip() for q in queries if q.strip())


def sampled_queries(graph, profile_nodes, seed, count=1000):
    rng = random.Random(seed)
    labels = [n['label'] for n in [*graph.nodes.values(), *profile_nodes.values()]]
    ids = [i for i in [*graph.nodes, *profile_nodes]]
    queries = []
    while len(queries) < count:
        label = rng.choice(labels)
        parts = label.split()
        choice = rng.randrange(14)
        if choice == 0:
            query = label
        elif choice == 1:
            query = label.upper()
        elif choice == 2:
            query = label.casefold()
        elif choice == 3:
            query = parts[0]
        elif choice == 4:
            query = parts[-1]
        elif choice == 5 and len(parts) >= 2:
            query = ' '.join(parts[:2])
        elif choice == 6:
            query = label[:rng.randint(1, max(1, len(label)))]
        elif choice == 7 and len(parts) >= 2:
            query = ' '.join(reversed(parts))
        elif choice == 8:
            query = '  ' + label + '   '
        elif choice == 9:
            query = rng.choice(ids)
        elif choice == 10:
            query = rng.choice(ids).upper()
        elif choice == 11 and len(parts) >= 2:
            query = parts[0][:3] + ' ' + parts[-1][:rng.randint(1, 4)]
        elif choice == 12:
            query = rng.choice(['!!', '--', '...', ',', "'", ' . ', 'x' * 60, 'a b c d e', 'The', 'of the'])
        else:
            query = label.replace(' ', '')
        query = query.strip()
        if query:
            queries.append(query)
    return queries


def identifier_queries(graph, profile_nodes, seed, count=400):
    """Pieces of identifiers, 2 to 12 characters of a node id, a bare uuid or a bioguide id, in either case, and whole ones padded."""
    rng = random.Random(seed)
    ids = [i for i in [*graph.nodes, *profile_nodes] if len(i) > 2]
    bios = sorted({b for n in graph.nodes.values() for b in n.get('member_ids', [])})
    queries = []
    for _ in range(count):
        ident = rng.choice(ids)
        roll = rng.randrange(6)
        if roll == 0:
            query = ident
        elif roll == 1:
            query = '  ' + ident.upper() + ' '
        elif roll == 2 and ident.startswith(POLITICO):
            query = ident[len(POLITICO):].upper()
        elif roll == 3:
            start = rng.randrange(len(ident) - 1)
            query = ident[start:start + rng.randint(2, 12)]
        elif roll == 4 and bios:
            bio = rng.choice(bios)
            query = rng.choice([bio, bio.lower(), bio[:rng.randint(1, 6)], ' ' + bio.lower() + '  '])
        else:
            query = ident[:rng.randint(2, len(ident))]
        if query.strip():
            queries.append(query)
    return queries


LIMITS = (1, 7, 20, 100)
STATIC_LIMITS = (None, 1, 5, 20, 60, 300)


class World:
    """A static graph and a set of profiles, the production search over them and every reference to compare it with.
    What a query matches is worked out once per query and kept, so a sweep over limits costs the sorts and nothing else."""

    def __init__(self, graph, profile_nodes, excluded):
        self.graph, self.nodes, self.excluded = graph, profile_nodes, excluded
        self.spec = StaticSpec(graph)
        people = [n for n in profile_nodes.values() if n['kind'] == 'person']
        self.old_entries = old_profile_search(profile_nodes)
        self.spec_entries = [(n['id'], human_text(n)) for n in people]
        self.prod_entries = [(n['id'], ProfileSearchIndex.haystack(n)) for n in people]
        assert self.prod_entries == self.spec_entries, 'the production text is the names only'
        self.index = ProfileSearchIndex(profile_nodes, self.prod_entries, excluded)
        assert self.index.ok
        self.entry_forms = [spec_forms(node_id) for node_id, _ in self.spec_entries]
        self.forms_seen = set()
        for node_id in [*graph.nodes, *profile_nodes]:
            self.forms_seen |= spec_forms(node_id)
        self._classified, self._whole = {}, {}

    def classify(self, query):
        """(static kept, entries kept, supported): of the old static answer the matches that do not stand only on an
        identifier, in the old order; the profile entries that match now (all of them match in the old text as well);
        and the ids that match now by either way."""
        found = self._classified.get(query)
        if found is None:
            spec = self.spec
            tokens, want = query.casefold().split(), query.strip().casefold()
            first = tokens[0] if tokens else None
            kept_static = [i for i in spec.old_full(query) if not static_identifier_only(spec, i, query)]
            kept_entries, supported = [], set(kept_static)
            for k, ((node_id, old), (_, human)) in enumerate(zip(self.old_entries, self.spec_entries)):
                # An entry can match, then or now, only if its old text holds the first query word or the query names it in full.
                if (first is not None and first in old) or want in self.entry_forms[k] or node_id == query:
                    if entry_matches(query, node_id, old, human)[1]:
                        kept_entries.append((node_id, old))
                        supported.add(node_id)
            found = self._classified[query] = (kept_static, kept_entries, supported)
        return found

    def production(self, query, limit):
        return search_people(self.graph, self.nodes, self.index, self.excluded, query, limit)

    def spec_answer(self, query, limit):
        _, kept_entries, _ = self.classify(query)
        return spec_service_search(self.spec, self.nodes, self.spec_entries, self.excluded, query, limit, matched=[i for i, _ in kept_entries])

    def old_answer(self, query, limit):
        return old_service_search(self.graph.nodes, self.spec.old_ids_of, self.nodes, self.old_entries, self.excluded, query, limit)

    def names_something_in_full(self, query):
        """True when the query is, ignoring case and padding, a node id, a bare uuid or one member's whole bioguide id."""
        want = query.strip().casefold()
        return want in self.forms_seen or want in self.spec.bios

    def old_minus_identifier_only(self, query, limit):
        """The old answer with every match that stood only on an identifier taken out, the static candidates refilled from
        below (the first max(3 * limit, 60) of those left) before the cut."""
        kept_static, kept_entries, _ = self.classify(query)
        if len(kept_static) <= max(limit * 3, 60):
            # The static cap cuts nothing at this limit, so the candidates are the same at every such limit and the answer
            # is a prefix of the whole one (the sorts do not depend on the limit): work the whole one out once.
            whole = self._whole.get(query)
            if whole is None:
                whole = self._whole[query] = old_service_search(self.graph.nodes, lambda q, k: kept_static, self.nodes, kept_entries, self.excluded, query, HUGE)
            return whole[:limit]
        return old_service_search(self.graph.nodes, lambda q, k: kept_static[:k], self.nodes, kept_entries, self.excluded, query, limit)

    def refilled(self, query, limit):
        """How many static matches the old cut at this limit had pushed out that now come back, the identifier matches gone."""
        kept_static, _, _ = self.classify(query)
        cap = max(limit * 3, 60)
        return len(set(kept_static[:cap]) - set(self.spec.old_full(query)[:cap]))

    def old_full_minus_identifier_only(self, query, old_all=None):
        """The whole old answer, no limit, in the old order, without the matches that stood only on an identifier."""
        _, _, supported = self.classify(query)
        return [n for n in (self.old_answer(query, HUGE) if old_all is None else old_all) if n['id'] in supported]


def same(got, expected):
    return [id(n) for n in got] == [id(n) for n in expected]


@pytest.fixture(scope='module')
def world():
    graph = build_static(11, 4000)
    profile_nodes = build_profiles(graph, 12, 2500, decoys=60)
    return World(graph, profile_nodes, build_excluded(graph, profile_nodes, 13))


# ---------------------------------------------------------------- the static index

def test_the_static_index_holds_names_only_and_the_whole_identifier_tables(world):
    graph, spec = world.graph, world.spec
    assert dict(graph._prefix_nodes) == dict(spec.prefix)
    assert graph._search_text == spec.text
    assert len(graph._rank) == len(graph.nodes) and sorted(graph._rank.values()) == list(range(len(graph.nodes)))
    assert 0 < graph._first_unresolved < len(graph.nodes), 'the fixture has resolved and unresolved names'
    bios = {b for n in graph.nodes.values() for b in n.get('member_ids', [])}
    assert graph._member_keys == {b.casefold(): {b} for b in bios} and len(bios) >= 30
    # Only ids that contain capitals are kept, under their casefolded spelling, every spelling in node order.
    assert graph._folded_ids == {i.casefold(): [j for j in graph.nodes if j.casefold() == i.casefold() and j != j.casefold()] for i in graph.nodes if i != i.casefold()}
    assert 'mixed:case-id' in graph._folded_ids and 'person:5' not in graph._folded_ids
    # The previous index held strictly more: every bioguide id was a word, so "a0" reached every attached staffer.
    assert 'a0' in spec.old_prefix and 'a0' not in graph._prefix_nodes
    assert len(spec.old_prefix['a']) > len(graph._prefix_nodes['a'])


# ---------------------------------------------------------------- the static search

def test_static_search_ids_equals_the_ruling_for_every_short_query_and_1000_sampled_queries(world):
    graph, spec = world.graph, world.spec
    queries = short_queries(graph, world.nodes) + sampled_queries(graph, world.nodes, 21) + identifier_queries(graph, world.nodes, 22)
    assert len(queries) > 3000
    unresolved_seen = nonempty = removed_queries = bio_whole = 0
    for query in queries:
        for limit in STATIC_LIMITS:
            assert graph.search_ids(query, limit) == spec.search(query, limit), (query, limit)
        full = spec.search(query)
        nonempty += bool(full)
        unresolved_seen += any(graph.nodes[i].get('name_status') == 'UNRESOLVED_SOURCE_NAME' for i in full)
        removed_queries += bool(set(spec.old_full(query)) - set(full))
        bio_whole += query.strip().casefold() in spec.bios
    assert nonempty > 1800 and unresolved_seen > 700 and removed_queries > 400 and bio_whole > 600, (nonempty, unresolved_seen, removed_queries, bio_whole)


def test_static_search_ids_is_the_old_result_without_the_matches_that_stood_only_on_an_identifier(world):
    """With and without a limit: the survivors keep their old order, and the limit is applied after the removal."""
    graph, spec = world.graph, world.spec
    removed_total = refilled = 0
    for query in short_queries(graph, world.nodes) + sampled_queries(graph, world.nodes, 23):
        if world.names_something_in_full(query):
            continue                                          # naming a node in full is the whole-identifier rule, tested on its own
        old_full = spec.old_full(query)
        kept = [i for i in old_full if not static_identifier_only(spec, i, query)]
        assert graph.search_ids(query) == kept, query
        removed_total += len(old_full) - len(kept)
        for limit in (1, 5, 20, 60, 300):
            expected = kept[:limit]
            assert graph.search_ids(query, limit) == expected, (query, limit)
            refilled += expected != [i for i in old_full[:limit]]
    assert removed_total > 5000 and refilled > 1500, (removed_total, refilled)


def test_static_search_ids_with_odd_limits_and_odd_queries(world):
    graph, spec = world.graph, world.spec
    bio = sorted({b for n in graph.nodes.values() for b in n.get('member_ids', [])})[3]
    some = [graph.nodes['person:5']['label'], 'al', 'a', 'co', 'x y z', '  Smith  ', '', '   ', '!!!', 'Mixed:Case-ID', 'mixed:case-id', 'MIXED:CASE-ID',
            ' Mixed:Case-Id ', 'person:5', 'PERSON:5', ' person:5 ', bio, bio.lower(), ' ' + bio.lower() + ' ', bio.lower()[:5], bio + ' ' + bio, 'alma ' + bio.lower()]
    for query in some:
        for limit in (None, 0, 1, 2, 3, 10, 10_000, -1, -3, -10_000):
            assert graph.search_ids(query, limit) == spec.search(query, limit), (query, limit)


@pytest.mark.parametrize('seed', [1, 2, 3])
def test_static_search_is_identical_on_small_tie_heavy_graphs(seed):
    graph = build_static(100 + seed, 300)
    for number in range(40):                                  # many nodes with one label, resolved and unresolved
        graph.nodes[f'person:dup{number}'] = {'id': f'person:dup{number}', 'kind': 'person', 'label': 'Al Smith' if number % 3 else f'person:dup{number}',
                                              'member_ids': ['B000001'] if number % 2 else []}
    for number in range(6):                                   # twins: the same id in other capitals
        graph.nodes[f'Twin:{number}'] = {'id': f'Twin:{number}', 'kind': 'person', 'label': f'Twin {number}'}
        graph.nodes[f'TWIN:{number}'] = {'id': f'TWIN:{number}', 'kind': 'person', 'label': f'Twin {number}'}
    graph.nodes['twin:0'] = {'id': 'twin:0', 'kind': 'person', 'label': 'Twin lower'}
    graph.reindex()
    spec = StaticSpec(graph)
    profile_nodes = build_profiles(graph, seed, 50)
    for query in short_queries(graph, profile_nodes) + sampled_queries(graph, profile_nodes, seed, 300) + ['al smith', 'Al Smith', 'smith al', 'name unavailable', 'b000001', 'B000001',
                                                                                                                'twin:1', 'TWIN:1', 'tWiN:2', 'Twin:3', 'twin:0', 'TWIN:0']:
        for limit in STATIC_LIMITS:
            assert graph.search_ids(query, limit) == spec.search(query, limit), (query, limit)
    # Twins, the same id in other capitals: the exact spelling wins alone, then an all-lower-case id, then every spelling in node order.
    assert graph.search_ids('Twin:1') == ['Twin:1'] and graph.search_ids('TWIN:1') == ['TWIN:1']
    assert graph.search_ids('twin:1') == ['Twin:1', 'TWIN:1'] and graph.search_ids('tWiN:1') == ['Twin:1', 'TWIN:1']
    assert graph.search_ids('twin:0') == ['twin:0'] and graph.search_ids('tWiN:0') == ['twin:0'] and graph.search_ids('Twin:0') == ['Twin:0']


def test_a_whole_bioguide_id_finds_its_staff_and_a_piece_of_one_finds_no_one(world):
    graph, spec = world.graph, world.spec
    bios = sorted({b for n in graph.nodes.values() for b in n.get('member_ids', [])})
    assert len(bios) >= 30
    for bio in bios:
        attached = {i for i, n in graph.nodes.items() if bio in n.get('member_ids', [])}
        assert attached
        for spelling in (bio, bio.lower(), '  ' + bio + ' '):
            assert attached <= set(graph.search_ids(spelling)), (bio, spelling)
        # Every proper piece of the id (its starts 'a' .. 'a00001', its ends), and the id with a character added.
        pieces = [bio[:n].lower() for n in range(1, len(bio))] + [bio[n:].lower() for n in range(1, len(bio))] + [bio.lower() + 'x']
        for piece in pieces:
            # Whatever is found is found by a name word that starts with the piece, never by the identifier.
            assert all(any(w.startswith(piece) for w in re.findall(r'[\w]+', spec.text[i])) for i in graph.search_ids(piece)), (bio, piece)


# ---------------------------------------------------------------- the whole typeahead

def test_search_people_equals_the_ruling_for_every_short_query_and_1000_sampled_queries(world, monkeypatch):
    graph = world.graph
    calls = []
    original = index_module.search_people_legacy
    monkeypatch.setattr(index_module, 'search_people_legacy', lambda *args: calls.append(args) or original(*args))
    queries = short_queries(graph, world.nodes) + sampled_queries(graph, world.nodes, 31) + identifier_queries(graph, world.nodes, 32)
    assert len(queries) > 3000
    people = nonempty = profile_first = id_named = 0
    for query in queries:
        for limit in LIMITS:
            expected = world.spec_answer(query, limit)
            assert same(world.production(query, limit), expected), (query, limit, [n['id'] for n in world.production(query, limit)][:5], [n['id'] for n in expected][:5])
        nonempty += bool(expected)
        people += len(expected)
        profile_first += bool(expected) and expected[0]['id'].startswith(POLITICO)
        id_named += world.names_something_in_full(query)
    assert not calls, 'the indexed path answered every query; the legacy search was never needed'
    assert nonempty > 1500 and people > 70000 and profile_first > 500 and id_named > 900, (nonempty, people, profile_first, id_named)


def test_search_people_is_the_old_answer_without_the_matches_that_stood_only_on_an_identifier_refilled_from_below(world):
    """The headline: for every 1- and 2-character query and 1,000 sampled queries, at every limit."""
    graph = world.graph
    queries = short_queries(graph, world.nodes) + sampled_queries(graph, world.nodes, 41)
    checked = removed_queries = refilled = 0
    for query in queries:
        if world.names_something_in_full(query):
            continue        # naming a node in full is the whole-identifier rule (its own tests): there an id match is wanted, and it ranks first
        checked += 1
        old_all = world.old_answer(query, HUGE)
        old_full, new_full = world.old_full_minus_identifier_only(query, old_all), world.production(query, HUGE)
        assert same(new_full, old_full), (query, len(new_full), len(old_full))
        removed_queries += len(old_all) > len(old_full)
        for limit in LIMITS:
            expected = world.old_minus_identifier_only(query, limit)
            assert same(world.production(query, limit), expected), (query, limit)
            refilled += bool(world.refilled(query, limit))
    assert checked > 2800 and removed_queries > 700 and refilled > 650, (checked, removed_queries, refilled)


def test_search_people_matches_names_only_when_every_profile_id_contains_the_query(world):
    """co, po and li are inside politico:<uuid>, so the old search matched every profile: the case the index is built for."""
    graph = world.graph
    for query in ['co', 'po', 'li', 'ol', 'ti', 'ic', 'it', 'polit', 'politico', 'ico:', 'o', 'i', 'c', 'p', 'l', 'politico:0', 'tico', 'politico:a']:
        old_matches = sum(all(t in text for t in query.casefold().split()) for _, text in world.old_entries)
        new_matches = sum(all(t in text for t in query.casefold().split()) for _, text in world.prod_entries)
        assert old_matches > len(world.old_entries) // 2 or len(query) > 3, (query, old_matches)
        assert new_matches < old_matches or old_matches == 0, (query, old_matches, new_matches)
        for limit in LIMITS:
            assert same(world.production(query, limit), world.spec_answer(query, limit)), (query, limit)
        full = world.production(query, HUGE)
        # Every answer is a person whose name, position or alias contains the query (profiles) or whose name starts with it (static).
        for node in full:
            human = human_text(node) if node['id'].startswith(POLITICO) else None
            if human is not None:
                assert all(t in human for t in query.casefold().split()), (query, node['id'])


@pytest.mark.parametrize('seed', [4, 5, 6, 7])
def test_search_people_is_identical_on_small_tie_heavy_worlds(seed):
    graph = build_static(200 + seed, 250)
    profile_nodes = build_profiles(graph, 300 + seed, 120, overlap=.3, decoys=12)
    # Many profiles that tie on every sort key except their place in the list.
    for number in range(30):
        ident = f'politico:tie{number}'
        profile_nodes[ident] = {'id': ident, 'kind': 'person', 'label': 'Alma Adams' if number % 2 else 'Al', 'last_reported_at': '2026-09-01'}
    excluded = build_excluded(graph, profile_nodes, seed)
    world = World(graph, profile_nodes, excluded)
    queries = (short_queries(graph, profile_nodes) + sampled_queries(graph, profile_nodes, seed, 400) + identifier_queries(graph, profile_nodes, seed, 100) +
               ['alma adams', 'al', 'adams alma', 'politico:tie3', 'POLITICO:TIE4', 'tie3', 'tie', 'politico:tie'])
    for query in queries:
        for limit in (1, 3, 20, 100):
            assert same(world.production(query, limit), world.spec_answer(query, limit)), (query, limit)
        if not world.names_something_in_full(query):
            assert same(world.production(query, HUGE), world.old_full_minus_identifier_only(query)), query
            for limit in (1, 3, 20):
                assert same(world.production(query, limit), world.old_minus_identifier_only(query, limit)), (query, limit)


def test_removed_identifier_matches_are_refilled_from_below_the_limit():
    """300 staff attached to W000001 sort ahead of every genuine W name. The old search fills its 60 static candidates with
    them, so no genuine W name was ever offered for 'w'; with the identifier out of the text the genuine ones come back."""
    graph = StaticGraph('fixture')
    graph._loaded = True
    for number in range(300):
        ident = f'person:crowd{number:03d}'
        graph.nodes[ident] = {'id': ident, 'kind': 'person', 'label': f'Aaron Abbott {number:03d}', 'member_ids': ['W000001'], 'aliases': []}
    genuine = ['Wanda Ng', 'Wes Oz', 'Willa Cather', 'Walt Lee', 'Xavier Wu']
    for number, label in enumerate(genuine):
        graph.nodes[f'person:real{number}'] = {'id': f'person:real{number}', 'kind': 'person', 'label': label, 'member_ids': [], 'aliases': []}
    graph.reindex()
    spec = StaticSpec(graph)
    index = ProfileSearchIndex({}, [], frozenset())
    old = lambda query, limit: old_service_search(graph.nodes, spec.old_ids_of, {}, [], frozenset(), query, limit)
    new = lambda query, limit: search_people(graph, {}, index, frozenset(), query, limit)
    offered = old('w', 20)
    assert len(offered) == 20 and all(n['label'].startswith('Aaron Abbott') for n in offered), 'the old answer is all identifier matches'
    assert not any(n['label'] in genuine for n in old('w', 100)), 'and the genuine names stay out of the old 300-candidate cut too'
    answer = new('w', 20)
    assert sorted(n['label'] for n in answer) == sorted(genuine), 'the genuine names are refilled from below the old cut'
    assert [n['id'] for n in new('w', 3)] == [n['id'] for n in answer[:3]] and len(new('w', 3)) == 3
    assert same(answer, spec_service_search(spec, {}, [], frozenset(), 'w', 20))
    # Two characters: 'w0' is the start of the bioguide id and of nothing a person reads.
    assert len(old('w0', 20)) == 20 and new('w0', 20) == [] and new('w00', 20) == []
    # The whole bioguide id still opens the member's staff, in either case.
    assert len(new('w000001', 400)) == 300 and len(new('W000001', 400)) == 300 and len(new(' w000001 ', 400)) == 300
    assert len(old('w000001', 400)) == 300 and new('w000001x', 400) == [] and new('000001', 400) == []


# ---------------------------------------------------------------- the fallback and the odd entries

def test_unusual_input_falls_back_to_the_legacy_search_and_never_breaks_the_index(world):
    graph, profile_nodes, excluded = world.graph, world.nodes, world.excluded
    broken = dict(profile_nodes)
    query = 'al'      # a person the query reaches, whom other people it reaches are sorted against
    ident = next(i for i, text in world.prod_entries if query in text and i not in excluded and profile_nodes[i]['kind'] == 'person')
    broken[ident] = {**broken[ident], 'label': None}
    index = ProfileSearchIndex(broken, world.prod_entries, excluded)
    assert index.ok is False, 'a label that cannot be folded must not raise; the legacy search is used instead'
    with pytest.raises(Exception):
        spec_service_search(world.spec, broken, world.spec_entries, excluded, query, 20)      # the reference fails the same way
    with pytest.raises(Exception):
        search_people(graph, broken, index, excluded, query, 20)
    clean = world.index
    # A limit below one, or an excluded set other than the one indexed, is answered by the legacy path, with the same result.
    for limit in (0, -1, -5):
        assert same(search_people(graph, profile_nodes, clean, excluded, 'al', limit), world.spec_answer('al', limit))
    other = frozenset(list(excluded)[:3])
    assert same(search_people(graph, profile_nodes, clean, other, 'al', 20),
                spec_service_search(world.spec, profile_nodes, world.spec_entries, other, 'al', 20))


def test_the_legacy_search_follows_the_same_rule_as_the_index(world):
    graph = world.graph
    for query in ['co', 'po', 'li', 'al', 'smith', 'jo ma', 'x']:
        for limit in (1, 20, 100):
            assert same(search_people_legacy(graph, world.nodes, world.prod_entries, world.excluded, query, limit), world.spec_answer(query, limit)), (query, limit)
    ident = next(i for i, n in world.nodes.items() if i.startswith(POLITICO) and n['kind'] == 'person' and i not in world.excluded)
    for spelling in (ident, ident.upper(), ' ' + ident[len(POLITICO):] + ' ', ident[len(POLITICO):].upper()):
        got = search_people_legacy(graph, world.nodes, world.prod_entries, world.excluded, spelling, 20)
        assert got and got[0]['id'] == ident, spelling


def test_search_people_with_no_profiles_and_with_exclusions(world):
    graph, profile_nodes, excluded = world.graph, world.nodes, world.excluded
    for nodes, banned in [({}, frozenset()), (profile_nodes, frozenset()), (profile_nodes, excluded)]:
        people = [n for n in nodes.values() if n['kind'] == 'person']
        entries = [(n['id'], human_text(n)) for n in people]
        index = ProfileSearchIndex(nodes, entries, banned)
        for query in ['al', 'smith', 'person:5', 'politico:00', 'a', 'zzzz', 'jo ma', 'co', 'po']:
            for limit in (1, 20):
                assert same(search_people(graph, nodes, index, banned, query, limit),
                            spec_service_search(world.spec, nodes, entries, banned, query, limit)), (query, limit)


def test_a_profile_entry_without_a_node_raises_only_when_it_matches(world):
    graph, profile_nodes, excluded = world.graph, world.nodes, world.excluded
    nodes = dict(profile_nodes)
    ghost = 'politico:11111111-2222-4333-8444-555555555555'
    entries = [*world.prod_entries, (ghost, 'zzghostname')]
    index = ProfileSearchIndex(nodes, entries, excluded)
    assert index.ok
    assert same(search_people(graph, nodes, index, excluded, 'al', 20), spec_service_search(world.spec, nodes, entries, excluded, 'al', 20))
    for query in ('zzghostname', ghost, ghost.upper(), '11111111-2222-4333-8444-555555555555'):    # its name, its id, its bare uuid
        with pytest.raises(KeyError):
            spec_service_search(world.spec, nodes, entries, excluded, query, 20)
        with pytest.raises(KeyError):
            search_people(graph, nodes, index, excluded, query, 20)
    # A piece of its id is not a match any more, so nothing looks the missing node up.
    for query in ('11111111', '2222-4333', 'tico:1111', 'politico:11111'):
        assert same(search_people(graph, nodes, index, excluded, query, 20), spec_service_search(world.spec, nodes, entries, excluded, query, 20))


def test_duplicate_profile_ids_collapse_to_one_entry_like_the_old_dictionary(world):
    graph, profile_nodes, excluded = world.graph, world.nodes, world.excluded
    entries = [*world.prod_entries, *world.prod_entries[:50]]
    index = ProfileSearchIndex(profile_nodes, entries, excluded)
    assert not index.unique_ids
    first = next(i for i, _ in world.prod_entries[:50])
    for query in ['co', 'al', 'politico', 'smith', first, first.upper(), first[len(POLITICO):] if first.startswith(POLITICO) else first]:
        got = search_people(graph, profile_nodes, index, excluded, query, 100)
        assert len({n['id'] for n in got}) == len(got)
        assert same(got, spec_service_search(world.spec, profile_nodes, entries, excluded, query, 100)), query
