# -*- coding: utf-8 -*-
"""Validate v3 member briefs against their packets and the style-guide mechanics (guide section 14).

    python fec/members/validate_member_briefs_v3.py --briefs data/members/member_briefs_v3.json \
        --packets data/members/member_brief_packets_v3.json --report issues.json [--write-receipt]

Exit status is nonzero when any brief fails.  ``--write-receipt`` writes
``member_briefs_v3.receipt.json`` only when every expected member passed, every brief carries the
packet_sha256 of the packet its writer saw, and the packet receipt is READY (not partial or dry run).

The desk (``load_publication``) trusts the receipt only to bind the briefs file.  Per member, it
recomputes the packet hash from the current inputs: a match shows the brief, a mismatch still shows
it with a visible "evidence updated" line and queues the member for a rewrite.  Nothing is hidden
silently and one member's change never blocks the others.

The banned-term lists are imported from ``revise_member_narratives_v2`` and extended with the
guide's additions; they are never copied.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import functools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fec.members import build_member_brief_packets as P  # noqa: E402
from fec.members import revise_member_narratives_v2 as R  # noqa: E402

BRIEFS_SCHEMA = "member-briefs-v3"
RECEIPT_SCHEMA = "member-briefs-v3-receipt-v1"
BRIEFS_PATH = P.BRIEFS_PATH
PROMPT_PATH = Path(__file__).with_name("member_briefs_v3_writer_prompt.md")
RECEIPT_PATH = ROOT / "data/members/member_briefs_v3.receipt.json"

KEYS = ("archetype", "bottom_line", "do", "dont", "warning_sign", "follow_up", "personality", "speaks",
        "decides", "evidence_refs")
REF_KEYS = (["archetype", "bottom_line"] + ["do.%d" % i for i in range(1, 5)] + ["dont.%d" % i for i in range(1, 4)]
            + ["warning_sign", "follow_up", "personality", "speaks", "decides"])
PROSE = ("bottom_line", "warning_sign", "follow_up", "personality", "speaks", "decides")

# ---- banned language: v2 lists (imported) + guide section 11 additions -------------------------
PRIVATE_STATE = set(R.BANNED_PRIVATE_STATE) | {
    "even keel", "neuroticism", "paranoid", "manic", "depressed", "depression", "bipolar", "narcissist",
    "narcissistic", "traumatized", "trauma", "hysterical", "disorder", "pathological", "obsessive",
    "compulsive", "insecure", "fragile", "unhinged", "meltdown", "mentally", "psychological", "diagnosis"}
UNSUPPORTED = set(R.BANNED_UNSUPPORTED) | {
    "drawn to attention", "loves the camera", "spotlight", "attention-seeking", "showboat", "grandstand",
    "ego", "vain", "secretly", "privately", "deep down", "at heart"}
JARGON = set(R.BANNED_JARGON) | {
    "ledger", "packet", "band", "tracked", "covered colleagues", "measured", "metric", "metrics", "rated", "rating",
    "score", "scores", "expert panel", "expert panels", "experts read", "experts see", "the data", "our data",
    "data show", "data shows", "data suggest", "dataset", "the record shows", "evidence shows", "evidence suggests",
    "in the evidence", "construct", "facet", "baseline", "jitter", "f0", "egemaps", "voiced segments",
    "words per turn", "floor-defense", "cosponsorship share", "tie share", "middling", "well above most",
    "well below most", "above most", "below most", "among the highest in the chamber",
    "among the lowest in the chamber", "openness", "conscientiousness", "extraversion", "extroversion",
    "agreeableness", "big five", "personality trait", "basis", "confidence level"}
HEDGES = {"seems", "seem to", "appears to", "apparently", "perhaps", "possibly", "arguably", "presumably",
          "it is possible", "might be", "may be", "could be", "likely", "probably", "to some extent",
          "in some ways", "sort of", "kind of"}
MISSING_TALK = {"no data", "limited data", "little data", "not enough", "insufficient", "unknown", "unclear",
                "not available", "no record of", "hard to say", "cannot tell", "difficult to assess",
                "thin record", "sparse"}
CLICHES = {"navigate", "landscape", "leverage", "delve", "nuanced", "multifaceted", "tapestry", "testament",
           "robust", "synergy", "stakeholders", "at the end of the day", "it's worth noting", "it is worth noting",
           "in today's", "game-changer", "double-edged sword", "force to be reckoned with",
           "wears his heart on his sleeve", "wears her heart on her sleeve", "close to the vest",
           "close to his chest", "close to her chest", "read the room", "lead with outcomes",
           "let the credit accrue", "a discount", "not immunity", "a discount rather than a wall",
           "best confirmed in person", "neither warmth nor directness", "two registers",
           "the interesting split", "pull apart", "runs on presence", "not just", "more than just"}
BANNED_GROUPS = (("private-state or clinical", PRIVATE_STATE), ("unsupported inference", UNSUPPORTED),
                 ("measurement jargon", JARGON), ("hedge", HEDGES), ("missing-data talk", MISSING_TALK),
                 ("cliche or worn phrase", CLICHES))
# The guide bans "above most" and "below most" as exact phrases; "more than most" is the replacement.

_DEFENSE = re.compile(r"\bdefen(?:s|c|d)\w*\b|\bpush\s*back\b|\bpushback\b|\bpushes\s+back\b|\bpushing\s+back\b"
                      r"|\bholds?\s+the\s+line\b|\bholding\s+the\s+line\b|\bdigs?\s+in\b|\bdigging\s+in\b", re.I)
_TOPIC = re.compile(r"\bsets?\s+(?:him|her|them)\s+off\b|\bsore\s+spot\b|\bhot-button\b|\boff-limits\b|"
                    r"\bsensitive\s+(?:topic|subject|area)\b|\bpressure\s+points?\b|\blandmines?\b|"
                    r"\b(?:topic|subject)s?\s+to\s+avoid\b|\btopic-specific\b|\b(?:particular|specific)\s+(?:topic|subject)\b", re.I)
_TRIGGER = re.compile(r"\btrigger(?:s|ed|ing)?\b", re.I)
_WORD = r"(?!and\b)[a-z]+(?:-[a-z]+)*"
_PART = r"%s(?: %s){0,3}" % (_WORD, _WORD)
_DESCRIPTOR = re.compile(r"%s and %s|%s, %s, and %s" % ((_PART,) * 5))
_PARTY = re.compile(r"\b(?:Republicans?|Democrats?|GOP)\b", re.I)
_PARTY_ADJ = re.compile(r"\bDemocratic\b")        # capitalized: "Democratic" as a party word
# Top-band words are for "among the highest/lowest" items only; weak-strength words are for "well below most" or lower.
_TOP_BAND = re.compile(r"\bfew (?:match|are|members|senators|in the)\b|\bas \w+(?: \w+){0,2} as (?:almost |nearly )?any(?:one)?\b|"
                       r"\bone of the (?:most|least)\b|\b(?:nearly|almost) (?:anyone|everyone)\b|\balmost no\b|\bamong the\b|"
                       r"\bthan (?:almost |nearly )?anyone\b", re.I)
_WEAK_WORDS = re.compile(r"(?<!\ba )\blittle\b|\brare(?:ly)?\b|\bseldom\b|\bhardly\b|\bbarely\b|\bdoes little\b|\bcounts? for little\b|"
                         r"\blow end\b", re.I)
# Appeal advice (guide 5.7, check 17): leading with an axis the member is below most on
_LEAD_VERB = re.compile(r"\b(?:lead with|open with|start with|put first|build the case on)\b", re.I)
_RESULTS_WORDS = {"results", "outcome", "outcomes", "facts", "data", "numbers", "wins"}
_PEOPLE_WORDS = {"people", "community", "communities", "constituent", "constituents", "stories", "human", "families", "relationship", "relationships"}
_STATES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|"
           "Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|"
           "New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
           "South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming").split("|")
_STATE = re.compile(r"\b(?:%s)\b" % "|".join(sorted(_STATES, key=len, reverse=True)))      # longest first: "West Virginia" is one state
# Clause-level strength (guide 14 check 15, S8): the construct keyword in a sentence names the ids its strength words are judged against.
CONSTRUCTS = (
    (re.compile(r"\bvotes?\b|\bvoting\b|\bfollow[- ]through\b|\bbills? through\b|\bsees? (?:his|her|their) bills\b|\bcarried through\b", re.I), ("BH.diligence",)),
    (re.compile(r"\bparty\b|\baisle\b|\bparties\b|\bcross-party\b", re.I), ("BH.coop",)),
    (re.compile(r"\bvisible\b|\bprofile\b|\bvisibility\b", re.I), ("VI.observed", "VI.role")),
    (re.compile(r"\bhearings?\b", re.I), ("HA",)),
    (re.compile(r"\bsharp(?:er)?\b|\bedge\b|\bblunt\b", re.I), ("QR.antagonism",)),
    (re.compile(r"\bfiller\b", re.I), ("IX.discmark",)),
    (re.compile(r"\bfloor\b|\btakes? charge\b", re.I), ("BH.dominance",)),
    (re.compile(r"\banswers?\b|\breplies\b|\bwords per turn\b", re.I), ("BH.wpt", "IX.wpt_mean", "IX.wpt_p90", "IX.gini")),
    (re.compile(r"\bissues?\b|\btopics?\b|\bissue areas\b", re.I), ("BH.breadth", "QR.breadth")),
    (re.compile(r"\bpressure\b", re.I), ("RC",)),
    (re.compile(r"\bemotional\b|\bemotion\b", re.I), ("QR.affect",)),
)
_ABSOLUTE = re.compile(r"\bin full\b|\bto the end\b|\bat the very top\b|\bdoes(?: not|n't) fluster\b", re.I)
_STRESS = re.compile(r"\bgets? to (?:him|her|them)\b|\blands? harder on\b|\bunsettles?\b|\brattles?\b", re.I)
_FORECAST = re.compile(r"\bso expect\b|\bso the (?:exchange|talk|conversation|meeting)\b|\bexpect an? (?:\S+\s+){0,6}?(?:exchange|talk|conversation|meeting)\b", re.I)
_FACT_WORDS = re.compile(r"\b(?:words|language|tone|sharp|sharper|gentle|mild|emotional|charge|hearing|hearings)\b", re.I)
_SHORT_WORDS = re.compile(r"\b(?:short|shorter|clipped|terse|brief)\b", re.I)
_LONG_WORDS = re.compile(r"\b(?:long|longer|stretch(?:es|ed|ing)?)\b", re.I)
LENGTH_IDS = {"BH.wpt", "IX.wpt_mean", "IX.wpt_p90", "IX.gini"}
PERSIST_VERBS = ("repeat", "restate", "press", "push", "persist", "insist", "escalate", "chase", "nag", "badger")
_GENERIC = {"make", "keep", "give", "take", "get", "let", "use", "need", "want", "put"}
_NEGATION = re.compile(r"\b(?:not|never|no|don't|avoid|stop)\b", re.I)
PRONOUN_FAMILY = {"he": {"he", "his", "him"}, "she": {"she", "her", "hers"}, "they": {"they", "their", "them"}}
_TITLES = re.compile(r"\b(?:Rep|Sen)\.|\b(?:Representative|Senator|Congressman|Congresswoman)\b")
_LABELS = re.compile(r"^\s*(?:personality|how\s+(?:he|she|they)\s+(?:speaks?|decides?)|bottom\s+line|do|don't|"
                     r"warning\s+sign|follow-up|basis|confidence)\s*[:.]", re.I)
_MARKUP = re.compile(r"[\"“”*`#]")
_BAD_FIRST = {"do", "don't", "dont", "never", "avoid", "not", "the", "a", "an", "he", "she", "they", "his", "her",
              "their", "it"}
BANNED_ANIMALS = {"snake", "rat", "weasel", "vulture", "hyena", "jackal", "pig", "sloth", "leech", "shark",
                  "donkey", "elephant"}
MYTHICAL_ANIMALS = {"dragon", "phoenix", "unicorn", "griffin", "gryphon", "hydra", "centaur", "minotaur", "pegasus", "sphinx",
                    "kraken", "basilisk", "chimera", "cerberus", "werewolf", "mermaid", "serpent", "yeti", "bigfoot", "sasquatch",
                    "leviathan", "behemoth", "thunderbird", "wyvern", "troll", "ogre", "golem", "siren", "harpy", "cyclops",
                    "fairy", "banshee", "wendigo", "chupacabra", "kitsune", "kirin", "qilin", "roc"}
# Spelled-out numbers that describe the member or the evidence (guide 11H).  Reader advice may use them
# ("a sentence or two", "within two days"), so only the profile and archetype fields are checked.  "one"
# is only barred in quantity patterns, never in "one of the most".
_NUMBER_WORDS = re.compile(
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|dozens?|half|twice|"
    r"double|triple|percent)\b|\bone (?:hundred|thousand|million|in|out of|third|half|quarter|percent)\b", re.I)
NUMBER_CHECKED = ("personality", "speaks", "decides", "archetype.descriptor", "archetype.justification")
DESCRIPTOR_NEEDS = {  # adjective -> predicate over (levels, gates, dims, ps categories)
    "partisan": lambda v: v.level.get("BH.coop", 0) <= -1,
    "visionary": lambda v: v.level.get("TR.O", 0) >= 1,
    "ambitious": lambda v: v.level.get("VI.role", 0) >= 1,
    "restless": lambda v: any(v.level.get(i, 0) >= 1 for i in ("IX.burst", "AU.pace", "FC.excitement")),
    "loud": lambda v: any(v.level.get(i, 0) >= 1 for i in ("AU.volume", "FC.assertiveness")),
    "eager": lambda v: v.level.get("TR.E", 0) >= 1,
    "protective": lambda v: "community" in v.dims and bool(v.ps & {"family", "formative", "district"}),
}
SENTENCE_MAX_WORDS = 28
FK_FAIL, FK_WARN = 10.0, 8.5


# ---- text helpers ---------------------------------------------------------------------------------
def words(text: str) -> list[str]:
    return str(text).split()


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", str(text).strip()) if s]


def _hit(term: str, text: str) -> bool:
    return re.search(r"(?<![\w])%s(?![\w])" % re.escape(term), text, re.I) is not None


def syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.casefold())
    if not w:
        return 0
    count = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and count > 1:
        count -= 1
    return max(1, count)


def fk_grade(parts: list[str]) -> float:
    sents = sum(max(1, len(sentences(p))) for p in parts)
    toks = [w for p in parts for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", p)]
    if not toks or not sents:
        return 0.0
    return 0.39 * len(toks) / sents + 11.8 * sum(syllables(w) for w in toks) / len(toks) - 15.59


def ngrams(text: str, n: int) -> set[str]:
    toks = re.findall(r"[a-z']+", plain_apostrophes(text).casefold())
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


COPY_RUN = 5   # style guide 12 and 14.11: five or more consecutive words copied from the packet fail


def copied_runs(text: str, source_grams: set[str], n: int = COPY_RUN) -> list[str]:
    """Five-word runs of ``text`` found in the packet's own wording.  A run that carries a proper-noun
    phrase (two or more capitalized words) is exempt: a named event or place must be named the same way."""
    toks = re.findall(r"[A-Za-z']+", plain_apostrophes(text))
    out = []
    for i in range(len(toks) - n + 1):
        window = toks[i:i + n]
        if " ".join(window).casefold() in source_grams and sum(1 for w in window if w[:1].isupper()) < 2:
            out.append(" ".join(window).casefold())
    return out


def plain_apostrophes(text) -> str:
    return str(text).replace("\u2019", "'").replace("\u2018", "'")


EXAMPLE_RUN = 6   # six or more consecutive words shared with the writer-prompt examples or the gold exemplars fail
# ... unless the run is boilerplate: the guide itself prescribes "as X as anyone in the House" and "one of the most X
# members", so a run counts as copied only when at least EXAMPLE_CONTENT of its words carry content.
EXAMPLE_CONTENT = 2
_FUNCTION_WORDS = frozenset("""a an the and or but nor so of in on at to for from by with as into over than is are was were be been being
    he she they his her their him them it its this that these those not no one two most least more less much very almost nearly
    anyone anybody few any all some each every other another members member house senate chamber who whom which when while""".split())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k != "evidence_refs":
                yield from _strings(v)


@functools.lru_cache(maxsize=None)
def example_rows() -> tuple:
    """((owner surname, lower case, row), ...) for the writer prompt's inline example briefs and the gold exemplars.
    The owner is the first word of the example's personality paragraph."""
    rows = []
    if PROMPT_PATH.is_file():
        for block in re.findall(r"```[a-z]*\n(.*?)```", PROMPT_PATH.read_text(encoding="utf-8"), re.S):
            try:
                rows.append(json.loads(block))
            except ValueError:
                continue
    if Path(P.EXEMPLARS_PATH).is_file():
        rows += P.load_json(P.EXEMPLARS_PATH).get("exemplars") or []
    return tuple(((re.findall(r"[A-Za-z]+", row["personality"]) or [""])[0].casefold(), row)
                 for row in rows if isinstance(row, dict) and isinstance(row.get("personality"), str))


