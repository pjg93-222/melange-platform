# -*- coding: utf-8 -*-
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from paths import ROOT as MB_ROOT

"""Behavioral roles v2 locked candidate study.

Usage:
  python fec/members/behavioral_roles_v2_study.py --simulate
  python fec/members/behavioral_roles_v2_study.py --look

The real-data path writes candidate/result artifacts consumed by NOTHING. It never mutates an
engine input. See behavioral_roles_v2_prereg.md for the frozen definitions and verdict bars.
"""

import argparse
import collections
import csv
import hashlib
import json
import math
import re
import sqlite3

import numpy as np

_s.stdout.reconfigure(encoding="utf-8", errors="replace")
B = MB_ROOT
SPLIT_SALTS = ("roles-a", "roles-b", "roles-c", "roles-d", "roles-e")
K_PERM = 5000
MIN_CELL_TURNS = 12
MIN_PROGRAM_MEMBERS = 8
MIN_FULL_TURNS = 30
MIN_HALF_TURNS = 15
PINS = {
    "social_media.db": "21f2e9f6c13d44f5bed27cabfc9414b445ba8b0f047e88b101ac5267112ab6ec",
    "data/engine/congestion.json": "ddc9d746600dbdfc60b76c54537662e19c758cbfb9d7a478a71b7bf90cd727a6",
    "behavior_panel.json": "491761ea130f8458ae0d09d15be20619b31011ab14e56e6a05bfbdf3d703a4cb",
    "behavioral_dispositions.json": "57ca1e2b1731cbeed4411645c2b527a8db0136362c2d3d4654dda8faaa852e03",
    "data/members/big5_blue_validated.json": "876bc2ecf8672477d586f3db34472cdb2cf61ee0573e30c4b909448cd0970a8f",
    "coop_v2_candidate.json": "f0f958dfd35e89f7ab08e73e4f4aab59f762e50558803da4f6dec3a455ff224c",
    "member_receptivity.json": "84391fd4438588d162ddf9d2caacee86a8ab0cbeca3a740455e080d79bb06c33",
    _o.path.join("fec", "legislators.csv"): "c5a6cbdbaae0bb19faa89852c29922809ececc70b97c149b4709588087e5e391",
}

DEFENSE = re.compile(
    r"\b(let me finish|may i finish|i'?m not finished|if i could just finish|hold on|"
    r"wait a (minute|second)|as i was saying|excuse me[,.]? (i|let))\b",
    re.I,
)


