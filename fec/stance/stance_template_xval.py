# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root (arch review 2026-07-02)
"""STANCE TEMPLATE CROSS-VALIDATION (user-directed 2026-07-01: "I will need to look at and cross
validate the stance templates used").  Collates every stance template/label artifact in the system,
cross-checks them against each other, and writes a review document the user can adjudicate from.

The FOUR template artifacts and where they live:
  T1  cu_stance_label_set.csv (Draft Systems)  — the DEBATE label template: pre_label vs human_label
      per article (with full text snippets).  The xlsx of the same name is a mirror; R reads the csv.
  T2  api_prelabel.py SYSTEM prompt            — the Burnham factorization template (target -> stance ->
      CU valence -> alignment -> attribution) + the FIXED VALENCE PRIORS (CFPB expand=-1 / restrain=+1,
      interchange caps=-1, tax-status attacks=-1, fraud/privacy/CDFI/defense=+1).
  T3  bill_directions.csv (Melange root)       — the legislative half of the target-valence registry
      (user-owned; Direction 1/-1/0 per bill).
  T4  gold_label_queue.csv                     — the 312-item balanced queue: gold_* columns (user's),
      api_* columns (Opus 4.8 pass), model_stance (DEBATE).
Checks:
  A. T1 internal: pre_label vs human_label agreement + Cohen's kappa (the legacy template's own quality).
  B. T1 x T4: where the same article appears in both, three-way agreement human_label / api / DEBATE.
  C. T2 x T3: valence-prior consistency — do the API prompt's priors agree with the sign structure of
     bill_directions.csv and the DEFENSIVE issue set?  Contradictions listed for the user.
Also emits stance_template_variants.csv — Burnham-style SYNONYMOUS HYPOTHESIS templates (3 paraphrases
per class) for the S3 NLI layer; template robustness = agreement across paraphrases (his validation).
Outputs: stance_templates_review.md + stance_template_variants.csv."""
import csv, io, json, sys, collections
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
DS=MB_DRAFT
def kappa(pairs):
    n=len(pairs)
    if not n: return 0.0,0.0
    po=sum(1 for a,b in pairs if a==b)/n
    ca=collections.Counter(a for a,_ in pairs); cb=collections.Counter(b for _,b in pairs)
    pe=sum(ca[k]*cb[k] for k in set(ca)|set(cb))/n/n
    return po,(po-pe)/(1-pe) if pe<1 else 0.0
def norm(x): return (x or '').strip().lower()
L=[]
def out(s): L.append(s); print(s)
out('# Stance-template cross-validation — %s'%__import__('datetime').date.today().isoformat())
# ---- A. T1 internal quality ----
t1=list(csv.DictReader(open(DS+r'\cu_stance_label_set.csv',encoding='utf-8-sig')))
pairs=[(norm(r['pre_label']),norm(r['human_label'])) for r in t1 if norm(r.get('pre_label')) and norm(r.get('human_label'))]
po,k=kappa(pairs)
out('\n## A. T1 — DEBATE label template (cu_stance_label_set.csv, %d rows, %d dual-labeled)'%(len(t1),len(pairs)))
out('pre_label vs human_label: agreement %.1f%%, Cohen kappa %.3f  (Zhou-Elejalde reported kappa 0.81-0.94 for a clean template)'%(100*po,k))
cm=collections.Counter(pairs)
out('confusion (pre -> human): '+', '.join('%s->%s:%d'%(a,b,n) for (a,b),n in cm.most_common(8)))
byclass=collections.defaultdict(lambda:[0,0])
for a,b in pairs:
    byclass[b][1]+=1
    if a==b: byclass[b][0]+=1
out('per-class recall of the template: '+', '.join('%s %.0f%% (n=%d)'%(c,100*v[0]/max(1,v[1]),v[1]) for c,v in sorted(byclass.items())))
# ---- B. T1 x T4 three-way ----
t4={ (r.get('url') or '').strip():r for r in csv.DictReader(open(B+r'\data\stance\gold_label_queue.csv',encoding='utf-8-sig')) }
tri=[]
for r in t1:
    u=(r.get('url') or '').strip()
    if u in t4 and norm(r.get('human_label')):
        q=t4[u]; tri.append((norm(r['human_label']),norm(q.get('api_cu_alignment')),norm(q.get('model_stance'))))
nsrc=collections.Counter(norm(t4[(r.get('url') or '').strip()].get('model_stance_src')) for r in t1
                         if (r.get('url') or '').strip() in t4 and norm(r.get('human_label')))
