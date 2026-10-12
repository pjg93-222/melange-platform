# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""SMOreg refit on the Pennebaker-King essays corpus (user-directed 2026-07-03: "see if we can fit
the SMOreg with it [Empath]"). Mairesse et al. (2007) trained SMOreg (SVM regression) on LIWC/MRC
features of the essays corpus; his model file is LIWC-bound. This refits the SAME construction on
OUR feature pipeline (big5_traits categories, Empath-augmented via fec/liwc/empath_categories.dic):
  essays.csv (2,467 stream-of-consciousness essays, Big5 y/n labels)
  -> per-1000-word category rates (identical tokenizer/features to big5_traits.rates)
  -> corpus-standardized -> per-trait LinearSVR (SMOreg = SMO-solved epsilon-SVR; linear kernel so
     the model exports as {trait:{feature:weight}} into fec/liwc/mairesse_smoreg.json, which
     big5_traits' parity hook consumes directly).
Reports 5-fold CV binary accuracy per trait (Mairesse-era benchmark: ~55-62%). The json is written
to *.candidate first — promote it only if the party-sign validation improves (decision printed)."""
import csv, io, json, os, re, sys, collections, urllib.request
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from sklearn.svm import LinearSVR
from sklearn.model_selection import KFold

FE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LX = os.path.join(FE, 'liwc')
ESSAYS = os.path.join(LX, 'essays.csv')
MIRRORS = [
    'https://raw.githubusercontent.com/SenticNet/personality-detection/master/essays.csv',
    'https://raw.githubusercontent.com/jcl132/personality-prediction-from-text/master/essays.csv',
]
if not os.path.exists(ESSAYS):
    os.makedirs(LX, exist_ok=True)
    for u in MIRRORS:
        try:
            print('fetching essays corpus:', u)
            urllib.request.urlretrieve(u, ESSAYS)
            break
        except Exception as e:
            print('  mirror failed:', str(e)[:120])
    if not os.path.exists(ESSAYS):
        print('!! could not fetch essays.csv — download it manually into fec/liwc/'); sys.exit(1)

# ---- categories: same construction as big5_traits (exemplar seeds + Empath dic overlay) ----
CAT={
 'i':"i me my mine myself im ive ill id",
 'we':"we us our ours ourselves were weve well",
 'you':"you your yours youre",
 'article':"a an the",
 'negate':"no not never none cannot cant dont wont isnt wasnt arent didnt doesnt wouldnt couldnt shouldnt nothing neither nor",
 'posemo':"good great love happy hope proud thank thanks celebrate honor honored best wonderful glad support win wins success benefit benefits opportunity grateful appreciate excellent congratulations congrats delighted terrific amazing",
 'negemo':"bad wrong hate hurt nasty terrible awful fail failed failure crisis threat worry worried fear afraid angry anger sad loss lose damage harm danger attack disaster outrage shameful disgrace broken devastating reckless",
 'anx':"worried anxious afraid fear fearful nervous risk risky threat threatened threatens uncertain uncertainty alarm alarming danger dangerous crisis vulnerable insecure unstable",
 'anger':"angry anger outrage outrageous furious fight fighting attack attacks hate disgusting shameful corrupt corruption betray betrayal rigged radical disgrace hostile",
 'social':"talk talked share friend friends family families community communities neighbor neighbors together meeting met join joined joining team people folks everyone partnership colleagues constituents neighbors town",
 'family':"family families mother father mom dad son daughter kids children parents grandmother grandfather wife husband brother sister grandparents",
 'achieve':"work working achieve achieved achievement accomplish accomplished succeed success successful win won effort efforts goal goals deliver delivered results earn earned lead leader leadership fought secured secure passed",
 'work':"job jobs work working business businesses economy economic employment worker workers industry wages career manufacturing farmers",
 'insight':"think thinking believe know understand consider considered because reason reasons means thus therefore however although question questions idea ideas analysis examine evidence solution solutions",
 'certain':"always never must certainly absolutely definitely clear clearly undeniable undoubtedly every all guarantee fact facts truth proven commit committed",
 'tentat':"maybe perhaps might may could seems appears possibly somewhat unclear likely probably potential potentially",
 'money':"money cost costs price prices dollar dollars fund funds funding budget tax taxes pay paid spending afford debt",
 'relig':"god faith church prayer pray blessed bless religious christian jewish muslim worship holy",
 'swear':"damn hell crap",
 'leisure':"vacation game games sports fun play movie music concert hobby travel weekend",
 'body':"body hands hand head heart blood eyes feet skin",
 'health':"health healthy sick illness disease doctor hospital medicine medical care treatment patient patients",
 'death':"death die died dying dead killed kill funeral grief mourn",
 'sad':"sad sadness cry crying tears grief heartbroken sorrow disappointed loss",
}
CATW={k:set(v.split()) for k,v in CAT.items()}
CATPRE={k:tuple() for k in CAT}
_dicf=sorted(f for f in os.listdir(LX) if f.endswith('.dic')) if os.path.isdir(LX) else []
if _dicf:
    _hdr={}; _inh=False; _exact={c:set() for c in CAT}; _pre={c:[] for c in CAT}; _used=set()
    for _ln in open(os.path.join(LX,_dicf[0]),encoding='utf-8',errors='replace'):
        _ln=_ln.strip()
        if _ln=='%': _inh=not _inh; continue
        if not _ln: continue
        _p=_ln.split('\t') if '\t' in _ln else _ln.split()
        if _inh:
            if len(_p)>=2 and _p[0].isdigit(): _hdr[_p[0]]=_p[1].strip().lower()
        else:
            _w=_p[0].lower()
            for _i in (_q for _q in _p[1:] if _q.isdigit()):
                _c=_hdr.get(_i)
                if _c in CAT:
                    _used.add(_c)
                    (_pre[_c].append(_w[:-1]) if _w.endswith('*') else _exact[_c].add(_w))
    for _c in _used: CATW[_c]=_exact[_c]; CATPRE[_c]=tuple(sorted(set(_pre[_c])))
    print('dictionary overlay: %s (%d categories)' % (_dicf[0], len(_used)))
FEATS=list(CAT)+['sixltr','exclam','number']
WORD=re.compile(r"[a-z']+")
def rates(t):
    cnt=collections.Counter(); n=0; six=0
    t=re.sub(r'https?://\S+|@\w+','',t.lower())
    excl=t.count('!'); num=len(re.findall(r'\d',t))
    for w in WORD.findall(t.replace("'","")):
        n+=1
        if len(w)>=6: six+=1
        for c,s in CATW.items():
            if w in s or (CATPRE[c] and w.startswith(CATPRE[c])): cnt[c]+=1
    if n<50: return None
    r={c:1000.0*cnt[c]/n for c in CAT}; r['sixltr']=1000.0*six/n; r['exclam']=1000.0*excl/n; r['number']=1000.0*num/n
    return r

rows=[]
with io.open(ESSAYS, encoding='latin-1', newline='') as fh:
    for r in csv.DictReader(fh):
        k={c.strip().upper():c for c in r}
        txt=r.get(k.get('TEXT',''),'')
        y={t:r.get(k.get(c,''),'').strip().lower()=='y' for t,c in
           (('E','CEXT'),('N','CNEU'),('A','CAGR'),('C','CCON'),('O','COPN'))}
        f=rates(txt or '')
        if f: rows.append((f,y))
print('essays parsed: %d usable' % len(rows))
X=np.array([[f[c] for c in FEATS] for f,_ in rows])
mu=X.mean(0); sd=X.std(0, ddof=1); sd[sd==0]=1.0
Z=(X-mu)/sd
OUT={}
GRID=[(C,e) for C in (0.03,0.1,0.3,1.0) for e in (0.0,0.1,0.2)]
print('%-3s %-16s %-10s %s' % ('', 'best (C,eps)', '5-fold acc', 'top standardized weights'))
for t in ('E','ES','A','C','O'):
    src='N' if t=='ES' else t
    y=np.array([1.0 if (not d[src] if t=='ES' else d[src]) else 0.0 for _,d in rows])
    best=(None,-1.0)
    for Cc,e in GRID:
        accs=[]
        for tr,te in KFold(5,shuffle=True,random_state=7).split(Z):
            m=LinearSVR(C=Cc,epsilon=e,max_iter=20000,random_state=7).fit(Z[tr],y[tr])
            p=m.predict(Z[te]); accs.append((((p>np.median(p)).astype(float))==y[te]).mean())
        cv=float(np.mean(accs))
        if cv>best[1]: best=((Cc,e),cv)
    (Cc,e),cv=best
    m=LinearSVR(C=Cc,epsilon=e,max_iter=20000,random_state=7).fit(Z,y)
    w={FEATS[i]:round(float(m.coef_[i]),4) for i in range(len(FEATS))}
    OUT[t]=w
    top=sorted(w,key=lambda k:-abs(w[k]))[:5]
    print('%-3s C=%-5g e=%-5g %.3f      %s' % (t,Cc,e,cv,', '.join('%s=%+.3f'%(k,w[k]) for k in top)))
cand=os.path.join(LX,'mairesse_smoreg.json.candidate')
json.dump(OUT,open(cand,'w'),indent=1)
print('wrote %s — promote by renaming to mairesse_smoreg.json ONLY if party-sign validation improves' % cand)
