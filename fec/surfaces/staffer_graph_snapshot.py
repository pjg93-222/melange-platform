"""Read-only, receipt-gated display adapter for the selected exploratory graph.

The bounded co-tenure sample is not a recomputation of the model. The other
layers use the builder's exact-key joins and unchanged declared weights.
No source file is written, no name match becomes an identity assertion.
"""
from __future__ import annotations

import gzip
import hashlib
import heapq
import itertools
import json
import re
import sys
from collections import Counter, defaultdict, OrderedDict
from pathlib import Path
import threading


GENERATION = "staffer_social_network_20260908_v8_math_primary"
VERIFICATION = "staffer_social_network_20260908_v8_math_verification"
SAMPLE_ROWS = 10000


def selected_graph_receipt_path(audits):
    """Consumers without a supplied receipt follow the same explicit graph pin."""
    return Path(audits) / GENERATION / 'staffer_social_network_receipt.json'


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class StaticGraph:
    def __init__(self, source_root):
        self.root = Path(source_root)
        if self.root.name != "staffer_network":
            self.root = self.root / "audits" / "staffer_network"
        self.nodes = {}
        self.edges = []
        self.sources = []
        self.errors = []
        self.receipt = {}
        self.verification = {}
        self._loaded = False
        self._lock = threading.RLock()
        self.adjacency = defaultdict(list)
        self._view_cache = OrderedDict()
        self._prefix_nodes = defaultdict(set)
        self._search_text = {}
        self._by_member = defaultdict(set)
        # Built by reindex(): every node's words, its place in the (unresolved, label, id) order, the
        # exact-label index for tiny labels and the lazily sorted 1-2 character prefix lists.
        self._search_words = {}
        self._rank = {}
        self._first_unresolved = 0
        self._tiny_labels = {}
        self._short_sorted = {}
        # Identifiers are findable only whole: member ids by their casefolded spelling, ids that contain capitals likewise.
        self._member_keys = {}
        self._folded_ids = {}
        self.load_seconds = None

    def _verified(self, path, spec, role):
        path = Path(path)
        actual = digest(path)
        if actual != spec["sha256"]:
            raise ValueError("SHA256 mismatch: " + str(path))
        self.sources.append({"role": role, "path": str(path), "sha256": actual,
                             "verified": True, "bytes": path.stat().st_size})
        return path

    def _edge(self, source, target, layer, weight, provenance, **extra):
        if source not in self.nodes or target not in self.nodes:
            return
        identity = "|".join((layer, source, target, str(extra.get("status", ""))))
        edge = {"id": extra.pop("edge_id", None) or "display:" + hashlib.sha256(identity.encode()).hexdigest(),
                "source": source, "target": target, "layer": layer, "weight": weight,
                "provenance": provenance, "review_status": "REVIEW_REQUIRED",
                "evidence_class": "exact_verified_graph" if provenance.get("method") == "exact emitted edge" else "derived_from_verified_inputs",
                "canonical_eligible": False, "canonical_employment_promoted": False, **extra}
        self.edges.append(edge)

    def load(self):
        import time
        started = time.perf_counter()
        with self._lock:
            if self._loaded:
                return self
            self._loaded = True
            try:
                self._load()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # Fail closed: a partially verified graph must never be displayed.
                self.errors.append(str(exc))
                self.nodes.clear()
                self.edges.clear()
            for index, edge in enumerate(self.edges):
                self.adjacency[edge["source"]].append(index)
                self.adjacency[edge["target"]].append(index)
            self.reindex()
            self.load_seconds = round(time.perf_counter() - started, 3)
            return self

    def reindex(self):
        """Name-prefix candidate index: refine a small bucket, never scan 150k rows.

        Only what a person reads is indexed: the label and the aliases. A machine identifier, such as the bioguide
        ids in member_ids, is not a word of the index, so no fragment of one matches; the whole identifier is found
        through _member_keys and _folded_ids instead (see search_ids).

        Every structure is built on its own and assigned at the end, so a reader never sees a half-built
        index and a shallow copy of this graph keeps sharing the old, untouched one until it is reindexed.
        """
        prefix_nodes, search_text, by_member = defaultdict(set), {}, defaultdict(set)
        search_words, tiny_labels = {}, defaultdict(list)
        member_keys, folded_ids = defaultdict(set), defaultdict(list)
        for node_id, node in self.nodes.items():
            label = str(node.get("label") or "").strip()
            raw_id = not label or label == node_id or label.startswith(("person:", "organization:", "container:", "HON:"))
            if raw_id:
                noun = {"person": "person", "organization": "lobbying organization", "container": "office", "member": "Member of Congress"}.get(node.get("kind"), "record")
                node["label"] = f"Name unavailable — {noun}"
                node["name_status"] = "UNRESOLVED_SOURCE_NAME"
            else:
                node.setdefault("name_status", "SOURCE_NAME")
            text = " ".join([node["label"], *node.get("aliases", [])]).casefold()
            search_text[node_id] = text
            words = tuple(re.findall(r"[\w]+", text))
            search_words[node_id] = tuple(map(sys.intern, words))  # names repeat across 200k nodes: share the strings
            for token in set(words):
                for length in (1, 2, 3):
                    if len(token) >= length:
                        prefix_nodes[token[:length]].add(node_id)
            for bio in node.get("member_ids", []):
                if bio not in by_member:
                    member_keys[str(bio).casefold()].add(bio)
                by_member[bio].add(node_id)
            folded = node["label"].casefold()
            if len(folded) <= 2:
                tiny_labels[folded].append(node_id)
            lowered = node_id.casefold()
            if lowered != node_id:
                folded_ids[lowered].append(node_id)
        # The order search_ids ranks matches in, apart from the exact-label-first term that depends on the query.
        nodes = self.nodes
        order = sorted(nodes, key=lambda node_id: (nodes[node_id].get("name_status") == "UNRESOLVED_SOURCE_NAME", nodes[node_id]["label"], node_id))
        rank = {node_id: place for place, node_id in enumerate(order)}
        self._first_unresolved = sum(nodes[node_id].get("name_status") != "UNRESOLVED_SOURCE_NAME" for node_id in order)
        self._rank, self._search_words, self._tiny_labels, self._short_sorted = rank, search_words, dict(tiny_labels), {}
        self._member_keys, self._folded_ids = dict(member_keys), dict(folded_ids)
        self._prefix_nodes, self._search_text, self._by_member = prefix_nodes, search_text, by_member
        self._view_cache.clear()

    def _sorted_bucket(self, token):
        """The ids of every node with a word starting with a 1-2 character token, in match order; built once per token."""
        cache, buckets, rank = self._short_sorted, self._prefix_nodes, self._rank  # one generation of the index, even across a reindex
        found = cache.get(token)
        if found is None:
            found = sorted(buckets.get(token, ()), key=rank.__getitem__)
            cache[token] = found  # concurrent builders produce equal lists
        return found

    def search_ids(self, query, limit=None):
        """Node ids matching every query token as a word prefix of a label or alias, best first; at most `limit` of them.

        Order: resolved names before unresolved ones, then a label equal to the whole query, then label, then id.
        The answer for a given graph is the one a full sort of all matches gives; with a limit it is built from
        the pre-ranked candidates without sorting them all.

        Identifiers are not names. A query that is a node id in full (trimmed, any case) finds that node alone, and a
        query that is one member's whole bioguide id finds the nodes attached to that member; a fragment of either
        finds nothing by way of the identifier.
        """
        original = str(query).strip()
        if original in self.nodes:
            return [original]
        query = original.casefold()
        if not query:
            return []
        if query in self.nodes:
            return [query]
        if query in self._folded_ids:
            return list(self._folded_ids[query])  # the same id in other capitals
        tokens = re.findall(r"[\w]+", query)
        if not tokens:
            return []
        if limit is not None and limit < 0:
            return self.search_ids(original)[:limit]
        want = limit
        if want == 0:
            return []
        buckets = sorted((self._prefix_nodes.get(token[:3], ()) for token in tokens), key=len)
        short = len(tokens) == 1 and len(tokens[0]) <= 2
        rank, first_unresolved = self._rank, self._first_unresolved
        if short:
            # One token of at most two characters: its bucket is exactly the set of matches, already sortable.
            bucket = buckets[0]
            presorted = self._sorted_bucket(tokens[0])
            tiny = self._tiny_labels.get(query, ()) if len(query) <= 2 else None
            if tiny is None:
                exact = [node_id for node_id in bucket if self.nodes[node_id]["label"].casefold() == query]
            else:
                exact = [node_id for node_id in tiny if node_id in bucket]
            exact_set = set(exact)
            rest = (node_id for node_id in presorted if node_id not in exact_set)
            rest = list(rest if want is None else itertools.islice(rest, want))
        else:
            # The smallest bucket first; a copy of it is intersected with the others, the buckets themselves are never changed.
            candidates = set(buckets[0]).intersection(*buckets[1:]) if len(buckets) > 1 else buckets[0]
            if len(tokens) > 1 or len(tokens[0]) > 3:
                words_of = self._search_words
                candidates = [node_id for node_id in candidates
                              if all(any(word.startswith(token) for word in words_of[node_id]) for token in tokens)]
            if len(tokens) == 1 and tokens[0] == query and query in self._member_keys:
                # The whole query is one member's bioguide id: everyone attached to that member, found by the whole id only.
                candidates = {*candidates, *(node_id for bio in self._member_keys[query] for node_id in self._by_member.get(bio, ()))}
            exact = [node_id for node_id in candidates if self.nodes[node_id]["label"].casefold() == query]
            exact_set = set(exact)
            rest = (node_id for node_id in candidates if node_id not in exact_set)
            rest = sorted(rest, key=rank.__getitem__) if want is None else heapq.nsmallest(want, rest, key=rank.__getitem__)
        exact.sort(key=rank.__getitem__)
        ordered = ([i for i in exact if rank[i] < first_unresolved] + [i for i in rest if rank[i] < first_unresolved]
                   + [i for i in exact if rank[i] >= first_unresolved] + [i for i in rest if rank[i] >= first_unresolved])
        return ordered if want is None else ordered[:want]

    def _load(self):
        base = self.root / GENERATION
        verification_path = self.root / VERIFICATION / "staffer_social_network_verification_receipt.json"
        self.verification = json.loads(verification_path.read_text(encoding="utf-8"))
        if self.verification.get("status") != "PASS" or self.verification.get("failures"):
            raise ValueError("The pinned graph verification has not passed")
        receipt_path = base / "staffer_social_network_receipt.json"
        receipt_check = next(x for x in self.verification["twin_byte_identity"] if x["path"] == receipt_path.name)
        self._verified(receipt_path, {"sha256": receipt_check["primary_sha256"]}, "graph_receipt")
        self.receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if self.receipt.get("canonical_employment_promotions") or self.receipt.get("canonical_identity_promotions"):
            raise ValueError("Promotion tripwire failed")
        files = {x["role"]: x for x in self.receipt["files"]}
        inputs = {x["role"]: x for x in self.receipt["inputs"]}

        def artifact(role):
            spec = files[role]
            return self._verified(base / spec["path"], spec, role)

        def upstream(role):
            spec = inputs[role]
            # Keep the pinned generation while permitting an equivalent source root.
            original = Path(spec["path"])
            candidate = self.root / original.parent.name / original.name
            return self._verified(candidate, spec, role)

        def display_label(row, node_id):
            # "name" is the v6 groups artifact's spelling of the same string the
            # node-score artifact carries as issue_area / organization_name /
            # office_key. A v5 row has no "name" key, so appending it last leaves
            # every v5 label byte-identical.
            return (row.get("issue_area") or row.get("organization_name")
                    or row.get("office_key") or row.get("name") or node_id)

        def add_node(row, node_id, kind, path):
            score_rows[node_id] = row
            self.nodes[node_id] = {**row, "id": node_id, "label": display_label(row, node_id), "kind": kind,
                "member_ids": row.get("attached_member_bioguides") or ([row["member_bioguide"]] if row.get("member_bioguide") else []),
                "committee_ids": [], "policy_ids": [], "provenance": {"path": str(path), "node_id": node_id}}

        score_path = artifact("staffer_social_node_scores")
        score_rows = {}
        for row in rows(score_path):
            add_node(row, row["node_id"], row["node_kind"], score_path)
        # v6 (generation ruling D-022) split the node universe across two files:
        # the node-score artifact became persons only, and the container,
        # organization and policy_area rows this reader has always needed moved
        # into a separately hashed roster declared under receipt role "groups".
        # They are the same display records under new key names, so they are read
        # through the same artifact() hash gate and merged into the same two
        # lookups -- self.nodes and score_rows -- that the membership join below,
        # the policy_area label index, the revolving endpoints and the member
        # office anchors all resolve against. A v5 generation declares no
        # "groups" role and still carries those rows in its node-score artifact;
        # nothing on that path changes.
        if "groups" in files:
            groups_path = artifact("groups")
            for row in rows(groups_path):
                node_id, kind = row["group_id"], row["group_kind"]
                add_node({**row, "node_id": node_id, "node_kind": kind}, node_id, kind, groups_path)
        spine_path = upstream("hill_person_spine")
        for row in rows(spine_path):
            node = self.nodes.get("person:" + row["spine_person_id"])
            if node:
                name = row.get("payee_key") or {}
                node["label"] = " ".join(str(name[k]).title() for k in ("first", "middle_initial", "last") if name.get(k)) or node["label"]
                node["provenance"]["identity_path"] = str(spine_path)
                node["spine_person_id"] = row["spine_person_id"]
                node["lda_source_person_id"] = row.get("lda_source_person_id")
                node["quorum_staff_id"] = row.get("quorum_staff_id")
        self.pending_source_assertions = defaultdict(list)
        if 'staffer_social_politico_review_layers' in files:
            review_path = artifact('staffer_social_politico_review_layers')
            for assertion in rows(review_path):
                target = assertion.get('subject_id')
                value = {**assertion, 'artifact_path': str(review_path), 'operator_included': False}
                if target in self.nodes:
                    self.nodes[target].setdefault('review_assertions', []).append(value)
                elif assertion.get('source_profile_uuid'):
                    self.pending_source_assertions['politico:' + assertion['source_profile_uuid']].append(value)
        trajectory_path = artifact("staffer_trajectories")
        strengths = self.receipt["dials"]["s_strength"]
        for row in rows(trajectory_path):
            person = "person:" + row["spine_person_id"]
            if person not in self.nodes:
                continue
            self.nodes[person]["stints"] = row["stints"]
            self.nodes[person]["moves"] = row["moves"]
            for container in sorted({s["container_id"] for s in row["stints"]}):
                info = score_rows[container]
                if info.get("container_kind") == "committee":
                    self.nodes[person]["committee_ids"].append(container)
                self._edge(person, container, "membership", info["kappa"] * strengths[row["identity_tier"]],
                    {"path": str(trajectory_path), "spine_person_id": row["spine_person_id"], "method": "verified person-container membership; declared kappa times tier strength"}, status="GUARANTEED")
        # v6 emits the person-to-policy_area memberships as its own hash-declared
        # grouping layer, already keyed in this generation's person id space. The
        # upstream Politico file the join below reads is keyed to the PREVIOUS
        # spine generation -- the v6 network is built on the v7 spine through
        # spine_id_map_v6_to_v7 -- so on v6 that join matches zero of its 11,525
        # rows and the derived-count gate at the end of this method would fail
        # closed. Read the generation's own layer when it declares one; keep the
        # upstream join, unchanged, for a generation that does not.
        policy_pairs, policy_status = defaultdict(list), {}
        if "policy_layer" in files:
            policy_path = artifact("policy_layer")
            policy_method = "generation-emitted person-to-policy_area grouping row"
            for row in rows(policy_path):
                person, target = row["subject_id"], row["object_id"]
                if person in self.nodes and target in self.nodes:
                    policy_pairs[person, target].extend(row.get("source_profile_uuids") or [row.get("source_profile_uuid")])
                    policy_status[person, target] = row.get("status") or "CURRENT_SNAPSHOT_UNDATED"
        else:
            policy_path = upstream("politico_direct_policy_memberships")
            policy_method = "exact verified spine_person_id"
            policies = {" ".join(n["issue_area"].casefold().split()): n["id"] for n in self.nodes.values() if n["kind"] == "policy_area"}
            for row in rows(policy_path):
                target = policies.get(" ".join(str(row.get("issue_area", "")).casefold().split()))
                person = "person:" + str(row.get("spine_person_id", ""))
                if target and person in self.nodes:
                    policy_pairs[person, target].append(row.get("source_profile_uuid"))
                    policy_status[person, target] = "CURRENT_SNAPSHOT_UNDATED"
        for (person, target), profiles in policy_pairs.items():
            self.nodes[person]["policy_ids"].append(target)
            self._edge(person, target, "policy_area", 1.0, {"path": str(policy_path), "source_profile_uuids": sorted(set(filter(None, profiles))), "method": policy_method}, duration_weight=0.0, status=policy_status[person, target])
        by_bio = defaultdict(list)
        for node in self.nodes.values():
            if node.get("container_kind") == "member_office" and node.get("member_bioguide"):
                by_bio[node["member_bioguide"]].append(node["id"])
        for bio, ids in by_bio.items():
            by_bio[bio] = sorted([cid for cid in ids if "INTERN COMPENSATION" not in cid.upper()] or ids)
        # Same split as the policy layer. v6 emits the office-to-organization
        # revolving edges itself, resolved to this generation's container ids and
        # carrying its own declared operator_weight -- which v6 sets to zero for
        # every grouping layer (receipt: operator_layers == ["co_tenure"],
        # revolving_in_operator == 0). Re-deriving the weight from the
        # exploratory upstream, as the v5 path must, would put 4,828 revolving
        # edges into the operator that this generation deliberately keeps out of
        # it. Only the container_organization rows become "revolving" edges: the
        # layer's other record kind (person_registrant) is an LDA identity bridge
        # that the receipt counts separately and that D-021 explicitly disclaims
        # as an employment claim, so it stays out of the displayed graph.
        if "revolving_layer" in files:
            revolving_path = artifact("revolving_layer")
            for row in rows(revolving_path):
                if row.get("layer") != "revolving" or row.get("record_kind") != "container_organization":
                    continue
                self._edge(row["subject_id"], row["object_id"], "revolving", float(row.get("operator_weight") or 0.0),
                    {"path": str(revolving_path), "source_edge_ids": row.get("source_edge_ids") or [],
                     "method": "generation-emitted office-to-organization row at its own declared operator weight"},
                    status=row.get("status", "UNKNOWN"), duration_weight=float(row.get("duration_weight") or 0.0))
        else:
            revolving_path = upstream("exploratory_revolving_edges")
            revolving = {}
            for row in rows(revolving_path):
                if row.get("layer") != "revolving":
                    continue
                source = str(row.get("subject_id", ""))
                targets = by_bio.get(source.removeprefix("container:bioguide:"), []) if source.startswith("container:bioguide:") else ([source] if source in self.nodes else [])
                for source_id in targets:
                    key = source_id, row["object_id"], row.get("status", "UNKNOWN")
                    record = revolving.setdefault(key, {"weight": 0.0, "source_edge_ids": []})
                    record["weight"] += max(0.0, float(row.get("w_tiered_lower") or 0.0)) / len(targets)
                    record["source_edge_ids"].append(row.get("edge_id"))
            for (source, target, status), record in revolving.items():
                self._edge(source, target, "revolving", record["weight"] if status == "GUARANTEED" else 0.0,
                    {"path": str(revolving_path), "source_edge_ids": record["source_edge_ids"], "method": "exact office/bioguide; split and sum source weights"}, status=status, duration_weight=0.0)
        edge_path = artifact("staffer_social_edges")
        co_tenure_kept, co_tenure_dropped_non_guaranteed = 0, 0
        for row in itertools.islice(rows(edge_path), SAMPLE_ROWS):
            # A co_tenure row's own status can be POSSIBLE_ONLY: the generation
            # emits it as a candidate tie, not a guaranteed one, and this reader
            # used to load it into the sidecar graph at full weight regardless
            # (fix, 2026-09-07): a co_tenure row is admitted only when GUARANTEED,
            # the same bar the revolving layer above already holds status to. Any
            # other layer that might one day flow through this same file is left
            # unfiltered; this is a co_tenure-specific leak, not a general one.
            if row["layer"] == "co_tenure" and row.get("status") != "GUARANTEED":
                co_tenure_dropped_non_guaranteed += 1
                continue
            if row["layer"] == "co_tenure":
                co_tenure_kept += 1
            self._edge(row["subject_id"], row["object_id"], row["layer"], row["w_tiered_lower"],
                {"path": str(edge_path), "edge_id": row["edge_id"], "method": "exact emitted edge"},
                **{k: v for k, v in row.items() if k not in {"subject_id", "object_id", "layer", "provenance"}})
        # Recorded on this artifact's own provenance entry in self.sources, the
        # same place every other verified input's binding lives, so a consumer of
        # the snapshot's provenance can see how many of the sampled co_tenure rows
        # were dropped without re-deriving it from the edge count.
        edges_source = next(s for s in self.sources if s["role"] == "staffer_social_edges")
        edges_source["co_tenure_status_filter"] = {
            "status_required": "GUARANTEED", "kept": co_tenure_kept,
            "dropped_non_guaranteed": co_tenure_dropped_non_guaranteed}
        self.layer_counts = dict(Counter(e["layer"] for e in self.edges))
        expected = self.receipt["edge_counts"]
        for layer, count_key in (("membership", "membership"), ("policy_area", "policy_area_membership"), ("revolving", "revolving")):
            if self.layer_counts.get(layer, 0) != expected[count_key]:
                raise ValueError("Derived layer count differs from verified graph: " + layer)
        if sum(e["layer"] == "revolving" and e["weight"] > 0 for e in self.edges) != expected["revolving_in_operator"]:
            raise ValueError("Derived revolving positive-weight count differs from verified graph")
        self.source_node_count = len(self.nodes)
        office_label_path = upstream("hill_member_office_index")
        member_labels = {}
        for row in rows(office_label_path):
            bio = row.get("member_bioguide")
            examples = row.get("office_raw_examples") or []
            if bio and row.get("container_kind") == "member_office" and examples:
                raw = str(examples[0])
                label = re.sub(r"^(?:FISCAL YEAR\s+)?\d{4}\s+", "", raw, flags=re.I)
                label = re.sub(r"^(?:HON\.?|SENATOR)\s+", "", label, flags=re.I)
                member_labels.setdefault(bio, (label.title(), raw))
        # Layout anchors are explicit verified-ID attachments, never model ties.
        for bio, containers in by_bio.items():
            anchor = "member:" + bio
            labels = [self.nodes[c]["label"] for c in containers]
            label = next((label for label in labels if not label.startswith("HON:")), bio)
            label, raw_label = member_labels.get(bio, (label, None))
            self.nodes[anchor] = {"id": anchor, "label": label, "kind": "member", "member_id": bio,
                "member_ids": [bio], "committee_ids": [], "policy_ids": [], "layout_only": True,
                "review_status": "REVIEW_REQUIRED", "canonical_eligible": False,
                "provenance": {"path": str(score_path), "member_bioguide": bio, "container_ids": containers},
                "label_provenance": {"path": str(office_label_path) if raw_label else str(score_path),
                    "member_bioguide": bio, "raw_label": raw_label, "display_only": True,
                    "method": "exact bioguide; display label only; no identity or model change"}}
        for person in list(self.nodes.values()):
            if person["kind"] != "person":
                continue
            for bio in person["member_ids"]:
                self._edge(person["id"], "member:" + bio, "member_anchor", 0.0,
                    {"path": str(score_path), "node_id": person["id"], "member_bioguide": bio,
                     "method": "verified attached_member_bioguides; layout attachment only"},
                    layout_only=True, operator_included=False, duration_weight=0.0)
        self.layer_counts = dict(Counter(e["layer"] for e in self.edges))

    def view(self, query="", limit=400):
        self.load()
        limit = max(1, min(int(limit), 1200))
        query = str(query).strip()
        key = (query, limit)
        if key in self._view_cache:
            self._view_cache.move_to_end(key)
            return self._view_cache[key]
        direct = self.search_ids(query)
        matching_members = [node_id for node_id in direct if self.nodes[node_id]["kind"] == "member"]
        matching_bios = {self.nodes[node_id]["member_id"] for node_id in matching_members}
        associated = sorted(set().union(*(self._by_member[bio] for bio in matching_bios))) if matching_bios else []
        matched = list(dict.fromkeys(matching_members + direct + associated))
        if query:
            selected = dict.fromkeys(matched[:limit])
        else:
            # Seed each layer and the two verified regressions before expansion.
            selected = {}
            for regression in self.verification.get("named_regression_checks", {}).values():
                for person in regression.get("spine_person_ids", []):
                    selected["person:" + person] = None
            for layer in ("co_tenure", "membership", "policy_area", "revolving"):
                for edge in itertools.islice((e for e in self.edges if e["layer"] == layer), 12):
                    selected[edge["source"]] = None
                    selected[edge["target"]] = None
            selected = dict.fromkeys(list(selected)[:limit])
        for node_id in list(selected):
            for index in self.adjacency.get(node_id, []):
                edge = self.edges[index]
                other = edge["target"] if edge["source"] == node_id else edge["source"]
                if len(selected) < limit:
                    selected[other] = None
        edge_indices = {i for node in selected for i in self.adjacency.get(node, [])}
        visible_edges = [self.edges[i] for i in sorted(edge_indices) if self.edges[i]["source"] in selected and self.edges[i]["target"] in selected]
        edge_limit = limit * 8
        matched_set = set(matched)
        result = {"nodes": [{**self.nodes[k], "is_search_match": k in matched_set} for k in selected if k in self.nodes], "edges": visible_edges[:edge_limit],
            "search": {"query": query, "matched_member_ids": sorted(matching_bios), "match_method": "label tokens or exact verified member affiliation; other visible nodes are neighbors"},
            "sources": self.sources, "errors": self.errors, "layers": ["co_tenure", "membership", "policy_area", "revolving", "member_anchor"],
            "totals": {"nodes": getattr(self, "source_node_count", 0), "display_nodes": len(self.nodes), "edges": self.receipt.get("edge_counts", {}).get("total_rows_emitted", 0), "loaded_edges": len(self.edges), "loaded_layers": getattr(self, "layer_counts", {}), "source_edge_counts": self.receipt.get("edge_counts", {})},
            "bounds": {"node_limit": limit, "edge_limit": edge_limit, "matching_nodes": len(matched), "co_tenure_sample_rows": SAMPLE_ROWS,
                "derived_layers": ["membership", "policy_area", "revolving"], "derived_layer_count_parity": not bool(self.errors),
                "co_tenure_complete": False, "sample_method": "first 10000 emitted edges; not representative; absent ties are not evidence of no relationship", "view_truncated": len(visible_edges) > edge_limit or len(matched) > limit},
            "verification_status": self.verification.get("status"), "named_regression_checks": self.verification.get("named_regression_checks", {}),
            "review_status": "REVIEW_REQUIRED", "canonical_eligible": False, "canonical_employment_promotions": 0,
            "weights_changed": False, "generation": GENERATION}
        self._view_cache[key] = result
        while len(self._view_cache) > 32:
            self._view_cache.popitem(last=False)
        return result
