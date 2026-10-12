# -*- coding: utf-8 -*-
"""Comfort-curve prep (2026-07-11, user idea): for members with BOTH hostile-to-them and friendly-to-them
platform appearances, extract their responses on EACH side separately. Haiku scores reactivity on each;
the DIFFERENCE (hostile - friendly) = how much the member destabilizes under hostile-venue pressure. This
within-member difference DIFFERENCES OUT baseline expressiveness (Extraversion), which is what contaminated
the full-corpus LLM-N (+0.14 with E). Platform lean: msnow/MSNBC=D, hugh-hewitt/fox/hannity/cruz-slugs=R,
CNN/PBS/NPR=center-left, C-SPAN/network-Sunday=neutral; comfort = member-party vs outlet-lean alignment."""
import os, sys, json, sqlite3, collections, re, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from paths import ROOT
B = ROOT
SCRATCH = os.environ.get('MONEYBALL_RESEARCH_DIR',os.path.join(B,'outputs','research'))
CAP = 9000
LEAN = {'msnow': -1, 'msnbc': -1, 'cnr': -.4, 'cnc': -.4, 'sitroom': -.4, 'acd': -.4, 'ctmo': -.4, 'nday': -.4, 'ip': -.4,
        'sotu': -.4, 'cnnt': -.4, 'ath': -.4, 'ndaysat': -.4, 'ndaysun': -.4, 'pbs-newshour': -.3,
        'npr-all-things-considered': -.3, 'npr-morning-edition': -.3, 'meet-the-press': 0, 'face-the-nation': 0,
        'this-week': 0, 'washington-journal': 0, 'hugh-hewitt': 1, 'fox-news-sunday': 1, 'fox-news-sunday-on': 1}
def lean(o):
    if o in LEAN: return LEAN[o]
    if re.search(r'hannity|fox|cruz|kelly-file|brit-hume|special-report|carlson', o, re.I): return 1
    if re.search(r'msnbc|maddow', o, re.I): return -1
    return 0

def run():
    Cj = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}; bio = {m: Cj[m].get('bioguide') for m in Cj}
    PARTY = {}
    for r in csv.DictReader(open(B + r'\fec\legislators.csv', encoding='utf-8')):
        if r.get('bioguide_id'): PARTY[r['bioguide_id']] = (r.get('party') or '')[:1]
    pty = {m: PARTY.get(bio.get(m), '?') for m in Cj}
    con = sqlite3.connect(B + r'\social_media.db', timeout=60); con.execute('PRAGMA query_only=1')
    side = collections.defaultdict(lambda: {'hostile': [], 'friendly': []})
    for m, o, t in con.execute("SELECT member, chamber, text FROM speeches WHERE source='INTERVIEW'"):
        if not t or len(t.split()) < 8: continue
        ps = -1 if pty.get(m) == 'D' else 1 if pty.get(m) == 'R' else 0
        if ps == 0: continue
        al = ps * lean(o)
        if al < 0: side[m]['hostile'].append(t)
        elif al > 0: side[m]['friendly'].append(t)
    con.close()
    items = []
    for m, sd in side.items():
        if len(sd['hostile']) < 6 or len(sd['friendly']) < 6: continue
        for s in ('hostile', 'friendly'):
            turns = sd[s]; text = ''; step = max(1, len(turns) // 30)
            for t in turns[::step]:
                if len(text) + len(t) > CAP: break
                text += t.strip() + '\n---\n'
            items.append({'member': m, 'side': s, 'text': text[:CAP]})
    cdir = os.path.join(SCRATCH, 'comfort_chunks'); os.makedirs(cdir, exist_ok=True)
    for f in os.listdir(cdir): os.remove(os.path.join(cdir, f))
    CH = 6; n = 0
    for i in range(0, len(items), CH):
        json.dump(items[i:i + CH], open(os.path.join(cdir, 'chunk_%03d.json' % n), 'w', encoding='utf-8')); n += 1
    print('comfort-curve prep: %d members x2 sides = %d items -> %d chunks' % (len(items) // 2, len(items), n))

if __name__ == '__main__':
    run()
