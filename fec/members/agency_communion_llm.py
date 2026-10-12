# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""AGENCY/COMMUNION personality-cue classifier — Birkenmaier & Lechner (Political Analysis 2025)
ported to congressional English (user-directed 2026-07-03: "combine its use of cue utilization and
prompting"). Their design, kept faithfully: sentence-level personality CUES classified into
agency ("Assertiveness & Will to Take Control") / communion ("Empathy & Compassion") / none via
few-shot LLM prompting with a codebook of explicit(strong) + implicit(weak) cue conditions — their
best config hit macro-F1 0.74-0.77 vs human coders, beating SVM (0.63) and fine-tuned RoBERTa (0.70).
Member-level score = share of self-referential sentences carrying each cue (strong=1.0, weak=0.5).

Modes:
  python fec/members/agency_communion_llm.py --selftest     # their 18-functional-test-unit pattern (must pass first)
  python fec/members/agency_communion_llm.py                # PILOT: RKH-overlap members only (ground truth exists)
  python fec/members/agency_communion_llm.py --all          # full 533 roster
Sentence cache (sidecar jsonl) makes reruns resume-safe/free. Output: data/members/agency_communion.json (atomic).
Requires ANTHROPIC_API_KEY. Model: CLAUDE_AC_MODEL env (default claude-sonnet-5)."""
import csv, hashlib, io, json, os, random, re, sqlite3, sys, threading, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import anthropic
from moneyball_core import dump_atomic

B = MB_ROOT
MODEL = os.environ.get('CLAUDE_AC_MODEL', 'claude-fable-5' if '--fable' in sys.argv else 'claude-sonnet-5')
EST_PER_CALL = 0.006 if 'fable' in MODEL else 0.0015   # cost-gate estimate (cached codebook + ~60 in + ~60 out)
NSENT = int(os.environ.get('AC_NSENT', '40'))
WORKERS = int(os.environ.get('AC_WORKERS', '8'))
CACHE = B + (r'\data\members\agency_communion_cache_fable.jsonl' if 'fable' in MODEL else r'\data\members\agency_communion_cache.jsonl')   # per-model caches: instruments never mix
OUT = B + r'\data\members\agency_communion.json'

SYSTEM = """You classify single sentences spoken or written by U.S. members of Congress into personality-cue
categories, following a validated political-psychology codebook (Lechner et al. politician inventory;
Birkenmaier & Lechner 2025). Categories:

1. **communion** ("Empathy & Compassion" — the drive to get along): the sentence shows the member is
   empathetic, feels for the problems and feelings of (disadvantaged) people, shows care and compassion,
   or understands the needs of disadvantaged groups.
   STRONG cues: direct reference to concrete (disadvantaged) people or groups and their suffering/needs;
   explicit calls for compassion or empathy; references to crises (war, disaster) with their consequences
   for human lives; first-person testimony of sitting with/listening to affected people.
   WEAK cues: moral evaluation that is fact-based/argumentative rather than explicitly empathetic;
   crisis references without naming the human consequences; empathic focus on political units
   (communities, states) rather than people.

2. **agency** ("Assertiveness & Will to Take Control" — the drive to get ahead): the sentence shows the
   member is determined to take leadership, carry responsibility, and act against resistance.
   STRONG cues: concrete power-related plans framed against resistance or objections ("I will get this
   done regardless of who stands in the way"); explicit claim or active exercise of leadership; reference
   to one's own perceived leadership role ("as chairman, I...").
   WEAK cues: collective calls to action without personal ownership ("we will push this forward");
   determination language without naming resistance; competitive framing without explicit leadership claim.

3. **none**: the sentence licenses no inference about either trait (procedural talk, policy facts,
   scheduling, thanks, citations, questions to witnesses without personal framing).

CRITICAL — POSITIVE POLES ONLY (codebook rule): agency and communion are the POSITIVE poles.
Statements expressing the OPPOSITE pole are "none", even though they mention the same domain:
passivity, submissiveness, staying in the background, avoiding leadership or decisions = none (NOT
agency); coldness, indifference, hard-heartedness, keeping distance from people in need = none (NOT
communion). Topic overlap is not a cue — only the positive expression of the trait counts.

Judge the CUE the public would perceive, not the member's true personality. One category per sentence
(the dominant cue). Respond in exact JSON: {"label": "agency"|"communion"|"none", "strength": "strong"|"weak"|"na", "reason": "<one short sentence>"}

Examples:
- "As the chair of this committee, I will make sure this bill reaches the floor no matter how hard the big banks push back." -> {"label":"agency","strength":"strong","reason":"claims leadership role and vows action against resistance"}
- "We will push this legislation forward and ensure its implementation." -> {"label":"agency","strength":"weak","reason":"collective determination without personal ownership of overcoming resistance"}
- "I sat with families in Ohio who lost their savings, and I carry their stories with me every day." -> {"label":"communion","strength":"strong","reason":"first-person testimony of compassion for concrete affected people"}
- "This crisis has consequences for working families across the country." -> {"label":"communion","strength":"weak","reason":"crisis framed via human impact but argumentative rather than explicitly empathetic"}
- "The subcommittee will reconvene at 2 p.m. to continue the markup." -> {"label":"none","strength":"na","reason":"procedural statement with no trait inference"}
- "I yield the balance of my time." -> {"label":"none","strength":"na","reason":"chamber ritual"}
- "I'd rather let others take the lead on this one." -> {"label":"none","strength":"na","reason":"opposite pole of agency (passivity) is not a positive agency cue"}
- "Frankly, their problems are not my concern." -> {"label":"none","strength":"na","reason":"opposite pole of communion (indifference) is not a positive communion cue"}"""

if not os.environ.get('ANTHROPIC_API_KEY'):
    _kf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'anthropic_key.txt')
    if os.path.exists(_kf): os.environ['ANTHROPIC_API_KEY'] = open(_kf).read().strip()   # govinfo_key.txt pattern — never paste keys into chat
client = anthropic.Anthropic()
lock = threading.Lock()

def classify(sentence, _retry=True):
    resp = client.messages.create(
        model=MODEL, max_tokens=1000,   # QA 2026-07-03: 400 truncated verbose 'reason' strings mid-JSON -> 33% error rate
        output_config={"format": {"type": "json_schema", "schema": {
            "type": "object",
            "properties": {"label": {"type": "string", "enum": ["agency", "communion", "none"]},
                           "strength": {"type": "string", "enum": ["strong", "weak", "na"]},
                           "reason": {"type": "string"}},
            "required": ["label", "strength", "reason"], "additionalProperties": False}}},
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],   # prompt caching: the codebook is ~85% of input tokens; cached reads cost ~10% -> per-sentence cost drops ~60-70%
        messages=[{"role": "user", "content": "Sentence (to classify): " + sentence[:500] + "\n(Keep reason under 12 words.)"}])
    try:
        return json.loads(next(b.text for b in resp.content if b.type == "text"))
    except (json.JSONDecodeError, StopIteration):
        if _retry: return classify(sentence, _retry=False)
        raise

# ---------------- functional self-test (their 18-unit design, English anchors from the paper) ----------
if '--selftest' in sys.argv:
    UNITS = [("I am a born leader.", "agency"), ("I am assertive and dominant.", "agency"),
             ("I claim leadership and I am not afraid to use power to get results.", "agency"),
             ("I show understanding for the needs of disadvantaged people.", "communion"),
             ("I am empathetic and show genuine compassion.", "communion"),
             ("I am compassionate; I have a soft heart for those who are struggling.", "communion"),
             ("I prefer to stay in the background.", "none"), ("I am passive and let others decide.", "none"),
             ("I am hard and cold-hearted.", "none"), ("I feel indifferent about other people's problems.", "none"),
             ("I avoid taking charge whenever possible.", "none"), ("I keep my distance from people in need.", "none"),
             ("The hearing will resume tomorrow at 10 a.m.", "none"),
             ("H.R. 1234 amends the Truth in Lending Act.", "none"),
             ("Thank you, Mr. Chairman, I yield back.", "none"),
             ("The CBO score was released on Tuesday.", "none"),
             ("Inflation rose 3.2 percent year over year.", "none"),
             ("My office is located in the Rayburn building.", "none")]
    ok = 0
    for s, want in UNITS:
        got = classify(s)
        hit = got["label"] == want
        ok += hit
        print("%s  %-9s (want %-9s) %s" % ("OK " if hit else "MISS", got["label"], want, s[:60]))
    print("FUNCTIONAL TEST: %d/18 correct" % ok)
    sys.exit(0 if ok >= 16 else 1)

# ---------------- corpus: self-referential sentences from floor/hearing speech ----------
SELF = re.compile(r"\b(i|we|my|our|me|us)\b", re.I)
RITUAL = re.compile(r"i yield|unanimous consent|the gentle(man|woman|lady)|quorum|balance of my time|mr\.? (speaker|president|chairman)|madam", re.I)
def sentences_for(members):
    con = sqlite3.connect(B + r'\social_media.db')
    txt = collections.defaultdict(list)
    for m, t in con.execute('SELECT member,text FROM speeches'):
        if m in members and t: txt[m].append(t)
    con.close()
    out = {}
    for m, ts in txt.items():
        sents = []
        for t in ts:
            for s in re.split(r'(?<=[.!?])\s+', t):
                s = s.strip()
                if 40 <= len(s) <= 350 and SELF.search(s) and not RITUAL.search(s):
                    sents.append(s)
        # COST GUARD (user 2026-07-03 "I don't have the money"): CACHE-PREFERRING sampling.
        # A growing corpus used to reshuffle the whole sample each pass -> paying to classify a
        # shifted sentence set every run. Already-classified sentences now claim sample slots FIRST
        # (free); new spend only tops up to NSENT.
        seen = set(); uniq = []
        for s in sents:
            if s not in seen: seen.add(s); uniq.append(s)
        pre = [s for s in uniq if hashlib.md5((m + '||' + s).encode('utf-8')).hexdigest() in CACHED_KEYS]
        new = [s for s in uniq if hashlib.md5((m + '||' + s).encode('utf-8')).hexdigest() not in CACHED_KEYS]
        rng = random.Random(7)
        rng.shuffle(new)
        out[m] = (pre[:NSENT] + new)[:NSENT]
    return out

C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
if '--all' in sys.argv:
    target = set(C)
else:  # PILOT: RKH-overlap only
    rkh_ic = set()
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'liwc', 'rkh', 'congressbios.tab'),
              encoding='utf-8', errors='replace') as fh:
        for r in csv.DictReader(fh, delimiter='\t'):
            try:
                float(r['openn_avg']); rkh_ic.add(int(float(r['icpsr'])))
            except (ValueError, TypeError, KeyError):
                continue
    b2i = {}
    for cn in (119, 118, 117):
        for r in csv.DictReader(open(os.path.join(B, 'votes', 'HS%d_members.csv' % cn), encoding='utf-8')):
            bg = (r.get('bioguide_id') or '').strip()
            try: ic = int(float(r.get('icpsr') or ''))
            except ValueError: continue
            if bg and bg not in b2i: b2i[bg] = ic
    target = {m for m, v in C.items() if b2i.get(v.get('bioguide') or '') in rkh_ic}
    print('PILOT mode: %d RKH-overlap members' % len(target))

cache = {}
if os.path.exists(CACHE):
    for ln in open(CACHE, encoding='utf-8'):
        try:
            # first-wins: append-only cache can hold a duplicate key with a conflicting label;
            # keep the earliest so the result is deterministic (not append-order dependent).
            d = json.loads(ln)
            if d['k'] not in cache: cache[d['k']] = d
        except Exception: pass
CACHED_KEYS = set(cache)
print('cache: %d sentences already classified' % len(cache))

corpus = sentences_for(target)
print('members with sentences: %d | total sentences: %d' % (len(corpus), sum(map(len, corpus.values()))))

todo = []
for m, sents in corpus.items():
    for s in sents:
        k = hashlib.md5((m + '||' + s).encode('utf-8')).hexdigest()
        if k not in cache: todo.append((k, m, s))
# COST GATE: estimate before spending; require --go to proceed (default = preview only).
_est = len(todo) * EST_PER_CALL   # cached-codebook input + short JSON out, per selected model
print('to classify: %d (model %s) | estimated cost ~$%.2f (prompt-cached)' % (len(todo), MODEL, _est))
if todo and '--go' not in sys.argv:
    print('COST GATE: add --go to spend. (Cached sentences already cover the sample where possible.)')
    sys.exit(0)

cf = io.open(CACHE, 'a', encoding='utf-8')
done = errs = 0
def work(item):
    k, m, s = item
    try: return k, m, s, classify(s), None
    except Exception as e: return k, m, s, None, (str(e)[:150] or type(e).__name__)
if todo:
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in as_completed([ex.submit(work, it) for it in todo]):
            k, m, s, out, err = f.result()
            with lock:
                done += 1
                if err or not out:
                    errs += 1
                    if errs <= 3: print('  err sample:', err, flush=True)
                else:
                    d = {'k': k, 'member': m, 'label': out['label'], 'strength': out['strength']}
                    cache[k] = d; cf.write(json.dumps(d) + '\n')
                if done % 250 == 0 or done == len(todo):
                    print('  %d/%d (%d errors)' % (done, len(todo), errs), flush=True); cf.flush()
cf.close()

res = {}
for m, sents in corpus.items():
    n = ag = co = ag_r = co_r = 0
    for s in sents:
        d = cache.get(hashlib.md5((m + '||' + s).encode('utf-8')).hexdigest())
        if not d: continue
        n += 1
        w = 1.0 if d['strength'] == 'strong' else 0.5
        if d['label'] == 'agency': ag += w; ag_r += 1
        elif d['label'] == 'communion': co += w; co_r += 1
    if n:
        res[m] = {'n': n, 'agency': round(ag / n, 4), 'communion': round(co / n, 4),
                  'agency_raw': round(ag_r / n, 4), 'communion_raw': round(co_r / n, 4)}
dump_atomic({'members': res, 'model': MODEL, 'nsent': NSENT,
             'method': 'Birkenmaier-Lechner 2025 cue prompting (few-shot codebook, strong=1.0/weak=0.5 share of self-referential floor/hearing sentences)'}, OUT, indent=1)
ags = sorted(res.values(), key=lambda d: -d['agency']); cos = sorted(res.values(), key=lambda d: -d['communion'])
print('wrote %s: %d members | mean agency %.3f | mean communion %.3f' % (
    OUT, len(res), sum(d['agency'] for d in res.values()) / max(1, len(res)),
    sum(d['communion'] for d in res.values()) / max(1, len(res))))
for tag, key in (('agency', 'agency'), ('communion', 'communion')):
    top = sorted(res, key=lambda m: -res[m][key])[:5]
    print('  top %-9s: %s' % (tag, ', '.join('%s %.2f' % (m, res[m][key]) for m in top)))
