# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT, DRAFT as MB_DRAFT
"""PRESS READER v4 'ftfix' (2026-07-04, user: 'keep going until we hit the absolute ceiling').
Two changes vs the 0.779 Opus replica, both diagnosed from the user's own gold labels:
  1. FULL BODY TEXT (press_text_cache + priority cache) instead of headline-only.
  2. ATTRIBUTION RULE FIXED: the old prompt said third-party-voice -> unrelated when the member's
     position 'cannot be inferred'. The user's 312 queue golds contradict this: 41/65 of his
     third-party-voice items are SUPPORT — CU-trade-press coverage of a member IS alignment
     evidence (they cover members who act for CUs). New rule: infer the member's position from
     described actions/quotes/coverage-selection; unrelated ONLY if the item evidences nothing.
Model: Opus (the measured-best press reader), adaptive thinking + effort medium (replica config).
Separate cache api_press_cache_opus_ftfix.jsonl — never mix instruments.
Usage: python press_reader_ftfix.py [--limit N] [--go]     (gold slice only: the 217 goldeval urls)"""
import csv, hashlib, io, json, os, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import anthropic

B = MB_ROOT
KEY = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'anthropic_key.txt')).read().strip()
client = anthropic.Anthropic(api_key=KEY)
MODEL = 'claude-opus-4-8'
CACHE = B + r'\data\stance\api_press_cache_opus_ftfix.jsonl'
WORKERS = int(os.environ.get('FT_WORKERS', '6'))
EST_PER_CALL = 0.105   # ~2.6k in (sys cached) + ~650 out+thinking, x1.4 safety
lock = threading.Lock()

SYSTEM = """You label press items for a credit-union (CU) legislative-stance model. You see the article's
FULL TEXT. Work through the factorization, in order:
1. TARGET — the specific bill/agency/rule/practice the item takes a position on. Constituent service,
   ceremony, grants, condolences, pure campaign content = no policy target = UNRELATED.
2. STANCE TOWARD TARGET — does the MEMBER support, oppose, or stay neutral on that target?
3. TARGET'S CU VALENCE — good (+1), bad (-1), or neutral (0) FOR CREDIT UNIONS?
   Fixed priors: expanding CFPB reach = -1, restraining/reforming it = +1; interchange caps / Credit Card
   Competition Act / card mandates = -1, blocking them = +1; measures favoring large banks over CUs or
   attacking the CU tax status = -1; reinforcing CU tax status, charter/field-of-membership expansion,
   MBL relief, reg-tailoring for small institutions = +1; fraud prevention, CU-sought data-privacy
   standards, CDFI funding, defense-CU access = +1. Generic anti-bank/populist rhetoric with no
   CU-relevant target = valence 0.
4. CU ALIGNMENT — stance(member toward target) x valence(target for CUs). Supporting an anti-CU target
   = oppose. Opposing an anti-CU target = support. Neutral stance or neutral target = neutral.
5. ATTRIBUTION — whose position does this item evidence?
   * member-own: the member's own press release / their quoted words.
   * reported-action: a third party reporting an ACTION the member took (sponsored, cosponsored, voted,
     met with advocates, withdrew an amendment). Alignment follows the member's ACTION, not the
     article's tone.
   * third-party-voice: the stance language belongs to someone other than the member. IMPORTANT: this
     does NOT default to unrelated. Credit-union trade press covers members BECAUSE they acted on CU
     issues — coverage describing a member's bill, vote, letter, or meeting evidences that member's
     alignment even when the quotes are the trade group's. Infer the member's position from the actions
     and framing described. Use unrelated ONLY if the text genuinely evidences nothing about this
     member's position on any CU-relevant target.
Confidence: high = text clearly establishes actor, target, and direction; medium = probable but one
element inferred; low = insufficient (prefer neutral/unrelated + low over a guessed direction).
Keep rationale to one sentence."""

