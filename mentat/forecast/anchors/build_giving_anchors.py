"""Build the Giving Forecast anchor snapshot: Mentat's own levels that the forecast applies its relative changes to.

    python build_giving_anchors.py [--project-root <Mentat\\mentat>] [--industry id ...] [--output-dir <dir>] [--check]

Owner rulings of 2026-09-30 implemented here: the forecast applies to MENTAT data (levels always from Mentat; OpenSecrets only estimated the
relationships); PAC shown as (1) the roster companies' PACs' own giving to candidates (FEC pas2, D-049) and (2) Mentat general giving (the
workbook's included Analysis Amount, less the non_giving receipts: see the 2026-10-01 ruling below); shares on the included-receipt and PAC bases;
Melange LDA levels, both series; coverage end by rule; thin-base flags; Community Banks separate; auto has no Mentat data and no entry.
Owner ruling of 2026-10-01 (D4, option b), in the FORECAST only: receipts the accounting engine buckets as non_giving (FEC-reported interest, refunds,
insurance claims, loans) are left out of the general-giving levels, the monthly observed cuts and the included-receipt House / Senate candidate bases
(mentat_sources.FORECAST_EXCLUDED_FUNDING_SOURCES); each block reports what was left out (`excluded_non_giving`, per cycle). The workbook is untouched.

READ-ONLY toward everything but its own two output files. SQLite files are opened immutable; FEC files and the LDA / CPI inputs are only read;
no money-collection or receipt-counting code is imported or run. The snapshot is written atomically (temporary file + replace) only after it
validates against the shared schema and the anchor invariants (mentat/forecast/contracts); an invalid build leaves the previous snapshot
in place. Output: <project>/state/giving-anchors/giving-anchors-v1.json + giving-anchors-v1.receipt.json (runtime state, D-048: not in git).
"""
import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'contracts'))
import contract  # noqa: E402
import coverage as coverage_rule  # noqa: E402
import cpi as cpi_reader  # noqa: E402
import fec_pas2  # noqa: E402
import glassbox  # noqa: E402
import lda as lda_reader  # noqa: E402
import mentat_sources as ms  # noqa: E402
sys.path.append(str(HERE.parents[1] / 'app'))          # receipt_path only; appended so no app module shadows the anchors' own
from receipt_path import receipt_path  # noqa: E402

VERSION = '1'
SNAPSHOT_NAME = 'giving-anchors-v1.json'
RECEIPT_NAME = 'giving-anchors-v1.receipt.json'
LDA_UNAVAILABLE = 'No Melange-LDA sector for this industry (sector_spend ROSTERS: crypto, credit_union, bank, fintech, telecom)'


class Unavailable(Exception):
    """An industry that cannot be anchored; the message is the reason the console shows."""


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def dollars(cents):
    return round(cents / 100, 2)


def _usd(cents):
    """'$1,234.56' (or '-$1,234.56') from integer cents."""
    return ('-' if cents < 0 else '') + '$' + format(abs(dollars(cents)), ',.2f')


def _cycle_label(cycle, open_cycle):
    """'2024' -> '2023-2024'; the open cycle holds every row to date: '2025-2026 to date'."""
    return f'{int(cycle) - 1}-{cycle}' + (' to date' if int(cycle) == int(open_cycle) else '')


def code_sha256():
    parts = sorted([p for p in HERE.glob('*.py')] + [HERE / 'registry.json'])
    return hashlib.sha256('\n'.join(ms.sha256_file(p) for p in parts).encode()).hexdigest()


def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream)


# ---------------------------------------------------------------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------------------------------------------------------------
def report_directory(project_root):
    value = os.environ.get('MENTAT_REPORT_DIRECTORY', '').strip()
    if not value:
        setting = read_json(Path(project_root) / 'config' / 'report-directory.json')
        if setting.get('schema_version') != 1:
            raise ValueError('Unsupported report-directory configuration.')
        value = setting['directory']
    directory = Path(value)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError('The configured report directory is unavailable.')
    return directory.resolve()


def fec_root(project_root, override=None):
    if override:
        return Path(override)
    return receipt_path(read_json(Path(project_root) / 'inputs' / 'active_fec_sources.json')['path'])          # recorded on Windows; a container rebases it onto the mounted project


def default_lda_csv():
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(base) / 'Melange' / 'lda_rebuild' / 'derived' / 'sector_spend' / 'sector_spend.csv'


def default_controls(project_root):
    return Path(project_root).resolve().parent / 'sector_growth_study' / 'data' / '00_controls.xlsx'


