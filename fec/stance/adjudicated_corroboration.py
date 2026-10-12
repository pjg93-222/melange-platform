"""Authorship and delivery gates over the accepted rows used by scores."""
from collections import defaultdict
import math
import bill_rules as BR
from engine.live_legislative_pillar import advancement
from legislation.congress_bill_repository import parse_bill_identity


def from_routing(rows, roster, bills=None):
    """Only positive, selected sponsor/original evidence corroborates CU support."""
    roster = set(roster)
    bills = bills or {}
    result = defaultdict(lambda: {'sponsored': set(), 'advanced': set(),
        'direct_sponsored_bills': set(), 'advanced_direct_bills': set()})
    seen = set()
    for row in rows:
        if row.get('disposition') != 'selected':
            continue
        member, category = row['member'], row['effective_category']
        if member not in roster or not category:
            raise ValueError('Corroboration routing has an invalid member/category')
        role = BR.canonical_role(row['effective_role'])
        sign, contribution = row['sign'], float(row['contribution'])
        if type(sign) is not int or sign not in (-1, 0, 1) or not math.isfinite(contribution):
            raise ValueError('Corroboration routing has an invalid direction/contribution')
        if sign * contribution < 0 or (sign == 0 and contribution != 0):
            raise ValueError('Corroboration routing direction disagrees with contribution')
        key = (str(row['bill']), str(BR._congress(row['congress'])))
        identity = (member, key, category)
        if identity in seen:
            raise ValueError('Duplicate selected corroboration evidence: ' + str(identity))
        seen.add(identity)
        if sign <= 0 or contribution <= 0 or role not in ('sponsor', 'original-cosponsor'):
            continue
        result[member]['sponsored'].add(category)
        if role == 'sponsor':
            result[member]['direct_sponsored_bills'].add(key)
        try:
            repository_key = parse_bill_identity(*key)['key']
        except ValueError:
            continue  # A letter cannot acquire a bill-passage badge.
        source = bills.get(repository_key, {})
        advanced = advancement(row.get('tracker_status'), (source.get('latest_action') or {}).get('text'))
        if advanced is True:
            result[member]['advanced'].add(category)
            if role == 'sponsor':
                result[member]['advanced_direct_bills'].add(key)
    return dict(result)
