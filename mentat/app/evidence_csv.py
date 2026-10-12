"""Read user-selected CSV evidence into a data-root store; no collection or attribution.

The store lives under the console data root, so an import survives a restart, a
stale-build relaunch and a launcher port hop: every console started on the same
data root lists the same imports. Nothing here contacts a network service and
nothing is attributed to a donor; the rows are the reader's own file.
"""
from collections import defaultdict
import codecs
from contextlib import closing, contextmanager
import csv
from datetime import date as date_type, datetime, time as time_type, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import io
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import string
import tempfile
import threading
import time
import uuid
import zipfile

from cross_reference_data import _source_url
import fec_citation

# Bytes are now the only memory-relevant gate: the reader streams the file to
# disk and the parser walks it row by row into sqlite, so neither the request
# thread nor the console ever holds the decoded file. Paul's ruling 2026-09-16
# raised the ceiling to "way past 5 or even 50" MB: the request thread only
# copies CHUNK_BYTES (64 KiB) at a time, so a bigger ceiling costs it no more
# time per chunk, and ingest itself runs off-thread with progress reported to
# the page as rows and bytes are read. MAX_BYTES is therefore a time and disk
# budget for that off-thread ingest, not a request-thread memory one.
#
# Disk is what the ceiling actually budgets, and disk is what measured
# reproducibly. On a 6,000,146-byte 20-column file of 21,759 rows (2026-09-16),
# storing every normalised field beside the cells cost 5.06x the source
# (30,343,168 bytes), because each row then repeated its header names and its
# derived values. Storing only the cells and recomputing normalize() for the
# hundred rows a page shows costs 1.58x (9,498,624 bytes), the same figure in
# every run - that is the design used here, so one import costs source plus
# 1.58x source in sqlite, about 2.6x the source in all. At the 500 MB ceiling
# that is about 1.3 GB of disk for one import. KEEP_BYTES budgets source bytes
# only (KEEP_IMPORTS stays 10); at 1,000,000,000 (1 GB) of kept sources, the
# sqlite databases behind them add roughly another 1.58 GB, about 2.6 GB kept
# on disk worst case. Lower MAX_BYTES or KEEP_BYTES if that is too much for
# the machine.
#
# Ingest time is deliberately NOT the justification, because it could not be
# pinned down here. The identical 6 MB ingest measured between 0.82 s and
# 34.66 s across runs on this host - a 40x spread on identical work, with
# 1.6 GB free of 31.5 GB and standalone runs sometimes slower than in-server
# ones. That is S0's Finding 2 environment (working-set trimming under memory
# pressure), not a property of this code, so no s/MB rate quoted here would
# survive being checked on a quiet machine; at 500 MB the honest expectation
# is minutes, not seconds. What did hold in every run is the part that
# matters: the POST returns in well under a second (0.08-0.81 s observed)
# because the request thread only copies bytes to disk, and the wait that
# follows is off-thread with rows and bytes reported to the page as they are
# read. The ceiling is therefore about how long a reader is willing to watch
# a progress line, not about blocking the console.
#
# Reading back is bounded by the page, not the file: a 100-row page measured
# 15-16 ms, and 46-47 ms at offset 7,000 of a 7,253-row committee.
#
# MAX_ROWS bounds ingest time and page offsets only - the rows themselves live
# in sqlite, not in memory - and scales with MAX_BYTES: 500 MB at roughly
# 100 bytes/row is about 5,000,000 rows.
#
# The generic 7 MB JSON body gate in server.py's do_POST (Content-Length >
# 7_000_000) still bounds the legacy in-request JSON import path only; the
# streamed CSV path above answers to MAX_BYTES.
MAX_BYTES = 500_000_000
MAX_COLUMNS = 200
MAX_ROWS = 5_000_000
CHUNK_BYTES = 65_536
KEEP_IMPORTS = 10
KEEP_BYTES = 1_000_000_000
PAGE_LIMIT = 1000

# The picker refused every name that did not end .csv, and the reader refused
# every byte that was not UTF-8, so the files Paul actually holds - a workbook,
# a tab-separated export, an openFEC .json, an FEC bulk .zip - could not be
# opened at all. Format is chosen by suffix first and content second: the
# suffix says which reader to use, and that reader then decides the encoding,
# the delimiter or the worksheet from the bytes themselves.
#
# JSON is the one format read whole, because an export is one document and
# cannot be walked row by row without a streaming parser, so it carries its own
# much smaller ceiling. A parsed document is not its bytes: every object, key
# and string becomes an interpreter object, which costs several times the file
# on disk before a single row is written, and this machine reads under the
# memory pressure S0 recorded as its Finding 2. 25 MB is therefore the ceiling
# for the one format that cannot be streamed; every other format streams, so
# MAX_BYTES still governs them.
MAX_JSON_BYTES = 25_000_000
DELIMITED_SUFFIXES = ('.csv', '.tsv', '.txt')
WORKBOOK_SUFFIXES = ('.xlsx', '.xlsm')
ACCEPTED_SUFFIXES = DELIMITED_SUFFIXES + WORKBOOK_SUFFIXES + ('.json', '.zip')
DELIMITERS = (',', ';', '\t', '|')
SNIFF_BYTES = 32768
HEADER_FIELD = re.compile(r'[A-Za-z_][A-Za-z0-9_ ]*')
INPUTS = Path(__file__).resolve().parents[1] / 'inputs'

# FEC bulk files ship without a header row, so the header has to come from
# somewhere. The order is: the header file the reader gave, then a copy under
# mentat/inputs, and only then the table below, which is the layout the
# commission publishes for each file type. A header file on disk wins over the
# table, because these layouts have changed before and the file beside the data
# is the one that describes it.
_BULK_TRANSACTION = ('CMTE_ID', 'AMNDT_IND', 'RPT_TP', 'TRANSACTION_PGI', 'IMAGE_NUM', 'TRANSACTION_TP',
                     'ENTITY_TP', 'NAME', 'CITY', 'STATE', 'ZIP_CODE', 'EMPLOYER', 'OCCUPATION',
                     'TRANSACTION_DT', 'TRANSACTION_AMT', 'OTHER_ID', 'TRAN_ID', 'FILE_NUM', 'MEMO_CD',
                     'MEMO_TEXT', 'SUB_ID')
_BULK_TO_CANDIDATE = _BULK_TRANSACTION[:16] + ('CAND_ID',) + _BULK_TRANSACTION[16:]
BULK_HEADERS = {
    # indiv26.zip is named for the layout and holds itcont.txt, named for the
    # content, so both names have to resolve to the same header.
    'indiv': _BULK_TRANSACTION,
    'itcont': _BULK_TRANSACTION,
    'itoth': _BULK_TRANSACTION,
    'oth': _BULK_TRANSACTION,
    'itpas2': _BULK_TO_CANDIDATE,
    'pas2': _BULK_TO_CANDIDATE,
    'cm': ('CMTE_ID', 'CMTE_NM', 'TRES_NM', 'CMTE_ST1', 'CMTE_ST2', 'CMTE_CITY', 'CMTE_ST', 'CMTE_ZIP',
           'CMTE_DSGN', 'CMTE_TP', 'CMTE_PTY_AFFILIATION', 'CMTE_FILING_FREQ', 'ORG_TP',
           'CONNECTED_ORG_NM', 'CAND_ID'),
    'cn': ('CAND_ID', 'CAND_NAME', 'CAND_PTY_AFFILIATION', 'CAND_ELECTION_YR', 'CAND_OFFICE_ST',
           'CAND_OFFICE', 'CAND_OFFICE_DISTRICT', 'CAND_ICI', 'CAND_STATUS', 'CAND_PCC', 'CAND_ST1',
           'CAND_ST2', 'CAND_CITY', 'CAND_ST', 'CAND_ZIP'),
    'ccl': ('CAND_ID', 'CAND_ELECTION_YR', 'FEC_ELECTION_YR', 'CMTE_ID', 'CMTE_TP', 'CMTE_DSGN', 'LINKAGE_ID'),
    'oppexp': ('CMTE_ID', 'AMNDT_IND', 'RPT_YR', 'RPT_TP', 'IMAGE_NUM', 'LINE_NUM', 'FORM_TP_CD',
               'SCHED_TP_CD', 'NAME', 'CITY', 'STATE', 'ZIP_CODE', 'TRANSACTION_DT', 'TRANSACTION_AMT',
               'TRANSACTION_PGI', 'PURPOSE', 'CATEGORY', 'CATEGORY_DESC', 'MEMO_CD', 'MEMO_TEXT',
               'ENTITY_TP', 'SUB_ID', 'FILE_NUM', 'TRAN_ID', 'BACK_REF_TRAN_ID'),
}
# Longest first, so 'itpas2' is not read as 'pas2' and 'itoth' not as 'oth'.
BULK_TYPES = tuple(sorted(BULK_HEADERS, key=len, reverse=True))

COMMITTEE_FIELDS = ('committee_id', 'cmte_id', 'source_committee_id', 'pac_committee_id', 'pac_id', 'recipient_key')
NAME_FIELDS = ('committee_name', 'cmte_nm', 'source_committee_name', 'pac_name', 'recipient_committee', 'recipient')
DATE_FIELDS = ('transaction_date', 'transaction_dt', 'date', 'payment_date')
AMOUNT_FIELDS = ('amount', 'transaction_amount', 'transaction_amt', 'net_included_amount')
CYCLE_FIELDS = ('cycle', 'election_cycle', 'two_year_transaction_period')
SCHEMAS = (
    ('receipt', ('contribution_receipt_amount', 'receipt_amount'), ('contribution_receipt_date', 'receipt_date')),
    ('disbursement', ('disbursement_amount',), ('disbursement_date',)),
    ('independent_spending', ('expenditure_amount',), ('expenditure_date',)),
)
TYPES = {
    'receipt':'receipt', 'receipts':'receipt', 'schedule_a':'receipt', 'sa':'receipt',
    'contribution_receipt':'receipt', 'contribution':'receipt', 'disbursement':'disbursement', 'disbursements':'disbursement',
    'schedule_b':'disbursement', 'sb':'disbursement', 'independent_expenditure':'independent_spending',
    'independent_spending':'independent_spending', 'schedule_e':'independent_spending', 'se':'independent_spending',
    # Paul's ruling 2026-09-17: a workbook's CORPORATE rows are the company's
    # own treasury giving, reported on the same amount and date columns as
    # its employees' contributions, so they belong in the same Receipts
    # channel rather than one of their own. Before this they matched no key
    # at all and every one of them read as unsupported - 333 rows in the
    # Crypto workbook, $176.9M of Fairshake's $178.0M.
    'corporate':'receipt', 'corporate_giving':'receipt', 'corporate_contribution':'receipt',
}
DIRECTIONS = {'s':'Support', 'support':'Support', 'o':'Oppose', 'oppose':'Oppose', 'opposition':'Oppose',
              'contribution_other':'Contribution / Other', 'contribution':'Contribution / Other',
              'unknown':'Unknown'}

WARNINGS = (
    'Imported observations are not verified coverage or donor-attributed PAC spending. Duplicate and amended rows are preserved; totals may overlap.',
    'Committee names and IDs are taken from this CSV; their registration and committee type have not been independently verified.',
    'Cycle aggregates have no inferred transaction date. Missing or ambiguous amounts, dates, or types remain unsupported.',
)

# Committee categories. The vocabulary is the Graph tab's (app/graph_data.py
# CATEGORIES) and has to stay equal to it: both tabs filter the same
# committees by the same words, and a name in one and not the other is a
# filter that silently drops PACs. It is repeated here rather than imported
# because graph_data pulls the report stack in with it and S3e made this
# module's imports lazy on purpose; tests/test_evidence_categories.py asserts
# the two lists are equal.
#
# Conduit is appended after Unclassified, not slotted in beside Super PAC: the
# colour of a group in a chart is its position, so a new name anywhere but the
# end would move every category after it.
CATEGORIES = ('Super PAC', 'Trade Association', 'JFC', 'Company PAC', 'Leadership PAC',
              'Carey', 'Other PAC', 'Candidate', 'Party Committee', 'Unclassified', 'Conduit')
CATEGORY_INDEX = {name.lower():name for name in CATEGORIES}

# The earmark conduits: platforms that pass individual donors' earmarked
# contributions through to other committees. The FEC files ActBlue and WinRed as
# type V (hybrid) committees, which the reports read as Super PAC (until
# 2026-09-08) and then as Carey; neither is right for money that is the donors',
# not the committee's. The registry is the single list of them: these are the
# two IDs fec_citation.CONDUIT_COMMITTEES uses for the filing links (the
# tests/test_conduit_category.py pins the two together), and it is read before
# any file, designation or type rule, so a stale label in a workbook or an
# import cannot override it.
CONDUIT = 'Conduit'
EARMARK_CONDUITS = {'C00401224':'ActBlue', 'C00694323':'WinRed'}
CONDUIT_SOURCE = 'earmark_conduit_registry:'


def conduit_name(committee_id):
    """'ActBlue' or 'WinRed' for a registered earmark conduit, else ''. Reads FEC:C... and any case."""
    return EARMARK_CONDUITS.get(re.sub(r'^FEC:', '', str(committee_id or '').strip().upper()), '')


def conduit_category(committee_id, category):
    """`category`, or Conduit when the committee is a registered earmark conduit."""
    return CONDUIT if conduit_name(committee_id) else category


def relabel_conduit_rows(rows):
    """Say Conduit in the recipient_category of every row of a registered conduit, in place.

    For answers that read the stored contribution facts as they are: those stores were built when the
    reports still filed ActBlue and WinRed under Super PAC, and rebuilding each industry's store for a
    label would cost hundreds of megabytes apiece."""
    for row in rows or ():
        if isinstance(row, dict) and row.get('recipient_category') is not None and conduit_name(row.get('committee_id')):
            row['recipient_category'] = CONDUIT
    return rows


def conduit_fingerprint():
    """What decides the label. Code that caches answers carrying a category folds this into its identity,
    so a change to the registry or to the name is a different key and an old label is never served."""
    return hashlib.sha256(json.dumps([CONDUIT, sorted(EARMARK_CONDUITS.items())]).encode()).hexdigest()
