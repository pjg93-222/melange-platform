"""D076: build a standalone bulk activity index (JSON) from verified FEC bulk files. Run it with `python -I`; it makes no network call and writes one new file.

    python -I build_bulk_activity_index.py --sources SOURCES.json --out INDEX.json [--built-utc 2026-10-17T00:00:00Z]

The index is an ORDERING HINT for the downstream collector (`--bulk-activity-index`). It records, per (committee, bulk-file cycle, endpoint kind B / E_regular / E_notice), how many
ROWS the bulk files OBSERVED for that committee as the filer, and which source files (sha256, vintage) those rows came from. It is not API coverage, not proof of payment completeness,
and an absent row is NOT zero: nothing is ever written for a committee/cycle/kind without an observed row. Raw rows are counted as they sit in the file (amendments and memo rows
included, no version resolution), so a count is never an amount and must not be summed across kinds, files or hops.

Cycle = the bulk file's own two-year cycle (D-074), never a transaction date. Mapping (calibrated against the accepted batch-05 API rows, see D076_NOTES.md):
  B          oppexp (every row, filer CMTE_ID); OTH rows of TRANSACTION_TP 24G/24K/24Z/22Z whose OTHER_ID is a committee other than the filer (the BULK_DISCOVERY_EVAL direction rules);
             PAS2 rows (filer CMTE_ID) except independent expenditures 24E/24A (Schedule E), coordinated party expenditures 24C (Schedule F) and communication costs 24F/24N.
  E_regular  every PAS2 24E/24A row (filer CMTE_ID). The owner ruled that PAS2 independent expenditures map to E_regular only; in the three files in use every one of them is on a regular
             report type (the report-class counts in each source's provenance record how many carry RPT_TP 24 or 48: none), so this is also what the data show.
  E_notice   every row of the FEC independent-expenditure file (spe_id; the file carries 24/48-hour notice rows only: all 1,825 notice keys of the accepted batch-05 ledger are in it and none of
             its 1,925 regular-report keys is).
Coverage: a (kind, cycle) is `complete` only when every required source file for it was ingested and verified (B: oppexp + OTH + PAS2; E_regular: PAS2; E_notice: IE); only then may a
consumer read "no row" as "the bulk shows 0 rows".
Every source's bytes are streamed through sha256 while they are parsed and must equal the hash the manifest recorded from the verified download receipts; any mismatch refuses the build.
"""
import argparse
import collections
import csv
import hashlib
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
KIND = 'mentat-bulk-activity-index'
CYCLES = (2022, 2024, 2026)
KINDS = ('B', 'E_regular', 'E_notice')
SOURCE_KINDS = ('oppexp', 'oth', 'pas2', 'ie')
REQUIRED = {'B': ('oppexp', 'oth', 'pas2'), 'E_regular': ('pas2',), 'E_notice': ('ie',)}
COMMITTEE = re.compile(rb'C[0-9]{8}')
OTH_B_TYPES = frozenset({b'24G', b'24K', b'24Z', b'22Z'})
PAS2_NOT_B = {b'24E': 'E', b'24A': 'E', b'24C': 'F', b'24F': 'communication_costs', b'24N': 'communication_costs'}
NOTICE_REPORTS = frozenset({b'24', b'48'})
IE_HEADER = ['cand_id', 'cand_name', 'spe_id', 'spe_nam', 'ele_type', 'can_office_state', 'can_office_dis', 'can_office', 'cand_pty_aff', 'exp_amo', 'exp_date', 'agg_amo', 'sup_opp', 'pur',
             'pay', 'file_num', 'amndt_ind', 'tran_id', 'image_num', 'receipt_dat', 'fec_election_yr', 'prev_file_num', 'dissem_dt']
BLOCK = 1 << 22
STATEMENT = ('An ordering hint, not API coverage and not proof of payment completeness. A row records that the bulk file OBSERVED rows for this committee as filer; an absent row is not zero. '
             'Counts are raw file rows (amendments and memo rows included) and are never amounts.')


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            block = stream.read(BLOCK)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def lines_of(stream, digest):
    """Every line (bytes, no newline) of a binary stream; every byte read is fed to `digest` so the parse and the hash are one pass."""
    tail = b''
    while True:
        block = stream.read(BLOCK)
        if not block:
            break
        digest.update(block)
        parts = (tail + block).split(b'\n')
        tail = parts.pop()
        yield from parts
    if tail:
        yield tail


def scan_oppexp(lines):
    counts, skipped, rows = collections.Counter(), collections.Counter(), 0
    for line in lines:
        rows += 1
        committee = line.split(b'|', 1)[0]
        if COMMITTEE.fullmatch(committee):
            counts[committee, 'B'] += 1
        else:
            skipped['filer_is_not_a_committee_id'] += 1
    return counts, skipped, rows, {}