@functools.lru_cache(maxsize=None)
def example_texts() -> dict:
    """{owner surname, lower case: [strings]} from the example rows."""
    owners = collections.defaultdict(list)
    for owner, row in example_rows():
        owners[owner].extend(_strings(row))
    return dict(owners)


@functools.lru_cache(maxsize=None)
def example_grams() -> dict:
    """{owner surname, lower case: set of six-word runs}; a member's own gold text is exempt from the example check."""
    return {owner: set().union(*[ngrams(t, EXAMPLE_RUN) for t in texts]) for owner, texts in example_texts().items()}


EXAMPLE_ANY_RUN = 8   # eight or more consecutive words from an example or gold brief fail, content words or not


def example_copy(text: str, foreign: set):
    """The first run of ``text`` copied from the examples: eight words or more, or six or seven that carry content."""
    toks = re.findall(r"[a-z']+", plain_apostrophes(text).casefold())
    hits = [i for i in range(len(toks) - EXAMPLE_RUN + 1) if " ".join(toks[i:i + EXAMPLE_RUN]) in foreign]
    start = 0
    while start < len(hits):
        end = start
        while end + 1 < len(hits) and hits[end + 1] == hits[end] + 1:
            end += 1
        run = toks[hits[start]:hits[end] + EXAMPLE_RUN]
        if len(run) >= EXAMPLE_ANY_RUN or any(sum(w not in _FUNCTION_WORDS for w in toks[i:i + EXAMPLE_RUN]) >= EXAMPLE_CONTENT for i in hits[start:end + 1]):
            return " ".join(run)
        start = end + 1
    return None


