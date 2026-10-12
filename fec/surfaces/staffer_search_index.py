"""Typeahead over the people the live view serves, without sorting every match.

search_people() returns what the service search always returned (the same nodes, in the same order, cut at the same
limit) but picks the few it needs instead of sorting every profile that matches. search_people_legacy() is the
previous algorithm, kept as the fallback when the index could not be built and as the reference the differential
tests compare against.

How the answer is chosen. The candidates are U = S + P: the static graph's best matches S (in the order the static
search ranks them) followed by the profile people P whose search text contains every query word, in the order of the
profile list. They are filtered to eligible people and ordered by four stable sorts: exact id, name rank, source-only
flag, then last report date descending. The sorts are stable, so equal keys keep their place in U. A subset of U that
contains the first `limit` elements of that order and keeps U's relative order yields the same first `limit`
elements. S is at most max(3 * limit, 60) people, so it is kept whole. Among the rest of P, elements that share an
exact-id flag and a name rank are ordered by (source-only, date descending, position in U): a key that does not
depend on the query, so one precomputed rank orders them and the best `limit` of each group are enough. The
name-rank group of an element is found from word-prefix postings (a superset of the people whose name starts with the
query words) and then computed exactly.

What is searched. Typeahead matches pieces of a query only against what a person reads: the label, the current
positions and the aliases. A machine identifier is never searched by its pieces: not a node id, not the
`politico:<uuid>` spelling, not a bare uuid, not a source id such as a bioguide id. Before this rule the profile text
began with the node id, so "co", "po" and "li" (inside `politico:`) matched every profile. A query that is one of those
identifiers in full still finds the person it names, and that person ranks first: the query is trimmed and casefolded
and compared with id_forms(), the whole id and, for a Politico profile, its bare uuid. A fragment of an identifier
finds nothing by way of the identifier. The static graph's search (StaticGraph.search_ids) follows the same rule.
"""
import heapq
import re

WORD = re.compile(r'[\w]+')
POLITICO = 'politico:'


def id_forms(node_id):
    """The spellings of a node id that a query may equal in full, casefolded: the id itself and, for a Politico
    profile (`politico:<uuid>`), its bare uuid."""
    folded = str(node_id).casefold()
    if folded.startswith(POLITICO) and len(folded) > len(POLITICO):
        return folded, folded[len(POLITICO):]
    return (folded,)


def folded_query(query):
    """The query as an identifier is compared: trimmed, any case."""
    return str(query).strip().casefold()


def order_candidates(nodes, query, limit):
    """The four stable sorts of the service search, then the cut. `nodes` is the candidate list in U order."""
    nodes.sort(key=lambda n: (n.get('last_reported_at') or n.get('source_as_of') or '', n['label']), reverse=True)
    nodes.sort(key=lambda n: (n['kind'] != 'person', n.get('source_only_identity', False)))
    terms = WORD.findall(query.casefold())

    def name_rank(node):
        words = WORD.findall(node.get('label', '').casefold())
        exact = words == terms
        all_names = bool(terms) and all(any(w.startswith(t) for w in words) for t in terms)
        return (not exact, not all_names)
    nodes.sort(key=name_rank)
    named = folded_query(query)
    nodes.sort(key=lambda n: named not in id_forms(n['id']))  # a person the query names in full, by id or bare uuid, first
    return nodes[:limit]


def eligible(node, excluded):
    return node.get('kind') == 'person' and node['id'] not in excluded


def search_people_legacy(static, profile_nodes, profile_search, excluded, query, limit):
    """The service search, reference form: every match is collected, filtered and sorted."""
    ids = static.search_ids(query, max(limit * 3, 60))
    nodes = {node_id: profile_nodes.get(node_id, static.nodes[node_id]) for node_id in ids}
    tokens = query.casefold().split()
    named = folded_query(query)
    nodes.update((node_id, profile_nodes[node_id]) for node_id, text in profile_search
                 if named in id_forms(node_id) or (tokens and all(t in text for t in tokens)))
    return order_candidates([n for n in nodes.values() if eligible(n, excluded)], query, limit)


