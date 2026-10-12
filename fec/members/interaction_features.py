# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""CONVERSATIONAL-DYNAMICS FEATURES (Big5 campaign, research-ranked build order 2026-07-04).
Literature basis (headline validity): words-per-turn / verbal output = top observed-Extraversion
feature (Mairesse JAIR'07: word count roots the E tree; Valente Interspeech'12: speech-activity
features alone = 73.5% binary E; Mehl JPSP'06 talking-E); overlap/interruption involvement = the
only feature family recovering Neuroticism (Valente 68.7%) and +E (Yu Interspeech'19: high-E in
243 vs 182 overlaps); turn stats = replicated dominance signal (Rienks & Heylen'05).
Transcript proxies (member turns only; host speech dropped at ingest; stage markers stripped):
  wpt_*        words-per-turn distribution (mean/median/p90) + floor-holding Gini    [E+, dom+]
  cutoff       turn ends '--'/'—'/'…'/no terminal punct = cut off mid-turn           [overlap involvement]
  burst        share of turns inside runs of >=3 consecutive short turns (<10 w)     [contested floor, E+]
  defense      floor-defense phrases /1k words ('let me finish','hold on',...)       [dom+/A-, exploratory]
  discmark     discourse markers /1k words ('you know','i mean')                     [C+, small (Laserna'14)]
  agree        complete short agreement turns share ('right','exactly',...)          [E+/A+, exploratory]
CONFOUND CONTROLS (mandatory per the survey): every feature z-scored WITHIN PROGRAM (format
manufactures overlap — Canal9; debates != sit-downs), member score = turn-weighted mean of
per-program z; all rates per-turn or per-1k-words (airtime control). INTERVIEW channel only for
traits; CHRG hearing cutoff-received kept as a separate CONTEXT covariate (being interrupted is
the interrupter's act, not the member's own conduct). NOTE 2026-07-10: the former blanket doctrine
here ("interruptions received in hearings are role/target-driven — Miller & Sutherland — not
disposition") was a session's methodological choice, never a user rule, and is WITHDRAWN by user
directive: hearing conduct COUNTS under the role-scope rule — within the normal scope of
chair/ranking-member duties it does not count; beyond it (temperamental escalation, or relenting,
which signals agreeableness) it does. User's rule of thumb, verbatim: "is this beyond what is
required of them". The agentic hearing channel lives in fetch_hearings_antag.py (hearing_antag2
tables; builder pending) — this module stays interview-only for its trait features.
These estimate REPUTATION (observer-perceived traits) — document as such; validate convergence vs
lexical Big5 E (expect r~.2-.4 if both channels work; more = suspect overfit).
Output: interaction_features.json. Read-only on social_media.db; no API."""
import json, math, os, re, sqlite3, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT

DEFENSE = re.compile(r"\b(let me finish|may i finish|i'?m not finished|if i could just finish|hold on|wait a (minute|second)|as i was saying|excuse me[,.]? (i|let))\b", re.I)
DISCMARK = re.compile(r"\b(you know|i mean)\b", re.I)

def program_of(pkg):
    p = (pkg or "").lower()
    if "transcripts.cnn.com" in p:
        m = re.search(r"/show/(\w+)/", p)
        return "cnn:" + (m.group(1) if m else "?")
    if "nbcnews" in p: return "mtp"
    if "pbs.org" in p: return "pbs"
    if "npr.org" in p: return "npr"
    if "c-span.org/program/" in p:
        m = re.search(r"c-span\.org/program/([^/]+)/", p)
        return "cspan:" + (m.group(1) if m else "?")
    return "other"

def cutoff(t):
    t = t.strip()
    return t.endswith("--") or t.endswith("—") or t.endswith("…") or t.endswith("...") or (t and t[-1] not in ".!?\"')”")

con = sqlite3.connect(B + r"\social_media.db", timeout=30)
con.execute("PRAGMA query_only=1")

# ---- interviews: per member x program raw features, then within-program z ----
# appearance = (member, package); turns in rowid order within appearance
app = collections.defaultdict(list)
for m, pkg, t, rid in con.execute("select member, package, text, rowid from speeches where source='INTERVIEW' order by rowid"):
    if t and t.strip(): app[(m, program_of(pkg), pkg)].append(t.strip())

# 'agree' dropped: the ^...$ end-anchored AGREE regex never matched a substantive transcript turn (every
# collector enforces an >=8-word turn floor, so a 1-3-word assent token could never BE a whole turn), so
# the raw count was 0 for every member and the z-scored column was a dead 0-for-all — no consumer read it
# (big5_hybrid IXF and behavioral_dispositions both exclude it). Fully removed: not computed, not in
# FEATS, not in the output -- rather than ship a zero-variance feature.
FEATS = ["wpt_mean", "wpt_p90", "gini", "cutoff", "burst", "defense", "discmark"]
mp = collections.defaultdict(lambda: collections.defaultdict(list))   # (program) -> feature -> [(member, value, nturns)]
raw_by_member_prog = collections.defaultdict(dict)
prog_members = collections.defaultdict(set)
acc = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0, 0.0, []]))  # member -> prog -> [words, turns, ...]
per = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(float)))
cnt = collections.defaultdict(lambda: collections.defaultdict(float))

agg = collections.defaultdict(lambda: collections.defaultdict(lambda: {"turns": 0, "words": 0, "cut": 0, "burst": 0,
                                                                        "defense": 0, "disc": 0, "lens": []}))
for (m, prog, pkg), turns in app.items():
    a = agg[m][prog]
    short_run = 0
    for i, t in enumerate(turns):
        w = len(t.split())
        a["turns"] += 1; a["words"] += w; a["lens"].append(w)
        if cutoff(t): a["cut"] += 1
        a["defense"] += len(DEFENSE.findall(t))
        a["disc"] += len(DISCMARK.findall(t))
        if w < 10:
            short_run += 1
            if short_run == 3: a["burst"] += 3
            elif short_run > 3: a["burst"] += 1
        else: short_run = 0

def gini(xs):
    if len(xs) < 2: return 0.0
    xs = sorted(xs); n = len(xs); s = sum(xs)
    if s == 0: return 0.0
    return (2 * sum((i + 1) * x for i, x in enumerate(xs)) / (n * s)) - (n + 1) / n

rawrows = collections.defaultdict(dict)   # prog -> member -> {feat: v, turns}
for m, progs in agg.items():
    for prog, a in progs.items():
        if a["turns"] < 12: continue   # per-program floor: too few turns = noise
        lens = a["lens"]; lens_s = sorted(lens)
        rawrows[prog][m] = {"turns": a["turns"],
            "wpt_mean": a["words"] / a["turns"],
            "wpt_p90": lens_s[int(0.9 * (len(lens_s) - 1))],
            "gini": gini(lens),
            "cutoff": a["cut"] / a["turns"],
            "burst": a["burst"] / a["turns"],
            "defense": 1000.0 * a["defense"] / max(1, a["words"]),
            "discmark": 1000.0 * a["disc"] / max(1, a["words"])}

# within-program z, then member = turn-weighted mean of per-program z
zsum = collections.defaultdict(lambda: collections.defaultdict(float))
zwt = collections.defaultdict(float)
minfo = collections.defaultdict(lambda: {"turns": 0, "programs": set()})
for prog, mem in rawrows.items():
    if len(mem) < 8: continue   # program needs enough members to define a distribution
    for f in FEATS:
        vals = [d[f] for d in mem.values()]
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / max(1, len(vals) - 1)) ** 0.5 or 1.0
        for m, d in mem.items():
            zsum[m][f] += ((d[f] - mu) / sd) * d["turns"]
    for m, d in mem.items():
        zwt[m] += d["turns"]; minfo[m]["turns"] += d["turns"]; minfo[m]["programs"].add(prog)

out = {}
for m, w in zwt.items():
    out[m] = {f: round(zsum[m][f] / w, 3) for f in FEATS}
    out[m]["turns"] = minfo[m]["turns"]; out[m]["n_programs"] = len(minfo[m]["programs"])

# ---- hearings: cutoff-received as CONTEXT covariate only ----
hx = collections.defaultdict(lambda: [0, 0])
for m, t in con.execute("select member, text from speeches where source='CHRG'"):
    if not t or not t.strip(): continue
    hx[m][1] += 1
    if cutoff(t): hx[m][0] += 1
hear = {m: {"cutoff_received": round(c / n, 3), "turns": n} for m, (c, n) in hx.items() if n >= 15}

from moneyball_core import dump_atomic
dump_atomic({"members": out, "hearing_covariate": hear, "feats": FEATS,
             "method": "interview interaction dynamics, per-program z (>=12 turns/program, >=8 members/program), "
                       "turn-weighted member aggregate; measures OBSERVER-PERCEIVED traits (reputation); "
                       "hearing cutoff-received kept separate (receiving a cutoff is the interrupter's act; "
                       "agentic hearing conduct counts via fetch_hearings_antag.py under the 2026-07-10 "
                       "user role-scope rule)"},
            B + r"\data\lobbying\interaction_features.json")
print("interaction features: %d members (interviews) | hearing covariate: %d members" % (len(out), len(hear)))
top = sorted(out, key=lambda m: -out[m]["wpt_mean"])[:5]
bot = sorted(out, key=lambda m: out[m]["wpt_mean"])[:5]
print("verbal output z high:", ", ".join("%s %+0.1f" % (m, out[m]["wpt_mean"]) for m in top))
print("verbal output z low: ", ", ".join("%s %+0.1f" % (m, out[m]["wpt_mean"]) for m in bot))
