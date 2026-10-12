#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify collector-captured cosponsor movement, then wire it into the bills ledger.

Paul 2026-08-29: "Wire the cosponsor joins into the ledger, it needs to
self-update ... anything the collectors ingest needs to be wired directly to the
ledger, it will go through a verification process first."

Until now ``bill_repository_live.json`` (the Congress.gov / GovInfo BILLSTATUS
cache refreshed weekly by ``update_bill_repository.py``) was DISPLAY-ONLY: the
member desk embedded it as ``bundle['bill_live']`` for the workbench template
and no computation ever read it.  A member who joined a tracked bill as a
cosponsor after the workbook was last hand-maintained therefore never earned
ledger credit, and the primacy factor / tier board never moved.

This producer is the verification gate between the collector and the ledger.
It reads local files ONLY (no network -- the chain's network-free law holds),
diffs the live rosters against the assembled ledger view carried by the previous
``member_desk_bundle.json``, and publishes an ADDITIVE sidecar that
``make_member_desk.py`` merges as a fourth membership source.

Contract
--------
* FAIL-CLOSED.  A missing refresh receipt, a receipt that is not a complete
  ``PASS``, or a repository whose bytes are not hash-bound to that receipt is a
  hard refusal: the process exits non-zero and writes NO sidecar, leaving the
  last verified sidecar in place (last-good semantics, as with the repository
  itself).  Every refusal condition here is a strict SUBSET of the conditions
  ``congress_bill_repository.validate_live_repository`` already enforces at
  ``make_member_desk`` import time, so this step can never halt a chain that
  would otherwise have completed -- it only halts it EARLIER, before
  ``compute_pillars.py --write`` mutates anything.
* ADDITIVE ONLY.  A join that the ledger lacks is auto-accepted.  Nothing is
  ever modified or removed: role conflicts, withdrawals on rows the ledger
  already carries, ledger rows the live roster no longer shows, sponsor
  discrepancies, and bills outside the ledger universe are QUEUED for
  adjudication and never applied.
* ADJUDICATED WITHDRAWALS DRAIN THE QUEUE (2026-08-29).  A withdrawn join is
  the one question this producer could never stop asking: suppressing the
  ledger row only moved it from ``withdrawn_on_existing_ledger_row`` to
  ``withdrawn_join_absent_from_ledger``.  Once a (member, bill, congress) pair
  is recorded in ``data/legislation/membership_withdrawals.csv`` -- the file
  make_member_desk uses to drop the row -- both reasons stop being queued for
  that pair.  The converse is fail-loud: a rule whose member the LIVE roster no
  longer shows as withdrawn is queued as ``withdrawal_adjudication_stale`` and
  that member's row is NOT accepted, so the sidecar can never emit a row the
  desk would then silently suppress.
* LEGACY VERIFICATION IS COSPONSOR-ONLY. The enabled official-movement and
  sentinel-admission policies can additionally emit verified sponsor facts.
  The desk's validated cumulative-history path projects those accepted roles
  only when the sponsor still matches the current official repository. The
  historical delta merge keeps its original/cosponsor-only default.
* STRICT BIOGUIDE.  Members resolve by bioguide against the bundle roster and
  nothing else.  An unresolvable bioguide is skipped and logged; it is never
  fuzzy-matched to a name.
* CLOSED UNIVERSE.  A row is only ever emitted for an act key the ledger
  ALREADY carries.  This is what protects ``primacy_factors.main``'s hard
  universe-sync assertion (fec/engine/primacy_factors.py:466) -- collector data
  can move a member's credit but can never introduce an act key that
  ``bill_beneficiary_tags.ndjson`` has not classified.
* SEED-FINGERPRINT INVARIANT.  ``validate_live_repository`` binds the refresh
  receipt to ``seeds_from_member_bundle(bundle)``, and BOTH ``make_member_desk``
  (:179) and ``audit_full`` (:1311) hard-fail when that fingerprint moves.  The
  seed dict for a bill takes title/sponsor/summary/action from the FIRST bundle
  row that mentions it, so a newly added member could silently become that
  first row.  Accepted rows therefore inherit those four fields verbatim from
  the seed the fingerprint is already computed over, and inherit areas as a
  subset of the seed's union -- which makes the fingerprint provably invariant
  under this sidecar.  A row whose summary would not survive the desk's
  re-truncation unchanged is queued instead of accepted.
* DETERMINISTIC.  Sidecars are regenerated whole (never appended) from sorted
  rows, and every field is a function of the inputs -- ``verified_at`` is the
  repository refresh's own ``completed_at``, not wall clock -- so two runs over
  the same inputs produce byte-identical files.  Wall clock lives in the
  receipt's ``generated_utc`` alone.

Run:
  python fec/legislation/verify_collector_roster_updates.py
  python fec/legislation/verify_collector_roster_updates.py --print-accepted
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import bill_rules as BR
from legislation.congress_bill_repository import (
    REFRESH_RECEIPT_SCHEMA,
    RepositoryError,
    parse_bill_identity,
    seed_fingerprint,
    seeds_from_member_bundle,
    sha256_file,
    validate_repository,
)
from moneyball_core import write_text_atomic
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)