def default_output_dir(project_root):
    return Path(project_root) / 'state' / 'giving-anchors'


# ---------------------------------------------------------------------------------------------------------------------------------
# the Mentat side of one industry
# ---------------------------------------------------------------------------------------------------------------------------------
def mentat_side(spec, ctx):
    run = ctx.get('run')
    def read_step(name, operation):
        stage = 'mentat:' + spec['id'] + ':' + name
        if run:
            run.stage_start(stage)
        try:
            value = operation()
        except Exception as error:
            if run:
                run.stage_finish(stage, type(error).__name__ + ': ' + str(error), status='failed')
            raise
        if run:
            run.stage_finish(stage)
        return value
    workbook = ctx['report_dir'] / spec['workbook']
    if not workbook.is_file():
        raise Unavailable('The published workbook ' + spec['workbook'] + ' is not in the report directory.')
    workbook_sha = read_step('workbook_hash', lambda: ms.sha256_file(workbook))
    found = read_step('index_selection', lambda: ms.find_index(ctx['index_dir'], workbook_sha))
    if found is None:
        raise Unavailable('No contribution-analytics index matches the published workbook (sha256 ' + workbook_sha[:8] + '). Open the industry in the console so its index is built, then rebuild the anchors.')
    index_path, manifest = found
    prov = manifest['provenance']
    source = read_step('source_db_hash', lambda: ms.find_source_db(ctx['project_root'], spec['source_db_candidates'], prov['database_sha256'], ctx['hashes']))
    if source is None:
        raise Unavailable('The pinned source database (sha256 ' + str(prov['database_sha256'])[:8] + ') was not found under the Mentat project folder.')
    collected = read_step('collection_date', lambda: ms.collection_date(source, spec['mentat_dataset']))
    open_cycle = int(collected[:4]) + int(collected[:4]) % 2
    cov = coverage_rule.coverage(collected, spec.get('coverage_cut_back'))
    cuts = fec_pas2.month_ends(open_cycle - 1, cov['coverage_end'])
    agg = read_step('index_aggregation', lambda: ms.index_aggregates(index_path, source, open_cycle, cuts))
    expected = {k.split('-')[-1]: v for k, v in manifest['receipt_cycles'].items()}
    if agg['analysis_cycles'] != expected:       # the manifest records the Analysis Amount BEFORE the forecast's non_giving exclusion (agg['cycles'] is after it)
        raise Unavailable('The index receipt totals differ from its own manifest; the index is not trustworthy.')
    progress = read_step('work_progress', lambda: ms.work_progress(source, open_cycle))
    return dict(workbook=workbook, workbook_sha=workbook_sha, index_path=index_path, manifest=manifest, source=source,
                collected=collected, open_cycle=open_cycle, cov=cov, cuts=cuts, agg=agg, progress=progress)


def _chambers(blocks):
    return {ch: {k: dollars(blocks[ch][k]) for k in ('D', 'R', 'other')} for ch in ('House', 'Senate')}


def _thin(cycles, threshold):
    return {cy: {ch: (row[ch]['D'] + row[ch]['R']) < threshold for ch in ('House', 'Senate')} for cy, row in cycles.items()}


