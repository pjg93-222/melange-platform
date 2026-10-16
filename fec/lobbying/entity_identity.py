# -*- coding: utf-8 -*-
"""Canonical organization identity for the LDA lane: renames, mergers, aliases.

WHY THIS EXISTS
---------------
Every roster in this project is name-pattern based.  An organization that
renames therefore appears as TWO entities and its time series breaks at the
boundary.  This module supplies one auditable identity table plus a resolver
that other modules import, so that a rename collapses and a merger does not.

THE DISTINCTION THAT DRIVES THE MATH
------------------------------------
``rename``      One legal entity, new name.  The series is CONTINUOUS across
                the boundary.  Every name variant collapses to one entity for
                ALL time; there is no date gate.
``dba_alias``   A trading name for the same entity.  No date boundary at all.
``merger``      Two or more entities become one.  This is NEVER collapsed
                backward.  Before the effective date the predecessors really
                did both exist and both lobbied, so folding them together
                pre-boundary would fabricate a series that never existed.
``acquisition`` Same date-gated treatment as a merger.

The resolver is consequently TIME-AWARE.  ``resolve(name, as_of=2022)``
returns the entity as it existed in 2022; ``resolve(name, as_of=2025)``
returns the successor.  Renames and DBA aliases ignore ``as_of`` because one
entity is one entity throughout.  Only ``merger`` and ``acquisition`` edges
are date-gated.  This is "collapse forward": nothing is invented backward and
the merger reads as the real event it was.

Calling ``resolve`` with ``as_of=None`` returns the LATEST regime and sets
``spans_boundary`` on the result when that answer crossed a merger edge.  A
caller that aggregates across such a boundary must say so; see
``constituent_breakdown``.

TWO SOURCES OF EVIDENCE, AND THEY ARE LABELLED DIFFERENTLY
----------------------------------------------------------
``filing_declared``  The filer declared its own prior name inside the
                     ``client.name`` or ``registrant.name`` string ("TRUIST
                     FINANCIAL CORPORATION (FORMERLY KNOWN AS BB&T)").  This
                     is the primary source: the entity is describing its own
                     identity.  ``evidence`` is the verbatim filing string.
``researched``       Established outside the filings, because the filings do
                     not declare it.  ``evidence`` names the source.
``observed_variant`` Punctuation/abbreviation variants of one filer's name,
                     folded by explicit adjudication.  Also researched in the
                     sense that a human decided; never inferred by fuzzy match.

The researched set is NOT exhaustive.  It catches the renames and mergers we
know about; it does not prove there are no others.  ``unresolved_client_names``
in the built artifact bounds the residual error.

A NECESSARY FACT ABOUT THE SOURCE DATA
--------------------------------------
The LDA API returns each client record's CURRENT name, applied retroactively
to every filing of that record: across 2021-2026 exactly zero client ids carry
more than one name, while 6,086 names span more than one client id.  So a
renamed organization does not show up as one record changing name over time.
It shows up as SEVERAL records, each frozen at whatever name it carried when
it was last touched.  That is the mechanism behind the split this module fixes,
and it is why a filer's self-declaration inside the name string is the only
in-corpus rename evidence available.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

RELATIONS = ("rename", "merger", "dba_alias", "acquisition")
DATE_GATED_RELATIONS = ("merger", "acquisition")
CONFIDENCE_LEVELS = ("filing_declared", "researched", "observed_variant")
BANK_TIERS = ("big", "community", "mixed", "unclassified")
DEFAULT_YEARS = (2021, 2022, 2023, 2024, 2025, 2026)
ARTIFACT_NAME = "entity_identity.json"
SCHEMA_VERSION = 1

# A merger effective on or before July 1 governs that filing year; one
# effective after it governs the next.  Stated as a rule so the transition
# year is never a silent judgement call.  An entry may override with
# ``effective_filing_year`` when the evidence names a different first year.
MIDYEAR_CUTOVER_MONTH_DAY = (7, 1)


class EntityIdentityError(RuntimeError):
    """Raised when the identity table is malformed or self-contradictory."""


# ---------------------------------------------------------------------------
# Normalization.  Byte-identical to sector_spend.normalize_name; the agreement
# is asserted by fec/tests and by _assert_normalization_agrees below.
# ---------------------------------------------------------------------------


def normalize_name(value):
    """Return the punctuation-stable uppercase form used by every roster."""
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.replace("&", " AND ").replace("’", "'").replace("`", "'")
    text = re.sub(r"[^A-Z0-9']+", " ", text.upper())
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Filer self-declaration parsing.
# ---------------------------------------------------------------------------

# Ordered: longer spellings first so "FORMERLY KNOWN AS" is not shortened to
# "FORMERLY" with a dangling "KNOWN AS" in the prior name.
_MARKER_ALTERNATIVES = (
    (r"FORMERLY\s+KNOWN\s+AS", "rename"),
    (r"FORMALLY\s+KNOWN\s+AS", "rename"),
    (r"FORMERLY\s+DOING\s+BUSINESS\s+AS", "rename"),
    (r"FORMERLY", "rename"),
    (r"F\s*[/.]\s*K\s*[/.]?\s*A\.?", "rename"),
    (r"FKA\.?", "rename"),
    (r"N\s*[/.]\s*K\s*[/.]?\s*A\.?", "rename_forward"),
    (r"NKA\.?", "rename_forward"),
    (r"SUCCESSOR\s+(?:IN\s+INTEREST\s+)?TO", "acquisition"),
    (r"SUCCESSOR", "acquisition"),
    (r"MERGED\s+(?:IN)?TO", "merger"),
    (r"MERGED\s+WITH", "merger"),
    (r"DOING\s+BUSINESS\s+AS", "dba_alias"),
    (r"D\s*[/.]\s*B\s*[/.]?\s*A\.?", "dba_alias"),
    (r"DBA\.?", "dba_alias"),
)
_MARKER_RE = re.compile(
    r"(?<![A-Z0-9])(?:"
    + "|".join("(?P<m%d>%s)" % (index, pattern) for index, (pattern, _kind) in enumerate(_MARKER_ALTERNATIVES))
    + r")(?![A-Z0-9])",
    re.IGNORECASE,
)
_MARKER_KINDS = {"m%d" % index: kind for index, (_pattern, kind) in enumerate(_MARKER_ALTERNATIVES)}


def has_declaration(raw_name):
    """True when the filer put a rename/DBA/successor marker in its own name."""
    return _MARKER_RE.search("" if raw_name is None else str(raw_name)) is not None


def _trim_fragment(text):
    """Strip the punctuation that brackets a declaration fragment."""
    text = re.sub(r"^[\s,;:.\-–—()\[\]]+", "", text)
    text = re.sub(r"[\s,;:.\-–—()\[\]]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_declaration(raw_name):
    """Split a self-declaring filing name into its declared identity parts.

    Returns ``{"primary": str, "parts": [{"relation", "name", "marker"}...]}``.
    ``primary`` is the text before the first marker: the name the filer is
    presenting as current.  Each part is a name the filer attached to itself
    with the given relation.  ``rename`` parts are PRIOR names; the rare
    ``N/K/A`` form declares a FORWARD rename and is normalized here by swapping
    it, so every emitted ``rename`` part is a prior name and ``primary`` is
    always the current one.

    This is mechanical.  It never decides which legal entity a name belongs to;
    that adjudication lives in the researched layer.
    """
    text = "" if raw_name is None else str(raw_name).strip()
    matches = list(_MARKER_RE.finditer(text))
    if not matches:
        return {"primary": _trim_fragment(text), "parts": []}
    primary = _trim_fragment(text[: matches[0].start()])
    parts = []
    for index, match in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fragment = _trim_fragment(text[match.end() : stop])
        if not fragment:
            continue
        kind = next(_MARKER_KINDS[name] for name in _MARKER_KINDS if match.group(name))
        parts.append(
            {
                "relation": kind,
                "name": fragment,
                "marker": _trim_fragment(match.group(0)).upper(),
            }
        )
    # N/K/A declares "now known as": the fragment is the CURRENT name and the
    # text before the marker is the prior one.  Swap so the contract holds.
    forward = [part for part in parts if part["relation"] == "rename_forward"]
    if forward and primary:
        new_primary = forward[0]["name"]
        rewritten = [{"relation": "rename", "name": primary, "marker": forward[0]["marker"]}]
        for part in parts:
            if part["relation"] == "rename_forward":
                continue
            rewritten.append(part)
        primary, parts = new_primary, rewritten
    else:
        for part in parts:
            if part["relation"] == "rename_forward":
                part["relation"] = "rename"
    return {"primary": primary, "parts": parts}


# ---------------------------------------------------------------------------
# The researched layer.
#
# Everything below is established OUTSIDE the filings, because the filings do
# not declare it.  Every entry carries its evidence.  This set is not
# exhaustive and is not claimed to be.
# ---------------------------------------------------------------------------

RESEARCHED_ENTITIES = (
    # -- credit union trade associations ----------------------------------
    {
        "entity_id": "CUNA",
        "display_name": "Credit Union National Association",
        "sector_hint": "credit_union",
        "names": (
            "CREDIT UNION NATIONAL ASSOCIATION",
            "CREDIT UNION NATIONAL ASSOCIATION, INC.",
            "CUNA",
        ),
        "confidence": "researched",
        "evidence": (
            "CUNA is the legal entity Credit Union National Association, Inc. "
            "It is a distinct organization from CUNA Mutual Group / TruStage, "
            "which is a credit-union-owned insurer and not the trade association."
        ),
    },
    {
        "entity_id": "NAFCU",
        "display_name": "National Association of Federally-Insured Credit Unions",
        "sector_hint": "credit_union",
        "names": (
            "NATIONAL ASSOCIATION OF FEDERALLY-INSURED CREDIT UNIONS",
            "NATIONAL ASSOCIATION OF FEDERAL CREDIT UNIONS",
            "NAFCU",
        ),
        "confidence": "researched",
        "evidence": (
            "RENAME 2017: 'National Association of Federal Credit Unions' became "
            "'National Association of Federally-Insured Credit Unions' when the "
            "association opened membership to federally-insured state-chartered "
            "credit unions. One legal entity throughout; the filings carry no "
            "marker for this change, so it is researched, not filing-declared."
        ),
    },
    {
        "entity_id": "AMERICAS_CREDIT_UNIONS",
        "display_name": "America's Credit Unions",
        "sector_hint": "credit_union",
        "names": ("AMERICA'S CREDIT UNIONS", "AMERICAS CREDIT UNIONS"),
        "confidence": "researched",
        "evidence": "Successor trade association formed by the CUNA/NAFCU merger.",
    },
    {
        "entity_id": "CUNA_MUTUAL_TRUSTAGE",
        "display_name": "TruStage",
        "sector_hint": "credit_union",
        "names": ("CUNA MUTUAL GROUP", "TRUSTAGE", "CUNA MUTUAL"),
        "confidence": "filing_declared",
        "evidence": "TRUSTAGE (FKA CUNA MUTUAL GROUP)",
        "note": (
            "SEPARATE ENTITY from CUNA the trade association. The pre-existing "
            "roster alias collapsed any name containing the token CUNA into "
            "America's Credit Unions, which silently merged this insurer's "
            "filings into the trade association's family."
        ),
    },
    # -- credit union leagues ---------------------------------------------
    {
        "entity_id": "NWCUA",
        "display_name": "Northwest Credit Union Association",
        "sector_hint": "credit_union",
        "names": ("NORTHWEST CREDIT UNION ASSOCIATION", "NWCUA"),
        "confidence": "researched",
        "evidence": "Idaho/Oregon/Washington league; predecessor of GoWest.",
    },
    {
        "entity_id": "MWCUA",
        "display_name": "Mountain West Credit Union Association",
        "sector_hint": "credit_union",
        "names": ("MOUNTAIN WEST CREDIT UNION ASSOCIATION", "MWCUA"),
        "confidence": "researched",
        "evidence": "Arizona/Colorado/Wyoming league; predecessor of GoWest.",
    },
    {
        "entity_id": "GOWEST",
        "display_name": "GoWest Credit Union Association",
        "sector_hint": "credit_union",
        "names": ("GOWEST CREDIT UNION ASSOCIATION", "GOWEST"),
        "confidence": "researched",
        "evidence": "Successor league to NWCUA + MWCUA, six states.",
    },
    {
        "entity_id": "PCUA",
        "display_name": "Pennsylvania Credit Union Association",
        "sector_hint": "credit_union",
        "names": ("PENNSYLVANIA CREDIT UNION ASSOCIATION", "PCUA"),
        "confidence": "researched",
        "evidence": "Predecessor of CrossState.",
    },
    {
        "entity_id": "NJCUL",
        "display_name": "New Jersey Credit Union League",
        "sector_hint": "credit_union",
        "names": ("NEW JERSEY CREDIT UNION LEAGUE", "NJCUL"),
        "confidence": "researched",
        "evidence": "Predecessor of CrossState.",
    },
    {
        "entity_id": "CROSSSTATE",
        "display_name": "CrossState Credit Union Association",
        "sector_hint": "credit_union",
        "names": ("CROSSSTATE CREDIT UNION ASSOCIATION", "CROSS STATE CREDIT UNION ASSOCIATION"),
        "confidence": "researched",
        "evidence": "Successor association to PCUA + NJCUL.",
    },
    {
        "entity_id": "HEARTLAND_CUA",
        "display_name": "Heartland Credit Union Association",
        "sector_hint": "credit_union",
        "names": ("HEARTLAND CREDIT UNION ASSOCIATION", "MISSOURI CREDIT UNION ASSOCIATION"),
        "confidence": "filing_declared",
        "evidence": "HEARTLAND CREDIT UNION ASSOCIATION (FKA MISSOURI CREDIT UNION ASSOCIATION)",
        "note": (
            "The Missouri association combined with Kansas to form Heartland "
            "before the 2021 window opens, and the filer declares the change as "
            "a rename of its own record, so it is treated as one entity here."
        ),
    },
    {
        "entity_id": "CORNERSTONE_LEAGUE",
        "display_name": "Cornerstone League",
        "sector_hint": "credit_union",
        "names": ("CORNERSTONE LEAGUE", "CORNERSTONE CREDIT UNION LEAGUE"),
        "confidence": "researched",
        "evidence": "Texas/Oklahoma/Arkansas league; successor after the Heartland merger.",
    },
    {
        "entity_id": "LSCU",
        "display_name": "League of Southeastern Credit Unions & Affiliates",
        "sector_hint": "credit_union",
        "names": (
            "LEAGUE OF SOUTHEASTERN CREDIT UNIONS",
            "LEAGUE OF SOUTHEASTERN CREDIT UNIONS AND AFFILIATES",
            "LSCU",
        ),
        "confidence": "researched",
        "evidence": "Alabama/Florida/Georgia league; predecessor of The League of Credit Unions & Affiliates.",
    },
    {
        "entity_id": "VACUL",
        "display_name": "Virginia Credit Union League",
        "sector_hint": "credit_union",
        "names": ("VIRGINIA CREDIT UNION LEAGUE", "VACUL"),
        "confidence": "researched",
        "evidence": "Predecessor of The League of Credit Unions & Affiliates.",
    },
    {
        "entity_id": "THE_LEAGUE_CU_AFFILIATES",
        "display_name": "The League of Credit Unions & Affiliates",
        "sector_hint": "credit_union",
        "names": ("THE LEAGUE OF CREDIT UNIONS AND AFFILIATES", "LEAGUE OF CREDIT UNIONS AND AFFILIATES"),
        "confidence": "researched",
        "evidence": "Successor league to LSCU + VACUL covering AL/FL/GA/VA.",
    },
    {
        "entity_id": "CA_NV_CU_LEAGUES",
        "display_name": "California and Nevada Credit Union Leagues",
        "sector_hint": "credit_union",
        "names": (
            "CALIFORNIA AND NEVADA CREDIT UNION LEAGUES",
            "CALIFORNIA AND NEVADA CREDIT UNION LEAGUE",
            "CALIFORNIA/NEVADA CREDIT UNION LEAGUE",
            "CALIFORNIA CREDIT UNION LEAGUE",
            "NEVADA CREDIT UNION LEAGUE",
        ),
        "confidence": "observed_variant",
        "evidence": (
            "Four filing name variants observed 2021-2026 across separate client "
            "records: 'CALIFORNIA AND NEVADA CREDIT UNION LEAGUES', 'CALIFORNIA "
            "AND NEVADA CREDIT UNION LEAGUE', 'CALIFORNIA/NEVADA CREDIT UNION "
            "LEAGUE', 'CALIFORNIA CREDIT UNION LEAGUE'. The California and Nevada "
            "leagues are affiliated and file jointly; folded by adjudication, not "
            "by fuzzy match."
        ),
    },
    {
        "entity_id": "MD_DC_CUA",
        "display_name": "MD|DC Credit Union Association",
        "sector_hint": "credit_union",
        "names": (
            "MARYLAND AND DISTRICT OF COLUMBIA CREDIT UNION ASSOCIATION",
            "MD DC CREDIT UNION ASSOCIATION",
            "MARYLAND/DC CREDIT UNION ASSOCIATION",
            "MD|DC CREDIT UNION ASSOCIATION",
        ),
        "confidence": "observed_variant",
        "evidence": (
            "Three filing name variants observed 2021-2026 across separate client "
            "records: 'MARYLAND & DISTRICT OF COLUMBIA CREDIT UNION ASSOCIATION', "
            "'MD|DC CREDIT UNION ASSOCIATION', 'MARYLAND/DC CREDIT UNION "
            "ASSOCIATION'. Same association, punctuation variants only."
        ),
    },
    # -- banks -------------------------------------------------------------
    {
        "entity_id": "SVB",
        "display_name": "SVB Financial Group (Silicon Valley Bank)",
        "sector_hint": "bank",
        "names": ("SVB FINANCIAL GROUP", "SILICON VALLEY BANK", "SVB"),
        "confidence": "researched",
        "evidence": (
            "SVB Financial Group is the holding company of Silicon Valley Bank. "
            "The holding-company name carries no 'BANK' token, so the pinned bank "
            "roster patterns never saw it."
        ),
    },
    {
        "entity_id": "CITIGROUP",
        "display_name": "Citigroup",
        "sector_hint": "bank",
        "names": ("CITIGROUP", "CITIBANK", "CITIGROUP WASHINGTON, INC.", "CITIGROUP WASHINGTON INC", "CITI"),
        "confidence": "observed_variant",
        "evidence": (
            "Four filing name variants observed 2021-2026: 'CITIGROUP WASHINGTON, "
            "INC.', 'CITIGROUP WASHINGTON INC.', 'CITIGROUP WASHINGTON INC', "
            "'CITIGROUP WASHINGTON, INC'. Citigroup Washington, Inc. is Citigroup's "
            "government-affairs subsidiary."
        ),
    },
)

RESEARCHED_EVENTS = (
    {
        "event_id": "ACU_2024",
        "relation": "merger",
        "predecessors": ("CUNA", "NAFCU"),
        "successor": "AMERICAS_CREDIT_UNIONS",
        "effective_date": "2024-01-01",
        "confidence": "researched",
        "evidence": (
            "CUNA and NAFCU merged to form America's Credit Unions. Announced "
            "2023-08-01, EFFECTIVE 2024-01-01; both predecessors continued filing "
            "separately under the ACU brand through 2024 and the combined "
            "association was fully operational in 2025. Corroborated in this "
            "corpus: NAFCU's last filing quarter under its own name is 2023Q4."
        ),
    },
    {
        "event_id": "GOWEST_2022",
        "relation": "merger",
        "predecessors": ("NWCUA", "MWCUA"),
        "successor": "GOWEST",
        "effective_date": "2022-06-30",
        "confidence": "researched",
        "evidence": (
            "Northwest Credit Union Association (ID/OR/WA) and Mountain West "
            "Credit Union Association (AZ/CO/WY) combined as GoWest Credit Union "
            "Association effective 2022-06-30."
        ),
    },
    {
        "event_id": "CROSSSTATE_2020",
        "relation": "merger",
        "predecessors": ("PCUA", "NJCUL"),
        "successor": "CROSSSTATE",
        "effective_date": "2020-01-01",
        "confidence": "researched",
        "evidence": (
            "Pennsylvania Credit Union Association and New Jersey Credit Union "
            "League combined as CrossState Credit Union Association in 2020, "
            "before this corpus window opens."
        ),
    },
    {
        "event_id": "CORNERSTONE_HEARTLAND_2022",
        "relation": "merger",
        "predecessors": ("CORNERSTONE_LEAGUE", "HEARTLAND_CUA"),
        "successor": "CORNERSTONE_LEAGUE",
        "effective_date": "2022-12-31",
        "effective_filing_year": 2023,
        "confidence": "researched",
        "evidence": (
            "COMPLETED, not pending: verified 2026-08-03. Members of both "
            "organizations voted to merge in October 2022 (93% in favour); "
            "Heartland Credit Union Association merged into Cornerstone League "
            "on 2022-12-31, and the combined five-state league (AR/KS/MO/OK/TX) "
            "began operating as Cornerstone League on 2023-01-01. Sources: "
            "cutimes.com 2022-10-31; cuinsight.com 'Cornerstone League welcomed "
            "2023 as newly combined organization'; cutoday.info 'Cornerstone, "
            "Heartland CU Associations Merger Complete'."
        ),
    },
    {
        "event_id": "THE_LEAGUE_2025",
        "relation": "merger",
        "predecessors": ("LSCU", "VACUL"),
        "successor": "THE_LEAGUE_CU_AFFILIATES",
        "effective_date": "2025-01-01",
        "confidence": "researched",
        "evidence": (
            "Discovered in this corpus and then verified: the League of "
            "Southeastern Credit Unions & Affiliates and the Virginia Credit "
            "Union League consolidated after a November 2024 member vote; the "
            "combined organization was unveiled as The League of Credit Unions & "
            "Affiliates on 2025-01-02, covering AL/FL/GA/VA. Corroborated here: "
            "the Virginia Credit Union League's last filing year is 2024 and the "
            "successor name first appears in 2025. Not listed in the original "
            "brief. Verified 2026-08-03."
        ),
    },
)


# ---------------------------------------------------------------------------
# Bank tier.  A SEPARATE AXIS from sector: `bank_tier` never changes which
# organizations are in the `bank` sector, it only labels the ones already
# there big / community / mixed / unclassified.  The `bank` sector is NOT
# split into two sectors, because that would silently redefine numbers that
# have already been published.
#
# The tier is resolvable AS OF a year and composes with the time-aware
# resolver: the name is first resolved to its canonical entity in that year,
# then the roster is applied with that year's membership window.
# ---------------------------------------------------------------------------

BANK_TIER_THRESHOLD_NOTE = (
    "The politically salient community/large boundary is $10 BILLION in total "
    "assets: the CFPB's supervision authority and the Durbin amendment's "
    "interchange exemption both switch there, so it is the line that decides "
    "who is regulated as large. It is documented here as THE POLICY LINE. Two "
    "caveats travel with it. (1) ICBA does not define its own membership by "
    "it. (2) LDA filings carry names, not balance sheets, so no threshold can "
    "be applied directly; the operational test is the named roster below, "
    "whose own inclusion lines are the BPI board, the FSB G-SIB list and the "
    "Federal Reserve's $100bn tailoring categories. Those sit an order of "
    "magnitude ABOVE the $10bn policy line, so banks between $10bn and $100bn "
    "are 'large' for policy purposes but are NOT on this roster and fall into "
    "`community` by default. See the defaulted counts in the output."
)

BPI_MEMBERSHIP_GAP_NOTE = (
    "BPI DOES NOT PUBLISH ITS MEMBERSHIP. The membership section of bpi.com is "
    "empty in the live page (retrieved 2026-08-03) and a 2023 archived capture "
    "is equally blank; BPI's own FAQ puts the count at about 40 members. What "
    "IS published is the board of directors, which names the institution each "
    "director represents - 19 institutions currently, plus 6 more on the 2023 "
    "board. The board is a STRICT SUBSET of the membership, so roughly 15 BPI "
    "members are unnamed by any vouchable source. This roster therefore "
    "UNDER-identifies big banks rather than over-identifying them, and the "
    "residual lands in the defaulted-community bucket where it is counted."
)

BARRED_SOURCE_NOTE = (
    "WIKIPEDIA'S 42-NAME BPI MEMBER LIST IS BARRED and must not be reached for "
    "later. Its sole citation is an OpenSecrets entry for the 'Financial "
    "Services Roundtable, 1998' - two decades before BPI existed - and the list "
    "still carries Silicon Valley Bank (failed March 2023) and Discover "
    "(acquired by Capital One 2025). It is not evidence of current BPI "
    "membership. The ~15-member gap described above is a REAL gap and is to be "
    "reported as one, not closed with this source."
)

ROSTER_NESTING_NOTE = (
    "BPI MEMBERSHIP AND THE $100BN LIST ARE NOT NESTED, in either direction. "
    "First Horizon sits on BPI's board at roughly $84bn in assets, and Zions "
    "likewise sits on the board below $100bn; neither appears in the Federal "
    "Reserve's Category I-IV table. Conversely most of the 32 stress-tested "
    "firms are not on BPI's board. A bank can be politically big without being "
    "big by balance sheet, and vice versa. The classifier therefore takes the "
    "UNION of the sourced lists and never assumes one subsumes the other."
)

# Every roster source is declared here and every roster entry cites one or
# more of these keys, so each name can be traced to where it came from.
BANK_TIER_SOURCES = {
    "bpi_board_current": {
        "label": "Bank Policy Institute board of directors, current",
        "source": "https://bpi.com/about-us/ (rendered DOM)",
        "retrieved": "2026-08-03",
        "kind": "membership_governance",
        "note": (
            "19 institutions, each named as the employer of a sitting director. "
            "BPI describes its members as 'universal banks, regional banks and "
            "the major foreign banks doing business in the United States'. The "
            "page names no general members; see BPI_MEMBERSHIP_GAP_NOTE."
        ),
    },
    "bpi_board_2023": {
        "label": "Bank Policy Institute board of directors, 2023 archived capture",
        "source": "Archived capture of the BPI board roster, 2023",
        "retrieved": "2026-08-03",
        "kind": "membership_governance",
        "note": (
            "Contributes only the 6 institutions that held a board seat in 2023 "
            "and do not hold one now. Board turnover removes a director, not a "
            "membership, so these remain sourced big-bank identifications."
        ),
    },
    "fsb_gsib_2025": {
        "label": "US global systemically important banks",
        "source": "Financial Stability Board G-SIB list, published 2025-11-27",
        "retrieved": "2026-08-03",
        "kind": "regulatory_designation",
        "note": "The 8 US institutions on the FSB's 2025 G-SIB list.",
    },
    "frb_stress_2026_t3": {
        "label": "Federal Reserve 2026 stress test, Table 3 (Category I-IV firms)",
        "source": "Federal Reserve 2026 supervisory stress-test disclosure, Table 3",
        "retrieved": "2026-08-03",
        "kind": "regulatory_designation",
        "note": (
            "32 holding companies at or above $100bn in assets, including the "
            "US intermediate holding companies of foreign banking organizations."
        ),
    },
    "frb_large_bhc_exited": {
        "label": "Large holding company that ceased to exist inside the window",
        "source": "Federal Reserve large holding company release (NIC); FDIC receivership records",
        "retrieved": "2026-08-03",
        "kind": "regulatory_designation_historic",
        "note": (
            "Institutions that were above the $100bn tailoring line while they "
            "filed, and are absent from the 2026 table because they no longer "
            "exist - NOT because they shrank. Each carries an explicit end year "
            "so the tier is correct on both sides of the boundary. This is the "
            "reason the tier has to be as-of-aware."
        ),
    },
}

# Trade associations by known identity, each with its own provenance.
# ABA and CBA are both MIXED, deliberately; see the evidence strings.
ASSOCIATION_TIERS = (
    (
        r"\bBANK POLICY INSTITUTE\b|\bFINANCIAL SERVICES ROUNDTABLE\b|\bCLEARING HOUSE ASSOCIATION\b",
        "big",
        "named_trade_association_big",
        (
            "BPI represents 'universal banks, regional banks and the major "
            "foreign banks doing business in the United States' (bpi.com/about-us, "
            "retrieved 2026-08-03). The Financial Services Roundtable and The "
            "Clearing House Association are the two predecessor bodies that "
            "combined to form BPI in 2018."
        ),
    ),
    (
        r"\bINDEPENDENT COMMUNITY BANKERS\b|\bICBA\b",
        "community",
        "named_trade_association_community",
        (
            "ICBA is explicitly the community-bank trade association, "
            "representing roughly 5,000 small and mid-sized institutions. Note "
            "that ICBA does NOT define its membership by the $10bn policy line."
        ),
    ),
    (
        r"\bCONSUMER BANKERS ASSOCIATION\b",
        "mixed",
        "named_trade_association_mixed",
        (
            "DELIBERATELY MIXED. CBA describes itself as 'the only member-driven "
            "trade association focused exclusively on retail banking' with 71 "
            "member institutions (consumerbankers.com/about/membership, retrieved "
            "2026-08-03). That is a BUSINESS-LINE statement, not a size "
            "statement: CBA publishes no asset floor and no membership criteria, "
            "and 71 members is too broad to read as exclusively large. `big` was "
            "considered and REJECTED because no sourced reason for it could be "
            "stated; where a reason cannot be stated the honest tier is mixed."
        ),
    ),
    (
        r"\bAMERICAN BANKERS ASSOCIATION\b|\bABA\b",
        "mixed",
        "named_trade_association_mixed",
        (
            "ABA genuinely represents both large and community institutions. "
            "DELIBERATELY UNASSIGNED: forcing it into either bucket would be a "
            "fabrication, and ABA is large enough that a wrong assignment would "
            "visibly distort BOTH series at once."
        ),
    ),
)

# State, regional and community-banking associations.  Applied only after the
# named national associations above and after the national-association guard,
# so that a national body is never swept in by the generic pattern.
COMMUNITY_ASSOCIATION_PATTERNS = (
    (
        r"\bCOMMUNITY BANKERS\b|\bCOMMUNITY BANKING\b",
        "named_community_banking_association",
        "Self-declared community-banking association.",
    ),
    (
        r"\bBANKERS ASSOCIATION\b|\bBANKERS ASSN\b|\bBANKERS SOCIETY\b",
        "state_or_regional_bankers_association",
        (
            "State and regional bankers associations (Florida Bankers "
            "Association, Community Bankers Association of Illinois and the like) "
            "are predominantly community institutions. This is a PATTERN "
            "identification, weaker than a named roster hit but stronger than the "
            "long-tail default, and it is counted under its own basis."
        ),
    ),
)

# Individual institutions positively identified as large.  Each entry names the
# parent, the match pattern, and the sourced roster(s) it came from.  Entries
# may carry `to_year` when the institution ceased to exist inside the window.
#
# THIS ROSTER IS THE UNION of: the current BPI board, the 2023 BPI board, the
# FSB US G-SIB list, and the Federal Reserve's 2026 stress-test Table 3.  It is
# NOT a $100bn list and NOT a BPI list; see ROSTER_NESTING_NOTE.
BIG_BANK_ROSTER = (
    {
        "parent": "American Express",
        "pattern": r"\bAMERICAN EXPRESS\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
    },
    {
        "parent": "Bank of America",
        "pattern": r"\bBANK OF AMERICA\b|\bMERRILL LYNCH\b",
        "sources": ("bpi_board_current", "fsb_gsib_2025", "frb_stress_2026_t3"),
        "note": "Merrill Lynch is a wholly-owned Bank of America subsidiary and files under that ownership.",
    },
    {
        "parent": "Barclays",
        "pattern": r"\bBARCLAYS\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
        "note": "Federal Reserve table lists the US intermediate holding company, Barclays US LLC.",
    },
    {
        "parent": "BMO Financial Group",
        "pattern": r"\bBMO\b|\bBANK OF MONTREAL\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
        "note": "Federal Reserve table lists BMO Financial Corp, the US holding company.",
    },
    {
        "parent": "BNY",
        "pattern": r"\bBNY\b|\bBANK OF NEW YORK\b",
        "sources": ("bpi_board_current", "fsb_gsib_2025", "frb_stress_2026_t3"),
        "note": "Renamed from BNY Mellon; the identity table carries the rename.",
    },
    {
        "parent": "Citigroup",
        "pattern": r"\bCITIGROUP\b|\bCITIBANK\b|\bCITICORP\b",
        "sources": ("bpi_board_current", "fsb_gsib_2025", "frb_stress_2026_t3"),
        "note": (
            "Bare 'CITI' is deliberately NOT matched: it is too short to be safe "
            "against unrelated names."
        ),
    },
    {
        "parent": "Fifth Third Bancorp",
        "pattern": r"\bFIFTH THIRD\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
    },
    {
        "parent": "First Horizon",
        "pattern": r"\bFIRST HORIZON\b",
        "sources": ("bpi_board_current",),
        "note": (
            "THE NON-NESTING CASE. First Horizon sits on BPI's board at roughly "
            "$84bn in assets and is absent from the Federal Reserve's Category "
            "I-IV table. Politically big, below the balance-sheet line."
        ),
    },
    {
        "parent": "JPMorgan Chase",
        "pattern": r"\bJPMORGAN\b|\bJ P MORGAN\b|\bCHASE BANK\b",
        "sources": ("bpi_board_current", "fsb_gsib_2025", "frb_stress_2026_t3"),
    },
    {
        "parent": "Northern Trust",
        "pattern": r"\bNORTHERN TRUST\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
    },
    {
        "parent": "RBC",
        "pattern": r"\bRBC\b|\bROYAL BANK OF CANADA\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
        "note": "Federal Reserve table lists RBC US Group Holdings.",
    },
    {
        "parent": "Regions Financial",
        "pattern": r"\bREGIONS FINANCIAL\b|\bREGIONS BANK\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
    },
    {
        "parent": "State Street",
        "pattern": r"\bSTATE STREET\b",
        "sources": ("bpi_board_current", "fsb_gsib_2025", "frb_stress_2026_t3"),
    },
    {
        "parent": "TD Bank Group",
        "pattern": r"\bTD BANK\b|\bTD GROUP\b|\bTORONTO DOMINION\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
        "note": "Federal Reserve table lists TD Group US Holdings.",
    },
    {
        "parent": "PNC Financial Services Group",
        "pattern": r"\bPNC\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
    },
    {
        "parent": "Truist",
        "pattern": r"\bTRUIST\b|\bBB AND T\b|\bSUNTRUST\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
        "note": "BB&T and SunTrust combined into Truist in 2019, before this window opens.",
    },
    {
        "parent": "U.S. Bancorp",
        "pattern": r"\bU S BANCORP\b|\bUS BANCORP\b|\bU S BANK\b|\bUS BANK\b",
        "sources": ("bpi_board_current", "frb_stress_2026_t3"),
    },
    {
        "parent": "Wells Fargo",
        "pattern": r"\bWELLS FARGO\b",
        "sources": ("bpi_board_current", "fsb_gsib_2025", "frb_stress_2026_t3"),
    },
    {
        "parent": "Zions Bancorporation",
        "pattern": r"\bZIONS\b",
        "sources": ("bpi_board_current",),
        "note": (
            "Second non-nesting case: on BPI's board, below $100bn, absent from "
            "the Federal Reserve Category I-IV table."
        ),
    },
    {
        "parent": "BNP Paribas",
        "pattern": r"\bBNP PARIBAS\b",
        "sources": ("bpi_board_2023",),
    },
    {
        "parent": "Capital One",
        "pattern": r"\bCAPITAL ONE\b",
        "sources": ("bpi_board_2023", "frb_stress_2026_t3"),
    },
    {
        "parent": "Citizens Financial Group",
        "pattern": r"\bCITIZENS FINANCIAL GROUP\b",
        "sources": ("bpi_board_2023", "frb_stress_2026_t3"),
        "note": (
            "Deliberately NARROW. A bare 'CITIZENS BANK' pattern would sweep in "
            "the many unrelated community banks called Citizens Bank of "
            "somewhere, manufacturing big-bank spend out of small-bank filings. "
            "Under-matching here is the safe direction."
        ),
    },
    {
        "parent": "Huntington Bancshares",
        "pattern": r"\bHUNTINGTON BANC\w*\b|\bHUNTINGTON NATIONAL BANK\b",
        "sources": ("bpi_board_2023", "frb_stress_2026_t3"),
    },
    {
        "parent": "M&T Bank",
        "pattern": r"\bM AND T BANK\b|\bM AND T CORP\w*\b|\bMANUFACTURERS AND TRADERS\b",
        "sources": ("bpi_board_2023", "frb_stress_2026_t3"),
        "note": "'&' normalizes to ' AND ', so M&T Bank Corporation reads M AND T BANK CORPORATION.",
    },
    {
        "parent": "MUFG",
        "pattern": r"\bMUFG\b|\bMITSUBISHI UFJ\b",
        "sources": ("bpi_board_2023",),
    },
    {
        "parent": "Goldman Sachs",
        "pattern": r"\bGOLDMAN SACHS\b",
        "sources": ("fsb_gsib_2025", "frb_stress_2026_t3"),
    },
    {
        "parent": "Morgan Stanley",
        "pattern": r"\bMORGAN STANLEY\b",
        "sources": ("fsb_gsib_2025", "frb_stress_2026_t3"),
    },
    {
        "parent": "Charles Schwab",
        "pattern": r"\bCHARLES SCHWAB\b|\bSCHWAB\b",
        "sources": ("frb_stress_2026_t3",),
    },
    {
        "parent": "HSBC North America",
        "pattern": r"\bHSBC\b",
        "sources": ("frb_stress_2026_t3",),
    },
    {
        "parent": "First Citizens BancShares",
        "pattern": r"\bFIRST CITIZENS BANC\w*\b|\bFIRST CITIZENS BANK\b",
        "sources": ("frb_stress_2026_t3",),
    },
    {
        "parent": "UBS Americas",
        "pattern": r"\bUBS\b",
        "sources": ("frb_stress_2026_t3",),
    },
    {
        "parent": "Ally Financial",
        "pattern": r"\bALLY FINANCIAL\b|\bALLY BANK\b",
        "sources": ("frb_stress_2026_t3",),
    },
    {
        "parent": "KeyCorp",
        "pattern": r"\bKEYCORP\b|\bKEYBANK\b|\bKEY BANK\b",
        "sources": ("frb_stress_2026_t3",),
    },
    {
        "parent": "Santander Holdings USA",
        "pattern": r"\bSANTANDER\b",
        "sources": ("frb_stress_2026_t3",),
    },
    {
        "parent": "Deutsche Bank",
        "pattern": r"\bDEUTSCHE BANK\b|\bDB USA\b",
        "sources": ("frb_stress_2026_t3",),
    },
    {
        "parent": "Synchrony Financial",
        "pattern": r"\bSYNCHRONY\b",
        "sources": ("frb_stress_2026_t3",),
    },
    # ---- Ceased to exist inside the window.  Date-gated, see frb_large_bhc_exited.
    {
        "parent": "Discover Financial Services",
        "pattern": r"\bDISCOVER FINANCIAL\b|\bDISCOVER BANK\b",
        "sources": ("frb_large_bhc_exited",),
        "to_year": 2025,
        "note": (
            "A Federal Reserve stress-test participant well above $100bn through "
            "the 2024 cycle; acquired by Capital One on 2025-05-18. It is absent "
            "from the 2026 Table 3 because it ceased to exist, not because it "
            "shrank. Classifying its 2021-2024 lobbying as community would be "
            "plainly wrong, which is precisely why the tier is as-of-aware."
        ),
    },
    {
        "parent": "Silicon Valley Bank",
        "pattern": r"\bSILICON VALLEY BANK\b|\bSVB FINANCIAL\b",
        "sources": ("frb_large_bhc_exited",),
        "to_year": 2023,
        "note": "About $209bn in assets at failure; FDIC receivership 2023-03-10.",
    },
    {
        "parent": "Signature Bank",
        "pattern": r"\bSIGNATURE BANK\b",
        "sources": ("frb_large_bhc_exited",),
        "to_year": 2023,
        "note": "About $110bn in assets at failure; FDIC receivership 2023-03-12.",
    },
    {
        "parent": "First Republic Bank",
        "pattern": r"\bFIRST REPUBLIC\b",
        "sources": ("frb_large_bhc_exited",),
        "to_year": 2023,
        "note": "About $212bn in assets at failure; receivership and sale to JPMorgan Chase 2023-05-01.",
    },
    {
        "parent": "Credit Suisse",
        "pattern": r"\bCREDIT SUISSE\b",
        "sources": ("frb_large_bhc_exited",),
        "to_year": 2023,
        "note": "US intermediate holding company above the tailoring line; acquired by UBS June 2023.",
    },
)


def _roster_entry_applies(entry, as_of):
    """True when a roster entry's membership window covers ``as_of``.

    ``as_of=None`` means no year was supplied, so the whole-window union
    applies and every entry is live.  This is the only place the tier's
    time-awareness lives.
    """
    if as_of is None:
        return True
    from_year = entry.get("from_year")
    to_year = entry.get("to_year")
    if from_year is not None and as_of < from_year:
        return False
    if to_year is not None and as_of > to_year:
        return False
    return True


def bank_tier(raw_name, as_of=None, resolver=None):
    """Label a bank-sector organization big / community / mixed / unclassified.

    Returns ``{"tier", "basis", "evidence", "defaulted", "parent", "sources"}``.
    ``defaulted`` is True only when the answer came from the long-tail rule
    rather than from any positive identification; the caller MUST report how
    much of the community series rests on it.  ``basis`` is finer than
    ``defaulted`` and gives the full evidence ladder.

    This never changes sector membership - it only labels an organization the
    `bank` roster has already selected.
    """
    name = normalize_name(raw_name)
    if resolver is not None:
        resolved = resolver.resolve(raw_name, as_of=as_of)
        if resolved["resolved"]:
            name = normalize_name(resolved["display_name"])

    # 1. Named national trade associations, by identity.
    for pattern, tier, basis, evidence in ASSOCIATION_TIERS:
        if re.search(pattern, name):
            return {
                "tier": tier,
                "basis": basis,
                "evidence": evidence,
                "defaulted": False,
                "parent": None,
                "sources": (),
            }

    # 2. The sourced large-institution roster, applied as of the year.
    for entry in BIG_BANK_ROSTER:
        if not re.search(entry["pattern"], name):
            continue
        if not _roster_entry_applies(entry, as_of):
            # On the roster, but outside its membership window in this year.
            # Fall through rather than assert bigness the source cannot carry.
            continue
        evidence = "on the sourced large-bank roster as %s; source(s): %s" % (
            entry["parent"],
            ", ".join(BANK_TIER_SOURCES[key]["label"] for key in entry["sources"]),
        )
        if entry.get("note"):
            evidence = "%s. %s" % (evidence, entry["note"])
        return {
            "tier": "big",
            "basis": "named_large_institution",
            "evidence": evidence,
            "defaulted": False,
            "parent": entry["parent"],
            "sources": tuple(entry["sources"]),
        }

    # 3. A national or international association that is not on the known list
    #    cannot be defaulted to community: its membership mix is UNKNOWN, not
    #    small.  Withholding a label is a decision, not a default.
    if re.search(
        r"\bASSOCIATION\b|\bINSTITUTE\b|\bCOUNCIL\b|\bROUNDTABLE\b|\bFORUM\b|\bCOALITION\b|\bFEDERATION\b",
        name,
    ) and re.search(
        r"\bNATIONAL\b|\bAMERICAN\b|\bAMERICA\b|\bU S\b|\bUS\b|\bUNITED STATES\b|\bINTERNATIONAL\b|\bGLOBAL\b",
        name,
    ):
        return {
            "tier": "unclassified",
            "basis": "national_association_not_on_known_list",
            "evidence": (
                "a national association's membership size mix is unknown; "
                "defaulting it to community would be an invention"
            ),
            "defaulted": False,
            "parent": None,
            "sources": (),
        }

    # 4. State, regional and self-declared community-banking associations.
    for pattern, basis, evidence in COMMUNITY_ASSOCIATION_PATTERNS:
        if re.search(pattern, name):
            return {
                "tier": "community",
                "basis": basis,
                "evidence": evidence,
                "defaulted": False,
                "parent": None,
                "sources": (),
            }

    # 5. The institution's own legal name declares it a community bank.
    if re.search(r"\bCOMMUNITY BANK\w*\b|\bCOMMUNITY BANC\w*\b", name):
        return {
            "tier": "community",
            "basis": "community_in_legal_name",
            "evidence": "the institution's own name declares it a community bank",
            "defaulted": False,
            "parent": None,
            "sources": (),
        }

    # 6. The long tail.  INFERENCE, and counted as such.
    return {
        "tier": "community",
        "basis": "default_unmatched_long_tail",
        "evidence": (
            "not on the sourced large-bank roster and not an identifiable "
            "association. The long tail of individually-filing banks is "
            "overwhelmingly small, so community is the default - but this is "
            "INFERENCE, not positive identification. Note that the roster's own "
            "line is ~$100bn, an order of magnitude above the $10bn policy line, "
            "so mid-size regionals land here too. Counted separately everywhere."
        ),
        "defaulted": True,
        "parent": None,
        "sources": (),
    }


def bank_tier_provenance():
    """Roster composition by source, for the receipt and the report."""
    by_source = {key: [] for key in BANK_TIER_SOURCES}
    for entry in BIG_BANK_ROSTER:
        for key in entry["sources"]:
            by_source[key].append(entry["parent"])
    return {
        "distinct_parents": len(BIG_BANK_ROSTER),
        "parents_by_source": {
            key: {"count": len(names), "parents": sorted(names)}
            for key, names in sorted(by_source.items())
        },
        "date_gated_parents": sorted(
            entry["parent"] for entry in BIG_BANK_ROSTER if entry.get("to_year") or entry.get("from_year")
        ),
        "association_rules": len(ASSOCIATION_TIERS),
    }


# ---------------------------------------------------------------------------
# Resolver.
# ---------------------------------------------------------------------------


def _effective_filing_year(event):
    """First filing year governed by the successor."""
    override = event.get("effective_filing_year")
    if override is not None:
        return int(override)
    date = str(event["effective_date"])
    try:
        year, month, day = (int(part) for part in date.split("-"))
    except ValueError as exc:
        raise EntityIdentityError(f"event {event['event_id']} has invalid effective_date {date!r}") from exc
    return year if (month, day) <= MIDYEAR_CUTOVER_MONTH_DAY else year + 1


class Resolver:
    """Time-aware name -> canonical entity resolution.

    ``resolve(name, as_of=YEAR)`` returns the entity AS IT EXISTED IN THAT
    YEAR.  Renames and DBA aliases collapse across the whole window because one
    legal entity is one entity throughout.  Merger and acquisition edges are
    gated on ``effective_filing_year``, so a predecessor stays itself before the
    boundary and becomes the successor after it.
    """

    def __init__(self, document):
        self.document = document
        self.entities = {entry["entity_id"]: entry for entry in document["entities"]}
        self.events = sorted(document["events"], key=lambda event: (event["effective_filing_year"], event["event_id"]))
        self._by_name = {}
        for entry in document["entities"]:
            for name in entry["names"]:
                key = name["normalized"]
                previous = self._by_name.get(key)
                if previous is not None and previous != entry["entity_id"]:
                    raise EntityIdentityError(
                        f"normalized name {key!r} maps to both {previous} and {entry['entity_id']}"
                    )
                self._by_name[key] = entry["entity_id"]
        self._successor = defaultdict(list)
        for event in self.events:
            if event["relation"] not in DATE_GATED_RELATIONS:
                continue
            for predecessor in event["predecessors"]:
                if predecessor == event["successor"]:
                    continue
                self._successor[predecessor].append(event)

    # -- lookup ---------------------------------------------------------
    def base_entity(self, raw_name):
        """Entity id for a name ignoring every date gate, or None."""
        normalized = normalize_name(raw_name)
        if normalized in self._by_name:
            return self._by_name[normalized]
        # A self-declaring filing name resolves on its declared PRIMARY name.
        declaration = parse_declaration(raw_name)
        if declaration["parts"]:
            primary = normalize_name(declaration["primary"])
            if primary in self._by_name:
                return self._by_name[primary]
            for part in declaration["parts"]:
                key = normalize_name(part["name"])
                if key in self._by_name:
                    return self._by_name[key]
        return None

    def resolve(self, raw_name, as_of=None):
        """Resolve a filing name to its canonical entity as of ``as_of``.

        ``as_of`` is a filing YEAR.  ``None`` means the latest regime, and sets
        ``spans_boundary`` when that crossed a merger edge, so a caller that
        aggregates over a range cannot do so silently.
        """
        base = self.base_entity(raw_name)
        if base is None:
            return {
                "resolved": False,
                "entity_id": None,
                "display_name": None,
                "base_entity_id": None,
                "relation": None,
                "effective_date": None,
                "confidence": None,
                "spans_boundary": False,
                "applied_events": [],
            }
        current = base
        applied = []
        crossed_any = False
        guard = 0
        while True:
            guard += 1
            if guard > len(self.events) + 2:
                raise EntityIdentityError(f"cycle in identity events at {current}")
            candidates = self._successor.get(current) or []
            following = None
            for event in candidates:
                crossed_any = True
                if as_of is None or int(as_of) >= event["effective_filing_year"]:
                    following = event
                    break
            if following is None:
                break
            applied.append(following["event_id"])
            current = following["successor"]
        entry = self.entities[current]
        last = self.document["_event_index"][applied[-1]] if applied else None
        return {
            "resolved": True,
            "entity_id": current,
            "display_name": entry["display_name"],
            "base_entity_id": base,
            "sector_hint": entry.get("sector_hint"),
            "relation": last["relation"] if last else "rename",
            "effective_date": last["effective_date"] if last else None,
            "confidence": entry.get("confidence"),
            "evidence": entry.get("evidence"),
            "spans_boundary": bool(as_of is None and crossed_any),
            "applied_events": applied,
        }

    def canonical_name(self, raw_name, as_of=None, default=None):
        """Canonical display name, or ``default`` (the raw name) when unknown."""
        record = self.resolve(raw_name, as_of=as_of)
        if not record["resolved"]:
            return raw_name if default is None else default
        return record["display_name"]

    # -- boundary honesty ------------------------------------------------
    def constituent_breakdown(self, entity_id, years):
        """Which entity is the correct one in each year of a spanning range.

        A total spanning a merger boundary is legitimate ONLY when labelled as
        the sum of its predecessors before the boundary plus the successor
        after.  This returns exactly that mapping so the surface can show it
        instead of smoothing over the event.
        """
        years = tuple(sorted(int(year) for year in years))
        predecessors = defaultdict(list)
        for event in self.events:
            if event["relation"] in DATE_GATED_RELATIONS and event["successor"] == entity_id:
                predecessors[event["effective_filing_year"]].append(event)
        rows = []
        for year in years:
            members = set()
            for entry in self.entities.values():
                if self.resolve(entry["display_name"], as_of=year)["entity_id"] == entity_id:
                    members.add(entry["entity_id"])
            if not members:
                members = {entity_id}
            rows.append({"year": year, "constituents": sorted(members)})
        boundaries = sorted(
            {
                event["effective_filing_year"]
                for events in predecessors.values()
                for event in events
                if min(years) < event["effective_filing_year"] <= max(years)
            }
        )
        return {
            "entity_id": entity_id,
            "years": list(years),
            "by_year": rows,
            "boundary_years": boundaries,
            "spans_boundary": bool(boundaries),
            "label": (
                "SUM of predecessors before the boundary plus the successor after; "
                "NOT one organization's continuous history"
                if boundaries
                else "single continuous entity across this range"
            ),
        }

    def receipt(self):
        return {
            "entities": len(self.entities),
            "names": len(self._by_name),
            "events": len(self.events),
            "by_confidence": self.document["counts"]["by_confidence"],
            "by_relation": self.document["counts"]["by_relation"],
        }


# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------


def _sweep_root(explicit=None):
    if explicit:
        return Path(explicit)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from paths import LOCAL_MELANGE
    return Path(LOCAL_MELANGE) / "lda_rebuild" / "raw" / "sweeps"


def iter_filing_names(sweep_root, years):
    """Yield (role, raw_name, client_id, year) for every filing in the sweeps."""
    sweep_root = Path(sweep_root)
    for year in sorted(set(int(value) for value in years)):
        year_dir = sweep_root / str(year)
        pages = sorted(year_dir.glob("page_*.json"))
        if not pages:
            raise EntityIdentityError(f"no sweep pages for {year}: {year_dir}")
        for page in pages:
            with page.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            for filing in payload.get("results") or ():
                client = filing.get("client") or {}
                name = (client.get("name") or "").strip()
                if name:
                    identifier = client.get("id")
                    yield "client", name, ("" if identifier is None else str(identifier)), year
                registrant = filing.get("registrant") or {}
                name = (registrant.get("name") or "").strip()
                if name:
                    identifier = registrant.get("id")
                    yield "registrant", name, ("" if identifier is None else str(identifier)), year


def build_document(sweep_root=None, years=DEFAULT_YEARS):
    """Mine filer self-declarations, merge the researched layer, and validate."""
    years = tuple(sorted(set(int(year) for year in years)))
    root = _sweep_root(sweep_root)

    observed = {"client": {}, "registrant": {}}
    for role, raw_name, identifier, year in iter_filing_names(root, years):
        table = observed[role]
        row = table.setdefault(raw_name, {"ids": set(), "by_year": defaultdict(int), "total": 0})
        row["total"] += 1
        row["by_year"][year] += 1
        if identifier:
            row["ids"].add(identifier)

    # --- filing-declared layer ---------------------------------------------
    declared = []
    for role in ("client", "registrant"):
        for raw_name in sorted(observed[role]):
            if not has_declaration(raw_name):
                continue
            parsed = parse_declaration(raw_name)
            if not parsed["parts"] or not parsed["primary"]:
                continue
            row = observed[role][raw_name]
            declared.append(
                {
                    "role": role,
                    "verbatim": raw_name,
                    "primary": parsed["primary"],
                    "parts": parsed["parts"],
                    "client_ids": sorted(row["ids"]),
                    "filings": row["total"],
                    "by_year": {str(year): row["by_year"][year] for year in sorted(row["by_year"])},
                }
            )

    entities = {}

    def entity_key(name):
        return re.sub(r"[^A-Z0-9]+", "_", normalize_name(name)).strip("_")[:60] or "UNNAMED"

    def add_name(entity, raw, relation, confidence, evidence, client_ids=(), filings=0, by_year=None):
        normalized = normalize_name(raw)
        if not normalized:
            return
        for existing in entity["names"]:
            if existing["normalized"] == normalized:
                existing["client_ids"] = sorted(set(existing["client_ids"]) | set(client_ids))
                existing["filings"] += filings
                if CONFIDENCE_LEVELS.index(confidence) < CONFIDENCE_LEVELS.index(existing["confidence"]):
                    existing.update({"confidence": confidence, "evidence": evidence, "relation": relation})
                return
        entity["names"].append(
            {
                "raw": raw,
                "normalized": normalized,
                "relation": relation,
                "confidence": confidence,
                "evidence": evidence,
                "client_ids": sorted(client_ids),
                "filings": filings,
                "by_year": by_year or {},
            }
        )

    # The researched layer is built FIRST so its names can act as ANCHORS.
    # An anchor never bridges two declarations: "...DBA AMERICA'S CREDIT UNIONS
    # (FORMERLY NAFCU)" names both the successor brand and a predecessor, and
    # letting that string fuse them would collapse the merger backward, which
    # is precisely the error this module exists to prevent.
    researched_names = {}
    for entry in RESEARCHED_ENTITIES:
        for name in entry["names"]:
            researched_names[normalize_name(name)] = entry["entity_id"]

    def declaration_owner(raw_name, parsed):
        """Which researched entity owns this filing name, if any."""
        owner = researched_names.get(normalize_name(raw_name))
        if owner is not None:
            return owner
        if not parsed["parts"]:
            return None
        # Prior-name declarations bind hardest: a filing that says
        # "(FORMERLY NAFCU)" is NAFCU's record whatever else it says.
        for part in parsed["parts"]:
            if part["relation"] == "rename":
                owner = researched_names.get(normalize_name(part["name"]))
                if owner is not None:
                    return owner
        return researched_names.get(normalize_name(parsed["primary"]))

    # --- mechanical layer: anchored union-find over declared names ----------
    # Two declarations that share a name are the same entity.  Names owned by
    # the researched layer are excluded from the union so they cannot bridge.
    parent = {}

    def find(key):
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left, right):
        left, right = find(left), find(right)
        if left != right:
            parent[max(left, right)] = min(left, right)

    mechanical = []
    for record in declared:
        parsed = {"primary": record["primary"], "parts": record["parts"]}
        if declaration_owner(record["verbatim"], parsed) is not None:
            continue
        keys = [normalize_name(record["primary"]), normalize_name(record["verbatim"])]
        keys += [normalize_name(part["name"]) for part in record["parts"]]
        keys = [key for key in keys if key and key not in researched_names]
        if not keys:
            continue
        for key in keys[1:]:
            union(keys[0], key)
        mechanical.append((record, keys))

    components = defaultdict(list)
    for record, keys in mechanical:
        components[find(keys[0])].append(record)

    for root_key in sorted(components):
        records = sorted(components[root_key], key=lambda row: (-row["filings"], row["verbatim"]))
        key = entity_key(records[0]["primary"])
        while key in entities:
            key = key + "_2"
        entity = {
            "entity_id": key,
            "display_name": records[0]["primary"],
            "sector_hint": None,
            "names": [],
            "sources": ["filing_declared"],
        }
        entities[key] = entity
        for record in records:
            add_name(entity, record["primary"], "rename", "filing_declared", record["verbatim"], record["client_ids"])
            add_name(
                entity,
                record["verbatim"],
                "rename",
                "filing_declared",
                record["verbatim"],
                record["client_ids"],
                record["filings"],
                record["by_year"],
            )
            for part in record["parts"]:
                if normalize_name(part["name"]) in researched_names:
                    continue
                add_name(entity, part["name"], part["relation"], "filing_declared", record["verbatim"])

    # --- researched layer ---------------------------------------------------
    for entry in RESEARCHED_ENTITIES:
        key = entry["entity_id"]
        entity = entities.get(key)
        if entity is None:
            entity = {
                "entity_id": key,
                "display_name": entry["display_name"],
                "sector_hint": entry.get("sector_hint"),
                "names": [],
                "sources": [],
            }
            entities[key] = entity
        entity["display_name"] = entry["display_name"]
        entity["sector_hint"] = entry.get("sector_hint")
        entity["confidence"] = entry["confidence"]
        entity["evidence"] = entry["evidence"]
        if entry.get("note"):
            entity["note"] = entry["note"]
        if entry["confidence"] not in entity["sources"]:
            entity["sources"].append(entry["confidence"])
        for name in entry["names"]:
            add_name(entity, name, "rename", entry["confidence"], entry["evidence"])

    # A researched entity claims every observed filing name it owns, carrying
    # that name's real filing counts so the table is auditable against volume.
    for role in ("client", "registrant"):
        for raw_name in sorted(observed[role]):
            row = observed[role][raw_name]
            parsed = parse_declaration(raw_name)
            owner = declaration_owner(raw_name, parsed)
            if owner is None:
                continue
            add_name(
                entities[owner],
                raw_name,
                "rename",
                "filing_declared" if parsed["parts"] else entities[owner].get("confidence", "researched"),
                raw_name if parsed["parts"] else entities[owner].get("evidence", ""),
                sorted(row["ids"]),
                row["total"],
                {str(year): row["by_year"][year] for year in sorted(row["by_year"])},
            )

    entities = {key: entity for key, entity in entities.items() if entity["names"]}

    # --- events -------------------------------------------------------------
    events = []
    for entry in RESEARCHED_EVENTS:
        if entry["relation"] not in RELATIONS:
            raise EntityIdentityError(f"event {entry['event_id']} has unknown relation")
        for identifier in tuple(entry["predecessors"]) + (entry["successor"],):
            if identifier not in entities:
                raise EntityIdentityError(f"event {entry['event_id']} references unknown entity {identifier}")
        event = dict(entry)
        event["predecessors"] = list(entry["predecessors"])
        event["effective_filing_year"] = _effective_filing_year(entry)
        events.append(event)
    events.sort(key=lambda event: (event["effective_filing_year"], event["event_id"]))

    # --- counts and residual ------------------------------------------------
    by_confidence = defaultdict(int)
    by_relation = defaultdict(int)
    for entity in entities.values():
        for name in entity["names"]:
            by_confidence[name["confidence"]] += 1
            by_relation[name["relation"]] += 1
    for event in events:
        by_relation[event["relation"]] += 1

    known = {name["normalized"] for entity in entities.values() for name in entity["names"]}
    client_names = set(observed["client"])
    unresolved = sorted(name for name in client_names if normalize_name(name) not in known)
    unresolved_declaring = sorted(name for name in unresolved if has_declaration(name))

    document = {
        "schema_version": SCHEMA_VERSION,
        "years": list(years),
        "source": {
            "sweep_root": str(root),
            "distinct_client_names": len(client_names),
            "distinct_registrant_names": len(observed["registrant"]),
            "client_names_self_declaring": sum(1 for name in client_names if has_declaration(name)),
            "registrant_names_self_declaring": sum(1 for name in observed["registrant"] if has_declaration(name)),
        },
        "honesty": {
            "primary_source": (
                "Filer self-declaration inside client.name / registrant.name is the "
                "PRIMARY source: the entity is describing its own identity."
            ),
            "researched_not_exhaustive": (
                "The researched entries catch the renames and mergers we know about. "
                "They do NOT prove there are no others. unresolved_client_names bounds "
                "the residual error."
            ),
            "collapse_forward": (
                "rename and dba_alias collapse across the whole window; merger and "
                "acquisition are date-gated and are NEVER collapsed backward."
            ),
            "current_name_retroaction": (
                "The LDA API returns each client record's CURRENT name applied to all "
                "of that record's filings: zero client ids carry more than one name "
                "across 2021-2026. A renamed organization therefore appears as several "
                "records frozen at different names, not as one record changing over time."
            ),
            "bank_tier_threshold": BANK_TIER_THRESHOLD_NOTE,
        },
        "counts": {
            "entities": len(entities),
            "names": sum(len(entity["names"]) for entity in entities.values()),
            "events": len(events),
            "by_confidence": dict(sorted(by_confidence.items())),
            "by_relation": dict(sorted(by_relation.items())),
        },
        "unresolved_client_names": {
            "count": len(unresolved),
            "share_of_distinct_client_names": round(len(unresolved) / max(len(client_names), 1), 6),
            "self_declaring_and_unresolved": len(unresolved_declaring),
            "samples": unresolved_declaring[:25],
        },
        "bank_tier": {
            "tiers": list(BANK_TIERS),
            "threshold_note": BANK_TIER_THRESHOLD_NOTE,
            "bpi_membership_gap": BPI_MEMBERSHIP_GAP_NOTE,
            "barred_source": BARRED_SOURCE_NOTE,
            "roster_nesting": ROSTER_NESTING_NOTE,
            "sources": {key: dict(value) for key, value in sorted(BANK_TIER_SOURCES.items())},
            "association_rules": [
                {"pattern": pattern, "tier": tier, "basis": basis, "evidence": evidence}
                for pattern, tier, basis, evidence in ASSOCIATION_TIERS
            ],
            "community_association_rules": [
                {"pattern": pattern, "basis": basis, "evidence": evidence}
                for pattern, basis, evidence in COMMUNITY_ASSOCIATION_PATTERNS
            ],
            "large_bank_roster": [
                {
                    "parent": entry["parent"],
                    "pattern": entry["pattern"],
                    "sources": list(entry["sources"]),
                    "from_year": entry.get("from_year"),
                    "to_year": entry.get("to_year"),
                    "note": entry.get("note"),
                }
                for entry in BIG_BANK_ROSTER
            ],
            "provenance": bank_tier_provenance(),
        },
        "entities": [entities[key] for key in sorted(entities)],
        "events": events,
    }
    for entity in document["entities"]:
        entity["names"].sort(key=lambda name: (name["normalized"],))
        entity["sources"] = sorted(set(entity.get("sources") or ()))
    _validate(document)
    return document


def _validate(document):
    seen = {}
    for entity in document["entities"]:
        if not entity["names"]:
            raise EntityIdentityError(f"entity {entity['entity_id']} has no names")
        for name in entity["names"]:
            if name["confidence"] not in CONFIDENCE_LEVELS:
                raise EntityIdentityError(f"unknown confidence {name['confidence']!r}")
            if name["relation"] not in RELATIONS:
                raise EntityIdentityError(f"unknown relation {name['relation']!r}")
            if not name.get("evidence"):
                raise EntityIdentityError(
                    f"{entity['entity_id']} name {name['raw']!r} has no evidence; "
                    "every entry must be auditable back to its source"
                )
            previous = seen.get(name["normalized"])
            if previous is not None and previous != entity["entity_id"]:
                raise EntityIdentityError(
                    f"normalized name {name['normalized']!r} claimed by both {previous} and {entity['entity_id']}"
                )
            seen[name["normalized"]] = entity["entity_id"]
    for event in document["events"]:
        if not event.get("evidence"):
            raise EntityIdentityError(f"event {event['event_id']} has no evidence")
    return True


def default_artifact_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ARTIFACT_NAME
    )


def load_document(path=None):
    path = path or default_artifact_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except OSError as exc:
        raise EntityIdentityError(f"cannot read identity table {path}: {exc}") from exc
    except ValueError as exc:
        raise EntityIdentityError(f"malformed identity table {path}: {exc}") from exc
    document["_event_index"] = {event["event_id"]: event for event in document["events"]}
    return document


_RESOLVER_CACHE = {}


def default_resolver(path=None):
    """Process-cached resolver over the shipped identity table."""
    path = path or default_artifact_path()
    key = os.path.abspath(path)
    if key not in _RESOLVER_CACHE:
        _RESOLVER_CACHE[key] = Resolver(load_document(path))
    return _RESOLVER_CACHE[key]


def _atomic_write(path, text):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _assert_normalization_agrees():
    """The identity table is keyed on sector_spend's normalization."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from lobbying import sector_spend
    except ImportError as exc:
        raise EntityIdentityError("cannot import sector_spend to verify normalization agreement") from exc
    probes = (
        "Credit Union National Association, Inc. DBA America's Credit Unions",
        "MD|DC Credit Union Association",
        "TRUIST FINANCIAL CORPORATION (FORMERLY KNOWN AS BB&T)",
        "Foris DAX, Inc. d/b/a Crypto.com",
        None,
        "",
    )
    for probe in probes:
        if normalize_name(probe) != sector_spend.normalize_name(probe):
            raise EntityIdentityError(f"normalization drift on {probe!r}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="rebuild entity_identity.json from the sweeps")
    parser.add_argument("--sweep-root", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--resolve", nargs="+", metavar="NAME", help="resolve names and print the result")
    parser.add_argument("--as-of", type=int, default=None)
    args = parser.parse_args(argv)

    if args.build:
        _assert_normalization_agrees()
        document = build_document(args.sweep_root)
        out = args.out or default_artifact_path()
        _atomic_write(out, json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        counts = document["counts"]
        print("entity identity table")
        print(f"  entities={counts['entities']} names={counts['names']} events={counts['events']}")
        print(f"  by confidence: {counts['by_confidence']}")
        print(f"  by relation:   {counts['by_relation']}")
        unresolved = document["unresolved_client_names"]
        print(
            f"  unresolved client names: {unresolved['count']:,} of "
            f"{document['source']['distinct_client_names']:,} "
            f"({unresolved['share_of_distinct_client_names']:.1%}); "
            f"{unresolved['self_declaring_and_unresolved']:,} of them self-declare a rename"
        )
        print(f"  -> {out}")
        return 0

    resolver = default_resolver()
    if args.resolve:
        for name in args.resolve:
            record = resolver.resolve(name, as_of=args.as_of)
            print(f"{name!r} as_of={args.as_of}: {json.dumps(record, ensure_ascii=False, sort_keys=True)}")
        return 0
    print(json.dumps(resolver.receipt(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
