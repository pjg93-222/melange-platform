# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root
"""PRODUCTION API STANCE LAYER (user-ratified 2026-07-04: "layer the API along with the DEBATE...
Run that and Tweet, lets get that data").

Design measured on the corrected golden set (goldeval n=217 hard slice + curated test n=20):
  * DEBATE stays the production classifier — it WINS on curated in-distribution articles (.80 vs .60).
  * The API scores every article/tweet in parallel (this tool) — it wins on fresh/hard articles
    (.779/.771 vs .571/.442) and is the only reader that handles 'unrelated'.
  * CONSENSUS is the auto-accept tier: when DEBATE and the API agree, measured accuracy is .83-.90 —
    higher than either alone, on BOTH populations.
  * DISAGREEMENTS go to the review queue with DEBATE's call as default and the API's as second
    opinion — the user's override file stays the supreme court.
Modes:
  python api_stance_layer.py press  [--go]   ~2,400 articles (title+metadata — matches the measured design)
  python api_stance_layer.py tweets [--go]   ~9,600 CU-relevant tweets (full text)
Cost-gated (--go), prompt-cached, resume-safe (cache jsonl); sidecars written atomically:
  api_press_stance.csv / api_tweet_stance.csv + press_review_queue.csv (consensus tiers)."""
import csv, hashlib, io, json, os, sys, threading, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
# model selection (user 2026-07-04: "I want that running on Fable"): --fable / --opus / default sonnet.
# Per-model result caches — switching models NEVER mixes labels in one dataset.
# 2026-07-04 measured ladder on goldeval (identical inputs): Opus+original-prompt 0.779 > Fable 0.608-0.618
# > DEBATE 0.571 -> production reader = OPUS REPLICA (original prelabel prompts verbatim + adaptive
# thinking + effort medium, the exact 0.779 configuration). Estimates include thinking tokens.
MODELS = {'fable': ('claude-fable-5', 0.0070), 'opus': ('claude-opus-4-8', 0.0055), 'sonnet': ('claude-sonnet-5', 0.0018)}
_mk = next((k for k in MODELS if '--' + k in sys.argv), 'sonnet')
MODEL, EST_PER_CALL = MODELS[_mk]
CACHE_SUFFIX = '' if _mk == 'sonnet' else '_' + _mk
WORKERS = 8

if not os.environ.get('ANTHROPIC_API_KEY'):
_kf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'anthropic_key.txt')
    if os.path.exists(_kf): os.environ['ANTHROPIC_API_KEY'] = open(_kf).read().strip()
import anthropic
client = anthropic.Anthropic()
lock = threading.Lock()

PRESS_SYSTEM = """You label press items for a credit-union (CU) legislative-stance model. Each item is a press
release or news article associated with a member of Congress. You see only metadata and the headline —
judge from those; when the headline alone is ambiguous, say so via low confidence rather than guessing hard.

Work through the factorization, in order:
1. TARGET — the specific thing the text takes a position on (a bill, agency, rule, practice, or event).
   If the item does not express any stance connected to the member (ceremonial, generic constituent news,
   or the member is only incidentally mentioned), the item is UNRELATED.
2. STANCE TOWARD TARGET — does the ACTOR support, oppose, or stay neutral on that target?
3. TARGET'S CU VALENCE — is the target good (+1), bad (-1), or neutral (0) FOR CREDIT UNIONS?
   Fixed priors for this model (institutional positions of the credit-union movement):
   * Expanding CFPB power/funding/enforcement reach = -1; restraining or reforming CFPB (bipartisan
     commission, appropriations oversight) = +1.
   * Interchange caps, the Credit Card Competition Act, credit-card rate/fee mandates = -1;
     blocking or repealing them = +1.
   * Measures favoring large banks over CUs, or attacks on the CU tax status = -1;
     reinforcing the CU tax status, CU charter/field-of-membership expansion = +1.
   * Fraud prevention, data-privacy standards CUs seek, CDFI funding, defense-CU access = +1.
4. CU ALIGNMENT — the member's alignment as evidenced by THIS item:
   stance(actor toward target) x valence(target for CUs). Supporting an anti-CU target = oppose.
   Opposing an anti-CU target = support. Neutral stance or neutral target = neutral.
5. ATTRIBUTION — whose position does this item evidence?
   * member-own: the member's own press release / their quoted words.
   * reported-action: a third party (e.g. America's Credit Unions) reporting an ACTION the member took
     (sponsored, voted, withdrew an amendment). The alignment is about the member's ACTION, not the
     article's tone. E.g. "Advocacy win: Senator X will not offer the amendment" = the member withdrew
     an anti-CU amendment -> weak support, NOT the article's celebratory tone.
   * third-party-voice: the text's stance belongs to someone other than the member (an advocacy group's
     opinion, an opponent's attack). If the member's own position cannot be inferred, alignment=unrelated.
Confidence: high = headline clearly establishes actor, target, and direction; medium = probable but one
element inferred; low = headline insufficient (prefer neutral/unrelated + low over a guessed direction).
Keep rationale to one sentence."""

