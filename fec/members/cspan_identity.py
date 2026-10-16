# -*- coding: utf-8 -*-
"""Conservative C-SPAN speaker attribution.

The C-SPAN transcript JSON records are authoritative for speaker labels, but
their ``role`` fields are blank.  Membership must therefore be established from
the label, the program title, and congressional service on the program date.

This module deliberately has no surname-only fallback.  If a full-name label,
program-title guest, and tenure record do not agree, the turn is not attributed.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from paths import ROOT as MB_ROOT
except ImportError:  # pragma: no cover - direct import from outside fec/
    MB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_HONORIFIC = re.compile(
    r"^(?:(?:u\.?s\.?\s+)?(?:representative|rep\.?|senator|sen\.?|delegate|del\.?|"
    r"congressman|congresswoman|speaker|dr\.?)\s+)+",
    re.I,
)
_TITLE_GUEST = re.compile(
    r"^(?:former\s+)?(?:u\.?s\.?\s+)?(?:representative|rep\.?|senator|sen\.?|delegate|del\.?|"
    r"congressman|congresswoman|speaker(?:\s+of\s+the\s+house)?)\s+"
    r"(.+?)(?=\s+(?:(?:and\s+)?(?:representative|rep\.?|senator|sen\.?)|on|discusses|discuss|"
    r"talks|speaks|about|reacts|responds)\b|$)",
    re.I,
)
_PARTY_STATE = re.compile(r"(?:\s*,\s*|\s*\(|\s+)\(?(?-i:[DRI])\s*[-–]\s*[A-Za-z. -]{1,24}\)?\s*$", re.I)
_SUFFIX = re.compile(
    r"(?:\s|,)+(?:M\.?D\.?|Ph\.?D\.?|D\.?D\.?S\.?|D\.?O\.?|J\.?D\.?|M\.?P\.?H\.?|"
    r"R\.?N\.?|Esq\.?|Jr\.?|Sr\.?|II|III|IV)\.?\s*$",
    re.I,
)

# C-SPAN/public names that are not derivable from the canonical roster first
# name.  Values are canonical names in congestion.json.  Keeping this local
# avoids changing identity behavior for unrelated ingestion pipelines.
PUBLIC_ALIASES = {
    "buddy carter": "Earl Carter",
    "jan schakowsky": "Janice Schakowsky",
    "brad schneider": "Bradley Schneider",
    "hank johnson": "Henry Johnson",
    "gabriel gabe vasquez": "Gabe Vasquez",
    "gabriel vasquez": "Gabe Vasquez",
    "lou correa": "Luis Correa",
    "jen kiggans": "Jennifer Kiggans",
    "dustin dusty johnson": "Dusty Johnson",
    "dustin johnson": "Dusty Johnson",
    # One C-SPAN program title misspells Gimenez as Gimemez.  The transcript
    # label remains correctly spelled; this alias is title matching only.
    "carlos gimemez": "Carlos Gimenez",
}

_NICKNAME_SETS = [
    {"bernard", "bernie"}, {"charles", "chuck", "charlie"},
    {"christopher", "chris"}, {"richard", "dick", "rich", "rick"},
    {"michael", "mike"}, {"james", "jim", "jimmy"},
    {"robert", "bob", "rob", "bobby"}, {"thomas", "tom", "tommy"},
    {"daniel", "dan", "danny"}, {"joseph", "joe", "joey"},
    {"timothy", "tim"}, {"patrick", "pat"},
    {"william", "bill", "will", "billy"},
    {"edward", "ed", "ted", "eddie"}, {"theodore", "ted"},
    {"elizabeth", "liz", "beth", "betty"},
    {"katherine", "kathryn", "katie", "kate", "kathy"},
    {"margaret", "maggie", "meg"}, {"benjamin", "ben"},
    {"alexander", "alex"}, {"anthony", "tony"},
    {"andrew", "andy", "drew"}, {"matthew", "matt"},
    {"jonathan", "jon"}, {"nicholas", "nick"},
    {"gregory", "greg"}, {"lawrence", "larry"},
    {"gerald", "jerry"}, {"ronald", "ron", "ronny"},
    {"donald", "don"}, {"kenneth", "ken"},
    {"stephen", "steven", "steve"}, {"david", "dave"},
    {"samuel", "sam"}, {"frederick", "fred"},
    {"susan", "sue", "suzy"}, {"deborah", "debbie", "deb"},
    {"cynthia", "cindy"},
    {"jacquelyn", "jacqueline", "jackie"},
    {"raphael", "rafael", "raffi"}, {"joshua", "josh"},
    {"zachary", "zach", "zack"}, {"abigail", "abby"},
    {"rebecca", "becca", "becky"}, {"jefferson", "jeff"},
    {"jeffrey", "jeff"}, {"mitchell", "mitch"},
    {"randall", "randy"}, {"terrence", "terri", "terry"},
    {"vincent", "vince"},
]
_NICKNAMES: dict[str, set[str]] = {}
for _names in _NICKNAME_SETS:
    for _name in _names:
        _NICKNAMES.setdefault(_name, set()).update(_names)


def norm(value: str | None) -> str:
    """ASCII-fold and collapse a name/title to space-delimited words."""
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def clean_label(value: str | None) -> str:
    """Remove credentials and role decorations without inventing a name."""
    value = (value or "").strip()
    value = _PARTY_STATE.sub("", value)
    value = _HONORIFIC.sub("", value)
    for _ in range(4):
        cleaned = _SUFFIX.sub("", value).strip(" ,")
        if cleaned == value:
            break
        value = cleaned
    return norm(value)


def congress_for_date(value: str | dt.date) -> int | None:
    """Return the Congress in session, respecting the January 3 boundary."""
    try:
        day = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
    congress = (day.year - 1789) // 2 + 1
    if day.year % 2 == 1 and (day.month, day.day) < (1, 3):
        congress -= 1
    return congress


def _historical_aliases(bioname: str) -> set[str]:
    """Convert Voteview's ``LAST, Given (Nickname)`` form into full names."""
    if not bioname or "," not in bioname:
        return set()
    last, given = (part.strip() for part in bioname.split(",", 1))
    nicknames = re.findall(r"\(([^)]+)\)", given)
    given_plain = re.sub(r"\([^)]*\)", "", given).strip()
    words = norm(given_plain).split()
    aliases = set()
    if words:
        aliases.add(norm(f"{words[0]} {last}"))
        aliases.add(norm(f"{' '.join(words)} {last}"))
    aliases.update(norm(f"{nickname} {last}") for nickname in nicknames)
    return {alias for alias in aliases if len(alias.split()) >= 2}


