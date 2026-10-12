# -*- coding: utf-8 -*-
"""PRE-REGISTERED affective-N promotion gate (locked 2026-07-11, committed BEFORE the run -- anti-basis-
shopping). Decides whether the affective-content N measure (Pennebaker LIWC base: +first-person-singular
+neg-emotion -pos-emotion; augmented by the LLM affective facets pessimism/anxiety/depression/volatility,
policy-stripped; averaged + party-controlled -> affective_n.json) may REPLACE reactivity_z in the engine
N-slot (RECEPT_N='affective', mirroring the RECEPT_COOP=v2 contract).

RATIONALE: reactivity_z is robustly Extraversion-contaminated (r=+0.185 with big5-E on 272 members;
NEGATIVE with N) -- it measures conversational engagement, not Neuroticism. Affective-content escapes this
(Pennebaker 2003: neg-emotion words track N, are E-distinct). No gold-standard N criterion exists, so the
gate is construct integrity + a FRESH reliability test, NOT convergence with a validated criterion.

PROMOTION requires ALL FIVE (thresholds fixed in advance):
  G1 E-DISTINCT   |r(affective_N, big5-E)| < 0.10                (was seen ~0.04 exploratory; must hold)
  G2 PARTY-CLEAN  |r(affective_N, party-R)| < 0.05 after control  (was ~0.00)
  G3 CONVERGENT   r(LIWC-N, LLM-N) >= 0.15 and n>=200            (two independent affective methods agree; ~0.24)
  G4 RELIABILITY  *** FRESH/UNSEEN *** LIWC-N split-half (odd vs even interview turns) Spearman-Brown >= 0.50
                  (beats reactivity_z's own SB .43; this test's result is NOT known at commit time)
  G5 BEATS-INCUMBENT  |r(affective_N, big5-E)| < |r(reactivity_z, big5-E)|  (strictly less E-contaminated)

ALL pass -> promote (RECEPT_N='affective', re-verify: ranks stable, receptivity changes intentionally,
audit green). ANY fail -> keep reactivity_z ('behavioral'), report honestly, do not force."""
import os, sys, json, re, collections, sqlite3, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from paths import ROOT
B = ROOT
AFF_INPUT = os.environ.get('AFFECTIVE_N_INPUT',os.path.join(B,'source_data','interview_n_affective.json'))
FPS = re.compile(r'\b(i|me|my|mine|myself)\b', re.I)
NEG = re.compile(r'\b(worri\w+|afraid|anxi\w+|scared|nervous|fear\w*|dread|panic|hopeless|helpless|overwhelm\w+|exhaust\w+|struggl\w+|suffer\w+|sad\w*|depress\w+|miserable|upset|frustrat\w+|hurt|alone|lonely|devastat\w+|painful)\b', re.I)
POS = re.compile(r'\b(happy|glad|great|wonderful|excited|hopeful|optimis\w+|proud|grateful|thrilled|fantastic|excellent|enjoy\w+|confident|encourag\w+|thank\w+|appreciat\w+|fortunate)\b', re.I)
WORD = re.compile(r'\b\w+\b')

def z(x):
    x = np.array(x, float); s = np.nanstd(x); return (x - np.nanmean(x)) / (s or 1)
def corr(a, b):
    a = np.array(a, float); b = np.array(b, float); k = ~(np.isnan(a) | np.isnan(b)); return float(np.corrcoef(a[k], b[k])[0, 1]) if k.sum() > 5 else float('nan')

def liwc_counts(split=None):
    """per-member [fps,neg,pos,words]; split=0 uses odd turns, 1 even turns, None all."""
    con = sqlite3.connect(B + r'\social_media.db', timeout=60); con.execute('PRAGMA query_only=1')
    ag = collections.defaultdict(lambda: [0, 0, 0, 0]); cnt = collections.Counter()
    for m, t in con.execute("SELECT member, text FROM speeches WHERE source='INTERVIEW' ORDER BY rowid"):
        if not t: continue
        w = len(WORD.findall(t))
        if w < 20: continue
        if split is not None:
            if cnt[m] % 2 != split: cnt[m] += 1; continue
            cnt[m] += 1
        a = ag[m]; a[0] += len(FPS.findall(t)); a[1] += len(NEG.findall(t)); a[2] += len(POS.findall(t)); a[3] += w
    con.close(); return ag

