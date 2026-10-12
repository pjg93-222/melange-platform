# -*- coding: utf-8 -*-
"""Stage 1 of N re-anchoring investigation (2026-07-11): extend the LLM-N instrument from WJ-only to the
FULL interview corpus (all sources). Linchpin verified: reactivity_z tracks Extraversion (+0.185) not
Neuroticism (-0.139), so the engine N-slot is misanchored; LLM-N (Haiku read of emotional reactivity
under questioning) is face-valid + E-distinct. To even consider replacing reactivity_z, LLM-N needs its
coverage (272). This scores every member on their full interview corpus with ONE consistent prompt;
the 163 WJ-only scores stay separate (wj_n_labels.json) as a venue-robustness check (full-corpus vs
WJ-only agreement). Prompt focuses the LLM on how the member handles being PRESSED/CHALLENGED across
their interviews, so a friendly-venue corpus doesn't just read as 'calm'. NO engine change here."""
import os, sys, json, sqlite3, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from paths import ROOT
B = ROOT
SCRATCH = os.environ.get('MONEYBALL_RESEARCH_DIR',os.path.join(B,'outputs','research'))
CAP = 12000

def run():
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
    cdir = os.path.join(SCRATCH, 'interview_n_chunks'); os.makedirs(cdir, exist_ok=True)
    for f in os.listdir(cdir):
        os.remove(os.path.join(cdir, f))
    CH = 4; n = 0
    for i in range(0, len(items), CH):
        json.dump(items[i:i + CH], open(os.path.join(cdir, 'chunk_%03d.json' % n), 'w', encoding='utf-8'))
        n += 1
    print('interview-N prep: %d members (>=6 substantive interview turns) -> %d chunks in %s' % (len(items), n, cdir))

if __name__ == '__main__':
    run()
