"""Offices and organisations as GROUPING LAYERS, listed away from the people map.

Paul, 2026-09-06 (D-022 item 2): organisations, offices and policy areas leave
the people network entirely; a simplified Groups tab lists people under offices
and organisations.

Nothing here creates a relationship. A group is a list of people who each carry
their own dated employment evidence for that office or organisation; sharing a
group is not a tie between the people in it (D-019 owns ties, and it needs
concurrent service, which this view never asserts).

THE EMPLOYMENT RULING, 2026-09-07, verbatim: "For the filer, you do what is
chronologically the most recent. If it appears they are contract/mercenary they
are tied to all those organizations as clients."

An organisation group now carries a MEMBERSHIP KIND per person, because the
ruling gives a lobbying filer two different sorts of tie and the page must not
print them as one:

  employee   this organisation is the registrant on the person's most recent
             filing period -- their employer on file.  ONE per person.
  represents this organisation is a CLIENT that the person, a contract filer, is
             named on filings for.  MANY per person, and that is the ruling, not
             a duplicate.
  listed     what every membership was before the ruling: the person's own dated
             employment evidence names this organisation.  Unchanged, and still
             what a LittleSis-sourced organisation gets.

The ruling reaches this module through ``lda_employment`` and only through it.
Passing nothing leaves every membership `listed`, exactly as before -- this view
has to keep serving while the static artifact it reads is being rebuilt, and a
Groups tab that silently lost its organisations would be worse than one that has
not caught up yet.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import gzip
import hashlib
import json


SCHEMA = "melange-staffer-groups-index-v1"

#: Ruled scope for the Offices list: member offices, committees and leadership
#: offices. 'admin' is OUT -- administrative payroll headers are not workplaces
#: (ruling in force 2026-09-07; the same ruling put CO_TENURE_EXCLUDED_CONTAINER
#: _KINDS = {'admin'} into the social-network builder, where 54 admin containers
#: accounted for 70.8% of v5 co-tenure pairs). A cheque drawn on "SERGEANT AT
#: ARMS AND DOORKEEPER OF THE SENATE" is not evidence that anyone worked in a
#: place of that name. 'unresolved' stays out because it names nothing. This
#: list is held equal with build_staffer_groups_section.OFFICE_KINDS by
#: fec/tests/surfaces/test_staffer_groups_index.py.
OFFICE_CONTAINER_KINDS = ("member_office", "committee", "leadership")
#: A group card names at most this many people; the rest are counted in `more`.
PEOPLE_CAP = 50
#: Bound on the LittleSis person records whose catalog organisations are resolved.
LITTLESIS_PERSON_SCAN = 1000

NOTE = ("Offices and organisations group people; they are not people and cannot be "
        "route endpoints. A person is listed under a group because their own dated "
        "employment evidence names it. Sharing a group does not establish that two "
        "people worked together.")

#: How a person is tied to an organisation. See the module docstring.
MEMBERSHIP_EMPLOYEE = "employee"
MEMBERSHIP_REPRESENTS = "represents"
MEMBERSHIP_LISTED = "listed"

#: Plain words for each, in the owner's register: one line, no jargon.
MEMBERSHIP_WORDS = {
    MEMBERSHIP_EMPLOYEE: "Employer on file",
    MEMBERSHIP_REPRESENTS: "Represents this client",
    MEMBERSHIP_LISTED: "Listed here",
}

EMPLOYMENT_NOTE = (
    "A lobbying filer has one employer on file -- the registrant on their most recent filing "
    "period -- and, where that employer files for clients other than itself, is tied to every "
    "client organisation named on filings that list them. The same person under many clients "
    "is that tie, not a duplicate. No employment tenure is established by any of it.")


def _kind_of(ident, node):
    """office / organization / None for one candidate group id."""
    ident = str(ident)
    if not isinstance(node, dict):
        return "organization" if ident.startswith("littlesis:entity:") else None
    kind = node.get("node_kind") or node.get("kind")
    if kind == "container":
        return "office" if node.get("container_kind") in OFFICE_CONTAINER_KINDS else None
    if kind == "organization" and ident.startswith(("organization:", "littlesis:entity:")):
        return "organization"
    return None


def _titles_for(node, person_facts):
    """The person's own title inside each office/organisation they name."""
    titles = {}
    for role in [*(person_facts or {}).get("latest_roles", []),
                 *(person_facts or {}).get("current_roles", []),
                 *(person_facts or {}).get("past_roles", [])]:
        title = str(role.get("title") or role.get("title_last") or "").strip()
        if not title:
            continue
        for key in (role.get("employer_id"), role.get("container_id"), role.get("office_id")):
            if key:
                titles.setdefault(str(key), title)
    for stint in node.get("stints", []) or []:
        title = str(stint.get("title_last") or "").strip()
        if stint.get("container_id") and title:
            titles.setdefault(str(stint["container_id"]), title)
    return titles


