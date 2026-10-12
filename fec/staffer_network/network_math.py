# -*- coding: utf-8 -*-
"""Parameter-explicit mathematics for the heterogeneous staffer graph.

Structural weights represent source-supported ties.  Layer balancing happens in
the transition operator, not by inventing cross-layer coefficients.  The
eigenvector score is component-local and diagnostic.  Descriptive connectedness
is PageRank on the layer-balanced transition matrix; bridging diversity is a
separate, strength-invariant composition statistic.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Sequence

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph


class NetworkMathError(ValueError):
    pass


def deduplicate_undirected_layer_edges(
    subjects: Sequence[int] | np.ndarray,
    objects: Sequence[int] | np.ndarray,
    weights: Sequence[float] | np.ndarray,
    layers: Sequence[int] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Keep the strongest support for each unordered endpoint/layer key.

    Evidence rows remain conserved elsewhere.  This function defines the
    scoring operator's non-additive support rule after direction is deliberately
    removed for connectedness traversal.
    """

    u = np.asarray(subjects, dtype=np.int64)
    v = np.asarray(objects, dtype=np.int64)
    w = np.asarray(weights, dtype=np.float64)
    layer = np.asarray(layers, dtype=np.int32)
    if not (u.size == v.size == w.size == layer.size):
        raise NetworkMathError("edge arrays must have equal length")
    if u.size == 0:
        return u, v, w, layer
    low = np.minimum(u, v)
    high = np.maximum(u, v)
    order = np.lexsort((high, low, layer))
    low_sorted = low[order]
    high_sorted = high[order]
    layer_sorted = layer[order]
    weight_sorted = w[order]
    starts = np.empty(order.size, dtype=bool)
    starts[0] = True
    starts[1:] = (
        (low_sorted[1:] != low_sorted[:-1])
        | (high_sorted[1:] != high_sorted[:-1])
        | (layer_sorted[1:] != layer_sorted[:-1])
    )
    start_indices = np.flatnonzero(starts)
    return (
        low_sorted[start_indices],
        high_sorted[start_indices],
        np.maximum.reduceat(weight_sorted, start_indices),
        layer_sorted[start_indices],
    )


def combine_independent_support(
    supports: Iterable[tuple[str, float]],
) -> float:
    """Combine support without counting repeated rows from one source twice.

    The strongest contribution per source is retained.  Independent source
    systems are then combined with noisy-OR, yielding a bounded evidence weight
    in ``[0, 1]``.  The result is evidence strength, not an identity probability.
    """

    by_source: dict[str, float] = defaultdict(float)
    for source, value in supports:
        strength = float(value)
        if not np.isfinite(strength) or not 0.0 <= strength <= 1.0:
            raise NetworkMathError("support strength must be finite and in [0, 1]")
        by_source[str(source)] = max(by_source[str(source)], strength)
    residual = 1.0
    for strength in by_source.values():
        residual *= 1.0 - strength
    return 1.0 - residual


def layer_balanced_transition(
    node_count: int,
    subjects: Sequence[int] | np.ndarray,
    objects: Sequence[int] | np.ndarray,
    weights: Sequence[float] | np.ndarray,
    layers: Sequence[int] | np.ndarray,
) -> sparse.csr_matrix:
    """Return a bidirectional, row-stochastic, equal-active-layer walk matrix.

    For node ``i`` and active layer ``l``::

        P[i,j] += (1 / active_layers[i]) * w[i,j,l] / sum_j w[i,j,l]

    This prevents a layer with millions of edges from winning merely by volume.
    Every supplied relationship is traversable in both directions for the
    connectedness operator; claim-specific causal orientation remains on the
    canonical fact and is not inferred here.
    """

    if node_count <= 0:
        raise NetworkMathError("node_count must be positive")
    u = np.asarray(subjects, dtype=np.int64)
    v = np.asarray(objects, dtype=np.int64)
    w = np.asarray(weights, dtype=np.float64)
    layer = np.asarray(layers, dtype=np.int32)
    if not (u.size == v.size == w.size == layer.size):
        raise NetworkMathError("edge arrays must have equal length")
    if u.size == 0:
        return sparse.csr_matrix((node_count, node_count), dtype=np.float64)
    if (
        np.any(u < 0)
        or np.any(v < 0)
        or np.any(u >= node_count)
        or np.any(v >= node_count)
        or np.any(u == v)
    ):
        raise NetworkMathError("edge endpoints are invalid")
    if np.any(~np.isfinite(w)) or np.any(w <= 0):
        raise NetworkMathError("edge weights must be positive and finite")
    if np.any(layer < 0):
        raise NetworkMathError("layer identifiers must be nonnegative")

    src = np.concatenate((u, v))
    dst = np.concatenate((v, u))
    arc_weight = np.concatenate((w, w))
    arc_layer = np.concatenate((layer, layer))
    active_layers = np.zeros(node_count, dtype=np.int32)
    layer_strengths: dict[int, np.ndarray] = {}
    for layer_id in np.unique(arc_layer):
        mask = arc_layer == layer_id
        strength = np.bincount(
            src[mask], weights=arc_weight[mask], minlength=node_count
        ).astype(np.float64, copy=False)
        layer_strengths[int(layer_id)] = strength
        active_layers += strength > 0
    if np.any(active_layers[src] <= 0):
        raise NetworkMathError("arc source has no active layer")

    probability = np.empty(arc_weight.size, dtype=np.float64)
    for layer_id, strength in layer_strengths.items():
        mask = arc_layer == layer_id
        probability[mask] = (
            arc_weight[mask]
            / strength[src[mask]]
            / active_layers[src[mask]]
        )
    transition = sparse.coo_matrix(
        (probability, (src, dst)), shape=(node_count, node_count)
    ).tocsr()
    transition.sum_duplicates()
    row_sums = np.asarray(transition.sum(axis=1)).ravel()
    nonzero = row_sums > 0
    if np.any(np.abs(row_sums[nonzero] - 1.0) > 1e-10):
        raise NetworkMathError(
            "equal-active-layer construction produced a non-stochastic row"
        )
    return transition