def general_and_candidates(side, threshold):
    """The general-giving and candidate-receipt blocks on the FORECAST basis: receipts whose funding source is in ms.FORECAST_EXCLUDED_FUNDING_SOURCES (the
    accounting engine's non_giving bucket; owner ruling 2026-10-01) are left out of the levels, the observed cuts and the candidate bases. `by_funding_source`
    stays the full split of the Analysis Amount, and `excluded_non_giving` reports what was left out, so cycles + excluded = the published Analysis Amount."""
    agg = side['agg']
    left_out = sorted(ms.FORECAST_EXCLUDED_FUNDING_SOURCES)
    general = {
        'status': 'ready', 'reason': None, 'basis': 'receipt',
        'label': "Published included receipt view (workbook Analysis Amount), all funding sources except those Mentat's accounting engine marks non-giving "
                 '(owner ruling 2026-10-01), all recipient categories, net of negative adjustments',
        'cycles': {cy: dollars(v) for cy, v in sorted(agg['cycles'].items())},
        'by_funding_source': {cy: {k: dollars(v) for k, v in sorted(src.items())} for cy, src in sorted(agg['by_source'].items())},
        'observed': [{'through': cut, 'amount': dollars(v)} for cut, v in agg['general_cuts']],
        'excluded_funding_sources': left_out,
        'excluded_non_giving': {cy: dollars(v) for cy, v in sorted(agg['excluded'].items())},
        'odd_year': {cy: dollars(v) for cy, v in sorted(agg['odd_year'].items())}}
    cycles = {cy: _chambers(blocks) for cy, blocks in sorted(agg['candidates'].items())}
    candidates = {
        'status': 'ready', 'reason': None,
        'basis': 'Included receipts to House (committee type H) and Senate (S) candidate committees, across all funding sources; funding identity is not necessarily verified. The index is joined to the pinned source '
                 'database on sub_id; party D / R from the index, anything else "other"; presidential (P) committees excluded; receipts the accounting '
                 'engine buckets as non_giving left out (owner ruling 2026-10-01)',
        'cycles': cycles, 'observed': [dict(through=cut, **_chambers(blocks)) for cut, blocks in agg['candidate_cuts']],
        'thin_base': _thin(cycles, threshold),
        'excluded_funding_sources': left_out,
        'excluded_non_giving': {cy: _chambers(blocks) for cy, blocks in sorted(agg['candidates_excluded'].items())}}
    if agg['type_conflicts']:
        exposure = agg['type_conflict_exposure']
        reason = (f"{exposure['rows']} included receipts ({_usd(exposure['net_cents'])} net; {_usd(exposure['gross_cents'])} gross) have conflicting recipient committee types involving House or Senate. "
                  'The candidate-receipt basis is unavailable; general giving retains every amount. Resolve the source conflict before using a chamber split.')
        candidates.update(status='unavailable', reason=reason, cycles={}, observed=[], thin_base={}, excluded_non_giving={})
    return general, candidates


# ---------------------------------------------------------------------------------------------------------------------------------
# FEC pas2
# ---------------------------------------------------------------------------------------------------------------------------------
def _yy(cycle):
    return '%02d' % (cycle % 100)


def _http_date(value):
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return None


def fec_inputs(root, cycles):
    """[(role, relative file, path, receipt dict)] of the pas2 / cn / cm files of these cycles, each verified against its source receipt."""
    out = []
    for cycle in cycles:
        yy = _yy(cycle)
        for role, folder, sub, name in (('pas2', 'fec_beneficiary', 'pas2' + yy, 'itpas2.txt'), ('cn', 'fec_beneficiary', 'cn' + yy, 'cn.txt'),
                                        ('cm', 'fec_committee_master', 'cm' + yy, 'cm.txt')):
            path = Path(root) / folder / sub / name
            if not path.is_file():
                raise Unavailable('FEC input missing: ' + str(Path(folder) / sub / name))
            receipt_path = path.with_name('source-receipt.json')
            receipt = read_json(receipt_path) if receipt_path.is_file() else {}
            sha = ms.sha256_file(path)
            if receipt.get('sha256') and receipt['sha256'] != sha:
                raise Unavailable('FEC input ' + sub + '/' + name + ' differs from its source receipt; re-run the FEC refresh before anchoring.')
            out.append({'role': role + ':' + str(cycle), 'file': sub + '/' + name, 'path': path, 'sha256': sha,
                        'fec_last_modified': _http_date(receipt.get('last_modified')), 'yy': yy, 'family': role})
    return out


def committee_ids_in_master(files):
    found = set()
    for f in files:
        if f['family'] == 'cm':
            with open(f['path'], encoding='latin-1') as stream:
                for line in stream:
                    found.add(line.split('|', 1)[0])
    return found