def plain_text_grams(packet: dict) -> set:
    """Five-word runs of the builder's plain phrases (each item's, and the appeal's) that carry content: a run of function
    words only ("he is one of the") is how the guide itself tells the writer to speak, so it never counts as a copy.
    The LD item is left out: the guide requires its title, LD.plain, verbatim."""
    texts = [str(i.get("plain") or "") for i in packet["evidence"] if i["id"] != "LD"] + [str((packet.get("appeals") or {}).get("plain") or "")]
    return {g for t in texts for g in ngrams(t, COPY_RUN) if sum(w not in _FUNCTION_WORDS for w in g.split()) >= EXAMPLE_CONTENT}


def all_text(brief: dict) -> dict[str, str]:
    """Every model-written string by field, with curly apostrophes made straight."""
    out = {"archetype.animal": brief["archetype"].get("animal", ""),
           "archetype.descriptor": brief["archetype"].get("descriptor", ""),
           "archetype.justification": brief["archetype"].get("justification", "")}
    for k in PROSE:
        out[k] = brief.get(k, "")
    for i, t in enumerate(brief.get("do") or [], 1):
        out["do.%d" % i] = t
    for i, t in enumerate(brief.get("dont") or [], 1):
        out["dont.%d" % i] = t
    return {k: plain_apostrophes(v) for k, v in out.items()}


# ---- packet helpers ---------------------------------------------------------------------------------
def packet_ids(packet: dict) -> dict[str, dict]:
    ids = {i["id"]: i for i in packet["evidence"]}
    ids.update({i["id"]: i for i in packet.get("public_sensibilities") or []})
    ids.update({i["id"]: i for i in packet.get("longform") or []})
    ids["PROC"] = {"id": "PROC", "neutral": False, "may_support": sorted(P.PERMISSION["PROC"])}
    return ids


def cue_ids(packet: dict) -> set:
    return P.cue_eligible_ids(packet["evidence"], packet.get("gates"), packet.get("longform"))


_EARLIER = re.compile(r"\b(?:earlier in|early in|before (?:he|she|they)\b|before becoming|prior to|previously|in an earlier|years ago)", re.I)
_USUAL = re.compile(r"\b(?:usually|usual|normally|normal|by default|as a rule)\b", re.I)


def packet_text(packet: dict) -> str:
    """Packet wording a brief must not copy beyond five consecutive words; the builder's plain phrases are checked separately,
    with a content-word filter (plain_text_grams)."""
    chunks = []
    for i in packet["evidence"]:
        chunks += [str(i.get("scope") or ""), str(i.get("claim") or "")]
    for p in packet.get("public_sensibilities") or []:
        chunks += [str(p.get("claim") or "")]
    for l in packet.get("longform") or []:
        chunks += [str(l.get(k) or "") for k in ("finding", "meeting_implication", "scope")]
    bio = (packet.get("member") or {}).get("bio") or {}
    chunks += [str(bio.get("prior_career") or "")]
    return " ".join(chunks)


