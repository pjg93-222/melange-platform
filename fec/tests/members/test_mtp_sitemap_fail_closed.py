import datetime
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from members import fetch_interviews as interviews


JANUARY = 'https://www.nbcnews.com/sitemap/nbcnews/sitemap-2014-01-article.xml'
FEBRUARY = 'https://www.nbcnews.com/sitemap/nbcnews/sitemap-2014-02-article.xml'
TRANSCRIPT = 'https://www.nbcnews.com/meet-the-press/transcripts/test-episode'
HUB = '<a href="%s">Transcript</a>' % TRANSCRIPT
SITEMAP = '<urlset><url><loc>%s</loc></url></urlset>' % TRANSCRIPT


@pytest.fixture
def mtp(monkeypatch, tmp_path):
    """Run the real source walk against a private DB and two scripted months."""
    class FrozenDate(datetime.date):
        @classmethod
        def today(cls):
            return cls(2014, 2, 15)

    monkeypatch.setattr(interviews, 'datetime', SimpleNamespace(
        date=FrozenDate, datetime=datetime.datetime))
    monkeypatch.setattr(interviews, 'DB', str(tmp_path / 'media.db'))
    monkeypatch.setattr(interviews, 'PAUSE', 0)
    monkeypatch.setattr(interviews, 'parse_turns', lambda page: [])
    outcomes = {JANUARY: SITEMAP, FEBRUARY: SITEMAP}
    requested = []

    def fetch(url, tries=3):
        requested.append(url)
        value = outcomes.get(url, '<html>2014-02-09</html>' if url == TRANSCRIPT else HUB)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(interviews, 'get', fetch)
    return outcomes, requested, tmp_path / 'media.db'


@pytest.mark.parametrize('status', [404, 410])
def test_missing_month_is_expected_but_is_rechecked_next_run(mtp, status):
    outcomes, requested, database = mtp
    outcomes[JANUARY] = HTTPError(JANUARY, status, 'missing', {}, None)
    assert interviews.run_mtp() is True

    # Expected absence never becomes a permanently skipped monthly checkpoint.
    requested.clear()
    outcomes[JANUARY] = SITEMAP
    assert interviews.run_mtp() is True
    assert requested.count(JANUARY) == requested.count(FEBRUARY) == 1
    assert TRANSCRIPT not in requested  # accepted articles still resume normally
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT key, status FROM iv_fetch_log').fetchall() == [
            ('mtp:test-episode', 'ok')]


@pytest.mark.parametrize('failure', [
    HTTPError(JANUARY, 503, 'unavailable', {}, None),
    HTTPError(JANUARY, 429, 'rate limited', {}, None),
    HTTPError(JANUARY, 403, 'forbidden', {}, None),
    URLError('connection reset'),
    TimeoutError('read timed out'),
    ValueError('invalid response'),
])
def test_exhausted_sitemap_error_fails_despite_valid_hub_and_can_retry(mtp, capsys, failure):
    outcomes, requested, _database = mtp
    outcomes[JANUARY] = failure
    assert interviews.run_mtp() is False
    assert 'MTP sitemap 2014-01 FAIL' in capsys.readouterr().out
    assert FEBRUARY in requested and TRANSCRIPT in requested

    # The next pass must fetch the failed month even when its article is already stored.
    requested.clear()
    outcomes[JANUARY] = SITEMAP
    assert interviews.run_mtp() is True
    assert JANUARY in requested and FEBRUARY in requested


def test_error_message_containing_404_is_not_official_absence(mtp):
    outcomes, _requested, _database = mtp
    outcomes[JANUARY] = RuntimeError('upstream failure mentioned HTTP 404')
    assert interviews.run_mtp() is False


@pytest.mark.parametrize('body', [
    '<html><body>Temporarily unavailable</body></html>',
    '<urlset><url><loc>https://www.nbcnews.com/broken</loc></url>',
    '<sitemapindex><sitemap><loc>https://www.nbcnews.com/other.xml</loc></sitemap></sitemapindex>',
    '<urlset xmlns="https://unexpected.example/schema"/>',
    '<urlset><error>temporarily unavailable</error></urlset>',
    '<urlset><url/></urlset>',
    '<urlset><url><loc>not-a-url</loc></url></urlset>',
    '<urlset>Temporarily unavailable</urlset>',
])
def test_http_200_invalid_sitemap_body_fails_despite_valid_hub(mtp, capsys, body):
    outcomes, requested, _database = mtp
    outcomes[JANUARY] = body
    assert interviews.run_mtp() is False
    assert 'MTP sitemap 2014-01 FAIL' in capsys.readouterr().out
    assert FEBRUARY in requested and TRANSCRIPT in requested
    requested.clear()
    outcomes[JANUARY] = SITEMAP
    assert interviews.run_mtp() is True
    assert JANUARY in requested


@pytest.mark.parametrize('body', [
    '<urlset/>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>',
    '<s:urlset xmlns:s="http://www.sitemaps.org/schemas/sitemap/0.9"/>',
])
def test_valid_empty_monthly_urlset_is_expected_and_rechecked(mtp, body):
    outcomes, requested, _database = mtp
    outcomes[JANUARY] = body
    assert interviews.run_mtp() is True
    requested.clear()
    assert interviews.run_mtp() is True
    assert JANUARY in requested


def test_namespaced_sitemap_discovers_links_with_optional_metadata(mtp):
    outcomes, _requested, _database = mtp
    outcomes[JANUARY] = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
                        '<url><loc>%s</loc><lastmod>2014-02-09</lastmod>'
                        '<news:news><news:title>Fixture</news:title></news:news></url></urlset>') % TRANSCRIPT
    assert interviews._mtp_sitemap_links(outcomes[JANUARY]) == {TRANSCRIPT}
    assert interviews.run_mtp() is True
