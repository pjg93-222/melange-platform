"""Complete source-person inventory; exact supported IDs only, never name merges."""
import json
from pathlib import Path

try:
    from .staffer_routing_evidence import LDA_ROOT, read_rows, sha
    from .staffer_graph_snapshot import selected_graph_receipt_path
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_routing_evidence import LDA_ROOT, read_rows, sha
    from fec.surfaces.staffer_graph_snapshot import selected_graph_receipt_path


PAYROLL_EXCEPTIONS = Path(__file__).with_name("staffer_payroll_inventory_exceptions.json")
PAYROLL_EXCEPTIONS_SHA256 = "d391a085ca9aed052548c11c4d42db8ca50e9222f1b94082cb5b07cc37ef2188"
USER_ASSERTIONS = Path(__file__).with_name("staffer_user_assertions.json")


def apply_identity_overlay(result, existing, identity_groups=None):
    """Apply evidenced groups, preserving every source record and its provenance."""
    supplied = identity_groups is not None
    assertions = {} if supplied else json.loads(USER_ASSERTIONS.read_text(encoding="utf-8"))
    result["identity_aliases"] = {}
    result["identity_groups"] = []
    combined = {**existing, **result["nodes"]}
    for group in identity_groups if supplied else assertions.get("identity_groups", []):
        if group.get("status") not in ("USER_CONFIRMED_SAME_PERSON", "REFERENCE_RESOLVED_SAME_PERSON"):
            continue
        primary = group["primary_id"]
        ids = [primary, *group.get("alias_ids", [])]
        records = [combined[i] for i in ids if i in combined]
        if not records:
            continue
        merged = dict(combined.get(primary) or records[0])
        merged.update(id=primary, label=group["display_name"], identity_status=group["status"],
            identity_basis=group.get('basis') or ('USER_CONFIRMED' if group['status']=='USER_CONFIRMED_SAME_PERSON' else 'REFERENCE_RESOLVED'),
            source_person_ids=group["source_person_ids"], identity_alias_ids=group.get("alias_ids", []),
            identity_provenance={**group["provenance"], "asserted_at": group["asserted_at"],
                **({} if supplied else {"path": str(USER_ASSERTIONS), "sha256": sha(USER_ASSERTIONS)})},
            source_identity_records=[{"id": i, "label": combined[i].get('label'),
                "source_summary": combined[i].get('source_summary'), "provenance": combined[i].get("provenance", {}),
                "lda_source_person_id": combined[i].get("lda_source_person_id")} for i in ids if i in combined],
            canonical_identity_promoted=False)
        merged['identity_references'] = list({(r.get('url'), r.get('sha256')): r
            for r in group.get('provenance', {}).get('citations', [])}.values())
        latest = max(records, key=lambda r: r.get('last_reported_at') or r.get('source_as_of') or '')
        for field in ('source_summary', 'last_reported_at', 'source_as_of'):
            if latest.get(field):
                merged[field] = latest[field]
        originals = {}
        for record in records:
            for original in record.get('source_identity_records', []) or [
                {k: record[k] for k in ('id', 'label', 'source_summary', 'provenance', 'lda_source_person_id') if k in record}]:
                originals[original['id']] = original
        merged['source_identity_records'] = list(originals.values())
        merged['source_person_ids'] = sorted(set(group['source_person_ids']) |
            {r['lda_source_person_id'] for r in records if r.get('lda_source_person_id')} |
            {r['provenance']['lda_source_person_id'] for r in records if r.get('provenance', {}).get('lda_source_person_id')} |
            {r['id'].removeprefix('person:') for r in records if r.get('id', '').startswith('person:lda:lobbyist:')} |
            {sid for r in records for sid in r.get('source_person_ids', [])})
        merged['source_person_ids'] = sorted({sid.removeprefix('person:') if sid.startswith('person:lda:') else sid
                                             for sid in merged['source_person_ids']})
        merged['aliases'] = sorted({r.get('label', '') for r in records} |
                                   {name for r in records for name in r.get('aliases', [])})
        for field in ("member_ids", "committee_ids", "policy_ids", "source_filing_years"):
            merged[field] = sorted({v for r in records for v in r.get(field, [])})
        for field in ("employment_history", "stints", "moves", "education", "covered_positions", "source_directory_roles", "review_assertions", "profile_records", "current_positions", "reconciled_career_evidence"):
            values = {}
            for record in records:
                for value in record.get(field, []):
                    values[json.dumps(value, sort_keys=True)] = value
            if values:
                merged[field] = list(values.values())
        result["nodes"][primary] = merged
        for alias in group.get("alias_ids", []):
            result["identity_aliases"][alias] = primary
            result["nodes"].pop(alias, None)
        result["identity_groups"].append(group)
        for example in result.get('sourcecoverage', {}).get('lda', {}).get('named_example_kathleen_coulombe', []):
            if example['node_id'] in ids:
                example.update(node_id=primary, identity_merge=True, identity_status=group['status'])
    if not supplied:
        result["sources"].append({"path": str(USER_ASSERTIONS), "sha256": sha(USER_ASSERTIONS), "role": "user_confirmed_identity_overlay"})


