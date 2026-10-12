from __future__ import annotations

import json
import os
from pathlib import Path
import pickle
import re
import runpy
import sqlite3
import subprocess
import sys
import types

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from collect import tweet_collector as T


class FixtureClassifier:
    def predict(self, texts):
        return ['support'] * len(texts)

    def decision_function(self, texts):
        import numpy as np
        return np.array([[0.1, 0.2, 0.8] for _ in texts])


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / 'social_media.db'
    with sqlite3.connect(path) as connection:
        connection.executescript(T.SCHEMA)
        connection.executemany('INSERT INTO members(bioguide,name,bluesky) VALUES(?,?,?)', [
            ('A1', 'Verified Member', 'verified.house.gov'),
            ('B2', 'Unverified Member', 'lookalike.bsky.social [UNVERIFIED — confirm]'),
            ('C3', 'Absent Member', 'none-found'),
        ])
    monkeypatch.setattr(T, 'DB', str(path))
    return path


def post(text='Credit union policy', *, did='did:plc:verified', date='2026-09-12T12:00:00Z', suffix='post'):
    return {'post': {'uri': 'at://' + did + '/app.bsky.feed.post/' + suffix,
                     'author': {'did': did}, 'record': {'text': text, 'createdAt': date}}}


def test_strict_fetch_only_visits_verified_handles_and_ignores_old_skip_flag(database, monkeypatch):
    monkeypatch.setenv('BSKY_SKIP_FETCHED', '1')
    with sqlite3.connect(database) as connection:
        connection.execute('INSERT INTO fetch_log VALUES(?,?,?,?,?,?)', ('bluesky', 'A1', '', 'yesterday', 1, 'ok'))
    calls = []
    def get(url):
        calls.append(url)
        if 'getProfile' in url:
            return {'handle': 'verified.house.gov', 'did': 'did:plc:verified'}
        return {'feed': [post()]}
    monkeypatch.setattr(T, 'get', get)
    receipt = T.fetch_bluesky(verified_only=True, strict=True, since='2026-08-30')
    assert receipt['status'] == 'COMPLETE' and receipt['selected_handles'] == 1
    assert receipt['unverified_handles_excluded'] == 1
    assert all('lookalike' not in url for url in calls)
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT DISTINCT bioguide FROM posts').fetchall() == [('A1',)]


@pytest.mark.parametrize('failure', ['network', 'author', 'pagination'])
def test_strict_source_errors_are_nonzero_and_receipted(database, tmp_path, monkeypatch, failure):
    def get(url):
        if 'getProfile' in url:
            return {'handle': 'verified.house.gov', 'did': 'did:plc:verified'}
        if failure == 'network':
            raise RuntimeError('network unavailable')
        if failure == 'author':
            return {'feed': [post(did='did:plc:someone-else')]}
        return {'feed': [post()], 'cursor': 'more'}
    monkeypatch.setattr(T, 'get', get)
    receipt = tmp_path / 'receipt.json'
    assert T.main(['fetch', '--platform', 'bluesky', '--verified-only', '--strict',
                   '--since', '2026-08-30', '--pages', '1', '--receipt', str(receipt)]) == 1
    result = json.loads(receipt.read_text())
    assert result['status'] == 'FAIL' and result['error_count'] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM fetch_log WHERE bioguide='A1'").fetchone()[0].startswith('ERR:')


