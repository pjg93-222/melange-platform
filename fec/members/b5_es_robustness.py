# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""ES cross-modal gender/party robustness (big5_es_robustness_prereg.md, locked 2026-07-09).
Re-derives the recorded ES cross-modal pass's out-of-fold prediction vector (b5_es_crossmodal.py
protocol verbatim, deterministic), then the Frisch-Waugh partial vs [gender, party] with a 5000-perm
null. --simulate validates the machinery on synthetic confound-only / true-signal cases (no look
consumed). --look = THE ONE LOOK (gated: refuses if the prereg already has a RESULT). ONE LOOK."""
import json, csv, argparse
import numpy as np
sys=_s; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
import moneyball_core as MC
B=MB_ROOT
PREREG=B+r'\big5_es_robustness_prereg.md'
ALPHAS=[1,3,10,30,100,300,1000]; SEEDS=[7,17,29,43,61]

def load():
    AU=json.load(open(B+r'\data\members\b5_audio_cache.json',encoding='utf-8')); MEM=AU['members']
    Z={m:AU['z'][m] for m in MEM}
    V2=json.load(open(B+r'\data\members\big5_v2.json',encoding='utf-8'))['traits']
    mem=[m for m in MEM if V2.get(m) and V2[m].get('ES') is not None and V2[m].get('A') is not None]
    X=np.array([Z[m] for m in mem],float)
    X=(X-X.mean(0))/np.where(X.std(0,ddof=1)<1e-12,1.0,X.std(0,ddof=1))
    yES=np.array([V2[m]['ES'] for m in mem])
    # gender: legislators.csv bioguide_id -> gender, via the kernel bioguide map
    g_by_bio={}
    with open(B+r'\fec\legislators.csv',encoding='utf-8') as f:
        rd=csv.DictReader(f)
        for row in rd:
            if row.get('bioguide_id'): g_by_bio[row['bioguide_id']]=row.get('gender','')
    n2b={m['member']:m['bioguide'] for m in json.load(open(B+r'\data\engine\congestion.json')) if m.get('bioguide')}
    P=MC.party()
    gen=[g_by_bio.get(n2b.get(m,''),'') for m in mem]
    keep=[i for i,g in enumerate(gen) if g in ('M','F')]
    dropped=len(mem)-len(keep)
    mem=[mem[i] for i in keep]; X=X[keep]; yES=yES[keep]
    gvec=np.array([1.0 if gen[i]=='F' else 0.0 for i in keep])
    pvec=np.array([1.0 if P.get(m)=='R' else 0.0 for m in mem])
    return mem,X,yES,gvec,pvec,dropped

def oof(X,y):
    pr=np.zeros((len(SEEDS),len(y)))
    for i,s in enumerate(SEEDS):
        for tr,te in KFold(5,shuffle=True,random_state=s).split(X):
            g=GridSearchCV(Ridge(),{'alpha':ALPHAS},cv=3,scoring='neg_mean_squared_error'); g.fit(X[tr],y[tr]); pr[i,te]=g.predict(X[te])
    return pr.mean(0)

def rr(a,b): return float(np.corrcoef(a,b)[0,1])

def resid(v,Z):
    Z1=np.column_stack([np.ones(len(v)),Z]); b,*_=np.linalg.lstsq(Z1,v,rcond=None); return v-Z1@b

def partial_test(pred,y,gvec,pvec,seed=20260710,K=5000):
    Z=np.column_stack([gvec,pvec])
    rp=resid(pred,Z); ry=resid(y,Z)
    r_part=rr(rp,ry)
    rng=np.random.default_rng(seed)
    null=np.array([rr(rp,rng.permutation(ry)) for _ in range(K)])
    p=float((np.sum(null>=r_part)+1)/(K+1))
    return r_part,p

def verdict(r_part,p):
    if r_part>=0.10 and p<0.05: return 'ROBUST'
    if r_part>0 and p<0.05: return 'PARTIAL'
    return 'CONFOUNDED'

def report(pred,y,gvec,pvec,label):
    raw=rr(pred,y)
    r_part,p=partial_test(pred,y,gvec,pvec)
    m=gvec<0.5; f=gvec>=0.5
    rM=rr(pred[m],y[m]) if m.sum()>10 else float('nan')
    rF=rr(pred[f],y[f]) if f.sum()>10 else float('nan')
    print('[%s] raw r=%+.3f | partial r(|gender,party)=%+.3f perm-p=%.4f | within-gender: M(n=%d) %+.3f  F(n=%d) %+.3f -> %s'
          %(label,raw,r_part,p,m.sum(),rM,f.sum(),rF,verdict(r_part,p)))
    return raw,r_part,p,verdict(r_part,p)

def simulate(nn=395):
    print('----- SIMULATE (machinery validation on synthetic cases; no real pairing touched) -----')
    rng=np.random.default_rng(5)
    gv=(rng.random(nn)<0.29).astype(float); pv=(rng.random(nn)<0.5).astype(float)
    # (a) confound-only: pred and y both driven by gender alone -> raw r large, partial ~0 -> CONFOUNDED
    ge=1.2*gv
    predA=ge+rng.normal(0,1,nn); yA=ge+rng.normal(0,1,nn)
    _,_,_,vA=report(predA,yA,gv,pv,'SIM confound-only (expect CONFOUNDED)')
    # (b) true signal + additive gender noise on both -> partial survives -> ROBUST
    s=rng.normal(0,1,nn)
    predB=0.55*s+0.8*gv+rng.normal(0,1,nn); yB=0.55*s+0.8*gv+rng.normal(0,1,nn)
    _,_,_,vB=report(predB,yB,gv,pv,'SIM true-signal+gender (expect ROBUST)')
    ok=(vA=='CONFOUNDED') and (vB=='ROBUST')
    print('SIM VERDICT: %s' % ('machinery VALIDATED' if ok else 'FAIL — do not take the look'))
    return ok

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--simulate',action='store_true'); ap.add_argument('--look',action='store_true')
    a=ap.parse_args()
    if a.simulate:
        simulate()
    elif a.look:
        pre=open(PREREG,encoding='utf-8').read()
        if '## RESULT' in pre:   # heading match — the prereg PROSE mentions the word RESULT, which false-tripped the plain substring gate
            print('!! LOOK REFUSED: prereg already contains a RESULT section (one look only).'); sys.exit(1)
        mem,X,yES,gvec,pvec,dropped=load()
        print('ES robustness — THE ONE LOOK | N=%d (dropped %d w/o gender) | F=%d M=%d'%(len(mem),dropped,int(gvec.sum()),int((1-gvec).sum())))
        pred=oof(X,yES)
        report(pred,yES,gvec,pvec,'LOOK')
    else:
        print('run --simulate first; then --look (one look, gated on the prereg having no RESULT).')
