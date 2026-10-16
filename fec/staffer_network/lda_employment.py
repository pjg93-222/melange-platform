# -*- coding: utf-8 -*-
"""Who a lobbying filer's employer is, and who their clients are.

PAUL'S RULING, 2026-09-07, verbatim and the whole of it:

    "For the filer, you do what is chronologically the most recent. If it
     appears they are contract/mercenary they are tied to all those
     organizations as clients."

This module is the ONE place that ruling is turned into code.  Three consumers
read it -- the static Organisations payload (fec/surfaces/build_staffer_groups_
section.py), the sidecar Groups index (fec/surfaces/staffer_groups_index.py) and
the plain-words status table (fec/staffer_network/lda_status_labels.py) -- so
that none of them can drift into a second, private reading of the same sentence.

THE THREE DEFINITIONS, as implemented
-------------------------------------
employer
    The registrant on the chronologically MOST RECENT filing that lists the
    person.  Chronology is the filing's own reporting period, (filing_year,
    period), not the date it was posted: the period is when the work is said to
    have happened, dt_posted is only when the paperwork arrived.  Ties inside one
    period go to the registrant with MORE filings in that period, then to the
    registrant with more filings overall, then to the lower registrant id so two
    runs never disagree.  ``Employment.tie_rule`` records which of the four
    decided it.  ONE employer per person: the person is an employee of that
    registrant and of no other.

former_employers
    Every other registrant that ever listed the person, each with the latest
    period on which it did.  These are carried for the person's own profile fold.
    They are NOT memberships: a former employer does not list the person as one
    of its people.

    ONE OF THESE IS NOT ACTUALLY FORMER, and the name says otherwise, so it is
    counted rather than left to be discovered.  Where two registrants filed in
    the SAME latest period and the tie ladder picked one, the loser is
    CONCURRENT, not older -- its ``last_filed`` equals the employer's own period.
    ``EmploymentLedger.census['concurrent_registrants_in_former_employers']``
    counts them; on the 2026-09-07 corpus it is 2 of 129 rows, because only 2 of
    25,340 filers had a tie at all.  The field keeps the name the ruling's brief
    gave it; whether those two should read differently on the page is an owner
    call, not a producer's.

contract  (Paul's word: "contract/mercenary")
    A property of the REGISTRANT, evaluated on the person's employer: the
    registrant files on behalf of clients other than itself.  in_house is the
    other case -- every filing that registrant makes names itself as the client.
    The test is on the organisations' names, because the LDA gives registrants
    and clients separate id spaces (``lda:registrant:17443`` and
    ``lda:client:207859`` can be the same company), so id equality could never
    detect a self-filing.  ``normalize_org_name`` is what "the same name" means.

    LIMIT, said here rather than left to be discovered: "or an affiliate" in the
    owner's definition of in-house is NOT implemented.  A parent registrant that
    files for a differently-named subsidiary reads as contract under this rule.
    Deciding otherwise needs an ownership graph the LDA corpus does not carry, so
    the population it would touch is COUNTED instead -- see
    ``EmploymentLedger.census['contract_filers_whose_employer_has_one_client']``,
    which is the affiliate-shaped case (a registrant with exactly one distinct
    client that is not itself).  That number is an owner call, not a producer's.

clients
    For a CONTRACT filer: every client organisation named on every filing that
    lists that person, over all periods -- "all those organizations", as ruled --
    each carrying the latest period on which it appeared.  An in-house filer has
    no client roster: their registrant's client is their registrant.

WHAT THIS MODULE DOES NOT SAY
-----------------------------
Nothing here establishes employment tenure.  D-021 stands: employment tenure is
NOT established for any of the 25,340 sealed LDA identities, and every field
below is a statement about what the filings say, in the filings' own periods.
"On file", not "worked there".

THE JOIN, and why it is the one it is
-------------------------------------
    lobbyist --NAMED_ON_ACTIVITY--> activity --(filing_uuid)--> filing
    filing --> (registrant, client, filing_year, filing_period)

``lda_covered_position_assertions`` also links a person to an activity and is a
tenth of the size, but it is NOT the general link: it exists only where a filing
declared a covered position, which is 9,484 of the 25,340 lobbyists (measured
2026-09-07; the corpus receipt's own ``lobbyists_with_covered_position`` says the
same number).  ``NAMED_ON_ACTIVITY`` in ``lda_industry_edges`` reaches all 25,340.
Using the cheaper file would have silently dropped 15,856 filers.

All activities inside one filing_uuid carry the same registrant, client, year and
period -- verified over all 1,037,547 activity rows, zero conflicts -- so the
filing, not the activity, is the unit this module keys on.
"""
from __future__ import annotations

