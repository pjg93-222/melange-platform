# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""S1 — stance evaluation harness. Scores ANY prediction column against the adjudicated gold labels in
gold_label_queue.csv (rows where gold_cu_alignment is filled). Reports per-class precision/recall/F1,
macro-F1 (the headline number -- classes are imbalanced, per ICCK/TACS.2025.928069 practice), and the
same breakdown per slice (source channel, hard-category tags), so per-slice champions can be picked as
new model layers come online (nli / stanceformer / llm columns can be appended to the queue csv later).
Usage: python stance_eval.py [pred_column]        (default: model_stance)"""
import csv, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
PRED = sys.argv[1] if len(sys.argv) > 1 else 'model_stance'
rows = [r for r in csv.DictReader(open(B + r'\data\stance\gold_label_queue.csv', encoding='utf-8-sig'))
        if (r.get('gold_cu_alignment') or '').strip()]
if not rows:
    print('No adjudicated rows yet (fill gold_cu_alignment in gold_label_queue.csv).'); sys.exit(0)
CLASSES = ['support', 'oppose', 'neutral', 'unrelated']
def norm(x): return (x or '').strip().lower()
def score(sub, label):
    tp = collections.Counter(); fp = collections.Counter(); fn = collections.Counter(); n = 0; ok = 0
    for r in sub:
        g, p = norm(r['gold_cu_alignment']), norm(r.get(PRED))
        if g not in CLASSES: continue
        n += 1; ok += (g == p)
        if g == p: tp[g] += 1
        else: fp[p] += 1; fn[g] += 1
    f1s = {}
    for c in CLASSES:
        pr = tp[c] / max(1, tp[c] + fp[c]); rc = tp[c] / max(1, tp[c] + fn[c])
        f1s[c] = 2 * pr * rc / max(1e-9, pr + rc)
    present = [c for c in CLASSES if tp[c] + fn[c] > 0]
    macro = sum(f1s[c] for c in present) / max(1, len(present))
    print('%-24s n=%-4d acc=%.3f macroF1=%.3f  ' % (label, n, ok / max(1, n), macro)
          + ' '.join('%s=%.2f' % (c[:3], f1s[c]) for c in present))
print('=== stance_eval: predictions from column %r vs gold_cu_alignment ===' % PRED)
score(rows, 'ALL adjudicated')
for chan in ('own-domain', 'acu', 'third-party'):
    sub = [r for r in rows if r.get('source_channel') == chan]
    if sub: score(sub, 'channel:' + chan)
for tag in ('cfpb', 'interchange', 'creditcard', 'bigbank', 'regression', 'lowconf'):
    sub = [r for r in rows if tag in (r.get('slices') or '')]
    if sub: score(sub, 'slice:' + tag)
# confusion matrix
cm = collections.Counter((norm(r['gold_cu_alignment']), norm(r.get(PRED))) for r in rows)
print('\nconfusion (gold -> pred):')
for g in CLASSES:
    line = '  %-10s' % g + ' '.join('%s:%-4d' % (p[:3], cm[(g, p)]) for p in CLASSES if cm[(g, p)])
    if line.strip() != g: print(line)
