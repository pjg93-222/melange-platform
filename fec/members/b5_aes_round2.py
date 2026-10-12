# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""A & ES round 2 (big5_aes_round2_prereg.md, locked 2026-07-09). Three independent one-look tests:
  --look-a   dialogic-assent Agreeableness instrument (interview turns; Paul's assent mechanism)
  --look-e1  sex-relative voice ES (features + target residualized on gender; Paul's inflection mechanism)
  --look-e2  NPAT survey-response criterion for text-ES (RKH's own criterion; data on disk)
  --simulate machinery validation per test (planted recovery + permuted-null calibration + confound
             detection) — computes NO real instrument x criterion pairing.
Gates: each look refuses if the prereg already contains its '## RESULT-<X>' heading. No LLM calls.
ONE LOOK each."""
import json, csv, re, sqlite3, argparse
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import moneyball_core as MC
B = MB_ROOT
PREREG = B + r'\big5_aes_round2_prereg.md'
ALPHAS = [1, 3, 10, 30, 100, 300, 1000]; SEEDS = [7, 17, 29, 43, 61]

# ---------- shared machinery (identical protocol to the 3/5 instrument round) ----------
def oof(X, y):
    pr = np.zeros((len(SEEDS), len(y)))
    for i, s in enumerate(SEEDS):
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error'); g.fit(X[tr], y[tr]); pr[i, te] = g.predict(X[te])
    return pr.mean(0)
def rr(a, b): return float(np.corrcoef(np.asarray(a, float), np.asarray(b, float))[0, 1])
def exact_perm_p(pred, y, K=5000, seed=20260709):
    obs = rr(pred, y); rng = np.random.default_rng(seed)
    null = np.array([rr(pred, rng.permutation(y)) for _ in range(K)])
    return obs, float((np.sum(null >= obs) + 1) / (K + 1))
def resid(v, Z):
    Z1 = np.column_stack([np.ones(len(v)), Z]); b, *_ = np.linalg.lstsq(Z1, v, rcond=None); return v - Z1 @ b
def rez(v): sd = v.std(ddof=1); return (v - v.mean()) / (sd if sd > 1e-12 else 1.0)
def partial_test(pred, y, gvec, pvec, seed=20260710):
    Z = np.column_stack([gvec, pvec]); rp = resid(np.asarray(pred, float), Z); ry = resid(np.asarray(y, float), Z)
    return exact_perm_p(rp, ry, seed=seed)

def gender_map():
    g = {}
    with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row.get('bioguide_id'): g[row['bioguide_id']] = row.get('gender', '')
    b2n = MC.bio2name()
    return {b2n[bg]: gg for bg, gg in g.items() if b2n.get(bg) and gg in ('M', 'F')}

def criteria_pack():
    BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
    VV = json.load(open(B + r'\data\members\voteview_consistency.json', encoding='utf-8'))['members']
    P = MC.party()
    return BD, VV, P
def crit_vectors(MEM, BD, VV, P):
    coop = np.array([BD[m]['cooperation_z'] for m in MEM]); dil = np.array([BD[m]['diligence_z'] for m in MEM])
    vis = np.array([BD[m]['visibility_z'] for m in MEM]); bre = np.array([BD[m]['breadth_z'] for m in MEM])
    gmp = np.array([VV[m]['gmp'] for m in MEM]); ext = np.array([VV[m]['extremity'] for m in MEM])
    par = np.array([1.0 if P.get(m) == 'R' else 0.0 for m in MEM])
    rg = resid(gmp, np.column_stack([coop, ext, par]))
    return {'cooperation': coop, 'diligence': dil, 'visibility': vis, 'breadth': bre, 'residGMP': rg}, par

# ---------- TEST A: dialogic-assent features (lexicons FIXED in the prereg, verbatim) ----------
_ap = lambda s: s.replace("’", "'")
UNAMB = re.compile(r"\b(?:exactly|absolutely|agreed|certainly|indeed|yes|yeah|of course)\b")
AMB = re.compile(r"\b(right|sure|true|correct|fair enough|that'?s right|that'?s true|no doubt)\s*[,.!—-]")
PHR = re.compile(r"\b(that makes sense|makes sense to me|i agree|i couldn'?t agree more|could not agree more|"
    r"you'?re right|you are right|you'?re absolutely right|that'?s a fair point|fair point|good point|great point|"
    r"well said|i understand|i hear you|i take your point|point well taken|i see your point|you make a good point|"
    r"that'?s exactly right|i think that'?s right|no question|without question)\b")
CONC = re.compile(r"\b(to be fair|i'?ll grant|granted,|look, i understand|i get it|i get that|you'?re not wrong)\b")
GRAT = re.compile(r"\b(thank you|thanks for having|i appreciate|grateful)\b")
DISA = re.compile(r"\b(i disagree|i don'?t agree|that'?s not true|that'?s wrong|that'?s false|that'?s not right|"
    r"no, no|with all due respect|let me push back|i take issue|that'?s ridiculous|nonsense|"
    r"couldn'?t disagree more|that'?s simply not|that'?s just not)\b")
DEFE = re.compile(r"\b(let me finish|may i finish|i'?m not finished|if i could just finish|hold on|"
    r"wait a (minute|second)|as i was saying|excuse me[,.]? (i|let))\b", re.I)
AFEATS = ['assent_initial', 'assent_phrases', 'concession', 'gratitude', 'disagreement', 'defense',
          'llm_communion', 'llm_agency']

def build_a_features():
    con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
    rows = con.execute("select member, chamber, text from speeches where source='INTERVIEW'").fetchall()
    con.close()
    per = {}   # (member, program) -> counters
    tot = {}   # member -> total turns
    for m, prog, t in rows:
        t = _ap((t or '').strip()); tl = t.lower()
        if not t: continue
        k = (m, prog or 'other'); d = per.setdefault(k, {'turns': 0, 'words': 0, 'ai': 0, 'ph': 0, 'co': 0, 'gr': 0, 'di': 0, 'de': 0})
        w = len(t.split()); d['turns'] += 1; d['words'] += w; tot[m] = tot.get(m, 0) + 1
        head = ' '.join(tl.split()[:4])
        if UNAMB.search(head) or AMB.search(head + ' '): d['ai'] += 1
        d['ph'] += len(PHR.findall(tl)); d['co'] += len(CONC.findall(tl)); d['gr'] += len(GRAT.findall(tl))
        d['di'] += len(DISA.findall(tl)); d['de'] += len(DEFE.findall(tl))
    # per-(member,program) rates on qualifying rows (>=12 turns); z within program (>=8 members); turn-weighted member mean
    byprog = {}
    for (m, prog), d in per.items():
        if d['turns'] < 12: continue
        r = {'assent_initial': d['ai'] / d['turns'],
             'assent_phrases': 1000.0 * d['ph'] / max(1, d['words']),
             'concession': 1000.0 * d['co'] / max(1, d['words']),
             'gratitude': 1000.0 * d['gr'] / max(1, d['words']),
             'disagreement': 1000.0 * d['di'] / max(1, d['words']),
             'defense': 1000.0 * d['de'] / max(1, d['words']), 'turns': d['turns']}
        byprog.setdefault(prog, {})[m] = r
    zsum, zwt = {}, {}
    for prog, mem in byprog.items():
        if len(mem) < 8: continue
        for f in AFEATS[:6]:
            xs = np.array([mem[m][f] for m in mem]); mu = xs.mean(); sd = xs.std(ddof=1) or 1.0
            for m in mem:
                zsum.setdefault(m, {}).setdefault(f, 0.0); zwt.setdefault(m, {}).setdefault(f, 0.0)
                zsum[m][f] += ((mem[m][f] - mu) / sd) * mem[m]['turns']; zwt[m][f] += mem[m]['turns']
    AC = json.load(open(B + r'\data\members\agency_communion.json', encoding='utf-8'))['members']
    cov = {m: d for m, d in AC.items() if d.get('n', 0) >= 15}
    mc = np.mean([d['communion'] for d in cov.values()]); ma = np.mean([d['agency'] for d in cov.values()])
    feats = {}
    for m in zsum:
        if tot.get(m, 0) < 20: continue
        v = [zsum[m][f] / zwt[m][f] if zwt[m].get(f) else 0.0 for f in AFEATS[:6]]
        v += [cov.get(m, {}).get('communion', mc), cov.get(m, {}).get('agency', ma)]
        feats[m] = v
    return feats

def a_matrix(feats, BD, VV, P, gm):
    MEM = [m for m in sorted(feats) if m in BD and m in VV and P.get(m) in ('D', 'R') and m in gm
           and all(BD[m].get(k) is not None for k in ('cooperation_z', 'diligence_z', 'visibility_z', 'breadth_z'))]
    X = np.array([feats[m] for m in MEM], float)
    sd = X.std(0, ddof=1); sd[sd < 1e-12] = 1; X = (X - X.mean(0)) / sd
    return MEM, X

def look_a():
    if '\n## RESULT-A' in open(PREREG, encoding='utf-8').read():   # newline-anchored: prose QUOTES of the heading must not trip the gate
        print('!! LOOK-A REFUSED: RESULT-A already recorded.'); return
    BD, VV, P = criteria_pack(); gm = gender_map()
    feats = build_a_features(); MEM, X = a_matrix(feats, BD, VV, P, gm)
    CR, par = crit_vectors(MEM, BD, VV, P)
    gv = np.array([1.0 if gm[m] == 'F' else 0.0 for m in MEM])
    print('TEST A — THE LOOK | N=%d members (>=20 turns, full criteria) | %d features' % (len(MEM), X.shape[1]))
    pred = oof(X, CR['cooperation'])
    own, p1 = exact_perm_p(pred, CR['cooperation'])
    row = {k: rr(pred, CR[k]) for k in CR}
    dom = all(abs(own) >= abs(row[k]) for k in CR if k != 'cooperation') and own > 0
    rp, p3 = partial_test(pred, CR['cooperation'], gv, par)
    print('  row: ' + ' '.join('%s %+.3f' % (k, row[k]) for k in CR))
    print('  A1 own r=%+.3f exact perm-p=%.4f (bar >=+0.15, p<.05): %s' % (own, p1, 'CLEAR' if own >= 0.15 and p1 < .05 else 'MISS'))
    print('  A2 diagonal-dominant: %s' % ('CLEAR' if dom else 'MISS'))
    print('  A3 partial r(|gender,party)=%+.3f perm-p=%.4f (bar >=+0.10, p<.05): %s' % (rp, p3, 'CLEAR' if rp >= 0.10 and p3 < .05 else 'MISS'))
    ok = own >= 0.15 and p1 < .05 and dom and rp >= 0.10 and p3 < .05
    # pre-declared non-gating secondaries
    try:
        BP = json.load(open(B + r'\behavior_panel.json')); BPm = BP.get('members', BP)
        ms = [i for i, m in enumerate(MEM) if isinstance(BPm.get(m), dict) and BPm[m].get('bipartisanship') is not None]
        print('  [secondary] r(pred, bipartisan-RATIO) = %+.3f (n=%d)' % (rr(pred[ms], np.array([BPm[MEM[i]]['bipartisanship'] for i in ms])), len(ms)))
    except Exception as e: print('  [secondary] bipartisan ratio unavailable:', str(e)[:60])
    print('  [secondary] per-feature r vs coop | dilig:')
    for j, f in enumerate(AFEATS):
        print('     %-16s %+.3f | %+.3f' % (f, rr(X[:, j], CR['cooperation']), rr(X[:, j], CR['diligence'])))
    print('VERDICT TEST A: %s' % ('PASS' if ok else 'MISS'))

# ---------- TEST E1: sex-relative voice ES ----------
def e1_data():
    AU = json.load(open(B + r'\data\members\b5_audio_cache.json', encoding='utf-8'))
    V2 = json.load(open(B + r'\data\members\big5_v2.json', encoding='utf-8'))['traits']
    gm = gender_map(); P = MC.party()
    mem = [m for m in AU['members'] if V2.get(m) and V2[m].get('ES') is not None and V2[m].get('A') is not None and m in gm]
    X = np.array([AU['z'][m] for m in mem], float)
    sd = X.std(0, ddof=1); sd[sd < 1e-12] = 1; X = (X - X.mean(0)) / sd
    gv = np.array([1.0 if gm[m] == 'F' else 0.0 for m in mem])
    pv = np.array([1.0 if P.get(m) == 'R' else 0.0 for m in mem])
    Xr = np.column_stack([rez(resid(X[:, j], gv[:, None])) for j in range(X.shape[1])])
    Z = np.column_stack([gv, pv])
    yES = rez(resid(np.array([V2[m]['ES'] for m in mem]), Z)); yA = rez(resid(np.array([V2[m]['A'] for m in mem]), Z))
    return mem, Xr, yES, yA, gv
def look_e1():
    if '\n## RESULT-E1' in open(PREREG, encoding='utf-8').read():   # newline-anchored: prose QUOTES of the heading must not trip the gate
        print('!! LOOK-E1 REFUSED: RESULT-E1 already recorded.'); return
    mem, Xr, yES, yA, gv = e1_data()
    print('TEST E1 — THE LOOK | N=%d (F=%d M=%d) | 88 sex-residualized eGeMAPS -> sex/party-residualized v2-ES' % (len(mem), int(gv.sum()), int(len(mem) - gv.sum())))
    pred = oof(Xr, yES)
    r1, p1 = exact_perm_p(pred, yES); rA = rr(pred, yA)
    print('  E1a held-out r=%+.3f (bar >=+0.15): %s' % (r1, 'CLEAR' if r1 >= .15 else 'MISS'))
    print('  E1b exact perm-p=%.4f (bar <.01): %s' % (p1, 'CLEAR' if p1 < .01 else 'MISS'))
    print('  E1c discriminant r_ES=%+.3f > r_A=%+.3f: %s' % (r1, rA, 'CLEAR' if r1 > rA else 'MISS'))
    print('VERDICT TEST E1: %s' % ('PASS' if (r1 >= .15 and p1 < .01 and r1 > rA) else 'MISS — voice-ES route CLOSED PERMANENTLY (pre-declared)'))

# ---------- TEST E2: NPAT respond criterion ----------
def e2_data():
    i2b = {}
    with open(B + r'\votes\HS119_members.csv', encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            ic = (row.get('icpsr') or '').strip(); bg = (row.get('bioguide_id') or '').strip()
            if ic and bg: i2b[ic] = bg
    b2n = MC.bio2name()
    resp = {}
    with open(B + r'\fec\liwc\rkh\HouseNPATwithPers.tab', encoding='utf-8', errors='replace') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            try: v = float((r.get('votesmart_respond') or '').strip())
            except ValueError: continue
            if v in (0.0, 1.0):
                m = b2n.get(i2b.get((r.get('icpsr') or '').strip().split('.')[0], ''))
                if m: resp[m] = int(v)
    added = 0
    try:
        with open(B + r'\fec\liwc\rkh\NPATrespICPSR.tab', encoding='utf-8', errors='replace') as f:
            for r in csv.DictReader(f, delimiter='\t'):
                v = (r.get('votesmart_respond') or '').strip()
                if v in ('0', '1', '0.0', '1.0'):
                    m = b2n.get(i2b.get((r.get('icpsr') or '').strip().split('.')[0], ''))
                    if m and m not in resp: resp[m] = int(float(v)); added += 1
    except FileNotFoundError: pass
    V2 = json.load(open(B + r'\data\members\big5_v2.json', encoding='utf-8'))['traits']
    PK = json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8'))['traits']
    gm = gender_map(); P = MC.party()
    mem = [m for m in sorted(resp) if V2.get(m) and all(V2[m].get(t) is not None for t in 'OCEA') and V2[m].get('ES') is not None and m in gm]
    return mem, resp, V2, PK, gm, P, added
def look_e2():
    if '\n## RESULT-E2' in open(PREREG, encoding='utf-8').read():   # newline-anchored: prose QUOTES of the heading must not trip the gate
        print('!! LOOK-E2 REFUSED: RESULT-E2 already recorded.'); return
    mem, resp, V2, PK, gm, P, added = e2_data()
    y = np.array([resp[m] for m in mem], float)
    gv = np.array([1.0 if gm[m] == 'F' else 0.0 for m in mem]); pv = np.array([1.0 if P.get(m) == 'R' else 0.0 for m in mem])
    print('TEST E2 — THE LOOK | N=%d current members w/ NPAT respond (responders=%d; +%d from sidecar)' % (len(mem), int(y.sum()), added))
    es = np.array([V2[m]['ES'] for m in mem])
    r1, p1 = exact_perm_p(es, y)
    others = {t: rr(np.array([V2[m][t] for m in mem]), y) for t in 'OCEA'}
    spec = all(abs(r1) > abs(v) for v in others.values())
    rp, p3 = partial_test(es, y, gv, pv)
    print('  E2a r(v2-ES, respond)=%+.3f exact perm-p=%.4f (bar >=+0.15 [+ sign], p<.05): %s' % (r1, p1, 'CLEAR' if r1 >= .15 and p1 < .05 else 'MISS'))
    print('  E2b ES-specificity: ' + ' '.join('%s %+.3f' % (t, others[t]) for t in 'OCEA') + ' -> %s' % ('CLEAR' if spec else 'MISS'))
    print('  E2c partial r(|gender,party)=%+.3f perm-p=%.4f (bar >=+0.10, p<.05): %s' % (rp, p3, 'CLEAR' if rp >= .10 and p3 < .05 else 'MISS'))
    pe = [PK[m]['raw_z']['ES'] if isinstance(PK.get(m, {}).get('raw_z'), dict) else PK.get(m, {}).get('ES') for m in mem]
    ok2 = [i for i, v in enumerate(pe) if v is not None]
    if len(ok2) > 30:
        r2, p2 = exact_perm_p(np.array([pe[i] for i in ok2]), y[ok2])
        print('  [secondary] r(Pink-ES, respond)=%+.3f perm-p=%.4f (n=%d)' % (r2, p2, len(ok2)))
    ok = r1 >= .15 and p1 < .05 and spec and rp >= .10 and p3 < .05
    print('VERDICT TEST E2: %s' % ('PASS' if ok else 'MISS — NPAT route closed for the current roster (powered path = fresh Vote Smart pull, user-gated)'))

# ---------- simulate ----------
def simulate():
    rng = np.random.default_rng(11)
    print('----- SIM TEST A machinery (features real; targets synthetic/permuted — no real pairing) -----')
    BD, VV, P = criteria_pack(); gm = gender_map()
    feats = build_a_features(); MEM, X = a_matrix(feats, BD, VV, P, gm)
    CR, par = crit_vectors(MEM, BD, VV, P)
    gv = np.array([1.0 if gm[m] == 'F' else 0.0 for m in MEM])
    print('  built features: N=%d x %d' % X.shape)
    w = rng.normal(0, 1, X.shape[1]); yp = rez(X @ w) + rng.normal(0, 1.6, len(MEM))
    pr = oof(X, yp); o, p = exact_perm_p(pr, yp); rp, pp = partial_test(pr, yp, gv, par)
    print('  planted: r=%+.3f p=%.4f partial=%+.3f p=%.4f -> %s' % (o, p, rp, pp, 'OK (recovered+survives partial)' if o > .15 and p < .05 and rp > .10 else 'FAIL'))
    yn = rng.permutation(CR['cooperation'])
    prn = oof(X, yn); on, pn = exact_perm_p(prn, yn)
    print('  null (permuted coop): r=%+.3f p=%.4f -> %s' % (on, pn, 'OK (no false pass)' if not (on >= .15 and pn < .05) else 'FAIL'))
    ge = 1.2 * gv   # confounder = pure function of gender (mirrors the validated b5_es_robustness sim)
    prc = rez(ge + rng.normal(0, 1, len(MEM))); yc = rez(ge + rng.normal(0, 1, len(MEM)))
    rc, pc = partial_test(prc, yc, gv, par)
    print('  confound-only: raw r=%+.3f partial=%+.3f p=%.4f -> %s' % (rr(prc, yc), rc, pc, 'OK (partial kills confound)' if not (rc >= .10 and pc < .05) else 'FAIL'))
    print('----- SIM TEST E1 machinery -----')
    mem, Xr, yES, yA, gv1 = e1_data()
    for j in range(Xr.shape[1]):
        rj = rr(Xr[:, j], gv1)
        if abs(rj) > 1e-6 and j == 0: pass
    mx = max(abs(rr(Xr[:, j], gv1)) for j in range(Xr.shape[1]))
    print('  residualization check: max |r(feature, gender)| = %.6f (must be ~0)' % mx)
    w = rng.normal(0, 1, Xr.shape[1]); yp = rez(Xr @ w) + rng.normal(0, 2.0, len(mem))
    pr = oof(Xr, yp); o, p = exact_perm_p(pr, yp)
    yn = rng.permutation(yES); prn = oof(Xr, yn); on, pn = exact_perm_p(prn, yn)
    print('  planted r=%+.3f p=%.4f | null r=%+.3f p=%.4f -> %s' % (o, p, on, pn, 'OK' if (o > .15 and p < .01 and not (on >= .15 and pn < .01)) else 'FAIL'))
    print('----- SIM TEST E2 machinery -----')
    mem2, resp, V2, PK, gm2, P2, added = e2_data()
    y = np.array([resp[m] for m in mem2], float)
    print('  join: N=%d responders=%d (+%d sidecar)' % (len(mem2), int(y.sum()), added))
    s = rng.normal(0, 1, len(mem2)); yb = (rez(s) + rng.normal(0, 1.5, len(mem2)) > 0.6).astype(float)
    o, p = exact_perm_p(s, yb)
    yperm = rng.permutation(y); on, pn = exact_perm_p(rng.normal(0, 1, len(mem2)), yperm)
    print('  planted-binary r=%+.3f p=%.4f | null r=%+.3f p=%.4f -> %s' % (o, p, on, pn, 'OK' if (p < .05 and pn > .05) else 'CHECK'))

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--simulate', action='store_true'); ap.add_argument('--look-a', action='store_true')
    ap.add_argument('--look-e1', action='store_true'); ap.add_argument('--look-e2', action='store_true')
    a = ap.parse_args()
    if a.simulate: simulate()
    elif a.look_a: look_a()
    elif a.look_e1: look_e1()
    elif a.look_e2: look_e2()
    else:
        pre = open(PREREG, encoding='utf-8').read() if _o.path.exists(PREREG) else ''
        for tag in ('A', 'E1', 'E2'):
            print('TEST %-3s look: %s' % (tag, 'TAKEN' if ('\n## RESULT-%s' % tag) in pre else 'not yet taken'))
