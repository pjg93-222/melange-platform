"""Population-only comparison against the pinned graph, with unchanged edge weights.

The diagnostic projection is not the cleaned work graph. Existing payroll-derived
edges may still need qualification. Never publish this projection as candidate ties.
"""
from array import array
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components, shortest_path
from scipy.stats import spearmanr


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def pagerank(graph, alpha=.85, tol=1e-12):
    n = graph.shape[0]
    if not n:
        return np.array([]), 0
    strength = np.asarray(graph.sum(axis=1)).ravel()
    inv = np.divide(1., strength, out=np.zeros(n), where=strength > 0)
    transition = sparse.diags(inv) @ graph
    score = np.full(n, 1. / n)
    for iteration in range(1, 1001):
        updated = alpha * (transition.T @ score + score[strength == 0].sum() / n) + (1 - alpha) / n
        if np.abs(updated - score).sum() < tol:
            return updated, iteration
        score = updated
    raise ValueError('Diagnostic PageRank did not converge')


def path_from_predecessors(predecessors, origin, target):
    if origin == target:
        return [origin]
    path, node = [target], target
    while node != origin:
        node = int(predecessors[node])
        if node < 0:
            return []
        path.append(node)
        if len(path) > len(predecessors):
            raise ValueError('Predecessor cycle')
    return list(reversed(path))


