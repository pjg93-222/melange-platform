# -*- coding: utf-8 -*-
"""Three-paragraph demographic brief per member, from the demographic evidence ledger.

Reused verbatim in shape from the approach-brief lane (paths in DEMOGRAPHIC_BRIEF_SCOPE.md
section 5.1):
  * the urllib API wrapper, hardened variant -- retry-after, jitter, process-global
    cooldown -- from fec/members/revise_member_narratives_v2.py:266-350, with the
    thinking:disabled body from gen_briefs.py:399-407.
  * the key resolver from fec/members/revise_member_narratives_v2.py:353-374.
    The key is never written to a file, never printed, never put on a command line.
  * the resumable jsonl progress log: one handle for process lifetime, locked
    write+flush+fsync (gen_briefs_v2.py:1095-1117), last-write-wins resume, and the
    final artifact assembled from disk rather than memory (gen_briefs.py:612-649).
  * the AdaptiveLimiter thread pool (gen_briefs_v2.py:425-460).
  * span-merge-before-counting in every count-vs-threshold check. That is the
    documented false-rejection bug class; 30 false rejections in 371 drafts.

The system prompt and the validator are written fresh for this lane.
"""

from __future__ import annotations

import argparse
import bisect  # noqa: F401  (kept for parity with the ledger builder's conventions)
import collections
import json
import math
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"  # Paul, 2026-09-03: reverted after the Haiku pilot showed
                           # 10/12 failures driven by figure-cap/superlative/word-count/
                           # calibration (not novelty) and a ~4x worse cost per accepted
                           # brief (no prompt caching engaged for the Haiku snapshot).

# Per-MTok USD, by model. cache_write/cache_read are not independently published for
# every model; where Anthropic does not list them, the standard ~1.25x-input /
# ~0.1x-input multipliers apply (matches the published Sonnet 5 rate exactly: 2.00 *
# 1.25 = 2.50, 2.00 * 0.1 = 0.20). Bug fixed 2026-09-03: this used to be a single
# hardcoded (Sonnet-priced) dict in main(), so switching MODEL silently mispriced
# every subsequent run's cost_usd. Keyed by the bare model id; price_for() strips a
# trailing -YYYYMMDD snapshot suffix to match a dated id against its floating alias.
PRICE_TABLE = {
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}


def price_for(model):
    if model in PRICE_TABLE:
        return PRICE_TABLE[model]
    base = re.sub(r"-\d{8}$", "", model)
    if base in PRICE_TABLE:
        return PRICE_TABLE[base]
    raise SystemExit("no price entry for model %r; add one to PRICE_TABLE before "
                     "spending money against it" % model)
KEYS_JSON = r"C:\Users\pgoff\OneDrive\Desktop\API Keys\keys.json"

WORD_MIN, WORD_MAX = 200, 250      # Paul's spec. The prompt asks for 210-240, strictly
PROMPT_MIN, PROMPT_MAX = 205, 240  # inside the gate, so the model has headroom both ways.
FIGURE_CAP = 6

# Measured 2026-09-02: an unbounded seen_grams (every prior acceptance in the run)
# makes the novelty gate unsatisfiable by the time ~130 of 533 briefs, all describing
# the same eight district statistics, have been accepted -- 404/404 attempted after
# that point were rejected, novelty the dominant reason. The trigger itself (3+
# distinct shared runs, or one run >=10 tokens) is untouched and still catches a real
# lift; only the population it is compared against is now bounded to the N most
# recently accepted briefs. See window_add() / rebuild_novelty_window() below.
NOVELTY_WINDOW = 50

NOVELTY_LOCK = threading.Lock()
PROGRESS_LOCK = threading.Lock()
APILOG_LOCK = threading.Lock()
SAY_LOCK = threading.Lock()


def say(msg):
    with SAY_LOCK:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()


# ============================================================================
# 1. KEY  (revise_member_narratives_v2.py:353-374)
# ============================================================================
def read_key(path: str, index: int = 0) -> str:
    candidates = []
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        if index == 0:
            return env_key
        candidates.append(env_key)
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text:
        raise SystemExit("API key file is empty")
    if text.startswith("{"):
        document = json.loads(text)
        anthropic = document.get("anthropic")
        anthropic = anthropic if isinstance(anthropic, dict) else {}
        candidates.extend([
            document.get("ANTHROPIC_API_KEY"),
            document.get("anthropic_api_key"),
            anthropic.get("key"),
        ])
        candidates.extend(anthropic.get("keys") or [])
    else:
        candidates.append(text)
    keys = []
    for value in candidates:
        key = str(value).strip() if value else ""
        if key and key not in keys:
            keys.append(key)
    if not keys:
        raise SystemExit("could not find an Anthropic API key in the supplied file")
    if index < 0 or index >= len(keys):
        raise SystemExit("Anthropic API key index %d is unavailable; %d configured key(s) "
                         "were found" % (index, len(keys)))
    return keys[index]


# ============================================================================
# 2. SYSTEM PROMPT  (written fresh; scanned against every ban regex before use)
# ============================================================================
SYSTEM = """You write the local-economy section of a briefing book. Your reader works in
government affairs for a credit union and is about to meet the person who holds this seat.
They want to understand the place, not the politician.

WHAT YOU ARE GIVEN
Measured facts about one geography. Everything given to you was measured. Nothing outside
it is available to you. If something is not given to you, it does not exist in our
records: say nothing about it, and never infer it from a neighbouring field.

LENGTH IS THE HARDEST CONSTRAINT HERE
The whole brief lands between 200 and 245 words. Aim at 220. A brief that runs long is
wrong even when every word in it is true, and length is the one thing this job fails on.
Each paragraph is about seventy words: four sentences, five at the outside.

Each paragraph makes exactly THREE points and stops. Nine points across the brief. Choose
the nine that tell this place apart from the next one. Do not walk the list you are given.
If you are over length, delete a whole point. Never shave a little off every sentence.

OUTPUT SHAPE
Three paragraphs, one blank line between them. No headings, no labels, no bullets, no
markdown of any kind. The page prints its own headings; you write only the prose beneath.

  First paragraph   The place itself. Who lives there, what they earn, what housing costs
                    them, how they work.
  Second paragraph  Credit unions in this place.
  Third paragraph   The rest of the economy, and what this place is unusually good at.

Nothing before or after the three paragraphs.

Never write the name of the person who holds the seat. Never mention their party, their
politics, or anything they have done. Never use he, she or they to mean that person. You
are writing about a place.

HOW TO SOUND
Plain speech, the way you would describe somewhere to a colleague who has never been.
Direct statements. Say the thing; do not hedge it behind words about how something reads
or appears or comes across. No long dashes anywhere: use a comma, a full stop, or a
different sentence. Ordinary keyboard characters only.

Skip the adjectives that belong on a tourism poster, and skip the vocabulary of management
consulting. If a sentence could be about anywhere in the country, cut it.

NUMBERS
At most SIX figures in the whole brief, and four is usually better. Years do not count
toward the six. Every figure must be copied exactly as it is written below. Do not
calculate a new one, do not restate one as a fraction in words, and do not round it into
something it does not say.

Most of what you are given should never appear as a number. A quality named in plain
words costs five words; the same quality given as a figure costs twelve and says less.

HOW HARD TO PUSH A CLAIM
Where a field name ends in "vs_peers" you are handed a short phrase placing this geography
against comparable ones. That phrase is calibration for you, not wording for the reader.
At most one such phrase may reach the reader; put the others in your own words.
Where a field name ends in "against_national_norm" you are handed a multiple: 1.00 is the
national norm, and 2.00 is twice it.

WHAT YOU MAY NEVER WRITE
1  Anything about how much formal learning people there have had. No such measurement
   exists here, for anywhere, in any form.
2  Any rating that treats a place as a business prospect, and any position in a league
   table. Nothing of that kind here is trustworthy, and none of it is yours to quote.
3  A number of offices or a sum of deposits presented as something somebody went and
   totalled. Those were apportioned from wider areas. Write about share and about scale.
4  How many people belong to a credit union there, or how credit unions there have
   changed across the years. Neither of those was ever measured.
5  Anything smaller than the whole state, where you are told the facts cover the whole
   state. The right word for the geography is given to you; use that word.
6  Any figure you are told is not dependable, and nothing built on top of one.

Where you are told the working picture of the economy was measured in an earlier year,
name that year in the third paragraph and write about it in the past tense.

THE SECOND PARAGRAPH HAS TO EARN ITS PLACE
It must be about credit unions in THIS place and impossible to paste into another brief.
Work from the share of retail offices credit unions hold against banks, how that compares
with similar geographies, the institutions named for you, and how thick credit union
employment is there against the national norm. Tie it back to the first paragraph: the
people and the money described there are what a credit union in this place actually works
with.

VARY YOUR WRITING
These sit side by side on a desk. Do not open two of them the same way, do not repeat a
sentence shape, and never reuse a phrase you have used in another brief.
"""