def build_pac(group, root, open_cycle, sides, threshold):
    """{industry id: pac_to_candidates block} for the industries (with rosters) of one open cycle."""
    cycles = [open_cycle - 4, open_cycle - 2, open_cycle]
    inputs = fec_inputs(root, cycles)
    cn_files = [(f['yy'], f['path']) for f in sorted(inputs, key=lambda f: f['yy'], reverse=True) if f['family'] == 'cn']
    parties = fec_pas2.load_parties(cn_files)
    membership = {}
    for spec in group:
        for committee in spec['pac_roster']:
            membership.setdefault(committee['id'], []).append(spec['id'])
    in_master = committee_ids_in_master(inputs)
    out, notes = {}, {}
    cuts = {spec['id']: sides[spec['id']]['cuts'] for spec in group}
    results = {}
    for f in inputs:
        if f['family'] != 'pas2':
            continue
        cycle = int(f['role'].split(':')[1])
        # A complete cycle also gets the one cut at December 31 of its first year (the odd-year split of the cycle model).
        results[cycle] = fec_pas2.aggregate_file(f['path'], f['yy'], parties, membership,
                                                 cuts if cycle == open_cycle else {spec['id']: [f'{cycle - 1}-12-31'] for spec in group})
    for spec in group:
        ident = spec['id']
        by_cycle, through, observed = {}, {}, []
        for cycle in cycles:
            block = results[cycle].get(ident)
            by_cycle[str(cycle)] = fec_pas2.dollars(block['totals'] if block else fec_pas2.blank_block())
            if block and block['max_date']:
                through[str(cycle)] = block['max_date']
        block = results[open_cycle].get(ident)
        for cut in cuts[ident]:
            cut_block = block['cuts'][cut] if block else fec_pas2.blank_block()
            d = fec_pas2.dollars(cut_block)
            observed.append({'through': cut, 'House': d['House'], 'Senate': d['Senate'], 'to_house_senate': d['to_house_senate']})
        odd_year = {}
        for cycle in cycles:
            if cycle < open_cycle:
                block_c = results[cycle].get(ident)
                d = fec_pas2.dollars(block_c['cuts'][f'{cycle - 1}-12-31'] if block_c else fec_pas2.blank_block())
                odd_year[str(cycle)] = {'House': d['House'], 'Senate': d['Senate'], 'to_house_senate': d['to_house_senate']}
        roster = spec['pac_roster']
        missing = sorted(c['id'] for c in roster if c['id'] not in in_master)
        out[ident] = {
            'status': 'ready', 'reason': None, 'source': 'FEC committee-to-candidate bulk files (pas2) with the candidate master (cn)',
            'rule': fec_pas2.RULE_TEXT, 'committee_count': len(roster), 'single_pac': len(roster) == 1,
            'committees': [dict(c) for c in roster],
            'inputs': [{'role': f['role'], 'file': f['file'], 'sha256': f['sha256'], 'fec_last_modified': f['fec_last_modified']} for f in inputs],
            'cycles': by_cycle, 'file_through': through, 'observed': observed,
            'thin_base': _thin({cy: row for cy, row in by_cycle.items()}, threshold), 'odd_year': odd_year}
        found = []
        if missing:
            found.append('Roster committee(s) absent from the FEC committee masters: ' + ', '.join(missing))
        undated = sum((results[c].get(ident) or {}).get('undated_cents', 0) for c in cycles)
        if undated:
            found.append(f'${format(dollars(undated), ",.2f")} of counted FEC rows carry no usable date: they are in the cycle totals and in no observed cut.')
        if found:
            notes[ident] = ' '.join(found)
    return out, notes


def unavailable_pac(reason):
    return {'status': 'unavailable', 'reason': reason, 'source': '', 'rule': '', 'committee_count': 0, 'single_pac': False, 'committees': [],
            'inputs': [], 'cycles': {}, 'file_through': {}, 'observed': [], 'thin_base': {}}


# ---------------------------------------------------------------------------------------------------------------------------------
# LDA
# ---------------------------------------------------------------------------------------------------------------------------------
def build_lda(spec, rows, lda_path, lda_sha, as_of, receipts):
    if spec.get('lda_sector') and rows is None:
        return {'status': 'unavailable', 'reason': 'The Melange LDA output ' + lda_path.name + ' was not found.', 'sector': spec['lda_sector'], 'label': None,
                'source_file': None, 'source_sha256': None, 'through': None, 'series': {}, 'note': lda_reader.NOTE}
    if not spec.get('lda_sector'):
        return {'status': 'unavailable', 'reason': LDA_UNAVAILABLE, 'sector': None, 'label': None, 'source_file': None, 'source_sha256': None,
                'through': None, 'series': {}, 'note': lda_reader.NOTE}
    if rows and as_of is None:          # fail closed: without the producer's sweep date the filings-only rule is NOT used
        why = ('record no sweep date for ' + str(max(int(r['year']) for r in rows))) if receipts.is_file() else 'were not found'
        return {'status': 'unavailable', 'reason': 'The Melange LDA receipts (' + receipts.name + ') ' + why + ', so the partial year cannot be dated.', 'sector': spec['lda_sector'],
                'label': None, 'source_file': lda_path.name, 'source_sha256': lda_sha, 'through': None, 'series': {}, 'note': lda_reader.NOTE}
    levels = lda_reader.sector_levels(rows, spec['lda_sector'], as_of)
    if levels is None:
        return {'status': 'unavailable', 'reason': 'The Melange LDA output has no rows for sector ' + spec['lda_sector'], 'sector': spec['lda_sector'],
                'label': None, 'source_file': lda_path.name, 'source_sha256': lda_sha, 'through': None, 'series': {}, 'note': lda_reader.NOTE}
    label = {'bank': 'Melange LDA bank sector (total, not a bank tier)', 'credit_union': 'Melange LDA credit-union sector',
             'telecom': 'Melange LDA telecom sector'}.get(spec['lda_sector'], 'Melange LDA sector ' + spec['lda_sector'])
    return {'status': 'ready', 'reason': None, 'sector': spec['lda_sector'], 'label': label, 'source_file': lda_path.name, 'source_sha256': lda_sha,
            'through': levels['through'], 'series': levels['series'], 'counts': levels['counts'], 'note': lda_reader.NOTE}


