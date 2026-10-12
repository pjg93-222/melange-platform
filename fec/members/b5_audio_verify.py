# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Caption-based, IDENTITY-AWARE speaker verification (user fix). For each fetched clip, pull the
video's auto-captions and ask Haiku two things: (1) is the TARGET member the solo speaker, and
(2) do the transcript's CONTEXT INDICATORS -- topic, title/committee role, linked bills, personal
background, and especially STATE and DISTRICT -- MATCH / stay NEUTRAL / CONTRADICT the member's
identity card (built from member_desk_bundle.json). Keep a clip only if SOLO_TARGET and identity is
not MISMATCH. This catches BOTH the multi-speaker contamination AND clean-but-wrong-member clips (the
Auchincloss/Texas case). Captions cached (b5_caption_cache.json) so each video is fetched once.
Model = Haiku 4.5 (mechanical classification; Fable is reserved for the per-member narratives).
Usage: python fec/members/b5_audio_verify.py"""
import subprocess, re, json, glob
sys=_s; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
from audio_rules import MIN_MEMBER_TARGET_SEC
B=MB_ROOT; import anthropic
KEY=open(B+r'\fec\anthropic_key.txt').read().strip(); cl=anthropic.Anthropic(api_key=KEY)
ADIR=B+r'\audio_pilot'; OUT=B+r'\data\members\b5_audio_verify.json'; CAPC=B+r'\data\members\b5_caption_cache.json'

# ---- identity cards from the desk bundle (title/state/district/party/committees) ----
_bundle=json.load(open(B+r'\member_desk_bundle.json',encoding='utf-8'))
_recs=_bundle['members'] if isinstance(_bundle,dict) and 'members' in _bundle else _bundle
_recs=_recs if isinstance(_recs,list) else list(_recs.values())
CARD={x['name']:x for x in _recs}
def card_text(member):
    r=CARD.get(member,{})
    if not r: return '%s: a U.S. member of Congress (no card on file).'%member
    title='U.S. Senator' if r.get('chamber')=='Senate' else 'U.S. Representative'
    dist=re.sub(r'\s*\(1\d\dth Congress\),?','',str(r.get('district') or '')).strip()
    coms=r.get('committees') or []
    cstr='; '.join((c.get('name','')+((' ['+c['role']+']') if c.get('role') else '')) for c in coms) or '(none listed)'
    return '%s: %s representing %s (%s). Party: %s. Committees: %s.'%(member,title,r.get('state','?'),dist or '?',r.get('party','?'),cstr)

# ---- caption cache (fetch each video's auto-captions once) ----
_CAP=json.load(open(CAPC,encoding='utf-8')) if _o.path.exists(CAPC) else {}
def caption_text(vid):
    if vid in _CAP: return _CAP[vid]
    tmp=B+r'\_capX'
    subprocess.run([sys.executable,'-m','yt_dlp','--skip-download','--write-auto-subs','--sub-langs','en','--sub-format','vtt',
        '--extractor-args','youtube:player_client=ios,web_safari,android','-o',tmp+'.%(ext)s',
        'https://www.youtube.com/watch?v='+vid],capture_output=True,timeout=90)
    f=glob.glob(tmp+'*.vtt'); txt=''
    if f:
        txt=open(f[0],encoding='utf-8',errors='replace').read()
        for x in f:
            try:_o.remove(x)
            except:pass
        lines=[re.sub(r'<[^>]+>','',l).strip() for l in txt.splitlines() if l and '-->' not in l and not l.startswith(('WEBVTT','Kind','Language'))]
        s=[]
        for l in lines:
            if l and (not s or s[-1]!=l): s.append(l)
        txt=' '.join(s)[:6000]
    _CAP[vid]=txt; return txt

def classify(member,tx):
    prompt=('You are verifying whether an audio clip is really a specific member of Congress speaking solo.\n'
      'IDENTITY CARD: %s\n\n'
      'Auto-captions have NO speaker labels. Judge IDENTITY only from a HARD signal, NEVER from topic.\n'
      'A HARD identity signal is EXPLICIT self-identification, e.g.: the speaker states their own name; '
      '"I represent <place>" or "the gentleman/gentlewoman from <state>"; being recognized or introduced by '
      'state, district, or title; "as <Chair/Ranking Member> of <committee>"; "my bill, the <name> Act"; or '
      'being addressed by name. These are USUALLY present in floor speeches and hearings -- look for one.\n'
      'Talking ABOUT an issue (the southern border, the economy, foreign policy, a national bill) is NUANCE, '
      'NOT identity -- any member may discuss any topic. NEVER infer MATCH or MISMATCH from topic alone.\n\n'
      'Answer on ONE line, two fields separated by a pipe:\n'
      'FIELD 1 (speaker structure): SOLO_TARGET (essentially one person speaks) | MULTI_SPEAKER (several speak '
      'substantially) | TARGET_NOT_SPEAKING (the clip is other people).\n'
      'FIELD 2 (identity, from a HARD signal only): MATCH (a hard signal is consistent with the card) | '
      'MISMATCH (a hard signal CONTRADICTS the card -- recognized as a different state/district, named as a '
      'different person, claims a role the card denies) | NEUTRAL (no hard identity signal present -- topic only). '
      'MATCH and MISMATCH BOTH require a hard signal; if the hard-signal phrase is NONE, FIELD 2 MUST be NEUTRAL.\n'
      'Format EXACTLY: <FIELD1>|<FIELD2>|<the exact hard-signal phrase, or NONE>\n\nTRANSCRIPT:\n%s'%(card_text(member),tx))
    m=cl.messages.create(model='claude-haiku-4-5-20251001',max_tokens=200,messages=[{'role':'user','content':prompt}])
    raw=''.join(b.text for b in m.content if getattr(b,'type','')=='text').strip(); r=raw.upper()
    sp=next((v for v in ('TARGET_NOT_SPEAKING','MULTI_SPEAKER','SOLO_TARGET') if v in r),'?')
    idn=next((v for v in ('MISMATCH','MATCH','NEUTRAL') if v in r),'?')
    ev=raw.split('|')[-1].strip()[:160] if '|' in raw else ''
    return {'speaker':sp,'identity':idn,'evidence':ev,'keep':bool(sp=='SOLO_TARGET' and idn in ('MATCH','NEUTRAL'))}

def _atomic(obj,path,**kw):
    import tempfile
    fd,tmp=tempfile.mkstemp(dir=_o.path.dirname(path) or '.',suffix='.tmp')
    try:
        with _o.fdopen(fd,'w',encoding='utf-8') as fh: json.dump(obj,fh,**kw)
        _o.replace(tmp,path)
    except BaseException:
        try: _o.remove(tmp)
        except OSError: pass
        raise

res=json.load(open(OUT,encoding='utf-8')) if _o.path.exists(OUT) else {}
res={k:v for k,v in res.items() if isinstance(v,dict)}   # drop old flat-string verdicts (schema changed)
mems=[d for d in glob.glob(ADIR+r'\*') if _o.path.isdir(d)]
for md in mems:
    member=_o.path.basename(md)
    for w in glob.glob(md+r'\clip_*.wav'):
        rel=member+'/'+_o.path.basename(w)
        if rel in res: continue
        vid=re.search(r'clip_(.+)\.wav',_o.path.basename(w)).group(1)
        tx=caption_text(vid)
        res[rel]={'speaker':'NO_CAPTIONS','identity':'-','keep':False} if not tx else classify(member,tx)
    # Non-atomic in-place rewrites of the verdict cache AND the expensive caption cache:
    # a kill between truncate and flush lost every cached caption fetch.
    _atomic(res,OUT,indent=0); _atomic(_CAP,CAPC)
# ---- report ----
from collections import Counter
keep=sum(1 for v in res.values() if v.get('keep'))
sp_c=Counter(v['speaker'] for v in res.values())
id_c=Counter(v['identity'] for v in res.values() if v.get('speaker')=='SOLO_TARGET')
print('CLIPS: %d total | speaker structure:'%len(res),dict(sp_c))
print('identity among SOLO clips:',dict(id_c))
print('KEEP (SOLO_TARGET & identity not MISMATCH): %d'%keep)
import wave,contextlib
ok=[]
for md in mems:
    member=_o.path.basename(md); s=0.0
    for w in glob.glob(md+r'\clip_*.wav'):
        if res.get(member+'/'+_o.path.basename(w),{}).get('keep'):
            try:
                with contextlib.closing(wave.open(w,'rb')) as wv: s+=wv.getnframes()/float(wv.getframerate() or 1)
            except:pass
    if s>=MIN_MEMBER_TARGET_SEC: ok.append(member)
print('members with >=%ds VERIFIED-SOLO + identity-OK audio: %d / %d'%(
    int(MIN_MEMBER_TARGET_SEC),len(ok),len(mems)))
