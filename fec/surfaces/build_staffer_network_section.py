# -*- coding: utf-8 -*-
"""Producer for the Melange dashboard's "Staffer Network" section.

Read-only and receipt-gated. It reads three artifacts of one verified staffer
social-network generation (node scores, trajectories, office moves), binds them
by sha256 to the generation receipt AND to the independent verification receipt,
and writes one deterministic JSON section file. It writes nothing else and it
never touches the generation directory.

Language law enforced by this producer (owner rulings, 2026-09-03/04):

  * The staffer network is its own social layer BELOW members. Members are
    attachment points, never nodes, and this file emits no member-member tie,
    edge or weight of any kind. Where two members appear together it is only as
    SHARED STAFF: a count of people attached to both offices. That count is a
    property of the people, not a relationship between the two members.
  * A person-to-person tie exists only with temporal overlap. This producer
    computes no ties; it propagates the generation's own count of pairs
    REJECTED for zero overlap so the section can show it.
  * Everything here is exploratory and REVIEW_REQUIRED. No influence or
    persuasion language is emitted. The one place the word can appear is
    ``meta.statement``, which is a verbatim copy of the generation receipt's own
    disclaimer ("... no influence claim") and is never edited.
  * No name match becomes an identity assertion. Display names are joined only
    by exact person ID from the generation's receipt-bound payroll spine, whose
    byte count and SHA-256 are checked before use. Verified node IDs and the
    payroll titles on their own stints remain available for provenance.

Verification approach mirrors surfaces/staffer_graph_snapshot.py. That module
exposes its binding only through a stateful StaticGraph loader that also parses
the 1.5 GB edge file, so its helper is not importable for this job; the rule it
applies is reproduced here explicitly (verification status PASS with no
failures, receipt bound through twin_byte_identity, artifacts bound through the
receipt's own files list, promotion tripwire) and tightened with a second,
independent cross-check against the verification receipt's primary_file_integrity.

Filename says Moneyball's repo conventions; the visible section name is Melange.

Usage:
    python fec\\surfaces\\build_staffer_network_section.py
    python fec\\surfaces\\build_staffer_network_section.py --generation <dir> --out <path>
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEC = os.path.dirname(_HERE)
if _FEC not in sys.path:
    sys.path.insert(0, _FEC)
_ROOT_FOR_IMPORT = os.path.dirname(_FEC)
if _ROOT_FOR_IMPORT not in sys.path:
    sys.path.insert(0, _ROOT_FOR_IMPORT)

from fec.staffer_network import name_standard  # noqa: E402  (after the path bootstrap)
from fec.staffer_network.closeness_math import ExposureIndex, VERSION as CLOSENESS_VERSION, MEMBER_BETA
from fec.staffer_network.member_context import load_member_context
from fec.staffer_network.relationship_uncertainty import specification as uncertainty_specification
from fec.surfaces.staffer_network_scope import intern_title

# paths.py's own root derivation, duplicated deliberately: the shared kernel
# (moneyball_core -> paths) freezes ROOT at import time, so the root has to be
# known and, when --root points elsewhere, published to the environment BEFORE
# the kernel is imported. Importing paths here would freeze the wrong tree.
DEFAULT_ROOT = os.path.abspath(
    os.environ.get('MONEYBALL_ROOT', os.path.join(_FEC, os.pardir))
)

PRODUCER_VERSION = 'staffer-network-section-v1'
SCHEMA_ID = 'staffer-network-section-v1'
GENERATION = 'staffer_social_network_20260908_v8_math_primary'
VERIFICATION_SUFFIX = '_verification'
PRIMARY_SUFFIX = '_primary'

RECEIPT_NAME = 'staffer_social_network_receipt.json'
MANIFEST_NAME = 'staffer_social_network_manifest.json'
VERIFICATION_NAME = 'staffer_social_network_verification_receipt.json'
EDGES_NAME = 'staffer_social_edges.ndjson.gz'

# role -> filename, exactly as the generation receipt names them. The edge file
# is a fourth emitted artifact of the same generation; this producer consumes
# none of it and, per the build brief, does not read 1.5 GB to hash it. Its
# declared hash is carried through unverified and labelled as such, and
# --hash-edges turns the check on for anyone who wants it bound here too.
CONSUMED_FILES = (
    ('staffer_social_node_scores', 'staffer_social_node_scores.ndjson.gz'),
    ('staffer_trajectories', 'staffer_trajectories.ndjson.gz'),
    ('staffer_office_moves', 'staffer_office_moves.ndjson.gz'),
)

# ---------------------------------------------------------------------------
# GENERATION-ADAPTIVE FIELD NAMES.  The staffer social network was reworked to a
# persons-only operator (D-022): the v6 generation renames two person-row fields
# and one receipt block.  The pinned generation above is v6 as of 2026-09-07, but
# this producer still reads BOTH shapes -- the v5 shape is exercised by this
# module's own test fixture and by any future rollback -- so nothing here may
# assume the pin never moves back.  First name present in each tuple wins, so
# the v6 name is listed first and a v5 file falls through to the old one.
#
#   office_diversity_exploratory   (v6)  Simpson diversity over the OFFICES that
#                                        produced a person's ties.
#   bridging_diversity_exploratory (v5)  Simpson over node KINDS -- with four
#                                        kinds in the operator it only ever
#                                        measured how many kinds a node touched.
#
# They are NOT the same quantity, so the emitted payload field is called
# office_diversity in both cases and meta.office_diversity_source names the
# field it was actually read from.  A v5-sourced column is the old kind-diversity
# number under the new name; the score note says so, in the payload, every build.
DIVERSITY_FIELDS = ('office_diversity_exploratory', 'bridging_diversity_exploratory')
DIVERSITY_SOURCE_NOTES = {
    'office_diversity_exploratory': (
        'office_diversity is the generation\'s own office_diversity_exploratory: normalised '
        'Simpson diversity of the OFFICES that produced this person\'s ties.'),
    'bridging_diversity_exploratory': (
        'office_diversity was read from this v5 generation\'s bridging_diversity_exploratory, '
        'which is Simpson diversity over node KINDS, not over offices. It is carried under the '
        'new name so one field serves both generations; on this generation it does NOT measure '
        'bridging between offices and must not be read as if it did.'),
}
# Same idea for the per-person revolving column.  v6 counts the registrant
# organisations a person's grouping layer attaches them to; v5 counted degree in
# a heterogeneous operator that had organisations in it.
REVOLVING_FIELDS = ('registrant_organization_group_count', 'revolving_degree')
# What office_revolving_edge_count says on a generation that cannot supply it.
OFFICE_REVOLVING_UNAVAILABLE = (
    'office_revolving_edge_count is null on this generation. It is summed from container rows in '
    'the node scores, and a persons-only generation (D-022) has none: the office-to-organization '
    'rows live in staffer_social_revolving_layer.ndjson.gz, which this producer does not read. '
    'Null is the honest value; zero would read as "no such edges".')

TOP_STAFF = 15
TOP_SHARED_WITH = 12
TOP_TITLES = 8
TOP_MOVE_CONTAINERS = 6
TOP_BRIDGE_PEOPLE = 25
MAX_COMPONENT_IDS = 5
PAGERANK_ALPHA = 0.85
SIGNIFICANT_DIGITS = 6

LANGUAGE_NOTE = ('Shared staff means people attached to both offices. It is not a tie '
                 'between the two members and carries no weight.')
TENURE_NOTE = ('tenure_days_with_member counts the union of half-open intervals from core_start '
               'to core_end_exclusive across that person\'s stints in this member\'s offices. '
               'Overlapping assignments count each day once. Senate-lane stints '
               'are bounded by reporting periods, so those days are period edges, not observed '
               'service days. Null means no stint carried two parseable dates.')
SHARED_STAFF_NOTE = ('shared_staff counts this member\'s people who are attached to at least one '
                     'other member office anywhere in the payroll record, current or historic. '
                     'shared_with is restricted to the two ends both being roster members.')

# Owner ruling 2026-09-07 (item 1, RELABEL): co_tenure_degree alone read as a
# colleague count with no way to tell WHERE the overlap happened, so every
# top_staff row now also names the specific payroll container -- not
# necessarily this member's own office -- and how many people were ever attached
# to it. Definition kept to one sentence because it is reused verbatim as the
# column's tooltip.
#
# AMENDED by the owner ruling of 2026-09-07 (office label): "It should be for
# most recent office or whatever committee they belong too, payroll takes
# precedent, then politico". The office named was the one the person spent the
# MOST DAYS in; it is now the MOST RECENT one. See MOST_RECENT_OFFICE_RULE.
CO_TENURE_OFFICE_NOTE = (
    'People whose payroll intervals overlapped this person\'s under the same office header; '
    'the office named is this person\'s most recent office or committee.')

# The selection rule, stated where the code that applies it can be read against
# it. It is NOT a second implementation of "most recent role": it is the rule the
# staffer live sidecar already applies in
# fec/surfaces/staffer_connection_facts.py:latest_roles(), which dates a payroll
# stint at its last covered day (core_end_exclusive minus one day), takes the
# newest such date across a person's observations, and skips any observation
# whose dates will not parse. That module is not importable for this job without
# dragging in the sidecar's whole node model, so the rule is reproduced
# explicitly -- the same treatment container naming already gets here from
# staffer_graph_snapshot.py.
#
# Two places where a one-cell surface needs more than latest_roles() gives:
#   * latest_roles() returns EVERY role tied at the newest date (measured on the
#     2026-09-05 v5 generation: exactly one office for 6,155 of 6,296 published
#     people, two or three for the other 141). One cell shows one office, so ties
#     break toward the stint the record marks current, then toward the container
#     the person logged the most days in, then by container id so two runs of this
#     producer agree. The tiebreak never reaches outside the set latest_roles()
#     itself returns -- checked 6,296/6,296 against that function before shipping.
#   * A person with no dated payroll stint gets no office from payroll, exactly as
#     latest_roles() skips an undated observation rather than guessing at it.
#     Employment tenure is not established for the sealed LDA identities
#     (adjudication 2026-09-01), and LDA is not a source here at all.
MOST_RECENT_OFFICE_RULE = (
    'Most recent means the newest last covered day (core_end_exclusive) on a dated payroll '
    'stint, the same date the staffer live sidecar uses for a payroll observation. Payroll '
    'takes precedence; a Politico current-committee observation names the office only for a '
    'person with no dated payroll stint. Ties at the same date break toward the stint the '
    'record marks current, then the container with the most days, then the container id.')

# Owner ruling 2026-09-07, item 2, already applied on the Groups surface
# (fec/surfaces/build_staffer_groups_section.py:office_name /
# load_hon_ledger_names): a member office whose bioguide is off the current
# roster is named from the House personnel ledger's own most recent printed
# "HON. FIRST LAST" office string, and an office the record never printed a name
# for is said to be unnamed rather than guessed at. The same rule applies here so
# the two surfaces cannot disagree about what an office is called. Reproduced
# rather than imported: that module is another lane's active file and binds the
# LDA and sidecar corpora, neither of which this producer touches. Agreement
# measured over all 1,833 containers of the 2026-09-05 v5 generation: 1,814
# identical; the 19 differences are all vacant-seat headers of the form
# HON:<ordinal>|<state fragment> ("HON:11TH|JERSEY"), which that module prints
# verbatim and this one refuses to print at all.
HON_LEDGER_GENERATION = 'house_personnel_ledger_20260903_v5_primary'
HON_LEDGER_RECEIPT = 'house_personnel_ledger_receipt.json'
HON_LEDGER_QUARTERS = 'house_member_quarters.ndjson.gz'
HON_PREFIX = 'HON:'

# What the cell says when the records name an office this producer cannot put a
# name to. Never a raw container id, never a guess: the reader is told, in the
# cell itself, that the name is the part that is missing.
OFFICE_NAME_UNRESOLVED = 'Office name not in the records'
OFFICE_NAME_POLICY = (
    'A member office whose bioguide is on the current roster is named for that member. A '
    'House office key that spells the member out (HON:FIRST|LAST) is rendered through the '
    'same name standard people are. A key carrying a bioguide off the current roster is '
    'named from the House personnel ledger\'s own most recent printed office string for that '
    'bioguide. Any other office keeps the record\'s own office_key verbatim. An office none '
    'of those name -- including a vacant-seat header such as HON:11TH|JERSEY -- reads "'
    + OFFICE_NAME_UNRESOLVED + '"; a raw container id is never printed.')

# Owner ruling 2026-09-07, the SECOND half of the same leak. The office column
# above is named here, in the producer. The MOVE tables (top_sources /
# top_destinations) are named in the BROWSER: the page's own containerLabel()
# strips container:office:<chamber>: off the id and looks the remainder up in the
# 533-member roster it already carries, and printed the raw key whenever that
# missed. Every miss is a member office off the CURRENT roster -- exactly the
# population the House personnel ledger exists to name -- so the same rule that
# fixed the office column fixes these, and the page stops inventing a naming rule
# of its own. Measured on the 2026-09-05 v5 generation before the fix: 1,514 of
# 5,912 published move cells read as a raw key.
MOVE_OFFICE_NAME_POLICY = (
    'The move tables are named by the same rule as the office column '
    '(meta.office_name_policy), resolved here rather than in the browser. '
    'meta.move_office_names carries one entry per office the move tables print '
    'whose name the page cannot work out from the roster alone, keyed by the '
    'HON: key the page already extracts from the container id. An office no '
    'record names is ABSENT from that map, which is how the page knows to print '
    'meta.office_name_unresolved_label instead of the key. page_key_differs '
    'counts cells where the office prints its own words here and the member\'s '
    'name in the office column: Senate offices are spelled in prose rather than '
    'as HON: keys, so both readings are the record\'s, and they are left as they '
    'are because one senator can hold several such offices.')

# The one Politico layer admissible here. politico_current_committee rows map to
# a real payroll container (mapping_status EXACT_PAYROLL_CONTAINER) and carry an
# observation date. politico_employment_history is NOT admissible: its object is a
# politico-employer id rather than a container, and its own temporal_scope is
# SOURCE_TEXT_ONLY_NO_INTERVAL_PROMOTION, so it cannot date an office at all.
POLITICO_ROLE = 'staffer_social_politico_review_layers'
POLITICO_NAME = 'staffer_social_politico_review_layers.ndjson.gz'
POLITICO_CURRENT_LAYER = 'politico_current_committee'

# The dedicated office-to-organization layer (D-022 persons-only generations,
# v6+). Optional and bound exactly like the Politico layer above: a generation
# that emits no such file is not a contradiction, it simply predates this file,
# and office_revolving_edge_count then falls back to the legacy container-row
# sum (see read_node_scores' has_container_rows) or to
# OFFICE_REVOLVING_UNAVAILABLE when neither source is present.
REVOLVING_LAYER_ROLE = 'revolving_layer'
REVOLVING_LAYER_NAME = 'staffer_social_revolving_layer.ndjson.gz'
# The mapping the layer itself must claim before a row may name an office: the
# profile's committee was matched to THAT payroll container, not merely to
# something container-shaped.
POLITICO_EXACT_MAPPING = 'EXACT_PAYROLL_CONTAINER'

# Owner ruling 2026-09-07: people whose title on THIS member's record is the
# shared-employee title leave the Top staff RANKING. They stay in the node
# universe, stay in staff_total / staff_current / shared_staff, keep every score,
# and stay reachable on the live surface; they are only removed from the ranked
# list, where they occupied 2,686 of 7,723 cells and topped 358 of 515 member
# pages in the v5 generation and so displaced the office's actual senior staff.
# Exclusion is MEMBER-SCOPED, because title_last is the representative stint for
# this member: the same person still ranks normally at an office where the record
# gives them a substantive title.
# Matched by exact normalized string, never by substring - SHARED FINANCIAL
# ADMINISTRATOR, SHARED IT and SHARED STAFF are different titles and stay.
TOP_STAFF_EXCLUDED_TITLES = frozenset({
    'SHARED EMPLOYEE',
    'SHARED EMPLOYEE (OTHER COMPENSATION)',
})
TOP_STAFF_EXCLUSION_NOTE = (
    'top_staff_excluded_by_title counts this member\'s attached people dropped from the '
    'Top staff ranking because the title on their representative stint with this member '
    'is one of: ' + ', '.join(sorted(TOP_STAFF_EXCLUDED_TITLES)) + '. They remain in '
    'staff_total, staff_current, shared_staff and the node universe; only the ranked '
    'list excludes them.')

# Vocabulary this producer refuses to emit. "influence" is checked separately
# because the receipt's own verbatim disclaimer contains the word inside a
# negation, and a copied receipt line is never edited.
BARRED_TOKENS = ('member_tie', 'member_edge', 'member_to_member', 'persuasion', 'persuade')


# ----------------------------------------------------------------- primitives

def digest(path):
    handle = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            handle.update(block)
    return handle.hexdigest()


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def sig(value, digits=SIGNIFICANT_DIGITS):
    """Round a float to a fixed significant width so the file is stable and small."""
    if value is None:
        return None
    value = float(value)
    if value != value or value in (float('inf'), float('-inf')):
        raise ValueError('non-finite score in the verified artifacts')
    if value == 0.0:
        return 0.0
    return float('%.*g' % (digits, value))


def built_at():
    """UTC build stamp. SOURCE_DATE_EPOCH pins it so a determinism check can
    compare two runs byte for byte; unset, it is the wall clock and is the only
    field that differs between two otherwise identical runs."""
    epoch = os.environ.get('SOURCE_DATE_EPOCH')
    moment = (datetime.fromtimestamp(int(epoch), timezone.utc) if epoch
              else datetime.now(timezone.utc))
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_day(text):
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def union_tenure_days(intervals):
    """Count distinct observed days across concurrent office assignments."""
    total, start, end = 0, None, None
    for left, right in sorted(intervals):
        if right < left:
            raise InputContradiction('trajectory ends before it starts')
        if start is None:
            start, end = left, right
        elif left <= end:
            end = max(end, right)
        else:
            total += (end - start).days
            start, end = left, right
    return total + ((end - start).days if start is not None else 0)


class InputContradiction(RuntimeError):
    """An input does not match what this producer was built against."""


NAME_UNAVAILABLE = 'Name unavailable in source'


def display_name_for(record):
    """The one place a Hill spine record becomes a printable name.

    The spine stores ``payee_key`` as three ALREADY-PARSED and casefolded fields
    (``{"first": "tristan", "last": "dunford", "middle_initial": null}``), so
    joining them raw prints a lower-case name.  ``name_standard`` owns the
    rendering rules (rule 8: "First Middle Last Suffix", with the Mc/Mac, O'/D',
    particle and suffix cases), and this producer never applies its own.

    ``parts_from_payee_key`` is used rather than re-serialising the triple into
    "LAST, FIRST M" and re-parsing it with ``parse_name(..., 'payroll_comma')``.
    ``name_standard.parts_from_payee_key`` (:613-643) documents why that round
    trip is wrong in a silent way: a ``middle_initial`` of "V" is the first
    letter of VICTOR, but as a bare token it is also a valid Roman-numeral
    suffix, so the suffix rule lifts it and the key loses its middle initial.
    ``first`` carries the same exposure.  The already-parsed lift has no such
    ambiguity to resolve.

    A record with no usable parts renders as NAME_UNAVAILABLE rather than an
    empty string: a blank cell reads as a missing person, not a missing name.
    """
    key = ((record or {}).get('payee_key') or {})
    parts = name_standard.parts_from_payee_key(
        key.get('last'), key.get('first'), key.get('middle_initial'))
    return name_standard.display_name(parts) or NAME_UNAVAILABLE


# ------------------------------------------------------------------- binding

def verification_dir_name(generation):
    """staffer_social_network_<v>_primary -> staffer_social_network_<v>_verification."""
    if generation.endswith(PRIMARY_SUFFIX):
        generation = generation[:-len(PRIMARY_SUFFIX)]
    return generation + VERIFICATION_SUFFIX


def bind_inputs(root, generation, hash_edges=False):
    """Fail closed unless every consumed byte is bound to both receipts.

    Mirrors surfaces/staffer_graph_snapshot.py: the verification receipt must be
    PASS with no failures, the generation receipt is bound through the
    verification receipt's twin_byte_identity, each artifact is bound through the
    generation receipt's own files list, and the promotion tripwire must be zero.
    Adds one check that module does not make: every hash the verification receipt
    independently recorded for a primary file must also equal what we computed.
    """
    base = os.path.join(root, 'audits', 'staffer_network', generation)
    verification_path = os.path.join(
        root, 'audits', 'staffer_network', verification_dir_name(generation), VERIFICATION_NAME)
    if not os.path.isdir(base):
        raise InputContradiction('generation directory not found: %s' % base)
    if not os.path.exists(verification_path):
        raise InputContradiction('verification receipt not found: %s' % verification_path)

    with open(verification_path, encoding='utf-8') as stream:
        verification = json.load(stream)
    if verification.get('status') != 'PASS':
        raise InputContradiction(
            'verification receipt status is %r, not PASS' % verification.get('status'))
    if verification.get('failures'):
        raise InputContradiction(
            'verification receipt records %d failure(s)' % len(verification['failures']))

    twin = {entry.get('path'): entry for entry in verification.get('twin_byte_identity') or []}
    integrity = {entry.get('path'): entry for entry in verification.get('primary_file_integrity') or []}
    bound = {}

    def bind(name, role, expected_sources):
        path = os.path.join(base, name)
        if not os.path.exists(path):
            raise InputContradiction('generation file missing: %s' % path)
        actual = digest(path)
        for label, expected in expected_sources:
            if expected is None:
                raise InputContradiction('%s records no sha256 for %s' % (label, name))
            if expected != actual:
                raise InputContradiction(
                    'sha256 mismatch for %s: computed %s, %s records %s'
                    % (name, actual, label, expected))
        bound[role] = {'path': name, 'sha256': actual, 'bytes': os.path.getsize(path),
                       'bound_against': sorted(label for label, _ in expected_sources)}
        return path

    receipt_twin = twin.get(RECEIPT_NAME) or {}
    receipt_path = bind(RECEIPT_NAME, 'generation_receipt',
                        [('verification_receipt.twin_byte_identity',
                          receipt_twin.get('primary_sha256'))])
    manifest_twin = twin.get(MANIFEST_NAME) or {}
    bind(MANIFEST_NAME, 'generation_manifest',
         [('verification_receipt.twin_byte_identity', manifest_twin.get('primary_sha256'))])

    with open(receipt_path, encoding='utf-8') as stream:
        receipt = json.load(stream)
    if receipt.get('canonical_employment_promotions') or receipt.get('canonical_identity_promotions'):
        raise InputContradiction('promotion tripwire failed: the receipt records promotions')
    if receipt.get('review_status') != 'REVIEW_REQUIRED':
        raise InputContradiction(
            'receipt review_status is %r, not REVIEW_REQUIRED' % receipt.get('review_status'))

    receipt_files = {}
    for entry in receipt.get('files') or []:
        receipt_files[entry.get('role')] = entry

    paths = {}
    for role, name in CONSUMED_FILES:
        declared = receipt_files.get(role)
        if declared is None:
            raise InputContradiction('generation receipt declares no file for role %r' % role)
        if declared.get('path') != name:
            raise InputContradiction(
                'generation receipt names %r for role %r, expected %r'
                % (declared.get('path'), role, name))
        checks = [('generation_receipt.files', declared.get('sha256'))]
        recorded = integrity.get(name)
        if recorded is not None:
            checks.append(('verification_receipt.primary_file_integrity.receipt_sha256',
                           recorded.get('receipt_sha256')))
            checks.append(('verification_receipt.primary_file_integrity.recomputed_sha256',
                           recorded.get('recomputed_sha256')))
        paths[role] = bind(name, role, checks)

    # OPTIONAL, and optional on purpose: the Politico review layer is the second
    # half of the owner's source precedence ("payroll takes precedent, then
    # politico"), but a generation that emits no such file is not a contradiction
    # -- it simply has no Politico evidence, and every office then comes from
    # payroll. Bound exactly like a required artifact when it IS declared, so an
    # unverifiable one still fails closed rather than being read anyway.
    politico_declared = receipt_files.get(POLITICO_ROLE)
    if politico_declared is not None:
        if politico_declared.get('path') != POLITICO_NAME:
            raise InputContradiction(
                'generation receipt names %r for role %r, expected %r'
                % (politico_declared.get('path'), POLITICO_ROLE, POLITICO_NAME))
        checks = [('generation_receipt.files', politico_declared.get('sha256'))]
        recorded = integrity.get(POLITICO_NAME)
        if recorded is not None:
            checks.append(('verification_receipt.primary_file_integrity.receipt_sha256',
                           recorded.get('receipt_sha256')))
            checks.append(('verification_receipt.primary_file_integrity.recomputed_sha256',
                           recorded.get('recomputed_sha256')))
        paths[POLITICO_ROLE] = bind(POLITICO_NAME, POLITICO_ROLE, checks)

    # OPTIONAL for the same reason: a generation built before this layer existed
    # has no file to bind, and that is not a contradiction either. Bound exactly
    # like the Politico layer when it IS declared -- an unverifiable one still
    # fails closed rather than being read anyway.
    revolving_declared = receipt_files.get(REVOLVING_LAYER_ROLE)
    if revolving_declared is not None:
        if revolving_declared.get('path') != REVOLVING_LAYER_NAME:
            raise InputContradiction(
                'generation receipt names %r for role %r, expected %r'
                % (revolving_declared.get('path'), REVOLVING_LAYER_ROLE, REVOLVING_LAYER_NAME))
        checks = [('generation_receipt.files', revolving_declared.get('sha256'))]
        recorded = integrity.get(REVOLVING_LAYER_NAME)
        if recorded is not None:
            checks.append(('verification_receipt.primary_file_integrity.receipt_sha256',
                           recorded.get('receipt_sha256')))
            checks.append(('verification_receipt.primary_file_integrity.recomputed_sha256',
                           recorded.get('recomputed_sha256')))
        paths[REVOLVING_LAYER_ROLE] = bind(REVOLVING_LAYER_NAME, REVOLVING_LAYER_ROLE, checks)

    edges_declared = receipt_files.get('staffer_social_edges') or {}
    if hash_edges:
        checks = [('generation_receipt.files', edges_declared.get('sha256'))]
        recorded = integrity.get(EDGES_NAME)
        if recorded is not None:
            checks.append(('verification_receipt.primary_file_integrity.recomputed_sha256',
                           recorded.get('recomputed_sha256')))
        bind(EDGES_NAME, 'staffer_social_edges', checks)
    else:
        bound['staffer_social_edges'] = {
            'path': EDGES_NAME,
            'sha256': edges_declared.get('sha256'),
            'bytes': edges_declared.get('bytes'),
            'bound_against': [],
            'not_recomputed': ('this producer reads no edge row; the 1.5 GB file is not hashed '
                               'here. Rerun with --hash-edges to bind it.')}

    return {'base': base, 'verification_path': verification_path, 'verification': verification,
            'receipt': receipt, 'receipt_path': receipt_path, 'bound': bound, 'paths': paths}


def bind_hon_ledger(root, generation):
    """Bind the House personnel ledger's member-quarter file, or say it is absent.

    Same binding shape the Groups surface uses (build_staffer_groups_section.py:
    bind_hon_ledger): the ledger's own receipt must read PASS and the one file
    opened here must match the sha256 that receipt declares for it, never trusted
    because its name matches. Unlike that module this one does NOT refuse when
    the ledger generation is absent: this producer already skips cleanly on a
    tree with no staffer generation at all, and a tree that has one but no ledger
    must still build. Absence costs names, and it is recorded in meta
    (hon_ledger_generation goes null, office_name_source_counts.ledger goes to 0)
    rather than being silently absorbed -- the offices it would have named read
    OFFICE_NAME_UNRESOLVED, which is visible on the page.
    """
    base = os.path.join(root, 'audits', 'staffer_network', generation)
    receipt_path = os.path.join(base, HON_LEDGER_RECEIPT)
    if not os.path.isfile(receipt_path):
        return None
    with open(receipt_path, encoding='utf-8') as handle:
        receipt = json.load(handle)
    if receipt.get('status') != 'PASS':
        raise InputContradiction('House personnel ledger receipt is not a clean PASS (%s)'
                                 % receipt.get('status'))
    spec = next((f for f in (receipt.get('outputs') or [])
                 if f.get('path') == HON_LEDGER_QUARTERS), None)
    if spec is None:
        raise InputContradiction('House personnel ledger receipt names no %s'
                                 % HON_LEDGER_QUARTERS)
    path = os.path.join(base, HON_LEDGER_QUARTERS)
    if not os.path.isfile(path):
        raise InputContradiction('House personnel ledger file missing: %s' % path)
    actual = digest(path)
    if spec.get('sha256') is None:
        raise InputContradiction('House personnel ledger receipt records no sha256 for %s'
                                 % HON_LEDGER_QUARTERS)
    if actual != spec['sha256']:
        raise InputContradiction(
            'sha256 mismatch for %s: computed %s, house_personnel_ledger_receipt.outputs '
            'records %s' % (HON_LEDGER_QUARTERS, actual, spec['sha256']))
    bound = {
        'house_personnel_ledger_receipt': {
            'path': os.path.relpath(receipt_path, root).replace('\\', '/'),
            'bytes': os.path.getsize(receipt_path), 'sha256': digest(receipt_path),
            'bound_against': ['read']},
        'house_member_quarters': {
            'path': os.path.relpath(path, root).replace('\\', '/'),
            'bytes': os.path.getsize(path), 'sha256': actual,
            'bound_against': ['house_personnel_ledger_receipt.outputs'],
            'purpose': 'names member offices whose bioguide is off the current roster'},
    }
    return {'generation': generation, 'path': path, 'bound': bound}


def load_hon_ledger_names(path):
    """bioguide -> rendered display name, from the ledger's own member-quarter rows.

    Reproduces build_staffer_groups_section.load_hon_ledger_names exactly (owner
    ruling 2026-09-07, item 2): office_family_normalized carries that office's own
    "HON. FIRST LAST" string once per bioguide per reporting quarter, the most
    recent quarter wins when a printed name changed, and a string that will not
    reduce to alphabetic name parts is dropped rather than rendered.
    """
    best = {}
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
        rendered = name_standard.display_name(name_standard.parse_name(remainder, 'display'))
        if rendered:
            names[bioguide] = rendered
    return names


# -------------------------------------------------------------------- roster

def load_roster(root):
    """Reuse the shared kernel's roster; never hand-roll a member list.

    moneyball_core.congestion() is the loader every dashboard producer already
    goes through (fec/surfaces/make_member_desk.py:153,
    fec/surfaces/build_dashboard_bundle.py:2). It reads data/engine/congestion.json,
    the same 533-row roster the generation receipt itself records having used.
    Party comes from the same kernel's Voteview map; state comes from
    fec/legislators.csv joined on bioguide, which is exactly how the member desk
    enriches (make_member_desk.py:924-935).
    """
    root = os.path.abspath(root)
    if os.path.abspath(os.environ.get('MONEYBALL_ROOT') or DEFAULT_ROOT) != root:
        os.environ['MONEYBALL_ROOT'] = root
    import moneyball_core as MBC
    if os.path.abspath(MBC.ROOT) != root:
        raise InputContradiction(
            'the shared kernel resolved root %r but this run targets %r' % (MBC.ROOT, root))
    congestion = MBC.congestion()
    party = MBC.party()

    states = {}
    legislators = os.path.join(root, 'fec', 'legislators.csv')
    if os.path.exists(legislators):
        with open(legislators, encoding='utf-8') as stream:
            for row in csv.DictReader(stream):
                bioguide = (row.get('bioguide_id') or '').strip()
                if bioguide:
                    states[bioguide] = (row.get('state') or '').strip() or None

    roster = {}
    for name, row in congestion.items():
        bioguide = str(row.get('bioguide') or '').strip()
        if not bioguide:
            continue
        roster[bioguide] = {'bioguide': bioguide, 'display_name': name,
                            'party': party.get(name, '?'),
                            'chamber': row.get('chamber'),
                            'state': states.get(bioguide)}
    if not roster:
        raise InputContradiction('the shared kernel returned an empty roster for %s' % root)
    return roster


# ----------------------------------------------------------------- pass one

def read_node_scores(path, roster):
    """One streaming pass over the node scores.

    Returns per-person score records for every person attached to at least one
    member office (roster or historic), the roster-scoped attachment index, the
    shared-staff pair counts, and the person-component summary.
    """
    people = []                       # index -> record tuple
    index_by_node = {}
    member_people = defaultdict(list)  # roster bioguide -> [person index]
    pair_people = Counter()            # (bioA, bioB) sorted -> people attached to both
    unrostered = Counter()
    component_ids = set()
    largest_component = 0
    person_rows = 0
    attached_any = 0
    attached_roster = 0
    node_kinds = Counter()
    member_revolving_edges = Counter()
    # This container row's own short name (ruling 1, RELABEL, 2026-09-07), when
    # this generation still carries container rows at all (v5). A persons-only
    # generation (v6, D-022) leaves this empty; container_display_name() falls
    # back to the container id itself.
    container_office_keys = {}
    # The container row's own member_bioguide, when it declares one. Read for the
    # office-naming rule (owner ruling 2026-09-07, item 2): the bioguide is what
    # the House personnel ledger is keyed by, and an office_key of the form
    # HON:FIRST|LAST carries no bioguide at all.
    container_bioguides = {}

    required = ('node_id', 'node_kind', 'identity_tier', 'member_bioguides',
                'co_tenure_degree', 'component_id', 'component_size',
                'connectedness_pagerank_exploratory', 'eigenvector_component_local_exploratory')
    # GENERATION-ADAPTIVE FIELDS.  Two names for one number, and the row itself
    # says which generation shape it came from.  Neither is optional: a row that
    # carries neither is a schema this producer does not know and refuses.
    office_strengths = {}
    diversity_field = None
    revolving_field = None

    for row in rows(path):
        kind = row.get('node_kind')
        node_kinds[kind] += 1
        if kind != 'person':
            if kind == 'container':
                container_office_keys[row['node_id']] = row.get('office_key')
                container_bioguides[row['node_id']] = row.get('member_bioguide') or None
                if row.get('member_bioguide'):
                    member_revolving_edges[row['member_bioguide']] += int(row.get('revolving_degree') or 0)
            continue
        person_rows += 1
        office_strengths[row['node_id']] = row.get('office_connection_strengths') or {}
        missing = [key for key in required if key not in row]
        if missing:
            raise InputContradiction(
                'node score row %r is missing required field(s): %s'
                % (row.get('node_id'), ', '.join(missing)))
        if diversity_field is None:
            diversity_field = next((name for name in DIVERSITY_FIELDS if name in row), None)
            if diversity_field is None:
                raise InputContradiction(
                    'node score row %r carries none of %s; this producer will not print a '
                    'diversity column it cannot name'
                    % (row.get('node_id'), ', '.join(DIVERSITY_FIELDS)))
        elif diversity_field not in row:
            raise InputContradiction(
                'node score row %r stops carrying %s partway through the file'
                % (row.get('node_id'), diversity_field))
        if revolving_field is None:
            revolving_field = next((name for name in REVOLVING_FIELDS if name in row), None)
            if revolving_field is None:
                raise InputContradiction(
                    'node score row %r carries none of %s'
                    % (row.get('node_id'), ', '.join(REVOLVING_FIELDS)))
        elif revolving_field not in row:
            # Same guard as the diversity column above, and for the same reason:
            # `row.get(field) or 0` on a row that stopped carrying the field is a
            # silent zero, which is indistinguishable from a real zero.
            raise InputContradiction(
                'node score row %r stops carrying %s partway through the file'
                % (row.get('node_id'), revolving_field))
        component_ids.add(row['component_id'])
        size = row.get('component_size') or 0
        if size > largest_component:
            largest_component = size

        bios = row.get('member_bioguides') or []
        attached = row.get('attached_member_bioguides')
        if attached is not None and sorted(attached) != sorted(bios):
            raise InputContradiction(
                'node score row %r disagrees with itself: member_bioguides %r vs '
                'attached_member_bioguides %r' % (row.get('node_id'), bios, attached))
        if not bios:
            continue
        attached_any += 1

        pagerank = None
        for entry in row['connectedness_pagerank_exploratory'] or []:
            if abs(float(entry.get('alpha', -1)) - PAGERANK_ALPHA) < 1e-9:
                pagerank = entry.get('score')
                break
        if pagerank is None:
            raise InputContradiction(
                'node score row %r carries no pagerank at alpha %s'
                % (row.get('node_id'), PAGERANK_ALPHA))

        node_id = row['node_id']
        rostered = sorted({bio for bio in bios if bio in roster})
        for bio in bios:
            if bio not in roster:
                unrostered[bio] += 1
        index = len(people)
        # Round at ingest, not at emission: the ranking below must use the very
        # numbers the file publishes, or the emitted order cannot be reproduced
        # from the emitted fields and a surface that re-sorts disagrees with us.
        people.append((
            node_id,
            row.get('identity_tier'),
            int(row.get('co_tenure_degree') or 0),
            sig(pagerank),
            sig(row.get('eigenvector_component_local_exploratory')),
            sig(row.get(diversity_field)),
            int(row.get(revolving_field) or 0),
            row.get('component_id'),
            len(bios),
            len(bios) - len(rostered),
        ))
        index_by_node[node_id] = index
        if rostered:
            attached_roster += 1
            for bio in rostered:
                member_people[bio].append(index)
            for position, first in enumerate(rostered):
                for second in rostered[position + 1:]:
                    pair_people[(first, second)] += 1

    return {'people': people, 'office_strengths': office_strengths,
            'index_by_node': index_by_node, 'member_people': member_people,
            'pair_people': pair_people, 'unrostered': unrostered,
            'component_ids': component_ids, 'largest_component': largest_component,
            'person_rows': person_rows, 'attached_any': attached_any,
            'attached_roster': attached_roster, 'node_kinds': node_kinds,
            'member_revolving_edges': member_revolving_edges,
            'container_office_keys': container_office_keys,
            'container_bioguides': container_bioguides,
            'diversity_field': diversity_field, 'revolving_field': revolving_field,
            # A generation whose node scores are PERSONS ONLY (D-022, v6) carries
            # no container rows here at all, so office-to-organization edges
            # cannot be summed from this file.  Recorded as a fact rather than
            # left to be inferred from a zero: see OFFICE_REVOLVING_UNAVAILABLE.
            'has_container_rows': bool(node_kinds.get('container'))}


def read_revolving_layer(path):
    """One streaming pass over the office-to-organization revolving layer.

    Counts container_organization rows (direction container_to_organization) per
    office, keyed by the row's own member_bioguide -- summed across all of that
    member's containers, which is how the same organization can legitimately
    appear more than once. The layer's other record kind, person_registrant
    (direction person_to_organization, subject_kind person), is a person-level
    row, not an office edge, and is not counted here. A row of either kind that
    carries no member_bioguide (an admin or non-roster container) is read but
    cannot be attributed to a member and is not counted.

    Every status the generation emits (GUARANTEED, POSSIBLE_ONLY, WRONG_ORDER) is
    counted: this is a count of edges the generation recorded, not a re-judgment
    of which of them it should have recorded.
    """
    counts = Counter()
    rows_read = 0
    office_rows = 0
    counted = 0
    for row in rows(path):
        rows_read += 1
        if row.get('direction') != 'container_to_organization':
            continue
        office_rows += 1
        bioguide = row.get('member_bioguide')
        if not bioguide:
            continue
        counts[bioguide] += 1
        counted += 1
    return {'counts': counts, 'rows_read': rows_read, 'office_rows': office_rows,
            'counted': counted}


# ----------------------------------------------------------------- pass two

def read_trajectories(path, roster, as_of=None):
    """One streaming pass over the trajectories.

    Per (member, person) it keeps whether any stint with that member is current,
    the title printed on the representative stint, and the summed dated tenure.
    The representative stint is the maximum of
    (is_current, core_end_exclusive, core_start, container_id, title_last) so a
    live posting outranks a departed one and the choice is total and stable.

    Alongside that roster-scoped pass, this function also finds, for every person
    project-wide, the ONE payroll container that is their MOST RECENT office or
    committee (owner ruling 2026-09-07, office label; it was the container they
    spent the most days in until that ruling). Admin pools and committees count,
    not only roster member offices, and a stint with no member_bioguide at all --
    which the roster-scoped pass above skips entirely -- still counts here.

    Recency is the stint's own last covered day, core_end_exclusive, which is the
    date the staffer live sidecar dates a payroll observation by
    (staffer_connection_facts.latest_roles: core_end_exclusive minus one day, a
    monotone shift, so the ordering is the same one). A stint missing either date
    is not dated evidence and never names an office, exactly as that function
    skips an observation whose dates will not parse; a person with no dated stint
    at all therefore gets no container here and the caller says so on the page.
    Ties at the same last covered day break toward the stint the record marks
    current, then toward the container holding the most of this person's days,
    then toward the container id itself so the choice is identical between two
    runs regardless of Python's per-process hash seed. See MOST_RECENT_OFFICE_RULE.

    container_people_ever is deliberately NOT restricted to dated stints: it is
    the office's size, "distinct people ever attached", and an undated stint is
    still an attachment.
    """
    as_of = as_of or date.today().isoformat()
    staff = defaultdict(dict)   # roster bioguide -> node_id -> record dict
    stint_rows = 0
    person_rows = 0
    unrostered_stints = Counter()
    home_intervals = defaultdict(lambda: defaultdict(list))  # node_id -> container_id -> [(start, end)]
    home_recency = defaultdict(dict)          # node_id -> container_id -> recency key
    container_people_ever = defaultdict(set)  # container_id -> {node_id, ...}, for v5 office size
    eligible_offices = defaultdict(set)
    member_offices = defaultdict(lambda: defaultdict(set))
    shared_cells = defaultdict(set)
    exposure_episodes, workplace_meta, identity_tiers = [], {}, {}

    for row in rows(path):
        person_rows += 1
        if 'spine_person_id' not in row or 'stints' not in row:
            raise InputContradiction(
                'trajectory row is missing spine_person_id or stints: %r' % sorted(row)[:8])
        node_id = 'person:' + row['spine_person_id']
        identity_tiers[node_id] = {'S1': .75, 'S2': .5, 'S3': .25}.get(row.get('identity_tier'), 1.)
        for stint in row['stints'] or []:
            stint_rows += 1
            is_current = stint.get('status') == 'current'
            container_id = stint.get('container_id')
            if container_id:
                workplace_meta[container_id] = {k: stint.get(k) for k in ('container_kind', 'chamber', 'member_bioguide')}
                exposure_episodes.append({'person_id': node_id, 'employer_id': container_id,
                    'start': stint.get('core_start'), 'end_exclusive': stint.get('core_end_exclusive'),
                    'title': stint.get('title_last'), **workplace_meta[container_id]})
            if excluded_from_top_staff(stint.get('title_last')):
                shared_cells[node_id].add(container_id)
            ranking_eligible = (stint.get('container_kind') != 'admin'
                and not re.search(r':INTERN\s+COMPENSATION(?:\s|[-:–—]|$)', str(container_id or '').upper())
                and not excluded_from_top_staff(stint.get('title_last')))
            if ranking_eligible and container_id:
                eligible_offices[node_id].add(container_id)
                if stint.get('member_bioguide'):
                    member_offices[node_id][stint['member_bioguide']].add(container_id)
            if container_id:
                container_people_ever[container_id].add(node_id)
                home_start = parse_day(stint.get('core_start'))
                home_end = parse_day(stint.get('core_end_exclusive'))
                # DATED EVIDENCE ONLY. A stint missing either bound is not
                # evidence of when this person was there, so it neither dates a
                # container nor makes one a candidate -- the sidecar's own
                # latest_roles() skips exactly these. It still counted as an
                # attachment above, which is what office SIZE is.
                if home_start is not None and home_end is not None:
                    home_intervals[node_id][container_id].append((home_start, home_end))
                    recency = (home_end, 1 if is_current else 0, home_start)
                    if recency > home_recency[node_id].get(container_id, ()):
                        home_recency[node_id][container_id] = recency
            bioguide = stint.get('member_bioguide')
            if not bioguide:
                continue
            if bioguide not in roster:
                unrostered_stints[bioguide] += 1
                continue
            record = staff[bioguide].get(node_id)
            if record is None:
                record = {'current': False, 'title': None, 'tenure_days': 0, 'tenure_intervals': [],
                          'dated_stints': 0, 'stints': 0, 'key': None}
                staff[bioguide][node_id] = record
            record['stints'] += 1
            record['current'] = record['current'] or is_current
            start = parse_day(stint.get('core_start'))
            end = parse_day(stint.get('core_end_exclusive'))
            if start is not None and end is not None:
                record['tenure_intervals'].append((start, end))
                record['dated_stints'] += 1
            key = (1 if is_current else 0,
                   str(stint.get('core_end_exclusive') or ''),
                   str(stint.get('core_start') or ''),
                   str(stint.get('container_id') or ''),
                   str(stint.get('title_last') or ''))
            if record['key'] is None or key > record['key']:
                record['key'] = key
                record['title'] = stint.get('title_last')

    for members in staff.values():
        for record in members.values():
            record['tenure_days'] = union_tenure_days(record.pop('tenure_intervals'))

    # Shared-role exclusion applies to that stint, not to a person's other
    # documented dedicated employment in the same office.
    exposure = ExposureIndex(exposure_episodes, workplace_meta, tiers=identity_tiers)
    member_anchors = {p: exposure.member_anchors(p) for p in identity_tiers}
    adjusted_anchors = {p: exposure.adjusted_member_anchors(p, as_of) for p in identity_tiers}
    home_container = {}
    for node_id, recency in home_recency.items():
        best_container, best_rank = None, None
        for container_id, (last_day, is_current, first_day) in recency.items():
            days = union_tenure_days(home_intervals[node_id].get(container_id, []))
            rank = (last_day, is_current, days, first_day, container_id)
            if best_rank is None or rank > best_rank:
                best_container, best_rank = container_id, rank
        home_container[node_id] = best_container

    return {'staff': staff, 'eligible_offices': eligible_offices, 'member_offices': member_offices,
            'stint_rows': stint_rows, 'person_rows': person_rows,
            'unrostered_stints': unrostered_stints, 'home_container': home_container,
            'container_people_ever': container_people_ever,
            'member_anchors': member_anchors,
            'adjusted_member_anchors': adjusted_anchors,
            'closeness_meta': {'model': CLOSENESS_VERSION, 'allocation': 'equal fractions across concurrent distinct workplaces',
                'uncertainty': uncertainty_specification(as_of),
                'headcount': 'distinct concurrently observed employees; incomplete sources can undercount actual staff',
                'rejected_undated_or_empty_intervals': exposure.rejected}}


# --------------------------------------------------------------- pass three

def read_office_moves(path, roster):
    inbound = defaultdict(list)
    outbound = defaultdict(list)
    rows_read = 0
    for row in rows(path):
        rows_read += 1
        people = int(row.get('people') or 0)
        to_bio = row.get('to_member_bioguide')
        from_bio = row.get('from_member_bioguide')
        if to_bio in roster:
            inbound[to_bio].append((people, row.get('from_container_id'),
                                    row.get('from_container_kind'), from_bio))
        if from_bio in roster:
            outbound[from_bio].append((people, row.get('to_container_id'),
                                       row.get('to_container_kind'), to_bio))
    return {'inbound': inbound, 'outbound': outbound, 'rows_read': rows_read}


# ---------------------------------------------------------------- pass four

def read_politico_current_offices(path):
    """node_id -> the container a Politico profile currently places this person in.

    The SECOND half of the owner's source precedence ("payroll takes precedent,
    then politico"), and it only ever fires for a person payroll cannot place at
    all. Restricted to the one admissible layer: a politico_current_committee row
    carries a spine_person_id, an object_id that is a real payroll container, and
    mapping_status EXACT_PAYROLL_CONTAINER, so it names an office in the same
    vocabulary payroll does. politico_employment_history is refused here -- its
    object is a politico-employer id, not a container, and its own temporal_scope
    (SOURCE_TEXT_ONLY_NO_INTERVAL_PROMOTION) says its dates may not be promoted.

    Newest observed_at wins, then the container id, so the choice is total and
    does not depend on file order. These rows are the generation's own exploratory
    review layer (review_status REVIEW_REQUIRED, canonical_eligible false); they
    name an office on a page that is itself labelled exploratory and they promote
    nothing.
    """
    best = {}
    rows_read = 0
    admissible = 0
    refused = 0
    for row in rows(path):
        rows_read += 1
        if row.get('layer') != POLITICO_CURRENT_LAYER:
            continue
        spine = row.get('spine_person_id')
        container_id = row.get('object_id')
        # Fail closed on all three: a row that does not name a person, does not
        # point at a payroll container, or whose own mapping_status does not
        # claim an EXACT payroll container is refused rather than read loosely.
        # Refusals are counted so a generation that renames or drops the field
        # shows up as a number instead of as a fallback that quietly stopped.
        if (not spine or not str(container_id or '').startswith('container:')
                or row.get('mapping_status') != POLITICO_EXACT_MAPPING):
            refused += 1
            continue
        admissible += 1
        node_id = 'person:' + spine
        key = (str(row.get('observed_at') or ''), str(container_id))
        current = best.get(node_id)
        if current is None or key > current[0]:
            best[node_id] = (key, container_id)
    return {'office': {node: entry[1] for node, entry in best.items()},
            'rows_read': rows_read, 'admissible_rows': admissible,
            'current_committee_rows_refused': refused}


def office_label(office_key, member_bioguide, roster, ledger_names):
    """What an office is called on the page, and which record named it.

    Four cases in order, none of them a name match, and the fifth is an honest
    absence rather than a raw id (owner ruling 2026-09-07, item 2; the same
    ordering build_staffer_groups_section.office_name applies):

      1. the container declares a bioguide on the current roster -> the roster's
         own display name for that member;
      2. the office_key IS HON:<a roster bioguide> -> the same, for a generation
         whose container rows carry no member_bioguide field;
      3. the office_key spells the member out (HON:FIRST|LAST) -> rendered through
         the same name standard people are, which is a rendering of the record's
         own words and not an identity claim;
      4. any other office_key -> the record's own string, verbatim
         ("COMMITTEE ON ENERGY AND COMMERCE");
      5. a HON: key naming neither a roster member nor a name the House personnel
         ledger ever printed -- vacant-seat headers such as HON:11TH|JERSEY, and
         bioguides the ledger is silent on -- returns (None, 'unresolved'), and
         the caller prints OFFICE_NAME_UNRESOLVED. Never the container id.

    Returns (name_or_None, source), where source is one of roster / key_spelled /
    ledger / office_key / unresolved / no_office_key and is counted into meta.
    """
    if member_bioguide and member_bioguide in roster:
        return roster[member_bioguide]['display_name'], 'roster'
    key = str(office_key or '').strip()
    if not key:
        return None, 'no_office_key'
    if not key.startswith(HON_PREFIX):
        return key, 'office_key'
    remainder = key[len(HON_PREFIX):]
    if remainder in roster:
        return roster[remainder]['display_name'], 'roster'
    # A bioguide ("K000404") is an identifier, not a name; only a remainder that
    # carries alphabetic name parts is rendered.
    spelled = ' '.join(part for part in remainder.split('|') if part.strip())
    if spelled and not any(char.isdigit() for char in spelled):
        rendered = name_standard.display_name(name_standard.parse_name(spelled, 'display'))
        if rendered:
            return rendered, 'key_spelled'
    resolved = ledger_names.get(member_bioguide or remainder)
    if resolved:
        return resolved, 'ledger'
    return None, 'unresolved'


# ------------------------------------------------------------------ assembly

def person_core(record):
    """The member-independent person fields. Kept tight: this block is repeated
    up to 15 times per member across 533 members and dominates the file size."""
    (node_id, tier, co_tenure, pagerank, eigen, diversity,
     _revolving, _component, member_count, _unrostered_count) = record
    return {'node_id': node_id,
            'identity_tier': tier,
            'co_tenure_degree': co_tenure,
            'pagerank_085': pagerank,
            'eigen_component_local': eigen,
            # One payload field for both generation shapes; meta.
            # office_diversity_source names the node-score field it came from.
            'office_diversity': diversity,
            'other_member_count': member_count - 1}


def excluded_from_top_staff(title):
    """True when a payroll title is barred from the Top staff ranking.

    Exact match on the normalized title, never a substring test: SHARED FINANCIAL
    ADMINISTRATOR, SHARED IT and SHARED STAFF are distinct titles that stay.
    """
    return (isinstance(title, str)
            and title.strip().upper() in TOP_STAFF_EXCLUDED_TITLES)


def bridge_fields(record):
    entry = person_core(record)
    entry['member_count'] = record[8]
    entry['unrostered_member_count'] = record[9]
    return entry


def move_summary(entries, limit=TOP_MOVE_CONTAINERS):
    ordered = sorted(entries, key=lambda item: (-item[0], str(item[1] or '')))
    return [{'container_id': container, 'container_kind': kind,
             'member_bioguide': bioguide, 'people': people}
            for people, container, kind, bioguide in ordered[:limit]]


def container_display_name(container_id, office_key, roster, ledger_names=None,
                           member_bioguide=None):
    """The name a payroll container is printed under, or the honest absence.

    Thin wrapper over office_label() kept under this name because that is what
    the rest of this file, its tests and the RELABEL record call it. It resolves
    the office_key -- this container's OWN short name off its own node-score row
    on a generation carrying container rows (v5), strings like "JUDICIARY" or
    "SERGEANT AT ARMS AND DOORKEEPER OF THE SENATE" that already read as they are
    -- and falls back to the container id's own trailing key on a persons-only
    generation (v6, D-022), which carries no container rows at all.

    What it will NOT do, as of the owner ruling of 2026-09-07 on the office
    label, is print a container id at a reader. The previous version ended in
    `return raw`, which is how HON:H001060 reached the page for 1,247 of 7,720
    published rows: every one of those is a member office whose bioguide is off
    the current 533-member roster, so the roster lookup failed and the raw key
    was printed as if it were a name. Those are now named from the House
    personnel ledger, and the handful the ledger cannot name say so.

    The caller checks staffer_social_groups.ndjson.gz, when this generation has
    one, BEFORE calling this at all -- that file is the generation's own name for
    the group and takes priority over anything derived here.

    Returns (name_or_None, source); see office_label for the source vocabulary.
    """
    key = str(office_key or '').strip()
    if not key:
        # v6: no container rows, so the id's own trailing segment is the key.
        raw = re.sub(r'^container:office:(house|senate):', '', str(container_id or ''))
        key = re.sub(r'^container:', '', raw)
    return office_label(key, member_bioguide, roster, ledger_names or {})


def group_display_name(group):
    """A group artifact can carry an office key in its name field, not a label."""
    name = str((group or {}).get('name') or '').strip()
    if re.match(r'^(?:\d{4}\s+)?(?:HON:|container:)', name, re.IGNORECASE):
        return None
    return name or None


def build_members(roster, scores, trajectories, moves, groups_by_id,
                  ledger_names=None, politico_offices=None, office_sources=None,
                  office_revolving_counts=None):
    people = scores['people']
    ledger_names = ledger_names or {}
    politico_offices = politico_offices or {}
    office_sources = office_sources if office_sources is not None else Counter()
    members = {}
    shared_partners = defaultdict(list)
    for (first, second), count in scores['pair_people'].items():
        shared_partners[first].append((count, second))
        shared_partners[second].append((count, first))

    for bioguide, identity in roster.items():
        indexes = scores['member_people'].get(bioguide, [])
        staff_records = trajectories['staff'].get(bioguide, {})

        staff_total = len(staff_records)
        staff_current = sum(1 for record in staff_records.values() if record['current'])

        shared_staff = sum(1 for index in indexes if people[index][8] >= 2)
        shared_with_unrostered = sum(1 for index in indexes if people[index][9] >= 1)
        partners = sorted(shared_partners.get(bioguide, []), key=lambda item: (-item[0], item[1]))
        shared_with = [{'bioguide': other, 'people': count}
                       for count, other in partners[:TOP_SHARED_WITH]]

        # Owner ruling 2026-09-07: shared-employee titles leave the RANKING POOL,
        # before ranking rather than after slicing, so the published list is the top
        # TOP_STAFF of the eligible people and not a short list with holes in it.
        eligible = []
        excluded_by_title = 0
        for index in indexes:
            record = staff_records.get(people[index][0])
            if intern_title(record['title'] if record else None):
                continue
            if excluded_from_top_staff(record['title'] if record else None):
                excluded_by_title += 1
                continue
            eligible.append(index)

        strengths = scores.get('office_strengths', {})
        def member_connection(index):
            node = people[index][0]
            if 'member_anchors' in trajectories:
                return sig(trajectories['member_anchors'].get(node, {}).get(bioguide, 0.)) or 0.
            offices = trajectories.get('member_offices', {}).get(node, {}).get(bioguide, ())
            return sig(sum(strengths.get(node, {}).get(c, 0) for c in offices)) or 0.
        context = trajectories.get('member_context')
        def ranking_values(index):
            anchor = member_connection(index)
            info = context.score(trajectories.get('member_anchors', {}).get(people[index][0], {}), {bioguide: 1.}) if context else {'value': 0., 'coverage': 0.}
            adjusted = trajectories.get('adjusted_member_anchors', {}).get(people[index][0], {}).get(bioguide, anchor)
            return sig(adjusted * (1 + MEMBER_BETA * info['value'])), anchor, info
        values = {i: ranking_values(i) for i in eligible}
        # Isolates and people attached only through excluded payroll headers have
        # no ranked connection to this member. A provenance-only attachment is not a tie.
        if 'member_anchors' in trajectories or any(strengths.values()):
            eligible = [i for i in eligible if member_connection(i) > 0]
        ranked = sorted(eligible, key=lambda index: (-values[index][0], -values[index][1], people[index][0]))
        top_staff = []
        for index in ranked[:TOP_STAFF]:
            entry = person_core(people[index])
            entry['member_connection_strength'] = member_connection(index)
            entry['connection_score'] = values[index][0]
            entry['member_context_score'] = sig(values[index][2]['value'])
            entry['rank_tie_size'] = sum(
                values[other][:2] == values[index][:2]
                for other in eligible)
            record = staff_records.get(entry['node_id'])
            entry['title_last'] = record['title'] if record else None
            entry['status'] = ('current' if record and record['current']
                               else ('departed' if record else None))
            entry['tenure_days_with_member'] = (
                record['tenure_days'] if record and record['dated_stints'] else None)
            # Owner ruling 2026-09-07 (item 1, RELABEL): a GLOBAL, person-scoped
            # pair of fields, not scoped to the member whose page is being built,
            # so the same person reports the same office and size on every member
            # page they appear on (home_container is keyed by node_id across ALL
            # of that person's stints project-wide).
            #
            # Owner ruling 2026-09-07 (office label): the container is now the
            # person's MOST RECENT one, payroll first and a Politico
            # current-committee observation only where payroll cannot place them
            # at all. Null, not a placeholder string, when NEITHER source carries
            # dated evidence of an office for this person; OFFICE_NAME_UNRESOLVED,
            # not a container id, when an office IS on record but no record names
            # it.
            node = entry['node_id']
            home_container_id = trajectories['home_container'].get(node)
            source = 'payroll'
            if home_container_id is None:
                home_container_id = politico_offices.get(node)
                source = 'politico' if home_container_id is not None else 'none'
            if home_container_id is None:
                office_sources['absent_no_dated_evidence'] += 1
                entry['co_tenure_office_name'] = None
                entry['co_tenure_office_size'] = None
            else:
                group_row = groups_by_id.get(home_container_id)
                office_key = scores['container_office_keys'].get(home_container_id)
                # NOT `bioguide`: that name is the member whose page is being
                # built, and rebinding it here silently rekeys the whole members
                # dict to whichever office the last displayed person came from.
                container_bioguide = scores['container_bioguides'].get(home_container_id)
                name, named_by = container_display_name(
                    home_container_id, office_key, roster, ledger_names, container_bioguide)
                group_name = group_display_name(group_row)
                if group_name:
                    name, named_by = group_name, 'group_file'
                office_sources['%s_%s' % (named_by, source)] += 1
                entry['co_tenure_office_name'] = (
                    name if name is not None else OFFICE_NAME_UNRESOLVED)
                entry['co_tenure_office_size'] = (
                    group_row.get('people_count') if group_row is not None
                    else len(trajectories['container_people_ever'].get(home_container_id, ())))
            top_staff.append(entry)

        title_counts = Counter()
        for record in staff_records.values():
            if record['current']:
                title_counts[record['title']] += 1
        titles = [{'title_last': title, 'people': count}
                  for title, count in sorted(title_counts.items(),
                                             key=lambda item: (-item[1], item[0] is None,
                                                               str(item[0] or '')))[:TOP_TITLES]]

        component_counts = Counter(people[index][7] for index in indexes)
        component_ids = [component for component, _ in
                         sorted(component_counts.items(), key=lambda item: (-item[1], item[0]))
                         ][:MAX_COMPONENT_IDS]
        largest_share = (sig(max(component_counts.values()) / float(len(indexes)))
                         if indexes else None)

        inbound = moves['inbound'].get(bioguide, [])
        outbound = moves['outbound'].get(bioguide, [])

        members[bioguide] = {
            'bioguide': bioguide,
            'display_name': identity['display_name'],
            'party': identity['party'],
            'chamber': identity['chamber'],
            'state': identity['state'],
            'staff_total': staff_total,
            'staff_current': staff_current,
            'staff_departed': staff_total - staff_current,
            'staff_total_node_scores': len(indexes),
            'shared_staff': shared_staff,
            'shared_staff_with_unrostered_members': shared_with_unrostered,
            'shared_with': shared_with,
            'shared_with_partner_count': len(partners),
            'top_staff': top_staff,
            'top_staff_excluded_by_title': excluded_by_title,
            'titles': titles,
            'moves_in': sum(item[0] for item in inbound),
            'moves_out': sum(item[0] for item in outbound),
            'moves_in_office_pairs': len(inbound),
            'moves_out_office_pairs': len(outbound),
            'top_sources': move_summary(inbound),
            'top_destinations': move_summary(outbound),
            'revolving_degree_sum': sum(people[index][6] for index in indexes),
            'office_revolving_edge_count': (
                office_revolving_counts.get(bioguide, 0) if office_revolving_counts is not None
                else (scores['member_revolving_edges'].get(bioguide, 0)
                      if scores['has_container_rows'] else None)),
            'component_ids': component_ids,
            'largest_component_share': largest_share,
        }
    return members


def move_office_names(members, roster, scores, groups_by_id, ledger_names):
    """The names the MOVE tables need, and nothing else.

    Resolution is not restated here: every container goes through the SAME
    container_display_name() / office_label() the office column goes through, with
    the same inputs -- the container's OWN office_key and member_bioguide off its
    node-score row, and this generation's staffer_social_groups name when it has
    one -- so the two surfaces cannot disagree about what an office is called.

    What this adds is scope. Only the containers the move tables actually publish
    are resolved, and of those only the ones whose name the page cannot already
    work out for itself are emitted:

      * a non-HON key ("COMMITTEE ON APPROPRIATIONS") is the record's own office
        string and the page prints it verbatim, which is what the rule says to do;
      * a HON: key naming a member on the CURRENT roster is already in the page's
        own name map;
      * everything else -- a bioguide off the roster named from the House
        personnel ledger, a key that spells the member out, a group-file name --
        is emitted, keyed by the HON: key the page already extracts;
      * an office no record names is ABSENT, and the page prints
        meta.office_name_unresolved_label rather than the key.

    The key is that extracted string rather than the container id because the id
    is four times longer and this map is spent against a fixed byte cap. Two
    containers that reduce to the same key and disagree about the name would make
    that key a lie, so they refuse rather than letting one silently win.

    Returns (names, counts); counts is over published CELLS, in the same
    vocabulary as meta.office_name_source_counts, so a source that stops firing
    shows up as a number rather than as a fallback nobody noticed.
    """
    cells = Counter()
    for member in members.values():
        for field in ('top_sources', 'top_destinations'):
            for row in member[field]:
                cells[row['container_id']] += 1

    names = {}
    counts = Counter()
    for container_id, published in cells.items():
        office_key = scores['container_office_keys'].get(container_id)
        container_bioguide = scores['container_bioguides'].get(container_id)
        name, named_by = container_display_name(
            container_id, office_key, roster, ledger_names, container_bioguide)
        group_row = groups_by_id.get(container_id)
        group_name = group_display_name(group_row)
        if group_name:
            name, named_by = group_name, 'group_file'

        page_key = re.sub(r'^container:office:(house|senate):', '', str(container_id or ''))
        page_key = re.sub(r'^container:', '', page_key)
        if not page_key.startswith(HON_PREFIX):
            # The page prints this key verbatim, which is what rule case 4 says to
            # do with an office's own string. It is NOT always what the office
            # column prints for the same container, and that gap is counted rather
            # than assumed away.
            #
            # MEASURED on the 2026-09-05 v5 generation: 272 of 5,912 cells, 135
            # containers, every one of them a SENATE member office. The Senate
            # payroll spells its offices in prose ("SENATOR ELIZABETH WARREN",
            # "INTERN COMPENSATION - SCHUMER") where the House uses HON:<bioguide>,
            # so the container declares a roster bioguide and office_label's case 1
            # names it after the member while the page prints the office's own
            # words. Both are honest; neither is a container id. They are left
            # DIFFERENT deliberately: two of those containers can belong to one
            # senator, and collapsing them onto the member's name would make two
            # distinct rows of a move table read identically. Whether the move
            # tables should say "Charles Schumer" instead is the owner's call, not
            # this producer's, and this count is what puts the question in front of
            # him.
            counts['office_key' if name == page_key else 'page_key_differs'] += published
            continue
        remainder = page_key[len(HON_PREFIX):]
        if name is None:
            counts['unresolved'] += published
            continue
        if remainder in roster and name == roster[remainder]['display_name']:
            counts['roster'] += published
            continue
        prior = names.get(remainder)
        if prior is not None and prior != name:
            raise InputContradiction(
                'two move containers reduce to the office key %r and name it '
                'differently (%r vs %r); the page keys its move names on that '
                'string and cannot tell the two apart' % (remainder, prior, name))
        names[remainder] = name
        counts[named_by] += published
    return names, dict(sorted(counts.items()))


def build_global(roster, scores, members, receipt, trajectories=None):
    edge_counts = receipt.get('edge_counts') or {}
    rejected = edge_counts.get('co_tenure_zero_overlap_REJECTED_never_emitted')
    overlap_check = (receipt.get('overlap_rule_check') or {}).get('pairs_REJECTED_for_no_overlap')
    if rejected is None:
        raise InputContradiction(
            'the generation receipt records no co_tenure_zero_overlap_REJECTED_never_emitted count')
    if overlap_check is not None and overlap_check != rejected:
        raise InputContradiction(
            'the receipt disagrees with itself on rejected zero-overlap pairs: %r vs %r'
            % (rejected, overlap_check))

    people = scores['people']
    bridge_strength = {}
    eligible_member_count = {}
    for index, person in enumerate(people):
        node = person[0]
        strength = scores.get('office_strengths', {}).get(node, {})
        offices = (trajectories or {}).get('member_offices', {}).get(node, {})
        # Count a member only when a permitted workplace actually supplied ties.
        mass = [sum(strength.get(c, 0) for c in containers) for containers in offices.values()]
        if 'member_anchors' in (trajectories or {}):
            anchors = trajectories.get('adjusted_member_anchors', trajectories['member_anchors'])
            mass = [anchors.get(node, {}).get(bio, 0.) for bio in offices]
        mass = [x for x in mass if x > 0]
        if len(mass) < 2:
            continue
        # Connection mass beyond the strongest office: years of extra supported
        # ties outrank a fleeting second posting. No count-first sort remains.
        bridge_strength[index] = sig(sum(mass) - max(mass)) or 0.
        eligible_member_count[index] = len(mass)
    bridge_order = sorted(bridge_strength, key=lambda i: (-bridge_strength[i], people[i][0]))
    top_bridge = [dict(bridge_fields(people[i]), bridge_connection_strength=bridge_strength[i],
                       eligible_member_count=eligible_member_count[i],
                       rank_tie_size=sum(bridge_strength[j] == bridge_strength[i]
                                         for j in bridge_order))
                  for i in bridge_order[:TOP_BRIDGE_PEOPLE]]

    return {
        'node_universe': receipt.get('node_universe'),
        'edge_counts': edge_counts,
        'zero_overlap_pairs_rejected': rejected,
        'zero_overlap_rule': (receipt.get('overlap_rule_check') or {}).get('rule'),
        'rule_version': receipt.get('rule_version'),
        'generated_at': receipt.get('generated_at'),
        'person_components': {
            'distinct_component_ids': len(scores['component_ids']),
            'largest_component_size': scores['largest_component'],
        },
        'top_bridge_people': top_bridge,
        'top_bridge_ranking': 'Size-adjusted direct member-office exposure beyond the strongest eligible member office; equal scores are ties',
        'members_with_any_staff': sum(1 for entry in members.values() if entry['staff_total'] > 0),
        'members_with_shared_staff': sum(1 for entry in members.values() if entry['shared_staff'] > 0),
        'roster_size': len(roster),
        'person_nodes': scores['person_rows'],
        'person_nodes_with_member_attachment': scores['attached_any'],
        'person_nodes_with_roster_member_attachment': scores['attached_roster'],
        'staff_total_sum_over_members': sum(entry['staff_total'] for entry in members.values()),
        'staff_total_node_scores_sum_over_members': sum(
            entry['staff_total_node_scores'] for entry in members.values()),
        'members_where_trajectory_and_node_score_populations_differ': sum(
            1 for entry in members.values()
            if entry['staff_total'] != entry['staff_total_node_scores']),
        'shared_staff_note': SHARED_STAFF_NOTE,
        'top_staff_excluded_titles': sorted(TOP_STAFF_EXCLUDED_TITLES),
        'top_staff_excluded_by_title_sum_over_members': sum(
            entry['top_staff_excluded_by_title'] for entry in members.values()),
        # Same figure as the sum above, named for ruling 3 specifically (2026-09-07):
        # TOP_STAFF_EXCLUDED_TITLES today contains only the two SHARED EMPLOYEE
        # variants, so every title-excluded cell IS a shared-employee cell. Kept as
        # its own named field so a reader does not have to cross-reference
        # top_staff_excluded_titles to know what this ruling specifically removed;
        # if a future, unrelated title is ever added to that set the two numbers
        # would stop agreeing, which is the point of keeping them separate.
        'top_staff_excluded_shared_employee': sum(
            entry['top_staff_excluded_by_title'] for entry in members.values()),
        'members_with_a_top_staff_title_exclusion': sum(
            1 for entry in members.values() if entry['top_staff_excluded_by_title'] > 0),
        'top_staff_exclusion_note': TOP_STAFF_EXCLUSION_NOTE,
    }


def check_vocabulary(payload, serialized):
    """Refuse to ship member-to-member vocabulary. Fail loud, never quietly edit."""
    lowered = serialized.lower()
    hits = [token for token in BARRED_TOKENS if token in lowered]
    if hits:
        raise InputContradiction('barred vocabulary in the output: %s' % ', '.join(hits))
    statement = (payload.get('meta') or {}).get('statement') or ''
    without_statement = serialized.replace(json.dumps(statement, ensure_ascii=False), '')
    if 'influence' in without_statement.lower():
        raise InputContradiction(
            'the word "influence" appears outside meta.statement, which is the only place a '
            'verbatim receipt quote may carry it')


def offenders(payload, members, limit=10):
    def size(value):
        return len(json.dumps(value, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=False).encode('utf-8'))
    sections = sorted(((key, size(value)) for key, value in payload.items()),
                      key=lambda item: -item[1])
    worst = sorted(((key, size(value)) for key, value in members.items()),
                   key=lambda item: (-item[1], item[0]))[:limit]
    return sections, worst


# -------------------------------------------------------------------- output

def write_atomic(path, payload_bytes):
    """Temp + os.replace, with a bounded retry because the tree lives under OneDrive."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=directory, prefix='.staffer_network_section.',
                                    suffix='.tmp')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(payload_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        delay = 0.25
        failure = None
        for attempt in range(12):
            try:
                os.replace(temp, path)
                return attempt + 1
            except PermissionError as exc:
                failure = exc
            except OSError as exc:
                if getattr(exc, 'winerror', None) not in (5, 32):
                    raise
                failure = exc
            if attempt < 11:
                time.sleep(delay)
                delay = min(delay * 2, 4.0)
        raise failure
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def glassbox(enabled, kind, status, summary, metadata=None, detail='', duration_ms=-1):
    """Emit one glassbox event. Non-fatal when the emitter is absent."""
    if not enabled:
        return False
    emitter = os.environ.get('GLASSBOX_EMIT') or os.path.join(
        os.path.expanduser('~'), '.claude', 'glassbox', 'emit.ps1')
    if not os.path.exists(emitter):
        return False
    # emit.ps1 takes named parameters; from a non-PowerShell parent the equivalent
    # of splatting a hashtable is passing the same -Name Value pairs on argv.
    # -ExecutionPolicy Bypass matches the shipped convention (see
    # fec/collect/campaign_sites_2026.py:emit_glassbox); without it this box's
    # machine policy refuses to load the emitter and every event is lost.
    argv = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', emitter,
            '-Lane', 'claude', '-Source', 'staffer-network-section',
            '-Kind', kind, '-Status', status, '-Summary', summary,
            '-Cwd', os.getcwd(), '-Quiet']
    if detail:
        argv += ['-Detail', detail]
    if metadata is not None:
        argv += ['-MetadataJson', json.dumps(metadata, sort_keys=True, separators=(',', ':'))]
    if duration_ms >= 0:
        argv += ['-DurationMs', str(int(duration_ms))]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        # Non-fatal, but never silent: a swallowed emitter failure is how a
        # process ends up looking instrumented while emitting nothing.
        print('glassbox emit failed (%s): %s' % (type(exc).__name__, exc), file=sys.stderr)
        return False
    if result.returncode != 0:
        print('glassbox emit returned %d: %s'
              % (result.returncode, (result.stderr or '').strip().splitlines()[:1]),
              file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------- main

def build(root, generation, out_path, max_bytes, use_glassbox=True, hash_edges=False,
          hon_ledger_generation=HON_LEDGER_GENERATION):
    started = time.perf_counter()
    glassbox(use_glassbox, 'producer', 'running',
             'Staffer Network section: binding %s' % generation,
             {'generation': generation, 'out': out_path, 'max_bytes': max_bytes})
    try:
        binding = bind_inputs(root, generation, hash_edges=hash_edges)
        receipt = binding['receipt']
        roster = load_roster(root)
        scores = read_node_scores(binding['paths']['staffer_social_node_scores'], roster)
        trajectories = read_trajectories(binding['paths']['staffer_trajectories'], roster)
        moves = read_office_moves(binding['paths']['staffer_office_moves'], roster)

        # The two artifacts are joined on node_id == "person:" + spine_person_id.
        # If that join ever slips, every title, status and tenure silently becomes
        # null while the counts still look plausible, so it is a hard gate rather
        # than a degraded read. Measured on the v4 generation: 0 misses.
        index = scores['index_by_node']
        missing = [node for staff in trajectories['staff'].values()
                   for node in staff if node not in index]
        if missing:
            raise InputContradiction(
                '%d trajectory people do not join the node scores on node_id (e.g. %s)'
                % (len(missing), missing[0]))

        # v6 only (D-022): container name/size move out of node_scores into a
        # fourth generation artifact -- id/kind/name/people_count per group.  The
        # v5 generation pinned above declares no such file, and its absence is
        # normal, not a contradiction: ruling 1 (RELABEL, 2026-09-07) falls back to
        # computing both from what this producer already reads (container_display_
        # name / container_people_ever) when it is missing.
        groups_spec = next((f for f in receipt.get('files', [])
                            if f.get('role') == 'groups'), None)
        groups_by_id = {}
        if groups_spec is not None:
            groups_path = os.path.join(binding['base'], groups_spec['path'])
            if digest(groups_path) != groups_spec['sha256']:
                raise InputContradiction(
                    'staffer_social_groups does not match its declared hash')
            for row in rows(groups_path):
                group_id = row.get('group_id', row.get('id'))
                if not isinstance(group_id, str) or not group_id:
                    raise InputContradiction('group artifact row has no group_id or legacy id')
                if 'group_id' in row and 'id' in row and row['group_id'] != row['id']:
                    raise InputContradiction('group artifact row has conflicting IDs')
                if group_id in groups_by_id:
                    raise InputContradiction('group artifact repeats ID: %s' % group_id)
                groups_by_id[group_id] = row
            binding['bound']['staffer_social_groups'] = {
                'path': groups_spec['path'], 'sha256': groups_spec['sha256'],
                'bytes': os.path.getsize(groups_path), 'verified': True,
                'bound_against': ['generation_receipt.files']}

        # Owner ruling 2026-09-07 (office label), the two halves of it that are
        # not the generation's own artifacts. The ledger NAMES member offices off
        # the current roster; the Politico layer is the fallback SOURCE for a
        # person payroll cannot place. Both optional, both bound when present.
        hon_ledger = bind_hon_ledger(root, hon_ledger_generation)
        ledger_names = load_hon_ledger_names(hon_ledger['path']) if hon_ledger else {}
        if hon_ledger:
            binding['bound'].update(hon_ledger['bound'])
        politico_path = binding['paths'].get(POLITICO_ROLE)
        politico = (read_politico_current_offices(politico_path) if politico_path
                    else {'office': {}, 'rows_read': 0, 'admissible_rows': 0,
                          'current_committee_rows_refused': 0})

        # OPTIONAL, same shape as the Politico binding above: None means the
        # generation carries no dedicated revolving layer at all (build_members
        # then falls back to the legacy container-row sum, or to
        # OFFICE_REVOLVING_UNAVAILABLE when neither source is present); a Counter
        # -- even an empty one -- means the layer was read and a member absent
        # from it genuinely has zero recorded office-to-organization edges.
        revolving_path = binding['paths'].get(REVOLVING_LAYER_ROLE)
        office_revolving = read_revolving_layer(revolving_path) if revolving_path else None

        office_sources = Counter()
        trajectories['member_context'] = load_member_context(root)
        members = build_members(roster, scores, trajectories, moves, groups_by_id,
                                ledger_names=ledger_names,
                                politico_offices=politico['office'],
                                office_sources=office_sources,
                                office_revolving_counts=(office_revolving['counts']
                                                          if office_revolving else None))
        # Owner ruling 2026-09-07, the move tables. Built AFTER the members, from
        # the published rows themselves, so the map covers exactly what ships and
        # cannot drift from it.
        move_names, move_name_counts = move_office_names(
            members, roster, scores, groups_by_id, ledger_names)
        summary = build_global(roster, scores, members, receipt, trajectories)
        displayed = [person for member in members.values() for person in member['top_staff']]
        displayed += summary['top_bridge_people']
        name_spec = next((s for s in receipt.get('inputs', []) if s['role'] == 'hill_person_spine'), None)
        if name_spec:
            original = os.path.normpath(name_spec['path'])
            name_path = os.path.join(root, 'audits', 'staffer_network', os.path.basename(os.path.dirname(original)), os.path.basename(original))
            if not os.path.isfile(name_path):
                name_path = original
            if not os.path.isfile(name_path):
                # Neither the generation-local copy nor the absolute path the receipt
                # recorded at build time exists. digest() would raise FileNotFoundError
                # here, which main() does not catch, so a missing spine printed a
                # traceback and exited 1 instead of the REFUSED line and exit 2 every
                # other binding failure in this producer uses.
                raise InputContradiction(
                    'display-name spine not found: neither %s nor the generation input %r exists'
                    % (name_path, name_spec['path']))
            if digest(name_path) != name_spec['sha256'] or os.path.getsize(name_path) != name_spec['bytes']:
                raise InputContradiction('Display-name spine does not match the generation input')
            wanted = {p['node_id'] for p in displayed}
            names = {}
            for record in rows(name_path):
                ident = 'person:' + record['spine_person_id']
                if ident in wanted:
                    names[ident] = display_name_for(record)
            for person in displayed:
                person['display_name'] = names.get(person['node_id']) or NAME_UNAVAILABLE
            binding['bound']['hill_person_spine_display_names'] = {**name_spec, 'path': name_path, 'verified': True, 'purpose': 'exact-ID display-name join'}

        unrostered = scores['unrostered']
        meta = {
            'schema_id': SCHEMA_ID,
            'producer': os.path.relpath(os.path.abspath(__file__), root).replace('\\', '/'),
            'producer_version': PRODUCER_VERSION,
            'generation': generation,
            'generation_root': os.path.relpath(binding['base'], root).replace('\\', '/'),
            'inputs': binding['bound'],
            'verification': {
                'status': binding['verification'].get('status'),
                'failures': len(binding['verification'].get('failures') or []),
                'path': os.path.relpath(binding['verification_path'], root).replace('\\', '/'),
                'sha256': digest(binding['verification_path']),
            },
            'roster_source': 'moneyball_core.congestion() over data/engine/congestion.json',
            'roster_size': len(roster),
            'unrostered_member_bioguides': len(unrostered),
            'unrostered_member_bioguide_attachments': int(sum(unrostered.values())),
            'exploratory': True,
            'review_status': 'REVIEW_REQUIRED',
            'canonical_eligible': False,
            'canonical_employment_promotions': 0,
            'statement': receipt.get('statement'),
            'language_note': LANGUAGE_NOTE,
            'tenure_note': TENURE_NOTE,
            'co_tenure_office_note': CO_TENURE_OFFICE_NOTE,
            # Owner ruling 2026-09-07 (office label). The rule, the naming policy,
            # and a census of which record named each published cell -- so a
            # reader can see, without rerunning anything, how many offices came
            # from the roster, from the House personnel ledger, from the record's
            # own office string, from Politico, and how many are honestly unnamed.
            'co_tenure_office_rule': MOST_RECENT_OFFICE_RULE,
            'office_name_policy': OFFICE_NAME_POLICY,
            'office_name_unresolved_label': OFFICE_NAME_UNRESOLVED,
            'office_name_source_counts': dict(sorted(office_sources.items())),
            # The same ruling applied to the MOVE tables, which name their offices
            # in the browser. The map is the names the page cannot work out from
            # the roster; the census says which record named each published cell.
            'move_office_name_policy': MOVE_OFFICE_NAME_POLICY,
            'move_office_names': move_names,
            'move_office_name_source_counts': move_name_counts,
            'hon_ledger_generation': hon_ledger['generation'] if hon_ledger else None,
            'politico_fallback': {
                'layer': POLITICO_CURRENT_LAYER,
                'bound': politico_path is not None,
                'rows_read': politico['rows_read'],
                'admissible_rows': politico['admissible_rows'],
                'current_committee_rows_refused': politico['current_committee_rows_refused'],
                'people_with_a_current_committee': len(politico['office']),
                'note': ('Politico names an office only for a person with no dated payroll '
                         'stint (payroll takes precedence). politico_employment_history is '
                         'not read: its object is not a payroll container and its own '
                         'temporal_scope forbids promoting its dates to an interval.'),
            },
            'co_tenure_office_size_source': (
                "this generation's own staffer_social_groups.ndjson.gz people_count for a "
                'container it lists; computed as distinct spine_person_id ever attached, from '
                'staffer_trajectories.ndjson.gz, for any container it does not'
                if groups_by_id else
                'computed from staffer_trajectories.ndjson.gz: distinct spine_person_id ever '
                'attached to the container (no staffer_social_groups.ndjson.gz on this generation)'),
            # ONE meta block for the receipt's policy-evidence control, whatever
            # the generation calls it.  v6 renamed ranking_controls (three
            # counterfactual rebuild scenarios over a kind-balanced operator) to
            # policy_link_pagerank_control (one descriptive comparison on the
            # persons-only operator).  meta.pagerank_policy_control_source names
            # the receipt key it came from, because the two are not the same
            # measurement and a reader must not treat them as interchangeable.
            'pagerank_policy_control': (receipt.get('policy_link_pagerank_control')
                                        if receipt.get('policy_link_pagerank_control') is not None
                                        else receipt.get('ranking_controls')),
            'pagerank_policy_control_source': (
                'policy_link_pagerank_control'
                if receipt.get('policy_link_pagerank_control') is not None
                else ('ranking_controls' if receipt.get('ranking_controls') is not None else None)),
            'office_diversity_source': scores['diversity_field'],
            'revolving_source': scores['revolving_field'],
            'office_revolving_source': (
                REVOLVING_LAYER_NAME if office_revolving is not None
                else ('node_scores container rows' if scores['has_container_rows'] else None)),
            'score_notes': {
                'connection_score': 'Direct member-office exposure with uncertainty penalties for unverified access, age and workplace concentration; member context capped at 15%; global staff PageRank is diagnostic only.',
                'pagerank_085': 'Exploratory connectedness on person-to-person co-tenure, with uniform restart over people; sensitive to source coverage.',
                'office_diversity': DIVERSITY_SOURCE_NOTES.get(scores['diversity_field'], ''),
                'eigen_component_local': 'Diagnostic on the symmetrized walk; normalized separately within each component and not comparable across components. Digits shown are not a per-entry accuracy guarantee.',
                'person_components': 'Components of the person-to-person co-tenure graph.',
                'office_revolving_edge_count': (
                    'Positive office-to-organization edges summed across this member\'s containers; the same organization can appear at more than one container.'
                    if (scores['has_container_rows'] or office_revolving is not None)
                    else OFFICE_REVOLVING_UNAVAILABLE),
                'revolving_degree_sum': 'Legacy sum over person-node degrees; revolving edges attach to containers, so this field does not measure member-office connections.',
            },
            'name_policy': ('Display names join only by exact spine ID from the generation-bound payroll spine; names never create identity matches.'
                            if name_spec else 'No display-name spine bound in this generation; names unavailable.'),
            'zero_overlap_pairs_rejected': summary['zero_overlap_pairs_rejected'],
            'closeness': {**trajectories.get('closeness_meta', {}),
                'member_context': trajectories['member_context'].provenance, 'member_context_max_bonus': MEMBER_BETA},
            'built_at': built_at(),
            'bytes': 0,
        }
        if meta['statement'] is None:
            raise InputContradiction('the generation receipt carries no statement field')

        payload = {'meta': meta, 'global': summary, 'members': members}

        # meta.bytes is written into the document it measures, so widening the
        # number widens the file. Iterate to the fixpoint: the length is
        # non-decreasing in the digit count, so this settles in a few passes and
        # the shipped number is never a lie.
        for _ in range(8):
            serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False)
            payload_bytes = serialized.encode('utf-8')
            if len(payload_bytes) == meta['bytes']:
                break
            meta['bytes'] = len(payload_bytes)
        else:
            raise RuntimeError('byte-count fixpoint did not settle')

        check_vocabulary(payload, serialized)

        size = len(payload_bytes)
        if size > max_bytes:
            sections, worst = offenders(payload, members)
            detail = ['REFUSED: %d bytes exceeds --max-bytes %d. Nothing written.'
                      % (size, max_bytes),
                      'sections: ' + ', '.join('%s=%d' % item for item in sections),
                      'largest members: ' + ', '.join('%s=%d' % item for item in worst)]
            glassbox(use_glassbox, 'producer', 'blocked',
                     'Staffer Network section refused: %d bytes over the %d cap' % (size, max_bytes),
                     {'bytes': size, 'max_bytes': max_bytes,
                      'largest_members': dict(worst)},
                     detail='\n'.join(detail),
                     duration_ms=(time.perf_counter() - started) * 1000.0)
            return {'ok': False, 'reason': 'size', 'bytes': size, 'max_bytes': max_bytes,
                    'sections': sections, 'largest_members': worst, 'lines': detail}

        attempts = write_atomic(out_path, payload_bytes)
        elapsed = time.perf_counter() - started
        glassbox(use_glassbox, 'producer', 'completed',
                 'Staffer Network section written: %d members, %d bytes' % (len(members), size),
                 {'generation': generation, 'members': len(members), 'bytes': size,
                  'roster_size': len(roster),
                  'members_with_any_staff': summary['members_with_any_staff'],
                  'members_with_shared_staff': summary['members_with_shared_staff'],
                  'zero_overlap_pairs_rejected': summary['zero_overlap_pairs_rejected'],
                  'rename_attempts': attempts, 'seconds': round(elapsed, 3)},
                 duration_ms=elapsed * 1000.0)
        return {'ok': True, 'bytes': size, 'path': os.path.abspath(out_path),
                'seconds': elapsed, 'payload': payload, 'rename_attempts': attempts,
                'scores': scores, 'trajectories': trajectories, 'moves': moves}
    except Exception as exc:                                   # noqa: BLE001 - reported, re-raised
        glassbox(use_glassbox, 'producer', 'failed',
                 'Staffer Network section failed: %s' % type(exc).__name__,
                 {'generation': generation, 'error': str(exc)[:800]},
                 duration_ms=(time.perf_counter() - started) * 1000.0)
        raise


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Build the Melange dashboard Staffer Network section from a verified '
                    'staffer social-network generation.')
    parser.add_argument('--generation', default=GENERATION,
                        help='generation directory name under audits/staffer_network')
    parser.add_argument('--root', default=DEFAULT_ROOT, help='project root')
    parser.add_argument('--out', default=None,
                        help='output path (default <root>/data/engine/staffer_network_section.json)')
    parser.add_argument('--max-bytes', type=int, default=6_000_000,
                        help='refuse to write a section larger than this')
    parser.add_argument('--no-glassbox', action='store_true', help='suppress glassbox events')
    parser.add_argument('--hash-edges', action='store_true',
                        help='also recompute the 1.5 GB edge file hash (not read otherwise)')
    parser.add_argument('--hon-ledger-generation', default=HON_LEDGER_GENERATION,
                        help='House personnel ledger generation under audits/staffer_network, '
                             'used to name member offices whose bioguide is off the roster')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    root = os.path.abspath(args.root)
    out_path = args.out or os.path.join(root, 'data', 'engine', 'staffer_network_section.json')
    # Clean skip, added 2026-09-04 when this became a chain step (fec/run_pipeline.py, right
    # after make_lobby_interactions_html.py). ABSENCE of the generation is not a contradiction:
    # a fresh or data-isolated tree simply has no staffer social-network generation, and the
    # chain must walk past that the way it walks past a missing social_media.db. Everything
    # else stays a loud refusal -- a generation that is PRESENT but unverifiable, mis-hashed,
    # promoted, or internally inconsistent still exits 2 through bind_inputs below. The
    # downstream surface builder (fec/surfaces/make_staffer_network_html.py) makes the matching
    # skip when this file is not written.
    generation_dir = os.path.join(root, 'audits', 'staffer_network', args.generation)
    if not os.path.isdir(generation_dir):
        print('SKIPPED: generation absent (%s)' % generation_dir)
        return 0
    try:
        result = build(root, args.generation, out_path, args.max_bytes,
                       use_glassbox=not args.no_glassbox, hash_edges=args.hash_edges,
                       hon_ledger_generation=args.hon_ledger_generation)
    except InputContradiction as exc:
        print('REFUSED: %s' % exc)
        return 2
    if not result['ok']:
        for line in result['lines']:
            print(line)
        return 3
    summary = result['payload']['global']
    print('wrote %s' % result['path'])
    print('bytes=%d members=%d roster=%d seconds=%.1f rename_attempts=%d'
          % (result['bytes'], len(result['payload']['members']),
             summary['roster_size'], result['seconds'], result['rename_attempts']))
    print('members_with_any_staff=%d members_with_shared_staff=%d zero_overlap_pairs_rejected=%d'
          % (summary['members_with_any_staff'], summary['members_with_shared_staff'],
             summary['zero_overlap_pairs_rejected']))
    office = result['payload']['meta']['office_name_source_counts']
    # Every source that leaves the cell without a name: no office at all
    # (absent_*), an office no record names (unresolved_*), and the unreachable-
    # in-practice case of a container carrying no office key (no_office_key_*).
    UNNAMED = ('unresolved', 'absent', 'no_office_key')
    named = sum(count for key, count in office.items() if not key.startswith(UNNAMED))
    unnamed = sum(count for key, count in office.items() if key.startswith(UNNAMED))
    print('office_labels named=%d unresolved_or_absent=%d %s'
          % (named, unnamed, ' '.join('%s=%d' % item for item in sorted(office.items()))))
    # The move tables, same shape. `emitted` is the map the page consults;
    # `unresolved` is the count of cells that now read the honest-absence label
    # instead of a raw container key.
    moves = result['payload']['meta']['move_office_name_source_counts']
    print('move_labels cells=%d map_entries=%d unresolved=%d %s'
          % (sum(moves.values()), len(result['payload']['meta']['move_office_names']),
             moves.get('unresolved', 0),
             ' '.join('%s=%d' % item for item in sorted(moves.items()))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
