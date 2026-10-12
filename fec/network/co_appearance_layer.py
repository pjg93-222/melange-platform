# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""CO-APPEARANCE LAYER (user 2026-07-05: relational layer should capture "if they appear in
public with someone"). Two members who each gave a SUBSTANTIAL on-air appearance in the same
interview episode co-appeared on that broadcast — a chosen public pairing.

Design guards (calibrated on the live corpus 2026-07-05):
  * episode = package with any '/segment/NN' suffix stripped + date (CNN splits one broadcast
    into segment pages; unmerged, real episodes fragment and clip montages double-count)
  * APPEARANCE FLOOR: a member counts as appearing only with >=4 turns OR >=200 words in the
    episode — played soundbite clips (86% of member-episode rows) fail this; sit-down guests
    always clear it. Without the floor the top "pairs" were leadership clip-montages
    (Jeffries<->Johnson n=176 = shows playing both clips, not joint appearances).
  * episodes with >8 qualifying members skipped as events (debates/town halls), not pairings
  * INTERVIEW source only — CHRG co-location is the job, not a chosen pairing
Output data/network/co_appearance.json {edges:[{a,b,n,programs{},last,bipartisan}]} — undirected, n>=1 kept;
the dashboard bundle draws n>=2 (repeat pairings). Party from member_desk_bundle.json."""
import json, re, sqlite3, sys, collections, itertools, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
party = {}
mdb = B + r'\member_desk_bundle.json'
if os.path.exists(mdb):
    for rec in json.load(open(mdb, encoding='utf-8')).get('members', []):
        if rec.get('name') in C: party[rec['name']] = rec.get('party') or '?'

def epkey(p): return re.sub(r'/segment/\d+.*$', '', p or '')
def slug(p):
    m = re.search(r'/show/([^/]+)/', p or '')
    if m: return m.group(1)
    if 'face-the-nation' in (p or ''): return 'face-the-nation'
    m = re.search(r'https?://(?:www\.)?([^/]+)', p or '')
    return m.group(1) if m else (p or '')[:40]

con = sqlite3.connect(B + r'\social_media.db', timeout=60)
con.execute('PRAGMA query_only=1')
app = collections.defaultdict(lambda: [0, 0])   # (member, episode-key, date) -> [turns, words]
for m, p, d, n, w in con.execute("select member, package, date, count(*), sum(nwords) "
                                 "from speeches where source='INTERVIEW' group by member, package, date"):
    if m in C and p and d:
        a = app[(m, epkey(p), str(d))]; a[0] += n; a[1] += (w or 0)
con.close()

epi = collections.defaultdict(set)   # (episode-key, date) -> {members with a substantial appearance}
for (m, ek, d), (n, w) in app.items():
    if n >= 4 or w >= 200: epi[(ek, d)].add(m)

edges = collections.defaultdict(lambda: {'n': 0, 'programs': collections.Counter(), 'last': ''})
skipped_events = 0
for (ek, d), mem in epi.items():
    if len(mem) < 2: continue
    if len(mem) > 8: skipped_events += 1; continue
    for a, b in itertools.combinations(sorted(mem), 2):
        e = edges[(a, b)]
        e['n'] += 1; e['programs'][slug(ek)] += 1
        if d > e['last']: e['last'] = d

from moneyball_core import dump_atomic
out = [{'a': a, 'b': b, 'n': e['n'], 'programs': dict(e['programs']), 'last': e['last'],
        'bipartisan': bool(party.get(a) and party.get(b) and party[a] != party[b] and '?' not in (party[a], party[b]))}
       for (a, b), e in sorted(edges.items(), key=lambda kv: -kv[1]['n'])]
dump_atomic({'edges': out, 'episodes_with_2plus': sum(1 for v in epi.values() if len(v) >= 2),
             'events_skipped_gt8': skipped_events,
             'method': 'undirected co-appearance: members each clearing the substantial-appearance floor '
                       '(>=4 turns OR >=200 words) in the same interview episode (segment-merged package+date); '
                       'INTERVIEW only; >8-member episodes skipped as events; measures shared-broadcast'},
            B + r'\data\network\co_appearance.json')
bip = sum(1 for e in out if e['bipartisan'])
rep = sum(1 for e in out if e['n'] >= 2)
print('co-appearance: %d pairs (%d bipartisan, %d repeat n>=2), %d co-episodes (%d events skipped)'
      % (len(out), bip, rep, sum(1 for v in epi.values() if len(v) >= 2), skipped_events))
for e in out[:6]:
    print('  %s <-> %s  n=%d  %s%s' % (e['a'], e['b'], e['n'], '/'.join(list(e['programs'])[:3]),
                                       '  [bipartisan]' if e['bipartisan'] else ''))