@pytest.mark.parametrize('handle,did,suffix,embed_type', [
    ('welch.senate.gov','did:plc:z2wvqdvi7hukokkpe2gnnqru','3mvduqe2nsq2o','app.bsky.embed.images'),
    ('schumer.senate.gov','did:plc:vkrwase7dxifkgxgpdcozict','3mv4a2442632p','app.bsky.embed.external'),
    ('heinrich.senate.gov','did:plc:67vugavgzxuc64jxpv4cqwbi','3mve2ewhl6u2v','app.bsky.embed.images'),
])
def test_observed_member_embed_null_record_preserves_post_and_media(
        database, monkeypatch, handle, did, suffix, embed_type):
    # Public AppView payload shape observed on 2026-09-13 for these exact posts:
    # unused fields in an image/external embed are present as explicit nulls.
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE members SET bluesky=? WHERE bioguide='A1'", (handle,))
    item=post(did=did,suffix=suffix)
    embed={'$type':embed_type,'captions':None,'external':None,'images':None,'record':None,'video':None}
    if embed_type.endswith('.images'):
        embed['images']=[{'alt':'Fixture image','image':{'$type':'blob','ref':{'$link':'fixture'}}}]
    else:
        embed['external']={'uri':'https://www.senate.gov/','title':'Fixture link','description':''}
    item['post']['record'].update(reply=None,embed=embed)
    monkeypatch.setattr(T,'get',lambda url: {'handle':handle,'did':did} if 'getProfile' in url else {'feed':[item]})
    receipt=T.fetch_bluesky(verified_only=True,strict=True,since='2026-08-30')
    assert receipt['status']=='COMPLETE' and receipt['error_count']==0
    with sqlite3.connect(database) as connection:
        row=connection.execute('SELECT post_id,text,reply_to,quote_of,media_json FROM posts').fetchone()
    assert row[:4]==(item['post']['uri'],'Credit union policy',None,None)
    assert json.loads(row[4])==embed


@pytest.mark.parametrize('fields', [
    {'reply':[]}, {'reply':{'parent':[]}}, {'reply':{'parent':None}},
    {'embed':[]}, {'embed':{'record':[]}}, {'embed':{'record':'not an object'}},
    {'embed':{'$type':'app.bsky.embed.record','record':None}},
    {'embed':{'$type':'app.bsky.embed.record','record':{'uri':'not-an-at-uri'}}},
    {'embed':{'$type':'app.bsky.embed.recordWithMedia','record':{'record':None}}},
])
def test_malformed_optional_or_required_reference_still_fails_closed(database, monkeypatch, fields):
    item=post()
    item['post']['record'].update(fields)
    monkeypatch.setattr(T,'get',lambda url: {'handle':'verified.house.gov','did':'did:plc:verified'}
                        if 'getProfile' in url else {'feed':[item]})
    receipt=T.fetch_bluesky(verified_only=True,strict=True,since='2026-08-30')
    assert receipt['status']=='FAIL' and receipt['error_count']==1
    assert receipt['results'][0]['error'].startswith('ValueError: Bluesky ')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM posts').fetchone()[0]==0


@pytest.mark.parametrize('with_media',[False,True])
def test_valid_reply_and_quote_references_are_preserved(with_media):
    parent='at://did:plc:parent/app.bsky.feed.post/parent'
    quoted='at://did:plc:quoted/app.bsky.feed.post/quoted'
    reference={'uri':quoted,'cid':'fixture'}
    embed={'$type':'app.bsky.embed.record','record':reference}
    if with_media:
        embed={'$type':'app.bsky.embed.recordWithMedia','record':embed,'media':{'$type':'app.bsky.embed.images','images':[]}}
    record={'reply':{'parent':{'uri':parent,'cid':'fixture'}},'embed':embed}
    assert T.bluesky_post_references(record,strict=True)==(parent,quoted)


def test_repost_is_not_stored_as_members_own_words(database, monkeypatch):
    item = post(did='did:plc:other')
    item['reason'] = {'$type': 'app.bsky.feed.defs#reasonRepost', 'indexedAt': '2026-09-12T13:00:00Z',
                      'by': {'did': 'did:plc:verified'}}
    def get(url):
        return {'handle': 'verified.house.gov', 'did': 'did:plc:verified'} if 'getProfile' in url else {'feed': [item]}
    monkeypatch.setattr(T, 'get', get)
    result = T.fetch_bluesky(verified_only=True, strict=True, since='2026-08-30')
    assert result['status'] == 'COMPLETE'
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT repost_of FROM posts').fetchone()[0] == item['post']['uri']