# ---------------------------------------------------------------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------------------------------------------------------------
def industry_flags(spec, side, pac_note, cpi_multipliers):
    coverage_flags, flags = [], []
    progress = side['progress']
    if progress and progress['stored_complete'] < progress['units']:
        coverage_flags.append(f"{progress['stored_complete']} of {progress['units']} {side['open_cycle']} collection work units are stored_complete "
                              f"({progress['fetched_complete']} fetched) in the collection database's own progress table; incomplete work-unit storage needs inspection. "
                              "These counters do not establish literal-query or whole-industry coverage.")
    if side['agg']['after_cut']:
        coverage_flags.append('Included receipts dated after the coverage end ($' + format(dollars(side['agg']['after_cut']), ',.2f') +
                              ') are not observed; they stay in the remainder window.')
    if side['cov']['reason']:
        coverage_flags.append('Coverage cut back from ' + side['cov']['rule_coverage_end'] + ' to ' + side['cov']['coverage_end'] + ': ' + side['cov']['reason'] + '.')
    if str(side['open_cycle']) not in cpi_multipliers:
        coverage_flags.append(f"No {side['open_cycle']} CPI exists yet: {side['open_cycle'] - 1}-{side['open_cycle']} observed amounts are compared at face value.")
    left_out = {cy: v for cy, v in side['agg']['excluded'].items() if v}
    if left_out:
        flags.append("Excluded from the forecast (owner ruling 2026-10-01): receipts Mentat's accounting engine marks non-giving - FEC-reported interest, refunds, "
                     'insurance claims and loans - ' + '; '.join(f'{_usd(v)} in {_cycle_label(cy, side["open_cycle"])}' for cy, v in sorted(left_out.items())) +
                     ". They stay in the published workbook's Analysis Amount; the general-giving levels, observed amounts and included-receipt House and Senate "
                     'candidate bases of this forecast leave them out.')
    if side['agg']['type_conflicts']:
        flags.append(f"{side['agg']['type_conflicts']} forecast-counted index facts have conflicting recipient committee types involving House or Senate; the candidate-receipt basis is unavailable.")
    if side['agg']['unmatched'][0]:
        flags.append(f"{side['agg']['unmatched'][0]} included facts (${format(dollars(side['agg']['unmatched'][1]), ',.2f')}) have no source row and are outside the chamber split.")
    if pac_note:
        flags.append(pac_note)
    if len(spec.get('pac_roster') or []) == 1:
        flags.append('PAC channel = one committee (' + spec['pac_roster'][0]['company'] + '); it is not the industry\'s PAC giving (thin base).')
    return coverage_flags, flags


