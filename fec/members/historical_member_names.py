# -*- coding: utf-8 -*-
"""Resolve a bioguide to a readable member name across historical Congresses.

Analyses in this repository span the 117th-119th Congresses, but the two roster
files describe only members who are SITTING RIGHT NOW.  Any member who has since
died, resigned, been appointed out, or simply lost re-election is absent from
both, so every display surface that looked a name up in a current roster and
fell back to ``or bioguide`` printed a raw identifier such as ``M001156``
(Patrick T. McHenry) at the user.

This module is the single fallback ladder for that problem:

  a. ``congestion.json`` display name          -> status "serving"
  b. ``fec/legislators.csv`` ``full_name``     -> status "serving"
  c. Voteview ``bioname``, rendered forename-first, from
     ``votes/HSall_members.csv``               -> status "former"
  d. the raw bioguide, and ONLY with a visible marker
                                               -> status "unresolved"

Tier (c) is reached only when both current rosters miss the member, which is
exactly the definition of "no longer serving", so tier (c) is what marks a
former member.  That marking is material, not cosmetic: a reader looking at a
champions board has to know that a name on it left office.

Voteview's per-Congress rows also provide the first-service year for every
member they cover, even when a higher roster tier supplies the display name.

Tier (d) never degrades silently.  An unresolvable bioguide is rendered with an
explicit marker so that the failure is visible in the artifact instead of
masquerading as a member's name.

This module is NOT ``member_identity.py``.  That module resolves a display NAME
to a canonical roster name; this one resolves a bioguide ID to a display name,
which is the opposite direction and a different failure mode.

The Voteview load reuses ``lobby_forecast.resolve_voteview_congress_rows``
rather than inventing a second dedupe: Senate wins a same-Congress House-to-
Senate move, and a mid-Congress party switch prefers the ICPSR carried into the
following Congress.  That import is deferred to call time because
``lobby_forecast`` imports ``lobby_champions``, which imports this module.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import csv
import hashlib
import io
import json
import os
import re


CURRENT_CONGRESS = 119
FIRST_CONGRESS_START_YEAR = 1789
# Voteview's nineteenth-century rows contain same-bioguide/same-Congress state
# contradictions that the shipped resolver treats as fatal (for example B000929
# in the 2nd Congress is recorded as both KY and VA).  The name index is only
# ever asked about members reachable through the BILLSTATUS corpus on disk
# (115th onward), so the identity window starts at the 100th Congress: far older
# than anything reachable, and clear of the archival contradictions.
VOTEVIEW_MIN_CONGRESS = 100

SOURCE_CONGESTION = "congestion"
SOURCE_LEGISLATORS = "legislators"
SOURCE_VOTEVIEW = "voteview"
SOURCE_NONE = "unresolved"

STATUS_SERVING = "serving"
STATUS_FORMER = "former"
STATUS_UNRESOLVED = "unresolved"

FORMER_MARKER = "former member"
UNRESOLVED_MARKER = "name unresolved"

DEFAULT_CONGESTION_PATH = os.path.join(MB_ROOT, "data", "engine", "congestion.json")
DEFAULT_LEGISLATORS_PATH = os.path.join(MB_ROOT, "fec", "legislators.csv")
DEFAULT_VOTEVIEW_PATH = os.path.join(MB_ROOT, "votes", "HSall_members.csv")


class IdentityError(Exception):
    """An input failure that must be reported rather than papered over."""


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path, label):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise IdentityError(f"{label} unreadable: {exc}") from exc


def _decode(data, label):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IdentityError(f"{label} is not UTF-8: {exc}") from exc


def _recase_word(word):
    """Restore ordinary capitalization to one Voteview surname word.

    Voteview upper-cases the surname but preserves lower-case particles, so
    ``McHENRY`` and ``DeFAZIO`` already carry their own boundary and only the
    trailing all-caps run needs folding.  A fully upper-cased ``MCCORMICK`` has
    no such boundary, and every English ``Mc`` surname capitalizes the letter
    after the particle, so that one prefix is restored explicitly.  ``Mac`` is
    deliberately NOT treated the same way, because ``Mack`` and ``MacArthur``
    are not the same shape and guessing would invent a name.
    """
    lower_positions = [index for index, ch in enumerate(word) if ch.islower()]
    if lower_positions:
        boundary = lower_positions[-1] + 1
        head, tail = word[:boundary], word[boundary:]
        if not tail:
            return head
        return head + tail[:1] + tail[1:].lower()
    if len(word) > 3 and word[:2].upper() == "MC" and word[2:3].isalpha():
        return "Mc" + word[2:3].upper() + word[3:].lower()
    return word[:1].upper() + word[1:].lower()


def _recase_surname(surname):
    parts = re.split(r"([\s\-'’.])", surname)
    return "".join(
        part if not any(ch.isalpha() for ch in part) else _recase_word(part)
        for part in parts
    )


def display_name_from_bioname(bioname):
    """Render Voteview's ``McHENRY, Patrick T.`` as ``Patrick T. McHenry``.

    The forename side is already conventionally cased in Voteview, so only the
    surname is recased.  A generational suffix carried in its own comma field
    (``MANCHIN, Joe, III``) is kept after the surname.
    """
    text = (bioname or "").strip()
    if not text:
        return ""
    if "," not in text:
        return _recase_surname(text)
    surname, _, remainder = text.partition(",")
    surname = _recase_surname(surname.strip())
    fields = [field.strip() for field in remainder.split(",")]
    fields = [field for field in fields if field]
    if not fields:
        return surname
    pieces = [fields[0], surname] + fields[1:]
    return " ".join(piece for piece in pieces if piece)


def load_congestion_names(path):
    """Read the model's current-roster display names (tier a)."""
    data = _read_bytes(path, "congestion roster")
    try:
        document = json.loads(_decode(data, "congestion roster"))
    except ValueError as exc:
        raise IdentityError(f"congestion roster is not JSON: {exc}") from exc
    if not isinstance(document, list):
        raise IdentityError("congestion roster root must be a list")
    names = {}
    for index, record in enumerate(document):
        if not isinstance(record, dict):
            raise IdentityError(f"congestion record {index} must be an object")
        bioguide = record.get("bioguide")
        member = record.get("member")
        if bioguide is None:
            continue
        if not isinstance(bioguide, str) or bioguide != bioguide.strip():
            raise IdentityError(f"congestion record {index} has an invalid bioguide")
        if not bioguide:
            continue
        if not isinstance(member, str) or not member.strip():
            raise IdentityError(
                f"congestion record {index} has no usable member display name"
            )
        if bioguide in names and names[bioguide] != member.strip():
            raise IdentityError(
                f"congestion roster maps {bioguide} to two display names"
            )
        names[bioguide] = member.strip()
    return names, {
        "path": os.path.abspath(path),
        "sha256": _sha256_bytes(data),
        "records": len(document),
        "named_bioguides": len(names),
        "role": "current-roster display names (tier a)",
    }