TWEET_SYSTEM = """You label social-media posts by members of Congress for a credit-union (CU) legislative-stance
model. You see the full post text. Work through the factorization, in order:
1. TARGET — the specific bill/agency/rule/practice the post takes a position on. Constituent service,
   ceremony, sports, condolences, pure campaign content = no policy target = UNRELATED.
2. STANCE TOWARD TARGET — does the member support, oppose, or stay neutral on that target?
3. TARGET'S CU VALENCE — good (+1), bad (-1), or neutral (0) FOR CREDIT UNIONS?
   Fixed priors: expanding CFPB reach = -1, restraining/reforming it = +1; interchange caps / Credit Card
   Competition Act / card mandates = -1, blocking them = +1; pro-big-bank or anti-CU-tax-status = -1,
   pro-CU-charter/tax status = +1; fraud prevention, CU-sought data-privacy, CDFI, defense-CU access = +1.
   CRITICAL DISCRIMINATOR: generic anti-bank/populist rhetoric ("big banks", "Wall Street greed", "junk
   fees", cheering CFPB actions against banks) is NOT pro-credit-union by default: bank-bashing with no
   CU-relevant target = valence 0 -> neutral. On interchange, CUs side WITH banks: cheering a swipe-fee
   cap = oppose. CFPB cheerleading = -1 when the action's reach covers CUs too.
4. CU ALIGNMENT — stance x valence. Supporting an anti-CU target = oppose; opposing one = support.
5. ATTRIBUTION — member-own / rt-endorsement (amplification is adoption unless clearly adversarial) /
   third-party-voice (member's own position not inferable -> unrelated).
Confidence high/medium/low (prefer neutral/unrelated + low over guessing). One-sentence rationale."""

def schema(attr_enum):
    return {"type": "object",
            "properties": {"target": {"type": "string"},
                           "stance_toward_target": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
                           "target_cu_valence": {"type": "integer", "enum": [1, 0, -1]},
                           "cu_alignment": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
                           "attribution": {"type": "string", "enum": attr_enum},
                           "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                           "rationale": {"type": "string"}},
            "required": ["target", "stance_toward_target", "target_cu_valence", "cu_alignment",
                         "attribution", "confidence", "rationale"], "additionalProperties": False}

PRESS_SCHEMA = schema(["member-own", "reported-action", "third-party-voice"])
TWEET_SCHEMA = schema(["member-own", "rt-endorsement", "third-party-voice"])

def classify(system, sch, user, _retry=True):
    kw = {}
    if _mk == 'opus':   # REPLICA of the measured 0.779 configuration: adaptive thinking + effort medium
        kw = {"thinking": {"type": "adaptive"}}
    resp = client.messages.create(
        model=MODEL, max_tokens=3000 if _mk == 'opus' else 1000,
        output_config=({"effort": "medium", "format": {"type": "json_schema", "schema": sch}} if _mk == 'opus'
                       else {"format": {"type": "json_schema", "schema": sch}}),
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user + "\n(Keep rationale under 12 words.)"}], **kw)
    try:
        return json.loads(next(b.text for b in resp.content if b.type == "text"))
    except (json.JSONDecodeError, StopIteration):
        if _retry: return classify(system, sch, user, _retry=False)
        raise

def run(items, system, sch, cache_path, tag):
    """items: list of (key, user_prompt, meta). Returns {key: result} incl. cached."""
    cache = {}
    if os.path.exists(cache_path):
        for ln in open(cache_path, encoding='utf-8'):
            try:
                d = json.loads(ln); cache[d['k']] = d['out']
            except Exception: pass
    todo = [(k, u, m) for k, u, m in items if k not in cache]
    est = len(todo) * EST_PER_CALL
    print('%s: %d items | cached %d | to classify %d | est ~$%.2f (prompt-cached)' % (tag, len(items), len(cache), len(todo), est))
    if todo and '--go' not in sys.argv:
        print('COST GATE: add --go to spend.'); sys.exit(0)
    if not todo: return cache
    cf = io.open(cache_path, 'a', encoding='utf-8')
    done = errs = 0
    def work(it):
        k, u, m = it
        try: return k, classify(system, sch, u), None
        except Exception as e: return k, None, (str(e)[:120] or type(e).__name__)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in as_completed([ex.submit(work, it) for it in todo]):
            k, out, err = f.result()
            with lock:
                done += 1
                if err or not out:
                    errs += 1
                    if errs <= 3: print('  err sample:', err, flush=True)
                else:
                    cache[k] = out
                    cf.write(json.dumps({'k': k, 'out': out}, ensure_ascii=True) + '\n')
                    if done % 250 == 0: cf.flush()
                if done % 500 == 0 or done == len(todo):
                    print('  %d/%d (%d errors)' % (done, len(todo), errs), flush=True)
    cf.close()
    if errs > len(todo) * 0.1: print('  !! high error rate — check credits/network before trusting coverage')
    return cache

