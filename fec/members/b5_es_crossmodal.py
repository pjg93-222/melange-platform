# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""ES cross-modal held-out re-test (big5_es_crossmodal_prereg.md @locked 2026-07-09). Ridge nested-CV
predicts big5_v2-ES from ALL 88 clean-audio eGeMAPS functionals, out-of-fold; 5000-label-permutation
null; discriminant vs big5_v2-A. NO analyst sign choice -> held-out CV cannot be sign-hacked. ONE LOOK."""
import json
import numpy as np
sys=_s; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
from scipy import stats
B=MB_ROOT
AU=json.load(open(B+r'\data\members\b5_audio_cache.json',encoding='utf-8')); MEM=AU['members']; FN=AU['feature_names']
Z={m:AU['z'][m] for m in MEM}
V2=json.load(open(B+r'\data\members\big5_v2.json',encoding='utf-8'))['traits']
PINK=json.load(open(B+r'\data\members\big5_llm_opus.json',encoding='utf-8'))['traits']
def pinkES(m):
    t=PINK.get(m) or {}; rz=t.get('raw_z')
    return (rz.get('ES') if isinstance(rz,dict) else t.get('ES'))
mem=[m for m in MEM if V2.get(m) and V2[m].get('ES') is not None and V2[m].get('A') is not None]
X=np.array([Z[m] for m in mem],float)
X=(X-X.mean(0))/np.where(X.std(0,ddof=1)<1e-12,1.0,X.std(0,ddof=1))
yES=np.array([V2[m]['ES'] for m in mem]); yA=np.array([V2[m]['A'] for m in mem])
ALPHAS=[1,3,10,30,100,300,1000]; SEEDS=[7,17,29,43,61]
def oof(y):
    pr=np.zeros((len(SEEDS),len(y)))
    for i,s in enumerate(SEEDS):
        for tr,te in KFold(5,shuffle=True,random_state=s).split(X):
            g=GridSearchCV(Ridge(),{'alpha':ALPHAS},cv=3,scoring='neg_mean_squared_error'); g.fit(X[tr],y[tr]); pr[i,te]=g.predict(X[te])
    return pr.mean(0)
def rr(a,b): return float(np.corrcoef(a,b)[0,1])
print('ES CROSS-MODAL HELD-OUT RE-TEST | N=%d | 88 eGeMAPS -> big5_v2-ES (ridge nested CV; analyst chooses NO signs)'%len(mem))
predES=oof(yES)
rES=rr(predES,yES); rA=rr(predES,yA)
# 5000 label permutations on the held-out prediction vector (one-sided, r>0)
rng=np.random.default_rng(20260709); K=5000
null=np.array([rr(predES, rng.permutation(yES)) for _ in range(K)])
perm_p=float((np.sum(null>=rES)+1)/(K+1))
# parametric cross-check
n=len(yES); t=rES*np.sqrt((n-2)/max(1e-9,1-rES*rES)); param_p=float(stats.t.sf(t,n-2))
# secondary: Pink-ES (compare to contaminated held-out +0.298)
pe=[(i,pinkES(m)) for i,m in enumerate(mem) if pinkES(m) is not None]
rP=None
if len(pe)>50:
    yPfull=np.array([pinkES(m) if pinkES(m) is not None else np.nan for m in mem])
    keep=~np.isnan(yPfull)
    # oof over the kept subset would change folds; simplest honest secondary: corr of the big5_v2-ES model's preds vs Pink-ES
    rP=rr(predES[keep], yPfull[keep])
print('\n===== RESULT (one look) =====')
print('  held-out r(pred, big5_v2-ES) = %+.3f'%rES)
print('  permutation p (5000, one-sided) = %.4f | parametric p = %.4f'%(perm_p,param_p))
print('  discriminant: r(pred, ES)=%+.3f  vs  r(pred, A)=%+.3f  -> ES-specific=%s'%(rES,rA,rES>rA))
if rP is not None: print('  [secondary] big5_v2-ES model preds vs Pink-ES = %+.3f (contaminated ridge->Pink-ES was +0.298)'%rP)
b1=rES>=0.15; b2=perm_p<0.01; b3=rES>rA
print('\n  BAR 1 held-out r >= +0.15 & right-signed: %s (%+.3f)'%('CLEAR' if b1 else 'MISS',rES))
print('  BAR 2 permutation p < 0.01: %s (%.4f)'%('CLEAR' if b2 else 'MISS',perm_p))
print('  BAR 3 discriminant r_ES > r_A: %s'%('CLEAR' if b3 else 'MISS'))
print('\nVERDICT: %s'%('PASS — voice carries ES-specific signal out-of-fold (cross-modal ES validated)' if (b1 and b2 and b3) else 'MISS — ES stays measured-but-unvalidated (documented)'))
# ---- display artifact (added POST-LOOK 2026-07-09, user 'augment what we have'): per-member voice-ES
# read for the desk. Deterministic (fixed KFold seeds) -> byte-stable re-derivation of the recorded
# look; the artifact adds NO new inference, it persists the same OOF predictions. DISPLAY-ONLY.
from moneyball_core import dump_atomic
zp=(predES-predES.mean())/max(1e-12,predES.std(ddof=1)); zt=(yES-yES.mean())/max(1e-12,yES.std(ddof=1))
dump_atomic({'members':{m:{'voice_es_z':round(float(zp[i]),3),'text_es_z':round(float(zt[i]),3),
                           'gap':round(float(zp[i]-zt[i]),3)} for i,m in enumerate(mem)},
             'heldout_r':round(rES,3),'perm_p':perm_p,'n':len(mem),
             'note':'voice->ES OOF ridge preds (b5_es_crossmodal one-look, prereg RESULT r=+0.208); display-only'},
            B+r'\data\members\b5_es_voice.json')
print('wrote b5_es_voice.json (%d members; display-only voice-ES read)'%len(mem))
