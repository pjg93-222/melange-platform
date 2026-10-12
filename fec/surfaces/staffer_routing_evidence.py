"""Exact-ID, hash-bound professional labels and dated LDA associations.

Filing-year bounds are evidence windows, never continuous employment claims.
No name-only joins or identity promotions are performed.
"""
import gzip
import hashlib
import json
from pathlib import Path


try:
    from .staffer_assertions import load_coworker_assertions, record_coworker_decision, local_date
    from .staffer_graph_snapshot import selected_graph_receipt_path
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_assertions import load_coworker_assertions, record_coworker_decision, local_date
    from fec.surfaces.staffer_graph_snapshot import selected_graph_receipt_path

LDA_ROOT = Path(r"C:\Users\pgoff\AppData\Local\Melange\staffer_network\normalized\lda-source-native\generation-20260830T231200Z")
EXAMPLES = {"Gordon Holzberg": "lda:lobbyist:147664", "Stephanie Belk": "lda:lobbyist:147663",
            "Greg Mesack": "lda:lobbyist:148612"}
NAMED_ARTIFACT = Path(__file__).with_name("staffer_named_filing_evidence.json")
NAMED_SHA256 = "81f95047665c442b01a13a6c5b53a10ca021a7151c35295d5b8d84fa0959a9aa"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def add_named_activities(result, lda_root):
    """Use a sealed extraction whose source hashes were verified during extraction."""
    receipt_path = lda_root / "lda_source_native_corpus_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    specs = {s["path"]: s for s in receipt["files"]}
    if "lda_industry_edges.ndjson.gz" not in specs:
        return  # Partial test corpus has no source activity evidence.
    if receipt.get("status") != "PASS" or receipt.get("failures"):
        raise ValueError("Named filing source receipt has not passed")
    if sha(NAMED_ARTIFACT) != NAMED_SHA256:
        raise ValueError("Sealed named filing extraction SHA-256 mismatch")
    data = json.loads(NAMED_ARTIFACT.read_text(encoding="utf-8"))
    for source in data["sources"]:
        path = Path(source["path"])
        spec = specs.get(path.name)
        if not spec or spec["sha256"] != source["sha256"]:
            raise ValueError("Named filing source hash is not bound by native receipt")
        if (lda_root / path.name).stat().st_size != spec["bytes"]:
            raise ValueError("Named filing source size changed since extraction")
    proof = {"path": str(NAMED_ARTIFACT), "sha256": NAMED_SHA256,
             "source_receipt": str(receipt_path), "source_receipt_sha256": sha(receipt_path),
             "sources": data["sources"], "method": "EXACT_NAMED_ON_SAME_ACTIVITY",
             "verification_basis": "Sealed extraction; original source hashes verified during extraction and matched to PASS receipt at load"}
    result["sources"].append(proof)
    mapping = {v["lda_source_person_id"]: v["person_id"] for v in result["named_examples"].values() if v["person_id"]}
    for activity in data["shared_activities"]:
        hub = "activity:" + activity["source_activity_id"]
        quarter = {"first_quarter": 1, "second_quarter": 2, "third_quarter": 3, "fourth_quarter": 4}[activity["filing_period"]]
        label = f"America’s Credit Unions · {activity['filing_year']} Q{quarter} activity {activity['general_issue_code']}"
        provenance = {**proof, **activity}
        result["labels"][hub] = {"label": label, "name_status": "SOURCE_DISPLAY_NAME", "provenance": provenance}
        result["nodes"][hub] = {"id": hub, "label": label, "kind": "organization",
            "evidence_kind": "lobbying_activity", "canonical_eligible": False, "provenance": provenance}
        for lda_id in activity["person_ids"]:
            if lda_id not in mapping:
                continue
            result["episodes"].append({"person_id": mapping[lda_id], "employer_id": hub,
                "employer_label": label, "firm_id": "organization:" + activity["registrant_source_org_id"],
                "relationship": "co_reported_lobbying_activity", "start": activity["start"],
                "end_exclusive": activity["end_exclusive"], "as_of": activity["dt_posted"],
                "date_precision": "report_quarter", "current_evidence": False,
                "confidence": "EXACT_SAME_FILING_SAME_ACTIVITY", "review_only": True,
                "continuous_employment_asserted": False, "provenance": provenance})
    for example in result["named_examples"].values():
        matches = [a for a in data["shared_activities"] if example["lda_source_person_id"] in a["person_ids"]]
        example["shared_activity_count"] = len(matches)
        example["shared_filing_count"] = len({a["filing_uuid"] for a in matches})
        if matches:
            latest = max(matches, key=lambda a: (a["end_exclusive"], a["dt_posted"]))
            example["latest_shared_activity"] = {k: latest[k] for k in
                ("start", "end_exclusive", "dt_posted", "filing_uuid", "filing_period", "filing_year")}


