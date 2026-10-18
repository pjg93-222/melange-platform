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
Output: press_stance_final.csv sidecar (url, member, stack_label, tier, stack_path). build_media_blend.py
consumes it: sign from stack label (UNRELATED -> 0 — kills trade-digest mention noise), intensity
x tier weight, DEBATE fallback when a url is missing.
Refresh rows (source PRESS_REFRESH_HOST_ARTICLE) carry a Stage 5 label as their DEBATE slot but no Fable reading
(owner ruling D-219, 2026-10-15). A Haiku reading (press_haiku_rater.py) fills the missing reader slot ONLY while its
kappa gate passes (stack_path 'stack_haiku'); otherwise the row bypasses the meta-model and keeps its Stage 5 label
(stack_path 'bypass', tier BYPASS_TIER). Every other row is stack_path 'stack'.
Debate-slot leak fix (owner ruling D-221, 2026-10-15): the DEBATE slot of the meta-model, in training and in scoring, is the RAW
pre-overlay DEBATE reading (Draft Systems cu_press_stance_raw.csv for a row whose tone stance_src is 'human_label'), never Paul's
overlay label -- in training that label IS the target, so the meta-model learned to copy the slot and then copied it on the corpus.
The tier is computed from the raw slot. Paul's 760 gold labels (data/stance/cu_golden_labels.csv) are applied AFTER the stack and
the tier, then his hand press overrides last; the output column label_source says which one decided each row: 'stack', 'gold_760'
or 'press_override' (stack_path still says how a 'stack' label was made: stack / stack_haiku / bypass).
Usage: python press_stance_stack.py            (train on golds + score full corpus + write sidecar)"""
import csv, hashlib, html, io, json, os, re, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
import config_overlay as CO
from press_stream import canonical_url, load_fulltext, load_tone_rows, source_paths
import stance.press_haiku_rater as HR
B = MB_ROOT
CL = ["support", "oppose", "neutral", "unrelated"]

fable = {}
for ln in open(os.path.join(B, "data", "stance", "api_press_cache_fable.jsonl"), encoding="utf-8"):
    try:
        d = json.loads(ln); fable[d["k"]] = d["out"]
    except Exception: pass
REG = CO.merged_rows("data/stance/cu_valence_registry.json","entries",("key",),data_root=B)
FT = load_fulltext()
READER, READER_ADMITTED, READER_DETAIL = HR.admitted_slot()   # {} unless the Haiku kappa gate passed
BYPASS_TIER = "review"   # one reader (the Stage 5 label) cannot be unanimous or a majority: lowest trust weight (0.35)
def is_refresh(row): return (row.get("source") or "").strip().upper() == HR.REFRESH_SOURCE

# ---- the DEBATE slot: RAW pre-overlay DEBATE (D-221) ----
OVERLAY_SRC = "human_label"      # tone stance_src of a row whose DEBATE stance Paul's hand label overwrote (run_stage5.R:139)
RAW_DEBATE_PATH = os.path.join(MB_DRAFT, "cu_press_stance_raw.csv")   # id (= the tone url), stance: DEBATE before the overlay
DEBATE_COLS = slice(8, 12)       # the DEBATE one-hot inside featurize()'s FB row
_RAW_DEBATE = None
RAW_MISSING = set()              # overlay rows the raw file has no reading for (slot left blank, never the label)
def _raw_debate_table():
    global _RAW_DEBATE
    if _RAW_DEBATE is None:
        if not os.path.isfile(RAW_DEBATE_PATH):
            raise RuntimeError("raw pre-overlay DEBATE file missing: %s (an overlay row's DEBATE slot cannot be recovered; "
                               "using the overlay label would leak Paul's label into the meta-model)" % RAW_DEBATE_PATH)
        with io.open(RAW_DEBATE_PATH, encoding="utf-8-sig", newline="") as _h:
            _RAW_DEBATE = {canonical_url(_r.get("id"), keep_fragment=True): (_r.get("stance") or "").strip().lower()
                           for _r in csv.DictReader(_h) if (_r.get("id") or "").strip()}
    return _RAW_DEBATE
def raw_debate(row):
    """A tone row's DEBATE slot. Refresh rows: their Stage 5 label (D-219). A row Paul's overlay relabelled: the raw DEBATE
    reading from before the overlay. Every other row: its stance, which already is the raw DEBATE reading."""
    d = (row.get("stance") or "").strip().lower()
    if is_refresh(row) or (row.get("stance_src") or "").strip() != OVERLAY_SRC: return d
    raw = _raw_debate_table().get(canonical_url(row.get("url"), keep_fragment=True), "")
    if raw not in CL:
        RAW_MISSING.add(row.get("url")); return ""
    return raw

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
        fb = fable.get(k) or (READER.get(k) if r.get("refresh") else None) or {}
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
# Paul's golds as ground truth AFTER the stack: canonical url -> label (first row wins, like Stage 5's own overlay loader).
GOLD_LABEL = {}
for r in gold:
    if (r.get("url") or "").strip(): GOLD_LABEL.setdefault(canonical_url(r.get("url"), keep_fragment=True), (r.get("label") or "").strip().lower())
def _gold_slot(r):
    t = tone.get(canonical_url(r.get("url"), keep_fragment=True))
    return raw_debate(t) if t else ""     # a gold with no corpus row has no DEBATE reading at all
grecs = [{"url_raw": (r.get("url") or ""), "title": r.get("title") or "",
          "body": (r.get("text") or "").strip() or (r.get("title") or ""),
          "debate": _gold_slot(r)}
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
          "debate": raw_debate(r), "refresh": is_refresh(r)}
         for r in tone_rows if (r.get("url") or "").strip()]
FBc, inpc, ftc = featurize(crecs)
Xc = np.hstack([FBc, pb(rd1, inpc), pb(rd2, ftc)])
pred = meta.predict(Xc)
out = []
tiern = collections.Counter()
sourcen = collections.Counter()
for i, r in enumerate([t for t in tone_rows if (t.get("url") or "").strip()]):
    k = hashlib.md5((r.get("url") or "").strip().encode()).hexdigest()
    fb = fable.get(k) or (READER.get(k) if is_refresh(r) else None) or {}
    f_flat = (fb.get("cu_alignment") or "").strip().lower()
    deb = raw_debate(r)
    s = pred[i]
    bypass = is_refresh(r) and not fb   # a refresh row with no reader features: the meta-model has nothing to read
    if bypass: s = deb if deb in CL else "neutral"
    # POLICY-FORCE, RELAXED 2026-07-15 (Paul's fresh CFPB gold re-adjudicated the agency-existence axis
    # as DIRECTIONAL: attacking/defunding CFPB = pro-CU/support, defending CFPB = anti-CU/oppose). Keep
    # neutral ONLY when the composed Fable read (f_flat) is itself non-directional; else trust the direction.
    if not bypass and reg_key(fb.get("target"), r.get("title")) in POLICY_KEYS and s in ("support", "oppose"):
        s = f_flat if f_flat in ("support", "oppose") else "neutral"
    # tier reflects MODEL reader agreement (pre-gold, pre-override) so a hand label changes the label but NOT the
    # blend weight (BUG-C fix: mirror the tweet stack, which sets tier from readers not the override). D-221: the
    # slot it compares against is the RAW DEBATE reading, not Paul's overlay label.
    tier = BYPASS_TIER if bypass else ("unanimous" if (s == f_flat == deb) else ("majority" if (s == f_flat or s == deb) else "review"))
    # Paul's labels are ground truth, applied after the stack: his 760 golds, then his hand press labels LAST
    # (beat stack + registry + pin + gold). D-221: the golds used to reach the final label only through the leaked slot.
    label_source = "stack"
    _gold = GOLD_LABEL.get(canonical_url(r.get("url"), keep_fragment=True))
    if _gold in CL: s, label_source = _gold, "gold_760"
    _pov = press_override(r.get("url"), r.get("title"))
    if _pov is not None: s, label_source = _pov, "press_override"
    tiern[tier] += 1
    sourcen[label_source] += 1
    out.append({"url": r.get("url"), "member": r.get("member"), "date": r.get("date"),
                "bioguide":r.get("bioguide"), "title": html.unescape(r.get("title") or ""),
                "stack_label": s, "tier": tier, "debate_stance": deb, "label_source": label_source,
                "source":r.get("source") or "HISTORICAL_PRESS",
                "stack_path": "bypass" if bypass else ("stack_haiku" if is_refresh(r) and k not in fable else "stack")})
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
_paths = collections.Counter(o["stack_path"] for o in out)
print("refresh reader slot: %s (%s) | stack paths: %s" % ("ADMITTED" if READER_ADMITTED else "bypass", READER_DETAIL, dict(_paths)))
print("labels: %s | tiers: %s | changed vs DEBATE: %d (%.0f%%)" % (dict(lab), dict(tiern), chg, 100*chg/len(out)))
print("label sources: %s | gold labels loaded: %d | overlay rows with no raw DEBATE reading: %d (slot left blank)" % (dict(sourcen), len(GOLD_LABEL), len(RAW_MISSING)))