UPDATE_SCHEMA = 'collector-roster-update-v1'
PENDING_SCHEMA = 'collector-roster-pending-v1'
RECEIPT_SCHEMA = 'collector-roster-updates-receipt-v1'
SOURCE = 'congress.gov BILLSTATUS via bill_repository_live'

UPDATES_REL = os.path.join('data', 'members', 'collector_roster_updates.ndjson')
PENDING_REL = os.path.join('data', 'members', 'collector_roster_pending.ndjson')
RECEIPT_REL = os.path.join(
    'data', 'engine', 'collector_roster_updates_receipt.json'
)
BUNDLE_REL = 'member_desk_bundle.json'
REPOSITORY_REL = 'bill_repository_live.json'
REFRESH_RECEIPT_REL = os.path.join(
    'data', 'engine', 'congress_bill_refresh_receipt.json'
)
OVERRIDES_REL = os.path.join('data', 'legislation', 'bill_overrides.csv')
# Adjudicated per-member withdrawals (2026-08-29).  make_member_desk removes the
# matching ledger rows; this producer stops re-queuing the questions those rows
# were asking, so an adjudication actually DRAINS the pending queue instead of
# merely changing which reason it is filed under.
WITHDRAWALS_REL = os.path.join(
    'data', 'legislation', 'membership_withdrawals.csv'
)

# Default roles for the legacy sidecar merge. Only the validated cumulative
# caller can additionally supply current, exact official sponsor identities.
ACCEPTABLE_ROLES = ('original', 'cosponsor')

# make_member_desk.py:1536 / :1612 truncate a ledger summary at this width.
SUMMARY_WIDTH = 220
SUMMARY_ELLIPSIS = '…'


class VerificationRefused(RuntimeError):
    """The collector feed did not clear the publication gate."""


# --------------------------------------------------------------------------
# shared pure helpers (imported by make_member_desk.py -- keep side-effect free)
# --------------------------------------------------------------------------

def desk_summary(value: object) -> str:
    """Reproduce the member desk's ledger summary truncation exactly."""
    text = str(value or '')
    return text[:SUMMARY_WIDTH] + (
        SUMMARY_ELLIPSIS if len(text) > SUMMARY_WIDTH else ''
    )


def ledger_act_key(bill: object, congress: object) -> tuple[str, str]:
    """The (bill, congress) identity make_member_desk collapses rows on."""
    return (str(bill or ''), str(congress or ''))


def index_withdrawals(rows):
    """Index adjudicated withdrawals by act key for O(1) lookup.

    BIOGUIDE-STRICT, matching this module's resolution law: a rule that names a
    bioguide is matched on that bioguide ALONE.  Only a rule that carries no
    bioguide at all falls back to the roster name, so a wrong-but-plausible
    name can never silently claim a member a bioguide already identifies.
    """
    index = {}
    for row in rows or ():
        bucket = index.setdefault(row['key'], {'bioguide': {}, 'nameonly': {}})
        bioguide = str(row.get('bioguide') or '').strip().upper()
        if bioguide:
            bucket['bioguide'][bioguide] = row
        else:
            bucket['nameonly'][
                str(row.get('member') or '').strip().casefold()] = row
    return index


def adjudicated_withdrawal(index, act, bioguide, name):
    """Return the adjudicated withdrawal for this person on this act, or None."""
    if not index:
        return None
    bucket = index.get(BR.withdrawal_act_key(act[0], act[1]))
    if not bucket:
        return None
    found = bucket['bioguide'].get(str(bioguide or '').strip().upper())
    if found is not None:
        return found
    return bucket['nameonly'].get(str(name or '').strip().casefold())


