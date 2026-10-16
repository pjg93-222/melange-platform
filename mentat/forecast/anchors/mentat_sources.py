"""Read-only access to Mentat's published report, its contribution-analytics index and the pinned source database.

EVERY SQLite file is opened `mode=ro&immutable=1` (plus PRAGMA query_only=ON), so nothing here can write, checkpoint or lock a
database. No money-collection or receipt-counting code is imported, run or edited: the index is read as the console built it
(`facts`, one row per FEC sub_id; `receipt_cents` = the published workbook's included Analysis Amount), and the FEC recipient committee
type that the index lacks comes from the pinned source database, joined on sub_id (as the phase-2 design did, P08).

ONE RULE, applied to the FORECAST only (owner ruling 2026-10-01, forecast-integrity decision D4 option b): FORECAST_EXCLUDED_FUNDING_SOURCES names the
receipts the anchors leave out. The workbook, the index and every money-collection view still count them in the Analysis Amount.
"""
import hashlib
import json
from pathlib import Path
import sqlite3

STATE_INDEX_DIR = Path('state') / 'contribution-analytics'

# Owner ruling 2026-10-01 (Paul, "D4, B"): receipts that Mentat's accounting engine buckets as non_giving (the FEC records them as interest,
# refunds, insurance claims, loans) are not giving, so the FORECAST leaves them out of (1) every cycle's general-giving level, (2) the monthly
# observed cuts and the observed-to-date amounts, and (3) the House / Senate included-receipt candidate bases. The index column `funding_source` IS the
# engine's bucket (the console index assigns it from the engine's decision, contribution_data.py), so the bucket name is the whole rule. Every
# query below takes its "counted" and "excluded" conditions from forecast_conditions(); nothing else in the anchors names the bucket.
FORECAST_EXCLUDED_FUNDING_SOURCES = frozenset({'non_giving'})


def ro_uri(path):
    return Path(path).resolve().as_uri() + '?mode=ro&immutable=1'


def ro_connect(path):
    db = sqlite3.connect(ro_uri(path), uri=True)
    db.execute('PRAGMA query_only=ON')
    return db


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 22), b''):
            digest.update(chunk)
    return digest.hexdigest()


def find_index(index_dir, workbook_sha256):
    """The newest contribution-analytics index built from exactly this published workbook: (path, manifest) or None.
    Unfinished builds (*.pending) and unreadable files are skipped."""
    best = None
    for path in sorted(Path(index_dir).glob('*.sqlite')):
        try:
            db = ro_connect(path)
            try:
                row = db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
            finally:
                db.close()
            manifest = json.loads(row[0])
        except (sqlite3.Error, TypeError, ValueError):
            continue
        if manifest.get('provenance', {}).get('workbook_sha256') != workbook_sha256:
            continue
        if best is None or manifest.get('built_at', 0) > best[1].get('built_at', 0):
            best = (path, manifest)
    return best


