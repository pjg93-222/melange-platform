import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
from moneyball_core import dump_atomic   # S2-03: crash-safe artifact write
"""Build im_allocation.json from the CURRENT 17-topic live model (M4 fix).

The legacy fec/im_allocate2.py produced im_allocation.json from a SUPERSEDED single-channel model:
overall-stance (R&A col 7), old taxonomy PRO={'Champion','Ally'} with NO 'Defender', and a moneyscale
pflip. Its FLIP top picks were stale (Dan Sullivan / Jeanne Shaheen). The live model is per-topic:
  - per_topic_alloc.json       -> FLIP  (competitive Linear-Threshold, per sub-category, from per_topic_flip.py)
  - per_topic_coalition.json   -> COALITION (competitive Independent Cascade, from per_topic_coalition.py)

The workbook "IM Allocation" sheet shows, per member: a FLIP column, a COALITION column, and one column per
topic (the topic columns ALREADY come from per_topic_alloc.json in write_im_sheet.py). To keep the sheet
internally consistent and LIVE, we set:
    flip[member]      = sum over topics of per_topic_alloc[topic][member]   (== row-total of the topic columns)
    coalition[member] = sum over topics of per_topic_coalition[topic][member]
So the FLIP column is literally the row-sum of the per-topic flip columns, and COALITION is the matching
per-topic coalition row-sum -- both straight from the current model, no legacy taxonomy.

Run per_topic_flip.py and per_topic_coalition.py first (they write the inputs), then this, then write_im_sheet.py.
"""
import json, sys, os, collections
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
pta=json.load(open(B+r'\per_topic_alloc.json'))
ptc=json.load(open(B+r'\data\lobbying\per_topic_coalition.json'))
flip=collections.Counter(); coal=collections.Counter()
for t,d in pta.items():
    for n,a in d.items(): flip[n]+=a
for t,d in ptc.items():
    for n,a in d.items(): coal[n]+=a
out={'flip':{n:int(v) for n,v in flip.items() if v>0},'coalition':{n:int(v) for n,v in coal.items() if v>0}}
# QME 2026-10-01 (fix 9): flip/coalition VALUES are unchanged by design (M4: per-topic row-sums; legacy audit G1/G2 assert equality). But each topic is an
# independent $2M diagnostic scope with its own per-member $50k cap, so a row-sum is NOT a per-member spend and can exceed one PAC's per-candidate limit
# (PMAX, read as optimize.py does). Label it, and list the over-limit members so no reader mistakes the sheet for a payable plan. Additive keys only.
PMAX=int(os.environ.get('PMAX','10000'))
out['basis']='per-topic diagnostic row-sums (each topic is an independent $2M scope); non-additive across topics and NOT a per-member spend -- see members_over_pmax'
out['members_over_pmax']={'pmax':PMAX,'flip':{n:v for n,v in out['flip'].items() if v>PMAX},'coalition':{n:v for n,v in out['coalition'].items() if v>PMAX}}
dump_atomic(out,B+r'\im_allocation.json')
print('build_im_allocation — regenerated im_allocation.json from the LIVE per-topic model')
print('  FLIP: %d members funded (sum across %d topics)'%(len(out['flip']),len(pta)))
print('  COALITION: %d members funded (sum across %d topics)'%(len(out['coalition']),len(ptc)))
print('  WARNING: %d FLIP and %d COALITION members have row-sums above PMAX $%s (diagnostic row-sums, not a per-member spend; see members_over_pmax)'%(
    len(out['members_over_pmax']['flip']),len(out['members_over_pmax']['coalition']),f'{PMAX:,}'))
print('\n  FLIP top-8 (live):')
for n,v in flip.most_common(8): print('     %-24s $%s'%(n,f'{v:,}'))
print('  COALITION top-8 (live):')
for n,v in coal.most_common(8): print('     %-24s $%s'%(n,f'{v:,}'))