SCHEMA = {"type": "object",
          "properties": {"target": {"type": "string"},
                         "stance_toward_target": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
                         "target_cu_valence": {"type": "integer", "enum": [1, 0, -1]},
                         "cu_alignment": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
                         "attribution": {"type": "string", "enum": ["member-own", "reported-action", "third-party-voice"]},
                         "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                         "rationale": {"type": "string"}},
          "required": ["target", "stance_toward_target", "target_cu_valence", "cu_alignment",
                       "attribution", "confidence", "rationale"], "additionalProperties": False}

def classify(user, _retry=True):
    resp = client.messages.create(
        model=MODEL, max_tokens=3000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user + "\n(Keep rationale under 12 words.)"}])
    try:
        return json.loads(next(b.text for b in resp.content if b.type == "text"))
    except (json.JSONDecodeError, StopIteration):
        if _retry: return classify(user, _retry=False)
        raise

# ---- inputs: goldeval urls + tone metadata + full text ----
gold = [r for r in csv.DictReader(open(B + r'\data\stance\cu_golden_labels.csv', encoding='utf-8-sig')) if r.get('split') == 'goldeval']
tone = {}
for r in csv.DictReader(io.open(os.path.join(MB_DRAFT, 'cu_press_stance_tone.csv'), encoding='utf-8-sig', newline='')):
    tone[(r.get('url') or '').strip().rstrip('/')] = r
FT = {}
for cp in (B + r'\press_text_cache.jsonl', B + r'\data\stance\press_text_cache_priority.jsonl'):
    if not os.path.exists(cp): continue
    for ln in open(cp, encoding='utf-8'):
        try:
            d = json.loads(ln); u = (d.get('url') or '').strip().rstrip('/')
            if d.get('text'): FT[u] = d['text']
        except Exception: pass

items = []
for r in gold:
    u = (r.get('url') or '').strip()
    un = u.rstrip('/')
    if not u: continue
    t = tone.get(un, {})
    body = FT.get(un, '') or (r.get('title') or '')
    ul = u.lower()
    dom = ('americascreditunions.org (the CU trade association itself)' if 'americascreditunions' in ul
           else ("the member's own congressional website" if ('.house.gov' in ul or '.senate.gov' in ul) else 'a third-party outlet'))
    user = ("Member: %s (%s, source: %s)\nDate: %s\nHeadline: %s\nFull text: %s" %
            (t.get('member') or r.get('member'), t.get('party') or '', dom, t.get('date') or '',
             r.get('title') or t.get('title') or '', body[:5000]))
    items.append((hashlib.md5(u.encode('utf-8')).hexdigest(), user, r))

lim = None
if '--limit' in sys.argv: lim = int(sys.argv[sys.argv.index('--limit') + 1])
cache = {}
if os.path.exists(CACHE):
    for ln in open(CACHE, encoding='utf-8'):
        try:
            d = json.loads(ln); cache[d['k']] = d['out']
        except Exception: pass
todo = [(k, u, m) for k, u, m in items if k not in cache]
if lim: todo = todo[:lim]
print('FTFIX[%s]: gold items %d | cached %d | to classify %d | est ~$%.2f' % (MODEL, len(items), len(cache), len(todo), len(todo) * EST_PER_CALL), flush=True)
if todo and '--go' not in sys.argv:
    print('COST GATE: add --go to spend.'); sys.exit(0)

cf = io.open(CACHE, 'a', encoding='utf-8')
done = errs = 0
def work(it):
    k, u, m = it
    try: return k, classify(u), None
    except Exception as e: return k, None, (str(e)[:140] or type(e).__name__)
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
            if done % 25 == 0 or done == len(todo): print('  %d/%d (%d errors)' % (done, len(todo), errs), flush=True)
cf.close()
if errs > len(todo) * 0.1: print('!! high error rate — check credits before trusting coverage')

# quick agreement vs gold (flat read only; the stack is measured separately, pre-registered)
CLS = ['support', 'oppose', 'neutral', 'unrelated']
n = ok = 0
for k, _, r in items:
    o = cache.get(k); g = (r.get('label') or '').strip().lower()
    if not o or g not in CLS: continue
    n += 1; ok += 1 if o['cu_alignment'] == g else 0
if n: print('FTFIX flat read vs gold: %d/%d = %.3f  (old headline Opus flat = 0.779)' % (ok, n, ok / n))
