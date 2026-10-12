"""Bounded member context from the independently built all-bills network."""
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import math

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components


class MemberContext:
    def __init__(self, edges=(), provenance=None):
        pairs = defaultdict(list)
        for a, b, weight in edges:
            weight = float(weight)
            if not math.isfinite(weight) or weight < 0:
                raise ValueError('member network weights must be finite and nonnegative')
            if a != b and weight:
                pairs[tuple(sorted((str(a), str(b))))].append(weight)
        self.ids = sorted({p for pair in pairs for p in pair})
        self.index = {p: i for i, p in enumerate(self.ids)}
        size = len(self.ids)
        matrix = np.zeros((size, size))
        for (a, b), values in pairs.items():
            # Same declared mean-symmetrization as the overall undirected loader.
            matrix[self.index[a], self.index[b]] = matrix[self.index[b], self.index[a]] = sum(values)/len(values)
        degree = matrix.sum(axis=1)
        inv = np.divide(1., np.sqrt(degree), out=np.zeros(size), where=degree > 0)
        normalized = inv[:, None] * matrix * inv[None, :]
        square = normalized @ normalized
        self.proximity = .7 * normalized + .2 * square + .1 * (square @ normalized)
        np.fill_diagonal(self.proximity, 1.)
        self.centrality = np.zeros(size)
        _, self.components = connected_components(sparse.csr_matrix(matrix), directed=False)
        for component in set(self.components):
            which = np.flatnonzero(self.components == component)
            part = matrix[np.ix_(which, which)]
            # Symmetric eigh has no nonconvergent power-iteration failure on
            # bipartite components. Only comparisons within this target's
            # component consume its normalization.
            values, vectors = np.linalg.eigh(part)
            if values[-1] > 0:
                eigen = np.abs(vectors[:, -1])
                self.centrality[which] = eigen/eigen.max()
        self.provenance = provenance or {'status': 'unavailable', 'reason': 'No member network supplied'}

    def score(self, anchors, targets):
        left_total, right_total = sum(anchors.values()), sum(targets.values())
        if not left_total or not right_total:
            return {'value': 0., 'coverage': 0., 'status': 'no_direct_member_anchor'}
        score, coverage = 0., 0.
        for member, anchor in anchors.items():
            i = self.index.get(member)
            for target, relevance in targets.items():
                j = self.index.get(target)
                if i is None or j is None or self.components[i] != self.components[j]:
                    continue
                share = anchor/left_total * relevance/right_total
                coverage += share
                score += share * self.proximity[i, j] * self.centrality[i]
        return {'value': min(1., max(0., float(score))), 'coverage': float(coverage),
                'status': 'available' if coverage else 'missing_or_incomparable_member_context'}


def load_member_context(root):
    """Use all-bills AUG, BASE fallback; never silently substitute a sector graph."""
    root = Path(root)
    files = list(root.glob('holistic_network_AUG_*.xlsx')) or list(root.glob('holistic_network_BASE_*.xlsx'))
    if not files:
        return MemberContext()
    path = max(files, key=lambda p: (p.name, p.stat().st_mtime_ns))
    manifest = path.with_suffix('.manifest.json')
    return _load(str(path), path.stat().st_mtime_ns, path.stat().st_size,
                 manifest.stat().st_mtime_ns if manifest.exists() else 0)


@lru_cache(maxsize=2)
def _load(filename, mtime, size, manifest_mtime):
    import openpyxl
    path = Path(filename)
    manifest_path = path.with_suffix('.manifest.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    layers = manifest.get('layer_scope', {})
    if any('staff' in key.lower() or 'committee' in key.lower() for key in layers):
        raise ValueError('member context must not duplicate staff or committee affiliation layers')
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook['Edges'].iter_rows(values_only=True)
        header = next(rows)
        if tuple(header[:3]) != ('source_bioguide', 'target_bioguide', 'weight'):
            raise ValueError('unexpected overall member-network columns')
        edges = [row[:3] for row in rows if row[0] and row[1]]
    finally:
        workbook.close()
    proof = {'status': 'available', 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
             'built_utc': manifest.get('built_utc'), 'layer_scope': layers,
             'graph': 'all-bills, mean-symmetrized; independent of staff employment',
             'centrality': 'eigenvector of weighted adjacency; normalized and compared only within the target component',
             'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.exists() else None}
    return MemberContext(edges, proof)