def test_two_members_reposting_same_original_keep_distinct_events_and_original(database, monkeypatch):
    with sqlite3.connect(database) as connection:
        connection.execute('INSERT INTO members(bioguide,name,bluesky) VALUES(?,?,?)',
                           ('D4', 'Second Verified Member', 'second.house.gov'))
        original = post(did='did:plc:original')['post']['uri']
        connection.execute('INSERT INTO posts(platform,post_id,bioguide,created_at,text) VALUES(?,?,?,?,?)',
                           ('bluesky', original, 'O5', '2026-09-12T12:00:00Z', 'Original own words'))
    def get(url):
        did = 'did:plc:second' if 'second.house.gov' in url else 'did:plc:verified'
        if 'getProfile' in url:
            return {'handle': 'second.house.gov' if did.endswith(':second') else 'verified.house.gov', 'did': did}
        item = post(did='did:plc:original')
        item['reason'] = {'$type': 'app.bsky.feed.defs#reasonRepost', 'indexedAt': '2026-09-12T13:00:00Z',
                          'by': {'did': did}}
        return {'feed': [item]}
    monkeypatch.setattr(T, 'get', get)
    for _ in range(2):
        assert T.fetch_bluesky(verified_only=True, strict=True, since='2026-08-30')['status'] == 'COMPLETE'
    with sqlite3.connect(database) as connection:
        rows = connection.execute('SELECT post_id,bioguide,repost_of,text FROM posts ORDER BY bioguide').fetchall()
    assert len(rows) == 3
    assert {row[1] for row in rows} == {'A1', 'D4', 'O5'}
    assert rows[-1] == (original, 'O5', None, 'Original own words')
    assert rows[0][0] != rows[1][0] and rows[0][2] == rows[1][2] == original
    assert T.bluesky_post_url(rows[0][2]) == 'https://bsky.app/profile/did:plc:original/post/post'


def test_original_post_links_use_official_at_uri_authority():
    # Example record from https://atproto.com/specs/at-uri-scheme.
    uri = 'at://did:plc:vwzwgnygau7ed7b7wt5ux7y2/app.bsky.feed.post/3k5nobkf2w72g'
    assert T.bluesky_post_url(uri) == 'https://bsky.app/profile/did:plc:vwzwgnygau7ed7b7wt5ux7y2/post/3k5nobkf2w72g'
    assert T.bluesky_post_url('at://member.house.gov/app.bsky.feed.post/key') == 'https://bsky.app/profile/member.house.gov/post/key'
    assert T.bluesky_post_url('event:bluesky-repost:invalid') == ''
    assert T.bluesky_post_url('at://member.house.gov/app.bsky.feed.post/../bad') == ''


def test_pinned_old_post_does_not_truncate_current_window(database, monkeypatch):
    calls = []
    def get(url):
        if 'getProfile' in url:
            return {'handle': 'verified.house.gov', 'did': 'did:plc:verified'}
        calls.append(url)
        if len(calls) == 1:
            pinned = post(date='2025-01-01T00:00:00Z', suffix='pinned')
            pinned['reason'] = {'$type': 'app.bsky.feed.defs#reasonPin'}
            return {'feed': [pinned, post(suffix='first')], 'cursor': 'second-page'}
        return {'feed': [post(suffix='second')]}
    monkeypatch.setattr(T, 'get', get)
    assert T.fetch_bluesky(verified_only=True, strict=True, since='2026-08-30')['status'] == 'COMPLETE'
    assert len(calls) == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM posts').fetchone()[0] == 2


