# -*- coding: utf-8 -*-
"""Build the locked O/N/C behavioral criteria without outcome-panel access.

The module is intentionally criterion-only. It reads public-record behavioral corpora and emits
`big5_onc_new_criteria.json.candidate`; a separate one-look module owns panel evaluation.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

O_MAX_PACKAGES = 200
O_TOKENS_PER_PACKAGE = 250
O_MIN_PACKAGES = 10
O_MIN_TOKENS = 2000
O_MIN_DOMAIN_HITS = 50
O_MIN_ACTIVE_DOMAINS = 4
N_MIN_TURN_TOKENS = 10
N_MIN_PACKAGES = 4
N_MIN_TURNS = 30
SCHEMA = "b5-onc-new-criteria-v1"
OUTPUT_BASENAME = "big5_onc_new_criteria.json.candidate"

EXPECTED_INPUTS = {
    "social_media.db": "a5c0b191f94fa721f752bf9d89d63af56f79aab7a70629347a69719e8681c93b",
    "behavior_panel.json": "93d695cde6470f500f3aaac7db61d988b9c3c2ddee66360d568f965c97072ceb",
    "data/members/lep_criterion.json": "10bf14924cfcc2f13ad08dd90fa3539561e1b47b39722345f319766a277c61fc",
    "data/engine/congestion.json": "ddc9d746600dbdfc60b76c54537662e19c758cbfb9d7a478a71b7bf90cd727a6",
    os.path.join("fec", "legislators.csv"): "c5a6cbdbaae0bb19faa89852c29922809ececc70b97c149b4709588087e5e391",
}

TOKEN_RE = re.compile(r"[a-z][a-z'-]*")

# Fixed before implementation in the RED contract. These lists measure within-interview change,
# never a member's mean ideological or policy content.
AFFECT_POSITIVE = frozenset({
    "calm", "safe", "good", "progress", "hope", "help", "success", "secure", "stable",
    "confident", "trust", "benefit", "improve", "solution", "agree", "support", "welcome",
    "effective", "strong", "opportunity",
})
AFFECT_NEGATIVE = frozenset({
    "angry", "fear", "crisis", "threat", "failure", "afraid", "anxious", "danger", "harm",
    "worry", "worried", "disaster", "chaos", "attack", "terrible", "frustrated", "risk",
    "unstable", "weak", "problem",
})


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tokenize(text):
    return TOKEN_RE.findall(str(text or "").lower())


def _document_key(member, source, package):
    blob = (str(member) + "\0" + str(source) + "\0" + str(package)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def select_documents(member, source, rows, max_packages=O_MAX_PACKAGES,
                     tokens_per_package=O_TOKENS_PER_PACKAGE):
    """Order-invariant, hash-selected package sample with a fixed token cap."""
    grouped = collections.defaultdict(list)
    for package, text in rows:
        package = str(package or "").strip()
        if package:
            grouped[package].append(str(text or ""))
    selected = []
    for package, fragments in grouped.items():
        tokens = tokenize(" ".join(sorted(fragments)))[:int(tokens_per_package)]
        if tokens:
            selected.append((_document_key(member, source, package), package, tokens))
    selected.sort(key=lambda row: (row[0], row[1]))
    return [(package, tokens) for _, package, tokens in selected[:int(max_packages)]]


def _mattr(tokens, window=100):
    if not tokens:
        return 0.0
    if len(tokens) <= window:
        return len(set(tokens)) / float(len(tokens))
    counts = collections.Counter(tokens[:window])
    total = len(counts) / float(window)
    n_windows = 1
    for index in range(window, len(tokens)):
        old = tokens[index - window]
        counts[old] -= 1
        if counts[old] == 0:
            del counts[old]
        counts[tokens[index]] += 1
        total += len(counts) / float(window)
        n_windows += 1
    return total / float(n_windows)


def measure_o_source(documents, domains, enforce_floors=True):
    tokens = [token for _, document in documents for token in document]
    joined = " " + " ".join(tokens) + " "
    counts = {}
    for domain, terms in sorted(domains.items()):
        hits = 0
        for term in terms:
            normalized = " ".join(tokenize(term))
            if normalized:
                hits += joined.count(" " + normalized + " ")
        counts[domain] = hits
    total_hits = sum(counts.values())
    active = sum(value > 0 for value in counts.values())
    if enforce_floors and not (
        len(documents) >= O_MIN_PACKAGES and len(tokens) >= O_MIN_TOKENS
        and total_hits >= O_MIN_DOMAIN_HITS and active >= O_MIN_ACTIVE_DOMAINS
    ):
        return None
    probabilities = [value / float(total_hits) for value in counts.values() if value > 0]
    entropy = -sum(p * math.log(p) for p in probabilities) / math.log(max(2, len(domains))) \
        if probabilities else 0.0
    return {
        "package_n": len(documents),
        "token_n": len(tokens),
        "domain_hits": total_hits,
        "active_domains": active,
        "domain_counts": counts,
        "domain_entropy": float(entropy),
        "mattr_100": float(_mattr(tokens, 100)),
    }


def _activation(tokens):
    positive = sum(token in AFFECT_POSITIVE for token in tokens)
    negative = sum(token in AFFECT_NEGATIVE for token in tokens)
    return (negative - positive) / math.sqrt(max(1, len(tokens)))


def _package_lability(metrics):
    differences = []
    spikes = []
    for values in metrics.values():
        if len(values) < 2:
            continue
        differences.append(float(np.mean(np.abs(np.diff(values)))))
        spikes.append(float(np.percentile(values, 90) - np.percentile(values, 50)))
    if not differences:
        return None
    return .70 * float(np.median(differences)) + .30 * float(np.median(spikes))


def _half(member, package):
    digest = hashlib.sha256((str(member) + "\0" + str(package)).encode("utf-8")).digest()
    return str(digest[0] & 1)


def measure_n_packages(packages, member="", enforce_floors=True):
    metrics = {}
    token_lengths = []
    activations = []
    minimum_tokens = N_MIN_TURN_TOKENS if enforce_floors else 1
    for package, turns in sorted(packages.items()):
        values = []
        for text in turns:
            tokens = tokenize(text)
            if len(tokens) < minimum_tokens:
                continue
            values.append(_activation(tokens))
            activations.append(values[-1])
            token_lengths.append(len(tokens))
        if len(values) >= 2:
            metrics[str(package)] = values
    turn_n = sum(len(values) for values in metrics.values())
    if enforce_floors and (len(metrics) < N_MIN_PACKAGES or turn_n < N_MIN_TURNS):
        return None
    lability = _package_lability(metrics)
    if lability is None:
        return None
    halves = {}
    for label in ("0", "1"):
        subset = {package: values for package, values in metrics.items()
                  if _half(member, package) == label}
        if not enforce_floors or len(subset) >= 2:
            value = _package_lability(subset)
            if value is not None:
                halves[label] = float(value)
    if enforce_floors and set(halves) != {"0", "1"}:
        return None
    used_activations = [value for values in metrics.values() for value in values]
    return {
        "lability": float(lability),
        "halves": halves,
        "package_n": len(metrics),
        "turn_n": turn_n,
        "mean_activation": float(np.mean(used_activations)),
        "mean_turn_length": float(np.mean(token_lengths)),
    }


def execution_consistency(vote_z, followthrough_z):
    vote_z, followthrough_z = float(vote_z), float(followthrough_z)
    return (vote_z + followthrough_z) / 2.0 - .5 * abs(vote_z - followthrough_z)


def _zscore_map(values):
    names = sorted(values)
    vector = np.asarray([float(values[name]) for name in names])
    sd = float(vector.std(ddof=1)) if len(vector) > 1 else 0.0
    if not math.isfinite(sd) or sd <= 0:
        raise ValueError("cannot z-score a constant or insufficient criterion")
    mean = float(vector.mean())
    return {name: (float(values[name]) - mean) / sd for name in names}


def _zscore_by_chamber(values, chamber):
    out = {}
    for label in ("House", "Senate"):
        subset = {name: value for name, value in values.items() if chamber.get(name) == label}
        if len(subset) >= 5:
            out.update(_zscore_map(subset))
    return out


def residualize_map(values, covariate_maps):
    names = sorted(name for name in values
                   if all(name in covariate for covariate in covariate_maps))
    if len(names) < len(covariate_maps) + 5:
        raise ValueError("insufficient complete rows for nuisance residualization")
    y = np.asarray([float(values[name]) for name in names])
    columns = [np.ones(len(names))]
    columns.extend(np.asarray([float(covariate[name]) for name in names])
                   for covariate in covariate_maps)
    design = np.column_stack(columns)
    if not np.all(np.isfinite(design)) or not np.all(np.isfinite(y)):
        raise ValueError("non-finite nuisance design")
    if np.linalg.matrix_rank(design) != design.shape[1]:
        raise ValueError("nuisance design is rank deficient")
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ beta
    return {name: float(residual[index]) for index, name in enumerate(names)}


def _corr(left, right):
    names = sorted(set(left) & set(right))
    if len(names) < 5:
        return None, len(names)
    a = np.asarray([float(left[name]) for name in names])
    b = np.asarray([float(right[name]) for name in names])
    if a.std() == 0 or b.std() == 0:
        return None, len(names)
    return float(np.corrcoef(a, b)[0, 1]), len(names)


def _spearman_brown(r):
    if r is None or r <= -1:
        return None
    return float(2 * r / (1 + r))


def _load_roster(data_root):
    with open(os.path.join(data_root, "data", "engine", "congestion.json"), encoding="utf-8") as handle:
        rows = json.load(handle)
    by_bioguide = {str(row.get("bioguide") or ""): row["member"] for row in rows}
    chamber = {row["member"]: row.get("chamber") for row in rows}
    female, republican = {}, {}
    with open(os.path.join(data_root, "fec", "legislators.csv"), newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            member = by_bioguide.get(str(row.get("bioguide_id") or ""))
            if member:
                female[member] = 1.0 if row.get("gender") == "F" else 0.0
                republican[member] = 1.0 if row.get("party") == "Republican" else 0.0
    senate = {name: 1.0 if label == "Senate" else 0.0 for name, label in chamber.items()}
    return {row["member"] for row in rows}, chamber, female, republican, senate


def _verify_inputs(data_root):
    observed = {}
    for relative, expected in EXPECTED_INPUTS.items():
        path = os.path.join(data_root, relative)
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError("input fingerprint mismatch for %s: %s != %s" %
                             (relative, actual, expected))
        observed[relative.replace("\\", "/")] = actual
    return observed


def _build_o(connection, roster, chamber, female, republican, senate, domains):
    raw = {"INTERVIEW": {}, "CREC": {}}
    query = """
        select member, source, coalesce(package,''), group_concat(coalesce(text,''),' ')
        from speeches where source in ('INTERVIEW','CREC')
        group by member, source, package order by member, source, package
    """
    current = None
    rows = []
    for member, source, package, text in connection.execute(query):
        key = (member, source)
        if current is not None and key != current:
            selected = select_documents(current[0], current[1], rows)
            measured = measure_o_source(selected, domains)
            if current[0] in roster and measured:
                raw[current[1]][current[0]] = measured
            rows = []
        current = key
        rows.append((package, text))
    if current is not None:
        selected = select_documents(current[0], current[1], rows)
        measured = measure_o_source(selected, domains)
        if current[0] in roster and measured:
            raw[current[1]][current[0]] = measured

    source_scores = {}
    for source in ("INTERVIEW", "CREC"):
        entropy = _zscore_by_chamber(
            {name: row["domain_entropy"] for name, row in raw[source].items()}, chamber)
        mattr = _zscore_by_chamber(
            {name: row["mattr_100"] for name, row in raw[source].items()}, chamber)
        names = sorted(set(entropy) & set(mattr))
        source_scores[source] = {name: .70 * entropy[name] + .30 * mattr[name]
                                 for name in names}
    names = sorted(set(source_scores["INTERVIEW"]) & set(source_scores["CREC"]))
    score = {name: (source_scores["INTERVIEW"][name] + source_scores["CREC"][name]) / 2.0
             for name in names}
    log_tokens = {name: math.log1p(raw["INTERVIEW"][name]["token_n"]
                                   + raw["CREC"][name]["token_n"]) for name in names}
    log_packages = {name: math.log1p(raw["INTERVIEW"][name]["package_n"]
                                     + raw["CREC"][name]["package_n"]) for name in names}
    residual = residualize_map(score, [female, republican, senate, log_tokens, log_packages])
    final = _zscore_map(residual)
    r, n = _corr(source_scores["INTERVIEW"], source_scores["CREC"])
    components = {name: {
        "interview_score": source_scores["INTERVIEW"][name],
        "crec_score": source_scores["CREC"][name],
        "interview_entropy": raw["INTERVIEW"][name]["domain_entropy"],
        "crec_entropy": raw["CREC"][name]["domain_entropy"],
        "selected_tokens": int(math.expm1(log_tokens[name])),
        "selected_packages": int(math.expm1(log_packages[name])),
    } for name in final}
    return final, components, {"source_r": r, "source_n": n,
                               "spearman_brown": _spearman_brown(r)}


def _build_n(connection, roster, chamber, female, republican, senate):
    query = """
        select member, coalesce(package,''), coalesce(text,'') from speeches
        where source='INTERVIEW' order by member, package, date, rowid
    """
    current = None
    packages = collections.defaultdict(list)
    raw = {}
    for member, package, text in connection.execute(query):
        if current is not None and member != current:
            measured = measure_n_packages(packages, member=current)
            if current in roster and measured:
                raw[current] = measured
            packages = collections.defaultdict(list)
        current = member
        packages[str(package)].append(text)
    if current is not None:
        measured = measure_n_packages(packages, member=current)
        if current in roster and measured:
            raw[current] = measured
    score = {name: row["lability"] for name, row in raw.items()}
    mean_activation = {name: row["mean_activation"] for name, row in raw.items()}
    mean_turn_length = {name: row["mean_turn_length"] for name, row in raw.items()}
    log_turns = {name: math.log1p(row["turn_n"]) for name, row in raw.items()}
    log_packages = {name: math.log1p(row["package_n"]) for name, row in raw.items()}
    residual = residualize_map(
        score, [female, republican, senate, mean_activation, mean_turn_length,
                log_turns, log_packages])
    final = _zscore_map(residual)
    half0 = {name: row["halves"]["0"] for name, row in raw.items() if "0" in row["halves"]}
    half1 = {name: row["halves"]["1"] for name, row in raw.items() if "1" in row["halves"]}
    r, n = _corr(half0, half1)
    components = {name: {
        "raw_lability": raw[name]["lability"],
        "half0": raw[name]["halves"]["0"],
        "half1": raw[name]["halves"]["1"],
        "package_n": raw[name]["package_n"],
        "turn_n": raw[name]["turn_n"],
        "mean_activation": raw[name]["mean_activation"],
        "mean_turn_length": raw[name]["mean_turn_length"],
    } for name in final}
    return final, components, {"half_r": r, "half_n": n,
                               "spearman_brown": _spearman_brown(r)}


def _build_c(data_root, roster, chamber, female, republican, senate):
    with open(os.path.join(data_root, "behavior_panel.json"), encoding="utf-8") as handle:
        behavior = json.load(handle).get("members") or {}
    with open(os.path.join(data_root, "data/members/lep_criterion.json"), encoding="utf-8") as handle:
        follow = json.load(handle).get("per_member") or {}
    votes_raw = {name: math.log1p(float(row["votes_cast"])) for name, row in behavior.items()
                 if name in roster and row.get("votes_cast") is not None}
    follow_raw = {name: float(row["followthrough"]) for name, row in follow.items()
                  if name in roster and row.get("followthrough") is not None}
    vote_z = _zscore_by_chamber(votes_raw, chamber)
    follow_z = _zscore_by_chamber(follow_raw, chamber)
    names = sorted(set(vote_z) & set(follow_z))
    score = {name: execution_consistency(vote_z[name], follow_z[name]) for name in names}
    residual = residualize_map(score, [female, republican, senate])
    final = _zscore_map(residual)
    r, n = _corr(vote_z, follow_z)
    components = {name: {"vote_z": vote_z[name], "followthrough_z": follow_z[name],
                         "raw_consistency": score[name]} for name in final}
    return final, components, {"component_r": r, "component_n": n}


def _coverage(values, chamber):
    return {"total": len(values),
            "House": sum(chamber.get(name) == "House" for name in values),
            "Senate": sum(chamber.get(name) == "Senate" for name in values)}


def build(data_root, lexicon_path):
    data_root = os.path.abspath(data_root)
    inputs = _verify_inputs(data_root)
    lexicon_path = os.path.abspath(lexicon_path)
    with open(lexicon_path, encoding="utf-8") as handle:
        domains = json.load(handle)
    if list(domains) != sorted(domains) or len(domains) != 18:
        raise ValueError("policy-domain lexicon must contain exactly 18 sorted domains")
    roster, chamber, female, republican, senate = _load_roster(data_root)
    database_path = os.path.join(data_root, "social_media.db")
    connection = sqlite3.connect("file:" + database_path + "?mode=ro", uri=True)
    try:
        o_values, o_components, o_reliability = _build_o(
            connection, roster, chamber, female, republican, senate, domains)
        n_values, n_components, n_reliability = _build_n(
            connection, roster, chamber, female, republican, senate)
    finally:
        connection.close()
    c_values, c_components, c_reliability = _build_c(
        data_root, roster, chamber, female, republican, senate)

    coverage = {"O": _coverage(o_values, chamber), "N": _coverage(n_values, chamber),
                "C": _coverage(c_values, chamber)}
    bars = {
        "O": {
            "coverage_total_gte_250": coverage["O"]["total"] >= 250,
            "coverage_senate_gte_30": coverage["O"]["Senate"] >= 30,
            "coverage_house_gte_150": coverage["O"]["House"] >= 150,
            "source_r_gte_0_10": o_reliability["source_r"] is not None
                                      and o_reliability["source_r"] >= .10,
            "spearman_brown_gte_0_18": o_reliability["spearman_brown"] is not None
                                            and o_reliability["spearman_brown"] >= .18,
        },
        "N": {
            "coverage_total_gte_150": coverage["N"]["total"] >= 150,
            "coverage_senate_gte_30": coverage["N"]["Senate"] >= 30,
            "coverage_house_gte_100": coverage["N"]["House"] >= 100,
            "half_r_gte_0_10": n_reliability["half_r"] is not None
                                    and n_reliability["half_r"] >= .10,
            "spearman_brown_gte_0_18": n_reliability["spearman_brown"] is not None
                                            and n_reliability["spearman_brown"] >= .18,
        },
        "C": {
            "coverage_total_gte_350": coverage["C"]["total"] >= 350,
            "coverage_senate_gte_30": coverage["C"]["Senate"] >= 30,
            "coverage_house_gte_250": coverage["C"]["House"] >= 250,
            "component_r_nonnegative": c_reliability["component_r"] is not None
                                           and c_reliability["component_r"] >= 0,
        },
    }
    trait_status = {trait: ("CRITERION_READY_FOR_ONE_LOOK" if all(values.values())
                            else "CRITERION_STOP_BAR_MISS")
                    for trait, values in bars.items()}
    member_rows = {}
    all_names = sorted(set(o_values) | set(n_values) | set(c_values))
    for name in all_names:
        row = {}
        if name in o_values:
            row["O"] = float(o_values[name])
            row["O_components"] = o_components[name]
        if name in n_values:
            row["N"] = float(n_values[name])
            row["N_components"] = n_components[name]
        if name in c_values:
            row["C"] = float(c_values[name])
            row["C_components"] = c_components[name]
        member_rows[name] = row
    return {
        "schema": SCHEMA,
        "status": ("CRITERIA_FROZEN_NO_GOLD_LOOK" if all(
            value == "CRITERION_READY_FOR_ONE_LOOK" for value in trait_status.values())
            else "CRITERIA_PARTIAL_STOP_BAR_MISS"),
        "production_consumers": 0,
        "trait_status": trait_status,
        "inputs_sha256": {**inputs, "data/members/b5_onc_policy_domains.json": sha256_file(lexicon_path)},
        "coverage": coverage,
        "reliability": {"O": o_reliability, "N": n_reliability, "C": c_reliability},
        "bars": bars,
        "members": member_rows,
        "method": {
            "O": "cross-context policy-domain entropy plus MATTR; nuisance-residualized",
            "N": "within-interview successive affective lability; nuisance-residualized",
            "C": "lower of within-chamber vote-participation and LEP followthrough z-scores",
            "outcome_panel_access": False,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build locked O/N/C criteria without panel access")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--lexicon", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "members", "b5_onc_policy_domains.json"))
    parser.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "members", OUTPUT_BASENAME))
    args = parser.parse_args(argv)
    if not str(args.out).endswith(".candidate"):
        raise SystemExit("REFUSED: criterion output must end in .candidate")
    doc = build(args.data_root, args.lexicon)
    from moneyball_core import dump_atomic
    dump_atomic(doc, args.out, sort_keys=True, indent=1)
    print("O/N/C criteria: %s" % doc["status"])
    for trait in ("O", "N", "C"):
        print("  %s %s coverage=%s reliability=%s" % (
            trait, doc["trait_status"][trait], doc["coverage"][trait],
            doc["reliability"][trait]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
