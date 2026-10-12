"""FEC filing links for rows the console did not collect itself.

Paul's ruling 2026-09-24 ("FEC: Whatever is most efficient"): console-side
fixes, no workbook rebuild, one link per row. Every FEC filing URL the server
hands the PAC-evidence page for an imported workbook row is built here.

Two faults, measured 2026-09-24 (the lane's evidence file fec_links.md):

A. The Overview workbooks' cycle sheets carry each transaction's FEC sub_id
   (the "Source ID" column: export_crypto_spending_data.R writes
   source_id = sub_id) but not its filing link, which the workbook builder
   drops, so every imported donor row said "FEC filing link unavailable".
   The link is recovered here from the database the workbook was BUILT from:
   contribution_data._sources() pins each published Overview workbook to its
   database through the source fingerprint printed inside the workbook, and
   an import is matched to that workbook by its sha256. A sub_id that database
   does not hold is looked up once more in the industry's newest
   receipt-metadata repair database. Either way a hit counts only when its
   committee and amount agree with the row; anything else stays unavailable.
   Nothing is guessed.

B. ActBlue and WinRed file reports so large that FEC never renders their page
   images. All 14 conduit page images tried (filings from 2022 to 2026)
   answered "Error#1896: Image is not processed yet", while the electronic
   filing itself, docquery.fec.gov/cgi-bin/forms/<committee>/<file number>/,
   opened 12 of 12 (ordinary committees' page images opened 7 of 7). A conduit
   row therefore links its whole FEC report, labelled as the report, never a
   page image that cannot open.

Reads are read-only (SQLite mode=ro plus query_only), batched per page, and
memoised under an explicit lock: lru_cache is not single-flight (DE-026).
"""
from __future__ import annotations

from collections import OrderedDict, namedtuple
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import os
from pathlib import Path
import re
import sqlite3
import threading

PROJECT = Path(__file__).resolve().parents[1]
# Where the industry repair databases live (receipt-metadata-repair-YYYYMMDD).
# A module attribute so a test can point it at a fixture tree.
CACHE_ROOT = PROJECT / '.cache'

# The conduit committees, as the codebase already defines them: R/collect.R
# and mentat.R set is_conduit_leg exactly when committee_id is one of these
# two, and R/report.R treats the same two as the registered conduits. In the
# pinned databases is_conduit_leg agrees with this set on every row. They are
# also the filers whose page images FEC never processed (fault B above), which
# is why this set, and nothing broader, decides the report link.
CONDUIT_COMMITTEES = frozenset({'C00401224',   # ActBlue
                                'C00694323'})  # WinRed

REPORT_TEXT = 'FEC report'
REPORT_TITLE = 'FEC has no page image for this filing'
IMAGE_TEXT = 'FEC filing page'
IMAGE_TITLE = 'The FEC page image of the filing page that holds this transaction'
# S3: the pointer for spend rows of a workbook that carries no filing links.
WORKBOOK_NOTE = 'Filing links are not in this workbook'
COMMITTEE_TEXT = 'Committee on FEC'

IMAGE_URL = re.compile(r'https://docquery\.fec\.gov/cgi-bin/fecimg/\?\d{5,30}')
COMMITTEE_ID = re.compile(r'C\d{8}')
FILE_NUMBER = re.compile(r'[1-9]\d{0,11}')
# An FEC sub_id is a long number (19 digits in every Mentat database). A short
# id - a fixture's '401', a filer's TRAN_ID - is never looked up at all.
SUB_ID = re.compile(r'[1-9]\d{9,23}')

TABLES = ('contributions', 'corporate_giving')
COLUMNS = ('sub_id', 'committee_id', 'contribution_receipt_amount', 'contribution_receipt_date',
           'file_num', 'pdf_url')
BATCH = 400
KEEP_IMPORTS = 8

Identity = namedtuple('Identity', 'table committee cents date file_num pdf_url')
Database = namedtuple('Database', 'path source')

_lock = threading.Lock()        # guards the two memos below
_resolving = threading.Lock()   # one database resolution at a time (single flight)
_resolutions = OrderedDict()    # (sha256, report folder signature, industry) -> [Database]
_identities = OrderedDict()     # (sha256, database, size, mtime_ns) -> {sub_id: [Identity]}


# ------------------------------------------------------------------ the links

def committee(value):
    text = str(value or '').strip().upper()
    return text if COMMITTEE_ID.fullmatch(text) else ''


def is_conduit(committee_id):
    return committee(committee_id) in CONDUIT_COMMITTEES


def file_number(value):
    """A positive FEC file number as text, or '' for anything else."""
    if isinstance(value, bool) or value is None:
        return ''
    if isinstance(value, float):
        if not value.is_integer():
            return ''
        value = int(value)
    text = str(value).strip()
    return text if FILE_NUMBER.fullmatch(text) else ''


def image_url(value):
    text = str(value or '').strip()
    return text if IMAGE_URL.fullmatch(text) else ''


