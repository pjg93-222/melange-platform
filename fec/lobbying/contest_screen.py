# -*- coding: utf-8 -*-
"""Build the CU x fintech under-contested screen for the industry surface.

Paul's directive 2026-08-05: "Add the CU x fintech under-contested screen
to the dashboard." The screen is the DESCRIPTIVE residual lens from the
round-3 declared look (partial rho +0.129, perm p .0007, robust in both
chambers and both split halves): members whose credit-union/fintech
dual-bill exposure predicts more independent two-sector contest than is
observed.

The 2026-08-05 interrogation is baked into the shipped semantics:
  - every listed member pairs HEAVY credit-union attention with a near-zero
    fintech counter-presence, so the honest reading is "CU-held members on
    dual-sector bills that fintech has not (yet) entered" - an asymmetry
    screen, never a symmetric battleground list;
  - members with ZERO shared bills are excluded (pure one-sidedness ranks
    for a different reason than the screen names);
  - most driving bills are current-congress, and filings run quarterly, so
    each row carries its 119th-Congress share as a lag flag;
  - each row carries its bootstrap stability (share of 1,000 member
    resamples, seed 11731, keeping the member in the top-15).

SCREEN, NOT A SCORE: consumed only by the industry page for display;
no engine, rank, or receptivity input flows from this artifact.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # noqa: E501,E702

import argparse
import collections
import csv
import json
import math
import os
import random
import re
import sys
import zipfile
from datetime import datetime, timezone

from paths import ROOT as MB_ROOT, LOCAL_MELANGE
import members.historical_member_names as identity

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = "contest-screen-v1"
PAIR = ("credit_union", "fintech")
YEARS = tuple(range(2021, 2027))
STRICT = frozenset({"formal_strict", "both_strict", "title_strict"})
FINANCE_COMMITTEES = frozenset({"HSBA", "SSBK", "HSAG", "SSAF"})
PORTFOLIO_FLOOR = 5
BOOTSTRAP_REPLICATES = 1000
BOOTSTRAP_SEED = 11731
TOP_ROWS = 15
STAGE_LAW = ("BECAME PUBLIC LAW", "BECAME PRIVATE LAW")
STAGE_HOUSE = "PASSED/AGREED TO IN HOUSE"
STAGE_SENATE = "PASSED/AGREED TO IN SENATE"
STAGE_COMMITTEE = (
    "REPORTED BY THE COMMITTEE",
    "REPORTED (AMENDED) BY THE COMMITTEE",
    "ORDERED TO BE REPORTED",
    "PLACED ON THE UNION CALENDAR",
    "PLACED ON SENATE LEGISLATIVE CALENDAR",
    "COMMITTEE CONSIDERATION AND MARK-UP SESSION HELD",
    "FILED WRITTEN REPORT",
)
TEXT_RE = re.compile(r"<TEXT>([^<]*)</TEXT>")

METHOD_NOTE = (
    "Descriptive residual screen from the 2026-08-05 round-3 look (partial "
    "Spearman +0.129, permutation p .0007, positive in both chambers and "
    "both split halves): members ranked by how much LESS independent "
    "credit-union-vs-fintech contest they receive than their dual-bill "
    "exposure, committee seat, seniority, portfolio volume, activity "
    "recency, and bill-advancement momentum predict. Every listed member "
    "pairs heavy credit-union attention with a near-zero fintech "
    "counter-presence: this is an asymmetry screen (CU-held members "
    "fintech has not entered), not a symmetric battleground list. Filings "
    "arrive quarterly, so rows dominated by current-Congress bills may "
    "simply be awaiting the next disclosure cycle. A screen, not a score: "
    "nothing in the engines, ranks, or receptivity reads this."
)


class ScreenError(RuntimeError):
    """An input contract failed; do not publish a partial screen."""


def _derived_dir():
    return os.path.join(LOCAL_MELANGE, "lda_rebuild", "derived")


def _raw_dir():
    return os.path.join(os.path.dirname(_derived_dir()), "raw")


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = rank
        i = j + 1
    return out


def residualize_joint(target, controls):
    n = len(target)
    columns = [[1.0] * n] + list(controls)
    k = len(columns)
    xtx = [[sum(columns[i][r] * columns[j][r] for r in range(n)) for j in range(k)] for i in range(k)]
    xty = [sum(columns[i][r] * target[r] for r in range(n)) for i in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(xtx[r][col]))
        if abs(xtx[pivot][col]) < 1e-12:
            continue
        xtx[col], xtx[pivot] = xtx[pivot], xtx[col]
        xty[col], xty[pivot] = xty[pivot], xty[col]
        for r in range(col + 1, k):
            factor = xtx[r][col] / xtx[col][col]
            for c2 in range(col, k):
                xtx[r][c2] -= factor * xtx[col][c2]
            xty[r] -= factor * xty[col]
    beta = [0.0] * k
    for r in range(k - 1, -1, -1):
        s = xty[r] - sum(xtx[r][c2] * beta[c2] for c2 in range(r + 1, k))
        beta[r] = s / xtx[r][r] if abs(xtx[r][r]) > 1e-12 else 0.0
    fitted = [sum(beta[i] * columns[i][r] for i in range(k)) for r in range(n)]
    return [target[r] - fitted[r] for r in range(n)]


def _iter_csv(path, label):
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ScreenError(f"cannot read {label} {path}: {exc}") from exc
    with handle:
        for row in csv.DictReader(handle):
            yield row


def load_inputs(derived=None, raw=None):
    derived = derived or _derived_dir()
    raw = raw or _raw_dir()
    org_sectors = {}
    bill_sectors = collections.defaultdict(set)
    org_bill_pairs = set()
    for year in YEARS:
        path = os.path.join(derived, f"our_bill_lobby_index_{year}.csv")
        for row in _iter_csv(path, "bill-lobby index"):
            if (row.get("channel") or "").strip() not in STRICT:
                continue
            org = (row.get("lob_id") or "").strip()
            bill = (row.get("bill_key") or "").strip()
            if not org or not bill:
                raise ScreenError(f"blank org/bill in {path}")
            sectors = {s.strip() for s in (row.get("org_sector") or "").split("|") if s.strip()}
            org_bill_pairs.add((org, bill))
            if sectors:
                org_sectors.setdefault(org, set()).update(sectors)
                bill_sectors[bill].update(sectors)
    members_by_bill = collections.defaultdict(set)
    for row in _iter_csv(
        os.path.join(derived, "interactions", "interaction_bill_members.csv"),
        "interaction bill members",
    ):
        bill = (row.get("bill_key") or "").strip()
        member = (row.get("bioguide_id") or "").strip()
        if bill and member:
            members_by_bill[bill].add(member)
    finance_committee = set()
    snapshot_dir = os.path.join(raw, "committee_snapshots")
    current = None
    for name in sorted(os.listdir(snapshot_dir)):
        if not name.endswith(".yaml"):
            continue
        with open(os.path.join(snapshot_dir, name), encoding="utf-8") as handle:
            for line in handle:
                stripped = line.rstrip("\n")
                if stripped and not stripped.startswith((" ", "-", "\t")) and stripped.endswith(":"):
                    current = stripped[:-1].strip()
                elif "bioguide:" in stripped and current in FINANCE_COMMITTEES:
                    finance_committee.add(stripped.split("bioguide:", 1)[1].strip())
    if not finance_committee:
        raise ScreenError("committee snapshots yielded no finance-committee members")
    first_congress = {}
    for row in _iter_csv(os.path.join(MB_ROOT, "votes", "HSall_members.csv"), "Voteview members"):
        bioguide = (row.get("bioguide_id") or "").strip()
        if not bioguide:
            continue
        try:
            congress = int(row.get("congress"))
        except (TypeError, ValueError):
            continue
        if bioguide not in first_congress or congress < first_congress[bioguide]:
            first_congress[bioguide] = congress
    seniority = {b: 2026 - (1789 + 2 * (c - 1)) + 1 for b, c in first_congress.items()}
    return org_sectors, bill_sectors, org_bill_pairs, members_by_bill, finance_committee, seniority


def stage_bills(bill_keys, raw=None):
    raw = raw or _raw_dir()
    stages = {}
    by_zip = collections.defaultdict(list)
    for key in bill_keys:
        congress, bill_type, number = key.split(":")
        by_zip[(congress, bill_type)].append((key, number))
    for (congress, bill_type), entries in sorted(by_zip.items()):
        zip_path = os.path.join(raw, f"billstatus{congress}", f"BILLSTATUS-{congress}-{bill_type}.zip")
        if not os.path.exists(zip_path):
            continue
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            for key, number in entries:
                entry = f"BILLSTATUS-{congress}{bill_type}{number}.xml"
                if entry not in names:
                    continue
                blob = "\n".join(TEXT_RE.findall(archive.read(entry).decode("utf-8", "replace").upper()))
                if any(marker in blob for marker in STAGE_LAW):
                    stages[key] = 5
                    continue
                house = STAGE_HOUSE in blob
                senate = STAGE_SENATE in blob
                if house and senate:
                    stages[key] = 4
                elif house or senate:
                    stages[key] = 3
                elif any(marker in blob for marker in STAGE_COMMITTEE):
                    stages[key] = 2
                else:
                    stages[key] = 1
    return stages


def screen_rows(eligible, portfolio, shared_sets, disjoint_vec, control_ranks,
                shared_vec):
    """Residual ordering + the shipped exclusion rule (shared >= 1)."""
    rs = ranks(shared_vec)
    rf = ranks(disjoint_vec)
    residual = residualize_joint(rf, list(control_ranks) + [rs])
    order = sorted(range(len(eligible)), key=lambda i: residual[i])
    kept = [i for i in order if len(shared_sets[i]) >= 1]
    return residual, kept


def build_payload():
    (org_sectors, bill_sectors, org_bill_pairs, members_by_bill,
     finance_committee, seniority) = load_inputs()
    portfolio = collections.defaultdict(set)
    for bill, members in members_by_bill.items():
        for member in members:
            portfolio[member].add(bill)
    lobbied = set(bill_sectors) | {b for _o, b in org_bill_pairs}
    for member in list(portfolio):
        portfolio[member] &= lobbied
    eligible = sorted(m for m, b in portfolio.items() if len(b) >= PORTFOLIO_FLOOR)
    n = len(eligible)
    if n < 100:
        raise ScreenError(f"eligible member panel implausibly small: {n}")
    stages = stage_bills(set().union(*(portfolio[m] for m in eligible)))
    member_sector_bills = collections.defaultdict(lambda: collections.defaultdict(set))
    for org, bill in org_bill_pairs:
        for sector in org_sectors.get(org, ()):
            for member in members_by_bill.get(bill, ()):
                member_sector_bills[member][sector].add((org, bill))

    def congress_of(bill_key):
        return int(bill_key.split(":", 1)[0])

    columns = {"volume": [], "committee": [], "seniority": [], "recency": [], "advancement": []}
    for member in eligible:
        bills = portfolio[member]
        columns["volume"].append(math.log1p(len(bills)))
        columns["committee"].append(1.0 if member in finance_committee else 0.0)
        columns["seniority"].append(float(seniority.get(member, 0)))
        columns["recency"].append(sum(1 for b in bills if congress_of(b) == 119) / len(bills))
        known = [stages[b] for b in bills if b in stages] or [1]
        columns["advancement"].append(sum(known) / len(known))
    control_ranks = [ranks(columns[c]) for c in ("volume", "committee", "seniority", "recency", "advancement")]

    a_sector, b_sector = PAIR
    shared_vec, disjoint_vec, shared_sets = [], [], []
    for member in eligible:
        bills = portfolio[member]
        shared_bills = {
            b for b in bills
            if a_sector in bill_sectors.get(b, ()) and b_sector in bill_sectors.get(b, ())
        }
        excl = {}
        for s in (a_sector, b_sector):
            pairs = member_sector_bills.get(member, {}).get(s, set())
            excl[s] = sum(1 for _o, b in pairs if b not in shared_bills)
        shared_vec.append(len(shared_bills))
        disjoint_vec.append(min(excl[a_sector], excl[b_sector]))
        shared_sets.append(shared_bills)

    residual, kept = screen_rows(eligible, portfolio, shared_sets, disjoint_vec, control_ranks, shared_vec)
    top = kept[:TOP_ROWS]

    rng = random.Random(BOOTSTRAP_SEED)
    keep_counts = collections.Counter()
    for _ in range(BOOTSTRAP_REPLICATES):
        sample = sorted(set(rng.randrange(n) for _ in range(n)))
        sub_controls = [ranks([control_ranks[c][i] for i in sample]) for c in range(5)]
        rs = ranks([shared_vec[i] for i in sample])
        rf = ranks([disjoint_vec[i] for i in sample])
        res = residualize_joint(rf, sub_controls + [rs])
        order = sorted(range(len(sample)), key=lambda j: res[j])
        winners = set()
        taken = 0
        for j in order:
            if len(shared_sets[sample[j]]) >= 1:
                winners.add(sample[j])
                taken += 1
                if taken == TOP_ROWS:
                    break
        for i in top:
            if i in winners:
                keep_counts[i] += 1

    resolver = identity.MemberNameResolver.from_paths()
    rows = []
    for i in top:
        member = eligible[i]
        record = resolver.resolve(member)
        shared = sorted(shared_sets[i])
        cu_attention = len(member_sector_bills.get(member, {}).get("credit_union", set()))
        fintech_attention = len(member_sector_bills.get(member, {}).get("fintech", set()))
        rows.append(
            {
                "bioguide_id": member,
                "name": record["name"],
                "is_former": bool(record["is_former"]),
                "residual_rank": round(residual[i], 1),
                "shared_bills": shared,
                "cu_attention": cu_attention,
                "fintech_attention": fintech_attention,
                "current_congress_share": round(
                    sum(1 for b in shared if b.startswith("119:")) / len(shared), 2
                ),
                "stability": round(keep_counts[i] / BOOTSTRAP_REPLICATES, 3),
            }
        )
    return {
        "schema": SCHEMA,
        "pair": list(PAIR),
        "method_note": METHOD_NOTE,
        "rows": rows,
        "receipt": {
            "members_in_panel": n,
            "portfolio_floor": PORTFOLIO_FLOOR,
            "controls": ["log1p_portfolio", "finance_committee_118_119", "seniority", "recency_119_share", "advancement_momentum"],
            "exclusion_rule": "members with zero shared bills never listed",
            "bootstrap": {"replicates": BOOTSTRAP_REPLICATES, "seed": BOOTSTRAP_SEED},
            "round3_reference": {"partial_rho": 0.1287, "perm_p": 0.0007},
            "staged_bills": len(stages),
        },
    }


def _write_json_atomic(payload, path):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    temporary = f"{path}.tmp.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8", newline='\r\n') as handle:
        handle.write(body)
    os.replace(temporary, path)
    return len(body.encode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        default=os.path.join(_derived_dir(), "contest_screen", "cu_fintech_screen.json"),
    )
    args = parser.parse_args(argv)
    payload = build_payload()
    payload["generated_utc"] = datetime.now(timezone.utc).isoformat()
    size = _write_json_atomic(payload, args.out)
    print(
        "contest screen: %d rows | panel %d members | %d bytes -> %s"
        % (len(payload["rows"]), payload["receipt"]["members_in_panel"], size, os.path.abspath(args.out))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
