# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""TWEET GOLD QUEUE (user-directed 2026-07-02: "a diverse 400 sample... similar in practice to the
other target-aware sentiment analysis").  Mirrors build_gold_queue.py v2, adapted to tweet form:

  * source = the unified Social Master corpus (X + Facebook + verified-handle Bluesky, all scored),
  * MEMBER CAP <= 2 (spread across the ~485 covered members),
  * platform quotas (all verified Bluesky, ~100 Facebook, X the rest) + RT/original mix preserved,
  * predicted-stance round-robin (oppose first — the rare class),
  * FORCED HARD SLICES:
      - antibank: the diagnosed failure mode (anti-bank/CFPB populist language reading as pro-CU) —
        the 62%-consistency investigation lives or dies here,
      - lowmargin: bottom-quartile classifier confidence (most informative labels),
      - disagree: posts from the members whose tweet net-stance contradicts their established tier
        (Garcia, Luna, Whitehouse, Green, Brown, Moore...),
  * target-aware factorization schema, tweet-form attribution classes:
      member-own | rt-endorsement (an RT is amplification — label the MEMBER's implied position) |
      third-party-voice.
Output: tweet_gold_queue.csv (gold_* columns = USER'S; api_* reserved for an optional pre-label pass)."""
import sys,csv,io,json,re,random,collections,os
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import pandas as pd
random.seed(7)
M=MB_ROOT
N_TOTAL=400; MEMBER_CAP=2
Q_FB=100; Q_ANTIBANK=100; Q_LOWMARGIN=60
ANTIBANK=re.compile(r'big bank|wall street|megabank|junk fee|price goug|\bcfpb\b|consumer financial|'
                    r'hold(ing)? banks accountable|bank profits|greedy bank',re.I)
DISAGREE_MEMBERS={'Sylvia Garcia','Shontel Brown','Gwen Moore','Anna Luna','Sheldon Whitehouse','Al Green'}
corp=pd.read_excel(M+r'\Social Master.xlsx',sheet_name='Corpus',dtype=str)
corp=corp[(corp['stance'].fillna('')!='')&(corp['verified_handle'].astype(str)=='1')].reset_index(drop=True)
corp['margin']=pd.to_numeric(corp['stance_margin'],errors='coerce').fillna(0)
# S3-04 fix (audit 2026-07-02): party from the SOURCE roster, not the previous cycle's dashboard bundle
import moneyball_core as MC
party=MC.party()
mlow=corp['margin'].quantile(0.25)
def slices(r):
    s=[]
    if ANTIBANK.search(r['text'] or ''): s.append('antibank')
    if r['margin']<=mlow: s.append('lowmargin')
    if r['member'] in DISAGREE_MEMBERS: s.append('disagree')
    if str(r['is_rt'])=='1': s.append('rt')
    return s
corp['slices']=corp.apply(slices,axis=1)
picked={}; percap=collections.Counter()
def take(i,why):
    if i in picked: picked[i].add(why); return True
    m=corp.at[i,'member']
    if percap[m]>=MEMBER_CAP: return False
    picked[i]={why}; percap[m]+=1; return True
def pool(mask):
    ix=list(corp[mask].index); random.shuffle(ix); return ix
# 1) all verified Bluesky (rare platform)
for i in pool(corp['platform']=='bluesky'): take(i,'bluesky')
# 2) forced hard slices, stance-round-robined within each
def fill_slice(tag,quota):
    got=sum(1 for v in picked.values() if tag in v)
    ix=pool(corp['slices'].map(lambda s:tag in s))
    byst=collections.defaultdict(list)
    for i in ix: byst[corp.at[i,'stance']].append(i)
    order=['oppose','support','neutral']; k={s:0 for s in order}
    while got<quota:
        moved=False
        for s in order:
            while k[s]<len(byst[s]):
                i=byst[s][k[s]]; k[s]+=1
                if take(i,tag): got+=1; moved=True; break
            if got>=quota: break
        if not moved: break
fill_slice('antibank',Q_ANTIBANK); fill_slice('lowmargin',Q_LOWMARGIN); fill_slice('disagree',12)
# 3) Facebook quota, then X fill — stance round-robin, oppose first
def fill_platform(plat,quota):
    got=sum(1 for i in picked if corp.at[i,'platform']==plat)
    byst=collections.defaultdict(list)
    for i in pool(corp['platform']==plat): byst[corp.at[i,'stance']].append(i)
    order=['oppose','support','neutral']; k={s:0 for s in order}
    while got<quota and len(picked)<N_TOTAL:
        moved=False
        for s in order:
            while k[s]<len(byst[s]):
                i=byst[s][k[s]]; k[s]+=1
                if take(i,plat): got+=1; moved=True; break
            if got>=quota or len(picked)>=N_TOTAL: break
        if not moved: break
fill_platform('facebook',Q_FB); fill_platform('x',N_TOTAL)   # x fills to the 400 ceiling
rows=[]
for i,whys in picked.items():
    r=corp.loc[i]
    rows.append({'id':'tw%05d'%i,'platform':r['platform'],'member':r['member'],
        'party':party.get(r['member'],'?'),'date':r['date'],'is_rt':r['is_rt'],
        'text':(r['text'] or '')[:900],'url':r['url'],'slices':';'.join(sorted(whys|set(r['slices']))),
        'model_stance':r['stance'],'model_margin':r['stance_margin'],
        'api_target':'','api_stance_toward_target':'','api_target_cu_valence':'','api_cu_alignment':'',
        'api_attribution':'','api_confidence':'','api_rationale':'','model_vs_api':'',
        'gold_target':'','gold_stance_toward_target':'','gold_target_cu_valence':'','gold_cu_alignment':'',
        'gold_attribution':'','gold_notes':''})
rows.sort(key=lambda r:(r['member'],r['date']))
OUT=M+r'\data\stance\tweet_gold_queue.csv'
# LABEL-PRESERVATION GUARD: ids are positional ('tw%05d' % corpus index) so hand gold_* / paid api_* labels cannot be re-joined
# after a rebuild; refuse to overwrite a queue that already holds any (--force overrides, discarding them).
if os.path.exists(OUT) and '--force' not in sys.argv:
    with io.open(OUT,encoding='utf-8-sig',newline='') as f:
        n=sum(1 for r in csv.DictReader(f) for k,v in r.items() if k and k.startswith(('gold_','api_')) and (v or '').strip())
    if n: raise SystemExit('REFUSING to overwrite %s: %d non-empty gold_*/api_* cells would be wiped (pass --force to override)'%(OUT,n))
with io.open(OUT+'.tmp','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
os.replace(OUT+'.tmp',OUT)
cc=collections.Counter(r['model_stance'] for r in rows); pl=collections.Counter(r['platform'] for r in rows)
mm=collections.Counter(r['member'] for r in rows); pt=collections.Counter(r['party'] for r in rows)
sl=collections.Counter(s for r in rows for s in r['slices'].split(';'))
rt=sum(1 for r in rows if str(r['is_rt'])=='1')
print('TWEET GOLD QUEUE: %d items -> tweet_gold_queue.csv'%len(rows))
print('  platforms %s | party %s | RTs %d'%(dict(pl),dict(pt),rt))
print('  predicted-stance mix %s (user labels will measure per-class truth)'%dict(cc))
print('  members: %d distinct (cap %d)'%(len(mm),MEMBER_CAP))
print('  slices:',{k:v for k,v in sl.most_common(8)})
print('  attribution classes for tweet form: member-own | rt-endorsement | third-party-voice')
