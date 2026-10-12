# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""STAFF DIRECTORY (user 2026-07-02): weave the eight Quorum staffer exports (45-52) into one
directory and match every staffer to their legislator via the export's own 'Member Works For'
column ("Rep. John Carter (R-TX-31)") -> canonical roster names.

Matching ladder (never guesses): strip quoted nicknames ("Coach Tuberville", "Dick") ->
fold-accent full-name match -> first-name nickname expansion (Chris->Christopher...) ->
surname+chamber match ONLY when unique. Unmatched offices are reported, not dropped silently.

CU-industry star: title regex (banking/financial services/tax/economic/housing/monetary/fintech/
securities/insurance/treasury/appropriations policy roles). 'Financial Administrator' is office
bookkeeping, NOT policy — excluded. Portfolios aren't in this export, so the star is a floor;
the dashboard lets the user toggle stars per staffer.

Output: data/members/staff_directory.json {member: [{id,n,t,star,dom,dsrc}]} consumed
by make_member_desk. Direct contact fields are deliberately excluded."""
import sys,json,re,unicodedata,collections,glob,os
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import openpyxl
import moneyball_core as MC
B=MB_ROOT
SRC=os.environ.get('STAFF_SRC',os.path.join(MB_ROOT,'source_data','staff'))
FILES=sorted(glob.glob(SRC+r'\search-results-generated-by-quorum (4[5-9]).xlsx'))+ \
      sorted(glob.glob(SRC+r'\search-results-generated-by-quorum (5[0-2]).xlsx'))
def fold(s): return unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().lower().strip()
NICKF={'chris':'christopher','ed':'edward','dick':'richard','rich':'richard','dave':'david','bernie':'bernard',
 'maggie':'margaret','tommy':'thomas','tom':'thomas','mike':'michael','jim':'james','bob':'robert','bobby':'robert',
 'bill':'william','dan':'daniel','danny':'daniel','ben':'benjamin','steve':'steven','tony':'anthony','rob':'robert',
 'andy':'andrew','drew':'andrew','ted':'edward','pat':'patrick','joe':'joseph','jerry':'gerald','greg':'gregory',
 'ron':'ronald','rick':'richard','jack':'john','jeff':'jeffrey','tim':'timothy','sam':'samuel','nick':'nicholas',
 'matt':'matthew','josh':'joshua','abe':'abraham','gabe':'gabriel','buddy':'earl','doc':'roger','coach':'thomas','chuck':'charles','hank':'henry','liz':'elizabeth','beth':'elizabeth','kate':'katherine','katie':'katherine','hal':'harold','don':'donald'}
C=MC.congestion()
roster_full={fold(m):m for m in C}
roster_full['pablo jose hernandez']=roster_full.get('pablo hernandez rivera','Pablo Hernández Rivera')   # PR commissioner name variant
by_sur=collections.defaultdict(list)
for m in C:
    ts=[t for t in fold(m).split() if t not in ('jr','sr','ii','iii','iv')]
    by_sur[(ts[-1],C[m].get('chamber','').lower())].append(m)
    if len(ts)>=3: by_sur[(' '.join(ts[-2:]),C[m].get('chamber','').lower())].append(m)
MRX=re.compile(r'^(Rep|Sen|Del|Resident Commissioner|Commish|Leader|Whip|Speaker|President Pro Tempore)\.?\s+(.+?)\s*\(([A-Z]{1,4})-([A-Z]{2})(-\d+)?\)')
def resolve(works):
    m=MRX.match(str(works or ''))
    if not m: return None
    base={'Rep':'house','Del':'house','Resident Commissioner':'house','Commish':'house','Sen':'senate'}
    # leadership prefixes carry no chamber -> infer from the district token: (R-LA-1) house, (D-NY) senate
    ch=base.get(m.group(1)) or ('house' if m.group(5) else 'senate')
    nm=re.sub(r'"[^"]*"\s*','',m.group(2))           # strip quoted nicknames
    f=fold(nm)
    if f in roster_full: return roster_full[f]
    toks=f.split()
    if toks and toks[0] in NICKF:
        f2=' '.join([NICKF[toks[0]]]+toks[1:])
        if f2 in roster_full: return roster_full[f2]
    for k in (' '.join(toks[-2:]),toks[-1]) if len(toks)>=2 else (toks[-1],):
        cand=[x for x in by_sur.get((k,ch),[])]
        if len(cand)==1: return cand[0]
        if len(cand)>1 and toks:
            fn=NICKF.get(toks[0],toks[0])
            # reverse-nickname too: the ROSTER may carry the nickname ('Mike Kennedy' vs export 'Michael')
            hit=[x for x in cand if NICKF.get(fold(x).split()[0],fold(x).split()[0])==fn]
            if len(hit)==1: return hit[0]
    return None
STAR=re.compile(r'banking|financial services|credit union|tax (policy|counsel)|econom|monetary|housing|fintech|securities|insurance|treasur|appropriation|budget',re.I)
NOSTAR=re.compile(r'financial administrator|office manager',re.I)
# ---- policy domains (run-4s, user: "it should say their policy domains") ----
# Ground truth = a Quorum 'Issues'/'Issue Areas' column, ingested automatically when the staffer
# exports are re-pulled with it included. Until then: title-derived domains (portfolio words in the
# title) as a labeled fallback. CU-relevant domains drive the star with a portfolio 'why'.
DOMV=[('Banking',r'banking'),('Financial Services',r'financial services'),('Credit Unions',r'credit union'),
 ('Tax',r'\btax'),('Economy',r'econom|monetary'),('Housing',r'housing'),('Fintech',r'fintech'),
 ('Securities',r'securities'),('Insurance',r'insurance'),('Treasury',r'treasur'),
 ('Appropriations',r'appropriation'),('Budget',r'budget'),('Small Business',r'small business'),
 ('Consumer Protection',r'consumer'),('Trade',r'\btrade'),('Defense',r'defense|armed services'),
 ('Energy',r'energy'),('Health',r'health'),('Agriculture',r'agricult'),('Judiciary',r'judiciary'),
 ('Immigration',r'immigration'),('Education',r'education'),('Labor',r'labor|workforce'),
 ('Transportation',r'transport|infrastructure'),('Telecom',r'telecom'),('Veterans',r'veteran'),
 ('Technology',r'technolog|cyber|\bai\b')]
DOMRX=[(lbl,re.compile(p,re.I)) for lbl,p in DOMV]
CUDOM={'Banking','Financial Services','Credit Unions','Tax','Economy','Housing','Fintech','Securities',
 'Insurance','Treasury','Small Business','Consumer Protection'}
def title_domains(t):
    return [lbl for lbl,rx in DOMRX if rx.search(t)][:8]
def validate_staff_rows(staff_rows):
    """Reject incomplete identity rows; a staffer title is required, never fabricated."""
    if not staff_rows:
        raise ValueError('no staff rows loaded; refusing to overwrite the existing directory')
    missing=[str(q) for q,s in staff_rows.items() if not str(s.get('t') or '').strip()]
    if missing:
        raise ValueError('staff directory row(s) missing title: '+', '.join(missing[:20]))
rows={}; unmatched=collections.Counter(); n_qdom=0
for f in FILES:
    ws=openpyxl.load_workbook(f,read_only=True).active
    it=ws.iter_rows(values_only=True); hdr=[str(x) for x in next(it)]
    ix={h:i for i,h in enumerate(hdr)}
    iss_col=next((i for h,i in ix.items() if h.strip().lower() in ('issues','issue areas')),None)
    for r in it:
        q=str(r[ix['Quorum ID']])
        qdom=[]
        if iss_col is not None and r[iss_col] not in (None,'None',''):
            qdom=[x.strip() for x in re.split(r'[;,]',str(r[iss_col])) if x.strip()][:10]
        if q in rows:
            if qdom and not rows[q].get('dom'): rows[q]['dom']=qdom; rows[q]['dsrc']='quorum'; n_qdom+=1
            continue
        works=r[ix['Member Works For']]
        mem=resolve(works)
        if not mem: unmatched[str(works)[:60]]+=1; continue
        t=str(r[ix['Title']] or '').replace('None','').strip()
        dom=qdom or title_domains(t)
        d={'id':q,'m':mem,
           'n':('%s %s'%(r[ix['First Name']] or '',r[ix['Last Name']] or '')).strip(),
           't':t,
           'star':1 if ((STAR.search(t) and not NOSTAR.search(t)) or (set(dom)&CUDOM)) else 0}
        if dom:
            d['dom']=dom; d['dsrc']='quorum' if qdom else 'title'
            if qdom: n_qdom+=1
        rows[q]=d
validate_staff_rows(rows)
out=collections.defaultdict(list)
ORDER={'chief of staff':0,'legislative director':1,'deputy chief of staff':2,'communications director':3,
       'legislative assistant':4,'senior legislative assistant':4,'legislative counsel':4,'tax policy advisor':4,
       'scheduler':7,'legislative correspondent':8,'staff assistant':9}
for s in rows.values():
    d={k:v for k,v in s.items() if k!='m'}
    out[s['m']].append(d)
for m in out:
    out[m].sort(key=lambda s:(-s['star'],ORDER.get(s['t'].split(';')[0].strip().lower(),5),s['n']))
MC.dump_atomic(dict(out),B+r'\data\members\staff_directory.json')
tot=sum(len(v) for v in out.values()); stars=sum(s['star'] for v in out.values() for s in v)
ndom=sum(1 for v in out.values() for s in v if s.get('dom'))
print('STAFF DIRECTORY: %d staffers -> %d members (avg %.1f) | %d industry-starred | %d with policy domains (%d from Quorum Issues col) | %d unmatched office rows'%(
    tot,len(out),tot/max(1,len(out)),stars,ndom,n_qdom,sum(unmatched.values())))
if not n_qdom: print('  (no Issues column in these exports — re-export the staffer searches WITH the Issues field for ground-truth domains; title-derived fallback active)')
if unmatched: print('unmatched offices:',unmatched.most_common(8))
