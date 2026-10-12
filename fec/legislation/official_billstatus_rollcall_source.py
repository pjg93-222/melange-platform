# -*- coding: utf-8 -*-
"""Official, no-key roll-call action discovery from BILLSTATUS bulk XML.

Congress.gov and GovInfo publish one complete BILLSTATUS XML record per bill or
resolution.  The record contains the same type-aware measure identity and
``recordedVotes`` associations used by the Congress.gov API actions endpoint,
but retrieving the canonical bulk-data URL does not require an API key.

This module deliberately does not fetch URLs.  It only:

* constructs the canonical official GovInfo bulk-data URL;
* verifies the XML's Congress/type/number before exposing any action;
* preserves every action and recorded-vote candidate; and
* fills a missing vote URL only when chamber/Congress/session/roll metadata
  uniquely determines the official House Clerk or Senate XML URL.

House and Senate amendments are not BILLSTATUS record types.  They remain
fail-closed and require an exhaustive official Congress.gov amendment-action
snapshot (or the Congress.gov API) rather than inference from a parent bill.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

from dataclasses import dataclass
import re
from typing import Any
import urllib.parse
import xml.etree.ElementTree as ET


BILLSTATUS_AUTHORITY = "GovInfo/Congress.gov BILLSTATUS bulk XML"
BILLSTATUS_HOST = "www.govinfo.gov"
BILLSTATUS_FORMAT_VERSIONS = frozenset({"3.0.0"})
MAX_BILLSTATUS_XML_BYTES = 64 * 1024 * 1024
BILLSTATUS_SUPPORTED_TYPES = frozenset(
    {
        "hr",
        "s",
        "hjres",
        "sjres",
        "hconres",
        "sconres",
        "hres",
        "sres",
    }
)
AMENDMENT_TYPES = frozenset({"hamdt", "samdt"})


class BillStatusSourceError(RuntimeError):
    """A fail-closed BILLSTATUS source or identity violation."""


@dataclass(frozen=True, order=True)
class BillStatusIdentity:
    congress: int
    measure_type: str
    number: int

    @property
    def canonical(self) -> str:
        return f"{self.congress}:{self.measure_type}:{self.number}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "congress": self.congress,
            "measure_type": self.measure_type,
            "number": self.number,
            "canonical_key": self.canonical,
        }


def _normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _normalized_tag(node: ET.Element) -> str:
    return _normalize_name(_local_name(node.tag))


def _normalize_measure_type(value: Any) -> str | None:
    token = _normalize_name(value)
    aliases = {
        "hr": "hr",
        "housebill": "hr",
        "s": "s",
        "senatebill": "s",
        "hjres": "hjres",
        "housejointresolution": "hjres",
        "sjres": "sjres",
        "senatejointresolution": "sjres",
        "hconres": "hconres",
        "houseconcurrentresolution": "hconres",
        "sconres": "sconres",
        "senateconcurrentresolution": "sconres",
        "hres": "hres",
        "houseresolution": "hres",
        "sres": "sres",
        "senateresolution": "sres",
        "hamdt": "hamdt",
        "houseamendment": "hamdt",
        "samdt": "samdt",
        "senateamendment": "samdt",
    }
    return aliases.get(token)


def _positive_int(value: Any, label: str) -> int:
    if value is None or isinstance(value, bool):
        raise BillStatusSourceError(f"{label} is missing")
    match = re.fullmatch(r"\s*0*(\d+)\s*", str(value))
    if not match or int(match.group(1)) <= 0:
        raise BillStatusSourceError(f"{label} is invalid: {value!r}")
    return int(match.group(1))


def _identity(congress: Any, measure_type: Any, number: Any) -> BillStatusIdentity:
    normalized_type = _normalize_measure_type(measure_type)
    if normalized_type in AMENDMENT_TYPES:
        raise BillStatusSourceError(
            "BILLSTATUS bulk XML does not publish amendment records; "
            f"{normalized_type} requires exhaustive official amendment actions"
        )
    if normalized_type not in BILLSTATUS_SUPPORTED_TYPES:
        raise BillStatusSourceError(
            f"unsupported BILLSTATUS measure type: {measure_type!r}"
        )
    return BillStatusIdentity(
        _positive_int(congress, "Congress"),
        normalized_type,
        _positive_int(number, "measure number"),
    )


def billstatus_url(congress: Any, measure_type: Any, number: Any) -> str:
    """Return the canonical HTTPS GovInfo bulk-data URL for one measure."""
    key = _identity(congress, measure_type, number)
    return (
        f"https://{BILLSTATUS_HOST}/bulkdata/BILLSTATUS/{key.congress}/"
        f"{key.measure_type}/BILLSTATUS-{key.congress}"
        f"{key.measure_type}{key.number}.xml"
    )


def validate_billstatus_url(
    url: Any, congress: Any, measure_type: Any, number: Any
) -> str:
    """Require the exact canonical official URL, with no query or fragment."""
    expected = billstatus_url(congress, measure_type, number)
    parts = urllib.parse.urlsplit(str(url or ""))
    if (
        parts.scheme.casefold() != "https"
        or (parts.hostname or "").casefold() != BILLSTATUS_HOST
        or parts.query
        or parts.fragment
        or urllib.parse.urlunsplit(parts) != expected
    ):
        raise BillStatusSourceError(
            f"BILLSTATUS source URL is not canonical for "
            f"{_identity(congress, measure_type, number).canonical}: {url!r}"
        )
    return expected


def _direct_children(node: ET.Element, *names: str) -> list[ET.Element]:
    wanted = {_normalize_name(name) for name in names}
    return [child for child in list(node) if _normalized_tag(child) in wanted]


def _direct_text(node: ET.Element, *names: str) -> str | None:
    children = _direct_children(node, *names)
    for child in children:
        text = " ".join("".join(child.itertext()).split())
        if text:
            return text
    return None


def _bill_candidates(root: ET.Element) -> list[ET.Element]:
    candidates = []
    for node in root.iter():
        if node is not root and _normalized_tag(node) != "bill":
            continue
        direct_names = {_normalized_tag(child) for child in list(node)}
        if {"congress", "billtype", "billnumber"}.issubset(direct_names):
            candidates.append(node)
    return candidates


def _bill_identity(node: ET.Element) -> BillStatusIdentity:
    return _identity(
        _direct_text(node, "congress"),
        _direct_text(node, "billType", "measureType"),
        _direct_text(node, "billNumber", "measureNumber"),
    )


def _recorded_vote_nodes(action: ET.Element) -> list[ET.Element]:
    nodes = []
    seen: set[int] = set()
    for container in action.iter():
        if _normalized_tag(container) != "recordedvotes":
            continue
        for node in container.iter():
            if node is container:
                continue
            tag = _normalized_tag(node)
            if tag not in {"recordedvote", "item"}:
                continue
            direct_names = {_normalized_tag(child) for child in list(node)}
            if "rollnumber" not in direct_names or "chamber" not in direct_names:
                continue
            identity = id(node)
            if identity not in seen:
                nodes.append(node)
                seen.add(identity)
    return nodes


def _normalize_chamber(value: Any) -> str:
    token = _normalize_name(value)
    if token in {"house", "h", "houseofrepresentatives", "ushouse"}:
        return "House"
    if token in {"senate", "s", "ussenate"}:
        return "Senate"
    raise BillStatusSourceError(f"recorded vote chamber is invalid: {value!r}")


def _date_year(value: Any) -> int | None:
    match = re.search(r"\b((?:19|20)\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def official_rollcall_xml_url(
    *,
    chamber: Any,
    congress: Any,
    session: Any,
    roll: Any,
    vote_date: Any = None,
) -> str:
    """Derive the unique official chamber XML URL from an immutable event key."""
    normalized_chamber = _normalize_chamber(chamber)
    congress_number = _positive_int(congress, "recorded vote Congress")
    session_number = _positive_int(session, "recorded vote session")
    roll_number = _positive_int(roll, "recorded vote roll number")
    if session_number not in {1, 2}:
        raise BillStatusSourceError(
            "official roll-call URL derivation supports regular sessions 1 or 2; "
            f"observed session {session_number}"
        )
    if normalized_chamber == "Senate":
        return (
            "https://www.senate.gov/legislative/LIS/roll_call_votes/"
            f"vote{congress_number}{session_number}/"
            f"vote_{congress_number}_{session_number}_{roll_number:05d}.xml"
        )
    expected_year = 1787 + (2 * congress_number) + session_number - 1
    observed_year = _date_year(vote_date)
    if observed_year is not None and observed_year != expected_year:
        raise BillStatusSourceError(
            "House recorded-vote date conflicts with Congress/session: "
            f"Congress {congress_number}, session {session_number}, "
            f"date {vote_date!r}"
        )
    return (
        f"https://clerk.house.gov/evs/{observed_year or expected_year}/"
        f"roll{roll_number:03d}.xml"
    )


def _recorded_vote(
    node: ET.Element, action_date: Any, expected_congress: int
) -> dict[str, Any]:
    chamber = _normalize_chamber(_direct_text(node, "chamber"))
    congress = _positive_int(
        _direct_text(node, "congress"), "recorded vote Congress"
    )
    if congress != expected_congress:
        raise BillStatusSourceError(
            "recorded vote Congress conflicts with BILLSTATUS measure: "
            f"expected {expected_congress}, observed {congress}"
        )
    session = _positive_int(
        _direct_text(node, "sessionNumber", "session"),
        "recorded vote session",
    )
    roll = _positive_int(
        _direct_text(node, "rollNumber", "roll"), "recorded vote roll number"
    )
    vote_date = _direct_text(node, "date", "voteDate") or action_date
    supplied_url = _direct_text(node, "url")
    canonical_xml_url = official_rollcall_xml_url(
        chamber=chamber,
        congress=congress,
        session=session,
        roll=roll,
        vote_date=vote_date,
    )
    association_url = None
    if supplied_url:
        association_url = " ".join(supplied_url.split())
        parts = urllib.parse.urlsplit(association_url)
        host = (parts.hostname or "").casefold()
        official_host = (
            host == "clerk.house.gov"
            if chamber == "House"
            else host == "senate.gov" or host.endswith(".senate.gov")
        )
        if parts.scheme.casefold() != "https" or not official_host:
            raise BillStatusSourceError(
                "BILLSTATUS recorded-vote association URL is not an official "
                f"{chamber} source: {association_url!r}"
            )
        if parts.path.casefold().endswith(".xml") and association_url != canonical_xml_url:
            raise BillStatusSourceError(
                "BILLSTATUS recorded-vote XML URL conflicts with its immutable "
                f"event key: {association_url!r}"
            )
    return {
        "chamber": chamber,
        "congress": congress,
        "date": vote_date,
        "rollNumber": roll,
        "sessionNumber": session,
        # BILLSTATUS often links a human-readable official vote page.  Preserve
        # that association separately and give the downstream XML parser the
        # canonical machine-readable URL determined by the same event key.
        "url": canonical_xml_url,
        "associationUrl": association_url,
        "urlProvenance": (
            "billstatus_recorded_vote_xml_url"
            if association_url == canonical_xml_url
            else "deterministic_official_event_key_template"
        ),
    }


def parse_billstatus_actions(
    content: bytes,
    *,
    congress: Any,
    measure_type: Any,
    number: Any,
    source_url: Any,
) -> dict[str, Any]:
    """Parse and identity-bind every action in one official BILLSTATUS record."""
    expected = _identity(congress, measure_type, number)
    canonical_source = validate_billstatus_url(
        source_url, expected.congress, expected.measure_type, expected.number
    )
    if not isinstance(content, bytes):
        raise BillStatusSourceError("BILLSTATUS XML content must be bytes")
    if len(content) > MAX_BILLSTATUS_XML_BYTES:
        raise BillStatusSourceError(
            f"BILLSTATUS XML exceeds {MAX_BILLSTATUS_XML_BYTES} bytes"
        )
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise BillStatusSourceError(
            "BILLSTATUS XML must not contain DOCTYPE or ENTITY declarations"
        )
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise BillStatusSourceError(
            f"BILLSTATUS XML is invalid for {expected.canonical}: {exc}"
        ) from exc
    if _local_name(root.tag) != "billStatus":
        raise BillStatusSourceError(
            "BILLSTATUS XML root must be billStatus"
        )
    version_nodes = [
        child for child in list(root) if _local_name(child.tag) == "version"
    ]
    version = (
        " ".join("".join(version_nodes[0].itertext()).split())
        if len(version_nodes) == 1
        else None
    )
    if version not in BILLSTATUS_FORMAT_VERSIONS:
        raise BillStatusSourceError(
            "BILLSTATUS XML must declare exactly one allowlisted format "
            f"version; observed {version!r}"
        )
    candidates = _bill_candidates(root)
    if len(candidates) != 1:
        raise BillStatusSourceError(
            f"BILLSTATUS XML must contain exactly one typed bill identity; "
            f"observed {len(candidates)}"
        )
    observed = _bill_identity(candidates[0])
    if observed != expected:
        raise BillStatusSourceError(
            f"BILLSTATUS identity mismatch: expected {expected.canonical}, "
            f"observed {observed.canonical}"
        )
    action_containers = _direct_children(candidates[0], "actions")
    if len(action_containers) != 1:
        raise BillStatusSourceError(
            f"BILLSTATUS XML for {expected.canonical} must contain exactly "
            "one complete actions collection"
        )
    action_nodes = _direct_children(action_containers[0], "item", "action")
    if not action_nodes:
        raise BillStatusSourceError(
            f"BILLSTATUS XML for {expected.canonical} has no actions"
        )
    actions = []
    for ordinal, action_node in enumerate(action_nodes, 1):
        action_date = _direct_text(action_node, "actionDate", "date")
        recorded_votes = [
            _recorded_vote(node, action_date, expected.congress)
            for node in _recorded_vote_nodes(action_node)
        ]
        actions.append(
            {
                "actionDate": action_date,
                "actionTime": _direct_text(action_node, "actionTime", "time"),
                "text": _direct_text(action_node, "text"),
                "type": _direct_text(action_node, "type"),
                "actionCode": _direct_text(action_node, "actionCode"),
                "sourceSystem": (
                    _direct_text(action_node, "sourceSystem")
                    or BILLSTATUS_AUTHORITY
                ),
                "recordedVotes": recorded_votes,
                "billstatusActionOrdinal": ordinal,
            }
        )
    return {
        "authority": BILLSTATUS_AUTHORITY,
        "source_url": canonical_source,
        "measure_key": expected.to_dict(),
        "format_version": version,
        "identity_verified": True,
        "actions_complete": True,
        "action_count": len(actions),
        "recorded_vote_count": sum(
            len(action["recordedVotes"]) for action in actions
        ),
        "actions": actions,
    }
