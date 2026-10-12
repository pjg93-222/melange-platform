# -*- coding: utf-8 -*-
"""DESK_PRESS_SOURCE (N1a, 2026-09-19; packet audits/laundry_list_closeout_20260918 A1).

THE DEFECT.  The ranking engine reads press through :mod:`press_stream` -- the historical
Draft Systems tone file plus ``data/stance/press_refresh_tone.csv`` -- while the member desk
read workbook sheet 'Press Release Articles'.  No script in the chain writes that sheet; it
arrives from the external R pipeline, so the desk showed a frozen corpus.  Measured
(receipts laneA/receipts/r9_engine_only_press.out.txt, fixN1/n1_desk_press_union.out.txt):
291 engine rows had no sheet row, 221 of them SCORING.  Press that moved a member's rank was
invisible on that member's desk.

THE UNION.  Under ``'engine'`` the desk walks every sheet row exactly as before, then walks
the engine rows whose release identity the sheet has no row for.  The row SET is the union
of both sources; the SHEET supplies every row it has.

WHY THE SHEET WINS THE OVERLAP.  That precedence is measured, not cautious.  On the 2,376
keys both sources carry, the sheet and the CSV disagree on:
  * ``date``          2,138 keys -- the base CSV stores 'M/D/YYYY', which the desk's own
                      ``fdate()`` blanks, while the sheet stores a real datetime;
  * ``score_graded``  1,207 keys -- and that value IS the displayed graded label;
  * ``stance_src``      493 keys -- the sheet flattens 'human_label' to 'debate';
  * ``stance`` / ``stance_sign``  57 keys -- e.g. sheet 'oppose' vs CSV 'support'.
Taking the CSV on the overlap would blank dates, move graded labels, and RELABEL 57 press
stances.  Press classification is the owner's domain and is never a producer's to move, so
for every row the desk already shows, the sheet's bytes are what renders.  The engine only
ADDS.  Nothing the desk shows today disappears -- including the 3 sheet-only releases and
every ``billstatus:`` pseudo-row, which keep their existing handling because they are still
sheet rows walked by the same loop.

WHAT THIS MODULE IS NOT.  It builds no desk row and applies no desk rule.  It returns rows
in the SHEET'S OWN TUPLE SHAPE, so ``make_member_desk``'s ``pv(r, k)`` accessor and every
rule below it -- billstatus routing, the ``stance_src == 'bill'`` sign branch, the
``press_stance_overrides`` correction, ``PDATE`` date recovery, ``norm_cats``,
``graded_label`` -- run over an added row exactly as they run over a sheet row.  Display
only: no engine input, rank input, ratified constant or stance label is computed here.

FIELD MAP.  The sheet header and the base tone CSV header are the SAME 28 names in the same
order (probe receipt fixN1/n1_field_map.out.txt); the refresh CSV adds ``source_run`` and
``source``, which the desk never reads.  So every one of the 13 columns the desk displays has
a CSV counterpart and none has to be borrowed from the sheet.  The one shape difference is
``date``: the sheet stores a datetime, the base CSV stores 'M/D/YYYY' and the refresh CSV
stores 'YYYY-MM-DD'.  ``engine_date`` puts a US date into the shape the sheet presents, which
is the shape ``fdate()`` reads; anything else (including 'NA') is passed through untouched so
``fdate()`` blanks it and the ``PDATE`` recovery still applies.

Default ``'engine'`` = the desk reads the engine's stream.  ``DESK_PRESS_SOURCE=sheet``
restores the pre-2026-09-19 path: the sheet iterable is handed straight back and the engine
stream is never read.
"""
from __future__ import annotations

import os
import re

import press_stream as PS

# Read once at module scope, and fail closed: a dial that silently changes what the desk
# SHOWS must never fall through to a default because it was misspelled (the
# DESK_MEMBERSHIP_SOURCE pattern, make_member_desk.py ~232).
DESK_PRESS_SOURCE = (os.environ.get('DESK_PRESS_SOURCE') or 'engine').strip().lower()
if DESK_PRESS_SOURCE not in ('engine', 'sheet'):
    raise ValueError(
        "DESK_PRESS_SOURCE must be 'engine' (the ranking engine's press stream, unioned "
        "with the workbook sheet) or 'sheet' (the legacy workbook-only path); got %r"
        % os.environ.get('DESK_PRESS_SOURCE'))