def _lda_source_ids(ident, node):
    """Every ``lda:lobbyist:<n>`` this display person carries, aliases included.

    Delegates to the shared status module so the join key is defined in ONE
    place; if that module cannot be imported the ruling simply does not reach
    this view, which is the same fail-open the ``lda_employment`` argument has.
    """
    try:
        try:
            from ..staffer_network.lda_status_labels import lda_source_ids
        except ImportError:
            from fec.staffer_network.lda_status_labels import lda_source_ids
    except ImportError:
        return []
    return lda_source_ids(ident, node)


def ruling_groups(ident, node, employment):
    """The organisations the employment ruling ties this person to.

    Yields ``(organisation_id, membership, label, as_of)``: their employer first,
    then every client organisation they are a contract filer for.  A person with
    no LDA identity, or one the shipped artifact's byte caps left out, yields
    nothing -- silence, not a guess.
    """
    if not employment:
        return
    for source in _lda_source_ids(ident, node):
        record = employment.get(source)
        if not record:
            continue
        employer = record.get("employer")
        if employer:
            yield (employer, MEMBERSHIP_EMPLOYEE,
                   record.get("employer_name") or employer, record.get("as_of") or "")
        for client_id, client in (record.get("clients") or {}).items():
            yield (client_id, MEMBERSHIP_REPRESENTS,
                   client.get("name") or client_id, client.get("latest_period") or "")


def littlesis_person_ids(ident, node):
    """LittleSis person records this display person carries, its own id included."""
    found = set()
    for value in (ident, *(node.get("source_person_ids") or ()), *(node.get("identity_alias_ids") or ())):
        text = str(value or "")
        if text.startswith("littlesis:entity:"):
            found.add(text)
    return found


def _littlesis_organizations(owners, catalog):
    """{person_id: [(organization_id, label)]} from the sealed LittleSis catalog."""
    memberships = {}
    if catalog is None or not owners:
        return memberships, 0
    sources = sorted(owners)[:LITTLESIS_PERSON_SCAN]
    try:
        with closing(catalog.connect()) as database:
            for source in sources:
                rows = database.execute(
                    "SELECT id, label FROM entities WHERE kind='organization' AND id IN ("
                    "SELECT object FROM relationships WHERE subject=? UNION "
                    "SELECT subject FROM relationships WHERE object=?)", (source, source)).fetchall()
                for group_id, label in rows:
                    memberships.setdefault(owners[source], []).append((group_id, label))
    except Exception:  # a catalog fault must not take the Groups view down
        return memberships, len(sources)
    return memberships, len(sources)


