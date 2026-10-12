# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""Build the fresh interview-antagonism criterion for the A successor experiment (2026-07-12).

The default mode is read-only and reports the deterministically split eligible roster. ``--go`` is
the explicit cost gate: Haiku 4.5 scores two blinded, package-disjoint interview halves per member
under one frozen rubric. The raw candidate is resumable and atomically written after each completed
member. It is consumed by no production script; scoring does not authorize a trait look.
"""
import argparse, collections, hashlib, json, re, sqlite3, time
from concurrent.futures import ThreadPoolExecutor, as_completed
_s.stdout.reconfigure(encoding='utf-8', errors='replace')
from moneyball_core import dump_atomic

B = MB_ROOT
OUT = B + r'\data\stance\interview_antagonism_halves.json.candidate'
MODEL = 'claude-haiku-4-5'
MIN_PACKAGES = 3
MIN_WORDS = 1000
CAP_CHARS = 14000

RUBRIC = r"""You are rating BEHAVIORAL DISPOSITION from interview responses by one public official.
The official's identity is intentionally withheld. Score each package-disjoint half separately.

For each half, return integer 0-100 scores for:
- reporter_hostility: gratuitous hostility, intimidation, contempt, or personal attack toward the interviewer;
- personal_contempt: demeaning, mocking, cruel, or contemptuous treatment of people as people;
- gratuitous_cruelty: punitive or callous personal affect beyond substantive policy disagreement;
- principled_dissent: good-faith independence, moral disagreement, or willingness to oppose one's own side;
- policy_conflict: forceful partisan or policy disagreement without personal contempt.

Do NOT count toughness, ideology, ordinary criticism, policy threat language, procedural impatience,
or principled dissent as antagonism. Scores must reflect observed behavior in the supplied text only.
Use the full 0-100 range when warranted; absence of evidence is low, not average. Output JSON only:
{"A":{"reporter_hostility":0,"personal_contempt":0,"gratuitous_cruelty":0,"principled_dissent":0,"policy_conflict":0,"confidence":"low|medium|high"},
 "B":{"reporter_hostility":0,"personal_contempt":0,"gratuitous_cruelty":0,"principled_dissent":0,"policy_conflict":0,"confidence":"low|medium|high"}}
"""

def load_halves():
    con = sqlite3.connect(B + r'\social_media.db', timeout=60)
    con.execute('PRAGMA query_only=1')
    packs = collections.defaultdict(lambda: collections.defaultdict(list))
    for member, package, text in con.execute(
            "SELECT member,package,text FROM speeches WHERE source='INTERVIEW' ORDER BY rowid"):
        if member and package and text and len(text.split()) >= 8:
            packs[member][package].append(text.strip())
    con.close()
    out = {}
    for member, pp in packs.items():
        items = sorted(pp.items(), key=lambda kv: hashlib.sha256(kv[0].encode('utf-8', 'replace')).hexdigest())
        halves = [items[0::2], items[1::2]]
        rec = []
        for half in halves:
            text = '\n--- APPEARANCE ---\n'.join(' '.join(turns) for _pkg, turns in half)
            rec.append({'packages': len(half), 'words': len(text.split()), 'text': text[:CAP_CHARS]})
        if all(x['packages'] >= MIN_PACKAGES and x['words'] >= MIN_WORDS for x in rec):
            out[member] = rec
    return out

def key():
    k = _o.environ.get('ANTHROPIC_API_KEY', '').strip()
    p = B + r'\fec\anthropic_key.txt'
    if not k and _o.path.exists(p):
        k = open(p, encoding='utf-8').read().strip()
    return k

def parse_json(text):
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I | re.S)
    # Haiku sometimes appends a second fenced rendering or a one-line note after a valid object.
    # Accept the first complete JSON value; the locked rubric and numeric fields remain unchanged.
    obj, _end = json.JSONDecoder().raw_decode(text.lstrip())
    for half in ('A', 'B'):
        d = obj[half]
        for field in ('reporter_hostility', 'personal_contempt', 'gratuitous_cruelty',
                      'principled_dissent', 'policy_conflict'):
            d[field] = int(max(0, min(100, round(float(d[field])))))
        d['antagonism'] = round((d['reporter_hostility'] + d['personal_contempt'] +
                                d['gratuitous_cruelty']) / 3.0, 3)
        d['confidence'] = str(d.get('confidence', 'low')).lower()
    return obj

def score_one(client, member, halves):
    prompt = RUBRIC + '\n\nHALF A:\n' + halves[0]['text'] + '\n\nHALF B:\n' + halves[1]['text']
    err = None
    for attempt in range(4):
        try:
            msg = client.messages.create(model=MODEL, max_tokens=450,
                                         messages=[{'role': 'user', 'content': prompt}])
            obj = parse_json(msg.content[0].text)
            return member, {'halves': obj,
                            'packages': [halves[0]['packages'], halves[1]['packages']],
                            'words': [halves[0]['words'], halves[1]['words']]}, None
        except Exception as exc:
            err = '%s: %s' % (type(exc).__name__, exc)
            time.sleep(2 ** attempt)
    return member, None, err

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--go', action='store_true', help='cost gate: call Haiku 4.5')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--limit', type=int, default=0, help='operational cap; partial output has no research status')
    args = ap.parse_args()
    halves = load_halves()
    total_chars = sum(len(h[0]['text']) + len(h[1]['text']) for h in halves.values())
    print('interview-antagonism eligible: %d members | %d package-disjoint halves | %.1fM chars' %
          (len(halves), 2 * len(halves), total_chars / 1e6))
    if not args.go:
        print('read-only roster check complete; pass --go to score with %s' % MODEL)
        return
    api_key = key()
    if not api_key:
        raise SystemExit('ANTHROPIC_API_KEY / fec/anthropic_key.txt missing')
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    prior = json.load(open(OUT, encoding='utf-8')) if _o.path.exists(OUT) else {}
    members = prior.get('members', {})
    todo = [m for m in sorted(halves) if m not in members]
    if args.limit: todo = todo[:args.limit]
    errors = {}
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futs = [pool.submit(score_one, client, m, halves[m]) for m in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            member, rec, err = fut.result()
            if rec is not None:
                members[member] = rec
                errors.pop(member, None)
            else: errors[member] = err
            dump_atomic({'model': MODEL, 'rubric_version': 'locked-2026-07-12-v1',
                         'eligible_n': len(halves), 'members': members, 'errors': errors}, OUT, indent=1)
            if i % 25 == 0 or i == len(futs):
                print('  scored %d/%d this run | total complete %d | errors %d' %
                      (i, len(futs), len(members), len(errors)), flush=True)
    print('interview_antagonism_halves.json.candidate: %d/%d members scored, %d errors' %
          (len(members), len(halves), len(errors)))

if __name__ == '__main__':
    main()
