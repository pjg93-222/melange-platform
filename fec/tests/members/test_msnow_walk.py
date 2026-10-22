"""MS NOW archive walk (fetch_interviews.run_msnow): newest-first, caught-up early stop, budget.

Incident 2026-10-18: the weekly `interviews: MS NOW` step hit its 3600 s timeout again.  The old walk
read the WHOLE WordPress archive oldest-first (215 pages, ~660 MB) on every run only to find zero new
posts, and printed nothing unless a page produced member turns, so a killed run left no diagnosis.
The walk now goes newest-first, stops at the first page that holds nothing unseen (when the previous
walk is known to have left no gap), prints one line per page, and ends on a wall-clock budget below the
callers' 3600 s with a PARTIAL receipt instead of being killed.

Everything here is offline: a scripted paged WordPress API stands in for ms.now, and the real run_msnow
runs against a private SQLite file and receipt directory.
"""
import ast
import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))


def import_under_scratch_root(name):
    """Import `name` with MONEYBALL_ROOT on a scratch root whose roster is empty.

    fetch_interviews reads data/engine/congestion.json when it is imported and paths.ROOT is fixed the
    first time paths is imported, so without the private data tree this suite could not even be collected.
    The project modules the import loads are forgotten afterwards, so no other test inherits a module
    bound to the scratch root (the same helper sits in test_refresh_cspan_wj_errors.py)."""
    root = tempfile.mkdtemp(prefix="moneyball_root_")
    before = dict(sys.modules)
    try:
        os.makedirs(os.path.join(root, "data", "engine"))
        Path(root, "data", "engine", "congestion.json").write_text("[]", encoding="utf-8")
        sys.modules.pop("paths", None)   # paths.ROOT is read once, at its first import
        with mock.patch.dict(os.environ, {"MONEYBALL_ROOT": root}):
            return importlib.import_module(name)
    finally:
        for key in [k for k in sys.modules if k not in before]:
            module = sys.modules[key]
            where = getattr(module, "__file__", None) or next(iter(getattr(module, "__path__", [])), "")
            if str(where).startswith(str(FEC)):   # project modules only; stdlib modules stay shared
                del sys.modules[key]
                parent, _, child = key.rpartition(".")
                if parent in before and getattr(before[parent], child, None) is module:
                    delattr(before[parent], child)
        if "paths" in before:
            sys.modules["paths"] = before["paths"]
        shutil.rmtree(root, ignore_errors=True)


interviews = import_under_scratch_root("members.fetch_interviews")

PER_PAGE = 50


class Site:
    """A scripted /wp-json/wp/v2/transcript endpoint: ids 1..n, id order == date order."""

    def __init__(self, count):
        self.posts = [self.post(i) for i in range(1, count + 1)]
        self.requests = []
        self.fail_pages = {}
        self.tick = lambda: None

    @staticmethod
    def post(i):
        return {'id': i, 'date': (date(2020, 1, 1) + timedelta(days=i)).isoformat() + 'T00:00:00',   # one post a day
                'link': 'https://www.ms.now/transcripts/post-%d' % i,
                'content': {'rendered': '<p>JANE DOE: transcript body %d</p>' % i}}

    def publish(self, *ids):
        self.posts += [self.post(i) for i in ids]

    def get(self, url, tries=3):
        self.requests.append(url)
        self.tick()
        query = parse_qs(urlsplit(url).query)
        page, descending = int(query['page'][0]), query['order'][0] == 'desc'
        if page in self.fail_pages:
            raise self.fail_pages[page]
        ordered = sorted(self.posts, key=lambda p: p['id'], reverse=descending)
        if 'before' in query:   # WordPress: only posts dated before this ISO 8601 moment
            ordered = [p for p in ordered if p['date'] < query['before'][0]]
        chunk = ordered[(page - 1) * PER_PAGE:page * PER_PAGE]
        if not chunk:   # WordPress answers 400 rest_post_invalid_page_number past the last page
            raise HTTPError(url, 400, 'invalid page', {}, None)
        return json.dumps(chunk)

    def pages_requested(self):
        return [int(parse_qs(urlsplit(u).query)['page'][0]) for u in self.requests]

    def befores_requested(self):
        """Per request, the `before` bound it carried (None for the plain newest-first walk)."""
        return [parse_qs(urlsplit(u).query).get('before', [None])[0] for u in self.requests]

    def orders_requested(self):
        return {parse_qs(urlsplit(u).query)['order'][0] for u in self.requests}