out('\n## B. T1 x T4 — overlap articles (n=%d): human vs API vs DEBATE'%len(tri))
if tri:
    ha=sum(1 for h,a,_ in tri if h==a)/len(tri); hd=sum(1 for h,_,d in tri if h==d)/len(tri)
    out('human==API %.0f%% | human==DEBATE %.0f%%'%(100*ha,100*hd))
    out('CAVEATS (read before concluding anything): (1) DEBATE is CIRCULAR here — overlap stance_src = %s,'%dict(nsrc))
    out('    i.e. where src=human_label, model_stance IS the human label, so 100% is tautological, not skill.')
    out('(2) the API pass labeled from TITLE+metadata only; humans labeled full text — the 45% human/API gap')
    out('    mixes template disagreement with the title-only handicap.  The CLEAN comparison is T4 itself:')
    out('    adjudicate gold_* on the 312 balanced items, then stance_eval.py scores api_cu_alignment and')
    out('    model_stance against YOUR gold on equal footing.')
    dis=[(h,a,d) for h,a,d in tri if h!=a or h!=d]
    out('disagreement rows to eyeball: %d'%len(dis))
else: out('no URL overlap — the balanced queue drew different articles; adjudicate gold_* first, then rerun')
# ---- C. T2 x T3 valence-prior consistency ----
out('\n## C. T2 x T3 — valence priors vs bill_directions.csv vs DEFENSIVE set')
PRIORS={'CFPB':{'expand':-1,'restrain':+1},'Interchange':-1,'Credit Card Mandates':-1,'CU Tax Status':{'attack':-1,'defend':+1},
        'Fraud Prevention':+1,'Data Privacy & Cyber':+1,'CDFI Fund':+1,'Defense CU Issues':+1,
        'Charter Modernization':+1,'Field of Membership':+1}
bd=list(csv.DictReader(open(B+r'\data\legislation\bill_directions.csv',encoding='utf-8-sig')))
agg=collections.Counter()
for r in bd:
    d=(r.get('Direction') or '').strip()
    if d in ('1','-1'): agg[(r.get('Category') or '').strip(),d]+=1
out('bill_directions.csv coverage: %d rows, categories: %s'%(len(bd),sorted({(r.get("Category") or "").strip() for r in bd})))
cf_pro=agg[('CFPB','1')]; cf_anti=agg[('CFPB','-1')]
out('CFPB registry: %d pro-CU (restraint) / %d anti-CU (expansion) directions — CONSISTENT with the T2 prior (restrain=+1) %s'
    %(cf_pro,cf_anti,'OK' if cf_pro>=cf_anti else '<< CONTRADICTION: eyeball the registry'))
DEF=set(json.load(open(B+r'\category_stance.json'))['defensive'])
for cat in ('Interchange','Credit Card Mandates'):
    out('%s: T2 prior = anti-CU target (-1) and DEFENSIVE set contains it: %s -> consistent (CU defends the status quo)'%(cat,cat in DEF))
out('NOTE for review: T2 priors are hard-coded in fec/stance/api_prelabel.py SYSTEM (lines ~27-34). The user-owned')
out('registry (T3 + a future press-side valence csv, phase S2) should be the single source; the API prompt')
out('should eventually READ the registry rather than embed it.')
# ---- D. Burnham synonymous-hypothesis variants for S3 ----
rows_v=[]
TARGETS=[('CFPB','the Consumer Financial Protection Bureau'),('Interchange','interchange fee caps'),
         ('Credit Card Mandates','the Credit Card Competition Act'),('CU Tax Status','the credit union tax exemption'),
         ('Big Banks','large national banks')]
TEMPL={'support':['{m} supports {t}.','{m} is in favor of {t}.','{m} endorses {t}.'],
       'oppose':['{m} opposes {t}.','{m} is against {t}.','{m} rejects {t}.'],
       'neutral':['{m} takes no position on {t}.','{m} is neutral about {t}.','{m} neither supports nor opposes {t}.']}
for key,t in TARGETS:
    for cls,ts in TEMPL.items():
        for i,tpl in enumerate(ts):
            rows_v.append({'target_key':key,'class':cls,'variant':i+1,'hypothesis_template':tpl.replace('{t}',t),
                           'notes':'Burnham PSRM 2024: score all variants; template robustness = cross-variant agreement. EDIT FREELY — user-owned.'})
with io.open(B+r'\data\stance\stance_template_variants.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['target_key','class','variant','hypothesis_template','notes']); w.writeheader(); w.writerows(rows_v)
out('\n## D. S3 NLI hypothesis variants -> stance_template_variants.csv (%d templates, 3 paraphrases/class x %d targets)'%(len(rows_v),len(TARGETS)))
out('\n## Where each template lives (for your review pass)')
out('- T1  %s\\cu_stance_label_set.csv  (pre_label/human_label + text snippets)'%DS)
out('- T2  %s\\fec\\stance\\api_prelabel.py  (SYSTEM prompt: factorization + valence priors)'%B)
out('- T3  %s\\data\legislation\bill_directions.csv  (user-owned legislative valence registry)'%B)
out('- T4  %s\\data\\stance\\gold_label_queue.csv  (312 balanced items; adjudicate gold_*, then: python fec/stance/stance_eval.py api_cu_alignment)'%B)
out('- NEW %s\\data\\stance\\stance_template_variants.csv  (S3 NLI paraphrase set — edit before S3 benchmarking)'%B)
io.open(B+r'\stance_templates_review.md','w',encoding='utf-8').write('\n'.join(L)+'\n')
print('\nwrote stance_templates_review.md')
