"""Greedy alternatives with explicit contact and evidence exclusions.

Each search is optimal under its accumulated exclusions. The portfolio is
not a globally optimal disjoint-path set and does not imply independent success.
"""
import re
import threading
import unicodedata

try:
    from .staffer_route_cancel import check_cancelled
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_route_cancel import check_cancelled


def contact_key(node, fallback):
    label = node.get('label') or node.get('name')
    if not label:
        return 'id:' + fallback
    label = unicodedata.normalize('NFKD', label).casefold()
    return 'name:' + ' '.join(re.findall(r'[^\W_]+', label, re.UNICODE))


_CONTACT_LOCK = threading.Lock()


def _contact_groups(router):
    """Node ids by contact key for the router's nodes mapping, built once and kept on the router.

    What it is built from, and so what invalidates it: the mapping object itself (held, so its identity cannot be
    reused), its length, and router.nodes_revision, which LiveService bumps when it installs nodes whose label, kind or
    name status differ. A mapping that is replaced by an equal one is indexed again on its first use; one that has no
    nodes_revision (a router built outside the service) is keyed by the mapping and its length alone.
    """
    nodes = router.nodes
    token = (len(nodes), getattr(router, 'nodes_revision', None))
    held = getattr(router, '_contact_groups', None)
    if held is None or held[0] is not nodes or held[1] != token:
        with _CONTACT_LOCK:
            held = getattr(router, '_contact_groups', None)
            if held is None or held[0] is not nodes or held[1] != token:
                groups = {}
                for ident, node in nodes.items():
                    groups.setdefault(contact_key(node, ident), []).append(ident)
                held = router._contact_groups = (nodes, token, groups)
    return held[2]


def contact_group(router, people):
    # Conservative search exclusion only, never an identity merge. Identical
    # displayed names must not manufacture an apparently independent contact.
    groups = _contact_groups(router)
    found = set()
    for key in {contact_key(router.nodes.get(p, {}), p) for p in people}:
        found.update(groups.get(key, ()))
    return found


def evidence_contexts(hop):
    if hop.get('work_evidence'):
        return evidence_contexts(hop['work_evidence'])
    contexts = {w.get('context_group') or w.get('employer_id') for w in hop.get('workplaces', [])}
    contexts.discard(None)
    if contexts:
        return contexts
    # Group recurring filings by organization rather than quarterly event ID.
    firm = hop.get('firm_id')
    via = hop.get('via_organization') or {}
    contexts = {firm, via.get('id')}
    if not any(contexts):
        contexts.add(hop.get('employer_id') or hop.get('policy_id'))
    return contexts - {None, ''}


def route_contexts(route):
    return set().union(*(evidence_contexts(h) for h in route.get('hops', [])))


def distinct_routes(router, origin, targets, options, progress):
    started = router._clock()
    count, strategy = options['route_count'], options['alternative_strategy']
    blocked = set(options['_excluded_nodes'])
    edges = set(options['_excluded_edges'])
    contexts = set(options['_excluded_contexts'])
    remaining = set(targets)
    # A set of destinations (and an explicit office) must use different contacts.
    office = bool(options.get('_target_office')) or len(targets) > 1
    accepted, first, last = [], None, None
    prior_people, prior_edges, prior_contexts = set(), set(), set()
    while first is None or (remaining and len(accepted) < count):
        check_cancelled()
        request = {**options, 'route_count': 1, 'alternative_strategy': 'ranked',
                   '_excluded_nodes': tuple(sorted(blocked)),
                   '_excluded_edges': tuple(sorted(edges)),
                   '_excluded_contexts': tuple(sorted(contexts))}
        last = router.route_many(origin, sorted(remaining), request, progress)
        first = first or last
        # An office search leaves its uncovered staff out once (D-086): they stay out of every later pass, and each route
        # reports the request's count, as the first pass counted it, not the count of the staff that pass was given, and the
        # basis of that count (what coverage they lack), which is the request's too.
        left_out = {key: first[key] for key in ('uncovered_staff_count', 'uncovered_staff_ids', 'uncovered_basis') if key in first}
        remaining.difference_update(left_out.get('uncovered_staff_ids', ()))
        if last['status'] != 'found':
            break
        ids = last['node_ids']
        people = set(ids[1:] if office else ids[1:-1])
        pairs = {tuple(sorted((a,b))) for a,b in zip(ids, ids[1:])}
        evidence = route_contexts(last)
        route = {**last, **left_out, 'route_rank': len(accepted)+1, 'alternative_strategy': strategy,
                 'optimality_scope': 'best route under exclusions from earlier choices',
                 'dependence_on_previous_routes': {
                     'shared_intermediaries': len(people & prior_people),
                     'shared_staff_edges': len(pairs & prior_edges),
                     'shared_workplace_contexts': len(evidence & prior_contexts),
                     'scope': 'recorded evidence only; shared endpoints remain; no independence claim'}}
        if accepted:
            best = accepted[0]
            best_ids = best['node_ids']
            best_edges = {tuple(sorted((a,b))) for a,b in zip(best_ids, best_ids[1:])}
            route['dependence_on_best_route'] = {
                'shared_intermediaries': len(set(ids[1:-1]) & set(best_ids[1:-1])),
                'shared_staff_edges': len(pairs & best_edges),
                'shared_workplace_contexts': len(evidence & route_contexts(best)),
                'same_terminal_contact': ids[-1] == best_ids[-1],
                'scope': 'recorded evidence only; no independence claim'}
        accepted.append(route)
        if progress:
            # An accepted route is final; later searches only add alternatives.
            progress({'routes_found': len(accepted), 'routes_requested': count, 'accepted_route': route})
        prior_people.update(people)
        prior_edges.update(pairs)
        prior_contexts.update(evidence)
        blocked.update(contact_group(router, people) - ({origin} if office else {origin, *targets}))
        # Office destinations already used cannot reappear as intermediaries.
        if office:
            used = contact_group(router, {ids[-1]})
            remaining.difference_update(used)
            blocked.update(used - {origin})
        edges.update(pairs)
        if strategy == 'independent':
            contexts.update(evidence)
        if not pairs and not office:
            break
    return {**first, 'routes': accepted, 'routes_requested': count,
            'routes_returned': len(accepted), 'alternative_strategy': strategy,
            'portfolio_optimal': False,
            'alternatives_complete': not (last and last['status'] not in ('found', 'unreachable')),
            'alternatives_stop_reason': ('requested_count' if len(accepted) == count else
                'search_error' if last and last['status'] not in ('found', 'unreachable') else
                'no_further_route_under_accumulated_exclusions'),
            'diversity_semantics': 'Greedy contact-disjoint routes; same displayed names are conservatively grouped for search only. '
                + ('Also excludes connections whose selected evidence shares a recorded workplace or organization.'
                   if strategy == 'independent' else 'Workplaces and organizations may still be shared.'),
            'search_elapsed_ms': round((router._clock()-started)*1000, 2)}