class ProfileSearchIndex:
    """What search_people needs to know about one published set of profile people. Immutable once built."""

    @staticmethod
    def haystack(node):
        """The text a profile is searched by: what a person reads, never an identifier. The refresh builds profile_search from it."""
        return ' '.join([node['label'], *node.get('current_positions', []), *node.get('aliases', [])]).casefold()

    def __init__(self, profile_nodes, profile_search, excluded):
        self.profile_search, self.excluded = profile_search, excluded
        self.ok = False
        try:
            self._build(profile_nodes, profile_search, excluded)
            self.ok = True
        except Exception:  # an unusual node must never stop a refresh: the legacy search answers instead
            self.ok = False

    def _build(self, profile_nodes, profile_search, excluded):
        count = len(profile_search)
        self.ids = [node_id for node_id, _ in profile_search]
        self.texts = [text for _, text in profile_search]
        # Every entry under each way a query can name it in full (id_forms): the only way an id is searched.
        self.exact = {}
        for position, node_id in enumerate(self.ids):
            for form in id_forms(node_id):
                self.exact.setdefault(form, []).append(position)
        self.unique_ids = len(set(self.ids)) == count
        self.nodes = [profile_nodes.get(node_id) for node_id in self.ids]
        self.missing = frozenset(p for p, node in enumerate(self.nodes) if node is None)
        self.eligible = [node is not None and eligible(node, excluded) for node in self.nodes]
        self.own_id = {}
        self.words = [()] * count
        postings, no_words, keys = {}, [], {}
        for position, node in enumerate(self.nodes):
            if not self.eligible[position]:
                continue
            for form in id_forms(node['id']):
                self.own_id.setdefault(form, []).append(position)
            words = tuple(WORD.findall(node.get('label', '').casefold()))
            self.words[position] = words
            if not words:
                no_words.append(position)
            for word in set(words):
                for length in (1, 2, 3):
                    if len(word) >= length:
                        postings.setdefault(word[:length], set()).add(position)
            keys[position] = ((node.get('last_reported_at') or node.get('source_as_of') or '', node['label']),
                              node.get('source_only_identity', False))
        self.postings = {prefix: frozenset(found) for prefix, found in postings.items()}
        self.no_words = frozenset(no_words)
        # The order of eligible people when nothing but (source-only, date descending, position) decides.
        order = sorted(keys, key=lambda p: keys[p][0], reverse=True)
        order.sort(key=lambda p: keys[p][1])
        self.rank = [0] * count
        for place, position in enumerate(order):
            self.rank[position] = place

    def matches(self, query, tokens):
        """Positions, ascending, of profile entries the reference scan would take: one the query names in full by its id
        (or bare uuid), or one whose text contains every query word as a substring."""
        found = set(self.exact.get(folded_query(query), ()))
        if tokens:
            first, others = tokens[0], tokens[1:]
            if others:
                found.update([p for p, text in enumerate(self.texts) if first in text and all(t in text for t in others)])
            else:
                found.update([p for p, text in enumerate(self.texts) if first in text])
        return sorted(found)

    def name_groups(self, query, candidates):
        """{position: (not exact, not all names)} for every candidate that can rank above the last name-rank group."""
        terms = WORD.findall(query.casefold())
        if terms:
            lists = sorted((self.postings.get(term[:3], frozenset()) for term in terms), key=len)
            narrowed = lists[0].intersection(*lists[1:]) if len(lists) > 1 else lists[0]
        else:
            narrowed = self.no_words
        result = {}
        for position in narrowed:
            if position not in candidates:
                continue
            words = self.words[position]
            group = (not (list(words) == terms), not (bool(terms) and all(any(w.startswith(t) for w in words) for t in terms)))
            if group != (True, True):
                result[position] = group
        return result


def search_people(static, profile_nodes, index, excluded, query, limit):
    if not index.ok or limit < 1 or excluded is not index.excluded:
        return search_people_legacy(static, profile_nodes, index.profile_search, excluded, query, limit)
    ids = static.search_ids(query, max(limit * 3, 60))
    in_static = set(ids)
    chosen = [node for node in (profile_nodes.get(node_id, static.nodes[node_id]) for node_id in ids) if eligible(node, excluded)]
    tokens = query.casefold().split()
    positions = index.matches(query, tokens)
    if index.missing:
        for position in positions:
            if position in index.missing:
                raise KeyError(index.ids[position])
    profile_ids, is_eligible = index.ids, index.eligible
    if index.unique_ids:
        candidates = [p for p in positions if is_eligible[p] and profile_ids[p] not in in_static]
    else:
        # One entry per id, as the dictionary of the legacy search had: the first position wins.
        candidates, seen = [], set(in_static)
        for position in positions:
            node_id = profile_ids[position]
            if node_id not in seen:
                seen.add(node_id)
                if is_eligible[position]:
                    candidates.append(position)
    if candidates:
        rank = index.rank.__getitem__
        pool = set(candidates)
        named = index.name_groups(query, pool)
        first = [p for p in index.own_id.get(folded_query(query), ()) if p in pool]  # named by id or bare uuid: above every name rank
        taken = set(first)
        groups = {}
        for position, group in named.items():
            if position not in taken:
                groups.setdefault(group, []).append(position)
                taken.add(position)
        picked = set(first)
        for members in groups.values():
            picked.update(heapq.nsmallest(limit, members, key=rank))
        remaining = [p for p in candidates if p not in taken] if taken else candidates
        picked.update(heapq.nsmallest(limit, remaining, key=rank))
        chosen.extend(index.nodes[p] for p in sorted(picked))
    return order_candidates(chosen, query, limit)