def build_snapshot(project_root, registry, options):
    """-> (snapshot dict, receipt inputs list, problems). `options`: report_dir, fec_root, lda_csv, controls, only (ids or None), run (glassbox.Run), and optionally
    lda_receipts (default: fullscope_receipts.json beside the lda_csv's folder, Melange's data-root layout)."""
    run = options.get('run')
    stage = (lambda name, summary=None: run and run.stage_start(name, summary))
    done = (lambda name, summary=None, **kw: run and run.stage_finish(name, summary, **kw))
    project_root = Path(project_root)
    threshold = float(registry['thin_base_threshold_usd'])
    specs = [s for s in registry['industries'] if not options.get('only') or s['id'] in options['only']]
    inputs = []

    stage('inputs', 'Reading the CPI and LDA inputs')
    cpi = cpi_reader.read_cpi(options['controls'])
    controls_sha = ms.sha256_file(options['controls'])
    inputs.append({'role': 'cpi', 'path': str(options['controls']), 'sha256': controls_sha})
    lda_path = Path(options['lda_csv'])
    lda_rows = lda_reader.read_sector_rows(lda_path) if lda_path.is_file() else None
    lda_sha = ms.sha256_file(lda_path) if lda_path.is_file() else None
    if lda_sha:
        inputs.append({'role': 'lda', 'path': str(lda_path), 'sha256': lda_sha})
    receipts = Path(options.get('lda_receipts') or lda_path.absolute().parents[1] / 'fullscope_receipts.json')          # the producer's own sweep date dates the partial year
    lda_as_of = lda_reader.sweep_as_of(receipts, max(int(r['year']) for r in lda_rows)) if lda_rows and receipts.is_file() else None
    if lda_as_of:
        inputs.append({'role': 'lda_receipts', 'path': str(receipts), 'sha256': ms.sha256_file(receipts)})
    done('inputs')

    ctx = dict(project_root=project_root, report_dir=options['report_dir'], index_dir=project_root / ms.STATE_INDEX_DIR, hashes={}, run=run)
    sides, unavailable = {}, {}
    for spec in specs:
        stage('mentat:' + spec['id'], 'Reading the index of ' + spec['mentat_dataset'])
        try:
            sides[spec['id']] = mentat_side(spec, ctx)
            done('mentat:' + spec['id'], status='succeeded')
        except (Unavailable, ValueError, OSError) as error:
            unavailable[spec['id']] = str(error)
            done('mentat:' + spec['id'], str(error), status='failed')
    for ident, side in sides.items():
        inputs.append({'role': 'workbook:' + ident, 'path': str(side['workbook']), 'sha256': side['workbook_sha']})
        inputs.append({'role': 'index:' + ident, 'path': str(side['index_path']), 'sha256': side['manifest']['token']})
        inputs.append({'role': 'source_db:' + ident, 'path': str(side['source']), 'sha256': side['manifest']['provenance']['database_sha256']})

    pac, pac_notes, pac_reason = {}, {}, {}
    by_cycle = {}
    for spec in specs:
        if spec['id'] in sides and spec.get('pac_roster'):
            by_cycle.setdefault(sides[spec['id']]['open_cycle'], []).append(spec)
    for open_cycle, group in by_cycle.items():
        stage('fec:' + str(open_cycle), 'Aggregating the roster PACs\' own giving to candidates')
        try:
            built, notes = build_pac(group, options['fec_root'], open_cycle, sides, threshold)
            pac.update(built)
            pac_notes.update(notes)
            for f in built[group[0]['id']]['inputs']:
                inputs.append({'role': 'fec:' + f['role'], 'path': f['file'], 'sha256': f['sha256']})
            done('fec:' + str(open_cycle))
        except (Unavailable, OSError, ValueError) as error:
            for spec in group:
                pac_reason[spec['id']] = str(error)
            done('fec:' + str(open_cycle), str(error), status='failed')

    stage('assemble', 'Assembling the snapshot')
    all_cycles = sorted({int(cy) for side in sides.values() for cy in side['agg']['cycles']} | {c for side in sides.values() for c in (side['open_cycle'] - 2,)})
    multipliers = cpi_reader.multipliers(cpi, all_cycles)
    industries = {}
    for spec in specs:
        ident = spec['id']
        if ident not in sides:
            continue
        side = sides[ident]
        general, candidates = general_and_candidates(side, threshold)
        if ident in pac:
            pac_block = pac[ident]
        else:
            pac_block = unavailable_pac(pac_reason.get(ident) or 'No roster PAC committees are resolved for this industry')
        lda = build_lda(spec, lda_rows, lda_path, lda_sha, lda_as_of, receipts)
        cov_flags, flags = industry_flags(spec, side, pac_notes.get(ident), multipliers)
        prov = side['manifest']['provenance']
        industries[ident] = {
            'id': ident, 'label': spec['label'], 'forecast_industry': spec['forecast_industry'],
            'mentat': {'dataset': spec['mentat_dataset'], 'workbook': spec['workbook'], 'workbook_sha256': side['workbook_sha'],
                       'index_token': side['manifest']['token'], 'database_sha256': prov['database_sha256'], 'payload_sha256': prov.get('payload_sha256')},
            'coverage': {'rule': coverage_rule.RULE_TEXT, 'collection_date': side['cov']['collection_date'], 'current_cycle': side['open_cycle'],
                         'rule_coverage_end': side['cov']['rule_coverage_end'], 'coverage_end': side['cov']['coverage_end'],
                         'reason': side['cov']['reason'], 'flags': cov_flags},
            'status': {'general_giving': general['status'], 'candidate_receipts': candidates['status'],
                       'pac_to_candidates': pac_block['status'], 'lda': lda['status']},
            'general_giving': general, 'candidate_receipts': candidates, 'pac_to_candidates': pac_block, 'lda': lda, 'flags': flags}
    snapshot = {
        'schema_version': 1, 'kind': 'giving-anchors', 'built_at_utc': utc_now(),
        'builder': {'name': 'build_giving_anchors', 'version': VERSION, 'code_sha256': code_sha256(), 'python': platform.python_version()},
        'cpi': {'series': cpi_reader.SERIES, 'source_file': Path(options['controls']).name, 'source_sha256': controls_sha,
                'base_year': cpi_reader.BASE_YEAR, 'multipliers': multipliers},
        'thin_base_threshold_usd': threshold, 'industries': industries, 'unavailable': unavailable}
    nowcast_errors = lda_reader.nowcast_errors(lda_rows, lda_as_of) if lda_as_of else None          # the pooled blind errors that give the lobbying nowcast its bands
    if nowcast_errors:
        snapshot['lda_nowcast_errors'] = nowcast_errors
    done('assemble')
    return snapshot, inputs