def test_extraction_checks_registry_instead_of_unverified_marker_inside_post(database, tmp_path):
    roster = tmp_path / 'data/engine/congestion.json'
    roster.parent.mkdir(parents=True)
    roster.write_text(json.dumps([{'bioguide': 'A1', 'member': 'Verified Member', 'chamber': 'House'},
                                 {'bioguide': 'B2', 'member': 'Unverified Member', 'chamber': 'House'}]))
    (tmp_path / 'data/stance').mkdir()
    with sqlite3.connect(database) as connection:
        for bio in ('A1', 'B2'):
            connection.execute('INSERT INTO posts(platform,post_id,bioguide,created_at,text) VALUES(?,?,?,?,?)',
                               ('bluesky', bio, bio, '2026-09-12', 'We support credit union field of membership reform.'))
    result = subprocess.run([sys.executable, str(FEC / 'stance/extract_cu_bluesky.py')],
                            env={**os.environ, 'MONEYBALL_ROOT': str(tmp_path)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    output = (tmp_path / 'data/stance/bluesky_cu.csv').read_text()
    assert 'Verified Member' in output
    assert 'Unverified Member' not in output
    assert 'dropped - UNVERIFIED handle        : 1' in result.stdout


def test_social_workbook_keeps_repost_provenance_and_own_words_extractor_drops_it(database, tmp_path, monkeypatch):
    import pandas as pd
    import paths
    monkeypatch.setattr(paths, 'ROOT', str(tmp_path))
    # Avoid process_quorum's unrelated production workbook import. Its licensed
    # source is already represented by the tiny prepared CSV below.
    quorum = types.ModuleType('network.process_quorum')
    quorum.CU_TERMS = re.compile('credit union', re.I)
    monkeypatch.setitem(sys.modules, 'network.process_quorum', quorum)
    stance = tmp_path / 'data/stance'
    stance.mkdir(parents=True)
    (tmp_path / 'data/engine').mkdir(parents=True)
    (tmp_path / 'data/members').mkdir(parents=True)
    (tmp_path / 'data/engine/congestion.json').write_text(json.dumps([
        {'bioguide': 'A1', 'member': 'Verified Member'},
        {'bioguide': 'D4', 'member': 'Second Verified Member'},
        {'bioguide': 'O5', 'member': 'Original Member'},
    ]))
    pd.DataFrame(columns=['Document Type', 'member', 'dt', 'Content', 'Title', 'is_rt',
                          'stance', 'stance_margin', 'Source URL']).to_csv(stance / 'quorum_member_tweets.csv', index=False)
    with (stance / 'stance_clf_composed.pkl').open('wb') as handle:
        pickle.dump(FixtureClassifier(), handle)
    original = 'at://did:plc:original/app.bsky.feed.post/same-post'
    with sqlite3.connect(database) as connection:
        connection.executemany('INSERT INTO members(bioguide,name,bluesky) VALUES(?,?,?)', [
            ('D4', 'Second Verified Member', 'second.house.gov'),
            ('O5', 'Original Member', 'original.house.gov'),
        ])
        for bio in ('A1', 'D4', 'O5'):
            repost = None if bio == 'O5' else original
            event = original if bio == 'O5' else 'event:bluesky-repost:' + bio
            connection.execute('INSERT INTO posts(platform,post_id,bioguide,created_at,text,repost_of) VALUES(?,?,?,?,?,?)',
                               ('bluesky', event, bio, '2026-09-12', 'We support credit union field of membership reform.', repost))
    runpy.run_path(str(FEC / 'network/build_social_master.py'), run_name='__main__')
    corpus = pd.read_excel(tmp_path / 'Social Master.xlsx', sheet_name='Corpus').fillna('')
    assert len(corpus) == 3
    assert set(corpus['url']) == {'https://bsky.app/profile/did:plc:original/post/same-post'}
    assert set(corpus['original_post_uri']) == {original}
    own = corpus[corpus['member'] == 'Original Member'].iloc[0]
    assert own['post_id'] == original and own['is_rt'] == 0 and own['repost_of'] == ''
    assert all(corpus.loc[corpus['member'] != 'Original Member', 'is_rt'] == 1)
    result = subprocess.run([sys.executable, str(FEC / 'stance/extract_cu_bluesky.py')],
                            env={**os.environ, 'MONEYBALL_ROOT': str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    extracted = pd.read_csv(stance / 'bluesky_cu.csv')
    assert extracted['member'].tolist() == ['Original Member']
