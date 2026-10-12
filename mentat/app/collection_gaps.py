"""Collection shortfalls named by a report's own Data Quality check. Display only.

The `collection_completeness` check fails with a Detail such as
"walks stored materially fewer rows than FEC reported - ...: Comcast/2026 contributions
11753 of 15269; ... ... and 19 more". This module takes that text apart so Chart Studio,
Graph and Insights can say "short" beside a chart whose company x cycle scope touches a
cell. The served build's own guard answers, from the first of three places that carries it:
the workbook's Data Quality sheet, the sibling `<workbook>.audit/quality_checks.csv`, and the
`export_guards` in the metadata of the payload pinned to that workbook (an all-cycles build
may have neither of the first two). Nothing is recounted: receipts, rules and money are not
touched here.
"""
from functools import lru_cache
from pathlib import Path
import csv
import re
import zipfile
from xml.etree import ElementTree as ET

CHECK = 'collection_completeness'
SHEET = 'Data Quality'
FROM_SHEET, FROM_CSV, FROM_PAYLOAD = 'Data Quality sheet', 'quality_checks.csv', 'pinned payload export_guards'
READ_ERRORS = (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile, ET.ParseError, csv.Error)
_MORE = re.compile(r'\s*\.\.\.\s*and\s+(\d+)\s+more\s*$')
# "<Company>/<cycle end year> <destination> <held> of <reported>", optionally behind the check's prose.
_CELL = re.compile(r'(?:.*: )?(.+)/(\d{4}) (\S+) (\d+) of (\d+)')
EMPTY = {'failed': False, 'cells': [], 'unitemized': 0, 'source': None}


def parse_detail(text):
    """{'cells': [...], 'unitemized': N} from the check's Detail. Percent rounds half up, as Math.round does."""
    text = str(text or '').strip()
    more = _MORE.search(text)
    if more:
        text = text[:more.start()]
    cells = []
    for piece in text.split('; '):
        found = _CELL.fullmatch(piece.strip())
        if not found:
            continue
        company, year, destination, held, reported = found.groups()
        held, reported, year = int(held), int(reported), int(year)
        if not reported:
            continue
        cells.append({'company': company, 'cycle': f'{year - 1}-{year}', 'destination': destination,
                      'held': held, 'reported': reported, 'percent': (200 * held + reported) // (2 * reported)})
    return {'cells': cells, 'unitemized': int(more[1]) if more else 0}


def _gaps(passed, detail, source):
    return {'failed': str(passed).strip().lower() in ('0', 'false', 'f', 'no'), **parse_detail(detail), 'source': source}


def _from_sheet(path):
    import graph_data  # graph_data imports this module for the Graph payload, so it is read on use
    for row in graph_data.activity_xml_rows(path, {}, SHEET, required={'check', 'severity', 'passed', 'detail'},
                                            optional=True):
        if row['check'] == CHECK:
            return _gaps(row['passed'], row['detail'], FROM_SHEET)
    return None


def _from_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row.get('id') == CHECK:
                return _gaps(row.get('passed'), row.get('detail'), FROM_CSV)
    return None


def quality_csv(path):
    """The sibling audit bundle's copy of the Data Quality sheet."""
    return Path(path).with_suffix('.audit') / 'quality_checks.csv'


def _payload_path(path):
    """The build payload pinned to this workbook by its source fingerprint (contribution_data's own lineage
    rule, read only), or None for a workbook that is not a published report or has no pinned source."""
    import contribution_data as source_data
    from report_identity import identity, lineage_paths
    info = identity(Path(path).name)
    if info is None:
        return None
    fingerprint = source_data._workbook_source(source_data._stamp(Path(path)))
    cache = source_data.PROJECT / '.cache' / 'industry-report' / source_data._slug(info['industry'])
    return lineage_paths(cache, fingerprint, info['cycle_label'])[1]


def _from_payload(path):
    import contribution_data as source_data
    for guard in source_data._payload_metadata(path).get('export_guards') or []:
        if isinstance(guard, dict) and guard.get('id') == CHECK:
            return _gaps(guard.get('passed'), guard.get('detail'), FROM_PAYLOAD)
    return None


def _stamp(path):
    try:
        stat = Path(path).stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _pinned(path):
    try:
        return _payload_path(path)
    except READ_ERRORS:
        return None


@lru_cache(maxsize=64)
def _read(path, book_stamp, csv_stamp, payload, payload_stamp):
    for stamp, read, target in ((book_stamp, _from_sheet, Path(path)), (csv_stamp, _from_csv, quality_csv(path)),
                                (payload_stamp, _from_payload, payload)):
        if stamp is None:
            continue
        try:
            found = read(target)
        except READ_ERRORS:
            continue
        if found is not None:
            return found
    return EMPTY


def workbook_gaps(path):
    """{'failed', 'cells', 'unitemized', 'source'} for one report workbook; failed False and no source when
    none of the three places carries the check (unknown, not complete). Cached by file stamps."""
    path = str(Path(path).resolve())
    payload = _pinned(path)
    found = _read(path, _stamp(path), _stamp(quality_csv(path)), str(payload) if payload else None,
                  _stamp(payload) if payload else None)
    return {**found, 'cells': [dict(cell) for cell in found['cells']]}


def scoped(gaps, years=None):
    """Only the cells of the election cycles (end years) a view can show; None keeps every cell.

    A check that itemized cells for other cycles only, and left none unitemized, says nothing about these
    cycles: it is not a failure here. Without this the page reads the emptied list as "short cells that are not
    itemized", which the check never said (coverage-view.js gapNotice and the pocket copy both read an empty
    list that way, and are right to when the check itself listed nothing)."""
    if years is None:
        return gaps
    wanted = {str(year) for year in years}
    cells = [cell for cell in gaps['cells'] if cell['cycle'][-4:] in wanted]
    if gaps['cells'] and not cells and not gaps['unitemized']:
        return {**gaps, 'failed': False, 'cells': []}
    return {**gaps, 'cells': cells}


def merge(parts):
    """One report group's gaps from its workbooks' (the Data Quality check covers every cycle in each, so
    a cell is listed once, and the unitemized remainder is the largest one, never a sum)."""
    parts = list(parts)
    cells = {}
    for part in parts:
        for cell in part['cells']:
            cells.setdefault((cell['company'], cell['cycle'], cell['destination']), cell)
    return {'failed': any(part['failed'] for part in parts), 'cells': list(cells.values()),
            'unitemized': max((part['unitemized'] for part in parts), default=0),
            'source': next((part['source'] for part in parts if part['source']), None)}


def attach(directory, catalog):
    """Each dataset of a contribution catalogue gets its report's gaps as `collection_gaps`. The workbook is
    named by the dataset's provenance; a cycle report ('... 2025-2026.xlsx') answers for that cycle only.
    Done by the server's catalogue routes, so contribution_data.py (a pinned accounting-rule file whose edit
    makes a running console refuse queries) stays as it is."""
    from report_identity import identity
    for dataset in catalog.get('datasets', []):
        name = (dataset.get('provenance') or {}).get('workbook')
        info = identity(name) if name else None
        if info is None:
            continue
        label = info['cycle_label']
        try:
            dataset['collection_gaps'] = scoped(workbook_gaps(Path(directory) / name), [label[-4:]] if label else None)
        except Exception:  # a display note must never take the catalogue, and with it every chart, down
            continue
    return catalog