import gzip
import json
import os
import re

SCHEMA = 'staffer-network-lda-employment-v1'
RULING = ('For the filer, you do what is chronologically the most recent. If it appears they are '
          'contract/mercenary they are tied to all those organizations as clients.')
RULING_DATE = '2026-09-07'

CONTRACT = 'contract'
IN_HOUSE = 'in_house'

#: How a filing period orders against the others.  Ranks are "period ending", so
#: a pre-2008 semiannual mid_year ties with a second_quarter and year_end ties
#: with a fourth_quarter -- correct, and unreachable in this corpus, which runs
#: 2021..2026 and carries only the four quarters.  An unknown period is refused
#: rather than sorted arbitrarily: guessing where a period falls would silently
#: move somebody's employer.
PERIOD_RANK = {'first_quarter': 1, 'second_quarter': 2, 'mid_year': 2,
               'third_quarter': 3, 'fourth_quarter': 4, 'year_end': 4}
PERIOD_SUFFIX = {1: 'Q1', 2: 'Q2', 3: 'Q3', 4: 'Q4'}
PERIODS_PER_YEAR = 8

ACTIVITY_PREFIX = 'lda:activity:'
NAMED_ON_ACTIVITY = 'NAMED_ON_ACTIVITY'

TIE_SINGLE = 'single_registrant_in_latest_period'
TIE_BY_PERIOD_FILINGS = 'more_filings_in_latest_period'
TIE_BY_TOTAL_FILINGS = 'more_filings_overall'
TIE_BY_ID = 'lowest_registrant_id'

EMPLOYER_RULE = (
    'A filer\'s employer is the registrant on the most recent filing period that lists them. '
    'Where two registrants filed in that same period the one with more filings in it is the '
    'employer; earlier registrants are kept as former employers on the person\'s own line and '
    'do not list the person as one of their people.')
CONTRACT_RULE = (
    'A filer is contract when their employer files on behalf of clients other than itself, and '
    'in-house when every filing that registrant makes names itself as the client. The test '
    'compares organisation names, because registrants and clients have separate LDA ids. A '
    'registrant that files for a differently-named affiliate reads as contract here; that case '
    'is counted, not guessed at.')
CLIENT_RULE = (
    'A contract filer is tied to every client organisation named on every filing that lists '
    'them, over all periods, each shown with the latest period it appeared on. An in-house '
    'filer has no client roster: their registrant is its own client.')
NO_TENURE_NOTE = (
    'These are statements about what the filings say, in the filings\' own periods. No '
    'employment tenure is established for any of these identities.')

#: The NORMALIZED clients artifact (2026-09-07, second ruling on the same
#: sentence).  The client half of this ruling reaches 41,203 client
#: organisations and 132,738 filer-client ties.  Written as cards -- a name and
#: a roster per client -- that measured 21,012,744 bytes against the 5,000,000
#: pin, because every filer's name was repeated once per client they file for
#: and every client's name once per card.  The producer's answer had been to
#: raise a minimum-representing-filers threshold until it fit, which shipped 203
#: of the 41,203; the ruling says every one of them, so the artifact is
#: NORMALIZED instead: each name is written exactly once and the ties are
#: integer indices into those tables.
#:
#: Keys are one or two characters because the tie arrays alone carry 132,738
#: numbers and a legend costs bytes once rather than 41,203 times.  This dict IS
#: the legend; the producer copies it into meta.key_legend so the artifact
#: explains itself, and this module reads the artifact back through the same
#: names, so a writer and a reader cannot drift apart.
CLIENTS_SCHEMA = 'staffer-clients-section-v1'
CLIENTS_FILENAME = 'staffer_clients_section.json'
CLIENTS_KEY_LEGEND = {
    'c': 'client organisation ids, sorted, indexed by position',
    'cn': 'client organisation display names, one per client, parallel to c',
    'e': 'the registrant ids that employ these contract filers, sorted, indexed by position',
    'en': 'those registrants\' display names, parallel to e',
    'f': 'contract filer person ids, sorted, indexed by position',
    'fn': 'contract filer display names, parallel to f',
    'fe': 'each filer\'s employer, as an index into e, parallel to f',
    't': 'one array per client, parallel to c: the indices into f of the filers tied to it',
    'tp': 'one array per client, parallel to t, one entry per tie: the index into '
          'meta.period_labels of the latest filing period that tie appeared on',
}
#: What meta.clients_mode says in the ORGANISATIONS artifact once the client half
#: has moved out of it.  A reader that finds this value knows the client ties are
#: complete and live in CLIENTS_FILENAME, rather than that they were thresholded.
CLIENTS_ELSEWHERE = 'separate_artifact'