@dataclass(frozen=True)
class Resolution:
    member: str | None
    status: str
    detail: str = ""


class CSpanIdentity:
    """Resolve C-SPAN labels using full name + title + service agreement."""

    def __init__(
        self,
        root: str = MB_ROOT,
        roster: Iterable[Mapping[str, object]] | None = None,
        service: Mapping[str, Iterable[int]] | None = None,
        historical_names: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.root = root
        if roster is None:
            with open(os.path.join(root, "data", "engine", "congestion.json"), encoding="utf-8") as handle:
                roster = json.load(handle)
        self.roster = {str(row["member"]): dict(row) for row in roster}
        self.bioguide_to_member = {
            str(row.get("bioguide")): str(row["member"])
            for row in self.roster.values() if row.get("bioguide")
        }

        loaded_historical: dict[str, set[str]] = {
            member: set(names) for member, names in (historical_names or {}).items()
        }
        if service is None:
            service_sets: dict[str, set[int]] = {member: set() for member in self.roster}
            hs_path = os.path.join(root, "votes", "HSall_members.csv")
            with open(hs_path, newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    member = self.bioguide_to_member.get(row.get("bioguide_id", ""))
                    if not member:
                        continue
                    try:
                        service_sets[member].add(int(row["congress"]))
                    except (KeyError, TypeError, ValueError):
                        pass
                    alias = row.get("bioname", "")
                    if alias:
                        loaded_historical.setdefault(member, set()).add(alias)
            self.service = service_sets
        else:
            self.service = {member: {int(x) for x in congresses} for member, congresses in service.items()}

        self.alias_to_members: dict[str, set[str]] = {}
        for member in self.roster:
            canonical = norm(member)
            self._add_alias(canonical, member)
            parts = canonical.split()
            if len(parts) >= 2:
                for first in _NICKNAMES.get(parts[0], {parts[0]}):
                    self._add_alias(" ".join([first, *parts[1:]]), member)
            for bioname in loaded_historical.get(member, set()):
                for alias in _historical_aliases(bioname):
                    self._add_alias(alias, member)

        for alias, member in PUBLIC_ALIASES.items():
            if member in self.roster:
                self._add_alias(norm(alias), member)

        self.pair_to_members: dict[tuple[str, str], set[str]] = {}
        for alias, members in self.alias_to_members.items():
            parts = alias.split()
            if len(parts) >= 2:
                self.pair_to_members.setdefault((parts[0], parts[-1]), set()).update(members)
        self.surname_to_members: dict[str, set[str]] = {}
        for member in self.roster:
            surname = norm(member).split()[-1]
            self.surname_to_members.setdefault(surname, set()).add(member)

        # Longest phrases win when scanning titles, preventing a shorter alias
        # from masking a more specific public name.
        self._title_aliases = sorted(self.alias_to_members, key=lambda x: (-len(x.split()), -len(x), x))

    def _add_alias(self, alias: str, member: str) -> None:
        if len(alias.split()) >= 2:
            self.alias_to_members.setdefault(alias, set()).add(member)

    def served(self, member: str, date: str | dt.date) -> bool:
        congress = congress_for_date(date)
        return congress is not None and congress in self.service.get(member, set())

    def resolve_label(self, label: str | None, date: str | dt.date) -> Resolution:
        cleaned = clean_label(label)
        if len(cleaned.split()) < 2:
            return Resolution(None, "unresolved_label", cleaned)
        members = set(self.alias_to_members.get(cleaned, set()))
        if not members:
            parts = cleaned.split()
            members.update(self.pair_to_members.get((parts[0], parts[-1]), set()))
        if not members:
            return Resolution(None, "unresolved_label", cleaned)
        if len(members) != 1:
            return Resolution(None, "ambiguous_label", ", ".join(sorted(members)))
        member = next(iter(members))
        if not self.served(member, date):
            return Resolution(None, "out_of_service", member)
        return Resolution(member, "label_resolved", cleaned)

    def expected_guest(
        self,
        title: str | None,
        date: str | dt.date,
        labels: Iterable[str | None] | None = None,
    ) -> Resolution:
        title_norm = norm(title)
        found: dict[str, str] = {}
        match = _TITLE_GUEST.match(title or "")
        if match:
            fragment = clean_label(match.group(1))
            parts = fragment.split()
            if len(parts) == 1:
                for member in self.surname_to_members.get(parts[0], set()):
                    found.setdefault(member, fragment)
            elif len(parts) >= 2:
                members = set(self.alias_to_members.get(fragment, set()))
                members.update(self.pair_to_members.get((parts[0], parts[-1]), set()))
                for member in members:
                    found.setdefault(member, fragment)

        # A small number of titles use an unrecognized office construction.
        # Full-name matching remains safe here; unlike a global surname scan it
        # cannot bind an unrelated surname mentioned as a topic.
        if not found:
            padded = f" {title_norm} "
            for alias in self._title_aliases:
                if f" {alias} " not in padded:
                    continue
                for member in self.alias_to_members[alias]:
                    found.setdefault(member, alias)
        if not found:
            return Resolution(None, "title_no_roster_guest", title_norm)
        serving = {member: alias for member, alias in found.items() if self.served(member, date)}
        if not serving:
            return Resolution(None, "title_guest_out_of_service", ", ".join(sorted(found)))
        if len(serving) != 1 and labels is not None:
            label_members = {
                result.member
                for result in (self.resolve_label(label, date) for label in labels)
                if result.member
            }
            narrowed = {member: alias for member, alias in serving.items() if member in label_members}
            if narrowed:
                serving = narrowed
        if len(serving) != 1:
            return Resolution(None, "title_ambiguous", ", ".join(sorted(serving)))
        member, alias = next(iter(serving.items()))
        return Resolution(member, "title_resolved", alias)

    def attribute(
        self,
        label: str | None,
        title: str | None,
        date: str | dt.date,
        expected: Resolution | None = None,
    ) -> Resolution:
        guest = expected or self.expected_guest(title, date, [label])
        if guest.member is None:
            return guest
        speaker = self.resolve_label(label, date)
        if speaker.member is None and _HONORIFIC.match((label or "").strip()):
            cleaned = clean_label(label)
            if len(cleaned.split()) == 1 and cleaned == norm(guest.member).split()[-1]:
                speaker = Resolution(guest.member, "title_confirmed_surname_label", cleaned)
        if speaker.member is None:
            return speaker
        if speaker.member != guest.member:
            return Resolution(
                None,
                "title_label_mismatch",
                f"title={guest.member}; label={speaker.member}",
            )
        return Resolution(speaker.member, "member", guest.detail)
