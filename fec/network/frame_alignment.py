# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root (arch review 2026-07-02)
"""FRAME ALIGNMENT — Joo & Steinert-Threlkeld (2022, Computational Communication Research) mapped from
images to press text.  Two of their instruments transplant directly:

 1. FRAME ALIGNMENT = DUPLICATION RATE (their Table 3).  They measured how often protesters share
    near-duplicate IMAGES per frame label (Venezuela state-violence: 31% duplication = highly aligned
    campaign; Hong Kong children: 0.3%).  Text analog: near-duplicate press TITLES across DIFFERENT
    members within an issue = coordinated messaging.  High alignment -> a disciplined campaign wave
    (their point: policymakers respond more to consistent frames — Wouters & Walgrave); our reading:
    an issue where members echo one script (e.g. a coordinated tax-status defense week) vs one where
    every office freelances.  Near-duplicate = 3-word-shingle Jaccard >= 0.5 (their CNN-feature
    distance < 0.2 analog).
 2. BOTTOM-UP LABEL ASSOCIATION (their Tables 1-2).  They chi-squared Google-Vision labels against
    party (Republican males: 'military officer', 'uniform'; Democrats: 'community', 'conversation').
    Ours: chi-squared press CATEGORIES against party, and distinctive title TERMS per party within the
    corpus — a data-driven read on how each party FRAMES credit-union issues, before any theory.
Writes data/stance/frame_alignment.json."""
import csv, json, sys, re, collections
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
SRC=MB_DRAFT+r'\cu_press_stance_tone.csv'
rows=list(csv.DictReader(open(SRC,encoding='utf-8-sig')))
def cats_of(r):
    c=(r.get('categories') or '').strip()
    return [p.strip() for p in c.replace('|',';').split(';') if p.strip()]
def pty(r):
    p=(r.get('party') or '').strip().upper()
    return p[0] if p[:1] in ('D','R','I') else '?'
for r in rows: r['party']=pty(r)
def shingles(t,k=3):
    ws=re.sub(r'[^a-z0-9 ]',' ',t.lower()).split()
    return {' '.join(ws[i:i+k]) for i in range(len(ws)-k+1)} if len(ws)>=k else {' '.join(ws)} if ws else set()
def jac(a,b):
    if not a or not b: return 0.0
    return len(a&b)/len(a|b)
# ---- 1. frame alignment per issue (and per issue x party) ----
byissue=collections.defaultdict(list)
for r in rows:
    sh=shingles(r.get('title') or '')
    for c in cats_of(r): byissue[c].append((r['member'],r.get('party') or '?',sh))
align=[]
print('FRAME ALIGNMENT (share of items near-duplicated by ANOTHER member — their Table 3 duplication rate):')
print('%-42s %6s %7s %7s %7s'%('issue','items','all','D','R'))
for c,its in sorted(byissue.items(),key=lambda kv:-len(kv[1])):
    if len(its)<10: continue
    def rate(sub):
        n=len(sub); dup=0
        for i in range(n):
            m,_,s=sub[i]
            if any(jac(s,s2)>=0.5 and m2!=m for m2,_,s2 in sub if s2 is not s): dup+=1
        return dup/n if n else 0.0
    ra=rate(its); rd=rate([x for x in its if x[1]=='D']); rr=rate([x for x in its if x[1]=='R'])
    align.append({'issue':c,'items':len(its),'alignment':round(ra,3),'D':round(rd,3),'R':round(rr,3)})
    print('%-42s %6d %6.1f%% %6.1f%% %6.1f%%'%(c[:42],len(its),100*ra,100*rd,100*rr))
best=max(align,key=lambda a:a['alignment'],default=None)
if best: print("  most-aligned issue: %s (%.0f%% duplication) — a coordinated campaign wave in their sense"%(best['issue'],100*best['alignment']))
# sample coordinated clusters on the top issue
clusters=[]
if best:
    its=byissue[best['issue']]; used=set()
    for i in range(len(its)):
        if i in used: continue
        grp=[i]+[j for j in range(i+1,len(its)) if j not in used and jac(its[i][2],its[j][2])>=0.5 and its[j][0]!=its[i][0]]
        if len(grp)>=3:
            used.update(grp); clusters.append(sorted({its[j][0] for j in grp}))
    if clusters: print('  largest echo-cluster on that issue: %d members repeating one script'%max(len(c) for c in clusters))
# ---- 2. bottom-up party framing: chi-squared category and term association (their Tables 1-2) ----
def chi2(a,b,c,d):
    n=a+b+c+d
    if not all((a+b,c+d,a+c,b+d)): return 0.0
    return n*(a*d-b*c)**2/((a+b)*(c+d)*(a+c)*(b+d))
nD=sum(1 for r in rows if (r.get('party') or '')=='D'); nR=sum(1 for r in rows if (r.get('party') or '')=='R')
catass=[]
for c in sorted(byissue):
    d=sum(1 for r in rows if (r.get('party') or '')=='D' and c in cats_of(r))
    rr=sum(1 for r in rows if (r.get('party') or '')=='R' and c in cats_of(r))
    x2=chi2(d,nD-d,rr,nR-rr)
    catass.append({'cat':c,'chi2':round(x2,1),'pD':round(d/max(1,nD),4),'pR':round(rr/max(1,nR),4),
                   'leans':'D' if d/max(1,nD)>rr/max(1,nR) else 'R'})
catass.sort(key=lambda a:-a['chi2'])
print('\nPARTY FRAMING — category association (their label-vs-party chi-squared):')
for a in catass[:8]:
    print('  %-44s chi2=%6.1f  pD=%5.1f%% pR=%5.1f%%  -> %s'%(a['cat'][:44],a['chi2'],100*a['pD'],100*a['pR'],a['leans']))
STOP=set('the a an and of to in for on with by from at as is are be will new act says say bill house senate congress us s rep sen reps sens'.split())
tf=collections.defaultdict(lambda:[0,0])
for r in rows:
    p=(r.get('party') or '')
    if p not in 'DR': continue
    for w in set(re.sub(r'[^a-z ]',' ',(r.get('title') or '').lower()).split()):
        if len(w)>3 and w not in STOP: tf[w][0 if p=='D' else 1]+=1
terms=[]
for w,(d,rr) in tf.items():
    if d+rr<8: continue
    terms.append((chi2(d,nD-d,rr,nR-rr),w,d,rr))
terms.sort(reverse=True)
Dterms=[(w,d,rr) for x2,w,d,rr in terms if d/nD>rr/nR][:12]
Rterms=[(w,d,rr) for x2,w,d,rr in terms if rr/nR>d/nD][:12]
print('\ndistinctive D terms:',', '.join(w for w,_,_ in Dterms))
print('distinctive R terms:',', '.join(w for w,_,_ in Rterms))
json.dump({'alignment':align,'echo_clusters':clusters[:20],'category_association':catass,
           'terms':{'D':Dterms,'R':Rterms}},open(B+r'\data\stance\frame_alignment.json','w'),indent=1)
print('\nwrote data/stance/frame_alignment.json')
