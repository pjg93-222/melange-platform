# -*- coding: utf-8 -*-
"""Producer for the Melange dashboard's simplified "Groups" surface.

Read-only and receipt-gated, the sibling of build_staffer_network_section.py and
built on that module's binding: the same generation, the same PASS-with-no-
failures verification receipt, the same sha256 gate on every consumed byte.

What this file is for (owner ruling D-022, 2026-09-06): organisations, offices
and policy areas LEAVE the people network.  Where the network section answers
"who is around this member", this one answers the flat directory question --
which people are listed under which office, and which people are listed under
which lobbying registrant.

THREE ARTIFACTS, NOT ONE (owner rulings 2026-09-07).  The single combined
document measured 8,285,129 bytes against the real generation -- 4,125,631 of
offices and 4,152,031 of organisations -- so it could never fit the
5,000,000-byte producer pin no matter how the caps were turned.  The parts are
independent directories that share nothing but a generation, so this producer
writes them as separate files, each with its own meta, its own counts and its
own byte cap:

  staffer_offices_section.json        {meta, offices}
  staffer_organisations_section.json  {meta, organisations}       registrants
  staffer_clients_section.json        {meta, c, cn, e, en, f, fn, fe, t, tp}

``--part offices|organisations|clients|both`` selects which.  ``both`` is all-or-
nothing on the cap: if any part is refused NONE is written, because a page built
from a fresh part and a stale part is worse than a page that did not rebuild.

Three rules this producer will not bend:

  * ONLY PEOPLE ARE PEOPLE.  A container or an organization node never enters a
    ``people`` or ``lobbyists`` list.  ``assert_person_only`` re-checks every
    emitted identifier against the group and organization id sets read from the
    same generation, so a join slip fails the build instead of printing an
    office as a staffer.
  * ONCE PER GROUP.  A person appears at most once under any one office and at
    most once under any one registrant, however many stints or filings back the
    membership.  Appearing under two different groups is a fact about the
    person, not a duplicate.
  * NO EMPLOYMENT CLAIM.  D-021: employment tenure is NOT established for any of
    the sealed LDA identities.  The status line is the corroboration tier said in
    the owner's plain words, and the employer line says only what is on file.

CURRENT DAY ONLY (owner ruling 2026-09-07, item 1).  An office is emitted at all
only when at least one person on its payroll record is current today
(``current_count`` >= 1); a payroll record that is entirely history is not a
directory entry for today's Hill.  Every current person in a shown office IS
listed, in full -- ``OFFICE_CURRENT_SAFETY_CAP`` is a safety valve against one
pathological office, not a product dial, and a real office is not expected to
come near it.  Who served earlier is a fact this page still states, but as a
count (``earlier_count``) and never as a second list of names: D-022 already
drew that line for organisations (a person's employer history beyond the LDA
record on file is not printed either), and this producer draws it the same way
for offices.  Organisations are unaffected by the current-day rule: every
registrant with at least one lobbyist on file is shown regardless of whether
that filing is current, because a registrant is not a payroll record with a
"today".

ONE EMPLOYER PER FILER, AND CLIENTS FOR THE CONTRACT ONES (owner ruling
2026-09-07, verbatim): "For the filer, you do what is chronologically the most
recent. If it appears they are contract/mercenary they are tied to all those
organizations as clients."

fec/staffer_network/lda_employment.py is the ONE place that sentence is turned
into code, and its module docstring is where the three definitions are written
out in full.  What changes here:

  * A filer is listed under exactly ONE registrant -- the one on their most
    recent filing period -- as an employee.  Before this ruling a person was
    listed under every registrant that had ever filed naming them, which put the
    same person on several cards with nothing saying which was their employer.
    Earlier registrants become ``former_employers`` on that person's own line and
    do NOT list them as one of their people.
  * A registrant card carries the employer line ONCE, because under this ruling
    every person on that card has that registrant as their employer, so the old
    per-person copy of the same string said nothing the card did not.  The
    per-person line now carries what actually varies: ``employer_as_of`` (the
    period), ``engagement`` (contract or in-house) and, for a contract filer,
    ``client_count``.
  * CLIENT ORGANISATIONS are the third artifact, NORMALIZED.  They were cards in
    the organisations document for one build; all 41,203 of them measured
    21,012,744 bytes, and the threshold that fit the pin shipped 203 -- half a
    percent of what the ruling reaches.  The bytes were the repeated NAMES, not
    the ties, so staffer_clients_section.json writes each name once and each tie
    as a pair of positions, which holds every client and every one of the 132,738
    ties in about 4 MB.  A person under many clients is the ruling, not a
    duplicate, and the normalized form is what lets all of them ship.

CLIENT NORMALIZATION, and the numbers behind it (MEASURED 2026-09-07 against the
real corpus; these replace the client ladder this file used to carry):

  cards, all 41,203 clients                        21,012,744 B    4.2x the pin
  cards, thresholded to fit (203 clients)           4,108,656 B    0.5% shipped
  normalized, objects {id,name} + index arrays      5,249,260 B    OVER by 249,260
  normalized, 1-2 char keys + parallel arrays      ~3,950,000 B    ALL 41,203

The readable object form does not fit and the short-key form does: 41,203 copies
of the key strings are the difference.  ``meta.key_legend`` ships in the document
so the shape explains itself, and fec/staffer_network/lda_employment.py owns both
the legend and the reader, so a writer and a reader cannot drift.

SINGLE-FILER REGISTRANTS (owner ruling 2026-09-07).  A registrant with two or
more employees is listed in full, capped at ``--org-cap`` with the remainder in
``more``.  A registrant with exactly one employee is a NAME-ONLY line --
``{id, kind, name, lobbyist_count}`` with no ``lobbyists`` array at all -- so the
registrant stays in the directory and the single filer is not named.  UNCHANGED
by the employment ruling; only the population it selects moved, because
"lobbyists on file under this registrant" is now "employees of this registrant".
If even that will not fit, the name-only lines are dropped too and only
``meta.singleton_registrants`` survives; ``meta.singleton_mode`` always says
which of the two is in force.  KNOWN CONSEQUENCE, said here rather than left to
be discovered: those people are no longer reachable from the page's search
box, because the search haystack is built from the names the page prints.

THE CLIENT LADDER IS RETIRED.  It raised a minimum-representing-filers threshold
until the organisations document fit the pin, and the rung that fit shipped 203
of 41,203.  Its measurements are kept here because they are the argument for the
normalization that replaced it -- capping ``represented_by`` at 8 saved 862,639
bytes against a 16,012,744-byte overrun, and requiring two or more representing
filers still left 12.2 MB over, so no cap on the CARD form was ever going to
work:

  all_clients                 41,203 clients   21,012,744 B   4.2x the pin
  roster_capped (cap 8)       41,203 clients   20,150,105 B
  min 2 representing filers   27,944 clients   17,211,893 B
  min 3                       19,275 clients   14,554,256 B
  min 5                        8,651 clients    9,984,453 B
  min 10                       1,588 clients    5,261,994 B   over by 261,994
  min 20                         203 clients    4,108,656 B   was SHIPPED

Nothing is thresholded now.  ``build_clients`` either holds every client and
every tie or refuses to write, and ``counts.client_organisations_dropped`` and
``counts.filer_client_ties_dropped`` are in the artifact, at zero, so that is a
number a reader can check rather than a claim in a docstring.

A COMPANY CAN APPEAR TWICE, and that is the LDA's shape rather than a duplicate.
Registrants and clients have separate id spaces, so BOEING COMPANY is one card as
a registrant (its 52 in-house lobbyists) and another as a client (the 23 outside
contract lobbyists it hires).  99 organisations are in both lists by name on the
2026-09-07 build.  ``kind`` and the summary line are what tell the two apart.

GENERATION SHAPES.  This producer reads two generation shapes and says which one
it read in ``meta.container_source``:

  v5 and earlier  containers are rows in ``staffer_social_node_scores`` with
                  ``node_kind == 'container'``.
  v6 and later    node scores are PERSONS ONLY (D-022) and the containers,
                  organisations and policy areas are rostered in the generation's
                  own ``staffer_social_groups.ndjson.gz``, bound here through the
                  receipt's ``groups`` file role like every other consumed byte.

ADMINISTRATIVE PAYROLL HEADERS ARE NOT WORKPLACES (ruling in force 2026-09-07).
Container kind ``admin`` is not an office in EITHER shape: ``OFFICE_KINDS`` is
the whitelist and admin is not in it, so a "SERGEANT AT ARMS AND DOORKEEPER OF
THE SENATE" payroll header never becomes a directory entry that reads as a place
someone worked.  ``unresolved`` is out for the same reason it always was -- it
names nothing.

Dates are the record's dates: ``tenure_days`` is the union of half-open stint
intervals in that one office, so concurrent assignments count a day once, and it
is null where no stint of that person in that office carried two parseable
dates.  Senate-lane stints are bounded by reporting periods, so those days are
period edges rather than observed service days (the network section's
``tenure_note`` says this and is carried through unedited).

COST.  The organisations part streams the LDA activity assertions (110 MB gz) and
the industry edges (430 MB gz) to apply the employment ruling, which takes 45-80
seconds depending on the file cache; before the ruling it took about 4.  The
offices part is unaffected.  This producer is NOT a run_pipeline step (checked
2026-09-07: run_pipeline.py names it nowhere), so that cost lands on the test
suite and on operators, not on the chain.

Usage:
    python fec\\surfaces\\build_staffer_groups_section.py
    python fec\\surfaces\\build_staffer_groups_section.py --part organisations --org-cap 12
    python fec\\surfaces\\build_staffer_groups_section.py --out-dir <dir> --max-bytes 5000000
    python fec\\surfaces\\build_staffer_groups_section.py --client-roster-cap 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEC = os.path.dirname(_HERE)
_ROOT_FOR_IMPORT = os.path.dirname(_FEC)
for _p in (_FEC, _ROOT_FOR_IMPORT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from paths import LOCAL_MELANGE, to_host  # noqa: E402

try:                                            # package context (kernel suites, chain)
    from . import build_staffer_network_section as network
except ImportError:                             # direct script execution
    from fec.surfaces import build_staffer_network_section as network

try:                                            # package context
    from ..staffer_network import lda_employment
except ImportError:                             # direct script execution
    from fec.staffer_network import lda_employment

InputContradiction = network.InputContradiction
digest = network.digest
rows = network.rows
built_at = network.built_at
parse_day = network.parse_day
union_tenure_days = network.union_tenure_days
display_name_for = network.display_name_for
NAME_UNAVAILABLE = network.NAME_UNAVAILABLE

PRODUCER_VERSION = 'staffer-groups-section-v4'
# One schema id per artifact: the two files no longer share a document shape, and
# a reader that finds `offices` must be able to tell it is not looking at a
# truncated copy of the old combined v1 document.
#
# The ORGANISATIONS id is v4 as of the 2026-09-07 client normalization: rows
# gained `kind` and a lobbyist row traded its (now redundant) per-person
# `employer_line` for `registrant_tier`, `employer_as_of`, `engagement` and
# `client_count` at v3, and at v4 the client CARDS left this document for the
# normalized clients artifact.  A reader that expects a v3 document to contain
# `represented_by` rosters must not be handed a v4 one quietly: the ties are all
# still there, in a sibling file, and the version bump is what says so.
# OFFICES stays v2 -- neither ruling touched a single field of it.
SCHEMA_ID = 'staffer-groups-section-v4'
OFFICES_SCHEMA_ID = 'staffer-offices-section-v2'
ORGANISATIONS_SCHEMA_ID = 'staffer-organisations-section-v4'
CLIENTS_SCHEMA_ID = lda_employment.CLIENTS_SCHEMA

OFFICES_FILENAME = 'staffer_offices_section.json'
ORGANISATIONS_FILENAME = 'staffer_organisations_section.json'
CLIENTS_FILENAME = lda_employment.CLIENTS_FILENAME

PART_OFFICES = 'offices'
PART_ORGANISATIONS = 'organisations'
PART_CLIENTS = 'clients'
PART_BOTH = 'both'
PARTS = (PART_OFFICES, PART_ORGANISATIONS, PART_CLIENTS, PART_BOTH)
#: What ``both`` means. THREE artifacts since the client normalization, and the
#: all-or-nothing cap rule covers all three for the reason it covered two: a page
#: assembled from a fresh part and a stale part is worse than one that did not
#: rebuild, and no reader can tell the parts apart once they are on the page.
BOTH_PARTS = (PART_OFFICES, PART_ORGANISATIONS, PART_CLIENTS)
PART_FILENAMES = {PART_OFFICES: OFFICES_FILENAME,
                  PART_ORGANISATIONS: ORGANISATIONS_FILENAME,
                  PART_CLIENTS: CLIENTS_FILENAME}

DEFAULT_ROOT = network.DEFAULT_ROOT
GENERATION = network.GENERATION
SIDECAR_GENERATION = 'tiered_corroboration_sidecar_20260907_v3_primary'
LDA_GENERATION = 'generation-20260830T231200Z'
# The House personnel ledger's own member-quarter records: the one place a
# HON:<bioguide> office whose member is off the current roster has ever had its
# payroll office string printed anywhere this producer binds.  Owner ruling
# 2026-09-07, item 2.
HON_LEDGER_GENERATION = 'house_personnel_ledger_20260903_v5_primary'

# Off-repo, machine-local store; same derivation as fec/surfaces/staffer_source_catalog.py:17.
LDA_DATA_ROOT = os.path.join(LOCAL_MELANGE, 'staffer_network', 'normalized', 'lda-source-native')

SIDECAR_RECEIPT = 'tiered_corroboration_receipt.json'
SIDECAR_REGISTRANTS = 'registrant_side_records.ndjson.gz'
SIDECAR_SUMMARY = 'tiered_person_summary_25340.ndjson.gz'
LDA_RECEIPT = 'lda_source_native_corpus_receipt.json'
LDA_LOBBYISTS = 'lda_lobbyist_assertions.ndjson.gz'
# The employment ruling's own three files.  The organisations and activities give
# a filing its registrant, its client and its period; the industry edges give the
# lobbyist -> activity link (NAMED_ON_ACTIVITY), which is the only one of the two
# candidate links that reaches all 25,340 filers -- see lda_employment.
LDA_ORGANISATIONS = 'lda_organization_assertions.ndjson.gz'
LDA_ACTIVITIES = 'lda_activity_assertions.ndjson.gz'
LDA_EDGES = 'lda_industry_edges.ndjson.gz'
HON_LEDGER_RECEIPT = 'house_personnel_ledger_receipt.json'
HON_LEDGER_QUARTERS = 'house_member_quarters.ndjson.gz'

# The v6 grouping roster: containers, organisations and policy areas, once each,
# in the generation's own words.  Declared in the generation receipt under role
# 'groups'; absent from a v5 generation, which is how the two shapes are told
# apart without a version string anywhere in the data.
GROUPS_ROLE = 'groups'
GROUPS_NAME = 'staffer_social_groups.ndjson.gz'
CONTAINER_SOURCE_GROUPS = 'staffer_social_groups'
CONTAINER_SOURCE_NODE_SCORES = 'staffer_social_node_scores'

# Administrative payroll headers are not workplaces (ruling in force 2026-09-07),
# so 'admin' is not here; neither is 'unresolved', which names nothing.  This is
# the whitelist for BOTH generation shapes -- there is no second list anywhere.
OFFICE_KINDS = ('member_office', 'committee', 'leadership')
EXCLUDED_CONTAINER_KINDS_NOTE = (
    'Container kinds outside %s are not offices on this page. An administrative payroll header '
    '(kind admin) is a place a cheque came from, not a place someone worked, and a container '
    'whose kind is unresolved names nothing.' % ', '.join(OFFICE_KINDS))

# Owner ruling 2026-09-07, item 1: offices and organisations are capped
# separately.  OFFICE_CURRENT_SAFETY_CAP was written as a safety valve against
# one pathological office and documented as "not expected to bind in practice".
# MEASURED 2026-09-07 against the real v5 generation: it binds on 37 of the 721
# emitted offices -- COMMITTEE ON APPROPRIATIONS (202 current, 142 unnamed),
# JUDICIARY (138 current, 78 unnamed), COMMITTEE ON OVERSIGHT AND ACCOUNTABILITY
# (130 current, 70 unnamed) and 34 more, every one of them a large committee
# rather than an anomaly.  So it IS a product dial today, and the claim that it
# is not has been taken out of CURRENT_DAY_NOTE, which is printed on the page.
# The count is emitted as counts.offices_over_current_cap every build, so this
# number never has to be believed on the strength of a comment again.  Raising
# the cap costs bytes in the offices artifact and must be re-measured against
# MAX_BYTES; that is an owner call, not a maintenance edit.  ORG_CAP is a size
# dial in the same way, tuned against --max-bytes below.
#
# ORG_CAP history, kept because the numbers are the argument.  Against the real
# generation, in ONE combined document: cap 12 measured 8,645,476 bytes and cap 8
# measured 8,285,129 -- both over the 5,000,000 pin, which is what forced the
# split.  SPLIT, at cap 8: offices 4,133,061 and organisations 3,520,189, both
# clear.  The organisations half has roughly 1.48 MB of headroom at cap 8; do not
# spend it by raising the cap without re-measuring against the real generation.
OFFICE_CURRENT_SAFETY_CAP = 60
ORG_CAP = 8
MAX_BYTES = 5_000_000

# A registrant with exactly this many employees is a name-only line.
SINGLETON_LOBBYIST_COUNT = 1
SINGLETON_MODE_LINES = 'name_only_lines'
SINGLETON_MODE_COUNT = 'count_only'

# --------------------------------------------------------------------------
# ORGANISATION KIND, and where the client half went.
#
# KIND_REGISTRANT cards carry `lobbyists` -- the people whose most recent filing
# period names that registrant, i.e. its employees.  That is the WHOLE of the
# organisations artifact again, as it was before the client work.
#
# KIND_CLIENT is kept as the word the CLIENTS artifact uses for what it holds, and
# as the value a pre-split organisations document on disk still carries on its
# retired client cards.  This producer no longer emits such a card.
#
# THE CLIENT LADDER IS GONE, deliberately, and this is what replaced it.  The
# ladder raised a minimum-representing-filers threshold until the document fit the
# byte pin, and against the real corpus the rung that fit shipped 203 of the
# 41,203 client organisations the ruling reaches -- half a percent.  A threshold
# was never the right instrument: the bytes were not the ties, they were the NAMES,
# repeated once per card.  The clients artifact writes each name once and the ties
# as integer indices, which holds all 41,203 clients and all 132,738 ties in about
# 4 MB.  Re-introducing a threshold here would drop client organisations that the
# normalized form has room for, so the rungs are removed rather than left unused.
# --------------------------------------------------------------------------
KIND_REGISTRANT = 'registrant'
KIND_CLIENT = 'client'

ENGAGEMENT_WORDS = {lda_employment.CONTRACT: 'contract',
                    lda_employment.IN_HOUSE: 'in-house'}

# --------------------------------------------------------------------------
# LDA status vocabulary -- the owner's own words (D-022 item 3), verbatim.
#
# DUPLICATION NOTE, and it is a KNOWN duplication, not an oversight.
# fec/staffer_network/lda_status_labels.py did not exist when this producer was
# written (checked across fec/ and audits/), so the table is defined here.  It
# appeared later the same night from a concurrent lane, uncommitted and still in
# flight, and carries a whole sidecar loader beside the table.  Binding a shipping
# producer to another lane's mid-edit module is the DE-017 failure, so this copy
# stays for now and the two are held EQUAL instead: test_staffer_groups_section
# [i2] imports that module when it is importable and fails the moment either side
# edits a status line or an employer line.  Consolidating is one import plus
# deleting this block and that test case -- an owner call, once that lane lands.
# --------------------------------------------------------------------------
LDA_STATUS_LABELS = {
    'L5': 'Lobbyist · former Hill staff, confirmed',
    'L4': 'Lobbyist · former Hill staff, confirmed',
    'L3': 'Lobbyist · former Hill staff, likely',
    'L2': 'Lobbyist · former Hill staff, likely',
    'L1': 'Lobbyist · no Hill record found',
    'L0': 'Lobbyist · no Hill record found',
}
EMPLOYER_ON_FILE = 'Employer on file: %s'
# Ruled 2026-09-07, held equal with fec/staffer_network/lda_status_labels.py:
# R0, R1 and R2 all name the employer on file (an R0 registrant IS on file,
# just not independently corroborated); "Employer not on file" is retired
# everywhere. EMPLOYER_NAME_UNKNOWN is the (unruled, defensive-only) fallback
# for the separate case of no resolvable name at any tier -- see the shared
# module for why that case is not expected to occur in the sealed generation.
EMPLOYER_NAME_UNKNOWN = 'No employer name on file'
EMPLOYER_TIERS_ON_FILE = ('R0', 'R1', 'R2')
# The fold phrase for employer_line's corroboration strength, held equal with
# the shared module's EMPLOYER_CORROBORATION of the same name.
EMPLOYER_CORROBORATION = {'R2': 'corroborated by dated evidence',
                          'R1': 'corroborated by disclosure',
                          'R0': 'not independently corroborated'}
LDA_STATUS_NOTE = (
    'The lobbyist line is the corroboration tier from the tiered-corroboration sidecar, '
    'said plainly. It is not an employment finding: no employment tenure is established '
    'for any of these identities.')

# This section's tenure is per OFFICE.  The network section's tenure_note describes a
# per-MEMBER field of the same name, so printing that one here would put a sentence on
# the page that the numbers beside it cannot be reconciled with.
TENURE_NOTE = (
    'Days on the record is the union of that person\'s dated stints in THIS office, so '
    'concurrent assignments count a day once. Senate-lane stints are bounded by reporting '
    'periods, so those days are period edges rather than observed service days, and the '
    'figure is absent where no stint in this office carried two parseable dates.')

# Owner ruling 2026-09-07, item 1: what "current" means on this page, said once
# here in full rather than left implicit in the cap numbers beside it.
CURRENT_DAY_NOTE = (
    'An office is shown only when at least one person on its payroll record is current '
    'today. Current people are listed by name, up to %d per office; where an office has '
    'more current people than that the remainder is counted on that office\'s own line and '
    'not named. People who served earlier in the same office are counted as earlier staff '
    'and are not named on this page either. counts.offices_over_current_cap says how many '
    'offices in this build were affected.' % OFFICE_CURRENT_SAFETY_CAP)

PERSON_PREFIX = 'person:'
LOBBYIST_PREFIX = 'lda:lobbyist:'


def singleton_rule_note(org_cap):
    return (
        'A registrant with %d or more employees is listed in full, capped at %d with the '
        'remainder counted in `more`. A registrant with exactly one employee is a '
        'name-only line -- id, name and lobbyist_count, with no lobbyists array -- so the '
        'registrant stays in the directory and the single filer is not named. Where even the '
        'name-only lines will not fit the byte cap they are dropped as well and only '
        'meta.singleton_registrants remains; meta.singleton_mode says which is in force.'
        % (SINGLETON_LOBBYIST_COUNT + 1, org_cap))


def employment_rule_note():
    """The ruling as the page states it, in the owner's own sentence plus what it
    means for the cards here.  Printed in the Sources-and-method fold."""
    return (
        'A person is listed under one registrant only: the one on their most recent filing '
        'period, which is their employer on file. Registrants they filed under earlier are '
        'carried on that person\'s own line as former employers and do not list them. Where '
        'that employer files for clients other than itself the person is a contract filer, and '
        'every client organisation on every filing that names them is tied to them in the '
        'clients artifact. The same person appearing under many clients is what the ruling '
        'says, not a duplicate. ' + lda_employment.NO_TENURE_NOTE)


def clients_elsewhere_note(clients, ties, filename):
    """What the organisations artifact says about the half that left it.

    An artifact that quietly stops carrying something a reader used to find in it
    is the failure mode this string exists to prevent: the count is stated here,
    in the document that no longer holds it, with the name of the file that does.
    """
    return ('Client organisations are not cards in this document. Every one of them -- %s '
            'clients and the %s filer-client ties the ruling reaches, complete -- is in %s, '
            'where each name is written once and the ties are indices into those tables. '
            'They were cards here until 2026-09-07, and the byte cap had thresholded them '
            'down to 203 of %s.' % ('{:,}'.format(clients), '{:,}'.format(ties), filename,
                                    '{:,}'.format(clients)))


def clients_rule_note(clients, filers, ties):
    """What the clients artifact is showing, in full, with its own numbers."""
    return ('Every client organisation the ruling reaches is here: %s of them, tied to %s '
            'contract filers by %s filer-client ties, with nothing dropped and no threshold '
            'applied. A name appears exactly once in this document; a tie is a pair of '
            'positions in the tables above it. meta.key_legend says what each key holds.'
            % ('{:,}'.format(clients), '{:,}'.format(filers), '{:,}'.format(ties)))


# ------------------------------------------------------------------- binding

def bind_file(path, spec, role, bound_against):
    """Fail closed unless the file on disk is the exact file the receipt names."""
    if not os.path.isfile(path):
        raise InputContradiction('%s input is missing: %s' % (role, path))
    size = os.path.getsize(path)
    got = digest(path)
    if not isinstance(spec, dict) or not isinstance(spec.get('sha256'), str) or not spec['sha256'].strip():
        raise InputContradiction('%s receipt records no sha256' % role)
    want_bytes, want_sha = spec.get('bytes'), spec['sha256']
    if want_bytes is not None and int(want_bytes) != size:
        raise InputContradiction('%s is %d bytes, receipt says %s' % (role, size, want_bytes))
    if want_sha != got:
        raise InputContradiction('%s sha256 %s does not match the receipt %s'
                                 % (role, got, want_sha))
    return {'path': path, 'bytes': size, 'sha256': got, 'bound_against': list(bound_against)}


def bind_generation_groups(binding):
    """Bind the v6 grouping roster, or report that this is a v5-shaped generation.

    Returns ``(path_or_None, bound_or_None)``.  A generation whose receipt
    declares no ``groups`` file is v5-shaped and its containers are read from the
    node scores instead -- that is a shape, not a fault, so it is not an error.
    A receipt that DOES declare the file and cannot produce it, or produces a
    different one, IS a fault and refuses here like every other bound byte.

    A declared groups file must also have an independent verifier integrity row:
    it is bound against the generation receipt's own sha256 and both hashes in
    that row. Older v5 generations remain compatible because they declare no
    groups file at all.
    """
    declared = None
    entries = binding['receipt'].get('files')
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise InputContradiction('generation receipt files must be a list of objects')
    for entry in entries:
        if entry.get('role') == GROUPS_ROLE:
            declared = entry
            break
    if declared is None:
        return None, None
    if declared.get('path') != GROUPS_NAME:
        raise InputContradiction('generation receipt names %r for role %r, expected %r'
                                 % (declared.get('path'), GROUPS_ROLE, GROUPS_NAME))
    path = os.path.join(binding['base'], GROUPS_NAME)
    bound = bind_file(path, declared, GROUPS_ROLE, ['generation_receipt.files'])
    integrity = {entry.get('path'): entry
                 for entry in binding['verification'].get('primary_file_integrity') or []}
    recorded = integrity.get(GROUPS_NAME)
    if recorded is None:
        raise InputContradiction('verification receipt records no integrity row for %s' % GROUPS_NAME)
    for label, expected in (
            ('verification_receipt.primary_file_integrity.receipt_sha256',
             recorded.get('receipt_sha256')),
            ('verification_receipt.primary_file_integrity.recomputed_sha256',
             recorded.get('recomputed_sha256'))):
        if not expected:
            raise InputContradiction('%s records no sha256 for %s' % (label, GROUPS_NAME))
        if expected != bound['sha256']:
            raise InputContradiction('sha256 mismatch for %s: computed %s, %s records %s'
                                     % (GROUPS_NAME, bound['sha256'], label, expected))
        bound['bound_against'].append(label)
    bound['bound_against'] = sorted(set(bound['bound_against']))
    return path, {'staffer_social_groups': bound}


def bind_sidecar(root, sidecar_generation):
    base = os.path.join(root, 'audits', 'staffer_network', sidecar_generation)
    receipt_path = os.path.join(base, SIDECAR_RECEIPT)
    if not os.path.isfile(receipt_path):
        raise InputContradiction('sidecar receipt absent: %s' % receipt_path)
    with open(receipt_path, encoding='utf-8') as handle:
        receipt = json.load(handle)
    if receipt.get('status') != 'PASS' or receipt.get('failures'):
        raise InputContradiction('sidecar receipt is not a clean PASS (%s, %d failures)'
                                 % (receipt.get('status'), len(receipt.get('failures') or [])))
    files = receipt.get('files') or {}
    bound = {'tiered_corroboration_receipt': {
        'path': receipt_path, 'bytes': os.path.getsize(receipt_path),
        'sha256': digest(receipt_path), 'bound_against': ['read']}}
    paths = {}
    for role, name in (('registrant_side_records', SIDECAR_REGISTRANTS),
                       ('tiered_person_summary', SIDECAR_SUMMARY)):
        path = os.path.join(base, name)
        bound[role] = bind_file(path, files.get(name), role, ['tiered_corroboration_receipt.files'])
        paths[role] = path
    return {'base': base, 'bound': bound, 'paths': paths, 'receipt': receipt}


def bind_lda(lda_root, lda_generation):
    base = os.path.join(lda_root, lda_generation)
    receipt_path = os.path.join(base, LDA_RECEIPT)
    if not os.path.isfile(receipt_path):
        raise InputContradiction('LDA corpus receipt absent: %s' % receipt_path)
    with open(receipt_path, encoding='utf-8') as handle:
        receipt = json.load(handle)
    if receipt.get('status') != 'PASS' or receipt.get('failures'):
        raise InputContradiction('LDA corpus receipt is not a clean PASS (%s, %d failures)'
                                 % (receipt.get('status'), len(receipt.get('failures') or [])))
    specs = {f.get('path'): f for f in (receipt.get('files') or [])}
    # Four files now, not one: the lobbyist assertions for names, and the three
    # the employment ruling reads.  Every one is bound against the corpus
    # receipt's own sha256 before a byte of it is used, exactly as before -- the
    # employment ruling did not loosen the gate, it widened what passes through it.
    bound = {
        'lda_source_native_corpus_receipt': {
            'path': receipt_path, 'bytes': os.path.getsize(receipt_path),
            'sha256': digest(receipt_path), 'bound_against': ['read']},
    }
    paths = {}
    for role, name in (('lda_lobbyist_assertions', LDA_LOBBYISTS),
                       ('lda_organization_assertions', LDA_ORGANISATIONS),
                       ('lda_activity_assertions', LDA_ACTIVITIES),
                       ('lda_industry_edges', LDA_EDGES)):
        spec = specs.get(name)
        if spec is None:
            raise InputContradiction('LDA corpus receipt names no %s' % name)
        path = os.path.join(base, name)
        bound[role] = bind_file(path, spec, role, ['lda_source_native_corpus_receipt.files'])
        paths[role] = path
    return {'base': base, 'bound': bound, 'paths': paths,
            'path': paths['lda_lobbyist_assertions'],
            'privacy_class': specs[LDA_LOBBYISTS].get('privacy_class')}


def bind_hon_ledger(root, generation):
    """Bind the House personnel ledger's member-quarter file, the same way every
    other input here is bound: the ledger's own receipt must read PASS, and the
    one file this producer actually opens is checked against that receipt's own
    sha256, never trusted because its name matches.  This receipt carries no
    ``failures`` list of its own -- its internal checks are invariant_checks and
    kiley_gold_check, this producer's business is only the ``status`` flag and the
    byte-identity of the one file it reads.
    """
    base = os.path.join(root, 'audits', 'staffer_network', generation)
    receipt_path = os.path.join(base, HON_LEDGER_RECEIPT)
    if not os.path.isfile(receipt_path):
        raise InputContradiction('House personnel ledger receipt absent: %s' % receipt_path)
    with open(receipt_path, encoding='utf-8') as handle:
        receipt = json.load(handle)
    if receipt.get('status') != 'PASS':
        raise InputContradiction('House personnel ledger receipt is not a clean PASS (%s)'
                                 % receipt.get('status'))
    spec = next((f for f in (receipt.get('outputs') or []) if f.get('path') == HON_LEDGER_QUARTERS),
               None)
    if spec is None:
        raise InputContradiction('House personnel ledger receipt names no %s' % HON_LEDGER_QUARTERS)
    path = os.path.join(base, HON_LEDGER_QUARTERS)
    bound = {
        'house_personnel_ledger_receipt': {
            'path': receipt_path, 'bytes': os.path.getsize(receipt_path),
            'sha256': digest(receipt_path), 'bound_against': ['read']},
        'house_member_quarters': bind_file(
            path, spec, 'house_member_quarters', ['house_personnel_ledger_receipt.outputs']),
    }
    return {'base': base, 'bound': bound, 'path': path}


# ------------------------------------------------------------------- offices

def read_containers_from_node_scores(path):
    """v5 shape.  One streaming pass: the office containers, and every non-person
    id there is.

    The id sets are what assert_person_only tests against later; they are read
    from the same file that supplies the offices, so the check cannot be fooled
    by a container the producer simply did not look at.
    """
    offices, non_person, kinds = {}, set(), Counter()
    for row in rows(path):
        kind = row.get('node_kind')
        kinds[kind] += 1
        if kind == 'person':
            continue
        node_id = row.get('node_id')
        if node_id:
            non_person.add(node_id)
        if kind != 'container':
            continue
        if row.get('container_kind') not in OFFICE_KINDS:
            continue
        offices[node_id] = {
            'id': node_id,
            'kind': row.get('container_kind'),
            'chamber': row.get('chamber'),
            'office_key': row.get('office_key'),
            'member_bioguide': row.get('member_bioguide') or None,
        }
    return offices, non_person, kinds


def read_containers_from_groups(path):
    """v6 shape.  The generation's own grouping roster is the container source.

    The v6 node scores are persons only (D-022), so nothing there could supply
    either the offices or the non-person id set; both come from this file, which
    rosters every container, organisation and policy area exactly once.  A group
    row's ``name`` is the same string a v5 container row carried in
    ``office_key`` (verified byte-identical on the 2,000-person dry run), so the
    naming policy below is generation-independent and needs no second branch.
    """
    offices, non_person, kinds = {}, set(), Counter()
    for row in rows(path):
        kind = row.get('group_kind')
        kinds[kind] += 1
        group_id = row.get('group_id')
        if group_id:
            non_person.add(group_id)
        if kind != 'container':
            continue
        if row.get('container_kind') not in OFFICE_KINDS:
            continue
        offices[group_id] = {
            'id': group_id,
            'kind': row.get('container_kind'),
            'chamber': row.get('chamber'),
            'office_key': row.get('office_key') or row.get('name'),
            'member_bioguide': row.get('member_bioguide') or None,
        }
    return offices, non_person, kinds


def read_office_containers(binding, groups_path):
    """Dispatch on the shape the generation actually has, and say which it was.

    Returns ``(offices, non_person_ids, kind_counts, source)``.
    """
    if groups_path is not None:
        offices, non_person, kinds = read_containers_from_groups(groups_path)
        source = CONTAINER_SOURCE_GROUPS
    else:
        offices, non_person, kinds = read_containers_from_node_scores(
            binding['paths']['staffer_social_node_scores'])
        source = CONTAINER_SOURCE_NODE_SCORES
    if not offices:
        raise InputContradiction('%s carries no container of kind %s'
                                 % (source, ', '.join(OFFICE_KINDS)))
    return offices, non_person, kinds, source


def read_office_people(path, offices):
    """One streaming pass over the trajectories.

    Per (office, person) it keeps whether any stint there is current, the title
    printed on the representative stint, the dated intervals, and the latest
    end date.  The representative stint is the maximum of
    (is_current, core_end_exclusive, core_start, title_last), the same rule
    build_staffer_network_section.read_trajectories applies, so the two sections
    print the same title for the same person in the same office.
    """
    facts = defaultdict(dict)      # office id -> person id -> record
    attachments = 0
    people_seen = set()
    for row in rows(path):
        person = row.get('spine_person_id')
        if not person:
            raise InputContradiction('a trajectory row carries no spine_person_id')
        for stint in (row.get('stints') or []):
            office = stint.get('container_id')
            if office not in offices:
                continue
            current = stint.get('status') == 'current'
            title = stint.get('title_last') or None
            start, end = parse_day(stint.get('core_start')), parse_day(stint.get('core_end_exclusive'))
            bucket = facts[office]
            record = bucket.get(person)
            if record is None:
                record = bucket[person] = {'current': False, 'title': None, 'rank': None,
                                           'intervals': [], 'last_end': None}
                attachments += 1
                people_seen.add(person)
            record['current'] = record['current'] or current
            rank = (current, str(stint.get('core_end_exclusive') or ''),
                    str(stint.get('core_start') or ''), title or '')
            if record['rank'] is None or rank > record['rank']:
                record['rank'], record['title'] = rank, title
            if start and end:
                # A stint that ends before it starts is a contradiction in the
                # generation, not a row to quietly drop: dropping it would shorten
                # someone's days on the record with nothing on the page or in the
                # receipt saying so.  The network section refuses on the same
                # condition (union_tenure_days), so this one does too.
                if end < start:
                    raise InputContradiction(
                        'stint for %s in %s ends %s before it starts %s'
                        % (person, office, stint.get('core_end_exclusive'),
                           stint.get('core_start')))
                record['intervals'].append((start, end))
            if end and (record['last_end'] is None or end > record['last_end']):
                record['last_end'] = end
    return {'facts': facts, 'attachments': attachments, 'people': people_seen}


OFFICE_NAME_POLICY = (
    'A member office whose bioguide is on the current roster is named for that member. '
    'A House office key of the form HON:FIRST|LAST carries the member name in the record '
    'itself, so those name parts are rendered through the same name standard the people '
    'use. A HON:<bioguide> key whose member is not on the current roster is named from '
    'the House personnel ledger\'s own most recent printed office string for that '
    'bioguide, rendered the same way: a record\'s own words, not an invented label. Every '
    'other office keeps the record\'s own office_key verbatim, including a HON:<bioguide> '
    'key the ledger never printed a name for -- those are counted in offices_unnamed '
    'rather than guessed at.')

_HON_PREFIX = 'HON:'

# office_name() status values -- meta counters only, never printed on the page.
HON_STATUS_ROSTER = 'roster'
HON_STATUS_KEY_SPELLED = 'key_spelled'
HON_STATUS_LEDGER = 'ledger'
HON_STATUS_UNRESOLVED = 'unresolved_hon'
HON_STATUS_OTHER = 'other'


def office_name(office, roster, ledger_names):
    """What an office is called on the page.

    Four cases, in order, and none of them is a match: (1) the container already
    names a roster bioguide, so the roster supplies the member's name; (2) the
    House key literally spells the member out as ``HON:FIRST|LAST``, so those parts
    are rendered by ``name_standard`` exactly as a person's would be -- that is a
    rendering of the record's own words, not an identity claim; (3) the container
    carries a bioguide the House personnel ledger printed a "HON. FIRST LAST"
    office string for at some point, even though that bioguide never made the
    current roster -- the ledger's own most recent such string is rendered the
    same way (owner ruling 2026-09-07, item 2); (4) anything else keeps the
    office_key verbatim.  A HON:<bioguide> key that reaches case 4 -- the ledger
    never printed a name for that bioguide either -- is the one counted in
    ``offices_unnamed``; a plain committee or leadership office_key that was never
    a HON: key in the first place also lands in case 4, harmlessly, because it was
    never a naming candidate to begin with.

    Returns ``(name, status)``; ``status`` is one of the ``HON_STATUS_*``
    constants above, read by the caller only to fill the meta counters.
    """
    bioguide = office.get('member_bioguide')
    if bioguide and bioguide in roster:
        return roster[bioguide]['display_name'], HON_STATUS_ROSTER
    key = office.get('office_key') or office['id']
    if not key.startswith(_HON_PREFIX):
        return key, HON_STATUS_OTHER
    remainder = key[len(_HON_PREFIX):]
    spelled = ' '.join(part for part in remainder.split('|') if part.strip())
    # A bioguide ("K000404") is an identifier, not a name; only a remainder that
    # carries alphabetic name parts is rendered.
    if spelled and not any(char.isdigit() for char in spelled):
        rendered = network.name_standard.display_name(
            network.name_standard.parse_name(spelled, 'display'))
        if rendered:
            return rendered, HON_STATUS_KEY_SPELLED
    if bioguide:
        resolved = ledger_names.get(bioguide)
        if resolved:
            return resolved, HON_STATUS_LEDGER
        return key, HON_STATUS_UNRESOLVED
    return key, HON_STATUS_OTHER


def load_hon_ledger_names(path):
    """bioguide -> rendered display name, from the House personnel ledger's own
    member-quarter records.

    Used only for HON:<bioguide> containers whose bioguide is not on the current
    roster (D-022's naming rule never invents a label the record did not print):
    the ledger's ``office_family_normalized`` field carries that same office's own
    "HON. FIRST LAST" string, one row per bioguide per reporting quarter.  The
    most recent quarter's string wins when a bioguide's printed name changed over
    time (a correction, a name change).  A bioguide the ledger never printed a
    HON. string for -- or printed one for that does not reduce to alphabetic name
    parts -- is simply absent from the returned map, and ``office_name`` leaves
    that office's key exactly as printed rather than guessing.
    """
    best = {}                                   # bioguide -> (quarter, family string)
    for row in rows(path):
        bioguide = row.get('member_bioguide')
        family = row.get('office_family_normalized')
        quarter = row.get('quarter')
        if not bioguide or not family or not quarter:
            continue
        if not family.upper().startswith('HON.'):
            continue
        prior = best.get(bioguide)
        if prior is None or quarter > prior[0]:
            best[bioguide] = (quarter, family)
    names = {}
    for bioguide, (_quarter, family) in best.items():
        remainder = family[4:].strip() if family[:4].upper() == 'HON.' else family.strip()
        if not remainder or any(char.isdigit() for char in remainder):
            continue
        rendered = network.name_standard.display_name(
            network.name_standard.parse_name(remainder, 'display'))
        if rendered:
            names[bioguide] = rendered
    return names


def select_current_people(bucket, cap):
    """Every CURRENT person in this office, current-day only (owner ruling
    2026-09-07, item 1).

    Who served earlier is a count (``earlier_count``), never a second list of
    names here.  The cap is the safety valve of last resort against one
    pathological office, so this sort key only has to be deterministic, not
    meaningful -- the page re-sorts the kept set by display_name once names are
    joined.
    """
    current = [(person, fact) for person, fact in bucket.items() if fact['current']]
    ordered = sorted(current, key=lambda kv: kv[0])
    return ordered[:cap], max(0, len(ordered) - cap)


# ------------------------------------------------------------- organisations

def read_registrants(path):
    """One streaming pass over the registrant-side records.

    Returns registrant id -> {name, people}, the per-person corroboration tier,
    and the count of people whose filings name no registrant at all.

    AFTER THE EMPLOYMENT RULING this is no longer where membership comes from --
    lda_employment owns that, from the corpus itself, at filing-period precision.
    What is still read here is the sidecar's own business: the R0/R1/R2
    corroboration tier that gates the employer line, and the registrant names it
    recorded.  ``sidecar_latest`` is kept as a CROSS-CHECK on the ruling, not as
    an input to it: the sidecar can only order registrants by year, the ruling
    orders them by period, and the build reports how often the two disagree
    (6 of 25,340 filers, measured 2026-09-07) rather than assuming they cannot.
    """
    groups = {}
    tier_by_person = {}
    employer_by_person = {}
    sidecar_latest = {}
    unplaced = 0
    seen = set()
    for row in rows(path):
        person = row.get('lda_source_person_id')
        if not person:
            raise InputContradiction('a registrant-side row carries no lda_source_person_id')
        if person in seen:
            raise InputContradiction('registrant-side records repeat %s' % person)
        seen.add(person)
        tier_by_person[person] = row.get('registrant_side_tier')
        employer_by_person[person] = ((row.get('ld203') or {}).get('matched_registrant_name')
                                      or None)
        placed = False
        best_key = None
        for entry in (row.get('registrants') or []):
            org = entry.get('registrant_source_org_id')
            name = (entry.get('employer_name') or '').strip()
            if not org:
                continue
            group = groups.get(org)
            if group is None:
                group = groups[org] = {'id': org, 'name': name or org, 'people': []}
            elif name and not group['name']:
                group['name'] = name
            if person not in group['people']:      # once per group
                group['people'].append(person)
                placed = True
            key = (entry.get('end_year') or 0, entry.get('start_year') or 0, org)
            if best_key is None or key > best_key:
                best_key = key
                sidecar_latest[person] = org
        if not placed:
            unplaced += 1
    return {'groups': groups, 'tier': tier_by_person, 'employer': employer_by_person,
            'unplaced': unplaced, 'people': seen, 'sidecar_latest': sidecar_latest}


def read_max_tier(path, wanted):
    tiers = {}
    for row in rows(path):
        person = row.get('lda_source_person_id')
        if person in wanted:
            tiers[person] = row.get('max_tier')
    return tiers


def read_lobbyist_names(path, wanted):
    names = {}
    for row in rows(path):
        person = row.get('source_person_id')
        if person in wanted:
            names[person] = network.name_standard.display_name(
                network.name_standard.parse_name(row.get('display_name') or '', 'display')
            ) or NAME_UNAVAILABLE
    return names


def status_label(tier):
    label = LDA_STATUS_LABELS.get(tier)
    if label is None:
        raise InputContradiction('unknown LDA corroboration tier %r; this producer refuses to '
                                 'guess a status line for it' % (tier,))
    return label


def employer_line(tier, registrant_name):
    if tier in EMPLOYER_TIERS_ON_FILE and registrant_name:
        return EMPLOYER_ON_FILE % registrant_name
    return EMPLOYER_NAME_UNKNOWN


def employer_corroboration(tier):
    return EMPLOYER_CORROBORATION.get(tier, '')


def is_singleton(group):
    """A registrant with one employee. UNCHANGED rule; the population moved.

    ``group['people']`` used to be everyone the registrant had ever filed naming;
    since the employment ruling it is the registrant's EMPLOYEES -- the people
    whose most recent filing period names it.  The rule itself, "exactly one
    person means a name-only line", is exactly as it was.
    """
    return len(group['people']) <= SINGLETON_LOBBYIST_COUNT


def singleton_line(group):
    """A registrant with one employee: the directory entry, and no names.

    Deliberately carries no ``lobbyists`` key at all rather than an empty list --
    the page has to tell "one person on file, not named here" apart from "nobody
    on file", and an empty array says the second thing.
    """
    return {'id': group['id'], 'name': group['name'],
            'lobbyist_count': len(group['people'])}


# ----------------------------------------------------------------- integrity

def assert_office_people_only(offices, non_person_ids):
    """No container and no organization is ever printed as a person.

    Both forms are tested: the id as emitted, and the id with this producer's own
    ``person:`` prefix taken back off.  Prefixing is what would otherwise hide the
    failure -- a container id that reached the trajectories would be emitted as
    ``person:container:office:...`` and would match neither the container id set
    nor a bare prefix test.
    """
    offenders = []
    for office in offices:
        for person in office['people']:
            ident = person['id']
            bare = ident[len(PERSON_PREFIX):] if ident.startswith(PERSON_PREFIX) else ident
            if (not ident.startswith(PERSON_PREFIX)
                    or ident in non_person_ids or bare in non_person_ids):
                offenders.append((office['id'], ident))
    _raise_offenders(offenders)


def assert_lobbyists_only(organisations, non_person_ids):
    """No organisation ever reaches a people list.

    ``represented_by`` is still checked, though this producer no longer writes it:
    the check is what would catch a client card reaching this document by some
    other road, and ``assert_no_client_cards`` refusing it separately does not
    make a second, cheaper guard wrong.
    """
    offenders = []
    for org in organisations:
        for key in ('lobbyists', 'represented_by'):
            for person in org.get(key) or ():
                ident = person['id']
                if ident in non_person_ids or not ident.startswith(LOBBYIST_PREFIX):
                    offenders.append((org['id'], ident))
    _raise_offenders(offenders)


def assert_one_employer_per_person(organisations):
    """A person is an employee of exactly one registrant. That IS the ruling.

    Appearing under several CLIENTS is the ruling too, so this checks only the
    ``lobbyists`` rosters -- the employee side.
    """
    seen = {}
    for org in organisations:
        if org.get('kind') != KIND_REGISTRANT:
            continue
        for person in org.get('lobbyists') or ():
            other = seen.setdefault(person['id'], org['id'])
            if other != org['id']:
                raise InputContradiction(
                    'the employment ruling gives each filer one employer, but %s is listed as an '
                    'employee of both %s and %s' % (person['id'], other, org['id']))


def assert_no_client_cards(organisations):
    """The organisations artifact is registrants again, and only registrants.

    The client half moved to its own normalized artifact.  A client card left in
    this document would be the 21 MB shape coming back one row at a time, and --
    worse -- a second, thresholded copy of ties the sibling already holds in full,
    so a reader could get two different answers to "who represents this client".
    """
    offenders = [org['id'] for org in organisations
                 if org.get('kind') != KIND_REGISTRANT or 'represented_by' in org]
    if offenders:
        raise InputContradiction(
            '%d organisation row(s) are not registrant cards (e.g. %s); client ties belong in %s'
            % (len(offenders), offenders[0], CLIENTS_FILENAME))


def assert_clients_normalized(payload, engagement):
    """Every check the clients artifact has to pass before it may be written.

    NAMES ONCE.  ``cn``/``fn``/``en`` are the only places a name is allowed to
    live; the tie arrays hold integers.  This is the whole point of the artifact,
    so it is asserted rather than assumed: a name that leaked back into a tie row
    is the 21 MB document returning by the back door.

    INDEX INTEGRITY.  Every tie index resolves to a filer, every period code
    resolves to a label, and the parallel arrays are the same length.  A dangling
    index would silently under-report a client's roster in the browser, which is
    exactly the failure the normalization was supposed to make impossible.

    CONTRACT ONLY.  The ruling's client tie is conditioned on "if it appears they
    are contract/mercenary"; an in-house filer in ``f`` would be this producer
    inventing a tie the ruling does not make.
    """
    clients, names = payload['c'], payload['cn']
    filers, filer_names = payload['f'], payload['fn']
    employers, employer_names = payload['e'], payload['en']
    ties, periods = payload['t'], payload['tp']
    labels = payload['meta']['period_labels']
    lengths = {'c/cn': (len(clients), len(names)), 'c/t': (len(clients), len(ties)),
               'c/tp': (len(clients), len(periods)), 'f/fn': (len(filers), len(filer_names)),
               'f/fe': (len(filers), len(payload['fe'])),
               'e/en': (len(employers), len(employer_names))}
    for label, (left, right) in sorted(lengths.items()):
        if left != right:
            raise InputContradiction('%s are parallel arrays of different lengths (%d vs %d)'
                                     % (label, left, right))
    for table, label in ((clients, 'c'), (filers, 'f'), (employers, 'e')):
        if len(set(table)) != len(table):
            raise InputContradiction('%s lists an id more than once' % label)
    for position, row in enumerate(ties):
        if len(row) != len(periods[position]):
            raise InputContradiction('client %s has %d ties and %d periods'
                                     % (clients[position], len(row), len(periods[position])))
        if len(set(row)) != len(row):
            raise InputContradiction('client %s lists a filer more than once' % clients[position])
        for index in row:
            if not isinstance(index, int) or not 0 <= index < len(filers):
                raise InputContradiction('client %s carries tie index %r, which resolves to no '
                                         'filer' % (clients[position], index))
        for code in periods[position]:
            if not isinstance(code, int) or not 0 <= code < len(labels):
                raise InputContradiction('client %s carries period code %r, which resolves to no '
                                         'label' % (clients[position], code))
    for position, slot in enumerate(payload['fe']):
        if not isinstance(slot, int) or not 0 <= slot < len(employers):
            raise InputContradiction('filer %s carries employer index %r, which resolves to no '
                                     'registrant' % (filers[position], slot))
    offenders = [p for p in filers if engagement.get(p) != lda_employment.CONTRACT]
    if offenders:
        raise InputContradiction(
            '%d in-house filer(s) reached the client tables (e.g. %s); only contract filers are '
            'tied to clients' % (len(offenders), offenders[0]))
    # NAMES ONCE, checked in two cheap passes rather than one quadratic one.  The
    # sets are hoisted deliberately: rebuilding them per value made this O(n*m)
    # over 55,447 strings against a 52,000-name set -- about 2.9 billion
    # comparisons, which turned a two-second check into an apparent hang.
    written_names = set(names) | set(filer_names) | set(employer_names)
    for key in ('t', 'tp'):
        for row in payload[key]:
            for value in row:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise InputContradiction(
                        'key %r carries %r; a tie is a pair of positions, and every name in this '
                        'artifact lives once, in cn/fn/en' % (key, value))
    for key in ('c', 'f', 'e'):
        for value in payload[key]:
            if value in written_names:
                raise InputContradiction('key %r carries the name %r; names live once, in '
                                         'cn/fn/en' % (key, value))


def _raise_offenders(offenders):
    if offenders:
        raise InputContradiction('%d non-person identifier(s) reached a people list (e.g. %s in %s)'
                                 % (len(offenders), offenders[0][1], offenders[0][0]))


def assert_once_per_group(groups, member_key, label):
    for group in groups:
        ids = [person['id'] for person in (group.get(member_key) or ())]
        if len(ids) != len(set(ids)):
            raise InputContradiction('%s %s lists a person more than once' % (label, group['id']))


def portable_path(path, root):
    """What a path may say on a page that gets shipped.

    A path under the project root becomes root-relative.  The LDA corpus lives in
    a machine-local store outside the repo, and printing "C:/Users/<name>/AppData
    /Local/..." on a surface publishes the operator's home directory to every
    reader of the page for no gain: the sha256 beside it is what identifies the
    file.  Those keep only <generation>/<file> and say where they came from.

    A path that is ALREADY relative is left alone.  network.bind_inputs records
    the generation's own files by bare name, joinable with meta.generation_root
    (that is exactly how fec/audit/audit_full.py re-hashes the sibling section);
    resolving those against the process's current working directory would make
    the shipped provenance depend on where the producer happened to be launched
    from.

    NOT IDEMPOTENT, and it does not need to be.  Re-running this over its own
    output turns a machine-local path into a repository one, because "already
    relative" is exactly what a machine-local path looks like after one pass.
    ``portable_inputs`` no longer feeds it its own output -- it copies each spec
    and always starts from the binding's absolute path -- so the second pass this
    used to rely on surviving no longer happens.
    """
    if not os.path.isabs(path):
        return {'path': path.replace('\\', '/'), 'store': 'repository'}
    absolute = os.path.abspath(path)
    try:
        relative = os.path.relpath(absolute, root)
    except ValueError:                                   # different drive
        relative = None
    if relative and not relative.startswith(os.pardir):
        return {'path': relative.replace('\\', '/'), 'store': 'repository'}
    return {'path': '/'.join((os.path.basename(os.path.dirname(absolute)),
                              os.path.basename(absolute))),
            'store': 'machine-local'}


def portable_inputs(inputs, root):
    """Rewrite every input spec for publication, WITHOUT touching the binding.

    This used to update the spec dicts in place, and the spec dicts are the ones
    ``bind_lda``/``bind_sidecar`` handed out -- one object shared by every part of
    the build.  A second call therefore saw a path this function had already
    rewritten, and ``portable_path`` reads an already-relative path as living in
    the repository: a MACHINE-LOCAL input silently relabelled itself
    ``store: repository`` on the second pass.

    That was reachable two ways.  The organisations ladder re-assembled its
    payload once per rung, so any build that did not fit on its first rung
    published the LDA corpus as a repository file -- which is every real-corpus
    build the client ladder ever walked.  And with three parts sharing one corpus
    binding, the second part to be built would have hit it every time.

    Copying the spec instead of mutating it removes the class of bug rather than
    the two instances: every call now starts from the absolute path the binding
    recorded, so the answer cannot depend on how many times this ran.
    """
    portable = {}
    for role, spec in inputs.items():
        if isinstance(spec, dict) and spec.get('path'):
            spec = dict(spec)
            spec.update(portable_path(spec['path'], root))
        portable[role] = spec
    return portable


def offenders_by_size(groups, member_key):
    """What to name when a byte cap refuses a write.

    ``member_key`` is the roster the caller cares about; a client card carries
    ``represented_by`` instead, and is counted on that so the refusal names the
    real offenders rather than reporting every client as size zero.
    """
    biggest = sorted(groups, key=lambda g: len(json.dumps(g, ensure_ascii=False)),
                     reverse=True)[:5]
    return [(g['name'], len(g.get(member_key) or g.get('represented_by') or ()))
            for g in biggest]


def write_all_atomic(items):
    """Stage every artifact first, then rename them all.

    ``network.write_atomic`` writes AND renames one file at a time.  With two
    artifacts that leaves a window in which the offices half is the new build and
    the organisations half is still the previous one -- exactly the half-built
    directory the all-or-nothing cap check above exists to prevent, arriving by a
    different road.  Staging both first shrinks that window to the gap between two
    ``os.replace`` calls, and any failure while staging leaves BOTH files exactly
    as they were.

    The rename retry is the same one ``network.write_atomic`` carries and for the
    same reason: this tree lives under OneDrive, which intermittently holds a
    handle open and returns WinError 5 or 32.

    ``items`` is ``[(path, payload_bytes), ...]``; returns ``{path: attempts}``.
    """
    staged = []
    attempts = {}
    try:
        for path, payload_bytes in items:
            directory = os.path.dirname(os.path.abspath(path))
            os.makedirs(directory, exist_ok=True)
            handle, temp = tempfile.mkstemp(dir=directory, prefix='.staffer_groups_section.',
                                            suffix='.tmp')
            staged.append((os.path.abspath(path), temp))
            with os.fdopen(handle, 'wb') as stream:
                stream.write(payload_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        for path, temp in staged:
            delay = 0.25
            failure = None
            for attempt in range(12):
                try:
                    os.replace(temp, path)
                    attempts[path] = attempt + 1
                    failure = None
                    break
                except PermissionError as exc:
                    failure = exc
                except OSError as exc:
                    if getattr(exc, 'winerror', None) not in (5, 32):
                        raise
                    failure = exc
                if attempt < 11:
                    time.sleep(delay)
                    delay = min(delay * 2, 4.0)
            if failure is not None:
                raise failure
        return attempts
    finally:
        failure_in_flight = sys.exc_info()[0] is not None
        for _path, temp in staged:
            if os.path.exists(temp):
                try:
                    os.remove(temp)
                except OSError:
                    # Cleanup can fail under OneDrive too. Keep the producer's
                    # original failure; a surviving temp is not a clean build.
                    if not failure_in_flight:
                        raise


def serialize(payload):
    """Serialize to the shipped bytes, iterating meta.bytes to its own fixpoint.

    meta.bytes is written into the document it measures, so it is solved for
    rather than guessed: the shipped number is never a lie about the shipped file.
    """
    for _ in range(8):
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                ensure_ascii=False, allow_nan=False)
        payload_bytes = serialized.encode('utf-8')
        if len(payload_bytes) == payload['meta']['bytes']:
            return payload_bytes
        payload['meta']['bytes'] = len(payload_bytes)
    raise RuntimeError('byte-count fixpoint did not settle')


# ---------------------------------------------------------------------- main

def base_meta(root, binding, generation, schema_id, part, max_bytes):
    """The provenance every part carries, identically.

    The whole generation binding rides on BOTH artifacts because BOTH pass
    through the same receipt gate, whether or not a given part opened every one
    of those files; ``meta.reads`` names the ones this part actually opened, so
    "bound" and "read" stay distinguishable to a reader.
    """
    return {
        'schema_id': schema_id,
        'part': part,
        'siblings': [PART_FILENAMES[name] for name in BOTH_PARTS if name != part],
        'producer': os.path.relpath(os.path.abspath(__file__), root).replace('\\', '/'),
        'producer_version': PRODUCER_VERSION,
        'generation': generation,
        'generation_root': os.path.relpath(binding['base'], root).replace('\\', '/'),
        'verification': {
            'status': binding['verification'].get('status'),
            'failures': len(binding['verification'].get('failures') or []),
            'path': os.path.relpath(binding['verification_path'], root).replace('\\', '/'),
            'sha256': digest(binding['verification_path']),
        },
        'exploratory': True,
        'review_status': 'REVIEW_REQUIRED',
        'canonical_eligible': False,
        'canonical_employment_promotions': 0,
        'max_bytes': max_bytes,
        'built_at': built_at(),
        'bytes': 0,
    }


def build_offices(root, binding, generation, roster, offices, non_person_ids, node_kinds,
                  container_source, groups_bound, hon_ledger_generation, office_cap, max_bytes):
    """The offices artifact: {meta, offices}."""
    attached = read_office_people(binding['paths']['staffer_trajectories'], offices)
    hon_ledger = bind_hon_ledger(root, hon_ledger_generation)
    ledger_names = load_hon_ledger_names(hon_ledger['path'])

    selected = {}
    wanted_people = set()
    for office_id, bucket in attached['facts'].items():
        kept, more = select_current_people(bucket, office_cap)
        if not kept:
            continue          # no CURRENT person here: excluded (item 1)
        selected[office_id] = (kept, more)
        wanted_people.update(person for person, _ in kept)

    receipt = binding['receipt']
    name_spec = next((s for s in receipt.get('inputs', [])
                      if s['role'] == 'hill_person_spine'), None)
    if name_spec is None:
        raise InputContradiction('the generation receipt binds no hill_person_spine; this '
                                 'section is a directory of people and will not print one '
                                 'without names')
    original = os.path.normpath(to_host(name_spec['path']))
    name_path = os.path.join(root, 'audits', 'staffer_network',
                             os.path.basename(os.path.dirname(original)),
                             os.path.basename(original))
    if not os.path.isfile(name_path):
        name_path = original
    spine_bound = bind_file(name_path, name_spec, 'hill_person_spine',
                            ['generation_receipt.inputs'])
    names = {}
    for record in rows(name_path):
        person = record.get('spine_person_id')
        if person in wanted_people:
            names[person] = display_name_for(record)

    office_rows = []
    offices_unnamed = 0
    offices_hon_ledger_resolved = 0
    for office_id, office in sorted(offices.items()):
        if office_id not in selected:
            continue           # no CURRENT person: excluded (item 1)
        kept, more = selected[office_id]
        bucket = attached['facts'][office_id]
        current_count = sum(1 for f in bucket.values() if f['current'])
        earlier_count = len(bucket) - current_count
        people = []
        for person, fact in kept:
            people.append({
                'id': PERSON_PREFIX + person,
                'display_name': names.get(person) or NAME_UNAVAILABLE,
                'title_last': fact['title'],
                'current': bool(fact['current']),
                'tenure_days': (union_tenure_days(fact['intervals'])
                                if fact['intervals'] else None),
            })
        people.sort(key=lambda p: p['display_name'])
        name, status = office_name(office, roster, ledger_names)
        if status == HON_STATUS_LEDGER:
            offices_hon_ledger_resolved += 1
        elif status == HON_STATUS_UNRESOLVED:
            offices_unnamed += 1
        office_rows.append({
            'id': office_id,
            'name': name,
            'kind': office['kind'],
            'chamber': office.get('chamber'),
            'member_bioguide': office.get('member_bioguide'),
            'people_count': len(bucket),
            'current_count': current_count,
            'earlier_count': earlier_count,
            'people': people,
            'more': more,
        })
    office_rows.sort(key=lambda o: (-o['current_count'], -o['people_count'], o['name']))

    inputs = dict(binding['bound'])
    inputs.update(groups_bound or {})
    inputs['hill_person_spine'] = spine_bound
    inputs.update(hon_ledger['bound'])
    reads = [container_source, 'staffer_trajectories', 'hill_person_spine',
             'house_member_quarters']

    meta = base_meta(root, binding, generation, OFFICES_SCHEMA_ID, PART_OFFICES, max_bytes)
    meta.update({
        'hon_ledger_generation': hon_ledger_generation,
        'inputs': portable_inputs(inputs, root),
        'reads': sorted(set(reads)),
        'container_source': container_source,
        'office_kinds': list(OFFICE_KINDS),
        'excluded_container_kinds_note': EXCLUDED_CONTAINER_KINDS_NOTE,
        'office_name_policy': OFFICE_NAME_POLICY,
        'current_day_note': CURRENT_DAY_NOTE,
        'office_current_safety_cap': office_cap,
        'tenure_note': TENURE_NOTE,
        'counts': {
            'offices': len(office_rows),
            # How often the safety cap actually bound.  Measured, not assumed:
            # see the OFFICE_CURRENT_SAFETY_CAP comment for why this counter exists.
            'offices_over_current_cap': sum(1 for o in office_rows if o['more']),
            'current_people_not_named': sum(o['more'] for o in office_rows),
            'people_listed': len({p['id'] for office in office_rows for p in office['people']}),
            'office_people_total': len(attached['people']),
            'office_attachments_total': attached['attachments'],
            'offices_unnamed': offices_unnamed,
            'offices_hon_ledger_resolved': offices_hon_ledger_resolved,
            # The kind histogram of whatever file the containers came from: node
            # kinds on a v5 generation, group kinds on a v6 one.  One key, so a
            # reader does not have to know the shape to find it; meta.
            # container_source says which vocabulary the keys are drawn from.
            'container_source_kinds': dict(sorted(node_kinds.items(),
                                                  key=lambda kv: str(kv[0]))),
            'roster_size': len(roster),
        },
    })
    payload = {'meta': meta, 'offices': office_rows}
    assert_office_people_only(office_rows, non_person_ids)
    assert_once_per_group(office_rows, 'people', 'office')
    return payload


def read_lda_ruling(root, sidecar_generation, lda_root, lda_generation, org_cap, parts):
    """The corpus join, done ONCE for however many parts of this build need it.

    ``lda_employment.build_ledger`` streams the activity assertions (110 MB gz)
    and the industry edges (430 MB gz) and takes 45-145 seconds depending on the
    file cache.  Both the organisations part and the clients part are built from
    it, so ``--part both`` reads the corpus once rather than twice; that was worth
    hoisting out of the two builders the moment there were two of them.

    ``parts`` decides how much is done:

      * the sidecar (registrant tiers, the corroboration join) is bound only for
        the ORGANISATIONS part, which is the only one that prints a status line.
        ``--part clients`` therefore needs no sidecar at all and does not fail on
        a box that has the LDA corpus but not the sidecar generation.
      * lobbyist display names are resolved in ONE pass for the union both parts
        want: the first ``org_cap`` employees of each listed registrant, plus, for
        the clients part, every contract filer.
    """
    lda = bind_lda(lda_root, lda_generation)
    ledger = lda_employment.build_ledger(lda['paths']['lda_activity_assertions'],
                                         lda['paths']['lda_industry_edges'],
                                         lda['paths']['lda_organization_assertions'])
    employees = ledger.employees_by_registrant()

    def organisation_name(ident):
        return ledger.organisation_names.get(ident) or ident

    # WHICH --org-cap people a big registrant names is decided by this sort, and
    # it is deliberately the same ARBITRARY-BUT-DETERMINISTIC choice the producer
    # made before the ruling (it took the sidecar's file order; this takes id
    # order). Ordering by employer_as_of instead would name a different eight of
    # the Chamber of Commerce's 158, and which lobbyists this page names is an
    # open owner question, so the ruling does not get to change it as a side
    # effect. Deterministic is the requirement here; meaningful is a decision.
    registrant_groups = {}
    for registrant, members in employees.items():
        registrant_groups[registrant] = {'id': registrant, 'name': organisation_name(registrant),
                                         'people': sorted(members)}

    wanted = set()
    if PART_ORGANISATIONS in parts:
        for group in registrant_groups.values():
            if not is_singleton(group):
                wanted.update(group['people'][:org_cap])
    contract_filers = sorted(p for p in ledger.clients if ledger.is_contract(p))
    if PART_CLIENTS in parts:
        wanted.update(contract_filers)
    lobbyist_names = read_lobbyist_names(lda['path'], wanted)

    context = {'lda': lda, 'ledger': ledger, 'registrant_groups': registrant_groups,
               'contract_filers': contract_filers, 'lobbyist_names': lobbyist_names,
               'organisation_name': organisation_name,
               'sidecar': None, 'registrants': None, 'tiers': None, 'agree': None}
    if PART_ORGANISATIONS not in parts:
        return context

    sidecar = bind_sidecar(root, sidecar_generation)
    registrants = read_registrants(sidecar['paths']['registrant_side_records'])
    tiers = read_max_tier(sidecar['paths']['tiered_person_summary'], registrants['people'])
    # A filer the sidecar knows and the corpus join does not (or the reverse) is a
    # contradiction between two files that are supposed to describe one corpus,
    # not a row to skip: skipping would drop somebody's employer with nothing
    # saying so.
    missing_from_ledger = sorted(registrants['people'] - set(ledger.employer))[:3]
    if missing_from_ledger:
        raise InputContradiction(
            '%d filer(s) in the tiered sidecar have no filing in the LDA corpus join (e.g. %s); '
            'the sidecar and the corpus disagree about who filed'
            % (len(registrants['people'] - set(ledger.employer)), ', '.join(missing_from_ledger)))
    context.update({
        'sidecar': sidecar, 'registrants': registrants, 'tiers': tiers,
        # The cross-check, reported and never acted on: the sidecar can only order
        # a person's registrants by year, this ruling orders them by filing period.
        'agree': sum(1 for person, registrant in ledger.employer.items()
                     if registrants['sidecar_latest'].get(person) == registrant)})
    return context


def build_organisations(root, binding, generation, non_person_ids, sidecar_generation,
                        lda_generation, org_cap, max_bytes, context):
    """The organisations artifact: {meta, organisations}, REGISTRANTS ONLY.

    THE EMPLOYMENT RULING (2026-09-07) decides who is on which card:
    ``lda_employment.build_ledger`` answers, for every one of the 25,340 filers,
    who their employer is (most recent filing period) and whether that employer is
    contract or in-house.  This function turns the employer half into cards.

    THE CLIENT HALF IS NOT HERE ANY MORE, and that is the 2026-09-07 client
    normalization rather than a retreat from the ruling.  Client cards lived in
    this document for one build; the full set measured 21,012,744 bytes against
    the 5,000,000 pin, and the threshold that fit shipped 203 of the 41,203 client
    organisations the ruling reaches.  The bytes were the NAMES, repeated once per
    card, so the ties moved to a normalized sibling that holds all 41,203 clients
    and all 132,738 ties.  ``meta.clients_mode`` says ``separate_artifact`` and
    ``meta.clients_artifact`` names the file, so nothing about the move is silent.

    Assembly keeps its one remaining ladder rung -- the singleton mode: name-only
    lines, then count-only.  Without the client half this document measured
    3,520,189 bytes at cap 8, so that fallback has not been reached in practice.
    """
    lda, ledger = context['lda'], context['ledger']
    registrants, tiers, agree = context['registrants'], context['tiers'], context['agree']
    registrant_groups = context['registrant_groups']
    organisation_name = context['organisation_name']
    lobbyist_names = context['lobbyist_names']
    sidecar = context['sidecar']

    def person_name(person):
        return lobbyist_names.get(person) or NAME_UNAVAILABLE

    def employee_row(person):
        """One line in a registrant card.

        Neither ``employer_line`` nor ``engagement`` is repeated here: under the
        ruling every person on this card has THIS registrant as their employer,
        and contract-or-in-house is a property OF that registrant, so both are
        facts about the card and the card states each of them once.  What is left
        is what actually varies from person to person: the corroboration tier, the
        period their employer is on file as of, and -- for a contract filer -- how
        many client organisations they are tied to.
        """
        row = {'id': person, 'display_name': person_name(person),
               'lda_status_label': status_label(tiers.get(person)),
               'registrant_tier': str(registrants['tier'].get(person) or '').upper() or None,
               'employer_as_of': lda_employment.period_label(ledger.employer_as_of[person])}
        if ledger.is_contract(person):
            row['client_count'] = len(ledger.clients.get(person) or {})
        formers = ledger.formers.get(person) or ()
        if formers:
            row['former_employers'] = [{'id': ident, 'name': organisation_name(ident),
                                        'last_filed': lda_employment.period_label(key)}
                                       for ident, key in formers]
        return row

    def registrant_row(group):
        listed = group['people'][:org_cap]
        people = sorted((employee_row(person) for person in listed),
                        key=lambda p: p['display_name'])
        return {'id': group['id'], 'kind': KIND_REGISTRANT, 'name': group['name'],
                'engagement': ledger.registrant_engagement.get(group['id']),
                'employer_line': card_employer_line(group['name']),
                'lobbyist_count': len(group['people']),
                'lobbyists': people,
                'more': max(0, len(group['people']) - len(listed))}

    full_rows = [registrant_row(group) for _ident, group in sorted(registrant_groups.items())
                 if not is_singleton(group)]
    # A name-only line names no person, but it can still say what SORT of place
    # the registrant is -- a lobbying firm or an in-house shop -- because that is
    # a fact about the organisation and not about its one unnamed filer.
    singleton_rows = [dict(singleton_line(group), kind=KIND_REGISTRANT,
                           engagement=ledger.registrant_engagement.get(group['id']))
                      for _ident, group in sorted(registrant_groups.items())
                      if is_singleton(group)]
    # A tier this producer has no word for still refuses, on EVERY filer in the
    # corpus and not only the ones that happen to be printed: the status
    # vocabulary is a gate on the whole corpus.
    for person in sorted(registrants['people']):
        status_label(tiers.get(person))

    inputs = dict(binding['bound'])
    inputs.update(sidecar['bound'])
    inputs.update(lda['bound'])
    reads = ['registrant_side_records', 'tiered_person_summary', 'lda_lobbyist_assertions',
             'lda_organization_assertions', 'lda_activity_assertions', 'lda_industry_edges']
    clients_available = len(ledger.filers_by_client())
    client_ties = ledger.census['contract_filer_client_pairs']

    def assemble(singleton_mode):
        organisation_rows = list(full_rows)
        if singleton_mode == SINGLETON_MODE_LINES:
            organisation_rows += singleton_rows
        # Biggest first, then by name. One kind of card now, so nothing sorts
        # ahead of anything else on kind.
        organisation_rows.sort(key=lambda o: (-(o.get('lobbyist_count') or 0),
                                              o['name'], o['id']))
        employees_listed = {p['id'] for org in organisation_rows
                            for p in (org.get('lobbyists') or ())}
        meta = base_meta(root, binding, generation, ORGANISATIONS_SCHEMA_ID,
                         PART_ORGANISATIONS, max_bytes)
        meta.update({
            'sidecar_generation': sidecar_generation,
            'lda_generation': lda_generation,
            'inputs': portable_inputs(dict(inputs), root),
            'reads': sorted(set(reads)),
            'org_cap': org_cap,
            # Where the client ties are, and that they are ALL there.  A reader of
            # this document must not have to work out from a missing key that the
            # client half exists; lda_employment.CLIENTS_ELSEWHERE is the value
            # its own loader keys on to know it should read the sibling.
            'clients_mode': lda_employment.CLIENTS_ELSEWHERE,
            'clients_artifact': CLIENTS_FILENAME,
            'client_rule': lda_employment.CLIENT_RULE,
            'clients_note': clients_elsewhere_note(clients_available, client_ties,
                                                   CLIENTS_FILENAME),
            'singleton_mode': singleton_mode,
            'singleton_registrants': len(singleton_rows),
            'singleton_rule': singleton_rule_note(org_cap),
            'employment_ruling': lda_employment.RULING,
            'employment_ruling_date': lda_employment.RULING_DATE,
            'employment_rule': lda_employment.EMPLOYER_RULE,
            'contract_rule': lda_employment.CONTRACT_RULE,
            'employment_note': employment_rule_note(),
            'organisation_kinds': [KIND_REGISTRANT],
            'lda_status_note': LDA_STATUS_NOTE,
            'lda_status_labels': dict(sorted(LDA_STATUS_LABELS.items())),
            'employer_corroboration': dict(sorted(EMPLOYER_CORROBORATION.items())),
            'lda_privacy_class': lda.get('privacy_class'),
            'counts': {
                'organisations': len(organisation_rows),
                'organisations_listed_in_full': len(full_rows),
                'singleton_registrants': len(singleton_rows),
                'registrants_total': len(registrant_groups),
                'registrants_with_no_employee': (ledger.census['registrants']
                                                 - len(registrant_groups)),
                # The client half's own numbers, carried here as a cross-check on
                # the sibling rather than as content: a reader who has both files
                # can tell in one comparison whether they came from one build.
                'client_organisations_in_clients_artifact': clients_available,
                'client_ties_in_clients_artifact': client_ties,
                'people_listed': len(employees_listed),
                'employees_listed': len(employees_listed),
                'employees_not_named': sum(org.get('more') or 0 for org in organisation_rows
                                           if org['kind'] == KIND_REGISTRANT),
                'lobbyist_rows_listed': sum(len(org.get('lobbyists') or ())
                                            for org in organisation_rows),
                'lobbyists_total': len(registrants['people']),
                'lobbyists_with_no_registrant_on_file': registrants['unplaced'],
                'contract_filers': ledger.census['contract'],
                'in_house_filers': ledger.census['in_house'],
                'contract_registrants': ledger.census['registrants_contract'],
                'in_house_registrants': ledger.census['registrants_in_house'],
                'clients_per_contract_filer': ledger.census['clients_per_contract_filer'],
                'contract_filer_client_pairs': ledger.census['contract_filer_client_pairs'],
                'filers_with_a_former_employer': ledger.census['filers_with_a_former_employer'],
                'former_employer_rows': ledger.census['former_employer_rows'],
                'concurrent_registrants_in_former_employers':
                    ledger.census['concurrent_registrants_in_former_employers'],
                'employer_tie_rules': ledger.census['tie_rules'],
                'contract_filers_whose_employer_has_one_client':
                    ledger.census['contract_filers_whose_employer_has_one_client'],
                'employer_agrees_with_sidecar_year_order': agree,
                'employer_differs_from_sidecar_year_order': len(ledger.employer) - agree,
                'filings_read': ledger.census['filings'],
                'named_on_activity_edges': ledger.census['named_on_activity'],
            },
        })
        payload = {'meta': meta, 'organisations': organisation_rows}
        assert_lobbyists_only(organisation_rows, non_person_ids)
        assert_once_per_group(organisation_rows, 'lobbyists', 'organisation')
        assert_one_employer_per_person(organisation_rows)
        assert_no_client_cards(organisation_rows)
        return payload

    for singleton_mode in (SINGLETON_MODE_LINES, SINGLETON_MODE_COUNT):
        payload = assemble(singleton_mode)
        payload_bytes = serialize(payload)
        if len(payload_bytes) <= max_bytes:
            return payload, payload_bytes
        if not singleton_rows:
            break
    # Both modes refused. Hand the last one back so build() can name what is over
    # by how much.
    return payload, payload_bytes


def build_clients(root, binding, generation, lda_generation, max_bytes, context):
    """The clients artifact: the client half of the ruling, NORMALIZED.

    D-023 ties a contract filer to EVERY client organisation on filings that name
    them.  Written as cards -- a name and a roster per client -- that reaches
    41,203 clients and 132,738 ties and measures 21,012,744 bytes, four times the
    5,000,000 pin, so the previous build thresholded it to the 203 clients with 20
    or more representing filers.  Half a percent of the ruling.

    The bytes were never the ties; they were the NAMES.  A filer's name was
    written once per client they file for and a client's name once per card, so
    the document paid for the same 52,000 strings 132,738 times over.  Written
    normalized -- each name once, each tie a pair of positions -- the SAME 41,203
    clients and the SAME 132,738 ties fit in about 4 MB with a fifth of the pin to
    spare, and nothing is dropped.  MEASURED against the real corpus 2026-09-07,
    which is where every number in this docstring comes from:

      cards, all clients                             21,012,744 B   4.2x the pin
      cards, thresholded to fit (203 clients)         4,108,656 B   0.5% shipped
      objects {id,name} + per-client index arrays     5,249,260 B   OVER by 249,260
      THIS, 1-2 char keys + parallel arrays           ~3,950,000 B   all 41,203

    So the readable object form does NOT fit and the short-key form does: the key
    strings are why.  ``{"id":...,"name":...}`` per client is 41,203 copies of two
    key names and their punctuation; parallel arrays under ``c`` and ``cn`` are two
    copies.  ``meta.key_legend`` (lda_employment.CLIENTS_KEY_LEGEND) is the price
    of that, paid once, and it ships IN the document so the shape is self-describing.

    TIE DIRECTION, measured rather than assumed.  One array per CLIENT of filer
    indices is 745,857 B; one array per FILER of client indices is 773,700 B; a
    flat index-pair array is 1,415,108 B.  Per-client is both the smallest and the
    one the page actually asks for ("who represents this organisation"), so it is
    what ships.

    ORDER.  ``f`` is ordered by (display name, id), so a client's tie row -- which
    is ordered by filer index -- is already in name order and the page sorts
    nothing.  ``c`` and ``e`` are ordered by id, which no name resolution can move.

    NO CAP, NO LADDER, NO THRESHOLD.  This artifact either holds every client and
    every tie or it refuses to be written, because a partial one would be the thing
    the normalization exists to stop.  If a future corpus outgrows the pin the next
    step is a second file split by client-id range, not a threshold.
    """
    lda, ledger = context['lda'], context['ledger']
    organisation_name = context['organisation_name']
    lobbyist_names = context['lobbyist_names']
    client_filers = ledger.filers_by_client()

    clients = sorted(client_filers)
    filers = sorted(context['contract_filers'],
                    key=lambda p: (lobbyist_names.get(p) or NAME_UNAVAILABLE, p))
    findex = {person: index for index, person in enumerate(filers)}
    employers = sorted({ledger.employer[person] for person in filers})
    eindex = {registrant: index for index, registrant in enumerate(employers)}
    period_keys = sorted({key for members in client_filers.values() for _p, key in members})
    kindex = {key: index for index, key in enumerate(period_keys)}

    ties, tie_periods, tie_rows = [], [], 0
    for client in clients:
        members = client_filers[client]
        unknown = [person for person, _key in members if person not in findex]
        if unknown:
            # filers_by_client() yields contract filers only, so this is
            # unreachable through the ledger; it is asserted anyway because a
            # dangling index is the one failure the page cannot detect.
            raise InputContradiction(
                'client %s is tied to %d filer(s) that are not contract filers (e.g. %s)'
                % (client, len(unknown), unknown[0]))
        ordered = sorted(members, key=lambda pk: findex[pk[0]])
        ties.append([findex[person] for person, _key in ordered])
        tie_periods.append([kindex[key] for _person, key in ordered])
        tie_rows += len(ordered)

    payload = {
        'meta': base_meta(root, binding, generation, CLIENTS_SCHEMA_ID, PART_CLIENTS, max_bytes),
        'c': clients,
        'cn': [organisation_name(client) for client in clients],
        'e': employers,
        'en': [organisation_name(registrant) for registrant in employers],
        'f': filers,
        'fn': [lobbyist_names.get(person) or NAME_UNAVAILABLE for person in filers],
        'fe': [eindex[ledger.employer[person]] for person in filers],
        't': ties,
        'tp': tie_periods,
    }
    payload['meta'].update({
        'lda_generation': lda_generation,
        'inputs': portable_inputs(dict(lda['bound']), root),
        'reads': ['lda_activity_assertions', 'lda_industry_edges', 'lda_lobbyist_assertions',
                  'lda_organization_assertions'],
        'key_legend': dict(sorted(lda_employment.CLIENTS_KEY_LEGEND.items())),
        'period_labels': [lda_employment.period_label(key) for key in period_keys],
        'client_kind': KIND_CLIENT,
        'organisation_kinds': [KIND_CLIENT],
        # The ruling, verbatim, and the three definitions it turned into, so this
        # artifact states what it is a rendering OF and not only what it holds.
        'employment_ruling': lda_employment.RULING,
        'employment_ruling_date': lda_employment.RULING_DATE,
        'employment_rule': lda_employment.EMPLOYER_RULE,
        'contract_rule': lda_employment.CONTRACT_RULE,
        'client_rule': lda_employment.CLIENT_RULE,
        'no_tenure_note': lda_employment.NO_TENURE_NOTE,
        'clients_rule': clients_rule_note(len(clients), len(filers), tie_rows),
        'lda_privacy_class': lda.get('privacy_class'),
        'counts': {
            'client_organisations': len(clients),
            'contract_filers': len(filers),
            'employer_registrants': len(employers),
            'filer_client_ties': tie_rows,
            'name_rows': len(clients) + len(filers) + len(employers),
            'clients_with_one_filer': sum(1 for row in ties if len(row) == 1),
            'largest_client_roster': max((len(row) for row in ties), default=0),
            'period_labels': len(period_keys),
            # The ledger's own numbers beside this artifact's, so conservation is
            # a comparison inside the document rather than a claim about it.
            'client_organisations_in_ledger': len(client_filers),
            'contract_filer_client_pairs_in_ledger':
                ledger.census['contract_filer_client_pairs'],
            'contract_filers_in_ledger': ledger.census['contract'],
            'in_house_filers_in_ledger': ledger.census['in_house'],
            'filers_in_ledger': ledger.census['filers'],
            'clients_per_contract_filer': ledger.census['clients_per_contract_filer'],
            'client_organisations_dropped': 0,
            'filer_client_ties_dropped': 0,
        },
    })
    # Conservation, checked here and not only in a test: an artifact that quietly
    # holds fewer ties than the ruling reaches is the failure this file exists to
    # end, so the producer refuses rather than ships a short one.
    if tie_rows != ledger.census['contract_filer_client_pairs']:
        raise InputContradiction(
            'the ruling reaches %d filer-client ties and this artifact holds %d'
            % (ledger.census['contract_filer_client_pairs'], tie_rows))
    if len(clients) != len(client_filers):
        raise InputContradiction(
            'the ruling reaches %d client organisations and this artifact holds %d'
            % (len(client_filers), len(clients)))
    if len(filers) != ledger.census['contract']:
        raise InputContradiction(
            'the ruling counts %d contract filers and this artifact holds %d'
            % (ledger.census['contract'], len(filers)))
    assert_clients_normalized(payload, ledger.engagement)
    return payload, serialize(payload)


def card_employer_line(name):
    """The employer line a REGISTRANT card carries, once, for all of its people.

    No corroboration tier gates this string, and that is deliberate.  Before the
    employment ruling the tier had work to do: a person was listed under every
    registrant that had ever named them, so "which of these is the employer" was
    a question the tier helped answer.  Under the ruling the card IS the person's
    employer -- that is what put them on it -- so the name is a fact about the
    card, not a claim about a person, and the only thing that could withhold it
    is not having a name at all.

    The per-person half of the old line has NOT been thrown away: the corroboration
    tier travels on each person's row as ``registrant_tier`` (R0/R1/R2, two
    characters) and ``meta.employer_corroboration`` carries the owner's ruled
    phrase for each of the three, once for the whole document.  Same words on the
    page, per person, at a twentieth of the bytes -- which is what buys the client
    half of this directory any room at all.
    """
    name = str(name or '').strip()
    return EMPLOYER_ON_FILE % name if name else EMPLOYER_NAME_UNKNOWN


def build(root, generation, out_offices=None, out_organisations=None, out_clients=None,
          part=PART_BOTH,
          max_bytes=MAX_BYTES, org_cap=ORG_CAP, office_cap=OFFICE_CURRENT_SAFETY_CAP,
          sidecar_generation=SIDECAR_GENERATION, lda_root=LDA_DATA_ROOT,
          lda_generation=LDA_GENERATION, hon_ledger_generation=HON_LEDGER_GENERATION,
          use_glassbox=True):
    started = time.perf_counter()
    if part not in PARTS:
        raise ValueError('part must be one of %s, not %r' % (', '.join(PARTS), part))
    wanted = BOTH_PARTS if part == PART_BOTH else (part,)
    # main() always supplies every path; an API caller may not, and a missing one
    # would otherwise surface as a TypeError deep inside the atomic write, after
    # the generation had already been read.
    out_paths = {PART_OFFICES: out_offices, PART_ORGANISATIONS: out_organisations,
                 PART_CLIENTS: out_clients}
    for name in wanted:
        if not out_paths[name]:
            raise ValueError('part %r was requested with no output path' % name)
    network.glassbox(use_glassbox, 'producer', 'running',
                     'Staffer Groups section: binding %s (part=%s)' % (generation, part),
                     {'generation': generation, 'part': part, 'max_bytes': max_bytes,
                      'org_cap': org_cap, 'office_cap': office_cap,
                      'out_offices': out_offices, 'out_organisations': out_organisations,
                      'out_clients': out_clients})
    try:
        binding = network.bind_inputs(root, generation)
        groups_path, groups_bound = bind_generation_groups(binding)
        roster = network.load_roster(root)
        offices, non_person_ids, node_kinds, container_source = read_office_containers(
            binding, groups_path)

        built = {}
        if PART_OFFICES in wanted:
            payload = build_offices(root, binding, generation, roster, offices, non_person_ids,
                                    node_kinds, container_source, groups_bound,
                                    hon_ledger_generation, office_cap, max_bytes)
            built[PART_OFFICES] = (payload, serialize(payload), out_offices)
        # ONE corpus join for however many of the two LDA parts were asked for:
        # build_ledger streams 540 MB, so doing it once rather than per part is
        # most of the cost of `--part both`.
        if PART_ORGANISATIONS in wanted or PART_CLIENTS in wanted:
            context = read_lda_ruling(root, sidecar_generation, lda_root, lda_generation,
                                      org_cap, wanted)
            if PART_ORGANISATIONS in wanted:
                payload, payload_bytes = build_organisations(
                    root, binding, generation, non_person_ids, sidecar_generation,
                    lda_generation, org_cap, max_bytes, context)
                built[PART_ORGANISATIONS] = (payload, payload_bytes, out_organisations)
            if PART_CLIENTS in wanted:
                payload, payload_bytes = build_clients(root, binding, generation, lda_generation,
                                                       max_bytes, context)
                built[PART_CLIENTS] = (payload, payload_bytes, out_clients)

        # ALL-OR-NOTHING on the cap when more than one part was asked for: a
        # directory built from a fresh part and a stale part is worse than one that
        # did not rebuild, and there is no way for a reader to tell them apart.
        over = [(name, len(blob)) for name, (_p, blob, _o) in sorted(built.items())
                if len(blob) > max_bytes]
        if over:
            detail = ['REFUSED: nothing written. org cap in force: %d, office safety cap in '
                      'force: %d' % (org_cap, office_cap)]
            for name, size in over:
                detail.append('%s: %d bytes exceeds --max-bytes %d' % (name, size, max_bytes))
            for name, (payload, blob, _out) in sorted(built.items()):
                if name == PART_CLIENTS:
                    # The clients artifact has no groups to name, so what a refusal
                    # owes a reader is which table is carrying the bytes.
                    detail.append('%s: %d bytes; %d clients, %d filers, %d ties (no threshold is '
                                  'applied here by design -- see build_clients)'
                                  % (name, len(blob), len(payload['c']), len(payload['f']),
                                     payload['meta']['counts']['filer_client_ties']))
                    continue
                member_key = 'people' if name == PART_OFFICES else 'lobbyists'
                groups = payload[name]
                detail.append('%s: %d bytes, %d groups; largest %s'
                              % (name, len(blob), len(groups),
                                 ', '.join('%s=%d' % b
                                           for b in offenders_by_size(groups, member_key))))
            network.glassbox(use_glassbox, 'producer', 'blocked',
                             'Staffer Groups section refused: %s over the %d cap'
                             % (', '.join('%s=%d' % o for o in over), max_bytes),
                             {'max_bytes': max_bytes, 'org_cap': org_cap, 'office_cap': office_cap,
                              'parts': sorted(built), 'over': dict(over)},
                             detail='\n'.join(detail),
                             duration_ms=(time.perf_counter() - started) * 1000.0)
            return {'ok': False, 'reason': 'size', 'max_bytes': max_bytes, 'lines': detail,
                    'parts': {name: {'bytes': len(blob)}
                              for name, (_p, blob, _o) in built.items()}}

        attempts = write_all_atomic([(out_path, blob)
                                     for _n, (_p, blob, out_path) in sorted(built.items())])
        results = {}
        for name, (payload, blob, out_path) in sorted(built.items()):
            results[name] = {'bytes': len(blob), 'path': os.path.abspath(out_path),
                             'payload': payload,
                             'rename_attempts': attempts.get(os.path.abspath(out_path)),
                             'counts': payload['meta']['counts']}
        elapsed = time.perf_counter() - started
        network.glassbox(use_glassbox, 'producer', 'completed',
                         'Staffer Groups written: %s'
                         % ', '.join('%s=%d bytes' % (n, r['bytes'])
                                     for n, r in sorted(results.items())),
                         {'generation': generation, 'part': part,
                          'container_source': container_source,
                          'org_cap': org_cap, 'office_cap': office_cap,
                          'seconds': round(elapsed, 3),
                          'bytes': {n: r['bytes'] for n, r in results.items()},
                          'counts': {n: r['counts'] for n, r in results.items()}},
                         duration_ms=elapsed * 1000.0)
        return {'ok': True, 'parts': results, 'seconds': elapsed,
                'container_source': container_source}
    except Exception as exc:                                # noqa: BLE001 - reported, re-raised
        network.glassbox(use_glassbox, 'producer', 'failed',
                         'Staffer Groups section failed: %s' % type(exc).__name__,
                         {'generation': generation, 'part': part, 'error': str(exc)[:800]},
                         duration_ms=(time.perf_counter() - started) * 1000.0)
        raise


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Build the Melange Groups artifacts (offices and organisations, one file '
                    'each) from a verified staffer social-network generation and the tiered '
                    'corroboration sidecar.')
    parser.add_argument('--part', choices=PARTS, default=PART_BOTH,
                        help='which artifact to write (default %s)' % PART_BOTH)
    parser.add_argument('--generation', default=GENERATION,
                        help='social-network generation under audits/staffer_network')
    parser.add_argument('--sidecar-generation', default=SIDECAR_GENERATION,
                        help='tiered corroboration sidecar generation under audits/staffer_network')
    parser.add_argument('--lda-generation', default=LDA_GENERATION,
                        help='LDA source-native corpus generation directory name')
    parser.add_argument('--lda-root', default=LDA_DATA_ROOT,
                        help='parent of the LDA source-native generation directories')
    parser.add_argument('--hon-ledger-generation', default=HON_LEDGER_GENERATION,
                        help='House personnel ledger generation under audits/staffer_network, '
                             'used to name HON:<bioguide> offices off the current roster')
    parser.add_argument('--root', default=DEFAULT_ROOT, help='project root')
    parser.add_argument('--out-dir', default=None,
                        help='directory for all three artifacts (default <root>/data/engine)')
    parser.add_argument('--out-offices', default=None,
                        help='explicit path for %s' % OFFICES_FILENAME)
    parser.add_argument('--out-organisations', default=None,
                        help='explicit path for %s' % ORGANISATIONS_FILENAME)
    parser.add_argument('--out-clients', default=None,
                        help='explicit path for %s' % CLIENTS_FILENAME)
    parser.add_argument('--org-cap', type=int, default=ORG_CAP,
                        help='most employees listed under any one registrant')
    parser.add_argument('--office-cap', type=int, default=OFFICE_CURRENT_SAFETY_CAP,
                        help='safety cap on current people listed under any one office '
                             '(a real office is not expected to reach this)')
    parser.add_argument('--max-bytes', type=int, default=MAX_BYTES,
                        help='refuse to write an artifact larger than this; applied to EACH '
                             'artifact separately')
    parser.add_argument('--no-glassbox', action='store_true', help='suppress glassbox events')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    root = os.path.abspath(args.root)
    out_dir = args.out_dir or os.path.join(root, 'data', 'engine')
    out_offices = args.out_offices or os.path.join(out_dir, OFFICES_FILENAME)
    out_organisations = args.out_organisations or os.path.join(out_dir, ORGANISATIONS_FILENAME)
    out_clients = args.out_clients or os.path.join(out_dir, CLIENTS_FILENAME)
    if args.org_cap < 1:
        print('REFUSED: --org-cap must be at least 1')
        return 2
    if args.office_cap < 1:
        print('REFUSED: --office-cap must be at least 1')
        return 2
    chosen = [os.path.abspath(p) for p in (out_offices, out_organisations, out_clients)]
    if len(set(chosen)) != len(chosen):
        print('REFUSED: the three artifacts cannot share a path (%s)' % ', '.join(sorted(chosen)))
        return 2
    generation_dir = os.path.join(root, 'audits', 'staffer_network', args.generation)
    if not os.path.isdir(generation_dir):
        # Same clean-skip contract as the network section: a fresh or data-isolated
        # tree simply has no generation, and the chain must walk past that.  A
        # generation that is PRESENT but unverifiable still exits 2 below.
        print('SKIPPED: generation absent (%s)' % generation_dir)
        return 0
    try:
        result = build(root, args.generation, out_offices=out_offices,
                       out_organisations=out_organisations, out_clients=out_clients,
                       part=args.part,
                       max_bytes=args.max_bytes, org_cap=args.org_cap,
                       office_cap=args.office_cap,
                       sidecar_generation=args.sidecar_generation,
                       lda_root=args.lda_root, lda_generation=args.lda_generation,
                       hon_ledger_generation=args.hon_ledger_generation,
                       use_glassbox=not args.no_glassbox)
    except (InputContradiction, lda_employment.EmploymentContradiction) as exc:
        print('REFUSED: %s' % ' '.join(str(exc).split())[:800])
        return 2
    if not result['ok']:
        for line in result['lines']:
            print(line)
        return 3
    print('container_source=%s' % result['container_source'])
    for name, part in sorted(result['parts'].items()):
        counts = part['counts']
        print('wrote %s' % part['path'])
        if name == PART_OFFICES:
            print('  offices=%d people_listed=%d bytes=%d office_people_total=%d '
                  'offices_unnamed=%d offices_hon_ledger_resolved=%d'
                  % (counts['offices'], counts['people_listed'], part['bytes'],
                     counts['office_people_total'], counts['offices_unnamed'],
                     counts['offices_hon_ledger_resolved']))
        elif name == PART_CLIENTS:
            print('  clients=%d contract_filers=%d employer_registrants=%d ties=%d bytes=%d'
                  % (counts['client_organisations'], counts['contract_filers'],
                     counts['employer_registrants'], counts['filer_client_ties'], part['bytes']))
            print('  CONSERVED: clients %d/%d, ties %d/%d, contract filers %d/%d; dropped %d '
                  'clients and %d ties'
                  % (counts['client_organisations'], counts['client_organisations_in_ledger'],
                     counts['filer_client_ties'],
                     counts['contract_filer_client_pairs_in_ledger'],
                     counts['contract_filers'], counts['contract_filers_in_ledger'],
                     counts['client_organisations_dropped'], counts['filer_client_ties_dropped']))
            print('  names written once: %d rows (clients+filers+employers); largest client '
                  'roster=%d; clients with one filer=%d'
                  % (counts['name_rows'], counts['largest_client_roster'],
                     counts['clients_with_one_filer']))
        else:
            meta = part['payload']['meta']
            print('  organisations=%d listed_in_full=%d singleton_registrants=%d (%s) '
                  'lobbyist_rows=%d people_listed=%d bytes=%d'
                  % (counts['organisations'], counts['organisations_listed_in_full'],
                     counts['singleton_registrants'], meta['singleton_mode'],
                     counts['lobbyist_rows_listed'], counts['people_listed'], part['bytes']))
            print('  lobbyists_total=%d lobbyists_with_no_registrant_on_file=%d'
                  % (counts['lobbyists_total'],
                     counts['lobbyists_with_no_registrant_on_file']))
            print('  EMPLOYMENT RULING: contract=%d in_house=%d; contract registrants=%d '
                  'in-house registrants=%d'
                  % (counts['contract_filers'], counts['in_house_filers'],
                     counts['contract_registrants'], counts['in_house_registrants']))
            distribution = counts['clients_per_contract_filer']
            print('  clients per contract filer: median=%d p90=%d max=%d mean=%.1f (n=%d, '
                  'pairs=%d)'
                  % (distribution['median'], distribution['p90'], distribution['max'],
                     distribution['mean'], distribution['n'],
                     counts['contract_filer_client_pairs']))
            print('  clients_mode=%s -> %s (%d client organisations, %d ties, all of them)'
                  % (meta['clients_mode'], meta['clients_artifact'],
                     counts['client_organisations_in_clients_artifact'],
                     counts['client_ties_in_clients_artifact']))
            print('  employer vs sidecar year order: agree=%d differ=%d; tie rules=%s'
                  % (counts['employer_agrees_with_sidecar_year_order'],
                     counts['employer_differs_from_sidecar_year_order'],
                     counts['employer_tie_rules']))
    print('seconds=%.1f' % result['seconds'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
