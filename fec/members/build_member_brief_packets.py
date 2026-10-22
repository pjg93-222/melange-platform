# -*- coding: utf-8 -*-
"""Build one evidence packet per member for the v3 meeting briefs.

Contract: ``fec/members/member_briefs_v3_style_guide.md`` (sections 5, 8, 9, 10, 13).  The writer
(Haiku) receives exactly one packet; the validator (``validate_member_briefs_v3.py``) judges the
brief against the same packet, so every id, gate, band, plain phrase and permission below is the
single source of truth for both.

Inputs (all hashed into the receipt; any missing input fails closed, nothing falls back):
  data/members/member_narrative_evidence_v4.json   ledger: every measure, band and long-form turn
  data/members/member_desk_src.json                district text and committee
  data/members/member_wikipedia_summaries.json     biography text
  fec/legislators.csv                              gender -> pronoun, party, state, surname
  data/engine/congestion.json (+ model_bio.json)   display name -> bioguide
  --basis-counts   (required) CSV of floor-speech and hearing counts and dates
  --longform-levels (required) JSON of long-form recording count, level and status

Outputs: data/members/member_brief_packets_v3.json and its .receipt.json.  Basis and Confidence
are computed here, never by the model.
"""
from __future__ import annotations

import argparse
import collections
import copy
import csv
import datetime as dt
import hashlib
import json
import os
import re
import statistics
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:                  # run as a script, fec.roster must still import
    sys.path.insert(0, str(ROOT))

from fec import roster  # noqa: E402

SCHEMA = "member-brief-packets-v3"
RECEIPT_SCHEMA = "member-brief-packets-v3-receipt-v1"
PACKETS_PATH = ROOT / "data/members/member_brief_packets_v3.json"
RECEIPT_PATH = ROOT / "data/members/member_brief_packets_v3.receipt.json"
LEDGER_PATH = ROOT / "data/members/member_narrative_evidence_v4.json"
DESK_SRC_PATH = ROOT / "data/members/member_desk_src.json"
WIKI_PATH = ROOT / "data/members/member_wikipedia_summaries.json"
LEGISLATORS_PATH = ROOT / "fec/legislators.csv"
CONGESTION_PATH = ROOT / "data/engine/congestion.json"
MODEL_BIO_PATH = ROOT / "model_bio.json"
STYLE_GUIDE_PATH = Path(__file__).with_name("member_briefs_v3_style_guide.md")
EXEMPLARS_PATH = Path(__file__).with_name("member_briefs_v3_exemplars.json")
BRIEFS_PATH = ROOT / "data/members/member_briefs_v3.json"

# ---------------------------------------------------------------------------------------------
# roster (fec/roster.py is the single source: data/engine/roster.json, written by the roster refresh)
# ---------------------------------------------------------------------------------------------
# A departed member keeps a packet but is never ready for writing and never shown a v3 brief; roster.departed() names them.
# The roster's arrivals get packets like everyone else, so nothing is deferred any more (kept as an empty tuple for the receipt).
DEFERRED_NEW_MEMBERS = ()

# ---------------------------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------------------------
FFFD = "\ufffd"
# Explicit aliases for OLDER artifacts that still carry a corrupted ledger key.  Never fuzzy.
KEY_ALIASES = {"Pablo Hern" + FFFD + "ndez Rivera": "Pablo Hern\u00e1ndez Rivera"}


def canon_key(key: str) -> str:
    """NFC-normalize a member key; map known corrupted keys explicitly; refuse any other U+FFFD."""
    key = unicodedata.normalize("NFC", str(key))
    key = KEY_ALIASES.get(key, key)
    if FFFD in key:
        raise ValueError("member key contains U+FFFD and has no explicit alias: %r" % key)
    return key


def assert_clean_keys(keys) -> None:
    bad = [k for k in keys if FFFD in k]
    if bad:
        raise ValueError("U+FFFD in member key(s): %s" % ", ".join(repr(k) for k in bad))


def departed_members() -> dict:
    """{member: "departed <date>"} from roster.departed(); a missing or malformed roster artifact raises (fail closed)."""
    return {canon_key(row["member"]): ("departed %s" % row["departed_on"]) if row.get("departed_on") else "departed"
            for row in roster.departed()}


def canon_map(mapping: dict) -> dict:
    out = {}
    for key, value in mapping.items():
        ck = canon_key(key)
        if ck in out:
            raise ValueError("two keys collapse to one member key: %r" % ck)
        out[ck] = value
    return out


# ---------------------------------------------------------------------------------------------
# io
# ---------------------------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def dump_atomic(document, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    os.replace(temporary, path)


# The per-member binding.  A brief stores the packet_sha256 of the packet its writer saw; the desk
# recomputes it from the current inputs and flags (never hides) a brief whose evidence moved.  The
# hash holds this member's ids, band labels, directions, strengths, plain text and gates, and nothing
# derived from other members: no percentiles (they shift whenever anyone else's data does) and no
# setting-contrast verdict (its threshold is a standard deviation over all members), only this
# member's own contrast deltas.  Corpus hooks, readiness and the computed Basis are not hashed.
def _hash_item(item: dict) -> dict:
    if item["id"].startswith("SC."):
        return {k: item.get(k) for k in ("id", "first_clips", "second_clips", "delta")}
    return {k: item.get(k) for k in ("id", "band", "direction", "strength", "neutral", "plain", "claim")}


def hash_view(packet: dict) -> dict:
    appeals = packet.get("appeals")
    pick = lambda rows, keys: [{k: row.get(k) for k in keys} for row in rows or []]
    return {
        "member": packet.get("member"), "evidence_tier": packet.get("evidence_tier"), "gates": packet.get("gates"),
        "evidence": [_hash_item(i) for i in packet.get("evidence") or []],
        "appeals": {k: appeals.get(k) for k in ("dims", "components", "plain", "lead_ok")} if appeals else None,
        "public_sensibilities": pick(packet.get("public_sensibilities"), ("id", "category", "claim", "date_text", "repeated_story")),
        "longform": pick(packet.get("longform"), ("id", "status", "era", "finding", "meeting_implication", "source_text")),
        "archetype_candidates": pick(packet.get("archetype_candidates"), ("animal", "matched", "slots", "contradicted", "disqualified")),
        "suggested_animal": packet.get("suggested_animal"), "warning_form": packet.get("warning_form"),
        # the registry grows between batches, so what it contributes (value, registry_match) is not what the writer's evidence says
        "coin_recommended": {k: v for k, v in (packet.get("coin_recommended") or {}).items() if k not in ("value", "registry_match")},
    }


