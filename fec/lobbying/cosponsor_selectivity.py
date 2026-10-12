# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from paths import ROOT as MB_ROOT   # centralized root (arch review 2026-07-02)
"""COSPONSOR SELECTIVITY — the real opportunity denominator  (built 2026-08-03).

THE OWNER'S PROBLEM, in his words (2026-08-03):  "Yeah we gotta account for selectivity".

WHAT WAS WRONG
  The cosponsor-propensity basis (make_member_desk_00.tpl, 2026-08-03) ranks candidates partly on
  cosponRate(v,area) — the share of a member's OWN signatures that landed in the selected area.
  That numerator and that denominator both come from the member's own ledger, so it cannot tell a
  member who signs EVERYTHING (and therefore signs some housing bills too) apart from one who signs
  housing bills SPECIFICALLY. cosponLift() was shipped as a per-row DISPLAY readout for exactly
  this reason and deliberately kept out of the score: it is a ratio on single-digit counts, so a
  member with one signature in the right place would have out-ranked a genuine specialist.

WHAT THIS IS
  The denominator the ledger cannot supply. For each (member, area, congress):

      opportunities = the bills in that area, introduced in a Congress the member SERVED in, in the
                      member's own chamber, that the member did not SPONSOR
      signatures    = of those, the ones they actually cosponsored
      selectivity   = signatures / opportunities,  empirical-Bayes shrunk

  The denominator now contains the bills a member DECLINED to sign, which is the entire point: an
  indiscriminate signer has a high numerator and a high denominator and scores near the field
  average; a specialist has a high numerator only where it counts.

CHAMBER GATE
  A cosponsor signs the SPONSOR'S bill, so only members of the bill's originating chamber ever had
  the opportunity. Without this gate every Senator would carry ~400 House "opportunities" they
  could not legally have taken and would read as maximally UN-selective. Chamber is taken
  per-Congress from Voteview HSall so a member who moved chambers is handled correctly in each.

SOURCES  (nothing here is a new taxonomy)
  ISSUE AREA   bills_audit.csv `section` -> bill_rules.canonical_category(). 676 rows over 623
    distinct (section, bill, congress) cells, mapping 100% onto the canonical 18 CATEGORIES with
    zero unmapped, and it is keyed by BILL, which is what an opportunity set needs.
    REJECTED bill_directions.csv: its `Category` is CFPB:Reduce / CFPB:Expand / CFPB:Neutral — a
      DIRECTION vocabulary on 126 rows, not an issue-area one.
    REJECTED legislation_membership_edges.ndjson: 93 edges over 73 distinct bills, 74 of them
      amendments (samdt/hamdt) which carry no cosponsor roster, and it is the live output of the
      in-flight culling promotion. Both its size and its stability disqualify it today.
  ROSTERS      BILLSTATUS 117/118/119 zips under %LOCALAPPDATA%\\Melange\\lda_rebuild\\raw, parsed
    with lobbying.build_network_2021_rebuild._bill_metadata_for_member() — the shipped parser, not a second
    XML implementation. Only the ~620 audit bills are opened, not all ~50,000 members.
  SERVICE      votes/HSall_members.csv (bioguide_id, congress, chamber) — the same seniority source
    lobbying/coalition_pricing.py uses.

SHRINKAGE
  EB_K = 2.0, imported from lobbying.coalition_pricing (Paul ratified 2026-08-03), toward the AREA's own
  global signature rate p_A rather than toward zero:
      sel = (signatures + EB_K * p_A) / (opportunities + EB_K)
  Shrinking a PROPORTION toward zero would penalise every member in a low-traffic area equally and
  is not what the constant is for. The measured opportunity counts make this a light touch — the
  chamber gate leaves a typical member tens of opportunities per area, not two — which is itself
  the difference between this and the ledger ratio it replaces.
"""
import json, sys, os, csv, math, re, zipfile, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import moneyball_core as MBC
from moneyball_core import dump_atomic
import bill_rules as BR
from lobbying.build_network_2021_rebuild import _bill_metadata_for_member, ZIP_TYPES

B = MB_ROOT
LA = os.path.expandvars(r'%LOCALAPPDATA%\Melange')
BSROOT = os.path.join(LA, 'lda_rebuild', 'raw')
CONGRESSES = (117, 118, 119)

from lobbying.coalition_pricing import EB_K     # 2.0, Paul ratified 2026-08-03

# "H.R.10036" -> ("HR",10036) ; "S.J.Res.32" -> ("SJRES",32).  Longest prefix first so that
# "S.J.Res." is never shortened to "S.".
BILL_PREFIXES = (('H.CON.RES.', 'HCONRES'), ('S.CON.RES.', 'SCONRES'),
                 ('H.J.RES.', 'HJRES'), ('S.J.RES.', 'SJRES'),
                 ('H.RES.', 'HRES'), ('S.RES.', 'SRES'),
                 ('H.R.', 'HR'), ('S.', 'S'))