def build_groups_index(people, nodes, revision, facts=None, source_catalog=None,
                       people_cap=PEOPLE_CAP, lda_employment=None):
    """Build the offices/organisations index from the kept people rows.

    `people` are the rows the people index kept, so a person can never appear in
    a group after being filtered out of the people list and the two surfaces
    always name the same population.

    `lda_employment` is the 2026-09-07 employment ruling as it was SHIPPED, in
    the shape ``fec.staffer_network.lda_employment.load_from_organisations_
    artifact`` returns.  It is OPTIONAL and defaults to off: without it every
    membership stays `listed` and this function behaves exactly as it did before
    the ruling, which is what keeps the running sidecar serving while the static
    artifact is rebuilt.
    """
    nodes = nodes or {}
    kept = {}
    for row in people:
        if row[8] != "person":
            continue
        kept[row[0]] = {"id": row[0], "name": row[1]}
    groups, dropped, littlesis_owners = {}, Counter(), {}
    employment = (lda_employment or {}).get("people") or {}
    memberships = Counter()

    def place(group_id, kind, label, person, current, title,
              membership=MEMBERSHIP_LISTED, as_of=""):
        group = groups.get(group_id)
        if group is None:
            group = groups[group_id] = {"id": group_id, "kind": kind, "name": label,
                                        "people": {}, "current_count": 0}
        entry = group["people"].get(person["id"])
        if entry is None:
            memberships[membership] += 1
            group["people"][person["id"]] = {"id": person["id"], "name": person["name"],
                                             "current": bool(current), "title": title or "",
                                             "membership": membership, "as_of": as_of or ""}
            group["current_count"] += bool(current)
        else:
            if current and not entry["current"]:
                entry["current"] = True
                group["current_count"] += 1
            if title and not entry["title"]:
                entry["title"] = title
            # An explicit ruling membership outranks a `listed` one that the
            # person's own evidence had already put here: both are true, and the
            # ruling's is the one that says WHICH sort of tie it is.
            if membership != MEMBERSHIP_LISTED and entry["membership"] == MEMBERSHIP_LISTED:
                memberships[entry["membership"]] -= 1
                memberships[membership] += 1
                entry["membership"] = membership
            if as_of and not entry["as_of"]:
                entry["as_of"] = as_of

    for ident, person in kept.items():
        node = nodes.get(ident) or {}
        person_facts = (facts.people.get(ident) if facts else None) or {}
        features = (facts.features.get(ident) if facts else None) or {}
        titles = _titles_for(node, person_facts)
        current_keys = {str(key) for key in features.get("current_employment", {})}
        current_stints = {str(stint.get("container_id")) for stint in node.get("stints", []) or []
                          if stint.get("status") == "current" and stint.get("container_id")}
        candidates = {}
        for dimension in ("current_employment", "past_employment"):
            for key, item in (features.get(dimension) or {}).items():
                candidates.setdefault(str(key), str(item.get("label") or key))
        for stint in node.get("stints", []) or []:
            container = str(stint.get("container_id") or "")
            if container:
                candidates.setdefault(container, str((nodes.get(container) or {}).get("label") or container))
        for group_id, label in candidates.items():
            group_node = nodes.get(group_id)
            kind = _kind_of(group_id, group_node)
            if kind is None:
                dropped["keys_outside_scope"] += 1
                continue
            name = str((group_node or {}).get("label") or label or group_id)
            place(group_id, kind, name, person,
                  group_id in current_keys or group_id in current_stints,
                  titles.get(group_id, ""))
        # The employment ruling. These are LDA organisations, so they are placed
        # as organisations; a container id could never arrive here because the
        # artifact this reads carries only lda:registrant / lda:client ids.
        for group_id, membership, label, as_of in ruling_groups(ident, node, employment):
            name = str((nodes.get(group_id) or {}).get("label") or label or group_id)
            place(group_id, "organization", name, person, False, "", membership, as_of)
        for source in littlesis_person_ids(ident, node):
            littlesis_owners[source] = ident

    resolved, scanned = _littlesis_organizations(littlesis_owners, source_catalog)
    for owner, rows in resolved.items():
        person = kept.get(owner)
        if person is None:
            continue
        for group_id, label in rows:
            place(group_id, "organization", str(label or group_id), person, False, "")

    offices, organizations = [], []
    for group in groups.values():
        # Employees before people who merely represent the organisation, then
        # current before past, then by name: on a client card the roster IS the
        # representing filers, and on an employer card the employees come first.
        entries = sorted(group["people"].values(),
                         key=lambda entry: (entry["membership"] != MEMBERSHIP_EMPLOYEE,
                                            not entry["current"], entry["name"].casefold(),
                                            entry["id"]))
        total = len(entries)
        listed = entries[:people_cap]
        record = {"id": group["id"], "kind": group["kind"], "name": group["name"],
                  "people_count": total, "current_count": group["current_count"],
                  "people": listed, "more": max(0, total - people_cap)}
        if group["kind"] == "office":
            # The ruling is about lobbying filers, and an office has none: every
            # membership under an office is `listed` and every as_of is empty.
            # Emitting both anyway would put two constant fields on 721 office
            # cards and invite a reader to think an office could hold an
            # employee or a client tie. The offices half of this payload is
            # byte-identical to what it was before the ruling.
            record["people"] = [{key: value for key, value in entry.items()
                                 if key not in ("membership", "as_of")} for entry in listed]
            offices.append(record)
        else:
            # The ruling's two counters are ORGANISATION facts and are emitted
            # only here.  An office cannot be a client and its people are not
            # anybody's LDA employees, so on an office card both would be a
            # constant zero -- a number that reads as "this office employs
            # nobody" rather than as "this counter does not apply".
            record["employee_count"] = sum(1 for entry in entries
                                           if entry["membership"] == MEMBERSHIP_EMPLOYEE)
            record["represented_by_count"] = sum(1 for entry in entries
                                                 if entry["membership"] == MEMBERSHIP_REPRESENTS)
            organizations.append(record)

    def order(record):
        return (-record["current_count"], -record["people_count"],
                record["name"].casefold(), record["id"])

    offices.sort(key=order)
    organizations.sort(key=order)
    payload = {"schema": SCHEMA, "ready": True, "revision": revision,
               "offices": offices, "organizations": organizations,
               "counts": {"offices": len(offices), "organizations": len(organizations),
                          "office_memberships": sum(g["people_count"] for g in offices),
                          "organization_memberships": sum(g["people_count"] for g in organizations),
                          "people_in_groups": len({person for group in groups.values()
                                                   for person in group["people"]}),
                          "people_available": len(kept),
                          "littlesis_persons_scanned": scanned,
                          "keys_outside_scope": dropped["keys_outside_scope"],
                          "memberships": dict(sorted(memberships.items())),
                          "employment_people": len(employment)},
               "office_scope": list(OFFICE_CONTAINER_KINDS), "people_cap": people_cap,
               "membership_kinds": dict(sorted(MEMBERSHIP_WORDS.items())),
               "employment_ruling_applied": bool(employment),
               "employment_ruling_truncated": bool((lda_employment or {}).get("truncated")),
               "employment_note": EMPLOYMENT_NOTE,
               "note": NOTE}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return {"revision": revision, "payload": payload, "raw": raw,
            "gzip": gzip.compress(raw, compresslevel=3),
            "etag": '"groups-' + hashlib.sha256(raw).hexdigest()[:24] + '"'}
