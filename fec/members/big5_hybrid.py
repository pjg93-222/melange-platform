# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""BIG5 TEXT INSTRUMENT v3 — HYBRID (door 3, user-ratified 2026-07-04: lexical + interaction
dynamics + persona cues in ONE behavior-calibrated model; no LLM spend).
Feature matrix per member: featz (standardized lexical category rates, ~30) + interaction-channel
features (8: words-per-turn, floor Gini, cutoff, burst, defense, discourse markers... per-program z;
0-imputed + availability flag) + persona cues (agency/communion z; 0-imputed + flag).
Same calibration protocol as v2 (big5_lexcal_v2.py): per trait, ridge vs the behavioral target,
nested 5-fold CV, HONEST HELD-OUT r. v2 (lexical-only) re-fit on the identical member subset for a
fair side-by-side. ES = genre-general composite fallback v1 (no behavioral criterion).
NOTE: interaction features are nickname-degraded until the interview re-ingest completes — the
post-re-ingest rebuild reruns this; treat pre-re-ingest numbers as the floor."""
import json, math, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
B = MB_ROOT

BT = json.load(open(B + r"\data\members\big5_traits.json"))
FZ = BT.get("featz") or {}
BD = json.load(open(B + r"\behavioral_dispositions.json"))["members"]
IXP = B + r"\data\lobbying\interaction_features.json"
IX = {m: d for m, d in (json.load(open(IXP))["members"] if os.path.exists(IXP) else {}).items() if d.get("turns", 0) >= 30}
AC = {m: d for m, d in json.load(open(B + r"\data\members\agency_communion.json"))["members"].items() if d.get("n", 0) >= 15}
_ag = [d["agency"] for d in AC.values()]; _co = [d["communion"] for d in AC.values()]
_ma, _mc = sum(_ag)/len(_ag), sum(_co)/len(_co)
_sa = (sum((x-_ma)**2 for x in _ag)/max(1, len(_ag)-1))**.5 or 1.0
_sc = (sum((x-_mc)**2 for x in _co)/max(1, len(_co)-1))**.5 or 1.0

LEX = sorted(next(iter(FZ.values())))
IXF = ["wpt_mean", "wpt_p90", "gini", "cutoff", "burst", "defense", "discmark"]
# NO availability flags: has-interview-coverage / has-persona-coverage are VOLUME proxies that leak
# into volume-flavored targets (E<-visibility went +.69 with flags — circular). The style features
# themselves are per-program z (volume-normalized) and legitimate; missing -> 0 (neutral).
def vec(m):
    v = [FZ[m][f] for f in LEX]
    ix = IX.get(m)
    v += [ix[f] for f in IXF] if ix else [0.0] * len(IXF)
    ac = AC.get(m)
    v += [(ac["agency"]-_ma)/_sa, (ac["communion"]-_mc)/_sc] if ac else [0.0, 0.0]
    return v
NLEX = len(LEX)

TARGET = {"A": "cooperation_z", "C": "diligence_z", "E": "visibility_z", "O": "breadth_z"}
def heldout_r(X, y):
    rs = []
    for tr, te in KFold(5, shuffle=True, random_state=7).split(X):
        g = GridSearchCV(Ridge(), {"alpha": [1, 3, 10, 30, 100, 300]}, cv=3, scoring="neg_mean_squared_error")
        g.fit(X[tr], y[tr]); p = g.predict(X[te])
        rs.append(0.0 if p.std() < 1e-9 else float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs)), rs

print("BIG5 v3 HYBRID — lexical + interaction + persona, behavior-calibrated (honest held-out)")
print("  channel coverage: interaction %d | persona %d | lexical %d" % (len(IX), len(AC), len(FZ)))
out_traits = {}; ho = {}
for tr, tgt in TARGET.items():
    ms = [m for m in FZ if m in BD and tgt in BD[m]]
    X = np.array([vec(m) for m in ms]); y = np.array([BD[m][tgt] for m in ms])
    r3, folds = heldout_r(X, y)
    r2, _ = heldout_r(X[:, :NLEX], y)   # lexical-only on the SAME members = fair v2 baseline
    ho[tr] = {"hybrid_heldout_r": round(r3, 3), "lexonly_heldout_r": round(r2, 3),
              "n": len(ms), "target": tgt, "folds": [round(x, 2) for x in folds]}
    print("  %-2s <- %-14s n=%-3d  HYBRID r=%+.3f   lexical-only r=%+.3f   %s" % (
        tr, tgt, len(ms), r3, r2, "HYBRID WINS" if r3 > r2 + 0.02 else ("tie" if r3 > r2 - 0.02 else "lexical wins")))
    g = GridSearchCV(Ridge(), {"alpha": [1, 3, 10, 30, 100, 300]}, cv=5, scoring="neg_mean_squared_error")
    g.fit(X, y)
    allm = list(FZ)
    pred = g.predict(np.array([vec(m) for m in allm]))
    mu, sd = float(pred.mean()), float(pred.std()) or 1.0
    for i, m in enumerate(allm):
        out_traits.setdefault(m, {})[tr] = round((pred[i] - mu) / sd, 3)

comp_p = os.environ.get("B5_COMPOSITE",os.path.join(B,"source_data","big5_composite.json"))
COMP = json.load(open(comp_p))["traits"] if os.path.exists(comp_p) else {}
for m in out_traits:
    es = COMP.get(m, {}).get("ES", BT["traits"].get(m, {}).get("ES", 0.0))
    out_traits[m]["ES"] = es; out_traits[m]["N"] = round(-es, 3)
    out_traits[m]["tokens"] = BT["traits"].get(m, {}).get("tokens", 0)

from moneyball_core import dump_atomic
dump_atomic({"traits": out_traits, "heldout": ho,
             "method": "v3 hybrid behavior-calibrated: ridge(featz + interaction + persona -> behavioral "
                       "disposition) per trait, nested 5-fold honest held-out; ES = composite fallback v1"},
            B + r"\data\members\big5_v3_hybrid.json")
print("wrote big5_v3_hybrid.json (%d members)" % len(out_traits))