# Not a bare `category`: an FEC bulk oppexp file has a CATEGORY column
# carrying the commission's purpose code, which would read as a committee
# category, canonicalise to Unclassified and then stop the committee master
# being consulted at all (the file wins, Paul's ruling 4).
CATEGORY_FIELDS = ('recipient_category', 'committee_category')
INCLUSION_FIELDS = ('include_in_analysis',)
INCLUSION_REASON_FIELDS = ('inclusion_reason',)
FALSE_TEXT = {'no', 'n', 'false', '0'}

# Catalogs are built once, at ingest, so a rule change here cannot reach an
# import already on disk. The version is written into import.json beside the
# catalog and Store.sources() re-reads anything older under the current
# rules, because the alternative is a console that answers two different
# things about one file depending on the day it was imported.
CATALOG_VERSION = 2

EXCLUDED_ISSUE = 'Excluded by the workbook.'

# Every refusal names the file or the reason, because a blank status line in
# the page is itself a defect. The three messages that quote a limit are built
# from the constants when they are raised, so a changed limit cannot leave a
# stale number in the reader's status line.
M_NAME = 'Choose a CSV file with a name ending in .csv.'
M_EMPTY = 'The CSV file is empty.'
M_UTF8 = 'Use a UTF-8 CSV file without invalid text characters.'
M_COMMITTEE_COLUMN = 'The CSV needs a Committee ID, CMTE_ID, or Source Committee ID column.'
M_NO_COMMITTEE_ROWS = 'The CSV has no rows with one valid FEC committee ID (C followed by eight digits).'
M_BUSY = 'Another CSV is still being read. Wait for it to finish.'
M_PAGE = 'Choose a valid CSV row page (1–1,000 rows).'
M_INTERRUPTED = 'Reading was interrupted by a console restart. Choose the file again.'
M_COMMITTEE = 'Choose a committee in this imported CSV.'
M_CYCLE = 'Choose a cycle recorded in this CSV.'
# M_UTF8 above is kept defined for callers that still import it, but nothing
# raises it any more: a file that is not valid UTF-8 is decoded as
# Windows-1252 and says so in the catalog, and only content that is not text at
# all (a NUL byte outside UTF-16) is refused, as M_BINARY.
M_BINARY = 'This file contains binary data and could not be read as text.'
M_JSON_SHAPE = 'The JSON must be an openFEC export ({"results": [...]}) or a list of objects.'
M_HEADER = ('This looks like an FEC bulk file without a header row and its name is not a known bulk '
            'type. Give its header file too.')
M_XLSX_READ = 'The workbook could not be opened. It may be corrupt or password-protected.'
M_ZIP_READ = 'The ZIP could not be opened. It may be corrupt.'
M_CP1252 = 'Decoded as Windows-1252 because the file is not valid UTF-8.'
STORE_DIRECTORY = 'evidence-csv'
IDENTIFIER = re.compile(r'csv-[a-f0-9]{32}')

# A Mentat industry workbook names its industry in its own file name, so an
# import read from one can be labelled with it without opening the file again.
# `.+?` is lazy and the pattern is anchored at both ends, so a name that
# repeats the phrase keeps the first copy inside the label rather than
# swallowing it. Anything else - an FEC bulk file, a hand-saved CSV - has no
# industry, and the field is then blank rather than guessed.
WORKBOOK_NAME = re.compile(r'^(?P<industry>.+?) Industry Spending (?:Overview|Data)\.xlsx$', re.IGNORECASE)
# The name a blank counterparty cell reads as in a ranking. The same words the
# row table already shows for an unreported counterparty.
BLANK_COUNTERPARTY = 'Not reported'
TOP_COUNTERPARTIES = 10


def industry_of(name):
    """The industry a Mentat workbook file name declares, or '' for anything else."""
    match = WORKBOOK_NAME.match(str(name or '').strip())
    return match.group('industry').strip() if match else ''


def bytes_message():
    return f'Choose a CSV no larger than {MAX_BYTES // 1_000_000} MB.'


def columns_message():
    return f'The CSV needs unique nonempty column names, with no more than {MAX_COLUMNS} columns.'


def rows_message():
    return f'Choose a CSV containing no more than {MAX_ROWS:,} rows.'


def M_READ(row_number):
    return f'The CSV could not be read at row {row_number:,}. Check its quoted fields and delimiter.'


def M_GONE(identifier):
    return f'This CSV import is no longer on disk ({identifier}). Choose the file again.'


def M_SHORT(received, expected):
    return f'The upload ended early: {received:,} of {expected:,} bytes arrived.'


def M_TYPE(suffix):
    return (f'This file type ({suffix or "no extension"}) is not supported. '
            'Choose CSV, TSV, TXT, XLSX, JSON or an FEC bulk ZIP.')


def M_ZIP(members):
    return f'The ZIP must hold exactly one .txt or .csv file; it holds: {", ".join(members[:10])}.'


def M_SHEET(name, sheets):
    return f'Worksheet {name!r} is not in this workbook. Sheets: {", ".join(sheets)}.'


def M_PATH(path, reason):
    return f'Cannot open {path}: {reason}'


def json_size_message():
    return f'Choose a JSON file no larger than {MAX_JSON_BYTES // 1_000_000} MB, or export it as CSV.'


_DERIVED = {'M_BYTES':bytes_message, 'M_COLUMNS':columns_message, 'M_ROWS':rows_message,
            'M_JSON_SIZE':json_size_message}


def __getattr__(name):
    """Serve the three limit messages at attribute time, not at import time.

    A caller reading ``evidence_csv.M_BYTES`` gets the message for the limit
    that is in force now, which keeps a monkeypatched limit and the message the
    reader sees in agreement.
    """
    if name in _DERIVED:
        return _DERIVED[name]()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def limits():
    return {'max_bytes':MAX_BYTES, 'max_columns':MAX_COLUMNS, 'max_rows':MAX_ROWS,
            'max_bytes_message':bytes_message(), 'max_columns_message':columns_message(),
            'max_rows_message':rows_message(), 'accepted_suffixes':list(ACCEPTED_SUFFIXES),
            'max_json_bytes':MAX_JSON_BYTES}


def decode_name(value):
    """Decode a percent-encoded upload name header without importing urllib here."""
    from urllib.parse import unquote
    return unquote(str(value or ''))


def page_args(offset, limit):
    if isinstance(offset, bool) or isinstance(limit, bool):
        raise ValueError(M_PAGE)
    try:
        offset, limit = int(str(offset).strip()), int(str(limit).strip())
    except (TypeError, ValueError):
        raise ValueError(M_PAGE) from None
    if not 0 <= offset <= MAX_ROWS or not 1 <= limit <= PAGE_LIMIT:
        raise ValueError(M_PAGE)
    return offset, limit


def key(value):
    return re.sub(r'[^a-z0-9]+', '_', str(value).strip().lower()).strip('_')


def first(row, names):
    return next((row[name].strip() for name in names if row.get(name, '').strip()), '')


def number(value):
    text = str(value).strip()
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1].strip()
    text = text.removeprefix('$').strip()
    if negative and text.startswith(('+', '-')):
        return None
    if ',' in text and not re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', text):
        return None
    text = text.replace(',', '')
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text):
        return None
    try:
        value = Decimal(text)
        value = -value if negative else value
        return value if value.is_finite() and abs(value) <= Decimal('1e300') and math.isfinite(float(value)) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def date(value):
    text = value.strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+Z-]+)?', text):
            return datetime.fromisoformat(text.replace('Z', '+00:00')).date().isoformat()
        if re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', text):
            return datetime.strptime(text, '%m/%d/%Y').date().isoformat()
        if re.fullmatch(r'\d{8}', text):
            return datetime.strptime(text, '%Y%m%d' if text[:2] in ('19','20','21') else '%m%d%Y').date().isoformat()
    except ValueError:
        pass
    return None


def cycle(value):
    match = re.fullmatch(r'(?:(\d{4})[-–])?(\d{4})', value.strip())
    if (match and 1980 <= int(match[2]) <= 2100 and int(match[2]) % 2 == 0
            and (not match[1] or int(match[1]) == int(match[2]) - 1)):
        return match[2]
    return ''


MEMO_ISSUE = 'Memo entry; not counted in totals.'
NOTICE_ISSUE = 'Notice report (24/48-hour); the periodic filing carries this amount.'
TRANSACTION_TABLE = 'inputs/fec_transaction_types.json'


@lru_cache(maxsize=1)
def transaction_types():
    """The FEC bulk TRANSACTION_TP -> channel table, read once from the inputs folder.

    The file is Paul's to edit (see its "ruling" field). A missing or unreadable
    file maps nothing, so every bulk row stays unsupported and visible rather
    than counted under a guess.
    """
    path = INPUTS / 'fec_transaction_types.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {'verified': False, 'codes': {}, 'excluded': {}, 'memo_codes': set(), 'notice_report_types': set(), 'missing': True}
    codes = {str(code).upper(): value for code, value in (data.get('codes') or {}).items()
             if isinstance(value, dict) and value.get('kind') in ('receipt', 'disbursement', 'independent_spending')}
    excluded = {str(code).upper(): str(reason) for code, reason in (data.get('excluded_by_ruling') or {}).items()}
    return {'verified': bool(data.get('verified')), 'codes': codes, 'excluded': excluded,
            'memo_codes': {str(code).upper() for code in data.get('memo_codes') or []},
            'notice_report_types': {str(code).upper() for code in data.get('notice_report_types') or []},
            'missing': False}


def unknown_code_issue(code):
    return f'Transaction type code {code} is not in the mapping table ({TRANSACTION_TABLE}).'


def excluded_code_issue(code, reason):
    return f'Transaction type code {code} is excluded by ruling: {reason}.'


def bulk_warnings():
    """Catalog notes for FEC bulk imports: what the channels mean here and whether the table is verified."""
    table = transaction_types()
    notes = ['Bulk itemized files: Disbursements here are contributions, transfers and refunds made; '
             'operating expenditures are in the separate oppexp file.']
    if table['missing']:
        notes.append(f'The transaction-type table {TRANSACTION_TABLE} is missing, so every bulk row stays unsupported.')
    elif not table['verified']:
        notes.append(f'The transaction-type table {TRANSACTION_TABLE} has not been verified against the FEC\'s published list.')
    return notes


def excluded_issue(reason):
    """Why a row the workbook marked Include in Analysis = No is out of the totals."""
    reason = str(reason or '').strip()
    return f'Excluded by the workbook: {reason}' if reason else EXCLUDED_ISSUE


def canonical_category(value):
    """One category name from the file, in the Graph vocabulary.

    Case-insensitive exact match; text that is not one of the eleven names is
    Unclassified, which is itself one of them; a blank cell stays blank, so a
    committee the file never labelled can be told from one the file calls
    Unclassified, and only the first kind reaches the committee master.
    """
    text = str(value or '').strip()
    return CATEGORY_INDEX.get(text.lower(), 'Unclassified') if text else ''


def modal_from_counts(counts):
    """The commonest category in a tally, ties broken by vocabulary order."""
    if not counts:
        return ''
    best = max(counts.values())
    return next((name for name in CATEGORIES if counts.get(name) == best), '')


def modal_category(rows):
    """The category of a committee: the commonest CLASSIFIED one over its rows.

    A file can disagree with itself - the Crypto workbook labels Fairshake a
    Super PAC on 22 rows and leaves the cell blank on 6 - so no single row
    can decide. Ties go to the earlier name in the vocabulary, a fixed order,
    so two runs over one file never disagree.

    'Unclassified' does not count. It is the absence of a classification
    rather than one of them - graph_data reads a missing recipient_category
    as exactly that word - so a committee named Carey once and Unclassified
    five times is Carey, and one the file only ever calls Unclassified still
    reaches the committee master.
    """
    counts = defaultdict(int)
    for row in rows:
        name = row.get('category') if isinstance(row, dict) else row
        if name and name != 'Unclassified':
            counts[name] += 1
    return modal_from_counts(counts)


def category_header(headers):
    """The file's own category column, under the name the file writes it with."""
    columns = [key(item) for item in headers]
    return next((headers[columns.index(name)] for name in CATEGORY_FIELDS if name in columns), '')


@lru_cache(maxsize=1)
def committee_master():
    """The FEC committee master (cm.txt), read once per process and lazily.

    Loaded only when a committee has no category in the file it came from, so
    a console that never opens PAC evidence never pays for it. Newest cycle
    first: cm26 answers, cm24 fills what it does not know, cm22 what neither
    does, and the cycle that supplied each ID is kept so the page can say
    where a label came from. The files ship without a header row; a copy
    saved through a spreadsheet can grow one, and its first field is not a
    committee ID so it is skipped like any other unusable line. Nothing here
    may raise: a master that cannot be read leaves its committees
    Unclassified and says so in the catalog warnings.
    """
    root = INPUTS / 'fec_committee_master'
    rows, cycles, notes = {}, [], []
    try:
        folders = sorted((path for path in root.glob('cm*') if (path / 'cm.txt').is_file()),
                         key=lambda path: path.name, reverse=True)
    except OSError:
        folders = []
    for folder in folders:
        try:
            text = (folder / 'cm.txt').read_text(encoding='utf-8', errors='replace')
        except OSError as error:
            notes.append(f'The FEC committee master file {folder.name}/cm.txt could not be read '
                         f'({type(error).__name__}); its committees stay Unclassified.')
            continue
        cycles.append(folder.name)
        for line in text.splitlines():
            fields = line.split('|')
            if len(fields) < 13:
                continue
            identifier = fields[0].strip().upper()
            if identifier in rows or not re.fullmatch(r'C\d{8}', identifier):
                continue
            rows[identifier] = (fields[8].strip().upper(), fields[9].strip().upper(),
                                fields[12].strip().upper(), folder.name)
    return {'rows':rows, 'cycles':cycles, 'warnings':notes}


