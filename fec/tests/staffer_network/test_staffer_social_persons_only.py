#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fixture generation for the PERSONS-ONLY staffer social network (D-022).

Mints a complete synthetic input world - 30 people, 3 containers, 2 organisations,
2 policy areas, with deliberately overlapping AND deliberately non-overlapping stints -
runs audits/staffer_network/build_staffer_social_network.py end to end over it, and
proves the properties the v4 rework is supposed to guarantee:

  * a same-container pair with NO temporal overlap gets no edge, and is counted in the
    zero-overlap rejection (D-019)
  * containers, organisations and policy areas never appear in node_scores, and the
    edges file carries person-person co_tenure rows only (D-022)
  * the grouping layers carry every attachment, with operator_included False on every
    row, and the groups roster covers all three group kinds
  * office_diversity is the Simpson index over the OFFICES that produced a person's
    ties, hand-checked on a person whose ties come from two offices, and the retired
    bridging_diversity field is absent from the payload
  * the restart distribution is uniform over persons
  * the Politico spine id remap: a bridge receipt bound to an older spine FAILS the
    build without --spine-id-map, PASSES with a complete map, and FAILS loudly on an
    incomplete one
  * the twin is byte-identical excluding generated_at, and the shipped verifier passes
  * NEGATIVE CONTROL: the verifier's naming law actually fires on the new generic
    artifact names when a live glob selector could reach them
  * OWNER RULING 2026-09-07: a REPORT_PERIOD stint is the UNION of the periods observed,
    one stint per contiguous run. A second world carries the v7 spine's contiguous_runs;
    the SAME world with those fields stripped is the negative control, and must still
    fabricate the guaranteed co-tenure edge the ruling exists to remove. A third pass
    puts a stint back across the gap and proves the verifier's containment check fires.

Run: python fec/tests/staffer_network/test_staffer_social_persons_only.py
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUDITS = ROOT / "audits" / "staffer_network"
BUILDER = AUDITS / "build_staffer_social_network.py"
VERIFIER = AUDITS / "verify_staffer_social_network.py"
sys.path.insert(0, str(AUDITS))
sys.path.insert(0, str(ROOT))

import build_staffer_social_network as subject  # noqa: E402

P = 0
F = 0
GENERATED_AT = "2026-09-07T00:00:00Z"


