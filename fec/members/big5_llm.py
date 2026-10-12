# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""BIG5 LLM SCORER — item-level, de-identified, chunk-then-aggregate (campaign 2026-07-04).
Design follows the literature survey (what the successful authors did):
  * ITEM-LEVEL prompting, not holistic trait rating (PsyCoT EMNLP'23 +4-10 F1; Ganesan WASSA'23:
    questionnaire-item knowledge injection was GPT-3's best config). 20 IPIP-style observer-keyed
    items, 4 per trait, balanced keying.
  * COARSE 3-point responses (-1/0/+1) — fine scales collapse (Ganesan 3-class ≈ chance) and
    'cannot tell' maps to 0 because personality cues are ABSENT in ~90% of political sentences
    (Birkenmaier & Lechner PA'25) — never force a guess.
  * DE-IDENTIFICATION of author name/state/party/title before scoring: LLMs carry reputational
    priors about named politicians (Cao & Kosinski PNAS Nexus'24: r=.76-.87 for public figures
    from the NAME alone) — we must rate the TEXT, not the model's prior. --named runs the
    contamination probe (same chunks, identity left in, separate cache).
  * CHUNK-then-AVERAGE: ~1,200-word chunks sampled evenly across each member's corpus, per-chunk
    item scoring, mean up (variance preservation; NHB'25 scored per-entry then averaged).
  * Aggregation: item -> chunk trait -> genre mean -> member (chunk-count-weighted) -> z within
    the scored pool. Artifact keeps the member x genre matrix for the MTMM/test-retest axes.
Pilot scope (--pilot): the RKH-overlap members only (measurable vs the anchor), genres CREC +
INTERVIEW. Full roster with --all. Validation: fec/members/validate_big5.py.
Usage:
  python fec/members/big5_llm.py --pilot [--sonnet|--opus|--haiku] [--limit N] [--go] [--named]
  python fec/members/big5_llm.py --all   [--sonnet|...] [--go]
Cache: big5_llm_cache_<model>[_named].jsonl, key md5(member|genre|chunk_idx|dei|itemset-v1|texthash16)
(text hash added 2026-07-07: corpus growth re-samples chunks, so identical text = hit, changed = re-score).
Output: big5_llm_<model>.json (never overwrites big5_traits.json)."""
import csv, hashlib, io, json, os, re, sqlite3, sys, threading, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import anthropic

B = MB_ROOT
KEY = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'anthropic_key.txt')).read().strip()
client = anthropic.Anthropic(api_key=KEY)
_mk = 'sonnet'
if '--opus' in sys.argv: _mk = 'opus'
elif '--haiku' in sys.argv: _mk = 'haiku'
MODEL = {'sonnet': 'claude-sonnet-5', 'opus': 'claude-opus-4-8', 'haiku': 'claude-haiku-4-5-20251001'}[_mk]
NAMED = '--named' in sys.argv
REP = int(sys.argv[sys.argv.index('--rep') + 1]) if '--rep' in sys.argv else 0   # reliability pass: same chunks, fresh calls (ICC across reps); rep 0 = the primary pass
CACHE = B + r'\data\members\big5_llm_cache_%s%s%s.jsonl' % (_mk, '_named' if NAMED else '', '_rep%d' % REP if REP else '')
OUT = B + r'\data\members\big5_llm_%s%s%s.json' % (_mk, '_named' if NAMED else '', '_rep%d' % REP if REP else '')
WORKERS = int(os.environ.get('B5_WORKERS', '8'))
EST_PER_CALL = {'sonnet': 0.014, 'opus': 0.075, 'haiku': 0.004}[_mk]   # x1.4 safety included
# Fast mode (user-enabled 2026-07-05): opus-only research preview, 2.5x OTPS at exactly 2x price
# ($10/$50 vs $5/$25 per MTok). Direct path only — the Batch API does not support it. B5_FAST=0
# reverts. If the org lacks preview access, classify() flips this off after the first rejection
# and the whole run continues at standard speed.
FAST = os.environ.get('B5_FAST', '1') != '0' and _mk == 'opus'
SPEED_N = collections.Counter()   # usage.speed served, for the end-of-run cost readout
CHUNK_WORDS = 1200; CHUNKS_PER_GENRE = 6; MIN_WORDS = 2000
lock = threading.Lock()

# ---- item battery (IPIP-style, observer-keyed, text-answerable; itemset-v1: changing ANY item
# text/keying = new itemset version = new cache key) ----
ITEMS = [
 ('O1', +1, 'O', "Expresses original ideas or imaginative framings rather than stock phrases"),
 ('O2', +1, 'O', "Engages with abstract concepts, theory, or ideas beyond the immediate topic"),
 ('O3', -1, 'O', "Sticks to routine, conventional talking points; avoids abstraction"),
 ('O4', +1, 'O', "Uses rich, varied vocabulary or vivid imagery"),
 ('C1', +1, 'C', "Cites specific facts, numbers, dates, or procedural details rather than staying vague"),
 ('C2', +1, 'C', "Shows evidence of sustained, long-term work on an issue (not one-off attention)"),
 ('C3', -1, 'C', "Is scattered or careless; jumps between topics without structure"),
 ('C4', +1, 'C', "Carefully qualifies claims, acknowledges limits, or corrects the record precisely"),
 ('E1', +1, 'E', "Comes across energetic and animated"),
 ('E2', +1, 'E', "References engaging with people warmly and often (events, conversations, crowds)"),
 ('E3', -1, 'E', "Is reserved and impersonal; keeps distance from the audience"),
 ('E4', +1, 'E', "Shows spontaneous humor, exclamation, or unscripted energy (beyond standard emphatic rhetoric)"),
 ('A1', +1, 'A', "Is warm, considerate, or cooperative toward others"),
 ('A2', +1, 'A', "Credits others, seeks common ground, or acknowledges opposing views respectfully"),
 ('A3', -1, 'A', "Is combative, contemptuous, or dismissive toward people (not just policies)"),
 ('A4', +1, 'A', "Expresses trust in others' good intentions"),
 ('S1', +1, 'ES', "Appears calm and even-keeled, including about problems"),
 ('S2', -1, 'ES', "Expresses PERSONAL distress, being overwhelmed, or defensiveness (policy alarm/advocacy does not count)"),
 ('S3', -1, 'ES', "Shows irritation, frustration, or anger"),
 ('S4', +1, 'ES', "Keeps a composed, measured tone about setbacks or criticism"),
]
# v2 (2026-07-04, from the v1 pilot's item base-rates on 202 chunks): C1/C2/C4 hit a 86-94% ceiling
# (drafted political text is definitionally organized/dutiful/goal-oriented -> the items measured the
# GENRE), E4 'assertive' 88% ceiling, S2 read threat-RHETORIC as anxiety (70% agree). Replaced with
# within-genre-variance versions: C = specificity/sustained-work/precision, E4 = unscripted energy,
# S2 = personal distress only. O/A/E-social items spread well and are unchanged.
ITEMSET_V = 'v2'
SYSTEM = ("You are a personality-assessment rater. You will read a text sample by ONE author "
          "(a public official; identifying details are redacted). For each numbered item, judge whether "
          "the statement describes THE AUTHOR as evidenced by THIS TEXT ONLY.\n"
          "Answer each item: 1 = the text provides evidence FOR the statement; -1 = the text provides "
          "evidence AGAINST it; 0 = no evidence either way (most political text carries little "
          "personality signal - use 0 freely, never guess from the topic or presumed politics).\n"
          "Judge style and disposition, not policy content or ideology.\nItems:\n" +
          "\n".join("%s. %s" % (i, t) for i, _, _, t in ITEMS))
SCHEMA = {"type": "object",
          "properties": {i: {"type": "integer", "enum": [1, 0, -1]} for i, _, _, _ in ITEMS},
          "required": [i for i, _, _, _ in ITEMS], "additionalProperties": False}

# Opus config = the repo's measured-best reader replica (api_stance_layer 0.779 goldeval:
# adaptive thinking + effort medium). Note: prompt caching on the system block is effectively
# a no-op on Opus 4.8 (its minimum cacheable prefix is 4096 tokens; this system is ~700) —
# the real cost lever on opus is --batch (Batches API, flat 50% off, non-latency-sensitive).
def _params(user):
    p = dict(model=MODEL, max_tokens=4000,   # hybrid-reasoning models think before the 20-item battery; 1000 truncated ~40% of calls
             output_config=({"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}
                            if _mk == 'opus' else {"format": {"type": "json_schema", "schema": SCHEMA}}),
             system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
             messages=[{"role": "user", "content": user}])
    if _mk == 'opus': p["thinking"] = {"type": "adaptive"}
    return p

def classify(user, _retry=True):
    global FAST
    p = _params(user)
    if FAST:
        # max_retries=0 on the fast attempt: fail over immediately instead of burning
        # retry-after waits against the dedicated fast pool (doc-recommended pattern).
        try:
            resp = client.with_options(max_retries=0).beta.messages.create(
                speed='fast', betas=['fast-mode-2026-02-01'], **p)
        except anthropic.RateLimitError as e:
            # "limit of 0 fast mode input tokens" = org not yet granted the preview -> permanent
            # standard for this run; any other 429 = pool momentarily busy -> keep trying fast.
            if '0 fast mode' in str(e):
                with lock:
                    if FAST:
                        print('  fast-mode limit is 0 for this org (preview not enabled) — standard speed', flush=True)
                        FAST = False
            resp = client.messages.create(**p)
        except anthropic.BadRequestError as e:
            if 'speed' in str(e).lower() or 'fast' in str(e).lower():
                with lock:
                    if FAST:
                        print('  fast mode rejected — continuing at standard speed:', str(e)[:100], flush=True)
                        FAST = False
                resp = client.messages.create(**p)
            else:
                raise   # unrelated 400 (e.g. grammar timeout) — surface exactly as before
        except anthropic.PermissionDeniedError:
            with lock:
                if FAST:
                    print('  fast mode not enabled for this org (research preview) — standard speed', flush=True)
                    FAST = False
            resp = client.messages.create(**p)
        except (anthropic.APIStatusError, anthropic.APIConnectionError):
            resp = client.messages.create(**p)   # transient 5xx/conn with retries off -> standard path retries
    else:
        resp = client.messages.create(**p)
    with lock: SPEED_N[getattr(resp.usage, 'speed', None) or 'standard'] += 1
    try:
        return json.loads(next(b.text for b in resp.content if b.type == "text"))
    except (json.JSONDecodeError, StopIteration):
        if _retry: return classify(user, _retry=False)
        raise

# ---- roster + RKH overlap ----
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
rkh_icpsr = set()
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'liwc', 'rkh', 'congressbios.tab'),
          encoding='utf-8', errors='replace') as fh:
    for r in csv.DictReader(fh, delimiter='\t'):
        try: rkh_icpsr.add(int(float(r['icpsr'])))
        except (ValueError, TypeError): continue
b2i = {}
for cn in (119, 118, 117):
    p = os.path.join(B, 'votes', 'HS%d_members.csv' % cn)
    if not os.path.exists(p): continue
    for r in csv.DictReader(open(p, encoding='utf-8')):
        bg = (r.get('bioguide_id') or '').strip()
        try: ic = int(float(r.get('icpsr') or ''))
        except ValueError: continue
        if bg and bg not in b2i: b2i[bg] = ic
overlap = [m for m in C if b2i.get(C[m].get('bioguide') or '') in rkh_icpsr]
members = overlap if '--pilot' in sys.argv else list(C)
if NAMED: members = members[:30]   # contamination probe subsample (stable roster order)
if REP: members = members[:25]     # reliability subsample: k reps x 25 members is enough for ICC
print('scope: %d members (%s%s)' % (len(members), 'pilot=RKH-overlap' if '--pilot' in sys.argv else 'full roster',
                                    ', NAMED contamination probe' if NAMED else ''))

# ---- corpus: CREC + INTERVIEW turns from social_media.db ----
# B5_GENRES env (run-4z, cache front-loading): scope scoring to stable genres while the interview
# corpus rebuilds — 'CREC,INTERVIEW' default is byte-identical to the original tuple. Cache keys
# embed the genre, so a CREC-only warm pass now + full pass later = identical final results.
GENRES = tuple(g.strip() for g in (os.environ.get('B5_GENRES') or 'CREC,INTERVIEW').split(',') if g.strip())
RITUAL = re.compile(r'\b(i yield (back|to|the floor|myself)[^.]*|mr\.? (president|speaker)|madam (president|speaker)|'
 r'i ask unanimous consent[^.]*|the (gentleman|gentlewoman|gentlelady) from [a-z ]+|'
 r'i (rise|reserve the balance of my time)|pursuant to [^.]*|the clerk will [^.]*|'
 r'i suggest the absence of a quorum|the balance of my time)\b', re.I)
STATES = ("alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho "
          "illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota "
          "mississippi missouri montana nebraska nevada ohio oklahoma oregon pennsylvania tennessee texas utah "
          "vermont virginia washington wisconsin wyoming").split() + \
         ["new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota",
          "rhode island", "south carolina", "south dakota", "west virginia"]
TITLE_RE = re.compile(r'\b(senator|congressman|congresswoman|representative|rep\.|sen\.|chairman|chairwoman|'
                      r'ranking member|speaker|leader|whip)\b', re.I)
PARTY_RE = re.compile(r'\b(republican[s]?|democrat[s]?|democratic|gop)\b', re.I)

def deidentify(text, member):
    if NAMED: return text
    parts = [p for p in re.split(r'\s+', member) if len(p) > 2]
    for p in parts: text = re.sub(re.escape(p), '[NAME]', text, flags=re.I)
    text = TITLE_RE.sub('[TITLE]', text)
    text = PARTY_RE.sub('[PARTY]', text)
    for st in sorted(STATES, key=len, reverse=True):
        text = re.sub(r'\b' + re.escape(st) + r'\b', '[STATE]', text, flags=re.I)
    return text

def chunks_for(member, genre, con):
    docs = [t for (t,) in con.execute(
        'select text from speeches where member=? and source=? order by date, rowid', (member, genre)) if t]
    words = []
    for t in docs:
        t = RITUAL.sub(' ', t)
        w = t.split()
        if len(w) >= 30: words.extend(w)   # skip micro-turns (procedural fragments)
    if len(words) < MIN_WORDS: return []
    total = len(words)
    n = min(CHUNKS_PER_GENRE, total // CHUNK_WORDS)
    if n == 0: return []
    out = []
    for j in range(n):   # sampled evenly across the corpus for temporal spread
        start = int(j * (total - CHUNK_WORDS) / max(1, n - 1)) if n > 1 else 0
        out.append(' '.join(words[start:start + CHUNK_WORDS]))
    return out

con = sqlite3.connect(B + r'\social_media.db', timeout=30)
con.execute('PRAGMA query_only=1')
items = []
for m in members:
    for g in GENRES:
        for ci, ch in enumerate(chunks_for(m, g, con)):
            txt = deidentify(ch, m)
            # QA 2026-07-07 (pre-GATE-2): cache key now includes a hash of the CHUNK TEXT. Without it,
            # a corpus rebuild re-samples different text at the same (member,genre,idx) but hits the
            # OLD cached judgment — the post-collector "re-score" would silently return stale scores.
            # Same text = cache hit (paid work preserved); changed text = honest re-score.
            th = hashlib.md5(txt.encode('utf-8', 'replace')).hexdigest()[:16]
            k = hashlib.md5(('%s|%s|%d|%s|%s|%s%s|%s' % (m, g, ci, 'named' if NAMED else 'dei', ITEMSET_V, _mk,
                                                      '|rep%d' % REP if REP else '', th)).encode()).hexdigest()
            items.append((k, 'Text sample:\n"""\n%s\n"""' % txt, (m, g, ci)))
print('chunks to score: %d (%d members with any genre coverage)' % (
    len(items), len(set(m for _, _, (m, g, c) in items))))

if '--limit' in sys.argv:
    items = items[:int(sys.argv[sys.argv.index('--limit') + 1])]
cache = {}
if os.path.exists(CACHE):
    for ln in open(CACHE, encoding='utf-8'):
        try:
            d = json.loads(ln); cache[d['k']] = d['out']
        except Exception: pass
todo = [it for it in items if it[0] not in cache]
BATCH = '--batch' in sys.argv   # Batches API: 50% off, <=1h turnaround — the efficient path for bulk scoring
_est = len(todo) * EST_PER_CALL * (0.5 if BATCH else 1.0) * (2.0 if FAST and not BATCH else 1.0)
print('BIG5-LLM[%s%s%s%s]: %d chunks | cached %d | to score %d | est ~$%.2f' % (
    MODEL, ' NAMED' if NAMED else '', ' BATCH(-50%)' if BATCH else '', ' FAST(x2)' if FAST and not BATCH else '',
    len(items), len(items) - len(todo), len(todo), _est))
if todo and '--go' not in sys.argv:
    print('COST GATE: add --go to spend.'); sys.exit(0)

if todo and BATCH:
    # Submit-or-resume: a pending-batch file survives interruption so a rerun NEVER resubmits paid work.
    import time
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    PEND = B + r'\big5_llm_batch_%s%s%s.json' % (_mk, '_named' if NAMED else '', '_rep%d' % REP if REP else '')
    if os.path.exists(PEND):
        bid = json.load(open(PEND))['batch_id']
        print('resuming pending batch %s' % bid)
    else:
        reqs = [Request(custom_id=k, params=MessageCreateParamsNonStreaming(**_params(u))) for k, u, _ in todo]
        try:
            bid = client.messages.batches.create(requests=reqs).id
        except Exception as e:
            # collector_supervisor historically retained stdout only, which turned submission
            # failures into an endless estimate-only loop.  Keep a permanent, credential-free
            # diagnostic on stdout even though the supervisor now captures stderr too.
            detail = str(e).replace('\r', ' ').replace('\n', ' ')[:1000]
            print('BATCH SUBMISSION FAILED [%s]: %s' % (type(e).__name__, detail), flush=True)
            raise
        with open(PEND, 'w') as _pf:   # QA 2026-07-07: closed handle — a truncated PEND made resume crash
            json.dump({'batch_id': bid, 'n': len(reqs)}, _pf)
        print('submitted batch %s (%d requests)' % (bid, len(reqs)))
    _poll_t0 = time.time()
    while True:
        b = client.messages.batches.retrieve(bid)
        if b.processing_status == 'ended': break
        if time.time() - _poll_t0 > 8.5 * 3600:   # QA 2026-07-07: bounded poll — PEND survives, a rerun resumes
            print('batch %s still %s after 8.5h — exiting; rerun resumes it (PEND kept)' % (bid, b.processing_status))
            sys.exit(1)
        print('  batch %s: %s (processing %d, succeeded %d)' % (
            bid, b.processing_status, b.request_counts.processing, b.request_counts.succeeded), flush=True)
        time.sleep(60)
    cf = io.open(CACHE, 'a', encoding='utf-8')
    ok = bad = 0
    for r in client.messages.batches.results(bid):
        if r.result.type == 'succeeded':
            try:
                txt = next(bl.text for bl in r.result.message.content if bl.type == 'text')
                out = json.loads(txt)
                cache[r.custom_id] = out
                cf.write(json.dumps({'k': r.custom_id, 'out': out}, ensure_ascii=True) + '\n')
                ok += 1
            except (StopIteration, json.JSONDecodeError): bad += 1
        else: bad += 1
    cf.close()
    os.remove(PEND)
    print('batch harvested: %d ok, %d failed (failed chunks stay in todo for a rerun)' % (ok, bad))
    todo = [it for it in todo if it[0] not in cache]
    if todo: print('  %d chunks unscored — rerun with --batch (or without, for direct top-up)' % len(todo))
    todo = []   # never fall through to the direct path in the same run

if todo:
    cf = io.open(CACHE, 'a', encoding='utf-8')
    done = errs = 0
    def work(it):
        k, u, meta = it
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
                    if done % 200 == 0: cf.flush()
                if done % 200 == 0 or done == len(todo): print('  %d/%d (%d errors)' % (done, len(todo), errs), flush=True)
    cf.close()
    if SPEED_N: print('  served: %s' % dict(SPEED_N), flush=True)   # fast-vs-standard split = actual price multiplier
    if errs > len(todo) * 0.1: print('!! high error rate — check credits before trusting coverage')

# ---- aggregate: item -> chunk trait -> genre -> member -> z ----
TR = ['O', 'C', 'E', 'A', 'ES']
bytr = {t: [(i, s) for i, s, tt, _ in ITEMS if tt == t] for t in TR}
gm = collections.defaultdict(lambda: collections.defaultdict(list))   # (member) -> genre -> [chunk trait dicts]
for k, u, (m, g, ci) in items:
    o = cache.get(k)
    if not o: continue
    if not isinstance(o, dict) or len(o) < 15:   # QA 2026-07-07: a truncated/corrupt cache line must not
        continue                                 # silently score as all-neutral (missing keys read as 0)
    ct = {t: sum(s * o.get(i, 0) for i, s in bytr[t]) / len(bytr[t]) for t in TR}
    gm[m][g].append(ct)
raw = {}; genre_scores = {}
# C genre-weighting — EARNED by the pre-registered interview-C retest PASS (2026-07-07: rho +0.155
# vs votes_cast bar +0.15, +0.112 vs follow-through bar +0.10, n=293; CREC contrast +0.106/+0.051).
# INTERVIEW carries the C criterion signal (the pilot's ~2x mechanism finding), so C aggregates
# interview chunks at 2x weight. The weight is the pilot's observed signal ratio, documented, NOT
# tuned against outcomes. Other traits keep plain chunk-count weighting. c_basis records the basis.
C_INT_W = 2.0
def _gw(gs, g, t): return len(gs[g]) * (C_INT_W if (t == 'C' and g == 'INTERVIEW') else 1.0)
for m, gs in gm.items():
    pergenre = {g: {t: sum(c[t] for c in cl) / len(cl) for t in TR} for g, cl in gs.items() if cl}
    genre_scores[m] = {g: {**v, 'n_chunks': len(gs[g])} for g, v in pergenre.items()}
    raw[m] = {t: sum(pergenre[g][t] * _gw(gs, g, t) for g in pergenre) /
                 sum(_gw(gs, g, t) for g in pergenre) for t in TR}
if raw:
    mu = {t: sum(d[t] for d in raw.values()) / len(raw) for t in TR}
    sd = {t: (sum((d[t] - mu[t]) ** 2 for d in raw.values()) / max(1, len(raw) - 1)) ** .5 or 1.0 for t in TR}
    # P6 coverage-GATED empirical-Bayes shrinkage — PASSED round-3 prereg @1967b15 (2026-07-07):
    # SB mean .748 -> .831 on the frozen snapshot with n>=8 members untouched BY CONSTRUCTION
    # (P4's ungated version failed exactly there). k per trait = method-of-moments from
    # within-member between-chunk variance; only n_chunks<8 members shrink toward 0; raw z kept.
    _ks = {}
    for _t in TR:
        _w, _mn, _ns = [], [], []
        for _m, _gs in gm.items():
            _a = [c[_t] for _g in _gs for c in gm[_m][_g]]
            if len(_a) >= 2:
                _mu2 = sum(_a) / len(_a)
                _w.append(sum((x - _mu2) ** 2 for x in _a) / (len(_a) - 1))
            _mn.append(sum(_a) / len(_a)); _ns.append(len(_a))
        _s2w = sum(_w) / max(1, len(_w)); _nbar = sum(_ns) / max(1, len(_ns))
        _mm = sum(_mn) / len(_mn)
        _s2b = max(1e-9, sum((x - _mm) ** 2 for x in _mn) / max(1, len(_mn) - 1) - _s2w / _nbar)
        _ks[_t] = _s2w / _s2b
    def _shrink(m, t, z):
        n = sum(len(gm[m][g]) for g in gm[m])
        return z * (n / (n + _ks[t])) if n < 8 else z
    traits = {m: {**{t: round(_shrink(m, t, (raw[m][t] - mu[t]) / sd[t]), 3) for t in TR},
                  'N': round(-_shrink(m, 'ES', (raw[m]['ES'] - mu['ES']) / sd['ES']), 3),
                  'raw_z': {t: round((raw[m][t] - mu[t]) / sd[t], 3) for t in TR},
                  'shrunk': sum(len(gm[m][g]) for g in gm[m]) < 8,
                  'n_chunks': sum(len(gm[m][g]) for g in gm[m]),
                  'c_basis': 'interview-weighted' if 'INTERVIEW' in genre_scores.get(m, {}) else 'crec-only'} for m in raw}
    print('P6 gated shrinkage: %d of %d members shrunk (n<8) | k: %s' % (
        sum(1 for m in raw if sum(len(gm[m][g]) for g in gm[m]) < 8), len(raw),
        ' '.join('%s %.1f' % (t, _ks[t]) for t in TR)))
    if os.environ.get('B5_NO_DUMP')=='1':
        print('B5_NO_DUMP=1: cache warmed (%d rows), artifact untouched' % len(cache)); sys.exit(0)
    from moneyball_core import dump_atomic
    dump_atomic({'traits': traits, 'genre_scores': genre_scores,
                 'method': 'LLM item-level (%s, itemset %s, %s), 3-pt observer-keyed IPIP-style, '
                           'deidentified=%s, chunk %dw x <=%d/genre, z within scored pool'
                           % (MODEL, ITEMSET_V, 'named' if NAMED else 'deid', not NAMED, CHUNK_WORDS, CHUNKS_PER_GENRE),
                 'raw_means': mu, 'raw_sds': sd}, OUT)
    print('wrote %s (%d members scored)' % (OUT, len(traits)))
    # quick variance audit (research: reject if >40% of members within +/-0.25 SD of median)
    for t in TR:
        vals = sorted(traits[m][t] for m in traits)
        med = vals[len(vals) // 2]
        frac = sum(1 for v in vals if abs(v - med) <= 0.25) / len(vals)
        print('  %-3s within +/-0.25SD of median: %.0f%% %s' % (t, 100 * frac, '(COMPRESSED)' if frac > 0.4 else ''))
