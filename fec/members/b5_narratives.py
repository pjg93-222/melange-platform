# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Legacy paid 3-paragraph narrative generator (explicit opt-in only).

The active product path is b5_narratives_local.py. This retained paid route
requires --allow-paid-api, keeps intrinsic E unavailable, and admits audio only
from the double-reviewed b5_audio_descriptors_v2.json artifact. It is never run
by the weekly refresh.

For each member,
assembles a DE-IDENTIFIED measured profile (qualified Blue O/C/low-antagonism A/affective-content N +
conversation-roles v2 + persona + clean-audio vocal descriptors + sourced personal texture) and has
the configured paid model (`claude-fable-5` by default) write three tight paragraphs:
  P1  Overall personality (succinct)
  P2  Individual quirks, manner of speech, landmines (interruptions/floor-dynamics + vocal + diction)
  P3  Persuasive approach (personality-matched, per the persuasion literature)
De-identified inputs (no name/party/state) so the model describes the MEASURED profile, not its prior
knowledge of the politician (no confabulation). Paid prose generation is used ONLY here;
everything mechanical is Haiku. Writes member_narratives.json {name:{p1,p2,p3,inputs,...}}. Resumable
(skips members already generated). Usage: python fec/members/b5_narratives.py [--limit N] [--only "Name1,Name2"]
  [--refresh] [--out PATH]