def ok(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print("PASS %s" % name)
    else:
        F += 1
        print("FAIL %s :: %s" % (name, detail))


def eq(name, actual, expected):
    ok(name, actual == expected, "actual=%r expected=%r" % (actual, expected))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_gz(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


# --------------------------------------------------------------------------- world
# Three containers.  C1 is a HOUSE personal office (day-precision lane), C2 a SENATE
# committee, C3 a SENATE personal office.  Windows are chosen so that inside C1 the
# 2010 cohort and the 2016 cohort can never have met.
C1 = ("house", "2019 HON. ALPHA", "member_office", "A000001")
C2 = ("senate", "JUDICIARY", "committee", None)
C3 = ("senate", "HON. BETA", "member_office", "B000002")
# Three single-occupant offices carrying the builder's NAMED REGRESSION people. The
# gate is a real-data check on two LDA identities; the fixture reproduces the exact
# structure it asserts so the gate stays HARD here instead of being switched off.
C4 = ("house", "HON. ADAMS", "member_office", "A000370")
C5 = ("house", "HON. SYKES", "member_office", "S001223")
C6 = ("senate", "HON. VITTER", "member_office", "V000127")
CONTAINERS = [C1, C2, C3, C4, C5, C6]
GORDON = 30
STEPHANIE = 31
PEOPLE = 32

# ---- the contiguous-runs world (owner ruling 2026-09-07) --------------------------
# C7 holds the ruling's exact shape: one person observed in Senate periods P1, P2 and
# P4, and a colleague observed ONLY in P3.  Under the old min-to-max rule the first
# person's span swallowed P3 whole and the pair got a GUARANTEED co-tenure edge for a
# year they never shared.  C8 holds a day-level House row whose printed service dates
# sit inside one quarter, which must survive the run split untouched.
C7 = ("senate", "APPROPRIATIONS", "committee", None)
C8 = ("house", "2019 HON. GAMMA", "member_office", "G000003")
RUNS_CONTAINERS = [C7, C8]
RUNS_A = 32          # senate, periods P1 P2 _ P4  -> two runs
RUNS_B = 33          # senate, period  _  _ P3 _   -> one run, inside A's gap
RUNS_C = 34          # house,  day-level dates inside one quarter
RUNS_PEOPLE = 35
# Senate half-year period tokens are named by their START date.
P1, P2, P3, P4 = "2012-04-01", "2012-10-01", "2013-04-01", "2013-10-01"

ORG_A = "organization:lda:registrant:9001"
ORG_B = "organization:lda:registrant:9002"
ISSUE_A = "Banking"
ISSUE_B = "Health Care"


def day(offset):
    return (date(2010, 1, 1) + timedelta(days=offset)).isoformat()


def person_id(index, rekeyed=False):
    if rekeyed and index in REKEYED:
        return "hill:person:q%02d" % index
    return "hill:person:p%02d" % index


REKEYED = {5, 12, 21}


def stints():
    """(person index, container, start offset, end offset) - end is INCLUSIVE."""
    rows = []
    # C1: people 0-4 all serve 2010-2011 and therefore all overlap each other.
    for i in range(0, 5):
        rows.append((i, C1, 0, 700))
    # C1: people 5-9 serve 2016-2017.  They overlap each other and NOBODY from 0-4.
    for i in range(5, 10):
        rows.append((i, C1, 2200, 2900))
    # C2: people 10-19 all overlap on a common window.
    for i in range(10, 20):
        rows.append((i, C2, 1000, 1900))
    # C3: people 20-29 all overlap on a common window.
    for i in range(20, 30):
        rows.append((i, C3, 1200, 2100))
    # Two bridge people who ALSO sit in a second container, so their co-tenure mass is
    # split across two offices and office_diversity is strictly positive for them.
    rows.append((10, C3, 1300, 1800))
    rows.append((20, C2, 1300, 1800))
    # The named regression people: Gordon leaves Adams and starts at Sykes the next day
    # (an ABUTTING move, gap 0); Stephanie sits in the Vitter office.
    rows.append((GORDON, C4, 0, 100))
    rows.append((GORDON, C5, 101, 200))
    rows.append((STEPHANIE, C6, 300, 500))
    return rows


def senate_window_of(day_iso):
    """(start, end) of the Senate half-year window containing ``day_iso``."""
    moment = date.fromisoformat(day_iso)
    if moment.month >= 10:
        return date(moment.year, 10, 1), date(moment.year + 1, 3, 31)
    if moment.month <= 3:
        return date(moment.year - 1, 10, 1), date(moment.year, 3, 31)
    return date(moment.year, 4, 1), date(moment.year, 9, 30)


def senate_shift(window_start, steps):
    """Move a Senate window START by ``steps`` half-year reporting periods."""
    ordinal = window_start.year * 2 + (0 if window_start.month == 4 else 1) + steps
    year, remainder = divmod(ordinal, 2)
    return date(year, 4, 1) if remainder == 0 else date(year, 10, 1)


def house_quarter_of(day_iso):
    moment = date.fromisoformat(day_iso)
    return "%04dQ%d" % (moment.year, (moment.month - 1) // 3 + 1)


def period_fields(span):
    """Add the v7 spine's period fields to a span, snapping Senate rows to windows.

    The House lane keeps its printed service dates (the spine writes them into core at
    DAY precision and the period tokens are only a label). The Senate lane is snapped
    onto real reporting-period boundaries because the builder's boundary guard
    recomputes them and would - correctly - refuse a row whose contiguous_runs
    endpoints do not reproduce its own core dates.
    """
    if span["chamber"] == "house":
        first = house_quarter_of(span["core"]["start_latest"])
        last = house_quarter_of(span["core"]["end_earliest"])
        span["date_precision"] = "DAY"
    else:
        start_window = senate_window_of(span["core"]["start_latest"])[0]
        end_window = senate_window_of(span["core"]["end_earliest"])[0]
        span["core"] = {"start_latest": start_window.isoformat(),
                        "end_earliest": senate_window_of(end_window.isoformat())[1].isoformat()}
        span["envelope"] = {
            "start_earliest": senate_shift(start_window, -1).isoformat(),
            "end_latest": senate_window_of(
                senate_shift(end_window, 1).isoformat())[1].isoformat(),
        }
        first = start_window.isoformat()
        last = end_window.isoformat()
        span["date_precision"] = "REPORT_PERIOD"
    span["first_period"] = first
    span["last_period"] = last
    span["observed_periods"] = sorted({first, last})
    span["observed_period_count"] = len(span["observed_periods"])
    span["contiguous_runs"] = [[first, last]]
    return span


def build_world(root, rekeyed=False, policy_rekeyed=None, runs=False, strip_runs=False):
    """Write a complete synthetic input world under ``root``. Returns a path map.

    ``policy_rekeyed`` lets the Politico bridge carry ids from a DIFFERENT spine
    generation than the spine itself - exactly the situation --spine-id-map exists for.

    ``runs`` adds the v7 spine's period fields to every span plus the three people of
    the contiguous-runs case; ``strip_runs`` then deletes the v7-only fields again,
    producing the SAME dates and people on an OLDER spine.  That pair is the positive
    and negative control for the 2026-09-07 ruling: identical inputs, one carrying
    contiguous_runs and one not.
    """
    root = Path(root)
    containers = CONTAINERS + (RUNS_CONTAINERS if runs else [])
    people_count = RUNS_PEOPLE if runs else PEOPLE
    if policy_rekeyed is None:
        policy_rekeyed = rekeyed
    spine_dir = root / "audits" / "staffer_network" / "hill_personnel_spine_test_primary"
    house_dir = root / "audits" / "staffer_network" / "house_personnel_ledger_test_primary"
    senate_dir = root / "audits" / "staffer_network" / "senate_committee_payroll_test_primary"
    identity_dir = root / "audits" / "staffer_network" / "house_person_identity_20260902_v2_primary"
    politico_dir = root / "audits" / "staffer_network" / "politico_links_test_primary"
    tier_dir = root / "audits" / "staffer_network" / "exploratory_tier_test_primary"

    # ---- spine ----------------------------------------------------------------
    named_lda = {GORDON: "lda:lobbyist:147664", STEPHANIE: "lda:lobbyist:147663"}
    spine_rows = []
    for i in range(people_count):
        spine_rows.append({
            "spine_person_id": person_id(i, rekeyed),
            "identity_tier": ["S1", "S2", "S3"][i % 3],
            "chambers": ["house"] if i < 10 or i in (GORDON, RUNS_C) else ["senate"],
            "member_bioguides": [C1[3]] if i < 10 else [],
            "lda_source_person_id": named_lda.get(
                i, ("lda:lobbyist:%d" % (7000 + i)) if i % 5 == 0 else None),
            "quorum_staff_id": None,
            "payee_key": {"last": "LAST%02d" % i, "first": "FIRST%02d" % i},
        })
    write_gz(spine_dir / "hill_person_spine.ndjson.gz", spine_rows)

    # ---- spans + the House day-precision observations they point at ------------
    span_rows = []
    observation_rows = []
    obs_index = 0
    for person, container, start, end in stints():
        chamber, office_key, kind, bioguide = container
        source_row_ids = []
        if chamber == "house":
            source_file = "sod_%02d.pdf" % person
            observation_rows.append({
                "source_file": source_file,
                "source_row_index": obs_index,
                "service_start": day(start),
                "service_end": day(end),
            })
            source_row_ids = ["house-ledger:%s#%d" % (source_file, obs_index)]
            obs_index += 1
        span_rows.append({
            "spine_person_id": person_id(person, rekeyed),
            "chamber": chamber,
            "office_key": office_key,
            "container_kind": kind,
            "member_bioguide": bioguide,
            "core": {"start_latest": day(start), "end_earliest": day(end)},
            "envelope": {"start_earliest": day(start - 5), "end_latest": day(end + 5)},
            "source_row_ids": source_row_ids,
            "source_lane": ["house_ledger" if chamber == "house" else "senate_payroll"],
            "status": "departed",
            "title_last": "STAFF",
            "periods_observed": 4,
        })
    if runs:
        span_rows = [period_fields(span) for span in span_rows]
        # RUNS_A: observed in P1, P2 and P4 - two contiguous runs with P3 missing.
        span_rows.append({
            "spine_person_id": person_id(RUNS_A, rekeyed),
            "chamber": "senate", "office_key": C7[1], "container_kind": C7[2],
            "member_bioguide": C7[3],
            "core": {"start_latest": P1, "end_earliest": "2014-03-31"},
            "envelope": {"start_earliest": "2011-10-01", "end_latest": "2014-09-30"},
            "source_row_ids": [], "source_lane": ["senate_payroll"],
            "status": "departed", "title_last": "STAFF", "periods_observed": 3,
            "date_precision": "REPORT_PERIOD",
            "first_period": P1, "last_period": P4,
            "observed_periods": [P1, P2, P4], "observed_period_count": 3,
            "contiguous_runs": [[P1, P2], [P4, P4]],
        })
        # RUNS_B: observed ONLY in P3, i.e. entirely inside RUNS_A's gap.
        span_rows.append({
            "spine_person_id": person_id(RUNS_B, rekeyed),
            "chamber": "senate", "office_key": C7[1], "container_kind": C7[2],
            "member_bioguide": C7[3],
            "core": {"start_latest": P3, "end_earliest": "2013-09-30"},
            "envelope": {"start_earliest": P2, "end_latest": "2014-03-31"},
            "source_row_ids": [], "source_lane": ["senate_payroll"],
            "status": "departed", "title_last": "STAFF", "periods_observed": 1,
            "date_precision": "REPORT_PERIOD",
            "first_period": P3, "last_period": P3,
            "observed_periods": [P3], "observed_period_count": 1,
            "contiguous_runs": [[P3, P3]],
        })
        # RUNS_C: a day-level House row whose printed dates sit inside one quarter.
        runs_source_file = "sod_runs_%02d.pdf" % RUNS_C
        observation_rows.append({
            "source_file": runs_source_file, "source_row_index": obs_index,
            "service_start": "2019-02-01", "service_end": "2019-03-15",
        })
        span_rows.append({
            "spine_person_id": person_id(RUNS_C, rekeyed),
            "chamber": "house", "office_key": C8[1], "container_kind": C8[2],
            "member_bioguide": C8[3],
            "core": {"start_latest": "2019-02-01", "end_earliest": "2019-03-15"},
            "envelope": {"start_earliest": "2019-02-01", "end_latest": "2019-03-15"},
            "source_row_ids": ["house-ledger:%s#%d" % (runs_source_file, obs_index)],
            "source_lane": ["house_ledger"],
            "status": "departed", "title_last": "STAFF", "periods_observed": 1,
            "date_precision": "DAY",
            "first_period": "2019Q1", "last_period": "2019Q1",
            "observed_periods": ["2019Q1"], "observed_period_count": 1,
            "contiguous_runs": [["2019Q1", "2019Q1"]],
        })
        obs_index += 1
    if strip_runs:
        # The SAME world on an older spine: identical dates, no v7 period fields.
        for span in span_rows:
            for field in ("contiguous_runs", "observed_periods", "observed_period_count"):
                span.pop(field, None)
    # spans are read in FILE ORDER and --limit-persons takes a prefix of that order,
    # so sort by id to make the fixture's order explicit rather than incidental.
    span_rows.sort(key=lambda row: (row["spine_person_id"], row["office_key"]))
    write_gz(spine_dir / "hill_person_office_spans.ndjson.gz", span_rows)
    if not observation_rows:
        observation_rows = [{"source_file": "none", "source_row_index": 0}]
    write_gz(house_dir / "house_personnel_observations.ndjson.gz", observation_rows)
    write_gz(house_dir / "house_person_office_spans.ndjson.gz", [])
    write_json(house_dir / "house_personnel_ledger_receipt.json", {"status": "PASS"})

    write_gz(
        spine_dir / "member_office_index.ndjson.gz",
        [
            {
                "chamber": chamber,
                "office_key": office_key,
                "container_kind": kind,
                "member_bioguide": bioguide,
                "member_resolution_method": "FIXTURE",
                "observation_count": 10,
                "person_count": 10,
                "first_period": "2010Q1",
                "last_period": "2018Q4",
            }
            for chamber, office_key, kind, bioguide in containers
        ],
    )
    write_json(spine_dir / "hill_personnel_spine_receipt.json",
               {"status": "PASS", "dials": {"s_strength": {"S1": 0.75, "S2": 0.5, "S3": 0.25}}})
    write_gz(senate_dir / "senate_committee_payroll_candidates_2011_2026.ndjson.gz", [])
    write_json(senate_dir / "senate_committee_payroll_receipt.json", {"status": "PASS"})
    write_json(identity_dir / "house_person_identity_receipt.json", {"status": "PASS"})

    # ---- roster + the two hashed modules --------------------------------------
    write_json(root / "data" / "engine" / "congestion.json",
               [{"bioguide": bioguide, "chamber": chamber}
                for chamber, _key, _kind, bioguide in containers if bioguide])
    (root / "fec" / "staffer_network").mkdir(parents=True, exist_ok=True)
    for name in ("intervals.py", "network_math.py"):
        shutil.copyfile(ROOT / "fec" / "staffer_network" / name,
                        root / "fec" / "staffer_network" / name)
    # The verifier's naming law refuses to run without at least one glob call site.
    # This one is deliberately non-matching; the negative control below supplies a
    # matching one and proves the bar fires.
    (root / "fec" / "unrelated_selector.py").write_text(
        "import glob\n\n\ndef find(base):\n"
        "    return glob.glob(base + '/sentinel_batch_*.json')\n",
        encoding="utf-8",
    )
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "design.md").write_text("fixture design doc\n", encoding="utf-8")
    (docs / "metrics.md").write_text("fixture metrics doc\n", encoding="utf-8")

    # ---- Politico policy memberships + the bridge receipt ----------------------
    policy_rows = []
    for i in (0, 5, 10, 12, 20, 21):
        policy_rows.append({
            "spine_person_id": person_id(i, policy_rekeyed),
            "issue_area": ISSUE_A if i % 2 == 0 else ISSUE_B,
            "source_profile_uuid": "uuid-%02d" % i,
            "observed_at": "2026-08-30T00:00:00Z",
        })
    policy_path = politico_dir / "politico_policy_memberships.ndjson.gz"
    write_gz(policy_path, policy_rows)

    # ---- exploratory revolving edges ------------------------------------------
    revolving_rows = [
        {
            "layer": "revolving",
            "status": "GUARANTEED",
            "subject_id": "container:bioguide:A000001",
            "object_id": ORG_A,
            "w_tiered_lower": 0.4,
            "edge_id": "src:rev:1",
            "contributions": [{"lda_source_person_id": "lda:lobbyist:7000"}],
        },
        {
            "layer": "revolving",
            "status": "GUARANTEED",
            "subject_id": "container:office:senate:HON. BETA",
            "object_id": ORG_B,
            "w_tiered_lower": 0.2,
            "edge_id": "src:rev:2",
            "contributions": [{"lda_source_person_id": "lda:lobbyist:7020"}],
        },
        {
            "layer": "co_tenure",
            "status": "GUARANTEED",
            "subject_id": "person:ignored",
            "object_id": "person:ignored2",
            "w_tiered_lower": 1.0,
            "edge_id": "src:cot:1",
        },
    ]
    tier_path = tier_dir / "exploratory_temporal_edges.ndjson.gz"
    write_gz(tier_path, revolving_rows)

    return {
        "root": root,
        "spine_dir": spine_dir,
        "house_dir": house_dir,
        "senate_dir": senate_dir,
        "politico_dir": politico_dir,
        "policy_path": policy_path,
        "tier_path": tier_path,
        "design_doc": docs / "design.md",
        "metrics_doc": docs / "metrics.md",
    }


def write_link_receipt(world, bound_spine_dir):
    """Bridge receipt binding the policy file AND a named Hill spine generation."""
    spine_file = Path(bound_spine_dir) / "hill_person_spine.ndjson.gz"
    receipt = {
        "identity_rule": "fixture exact name plus current office",
        "counts": {"status": {"LINKED": 6}, "unique_linked_spine_people": 6},
        "negative_control": {"rate": 0.0, "acceptance_bar_lt_0_01": True},
        "files": {
            world["policy_path"].name: {
                "sha256": sha256_file(world["policy_path"]),
                "bytes": world["policy_path"].stat().st_size,
                "rows": 6,
            }
        },
        "inputs": {
            str(spine_file): {
                "sha256": sha256_file(spine_file),
                "bytes": spine_file.stat().st_size,
            }
        },
    }
    path = world["politico_dir"] / "politico_payroll_direct_link_receipt.json"
    write_json(path, receipt)
    return path


def run_builder(world, link_receipt, out, extra=()):
    argv = [
        sys.executable, str(BUILDER),
        "--repository-root", str(world["root"]),
        "--hill-spine-root", str(world["spine_dir"]),
        "--house-ledger-root", str(world["house_dir"]),
        "--senate-committee-root", str(world["senate_dir"]),
        "--politico-direct-policy-memberships", str(world["policy_path"]),
        "--politico-direct-link-receipt", str(link_receipt),
        "--exploratory-revolving-edges", str(world["tier_path"]),
        "--design-doc", str(world["design_doc"]),
        "--metrics-doc", str(world["metrics_doc"]),
        "--generated-at", GENERATED_AT,
        "--output", str(out),
        "--no-glassbox",
    ] + list(extra)
    env = dict(os.environ)
    env.pop("MONEYBALL_ROOT", None)
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def run_verifier(root, primary, twin, out):
    argv = [sys.executable, str(VERIFIER), "--primary", str(primary), "--twin", str(twin),
            "--output", str(out), "--repository-root", str(root)]
    env = dict(os.environ)
    env.pop("MONEYBALL_ROOT", None)
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


# --------------------------------------------------------------------------- checks
def check_pure_functions():
    """office_diversity_simpson, hand-computed."""
    import numpy as np

    # p0 ties: to p1 through container 0 (w=1) and to p2 through container 1 (w=1).
    ga = np.array([0, 0], dtype=np.int64)
    gb = np.array([1, 2], dtype=np.int64)
    w = np.array([1.0, 1.0], dtype=np.float64)
    ptr = np.array([0, 1, 2], dtype=np.int64)
    flat = np.array([0, 1], dtype=np.int64)
    diversity, distinct, _mass = subject.office_diversity_simpson(ga, gb, w, ptr, flat, 3, 2,
                                                               container_shares=[1., 1.])
    # p0 mass is 0.5 / 0.5 across two offices -> raw Simpson 0.5, K=2 -> 0.5/(1-1/2)=1.0
    ok("office_diversity: a person split evenly across two offices scores 1.0",
       abs(diversity[0] - 1.0) < 1e-12, diversity[0])
    ok("office_diversity: a person whose ties all come from one office scores 0.0",
       abs(diversity[1]) < 1e-12 and abs(diversity[2]) < 1e-12,
       (diversity[1], diversity[2]))
    eq("office_diversity: distinct office counts", list(distinct), [2, 1, 1])

    ga2 = np.array([0], dtype=np.int64)
    gb2 = np.array([1], dtype=np.int64)
    diversity2, distinct2, _ = subject.office_diversity_simpson(
        ga2, gb2, np.array([1.0]), np.array([0, 1], dtype=np.int64),
        np.array([0], dtype=np.int64), 3, 1, container_shares=[1.])
    ok("office_diversity: isolated people score zero", abs(diversity2[2]) < 1e-12, diversity2[2])
    ok("office_diversity: a single-office generation cannot bridge",
       float(diversity2.max()) == 0.0, diversity2)
    eq("office_diversity: isolates report zero distinct offices", int(distinct2[2]), 0)

    ok("the retired kind-Simpson statistic is not imported by the builder",
       not hasattr(subject, "bridging_diversity"), "bridging_diversity is still bound")
    ok("group_balanced_teleport is not imported by the builder",
       not hasattr(subject, "group_balanced_teleport"),
       "group_balanced_teleport is still bound")
    eq("the operator node universe declares one kind", subject.NODE_KINDS, ("person",))
    eq("the operator layer set is co_tenure alone", subject.OPERATOR_LAYERS, (0,))
    ok("the config name is the duration-attributed generation",
       subject.CONFIG_NAME == "staffer_social_network_20260908_v7_math", subject.CONFIG_NAME)
    ok("the rule version names the persons-only rework",
       subject.RULE_VERSION == "staffer-social-network-v5-duration-attributed", subject.RULE_VERSION)
    ok("the two open rulings are declared pending",
       sorted(entry["id"] for entry in subject.PENDING_RULINGS)
       == ["container_size_normalisation", "decay"],
       [entry["id"] for entry in subject.PENDING_RULINGS])
    ok("no pending ruling is silently applied",
       all(entry["applied_in_this_build"] is False for entry in subject.PENDING_RULINGS),
       subject.PENDING_RULINGS)


def check_generation(workspace):
    world = build_world(workspace / "world_v6")
    link_receipt = write_link_receipt(world, world["spine_dir"])
    primary = workspace / "gen_primary"
    twin = workspace / "gen_twin"

    code, output = run_builder(world, link_receipt, primary)
    ok("the fixture build succeeds", code == 0, output[-2500:])
    if code != 0:
        return None
    code_twin, output_twin = run_builder(world, link_receipt, twin)
    ok("the twin build succeeds", code_twin == 0, output_twin[-2500:])

    receipt = json.loads((primary / "staffer_social_network_receipt.json").read_text(encoding="utf-8"))
    nodes = read_gz(primary / "staffer_social_node_scores.ndjson.gz")
    edges = read_gz(primary / "staffer_social_edges.ndjson.gz")
    membership = read_gz(primary / "staffer_social_membership_layer.ndjson.gz")
    revolving = read_gz(primary / "staffer_social_revolving_layer.ndjson.gz")
    policy = read_gz(primary / "staffer_social_policy_layer.ndjson.gz")
    groups = read_gz(primary / "staffer_social_groups.ndjson.gz")

    named = receipt["named_regression_checks"]
    ok("the named regression gate stays hard and passes on the fixture",
       named["Gordon_Holzberg"]["status"] == "PASS"
       and named["Stephanie_Belk"]["status"] == "PASS", named)
    eq("the fixture reproduces the abutting move the gate asserts",
       (named["Gordon_Holzberg"]["move_count"],
        named["Gordon_Holzberg"]["moves"][0]["gap_bucket"]), (1, "ABUTTING"))

    # ---- persons only ---------------------------------------------------------
    eq("node_scores holds exactly one row per person", len(nodes), PEOPLE)
    eq("node_scores holds only person rows",
       sorted({row["node_kind"] for row in nodes}), ["person"])
    ok("no container id appears in node_scores",
       not any("container" in row["node_id"] for row in nodes),
       [row["node_id"] for row in nodes if "container" in row["node_id"]][:3])
    ok("no organisation or policy id appears in node_scores",
       not any(row["node_id"].startswith(("organization:", "policy:")) for row in nodes),
       [row["node_id"] for row in nodes if not row["node_id"].startswith("person:")][:3])
    eq("the receipt's node universe is the person universe",
       (receipt["node_universe"]["person"], receipt["node_universe"]["total"]), (PEOPLE, PEOPLE))
    eq("the receipt reports zero non-person nodes", receipt["operator"]["non_person_nodes"], 0)
    ok("the receipt no longer counts containers as nodes",
       "container" not in receipt["node_universe"], receipt["node_universe"].keys())
    eq("the group universe carries every fixture office",
       receipt["group_universe"]["container"], len(CONTAINERS))
    eq("the group universe carries both organisations",
       receipt["group_universe"]["organization"], 2)
    eq("the group universe carries both policy areas",
       receipt["group_universe"]["policy_area"], 2)

    # ---- the edges file is the operator ---------------------------------------
    eq("the edges file carries co_tenure rows only",
       sorted({row["layer"] for row in edges}), ["co_tenure"])
    ok("every edge is person to person",
       all(row["subject_kind"] == "person" and row["object_kind"] == "person"
           for row in edges), "a non-person endpoint reached the edges file")
    ok("every edge id is a person node id",
       all(row["subject_id"].startswith("person:") and row["object_id"].startswith("person:")
           for row in edges), "a non-person id reached the edges file")

    # ---- OVERLAP: non-overlapping same-office pairs get no edge ----------------
    early = {"person:" + person_id(i) for i in range(0, 5)}
    late = {"person:" + person_id(i) for i in range(5, 10)}
    crossing = [row for row in edges
                if (row["subject_id"] in early and row["object_id"] in late)
                or (row["subject_id"] in late and row["object_id"] in early)]
    eq("the 2010 and 2016 cohorts of one office share no edge at all", len(crossing), 0)
    guaranteed = [row for row in edges if row["status"] == "GUARANTEED"]
    ok("every guaranteed edge has strictly positive overlap days",
       all(row["guaranteed_days"] > 0 for row in guaranteed), "a zero-day edge was emitted")
    rejected = receipt["edge_counts"]["co_tenure_zero_overlap_REJECTED_never_emitted"]
    ok("the zero-overlap rejection count is live", rejected > 0, rejected)
    eq("the 25 cross-cohort pairs are the rejected ones", rejected, 25)
    eq("the overlap-rule block reports the same rejection count",
       receipt["overlap_rule_check"]["pairs_REJECTED_for_no_overlap"], rejected)

    # ---- grouping layers -------------------------------------------------------
    eq("membership rows equal the person-container cells",
       len(membership), receipt["episodes"]["person_container_cells"])
    ok("every grouping row declares itself out of the operator",
       all(row["operator_included"] is False and row["operator_weight"] == 0.0
           for row in membership + revolving + policy),
       "a grouping row claimed operator inclusion")
    ok("no grouping row carries an operator weight field",
       all("w_tiered_lower" not in row for row in membership + policy),
       "a grouping row still carries w_tiered_lower")
    eq("the policy layer carries every joined membership", len(policy), 6)
    eq("the revolving layer carries both container->organisation paths",
       len([row for row in revolving if row["record_kind"] == "container_organization"]), 2)
    ok("the revolving layer carries per-person registrant records",
       len([row for row in revolving if row["record_kind"] == "person_registrant"]) >= 2,
       [row["record_kind"] for row in revolving])
    eq("the groups roster covers containers, organisations and policy areas",
       sorted({row["group_kind"] for row in groups}),
       ["container", "organization", "policy_area"])
    eq("the groups roster has one row per group", len(groups), len(CONTAINERS) + 2 + 2)
    ok("every group row carries the five required fields",
       all(set(("group_id", "group_kind", "name", "member_bioguide", "people_count"))
           <= set(row) for row in groups), "a group row is missing a required field")
    alpha = [row for row in groups if row["group_id"].endswith("HON. ALPHA")]
    eq("the House personal office group names its member", len(alpha), 1)
    eq("the House personal office group counts its ten people",
       alpha[0]["people_count"], 10)
    eq("the House personal office group carries its bioguide",
       alpha[0]["member_bioguide"], "A000001")
    conservation = receipt["grouping_layers"]
    ok("the receipt declares every grouping layer conserved",
       all(conservation[name]["conserved"] for name in
           ("membership", "revolving", "policy_area", "groups")), conservation)
    eq("the receipt reports zero grouping rows in the operator",
       conservation["operator_rows_in_grouping_layers"], 0)

    # ---- office_diversity ------------------------------------------------------
    by_id = {row["node_id"]: row for row in nodes}
    ok("the retired bridging_diversity field is gone from every node row",
       all("bridging_diversity_exploratory" not in row for row in nodes),
       "bridging_diversity_exploratory is still emitted")
    ok("every node row carries office_diversity",
       all("office_diversity_exploratory" in row for row in nodes), "missing field")
    single = by_id["person:" + person_id(0)]
    eq("a person who only ever worked in one office scores zero office diversity",
       single["office_diversity_exploratory"], 0.0)
    eq("that person reports one distinct office",
       single["office_diversity_distinct_offices"], 1)
    bridge = by_id["person:" + person_id(10)]
    ok("a person whose ties come from two offices scores above zero",
       bridge["office_diversity_exploratory"] > 0.0, bridge["office_diversity_exploratory"])
    eq("that person reports two distinct offices",
       bridge["office_diversity_distinct_offices"], 2)
    ok("office diversity never exceeds one",
       all(0.0 <= row["office_diversity_exploratory"] <= 1.0 for row in nodes),
       "a value fell outside [0, 1]")
    eq("the receipt records the group count used for normalisation",
       receipt["office_diversity"]["group_count_K_distinct_containers"], len(CONTAINERS))
    ok("the receipt states what office_diversity replaced",
       "bridging_diversity" in receipt["office_diversity"]["replaces"],
       receipt["office_diversity"]["replaces"])

    # ---- uniform teleport ------------------------------------------------------
    teleport = receipt["operator"]["teleport"]
    eq("the restart distribution is uniform over persons", teleport["kind"],
       "uniform_over_persons")
    ok("each person receives exactly 1/N of the restart mass",
       abs(teleport["mass_per_person"] - 1.0 / PEOPLE) < 1e-15, teleport["mass_per_person"])
    ok("the restart distribution sums to one", teleport["sums_to_one"] is True, teleport)

    # ---- components ------------------------------------------------------------
    histogram = receipt["operator"]["component_size_histogram"]
    ok("the component size histogram is present", bool(histogram), histogram)
    eq("the histogram accounts for every person",
       sum(int(size) * count for size, count in histogram.items()), PEOPLE)
    eq("the histogram row count equals the component count",
       sum(histogram.values()), receipt["operator"]["components"])
    ok("the persons-only operator separates into more than one colony",
       receipt["operator"]["components"] > 1, receipt["operator"]["components"])

    # ---- promotion counters ----------------------------------------------------
    for field in ("canonical_employment_promotions", "canonical_identity_promotions",
                  "canonical_office_promotions", "canonical_person_merges",
                  "primary_weights_emitted", "date_promotions"):
        eq("promotion counter %s is pinned at zero" % field, receipt[field], 0)

    # ---- pending rulings -------------------------------------------------------
    eq("the receipt names the two open rulings",
       sorted(entry["id"] for entry in receipt["pending_rulings"]),
       ["container_size_normalisation", "decay"])

    # ---- FALLBACK: this world's spine carries no contiguous_runs ----------------
    trajectories = read_gz(primary / "staffer_trajectories.ndjson.gz")
    all_stints = [stint for row in trajectories for stint in row["stints"]]
    census = receipt["stint_census"]
    ok("a spine WITHOUT contiguous_runs stamps stints_from_contiguous_runs false",
       receipt["stints_from_contiguous_runs"] is False, census)
    eq("the fallback census counts every span as one run",
       (census["spans"], census["stints"], census["spans_with_multiple_runs"]),
       (len(stints()), len(stints()), 0))
    eq("the fallback census reports no span carrying the field",
       census["spans_with_contiguous_runs_field"], 0)
    ok("every fallback stint is run 0 of 1",
       all(stint["stint_run_index"] == 0 and stint["run_count_for_span"] == 1
           for stint in all_stints), all_stints[:3])
    eq("the trajectory census closes against the receipt",
       (len(all_stints), sum(row["span_count"] for row in trajectories)),
       (receipt["trajectories"]["stint_rows"], receipt["trajectories"]["span_rows"]))

    # ---- twin identity ---------------------------------------------------------
    if code_twin == 0:
        differing = []
        for entry in receipt["files"]:
            if sha256_file(primary / entry["path"]) != sha256_file(twin / entry["path"]):
                differing.append(entry["path"])
        eq("every payload file is byte-identical in the twin", differing, [])
        left = json.loads((primary / "staffer_social_network_receipt.json").read_text(encoding="utf-8"))
        right = json.loads((twin / "staffer_social_network_receipt.json").read_text(encoding="utf-8"))
        drift = {k for k in set(left) | set(right) if left.get(k) != right.get(k)}
        ok("the twin receipt differs in nothing but generated_at at most",
           drift <= {"generated_at"}, sorted(drift))

    # ---- the shipped verifier --------------------------------------------------
    vcode, voutput = run_verifier(world["root"], primary, twin, workspace / "verification")
    ok("the shipped verifier passes the fixture generation", vcode == 0, voutput[-3000:])
    verification = json.loads(
        (workspace / "verification" / "staffer_social_network_verification_receipt.json")
        .read_text(encoding="utf-8"))
    eq("the verifier records no failures", verification["failures"], [])
    ok("the verifier confirms node_scores is persons only",
       verification["node_scores_are_persons_only"]["match"],
       verification["node_scores_are_persons_only"])
    ok("the verifier re-derives office_diversity independently",
       verification["independent_office_diversity"]["match"],
       verification["independent_office_diversity"])
    ok("the verifier confirms the grouping layers are conserved",
       verification["grouping_layer_conservation"]["membership_conserved"],
       verification["grouping_layer_conservation"])
    ok("the verifier confirms the zero-overlap rejection fires",
       verification["zero_overlap_rejection_is_live"]["match"],
       verification["zero_overlap_rejection_is_live"])
    ok("the verifier confirms the component histogram closes",
       verification["component_histogram"]["match"], verification["component_histogram"])
    ok("the verifier confirms the uniform restart",
       verification["uniform_teleport"]["match"], verification["uniform_teleport"])
    ok("the verifier's naming law finds no live selector",
       verification["naming_law"]["verdict"].startswith("PASS"),
       verification["naming_law"]["verdict"])
    return world, link_receipt, primary


def check_contiguous_runs(workspace):
    """OWNER RULING 2026-09-07: a REPORT_PERIOD stint is the UNION of observed periods.

    Two builds over the SAME dates and the SAME people:
      * ``runs``   - the v7 spine, contiguous_runs present
      * ``legacy`` - the identical world with the v7 fields stripped
    The only difference between them is the field, so any behaviour that differs is
    caused by the ruling and nothing else.  The legacy build is the negative control:
    it must still emit the fabricated GUARANTEED edge the ruling exists to remove.
    """
    world = build_world(workspace / "world_runs", runs=True)
    link_receipt = write_link_receipt(world, world["spine_dir"])
    primary = workspace / "runs_primary"
    twin = workspace / "runs_twin"
    code, output = run_builder(world, link_receipt, primary)
    ok("the contiguous-runs world builds", code == 0, output[-2500:])
    if code != 0:
        return
    code_twin, output_twin = run_builder(world, link_receipt, twin)
    ok("the contiguous-runs twin builds", code_twin == 0, output_twin[-2000:])

    receipt = json.loads((primary / "staffer_social_network_receipt.json").read_text(encoding="utf-8"))
    edges = read_gz(primary / "staffer_social_edges.ndjson.gz")
    trajectories = {row["spine_person_id"]: row
                    for row in read_gz(primary / "staffer_trajectories.ndjson.gz")}
    census = receipt["stint_census"]

    ok("a spine WITH contiguous_runs stamps stints_from_contiguous_runs true",
       receipt["stints_from_contiguous_runs"] is True, census)
    eq("the census counts one span with more than one run", census["spans_with_multiple_runs"], 1)
    eq("the census counts one more stint than spans", census["stints"] - census["spans"], 1)
    eq("the census reports no period-boundary mismatch", census["period_boundary_mismatches"], 0)
    ok("the census names the largest span-to-run reduction",
       census["largest_span_to_run_reductions"][0]["spine_person_id"] == person_id(RUNS_A)
       and census["largest_span_to_run_reductions"][0]["reduction_days"] > 0,
       census["largest_span_to_run_reductions"][:2])

    # ---- P1, P2, _, P4 becomes TWO stints --------------------------------------
    gapped = trajectories[person_id(RUNS_A)]
    eq("periods P1 P2 _ P4 in one office produce two stints", gapped["stint_count"], 2)
    eq("...from a single office span", gapped["span_count"], 1)
    eq("...indexed 0 and 1 of the same span",
       [(stint["stint_run_index"], stint["run_count_for_span"]) for stint in gapped["stints"]],
       [(0, 2), (1, 2)])
    eq("the first stint ends with P2 instead of running to the end of the span",
       (gapped["stints"][0]["core_start"], gapped["stints"][0]["core_end_exclusive"]),
       (P1, P3))
    eq("the second stint is P4 alone",
       (gapped["stints"][1]["core_start"], gapped["stints"][1]["core_end_exclusive"]),
       (P4, "2014-04-01"))
    ok("both stints declare the contiguous_runs basis",
       {stint["run_basis"] for stint in gapped["stints"]} == {"contiguous_runs"},
       gapped["stints"])
    eq("the P3-only colleague keeps one stint",
       trajectories[person_id(RUNS_B)]["stint_count"], 1)

    # ---- and therefore no co-tenure with the person who was only there in P3 ----
    pair = {"person:" + person_id(RUNS_A), "person:" + person_id(RUNS_B)}
    shared = [row for row in edges if {row["subject_id"], row["object_id"]} == pair]
    ok("the gapped person and the P3 person share NO guaranteed co-tenure",
       all(row["status"] != "GUARANTEED" for row in shared),
       [(row["status"], row["guaranteed_days"]) for row in shared])
    ok("any surviving envelope edge between them carries zero operator weight",
       all(row["w_tiered_lower"] == 0.0 and row["guaranteed_days"] == 0 for row in shared),
       [(row["status"], row["w_tiered_lower"]) for row in shared])

    # ---- a day-level House row keeps its exact printed dates --------------------
    dayrow = trajectories[person_id(RUNS_C)]
    eq("a day-level House row stays one stint in one run",
       (dayrow["stint_count"], dayrow["stints"][0]["run_count_for_span"]), (1, 1))
    eq("...keeping its exact printed service dates",
       (dayrow["stints"][0]["core_start"], dayrow["stints"][0]["core_end_exclusive"]),
       ("2019-02-01", "2019-03-16"))
    eq("...on the day-interval basis at DAY precision",
       (dayrow["stints"][0]["run_basis"], dayrow["stints"][0]["date_precision"]),
       ("day_intervals", "DAY"))

    # ---- NEGATIVE CONTROL: the same world on an older spine ---------------------
    legacy_world = build_world(workspace / "world_runs_legacy", runs=True, strip_runs=True)
    legacy_receipt_path = write_link_receipt(legacy_world, legacy_world["spine_dir"])
    legacy = workspace / "runs_legacy_primary"
    legacy_code, legacy_output = run_builder(legacy_world, legacy_receipt_path, legacy)
    ok("the legacy-spine control builds", legacy_code == 0, legacy_output[-2000:])
    if legacy_code == 0:
        legacy_json = json.loads(
            (legacy / "staffer_social_network_receipt.json").read_text(encoding="utf-8"))
        legacy_edges = read_gz(legacy / "staffer_social_edges.ndjson.gz")
        legacy_shared = [row for row in legacy_edges
                         if {row["subject_id"], row["object_id"]} == pair]
        ok("NEGATIVE CONTROL: without contiguous_runs the receipt stamps false",
           legacy_json["stints_from_contiguous_runs"] is False, legacy_json["stint_census"])
        ok("NEGATIVE CONTROL: min-to-max DOES fabricate the guaranteed edge the ruling removes",
           len(legacy_shared) == 1 and legacy_shared[0]["status"] == "GUARANTEED"
           and legacy_shared[0]["guaranteed_days"] > 0,
           [(row["status"], row["guaranteed_days"]) for row in legacy_shared])
        eq("NEGATIVE CONTROL: min-to-max collapses the gapped person to one stint",
           len(read_gz(legacy / "staffer_trajectories.ndjson.gz")) and
           [row["stint_count"] for row in read_gz(legacy / "staffer_trajectories.ndjson.gz")
            if row["spine_person_id"] == person_id(RUNS_A)], [1])

    # ---- the shipped verifier -------------------------------------------------
    if code_twin != 0:
        return
    vcode, voutput = run_verifier(world["root"], primary, twin, workspace / "runs_verification")
    ok("the shipped verifier passes the contiguous-runs generation", vcode == 0, voutput[-3000:])
    verification = json.loads(
        (workspace / "runs_verification" / "staffer_social_network_verification_receipt.json")
        .read_text(encoding="utf-8"))
    eq("the verifier records no failures on the runs generation", verification["failures"], [])
    stint_runs = verification["stint_runs"]
    eq("the verifier re-derives the stint total from the spans file",
       (stint_runs["verifier_stints_from_spans_file"], stint_runs["trajectory_stint_rows"]),
       (census["stints"], census["stints"]))
    eq("the verifier finds no stint outside a contiguous run",
       stint_runs["stints_outside_any_contiguous_run"], 0)
    eq("the verifier finds no run-index violation", stint_runs["stint_index_violations"], 0)
    ok("the verifier agrees the spine supplied contiguous_runs",
       stint_runs["verifier_stints_from_contiguous_runs"] is True, stint_runs)

    # ---- NEGATIVE CONTROL: the verifier's containment check can actually fail ---
    tampered = workspace / "runs_tampered"
    shutil.copytree(primary, tampered)
    rewritten = []
    for row in read_gz(primary / "staffer_trajectories.ndjson.gz"):
        if row["spine_person_id"] == person_id(RUNS_A):
            first, last = row["stints"][0], row["stints"][-1]
            collapsed = dict(first)
            collapsed["core_end_exclusive"] = last["core_end_exclusive"]
            collapsed["run_count_for_span"] = 1
            row = dict(row, stints=[collapsed], stint_count=1)
        rewritten.append(row)
    write_gz(tampered / "staffer_trajectories.ndjson.gz", rewritten)
    tcode, _toutput = run_verifier(world["root"], tampered, twin, workspace / "runs_tampered_ver")
    tampered_result = json.loads(
        (workspace / "runs_tampered_ver" / "staffer_social_network_verification_receipt.json")
        .read_text(encoding="utf-8"))
    tampered_checks = {entry["check"] for entry in tampered_result["failures"]}
    ok("NEGATIVE CONTROL: a stint put back across the gap fails the verifier",
       tcode != 0 and "stint_outside_its_contiguous_run" in tampered_checks,
       sorted(tampered_checks))
    ok("NEGATIVE CONTROL: the stint total no longer matches the run total",
       "stint_count_is_not_the_sum_of_run_counts" in tampered_checks,
       sorted(tampered_checks))


def check_naming_law_negative_control(workspace):
    """A selector that COULD reach the new generic artifact names must fail the law."""
    world = build_world(workspace / "world_neg")
    link_receipt = write_link_receipt(world, world["spine_dir"])
    primary = workspace / "neg_primary"
    twin = workspace / "neg_twin"
    if run_builder(world, link_receipt, primary)[0] != 0:
        ok("negative control build succeeds", False, "build failed")
        return
    run_builder(world, link_receipt, twin)
    # The selector is assembled from fragments so that THIS file never itself contains
    # a literal the naming law would read as a live selector: a test fixture must not
    # fail a real generation's verification just by existing in the tree.
    # Retargeted 2026-09-07 to the renamed artifact (staffer_social_groups.ndjson.gz);
    # the bare "groups" fragment predates that rename and no longer reaches anything.
    selector = "'/" + "staffer_social_groups" + "*.ndjson.gz'"
    (world["root"] / "fec" / "greedy_selector.py").write_text(
        "\n".join([
            "import glob",
            "",
            "",
            "STAFFER_ROOT = 'audits/staffer_network'",
            "",
            "",
            "def find():",
            "    return glob.glob(STAFFER_ROOT + " + selector + ")",
            "",
        ]),
        encoding="utf-8",
    )
    code, _output = run_verifier(world["root"], primary, twin, workspace / "neg_verification")
    verification = json.loads(
        (workspace / "neg_verification" / "staffer_social_network_verification_receipt.json")
        .read_text(encoding="utf-8"))
    ok("NEGATIVE CONTROL: a live glob selector for staffer_social_groups*.ndjson.gz fails the naming law",
       code != 0 and any(entry["check"].startswith("naming_law")
                         for entry in verification["failures"]),
       verification["naming_law"]["verdict"])


def check_retained_audit_rule_never_exempts_fec(workspace):
    """The 2026-09-07 retained_audit_or_backup ruling covers audits/ snapshots only.

    A synthetic fec/ reference shaped exactly like the new exemptions - a "before"
    snapshot segment AND a seal.py basename - must still fail the law. If this ever
    passes, the rule has leaked out of audits/ and live code is no longer governed.
    """
    world = build_world(workspace / "world_fec_guard")
    link_receipt = write_link_receipt(world, world["spine_dir"])
    primary = workspace / "fec_guard_primary"
    twin = workspace / "fec_guard_twin"
    if run_builder(world, link_receipt, primary)[0] != 0:
        ok("fec-guard build succeeds", False, "build failed")
        return
    run_builder(world, link_receipt, twin)
    # Deliberately shaped like the two new audits/-only exemptions (a "before" segment
    # and a seal.py basename) but rooted under fec/, where the ruling must not reach.
    decoy = world["root"] / "fec" / "staffer_network" / "before" / "seal.py"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text(
        "# Shaped like an audits/ retained-audit path (a 'before' segment, a seal.py\n"
        "# basename) but lives under fec/, where the 2026-09-07 ruling must not reach.\n"
        "STAFFER_SOCIAL_ARTIFACT = 'staffer_social_groups.ndjson.gz'\n",
        encoding="utf-8",
    )
    code, _output = run_verifier(world["root"], primary, twin, workspace / "fec_guard_verification")
    verification = json.loads(
        (workspace / "fec_guard_verification" / "staffer_social_network_verification_receipt.json")
        .read_text(encoding="utf-8"))
    relative = "fec/staffer_network/before/seal.py"
    ok("POSITIVE CHECK: a fec/ reference shaped like a retained-audit path still fails the law",
       code != 0 and relative in verification["naming_law"]["files_already_referencing_staffer_social"],
       verification["naming_law"])


def check_limit_persons(workspace):
    world = build_world(workspace / "world_limit")
    link_receipt = write_link_receipt(world, world["spine_dir"])
    out = workspace / "limited"
    code, output = run_builder(world, link_receipt, out, extra=["--limit-persons", "10"])
    ok("a limited run succeeds", code == 0, output[-2000:])
    if code != 0:
        return
    receipt = json.loads((out / "staffer_social_network_receipt.json").read_text(encoding="utf-8"))
    eq("the limited run admits exactly the requested people",
       receipt["node_universe"]["person"], 10)
    ok("the limited run declares itself limited",
       receipt["limited_run"]["limited_run"] is True, receipt["limited_run"])
    ok("the limited run carries a not-representative warning",
       "SMOKE TEST ONLY" in (receipt["limited_run"]["warning"] or ""),
       receipt["limited_run"]["warning"])
    ok("the full-population gates are downgraded to warnings, never dropped",
       receipt["status"] == "PASS" and receipt["failures"] == []
       and all(name.startswith("LIMITED_RUN_") for name in receipt["warnings"]),
       (receipt["status"], receipt["failures"], receipt["warnings"]))
    nodes = read_gz(out / "staffer_social_node_scores.ndjson.gz")
    eq("the limited run emits one node per admitted person", len(nodes), 10)


def check_spine_id_map(workspace):
    """The Politico bridge's ids live in the spine generation it bound."""
    old_world = build_world(workspace / "world_old")
    link_receipt = write_link_receipt(old_world, old_world["spine_dir"])

    # The bridge was built against the OLD spine, so its policy rows carry LEGACY ids
    # while the spine this build reads carries the re-keyed ones.
    new_world = build_world(workspace / "world_new", rekeyed=True, policy_rekeyed=False)
    # The bridge receipt still binds the OLD spine, so the new build must refuse it.
    stale_receipt = new_world["politico_dir"] / "politico_payroll_direct_link_receipt.json"
    payload = json.loads(link_receipt.read_text(encoding="utf-8"))
    payload["files"] = {
        new_world["policy_path"].name: {
            "sha256": sha256_file(new_world["policy_path"]),
            "bytes": new_world["policy_path"].stat().st_size,
        }
    }
    write_json(stale_receipt, payload)

    code, output = run_builder(new_world, stale_receipt, workspace / "remap_refused")
    ok("a bridge bound to another spine generation is refused without a map",
       code != 0 and "spine-id-map" in output, output[-1200:])

    complete = workspace / "spine_id_map_complete.ndjson.gz"
    write_gz(complete, [
        {"legacy_person_id": person_id(i), "person_id": person_id(i, True),
         "reason": "NAME_STANDARD_REKEY"}
        for i in sorted(REKEYED)
    ])
    out = workspace / "remap_applied"
    code, output = run_builder(new_world, stale_receipt, out,
                               extra=["--spine-id-map", str(complete)])
    ok("a complete map lets the build proceed", code == 0, output[-2000:])
    if code == 0:
        receipt = json.loads((out / "staffer_social_network_receipt.json").read_text(encoding="utf-8"))
        binding = receipt["spine_id_binding"]
        ok("the receipt records that the remap was applied",
           binding["remap_applied"] is True, binding)
        eq("the receipt records how many ids were remapped",
           binding["politico_spine_ids_remapped"], 3)
        eq("no id is left unmapped", binding["politico_spine_ids_unmapped"], 0)
        eq("the remapped policy memberships still join", len(read_gz(out / "staffer_social_policy_layer.ndjson.gz")), 6)
        policy_subjects = {row["subject_id"] for row in read_gz(out / "staffer_social_policy_layer.ndjson.gz")}
        ok("a remapped person is joined under the NEW id",
           "person:" + person_id(5, True) in policy_subjects, sorted(policy_subjects))
        ok("the legacy id is nowhere in the emitted rows",
           "person:" + person_id(5) not in policy_subjects, sorted(policy_subjects))

    incomplete = workspace / "spine_id_map_incomplete.ndjson.gz"
    write_gz(incomplete, [
        {"legacy_person_id": person_id(i), "person_id": person_id(i, True),
         "reason": "NAME_STANDARD_REKEY"}
        for i in sorted(REKEYED) if i != 5
    ])
    code, output = run_builder(new_world, stale_receipt, workspace / "remap_incomplete",
                               extra=["--spine-id-map", str(incomplete)])
    ok("an id that is neither mapped nor current fails the build loudly",
       code != 0 and "neither listed in --spine-id-map" in output, output[-1200:])

    broken = workspace / "spine_id_map_broken.ndjson.gz"
    write_gz(broken, [{"legacy_person_id": person_id(5), "person_id": "hill:person:nope",
                       "reason": "NAME_STANDARD_REKEY"}])
    code, output = run_builder(new_world, stale_receipt, workspace / "remap_broken",
                               extra=["--spine-id-map", str(broken)])
    ok("a map that points at a person absent from the spine fails the build",
       code != 0 and "absent" in output, output[-1200:])

    # A map supplied when none is needed must be validated but NOT applied.
    same_world = build_world(workspace / "world_same", rekeyed=True)
    fresh_receipt = write_link_receipt(same_world, same_world["spine_dir"])
    out2 = workspace / "remap_unnecessary"
    code, output = run_builder(same_world, fresh_receipt, out2,
                               extra=["--spine-id-map", str(complete)])
    ok("an unnecessary map is accepted but not applied", code == 0, output[-1500:])
    if code == 0:
        binding = json.loads(
            (out2 / "staffer_social_network_receipt.json").read_text(encoding="utf-8")
        )["spine_id_binding"]
        ok("the receipt says the map was supplied and deliberately not applied",
           binding["spine_id_map_supplied"] is True and binding["remap_applied"] is False
           and "corrupt" in (binding["remap_not_applied_reason"] or ""), binding)


def main():
    workspace = Path(tempfile.mkdtemp(prefix="staffer_social_persons_only_"))
    try:
        check_pure_functions()
        check_generation(workspace)
        check_contiguous_runs(workspace)
        check_limit_persons(workspace)
        check_spine_id_map(workspace)
        check_naming_law_negative_control(workspace)
        check_retained_audit_rule_never_exempts_fec(workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    print("\n%d passed, %d failed" % (P, F))
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
