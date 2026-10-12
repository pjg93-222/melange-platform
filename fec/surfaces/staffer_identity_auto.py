"""Deterministic evidence-backed display identity consolidation, never raw mutation."""
import hashlib
import itertools
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlsplit
try:
    from .staffer_payroll_identity import employment_overlap
    from fec.staffer_network.name_standard import parse_name, fold_key, NAME_STANDARD_VERSION
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_payroll_identity import employment_overlap
    from fec.staffer_network.name_standard import parse_name, fold_key, NAME_STANDARD_VERSION

EVIDENCE_VERSION = "staffer-identity-evidence-v8-surname-guard"
NICKNAMES = {"greg": "gregory", "bob": "robert", "bill": "william", "liz": "elizabeth",
             "beth": "elizabeth", "jim": "james", "mike": "michael", "steve": "stephen",
             "tom": "thomas", "dan": "daniel", "joe": "joseph", "kate": "katherine"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
# Overlay-application literal read by staffer_person_inventory.apply_identity_overlay's
# status gate; the proof kind of each automatic group lives in resolution_status/basis.
OVERLAY_STATUS = "REFERENCE_RESOLVED_SAME_PERSON"
REFERENCE_METHODS = {"OFFICIAL_BIOGRAPHY_BRIDGES_CAREER_ANCHORS",
                     "TWO_INDEPENDENT_PUBLIC_BIOGRAPHIES_BRIDGE_CAREER_ANCHORS"}
EVIDENCE_DATE_KEYS = ("last_filing_posted_at", "as_of", "last_reported_at")


def normalized(value):
    value = str(value).replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def name_parts(node):
    parts = normalized(node.get("label") or node.get("display_name") or "").split()
    while parts and parts[0] in {"mr", "mrs", "ms", "dr", "miss"}:
        parts.pop(0)
    suffix = parts.pop() if parts and parts[-1] in SUFFIXES else None
    if len(parts) < 2:
        return None
    return NICKNAMES.get(parts[0], parts[0]), parts[-1], parts[1:-1], suffix


@lru_cache(maxsize=65536)
def surname_signature(label):
    """Use the corpus parser to preserve compound surnames and explicit suffixes.

    This is a veto on existing candidates, not a new positive identity rule.
    Keep the legacy first-name candidate table until expanded classes have an
    independently reviewed migration (some classes join distinct formal names).
    """
    parts = parse_name(label, source='display')
    return fold_key(parts.last), fold_key(parts.suffix)


def compatible(a, b):
    x, y = name_parts(a), name_parts(b)
    if not x or not y or x[:2] != y[:2]:
        return False
    labels = [str(n.get('label') or n.get('display_name') or '') for n in (a, b)]
    sa, sb = map(surname_signature, labels)
    # Identical complete token sequences may differ only in punctuation, which
    # can change the parser's surname boundary (Connery-Dawe / Connery Dawe).
    # No omitted, reordered, or expanded token receives this exception.
    same_tokens = normalized(labels[0]) == normalized(labels[1])
    if not same_tokens and (not sa[0] or sa[0] != sb[0] or (sa[1] and sb[1] and sa[1] != sb[1])):
        return False
    if x[3] and y[3] and x[3] != y[3]:
        return False
    if x[2] and y[2]:
        if len(x[2]) != len(y[2]):
            return False
        for left, right in zip(x[2], y[2]):
            if left != right and not (left[0] == right[0] and (len(left) == 1 or len(right) == 1)):
                return False
    for field in ("date_of_birth", "dob"):
        if a.get(field) and b.get(field) and a[field] != b[field]:
            return False
    da, db = a.get("date_of_birth") or a.get("dob"), b.get("date_of_birth") or b.get("dob")
    return not (da and db and da != db)


def source_bound(proof):
    return isinstance(proof, dict) and bool(proof.get("sha256") and (proof.get("path") or proof.get("url")))


def normalized_title(value):
    return re.sub(r'\badviser\b', 'advisor', normalized(value))


def organization_variants(value):
    text = normalized(value)
    pieces = re.split(r"\b(?:dba|fka|formerly|formerly known as)\b", text)
    variants = set()
    for piece in pieces:
        piece = re.sub(r"\b(?:incorporated|inc|llc|llp|pa|pllc|corp|corporation)\b", " ", piece)
        piece = " ".join(piece.split())
        if len(piece) >= 8:
            variants.add(piece)
    return variants


def phrase_present(phrase, text):
    return bool(phrase) and (" " + phrase + " ") in (" " + text + " ")


def independent_organizations(left, right):
    # A short/long spelling of one employer is not a two-employer career bridge.
    return not phrase_present(left, right) and not phrase_present(right, left)


def publisher_domain(url):
    """Conservative hostname grouping; common country-code suffixes retain 3 labels."""
    host = (urlsplit(url).hostname or "").strip(".").casefold()
    labels = host.split(".")
    if len(labels) < 2 or all(p.isdigit() for p in labels):
        return ""
    country_suffix = len(labels[-1]) == 2 and labels[-2] in {"co", "com", "net", "org", "ac", "edu", "gov"}
    return ".".join(labels[-3:] if country_suffix and len(labels) >= 3 else labels[-2:])


def source_person_id(value):
    """Source-record id vocabulary: node ids carry a person: prefix, source ids never do."""
    return str(value).removeprefix("person:")


def proof_kind(proof):
    return "reference" if proof.get("method") in REFERENCE_METHODS or proof.get("citations") else "record"


def evidence_date(proof):
    """Latest date the proof's own evidence carries, labelled; (None, None) when it has none."""
    cited = [str(c.get("retrieved_at") or "") for c in proof.get("citations", []) if isinstance(c, dict)]
    if any(cited):
        return max(cited), "reference_retrieved_at"
    employment = [str(e.get("end_exclusive") or "") for e in proof.get("employment", []) if isinstance(e, dict)]
    if employment and all(employment):
        return min(employment), "employment_overlap_end"
    posted = [str(p.get(key) or "") for p in proof.get("source_provenance", []) if isinstance(p, dict)
              for key in EVIDENCE_DATE_KEYS]
    if any(posted):
        return max(posted), "source_record_posted_at"
    return None, None


def resolve_identities(nodes, episodes, references, confirmed_aliases, keep_separate_pairs, resolved_at=None):
    resolved_at = resolved_at or datetime.now(timezone.utc).isoformat()
    people = {k: v for k, v in nodes.items() if v.get("kind", "person") == "person" and name_parts(v)}
    def confirmed(ident):
        seen = set()
        while ident in confirmed_aliases:
            if ident in seen:
                raise ValueError("Confirmed identity alias cycle")
            seen.add(ident)
            ident = confirmed_aliases[ident]
        return ident
    denied = {frozenset((confirmed(a), confirmed(b))) for a, b in keep_separate_pairs if confirmed(a) != confirmed(b)}
    families = defaultdict(list)
    for ident, node in people.items():
        families[name_parts(node)[:2]].append(ident)
    by_person = defaultdict(list)
    by_job = defaultdict(lambda: defaultdict(list))
    for episode in episodes:
        if episode.get("person_id") in people:
            by_person[episode["person_id"]].append(episode)
            title = normalized_title(episode.get('title', ''))
            if (episode.get('employment_verified') and source_bound(episode.get('provenance'))
                    and episode.get('employer_id') and len(title) >= 8):
                by_job[episode['person_id']][(episode['employer_id'], title)].append(episode)
    valid_refs = []
    for reference in references:
        text = reference.get("text", "")
        if (reference.get("source_kind") in {"official_biography", "official_filing", "verified_directory", "public_biography_candidate"}
                and str(reference.get("url", "")).startswith("https://")
                and (reference.get("source_kind") != "public_biography_candidate" or reference.get("subject_scoped") is True)
                and (reference.get("subject_scoped") is True or
                     (reference.get("name") and compatible({"label": reference["name"]}, {"label": reference.get("title", "")})))
                and reference.get("sha256") == hashlib.sha256(text.encode("utf-8")).hexdigest()):
            valid_refs.append(reference)
    histories, history_owners, anchors, external = {}, {}, {}, defaultdict(set)
    for ident, node in people.items():
        proof = node.get("provenance", {})
        owners = set(node.get("covered_position_source_ids", []))
        owners.update(i for i in node.get("source_person_ids", []) if str(i).startswith(("lda:lobbyist:", "politico:")))
        for owner in (node.get("lda_source_person_id"), proof.get("source_person_id"),
                      proof.get("lda_source_person_id"), proof.get("profile_uuid")):
            if owner:
                owners.add(str(owner))
        if ident.startswith("person:lda:lobbyist:"):
            owners.add(ident.removeprefix("person:"))
        elif ident.startswith("politico:"):
            owners.add(ident)
        history_owners[ident] = owners
        values = node.get("covered_positions") or node.get("covered_position_values") or []
        histories[ident] = {normalized(h) for h in values if isinstance(h, str) and len(normalized(h)) >= 80
                            and len(set(normalized(h).split())) >= 12} if source_bound(proof) else set()
        anchors[ident] = []
        for anchor in node.get("career_anchors", []) + node.get("employment_history", []):
            if not isinstance(anchor, dict):
                continue
            org = anchor.get("organization") or anchor.get("employer") or ""
            role = anchor.get("title") or anchor.get("role") or ""
            if len(normalized(org)) >= 8 and source_bound(anchor.get("provenance") or proof):
                anchors[ident].extend((variant, normalized(role)) for variant in organization_variants(org))
        for ep in by_person[ident]:
            org = ep.get("employer_label") or nodes.get(ep.get("employer_id"), {}).get("label", "")
            if len(normalized(org)) >= 8 and source_bound(ep.get("provenance")):
                anchors[ident].extend((variant, normalized(ep.get("title") or "")) for variant in organization_variants(org))
        summary = node.get("source_summary", "").split("·", 1)[0].strip()
        if summary and source_bound(proof):
            anchors[ident].extend((variant, "") for variant in organization_variants(summary))
        # Only unique externally verified keys, not ambiguous upstream LDA match assignments.
        for key in node.get("external_ids", []):
            if key.get("verified_unique") and source_bound(key.get("provenance")):
                external[(key.get("namespace"), key.get("value"))].add(ident)

    def proof_for(a, b):
        pair = {a, b}
        for key, ids in external.items():
            if ids == pair and all(key):
                return {"method": "SHARED_UNIQUE_EXTERNAL_ID", "external_id": list(key),
                        "source_provenance": [people[i].get("external_ids") for i in (a, b)]}
        common = histories[a] & histories[b]
        # Enriching one ambiguous LDA record onto several payroll candidates
        # does not create independent matching histories for those candidates.
        if common and history_owners[a] and history_owners[b] and not history_owners[a] & history_owners[b]:
            return {"method": "IDENTICAL_DISTINCTIVE_COVERED_POSITION_HISTORY", "history": sorted(common)[0],
                    "history_source_owners": {a: sorted(history_owners[a]), b: sorted(history_owners[b])},
                    "source_provenance": [people[i].get("provenance") for i in (a, b)]}
        for job in sorted(by_job[a].keys() & by_job[b].keys()):
            for left, right in itertools.product(by_job[a][job], by_job[b][job]):
                title = job[1]
                overlap = employment_overlap(left, right)
                if (left.get("employer_id") == right.get("employer_id") and left.get("employer_id")
                        and len(title) >= 8 and title == normalized_title(right.get("title", ""))
                        and overlap):
                    overlap_start, overlap_end = overlap
                    competing = any(ident not in pair and compatible(people[a], people[ident]) and any(
                        ep.get("employment_verified") and source_bound(ep.get("provenance"))
                        and ep.get("employer_id") == left["employer_id"] and normalized_title(ep.get("title", "")) == title
                        and ep.get("start") and ep.get("end_exclusive")
                        and max(overlap_start, ep["start"]) < min(overlap_end, ep["end_exclusive"])
                        for ep in by_job[ident].get(job, [])) for ident in families[name_parts(people[a])[:2]])
                    if competing:
                        continue
                    method = ("VERIFIED_OFFICE_TITLE_DURING_REPORTED_WINDOW"
                              if any(ep.get('date_evidence') == 'presence_within_report' for ep in (left, right))
                              else "VERIFIED_SAME_OFFICE_TITLE_OVERLAP")
                    return {"method": method, "employment": [left, right]}
        public_support = defaultdict(list)
        for reference in valid_refs:
            subject = reference.get("name") or reference.get("title", "")
            if not compatible(people[a], {"label": subject}) or not compatible(people[b], {"label": subject}):
                continue
            text = normalized(reference["text"])
            # Generic long-form (ACRONYM) expansions are tied to this reference,
            # never a person-specific identity dictionary.
            for full, acronym in re.findall(r"([A-Za-z][A-Za-z ,&’'\-]{10,120})\s*\(([A-Z][A-Z0-9]{1,12})\)", reference["text"]):
                if normalized(acronym) in text:
                    text += " " + normalized(full)
            hits = {i: {(org, title) for org, title in anchors[i] if phrase_present(org, text)
                        and (not title or phrase_present(title, text))} for i in (a, b)}
            # At least two distinct employers bridge both records; one shared firm is insufficient.
            org_a, org_b = {org for org, _ in hits[a]}, {org for org, _ in hits[b]}
            bridge_pairs = [(left, right) for left in sorted(org_a - org_b) for right in sorted(org_b - org_a)
                            if independent_organizations(left, right)]
            if bridge_pairs:
                evidence = {"method": "OFFICIAL_BIOGRAPHY_BRIDGES_CAREER_ANCHORS",
                    "anchors": {i: sorted(hits[i]) for i in (a, b)},
                    "citations": [{k: reference.get(k) for k in ("url", "title", "sha256", "retrieved_at", "source_kind")}],
                    "source_provenance": [people[i].get("provenance") for i in (a, b)]}
                if reference.get("source_kind") != "public_biography_candidate":
                    return evidence
                domain = publisher_domain(reference["url"])
                if not domain:
                    continue
                publisher = normalized(reference.get("publisher_id") or reference.get("publisher") or domain)
                for anchor_pair in bridge_pairs:
                    prior = public_support[anchor_pair]
                    independent = next((item for item in prior if item[0] != domain and item[1] != publisher
                                        and item[2] != reference["sha256"]), None)
                    if independent:
                        return {**evidence, "method": "TWO_INDEPENDENT_PUBLIC_BIOGRAPHIES_BRIDGE_CAREER_ANCHORS",
                            "shared_anchor_pair": list(anchor_pair),
                            "publisher_domains": sorted([independent[0], domain]),
                            "citations": independent[3]["citations"] + evidence["citations"]}
                    prior.append((domain, publisher, reference["sha256"], evidence))
        return None

    pairs, unresolved, blocked = {}, [], 0
    for family in families.values():
        for a, b in itertools.combinations(sorted(family), 2):
            if confirmed(a) == confirmed(b):
                continue
            if not compatible(people[a], people[b]) or frozenset((confirmed(a), confirmed(b))) in denied:
                blocked += 1
                continue
            proof = proof_for(a, b)
            if proof:
                pairs[(a, b)] = proof
            else:
                unresolved.append({"display_name": people[a].get("label"), "node_ids": [a, b], "reason": "INSUFFICIENT_IDENTITY_EVIDENCE", "auto_merge": False})
    # A component must remain a clique of direct evidence. A-B plus B-C alone
    # cannot bridge two homonyms whose A-C records lack evidence or contradict.
    initial = defaultdict(set)
    for ident in people:
        initial[confirmed(ident)].add(ident)
    components = {i: group for group in initial.values() for i in group}
    for (a, b), proof in sorted(pairs.items()):
        combined = components[a] | components[b]
        if all((x == y or confirmed(x) == confirmed(y) or tuple(sorted((x, y))) in pairs)
               and compatible(people[x], people[y])
               and frozenset((confirmed(x), confirmed(y))) not in denied
               for x in combined for y in combined):
            for ident in combined:
                components[ident] = combined
        else:
            unresolved.append({"display_name": people[a].get("label"), "node_ids": [a, b], "reason": "TRANSITIVE_COMPONENT_CONFLICT", "auto_merge": False})
    groups, aliases, emitted = [], dict(confirmed_aliases), set()
    for component in components.values():
        key = tuple(sorted(component))
        if len(key) < 2 or key in emitted:
            continue
        emitted.add(key)
        existing_primaries = sorted({confirmed(i) for i in key if confirmed(i) != i or i in confirmed_aliases.values()})
        primary = existing_primaries[0] if existing_primaries else key[0]
        group_aliases = sorted(set(key) - {primary})
        proofs = [pairs[p] for p in itertools.combinations(key, 2) if p in pairs]
        if not proofs:
            continue
        kinds = sorted({proof_kind(proof) for proof in proofs})
        basis = "_AND_".join(kind.upper() for kind in kinds) + "_RESOLVED"
        dated = [d for d in (evidence_date(proof) for proof in proofs) if d[0]]
        asserted_at, asserted_basis = max(dated, key=lambda d: d[0]) if dated else (resolved_at, "resolution_run_time")
        group = {"primary_id": primary, "alias_ids": group_aliases,
            "source_person_ids": sorted({source_person_id(source) for i in key for source in
                (people[i].get("source_person_ids") or [people[i].get("lda_source_person_id") or i])}),
            "display_name": people.get(primary, people[key[0]]).get("label", ""),
            "status": OVERLAY_STATUS, "resolution_status": basis + "_SAME_PERSON", "basis": basis,
            "asserted_at": asserted_at, "asserted_at_basis": asserted_basis,
            "provenance": {"method": EVIDENCE_VERSION, "basis": basis, "proof_kinds": kinds,
                "evidence_methods": sorted({proof["method"] for proof in proofs}), "asserted_at_basis": asserted_basis,
                "evidence": proofs, "citations": [c for proof in proofs for c in proof.get("citations", [])]},
            "canonical_identity_promoted": False}
        groups.append(group)
        aliases.update({i: primary for i in group_aliases})
    return {"identity_groups": groups, "identity_aliases": aliases, "unresolved_candidates": unresolved,
        "counts": {"people_input": sum(n.get("kind", "person") == "person" for n in nodes.values()),
            "people_checked": len(people), "automatic_resolved_groups": len(groups),
            "resolved_groups_by_basis": {b: sum(g["basis"] == b for g in groups) for b in sorted({g["basis"] for g in groups})},
            "new_aliases": sum(len(g["alias_ids"]) for g in groups), "unresolved_candidate_pairs": len(unresolved),
            "contradicted_or_separated_pairs": blocked, "validated_references": len(valid_refs)},
        "evidence_version": EVIDENCE_VERSION}
