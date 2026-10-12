# -*- coding: utf-8 -*-
"""Expert-panel observer items — single source of truth shared by b5_panel_build.py (embeds into the
rater HTML) and b5_panel_score.py (keys/reverse-scoring). Instrument: BFI-2-XS (Soto & John 2017,
JRP 68:69-81; free for research use) adapted to OBSERVER form ("This person is someone who...").
15 items, 3 per domain, 1-5 Likert (1=disagree strongly, 5=agree strongly). PROVISIONAL_TEXT flag:
item wording is being verified verbatim against the published instrument by the panel-design-research
workflow — finalize before any rater sees a packet (prereg locks at rating start, not at build)."""

PROVISIONAL_TEXT = False   # VERIFIED verbatim vs Soto & John 2017 (panel-design-research workflow wu6cbr5od,
                           # verdict CONFIRMED vs the published Appendix + Colby bfi2xs-form.pdf). The verify
                           # pass caught 1 error in the provisional draft: item 8 is "Has difficulty getting
                           # started on tasks." (REVERSE-keyed) — not the recalled "Is persistent..." item.
                           # STEM NOTE: the published stem is self-report ("I am someone who..."); ours is the
                           # standard informant-form adaptation to third person — disclosed in the prereg.

STEM = "This person is someone who..."
SCALE = ["Disagree strongly", "Disagree a little", "Neutral; no opinion", "Agree a little", "Agree strongly"]

# (id, domain, reverse?, text) — BFI-2-XS published item numbers in comments (Soto & John 2017).
ITEMS = [
    ("E1", "E", True,  "Tends to be quiet."),                                   # item 1  (R)
    ("E2", "E", False, "Is dominant, acts as a leader."),                       # item 6
    ("E3", "E", False, "Is full of energy."),                                   # item 11
    ("A1", "A", False, "Is compassionate, has a soft heart."),                  # item 2
    ("A2", "A", True,  "Is sometimes rude to others."),                         # item 7  (R)
    ("A3", "A", False, "Assumes the best about people."),                       # item 12
    ("C1", "C", True,  "Tends to be disorganized."),                            # item 3  (R)
    ("C2", "C", False, "Is reliable, can always be counted on."),               # item 13
    ("C3", "C", True,  "Has difficulty getting started on tasks."),             # item 8  (R) — verify-pass fix
    ("N1", "N", False, "Worries a lot."),                                       # item 4
    ("N2", "N", False, "Tends to feel depressed, blue."),                       # item 9
    ("N3", "N", True,  "Is emotionally stable, not easily upset."),             # item 14 (R)
    ("O1", "O", False, "Is fascinated by art, music, or literature."),          # item 5
    ("O2", "O", True,  "Has little interest in abstract ideas."),               # item 10 (R)
    ("O3", "O", False, "Is original, comes up with new ideas."),                # item 15
]
# The six published reverse-keyed items (1,3,7,8,10,14) = E1,C1,A2,C3,O2,N3. REVERSED is authoritative.
REVERSED = {"E1", "A2", "C1", "C3", "N3", "O2"}

# panel-domain -> instrument-trait mapping (panel rates N; instruments store ES = -N)
DOM2TRAIT = {"E": "E", "A": "A", "C": "C", "O": "O", "N": "ES"}   # N consensus is SIGN-FLIPPED to ES in scoring

# per-packet supplemental probes (not trait items)
PROBES = [
    ("REC", "Do you believe you recognize who this specific person is? If yes, type the name; if no, leave blank."),
    ("CONF", "How confident are you in your ratings of this person overall? (1=guessing, 5=very confident)"),
]

def trait_score(ratings):
    """ratings: {item_id: 1..5} -> {trait: mean score with reverse-keying, N flipped to ES}. None if incomplete."""
    by = {}
    for iid, dom, _rev, _txt in ITEMS:
        v = ratings.get(iid)
        if v is None: return None
        v = float(v)
        if iid in REVERSED: v = 6.0 - v
        by.setdefault(dom, []).append(v)
    out = {}
    for dom, vals in by.items():
        m = sum(vals) / len(vals)
        tr = DOM2TRAIT[dom]
        out[tr] = (6.0 - m) if dom == "N" else m   # ES = reversed N
    return out
