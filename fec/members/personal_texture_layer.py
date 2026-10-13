# -*- coding: utf-8 -*-
"""Build a provenance-first personal-texture layer for every member.

The catch-net scans the member-attributed corpus (floor speeches, hearings,
interviews, original Bluesky posts, Quorum/X/social rows, and press-release
titles) for small autobiographical disclosures: family, tastes, books/media,
food, quotations, inspirations, hobbies, faith, pets, formative experiences,
places, and personal routines. A conservative LLM pass decides whether the
candidate is genuinely about the member; policy rhetoric, third-party
biographies, ceremonial tributes, jokes, and unsupported inference are dropped.

Every retained observation carries the verbatim evidence window plus source,
date, package/post id, and URL when available. The JSON is display/research
context only and never feeds ranks, traits, or engines.

  python fec/members/personal_texture_layer.py --probe
  python fec/members/personal_texture_layer.py --score --go
  python fec/members/personal_texture_layer.py --local       # credit-free explicit/verbatim fallback
  python fec/members/personal_texture_layer.py --rebuild     # cache-only production refresh
"""
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from paths import ROOT as B, DRAFT
from moneyball_core import dump_atomic

import collections, csv, hashlib, io, json, os, re, sqlite3, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

MODEL = 'claude-fable-5'
CACHE = os.path.join(B, 'data', 'members', 'personal_texture_cache_fable.jsonl')
OUT = os.path.join(B, 'data', 'members', 'member_personal_texture.json')
SEMANTIC_CAP = int(os.environ.get('PERSONAL_CATEGORY_CAP', '12'))
LOCAL_CLASSIFIER = 'local_explicit_v6'