# ---- one brief ---------------------------------------------------------------------------------------
def validate_brief(brief: dict, packet: dict, *, require_binding: bool = False) -> tuple[list[dict], list[dict]]:
    issues: list[dict] = []
    warnings: list[dict] = []
    stamp = brief.get("packet_sha256") if isinstance(brief, dict) else None
    if isinstance(brief, dict):
        brief = {k: v for k, v in brief.items() if k != "packet_sha256"}   # metadata, not writer output
    member = packet["member"]
    tier = packet["evidence_tier"]
    thin = tier == "thin"

    def bad(field, kind, detail):
        issues.append({"field": field, "kind": kind, "detail": detail})

    # 1. schema
    if not isinstance(brief, dict) or set(brief) != set(KEYS):
        return [{"field": "*", "kind": "schema", "detail": "keys must be exactly %s" % ", ".join(KEYS)}], warnings
    arch = brief["archetype"]
    if not (isinstance(arch, dict) and set(arch) == {"animal", "descriptor", "justification"}
            and all(isinstance(v, str) for v in arch.values())):
        return [{"field": "archetype", "kind": "schema", "detail": "archetype needs animal, descriptor, justification strings"}], warnings
    shape_ok = (all(isinstance(brief[k], str) for k in PROSE)
                and isinstance(brief["do"], list) and len(brief["do"]) == 4 and all(isinstance(x, str) for x in brief["do"])
                and isinstance(brief["dont"], list) and len(brief["dont"]) == 3 and all(isinstance(x, str) for x in brief["dont"]))
    if not shape_ok:
        return [{"field": "*", "kind": "schema", "detail": "do needs 4 strings, dont 3, every other field a string"}], warnings
    refs = brief["evidence_refs"]
    if not isinstance(refs, dict) or set(refs) != set(REF_KEYS):
        return [{"field": "evidence_refs", "kind": "schema", "detail": "evidence_refs keys must be exactly %s" % ", ".join(REF_KEYS)}], warnings
    texts = all_text(brief)
    if stamp is None and require_binding:
        issues.append({"field": "packet_sha256", "kind": "schema", "detail": "brief does not store the packet_sha256 its writer saw"})
    elif stamp is not None and stamp != packet.get("packet_sha256"):
        issues.append({"field": "packet_sha256", "kind": "schema", "detail": "brief was written against a different packet than the one judged"})

    # 2. word bands (guide 3)
    def band(field, text, lo, hi, kind="length"):
        n = len(words(text))
        if not lo <= n <= hi:
            bad(field, kind, "%d words, allowed %d to %d" % (n, lo, hi))

    band("archetype.animal", arch["animal"], 1, 3)
    if len(words(arch["descriptor"])) > 6 or not _DESCRIPTOR.fullmatch(arch["descriptor"]):
        bad("archetype.descriptor", "form", "descriptor must be 'x and y' or 'x, y, and z' in lower case, "
                                            "each part up to four words, at most six words in all")
    band("archetype.justification", arch["justification"], 12, 30)
    if len(sentences(arch["justification"])) != 1:
        bad("archetype.justification", "length", "justification must be one sentence")
    band("bottom_line", brief["bottom_line"], 12 if thin else 15, 30)
    if not 1 <= len(sentences(brief["bottom_line"])) <= 2:
        bad("bottom_line", "length", "bottom_line must be 1 or 2 sentences")
    for i, t in enumerate(brief["do"], 1):
        band("do.%d" % i, t, 3, 15)
    for i, t in enumerate(brief["dont"], 1):
        band("dont.%d" % i, t, 3, 15)
    band("warning_sign", brief["warning_sign"], 25, 45)
    if not 2 <= len(sentences(brief["warning_sign"])) <= 3:
        bad("warning_sign", "length", "warning_sign must be 2 or 3 sentences")
    band("follow_up", brief["follow_up"], 15, 35)
    if not 1 <= len(sentences(brief["follow_up"])) <= 2:
        bad("follow_up", "length", "follow_up must be 1 or 2 sentences")
    for k in ("personality", "speaks", "decides"):
        band(k, brief[k], 35 if thin else 60, 110)

    # 3. bullets
    for field in ["do.%d" % i for i in range(1, 5)] + ["dont.%d" % i for i in range(1, 4)]:
        t = texts[field].strip()
        if t.endswith((".", "!", "?")):
            bad(field, "form", "bullet must not end with a period")
        first = (re.findall(r"[A-Za-z']+", t) or [""])[0]
        if first.casefold() in _BAD_FIRST or not first[:1].isupper():
            bad(field, "form", "bullet must start with a capitalized verb; got %r" % first)
        if ";" in t:
            bad(field, "form", "no semicolon chains in bullets")

    for field in NUMBER_CHECKED:
        if _NUMBER_WORDS.search(texts[field]):
            bad(field, "numbers", "spelled-out number describing the member or the evidence: %r" % _NUMBER_WORDS.search(texts[field]).group(0))

    # 4/5/6. characters, banned language, gates
    gates = packet["gates"]
    for field, t in texts.items():
        if re.search(r"\d", t):
            bad(field, "numbers", "digits are not allowed in model output")
        if "—" in t or "–" in t:
            bad(field, "dash", "em or en dash")
        if _MARKUP.search(t):
            bad(field, "form", "no markdown or quotation marks")
        if _LABELS.match(t):
            bad(field, "form", "section label written by the model")
        for group, terms in BANNED_GROUPS:
            for term in sorted(terms):
                if _hit(term, t):
                    bad(field, "banned_phrase", "%s: %r" % (group, term))
        if _TRIGGER.search(t):
            bad(field, "gate_violation", "trigger language is barred for every member")
        if not gates.get("DF") and _DEFENSE.search(t):
            bad(field, "gate_violation", "defend or push-back language without the DF gate")
        if not gates.get("BH.coop") and R.cross_party_hits(t):
            bad(field, "gate_violation", "cross-party language without the BH.coop gate")
        if not gates.get("topic_associations") and _TOPIC.search(t):
            bad(field, "gate_violation", "topic or sensitivity language without a supported topic association")
        if _TITLES.search(t):
            bad(field, "name_use", "title written; the title line is rendered by code")
        if _PARTY.search(t) or _PARTY_ADJ.search(t):          # a leadership role is written with LD.plain, never with its party-word title
            bad(field, "banned_phrase", "party name: %r" % (_PARTY.search(t) or _PARTY_ADJ.search(t)).group(0))

    # 7. names
    for field, t in texts.items():
        states = _STATE.findall(t)
        if field != "personality" and states:
            bad(field, "name_use", "state name %r may appear only in personality" % states[0])
        elif len(states) > 1:
            bad(field, "name_use", "state names may appear at most once in personality (found %d)" % len(states))
    surname, name = member["surname"], member["display_name"]
    for field, t in texts.items():
        count = len(re.findall(r"(?<!\w)%s(?!\w)" % re.escape(surname), t))
        if field == "personality":
            if count != 1 or not t.startswith(surname):
                bad(field, "name_use", "surname %r must appear exactly once, at the start" % surname)
        elif count:
            bad(field, "name_use", "surname %r may appear only in personality" % surname)
        if name.casefold() in t.casefold():
            bad(field, "name_use", "full display name written")

    # 8. pronouns
    pron = member["pronoun"]
    forbid = {"he": r"she|her|hers|herself", "she": r"he|him|his|himself", "they": r"he|she|him|her|his|hers"}[pron]
    for field, t in texts.items():
        if re.search(r"(?<![\w'])(?:%s)(?![\w'])" % forbid, t, re.I):
            # a public story about a relative ("her father lost his job") legitimately uses the other pronoun
            story = any(c.startswith("PS.") for c in (refs.get(field) or []))
            (warnings if story else issues).append({"field": field, "kind": "pronoun", "detail": "pronoun does not match %s" % pron})
        if pron == "they" and re.search(r"\bthey\s+(?:is|has|was|does|doesn't|isn't)\b", t, re.I):
            bad(field, "pronoun", "they needs plural verbs")

    # 9. evidence refs
    ids = packet_ids(packet)
    appeals = packet.get("appeals") or {}
    mixed = appeals.get("dims") == ["mixed"]
    proc_total = 0
    for key in REF_KEYS:
        cited = refs[key]
        if not isinstance(cited, list) or not cited or not all(isinstance(x, str) for x in cited):
            bad("evidence_refs." + key, "ref_missing", "needs a non-empty list of ids")
            continue
        field = key.split(".")[0]
        for cid in cited:
            item = ids.get(cid)
            if item is None:
                bad("evidence_refs." + key, "ref_wrong", "%s is not in the packet" % cid)
            elif item.get("neutral"):
                bad("evidence_refs." + key, "ref_neutral", "%s is neutral and not citable" % cid)
            elif field not in (item.get("may_support") or []) or (cid == "MA" and mixed):
                bad("evidence_refs." + key, "ref_not_allowed", "%s may not support %s" % (cid, field))
        proc_total += cited.count("PROC")
        known = [c for c in cited if c in ids]
        if field in ("do", "dont", "warning_sign", "follow_up") and not [c for c in known if not c.startswith("TR.")]:
            bad("evidence_refs." + key, "ref_wrong", "%s needs an id that is not a broad trait" % key)
        if field in ("bottom_line", "do", "dont") and known and all(c.startswith("TR.") for c in known):
            bad("evidence_refs." + key, "ref_wrong", "a broad trait needs a non-trait id beside it")
        if field in ("do", "dont") and any(c.startswith(("AU.", "SC.")) for c in known) and not any(
                c.startswith(("IX.", "BH.")) or c == "DF" for c in known):
            bad("evidence_refs." + key, "ref_wrong", "audio ids need an IX, BH or DF id beside them")
        if field == "warning_sign" and any(c.startswith(("AU.", "SC.")) for c in known) and not any(
                c.startswith(("IX.", "BH.")) or c in ("DF", "QR.antagonism") for c in known):
            bad("evidence_refs." + key, "ref_wrong", "an audio cue needs an IX, BH, DF or QR.antagonism id beside it")
        if field != "speaks" and any(i.startswith("LF.") and (ids.get(i) or {}).get("status") == "provisional" for i in known):
            bad("evidence_refs." + key, "longform_misuse", "provisional long-form findings may support speaks only")
        if field not in ("speaks", "personality") and any(i.startswith("LF.") and P.era_restricted((ids.get(i) or {}).get("era")) for i in known):
            bad("evidence_refs." + key, "longform_misuse",
                "a long-form finding from before Congress or before the current role may support speaks and personality only")
    bio_cited = [c for c in refs.get("personality") or [] if isinstance(c, str) and c.startswith(("BIO.", "PS."))]
    if len(set(bio_cited)) > 2:
        bad("evidence_refs.personality", "bio_cap", "personality cites %d biography or sensibility items (%s); the cap is two" % (len(set(bio_cited)), ", ".join(sorted(set(bio_cited)))))
    if tier == "rich" and proc_total > 2:
        bad("evidence_refs", "generic_advice", "a rich packet may cite PROC at most twice (cited %d)" % proc_total)
    cited_all = {c for v in refs.values() if isinstance(v, list) for c in v}
    if "TR.N" in cited_all:
        for key, v in refs.items():
            if isinstance(v, list) and "TR.N" in v and key.split(".")[0] not in ("personality", "archetype"):
                bad("evidence_refs." + key, "ref_not_allowed", "TR.N may be cited under personality or archetype only")

    # strength words need the strength behind them (E1/E6), clause by clause (S8): the keyword in the same sentence names the
    # construct; without one the ids cited for the field decide.  Top-band phrases need +3 or -3, little/rare/seldom -2 or lower.
    levels = {i["id"]: P.level_of(i["band"]) for i in packet["evidence"] if i.get("band") in P.LEVELS}
    rc_item = next((i for i in packet["evidence"] if i["id"] == "RC" and not i.get("neutral")), None)
    if rc_item:       # pressure carries a strength, not a band: only "outer" earns top-band or low-strength words
        levels["RC"] = (3 if rc_item.get("strength") == "outer" else 1) * (-1 if rc_item.get("direction") == "less" else 1)

    def construct_ok(ids, strong):
        return any((abs(levels.get(i, 0)) == 3) if strong else levels.get(i, 0) <= -2 for i in ids)

    def nearest_construct(sentence, match):
        found = [(max(0, max(m.start(), match.start()) - min(m.end(), match.end())), ids) for pattern, ids in CONSTRUCTS for m in pattern.finditer(sentence)]
        return min(found, key=lambda x: x[0])[1] if found else None

    for field, t in texts.items():
        key = "archetype" if field.startswith("archetype.") else field
        cited = [levels[c] for c in (refs.get(key) or []) if isinstance(c, str) and c in levels]
        for sentence in sentences(t):
            for strong, pattern, need in ((True, _TOP_BAND, "among the highest or lowest in the chamber"), (False, _WEAK_WORDS, "well below most or lower")):
                for m in pattern.finditer(sentence):
                    construct = nearest_construct(sentence, m)
                    ok = construct_ok(construct, strong) if construct else (any(abs(v) == 3 for v in cited) if strong else any(v <= -2 for v in cited))
                    if not ok:
                        bad(field, "wrong_strength", "%r needs %s at %s" % (m.group(0), "/".join(construct) if construct else "a cited item", need))
        if _ABSOLUTE.search(t):
            bad(field, "absolute_claim", "%r is an absolute the evidence cannot carry at any band" % _ABSOLUTE.search(t).group(0))
        for sentence in sentences(t):
            if _STRESS.search(sentence) and re.search(r"\bpressure\b", sentence, re.I) and ("RC" in (refs.get(key) or []) or "TR.N" not in (refs.get(key) or [])):
                bad(field, "construct_conflation", "pressure worded as a feeling (%r): RC is how much outside pressure moves positions, not stress" % _STRESS.search(sentence).group(0))
            if _FORECAST.search(sentence) and _FACT_WORDS.search(sentence):
                warnings.append({"field": field, "kind": "conversation_forecast",
                                 "detail": "a words or hearing fact followed by a forecast of the conversation (%r)" % _FORECAST.search(sentence).group(0)})

    # an earlier-career finding is said to be one ("earlier in his career"); a reviewer sees a warning when the sentence does not
    for key in ("personality", "speaks"):
        if any(isinstance(c, str) and c.startswith("LF.") and P.era_restricted((ids.get(c) or {}).get("era")) for c in refs.get(key) or []) \
                and not _EARLIER.search(texts[key]):
            warnings.append({"field": key, "kind": "era_phrase",
                             "detail": "cites a finding from before Congress or before the current role; say so (\"earlier in his career\")"})

    # 19. the warning form the packet set
    form, cited_warning = packet.get("warning_form"), [c for c in refs.get("warning_sign") or [] if isinstance(c, str)]
    if form == "observed":
        if not _USUAL.search(texts["warning_sign"]):
            bad("warning_sign", "warning_form", "an observed warning sign states the usual pattern first (usually, usual, normally, normal, by default, as a rule)")
        if not set(cited_warning) & cue_ids(packet):
            bad("warning_sign", "warning_form", "an observed warning sign cites a cue-eligible id (DF, IX, BH.wpt, BH.dominance, QR.antagonism, HA or a qualified long-form finding)")
        short_word, long_word = _SHORT_WORDS.search(texts["warning_sign"]), _LONG_WORDS.search(texts["warning_sign"])
        if short_word and levels.get("IX.burst", 0) >= 1:
            bad("warning_sign", "warning_form", "%r as the change, but IX.burst is above most or higher: quick, short replies are his usual pattern" % short_word.group(0))
        if long_word and levels.get("IX.wpt_p90", 0) >= 1:
            bad("warning_sign", "warning_form", "%r as the change, but IX.wpt_p90 is above most or higher: long answers are his usual pattern" % long_word.group(0))
        if (short_word or long_word) and levels.get("IX.gini", 0) >= 1:
            bad("warning_sign", "warning_form", "answer length (%r) as the change, but IX.gini is above most or higher" % (short_word or long_word).group(0))
        cited_cues = set(cited_warning) & cue_ids(packet)
        stronger = [i for i in cue_ids(packet) - LENGTH_IDS if abs(levels.get(i, 0)) == 3]
        if cited_cues and cited_cues <= LENGTH_IDS and stronger:
            bad("warning_sign", "warning_form", "the cue rests only on answer-length items (%s) while %s sits at the outer band" % (", ".join(sorted(cited_cues)), ", ".join(sorted(stronger))))
    elif form == "procedural":
        if "PROC" not in cited_warning:
            bad("warning_sign", "warning_form", "a procedural warning sign cites PROC")
        if re.search(r"\bmeans\b", texts["warning_sign"], re.I):
            bad("warning_sign", "warning_form", "a procedural warning sign never says what a quiet meeting means")

    # the warning sign must not tell the reader to stop what a Do asks for, or to keep doing what a Don't bars (heuristic: a reviewer check)
    for kind_list, cue in ((brief["do"], r"(?:stop|quit|cease|avoid|never|don't|do not)"), (brief["dont"], r"(?:keep|continue|go on|start|begin)")):
        for bullet in kind_list:
            verb = (re.findall(r"[A-Za-z]+", bullet) or [""])[0].casefold()
            if len(verb) >= 4 and re.search(r"\b%s\s+%s\w*" % (cue, re.escape(verb[:4])), texts["warning_sign"], re.I):
                warnings.append({"field": "warning_sign", "kind": "warning_conflict",
                                 "detail": "the warning sign says %s %s-, which a %s bullet (%r) says the opposite of" % (cue.split("|")[0].strip("(?:"), verb[:4], "Do" if kind_list is brief["do"] else "Don't", bullet[:40])})

    # 21. two Do bullets that share four content words (a reviewer check: warning only)
    content = [{w for w in re.findall(r"[a-z']+", texts["do.%d" % i].casefold()) if w not in _FUNCTION_WORDS} for i in range(1, 5)]
    for j in range(1, 4):
        twin = next((i for i in range(j) if len(content[i] & content[j]) >= 4), None)
        if twin is not None:
            warnings.append({"field": "do.%d" % (j + 1), "kind": "do_duplication",
                             "detail": "shares %d content words with do.%d" % (len(content[twin] & content[j]), twin + 1)})

    # 22. exemplar tracking (S2, B6): bullets that track one exemplar, plus a warning sign that does; a reviewer check
    def words_of(text):
        return {w for w in re.findall(r"[a-z']+", str(text).casefold()) if w not in _FUNCTION_WORDS}

    mine = [words_of(b) for b in brief["do"] + brief["dont"]]
    for owner, row in example_rows():
        if owner == member["surname"].casefold():
            continue
        theirs = [words_of(b) for b in (row.get("do") or []) + (row.get("dont") or []) if isinstance(b, str)]
        count = sum(1 for m in mine if any(len(m & t) >= 3 for t in theirs))
        if len(words_of(texts["warning_sign"]) & words_of(row.get("warning_sign") or "")) >= 5:
            count += 1
        same = str((row.get("archetype") or {}).get("animal") or "").casefold() == arch["animal"].casefold()
        if count >= (2 if same else 3):
            warnings.append({"field": "*", "kind": "exemplar_tracking",
                             "detail": "%d of the Do/Don't bullets and the warning sign track the %s exemplar%s" % (count, owner, " (same animal)" if same else "")})

    # 21 (S9). a Don't that cites a Do's id, shares its noun, or argues with decides (a reviewer check: warning only)
    def tail_words(text):
        return {w for w in re.findall(r"[a-z']+", " ".join(text.split()[1:]).casefold()) if w not in _FUNCTION_WORDS and w not in _GENERIC and len(w) >= 3}

    def ref_list(key):
        return {c for c in refs.get(key) or [] if isinstance(c, str)} - {"PROC"}

    rc_more = rc_item is not None and rc_item.get("direction") == "more"
    for k, bullet in enumerate(brief["dont"], 1):
        field = "dont.%d" % k
        for j, do_bullet in enumerate(brief["do"], 1):
            shared_ids, shared_nouns = ref_list(field) & ref_list("do.%d" % j), tail_words(bullet) & tail_words(do_bullet)
            if shared_ids:
                warnings.append({"field": field, "kind": "mirrored_dont", "detail": "cites %s like do.%d" % (", ".join(sorted(shared_ids)), j)})
            if shared_nouns:
                warnings.append({"field": field, "kind": "mirrored_dont", "detail": "shares %s with do.%d" % (", ".join(sorted(shared_nouns)), j)})
        verb = (re.findall(r"[A-Za-z]+", bullet) or [""])[0].casefold()
        if verb in PERSIST_VERBS:
            if rc_more:
                warnings.append({"field": field, "kind": "mirrored_dont", "detail": "a Don't about %s needs RC less; with RC more, returning to the ask is a Do" % verb})
            for m in re.finditer(r"\b%s(?:s|ed|ing)?\b" % verb, texts["decides"], re.I):
                if not _NEGATION.search(texts["decides"][max(0, m.start() - 25):m.start()]):
                    warnings.append({"field": field, "kind": "mirrored_dont", "detail": "decides recommends the action this Don't bars (%r)" % m.group(0)})
                    break

    # 17. appeals: never lead with an axis the member is below most on (a Don't may warn against it)
    lead_ok = appeals.get("lead_ok") or {}
    for field, t in texts.items():
        if field.startswith("dont."):
            continue
        for m in _LEAD_VERB.finditer(t):
            tail = set(re.findall(r"[a-z']+", t[m.end():].casefold())[:8])
            if lead_ok.get("results") is False and tail & _RESULTS_WORDS:
                bad(field, "appeal_advice", "%r followed by a results word, but the results axis is below most for this member" % m.group(0))
            if lead_ok.get("people") is False and tail & _PEOPLE_WORDS:
                bad(field, "appeal_advice", "%r followed by a people word, but the people axis is below most for this member" % m.group(0))

    # 10. sentence length and reading level
    for field, t in texts.items():
        for s in sentences(t):
            if len(words(s)) > SENTENCE_MAX_WORDS:
                bad(field, "length", "a sentence has %d words (limit %d)" % (len(words(s)), SENTENCE_MAX_WORDS))
    grade = fk_grade([brief[k] for k in PROSE] + brief["do"] + brief["dont"])
    if grade > FK_FAIL:
        bad("*", "plain_language", "Flesch-Kincaid grade %.1f is above %.0f" % (grade, FK_FAIL))
    elif grade > FK_WARN:
        warnings.append({"field": "*", "kind": "plain_language", "detail": "Flesch-Kincaid grade %.1f is above %.1f" % (grade, FK_WARN)})

    # 11. copying
    source_grams = ngrams(packet_text(packet), COPY_RUN)
    for field, t in texts.items():
        shared = copied_runs(t, source_grams)
        if shared:
            bad(field, "copied_text", "%d or more consecutive words copied from the packet: %r" % (COPY_RUN, shared[0]))
    plain_grams = plain_text_grams(packet)
    for field, t in texts.items():
        shared = copied_runs(t, plain_grams)
        if shared:
            bad(field, "copied_text", "%d or more consecutive words copied from a plain phrase in the packet: %r" % (COPY_RUN, shared[0]))
    foreign = set().union(*[g for owner, g in example_grams().items() if owner != member["surname"].casefold()])
    for field, t in texts.items():
        hit = example_copy(t, foreign)
        if hit:
            bad(field, "copied_text", "%d or more consecutive words copied from the writer-prompt examples or a gold exemplar: %r" % (EXAMPLE_RUN, hit))
    # the seed lines apply to every brief, whatever the packet carries (null, missing or a short list)
    blob = " ".join(t.casefold() for t in texts.values())
    for phrase in dict.fromkeys(a.casefold() for a in [*P.SEED_AVOID_PHRASES, *(packet.get("avoid_phrases") or [])]):
        if phrase and re.search(r"(?<!\w)%s(?!\w)" % re.escape(phrase), blob):
            bad("*", "repetition", "avoid_phrases hit: %r" % phrase)

    # 12. archetype
    animal = arch["animal"]
    cands = {c["animal"]: c for c in packet["archetype_candidates"]}
    if animal.casefold() in BANNED_ANIMALS or any(w.casefold() in BANNED_ANIMALS for w in words(animal)):
        bad("archetype.animal", "archetype_misfit", "animal %r is barred" % animal)
    if animal.casefold() in MYTHICAL_ANIMALS or any(w.casefold() in MYTHICAL_ANIMALS for w in words(animal)):
        bad("archetype.animal", "archetype_misfit", "animal %r is mythical; only real, common animals are allowed" % animal)
    named = (re.compile(r"(?<![\w])(?:a|the|like|as|of) bear(?![\w])", re.I) if animal.casefold() == "bear"    # "bear in mind" is a verb
             else re.compile(r"(?<![\w])%s(?![\w])" % re.escape(animal), re.I))
    for field, t in texts.items():
        if not field.startswith("archetype") and named.search(t):
            bad(field, "archetype_misfit", "the animal %r is named outside the archetype object" % animal)
    coin = packet["coin_recommended"]
    match = coin.get("registry_match")
    registry = {c["animal"]: c for c in packet.get("coined_animals") or []}
    view = P.view_of(packet)
    if member.get("leadership"):          # 18. an elected party leader works through the caucus (guide 8.1, R4)
        if animal.casefold() == "lone wolf":
            bad("archetype.animal", "archetype_misfit", "Lone Wolf is barred for an elected party leader")
        if re.search(r"\b(?:independent|lone)\b", arch["descriptor"], re.I):
            bad("archetype.descriptor", "descriptor_unsupported", "'independent' and 'lone' are barred for an elected party leader")
    if match:
        if animal != match["animal"]:
            bad("archetype.animal", "archetype_misfit", "animal must be %s: a registry entry matches this member" % match["animal"])
    elif not coin["value"]:
        if animal != packet.get("suggested_animal"):
            bad("archetype.animal", "archetype_misfit", "animal must be %s: the packet recommends it and does not recommend a coin" % packet.get("suggested_animal"))
        elif animal in cands and cands[animal]["disqualified"]:
            bad("archetype.animal", "archetype_misfit", "%s is disqualified: %s" % (animal, "; ".join(cands[animal]["contradicted"])))
    elif animal in P.TEMPLATE_ANIMALS:
        bad("archetype.animal", "archetype_misfit", "%s is a template animal, but the packet recommends a coin: use a registry animal or a new name" % animal)
    elif animal in registry and not P.signature_holds(registry[animal].get("signature"), view):
        bad("archetype.animal", "archetype_misfit", "%s is registered with a different signature than this member's evidence" % animal)
    for adjective in re.findall(r"[a-z-]+", arch["descriptor"]):
        need = DESCRIPTOR_NEEDS.get(adjective)
        if need and not need(view):
            bad("archetype.descriptor", "descriptor_unsupported", "%r is not supported by the packet" % adjective)

    # within-brief repetition (guide 12, check 16): no two consecutive sentences open with the same word, except the
    # member's pronoun family (he, his, him), which may open two in a row but not three.  The same rule reads the
    # bullets of do, and of dont, as a list.
    family = PRONOUN_FAMILY[pron]

    def opener_runs(pairs, what):
        run, prev = 0, None
        for field, first in pairs:
            key = "<family>" if first in family else first
            run = run + 1 if (key == prev and first) else 1
            prev = key
            if run >= (3 if key == "<family>" else 2):
                bad(field, "repetition", "%d consecutive %s open with %r" % (run, what, first))

    first_word = lambda t: (re.findall(r"[A-Za-z']+", t) or [""])[0].casefold()
    for field in ("bottom_line", "warning_sign", "follow_up", "personality", "speaks", "decides"):
        opener_runs([(field, first_word(t)) for t in sentences(texts[field])], "sentences")
    opener_runs([("do.%d" % i, first_word(t)) for i, t in enumerate(brief["do"], 1)], "bullets")
    opener_runs([("dont.%d" % i, first_word(t)) for i, t in enumerate(brief["dont"], 1)], "bullets")
    return issues, warnings


