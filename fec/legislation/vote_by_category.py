import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
from moneyball_core import dump_atomic   # S2-03: crash-safe artifact write
import pandas as pd, csv, sys, re, glob, collections
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
ARCH=sorted(glob.glob(MB_ROOT+r'\congress_bills_archive_*.xlsx'))[-1]
a=pd.read_excel(ARCH,sheet_name='All Bills')
def billkey(name,cong):
    if not isinstance(name,str) or pd.isna(cong): return None
    m=re.match(r'\s*([HS])\.?\s*([RJ]?\.?\s*(?:Res\.?)?)\s*(\d+)',name.replace('  ',' '),re.I)
    if not m: return None
    return (int(cong),'H' if m.group(1).upper()=='H' else 'S',int(m.group(3)))
# Section is the category NAME -> index 1..17 (keyword match)
SECKW=[(1,'examination'),(2,'capital'),(3,'ncua'),(4,'interchange'),(5,'payments'),(6,'technology'),(7,'fraud'),
(8,'data privacy'),(9,'charter'),(10,'defense'),(11,'field of membership'),(12,'cdfi'),(13,'cfpb'),
(14,'credit card'),(15,'telephone'),(16,'member business'),(17,'mortgage')]
def secnum(s):
    s=str(s).lower()
    for i,kw in SECKW:
        if kw in s: return i
    return None
bill2cat=collections.defaultdict(set)
for _,r in a.iterrows():
    bk=billkey(r['Name'],r['Congress']); sn=secnum(r['Section'])
    if bk and sn: bill2cat[bk].add(sn)   # V-2 (audit 2026-07-08): a bill can carry >1 Section category (107/468 do) --
                                          # accumulate the SET; the old bill2cat[bk]=sn last-write-wins clobber credited
                                          # each multi-category bill to one arbitrary (row-order) category and dropped it from the rest
print('bills mapped to a Section:',len(bill2cat))
# CFPB (section 13) per-bill direction: Direction 1=Reduce/Restrain(pro), else Reach/Expand(anti)
cfpb={}
with open(MB_ROOT+r'\data\legislation\cfpb_bill_directions.csv',encoding='utf-8-sig') as fh:
    for row in csv.DictReader(fh):
        b=row['Bill']; m=re.match(r'\s*([HS])\.?\s*([RJ]?\.?\s*(?:Res\.?)?)\s*(\d+)',b,re.I)
        if m: cfpb[('H' if m.group(1).upper()=='H' else 'S',int(m.group(3)))]=row['Direction'].strip()
ANTI={4,14}   # Interchange, Credit Card = anti-CU categories (Yea = anti)
CATLAB={1:'Examination & Supervision',2:'CU Capital Requirements',3:'NCUA Budget & Insurance',4:'Interchange',5:'Payments',
6:'Technology & Emerging',7:'Fraud Prevention',8:'Data Privacy & Cyber',9:'Charter Modernization',10:'Defense CU Issues',
11:'Field of Membership',12:'CDFI Fund',13:'CFPB',14:'Credit Card Mandates',15:'TCPA',16:'Member Business Lending',17:'Mortgage & Housing'}
def rc_bill(s):
    if not isinstance(s,str): return None
    # V-3 (audit 2026-07-08): use the SAME bill-id grammar as billkey() above so J.Res/H.J.Res roll-call rows
    # parse instead of returning None. The old r'([HS])\s*([RJ]?)\s*(\d+)' lacked the (?:Res\.?)? group, so
    # every S.J.Res/H.J.Res final-passage vote (incl. e.g. S.J.Res.32, pro-CU) was silently skipped even
    # though the bill is present in bill2cat and cfpb. (Both parsers still key on (chamber,number) only, so
    # an H.R.N vs H.J.Res.N collision remains possible -- a rarer, pre-existing issue left for a keyed refactor.)
    m=re.match(r'\s*([HS])\.?\s*([RJ]?\.?\s*(?:Res\.?)?)\s*(\d+)',s.replace('  ',' '),re.I)
    return ('H' if m.group(1).upper()=='H' else 'S',int(m.group(3))) if m else None
