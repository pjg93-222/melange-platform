# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""ATTRIBUTION GOLD VALIDATOR (2026-07-04 — gives the attribution field its first non-circular number).
Scores the production press reader's ATTRIBUTION labels (api_press_cache*.jsonl, newest-model cache
that covers each row's url) against attribution_gold_sheet.csv, whose truth is now the user's
domain rule (fec/stance/resolve_attribution_domain.py: own .house.gov/.senate.gov -> 'own', anything else
-> 'thirdparty' — a small number of rows may instead carry the user's own hand-typed override,
which stands as-is). Gold is 2-CLASS (own / not-own); the reader is 3-class (member-own /
reported-action / third-party-voice). Domain truth cannot distinguish "reported an action" from
"voiced an opinion", so for a fair grade the reader's own two not-own classes are COLLAPSED to
'thirdparty' before comparing — a 'reported-action' call on a non-own URL is a correct not-own
read, not a miss.
Prints accuracy + confusion; 'unfilled' until the sheet has resolved/labeled rows.
Why it matters: the press stack's channel weighting (own .gov @1.0 vs third-party @PRESS_THIRD_W=0.5)
already keys off URL domain in production; this checks whether the READER's independent attribution
guess agrees with that same domain signal.
Usage: python validate_attribution_gold.py"""
import csv, glob, hashlib, io, json, os, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
READER2GOLD = {'member-own': 'own', 'reported-action': 'thirdparty', 'third-party-voice': 'thirdparty'}

rows = [r for r in csv.DictReader(io.open(B + r'\data\stance\attribution_gold_sheet.csv', encoding='utf-8-sig'))]
col = 'your_attribution_own_action_thirdparty'
filled = [r for r in rows if (r.get(col) or '').strip().lower() in ('own', 'thirdparty')]
print('attribution gold sheet: %d rows, %d resolved/labeled (own or thirdparty)' % (len(rows), len(filled)))
if len(filled) < 15:
    print('UNFILLED (need >=15 for stable numbers) — run `python fec/stance/resolve_attribution_domain.py '
          '--write` to apply the domain rule, or hand-label %s, then rerun.' % col)
    sys.exit(0)

# url -> reader attribution, preferring the opus cache (production replica), then fable, then base
cache = {}
for pat in ('data/stance/api_press_cache.jsonl', 'data/stance/api_press_cache_fable.jsonl', 'api_press_cache_opus.jsonl'):
    p = os.path.join(B, pat)
    if not os.path.exists(p): continue
    for ln in io.open(p, encoding='utf-8', errors='replace'):
        try: d = json.loads(ln)
        except ValueError: continue
        k = d.get('k') or d.get('key'); out = d.get('out') or d.get('res') or {}
        att = out.get('attribution')
        if k and att: cache[k] = att   # later files (opus last) win
# gold sheet rows carry member+title; the reader cache is keyed md5(url) — the sheet builder stored row_id
# url -> cache key: prefer the sheet's own resolved_url (written by resolve_attribution_domain.py);
# fall back to a fresh title match only for hand-labeled rows that predate that column.
tone = os.path.join(os.path.dirname(B), 'Draft Systems', 'cu_press_stance_tone.csv')
t2u = {}
if os.path.exists(tone):
    for r in csv.DictReader(io.open(tone, encoding='utf-8-sig', newline='')):
        t = (r.get('title') or '').strip().lower(); u = (r.get('url') or '').strip()
        if t and u: t2u.setdefault(t, u)
row2key = {}
for r in rows:
    u = (r.get('resolved_url') or '').strip() or t2u.get((r.get('title') or '').strip().lower())
    if u: row2key[r['row_id']] = hashlib.md5(u.encode('utf-8')).hexdigest()
n_from_sheet = sum(1 for r in rows if (r.get('resolved_url') or '').strip())
print('  urls: %d from the sheet\'s own resolved_url, %d/%d total resolvable' % (n_from_sheet, len(row2key), len(rows)))

conf = collections.Counter(); n = hit = 0; unmatched = 0
for r in filled:
    g = (r.get(col) or '').strip().lower()   # already 2-class: own / thirdparty
    k = row2key.get(r.get('row_id'))
    p_raw = cache.get(k)
    if not p_raw: unmatched += 1; continue
    p = READER2GOLD.get(p_raw)
    if p is None: unmatched += 1; continue   # reader returned a value outside its own schema
    n += 1; hit += (g == p); conf[(g, p)] += 1
if unmatched: print('  (%d labeled rows had no reader cache entry)' % unmatched)
if not n:
    print('no scorable overlap — check cache files exist'); sys.exit(0)
print('attribution accuracy vs domain truth: %.3f (n=%d)  [reader\'s reported-action + third-party-voice both count as "thirdparty" here]' % (hit / n, n))
for cls in ('own', 'thirdparty'):
    tp = conf[(cls, cls)]
    fp = sum(v for (g, p), v in conf.items() if p == cls and g != cls)
    fn = sum(v for (g, p), v in conf.items() if g == cls and p != cls)
    prec = tp / max(1, tp + fp); rec = tp / max(1, tp + fn)
    print('  %-18s precision %.2f  recall %.2f  (tp %d fp %d fn %d)' % (cls, prec, rec, tp, fp, fn))
print('confusion (domain-truth -> reader, collapsed):')
for (g, p), v in sorted(conf.items(), key=lambda kv: -kv[1]):
    if g != p: print('   %s -> %s : %d' % (g, p, v))