# ---- corpus ------------------------------------------------------------------------------------------
CORPUS_MIN_BRIEFS = 3   # a phrase or opening is cross-brief repetition only when at least three briefs share it ...


def corpus_issues(briefs: dict, packets: dict, *, ngram=6, share=0.02, start_share=0.02) -> dict[str, list[dict]]:
    out = collections.defaultdict(list)
    n = len(briefs)
    if n < 2:
        return out
    grams, starts = {}, collections.Counter()
    for name, b in briefs.items():
        text = " ".join(all_text(b).values())
        grams[name] = ngrams(text, ngram)
        sur = packets[name]["member"]["surname"]
        lead = re.findall(r"[A-Za-z']+", b["personality"])
        lead = lead[1:5] if lead and lead[0] == sur else lead[:4]  # surname dropped: it differs per member
        starts[" ".join(lead).casefold()] += 1
        b["__lead"] = " ".join(lead).casefold()
    counts = collections.Counter(g for s in grams.values() for g in s)
    common = {g for g, c in counts.items() if c >= CORPUS_MIN_BRIEFS and c / n > share}      # ... and that is over share of all briefs
    for name in briefs:
        hit = sorted(grams[name] & common)
        if hit:
            out[name].append({"field": "*", "kind": "repetition", "detail": "%d six-word phrases shared by more than %.0f%% of briefs, e.g. %r" % (len(hit), 100 * share, hit[0])})
        if starts[briefs[name]["__lead"]] >= CORPUS_MIN_BRIEFS and starts[briefs[name]["__lead"]] / n > start_share:
            out[name].append({"field": "personality", "kind": "repetition", "detail": "opening shared by more than %.0f%% of briefs" % (100 * start_share)})
    for b in briefs.values():
        b.pop("__lead", None)
    return out