def scan_oth(lines):
    counts, skipped, types, rows = collections.Counter(), collections.Counter(), collections.Counter(), 0
    match = COMMITTEE.fullmatch
    for line in lines:
        rows += 1
        part = line.split(b'|', 16)
        if len(part) < 16:
            skipped['short_row'] += 1
            continue
        kind = part[5]
        types[kind] += 1
        if kind not in OTH_B_TYPES:
            continue
        other = part[15]
        if other == part[0]:
            skipped['self_transfer'] += 1
        elif match(other) and match(part[0]):
            counts[part[0], 'B'] += 1
        else:
            skipped['counterparty_or_filer_is_not_a_committee_id'] += 1
    return counts, skipped, rows, {'transaction_type_census': {k.decode('latin-1'): v for k, v in sorted(types.items())}}


def scan_pas2(lines):
    counts, skipped, types, other_family, reports, rows = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter(), 0
    match = COMMITTEE.fullmatch
    for line in lines:
        rows += 1
        part = line.split(b'|', 17)
        if len(part) < 17:
            skipped['short_row'] += 1
            continue
        committee, report, kind = part[0], part[2], part[5]
        types[kind] += 1
        if not match(committee):
            skipped['filer_is_not_a_committee_id'] += 1
            continue
        family = PAS2_NOT_B.get(kind)
        if family is None:
            counts[committee, 'B'] += 1
        elif family == 'E':
            reports['notice' if report in NOTICE_REPORTS else 'regular'] += 1      # recorded for the provenance only: the ruling maps every PAS2 24E/24A row to E_regular
            counts[committee, 'E_regular'] += 1
        else:
            other_family[family] += 1
            skipped['other_family_' + family] += 1
    return counts, skipped, rows, {'transaction_type_census': {k.decode('latin-1'): v for k, v in sorted(types.items())},
                                   'independent_expenditure_rows_by_report_class': dict(sorted(reports.items()))}


def scan_ie(text, cycle):
    counts, skipped, years, rows = collections.Counter(), collections.Counter(), collections.Counter(), 0
    reader = csv.reader(io.StringIO(text, newline=''))
    header = next(reader, None)
    if header != IE_HEADER:
        raise ValueError('independent-expenditure header differs from the reviewed 23 columns: ' + repr(header)[:300])
    for row in reader:
        rows += 1
        if len(row) != len(IE_HEADER):
            skipped['wrong_field_count'] += 1
            continue
        years[row[20]] += 1
        spender = row[2].encode('ascii', 'replace')
        if COMMITTEE.fullmatch(spender):
            counts[spender, 'E_notice'] += 1
        else:
            skipped['spender_is_not_a_committee_id'] += 1
    return counts, skipped, rows, {'fec_election_yr_census': dict(sorted(years.items())), 'file_cycle_rows_with_another_election_year': sum(v for k, v in years.items() if k != str(cycle))}


def verify_source(entry):
    """Hash the container, read the body in ONE pass (parse + sha256), compare with the recorded hashes and row count. Returns (counts, record)."""
    for field in ('id', 'kind', 'cycle', 'path', 'sha256', 'vintage_asof'):
        if field not in entry:
            raise ValueError('source lacks ' + field + ': ' + repr(entry)[:200])
    if entry['kind'] not in SOURCE_KINDS or entry['cycle'] not in CYCLES or not re.fullmatch('[0-9a-f]{64}', str(entry['sha256'])):
        raise ValueError('source has an unknown kind, cycle or sha256: ' + entry['id'])
    path = Path(entry['path'])
    size = path.stat().st_size
    container = hash_file(path)
    if container != entry['sha256']:
        raise ValueError('source ' + entry['id'] + ' file sha256 ' + container + ' differs from the recorded ' + entry['sha256'] + '; refusing')
    member = entry.get('member')
    body = hashlib.sha256()
    if member:
        with zipfile.ZipFile(path) as archive:
            if archive.namelist() != [member]:
                raise ValueError('source ' + entry['id'] + ' zip holds ' + repr(archive.namelist()) + ' instead of exactly ' + member)
            with archive.open(member) as stream:
                result = parse(entry, stream, body)
        if 'member_sha256' in entry and body.hexdigest() != entry['member_sha256']:
            raise ValueError('source ' + entry['id'] + ' member sha256 ' + body.hexdigest() + ' differs from the recorded ' + entry['member_sha256'] + '; refusing')
    else:
        with open(path, 'rb') as stream:
            result = parse(entry, stream, body)
        if body.hexdigest() != container:
            raise ValueError('source ' + entry['id'] + ' changed while it was read; refusing')
    counts, skipped, rows, extra = result
    if entry.get('rows') is not None and rows != entry['rows']:
        raise ValueError('source ' + entry['id'] + ' has ' + str(rows) + ' rows, the verified record says ' + str(entry['rows']) + '; refusing')
    used = collections.Counter()
    for (_, kind), count in counts.items():
        used[kind] += count
    record = {key: entry[key] for key in ('id', 'kind', 'cycle', 'sha256', 'vintage_asof') if key in entry}
    record.update({key: entry[key] for key in ('last_modified', 'url', 'retrieved_at', 'member', 'member_sha256', 'verified_from') if entry.get(key) is not None})
    record.update(file_name=path.name, bytes=size, rows_scanned=rows, rows_used=dict(sorted(used.items())), rows_skipped=dict(sorted(skipped.items())), hashes_match_recorded=True, **extra)
    return counts, record