def assert_pins():
    for relpath, expected in PINS.items():
        digest = hashlib.sha256()
        with open(_o.path.join(B, relpath), "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        actual = digest.hexdigest()
        if actual != expected:
            raise SystemExit(
                "input pin drift: %s expected %s got %s" % (relpath, expected, actual)
            )


def program_of(pkg):
    p = (pkg or "").lower()
    if "transcripts.cnn.com" in p:
        m = re.search(r"/show/(\w+)/", p)
        return "cnn:" + (m.group(1) if m else "?")
    if "nbcnews" in p:
        return "mtp"
    if "pbs.org" in p:
        return "pbs"
    if "npr.org" in p:
        return "npr"
    return "other"


def is_cutoff(text):
    t = text.strip()
    return (
        t.endswith("--")
        or t.endswith("—")
        or t.endswith("…")
        or t.endswith("...")
        or (t and t[-1] not in ".!?\"')”")
    )


def gini(values):
    if len(values) < 2:
        return 0.0
    xs = sorted(values)
    n = len(xs)
    total = sum(xs)
    if total == 0:
        return 0.0
    return 2 * sum((i + 1) * x for i, x in enumerate(xs)) / (n * total) - (n + 1) / n


def corr(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 5 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def sb(r):
    return 2 * r / (1 + r) if np.isfinite(r) and r > -1 else float("nan")


def residual(values, controls):
    values = np.asarray(values, float)
    controls = np.asarray(controls, float)
    design = np.column_stack([np.ones(len(values)), controls])
    return values - design @ np.linalg.lstsq(design, values, rcond=None)[0]


def partial_test(x, y, controls, seed, k=K_PERM):
    rx = residual(x, controls)
    ry = residual(y, controls)
    observed = corr(rx, ry)
    rng = np.random.default_rng(seed)
    ge = 0
    for _ in range(k):
        ge += corr(rx, rng.permutation(ry)) >= observed
    return observed, (ge + 1) / (k + 1)


def stable_half(package, salt):
    raw = (salt + "\0" + package).encode("utf-8", "surrogatepass")
    return hashlib.sha256(raw).digest()[0] & 1


def chamber_z(values, chamber):
    out = {}
    for ch in ("House", "Senate"):
        names = [m for m in values if chamber.get(m) == ch and np.isfinite(values[m])]
        if len(names) < 5:
            continue
        xs = np.asarray([values[m] for m in names], float)
        sdv = float(xs.std(ddof=1)) or 1.0
        mu = float(xs.mean())
        for m in names:
            out[m] = (values[m] - mu) / sdv
    return out


def load_appearances():
    con = sqlite3.connect(_o.path.join(B, "social_media.db"), timeout=60)
    con.execute("PRAGMA query_only=1")
    app = collections.defaultdict(list)
    query = "select member, package, text from speeches where source='INTERVIEW' order by rowid"
    for member, package, text_value in con.execute(query):
        if text_value and text_value.strip():
            app[(member, program_of(package), package)].append(text_value.strip())
    con.close()
    return app


def build_scores(appearances, chamber, min_score_turns=MIN_FULL_TURNS):
    """Run the frozen cell floors, program standardization, and member aggregation."""
    agg = collections.defaultdict(
        lambda: {"turns": 0, "words": 0, "lens": [], "cut": 0, "defense": 0}
    )
    for (member, program, _package), turns in appearances.items():
        row = agg[(member, program)]
        for text_value in turns:
            words = len(text_value.split())
            row["turns"] += 1
            row["words"] += words
            row["lens"].append(words)
            row["cut"] += int(is_cutoff(text_value))
            row["defense"] += len(DEFENSE.findall(text_value))

    raw = collections.defaultdict(dict)
    for (member, program), row in agg.items():
        n = row["turns"]
        if n < MIN_CELL_TURNS:
            continue
        lens = sorted(row["lens"])
        raw[program][member] = {
            "turns": n,
            "wpt_mean": row["words"] / n,
            "wpt_p90": lens[int(0.9 * (len(lens) - 1))],
            "gini": gini(lens),
            "cutoff": row["cut"] / n,
            "defense": 1000.0 * row["defense"] / max(1, row["words"]),
        }

    features = ("wpt_mean", "wpt_p90", "gini", "cutoff", "defense")
    zsum = collections.defaultdict(lambda: collections.defaultdict(float))
    weights = collections.Counter()
    for program, members in raw.items():
        if len(members) < MIN_PROGRAM_MEMBERS:
            continue
        for feature in features:
            vals = np.asarray([row[feature] for row in members.values()], float)
            sdv = float(vals.std(ddof=1)) or 1.0
            mu = float(vals.mean())
            for member, row in members.items():
                zsum[member][feature] += ((row[feature] - mu) / sdv) * row["turns"]
        for member, row in members.items():
            weights[member] += row["turns"]

    feat = {
        member: {key: zsum[member][key] / weight for key in features}
        for member, weight in weights.items()
        if weight > 0
    }
    dom_raw = {
        m: (row["gini"] + row["defense"]) / 2.0
        for m, row in feat.items()
        if weights[m] >= min_score_turns
    }
    exp_raw = {
        m: (row["wpt_mean"] + row["wpt_p90"]) / 2.0
        for m, row in feat.items()
        if weights[m] >= min_score_turns
    }
    react_raw = {
        m: row["cutoff"] for m, row in feat.items() if weights[m] >= min_score_turns
    }
    return {
        "dominance_v2": chamber_z(dom_raw, chamber),
        "expressiveness_v2": chamber_z(exp_raw, chamber),
        "reactivity_raw": chamber_z(react_raw, chamber),
        "turns": dict(weights),
        "features": feat,
    }


def split_reliability(appearances, chamber):
    records = {name: [] for name in ("dominance_v2", "expressiveness_v2", "reactivity_raw")}
    for salt in SPLIT_SALTS:
        halves = ({}, {})
        hdict = (collections.defaultdict(list), collections.defaultdict(list))
        for key, turns in appearances.items():
            hdict[stable_half(key[2], salt)][key] = turns
        scores = [build_scores(hdict[h], chamber, MIN_HALF_TURNS) for h in (0, 1)]
        for name in records:
            a, b = scores[0][name], scores[1][name]
            names = [
                m
                for m in a
                if m in b
                and scores[0]["turns"].get(m, 0) >= MIN_HALF_TURNS
                and scores[1]["turns"].get(m, 0) >= MIN_HALF_TURNS
            ]
            r = corr([a[m] for m in names], [b[m] for m in names])
            records[name].append({"salt": salt, "r_half": r, "r_sb": sb(r), "n": len(names)})
    return records


def joined_arrays(left, right, control_maps):
    names = [
        m
        for m in left
        if m in right
        and left[m] is not None
        and right[m] is not None
        and all(m in c and c[m] is not None for c in control_maps)
    ]
    return (
        names,
        np.asarray([left[m] for m in names], float),
        np.asarray([right[m] for m in names], float),
        np.asarray([[c[m] for c in control_maps] for m in names], float),
    )


def partial_pair(left, right, controls, seed=20260713, permutations=False):
    names, x, y, z = joined_arrays(left, right, controls)
    if len(names) < 20:
        return {"r": None, "p": None, "n": len(names)}
    if permutations:
        r, p = partial_test(x, y, z, seed)
    else:
        r, p = corr(residual(x, z), residual(y, z)), None
    return {"r": r, "p": p, "n": len(names)}


def rank_sensitivity(candidate):
    """In-memory-only flip-model winnability sensitivity; restores the kernel cache."""
    import moneyball_core as MC

    artifact = json.load(open(_o.path.join(B, "member_receptivity.json"), encoding="utf-8"))
    members = artifact["members"]
    MC.receptivity()
    base_rec = {m: dict(v) for m, v in MC._c["rec"].items()}
    from engine import flip_model as FM

    def winnability():
        values = collections.Counter()
        for category in FM.cats:
            _cs, _camp, n_a, n_b, m_at, m_bt, target = FM.topic_setup(category, FM.CS0)
            for member in FM.members:
                values[member] += target[member] * FM.pflip(
                    FM.cu0[member],
                    FM.ind0[member],
                    FM.R[member],
                    n_a[member],
                    n_b[member],
                    m_at,
                    m_bt,
                    FM.rc(member, "pressure_mult"),
                    FM.CM[member],  # COMPET_MULT: same c as flip_model (dial OFF -> 1.0)
                )
        return values

    base = winnability()
    varied = {m: dict(base_rec.get(m, {})) for m in members}
    for member, row in varied.items():
        old_dom = float(row.get("dominance_z") or 0.0)
        new_dom = float(candidate.get(member, 0.0))
        new_trait = float(row["susc_trait_z"]) + 0.10 * old_dom - 0.10 * new_dom
        persona = row.get("persona_susc_z")
        combined = 0.70 * new_trait + 0.30 * persona if persona is not None else new_trait
        susc_mult = max(0.70, min(1.30, 1.0 + 0.18 * combined))
        row["susc_trait_z"] = new_trait
        row["susc_z"] = combined
        row["susc_mult"] = susc_mult
        row["edge_mult"] = max(0.80, min(1.20, 1.0 + 0.12 * combined))
        row[MC.pressure_field()] = max(   # R1 (Paul ruling D-107): the field the engines read, rebuilt from the matching incent field
            0.50, min(1.60, susc_mult * row[MC.incent_field()] * row["pt_mult"])
        )
    MC._c["rec"] = varied
    changed = winnability()
    MC._c["rec"] = base_rec

    def rank_map(counter):
        return {name: i for i, (name, _value) in enumerate(counter.most_common())}

    rb, rc = rank_map(base), rank_map(changed)
    names = list(rb)
    d2 = sum((rb[m] - rc[m]) ** 2 for m in names)
    n = len(names)
    rho = 1 - 6 * d2 / (n * (n * n - 1))
    base20 = {m for m, _ in base.most_common(20)}
    base50 = {m for m, _ in base.most_common(50)}
    return {
        "spearman_rho": rho,
        "top20_overlap": len(base20 & {m for m, _ in changed.most_common(20)}),
        "top50_overlap": len(base50 & {m for m, _ in changed.most_common(50)}),
        "largest_rank_moves": sorted(
            ({"member": m, "delta": rc[m] - rb[m]} for m in names),
            key=lambda row: abs(row["delta"]),
            reverse=True,
        )[:10],
    }


def synthetic_battery():
    rng = np.random.default_rng(20260713)
    n = 500
    controls = rng.normal(size=(n, 4))
    latent = rng.normal(size=n)
    x = latent + 0.35 * controls[:, 0] + rng.normal(scale=0.55, size=n)
    y = 0.30 * latent + 0.40 * controls[:, 0] + rng.normal(size=n)
    r_signal, p_signal = partial_test(x, y, controls, 111, k=2000)
    null_y = rng.normal(size=n)
    r_null, p_null = partial_test(x, null_y, controls, 222, k=2000)
    half_a = latent + rng.normal(scale=0.70, size=n)
    half_b = latent + rng.normal(scale=0.70, size=n)
    r_half = corr(half_a, half_b)
    r_sb = sb(r_half)
    sparse_turns = {"covered": 30, "sparse": 29}
    emitted = {m for m, turns in sparse_turns.items() if turns >= MIN_FULL_TURNS}
    checks = {
        "planted_partial_admitted": r_signal >= 0.10 and p_signal < 0.05,
        "null_rejected": not (r_null >= 0.10 and p_null < 0.05),
        "spearman_brown_formula": abs(r_sb - (2 * r_half / (1 + r_half))) < 1e-12,
        "missingness_enforced": emitted == {"covered"},
    }
    print("BEHAVIORAL ROLES v2 — SYNTHETIC BATTERY")
    print("  planted partial r=%+.3f p=%.4f" % (r_signal, p_signal))
    print("  null partial    r=%+.3f p=%.4f" % (r_null, p_null))
    print("  half r=%.3f -> SB=%.3f" % (r_half, r_sb))
    for name, passed in checks.items():
        print("  %-32s %s" % (name, "PASS" if passed else "FAIL"))
    if not all(checks.values()):
        raise SystemExit("synthetic battery refused the real look")
    print("BATTERY PASS — real-data path is mechanically licensed; prereg commit still required.")


def real_look():
    assert_pins()
    congestion = json.load(open(_o.path.join(B, "data", "engine", "congestion.json"), encoding="utf-8"))
    chamber = {row["member"]: row.get("chamber") for row in congestion}
    appearances = load_appearances()
    full = build_scores(appearances, chamber)
    reliability = split_reliability(appearances, chamber)

    panel = json.load(open(_o.path.join(B, "behavior_panel.json"), encoding="utf-8"))["members"]
    dispositions = json.load(
        open(_o.path.join(B, "behavioral_dispositions.json"), encoding="utf-8")
    )["members"]
    blue = json.load(open(_o.path.join(B, "data", "members", "big5_blue_validated.json"), encoding="utf-8"))["traits"]
    try:
        cooperation = {
            m: row.get("cooperation_v2_z")
            for m, row in json.load(
                open(_o.path.join(B, "coop_v2_candidate.json"), encoding="utf-8")
            )["members"].items()
            if row.get("cooperation_v2_z") is not None
        }
    except FileNotFoundError:
        cooperation = {}

    import moneyball_core as MC

    party_map = MC.party()
    bio_to_name = MC.bio2name()
    gender_map = {}
    with open(_o.path.join(B, "fec", "legislators.csv"), encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            member = bio_to_name.get(row.get("bioguide_id"))
            if member and row.get("gender") in ("F", "M"):
                gender_map[member] = row["gender"]

    party = {m: 1.0 if party_map.get(m) == "R" else 0.0 for m in chamber if party_map.get(m) in ("D", "R")}
    gender = {m: 1.0 if value == "F" else 0.0 for m, value in gender_map.items()}
    senate = {m: 1.0 if value == "Senate" else 0.0 for m, value in chamber.items()}
    log_turns = {m: math.log1p(v) for m, v in full["turns"].items()}
    common_controls = [party, gender, senate, log_turns]

    roles = {
        "visibility": {m: row.get("visibility_z") for m, row in dispositions.items() if row.get("visibility_z") is not None},
        "breadth": {m: row.get("breadth_z") for m, row in dispositions.items() if row.get("breadth_z") is not None},
        "diligence": {m: row.get("diligence_z") for m, row in dispositions.items() if row.get("diligence_z") is not None},
        "cooperation_v2": cooperation,
        "reactivity": {m: row.get("reactivity_z") for m, row in dispositions.items() if row.get("reactivity_z") is not None},
    }
    dom = full["dominance_v2"]
    exp = full["expressiveness_v2"]

    contamination = {
        "dominance_vs_expressiveness": partial_pair(dom, exp, common_controls),
        "dominance": {name: partial_pair(dom, target, common_controls) for name, target in roles.items()},
        "expressiveness": {name: partial_pair(exp, target, common_controls) for name, target in roles.items()},
    }

    leader = {m: float(row.get("committee_leader", 0)) for m, row in panel.items()}
    dom_criterion = partial_pair(dom, leader, common_controls, seed=2026071301, permutations=True)
    floor_elaboration = {
        m: math.log((row.get("floor_words") or 0) / row["n_floor_speeches"])
        for m, row in panel.items()
        if (row.get("n_floor_speeches") or 0) >= 20 and (row.get("floor_words") or 0) > 0
    }
    log_floor_count = {
        m: math.log1p(row.get("n_floor_speeches") or 0)
        for m, row in panel.items()
        if (row.get("n_floor_speeches") or 0) >= 20
    }
    exp_criterion = partial_pair(
        exp,
        floor_elaboration,
        common_controls + [log_floor_count],
        seed=2026071302,
        permutations=True,
    )
    blue_e = {m: row.get("E") for m, row in blue.items() if row.get("E") is not None}
    blue_tokens = {m: math.log1p(row.get("tokens") or 0) for m, row in blue.items()}
    blue_alignment = {
        "dominance_E": partial_pair(dom, blue_e, common_controls + [blue_tokens]),
        "expressiveness_E": partial_pair(exp, blue_e, common_controls + [blue_tokens]),
    }

    rel_summary = {}
    for name, rows in reliability.items():
        vals = [row["r_sb"] for row in rows if np.isfinite(row["r_sb"])]
        rel_summary[name] = {
            "median_sb": float(np.median(vals)) if vals else None,
            "n_ge_40": sum(value >= 0.40 for value in vals),
            "splits": rows,
        }

    dom_off = [abs(contamination["dominance_vs_expressiveness"]["r"] or 0.0)] + [
        abs(row["r"] or 0.0) for row in contamination["dominance"].values()
    ]
    exp_off = [
        abs(contamination["expressiveness"][name]["r"] or 0.0)
        for name in ("breadth", "diligence", "cooperation_v2", "reactivity")
    ]
    missing_ok = all(full["turns"].get(m, 0) >= MIN_FULL_TURNS for m in set(dom) | set(exp))
    ranks = rank_sensitivity(dom)
    gates = {
        "coverage": len(dom) >= 250 and len(exp) >= 250,
        "missingness": missing_ok,
        "dominance_reliability": rel_summary["dominance_v2"]["median_sb"] >= 0.50
        and rel_summary["dominance_v2"]["n_ge_40"] >= 4,
        "expressiveness_reliability": rel_summary["expressiveness_v2"]["median_sb"] >= 0.50
        and rel_summary["expressiveness_v2"]["n_ge_40"] >= 4,
        "reactivity_reliability": rel_summary["reactivity_raw"]["median_sb"] >= 0.45,
        "dominance_contamination": max(dom_off) <= 0.45,
        "expressiveness_visibility": abs(contamination["expressiveness"]["visibility"]["r"] or 0.0) <= 0.45,
        "expressiveness_other_contamination": max(exp_off) <= 0.35,
        "expressiveness_floor_criterion": exp_criterion["r"] is not None
        and exp_criterion["r"] >= 0.10
        and exp_criterion["p"] < 0.05,
        "rank_sensitivity": ranks["spearman_rho"] >= 0.995
        and ranks["top20_overlap"] >= 18
        and ranks["top50_overlap"] >= 45,
    }
    verdicts = {
        "dominance_v2_qualified_candidate": all(
            gates[name]
            for name in (
                "coverage",
                "missingness",
                "dominance_reliability",
                "dominance_contamination",
                "rank_sensitivity",
            )
        ),
        "expressiveness_v2_qualified_role": all(
            gates[name]
            for name in (
                "coverage",
                "missingness",
                "expressiveness_reliability",
                "expressiveness_visibility",
                "expressiveness_other_contamination",
            )
        ),
    }
    verdicts["expressiveness_v2_independent_E_indicator"] = (
        verdicts["expressiveness_v2_qualified_role"] and gates["expressiveness_floor_criterion"]
    )

    candidate_members = {}
    for member in sorted(set(dom) | set(exp)):
        row = {"qualifying_turns": full["turns"].get(member, 0)}
        if member in dom:
            row["dominance_v2"] = round(dom[member], 6)
        if member in exp:
            row["expressiveness_v2"] = round(exp[member], 6)
        candidate_members[member] = row

    result = {
        "locked_prereg": "behavioral_roles_v2_prereg.md",
        "coverage": {"dominance_v2": len(dom), "expressiveness_v2": len(exp)},
        "reliability": rel_summary,
        "criterion": {
            "dominance_vs_committee_leadership": dom_criterion,
            "expressiveness_vs_floor_elaboration": exp_criterion,
            "shared_interview_blue_E_descriptive": blue_alignment,
        },
        "contamination": contamination,
        "rank_sensitivity": ranks,
        "gates": gates,
        "verdicts": verdicts,
    }

    from moneyball_core import dump_atomic

    dump_atomic(
        {
            "members": candidate_members,
            "coverage": result["coverage"],
            "method": "LOCKED candidate: dominance_v2=mean(program-z gini, defense), expressiveness_v2=mean(program-z wpt_mean, wpt_p90), chamber-z, >=30 qualifying turns; formal leadership excluded; consumed by nothing",
        },
        _o.path.join(B, "data", "members", "behavioral_roles_v2_candidate.json"),
    )
    dump_atomic(result, _o.path.join(B, "data", "members", "behavioral_roles_v2_study.json"))

    print("BEHAVIORAL ROLES v2 — THE ONE LOOK")
    print("coverage: dominance %d | expressiveness %d" % (len(dom), len(exp)))
    for name in ("dominance_v2", "expressiveness_v2", "reactivity_raw"):
        row = rel_summary[name]
        print(
            "reliability %-18s median SB=%.3f | >=.40 %d/5 | %s"
            % (name, row["median_sb"], row["n_ge_40"], ", ".join("%.3f" % s["r_sb"] for s in row["splits"]))
        )
    print(
        "criterion dominance~leadership partial r=%+.3f p=%.4f n=%d (descriptive)"
        % (dom_criterion["r"], dom_criterion["p"], dom_criterion["n"])
    )
    print(
        "criterion expressiveness~floor-elaboration partial r=%+.3f p=%.4f n=%d"
        % (exp_criterion["r"], exp_criterion["p"], exp_criterion["n"])
    )
    print("partial contamination:")
    print("  dominance~expressiveness %+.3f" % contamination["dominance_vs_expressiveness"]["r"])
    for role in roles:
        print(
            "  %-14s dominance %+.3f | expressiveness %+.3f"
            % (role, contamination["dominance"][role]["r"], contamination["expressiveness"][role]["r"])
        )
    print(
        "shared-interview Blue E (descriptive): dominance %+.3f | expressiveness %+.3f"
        % (blue_alignment["dominance_E"]["r"], blue_alignment["expressiveness_E"]["r"])
    )
    print(
        "rank sensitivity: rho %.6f | top20 %d/20 | top50 %d/50"
        % (ranks["spearman_rho"], ranks["top20_overlap"], ranks["top50_overlap"])
    )
    print("gates:")
    for name, passed in gates.items():
        print("  %-40s %s" % (name, "PASS" if passed else "MISS"))
    print("verdicts:")
    for name, passed in verdicts.items():
        print("  %-48s %s" % (name, "PASS" if passed else "MISS"))
    print("wrote behavioral_roles_v2_candidate.json and behavioral_roles_v2_study.json (consumed by nothing)")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--simulate", action="store_true")
    group.add_argument("--look", action="store_true")
    args = parser.parse_args()
    if args.simulate:
        synthetic_battery()
    else:
        real_look()


if __name__ == "__main__":
    main()