OTHER_RULE_REASON = ('Its anchors in the published snapshot were built under a different exclusion rule than this build (receipts the accounting engine buckets as '
                     'non_giving are left out of the forecast: owner ruling 2026-10-01), so they are on another basis; rebuild it (a full build, or --industry <id>).')


def _excluded_sources(block):
    """The funding sources a published block says it left out, sorted; None when it names none or is not a list of names (anchors from before the ruling)."""
    names = block.get('excluded_funding_sources') if isinstance(block, dict) else None
    return sorted(names) if isinstance(names, list) and all(isinstance(name, str) for name in names) else None


def merge_with_published(snapshot, published_path, only):
    """A partial build (--industry) replaces only the industries it built: the others keep the anchors of the published snapshot, so one flag
    cannot wipe them. No valid published snapshot means the partial build stands alone. An industry whose published anchors were not built under
    the current exclusion rule is not carried over: its general-giving AND candidate blocks must both name exactly the funding sources this build
    leaves out (`excluded_funding_sources` equal to sorted(ms.FORECAST_EXCLUDED_FUNDING_SOURCES)). Anchors from before the 2026-10-01 ruling name
    none and an empty list names none either; mixing two bases in one snapshot would make its levels and shares incomparable, so such an industry
    is listed as unavailable until it is rebuilt."""
    try:
        old = read_json(published_path)
    except (OSError, ValueError):
        return snapshot
    if old.get('schema_version') != 1 or old.get('kind') != 'giving-anchors':
        return snapshot
    rule = sorted(ms.FORECAST_EXCLUDED_FUNDING_SOURCES)
    carried = {k: v for k, v in old['industries'].items() if k not in only}
    industries = {k: v for k, v in carried.items()
                  if isinstance(v, dict) and all(_excluded_sources(v.get(block)) == rule for block in ('general_giving', 'candidate_receipts'))}
    other_rule = {k: OTHER_RULE_REASON for k in carried if k not in industries}
    industries.update(snapshot['industries'])
    unavailable = {k: v for k, v in old.get('unavailable', {}).items() if k not in only and k not in industries}
    unavailable.update({k: v for k, v in other_rule.items() if k not in industries})
    unavailable.update({k: v for k, v in snapshot['unavailable'].items() if k not in industries})
    return {**snapshot, 'industries': industries, 'unavailable': unavailable}


def validate_snapshot(snapshot):
    problems = contract.validate_kind(snapshot, 'anchors')
    if not problems:
        problems = contract.check_anchor_invariants(snapshot)
    return problems


def replace_atomic(temp, final, attempts=40):
    for attempt in range(attempts):
        try:
            os.replace(temp, final)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 + 0.2 * ((attempt * 37) % 10) / 10)