def balanced_symmetric_adjacency(
    transition: sparse.spmatrix,
) -> sparse.csr_matrix:
    """Symmetrize the walk operator for a component-local eigenvector diagnostic."""

    matrix = sparse.csr_matrix(transition, dtype=np.float64)
    if matrix.shape[0] != matrix.shape[1]:
        raise NetworkMathError("transition matrix must be square")
    result = ((matrix + matrix.T) * 0.5).tocsr()
    result.eliminate_zeros()
    return result


def componentwise_eigenvector_centrality(
    adjacency: sparse.spmatrix,
    *,
    tolerance: float = 1e-10,
    max_iterations: int = 500,
    allow_partial: bool = False,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | int | str]]:
    """Compute L1-normalized eigenvectors independently in every component.

    All components are iterated simultaneously and normalized by component after
    each multiplication.  Adding the identity removes bipartite two-cycles but
    leaves eigenvectors unchanged.  Isolates receive zero.
    """

    matrix = sparse.csr_matrix(adjacency, dtype=np.float64)
    if matrix.shape[0] != matrix.shape[1] or matrix.shape[0] == 0:
        raise NetworkMathError("adjacency must be a nonempty square matrix")
    if matrix.nnz and (np.any(~np.isfinite(matrix.data)) or np.any(matrix.data < 0)):
        raise NetworkMathError("adjacency must be finite and nonnegative")
    asymmetry = matrix - matrix.T
    if asymmetry.nnz and np.max(np.abs(asymmetry.data)) > 1e-12:
        raise NetworkMathError("componentwise eigenvector requires symmetric adjacency")

    component_count, labels = csgraph.connected_components(
        matrix, directed=False, return_labels=True
    )
    sizes = np.bincount(labels, minlength=component_count).astype(np.int64)
    degree = np.asarray(matrix.sum(axis=1)).ravel()
    active = degree > 0
    x = np.zeros(matrix.shape[0], dtype=np.float64)
    x[active] = 1.0 / sizes[labels[active]]
    residual = float("inf")
    iterations = 0
    component_residuals = np.full(component_count, float("inf"))
    for iterations in range(1, max_iterations + 1):
        y = matrix @ x + x
        sums = np.bincount(labels, weights=np.abs(y), minlength=component_count)
        denom = sums[labels]
        y = np.divide(y, denom, out=np.zeros_like(y), where=denom > 0)
        y[~active] = 0.0
        delta = np.abs(y - x)
        component_residuals.fill(0.0)
        np.maximum.at(component_residuals, labels, delta)
        residual = float(np.max(component_residuals))
        x = y
        if residual <= tolerance:
            break
    else:
        if not allow_partial:
            raise NetworkMathError(
                f"component eigenvector did not converge in {max_iterations} iterations; "
                f"residual={residual:.6g}"
            )
    converged_components = component_residuals <= tolerance
    nonconverged_nodes = ~converged_components[labels]
    if np.any(nonconverged_nodes):
        # NaN is an internal sentinel only; serializers must emit JSON null.
        x[nonconverged_nodes] = np.nan
    return x, labels, {
        "status": "PASS" if np.all(converged_components) else "PARTIAL_NULL",
        "components": int(component_count),
        "components_converged": int(np.count_nonzero(converged_components)),
        "components_nonconverged": int(np.count_nonzero(~converged_components)),
        "nodes_nonconverged": int(np.count_nonzero(nonconverged_nodes)),
        "isolates": int(np.count_nonzero(~active)),
        "iterations": int(iterations),
        "residual": residual,
    }