def classify_committee(committee_id, has_file_categories=False):
    """(category, category_source) for one committee ID from the FEC master.

    The order is the report pipeline's own fallback order (R/report.R), so a
    committee the workbook did not label reads here the way it reads in the
    industry report, with the ORG_TP rules added for files that carry no
    category column at all. Designation beats type and type beats ORG_TP,
    because a joint fundraising committee filed as a Super PAC is still a
    JFC. Exported for server.py, which labels saved snapshots the same way.

    `has_file_categories` says the file carried a category column of its own.
    A committee connected to a corporation (ORG_TP C or W) is then read as
    Other PAC rather than Company PAC, because 'Company PAC' in a Mentat
    workbook means one of the industry's OWN companies' PACs - a label the
    file assigns - and a payment processor's PAC that happens to be
    registered to a corporation must not land in that bucket.

    An earmark conduit (EARMARK_CONDUITS) is answered first, from the registry
    alone: its FEC record is a hybrid type V, which would read as Carey, and a
    designation or type rule must never turn it back into one.
    """
    name = conduit_name(committee_id)
    if name:
        return CONDUIT, CONDUIT_SOURCE + name
    master = committee_master()
    entry = master['rows'].get(str(committee_id or '').strip().upper())
    if entry is None:
        if not master['cycles']:
            return 'Unclassified', 'committee master not available'
        return 'Unclassified', 'not in the FEC committee master (' + ', '.join(sorted(master['cycles'])) + ')'
    designation, committee_type, organisation, cycle = entry
    source = f'fec_master:{cycle}'
    if designation == 'J':
        return 'JFC', source
    if designation == 'D':
        return 'Leadership PAC', source
    if committee_type in ('X', 'Y', 'Z'):
        return 'Party Committee', source
    if committee_type in ('O', 'U'):
        return 'Super PAC', source
    if committee_type in ('V', 'W'):
        return 'Carey', source
    if committee_type in ('H', 'S', 'P') or designation in ('A', 'P'):
        return 'Candidate', source
    connected = f'fec_master:org_type:{cycle}'
    if organisation == 'T':
        return 'Trade Association', connected
    if organisation in ('C', 'W'):
        return ('Other PAC' if has_file_categories else 'Company PAC'), connected
    if organisation in ('L', 'M', 'V'):
        return 'Other PAC', connected
    return 'Unclassified', source


def committee_category(counts, identifier, header, unclassified=False):
    """(category, category_source, consulted) for one committee.

    Paul's ruling 2026-09-17: when both know the committee the file wins, and
    the master only fills blanks. The file is the industry pipeline's own
    classification of this recipient; the master is a registration record
    that does not know, for instance, which of a company's PACs the analysis
    treats as its own.

    A blank cell and the word 'Unclassified' are the same absence, so both
    reach the master: measured on the Crypto import, of the 15 committees
    the file leaves Unclassified the master classifies 7, among them
    C00567412 CHAMBER OF DIGITAL COMMERCE PAC (ORG_TP T -> Trade
    Association), exactly the kind of PAC that read as missing. When the
    master cannot do better either, the source names both, so a reader can
    tell 'nobody knows' from 'the file never said'. `consulted` reports
    whether the master was asked, which is what the catalog sentence counts.

    The one exception to "the file wins" is an earmark conduit: the registry
    answers before the file, because the files that file ActBlue under Super
    PAC are the stale ones.
    """
    name = conduit_name(identifier)
    if name:
        return CONDUIT, CONDUIT_SOURCE + name, False
    category = modal_from_counts(counts or {})
    if category:
        return category, f'file:{header}', False
    category, source = classify_committee(identifier, bool(header))
    if category != 'Unclassified' or not unclassified:
        return category, source, True
    return 'Unclassified', f'file:{header}; {source}', True


def category_buckets(committees):
    """The per-category rollup the picker's type filter is built from."""
    buckets = defaultdict(lambda: {'committees':0, 'rows':0, 'supported_rows':0, 'excluded_rows':0})
    for item in committees:
        if not item.get('category'):
            continue
        bucket = buckets[item['category']]
        bucket['committees'] += 1
        for field in ('rows', 'supported_rows', 'excluded_rows'):
            bucket[field] += int(item.get(field) or 0)
    return [{'category':name, **buckets[name]} for name in CATEGORIES if name in buckets]


def category_warning(header, consulted):
    """The catalog sentence that says where THIS import's categories came from.

    It replaces the standing note that committee type 'has not been
    independently verified', which was equally true of every import and told
    a reader nothing about the one in front of them. The master is only
    consulted when it was actually used, so a file that labels every
    committee itself never loads it.
    """
    if header and not consulted:
        return f"Committee categories come from this file's {header} column."
    cycles = ', '.join(committee_master()['cycles'])
    plural = 'committee' if consulted == 1 else 'committees'
    if header and cycles:
        return (f"Committee categories come from this file's {header} column; {consulted:,} {plural} the "
                f'file left blank or Unclassified were checked against the FEC committee master ({cycles}).')
    if header:
        return (f"Committee categories come from this file's {header} column; the FEC committee master is "
                f'not available, so the {consulted:,} {plural} the file left blank or Unclassified stay '
                'Unclassified.')
    if cycles:
        return f'Committee categories were classified from the FEC committee master ({cycles}).'
    return 'The FEC committee master is not available, so every committee here is Unclassified.'


def catalog_warnings(header, consulted):
    """The three standing catalog notes, the second one built for this import."""
    notes = [WARNINGS[0], category_warning(header, consulted), WARNINGS[2]]
    return notes + committee_master()['warnings'] if consulted else notes


def normalize(raw, row_number, columns, extra_cells=()):
    row = dict(zip(columns, raw.values()))
    issues = []
    supplied_ids = {re.sub(r'^FEC:', '', row[name].strip().upper()) for name in COMMITTEE_FIELDS if row.get(name, '').strip()}
    valid_ids = {value for value in supplied_ids if re.fullmatch(r'C\d{8}', value)}
    committee = next(iter(valid_ids)) if len(valid_ids) == 1 and len(supplied_ids) == 1 else ''
    if not committee:
        issues.append('A unique valid FEC committee ID is missing or conflicting.')
    specific = [(kind, amount_fields, dates) for kind,amount_fields,dates in SCHEMAS if first(row, amount_fields)]
    explicit_kind = TYPES.get(key(first(row, ('record_type', 'activity_type', 'schedule', 'schedule_type', 'type', 'transaction_type'))))
    # FEC bulk rows carry their type as the commission's TRANSACTION_TP code.
    # The code -> channel table is Paul's data file; memo entries and 24/48-hour
    # notice reports are classified but kept out of totals; an unlisted code
    # names the table it is missing from. Only the bulk key is read here, so a
    # workbook's own transaction_type column keeps its existing behaviour.
    code = row.get('transaction_tp', '').strip().upper()
    code_direction, code_issue = '', ''
    if code:
        table = transaction_types()
        entry = table['codes'].get(code)
        if entry:
            explicit_kind, code_direction = entry['kind'], entry.get('direction', '')
        if row.get('memo_cd', '').strip().upper() == 'X' or code in table['memo_codes']:
            code_issue = MEMO_ISSUE
        elif row.get('rpt_tp', '').strip().upper() in table['notice_report_types']:
            code_issue = NOTICE_ISSUE
        elif code in table['excluded']:
            code_issue = excluded_code_issue(code, table['excluded'][code])
        elif entry is None:
            code_issue = unknown_code_issue(code)
        if code_issue:
            issues.append(code_issue)
    kind, amount_raw, date_raw = 'unsupported', '', ''
    basis = 'dated observation'
    if len(specific) == 1:
        kind, amount_fields, dates = specific[0]
        amount_raw, date_raw = first(row, amount_fields), first(row, dates + DATE_FIELDS)
        if explicit_kind and explicit_kind != kind:
            issues.append('The explicit row type conflicts with its amount column; no channel total was assigned.')
    elif len(specific) > 1:
        issues.append('Several receipt/activity amount columns are populated; the row cannot be assigned to one channel.')
    else:
        amount_raw, date_raw = first(row, AMOUNT_FIELDS), first(row, DATE_FIELDS)
        kind = explicit_kind or 'unsupported'
    direction_raw = first(row, ('support_oppose', 'support_oppose_indicator', 'direction'))
    direction = code_direction or DIRECTIONS.get(key(direction_raw), 'Unknown')
    selected_cycle = cycle(first(row, CYCLE_FIELDS))
    if (not specific and kind == 'unsupported' and row.get('net_included_amount', '').strip()
            and row.get('source_committee_id', '').strip() and direction_raw and selected_cycle):
        kind, basis = 'reported_aggregate', 'cycle aggregate'
    amount_value = number(amount_raw)
    observed_date = date(date_raw)
    if amount_value is None:
        issues.append('The observed amount is missing or not a finite number.')
    if not observed_date and (date_raw or basis != 'cycle aggregate'):
        issues.append('The transaction date is missing or invalid; no date was inferred.')
    if direction_raw and key(direction_raw) not in DIRECTIONS and kind in ('independent_spending', 'reported_aggregate'):
        issues.append('The activity direction is not recognized; it was not assigned to support or opposition.')
    if kind == 'unsupported' and not code_issue:
        issues.append('The CSV does not identify a supported receipt or activity type.')
    if extra_cells:
        issues.append('This row contains more values than its header.')
    # Paul's ruling 2026-09-17: the workbook already decided which of its own
    # rows count, and its duplicate memo legs would otherwise double the
    # corporate channel. An excluded row keeps its kind, channel and amount
    # and stays on the page with the workbook's own reason; it is simply out
    # of every total. Its status is a third value, so every consumer that
    # tests for 'supported' goes on counting exactly what it counted before.
    excluded = key(first(row, INCLUSION_FIELDS)) in FALSE_TEXT
    if excluded:
        issues.append(excluded_issue(first(row, INCLUSION_REASON_FIELDS)))
    channel = {'receipt':'Receipts', 'disbursement':'Disbursements'}.get(kind)
    # The file's own word for the committee, except that a registered earmark
    # conduit is a Conduit whatever a stale file calls it (a blank stays blank,
    # so the catalog still knows the file never labelled the committee).
    file_category = canonical_category(first(row, CATEGORY_FIELDS))
    if kind == 'independent_spending':
        channel = 'Independent spending · ' + direction.lower()
    if kind == 'reported_aggregate':
        channel = 'Reported cycle aggregate · ' + direction.lower()
    return {
        'row_number':row_number, 'raw':raw, 'extra_cells':list(extra_cells),
        'committee_id':committee, 'committee_name':first(row, NAME_FIELDS),
        'kind':kind, 'channel':channel or 'Unsupported', 'basis':basis,
        'amount':float(amount_value) if amount_value is not None else None, '_amount':amount_value,
        'date':observed_date, 'date_raw':date_raw, 'cycle':selected_cycle,
        'direction':direction if kind in ('independent_spending', 'reported_aggregate') else '',
        'counterparty':first(row, ('contributor_name', 'contributor', 'recipient_name', 'candidate_name', 'candidate_officeholder', 'reported_name', 'name')),
        'candidate_id':first(row, ('candidate_id', 'cand_id', 'candidate_id_as_filed')),
        'candidate_name':first(row, ('candidate_name', 'candidate_officeholder', 'candidate_name_as_filed')),
        # A Mentat workbook names the FEC sub_id "Source ID" (normalised
        # source_id); it is the last candidate, so every file that already
        # had one of the others keeps the id it had.
        'record_id':first(row, ('sub_id', 'transaction_id', 'tran_id', 'record_id', 'source_id')),
        'source_url':_source_url(first(row, ('source_url', 'pdf_url', 'filing_url'))),
        'category':conduit_category(committee, file_category) if file_category else '',
        'status':'excluded' if excluded else ('unsupported' if issues else 'supported'), 'issues':issues,
    }


def rebuild(headers, columns, line_number, cells):
    """Rebuild one normalised row from the original cells that were stored.

    normalize() is pure - it reads only its arguments, the module tables and
    _source_url, itself a pure string check - so recomputing it here returns
    exactly what the ingest computed. Only the cells need to live on disk, and
    storing the derived fields as well would have cost several times the size
    of the file they came from.
    """
    raw = {header:cells[index] if index < len(cells) else '' for index,header in enumerate(headers)}
    return normalize(raw, line_number, columns, cells[len(headers):])


def atomic_json(path, value, attempts=6):
    """Replace a record in one step so a reader never sees a half-written file.

    attempts=1 for an advisory write, such as the progress the page polls. A
    scanner or indexer holding import.json open for a moment is common on
    Windows, and a progress update is not worth waiting on: retrying with a
    backoff inside the row loop adds its sleeps to the ingest, and raising
    would fail a perfectly good import over a counter nobody had read yet.
    """
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(attempts):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def descriptive(record):
    """How this import was read, in the terms the page shows the reader.

    The same block rides on the JOB and on the CATALOG, so a listing read back
    tomorrow says what a poll of the running import said: which reader ran,
    whether the file came from a browser upload or from a path on this
    computer, which worksheet, which encoding and delimiter, and where an FEC
    bulk header came from.
    """
    name = str(record.get('name') or '')
    fmt = record.get('format') or ''
    if not fmt and Path(name).suffix.lower() in ACCEPTED_SUFFIXES:
        # An import read before these fields existed still names its format,
        # from the only evidence it kept: the file name it was stored under.
        fmt = source_format(name, record.get('header_path') or '')
    return {'format':fmt, 'source_kind':record.get('source_kind') or 'upload',
            'source_path':record.get('source_path') or '', 'sheet':record.get('sheet') or '',
            'sheets':list(record.get('sheets') or []), 'sheets_read':list(record.get('sheets_read') or []), 'encoding':record.get('encoding') or '',
            'delimiter':record.get('delimiter') or '', 'header_source':record.get('header_source') or ''}