# accumulate signed CU-aligned vote per (bioguide,cat)
agg=collections.defaultdict(lambda:[0.0,0]); name_of={}; covered=collections.Counter(); skip=0; unmapped=0   # V-6: count non-yea/nay rows instead of dropping them silently
# V-1 (audit 2026-07-08): cu_rollcall_votes_clean.csv is an EXTERNAL, manually-cleaned roll-call export --
# NO script in this repo produces it, and it is currently ABSENT from votes/. Fail with an actionable message
# instead of a cryptic pandas error. This script is deliberately NOT wired into run_pipeline/weekly_refresh;
# its output vote_category_lean.json is a FROZEN, manually-refreshed input still consumed live by
# moneyball_core Path V and per_category_stance (audit_full carries a freshness check on it). To refresh the
# vote signal: drop the cleaned roll-call CSV at _RC, run this by hand, then verify vote_category_lean.json.
_RC=MB_ROOT+r'\votes\cu_rollcall_votes_clean.csv'
if not _o.path.exists(_RC):
    sys.exit('vote_by_category: missing input %s -- external cleaned roll-call export with no in-repo producer; '
             'restore it (or repoint _RC) before regenerating vote_category_lean.json (a frozen input consumed '
             'live by moneyball_core Path V + per_category_stance).' % _RC)
v=pd.read_csv(_RC)
def passage(s): return isinstance(s,str) and re.search(r'passage|suspend the rules and pass|concur',s,re.I) and not re.search(r'recommit|amendment',s,re.I)
for _,r in v.iterrows():
    if not passage(r['vote_question']): skip+=1; continue   # final-passage only (drop amendment/procedural noise)
    bk=rc_bill(r['bill_number'])
    if not bk: skip+=1; continue
    key=(int(r['congress']),bk[0],bk[1]); cats_hit=bill2cat.get(key)
    if not cats_hit: skip+=1; continue
    vote=str(r['vote']).strip().lower()
    if vote in ('yea','yes','aye'): yv=1
    elif vote in ('nay','no'): yv=-1
    else: unmapped+=1; continue   # V-6: Present/Not-Voting/blank -- correctly excluded, now counted (was a silent drop)
    bio=r['bioguide']; name_of[bio]=r['member']
    for cat in cats_hit:          # V-2: aggregate the vote into EVERY category the bill belongs to
        # CU-aligned sign
        if cat==13:
            # V-4 (audit 2026-07-08): Direction '1'=Restrain(pro CU), '-1'=Expand(anti); '0'=Neutral and any
            # bill missing from cfpb_bill_directions.csv -> 0 = skip (uncounted). The old
            # `-1 if d is not None else +1` scored the 6 Neutral bills as anti-CU and unknown bills as pro-CU.
            d=cfpb.get(bk); sign=+1 if d=='1' else -1 if d=='-1' else 0
            if sign==0: continue
        else:
            sign=-1 if cat in ANTI else +1
        aligned=yv*sign
        covered[CATLAB[cat]]+=1
        s=agg[(bio,cat)]; s[0]+=aligned; s[1]+=1
print('votes skipped (no bill/category match):',skip,'of',len(v),'| non-yea/nay rows excluded:',unmapped)
print('\nvote coverage by category (roll-call votes landing in each):')
for c in range(1,18):
    print('   %-26s %5d votes'%(CATLAB[c],covered[CATLAB[c]]))
# per-member per-category mean signed lean [-1,1]
out=collections.defaultdict(dict)
for (bio,cat),(ssum,cnt) in agg.items():
    out[bio][CATLAB[cat]]={'lean':ssum/cnt,'n':cnt}
dump_atomic({'name_of':name_of,'votes':out},MB_ROOT+r'\vote_category_lean.json')
nm=len(out); print('\nmembers with >=1 categorized vote:',nm)
# sanity: Durbin (interchange author) and a known CFPB opponent
b2n={v:k for k,v in name_of.items()}
for who in ['Richard Durbin','Andy Barr','Maxine Waters']:
    bio=b2n.get(who)
    if bio and bio in out:
        items=sorted(out[bio].items(),key=lambda x:x[1]['lean'])
        print('   %s: most-anti %s | most-pro %s'%(who,
            ', '.join('%s %.2f(n%d)'%(k,d['lean'],d['n']) for k,d in items[:2]),
            ', '.join('%s %.2f(n%d)'%(k,d['lean'],d['n']) for k,d in items[-2:])))
print('\nwrote vote_category_lean.json')