# Wide recall is intentional; the semantic pass is the precision gate.
PATTERNS = {
    'family': r"\bmy\s+(?:wife|husband|spouse|partner|mother|mom|father|dad|parents?|daughter|son|children|kids?|brother|sister|siblings?|grandmother|grandfather|grandparents?|granddaughter|grandson|family|families|aunt|uncle|cousin|in-laws?)\b|\b(?:wife|husband|mother|father|parent)\s+of\s+my\b",
    'favorite': r"\b(?:my|one of my|our)\s+favou?rites?\b|\bfavou?rite\s+(?:book|movie|film|show|series|food|meal|restaurant|song|album|musician|artist|author|quote|team|sport|place|holiday|tradition)\b",
    'books_media': r"\b(?:i|we)\s+(?:am|are)\s+(?:reading|watching|listening to)\b|\b(?:i|we)\s+(?:read|reread|watch|watched|listen|listened)\s+(?:to\s+)?(?:the book|a book|the movie|the film|the show|the series|the album|the podcast)\b|\b(?:book|movie|film|tv|television|show|song|album|podcast)\s+(?:i|we)\s+(?:love|like|enjoy|recommend)\b",
    'food': r"\b(?:i|we)\s+(?:love|like|enjoy|cook|bake|eat|drink|order|make)\b.{0,70}\b(?:food|dish|meal|breakfast|lunch|dinner|coffee|tea|beer|wine|pizza|barbecue|bbq|tacos?|chili|dessert|recipe|restaurant)\b|\b(?:my|our)\s+(?:go-to|favorite|favourite)\s+(?:food|dish|meal|restaurant|drink)\b",
    'inspiration': r"\bmy\s+(?:hero|role model|mentor|inspiration)\b|\b(?:i|we)\s+(?:am|are|was|were)\s+inspired by\b|\b(?:inspired|inspires|influenced|mentored|taught)\s+me\b|\bi\s+(?:look|looked)\s+up\s+to\b",
    'quotes_values': r"\b(?:my|a)\s+(?:favorite|favourite)\s+quote\b|\bwords\s+(?:i|we)\s+live\s+by\b|\bmy\s+(?:motto|mantra)\b|\b(?:i|we)\s+always\s+say\b|\b(?:as my|my)\s+(?:mother|mom|father|dad|grandmother|grandfather|coach|teacher|mentor)\s+(?:said|used to say|taught me)\b",
    'hobbies_sports': r"\b(?:my|our)\s+(?:hobby|hobbies|pastime|pastimes)\b|\b(?:in my|in our)\s+(?:spare|free)\s+time\b|\b(?:i|we)\s+(?:love|like|enjoy|grew up)\b.{0,80}\b(?:hunt|hunting|fish|fishing|golf|golfing|run|running|hike|hiking|bike|biking|cycle|cycling|garden|gardening|cook|cooking|bake|baking|paint|painting|play|sports?|baseball|football|basketball|hockey|soccer|tennis|music)\b|\b(?:lifelong|die-hard|avid|huge|big)\s+(?:fan|reader|runner|hunter|fisherman|golfer|hiker|cyclist|gardener)\b",
    'formative': r"\b(?:i|we)\s+(?:grew up|was raised|were raised|spent my childhood|started out|got my first job)\b|\b(?:when i was (?:a child|a kid|young|growing up)|as a child,?\s+i|as a kid,?\s+i|in my childhood|my first job)\b",
    'faith': r"\bmy\s+faith\b|\bi\s+(?:pray|prayed|worship|attend church|go to church)\b|\b(?:my church|my synagogue|my mosque|my temple)\b",
    'pets': r"\b(?:my|our)\s+(?:dog|dogs|cat|cats|pet|pets|horse|horses)\b",
    'places_travel': r"\b(?:my|our)\s+(?:favorite|favourite|special)\s+place\b|\b(?:i|we)\s+(?:love|like|enjoy)\s+(?:visiting|traveling|travelling|going to)\b|\b(?:i|we)\s+(?:vacation|vacationed|honeymooned|travelled|traveled)\b",
    'personal_routine': r"\b(?:every|most)\s+(?:morning|night|weekend)\s+(?:i|we)\b|\b(?:i|we)\s+(?:usually|always|never)\s+(?:start|end|spend|read|watch|listen|cook|walk|run)\b",
}
RX = {k: re.compile(v, re.I | re.S) for k, v in PATTERNS.items()}
# Cheap literal-family gates keep the wide scan fast; the precise regex only runs when its
# vocabulary is present. These are supersets of the corresponding full expressions.
MARKERS = {
 'family':r'family|wife|husband|spouse|partner|mother|mom|father|dad|parent|daughter|son|child|kid|brother|sister|sibling|grand|aunt|uncle|cousin|in-law',
 'favorite':r'favou?rite', 'books_media':r'read|watch|listen|book|movie|film|show|series|album|podcast',
 'food':r'food|dish|meal|breakfast|lunch|dinner|coffee|tea|beer|wine|pizza|barbecue|bbq|taco|chili|dessert|recipe|restaurant|cook|bake',
 'inspiration':r'hero|role model|mentor|inspir|influenc|taught me|look(?:ed)? up to',
 'quotes_values':r'quote|words.{0,80}live by|motto|mantra|always say|used to say|taught me',
 'hobbies_sports':r'hobb|pastime|spare time|free time|hunt|fish|golf|run|hik|bik|cycl|garden|cook|bak|paint|play|sport|baseball|football|basketball|hockey|soccer|tennis|music|fan|reader',
 'formative':r'grew up|raised|learned|remember|never forget|used to|childhood|as a child|as a kid|first job|started out',
 'faith':r'faith|pray|worship|church|synagogue|mosque|temple', 'pets':r'dog|cat|pet|horse',
 'places_travel':r'place|visit|travel|vacation|honeymoon',
 'personal_routine':r'morning|night|weekend|usually|always|never',
}
MARKER_WORDS = {
 'family':('famil','wife','husband','spouse','partner','mother','mom','father','dad','parent','daughter','son','child','kid','brother','sister','sibling','grand','aunt','uncle','cousin','in-law'),
 'favorite':('favorite','favourite'), 'books_media':('read','watch','listen','book','movie','film','show','series','album','podcast','tv','television','song'),
 'food':('food','dish','meal','breakfast','lunch','dinner','coffee','tea','beer','wine','pizza','barbecue','bbq','taco','chili','dessert','recipe','restaurant','cook','bake','drink'),
 'inspiration':('hero','role model','mentor','inspir','influenc','taught me','look up to','looked up to'),
 'quotes_values':('quote','words i live by','words we live by','motto','mantra','always say','used to say','taught me','said'),
 'hobbies_sports':('hobb','pastime','spare time','free time','hunt','fish','golf','run','hik','bik','cycl','garden','cook','bak','paint','play','sport','baseball','football','basketball','hockey','soccer','tennis','music','fan','read'),
 'formative':('grew up','was raised','were raised','learned','remember','never forget','used to','childhood','as a child','as a kid','first job','started out','young','growing up'),
 'faith':(' faith',' pray','worship','church','synagogue','mosque','temple'), 'pets':(' dog',' cat',' pet',' horse'),
 'places_travel':('favorite place','favourite place','special place','visit','travel','vacation','honeymoon','going to'),
 'personal_routine':('morning','night','weekend','usually','always','never'),
}