def group_balanced_teleport(groups: Sequence[int] | np.ndarray) -> np.ndarray:
    """Give every represented node kind equal teleport mass."""

    group = np.asarray(groups, dtype=np.int32)
    if group.ndim != 1 or group.size == 0 or np.any(group < 0):
        raise NetworkMathError("groups must be a nonempty nonnegative vector")
    unique, inverse, counts = np.unique(group, return_inverse=True, return_counts=True)
    del unique
    return 1.0 / (counts[inverse] * counts.size)


def connectedness_pagerank(
    transition: sparse.spmatrix,
    *,
    damping: float,
    teleport: Sequence[float] | np.ndarray | None = None,
    tolerance: float = 1e-12,
    max_iterations: int = 500,
) -> tuple[np.ndarray, dict[str, float | int]]:
    """Return PageRank on the explicit layer-balanced transition operator."""

    if not 0.0 < damping < 1.0:
        raise NetworkMathError("damping must be strictly between zero and one")
    matrix = sparse.csr_matrix(transition, dtype=np.float64)
    n = matrix.shape[0]
    if matrix.shape != (n, n) or n == 0:
        raise NetworkMathError("transition must be a nonempty square matrix")
    row_sums = np.asarray(matrix.sum(axis=1)).ravel()
    if np.any((row_sums > 0) & (np.abs(row_sums - 1.0) > 1e-10)):
        raise NetworkMathError("non-dangling transition rows must sum to one")
    if teleport is None:
        v = np.full(n, 1.0 / n, dtype=np.float64)
    else:
        v = np.asarray(teleport, dtype=np.float64)
        if v.shape != (n,) or np.any(v < 0) or not np.isclose(v.sum(), 1.0):
            raise NetworkMathError("teleport vector must be nonnegative and sum to one")
    dangling = row_sums == 0
    rank = v.copy()
    residual = float("inf")
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        updated = (1.0 - damping) * v
        updated += damping * (matrix.T @ rank)
        updated += damping * float(rank[dangling].sum()) * v
        residual = float(np.abs(updated - rank).sum())
        rank = updated
        if residual <= tolerance:
            break
    else:
        raise NetworkMathError(
            f"PageRank did not converge in {max_iterations} iterations; "
            f"residual={residual:.6g}"
        )
    rank /= rank.sum()
    return rank, {"iterations": int(iterations), "residual_l1": residual}


def bridging_diversity(
    transition: sparse.spmatrix,
    target_groups: Sequence[int] | np.ndarray,
) -> np.ndarray:
    """Normalized Simpson diversity of each node's tie mass across groups.

    Because it operates on row-normalized mass, the result is invariant under a
    scalar increase in a node's total edge strength and does not restate degree.
    """

    matrix = sparse.csr_matrix(transition, dtype=np.float64)
    groups = np.asarray(target_groups, dtype=np.int32)
    if matrix.shape[0] != groups.size or np.any(groups < 0):
        raise NetworkMathError("target group vector does not match transition")
    unique = np.unique(groups)
    if unique.size <= 1:
        return np.zeros(groups.size, dtype=np.float64)
    coo = matrix.tocoo()
    squared_mass = np.zeros(groups.size, dtype=np.float64)
    for group in unique:
        mask = groups[coo.col] == group
        mass = np.bincount(
            coo.row[mask], weights=coo.data[mask], minlength=groups.size
        )
        squared_mass += mass * mass
    raw = np.clip(1.0 - squared_mass, 0.0, 1.0)
    normalized = raw / (1.0 - 1.0 / unique.size)
    row_sums = np.asarray(matrix.sum(axis=1)).ravel()
    normalized[row_sums == 0] = 0.0
    return np.clip(normalized, 0.0, 1.0)


__all__ = [
    "NetworkMathError",
    "balanced_symmetric_adjacency",
    "bridging_diversity",
    "combine_independent_support",
    "componentwise_eigenvector_centrality",
    "connectedness_pagerank",
    "deduplicate_undirected_layer_edges",
    "group_balanced_teleport",
    "layer_balanced_transition",
]
