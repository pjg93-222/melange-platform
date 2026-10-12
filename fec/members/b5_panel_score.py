# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Expert-panel scorer (big5_expert_panel_prereg.md). Two modes:
  (default)   RELIABILITY REPORT — safe anytime: per-trait ICC(2,k), Krippendorff alpha (interval),
              Spearman-Brown k-rater projection, recognition-contamination audit, completion status.
              This does NOT touch the instruments (knowing the anchor is reliable is pre-look by design).
  --look      THE ONE LOOK — instrument x panel 5x5 Campbell-Fiske matrices (Pink + big5_v2) + bars.
              HARD-GATED: refuses unless (a) b5_panel_items.PROVISIONAL_TEXT is False, (b) the prereg
              file contains 'STATUS: LOCKED', (c) >=3 raters each >=90% complete. One look, no re-runs.
  --selftest  synthetic-rater machinery validation (planted structure recovered; null -> no false pass).
Rater files: panel_ratings_<ID>.json (exported from expert_panel_rater.html) in repo root or panel_ratings/."""
import json, glob, re
import numpy as np
sys=_s; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
from members.b5_panel_items import trait_score, PROVISIONAL_TEXT, DOM2TRAIT
TR=['O','C','E','A','ES']
PREREG=B+r'\big5_expert_panel_prereg.md'
# bars — MIRROR of the prereg (the prereg text is authoritative; change there first)
BAR_ICC_DROP=0.60; BAR_ICC_TARGET=0.72; BAR_CONV=0.30; BAR_P=0.05

def icc2k(M):
    """Shrout-Fleiss ICC(2,k): M = targets x raters matrix (no NaNs)."""
    n,k=M.shape
    if n<3 or k<2: return float('nan')
    mr=M.mean(1); mc=M.mean(0); g=M.mean()
    MSR=k*np.sum((mr-g)**2)/(n-1); MSC=n*np.sum((mc-g)**2)/(k-1)
    SSE=np.sum((M-mr[:,None]-mc[None,:]+g)**2); MSE=SSE/((n-1)*(k-1))
    return float((MSR-MSE)/(MSR+(MSC-MSE)/n)) if (MSR+(MSC-MSE)/n)>1e-12 else float('nan')
def kripp_interval(M):
    """Krippendorff alpha, interval metric; M = targets x raters, NaN = missing."""
    vals=[]; units=[]
    for row in M:
        u=row[~np.isnan(row)]
        if len(u)>=2: units.append(u); vals.extend(u.tolist())
    if not units: return float('nan')
    vals=np.array(vals); n=len(vals)
    Do=num=0.0
    for u in units:
        m=len(u); pair=sum((a-b)**2 for i,a in enumerate(u) for b in u[i+1:])
        Do+=pair/(m-1); num+=m
    Do/=max(1e-12,num/2.0)
    De=np.sum((vals[:,None]-vals[None,:])**2)/(n*(n-1))
    return float(1-Do/max(1e-12,De))
def rr(a,b):
    a,b=np.asarray(a,float),np.asarray(b,float)
    ok=~(np.isnan(a)|np.isnan(b)); a,b=a[ok],b[ok]
    if len(a)<4 or a.std()<1e-12 or b.std()<1e-12: return float('nan'),len(a)
    return float(np.corrcoef(a,b)[0,1]),len(a)
def pval(r,n):
    from scipy import stats
    if not np.isfinite(r) or n<4: return float('nan')
    t=r*np.sqrt((n-2)/max(1e-9,1-r*r)); return float(stats.t.sf(t,n-2))

def load_raters():
    files=sorted(set(glob.glob(B+r'\panel_ratings_*.json')+glob.glob(B+r'\panel_ratings\*.json')))
    out={}
    for f in files:
        try:
            d=json.load(open(f,encoding='utf-8')); out[d['rater']]=d
        except Exception as e: print('  !! unreadable %s (%s)'%(_o.path.basename(f),str(e)[:50]))
    return out

def assemble(raters,key):
    """-> traits[trait] = {pid: {rater: score}}, recognized = {(rater,pid)} correctly identified."""
    traits={t:{} for t in TR}; recognized=set()
    for rid,d in raters.items():
        for pid,r in (d.get('ratings') or {}).items():
            if pid not in key: continue
            ts=trait_score(r)
            if ts is None: continue
            true_last=key[pid]['member'].split()[-1].lower()
            if r.get('REC') and true_last in str(r['REC']).lower(): recognized.add((rid,pid))
            for t,v in ts.items(): traits[t].setdefault(pid,{})[rid]=v
    return traits,recognized

def reliability(traits,raters,recognized,key):
    rids=sorted(raters); print('raters: %d (%s)'%(len(rids),', '.join(rids)))
    for rid in rids:
        d=raters[rid]; print('  %-10s %s/%s complete'%(rid,d.get('n_complete','?'),d.get('n_total','?')))
    if recognized:
        print('recognition contamination: %d (rater,packet) pairs correctly identified -> excluded from primary'%len(recognized))
    iv_pids=[p for p in key if key[p]['genre']=='INTERVIEW']
    print('\nper-trait panel reliability (INTERVIEW packets, recognized-excluded):')
    ok=True
    for t in TR:
        rows=[]
        for p in iv_pids:
            d=traits[t].get(p,{})
            row=[d.get(r,np.nan) if (r,p) not in recognized else np.nan for r in rids]
            if sum(np.isfinite(row))>=2: rows.append(row)
        M=np.array(rows,float)
        Mc=M[~np.isnan(M).any(1)] if len(M) else M
        icc=icc2k(Mc) if len(Mc)>=3 else float('nan')
        ka=kripp_interval(M) if len(M) else float('nan')
        stat='OK' if (np.isfinite(icc) and icc>=BAR_ICC_TARGET) else ('acceptable' if (np.isfinite(icc) and icc>=BAR_ICC_DROP) else 'BELOW FLOOR -> trait drops from the A-case')
        if not (np.isfinite(icc) and icc>=BAR_ICC_DROP): ok=False
        print('  %-3s ICC(2,k)=%.3f  Kripp-alpha=%.3f  (n=%d complete-case)  %s'%(t,icc,ka,len(Mc),stat))
    return ok

def consensus(traits,recognized,key,genre='INTERVIEW'):
    """member -> {trait: mean over raters (recognized-excluded)} for one genre."""
    out={}
    for t in TR:
        for p,d in traits[t].items():
            if key.get(p,{}).get('genre')!=genre: continue
            vals=[v for r,v in d.items() if (r,p) not in recognized]
            if len(vals)>=2:
                m=key[p]['member']; out.setdefault(m,{})[t]=float(np.mean(vals))
    return out

def the_look(traits,recognized,key):
    # ---- GATES ----
    if PROVISIONAL_TEXT: print('GATE FAIL: items still PROVISIONAL — verify text, flip flag.'); return
    pre=open(PREREG,encoding='utf-8').read() if _o.path.exists(PREREG) else ''
    if 'STATUS: LOCKED' not in pre: print('GATE FAIL: prereg not LOCKED (add "STATUS: LOCKED" after final review).'); return
    raters=load_raters()
    full=[r for r,d in raters.items() if (d.get('n_complete',0)/max(1,d.get('n_total',1)))>=0.90]
    if len(full)<3: print('GATE FAIL: need >=3 raters at >=90%% completion (have %d).'%len(full)); return
    if 'LOOK TAKEN' in pre: print('GATE FAIL: prereg records LOOK TAKEN — one look only.'); return
    cons=consensus(traits,recognized,key)
    PINK=json.load(open(B+r'\data\members\big5_llm_opus.json',encoding='utf-8'))['traits']
    V2=json.load(open(B+r'\data\members\big5_v2.json',encoding='utf-8'))['traits']
    def pink_t(m,t):
        d=PINK.get(m) or {}; rz=d.get('raw_z')
        return (rz.get(t) if isinstance(rz,dict) else d.get(t))
    for label,getv in (('Pink LLM',pink_t),('big5_v2',lambda m,t:(V2.get(m) or {}).get(t))):
        mem=[m for m in cons if all(t in cons[m] for t in TR) and all(getv(m,t) is not None for t in TR)]
        print('\n===== %s x PANEL (n=%d members) — Campbell-Fiske ====='%(label,len(mem)))
        Mx={}
        for ti in TR:
            for tj in TR:
                Mx[(ti,tj)],_=rr([getv(m,ti) for m in mem],[cons[m][tj] for m in mem])
        print('  inst\\panel '+''.join('%8s'%t for t in TR))
        for ti in TR: print('  %-9s'%ti+''.join('%+8.3f'%Mx[(ti,tj)] for tj in TR))
        npass=0
        for t in TR:
            diag=Mx[(t,t)]; p=pval(diag,len(mem))
            row=all(diag> Mx[(t,c)] for c in TR if c!=t); col=all(diag>Mx[(c,t)] for c in TR if c!=t)
            b=(diag>=BAR_CONV) and (p<BAR_P) and row and col
            npass+=b
            print('  %-3s diag=%+.3f p=%.3f row-dom=%s col-dom=%s -> %s'%(t,diag,p,row,col,'PASS' if b else 'MISS'))
        print('  %s VERDICT: %d/5 traits (A-case needs >=4/5 on the PRIMARY instrument per prereg)'%(label,npass))
    # halo adjudication + human cross-genre
    a_es,n1=rr([cons[m]['A'] for m in cons if 'A' in cons[m] and 'ES' in cons[m]],
               [cons[m]['ES'] for m in cons if 'A' in cons[m] and 'ES' in cons[m]])
    print('\npanel heterotrait A-ES r=%+.3f (n=%d): >=.5 -> the text .65 halo is partly REAL covariance; <=.3 -> method halo'%(a_es,n1))
    cr=consensus(traits,recognized,key,'CREC')
    xn=[m for m in cons if m in cr]
    if len(xn)>=10:
        print('human cross-genre consistency (IV vs CREC, n=%d):'%len(xn))
        for t in TR:
            r,_=rr([cons[m][t] for m in xn if t in cons[m] and t in cr.get(m,{})],
                   [cr[m][t] for m in xn if t in cons[m] and t in cr.get(m,{})])
            print('  %-3s %+.3f'%(t,r))
        print('  (if HUMANS also fail cross-genre, public personas are genre-bound at the construct level)')
    print('\nRecord LOOK TAKEN + results in the prereg now (one look).')

def selftest():
    print('SELFTEST: synthetic raters — planted structure must be recovered; null must not pass.')
    rng=np.random.default_rng(7); n=80; k=4
    true={t:rng.normal(0,1,n) for t in TR}
    key={'p%03d'%i:{'member':'M%03d'%i,'genre':'INTERVIEW'} for i in range(n)}
    def sim(noise):
        traits={t:{} for t in TR}
        for t in TR:
            for i in range(n):
                for r in range(k):
                    traits[t].setdefault('p%03d'%i,{})['R%d'%r]=true[t][i]+rng.normal(0,noise)
        return traits
    for noise,lab in ((0.8,'planted (noise .8)'),(1e6,'null (pure noise)')):
        traits=sim(noise)
        iccs=[]
        for t in TR:
            M=np.array([[traits[t]['p%03d'%i]['R%d'%r] for r in range(k)] for i in range(n)])
            iccs.append(icc2k(M))
        cons={key[p]['member']:{t:float(np.mean(list(traits[t][p].values()))) for t in TR} for p in key}
        diag=np.mean([rr([true[t][i] for i in range(n)],[cons['M%03d'%i][t] for i in range(n)])[0] for t in TR])
        print('  %-20s mean ICC(2,k)=%.3f  mean diag r(true,consensus)=%+.3f'%(lab,np.mean(iccs),diag))
    print('  expect: planted ICC>=.7 & diag>~.7 ; null ICC~0 & diag~0')

if __name__=='__main__':
    if '--selftest' in sys.argv: selftest(); sys.exit(0)
    key=json.load(open(B+r'\data\members\panel_key.json',encoding='utf-8')) if _o.path.exists(B+r'\data\members\panel_key.json') else {}
    if not key: print('no data/members/panel_key.json — run b5_panel_build.py first'); sys.exit(1)
    raters=load_raters()
    if not raters: print('no panel_ratings_*.json found — hand expert_panel_rater.html to raters first'); sys.exit(0)
    traits,recognized=assemble(raters,key)
    reliability(traits,raters,recognized,key)
    if '--look' in sys.argv: the_look(traits,recognized,key)
    else: print('\n(reliability-only report; THE LOOK requires --look + all gates)')