def report_url(committee_id, file_num):
    """The electronic FEC report; its cover page lists every schedule in it."""
    identifier, number = committee(committee_id), file_number(file_num)
    return f'https://docquery.fec.gov/cgi-bin/forms/{identifier}/{number}/' if identifier and number else ''


def committee_url(committee_id):
    identifier = committee(committee_id)
    return f'https://www.fec.gov/data/committee/{identifier}/' if identifier else ''


def filing_link(committee_id, file_num, pdf_url):
    """The ONE link a transaction gets, or None when it has none that opens.

    A conduit's transaction links its whole report, because FEC has no page
    image for it; every other transaction links the page image of the filing
    page that holds it. A conduit row without a file number gets nothing: its
    page image is known not to open, and no other link would be this filing.
    """
    identifier, number = committee(committee_id), file_number(file_num)
    if identifier in CONDUIT_COMMITTEES:
        url = report_url(identifier, number)
        if not url:
            return None
        return {'url': url, 'kind': 'report', 'text': REPORT_TEXT, 'title': REPORT_TITLE,
                'committee_id': identifier, 'file_num': number}
    url = image_url(pdf_url)
    if not url:
        return None
    return {'url': url, 'kind': 'image', 'text': IMAGE_TEXT, 'title': IMAGE_TITLE,
            'committee_id': identifier, 'file_num': number}


def workbook_pointer(committee_id):
    """S3: an honest pointer for spend rows of a workbook with no filing links."""
    url = committee_url(committee_id)
    if not url:
        return None
    return {'note': WORKBOOK_NOTE, 'url': url, 'text': COMMITTEE_TEXT,
            'title': f'{WORKBOOK_NOTE}. This opens the committee on FEC, not a filing.'}


def cents(value):
    """Whole cents, rounded half-even, or None. Float noise cannot split a match."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))


# --------------------------------------------------- which database, per import

def _slug(industry):
    """contribution_data._slug, repeated so this module imports nothing heavy."""
    return re.sub(r'[^a-z0-9]+', '_', str(industry or '').lower()).strip('_')


def _report_directory():
    from report_directory import report_directory  # mentat/tools, imported on first use
    return report_directory(PROJECT)


def _report_signature():
    """What a pin depends on: the report folder's Overview workbooks, by stamp."""
    try:
        directory = _report_directory()
        stamps = []
        for path in directory.glob('* Industry Spending Overview.xlsx'):
            if not path.name.startswith('~$'):
                info = path.stat()
                stamps.append((path.name, info.st_size, info.st_mtime_ns))
        return (str(directory), *sorted(stamps))
    except (OSError, ValueError, ImportError):
        return None


def pinned_sources():
    """contribution_data._sources() for the configured report folder, or []."""
    try:
        import contribution_data
        return contribution_data._sources(_report_directory())
    except (OSError, ValueError, ImportError, KeyError):
        return []


def fallback_database(industry):
    """The newest industry repair database, e.g. receipt-metadata-repair-20260910/crypto/mentat.db."""
    slug = _slug(industry)
    if not slug:
        return None
    found = sorted(path for path in Path(CACHE_ROOT).glob(f'receipt-metadata-repair-*/{slug}/mentat.db')
                   if path.is_file())
    return found[-1] if found else None


def _resolve(sha256, industry):
    databases = []
    for item in pinned_sources():
        if item.get('workbook_sha256') != sha256:
            continue
        industry = item.get('industry') or industry
        database = item.get('database')
        if item.get('available') and database and Path(database).is_file():
            databases.append(Database(Path(database).resolve(), 'pinned'))
        break
    fallback = fallback_database(industry)
    if fallback is not None and all(fallback.resolve() != item.path for item in databases):
        databases.append(Database(fallback.resolve(), 'fallback'))
    return databases


def databases_for(record, industry=''):
    """The databases an import's sub_ids are read from, pinned first. Single flight."""
    sha256 = str((record or {}).get('sha256') or '')
    if not re.fullmatch(r'[0-9a-f]{64}', sha256):
        return []
    key = (sha256, _report_signature(), str(industry or ''))
    with _lock:
        if key in _resolutions:
            _resolutions.move_to_end(key)
            return list(_resolutions[key])
    with _resolving:
        with _lock:
            if key in _resolutions:
                return list(_resolutions[key])
        databases = _resolve(sha256, industry)
        with _lock:
            _resolutions[key] = databases
            while len(_resolutions) > KEEP_IMPORTS:
                _resolutions.popitem(last=False)
        return list(databases)


# ------------------------------------------------------------------ the reads