class EmploymentContradiction(Exception):
    """The corpus cannot be read the way this ruling needs it read."""


_CORPORATE_SUFFIX = re.compile(
    r'\b(inc|incorporated|corp|corporation|co|company|llc|llp|lp|plc|ltd|limited|pc|pllc|'
    r'the|and)\b')


def normalize_org_name(name):
    """What "the registrant is its own client" compares.

    Case, punctuation and the usual corporate-form words are dropped, so
    "MICROSOFT CORPORATION" and "Microsoft Corp." are the same organisation and
    "MICROSOFT" and "MICROSOFT MOBILITY" are not.  This is name normalisation,
    not affiliate inference -- see the module docstring.
    """
    text = re.sub(r'[^a-z0-9 ]+', ' ', str(name or '').lower())
    return ' '.join(_CORPORATE_SUFFIX.sub(' ', text).split())


def period_key(filing_year, filing_period):
    """One sortable integer for (year, period). Refuses an unknown period."""
    rank = PERIOD_RANK.get(str(filing_period or ''))
    if rank is None:
        raise EmploymentContradiction(
            'unknown LDA filing_period %r; this module refuses to guess where it falls in time'
            % (filing_period,))
    return int(filing_year) * PERIODS_PER_YEAR + rank


def period_label(key):
    """"2026 Q2" -- the period a key stands for, in the words a reader uses."""
    if not key:
        return ''
    return '%d %s' % (key // PERIODS_PER_YEAR, PERIOD_SUFFIX[key % PERIODS_PER_YEAR])


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def read_organisations(path):
    """org id -> display name, and org id -> kind, from the organisation assertions."""
    names, kinds = {}, {}
    for row in rows(path):
        ident = row.get('source_org_id')
        if not ident:
            continue
        names[ident] = row.get('display_name') or ''
        kinds[ident] = row.get('organization_kind') or ''
    return names, kinds


class Filings(object):
    """filing_uuid -> (registrant, client, period key), interned to small ints.

    One packed int per filing rather than three parallel structures: the corpus
    holds 475,613 filings and this table is the producer's memory high-water
    mark, so the shape is chosen for size.  ``registrant`` and ``client`` are
    indexes into ``registrant_ids`` and ``client_ids``.
    """

    __slots__ = ('table', 'registrant_ids', 'client_ids', '_registrants', '_clients',
                 'activity_rows', 'conflicts')

    # 15 bits of period key (max 4095*8+4), 18 bits of client index (262,143) and
    # the rest for the registrant.  The corpus measured 2026-09-07 uses 16,212 /
    # 44,797 / 6,817 of those, but a silent overflow would move somebody's
    # employer with nothing on the page saying so, so ``add`` checks the bounds.
    _CLIENT_SHIFT = 15
    _REGISTRANT_SHIFT = 33
    _MAX_KEY = (1 << 15) - 1
    _MAX_CLIENT = (1 << 18) - 1

    def __init__(self):
        self.table = {}
        self.registrant_ids, self.client_ids = [], []
        self._registrants, self._clients = {}, {}
        self.activity_rows = 0
        self.conflicts = 0

    def _intern(self, value, index, ids):
        slot = index.get(value)
        if slot is None:
            slot = index[value] = len(ids)
            ids.append(value)
        return slot

    def add(self, filing_uuid, registrant, client, key):
        client_slot = self._intern(client, self._clients, self.client_ids)
        if key > self._MAX_KEY or client_slot > self._MAX_CLIENT:
            raise EmploymentContradiction(
                'the filing table\'s packing no longer fits this corpus (period key %d, client '
                'slot %d); widen Filings before trusting another employer' % (key, client_slot))
        packed = ((self._intern(registrant, self._registrants, self.registrant_ids)
                   << self._REGISTRANT_SHIFT)
                  | (client_slot << self._CLIENT_SHIFT)
                  | key)
        seen = self.table.get(filing_uuid)
        if seen is None:
            self.table[filing_uuid] = packed
        elif seen != packed:
            self.conflicts += 1

    def get(self, filing_uuid):
        packed = self.table.get(filing_uuid)
        if packed is None:
            return None
        return (packed >> self._REGISTRANT_SHIFT,
                (packed >> self._CLIENT_SHIFT) & ((1 << 18) - 1),
                packed & ((1 << self._CLIENT_SHIFT) - 1))

    def __len__(self):
        return len(self.table)


def read_filings(activity_path):
    """Stream the activity assertions into the filing table.

    A filing's activities are its issue areas; they all carry the same
    registrant, client, year and period.  ``Filings.conflicts`` counts any that
    do not, and ``build_ledger`` refuses on a non-zero count rather than picking
    a winner.
    """
    filings = Filings()
    for row in rows(activity_path):
        filings.activity_rows += 1
        uuid = row.get('filing_uuid')
        registrant = row.get('registrant_source_org_id')
        client = row.get('client_source_org_id')
        if not uuid or not registrant or not client:
            raise EmploymentContradiction(
                'an activity assertion carries no filing_uuid, registrant or client: %r'
                % (row.get('source_activity_id'),))
        filings.add(uuid, registrant, client, period_key(row.get('filing_year'),
                                                         row.get('filing_period')))
    if not filings.table:
        raise EmploymentContradiction('the activity assertions carry no filings')
    return filings


def filing_uuid_of(activity_id):
    """``lda:activity:<uuid>:<ordinal>`` -> ``<uuid>``."""
    text = str(activity_id or '')
    if not text.startswith(ACTIVITY_PREFIX) or ':' not in text[len(ACTIVITY_PREFIX):]:
        raise EmploymentContradiction('not an LDA activity id: %r' % (activity_id,))
    return text[len(ACTIVITY_PREFIX):text.rindex(':')]


def read_filer_links(edges_path, filings):
    """Stream NAMED_ON_ACTIVITY into per-person filing facts.

    Per person: the latest period seen, per-registrant (latest period, filings in
    that latest period, filings overall), and per-client the latest period.
    Filings are counted once per person however many issue-area edges name them.
    Everything is folded as it streams, so memory is O(people x their own
    filings), never O(edges).
    """
    people = {}
    edge_rows = named = unmatched = 0
    with gzip.open(edges_path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            edge_rows += 1
            # A cheap substring gate before json.loads: NAMED_ON_ACTIVITY is
            # 2,741,465 of the 4,816,559 edges and the other two predicates
            # (FILED_BY, FOR_CLIENT) are already carried by the filing table.
            if NAMED_ON_ACTIVITY not in line:
                continue
            row = json.loads(line)
            if row.get('predicate') != NAMED_ON_ACTIVITY:
                continue
            named += 1
            filing_uuid = filing_uuid_of(row.get('object_id'))
            found = filings.get(filing_uuid)
            if found is None:
                unmatched += 1
                continue
            registrant, client, key = found
            person = row.get('subject_id')
            state = people.get(person)
            if state is None:
                state = people[person] = {'latest': 0, 'registrants': {}, 'clients': {},
                                          'seen': set()}
            # One edge per (person, activity) and a filing has one activity per issue
            # area: count each filing once per person, as the ruling says "filings".
            # ponytail: holds the person's filing uuids; fine at corpus scale.
            if filing_uuid in state['seen']:
                continue
            state['seen'].add(filing_uuid)
            if key > state['latest']:
                state['latest'] = key
            seen = state['registrants'].get(registrant)
            if seen is None:
                state['registrants'][registrant] = [key, 1, 1]
            elif key > seen[0]:
                seen[0], seen[1], seen[2] = key, 1, seen[2] + 1
            elif key == seen[0]:
                seen[1] += 1
                seen[2] += 1
            else:
                seen[2] += 1
            if key > state['clients'].get(client, 0):
                state['clients'][client] = key
    return people, {'edge_rows': edge_rows, 'named_on_activity': named,
                    'unmatched_activities': unmatched}


def registrant_engagement(filings, organisation_names):
    """registrant index -> contract | in_house, plus its distinct client count and
    its count of clients whose name differs from its own.

    A registrant is in-house only when EVERY client it ever named normalises to
    its own name.  One differently-named client makes it contract, which is the
    ruled test.
    """
    clients_of = {}
    for packed in filings.table.values():
        registrant = packed >> Filings._REGISTRANT_SHIFT
        client = (packed >> Filings._CLIENT_SHIFT) & ((1 << 18) - 1)
        clients_of.setdefault(registrant, set()).add(client)
    engagement, client_counts, other_counts = {}, {}, {}
    for registrant, clients in clients_of.items():
        own = normalize_org_name(organisation_names.get(filings.registrant_ids[registrant], ''))
        others = sum(1 for client in clients
                     if normalize_org_name(
                         organisation_names.get(filings.client_ids[client], '')) != own)
        engagement[registrant] = CONTRACT if others else IN_HOUSE
        client_counts[registrant] = len(clients)
        other_counts[registrant] = others
    return engagement, client_counts, other_counts


def employer_of(state, registrant_ids):
    """Apply the ruling to one person's filing facts.

    Returns ``(registrant_index, latest_period_key, formers, tie_rule)`` where
    ``formers`` is ``[(registrant_index, latest_period_key), ...]`` sorted most
    recent first.
    """
    latest = state['latest']
    candidates = [(registrant, facts) for registrant, facts in state['registrants'].items()
                  if facts[0] == latest]
    if not candidates:
        raise EmploymentContradiction('a filer has filings but no registrant in its own latest '
                                      'period; the filing table and the edges disagree')
    if len(candidates) == 1:
        tie_rule = TIE_SINGLE
    else:
        top_period = max(facts[1] for _r, facts in candidates)
        leaders = [r for r, facts in candidates if facts[1] == top_period]
        if len(leaders) == 1:
            tie_rule = TIE_BY_PERIOD_FILINGS
        else:
            top_total = max(facts[2] for r, facts in candidates if r in leaders)
            if sum(1 for r, facts in candidates if r in leaders and facts[2] == top_total) == 1:
                tie_rule = TIE_BY_TOTAL_FILINGS
            else:
                tie_rule = TIE_BY_ID
    chosen = min(candidates, key=lambda rf: (-rf[1][1], -rf[1][2], registrant_ids[rf[0]]))[0]
    formers = sorted(((registrant, facts[0])
                      for registrant, facts in state['registrants'].items()
                      if registrant != chosen),
                     key=lambda rk: (-rk[1], registrant_ids[rk[0]]))
    return chosen, latest, formers, tie_rule


class EmploymentLedger(object):
    """The ruling, applied to every filer in the corpus.

    ``employer``/``formers``/``clients`` are keyed by ``lda:lobbyist:<n>`` and
    carry LDA organisation ids, so a consumer never has to know about the
    interning above.
    """

    __slots__ = ('employer', 'employer_as_of', 'formers', 'clients', 'engagement',
                 'registrant_engagement', 'registrant_client_count', 'organisation_names',
                 'registrant_ids', 'client_ids', 'census', 'provenance')

    def __init__(self):
        self.employer, self.employer_as_of, self.formers, self.clients = {}, {}, {}, {}
        self.engagement = {}
        self.registrant_engagement, self.registrant_client_count = {}, {}
        self.organisation_names = {}
        self.registrant_ids, self.client_ids = [], []
        self.census, self.provenance = {}, {}

    def is_contract(self, person):
        return self.engagement.get(person) == CONTRACT

    def employees_by_registrant(self):
        """registrant id -> [person ids]. One membership per person, by the ruling."""
        grouped = {}
        for person, registrant in self.employer.items():
            grouped.setdefault(registrant, []).append(person)
        return grouped

    def filers_by_client(self):
        """client id -> [(person id, latest period key)], contract filers only."""
        grouped = {}
        for person, clients in self.clients.items():
            if not self.is_contract(person):
                continue
            for client, key in clients.items():
                grouped.setdefault(client, []).append((person, key))
        return grouped


def build_ledger(activity_path, edges_path, organisation_path):
    """Read the three corpus files and apply the ruling to every filer."""
    organisation_names, organisation_kinds = read_organisations(organisation_path)
    filings = read_filings(activity_path)
    if filings.conflicts:
        raise EmploymentContradiction(
            '%d activity rows disagree with their own filing about the registrant, client or '
            'period; this module will not pick a winner' % filings.conflicts)
    engagement, client_counts, other_counts = registrant_engagement(filings, organisation_names)
    people, edge_counts = read_filer_links(edges_path, filings)
    if edge_counts['unmatched_activities']:
        raise EmploymentContradiction(
            '%d NAMED_ON_ACTIVITY edges point at an activity whose filing is not in the activity '
            'assertions' % edge_counts['unmatched_activities'])

    ledger = EmploymentLedger()
    ledger.organisation_names = organisation_names
    ledger.registrant_ids = filings.registrant_ids
    ledger.client_ids = filings.client_ids
    ledger.registrant_engagement = {filings.registrant_ids[index]: value
                                    for index, value in engagement.items()}
    ledger.registrant_client_count = {filings.registrant_ids[index]: value
                                      for index, value in client_counts.items()}

    tie_rules, engagement_census = {}, {CONTRACT: 0, IN_HOUSE: 0}
    former_rows = affiliate_shaped = concurrent_rows = 0
    client_pairs = 0
    for person, state in people.items():
        chosen, latest, formers, tie_rule = employer_of(state, filings.registrant_ids)
        registrant_id = filings.registrant_ids[chosen]
        ledger.employer[person] = registrant_id
        ledger.employer_as_of[person] = latest
        ledger.formers[person] = [(filings.registrant_ids[index], key) for index, key in formers]
        former_rows += len(formers)
        concurrent_rows += sum(1 for _index, key in formers if key == latest)
        tie_rules[tie_rule] = tie_rules.get(tie_rule, 0) + 1
        kind = engagement[chosen]
        ledger.engagement[person] = kind
        engagement_census[kind] += 1
        if kind == CONTRACT:
            ledger.clients[person] = {filings.client_ids[index]: key
                                      for index, key in state['clients'].items()}
            client_pairs += len(state['clients'])
            if other_counts.get(chosen) == 1:
                affiliate_shaped += 1
        else:
            ledger.clients[person] = {}

    clients_per_filer = sorted(len(v) for person, v in ledger.clients.items()
                               if ledger.is_contract(person))
    ledger.census = {
        'filers': len(people),
        'contract': engagement_census[CONTRACT],
        'in_house': engagement_census[IN_HOUSE],
        'tie_rules': dict(sorted(tie_rules.items())),
        'former_employer_rows': former_rows,
        'filers_with_a_former_employer': sum(1 for v in ledger.formers.values() if v),
        # Rows in former_employers whose last filing period is the SAME as the
        # employer's: a tie loser, concurrent rather than older. See the module
        # docstring's former_employers entry.
        'concurrent_registrants_in_former_employers': concurrent_rows,
        'contract_filer_client_pairs': client_pairs,
        'clients_per_contract_filer': _distribution(clients_per_filer),
        'contract_filers_whose_employer_has_one_client': affiliate_shaped,
        'registrants': len(filings.registrant_ids),
        'registrants_contract': sum(1 for v in engagement.values() if v == CONTRACT),
        'registrants_in_house': sum(1 for v in engagement.values() if v == IN_HOUSE),
        'client_organisations': len(filings.client_ids),
        'filings': len(filings),
        'activity_rows': filings.activity_rows,
        'organisation_kinds': _counted(organisation_kinds.values()),
    }
    ledger.census.update(edge_counts)
    ledger.provenance = {'schema': SCHEMA, 'ruling': RULING, 'ruling_date': RULING_DATE,
                         'employer_rule': EMPLOYER_RULE, 'contract_rule': CONTRACT_RULE,
                         'client_rule': CLIENT_RULE, 'no_tenure_note': NO_TENURE_NOTE}
    return ledger


def _counted(values):
    counted = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return dict(sorted(counted.items()))


def _distribution(sorted_values):
    if not sorted_values:
        return {'n': 0, 'median': 0, 'p90': 0, 'max': 0, 'mean': 0.0, 'total': 0}
    def at(fraction):
        return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * fraction))]
    return {'n': len(sorted_values), 'median': at(0.5), 'p90': at(0.9),
            'max': sorted_values[-1], 'total': sum(sorted_values),
            'mean': round(sum(sorted_values) / float(len(sorted_values)), 2)}


