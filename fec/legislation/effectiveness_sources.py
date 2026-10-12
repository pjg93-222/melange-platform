"""Read-only adapters for frozen Melange acceptance, populations and publications."""
from __future__ import annotations
from collections import Counter, defaultdict
import csv
from datetime import date, datetime
import json
from pathlib import Path
import re

from legislation.build_bill_impact import canonical_url, clean_text


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def read_rows(path):
    path = Path(path)
    if not path.exists(): return []
    if path.suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle: return list(csv.DictReader(handle))
    with path.open(encoding="utf-8-sig") as handle: return [json.loads(line) for line in handle if line.strip()]


def measure_id(label, congress):
    value = str(label).strip()
    if re.fullmatch(r"\d+:[a-z]+:[^:]+", value, re.I):
        term, kind, number = value.split(":", 2)
        if int(term) != int(congress): return None
        return f"{int(term)}:{kind.lower()}:{int(number) if number.isdigit() else number}"
    normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
    match = re.fullmatch(r"(HJRES|SJRES|HCONRES|SCONRES|HRES|SRES|HR|S|HAMDT|SAMDT)(\d+)", normalized)
    if match: return f"{int(congress)}:{match[1].lower()}:{int(match[2])}"
    if value.upper().startswith("LTR-"): return f"{int(congress)}:letter:{value.upper()}"
    return None


def read_service_spine(paths):
    """Read just scalar ID/name/dated terms from the frozen congress-legislators YAML.

    This deliberately restricted reader rejects malformed service dates. It does
    not interpret arbitrary YAML or execute tags and never uses names as IDs.
    """
    people = {}
    for path in paths:
        for block in re.split(r"(?m)^- id:\s*$", Path(path).read_text(encoding="utf-8"))[1:]:
            match = re.search(r"(?m)^    bioguide: ['\"]?([A-Z]\d{6})", block)
            if not match: continue
            bio = match[1]
            name_block = block.split("  name:\n", 1)[-1].split("  bio:", 1)[0]
            fields = dict(re.findall(r"(?m)^    (first|last|official_full): (.+)$", name_block))
            name = fields.get("official_full") or " ".join(fields.get(k, "") for k in ("first", "last"))
            terms_text = block.split("  terms:\n", 1)
            if len(terms_text) < 2: continue
            terms = []
            for term in re.split(r"(?m)^  - type: ", terms_text[1])[1:]:
                values = dict(re.findall(r"(?m)^    (start|end|state|party|district): (.+)$", term))
                values = {k: v.strip("'\"") for k,v in values.items()}
                values["type"] = term.splitlines()[0].strip()
                if not values.get("start") or not values.get("end"): raise ValueError("Undated term " + bio)
                for key in ("start", "end"): date.fromisoformat(values[key])
                if values["end"] <= values["start"]: raise ValueError("Nonpositive service term " + bio)
                terms.append(values)
            target = people.setdefault(bio, {"name": name.strip("'\""), "terms": []})
            for term in terms:
                if term not in target["terms"]: target["terms"].append(term)
    return people


