# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""BIG5 TEXT INSTRUMENT v2 — BEHAVIOR-CALIBRATED (user 2026-07-04: 'we are going to make it work').
v1 failed the nomological battery because its feature WEIGHTS were fixed literature priors
(Pennebaker/Mairesse essay correlates + essays/RKH SMOreg) that don't transfer to drafted political
speech. v2 keeps the same text FEATURES (big5_traits.json featz — the standardized category-rate
vectors) and LEARNS the weights against the behavioral criteria, evaluated with honest 5-fold
OUTER cross-validation (fit on train members, score held-out members — the reported r cannot
overfit). Trait -> behavioral calibration target (pre-registered construct roles):
    A  <- cooperation_z   (bipartisanship + cosponsor embedding)     [channel-clean]
    C  <- diligence_z     (roll-call participation)                  [channel-clean]
    E  <- visibility_z    (broadcast engagement)  [mild common-source: text volume; flagged]
    O  <- breadth_z       (legislative portfolio breadth)            [channel-clean]
    ES <- (no behavioral criterion exists) — v2 carries the genre-general composite ES when
          available (best structural instrument: split-half .92), else v1 ES.
Ridge alpha by inner CV. Outputs: big5_v2.json (traits, z-scored) + liwc/behavior_calibrated.json.candidate
(weights in the {trait:{feature:weight}} format big5_traits.py's hook consumes — promotion is a
separate, ratified step). PASS bar: held-out r >= 0.25 on the channel-clean traits (A, C, O)."""
import json, math, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
B = MB_ROOT

BT = json.load(open(B + r"\data\members\big5_traits.json"))
FZ = BT.get("featz") or {}
if not FZ: print("!! big5_traits.json has no featz — run big5_traits.py"); sys.exit(1)
BD = json.load(open(B + r"\behavioral_dispositions.json"))["members"]
FEATS = sorted(next(iter(FZ.values())))
TARGET = {"A": "cooperation_z", "C": "diligence_z", "E": "visibility_z", "O": "breadth_z"}

def heldout_r(X, y):
    """5-fold outer CV; inner grid for alpha. Returns mean held-out Pearson r + per-fold list."""
    rs = []
    for tr, te in KFold(5, shuffle=True, random_state=7).split(X):
        g = GridSearchCV(Ridge(), {"alpha": [1, 3, 10, 30, 100, 300]}, cv=3,
                         scoring="neg_mean_squared_error")
        g.fit(X[tr], y[tr])
        p = g.predict(X[te])
        if p.std() < 1e-9: rs.append(0.0); continue
        rs.append(float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs)), rs

print("BIG5 v2 — behavior-calibrated text instrument (nested CV, honest held-out)")
out_traits = {}; weights = {}; ho = {}
for tr, tgt in TARGET.items():
    ms = [m for m in FZ if m in BD and tgt in BD[m]]
    X = np.array([[FZ[m][f] for f in FEATS] for m in ms])
    y = np.array([BD[m][tgt] for m in ms])
    r, folds = heldout_r(X, y)
    # v1 comparison on the same members: v1 trait z vs the same behavioral target
    v1 = np.array([BT["traits"][m][tr] for m in ms])
    rv1 = float(np.corrcoef(v1, y)[0, 1]) if v1.std() > 0 else 0.0
    ho[tr] = {"heldout_r": round(r, 3), "folds": [round(x, 2) for x in folds],
              "v1_r_same_members": round(rv1, 3), "n": len(ms), "target": tgt}
    print("  %-2s <- %-14s n=%-3d  v2 HELD-OUT r=%+.3f (folds %s)   v1 r=%+.3f" % (
        tr, tgt, len(ms), r, ",".join("%+.2f" % x for x in folds), rv1))
    # final model on all members -> scores + exportable weights
    g = GridSearchCV(Ridge(), {"alpha": [1, 3, 10, 30, 100, 300]}, cv=5,
                     scoring="neg_mean_squared_error")
    g.fit(X, y)
    pred = g.predict(np.array([[FZ[m][f] for f in FEATS] for m in FZ]))
    mu, sd = float(pred.mean()), float(pred.std()) or 1.0
    for i, m in enumerate(FZ):
        out_traits.setdefault(m, {})[tr] = round((pred[i] - mu) / sd, 3)
    coef = g.best_estimator_.coef_
    weights[tr] = {FEATS[i]: round(float(coef[i]), 4) for i in range(len(FEATS)) if abs(coef[i]) > 1e-4}

# ES: genre-general composite when available (structurally strongest), else v1
comp_p = os.environ.get("B5_COMPOSITE",os.path.join(B,"source_data","big5_composite.json"))
COMP = json.load(open(comp_p))["traits"] if os.path.exists(comp_p) else {}
es_src = {"composite": 0, "v1": 0}
for m in FZ:
    if m in COMP and "ES" in COMP[m]:
        out_traits[m]["ES"] = COMP[m]["ES"]; es_src["composite"] += 1
    else:
        out_traits[m]["ES"] = BT["traits"][m]["ES"]; es_src["v1"] += 1
    out_traits[m]["N"] = round(-out_traits[m]["ES"], 3)
    out_traits[m]["tokens"] = BT["traits"][m].get("tokens", 0)
print("  ES carried from: %s" % es_src)

from moneyball_core import dump_atomic
dump_atomic({"traits": out_traits, "heldout": ho,
             "method": "v2 behavior-calibrated: ridge(featz -> behavioral disposition) per trait, "
                       "nested 5-fold CV honest held-out; ES = genre-general composite fallback v1; "
                       "z within scored pool"}, B + r"\data\members\big5_v2.json")
json.dump(weights, open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "liwc",
                                     "behavior_calibrated.json.candidate"), "w"), indent=1)
print("wrote big5_v2.json (%d members) + liwc/behavior_calibrated.json.candidate" % len(out_traits))
print("PASS bar (channel-clean traits A/C/O): held-out r >= 0.25")