def stored_catalog(record, state='ready', reread=False, stale=False):
    """One stored catalog, with any descriptive field it predates filled in.

    The catalog is written once, when the import is read, so an import already
    on disk carries the S3-era shape. The record beside it is filled the same
    way for both, so the listing the page renders has one shape whether the
    file was read today or last week - including the S7 category fields,
    which an import read before them is about to be re-read for.

    The last three fields describe the ENTRY, not the file: whether this
    console is re-reading the import under the current rules right now, and
    whether it is one a read-only console can only report as out of date,
    because a preview shares its data root with the console that owns these
    imports and must not rewrite them.
    """
    catalog = dict(record.get('catalog') or {})
    if not catalog:
        return catalog
    filled = {'categories':[], 'excluded_rows':0, **descriptive(record), **catalog}
    filled['committees'] = [{'category':'', 'category_source':'', 'excluded_rows':0, **item}
                            for item in filled.get('committees') or []]
    # An import already on disk was read when a workbook still filed ActBlue
    # and WinRed under Super PAC. The registry is read here, at listing time,
    # so no import is re-read (CATALOG_VERSION stays) for a label.
    relabelled = False
    for item in filled['committees']:
        name = conduit_name(item.get('id'))
        if name and item.get('category') != CONDUIT:
            item['category'], item['category_source'] = CONDUIT, CONDUIT_SOURCE + name
            relabelled = True
    if relabelled:
        filled['categories'] = category_buckets(filled['committees'])
    filled['catalog_version'] = int(record.get('catalog_version') or 0)
    # Derived here rather than written at ingest, so every import already on
    # disk answers with it on the first listing and none has to be re-read for
    # a label that is a function of its own file name.
    filled['industry'] = (industry_of(filled.get('name'))
                          or industry_of(PureWindowsPath(str(record.get('source_path') or '')).name))
    filled['rows_read'] = int(record.get('rows_read') or catalog.get('row_count') or 0)
    filled['state'], filled['reread'], filled['stale'] = state, bool(reread), bool(stale)
    return filled


def job_view(record):
    """The reading/ready/failed record the page polls, without store internals."""
    return {'id':record.get('id', ''), 'name':record.get('name', ''), 'state':record.get('state', 'failed'),
            'bytes':int(record.get('bytes') or 0), 'bytes_read':int(record.get('bytes_read') or 0),
            'rows_read':int(record.get('rows_read') or 0), 'message':record.get('message') or '',
            'started_at':record.get('started_at'), 'finished_at':record.get('finished_at'),
            'catalog':stored_catalog(record) or None, **descriptive(record)}


# One ingest thread per process, registered here so a record that claims to be
# reading can be told apart from one whose reader died with a restart.
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = {}


def _active_worker(directory):
    """The live ingest or re-read thread for one import directory, if any.

    Dead entries are dropped on the way past: a worker that has finished must
    not leave its import looking busy until the next upload happens to prune
    the registry.
    """
    with _ACTIVE_LOCK:
        for finished in [running for running, thread in _ACTIVE.items() if not thread.is_alive()]:
            _ACTIVE.pop(finished, None)
        return _ACTIVE.get(str(directory))


def accepted_suffix(name):
    """The suffix this reader will open, or a refusal naming the one it got."""
    if not isinstance(name, str) or not name.strip() or len(name) > 250:
        raise ValueError(M_NAME)
    suffix = Path(name).suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise ValueError(M_TYPE(suffix))
    return suffix


def bulk_type(stem):
    """The FEC bulk file type a name begins with, if it begins with one."""
    lowered = str(stem or '').lower()
    return next((name for name in BULK_TYPES if lowered.startswith(name)), '')


def source_format(name, header_path=''):
    """Which reader a name asks for.

    Decided from the name alone, so the JOB the page polls can say what is
    being read from the first poll onwards. A .zip here is always an FEC bulk
    archive, and a .txt is bulk only when its name is a bulk type or the reader
    supplied a header file; every other delimited name is read as text with its
    own header row.
    """
    suffix = Path(str(name or '')).suffix.lower()
    if suffix in WORKBOOK_SUFFIXES:
        return 'xlsx'
    if suffix == '.json':
        return 'json'
    if suffix == '.zip':
        return 'fec_bulk'
    if suffix == '.txt' and bulk_type(Path(str(name or '')).stem):
        return 'fec_bulk'
    if suffix in ('.txt', '.csv') and header_path:
        return 'fec_bulk'
    return 'delimited'


def import_id(digest, sheet=''):
    """'csv-' + the content digest, with a chosen worksheet folded in after it.

    A file with no worksheet choice keeps the identifier its bytes alone gave
    it before worksheets existed, so a CSV already in the store is still found
    under the id it was stored with. Two worksheets of one workbook are two
    imports, so the sheet name has to reach the identifier for the second one
    not to dedupe onto the first.
    """
    if sheet:
        digest = digest.copy()
        digest.update(b'\x00' + sheet.encode('utf-8'))
    return 'csv-' + digest.hexdigest()[:32]


class Upload:
    """One in-flight file: size-gated, hashed and written as it arrives.

    Nothing is decoded here any more. A workbook, a zip and a UTF-16 export are
    all legitimate now and none of them is UTF-8 text, so the encoding question
    belongs to the reader that walks the file, not to the thread copying bytes
    off a socket.
    """

    def __init__(self, store, name, sheet='', header_path=''):
        self.suffix = accepted_suffix(name)
        self.store = store
        self.name = Path(name).name
        self.sheet = str(sheet or '')
        self.header_path = str(header_path or '')
        self.path = store.incoming / (uuid.uuid4().hex + self.suffix)
        self.bytes = 0
        self._handle = self.path.open('wb')
        self._digest = hashlib.sha256()
        self._blank = True
        self._closed = False

    def write(self, chunk):
        """Accept one chunk, refusing before it is written if it crosses a limit.

        A refusal closes and deletes the part-written file here rather than
        waiting for the caller to abort. An open handle keeps its directory
        undeletable on Windows, so a caller that simply stops writing after a
        refusal would strand both the file and the sweep that should remove it.
        """
        if not chunk:
            return
        try:
            if self.bytes + len(chunk) > MAX_BYTES:
                raise ValueError(bytes_message())
            if self._blank and chunk.strip():
                self._blank = False
            self._digest.update(chunk)
            self._handle.write(chunk)
            self.bytes += len(chunk)
        except BaseException:
            self.abort()
            raise

    def abort(self):
        self._close()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def _close(self):
        if not self._closed:
            self._closed = True
            try:
                self._handle.close()
            except OSError:
                pass

    def finish(self):
        """Name the import by its content and hand the file to a reader thread."""
        self._close()
        if self._blank:
            self.abort()
            raise ValueError(M_EMPTY)
        try:
            # The workbook is opened by the reader thread and nowhere else, so
            # a corrupt file and an unknown worksheet are reported the way
            # every other unreadable file is: on the JOB the page is polling,
            # not as a refusal of the upload that has already arrived.
            return self.store.adopt(import_id(self._digest, self.sheet), self.name, self.path, self.bytes,
                                    self._digest.hexdigest(), suffix=self.suffix, sheet=self.sheet,
                                    header_path=self.header_path)
        except BaseException:
            self.abort()
            raise


class Reader:
    """One open source: its header row, its rows, and how far reading has got.

    Every format answers with the same three things - a header list, an
    iterator of (line_number, cells) and a byte position for the progress the
    page polls - so the row loop in read_source never learns which format it is
    walking, and the per-row path (row cap, normalise, batch, totals) stays the
    one CSV imports have always used.
    """

    def __init__(self, headers, rows, tell, meta, warnings=()):
        self.headers = list(headers)
        self.rows = rows
        self.tell = tell
        self.meta = dict(meta)
        self.warnings = list(warnings)


def text_encoding(open_bytes):
    """Choose an encoding for a text source, refusing content that is not text.

    A BOM decides on its own. Otherwise the file is decoded once, incrementally,
    in CHUNK_BYTES chunks: valid UTF-8 is read as UTF-8 and anything else falls
    back to Windows-1252, which cannot fail and is what Excel writes on this
    machine. The reader is told in the catalog when that fallback was used,
    because a mangled name is a data question, not a reason to refuse the file.
    A NUL byte outside UTF-16 is not text at all, and is refused.
    """
    with open_bytes() as raw:
        head = raw.read(4)
    if head.startswith(codecs.BOM_UTF8):
        bom = 'utf-8-sig'
    elif head.startswith(codecs.BOM_UTF16_LE) or head.startswith(codecs.BOM_UTF16_BE):
        return 'utf-16'
    else:
        bom = ''
    decoder = codecs.getincrementaldecoder('utf-8')()
    valid = True
    with open_bytes() as raw:
        while True:
            chunk = raw.read(CHUNK_BYTES)
            if not chunk:
                break
            if b'\x00' in chunk:
                raise ValueError(M_BINARY)
            if valid:
                try:
                    decoder.decode(chunk)
                except UnicodeDecodeError:
                    valid = False
    if valid:
        try:
            decoder.decode(b'', True)
        except UnicodeDecodeError:
            valid = False
    return (bom or 'utf-8') if valid else 'cp1252'


def head_text(open_bytes, encoding, size=SNIFF_BYTES):
    with open_bytes() as raw:
        head = raw.read(size)
    return head.decode(encoding, 'replace')


def steady_delimiter(lines):
    """The candidate whose count repeats identically on every line, if any.

    That is what a delimiter does: 20 pipes on every row. A comma inside one
    field does not, and neither does a semicolon that appears in a note.
    Returns the steadiest candidate and the count that made it steady.
    """
    chosen, best = '', 0
    for candidate in DELIMITERS:
        counts = [line.count(candidate) for line in lines]
        if counts and counts[0] > best and all(count == counts[0] for count in counts):
            chosen, best = candidate, counts[0]
    return chosen, best


def sniff_dialect(text, suffix):
    """The delimiter a text file is written with, and the dialect to read it.

    csv.Sniffer answers first, because it settles quoting at the same time.
    It is overruled only by a candidate that is strictly steadier than the one
    it chose, because its own preference table puts the comma ahead of the
    pipe: on an FEC bulk row that holds one name like "Doe, Jane" and twenty
    pipes, Sniffer returns the comma and the row reads as two fields. When
    Sniffer cannot answer at all, the steadiest candidate stands on its own.
    """
    lines = [line for line in text.splitlines()[:20] if line.strip()]
    if suffix == '.tsv' and any('\t' in line for line in lines[:1]):
        return type('tabs', (csv.excel,), {'delimiter':'\t'})
    chosen, best = steady_delimiter(lines)
    try:
        dialect = csv.Sniffer().sniff(text, delimiters=''.join(DELIMITERS))
    except csv.Error:
        dialect = None
    if dialect is not None:
        counts = [line.count(dialect.delimiter) for line in lines]
        steady = bool(counts) and counts[0] > 0 and all(count == counts[0] for count in counts)
        if not chosen or chosen == dialect.delimiter or (steady and counts[0] >= best):
            return dialect
        return type('steadier', (csv.excel,), {'delimiter':chosen})
    if not chosen:
        totals = {candidate:sum(line.count(candidate) for line in lines) for candidate in DELIMITERS}
        chosen = (max(totals, key=lambda candidate: totals[candidate]) if any(totals.values())
                  else ('\t' if suffix == '.tsv' else ','))
    return type('sniffed', (csv.excel,), {'delimiter':chosen})


def header_like(fields):
    """True when a first line reads as column names rather than as data."""
    values = [str(field).strip() for field in fields]
    return bool(values) and all(HEADER_FIELD.fullmatch(value) for value in values)


def cell_text(value):
    """One workbook cell as the string a CSV would have carried."""
    if value is None:
        return ''
    if value is True:
        return 'TRUE'
    if value is False:
        return 'FALSE'
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and math.isfinite(value) else repr(value)
    if isinstance(value, datetime):
        return (value.date().isoformat()
                if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0)
                else value.isoformat())
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (date_type, time_type)):
        return value.isoformat()
    return str(value)


def json_text(value):
    """One JSON field as a cell: a nested value keeps its own JSON text."""
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def read_header_file(path):
    """One line of CSV column names, from the header file the reader gave."""
    path = Path(path)
    try:
        if not path.is_file():
            raise ValueError(M_PATH(str(path), 'no such file'))
        if path.stat().st_size > 1_000_000:
            raise ValueError(M_PATH(str(path), 'larger than 1 MB'))
        raw = path.read_bytes()
    except PermissionError as error:
        raise ValueError(M_PATH(str(path), 'permission denied')) from error
    except OSError as error:
        raise ValueError(M_PATH(str(path), 'no such file')) from error
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    line = next((item for item in text.splitlines() if item.strip()), '')
    fields = [field.strip() for field in next(csv.reader([line]), [])]
    if not fields or not any(fields):
        raise ValueError(M_PATH(str(path), 'it holds no column names'))
    return fields


def bulk_headers(stems, header_path=''):
    """The header row for a bulk file: given, found on disk, or bundled.

    More than one name can carry the type: the commission ships its individual
    contributions as itcont.txt inside indiv26.zip, so one name describes the
    content and the other the layout. Both are known here, and either may be
    the one a reader's own copy was renamed to, so each is tried in turn.
    """
    if header_path:
        return read_header_file(header_path), 'header_path'
    name = next((found for found in (bulk_type(stem) for stem in stems) if found), '')
    if not name:
        raise ValueError(M_HEADER)
    try:
        found = sorted(INPUTS.glob(f'**/{name}_header_file.csv'))
    except OSError:
        found = []
    if found:
        return read_header_file(found[0]), 'inputs'
    return list(BULK_HEADERS[name]), 'bundled'


@contextmanager
def delimited_reader(path, record):
    """CSV, TSV and plain text: one header row, then rows as they are read."""
    suffix = Path(str(record.get('name') or path.name)).suffix.lower()

    def open_bytes():
        return path.open('rb')

    encoding = text_encoding(open_bytes)
    dialect = sniff_dialect(head_text(open_bytes, encoding), suffix)
    warnings = [M_CP1252] if encoding == 'cp1252' else []
    meta = {'encoding':encoding, 'delimiter':dialect.delimiter, 'header_source':'row', 'sheets':[]}
    with path.open('r', encoding=encoding, newline='') as handle:
        reader = csv.reader(handle, dialect=dialect, strict=True)
        try:
            headers = next(reader, [])
        except csv.Error as error:
            raise ValueError(M_READ(reader.line_num)) from error
        if suffix == '.txt' and dialect.delimiter == '|' and not header_like(headers):
            raise ValueError(M_HEADER)

        def rows():
            try:
                for values in reader:
                    yield reader.line_num, values
            except csv.Error as error:
                raise ValueError(M_READ(reader.line_num)) from error

        yield Reader(headers, rows(), lambda: handle.buffer.tell(), meta, warnings)