def norm_space(s): return re.sub(r'\s+', ' ', s or '').strip()

with open(os.path.join(B,'data', 'engine', 'congestion.json'),encoding='utf-8') as _roster_handle:
    ROSTER_NAMES={d['member'] for d in json.load(_roster_handle)}

def claim_fingerprint(claim):
    """Order-insensitive near-duplicate key for short autobiographical claims."""
    number={'one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9','ten':'10'}
    stop={'a','an','the','and','or','i','we','my','our','me','to','of','in','on','at','for','during','those','these'}
    tokens=[]
    for token in re.findall(r'[a-z0-9]+',norm_space(claim).lower()):
        token=number.get(token,token)
        if token.startswith('travel'): token='travel'
        elif token.endswith('ies') and len(token)>4: token=token[:-3]+'y'
        elif token.endswith('s') and len(token)>4: token=token[:-1]
        if token not in stop: tokens.append(token)
    return ' '.join(sorted(set(tokens)))

def attributed_first_person(sent):
    """True when first-person language is introduced as somebody else's quote/story."""
    match=re.search(r'\b(?:i|me|my|mine|myself|we|us|our|ours|ourselves)\b',sent,re.I)
    if not match: return False
    prefix=sent[:match.start()]
    speaker=r'(?:constituent|resident|witness|speaker|guest|caller|jodie|he|she|they|[A-Z][a-z]{2,})'
    attribution=r'(?:said|says|wrote|writes|told|recalled|shared|stated|testified|noted|explained|added|continued)'
    return bool(re.search(speaker+r'.{0,120}\b'+attribution+r'\b',prefix,re.S))

def windows(text):
    """Return category-anchored windows; merge nearby hits to avoid duplicate spend."""
    text = text or ''
    low = text.lower()
    hits = []
    for cat, rx in RX.items():
        if any(tok in low for tok in MARKER_WORDS[cat]):
            hits.extend((m.start(),m.end(),cat) for m in rx.finditer(text))
    hits.sort()
    out = []
    for a, z, cat in hits:
        lo, hi = max(0, a - 420), min(len(text), z + 620)
        if out and lo <= out[-1]['hi'] - 120:
            out[-1]['hi'] = max(out[-1]['hi'], hi); out[-1]['cats'].add(cat)
        else: out.append({'lo': lo, 'hi': hi, 'cats': {cat}})
    return [(norm_space(text[x['lo']:x['hi']]), sorted(x['cats'])) for x in out]

def iter_docs():
    """Yield member-attributed documents from every locally available text channel."""
    con = sqlite3.connect(os.path.join(B, 'social_media.db'), timeout=120)
    for rid, member, date, source, chamber, package, text in con.execute(
            'SELECT rowid,member,date,source,chamber,package,text FROM speeches'):
        yield {'member': member, 'date': date or '', 'source': source, 'locator': package or str(rid),
               'url': package if str(package or '').startswith('http') else '', 'text': text or ''}
    bio2name = {r['bioguide']: r['member'] for r in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json'), encoding='utf-8'))
                if r.get('bioguide')}
    for platform, pid, bio, date, text, repost in con.execute(
            'SELECT platform,post_id,bioguide,created_at,text,repost_of FROM posts'):
        if repost or bio not in bio2name: continue
        yield {'member': bio2name[bio], 'date': date or '', 'source': platform.upper(),
               'locator': pid or '', 'url': '', 'text': text or ''}
    con.close()
    # Quorum contains X plus other captured social channels. Exclude reposts: another person's words.
    qp = os.path.join(B, 'data', 'stance', 'quorum_member_tweets.csv')
    if os.path.exists(qp):
        for r in csv.DictReader(io.open(qp, encoding='utf-8-sig', newline='')):
            if str(r.get('is_rt') or '').lower() in ('1', 'true', 'yes'): continue
            yield {'member': r.get('member') or '', 'date': r.get('dt') or '',
                   'source': (r.get('Document Type') or r.get('Source') or 'SOCIAL').upper(),
                   'locator': r.get('Quorum ID') or '', 'url': r.get('Source URL') or '',
                   'text': r.get('Content') or ''}
    # The local press archive retains titles/URLs, not article bodies. Titles still catch explicit
    # first-person personal releases; the evidence field makes that limitation visible.
    pp = os.path.join(DRAFT, 'all_press_release_index.csv')
    if os.path.exists(pp):
        for r in csv.DictReader(io.open(pp, encoding='utf-8-sig', newline='')):
            yield {'member': r.get('member') or '', 'date': r.get('date') or '', 'source': 'PRESS_TITLE',
                   'locator': r.get('url') or '', 'url': r.get('url') or '', 'text': r.get('title') or ''}