def validate_all(briefs: dict, packets: dict, *, expected: set | None = None, require_ready: bool = True,
                 require_binding: bool = False) -> dict:
    results = {}
    for name in sorted(briefs):
        if name not in packets:
            results[name] = {"pass": False, "issues": [{"field": "*", "kind": "schema", "detail": "no packet for this member"}], "warnings": []}
            continue
        packet = packets[name]
        issues, warnings = [], []
        if require_ready and not packet.get("ready_for_writing"):
            issues.append({"field": "*", "kind": "schema", "detail": "packet is not ready_for_writing: " + "; ".join(packet.get("not_ready_reasons") or [])})
        i2, w2 = validate_brief(briefs[name], packet, require_binding=require_binding)
        issues += i2
        warnings += w2
        results[name] = {"pass": False, "issues": issues, "warnings": warnings}
    valid = {n: b for n, b in briefs.items() if n in packets and not any(i["kind"] == "schema" for i in results[n]["issues"])}
    for name, extra in corpus_issues(valid, packets).items():
        results[name]["issues"] += extra
    for r in results.values():
        r["pass"] = not r["issues"]
    missing = sorted((expected or set()) - set(briefs))
    extra = sorted(set(briefs) - (expected if expected is not None else set(briefs)))
    failed = sorted(n for n, r in results.items() if not r["pass"])
    return {"members": results, "counts": {"briefs": len(briefs), "passed": len(briefs) - len(failed), "failed": len(failed)},
            "failed": failed, "missing": missing, "unexpected": extra}