@contextmanager
def bulk_reader(path, record):
    """An FEC bulk file: pipe-delimited, no header row, header from elsewhere."""
    name = str(record.get('name') or path.name)
    if Path(name).suffix.lower() == '.zip':
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            raise ValueError(M_ZIP_READ) from error
        with archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            chosen = [item for item in members if item.filename.lower().endswith(('.txt', '.csv'))]
            if len(chosen) != 1:
                raise ValueError(M_ZIP([item.filename for item in members]))
            member = chosen[0]
            if member.file_size > MAX_BYTES:
                raise ValueError(bytes_message())
            with bulk_rows(lambda: archive.open(member), (Path(member.filename).stem, Path(name).stem),
                           record, int(member.file_size)) as reader:
                yield reader
        return
    with bulk_rows(lambda: path.open('rb'), (Path(name).stem,), record, path.stat().st_size) as reader:
        yield reader


class BoundedBytes(io.RawIOBase):
    """A byte stream that refuses to hand out more than `limit` bytes.

    A zip member's declared size is the only gate before extraction; a lying
    header could otherwise stream past MAX_BYTES into one unbounded csv line.
    The stored source files are already capped on disk, so this only matters
    for what is decompressed on the way out of an archive.
    """

    def __init__(self, raw, limit):
        super().__init__()
        self._raw, self._limit, self._count = raw, int(limit), 0

    def readable(self):
        return True

    def readinto(self, buffer):
        chunk = self._raw.read(len(buffer))
        if not chunk:
            return 0
        self._count += len(chunk)
        if self._count > self._limit:
            raise ValueError(bytes_message())
        buffer[:len(chunk)] = chunk
        return len(chunk)

    def tell(self):
        return self._count

    def close(self):
        try:
            self._raw.close()
        finally:
            super().close()


@contextmanager
def bulk_rows(open_bytes, stems, record, size):
    """The rows of one bulk file or zip member, under a resolved header."""
    encoding = text_encoding(open_bytes)
    headers, header_source = bulk_headers(stems, record.get('header_path') or '')
    warnings = [f'Header taken from {header_source}.', *bulk_warnings()]
    if encoding == 'cp1252':
        warnings.append(M_CP1252)
    meta = {'encoding':encoding, 'delimiter':'|', 'header_source':header_source, 'sheets':[]}
    raw = io.BufferedReader(BoundedBytes(open_bytes(), MAX_BYTES))
    # QUOTE_NONE because bulk rows are not quoted: an apostrophe or an inch
    # mark in an employer name is a character there, not an opening quote.
    stream = io.TextIOWrapper(raw, encoding=encoding, newline='')
    reader = csv.reader(stream, delimiter='|', quoting=csv.QUOTE_NONE)

    def rows():
        try:
            for values in reader:
                yield reader.line_num, values
        except csv.Error as error:
            raise ValueError(M_READ(reader.line_num)) from error

    def tell():
        try:
            return int(raw.tell())
        except (OSError, ValueError):
            return size

    try:
        yield Reader(headers, rows(), tell, meta, warnings)
    finally:
        try:
            stream.detach()
        except ValueError:
            pass
        raw.close()


COMMITTEE_KEYS = frozenset(COMMITTEE_FIELDS)
AMOUNT_KEYS = frozenset(AMOUNT_FIELDS) | frozenset(name for _, amounts, _ in SCHEMAS for name in amounts)
DATE_KEYS = frozenset(DATE_FIELDS) | frozenset(name for _, _, dates in SCHEMAS for name in dates)


def M_NO_SHEET(sheets):
    return f'No worksheet in this workbook has a Committee ID column. Sheets: {", ".join(sheets)}.'


def header_row(rows, limit=50):
    """(row index, header cells) of the first header-like row.

    Exports and Mentat's own workbooks put a title, a note and a source line
    above the header, so the header is the first row (within `limit`) whose
    cells name a committee column; failing that, the first row with three or
    more filled cells; failing that, the first row with anything in it.
    """
    wide = first = None
    for index, values in enumerate(rows, 1):
        if index > limit:
            break
        cells = [cell_text(value) for value in values]
        while cells and not cells[-1].strip():
            cells.pop()
        filled = [cell for cell in cells if cell.strip()]
        if not filled:
            continue
        if {key(cell) for cell in filled} & COMMITTEE_KEYS:
            return index, cells
        if wide is None and len(filled) >= 3:
            wide = (index, cells)
        if first is None:
            first = (index, cells)
    return wide or first or (0, [])


def sheet_grade(columns):
    """How much of a table a header describes: 3 dated amounts, 2 aggregates, 1 ids only, 0 none."""
    keys = set(columns)
    if not keys & COMMITTEE_KEYS:
        return 0
    if keys & AMOUNT_KEYS and keys & DATE_KEYS:
        return 3
    if 'net_included_amount' in keys:
        return 2
    return 1


def calamine_available():
    """python-calamine (a Rust reader) parses this workbook ~10x faster than openpyxl."""
    try:
        import python_calamine  # noqa: F401
    except ImportError:
        return False
    return True


def choose_sheets(record, sheets, survey):
    """The worksheets to read as one table, or the refusal that names them.

    survey maps each sheet name to (header_index, headers, columns, grade,
    height). Naming a `sheet` reads only that one; otherwise the best-graded
    header wins, ties by height then workbook order, and every sheet whose
    keyed header is identical to the winner's is read after it.
    """
    chosen = str(record.get('sheet') or '')
    if chosen and chosen not in sheets:
        raise ValueError(M_SHEET(chosen, sheets))
    if chosen:
        return [chosen]
    best = max(sheets, key=lambda name: (survey[name][3], survey[name][4], -sheets.index(name)))
    if survey[best][3] == 0:
        raise ValueError(M_NO_SHEET(sheets))
    return [name for name in sheets if survey[name][2] == survey[best][2]]


def table_headers(group, survey):
    headers = list(survey[group[0]][1])
    if not headers:
        raise ValueError(M_EMPTY)
    if len(headers) > MAX_COLUMNS:
        raise ValueError(columns_message())
    return headers


def table_meta(record, sheets, group):
    meta = {'encoding':'', 'delimiter':'', 'header_source':'row', 'sheets':sheets,
            'sheet':group[0], 'sheets_read':list(group)}
    warnings = ([f'Read {len(group)} worksheets with the same columns: {", ".join(group)}.']
                if len(group) > 1 else [])
    return meta, warnings


@contextmanager
def calamine_reader(path, record):
    """The worksheets of an .xlsx/.xlsm through python-calamine.

    Every sheet gets a 50-row survey for its header; only the sheets chosen as
    the table are converted in full, one at a time. Paul's 26-sheet industry
    workbook reads in a few seconds this way; openpyxl needed 16.
    """
    from python_calamine import CalamineWorkbook
    try:
        book = CalamineWorkbook.from_path(str(path))
    except Exception as error:  # calamine raises its own exception classes
        raise ValueError(M_XLSX_READ) from error
    sheets = list(book.sheet_names)
    record['sheets'] = sheets
    chosen = str(record.get('sheet') or '')
    if chosen and chosen not in sheets:
        raise ValueError(M_SHEET(chosen, sheets))
    # A 50-row survey per sheet is close to free in calamine (the XML is parsed
    # in Rust; only those rows become Python objects), while converting a whole
    # 39,000-row aggregate sheet that is then discarded costs seconds. So every
    # sheet is surveyed cheaply and only the chosen table is converted in full.
    survey = {}
    for name in sheets:
        if chosen and name != chosen:
            survey[name] = (0, [], [], 0, 0)
            continue
        try:
            sheet = book.get_sheet_by_name(name)
            head = sheet.to_python(nrows=50, skip_empty_area=False)
            height = int(getattr(sheet, 'height', 0) or len(head))
        except Exception as error:
            raise ValueError(M_XLSX_READ) from error
        index, headers = header_row(head)
        columns = [key(item) for item in headers]
        survey[name] = (index, headers, columns, sheet_grade(columns), height)
    group = choose_sheets(record, sheets, survey)
    headers = table_headers(group, survey)
    size = int(record.get('bytes') or 0)
    meta, warnings = table_meta(record, sheets, group)

    def rows():
        for name in group:
            first = survey[name][0]
            index = first
            try:
                data = book.get_sheet_by_name(name).to_python(skip_empty_area=False)
                for index, row in enumerate(data, 1):
                    if index <= first:
                        continue
                    cells = [cell_text(value) for value in row]
                    while len(cells) > len(headers) and not cells[-1].strip():
                        cells.pop()
                    yield index, cells
            except ValueError:
                raise
            except Exception as error:
                raise ValueError(M_READ(index)) from error
            del data

    yield Reader(headers, rows(), lambda: size, meta, warnings)


@contextmanager
def workbook_reader(path, record):
    """An .xlsx/.xlsm through calamine when it is installed, else openpyxl."""
    engine = calamine_reader if calamine_available() else openpyxl_reader
    record['engine'] = 'calamine' if engine is calamine_reader else 'openpyxl'
    with engine(path, record) as reader:
        yield reader


@contextmanager
def openpyxl_reader(path, record):
    """The openpyxl fallback: the worksheets of an .xlsx/.xlsm, read cell by cell.

    With no worksheet named, every sheet is surveyed (its first rows only): the
    best-graded header wins, ties going to the sheet with more rows, and every
    sheet whose header is identical to the winner's is read after it as one
    table. A Mentat industry workbook therefore reads its cycle sheets
    together and skips its overview, summary and list sheets; a workbook with
    one data sheet reads that one. Naming a worksheet reads only that one.
    """
    import openpyxl
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        raise ValueError(M_XLSX_READ) from error
    try:
        sheets = list(book.sheetnames)
        # Written onto the record before anything can fail, so a refused
        # workbook still tells the reader which worksheets it has.
        record['sheets'] = sheets
        chosen = str(record.get('sheet') or '')
        if chosen and chosen not in sheets:
            raise ValueError(M_SHEET(chosen, sheets))
        survey = {}
        for name in sheets:
            sheet = book[name]
            index, headers = header_row(sheet.iter_rows(values_only=True))
            columns = [key(item) for item in headers]
            survey[name] = (index, headers, columns, sheet_grade(columns), int(sheet.max_row or 0))
        if chosen:
            group = [chosen]
        else:
            best = max(sheets, key=lambda name: (survey[name][3], survey[name][4], -sheets.index(name)))
            if survey[best][3] == 0:
                raise ValueError(M_NO_SHEET(sheets))
            group = [name for name in sheets if survey[name][2] == survey[best][2]]
        headers = list(survey[group[0]][1])
        if not headers:
            raise ValueError(M_EMPTY)
        if len(headers) > MAX_COLUMNS:
            raise ValueError(columns_message())
        size = int(record.get('bytes') or 0)
        meta = {'encoding':'', 'delimiter':'', 'header_source':'row', 'sheets':sheets,
                'sheet':group[0], 'sheets_read':list(group)}
        warnings = ([f'Read {len(group)} worksheets with the same columns: {", ".join(group)}.']
                    if len(group) > 1 else [])

        def rows():
            for name in group:
                first = survey[name][0]
                index = first
                try:
                    for index, row in enumerate(book[name].iter_rows(values_only=True), 1):
                        if index <= first:
                            continue
                        cells = [cell_text(value) for value in row]
                        # Trailing blanks past the header are the sheet's used
                        # range, not extra values: dropping them keeps a whole
                        # worksheet from being reported as ragged rows.
                        while len(cells) > len(headers) and not cells[-1].strip():
                            cells.pop()
                        yield index, cells
                except ValueError:
                    raise
                except Exception as error:
                    raise ValueError(M_READ(index)) from error

        yield Reader(headers, rows(), lambda: size, meta, warnings)
    finally:
        book.close()

@contextmanager
def json_reader(path, record):
    """An openFEC export or a list of objects, read whole under its own cap."""
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise ValueError(json_size_message())

    def open_bytes():
        return path.open('rb')

    encoding = text_encoding(open_bytes)
    try:
        document = json.loads(path.read_text(encoding=encoding))
    except (ValueError, UnicodeDecodeError, OSError) as error:
        raise ValueError(M_JSON_SHAPE) from error
    items = document.get('results') if isinstance(document, dict) else document
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(M_JSON_SHAPE)
    if not items:
        raise ValueError(M_EMPTY)
    headers, seen = [], set()
    for item in items:
        for name in item:
            if name not in seen:
                seen.add(name)
                headers.append(str(name))
    if len(headers) > MAX_COLUMNS:
        raise ValueError(columns_message())
    warnings = [M_CP1252] if encoding == 'cp1252' else []
    meta = {'encoding':encoding, 'delimiter':'', 'header_source':'row', 'sheets':[]}

    def rows():
        for number, item in enumerate(items, 1):
            yield number, [json_text(item.get(name)) for name in headers]

    yield Reader(headers, rows(), lambda: size, meta, warnings)


@contextmanager
def open_source(path, record):
    """The reader for one stored source, chosen by the format on its record."""
    readers = {'xlsx':workbook_reader, 'json':json_reader, 'fec_bulk':bulk_reader}
    fmt = record.get('format') or source_format(record.get('name', ''), record.get('header_path', ''))
    with readers.get(fmt, delimited_reader)(path, record) as reader:
        yield reader


def drive_roots():
    """Every drive root that answers, for a picker that starts from nothing."""
    if os.name != 'nt':
        return ['/']
    roots = []
    for letter in string.ascii_uppercase:
        root = f'{letter}:\\'
        try:
            if Path(root).is_dir():
                roots.append(root)
        except OSError:
            continue
    return roots