def merge_collector_roster_updates(path, bills, resolve, exclude=None, log=print,
                                   *, verified_sponsors=None):
    """Merge the verified collector sidecar into the desk's pre-collapse ledger.

    ``bills`` maps roster name -> list of ledger rows, exactly as
    ``make_member_desk.load_press_and_bills`` builds it, and MUST already carry
    the workbook, manual-evidence and adjudicated-primacy rows: the ledger
    universe is read off it.  ``resolve(name, bioguide)`` is the desk's member
    resolver.  ``exclude(bill, congress, title, summary)`` re-applies the desk's
    bill-rules and override filters. Returns an audit dict; mutates ``bills``.
    ``verified_sponsors`` is an optional set of (repository_key, bioguide) pairs
    from the current validated official repository, excluding withdrawn people.
    Only the receipt-validated cumulative-history caller supplies it. A sponsor
    must match one of those exact identities and resolve by Bioguide alone;
    default/delta callers retain the original/cosponsor-only role boundary.

    Defensive by design.  Unlike the adjudicated primacy sidecars (which raise
    on a bad row because a human wrote them), every malformed or out-of-universe
    collector row is SKIPPED with a warning.  A collector artifact must never be
    able to crash the desk, and -- critically -- must never be able to widen the
    act universe past ``bill_beneficiary_tags.ndjson``, which would crash
    ``primacy_factors.main`` instead.

    RECONCILIATION CONTRACT.  ``dashboard_integrity.postflight`` requires every
    declared legislation source row to be displayed or carry one explicit
    exclusion reason.  ``appended`` counts LEDGER ROWS (one per area, which is
    what the collapse sees) and ``skipped`` counts lines that never reached the
    ledger, so ``appended + skipped`` is the exact source-row count to declare
    and ``skipped`` is its exclusion term.  This holds for multi-area rows,
    which a per-line source count would under-report.
    """
    audit = {
        'collector_roster_lines': 0,
        'collector_roster_merged': 0,
        'collector_roster_appended': 0,
        'collector_roster_skipped': 0,
        'collector_roster_skipped_unknown_act': 0,
        'collector_roster_skipped_unknown_member': 0,
        'collector_roster_skipped_withdrawn': 0,
        'collector_roster_skipped_role': 0,
        'collector_roster_skipped_excluded': 0,
        'collector_roster_skipped_malformed': 0,
    }
    if not os.path.exists(path):
        return audit
    known = {
        ledger_act_key(row.get('bill'), row.get('congress'))
        for rows in bills.values()
        for row in rows
    }
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            audit['collector_roster_lines'] += 1
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            if not isinstance(record, dict):
                audit['collector_roster_skipped_malformed'] += 1
                continue
            role = str(record.get('role') or '').strip().lower()
            sponsor_verified = (
                role == 'sponsor' and bool(record.get('bioguide'))
                and (record.get('repository_key'), record.get('bioguide'))
                in (verified_sponsors or ()))
            if role not in ACCEPTABLE_ROLES and not sponsor_verified:
                # Sponsors require the cumulative caller's current official match.
                audit['collector_roster_skipped_role'] += 1
                continue
            if record.get('withdrawn'):
                audit['collector_roster_skipped_withdrawn'] += 1
                continue
            key = ledger_act_key(record.get('bill'), record.get('congress'))
            if key not in known:
                # DEFENSE IN DEPTH: the verifier already refuses to emit these.
                audit['collector_roster_skipped_unknown_act'] += 1
                continue
            if exclude is not None and exclude(
                key[0], key[1], record.get('title'), record.get('summary')
            ):
                audit['collector_roster_skipped_excluded'] += 1
                continue
            name = resolve(None if role == 'sponsor' else record.get('member'),
                           record.get('bioguide'))
            if not name:
                audit['collector_roster_skipped_unknown_member'] += 1
                continue
            areas = record.get('areas')
            if not isinstance(areas, list):
                areas = []
            for area in areas or ['']:
                bills.setdefault(name, []).append({
                    'role': role,
                    # areas are inherited verbatim from the ledger by the
                    # verifier and are already canonical -- re-canonicalising
                    # here could only perturb the seed fingerprint.
                    'area': str(area or ''),
                    'bill': key[0],
                    'title': record.get('title') or '',
                    'sponsor': record.get('sponsor') or '',
                    'ncos': record.get('ncos') or 0,
                    'congress': key[1],
                    'action': record.get('action') or '',
                    'summary': desk_summary(record.get('summary')),
                    'url': record.get('url') or '',
                })
                audit['collector_roster_appended'] += 1
            audit['collector_roster_merged'] += 1
    audit['collector_roster_skipped'] = (
        audit['collector_roster_skipped_unknown_act']
        + audit['collector_roster_skipped_unknown_member']
        + audit['collector_roster_skipped_withdrawn']
        + audit['collector_roster_skipped_role']
        + audit['collector_roster_skipped_excluded']
        + audit['collector_roster_skipped_malformed']
    )
    if audit['collector_roster_skipped']:
        log(
            '  !! collector roster sidecar: %d/%d line(s) skipped '
            '(unknown act %d, unknown member %d, withdrawn %d, role %d, '
            'excluded %d, malformed %d)'
            % (
                audit['collector_roster_skipped'],
                audit['collector_roster_lines'],
                audit['collector_roster_skipped_unknown_act'],
                audit['collector_roster_skipped_unknown_member'],
                audit['collector_roster_skipped_withdrawn'],
                audit['collector_roster_skipped_role'],
                audit['collector_roster_skipped_excluded'],
                audit['collector_roster_skipped_malformed'],
            )
        )
    return audit


# --------------------------------------------------------------------------
# publication gate
# --------------------------------------------------------------------------

def _read_json_object(path: Path, label: str) -> dict:
    if not path.is_file():
        raise VerificationRefused('%s is missing: %s' % (label, path))
    try:
        with path.open(encoding='utf-8') as stream:
            value = json.load(stream)
    except ValueError as exc:
        raise VerificationRefused(
            '%s is not readable JSON: %s' % (label, exc)
        ) from exc
    if not isinstance(value, dict):
        raise VerificationRefused('%s must be a JSON object' % label)
    return value


