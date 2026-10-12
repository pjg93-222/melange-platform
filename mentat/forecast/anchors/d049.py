"""D-049 filing-version resolution for FEC pas2 rows (brain D-049: identity decides; a reused transaction id is not a competing version).

Verbatim port of the 05 v3 study's Python implementation (SP phase2_mentat_anchor/work/pod/d049.py) of Mentat's R pas2 parser, which the
phase-2 design validated: the all-recipient totals it gives reproduce the study receipt exactly for every industry x cycle x channel.
Pure function over parsed rows; it reads and writes no file.
"""
# D-049 filing-version resolution for FEC pas2 rows, count_latest (the policy D-049 applied: transfer index VERSION 4,
# PAS2 parser "5"), mirroring mentat\R\fec_beneficiary.R fec_build_pas2_index under
# FEC_PAS2_AMBIGUOUS_VERSION_POLICY = "count_latest":
#  - version key = committee | report type | transaction id (SUB|sub_id when the transaction id is blank)
#  - identity = candidate id | recipient committee | amount (cents) | date | transaction type
#  - duplicate FEC sub-ID: one kept; the same identity in a later filing supersedes (highest file number wins);
#    the same identity repeated inside the winning filing is one itemization (counted once)
#  - different identities under one key are different transactions reusing a label (counted, flagged tran_id_reused),
#    except same-candidate rows in different filings agreeing on the amount OR the date: ambiguous -> the latest filed
#    version is counted (flag unverified_version), the rest superseded
#  - memo rows (MEMO_CD X) are never counted; a row with no candidate ID is flagged (see resolve()).
from collections import defaultdict

AMEND = {"T": 2, "A": 1}


def resolve(rows):
    """rows: list of dicts with rid, cmte_id, rpt_tp, tran_id, sub_id, file_num (int|None), amndt_ind, cand_id,
    other_id, amount_cents, date (ISO or ''), transaction_tp, memo_cd, order (int).
    Returns {rid: dict(include, status, tran_id_reused, unverified_version, version_key)}."""
    R = []
    for r in rows:
        vk = "|".join((r["cmte_id"], r["rpt_tp"], r["tran_id"])) if r["tran_id"] else "SUB|" + (r["sub_id"] or str(r["order"]))
        fn = r["file_num"] if r["file_num"] is not None else float("-inf")
        R.append(dict(r, vk=vk, fn=fn, ap=AMEND.get(r["amndt_ind"], 0),
                      sid=r["sub_id"] if r["sub_id"] else "%020d" % r["order"],
                      ident=(r["cand_id"], r["other_id"], r["amount_cents"], r["date"], r["transaction_tp"])))
    # setorder(version_key, -file_num_sort, -amendment_priority, -sub_id_sort): stable multi-key sort
    R.sort(key=lambda x: x["sid"], reverse=True)
    R.sort(key=lambda x: x["ap"], reverse=True)
    R.sort(key=lambda x: x["fn"], reverse=True)
    R.sort(key=lambda x: x["vk"])
    seen_sub = set()
    for x in R:
        x["exact_dup"] = bool(x["sub_id"]) and x["sub_id"] in seen_sub
        if x["sub_id"]:
            seen_sub.add(x["sub_id"])
        x["live"] = not x["exact_dup"]
    latest = defaultdict(lambda: float("-inf"))
    for x in R:
        if x["live"]:
            k = (x["vk"], x["ident"])
            latest[k] = max(latest[k], x["fn"])
    for x in R:
        x["superseded"] = x["live"] and x["fn"] < latest[(x["vk"], x["ident"])]
        x["live"] = x["live"] and not x["superseded"]
    seen = set()
    for x in R:
        x["repeated"] = False
        if x["live"]:
            k = (x["vk"], x["ident"])
            if k in seen:
                x["repeated"] = True
            seen.add(k)
        x["live"] = x["live"] and not x["repeated"]
    files_vk = defaultdict(set); blank_vk = defaultdict(bool)
    files_amt = defaultdict(set); files_date = defaultdict(set)
    for x in R:
        if x["live"]:
            files_vk[x["vk"]].add(x["fn"])
            blank_vk[x["vk"]] |= (x["cand_id"] == "")
            files_amt[(x["vk"], x["cand_id"], x["amount_cents"])].add(x["fn"])
            files_date[(x["vk"], x["cand_id"], x["date"])].add(x["fn"])
    for x in R:
        sf = len(files_vk[x["vk"]]); br = blank_vk[x["vk"]]
        af = len(files_amt[(x["vk"], x["cand_id"], x["amount_cents"])]) if x["live"] else 0
        df = len(files_date[(x["vk"], x["cand_id"], x["date"])]) if x["live"] else 0
        x["unresolved"] = x["live"] and ((x["cand_id"] != "" and (af > 1 or df > 1)) or (br and sf > 1))
        x["conflicted"] = x["unresolved"] and x["cand_id"] != ""
        # Mentat's R parser never counts a row without a candidate ID. Here the recipient committee identifies the
        # champion (brief: match by CAND_ID or by the authorized committee), so a candidate-less row that is NOT in a
        # multi-filing ambiguity keeps its dollars under the flag no_candidate_id (the caller decides whether a
        # committee match exists); a candidate-less row inside an ambiguous multi-filing group stays unresolved.
        x["no_candidate_id"] = x["cand_id"] == "" and x["memo_cd"] != "X" and x["live"] and not x["unresolved"]
        x["unverified"] = False
    # count_latest: conflicted rows keep the latest filed version per version key + candidate
    groups = defaultdict(list)
    for x in R:
        if x["conflicted"]:
            groups[(x["vk"], x["cand_id"])].append(x)
    for g in groups.values():
        best = max(range(len(g)), key=lambda i: (g[i]["fn"], -i))  # which.max: first maximum in sorted order
        for i, x in enumerate(g):
            x["unresolved"] = False
            x["unverified"] = True
            x["superseded"] = (i != best)
    ids_vk = defaultdict(set)
    for x in R:
        if not x["exact_dup"]:
            ids_vk[x["vk"]].add(x["ident"])
    out = {}
    for x in R:
        if x["exact_dup"]:
            st = "duplicate_sub_id"
        elif x["repeated"]:
            st = "repeated_itemization"
        elif x["superseded"]:
            st = "superseded_filing_version"
        elif x["unresolved"]:
            st = "unresolved_version"
        elif x["memo_cd"] == "X":
            st = "memo_only"
        elif x["no_candidate_id"]:
            st = "included_no_candidate_id"
        else:
            st = "included"
        out[x["rid"]] = dict(include=st.startswith("included"), status=st, tran_id_reused=int(len(ids_vk[x["vk"]]) > 1),
                             unverified_version=int(x["unverified"]), version_key=x["vk"])
    return out
