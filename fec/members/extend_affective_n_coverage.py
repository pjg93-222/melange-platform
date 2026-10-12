# -*- coding: utf-8 -*-
"""Coverage-extension amendment for the affective-N facet input (Paul-authorized 2026-07-17
"Run all three"). METHOD-UNCHANGED extension of the round-7b promoted instrument: scores the
SAME four facets with the VERBATIM rubric + schema recovered from the original run
(interview-n-affective-facets-wf_d283ae83-8a9.js, session clever-elbakyan 2026-07-11), the same
text construction as interview_llm_n_prep.py (>=6 substantive turns, step-sampled, CAP 12000),
the same model (claude-haiku-4-5 — cost law), for ONLY the eligible members absent from
source_data/interview_n_affective.json. Existing entries are never touched. Transport differs
from the original (direct API, temperature 0, vs workflow agents) — a documented amendment note,
not a method change; facet scales/rubric/model identical.

Usage:  python fec/members/extend_affective_n_coverage.py [--dry-run]
Then:   AFFECTIVE_N_INPUT unset (default path) -> python fec/members/emit_affective_n.py
"""
import os, sys, json, sqlite3, collections, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT as B
from moneyball_core import dump_atomic

FACETS = os.path.join(B, 'source_data', 'interview_n_affective.json')
MODEL = 'claude-haiku-4-5'
CAP = 12000

RUBRIC = """You score facets of NEUROTICISM from a sample of a US member of Congress's media-interview responses.
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


def eligible_texts():
    """Reproduce interview_llm_n_prep.py's construction verbatim: >=6 substantive turns,
    step-sampled to CAP characters."""
    con = sqlite3.connect(B + r'\social_media.db', timeout=60); con.execute('PRAGMA query_only=1')
    rows = con.execute("SELECT member, text FROM speeches WHERE source='INTERVIEW' ORDER BY member").fetchall()
    con.close()
    bym = collections.defaultdict(list)
    for m, t in rows:
        if t and len(t.split()) >= 8:
            bym[m].append(t)
    items = []
    for m, turns in bym.items():
        if len(turns) < 6:
            continue
        text = ''; step = max(1, len(turns) // 45)
        for t in turns[::step]:
            if len(text) + len(t) > CAP: break
            text += t.strip() + '\n---\n'
        items.append({'member': m, 'text': text[:CAP]})
    items.sort(key=lambda x: x['member'])
    return items


def main():
    dry = '--dry-run' in sys.argv
    S = json.load(open(FACETS, encoding='utf-8'))
    covered = set(S)
    todo = [it for it in eligible_texts() if it['member'] not in covered]
    print('facet file: %d covered | eligible-but-uncovered: %d' % (len(covered), len(todo)))
    for it in todo: print('   +', it['member'])
    if dry or not todo:
        return
    import anthropic
    client = anthropic.Anthropic(api_key=key())
    added = 0
    for i in range(0, len(todo), 4):
        chunk = todo[i:i + 4]
        body = '\n\n'.join('=== MEMBER: %s ===\n%s' % (c['member'], c['text']) for c in chunk)
        for attempt in range(3):
            try:
                r = client.messages.create(
                    model=MODEL, max_tokens=1500, temperature=0,
                    system=RUBRIC,
                    messages=[{'role': 'user', 'content': 'Score each member from their interview responses below.\n\n' + body}])
                txt = r.content[0].text.strip()
                txt = txt[txt.index('{'):txt.rindex('}') + 1]
                got = json.loads(txt)['scores']
                for s in got:
                    m = s.get('member')
                    if m and any(c['member'] == m for c in chunk) and m not in S:
                        S[m] = {'member': m, 'pessimism': int(s['pessimism']), 'anxiety': int(s['anxiety']),
                                'depression': int(s['depression']), 'volatility': int(s['volatility']),
                                'reactive': int(s.get('reactive', 0)), 'confidence': s.get('confidence', 'low'),
                                'amendment': 'coverage-extension 2026-07-17 (Paul-authorized; verbatim round-7b rubric; direct-API temp0 transport)'}
                        added += 1
                print('  chunk %d/%d ok (running added=%d)' % (i // 4 + 1, (len(todo) + 3) // 4, added), flush=True)
                break
            except Exception as e:
                print('  chunk %d retry %d: %s' % (i // 4 + 1, attempt + 1, str(e)[:120]), flush=True)
                time.sleep(4 * (attempt + 1))
        time.sleep(0.4)
    dump_atomic(S, FACETS, ensure_ascii=False, indent=1)
    print('wrote %s: %d members (+%d new; original entries untouched)' % (FACETS, len(S), added))


if __name__ == '__main__':
    main()
