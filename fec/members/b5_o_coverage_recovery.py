# -*- coding: utf-8 -*-
"""Build the locked O coverage-recovery criterion without panel access."""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import members.b5_onc_new_criteria as base

SCHEMA = "b5-o-coverage-recovery-v1"
OUTPUT_BASENAME = "big5_o_coverage_recovery.json.candidate"
STOPPED_CRITERION_SHA = "eb16cde3c160f7fc14280a3c6b0ae4754a913606fcd2e78fa7a4fd1c8dea8e12"

O_MIN_PACKAGES = 5
O_MIN_TOKENS = 1000
O_MIN_DOMAIN_HITS = 25
O_MIN_ACTIVE_DOMAINS = 4

EXPECTED_INPUTS = {
    "social_media.db": "a5c0b191f94fa721f752bf9d89d63af56f79aab7a70629347a69719e8681c93b",
    "data/engine/congestion.json": "ddc9d746600dbdfc60b76c54537662e19c758cbfb9d7a478a71b7bf90cd727a6",
    os.path.join("fec", "legislators.csv"):
        "c5a6cbdbaae0bb19faa89852c29922809ececc70b97c149b4709588087e5e391",
}


def source_eligible(row):
    return bool(
        int(row.get("package_n", 0)) >= O_MIN_PACKAGES
        and int(row.get("token_n", 0)) >= O_MIN_TOKENS
        and int(row.get("domain_hits", 0)) >= O_MIN_DOMAIN_HITS
        and int(row.get("active_domains", 0)) >= O_MIN_ACTIVE_DOMAINS
    )


def evidence_factor(package_n, token_n, domain_hits):
    factors = (
        min(1.0, float(package_n) / 10.0),
        min(1.0, float(token_n) / 2000.0),
        min(1.0, float(domain_hits) / 50.0),
    )
    if min(factors) < 0:
        raise ValueError("evidence counts cannot be negative")
    return float(math.sqrt(factors[0] * factors[1] * factors[2]))


def paired_names(left, right):
    return sorted(set(left) & set(right))


def _ge(value, floor):
    return value is not None and math.isfinite(float(value)) and float(value) >= floor


def classify_readiness(coverage, reliability):
    primary_bars = {
        "coverage_total_gte_250": int(coverage.get("total", 0)) >= 250,
        "coverage_house_gte_150": int(coverage.get("House", 0)) >= 150,
        "coverage_senate_gte_30": int(coverage.get("Senate", 0)) >= 30,
        "source_r_gte_0_10": _ge(reliability.get("source_r"), .10),
        "spearman_brown_gte_0_18": _ge(reliability.get("spearman_brown"), .18),
        "added_n_gte_22": int(reliability.get("added_n", 0)) >= 22,
        "added_source_r_gte_0_05": _ge(reliability.get("added_source_r"), .05),
        "core_n_gte_190": int(reliability.get("core_n", 0)) >= 190,
        "core_r_gte_0_90": _ge(reliability.get("core_r"), .90),
    }
    guarded_bars = {
        "coverage_total_220_to_249": 220 <= int(coverage.get("total", 0)) <= 249,
        "coverage_house_gte_135": int(coverage.get("House", 0)) >= 135,
        "coverage_senate_gte_30": int(coverage.get("Senate", 0)) >= 30,
        "source_r_gte_0_15": _ge(reliability.get("source_r"), .15),
        "spearman_brown_gte_0_25": _ge(reliability.get("spearman_brown"), .25),
        "added_n_gte_22": int(reliability.get("added_n", 0)) >= 22,
        "added_source_r_gte_0_10": _ge(reliability.get("added_source_r"), .10),
        "core_n_gte_190": int(reliability.get("core_n", 0)) >= 190,
        "core_r_gte_0_95": _ge(reliability.get("core_r"), .95),
    }
    if all(primary_bars.values()):
        path = "PRIMARY_250"
    elif all(guarded_bars.values()):
        path = "GUARDED_220"
    else:
        path = "STOP"
    return {
        "path": path,
        "ready_for_look": path != "STOP",
        "status": ("CRITERION_READY_FOR_ONE_LOOK" if path != "STOP"
                   else "CRITERION_STOP_BAR_MISS"),
        "primary_bars": primary_bars,
        "guarded_bars": guarded_bars,
    }


