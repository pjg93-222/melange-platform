# -*- coding: utf-8 -*-
"""Build the typed canonical graph and its non-conflated network measures."""

from __future__ import annotations

from array import array
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterator, Mapping

import numpy as np

from .network_math import (
    balanced_symmetric_adjacency,
    bridging_diversity,
    componentwise_eigenvector_centrality,
    connectedness_pagerank,
    deduplicate_undirected_layer_edges,
    group_balanced_teleport,
    layer_balanced_transition,
)
from .source_manifest import canonical_json_bytes, sha256_file


EDGE_SCHEMA_ID = "staffer-network-canonical-network-edge-v1"
SCORE_SCHEMA_ID = "staffer-network-network-score-v1"
RECEIPT_SCHEMA_ID = "staffer-network-network-build-receipt-v1"


class GraphBuildError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _gzip_writer(path: Path) -> Iterator[gzip.GzipFile]:
    raw = path.open("wb")
    compressed = gzip.GzipFile(
        # Level 6 materially shortens multi-million-row production builds while
        # retaining deterministic mtime-free gzip output and strong compression.
        filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0
    )
    try:
        yield compressed
    finally:
        compressed.close()
        raw.close()


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise GraphBuildError(f"blank row: {path}:{line_number}")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise GraphBuildError(f"non-object row: {path}:{line_number}")
            yield row


def _edge_id(value: str) -> str:
    return "network:edge:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _top_overlap(left: np.ndarray, right: np.ndarray, size: int = 1000) -> float:
    size = min(size, left.size)
    if size == 0:
        return 1.0
    left_top = set(np.argpartition(left, -size)[-size:].tolist())
    right_top = set(np.argpartition(right, -size)[-size:].tolist())
    return len(left_top & right_top) / size