@pytest.fixture
def world(monkeypatch, tmp_path):
    site = Site(120)   # 3 pages of 50/50/20
    monkeypatch.setattr(interviews, 'B', str(tmp_path))
    monkeypatch.setattr(interviews, 'DB', str(tmp_path / 'media.db'))
    monkeypatch.setattr(interviews, 'PAUSE', 0)
    monkeypatch.setattr(interviews, 'get', site.get)
    monkeypatch.setattr(interviews, 'parse_turns',
                        lambda text: [('Test Member', 'a spoken sentence with plenty of words in it ok')])
    receipt = tmp_path / 'data' / 'engine' / 'interview_source_receipts' / 'msnow.json'

    def seen(*ids):
        """Pretend an earlier run already collected these post ids."""
        con = interviews.db()
        con.executemany('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)',
                        [('msnow:%d' % i, 'ok', '2026-10-16T00:00:00') for i in ids])
        con.commit(); con.close()

    def prior_receipt(status, **extra):
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({'schema': 'interview-source-receipt-v1', 'status': status, **extra}))

    def forget(*ids):
        """Someone deletes these posts' log rows to force a re-collect."""
        con = interviews.db()
        con.executemany('DELETE FROM iv_fetch_log WHERE key = ?', [('msnow:%d' % i,) for i in ids])
        con.commit(); con.close()

    def query(sql):
        con = sqlite3.connect(tmp_path / 'media.db')
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def logged():
        return dict(query("SELECT key, status FROM iv_fetch_log WHERE key LIKE 'msnow:%'"))

    def stored_links():
        return sorted(r[0] for r in query('SELECT package FROM speeches'))

    return SimpleNamespace(site=site, seen=seen, forget=forget, prior_receipt=prior_receipt, logged=logged,
                           stored_links=stored_links, receipt_path=receipt,
                           receipt=lambda: json.loads(receipt.read_text()))


def test_walk_is_newest_first_and_stops_at_the_first_fully_seen_page(world):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE')
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1]            # was: all 3 pages, every run
    assert world.site.orders_requested() == {'desc'}
    receipt = world.receipt()
    assert (receipt['status'], receipt['archive_pages_checked'], receipt['new_posts_checked']) == ('COMPLETE', 1, 0)
    assert receipt['archive_page_failures'] == 0


def test_new_posts_on_page_one_are_still_captured_then_the_next_seen_page_ends_the_walk(world):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE')
    world.site.publish(121, 122, 123)
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2]         # page 1 held 3 unseen, page 2 held none
    assert world.stored_links() == ['https://www.ms.now/transcripts/post-%d' % i for i in (121, 122, 123)]
    assert {world.logged()['msnow:%d' % i] for i in (121, 122, 123)} == {'ok'}
    assert world.receipt()['new_posts_checked'] == 3
    assert world.receipt()['accepted_turns'] == 3


def test_every_unseen_post_is_processed_even_when_several_pages_are_new(world):
    world.seen(*range(1, 21))                              # only the oldest 20 are known
    world.prior_receipt('COMPLETE')
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2, 3]      # pages 1 and 2 are all-new; page 3 is fully seen
    assert len(world.stored_links()) == 100
    assert set(world.logged().values()) == {'ok'} and len(world.logged()) == 120


@pytest.mark.parametrize('prior', [None, 'PARTIAL'])
def test_no_proof_of_a_gap_free_previous_walk_means_a_full_walk(world, prior):
    """Early stop is only safe after a COMPLETE walk.  A missing or PARTIAL receipt can hide an
    unseen post deeper than the first seen page, so the walk must reach the end of the archive."""
    world.seen(*(i for i in range(1, 121) if i != 5))      # post 5 sits on the LAST page
    if prior:
        world.prior_receipt(prior)
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2, 3, 4]    # page 4 is the empty past-the-end probe
    assert world.stored_links() == ['https://www.ms.now/transcripts/post-5']
    assert world.receipt()['status'] == 'COMPLETE'
    # ...and that COMPLETE walk is what re-enables the cheap path next time
    world.site.requests.clear()
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1]


def test_a_failed_page_leaves_a_partial_receipt_and_the_next_run_resumes_below_the_cursor(world):
    world.seen(*range(1, 21))
    world.prior_receipt('COMPLETE')
    world.site.fail_pages[2] = HTTPError('u', 503, 'unavailable', {}, None)
    assert interviews.run_msnow() is False
    assert world.receipt()['status'] == 'PARTIAL' and world.receipt()['archive_page_failures'] == 1
    assert not any(world.logged().get('msnow:%d' % i) for i in range(51, 71))   # page 2's posts untouched

    world.site.fail_pages.clear()
    world.site.requests.clear()
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 1, 2, 3]    # catch-up page 1, then the backfill below the cursor
    assert world.logged()['msnow:60'] == 'ok'              # the page that failed is now collected


