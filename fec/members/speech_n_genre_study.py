# -*- coding: utf-8 -*-
"""Speech-genre N estimate study (Paul-authorized 2026-07-17; bar PRE-DECLARED in
FIX_CAMPAIGN_LOG_2026-07-16.md entry 009 BEFORE any scoring).

QUESTION: may a floor/hearing-speech affective read fill the N display for interview-desert members?
METHOD (frozen): round-7b facet definitions verbatim (only the source-description sentence names
floor/hearing remarks), Haiku temp0, prep construction mirrored (>=8-word rows, step-sample,
CAP 12000); composite pess+anx+dep+vol, E-residualized + party-controlled + z (mirror of
emit_affective_n). BAR (one look): Spearman rho >= 0.40 vs the interview-based affective_n values
on dual-coverage members. PASS -> desert percentiles ship as labeled display estimates
(source_data/speech_n_estimates.json). FAIL -> nothing ships; the number is logged; route closed.

Usage: python fec/members/speech_n_genre_study.py --score     (resumable Haiku pass)
       python fec/members/speech_n_genre_study.py --evaluate  (ONE look; refuses a second)
"""
import os, sys, json, sqlite3, collections, time, math
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from paths import ROOT as B
from moneyball_core import dump_atomic

SCORES = os.path.join(B, 'source_data', 'speech_n_affective_study.json')
RESULT = os.path.join(B, 'source_data', 'speech_n_genre_result.json')
SHIP = os.path.join(B, 'source_data', 'speech_n_estimates.json')
MODEL = 'claude-haiku-4-5-20251001'
CAP = 12000
BAR = 0.40

RUBRIC = """You score facets of NEUROTICISM from a sample of a US member of Congress's floor-speech and committee-hearing remarks.
Neuroticism = the tendency toward negative affect. Score the member's PERSONAL emotional tone and content, NOT
their policy views, party, or how animated/talkative they are (that's Extraversion, which you must IGNORE here).

CRITICAL: distinguish PERSONAL affect from STRATEGIC MESSAGING. "The other party is destroying the country"
is a partisan talking point, NOT personal pessimism. Score genuine, self-referential negative affect: worry
they seem to actually feel, bleakness in their worldview, sadness/depletion in tone, emotional instability.
An animated optimist scores LOW even if loud; a calm person voicing genuine dread/hopelessness scores HIGH.

- pessimism (0-100): personally bleak/fatalistic outlook, catastrophizing, expecting the worst, no hope.
- anxiety (0-100): worry, fear, apprehension, dread, on-edge; frets, second-guesses, anticipates threats.
- depression (0-100): sadness, defeatism, exhaustion, overwhelmed, drained/low-energy affect.
- volatility (0-100): sudden emotional swings, mood shifts within the exchange, affective inconsistency.
- reactive (0-100): gets rattled/defensive/heated when challenged (comparison facet).
Score every member, echo the member name EXACTLY. Reply with JSON only:
{"scores":[{"member":"...","pessimism":0,"anxiety":0,"depression":0,"volatility":0,"reactive":0,"confidence":"low|medium|high"}]}"""


def key():
    k = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if k: return k
    p = os.path.join(B, 'fec', 'anthropic_key.txt')
    return open(p, encoding='utf-8').read().strip() if os.path.exists(p) else ''