def find_source_db(project_root, patterns, expected_sha256, hashes=None):
    """The pinned source database: the first file matching a registry glob (under the Mentat project folder, newest first) whose sha256
    equals the index's `database_sha256`. None if none does."""
    hashes = {} if hashes is None else hashes
    for pattern in patterns:
        for path in sorted(Path(project_root).glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key not in hashes:
                hashes[key] = sha256_file(path)
            if hashes[key] == expected_sha256:
                return path
    return None


def _tables(db, schema='main'):
    return {r[0] for r in db.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")}


def collection_date(source_db, industry):
    """The latest scrape timestamp of this industry's rows in the pinned source DB, as a date ('YYYY-MM-DD')."""
    db = ro_connect(source_db)
    try:
        tables = _tables(db)
        stamps = []
        for table in ('contributions', 'corporate_giving'):
            if table in tables:
                value = db.execute(f'SELECT MAX(scraped_at) FROM {table} WHERE industry=?', (industry,)).fetchone()[0]
                if value:
                    stamps.append(str(value))
        if not stamps:
            raise ValueError('The pinned source database has no scrape timestamps for ' + industry)
        return max(stamps)[:10]
    finally:
        db.close()


def work_progress(source_db, open_cycle):
    """The open cycle's api_work_progress completion counts {fetched_complete, stored_complete, units}; None when the table is absent or empty."""
    db = ro_connect(source_db)
    try:
        if 'api_work_progress' not in _tables(db):
            return None
        fields = {row[1] for row in db.execute('PRAGMA table_info(api_work_progress)')}
        if {'unit_id', 'company', 'kind', 'rows_seen'}.issubset(fields):
            # Match validate_industry_coverage.api_progress_grid: verified date-window
            # children are fetched checkpoints, stored once by their exact parent.
            query = """
                WITH window_parents AS (
                    SELECT substr(unit_id, 1, instr(unit_id, '::window::')-1) AS parent_id,
                           company, cycle, kind, SUM(rows_seen) AS child_rows,
                           MIN(COALESCE(fetched_complete, 0)) AS children_fetched,
                           MIN(CASE WHEN typeof(rows_seen)='integer' AND rows_seen>=0
                                    THEN 1 ELSE 0 END) AS child_counts_valid
                    FROM api_work_progress WHERE instr(unit_id, '::window::')>0
                    GROUP BY parent_id, company, cycle, kind
                ), stored_windows AS (
                    SELECT p.unit_id, p.company, p.cycle, p.kind
                    FROM api_work_progress p JOIN window_parents w
                      ON p.unit_id=w.parent_id AND p.company=w.company
                         AND p.cycle=w.cycle AND p.kind=w.kind
                    WHERE p.fetched_complete=1 AND p.stored_complete=1
                      AND w.children_fetched=1 AND w.child_counts_valid=1
                      AND typeof(p.rows_seen)='integer' AND p.rows_seen>=0
                      AND w.child_rows=p.rows_seen
                )
                SELECT SUM(fetched_complete), SUM(stored_complete), COUNT(*)
                FROM api_work_progress AS progress WHERE cycle=? AND NOT EXISTS (
                    SELECT 1 FROM stored_windows p
                    WHERE p.unit_id=substr(progress.unit_id, 1, instr(progress.unit_id, '::window::')-1)
                      AND p.company=progress.company AND p.cycle=progress.cycle AND p.kind=progress.kind
                      AND instr(progress.unit_id, '::window::')>0
                )
            """
        else:
            query = 'SELECT SUM(fetched_complete), SUM(stored_complete), COUNT(*) FROM api_work_progress WHERE cycle=?'
        r = db.execute(query, (open_cycle,)).fetchone()
        if not r or not r[2]:
            return None
        return {'fetched_complete': int(r[0] or 0), 'stored_complete': int(r[1] or 0), 'units': int(r[2])}
    finally:
        db.close()


def _cycle_key(label):
    """'2023-2024' -> '2024'."""
    return label.split('-')[-1]


def _cumulative(monthly, cuts):
    """[(cut date, running total of every month <= the cut's month)] from {'YYYY-MM': cents}."""
    out = []
    for cut in cuts:
        out.append((cut, sum(v for month, v in monthly.items() if month <= cut[:7])))
    return out


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def forecast_conditions(alias='f'):
    """(counted, excluded): the two SQL conditions on `alias`.funding_source that split every included receipt by FORECAST_EXCLUDED_FUNDING_SOURCES.
    Exactly one holds for any row (a NULL source counts), so counted + excluded = the published Analysis Amount."""
    listed = ', '.join(_quote(source) for source in sorted(FORECAST_EXCLUDED_FUNDING_SOURCES))
    excluded = f"COALESCE({alias}.funding_source, '') IN ({listed})"
    return f'NOT ({excluded})', excluded


def index_aggregates(index_path, source_db, open_cycle, cuts):
    """Everything the snapshot needs from one industry's index (+ its pinned source DB for chamber). Dollars are returned as integer CENTS.

    {'cycles': {'2024': cents}, 'analysis_cycles': {'2024': cents}, 'excluded': {'2024': cents}, 'by_source': {'2024': {source: cents}},
     'general_cuts': [(cut, cents)], 'after_cut': cents,
     'candidates': {'2024': {'House': {'D','R','other'}, 'Senate': {...}}}, 'candidates_excluded': {same shape}, 'candidate_cuts': [(cut, {'House':..., 'Senate':...})],
     'unmatched': (n, cents), 'type_conflicts': n, 'odd_year': {'2024': cents}}
    FORECAST BASIS (owner ruling 2026-10-01): `cycles`, `general_cuts`, `after_cut`, `candidates`, `candidate_cuts` and `unmatched` count only the receipts whose
    funding source is NOT in FORECAST_EXCLUDED_FUNDING_SOURCES. `analysis_cycles` is the published Analysis Amount before that exclusion (what the index's own
    manifest records), `excluded` / `candidates_excluded` what the rule left out (cycles + excluded = analysis_cycles), and `by_source` the full split by the
    engine's bucket. `odd_year` is the forecast-basis amount of each COMPLETE cycle dated on or before December 31 of its first year (cents). `cuts` are month-end ISO dates of the open cycle (ending on the coverage end)."""
    label = f'{open_cycle - 1}-{open_cycle}'
    counted, excluded = forecast_conditions('f')
    kept_sum = f'SUM(CASE WHEN {counted} THEN f.receipt_cents ELSE 0 END)'
    left_out_sum = f'SUM(CASE WHEN {excluded} THEN f.receipt_cents ELSE 0 END)'
    db = sqlite3.connect('file::memory:', uri=True)
    try:
        db.execute('ATTACH DATABASE ? AS idx', (ro_uri(index_path),))
        db.execute('ATTACH DATABASE ? AS src', (ro_uri(source_db),))
        analysis, cycles, left_out = {}, {}, {}
        for c, total, kept, dropped in db.execute(f'SELECT f.cycle, SUM(f.receipt_cents), {kept_sum}, {left_out_sum} FROM idx.facts f '
                                                  'GROUP BY f.cycle'):
            analysis[_cycle_key(c)], cycles[_cycle_key(c)], left_out[_cycle_key(c)] = total, kept, dropped
        by_source = {}
        for c, source, v in db.execute('SELECT cycle, funding_source, SUM(receipt_cents) FROM idx.facts WHERE receipt_cents != 0 GROUP BY 1, 2'):
            by_source.setdefault(_cycle_key(c), {})[source] = v
        # Odd-year split of every COMPLETE cycle (cycle model, 2026-10-07): the receipts dated on or before December 31 of the cycle's first year. An undated row
        # is part of the cycle total and of no odd-year amount, as for the observed cuts.
        odd_year = {}
        for c, v in db.execute(f"SELECT f.cycle, SUM(CASE WHEN {counted} AND COALESCE(f.date, '') != '' AND f.date <= substr(f.cycle, 1, 4) || '-12-31' "
                               'THEN f.receipt_cents ELSE 0 END) FROM idx.facts f WHERE f.receipt_cents != 0 GROUP BY f.cycle'):
            if int(_cycle_key(c)) < open_cycle:
                odd_year[_cycle_key(c)] = v
        monthly = {m: v for m, v in db.execute('SELECT substr(f.date, 1, 7), SUM(f.receipt_cents) FROM idx.facts f '
                                               f"WHERE f.cycle=? AND f.receipt_cents != 0 AND {counted} AND COALESCE(f.date, '') != '' GROUP BY 1", (label,))}
        last_cut = cuts[-1] if cuts else None
        after_cut = db.execute('SELECT COALESCE(SUM(f.receipt_cents), 0) FROM idx.facts f '
                               f'WHERE f.cycle=? AND f.receipt_cents != 0 AND {counted} AND f.date > ?',
                               (label, last_cut)).fetchone()[0] if last_cut else 0
        tables = _tables(db, 'src')
        parts = [f'SELECT CAST(sub_id AS TEXT) AS sid, recipient_committee_type AS t FROM src.{t}'
                 for t in ('contributions', 'corporate_giving') if t in tables]
        if not parts:
            raise ValueError('The pinned source database has neither contributions nor corporate_giving')
        db.execute("CREATE TEMP TABLE rt AS SELECT sid, CASE WHEN COUNT(DISTINCT t)=1 THEN MAX(t) END AS t, "
                   "COUNT(DISTINCT t) AS nt, MAX(CASE WHEN t IN ('H','S') THEN 1 ELSE 0 END) AS has_candidate "
                   'FROM (' + ' UNION ALL '.join(parts) + ') GROUP BY sid')
        db.execute('CREATE INDEX temp.rt_sid ON rt(sid)')
        conflict_exposure = db.execute('SELECT COUNT(*), COALESCE(SUM(f.receipt_cents),0), COALESCE(SUM(ABS(f.receipt_cents)),0) '
                                       'FROM idx.facts f JOIN rt ON rt.sid=f.source_id '
                                       f'WHERE f.receipt_cents != 0 AND {counted} AND rt.nt>1 AND rt.has_candidate=1').fetchone()
        conflicts = conflict_exposure[0]
        unmatched = db.execute('SELECT COUNT(*), COALESCE(SUM(f.receipt_cents), 0) FROM idx.facts f LEFT JOIN rt ON rt.sid = f.source_id '
                               f'WHERE f.receipt_cents != 0 AND {counted} AND rt.sid IS NULL').fetchone()

        def party(p):
            return p if p in ('D', 'R') else 'other'

        def blank():
            return {'House': {'D': 0, 'R': 0, 'other': 0}, 'Senate': {'D': 0, 'R': 0, 'other': 0}}
        # One pass over the join gives both sums, so a cycle that has candidate rows keeps its (possibly all-zero) entry once its excluded rows are left out.
        candidates, candidates_excluded = {}, {}
        for c, chamber, p, kept, dropped in db.execute(f'SELECT f.cycle, rt.t, f.party, {kept_sum}, {left_out_sum} FROM idx.facts f JOIN rt ON rt.sid = f.source_id '
                                                       "WHERE f.receipt_cents != 0 AND rt.t IN ('H', 'S') GROUP BY 1, 2, 3"):
            name = 'House' if chamber == 'H' else 'Senate'
            candidates.setdefault(_cycle_key(c), blank())[name][party(p)] += kept
            candidates_excluded.setdefault(_cycle_key(c), blank())[name][party(p)] += dropped
        monthly_c = {}
        for month, chamber, p, v in db.execute("SELECT substr(f.date, 1, 7), rt.t, f.party, SUM(f.receipt_cents) FROM idx.facts f JOIN rt ON rt.sid = f.source_id "
                                               f"WHERE f.cycle=? AND f.receipt_cents != 0 AND {counted} AND COALESCE(f.date, '') != '' AND rt.t IN ('H', 'S') GROUP BY 1, 2, 3", (label,)):
            monthly_c.setdefault(month, blank())['House' if chamber == 'H' else 'Senate'][party(p)] += v
        candidate_cuts = []
        for cut in cuts:
            running = blank()
            for month, part in monthly_c.items():
                if month <= cut[:7]:
                    for chamber in ('House', 'Senate'):
                        for k in ('D', 'R', 'other'):
                            running[chamber][k] += part[chamber][k]
            candidate_cuts.append((cut, running))
        return {'cycles': cycles, 'analysis_cycles': analysis, 'excluded': left_out, 'by_source': by_source, 'general_cuts': _cumulative(monthly, cuts),
                'after_cut': after_cut, 'candidates': candidates, 'candidates_excluded': candidates_excluded, 'candidate_cuts': candidate_cuts,
                'unmatched': tuple(unmatched), 'type_conflicts': conflicts,
                'type_conflict_exposure': {'rows': conflicts, 'net_cents': conflict_exposure[1], 'gross_cents': conflict_exposure[2]}, 'odd_year': odd_year}
    finally:
        db.close()
