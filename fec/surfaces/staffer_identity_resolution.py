"""Systematic identity review across source records, separate from confirmed aliases.

Shared names, firms and covered-position text are review evidence, not unique
identity keys; candidates here never merge. Aliases are applied from explicit
user-confirmed groups and from the evidence-backed automatic groups produced by
staffer_identity_auto.resolve_identities, whose applied count is reported here.
"""
import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
import unicodedata
from collections import defaultdict
from pathlib import Path

USER_ASSERTIONS = Path(__file__).with_name("staffer_user_assertions.json")
_DECISION_LOCK = threading.RLock()
_LDA_ROWS_CACHE = {}


def record_identity_decision(primary_id, alias_ids, decision="merge", display_name=None):
    """Persist an explicit review click atomically; callers validate group membership."""
    ids = [primary_id, *alias_ids] if isinstance(alias_ids, list) else []
    if (decision not in {"merge", "keep_separate"} or len(ids) > 100
            or any(not isinstance(i, str) or not re.fullmatch(r"(?:person:|politico:)[A-Za-z0-9:_-]{1,200}", i) for i in ids)
            or len(set(ids)) < 2):
        raise ValueError("Invalid identity decision or source person IDs")
    with _DECISION_LOCK:
        data = json.loads(USER_ASSERTIONS.read_text(encoding="utf-8"))
        groups = data.setdefault("identity_groups", [])
        mappings = {a: g["primary_id"] for g in groups for a in g.get("alias_ids", [])}
        canonical = mappings.get(primary_id, primary_id)
        members = set(ids)
        changed = True
        while changed:
            changed = False
            for group in groups:
                group_ids = {group["primary_id"], *group.get("alias_ids", [])}
                if members & group_ids and not group_ids <= members:
                    members.update(group_ids)
                    changed = True
        denied = {tuple(sorted(p)) for p in data.get("keep_separate_pairs", [])}
        requested_pairs = {tuple(sorted((a, b))) for a in set(ids) for b in set(ids) if a < b}
        if decision == "merge":
            if any(set(pair) <= members for pair in denied):
                raise ValueError("Merge contradicts a persisted keep-separate decision")
            retained = [g for g in groups if not ({g["primary_id"], *g.get("alias_ids", [])} & members)]
            previous = next((g for g in groups if g["primary_id"] == canonical), {})
            group_id = "identity-group:" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
            group = {**previous, "group_id": group_id, "primary_id": canonical,
                "alias_ids": sorted(members - {canonical}),
                "source_person_ids": sorted({i.removeprefix("person:") for i in members}),
                "display_name": display_name or previous.get("display_name") or "User-confirmed person",
                "status": "USER_CONFIRMED_SAME_PERSON", "asserted_at": datetime.now(timezone.utc).isoformat(),
                "provenance": {"source": "user_review_confirmation", "decision": "merge", "previous_provenance": previous.get("provenance")},
                "canonical_identity_promoted": False}
            data["identity_groups"] = retained + [group]
            response = {"decision": decision, "group_id": group_id, "group": group,
                "identity_aliases": {a: canonical for a in group["alias_ids"]}}
        else:
            for a, b in requested_pairs:
                if mappings.get(a, a) == mappings.get(b, b):
                    raise ValueError("Keep-separate decision contradicts an existing confirmed identity group")
            requested_pairs = {tuple(sorted((mappings.get(a, a), mappings.get(b, b)))) for a, b in requested_pairs}
            denied.update(requested_pairs)
            data["keep_separate_pairs"] = [list(p) for p in sorted(denied)]
            response = {"decision": decision, "group_id": "separate:" + hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()[:24],
                "keep_separate_pairs": [list(p) for p in sorted(requested_pairs)]}
        data.setdefault("review_decisions", []).append({**response, "recorded_at": datetime.now(timezone.utc).isoformat()})
        fd, temporary = tempfile.mkstemp(prefix=".staffer-identity-", suffix=".json", dir=USER_ASSERTIONS.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, USER_ASSERTIONS)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return response