PRESS_SOURCE_LEDGER_SCHEMA = 'desk-press-source-v1'

# Exactly the columns make_member_desk.load_press_and_bills reads with pv(r, k).  Listed for
# the suite and for readers; the mapping itself copies every column the sheet header names.
DESK_PRESS_COLUMNS = ('member', 'bioguide', 'url', 'stance_src', 'score', 'stance_sign',
                      'score_graded', 'title', 'stance', 'stance_conf', 'date', 'categories',
                      'relevance')

# 'M/D/YYYY' or 'MM/DD/YYYY' -- the base tone file's only date shape besides the literal 'NA'.
_US_DATE = re.compile(r'^\s*(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})\s*$')


def engine_date(value):
    """A tone-CSV date in the shape the sheet presents it, or the value untouched.

    ``fdate()`` accepts anything whose first four characters are digits, so 'YYYY-MM-DD' and
    a datetime already work and are returned as they are.  'M/D/YYYY' would be BLANKED, so it
    is rewritten.  Everything else -- 'NA', '', a partial date -- is returned unchanged, which
    keeps ``fdate()``'s blank and the ``PDATE`` recovery that follows it.
    """
    if not isinstance(value, str):
        return value
    match = _US_DATE.match(value)
    if not match:
        return value
    return '%s-%02d-%02d' % (match.group('y'), int(match.group('m')), int(match.group('d')))


def _plain_date(value):
    """A sheet datetime or a tone-CSV date as 'YYYY-MM-DD', or '' when it cannot be read."""
    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d')
    text = str(engine_date(value) if isinstance(value, str) else (value or ''))
    return text[:10] if text[:4].isdigit() else ''


def row_key(url, bioguide, member=''):
    """The release identity the ENGINE uses, so both sides are keyed the same way.

    ``press_stream.tone_turn_key`` is (release_url_key, bioguide-or-member upper).  Reusing it
    means A5's release identity decides the match: the sheet's two URL forms of one release
    fold onto the single key the engine kept, so neither form is duplicated and neither is
    dropped.
    """
    return PS.tone_turn_key({'url': url, 'bioguide': bioguide, 'member': member})


def _engine_tuple(row, header_index, width):
    """One tone-CSV row in the sheet's tuple shape.

    Every column the sheet header names and the CSV carries is copied across; a column the
    CSV has no value for stays None, exactly as an empty sheet cell would.  Nothing is
    invented and no value is transformed except the date shape.
    """
    out = [None] * width
    for name, index in header_index.items():
        if name is None or name not in row:
            continue
        value = row[name]
        out[index] = engine_date(value) if name == 'date' else value
    return tuple(out)