# ---------------------------------------------------------------- consumers

def load_from_clients_artifact(path):
    """The NORMALIZED client half, read back and re-expanded per person.

    The inverse of what the producer writes: index tables in, ``{person id:
    {client id: {name, latest_period}}}`` out, which is the shape the rest of
    this module and the 8841 Groups index already speak.  Expansion is one pass
    over the tie arrays -- 132,738 entries -- and costs about a tenth of a second,
    against the minute the corpus join costs.

    Returns ``None`` when the file is absent or is not a clients document, so a
    caller can fall back rather than fail.  A tie index that does not resolve is
    an ``EmploymentContradiction``, NOT a skipped row: a dangling index means the
    tables and the ties disagree about who is in them, and silently dropping the
    tie would under-report a client's roster with nothing saying so.
    """
    try:
        with open(path, encoding='utf-8') as stream:
            payload = json.load(stream)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get('c'), list):
        return None
    clients = payload.get('c') or []
    client_names = payload.get('cn') or []
    filers = payload.get('f') or []
    filer_names = payload.get('fn') or []
    employers = payload.get('e') or []
    employer_names = payload.get('en') or []
    filer_employer = payload.get('fe') or []
    ties = payload.get('t') or []
    tie_periods = payload.get('tp') or []
    meta = payload.get('meta') or {}
    period_labels = meta.get('period_labels') or []

    if not (len(client_names) == len(ties) == len(clients)):
        raise EmploymentContradiction(
            'the clients artifact has %d client ids, %d client names and %d tie rows; the three '
            'are parallel arrays and must be the same length'
            % (len(clients), len(client_names), len(ties)))
    if len(tie_periods) != len(ties):
        raise EmploymentContradiction(
            'the clients artifact has %d tie rows and %d tie-period rows; tp is parallel to t, '
            'and a short tp would silently blank a client roster\'s latest periods'
            % (len(ties), len(tie_periods)))
    if len(filer_names) != len(filers) or len(filer_employer) != len(filers):
        raise EmploymentContradiction(
            'the clients artifact has %d filer ids, %d filer names and %d employer indices'
            % (len(filers), len(filer_names), len(filer_employer)))

    people, tie_rows = {}, 0
    for position, client in enumerate(clients):
        name = client_names[position]
        periods = tie_periods[position] if position < len(tie_periods) else ()
        for offset, index in enumerate(ties[position]):
            if not isinstance(index, int) or index < 0 or index >= len(filers):
                raise EmploymentContradiction(
                    'client %s carries tie index %r, which is not one of the %d filers in this '
                    'artifact' % (client, index, len(filers)))
            person = filers[index]
            record = people.setdefault(person, {'employer': None, 'clients': {},
                                                'engagement': None, 'as_of': ''})
            code = periods[offset] if offset < len(periods) else None
            label = (period_labels[code] if isinstance(code, int) and 0 <= code < len(period_labels)
                     else '')
            record['clients'][client] = {'name': name, 'latest_period': label}
            tie_rows += 1
    for index, person in enumerate(filers):
        record = people.setdefault(person, {'employer': None, 'clients': {},
                                            'engagement': None, 'as_of': ''})
        record['display_name'] = filer_names[index]
        record['engagement'] = CONTRACT
        slot = filer_employer[index]
        if not isinstance(slot, int) or slot < 0 or slot >= len(employers):
            raise EmploymentContradiction(
                'filer %s carries employer index %r, which is not one of the %d employers in '
                'this artifact' % (person, slot, len(employers)))
        record['employer'] = employers[slot]
        if slot < len(employer_names):
            record['employer_name'] = employer_names[slot]
        record['client_count'] = len(record['clients'])
    return {'people': people, 'clients': len(clients), 'filers': len(filers), 'ties': tie_rows,
            'meta': {key: meta.get(key) for key in
                     ('schema_id', 'generation', 'built_at', 'bytes', 'counts')}}