USER_TMPL = """Write the three-paragraph brief for the {noun} described below.

WHAT THIS ONE NEEDS
{directive}

WHAT WE MEASURED
{ledger}
"""


# ============================================================================
# 3. BAN LISTS  (fresh for this lane)
# ============================================================================
BANNED_EDUCATION = (
    r"\bbachelor", r"\battainment\b", r"\bdegrees?\b", r"\bdiploma", r"\bcollege",
    r"\buniversit", r"\bhigh school\b", r"\bliterac", r"\beducated\b", r"\bschool",
    r"\bgraduat", r"\bstudents?\b", r"\bcredential", r"\bskilled workforce\b",
    r"\bwell.educated\b", r"\beducation\b(?!al services)", r"\beducational\b(?! services)",
)
BANNED_RATING = (
    r"\bpercentile\b", r"\brank(?:s|ed|ing|ings)?\b", r"\btiers?\b", r"\bscores?\b",
    r"\bindex\b", r"\bpotential (?:score|rating|rank|tier)\b", r"\bgrade[ds]?\b",
    r"\bopportunity (?:score|rating)\b", r"\bnth\b", r"\bout of \d+\b",
)
BANNED_JARGON = (
    r"\blocation quotient\b", r"\bquotient\b", r"\blq\b", r"\bcns\d\d\b", r"\b5221\d\d\b",
    r"\bnaics\b", r"\blodes\b", r"\bqcew\b", r"\bcbp\b", r"\bhmda\b", r"\bacs\b",
    r"\bshift.share\b", r"\bcompetitive shift\b", r"\bgrowth quotient\b", r"\bmakeup\b",
    r"\bkusd\b", r"\ballocat", r"\bequal.split\b", r"\bgeocod", r"\bledger\b",
    r"\bevidence packet\b", r"\bdataset\b", r"\bdata set\b", r"\bartifact\b",
    r"\bthe packet\b", r"\bstanding\b", r"\bthe data\b", r"\bour records\b",
)
BANNED_HEDGE = (
    r"\breads as\b", r"\bappears? to\b", r"\bseems? to\b", r"\bwould appear\b",
    r"\bsuggests?\b", r"\bindicates?\b", r"\bit is worth noting\b", r"\bnotably\b",
    r"\bindeed\b", r"\barguably\b", r"\bone might\b", r"\bcan be seen as\b",
    r"\bserves as\b", r"\bplays? a (?:key |major |significant |central )?role\b",
    r"\bwhen it comes to\b", r"\bin terms of\b", r"\bat the end of the day\b",
    r"\bkey takeaway\b", r"\bpaints? a picture\b", r"\btells? a story\b",
    r"\bspeaks to\b", r"\bthat said\b", r"\bby and large\b", r"\brelatively speaking\b",
)
BANNED_SLOP = (
    r"\bvibrant\b", r"\bbustling\b", r"\bnestled\b", r"\bboasts?\b", r"\btapestry\b",
    r"\blandscape\b", r"\brobust\b", r"\bleverage\b", r"\becosystem\b", r"\bthriving\b",
    r"\bbackbone\b", r"\blifeblood\b", r"\bhub of\b", r"\bpoised\b",
    r"\bwell.positioned\b", r"\bunique blend\b", r"\brich history\b", r"\bstoried\b",
    r"\bpicturesque\b", r"\bheart of the\b", r"\bfabric of\b", r"\bdriving force\b",
)
BANNED_CENSUS = (
    r"\bthere are \d", r"\bcounted\b", r"\bexactly \d", r"\b\d[\d,]* branches\b",
    r"\bbranch count\b", r"\bin deposits\b", r"\bdeposits? of \$", r"\bholds \$",
    r"\bcensus of\b", r"\btotal deposits\b", r"\bdeposit base\b",
)
BANNED_BURDEN = (
    r"\bcost.burdened?\b", r"\bhouseholds spend\b", r"\bshare of households\b",
    r"\bof households (?:are|spend|pay|is)\b", r"\brent.burdened?\b",
)
BANNED_MEMBERSHIP = (
    r"\bmembership\b", r"\bpenetration\b", r"\bfield of membership\b",
    r"\bcredit union members\b", r"\bmember growth\b", r"\bmembers? per\b",
    # Pilot defect 2 (Begich, "a member base scattered across enormous distances"):
    # no membership count exists for any credit union anywhere in this ledger, so any
    # phrase that implies one -- a base, a headcount, a population of members, who is
    # served -- is banned exactly like "membership" itself. "member" alone is not
    # banned: the system prompt forbids ever naming or referring to the seat holder
    # ("the member", already covered by BANNED_MEMBER_REF below), so nothing in a
    # compliant brief ever needs "member" to mean a person in Congress, and the only
    # legitimate remaining sense -- a credit union member -- is exactly what this
    # ledger has no data on.
    r"\bmember base\b", r"\bmembers served\b", r"\bmember count\b", r"\btheir members\b",
    r"\bmember population\b", r"\bmembers? across\b", r"\bhow many members\b",
    r"\bnumber of members\b",
)
BANNED_SINGLE_BANK = (
    # Pilot defect 1 (Owens, "against a single named bank competitor, Synchrony
    # Bank"): the ledger fix (largest_bank_by_deposits_name, bank_branch_count, and
    # the caveat that spells out why this can never be "the only bank") is the root
    # fix; this is the backstop for the exact framing that produced the defect, span
    # bounded around "competitor" the way the pilot report specified so an unrelated,
    # legitimate use of "one bank" elsewhere is not caught by accident.
    r"\b(?:a single(?:\s+named)?\s+bank|the only bank|sole bank|one bank)\b"
    r"(?:\W+\w+){0,6}\W+competitor\b",
    r"\bcompetitor\b(?:\W+\w+){0,6}\W+"
    r"(?:a single(?:\s+named)?\s+bank|the only bank|sole bank|one bank)\b",
)
BANNED_DEPOSIT_FRAMING = (
    # Pilot defect 3, SOFT (Harris, "relative to the people and deposits they serve";
    # Magaziner, "compete hard for the deposits of a well-paid population"): deposits
    # are an equal-split estimate (scale_caveat above), so a per-branch or
    # per-population framing implies a precision, and a market of depositors to "win",
    # that the figure cannot support.
    r"\bdeposits? per branch\b", r"\bdeposits? (?:they|it) serves?\b",
    r"\bpeople and deposits\b",
    r"\bcompete\w*\b.{0,30}\bfor (?:the )?deposits\b",
    r"\bdeposits? of (?:a|the|this)\b.{0,40}\bpopulation\b",
)
BANNED_CU_HISTORY = (
    r"\bcredit union growth\b", r"\bgrowth (?:in|of) credit unions\b",
    r"\bcredit unions? (?:have|has) (?:grown|expanded|gained|risen)\b",
    r"\bcredit unions? (?:are|is) (?:growing|expanding)\b",
    r"\bexpanding credit union\b", r"\bcredit unions? grew\b",
    r"\bover the (?:past|last) \w+ years?,? credit union\b",
)
BANNED_EMPLOYER = (
    r"\blargest employers?\b", r"\bbiggest employers?\b", r"\btop employers?\b",
    r"\bmajor employers?\b",
)
BANNED_OUTSIDE = (
    r"\b(?:democrat|republican|gop|congress|senate|house of representatives)\b",
    r"\belections?\b", r"\bvoters?\b", r"\bcampaign\b", r"\bconstituents?\b",
    r"\blegislation\b", r"\bcommittees?\b", r"\bcapitol\b",
    r"\b(?:19|20)\d0s\b", r"\bhistorically\b",
)
BANNED_FRACTION_WORDS = (
    r"\b(?:one|two|three|four|five|six|seven|eight|nine) in "
    r"(?:two|three|four|five|six|seven|eight|nine|ten)\b",
    r"\btwo[- ]thirds\b", r"\bthree[- ]quarters\b", r"\bone[- ]third\b",
    r"\ba third of\b", r"\ba quarter of\b", r"\bhalf (?:of|the)\b",
    r"\btwice as many\b", r"\bdouble the\b",
)
BANNED_MEMBER_REF = (
    r"\bthe member\b", r"\bthis lawmaker\b", r"\bthe representative\b",
    r"\bthe senator\b", r"\bthis office ?holder\b",
    r"\b(?:he|she|they) represents?\b", r"\bwhoever holds\b",
)