def shortcuts():
    """The folders a reader actually keeps evidence in, when they exist."""
    home = Path.home()
    found = []
    for label, candidates in (('Downloads', (home / 'Downloads', home / 'OneDrive' / 'Downloads')),
                              ('Mentat inputs', (INPUTS,)),
                              ('Desktop', (home / 'Desktop', home / 'OneDrive' / 'Desktop'))):
        for candidate in candidates:
            try:
                if candidate.is_dir():
                    found.append({'name':label, 'path':str(candidate)})
                    break
            except OSError:
                continue
    return found


def directory_entries(directory, limit=2000):
    """One folder's children, folders first, with what the reader may open."""
    rows, truncated = [], False
    with os.scandir(directory) as scan:
        for entry in scan:
            if len(rows) >= limit:
                truncated = True
                break
            try:
                is_dir = entry.is_dir()
                info = entry.stat()
                size = None if is_dir else int(info.st_size)
                modified = datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
            except OSError:
                is_dir, size, modified = False, None, None
            suffix = Path(entry.name).suffix.lower()
            rows.append({'name':entry.name, 'path':str(Path(directory) / entry.name),
                         'kind':'dir' if is_dir else 'file', 'size':size, 'modified':modified,
                         'supported':(not is_dir) and suffix in ACCEPTED_SUFFIXES})
    rows.sort(key=lambda row: (row['kind'] != 'dir', row['name'].lower()))
    return rows, truncated


def browse(path=''):
    """List one folder on this computer, for the console's own file picker.

    Read-only, and answered only over an authenticated POST. Nothing is hidden
    by a hidden or system attribute: this is Paul's own machine, and a file he
    cannot see in the picker is a file he cannot import.
    """
    text = str(path or '').strip().strip('"')
    if not text:
        return {'path':'', 'parent':None, 'roots':drive_roots(), 'shortcuts':shortcuts(),
                'entries':[], 'truncated':False}
    try:
        chosen = Path(text).expanduser().resolve()
        info = os.stat(chosen)
    except PermissionError as error:
        raise ValueError(M_PATH(text, 'permission denied')) from error
    except (OSError, ValueError) as error:
        raise ValueError(M_PATH(text, 'no such file')) from error
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(M_PATH(text, 'it is not a folder'))
    try:
        entries, truncated = directory_entries(chosen)
    except PermissionError as error:
        raise ValueError(M_PATH(text, 'permission denied')) from error
    except OSError as error:
        raise ValueError(M_PATH(text, 'it could not be listed')) from error
    parent = str(chosen.parent) if chosen.parent != chosen else None
    return {'path':str(chosen), 'parent':parent, 'roots':drive_roots(), 'shortcuts':shortcuts(),
            'entries':entries, 'truncated':truncated}