def _verify_inputs(data_root, stopped_path, lexicon_path):
    observed = {}
    for relative, expected in EXPECTED_INPUTS.items():
        path = os.path.join(data_root, relative)
        actual = base.sha256_file(path)
        if actual != expected:
            raise ValueError("input fingerprint mismatch for %s: %s != %s" %
                             (relative, actual, expected))
        observed[relative.replace("\\", "/")] = actual
    stopped_sha = base.sha256_file(stopped_path)
    if stopped_sha != STOPPED_CRITERION_SHA:
        raise ValueError("stopped criterion fingerprint mismatch")
    observed["stopped_criterion"] = stopped_sha
    observed["data/members/b5_onc_policy_domains.json"] = base.sha256_file(lexicon_path)
    return observed


def _collect_raw(connection, roster, domains):
    raw = {"INTERVIEW": {}, "CREC": {}}
    query = """
        select member, source, coalesce(package,''), group_concat(coalesce(text,''),' ')
        from speeches where source in ('INTERVIEW','CREC')
        group by member, source, package order by member, source, package
    """
    current = None
    rows = []

    def finish(key, package_rows):
        if key is None or key[0] not in roster:
            return
        documents = base.select_documents(key[0], key[1], package_rows)
        measured = base.measure_o_source(documents, domains, enforce_floors=False)
        if measured and source_eligible(measured):
            measured["evidence_factor"] = evidence_factor(
                measured["package_n"], measured["token_n"], measured["domain_hits"])
            raw[key[1]][key[0]] = measured

    for member, source, package, text in connection.execute(query):
        key = (member, source)
        if current is not None and key != current:
            finish(current, rows)
            rows = []
        current = key
        rows.append((package, text))
    finish(current, rows)
    return raw


def _source_scores(raw, chamber):
    scores = {}
    for source in ("INTERVIEW", "CREC"):
        entropy = base._zscore_by_chamber(
            {name: row["domain_entropy"] for name, row in raw[source].items()}, chamber)
        mattr = base._zscore_by_chamber(
            {name: row["mattr_100"] for name, row in raw[source].items()}, chamber)
        names = paired_names(entropy, mattr)
        scores[source] = {
            name: (.70 * entropy[name] + .30 * mattr[name])
                  * raw[source][name]["evidence_factor"]
            for name in names
        }
    return scores


def _load_stopped_o(stopped_path):
    with open(stopped_path, encoding="utf-8") as handle:
        document = json.load(handle)
    return {name: float(row["O"]) for name, row in (document.get("members") or {}).items()
            if isinstance(row, dict) and row.get("O") is not None}


def _coverage(values, chamber):
    return {
        "total": len(values),
        "House": sum(chamber.get(name) == "House" for name in values),
        "Senate": sum(chamber.get(name) == "Senate" for name in values),
    }


