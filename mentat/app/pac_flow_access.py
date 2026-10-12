"""Choose a committee for Flow and collect its filings without a user snapshot."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import hashlib
import os
import re
import threading

import evidence_csv
import graph_data
import pac_evidence


_locks = {}
_locks_guard = threading.Lock()
COMMITTEE = re.compile(r'C\d{8}')


@lru_cache(maxsize=4)
def _master_names(files):
    seen, names = set(), []
    for filename, _modified, _size in files:
        for line in Path(filename).read_text(encoding='utf-8', errors='replace').splitlines():
            fields = line.split('|')
            if len(fields) < 2 or not COMMITTEE.fullmatch(fields[0]) or fields[0] in seen:
                continue
            seen.add(fields[0])
            names.append((fields[0], fields[1]))
    return tuple(names)


def catalog(app, industry='', query=''):
    """Published report committees in one sector, or FEC master name/ID matches."""
    try:
        groups = app.graph_catalog('published-reports')['industries']
    except (OSError, ValueError):
        if industry:
            raise
        groups = []
    sectors = [row['name'] for row in groups]
    query = str(query or '').strip().casefold()
    if len(query) > 150:
        raise ValueError('Search by a PAC name or FEC ID under 150 characters.')
    if industry:
        group = next((row for row in groups if row['name'] == industry), None)
        if group is None:
            raise ValueError('Choose a sector with a published report.')
        found = {}
        for index in group['indices']:
            path = app.report_path('published-reports', index)
            report = graph_data.report_facts(graph_data.stamp(path))
            for row in report['facts']:
                identifier = str(row['recipient_id'])
                if not COMMITTEE.fullmatch(identifier) or row['category'] == 'Candidate':
                    continue
                item = found.setdefault(identifier, {'id': identifier, 'name': row['recipient'],
                                                       'category': row['category'], 'cycles': set()})
                item['cycles'].add(str(row['cycle']))
        committees = [{**row, 'cycles': sorted(row['cycles'], reverse=True)} for row in found.values()]
    else:
        # cm.txt is the FEC's committee directory. Search it only when asked;
        # listing the entire national directory would bury the sector picker.
        committees = []
        if query:
            root = evidence_csv.INPUTS / 'fec_committee_master'
            files = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size)
                          for path in (folder / 'cm.txt' for folder in sorted(root.glob('cm*'), reverse=True))
                          if path.is_file())
            matches = [(identifier, name) for identifier, name in _master_names(files)
                       if query in identifier.casefold() or query in name.casefold()]
            matches.sort(key=lambda row: (row[1].casefold(), row[0]))
            for identifier, name in matches[:500]:
                category, _ = evidence_csv.classify_committee(identifier)
                committees.append({'id': identifier, 'name': name, 'category': category, 'cycles': []})
            total = len(matches)
    if query:
        committees = [row for row in committees if query in row['id'].casefold() or query in row['name'].casefold()]
        if not industry and COMMITTEE.fullmatch(query.upper()) and not committees:
            committees = [{'id': query.upper(), 'name': query.upper(),
                           'category': 'Unclassified', 'cycles': []}]
            total = 1
    committees.sort(key=lambda row: (row['name'].casefold(), row['id']))
    return {'sectors': sectors, 'committees': committees[:500], 'total': total if not industry and query else len(committees),
            'industry': industry}


def direct_evidence(committee, cycle, root, *, allow_collect=True):
    """Prepare a reusable FEC filing cache for a Flow request, with no snapshot step."""
    if not COMMITTEE.fullmatch(committee) or not re.fullmatch(r'\d{4}', str(cycle)) or int(cycle) % 2:
        raise ValueError('Choose a FEC committee ID and an even election year.')
    identifier = f'direct-{committee.lower()}-{cycle}'
    folder = Path(root) / identifier
    with _locks_guard:
        lock = _locks.setdefault(identifier, threading.Lock())
    with lock:
        if (folder / 'snapshots.json').is_file() and (folder / 'evidence.json').is_file():
            return identifier
        if not allow_collect:
            raise ValueError('This read-only console cannot collect the PAC filings for a new flow.')
        folder.mkdir(parents=True, exist_ok=True)
        client = pac_evidence.Client(folder / 'source-cache')
        # A direct view collects the chosen root. Transferred-to committees
        # remain explicit gaps until their own flow is opened or saved evidence
        # already covers them. A national intermediary can have >100,000 rows;
        # collecting it just because this PAC paid it would stall every click.
        units, missing = pac_evidence.collect_network(client, [(committee, int(cycle))], max_units=1)
        # Flow reads raw Schedule A/B/E units and FEC totals. It only needs
        # coverage and name metadata from evidence.json; company attribution,
        # cash estimates and candidate enrichment belong to saved snapshots.
        result = {'version': 1, 'company': '', 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                  'downstream_coverage': missing, 'transfers': [],
                  'committee_finances': [
                      {'committee_id': unit['committee_id'], 'cycle': unit['cycle'],
                       'committee_name': (unit.get('totals') or {}).get('committee_name') or unit['committee_id']}
                      for unit in units]}
        # Inventory only globs snapshots.json. Publish that filename last so
        # another request cannot discover a root before its metadata is ready.
        pending = folder / 'snapshots.json.pending'
        pac_evidence.atomic_json(pending, units)
        result['source_snapshot_sha256'] = hashlib.sha256(pending.read_bytes()).hexdigest()
        pac_evidence.atomic_json(folder / 'evidence.json', result)
        os.replace(pending, folder / 'snapshots.json')
    return identifier