def evaluate(directory):
    started = time.monotonic()
    directory = Path(directory).resolve()
    output = directory / 'population_evaluation.json'
    if output.exists():
        raise FileExistsError('Preserve previous evaluation')
    receipt = json.loads((directory / 'spine_receipt.json').read_text())
    verification = json.loads((directory / 'spine_verification.json').read_text())
    if verification['status'] != 'PASS' or verification['receipt_sha256'] != sha(directory / 'spine_receipt.json'):
        raise ValueError('Verify the candidate before population evaluation')
    db = sqlite3.connect(Path(receipt['database']['path']).as_uri() + '?mode=ro', uri=True)
    people = dict(db.execute('SELECT id,label FROM people'))
    admitted = {r[0] for r in db.execute("SELECT DISTINCT person_id FROM memberships WHERE population IN ('staffer_current_reported','staffer_historical','staffer_undated')")}
    current = {r[0] for r in db.execute("SELECT DISTINCT person_id FROM memberships WHERE population='staffer_current_reported'")}
    baseline = json.loads(db.execute("SELECT value FROM metadata WHERE key='baseline'").fetchone()[0])
    base_spec = next(x for x in receipt['inputs'] if x['id'] == 'baseline_receipt')
    base = Path(base_spec['path']).parent
    specs = {f['role']: f for f in baseline['files']}
    scorepath = base / specs['staffer_social_node_scores']['path']
    edgepath = base / specs['staffer_social_edges']['path']
    for role, path in [('staffer_social_node_scores', scorepath), ('staffer_social_edges', edgepath)]:
        if sha(path) != specs[role]['sha256']:
            raise ValueError('Baseline artifact hash mismatch: ' + role)
    ids, old_degree, old_score = [], [], []
    for row in rows(scorepath):
        ids.append(row['node_id'])
        old_degree.append(row['co_tenure_degree'])
        old_score.append(next(x['score'] for x in row['connectedness_pagerank_exploratory'] if x['alpha'] == .85))
    lookup = {ident: i for i, ident in enumerate(ids)}
    selected = sorted(lookup[i] for i in admitted & lookup.keys())
    selected_set = set(selected)
    print('Baseline comparison: verified inputs; scanning complete edge file.', flush=True)
    u, v, weights = array('I'), array('I'), array('d')
    edge_status = Counter()
    for row in rows(edgepath):
        edge_status[row['status']] += 1
        if row['status'] != 'GUARANTEED':
            continue
        weight = row['w_tiered_lower']
        if not np.isfinite(weight) or weight <= 0:
            raise ValueError('Invalid baseline guaranteed weight')
        u.append(lookup[row['subject_id']]); v.append(lookup[row['object_id']]); weights.append(weight)
    u, v, weights = np.asarray(u, dtype=np.int32), np.asarray(v, dtype=np.int32), np.asarray(weights)
    graph = sparse.coo_matrix((np.r_[weights, weights], (np.r_[u, v], np.r_[v, u])), shape=(len(ids), len(ids))).tocsr()
    projection = graph[selected][:, selected].tocsr()
    degree = np.diff(projection.indptr)
    new_score, iterations = pagerank(projection)
    components, labels = connected_components(projection, directed=False)
    selected_ids = [ids[i] for i in selected]
    old_degree = np.asarray(old_degree)
    old_score = np.asarray(old_score)
    # Equal values receive average ranks; report order association, not raw score parity.
    correlation = float(spearmanr(old_score[selected], new_score).statistic) if len(selected) > 1 else None
    if correlation is not None and not np.isfinite(correlation):
        correlation = None
    high = sorted(range(len(selected)), key=lambda j: (-old_degree[selected[j]], selected_ids[j]))[:50]
    low = sorted(range(len(selected)), key=lambda j: (degree[j], selected_ids[j]))[:50]
    cohort = []
    for bucket, indices in [('high_baseline_degree', high), ('sparse_after_population_filter', low)]:
        for j in indices:
            ident = selected_ids[j]
            cohort.append({'cohort': bucket, 'person_id': ident, 'label': people[ident],
                           'baseline_degree': int(old_degree[selected[j]]), 'population_filtered_degree': int(degree[j]),
                           'neighbors_outside_admitted_population': int(old_degree[selected[j]] - degree[j]),
                           'meaning': 'Scope-only comparison; removed neighbors are not adjudicated false ties.',
                           'review_status': 'REQUIRES_RELATIONSHIP_REVIEW'})
    (directory / 'connectivity_review_cohorts.ndjson').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in cohort))
    # Fixed deterministic endpoints from the common cohort, exact graph search.
    route_cases = []
    positions = sorted(set([0, len(selected)//4, len(selected)//2, 3*len(selected)//4, len(selected)-1])) if selected else []
    cost_graph = graph.copy()
    cost_graph.data = 1. / cost_graph.data
    cost_projection = projection.copy()
    cost_projection.data = 1. / cost_projection.data
    for origin_j in positions:
        before, before_pred = shortest_path(cost_graph, directed=False, indices=selected[origin_j], return_predecessors=True)
        after, after_pred = shortest_path(cost_projection, directed=False, indices=origin_j, return_predecessors=True)
        for target_j in positions:
            if target_j <= origin_j:
                continue
            before_path = path_from_predecessors(before_pred, selected[origin_j], selected[target_j])
            after_path = path_from_predecessors(after_pred, origin_j, target_j)
            a, b = before[selected[target_j]], after[target_j]
            if np.isfinite(b) and b + 1e-9 < a:
                raise ValueError('Removing nodes unexpectedly shortened a positive-weight route')
            route_cases.append({'origin': selected_ids[origin_j], 'target': selected_ids[target_j],
                'baseline_cost': float(a) if np.isfinite(a) else None, 'scope_filtered_cost': float(b) if np.isfinite(b) else None,
                'baseline_path': [ids[i] for i in before_path], 'scope_filtered_path': [selected_ids[i] for i in after_path],
                'excluded_baseline_intermediaries': [ids[i] for i in before_path if i not in selected_set],
                'change': 'LOST_WITH_SCOPE' if np.isfinite(a) and not np.isfinite(b) else 'LONGER_WITH_SCOPE' if np.isfinite(a) and b > a + 1e-9 else 'UNCHANGED',
                'scope': 'Pinned work graph with reciprocal unchanged weights; not the live mixed-evidence router.'})
    (directory / 'population_route_comparison.json').write_text(json.dumps(route_cases, indent=2))
    absent = sorted(admitted - lookup.keys())
    (directory / 'directory_people_without_baseline_work_nodes.ndjson').write_text(''.join(
        json.dumps({'person_id': ident, 'label': people[ident], 'reason': 'NO_EXACT_NODE_IN_PINNED_PAYROLL_WORK_GRAPH_NOT_PROOF_OF_ISOLATION'}) + '\n' for ident in absent))
    result = {'status': 'PASS', 'scope': 'POPULATION_ONLY_DIAGNOSTIC_UNCHANGED_BASELINE_MATH',
        'candidate_receipt_sha256': sha(directory / 'spine_receipt.json'),
        'baseline_nodes': len(ids), 'baseline_guaranteed_edges': int(graph.nnz//2),
        'admitted_staffer_ids': len(admitted), 'current_reported_staffer_ids': len(current),
        'common_baseline_work_nodes': len(selected), 'directory_ids_without_baseline_work_nodes': len(absent),
        'scope_filtered_edges': int(projection.nnz//2), 'common_cohort_isolates_after_scope': int(np.sum(degree==0)),
        'common_cohort_components_after_scope': int(components),
        'largest_common_cohort_component': int(np.bincount(labels).max()) if len(labels) else 0,
        'pagerank_alpha': .85, 'pagerank_iterations': iterations,
        'common_cohort_rank_spearman': correlation,
        'route_cases': len(route_cases), 'route_changes': dict(Counter(r['change'] for r in route_cases)),
        'high_degree_review_cases': len(high), 'sparse_review_cases': len(low),
        'source_edge_status_counts': dict(edge_status),
        'runtime_seconds': round(time.monotonic()-started, 2),
        'limitations': ['Old work edges are used only to isolate population effects; they are not newly qualified candidate ties.',
                       'Live routes also use activity and explicit relationship evidence; these diagnostics do not claim to reproduce those routes.',
                       'Absent baseline work nodes are coverage gaps, not isolated humans.',
                       'New affiliations remain candidates pending identity/container/temporal validation.',
                       'No primary scoring weights, live graph, or production pointer changed.'],
        'inputs': [{'path': str(scorepath), 'sha256': specs['staffer_social_node_scores']['sha256']},
                   {'path': str(edgepath), 'sha256': specs['staffer_social_edges']['sha256']}],
        'evaluator_sha256': sha(__file__)}
    db.close()
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.directory), indent=2))
