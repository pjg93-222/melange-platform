# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root (arch review 2026-07-02)
"""GOLDEN LABEL SET (user-directed 2026-07-01: "give me the most cohesive and golden label set based
off MY labels").  Consolidates every USER-labeled artifact into one authoritative csv.  Machine labels
(DEBATE pre_label, API api_*) are NEVER the label here — they appear only as context columns.

Sources and precedence (all three are the user's own work):
  1. cu_stance_label_set_AUGMENTED.xlsx 'Labels (augmented)' — the v2 curated training instrument:
     human_label (3-class), user train/test/exclude split, hop1_stance where user-verified, target,
     hop2_acu_position, speaker_source, review_flag.  SUPERSEDES the v1 csv (same articles).
  2. gold_label_queue.csv gold_* — the user-ADJUDICATED balanced eval queue (4-class incl. UNRELATED,
     plus the factorization: gold_target / gold_stance_toward_target / gold_target_cu_valence /
     gold_attribution).  312 rows, 77 explicit corrections to the API pre-seed.
  3. cu_stance_label_set.csv — text_snippet join only (v1 labels superseded by 1).
Merge key: URL.  Conflict policy (the 95 overlap articles): if the two user labels disagree,
  - 'unrelated' from the queue WINS over any 3-class label (the 3-class schema couldn't express it), else
  - the AUGMENTED label wins for rows carrying a train/test split (it is the curated instrument),
  and EVERY conflict is flagged in `conflict` for the user's eyes — nothing is silently resolved.
Split assignment: augmented rows keep the user's split; queue-only rows get split='goldeval' so the
balanced eval set can never contaminate training.  Writes cu_golden_labels.csv."""
import csv, io, sys, collections, os
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import openpyxl
B=MB_ROOT
DS=MB_DRAFT
def norm(x): return (str(x) if x is not None else '').strip()
snip={}
for r in csv.DictReader(open(DS+r'\cu_stance_label_set.csv',encoding='utf-8-sig')):
    u=norm(r.get('url'))
    if u: snip[u]=r.get('text_snippet') or ''
# 1) AUGMENTED workbook
from paths import ROOT
source=os.environ.get('STANCE_LABEL_SET',os.path.join(ROOT,'source_data','cu_stance_label_set_AUGMENTED.xlsx'))
wb=openpyxl.load_workbook(source,read_only=True,data_only=True)
ws=wb['Labels (augmented)']; rows=list(ws.iter_rows(values_only=True))
ix={str(h):i for i,h in enumerate(rows[0])}
g=lambda r,k: norm(r[ix[k]]) if k in ix else ''
recs={}   # url -> record
aug_url=0
for r in rows[1:]:
    if not g(r,'member'): continue
    # AUGMENTED sheet lacks url — join via (member,title) to v1 csv for url+text
    key=None
    for u,t in (): pass
    recs[('AUG',g(r,'row'))]={'row':g(r,'row'),'member':g(r,'member'),'date':g(r,'date'),'title':g(r,'title'),
        'label':g(r,'human_label').lower(),'split':g(r,'split'),'review_flag':g(r,'review_flag'),
        'target':g(r,'target'),'hop1_stance':g(r,'hop1_stance').lower(),'hop2_acu_position':g(r,'hop2_acu_position'),
        'speaker_source':g(r,'speaker_source'),'stance_span':g(r,'stance_span'),
        'source':'augmented_v2','conflict':'','url':'','text':''}
# url+text join from v1 by row id
v1={ norm(r.get('row')):r for r in csv.DictReader(open(DS+r'\cu_stance_label_set.csv',encoding='utf-8-sig')) }
for k,rec in recs.items():
    v=v1.get(rec['row'])
    if v: rec['url']=norm(v.get('url')); rec['text']=(v.get('text_snippet') or '')[:4000]
