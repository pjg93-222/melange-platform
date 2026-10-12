# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""API pre-label pass over tweet_gold_queue.csv (user-directed 2026-07-03: "Do your run") — the
tweet-form sibling of api_prelabel.py. Same Burnham factorization and the SAME fixed CU priors, with
the tweet-specific pieces: full post text, tweet attribution classes (member-own | rt-endorsement |
third-party-voice), and the antibank discriminator taught explicitly.

SIDECAR DESIGN (first run lost 400 labels to a PermissionError — the queue was open in the user's
Excel): results ALWAYS land atomically in tweet_gold_queue.API_RESULTS.csv first; the main queue is
then merged IF writable, else run `python fec/stance/api_prelabel_tweets.py --merge` after closing Excel.
Resume-safe from BOTH the main queue and the sidecar (a rerun after a failed merge costs $0).
gold_* cells are pre-seeded ONLY where empty — the user's adjudications are never overwritten.
Requires ANTHROPIC_API_KEY."""
import csv, io, json, os, sys, threading, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
QUEUE = B + r'\data\stance\tweet_gold_queue.csv'
SIDECAR = B + r'\data\stance\tweet_gold_queue.API_RESULTS.csv'
MODEL = "claude-opus-4-8"
WORKERS = 8
API_FIELDS = ['api_target', 'api_stance_toward_target', 'api_target_cu_valence',
              'api_cu_alignment', 'api_attribution', 'api_confidence', 'api_rationale']

SYSTEM = """You label social-media posts by members of Congress for a credit-union (CU) legislative-stance
model. You see the full post text. Work through the factorization, in order:
1. TARGET — the specific bill/agency/rule/practice the post takes a position on. Constituent service,
   ceremony, sports, condolences, pure campaign content = no policy target = UNRELATED.
2. STANCE TOWARD TARGET — does the member support, oppose, or stay neutral on that target?
3. TARGET'S CU VALENCE — is the target good (+1), bad (-1), or neutral (0) FOR CREDIT UNIONS?
   Fixed priors (institutional positions of the credit-union movement):
   * Expanding CFPB power/funding/enforcement reach = -1; restraining or reforming CFPB = +1.
   * Interchange caps, the Credit Card Competition Act, credit-card rate/fee mandates = -1;
     blocking or repealing them = +1.
   * Measures favoring large banks over CUs, or attacks on the CU tax status = -1;
     reinforcing CU tax status, charter/field-of-membership expansion = +1.
   * Fraud prevention, data-privacy standards CUs seek, CDFI funding, defense-CU access = +1.
   CRITICAL DISCRIMINATOR (the known failure mode you are here to avoid): generic anti-bank/populist
   rhetoric — "big banks", "Wall Street greed", "junk fees", cheering CFPB enforcement against banks —
   is NOT pro-credit-union by default. Bashing banks with no CU-relevant policy target = valence 0 ->
   alignment neutral. On interchange specifically, CUs are on the SAME side as banks: cheering an
   interchange cap by attacking "swipe fees" is alignment=oppose, not support. CFPB cheerleading is
   -1 when the action's reach covers CUs too.
4. CU ALIGNMENT — stance x valence. Supporting an anti-CU target = oppose. Opposing an anti-CU
   target = support. Neutral stance or valence-0 target = neutral.
5. ATTRIBUTION —
   * member-own: the member's own words (original post, or reply written by them).
   * rt-endorsement: a retweet/repost — amplification is adoption: label the member's IMPLIED position
     on the amplified content, UNLESS the quote/comment is clearly adversarial (then their stance is
     the opposition they express).
   * third-party-voice: the stance in the text belongs to someone else and the member's own position
     cannot be inferred -> alignment=unrelated.
