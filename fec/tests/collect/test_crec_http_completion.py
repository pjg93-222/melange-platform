"""CREC source absence requires typed HTTP 404; recent absences remain retryable."""
import ast
import datetime
import io
import json
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import pytest


@pytest.fixture
def collector(tmp_path):
    # Load only the collector functions; tests never read the live roster, keys,
    # source database, or other import-time production inputs.
    path=Path(__file__).resolve().parents[2]/'collect/fetch_congressional_record.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    names={'db','get','_redact_url','_strip_api_key','_http_status_code','_completed_crec_keys','_crec_package_absent','run_crec'}
    selected=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
    scope={'sqlite3':sqlite3,'urllib':urllib,'datetime':datetime,'io':io,'zipfile':zipfile,'json':json,
           'os':__import__('os'),'time':time,'DB':str(tmp_path/'social_media.db'),'UA':{},'KEY':'private-fixture-key'}
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),scope)
    def parse(pages,chamber,date,source,package,cursor):
        assert pages and chamber(pages[0][0])=='house'
        cursor.execute('INSERT INTO speeches VALUES(?,?,?,?,?,?,?)',
                       ('Fixture Member',date,source,'house',package,4,'Fixture official speech text'))
        return 1,{'Fixture Member':1}
    scope['parse_pages']=parse
    return scope


def fetch_status(scope,date):
    with sqlite3.connect(scope['DB']) as con:
        return con.execute('SELECT status FROM cr_fetch_log WHERE key=?',('crec:CREC-'+date,)).fetchone()[0]


def fail_http(code):
    def fail(request,**kwargs):
        raise urllib.error.HTTPError(request.full_url,code,'fixture HTTP error',{},None)
    return fail


def test_redacted_request_preserves_typed_404_cause(collector,monkeypatch):
    monkeypatch.setattr(urllib.request,'urlopen',fail_http(404))
    with pytest.raises(RuntimeError) as failure:
        collector['get']('https://www.govinfo.gov/example?api_key=private-test-key',tries=1)
    assert 'private-test-key' not in str(failure.value)
    assert 'REDACTED' in str(failure.value)
    assert collector['_http_status_code'](failure.value)==404


@pytest.mark.parametrize('status',[404,403,429,500,503])
def test_only_actual_http_404_can_certify_non_session(collector,monkeypatch,status):
    monkeypatch.setattr(urllib.request,'urlopen',fail_http(status))
    date=(datetime.date.today()-datetime.timedelta(days=2)).isoformat()
    collector['run_crec'](date,date)
    recorded=fetch_status(collector,date)
    assert (recorded=='nosession') is (status==404)
    if status!=404:assert recorded.startswith('err:')


@pytest.mark.parametrize('body',[b'404 not found',b'<html>Verification required</html>'])
def test_non_zip_http_success_is_never_a_non_session(collector,body):
    collector['get']=lambda *args,**kwargs:body
    date=(datetime.date.today()-datetime.timedelta(days=2)).isoformat()
    collector['run_crec'](date,date)
    assert fetch_status(collector,date).startswith('err:')


def test_error_message_containing_404_is_not_source_attestation(collector):
    def fail(*args,**kwargs):raise RuntimeError('HTTP 404-looking untyped error')
    collector['get']=fail
    date=(datetime.date.today()-datetime.timedelta(days=2)).isoformat()
    collector['run_crec'](date,date)
    assert fetch_status(collector,date).startswith('err:')


def test_recent_404_is_retried_and_later_publication_is_ingested_once(collector,monkeypatch):
    monkeypatch.setattr(urllib.request,'urlopen',fail_http(404))
    date=(datetime.date.today()-datetime.timedelta(days=2)).isoformat()
    collector['run_crec'](date,date)
    assert fetch_status(collector,date)=='nosession'
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w') as archive:
        archive.writestr('CREC-'+date+'-PgH1.htm','Fixture official floor page')
    calls=[]
    def fetch(*args,**kwargs):calls.append(args[0]);return payload.getvalue()
    collector['get']=fetch
    collector['run_crec'](date,date)
    collector['run_crec'](date,date)
    assert fetch_status(collector,date)=='ok' and len(calls)==1
    with sqlite3.connect(collector['DB']) as con:
        assert con.execute('SELECT COUNT(*) FROM speeches').fetchone()[0]==1


def test_old_attested_non_session_is_retained_but_recent_one_is_not_final(collector):
    today=datetime.date(2026,9,13)
    rows=[('crec:CREC-2026-09-12','nosession'),('crec:CREC-2026-08-01','nosession'),
          ('crec:CREC-2026-09-11','ok'),('crec:CREC-2026-09-10','err:timeout')]
    assert collector['_completed_crec_keys'](rows,today)=={
        'crec:CREC-2026-08-01','crec:CREC-2026-09-11'}


@pytest.mark.parametrize('api_status',[404,200,403,429,503])
def test_soft_404_requires_fresh_typed_api_absence(collector,api_status,capsys):
    date=(datetime.date.today()-datetime.timedelta(days=2)).isoformat()
    package='CREC-'+date
    calls=[]
    def fetch(url,**kwargs):
        calls.append((url,kwargs))
        if url.endswith('.zip'):return b'<!DOCTYPE html><title>GovInfo error</title>'
        if api_status==200:return json.dumps({'packageId':package})
        try:
            raise urllib.error.HTTPError(url,api_status,'fixture',{},None)
        except urllib.error.HTTPError as error:
            raise RuntimeError('request failed: '+url) from error
    collector['get']=fetch
    collector['run_crec'](date,date)
    assert (fetch_status(collector,date)=='nosession') is (api_status==404)
    assert calls[1][0]=='https://api.govinfo.gov/packages/'+package+'/summary'
    assert calls[1][1]['api_key']=='private-fixture-key'
    output=capsys.readouterr().out
    assert 'private-fixture-key' not in output
    if api_status==404:assert 'official package-summary absence' in output


def test_soft_404_api_absence_is_rechecked_when_package_later_appears(collector):
    date=(datetime.date.today()-datetime.timedelta(days=2)).isoformat()
    package='CREC-'+date
    calls=[]
    def absent(url,**kwargs):
        calls.append(url)
        if url.endswith('.zip'):return b'<html>soft not-found</html>'
        try:raise urllib.error.HTTPError(url,404,'fixture',{},None)
        except urllib.error.HTTPError as error:raise RuntimeError('redacted request failed') from error
    collector['get']=absent
    collector['run_crec'](date,date)
    assert fetch_status(collector,date)=='nosession'
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w') as archive:
        archive.writestr(package+'-PgH1.htm','Fixture official floor page')
    collector['get']=lambda *args,**kwargs:payload.getvalue()
    collector['run_crec'](date,date)
    assert fetch_status(collector,date)=='ok' and len(calls)==2