HOUSE_TYPES = {'HR', 'HRES', 'HJRES', 'HCONRES'}
ARCHIVE_FOR_TYPE = {token: archive for archive, token in ZIP_TYPES}


def parse_bill(text):
    raw = re.sub(r'\s+', '', str(text or '')).upper()
    for prefix, token in BILL_PREFIXES:
        if raw.startswith(prefix):
            tail = raw[len(prefix):]
            if tail.isdigit():
                return token, int(tail)
            return None
    return None


def load_area_bills():
    """(area, congress) -> set of (type_token, number), from bills_audit.csv."""
    path = os.path.join(B, 'bills_audit.csv')
    area_bills = collections.defaultdict(set)
    rows = unparsed = unmapped = 0
    with open(path, encoding='utf-8-sig', errors='replace', newline='') as fh:
        for row in csv.DictReader(fh):
            rows += 1
            area = BR.canonical_category(row.get('section'))
            if not area:
                unmapped += 1; continue
            key = parse_bill(row.get('bill'))
            if key is None:
                unparsed += 1; continue
            for token in re.split(r'[;,|/\s]+', str(row.get('congresses') or '')):
                if token.strip().isdigit():
                    congress = int(token.strip())
                    if congress in CONGRESSES:
                        area_bills[(area, congress)].add(key)
    return area_bills, {'audit_rows': rows, 'unmapped_section': unmapped,
                        'unparseable_bill': unparsed}


def load_rosters(wanted):
    """(type,number,congress) -> (sponsor_bioguide, frozenset(cosponsor_bioguides)).

    Opens only the archive members we actually need. Reuses the shipped BILLSTATUS parser.
    """
    out, missing = {}, []
    by_archive = collections.defaultdict(set)
    for token, number, congress in wanted:
        by_archive[(congress, token)].add(number)
    for (congress, token), numbers in sorted(by_archive.items()):
        archive_token = ARCHIVE_FOR_TYPE.get(token)
        if archive_token is None:
            missing.extend((token, n, congress) for n in numbers); continue
        name = 'BILLSTATUS-%d-%s.zip' % (congress, archive_token)
        path = os.path.join(BSROOT, 'billstatus%d' % congress, name)
        if not os.path.isfile(path):
            print('  !! missing archive %s' % path)
            missing.extend((token, n, congress) for n in numbers); continue
        want_names = {'BILLSTATUS-%d%s%d.xml' % (congress, archive_token, n): n for n in numbers}
        with zipfile.ZipFile(path, 'r') as archive:
            index = {os.path.basename(i.filename).upper(): i for i in archive.infolist()
                     if i.filename.lower().endswith('.xml')}
            for member_name, number in sorted(want_names.items()):
                info = index.get(member_name.upper())
                if info is None:
                    missing.append((token, number, congress)); continue
                with archive.open(info, 'r') as stream:
                    sponsor, cosponsors, _titles = _bill_metadata_for_member(stream, name, info.filename)
                out[(token, number, congress)] = (sponsor, frozenset(cosponsors))
    return out, missing


def load_service():
    """bioguide -> {congress: chamber}, from Voteview HSall."""
    path = os.path.join(B, 'votes', 'HSall_members.csv')
    service = collections.defaultdict(dict)
    if not os.path.exists(path):
        print('  !! missing %s — every member will read as never serving' % path)
        return service
    with open(path, encoding='utf-8-sig', errors='replace', newline='') as fh:
        for row in csv.DictReader(fh):
            bioguide = (row.get('bioguide_id') or '').strip()
            chamber = (row.get('chamber') or '').strip()
            if not bioguide or chamber not in ('House', 'Senate'):
                continue
            try:
                congress = int(row['congress'])
            except (TypeError, ValueError, KeyError):
                continue
            if congress in CONGRESSES:
                service[bioguide][congress] = chamber
    return service


