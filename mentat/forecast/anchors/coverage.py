"""Coverage end of a Mentat collection, declared by rule (owner ruling 2026-09-30).

None of the pinned source databases carries a verified coverage-through date (no `fec_date_evidence` table, and the console refuses to treat a
maximum observed date as coverage), so the coverage end is a convention: the LAST calendar quarter-end whose quarterly-report due date
(the 15th of the following month) fell on or before the collection date. Rows dated after the coverage end are not "observed"; they belong
to the remainder window. An industry may cut the rule back (credit unions: 2026-05-31, "June incomplete") for as long as the rule end is
the one the cut-back names.
"""
from datetime import date
import calendar

RULE_TEXT = ('Latest calendar quarter-end whose quarterly-report due date (the 15th of the following month) fell on or before the '
             'collection date; observed amounts run to that day, the remainder starts the day after')


def _quarter_end(year, quarter):
    month = quarter * 3
    return date(year, month, calendar.monthrange(year, month)[1])


def _due(end):
    year, month = (end.year + 1, 1) if end.month == 12 else (end.year, end.month + 1)
    return date(year, month, 15)


def rule_coverage_end(collection):
    """Latest quarter-end Q with due(Q) <= collection (an ISO string or a date). None when no quarter qualifies."""
    collected = date.fromisoformat(collection) if isinstance(collection, str) else collection
    best = None
    for year in (collected.year - 1, collected.year):
        for quarter in (1, 2, 3, 4):
            end = _quarter_end(year, quarter)
            if _due(end) <= collected and (best is None or end > best):
                best = end
    return best


def coverage(collection, cut_back=None):
    """{'collection_date', 'rule_coverage_end', 'coverage_end', 'reason'} for a collection date and an optional registry cut-back
    {'when_rule_coverage_end', 'coverage_end', 'reason'}."""
    rule_end = rule_coverage_end(collection)
    if rule_end is None:
        raise ValueError('No quarter-end report deadline precedes the collection date ' + str(collection))
    end, reason = rule_end.isoformat(), None
    if cut_back and cut_back['when_rule_coverage_end'] == end:
        end, reason = cut_back['coverage_end'], cut_back['reason']
    return {'collection_date': str(collection), 'rule_coverage_end': rule_end.isoformat(), 'coverage_end': end, 'reason': reason}
