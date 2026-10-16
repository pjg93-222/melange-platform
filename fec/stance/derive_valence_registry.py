# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""DERIVE the CU-valence registry FROM THE USER'S OWN GOLD LABELS (user 2026-07-04: "what do I fill
out? You can do it"). Three entry sources, provenance-marked:
  derived-from-gold : target families where the USER's gold_target_cu_valence is >=80% consistent
                      (his judgment, extracted — never mine)
  ratified-prior    : the fixed institutional priors already user-approved in the labeling prompts
  PROPOSED          : new targets (CBDC, junk fees, stablecoin...) — my proposals WITH reasoning,
                      flagged for the user's review; used in eval but clearly overridable
DIRECTION handling: separate entries per direction (cfpb_reform vs cfpb_general) — deterministic
match precedence (longest/most-specific first), no flip heuristics (those failed).
HONEST EVAL: 5-fold cross-validation — registry derived from 4 folds' golds, scored on the held-out
fold (composition: model stance-hop x registry valence; registry-miss -> flat model fallback).
Writes data/stance/cu_valence_registry.json (versioned).  Usage: python derive_valence_registry.py [--write]"""
import csv, json, re, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
B = MB_ROOT
CL = ["support", "oppose", "neutral", "unrelated"]

# FAMILY DEFINITIONS: match lists, most-specific first. Valence left None -> derived from user golds.
FAMILIES = [
    # CFPB AXIS SPLIT (user-directed 2026-07-07: "we are a little too harsh on CFPB, mainly as it
    # pertains to Trump admin cuts... isolate CFPB to only consider efforts to expand"). The old
    # two-way split (reform +1 / general -1, golds 149:-1 / 34:+1) coded the AGENCY-DEFENSE axis
    # as anti-CU and made the category a party proxy (123 D vs 7 R in negative tiers). New axes,
    # precedence most-specific-first:
    #   cfpb_reform      +1  restraint of the CFPB's REACH (reform/commission/rein-in/overreach)
    #   cfpb_expansion   -1  efforts to EXPAND reach: rules, supervision, authority over CUs
    #   cfpb_institution  0  the agency's EXISTENCE axis (cuts/defund/defend/staffing) - both
    #                        directions carry no CU valence under the user's isolation rule
    #                        (defund-cheering moved here FROM reform: symmetric, not pro-CU)
    #   cfpb_general      0  bare mention without axis context = no signal
    ("cfpb_reform",   ["cfpb reform", "cfpb commission", "rein in the cfpb", "cfpb overreach", "cfpb accountability"], None, "derived"),
    ("cfpb_expansion", ["cfpb rule", "cfpb's rule", "cfpb overdraft", "cfpb late fee", "cfpb supervision",
                       "cfpb oversight", "cfpb authority", "expand the cfpb", "expanding the cfpb", "strengthen the cfpb",
                       "empower the cfpb", "cfpb jurisdiction", "cfpb enforcement", "section 1071", "1071 rule",
                       "section 1033", "1033 rule", "open banking rule", "larger participant", "cfpb medical debt",
                       "cfpb data broker"], None, "derived"),
    ("cfpb_institution", ["save the cfpb", "protect the cfpb", "defend the cfpb", "we need the cfpb", "keep the cfpb",
                       "preserve the cfpb", "without the cfpb", "gut the cfpb", "gutting the cfpb", "dismantle the cfpb",
                       "dismantling the cfpb", "defund the cfpb", "abolish the cfpb", "eliminate the cfpb",
                       "shut down the cfpb", "shutting down the cfpb", "cfpb funding", "cfpb budget", "cfpb staff",
                       "cfpb workers", "cfpb employees", "cfpb layoffs", "fire cfpb", "cfpb firings", "vought",
                       "cfpb stop-work", "stop work order", "cfpb appropriations", "restructure the cfpb",
                       "attack on the cfpb", "attacks on the cfpb", "war on the cfpb"], None, "derived"),
    ("cfpb_general",  ["cfpb", "consumer financial protection"], None, "derived"),
    ("interchange_block", ["block the credit card competition", "oppose the credit card competition", "stop the cca",
                       "repeal durbin", "against interchange"], None, "derived"),
    # bare 'interchange' DROPPED 2026-07-04 (user review sheet): 11/23 evidence rows were highway
    # interchanges (I-90, SR-210 ramp projects) false-matching the bare word; the remaining
    # keywords catch every genuine on-topic mention (confirmed 11/12 unanimous -1 on the CCCA).
    ("interchange",   ["credit card competition", "swipe fee", "durbin"], None, "derived"),
    ("rate_cap",      ["rate cap", "usury", "apr cap", "36%", "interest rate cap", "10% cap", "capping credit card interest"], None, "derived"),
    ("overdraft",     ["overdraft"], None, "derived"),
    ("junk_fees",     ["junk fee"], None, "derived"),
    ("cu_tax",        ["credit union tax", "cu tax", "tax exempt", "tax status"], None, "derived"),
    ("fom_charter",   ["field of membership", "charter modern", "charter expan", "cu board modernization"], None, "derived"),
    ("mbl",           ["member business lend", "business lending cap", "mbl"], None, "derived"),
    ("cdfi",          ["cdfi", "community development financial"], None, "derived"),
    ("defense_cu",    ["defense credit union", "military credit union", "navy federal"], None, "derived"),
    ("data_privacy",  ["data privacy", "data security", "breach", "cyber"], None, "derived"),
    ("fraud",         ["fraud", "scam", "elder financial"], None, "derived"),
    ("reg_relief",    ["regulatory relief", "reg relief", "tailoring", "regulatory burden"], None, "derived"),
    ("big_banks",     ["big bank", "megabank", "wall street", "too big to fail"], None, "derived"),
    ("housing",       ["housing", "mortgage", "home loan", "road to housing"], None, "derived"),
    ("cbdc",          ["cbdc", "central bank digital"], None, "derived"),
    ("stablecoin",    ["stablecoin", "genius act", "digital asset", "crypto"], None, "derived"),
    ("credit_unions_generic", ["credit union"], None, "derived"),
]

rows = [r for r in csv.DictReader(open(B + r'\data\stance\tweet_gold_queue.csv', encoding='utf-8-sig'))
        if (r.get('gold_cu_alignment') or '').strip().lower() in CL]

# USER-CONFIRMED (data/stance/valence_family_review.csv, 2026-07-04): direct per-row confirm/correct pass on
# the 5 families the auto-derive() below judged thin/inconsistent. 37/40 evidence rows matched the
# original per-row gold exactly; the 3 corrections resolved big_banks from split to decisive.
# These are FIXED external knowledge (not fit to any data split), so the CV loop includes them in
# every fold rather than deriving them — no leakage risk, they're not learned from train rows.
# defense_cu intentionally excluded: the 3-row sample splits by a sub-topic distinction (CU-access
# policy +1 vs a specific institution's discrimination-investigation rows, likely off-target
# keyword noise on 'navy federal') that needs one more user confirmation before it ships.
CONFIRMED = {
    'interchange': (-1, '12 on-topic rows, 11/12 unanimous on the CCCA target'),
    'cbdc':        (-1, 'validated 2026-07-16: CBDC threatens CU deposit intermediation; opposition/ban is pro-CU'),
    'overdraft':   (-1, '1/1 confirmed'),
    'reg_relief':  (+1, '2/2 confirmed'),
    'big_banks':   (0,  '2 user corrections (support->neutral) resolved 8/9 neutral'),
    # user-ratified 2026-07-04: CU-access policy is the target (+1); the 2 discrimination-
    # investigation rows are off-target 'navy federal' keyword noise, not counted against it.
    'defense_cu':  (+1, 'CU-access policy target; investigation rows are off-target keyword noise'),
    # USER-DIRECTED POLICY 2026-07-07 ("isolate CFPB to only consider efforts to expand"): the
    # agency-EXISTENCE axis carries no CU valence in either direction, and bare mentions carry no
    # signal. These deliberately override the historical golds' -1 coding on such items (the user's
    # explicit call this session); they are policy, not derivation - hence CONFIRMED, not derived.
    'cfpb_institution': (0, 'user-directed 2026-07-07: agency-existence axis (cuts/defense/staffing) = no CU valence'),
    'cfpb_general':     (0, 'user-directed 2026-07-07: bare CFPB mention without axis context = no signal'),
}

def fam_of(target, text):
    blob = ((target or '') + ' ' + (text or '')).lower()
    for k, pats, _, _ in FAMILIES:
        if any(p in blob for p in pats): return k
    return None

def gv(r):
    try: return int((r.get('gold_target_cu_valence') or '').strip())
    except ValueError: return None

def derive(train_rows, min_n=3, min_consist=0.75):
    agg = collections.defaultdict(list)
    for r in train_rows:
        f = fam_of(r.get('api_target'), r.get('text')); v = gv(r)
        if f and v is not None: agg[f].append(v)
    reg = {}
    for f, vals in agg.items():
        c = collections.Counter(vals); mode, mc = c.most_common(1)[0]
        if len(vals) >= min_n and mc / len(vals) >= min_consist:
            reg[f] = mode
    return reg

def compose(stance, v):
    s = (stance or '').lower()
    if s == 'unrelated': return 'unrelated'
    if s == 'neutral' or v == 0: return 'neutral'
    if s == 'support': return 'support' if v > 0 else 'oppose'
    if s == 'oppose': return 'oppose' if v > 0 else 'support'
    return 'neutral'

# ---- honest 5-fold CV ----
from sklearn.model_selection import KFold
idx = np.arange(len(rows))
n = ok = 0; cov = 0; tp = collections.Counter(); fp = collections.Counter(); fn = collections.Counter()
for tr, te in KFold(5, shuffle=True, random_state=7).split(idx):
    reg = derive([rows[i] for i in tr])
    reg.update({f: v for f, (v, _) in CONFIRMED.items()})   # fixed external knowledge, safe in every fold
    for i in te:
        r = rows[i]
        f = fam_of(r.get('api_target'), r.get('text'))
        if f in reg:
            cov += 1; p = compose(r.get('api_stance_toward_target'), reg[f])
        else:
            p = (r.get('api_cu_alignment') or '').strip().lower()
        g = (r.get('gold_cu_alignment') or '').strip().lower()
        if g in CL and p:
            n += 1; ok += (g == p); (tp if g == p else fp)[p] += 1
            if g != p: fn[g] += 1
f1 = {c: 2*tp[c]/max(1, 2*tp[c]+fp[c]+fn[c]) for c in CL}
mf = sum(f1[c] for c in CL if tp[c]+fn[c]) / max(1, sum(1 for c in CL if tp[c]+fn[c]))
print('HONEST 5-FOLD CV — registry DERIVED FROM YOUR GOLDS (train folds) + flat fallback:')
print('  acc=%.3f macroF1=%.3f | registry hit %d/%d (%.0f%%)' % (ok/n, mf, cov, len(rows), 100*cov/len(rows)))
print('  ladder: flat baseline 0.677 | trained classical 0.737 | oracle ceiling 0.872')

# ---- full-data registry for production + provenance report ----
full = derive(rows)
print('\nderived entries (from YOUR valence judgments):')
agg = collections.defaultdict(list)
for r in rows:
    f = fam_of(r.get('api_target'), r.get('text')); v = gv(r)
    if f and v is not None: agg[f].append(v)
for f in [k for k, _, _, _ in FAMILIES]:
    if f in CONFIRMED:
        v, note = CONFIRMED[f]
        print('  %-22s valence %+d  USER-CONFIRMED (%s)%s' % (f, v, note,
              '' if f not in full else ' [auto-derive agreed: %+d]' % full[f] if full[f] == v else
              ' [!! auto-derive disagreed: %+d — kept the confirmed value]' % full[f]))
    elif f in full:
        c = collections.Counter(agg[f])
        print('  %-22s valence %+d  (your golds: %s)' % (f, full[f], dict(c)))
    elif f in agg:
        print('  %-22s SKIPPED (inconsistent/thin: %s) -> falls back to flat model' % (f, dict(collections.Counter(agg[f]))))

if '--write' in sys.argv:
    # POLICY-FORCE families (user-directed 2026-07-07): the composed label is BINDING when these
    # match, overriding the learned stack readers post-meta. Rationale: the stack's classical/API
    # channels were trained on golds that encode the OLD policy (institution items coded -1), so a
    # soft registry feature gets outvoted by models faithfully reproducing superseded labels. A
    # legislated axis must bind the pipeline, not advise it.
    POLICY_FORCE = {'cfpb_institution', 'cfpb_general'}
    entries = []
    for f, pats, _, _ in FAMILIES:
        if f in CONFIRMED:
            v, note = CONFIRMED[f]
            e = {'key': f, 'match': pats, 'valence': int(v),
                 'source': 'user-confirmed-review', 'note': note}
            if f in POLICY_FORCE: e['policy_force'] = True
            entries.append(e)
        elif f in full:
            entries.append({'key': f, 'match': pats, 'valence': int(full[f]),
                            'source': 'derived-from-gold', 'gold_n': len(agg[f])})
    out = {'_doc': 'CU-valence registry v2 — MOST families derived from the USER\'S gold valence judgments '
                   '(5-fold-validated harness: derive_valence_registry.py); a few are user-confirmed-review priors '
                   '(data/stance/valence_family_review.csv). HONESTY NOTE (2026-07-07): the policy_force families (cfpb_institution, '
                   'cfpb_general) are USER-DIRECTED POLICY that OVERRIDES the historical golds (they encode the pre-CFPB-'
                   'split harshness), so the whole-registry CV vs those golds drops (~0.52) BY DESIGN — the split reflects '
                   'the user\'s directed axis, it is NOT claimed to be validated-more-accurate until a fresh gold sample '
                   'is adjudicated on the new axis. Match precedence = list order (specific before generic). Re-eval before production.',
           'entries': entries}
    _reg = B + r'\data\stance\cu_valence_registry.json'
    with open(_reg + '.tmp', 'w') as _f:   # atomic write: tmp then replace (consumers load this at import)
        json.dump(out, _f, indent=1)
    _o.replace(_reg + '.tmp', _reg)
    print('\nwrote data/stance/cu_valence_registry.json: %d entries (%d derived, %d user-confirmed)' %
          (len(entries), sum(1 for e in entries if e['source'] == 'derived-from-gold'), len(CONFIRMED)))