DISPLAY-ONLY: does not touch behavioral dispositions, persona, the engine, or ranks."""
import json, re, hashlib, threading
sys=_s; sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
_COORD_SCRIPTS=_o.path.join(B,'.claude','skills','codex-executor','scripts')
if _COORD_SCRIPTS not in sys.path: sys.path.insert(0,_COORD_SCRIPTS)
from coordination_ledger import CoordinationLedger, LedgerEventHandler
from fable_sol_adapters import (
    CodexExecSolClient, JsonProcessSolClient, command_from_json,
)
from fable_sol_retry import FableResult, ReviewFlag, run_until_clear_sync
from scipy.stats import norm
import bisect
import anthropic
cl=None
L=lambda f: json.load(open(B+'\\'+f,encoding='utf-8'))
V2=(L('data/members/big5_blue_validated.json').get('traits') or {})
BD=(L('behavioral_dispositions.json').get('members') or {})
# Cooperation descriptor = share-based cooperation_v2 (audit 2026-07-10 S3-04, matching the
# promoted engine A-slot + desk radar @94f09cf): overlay v2 into the narrative view; members
# without v2 coverage get None -> the descriptor line is omitted (line() returns None), never a
# silent fallback to the volume-entangled raw-count read.
try:
    _cv2={m:(d or {}).get('cooperation_v2_z') for m,d in L('coop_v2_candidate.json').get('members',{}).items()}
    BD={m:{**d,'cooperation_z':_cv2.get(m)} for m,d in BD.items()}
except FileNotFoundError as _cv2_exc:
    raise FileNotFoundError('coop_v2_candidate.json is missing; the cooperation descriptor must not fall back to the volume-entangled raw-count cooperation_z') from _cv2_exc
try: R2=(L('data/members/behavioral_roles_v2_candidate.json').get('members') or {})
except FileNotFoundError: R2={}
for _m,_d in BD.items():
    _r=R2.get(_m) or {}
    _d['dominance_z']=_r.get('dominance_v2')
IF=L('data/lobbying/interaction_features.json'); IF=IF.get('members',IF) if isinstance(IF,dict) else {}
RC=L('member_receptivity.json'); RC=RC.get('members',RC) if isinstance(RC,dict) else {}
def pctmap(key):
    vals=sorted(float(d[key]) for d in RC.values() if d.get(key) is not None); den=max(1,len(vals)-1)
    return {m:round(100*bisect.bisect_left(vals,float(d[key]))/den) for m,d in RC.items() if d.get(key) is not None}
AGPCT=pctmap('agency'); COMPCT=pctmap('communion')
try:
    _AUDOC=L('data/members/b5_audio_descriptors_v2.json')
    if _AUDOC.get('schema')!='b5-audio-descriptors-v2' or _AUDOC.get('status')!='ACTIVE_FAIL_CLOSED':
        raise ValueError('audio v2 is not active/fail-closed')
    AZ=_AUDOC.get('members') or {}
except (FileNotFoundError, ValueError):
    AZ={}
try: PT=(L('data/members/member_personal_texture.json').get('members') or {})
except FileNotFoundError: PT={}
DEFAULT_OUT=B+r'\data\members\member_narratives.json'
PROMPT_VERSION='fable-sol-bounded-v5-guarded-2026-07-24'
BASIS_VERSION='guarded-blue-no-E+roles-v2+audio-v2+personal-texture'
PRIMARY_MODEL=_o.environ.get('NARRATIVE_PRIMARY_MODEL','claude-fable-5')
_SOL_LOCK=threading.Lock()

def _ord(n): return '%d%s'%(n,'th' if 10<=n%100<=20 else {1:'st',2:'nd',3:'rd'}.get(n%10,'th'))
def band(z):
    if z is None: return None
    p=norm.cdf(float(z))*100
    lab='very high' if p>=90 else 'high' if p>=70 else 'average' if p>=30 else 'low' if p>=10 else 'very low'
    return '%s (%s pct)'%(lab,_ord(round(p)))
def line(lbl,mean,z):
    b=band(z);  return ('  - %s — %s: %s'%(lbl,mean,b)) if b else None

TRAITS=[('Agreeableness','validated low-antagonism public-language facet vs. more confrontational language','A'),
        ('Conscientiousness','disciplined, detail-oriented, reliable vs. flexible, improvisational','C'),
        ('Openness','curious, idea-driven, novelty-seeking vs. practical, conventional','O'),
        ('Affective-content Neuroticism','more negative-affect/stress language vs. less','N')]
BEH=[('Cooperation','reaches across the aisle','cooperation_z'),
     ('Visibility','high public/media profile','visibility_z'),
     ('Breadth','engages a wide range of issues','breadth_z'),
     ('Diligence','legislative follow-through','diligence_z')]
# conversational-dynamics descriptors (z-within-program); (label, low-pole phrase, high-pole phrase)
INTER=[('wpt_mean','speaks in short, clipped turns','holds forth in long, uninterrupted turns'),
       ('gini','shares the floor evenly','dominates the floor / holds forth'),
       ('cutoff','rarely cut off','often cut off or trails off mid-thought in contested exchanges'),
       ('burst','avoids rapid crosstalk','thrives in rapid, contested back-and-forth'),
       ('defense','rarely defends the floor','frequently deploys floor-defense moves ("let me finish")'),
       ('discmark','clean, spare diction','peppers speech with fillers ("you know", "I mean")'),
       ('agree','slow to concede a point','quick to voice agreement ("right", "exactly")')]
def guarded_audio_lines(audio):
    """Translate only v2 cross-source delivery measurements; never proxy pace or character."""
    metrics=(audio or {}).get('metrics') or {}; out=[]
    pace=metrics.get('delivery_rate_wpm') or {}; lo=pace.get('min'); hi=pace.get('max')
    if lo is not None and hi is not None:
        out.append('reviewed delivery rate range: %.0f to %.0f transcript words per elapsed minute'%(lo,hi))
    for key,label in (('pitch_variability_raw','pitch variation'),('volume_dynamics_raw','within-recording volume dynamics')):
        band=(metrics.get(key) or {}).get('stable_outer_quartile')
        if band=='high_across_sources': out.append(label+' is consistently in the upper quartile across sources')
        elif band=='low_across_sources': out.append(label+' is consistently in the lower quartile across sources')
    return out
PERSONAL_PRIORITY={'favorite':0,'books_media':1,'food':2,'hobbies_sports':3,'pets':4,
                   'inspiration':5,'quotes_values':6,'faith':7,'family':8,'formative':9,
                   'places_travel':10,'personal_routine':11,'other':12}

def phrases(items, getz, th=0.6):
    out=[]
    for key,lo,hi in items:
        z=getz(key)
        if z is None: continue
        if z>=th: out.append(hi)
        elif z<=-th: out.append(lo)
    return out

def profile(name):
    v=V2.get(name,{}); bd=BD.get(name,{}); rc=RC.get(name,{}); az=AZ.get(name); r2=R2.get(name,{})
    P=['MEASURED PROFILE OF AN ANONYMOUS U.S. LEGISLATOR (describe ONLY what these measures support):','','QUALIFIED MULTIMETHOD BLUE READS (intrinsic E unavailable):']
    for lbl,mean,k in TRAITS:
        ln=line(lbl,mean,v.get(k));  P.append(ln) if ln else None
    P+=['  - Extraversion — unavailable as an intrinsic trait; public exposure is separate']
    for lbl,mean,k in BEH:
        ln=line(lbl,mean,bd.get(k));  P.append(ln) if ln else None
    ln=line('Conversational dominance','holds and defends the floor vs. shares it evenly',r2.get('dominance_v2'))
    P.append(ln) if ln else None
    ln=line('Verbal expressiveness','elaborates in longer turns vs. speaks tersely',r2.get('expressiveness_v2'))
    P.append(ln) if ln else None
    # persona / receptivity
    sr=rc.get('susc_read'); ag=AGPCT.get(name); co=COMPCT.get(name)
    per=[]
    if ag is not None and co is not None: per.append('interpersonal stance: agency %d%% / communion %d%%'%(round(ag),round(co)))
    if sr and sr!='both-neutral': per.append('susceptibility read: %s'%sr.replace('-',' '))
    if per: P+=['','PERSONA:']+['  - '+x for x in per]
    # conversational dynamics
    ip=phrases(INTER, lambda k: (IF.get(name) or {}).get(k))
    if ip: P+=['','MANNER OF SPEECH (hearing/interview conversational dynamics):']+['  - '+x for x in ip]
    # vocal delivery: active double-review v2 only
    if az:
        vp=guarded_audio_lines(az)
        if vp: P+=['','VOICE (double-reviewed target-only audio):']+['  - '+x for x in vp]
    # Provenance-retained personal facts are allowed only as grounded rapport/context cues. Prefer
    # favorites and interests, then values/family; never ask the prose model to infer a trait from them.
    personal=sorted([x for x in ((PT.get(name) or {}).get('items') or []) if x.get('narrative_eligible') is True],
                    key=lambda x:(PERSONAL_PRIORITY.get(x.get('category','other'),99),
                                  0 if x.get('confidence')=='high' else 1,
                                  x.get('date') or ''))[:3]
    if personal:
        P+=['','PUBLIC PERSONAL DETAILS (source-grounded; use only if naturally relevant, never overinterpret):']
        for x in personal:
            claim=re.sub(r'\s+',' ',x.get('claim') or '').strip()
            if claim: P.append('  - [%s; %s] %s'%(x.get('category','other'),x.get('source','public'),claim[:300]))
    has={'audio':bool(az),'interaction':bool(r2),'v2':bool(v),'behavioral':bool(bd),'personal':len(personal)}
    return '\n'.join(x for x in P if x is not None), has

CACHED_INSTRUCTIONS="""You write confidential, evidence-bounded communication-preparation briefs for a
legislative-affairs team. The purpose is respectful, effective professional communication: selecting
the right meeting format, level of detail, tone, pacing, and rapport cues. You receive only a
de-identified measured profile of one legislator. Describe only what the supplied measures and
source-grounded details support. Never guess the person's identity. Never invent biography, party,
state, district, positions, votes, relationships, motives, diagnoses, private facts, or events.