try:
    from .staffer_routing_evidence import LDA_ROOT, read_rows, sha
    from .staffer_identity_auto import compatible, name_parts
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_routing_evidence import LDA_ROOT, read_rows, sha
    from fec.surfaces.staffer_identity_auto import compatible, name_parts


def name_key(name):
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().casefold()
    tokens = re.findall(r"[a-z]+", text)
    while tokens and tokens[0] in {"mr", "mrs", "ms", "miss", "dr"}:
        tokens.pop(0)
    # Exact-name key; retrieval groups by first+last family and compatible() decides pairs.
    if tokens and tokens[0] == "greg":
        tokens[0] = "gregory"
    return " ".join(tokens)


def detect_candidates(records, confirmed_aliases=None, episodes=(), keep_separate_pairs=()):
    confirmed_aliases = confirmed_aliases or {}

    def resolve(ident):
        seen = set()
        while ident in confirmed_aliases:
            if ident in seen:
                raise ValueError("Cycle in confirmed identity aliases")
            seen.add(ident)
            ident = confirmed_aliases[ident]
        return ident

    firms = defaultdict(set)
    for episode in episodes:
        firm = episode.get("firm_id") or episode.get("employer_id")
        if firm:
            firms[resolve(episode["person_id"])].add(firm)
    groups = defaultdict(list)
    for record in records:
        key = name_key(record.get("label", ""))
        parts = name_parts(record)
        if parts and len(key.split()) >= 2 and not key.startswith("name unavailable"):
            groups[" ".join(parts[:2])].append(record)
    candidates = []
    denied = set()
    for a, b in keep_separate_pairs:
        a, b = resolve(a), resolve(b)
        if a != b:
            denied.add(tuple(sorted((a, b))))
    for key, matches in sorted(groups.items()):
        by_id = defaultdict(list)
        for record in matches:
            by_id[resolve(record["id"])].append(record)
        # Family retrieval only widens what is reviewed; compatible() (middle initial,
        # suffix, date of birth) decides the pairs and nothing here ever merges.
        distinct = sorted(by_id)
        pairs = [(a, b) for index, a in enumerate(distinct) for b in distinct[index + 1:]
                 if any(compatible(x, y) for x in by_id[a] for y in by_id[b])]
        ids = sorted({i for pair in pairs for i in pair})
        if len(ids) < 2:
            continue
        matches = [m for m in matches if resolve(m["id"]) in ids]
        candidate_pairs = [[a, b] for a, b in pairs if (a, b) not in denied]
        if not candidate_pairs:
            continue
        histories = defaultdict(set)
        for record in matches:
            for history in record.get("covered_positions", []):
                normalized = " ".join(re.findall(r"[a-z0-9]+", str(history).casefold()))
                if len(normalized) >= 35:
                    histories[normalized].add(resolve(record["id"]))
        shared_histories = [{"text": h, "node_ids": sorted(p)} for h, p in histories.items() if len(p) > 1]
        shared_firms = defaultdict(set)
        for ident in ids:
            for firm in firms[ident]:
                shared_firms[firm].add(ident)
        firm_support = [{"firm_id": f, "node_ids": sorted(p)} for f, p in shared_firms.items() if len(p) > 1]
        evidence = ["NORMALIZED_NAME_CANDIDATE_ONLY"]
        if shared_histories:
            evidence.append("EXACT_COVERED_POSITION_TEXT")
        if firm_support:
            evidence.append("SHARED_FILING_REGISTRANT")
        candidates.append({"candidate_id": "identity-candidate:" + hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:24],
            "display_name": matches[0]["label"], "normalized_name": key, "node_ids": ids,
            "status": "CONFIRMATION_REQUIRED", "auto_merge": False,
            "candidate_pairs": candidate_pairs,
            "priority": "corroborated_candidate" if shared_histories or firm_support else "name_only_candidate",
            "evidence_types": evidence, "shared_covered_positions": shared_histories, "shared_firms": firm_support,
            "records": [{"node_id": m["id"], "source_person_ids": m.get("source_person_ids", []),
                "label": m["label"], "covered_positions": m.get("covered_positions", []),
                "provenance": m.get("provenance", {})} for m in matches],
            "limitation": "Names and professional histories can coincide; no unique cross-source identity anchor established."})
    return candidates