def load_evidence(source_root, graph):
    """Return sidecar evidence; never modify graph, raw sources, or model weights."""
    root = Path(source_root)
    audits = root if root.name == "staffer_network" else root / "audits/staffer_network"
    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", {})
    if isinstance(nodes, list):
        nodes = {n["id"]: n for n in nodes}
    result = {"labels": {}, "nodes": {}, "episodes": [], "named_examples": {},
              "sources": [], "errors": [], "user_assertions": []}

    def checked(directory, receipt_name, filename):
        receipt_path = directory / receipt_name
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("status") != "PASS" or receipt.get("failures"):
            raise ValueError("Evidence receipt not PASS: " + str(receipt_path))
        spec = next(s for s in receipt["files"] if s["path"] == filename)
        path = directory / filename
        actual = sha(path)
        if actual != spec["sha256"] or path.stat().st_size != spec["bytes"]:
            raise ValueError("Evidence hash/size mismatch: " + str(path))
        provenance = {"path": str(path), "sha256": actual, "receipt": str(receipt_path),
                      "receipt_sha256": sha(receipt_path), "method": "EXACT_SOURCE_ID",
                      "source_cutoff_exclusive": receipt.get("cutoff_exclusive")}
        result["sources"].append(provenance)
        return path, provenance

    try:
        episode_dir = audits / "lda_registrant_employment_20260831"
        path, provenance = checked(episode_dir, "lda_registrant_employment_receipt.json",
                                   "lda_registrant_employment_episodes.ndjson.gz")
        episodes = list(read_rows(path))
        # Graph's verified spine supplies exact cross-source identity links.
        joins = {}
        for node_id, node in nodes.items():
            lda_id = node.get("lda_source_person_id")
            if lda_id:
                joins.setdefault(lda_id, []).append(node_id)
        for row in episodes:
            lda_id = row["lda_source_person_id"]
            matched = joins.get(lda_id, [])
            if len(matched) > 1:
                result["errors"].append("Ambiguous exact LDA identity link: " + lda_id)
            person = matched[0] if len(matched) == 1 else "person:" + lda_id
            employer = "organization:" + row["registrant_source_org_id"]
            proof = {**provenance, "episode_id": row["episode_id"], "lda_source_person_id": lda_id,
                     "registrant_source_org_id": row["registrant_source_org_id"],
                     "first_filing_posted_at": row.get("first_filing_posted_at"),
                     "last_filing_posted_at": row.get("last_filing_posted_at"),
                     "filing_activity_support_count": row.get("filing_activity_support_count"),
                     "filing_years": row.get("filing_years", [])}
            for ident, label in ((person, row["display_name"]), (employer, row["employer_name"])):
                result["labels"][ident] = {"label": label, "name_status": "SOURCE_DISPLAY_NAME", "provenance": proof}
            if lda_id in EXAMPLES.values() and person not in nodes:
                result["nodes"][person] = {"id": person, "label": row["display_name"], "kind": "person",
                    "lda_source_person_id": lda_id, "source_only_identity": True, "canonical_eligible": False,
                    "member_ids": [], "committee_ids": [], "policy_ids": [], "provenance": proof}
            result["episodes"].append({"person_id": person, "employer_id": employer,
                "start": f"{row['start_year']}-01-01", "end_exclusive": f"{row['end_year'] + 1}-01-01",
                "as_of": row.get("last_filing_posted_at"), "current_evidence": False,
                "confidence": "SOURCE_NATIVE_FILING_ASSOCIATION", "review_only": True,
                "date_precision": "filing_year", "continuous_employment_asserted": False,
                "filing_years": row.get("filing_years", []), "provenance": proof})
        # Explicit source IDs are already known; matching a similar name does not join them.
        for name, lda_id in EXAMPLES.items():
            found = [e for e in result["episodes"] if e["provenance"]["lda_source_person_id"] == lda_id]
            result["named_examples"][name] = {"lda_source_person_id": lda_id, "episodes": found,
                "person_id": found[0]["person_id"] if found else None,
                "current_employment_verified": False,
                "identity_note": ("Standalone LDA source identity; older Mesack IDs 121707 and 70066 remain separate"
                                  if name == "Greg Mesack" else "Exact verified-spine LDA ID when available")}
        result["user_assertions"] = load_coworker_assertions()
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result["errors"].append(str(exc))

    # Full organization dictionary supplies names even for non-employment graph firms.
    try:
        candidate = root / "data/staffer_network/lda-source-native/generation-20260830T231200Z"
        lda_root = candidate if candidate.exists() else LDA_ROOT
        path, proof = checked(lda_root, "lda_source_native_corpus_receipt.json", "lda_organization_assertions.ndjson.gz")
        for row in read_rows(path):
            ident = "organization:" + row["source_org_id"]
            if ident in nodes or ident in result["labels"]:
                result["labels"][ident] = {"label": row["display_name"], "name_status": "SOURCE_DISPLAY_NAME",
                    "provenance": {**proof, "source_org_id": row["source_org_id"]}}
        add_named_activities(result, lda_root)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result["errors"].append(str(exc))
    # Exact chamber/office keys retain the payroll source's readable office names.
    try:
        graph_receipt_path = next((Path(s['path']) for s in getattr(graph, 'sources', [])
                                   if s.get('role') == 'graph_receipt'),
                                  selected_graph_receipt_path(audits))
        graph_receipt = getattr(graph, 'receipt', None) or json.loads(graph_receipt_path.read_text(encoding="utf-8"))
        spec = next(s for s in graph_receipt["inputs"] if s["role"] == "hill_member_office_index")
        original = Path(spec["path"])
        path = audits / original.parent.name / original.name
        actual = sha(path)
        if actual != spec["sha256"]:
            raise ValueError("Office name input SHA-256 mismatch")
        proof = {"path": str(path), "sha256": actual, "receipt": str(graph_receipt_path),
                 "method": "EXACT_CHAMBER_OFFICE_KEY"}
        result["sources"].append(proof)
        for row in read_rows(path):
            ident = "container:office:" + str(row.get("chamber")) + ":" + str(row.get("office_key"))
            names = row.get("office_raw_examples") or []
            if ident in nodes and names:
                result["labels"][ident] = {"label": names[0], "name_status": "SOURCE_DISPLAY_NAME",
                    "provenance": {**proof, "office_key": row["office_key"], "chamber": row["chamber"]}}
    except FileNotFoundError:
        pass  # Small synthetic graphs may not include payroll office artifacts.
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result["errors"].append(str(exc))
    for ident, node in nodes.items():
        if ident in result["labels"]:
            continue
        label = node.get("label", "")
        unresolved = node.get('name_status') == 'UNRESOLVED_SOURCE_NAME' or not label or label == ident or label.startswith(("person:", "organization:", "hill:"))
        result["labels"][ident] = {"label": ("Name unavailable in verified source" if unresolved else label),
            "name_status": "UNRESOLVED_SOURCE_NAME" if unresolved else "EXISTING_VERIFIED_DISPLAY_NAME",
            "provenance": node.get("label_provenance") or node.get("provenance", {})}
    return result