def load_briefs(path: Path) -> dict:
    doc = P.load_json(path)
    if isinstance(doc.get("briefs"), dict):
        return doc["briefs"]
    if isinstance(doc.get("exemplars"), list):
        return {row["display_name"]: {k: v for k, v in row.items() if k != "display_name"} for row in doc["exemplars"]}
    raise SystemExit("%s: expected a 'briefs' object or an 'exemplars' list" % path)


# ---- publication gate (used by make_member_desk.py) -------------------------------------------------
QUEUE_SCHEMA = "member-briefs-v3-rewrite-queue-v1"


def departed_notice(name: str) -> str | None:
    """The line the desk shows instead of any brief (v3 or v2) for a member who has left."""
    reason = P.departed_members().get(name)
    return "No longer serving (%s)" % reason if reason else None


def _read(path):
    """(object, None) or (None, "unreadable <file>: <why>"); a corrupt file is a result, never a crash."""
    try:
        return P.load_json(path), None
    except (OSError, ValueError) as error:
        return None, "unreadable %s: %s" % (Path(path).name, error)


def _basis_gate(packet_receipt_path, packets_path):
    """None when the Basis line may be shown, else the visible reason it is withheld.  The packets must come
    from a READY packet receipt (never a partial-long-form or dry run) whose hash matches the packets file."""
    if not Path(packet_receipt_path).is_file():
        return "packet receipt is missing"
    receipt, error = _read(packet_receipt_path)
    if error:
        return "packet receipt is unreadable"
    if not isinstance(receipt, dict) or receipt.get("schema") != P.RECEIPT_SCHEMA:
        return "packet receipt is not a packet receipt"
    if receipt.get("status") != "READY":
        return "packet receipt is not READY (status %s)" % receipt.get("status")
    if receipt.get("allow_partial_longform"):
        return "packet receipt was written with --allow-partial-longform"
    if receipt.get("packets_sha256") != P.sha256_file(packets_path):
        return "packets changed after the packet receipt was written"
    return None


