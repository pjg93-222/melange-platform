"""D-260: the honors lane reads the admissions file directly, so it must drop an expired source-outage carry exactly as
sentinel_admissions.load() does. Otherwise every other reader stops scoring the bill after 30 days while this one keeps it,
which is the mixed vintage D-212 forbids."""
from __future__ import annotations

from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import bill_rules as BR
from stance.build_field_action_evidence import _admissions, legislative_actions

EXPIRED, FRESH, OLD_BUT_VERIFIED = '119:hr:1', '119:hr:2', '119:hr:3'


def stamp(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def include(key, days_ago, **extra):
    return {'canonical_key': key, 'disposition': 'include', 'confidence': 'high', 'checked_at': stamp(days_ago),
            'categories': [{'category': 'Payments', 'direction': 1, 'quote': 'q' * 25, 'rationale': 'r' * 40}], **extra}


class Source:
    """Only the two reads _admissions makes."""

    def __init__(self, doc):
        self.doc = doc

    def json(self, path):
        return copy.deepcopy(self.doc)

    def digest(self, path):
        return 'ok'


def document():
    carried = {'carried_forward': 'source_unavailable', 'carried_at': stamp(1)}
    return {'schema': 'sentinel-admissions-v1', 'status': 'PASS', 'checked_at': stamp(1),
            'sources': [{'path': 'evidence', 'sha256': 'ok'}],
            'bills': {k: {'key': k, 'areas': ['Payments']} for k in (EXPIRED, FRESH, OLD_BUT_VERIFIED)},
            'decisions': {EXPIRED: include(EXPIRED, 31, **carried), FRESH: include(FRESH, 5, **carried),
                          OLD_BUT_VERIFIED: include(OLD_BUT_VERIFIED, 45)},     # a real verdict, not a carry: age is not its limit
            'memberships': []}


def test_field_action_admissions_drop_expired_carries():
    doc = _admissions(Source(document()))
    assert set(doc['bills']) == {FRESH, OLD_BUT_VERIFIED}
    assert doc['carry_expired_keys'] == [EXPIRED]
    assert EXPIRED in doc['decisions']          # the verdict stays on record; only the scored set shrinks


def test_field_action_admissions_unchanged_when_nothing_expired():
    clean = document()
    del clean['bills'][EXPIRED]
    assert set(_admissions(Source(clean))['bills']) == {FRESH, OLD_BUT_VERIFIED}


def test_field_action_admissions_still_refuse_a_failed_generation():
    bad = document()
    bad['status'] = 'FAIL'
    with pytest.raises(ValueError, match='Invalid official admissions'):
        _admissions(Source(bad))


def test_field_action_admissions_count_and_name_the_dropped_keys(capsys):
    counts = Counter()
    _admissions(Source(document()), counts)
    assert counts['admission_carry_expired'] == 1
    out = capsys.readouterr().out
    assert '!!' in out and EXPIRED in out and 'D-260' in out
    clean = document()
    del clean['bills'][EXPIRED]
    counts = Counter()
    _admissions(Source(clean), counts)
    assert 'admission_carry_expired' not in counts and '!!' not in capsys.readouterr().out


# ---- a neutral official direction needs a bill that is still admitted ------------------------------------------------

CATEGORY = BR.CATEGORIES[0]
MEASURE = '119:hr:7'


class Routing:
    """Only the reads legislative_actions makes, for one neutral-official-evidence routing row."""

    def __init__(self, row):
        self.row = row

    def json(self, path):
        return {'legislation_routing_audit.json': {'schema': 'legislation-routing-audit-v1', 'rows': [self.row]},
                'archive_leg.json': {},
                'data/legislation/official_membership_movements.json': {
                    'schema': 'official-membership-movements-v1', 'status': 'PASS', 'repository_sha256': 'ok', 'facts': []},
                }[path]

    def rows(self, path):
        return []

    def digest(self, path):
        return 'ok'


def neutral_row():
    return {'disposition': 'neutral-official-evidence', 'member': 'Alice Adams', 'effective_category': CATEGORY,
            'bill': 'H.R. 7', 'congress': 119, 'title': 'A measure', 'sign': 0, 'source_row': 'r1', 'member_source': 'roster',
            'source_role': 'sponsor', 'contribution': 0, 'decay_weight': 1}


def neutral_admissions(admitted, expired=False):
    decision = {'categories': [{'category': CATEGORY, 'direction': 0}]}
    return {'bills': {MEASURE: {'key': MEASURE}} if admitted else {}, 'decisions': {MEASURE: decision},
            'carry_expired_keys': [MEASURE] if expired else []}


ROSTER = {'Alice Adams': {'member_id': 'A000001'}}


def test_a_neutral_official_direction_is_kept_while_the_bill_is_admitted():
    actions = legislative_actions(Routing(neutral_row()), ROSTER, [CATEGORY], neutral_admissions(True), Counter())
    assert [a['measure_id'] for a in actions] == [MEASURE]


def test_a_neutral_official_row_for_a_bill_missing_from_both_lists_is_a_real_inconsistency():
    # Neither admitted nor withheld on an expired carry: the routing audit names a bill the admissions never produced.
    with pytest.raises(ValueError, match='Neutral official direction is not admitted'):
        legislative_actions(Routing(neutral_row()), ROSTER, [CATEGORY], neutral_admissions(False), Counter())


def test_a_stale_routing_audit_does_not_fail_the_build_for_a_bill_dropped_on_an_expired_carry(capsys):
    # The verdict stays on record in decisions, but SA.load drops the bill, so its neutral rows are excluded and counted.
    counts = Counter()
    actions = legislative_actions(Routing(neutral_row()), ROSTER, [CATEGORY], neutral_admissions(False, expired=True), counts)
    assert actions == [] and counts['neutral_rows_dropped_carry'] == 1
    out = capsys.readouterr().out
    assert '!!' in out and MEASURE in out and 'D-260' in out
