# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""HEARING STANCE STACK â€” API-FREE committee-hearing CU-stance labeler (2026-07-15).

Mirrors speech_stance_stack.py / tweet_stance_stack.py / press_stance_stack.py: registry two-hop
compose + the RELAXED CFPB policy_force pin + a hand-label (gold) override applied LAST.  NO API, NO
DEBATE, NO network, NO spend.  Scores the member's OWN words questioning witnesses in a hearing
(source='CHRG').

Precedence per hearing row (highest wins):
  (a) GOLD      â€” Paul's hearing_gold_queue.csv gold_stance where filled, matched by the content-hash
                  id = md5(bioguide|date|text)[:12] (identical to build_hearing_gold_queue.py, so a
                  label binds to the row's CONTENT, surviving a DB rebuild).  Ground truth; tier='gold'.
  (b) COMPOSE   â€” the matched registry family's CU valence x a stance-toward-target derived from
  (c) HEURISTIC â€” a target-scoped attack/defend cue read within +/-120 chars of a CFPB / interchange /
                  overdraft / swipe-fee / rate-cap anchor (mirrors build_hearing_gold_queue.prelabel,
                  BUG-G windows).  RELAXED CFPB directionality: attacking/defunding the CFPB *agency*
                  = support (pro-CU), defending it = oppose (anti-CU); a non-directional cue read keeps
                  neutral (the anti-populist neutral guard).  tier='review'.

WITNESS-ATTRIBUTION CAVEAT: CHRG is under an attribution quarantine â€” a witness surname can collide
with a member surname (Gov. Michael Barr vs Rep. Andy Barr) and big5_traits.py defaults CHRG OUT of
the personality text corpus.  Resolution upstream is fail-closed (unresolved rows dropped), but the
heuristic still cannot tell a member from a mis-stored speaker or tell a critic quoting "the CFPB's
defenders" from an actual defender.  That is why the channel is gated OFF by default and Paul's gold
(precedence a) is the correction path.

Output: hearing_stance_final.csv (bioguide, member, date, categories, stance, tier).  build_media_blend.py
consumes it ONLY when CU_HEARING_CHANNEL=1 (default OFF): SIGN{support:+1,oppose:-1,neutral:0}, category
route, 2.0yr half-life, own-source weight 0.85 (below floor speeches' 1.0 due to witness-attribution
risk), tier weights gold 1.0 / review 0.35.

Usage: python hearing_stance_stack.py     (score hearings_cu.csv -> hearing_stance_final.csv)
"""
import csv, io, json, os, re, sys, hashlib, collections
import config_overlay as CO
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
CL = ["support", "oppose", "neutral", "unrelated"]
SRC  = os.path.join(B, 'data', 'stance', 'hearings_cu.csv')
GOLDF = os.path.join(B, 'data', 'stance', 'hearing_gold_queue.csv')
REGF = os.path.join(B, 'data/stance/cu_valence_registry.json')
OUT  = os.path.join(B, 'data', 'stance', 'hearing_stance_final.csv')

REG = CO.merged_rows('data/stance/cu_valence_registry.json','entries',('key',),data_root=B)
POLICY_KEYS = {e['key'] for e in REG if e.get('policy_force')}   # user-legislated CFPB agency axis (valence 0)


def fam_lookup(text):
    """First-matching registry family (list order = precedence, specific before generic).
    Returns (valence, key), or (None, None) when nothing matches. Mirrors the stacks' fam_lookup."""
    blob = (text or '').lower()
    for e in REG:
        if any(m in blob for m in e['match']):
            return e['valence'], e['key']
    return None, None


def compose(s, v):
    """IDENTICAL to tweet/press/speech stacks: member stance-toward-target x target CU valence -> CU alignment."""
    s = (s or '').lower()
    if s == 'unrelated': return 'unrelated'
    if s == 'neutral' or v == 0: return 'neutral'
    if s == 'support': return 'support' if v > 0 else 'oppose'
    if s == 'oppose': return 'oppose' if v > 0 else 'support'
    return 'neutral'


# --- target-scoped attack/defend cue heuristic (mirrors build_hearing_gold_queue.prelabel windows) ---
_ANCHOR_RX = re.compile(r'cfpb|consumer financial protection|interchange|swipe fee|'
                        r'credit card competition|durbin|overdraft|junk fee|rate cap')
# ATTACK-the-agency cues => member OPPOSES the (anti-CU) target => pro-CU.  DEFEND-the-agency cues =>
# member SUPPORTS the target => anti-CU.  Word boundaries keep 'gut' (attack) off 'gutting' (the
# defender's framing "stop the gutting of the CFPB").  Same cue lists as build_hearing_gold_queue.py.
_ATTACK_CUES = [r'\brein (?:it |them )?in\b', r'\bdefund', r'\bgut\b', r'\babolish', r'\boverreach',
                r'\bunaccountable\b', r'\brogue\b']
_DEFEND_CUES = [r'\bprotect', r'\bdefend', r'\bsave\b', r'\bstrengthen', r'\bexpand',
                r'\bgutting\b', r'\battack on\b']
_ATK_RX = [re.compile(p) for p in _ATTACK_CUES]
_DFD_RX = [re.compile(p) for p in _DEFEND_CUES]


def cue_stance_toward_target(text_low):
    """Windowed stance TOWARD the mentioned anti-CU / CFPB target (only text within +/-120 chars of an
    anchor is read, BUG-G discipline):
      'oppose'  -> attacks / curtails the target (rein in, defund, gut, abolish, overreach...)
      'support' -> defends / grows the target    (protect, defend, save, strengthen, expand...)
      'neutral' -> no confident directional cue near a target mention."""
    anchors = [m.start() for m in _ANCHOR_RX.finditer(text_low)]
    if not anchors:
        return 'neutral'
    W = 120
    windows = ' '.join(text_low[max(0, a - W):a + W] for a in anchors)
    windows = re.sub(r'(?:consumer )?financial protection( bureau| board| agency)?', ' cfpb ', windows)  # F2: mask the agency's own name so the \bprotect DEFEND cue does not self-fire on '...Financial PROTECTION Bureau'
    atk = any(rx.search(windows) for rx in _ATK_RX)
    dfd = any(rx.search(windows) for rx in _DFD_RX)
    if atk and not dfd:
        return 'oppose'    # opposes the target agency
    if dfd and not atk:
        return 'support'   # supports the target agency
    return 'neutral'


def _is_directional(valence, key):
    """Anti-CU / CFPB-agency families whose direction the windowed cue read can resolve â€” mirrors the
    gold queue's DIRECTIONAL set: any cfpb* family, the interchange / rate-cap / overdraft / junk-fee
    targets, or any -1-valenced target.  Pro-CU (+1) and no-signal (0, non-CFPB) families stay neutral
    (they rarely carry an anchor, and Paul's gold fills them)."""
    return ((key or '').startswith('cfpb')
            or key in ('interchange', 'rate_cap', 'overdraft', 'junk_fees')
            or valence == -1)


def score_hearing_text(text):
    """(b)+(c): compose the matched registry family with the windowed cue direction. Returns a CU stance
    in {support, oppose, neutral}. No API.  Non-directional families -> neutral (Paul's gold fills those)."""
    valence, key = fam_lookup(text)
    if key is None or not _is_directional(valence, key):
        return 'neutral'
    s = cue_stance_toward_target((text or '').lower())
    # (b) base registry compose with the family's own valence
    label = compose(s, valence)
    # RELAXED CFPB DIRECTIONALITY PIN â€” mirrors the tweet/press/speech stacks' policy_force relaxation.
    # The CFPB *agency* axis is anti-CU under Paul's fresh gold (attack/defund = support, defend =
    # oppose).  The valence-0 policy_force families (POLICY_KEYS = cfpb_institution/cfpb_general)
    # compose to neutral above; the +1 cfpb_reform family composes with the wrong sign because the
    # windowed cue reads the literal agency, not the abstract "reform" concept the API would score.
    # For ANY cfpb* family we therefore trust the agency-scoped cue direction at valence -1, keeping
    # neutral only when the cue read is itself non-directional (the anti-populist neutral guard).
    if (key or '').startswith('cfpb') and s in ('support', 'oppose'):
        label = compose(s, -1)
    return label


def row_id(bioguide, date, text):
    """Content-hash id, byte-identical to build_hearing_gold_queue.py so a gold label binds by content."""
    return 'x' + hashlib.md5(('%s|%s|%s' % (bioguide or '', date or '', text or ''))
                       .encode('utf-8', 'replace')).hexdigest()[:12]


def load_gold(path=GOLDF):
    """id -> gold stance (support/oppose/neutral) for the FILLED rows of hearing_gold_queue.csv.
    Blank gold_stance (Paul labels later) and any non-{support,oppose,neutral} value are ignored."""
    g = {}
    if not os.path.exists(path):
        return g
    with io.open(path, encoding='utf-8-sig', newline='') as fh:
        for r in csv.DictReader(fh):
            st = (r.get('gold_stance') or '').strip().lower()
            rid = (r.get('id') or '').strip()
            if rid and st in ('support', 'oppose', 'neutral'):
                g[rid] = st
    return g


def score_row(row, gold):
    """Apply the full precedence to one hearings_cu.csv row. Returns (stance, tier).
    (a) gold beats everything; (b)+(c) registry compose + heuristic otherwise (tier 'review')."""
    rid = row_id(row.get('bioguide'), row.get('date'), row.get('text'))
    g = gold.get(rid)
    if g is not None:
        return g, 'gold'
    return score_hearing_text(row.get('text') or ''), 'review'


def main():
    from stance.own_source_stance_model import run_channel
    return run_channel('hearing')

if __name__ == '__main__':
    sys.exit(main())