def gate_repository(
    bundle_path: Path,
    repository_path: Path,
    refresh_receipt_path: Path,
) -> tuple[dict, dict, dict]:
    """Return (bundle, repository, refresh_receipt) or refuse.

    The refusal set is deliberately a SUBSET of validate_live_repository's:
    receipt presence/schema, a complete PASS, and byte-level hash binding of the
    repository to that receipt.  Freshness and seed-universe binding are
    RECORDED rather than gated -- a cosponsor join is an additive historical
    fact that an older-but-authentic scrape still reports truthfully, and the
    display consumer already enforces the eight-day window at its own door.
    Gating them here would convert a benign condition (a new act adjudicated
    into the ledger mid-cycle) into a chain-halting failure.
    """
    bundle = _read_json_object(bundle_path, 'member desk bundle')
    if not isinstance(bundle.get('members'), list):
        raise VerificationRefused('member desk bundle has no members list')
    receipt = _read_json_object(
        refresh_receipt_path, 'bill refresh receipt'
    )
    if receipt.get('schema') != REFRESH_RECEIPT_SCHEMA:
        raise VerificationRefused('bill refresh receipt schema mismatch')
    if receipt.get('status') != 'PASS' or int(receipt.get('failed') or 0):
        raise VerificationRefused('bill refresh receipt is not a complete PASS')
    if not repository_path.is_file():
        raise VerificationRefused(
            'bill repository is missing: %s' % repository_path
        )
    bound = receipt.get('repository') or {}
    if not isinstance(bound, dict):
        raise VerificationRefused('bill refresh receipt repository block is invalid')
    if (
        bound.get('sha256') != sha256_file(repository_path)
        or int(bound.get('size') or -1) != repository_path.stat().st_size
    ):
        raise VerificationRefused(
            'bill repository is not hash-bound to its refresh receipt'
        )
    repository = _read_json_object(repository_path, 'bill repository')
    try:
        validate_repository(repository)
    except RepositoryError as exc:
        raise VerificationRefused('bill repository is invalid: %s' % exc) from exc
    return bundle, repository, receipt


# --------------------------------------------------------------------------
# ledger view
# --------------------------------------------------------------------------

def build_ledger_view(bundle: dict) -> dict:
    """Index the assembled ledger carried by the previous member desk bundle."""
    roster: dict[str, str] = {}
    ambiguous_bioguides: set[str] = set()
    for member in bundle.get('members') or []:
        if not isinstance(member, dict):
            continue
        name = str(member.get('name') or '').strip()
        bioguide = str(((member.get('bio') or {}) or {}).get('bioguide') or '').strip()
        if not name or not bioguide:
            continue
        if bioguide in roster and roster[bioguide] != name:
            # two roster names claiming one bioguide: refuse to guess
            ambiguous_bioguides.add(bioguide)
            continue
        roster[bioguide] = name
    for bioguide in ambiguous_bioguides:
        roster.pop(bioguide, None)

    members_by_act: dict[tuple[str, str], dict[str, str]] = {}
    areas_by_act: dict[tuple[str, str], list[str]] = {}
    reference_by_act: dict[tuple[str, str], dict] = {}
    repo_key_to_acts: dict[str, set[tuple[str, str]]] = {}
    unparseable: set[tuple[str, str]] = set()
    identified: set[tuple[str, str]] = set()
    for member in bundle.get('members') or []:
        if not isinstance(member, dict):
            continue
        name = str(member.get('name') or '').strip()
        for row in member.get('bills') or []:
            if not isinstance(row, dict):
                continue
            key = ledger_act_key(row.get('bill'), row.get('congress'))
            reference_by_act.setdefault(key, row)
            if name:
                # sponsor > original > cosponsor is already collapsed per member
                members_by_act.setdefault(key, {}).setdefault(
                    name, str(row.get('role') or '').strip().lower()
                )
            bucket = areas_by_act.setdefault(key, [])
            for area in row.get('areas') or ():
                if area and area not in bucket:
                    bucket.append(area)
            if key in unparseable or key in identified:
                continue
            try:
                identity = parse_bill_identity(key[0], key[1])
            except (ValueError, KeyError):
                # amendments and unparseable identities have no repository row
                unparseable.add(key)
                continue
            identified.add(key)
            repo_key_to_acts.setdefault(identity['key'], set()).add(key)
    for bucket in areas_by_act.values():
        bucket.sort()
    seeds = {}
    for row in seeds_from_member_bundle(bundle):
        try:
            identity = parse_bill_identity(row.get('bill'), row.get('congress'))
        except (ValueError, KeyError):
            continue
        seeds[identity['key']] = row
    return {
        'roster': roster,
        'ambiguous_bioguides': sorted(ambiguous_bioguides),
        'members_by_act': members_by_act,
        'areas_by_act': areas_by_act,
        'reference_by_act': reference_by_act,
        'repo_key_to_acts': repo_key_to_acts,
        'unparseable_acts': sorted(unparseable),
        'seeds': seeds,
    }


def _live_people(bill: dict) -> list[tuple[str, dict]]:
    rows = []
    for person in bill.get('original_cosponsors') or ():
        if isinstance(person, dict):
            rows.append(('original', person))
    for person in bill.get('cosponsors') or ():
        if isinstance(person, dict):
            rows.append(('cosponsor', person))
    return rows


