# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""ES EXTERNAL CORROBORATION LOOK — Vote Smart PCT respond (big5_es_votesmart_prereg.md, LOCKED
2026-07-09 PRE-COLLECTION). Existing display reads (v2-ES primary, Pink-ES secondary) vs the binary
pct_respond criterion; NOTHING is trained. Statistics imported: exact perm from
b5_behavioral_instrument (K=5000 seed 20260708), Frisch-Waugh partial from b5_n_matrix (seed
20260710). Nested outcome tiers with FIXED wordings (prereg). Gate: prereg LOCKED + no
newline-anchored RESULT-VS + COLLECTION RECORD addendum present and matching the artifact + all
instrument fingerprints match + coverage/degeneracy guards pass (base rate computed from the
collection artifact ALONE, before any instrument file is opened). --verify = gateless recomputation
for the fresh-process re-derivation (prints statistics only; appends nothing). Honest prior: LOW
(corroboration) / VERY LOW (validity); adverse prior §18 RESULT-E2 disclosed in the prereg."""
import sys, os, json, csv, math, re, argparse, collections
import numpy as np
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import members.b5_behavioral_instrument as BI
import members.b5_n_matrix as NM
B = MB_ROOT
PREREG = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))), 'big5_es_votesmart_prereg.md')
NPATF = B + r'\data\members\votesmart_npat.json'
PIN = {'v2_sha': 'aed97ffa7c5cabf933bb7924b9c01d80c076452a137d4ebecf6b64c5c217fd7b',
       'llm_sha': '4b462228f5445dec43d66fc04e801cbd1974d31d569908938150604c8ec19632',
       'leg_sha': 'c5a6cbdbaae0bb19faa89852c29922809ececc70b97c149b4709588087e5e391',
       'hs_sha': '7f7b41cfec668961587c8902963b2ab491d811bd8841707e436e58ad7709553a',
       'pn_sha': 'f47b9b045357cad2c5644f551fc934e8a0aa793a22d98272632547a276d22a50'}
AXES = ['O', 'C', 'E', 'A', 'ES']
rr = NM.rr

def collection_record(pre):
    """Parse the COLLECTION RECORD addendum: artifact_sha= / joined_n= / base_rate= lines."""
    if '\n## COLLECTION RECORD' not in pre: return None
    seg = pre.split('\n## COLLECTION RECORD', 1)[1]
    g = lambda k: (re.search(r'%s\s*=\s*([0-9a-fA-F.]+)' % k, seg) or [None, None])[1]
    try:
        return {'sha': g('artifact_sha'), 'joined_n': int(g('joined_n')), 'base_rate': float(g('base_rate'))}
    except (TypeError, ValueError):
        return None

def build_join():
    """Criterion-side join ONLY (no instrument file opened here — the prereg ordering)."""
    C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
    b2m = {info.get('bioguide'): m for m, info in C.items() if info.get('bioguide')}
    vsid = {}; gender = {}
    with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            m = b2m.get(row.get('bioguide_id'))
            if not m: continue
            if (row.get('votesmart_id') or '').strip(): vsid[m] = row['votesmart_id'].strip()
            gender[m] = row.get('gender', '')
    NP = json.load(open(NPATF, encoding='utf-8'))['members'] if os.path.exists(NPATF) else {}
    y = {m: NP[m]['responded'] for m in vsid if m in NP and NP[m].get('responded') in (0, 1)}
    nfail = sum(1 for m in vsid if m in NP and NP[m].get('responded') is None)
    # tenure: first congress per bioguide from HSall_members.csv (prereg-pinned formula)
    fc = {}
    m2b = {m: info.get('bioguide') for m, info in C.items()}
    with open(B + r'\votes\HSall_members.csv', encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            bg = row.get('bioguide_id')
            if bg:
                try: cg = int(row.get('congress'))
                except (TypeError, ValueError): continue
                if bg not in fc or cg < fc[bg]: fc[bg] = cg
    yrs = {m: 2026 - (1789 + 2 * (fc[m2b[m]] - 1) - 1) for m in vsid if m2b.get(m) in fc}
    chamber = {m: 1.0 if (C[m].get('chamber') == 'Senate') else 0.0 for m in vsid}
    return C, vsid, gender, y, nfail, yrs, chamber

def stats_for(scores, mem, y, gender, party, chamber, yrs, label):
    xs = np.array([scores[m] for m in mem]); yy = np.array([float(y[m]) for m in mem])
    own, pp, para = BI.exact_perm_p(xs, yy)
    Z = np.column_stack([np.array([1.0 if gender.get(m) == 'F' else 0.0 for m in mem]),
                         np.array([party[m] for m in mem]),
                         np.array([chamber[m] for m in mem]),
                         np.array([math.log1p(yrs.get(m, 0)) for m in mem])])
    r_part, p_part = NM.fw_partial(xs, yy, Z)
    print('  [%s] point-biserial r=%+.4f exact perm-p=%.4f (param %.4f) | FW partial (gender,party,chamber,log-tenure) = %+.4f p=%.4f'
          % (label, own, pp, para, r_part, p_part))
    return own, pp, r_part, p_part, xs, yy

def run(verify_only):
    pre = open(PREREG, encoding='utf-8').read()
    C, vsid, gender, y, nfail, yrs, chamber = build_join()
    joined = sorted(y)
    n = len(joined); base = (sum(y[m] for m in joined) / n) if n else 0.0
    print('criterion-side join: votesmart_id members=%d | responded-in-{0,1}=%d | fetch_failed=%d | base rate=%.3f'
          % (len(vsid), n, nfail, base))
    art_sha = NM.sha(NPATF) if os.path.exists(NPATF) else 'ABSENT'
    if not verify_only:
        rec = collection_record(pre)
        if rec is None: print('!! LOOK REFUSED: COLLECTION RECORD addendum absent/unparseable'); return
        if rec['sha'] != art_sha or rec['joined_n'] != n or abs(rec['base_rate'] - base) > 5e-4:
            print('!! LOOK REFUSED (not consumed): collection record mismatch (sha/N/base rate)'); return
        if n < 450: print('!! VOID-FOR-COVERAGE (joined N=%d < 450): no look consumed' % n); return
        if base < 0.05 or base > 0.95: print('!! VOID-FOR-DEGENERACY (base rate %.3f): no look consumed' % base); return
    # ---- instruments open ONLY past this line (prereg ordering) ----
    import moneyball_core as MC
    P = MC.party()
    party = {m: 1.0 if P.get(m) == 'R' else 0.0 for m in joined}
    V2 = json.load(open(B + r'\data\members\big5_v2.json', encoding='utf-8'))['traits']
    mem = [m for m in joined if (V2.get(m) or {}).get('ES') is not None and gender.get(m) in ('M', 'F') and m in yrs]
    print('analysis roster (∩ v2-ES non-null ∩ gender M/F ∩ tenure computable): n=%d' % len(mem))
    print('\n=== PRIMARY: v2-ES vs pct_respond ===')
    es = {m: V2[m]['ES'] for m in mem}
    own, pp, r_part, p_part, xs, yy = stats_for(es, mem, y, gender, party, chamber, yrs, 'v2-ES')
    S = (own > 0) and (pp < .05)
    rivals = {}
    for ax in ('O', 'C', 'E', 'A'):
        sc = np.array([V2[m].get(ax) if V2[m].get(ax) is not None else 0.0 for m in mem])
        rivals[ax] = rr(sc, yy)
        print('    specificity rival v2-%s: r=%+.4f' % (ax, rivals[ax]))
    SPEC = all(abs(own) > abs(v) for v in rivals.values())
    near = max(rivals, key=lambda k: abs(rivals[k]))
    wt, wp = NM.williams_t(yy, xs, np.array([V2[m].get(near) or 0.0 for m in mem]))
    print('    Williams-t ES vs nearest rival (%s): t=%.2f p=%.4f (reported)' % (near, wt, wp))
    PART_C = (r_part > 0) and (p_part < .05)
    PART_M = (r_part >= 0.10) and (p_part < .05)
    print('\n=== SECONDARY (reported, firewalled): Pink-ES ===')
    LLM = json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8'))['traits']
    pmem = [m for m in mem if (LLM.get(m) or {}).get('ES') is not None]
    if len(pmem) >= 100:
        stats_for({m: LLM[m]['ES'] for m in pmem}, pmem, y, gender, party, chamber, yrs, 'Pink-ES')
    PANEL = json.load(open(B + r'\behavior_panel.json'))['members']
    vsl = {m: (PANEL.get(m) or {}).get('vote_share_last') for m in mem}
    vok = [m for m in mem if vsl.get(m) is not None]
    if len(vok) >= 100:
        Zv = np.column_stack([np.array([float(vsl[m]) for m in vok])])
        rp2, pp2 = NM.fw_partial(np.array([es[m] for m in vok]), np.array([float(y[m]) for m in vok]), Zv)
        print('  extra reported partial (| vote_share_last): %+.4f p=%.4f (n=%d)' % (rp2, pp2, len(vok)))
    print('\n=== VERDICT (nested tiers, highest met; wordings fixed by the prereg) ===')
    if own >= 0.15 and S and SPEC and PART_M:
        print('  TIER 1 VALIDITY (pending fresh-process re-derivation agreement): "ES text read externally validated (PCT respond)"')
    elif S and SPEC and PART_C:
        print('  TIER 2: "ES text read WEAKLY CORROBORATED by PCT-respond (r=%+.3f, under the validity bar; annotation only)"' % own)
    elif S and not SPEC:
        print('  TIER 3: "corroboration signal present but not ES-specific — closed"')
    elif S and SPEC and not PART_C:
        print('  TIER 4: "signal present, confound-attributable (fails the conditioning set) — closed, no corroboration claim"')
    else:
        print('  TIER 5: "ES text read externally UNCORROBORATED — the §18 limbo closes with evidence"')
    print('\n  artifact sha at look: %s… | joined n=%d | base rate=%.3f | non-finite events: %d'
          % (art_sha[:12], n, base, NM.NONFIN[0]))
    if verify_only:
        print('\n--verify run (gateless recomputation): compare every statistic to the look transcript; a mismatch is a DEFECT HALT.')
    else:
        print('\nAppend the RESULT-VS section to big5_es_votesmart_prereg.md verbatim and commit same session.')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--look', action='store_true'); ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()
    print('ES external corroboration — Vote Smart PCT respond (prereg: big5_es_votesmart_prereg.md)')
    pre = open(PREREG, encoding='utf-8').read() if os.path.exists(PREREG) else ''
    if not pre: print('!! prereg ABSENT'); return
    locked = 'STATUS: LOCKED' in pre
    taken = '\n## RESULT-VS' in pre
    fpok = True
    for k, p in (('v2_sha', B + r'\data\members\big5_v2.json'), ('llm_sha', B + r'\data\members\big5_llm_opus.json'),
                 ('leg_sha', B + r'\fec\legislators.csv'), ('hs_sha', B + r'\votes\HSall_members.csv'),
                 ('pn_sha', B + r'\behavior_panel.json')):
        if not os.path.exists(p) or NM.sha(p) != PIN[k]: fpok = False
    has_npat = os.path.exists(NPATF)
    print('  lock: %s | look: %s | instrument fingerprints: %s | collection artifact: %s'
          % (locked, 'TAKEN' if taken else 'not yet taken', 'MATCH' if fpok else 'DRIFT',
             'present' if has_npat else 'ABSENT (awaiting API key -> fec/members/votesmart_npat_fetch.py)'))
    if not (args.look or args.verify): return
    if args.look:
        if not locked: print('!! LOOK REFUSED: prereg not LOCKED'); return
        if taken: print('!! LOOK REFUSED: RESULT-VS already recorded (one look only)'); return
        if not fpok: print('!! LOOK REFUSED (not consumed): instrument fingerprint drift'); return
        if not has_npat: print('!! LOOK REFUSED: collection artifact absent'); return
    run(verify_only=args.verify and not args.look)

if __name__ == '__main__':
    main()