def parse(entry, stream, digest):
    kind = entry['kind']
    if kind == 'ie':
        data = stream.read()
        digest.update(data)
        return scan_ie(data.decode('utf-8', 'replace'), entry['cycle'])
    scan = {'oppexp': scan_oppexp, 'oth': scan_oth, 'pas2': scan_pas2}[kind]
    return scan(lines_of(stream, digest))


def build(entries, built_utc):
    ids = [entry['id'] for entry in entries]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate source ids')
    pairs = [(entry['kind'], entry['cycle']) for entry in entries]
    if len(set(pairs)) != len(pairs):
        raise ValueError('two sources for one (kind, cycle)')
    totals, records = {}, []
    for entry in entries:
        counts, record = verify_source(entry)
        records.append(record)
        for (committee, kind), count in counts.items():
            key = (committee.decode('ascii'), entry['cycle'], kind)
            totals.setdefault(key, {})[entry['id']] = count
    coverage = {}
    for kind in KINDS:
        coverage[kind] = {}
        for cycle in CYCLES:
            have = sorted(entry['id'] for entry in entries if entry['cycle'] == cycle and entry['kind'] in REQUIRED[kind])
            missing = sorted(set(REQUIRED[kind]) - {entry['kind'] for entry in entries if entry['cycle'] == cycle})
            coverage[kind][str(cycle)] = {'complete': not missing, 'ingested': have, 'missing_source_kinds': missing}
    rows = [[committee, cycle, kind, sum(per.values()), dict(sorted(per.items()))] for (committee, cycle, kind), per in sorted(totals.items())]
    summary = collections.Counter()
    committees = collections.defaultdict(set)
    for committee, cycle, kind, _, _ in rows:
        summary[str(cycle) + '/' + kind] += 1
        committees[cycle].add(committee)
    vintages = sorted(entry['vintage_asof'] for entry in entries)
    return {'schema_version': SCHEMA_VERSION, 'kind': KIND,
            'provenance': {'statement': STATEMENT, 'built_utc': built_utc, 'builder': {'file': Path(__file__).name, 'sha256': hash_file(__file__)},
                           'python': sys.version.split()[0], 'cycle_rule': "the bulk file's own two-year cycle (D-074); transaction dates are never used",
                           'no_row_is_not_zero': True, 'sources': records, 'vintage': {'oldest_asof': vintages[0] if vintages else None, 'newest_asof': vintages[-1] if vintages else None}},
            'coverage': coverage, 'rows': rows,
            'stats': {'rows': len(rows), 'rows_by_cycle_kind': dict(sorted(summary.items())), 'committees_by_cycle': {str(c): len(v) for c, v in sorted(committees.items())}}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sources', required=True, type=Path, help='JSON list of source entries: id, kind, cycle, path, sha256, vintage_asof [, member, member_sha256, rows, last_modified, url, retrieved_at, verified_from]')
    parser.add_argument('--out', required=True, type=Path, help='the index file to write; it must not exist')
    parser.add_argument('--built-utc', default=None)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit('Refusing to overwrite ' + str(args.out))
    entries = json.loads(args.sources.read_text(encoding='utf-8'))
    if not isinstance(entries, list) or not entries:
        raise SystemExit('The sources manifest must be a non-empty JSON list.')
    index = build(entries, args.built_utc or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    body = (json.dumps(index, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    with open(args.out, 'xb') as stream:
        stream.write(body)
    print(json.dumps({'index': str(args.out), 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body), 'rows': len(index['rows']), 'stats': index['stats']}, sort_keys=True))


if __name__ == '__main__':
    main()
