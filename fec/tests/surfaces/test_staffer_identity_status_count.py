"""IdentityWebIndex.status(): the scanned-people total is counted by SQLite, and is the total the Python loop gave.

old_status below is status() of the commit before the change, verbatim; the fixture database mixes states, empty
and long arrays and non-ASCII ids. An optional run on a copy of the live reference database is enabled with
STAFFER_IDREF_COPY=<path to a copy>; the copy is opened read-only and the live file is never touched.
"""
import json
import os
import random
import sqlite3

import pytest

from fec.surfaces import staffer_identity_web as web

CHECKED = ('referenced', 'unresolved')


def old_status(index):
    with index.connect() as db:
        states = dict(db.execute('SELECT state,count(*) FROM queue GROUP BY state'))
        total = len({json.loads(row[0]).get('sha256') for row in db.execute('SELECT payload FROM documents')})
        last = db.execute("SELECT max(updated_at) FROM queue WHERE state IN ('referenced','unresolved')").fetchone()[0]
        errors = [row[0] for row in db.execute("SELECT error FROM queue WHERE state='retry' AND error IS NOT NULL GROUP BY error ORDER BY max(updated_at) DESC LIMIT 3")]
        total_people = db.execute("SELECT value FROM metadata WHERE key='total_people'").fetchone()
        scanned_people = sum(len(json.loads(row[0])) for row in db.execute("SELECT node_ids FROM queue WHERE updated_at IS NOT NULL AND state IN ('referenced','unresolved')"))
        outcomes = dict(db.execute('SELECT outcome,count(*) FROM reference_outcomes GROUP BY outcome'))
    queued = states.get('queued', 0) + states.get('checking', 0) + states.get('retry', 0)
    return {'status': 'checking_references' if queued else 'cycle_complete', 'queued': queued,
            'total_people': int(total_people[0]) if total_people else None, 'scanned_people': scanned_people,
            'retrying': states.get('retry', 0), 'attention': states.get('retry', 0),
            'referenced': states.get('referenced', 0), 'unresolved': states.get('unresolved', 0),
            'checked': states.get('referenced', 0) + states.get('unresolved', 0),
            'references_verified': total, 'reference_outcomes': outcomes, 'last_completed_at': last, 'errors': errors}


@pytest.fixture
def index(tmp_path, monkeypatch):
    seeds = tmp_path / 'seeds.json'
    seeds.write_text(json.dumps({'references': []}))
    monkeypatch.setattr(web, 'SEEDS', seeds)
    return web.IdentityWebIndex(tmp_path)


def fill(index, rows, seed=1):
    """rows: (state, updated_at, node_ids list) triples; returns the expected total by the independent rule."""
    rng = random.Random(seed)
    with index.connect() as db:
        for number, (state, updated_at, node_ids) in enumerate(rows):
            db.execute('INSERT INTO queue(key,name,node_ids,priority,state,updated_at,error) VALUES(?,?,?,?,?,?,?)',
                       (f'family {number}', f'Person {number}', json.dumps(node_ids, ensure_ascii=rng.random() < .5),
                        1, state, updated_at, 'timeout' if state == 'retry' else None))
        for number in range(40):
            db.execute('INSERT INTO documents(key,name,payload) VALUES(?,?,?)',
                       (f'doc{number}', 'Person', json.dumps({'sha256': f'sha{number % 25}', 'text': 'x'})))
        db.execute("INSERT OR REPLACE INTO metadata VALUES('total_people','123')")
        for number in range(30):
            db.execute('INSERT INTO reference_outcomes(key,name,url,outcome,recorded_at) VALUES(?,?,?,?,?)',
                       (f'family {number}', 'n', f'https://example.org/{number}', ('accepted', 'rejected', 'terminal')[number % 3], 1.0))
    return sum(len(ids) for state, updated, ids in rows if updated is not None and state in CHECKED)


def random_rows(count, seed):
    rng = random.Random(seed)
    states = ['queued', 'checking', 'retry', 'referenced', 'unresolved']
    pool = ['person:lda:lobbyist:%d' % i for i in range(500)] + ['politico:uuid-%d' % i for i in range(500)] + ['person:Jos\u00e9 \u201cquoted\u201d \u4e2d\u6587']
    rows = []
    for _ in range(count):
        size = rng.choice([0, 0, 1, 1, 2, 3, 5, 8, 30])
        rows.append((rng.choice(states), rng.choice([None, '2026-09-%02dT00:00:00Z' % rng.randint(1, 28)]),
                     rng.sample(pool, size)))
    return rows


def test_status_is_identical_to_the_python_loop_version_on_a_mixed_database(index):
    expected = fill(index, random_rows(3000, seed=7))
    assert expected > 1000, 'the fixture must exercise the count'
    status = index.status()
    assert status['scanned_people'] == expected
    assert status == old_status(index)


@pytest.mark.parametrize('rows', [
    [],
    [('queued', None, ['a', 'b'])],                                   # nothing has been checked yet
    [('referenced', None, ['a'])],                                    # checked states without a completion time are not counted
    [('referenced', '2026-09-01T00:00:00Z', [])],                     # an empty family counts zero
    [('unresolved', '2026-09-01T00:00:00Z', ['a', 'a', 'b'])],        # repeats are counted, as before
    [('retry', '2026-09-01T00:00:00Z', ['a']), ('checking', '2026-09-01T00:00:00Z', ['b'])],
])
def test_boundary_databases_give_the_same_status(index, rows):
    expected = fill(index, rows)
    assert index.status()['scanned_people'] == expected
    assert index.status() == old_status(index)


def test_the_count_is_one_database_query_not_a_row_loop(index):
    fill(index, random_rows(50, seed=3))
    statements = []
    with index.connect() as db:
        db.set_trace_callback(statements.append)
        web.IdentityWebIndex._scanned_people(db)
    assert len(statements) == 1 and 'json_array_length' in statements[0]


def test_a_sqlite_without_json_functions_gives_the_same_total_through_the_loop(index):
    expected = fill(index, random_rows(300, seed=11))

    class WithoutJson:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if 'json_array_length' in sql:
                raise sqlite3.OperationalError('no such function: json_array_length')
            return self.db.execute(sql, *args)
    with index.connect() as db:
        assert web.IdentityWebIndex._scanned_people(WithoutJson(db)) == expected

    class Broken:
        def execute(self, sql, *args):
            raise sqlite3.OperationalError('no such table: queue')
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        web.IdentityWebIndex._scanned_people(Broken())


@pytest.mark.skipif(not os.environ.get('STAFFER_IDREF_COPY'), reason='set STAFFER_IDREF_COPY to a copy of the live reference database')
def test_the_count_matches_the_loop_on_a_copy_of_the_live_reference_database():
    path = os.environ['STAFFER_IDREF_COPY']
    db = sqlite3.connect('file:' + path.replace('\\', '/') + '?mode=ro', uri=True)
    try:
        rows = db.execute("SELECT count(*) FROM queue WHERE updated_at IS NOT NULL AND state IN ('referenced','unresolved')").fetchone()[0]
        looped = sum(len(json.loads(row[0])) for row in db.execute("SELECT node_ids FROM queue WHERE updated_at IS NOT NULL AND state IN ('referenced','unresolved')"))
        counted = web.IdentityWebIndex._scanned_people(db)
        print('live copy: %d checked families, %d people (loop) vs %d (sql)' % (rows, looped, counted))
        assert rows > 0 and counted == looped
    finally:
        db.close()