def extract():
    seen = set(); out = []
    for d in iter_docs():
        if not d['member'] or not d['text']: continue
        for w, cats in windows(d['text']):
            k = hashlib.sha256((d['member'] + '|' + w.lower()).encode('utf-8', 'replace')).hexdigest()
            if k in seen: continue
            seen.add(k); out.append({**{x: d[x] for x in ('member','date','source','locator','url')},
                                     'evidence': w, 'hints': cats, 'key': k})
    return out

def select_for_semantic_gate(rows):
    """Deduplicate the semantic workload while preserving member/category/source breadth.

    The catch-net still scans every document and reports the full candidate count. Repeated
    appearances of the same broad family/formative trigger are bounded per member and hint
    family: the newest item from every available source first, then the newest remaining items.
    This keeps the expanded speech corpus from crowding out smaller interview/social channels.
    """
    groups=collections.defaultdict(list)
    for r in rows:
        for h in r['hints']: groups[(r['member'],h)].append(r)
    chosen={}
    pri={'INTERVIEW':0,'TWEET':1,'FACEBOOK POST':2,'BLUESKY':3,'CHRG':4,'CREC':5,'PRESS RELEASE':6,'PRESS_TITLE':7}
    for vals in groups.values():
        vals=sorted(vals,key=lambda r:(r['date'],r['key']),reverse=True)
        pick=[]
        for source in sorted({r['source'] for r in vals}, key=lambda s:(pri.get(s,4),s)):
            r=next(r for r in vals if r['source']==source)
            pick.append(r)
            if len(pick)>=SEMANTIC_CAP: break
        if len(pick)<SEMANTIC_CAP:
            picked={x['key'] for x in pick}
            for r in vals:
                if r['key'] not in picked:
                    pick.append(r);picked.add(r['key'])
                if len(pick)>=SEMANTIC_CAP: break
        for r in pick: chosen[r['key']]=r
    return list(chosen.values())

SYSTEM = """You extract small, source-grounded autobiographical facts about a U.S. member of Congress.
The evidence is member-attributed public text. Keep an item only when the MEMBER reveals something
about themself or their own immediate personal world: family relationship, genuine personal taste,
favorite book/movie/TV/music/food, hobby, sport/team fandom, pet, faith practice, formative experience,
personal routine, place attachment, mentor/hero/inspiration, quotation/motto, or comparable human detail.

Reject policy positions, generic rhetoric, campaign slogans, jokes/sarcasm, chamber formalities,
third-party biographies/tributes/obituaries, constituent stories, and facts merely about somebody else.
Reject a family word unless the relationship is clearly the member's. Never infer an identity, belief,
preference, diagnosis, or relationship beyond what the excerpt states. If context is insufficient, reject.
For accepted items, write one compact factual claim, preserve names/titles only when explicit, and assign
the narrowest category. confidence=high only for an explicit first-person statement; medium for a clear
self-reference stated indirectly. Sensitive-but-public details may be retained, but do not embellish."""
SCHEMA = {'type':'object','properties':{
    'keep':{'type':'boolean'},
    'category':{'type':'string','enum':['family','favorite','books_media','food','inspiration','quotes_values','hobbies_sports','formative','faith','pets','places_travel','personal_routine','other']},
    'claim':{'type':'string'}, 'confidence':{'type':'string','enum':['high','medium','low']},
    'reason':{'type':'string'}},
    'required':['keep','category','claim','confidence','reason'],'additionalProperties':False}

def cache_key(r): return r['key']