def _read(path, sub_ids):
    """{sub_id: [Identity]} for the ids found, in batched read-only queries."""
    found = {}
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in tables:
                continue
            columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
            if not set(COLUMNS) <= columns:
                continue
            for start in range(0, len(sub_ids), BATCH):
                batch = sub_ids[start:start + BATCH]
                marks = ','.join('?' for _ in batch)
                query = (f'SELECT CAST(sub_id AS TEXT), committee_id, contribution_receipt_amount, '
                         f'contribution_receipt_date, file_num, pdf_url FROM {table} WHERE sub_id IN ({marks})')
                for sub_id, identifier, amount, day, number, pdf in db.execute(query, batch):
                    found.setdefault(str(sub_id), []).append(Identity(
                        table, committee(identifier), cents(amount), str(day or '')[:10],
                        file_number(number), str(pdf or '')))
    return found


def identities(database, sub_ids, sha256):
    """{sub_id: [Identity]} for the ids asked, from the memo or one batched read."""
    try:
        info = os.stat(database.path)
    except OSError:
        return {}
    key = (sha256, str(database.path), info.st_size, info.st_mtime_ns)
    wanted = list(dict.fromkeys(sub_ids))
    with _lock:
        known = _identities.setdefault(key, {})
        _identities.move_to_end(key)
        while len(_identities) > KEEP_IMPORTS * 2:
            _identities.popitem(last=False)
        missing = [sub_id for sub_id in wanted if sub_id not in known]
    if missing:
        try:
            found = _read(database.path, missing)
        except sqlite3.Error:
            return {}
        with _lock:
            for sub_id in missing:
                known[sub_id] = found.get(sub_id, [])
    with _lock:
        return {sub_id: list(known.get(sub_id, [])) for sub_id in wanted}


def match(candidates, committee_id, amount_cents, source_table=''):
    """(identity, '') when exactly one agrees with the row's committee and amount, else (None, reason)."""
    if not candidates:
        return None, 'not_found'
    if source_table in TABLES:
        candidates = [item for item in candidates if item.table == source_table] or candidates
    wanted = committee(committee_id)
    agreeing = [item for item in candidates
                if wanted and item.committee == wanted
                and amount_cents is not None and item.cents == amount_cents]
    if not agreeing:
        return None, 'identity_mismatch'
    if len({(item.committee, item.cents, item.file_num, item.pdf_url) for item in agreeing}) > 1:
        return None, 'ambiguous'
    return agreeing[0], ''


# --------------------------------------------------------------- the entry point

def _field(row, columns, names):
    values = dict(zip(columns, (row.get('raw') or {}).values()))
    return next((str(values[name]).strip() for name in names if str(values.get(name) or '').strip()), '')


def attach(record, rows, columns, industry=''):
    """Give each imported row the FEC link its sub_id resolves to, in place.

    Called AFTER normalize()/rebuild(), which stay pure: this is the only
    step that reads anything outside the row. A resolved row gains `fec_link`
    and, when it had none of its own, `source_url` (so an older page still
    links it) and `record_id`. An unresolved row is left exactly as it was.
    Returns counts for the caller's own diagnostics; they are not sent.
    """
    stats = {'rows': len(rows), 'looked_up': 0, 'linked': 0, 'reports': 0, 'pinned': 0,
             'fallback': 0, 'misses': {}}
    pending = []
    for row in rows:
        # Only a Mentat workbook's own "Source ID" column is looked up. An FEC
        # bulk file's SUB_ID is not: its rows carry their own image and file
        # numbers, and no pinned database stands behind them.
        sub_id = _field(row, columns, ('source_id',))
        if SUB_ID.fullmatch(sub_id):
            pending.append((row, sub_id))
    if not pending:
        return stats
    databases = databases_for(record, industry)
    if not databases:
        stats['misses']['no_database'] = len(pending)
        return stats
    stats['looked_up'] = len(pending)
    reasons = {}
    for database in databases:
        found = identities(database, [sub_id for _row, sub_id in pending], record['sha256'])
        unresolved = []
        for row, sub_id in pending:
            amount = row.get('_amount', row.get('amount'))
            table = _field(row, columns, ('source_table',)).lower()
            hit, reason = match(found.get(sub_id) or [], row.get('committee_id'), cents(amount), table)
            link = filing_link(hit.committee, hit.file_num, hit.pdf_url) if hit else None
            if hit and not link:
                reason = 'no_link'
            if not link:
                reasons[id(row)] = reason
                # Only a sub_id this database does not hold is asked of the next
                # one; a disagreeing identity is a conflict, not a reason to shop.
                if reason == 'not_found':
                    unresolved.append((row, sub_id))
                continue
            reasons.pop(id(row), None)
            row['fec_link'] = {**link, 'sub_id': sub_id, 'date': hit.date, 'source': database.source}
            if not row.get('source_url'):
                row['source_url'] = link['url']
            if not row.get('record_id'):
                row['record_id'] = sub_id
            stats['linked'] += 1
            stats['reports'] += link['kind'] == 'report'
            stats[database.source] += 1
        pending = unresolved
        if not pending:
            break
    for reason in reasons.values():
        stats['misses'][reason] = stats['misses'].get(reason, 0) + 1
    return stats


def clear():
    """Forget every memo. For tests, and for a caller that knows the sources moved."""
    with _lock:
        _resolutions.clear()
        _identities.clear()