def test_receipt_counts_the_msnow_posts_the_log_holds_and_only_those(world):
    world.seen(*range(1, 11))
    con = interviews.db()   # other sources' rows share iv_fetch_log and must not inflate the count
    con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', ('cnn:abc', 'ok', '2026-10-16T00:00:00'))
    con.commit(); con.close()
    assert interviews.run_msnow() is True
    assert world.receipt()['known_ok_posts'] == 120


def test_deleted_log_rows_force_a_full_walk_that_collects_them_again(world):
    """The COMPLETE receipt proves the last walk left no gap, not that the log still holds what it
    collected.  Deleting msnow: rows to force a re-collect must not be answered by a one-page walk
    and a receipt that keeps saying COMPLETE."""
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=120)
    world.forget(5, 60)                                    # post 5 is on the last page, post 60 on page 2
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2, 3, 4]    # was: [1] and the deleted posts never came back
    assert world.stored_links() == ['https://www.ms.now/transcripts/post-%d' % i for i in (5, 60)]
    assert {world.logged()['msnow:5'], world.logged()['msnow:60']} == {'ok'}
    assert world.receipt()['known_ok_posts'] == 120
    world.site.requests.clear()
    assert interviews.run_msnow() is True                  # the repaired log is trusted again
    assert world.site.pages_requested() == [1]


@pytest.mark.parametrize('recorded', [120, 100])
def test_a_log_holding_at_least_what_the_receipt_recorded_keeps_the_cheap_path(world, recorded):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=recorded)
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1]
    assert world.receipt()['known_ok_posts'] == 120


def test_a_receipt_without_the_count_keeps_the_early_stop(world):
    """The live msnow.json predates known_ok_posts: it is COMPLETE and the log holds every post, so
    refusing the early stop would cost a 215-page walk on the first run after the deploy."""
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE')
    assert 'known_ok_posts' not in json.loads(world.receipt_path.read_text())
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1]
    assert world.receipt()['known_ok_posts'] == 120        # the next receipt carries it


def test_an_unreadable_count_is_not_trusted(world):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts='lots')
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2, 3, 4]


def test_the_full_walk_names_the_missing_rows(world, capsys):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=120)
    world.forget(7)
    assert interviews.run_msnow() is True
    out = capsys.readouterr().out
    assert 'holds 119 msnow posts' in out and 'recorded 120' in out and 'full archive' in out


@pytest.fixture
def clock(monkeypatch, world):
    """Each page request costs 40 simulated seconds; time.sleep is a no-op."""
    now = [0.0]
    monkeypatch.setattr(interviews, 'time', SimpleNamespace(monotonic=lambda: now[0], sleep=lambda s: None))
    world.site.tick = lambda: now.__setitem__(0, now[0] + 40)
    return now


def test_budget_exhaustion_stops_between_pages_with_a_partial_receipt(world, clock, capsys):
    assert interviews.run_msnow(budget=100) is False       # same contract as run_cnn(budget=): False => --strict exit 2
    assert world.site.pages_requested() == [1, 2, 3]        # t=0,40,80 are inside the budget; t=120 is not
    out = capsys.readouterr().out
    assert 'MSNOW BUDGET EXHAUSTED after 100s at page 4' in out
    receipt = world.receipt()
    assert receipt['status'] == 'PARTIAL'
    assert receipt['walk_stop'] == 'budget'
    assert receipt['archive_pages_checked'] == 3
    assert len(world.logged()) == 120                       # work done before the stop is flushed to the log


def test_a_budget_stop_is_not_trusted_by_the_next_run(world, clock):
    world.seen(*range(1, 51))                               # the first page was finished before the stop
    world.prior_receipt('PARTIAL')
    assert interviews.run_msnow(budget=10_000) is True
    assert world.site.pages_requested() == [1, 2, 3, 4]     # page 1 is fully seen but the walk goes on
    assert len(world.logged()) == 120


def test_default_budget_sits_below_every_caller_timeout(world, clock, monkeypatch):
    assert 0 < interviews.MSNOW_DEFAULT_BUDGET_S < 3600     # weekly_refresh, daily_media_refresh, refresh_table
    monkeypatch.setattr(interviews, 'MSNOW_DEFAULT_BUDGET_S', 50)
    assert interviews.run_msnow() is False                  # no argument => the default budget applies
    assert world.receipt()['walk_stop'] == 'budget'