ALL_BANS = {
    "education": BANNED_EDUCATION,
    "rating": BANNED_RATING,
    "jargon": BANNED_JARGON,
    "hedging": BANNED_HEDGE,
    "brochure language": BANNED_SLOP,
    "counted-not-apportioned": BANNED_CENSUS,
    "cost burden": BANNED_BURDEN,
    "credit union membership": BANNED_MEMBERSHIP,
    "single-bank competitor framing": BANNED_SINGLE_BANK,
    "deposit-per-branch framing": BANNED_DEPOSIT_FRAMING,
    "credit union history": BANNED_CU_HISTORY,
    "named employers": BANNED_EMPLOYER,
    "outside knowledge": BANNED_OUTSIDE,
    "arithmetic in words": BANNED_FRACTION_WORDS,
    "the member as a person": BANNED_MEMBER_REF,
}

BAND_PHRASES = ("among the highest", "among the lowest", "well above most",
                "above most", "well below most", "below most", "middling")
LQ_PHRASES = ("highly specialized", "highly specialised", "slightly specialized",
              "slightly specialised", "not specialized", "not specialised",
              "specialized", "specialised")
# An explicit stem list, not \w+est, because "interest in" and "invest in" would
# otherwise be scored as superlative claims and eat the budget.
SUPERLATIVE_RE = (
    r"\bamong the (?:highest|lowest|largest|smallest|most|least|biggest|densest|"
    r"poorest|richest|best|worst|thickest|heaviest|youngest|oldest)\b",
    r"\b(?:high|low|larg|small|bigg|dens|poor|rich|strong|thick|heavi|wealthi|"
    r"young|old|great|cheap|expensiv)est\b",
    r"\bmore than (?:almost )?anywhere\b",
    r"\bnowhere else\b",
    r"\bthe most \w+ (?:in|of) the (?:country|nation)\b",
    r"\b(?:lower|higher|greater|larger|smaller|denser|thicker) than (?:almost )?any\w*\b",
)
# Naming the leading institution is not a superlative claim about the geography, so
# those phrases are masked out before the budget is counted.
SUPERLATIVE_MASK = r"\b(?:largest|biggest|leading)\s+(?:credit union|bank)s?\b"
STOCK_PATTERNS = (
    r"\bthis is a \w+ (?:place|area|community)\b",
    r"\bwhat (?:this|that) means for\b",
    r"\bthe numbers tell\b",
    r"\bfor credit unions,? (?:this|that) means\b",
    r"\bin short,?\b",
    r"\bput simply,?\b",
    r"\ball of (?:this|which) (?:means|adds)\b",
    r"\bat first glance\b",
)


# ============================================================================
# 4. FIGURE GROUNDING
# ============================================================================
# Grouped thousands or a plain number. NOT r"\d[\d,]*(?:\.\d+)?": that form swallows a
# trailing sentence comma, so "as measured in 2023," produced the token "2023," which is
# five characters long and therefore failed the four-digit year test. Measured cost:
# three wasted rounds on one member, rejected for a figure it never used.
NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
ORDINAL_RE = re.compile(r"(?:st|nd|rd|th)\b", re.I)
SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def numeric_leaves(node, acc):
    if isinstance(node, dict):
        for value in node.values():
            numeric_leaves(value, acc)
    elif isinstance(node, list):
        for value in node:
            numeric_leaves(value, acc)
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        value = finite(node)
        if value is not None:
            acc.append(value)


def renderings(value):
    """The set of writings of one ledger number a brief is allowed to use.
    Deliberately narrow: exact, two to four significant figures, the packet's own
    decimal places, thousands/millions shorthand, and a fraction written as a
    percentage. No one-significant-figure rounding: that would let '100,000' stand
    in for 109,469."""
    out = set()

    def add(x):
        x = finite(x)
        if x is not None:
            out.add(round(x, 6))

    add(value)
    for places in (0, 1, 2):
        add(round(value, places))
    magnitude = abs(value)
    if magnitude > 0:
        exponent = math.floor(math.log10(magnitude))
        for sig in (2, 3, 4):
            add(round(value, -(exponent - sig + 1)))
    if magnitude >= 1000:
        for places in (0, 1):
            add(round(value / 1000.0, places))
    if magnitude >= 100000:
        for places in (1, 2):
            add(round(value / 1e6, places))
    if 0 < magnitude <= 1.5:
        for places in (0, 1):
            add(round(value * 100.0, places))
    return out


def allowed_figures(record):
    values = []
    numeric_leaves(record, values)
    allowed = {}
    for value in values:
        for rendering in renderings(value):
            allowed.setdefault(rendering, value)
    return allowed


def figure_name_masks(record):
    """Numeric proper names that are evidence labels, not statistical figures."""
    credit_unions = (record or {}).get("credit_unions") or {}
    names = list(credit_unions.get("largest_credit_unions_by_name") or [])
    names.append(credit_unions.get("largest_bank_by_deposits_name"))
    return [str(name) for name in names if name and re.search(r"\d", str(name))]


