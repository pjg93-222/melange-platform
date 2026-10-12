# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""PSYCH-AUDIT ROUND 6 — text-A vs the de-entangled cooperation criterion, NEW INSTRUMENT BASIS
(big5_a_matrix2_prereg.md; user-authorized "Lock it in" 2026-07-10). Round 5 (Empath instrument)
RETIRED WITHOUT A LOOK; its Addendum-1 successor gate was MET by the st_mean basis (fresh-seed
confirmed, b5_a_stratum_diag_record.md). Instrument = st_mean: all-MiniLM-L6-v2 chunk embeddings,
mean-pooled per member-genre (b5_emb_cache_r6_st.npz, SHA-pinned), within-genre z + token-weighted
cross-genre average (the b5_round4_eval composition, via b5_a_stratum_diag.member_matrix — same
commit). ALL SIX models (O/C/E/A_v1/ES competitors + A_v2) are fit on st_mean features — bar 4
compares within one instrument, never across. Round-5 machinery imported verbatim: load_all /
eval_bars_A / simulate_battery from b5_a_matrix (bars, battery sims a-d incl. volume loadings,
conditioning set [gender, party, log1p(n_cosponsored)]). BI.oof is replaced process-wide by
b5_a_stratum_diag.oof — the SAME SEEDS/ALPHAS/KFold/GridSearchCV recipe with n_jobs distribution
only; numerical identity was demonstrated by the diagnostic's exact Addendum-1 reproduction.
ONE look, gated on: prereg present + 'STATUS: LOCKED' + no newline-anchored RESULT-A2 heading +
input fingerprints/rosters match + in-process battery green (on st_mean features). NO demotion
events this round (the §17 record concerns the Empath instrument; st-model cross-reads cannot
demote it — prereg §"No demotion events"). The ONLY permitted Empath computation is the declared
A_v1×coop_v1 REPRODUCTION secondary, isolated in empath_a1_repro() which returns two floats and
never sees cooperation_v2. Adversarially reviewed pre-lock (statistics + discipline lenses), all
required changes incorporated. Honest prior: MODERATE-LOW. No LLM calls. No audio."""
import sys, os, json, argparse, hashlib
import numpy as np
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import members.b5_behavioral_instrument as BI     # import-safe: exact_perm_p/SEEDS/ALPHAS (oof replaced below)
import members.b5_n_matrix as NM                  # import-safe: rr/zz/fw_partial/NONFIN
import members.b5_a_matrix as AM                  # import-safe: round-5 load_all/eval_bars_A/simulate_battery/fingerprints
import members.b5_a_stratum_diag as DIAG          # import-safe: member_matrix (st composition) + n_jobs-identical oof
B = MB_ROOT
PREREG = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))), 'big5_a_matrix2_prereg.md')
CORE = AM.CORE                            # ['O','C','E','A','ES']; 'A' = A_v1 (old cooperation)
SEEDS17 = [7, 17, 29, 43, 61]
RESEEDS = [101, 113, 127, 139, 151]
PIN = {'MEM': 456, 'MEM_A': 290, 'ST_DIM': 384,
       'cv_sha': '03418fd1d9407f8cb1a15e7beac62fec342c13f8b2fd35122a7748838fd11ecb',
       'bd_sha': '6b9bb9d2a15ce9be1ddd0a8ec63c51fef1449e68f37ffda08b3c70e6564397d7',
       'vv_sha': '0bf7c461e9eca440535484bf325c5b90d423daf0573e984a1b8691a242194751',
       'pn_sha': 'f47b9b045357cad2c5644f551fc934e8a0aa793a22d98272632547a276d22a50',
       'stz_sha': '69b1da00ebcc7bf4ecdbb07676630f39f6bbb01591ad8e8f8a83f6f4edd17619',
       'stm_sha': 'e3189a7a4fcc5d13c4815da65481e5393ff0804d50cb2169f3a14a74ea0f2ea8',
       # ADDENDUM 1: counts moved to REPORTED (features frozen in the SHA-pinned npz; live corpus
       # only feeds MEM eligibility + the reported Empath repro); the gate that replaces them is
       # STRICTLY STRONGER — roster CONTENT hashes, not row counts:
       'mem_md5': '374fa4a44d64b7de3d468913a0297c6d', 'mema_md5': '3346d05a720f58bb5c46198fae1d1048'}
rr = NM.rr
BI.oof = DIAG.oof                         # disclosed in the prereg: identical recipe, n_jobs only

def fingerprints():
    fp = AM.fingerprints()                # cv/bd/vv/pn SHAs + speeches/posts counts
    fp['stz_sha'] = NM.sha(B + r'\data\members\b5_emb_cache_r6_st.npz')
    fp['stm_sha'] = NM.sha(B + r'\data\members\b5_emb_meta_r6_st.json')
    return fp

def empath_a1_repro(D, mask):
    """Prereg-declared REPRODUCTION secondary (Finding 5): r(pred_A_v1[Empath, §17 recipe],
    coop_v1) on MEM and MEM_A — an already-recorded §17 pairing, an accountability check on the
    criterion re-spec. ISOLATION: the Empath prediction vector exists only inside this frame and
    is correlated ONLY with CRIT['A'] (coop_v1). cooperation_v2 is deliberately not passed in."""
    Xe = BI.featurize(D['MEM'], D['alltext'])
    pA1 = BI.oof(Xe, D['CRIT']['A'])
    return rr(pA1, D['CRIT']['A']), rr(pA1[mask], D['CRIT']['A'][mask])

def run(simulate_only, jobs):
    DIAG.JOBS[0] = jobs
    D = AM.load_all()
    MEM, mask = D['MEM'], D['mask']
    nM, nS = len(MEM), int(mask.sum())
    print('rosters: MEM=%d (pinned %d) | MEM_A=%d (pinned %d)' % (nM, PIN['MEM'], nS, PIN['MEM_A']))
    if not simulate_only and (nM != PIN['MEM'] or nS != PIN['MEM_A']):
        print('!! LOOK REFUSED (not consumed): roster drift vs pinned lock values'); return
    mem_md5 = hashlib.md5('|'.join(MEM).encode()).hexdigest()
    mema_md5 = hashlib.md5('|'.join(m for m, k in zip(MEM, mask) if k).encode()).hexdigest()
    print('roster content: MEM md5 %s | MEM_A md5 %s' % (mem_md5, mema_md5))
    if not simulate_only and (mem_md5 != PIN['mem_md5'] or mema_md5 != PIN['mema_md5']):
        print('!! LOOK REFUSED (not consumed): roster CONTENT drift vs Addendum-1 hashes'); return
    X, keep = DIAG.member_matrix('st_mean', MEM)
    if keep != MEM or X.shape[1] != PIN['ST_DIM']:
        print('!! LOOK REFUSED (not consumed): st_mean coverage/dims mismatch (keep %d, dims %d)'
              % (len(keep), X.shape[1])); return
    if not AM.simulate_battery(X, D):     # round-5 battery verbatim, on st_mean features
        if not simulate_only: print('!! LOOK REFUSED (not consumed): simulate battery failure')
        return
    if simulate_only:
        print('simulate complete — no look consumed'); return

    # ============================== THE ONE LOOK ==============================
    CRIT = D['CRIT']; y_s = D['coopv2'][mask]
    Zvol = np.column_stack([D['gen'][mask], D['party'][mask], np.log1p(D['ncosp'][mask])])
    PRED = {t: BI.oof(X, CRIT[t]) for t in CORE}     # five competitor models ON ST FEATURES, full MEM, sliced below
    predA2 = BI.oof(X[mask], y_s)
    ROWS = CORE + ['A2']; COLS = CORE + ['CV2']
    print('\n=== LOOK: masked discriminant matrix corr(held-out pred_row, criterion_col) — st_mean instrument ===')
    print('    cells involving cooperation_v2 / pred_A_v2: n=%d; all other cells: n=%d' % (nS, nM))
    print('    (context only: this is NOT the §17 Empath instrument — no §17 comparison, no status change)')
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
    print('\n=== LOOK: the five locked bars for A_v2 (st_mean) ===')
    dA = AM.eval_bars_A(predA2, y_s, PRED, CRIT, mask, Zvol, label='primary')
    print('\n=== LOOK: secondaries (reported, non-gating, firewalled) ===')
    for k, lab in (('c1_bip_z', 'c1 bipartisan share'), ('c2_xshare_z', 'c2 cross-party share'), ('c3_recip_z', 'c3 reciprocity')):
        v = D['facets'][k][mask]; ok = ~np.isnan(v)
        print('  facet r(pred_A_v2, %-22s) = %+.3f (n=%d)' % (lab, rr(predA2[ok], v[ok]), int(ok.sum())))
    print('  r(pred_A_v2, coop_v1) on MEM_A        = %+.3f  (still chasing volume?)' % rr(predA2, CRIT['A'][mask]))
    pm = D['party'][mask]
    for lab, sel in (('D-only', pm < 0.5), ('R-only', pm > 0.5)):
        print('  within-party own r (%s, n=%d)      = %+.3f' % (lab, int(sel.sum()), rr(predA2[sel], y_s[sel])))
    print('  raw corr: gender %+.3f | party %+.3f' % (rr(predA2, D['gen'][mask]), rr(predA2, pm)))
    rA1_full, rA1_strat = empath_a1_repro(D, mask)
    print('  REPRO (Empath A_v1 x coop_v1, declared): MEM %+.3f (§17 recorded +.183) | MEM_A %+.3f' % (rA1_full, rA1_strat))
    red = None
    if dA['pass']:
        print('\n=== LOOK: adversarial re-derivation (fresh OOF seeds %s; perm seeds pinned) ===' % RESEEDS)
        try:
            BI.SEEDS = RESEEDS
            PRED2 = {t: BI.oof(X, CRIT[t]) for t in CORE}
            predA2b = BI.oof(X[mask], y_s)
        finally:
            BI.SEEDS = SEEDS17
        red = AM.eval_bars_A(predA2b, y_s, PRED2, CRIT, mask, Zvol, label='re-derivation')
        if not red['pass']: print('  -> FRAGILE = MISS for ALL consequences INCLUDING closure (prereg rule)')
    print('\n=== VERDICT ===')
    if dA['pass'] and red and red['pass']:
        print('  A_v2 PASS (bars 1-5, re-derived): "A: criterion-valid AND distinct against the de-entangled')
        print('  cooperation criterion (v2; MEM_A stratum), via the st_mean semantic instrument" — consequences')
        print('  per prereg (methods §23, memory, ONE desk caveat line); NO engine change from this look.')
    elif dA['pass'] and red and not red['pass']:
        print('  A_v2 FRAGILE = MISS: route CLOSED for the st_mean instrument-basis (both runs recorded verbatim).')
    else:
        print('  A_v2 MISS: "st_mean-A vs de-entangled cooperation: not distinct — route CLOSED for this')
        print('  instrument-basis (any composite over {c1,c2,c3})". A stays criterion-valid / not-discriminant.')
    fp = fingerprints()
    print('\n  fingerprints at look: cv %s… bd %s… vv %s… pn %s… stz %s… speeches %d posts %d | non-finite events: %d'
          % (fp['cv_sha'][:10], fp['bd_sha'][:10], fp['vv_sha'][:10], fp['pn_sha'][:10], fp['stz_sha'][:10],
             fp['speeches'], fp['posts'], NM.NONFIN[0]))
    print('\nAppend the RESULT-A2 section to big5_a_matrix2_prereg.md verbatim (MISSes included) and commit same session.')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--simulate', action='store_true'); ap.add_argument('--look', action='store_true')
    ap.add_argument('--jobs', type=int, default=4)
    args = ap.parse_args()
    print('psych-audit round 6 — A vs de-entangled cooperation, st_mean instrument (prereg: big5_a_matrix2_prereg.md)')
    pre = open(PREREG, encoding='utf-8').read() if os.path.exists(PREREG) else ''
    if not pre: print('!! prereg ABSENT — nothing can run'); return
    locked = 'STATUS: LOCKED' in pre
    taken = '\n## RESULT-A2' in pre       # newline-anchored heading match (@72690c9 lesson)
    fp = fingerprints()
    fp_ok = all(fp[k] == PIN[k] for k in ('cv_sha', 'bd_sha', 'vv_sha', 'pn_sha', 'stz_sha', 'stm_sha'))
    print('  lock: %s | look: %s | artifact fingerprints: %s | corpus (REPORTED, Addendum 1): speeches %d posts %d'
          % (locked, 'TAKEN' if taken else 'not yet taken', 'MATCH' if fp_ok else 'DRIFT', fp['speeches'], fp['posts']))
    if not (args.simulate or args.look): return
    if args.look:
        if not locked: print('!! LOOK REFUSED: prereg not LOCKED'); return
        if taken: print('!! LOOK REFUSED: RESULT-A2 already recorded (one look only)'); return
        if not fp_ok: print('!! LOOK REFUSED (not consumed): input fingerprint drift vs pinned lock values'); return
    run(simulate_only=not args.look, jobs=args.jobs)

if __name__ == '__main__':
    main()
