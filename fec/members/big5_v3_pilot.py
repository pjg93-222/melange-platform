# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""V3P — judge-level pilot for the pink A-ES halo (big5_round3_prereg.md Addendum 2, @3894fe6).
P2/P7 proved halo and C-signal are inseparable in the SCORES; v3 intervenes at the JUDGE:
 (a) SYSTEM gains an explicit item-independence (anti-halo) instruction;
 (b) ES items S1/S4 rewritten to composure-under-challenge framing (warmth-independent);
 (c) everything else lockstep with big5_llm v2 (chunking, de-id, scale, model, effort).
PILOT: the 50 highest-coverage members with BOTH genres, up to 8 chunks each (~$25 standard).
Bars (one look, paired vs the SAME members' v2 production scores):
  pilot A-ES |r| <= 0.50  AND  >= 0.10 below the same-members' v2 A-ES
  SB(A) >= 0.55 and SB(ES) >= 0.55 on pilot even/odd halves
  C(INT) vs votes_cast Spearman >= +0.10 (directional; pilot n ~50)
PASS -> full v3 re-score (~$158 batch) surfaced to the user as a funded go/no-go.
MISS -> itemset v2 stands; the judge-level lever is documented as tried.
Usage: python fec/members/big5_v3_pilot.py [--go]   (cost gate prints first)"""
import json, re, math, hashlib, sqlite3, collections, sys, io, threading
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import anthropic
B = MB_ROOT
MODEL = 'claude-opus-4-8'
CHUNK_WORDS, MIN_WORDS, PER_GENRE, NMEM = 1200, 2000, 4, 50
ITEMSET_V = 'v3pilot'

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
 # v3 ES: composure-under-challenge framing, explicitly warmth-independent (S1/S4 rewritten; S2/S3 kept)
 ('S1', +1, 'ES', "Stays composed and steady when directly challenged, criticized, or under hostile questioning "
                  "(judge composure only - a person can be cold yet composed, or warm yet volatile)"),
 ('S2', -1, 'ES', "Expresses PERSONAL distress, being overwhelmed, or defensiveness (policy alarm/advocacy does not count)"),
 ('S3', -1, 'ES', "Shows irritation, frustration, or anger"),
 ('S4', +1, 'ES', "Reacts to setbacks, bad news, or opposition with measured steadiness rather than agitation "
                  "(independent of how friendly or unfriendly they are)"),
]
SYSTEM = ("You are a personality-assessment rater. You will read a text sample by ONE author "
          "(a public official; identifying details are redacted). For each numbered item, judge whether "
          "the statement describes THE AUTHOR as evidenced by THIS TEXT ONLY.\n"
          "Answer each item: 1 = the text provides evidence FOR the statement; -1 = the text provides "
          "evidence AGAINST it; 0 = no evidence either way (most political text carries little "
          "personality signal - use 0 freely, never guess from the topic or presumed politics).\n"
          "RATE EACH ITEM STRICTLY ON ITS OWN EVIDENCE, INDEPENDENTLY OF THE OTHERS. Do not let an "
          "overall impression of the author color unrelated items: a person can be warm but volatile, "
          "cold but calm, energetic but careless. An answer pattern that simply repeats the same "
          "value for most items usually means the independence rule was not applied.\n"
          "Judge style and disposition, not policy content or ideology.\nItems:\n" +
          "\n".join("%s. %s" % (i, t) for i, _, _, t in ITEMS))
SCHEMA = {"type": "object", "properties": {i: {"type": "integer", "enum": [1, 0, -1]} for i, _, _, _ in ITEMS},
          "required": [i for i, _, _, _ in ITEMS], "additionalProperties": False}

# ---- lockstep corpus law (big5_llm) ----
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
    parts = [p for p in re.split(r'\s+', member) if len(p) > 2]
    for p in parts: text = re.sub(re.escape(p), '[NAME]', text, flags=re.I)
    text = TITLE_RE.sub('[TITLE]', text); text = PARTY_RE.sub('[PARTY]', text)
    for st in sorted(STATES, key=len, reverse=True):
        text = re.sub(r'\b' + re.escape(st) + r'\b', '[STATE]', text, flags=re.I)
    return text
def chunks_for(member, genre, con, cap):
    docs = [t for (t,) in con.execute(
        'select text from speeches where member=? and source=? order by date, rowid', (member, genre)) if t]
    words = []
    for t in docs:
        t = RITUAL.sub(' ', t); w = t.split()
        if len(w) >= 30: words.extend(w)
    if len(words) < MIN_WORDS: return []
    total = len(words); n = min(cap, total // CHUNK_WORDS)
    if n == 0: return []
    out = []
    for j in range(n):
        start = int(j * (total - CHUNK_WORDS) / max(1, n - 1)) if n > 1 else 0
        out.append(' '.join(words[start:start + CHUNK_WORDS]))
    return out

# ---- pilot roster: 50 highest-coverage members with BOTH genres (deterministic) ----
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
con = sqlite3.connect(B + r'\social_media.db', timeout=30); con.execute('PRAGMA query_only=1')
cov = []
for m in C:
    ws = {g: sum((len(RITUAL.sub(' ', t or '').split()) for (t,) in
          con.execute('select text from speeches where member=? and source=?', (m, g))), 0)
          for g in ('CREC', 'INTERVIEW')}
    if ws['CREC'] >= MIN_WORDS and ws['INTERVIEW'] >= MIN_WORDS:
        cov.append((min(ws['CREC'], ws['INTERVIEW']), m))
cov.sort(reverse=True)
pilot = [m for _, m in cov[:NMEM]]
print('pilot roster: %d members (both genres, highest balanced coverage)' % len(pilot))

items = []
for m in pilot:
    for g in ('CREC', 'INTERVIEW'):
        for ci, ch in enumerate(chunks_for(m, g, con, PER_GENRE)):
            txt = deidentify(ch, m)
            k = hashlib.md5(('%s|%s|%d|dei|%s|opus|%s' % (m, g, ci, ITEMSET_V,
                 hashlib.md5(txt.encode('utf-8', 'replace')).hexdigest()[:16])).encode()).hexdigest()
            items.append((k, 'Text sample:\n"""\n%s\n"""' % txt, (m, g, ci)))
con.close()
CACHE = B + r'\data\members\big5_v3pilot_cache.jsonl'
cache = {}
if _o.path.exists(CACHE):
    for ln in open(CACHE, encoding='utf-8'):
        try: d = json.loads(ln); cache[d['k']] = d['out']
        except Exception: pass
todo = [it for it in items if it[0] not in cache]
print('V3 PILOT: %d chunks | cached %d | to score %d | est ~$%.2f' % (
    len(items), len(items) - len(todo), len(todo), len(todo) * 0.075))
if todo and '--go' not in sys.argv:
    print('COST GATE: add --go to spend.'); sys.exit(0)

if todo:
    key = open(B + r'\fec\anthropic_key.txt').read().strip()
    client = anthropic.Anthropic(api_key=key)
    lock = threading.Lock()
    def _params(user):
        return dict(model=MODEL, max_tokens=4000,
                    output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
                    thinking={"type": "adaptive"},
                    system=[{"type": "text", "text": SYSTEM}],
                    messages=[{"role": "user", "content": user}])
    from concurrent.futures import ThreadPoolExecutor, as_completed
    cf = io.open(CACHE, 'a', encoding='utf-8')
    done = errs = 0
    def work(it):
        k, u, meta = it
        try:
            r = client.messages.create(**_params(u))
            txt = next(bl.text for bl in r.content if bl.type == 'text')
            return k, json.loads(txt), None
        except Exception as e:
            return k, None, str(e)[:120]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(work, it) for it in todo]):
            k, out, err = f.result()
            with lock:
                done += 1
                if err or not out: errs += 1
                else:
                    cache[k] = out
                    cf.write(json.dumps({'k': k, 'out': out}, ensure_ascii=True) + '\n')
                if done % 50 == 0 or done == len(todo):
                    print('  %d/%d (%d errors)' % (done, len(todo), errs), flush=True)
    cf.close()

# ---- evaluate the pilot bars ----
import numpy as np
TR = ['O', 'C', 'E', 'A', 'ES']
bytr = {t: [(i, s) for i, s, tt, _ in ITEMS if tt == t] for t in TR}
gm = collections.defaultdict(lambda: collections.defaultdict(list))
for k, u, (m, g, ci) in items:
    o = cache.get(k)
    if not o or len(o) < 15: continue
    ct = {t: sum(s * o.get(i, 0) for i, s in bytr[t]) / len(bytr[t]) for t in TR}
    gm[m][g].append((ci, ct))
names = sorted(gm)
raw = {}
half = {0: {}, 1: {}}
for m in names:
    pg = {g: {t: sum(c[t] for _, c in cl) / len(cl) for t in TR} for g, cl in gm[m].items()}
    w = {g: len(gm[m][g]) for g in pg}
    tot = sum(w.values())
    raw[m] = {t: sum(pg[g][t] * w[g] for g in pg) / tot for t in TR}
    for par in (0, 1):
        ph = {g: [c for ci, c in cl if ci % 2 == par] for g, cl in gm[m].items()}
        ph = {g: v for g, v in ph.items() if v}
        if ph:
            wt = sum(len(v) for v in ph.values())
            half[par][m] = {t: sum(sum(c[t] for c in v) / len(v) * len(v) for v in ph.values()) / wt for t in TR}
def z(d):
    mu = {t: np.mean([d[m][t] for m in d]) for t in TR}
    sd = {t: np.std([d[m][t] for m in d], ddof=1) or 1 for t in TR}
    return {m: {t: (d[m][t] - mu[t]) / sd[t] for t in TR} for m in d}
Z = z(raw)
def pear(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 and a.std() and b.std() else 0.0
aes_v3 = abs(pear([Z[m]['A'] for m in names], [Z[m]['ES'] for m in names]))
# paired v2 control: SAME members from the production artifact (raw_z to avoid P6 shrink effects; pilot members are high-coverage so unshrunk anyway)
ART = json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8'))['traits']
v2A = [ART[m].get('raw_z', ART[m])['A'] if isinstance(ART[m].get('raw_z'), dict) else ART[m]['A'] for m in names if m in ART]
v2E = [ART[m].get('raw_z', ART[m])['ES'] if isinstance(ART[m].get('raw_z'), dict) else ART[m]['ES'] for m in names if m in ART]
aes_v2 = abs(pear(v2A, v2E))
both = [m for m in names if m in half[0] and m in half[1]]
sb = {}
for t in ('A', 'ES'):
    r = pear([half[0][m][t] for m in both], [half[1][m][t] for m in both])
    sb[t] = 2 * r / (1 + r) if r > -1 else -1
PAN = json.load(open(B + r'\behavior_panel.json', encoding='utf-8'))['members']
ci_pairs = []
for m in names:
    if 'INTERVIEW' in gm[m] and PAN.get(m, {}).get('votes_cast') is not None:
        cint = sum(c['C'] for _, c in gm[m]['INTERVIEW']) / len(gm[m]['INTERVIEW'])
        ci_pairs.append((cint, PAN[m]['votes_cast']))
def spearman(pairs):
    if len(pairs) < 3: return None
    def rk(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); out = [0.0] * len(v); i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]: j += 1
            for kk in range(i, j + 1): out[order[kk]] = (i + j) / 2 + 1
            i = j + 1
        return out
    return pear(rk([p[0] for p in pairs]), rk([p[1] for p in pairs]))
rC = spearman(ci_pairs)
print('\n===== V3 PILOT RESULTS (n=%d members, %d chunks scored) =====' % (len(names), sum(len(cl) for m in gm for cl in gm[m].values())))
print('  A-ES |r|: v3 pilot %.3f  vs same-members v2 %.3f  (delta %+.3f)' % (aes_v3, aes_v2, aes_v3 - aes_v2))
print('  SB(A)=%.3f SB(ES)=%.3f | C(INT) vs votes Spearman %s (n=%d)' % (
    sb['A'], sb['ES'], '%+.3f' % rC if rC is not None else 'n/a', len(ci_pairs)))
bars = [('A-ES <= 0.50', aes_v3 <= 0.50, '%.3f' % aes_v3),
        ('A-ES >= 0.10 below v2 (paired)', aes_v3 <= aes_v2 - 0.10, '%.3f vs %.3f' % (aes_v3, aes_v2)),
        ('SB(A) >= 0.55', sb['A'] >= 0.55, '%.3f' % sb['A']),
        ('SB(ES) >= 0.55', sb['ES'] >= 0.55, '%.3f' % sb['ES']),
        ('C(INT) vs votes >= +0.10', (rC or -9) >= 0.10, '%+.3f' % (rC or -9))]
for nm, hit, det in bars: print('  BAR %-32s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', det))
print('V3P VERDICT: %s' % ('PASS — full v3 re-score (~$158 batch) is a funded go/no-go for the user'
                           if all(b[1] for b in bars) else 'MISS — itemset v2 stands; judge-level lever documented'))