def write_outputs(snapshot, inputs, output_dir, elapsed):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(snapshot, indent=1, ensure_ascii=False, allow_nan=False) + '\n'
    final = output_dir / SNAPSHOT_NAME
    temp = output_dir / (SNAPSHOT_NAME + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_bytes(text.encode('utf-8'))
    replace_atomic(temp, final)
    receipt = {'schema_version': 1, 'snapshot': SNAPSHOT_NAME, 'snapshot_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
               'built_at_utc': snapshot['built_at_utc'], 'elapsed_seconds': round(elapsed, 2), 'builder': snapshot['builder'],
               'industries': {k: {'status': v['status'], 'workbook_sha256': v['mentat']['workbook_sha256'], 'index_token': v['mentat']['index_token'],
                                  'coverage_end': v['coverage']['coverage_end']} for k, v in snapshot['industries'].items()},
               'unavailable': snapshot['unavailable'], 'inputs': inputs}
    rtext = json.dumps(receipt, indent=1, ensure_ascii=False, allow_nan=False) + '\n'
    rtemp = output_dir / (RECEIPT_NAME + '.' + uuid.uuid4().hex + '.tmp')
    rtemp.write_bytes(rtext.encode('utf-8'))
    replace_atomic(rtemp, output_dir / RECEIPT_NAME)
    return final, receipt


def check_fresh(snapshot_path, project_root, report_dir):
    """{industry id: 'fresh' | 'stale' | 'missing'}: the snapshot's workbook sha against the workbook published now."""
    snapshot = read_json(snapshot_path)
    out = {}
    for ident, ind in snapshot['industries'].items():
        workbook = Path(report_dir) / ind['mentat']['workbook']
        out[ident] = 'missing' if not workbook.is_file() else 'fresh' if ms.sha256_file(workbook) == ind['mentat']['workbook_sha256'] else 'stale'
    return out


def run_build(project_root, output_dir=None, only=None, registry_path=None, fec=None, lda_csv=None, controls=None, report_dir=None, run_id=None, lda_receipts=None, progress=False):
    started = time.time()
    project_root = Path(project_root)
    output_dir = Path(output_dir) if output_dir else default_output_dir(project_root)
    run = glassbox.Run(run_id or 'giving-anchors-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), progress=progress)
    run.start('Building the Giving Forecast anchor snapshot', 'project ' + str(project_root))
    try:
        registry = read_json(registry_path or HERE / 'registry.json')
        options = dict(report_dir=Path(report_dir) if report_dir else report_directory(project_root), fec_root=fec_root(project_root, fec),
                       lda_csv=lda_csv or default_lda_csv(), controls=Path(controls) if controls else default_controls(project_root), only=only, run=run,
                       lda_receipts=lda_receipts)
        snapshot, inputs = build_snapshot(project_root, registry, options)
        if only:
            snapshot = merge_with_published(snapshot, output_dir / SNAPSHOT_NAME, only)
        run.stage_start('validate', 'Validating against the shared schema and invariants')
        problems = validate_snapshot(snapshot) if snapshot['industries'] else ['no industry could be anchored: ' + json.dumps(snapshot['unavailable'])]
        if problems:
            run.stage_finish('validate', '; '.join(problems[:3]), status='failed')
            raise ValueError('The anchor snapshot is not valid; the previous snapshot is left in place: ' + '; '.join(problems[:5]))
        run.stage_finish('validate')
        run.stage_start('write', 'Writing the snapshot and its receipt')
        final, receipt = write_outputs(snapshot, inputs, output_dir, time.time() - started)
        run.stage_finish('write', str(final))
        run.finish('Anchor snapshot written: ' + ', '.join(sorted(snapshot['industries'])), json.dumps(snapshot['unavailable']))
        return final, receipt, snapshot
    except Exception as error:
        run.fail('Anchor build failed: ' + type(error).__name__, str(error))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project-root', type=Path, default=HERE.parents[1], help='the Mentat project folder (<checkout>\\mentat); default: this checkout')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--industry', action='append', help='build only this registry id (repeatable)')
    parser.add_argument('--registry', type=Path)
    parser.add_argument('--fec-root', type=Path, help='default: inputs/active_fec_sources.json')
    parser.add_argument('--lda-csv', type=Path)
    parser.add_argument('--controls', type=Path, help='00_controls.xlsx (CPI)')
    parser.add_argument('--report-directory', type=Path)
    parser.add_argument('--check', action='store_true', help='only report whether the existing snapshot still matches the published workbooks')
    args = parser.parse_args(argv)
    if args.check:
        output_dir = args.output_dir or default_output_dir(args.project_root)
        result = check_fresh(output_dir / SNAPSHOT_NAME, args.project_root, args.report_directory or report_directory(args.project_root))
        print(json.dumps(result, indent=1))
        return 0 if all(v == 'fresh' for v in result.values()) else 2
    try:
        final, receipt, snapshot = run_build(args.project_root, args.output_dir, args.industry, args.registry, args.fec_root, args.lda_csv, args.controls,
                                             args.report_directory, progress=True)
    except Exception as error:  # the message is the diagnosis; the previous snapshot is untouched
        print('FAILED: ' + str(error), file=sys.stderr)
        return 1
    print('wrote', final, 'sha256', receipt['snapshot_sha256'])
    for ident, ind in snapshot['industries'].items():
        print(' ', ident, 'coverage end', ind['coverage']['coverage_end'], 'status', json.dumps(ind['status']))
    for ident, reason in snapshot['unavailable'].items():
        print('  UNAVAILABLE', ident, '-', reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