def desk_press_rows(sheet_rows, header_index, source=None, load_rows=None):
    """Return ``(rows, ledger)``: the desk's press rows and the source accounting.

    ``sheet_rows``   the sheet's data rows as openpyxl yields them (tuples).
    ``header_index`` the sheet's ``{header: column index}`` map -- the desk's own ``ph``.
    ``source``       overrides the dial (the suite exercises both positions).
    ``load_rows``    overrides the engine reader (the suite injects synthetic rows).

    Under ``'sheet'`` the iterable is handed straight back, so the desk's loop walks exactly
    the object it walks today and the engine stream is never opened.
    """
    source = DESK_PRESS_SOURCE if source is None else str(source).strip().lower()
    if source not in ('engine', 'sheet'):
        raise ValueError(
            "DESK_PRESS_SOURCE must be 'engine' or 'sheet'; got %r" % (source,))
    ledger = {'schema': PRESS_SOURCE_LEDGER_SCHEMA, 'press_source': source,
              'press_engine_rows_read': 0, 'press_engine_rows_already_in_sheet': 0,
              'press_engine_rows_appended': 0, 'press_engine_rows_no_url': 0,
              'press_engine_rows_same_slug_other_day_appended': 0,
              'press_engine_dates_normalised': 0}
    if source == 'sheet':
        return sheet_rows, ledger

    rows = list(sheet_rows)
    width = (max(header_index.values()) + 1) if header_index else 0
    url_at = header_index.get('url')
    bio_at = header_index.get('bioguide')
    name_at = header_index.get('member')
    # Index the sheet by release identity.  billstatus: pseudo-rows are legislation, not
    # releases, and no engine row can ever carry one, so they are counted and skipped rather
    # than keyed -- they stay in `rows` and keep their existing routing.
    sheet_keys = set()
    sheet_days = {}       # release key -> the day of every sheet row held under it
    date_at = header_index.get('date')
    billstatus = 0
    for row in rows:
        url = '' if url_at is None else str(row[url_at] or '')
        if url.startswith('billstatus:'):
            billstatus += 1
            continue
        key = row_key(url,
                      None if bio_at is None else row[bio_at],
                      '' if name_at is None else row[name_at])
        if key[0]:
            sheet_keys.add(key)
            sheet_days.setdefault(key, set()).add(
                _plain_date(None if date_at is None else row[date_at]))
    ledger['press_sheet_rows'] = len(rows)
    # Counted on the URL prefix alone.  The desk's own
    # press_billstatus_rows_routed_to_legislation resolves the member FIRST, so a billstatus
    # row whose member the roster does not carry lands in press_unknown_member_excluded
    # instead: measured 1310 here against 1309 there (receipt fixN1/n1_desk_press_union.out.txt).
    # Both censuses are right about what they count; neither is the other's check.
    ledger['press_sheet_billstatus_rows'] = billstatus
    ledger['press_sheet_press_rows'] = len(rows) - billstatus

    # load_tone_rows() validates the refresh receipt and RAISES on a mixed or uncommitted
    # refresh output set.  The desk therefore inherits the gate the engine already has,
    # which is the point: a desk that quietly fell back to the frozen sheet is the defect.
    # DESK_PRESS_SOURCE=sheet is the documented way past it.
    tone_ledger = {}
    engine_rows = (load_rows or PS.load_tone_rows)(ledger=tone_ledger)
    engine_keys = set()
    for row in engine_rows:
        ledger['press_engine_rows_read'] += 1
        key = row_key(row.get('url'), row.get('bioguide'), row.get('member'))
        if not key[0]:
            ledger['press_engine_rows_no_url'] += 1
            continue
        engine_keys.add(key)
        # A release key only NOMINATES a match (press_stream.load_tone_rows): a headline
        # republished on another day reuses its slug, the engine keeps both releases, and the
        # frozen sheet may hold only the older one.  So an engine row is "already in the
        # sheet" only when the sheet has a row under that key for the SAME DAY -- or when
        # either side's date cannot be read, in which case the key alone must decide.
        # Measured on 2026-09-19: all 2,377 overlapping rows agree on the day, and the one
        # slug-sharing pair (Vargas, 2022-09-30 and 2023-01-30) is in the sheet twice, so this
        # appends nothing extra today; it exists so the NEXT reused slug is not hidden.
        held = sheet_days.get(key)
        day = _plain_date(row.get('date'))
        if held is not None and (not day or '' in held or day in held):
            ledger['press_engine_rows_already_in_sheet'] += 1
            continue
        if held is not None:
            ledger['press_engine_rows_same_slug_other_day_appended'] += 1
        if engine_date(row.get('date')) != row.get('date'):
            ledger['press_engine_dates_normalised'] += 1
        rows.append(_engine_tuple(row, header_index, width))
        ledger['press_engine_rows_appended'] += 1
    ledger['press_sheet_keys_absent_from_engine'] = len(sheet_keys - engine_keys)
    ledger['press_rows_walked'] = len(rows)
    ledger['press_engine_tone_dedup'] = tone_ledger
    return rows, ledger