Confidence: high = text clearly establishes target and direction; medium = one element inferred;
low = insufficient (prefer neutral/unrelated + low over a guessed direction). Rationale: one sentence."""

SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string", "description": "the specific bill/agency/practice the stance is about, or 'none'"},
        "stance_toward_target": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
        "target_cu_valence": {"type": "integer", "enum": [1, 0, -1]},
        "cu_alignment": {"type": "string", "enum": ["support", "oppose", "neutral", "unrelated"]},
        "attribution": {"type": "string", "enum": ["member-own", "rt-endorsement", "third-party-voice"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "rationale": {"type": "string"}
    },
    "required": ["target", "stance_toward_target", "target_cu_valence", "cu_alignment",
                 "attribution", "confidence", "rationale"],
    "additionalProperties": False,
}

def read_csv(path):
    with io.open(path, encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))

def write_csv_atomic(path, rows, cols):
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    os.replace(tmp, path)

def merge_into_queue(side_rows):
    """Fold sidecar api_* into a FRESH read of the main queue; pre-seed gold_* only where empty."""
    fresh = read_csv(QUEUE)
    cols = list(fresh[0].keys())
    byid = {r['id']: r for r in side_rows}
    napi = ngold = 0
    for r in fresh:
        s = byid.get(r['id'])
        if not s or not (s.get('api_cu_alignment') or '').strip(): continue
        for f in API_FIELDS:
            if f in cols and (s.get(f) or '').strip(): r[f] = s[f]; napi += 1
        for g, a in (('gold_target', 'api_target'), ('gold_stance_toward_target', 'api_stance_toward_target'),
                     ('gold_target_cu_valence', 'api_target_cu_valence'),
                     ('gold_cu_alignment', 'api_cu_alignment'), ('gold_attribution', 'api_attribution')):
            if g in cols and not (r.get(g) or '').strip() and (s.get(a) or '').strip():
                r[g] = s[a]; ngold += 1
    write_csv_atomic(QUEUE, fresh, cols)
    print('merged into tweet_gold_queue.csv: %d api cells, %d gold cells pre-seeded (user gold untouched)' % (napi, ngold))

if '--merge' in sys.argv:
    if not os.path.exists(SIDECAR): print('no sidecar to merge (%s missing)' % SIDECAR); sys.exit(1)
    try:
        merge_into_queue(read_csv(SIDECAR))
    except PermissionError:
        print('!! tweet_gold_queue.csv still locked (Excel open?) — close it and rerun --merge'); sys.exit(1)
    sys.exit(0)

import anthropic
from concurrent.futures import ThreadPoolExecutor, as_completed
client = anthropic.Anthropic()
lock = threading.Lock()

rows = read_csv(QUEUE)
COLS = list(rows[0].keys())
have = {}
if os.path.exists(SIDECAR):
    for r in read_csv(SIDECAR):
        if (r.get('api_cu_alignment') or '').strip(): have[r['id']] = r
    for r in rows:                                   # fold prior sidecar results into memory (free resume)
        s = have.get(r['id'])
        if s and not (r.get('api_cu_alignment') or '').strip():
            for f in API_FIELDS: r[f] = s.get(f, '')
todo = [r for r in rows if not (r.get('api_cu_alignment') or '').strip()]
print('queue rows: %d | already labeled: %d | to label: %d | model: %s' % (len(rows), len(rows) - len(todo), len(todo), MODEL))

def label(row):
    rt = str(row.get('is_rt') or '').lower() in ('1', 'true')
    user = (f"Member: {row['member']} ({row['party']})\nPlatform: {row.get('platform')}\n"
            f"Date: {row['date']}\nRetweet/repost: {'yes' if rt else 'no'}\n"
            f"Post text: {str(row.get('text') or '')[:1200]}")
    resp = client.messages.create(
        model=MODEL, max_tokens=3000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    txt = next(b.text for b in resp.content if b.type == "text")
    return json.loads(txt), resp.usage

def work(r):
    try:
        out, u = label(r)
        return r['id'], out, u, None
    except Exception as e:
        return r['id'], None, None, str(e)[:200]

done = errs = 0; usage_in = usage_out = 0
byid = {r['id']: r for r in rows}
if todo:
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, r) for r in todo]
        for f in as_completed(futs):
            rid, out, u, err = f.result()
            with lock:
                done += 1
                r = byid[rid]
                if err:
                    errs += 1; r['api_rationale'] = 'ERROR: ' + err
                else:
                    r['api_target'] = out['target']; r['api_stance_toward_target'] = out['stance_toward_target']
                    r['api_target_cu_valence'] = str(out['target_cu_valence']); r['api_cu_alignment'] = out['cu_alignment']
                    r['api_attribution'] = out['attribution']; r['api_confidence'] = out['confidence']
                    r['api_rationale'] = out['rationale']
                    usage_in += u.input_tokens; usage_out += u.output_tokens
                if done % 25 == 0 or done == len(todo):
                    print('  %d/%d labeled (%d errors)' % (done, len(todo), errs), flush=True)

write_csv_atomic(SIDECAR, rows, COLS)   # results are SAFE here no matter what happens next
print('sidecar written:', SIDECAR)
al = collections.Counter(r.get('api_cu_alignment') for r in rows
                         if (r.get('api_cu_alignment') or '').strip() and not str(r.get('api_rationale', '')).startswith('ERROR'))
att = collections.Counter(r.get('api_attribution') for r in rows if (r.get('api_attribution') or '').strip())
agree = sum(1 for r in rows if (r.get('api_cu_alignment') or '').strip() and (r.get('model_stance') or '').strip() == r.get('api_cu_alignment'))
n_lab = sum(1 for r in rows if (r.get('api_cu_alignment') or '').strip())
print('API alignment distribution:', dict(al))
print('attribution:', dict(att))
print('model_stance vs API agreement: %d/%d (%.0f%%)' % (agree, n_lab, 100 * agree / max(1, n_lab)))
print('tokens: %dk in / %dk out | errors %d' % (usage_in / 1000, usage_out / 1000, errs))
try:
    merge_into_queue(rows)
except PermissionError:
    print('!! tweet_gold_queue.csv is OPEN (Excel lock) — results are safe in the sidecar;')
    print('   close Excel and run:  python fec/stance/api_prelabel_tweets.py --merge')
