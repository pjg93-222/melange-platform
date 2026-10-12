"""Verify a compiled local shadow generation without parsing BILLSTATUS again.

Checks frozen hashes, chamber populations, independent denominator arithmetic,
and every explanatory contribution against canonical dates, roles and signals.
The audit is read-only. Code drift is an error by default; a development-only
flag can label that drift explicitly while auditing an earlier generation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path

STAGES = ("BILL", "AIC", "ABC", "PASS", "LAW")
LAW_TYPES = {"hr", "s", "hjres", "sjres"}
BASE_ROLES = {"standard": {"sponsor": 3., "original": 2., "cosponsor": 1.},
              "tensor": {"sponsor": 1., "original": .5, "cosponsor": .25}}


class ShadowVerificationError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("; ".join(report["errors"][:10]))


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _rows(path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _equal(a, b):
    if a is None or b is None:
        return a is b
    return isinstance(a, (int,float)) and isinstance(b, (int,float)) and math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-10)


def _iso(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _cell(rows, field):
    values = set()
    for row in rows:
        if row.get(field) is None:
            continue
        try:
            number = float(row[field])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or not -1 <= number <= 1:
            return None
        values.add(number)
    return next(iter(values)) if len(values) == 1 else None


def _valid_role(row, bill, cutoff):
    kind = row.get("role")
    joined, introduced, withdrawn = _iso(row.get("joined_date")), _iso(bill.get("introduced_date")), _iso(row.get("withdrawn_date"))
    if kind not in BASE_ROLES["standard"] or not joined or not introduced or joined > cutoff or joined < introduced:
        return False
    if withdrawn and withdrawn < joined:
        return False
    if kind in {"sponsor","original"} and joined != introduced:
        return False
    status = str(row.get("chronology_status", "")).lower()
    return not any(word in status for word in ("unknown","invalid","inconsistent","quarantine","conflict"))


def _active(row, day):
    return row["joined_date"] <= day and (not row.get("withdrawn_date") or day < row["withdrawn_date"])


def verify_generation(generation_dir, *, check_source_hashes=True, allow_code_drift=False):
    """Return a compact audit receipt, raising ShadowVerificationError on errors.

    This verifies internal consistency and local file integrity, not independent
    certification of Congressional coverage or cryptographic source authenticity.
    """
    root = Path(generation_dir).resolve()
    report = {"schema":"effectiveness-shadow-verification-v1", "generation":str(root),
              "status":"checking", "errors":[], "warnings":[], "checks":0,
              "hashes_checked":{"outputs":0,"inputs":0,"code":0,"archives":0,"manifest_artifacts":0},
              "terms":[]}

    def check(condition, label):
        report["checks"] += 1
        if not condition and len(report["errors"]) < 100:
            report["errors"].append(label)

    receipt_path = root / "shadow_generation.json"
    check(receipt_path.is_file(), "missing generation receipt")
    if not receipt_path.is_file():
        report["status"] = "failed"
        raise ShadowVerificationError(report)
    generation = _read(receipt_path)
    check(generation.get("schema") == "effectiveness-shadow-generation-v1", "unsupported generation schema")
    check(generation.get("status") == "complete", "generation is not complete")
    check(generation.get("production_activation") is False, "generation is not explicitly shadow-only")
    check(generation.get("production_hashes_unchanged") is True, "production preservation not attested")
    cutoff = _iso(generation.get("as_of"))
    check(cutoff is not None, "invalid generation as_of")
    if cutoff is None:
        report["status"] = "failed"
        raise ShadowVerificationError(report)
    digest_cache = {}

    def validate_hash(path, expected, kind, code=False):
        target = Path(path).resolve()
        report["hashes_checked"][kind] += 1
        if not target.is_file():
            check(False, f"missing {kind} file: {target}")
            return
        actual = digest_cache.setdefault(str(target), _hash(target)) if str(target) not in digest_cache else digest_cache[str(target)]
        if actual != expected and code and allow_code_drift:
            report["warnings"].append(f"code drift allowed for development audit: {target}")
        else:
            check(actual == expected, f"{kind} hash mismatch: {target}")

    outputs = generation.get("outputs_sha256", {})
    for field,kind in (("outputs_sha256","outputs"),("inputs_sha256","inputs"),("code_sha256","code")):
        table = generation.get(field, {})
        check(bool(table), f"empty {field}")
        for path,expected in table.items():
            if kind == "outputs":
                target = Path(path).resolve()
                check(root in target.parents, f"output receipt escapes generation directory: {target}")
            if kind != "inputs" or check_source_hashes:
                validate_hash(path, expected, kind, code=kind == "code")
    if not check_source_hashes:
        report["warnings"].append("source and archive hashes deliberately not checked")

    terms = generation.get("terms", [])
    check(bool(terms), "generation has no Congress entries")
    for generation_term in terms:
        congress = int(generation_term["congress"])
        folder = root / str(congress)
        evidence, engine = folder / "data/legislation", folder / "data/engine"
        required = [evidence/name for name in ("effectiveness_universe_manifest.json", "bill_milestones.ndjson",
                                               "bill_impact.ndjson", "member_measure_roles.ndjson", "member_population.ndjson",
                                               "accepted_signals.ndjson")]
        required.append(engine / "legislative_effectiveness_v1.json")
        for path in required:
            check(path.is_file(), f"missing required artifact: {path}")
            check(str(path.resolve()) in outputs, f"required artifact absent from output hashes: {path}")
        if not all(path.is_file() for path in required):
            continue
        manifest = _read(required[0])
        score = _read(required[-1])
        check(score.get("schema_version") == "legislative_effectiveness_v1", f"{congress}: invalid score schema")
        check(score.get("as_of") == cutoff == manifest.get("as_of"), f"{congress}: inconsistent cutoff")
        check(score.get("evidence_manifest_sha256") == _hash(required[0]), f"{congress}: score/manifest hash mismatch")
        check(score.get("policy", {}).get("policy_status") == "shadow_candidate", f"{congress}: policy is not shadow_candidate")
        if congress == 119:
            check(score.get("label") == "current_term_provisional", "119: missing current-term provisional label")
        for name,entry in manifest.get("artifacts", {}).items():
            target = (evidence / name).resolve()
            check(target.parent == evidence.resolve(), f"{congress}: manifest artifact escapes evidence directory")
            validate_hash(target, entry["sha256"], "manifest_artifacts")
        if check_source_hashes:
            for archive in manifest.get("source_archives", []):
                validate_hash(archive["path"], archive["sha256"], "archives")
        bill_rows, impact_rows = _rows(required[1]), _rows(required[2])
        bills, impacts = {r["measure_id"]:r for r in bill_rows}, {r["measure_id"]:r for r in impact_rows}
        check(len(bills) == len(bill_rows), f"{congress}: duplicate canonical bills")
        check(len(impacts) == len(impact_rows), f"{congress}: duplicate impact rows")
        roster = _rows(required[4])
        populations = defaultdict(set)
        for row in roster:
            start = _iso(row.get("service_start") or row.get("start_date"))
            if not start or start <= cutoff:
                populations[(int(row["congress"]),row["chamber"])].add(row["bioguide_id"])
        member_map = {(int(r["congress"]),r["chamber"],r["bioguide_id"]):r for r in score["members"]}
        check(len(member_map) == len(score["members"]), f"{congress}: duplicate score member/chamber")
        computed = {key:dict.fromkeys(STAGES,0.) for key in populations}
        unknown = {key:dict.fromkeys(STAGES,0) for key in populations}
        missing_impact = defaultdict(int)
        reached = {}
        for identity,bill in bills.items():
            intro = _iso(bill.get("introduced_date"))
            key = (int(bill["congress"]),bill["chamber"])
            if key not in computed or not intro or intro > cutoff:
                continue
            if bill.get("eligible") is not True or bill["measure_type"] not in LAW_TYPES:
                continue
            weight = impacts.get(identity,{}).get("weight")
            if weight not in (1,5,10):
                missing_impact[key] += 1
            reached[identity] = {}
            for stage in STAGES:
                milestone = bill.get("milestones",{}).get(stage,{})
                day = _iso(milestone.get("date"))
                state, attained = milestone.get("state","unknown"), milestone.get("reached",day is not None)
                if attained is None or state in {"unknown","not_applicable"} or (attained is not False and day is None):
                    unknown[key][stage] += 1
                elif attained is not False and day <= cutoff:
                    reached[identity][stage] = day
                    if weight in (1,5,10):
                        computed[key][stage] += weight
        for key,population in populations.items():
            term = score["terms"].get(f"{key[0]}:{key[1]}",{})
            check(term.get("population") == len(population), f"{key}: engine population mismatch")
            manifest_n = manifest.get("historical_member_population",{}).get("normalization_N") or {}
            check(manifest_n.get(key[1]) == len(population), f"{key}: manifest population mismatch")
            expected_members = {(*key,bio) for bio in population}
            check(expected_members <= set(member_map), f"{key}: missing scored roster members")
            for stage in STAGES:
                check(_equal(term.get("denominators",{}).get(stage),computed[key][stage]), f"{key}:{stage}: unsigned denominator mismatch")
                check(term.get("coverage",{}).get("unknown_milestones",{}).get(stage) == unknown[key][stage], f"{key}:{stage}: unknown coverage mismatch")
            available = all(computed[key].values()) and not missing_impact[key] and not any(unknown[key].values())
            check(term.get("denominators_available") == bool(available), f"{key}: denominator availability mismatch")
            report["terms"].append({"congress":key[0],"chamber":key[1],"population":len(population),
                                    "denominators":computed[key],"denominators_available":bool(available),
                                    "unknown_milestones":unknown[key]})
        role_index, signal_index = defaultdict(list), defaultdict(list)
        for row in _rows(required[3]):
            identity = row["measure_id"]
            if identity in bills and _valid_role(row,bills[identity],cutoff):
                role_index[(identity,row["bioguide_id"])].append(row)
        for row in _rows(required[5]):
            effective = _iso(row.get("effective_date"))
            if not effective or effective <= cutoff:
                signal_index[row["measure_id"]].append(row)
        reference_sums, result_sums = defaultdict(float), defaultdict(lambda:[0.,0.])
        trace_keys = set()
        for trace in score.get("contributions",[]):
            identity, bio = trace["measure_id"],trace["bioguide_id"]
            if identity not in bills:
                check(False,f"contribution references missing bill: {identity}")
                continue
            bill = bills[identity]
            member_key = (int(bill["congress"]),bill["chamber"],bio)
            key = member_key[:2]
            stage, kind, model = trace["stage"],trace["kind"],trace.get("model")
            label = f"{identity}:{bio}:{kind}:{model}:{stage}"
            trace_key = (identity,bio,kind,model,stage)
            check(trace_key not in trace_keys, label+": duplicate contribution")
            trace_keys.add(trace_key)
            day = _iso(trace.get("date"))
            check(day is not None and day <= cutoff, label+": missing/future contribution date")
            check(member_key in member_map, label+": member absent from chamber population")
            if day is None or key not in computed:
                continue
            weight = 5 if bill["measure_type"] in {"letter","congressional_letter"} else impacts.get(identity,{}).get("weight")
            if kind != "advocacy":
                check(identity in reached, label+": ineligible measure awarded results")
                check(reached.get(identity,{}).get(stage) == day, label+": stage/date absent from official milestones")
                denominator = computed[key].get(stage)
                check(bool(denominator), label+": undefined denominator credited")
                if not denominator or weight not in (1,5,10):
                    continue
                unit = len(populations[key])/5 * weight/denominator
                check(_equal(trace.get("denominator"),denominator), label+": contribution denominator mismatch")
            else:
                check(stage == "ADVOCACY",label+": advocacy has invented bill milestone")
                unit = weight/5 if weight in (1,5,10) else None
            check(_equal(trace.get("unit"),unit),label+": bill-stage unit mismatch")
            check(trace.get("weight" if kind == "sponsor_reference" else "impact_weight") == weight,label+": impact weight mismatch")
            episodes = role_index.get((identity,bio),[])
            if kind == "sponsor_reference":
                check(any(r["role"] == "sponsor" and _active(r,day) for r in episodes),label+": sponsor inactive/prejoin")
                reference_sums[(*member_key,stage)] += trace["unit"]
                continue
            check(model in BASE_ROLES,label+": unknown scoring model")
            joined = _iso(trace.get("joined_date"))
            withdrawn = _iso(trace.get("withdrawn_date"))
            role = trace.get("role")
            matched = [r for r in episodes if r["role"] == role and r["joined_date"] == joined and r.get("withdrawn_date") == withdrawn]
            check(bool(matched),label+": role episode absent/quarantined")
            check(joined is not None and joined <= day,label+": prejoin credit")
            if kind == "results":
                check(not withdrawn or day < withdrawn,label+": credit at/after withdrawal")
            first_join = min((r["joined_date"] for r in episodes if r["role"] == "cosponsor"),default=joined)
            delay = (date.fromisoformat(first_join)-date.fromisoformat(bill["introduced_date"])).days if first_join else None
            timing = 2 ** (-delay/score["policy"]["cosponsor_half_life_days"]) if role == "cosponsor" and delay is not None else 1.
            check(_equal(trace.get("timing_multiplier"),timing),label+": timing multiplier mismatch")
            if model not in BASE_ROLES or role not in BASE_ROLES[model] or unit is None:
                continue
            base = BASE_ROLES[model][role]
            check(_equal(trace.get("base_role_credit"),base),label+": base role mismatch")
            accepted = [r for r in signal_index.get(identity,[]) if not r.get("bioguide_id") or r["bioguide_id"] == bio]
            categories = defaultdict(list)
            for row in accepted:
                categories[str(row.get("category") or "uncategorized")].append(row)
            check(bool(categories),label+": no accepted signal membership")
            cells = {category:{"standard":_cell(rows,"standard_direction"),"tensor":_cell(rows,"tensor_cu")}
                     for category,rows in categories.items()}
            check(trace.get("category_signals") == cells,label+": accepted category/cell mismatch")
            discount = float(bill.get("advocacy_discount",next((r["advocacy_discount"] for r in accepted if r.get("advocacy_discount") is not None),1.))) if kind == "advocacy" else 1.
            check(_equal(trace.get("advocacy_discount"),discount),label+": advocacy discount mismatch")
            scalar = unit * base * timing * discount
            if model == "tensor":
                signal = _cell(accepted,"tensor_cu")
                amounts = [] if signal is None else [scalar*signal]
            else:
                allocations = {category:score["policy"].get("category_weights",{}).get(category,1.)/len(cells) for category in cells}
                signal = sum(values["standard"]*allocations[category] for category,values in cells.items()) \
                    if cells and all(values["standard"] is not None for values in cells.values()) else None
                amounts = [scalar*values["standard"]*allocations[category] for category,values in cells.items() if values["standard"] is not None]
            check(_equal(trace.get("signal"),signal),label+": aggregate signal mismatch")
            check(_equal(trace.get("contribution"),None if signal is None else scalar*signal),label+": contribution arithmetic mismatch")
            sums = result_sums[(*member_key,model,kind)]
            sums[0] += sum(max(v,0.) for v in amounts)
            sums[1] += sum(max(-v,0.) for v in amounts)
        for member_key,member in member_map.items():
            term = score["terms"][f"{member_key[0]}:{member_key[1]}"]
            for stage in STAGES:
                check(_equal(member["sponsor_reference"]["by_stage"][stage],reference_sums[(*member_key,stage)]), f"{member_key}:{stage}: sponsor reference does not reconcile to traces")
            check(_equal(member["sponsor_reference"]["observed_total"],sum(member["sponsor_reference"]["by_stage"].values())),f"{member_key}: sponsor stage sum mismatch")
            if term["reference_status"] != "available":
                check(member["sponsor_reference"].get("total") is None,f"{member_key}: provisional reference presented as full")
            for model in BASE_ROLES:
                for kind in ("results","advocacy"):
                    bucket = member[model][kind]
                    positive,negative = result_sums[(*member_key,model,kind)]
                    check(_equal(bucket["positive"],positive) and _equal(bucket["negative"],negative),f"{member_key}:{model}:{kind}: totals do not reconcile to traces")
                    check(_equal(bucket["observed_net"],positive-negative),f"{member_key}:{model}:{kind}: observed net mismatch")
                    available = bool(bucket.get("known_count") and not bucket.get("unknown_count") and (kind == "advocacy" or term["denominators_available"]))
                    check(bucket.get("available") == available,f"{member_key}:{model}:{kind}: invalid missing-data availability")
                    check(_equal(bucket.get("net"),bucket["observed_net"] if available else None),f"{member_key}:{model}:{kind}: unavailable result fabricated as zero/value")
                    check(bucket.get("distinct_measures") == len(set(bucket.get("measure_ids",[]))),f"{member_key}:{model}:{kind}: distinct evidence count mismatch")
        report.setdefault("contribution_rows",{})[str(congress)] = len(score.get("contributions",[]))
    report["status"] = "failed" if report["errors"] else "passed_with_development_warnings" if report["warnings"] else "passed"
    report["scope"] = "local integrity and compiled arithmetic; official completeness/significance calibration remain separate gates"
    if report["errors"]:
        raise ShadowVerificationError(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generation")
    parser.add_argument("--allow-code-drift",action="store_true",help="Development audit only; report stale code hashes as warnings")
    parser.add_argument("--skip-source-hashes",action="store_true",help="Explicitly partial audit when original inputs are unavailable")
    args = parser.parse_args(argv)
    try:
        report = verify_generation(args.generation,check_source_hashes=not args.skip_source_hashes,allow_code_drift=args.allow_code_drift)
    except ShadowVerificationError as error:
        print(json.dumps(error.report,indent=2))
        return 1
    print(json.dumps(report,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
