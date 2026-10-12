# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Expert-panel packet builder (big5_expert_panel_prereg.md). Builds the multi-rater gold-panel
materials — the ONLY remaining path from A- to A (independent-TRAIT anchor; re-grade 2026-07-09).
Stratified member sample (party x chamber x visibility tertile) -> de-identified rating packets
(interview primary + CREC cross-genre subsample) -> ONE self-contained rater HTML (packets embedded,
blind, randomized per rater, localStorage autosave, JSON export) + SEALED pid->member key.
De-id replicates big5_llm.py deidentify() (names/titles/party/states) + demonyms + district ordinals.
All outputs gitignored (*.json/*.html). Usage: python fec/members/b5_panel_build.py [--n 75] [--xgenre 25]"""
import json, re, sqlite3, hashlib, random
sys=_s; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
from members.b5_panel_items import ITEMS, PROBES, STEM, SCALE, PROVISIONAL_TEXT
import moneyball_core as MC

N_PRIMARY   = next((int(a.split('=')[1]) for a in sys.argv if a.startswith('--n=')),75)
N_XGENRE    = next((int(a.split('=')[1]) for a in sys.argv if a.startswith('--xgenre=')),25)
EXCERPT_W   = 420   # words per excerpt; 2 excerpts/packet ~ 840 words ~ 3-4 min read
MIN_IV_W    = 1600  # eligibility floors (words after ritual-strip)
SEED        = 20260709

# ---- de-identification (replicates big5_llm.py:182-204, which import-executes so can't be imported;
#      spot-checked there 2026-07-04 'names/states/parties/titles scrubbed') + demonyms + districts ----
RITUAL=re.compile(r'\b(i yield (back|to|the floor|myself)[^.]*|mr\.? (president|speaker)|madam (president|speaker)|'
 r'i ask unanimous consent[^.]*|the (gentleman|gentlewoman|gentlelady) from [a-z ]+|'
 r'i (rise|reserve the balance of my time)|pursuant to [^.]*|the clerk will [^.]*|'
 r'i suggest the absence of a quorum|the balance of my time)\b',re.I)
STATES=("alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho "
 "illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi "
 "missouri montana nebraska nevada ohio oklahoma oregon pennsylvania tennessee texas utah vermont virginia "
 "washington wisconsin wyoming").split()+["new hampshire","new jersey","new mexico","new york","north carolina",
 "north dakota","rhode island","south carolina","south dakota","west virginia"]
DEMONYMS=("alabamian alaskan arizonan arkansan californian coloradan connecticuter delawarean floridian georgian "
 "hawaiian idahoan illinoisan hoosier iowan kansan kentuckian louisianian mainer marylander "
 "michigander minnesotan mississippian missourian montanan nebraskan nevadan ohioan oklahoman oregonian "
 "pennsylvanian tennessean texan utahn vermonter virginian washingtonian wisconsinite wyomingite "
 "new yorker new jerseyan new mexican north carolinian south carolinian north dakotan south dakotan "
 "rhode islander west virginian new hampshirite bay stater").split("  ")[0].split() + \
 ["new yorker","new jerseyan","new mexican","north carolinian","south carolinian","north dakotan",
  "south dakotan","rhode islander","west virginian","new hampshirite"]
TITLE_RE=re.compile(r'\b(senator|congressman|congresswoman|representative|rep\.|sen\.|chairman|chairwoman|'
 r'ranking member|speaker|leader|whip)\b',re.I)
PARTY_RE=re.compile(r'\b(republican[s]?|democrat[s]?|democratic|gop)\b',re.I)
DIST_RE=re.compile(r'\b\d{1,2}(st|nd|rd|th)\s+(congressional\s+)?district\b',re.I)
def deidentify(text,member):
    for p in [p for p in re.split(r'\s+',member) if len(p)>2]:
        text=re.sub(re.escape(p),'[NAME]',text,flags=re.I)
    text=TITLE_RE.sub('[TITLE]',text); text=PARTY_RE.sub('[PARTY]',text); text=DIST_RE.sub('[DISTRICT]',text)
    for st in sorted(STATES,key=len,reverse=True): text=re.sub(r'\b'+re.escape(st)+r'\b','[STATE]',text,flags=re.I)
    for d in sorted(set(DEMONYMS),key=len,reverse=True): text=re.sub(r'\b'+re.escape(d)+r's?\b','[STATE]person',text,flags=re.I)
    return text

# ---- corpus pulls ----
def excerpts(con,member,source,k=2,w=EXCERPT_W):
    """k excerpts of ~w words from k different packages (temporal/program spread), ritual-stripped."""
    rows=[(pkg,t) for pkg,t in con.execute("select package,text from speeches where member=? and source=? "
          "order by date,rowid",(member,source)) if t]
    bypkg={}
    for pkg,t in rows:
        t=RITUAL.sub(' ',t); ws=t.split()
        if len(ws)>=30: bypkg.setdefault(pkg or '?',[]).extend(ws)
    pkgs=[p for p in bypkg if len(bypkg[p])>=w]
    if len(pkgs)<1: return None
    rng=random.Random(SEED+hash(member)%9999)
    if len(pkgs)>=k: chosen=[pkgs[0],pkgs[-1]] if k==2 else rng.sample(pkgs,k)   # first+last program = max spread
    else: chosen=pkgs*k
    out=[]
    for i,p in enumerate(chosen[:k]):
        ws=bypkg[p]; start=rng.randrange(0,max(1,len(ws)-w))
        out.append(' '.join(ws[start:start+w]))
    total=sum(len(x.split()) for x in out)
    return ('\n\n[— second excerpt —]\n\n'.join(out),total) if total>=int(0.8*k*w) else None

def build():
    if PROVISIONAL_TEXT:
        print('!! b5_panel_items.PROVISIONAL_TEXT is True — items not yet verified verbatim; build proceeds '
              'but DO NOT hand to raters until verified + prereg locked.')
    con=sqlite3.connect(B+r'\social_media.db'); con.execute('PRAGMA query_only=1')
    iv={m:(n or 0) for m,n in con.execute("select member,sum(nwords) from speeches where source='INTERVIEW' group by member")}
    cr={m:(n or 0) for m,n in con.execute("select member,sum(nwords) from speeches where source='CREC' group by member")}
    prog={m:c for m,c in con.execute("select member,count(distinct package) from speeches where source='INTERVIEW' group by member")}
    BD=json.load(open(B+r'\behavioral_dispositions.json'))['members']; P=MC.party()
    bundle=json.load(open(B+r'\member_desk_bundle.json',encoding='utf-8'))
    recs=bundle['members'] if isinstance(bundle,dict) and 'members' in bundle else bundle
    CH={x['name']:x.get('chamber','') for x in (recs if isinstance(recs,list) else recs.values())}
    elig=[m for m in iv if iv[m]>=MIN_IV_W and prog.get(m,0)>=2 and m in BD and P.get(m) in ('D','R')]
    # visibility tertiles over eligible
    vis=sorted(elig,key=lambda m:BD[m].get('visibility_z',0.0))
    tert={m:(0 if i<len(vis)/3 else 1 if i<2*len(vis)/3 else 2) for i,m in enumerate(vis)}
    strata={}
    for m in elig: strata.setdefault((P[m],CH.get(m,'?'),tert[m]),[]).append(m)
    rng=random.Random(SEED); sample=[]
    quota=max(1,N_PRIMARY//max(1,len(strata)))
    for k in sorted(strata,key=str):
        pool=sorted(strata[k]); rng.shuffle(pool); sample+=pool[:quota]
    # top up to N_PRIMARY from the remainder, preserving stratum diversity
    rest=[m for m in sorted(elig) if m not in sample]; rng.shuffle(rest)
    sample=(sample+rest)[:N_PRIMARY]
    xg=[m for m in sample if cr.get(m,0)>=MIN_IV_W][:N_XGENRE]
    print('sample: %d primary (%d strata), %d cross-genre'%(len(sample),len(strata),len(xg)))
    packets={}; key={}
    def pid(m,g): return hashlib.md5(('panelv1|%s|%s'%(m,g)).encode()).hexdigest()[:10]
    skipped=0
    for m in sample:
        ex=excerpts(con,m,'INTERVIEW')
        if not ex: skipped+=1; continue
        p=pid(m,'IV'); packets[p]={'text':deidentify(ex[0],m),'w':ex[1]}; key[p]={'member':m,'genre':'INTERVIEW'}
        if m in xg:
            ex2=excerpts(con,m,'CREC')
            if ex2:
                p2=pid(m,'CR'); packets[p2]={'text':deidentify(ex2[0],m),'w':ex2[1]}; key[p2]={'member':m,'genre':'CREC'}
    con.close()
    if skipped: print('  skipped %d members (packet extraction short)'%skipped)
    json.dump(key,open(B+r'\data\members\panel_key.json','w'),indent=0)                       # SEALED — raters never see this
    json.dump({'n_packets':len(packets),'n_members':len(set(v['member'] for v in key.values())),
               'strata':{'%s|%s|t%d'%k:len(v) for k,v in strata.items()},'seed':SEED,
               'provisional_items':PROVISIONAL_TEXT},
              open(B+r'\data\members\panel_manifest.json','w'),indent=1)
    html=render_html(packets)
    open(B+r'\expert_panel_rater.html','w',encoding='utf-8').write(html)
    print('wrote data/members/panel_key.json (SEALED), data/members/panel_manifest.json, expert_panel_rater.html '
          '(%d packets, %d members)'%(len(packets),len(set(v['member'] for v in key.values()))))

def render_html(packets):
    items=[{'id':i,'dom':d,'text':t} for i,d,_r,t in ITEMS]
    data=json.dumps({'packets':packets,'items':items,'probes':PROBES,'stem':STEM,'scale':SCALE},ensure_ascii=False)
    return """<!doctype html><html><head><meta charset="utf-8"><title>Personality Rating Panel</title><style>
body{font-family:Segoe UI,Arial,sans-serif;background:#0d1117;color:#dbe2ea;margin:0;padding:20px;max-width:900px;margin:auto}
.card{background:#161b22;border:1px solid #2a2f3a;border-radius:10px;padding:18px;margin:14px 0}
.txt{white-space:pre-wrap;line-height:1.55;font-size:14.5px;background:#0d1117;border:1px solid #2a2f3a;border-radius:8px;padding:14px;max-height:340px;overflow-y:auto}
.item{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:7px 0;border-bottom:1px solid #1b2129;font-size:13.5px}
.opts{display:flex;gap:6px;flex-shrink:0}.opts label{cursor:pointer;font-size:11px;color:#9ca3af;display:flex;flex-direction:column;align-items:center;gap:2px;width:34px;text-align:center}
button{background:#1f6feb;color:#fff;border:0;border-radius:6px;padding:9px 18px;cursor:pointer;font-size:14px}
button:disabled{background:#30363d;cursor:default}.ghost{background:#21262d}
.bar{height:6px;background:#21262d;border-radius:3px;margin:8px 0}.bar i{display:block;height:100%;background:#238636;border-radius:3px}
input[type=text]{background:#0d1117;border:1px solid #2a2f3a;border-radius:6px;color:#dbe2ea;padding:6px 9px;width:280px}
.small{font-size:12px;color:#9ca3af}.warn{color:#e0a020}
</style></head><body>
<h2>Personality Rating Panel</h2>
<div id=setup class=card>
 <p>You will read short, anonymized transcript excerpts of U.S. legislators speaking, and rate each speaker's
 personality. There are no right answers — give your honest impression <b>from the text alone</b>. Names, states,
 parties, and titles are redacted; please do not try to look anyone up.</p>
 <p>Your rater ID (your name or initials): <input type=text id=rid placeholder="e.g. JD"> <button onclick="start()">Start / Resume</button></p>
 <p class=small>Progress saves automatically in this browser. Export your ratings at the end (or any time) with the Export button.</p>
</div>
<div id=main style="display:none">
 <div class=small><span id=prog></span> · <span id=ridshow></span> <button class=ghost style="float:right;padding:4px 10px;font-size:12px" onclick="exportJSON()">Export ratings</button></div>
 <div class=bar><i id=bari style="width:0%"></i></div>
 <div class=card><div class=txt id=ptext></div><div class=small style="margin-top:6px">Word count: <span id=pw></span> · two excerpts from different appearances by the SAME person</div></div>
 <div class=card><b id=stem></b><div id=items></div></div>
 <div class=card><div class=item><span id=recq style="max-width:520px"></span><input type=text id=rec placeholder="(leave blank if no)"></div>
  <div class=item><span id=confq style="max-width:520px"></span><span class=opts id=confopts></span></div></div>
 <div style="display:flex;justify-content:space-between"><button class=ghost onclick="nav(-1)">◀ Previous</button>
  <span class=small id=missing></span><button onclick="nav(1)">Save & Next ▶</button></div>
</div>
<script>
const D=__DATA__;
let RID=null, ORDER=[], IDX=0, R={};
function h32(s){let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0}
function start(){RID=document.getElementById('rid').value.trim(); if(!RID){alert('Enter a rater ID');return}
 const pids=Object.keys(D.packets).sort(); const seed=h32('panel|'+RID); // deterministic per-rater order
 ORDER=pids.map((p,i)=>[ (h32(RID+'|'+p)) , p]).sort((a,b)=>a[0]-b[0]).map(x=>x[1]);
 R=JSON.parse(localStorage.getItem('panelR|'+RID)||'{}');
 IDX=ORDER.findIndex(p=>!done(p)); if(IDX<0)IDX=0;
 document.getElementById('setup').style.display='none';document.getElementById('main').style.display='block';
 document.getElementById('ridshow').textContent='rater: '+RID; render();}
function done(p){const r=R[p];if(!r)return false;return D.items.every(it=>r[it.id]!=null)&&r.CONF!=null}
function render(){const p=ORDER[IDX], pk=D.packets[p];
 document.getElementById('ptext').textContent=pk.text; document.getElementById('pw').textContent=pk.w;
 document.getElementById('stem').textContent=D.stem;
 const r=R[p]||{}; let html='';
 for(const it of D.items){html+='<div class=item><span>'+it.text+'</span><span class=opts>';
  for(let v=1;v<=5;v++){html+='<label><input type=radio name=i_'+it.id+' value='+v+(r[it.id]==v?' checked':'')+
   ' onchange="setv(\\''+it.id+'\\','+v+')"><span>'+v+'</span></label>'}
  html+='</span></div>'}
 document.getElementById('items').innerHTML=html;
 document.getElementById('recq').textContent=D.probes[0][1]; document.getElementById('confq').textContent=D.probes[1][1];
 document.getElementById('rec').value=r.REC||'';
 let ch='';for(let v=1;v<=5;v++){ch+='<label><input type=radio name=conf value='+v+(r.CONF==v?' checked':'')+
  ' onchange="setv(\\'CONF\\','+v+')"><span>'+v+'</span></label>'}
 document.getElementById('confopts').innerHTML=ch;
 const nd=ORDER.filter(done).length;
 document.getElementById('prog').textContent='Packet '+(IDX+1)+' of '+ORDER.length+' — '+nd+' complete';
 document.getElementById('bari').style.width=(100*nd/ORDER.length)+'%';
 document.getElementById('missing').textContent=done(p)?'✓ complete':'';
 window.scrollTo(0,0);}
function setv(k,v){const p=ORDER[IDX]; R[p]=R[p]||{}; R[p][k]=v; save(); document.getElementById('missing').textContent=done(p)?'✓ complete':''}
function save(){const p=ORDER[IDX]; R[p]=R[p]||{}; R[p].REC=document.getElementById('rec').value.trim();
 localStorage.setItem('panelR|'+RID,JSON.stringify(R))}
function nav(d){save(); IDX=Math.min(ORDER.length-1,Math.max(0,IDX+d)); render()}
function exportJSON(){save(); const out={rater:RID,exported:new Date().toISOString(),
 n_complete:ORDER.filter(done).length,n_total:ORDER.length,ratings:R};
 const b=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='panel_ratings_'+RID+'.json';a.click()}
</script></body></html>""".replace('__DATA__',data)

if __name__=='__main__':
    build()