def load_identity_resolution(source_root, graph, episodes=()):
    root = Path(source_root)
    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", {})
    if isinstance(nodes, list):
        nodes = {n["id"]: n for n in nodes}
    records = {ident: {"id": ident, "label": n.get("label", ""),
        "source_person_ids": n.get("source_person_ids") or ([n["lda_source_person_id"]] if n.get("lda_source_person_id") else []),
        "provenance": n.get("provenance", {})} for ident, n in nodes.items() if n.get("kind") == "person"}
    result = {"identity_aliases": {}, "proposed_aliases": {}, "candidate_groups": [], "sources": [], "errors": []}
    try:
        assertions_path = USER_ASSERTIONS
        assertions = json.loads(assertions_path.read_text(encoding="utf-8"))
        for group in assertions.get("identity_groups", []):
            if group.get("status") == "USER_CONFIRMED_SAME_PERSON":
                result["identity_aliases"].update({a: group["primary_id"] for a in group["alias_ids"]})
        result["sources"].append({"path": str(assertions_path), "sha256": sha(assertions_path), "role": "confirmed_identity_overlay"})
        candidate = root / "data/staffer_network/lda-source-native/generation-20260830T231200Z"
        base = candidate if candidate.exists() else LDA_ROOT
        receipt_path = base / "lda_source_native_corpus_receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("status") != "PASS" or receipt.get("failures"):
            raise ValueError("Identity candidate source receipt not PASS")
        spec = next(s for s in receipt["files"] if s["role"] == "lobbyist_assertions")
        path = base / spec["path"]
        stat = path.stat()
        cache_key = (str(path), stat.st_size, stat.st_mtime_ns, spec["sha256"])
        if cache_key not in _LDA_ROWS_CACHE:
            if sha(path) != spec["sha256"]:
                raise ValueError("Identity candidate source hash mismatch")
            _LDA_ROWS_CACHE.clear()
            _LDA_ROWS_CACHE[cache_key] = list(read_rows(path))
        proof = {"path": str(path), "sha256": spec["sha256"], "receipt": str(receipt_path)}
        by_source = defaultdict(set)
        for ident, record in records.items():
            for source in record["source_person_ids"]:
                by_source[source].add(ident)
        for row in _LDA_ROWS_CACHE[cache_key]:
            source = row["source_person_id"]
            targets = by_source.get(source) or {"person:" + source}
            for ident in targets:
                record = records.setdefault(ident, {"id": ident, "label": row["display_name"], "source_person_ids": [source], "provenance": proof})
                record.setdefault("covered_positions", []).extend(row.get("covered_position_values", []))
                record.setdefault("covered_position_source_ids", []).append(source)
        result["sources"].append(proof)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result["errors"].append(str(exc))
    result["candidate_groups"] = detect_candidates(records.values(), result["identity_aliases"], episodes,
        assertions.get("keep_separate_pairs", []) if "assertions" in locals() else [])
    result["records"] = records
    # Automatic groups already applied to this graph by the evidence resolver (staffer_identity_auto).
    automatic = [n for n in nodes.values() if n.get("kind") == "person"
                 and n.get("identity_status") not in (None, "", "USER_CONFIRMED_SAME_PERSON")]
    result["counts"] = {"records_checked": len(records), "candidate_groups": len(result["candidate_groups"]),
        "candidate_identity_nodes": len({i for g in result["candidate_groups"] for i in g["node_ids"]}),
        "corroborated_candidate_groups": sum(g["priority"] == "corroborated_candidate" for g in result["candidate_groups"]),
        "automatic_inferred_merges": len(automatic),
        "automatic_inferred_aliases": sum(len(n.get("identity_alias_ids") or []) for n in automatic),
        "user_confirmed_aliases": len(result["identity_aliases"])}
    return result
