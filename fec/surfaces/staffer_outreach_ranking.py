"""Rank tied candidates relative to a named origin and target on a verified graph.

This command reads the COMPLETE co-tenure operator. A sampled display graph is
insufficient evidence for these costs. It writes a separate query result only.
"""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import dijkstra

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fec.staffer_network.ranking_math import rank_outreach_candidates


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def route_costs(n, u, v, weights, origin, target):
    weights = np.asarray(weights, dtype=float)
    if np.any(~np.isfinite(weights)) or np.any(weights <= 0):
        raise ValueError('only finite positive co-tenure weights can supply routes')
    costs = 1. / weights
    graph = sparse.coo_matrix((np.r_[costs, costs], (np.r_[u, v], np.r_[v, u])), shape=(n, n)).tocsr()
    return dijkstra(graph, directed=False, indices=[origin, target])


def rank(generation, verification, origin, target, candidate_ids):
    check = json.loads(verification.read_text())
    if check.get('status') != 'PASS' or check.get('failures'):
        raise ValueError('generation verification has not passed')
    receipt_path = generation / 'staffer_social_network_receipt.json'
    binding = next((r for r in check['twin_byte_identity'] if r['path'] == receipt_path.name), {})
    if sha(receipt_path) != binding.get('primary_sha256'):
        raise ValueError('verification does not bind this generation receipt')
    receipt = json.loads(receipt_path.read_text())
    paths = {}
    for role in ('staffer_social_node_scores', 'staffer_social_edges'):
        entry = next(r for r in receipt['files'] if r['role'] == role)
        path = generation / entry['path']
        if not path.is_file() or path.stat().st_size != entry['bytes'] or sha(path) != entry['sha256']:
            raise ValueError('artifact binding failed: ' + role)
        paths[role] = path
    nodes = [r for r in rows(paths['staffer_social_node_scores']) if r['node_kind'] == 'person']
    index = {r['node_id']: i for i, r in enumerate(nodes)}
    if any(p not in index for p in [origin, target, *candidate_ids]):
        raise ValueError('origin, target and candidates must be exact IDs in this generation')
    u, v, weight = [], [], []
    for row in rows(paths['staffer_social_edges']):
        if row['layer'] == 'co_tenure' and row['status'] == 'GUARANTEED':
            u.append(index[row['subject_id']]); v.append(index[row['object_id']])
            weight.append(row['w_tiered_lower'])
    distances = route_costs(len(nodes), u, v, weight, index[origin], index[target])
    candidates = []
    for ident in sorted(set(candidate_ids)):
        node = nodes[index[ident]]
        score = next(r['score'] for r in node['connectedness_pagerank_exploratory'] if r['alpha'] == .85)
        candidates.append({'node_id': ident, 'pagerank_085': float(format(score, '.6g'))})
    left = {p: distances[0, index[p]] for p in candidate_ids}
    right = {p: distances[1, index[p]] for p in candidate_ids}
    return {'origin': origin, 'target': target, 'generation': generation.name,
            'receipt_sha256': sha(receipt_path), 'complete_search': True,
            'cost_rule': 'sum of independently shortest origin-candidate and candidate-target legs using reciprocal positive co-tenure weights; legs may retrace nodes',
            'ranking_rule': 'PageRank, then origin-candidate-target distance, then smaller worst leg; remaining ties labeled',
            'candidates': rank_outreach_candidates(candidates, left, right)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--generation', type=Path, required=True)
    parser.add_argument('--verification', type=Path, required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--candidates', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(rank(args.generation, args.verification, args.origin,
                                         args.target, args.candidates), indent=2, allow_nan=False) + '\n')