def load_legislators_names(path):
    """Read ``full_name`` per bioguide from the current legislators roster."""
    data = _read_bytes(path, "legislators roster")
    reader = csv.DictReader(
        io.StringIO(_decode(data, "legislators roster"), newline="")
    )
    required = {"bioguide_id", "full_name"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise IdentityError("legislators roster header mismatch")
    names = {}
    rows = 0
    blank_full_name = []
    for row in reader:
        rows += 1
        bioguide = (row.get("bioguide_id") or "").strip()
        if not bioguide:
            continue
        full_name = (row.get("full_name") or "").strip()
        if not full_name:
            # A roster row with no full_name is exactly how G000607 reached the
            # page as a raw bioguide.  Record it and let a later tier answer.
            blank_full_name.append(bioguide)
            continue
        if bioguide in names and names[bioguide] != full_name:
            raise IdentityError(f"legislators roster maps {bioguide} to two full names")
        names[bioguide] = full_name
    return names, {
        "path": os.path.abspath(path),
        "sha256": _sha256_bytes(data),
        "rows": rows,
        "named_bioguides": len(names),
        "rows_with_blank_full_name": sorted(blank_full_name),
        "role": "current-roster full names (tier b)",
    }


def congress_start_year(congress):
    """Return the calendar year in which a numbered Congress began."""
    return FIRST_CONGRESS_START_YEAR + (int(congress) - 1) * 2


def load_voteview_identities(path, min_congress=VOTEVIEW_MIN_CONGRESS):
    """Build bioguide -> historical name and service bounds (tier c).

    The dedupe is ``lobby_forecast.resolve_voteview_congress_rows`` verbatim:
    this module owns no collision rules of its own.
    """
    from lobbying import lobby_forecast as prior_harness  # deferred: breaks an import cycle

    data = _read_bytes(path, "Voteview member source")
    reader = csv.DictReader(
        io.StringIO(_decode(data, "Voteview member source"), newline="")
    )
    required = {"congress", "chamber", "bioguide_id", "bioname"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise IdentityError("Voteview member source header mismatch")
    eligible = []
    rows = 0
    outside_window = 0
    for row in reader:
        rows += 1
        bioguide = (row.get("bioguide_id") or "").strip()
        chamber = (row.get("chamber") or "").strip().casefold()
        try:
            congress = int(row.get("congress") or "")
        except (TypeError, ValueError):
            continue
        if not bioguide or chamber not in {"house", "senate"}:
            continue
        if congress < min_congress:
            outside_window += 1
            continue
        candidate = dict(row)
        candidate["bioguide_id"] = bioguide
        candidate["congress"] = congress
        eligible.append(candidate)
    try:
        resolved, receipts = prior_harness.resolve_voteview_congress_rows(eligible)
    except prior_harness.ForecastError as exc:
        raise IdentityError(f"Voteview identity resolution failed: {exc}") from exc

    identities = {}
    for (bioguide, congress), row in sorted(resolved.items()):
        name = display_name_from_bioname(row.get("bioname"))
        if not name:
            continue
        previous = identities.get(bioguide)
        if previous is None:
            identities[bioguide] = {
                "name": name,
                "bioname": (row.get("bioname") or "").strip(),
                "first_congress": congress,
                "first_service_year": congress_start_year(congress),
                "last_congress": congress,
                "chamber": (row.get("chamber") or "").strip(),
            }
        elif congress >= previous["last_congress"]:
            # Preserve the earliest Congress while keeping the latest name and
            # chamber, matching the historical-name behavior already provided.
            previous.update(
                {
                    "name": name,
                    "bioname": (row.get("bioname") or "").strip(),
                    "last_congress": congress,
                    "chamber": (row.get("chamber") or "").strip(),
                }
            )
    return identities, {
        "path": os.path.abspath(path),
        "sha256": _sha256_bytes(data),
        "rows": rows,
        "identity_window_min_congress": min_congress,
        "rows_before_window_ignored": outside_window,
        "rows_validated": len(eligible),
        "resolved_bioguide_congress_pairs": len(resolved),
        "named_bioguides": len(identities),
        "dedupe_rules": (
            "lobby_forecast.resolve_voteview_congress_rows: Senate wins a "
            "same-Congress chamber move; a mid-Congress party switch prefers "
            "the ICPSR carried into the following Congress"
        ),
        "dedupe_collapses": len(receipts),
        "role": "historical names (tier c) and member service bounds",
    }


class MemberNameResolver(object):
    """The single fallback ladder from a bioguide to a name a reader can use."""

    def __init__(
        self,
        congestion_names=None,
        legislators_names=None,
        voteview_identities=None,
        current_congress=CURRENT_CONGRESS,
        provenance=None,
    ):
        self.congestion_names = dict(congestion_names or {})
        self.legislators_names = dict(legislators_names or {})
        self.voteview_identities = dict(voteview_identities or {})
        self.current_congress = int(current_congress)
        self.provenance = dict(provenance or {})
        self._cache = {}
        self._seen = {}

    @classmethod
    def from_paths(
        cls,
        congestion_path=None,
        legislators_path=None,
        voteview_path=None,
        current_congress=CURRENT_CONGRESS,
        min_congress=VOTEVIEW_MIN_CONGRESS,
    ):
        congestion_path = congestion_path or DEFAULT_CONGESTION_PATH
        legislators_path = legislators_path or DEFAULT_LEGISLATORS_PATH
        voteview_path = voteview_path or DEFAULT_VOTEVIEW_PATH
        congestion_names, congestion_receipt = load_congestion_names(congestion_path)
        legislators_names, legislators_receipt = load_legislators_names(legislators_path)
        voteview_identities, voteview_receipt = load_voteview_identities(
            voteview_path, min_congress=min_congress
        )
        return cls(
            congestion_names=congestion_names,
            legislators_names=legislators_names,
            voteview_identities=voteview_identities,
            current_congress=current_congress,
            provenance={
                "congestion": congestion_receipt,
                "legislators": legislators_receipt,
                "voteview": voteview_receipt,
                "fallback_order": [
                    "congestion.json member",
                    "legislators.csv full_name",
                    "Voteview bioname (marked former member)",
                    "raw bioguide with a visible unresolved marker",
                ],
            },
        )

    def resolve(self, bioguide):
        """Return the identity record for one bioguide.

        Keys: ``bioguide``, ``name``, ``display``, ``source``, ``status``,
        ``is_former``, ``resolved``, ``first_congress``,
        ``first_service_year``, ``last_congress``.
        """
        key = (bioguide or "").strip()
        if not key:
            raise IdentityError("cannot resolve a blank bioguide")
        record = self._cache.get(key)
        if record is None:
            record = self._resolve_uncached(key)
            self._cache[key] = record
        self._seen[key] = record
        return record

    def _serving(self, bioguide, name, source):
        service = self.voteview_identities.get(bioguide) or {}
        return {
            "bioguide": bioguide,
            "name": name,
            "display": name,
            "source": source,
            "status": STATUS_SERVING,
            "is_former": False,
            "resolved": True,
            "first_congress": service.get("first_congress"),
            "first_service_year": service.get("first_service_year"),
            "last_congress": None,
        }

    def _resolve_uncached(self, bioguide):
        name = self.congestion_names.get(bioguide)
        if name:
            return self._serving(bioguide, name, SOURCE_CONGESTION)
        name = self.legislators_names.get(bioguide)
        if name:
            return self._serving(bioguide, name, SOURCE_LEGISLATORS)
        identity = self.voteview_identities.get(bioguide)
        if identity and identity.get("name"):
            name = identity["name"]
            return {
                "bioguide": bioguide,
                "name": name,
                "display": f"{name} ({FORMER_MARKER})",
                "source": SOURCE_VOTEVIEW,
                "status": STATUS_FORMER,
                "is_former": True,
                "resolved": True,
                "first_congress": identity.get("first_congress"),
                "first_service_year": identity.get("first_service_year"),
                "last_congress": identity.get("last_congress"),
            }
        return {
            "bioguide": bioguide,
            "name": bioguide,
            "display": f"{bioguide} ({UNRESOLVED_MARKER})",
            "source": SOURCE_NONE,
            "status": STATUS_UNRESOLVED,
            "is_former": False,
            "resolved": False,
            "first_congress": None,
            "first_service_year": None,
            "last_congress": None,
        }

    def display(self, bioguide):
        return self.resolve(bioguide)["display"]

    def name(self, bioguide):
        return self.resolve(bioguide)["name"]

    def is_former(self, bioguide):
        return self.resolve(bioguide)["is_former"]

    def receipt(self, unresolved_sample=25, former_sample=25):
        """Report what the ladder actually did for every bioguide asked about."""
        by_source = {
            SOURCE_CONGESTION: 0,
            SOURCE_LEGISLATORS: 0,
            SOURCE_VOTEVIEW: 0,
            SOURCE_NONE: 0,
        }
        unresolved = []
        former = []
        for bioguide in sorted(self._seen):
            record = self._seen[bioguide]
            by_source[record["source"]] = by_source.get(record["source"], 0) + 1
            if record["status"] == STATUS_UNRESOLVED:
                unresolved.append(bioguide)
            elif record["status"] == STATUS_FORMER:
                former.append(f"{bioguide} | {record['name']}")
        return {
            "bioguides_requested": len(self._seen),
            "by_source": by_source,
            "former_members": len(former),
            "former_member_samples": former[:former_sample],
            "former_members_capped_at": former_sample,
            "unresolved": len(unresolved),
            "unresolved_bioguides": unresolved[:unresolved_sample],
            "unresolved_capped_at": unresolved_sample,
            "fallback_order": [
                SOURCE_CONGESTION,
                SOURCE_LEGISLATORS,
                SOURCE_VOTEVIEW,
                SOURCE_NONE,
            ],
            "former_marker": FORMER_MARKER,
            "unresolved_marker": UNRESOLVED_MARKER,
        }