def load_cache():
    out = {}
    if os.path.exists(CACHE):
        for line_no,ln in enumerate(io.open(CACHE, encoding='utf-8'),1):
            try:
                d=json.loads(ln); k=d['k']; new=d['out']; old=out.get(k)
                # LLM judgments are immutable first-wins. Deterministic local rules are versioned:
                # a later local_explicit version replaces an earlier local fallback for the same key.
                if old is None or (str((old or {}).get('classifier','')).startswith('local_explicit_') and
                                   str((new or {}).get('classifier','')).startswith('local_explicit_')):
                    out[k]=new
            except (json.JSONDecodeError,KeyError,TypeError) as exc:
                raise RuntimeError('malformed personal-texture cache at line %d'%line_no) from exc
    return out

def local_explicit(r):
    """Credit-free conservative fallback: retain only the exact sentence that fired a self-reference.

    This makes no paraphrase and therefore cannot invent a fact. Confidence stays medium because
    Congressional Record turns can contain material read into the record; the exact evidence remains
    visible for verification. Existing Fable judgments always take precedence in the first-wins cache.
    """
    ev=r['evidence']; matches=[]
    for cat in r['hints']:
        m=RX[cat].search(ev)
        if m: matches.append((m.start(),m.end(),cat))
    if not matches: return {'keep':False,'category':'other','claim':'','confidence':'low','reason':'no explicit self-reference','classifier':LOCAL_CLASSIFIER}
    # Prefer the narrow taste/biographical families over a broad family match in the same window.
    order={'favorite':0,'food':1,'books_media':2,'quotes_values':3,'inspiration':4,'hobbies_sports':5,
           'pets':6,'places_travel':7,'personal_routine':8,'formative':9,'faith':10,'family':11}
    a,z,cat=sorted(matches,key=lambda x:(order.get(x[2],99),x[0]))[0]
    lo=max(ev.rfind('.',0,a),ev.rfind('?',0,a),ev.rfind('!',0,a)); lo=0 if lo<0 else lo+1
    ends=[x for x in (ev.find('.',z),ev.find('?',z),ev.find('!',z)) if x>=0]; hi=min(ends)+1 if ends else min(len(ev),z+260)
    sent=norm_space(ev[lo:hi])
    reject=False
    # Reject attributed quotations whose "my" belongs to someone other than the member.
    if re.search(r'\b(?:said|says|wrote|writes|calls?|recalled)\b.{0,100}\bmy\b',sent,re.I): reject=True
    if attributed_first_person(sent): reject=True
    # Catch a quoted hypothetical whose attribution sits in the preceding sentence/window, such
    # as "that waiter will go: I can finally start a college fund for my kid."  The old sentence-
    # only check retained the quote because the extracted claim began at "I".
    prefix=ev[max(0,lo-260):lo]
    if re.search(r"\b(?:he|she|they|worker|waiter|waitress|constituent|caller|guest|witness)\b"
                 r".{0,180}(?:said|says|wrote|told|will (?:say|go)|going to (?:say|go)|:)\s*$",
                 prefix,re.I|re.S): reject=True
    if re.search(r"\b(?:he|she|they|worker|waiter|waitress|constituent|caller|guest|witness)\b"
                 r".{0,180}(?:said|says|wrote|told|will (?:say|go)|going to (?:say|go)|:)"
                 r".{0,100}\b(?:i|me|my|mine|we|us|our|ours)\b",sent,re.I|re.S): reject=True
    # Memorial/tribute companion anecdotes describe the honored third party, not a stable member
    # preference.  Likewise, an office-holder explicitly assigning a favorite/quote to somebody
    # else is not revealing their own favorite.
    if (re.search(r'\b(?:he|she)\b.{0,140}\b(?:passed|died|taken from us)\b',ev,re.I) and
        re.search(r'\b[A-Z][a-z]+\s+and\s+I\b|\bwe\b',sent)):
        reject=True
    if re.search(r'\b(?:our|the)\s+(?:speaker|leader|chair|president|senator|representative)\b.{0,100}\b(?:favou?rite|quote)\b',sent,re.I):
        reject=True
    # Congressional courtesy frequently uses "my brother/sister" metaphorically for colleagues.
    if cat=='family' and re.search(r'\bmy\s+(?:brother|sister)\b',sent,re.I):
        others=[n for n in ROSTER_NAMES if n!=r['member'] and n.lower() in sent.lower()]
        if others or re.search(r'\bmy friend\b.{0,60}\bmy (?:brother|sister)\b',sent,re.I): reject=True
    # Transcript fragments without a finite/action verb are not usable personal facts.
    if not re.search(r"\b(?:am|is|are|was|were|have|has|had|do|does|did|can|could|will|would|should|"
                     r"love|like|enjoy|live|lived|work|worked|serve|served|meet|met|tell|told|say|said|"
                     r"grow|grew|take|took|get|got|go|went|read|watch|listen|play|played|[a-z]+ed|[a-z]+ing)\b",sent,re.I): reject=True
    if len(sent)<12 or reject:
        return {'keep':False,'category':cat,'claim':'','confidence':'low',
                'reason':'fragment/quotation/metaphorical kinship rejected','classifier':LOCAL_CLASSIFIER}
    if len(sent)>360: sent=sent[:357].rstrip()+'...'
    return {'keep':True,'category':cat,'claim':sent,'confidence':'medium',
            'reason':'explicit first-person phrase; verbatim fallback','classifier':LOCAL_CLASSIFIER}