def populations():
    aff = json.load(open(os.path.join(B, 'data', 'money', 'affective_n.json'), encoding='utf-8'))['members']
    bundle = json.load(open(os.path.join(B, 'member_desk_bundle.json'), encoding='utf-8'))['members']
    missing_N = set()
    for m in bundle:
        p = m.get('psy') or {}
        g = (p.get('big5_pct') or [None] * 5); f = (p.get('big5_fb_pct') or [None] * 5)
        if g[4] is None and f[4] is None: missing_N.add(m['name'])
    con = sqlite3.connect(B + r'\social_media.db', timeout=60); con.execute('PRAGMA query_only=1')
    bym = collections.defaultdict(list)
    for mname, t in con.execute("SELECT member, text FROM speeches WHERE source IN ('CREC','CHRG')"):
        if t and len(t.split()) >= 8: bym[mname].append(t)
    con.close()
    words = {m: sum(len(t.split()) for t in ts) for m, ts in bym.items()}
    dual = sorted(m for m in aff if words.get(m, 0) >= 3000)
    desert = sorted(m for m in missing_N if words.get(m, 0) >= 3000)
    texts = {}
    for m in set(dual) | set(desert):
        turns = bym[m]; text = ''; step = max(1, len(turns) // 45)
        for t in turns[::step]:
            if len(text) + len(t) > CAP: break
            text += t.strip() + '\n---\n'
        texts[m] = text[:CAP]
    return aff, dual, desert, texts


def score():
    S = json.load(open(SCORES, encoding='utf-8')) if os.path.exists(SCORES) else {}
    aff, dual, desert, texts = populations()
    todo = [m for m in dual + desert if m not in S]
    print('dual-coverage=%d desert=%d | already scored=%d | todo=%d' % (len(dual), len(desert), len(S), len(todo)))
    if not todo: return
    import anthropic
    client = anthropic.Anthropic(api_key=key())
    for i in range(0, len(todo), 4):
        chunk = todo[i:i + 4]
        body = '\n\n'.join('=== MEMBER: %s ===\n%s' % (m, texts[m]) for m in chunk)
        for attempt in range(3):
            try:
                r = client.messages.create(model=MODEL, max_tokens=1500, temperature=0, system=RUBRIC,
                    messages=[{'role': 'user', 'content': 'Score each member from their floor/hearing remarks below.\n\n' + body}])
                txt = r.content[0].text.strip(); txt = txt[txt.index('{'):txt.rindex('}') + 1]
                for s in json.loads(txt)['scores']:
                    m = s.get('member')
                    if m in chunk:
                        facets = {k2: s.get(k2) for k2 in ('pessimism', 'anxiety', 'depression', 'volatility', 'reactive', 'confidence')}
                        # A missing facet used to be stored as None and then crashed
                        # evaluate() with float(None); leave the member unscored instead.
                        if any(v is None for v in facets.values()):
                            print('  !! %s: incomplete facet set, left unscored' % m, flush=True)
                            continue
                        S[m] = facets
                break
            except Exception as e:
                print('  chunk %d retry %d: %s' % (i // 4 + 1, attempt + 1, str(e)[:110]), flush=True)
                time.sleep(4 * (attempt + 1))
        if (i // 4) % 10 == 0:
            dump_atomic(S, SCORES, ensure_ascii=False, indent=1)
            print('  progress %d/%d scored' % (len(S), len(dual) + len(desert)), flush=True)
        time.sleep(0.3)
    dump_atomic(S, SCORES, ensure_ascii=False, indent=1)
    print('scored %d members -> %s' % (len(S), SCORES))


def evaluate():
    if os.path.exists(RESULT):
        raise SystemExit('LOOK ALREADY CONSUMED: %s exists (one-look discipline)' % RESULT)
    S = json.load(open(SCORES, encoding='utf-8'))
    aff, dual, desert, _ = populations()
    A5 = json.load(open(B + r'\data\members\big5_v3_hybrid.json'))['traits']
    import csv as _csv
    Cj = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
    PP = {}
    for r in _csv.DictReader(open(B + r'\fec\legislators.csv', encoding='utf-8')):
        if r.get('bioguide_id'): PP[r['bioguide_id']] = (r.get('party') or '')[:1]
    def resid(y, x):
        X = np.column_stack([np.ones(len(y)), x]); return y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
    pool = [m for m in (dual + desert) if m in S and A5.get(m, {}).get('E') is not None]
    comp = np.array([sum(float(S[m][k]) for k in ('pessimism', 'anxiety', 'depression', 'volatility')) for m in pool])
    Ev = np.array([A5[m]['E'] for m in pool], float)
    Rp = np.array([1.0 if PP.get(Cj.get(m, {}).get('bioguide')) == 'R' else 0.0 for m in pool])
    z = resid(resid(comp, Ev), Rp); z = (z - z.mean()) / (z.std() or 1)
    zmap = {m: float(z[i]) for i, m in enumerate(pool)}
    dd = [m for m in dual if m in zmap]
    xs = [zmap[m] for m in dd]; ys = [aff[m] for m in dd]
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); rk = [0] * len(v)
        for r2, i in enumerate(order): rk[i] = r2
        return rk
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(rx)))
    rho = num / math.sqrt(sum((x - mx) ** 2 for x in rx) * sum((y - my) ** 2 for y in ry))
    verdict = 'PASS' if rho >= BAR else 'FAIL'
    print('SPEECH-N GENRE LOOK: n_dual=%d rho=%.3f bar=%.2f -> %s' % (len(dd), rho, BAR, verdict))
    dump_atomic({'rho': round(rho, 4), 'n_dual': len(dd), 'bar': BAR, 'verdict': verdict,
                 'declared': 'FIX_CAMPAIGN_LOG_2026-07-16.md entry 009 (pre-scoring)',
                 'method': 'speech-genre round-7b facets, Haiku temp0, E-resid+party-controlled composite'}, RESULT, indent=1)
    if verdict == 'PASS':
        ranks = sorted(zmap.values())
        import bisect
        pct = {m: round(bisect.bisect_left(ranks, zmap[m]) / (len(ranks) - 1), 3) for m in desert if m in zmap}
        dump_atomic({'members': pct, 'rho': round(rho, 4), 'n_dual': len(dd),
                     'label': 'speech-based N estimate (genre-extension; convergence rho=%.2f)' % rho}, SHIP, indent=1)
        print('shipped %d desert estimates -> %s' % (len(pct), SHIP))
    else:
        print('bar FAILED: nothing ships; route closed pending a real prereg.')


if __name__ == '__main__':
    if '--score' in sys.argv: score()
    elif '--evaluate' in sys.argv: evaluate()
    else: print(__doc__)