MEASUREMENT RULES
1. Intrinsic Extraversion is unavailable. The operational product cue is role-adjusted public exposure,
not a private-trait claim. Do not infer intrinsic E from visibility, turn length, warmth, excitement,
activity, gregariousness, dominance, or persona. Do not collapse Openness into Agreeableness,
cooperation into Agreeableness, formal leadership into conversational dominance, or stress language
into hostility.
2. The qualified Agreeableness read is specifically a low-antagonism public-language facet: higher
means less hostile or contemptuous interview language. It is not the same as bipartisan cooperation. A person can
cooperate strategically while communicating abrasively, or communicate pleasantly without crossing
party lines. Preserve that distinction.
3. The qualified Neuroticism read is affective-content in public text. It is not a diagnosis, private
emotional state, or direct composure/reactivity measure. Do not infer anxiety, instability, mental
illness, trauma, private emotional vulnerability, or communication preferences from it.
4. Interview visibility is exposure/activity, not Extraversion. Conversational dominance is observed
floor-holding/defense and excludes formal leadership. Verbal expressiveness is turn-level elaboration;
it is a speaking-style role, not independent Extraversion validation. Low visibility does not by
itself establish a preference for privacy, small meetings, written communication, or avoiding media.
Do not recommend a meeting format from intrinsic Extraversion because no intrinsic E value is supplied.
Agency/communion are projected persona cues, not observed turn behavior:
do not infer dominance, interruption style, warmth, or meeting format from persona alone.
5. Voice cues come from verified solo audio. Use them only for delivery observations—pitch level or
variation, loudness, pace, resonance, and emphatic dynamics. Do not infer sincerity, competence,
warmth, health, deception, ethnicity, age, gender, or mood from acoustics. A high-pitched or dynamic
voice is not automatically excited; describe the audible style, then connect it to meeting pacing only.
6. Public personal details are source-grounded rapport/context cues. Use at most one when it naturally
improves the brief. Do not infer personality from a favorite book, family reference, faith statement,
food, pet, hobby, quotation, or inspiration. Never fabricate a detail and never turn family into
pressure or leverage.
7. Missing evidence is missing. Do not fill a gap with a generic stereotype. If modalities conflict,
state the practical implication without pretending the conflict is resolved.

