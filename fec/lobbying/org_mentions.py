# -*- coding: utf-8 -*-
"""Build volume-only organization mentions across six public-speech channels.

Law:
Volume-only public-speech measurement. A mention is NOT evidence of
contact, agreement, or lobbying. This producer reads NO stance labels,
writes NO stance artifacts, moves NO rank, and feeds NO factor — display
only. Press stance classification is Paul's domain; the producer touches
only identity/date/text columns of press stores. Channels are never
summed with each other or with anything else.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # noqa: E501,E702

import argparse
import bisect
import collections
import csv
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import date, datetime, timezone

from paths import DRAFT as DRAFT_ROOT
from paths import ROOT as MB_ROOT
from lobbying.entity_identity import RESEARCHED_ENTITIES
from lobbying.fec_lda_bridge3_look import CROSSWALK as PINNED_COMPANIES
from lobbying.sector_spend import ROSTERS, normalize_name

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


SCHEMA = "org-mentions-v1"
CHANNELS = ("press", "tweet", "floor", "interview", "bluesky", "x")
SECTORS = tuple(ROSTERS)
GLOBAL_DATE_FLOOR = "2022-01-01"
X_DATE_MIN = "2026-07-16"
X_DATE_MAX = "2026-07-26"
BY_ORG_LIMIT = 20
TOP_MEMBER_LIMIT = 20
UNVERIFIED_MARKER = "[UNVERIFIED — confirm]"
NON_VOTING_DELEGATES = ("K000404", "P000610", "R000600")

METHOD_NOTE = (
    "Volume-only public-speech measurement. A mention is NOT evidence of "
    "contact, agreement, or lobbying. This producer reads NO stance labels, "
    "writes NO stance artifacts, moves NO rank, and feeds NO factor — display "
    "only. Press stance classification is Paul's domain; the producer touches "
    "only identity/date/text columns of press stores. Channels are never "
    "summed with each other or with anything else. Counts are documents naming "
    "an organization, not raw occurrences."
)

ACU_PAYWALL_RE = re.compile(
    r"America(?:'|\u2019)s Credit Unions membership brings you access",
    re.IGNORECASE,
)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
TOKEN_RE = re.compile(r"[A-Z0-9]+")
BLOCK_VERB_OBJECT_RE = re.compile(r"\s+(?:A|AN|THE|THIS|THAT|THESE|THOSE)\b")

# This closed word list is used only after the candidate universe has been
# imported from the existing authorities.  It does not add organizations.
# Any candidate exposing one of these as a bare one-token alias is Tier B.
ENGLISH_DICTIONARY_WORDS = frozenset(
    {
        "AFFIRM", "BETTERMENT", "BLOCK", "BULLISH", "CHARTER", "CHIME", "CIRCLE",
        "COX", "DAVE", "DEEL", "DISCOVER", "DISH", "GUSTO", "LEDGER", "LUMEN",
        "MERCURY", "PARADIGM", "PHANTOM", "PUBLIC", "RAMP", "RIPPLE", "SQUARE",
        "STRATEGY", "STRIPE", "TDS", "TOAST", "VISA", "WISE",
    }
)
STRICT_TIER_B = frozenset(
    {"BLOCK", "LEDGER", "WISE", "BETTERMENT", "PARADIGM", "SQUARE", "CHARTER"}
)
RESIDUAL_PRECISION = {
    "VISA": {"genuine": 50, "accepted": 50, "precision": 1.0},
    "RIPPLE": {"genuine": 4, "accepted": 5, "precision": 0.8},
    "AFFIRM": {"genuine": 2, "accepted": 3, "precision": 2 / 3},
    "CIRCLE": {"genuine": 1, "accepted": 5, "precision": 0.2},
    "DAVE": {"genuine": 1, "accepted": 4, "precision": 0.25},
}
US_STATE_WORDS = frozenset(
    {
        "ALABAMA", "ALASKA", "ARIZONA", "ARKANSAS", "CALIFORNIA",
        "COLORADO", "CONNECTICUT", "DC", "DELAWARE", "DISTRICT OF COLUMBIA",
        "FLORIDA", "GEORGIA", "HAWAII", "IDAHO", "ILLINOIS", "INDIANA",
        "IOWA", "KANSAS", "KENTUCKY", "LOUISIANA", "MAINE", "MARYLAND",
        "MASSACHUSETTS", "MICHIGAN", "MINNESOTA", "MISSISSIPPI", "MISSOURI",
        "MONTANA", "NEBRASKA", "NEVADA", "NEW HAMPSHIRE", "NEW JERSEY",
        "NEW MEXICO", "NEW YORK", "NORTH CAROLINA", "NORTH DAKOTA", "OHIO",
        "OKLAHOMA", "OREGON", "PENNSYLVANIA", "RHODE ISLAND",
        "SOUTH CAROLINA", "SOUTH DAKOTA", "TENNESSEE", "TEXAS", "UTAH",
        "VERMONT", "VIRGINIA", "WASHINGTON", "WEST VIRGINIA", "WISCONSIN",
        "WYOMING",
    }
)
QUALIFIER_RE = re.compile(
    r"\b(?:INC|INCORPORATED|CORP|CORPORATION|COMPANY|LLC|LTD|PAYMENTS?|"
    r"CRYPTO|CRYPTOCURRENCY|BLOCKCHAIN|STABLECOIN|EXCHANGE|FINTECH|APP|"
    r"PLATFORM|CEOS?|CHIEF EXECUTIVE OFFICERS?|BANK|FINANCIAL|"
    r"TECHNOLOG(?:Y|IES)|CARD|NETWORK|"
    r"BROKER|LENDER|ASSOCIATION|COALITION|FOUNDATION|INSTITUTE)\b"
)
LEGAL_SUFFIX_RE = re.compile(
    r"\s+(?:INC|INCORPORATED|LLC|LTD|CORP|CORPORATION|HOLDINGS|"
    r"TECHNOLOGIES|COMPANY)$"
)
FAMILY_MAP = {
    "COINBASE GLOBAL": "COINBASE",
    "BLOCK": "BLOCK / SQUARE / CASH APP",
    "SQUARE": "BLOCK / SQUARE / CASH APP",
    "BLOCK SQUARE CASH APP": "BLOCK / SQUARE / CASH APP",
}
FAMILY_DISPLAY_NAMES = {
    "BLOCK SQUARE CASH APP": "BLOCK / SQUARE / CASH APP",
}

TOPICAL_PATTERNS = {
    "credit_union": (re.compile(r"\bCREDIT UNIONS?\b"),),
    "bank": (re.compile(r"\bBANKS?\b"),),
    "crypto": (
        re.compile(
            r"\b(?:CRYPTO|CRYPTOCURRENC(?:Y|IES)|BLOCKCHAIN|STABLECOINS?|"
            r"DIGITAL ASSETS?)\b"
        ),
    ),
    "fintech": (
        re.compile(r"\bFINTECH\b"),
        re.compile(r"\bBUY NOW PAY LATER\b"),
        re.compile(r"\bINTERCHANGE\b"),
    ),
    "telecom": (
        re.compile(r"\bTELECOMMUNICATIONS?\b"),
        re.compile(r"\bBROADBAND\b"),
        re.compile(r"\bROBOCALLS?\b"),
        re.compile(r"\bSPECTRUM AUCTIONS?\b"),
    ),
}

# The supplied specification names these held examples but does not enumerate
# the remaining held list.  Authority-only construction excludes every name
# outside ROSTERS/RESEARCHED_ENTITIES/PINNED_COMPANIES without guessing it.
KNOWN_HELD_AMBIGUOUS = ("AMERICAN EXPRESS", "DISCOVER", "SYNCHRONY")


class OrgMentionsError(RuntimeError):
    """An input or structural contract failed; publish no partial artifact."""


def _derived_dir():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise OrgMentionsError("LOCALAPPDATA is not set")
    return os.path.join(local, "Melange", "lda_rebuild", "derived")


def _default_paths():
    return {
        "member_roster": os.path.join(MB_ROOT, "data", "engine", "congestion.json"),
        "press_spine": os.path.join(DRAFT_ROOT, "cu_press_stance_tone.csv"),
        "press_cache": os.path.join(MB_ROOT, "press_text_cache.jsonl"),
        "press_priority": os.path.join(
            MB_ROOT, "data", "stance", "press_text_cache_priority.jsonl"
        ),
        "press_refresh_spine": os.path.join(
            MB_ROOT, "data", "stance", "press_refresh_tone.csv"
        ),
        "press_refresh_cache": os.path.join(
            MB_ROOT, "data", "stance", "press_refresh_fulltext.jsonl"
        ),
        "press_debate": os.path.join(DRAFT_ROOT, "cu_press_debate_in.csv"),
        "quorum": os.path.join(MB_ROOT, "Quorum Processed.xlsx"),
        "social_db": os.path.join(MB_ROOT, "social_media.db"),
        "x_catalog": os.path.join(MB_ROOT, "data", "stance", "x_catalog.jsonl"),
    }


def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise OrgMentionsError(f"cannot read {label} {path}: {exc}") from exc


def _date(value, label):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        raise OrgMentionsError(f"{label} has a blank date")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%m/%d/%Y").date().isoformat()
    except ValueError as exc:
        raise OrgMentionsError(f"{label} has invalid date {text!r}") from exc


def _press_spine_date(value, label):
    text = str(value or "").strip()
    if not text or text.casefold() == "na":
        return None
    return _date(value, label)


def _truth(value, label):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value or "").strip().casefold()
    if text in {"", "0", "false", "no", "n"}:
        return False
    if text in {"1", "true", "yes", "y"}:
        return True
    raise OrgMentionsError(f"{label} has invalid boolean {value!r}")


def _normalized_url(value):
    return str(value or "").strip().split("#", 1)[0]


def strip_paywall_stub(text):
    """Remove the ACU login tail and return (clean_text, stripped)."""
    value = str(text or "")
    match = ACU_PAYWALL_RE.search(value)
    if match is None:
        return value, False
    return value[:match.start()].rstrip(), True


def _slash_alias_allowed(alias):
    return (
        " " in alias
        or any(character.isdigit() for character in alias)
        or (
            alias not in ENGLISH_DICTIONARY_WORDS
            and alias not in US_STATE_WORDS
        )
    )


def _family_key(value):
    normalized = normalize_name(value)
    while normalized:
        stripped = LEGAL_SUFFIX_RE.sub("", normalized).strip()
        if stripped == normalized:
            break
        normalized = stripped
    return normalize_name(FAMILY_MAP.get(normalized, normalized))


def _alias_variants(value):
    raw = str(value or "")
    pieces = [(raw, False)]
    if "/" in raw:
        pieces.extend((piece, True) for piece in re.split(r"\s*/\s*", raw))
    normalized = normalize_name(raw)
    if " D B A " in normalized:
        pieces.append((normalized.split(" D B A ", 1)[1], False))
    variants = set()
    for piece, slash_derived in pieces:
        alias = normalize_name(piece)
        if not alias:
            continue
        if slash_derived and not _slash_alias_allowed(alias):
            continue
        variants.add(alias)
        shortened = alias
        while True:
            shorter = LEGAL_SUFFIX_RE.sub("", shortened).strip()
            if shorter == shortened:
                break
            shortened = shorter
            if shortened and (not slash_derived or _slash_alias_allowed(shortened)):
                variants.add(shortened)
    return tuple(sorted(variants, key=lambda item: (-len(item), item)))


def _literal_aliases_from_pattern(pattern):
    """Extract only wholly literal alternatives from an imported regex."""
    value = str(pattern)
    value = value.replace(r"\b", "").replace("^", "").replace("$", "")
    value = value.replace("(?:", "").replace("(", "").replace(")", "")
    aliases = []
    for piece in value.split("|"):
        if re.search(r"[\[\]{}+*?\\]", piece):
            continue
        alias = normalize_name(piece)
        if alias:
            aliases.append(alias)
    return tuple(sorted(set(aliases)))


def _is_named_unfixed(documentation):
    note = str(documentation or "").casefold()
    return (
        note.startswith("documented ")
        or "card-network name" in note
        or "payments infrastructure name" in note
    )


def _tier(aliases, force_a=False):
    if force_a:
        return "A"
    if any(alias in ENGLISH_DICTIONARY_WORDS for alias in aliases if " " not in alias):
        return "B"
    return "A"


def _compile_alias(alias):
    return re.compile(r"(?<![A-Z0-9])" + re.escape(alias) + r"(?![A-Z0-9])")


def build_candidates(rosters=None, researched_entities=None, pinned_companies=None):
    """Build candidates only from imported authorities; mint no fuzzy names."""
    rosters = ROSTERS if rosters is None else rosters
    researched_entities = (
        RESEARCHED_ENTITIES if researched_entities is None else researched_entities
    )
    pinned_companies = PINNED_COMPANIES if pinned_companies is None else pinned_companies
    candidates = []
    generic_exclusions = []

    for sector in sorted(rosters):
        roster = rosters[sector]
        grouped = collections.defaultdict(list)
        for pattern, fixed_family, documentation in roster.get("patterns", ()):
            if fixed_family is not None:
                grouped[str(fixed_family)].append(pattern)
                continue
            if _is_named_unfixed(documentation):
                for alias in _literal_aliases_from_pattern(pattern):
                    # The imported alternation establishes the candidate names,
                    # but each extracted name must match only its own literal.
                    # Reusing the whole alternation for every name would, for
                    # example, attribute VISA text to MASTERCARD as well.
                    grouped.setdefault(alias, [])
            else:
                generic_exclusions.append(
                    {
                        "sector": sector,
                        "pattern": pattern,
                        "reason": str(documentation),
                    }
                )
        for family in sorted(grouped):
            aliases = _alias_variants(family)
            candidates.append(
                {
                    "key": f"roster:{sector}:{normalize_name(family)}",
                    "org": family,
                    "sector": sector,
                    "source": "sector_spend.ROSTERS",
                    "authority_priority": 2,
                    "lob_id": None,
                    "authority_names": (str(family),),
                    "aliases": aliases,
                    "alias_patterns": tuple(_compile_alias(alias) for alias in aliases),
                    "source_patterns": tuple(re.compile(item) for item in grouped[family]),
                    "tier": _tier(aliases),
                }
            )

    for entity in researched_entities:
        entity_id = str(entity.get("entity_id") or "").strip()
        display = str(entity.get("display_name") or "").strip()
        sector = str(entity.get("sector_hint") or "").strip()
        names = entity.get("names")
        if not entity_id or not display or sector not in rosters or not isinstance(names, tuple):
            raise OrgMentionsError("entity_identity.RESEARCHED_ENTITIES is malformed")
        aliases = set(_alias_variants(display))
        for name in names:
            aliases.update(_alias_variants(name))
        aliases = tuple(sorted(aliases, key=lambda item: (-len(item), item)))
        candidates.append(
            {
                "key": f"entity:{entity_id}",
                "org": display,
                "sector": sector,
                "source": "entity_identity.RESEARCHED_ENTITIES",
                "authority_priority": 1,
                "lob_id": None,
                "authority_names": (display,) + tuple(str(name) for name in names),
                "aliases": aliases,
                "alias_patterns": tuple(_compile_alias(alias) for alias in aliases),
                "source_patterns": (),
                "tier": _tier(aliases),
            }
        )

    for sector in sorted(pinned_companies):
        if sector not in rosters:
            raise OrgMentionsError(f"pinned-company sector {sector!r} is unsupported")
        for company, lob_id in sorted(pinned_companies[sector].items()):
            aliases = _alias_variants(company)
            candidates.append(
                {
                    "key": f"pinned:{lob_id}",
                    "org": company,
                    "sector": sector,
                    "source": "fec_lda_bridge3_look.CROSSWALK",
                    "authority_priority": 0,
                    "lob_id": str(lob_id),
                    "authority_names": (str(company),),
                    "aliases": aliases,
                    "alias_patterns": tuple(_compile_alias(alias) for alias in aliases),
                    "source_patterns": (),
                    "tier": _tier(aliases, force_a=normalize_name(company).startswith("KALSHI")),
                }
            )

    seen = set()
    for candidate in candidates:
        if candidate["key"] in seen:
            raise OrgMentionsError(f"duplicate organization candidate {candidate['key']}")
        seen.add(candidate["key"])

    by_normalized_org = collections.defaultdict(list)
    for candidate in candidates:
        by_normalized_org[_family_key(candidate["org"])].append(candidate)
    merged_candidates = []
    deduplicated = []
    for normalized_org in sorted(by_normalized_org):
        group = by_normalized_org[normalized_org]
        winner = min(
            group,
            key=lambda row: (
                0 if row["authority_priority"] == 1 else 1,
                row["authority_priority"],
                row["key"],
            ),
        )
        winner = dict(winner)
        winner["family_key"] = normalized_org
        winner["family_members"] = tuple(sorted({row["org"] for row in group}))
        winner["family_member_keys"] = tuple(sorted(row["key"] for row in group))
        winner["org"] = FAMILY_DISPLAY_NAMES.get(normalized_org, winner["org"])
        family_lob_ids = sorted({
            row["lob_id"] for row in group if row.get("lob_id")
        })
        if not winner.get("lob_id") and family_lob_ids:
            winner["lob_id"] = family_lob_ids[0]
        aliases = {
            alias
            for candidate in group
            for alias in candidate["aliases"]
        }
        winner["aliases"] = tuple(sorted(aliases, key=lambda item: (-len(item), item)))
        winner["alias_patterns"] = tuple(
            _compile_alias(alias) for alias in winner["aliases"]
        )
        winner["authority_names"] = tuple(sorted({
            name
            for candidate in group
            for name in candidate["authority_names"]
        }))
        verbatim_aliases = {
            normalize_name(name) for name in winner["authority_names"] if normalize_name(name)
        }
        winner["minted_aliases"] = tuple(
            alias for alias in winner["aliases"] if alias not in verbatim_aliases
        )
        source_patterns = {
            (pattern.pattern, pattern.flags): pattern
            for candidate in group
            for pattern in candidate["source_patterns"]
        }
        winner["source_patterns"] = tuple(
            source_patterns[key] for key in sorted(source_patterns)
        )
        winner["tier"] = "B" if any(row["tier"] == "B" for row in group) else "A"
        for absorbed in sorted(
            (row for row in group if row["key"] != winner["key"]),
            key=lambda row: (row["authority_priority"], row["key"]),
        ):
            merged_candidates.append({"kept": winner["org"], "absorbed": absorbed["org"]})
        deduplicated.append(winner)
    candidates = deduplicated

    for candidate in candidates:
        trigger_text = " ".join(candidate["aliases"])
        for pattern in candidate["source_patterns"]:
            trigger_text += " " + pattern.pattern
        candidate["trigger_tokens"] = frozenset(
            token for token in TOKEN_RE.findall(trigger_text) if len(token) >= 3
            if token not in {"INC", "LLC", "LTD", "CORP"}
        )
    candidates.sort(key=lambda row: (row["authority_priority"], row["key"]))
    return candidates, generic_exclusions, merged_candidates


class CandidateMatcher:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.candidate_by_key = {
            candidate["key"]: candidate for candidate in self.candidates
        }
        index = collections.defaultdict(list)
        fallback = []
        for candidate in self.candidates:
            if candidate["trigger_tokens"]:
                for token in candidate["trigger_tokens"]:
                    index[token].append(candidate)
            else:
                fallback.append(candidate)
        self.index = {token: tuple(rows) for token, rows in index.items()}
        self.fallback = tuple(fallback)
        self.trigger_tokens = frozenset(self.index)

    def _pool(self, tokens):
        rows = {row["key"]: row for row in self.fallback}
        for token in tokens:
            for candidate in self.index.get(token, ()):
                rows[candidate["key"]] = candidate
        return rows.values()

    @staticmethod
    def _qualifier_near(
        passage, start, end, matched_alias, qualifier_spans, token_starts
    ):
        verb_object = (
            matched_alias == "BLOCK" and BLOCK_VERB_OBJECT_RE.match(passage, end)
        )
        for qualifier_start, qualifier_end in qualifier_spans:
            if qualifier_end <= start:
                gap = (
                    bisect.bisect_left(token_starts, start)
                    - bisect.bisect_left(token_starts, qualifier_end)
                )
            elif qualifier_start >= end:
                if verb_object:
                    continue
                gap = (
                    bisect.bisect_left(token_starts, qualifier_start)
                    - bisect.bisect_left(token_starts, end)
                )
            else:
                return True
            if gap < 3:
                return True
        return False

    @staticmethod
    def _accepted_for(records, allowed_keys):
        accepted = []
        seen_candidates = set()
        for record in records:
            _sentence_index, start, end, candidate, _alias, _qualifier_adjacent = record
            if candidate["key"] not in allowed_keys or candidate["key"] in seen_candidates:
                continue
            if any(
                start < prior_end and end > prior_start
                for (
                    _prior_sentence, prior_start, prior_end, _prior_candidate,
                    _prior_alias, _prior_qualifier,
                ) in accepted
            ):
                continue
            accepted.append(record)
            seen_candidates.add(candidate["key"])
        return accepted

    def text_hits(self, text):
        # Pass 1: normalize each sentence once and collect every regex hit once.
        sentences = []
        topical_hits = collections.Counter()
        document = str(text or "")
        document_tokens = set()
        for passage in SENTENCE_RE.split(document):
            normalized = normalize_name(passage)
            if not normalized:
                continue
            token_matches = tuple(TOKEN_RE.finditer(normalized))
            tokens = frozenset(match.group(0) for match in token_matches)
            token_starts = tuple(match.start() for match in token_matches)
            sentences.append((normalized, tokens, token_starts))
            document_tokens.update(tokens)
            for sector in SECTORS:
                if any(pattern.search(normalized) for pattern in TOPICAL_PATTERNS[sector]):
                    topical_hits[sector] = 1

        if not self.fallback and not (document_tokens & self.trigger_tokens):
            return collections.Counter(), topical_hits

        sentence_records = [[] for _ in sentences]
        candidate_records = collections.defaultdict(list)
        for sentence_index, sentence in enumerate(sentences):
            normalized, tokens, token_starts = sentence
            candidates = tuple(self._pool(tokens))
            qualifier_spans = (
                tuple(
                    (match.start(), match.end())
                    for match in QUALIFIER_RE.finditer(normalized)
                )
                if any(candidate["tier"] == "B" for candidate in candidates)
                else ()
            )
            for candidate in candidates:
                matches = set()
                for alias, pattern in zip(candidate["aliases"], candidate["alias_patterns"]):
                    matches.update(
                        (match.start(), match.end(), alias)
                        for match in pattern.finditer(normalized)
                    )
                for pattern in candidate["source_patterns"]:
                    matches.update(
                        (
                            match.start(), match.end(),
                            normalize_name(match.group(0))
                            or normalized[match.start():match.end()],
                        )
                        for match in pattern.finditer(normalized)
                    )
                for start, end, alias in matches:
                    record = (
                        sentence_index,
                        start,
                        end,
                        candidate,
                        alias,
                        (
                            candidate["tier"] == "B"
                            and self._qualifier_near(
                                normalized, start, end, alias,
                                qualifier_spans, token_starts,
                            )
                        ),
                    )
                    sentence_records[sentence_index].append(record)
                    candidate_records[candidate["key"]].append(record)

        for records in sentence_records:
            records.sort(
                key=lambda item: (
                    item[1], -(item[2] - item[1]),
                    item[3]["authority_priority"], item[3]["key"], item[4],
                )
            )

        # Pass 2: resolve corroboration from hit records only; no text is rescanned.
        base_qualified = set()
        strict_anchor_denied = set()
        for key, records in candidate_records.items():
            candidate = self.candidate_by_key[key]
            aliases = {record[4] for record in records}
            strict_bare_only = bool(aliases) and aliases <= STRICT_TIER_B
            if strict_bare_only:
                strict_anchor_denied.add(key)
                continue
            if (
                candidate["tier"] == "A"
                or len(aliases) >= 2
                or any(record[5] for record in records)
            ):
                base_qualified.add(key)

        base_accepted_by_sentence = [
            self._accepted_for(records, base_qualified) for records in sentence_records
        ]
        supported = set()
        for records, anchors in zip(sentence_records, base_accepted_by_sentence):
            anchors_by_sector = collections.defaultdict(set)
            for anchor_record in anchors:
                anchor = anchor_record[3]
                anchors_by_sector[anchor["sector"]].add(anchor["key"])
            for record in records:
                candidate = record[3]
                if candidate["key"] in base_qualified:
                    continue
                if candidate["key"] in strict_anchor_denied:
                    continue
                if anchors_by_sector.get(candidate["sector"]):
                    supported.add(candidate["key"])

        org_hits = collections.Counter()
        accepted_keys = base_qualified | supported
        for records in sentence_records:
            for record in self._accepted_for(records, accepted_keys):
                org_hits[record[3]["key"]] = 1
        return org_hits, topical_hits

    def passage_hits(self, passage):
        org_hits, topical_hits = self.text_hits(passage)
        return (
            tuple(self.candidate_by_key[key] for key in org_hits),
            tuple(sector for sector in SECTORS if sector in topical_hits),
        )


def _member_indexes(member_rows):
    members = {}
    by_name = {}
    for index, row in enumerate(member_rows):
        if not isinstance(row, dict):
            raise OrgMentionsError(f"member roster row {index} is not an object")
        bioguide = str(row.get("bioguide") or "").strip()
        name = str(row.get("member") or row.get("name") or "").strip()
        chamber = str(row.get("chamber") or "").strip()
        if not bioguide or not name:
            raise OrgMentionsError(f"member roster row {index} lacks member/bioguide")
        if bioguide in members:
            raise OrgMentionsError(f"member roster repeats {bioguide}")
        members[bioguide] = {"name": name, "chamber": chamber or "Unknown"}
        normalized = normalize_name(name)
        prior = by_name.get(normalized)
        if prior is not None and prior != bioguide:
            raise OrgMentionsError(f"member roster name {name!r} maps to two bioguides")
        by_name[normalized] = bioguide
    return members, by_name


def _load_member_rows(path):
    if not path or not os.path.isfile(path):
        return [], {"status": "skipped", "reason": "member roster absent", "path": path}
    payload = _load_json(path, "member roster")
    if not isinstance(payload, list):
        raise OrgMentionsError("member roster top level must be a list")
    _member_indexes(payload)
    return payload, {
        "status": "built",
        "input_rows": len(payload),
        "columns_consumed": ["member", "bioguide", "chamber"],
    }


def _skipped(channel, reason, missing=None):
    receipt = {
        "status": "skipped",
        "channel": channel,
        "reason": reason,
        "input_rows": 0,
        "date_window": {"min": None, "max": None},
        "global_date_floor": GLOBAL_DATE_FLOOR,
    }
    if missing:
        receipt["missing_inputs"] = list(missing)
    return iter(()), receipt


def _csv_projection(path, label, required):
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise OrgMentionsError(f"cannot read {label} {path}: {exc}") from exc
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except (StopIteration, csv.Error) as exc:
            raise OrgMentionsError(f"{label} has no readable header") from exc
        positions = {name: index for index, name in enumerate(header)}
        missing = sorted(set(required) - set(positions))
        if missing:
            raise OrgMentionsError(f"{label} lacks required columns: {', '.join(missing)}")
        selected = [(name, positions[name]) for name in required]
        try:
            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(header):
                    raise OrgMentionsError(
                        f"{label} row {row_number} has {len(row)} cells; expected {len(header)}"
                    )
                yield row_number, {name: row[index] for name, index in selected}, header
        except csv.Error as exc:
            raise OrgMentionsError(f"cannot parse {label} {path}: {exc}") from exc


def _press_documents(paths, member_by_name):
    required_paths = (
        paths["press_spine"], paths["press_cache"],
        paths["press_priority"], paths["press_debate"],
    )
    missing = [path for path in required_paths if not os.path.isfile(path)]
    if missing:
        return _skipped("press", "one or more press inputs absent", missing)
    receipt = {
        "status": "built",
        "channel": "press",
        "input_rows": 0,
        "eligible_rows": 0,
        "documents": 0,
        "undated_rows": 0,
        "non_member_speech_rows_excluded": 0,
        "non_member_speech_exclusion_note": (
            "org-authored trade-press coverage is not member speech; excluded from "
            "mention counting"
        ),
        "rows_dropped_before_floor": 0,
        "unmatched_members": 0,
        "text_rows": {},
        "text_urls": 0,
        "missing_text_rows": 0,
        "paywall_stub_strips": 0,
        "date_window": {"min": None, "max": None},
        "global_date_floor": GLOBAL_DATE_FLOOR,
        "columns_consumed": ["member", "bioguide", "chamber", "date", "url", "title"],
        "corpus_vintage": "historical tone plus hash-bound audited press refresh supplement",
        "universe_note": (
            "The spine is the CU-relevance-culled corpus: 1,434 counted documents "
            "(0.63% of the 249,769-release all-member index, which has no text); "
            "242/533 members have zero press documents; cross-sector press counts "
            "are floors biased toward CU-relevant contexts. Production additionally "
            "reads the hash-bound audited refresh supplement when present."
        ),
        "floor_notes": [
            "The excluded org-authored trade-press subset is 97.7% paywall-stub.",
            "A 6,000-character cap applies to 20% of member.gov rows; counts are FLOORS.",
        ],
    }
    best = {}

    def offer(url, text):
        key = _normalized_url(url)
        value = str(text or "")
        if key and len(value) > len(best.get(key, "")):
            best[key] = value

    cache_sources=[("press_cache",paths["press_cache"]),
                   ("press_priority",paths["press_priority"])]
    if paths.get("press_refresh_cache") and os.path.isfile(paths["press_refresh_cache"]):
        cache_sources.append(("press_refresh_cache",paths["press_refresh_cache"]))
    for label, path in cache_sources:
        rows = 0
        try:
            handle = open(path, "r", encoding="utf-8")
        except OSError as exc:
            raise OrgMentionsError(f"cannot read {label} {path}: {exc}") from exc
        with handle:
            for row_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                rows += 1
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise OrgMentionsError(f"{label} row {row_number} is invalid JSON: {exc}") from exc
                if not isinstance(row, dict) or not {"url", "text"}.issubset(row):
                    raise OrgMentionsError(f"{label} row {row_number} lacks url/text")
                offer(row["url"], row["text"])
        receipt["text_rows"][label] = rows
    debate_rows = 0
    for _row_number, row, _header in _csv_projection(
        paths["press_debate"], "press debate text", ("id", "text")
    ):
        debate_rows += 1
        offer(row["id"], row["text"])
    receipt["text_rows"]["press_debate"] = debate_rows
    for url in list(best):
        best[url], stripped = strip_paywall_stub(best[url])
        receipt["paywall_stub_strips"] += int(stripped)
    receipt["text_urls"] = len(best)

    def iterator():
        minimum = maximum = None
        required = ("member", "bioguide", "chamber", "date", "url", "title")
        seen_turns=set()
        spine_sources=[("press spine",paths["press_spine"])]
        if paths.get("press_refresh_spine") and os.path.isfile(paths["press_refresh_spine"]):
            spine_sources.append(("press refresh spine",paths["press_refresh_spine"]))
        for spine_label,spine_path in spine_sources:
            for row_number, row, _header in _csv_projection(
                spine_path, spine_label, required
            ):
                turn_key=(_normalized_url(row["url"]),str(row["bioguide"] or '').strip())
                if turn_key in seen_turns: continue
                seen_turns.add(turn_key)
                receipt["input_rows"] += 1
                host = urllib.parse.urlparse(str(row["url"] or "")).netloc.lower()
                if not host.endswith(".gov"):
                    receipt["non_member_speech_rows_excluded"] += 1
                    continue
                when = _press_spine_date(row["date"], f"press spine row {row_number}")
                if when is None:
                    receipt["undated_rows"] += 1
                    continue
                if when < GLOBAL_DATE_FLOOR:
                    receipt["rows_dropped_before_floor"] += 1
                    continue
                bioguide = str(row["bioguide"] or "").strip()
                name = str(row["member"] or "").strip()
                if not bioguide:
                    bioguide = member_by_name.get(normalize_name(name), "")
                if not bioguide:
                    receipt["unmatched_members"] += 1
                    continue
                text = best.get(_normalized_url(row["url"]))
                if text is None:
                    receipt["missing_text_rows"] += 1
                    continue
                receipt["eligible_rows"] += 1
                receipt["documents"] += 1
                minimum = when if minimum is None else min(minimum, when)
                maximum = when if maximum is None else max(maximum, when)
                receipt["date_window"] = {"min": minimum, "max": maximum}
                yield {
                    "bioguide": bioguide,
                    "name": name,
                    "chamber": str(row["chamber"] or "").strip(),
                    "date": when,
                    "text": text,
                }
    return iterator(), receipt


def _tweet_documents(path, member_by_name):
    if not os.path.isfile(path):
        return _skipped("tweet", "Quorum workbook absent", [path])
    receipt = {
        "status": "built", "channel": "tweet", "input_rows": 0,
        "tweet_rows": 0, "eligible_rows": 0, "documents": 0,
        "non_tweet_rows": 0, "rt_exclusions": 0, "unmatched_members": 0,
        "rows_dropped_before_floor": 0,
        "date_window": {"min": None, "max": None},
        "global_date_floor": GLOBAL_DATE_FLOOR,
        "sheet": "Members",
        "columns_consumed": [
            "member", "principal_member", "dt", "Document Type", "Title", "Content", "is_rt"
        ],
        "universe_note": (
            "Quorum Processed.xlsx Members sheet is the ALL-TOPIC universe; "
            "data/stance/quorum_member_tweets.csv is a CU-filtered subset and is not read."
        ),
    }

    def iterator():
        try:
            import openpyxl
        except ImportError as exc:
            raise OrgMentionsError("openpyxl is required for Quorum Processed.xlsx") from exc
        try:
            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except (OSError, ValueError) as exc:
            raise OrgMentionsError(f"cannot read Quorum workbook {path}: {exc}") from exc
        try:
            if "Members" not in workbook.sheetnames:
                raise OrgMentionsError("Quorum workbook lacks Members sheet")
            sheet = workbook["Members"]
            try:
                header_values = next(
                    sheet.iter_rows(min_row=1, max_row=1, values_only=True)
                )
                header = tuple(str(value or "").strip() for value in header_values)
            except StopIteration as exc:
                raise OrgMentionsError("Quorum Members sheet is empty") from exc
            required = receipt["columns_consumed"]
            positions = {name: index for index, name in enumerate(header)}
            missing = sorted(set(required) - set(positions))
            if missing:
                raise OrgMentionsError(
                    "Quorum Members sheet lacks required columns: " + ", ".join(missing)
                )
            first_column = min(positions[name] for name in required)
            last_column = max(positions[name] for name in required)
            # Actual data iteration ends at is_rt (column M).  The shipped
            # label columns are to its right and are never materialized.
            rows = sheet.iter_rows(
                min_row=2,
                min_col=first_column + 1,
                max_col=last_column + 1,
                values_only=True,
            )
            minimum = maximum = None
            for row_number, values in enumerate(rows, start=2):
                receipt["input_rows"] += 1
                row = {
                    name: values[positions[name] - first_column]
                    for name in required
                }
                if str(row["Document Type"] or "").strip().casefold() != "tweet":
                    receipt["non_tweet_rows"] += 1
                    continue
                receipt["tweet_rows"] += 1
                if _truth(row["is_rt"], f"Quorum Members row {row_number} is_rt"):
                    receipt["rt_exclusions"] += 1
                    continue
                when = _date(row["dt"], f"Quorum Members row {row_number}")
                if when < GLOBAL_DATE_FLOOR:
                    receipt["rows_dropped_before_floor"] += 1
                    continue
                name = str(row["member"] or row["principal_member"] or "").strip()
                bioguide = member_by_name.get(normalize_name(name), "")
                if not bioguide:
                    receipt["unmatched_members"] += 1
                    continue
                text = "\n".join(
                    part for part in (str(row["Title"] or "").strip(), str(row["Content"] or "").strip())
                    if part
                )
                receipt["eligible_rows"] += 1
                receipt["documents"] += 1
                minimum = when if minimum is None else min(minimum, when)
                maximum = when if maximum is None else max(maximum, when)
                receipt["date_window"] = {"min": minimum, "max": maximum}
                yield {"bioguide": bioguide, "name": name, "date": when, "text": text}
        finally:
            workbook.close()
    return iterator(), receipt


def _sqlite_connection(path):
    try:
        absolute = os.path.abspath(path).replace("\\", "/")
        connection = sqlite3.connect(f"file:{absolute}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        raise OrgMentionsError(f"cannot open social database {path}: {exc}") from exc


def _verify_sqlite_schema(connection):
    required = {
        "posts": {"platform", "post_id", "bioguide", "created_at", "text"},
        "members": {"bioguide", "name", "chamber", "bluesky"},
        "speeches": {"member", "date", "source", "chamber", "package", "text"},
    }
    present = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for table, columns in required.items():
        if table not in present:
            raise OrgMentionsError(f"social database lacks {table} table")
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(columns - actual)
        if missing:
            raise OrgMentionsError(
                f"social database {table} lacks required columns: {', '.join(missing)}"
            )
    return {table: sorted(columns) for table, columns in required.items()}


def _bluesky_documents(path):
    if not os.path.isfile(path):
        return _skipped("bluesky", "social database absent", [path])
    connection = _sqlite_connection(path)
    try:
        schemas = _verify_sqlite_schema(connection)
        unverified = connection.execute(
            "SELECT COUNT(*) FROM posts p JOIN members m ON m.bioguide=p.bioguide "
            "WHERE p.platform='bluesky' AND instr(m.bluesky, ?) > 0",
            (UNVERIFIED_MARKER,),
        ).fetchone()[0]
        verified = connection.execute(
            "SELECT COUNT(*) FROM posts p JOIN members m ON m.bioguide=p.bioguide "
            "WHERE p.platform='bluesky' AND trim(coalesce(m.bluesky,''))<>'' "
            "AND instr(m.bluesky, ?) = 0",
            (UNVERIFIED_MARKER,),
        ).fetchone()[0]
    except sqlite3.Error as exc:
        raise OrgMentionsError(f"cannot inspect Bluesky inputs: {exc}") from exc
    finally:
        connection.close()
    receipt = {
        "status": "built", "channel": "bluesky", "input_rows": int(verified),
        "eligible_rows": 0, "documents": 0, "rows_dropped_before_floor": 0,
        "unmatched_members": 0, "unverified_posts_aggregate": int(unverified),
        "unverified_marker": UNVERIFIED_MARKER,
        "verified_only": True, "date_window": {"min": None, "max": None},
        "global_date_floor": GLOBAL_DATE_FLOOR, "schemas": schemas,
        "coverage": {
            "counted_members": 33,
            "collected_posters": 134,
            "verified_handles": 335,
            "roster_members": 533,
        },
    }

    def iterator():
        connection = _sqlite_connection(path)
        minimum = maximum = None
        try:
            cursor = connection.execute(
                "SELECT p.bioguide,p.created_at,p.text,m.name,m.chamber "
                "FROM posts p JOIN members m ON m.bioguide=p.bioguide "
                "WHERE p.platform='bluesky' AND trim(coalesce(m.bluesky,''))<>'' "
                "AND instr(m.bluesky, ?) = 0 ORDER BY p.created_at,p.post_id",
                (UNVERIFIED_MARKER,),
            )
            for row_number, (bioguide, created_at, text, name, chamber) in enumerate(cursor, start=1):
                when = _date(created_at, f"Bluesky row {row_number}")
                if when < GLOBAL_DATE_FLOOR:
                    receipt["rows_dropped_before_floor"] += 1
                    continue
                receipt["eligible_rows"] += 1
                receipt["documents"] += 1
                minimum = when if minimum is None else min(minimum, when)
                maximum = when if maximum is None else max(maximum, when)
                receipt["date_window"] = {"min": minimum, "max": maximum}
                yield {
                    "bioguide": str(bioguide or "").strip(), "name": str(name or "").strip(),
                    "chamber": str(chamber or "").strip(), "date": when, "text": str(text or ""),
                }
        except sqlite3.Error as exc:
            raise OrgMentionsError(f"cannot read Bluesky posts: {exc}") from exc
        finally:
            connection.close()
    return iterator(), receipt


def _speech_documents(path, source, channel, member_by_name):
    if not os.path.isfile(path):
        return _skipped(channel, "social database absent", [path])
    connection = _sqlite_connection(path)
    try:
        schemas = _verify_sqlite_schema(connection)
        input_rows = connection.execute(
            "SELECT COUNT(*) FROM speeches WHERE source=?", (source,)
        ).fetchone()[0]
        dropped = connection.execute(
            "SELECT COUNT(*) FROM speeches WHERE source=? AND date<?",
            (source, GLOBAL_DATE_FLOOR),
        ).fetchone()[0]
    except sqlite3.Error as exc:
        raise OrgMentionsError(f"cannot inspect {channel} speeches: {exc}") from exc
    finally:
        connection.close()
    receipt = {
        "status": "built", "channel": channel, "source_discriminator": source,
        "input_rows": int(input_rows), "eligible_rows": 0, "documents": 0,
        "rows_dropped_before_floor": int(dropped), "unmatched_members": 0,
        "date_window": {"min": None, "max": None},
        "global_date_floor": GLOBAL_DATE_FLOOR, "schemas": schemas,
    }

    def iterator():
        connection = _sqlite_connection(path)
        minimum = maximum = None
        try:
            cursor = connection.execute(
                "SELECT member,date,text,chamber FROM speeches "
                "WHERE source=? AND date>=? ORDER BY date,member,package",
                (source, GLOBAL_DATE_FLOOR),
            )
            for row_number, (name, raw_date, text, chamber) in enumerate(cursor, start=1):
                when = _date(raw_date, f"{channel} row {row_number}")
                bioguide = member_by_name.get(normalize_name(name), "")
                if not bioguide:
                    receipt["unmatched_members"] += 1
                    continue
                receipt["eligible_rows"] += 1
                receipt["documents"] += 1
                minimum = when if minimum is None else min(minimum, when)
                maximum = when if maximum is None else max(maximum, when)
                receipt["date_window"] = {"min": minimum, "max": maximum}
                yield {
                    "bioguide": bioguide, "name": str(name or "").strip(),
                    "chamber": str(chamber or "").strip(), "date": when, "text": str(text or ""),
                }
        except sqlite3.Error as exc:
            raise OrgMentionsError(f"cannot read {channel} speeches: {exc}") from exc
        finally:
            connection.close()
    return iterator(), receipt


def _hearing_exclusion(path):
    base = {
        "source_discriminator": "CHRG",
        "reason": "finance-committee-only corpus is a biased denominator",
        "vintage": "2026-02-10",
    }
    if not os.path.isfile(path):
        return {**base, "status": "skipped", "input_rows": 0, "reason": "social database absent"}
    connection = _sqlite_connection(path)
    try:
        _verify_sqlite_schema(connection)
        count, minimum, maximum = connection.execute(
            "SELECT COUNT(*),MIN(date),MAX(date) FROM speeches WHERE source='CHRG'"
        ).fetchone()
        return {
            **base, "status": "excluded", "input_rows": int(count),
            "date_window": {"min": minimum, "max": maximum},
        }
    except sqlite3.Error as exc:
        raise OrgMentionsError(f"cannot inspect excluded hearings: {exc}") from exc
    finally:
        connection.close()


def _x_documents(path, members):
    if not os.path.isfile(path):
        return _skipped("x", "X catalog absent", [path])
    receipt = {
        "status": "built", "channel": "x", "input_rows": 0,
        "eligible_rows": 0, "documents": 0, "outside_window_rows": 0,
        "rows_dropped_before_floor": 0, "delegate_exclusions": 0,
        "delegate_bioguides": list(NON_VOTING_DELEGATES), "unmatched_members": 0,
        "date_window": {"min": None, "max": None},
        "global_date_floor": GLOBAL_DATE_FLOOR,
        "tiny_window": {"min": X_DATE_MIN, "max": X_DATE_MAX},
        "columns_consumed": ["handle", "bioguide", "utc_date", "text"],
        "promotion_gate": "display layer only; any promotion to a factor is Paul's gate",
        "raw_carriage_return_rule": (
            "Logical JSONL records are newline-delimited; raw carriage returns inside text "
            "are escaped before JSON decoding."
        ),
    }

    def iterator():
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                raw = handle.read()
        except OSError as exc:
            raise OrgMentionsError(f"cannot read X catalog {path}: {exc}") from exc
        minimum = maximum = None
        for row_number, physical in enumerate(raw.split("\n"), start=1):
            if not physical.strip():
                continue
            line = physical[:-1] if physical.endswith("\r") else physical
            line = line.replace("\r", r"\n")
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise OrgMentionsError(f"X catalog row {row_number} is invalid JSON: {exc}") from exc
            receipt["input_rows"] += 1
            if not isinstance(row, dict) or not {"handle", "bioguide", "utc_date", "text"}.issubset(row):
                raise OrgMentionsError(f"X catalog row {row_number} lacks required fields")
            when = _date(row["utc_date"], f"X catalog row {row_number}")
            if when < GLOBAL_DATE_FLOOR:
                receipt["rows_dropped_before_floor"] += 1
                continue
            if not X_DATE_MIN <= when <= X_DATE_MAX:
                receipt["outside_window_rows"] += 1
                continue
            bioguide = str(row["bioguide"] or "").strip()
            if bioguide in NON_VOTING_DELEGATES:
                receipt["delegate_exclusions"] += 1
                continue
            if members and bioguide not in members:
                receipt["unmatched_members"] += 1
                continue
            receipt["eligible_rows"] += 1
            receipt["documents"] += 1
            minimum = when if minimum is None else min(minimum, when)
            maximum = when if maximum is None else max(maximum, when)
            receipt["date_window"] = {"min": minimum, "max": maximum}
            yield {"bioguide": bioguide, "date": when, "text": str(row["text"] or "")}
    return iterator(), receipt


class _Accumulator:
    def __init__(self, candidates, members):
        self.candidates = {row["key"]: row for row in candidates}
        self.members = {key: dict(value) for key, value in members.items()}
        self.roster_bioguides = frozenset(members)
        self.members_with_documents = set()
        self.matcher = CandidateMatcher(candidates)
        self.org_counts = collections.Counter()
        self.org_dates = {}
        self.sector_counts = collections.Counter()
        self.topical_counts = collections.Counter()
        self.channel_windows = {channel: [None, None] for channel in CHANNELS}

    def consume(self, channel, document):
        if channel not in CHANNELS or not isinstance(document, dict):
            raise OrgMentionsError("injected channel document is malformed")
        bioguide = str(document.get("bioguide") or "").strip()
        if not bioguide:
            raise OrgMentionsError(f"{channel} document lacks bioguide")
        when = _date(document.get("date"), f"{channel} document")
        if when < GLOBAL_DATE_FLOOR:
            return
        text = str(document.get("text") or "")
        if bioguide in self.roster_bioguides:
            self.members_with_documents.add(bioguide)
        if bioguide not in self.members:
            self.members[bioguide] = {
                "name": str(document.get("name") or bioguide).strip(),
                "chamber": str(document.get("chamber") or "Unknown").strip(),
            }
        else:
            if self.members[bioguide].get("name") == bioguide and document.get("name"):
                self.members[bioguide]["name"] = str(document["name"]).strip()
            if self.members[bioguide].get("chamber") == "Unknown" and document.get("chamber"):
                self.members[bioguide]["chamber"] = str(document["chamber"]).strip()
        window = self.channel_windows[channel]
        window[0] = when if window[0] is None else min(window[0], when)
        window[1] = when if window[1] is None else max(window[1], when)
        org_hits, topical_hits = self.matcher.text_hits(text)
        year = when[:4]
        matched_sectors = set()
        for key in org_hits:
            candidate = self.candidates[key]
            self.org_counts[(bioguide, key, channel, year)] += 1
            matched_sectors.add(candidate["sector"])
            date_key = (bioguide, key, channel)
            current = self.org_dates.get(date_key)
            self.org_dates[date_key] = (
                when if current is None else min(current[0], when),
                when if current is None else max(current[1], when),
            )
        for sector in matched_sectors:
            self.sector_counts[(bioguide, sector, channel, year)] += 1
        for sector in topical_hits:
            self.topical_counts[(bioguide, sector, channel, year)] += 1

    @staticmethod
    def _nested(counter, bioguide):
        result = {}
        for (member, sector, channel, year), count in sorted(counter.items()):
            if member != bioguide:
                continue
            result.setdefault(sector, {}).setdefault(channel, {})[year] = int(count)
        return result

    def payload_members(self):
        active = {key[0] for key in self.org_counts} | {key[0] for key in self.topical_counts}
        grouped = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
        for (bioguide, key, channel, _year), count in self.org_counts.items():
            grouped[bioguide][key][channel] += int(count)
        result = {}
        for bioguide in sorted(active):
            org_rows = []
            org_keys = sorted(grouped.get(bioguide, {}))
            for key in org_keys:
                candidate = self.candidates[key]
                channels = {}
                for channel in CHANNELS:
                    count = grouped[bioguide][key][channel]
                    if not count:
                        continue
                    first, last = self.org_dates[(bioguide, key, channel)]
                    channels[channel] = {"mentions": int(count), "first": first, "last": last}
                row = {
                    "org": candidate["org"], "sector": candidate["sector"],
                    "tier": candidate["tier"], "channels": channels,
                }
                if candidate["lob_id"]:
                    row["lob_id"] = candidate["lob_id"]
                org_rows.append(row)
            org_rows.sort(
                key=lambda row: (
                    -max((value["mentions"] for value in row["channels"].values()), default=0),
                    normalize_name(row["org"]), row.get("lob_id", ""),
                )
            )
            meta = self.members[bioguide]
            result[bioguide] = {
                "name": meta["name"], "chamber": meta["chamber"],
                "by_sector": self._nested(self.sector_counts, bioguide),
                "topical": self._nested(self.topical_counts, bioguide),
                "by_org": org_rows[:BY_ORG_LIMIT],
            }
        return result

    def payload_orgs(self):
        result = {}
        grouped = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
        for (bioguide, key, channel, _year), count in self.org_counts.items():
            grouped[key][channel][bioguide] += int(count)
        active_keys = sorted(grouped)
        for key in active_keys:
            candidate = self.candidates[key]
            by_channel = {}
            top_members = {}
            for channel in CHANNELS:
                member_counts = grouped[key][channel]
                dates = []
                for bioguide in member_counts:
                    dates.extend(self.org_dates[(bioguide, key, channel)])
                if not member_counts:
                    continue
                by_channel[channel] = {
                    "mentions": int(sum(member_counts.values())),
                    "first": min(dates), "last": max(dates),
                }
                top_members[channel] = [
                    {
                        "bioguide": bioguide,
                        "name": self.members[bioguide]["name"],
                        "mentions": int(count),
                    }
                    for bioguide, count in sorted(
                        member_counts.items(),
                        key=lambda item: (-item[1], self.members[item[0]]["name"].casefold(), item[0]),
                    )[:TOP_MEMBER_LIMIT]
                ]
            org_key = candidate["org"]
            if org_key in result:
                org_key = f"{candidate['org']} [{candidate['key']}]"
            row = {
                "sector": candidate["sector"], "tier": candidate["tier"],
                "by_channel": by_channel, "top_members": top_members,
            }
            if candidate["lob_id"]:
                row["lob_id"] = candidate["lob_id"]
            result[org_key] = row
        return result


def _tier_receipt(candidates):
    rows = []
    for candidate in sorted(candidates, key=lambda row: (row["org"], row["key"])):
        row = {
            "org": candidate["org"], "sector": candidate["sector"],
            "tier": candidate["tier"], "source": candidate["source"],
        }
        if candidate["lob_id"]:
            row["lob_id"] = candidate["lob_id"]
        rows.append(row)
    return rows


def _minted_alias_receipt(candidates):
    return [
        {"org": candidate["org"], "aliases": list(candidate["minted_aliases"])}
        for candidate in sorted(candidates, key=lambda row: (row["org"], row["key"]))
        if candidate["minted_aliases"]
    ]


def _family_receipt(candidates):
    return [
        {
            "family": candidate["org"],
            "family_key": candidate["family_key"],
            "members": list(candidate["family_members"]),
            "member_keys": list(candidate["family_member_keys"]),
        }
        for candidate in sorted(candidates, key=lambda row: (row["org"], row["key"]))
        if len(candidate["family_member_keys"]) > 1
    ]


def _assert_channels_never_summed(payload):
    forbidden = {"total", "total_mentions", "combined", "all_channels"}

    def visit(value, path="payload"):
        if isinstance(value, dict):
            overlap = forbidden & set(value)
            if overlap:
                raise OrgMentionsError(
                    f"cross-channel aggregate key forbidden at {path}: {sorted(overlap)[0]}"
                )
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
    visit(payload)
    receipt = payload.get("receipt") or {}
    press_receipt = (receipt.get("channels") or {}).get("press") or {}
    refresh_rows = int(((press_receipt.get("text_rows") or {}).get(
        "press_refresh_cache") or 0))
    press_vintage = ((receipt.get("artifact_vintages") or {}).get("press") or "")
    if refresh_rows and "audited press refresh" not in press_vintage:
        raise OrgMentionsError(
            "press refresh rows are present but the top-level vintage omits their provenance"
        )


def build_payload(
    member_roster_path=None,
    press_spine_path=None,
    press_cache_path=None,
    press_priority_path=None,
    press_debate_path=None,
    quorum_path=None,
    social_db_path=None,
    x_catalog_path=None,
    member_rows=None,
    documents_by_channel=None,
    channel_receipts=None,
):
    """Build a deterministic payload from paths or injected channel documents."""
    defaults = _default_paths()
    paths = {
        "member_roster": member_roster_path or defaults["member_roster"],
        "press_spine": press_spine_path or defaults["press_spine"],
        "press_cache": press_cache_path or defaults["press_cache"],
        "press_priority": press_priority_path or defaults["press_priority"],
        "press_refresh_spine": defaults["press_refresh_spine"],
        "press_refresh_cache": defaults["press_refresh_cache"],
        "press_debate": press_debate_path or defaults["press_debate"],
        "quorum": quorum_path or defaults["quorum"],
        "social_db": social_db_path or defaults["social_db"],
        "x_catalog": x_catalog_path or defaults["x_catalog"],
    }
    if member_rows is None:
        member_rows, member_receipt = _load_member_rows(paths["member_roster"])
    else:
        member_rows = list(member_rows)
        member_receipt = {
            "status": "injected", "input_rows": len(member_rows),
            "columns_consumed": ["member", "bioguide", "chamber"],
        }
    members, member_by_name = _member_indexes(member_rows)
    candidates, generic_exclusions, merged_candidates = build_candidates()
    accumulator = _Accumulator(candidates, members)
    receipts = {}

    if documents_by_channel is not None:
        unknown = set(documents_by_channel) - set(CHANNELS)
        if unknown:
            raise OrgMentionsError(f"unsupported injected channels: {', '.join(sorted(unknown))}")
        for channel in CHANNELS:
            documents = list(documents_by_channel.get(channel, ()))
            receipt = dict((channel_receipts or {}).get(channel, {}))
            receipt.setdefault("status", "injected" if channel in documents_by_channel else "skipped")
            receipt.setdefault("channel", channel)
            receipt.setdefault("input_rows", len(documents))
            receipt.setdefault("documents", len(documents))
            receipt.setdefault("global_date_floor", GLOBAL_DATE_FLOOR)
            receipt.setdefault("date_window", {"min": None, "max": None})
            for document in documents:
                accumulator.consume(channel, document)
            window = accumulator.channel_windows[channel]
            if window[0] is not None:
                receipt["date_window"] = {"min": window[0], "max": window[1]}
            receipts[channel] = receipt
        hearing_receipt = {
            "status": "excluded", "source_discriminator": "CHRG", "input_rows": 0,
            "reason": "finance-committee-only corpus is a biased denominator",
            "vintage": "2026-02-10",
        }
    else:
        mapping_missing = member_receipt.get("status") == "skipped"
        tweet_source = (
            _skipped("tweet", "member roster absent", [paths["member_roster"]])
            if mapping_missing else _tweet_documents(paths["quorum"], member_by_name)
        )
        floor_source = (
            _skipped("floor", "member roster absent", [paths["member_roster"]])
            if mapping_missing
            else _speech_documents(paths["social_db"], "CREC", "floor", member_by_name)
        )
        interview_source = (
            _skipped("interview", "member roster absent", [paths["member_roster"]])
            if mapping_missing
            else _speech_documents(
                paths["social_db"], "INTERVIEW", "interview", member_by_name
            )
        )
        sources = {
            "press": _press_documents(paths, member_by_name),
            "tweet": tweet_source,
            "floor": floor_source,
            "interview": interview_source,
            "bluesky": _bluesky_documents(paths["social_db"]),
            "x": _x_documents(paths["x_catalog"], members),
        }
        for channel in CHANNELS:
            documents, receipt = sources[channel]
            for document in documents:
                accumulator.consume(channel, document)
            window = accumulator.channel_windows[channel]
            if window[0] is not None and receipt.get("date_window", {}).get("min") is None:
                receipt["date_window"] = {"min": window[0], "max": window[1]}
            receipts[channel] = receipt
        hearing_receipt = _hearing_exclusion(paths["social_db"])

    press_receipt = receipts.get("press") or {}
    press_refresh_rows = int(((press_receipt.get("text_rows") or {}).get(
        "press_refresh_cache") or 0))
    press_vintage = "2026-06-30 tone vintage / 2026-07-04 fetch"
    if press_refresh_rows:
        press_vintage += " + hash-bound audited press refresh through %s" % (
            (press_receipt.get("date_window") or {}).get("max") or "unknown"
        )

    payload = {
        "schema": SCHEMA,
        "method_note": METHOD_NOTE,
        "members": accumulator.payload_members(),
        "orgs": accumulator.payload_orgs(),
        "receipt": {
            "method_note": METHOD_NOTE,
            "channels": receipts,
            "excluded_channels": {"hearing": hearing_receipt},
            "member_roster": member_receipt,
            "global_date_floor": GLOBAL_DATE_FLOOR,
            "counting_unit": "documents naming, not raw occurrences",
            "merged_candidates": merged_candidates,
            "minted_aliases": _minted_alias_receipt(candidates),
            "strict_tier_b": sorted(STRICT_TIER_B),
            "residual_precision": {
                **RESIDUAL_PRECISION,
                "known_noise_floor": "~10 accepted-false documents artifact-wide",
            },
            "families": _family_receipt(candidates),
            "family_map": dict(sorted(FAMILY_MAP.items())),
            "roster_members_without_documents": len(
                accumulator.roster_bioguides - accumulator.members_with_documents
            ),
            "delegate_policy_asymmetry": {
                "x_excluded_bioguides": list(NON_VOTING_DELEGATES),
                "press_included_bioguide": "M001219",
                "note": (
                    "Guam delegate M001219 may enter through press while X excludes "
                    "three delegates; policy change remains Paul's gate."
                ),
            },
            "channel_order": list(CHANNELS),
            "channels_never_summed": (
                "No field sums channels. Selection order uses the largest single-channel "
                "count, with deterministic name/ID tie-breaks."
            ),
            "passage_rule": (
                "Sentence-snapped passages are used for exact matching; each document "
                "contributes at most one count per organization and one topical count "
                "per sector."
            ),
            "by_org_limit_per_member": BY_ORG_LIMIT,
            "top_members_per_org_channel": TOP_MEMBER_LIMIT,
            "tier_assignment": _tier_receipt(candidates),
            "tier_b_dictionary_words": sorted(ENGLISH_DICTIONARY_WORDS),
            "tier_b_qualifier_rule": QUALIFIER_RE.pattern,
            "tier_b_corroboration_rule": (
                "A Tier-B alias requires a qualifier within three tokens, another "
                "alias of the same organization anywhere in the document, or another "
                "matched organization from the same sector in the same sentence. The "
                "matched token cannot corroborate itself; BLOCK followed by an article "
                "or demonstrative is verb use for qualifier-only support. Bare tokens "
                "in strict_tier_b accept only when the document also matches a "
                "non-strict alias from the same family; two bare strict aliases cannot "
                "corroborate each other, and qualifier and same-sector-anchor support "
                "are denied."
            ),
            "exclusions": {
                "generic_roster_patterns": generic_exclusions,
                "known_held_ambiguous_not_in_authorities": list(KNOWN_HELD_AMBIGUOUS),
                "held_policy": (
                    "Names outside the imported authorities are never candidates; Kalshi "
                    "is retained as pinned and Tier A."
                ),
                "non_voting_delegate_bioguides": list(NON_VOTING_DELEGATES),
            },
            "unmatched_org_note": (
                "Authority-only exact matching: no fuzzy organization candidates are "
                "minted; every alias not equal to a normalized source authority name is "
                "enumerated in minted_aliases, and unmatched public text remains unmatched."
            ),
            "artifact_vintages": {
                "press": press_vintage,
                "tweet": "frozen 2026-07-01",
                "x": "2026-07-16..2026-07-26 tiny collection window",
                "excluded_hearing": "2026-02-10",
            },
        },
    }
    _assert_channels_never_summed(payload)
    return payload


def _write_json_atomic(payload, path):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    temporary = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    return len(body.encode("utf-8"))


def _csv_rows(payload):
    rows = []
    for bioguide, member in sorted(payload["members"].items()):
        for org in member["by_org"]:
            for channel, values in sorted(org["channels"].items()):
                rows.append(
                    {
                        "row_type": "organization", "bioguide": bioguide,
                        "name": member["name"], "chamber": member["chamber"],
                        "sector": org["sector"], "org": org["org"],
                        "lob_id": org.get("lob_id", ""), "tier": org["tier"],
                        "channel": channel, "year": "", "mentions": values["mentions"],
                        "first": values["first"], "last": values["last"],
                    }
                )
        for sector, channels in sorted(member["topical"].items()):
            for channel, years in sorted(channels.items()):
                for year, count in sorted(years.items()):
                    rows.append(
                        {
                            "row_type": "topical", "bioguide": bioguide,
                            "name": member["name"], "chamber": member["chamber"],
                            "sector": sector, "org": "", "lob_id": "", "tier": "",
                            "channel": channel, "year": year, "mentions": count,
                            "first": "", "last": "",
                        }
                    )
    return rows


def _write_csv_atomic(payload, path):
    fields = (
        "row_type", "bioguide", "name", "chamber", "sector", "org",
        "lob_id", "tier", "channel", "year", "mentions", "first", "last",
    )
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    temporary = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(_csv_rows(payload))
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def main(argv=None):
    defaults = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for option, key in (
        ("--member-roster", "member_roster"), ("--press-spine", "press_spine"),
        ("--press-cache", "press_cache"), ("--press-priority", "press_priority"),
        ("--press-debate", "press_debate"), ("--quorum", "quorum"),
        ("--social-db", "social_db"), ("--x-catalog", "x_catalog"),
    ):
        parser.add_argument(option, default=defaults[key])
    output_dir = os.path.join(_derived_dir(), "org_mentions")
    parser.add_argument("--out", default=os.path.join(output_dir, "mentions.json"))
    parser.add_argument("--csv", default=os.path.join(output_dir, "mentions.csv"))
    args = parser.parse_args(argv)
    payload = build_payload(
        member_roster_path=args.member_roster,
        press_spine_path=args.press_spine,
        press_cache_path=args.press_cache,
        press_priority_path=args.press_priority,
        press_debate_path=args.press_debate,
        quorum_path=args.quorum,
        social_db_path=args.social_db,
        x_catalog_path=args.x_catalog,
    )
    payload["generated_utc"] = datetime.now(timezone.utc).isoformat()
    _assert_channels_never_summed(payload)
    size = _write_json_atomic(payload, args.out)
    _write_csv_atomic(payload, args.csv)
    print(
        "org mentions: %d members | %d orgs | %d bytes -> %s"
        % (len(payload["members"]), len(payload["orgs"]), size, os.path.abspath(args.out))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
