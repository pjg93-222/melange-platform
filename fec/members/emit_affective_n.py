# -*- coding: utf-8 -*-
"""Regenerate affective_n.json — the engine N-slot input (round-7 promotion 2026-07-11, gate 5/5).
Combines the Pennebaker LIWC base (transparent, deterministic, from the interview corpus) with the LLM
affective facets (scratchpad/interview_n_affective.json — Haiku pessimism/anxiety/depression/volatility,
policy-stripped), averaged and party-controlled. reactivity_z retired as Extraversion-contaminated.
LIWC is deterministic; the LLM labels are the one non-deterministic input (regenerate via
interview_llm_n_prep.py + the interview-n-affective-facets workflow if absent)."""
import os, sys, json, re, collections, sqlite3, csv, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT as B
from moneyball_core import dump_atomic
AFFECTIVE_N_INPUT=os.environ.get('AFFECTIVE_N_INPUT',os.path.join(B,'source_data','interview_n_affective.json'))
FPS = re.compile(r'\b(i|me|my|mine|myself)\b', re.I)
NEG = re.compile(r'\b(worri\w+|afraid|anxi\w+|scared|nervous|fear\w*|dread|panic|hopeless|helpless|overwhelm\w+|exhaust\w+|struggl\w+|suffer\w+|sad\w*|depress\w+|miserable|upset|frustrat\w+|hurt|alone|lonely|devastat\w+|painful)\b', re.I)
POS = re.compile(r'\b(happy|glad|great|wonderful|excited|hopeful|optimis\w+|proud|grateful|thrilled|fantastic|excellent|enjoy\w+|confident|encourag\w+|thank\w+|appreciat\w+|fortunate)\b', re.I)
WORD = re.compile(r'\b\w+\b')

def z(x):
    x = np.array(x, float); s = np.nanstd(x); return (x - np.nanmean(x)) / (s or 1)

def run():
    llmp = AFFECTIVE_N_INPUT
    if not os.path.exists(llmp):
        raise FileNotFoundError('required affective-facet input missing: %s (set AFFECTIVE_N_INPUT)'%llmp)
    S = json.load(open(llmp, encoding='utf-8'))
    con = sqlite3.connect(B + r'\social_media.db', timeout=60); con.execute('PRAGMA query_only=1')
    ag = collections.defaultdict(lambda: [0, 0, 0, 0])
    for m, t in con.execute("SELECT member, text FROM speeches WHERE source='INTERVIEW'"):
        if not t: continue
        w = len(WORD.findall(t))
        if w < 20: continue
        a = ag[m]; a[0] += len(FPS.findall(t)); a[1] += len(NEG.findall(t)); a[2] += len(POS.findall(t)); a[3] += w
    con.close()
    lm = [m for m in ag if ag[m][3] >= 800]
    fps = z([ag[m][0] / ag[m][3] for m in lm]); neg = z([ag[m][1] / ag[m][3] for m in lm]); pos = z([ag[m][2] / ag[m][3] for m in lm])
    liwc = {m: float(fps[i] + neg[i] - pos[i]) for i, m in enumerate(lm)}
    # round-7b (2026-07-11 airtight review): MEASURE = E-residualized LLM affective-N, party-controlled.
    # The context-aware LLM is the accurate instrument; E-residualization removes its E-contamination
    # (rather than the crude LIWC doing it, which dragged in false positives like Jack Bergman #1->#412).
    # LIWC stays the independent convergent-validity ANCHOR (r=+.24, gate G3), no longer a component.
    # SB .742->.911, face validity fixed, E −.02, party .00, C −.15. liwc computed above for the gate.
    A5 = json.load(open(B + r'\data\members\big5_v3_hybrid.json'))['traits']
    llm = {m: S[m]['pessimism'] + S[m]['anxiety'] + S[m]['depression'] + S[m]['volatility'] for m in S}
    Cj = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}; bio = {m: Cj[m].get('bioguide') for m in Cj}
    PP = {}
    for r in csv.DictReader(open(B + r'\fec\legislators.csv', encoding='utf-8')):
        if r.get('bioguide_id'): PP[r['bioguide_id']] = (r.get('party') or '')[:1]
    pty = {m: PP.get(bio.get(m), '?') for m in Cj}
    mem = [m for m in S if A5.get(m, {}).get('E') is not None]
    def resid(y, x):
        X = np.column_stack([np.ones(len(y)), x]); return y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
    la = np.array([llm[m] for m in mem], float); Ea = np.array([A5[m]['E'] for m in mem], float)
    Rp = np.array([1.0 if pty.get(m) == 'R' else 0.0 for m in mem])
    combc = z(resid(resid(la, Ea), Rp))   # E-residualized LLM affective-N, then party-controlled
    out = {m: round(float(combc[i]), 3) for i, m in enumerate(mem)}
    input_sha=hashlib.sha256(open(llmp,'rb').read()).hexdigest()
    dump_atomic({'members': out, 'n': len(out), 'basis': 'affective-content N: E-residualized LLM affective facets (pess/anx/dep/vol), party-controlled, z-scored; LIWC = convergent-validity anchor',
               'promotion': 'round-7b 2026-07-11 gate 5/5 (airtight-review refinement); retired E-contaminated reactivity_z',
               'inputs_sha256':{os.path.basename(llmp):input_sha}},B+r'\data\money\affective_n.json',indent=0)
    print('affective_n.json: %d members' % len(out))
    o = sorted(mem, key=lambda m: -out[m])
    print('highest N:', ', '.join('%s(%s)' % (m, pty.get(m)) for m in o[:6]))

if __name__ == '__main__':
    run()
