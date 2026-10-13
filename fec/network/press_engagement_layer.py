# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root (arch review 2026-07-02)
"""PRESS ENGAGEMENT LAYER — Fink et al. (2023, Data in Brief) Congressional Twitter influence network +
Danowski & Cepela (2010) named-entity mention networks, transplanted to the press corpus.

Fink et al. built the only network with EMPIRICAL pairwise influence probabilities: influence(i->j) =
(times j retweeted/quoted/replied-to/mentioned i) / (tweets issued by i) — purpose-built to feed the
Independent Cascade Model (the same cascade family our coalition engine runs).  We have no tweets, but
member press TITLES name co-actors ("Senators Kim and Warren Seek Answers...", "Marshall, Durbin
introduce...").  Mapping: member j's press naming member i = j engaging with i's work; so
    P(i influences j) = (# of j's items naming i) / (# of items issued by i)      [Fink normalization]
This yields a directed, weighted engagement layer that (a) empirically validates cosponsor-edge
probabilities, and (b) reaches actors the cosponsor graph can't (leadership norm: D4).

Mention extraction is PRECISION-FIRST (headline case makes bare surnames toxic: 'Banks', 'Flood',
'Young', 'Brown' are common nouns/adjectives in Title Case).  A mention counts only when:
  * the member's FULL name appears verbatim, or
  * an honorific chain precedes the surname: Sen./Sens./Senator(s)/Rep./Reps./Representative(s)/
    Congressman/-woman/Chair(man|woman)/Ranking Member/Leader/Speaker/Whip/Dr., allowing comma/'and'
    lists ("Sens. Marshall, Durbin and Vance").
  * never the author him/herself, never a surname that is a token of the author's own name, never
    surnames shared by two members (ambiguous), never surnames < 4 chars.
Writes press_engagement.json (directed probs + symmetrized layer + overlap diagnostics)."""
import csv, json, sys, re, collections, glob, os
from press_stream import load_tone_rows
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
SRC=os.path.join(MB_DRAFT, 'cu_press_stance_tone.csv')
rows=load_tone_rows()
members=sorted(set(r['member'] for r in rows))
# surname map: last token, plus two-token surnames (Van Hollen etc.)
sur=collections.defaultdict(list)
for m in members:
    ts=m.split()
    sur[ts[-1]].append(m)
    if len(ts)>=3 and ts[-2] in ('Van','Von','De','Del','La','Mc','Della'): sur[' '.join(ts[-2:])].append(m)
AMB={s for s,ml in sur.items() if len(set(ml))>1}
HON=r'(?:Sens?\.|Senators?|Reps?\.|Representatives?|Congressman|Congresswoman|Chairman|Chairwoman|Chair|Ranking Member|Leader|Speaker|Whip|Dr\.)'
items=collections.Counter(); mention=collections.Counter()
for r in rows:
    au=r['member']; items[au]+=1
    t=(r.get('title') or '')
    autoks=set(au.split())
    hits=set()
    for s,ml in sur.items():
        if s in AMB or len(s)<4: continue
        tgt=ml[0]
        if tgt==au or s in autoks: continue
        if ' '.join(tgt.split()[-2:]) in t or tgt in t:            # full-name (or full two-token surname w/ first name) match
            if re.search(r'\b'+re.escape(tgt)+r'\b',t): hits.add(tgt); continue
        # honorific-chain match: "Sens. Marshall, Durbin and Vance" -> every listed surname counts
        if re.search(HON+r'\s+(?:[A-Z][\w.\-]+(?:,\s*|\s+and\s+|\s*&\s*|\s+))*'+re.escape(s)+r'\b',t):
            hits.add(tgt)
    for tgt in hits: mention[(au,tgt)]+=1                          # author engages target
# Fink normalization: P(i influences j) = mentions of i BY j / items BY i.
# Fink et al. required >=100 tweets/member before trusting empirical probabilities; our corpus is far
# smaller, so the analog floor is MIN_ITEMS on the SOURCE (else tiny denominators mint fake P=1.0 edges).
MIN_ITEMS=int(os.environ.get('ENG_MIN_ITEMS','5'))
prob={}
for (j,i),n in mention.items():                                     # j authored, i was named -> i influenced j
    if items[i]>=MIN_ITEMS: prob[(i,j)]=min(1.0,n/items[i])
# symmetrized layer (house rule D2: mean-symmetrize) for cascade augmentation
sym=collections.defaultdict(dict)
for (i,j),p in prob.items():
    q=prob.get((j,i),0.0); w=(p+q)/2
    sym[i][j]=max(sym[i].get(j,0),w); sym[j][i]=max(sym[j].get(i,0),w)
# diagnostics vs the cosponsor layer
try:
    import openpyxl
    C={m['member']:m for m in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json')))}
    bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
    net=max(glob.glob(os.path.join(B, 'congress_cosponsor_network_*.xlsx')),key=os.path.getmtime)
    ne=openpyxl.load_workbook(net,data_only=True)['Edges']; cadj=collections.defaultdict(set); deg=collections.Counter()
    for r2 in range(2,ne.max_row+1):
        a,b=bio2name.get(ne.cell(r2,1).value),bio2name.get(ne.cell(r2,2).value)
        if a in C and b in C and a!=b: cadj[a].add(b); cadj[b].add(a); deg[a]+=1; deg[b]+=1
    pairs={(min(i,j),max(i,j)) for (i,j) in prob}
    overlap=sum(1 for i,j in pairs if j in cadj.get(i,()))
    isolated=[m for m in C if deg[m]==0]
    isol_cov=sorted({m for (i,j) in prob for m in (i,j)} & set(isolated))
    print('ENGAGEMENT LAYER: %d directed influence edges over %d member pairs (from %d validated mentions)'%(len(prob),len(pairs),sum(mention.values())))
    print('  overlap with cosponsor layer: %d/%d pairs (%.0f%%) are also cosponsor edges — independent confirmation'%(overlap,len(pairs),100*overlap/max(1,len(pairs))))
    print('  D4: engagement layer reaches %d of the %d cosponsor-isolated members: %s'%(len(isol_cov),len(isolated),', '.join(isol_cov[:8])))
except Exception as e:
    overlap=None; print('(cosponsor diagnostics skipped: %s)'%e)
top=sorted(prob.items(),key=lambda kv:-kv[1])[:10]
print('  strongest empirical influence probabilities (Fink-normalized):')
for (i,j),p in top: print('    P(%s -> %s) = %.3f'%(i,j,p))
json.dump({'method':'Fink et al. 2023 normalization over honorific/full-name press-title mentions',
           'items_per_member':dict(items),
           'directed':[{'src':i,'dst':j,'p':round(p,4)} for (i,j),p in sorted(prob.items())],
           'symmetric':[{'a':i,'b':j,'w':round(w,4)} for i in sorted(sym) for j,w in sorted(sym[i].items()) if i<j],
           'mentions':[{'author':a,'named':b,'n':n} for (a,b),n in sorted(mention.items())]},
          open(os.path.join(B, 'data', 'stance', 'press_engagement.json'),'w', newline='\r\n'),indent=1)
print('wrote press_engagement.json (%d directed edges) — candidate augmentation layer for the cascade'%len(prob))
