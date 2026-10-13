# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT, DRAFT as MB_DRAFT
"""PRESS STANCE STACK — production scorer (user 2026-07-04: "wire in the most accurate stack",
zero new API spend). Mirrors tweet_stance_stack.py.
Instrument: meta-LogReg(C=1, untuned) over corpus-wide fields only —
  registry-compose (Fable target x cu_valence_registry) + Fable flat/hop/valence/conf + DEBATE +
  two TF-IDF readers (inp = target/speaker/headline, fulltext = title+body) trained on ALL 760
  user golds. Measured (pre-registered, GroupKFold dedup): 0.837 acc on the 760-gold frame /
  0.765 on the adversarial goldeval frame; DEBATE alone = 0.796/0.571 on the same frames.
Tiers (production trust levels, measured): unanimous(stack==fable==debate) 0.955 · 44%,
  majority(stack matches one) 0.776 · 44%, review 0.638 · 12%.
Output: press_stance_final.csv sidecar (url, member, stack_label, tier). build_media_blend.py
consumes it: sign from stack label (UNRELATED -> 0 — kills trade-digest mention noise), intensity
x tier weight, DEBATE fallback when a url is missing.
Usage: python press_stance_stack.py            (train on golds + score full corpus + write sidecar)"""
import csv, hashlib, html, io, json, os, re, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
import config_overlay as CO
from press_stream import canonical_url, load_fulltext, load_tone_rows, source_paths
B = MB_ROOT
CL = ["support", "oppose", "neutral", "unrelated"]

fable = {}
for ln in open(os.path.join(B, "data", "stance", "api_press_cache_fable.jsonl"), encoding="utf-8"):
    try:
        d = json.loads(ln); fable[d["k"]] = d["out"]
    except Exception: pass
REG = CO.merged_rows("data/stance/cu_valence_registry.json","entries",("key",),data_root=B)
FT = load_fulltext()

POLICY_KEYS = {e["key"] for e in REG if e.get("policy_force")}   # user-legislated axes bind the final label
def reg_lookup(target, text):
    blob = ((target or "") + " " + (text or "")).lower()
    for e in REG:
        if any(m in blob for m in e["match"]): return e["valence"]
    return None
def reg_key(target, text):
    blob = ((target or "") + " " + (text or "")).lower()
    for e in REG:
        if any(m in blob for m in e["match"]): return e["key"]
    return None
# Paul's hand press-stance overrides (ground truth; applied LAST). Same file the desk display uses.
_POV = CO.merged_rows("data/stance/press_stance_overrides.json","corrections",
                      ("url","title_contains"),data_root=B)
def _uurl(u): return canonical_url(u, keep_fragment=True)
_POV_URL = {_uurl(c.get("url")): (c.get("stance") or "").strip().lower() for c in _POV
            if c.get("url") and (c.get("stance") or "").strip().lower() in ("support", "oppose", "neutral")}
_POV_TITLE = [((c.get("title_contains") or "").lower(), (c.get("stance") or "").strip().lower()) for c in _POV
              if c.get("title_contains") and (c.get("stance") or "").strip().lower() in ("support", "oppose", "neutral")]
def press_override(url, title):
    if url and _uurl(url) in _POV_URL: return _POV_URL[_uurl(url)]
    if url and canonical_url(url, keep_fragment=False) in _POV_URL:
        return _POV_URL[canonical_url(url, keep_fragment=False)]
    t = (title or "").lower()
    for sub, st in _POV_TITLE:
        if sub and sub in t: return st
    return None
def compose(s, v):
    s = (s or "").lower()
    if s == "unrelated": return "unrelated"
    if s == "neutral" or v == 0: return "neutral"
    if s == "support": return "support" if v > 0 else "oppose"
    if s == "oppose": return "oppose" if v > 0 else "support"
    return "neutral"
def onehot(l, dom): return [1.0 if l == c else 0.0 for c in dom]
def vnum(v):
    try: return float(int(str(v).strip()))
    except (ValueError, TypeError): return 0.0
CONF = {"high": 2.0, "medium": 1.0, "low": 0.0}

def featurize(recs):
    """recs: [{url,title,body,debate}] -> (FBASE, inps, fts) with fields from the fable cache."""
    FB = []; inps = []; fts = []
    for r in recs:
        k = hashlib.md5((r["url_raw"]).strip().encode()).hexdigest()
        fb = fable.get(k, {})
        tgt = (fb.get("target") or "Credit Unions (direct)").strip()
        f_flat = (fb.get("cu_alignment") or "").strip().lower()
        f_hop = (fb.get("stance_toward_target") or "").strip().lower()
        v = reg_lookup(fb.get("target") or "", (r["title"] or "") + " " + (r["body"] or "")[:300])
        A = compose(f_hop, v) if (v is not None and f_hop) else f_flat
        hit = 1.0 if v is not None and f_hop else 0.0
        FB.append(onehot(A, CL) + onehot(f_flat, CL) + onehot(r["debate"], CL) + onehot(f_hop, CL) +
                  [vnum(fb.get("target_cu_valence", "")), CONF.get((fb.get("confidence") or "").strip().lower(), 1.0), hit])
        inps.append("target: %s. speaker: %s. %s" % (tgt, fb.get("attribution") or "member", (r["body"] or "")[:1500]))
        u = canonical_url(r["url_raw"], keep_fragment=True)
        fts.append(((r["title"] or "") + ". " + (FT.get(u) or r["body"] or ""))[:6000])
    return np.array(FB), inps, fts