WRITING RULES
- Write exactly three paragraphs, each beginning with its required tag on its own line: [P1], [P2],
  and [P3]. Output no title, preamble, bullets, caveat section, or text outside those paragraphs.
- Voice: crisp, concrete, professional—a sharp staffer briefing a principal, not a horoscope, clinical
  assessment, campaign attack, or personality-test report.
- Use gender-neutral references throughout: "this member," "the member," or singular "they." The
  anonymous evidence packet does not supply gender, even when a personal detail appears suggestive.
- Keep the complete three-paragraph brief between 330 and 390 words. Prefer omission over padding.
- Do not repeat raw z-scores or percentiles. Translate only the strongest supplied signals into
  observable expectations and practical preparation.
- Prefer calibrated wording: "likely," "tends to," "public style," or "in observed exchanges" when
  the evidence is behavioral. Avoid absolutes such as "always," "never," "secretly," or "truly."
- Do not call someone weak, unstable, neurotic, hostile, cold, unintelligent, irrational, gullible,
  manipulable, or easy to pressure. Describe behavior and communication fit.
- Do not advise deception, coercion, intimidation, exploitation, emotional pressure, manufactured
  urgency, or using personal/family facts as leverage.
- Make every sentence earn its place. Do not restate the same trait in multiple paragraphs.

PARAGRAPH SPECIFICATION
[P1] OVERALL PERSONALITY — 3 to 4 sentences. Synthesize the qualified O/C, low-antagonism A,
and affective-content N dimensions with the behavioral record into a compact public-working-style portrait. Emphasize the two or three
strongest, mutually distinct signals. Reconcile apparent contrasts when useful—for example, high
visibility with low conversational expressiveness, or cooperation with low-antagonism disagreement.

[P2] QUIRKS, MANNER OF SPEECH & LANDMINES — 4 to 5 sentences. Describe only supported speaking and
interaction patterns: pace, pitch range, loudness dynamics, floor-sharing, defense moves, turn length,
fillers, agreement markers, and observed pressure style. "Landmines" means avoidable communication
friction such as interrupting a deliberate speaker, bringing an unsourced ask to a detail-oriented
member, overloading a narrow-issue specialist, or forcing a public decision on someone whose profile
supports private preparation. Do not invent triggers. If the audio and interaction evidence is thin,
say less rather than filling space.