def historical_roster(root, congress, as_of, *, service_paths=()):
    root = Path(root)
    source = root / f"votes/HS{congress}_members.csv"
    raw = read_rows(source)
    start = f"{1789+2*(congress-1)}-01-03"
    end = f"{1789+2*congress}-01-03"
    date.fromisoformat(as_of)
    cutoff = min(as_of, end)
    if cutoff < start:
        raise ValueError("as_of precedes the requested Congress")
    spine = read_service_spine(service_paths)
    grouped = defaultdict(list); exclusions = Counter()
    for row in raw:
        if row.get("chamber") not in {"House", "Senate"}: exclusions["non_chamber"] += 1; continue
        if not re.fullmatch(r"[A-Z]\d{6}", row.get("bioguide_id", "")): exclusions["missing_bioguide"] += 1; continue
        grouped[(row["bioguide_id"], row["chamber"])].append(row)
    added = 0
    for bio, person in spine.items():
        for term in person["terms"]:
            if term["type"] not in {"rep", "sen"} or not term["start"] < end or not term["start"] <= cutoff or not term["end"] > start: continue
            chamber = "House" if term["type"] == "rep" else "Senate"
            if (bio,chamber) not in grouped:
                grouped[(bio,chamber)] = []; added += 1
    rows = []; contexts = []
    for (bio,chamber), records in sorted(grouped.items()):
        person = spine.get(bio, {})
        service = [t for t in person.get("terms", []) if t["type"] == ("rep" if chamber == "House" else "sen")
                   and t["start"] < end and t["start"] <= cutoff and t["end"] > start]
        if person and not service:
            exclusions["no_service_by_cutoff"] += 1; continue
        parties = sorted({t["party"] for t in service if t.get("party")})
        if not parties: parties = sorted({{"100":"Democrat","200":"Republican"}.get(r.get("party_code"), "Independent/other") for r in records})
        name = person.get("name") or records[0].get("bioname")
        row = {"bioguide_id": bio, "bioguide": bio, "congress": congress, "chamber": chamber, "name": name,
               "service_start": min((t["start"] for t in service), default=None), "service_end": max((t["end"] for t in service), default=None),
               "service_periods": sorted({(t["start"],t["end"]) for t in service}),
               "party_history": parties, "source": str(source), "raw_roster_rows": len(records),
               "population_convention": "distinct_bioguide_per_chamber_term_including_replacements_and_delegates",
               "coverage_status": "dated_service_reconciled" if service else "voteview_member_term_only"}
        row["service_periods"] = [{"start":a,"end":b} for a,b in row["service_periods"]]
        rows.append(row)
        majority_party = {(118,"House"):"Republican", (118,"Senate"):"Democrat",
                          (119,"House"):"Republican", (119,"Senate"):"Republican"}.get((congress,chamber))
        context = {"bioguide_id":bio, "bioguide":bio, "congress":congress, "chamber":chamber,
                   "party_history": parties, "majority_status": "unavailable_for_term" if majority_party is None else "majority" if parties == [majority_party] else "minority" if len(parties)==1 and parties[0] in {"Democrat","Republican"} else "unknown_caucus_or_party_change",
                   "majority_basis": "118 House Republican/Senate Democratic; 119 both Republican, party != caucus for independents",
                   "service_start":row["service_start"], "service_end":row["service_end"], "service_periods":row["service_periods"],
                   "committee_role_history": "unavailable_unreconciled", "leadership_history": "unavailable_unreconciled",
                   "institutional_adjustment": "none_actual_results_retained"}
        contexts.append(context)
    receipt = {"source":str(source),"raw_rows":len(raw),"normalized_members":len(rows),"by_chamber":dict(Counter(r["chamber"] for r in rows)),
               "exclusions":dict(exclusions),"spine_additions":added,"service_spine_files":[str(p) for p in service_paths],
               "member_convention":"distinct members including replacements and nonvoting delegates, separately per chamber",
               "certification":"local frozen source reconciliation; not recertified against live Congress roster"}
    return rows,contexts,receipt


def accepted_signals(root, bills, congress, as_of):
    """Join accepted selection by Bioguide and canonical bill. Unknowns are receipts."""
    root = Path(root)
    bundle = read_json(root / "member_desk_bundle.json", {"members":[]})
    identities = [{"name":m["name"],"bioguide":(m.get("bio") or {}).get("bioguide"),"chamber":m.get("chamber")}
                  for m in bundle["members"]]
    names = {}
    for identity in identities:
        if identity["name"] in names and names[identity["name"]] != identity["bioguide"]:
            raise ValueError("Conflicting accepted member identity: " + identity["name"])
        if identity["bioguide"]:
            names[identity["name"]] = identity["bioguide"]
    routing = read_json(root / "legislation_routing_audit.json", {"rows":[]})["rows"]
    from engine import primacy_factors as PF
    tags_path = root / "bill_beneficiary_tags.ndjson"
    tags = PF.load_tags(str(tags_path)) if tags_path.exists() else {}
    cells = PF.load_cu_tensor_cells(str(root / "data/lobbying/industry_axes.ndjson"))
    bill_keys = {b["measure_id"] for b in bills if b.get("introduced_date") and b["introduced_date"] <= as_of}
    signals = []; failures = Counter(); exceptions = []; letters = {}
    tensor_membership = {(m["name"], measure_id(a.get("bill"),a.get("congress"))) for m in bundle["members"]
                         for a in m.get("bills",[]) if a.get("congress") and a.get("bill")}
    for r in routing:
        if r.get("disposition") != "selected" or int(r.get("congress") or 0) != congress: continue
        bio = names.get(r["member"]); key = measure_id(r["bill"],congress)
        if not bio or not key: failures["unresolved_identity"] += 1; exceptions.append(r); continue
        if ":letter:" in key:
            letters.setdefault(key,{"measure_id":key,"congress":congress,"measure_type":"letter","title":r.get("title"),
                                   "impact_class":"S","impact_weight":5,"milestones":"not_applicable",
                                   "verification_status":"canonical_input_present_roles_still_require_verification" if key in bill_keys else "original_document_and_dated_signatures_missing","accepted_rows":[]})["accepted_rows"].append(r)
            if key not in bill_keys:
                failures["letter_signature_date_and_document_unverified"] += 1; continue
        if key not in bill_keys: failures["not_in_official_measure_universe"] += 1; exceptions.append(r); continue
        tagkey = f"{r['bill']}|{congress}"
        tag = tags.get(tagkey)
        tensor = None
        if tag and (r["member"],key) in tensor_membership:
            tensor = PF.act_weight(tag, tagkey, cu_cells=cells)
        else: failures["tensor_tag_or_accepted_membership_missing"] += 1
        signals.append({"measure_id":key,"bioguide_id":bio,"category":r["effective_category"],
                        "standard_direction":r.get("sign"),"tensor_cu":tensor,"accepted":True,
                        "advocacy_discount": .5 if key.split(":")[1] in {"hamdt","samdt"} else 1.,
                        "source":"legislation_routing_audit.json","direction_mechanism":r.get("direction_mechanism"),
                        "tensor_basis":"existing accepted beneficiary cell/declared-position scope; confidence validated not multiplied",
                        "annotation_time_basis":"current accepted annotations applied retrospectively to frozen term evidence"})
    topic_weights = read_json(root / "data/engine/live_legislative_pillar.json", {}).get("topic_weights", {})
    return signals,identities,topic_weights,list(letters.values()),{"counts":dict(failures),"exceptions":exceptions,
            "accepted_signals":len(signals),"unique_measures":len({s["measure_id"] for s in signals}),
            "legacy_selection_scope":"current accepted CU selection; historical nonselected members have unknown CU coverage"}