def build():
    cats = list(json.load(open(os.path.join(B, 'category_stance.json'), encoding='utf-8'))['cats'])
    cong = json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json'), encoding='utf-8'))
    members = sorted(m['member'] for m in cong)
    bio = {m['member']: (m.get('bioguide') or '') for m in cong}

    area_bills, audit_receipt = load_area_bills()
    wanted = {(token, number, congress)
              for (_area, congress), keys in area_bills.items()
              for token, number in keys}
    print('audit bills: %d distinct (bill, congress) cells over %d (area, congress) cells'
          % (len(wanted), len(area_bills)))
    rosters, missing = load_rosters(wanted)
    print('BILLSTATUS rosters resolved: %d of %d  (missing %d)'
          % (len(rosters), len(wanted), len(missing)))
    if missing:
        print('  missing sample: %s' % sorted(missing)[:12])
    service = load_service()
    print('members with 117-119 service rows: %d of %d'
          % (sum(1 for n in members if service.get(bio[n])), len(members)))

    ai = {c: i for i, c in enumerate(cats)}
    sig = {n: [0] * len(cats) for n in members}
    opp = {n: [0] * len(cats) for n in members}

    for (area, congress), keys in sorted(area_bills.items()):
        j = ai.get(area)
        if j is None:
            continue
        for token, number in sorted(keys):
            roster = rosters.get((token, number, congress))
            if roster is None:
                continue                                  # unresolved bill is no one's opportunity
            sponsor, cosponsors = roster
            bill_chamber = 'House' if token in HOUSE_TYPES else 'Senate'
            for n in members:
                b = bio[n]
                if not b or service.get(b, {}).get(congress) != bill_chamber:
                    continue                              # not serving, or wrong chamber
                if b == sponsor:
                    continue                              # you cannot cosponsor your own bill
                opp[n][j] += 1
                if b in cosponsors:
                    sig[n][j] += 1

    # area base rate p_A = all signatures / all opportunities in that area (the shrinkage target)
    base = []
    for j in range(len(cats)):
        s = sum(sig[n][j] for n in members)
        o = sum(opp[n][j] for n in members)
        base.append(s / o if o else 0.0)

    sel = {}
    for n in members:
        sel[n] = [round((sig[n][j] + EB_K * base[j]) / (opp[n][j] + EB_K), 6) for j in range(len(cats))]
    return cats, members, sig, opp, sel, base, audit_receipt, len(rosters), len(wanted)


def main():
    cats, members, sig, opp, sel, base, audit_receipt, resolved, wanted = build()
    tot_opp = sum(sum(opp[n]) for n in members)
    tot_sig = sum(sum(sig[n]) for n in members)
    print('\nmember-area cells: %d | opportunities %s | signatures %s | overall rate %.4f'
          % (len(members) * len(cats), f'{tot_opp:,}', f'{tot_sig:,}',
             tot_sig / tot_opp if tot_opp else 0))
    nz = [(n, j) for n in members for j in range(len(cats)) if opp[n][j] > 0]
    print('cells with >=1 opportunity: %d  | with >=1 signature: %d'
          % (len(nz), sum(1 for n, j in nz if sig[n][j] > 0)))
    counts = sorted(opp[n][j] for n, j in nz)
    if counts:
        print('opportunities per live cell: min %d  p25 %d  median %d  p75 %d  max %d'
              % (counts[0], counts[len(counts) // 4], counts[len(counts) // 2],
                 counts[3 * len(counts) // 4], counts[-1]))
        thin = sum(1 for c in counts if c <= 2)
        print('  cells with <=2 opportunities (the case EB_K exists for): %d (%.2f%%)'
              % (thin, 100.0 * thin / len(counts)))
    print('\nper-area base rate and the most selective member:')
    for j, c in enumerate(cats):
        live = [n for n in members if opp[n][j] > 0]
        if not live:
            print('  %-28s no opportunities (no audit bills in this area)' % c); continue
        top = max(live, key=lambda n: (sel[n][j], sig[n][j]))
        print('  %-28s p_A %.4f  n_live %3d  top %-24s %d/%d  sel %.4f'
              % (c, base[j], len(live), top, sig[top][j], opp[top][j], sel[top][j]))
    dump_atomic({
        'schema': 'cosponsor-selectivity-v1',
        'scope': 'decision-layer surface for the coalition builder; changes no rank in ranks_python.json',
        'built': '2026-08-03',
        'definition': ('signatures / opportunities per (member, area); opportunity = a bill in that '
                       'area, introduced in a Congress the member served, in the member\'s own '
                       'chamber, that they did not sponsor'),
        'area_source': 'bills_audit.csv section -> bill_rules.canonical_category',
        'roster_source': 'BILLSTATUS %s via lobbying.build_network_2021_rebuild._bill_metadata_for_member'
                         % (','.join(str(c) for c in CONGRESSES)),
        'service_source': 'votes/HSall_members.csv (bioguide_id, congress, chamber)',
        'shrinkage': {'EB_K': EB_K, 'target': 'per-area global signature rate p_A',
                      'ratified': 'EB_K ratified by Paul 2026-08-03 (lobbying/coalition_pricing.py)'},
        'congresses': list(CONGRESSES),
        'audit_receipt': dict(audit_receipt, bills_wanted=wanted, rosters_resolved=resolved),
        'areas': cats,
        'area_base_rate': [round(b, 6) for b in base],
        'members': {n: {'sig': sig[n], 'opp': opp[n], 'sel': sel[n]} for n in members},
    }, os.path.join(B, 'data', 'network', 'cosponsor_selectivity.json'))
    print('\nwrote cosponsor_selectivity.json (%d members x %d areas)' % (len(members), len(cats)))


if __name__ == '__main__':
    main()
