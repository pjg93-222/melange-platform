# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""TWO-HOP STANCE via curated valence registry (user-directed 2026-07-04 "we can break 80%").
Decomposition proven on the 400 tweet golds: model STANCE-hop 0.915, model VALENCE-hop only 0.689
(esp. weak on pro-CU targets). compose(model_stance, REGISTRY_valence) ceiling = 0.872.
This tool: for each item, take the model's stance_toward_target (strong) and look up valence in
data/stance/cu_valence_registry.json (your curated CU knowledge), compose the alignment. Registry misses fall
back to the model's flat alignment.
  --eval           measure current registry on the 400 tweet golds (free)
  --uncovered      surface high-volume targets the registry MISSES, ranked (tells you what to curate)
No API calls; pure composition over already-collected model fields."""
import csv, json, re, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
REG = json.load(open(B + r'\data\stance\cu_valence_registry.json'))['entries']
REIN = re.compile(r"\b(reform|commission|bipartisan|rein|reining|oversight|appropriat|accountab|curb|constrain|repeal|block|stop|oppos|against|overreach|protect|defend|save|preserve)\b", re.I)

def lookup(target, text):
    blob = ((target or '') + ' ' + (text or '')).lower()
    for e in REG:
        if any(m in blob for m in e['match']):
            v = e['valence']
            if e.get('rein_flips') and REIN.search(blob): v = -v
            return v, e['key']
    return None, None

def compose(stance, v):
    s = (stance or '').lower()
    if s == 'unrelated': return 'unrelated'
    if s == 'neutral' or v == 0: return 'neutral'
    if s == 'support': return 'support' if v > 0 else 'oppose'
    if s == 'oppose': return 'oppose' if v > 0 else 'support'
    return 'neutral'

CL = ["support", "oppose", "neutral", "unrelated"]
rows = [r for r in csv.DictReader(open(B + r'\data\stance\tweet_gold_queue.csv', encoding='utf-8-sig'))
        if (r.get('gold_cu_alignment') or '').strip().lower() in CL]

if '--uncovered' in sys.argv:
    miss = collections.Counter()
    for r in rows:
        v, k = lookup(r.get('api_target'), r.get('text'))
        if v is None: miss[(r.get('api_target') or '(none)').strip().lower()[:40]] += 1
    print('HIGH-VOLUME TARGETS THE REGISTRY MISSES (add these to close the gap):')
    for t, n in miss.most_common(20): print('  %3d  %s' % (n, t))
    sys.exit(0)

# --eval
n = ok = 0; cov = 0; tp = collections.Counter(); fp = collections.Counter(); fn = collections.Counter()
for r in rows:
    v, k = lookup(r.get('api_target'), r.get('text'))
    if v is not None:
        cov += 1; p = compose(r.get('api_stance_toward_target'), v)
    else:
        p = (r.get('api_cu_alignment') or '').strip().lower()   # fallback to flat model on registry miss
    g = (r.get('gold_cu_alignment') or '').strip().lower()
    if g in CL and p:
        n += 1; ok += (g == p); (tp if g == p else fp)[p] += 1
        if g != p: fn[g] += 1
f1 = {c: 2*tp[c]/max(1, 2*tp[c]+fp[c]+fn[c]) for c in CL}
print('TWO-HOP (registry + flat fallback) on 400 tweet golds:')
print('  acc=%.3f macroF1=%.3f | registry covers %d/%d (%.0f%%)' % (
    ok/n, sum(f1[c] for c in CL if tp[c]+fn[c])/max(1, sum(1 for c in CL if tp[c]+fn[c])), cov, len(rows), 100*cov/len(rows)))
print('  baseline flat 0.677 | oracle ceiling 0.872 | run --uncovered to see what to curate next')