def build_network_generation(
    *,
    canonical_root: str | Path,
    output_root: str | Path,
    generated_at: str | None = None,
    include_unambiguous_identity_candidates: bool = True,
) -> dict[str, Any]:
    """Materialize graph edges, component eigenvectors, PageRank, and bridging.

    Candidate identity links, when enabled, form an explicitly exploratory
    layer and do not become canonical memberships or canonical facts.
    """

    canonical = Path(canonical_root).resolve()
    output = Path(output_root).resolve()
    entities_path = canonical / "canonical_entities.ndjson.gz"
    facts_path = canonical / "canonical_facts.ndjson.gz"
    links_path = canonical / "canonical_identity_links.ndjson.gz"
    receipt_path = canonical / "canonical_generation_receipt.json"
    for path in (entities_path, facts_path, links_path, receipt_path):
        if not path.is_file():
            raise GraphBuildError(f"missing canonical input: {path}")
    canonical_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        canonical_receipt.get("status") != "PASS"
        or canonical_receipt.get("canonical_facts_materialized") is not True
        or canonical_receipt.get("canonical_entity_merges") != 0
    ):
        raise GraphBuildError("canonical generation is not eligible for graph build")
    if output.exists():
        raise GraphBuildError(f"immutable network generation exists: {output}")

    generated_at = generated_at or _now()
    node_ids: list[str] = []
    node_index: dict[str, int] = {}
    kind_names: list[str] = []
    kind_lookup: dict[str, int] = {}
    kind_codes = array("I")
    for row in _rows(entities_path):
        node_id = str(row["canonical_entity_id"])
        if node_id in node_index:
            raise GraphBuildError(f"duplicate canonical entity: {node_id}")
        kind = str(row["entity_kind"])
        if kind not in kind_lookup:
            kind_lookup[kind] = len(kind_names)
            kind_names.append(kind)
        node_index[node_id] = len(node_ids)
        node_ids.append(node_id)
        kind_codes.append(kind_lookup[kind])
    if not node_ids:
        raise GraphBuildError("canonical generation has no entities")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    edges_path = staging / "network_edges.ndjson.gz"
    scores_path = staging / "network_scores.ndjson.gz"
    subjects = array("I")
    objects = array("I")
    weights = array("f")
    layer_codes = array("I")
    layer_names: list[str] = []
    layer_lookup: dict[str, int] = {}
    layer_counts: Counter[str] = Counter()
    candidate_edges = 0

    try:
        with _gzip_writer(edges_path) as edge_stream:
            def add_edge(
                *,
                source_id: str,
                target_id: str,
                layer: str,
                raw_weight: float,
                directed: bool,
                evidence_class: str,
                support_id: str,
                precision_bucket: str,
            ) -> None:
                if source_id == target_id:
                    raise GraphBuildError("network self-loop")
                try:
                    source_index = node_index[source_id]
                    target_index = node_index[target_id]
                except KeyError as exc:
                    raise GraphBuildError(f"edge endpoint is absent: {exc.args[0]}") from exc
                if layer not in layer_lookup:
                    layer_lookup[layer] = len(layer_names)
                    layer_names.append(layer)
                subjects.append(source_index)
                objects.append(target_index)
                weights.append(float(raw_weight))
                layer_codes.append(layer_lookup[layer])
                layer_counts[layer] += 1
                row = {
                    "schema_id": EDGE_SCHEMA_ID,
                    "edge_id": _edge_id(f"{evidence_class}\0{support_id}"),
                    "subject_entity_id": source_id,
                    "object_entity_id": target_id,
                    "layer": layer,
                    "raw_weight": float(raw_weight),
                    "directed": directed,
                    "evidence_class": evidence_class,
                    "support_id": support_id,
                    "precision_bucket": precision_bucket,
                }
                edge_stream.write(canonical_json_bytes(row))

            for fact in _rows(facts_path):
                object_id = fact.get("object_entity_id")
                if object_id is None:
                    continue
                add_edge(
                    source_id=str(fact["subject_entity_id"]),
                    target_id=str(object_id),
                    layer=str(fact["layer"]),
                    raw_weight=float(fact["structural_weight"]),
                    directed=bool(fact["directed"]),
                    evidence_class="DIRECT_CANONICAL_FACT",
                    support_id=str(fact["fact_id"]),
                    precision_bucket=str(fact["precision_bucket"]),
                )
            core_edge_count = len(subjects)
            if include_unambiguous_identity_candidates:
                for link in _rows(links_path):
                    if link["link_status"] != "UNAMBIGUOUS_REVIEW_CANDIDATE":
                        continue
                    add_edge(
                        source_id=str(link["left_entity_id"]),
                        target_id=str(link["right_entity_id"]),
                        layer="identity_candidate",
                        raw_weight=1.0,
                        directed=False,
                        evidence_class="UNAMBIGUOUS_EXACT_NAME_REVIEW_CANDIDATE",
                        support_id=str(link["identity_link_id"]),
                        precision_bucket="undated-only",
                    )
                    candidate_edges += 1

        u = np.frombuffer(subjects, dtype=np.uint32).astype(np.int64, copy=False)
        v = np.frombuffer(objects, dtype=np.uint32).astype(np.int64, copy=False)
        w = np.frombuffer(weights, dtype=np.float32).astype(np.float64, copy=False)
        layer = np.frombuffer(layer_codes, dtype=np.uint32).astype(np.int32, copy=False)
        core_u, core_v, core_w, core_layer = deduplicate_undirected_layer_edges(
            u[:core_edge_count],
            v[:core_edge_count],
            w[:core_edge_count],
            layer[:core_edge_count],
        )
        # The primary measures are computed only from canonical facts.  Exact-name
        # review candidates may connect source components in a second explicitly
        # exploratory view, but cannot change primary centrality before adjudication.
        transition = layer_balanced_transition(
            len(node_ids),
            core_u,
            core_v,
            core_w,
            core_layer,
        )
        symmetric = balanced_symmetric_adjacency(transition)
        eigenvector, component_labels, eigen_receipt = (
            componentwise_eigenvector_centrality(
                symmetric,
                tolerance=1e-9,
                max_iterations=750,
                allow_partial=True,
            )
        )
        group_codes = np.frombuffer(kind_codes, dtype=np.uint32).astype(
            np.int32, copy=False
        )
        teleport = group_balanced_teleport(group_codes)
        pageranks: dict[float, np.ndarray] = {}
        pagerank_receipts: dict[str, dict[str, float | int]] = {}
        for damping in (0.70, 0.85, 0.95):
            score, convergence = connectedness_pagerank(
                transition,
                damping=damping,
                teleport=teleport,
                tolerance=1e-11,
                max_iterations=750,
            )
            pageranks[damping] = score
            pagerank_receipts[f"{damping:.2f}"] = convergence
        primary = pageranks[0.85]
        for damping in (0.70, 0.95):
            pagerank_receipts[f"{damping:.2f}"]["l1_from_0_85"] = float(
                np.abs(pageranks[damping] - primary).sum()
            )
            pagerank_receipts[f"{damping:.2f}"]["top_1000_overlap_with_0_85"] = (
                _top_overlap(pageranks[damping], primary)
            )
        bridging = bridging_diversity(transition, group_codes)
        raw_strength = np.bincount(
            np.concatenate((core_u, core_v)),
            weights=np.concatenate((core_w, core_w)),
            minlength=len(node_ids),
        )
        if candidate_edges:
            exploratory_u, exploratory_v, exploratory_w, exploratory_layer = (
                deduplicate_undirected_layer_edges(u, v, w, layer)
            )
            exploratory_transition = layer_balanced_transition(
                len(node_ids),
                exploratory_u,
                exploratory_v,
                exploratory_w,
                exploratory_layer,
            )
            exploratory_connectedness, exploratory_convergence = (
                connectedness_pagerank(
                    exploratory_transition,
                    damping=0.85,
                    teleport=teleport,
                    tolerance=1e-11,
                    max_iterations=750,
                )
            )
            exploratory_bridging = bridging_diversity(
                exploratory_transition, group_codes
            )
            exploratory_raw_strength = np.bincount(
                np.concatenate((exploratory_u, exploratory_v)),
                weights=np.concatenate((exploratory_w, exploratory_w)),
                minlength=len(node_ids),
            )
        else:
            exploratory_u, exploratory_v = core_u, core_v
            exploratory_connectedness = primary
            exploratory_convergence = pagerank_receipts["0.85"]
            exploratory_bridging = bridging
            exploratory_raw_strength = raw_strength
        component_sizes = np.bincount(component_labels)
        kind_max = np.zeros(len(kind_names), dtype=np.float64)
        np.maximum.at(kind_max, group_codes, primary)
        within_kind = np.divide(
            primary,
            kind_max[group_codes],
            out=np.zeros_like(primary),
            where=kind_max[group_codes] > 0,
        )

        with _gzip_writer(scores_path) as score_stream:
            for index, entity in enumerate(_rows(entities_path)):
                score_stream.write(
                    canonical_json_bytes(
                        {
                            "schema_id": SCORE_SCHEMA_ID,
                            "canonical_entity_id": node_ids[index],
                            "entity_kind": kind_names[group_codes[index]],
                            "display_name": entity.get("display_name"),
                            "raw_strength": float(raw_strength[index]),
                            "exploratory_raw_strength": float(
                                exploratory_raw_strength[index]
                            ),
                            "primary_isolate": bool(raw_strength[index] == 0),
                            "exploratory_isolate": bool(
                                exploratory_raw_strength[index] == 0
                            ),
                            "component_id": int(component_labels[index]),
                            "component_size": int(component_sizes[component_labels[index]]),
                            "eigenvector_component_l1": (
                                float(eigenvector[index])
                                if np.isfinite(eigenvector[index])
                                else None
                            ),
                            "connectedness_pagerank_0_70": float(pageranks[0.70][index]),
                            "connectedness_pagerank_0_85": float(primary[index]),
                            "connectedness_pagerank_0_95": float(pageranks[0.95][index]),
                            "connectedness_within_kind": float(within_kind[index]),
                            "bridging_diversity": (
                                float(bridging[index])
                                if raw_strength[index] > 0
                                else None
                            ),
                            "connectedness_exploratory_pagerank_0_85": float(
                                exploratory_connectedness[index]
                            ),
                            "bridging_exploratory": (
                                float(exploratory_bridging[index])
                                if exploratory_raw_strength[index] > 0
                                else None
                            ),
                            "composite_influence_score": None,
                        }
                    )
                )

        receipt = {
            "schema_id": RECEIPT_SCHEMA_ID,
            "generated_at": generated_at,
            "status": "PASS",
            "network_version": "typed-layer-balanced-deduplicated-contact-minimized-structural-v6",
            "canonical_receipt_sha256": sha256_file(receipt_path),
            "nodes": len(node_ids),
            "edges": len(subjects),
            "primary_evidence_edges": core_edge_count,
            "primary_scoring_edges": int(core_u.size),
            "exploratory_scoring_edges": int(exploratory_u.size),
            "duplicate_support_edges_excluded_primary": int(
                core_edge_count - core_u.size
            ),
            "primary_isolates": int(np.count_nonzero(raw_strength == 0)),
            "exploratory_isolates": int(
                np.count_nonzero(exploratory_raw_strength == 0)
            ),
            "layers": dict(sorted(layer_counts.items())),
            "identity_candidate_edges_included": candidate_edges,
            "identity_candidate_policy": (
                "UNAMBIGUOUS_REVIEW_CANDIDATES_EXPLORATORY_LAYER"
                if include_unambiguous_identity_candidates
                else "EXCLUDED"
            ),
            "primary_scores_exclude_identity_candidates": True,
            "canonical_identity_merges": 0,
            "edge_weight_formula": (
                "direct source support=1; for each unordered endpoint/layer "
                "scoring key only the strongest support is retained, so both "
                "repeated and reciprocal evidence rows collapse for scoring; "
                "each node allocates equal transition mass to each active layer, "
                "then proportionally within layer; equal-layer allocation is an "
                "explicit modeling prior, not a neutral empirical estimate"
            ),
            "scoring_directionality": (
                "all canonical facts retain source direction on evidence edges; "
                "connectedness traversal is explicitly undirected and makes no "
                "causal-direction claim"
            ),
            "temporal_policy": (
                "structural view is time-neutral; temporal_weight is retained on "
                "canonical facts and must gate any as-of/co-tenure claim; a dated "
                "filing-participation fact is not a dated employment episode"
            ),
            "eigenvector_semantics": (
                "component-local L1-normalized Perron vector of (P+P.T)/2; "
                "degree-normalized diagnostic only, not standard Bonacich "
                "centrality; values are not comparable across components; "
                "isolates=0; components failing the fixed convergence gate are "
                "null, never approximate"
            ),
            "eigenvector_convergence": eigen_receipt,
            "eigenvector_nulls_by_entity_kind": dict(
                sorted(
                    Counter(
                        kind_names[group_codes[index]]
                        for index in np.flatnonzero(~np.isfinite(eigenvector))
                    ).items()
                )
            ),
            "connectedness_semantics": (
                "PageRank on the row-stochastic equal-active-layer transition; "
                "teleport mass is equal across represented entity kinds; primary "
                "scores exclude all identity review candidates; isolates retain "
                "positive teleport-prior mass and must be excluded from ranked "
                "connectedness reads using primary_isolate=true; cross-kind "
                "comparisons are prior-shaped"
            ),
            "exploratory_connectedness_semantics": (
                "PageRank at damping=0.85 on the same operator after adding only "
                "strict one-to-one exact-name identity review candidates; not an "
                "identity finding or primary influence score; because identity "
                "candidate is its own active layer, even one candidate can receive "
                "a large share of a node's outgoing walk mass"
            ),
            "exploratory_connectedness_convergence": exploratory_convergence,
            "pagerank_sensitivity": pagerank_receipts,
            "within_kind_connectedness_semantics": (
                "primary PageRank divided by the maximum primary PageRank within "
                "the same entity kind; descriptive max scaling only, not a rank "
                "transform, and sensitive to the within-kind maximum outlier"
            ),
            "bridging_semantics": (
                "normalized Simpson diversity of layer-balanced walk mass across "
                "entity kinds; invariant to scalar tie-strength growth but "
                "constrained by which neighbor kinds each entity kind can reach "
                "and therefore not directly comparable across entity kinds"
            ),
            "composite_influence_score": None,
            "files": [
                {
                    "role": "network_edges",
                    "path": edges_path.name,
                    "schema_id": EDGE_SCHEMA_ID,
                    "records": len(subjects),
                    "bytes": edges_path.stat().st_size,
                    "sha256": sha256_file(edges_path),
                },
                {
                    "role": "network_scores",
                    "path": scores_path.name,
                    "schema_id": SCORE_SCHEMA_ID,
                    "records": len(node_ids),
                    "bytes": scores_path.stat().st_size,
                    "sha256": sha256_file(scores_path),
                },
            ],
            "failures": [],
        }
        (staging / "network_build_receipt.json").write_bytes(
            canonical_json_bytes(receipt)
        )
        os.replace(staging, output)
        return receipt
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


__all__ = [
    "GraphBuildError",
    "build_network_generation",
    "EDGE_SCHEMA_ID",
    "SCORE_SCHEMA_ID",
    "RECEIPT_SCHEMA_ID",
]