class Store:
    """CSV imports kept under the console data root, one directory per file."""

    def __init__(self, data_root, evict=True):
        # evict=False for a read-only preview: it shares the data root with the
        # console that owns it, and a preview must not delete the imports the
        # reader chose in the main console. Sweeping abandoned uploads and old
        # failures is safe from either and stays on.
        self.data_root = Path(data_root)
        self.evicts = bool(evict)
        self.root = self.data_root / STORE_DIRECTORY
        self.incoming = self.root / 'incoming'
        self.incoming.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        self.incoming = self.incoming.resolve()
        # identifier -> ((size, mtime_ns) of rows.sqlite, committee figures)
        self._figures = {}
        self.sweep()

    def directory(self, identifier):
        return self.root / identifier

    def record_path(self, identifier):
        return self.directory(identifier) / 'import.json'

    def source_path(self, identifier, suffix=''):
        """The stored source, kept under the suffix it arrived with."""
        directory = self.directory(identifier)
        if suffix:
            return directory / ('source' + suffix)
        existing = sorted(path for path in directory.glob('source.*') if path.is_file())
        return existing[0] if existing else directory / 'source.csv'

    def database_path(self, identifier):
        return self.directory(identifier) / 'rows.sqlite'

    def begin_upload(self, name, sheet='', header_path=''):
        return Upload(self, name, sheet=sheet, header_path=header_path)

    def refusal(self, name, message, source_kind='path', source_path='', sheet='', header_path=''):
        """Record a refusal the page can poll, the way a failed read is polled.

        A path import can be refused before a byte is read - the file is not
        there, it is a folder, it is too large, it is a type this reader does
        not open - and the page is watching one channel, the JOB. Recording the
        refusal as a failed import keeps the reader's status line in one place
        instead of splitting 'could not start' from 'could not read', and the
        hourly sweep drops it like any other failure.
        """
        suffix = Path(str(name or '')).suffix.lower()
        identifier = 'csv-' + hashlib.sha256(
            ('refused\x00' + str(source_path or name) + '\x00' + str(sheet or '')).encode('utf-8')).hexdigest()[:32]
        stamp = time.time()
        value = {'id':identifier, 'name':str(name or ''), 'state':'failed', 'bytes':0, 'bytes_read':0,
                 'rows_read':0, 'message':str(message), 'started_at':stamp, 'finished_at':stamp,
                 'catalog':None, 'columns':[], 'sha256':'', 'imported_at':'', 'pid':os.getpid(),
                 'format':source_format(name, header_path) if suffix in ACCEPTED_SUFFIXES else '',
                 'source_kind':source_kind, 'source_path':str(source_path or ''), 'sheet':str(sheet or ''),
                 'sheets':[], 'encoding':'', 'delimiter':'', 'header_source':'', 'suffix':suffix,
                 'header_path':str(header_path or '')}
        self.directory(identifier).mkdir(parents=True, exist_ok=True)
        atomic_json(self.record_path(identifier), value)
        return job_view(value)

    def import_path(self, path, sheet='', header_path=''):
        """Read a file the reader chose by path, with no browser upload at all.

        The answer is a JOB either way: a file that can be read starts a reader
        thread, and one that cannot is recorded as a refusal under the same
        identifier shape, so the page polls one thing and the console's own
        message - never a Python exception - is what the reader is shown.
        """
        text = str(path or '').strip().strip('"')
        try:
            return self.read_chosen_path(text, sheet, header_path)
        except ValueError as error:
            return self.refusal(Path(text).name or text, str(error), source_path=text,
                                sheet=sheet, header_path=header_path)

    def read_chosen_path(self, text, sheet='', header_path=''):
        """Copy the chosen file into the store and start reading it.

        The copy runs in the same CHUNK_BYTES steps an upload arrives in, so
        the import reads a still snapshot rather than a file being written
        underneath it, and the identifier is the digest of exactly the bytes
        that were read.
        """
        if not text:
            raise ValueError(M_PATH(text, 'no such file'))
        try:
            chosen = Path(text).expanduser()
            info = os.stat(chosen)
        except PermissionError as error:
            raise ValueError(M_PATH(text, 'permission denied')) from error
        except (OSError, ValueError) as error:
            raise ValueError(M_PATH(text, 'no such file')) from error
        if stat.S_ISDIR(info.st_mode):
            raise ValueError(M_PATH(text, 'it is a folder'))
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(M_PATH(text, 'no such file'))
        suffix = accepted_suffix(chosen.name)
        if info.st_size > MAX_BYTES:
            raise ValueError(M_PATH(text, f'larger than {MAX_BYTES // 1_000_000} MB'))
        header_path = str(header_path or '')
        if header_path:
            read_header_file(header_path)
        sheet = str(sheet or '')
        target = self.incoming / (uuid.uuid4().hex + suffix)
        digest, total, blank = hashlib.sha256(), 0, True
        try:
            try:
                with chosen.open('rb') as raw, target.open('wb') as handle:
                    while True:
                        chunk = raw.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError(M_PATH(text, f'larger than {MAX_BYTES // 1_000_000} MB'))
                        if blank and chunk.strip():
                            blank = False
                        digest.update(chunk)
                        handle.write(chunk)
            except PermissionError as error:
                raise ValueError(M_PATH(text, 'permission denied')) from error
            except OSError as error:
                raise ValueError(M_PATH(text, 'it could not be read')) from error
            if blank:
                raise ValueError(M_EMPTY)
            return self.adopt(import_id(digest, sheet), chosen.name, target, total, digest.hexdigest(),
                              suffix=suffix, sheet=sheet, source_kind='path',
                              source_path=str(chosen), header_path=header_path)
        except BaseException:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def read_record(self, identifier, attempts=5):
        """Read a record, tolerating a reader that loses a race with a writer.

        import.json is rewritten while it is being read: the ingest reports its
        progress into it every thousand rows and the page polls it twice a
        second. On Windows the reader's open can lose that race and come back
        with a sharing violation, and treating that as 'this import is not
        there' told the reader their file was gone while it sat on disk being
        read. A file that is genuinely absent still answers at once.
        """
        path = self.record_path(identifier)
        for attempt in range(attempts):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return None
            except (OSError, ValueError):
                if attempt == attempts - 1:
                    return None
                time.sleep(0.02 * (attempt + 1))
                continue
            return value if isinstance(value, dict) else None
        return None

    def reading(self, identifier, record):
        """True only while this process still has a live reader for the import."""
        if record.get('pid') != os.getpid():
            return False
        with _ACTIVE_LOCK:
            thread = _ACTIVE.get(str(self.directory(identifier)))
        return bool(thread is not None and thread.is_alive())

    def record(self, identifier):
        """The stored record, with a reader that died recorded as interrupted."""
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise ValueError(M_GONE(identifier))
        value = self.read_record(identifier)
        if value is None or not self.directory(identifier).is_dir():
            raise ValueError(M_GONE(identifier))
        if value.get('state') == 'reading' and not self.reading(identifier, value):
            value['state'] = 'failed'
            value['message'] = M_INTERRUPTED
            value['finished_at'] = time.time()
            try:
                atomic_json(self.record_path(identifier), value)
            except OSError:
                pass
        return value

    def job(self, identifier):
        return job_view(self.record(identifier))

    def wait(self, identifier, timeout=None):
        """Block until the import stops reading. For the legacy route and tests."""
        with _ACTIVE_LOCK:
            thread = _ACTIVE.get(str(self.directory(identifier)))
        if thread is not None:
            thread.join(timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            current = self.job(identifier)
            if current['state'] != 'reading':
                return current
            if deadline is not None and time.monotonic() >= deadline:
                return current
            time.sleep(0.01)

    def ready(self, identifier):
        value = self.record(identifier)
        if value.get('state') == 'failed':
            raise ValueError(value.get('message') or M_GONE(identifier))
        if value.get('state') != 'ready' or not value.get('catalog'):
            raise ValueError(M_BUSY)
        # A re-read leaves the record READY on purpose (see reread()), so the
        # import survives a restart or a failure. The reader still must not be
        # handed a catalog that is about to be replaced under them.
        if _active_worker(self.directory(identifier)) is not None:
            raise ValueError(M_BUSY)
        return value

    def sources(self):
        records = []
        for path in sorted(self.root.glob('csv-*')):
            if not path.is_dir():
                continue
            value = self.read_record(path.name)
            if value and value.get('state') == 'ready' and value.get('catalog'):
                records.append(value)
        records.sort(key=lambda value: (str(value.get('imported_at') or ''), str(value.get('id') or '')), reverse=True)
        running = self.refresh([value for value in records if self.rereadable(value)])
        listed = []
        for value in records:
            catalog = stored_catalog(value,
                                     state='reading' if value['id'] in running else 'ready',
                                     reread=value['id'] in running,
                                     stale=value['id'] not in running
                                           and int(value.get('catalog_version') or 0) < CATALOG_VERSION)
            # An import being re-read has its database rewritten under it, and
            # the page cannot open it until the re-read ends, so it goes without.
            if value['id'] not in running:
                figures = self.committee_figures(value['id'])
                for item in catalog.get('committees') or []:
                    item.update(figures.get(item.get('id'), {'receipts':None, 'receipts_channel':'',
                                                             'receipts_by_cycle':{}}))
            listed.append(catalog)
        return listed

    def committee_figures(self, identifier):
        """Each committee's lead figure, over all cycles and per cycle, for the rail.

        Console redesign build 06 (board B19): the picker rail ranks an import's
        PACs by the number its pane leads with, which is the largest channel
        total the committee has (evidence.js mainFigure), and shows it at the
        right of the row. `receipts` is that total over every cycle,
        `receipts_channel` names the channel, and `receipts_by_cycle` holds the
        largest channel total within each cycle the committee has supported
        rows in. A committee or cycle with no supported row has no figure
        (None, or no key), exactly where its pane shows a dash.

        Read from the import's own sqlite `totals` table (one row per
        committee, cycle and channel), so the listing rebuilds no row, and
        cached against the database's size and modification time.
        """
        database = self.database_path(identifier)
        try:
            stat = database.stat()
        except OSError:
            return {}
        stamp = (stat.st_size, stat.st_mtime_ns)
        cached = self._figures.get(identifier)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        try:
            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute('SELECT committee_id, cycle, channel, amount FROM totals').fetchall()
        except sqlite3.Error:
            return {}
        overall = defaultdict(lambda: defaultdict(Decimal))
        by_cycle = defaultdict(lambda: defaultdict(lambda: defaultdict(Decimal)))
        for committee, row_cycle, channel, amount in rows:
            name = committee or 'unassigned'
            value = Decimal(amount)
            overall[name][channel] += value
            if row_cycle:
                by_cycle[name][row_cycle][channel] += value
        figures = {}
        for name, channels in overall.items():
            # Ties go to the channel first by name, as the pane's stable sort of
            # the name-ordered totals does.
            channel, amount = max(sorted(channels.items()), key=lambda item: item[1])
            figures[name] = {'receipts':float(amount), 'receipts_channel':channel,
                             'receipts_by_cycle':{row_cycle:float(max(amounts.values()))
                                                  for row_cycle, amounts in sorted(by_cycle[name].items())}}
        self._figures[identifier] = (stamp, figures)
        return figures

    def rereadable(self, value):
        """True when this catalog predates the current rules and the file is still here.

        An import whose stored source was evicted cannot be re-read at all, and
        one whose re-read already failed under this version is not retried on
        every listing; both are reported stale instead.
        """
        if int(value.get('catalog_version') or 0) >= CATALOG_VERSION:
            return False
        if int(value.get('reread_failed') or 0) >= CATALOG_VERSION:
            return False
        return self.source_path(value.get('id', ''), value.get('suffix', '')).is_file()

    def refresh(self, records):
        """Queue a re-read of every import whose catalog predates the current rules.

        Catalogs are built once, at ingest, so a rule change reaches an import
        already on disk only by reading it again. The bytes are the same, so
        the id is the same and the reader sees the import they already had
        gain the new fields rather than a second copy of it. One worker at a
        time, the same as an ingest; a read-only console never starts one,
        because it shares its data root with the console that owns these
        imports. Returns the ids the page should show as re-reading. This
        never blocks: the listing is answered from the catalogs on disk while
        the worker runs behind it.
        """
        if not records or not self.evicts:
            return set()
        with _ACTIVE_LOCK:
            for finished in [running for running, thread in _ACTIVE.items() if not thread.is_alive()]:
                _ACTIVE.pop(finished, None)
            if _ACTIVE:
                return {value['id'] for value in records
                        if str(self.directory(value['id'])) in _ACTIVE}
            worker = threading.Thread(target=self.reread_all, args=(list(records),), daemon=True,
                                      name='evidence-csv-reread')
            for value in records:
                _ACTIVE[str(self.directory(value['id']))] = worker
            worker.start()
        return {value['id'] for value in records}

    def reread_all(self, records):
        """Re-read each stale import in turn. No exception may leave this thread."""
        for value in records:
            identifier = str(value.get('id') or '')
            try:
                self.reread(identifier, value)
            except Exception:
                pass
            finally:
                with _ACTIVE_LOCK:
                    _ACTIVE.pop(str(self.directory(identifier)), None)

    def reread(self, identifier, value):
        """Read one stored import again under the current rules, in place.

        The import stays exactly as it is until the new catalog is complete:
        the new rows database is built BESIDE the old one and swapped in only
        once it is whole, the record keeps saying ready with the catalog it
        already has, and a re-read that fails leaves the reader the import
        they had rather than deleting its bytes the way a failed first ingest
        does. The reader options come off the record, so the same worksheet,
        header file and format that produced this import produce it again.
        The import date is kept too: the console re-reading a file is not the
        reader importing it again, and the picker is ordered by that date.
        """
        working = dict(value)
        # The record's `sheet` is the worksheet the reader ENDED UP on, not the
        # one it was asked for: read_source overwrites it from the reader's own
        # metadata. Replaying it would narrow a workbook that was read as
        # several compatible cycle sheets down to the first of them - measured
        # on the Crypto workbook, 6,343 rows became 1,063. The identifier says
        # which it was, because a chosen worksheet is folded into it
        # (import_id): an id that is the file digest alone was read with no
        # worksheet chosen, and must be read that way again.
        digest = str(value.get('sha256') or '')
        working['sheet'] = '' if digest and identifier == 'csv-' + digest[:32] else str(value.get('sheet') or '')
        temporary = self.directory(identifier) / 'rows.reread.sqlite'
        try:
            catalog, columns = self.read_source(identifier, working, database=temporary)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            self.amend(identifier, {'reread_failed':CATALOG_VERSION,
                                    'reread_message':f'{type(error).__name__}: {error}'})
            return
        stored = int((value.get('catalog') or {}).get('row_count') or 0)
        if stored and catalog['row_count'] != stored:
            # The same bytes read with the same options give the same rows, so a
            # different count means the options were NOT reproduced. Refuse the
            # swap rather than replace a whole import with a partial one; the
            # entry stays listed, stale, with what it already had. Relax this
            # deliberately if a future CATALOG_VERSION changes WHICH rows are
            # read rather than only how they are classified.
            temporary.unlink(missing_ok=True)
            self.amend(identifier, {'reread_failed':CATALOG_VERSION,
                                    'reread_message':f're-read produced {catalog["row_count"]:,} rows, '
                                                     f'not the {stored:,} this import was stored with'})
            return
        for attempt in range(6):
            try:
                os.replace(temporary, self.database_path(identifier))
                break
            except OSError:
                # A page reading the old database holds it open for a moment.
                # Losing the swap is not a failure worth marking: the listing
                # will offer the re-read again.
                if attempt == 5:
                    temporary.unlink(missing_ok=True)
                    return
                time.sleep(0.05 * (attempt + 1))
        catalog['imported_at'] = value.get('imported_at') or catalog['imported_at']
        self.amend(identifier, {'catalog':catalog, 'columns':columns, 'catalog_version':CATALOG_VERSION,
                                'imported_at':catalog['imported_at'], 'reread_failed':0, 'reread_message':'',
                                'rows_read':int(working.get('rows_read') or catalog['row_count']),
                                'bytes_read':int(working.get('bytes_read') or value.get('bytes') or 0)})

    def amend(self, identifier, fields):
        """Merge fields into a record, leaving everything else as it stands."""
        record = self.read_record(identifier)
        if not record:
            return
        record.update(fields)
        try:
            atomic_json(self.record_path(identifier), record)
        except OSError:
            pass

    def catalog(self, identifier):
        return stored_catalog(self.ready(identifier))

    def select(self, identifier, committee='', cycle='', offset=0, limit=200):
        value = self.ready(identifier)
        source = stored_catalog(value)
        offset, limit = page_args(offset, limit)
        committee = committee or (source['committees'][0]['id'] if source['committees'] else '')
        chosen = next((item for item in source['committees'] if item['id'] == committee), None)
        if chosen is None:
            raise ValueError(M_COMMITTEE)
        if cycle and cycle not in source['cycles']:
            raise ValueError(M_CYCLE)
        database = self.database_path(identifier)
        if not database.is_file():
            raise ValueError(M_GONE(identifier))
        stored = '' if committee == 'unassigned' else committee
        where, parameters = 'committee_id = ?', [stored]
        if cycle:
            where, parameters = where + ' AND cycle = ?', parameters + [cycle]
        try:
            with closing(sqlite3.connect(database)) as connection:
                total = connection.execute(f'SELECT COUNT(*) FROM csv_rows WHERE {where}', parameters).fetchone()[0]
                supported = connection.execute(f'SELECT COUNT(*) FROM csv_rows WHERE {where} AND status = ?',
                                               parameters + ['supported']).fetchone()[0]
                excluded = connection.execute(f'SELECT COUNT(*) FROM csv_rows WHERE {where} AND status = ?',
                                              parameters + ['excluded']).fetchone()[0]
                page = connection.execute(f'SELECT line_num, cells FROM csv_rows WHERE {where} ORDER BY row_number LIMIT ? OFFSET ?',
                                          parameters + [limit, offset]).fetchall()
                channels = connection.execute(f'SELECT channel, amount, row_count FROM totals WHERE {where}', parameters).fetchall()
        except sqlite3.Error as error:
            raise ValueError(M_GONE(identifier)) from error
        amounts, counts = defaultdict(Decimal), defaultdict(int)
        for channel, amount, row_count in channels:
            amounts[channel] += Decimal(amount)
            counts[channel] += row_count
        headers = value.get('columns', [])
        columns = [key(item) for item in headers]
        rows = [rebuild(headers, columns, line_number, json.loads(cells)) for line_number,cells in page]
        # FEC filing links (Paul's ruling 2026-09-24): a Mentat workbook row
        # carries its sub_id but not its link, so the link is looked up in the
        # database the workbook was built from - AFTER rebuild(), which stays
        # pure. A row that resolves nowhere is left exactly as it was, and a
        # lookup that fails for any reason costs the page its links, never
        # its rows; the failure goes to the console log.
        try:
            fec_citation.attach(value, rows, columns, industry_of(value.get('name')))
        except Exception:
            try:
                import traceback
                traceback.print_exc()
            except Exception:
                pass
        return {'source':source, 'committee':chosen, 'committees':source['committees'],
                'cycles':source['cycles'], 'selected_cycle':cycle, 'columns':headers,
                'rows':[{name:item for name,item in row.items() if not name.startswith('_')} for row in rows],
                'totals':[{'channel':channel, 'amount':float(amounts[channel]), 'row_count':counts[channel]}
                          for channel in sorted(amounts)],
                'supported_rows':supported, 'excluded_rows':excluded,
                'unsupported_rows':total - supported - excluded,
                'offset':offset, 'limit':limit, 'total_rows':total, 'has_more':offset + limit < total,
                'warnings':list(source.get('warnings') or WARNINGS)}

    def summary(self, identifier, committee='', cycle=''):
        """One committee's channel totals by cycle, top counterparties and counts.

        Read from the import's own sqlite and bounded by that committee's rows.
        The totals table already carries the (cycle, channel) sums, so nothing
        is parsed back for them; only the committee's SUPPORTED rows are
        rebuilt, for the counterparty ranking a pane shows above its row table.
        A cycle in the request narrows the totals, the ranking and the counts,
        and leaves totals_by_cycle whole so the pane can still draw every bar.
        """
        value = self.ready(identifier)
        source = stored_catalog(value)
        committee = committee or (source['committees'][0]['id'] if source['committees'] else '')
        chosen = next((item for item in source['committees'] if item['id'] == committee), None)
        if chosen is None:
            raise ValueError(M_COMMITTEE)
        if cycle and cycle not in source['cycles']:
            raise ValueError(M_CYCLE)
        database = self.database_path(identifier)
        if not database.is_file():
            raise ValueError(M_GONE(identifier))
        stored = '' if committee == 'unassigned' else committee
        scope, parameters = 'committee_id = ?', [stored]
        if cycle:
            scope, parameters = scope + ' AND cycle = ?', parameters + [cycle]
        try:
            with closing(sqlite3.connect(database)) as connection:
                channels = connection.execute('SELECT cycle, channel, amount, row_count FROM totals '
                                              'WHERE committee_id = ?', [stored]).fetchall()
                counted = dict(connection.execute(f'SELECT status, COUNT(*) FROM csv_rows WHERE {scope} '
                                                  'GROUP BY status', parameters).fetchall())
                supported = connection.execute(f'SELECT line_num, cells FROM csv_rows WHERE {scope} '
                                               'AND status = ? ORDER BY row_number',
                                               parameters + ['supported']).fetchall()
        except sqlite3.Error as error:
            raise ValueError(M_GONE(identifier)) from error
        cycle_amounts, cycle_counts = defaultdict(Decimal), defaultdict(int)
        for row_cycle, channel, amount, row_count in channels:
            cycle_amounts[(row_cycle, channel)] += Decimal(amount)
            cycle_counts[(row_cycle, channel)] += row_count
        totals_by_cycle = {}
        for row_cycle in sorted({pair[0] for pair in cycle_amounts if pair[0]}, reverse=True):
            totals_by_cycle[row_cycle] = [
                {'channel':channel, 'amount':float(cycle_amounts[(row_cycle, channel)]),
                 'row_count':cycle_counts[(row_cycle, channel)]}
                for channel in sorted(pair[1] for pair in cycle_amounts if pair[0] == row_cycle)]
        amounts, counts = defaultdict(Decimal), defaultdict(int)
        for (row_cycle, channel), amount in cycle_amounts.items():
            if cycle and row_cycle != cycle:
                continue
            amounts[channel] += amount
            counts[channel] += cycle_counts[(row_cycle, channel)]
        headers = value.get('columns', [])
        columns = [key(item) for item in headers]
        party_amounts, party_rows = defaultdict(Decimal), defaultdict(int)
        for line_number, cells in supported:
            row = rebuild(headers, columns, line_number, json.loads(cells))
            name = row['counterparty'] or BLANK_COUNTERPARTY
            party_amounts[name] += row['_amount'] or Decimal('0')
            party_rows[name] += 1
        ranked = sorted(party_amounts.items(), key=lambda item: (-item[1], item[0]))[:TOP_COUNTERPARTIES]
        return {'source':{'id':source['id'], 'name':source['name'], 'industry':source['industry']},
                'committee':{field:chosen[field] for field in
                             ('id', 'name', 'category', 'category_source', 'rows',
                              'supported_rows', 'excluded_rows')},
                'cycles':list(source['cycles']), 'selected_cycle':cycle,
                'totals_by_cycle':totals_by_cycle,
                'totals':[{'channel':channel, 'amount':float(amounts[channel]),
                           'row_count':counts[channel]} for channel in sorted(amounts)],
                'counterparties':[{'name':name, 'amount':float(amount), 'rows':party_rows[name]}
                                  for name, amount in ranked],
                'counts':{'supported':int(counted.get('supported', 0)),
                          'excluded':int(counted.get('excluded', 0)),
                          'unsupported':int(counted.get('unsupported', 0))}}

    def adopt(self, identifier, name, path, size, digest, suffix='.csv', sheet='',
              source_kind='upload', source_path='', header_path=''):
        """Take a finished upload: dedupe by content, else start reading it."""
        existing = self.read_record(identifier)
        if existing and existing.get('state') == 'ready' and existing.get('catalog'):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return job_view(existing)
        with _ACTIVE_LOCK:
            for stale in [running for running, thread in _ACTIVE.items() if not thread.is_alive()]:
                _ACTIVE.pop(stale, None)
            if _ACTIVE:
                raise ValueError(M_BUSY)
            directory = self.directory(identifier)
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True, exist_ok=True)
            os.replace(path, self.source_path(identifier, suffix))
            value = {'id':identifier, 'name':name, 'state':'reading', 'bytes':int(size), 'bytes_read':0,
                     'rows_read':0, 'message':'', 'started_at':time.time(), 'finished_at':None,
                     'catalog':None, 'columns':[], 'sha256':digest, 'imported_at':'', 'pid':os.getpid(),
                     'format':source_format(name, header_path), 'source_kind':source_kind,
                     'source_path':str(source_path or ''), 'sheet':str(sheet or ''), 'sheets':[],
                     'encoding':'', 'delimiter':'', 'header_source':'', 'suffix':suffix,
                     'header_path':str(header_path or '')}
            atomic_json(self.record_path(identifier), value)
            thread = threading.Thread(target=self.ingest, args=(identifier, value), daemon=True,
                                      name='evidence-csv-' + identifier)
            _ACTIVE[str(directory)] = thread
            thread.start()
        return job_view(value)

    def ingest(self, identifier, value):
        """Read one import to completion. No exception may leave this thread."""
        try:
            catalog, columns = self.read_source(identifier, value)
        except ValueError as error:
            value.update(state='failed', message=str(error), finished_at=time.time())
        except Exception as error:
            value.update(state='failed', message=f'The CSV could not be read: {type(error).__name__}: {error}',
                         finished_at=time.time())
        else:
            value.update(state='ready', message='', finished_at=time.time(), catalog=catalog,
                         columns=columns, imported_at=catalog['imported_at'],
                         catalog_version=CATALOG_VERSION)
        if value['state'] == 'failed':
            # Keep the directory so the page can still read the reason; drop the
            # bytes, which are now only a partial database and a refused file.
            for path in (self.database_path(identifier),
                         self.source_path(identifier, value.get('suffix', ''))):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            atomic_json(self.record_path(identifier), value)
        except OSError:
            pass
        if value['state'] == 'ready' and self.evicts:
            try:
                self.evict()
            except OSError:
                pass
        try:
            self.sweep()
        except OSError:
            pass

    def read_source(self, identifier, value, database=None):
        """Walk the file once into sqlite, accumulating exact totals as it goes.

        `database` writes the rows somewhere other than the import's own, for
        a re-read that must not disturb the database the page is reading.
        """
        source = self.source_path(identifier, value.get('suffix', ''))
        database = Path(database) if database else self.database_path(identifier)
        database.unlink(missing_ok=True)
        names = defaultdict(set)
        committee_rows, committee_supported = defaultdict(int), defaultdict(int)
        committee_excluded = defaultdict(int)
        committee_categories = defaultdict(lambda: defaultdict(int))
        committee_unclassified = set()
        amounts, counts = defaultdict(Decimal), defaultdict(int)
        cycles = set()
        rows_read = unassigned = supported = unsupported = excluded = 0
        header = ''
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('PRAGMA journal_mode=OFF')
            connection.execute('PRAGMA synchronous=OFF')
            # Only the original cells are stored. The columns beside them are
            # the ones a selection filters, counts or orders on; every other
            # field the page shows is recomputed by rebuild() at select time.
            connection.execute('CREATE TABLE csv_rows (row_number INTEGER PRIMARY KEY, line_num INTEGER NOT NULL, '
                               'committee_id TEXT NOT NULL, cycle TEXT NOT NULL, status TEXT NOT NULL, '
                               'channel TEXT NOT NULL, amount TEXT, cells TEXT NOT NULL)')
            connection.execute('CREATE TABLE totals (committee_id TEXT NOT NULL, cycle TEXT NOT NULL, '
                               'channel TEXT NOT NULL, amount TEXT NOT NULL, row_count INTEGER NOT NULL)')
            with open_source(source, value) as reader:
                headers = reader.headers
                columns = [key(item) for item in headers]
                if not headers or len(headers) > MAX_COLUMNS or not all(columns) or len(set(columns)) != len(columns):
                    raise ValueError(columns_message())
                if not set(columns).intersection(COMMITTEE_FIELDS):
                    raise ValueError(M_COMMITTEE_COLUMN)
                header = category_header(headers)
                # How the file turned out to be written is recorded before the
                # first row, so a reader watching the progress line sees the
                # encoding and delimiter that were chosen, not only the count.
                value.update(reader.meta)
                extra_warnings = list(reader.warnings)
                batch = []
                for line_number, values in reader.rows:
                    if not any(item.strip() for item in values):
                        continue
                    if rows_read >= MAX_ROWS:
                        raise ValueError(rows_message())
                    row = rebuild(headers, columns, line_number, values)
                    rows_read += 1
                    committee, row_cycle = row['committee_id'], row['cycle']
                    if committee:
                        names[committee].add(row['committee_name'] or committee)
                        committee_rows[committee] += 1
                        committee_supported[committee] += row['status'] == 'supported'
                        committee_excluded[committee] += row['status'] == 'excluded'
                        if row['category'] == 'Unclassified':
                            committee_unclassified.add(committee)
                        elif row['category']:
                            committee_categories[committee][row['category']] += 1
                    else:
                        unassigned += 1
                    if row_cycle:
                        cycles.add(row_cycle)
                    if row['status'] == 'supported':
                        supported += 1
                        amounts[(committee, row_cycle, row['channel'])] += row['_amount']
                        counts[(committee, row_cycle, row['channel'])] += 1
                    elif row['status'] == 'excluded':
                        excluded += 1
                    else:
                        unsupported += 1
                    batch.append((rows_read, line_number, committee, row_cycle, row['status'], row['channel'],
                                  None if row['_amount'] is None else str(row['_amount']),
                                  json.dumps(values, ensure_ascii=False)))
                    if len(batch) >= 1000:
                        connection.executemany('INSERT INTO csv_rows VALUES (?,?,?,?,?,?,?,?)', batch)
                        connection.commit()
                        batch.clear()
                        value.update(bytes_read=reader.tell(), rows_read=rows_read)
                        try:
                            atomic_json(self.record_path(identifier), value, attempts=1)
                        except OSError:
                            pass
                if batch:
                    connection.executemany('INSERT INTO csv_rows VALUES (?,?,?,?,?,?,?,?)', batch)
            if not names:
                raise ValueError(M_NO_COMMITTEE_ROWS)
            connection.executemany('INSERT INTO totals VALUES (?,?,?,?,?)',
                                   [(committee, row_cycle, channel, str(amounts[(committee, row_cycle, channel)]),
                                     counts[(committee, row_cycle, channel)])
                                    for committee, row_cycle, channel in amounts])
            connection.execute('CREATE INDEX csv_rows_committee ON csv_rows (committee_id, cycle, row_number)')
            connection.execute('CREATE INDEX totals_committee ON totals (committee_id, cycle)')
            connection.commit()
        committees, consulted = [], 0
        for committee in sorted(names):
            labels = sorted(name for name in names[committee] if name != committee) or [committee]
            category, category_source, asked = committee_category(
                committee_categories.get(committee), committee, header, committee in committee_unclassified)
            consulted += asked
            committees.append({'id':committee, 'name':' / '.join(labels), 'rows':committee_rows[committee],
                               'supported_rows':committee_supported[committee],
                               'excluded_rows':committee_excluded[committee],
                               'category':category, 'category_source':category_source})
        if unassigned:
            # The pseudo-committee is rows with no committee ID at all, so it
            # has no registration to classify and no category to filter on.
            committees.append({'id':'unassigned', 'name':'Unassigned committee rows', 'rows':unassigned,
                               'supported_rows':0, 'excluded_rows':0, 'category':'', 'category_source':''})
        value.update(bytes_read=int(value.get('bytes') or 0), rows_read=rows_read)
        return ({'id':identifier, 'name':value['name'], 'sha256':value['sha256'], 'row_count':rows_read,
                 'committees':committees, 'cycles':sorted(cycles, reverse=True),
                 'categories':category_buckets(committees),
                 'supported_rows':supported, 'unsupported_rows':unsupported, 'unassigned_rows':unassigned,
                 'excluded_rows':excluded,
                 'session_only':False, 'imported_at':datetime.now(timezone.utc).isoformat(),
                 'bytes':int(value.get('bytes') or 0),
                 'warnings':catalog_warnings(header, consulted) + extra_warnings,
                 **descriptive(value)}, headers)

    def sweep(self):
        """Drop abandoned uploads and old failures. Undeletable paths wait a turn."""
        cutoff = time.time() - 3600
        for path in self.incoming.glob('*.*'):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
        for path in self.root.glob('csv-*'):
            if not path.is_dir():
                continue
            value = self.read_record(path.name)
            if value and value.get('state') == 'failed' and float(value.get('finished_at') or 0) < cutoff:
                shutil.rmtree(path, ignore_errors=True)

    def evict(self):
        """Keep the newest imports within the file and byte budgets."""
        records = []
        for path in self.root.glob('csv-*'):
            if not path.is_dir():
                continue
            value = self.read_record(path.name)
            if value and value.get('state') == 'ready':
                records.append(value)
        records.sort(key=lambda value: (str(value.get('imported_at') or ''), str(value.get('id') or '')), reverse=True)
        kept = 0
        for index, value in enumerate(records):
            kept += int(value.get('bytes') or 0)
            if index >= KEEP_IMPORTS or kept > KEEP_BYTES:
                shutil.rmtree(self.directory(value['id']), ignore_errors=True)

    def ingest_text(self, name, text):
        """Write text through an upload so in-memory callers use the one parser."""
        upload = self.begin_upload(name)
        try:
            if not isinstance(text, str) or not text.strip():
                raise ValueError(M_EMPTY)
            if '\x00' in text or '�' in text:
                raise ValueError(M_BINARY)
            encoded = text.encode('utf-8')
            for start in range(0, len(encoded), CHUNK_BYTES):
                upload.write(encoded[start:start + CHUNK_BYTES])
        except UnicodeEncodeError as error:
            upload.abort()
            raise ValueError(M_BINARY) from error
        except BaseException:
            upload.abort()
            raise
        return upload.finish()

    def document(self, identifier, text):
        """Materialise a stored import in the in-memory shape the legacy API uses."""
        value = self.ready(identifier)
        headers = value.get('columns', [])
        columns = [key(item) for item in headers]
        rows = []
        with closing(sqlite3.connect(self.database_path(identifier))) as connection:
            for line_number, cells in connection.execute('SELECT line_num, cells FROM csv_rows ORDER BY row_number'):
                rows.append(rebuild(headers, columns, line_number, json.loads(cells)))
        names = defaultdict(set)
        for row in rows:
            if row['committee_id']:
                names[row['committee_id']].add(row['committee_name'] or row['committee_id'])
        return {'id':identifier, 'name':value['name'], 'sha256':value['sha256'], 'text':text,
                'columns':value.get('columns', []), 'rows':rows,
                'committee_names':{committee:sorted(labels) for committee,labels in names.items()},
                'imported_at':value.get('imported_at', ''), 'bytes':int(value.get('bytes') or 0)}