def test_caught_up_runs_are_not_budget_limited(world, clock):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE')
    assert interviews.run_msnow(budget=1) is True
    assert world.receipt()['walk_stop'] == 'caught_up'


def test_every_page_prints_a_progress_line_even_when_it_adds_no_turns(world, capsys):
    world.seen(*range(1, 121))
    assert interviews.run_msnow() is True                   # no prior receipt: full walk, 3 pages
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith('msnow page ')]
    assert len(lines) == 3, lines
    assert lines[0].startswith('msnow page 1:') and '50 posts' in lines[0] and '0 new' in lines[0]


def test_cli_passes_the_budget_flag_to_the_leg_in_both_strict_and_lenient_modes():
    tree = ast.parse((FEC / 'members' / 'fetch_interviews.py').read_text(encoding='utf-8-sig'))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == 'run_msnow']
    assert len(calls) == 2
    for call in calls:
        assert [k.arg for k in call.keywords] == ['budget']
        assert ast.unparse(call.keywords[0].value) == '_budget'


def day(i):
    """The date the scripted site gives post i."""
    return Site.post(i)['date'][:10]


@pytest.fixture
def big(world):
    world.site.publish(*range(121, 401))   # 400 posts = 8 pages of 50
    return world


def test_a_budget_stop_records_where_the_walk_got_to(big, clock):
    assert interviews.run_msnow(budget=100) is False       # pages 1-3 = posts 400..251
    assert big.receipt()['resume_before'] == day(251)       # the oldest post reached, as a date


def test_the_next_run_catches_up_then_resumes_the_backfill_from_the_cursor(big, clock):
    interviews.run_msnow(budget=100)
    big.site.publish(401, 402)
    big.site.requests.clear()
    assert interviews.run_msnow(budget=10_000) is True
    befores = big.site.befores_requested()
    plain = [i for i, b in enumerate(befores) if b is None]
    assert plain == [0, 1]                                  # catch-up: page 1 holds 401/402, page 2 is seen
    assert big.site.pages_requested()[:2] == [1, 2]
    assert set(befores[2:]) == {day(251) + 'T23:59:59'}     # backfill resumes under the cursor, from page 1
    assert big.site.pages_requested()[2:] == [1, 2, 3, 4, 5, 6, 7]   # posts 251..1 = 6 pages + the empty probe
    assert len(big.logged()) == 402 and set(big.logged().values()) == {'ok'}
    receipt = big.receipt()
    assert receipt['status'] == 'COMPLETE' and receipt.get('resume_before') is None


def test_repeated_budget_stops_converge_to_complete(big, clock):
    cursors = []
    for _ in range(12):
        clock[0] = 0.0
        if interviews.run_msnow(budget=100):
            break
        cursors.append(big.receipt()['resume_before'])
    else:
        pytest.fail('never reached COMPLETE: %s' % cursors)
    assert cursors == sorted(cursors, reverse=True) and len(set(cursors)) == len(cursors)   # every run got further
    assert big.receipt()['status'] == 'COMPLETE'
    assert len(big.logged()) == 400
    big.site.requests.clear()
    assert interviews.run_msnow(budget=100) is True         # COMPLETE re-enables the one-page path
    assert big.site.pages_requested() == [1]


def test_a_failed_page_holds_the_cursor_back_so_the_gap_is_resumed(big):
    big.site.fail_pages[3] = HTTPError('u', 503, 'unavailable', {}, None)
    assert interviews.run_msnow() is False                  # page 3 (posts 300..251) failed, 4-8 walked
    assert big.receipt()['resume_before'] == day(301)       # NOT day(1): the cursor stops at the last clean page
    assert not any(big.logged().get('msnow:%d' % i) for i in range(251, 301))
    big.site.fail_pages.clear()
    big.site.requests.clear()
    assert interviews.run_msnow() is True
    assert all(big.logged().get('msnow:%d' % i) == 'ok' for i in range(251, 301))   # the gap came back
    assert big.site.befores_requested()[1] == day(301) + 'T23:59:59'


@pytest.mark.parametrize('cursor, recorded', [('yesterday', 400), (None, 400), ('2020-03-01', 500)])
def test_a_cursor_the_receipt_cannot_back_means_a_full_walk(big, cursor, recorded):
    """No cursor, a malformed one, or a log that lost rows since the receipt: resuming under the cursor
    would skip posts the log no longer holds, so the walk starts over from the top."""
    big.seen(*range(1, 401))
    big.prior_receipt('PARTIAL', resume_before=cursor, known_ok_posts=recorded)
    assert interviews.run_msnow() is True
    assert big.site.befores_requested() == [None] * 9      # 8 pages + the empty probe, no bound


