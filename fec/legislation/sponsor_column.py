# -*- coding: utf-8 -*-
"""Read the bill archive's bill-level ``Sponsor`` column into member rows.

congress_bills_archive_*.xlsx carries a bill-level ``Sponsor`` on every row of 'All Bills'
(10,181 of 10,181 in the 20260825 archive) and build_archive_legislative.py never read it.
Where the named sponsor is a current roster member who holds no member row in a scoring
cell, the strongest evidence the archive has about that bill reached the stance matrix
nowhere: Tim Scott x Member Business Lending was published at 10.76 with +6.24 -- three
sponsor rows on S.762/S.3382 (117th) and S.3992 (118th) -- missing entirely
(audits/laundry_list_closeout_20260918/F1/receipts/s3_s4_missing_weight.txt, and the
earlier laneC receipt q2c_scott_sponsor_trace.txt that first found the 6.24).

This module only SYNTHESISES rows; it decides nothing.  Each row it returns is shaped
exactly like an archive member row for the cell it belongs to, so the producer's own gates
-- official-membership eligibility, category routing, direction/sign, decay, and
strongest-role-wins per (member, bill, Congress, category) -- adjudicate it unchanged.
Nothing here is bypassed and nothing here is re-derived: the caller passes the producer's
own Resolver and rule functions in, so a row this module proposes can never resolve to a
different member than the row loop would.

Fail closed.  A cell contributes a row only when its ``Sponsor`` spellings resolve, through
that same Resolver, to exactly ONE roster member.  A name that is ambiguous ('Scott'), off
the roster ('Armstrong, Kelly', 'Scott, David'), blank, or in conflict with another
spelling on the same cell yields nothing -- on the 20260825 archive 49 of 468 bills name a
sponsor who is not a current member, and every one of them must stay out.

Side-effect free by construction: no pandas, no I/O, no ``fec`` imports, and the caller's
rows are never mutated.  Ships behind LEG_SPONSOR_FROM_COLUMN (default '1' = read the
column; '0' restores the pre-2026-09-19 behaviour, which synthesised nothing).
"""
import os
import re


# 2026-09-19, Paul-approved (GO on the F1 recommendations).  Strict on the revert side,
# exactly like build_archive_legislative.legacy_direction_enabled: only the literal '0'
# rolls the fix back, so a typo cannot silently un-ship a ratified change.
DIAL = 'LEG_SPONSOR_FROM_COLUMN'

# The provenance marker the routing audit carries on these rows, and the frame column that
# transports it.  Rows the archive already held carry neither, so an audit written with the
# dial off is byte-identical to the pre-fix one.
SOURCE_COLUMN = 'EvidenceSource'
SOURCE = 'archive-sponsor-column'

# The producer's own column names ('All Bills' header order) and role token.  BR.role_weight
# scores 'sponsor' at 3.0 and BR.preferred_role ranks it above original-cosponsor (2.0) and
# cosponsor (1.0), which is what makes a duplicate of an existing sponsor row inert.
SECTION_COLUMN = 'Section'
ROLE_COLUMN = 'Role'
MEMBER_COLUMN = 'Member'
NAME_COLUMN = 'Name'
CONGRESS_COLUMN = 'Congress'
SPONSOR_COLUMN = 'Sponsor'
SPONSOR_ROLE = 'sponsor'

# 'Rep. Ogles, Andrew [R-TN-5]' / 'Hagerty, Bill [Sen.-R-TN]' -> the bare name the Resolver
# reads.  Congress.gov writes the party/state bracket on either side of the honorific.
_BRACKET = re.compile(r'\[[^\]]*\]')
_HONORIFIC = re.compile(r'^\s*(?:rep|sen|del|res\.?\s*comm|commissioner)\.?\s+', re.I)
_BLANK = ('', 'nan', 'none')


def enabled(environ=None):
    """True unless LEG_SPONSOR_FROM_COLUMN is exactly '0'."""
    environment = os.environ if environ is None else environ
    return environment.get(DIAL, '1') != '0'


def text(value):
    """A cell as text; pandas hands blanks over as float('nan'), which is not a name."""
    rendered = str(value).strip() if value is not None else ''
    return '' if rendered.lower() in _BLANK else rendered


def clean_sponsor_cell(value):
    """Strip the Congress.gov party/state bracket and any chamber honorific."""
    rendered = text(value)
    if not rendered:
        return ''
    rendered = re.sub(r'\s+', ' ', _BRACKET.sub(' ', rendered)).strip().strip(',').strip()
    return _HONORIFIC.sub('', rendered).strip()


def bill_identity(value):
    """The bill half of the producer's event_key, as the producer spells it.

    Verbatim ``str(r.get('Name') or '').strip().lower()``, not the blank-aware ``text``
    above: a cell this helper grouped differently from the row loop would put a
    synthesised row in a cell whose dedup never sees it.  A missing Name therefore keys
    on 'nan' here exactly as it does there.
    """
    return str(value or '').strip().lower()


def sponsor_rows_from_column(rows, resolve, category_of, congress_of, environ=None):
    """Return the member rows the bill-level ``Sponsor`` column implies but the frame lacks.

    ``rows``         the frame's rows as mappings, in frame order, carrying the producer's
                     own column names.  Only rows with a ``Sponsor`` value can contribute a
                     cell, so this is the archive slice -- sentinel and manual evidence
                     arrive with that cell blank.
    ``resolve``      the producer's Resolver.resolve (matchmember): name -> roster member or
                     None.  The single authority on identity, for sponsors and members both.
    ``category_of``  bill_rules.canonical_category, so a cell here is the same cell
                     archive_leg.json sums on.
    ``congress_of``  bill_rules._congress, so 117 and 117.0 are one Congress.

    One row per cell at most, in first-appearance order, each a copy of the cell's first row
    with the member, the role and the provenance marker replaced.
    """
    if not enabled(environ):
        return []
    cells = {}
    order = []
    for source in rows:
        category = category_of(source.get(SECTION_COLUMN))
        if not category:
            continue                       # the producer routes this row to unknown-section
        key = (bill_identity(source.get(NAME_COLUMN)),
               congress_of(source.get(CONGRESS_COLUMN)), category)
        cell = cells.get(key)
        if cell is None:
            cell = cells[key] = {'template': source, 'members': set(), 'sponsors': []}
            order.append(key)
        member = resolve(text(source.get(MEMBER_COLUMN)))
        if member:
            cell['members'].add(member)
        named = clean_sponsor_cell(source.get(SPONSOR_COLUMN))
        if named and named not in cell['sponsors']:
            cell['sponsors'].append(named)
    extra = []
    for key in order:
        cell = cells[key]
        resolved = {resolve(named) for named in cell['sponsors']}
        if len(resolved) != 1 or None in resolved:
            continue                       # unnamed, off-roster, ambiguous, or in conflict
        if resolved.pop() in cell['members']:
            continue                       # the cell already carries the sponsor's own row
        row = dict(cell['template'])
        row[MEMBER_COLUMN] = cell['sponsors'][0]
        row[ROLE_COLUMN] = SPONSOR_ROLE
        row[SOURCE_COLUMN] = SOURCE
        extra.append(row)
    return extra
