# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""GOLD QUEUE — hand-label queue for the broadcast/podcast INTERVIEW CU-stance channel (2026-07-15).

Reads interviews_cu.csv and emits interview_gold_queue.csv for Paul to hand-label the gold_stance
column (support / oppose / neutral).  API-FREE, no network, no spend.

Mirrors build_speech_gold_queue.py EXACTLY (same id hash, same registry pre-label, same 12 columns).
The ONE channel difference: the CU-relevant interview set is tiny (<250 rows), so TARGET and MEMBER_CAP
are set very high — the queue is the FULL interviews_cu.csv (no down-sampling); category-coverage and
priority ordering still run (deterministic) but never exclude a row.

Sampling (deterministic, member-capped):
  1. category coverage  — guarantee every routed category appears at least once
  2. priority oversample — the highest-volume CU interviewees, up to MEMBER_CAP each, CFPB-first
  3. CFPB oversample + fill — remaining slots, CFPB-mentioning rows first, to TARGET

Each sampled interview gets a FREE registry pre-label (from data/stance/cu_valence_registry.json) as a HINT only:
  * registry_target / registry_valence : first-matching family (list-order precedence, substring)
  * prelabel_stance : a directional GUESS from attack/defend cue words for CFPB / interchange /
    anti-CU-valenced targets (Credit Unions OPPOSE the CFPB: attacking CFPB = support/pro-CU,
    defending CFPB = oppose/anti-CU).  Kept clearly separate from Paul's blank gold_stance column.
