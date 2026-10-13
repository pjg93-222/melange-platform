# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""TWEET STANCE STACK — the production tweet labeler (user 2026-07-04 "I need to break 80" -> BROKEN:
0.855 acc / 0.819 macro-F1, honest 5-fold out-of-fold on the 400 user golds; no meta tuning, C=1).
Architecture (the decomposition finding made concrete):
  base fields per tweet: registry two-hop compose (user-derived data/stance/cu_valence_registry.json) |
  classical word+char TF-IDF probas (trained on user golds) | API flat label + stance-hop +
  valence + confidence (Fable corpus pass) | registry-hit + is_rt flags
  meta: LogReg(C=1, balanced) trained on the 400 golds' out-of-fold base features.
Ladder for the record: production transfer 0.481 -> Fable flat 0.616 -> classical 0.737 ->
registry two-hop 0.782 -> stack 0.855 (oracle ceiling 0.872).
DISCIPLINE: number is OOF on the same 400 golds the design iterated against; PRE-REGISTERED for
confirmation on the next fresh adjudication batch before being called final.
Usage:
  python tweet_stance_stack.py --eval     # reprint the honest OOF measurement
  python tweet_stance_stack.py --score    # label all 9,559 -> tweet_stance_final.csv (stack labels+tiers)
"""
import csv, io, json, re, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold
import stance.tweet_image_evidence as TIE
import config_overlay as CO
import stance.stance_rules as SR
B = MB_ROOT
CL = ["support", "oppose", "neutral", "unrelated"]
REG = CO.merged_rows('data/stance/cu_valence_registry.json','entries',('key',),data_root=B)
POLICY_KEYS = {e['key'] for e in REG if e.get('policy_force')}   # user-legislated axes bind the final label

def fam_lookup(target, text):
    blob = ((target or '') + ' ' + (text or '')).lower()
    for e in REG:
        if any(m in blob for m in e['match']): return e['valence'], e['key']
    return None, None

def compose(s, v):
    s = (s or '').lower()
    if s == 'unrelated': return 'unrelated'
    if s == 'neutral' or v == 0: return 'neutral'
    if s == 'support': return 'support' if v > 0 else 'oppose'
    if s == 'oppose': return 'oppose' if v > 0 else 'support'
    return 'neutral'

def _norm_ov(s):
    s = (s or '').lower(); s = re.sub(r'https?://\S+', '', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s); return re.sub(r'\s+', ' ', s).strip()
# Paul's hand-labeled tweet stance overrides (ground truth; applied LAST, beats stack + policy-force). 2026-07-15.
_TOV = CO.merged_rows('data/stance/tweet_stance_overrides.json','corrections',
                      ('quorum_id','text_contains'),data_root=B)
_IMG = TIE.index_annotations(CO.merged_rows('data/stance/tweet_image_annotations.json','annotations',
                                            ('quorum_id',),data_root=B))
_TOV_TXT = [(_norm_ov(c.get('text_contains', '')), (c.get('stance') or '').strip().lower()) for c in _TOV
            if c.get('text_contains') and (c.get('stance') or '').strip().lower() in ('support', 'oppose', 'neutral')]
_TOV_QID = {str(c['quorum_id']): (c.get('stance') or '').strip().lower() for c in _TOV
            if c.get('quorum_id') and (c.get('stance') or '').strip().lower() in ('support', 'oppose', 'neutral')}
def tweet_override(qid, text):
    if qid and str(qid) in _TOV_QID: return _TOV_QID[str(qid)]
    tn = _norm_ov(text)
    for sub, st in _TOV_TXT:
        if sub and sub in tn: return st
    return None

def clf():
    return Pipeline([("f", FeatureUnion([("w", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
                                         ("c", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True))])),
                     ("m", LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced"))])

def onehot(lbl): return [1.0 if lbl == c else 0.0 for c in CL]
def conf_num(c): return {'high': 2.0, 'medium': 1.0, 'low': 0.0}.get((c or '').strip().lower(), 1.0)
def vnum(v):
    try: return float(int(str(v).strip()))
    except (ValueError, TypeError): return 0.0

def base_features(api_target, text, api_stance, api_val, api_align, api_conf, is_rt, Bproba):
    v, key = fam_lookup(api_target, text)
    if v is not None: A = compose(api_stance, v); hit = 1.0
    else: A = (api_align or '').strip().lower(); hit = 0.0
    return (onehot(A) + list(Bproba) + onehot((api_align or '').strip().lower()) +
            onehot((api_stance or '').strip().lower()) + [vnum(api_val), conf_num(api_conf), hit, 1.0 if is_rt else 0.0]), A

gold = [r for r in csv.DictReader(open(_o.path.join(B, 'data', 'stance', 'tweet_gold_queue.csv'), encoding='utf-8-sig'))
        if (r.get('gold_cu_alignment') or '').strip().lower() in CL and (r.get('text') or '').strip()]
yg = np.array([(r.get('gold_cu_alignment') or '').strip().lower() for r in gold])
Xg = [r['text'] for r in gold]
N = len(gold); folds = list(KFold(5, shuffle=True, random_state=7).split(np.arange(N)))

# out-of-fold base probas for gold (needed both for --eval and to train the production meta cleanly)
Bp = np.zeros((N, 4))
for tr, te in folds:
    p = clf(); p.fit([Xg[i] for i in tr], yg[tr])
    pr = p.predict_proba([Xg[i] for i in te])
    for j, i in enumerate(te): Bp[i] = pr[j]
Fg = []; Ag = []
for i, r in enumerate(gold):
    f, A = base_features(r.get('api_target'), r.get('text'), r.get('api_stance_toward_target'),
                         r.get('api_target_cu_valence'), r.get('api_cu_alignment'), r.get('api_confidence'),
                         (r.get('is_rt') or '').strip().lower() in ('1', 'true'), Bp[i])
    Fg.append(f); Ag.append(A)
Fg = np.array(Fg)

if '--eval' in sys.argv or '--score' not in sys.argv:
    meta_oof = np.empty(N, dtype=object)
    for tr, te in folds:
        m = LogisticRegression(C=1.0, max_iter=3000, class_weight='balanced')
        m.fit(Fg[tr], yg[tr]); meta_oof[te] = m.predict(Fg[te])
    ok = sum(1 for i in range(N) if meta_oof[i] == yg[i])
    tp = collections.Counter(); fp = collections.Counter(); fn = collections.Counter()
    for i in range(N):
        (tp if meta_oof[i] == yg[i] else fp)[meta_oof[i]] += 1
        if meta_oof[i] != yg[i]: fn[yg[i]] += 1
    f1 = {c: 2*tp[c]/max(1, 2*tp[c]+fp[c]+fn[c]) for c in CL}
    mf = sum(f1[c] for c in CL if tp[c]+fn[c]) / max(1, sum(1 for c in CL if tp[c]+fn[c]))
    print('STACK honest OOF on %d golds: acc=%.3f macroF1=%.3f' % (N, ok/N, mf))
    print('  per-class F1:', ' '.join('%s=%.2f' % (c, f1[c]) for c in CL))
    if '--score' not in sys.argv: sys.exit(0)

if '--score' in sys.argv:
    # production: classical trained on ALL golds; meta trained on the OOF gold features (no leakage into meta)
    # FULL API fields (stance-hop, valence, confidence) come from the Fable CACHE — the trimmed csv
    # dropped them, which mangled the meta features on the first scoring run (distribution flip bug).
    import hashlib
    pc = clf(); pc.fit(Xg, yg)
    meta = LogisticRegression(C=1.0, max_iter=3000, class_weight='balanced'); meta.fit(Fg, yg)
    cache = {}
    for ln in open(_o.path.join(B, 'data', 'stance', 'api_tweet_cache_fable.jsonl'), encoding='utf-8'):
        try:
            d = json.loads(ln); cache[d['k']] = d['out']
        except Exception: pass
    tw = list(csv.DictReader(io.open(_o.path.join(B, 'data', 'stance', 'quorum_member_tweets.csv'), encoding='utf-8-sig', newline='')))
    # BUG-A GUARD (2026-07-15): a text_contains override must uniquely identify ONE tweet. The first
    # cut keyed on norm(text)[:45], which for one tweet was the CFPB's spelled-out name -> matched 149
    # tweets and mass-forced 'support'. Overrides now key by quorum_id; any residual non-unique
    # text_contains rule is DROPPED here (not silently applied) so a generic phrase can never mass-relabel.
    if _TOV_TXT:
        _cn = [_norm_ov(r.get('Content')) for r in tw]
        _keep = []
        for _sub, _st in _TOV_TXT:
            _c = sum(1 for x in _cn if _sub and _sub in x)
            if _c == 1: _keep.append((_sub, _st))
            else: print('  !! override text_contains DROPPED (matches %d tweets, not unique): %r' % (_c, _sub[:50]))
        _TOV_TXT[:] = _keep
    texts = [(r.get('Content') or '').strip() or '.' for r in tw]
    probs = pc.predict_proba(texts)
    out = []
    for r, pr in zip(tw, probs):
        t = (r.get('Content') or '').strip()
        k = hashlib.md5((r.get('Quorum ID') or t[:80]).encode('utf-8', 'replace')).hexdigest()
        o = cache.get(k, {})
        f, A = base_features(o.get('target'), t, o.get('stance_toward_target'),
                             o.get('target_cu_valence'), o.get('cu_alignment'), o.get('confidence'),
                             t.startswith('RT @'), pr)
        lbl = meta.predict([f])[0]
        clab = pc.classes_[int(np.argmax(pr))]   # 3rd-pass S2 fix: index the classifier's OWN class order (sklearn is alphabetical), not CL — was swapping neutral<->support in the tier weight
        alab = (o.get('cu_alignment') or '').strip().lower()
        lbl = SR.enforce_reader_unanimity(lbl, (A, clab, alab))
        # POLICY-FORCE, RELAXED 2026-07-15 (Paul's fresh CFPB gold re-adjudicated the agency-existence
        # axis as DIRECTIONAL: attacking/defunding CFPB = pro-CU/support, defending CFPB = anti-CU/oppose).
        # The 2026-07-07 blanket neutral-pin over-neutralized ~1,582 tweets (tweet-gold sign acc 0.54).
        # Relaxation: keep neutral ONLY when the composed API read is itself non-directional; otherwise
        # trust the composed direction. Validated vs 400 tweet gold: 0.54 -> ~0.75, ~0 overcorrection.
        _, _fkey = fam_lookup(o.get('target'), t)
        if _fkey in POLICY_KEYS and lbl in ('support', 'oppose'):
            lbl = alab if alab in ('support', 'oppose') else 'neutral'
        tier = 'unanimous' if (alab and A == clab == alab) else ('review' if (alab and A != clab and A != alab and clab != alab) else 'majority')
        # Reviewed image evidence is a separate reader. It can resolve weak text, while a conflict
        # with clear directional text remains a review flag instead of silently flipping the label.
        _im = _IMG.get(str(r.get('Quorum ID') or ''), {})
        _fused = TIE.fuse_stance(lbl, tier, _im.get('cu_alignment'), _im.get('confidence'))
        lbl = _fused['stance']
        # Paul's hand labels are ground truth -- applied LAST, beat stack + registry + pin alike.
        _ov = tweet_override(r.get('Quorum ID'), t)
        if _ov is not None:
            lbl = _ov
            if _fused['image_read']:
                _fused['image_disposition'] = 'manual-override-after-image'
        out.append({'quorum_id': r.get('Quorum ID'), 'member': r.get('member'), 'date': r.get('dt'),
                    'stack_stance': lbl, 'registry_read': A, 'classical_read': clab, 'api_read': alab,
                    'image_read': _fused['image_read'], 'image_confidence': _fused['image_confidence'],
                    'image_disposition': _fused['image_disposition'], 'tier': tier, 'text': t[:200]})
    tmp = _o.path.join(B, 'data', 'stance', 'tweet_stance_final.csv')
    with io.open(tmp + '.tmp', 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    import os as _os; _os.replace(tmp + '.tmp', tmp)
    print('scored %d tweets -> tweet_stance_final.csv (stack labels + reader tiers)' % len(out))
    print('  distribution:', dict(collections.Counter(r['stack_stance'] for r in out)))
    print('  tiers:', dict(collections.Counter(r['tier'] for r in out)))