def content_sha256(packet: dict) -> str:
    body = json.dumps(hash_view(packet), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def input_paths(root: Path = ROOT) -> dict:
    """Where the packet content inputs live under a root (the same files the builder reads)."""
    root = Path(root)
    return {"ledger": root / LEDGER_PATH.relative_to(ROOT), "desk_src": root / DESK_SRC_PATH.relative_to(ROOT),
            "wikipedia": root / WIKI_PATH.relative_to(ROOT), "legislators": root / LEGISLATORS_PATH.relative_to(ROOT),
            "congestion": root / CONGESTION_PATH.relative_to(ROOT), "model_bio": root / MODEL_BIO_PATH.relative_to(ROOT)}


def load_content_inputs(root: Path = ROOT) -> dict:
    """Everything build_contents needs, read from a root; a missing file is an error, never an empty default."""
    paths = input_paths(root)
    for key, path in paths.items():
        if key != "model_bio" and not path.is_file():
            raise FileNotFoundError("missing %s: %s" % (key, path))
    with paths["legislators"].open(encoding="utf-8-sig", newline="") as handle:
        legislators = list(csv.DictReader(handle))
    return {"ledger_doc": load_json(paths["ledger"]), "legislators": legislators, "congestion": load_json(paths["congestion"]),
            "model_bio": load_json(paths["model_bio"]) if paths["model_bio"].is_file() else {},
            "desk_src": load_json(paths["desk_src"]), "wiki": load_json(paths["wikipedia"]).get("members") or {}}


# ---------------------------------------------------------------------------------------------
# bands, strengths, permissions
# ---------------------------------------------------------------------------------------------
LEVELS = {
    "among the highest in the chamber": 3, "well above most": 2, "above most": 1, "middling": 0,
    "below most": -1, "well below most": -2, "among the lowest in the chamber": -3,
}
STRENGTH = {3: "outer", 2: "clear", 1: "mild"}


def level_of(label) -> int:
    if label not in LEVELS:
        raise ValueError("unknown band label %r" % (label,))
    return LEVELS[label]


FIELDS = ("archetype", "bottom_line", "do", "dont", "warning_sign", "follow_up", "personality",
          "speaks", "decides")
_F = set(FIELDS)
# Style guide 9.2.  A field name here means "may be cited"; conditions (non-TR id alongside,
# IX/BH/DF alongside for audio) are enforced by the validator.
PERMISSION = {
    "TR": _F - {"follow_up", "speaks"},
    "TR.N": {"archetype", "personality"},
    "QR.antagonism": {"archetype", "bottom_line", "do", "dont", "warning_sign", "personality", "speaks"},
    "QR": {"archetype", "personality", "speaks"},
    "BH.coop": {"archetype", "bottom_line", "do", "dont", "follow_up", "personality", "decides"},
    "BH.breadth": {"archetype", "personality", "decides"},          # description only: no advice about how wide to make an ask (B8)
    "BH.diligence": {"archetype", "bottom_line", "do", "dont", "follow_up", "personality", "decides"},
    "BH.conv": {"archetype", "bottom_line", "do", "dont", "warning_sign", "personality", "speaks", "decides"},
    "FC": {"archetype", "do", "dont", "personality", "speaks"},
    "AU": {"archetype", "do", "dont", "warning_sign", "speaks"},
    "DF": set(_F),
    "MA": {"archetype", "bottom_line", "do", "dont", "warning_sign", "follow_up", "decides"},
    "HA": {"archetype", "bottom_line", "do", "dont", "warning_sign", "personality", "speaks", "decides"},
    "VI": {"archetype", "do", "dont", "personality", "decides"},
    "RC": {"bottom_line", "do", "dont", "follow_up", "decides"},
    "PS": {"archetype", "do", "dont", "follow_up", "personality", "speaks", "decides"},
    "LD": {"personality", "decides"},
    "BIO": {"personality", "decides"},
    "LF.qualified": {"archetype", "bottom_line", "do", "dont", "warning_sign", "follow_up", "speaks", "decides"},
    "LF.provisional": {"speaks"},
    "PROC": {"do", "dont", "warning_sign", "follow_up"},
}


def permission_for(item_id: str) -> set[str]:
    if item_id == "TR.N":
        return PERMISSION["TR.N"]
    if item_id.startswith("TR."):
        return PERMISSION["TR"]
    if item_id == "QR.antagonism":
        return PERMISSION["QR.antagonism"]
    if item_id.startswith("QR."):
        return PERMISSION["QR"]
    if item_id in ("BH.coop", "BH.breadth", "BH.diligence"):
        return PERMISSION[item_id]
    if item_id in ("BH.dominance", "BH.wpt") or item_id.startswith("IX."):
        return PERMISSION["BH.conv"]
    for prefix in ("FC", "AU", "VI", "PS", "BIO"):
        if item_id.startswith(prefix + "."):
            return PERMISSION[prefix]
    if item_id.startswith("SC."):
        return PERMISSION["AU"]
    if item_id in ("DF", "MA", "HA", "RC", "LD", "PROC"):
        return PERMISSION[item_id]
    raise ValueError("no permission row for id %r" % item_id)


# ---------------------------------------------------------------------------------------------
# plain phrases (style guide 5.1 to 5.9).  Templates are full clauses; markers: {Subj} {subj}
# {Poss} {poss} {obj} {is} {s} {has} {ch}.
# ---------------------------------------------------------------------------------------------
PRONOUNS = {
    "he": dict(Subj="He", subj="he", Poss="His", poss="his", obj="him", s="s", has="has"),
    "she": dict(Subj="She", subj="she", Poss="Her", poss="her", obj="her", s="s", has="has"),
    "they": dict(Subj="They", subj="they", Poss="Their", poss="their", obj="them", s="", has="have"),
}
PRONOUNS["he"]["is"] = PRONOUNS["she"]["is"] = "is"
PRONOUNS["they"]["is"] = "are"


def _scale(pre, hi, lo, post="", ex_tail="than almost anyone in the {ch}", tail="than most"):
    def one(word):
        return {
            3: "%s %s %s %s" % (pre, word, post, ex_tail),
            2: "%s much %s %s %s" % (pre, word, post, tail),
            1: "%s somewhat %s %s %s" % (pre, word, post, tail),
        }
    out = {}
    for lvl, text in one(hi).items():
        out[lvl] = text
    for lvl, text in one(lo).items():
        out[-lvl] = text
    return {k: re.sub(r"\s+", " ", v).strip() for k, v in out.items()}


def _person(adj, opposite=None, post=""):
    tail = (" " + post) if post else ""
    out = {
        3: "{Subj} {is} one of the most %s members%s in the {ch}" % (adj, tail),
        2: "{Subj} {is} very %s%s" % (adj, tail),
        1: "{Subj} {is} fairly %s%s" % (adj, tail),
    }
    if opposite:
        out[-1] = "{Subj} {is} a little %s%s" % (opposite, tail)
        out[-2] = "{Subj} {is} quite %s%s" % (opposite, tail)
        out[-3] = "{Subj} {is} one of the most %s members%s in the {ch}" % (opposite, tail)
    else:
        out[-1] = "{Subj} {is} somewhat less %s%s than most" % (adj, tail)
        out[-2] = "{Subj} {is} much less %s%s than most" % (adj, tail)
        out[-3] = "{Subj} {is} one of the least %s members%s in the {ch}" % (adj, tail)
    return out


WORDS_EX = "than almost anyone's in the {ch}"
# id -> (label, scope, {level: template})
PHRASES = {
    "TR.E": ("outgoing versus reserved: energetic in a room, takes up space versus quiet, keeps to self",
             "broad public read of character; not observed behavior", _person("outgoing", "reserved")),
    "TR.O": ("curious, open to new ideas versus practical, prefers familiar ground",
             "broad public read of character; not observed behavior", _person("curious", "practical")),
    "TR.C": ("careful, organized, precise versus loose with details",
             "broad public read of character; not observed behavior",
             {3: "{Subj} {is} one of the most careful and organized members in the {ch}",
              2: "{Subj} {is} very careful and organized", 1: "{Subj} {is} fairly careful and organized",
              -1: "{Subj} {is} somewhat less careful with details than most",
              -2: "{Subj} {is} much looser with details than most",
              -3: "{Subj} {is} looser with details than almost anyone in the {ch}"}),
    "TR.A": ("agreeable, easy to work with versus skeptical by default, hard to win over",
             "broad public read of character; not observed behavior", _person("agreeable", "skeptical")),
    "TR.N": ("gets worked up easily versus hard to fluster",
             "broad public read of character; personality paragraph only; never supports advice",
             {3: "{Subj} {is} one of the members of the {ch} who gets worked up most easily",
              2: "{Subj} {is} quick to get worked up, much more than most",
              1: "{Subj} gets worked up somewhat more easily than most",
              -1: "{Subj} {is} somewhat harder to fluster than most",
              -2: "{Subj} {is} much harder to fluster than most",
              -3: "{Subj} {is} one of the hardest members of the {ch} to fluster"}),
    "QR.breadth": ("talks about a wide versus narrow range of issues", "how the member's own public words read",
                   _scale("{Subj} talk{s} about a", "wider", "narrower", "range of issues")),
    "QR.diligence": ("talks about the work in careful, detailed terms (or not)",
                     "how the member's own public words read",
                     _scale("{Subj} talk{s} about {poss} work in", "more careful, detailed", "less careful, detailed", "terms")),
    "QR.antagonism": ("gentle, mild words versus sharp words", "how the member's own public words read",
                      _scale("{Poss} words are", "gentler", "sharper", ex_tail=WORDS_EX)),
    "QR.affect": ("words carry a lot of versus little emotional charge", "how the member's own public words read",
                  _scale("{Poss} words carry", "more", "less", "emotional charge", ex_tail=WORDS_EX)),
    "BH.coop": ("works with the other party more or less than most", "share of cross-party ties and joined bills; not friendliness, compromise or a vote",
                _scale("{Subj} work{s} with the other party", "more", "less", ex_tail="than almost anyone in the {ch}")),
    "BH.breadth": ("bills cover many versus few issues", "number of issue areas in the member's bills; not curiosity",
                   _scale("{Poss} bills cover", "more", "fewer", "issues", ex_tail=WORDS_EX)),
    "BH.diligence": ("record of voting and finishing bills", "roll-call participation and bill follow-through; not work ethic",
                     _person("reliable", None, "at voting and seeing bills through")),
    "BH.dominance": ("takes charge of a conversation and holds the floor", "conversation role; not energy or leadership",
                     {3: "{Subj} take{s} charge of a conversation and hold{s} the floor as firmly as almost anyone in the {ch}",
                      2: "{Subj} take{s} charge of a conversation and hold{s} the floor, much more than most",
                      1: "{Subj} tend{s} to take charge of a conversation, somewhat more than most",
                      -1: "{Subj} take{s} charge of a conversation somewhat less than most",
                      -2: "{Subj} take{s} charge of a conversation much less than most",
                      -3: "{Subj} {is} one of the members of the {ch} least likely to take charge of a conversation"}),
    "BH.wpt": ("answers run short or long", "answer length in conversations; not enthusiasm",
               _scale("{Subj} give{s}", "longer", "shorter", "answers")),
    "FC.warmth": ("warm versus cool words", "public-language style; not a broad trait",
                  _scale("{Poss} words are", "warmer", "cooler", ex_tail=WORDS_EX)),
    "FC.excitement": ("upbeat, energetic versus flat words", "public-language style; not a broad trait",
                      _scale("{Poss} words are", "more upbeat", "flatter", ex_tail=WORDS_EX)),
    "FC.activity": ("action-heavy versus action-light words", "public-language style; not a broad trait",
                    _scale("{Poss} words are", "more action-focused", "less action-focused", ex_tail=WORDS_EX)),
    "FC.gregariousness": ("social versus solitary words", "public-language style; not a broad trait",
                          _scale("{Poss} words are", "more social", "less social", ex_tail=WORDS_EX)),
    "FC.assertiveness": ("forceful versus mild word choice", "public-language style; not a broad trait",
                         _scale("{Poss} words are", "more forceful", "less forceful", ex_tail=WORDS_EX)),
    "IX.wpt_mean": ("average answer length in interviews", "observed interview turn structure",
                    _scale("{Subj} give{s}", "longer", "shorter", "answers in interviews")),
    "IX.wpt_p90": ("how long the longest answers run", "observed interview turn structure",
                   _scale("{Poss} longest answers run", "longer", "shorter", ex_tail=WORDS_EX)),
    "IX.gini": ("answer length varies a lot or stays even", "observed interview turn structure",
                _scale("{Poss} answer length", "varies more", "stays more even", ex_tail=WORDS_EX)),
    "IX.cutoff": ("trails off or gets cut off", "observed transcript ending pattern; not interrupting",
                  _scale("{Subj} trail{s} off or leave{s} sentences unfinished", "more", "less")),
    "IX.burst": ("answers in quick runs of short replies", "runs of short turns; not arguing or interrupting",
                 _scale("{Subj} answer{s} in quick runs of short replies", "more", "less")),
    "IX.defense": ("defends position when challenged", "repeated explicit floor-defense language; not a private state",
                   {3: "{Subj} defend{s} {poss} position when challenged, harder than almost anyone in the {ch}",
                    2: "{Subj} defend{s} {poss} position when challenged, much more than most",
                    1: "{Subj} defend{s} {poss} position when challenged, somewhat more than most"}),
    "IX.discmark": ("uses a lot of or almost no filler", "filler and discourse markers; not nervousness",
                    {3: "{Subj} use{s} more filler words than almost anyone in the {ch}",
                     2: "{Subj} use{s} much more filler than most", 1: "{Subj} use{s} somewhat more filler than most",
                     -1: "{Subj} use{s} somewhat less filler than most", -2: "{Subj} use{s} much less filler than most",
                     -3: "{Subj} use{s} almost no filler, less than nearly anyone in the {ch}"}),
    "AU.pace": ("talks fast or slowly", "descriptive delivery only; not words per minute",
                _scale("{Subj} talk{s}", "faster", "more slowly", ex_tail="than almost anyone in the {ch}")),
    "AU.pause": ("long or short pauses between phrases", "descriptive delivery only; not hesitation",
                 _scale("{Subj} pause{s} for", "longer", "shorter", "stretches between phrases")),
    "AU.volume": ("volume rises and falls a lot or stays even", "descriptive delivery only; variability, not loudness",
                  _scale("{Poss} volume", "rises and falls more", "stays more even", ex_tail=WORDS_EX)),
    "AU.pitch": ("pitch rises and falls a lot or stays flat", "descriptive delivery only; not emotion",
                 _scale("{Poss} pitch", "rises and falls more", "stays flatter", ex_tail=WORDS_EX)),
    "HA": ("combative or mild in hearings", "hearing behavior only; scope advice to formal or adversarial settings",
           {3: "{Subj} {is} one of the most combative members of the {ch} in hearings",
            2: "{Subj} {is} much more combative in hearings than most members",
            1: "{Subj} {is} somewhat more combative in hearings than most members",
            -1: "{Subj} {is} somewhat less combative in hearings than most members",
            -2: "{Subj} {is} much less combative in hearings than most members",
            -3: "{Subj} {is} one of the least combative members of the {ch} in hearings"}),
    "VI.observed": ("how visible the member is", "exposure; not attention-seeking", _person("visible")),
    "VI.role": ("how visible the member is for the role", "exposure relative to role; not attention-seeking",
                _person("visible", None, "for {poss} role")),
}
# DF exists only for repeated defense across programs, so it has high levels only.
PHRASES["DF"] = (PHRASES["IX.defense"][0], PHRASES["IX.defense"][1],
                 {3: "{Subj} defend{s} {poss} position when challenged, harder than almost anyone, across many different shows",
                  2: "{Subj} defend{s} {poss} position when challenged, across different shows",
                  1: "{Subj} defend{s} {poss} position when challenged, across shows"})


def render(template: str, pronoun: str, chamber: str, **extra) -> str:
    ctx = dict(PRONOUNS[pronoun])
    ctx["ch"] = chamber
    ctx.update(extra)
    return template.format(**ctx)


def band_item(item_id, band, pronoun, chamber, *, scope_extra=None):
    """One citable evidence item from a ledger band {pct,label,neutral}."""
    label, scope, table = PHRASES[item_id]
    lbl = band["label"]
    lvl = level_of(lbl)
    neutral = lvl == 0 or band.get("neutral") is True
    item = {
        "id": item_id, "label": label, "band": lbl, "pct": band.get("pct"),
        "direction": None if neutral else ("high" if lvl > 0 else "low"),
        "strength": None if neutral else STRENGTH[abs(lvl)],
        "neutral": bool(neutral), "plain": None,
        "scope": scope_extra or scope,
        "may_support": [] if neutral else sorted(permission_for(item_id)),
    }
    if not neutral:
        template = table.get(lvl)
        if template is None:
            raise ValueError("%s has no phrase for level %d" % (item_id, lvl))
        item["plain"] = render(template, pronoun, chamber)
    return item


# ---------------------------------------------------------------------------------------------
# appeals, receptivity, setting contrasts, sensibilities
# ---------------------------------------------------------------------------------------------
APPEAL_FRAMES = (  # frame prefix written by make_member_desk._approach -> normalized dimension
    ("Partnership framing", "partnership"), ("Lead with outcomes", "outcome"), ("Direct style", "outcome"),
    ("Relationship-first", "community"), ("Warmth-forward", "community"), ("Technical brief", "technical"),
    ("Mixed interpersonal style", "mixed"),
)
APPEAL_SUPPORT = {  # ledger supported_dimensions text -> dimension
    "assertive, outcome-led, or immediate framing": "outcome",
    "communicative, community, or human-impact framing": "community",
    "technical, factual, and written-evidence framing": "technical",
}
APPEAL_PLAIN = {
    "outcome": "results and facts fit best: a short, direct case about what the policy delivers",
    "community": "people and community impact fit best: stories about who is helped",
    "technical": "written, technical evidence shared ahead of time fits best",
    "partnership": "a visible shared role fits: something to lead or announce together",
}


APPEAL_AXIS_FLOOR = 0.42   # below this percentile an axis is "below most" or lower (ledger band cut) and is never led with
MIXED_PLAIN = "no single frame leads; give no framing advice"


def normalize_appeals(appeals: dict | None) -> dict | None:
    """Collapse the desk's approach sentence to one dimension; refuse an unknown or inconsistent one.

    "Mixed" is the desk's catch-all (neither axis leads): it keeps the shape but gives no advice and no lead_ok.
    lead_ok says which axis the writer may lead with: results needs agency, people needs communion, each at or
    above the "middling" cut."""
    appeals = appeals or {}
    frame = appeals.get("assertive_vs_communicative_frame")
    if not frame:
        return None
    dim = next((d for prefix, d in APPEAL_FRAMES if str(frame).startswith(prefix)), None)
    if dim is None:
        raise ValueError("unrecognized appeal frame: %r" % str(frame)[:60])
    supported = {APPEAL_SUPPORT[s] for s in appeals.get("supported_dimensions") or []}
    expect = {"partnership": {"outcome", "community"}, "mixed": set()}.get(dim, {dim})
    if supported != expect:
        raise ValueError("appeal frame %r disagrees with supported dimensions %r" % (dim, sorted(supported)))
    agency, communion = appeals.get("agency_percentile"), appeals.get("communion_percentile")
    ok = lambda v: v is None or v >= APPEAL_AXIS_FLOOR
    lead_ok = {"results": False, "people": False} if dim == "mixed" else {"results": ok(agency), "people": ok(communion)}
    return {"dims": [dim], "components": sorted(supported), "plain": MIXED_PLAIN if dim == "mixed" else APPEAL_PLAIN[dim],
            "agency_percentile": agency, "communion_percentile": communion, "lead_ok": lead_ok,
            "scope": "demonstrated framing fit, not a claim about private motivation"}


RC_PHRASE = {"mild": "outside pressure moves {obj} a little {d} than it moves most members",
             "clear": "outside pressure moves {obj} {d} than it moves most members",
             "outer": "outside pressure moves {obj} clearly {d} than it moves most members"}


def receptivity_item(recep: dict | None, pronoun: str):
    """Neutral only when the ledger says 'about the same'; strength follows the stated percentage."""
    note = str((recep or {}).get("pressure_note") or "").casefold()
    direction = "less" if "less than" in note else ("more" if "more than" in note else None)
    magnitude = re.search(r"(\d+)%", note)
    pct = int(magnitude.group(1)) if magnitude else 0
    strength = None if direction is None else ("mild" if pct < 10 else ("clear" if pct < 25 else "outer"))
    return {
        "id": "RC", "label": "how outside pressure lands",
        "band": "about the same" if direction is None else direction + " than most",
        "direction": direction, "strength": strength, "neutral": direction is None,
        "plain": None if direction is None else RC_PHRASE[strength].format(d=direction, **PRONOUNS[pronoun]),
        "scope": "covered responsiveness estimate; a tilt, not a guarantee or instruction to abandon an ask",
        "may_support": [] if direction is None else sorted(permission_for("RC")),
    }


# Setting-contrast threshold.  The guide asks for ``meaningful`` but gives no number; this is the
# builder's rule: both settings have at least MIN_CLIPS clips, neither is the House floor (floor
# and interview delivery are never compared, guide 5.6), and one channel moved by at least
# SD_MULT population standard deviations of that setting pair's delta.
SC_MIN_CLIPS = 5
SC_SD_MULT = 1.0
SC_MIN_PAIR_MEMBERS = 30
SC_CHANNELS = {  # channel -> (first setting higher, first setting lower)
    "pace": ("{Subj} talk{s} noticeably faster in {a} than in {b}", "{Subj} talk{s} noticeably more slowly in {a} than in {b}"),
    "pause": ("{Subj} pause{s} for noticeably longer stretches in {a} than in {b}",
              "{Subj} pause{s} for noticeably shorter stretches in {a} than in {b}"),
    "volume_dyn": ("{Poss} volume varies noticeably more in {a} than in {b}", "{Poss} volume varies noticeably less in {a} than in {b}"),
    "pitch_var": ("{Poss} pitch varies noticeably more in {a} than in {b}", "{Poss} pitch varies noticeably less in {a} than in {b}"),
}
SETTING_WORDS = {"house_floor": "the House floor", "npr": "NPR interviews", "sunday_show": "Sunday shows",
                 "washington_journal": "Washington Journal", "longform_podcast": "long-form podcasts"}


def setting_word(key: str) -> str:
    return SETTING_WORDS.get(key, key.replace("_", " "))


def contrast_stats(ledger_members: dict) -> dict:
    deltas = collections.defaultdict(list)
    for rec in ledger_members.values():
        for c in (rec.get("speech_profile") or {}).get("setting_contrasts") or []:
            for ch in SC_CHANNELS:
                v = (c.get("delta_first_minus_second") or {}).get(ch)
                if v is not None:
                    deltas[(c["first"], c["second"], ch)].append(float(v))
    return {k: (statistics.pstdev(v), len(v)) for k, v in deltas.items() if len(v) >= SC_MIN_PAIR_MEMBERS}


def contrast_items(rec: dict, stats: dict, pronoun: str):
    items = []
    for c in (rec.get("speech_profile") or {}).get("setting_contrasts") or []:
        first, second = c["first"], c["second"]
        best = None
        if (min(c.get("first_clips") or 0, c.get("second_clips") or 0) >= SC_MIN_CLIPS
                and "house_floor" not in (first, second)):
            for ch, words in SC_CHANNELS.items():
                v = (c.get("delta_first_minus_second") or {}).get(ch)
                sd = stats.get((first, second, ch))
                if v is None or not sd or sd[0] <= 0:
                    continue
                z = abs(float(v)) / sd[0]
                if z >= SC_SD_MULT and (best is None or z > best[0]):
                    best = (z, ch, float(v) > 0)
        meaningful = best is not None
        item = {"id": "SC.%s_%s" % (first, second),
                "label": "delivery differs between %s and %s" % (setting_word(first), setting_word(second)),
                "band": None, "direction": None, "strength": None, "neutral": not meaningful,
                "meaningful": meaningful, "plain": None,
                "first_clips": c.get("first_clips"), "second_clips": c.get("second_clips"),
                "delta": {ch: round(float(v), 4) for ch in SC_CHANNELS
                          if (v := (c.get("delta_first_minus_second") or {}).get(ch)) is not None},
                "scope": "descriptive association between settings; never a cause",
                "may_support": sorted(permission_for("SC.x")) if meaningful else []}
        if meaningful:
            _, ch, first_higher = best
            item["plain"] = render(SC_CHANNELS[ch][0 if first_higher else 1], pronoun, "",
                                   a=setting_word(first), b=setting_word(second))
        items.append(item)
    return items


def sensibility_items(rec: dict):
    items = []
    for n, s in enumerate((rec.get("speech_profile") or {}).get("publicly_expressed_sensibilities") or [], 1):
        items.append({"id": "PS.%d" % n, "category": s.get("category"), "claim": s.get("claim"),
                      "date_text": s.get("date"), "source": s.get("source"),
                      "scope": "publicly stated; never a private belief or motive",
                      "may_support": sorted(permission_for("PS.1"))})
    words = [set(w for w in re.findall(r"[a-z]{4,}", str(i["claim"]).casefold()) if w not in _STOP) for i in items]
    for a in range(len(items)):
        items[a]["repeated_story"] = False
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            shared = words[a] & words[b]
            union = words[a] | words[b]
            if items[a]["category"] == items[b]["category"] and union and (
                    len(shared) >= 4 or len(shared) / len(union) >= 0.35):
                items[a]["repeated_story"] = items[b]["repeated_story"] = True
    return items


_STOP = set("that with from this they their were have been which when what into about after before while "
            "also each other than then them these those there where would could should only over under".split())

# ---------------------------------------------------------------------------------------------
# leadership titles (style guide 4) and the warning form (style guide 7)
# ---------------------------------------------------------------------------------------------
LD_PLAIN = {  # LD.claim -> LD.plain; <pron> is the member's possessive pronoun
    "House Democratic Leader": "<pron> party's House leader",
    "House Assistant Democratic Leader": "<pron> party's assistant leader in the House",
    "House Democratic Whip": "<pron> party's House whip",
    "House Democratic Caucus Chair": "chair of <pron> party's House caucus",
    "House Democratic Caucus Vice Chair": "vice chair of <pron> party's House caucus",
    "House Republican Conference Chair": "chair of <pron> party's House conference",
    "House Republican Policy Committee Chair": "chair of <pron> party's House policy committee",
    "National Republican Senatorial Committee Chair": "chair of <pron> party's Senate campaign committee",
    "Senate Democratic Leader": "<pron> party's Senate leader",
    "Senate Democratic Whip": "<pron> party's Senate whip",
    "Senate Democratic Conference Vice Chair": "vice chair of <pron> party's Senate conference",
    "Senate Democratic Conference Secretary": "secretary of <pron> party's Senate conference",
    "Senate Democratic Deputy Conference Secretary": "deputy secretary of <pron> party's Senate conference",
    "Senate Democratic Conference leadership": "a member of <pron> party's Senate leadership",
    "Senate Democratic Steering and Policy Committee Chair": "chair of <pron> party's Senate steering and policy committee",
    "Senate Democratic Strategic Communications Committee Chair": "chair of <pron> party's Senate communications committee",
    "Senate Democratic Outreach Chair": "chair of <pron> party's Senate outreach effort",
    "Senate Democratic Outreach Vice Chair": "vice chair of <pron> party's Senate outreach effort",
    "Senate Republican Conference Chair": "chair of <pron> party's Senate conference",
    "Senate Republican Conference Vice Chair": "vice chair of <pron> party's Senate conference",
    "Senate Republican Policy Committee Chair": "chair of <pron> party's Senate policy committee",
}
PARTY_WORD = re.compile(r"\b(?:Democrat\w*|Republican\w*|GOP)\b")


def ld_plain(claim: str, pronoun: str) -> str:
    """The party-free form of a leadership title.  A title without a party word is used as given; one with a party word and
    no row in LD_PLAIN fails the packet build until a row is added."""
    if claim in LD_PLAIN:
        return LD_PLAIN[claim].replace("<pron>", PRONOUNS[pronoun]["poss"])
    if PARTY_WORD.search(claim):
        raise ValueError("leadership title %r has a party word and no LD.plain row" % claim)
    return claim


CUE_BASE = ("BH.wpt", "BH.dominance", "QR.antagonism", "HA")


def cue_eligible_ids(evidence, gates, longform=()) -> set:
    """Items a Warning sign cue may rest on: DF (gate true), any non-neutral IX.*, BH.wpt, BH.dominance, QR.antagonism, HA,
    and a qualified long-form finding."""
    ids = {i["id"] for i in evidence if not i.get("neutral") and (i["id"] in CUE_BASE or i["id"].startswith("IX."))}
    if (gates or {}).get("DF"):
        ids.add("DF")
    return ids | {l["id"] for l in longform or [] if l.get("status") == "qualified" and not era_restricted(l.get("era"))}


# ---------------------------------------------------------------------------------------------
# archetypes (style guide 8)
# ---------------------------------------------------------------------------------------------
TEMPLATE_ANIMALS = ("Bull", "Hummingbird", "Terrier", "Orca", "Tortoise", "Lone Wolf", "Blue Jay",
                    "Owl", "Butterfly", "Bear")
ARCHETYPES = {
    "Bull": {"descriptor": "dominant and skeptical", "slots": [
        [("BH.dominance", "hi"), ("FC.assertiveness", "hi")],
        [("TR.A", "lo"), ("QR.antagonism", "lo")],
        [("TR.C", "hi"), ("BH.diligence", "hi"), ("QR.diligence", "hi")],
        [("DF", "present")],
        [("BH.wpt", "lo"), ("IX.wpt_mean", "lo"), ("IX.discmark", "lo")],
        [("MA", "outcome"), ("MA", "technical")]], "contra": []},
    "Hummingbird": {"descriptor": "warm and restless", "slots": [
        [("FC.warmth", "hi")], [("MA", "community")],
        [("TR.E", "hi"), ("FC.excitement", "hi"), ("FC.gregariousness", "hi")],
        [("TR.N", "hi"), ("QR.affect", "hi")],
        [("IX.burst", "hi"), ("IX.cutoff", "hi"), ("AU.pace", "hi")]],
        "contra": [[("FC.warmth", "LO")], [("TR.A", "LO")]]},
    "Terrier": {"descriptor": "eager and fiercely partisan", "slots": [
        [("TR.E", "hi")], [("TR.C", "lo"), ("BH.diligence", "lo")],
        [("IX.burst", "hi"), ("BH.dominance", "hi")], [("MA", "outcome")], [("BH.coop", "lo")],
        [("FC.excitement", "hi"), ("FC.activity", "hi")]],
        "contra": [[("TR.E", "LO")], [("BH.coop", "HI")]]},
    "Orca": {"descriptor": "charismatic and visionary", "slots": [
        [("TR.E", "hi")], [("VI.observed", "hi"), ("VI.role", "hi")], [("TR.O", "hi")],
        [("TR.C", "lo"), ("BH.diligence", "lo")], [("FC.excitement", "hi")], [("MA", "partnership")]],
        "contra": [[("TR.E", "LO")], [("VI.observed", "LO")]]},
    "Tortoise": {"descriptor": "patient and guarded", "required": [1], "slots": [
        [("TR.E", "lo")], [("TR.C", "hi"), ("BH.diligence", "hi"), ("QR.diligence", "hi")],
        [("BH.dominance", "lo"), ("BH.wpt", "lo"), ("IX.wpt_mean", "lo")],
        [("AU.pace", "lo"), ("AU.pause", "hi")],
        [("VI.observed", "lo"), ("VI.role", "lo"), ("HA", "lo")]],   # one low-profile slot (guide 8.1)
        "contra": [[("TR.E", "hi")], [("BH.dominance", "HI")]]},
    "Lone Wolf": {"descriptor": "principled and independent", "slots": [
        [("TR.A", "lo")], [("RC", "less")], [("PS", "formative|inspiration")],
        [("IX.wpt_mean", "hi"), ("IX.wpt_p90", "hi")], [("QR.antagonism", "lo"), ("HA", "hi")],
        [("FC.gregariousness", "lo")]], "contra": [[("TR.A", "HI")], [("BH.coop", "HI")], [("LD", "present")]]},
    "Blue Jay": {"descriptor": "loud, curious, and committed", "slots": [
        [("TR.E", "hi")], [("TR.O", "hi")], [("TR.A", "lo"), ("QR.antagonism", "lo"), ("HA", "hi")],
        [("FC.assertiveness", "hi"), ("FC.excitement", "hi")], [("BH.dominance", "hi"), ("DF", "present")],
        [("TR.N", "hi"), ("QR.affect", "hi")]], "contra": [[("TR.E", "LO")], [("TR.O", "LO")]]},
    "Owl": {"descriptor": "scholarly and ambitious", "slots": [
        [("TR.O", "hi")], [("TR.C", "hi"), ("QR.diligence", "hi")],
        [("IX.wpt_mean", "hi"), ("IX.wpt_p90", "hi"), ("BH.wpt", "hi")],
        [("BH.breadth", "hi"), ("QR.breadth", "hi")], [("MA", "technical")], [("VI.role", "hi")]],
        "contra": [[("TR.O", "LO")]]},
    "Butterfly": {"descriptor": "social and hard to pin down", "slots": [
        [("FC.gregariousness", "hi"), ("FC.warmth", "hi")], [("TR.E", "hi")],
        [("TR.C", "lo"), ("BH.diligence", "lo")], [("BH.coop", "hi")], [("VI.observed", "hi")]],
        "contra": [[("TR.E", "LO")], [("TR.C", "HI"), ("BH.diligence", "hi")]]},
    "Bear": {"descriptor": "warm and fiercely protective", "slots": [
        [("MA", "community")], [("FC.warmth", "hi")],
        [("QR.antagonism", "lo"), ("HA", "hi"), ("TR.A", "lo")], [("PS", "family|formative|district")],
        [("TR.N", "hi"), ("QR.affect", "hi")], [("AU.volume", "hi")]], "contra": [[("FC.warmth", "LO")]]},
}
COIN_THRESHOLD = {"rich": 3, "standard": 3, "thin": 2}
# Step 5 themes (guide 8.2): outer-band items that point the same way.  A theme is unexplained when two or more of
# its items sit at +3 or -3 in the theme's direction and none of those is a holding alternative in the chosen
# animal's matched slots.  An unexplained "behavior" theme is binding (coin); a "range" theme, or fewer than two
# items, is advisory only.
THEMES = {
    "cooperative": ("behavior", [("BH.coop", 1), ("TR.A", 1), ("QR.antagonism", 1), ("FC.warmth", 1), ("HA", -1)]),
    "combative": ("behavior", [("TR.A", -1), ("QR.antagonism", -1), ("HA", 1)]),
    "expressive": ("behavior", [("TR.E", 1), ("FC.excitement", 1), ("FC.gregariousness", 1), ("FC.warmth", 1)]),
    "reserved": ("behavior", [("TR.E", -1), ("FC.gregariousness", -1), ("BH.dominance", -1), ("FC.warmth", -1)]),
    "careful": ("behavior", [("TR.C", 1), ("BH.diligence", 1), ("QR.diligence", 1)]),
    "loose": ("behavior", [("TR.C", -1), ("BH.diligence", -1), ("QR.diligence", -1)]),
    "wide": ("range", [("BH.breadth", 1), ("QR.breadth", 1), ("TR.O", 1)]),
    "narrow": ("range", [("BH.breadth", -1), ("QR.breadth", -1), ("TR.O", -1)]),
}
COIN_UNEXPLAINED_MIN = 2


class _View:
    """What the archetype rules read: signed levels, gates, appeal dimensions, sensibilities."""

    def __init__(self, items, gates, dims, ps, rc):
        self.level = {i["id"]: level_of(i["band"]) for i in items if i.get("band") in LEVELS and not i["neutral"]}
        self.gates, self.rc = gates, rc
        self.raw_dims = set(dims)                                  # appeals.dims as given, "mixed" included
        self.dims = {d for d in dims if d != "mixed"}              # MA:x never holds for a mixed appeal
        self.ps = {p.get("category") for p in ps}
        self.leader = any(i["id"] == "LD" for i in items)         # an elected party leader (the packet carries the LD item)

    def holds(self, item_id, cond):
        if item_id == "DF":
            return bool(self.gates.get("DF"))
        if item_id == "MA":
            return cond in self.dims
        if item_id == "PS":
            return bool(self.ps & set(cond.split("|")))
        if item_id == "RC":
            return self.rc == cond
        if item_id == "LD":
            return self.leader
        lvl = self.level.get(item_id)
        if lvl is None:
            return False
        return {"hi": lvl >= 1, "lo": lvl <= -1, "HI": lvl >= 2, "LO": lvl <= -2, "present": True}[cond]      # HI and LO: the two outer bands (guide 8.1)

    def opposite_strong(self, alternatives):
        """Strongly opposite: at least one band alternative is present and every present one sits two or more bands the
        other way.  MA, PS, RC, DF and LD alternatives are never "present" for this test."""
        present = [(i, c) for i, c in alternatives if c in ("hi", "lo") and i in self.level]
        if not present:
            return False
        return all((self.level[i] <= -2) if c == "hi" else (self.level[i] >= 2) for i, c in present)


def archetype_candidates(items, gates, appeals, ps, rc_dir, tier):
    view = _View(items, gates, (appeals or {}).get("dims") or [], ps, rc_dir)
    out = []
    for animal, spec in ARCHETYPES.items():
        matched, via = [], []
        for n, alternatives in enumerate(spec["slots"], 1):
            hit = next(((i, c) for i, c in alternatives if view.holds(i, c)), None)
            if hit:
                matched.append(n)
                via.append("slot %d via %s" % (n, hit[0] if hit[0] not in ("MA", "PS", "RC") else "%s:%s" % hit))
        contradicted = ["required slot %d unmatched" % n for n in spec.get("required", []) if n not in matched]
        for n, slot in enumerate(spec["slots"], 1):          # global disqualifier R1; a matched slot is never strongly opposite
            if n not in matched and view.opposite_strong(slot):
                contradicted.append("slot %d strongly opposite" % n)
        for clause in spec["contra"]:
            if all(view.holds(i, c) for i, c in clause):
                contradicted.append(" and ".join("%s %s" % (i, c) for i, c in clause))
        cand = {"animal": animal, "matched": len(matched), "slots": via, "matched_slots": matched,
                "contradicted": contradicted, "disqualified": bool(contradicted)}
        cand["outer"] = _outer_explained(cand, view)
        out.append(cand)
    return out, view


def _appeal_matched(c, view):
    return any(i == "MA" and view.holds("MA", cond) for n in c["matched_slots"]
               for i, cond in ARCHETYPES[c["animal"]]["slots"][n - 1])


def _ma_conflict(c, view):
    """True when the animal has an MA slot and none of that slot's dimensions is in appeals.dims (always, for a mixed appeal)."""
    for slot in ARCHETYPES[c["animal"]]["slots"]:
        dims = [cond for i, cond in slot if i == "MA"]
        if dims and not any(d in view.raw_dims for d in dims):
            return True
    return False


def _outer_explained(c, view):
    """outer(c): matched slots that hold at least one band alternative at level +3 or -3.  One per slot; an alternative
    counts only if it holds (it points the way the slot says)."""
    return sum(1 for n in c["matched_slots"]
               if any(view.holds(i, cond) and abs(view.level.get(i, 0)) == 3 for i, cond in ARCHETYPES[c["animal"]]["slots"][n - 1]))


def choose_animal(candidates, view, tier):
    """Guide 8.2 steps 1 to 4: returns the chosen candidate, or None when no template animal reaches the threshold."""
    live = sorted((c for c in candidates if not c["disqualified"]), key=lambda c: (-c["matched"], c["animal"]))
    if not live or live[0]["matched"] < COIN_THRESHOLD[tier]:
        return None
    top = live[0]["matched"]
    group = [c for c in live if c["matched"] == top or (c["matched"] == top - 1 and c["matched"] >= 4)]
    if len(group) == 1:
        return group[0]
    appeal = [c for c in group if _appeal_matched(c, view)]                    # 4a: the appeal slot matches
    group = appeal or group
    if len(group) == 1:
        return group[0]
    outer = {c["animal"]: _outer_explained(c, view) for c in group}            # 4b
    best = max(c["matched"] for c in group)                                     # 4c
    tied = [c for c in group if c["matched"] == best]
    top_outer = max(outer[c["animal"]] for c in tied)
    t = [c for c in tied if outer[c["animal"]] == top_outer]
    if best == top:      # a runner-up replaces T only when it explains at least two more outer-band items (E12)
        runners = [c for c in group if c["matched"] == top - 1 and outer[c["animal"]] >= top_outer + 2]
        if runners:
            r_best = max(outer[c["animal"]] for c in runners)
            t = [c for c in runners if outer[c["animal"]] == r_best]
    if len(t) > 1:       # 4d: drop animals whose MA slot names another dimension, unless that drops them all
        t = [c for c in t if not _ma_conflict(c, view)] or t
    return sorted(t, key=lambda c: c["animal"])[0]                              # 4e


def coin_recommendation(candidates, view, tier):
    """Guide 8.2 step 5.  Returns (coin_recommended, chosen candidate or None)."""
    chosen = choose_animal(candidates, view, tier)
    base = {"binding_unexplained": {}, "advisory_unexplained": {}, "registry_match": None}
    if chosen is None:
        best = max((c["matched"] for c in candidates if not c["disqualified"]), default=0)
        return {"value": True, "reason": "no template animal reaches %d matched slots (best %d)" % (COIN_THRESHOLD[tier], best), **base}, None
    used = {i for n in chosen["matched_slots"] for i, cond in ARCHETYPES[chosen["animal"]]["slots"][n - 1] if view.holds(i, cond)}
    binding, advisory = {}, {}
    for theme, (kind, members) in THEMES.items():
        hits = [i for i, sign in members if view.level.get(i) is not None and view.level[i] * sign == 3]
        if not hits or any(i in used for i in hits):
            continue
        (binding if kind == "behavior" and len(hits) >= COIN_UNEXPLAINED_MIN else advisory)[theme] = hits
    base.update(binding_unexplained=binding, advisory_unexplained=advisory)
    if binding:
        return {"value": True, "reason": "%s leaves outer-band items unexplained: %s" % (
            chosen["animal"], "; ".join("%s (%s)" % (t, ", ".join(v)) for t, v in sorted(binding.items()))), **base}, chosen
    return {"value": False, "reason": "%s matches %d slots" % (chosen["animal"], chosen["matched"]), **base}, chosen


REGISTRY_CONDS = {"hi", "lo", "HI", "LO", "less", "more", "present"}


def view_of(packet: dict) -> "_View":
    rc = next((i for i in packet["evidence"] if i["id"] == "RC" and not i.get("neutral")), None)
    return _View(packet["evidence"], packet["gates"], (packet.get("appeals") or {}).get("dims") or [],
                 packet.get("public_sensibilities") or [], rc["direction"] if rc else None)


def signature_holds(signature, view) -> bool:
    return bool(signature) and all(view.holds(i, c) for i, c in signature)


def covers_themes(signature, binding) -> bool:
    """A signature has at least one item of every theme that triggered the coin (guide 8.2 step 6)."""
    ids = {i for i, _ in signature}
    return all(ids & {t for t, _ in THEMES[theme][1]} for theme in binding or () if theme in THEMES)


def registry_match(registry, view, binding=None):
    """The registry entry whose signature holds in full and covers every binding theme, the one with the most signature items
    first, then alphabetical."""
    held = sorted((e for e in registry if isinstance(e.get("signature"), list) and signature_holds(e["signature"], view)
                   and covers_themes(e["signature"], binding)), key=lambda e: (-len(e["signature"]), e["animal"]))
    return {"animal": held[0]["animal"], "descriptor": held[0].get("descriptor")} if held else None


def apply_registry(packet: dict, registry) -> dict:
    """Guide 8.2 step 6: when step 5 says coin, or the chosen template animal has fewer than three matched slots, a registry
    entry that holds in full decides the call.  Changes ``value`` and ``registry_match`` only; neither is hashed."""
    coin = packet["coin_recommended"]
    chosen = packet.get("suggested_animal")
    matched = next((c["matched"] for c in packet.get("archetype_candidates") or [] if c["animal"] == chosen), None)
    if coin["value"] or (chosen and (matched or 0) < 3):
        match = registry_match(registry, view_of(packet), coin.get("binding_unexplained"))
        if match:
            coin["registry_match"], coin["value"] = match, True
    return packet


def registry_seed(doc: dict) -> list[dict]:
    """The exemplars file's ``coined_registry``, checked for the guide's entry shape."""
    out = []
    for e in (doc or {}).get("coined_registry") or []:
        signature = [[str(i), str(c)] for i, c in e.get("signature") or []]
        if not (e.get("animal") and e.get("descriptor")) or len(signature) < 2 or any(c not in REGISTRY_CONDS for _, c in signature):
            raise ValueError("coined_registry entry %r is malformed" % e.get("animal"))
        out.append({"animal": e["animal"], "descriptor": e["descriptor"], "image": e.get("image"),
                    "signature": signature, "first_member": e.get("first_member")})
    return out


IMAGE_WORDS = (3, 8)


def image_problem(image, justification=None):
    """Why a registry image is not allowed, or None.  It is a plain everyday picture of 3 to 10 words, never the justification."""
    words = str(image or "").split()
    if not IMAGE_WORDS[0] <= len(words) <= IMAGE_WORDS[1]:
        return "an image has %d to %d words (got %d)" % (IMAGE_WORDS[0], IMAGE_WORDS[1], len(words))
    if justification and " ".join(words).casefold().strip(".") == " ".join(str(justification).split()).casefold().strip("."):
        return "an image is never the justification sentence"
    return None


def registry_image_problems(registry) -> list[dict]:
    return [{"animal": e.get("animal"), "problem": image_problem(e.get("image"))} for e in registry if image_problem(e.get("image"))]


def grow_registry(registry, briefs, packets, images=None, pending=None) -> list[dict]:
    """Guide 8.4: register each new coin of the finished briefs, in file order, so a coin in one batch is reusable in the next.
    The signature is every band id the brief cited under archetype at +2 or more (HI) or -2 or less (LO), plus receptivity;
    an animal with fewer than two such items is used once and not registered.  ``images`` is the orchestrator's
    {animal: image} (3 to 10 words, set at registration); an animal without one is listed in ``pending`` and not offered
    yet, and an image that breaks the rule raises ValueError."""
    out = [dict(e) for e in registry]
    known = {e["animal"] for e in out}
    for name, brief in (briefs or {}).items():
        packet = packets.get(name)
        arch = brief.get("archetype") if isinstance(brief, dict) else None
        if not packet or not isinstance(arch, dict) or not arch.get("animal") or arch["animal"] in known or arch["animal"] in TEMPLATE_ANIMALS:
            continue
        levels = {i["id"]: level_of(i["band"]) for i in packet["evidence"] if i.get("band") in LEVELS and not i.get("neutral")}
        rc = next((i for i in packet["evidence"] if i["id"] == "RC" and not i.get("neutral")), None)
        signature = []
        for cid in (brief.get("evidence_refs") or {}).get("archetype") or []:
            row = ([cid, "HI"] if levels.get(cid, 0) >= 2 else [cid, "LO"] if levels.get(cid, 0) <= -2 else
                   [cid, rc["direction"]] if cid == "RC" and rc else None)
            if row and row not in signature:
                signature.append(row)
        if len(signature) < 2:
            continue
        animal, image = arch["animal"], (images or {}).get(arch["animal"])
        if image is None:
            if pending is not None:
                pending.append({"animal": animal, "descriptor": arch.get("descriptor"), "signature": signature, "first_member": name,
                                "reason": "no image supplied: the orchestrator sets a %d to %d word image at registration" % IMAGE_WORDS})
            continue
        problem = image_problem(image, arch.get("justification"))
        if problem:
            raise ValueError("registry image for %r: %s" % (animal, problem))
        out.append({"animal": animal, "descriptor": arch.get("descriptor"), "image": image, "signature": signature, "first_member": name})
        known.add(animal)
    return out


# ---------------------------------------------------------------------------------------------
# long-form
# ---------------------------------------------------------------------------------------------
LEVEL_PLAIN = {"VOICE_VERIFIED": "voice verified", "TRANSCRIPT_ATTRIBUTED": "transcript attributed"}


def era_restricted(era) -> bool:
    """A recording from before Congress (PRE_CONGRESS_*) or before the current leadership role (PRE_CURRENT_LEADERSHIP_ROLE)."""
    return str(era or "").startswith("PRE_")


def lf_permission(status: str, era) -> list[str]:
    """Fields a long-form finding may support.  A restricted-era qualified finding supports speaks and personality only
    ("earlier in his career..."); a provisional one stays at speaks; neither ever supports a Do, Don't, Warning sign or the archetype."""
    if status == "qualified":
        return ["personality", "speaks"] if era_restricted(era) else sorted(PERMISSION["LF.qualified"])
    return sorted(PERMISSION["LF.provisional"])


def recording_era(turn: dict, records) -> str | None:
    """The diagnostics era of a ledger turn: a record naming the turn's turn_id or source_id first, then a record with no id
    (it speaks for every recording of the member)."""
    ids = {x for x in (turn.get("turn_id"), turn.get("source_id")) if x}
    for r in records or []:
        if r["ids"] and ids & r["ids"]:
            return r["source_era"]
    return next((r["source_era"] for r in records or [] if not r["ids"]), None)


def longform_items(rec: dict, records=None):
    block = (rec.get("speech_profile") or {}).get("longform_interview_evidence") or {}
    items = []
    for turn in block.get("full_turns") or []:
        qualified = turn.get("semantic_review_qualified") is True
        year = str(turn.get("recorded_at") or turn.get("published") or "")[:4]
        source_text = ", ".join(x for x in (turn.get("channel"), year) if x)
        findings = turn.get("semantic_findings") or []
        tid = turn["turn_id"]
        diagnostic = recording_era(turn, records)
        era = diagnostic or turn.get("source_era")
        ident = {"turn_id": tid, "source_id": turn.get("source_id"), "era": era, "era_source": "diagnostics" if diagnostic else "ledger"}
        items.append({"id": "LF.%s" % tid, "status": "no_findings" if not findings else "turn", **ident,
                      "source_text": source_text, "may_support": []})
        for n, f in enumerate(findings, 1):
            status = "qualified" if qualified else "provisional"
            item = {"id": "LF.%s.%d" % (tid, n), "status": status, **ident,
                    "finding": f.get("finding"), "meeting_implication": f.get("meeting_implication"),
                    "scope": f.get("scope"), "source_text": source_text, "may_support": lf_permission(status, era)}
            if era_restricted(era):
                item["era_rule"] = "from earlier in the career: say so in the sentence; speaks and personality only"
            items.append(item)
    return items


ERA_ID_FIELDS = ("turn_id", "source_id", "recording_id", "recording", "id", "job")


def read_longform_diagnostics(path: Path) -> dict:
    """{member: [{"ids": set, "source_era": str, "recorded_at": str | None}]} from the levels file's diagnostics sibling.

    A member maps to one recording (a dict with source_era), to recordings by key (a dict of such dicts) or to a list of them.
    A record with no source_era says nothing and is dropped; one with no id field speaks for every recording of the member."""
    doc = load_json(path)
    if not isinstance(doc, dict):
        raise SystemExit("long-form diagnostics file is not a member-keyed map: %s" % path)
    out = {}
    for member, value in canon_map(doc).items():
        if isinstance(value, dict) and "source_era" in value:
            rows = [("", value)]
        elif isinstance(value, dict):
            rows = list(value.items())
        elif isinstance(value, list):
            rows = [("", v) for v in value]
        else:
            raise SystemExit("long-form diagnostics for %r must be a record, a map of records or a list" % member)
        records = []
        for key, row in rows:
            if isinstance(row, dict) and row.get("source_era"):
                ids = {str(row[f]) for f in ERA_ID_FIELDS if row.get(f)} | ({str(key)} if key else set())
                records.append({"ids": ids, "source_era": row["source_era"], "recorded_at": row.get("recorded_at") or row.get("date") or None})
        if records:
            out[member] = records
    return out


def default_diagnostics_path(levels_path: Path) -> Path:
    levels_path = Path(levels_path)
    return levels_path.with_name(levels_path.stem + ".diagnostics.json")


# ---------------------------------------------------------------------------------------------
# biography
# ---------------------------------------------------------------------------------------------
def ordinal(n: int) -> str:
    return "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def first_sentences(text: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if out and len(out) + len(sentence) > limit:
            break
        out = (out + " " + sentence).strip()
    return out[:limit + 80]


BIO_PARTY = re.compile(r"\b(?:Democrat\w*|Republican\w*|GOP|Libertarian\w*|Independent|Party)\b")
BIO_MARKUP = re.compile(r"\[\[|\]\]|\{\{|\}\}")
BIO_TITLE = re.compile(r"\b(?:Rep|Sen)\.")
BIO_VOTE = re.compile(r"\bvot(?:e|ed|es|ing)\b", re.I)
BIO_FAMILY = re.compile(r"\b(?:married|marriage|wife|husband|widow\w*|divorc\w*|born|died|death|funeral|son|daughter|children|child|"
                        r"grandson|granddaughter|father|mother|brother|sister)\b", re.I)
BIO_JOB = re.compile(r"\b(?:worked as|served as|was an?|practiced)\b", re.I)


def prior_career_violation(text):
    """Why ``text`` may not be BIO.prior_career (guide 4), or None: one complete sentence of 30 words or fewer that names a job
    held before Congress, with no party word, title, wiki markup, vote or family event."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return "empty"
    for pattern, why in ((BIO_MARKUP, "wiki markup"), (BIO_TITLE, "a title (Rep., Sen.)"), (BIO_PARTY, "a party word"),
                         (BIO_VOTE, "a vote"), (BIO_FAMILY, "a family event")):
        if pattern.search(t):
            return why
    if len(re.split(r"(?<=[.!?])\s+", t)) != 1 or not re.search(r"[.!?]$", t):
        return "not exactly one complete sentence"
    if len(t.split()) > 30:
        return "over 30 words"
    return None if BIO_JOB.search(t) else "no job phrase (worked as, served as, was a, practiced)"


def clean_prior_career(text):
    """The first sentence of a wiki career paragraph when it passes the guide-4 filter, else None."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or BIO_MARKUP.search(t) or BIO_TITLE.search(t):
        return None
    first = re.split(r"(?<=[.!?])\s+", t)[0]
    return first if prior_career_violation(first) is None else None


def build_bio(name, leg_row, desk_row, wiki_row):
    district = str((desk_row or {}).get("district") or "")
    m = re.search(r"District\s+(\d+).*?,\s*([^,]+)$", district)
    state_name = (m.group(2).strip() if m else (district.split(",")[-1].strip() if district else None)) or None
    number = int(m.group(1)) if m else None
    committee = (desk_row or {}).get("committee") or {}
    summary = (wiki_row or {}).get("summary") if (wiki_row or {}).get("status") == "ok" else None
    overview = str((summary or {}).get("overview") or "")
    since = re.search(r"(?:serving|has served|served)[^.]{0,160}?\bsince\s+(?:[A-Z][a-z]+\s+\d{1,2},\s+)?(\d{4})", overview)
    return {
        "party": (leg_row or {}).get("party") or None,
        "state": state_name,
        "district": (ordinal(number) + " district") if number else None,
        "committees": [committee["on"]] if committee.get("on") else [],
        "prior_career": clean_prior_career((summary or {}).get("career")),
        "first_elected_text": ("serving since " + since.group(1)) if since else None,
    }


def bio_items(bio: dict):
    items = []
    for key, value in (("party", bio.get("party")), ("state", bio.get("state")), ("district", bio.get("district")),
                       ("committees", bio.get("committees")), ("prior_career", bio.get("prior_career")),
                       ("first_elected", bio.get("first_elected_text"))):
        if value:
            items.append({"id": "BIO." + key, "label": key.replace("_", " "), "band": None, "direction": None,
                          "strength": None, "neutral": False, "claim": value, "plain": None,
                          "scope": "public biography; personality paragraph only, at most two facts",
                          "may_support": [] if key in ("party", "district") else sorted(PERMISSION["BIO"])})
    return items


def audit_bio_items(items) -> None:
    """Fail the build closed when a BIO item breaks guide 4: party and district are never citable, and a kept prior_career
    passes the filter.  The builder filters first; this catches a filter that stopped working."""
    for item in items:
        if item["id"] in ("BIO.party", "BIO.district") and item.get("may_support"):
            raise ValueError("%s must have an empty may_support" % item["id"])
        if item["id"] == "BIO.prior_career" and prior_career_violation(item.get("claim")):
            raise ValueError("BIO.prior_career fails the guide-4 filter: %s" % prior_career_violation(item.get("claim")))


# ---------------------------------------------------------------------------------------------
# basis (computed here, never by the model)
# ---------------------------------------------------------------------------------------------
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
CONFIDENCE_RULE = (
    "Low: thin evidence tier, or fewer than 10 floor speeches and hearings together. "
    "High: rich tier with at least 50 floor speeches and hearings together. Medium: everything else.")


def parse_date(text):
    text = str(text or "").strip()
    if not text:
        return None
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", text)
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError("unparseable basis date %r" % text)
    return (int(m.group(1)), int(m.group(2)))


def month_text(ym) -> str:
    return "%s %d" % (_MONTHS[ym[1] - 1], ym[0])


def _plural(n, word, plural=None):
    return "%d %s" % (n, word if n == 1 else (plural or word + "s"))


def compute_basis(counts: dict, longform: dict, tier: str) -> dict:
    floor, hearings = int(counts["floor_speeches"]), int(counts["hearings"])
    dates = [d for d in (counts.get("floor_from"), counts.get("floor_to"), counts.get("hearing_from"),
                         counts.get("hearing_to"), longform.get("date_from"), longform.get("date_to")) if d]
    dates = [parse_date(d) for d in dates]
    date_from, date_to = (min(dates), max(dates)) if dates else (None, None)
    volume = floor + hearings
    confidence = "Low" if (tier == "thin" or volume < 10) else ("High" if (tier == "rich" and volume >= 50) else "Medium")
    recordings = int(longform.get("recordings") or 0)
    level = longform.get("level")
    parts = [_plural(floor, "floor speech", "floor speeches"), _plural(hearings, "hearing transcript")]
    if recordings:
        parts.append(_plural(recordings, "long-form recording") + (" (%s)" % LEVEL_PLAIN[level] if level else ""))
    if date_from:
        parts.append(month_text(date_from) if date_from == date_to else "%s to %s" % (month_text(date_from), month_text(date_to)))
    return {"floor_speeches": floor, "hearings": hearings, "longform_recordings": recordings,
            "longform_level": level, "longform_hours": longform.get("hours"),
            "date_from": "%04d-%02d" % date_from if date_from else None,
            "date_to": "%04d-%02d" % date_to if date_to else None,
            "confidence": confidence,
            "line": "Basis: %s. Confidence: %s." % (", ".join(parts), confidence)}


# ---------------------------------------------------------------------------------------------
# packet assembly
# ---------------------------------------------------------------------------------------------
def evidence_tier(rec: dict) -> str:
    sp = rec.get("speech_profile") or {}
    ih = sp.get("interaction_habits") or {}
    has_interaction = bool(ih.get("measures"))
    turns = max(int(ih.get("turns") or 0),
                int(((sp.get("general_word_habits") or {}).get("raw_text_observations") or {}).get("interview_turns") or 0))
    clips = int((sp.get("general_audio_habits") or {}).get("clips") or 0)
    if has_interaction and clips >= 10:
        return "rich"
    if not has_interaction and turns < 25 and clips == 0:
        return "thin"
    return "standard"


TRAIT_IDS = {"Extraversion": "TR.E", "Openness": "TR.O", "Conscientiousness": "TR.C",
             "Agreeableness": "TR.A", "Neuroticism": "TR.N"}
QR_IDS = {"openness_issue_breadth": "QR.breadth", "conscientiousness_diligence": "QR.diligence",
          "agreeableness_low_antagonism": "QR.antagonism", "neuroticism_affective_content": "QR.affect"}
BH_IDS = {"cross_party_cooperation": "BH.coop", "policy_breadth": "BH.breadth",
          "legislative_diligence": "BH.diligence", "conversational_dominance": "BH.dominance",
          "words_per_turn": "BH.wpt"}
FC_IDS = {"warmth": "FC.warmth", "excitement": "FC.excitement", "activity": "FC.activity",
          "gregariousness": "FC.gregariousness", "assertiveness": "FC.assertiveness"}
IX_IDS = {k: "IX." + k for k in ("wpt_mean", "wpt_p90", "gini", "cutoff", "burst", "discmark")}
AU_IDS = {"pace": "AU.pace", "pause": "AU.pause", "volume_dyn": "AU.volume", "pitch_var": "AU.pitch"}


def _band(node):
    b = (node or {}).get("band") if "band" in (node or {}) else node
    return b if isinstance(b, dict) and b.get("label") else None


def build_member_packet(name, rec, *, pronoun, surname, pronoun_source, bio, leadership, contrast_stat,
                        coined_animals=(), avoid_phrases=(), longform_records=None):
    chamber = rec.get("chamber")
    if chamber not in ("House", "Senate"):
        raise ValueError("chamber must be House or Senate")
    sp = rec.get("speech_profile") or {}
    items = []

    def add(item_id, band):
        if band:
            items.append(band_item(item_id, band, pronoun, chamber))

    for trait, item_id in TRAIT_IDS.items():
        add(item_id, _band((rec.get("traits") or {}).get(trait)))
    for key, item_id in QR_IDS.items():
        add(item_id, _band((rec.get("qualified_reads") or {}).get(key)))
    behavior = rec.get("behavior") or {}
    for key, item_id in BH_IDS.items():
        add(item_id, _band(behavior.get(key)))
    for key, item_id in FC_IDS.items():
        add(item_id, _band(((sp.get("general_word_habits") or {}).get("communication_facets") or {}).get(key)))
    measures = (sp.get("interaction_habits") or {}).get("measures") or {}
    for key, item_id in IX_IDS.items():
        add(item_id, _band(measures.get(key)))
    df_node = sp.get("repeated_defensive_reactions")
    df_band = _band(df_node)
    gates = {"DF": bool(df_band and not df_band.get("neutral")),
             "BH.coop": bool(_band(behavior.get("cross_party_cooperation"))),
             "topic_associations": list(sp.get("supported_topic_associations") or []),
             "party_unity": False}
    if gates["DF"]:
        add("IX.defense", df_band)
        add("DF", df_band)
    audio = sp.get("general_audio_habits") or {}
    for key, item_id in AU_IDS.items():
        add(item_id, _band((audio.get("channels") or {}).get(key)))
    floor = (audio.get("floor_composition") or {}).get("composition")
    if floor in ("floor_only", "floor_dominant"):
        items.append({"id": "AU.floor", "label": "audio composition", "band": None, "direction": floor,
                      "strength": None, "neutral": False,
                      "plain": render("{Poss} recorded audio is %s from the House floor" % (
                          "all" if floor == "floor_only" else "mostly"), pronoun, chamber),
                      "scope": "floor microphones and format differ; never compare with interview delivery",
                      "may_support": sorted(permission_for("AU.floor"))})
    items.extend(contrast_items(rec, contrast_stat, pronoun))
    appeals = normalize_appeals(sp.get("motivating_appeals"))
    if appeals:
        items.append({"id": "MA", "label": "the case that fits", "band": None, "direction": appeals["dims"][0],
                      "strength": None, "neutral": False, "plain": appeals["plain"], "scope": appeals["scope"],
                      "may_support": [] if appeals["dims"] == ["mixed"] else sorted(permission_for("MA"))})
    ha = _band(rec.get("hearing_antagonism"))
    add("HA", ha)
    vis = rec.get("visibility") or {}
    add("VI.observed", _band(vis.get("observed")))
    add("VI.role", _band(vis.get("role_adjusted")))
    rc = receptivity_item(rec.get("receptivity"), pronoun)
    items.append(rc)
    if leadership:
        leadership = {**leadership, "plain": ld_plain(leadership["title"], pronoun)}
        items.append({"id": "LD", "label": "leadership role", "band": None, "direction": None, "strength": None,
                      "neutral": False, "claim": leadership["title"], "plain": leadership["plain"],
                      "scope": "state the title once, in the personality paragraph",
                      "may_support": sorted(permission_for("LD"))})
    bio_list = bio_items(bio)
    audit_bio_items(bio_list)
    items.extend(bio_list)
    ps = sensibility_items(rec)
    lf = longform_items(rec, longform_records)
    tier = evidence_tier(rec)
    candidates, view = archetype_candidates(items, gates, appeals, ps, None if rc["neutral"] else rc["direction"], tier)
    coin, chosen = coin_recommendation(candidates, view, tier)
    coin["template_coin"] = coin["value"]          # the call before the registry: the registry may turn it into a coin later, never the reverse
    effectiveness = rec.get("effectiveness") or {}
    return {
        "member": {"display_name": name, "surname": surname, "pronoun": pronoun, "pronoun_source": pronoun_source,
                   "chamber": chamber, "leadership": leadership, "bio": bio},
        "evidence_tier": tier,
        "evidence": items,
        "gates": gates,
        "appeals": appeals,
        "public_sensibilities": ps,
        "longform": lf,
        "archetype_candidates": candidates,
        "suggested_animal": chosen["animal"] if chosen else None,
        "warning_form": "observed" if cue_eligible_ids(items, gates, lf) else "procedural",
        "coin_recommended": coin,
        "coined_animals": list(coined_animals),
        "avoid_phrases": list(avoid_phrases),
        "reference_only": {"effectiveness": {k: effectiveness.get(k) for k in ("do_pct", "les_pct", "ft", "kb_pct")},
                           "note": "not citable: see EFFECTIVENESS_DEFINITIONS in build_member_brief_packets.py"},
    }


# Effectiveness fields are defined, not cited (define-or-drop).  Sources: make_member_desk.py
# psy_for() ext block, say_do_gap.py, lep_criterion.py, kb_benchmark.py.
EFFECTIVENESS_DEFINITIONS = {
    "do_pct": "percentile (desk bisect_left/(n-1) rank) of do_z, credit-union bill delivery z within chamber "
              "(say_do_gap.py); the measure the engines rank on; sponsored and cosponsored CU bills weighted by role and advancement",
    "les_pct": "percentile of classic Legislative Effectiveness Score (lep_criterion.py lesclassic, 117th-118th mean); "
               "passage-weighted, so it tilts toward the majority party (about 2.3x majority vs minority)",
    "ft": "follow-through ratio (lep_criterion.py): committee-action-and-beyond stage events per bill introduced, "
          "115th-118th Congress pooled; can exceed 1; absent under 5 bills",
    "kb_pct": "percentile of Katz-Bonacich network centrality (kb_benchmark.py), diffusion-kernel position; "
              "a targeting diagnostic, not effectiveness",
}


# Guide 12 seed lines (R8, E5, pilot review copies) plus the pilot-2 review additions; every packet carries them, always as a list.
SEED_AVOID_PHRASES = (
    "the meeting has become a debate", "stop adding arguments", "make your best case before", "make your strongest point before",
    "the one decision you need", "keep each point short and tied", "argument matters more than pressure", "a visible role he can lead",
    "a visible role she can lead", "written recap within a day", "fewer specifics than you might want", "for agreement", "as a no",
    "pad the case with", "pad your ask with", "across the issues it touches", "whether he has the details he needs",
    "whether she has the details she needs", "fits how he legislates", "fits how she legislates", "stays on the issues she already names",
    "stays on the topics he already names",
    "is his home state", "is her home state", "answers run before you respond", "a weaker suit for", "reach him before his view",
    "reach her before her view", "hearing manner as", "lands whole", "crowd his sentences", "crowd her sentences",
    "gets to him more than", "gets to her more than",
)


def compute_avoid_phrases(briefs: dict, *, ngram=6, share=0.02, cap=40) -> list[str]:
    """Six-word runs used by more than ``share`` of finished briefs (guide section 12)."""
    texts = [" ".join([b.get(k, "") if isinstance(b.get(k), str) else " ".join(b.get(k) or [])
                       for k in ("bottom_line", "warning_sign", "follow_up", "personality", "speaks", "decides")]
                      + list(b.get("do") or []) + list(b.get("dont") or [])) for b in briefs.values()]
    if not texts:
        return []
    seen = collections.Counter()
    for text in texts:
        words = re.findall(r"[a-z']+", text.casefold())
        seen.update({" ".join(words[i:i + ngram]) for i in range(len(words) - ngram + 1)})
    limit = max(2, share * len(texts))
    return [p for p, n in seen.most_common() if n > limit][:cap]


# ---------------------------------------------------------------------------------------------
# input loading
# ---------------------------------------------------------------------------------------------
def read_basis_counts(path: Path) -> dict:
    required = ("member", "crec_count", "crec_min_date", "crec_max_date", "chrg_count", "chrg_min_date", "chrg_max_date")
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit("basis counts file lacks column(s): " + ", ".join(missing))
        out = {}
        for row in reader:
            key = canon_key(row["member"])
            if key in out:
                raise SystemExit("basis counts file repeats member %r" % key)
            out[key] = {"floor_speeches": int(row["crec_count"] or 0), "floor_from": row["crec_min_date"] or None,
                        "floor_to": row["crec_max_date"] or None, "hearings": int(row["chrg_count"] or 0),
                        "hearing_from": row["chrg_min_date"] or None, "hearing_to": row["chrg_max_date"] or None}
    return out


def read_longform_levels(path: Path) -> dict:
    doc = load_json(path)
    if not isinstance(doc.get("members"), dict) or "status" not in doc:
        raise SystemExit("long-form levels file lacks status or members")
    doc["members"] = canon_map(doc["members"])
    return doc


def surname_for(name, leg_row):
    folded = lambda s: unicodedata.normalize("NFC", str(s)).casefold()
    last = (leg_row or {}).get("last_name") or ""
    if last and folded(last) in folded(name):
        return last
    tokens = [t for t in name.replace(",", " ").split() if t.rstrip(".").casefold() not in ("jr", "sr", "ii", "iii", "iv")]
    return tokens[-1]


def leadership_block(rec):
    policy = rec.get("leadership_audio_source_policy")
    if not policy:
        return None
    assumed = str(policy.get("current_role_assumed_on") or "")
    return {"title": policy["current_role"], "since_year_text": ("since " + assumed[:4]) if assumed[:4].isdigit() else None,
            "assumed_on": assumed or None,
            "era_rule": "interview and audio evidence is not split by era in the ledger; describe speech as current practice, "
                        "never as a lifelong baseline; long-form findings carry their own era",
            "interview_audio_split_by_era": False,
            "verified_post_role_source_count": policy.get("verified_post_role_source_count")}


def problems_by_member(problems) -> dict:
    """{member: message} from the "Name: message" strings build_contents returns."""
    out = {}
    for line in problems:
        name, _, message = line.partition(": ")
        out.setdefault(name, message)
    return out


def build_contents(ledger_doc, *, legislators, congestion, model_bio, desk_src, wiki, longform_eras=None):
    """Per-member packet content (what a writer is shown) with its packet_sha256; no basis, no readiness.

    A member whose record cannot be turned into a packet is left out and named in ``problems``; the rest
    still build.  The packet builder CLI fails closed on any problem; the desk flags only that member."""
    ledger = ledger_doc["members"]
    assert_clean_keys(ledger)
    problems = []
    stats = contrast_stats(ledger)
    legs = {r["bioguide_id"]: r for r in legislators}
    cg = canon_map({r["member"]: r for r in congestion})
    bg = canon_map(model_bio or {})
    desk = canon_map({r["name"]: r for r in desk_src["members"]})
    wiki = canon_map(wiki or {})
    contents = {}
    departed = departed_members()
    for name in sorted(ledger):
        rec = ledger[name]
        bioguide = cg.get(name, {}).get("bioguide")
        if name in bg and bg[name] and bioguide and bg[name] != bioguide:
            problems.append("%s: model_bio and congestion bioguides disagree" % name)
        bioguide = bioguide or bg.get(name)
        leg = legs.get(bioguide)
        if leg is None and name in departed:       # he has left the roster: no row to find, and not finding one is no failure
            leg = {}
        if leg is None:
            problems.append("%s: no legislators.csv row for bioguide %r" % (name, bioguide))
            continue
        gender = (leg.get("gender") or "").strip().upper()
        pronoun, source = {"M": ("he", "legislators.csv gender"), "F": ("she", "legislators.csv gender")}.get(
            gender, ("they", "gender absent in legislators.csv; neutral default"))
        try:
            packet = build_member_packet(
                name, rec, pronoun=pronoun, surname=surname_for(name, leg), pronoun_source=source,
                bio=build_bio(name, leg, desk.get(name), wiki.get(name)), leadership=leadership_block(rec),
                contrast_stat=stats, longform_records=(longform_eras or {}).get(name))
        except (ValueError, KeyError, TypeError, IndexError) as error:
            problems.append("%s: %s" % (name, error if isinstance(error, ValueError) else "%s %s" % (type(error).__name__, error)))
            continue
        packet["packet_sha256"] = content_sha256(packet)
        contents[name] = packet
    return contents, problems


def build_all(ledger_doc, *, legislators, congestion, model_bio, desk_src, wiki, basis_counts, levels_doc,
              allow_partial_longform=False, coined=(), avoid=(), finished=None, longform_eras=None, registry_images=None):
    """``coined`` is the seed registry (guide 8.4 entries); ``finished`` the finished briefs, whose new coins join it."""
    packets, problems = build_contents(ledger_doc, legislators=legislators, congestion=congestion, model_bio=model_bio,
                                       desk_src=desk_src, wiki=wiki, longform_eras=longform_eras)
    registry = grow_registry(coined, finished, packets, images=registry_images)
    avoid_list = list(dict.fromkeys([*SEED_AVOID_PHRASES, *(avoid or ())]))
    departed = departed_members()
    levels = levels_doc["members"] if levels_doc else {}
    status = levels_doc.get("status") if levels_doc else None
    for name, packet in packets.items():
        packet["coined_animals"], packet["avoid_phrases"] = copy.deepcopy(registry), list(avoid_list)
        apply_registry(packet, registry)
        reasons = [departed[name]] if name in departed else []
        packet["departed"] = name in departed
        counts = basis_counts.get(name) if basis_counts is not None else None
        level_row = levels.get(name)
        if counts is None:
            reasons.append("no speech basis counts row")
        if level_row is None:
            reasons.append("no long-form level row")
        elif level_row.get("level") is None:
            reasons.append("long-form level is null: %s" % (level_row.get("exception_reason") or "no reason recorded"))
        if status != "FINAL" and not allow_partial_longform:
            reasons.append("long-form levels status is %s, not FINAL" % status)
        packet["basis"] = (compute_basis(counts, level_row, packet["evidence_tier"])
                           if counts is not None and level_row is not None else None)
        packet["longform_level_row"] = ({k: level_row.get(k) for k in ("level", "recordings", "hours", "date_from", "date_to", "exception_reason")}
                                        if level_row else None)
        packet["not_ready_reasons"] = reasons
        packet["ready_for_writing"] = not reasons
    return packets, problems


def distribution(packets) -> dict:
    def call(p):
        coin = p["coin_recommended"]
        if coin.get("registry_match"):
            return "%s (registry)" % coin["registry_match"]["animal"]
        return "COIN" if coin["value"] else p["suggested_animal"]
    return dict(collections.Counter(call(p) for p in packets.values()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    ap.add_argument("--desk-src", type=Path, default=DESK_SRC_PATH)
    ap.add_argument("--wikipedia", type=Path, default=WIKI_PATH)
    ap.add_argument("--legislators", type=Path, default=LEGISLATORS_PATH)
    ap.add_argument("--congestion", type=Path, default=CONGESTION_PATH)
    ap.add_argument("--model-bio", type=Path, default=MODEL_BIO_PATH)
    ap.add_argument("--basis-counts", type=Path, required=True)
    ap.add_argument("--longform-levels", type=Path, required=True)
    ap.add_argument("--longform-diagnostics", type=Path, default=None,
                    help="per-recording source_era; defaults to <levels file stem>.diagnostics.json next to --longform-levels; "
                         "required when the levels file is FINAL")
    ap.add_argument("--briefs", type=Path, default=BRIEFS_PATH, help="finished briefs: feeds avoid_phrases and coined_animals")
    ap.add_argument("--exemplars", type=Path, default=EXEMPLARS_PATH)
    ap.add_argument("--registry-images", type=Path, default=None,
                    help="JSON {animal: image}: the 3 to 10 word image the orchestrator sets when a new coin is registered")
    ap.add_argument("--out", type=Path, default=PACKETS_PATH)
    ap.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    ap.add_argument("--allow-partial-longform", action="store_true",
                    help="DRY RUNS ONLY: mark packets ready although the long-form levels status is not FINAL")
    ap.add_argument("--dry-run", action="store_true",
                    help="write nothing; tolerate missing basis counts; print the archetype distribution")
    args = ap.parse_args(argv)
    if args.allow_partial_longform and not args.dry_run and (
            Path(args.out).resolve() == PACKETS_PATH.resolve() or Path(args.receipt).resolve() == RECEIPT_PATH.resolve()):
        print("FAIL CLOSED: refusing to write --allow-partial-longform packets to the live packet path; "
              "pass --out and --receipt elsewhere, or --dry-run", file=sys.stderr)
        return 2

    required = {"ledger": args.ledger, "desk-src": args.desk_src, "wikipedia": args.wikipedia,
                "legislators": args.legislators, "congestion": args.congestion, "longform-levels": args.longform_levels}
    if not args.dry_run:
        required["basis-counts"] = args.basis_counts
    missing = ["--%s %s" % (k, v) for k, v in required.items() if not Path(v).is_file()]
    if missing:
        print("FAIL CLOSED: missing required input(s):\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2
    ledger_doc = load_json(args.ledger)
    if ledger_doc.get("schema") != "member-narrative-evidence-v4" or ledger_doc.get("status") != "ACTIVE_FAIL_CLOSED_INPUT":
        print("FAIL CLOSED: ledger is not the active member-narrative-evidence-v4 artifact", file=sys.stderr)
        return 2
    levels_doc = read_longform_levels(args.longform_levels)
    diagnostics_path = args.longform_diagnostics or default_diagnostics_path(args.longform_levels)
    longform_eras = None
    if Path(diagnostics_path).is_file():
        longform_eras = read_longform_diagnostics(diagnostics_path)
    elif levels_doc.get("status") == "FINAL" or args.longform_diagnostics:
        print("FAIL CLOSED: %s long-form diagnostics file is missing: %s" % (
            "the levels file is FINAL but the" if levels_doc.get("status") == "FINAL" else "the named", diagnostics_path), file=sys.stderr)
        return 2
    else:
        print("WARNING: no long-form diagnostics file at %s; long-form eras come from the ledger" % diagnostics_path, file=sys.stderr)
    basis_counts = read_basis_counts(args.basis_counts) if Path(args.basis_counts).is_file() else None
    if basis_counts is None:
        print("WARNING (dry run): basis counts file absent; packets carry basis=null", file=sys.stderr)
    with Path(args.legislators).open(encoding="utf-8-sig", newline="") as handle:
        legislators = list(csv.DictReader(handle))
    model_bio = load_json(args.model_bio) if Path(args.model_bio).is_file() else {}
    wiki_doc = load_json(args.wikipedia)
    if wiki_doc.get("schema") != "member-wikipedia-biographies-v1":
        print("FAIL CLOSED: Wikipedia summary artifact has an unsupported schema", file=sys.stderr)
        return 2
    finished = load_json(args.briefs).get("briefs", {}) if Path(args.briefs).is_file() else {}
    finished = finished if isinstance(finished, dict) else {}
    seed = registry_seed(load_json(args.exemplars)) if Path(args.exemplars).is_file() else []
    avoid = compute_avoid_phrases(finished)
    images = load_json(args.registry_images) if args.registry_images and Path(args.registry_images).is_file() else None
    for bad in registry_image_problems(seed):
        print("WARNING: seed registry image for %s: %s" % (bad["animal"], bad["problem"]), file=sys.stderr)

    try:
        packets, problems = build_all(
            ledger_doc, legislators=legislators, congestion=load_json(args.congestion), model_bio=model_bio,
            desk_src=load_json(args.desk_src), wiki=wiki_doc.get("members") or {}, basis_counts=basis_counts,
            levels_doc=levels_doc, allow_partial_longform=args.allow_partial_longform, coined=seed, avoid=avoid, finished=finished,
            longform_eras=longform_eras, registry_images=images)
    except ValueError as error:
        print("FAIL CLOSED: %s" % error, file=sys.stderr)
        return 2
    coined = next(iter(packets.values()))["coined_animals"] if packets else seed
    if basis_counts is not None:
        absent = sorted(set(packets) - set(basis_counts))
        if absent and not args.dry_run:
            problems.append("basis counts lack %d member(s): %s" % (len(absent), ", ".join(absent[:8])))
    unexpected = sorted(set(levels_doc["members"]) - set(packets))
    if unexpected:
        problems.append("long-form levels carry member(s) the ledger lacks: " + ", ".join(unexpected))
    absent_levels = sorted(set(packets) - set(levels_doc["members"]))
    if absent_levels:
        problems.append("long-form levels lack %d ledger member(s): %s" % (len(absent_levels), ", ".join(absent_levels[:8])))
    if problems:
        print("FAIL CLOSED:\n  " + "\n  ".join(problems[:40]), file=sys.stderr)
        return 2

    dist = distribution(packets)
    ready = sum(1 for p in packets.values() if p["ready_for_writing"])
    tiers = dict(collections.Counter(p["evidence_tier"] for p in packets.values()))
    print("members: %d  ready_for_writing: %d  tiers: %s" % (len(packets), ready, tiers))
    print("long-form levels status: %s%s" % (levels_doc.get("status"),
          "  (--allow-partial-longform: DRY RUN ONLY)" if args.allow_partial_longform else ""))
    print("archetype distribution (suggested; COIN = coin_recommended with no registry match):")
    for animal, n in sorted(dist.items(), key=lambda kv: -kv[1]):
        print("  %-12s %4d  %4.1f%%" % (animal, n, 100.0 * n / len(packets)))
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    inputs = {k: {"path": str(Path(v).resolve()), "sha256": sha256_file(v)} for k, v in {
        "ledger": args.ledger, "desk_src": args.desk_src, "wikipedia": args.wikipedia, "legislators": args.legislators,
        "congestion": args.congestion, "basis_counts": args.basis_counts, "longform_levels": args.longform_levels,
        "style_guide": STYLE_GUIDE_PATH}.items()}
    if Path(args.model_bio).is_file():
        inputs["model_bio"] = {"path": str(Path(args.model_bio).resolve()), "sha256": sha256_file(args.model_bio)}
    if longform_eras is not None:
        inputs["longform_diagnostics"] = {"path": str(Path(diagnostics_path).resolve()), "sha256": sha256_file(diagnostics_path)}
    turns = [i for p in packets.values() for i in p["longform"] if i["status"] in ("turn", "no_findings")]      # one item per recording turn
    document = {"schema": SCHEMA, "status": "PACKETS", "count": len(packets),
                "ledger_sha256": inputs["ledger"]["sha256"], "members": packets}
    dump_atomic(document, args.out)
    full = levels_doc.get("status") == "FINAL"
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "READY" if full and not args.allow_partial_longform else
                  ("DRY_RUN_PARTIAL_ALLOWED" if args.allow_partial_longform else "NOT_READY_PARTIAL_LONGFORM"),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "packets_sha256": sha256_file(args.out),
        "ledger_sha256": inputs["ledger"]["sha256"],
        "ledger_sources_sha256": ledger_doc.get("sources_sha256"),
        "inputs": inputs,
        "longform_levels_status": levels_doc.get("status"),
        "longform_levels_generated_at": levels_doc.get("generated_at"),
        "longform_levels_census": levels_doc.get("census"),
        "allow_partial_longform": bool(args.allow_partial_longform),
        "counts": {"members": len(packets), "ready_for_writing": ready, "not_ready": len(packets) - ready,
                   "tiers": tiers, "archetype_distribution": dist,
                   "longform_turns_by_era": dict(collections.Counter(i.get("era") for i in turns)),
                   "longform_turns_without_diagnostics_era": (sum(1 for i in turns if i.get("era_source") != "diagnostics")
                                                              if longform_eras is not None else None),
                   "departed": sorted(departed_members()), "deferred_new_members": list(DEFERRED_NEW_MEMBERS),
                   "levels_extra_keys": sorted(set(levels_doc["members"]) - set(packets)),
                   "basis_extra_keys": sorted(set(basis_counts) - set(packets))},
        "not_ready_reasons": dict(collections.Counter(r.split(":")[0] for p in packets.values() for r in p["not_ready_reasons"])),
        "confidence_rule": CONFIDENCE_RULE,
        "setting_contrast_rule": {"min_clips": SC_MIN_CLIPS, "sd_multiple": SC_SD_MULT, "min_pair_members": SC_MIN_PAIR_MEMBERS,
                                  "floor_pairs_never_meaningful": True},
        "effectiveness_definitions": EFFECTIVENESS_DEFINITIONS,
        "party_unity": "no party-unity or leadership-alignment measure exists in the data; gates.party_unity is false for every member",
        "avoid_phrases": len(avoid), "coined_animals": len(coined),
    }
    dump_atomic(receipt, args.receipt)
    print("wrote %s\nwrote %s  status=%s" % (args.out, args.receipt, receipt["status"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