def write_artifact(rows, cache, selected_count=None):
    by = collections.defaultdict(list); rejected = 0
    for r in rows:
        o = cache.get(cache_key(r))
        stale_local=str((o or {}).get('classifier','')).startswith('local_explicit_') and (o or {}).get('classifier')!=LOCAL_CLASSIFIER
        if not o or not o.get('keep') or o.get('confidence') == 'low' or stale_local:
            if o: rejected += 1
            continue
        classifier=o.get('classifier','fable')
        # Deterministic excerpts stay barred from personality-narrative prompts, but a directly
        # attributed INTERVIEW sentence can be shown as an exact-source personal detail.  Other
        # local sources remain hidden because CREC can contain material read into the record and
        # social rows need their own verified-handle contract.
        narrative_eligible=classifier=='fable'
        display_eligible=(narrative_eligible or
                          (classifier==LOCAL_CLASSIFIER and r['source']=='INTERVIEW'))
        item = {'category':o['category'], 'claim':norm_space(o['claim']), 'confidence':o['confidence'],
                'evidence':r['evidence'], 'source':r['source'], 'date':r['date'],
                'locator':r['locator'], 'url':r['url'], 'classifier':classifier,
                'display_eligible':display_eligible,
                'narrative_eligible':narrative_eligible}
        by[r['member']].append(item)
    # Exact claim duplicates collapse, keeping the strongest/newest sourced instance.
    members = {}
    for member, items in by.items():
        uniq = {}
        for x in sorted(items, key=lambda q: (q['confidence']=='high', q['date']), reverse=True):
            k = claim_fingerprint(x['claim'])
            uniq.setdefault(k, x)
        vals = sorted(uniq.values(), key=lambda q: (q['category'], q['date']), reverse=True)
        members[member] = {'items':vals, 'n_items':len(vals),
                           'categories':dict(collections.Counter(x['category'] for x in vals)),
                           'sources':dict(collections.Counter(x['source'] for x in vals))}
    provenance = {'method':'wide regex catch-net; cached Fable semantic judgments plus conservative verbatim local-explicit fallback',
                  'model':'mixed: %s + %s'%(MODEL,LOCAL_CLASSIFIER),
                  'candidate_count':len(rows), 'classified_count':sum(cache_key(r) in cache for r in rows),
                  'candidate_member_count':len({r['member'] for r in rows}),
                  'semantic_gate_count':selected_count, 'semantic_cap_per_member_category':SEMANTIC_CAP,
                  'display_policy':'Fable-kept rows plus exact INTERVIEW sentences passing local_explicit_v6; local rows remain narrative-ineligible',
                  'rejected_count':rejected, 'member_count':len(members),
                  'source_policy':'member-attributed text only; reposts excluded; evidence and locator retained; display-only'}
    dump_atomic({'members':members,'provenance':provenance}, OUT, ensure_ascii=False, separators=(',',':'))
    print('PERSONAL TEXTURE: %d members | %d retained details | %d candidates | %d classified | %d rejected' %
          (len(members), sum(v['n_items'] for v in members.values()), len(rows), provenance['classified_count'], rejected))

