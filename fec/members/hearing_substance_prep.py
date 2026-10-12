# -*- coding: utf-8 -*-
"""Extract candidate hostile-exchange snippets from the cached hearing sequences for the Haiku substance
classifier (2026-07-11, user). The keyword lexicon over-flags procedural questioning (88%) and mis-frames
genuine aggression (Ted Cruz 'are you going to answer the question' -> 'procedural'). Haiku reads the
member's actual words and labels frame + hostile + partisan valence. This just pulls the candidates;
classification is the workflow. Incremental: stable per-exchange id (package+member+text-hash) so re-runs
as the cache grows only add new candidates. Output: scratchpad/substance_candidates.json."""
import sys, os, sqlite3, json, re, hashlib, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from members.hearing_antagonism_build import is_hostile   # same candidate net as the builder

from paths import ROOT
B = ROOT
SCRATCH = os.environ.get('HEARING_SUBSTANCE_DIR',os.path.join(B,'outputs','research','hearing_substance'))
WT = {'G': 'government official', 'S': 'state/local official', 'M': 'military officer',
      'E': 'expert/academic', 'J': 'judge', 'B': 'business executive', 'A': 'advocate/association', '?': 'private witness'}

def run():
    sc = sqlite3.connect(B + r'\hearings_seq.db', timeout=60); sc.execute('PRAGMA query_only=1')
    mc = sqlite3.connect(B + r'\social_media.db', timeout=60); mc.execute('PRAGMA query_only=1')
    title = {p: (t or '')[:120] for p, t in mc.execute("select package, title from hearing_meta")}
    mc.close()
    cands = {}
    for pkg, date, seqj in sc.execute("select package, date, seq from hearing_seq"):
        seq = json.loads(seqj)
        for i, turn in enumerate(seq):
            if turn[0] in ('W', 'C', 'O'): continue
            m = turn[0]; text = turn[3] if len(turn) > 3 else ''
            if not text: continue
            prev = seq[i - 1] if i > 0 else None
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            wtype = prev[1] if (prev and prev[0] == 'W') else nxt[1] if (nxt and nxt[0] == 'W') else None
            if wtype is None or not is_hostile(text): continue
            hid = hashlib.md5(('%s|%s|%s' % (pkg, m, text[:80])).encode('utf-8', 'replace')).hexdigest()[:16]
            cands[hid] = {'id': hid, 'member': m, 'witness': WT.get(wtype, 'witness'),
                          'hearing': title.get(pkg, ''), 'text': text[:600]}
    sc.close()
    os.makedirs(SCRATCH, exist_ok=True)
    items = list(cands.values())
    outp = os.path.join(SCRATCH, 'substance_candidates.json')
    json.dump(items, open(outp, 'w', encoding='utf-8'))
    # chunk into small per-agent files (skip already-labeled ids so re-runs only chunk NEW candidates)
    labp = os.path.join(SCRATCH, 'substance_labels.json')
    labeled = set(json.load(open(labp, encoding='utf-8')).keys()) if os.path.exists(labp) else set()
    todo = [c for c in items if c['id'] not in labeled]
    cdir = os.path.join(SCRATCH, 'substance_chunks')
    os.makedirs(cdir, exist_ok=True)
    for f in os.listdir(cdir):
        os.remove(os.path.join(cdir, f))
    CH = 40; nch = 0
    for i in range(0, len(todo), CH):
        json.dump(todo[i:i + CH], open(os.path.join(cdir, 'chunk_%03d.json' % nch), 'w', encoding='utf-8'))
        nch += 1
    byw = collections.Counter(c['witness'] for c in cands.values())
    print('candidates: %d total | %d already labeled | %d to classify -> %d chunks in %s'
          % (len(items), len(labeled), len(todo), nch, cdir))
    print('by witness type:', dict(byw))

if __name__ == '__main__':
    run()