byurl={rec['url']:rec for rec in recs.values() if rec['url']}
# 2) gold queue overlay
q=list(csv.DictReader(open(B+r'\data\stance\gold_label_queue.csv',encoding='utf-8-sig')))
conflicts=[]; added=0; enriched=0
# 2b) USER CONFLICT RESOLUTIONS (2026-07-03: user filled `your_decision` in the conflicts review
# sheet). A decided url is AUTHORITATIVE — it overrides both the aug label and the queue label,
# and the row is no longer a conflict. Latest conflicts_review_*.csv wins if several exist.
import glob as _g
RES={}
_rv=sorted(_g.glob(B+r'\conflicts_review_*.csv'))
if _rv:
    for r in csv.DictReader(open(_rv[-1],encoding='utf-8-sig')):
        u=norm(r.get('url')); d=norm(r.get('your_decision')).lower()
        if u and d in ('support','oppose','neutral','unrelated'): RES[u]=d
    print('  user conflict resolutions loaded: %d (%s)'%(len(RES),_rv[-1].rsplit(chr(92),1)[-1]))
for r in q:
    u=norm(r.get('url')); gl=norm(r.get('gold_cu_alignment')).lower()
    if not u or not gl: continue
    fac={'gold_target':norm(r.get('gold_target')),'gold_stance_toward_target':norm(r.get('gold_stance_toward_target')).lower(),
         'gold_target_cu_valence':norm(r.get('gold_target_cu_valence')),'gold_attribution':norm(r.get('gold_attribution'))}
    if u in byurl:
        rec=byurl[u]; rec.update({k:v for k,v in fac.items() if v}); enriched+=1
        if u in RES:
            if rec['label']!=RES[u] or gl!=RES[u]:
                rec['conflict']='user-resolved: %s (was aug=%s queue=%s)'%(RES[u],rec['label'],gl)
            rec['label']=RES[u]
        elif rec['label'] and gl!=rec['label']:
            if gl=='unrelated':
                rec['conflict']='aug=%s queue=unrelated -> unrelated wins (finer class)'%rec['label']; rec['label']='unrelated'
            else:
                rec['conflict']='aug=%s vs queue=%s -> augmented kept (curated split instrument); REVIEW'%(rec['label'],gl)
            conflicts.append((u,rec['conflict']))
        rec['source']+='+goldqueue'
    else:
        byurl[u]={'row':'','member':norm(r.get('member')),'date':norm(r.get('date')),'title':norm(r.get('title')),
            'label':gl,'split':'goldeval','review_flag':'','target':fac['gold_target'],
            'hop1_stance':fac['gold_stance_toward_target'],'hop2_acu_position':'','speaker_source':fac['gold_attribution'],
            'stance_span':'','source':'goldqueue_adjudicated','conflict':'','url':u,
            'text':'', **fac}
        added+=1
out=[r for r in byurl.values() if r['label'] and r['split']!='exclude']
cols=['url','member','date','title','label','split','source','conflict','review_flag','target','hop1_stance',
      'hop2_acu_position','speaker_source','gold_target','gold_stance_toward_target','gold_target_cu_valence',
      'gold_attribution','stance_span','text']
with io.open(B+r'\data\stance\cu_golden_labels.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore'); w.writeheader()
    for r in sorted(out,key=lambda x:(x['split'],x['member'],x['date'])): w.writerow(r)
lb=collections.Counter(r['label'] for r in out); sp=collections.Counter(r['split'] for r in out)
print('GOLDEN LABEL SET -> cu_golden_labels.csv')
print('  %d rows, all USER-labeled | classes %s'%(len(out),dict(lb)))
print('  splits %s  (goldeval = balanced queue, firewalled from training)'%dict(sp))
print('  queue overlay: %d articles enriched with factorization fields, %d new eval rows added'%(enriched,added))
print('  CONFLICTS flagged for your review: %d'%len(conflicts))
for u,c in conflicts[:8]: print('    - %s | %s'%(u[:60],c))
from moneyball_core import dump_atomic   # S2-03: crash-safe artifact write
dump_atomic({'rows':len(out),'classes':dict(lb),'splits':dict(sp),'conflicts':len(conflicts)},
          B+r'\data\stance\cu_golden_labels_stats.json',indent=1)