def verify(bundle: dict, repository: dict, refresh_receipt: dict,
           overrides: dict, repository_sha256: str,
           withdrawals: list | tuple = ()) -> dict:
    """Diff the live rosters against the ledger.  Pure: returns rows + counts.

    ``withdrawals`` is the adjudicated per-member withdrawal list from
    ``bill_rules.load_membership_withdrawals``; it defaults to empty so every
    pre-existing caller behaves exactly as before.
    """
    withdrawal_index = index_withdrawals(withdrawals)
    view = build_ledger_view(bundle)
    roster = view['roster']
    verified_at = str(refresh_receipt.get('completed_at') or '')
    accepted: list[dict] = []
    queued: list[dict] = []
    unresolvable: dict[str, int] = {}
    counts = {
        'repository_bills': 0,
        'accepted': 0,
        'queued': 0,
        'already_present': 0,
        'skipped_unresolvable': 0,
        'skipped_excluded': 0,
        'skipped_ambiguous_identity': 0,
        # adjudicated withdrawals (2026-08-29)
        'withdrawal_rules': len(withdrawals or ()),
        'withdrawal_adjudicated': 0,
        'withdrawal_adjudication_stale': 0,
    }

    def queue(reason, repo_key, act, name, bioguide, role, date, detail=''):
        queued.append({
            'schema': PENDING_SCHEMA,
            'reason': reason,
            'repository_key': repo_key,
            'bill': act[0] if act else '',
            'congress': act[1] if act else '',
            'member': name or '',
            'bioguide': bioguide or '',
            'role': role or '',
            'date': date or '',
            'detail': detail,
            'source': SOURCE,
            'verified_at': verified_at,
            'repository_sha256': repository_sha256,
        })

    for repo_key in sorted((repository.get('bills') or {})):
        bill = (repository.get('bills') or {})[repo_key]
        if not isinstance(bill, dict):
            continue
        counts['repository_bills'] += 1
        acts = view['repo_key_to_acts'].get(repo_key) or set()
        if not acts:
            # Currently unreachable: the repository is seeded FROM the bundle.
            # Kept so a future seeding source cannot silently widen the universe.
            queue(
                'bill_not_in_ledger_universe', repo_key, None, '', '', '', '',
                detail=str(bill.get('display') or ''),
            )
            continue
        if len(acts) > 1:
            counts['skipped_ambiguous_identity'] += 1
            queue(
                'ambiguous_ledger_identity', repo_key, None, '', '', '', '',
                detail='; '.join('%s|%s' % act for act in sorted(acts)),
            )
            continue
        act = next(iter(acts))
        reference = view['reference_by_act'].get(act) or {}
        if BR.excluded_bill(
            act[0], act[1], reference.get('title'), reference.get('summary')
        ):
            counts['skipped_excluded'] += 1
            continue
        override = BR.override_for(overrides, act[0], act[1])
        if override and not BR.override_included(override):
            counts['skipped_excluded'] += 1
            continue
        seed = view['seeds'].get(repo_key) or {}
        areas = list(view['areas_by_act'].get(act) or [])
        ledger_members = view['members_by_act'].get(act) or {}

        live_names: set[str] = set()
        sponsor = bill.get('sponsor') if isinstance(bill.get('sponsor'), dict) else {}
        sponsor_bioguide = str(sponsor.get('bioguide') or '').strip()
        sponsor_name = roster.get(sponsor_bioguide) if sponsor_bioguide else None
        if sponsor_name:
            live_names.add(sponsor_name)
            existing = ledger_members.get(sponsor_name)
            if existing is None:
                queue(
                    'sponsor_absent_from_ledger', repo_key, act, sponsor_name,
                    sponsor_bioguide, 'sponsor', str(sponsor.get('date') or ''),
                )
            elif existing != 'sponsor':
                queue(
                    'role_conflict', repo_key, act, sponsor_name,
                    sponsor_bioguide, 'sponsor', str(sponsor.get('date') or ''),
                    detail='ledger role %s' % existing,
                )

        # One decision per person per bill.  Seeding this with the sponsor keeps
        # "never auto-accept a sponsor" airtight even if a malformed feed also
        # listed them as a cosponsor, and stops a duplicated roster entry from
        # emitting the same accepted row twice.
        handled = {sponsor_bioguide} if sponsor_bioguide else set()
        for live_role, person in _live_people(bill):
            bioguide = str(person.get('bioguide') or '').strip()
            if bioguide and bioguide in handled:
                continue
            if bioguide:
                handled.add(bioguide)
            name = roster.get(bioguide) if bioguide else None
            if not name:
                counts['skipped_unresolvable'] += 1
                label = bioguide or ('name:%s' % str(person.get('name') or ''))
                unresolvable[label] = unresolvable.get(label, 0) + 1
                continue
            live_names.add(name)
            date = str(person.get('date') or '')
            withdrawn = bool(str(person.get('withdrawn_date') or '').strip())
            existing = ledger_members.get(name)
            adjudged = adjudicated_withdrawal(
                withdrawal_index, act, bioguide, name)
            if adjudged is not None and not withdrawn:
                # STALE ADJUDICATION.  A rule says this membership was withdrawn
                # but the live roster no longer carries a withdrawal date -- the
                # member re-joined, or the rule is simply wrong.  Either way the
                # desk's withdrawal pass would suppress a membership Congress.gov
                # currently affirms, so this is escalated and the row is NOT
                # accepted: accepting it would emit a sidecar row that the desk
                # then silently drops.
                counts['withdrawal_adjudication_stale'] += 1
                queue(
                    'withdrawal_adjudication_stale', repo_key, act, name,
                    bioguide, live_role, date,
                    detail='adjudicated withdrawn %s; live roster shows no '
                           'withdrawal (ledger role %s)'
                    % (str(adjudged.get('withdrawn_date') or ''),
                       existing if existing is not None else 'absent'),
                )
                continue
            if existing is not None:
                if withdrawn:
                    if adjudged is not None:
                        # Adjudicated: the desk's withdrawal pass removes this
                        # row on its next build.  Asking again would keep a
                        # decided question in the queue forever.
                        counts['withdrawal_adjudicated'] += 1
                        continue
                    queue(
                        'withdrawn_on_existing_ledger_row', repo_key, act, name,
                        bioguide, live_role, date,
                        detail='withdrawn %s; ledger role %s'
                        % (str(person.get('withdrawn_date') or ''), existing),
                    )
                elif existing != live_role:
                    queue(
                        'role_conflict', repo_key, act, name, bioguide,
                        live_role, date, detail='ledger role %s' % existing,
                    )
                else:
                    counts['already_present'] += 1
                continue
            if withdrawn:
                if adjudged is not None:
                    # The terminal, CORRECT state for a withdrawn join: absent
                    # from the ledger and adjudicated.  Without this the queue
                    # could never drain -- suppressing the ledger row only moves
                    # the question from 'withdrawn_on_existing_ledger_row' to
                    # 'withdrawn_join_absent_from_ledger'.
                    counts['withdrawal_adjudicated'] += 1
                    continue
                queue(
                    'withdrawn_join_absent_from_ledger', repo_key, act, name,
                    bioguide, live_role, date,
                    detail='withdrawn %s' % str(person.get('withdrawn_date') or ''),
                )
                continue
            summary = str(seed.get('summary') or '')
            if desk_summary(summary) != summary:
                # Accepting would perturb seeds_from_member_bundle and break the
                # receipt hash binding both the desk and audit_full depend on.
                queue(
                    'summary_truncation_not_idempotent', repo_key, act, name,
                    bioguide, live_role, date,
                )
                continue
            accepted.append({
                'schema': UPDATE_SCHEMA,
                'bill': act[0],
                'congress': act[1],
                'repository_key': repo_key,
                'member': name,
                'bioguide': bioguide,
                'role': live_role,
                'join_date': date,
                'withdrawn': False,
                'areas': areas,
                # Fingerprint-bearing fields: verbatim from the seed dict that
                # seed_fingerprint is already computed over.
                'title': str(seed.get('title') or ''),
                'sponsor': str(seed.get('sponsor') or ''),
                'summary': summary,
                'action': str(seed.get('action') or ''),
                # Display-only, not fingerprint-bearing.
                'ncos': reference.get('ncos') or 0,
                'url': str(reference.get('url') or ''),
                'source': SOURCE,
                'verified_at': verified_at,
                'repository_sha256': repository_sha256,
            })

        for name, existing_role in sorted(ledger_members.items()):
            if name not in live_names:
                queue(
                    'ledger_row_absent_from_live_roster', repo_key, act, name,
                    '', existing_role, '',
                )

    accepted.sort(
        key=lambda row: (
            row['repository_key'], row['member'], row['role'], row['bioguide']
        )
    )
    queued.sort(
        key=lambda row: (
            row['reason'], row['repository_key'], row['member'], row['role']
        )
    )
    counts['accepted'] = len(accepted)
    counts['queued'] = len(queued)
    return {
        'accepted': accepted,
        'queued': queued,
        'counts': counts,
        'unresolvable': unresolvable,
        'view': view,
    }