def load_publication(root: Path = ROOT, *, briefs_path=None, receipt_path=None, packets_path=None, packet_receipt_path=None,
                     queue_path=None, write_queue: bool = True, today: str | None = None) -> tuple[list[str], dict, dict]:
    """Return (errors, payloads, summary).

    ``errors`` are file-level problems (a missing, corrupt or non-PASS receipt, a briefs file that changed
    after its receipt, no packets, inputs that cannot be read): the desk then shows no v3 brief.  Otherwise
    every brief of a member still serving is returned.  A brief whose packet hash no longer matches the
    current inputs, or that cannot be checked, is returned too, carrying ``stale`` (the date the hash first
    diverged, carried over from the previous queue), and its member is written to the rewrite queue with the
    reason.  A Basis line is attached only when the packet receipt is READY and matches the packets.
    """
    root = Path(root)
    briefs_path = Path(briefs_path or root / "data/members/member_briefs_v3.json")
    receipt_path = Path(receipt_path or root / "data/members/member_briefs_v3.receipt.json")
    packets_path = Path(packets_path or root / "data/members/member_brief_packets_v3.json")
    packet_receipt_path = Path(packet_receipt_path or root / "data/members/member_brief_packets_v3.receipt.json")
    queue_path = Path(queue_path or root / "data/members/member_briefs_v3_rewrite_queue.json")
    for p in (briefs_path, receipt_path, packets_path):
        if not p.is_file():
            return ["missing %s" % p.name], {}, {}
    receipt, error = _read(receipt_path)
    if error:
        return [error], {}, {}
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("status") != "PASS":
        return ["brief receipt is not a PASS receipt"], {}, {}
    if receipt.get("briefs_sha256") != P.sha256_file(briefs_path):
        return ["briefs file changed after its receipt was written"], {}, {}
    briefs_doc, error = _read(briefs_path)
    if error:
        return [error], {}, {}
    packets_doc, error = _read(packets_path)
    if error:
        return [error], {}, {}
    if (not isinstance(briefs_doc, dict) or not isinstance(packets_doc, dict)
            or briefs_doc.get("schema") != BRIEFS_SCHEMA or packets_doc.get("schema") != P.SCHEMA):
        return ["brief or packet schema mismatch"], {}, {}
    try:
        current, problems = P.build_contents(**P.load_content_inputs(root))
    except (OSError, KeyError, ValueError) as error:
        return ["cannot recompute the packets from the current inputs: %s" % error], {}, {}
    why = P.problems_by_member(problems)
    briefs, packets = briefs_doc.get("briefs") or {}, packets_doc.get("members") or {}
    basis_reason = _basis_gate(packet_receipt_path, packets_path)
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    previous, _ = _read(queue_path) if queue_path.is_file() else ({}, None)
    previous = (previous or {}).get("members") if isinstance(previous, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    payloads, queue, reasons, departed = {}, {}, {}, []
    gone = P.departed_members()
    for name in sorted(briefs):
        packet = packets.get(name)
        if name in gone or (packet or {}).get("departed"):
            departed.append(name)
            continue
        want, now = briefs[name].get("packet_sha256"), (current.get(name) or {}).get("packet_sha256")
        if want is None:
            reason = "brief stores no packet_sha256"
        elif now is None:
            reason = "cannot rebuild the packet: %s" % why.get(name, "the member is not in the current ledger")
        elif want != now:
            reason = "evidence changed since the brief was written"
        else:
            reason = None
        stale = None
        if reason:
            before = previous.get(name) if isinstance(previous.get(name), dict) else {}
            first = before.get("first_diverged") if before.get("brief_packet_sha256") == want and before.get("first_diverged") else today
            queue[name] = {"brief_packet_sha256": want, "current_packet_sha256": now, "first_diverged": first, "reason": reason}
            reasons[name], stale = reason, {"date": first}
        source = packet or {"member": (current.get(name) or {}).get("member") or {}}
        withheld = basis_reason or (None if packet else "no packet for this member")
        payloads[name] = display_payload(name, briefs[name], source, stale=stale, basis_withheld=withheld)
    if write_queue:
        created = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        P.dump_atomic({"schema": QUEUE_SCHEMA, "created_utc": created, "briefs_sha256": receipt["briefs_sha256"],
                       "members": queue}, queue_path)
    return [], payloads, {"briefs": len(briefs), "shown": len(payloads), "stale": sorted(queue), "reasons": reasons,
                          "departed": departed, "basis_withheld": basis_reason}


def display_payload(name: str, brief: dict, packet: dict, stale: dict | None = None, basis_withheld: str | None = None) -> dict:
    """What the desk ships per member: the brief text, the pronoun, and the code-computed Basis (or why it is withheld)."""
    basis = None if basis_withheld else packet.get("basis")
    payload = {"name": name, "pronoun": (packet.get("member") or {}).get("pronoun") or "they", "animal": brief["archetype"]["animal"],
               "descriptor": brief["archetype"]["descriptor"], "bottom_line": brief["bottom_line"],
               "do": brief["do"], "dont": brief["dont"], "warning_sign": brief["warning_sign"],
               "follow_up": brief["follow_up"], "personality": brief["personality"], "speaks": brief["speaks"],
               "decides": brief["decides"], "basis": basis}
    if not basis:
        payload["basis_withheld"] = basis_withheld or "no basis was computed for this member"
    if stale:
        payload["stale"] = stale
    return payload


# ---- cli ------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--briefs", type=Path, default=BRIEFS_PATH)
    ap.add_argument("--packets", type=Path, default=P.PACKETS_PATH)
    ap.add_argument("--packet-receipt", type=Path, default=P.RECEIPT_PATH)
    ap.add_argument("--report", type=Path, help="per-member issue list (JSON)")
    ap.add_argument("--write-receipt", action="store_true")
    ap.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    ap.add_argument("--ledger", type=Path, default=P.LEDGER_PATH)
    ap.add_argument("--allow-unready-packets", action="store_true",
                    help="DRY RUNS ONLY (for example the gold exemplars against partial packets); never with --write-receipt")
    args = ap.parse_args(argv)
    if args.allow_unready_packets and args.write_receipt:
        ap.error("--allow-unready-packets cannot be combined with --write-receipt")
    for p in (args.briefs, args.packets):
        if not Path(p).is_file():
            print("FAIL CLOSED: missing %s" % p, file=sys.stderr)
            return 2
    briefs = load_briefs(args.briefs)
    packet_doc = P.load_json(args.packets)
    packets = packet_doc.get("members") or {}
    expected = {n for n, p in packets.items() if p.get("ready_for_writing")} if args.write_receipt else None
    report = validate_all(briefs, packets, expected=expected, require_ready=not args.allow_unready_packets,
                          require_binding=args.write_receipt)
    if args.report:
        P.dump_atomic(report, args.report)
    for name in report["failed"][:20]:
        print("FAIL %s" % name)
        for i in report["members"][name]["issues"][:6]:
            print("   %-22s %-16s %s" % (i["field"], i["kind"], i["detail"]))
    print("briefs %(briefs)d  passed %(passed)d  failed %(failed)d" % report["counts"])
    ok = not report["failed"]
    if args.write_receipt:
        ok = ok and not report["missing"] and not report["unexpected"]
        if report["missing"] or report["unexpected"]:
            print("roster mismatch: missing %d, unexpected %d" % (len(report["missing"]), len(report["unexpected"])), file=sys.stderr)
        preceipt = P.load_json(args.packet_receipt) if Path(args.packet_receipt).is_file() else {}
        if preceipt.get("status") != "READY" or preceipt.get("allow_partial_longform"):
            ok = False
            print("not writing receipt: packet receipt status=%s" % preceipt.get("status"), file=sys.stderr)
        if ok:
            P.dump_atomic({
                "schema": RECEIPT_SCHEMA, "status": "PASS",
                "created_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                "briefs_sha256": P.sha256_file(args.briefs), "packets_sha256": P.sha256_file(args.packets),
                "packet_receipt_sha256": P.sha256_file(args.packet_receipt),
                "ledger_sha256": P.sha256_file(args.ledger),   # provenance only; the desk binds per member, not per file
                "counts": {"briefs": len(briefs), "passed": report["counts"]["passed"], "failed": 0}}, args.receipt)
            print("wrote %s" % args.receipt)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