def _publication_date(value):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:return datetime.strptime(str(value).strip()[:10] if fmt=="%Y-%m-%d" else str(value).strip(),fmt).date().isoformat()
        except ValueError:pass
    return None


def publications_from_local(root, draft_root, bills):
    root,draft_root = Path(root),Path(draft_root)
    tones = read_rows(draft_root/"cu_press_stance_tone.csv") + read_rows(root/"data/stance/press_refresh_tone.csv")
    texts = read_rows(root/"press_text_cache.jsonl") + read_rows(root/"data/stance/press_text_cache_priority.jsonl") + read_rows(root/"data/stance/press_refresh_fulltext.jsonl")
    by_url = defaultdict(list)
    for row in texts: by_url[canonical_url(row.get("url"))].append(row)
    bill_map = {b["measure_id"]:b for b in bills}
    number_index = defaultdict(list)
    for bill in bills: number_index[(bill["measure_type"],int(bill["number"]))].append(bill)
    documents = []
    citation = re.compile(r"\b(H\.?\s*R\.?|S\.?|H\.?\s*J\.?\s*Res\.?|S\.?\s*J\.?\s*Res\.?)\s*(\d{1,5})\b", re.I)
    for row in tones:
        url = canonical_url(row.get("url")); host = urlsplit_host(url)
        if host.endswith(("house.gov","senate.gov")) or host in {"x.com","twitter.com"}: continue
        if host in {"americascreditunions.org","cuna.org","nafcu.org"}:
            org,kind,sector = "americas_credit_unions","trade_association","credit_union"
        elif host.endswith("cutimes.com"):
            org,kind,sector = "cu_times","trade_journalism","credit_union"
        else: org,kind,sector = host,"unknown",None
        candidates = by_url.get(url) or [{}]
        for source in candidates:
            text = clean_text(source.get("text")); combined = clean_text(str(row.get("title") or "") + " " + text)
            pub = _publication_date(row.get("date"))
            links = []
            for c,typ,num in re.findall(r"congress\.gov/bill/(\d+)(?:st|nd|rd|th)-congress/(house-bill|senate-bill|house-joint-resolution|senate-joint-resolution)/(\d+)",combined):
                key=f"{c}:{ {'house-bill':'hr','senate-bill':'s','house-joint-resolution':'hjres','senate-joint-resolution':'sjres'}[typ]}:{int(num)}"
                if key in bill_map:links.append({"measure_id":key,"verified":True,"basis":"explicit_congress_qualified_official_url"})
            normalized = re.sub(r"[^a-z0-9]+"," ",combined.casefold()).strip()
            seen = Counter((re.sub(r"[^a-z]","",m[0].casefold()),int(m[1])) for m in citation.findall(combined))
            for (typ,num),occurrences in seen.items():
                possible=[]
                for bill in number_index.get((typ,num),[]):
                    title=re.sub(r"[^a-z0-9]+"," ",bill["title"].casefold()).strip()
                    if len(title)>18 and title in normalized and pub and bill["introduced_date"]<=pub:possible.append(bill)
                if len(possible)==1:
                    links.append({"measure_id":possible[0]["measure_id"],"verified":True,"basis":"explicit_bill_number_and_exact_unique_official_title","literal_occurrences":occurrences})
                else:
                    links.append({"citation":f"{typ} {num}","verified":False,"basis":"unresolved_congress_or_title","literal_occurrences":occurrences})
            documents.append({"url":url,"title":row.get("title"),"text":text,"published_date":pub,
                              "organization_family":org,"source_kind":kind,"sector":sector,
                              "full_text_complete":False,"bill_links":links,"source":"existing member excerpt feed",
                              "has_login_marker":bool(re.search(r"log.?in|sign.?in",text,re.I))})
    return documents


def urlsplit_host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).netloc