def figures_in(text, name_masks=()):
    """Return [(token, effective_value, raw_value, is_year)] for every digit-bearing
    figure. Ordinals are dropped: the only ordinal in this evidence is the seat number
    inside the geography's own label ("the 1st congressional seat of Louisiana"), which
    is handed to the writer verbatim and is not a statistic."""
    scan = text
    for phrase in sorted(name_masks, key=len, reverse=True):
        scan = re.sub(re.escape(phrase),
                      lambda m: re.sub(r"\d", "#", m.group(0)),
                      scan, flags=re.I)
    out = []
    for match in NUM_RE.finditer(scan):
        token = match.group(0)
        raw = finite(token.replace(",", ""))
        if raw is None:
            continue
        tail = scan[match.end():match.end() + 12]
        if ORDINAL_RE.match(tail):
            continue
        tail = tail.lower()
        scale = 1.0
        for word, factor in SCALE_WORDS.items():
            if re.match(r"\s*" + word, tail):
                scale = factor
                break
        is_year = (scale == 1.0 and "," not in token and "." not in token
                   and len(token) == 4 and 1900 <= raw <= 2100)
        out.append((token, raw * scale, raw, is_year))
    return out


# ============================================================================
# 5. VALIDATOR  (every count is span-merged before it is compared to a threshold)
# ============================================================================
def merged_spans(text, patterns, literal=False):
    spans = []
    for pattern in patterns:
        if literal:
            start = 0
            while True:
                index = text.find(pattern, start)
                if index < 0:
                    break
                spans.append((index, index + len(pattern)))
                start = index + 1
        else:
            for match in re.finditer(pattern, text):
                spans.append(match.span())
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def superlative_budget(record):
    """How many fields actually sit in an extreme band. Structural, not semantic."""
    total = 0

    def walk(node):
        nonlocal total
        if isinstance(node, dict):
            if isinstance(node.get("label"), str) and node["label"].startswith("among the"):
                total += 1
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(record)
    return total


def tokens_of(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def sentence_token_runs(text, masks=()):
    """Token runs for n-gramming, bounded by sentence ends and by phrases every brief
    is entitled to write identically.

    Two measured false positives this fixes:
      * a gram spanning a full stop ("... rival in Synchrony Bank. Credit union
        employment runs ...") is not reuse, it is two unrelated sentences;
      * a sector's own name ("health care and social assistance") and an institution's
        own name come from the evidence and cannot be paraphrased away."""
    low = text.lower()
    for phrase in sorted(masks, key=len, reverse=True):
        if phrase and len(phrase) > 3:
            low = low.replace(phrase, " . ")
    runs = []
    for sentence in re.split(r"[.!?;:\n]+", low):
        tokens = re.findall(r"[a-z0-9]+", sentence)
        if tokens:
            runs.append(tokens)
    return runs


def brief_grams(text, masks=()):
    grams = set()
    for tokens in sentence_token_runs(text, masks):
        for i in range(len(tokens) - 4):
            grams.add(tuple(tokens[i:i + 5]))
    return grams


def window_add(window, counts, grams):
    """Feed one accepted brief's grams into a bounded, acceptance-order window.

    window is a deque(maxlen=NOVELTY_WINDOW); counts is a Counter mapping gram ->
    how many briefs currently in the window contain it, kept in sync so membership
    in the window is a plain `gram in counts` test (a Counter is a dict subclass).
    The oldest brief is evicted -- and its grams decremented out of counts, deleting
    any that hit zero -- only once the window is already full, so the window always
    holds exactly the last min(N, NOVELTY_WINDOW) accepted briefs, in order."""
    if window.maxlen is not None and len(window) == window.maxlen:
        evicted = window[0]
        for gram in evicted:
            counts[gram] -= 1
            if counts[gram] <= 0:
                del counts[gram]
    window.append(grams)
    for gram in grams:
        counts[gram] += 1


def novelty_defects(text, seen_grams, masks=()):
    """Five distinct reused runs, OR one reused run of ten tokens or more.

    Span-merged before counting: a nine-token lift is three overlapping five-grams, and
    a plain count would clear a threshold that should have caught it.

    Raised 3 -> 5 on 2026-09-03 (Paul): measured at 200/533 accepted with the 50-brief
    window, novelty was 57% of failures and cost/accepted-brief was 4x the baseline. 533
    briefs describing the same eight measured quantities will keep sharing ordinary
    collocations ("with a median age of") even against a bounded window; a 3-run
    threshold sits below that content domain's noise floor. The single-run-of-ten
    trigger -- what actually distinguishes a lift from coincidence -- is unchanged and
    not negotiable; only the distinct-run count that fires without it moved."""
    if not seen_grams:
        return []
    merged_all, longest, shown = [], 0, []
    for tokens in sentence_token_runs(text, masks):
        hits = [(i, i + 5) for i in range(len(tokens) - 4)
                if tuple(tokens[i:i + 5]) in seen_grams]
        merged = []
        for a, b in hits:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append([a, b])
        for a, b in merged:
            merged_all.append((a, b))
            longest = max(longest, b - a)
            shown.append(" ".join(tokens[a:b]))
    if len(merged_all) >= 5 or longest >= 10:
        return ["Reuses wording from another brief in this run (%d run(s), longest %d "
                "words: %s). These are read side by side. Say it differently here."
                % (len(merged_all), longest,
                   "; ".join('"%s"' % s for s in shown[:3]))]
    return []


def validate(text, record, budget, allowed, seen_grams, want_year=None,
             forbid_word=None, require_word=None, member_name=None, masks=()):
    bad = []
    low = text.lower()

    # -- shape
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(paragraphs) != 3:
        bad.append("Wrote %d paragraphs. It must be exactly three, separated by a blank "
                   "line." % len(paragraphs))
    if re.search(r"(\*\*|^#|^\s*[-*]\s)", text, re.M):
        bad.append("Contains markdown. Write plain prose only: no bold, no headings, "
                   "no bullets.")
    words = re.findall(r"[A-Za-z0-9$%,.'-]+", text)
    count = len(words)
    if count < WORD_MIN or count > WORD_MAX:
        bad.append("Runs %d words. It must land between %d and %d words in total, and 220 "
                   "is the target. Get there by deleting whole points, not by trimming "
                   "every sentence." % (count, WORD_MIN, WORD_MAX))
    if re.search(r"[\u2012\u2013\u2014\u2015]", text):
        bad.append("Uses a long dash. Rewrite those sentences with a comma or a full stop.")
    non_ascii = sorted({ch for ch in text if ord(ch) > 127})
    if non_ascii:
        bad.append("Uses characters outside plain ASCII (%s). Use ordinary keyboard "
                   "characters only." % ", ".join(repr(c) for c in non_ascii[:6]))

    # -- ban lists
    for label, patterns in ALL_BANS.items():
        for pattern in patterns:
            match = re.search(pattern, low)
            if match:
                bad.append('Breaks the rule on %s ("%s"). Remove that claim or say it '
                           "another way." % (label, match.group(0)))
                break

    # -- calibration phrases are guidance, not vocabulary (span-merged)
    hits = merged_spans(low, BAND_PHRASES, literal=True)
    if len(hits) > 1:
        bad.append("Repeats the calibration phrasing %d times (%s). At most one may reach "
                   "the reader; put the others in ordinary English."
                   % (len(hits), ", ".join('"%s"' % low[a:b] for a, b in hits)))
    hits = merged_spans(low, LQ_PHRASES, literal=True)
    if len(hits) > 2:
        bad.append("Uses the specialisation wording %d times. Twice is the limit; describe "
                   "the rest plainly." % len(hits))

    # -- superlative budget against extreme bands actually present (span-merged)
    masked = re.sub(SUPERLATIVE_MASK, lambda m: "#" * len(m.group(0)), low)
    sup = merged_spans(masked, SUPERLATIVE_RE)
    if len(sup) > budget:
        bad.append("Makes %d superlative claims but only %d measure(s) here sit at an "
                   "extreme against comparable places (%s). Scale the others back."
                   % (len(sup), budget, ", ".join('"%s"' % low[a:b] for a, b in sup)))

    for pattern in STOCK_PATTERNS:
        match = re.search(pattern, low)
        if match:
            bad.append('Uses the worn construction "%s". Find another way in.'
                       % match.group(0))
            break

    # -- figures
    found = figures_in(text, figure_name_masks(record))
    counted = [f for f in found if not f[3]]
    if len(counted) > FIGURE_CAP:
        bad.append("Uses %d figures. At most %d are allowed, years excluded. Drop the "
                   "ones that carry least here." % (len(counted), FIGURE_CAP))
    for token, effective, raw, _is_year in found:
        if round(effective, 6) in allowed or round(raw, 6) in allowed:
            continue
        bad.append('The figure "%s" is not in this member\'s packet. Every number must be '
                   "copied from it. Remove that figure or replace it with one that is "
                   "there." % token)
        break

    # -- thousands separator (added 2026-09-03, Paul): mechanical formatting, not a new
    # content restriction. Years are exempt (is_year is already false-excluded from
    # `counted` above but figures_in() still returns them here, so check the flag).
    unformatted = [token for token, _eff, _raw, is_year in found
                   if not is_year and "," not in token and len(token.split(".")[0]) >= 4]
    if unformatted:
        bad.append("Writes %s without a thousands separator. Every figure of four or more "
                   'digits needs commas ("273,500", not "273500"). Rewrite it with commas.'
                   % ", ".join('"%s"' % t for t in unformatted))

    # -- per-member gates
    for token in re.findall(r"[A-Za-z]{3,}", member_name or ""):
        if token.lower() in ("the", "van", "von", "del", "jr", "sr", "mac"):
            continue
        # Match the complete name token.  The former five-character prefix check
        # made ordinary district prose impossible for members such as Cory Booker
        # ("booked" deposits), Lauren Underwood ("under" the national norm), and
        # anyone named Mark ("market").
        if re.search(r"\b%s\b" % re.escape(token.lower()), low):
            bad.append('Names the person who holds the seat ("%s"). Write about the '
                       "place only." % token)
            break
    if forbid_word and re.search(r"\b%ss?\b" % forbid_word, low):
        bad.append('Uses the word "%s". The evidence for this member covers the whole '
                   "state; write about the state." % forbid_word)
    if require_word and not re.search(r"\b%s\b" % require_word, low):
        bad.append('Never says "%s". Name the geography this evidence actually covers.'
                   % require_word)
    if want_year and str(want_year) not in text:
        bad.append("The working picture of this economy was measured in %s, not now. Name "
                   "%s in the third paragraph and write about it in the past tense."
                   % (want_year, want_year))

    if not bad:
        bad.extend(novelty_defects(text, seen_grams, masks))
    return bad


def scan_text(text):
    """Every ban regex, run against text we author. The approach lane shipped a prompt
    that seeded three phrases its own validator then banned."""
    hits = []
    low = text.lower()
    for label, patterns in ALL_BANS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, low):
                hits.append((label, pattern, match.group(0)))
    for phrase in BAND_PHRASES:
        if phrase in low:
            hits.append(("calibration phrase", phrase, phrase))
    for pattern in SUPERLATIVE_RE + STOCK_PATTERNS:
        match = re.search(pattern, low)
        if match:
            hits.append(("superlative/stock", pattern, match.group(0)))
    return hits