[P3] SUGGESTED APPROACH — 4 to 5 sentences with two or three concrete, ethical meeting tactics.
Match format and framing to the measured profile: high Conscientiousness favors sourced detail,
clear implementation, and follow-through; low Conscientiousness favors a concise framework and room
to adapt. Intrinsic Extraversion is unavailable, so do not choose live versus written, large versus small, or
fast versus reflective meeting formats from a personality assumption. High Openness can support a new
model or long horizon; low Openness favors precedent, operational proof, and concrete near-term
results. Low-antagonism A may guide tone but must stay distinct from cooperation. Affective-content N
may be described only as public-language subject matter and must not dictate a persuasion tactic.
High conversational dominance calls for
clear outcomes and room to lead the discussion; low dominance calls for direct questions and protected
time to answer. A source-grounded personal detail may appear once as a natural rapport cue, never as
leverage. End with a practical recommendation, not a slogan.
"""
def prompt(prof):
    return prof+'\n\nReturn only [P1], [P2], and [P3] paragraphs under the cached rubric.'

def parse(raw):
    parts={}
    for tag in ('P1','P2','P3'):
        m=re.search(r'\[%s\][:\-\s]*(.+?)(?=\[P[123]\]|$)'%tag, raw, re.S)
        if m: parts[tag.lower()]=re.sub(r'\s+',' ',m.group(1)).strip()
    # Some otherwise valid completions omit the literal tags despite the format instruction. Accept
    # exactly three blank-line-separated paragraphs, but never guess boundaries for any other shape.
    if len(parts)<3:
        paras=[re.sub(r'^\s*(?:\*\*)?\[?P[123]\]?(?:\*\*)?\s*[:\-]?\s*','',p.strip(),flags=re.I)
               for p in re.split(r'\n\s*\n',raw.strip()) if p.strip()]
        if len(paras)==3 and all(paras): parts={'p1':re.sub(r'\s+',' ',paras[0]),
                                                'p2':re.sub(r'\s+',' ',paras[1]),
                                                'p3':re.sub(r'\s+',' ',paras[2])}
    return parts

def scrub(text):
    """Mechanical style cleanup only; never adds a substantive claim."""
    text=re.sub(r'\bshe or he\b','they',text,flags=re.I)
    text=re.sub(r'\bhim or her\b','them',text,flags=re.I)
    def pct(m):
        n=int(m.group(1)); band='very low' if n<10 else 'low' if n<30 else 'average' if n<70 else 'high' if n<90 else 'very high'
        return band if m.group(3).startswith('-') else band+' range'
    text=re.sub(r'\b(\d{1,3})(st|nd|rd|th)(-percentile| percentile)\b',pct,text,flags=re.I)
    text=re.sub(r'\bz-score\b','measured score',text,flags=re.I)
    return re.sub(r'\s+',' ',text).strip()

def _txt(m): return ''.join(b.text for b in m.content if getattr(b,'type','')=='text').strip()
def _call_text(user_prompt,model,mx=2400):
    kw={'model':model,'max_tokens':mx,
        'system':[{'type':'text','text':CACHED_INSTRUCTIONS,'cache_control':{'type':'ephemeral'}}],
        'messages':[{'role':'user','content':[{'type':'text','text':user_prompt}]}]}
    # This is a bounded evidence-to-prose transformation, not an open-ended reasoning task.
    # Keep the legacy Sonnet override available when a user explicitly selects that model.
    if model=='claude-sonnet-5': kw['thinking']={'type':'disabled'}
    return cl.messages.create(**kw)
def _usage(m):
    u=getattr(m,'usage',None)
    return {k:int(getattr(u,k,0) or 0) for k in
            ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens')}

class _NarrativeFable:
    """Paid Fable generation plus deterministic completion review."""
    def __init__(self):
        self.usages=[]
    def run(self,current_prompt,*,attempt):
        m=_call_text(current_prompt,PRIMARY_MODEL)
        raw=_txt(m); self.usages.append(_usage(m))
        flags=[]
        if getattr(m,'stop_reason','')=='refusal':
            flags.append(ReviewFlag('REFUSAL','Fable declined the narrative request.','error'))
        if getattr(m,'stop_reason','')=='max_tokens':
            flags.append(ReviewFlag('TRUNCATED','Generation stopped at max_tokens; the brief is cut off.','error'))
        parsed=parse(raw)
        missing=[key.upper() for key in ('p1','p2','p3') if not parsed.get(key)]
        if missing:
            flags.append(ReviewFlag('FORMAT','Missing required sections: '+', '.join(missing),'error'))
        return FableResult(
            output=raw,
            reasoning_backend=str(getattr(m,'model',PRIMARY_MODEL)),
            flags=tuple(flags),
            review_completed=True,
        )

class _NarrativeSol:
    """Serialize rare Sol rewrites so fallback pressure cannot fan out."""
    def __init__(self):
        self.command=_o.environ.get('NARRATIVE_SOL_COMMAND_JSON')
        self.timeout=float(_o.environ.get('NARRATIVE_SOL_TIMEOUT_SECONDS','900'))
    def rewrite(self,request):
        with _SOL_LOCK:
            if self.command:
                client=JsonProcessSolClient(
                    command_from_json(self.command,label='NARRATIVE_SOL_COMMAND_JSON'),
                    timeout_seconds=self.timeout,
                )
            else:
                client=CodexExecSolClient(
                    executable=_o.environ.get('NARRATIVE_CODEX_EXECUTABLE'),
                    working_directory=B,
                    timeout_seconds=self.timeout,
                )
            return client.rewrite(request)

def _summed_usage(usages):
    keys=('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens')
    return {key:sum(item.get(key,0) for item in usages) for key in keys}

def gen(name):
    prof,has=profile(name)
    input_hash=hashlib.sha256(prof.encode('utf-8')).hexdigest()
    signal_n=sum(1 for x in prof.splitlines() if x.startswith('  - ') and 'unavailable as an intrinsic trait' not in x)
    if signal_n<2:
        return {'p1':'The current evidence base is too thin to support a defensible personality or working-style portrait for this member. No validated Big-Five, observed conversation, verified voice, or source-grounded personal-texture evidence is available, so the profile remains intentionally unfilled rather than inferred.',
                'p2':'There is not enough observed material to characterize pace, vocal delivery, floor-sharing, verbal expressiveness, or communication friction. Treat every such dimension as unknown and record what is observed in the next substantive interaction instead of relying on a roster stereotype.',
                'p3':'Use the neutral default: send a concise, sourced brief in advance, state one concrete ask, leave time for questions, and avoid assumptions about preferred tone or meeting format. Update the approach only when member-attributed evidence enters the validated profile.',
                'has':has,'model':'insufficient-evidence-rule','prompt_version':PROMPT_VERSION,
                'basis_version':BASIS_VERSION,'input_sha256':input_hash,
                'measurement_note':'Current evidence insufficient; neutral non-inference brief shown.',
                'usage':{'input_tokens':0,'output_tokens':0,'cache_creation_input_tokens':0,'cache_read_input_tokens':0},
                'raw_unparsed':None,'ok':True}
    fable=_NarrativeFable()
    ledger_path=_o.environ.get(
        'NARRATIVE_COORDINATION_LEDGER',
        str(_o.path.join(_o.path.expanduser('~'),'.claude','state','codex-executor-ledger.sqlite3')),
    )
    routed=run_until_clear_sync(
        prompt(prof),
        fable=fable,
        sol=_NarrativeSol(),
        max_attempts=int(_o.environ.get('NARRATIVE_MAX_ATTEMPTS','5')),
        on_event=LedgerEventHandler(
            CoordinationLedger(ledger_path),
            run_id='narrative-'+input_hash[:16],
            task_slug='narrative-'+re.sub(r'[^a-z0-9]+','-',name.casefold()).strip('-'),
        ),
    )
    raw=routed.output
    usage=_summed_usage(fable.usages)
    accepted_backend=routed.attempts[-1].reasoning_backend
    model=accepted_backend+('+sol-rewrite-%d'%routed.sol_rewrites if routed.sol_rewrites else '')
    p=parse(raw)
    p={k:scrub(v) for k,v in p.items()}
    ok=bool(p.get('p1') and p.get('p2') and p.get('p3'))
    return {'p1':p.get('p1',''),'p2':p.get('p2',''),'p3':p.get('p3',''),'has':has,'model':model,
            'prompt_version':PROMPT_VERSION,'basis_version':BASIS_VERSION,
            'input_sha256':input_hash,
            'measurement_note':'Current validated Blue + conversation roles v2 + verified voice + sourced personal texture.',
            'usage':usage,'raw_unparsed':raw if not ok else None,'ok':ok}

def dump_atomic(obj,path):
    tmp=path+'.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=0)
    _o.replace(tmp,path)

if __name__=='__main__':
    args=sys.argv[1:]
    if '--allow-paid-api' not in args:
        raise SystemExit('paid narrative route disabled; use b5_narratives_local.py after explicit user approval, or pass --allow-paid-api')
    cl=anthropic.Anthropic(api_key=open(B+r'\fec\anthropic_key.txt').read().strip())
    def argval(flag,default=None):
        eq=next((a.split('=',1)[1] for a in args if a.startswith(flag+'=')),None)
        if eq is not None: return eq
        return args[args.index(flag)+1] if flag in args and args.index(flag)+1<len(args) else default
    outarg=argval('--out')
    PRIMARY_MODEL=argval('--primary',PRIMARY_MODEL)
    OUT=DEFAULT_OUT if not outarg else (outarg if _o.path.isabs(outarg) else _o.path.join(B,outarg))
    _o.makedirs(_o.path.dirname(OUT),exist_ok=True)
    res={} if '--refresh' in args else (json.load(open(OUT,encoding='utf-8')) if _o.path.exists(OUT) else {})
    for _v in res.values():
        for _k in ('p1','p2','p3'):
            if _v.get(_k): _v[_k]=scrub(_v[_k])
    roster=sorted(set(BD)|set(V2))   # union: behavioral record OR text instrument (the old `m in BD or ...` over BD keys was a tautology that silently excluded V2-only members)
    only=argval('--only')
    if only: roster=[m.strip() for m in only.split(',')]
    lim=int(argval('--limit')) if argval('--limit') else None
    todo=[m for m in roster if m not in res or not res[m].get('ok') or
          res[m].get('prompt_version')!=PROMPT_VERSION or res[m].get('basis_version')!=BASIS_VERSION]
    if lim: todo=todo[:lim]
    workers=int(argval('--workers',6))
    print('narratives: %d target, %d to generate (%s primary; cached rubric; bounded Sol revision), %d workers -> %s'%
          (len(roster),len(todo),PRIMARY_MODEL,workers,OUT))
    from concurrent.futures import ThreadPoolExecutor, as_completed
    lock=threading.Lock(); n=[0]
    def work(name):
        try: r=gen(name); r['name']=name; return name,r
        except Exception as e: return name,{'err':str(e)[:90],'ok':False}
    def record(name,r):
        with lock:
            res[name]=r; n[0]+=1
            tag='ok(%s)'%r.get('model','?') if r.get('ok') else ('ERR '+r['err'] if r.get('err') else 'PARSE-FAIL')
            print('  [%d/%d] %-24s %s'%(n[0],len(todo),name[:24],tag))
            if n[0]%10==0 or n[0]==len(todo): dump_atomic(res,OUT)
    # Warm the static-rubric cache once before parallel fan-out. Without this, the first worker wave
    # all creates the same cache entry and pays the full input-token cost concurrently.
    rest=todo
    if todo and workers>1:
        name,r=work(todo[0]); record(name,r); rest=todo[1:]
    with ThreadPoolExecutor(max_workers=max(1,workers)) as ex:
        futs=[ex.submit(work,nm) for nm in rest]
        for f in as_completed(futs):
            name,r=f.result(); record(name,r)
    dump_atomic(res,OUT)
    done=sum(1 for v in res.values() if v.get('ok'))
    fb=sum(1 for v in res.values() if '+sol-rewrite-' in v.get('model',''))
    cr=sum((v.get('usage') or {}).get('cache_read_input_tokens',0) for v in res.values())
    cc=sum((v.get('usage') or {}).get('cache_creation_input_tokens',0) for v in res.values())
    print('%s: %d generated (%d total); %d needed Sol revision; cache create/read tokens %d/%d'%
          (_o.path.basename(OUT),done,len(res),fb,cc,cr))