def build(data_root, lexicon_path, stopped_path):
    data_root = os.path.abspath(data_root)
    lexicon_path = os.path.abspath(lexicon_path)
    stopped_path = os.path.abspath(stopped_path)
    inputs = _verify_inputs(data_root, stopped_path, lexicon_path)
    with open(lexicon_path, encoding="utf-8") as handle:
        domains = json.load(handle)
    if list(domains) != sorted(domains) or len(domains) != 18:
        raise ValueError("policy-domain lexicon must contain exactly 18 sorted domains")

    roster, chamber, female, republican, senate = base._load_roster(data_root)
    database_path = os.path.join(data_root, "social_media.db")
    connection = sqlite3.connect("file:" + database_path + "?mode=ro", uri=True)
    try:
        raw = _collect_raw(connection, roster, domains)
    finally:
        connection.close()

    source_scores = _source_scores(raw, chamber)
    names = paired_names(source_scores["INTERVIEW"], source_scores["CREC"])
    mean_score = {
        name: (source_scores["INTERVIEW"][name] + source_scores["CREC"][name]) / 2.0
        for name in names
    }
    log_tokens = {
        name: math.log1p(raw["INTERVIEW"][name]["token_n"]
                         + raw["CREC"][name]["token_n"])
        for name in names
    }
    log_packages = {
        name: math.log1p(raw["INTERVIEW"][name]["package_n"]
                         + raw["CREC"][name]["package_n"])
        for name in names
    }
    residual = base.residualize_map(
        mean_score, [female, republican, senate, log_tokens, log_packages])
    values = base._zscore_map(residual)

    stopped = _load_stopped_o(stopped_path)
    source_r, source_n = base._corr(
        source_scores["INTERVIEW"], source_scores["CREC"])
    added_names = sorted(set(values) - set(stopped))
    added_r, added_n = base._corr(
        {name: source_scores["INTERVIEW"][name] for name in added_names},
        {name: source_scores["CREC"][name] for name in added_names})
    core_r, core_n = base._corr(stopped, values)
    reliability = {
        "source_r": source_r,
        "source_n": source_n,
        "spearman_brown": base._spearman_brown(source_r),
        "added_source_r": added_r,
        "added_n": added_n,
        "core_r": core_r,
        "core_n": core_n,
    }
    coverage = _coverage(values, chamber)
    readiness = classify_readiness(coverage, reliability)
    components = {}
    for name in values:
        components[name] = {
            "interview_score": source_scores["INTERVIEW"][name],
            "crec_score": source_scores["CREC"][name],
            "interview_evidence_factor": raw["INTERVIEW"][name]["evidence_factor"],
            "crec_evidence_factor": raw["CREC"][name]["evidence_factor"],
            "interview_metrics": raw["INTERVIEW"][name],
            "crec_metrics": raw["CREC"][name],
            "recovered_member": name not in stopped,
        }
    return {
        "schema": SCHEMA,
        "status": readiness["status"],
        "production_consumers": 0,
        "stopped_criterion_sha256": STOPPED_CRITERION_SHA,
        "inputs_sha256": inputs,
        "coverage": coverage,
        "reliability": reliability,
        "readiness": readiness,
        "members": {name: {"O": float(values[name])} for name in sorted(values)},
        "components": components,
        "method": {
            "sources": ["INTERVIEW", "CREC"],
            "both_sources_required": True,
            "source_floors": {"packages": O_MIN_PACKAGES, "tokens": O_MIN_TOKENS,
                              "domain_hits": O_MIN_DOMAIN_HITS,
                              "active_domains": O_MIN_ACTIVE_DOMAINS},
            "evidence_factor":
                "sqrt(min(1,p/10)*min(1,t/2000)*min(1,h/50))",
            "source_formula": ".70*entropy_z + .30*mattr_z, then evidence factor",
            "outcome_panel_access": False,
        },
    }


def main(argv=None):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser = argparse.ArgumentParser(description="Build locked O coverage recovery")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--lexicon", default=os.path.join(
        root, "data", "members", "b5_onc_policy_domains.json"))
    parser.add_argument("--stopped", default=os.path.join(
        root, "data", "members", "big5_onc_new_criteria.json.candidate"))
    parser.add_argument("--out", default=os.path.join(
        root, "data", "members", OUTPUT_BASENAME))
    args = parser.parse_args(argv)
    if not str(args.out).endswith(".candidate"):
        raise SystemExit("REFUSED: criterion output must end in .candidate")
    document = build(args.data_root, args.lexicon, args.stopped)
    from moneyball_core import dump_atomic
    dump_atomic(document, args.out, sort_keys=True, indent=1)
    print("O recovery: %s path=%s" %
          (document["status"], document["readiness"]["path"]))
    print("  coverage=%s" % document["coverage"])
    print("  reliability=%s" % document["reliability"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