"""
import sys, os, csv, json, re, collections, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SRC   = os.path.join(MB_ROOT, 'data', 'stance', 'interviews_cu.csv')
REG   = os.path.join(MB_ROOT, 'data/stance/cu_valence_registry.json')
OUT   = os.path.join(MB_ROOT, 'data', 'stance', 'interview_gold_queue.csv')
# The CU-relevant interview set is < 250 rows: LABEL ALL OF IT. High TARGET + MEMBER_CAP => the mirrored
# selection includes every row (no down-sampling); ordering steps still run deterministically.
TARGET     = 100000
MEMBER_CAP = 9999
SNIPPET    = 1200
CFPB_CAT   = 'CFPB Issues & Structural Reform'

# Highest-volume CU interviewees to oversample — EXACT canonical names. With MEMBER_CAP high enough to
# admit everyone, this list only affects ordering within the coverage/priority passes, never inclusion.
PRIORITY = ['Elizabeth Warren', 'Richard Blumenthal', 'Bernard Sanders', 'Jamie Raskin',
            'Mitch McConnell']
PRIORITY_SET = set(PRIORITY)

# ---- registry: entries in list order = match precedence ----
_reg = json.load(open(REG, encoding='utf-8'))['entries']
REG_ENTRIES = [(e['key'], [m.lower() for m in e['match']], int(e['valence'])) for e in _reg]
# families for which we attempt a directional prelabel: the CFPB subject families, interchange,
# and any anti-CU-valenced (-1) target.
DIRECTIONAL = {k for k, _m, v in REG_ENTRIES
               if k.startswith('cfpb') or k in ('interchange', 'rate_cap', 'overdraft', 'junk_fees')
               or v == -1}

# attack CFPB -> pro-CU -> support ; defend CFPB -> anti-CU -> oppose (task-specified cue lists).
# word-boundaries so 'gut' (attack/support) does not fire on 'gutting' (defend/oppose).
SUPPORT_CUES = [r'\brein (?:it |them )?in\b', r'\bdefund', r'\bgut\b', r'\babolish', r'\boverreach',
                r'\bunaccountable\b', r'\brogue\b']
OPPOSE_CUES  = [r'\bprotect', r'\bdefend', r'\bsave\b', r'\bstrengthen', r'\bexpand',
                r'\bgutting\b', r'\battack on\b']
_SUP_RX = [re.compile(p) for p in SUPPORT_CUES]
_OPP_RX = [re.compile(p) for p in OPPOSE_CUES]


def registry_match(text_low):
    for key, matches, val in REG_ENTRIES:
        for m in matches:
            if m in text_low:
                return key, val
    return '', ''


# BUG-G: cues must be NEAR the CFPB/target mention, not anywhere in a long interview. Generic verbs
# like "protect"/"defend" fired target-agnostically before, producing oppose-biased/inverted hints.
_ANCHOR_RX = re.compile(r'cfpb|consumer financial protection|interchange|swipe fee|credit card competition|durbin|overdraft|junk fee|rate cap')   # B3 audit fix: mirrors the stack's anchor list exactly
def prelabel(text_low, family):
    if family not in DIRECTIONAL:
        return 'neutral'
    anchors = [m.start() for m in _ANCHOR_RX.finditer(text_low)]
    if not anchors:
        return 'neutral'
    W = 120
    windows = ' | '.join(text_low[max(0, a - W):a + W] for a in anchors)
    windows = re.sub(r'(?:consumer )?financial protection( bureau| board| agency)?', ' cfpb ', windows)  # F2: mask the agency's own name so the \bprotect DEFEND cue does not self-fire on '...Financial PROTECTION Bureau'   # only text near a target mention
    atk = any(rx.search(windows) for rx in _SUP_RX)
    dfd = any(rx.search(windows) for rx in _OPP_RX)
    if atk and not dfd:
        return 'support'
    if dfd and not atk:
        return 'oppose'
    return 'neutral'


# ---- load source rows, assign stable id = content hash (survives a DB rebuild / re-extraction) ----
rows = []
for i, r in enumerate(csv.DictReader(open(SRC, encoding='utf-8-sig'))):
    # BUG-D: content-hash id binds a hand label to the row's content, not its scan position, so labels
    # survive a DB rebuild / re-extraction (the extract is now also deterministically ORDER BY'd).
    r['id'] = 'x' + hashlib.md5(('%s|%s|%s' % (r.get('bioguide',''), r.get('date',''), r.get('text',''))).encode('utf-8', 'replace')).hexdigest()[:12]
    r['cats'] = r['categories'].split(';') if r['categories'] else []
    r['is_cfpb'] = CFPB_CAT in r['cats']
    r['is_prio'] = r['member'] in PRIORITY_SET
    rows.append(r)
by_id = {r['id']: r for r in rows}
assert len(by_id) == len(rows), 'content-hash id collision: %d rows -> %d ids' % (len(rows), len(by_id))   # B5 audit fix: a collision must be LOUD

member_count = collections.Counter()
selected = []            # ordered list of ids
selected_set = set()


def add(rid, force=False):
    r = by_id[rid]
    if rid in selected_set:
        return False
    if not force and member_count[r['member']] >= MEMBER_CAP:
        return False
    selected.append(rid)
    selected_set.add(rid)
    member_count[r['member']] += 1
    return True


def ranked(pool):
    """Stable-sort a pool: priority first, CFPB first, then newest date, then higher id."""
    pool = sorted(pool, key=lambda r: (r['date'], r['id']), reverse=True)   # newest / higher id first
    pool.sort(key=lambda r: (0 if r['is_prio'] else 1, 0 if r['is_cfpb'] else 1))  # stable primary
    return pool


# 1) category coverage — every routed category present at least once
for cat in sorted({c for r in rows for c in r['cats']}):
    if any(cat in by_id[i]['cats'] for i in selected):
        continue
    cands = ranked([r for r in rows if cat in r['cats'] and r['id'] not in selected_set])
    placed = any(add(r['id']) for r in cands)
    if not placed and cands:                 # guarantee coverage even if the member cap blocks
        add(cands[0]['id'], force=True)

# 2) priority members — fill each up to MEMBER_CAP, CFPB-first
for m in PRIORITY:
    for r in ranked([r for r in rows if r['member'] == m and r['id'] not in selected_set]):
        if member_count[m] >= MEMBER_CAP:
            break
        add(r['id'])

# 3) CFPB oversample + general fill to TARGET
for r in ranked([r for r in rows if r['id'] not in selected_set]):
    if len(selected) >= TARGET:
        break
    add(r['id'])

# ---- assemble output rows (ordered by member, then date, for human labeling) ----
sel_rows = [by_id[i] for i in selected]
sel_rows.sort(key=lambda r: (r['member'], r['date'], r['id']))

out = []
prelabel_dist = collections.Counter()
for r in sel_rows:
    tlow = r['text'].lower()
    fam, val = registry_match(tlow)
    guess = prelabel(tlow, fam)
    prelabel_dist[guess] += 1
    out.append({
        'id': r['id'],
        'bioguide': r['bioguide'],
        'member': r['member'],
        'date': r['date'],
        'source': r.get('source', ''),             # INTERVIEW only after the source filter (auditable)
        'chamber': r['chamber'],
        'categories': r['categories'],
        'registry_target': fam,
        'registry_valence': val,
        'prelabel_stance': guess,
        'gold_stance': '',                         # BLANK — Paul fills support/oppose/neutral
        'text_snippet': r['text'][:SNIPPET],
    })

cols = ['id', 'bioguide', 'member', 'date', 'source', 'chamber', 'categories', 'registry_target',
        'registry_valence', 'prelabel_stance', 'gold_stance', 'text_snippet']
# F1 label-preservation: NEVER destroy hand labels on a re-run. Merge any previously-filled
# gold_stance from the existing OUT file (keyed by the content-hash id), and RE-APPEND any labeled
# row that fell out of a new sample so no hand label is ever orphaned.
_prev = {}
if os.path.exists(OUT):
    with open(OUT, encoding='utf-8-sig', newline='') as _pf:
        for _pr in csv.DictReader(_pf):
            if (_pr.get('gold_stance') or '').strip():
                _prev[(_pr.get('id') or '').strip()] = _pr
if _prev:
    _seen = set()
    for _row in out:
        _seen.add(_row['id'])
        if _row['id'] in _prev:
            _row['gold_stance'] = (_prev[_row['id']].get('gold_stance') or '').strip()
    _reapp = [ _id for _id in _prev if _id and _id not in _seen ]
    for _id in _reapp:
        out.append({_c: (_prev[_id].get(_c, '') or '') for _c in cols})
    out.sort(key=lambda r: (r.get('member', ''), r.get('date', ''), r.get('id', '')))
    print('label-preservation: merged %d prior gold labels (%d re-appended out-of-sample)' % (len(_prev), len(_reapp)))
tmp = OUT + '.tmp'
with open(tmp, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(out)
os.replace(tmp, OUT)

# ---- report ----
sample_cats = collections.Counter(c for r in sel_rows for c in r['cats'])
source_cats = {c for r in rows for c in r['cats']}
cfpb_n = sum(1 for r in sel_rows if r['is_cfpb'])
print('=== build_interview_gold_queue ===')
print('queue size                         : %d  (target %d, member cap %d)' % (len(out), TARGET, MEMBER_CAP))
print('distinct members in sample         : %d' % len({r['member'] for r in sel_rows}))
print('CFPB-mentioning rows               : %d  (%.0f%%)' % (cfpb_n, 100.0 * cfpb_n / len(out) if out else 0))
print('category coverage                  : %d / %d routed categories present'
      % (len(sample_cats), len(source_cats)))
missing = source_cats - set(sample_cats)
if missing:
    print('  MISSING CATEGORIES: ' + ', '.join(sorted(missing)))
print('wrote                              : %s' % OUT)
print('--- priority members (count in sample) ---')
for m in PRIORITY:
    print('  %-20s %d' % (m, member_count[m]))
print('--- prelabel_stance distribution ---')
for k in ('support', 'oppose', 'neutral'):
    print('  %-8s %d' % (k, prelabel_dist[k]))
print('--- category distribution in sample ---')
for c, n in sample_cats.most_common():
    print('  %-40s %d' % (c, n))