def _ndjson(rows: list[dict]) -> str:
    return ''.join(
        json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n'
        for row in rows
    )


def run(root: Path, *, quiet: bool = False, refuse_absorbed: bool = False) -> dict:
    from legislation import sentinel_admissions as SA
    admissions = SA.load(root)
    bundle_path = root / BUNDLE_REL
    repository_path = root / REPOSITORY_REL
    refresh_receipt_path = root / REFRESH_RECEIPT_REL
    bundle, repository, refresh_receipt = gate_repository(
        bundle_path, repository_path, refresh_receipt_path
    )
    repository_sha256 = sha256_file(repository_path)
    overrides = BR.load_overrides(str(root / OVERRIDES_REL))
    try:
        withdrawals = BR.load_membership_withdrawals(
            str(root / WITHDRAWALS_REL)
        )
    except ValueError as exc:
        # FAIL-CLOSED, same shape as every other refusal here: no sidecar is
        # written and the last verified one stands.  A malformed adjudication
        # must never be silently ignored -- ignoring it means the desk keeps a
        # membership the adjudication said to remove.
        raise VerificationRefused(
            'membership withdrawal adjudications are malformed: %s' % exc
        ) from exc
    result = verify(
        bundle, repository, refresh_receipt, overrides, repository_sha256,
        withdrawals=withdrawals,
    )
    from legislation import official_membership_movements as OM
    movement_document = None
    if OM.enabled(root):
        from legislation.congress_bill_repository import validate_live_repository
        validate_live_repository(bundle_path,repository_path,refresh_receipt_path)
        movement_document = OM.build(SA.augment_bundle(bundle,admissions), repository,
                                     refresh_receipt,repository_sha256,overrides)
        result['accepted'] = movement_document['additions']
        resolved={(r['repository_key'],r['bioguide']) for r in movement_document['facts']}
        result['queued']=[r for r in result['queued'] if (r['repository_key'],r['bioguide']) not in resolved]
        result['counts']['accepted']=len(result['accepted'])
        result['counts']['queued']=len(result['queued'])
    # Official sponsor/cosponsor facts for newly classified measures have their
    # own complete admission authority. They do not rely on the legacy closed-
    # universe cosponsor-only rule or a fabricated institutional endorsement.
    admitted = []
    roster = build_ledger_view(bundle)['roster']
    for row in admissions['memberships']:
        if row.get('withdrawn'):
            continue
        if roster.get(row['bioguide']) != row['member']:
            raise VerificationRefused('admission roster identity has changed')
        key = row['repository_key']
        official = repository['bills'].get(key)
        if official is None:
            raise VerificationRefused('admitted measure was not refreshed into the repository: ' + key)
        people = ([official['sponsor']] if row['role']=='sponsor' else
                  official['original_cosponsors'] if row['role']=='original' else official['cosponsors'])
        if not any(p.get('bioguide')==row['bioguide'] and not p.get('withdrawn_date') for p in people):
            raise VerificationRefused('admitted membership differs from official repository: ' + key)
        admitted.append({**row,'repository_sha256':repository_sha256})
    if admitted:
        keys = {(r['repository_key'],r['bioguide'],r['role']) for r in admitted}
        result['accepted'] = [r for r in result['accepted'] if (r['repository_key'],r['bioguide'],r['role']) not in keys] + admitted
        result['queued'] = [r for r in result['queued'] if r['repository_key'] not in admissions['bills']]
        result['counts']['accepted'] = len(result['accepted'])
        result['counts']['queued'] = len(result['queued'])
    view = result['view']
    counts = dict(result['counts'])
    counts['skipped_unparseable_identity'] = len(view['unparseable_acts'])
    counts['ledger_acts'] = len(view['reference_by_act'])
    counts['roster_members'] = len(view['roster'])

    updates_path = root / UPDATES_REL
    pending_path = root / PENDING_REL
    receipt_path = root / RECEIPT_REL

    # DE-016.  make_member_desk.py:1720 merges THIS producer's sidecar into the
    # ledger it writes back as member_desk_bundle.json (:2032 / :2496) -- which is
    # this producer's own next-cycle input.  Every row accepted last run is
    # therefore 'already_present' on the next read, and because the sidecar is
    # regenerated WHOLE (never appended) the delta collapses; a standalone re-run
    # straight after a completed chain collapsed 202 accepted rows to 3.
    # This block does not prevent the collapse -- the next CHAIN cycle reads the
    # same post-desk bundle and collapses identically -- it makes it visible and,
    # with --refuse-absorbed-rerun, refusable before anything is overwritten.
    previous_receipt = {}
    if receipt_path.is_file():
        try:
            previous_receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous_receipt = {}
    absorbed: list[dict] = []
    if (isinstance(previous_receipt, dict)
            and previous_receipt.get('schema') == RECEIPT_SCHEMA):
        still_accepted = {
            (row['bill'], row['congress'], row['member'], row['role'])
            for row in result['accepted']
        }
        for row in previous_receipt.get('accepted') or []:
            identity = (
                str(row.get('bill') or ''), str(row.get('congress') or ''),
                str(row.get('member') or ''), str(row.get('role') or ''),
            )
            if identity in still_accepted:
                continue
            if identity[2] in (view['members_by_act'].get(identity[:2]) or {}):
                absorbed.append({
                    'bill': identity[0], 'congress': identity[1],
                    'member': identity[2], 'role': identity[3],
                })
    counts['previously_accepted_absorbed_by_bundle'] = len(absorbed)
    if absorbed and refuse_absorbed:
        raise VerificationRefused(
            '%d row(s) accepted by the previous run are now carried by the bundle; '
            'rewriting the sidecar here would publish a collapsed delta and the '
            'desk would drop them on its next rebuild' % len(absorbed)
        )
    if absorbed and not quiet:
        print(
            '  !! %d previously accepted row(s) were absorbed into the bundle and '
            'are NOT re-emitted; the desk drops them on its next rebuild'
            % len(absorbed)
        )
    for target in (updates_path, pending_path, receipt_path):
        target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(_ndjson(result['accepted']), str(updates_path))
    write_text_atomic(_ndjson(result['queued']), str(pending_path))
    if movement_document is not None:
        movement_path=root/OM.RELATIVE
        movement_path.parent.mkdir(parents=True,exist_ok=True)
        write_text_atomic(json.dumps(movement_document,indent=2)+'\n',str(movement_path))

    try:
        seeds_now = seed_fingerprint(bundle)
    except Exception:
        seeds_now = {'count': -1, 'sha256': ''}
    receipt_seeds = refresh_receipt.get('seeds') or {}
    document = {
        'schema': RECEIPT_SCHEMA,
        'status': 'PASS',
        'generated_utc': datetime.now(timezone.utc)
        .isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'counts': counts,
        'repository': {
            'path': str(repository_path),
            'sha256': repository_sha256,
            'size': repository_path.stat().st_size,
            'updated_at': str(repository.get('updated_at') or ''),
        },
        'refresh_receipt': {
            'path': str(refresh_receipt_path),
            'schema': refresh_receipt.get('schema'),
            'status': refresh_receipt.get('status'),
            'completed_at': refresh_receipt.get('completed_at'),
            'failed': int(refresh_receipt.get('failed') or 0),
        },
        'bundle': {
            'path': str(bundle_path),
            'sha256': sha256_file(bundle_path),
            'size': bundle_path.stat().st_size,
            'modified_at': datetime.fromtimestamp(
                bundle_path.stat().st_mtime, timezone.utc
            ).isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'members': len(bundle.get('members') or []),
            # Observational, NOT a gate: see gate_repository's docstring.
            'seed_universe_bound': bool(
                seeds_now.get('sha256')
                and seeds_now.get('sha256') == str(receipt_seeds.get('sha256') or '')
                and int(seeds_now.get('count') or -1)
                == int(receipt_seeds.get('count') or -2)
            ),
        },
        # FULL accepted list: every future rank or tier move sourced from the
        # collectors is traceable to exactly one line of this receipt.
        'accepted': [
            {
                'member': row['member'],
                'bioguide': row['bioguide'],
                'bill': row['bill'],
                'congress': row['congress'],
                'role': row['role'],
                'date': row['join_date'],
            }
            for row in result['accepted']
        ],
        'queued': [
            {
                'reason': row['reason'],
                'member': row['member'],
                'bill': row['bill'],
                'congress': row['congress'],
                'role': row['role'],
            }
            for row in result['queued']
        ],
        'unresolvable_bioguides': [
            {'bioguide': key, 'occurrences': value}
            for key, value in sorted(result['unresolvable'].items())
        ],
        'ambiguous_bioguides': view['ambiguous_bioguides'],
        # DE-016 evidence: rows this producer accepted last run that the bundle
        # now carries, so they are not re-emitted into the regenerated sidecar.
        'previous_delta_absorbed_by_bundle': absorbed,
        'outputs': {
            'updates': str(updates_path),
            'pending': str(pending_path),
        },
        'withdrawal_adjudications': [
            {
                'bill': row['bill'], 'congress': row['congress'],
                'member': row['member'], 'bioguide': row['bioguide'],
                'withdrawn_date': row['withdrawn_date'],
            }
            for row in withdrawals
        ],
        'contract': {
            'additive_only': movement_document is None,
            'sponsor_role_never_auto_accepted': movement_document is None and not admitted,
            'act_universe_closed_to_the_existing_ledger': True,
            'bioguide_resolution_is_strict': True,
            'sidecar_regenerated_whole_each_run': True,
            'official_evidence_admissions': len(admitted),
        },
    }
    write_text_atomic(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
        str(receipt_path),
    )
    if not quiet:
        print(
            'collector roster verification: %d accepted, %d queued, '
            '%d already present, %d unresolvable, %d excluded '
            '(%d repository bills over %d ledger acts)'
            % (
                counts['accepted'], counts['queued'], counts['already_present'],
                counts['skipped_unresolvable'], counts['skipped_excluded'],
                counts['repository_bills'], counts['ledger_acts'],
            )
        )
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            'Verify collector-captured cosponsor movement and publish the '
            'additive ledger sidecar.'
        )
    )
    result.add_argument('--root', type=Path, default=ROOT)
    result.add_argument('--quiet', action='store_true')
    result.add_argument(
        '--print-accepted',
        action='store_true',
        help='Echo every accepted (member, bill, role, date) tuple.',
    )
    result.add_argument(
        '--refuse-absorbed-rerun',
        action='store_true',
        help=('Refuse instead of publishing when the bundle already carries rows '
              'this producer accepted last run (the DE-016 signature of a '
              'standalone re-run after a completed chain).'),
    )
    return result


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    args = parser().parse_args(argv)
    try:
        document = run(Path(args.root).resolve(), quiet=args.quiet,
                       refuse_absorbed=args.refuse_absorbed_rerun)
    except VerificationRefused as exc:
        print('REFUSED: %s' % exc)
        print(
            'no collector sidecar was written; the previously verified '
            'sidecar (if any) stands unchanged'
        )
        return 1
    if args.print_accepted:
        for row in document['accepted']:
            print(
                '  accept %-28s %-14s %-9s %s'
                % (row['member'], row['bill'], row['role'], row['date'])
            )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