def load_inventory(source_root, graph):
    root = Path(source_root)
    audits = root if root.name == "staffer_network" else root / "audits/staffer_network"
    existing = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", {})
    if isinstance(existing, list):
        existing = {n["id"]: n for n in existing}
    result = {"nodes": {}, "counts": {}, "sourcecoverage": {}, "sources": [], "exceptions": []}
    result["headcount_semantics"] = "Source IDs are not globally unique people. Verified LDA-to-payroll joins and accepted identity aliases consolidate records; unresolved matches remain separate. Source counts include historical people and must not be summed as unique humans."

    try:
        operational_path = root / 'audits/staffer_live_runtime/operational_population.json'
        operational = json.loads(operational_path.read_text(encoding='utf8'))
        if operational.get('status') != 'PASS' or operational.get('schema') != 'melange-operational-staffer-population-v1':
            raise ValueError('Operational staffer population is not approved')
        if sha(Path(operational['spine']['receipt'])) != operational['spine']['receipt_sha256']:
            raise ValueError('Operational staffer receipt changed')
        verification = json.loads(Path(operational['spine']['verification']).read_text(encoding='utf8'))
        if sha(Path(operational['spine']['verification'])) != operational['spine']['verification_sha256'] or verification.get('status') != 'PASS':
            raise ValueError('Operational staffer verification changed or failed')
        result['operational_staffer_ids'] = operational['staffer_ids']
        result['counts']['operational_staffer_population'] = operational['staffer_count']
        result['sources'].append({'path': str(operational_path), 'sha256': sha(operational_path),
                                  'role': 'operational_staffer_population'})
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result['exceptions'].append('Operational staffer population: ' + str(exc))

    def node(ident, label, proof, **fields):
        return {"id": ident, "label": label or "Name unavailable in source", "kind": "person",
                "member_ids": [], "committee_ids": [], "policy_ids": [], "canonical_eligible": False,
                "review_status": "REVIEW_REQUIRED", "source_only_identity": True,
                "name_status": "SOURCE_DISPLAY_NAME" if label else "UNRESOLVED_SOURCE_NAME",
                "provenance": proof, **fields}

    # The complete verified payroll spine, including people without model ties.
    try:
        receipt_path = next((Path(s['path']) for s in getattr(graph, 'sources', [])
                             if s.get('role') == 'graph_receipt'),
                            selected_graph_receipt_path(audits))
        receipt = getattr(graph, 'receipt', None) or json.loads(receipt_path.read_text(encoding="utf-8"))
        spec = next(s for s in receipt["inputs"] if s["role"] == "hill_person_spine")
        original = Path(spec["path"])
        path = audits / original.parent.name / original.name
        verified_sources = getattr(graph, "sources", [])
        existing_spine = {k: v for k, v in existing.items() if k.startswith("person:hill:person:")}
        reuse_verified = (getattr(graph, "verification", {}).get("status") == "PASS"
            and len(existing_spine) == receipt.get("node_universe", {}).get("person")
            and any(s.get("role") == "hill_person_spine" and s.get("verified")
                    and s.get("sha256") == spec["sha256"] for s in verified_sources))
        if not reuse_verified and sha(path) != spec["sha256"]:
            raise ValueError("Payroll spine hash mismatch")
        proof = {"path": str(path), "sha256": spec["sha256"], "receipt": str(receipt_path), "method": "EXACT_SPINE_ID"}
        staged = dict(existing_spine) if reuse_verified else {}
        for row in (() if reuse_verified else read_rows(path)):
            ident = "person:" + row["spine_person_id"]
            if ident in staged:
                raise ValueError("Duplicate payroll spine ID")
            key = row.get("payee_key") or {}
            label = " ".join(str(key.get(k) or "").strip() for k in ("first", "middle_initial", "last")).strip()
            staged[ident] = node(ident, label, proof, spine_person_id=row["spine_person_id"],
                lda_source_person_id=row.get("lda_source_person_id"), verification="PAYROLL_SPINE_VERIFIED")
        result["nodes"].update({k: v for k, v in staged.items() if k not in existing})
        result["counts"]["payroll_source_identities"] = len(staged)
        result["sourcecoverage"]["payroll"] = {"status": "VERIFIED_SPINE_INVENTORY", "source_ids": len(staged),
            "reused_verified_static_inventory": reuse_verified,
            "scope": "Verified payroll identities in the bound generation, not a claim every historical raw payroll record was resolved"}
        spine_receipt_path = path.parent / "hill_personnel_spine_receipt.json"
        spine_spec = next(s for s in receipt["inputs"] if s["role"] == "hill_personnel_spine_receipt")
        if sha(spine_receipt_path) != spine_spec["sha256"]:
            raise ValueError("Payroll coverage receipt hash mismatch")
        spine_receipt = json.loads(spine_receipt_path.read_text(encoding="utf-8"))
        result["sourcecoverage"]["payroll"].update({
            "admitted_source_observations": spine_receipt["conservation"]["observations_admitted"],
            "observation_conservation_holds": spine_receipt["conservation"]["observation_conservation_holds"],
            "source_rows_suppressed": spine_receipt["sources"]["source_rows_suppressed"],
            "source_rows_suppressed_by_reason": spine_receipt["sources"]["source_rows_suppressed_by_reason"],
            "coverage_receipt": str(spine_receipt_path), "coverage_receipt_sha256": spine_spec["sha256"]})
        if spine_receipt.get("inputs"):
            if sha(PAYROLL_EXCEPTIONS) != PAYROLL_EXCEPTIONS_SHA256:
                raise ValueError("Sealed payroll exception inventory hash mismatch")
            exceptions = json.loads(PAYROLL_EXCEPTIONS.read_text(encoding="utf-8"))
            bound_hashes = {s["sha256"] for s in spine_receipt["inputs"].values() if s.get("sha256")}
            if any(s["sha256"] not in bound_hashes for s in exceptions["sources"]):
                raise ValueError("Payroll exception sources not bound by the payroll receipt")
            for record in exceptions["nonempty_unkeyed_rows"]:
                ident = "payroll-unresolved-record:" + str(record["row_id"])
                result["nodes"][ident] = node(ident, record["name"], {
                    "path": str(PAYROLL_EXCEPTIONS), "sha256": PAYROLL_EXCEPTIONS_SHA256,
                    "source_hashes": exceptions["sources"], **record},
                    kind="payroll_record", verification="SOURCE_ROW_VERIFIED_IDENTITY_UNRESOLVED",
                    identity_resolved=False, staffer_identity_asserted=False,
                    source_record_not_unique_person=True, operator_included=False)
            result["counts"]["payroll_unresolved_named_source_records"] = len(exceptions["nonempty_unkeyed_rows"])
            result["counts"]["payroll_empty_payee_source_rows"] = exceptions["empty_payee_rows"]
            result["sourcecoverage"]["payroll"]["unresolved_named_rows_visible_as_source_records"] = len(exceptions["nonempty_unkeyed_rows"])
        result["sources"].append(proof)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result["exceptions"].append(str(exc))

    try:
        candidate = root / "data/staffer_network/lda-source-native/generation-20260830T231200Z"
        base = candidate if candidate.exists() else LDA_ROOT
        receipt_path = base / "lda_source_native_corpus_receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("status") != "PASS" or receipt.get("failures"):
            raise ValueError("LDA inventory receipt not PASS")
        spec = next(s for s in receipt["files"] if s["role"] == "lobbyist_assertions")
        path = base / spec["path"]
        if sha(path) != spec["sha256"]:
            raise ValueError("LDA person inventory hash mismatch")
        proof = {"path": str(path), "sha256": spec["sha256"], "receipt": str(receipt_path),
                 "receipt_sha256": sha(receipt_path), "cutoff_exclusive": receipt.get("cutoff_exclusive"), "method": "EXACT_LDA_SOURCE_ID"}
        links = {}
        for ident, value in {**result["nodes"], **existing}.items():
            if value.get("lda_source_person_id") and (value.get("spine_person_id") or ident.startswith("person:hill:person:")):
                links.setdefault(value["lda_source_person_id"], set()).add(ident)
        staged, seen, joined, ambiguous = {}, set(), 0, 0
        verified_source_links = {}
        kathleen = []
        for row in read_rows(path):
            source_id = row["source_person_id"]
            if source_id in seen:
                raise ValueError("Duplicate LDA source person ID")
            seen.add(source_id)
            targets = links.get(source_id, set())
            if len(targets) == 1:
                ident = next(iter(targets))
                joined += 1
                verified_source_links[source_id] = ident
            else:
                ident = "person:" + source_id
                ambiguous += len(targets) > 1
            staged[ident] = node(ident, row.get("display_name"), {**proof, "source_person_id": source_id},
                lda_source_person_id=source_id, verification="LDA_SOURCE_PERSON_VERIFIED",
                source_filing_years=row.get("filing_years", []), source_as_of=row.get("last_dt_posted"))
            if "COULOMBE" in row.get("display_name", "").upper() and "KATHLEEN" in row.get("display_name", "").upper():
                kathleen.append({"source_person_id": source_id, "node_id": ident, "display_name": row["display_name"],
                    "last_dt_posted": row.get("last_dt_posted"), "activity_support_count": row.get("activity_support_count"), "identity_merge": False})
        if len(seen) != spec.get("records", len(seen)):
            raise ValueError("LDA inventory record count mismatch")
        result["nodes"].update({k: v for k, v in staged.items() if k not in existing and k not in result["nodes"]})
        result["counts"].update(lda_source_person_ids=len(seen), lda_unique_verified_spine_joins=joined,
                                 lda_ambiguous_spine_joins_kept_separate=ambiguous)
        result['verified_source_links'] = verified_source_links
        result["sourcecoverage"]["lda"] = {"status": "ALL_VERIFIED_SOURCE_PERSON_IDS", "source_ids": len(seen), "named_example_kathleen_coulombe": kathleen}
        result["sources"].append(proof)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result["exceptions"].append(str(exc))

    try:
        receipt_path = audits / "politico_profile_crawl_20260903_raw/crawl_verification.json"
        before = receipt_path.read_bytes()
        receipt = json.loads(before)
        if receipt.get("integrity_status", receipt.get("status")) != "PASS" or receipt.get("verification_failure_count", 0):
            raise ValueError("Politico directory inventory receipt integrity not PASS")
        path = Path(receipt["inputs"]["directory"])
        expected_hash = receipt["input_sha256"]["directory"]
        payload = path.read_bytes()
        import hashlib
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise ValueError("Politico directory hash mismatch")
        staged = {}
        proof = {"path": str(path), "sha256": expected_hash, "receipt": str(receipt_path),
                 "method": "SEALED_DIRECTORY_UUID_ONLY"}
        for line in payload.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            ident = "politico:" + row["profile_uuid"]
            if ident in staged:
                raise ValueError("Duplicate Politico directory UUID")
            staged[ident] = node(ident, row.get("name"), {**proof, "profile_uuid": row["profile_uuid"]},
                source_profile_uuid=row["profile_uuid"], verification="SOURCE_DIRECTORY_VERIFIED_PROFILE_PENDING",
                source="Politico sealed directory", source_directory_roles=row.get("roles", []),
                profile_pending=True, profile_status="NOT_ASSERTED_BY_DIRECTORY", operator_included=False)
        expected = receipt.get("counts", {}).get("sealed_directory_unique_profiles")
        if expected is None or len(staged) != expected:
            raise ValueError("Politico sealed directory count mismatch")
        # Receipt may advance its profile prefix meanwhile; immutable directory digest remains authoritative.
        after = json.loads(receipt_path.read_bytes())
        if after.get("input_sha256", {}).get("directory") != expected_hash:
            raise ValueError("Politico directory receipt changed while loading")
        result["nodes"].update({k: v for k, v in staged.items() if k not in existing})
        result["counts"]["politico_directory_source_ids"] = len(staged)
        result["sourcecoverage"]["politico"] = {"status": "SEALED_DIRECTORY_ONLY", "source_ids": len(staged),
            "profile_coverage_inferred": False, "missing_profile_is_absence": False}
        result["sources"].append(proof)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result["exceptions"].append(str(exc))
    from fec.surfaces.staffer_reconciled_evidence import load_release, augment_inventory
    augment_inventory(result, load_release(root / 'audits/staffer_live_runtime'), existing)
    result["raw_nodes"] = dict(result["nodes"])
    try:
        apply_identity_overlay(result, existing)
    except (OSError, ValueError, KeyError) as exc:
        result["exceptions"].append("User identity overlay: " + str(exc))
    result["counts"]["user_confirmed_identity_groups"] = len(result.get("identity_groups", []))
    result["counts"]["new_display_nodes"] = len(result["nodes"])
    result["counts"]["new_display_identity_nodes"] = sum(n.get("kind") == "person" for n in result["nodes"].values())
    return result
