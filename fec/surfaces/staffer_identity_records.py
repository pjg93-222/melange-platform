"""Add directly evidenced record matches to independently resolved identities.

Existing groups keep their original proofs. New components require direct
evidence between every pair of existing identities, plus conflict checks across
all their source names. A-B/B-C chains alone never establish an A-C merge.
"""
import itertools
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone

try:
    from .staffer_identity_auto import compatible, evidence_date, proof_kind, source_person_id, OVERLAY_STATUS
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_identity_auto import compatible, evidence_date, proof_kind, source_person_id, OVERLAY_STATUS


def consolidate_record_pairs(resolved, nodes, record_pairs, provenance, keep_separate_pairs=(), prior_groups=()):
    result = dict(resolved)
    aliases = dict(resolved['identity_aliases'])

    def canonical(ident):
        seen = set()
        while ident in aliases:
            if ident in seen:
                raise ValueError('Identity alias cycle')
            seen.add(ident)
            ident = aliases[ident]
        return ident

    memberships = defaultdict(set)
    for ident, node in nodes.items():
        if node.get('kind', 'person') == 'person':
            memberships[canonical(ident)].add(ident)
    for alias in aliases:
        memberships[canonical(alias)].update((alias, canonical(alias)))
    groups = {g['primary_id']: g for g in [*prior_groups, *resolved['identity_groups']]}
    denied = {frozenset((canonical(a), canonical(b))) for a, b in keep_separate_pairs}
    edges, rejected = {}, Counter()
    for record in record_pairs:
        a, b = (canonical(i) for i in record['node_ids'])
        if a == b:
            continue
        if a not in memberships or b not in memberships:
            rejected['missing_source_person'] += 1
            continue
        names = [nodes[i] for root in (a, b) for i in memberships[root] if i in nodes]
        if frozenset((a, b)) in denied or not all(compatible(x, y) for x, y in itertools.combinations(names, 2)):
            rejected['conflicting_name_or_separation'] += 1
            continue
        key = tuple(sorted((a, b)))
        evidence = {**record, 'source_provenance': [provenance], 'resolved_endpoints': list(key)}
        edges.setdefault(key, []).append(evidence)
    components = {root: {root} for root in memberships}
    for (a, b), proofs in sorted(edges.items()):
        union = components[a] | components[b]
        originals = [nodes[i] for root in union for i in memberships[root] if i in nodes]
        if not (all(tuple(sorted((x, y))) in edges and frozenset((x, y)) not in denied
                    for x, y in itertools.combinations(sorted(union), 2))
                and all(compatible(x, y) for x, y in itertools.combinations(originals, 2))):
            rejected['transitive_component_conflict'] += 1
            continue
        for root in union:
            components[root] = union
    changed, additions = set(), []
    for component in {tuple(sorted(c)) for c in components.values() if len(c) > 1}:
        primary = component[0]
        members = sorted({i for root in component for i in memberships[root]})
        parents = [deepcopy(g) for root in component for g in groups.values() if canonical(g['primary_id']) == root]
        old_proofs = [e for group in parents for e in group.get('provenance', {}).get('evidence', [])]
        proofs = old_proofs + [e for a, b in itertools.combinations(component, 2) for e in edges[(a, b)]]
        kinds = sorted({proof_kind(e) for e in proofs})
        basis = '_AND_'.join(kind.upper() for kind in kinds) + '_RESOLVED'
        dated = [d for d in (evidence_date(e) for e in proofs) if d[0]]
        asserted, date_basis = max(dated, key=lambda d: d[0]) if dated else (datetime.now(timezone.utc).isoformat(), 'resolution_run_time')
        sources = {source_person_id(i) for i in members}
        sources.update(source_person_id(i) for g in parents for i in g.get('source_person_ids', []))
        label = next((g['display_name'] for g in parents if g['primary_id'] == primary), nodes[primary]['label'])
        additions.append({'primary_id': primary, 'alias_ids': [i for i in members if i != primary],
            'source_person_ids': sorted(sources), 'display_name': label, 'status': OVERLAY_STATUS,
            'resolution_status': basis + '_SAME_PERSON', 'basis': basis, 'asserted_at': asserted,
            'asserted_at_basis': date_basis, 'canonical_identity_promoted': False,
            'provenance': {'method': resolved['evidence_version'], 'basis': basis, 'proof_kinds': kinds,
                'evidence_methods': sorted({e['method'] for e in proofs}), 'evidence': proofs,
                'prior_identity_groups': parents, 'asserted_at_basis': date_basis,
                'citations': [c for e in proofs for c in e.get('citations', [])]}})
        changed.update(component)
    final_groups = [g for g in resolved['identity_groups'] if canonical(g['primary_id']) not in changed] + additions
    for group in additions:
        aliases.update({i: group['primary_id'] for i in group['alias_ids']})
    aliases = {i: canonical(i) for i in aliases}
    result['identity_aliases'] = aliases
    result['identity_groups'] = sorted(final_groups, key=lambda g: g['primary_id'])
    result['unresolved_candidates'] = [c for c in resolved['unresolved_candidates'] if len({canonical(i) for i in c['node_ids']}) > 1]
    result['record_reconciliation'] = {'evidenced_pairs': len(edges), 'new_alias_reductions': len(aliases) - len(resolved['identity_aliases']),
        'rejected': dict(rejected), 'source_provenance': provenance}
    result['counts'] = {**resolved['counts'], 'automatic_resolved_groups': len(final_groups),
        'resolved_groups_by_basis': dict(Counter(g['basis'] for g in final_groups)),
        'new_aliases': sum(len(g['alias_ids']) for g in final_groups),
        'unresolved_candidate_pairs': len(result['unresolved_candidates'])}
    return result
