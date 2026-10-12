# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""VOTE SMART PCT/NPAT COLLECTOR (big5_es_votesmart_prereg.md, locked 2026-07-09 pre-collection).
Fetches, per current member with a votesmart_id (fec/legislators.csv), whether Vote Smart records a
completed Political Courage Test / NPAT (any cycle) — the §18-recorded sole remaining ES path,
user-gated and opened by Paul 2026-07-09.

Requires fec/votesmart_api_key.txt (GITIGNORED — a personal credential; register via
votesmart.org/share/api or webmaster@votesmart.org; academic use may qualify). Protocol per the
prereg: Npat.getNpat(candidateId) with JSON output, 0.5s politeness delay, single pass, resume
cache, failures recorded per member as fetch_failed (never guessed). Because the API schema cannot
be inspected before a key exists, each member's record stores a compact raw-shape summary; the
exact responded-field mapping is pinned in this artifact's method block AT COLLECTION TIME, before
any look (the prereg requires this ordering). Output: data/members/votesmart_npat.json. Read-only otherwise.
NO LOOK HAPPENS HERE — no instrument file is ever loaded by this script."""
import csv, json, os, sys, time, argparse, urllib.request, urllib.parse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from moneyball_core import dump_atomic
B = MB_ROOT
KEYF = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'votesmart_api_key.txt')
OUTF = B + r'\data\members\votesmart_npat.json'
API = 'https://api.votesmart.org/Npat.getNpat?%s'

def load_roster():
    C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
    b2m = {info.get('bioguide'): m for m, info in C.items() if info.get('bioguide')}
    out = {}
    with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            m = b2m.get(row.get('bioguide_id'))
            vs = (row.get('votesmart_id') or '').strip()
            if m and vs: out[m] = vs
    return out

def classify(payload):
    """Map one API response to (responded, cycles, note). ERROR TAXONOMY PINNED BY THE PREREG:
    responded=0 ONLY on a RECOGNIZED no-NPAT/empty-npat response; auth/quota/transport/unparseable
    => fetch_failed (responded None — retryable, never cached as done, never in the base rate).
    A quota hiccup must never silently manufacture non-responders."""
    import re as _re
    try:
        j = json.loads(payload)
    except Exception:
        return None, [], 'fetch_failed: unparseable'
    if isinstance(j, dict) and 'error' in j:
        err = str(j.get('error'))
        if 'npat' in err.lower() or 'no political courage' in err.lower():
            return 0, [], 'api-no-npat: %s' % err[:100]          # the one recognized 0
        return None, [], 'fetch_failed: api-error %s' % err[:100]  # auth/quota/anything else
    if not isinstance(j, dict):
        return None, [], 'fetch_failed: non-dict payload'
    np_ = j.get('npat')
    if np_ is None:
        return None, [], 'fetch_failed: unexpected-shape %s' % ','.join(sorted(j.keys()))[:80]
    if not np_:
        return 0, [], 'empty-npat-object'
    keys = sorted(np_.keys()) if isinstance(np_, dict) else []
    has = any(k for k in keys if k.lower() in ('section', 'sections', 'survey', 'instance', 'instances'))
    cycles = sorted(set(_re.findall(r'\b(19|20)\d{2}\b', json.dumps(np_)[:20000])))[:12]
    return (1 if has else 0), cycles, 'npat-keys:' + ','.join(keys)[:140]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='smoke-run cap (0 = full roster)')
    a = ap.parse_args()
    if not os.path.exists(KEYF):
        print('!! NO API KEY: put your Vote Smart API key (one line) in fec/votesmart_api_key.txt')
        print('   (register: votesmart.org/share/api or webmaster@votesmart.org — gitignored, never committed)')
        return
    key = open(KEYF, encoding='utf-8').read().strip()
    roster = load_roster()
    prev = (json.load(open(OUTF, encoding='utf-8')) if os.path.exists(OUTF) else {}).get('members', {})
    out = dict(prev)
    # retry semantics (prereg): fetch_failed members stay retryable until the COLLECTION RECORD
    # addendum freezes the artifact; only definitive 0/1 results are done.
    todo = [m for m in roster if not (out.get(m) or {}).get('done')]
    if a.limit: todo = todo[:a.limit]
    print('Vote Smart NPAT fetch: %d members with votesmart_id | %d to fetch (resume cache %d done)'
          % (len(roster), len(todo), sum(1 for v in out.values() if v.get('done'))))
    for i, m in enumerate(todo):
        q = urllib.parse.urlencode({'key': key, 'candidateId': roster[m], 'o': 'JSON'})
        try:
            req = urllib.request.Request(API % q, headers={'User-Agent': 'moneyball-research/1.0 (academic; goff0070@umn.edu)'})
            payload = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')
            responded, cycles, note = classify(payload)
            out[m] = {'votesmart_id': roster[m], 'responded': responded, 'cycles': cycles, 'note': note,
                      'done': responded is not None}
        except Exception as e:
            out[m] = {'votesmart_id': roster[m], 'responded': None, 'cycles': [], 'note': 'fetch_failed: %s' % type(e).__name__, 'done': False}
        if (i + 1) % 25 == 0:
            print('  %d/%d ...' % (i + 1, len(todo)))
            dump_atomic({'members': out, 'method': 'PARTIAL (in progress)'}, OUTF)
        time.sleep(0.5)
    n1 = sum(1 for v in out.values() if v.get('responded') == 1)
    n0 = sum(1 for v in out.values() if v.get('responded') == 0)
    nf = sum(1 for v in out.values() if v.get('responded') is None)
    dump_atomic({'members': out,
                 'method': 'Vote Smart Npat.getNpat per candidateId (JSON), 0.5s delay, resume cache; '
                           'responded=1 iff npat object contains survey content (sections/instances), '
                           '0 on explicit api error / empty npat, None on fetch failure (never guessed). '
                           'Mapping pinned at collection per big5_es_votesmart_prereg.md; base rate and '
                           'artifact SHA recorded in the RESULT before any look.',
                 'counts': {'responded': n1, 'not_responded': n0, 'failed': nf}}, OUTF)
    print('DONE: responded %d | not %d | failed %d -> data/members/votesmart_npat.json' % (n1, n0, nf))
    print('Next: record the artifact SHA + join N in the prereg, then the ONE look (b5_es_votesmart_look.py).')

if __name__ == '__main__':
    main()
