"""Insights records counted and paged on the narrow projection answer exactly what the full index
answered (same count, rows, order); every irregular stored date is still judged by the UDF. The
faster row stream, context pass and options memo give the same values as before."""
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import analysis_data as a  # noqa: E402
import analysis_history as h  # noqa: E402
import analysis_metrics as metrics  # noqa: E402
import analysis_scan  # noqa: E402

IRREGULAR = ['2024-02-30', '20240115', '2024-W03-2', '', ' 2024-01-01', '2023-13-01', '0000-01-01', '2024-1-05',
             '2024-02-29T00:00']


def build_index(path, seed=11, count=400):
    rng = random.Random(seed)
    with closing(sqlite3.connect(path)) as db:
        a.data._schema(db)
        dims = list(a.data.DIMENSIONS)
        for index in range(count):
            day = date(2021, 1, 1) + timedelta(days=rng.randrange(0, 2100))
            value = rng.choice(IRREGULAR) if rng.random() < 0.12 else day.isoformat()
            year = day.year
            row = {key: '' for key in dims}
            row.update(source_id=f'{rng.randrange(10**6):06d}-{index}', industry='Example',
                       cycle=f'{year - 1 + year % 2}-{year + year % 2}' if rng.random() > 0.05 else 'bad',
                       year=str(year), company=rng.choice(['Acme', 'Beta', 'Gamma', '']),
                       date=value, committee_id=rng.choice(['C00000001', 'C00000002', 'C12345678', 'X1', '']),
                       recipient=rng.choice(['Friends of One', 'Two for Congress', 'Three PAC']),
                       funding_source=rng.choice(['employee_original', 'corporate_original', '']),
                       party=rng.choice(['DEM', 'REP', '']), state=rng.choice(['MN', 'NY', 'CA']))
            money = {'raw_cents': rng.randrange(-5000, 90000), 'receipt_cents': rng.choice([0, rng.randrange(-900, 90000)]),
                     'original_cents': rng.choice([0, rng.randrange(1, 5000)]), 'supported_cents': 0,
                     'pac_cents': rng.choice([0, 0, rng.randrange(1, 900)])}
            columns = ['source_id', *dims, 'contributor_key', 'raw_zip', *money, 'unresolved', 'reasons', 'evidence_ids',
                       'source_url', 'source_table']
            values = [row['source_id'], *[row[key] for key in dims], f'reported:D{index % 17}', '55401', *money.values(),
                      0, '[]', '[]', None, 'fixture']
            db.execute('INSERT INTO facts(' + ','.join('"' + c + '"' for c in columns) + ') VALUES (' +
                       ','.join('?' * len(columns)) + ')', values)
        db.execute('INSERT INTO metadata VALUES (?,?)', ('manifest', json.dumps({'provenance': {}})))
        db.commit()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / 'index.sqlite'
        build_index(self.path)
        self.source = {'id': 'Example', 'industry': 'Example'}
        patches = [patch.object(h, 'ROOT', self.tmp / 'history'),
                   patch.object(a.store, 'ROOT', self.tmp / 'query-cache'),
                   patch.object(a.data, '_source', return_value=self.source),
                   patch.object(a.data, '_ready', return_value=(self.path, None)),
                   patch.object(a.data, '_token', side_effect=lambda source: 'fixture-token'),
                   patch.object(a.data, '_provenance', return_value={})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        a._date_census.clear()


class RecordsTests(Fixture):
    REQUESTS = [
        {}, {'offset': 7, 'limit': 13}, {'limit': 5000}, {'offset': 10 ** 6},
        {'company': 'Acme'}, {'company': 'Beta', 'as_of': '2023-06-30'},
        {'filters': [{'field': 'date', 'op': 'gte', 'value': '2022-01-01'}, {'field': 'date', 'op': 'lte', 'value': '2022-12-31'}]},
        {'filters': [{'field': 'cycle', 'op': 'eq', 'value': '2023-2024'}]},
        {'filters': [{'field': 'funding_source', 'op': 'eq', 'value': 'employee_original'},
                     {'field': 'committee_id', 'op': 'eq', 'value': 'C00000001'}]},
        {'filters': [{'field': 'committee_id', 'op': 'ne', 'value': ''}], 'as_of': '2022-03-01'},
        {'filters': [{'field': 'amount', 'op': 'gte', 'value': 50000}]},
        {'filters': [{'field': 'company', 'op': 'in', 'value': ['Acme', 'Gamma']}]},
        {'filters': [{'field': 'recipient', 'op': 'contains', 'value': 'congress'}]},
        {'basis': 'original_observed'}, {'basis': 'pac_activity', 'as_of': '2021-06-01'},
        {'as_of': '2024-12-31'},              # a cutoff inside the year of the stored week-date values
        {'filters': [{'field': 'state', 'op': 'eq', 'value': 'MN'}]},     # not projected: full index
    ]

    def records(self, request, narrow=True):
        body = {'dataset': 'Example', 'as_of': '2026-09-24', **request}
        if narrow:
            return a.records(self.tmp, dict(body))
        with patch.object(a, '_projection', lambda path: None):
            return a.records(self.tmp, dict(body))

    def test_projected_records_equal_full_index_records(self):
        analysis_scan.view(self.path)              # the projection an Insights query builds first
        used = []
        real = a._projected_page

        def spy(*args):
            answer = real(*args)
            used.append(answer is not None)
            return answer
        with patch.object(a, '_projected_page', spy):
            for request in self.REQUESTS:
                with self.subTest(request=request):
                    used.clear()
                    narrow, wide = self.records(request), self.records(request, narrow=False)
                    self.assertEqual(json.dumps(narrow, sort_keys=True), json.dumps(wide, sort_keys=True))
                    projected = all(item.get('field') in a._PROJECTED for item in request.get('filters', []))
                    self.assertEqual(used[:1], [True] if projected else [])
        self.assertTrue(a._irregular_dates(a._projection(self.path)))   # the fixture's odd dates were found

    def test_every_irregular_stored_date_is_left_to_the_udf(self):
        projection = analysis_scan.view(self.path)
        irregular = set(a._irregular_dates(projection))
        with closing(sqlite3.connect(projection)) as db:
            stored = {row[0] for row in db.execute('SELECT DISTINCT date FROM facts')}
        self.assertEqual(irregular, {value for value in stored if not a._canonical_date(value)})
        self.assertTrue(set(IRREGULAR) & stored)
        self.assertEqual(set(IRREGULAR) & stored, set(IRREGULAR) & irregular)
        rng = random.Random(3)
        for _ in range(3000):
            day = date(1, 1, 1) + timedelta(days=rng.randrange(0, 3_652_000))
            text = day.isoformat()
            cutoff = date(1, 1, 1) + timedelta(days=rng.randrange(0, 3_652_000))
            self.assertTrue(a._canonical_date(text))
            self.assertEqual(text <= cutoff.isoformat(), day <= cutoff)
        for value in IRREGULAR + [None, 20240115, b'2024-01-05', '2024-01-01 ', '\uff12\uff10\uff12\uff14-01-01']:
            self.assertFalse(a._canonical_date(value), value)

    def test_without_a_verified_projection_the_full_index_answers(self):
        calls = []
        with patch.object(a, '_projected_page', lambda *args: calls.append(args)):
            first = self.records({})                 # no projection built yet
        self.assertEqual(calls, [])
        projection = analysis_scan.view(self.path)
        with closing(sqlite3.connect(projection)) as db:
            db.execute("UPDATE facts SET company='Tampered' WHERE rowid=1")
            db.commit()
        a.store._verified.clear()
        with patch.object(a, '_projected_page', lambda *args: calls.append(args)):
            second = self.records({})
        self.assertEqual(calls, [])                  # the proof no longer verifies
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


def reference_rows(path, request):
    column = a.data.BASES[request['basis']][1]
    where, params = a.data._row_filter(a._scope_filters(request), column)
    fields = ('source_id,industry,company,committee_id,recipient,date,cycle,funding_source,party,'
              'contributor_key,raw_cents,receipt_cents,original_cents,supported_cents,pac_cents')
    with closing(a.data._ro(path)) as db:
        for row in db.execute('SELECT ' + fields + ',' + column + ' AS amount_cents FROM facts WHERE ' + where + ' AND ' + column + ' != 0', params):
            yield dict(row)


def reference_context_rows(rows, context, request):
    pairs, companies = set(), {request['company']} if request.get('company') else set()
    cutoff = date.fromisoformat(request['as_of'])
    for row in rows:
        try:
            observed = date.fromisoformat(row['date'])
            eligible = observed <= cutoff
        except (ValueError, TypeError):
            eligible = False
        cycle = metrics._cycle(row['cycle'])
        if eligible and cycle and observed.year in (cycle - 1, cycle):
            companies.add(row['company'])
            if re.fullmatch(r'C\d{8}', row['committee_id'] or ''):
                pairs.add((row['committee_id'], cycle))
        yield row
    context.update(recipient_cycles=[{'recipient_id': recipient, 'cycle': cycle} for recipient, cycle in sorted(pairs)],
                   recipient_ids=sorted({p[0] for p in pairs}), cycles=sorted(set(context.get('cycles', [])) | {p[1] for p in pairs}), companies=sorted(companies))


class StreamTests(Fixture):
    def test_row_stream_is_the_same_dicts_in_the_same_order(self):
        projection = analysis_scan.view(self.path)
        for request in ({'basis': 'receipt'}, {'basis': 'original_observed', 'company': 'Acme'}):
            scoped = a.validate({'dataset': 'Example', 'as_of': '2026-09-24', **request})
            new, old = list(a._rows(projection, scoped)), list(reference_rows(projection, scoped))
            self.assertEqual(new, old)
            self.assertEqual([list(row) for row in new], [list(row) for row in old])

    def test_context_pass_matches_the_per_row_tests_on_mixed_types(self):
        rng = random.Random(5)
        dates = ['2024-03-01', '2023-12-31', '2025-01-01', '2020-05-05', 'bad', '', None, 20240301, b'2024-03-01', '20240301']
        cycles = ['2023-2024', '2024', 2024, 2024.0, True, '2025-2026', 'bad', '', None, ' 2024 ']
        committees = ['C00000001', 'C12345678', 'C1234567', 'X00000001', '', None, 'C00000001 ']
        # A company and a committee of their own per row, so every row's own eligibility shows in the context.
        rows = [{'date': rng.choice(dates), 'cycle': rng.choice(cycles),
                 'committee_id': rng.choice(committees + [f'C{index:08d}']), 'company': f'company {index}'}
                for index in range(4000)]
        for request in ({'as_of': '2024-06-30'}, {'as_of': '2026-01-01', 'company': 'Z'}):
            new_context, old_context = {'cycles': [2022]}, {'cycles': [2022]}
            self.assertEqual(list(a._context_rows(iter(rows), new_context, request)),
                             list(reference_context_rows(iter(rows), old_context, request)))
            self.assertEqual(new_context, old_context)

    def test_options_memo_answers_what_the_query_answers_and_follows_the_file(self):
        projection = analysis_scan.view(self.path)
        a._option_cache.clear()
        first = a._options(projection, 'receipt')
        first['companies'].append('mutated by a caller')
        again = a._options(projection, 'receipt')          # served from the memo
        self.assertNotIn('mutated by a caller', again['companies'])
        again['companies'].append('mutated after a hit')
        self.assertNotIn('mutated after a hit', a._options(projection, 'receipt')['companies'])
        again['companies'].pop()
        self.assertEqual(len(a._option_cache), 1)
        with patch.object(a, '_option_cache', {}):
            self.assertEqual(a._options(projection, 'receipt'), again)
        with closing(sqlite3.connect(projection)) as db:
            db.execute("UPDATE facts SET company='Zeta' WHERE company='Acme'")
            db.commit()
        changed = a._options(projection, 'receipt')
        self.assertIn('Zeta', changed['companies'])
        self.assertNotEqual(changed, again)


if __name__ == '__main__':
    unittest.main()