def parse(name, text):
    """Parse text in memory through the same streaming reader the store uses."""
    with tempfile.TemporaryDirectory(prefix='mentat-evidence-csv-') as temporary:
        store = Store(temporary)
        started = store.ingest_text(name, text)
        finished = store.wait(started['id'])
        if finished['state'] != 'ready':
            raise ValueError(finished['message'] or M_GONE(finished['id']))
        return store.document(finished['id'], text)


def catalog(document):
    rows = document['rows']
    committees, consulted = [], 0
    counts, supported_counts, excluded_counts = defaultdict(int), defaultdict(int), defaultdict(int)
    categories = defaultdict(lambda: defaultdict(int))
    unclassified_ids = set()
    for row in rows:
        counts[row['committee_id']] += 1
        supported_counts[row['committee_id']] += row['status'] == 'supported'
        excluded_counts[row['committee_id']] += row['status'] == 'excluded'
        if row.get('category') == 'Unclassified':
            unclassified_ids.add(row['committee_id'])
        elif row.get('category'):
            categories[row['committee_id']][row['category']] += 1
    header = category_header(document.get('columns') or [])
    for identifier, names in sorted(document['committee_names'].items()):
        labels = sorted(name for name in names if name != identifier) or [identifier]
        category, category_source, asked = committee_category(
            categories.get(identifier), identifier, header, identifier in unclassified_ids)
        consulted += asked
        committees.append({'id':identifier, 'name':' / '.join(labels), 'rows':counts[identifier],
                           'supported_rows':supported_counts[identifier],
                           'excluded_rows':excluded_counts[identifier],
                           'category':category, 'category_source':category_source})
    unassigned = sum(not row['committee_id'] for row in rows)
    if unassigned:
        committees.append({'id':'unassigned', 'name':'Unassigned committee rows', 'rows':unassigned,
                           'supported_rows':0, 'excluded_rows':0, 'category':'', 'category_source':''})
    return {'id':document['id'], 'name':document['name'], 'sha256':document['sha256'],
            'row_count':len(rows), 'committees':committees,
            'cycles':sorted({row['cycle'] for row in rows if row['cycle']}, reverse=True),
            'categories':category_buckets(committees),
            'supported_rows':sum(row['status'] == 'supported' for row in rows),
            'unsupported_rows':sum(row['status'] == 'unsupported' for row in rows), 'unassigned_rows':unassigned,
            'excluded_rows':sum(row['status'] == 'excluded' for row in rows),
            'session_only':False, 'imported_at':document.get('imported_at', ''),
            'bytes':int(document.get('bytes') or 0),
            'warnings':catalog_warnings(header, consulted) + list(document.get('warnings') or [])}


def select(document, committee='', selected_cycle='', offset=0, limit=200):
    source = catalog(document)
    committee = committee or source['committees'][0]['id']
    chosen = next((row for row in source['committees'] if row['id'] == committee), None)
    if chosen is None:
        raise ValueError(M_COMMITTEE)
    if selected_cycle and selected_cycle not in source['cycles']:
        raise ValueError(M_CYCLE)
    offset, limit = page_args(offset, limit)
    rows = [row for row in document['rows'] if (row['committee_id'] == committee or committee == 'unassigned' and not row['committee_id'])
            and (not selected_cycle or row['cycle'] == selected_cycle)]
    totals = defaultdict(Decimal)
    counts = defaultdict(int)
    for row in rows:
        if row['status'] == 'supported':
            totals[row['channel']] += row['_amount']
            counts[row['channel']] += 1
    return {'source':source, 'committee':chosen, 'committees':source['committees'],
            'cycles':source['cycles'], 'selected_cycle':selected_cycle, 'columns':document['columns'],
            'rows':[{k:v for k,v in row.items() if not k.startswith('_')} for row in rows[offset:offset+limit]],
            'totals':[{'channel':channel, 'amount':float(value), 'row_count':counts[channel]} for channel,value in sorted(totals.items())],
            'supported_rows':sum(row['status'] == 'supported' for row in rows),
            'excluded_rows':sum(row['status'] == 'excluded' for row in rows),
            'unsupported_rows':sum(row['status'] == 'unsupported' for row in rows),
            'offset':offset, 'limit':limit, 'total_rows':len(rows), 'has_more':offset+limit < len(rows),
            'warnings':source['warnings']}
