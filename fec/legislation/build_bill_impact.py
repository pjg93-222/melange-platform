"""Bill-centred, sentiment-free publication evidence and bounded 1/5/10 impact.

Pure functions; no collectors, production writes, or import-time data access.
The default is an explicitly provisional C/S baseline. SS requires a named,
coverage-qualified candidate policy or a reviewed override, never sentiment,
cosponsor counts, or amendment counts. Documents are counted before members.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

VERSION = "bill-impact-frequency-v1"
WEIGHTS = {"C": 1, "S": 5, "SS": 10}
SECTOR_KINDS = {"credit_union", "trade_association", "trade_journalism"}


@dataclass(frozen=True)
class FrequencyPolicy:
    policy_id: str = "cu_frequency_uncalibrated_v1"
    profile: str = "cs_baseline"
    panel: tuple = ("americas_credit_unions",)
    sector: str = "credit_union"
    min_publications: int | None = None
    min_frequency: float | None = None
    min_exposure_per_organization: int | None = None
    status: str = "shadow_candidate_unselected"

    def __post_init__(self):
        if self.profile not in {"cs_baseline", "sector_frequency", "independent_news"}:
            raise ValueError("Unknown significance profile")
        if len(self.panel) != len(set(self.panel)) or not self.panel:
            raise ValueError("A nonempty fixed unique source panel is required")
        if self.profile == "sector_frequency" and (
            self.min_publications is None or self.min_publications < 1 or
            self.min_frequency is None or not 0 <= self.min_frequency <= 1 or
            self.min_exposure_per_organization is None or self.min_exposure_per_organization < 1
        ):
            raise ValueError("Sector SS requires explicit candidate count, frequency and exposure thresholds")


def clean_text(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def canonical_url(value):
    parts = urlsplit(str(value or "").strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")
             and k.lower() not in {"fbclid", "gclid", "print", "output"}]
    path = re.sub(r"/(?:print|amp)/?$", "", parts.path).rstrip("/") or "/"
    return urlunsplit(("https", parts.netloc.lower().removeprefix("www."), path, urlencode(sorted(query)), ""))


def _valid_day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def publication_registry(documents, *, as_of):
    """Reconcile fragments/variants/reprints without treating missing text as absence.

    Explicit origin_document_id or syndication_group binds PDF/HTML and copies.
    Otherwise canonical URL and identical complete body hashes bind duplicates.
    Distinct partial excerpts are not body-hash deduplicated across different URLs.
    Verified links must carry Congress-qualified IDs and a verification basis.
    """
    cutoff = date.fromisoformat(as_of).isoformat()
    docs = []
    for index, raw in enumerate(documents):
        row = dict(raw)
        row["url"] = canonical_url(row.get("url"))
        row["published_date"] = _valid_day(row.get("published_date"))
        row["available_date"] = _valid_day(row.get("available_date") or row.get("observed_date"))
        row["text"] = clean_text(row.get("text"))
        row["content_hash"] = hashlib.sha256(row["text"].encode()).hexdigest() if row["text"] else None
        row["input_index"] = index
        docs.append(row)
    parent = list(range(len(docs)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    aliases = {}
    for i, row in enumerate(docs):
        # Unavailable versions cannot bridge two older publications or supply
        # their origin, full-body completeness, or bill links to an earlier cut.
        if not row["published_date"] or row["published_date"] > cutoff or (row["available_date"] and row["available_date"] > cutoff):
            continue
        keys = [("url", row["url"])] if urlsplit(row["url"]).netloc else []
        for field in ("origin_document_id", "syndication_group", "variant_group"):
            if row.get(field): keys.append((field, row[field]))
        if row.get("full_text_complete") and row["content_hash"]:
            keys.append(("body", row["content_hash"]))
        for key in keys:
            if key in aliases: parent[find(i)] = find(aliases[key])
            else: aliases[key] = i
    groups = defaultdict(list)
    for i, row in enumerate(docs): groups[find(i)].append(row)
    output = []
    for rows in groups.values():
        rows.sort(key=lambda r: (r.get("is_republication", False), not r.get("full_text_complete"), r["url"], r["input_index"]))
        origin = rows[0]
        dates = {r["published_date"] for r in rows if r["published_date"]}
        published = min(dates) if dates else None
        org = origin.get("origin_organization") or origin.get("organization_family")
        kind = origin.get("origin_kind") or origin.get("source_kind", "unknown")
        exclusion = None
        if published is None: exclusion = "missing_publication_date"
        elif published > cutoff: exclusion = "future_publication"
        elif origin["available_date"] and origin["available_date"] > cutoff: exclusion = "future_observation"
        elif kind in {"congressional_office", "legislator_social", "legislator_communication"}:
            exclusion = "legislator_communication"
        elif not org or kind == "unknown": exclusion = "unverified_origin"
        elif origin.get("is_republication") and not origin.get("origin_document_id"):
            exclusion = "unreconciled_republication"
        links = {}
        candidates = []
        for r in rows:
            # A newer fragment cannot establish an earlier mention.
            if not r["published_date"] or r["published_date"] > cutoff or (r["available_date"] and r["available_date"] > cutoff): continue
            for link in r.get("bill_links", []):
                key = link.get("measure_id", "")
                if re.fullmatch(r"\d+:(?:hr|s|hjres|sjres|hres|sres|hconres|sconres):\d+", key) and link.get("verified") is True and link.get("basis"):
                    if link.get("observed_date") and link["observed_date"] > cutoff: continue
                    mention_date = _valid_day(link.get("mention_date")) or r["published_date"]
                    if mention_date > cutoff: continue
                    old = links.get(key, {})
                    links[key] = dict(link, mention_date=min(mention_date, old.get("mention_date", mention_date)), literal_occurrences=max(int(link.get("literal_occurrences", 1)), int(old.get("literal_occurrences", 0))))
                else: candidates.append(link)
        stable = origin.get("origin_document_id") or origin.get("syndication_group") or min(r["url"] for r in rows)
        if exclusion in {"future_publication", "future_observation", "missing_publication_date"}:
            stable = (stable, "unavailable_version", origin["published_date"], origin["available_date"], origin["content_hash"])
        output.append({
            "schema": VERSION, "document_id": hashlib.sha256(str(stable).encode()).hexdigest(),
            "url": origin["url"], "origin_document_id": origin.get("origin_document_id"),
            "originating_publisher": origin.get("publisher", org), "organization_family": org,
            "source_kind": kind, "sector": origin.get("sector"), "published_date": published,
            "full_text_complete": any(r.get("full_text_complete", False) for r in rows),
            "content_hash": origin["content_hash"], "content_hashes": sorted({r["content_hash"] for r in rows if r["content_hash"]}),
            "title": origin.get("title"), "excerpt": origin["text"][:900],
            "bill_links": sorted(links.values(), key=lambda r: r["measure_id"]), "unverified_bill_links": candidates,
            "syndication_group": origin.get("syndication_group"), "source_urls": sorted({r["url"] for r in rows}),
            "fragment_count": len(rows), "count_eligible": exclusion is None, "exclusion_reason": exclusion,
            "coverage_status": "complete_document" if any(r.get("full_text_complete") for r in rows) else "accessible_excerpt_only",
        })
    return sorted(output, key=lambda r: (r["published_date"] or "", r["document_id"]))


def frequency_for_bill(bill, registry, policy, *, as_of, coverage=(), days=None):
    intro = date.fromisoformat(bill["introduced_date"])
    end = date.fromisoformat(as_of)
    if days is not None: end = min(end, intro + timedelta(days=days))
    start_s, end_s = intro.isoformat(), end.isoformat()
    panel = policy.panel
    exposures = Counter(); mentions = Counter(); literal = 0; documents = []
    for row in registry:
        pub = row.get("published_date")
        if not row["count_eligible"] or not pub or not start_s <= pub <= end_s: continue
        if row.get("source_kind") not in SECTOR_KINDS or row.get("sector") != policy.sector: continue
        org = row["organization_family"]
        if org not in panel: continue
        exposures[org] += 1
        links = [l for l in row["bill_links"] if l["measure_id"] == bill["measure_id"]
                 and start_s <= l.get("mention_date", pub) <= end_s]
        if links:
            mentions[org] += 1; documents.append(row["document_id"])
            literal += max(int(l.get("literal_occurrences", 1)) for l in links)
    covered = {}
    minimum = policy.min_exposure_per_organization or 1
    for org in panel:
        # Only a reconciled complete publication inventory certifies zero / F.
        spans = [c for c in coverage if c.get("organization_family") == org and c.get("inventory_complete") is True
                 and c.get("start_date", "9999") <= start_s and c.get("end_date", "0000") >= end_s
                 and c.get("bill_reference_search_complete") is True]
        covered[org] = bool(spans) and exposures[org] >= minimum
    available = all(covered.values())
    observed = [mentions[o]/exposures[o] for o in panel if exposures[o]]
    return {"window_days": days, "start_date": start_s, "end_date": end_s,
            "matched_age_window_complete": days is None or date.fromisoformat(as_of) >= intro + timedelta(days=days),
            "M": sum(mentions.values()), "U": sum(v > 0 for v in mentions.values()), "literal_occurrences": literal,
            "mentions_by_organization": dict(mentions), "exposure_by_organization": {o: exposures[o] for o in panel},
            "panel": list(panel), "coverage_by_organization": covered,
            "F": sum(mentions[o]/exposures[o] for o in panel)/len(panel) if available else None,
            "observed_source_F": sum(observed)/len(observed) if observed else None,
            "observed_source_profile": "incomplete_inventory_lower_bound_mentions",
            "coverage_status": "covered" if available else "unavailable_or_incomplete_panel",
            "zero_interpretation": "observed_zero" if available and not mentions else "not_evidence_of_absence",
            "document_ids": sorted(documents)}


def classify_content(bill):
    if bill.get("measure_type") == "letter": return "S", "congressional_letter_substantive", False
    title = clean_text(bill.get("title")).casefold()
    summary = clean_text(bill.get("summary")).casefold()
    # Narrow naming pattern. Mixed operative provisions are reviewed as S pending adjudication.
    naming = bool(re.search(r"\b(?:to (?:designate|rename|name)|designating|renaming)\b.*\b(?:post office|postal service|building|courthouse|facility|bridge|highway)\b.*\b(?:as the|known as|in honor)\b", title))
    naming = naming or bool(re.search(r"\b(?:to award|providing for the award of|to provide for the award of)\b.*\bcongressional gold medal\b|^(?:honoring|commemorating) the (?:life|service|anniversary)\b", title))
    mixed = bool(re.search(r"\b(?:appropriat(?:e|ion|ions)|grant program|eligibility|regulat(?:e|ion|ions)|amend.*(?:code|act)|tax credit|establish.*program)\b", title + " " + summary))
    if naming and not mixed and not ("and for other purposes" in title and not summary):
        return "C", "narrow_title_summary_commemoration_rule", not bool(summary)
    return "S", "policy_substantive_default_pending_full_text_review", naming or not bool(summary)


def build_impacts(bills, registry=(), *, as_of, policy=None, coverage=(), reviews=()):
    policy = policy or FrequencyPolicy()
    reviews_by_id = {r["measure_id"]: r for r in sorted(reviews, key=lambda r: r.get("reviewed_date", "9999")) if r.get("reviewed_date", "9999") <= as_of}
    out = []
    for bill in bills:
        if not bill.get("introduced_date") or bill["introduced_date"] > as_of: continue
        cls, basis, needs_review = classify_content(bill)
        baseline_class = cls
        freq = frequency_for_bill(bill, registry, policy, as_of=as_of, coverage=coverage)
        independent = [r["document_id"] for r in registry if r["count_eligible"]
                       and r["source_kind"] == "independent_news" and r["organization_family"] in policy.panel
                       and bill["introduced_date"] <= r["published_date"] <= as_of
                       and any(l["measure_id"] == bill["measure_id"] and bill["introduced_date"] <= l.get("mention_date", r["published_date"]) <= as_of for l in r["bill_links"])]
        sector_ss = (policy.profile == "sector_frequency" and freq["F"] is not None
                     and freq["M"] >= policy.min_publications and freq["F"] >= policy.min_frequency)
        if cls == "S" and ((policy.profile == "independent_news" and independent) or sector_ss):
            cls = "SS"; basis = policy.profile + "_candidate_threshold"
        review = reviews_by_id.get(bill["measure_id"])
        if review:
            if review.get("class") not in WEIGHTS or not review.get("evidence") or not review.get("reviewer"):
                raise ValueError("Impact review requires class, reviewer and evidence")
            cls = review["class"]; basis = "reviewed_content_and_profile"; needs_review = False
        if bill.get("measure_type") == "letter": cls = "S"
        out.append({"schema": VERSION, "measure_id": bill["measure_id"], "class": cls, "weight": WEIGHTS[cls],
                    "baseline_class": baseline_class, "basis": basis, "coding_version": VERSION,
                    "policy": asdict(policy), "as_of": as_of, "classification_time_basis": "retrospective_to_cutoff",
                    "sector_significance": freq, "global_significance": {"profile": "independent_news", "qualifying_document_ids": independent,
                        "coverage_status": "positive_verified" if independent else "unavailable_no_reconciled_panel"},
                    "matched_age_windows": {str(days): frequency_for_bill(bill, registry, policy, as_of=as_of, coverage=coverage, days=days) for days in (30,90,180)},
                    "candidate_features": bill.get("candidate_features", {}), "candidate_features_affect_weight": False,
                    "operative_text_evidence": bill.get("operative_text_evidence", "title_and_available_summary_only"),
                    "coverage_status": "provisional_cs_only" if policy.profile == "cs_baseline" else freq["coverage_status"],
                    "needs_content_review": needs_review, "review_history": [review] if review else []})
    return out