if __name__ == '__main__':
    rows = extract(); cache = load_cache()
    selected = select_for_semantic_gate(rows)
    bysrc=collections.Counter(r['source'] for r in rows); byhint=collections.Counter(h for r in rows for h in r['hints'])
    if '--probe' in sys.argv:
        print('PERSONAL TEXTURE PROBE: %d unique candidates across %d members' % (len(rows), len({r['member'] for r in rows})))
        print('  sources:', dict(bysrc)); print('  hint families:', dict(byhint))
        print('  semantic gate %d (cap=%d/member/category) | cached %d | to classify %d | estimated Fable cost ~$%.2f' %
              (len(selected),SEMANTIC_CAP,sum(r['key'] in cache for r in selected),sum(r['key'] not in cache for r in selected),sum(r['key'] not in cache for r in selected)*.006))
        for r in rows[:8]: print('  [%s/%s] %s | %s' % (r['source'], ','.join(r['hints']), r['member'], r['evidence'][:150]))
        sys.exit(0)
    if '--rebuild' in sys.argv:
        write_artifact(rows, cache, len(selected)); sys.exit(0)
    if '--local' in sys.argv:
        todo=[r for r in selected if cache_key(r) not in cache]
        with io.open(CACHE,'a',encoding='utf-8', newline='\r\n') as cf:
            for r in todo:
                o=local_explicit(r); cache[cache_key(r)]=o
                cf.write(json.dumps({'k':cache_key(r),'out':o},ensure_ascii=True)+'\n')
        print('LOCAL EXPLICIT FALLBACK: classified %d previously uncached semantic-gate windows'%len(todo))
        write_artifact(rows,cache,len(selected));sys.exit(0)
    if '--refresh-local' in sys.argv:
        todo=[r for r in selected if str((cache.get(cache_key(r)) or {}).get('classifier','')).startswith('local_explicit_') and
               (cache.get(cache_key(r)) or {}).get('classifier')!=LOCAL_CLASSIFIER]
        with io.open(CACHE,'a',encoding='utf-8', newline='\r\n') as cf:
            for r in todo:
                o=local_explicit(r);cache[cache_key(r)]=o
                cf.write(json.dumps({'k':cache_key(r),'out':o},ensure_ascii=True)+'\n')
        print('LOCAL EXPLICIT REFRESH v6: reclassified %d deterministic windows'%len(todo))
        write_artifact(rows,cache,len(selected));sys.exit(0)
    if '--score' not in sys.argv:
        print('usage: personal_texture_layer.py [--probe | --score --go | --local | --refresh-local | --rebuild]'); sys.exit(0)
    todo = [r for r in selected if cache_key(r) not in cache]
    print('PERSONAL TEXTURE SCORE: %d candidates -> %d semantic-gate windows | cached %d | to classify %d | est ~$%.2f' %
          (len(rows),len(selected),len(selected)-len(todo),len(todo),len(todo)*.006))
    if todo and '--go' not in sys.argv: print('COST GATE: add --go to spend.'); sys.exit(0)
    if todo:
        if not os.environ.get('ANTHROPIC_API_KEY'):
            kf=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'anthropic_key.txt')
            if os.path.exists(kf): os.environ['ANTHROPIC_API_KEY']=open(kf).read().strip()
        import anthropic, threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        client=anthropic.Anthropic(); lock=threading.Lock(); done=errs=0
        def work(r):
            last=''
            for attempt in range(6):
                try:
                    resp=client.messages.create(model=MODEL,max_tokens=350,
                        output_config={'format':{'type':'json_schema','schema':SCHEMA}},
                        system=[{'type':'text','text':SYSTEM,'cache_control':{'type':'ephemeral'}}],
                        messages=[{'role':'user','content':'MEMBER: %s\nSOURCE HINTS: %s\nEVIDENCE: %s' % (r['member'], ', '.join(r['hints']), r['evidence'][:1800])}])
                    return r,json.loads(next(b.text for b in resp.content if b.type=='text')),None
                except Exception as e:
                    last=str(e)[:140]
                    if 'rate' not in last.lower() and 'overload' not in last.lower(): break
                    time.sleep(min(30,2**attempt))
            return r,None,last
        cf=io.open(CACHE,'a',encoding='utf-8', newline='\r\n')
        with ThreadPoolExecutor(max_workers=int(os.environ.get('PERSONAL_WORKERS','12'))) as ex:
            for f in as_completed([ex.submit(work,r) for r in todo]):
                r,o,e=f.result()
                with lock:
                    done+=1
                    if e or not o: errs+=1
                    else:
                        cache[cache_key(r)]=o
                        cf.write(json.dumps({'k':cache_key(r),'out':o},ensure_ascii=True)+'\n'); cf.flush()
                    if done%250==0 or done==len(todo): print('  %d/%d (%d errors)'%(done,len(todo),errs),flush=True)
        cf.close()
    write_artifact(rows, cache, len(selected))