def n_ban_patterns():
    return (sum(len(v) for v in ALL_BANS.values()) + len(BAND_PHRASES)
            + len(SUPERLATIVE_RE) + len(STOCK_PATTERNS))


# ============================================================================
# 6. TRANSPORT
# ============================================================================
class AdaptiveLimiter:
    def __init__(self, permits, lo=2, hi=12, adaptive=True):
        self.permits = max(1, permits)
        self.lo = lo
        self.hi = max(hi, self.permits)
        self.adaptive = adaptive
        self.in_flight = 0
        self.last_limit_hit = time.time()
        self.cond = threading.Condition()

    def acquire(self):
        with self.cond:
            while self.in_flight >= self.permits:
                self.cond.wait(timeout=1.0)
            self.in_flight += 1

    def release(self):
        with self.cond:
            self.in_flight -= 1
            if (self.adaptive and self.permits < self.hi
                    and time.time() - self.last_limit_hit > 60):
                self.permits += 1
                self.last_limit_hit = time.time()
                say("    [limiter] no rate limit for 60s, permits -> %d" % self.permits)
            self.cond.notify_all()

    def on_rate_limit(self):
        with self.cond:
            self.last_limit_hit = time.time()
            if self.adaptive and self.permits > self.lo:
                self.permits = max(self.lo, self.permits // 2)
                say("    [limiter] rate limited, permits -> %d" % self.permits)


class ApiClient:
    def __init__(self, key, model, api_log, limiter):
        self.key = key
        self.model = model
        self.api_log = api_log
        self.limiter = limiter
        self.cooldown_lock = threading.Lock()
        self.cooldown_until = 0.0
        self.stats = collections.Counter()
        self.stats_lock = threading.Lock()

    def _wait(self):
        while True:
            with self.cooldown_lock:
                delay = self.cooldown_until - time.time()
            if delay <= 0:
                return
            time.sleep(min(delay, 5.0))

    def _cooldown(self, seconds):
        with self.cooldown_lock:
            self.cooldown_until = max(self.cooldown_until, time.time() + seconds)

    def call(self, messages, member, round_number, max_tokens=1200):
        # thinking must be explicitly disabled. Left adaptive, claude-sonnet-5 spends the
        # whole max_tokens budget inside a thinking block and returns HTTP 200 with
        # stop_reason max_tokens and zero text (gen_briefs.py:387-394).
        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 1.0,
            "system": [{"type": "text", "text": SYSTEM,
                        "cache_control": {"type": "ephemeral"}}],
            "thinking": {"type": "disabled"},
            "messages": messages,
        }).encode("utf-8")
        delay = 2.0
        last_error = None
        for _attempt in range(8):
            self._wait()
            request = urllib.request.Request(API_URL, data=payload, method="POST")
            request.add_header("content-type", "application/json")
            request.add_header("x-api-key", self.key)
            request.add_header("anthropic-version", "2023-06-01")
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    document = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:400]
                last_error = "HTTP %s: %s" % (error.code, detail)
                if error.code not in (429, 500, 502, 503, 529):
                    raise RuntimeError(last_error)
                header = error.headers.get("retry-after") if error.headers else None
                try:
                    wait = float(header) if header else delay
                except ValueError:
                    wait = delay
                wait += random.uniform(0, min(5.0, wait))
                if error.code in (429, 529):
                    self._cooldown(wait)
                    self.limiter.on_rate_limit()
                with self.stats_lock:
                    self.stats["retries"] += 1
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            except Exception as error:                                   # noqa: BLE001
                last_error = repr(error)
                with self.stats_lock:
                    self.stats["retries"] += 1
                time.sleep(delay + random.uniform(0, 2.0))
                delay = min(delay * 2, 60.0)
                continue

            content = document.get("content") or []
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            usage = document.get("usage") or {}
            with self.stats_lock:
                self.stats["calls"] += 1
                for field in ("input_tokens", "output_tokens",
                              "cache_creation_input_tokens", "cache_read_input_tokens"):
                    self.stats[field] += usage.get(field) or 0
            entry = {"member": member, "round": round_number, "model": self.model,
                     "usage": usage, "stop_reason": document.get("stop_reason"),
                     "block_types": [b.get("type") for b in content]}
            with APILOG_LOCK:
                with open(self.api_log, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if not text.strip():
                raise RuntimeError(
                    "response for %r round %r carried no text block (stop_reason=%r, "
                    "blocks=%r, usage=%r). Silent empty output is not allowed to reach "
                    "the validator." % (member, round_number, document.get("stop_reason"),
                                        entry["block_types"], usage))
            return text.strip()
        raise RuntimeError("API failed after retries: %s" % last_error)


# ============================================================================
# 7. PROGRESS
# ============================================================================
class ProgressWriter:
    def __init__(self, path):
        self.fh = open(path, "a", encoding="utf-8")

    def append(self, record):
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with PROGRESS_LOCK:
            self.fh.write(line)
            self.fh.flush()
            os.fsync(self.fh.fileno())

    def close(self):
        with PROGRESS_LOCK:
            try:
                self.fh.close()
            except Exception:                                            # noqa: BLE001
                pass


def iter_progress(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def artifact_records(path):
    """Yield accepted records from a previously published, slim artifact.

    Published artifacts intentionally omit private API attempts and the resumable
    JSONL log.  Reconstructing ``text`` here lets a clean checkout resume from the
    accepted public records without resubmitting them or resetting the novelty
    window.
    """
    if not path:
        return
    with open(path, encoding="utf-8") as fh:
        document = json.load(fh)
    briefs = document.get("briefs")
    if not isinstance(briefs, dict):
        raise SystemExit("resume artifact has no briefs object: %s" % path)
    for name, published in briefs.items():
        if not isinstance(published, dict) or not published.get("ok"):
            continue
        paragraphs = [published.get("p%d" % i) for i in range(1, 4)]
        if not all(isinstance(p, str) and p.strip() for p in paragraphs):
            raise SystemExit("resume artifact has an incomplete brief for %r" % name)
        record = dict(published)
        record.update({
            "name": name,
            "ok": True,
            "text": "\n\n".join(p.strip() for p in paragraphs),
            "rounds": 0,
            "resumed_from_artifact": True,
        })
        yield record


def rebuild_novelty_window(progress_path, masks, maxlen=NOVELTY_WINDOW,
                           seed_records=()):
    """Resume support for the windowed novelty gate.

    The progress log has exactly one line per member per attempted run, appended in
    completion order; a member that failed once and was accepted on a later run
    contributes only its (later, in-order) ok=True line, since a member already
    marked ok is never resubmitted. Replaying every ok=True line through
    window_add(), in file order, therefore reconstructs precisely the state a
    single continuous run would have reached: the last `maxlen` accepted briefs, in
    the order they were accepted. A restart is not allowed to silently compare
    against an empty window."""
    window = collections.deque(maxlen=maxlen)
    counts = collections.Counter()
    for record in seed_records:
        if record.get("ok"):
            window_add(window, counts, brief_grams(record.get("text") or "", masks))
    for record in iter_progress(progress_path):
        if record.get("ok"):
            window_add(window, counts, brief_grams(record.get("text") or "", masks))
    return window, counts


# ============================================================================
# 8. PER-MEMBER DIRECTIVE
# ============================================================================
SEAT_ANGLE = {
    "first of two by name":
        "Open on the people and the money: who lives there and what they earn.",
    "second of two by name":
        "Open on the shape of the working economy: where the jobs sit and how the "
        "place is settled.",
}

# Pilot defect 5 (template repetition, Owens 3 reused runs / Magaziner 4): a per-
# member opening angle for p1 and p2, drawn mechanically from that member's own
# evidence so different places open differently, instead of every brief walking
# the same four-move shape (population -> income -> housing; then share -> names ->
# jobs-vs-offices -> tie-back). This is a structural nudge, not a rewrite of the
# prompt's voice: it tells the model WHICH fact to lead with, never how to phrase it.
# Two senators sharing one state's evidence (Moran/Marshall) get identical inputs to
# this function by construction, so p1 there is left to the existing SEAT_ANGLE
# split above (by alphabetical slot) and p2 gets its own slot-based split below;
# whether the model still produces genuinely distinct prose from identical facts and
# an identical angle is exactly the open question the Kansas-pair run measures, not
# something this function can settle by itself.
EXTREMITY = {
    "among the highest": 3, "among the lowest": 3,
    "well above most": 2, "well below most": 2,
    "above most": 1, "below most": 1,
    "middling": 0,
}
P1_METRIC_PRIORITY = (
    ("density_vs_peers", "how tightly or thinly the population is settled"),
    ("median_household_income_vs_peers", "household income"),
    ("housing_cost_vs_peers", "housing cost measured against income"),
    ("poverty_vs_peers", "the poverty rate"),
    ("unemployment_vs_peers", "the unemployment rate"),
    ("homeownership_vs_peers", "the homeownership share"),
    ("median_home_value_vs_peers", "home values"),
)
P2_SEAT_ANGLE = {
    "first of two by name":
        "For the second paragraph, open on the credit unions named for you, before "
        "quoting the office-share number.",
    "second of two by name":
        "For the second paragraph, open on the office-share number itself, before "
        "naming any credit union.",
}


def p1_angle(record):
    """Pick the one vs_peers band that stands out most from 'middling' and hand the
    model a specific fact to lead the first paragraph with. None if nothing stands
    out at all, in which case the model chooses its own opening as before."""
    community = record.get("community") or {}
    best_key, best_score, best_desc = None, -1, None
    for key, desc in P1_METRIC_PRIORITY:
        label = (community.get(key) or {}).get("label")
        score = EXTREMITY.get(label, -1)
        if score > best_score:
            best_key, best_score, best_desc = key, score, desc
    if best_score <= 0 or best_desc is None:
        return None
    return ("Open the first paragraph on %s. Across everything measured here, that is "
            "the fact that sets this place apart most from comparable geographies."
            % best_desc)


def p2_angle(record):
    """Same idea for the second paragraph: lead on the office-share number when that
    share is itself the standout fact, lead on the jobs-vs-offices relationship when
    THAT is what stands out, and otherwise lead by naming the credit unions before
    quoting any number. Senate members use P2_SEAT_ANGLE instead (see note above)."""
    cu = record.get("credit_unions") or {}
    share_label = (cu.get("credit_union_office_share_vs_peers") or {}).get("label")
    share_score = EXTREMITY.get(share_label, -1)
    relationship = cu.get("credit_union_jobs_vs_offices_relationship")
    if share_score >= 2:
        return ("Open the second paragraph on the office-share number itself: the "
                "share credit unions hold here runs %s, more than any other single "
                "fact about credit unions in this place. Name the institutions after "
                "that, not before it." % share_label)
    if relationship in ("jobs_outpace_offices", "offices_outpace_jobs"):
        return ("Open the second paragraph on how credit union employment compares "
                "with credit union office presence here, since the two diverge "
                "enough to be worth leading with. Name the institutions and quote "
                "the office-share number afterward, not first.")
    return ("Open the second paragraph by naming the credit unions given to you, "
            "before quoting the office-share number.")


def directive(record):
    noun = record["geography"]["noun"]
    con = record["constraints"]
    lines = []
    if noun == "state":
        sib = record["geography"].get("shared_with_other_senator")
        slot = record["geography"].get("seat_slot") or ""
        lines.append("These facts cover the whole state of %s. Both of the state's two "
                     "seats are handed exactly the same facts, so this brief must not "
                     "read like the one written for the seat held by %s. Call the "
                     "geography the state, and never use the smaller word for a piece "
                     "of a state."
                     % (record["geography"]["state_name"], sib or "the other holder"))
        if slot in SEAT_ANGLE:
            lines.append(SEAT_ANGLE[slot] + " Hold that angle through the whole brief.")
        if slot in P2_SEAT_ANGLE:
            lines.append(P2_SEAT_ANGLE[slot])
    else:
        lines.append("This is one congressional seat: %s. The word for this geography is "
                     "district." % record["geography"]["label"])
        if con.get("boundaries_redrawn_since_118th"):
            lines.append("Its boundaries changed at the last redrawing, so the ten-year "
                         "comparison spans two different maps. Do not describe a change "
                         "over those years as if one fixed place changed.")
        p1 = p1_angle(record)
        if p1:
            lines.append(p1)
        p2 = p2_angle(record)
        if p2:
            lines.append(p2)
    if con.get("employment_levels_unreliable"):
        gap = con.get("employment_shortfall") or {}
        lines.append("The workplace employment base for this state runs about %s per cent "
                     "below the independent count, so no total number of jobs is given "
                     "to you and none may be stated. Write about the mix of work and how "
                     "it compares nationally, never about how many jobs there are."
                     % gap.get("shortfall_pct"))
    if con.get("industry_vintage_is_current") is False:
        year = con.get("industry_vintage_year")
        lines.append("The working picture of this economy was measured in %s. Name %s in "
                     "the third paragraph and write about that year in the past tense. "
                     "Do not write as though it describes the present." % (year, year))
    lines.append("Housing ratios compare one published middle value against another. They "
                 "do not measure what any individual pays, so do not turn them into a "
                 "proportion of people.")
    lines.append("Credit union office totals and deposit sums were apportioned from wider "
                 "areas, so only their share and their scale are yours to write about.")
    cu = record.get("credit_unions") or {}
    if cu.get("credit_union_jobs_vs_offices_relationship") == "about_equal":
        lines.append("Credit union employment and credit union office presence sit at "
                     "effectively the same level against the national norm here. Do not "
                     "describe one as running thicker, deeper, or leaner than the other; "
                     "if you mention both, say they track each other closely, or write "
                     "about only one of them.")
    return "\n".join("- " + line for line in lines)


def trim(node):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            value = trim(value)
            if value not in (None, {}, [], ""):
                out[key] = value
        return out
    if isinstance(node, list):
        return [trim(v) for v in node]
    return node


# Never sent to the model: percentile fractions and margins would become quotable
# figures, and the name/chamber/party would let it write about the person.
AUDIT_KEYS = ("pct", "margin_of_error_90pct", "name", "chamber", "party")


def strip_audit(node):
    if isinstance(node, dict):
        return {k: strip_audit(v) for k, v in node.items() if k not in AUDIT_KEYS}
    if isinstance(node, list):
        return [strip_audit(v) for v in node]
    return node


# ============================================================================
# 9. GENERATE
# ============================================================================
def build_user(record):
    sent = trim(strip_audit(record))
    return sent, USER_TMPL.format(
        noun=sent["geography"]["noun"], directive=directive(sent),
        ledger=json.dumps(sent, indent=1, ensure_ascii=False))


def ledger_phrases(ledger):
    """Phrases every brief is entitled to write identically: a sector's own name, an
    institution's own name, a state's own name. Masked out of the novelty check."""
    out = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("sector", "state_name", "largest_bank_by_deposits_name",
                           "census_region", "label"):
                    if isinstance(value, str):
                        out.add(value.lower())
                elif key == "largest_credit_unions_by_name" and isinstance(value, list):
                    out.update(str(v).lower() for v in value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for record in ledger.values():
        walk(record)
    out.update({"credit union", "credit unions", "national norm", "median household",
                "retail offices", "square mile", "home value"})
    return {p for p in out if len(p) > 3}


def generate(client, name, record, max_rounds, seen_grams, masks=()):
    sent, user = build_user(record)
    budget = superlative_budget(record)
    allowed = allowed_figures(sent)
    noun = sent["geography"]["noun"]
    con = sent["constraints"]
    want_year = (con.get("industry_vintage_year")
                 if con.get("industry_vintage_is_current") is False else None)
    forbid_word = "district" if noun == "state" else None
    require_word = "state" if noun == "state" else None

    messages = [{"role": "user", "content": user}]
    attempts = []
    for rnd in range(max_rounds):
        text = client.call(messages, name, rnd + 1).strip()
        defects = validate(text, sent, budget, allowed, seen_grams,
                           want_year=want_year, forbid_word=forbid_word,
                           require_word=require_word, member_name=name, masks=masks)
        attempts.append({"round": rnd + 1, "text": text, "defects": defects})
        if not defects:
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
            found = figures_in(text, figure_name_masks(sent))
            return {
                "ok": True, "name": name, "rounds": rnd + 1,
                "p1": paragraphs[0], "p2": paragraphs[1], "p3": paragraphs[2],
                "text": text,
                "words": len(re.findall(r"[A-Za-z0-9$%,.'-]+", text)),
                "figures": [f[0] for f in found if not f[3]],
                "years": [f[0] for f in found if f[3]],
                "figure_sources": {f[0]: allowed.get(round(f[1], 6),
                                                     allowed.get(round(f[2], 6)))
                                   for f in found},
                "superlative_budget": budget,
                "attempts": attempts,
            }
        # The lane's measured failure mode is that the model shaves a few words off
        # every sentence instead of cutting a point, and asymptotes above the ceiling.
        # Tell it the exact number of words to remove and what to remove.
        length = len(re.findall(r"[A-Za-z0-9$%,.'-]+", text))
        note = ""
        if length > WORD_MAX:
            note = ("\n\nThis draft is %d words. Cut %d of them. Do that by deleting two "
                    "or three whole points outright, not by trimming every sentence: the "
                    "last several attempts at this failed because they trimmed evenly and "
                    "stayed long. Nine points total, three to a paragraph, four sentences "
                    "each." % (length, length - 220))
        elif length < WORD_MIN:
            note = ("\n\nThis draft is %d words. Add about %d by developing a point you "
                    "already make, not by adding new figures." % (length, 220 - length))
        messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "That draft fails these checks:\n"
                                        + "\n".join("- " + d for d in defects)
                                        + "\n\nRewrite the whole brief, fixing every "
                                          "point. Keep everything that already worked."
                                        + note},
        ]
    return {"ok": False, "name": name, "rounds": max_rounds,
            "defects": attempts[-1]["defects"], "text": attempts[-1]["text"],
            "attempts": attempts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(OUT, "demo_ledger.json"))
    ap.add_argument("--members", nargs="*", default=None)
    ap.add_argument("--members-file", default=None)
    ap.add_argument("--all-members", action="store_true",
                    help="explicitly request every member in the ledger")
    ap.add_argument("--resume-artifact", default=None,
                    help="published slim artifact whose accepted briefs seed resume")
    ap.add_argument("--out", default=os.path.join(OUT, "demo_briefs.json"))
    ap.add_argument("--progress", default=os.path.join(OUT, "demo_progress.jsonl"))
    ap.add_argument("--api-log", default=os.path.join(OUT, "demo_api_log.jsonl"))
    ap.add_argument("--api-key-file", default=KEYS_JSON)
    ap.add_argument("--api-key-index", type=int, default=0,
                    help="zero-based configured Anthropic key to use (never printed)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-rounds", type=int, default=6)
    ap.add_argument("--scan-only", action="store_true")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    hits = scan_text(SYSTEM)
    if hits:
        say("SYSTEM-PROMPT SELF-SCAN: %d self-inflicted match(es)" % len(hits))
        for label, pattern, matched in hits:
            say('  [%s] pattern %s matched "%s"' % (label, pattern, matched))
        raise SystemExit("Fix the system prompt before spending anything.")
    say("SYSTEM-PROMPT SELF-SCAN: clean, 0 matches against %d ban patterns."
        % n_ban_patterns())

    ledger = json.load(open(args.ledger, encoding="utf-8"))["members"]
    names = list(ledger) if args.all_members else (args.members or [])
    if args.members_file:
        if args.all_members:
            raise SystemExit("--all-members and --members-file are mutually exclusive")
        names = [n.strip() for n in open(args.members_file, encoding="utf-8-sig")
                 if n.strip() and not n.lstrip().startswith("#")]
    if not names:
        raise SystemExit("no members requested; this script does not run the full roster "
                         "without an explicit list")
    missing = [n for n in names if n not in ledger]
    if missing:
        raise SystemExit("not in the ledger: %s" % missing)

    # The same scan over every user message we are about to send. Data values (an
    # institution's own name, a sector's own name) can legitimately contain a banned
    # word, so these are reported, not fatal.
    say("USER-MESSAGE SELF-SCAN over %d member(s):" % len(names))
    total = 0
    for name in names:
        _sent, user = build_user(ledger[name])
        member_hits = scan_text(user)
        total += len(member_hits)
        for label, _pattern, matched in member_hits:
            say('  %-22s [%s] seeds "%s"' % (name, label, matched))
    say("  %d seeded phrase(s) across %d member(s)." % (total, len(names)))
    if args.scan_only:
        return

    candidate_seeds = list(artifact_records(args.resume_artifact))
    unknown_seed_names = sorted({r["name"] for r in candidate_seeds} - set(ledger))
    if unknown_seed_names:
        raise SystemExit("resume artifact contains members outside the ledger: %s"
                         % unknown_seed_names)
    seed_records = []
    rejected_seeds = []
    for record in candidate_seeds:
        name = record["name"]
        if name not in names:
            continue
        raw = ledger[name]
        sent, _user = build_user(raw)
        noun = sent["geography"]["noun"]
        constraints = sent["constraints"]
        want_year = (constraints.get("industry_vintage_year")
                     if constraints.get("industry_vintage_is_current") is False
                     else None)
        defects = validate(
            record["text"], sent, superlative_budget(raw), allowed_figures(sent), set(),
            want_year=want_year,
            forbid_word="district" if noun == "state" else None,
            require_word="state" if noun == "state" else None,
            member_name=name,
        )
        if defects:
            rejected_seeds.append((name, defects))
        else:
            seed_records.append(record)
    if candidate_seeds:
        say("resume artifact: %d accepted under current gates, %d queued for regeneration"
            % (len(seed_records), len(rejected_seeds)))
        for name, defects in rejected_seeds:
            say("  %-26s STALE  %s" % (name, str(defects)[:200]))
    done = {record["name"]: record for record in seed_records}
    for record in iter_progress(args.progress):
        done[record.get("name")] = record
    todo = [n for n in names if not (done.get(n) or {}).get("ok")]
    say("requested %d, already accepted %d, generating %d"
        % (len(names), len(names) - len(todo), len(todo)))

    key = read_key(args.api_key_file, args.api_key_index)
    limiter = AdaptiveLimiter(args.workers)
    client = ApiClient(key, MODEL, args.api_log, limiter)
    writer = ProgressWriter(args.progress)
    masks = ledger_phrases({n: ledger[n] for n in names})
    say("novelty mask: %d phrases the evidence entitles every brief to repeat" % len(masks))
    window, gram_counts = rebuild_novelty_window(
        args.progress, masks, seed_records=seed_records)
    say("novelty window: comparing each new brief against the %d most recently "
        "accepted brief(s) (%d gram(s) in the window), not the whole run"
        % (len(window), len(gram_counts)))

    started = time.time()

    def run(name):
        limiter.acquire()
        try:
            with NOVELTY_LOCK:
                grams = set(gram_counts)
            result = generate(client, name, ledger[name], args.max_rounds, grams, masks)
            if result.get("ok"):
                with NOVELTY_LOCK:
                    window_add(window, gram_counts, brief_grams(result["text"], masks))
            return result
        except Exception as error:                                       # noqa: BLE001
            return {"ok": False, "name": name, "error": repr(error)}
        finally:
            limiter.release()

    if todo:
        # One member serially first, so the shared system prompt is written to cache
        # once rather than raced for by cold workers.
        first = run(todo[0])
        writer.append(first)
        say("  %-26s %s rounds=%s" % (first["name"], "OK " if first.get("ok") else "FAIL",
                                      first.get("rounds")))
        rest = todo[1:]
        if rest:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                futures = {pool.submit(run, n): n for n in rest}
                for future in as_completed(futures):
                    result = future.result()
                    writer.append(result)
                    say("  %-26s %s rounds=%s%s"
                        % (result["name"], "OK " if result.get("ok") else "FAIL",
                           result.get("rounds"),
                           "" if result.get("ok") else
                           ("  " + str(result.get("error") or result.get("defects"))[:200])))
    writer.close()

    # assemble from disk, not memory
    final = {record["name"]: record for record in seed_records}
    for record in iter_progress(args.progress):
        final[record.get("name")] = record
    briefs = {n: final[n] for n in names if n in final}
    missing = [n for n in names if n not in briefs]
    if missing:
        raise SystemExit("progress log is missing requested members: %s" % missing)

    stats = dict(client.stats)
    price = price_for(MODEL)
    cost = (stats.get("input_tokens", 0) / 1e6 * price["input"]
            + stats.get("output_tokens", 0) / 1e6 * price["output"]
            + stats.get("cache_creation_input_tokens", 0) / 1e6 * price["cache_write"]
            + stats.get("cache_read_input_tokens", 0) / 1e6 * price["cache_read"])
    summary = {
        "schema": "district-demographic-briefs-v1",
        "model": MODEL,
        "word_gate": [WORD_MIN, WORD_MAX],
        "prompt_target": [PROMPT_MIN, PROMPT_MAX],
        "figure_cap": FIGURE_CAP,
        "requested": len(names),
        "passed": sum(1 for b in briefs.values() if b.get("ok")),
        "failed": sum(1 for b in briefs.values() if not b.get("ok")),
        "rounds_total": sum(b.get("rounds") or 0 for b in briefs.values()),
        "elapsed_seconds": round(time.time() - started, 1),
        "api": stats,
        "price_per_mtok_usd": price,
        "cost_usd": round(cost, 4),
        "briefs": briefs,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    say("")
    say(json.dumps({k: v for k, v in summary.items() if k != "briefs"}, indent=1))
    say("wrote " + args.out)


if __name__ == "__main__":
    main()