def liwc_n(ag, floor=400):
    lm = [m for m in ag if ag[m][3] >= floor]
    fps = z([ag[m][0] / ag[m][3] for m in lm]); neg = z([ag[m][1] / ag[m][3] for m in lm]); pos = z([ag[m][2] / ag[m][3] for m in lm])
    return {m: float(fps[i] + neg[i] - pos[i]) for i, m in enumerate(lm)}

def main():
    A5 = json.load(open(B + r'\data\members\big5_v3_hybrid.json'))['traits']
    BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
    if not os.path.isfile(AFF_INPUT):
        raise FileNotFoundError('required affective-N criterion input missing: '+AFF_INPUT)
    S = json.load(open(AFF_INPUT,encoding='utf-8'))
    AN = json.load(open(B + r'\data\money\affective_n.json'))['members']   # combined party-controlled affective-N
    Cj = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}; bio = {m: Cj[m].get('bioguide') for m in Cj}
    PP = {}
    for r in csv.DictReader(open(B + r'\fec\legislators.csv', encoding='utf-8')):
        if r.get('bioguide_id'): PP[r['bioguide_id']] = (r.get('party') or '')[:1]
    pty = {m: PP.get(bio.get(m), '?') for m in Cj}
    liwc = liwc_n(liwc_counts())
    llm = {m: S[m]['pessimism'] + S[m]['anxiety'] + S[m]['depression'] + S[m]['volatility'] for m in S}
    mem = list(AN.keys())
    an = [AN[m] for m in mem]; E = [A5.get(m, {}).get('E', np.nan) for m in mem]
    Rp = [1.0 if pty.get(m) == 'R' else 0.0 for m in mem]
    print('=== AFFECTIVE-N PROMOTION GATE (n=%d) ===' % len(mem))
    res = {}
    g1 = abs(corr(an, E)); res['G1'] = g1 < 0.10; print('  G1 E-distinct |r(affN,E)|=%.3f (<0.10) -> %s' % (g1, 'PASS' if res['G1'] else 'FAIL'))
    g2 = abs(corr(an, Rp)); res['G2'] = g2 < 0.05; print('  G2 party-clean |r(affN,party)|=%.3f (<0.05) -> %s' % (g2, 'PASS' if res['G2'] else 'FAIL'))
    cm = [m for m in mem if m in liwc and m in llm]
    g3 = corr([liwc[m] for m in cm], [llm[m] for m in cm]); res['G3'] = g3 >= 0.15 and len(cm) >= 200
    print('  G3 convergent r(LIWC,LLM)=%.3f n=%d (>=0.15) -> %s' % (g3, len(cm), 'PASS' if res['G3'] else 'FAIL'))
    # G4 FRESH: LIWC split-half
    a0 = liwc_n(liwc_counts(0)); a1 = liwc_n(liwc_counts(1))
    sh = [m for m in a0 if m in a1]
    rhh = corr([a0[m] for m in sh], [a1[m] for m in sh]); sb = 2 * rhh / (1 + rhh) if rhh > -1 else float('nan')
    res['G4'] = sb >= 0.50; print('  G4 *FRESH* LIWC split-half SB=%.3f n=%d (>=0.50) -> %s' % (sb, len(sh), 'PASS' if res['G4'] else 'FAIL'))
    rz = [BD[m].get('reactivity_z', np.nan) if isinstance(BD.get(m), dict) else np.nan for m in mem]
    g5 = abs(corr(an, E)) < abs(corr(rz, E)); res['G5'] = g5
    print('  G5 beats-incumbent affN|E|=%.3f < reactivity_z|E|=%.3f -> %s' % (abs(corr(an, E)), abs(corr(rz, E)), 'PASS' if res['G5'] else 'FAIL'))
    npass = sum(res.values())
    print('\n  === VERDICT: %d/5 -> %s ===' % (npass, 'PROMOTE (RECEPT_N=affective)' if npass == 5 else 'DO NOT PROMOTE (keep reactivity_z)'))
    return res

if __name__ == '__main__':
    main()
