"""Plain-words LDA status for the people surface.

ONE table, ruled by Paul on 2026-09-06 (D-022 item 3) and reproduced here
verbatim.  Nothing in this module infers, promotes or weights anything: it maps
the sealed tiered-corroboration sidecar's already-computed tiers onto the words
a non-technical reader is shown, and carries the tier itself along for the fold.

Employer line, ruled 2026-09-07: R0, R1 and R2 all name the employer on file --
an R0 registrant IS on file, it is just not independently corroborated (see the
sealed tiered-corroboration sidecar's own receipt, registrant_tier_people). The
string "Employer not on file" is retired everywhere. What used to be signalled
by hiding the name is now said outright, next to it, as employer_corroboration.

WHICH registrant the employer line names, ruled the same day: "For the filer, you
do what is chronologically the most recent."  fec/staffer_network/lda_employment.py
owns that ruling and applies it at FILING-PERIOD precision over the LDA corpus.
This module can be handed the result -- ``load_sidecar(..., employment=...)`` --
and then the line names that registrant, with ``employer_as_of`` beside it and,
for a contract filer, ``client_count``.

Handed nothing, it falls back to the sealed sidecar's own registrant list ordered
by YEAR, which is all the sidecar records.  That fallback is not a guess: the two
orders were compared over all 25,340 filers on 2026-09-07 and agree on 25,334 of
them; the 6 they differ on are people who changed registrant inside one year, the
only case a year-granular order cannot see.  ``employer_source`` says which of the
two produced the line, per person, so a reader is never left inferring it.

Canonical and operator flags stay False by construction (D-021): a status label
is a reading aid, never a promotion.  Nothing here -- least of all the word
"employer" -- establishes employment tenure.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path


SCHEMA = "staffer-network-lda-status-labels-v1"

#: The sealed Part A sidecar this module reads.  Pinned, never discovered.
SIDECAR_GENERATION = "tiered_corroboration_sidecar_20260907_v3_primary"
SUMMARY_FILE = "tiered_person_summary_25340.ndjson.gz"
REGISTRANT_FILE = "registrant_side_records.ndjson.gz"
RECEIPT_FILE = "tiered_corroboration_receipt.json"

LDA_PREFIX = "lda:lobbyist:"

# ---------------------------------------------------------------------------
# THE TABLE.  Approved verbatim; every string below is Paul's wording.
# ---------------------------------------------------------------------------
CONFIRMED = "CONFIRMED"
LIKELY = "LIKELY"
NO_RECORD = "NO_RECORD"
NONE = "NONE"

TIER_CODES = {"L5": CONFIRMED, "L4": CONFIRMED,
              "L3": LIKELY, "L2": LIKELY,
              "L1": NO_RECORD, "L0": NO_RECORD}

CODE_LABELS = {CONFIRMED: "Lobbyist · former Hill staff, confirmed",
               LIKELY: "Lobbyist · former Hill staff, likely",
               NO_RECORD: "Lobbyist · no Hill record found",
               NONE: ""}

EMPLOYER_ON_FILE = "Employer on file: {name}"
#: NOT part of the ruled table above -- Paul ruled the R0/R1/R2 wording and the
#: retirement of "Employer not on file", not a replacement for the separate,
#: narrower case of no resolvable registrant name at any tier (every R0/R1/R2
#: person in the sealed generation carries one, so this is a defensive fallback
#: that keeps employer_line() total rather than an observed, ruled string).
#: Whatever it says, it must never be "Employer not on file".
EMPLOYER_NAME_UNKNOWN = "No employer name on file"
EMPLOYER_TIERS_ON_FILE = ("R0", "R1", "R2")

#: How solid the employer-on-file evidence is, in the owner's words. Ruled
#: 2026-09-07, alongside retiring "Employer not on file": R0 now shows the
#: same "Employer on file: {name}" line as R1/R2, and this is where the
#: difference between them is actually said.
EMPLOYER_CORROBORATION = {"R2": "corroborated by dated evidence",
                          "R1": "corroborated by disclosure",
                          "R0": "not independently corroborated"}

TIER_ORDER = ("L0", "L1", "L2", "L3", "L4", "L5")
REGISTRANT_TIER_ORDER = ("R0", "R1", "R2")

#: Where the employer line's registrant came from, per person.
EMPLOYER_SOURCE_RULING = "lda_employment_ruling"
EMPLOYER_SOURCE_SIDECAR = "sidecar_registrant_year_order"

#: Plain words for the two engagements, ruled 2026-09-07 ("contract/mercenary"
#: against in-house).  "mercenary" is the owner's word for the CONCEPT and is not
#: printed at a person: what the page says about a named individual is the neutral
#: half of his own pair.
ENGAGEMENT_LABELS = {"contract": "Contract lobbyist",
                     "in_house": "In-house lobbyist"}
CLIENT_COUNT_LINE = "Files for {count} client organisations"
CLIENT_COUNT_LINE_ONE = "Files for 1 client organisation"


class SidecarUnavailable(Exception):
    """The pinned sidecar is missing or does not match its own receipt."""


def status_code(tier):
    """Map one corroboration tier onto its ruled code. Unknown tiers are NONE."""
    return TIER_CODES.get(str(tier or "").upper(), NONE)


def status_label(code):
    """The ruled words for a code. A person who is not a lobbyist gets no label."""
    return CODE_LABELS.get(str(code or NONE).upper(), "")


def employer_line(registrant_tier, registrant_name=None):
    """R0, R1 and R2 alike name the employer on file. Ruled wording, 2026-09-07.

    A missing name cannot be turned into an employer line at any tier -- that
    part of the old rule stands. What changed is that R0 is no longer treated
    as if no registrant were on file: it is, just not independently
    corroborated, and employer_corroboration() below is where that is said.
    """
    tier = str(registrant_tier or "").upper()
    name = str(registrant_name or "").strip()
    if tier in EMPLOYER_TIERS_ON_FILE and name:
        return EMPLOYER_ON_FILE.format(name=name)
    return EMPLOYER_NAME_UNKNOWN


def employer_corroboration(registrant_tier):
    """Plain words for how solid the employer-on-file evidence is, or "".

    Companion to employer_line(): the registrant tier is already carried in
    the fold as `registrant_tier` (R0/R1/R2, a code); this is its plain-words
    pair, the same shape as status_code()/status_label() for the L-tiers.
    """
    return EMPLOYER_CORROBORATION.get(str(registrant_tier or "").upper(), "")


def engagement_label(engagement):
    """"Contract lobbyist" / "In-house lobbyist", or "" for a filer with neither.

    A filer has an engagement only where the employment ruling has been applied
    to them; without it this is empty rather than guessed, because the sidecar
    alone cannot tell a lobbying firm from an in-house shop.
    """
    return ENGAGEMENT_LABELS.get(str(engagement or "").lower(), "")


def client_count_line(count):
    """"Files for N client organisations", or "" when there is no count.

    Only a contract filer has one: an in-house filer's registrant is its own
    client, so a client count for them would be a number about nothing.
    """
    try:
        count = int(count)
    except (TypeError, ValueError):
        return ""
    if count < 1:
        return ""
    return CLIENT_COUNT_LINE_ONE if count == 1 else CLIENT_COUNT_LINE.format(count=count)


def _rank(value, order):
    try:
        return order.index(str(value or "").upper())
    except ValueError:
        return -1


def _max_tier(left, right, order):
    return left if _rank(left, order) >= _rank(right, order) else right


def normalize_source_id(value):
    """The node-id form of an LDA lobbyist id joins on its bare source id."""
    text = str(value or "").strip()
    if text.startswith("person:"):
        text = text[len("person:"):]
    return text if text.startswith(LDA_PREFIX) else ""


def lda_source_ids(ident, node):
    """Every LDA lobbyist id this display person carries, aliases included."""
    found = set()
    for value in (ident, node.get("lda_source_person_id")):
        source = normalize_source_id(value)
        if source:
            found.add(source)
    for field in ("source_person_ids", "identity_alias_ids"):
        for value in node.get(field) or ():
            source = normalize_source_id(value)
            if source:
                found.add(source)
    for record in node.get("source_identity_records") or ():
        if isinstance(record, dict):
            for value in (record.get("id"), record.get("lda_source_person_id")):
                source = normalize_source_id(value)
                if source:
                    found.add(source)
    return sorted(found)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def sidecar_dir(source_root):
    root = Path(source_root)
    if root.name != "staffer_network":
        root = root / "audits" / "staffer_network"
    return root / SIDECAR_GENERATION


def load_sidecar(directory, organization_labels=None, employment=None):
    """Read the sealed summary and registrant files, byte-bound by the receipt.

    Fails closed: a hash or receipt mismatch raises rather than serving tiers
    that do not belong to the pinned generation.

    ``employment`` is the 2026-09-07 ruling as it was SHIPPED, in the shape
    ``fec.staffer_network.lda_employment.load_from_organisations_artifact``
    returns: ``{'people': {lda_id: {...}}, 'truncated': bool, 'meta': {...}}``.
    Passing it makes the employer line name the registrant on the person's most
    recent FILING PERIOD and adds ``employer_as_of``, ``engagement`` and
    ``client_count``.  Passing nothing keeps the sidecar's year-ordered
    registrant, which is what this module has always used.
    """
    directory = Path(directory)
    receipt_path = directory / RECEIPT_FILE
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SidecarUnavailable("Tiered corroboration receipt unreadable: " + str(exc)) from exc
    if receipt.get("status") != "PASS" or receipt.get("failures"):
        raise SidecarUnavailable("Tiered corroboration receipt is not PASS")
    files = receipt.get("files")
    if not isinstance(files, dict):
        raise SidecarUnavailable("Tiered corroboration receipt has no file inventory")
    provenance = {"schema": SCHEMA, "generation": directory.name, "path": str(directory),
                  "receipt": str(receipt_path), "receipt_sha256": _sha256(receipt_path),
                  "rule_version": receipt.get("rule_version"),
                  "review_status": receipt.get("review_status"),
                  "promotion_policy": receipt.get("promotion_policy"),
                  "canonical_eligible": False, "operator_included": False,
                  "files": []}
    for name in (SUMMARY_FILE, REGISTRANT_FILE):
        spec = files.get(name)
        path = directory / name
        if not path.is_file():
            raise SidecarUnavailable("Tiered corroboration file missing: " + name)
        if not isinstance(spec, dict) or not spec.get("sha256") or not spec.get("bytes"):
            raise SidecarUnavailable(
                "Tiered corroboration receipt does not bind: " + name)
        actual = _sha256(path)
        if actual != spec["sha256"]:
            raise SidecarUnavailable("Tiered corroboration hash mismatch: " + name)
        if path.stat().st_size != spec["bytes"]:
            raise SidecarUnavailable("Tiered corroboration size mismatch: " + name)
        provenance["files"].append({"role": name, "path": str(path), "sha256": actual,
                                    "bytes": path.stat().st_size, "rows": spec.get("rows")})
    labels = organization_labels or {}
    summaries = {}
    for row in _rows(directory / SUMMARY_FILE):
        source = normalize_source_id(row.get("lda_source_person_id"))
        if source:
            summaries[source] = {"max_tier": row.get("max_tier"),
                                 "registrant_side_tier": row.get("registrant_side_tier"),
                                 "tier_by_chamber": dict(row.get("tier_by_chamber") or {}),
                                 "person_office_record_count": row.get("person_office_record_count")}
    registrants = {}
    for row in _rows(directory / REGISTRANT_FILE):
        source = normalize_source_id(row.get("lda_source_person_id"))
        if not source:
            continue
        best, best_key = "", None
        for record in row.get("registrants") or ():
            org_id = str(record.get("registrant_source_org_id") or "")
            name = str(labels.get("organization:" + org_id) or record.get("employer_name") or "").strip()
            if not name:
                continue
            key = (record.get("end_year") or 0, record.get("start_year") or 0, name)
            if best_key is None or key > best_key:
                best, best_key = name, key
        if best:
            registrants[source] = best
    employment_people = ((employment or {}).get("people") or {})
    provenance["employment_ruling_applied"] = bool(employment_people)
    provenance["employment_ruling_meta"] = (employment or {}).get("meta")
    provenance["employment_ruling_truncated"] = bool((employment or {}).get("truncated"))
    return {"summaries": summaries, "registrants": registrants, "provenance": provenance,
            "employment": employment_people,
            "counts": {"summary_rows": len(summaries), "registrant_names": len(registrants),
                       "employment_people": len(employment_people)}}


def person_status(source_ids, sidecar):
    """Fold every LDA id this person carries into one ruled status, or None.

    Where an identity decision has joined several LDA source records, the
    person's evidence is their union: the strongest tier each side reached.
    Every contributing source id stays visible in the fold.
    """
    ids = [normalize_source_id(value) for value in (source_ids or ())]
    ids = sorted({value for value in ids if value})
    if not ids:
        return None
    summaries = (sidecar or {}).get("summaries") or {}
    matched = [i for i in ids if i in summaries]
    tier, registrant_tier, chambers, records = "", "", {}, 0
    for source in matched:
        row = summaries[source]
        tier = _max_tier(tier, row.get("max_tier"), TIER_ORDER)
        registrant_tier = _max_tier(registrant_tier, row.get("registrant_side_tier"), REGISTRANT_TIER_ORDER)
        for chamber, value in (row.get("tier_by_chamber") or {}).items():
            chambers[chamber] = _max_tier(chambers.get(chamber, ""), value, TIER_ORDER)
        records += int(row.get("person_office_record_count") or 0)
    code = status_code(tier) if matched else NONE

    # The employment ruling first, the sidecar's year order second.  A merged
    # person can carry several LDA ids; the ruling is applied per LDA id, so the
    # one whose employer is on the LATEST period wins -- taking the first id in
    # sort order would let an identity decision, not a filing date, choose
    # somebody's employer.
    ruled = (sidecar or {}).get("employment") or {}
    employer_source, as_of, engagement, client_count = "", "", "", None
    name = ""
    best_as_of = ""
    for source in ids:
        record = ruled.get(source)
        if not record or not record.get("employer_name"):
            continue
        candidate = str(record.get("as_of") or "")
        if name and candidate <= best_as_of:
            continue
        name, best_as_of = str(record["employer_name"]), candidate
        employer_source = EMPLOYER_SOURCE_RULING
        as_of = candidate
        engagement = str(record.get("engagement") or "")
        client_count = record.get("client_count")

    if not name and registrant_tier.upper() in EMPLOYER_TIERS_ON_FILE:
        registrant_names = (sidecar or {}).get("registrants") or {}
        for source in matched:
            name = registrant_names.get(source) or name
            if name:
                employer_source = EMPLOYER_SOURCE_SIDECAR
                break

    clients = []
    for source in ids:
        for client_id, client in ((ruled.get(source) or {}).get("clients") or {}).items():
            clients.append({"id": client_id, "name": client.get("name") or client_id,
                            "latest_period": client.get("latest_period") or ""})
    clients.sort(key=lambda row: (row["name"], row["id"]))

    # A name the RULING supplied is a filing fact: the registrant is on that
    # person's most recent LD-2, so it is on file whatever the sidecar's
    # corroboration tier says about it.  The tier still governs
    # employer_corroboration beside it, which is where "how solid is this" is
    # said.  A name from the SIDECAR keeps the tier gate employer_line() has
    # always applied, because that name only exists for an R0/R1/R2 person.
    line = (EMPLOYER_ON_FILE.format(name=name) if employer_source == EMPLOYER_SOURCE_RULING
            else employer_line(registrant_tier, name))

    return {"code": code, "label": status_label(code),
            "employer_line": line,
            "employer_corroboration": employer_corroboration(registrant_tier),
            "employer_as_of": as_of,
            "employer_source": employer_source or None,
            "engagement": engagement or None,
            "engagement_label": engagement_label(engagement),
            "client_count": client_count,
            "client_count_line": client_count_line(client_count),
            "clients": clients,
            "tier": tier or None, "registrant_tier": registrant_tier or None,
            "tier_by_chamber": chambers, "person_office_record_count": records,
            "lda_source_person_ids": ids, "matched_source_person_ids": matched,
            "canonical_eligible": False, "operator_included": False,
            "provenance": (sidecar or {}).get("provenance")}


def attach(nodes, sidecar):
    """Write `lda_status` onto every LDA person in `nodes`; return the counts.

    Without a loaded sidecar nothing is written: an unlabelled person is honest,
    a person labelled from no evidence is not.
    """
    attached, joined, distribution = 0, 0, {}
    ruled, sidecar_order, engagements = 0, 0, {}
    if not (sidecar or {}).get("summaries"):
        return {"lda_people": 0, "joined_to_summary": 0, "labels": {}, "status": "sidecar_unavailable"}
    for ident, node in (nodes or {}).items():
        if not isinstance(node, dict) or node.get("kind") != "person":
            continue
        status = person_status(lda_source_ids(ident, node), sidecar)
        if status is None:
            node.pop("lda_status", None)
            continue
        node["lda_status"] = status
        attached += 1
        joined += bool(status["matched_source_person_ids"])
        distribution[status["code"]] = distribution.get(status["code"], 0) + 1
        if status.get("employer_source") == EMPLOYER_SOURCE_RULING:
            ruled += 1
            key = status.get("engagement") or "unstated"
            engagements[key] = engagements.get(key, 0) + 1
        elif status.get("employer_source") == EMPLOYER_SOURCE_SIDECAR:
            sidecar_order += 1
    return {"lda_people": attached, "joined_to_summary": joined, "labels": distribution,
            "employer_from_ruling": ruled,
            "employer_from_sidecar_year_order": sidecar_order,
            "employer_none": attached - ruled - sidecar_order,
            "engagements": dict(sorted(engagements.items()))}