def load_from_organisations_artifact(path, clients_path=None):
    """The ruling as it was actually SHIPPED, read back from the static payload.

    The 8841 sidecar cannot re-run the corpus join on a web request -- it streams
    540 MB and takes about a minute -- so it reads the artifact the producer
    already wrote.  Returns ``{lda_source_person_id: {...}}`` with the same keys
    ``build_ledger`` produces per person, restricted to the people and clients
    that survived the artifact's own byte caps; ``truncated`` says so.

    TWO SHAPES, and this reads both.  Until 2026-09-07 the client ties were CARDS
    in this same document, and a byte cap had thresholded them down to 203 of
    41,203; they are now a normalized sibling artifact carrying all of them.
    ``clients_path`` names that sibling (default: CLIENTS_FILENAME beside this
    file).  The old client-card branch is kept because an organisations artifact
    written before the split is still a valid document on disk, and reading it as
    "no clients at all" would be a silent regression rather than a refusal.

    Returns ``None`` when the artifact is absent or is not an organisations
    document, so a caller can fall back to today's behaviour rather than fail.
    """
    try:
        with open(path, encoding='utf-8') as stream:
            payload = json.load(stream)
    except (OSError, ValueError):
        return None
    organisations = payload.get('organisations')
    if not isinstance(organisations, list):
        return None
    people = {}
    for organisation in organisations:
        if not isinstance(organisation, dict):
            continue
        kind = organisation.get('kind')
        if kind == 'registrant':
            for entry in organisation.get('lobbyists') or ():
                person = people.setdefault(entry.get('id'), {'employer': None, 'clients': {},
                                                             'engagement': None, 'as_of': ''})
                person['employer'] = organisation.get('id')
                person['employer_name'] = organisation.get('name')
                # Contract-or-in-house is a property of the REGISTRANT, so the
                # artifact states it once on the card and not on every person.
                # Reading it off the person row instead -- which an earlier draft
                # of this loader did -- silently produced engagement=None for all
                # 15,873 filers, and the only symptom was a blank line on a fold.
                person['engagement'] = organisation.get('engagement')
                person['as_of'] = entry.get('employer_as_of') or ''
                person['client_count'] = entry.get('client_count')
        elif kind == 'client':
            for entry in organisation.get('represented_by') or ():
                person = people.setdefault(entry.get('id'), {'employer': None, 'clients': {},
                                                             'engagement': None, 'as_of': ''})
                person['clients'][organisation.get('id')] = {
                    'name': organisation.get('name'),
                    'latest_period': entry.get('latest_period') or ''}
    people.pop(None, None)
    meta = payload.get('meta') or {}
    counts = meta.get('counts') or {}
    mode = meta.get('clients_mode')

    # The client half, from the normalized sibling.  Merged INTO the people this
    # document already named rather than replacing them: the employer line comes
    # from a registrant card here, the client ties come from there, and a contract
    # filer whose registrant card was capped away still gets their clients.
    clients_read = None
    if mode == CLIENTS_ELSEWHERE:
        if clients_path is None:
            clients_path = os.path.join(os.path.dirname(os.path.abspath(path)), CLIENTS_FILENAME)
        clients_read = load_from_clients_artifact(clients_path)
        for person, record in ((clients_read or {}).get('people') or {}).items():
            merged = people.setdefault(person, {'employer': None, 'clients': {},
                                                'engagement': None, 'as_of': ''})
            merged['clients'] = record['clients']
            merged['client_count'] = record.get('client_count')
            merged.setdefault('engagement', None)
            # A registrant card is the authority on the employer line, because it
            # is the card the person is listed on.  These fill in only where the
            # org cap kept that card from naming them.
            for key in ('employer', 'employer_name', 'display_name'):
                if not merged.get(key) and record.get(key):
                    merged[key] = record[key]
            if not merged.get('engagement'):
                merged['engagement'] = record.get('engagement')

    # A truncated read is the normal case, not a fault: the artifact ships under a
    # byte cap, so a person below an org cap is simply not in it.  The flag exists
    # so a consumer says "not listed here" rather than "no employer".
    #
    # The CLIENT half is no longer one of the reasons: since the split it is
    # complete or it is missing, and missing is what the flag reports.
    if mode == CLIENTS_ELSEWHERE:
        client_truncation = clients_read is None
    else:
        client_truncation = bool(counts.get('client_filers_not_named')
                                 or counts.get('client_organisations_dropped')
                                 or mode != 'all_clients')
    truncated = bool(counts.get('employees_not_named') or client_truncation)
    return {'people': people, 'truncated': truncated,
            'clients_artifact': (clients_read or {}).get('meta') if clients_read else None,
            'client_ties': (clients_read or {}).get('ties'),
            'client_organisations': (clients_read or {}).get('clients'),
            'meta': {key: meta.get(key) for key in
                     ('schema_id', 'generation', 'clients_mode', 'org_cap', 'client_roster_cap',
                      'client_min_representing_filers', 'built_at')}}
