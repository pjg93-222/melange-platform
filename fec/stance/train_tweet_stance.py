# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""TWEET STANCE CLASSIFIER trained on the user's 400 adjudicated tweet golds (user-directed
2026-07-04: "Do not disappoint me again. Especially for Tweet Stance").
Recipe = the measured press winner (word+char TF-IDF union + linear model, CV-tuned) applied to the
tweet domain. 400 rows is small, so the HONEST headline metric is 5-fold CV (never a single split).
Also measures the ENSEMBLE ladder against the Fable/Opus API labels on the same folds:
  production transfer model:      0.481 acc / 0.357 F1   (the thing being replaced)
  Fable API (full corpus):        0.616 / 0.548
  Opus API (400 sample):          0.677 / 0.662
--save writes stance_clf_tweets.pkl for scoring the full 9,559-tweet corpus (free)."""
import csv, io, json, os, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score, accuracy_score

B = MB_ROOT
CL = ["support", "oppose", "neutral", "unrelated"]
rows = [r for r in csv.DictReader(open(B + r'\data\stance\tweet_gold_queue.csv', encoding='utf-8-sig'))
        if (r.get('gold_cu_alignment') or '').strip().lower() in CL and (r.get('text') or '').strip()]
X = [r['text'] for r in rows]
y = [r['gold_cu_alignment'].strip().lower() for r in rows]
api = [(r.get('api_cu_alignment') or '').strip().lower() for r in rows]   # Opus prelabel on these rows
print('gold tweets: %d | classes %s' % (len(rows), dict(collections.Counter(y))))

def make(model):
    feats = FeatureUnion([
        ('w', TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        ('c', TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), min_df=2, sublinear_tf=True))])
    return Pipeline([('f', feats), ('m', model)])

GRID = [('SVM w+c C=0.5', make(LinearSVC(C=0.5, class_weight='balanced'))),
        ('SVM w+c C=1.0', make(LinearSVC(C=1.0, class_weight='balanced'))),
        ('SVM w+c C=2.0', make(LinearSVC(C=2.0, class_weight='balanced'))),
        ('LogReg w+c C=4', make(LogisticRegression(C=4.0, max_iter=3000, class_weight='balanced')))]

skf = StratifiedKFold(5, shuffle=True, random_state=7)
best = (None, -1, None)
for name, pipe in GRID:
    preds = np.empty(len(y), dtype=object)
    for tr, te in skf.split(X, y):
        pipe.fit([X[i] for i in tr], [y[i] for i in tr])
        p = pipe.predict([X[i] for i in te])
        for j, i in enumerate(te): preds[i] = p[j]
    acc = accuracy_score(y, preds); f1 = f1_score(y, preds, average='macro')
    print('  CV5 %-16s acc=%.3f macroF1=%.3f' % (name, acc, f1))
    if f1 > best[1]: best = (name, f1, preds)
name, f1b, cvp = best
print('CV winner: %s' % name)

def report(pred, tag):
    ok = [(g, p) for g, p in zip(y, pred) if p]
    acc = sum(1 for g, p in ok if g == p) / len(ok)
    f1 = f1_score([g for g, _ in ok], [p for _, p in ok], average='macro', labels=CL)
    print('  %-44s n=%d acc=%.3f macroF1=%.3f' % (tag, len(ok), acc, f1))

print('\nladder on the 400 golds (classical = out-of-fold CV predictions, no leakage):')
report(cvp, 'TRAINED classical (CV out-of-fold)')
report(api, 'Opus API prelabel')
ens = [c if (a and c == a) else (a or c) for c, a in zip(cvp, api)]
report(ens, 'ENSEMBLE: agree->label, disagree->API')
ens2 = [c if (a and c == a) else c for c, a in zip(cvp, api)]
report(ens2, 'ENSEMBLE: agree->label, disagree->classical')
agree = [(g, c) for g, c, a in zip(y, cvp, api) if a and c == a]
if agree:
    print('  consensus tier: %d/%d rows (%.0f%%) at acc=%.3f' % (len(agree), len(rows),
          100 * len(agree) / len(rows), sum(1 for g, c in agree if g == c) / len(agree)))

if '--save' in sys.argv:
    _, pipe = next(g for g in GRID if g[0] == name)
    pipe.fit(X, y)
    import pickle
    pickle.dump(pipe, open(B + r'\data\stance\stance_clf_tweets.pkl', 'wb'))
    print('saved stance_clf_tweets.pkl (%s, fit on all %d golds)' % (name, len(rows)))