def write_csv(path, rows, cols):
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    os.replace(tmp, path)

if 'press' in sys.argv:
    src = os.path.join(MB_DRAFT, 'cu_press_stance_tone.csv')
    arts = list(csv.DictReader(io.open(src, encoding='utf-8-sig', newline='')))
    items = []
    for r in arts:
        u = (r.get('url') or '').strip()
        if not u: continue
        k = hashlib.md5(u.encode('utf-8')).hexdigest()
        ul = u.lower()   # source channel derived from url (replica of the original prelabel's source line)
        dom = ('americascreditunions.org (the CU trade association itself)' if 'americascreditunions' in ul
               else ("the member's own congressional website" if ('.house.gov' in ul or '.senate.gov' in ul) else 'a third-party outlet'))
        user = ("Member: %s (%s, source: %s)\nDate: %s\nPress categories: %s\nHeadline: %s" %
                (r.get('member'), r.get('party'), dom, r.get('date'), r.get('categories'), r.get('title')))
        items.append((k, user, r))
    res = run(items, PRESS_SYSTEM, PRESS_SCHEMA, B + r'\api_press_cache%s.jsonl' % CACHE_SUFFIX, 'PRESS[%s]' % MODEL)
    out = []; agree = dis = 0
    for k, _, r in items:
        o = res.get(k)
        if not o: continue
        deb = (r.get('stance') or '').strip().lower()
        a = o['cu_alignment']
        tier = 'agree' if deb == a else 'disagree'
        if deb == a: agree += 1
        else: dis += 1
        out.append({'url': r.get('url'), 'member': r.get('member'), 'date': r.get('date'), 'title': r.get('title'),
                    'debate_stance': deb, 'api_stance': a, 'api_target': o['target'], 'api_attribution': o['attribution'],
                    'api_confidence': o['confidence'], 'api_rationale': o['rationale'], 'tier': tier})
    if not out: print('no press results (credits exhausted?) — sidecars left untouched'); sys.exit(1)
    write_csv(B + r'\data\stance\api_press_stance.csv', out, list(out[0].keys()))
    rev = [r for r in out if r['tier'] == 'disagree']
    for r in rev: r['your_decision'] = ''
    write_csv(B + r'\data\stance\press_review_queue.csv', rev, list(out[0].keys()) + ['your_decision'])
    print('PRESS done: %d scored | consensus %d (%.0f%%) | disagreements -> press_review_queue.csv: %d' %
          (len(out), agree, 100 * agree / max(1, len(out)), dis))

elif 'tweets' in sys.argv:
    src = B + r'\data\stance\quorum_member_tweets.csv'
    tws = list(csv.DictReader(io.open(src, encoding='utf-8-sig', newline='')))
    items = []
    for r in tws:
        t = (r.get('Content') or '').strip()
        if not t: continue
        k = hashlib.md5((r.get('Quorum ID') or t[:80]).encode('utf-8', 'replace')).hexdigest()
        rt = 'yes' if t.startswith('RT @') else 'no'
        user = ("Member: %s\nDate: %s\nRetweet/repost: %s\nPost text: %s" % (r.get('member'), r.get('dt'), rt, t[:1200]))
        items.append((k, user, r))
    res = run(items, TWEET_SYSTEM, TWEET_SCHEMA, B + r'\api_tweet_cache%s.jsonl' % CACHE_SUFFIX, 'TWEETS[%s]' % MODEL)
    out = []
    for k, _, r in items:
        o = res.get(k)
        if not o: continue
        out.append({'quorum_id': r.get('Quorum ID'), 'member': r.get('member'), 'date': r.get('dt'),
                    'is_rt': 1 if (r.get('Content') or '').startswith('RT @') else 0,
                    'api_stance': o['cu_alignment'], 'api_target': o['target'], 'api_attribution': o['attribution'],
                    'api_confidence': o['confidence'], 'api_rationale': o['rationale'],
                    'text': (r.get('Content') or '')[:280]})
    if not out: print('no tweet results (credits exhausted?) — sidecars left untouched'); sys.exit(1)
    write_csv(B + r'\data\stance\api_tweet_stance.csv', out, list(out[0].keys()))
    dist = collections.Counter(r['api_stance'] for r in out)
    print('TWEETS done: %d scored | distribution %s -> api_tweet_stance.csv' % (len(out), dict(dist)))
else:
    print('usage: api_stance_layer.py [press|tweets] [--go]')
