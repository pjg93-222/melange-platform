"""Bounded display projection: people are vertices; institutions are evidence.

This is a view, never a replacement for the verified model. Employers, offices,
committees and activities remain internal routing hubs and are not drawn.
"""
from __future__ import annotations

import hashlib
import re
import time


PERSON_FIELDS = ("id", "label", "kind", "name_status", "identity_tier", "review_status",
                 "verification", "source", "source_only_identity", "source_person_ids",
                 "identity_status", "canonical_eligible", "profile_pending")
DEFAULT_SOURCE_IDS = ("lda:lobbyist:147664", "lda:lobbyist:147663", "lda:lobbyist:148612", "lda:lobbyist:154321")
DEFAULT_NAMES = ("Gordon Holzberg", "Stephanie Belk", "Greg Mesack", "Kathleen Coulombe")
NODE_LIMIT = 500
SEED_LIMIT = 8
# Effort is bounded by neighbor checks so one revision always projects one map; the clock is only a
# safety valve, and a map it cuts is reported truncated, never cached or described as complete.
NEIGHBOR_CHECKS = 8000
SEED_TIMEOUT_MS = 1500


def project_people(static, router, query="", limit=300, profile_nodes=None, identity_aliases=None):
    """Return a lightweight people-only graph; caller caches by source revision."""
    started = time.monotonic()
    query = str(query).strip()
    limit = max(1, min(int(limit), NODE_LIMIT))
    profiles, aliases = profile_nodes or {}, identity_aliases or {}
    selected, matches, edges = {}, set(), {}
    search_limited = time_limited = False
    queried = 0

    def canonical(node_id):
        # Alias links are explicit user/identity overlays supplied by the service.
        seen = set()
        while node_id in aliases and node_id not in seen:
            seen.add(node_id)
            node_id = aliases[node_id]
        return node_id

    def lookup(node_id):
        node_id = canonical(node_id)
        return profiles.get(node_id) or static.nodes.get(node_id) or router.nodes.get(node_id)

    def add(node_id, match=False):
        node_id = canonical(node_id)
        node = lookup(node_id)
        if not node or node.get("kind", node.get("node_kind")) != "person":
            return False
        if node_id not in selected and len(selected) >= limit:
            return False
        selected[node_id] = node
        if match:
            matches.add(node_id)
        return True

    if query:
        exact = canonical(query)
        if lookup(exact):
            add(exact, True)
        for node_id in static.search_ids(query, max(limit * 3, 60)):
            add(node_id, True)
        tokens = re.findall(r"\w+", query.casefold())
        for node_id, node in profiles.items():
            if canonical(node_id) in matches:
                continue
            words = re.findall(r"\w+", str(node.get("label", "")).casefold())
            if tokens and all(any(word.startswith(token) for word in words) for token in tokens):
                add(node_id, True)
    else:
        provider = getattr(router, "activity_provider", None)
        source_mapping = getattr(provider, "to_node", {})
        for source_id, name in zip(DEFAULT_SOURCE_IDS, DEFAULT_NAMES):
            exact = source_mapping.get(source_id) or "person:" + source_id
            if not add(exact):
                for node_id in static.search_ids(name, 3):
                    if add(node_id):
                        break
        # A few actual model endpoints keep the default useful in smaller corpora.
        for edge in static.edges[:100]:
            if len(selected) >= min(SEED_LIMIT, limit):
                break
            if edge.get("layer") == "co_tenure":
                add(edge["source"])
                add(edge["target"])
    seeds = list(selected)
    if query:
        seeds = [node_id for node_id in seeds if node_id in matches]

    def add_hop(hop):
        source, target = canonical(hop["source"]), canonical(hop["target"])
        if source == target or not lookup(source) or not lookup(target):
            return
        if lookup(source).get("kind") != "person" or lookup(target).get("kind") != "person":
            return
        missing = len({source, target} - selected.keys())
        if len(selected) + missing > limit:
            return
        add(source)
        add(target)
        relation = hop["relationship"]
        key = (*sorted((source, target)), relation)
        previous = edges.get(key)
        if previous and (previous.get("end_exclusive") or "") >= (hop.get("end_exclusive") or ""):
            return
        evidence_id = hop.get("activity_id") or hop.get("employer_id") or ""
        identifier = hashlib.sha256("|".join((*key, evidence_id)).encode()).hexdigest()[:24]
        edges[key] = {"id": "people:" + identifier, "source": source, "target": target,
            "layer": relation, "relationship": relation, "weight": 0, "cost": hop.get("cost"),
            "display_only": True, "operator_included": False, "canonical_eligible": False,
            "temporal_status": hop.get("temporal_status"), "start": hop.get("start"),
            "end_exclusive": hop.get("end_exclusive"), "as_of": hop.get("as_of"),
            "employer_label": hop.get("employer_label"), "firm_id": hop.get("firm_id"),
            "activity_id": hop.get("activity_id"), "confidence_label": hop.get("confidence_label"),
            "confidence_scope": hop.get("confidence_scope"), "review_only": hop.get("review_only", True),
            "user_asserted": hop.get("user_asserted", False), "evidence_person_id": source,
            "provenance_available_on_selection": True}

    def bounded(result):
        nonlocal search_limited, time_limited
        if result.get("search_limited"):
            search_limited = True
            # Within the effort bound only the clock can have cut the search short.
            time_limited |= int(result.get("neighbor_checks") or 0) <= NEIGHBOR_CHECKS
        return result

    options = {"limit": min(100, limit), "max_neighbor_checks": NEIGHBOR_CHECKS, "timeout_ms": SEED_TIMEOUT_MS,
               "include_policy": False, "include_weak": False, "include_user_asserted": True}
    # Every seed gets the same fixed budget, so a slow neighbourhood never starves the seeds after it.
    for person in seeds[:SEED_LIMIT]:
        queried += 1
        # Reported activities are independent of employment; do not call them coworkers.
        for hop in bounded(router.reported_collaborators(person, options)).get("collaborators", []):
            add_hop(hop)
        for hop in bounded(router.coworkers(person, options)).get("coworkers", []):
            add_hop(hop)

    # Preserve exact sampled person-person evidence already available, without
    # treating absent sampled ties as absent relationships.
    for person in seeds[:SEED_LIMIT]:
        for index in static.adjacency.get(person, [])[:1000]:
            edge = static.edges[index]
            source, target = canonical(edge["source"]), canonical(edge["target"])
            if edge.get("layer") != "co_tenure" or source not in selected or target not in selected or source == target:
                continue
            key = (*sorted((source, target)), "co_tenure")
            edges[key] = {"id": edge["id"], "source": source, "target": target, "layer": "co_tenure",
                "relationship": "co_tenure", "weight": edge.get("weight", 0), "status": edge.get("status"),
                "evidence_class": "exact_verified_graph", "evidence_person_id": source,
                "provenance_available_on_selection": True, "canonical_eligible": False}
    result_nodes = [{**{key: node[key] for key in PERSON_FIELDS if key in node}, "id": node_id,
                     "kind": "person", "is_search_match": node_id in matches} for node_id, node in selected.items()]
    return {"nodes": result_nodes, "edges": list(edges.values())[:limit * 8],
        "layers": ["co_tenure", "overlapped_tenure", "current_coworker", "co_reported_lobbying_activity"],
        "search": {"query": query, "matched_people": len(matches)},
        "bounds": {"node_limit": limit, "seed_limit": SEED_LIMIT, "queried_people": queried,
            "neighbor_check_limit": NEIGHBOR_CHECKS, "seed_timeout_ms": SEED_TIMEOUT_MS,
            "search_limited": search_limited, "truncated": search_limited, "time_limited": time_limited,
            "neighborhoods_complete": not search_limited, "people_only": True, "complete_network": False,
            "selection_method": "bounded exact-person search and one-hop dated/source-activity neighborhoods",
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2)},
        "notice": "Only people are displayed. Institutions and reporting activities remain relationship evidence; no layout hub is a person."}
