# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""PSYCH-AUDIT ROUND 5 — text-A vs the DE-ENTANGLED cooperation criterion (big5_a_matrix_prereg.md,
LOCKED 2026-07-09 pre-look, adversarially reviewed). The §17 machinery IMPORTED from
b5_behavioral_instrument.py; round-4 masked-matrix components IMPORTED from b5_n_matrix.py
(rr/zz/fw_partial/williams_t/plant_set — the NONFIN counter therefore lives in b5_n_matrix and is
reported from there). Criterion: cooperation_v2_z (coop_v2_candidate.json, SHA-pinned; composition
frozen in the same-commit fec/members/coop_v2_candidate.py). ONE look, gated on: prereg present + 'STATUS:
LOCKED' + no newline-anchored RESULT-A heading + input fingerprints/rosters match + in-process
battery green (battery fw_partial uses THIS round's bar-5 conditioning set [gender, party,
log1p(n_cosponsored)]; sim(d) includes a volume loading — the round's claim is that the gate admits
true signal coexisting with volume). Honest prior: MODERATE. No LLM calls. No audio."""
import sys, os, json, csv, math, hashlib, sqlite3, argparse
import numpy as np
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import members.b5_behavioral_instrument as BI     # import-safe (__main__-guarded)
import members.b5_n_matrix as NM                  # import-safe (__main__-guarded): rr/zz/fw_partial/williams_t/plant_set
import moneyball_core as MC
B = MB_ROOT
PREREG = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))), 'big5_a_matrix_prereg.md')
CORE = ['O', 'C', 'E', 'A', 'ES']         # the five §17 models/criteria; 'A' here = A_v1 (old cooperation)
SEEDS17 = [7, 17, 29, 43, 61]
RESEEDS = [101, 113, 127, 139, 151]
S17_DIAG = {'O': .198, 'C': .277, 'E': .552, 'A': .183, 'ES': .149}
S17_PASS = ['O', 'C', 'E']
PIN = {'MEM': 456, 'MEM_A': 290,
       'cv_sha': '03418fd1d9407f8cb1a15e7beac62fec342c13f8b2fd35122a7748838fd11ecb',
       'bd_sha': '6b9bb9d2a15ce9be1ddd0a8ec63c51fef1449e68f37ffda08b3c70e6564397d7',
       'vv_sha': '0bf7c461e9eca440535484bf325c5b90d423daf0573e984a1b8691a242194751',
       'pn_sha': 'f47b9b045357cad2c5644f551fc934e8a0aa793a22d98272632547a276d22a50',
       'speeches': 234827, 'posts': 29098}
rr, zz = NM.rr, NM.zz

def fingerprints():
    con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
    ns = con.execute('select count(*) from speeches').fetchone()[0]
    npo = con.execute('select count(*) from posts').fetchone()[0]
    con.close()
    return {'cv_sha': NM.sha(B + r'\coop_v2_candidate.json'), 'bd_sha': NM.sha(B + r'\behavioral_dispositions.json'),
            'vv_sha': NM.sha(B + r'\data\members\voteview_consistency.json'), 'pn_sha': NM.sha(B + r'\behavior_panel.json'),
            'speeches': ns, 'posts': npo}

def load_all():
    BD, VV, P, MEM, alltext = BI.load_corpus()
    CRIT = BI.criteria(BD, VV, P, MEM)             # O/C/E/A_v1/ES computed ONCE on MEM (sliced later)
    CV = json.load(open(B + r'\coop_v2_candidate.json'))['members']
    coopv2 = np.array([(CV.get(m) or {}).get('cooperation_v2_z') if (CV.get(m) or {}).get('cooperation_v2_z') is not None
                       else np.nan for m in MEM], float)
    mask = ~np.isnan(coopv2)
    facets = {k: np.array([(CV.get(m) or {}).get(k) if (CV.get(m) or {}).get(k) is not None else np.nan for m in MEM], float)
              for k in ('c1_bip_z', 'c2_xshare_z', 'c3_recip_z')}
    PANEL = json.load(open(B + r'\behavior_panel.json'))['members']
    ncosp = np.array([float((PANEL.get(m) or {}).get('n_cosponsored') or 0) for m in MEM])   # missing -> 0 (pinned)
    g_by_bio = {}
    with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row.get('bioguide_id'): g_by_bio[row['bioguide_id']] = row.get('gender', '')
    n2b = {m['member']: m['bioguide'] for m in json.load(open(B + r'\data\engine\congestion.json')) if m.get('bioguide')}
    gen = np.array([1.0 if g_by_bio.get(n2b.get(m, ''), '') == 'F' else 0.0 for m in MEM])
    party = np.array([1.0 if P.get(m) == 'R' else 0.0 for m in MEM])
    return dict(MEM=MEM, alltext=alltext, CRIT=CRIT, coopv2=coopv2, mask=mask, facets=facets,
                ncosp=ncosp, gen=gen, party=party, P=P)

def eval_bars_A(predA2, y_s, PRED, CRIT, mask, Zvol, label=''):
    """The five locked bars for A_v2. Row set = O/C/E/ES (coop_v1 excluded per prereg bar 3);
    column competitors = ALL FIVE §17 models incl. A_v1 (bar 4, strict)."""
    own, pp, para = BI.exact_perm_p(predA2, y_s)
    row_ok = all(abs(own) >= abs(rr(predA2, CRIT[c][mask])) for c in ['O', 'C', 'E', 'ES']) and own > 0
    col = {c: abs(rr(PRED[c][mask], y_s)) for c in CORE}
    col_ok = all(abs(own) > v for v in col.values())
    r_part, p_part = NM.fw_partial(predA2, y_s, Zvol)
    ok = (own >= 0.15) and (pp < .05) and row_ok and col_ok and (r_part >= 0.10) and (p_part < .05)
    d = {'own': own, 'perm_p': pp, 'param_p': para, 'row_dom': row_ok, 'col_ok': col_ok,
         'col_max_rival': max(col.values()), 'partial': r_part, 'partial_p': p_part, 'pass': ok}
    if label:
        print('  [%s] bar1 own r=%+.3f | bar2 perm-p=%.4f | bar3 row-dom=%s | bar4 col-specific=%s '
              '(max rival |r|=%.3f) | bar5 partial=%+.3f p=%.4f  =>  %s'
              % (label, own, pp, row_ok, col_ok, d['col_max_rival'], r_part, p_part, 'PASS' if ok else 'MISS'))
    return d

def simulate_battery(X, D):
    """Round-4 battery adapted per the prereg: fw conditioning = [gender, party, log1p(ncosp)];
    sim(c) volume term = log1p(ncosp); sim(d) INCLUDES a volume loading. No real pairing computed."""
    mask = D['mask']; Xs = X[mask]; ns = int(mask.sum())
    gen_s, party_s, lv_s = D['gen'][mask], D['party'][mask], np.log1p(D['ncosp'][mask])
    Zvol = np.column_stack([gen_s, party_s, lv_s])
    green = True
    # (a) PLANTED (seed 424242, orthogonalized): core >=4/5; masked-A plant clears bars 1-4
    rng = np.random.default_rng(424242)
    yc, yn = NM.plant_set(X, Xs, mask, rng)
    pc = {t: BI.oof(X, yc[t]) for t in CORE}
    npass = 0
    for t in CORE:
        o, p, _ = BI.exact_perm_p(pc[t], yc[t])
        dom = all(abs(o) >= abs(rr(pc[t], yc[c])) for c in CORE if c != t) and o > 0
        npass += (o >= 0.15 and p < .05 and dom)
    pn = BI.oof(Xs, yn)
    o, p, _ = BI.exact_perm_p(pn, yn)
    rown = all(abs(o) >= abs(rr(pn, yc[c][mask])) for c in ['O', 'C', 'E', 'ES']) and o > 0
    coln = all(abs(o) > abs(rr(pc[c][mask], yn)) for c in CORE)
    rp, _pp = NM.fw_partial(pn, yn, Zvol)
    a_ok = (npass >= 4) and (o >= 0.15 and p < .05 and rown and coln)
    green &= a_ok
    print('  sim(a) PLANTED: core %d/5 (need>=4) | A own %+.3f p %.4f row %s col %s (bar5 %+.3f rpt) -> %s'
          % (npass, o, p, rown, coln, rp, 'OK' if a_ok else 'FAIL'))
    # (b) NULL (seed 535353): permuted real criteria -> core 0/5; masked-A leg misses the 5-bar stack
    rng = np.random.default_rng(535353)
    ycn = {t: rng.permutation(D['CRIT'][t]) for t in CORE}
    yn0 = rng.permutation(D['coopv2'][mask])
    pcn = {t: BI.oof(X, ycn[t]) for t in CORE}
    n0 = 0
    for t in CORE:
        o, p, _ = BI.exact_perm_p(pcn[t], ycn[t])
        dom = all(abs(o) >= abs(rr(pcn[t], ycn[c])) for c in CORE if c != t) and o > 0
        n0 += (o >= 0.15 and p < .05 and dom)
    dA = eval_bars_A(BI.oof(Xs, yn0), yn0, pcn, {t: ycn[t] for t in CORE}, mask, Zvol)
    b_ok = (n0 == 0) and (not dA['pass'])
    green &= b_ok
    print('  sim(b) NULL: core %d/5 (need 0) | A 5-bar stack pass=%s (need MISS) -> %s' % (n0, dA['pass'], 'OK' if b_ok else 'FAIL'))
    # (c) CONFOUND-ONLY (seed 646464): gender/party/VOLUME-driven criterion -> bar 5 must REFUSE
    rng = np.random.default_rng(646464)
    ycf = zz(0.8 * zz(gen_s) - 0.5 * zz(party_s) + 0.6 * zz(lv_s)) + 0.5 * rng.standard_normal(ns)
    pcf = BI.oof(Xs, ycf)
    o, p, _ = BI.exact_perm_p(pcf, ycf)
    rp, _pp = NM.fw_partial(pcf, ycf, Zvol)
    c_ok = rp < 0.10
    green &= c_ok
    print('  sim(c) CONFOUND-ONLY (incl. volume): bars1-2 own %+.3f p %.4f (may pass) | bar5 partial %+.3f (<.10) -> %s'
          % (o, p, rp, 'OK' if c_ok else 'FAIL'))
    # (d) TRUE+CONFOUND (seed 757575): planted signal + gender/party AND VOLUME loadings -> bar 5 must ADMIT
    rng = np.random.default_rng(757575)
    sig = zz(Xs @ rng.standard_normal(Xs.shape[1]))
    ytc = 0.5 * sig + 0.4 * zz(gen_s) + 0.25 * zz(party_s) + 0.35 * zz(lv_s) + 0.4 * rng.standard_normal(ns)
    ptc = BI.oof(Xs, ytc)
    rp, ppart = NM.fw_partial(ptc, ytc, Zvol)
    d_ok = rp >= 0.10 and ppart < .05
    green &= d_ok
    print('  sim(d) TRUE+CONFOUND (incl. volume loading): bar5 partial %+.3f p %.4f (>=.10) -> %s'
          % (rp, ppart, 'OK' if d_ok else 'FAIL'))
    print('  SIMULATE BATTERY: %s' % ('ALL GREEN' if green else '!! FAILURE — look forbidden'))
    return green

def run(simulate_only):
    D = load_all()
    MEM, mask = D['MEM'], D['mask']
    nM, nS = len(MEM), int(mask.sum())
    print('rosters: MEM=%d (pinned %d) | MEM_A=%d (pinned %d)' % (nM, PIN['MEM'], nS, PIN['MEM_A']))
    if not simulate_only and (nM != PIN['MEM'] or nS != PIN['MEM_A']):
        print('!! LOOK REFUSED (not consumed): roster drift vs pinned lock values'); return
    X = BI.featurize(MEM, D['alltext'])
    if not simulate_battery(X, D):
        if not simulate_only: print('!! LOOK REFUSED (not consumed): simulate battery failure')
        return
    if simulate_only:
        print('simulate complete — no look consumed'); return

    # ============================== THE ONE LOOK ==============================
    CRIT = D['CRIT']; y_s = D['coopv2'][mask]
    Zvol = np.column_stack([D['gen'][mask], D['party'][mask], np.log1p(D['ncosp'][mask])])
    PRED = {t: BI.oof(X, CRIT[t]) for t in CORE}     # five §17 models, computed once on MEM, sliced below
    predA2 = BI.oof(X[mask], y_s)
    ROWS = CORE + ['A2']; COLS = CORE + ['CV2']
    print('\n=== LOOK: masked discriminant matrix corr(held-out pred_row, criterion_col) ===')
    print('    cells involving cooperation_v2 / pred_A_v2: n=%d; all other cells: n=%d' % (nS, nM))
    print('  model\\crit ' + ''.join('%9s' % c for c in COLS))
    for t in ROWS:
        row = []
        for c in COLS:
            if t == 'A2' and c == 'CV2': r = rr(predA2, y_s)
            elif t == 'A2':              r = rr(predA2, CRIT[c][mask])
            elif c == 'CV2':             r = rr(PRED[t][mask], y_s)
            else:                        r = rr(PRED[t], CRIT[c])
            row.append(r)
        print('  pred_%-4s ' % t + ''.join('%+9.3f' % x for x in row))
    for t in CORE:
        d = rr(PRED[t], CRIT[t]) - S17_DIAG[t]
        if abs(d) > 0.02: print('  input-drift note: %s diagonal %+.3f vs §17 %+.3f (Δ%+.3f) — drift only, no status change'
                                % (t, rr(PRED[t], CRIT[t]), S17_DIAG[t], d))
    print('\n=== LOOK: the five locked bars for A_v2 ===')
    dA = eval_bars_A(predA2, y_s, PRED, CRIT, mask, Zvol, label='primary')
    print('\n=== LOOK: demotion checks (§17-passing traits vs the cooperation-v2 column, on MEM_A) ===')
    for t in S17_PASS:
        r_new = rr(PRED[t][mask], y_s); r_own = rr(PRED[t][mask], CRIT[t][mask])
        if abs(r_new) > abs(r_own):
            wt, wp = NM.williams_t(PRED[t][mask], y_s, CRIT[t][mask])
            tag = 'DEMOTION EVENT (not row-dominant on the cooperation-v2 stratum)' if wp < .05 else 'stratum inversion, n.s.'
            print('  %s: |r_cv2|=%.3f > |r_own|=%.3f | Williams t=%.2f p=%.4f -> %s' % (t, abs(r_new), abs(r_own), wt, wp, tag))
        else:
            print('  %s: |r_cv2|=%.3f <= |r_own|=%.3f -> no inversion' % (t, abs(r_new), abs(r_own)))
    print('\n=== LOOK: secondaries (reported, non-gating, firewalled) ===')
    for k, lab in (('c1_bip_z', 'c1 bipartisan share'), ('c2_xshare_z', 'c2 cross-party share'), ('c3_recip_z', 'c3 reciprocity')):
        v = D['facets'][k][mask]; ok = ~np.isnan(v)
        print('  facet r(pred_A_v2, %-22s) = %+.3f (n=%d)' % (lab, rr(predA2[ok], v[ok]), int(ok.sum())))
    print('  r(pred_A_v2, coop_v1) on MEM_A        = %+.3f  (still chasing volume?)' % rr(predA2, CRIT['A'][mask]))
    pm = D['party'][mask]
    for lab, sel in (('D-only', pm < 0.5), ('R-only', pm > 0.5)):
        print('  within-party own r (%s, n=%d)      = %+.3f' % (lab, int(sel.sum()), rr(predA2[sel], y_s[sel])))
    print('  repro: r(pred_A_v1, coop_v1) MEM=%+.3f | MEM_A=%+.3f' % (rr(PRED['A'], CRIT['A']), rr(PRED['A'][mask], CRIT['A'][mask])))
    print('  raw corr: gender %+.3f | party %+.3f' % (rr(predA2, D['gen'][mask]), rr(predA2, pm)))
    red = None
    if dA['pass']:
        print('\n=== LOOK: adversarial re-derivation (fresh OOF seeds %s; perm seeds pinned) ===' % RESEEDS)
        try:
            BI.SEEDS = RESEEDS
            PRED2 = {t: BI.oof(X, CRIT[t]) for t in CORE}
            predA2b = BI.oof(X[mask], y_s)
        finally:
            BI.SEEDS = SEEDS17
        red = eval_bars_A(predA2b, y_s, PRED2, CRIT, mask, Zvol, label='re-derivation')
        if not red['pass']: print('  -> FRAGILE = MISS for ALL consequences INCLUDING closure (prereg rule)')
    print('\n=== VERDICT ===')
    if dA['pass'] and red and red['pass']:
        print('  A_v2 PASS (bars 1-5, re-derived): "A: criterion-valid AND distinct against the de-entangled')
        print('  cooperation criterion (v2; MEM_A=290 stratum)" — consequences per prereg; NO engine change from this look.')
    elif dA['pass'] and red and not red['pass']:
        print('  A_v2 FRAGILE = MISS: route CLOSED (both runs recorded verbatim).')
    else:
        print('  A_v2 MISS: "text-A vs de-entangled cooperation: not distinct — route CLOSED" (closure covers any')
        print('  composite over {c1,c2,c3}). A stays criterion-valid / not-discriminant.')
    fp = fingerprints()
    print('\n  fingerprints at look: cv %s… bd %s… vv %s… pn %s… speeches %d posts %d | non-finite events: %d'
          % (fp['cv_sha'][:10], fp['bd_sha'][:10], fp['vv_sha'][:10], fp['pn_sha'][:10], fp['speeches'], fp['posts'], NM.NONFIN[0]))
    print('\nAppend the RESULT-A section to big5_a_matrix_prereg.md verbatim (MISSes included) and commit same session.')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--simulate', action='store_true'); ap.add_argument('--look', action='store_true')
    args = ap.parse_args()
    print('psych-audit round 5 — A vs de-entangled cooperation (prereg: big5_a_matrix_prereg.md)')
    pre = open(PREREG, encoding='utf-8').read() if os.path.exists(PREREG) else ''
    if not pre: print('!! prereg ABSENT — nothing can run'); return
    locked = 'STATUS: LOCKED' in pre
    taken = '\n## RESULT-A' in pre        # newline-anchored heading match (@72690c9 lesson)
    fp = fingerprints()
    fp_ok = all(fp[k] == PIN[k] for k in ('cv_sha', 'bd_sha', 'vv_sha', 'pn_sha', 'speeches', 'posts'))
    print('  lock: %s | look: %s | input fingerprints: %s' % (locked, 'TAKEN' if taken else 'not yet taken',
                                                              'MATCH' if fp_ok else 'DRIFT'))
    if not (args.simulate or args.look): return
    if args.look:
        if not locked: print('!! LOOK REFUSED: prereg not LOCKED'); return
        if taken: print('!! LOOK REFUSED: RESULT-A already recorded (one look only)'); return
        if not fp_ok: print('!! LOOK REFUSED (not consumed): input fingerprint drift vs pinned lock values'); return
    run(simulate_only=not args.look)

if __name__ == '__main__':
    main()