# ---- train on ALL golds ----
gold = [r for r in csv.DictReader(open(os.path.join(B, "data", "stance", "cu_golden_labels.csv"), encoding="utf-8-sig"))
        if (r.get("label") or "").strip().lower() in CL]
tone_rows = load_tone_rows()
tone = {canonical_url(r.get("url"), keep_fragment=True): r for r in tone_rows}
grecs = [{"url_raw": (r.get("url") or ""), "title": r.get("title") or "",
          "body": (r.get("text") or "").strip() or (r.get("title") or ""),
          "debate": ((tone.get(canonical_url(r.get("url"), keep_fragment=True)) or {}).get("stance") or "").strip().lower()}
         for r in gold]
y = np.array([(r.get("label") or "").strip().lower() for r in gold])
FB, inps, fts = featurize(grecs)
rd1 = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
                    LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced")).fit(inps, y)
rd2 = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
                    LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced")).fit(fts, y)
def pb(mdl, X):
    P = np.zeros((len(X), 4)); pr = mdl.predict_proba(X)
    for j, c in enumerate(list(mdl.classes_)):
        if c in CL: P[:, CL.index(c)] = pr[:, j]
    return P
X = np.hstack([FB, pb(rd1, inps), pb(rd2, fts)])
meta = LogisticRegression(C=1.0, max_iter=3000, class_weight="balanced").fit(X, y)
print("trained on %d golds (meta + 2 in-set readers)" % len(y))

# ---- score the full corpus ----
crecs = [{"url_raw": (r.get("url") or ""), "title": r.get("title") or "",
          "body": r.get("title") or "",   # corpus rows carry no body col; fulltext cache supplies it in fts
          "debate": (r.get("stance") or "").strip().lower()} for r in tone_rows if (r.get("url") or "").strip()]
FBc, inpc, ftc = featurize(crecs)
Xc = np.hstack([FBc, pb(rd1, inpc), pb(rd2, ftc)])
pred = meta.predict(Xc)
out = []
tiern = collections.Counter()
for i, r in enumerate([t for t in tone_rows if (t.get("url") or "").strip()]):
    k = hashlib.md5((r.get("url") or "").strip().encode()).hexdigest()
    f_flat = (fable.get(k, {}).get("cu_alignment") or "").strip().lower()
    deb = (r.get("stance") or "").strip().lower()
    s = pred[i]
    # POLICY-FORCE, RELAXED 2026-07-15 (Paul's fresh CFPB gold re-adjudicated the agency-existence axis
    # as DIRECTIONAL: attacking/defunding CFPB = pro-CU/support, defending CFPB = anti-CU/oppose). Keep
    # neutral ONLY when the composed Fable read (f_flat) is itself non-directional; else trust the direction.
    if reg_key(fable.get(k, {}).get("target"), r.get("title")) in POLICY_KEYS and s in ("support", "oppose"):
        s = f_flat if f_flat in ("support", "oppose") else "neutral"
    # tier reflects MODEL reader agreement (pre-override) so a hand label changes the label but NOT the
    # blend weight (BUG-C fix: mirror the tweet stack, which sets tier from readers not the override).
    tier = "unanimous" if (s == f_flat == deb) else ("majority" if (s == f_flat or s == deb) else "review")
    # Paul's hand press labels are ground truth -- applied LAST, beat stack + registry + pin.
    _pov = press_override(r.get("url"), r.get("title"))
    if _pov is not None: s = _pov
    tiern[tier] += 1
    out.append({"url": r.get("url"), "member": r.get("member"), "date": r.get("date"),
                "bioguide":r.get("bioguide"), "title": html.unescape(r.get("title") or ""),
                "stack_label": s, "tier": tier, "debate_stance": deb,
                "source":r.get("source") or "HISTORICAL_PRESS"})
if not out:
    raise RuntimeError("press tone corpus is empty")
tmp = os.path.join(B, "press_stance_final.csv.tmp")
with io.open(tmp, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
    w.writeheader(); w.writerows(out)
os.replace(tmp, os.path.join(B, "press_stance_final.csv"))
lab = collections.Counter(o["stack_label"] for o in out)
chg = sum(1 for o in out if o["stack_label"] != o["debate_stance"])
print("scored %d articles -> press_stance_final.csv" % len(out))
print("tone sources: %s" % ", ".join(source_paths()))
print("labels: %s | tiers: %s | changed vs DEBATE: %d (%.0f%%)" % (dict(lab), dict(tiern), chg, 100*chg/len(out)))