def digest(*ids):
    """sha256 over the sorted msnow: keys the log holds, newline-joined (what the receipt records)."""
    return hashlib.sha256('\n'.join(sorted('msnow:%d' % i for i in ids)).encode()).hexdigest()


def test_receipt_records_a_digest_of_the_msnow_keys_the_log_holds(world):
    con = interviews.db()   # other sources' rows are not part of the msnow key set
    con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', ('cnn:abc', 'ok', '2026-10-16T00:00:00'))
    con.commit(); con.close()
    assert interviews.run_msnow() is True
    assert world.receipt()['known_ok_post_keys'] == digest(*range(1, 121))


def test_same_count_replacement_defeats_the_early_stop(world):
    """Rows deleted and others added keeps the count at 120, so the count alone trusted the receipt and
    the deleted posts were never collected again."""
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=120, known_ok_post_keys=digest(*range(1, 121)))
    world.forget(5, 60)
    world.seen(5001, 5002)                                 # same count, different key set
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2, 3, 4]
    assert world.logged()['msnow:5'] == 'ok' and world.logged()['msnow:60'] == 'ok'
    assert world.receipt()['known_ok_post_keys'] == digest(*range(1, 121), 5001, 5002)


def test_an_exact_key_set_match_keeps_the_early_stop(world):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=120, known_ok_post_keys=digest(*range(1, 121)))
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1]


def test_a_log_that_only_grew_keeps_the_early_stop(world):
    """A run killed after storing posts but before its receipt leaves a log with MORE keys than the
    receipt describes; that is not a deletion."""
    world.seen(*range(1, 126))
    world.prior_receipt('COMPLETE', known_ok_posts=120, known_ok_post_keys=digest(*range(1, 121)))
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1]


def test_an_unreadable_digest_is_not_trusted(world):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=120, known_ok_post_keys='not-a-digest')
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2, 3, 4]


def test_a_count_only_receipt_from_before_the_digest_stays_trusted(world):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=120)
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1]
    assert world.receipt()['known_ok_post_keys'] == digest(*range(1, 121))   # the next receipt carries it


def test_a_partial_receipt_with_a_replaced_key_set_is_not_resumed(big):
    big.seen(*range(1, 401))
    big.prior_receipt('PARTIAL', resume_before=day(301), known_ok_posts=400, known_ok_post_keys=digest(*range(2, 402)))
    assert interviews.run_msnow() is True
    assert big.site.befores_requested() == [None] * 9


def ids_field(*ids):
    """The receipt's known_ok_post_ids: the sorted msnow: key suffixes, comma-joined on one line."""
    return ','.join(k[len('msnow:'):] for k in sorted('msnow:%d' % i for i in ids))


def test_receipt_records_the_msnow_ids_the_log_holds(world):
    assert interviews.run_msnow() is True
    assert world.receipt()['known_ok_post_ids'] == ids_field(*range(1, 121))


def test_a_delete_and_add_that_nets_positive_defeats_the_early_stop(world):
    """The digest alone had to accept any log with more keys than recorded.  With the ids the superset
    is checked, so two deleted posts hidden behind four added keys still force the full walk."""
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=120, known_ok_post_keys=digest(*range(1, 121)),
                        known_ok_post_ids=ids_field(*range(1, 121)))
    world.forget(5, 60)
    world.seen(5001, 5002, 5003, 5004)                     # 122 keys > 120 recorded
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2, 3, 4]
    assert world.logged()['msnow:5'] == 'ok' and world.logged()['msnow:60'] == 'ok'


def test_a_log_that_is_a_superset_of_the_recorded_ids_keeps_the_early_stop(world):
    world.seen(*range(1, 126))
    world.prior_receipt('COMPLETE', known_ok_posts=120, known_ok_post_keys=digest(*range(1, 121)),
                        known_ok_post_ids=ids_field(*range(1, 121)))
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1]


def test_unreadable_ids_are_not_trusted(world):
    world.seen(*range(1, 121))
    world.prior_receipt('COMPLETE', known_ok_posts=120, known_ok_post_ids=['1', '2'])
    assert interviews.run_msnow() is True
    assert world.site.pages_requested() == [1, 2, 3, 4]


def test_a_partial_receipt_whose_ids_are_not_all_in_the_log_is_not_resumed(big):
    big.seen(*range(2, 401))                               # post 1 is gone from the log
    big.prior_receipt('PARTIAL', resume_before=day(301), known_ok_posts=400,
                      known_ok_post_ids=ids_field(*range(1, 401)))
    assert interviews.run_msnow() is True
    assert big.site.befores_requested() == [None] * 9
