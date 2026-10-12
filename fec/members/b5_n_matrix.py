# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""PSYCH-AUDIT ROUND 4 — the fifth trait re-anchored as NEUROTICISM (big5_n_matrix_prereg.md, LOCKED
2026-07-09 pre-look, adversarially panel-reviewed). Supervised Empath text read of N validated against
the behavioral criterion reactivity_z (round-3's engine N-slot). Machinery IMPORTED VERBATIM from the
§17 look (b5_behavioral_instrument.py: featurize/oof/exact_perm_p/load_corpus/criteria); the only new
numerics (all pinned in the prereg) are the masked matrix, the volume-hardened Frisch-Waugh gating
partial (b5_es_robustness.py idiom, perm seed 20260710), Williams' t demotion guard, the noiv
ship-gate corpus, and the simulate battery (a)-(d). ONE look, gated on: prereg present + 'STATUS:
LOCKED' + no newline-anchored RESULT-N heading + input fingerprints/rosters match the pinned values +
the in-process simulate battery green. Honest prior: LOW. No LLM calls. No audio."""
import sys, os, json, csv, math, hashlib, sqlite3, argparse
import numpy as np
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import members.b5_behavioral_instrument as BI     # import-safe: all execution behind __main__
import moneyball_core as MC
B = MB_ROOT
# The prereg travels WITH THE CODE (script-relative checkout root), so the gate always reads the same
# tree the look runs from; data artifacts + caches stay on MB_ROOT (the live tree) like every consumer.
PREREG = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))), 'big5_n_matrix_prereg.md')
CACHE_NOIV = B + r'\data\members\b5_empath_cache_noiv.json'
CORE = ['O', 'C', 'E', 'A', 'ES']
TR6 = CORE + ['N']
SEEDS17 = [7, 17, 29, 43, 61]             # the locked §17 OOF seeds (BI.SEEDS)
RESEEDS = [101, 113, 127, 139, 151]       # fresh-seed re-derivation (fold-partition robustness)
PLANT_SEEDS = [424242]                     # historical round-4 in-process battery default
PLANT_MIN_PASS = 1                         # successors may predeclare a multi-seed aggregate gate
S17_DIAG = {'O': .198, 'C': .277, 'E': .552, 'A': .183, 'ES': .149}   # §17 diagonals (drift report only)
S17_PASS = ['O', 'C', 'E']                # §17-passing traits eligible for the demotion check
PIN = {'MEM': 456, 'MEM_N': 201,
       'bd_sha': '6b9bb9d2a15ce9be1ddd0a8ec63c51fef1449e68f37ffda08b3c70e6564397d7',
       'ix_sha': '55d908d7ed1a7b1d0b7cb889f01484f6d454a9bed57847cf39ebfdcf68f26553',
       'speeches': 234827, 'posts': 29098}
NONFIN = [0]                              # prereg non-finite rule: recorded as 0 (-> MISS), counted

def rr(a, b):
    with np.errstate(all='ignore'):
        r = np.corrcoef(a, b)[0, 1]
    if np.isfinite(r): return float(r)
    NONFIN[0] += 1; return 0.0

def zz(v):
    v = np.asarray(v, float); return (v - v.mean()) / (v.std(ddof=1) or 1.0)

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for ch in iter(lambda: f.read(1 << 20), b''): h.update(ch)
    return h.hexdigest()

def fingerprints():
    con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
    ns = con.execute('select count(*) from speeches').fetchone()[0]
    npo = con.execute('select count(*) from posts').fetchone()[0]
    con.close()
    return {'bd_sha': sha(B + r'\behavioral_dispositions.json'),
            'ix_sha': sha(B + r'\data\lobbying\interaction_features.json'),
            'vv_sha': sha(B + r'\data\members\voteview_consistency.json'),
            'bp_sha': sha(B + r'\behavior_panel.json'),
            'speeches': ns, 'posts': npo}

def load_all():
    BD, VV, P, MEM, alltext = BI.load_corpus()
    CRIT = BI.criteria(BD, VV, P, MEM)                       # O/C/E/A + ES=residual-GMP (§17 model set)
    react = np.array([BD[m].get('reactivity_z') if BD[m].get('reactivity_z') is not None else np.nan
                      for m in MEM], float)
    mask = ~np.isnan(react)
    IX = json.load(open(B + r'\data\lobbying\interaction_features.json'))['members']
    turns = np.array([(IX.get(m) or {}).get('turns', 0) for m in MEM], float)
    cutoff = np.array([(IX.get(m) or {}).get('cutoff') if (IX.get(m) or {}).get('cutoff') is not None
                       else np.nan for m in MEM], float)
    nprog = np.array([(IX.get(m) or {}).get('n_programs', 0) for m in MEM], float)
    g_by_bio = {}
    with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:      # b5_es_robustness gender idiom
        for row in csv.DictReader(f):
            if row.get('bioguide_id'): g_by_bio[row['bioguide_id']] = row.get('gender', '')
    n2b = {m['member']: m['bioguide'] for m in json.load(open(B + r'\data\engine\congestion.json')) if m.get('bioguide')}
    gvals = [g_by_bio.get(n2b.get(m, ''), '') for m in MEM]
    nogen = sum(1 for g in gvals if g not in ('M', 'F'))
    gen = np.array([1.0 if g == 'F' else 0.0 for g in gvals])
    party = np.array([1.0 if P.get(m) == 'R' else 0.0 for m in MEM])
    sh = np.sqrt(np.minimum(1.0, turns / 150.0))
    return dict(BD=BD, MEM=MEM, alltext=alltext, CRIT=CRIT, react=react, mask=mask, turns=turns,
                cutoff=cutoff, nprog=nprog, gen=gen, nogen=nogen, party=party, sh=sh)

def resid(v, Z):
    Z1 = np.column_stack([np.ones(len(v)), Z]); b, *_ = np.linalg.lstsq(Z1, v, rcond=None); return v - Z1 @ b

def fw_partial(pred, y, Z, seed=20260710, K=5000):
    """Frisch-Waugh partial + exact perm p (b5_es_robustness.partial_test, generalized conditioning)."""
    rp, ry = resid(pred, Z), resid(y, Z)
    r_part = rr(rp, ry)
    rng = np.random.default_rng(seed)
    null = np.array([rr(rp, rng.permutation(ry)) for _ in range(K)])
    return r_part, float((np.sum(null >= r_part) + 1) / (K + 1))

def williams_t(pred, a, b):
    """Williams' t (Steiger 1980), dependent overlapping correlations sharing pred; sign-aligned so the
    compared pair is positive (the prereg demotion-guard recipe). Returns (t, two-sided p)."""
    from scipy import stats
    r12, r13 = rr(pred, a), rr(pred, b)
    if r12 < 0: a = -np.asarray(a); r12 = -r12
    if r13 < 0: b = -np.asarray(b); r13 = -r13
    r23 = rr(a, b); n = len(pred)
    detR = 1 - r12 ** 2 - r13 ** 2 - r23 ** 2 + 2 * r12 * r13 * r23
    rbar = (r12 + r13) / 2.0
    den = 2 * detR * (n - 1) / (n - 3) + rbar ** 2 * (1 - r23) ** 3
    if den <= 0: return 0.0, 1.0
    t = (r12 - r13) * math.sqrt((n - 1) * (1 + r23) / den)
    return float(t), float(2 * stats.t.sf(abs(t), n - 3))

def eval_bars(predN, react_s, PRED, CRIT, mask, Zvol, label=''):
    """The five locked bars for N. PRED = full-roster core model predictions (dict trait->vector)."""
    own, pp, para = BI.exact_perm_p(predN, react_s)                        # bars 1+2 (seed 20260708)
    row_ok = all(abs(own) >= abs(rr(predN, CRIT[c][mask])) for c in ['O', 'C', 'E', 'A']) and own > 0
    col = {c: abs(rr(PRED[c][mask], react_s)) for c in CORE}               # bar 4 competitors incl. ES
    col_ok = all(abs(own) > v for v in col.values())                       # strict; exact tie = FAIL
    r_part, p_part = fw_partial(predN, react_s, Zvol)                      # bar 5 (seed 20260710)
    ok = (own >= 0.15) and (pp < .05) and row_ok and col_ok and (r_part >= 0.10) and (p_part < .05)
    d = {'own': own, 'perm_p': pp, 'param_p': para, 'row_dom': row_ok, 'col_ok': col_ok,
         'col_max_rival': max(col.values()) if col else 0.0, 'partial': r_part, 'partial_p': p_part, 'pass': ok}
    if label:
        print('  [%s] bar1 own r=%+.3f | bar2 perm-p=%.4f | bar3 row-dom=%s | bar4 col-specific=%s '
              '(max rival |r|=%.3f) | bar5 partial=%+.3f p=%.4f  =>  %s'
              % (label, own, pp, row_ok, col_ok, d['col_max_rival'], r_part, p_part,
                 'PASS' if ok else 'MISS'))
    return d

def plant(Xm, rng, target=0.4):
    w = rng.standard_normal(Xm.shape[1]); raw = zz(Xm @ w)
    return target * raw + math.sqrt(max(1e-9, 1 - target ** 2)) * rng.standard_normal(Xm.shape[0])

def plant_set(X, Xs, mask, rng, target=0.4):
    """ADDENDUM 1: sim(a) plants with ORTHOGONALIZED signal components (Gram-Schmidt in signal space) —
    battery run #1 showed random directions over the low-effective-rank Empath matrix share X-span
    variance that OOF predictions amplify (pred_N read the E-plant at -.224 vs own +.187); the real
    criteria are empirically near-orthogonal (reactivity worst |r|=.15), so distinct plants must be
    distinct by construction. Same seed/target/noise model; pass requirements unchanged."""
    S = []
    for _ in CORE:
        s = zz(X @ rng.standard_normal(X.shape[1]))
        for s0 in S: s = s - rr(s, s0) * s0
        S.append(zz(s))
    yc = {t: target * S[i] + math.sqrt(max(1e-9, 1 - target ** 2)) * rng.standard_normal(X.shape[0])
          for i, t in enumerate(CORE)}
    sn = zz(Xs @ rng.standard_normal(Xs.shape[1]))
    for s0 in S:
        s0s = zz(s0[mask]); sn = sn - rr(sn, s0s) * s0s
    sn = zz(sn)
    yn = target * sn + math.sqrt(max(1e-9, 1 - target ** 2)) * rng.standard_normal(Xs.shape[0])
    return yc, yn

def simulate_battery(X, D):
    """Pinned battery (a)-(d) per the prereg. NO real trait x criterion pairing is ever computed."""
    mask = D['mask']; Xs = X[mask]
    react_s_shape = int(mask.sum())
    gen_s, party_s = D['gen'][mask], D['party'][mask]
    lt_s, sh_s = np.log1p(D['turns'][mask]), D['sh'][mask]
    Zvol = np.column_stack([gen_s, party_s, lt_s, sh_s])
    green = True
    # (a) PLANTED: the historical round-4 default is the original single seed. A successor may
    # predeclare a multi-seed aggregate for masked N while retaining the original core >=4/5 check
    # on seed 424242. This resolves seed-draw instability without changing a real-result bar.
    core_first = 0; nplant = 0
    for j, sd in enumerate(PLANT_SEEDS):
        rng = np.random.default_rng(sd)
        yc, yn = plant_set(X, Xs, mask, rng)
        pc = {t: BI.oof(X, yc[t]) for t in CORE}
        npass = 0
        for t in CORE:
            o, p, _ = BI.exact_perm_p(pc[t], yc[t])
            dom = all(abs(o) >= abs(rr(pc[t], yc[c])) for c in CORE if c != t) and o > 0
            npass += (o >= 0.15 and p < .05 and dom)
        if j == 0: core_first = npass
        pn = BI.oof(Xs, yn)
        o, p, _ = BI.exact_perm_p(pn, yn)
        rown = all(abs(o) >= abs(rr(pn, yc[c][mask])) for c in ['O', 'C', 'E', 'A']) and o > 0
        coln = all(abs(o) > abs(rr(pc[c][mask], yn)) for c in CORE)
        rp, ppart = fw_partial(pn, yn, Zvol)
        one_ok = o >= 0.15 and p < .05 and rown and coln
        nplant += one_ok
        print('  sim(a) seed %d: core %d/5 | N own %+.3f p %.4f row %s col %s -> %s'
              % (sd, npass, o, p, rown, coln, 'PASS' if one_ok else 'miss'))
    a_ok = (core_first >= 4) and (nplant >= PLANT_MIN_PASS)
    green &= a_ok
    print('  sim(a) PLANTED SUMMARY: seed-424242 core %d/5 (need>=4) | N bars1-4 %d/%d (need>=%d) -> %s'
          % (core_first, nplant, len(PLANT_SEEDS), PLANT_MIN_PASS, 'OK' if a_ok else 'FAIL'))
    # (b) NULL (seed 535353): real criteria permuted pre-pairing -> core 0/5; N misses full 5-bar stack
    rng = np.random.default_rng(535353)
    ycn = {t: rng.permutation(D['CRIT'][t]) for t in CORE}
    yn0 = rng.permutation(D['react'][mask])
    pcn = {t: BI.oof(X, ycn[t]) for t in CORE}
    n0 = 0
    for t in CORE:
        o, p, _ = BI.exact_perm_p(pcn[t], ycn[t])
        dom = all(abs(o) >= abs(rr(pcn[t], ycn[c])) for c in CORE if c != t) and o > 0
        n0 += (o >= 0.15 and p < .05 and dom)
    dN = eval_bars(BI.oof(Xs, yn0), yn0, pcn, {t: ycn[t] for t in CORE}, mask, Zvol)
    b_ok = (n0 == 0) and (not dN['pass'])
    green &= b_ok
    print('  sim(b) NULL: core %d/5 (need 0) | N 5-bar stack pass=%s (need MISS) -> %s'
          % (n0, dN['pass'], 'OK' if b_ok else 'FAIL'))
    # (c) CONFOUND-ONLY (seed 646464): criterion from gender/party/volume alone -> bar 5 must FAIL
    rng = np.random.default_rng(646464)
    ycf = zz(0.8 * zz(gen_s) - 0.5 * zz(party_s) + 0.6 * zz(lt_s)) + 0.5 * rng.standard_normal(react_s_shape)
    pcf = BI.oof(Xs, ycf)
    o, p, _ = BI.exact_perm_p(pcf, ycf)
    rp, ppart = fw_partial(pcf, ycf, Zvol)
    c_ok = rp < 0.10
    green &= c_ok
    print('  sim(c) CONFOUND-ONLY: bars1-2 own %+.3f p %.4f (may pass) | bar5 partial %+.3f (must be <.10) -> %s'
          % (o, p, rp, 'OK' if c_ok else 'FAIL'))
    # (d) TRUE+CONFOUND (seed 757575): planted signal + gender/party loading -> bar 5 must PASS
    rng = np.random.default_rng(757575)
    sig = zz(Xs @ rng.standard_normal(Xs.shape[1]))
    ytc = 0.5 * sig + 0.5 * zz(gen_s) + 0.3 * zz(party_s) + 0.4 * rng.standard_normal(react_s_shape)
    ptc = BI.oof(Xs, ytc)
    rp, ppart = fw_partial(ptc, ytc, Zvol)
    d_ok = rp >= 0.10 and ppart < .05
    green &= d_ok
    print('  sim(d) TRUE+CONFOUND: bar5 partial %+.3f p %.4f (must pass >=.10) -> %s'
          % (rp, ppart, 'OK' if d_ok else 'FAIL'))
    print('  SIMULATE BATTERY: %s' % ('ALL GREEN' if green else '!! FAILURE — look forbidden'))
    return green

def noiv_predictions(D, mask_elig_out):
    """The noiv ship-gate corpus: speeches WHERE source != 'INTERVIEW' (CREC+CHRG, deliberate) +
    non-repost posts; featurized with the module CACHEF swapped to CACHE_NOIV (the warm §17 cache is
    NEVER touched); z within full MEM; train/eval on MEM_N ∩ {noiv text > 2000 chars}."""
    MEM = D['MEM']
    con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
    SPE2 = {m: (t or '') for m, t in con.execute(
        "select member, group_concat(text,' ') from speeches where source != 'INTERVIEW' group by member")}
    b2n = MC.bio2name(); TW = {}
    for bg, t in con.execute("select bioguide, group_concat(text,' ') from posts where repost_of is null group by bioguide"):
        m = b2n.get(bg)
        if m: TW[m] = (t or '')
    con.close()
    def alltext_noiv(m): return (SPE2.get(m, '') + ' ' + TW.get(m, ''))[:600000]
    elig = np.array([len(alltext_noiv(m)) > 2000 for m in MEM]) & D['mask']
    mask_elig_out.append(elig)
    old = BI.CACHEF
    try:
        BI.CACHEF = CACHE_NOIV
        Xn = BI.featurize(MEM, alltext_noiv)
    finally:
        BI.CACHEF = old
    return BI.oof(Xn[elig], D['react'][elig])

def interview_share(D):
    """Diagnostic (uncapped approximation, per prereg): interview chars / total corpus chars, MEM_N."""
    con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
    IVL = {m: (n or 0) for m, n in con.execute(
        "select member, sum(length(text)) from speeches where source='INTERVIEW' group by member")}
    con.close()
    sh = sorted(min(IVL.get(m, 0), 600000) / max(1.0, float(len(D['alltext'](m))))
                for m, keep in zip(D['MEM'], D['mask']) if keep)
    return sh[len(sh) // 2], sh[int(len(sh) * 0.75)]

def run(simulate_only):
    D = load_all()
    MEM, mask, CRIT, react = D['MEM'], D['mask'], D['CRIT'], D['react']
    nM, nS = len(MEM), int(mask.sum())
    print('rosters: MEM=%d (pinned %d) | MEM_N=%d (pinned %d) | no-gender drops=%d (pinned 0)'
          % (nM, PIN['MEM'], nS, PIN['MEM_N'], D['nogen']))
    if not simulate_only and (nM != PIN['MEM'] or nS != PIN['MEM_N']):
        print('!! LOOK REFUSED (not consumed): roster drift vs pinned lock values'); return
    X = BI.featurize(MEM, D['alltext'])
    if not simulate_battery(X, D):
        if not simulate_only: print('!! LOOK REFUSED (not consumed): simulate battery failure')
        return
    if simulate_only:
        print('simulate complete — no look consumed (no real trait x criterion pairing computed)'); return

    # ============================== THE ONE LOOK ==============================
    react_s = react[mask]
    Zvol = np.column_stack([D['gen'][mask], D['party'][mask], np.log1p(D['turns'][mask]), D['sh'][mask]])
    PRED = {t: BI.oof(X, CRIT[t]) for t in CORE}          # §17 six-model reproduction (five core models)
    predN = BI.oof(X[mask], react_s)
    # masked matrix (cells involving N on MEM_N; the rest on MEM)
    print('\n=== LOOK: masked discriminant matrix corr(held-out pred_row, criterion_col) ===')
    print('    cells involving N: n=%d; all other cells: n=%d' % (nS, nM))
    print('  model\\crit ' + ''.join('%9s' % c for c in TR6))
    for t in TR6:
        row = []
        for c in TR6:
            if t == 'N' and c == 'N': r = rr(predN, react_s)
            elif t == 'N':            r = rr(predN, CRIT[c][mask])
            elif c == 'N':            r = rr(PRED[t][mask], react_s)
            else:                     r = rr(PRED[t], CRIT[c])
            row.append(r)
        print('  pred_%-3s  ' % t + ''.join('%+9.3f' % x for x in row))
    # §17 drift report (no-status-change clause: informational ONLY)
    for t in CORE:
        d = rr(PRED[t], CRIT[t]) - S17_DIAG[t]
        if abs(d) > 0.02: print('  input-drift note: %s diagonal %+.3f vs §17 %+.3f (Δ%+.3f) — drift only, no status change' % (t, rr(PRED[t], CRIT[t]), S17_DIAG[t], d))
    # the five bars
    print('\n=== LOOK: the five locked bars for N ===')
    dN = eval_bars(predN, react_s, PRED, CRIT, mask, Zvol, label='primary')
    # demotion checks (pinned rule: wholly on MEM_N, Williams-guarded)
    print('\n=== LOOK: demotion checks (§17-passing traits, on MEM_N) ===')
    for t in S17_PASS:
        r_react = rr(PRED[t][mask], react_s); r_own = rr(PRED[t][mask], CRIT[t][mask])
        if abs(r_react) > abs(r_own):
            wt, wp = williams_t(PRED[t][mask], react_s, CRIT[t][mask])
            tag = 'DEMOTION EVENT (not row-dominant on the interview stratum)' if wp < .05 else 'stratum inversion, n.s.'
            print('  %s: |r_react|=%.3f > |r_own|=%.3f | Williams t=%.2f p=%.4f -> %s' % (t, abs(r_react), abs(r_own), wt, wp, tag))
        else:
            print('  %s: |r_react|=%.3f <= |r_own|=%.3f -> no inversion' % (t, abs(r_react), abs(r_own)))
    # secondaries (reported; firewall per prereg)
    print('\n=== LOOK: secondaries (reported, non-gating) ===')
    unshr = D['cutoff'][mask]
    print('  r(pred_N, unshrunken cutoff) = %+.3f' % rr(predN, unshr))
    rp_gp, pp_gp = fw_partial(predN, react_s, np.column_stack([D['gen'][mask], D['party'][mask]]))
    print('  gender/party-only partial   = %+.3f p=%.4f (round-2 continuity)' % (rp_gp, pp_gp))
    rp_lt, pp_lt = fw_partial(predN, react_s, np.column_stack([np.log1p(D['turns'][mask])]))
    print('  log-turns-only partial      = %+.3f p=%.4f' % (rp_lt, pp_lt))
    print('  raw corr: gender %+.3f | party %+.3f' % (rr(predN, D['gen'][mask]), rr(predN, D['party'][mask])))
    mp = D['nprog'][mask] >= 2
    print('  own r on n_programs>=2 subset (n=%d) = %+.3f' % (int(mp.sum()), rr(predN[mp], react_s[mp])))
    med, p75 = interview_share(D)
    print('  interview share of corpus (uncapped approx): median %.2f | p75 %.2f' % (med, p75))
    # noiv ship-gate
    print('\n=== LOOK: noiv ship-gate (CREC+CHRG+posts, interview text excluded) ===')
    holder = []
    predNoiv = noiv_predictions(D, holder)
    elig = holder[0]
    o_nv, p_nv, _ = BI.exact_perm_p(predNoiv, react[elig])
    ship = (o_nv > 0) and (p_nv < .05)
    half = o_nv >= dN['own'] / 2.0
    print('  noiv n=%d (dropped %d) | r=%+.3f perm-p=%.4f -> ship-gate %s%s'
          % (int(elig.sum()), nS - int(elig.sum()), o_nv, p_nv, 'PASS' if ship else 'FAIL',
             '' if (not ship or half) else ' | BELOW HALF PRIMARY -> mandatory caveat "interview-leaning text signal"'))
    # re-derivation (only reached on a bars-1-5 pass)
    red = None
    if dN['pass']:
        print('\n=== LOOK: adversarial re-derivation (fresh OOF seeds %s; perm seeds stay pinned) ===' % RESEEDS)
        try:
            BI.SEEDS = RESEEDS
            PRED2 = {t: BI.oof(X, CRIT[t]) for t in CORE}
            predN2 = BI.oof(X[mask], react_s)
        finally:
            BI.SEEDS = SEEDS17
        red = eval_bars(predN2, react_s, PRED2, CRIT, mask, Zvol, label='re-derivation')
        if not red['pass']: print('  -> FRAGILE = MISS for ALL consequences INCLUDING closure (prereg rule)')
    # verdict
    print('\n=== VERDICT ===')
    if dN['pass'] and red and red['pass'] and ship:
        print('  N PASS (bars 1-5, re-derived%s) — consequences (a)-(e) per prereg%s'
              % ('', '' if half else ' + mandatory "interview-leaning text signal" caveat'))
    elif dN['pass'] and red and red['pass'] and not ship:
        print('  N PASS — interview-stratum text only (shared-source-dependent): NO artifact, NO desk reframe; methods+memory record; route CLOSED')
    elif dN['pass'] and red and not red['pass']:
        print('  N FRAGILE = MISS: route CLOSED (both runs recorded verbatim)')
    else:
        print('  N MISS: the Empath-text->reactivity route is CLOSED (one look; closure covers the secondary variants)')
    fp = fingerprints()
    print('\n  fingerprints at look: bd %s… ix %s… vv %s… panel %s… speeches %d posts %d | non-finite events: %d'
          % (fp['bd_sha'][:12], fp['ix_sha'][:12], fp['vv_sha'][:12], fp['bp_sha'][:12],
             fp['speeches'], fp['posts'], NONFIN[0]))
    print('\nAppend the RESULT-N section to %s verbatim (MISSes included) and commit same session.'
          % os.path.basename(PREREG))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--simulate', action='store_true'); ap.add_argument('--look', action='store_true')
    args = ap.parse_args()
    print('psych-audit N-anchored matrix (prereg: %s)' % os.path.basename(PREREG))
    pre = open(PREREG, encoding='utf-8').read() if os.path.exists(PREREG) else ''
    if not pre: print('!! prereg ABSENT — nothing can run'); return
    locked = 'STATUS: LOCKED' in pre
    taken = '\n## RESULT-N' in pre        # newline-anchored heading match (the @72690c9 lesson)
    fp = fingerprints()
    # Successor locks also pin Voteview + behavior-panel inputs. The optional-key shape preserves the
    # historical round-4 lock while allowing b5_n2_matrix.py to harden its fresh lock without changing
    # any statistical machinery.
    fp_keys = ['bd_sha', 'ix_sha', 'vv_sha', 'bp_sha', 'speeches', 'posts']
    fp_ok = all(fp[k] == PIN[k] for k in fp_keys if k in PIN)
    print('  lock: %s | look: %s | input fingerprints: %s'
          % (locked, 'TAKEN' if taken else 'not yet taken', 'MATCH' if fp_ok else 'DRIFT'))
    if not (args.simulate or args.look): return
    if args.look:
        if not locked: print('!! LOOK REFUSED: prereg not LOCKED'); return
        if taken: print('!! LOOK REFUSED: RESULT-N already recorded (one look only)'); return
        if not fp_ok: print('!! LOOK REFUSED (not consumed): input fingerprint drift vs pinned lock values'); return
    run(simulate_only=not args.look)

if __name__ == '__main__':
    main()
