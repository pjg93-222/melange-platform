"""Declared fake secrets and mocked redirect transport only; never access real credentials."""
import ast
import hashlib
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qsl
import urllib.request
import re
import json
from unittest.mock import patch
import pytest

FEC=Path(__file__).resolve().parents[2]

def lift(label,names,extra=None):
    tree=ast.parse((FEC/label).read_text(encoding="utf-8-sig"))
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    # New optional helper is omitted on the baseline; actual old sha/picker still reproduce the read.
    ns=dict(os=os,Path=Path,hashlib=hashlib,time=time,tempfile=tempfile,urllib=urllib,
        urlsplit=urlsplit,parse_qsl=parse_qsl,re=re)
    ns.update(extra or {})
    tree=ast.Module([ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0),*nodes],type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree),label,"exec"),ns)
    return ns

def producer(tmp_path):
    return lift('audit/build_system_manifest.py',['_secret_path','_forbid_secret','SecretAccessError','sha256_file','_pick_file_sentinels'],{
        'ROOTS':[{'id':'fixture','path':str(tmp_path)}],
        'REGISTRY':[{'root_id':'fixture','rel':'secrets/fake.key','class':'SECRET'}],
        '_volatile':lambda *a:False,'_excluded':lambda *a:False,
        '_by_hashable':lambda *a:[], 'SENTINEL_COUNT':3,'SENTINEL_HASH_MAX_BYTES':1024})

def test_overlapping_store_cannot_hash_declared_secret(tmp_path):
    ns=producer(tmp_path);fake=tmp_path/'secrets/fake.key'
    def forbidden(*a,**k):raise AssertionError('hash reader reached fake secret')
    with patch('builtins.open',forbidden):
        with pytest.raises(ns['SecretAccessError']):ns['sha256_file'](str(fake),{'class':'SOURCE','id':'broad','path':str(tmp_path)})

def test_public_sibling_remains_hashable(tmp_path):
    ns=producer(tmp_path);p=tmp_path/'public.txt';p.write_bytes(b'public fixture')
    assert ns['sha256_file'](str(p),{'class':'SOURCE','id':'broad','path':str(tmp_path)})==hashlib.sha256(p.read_bytes()).hexdigest()

def test_sentinel_selection_excludes_secret_before_fallback(tmp_path):
    ns=producer(tmp_path);calls=[]
    ns['sha256_file']=lambda p,s:calls.append(p) or 'digest'
    store={'class':'SOURCE','id':'broad','path':str(tmp_path),'measured':{'kind':'dir'},'sentinel_excludes':['public.txt']}
    sent=ns['_pick_file_sentinels'](store,[('secrets/fake.key',7,2),('public.txt',6,1)])
    assert {r['rel'] for r in sent}=={'public.txt'}
    assert all('fake.key' not in p for p in calls)

@pytest.mark.parametrize('destination',['http://allowed.example/file','https://other.example/file','https://allowed.example:444/file','https://user:pw@allowed.example/file','https://allowed.example/file?api_key=fake'])
def test_download_refuses_redirect_before_target_activation(tmp_path,destination):
    ns=lift('tools/b0_bicam_arrival_audit.py',['atomic_download','validate_source_url','_secret_query_key','UsageError','IntegrityError','_AllowedHostRedirect'],
        {'SECRET_QUERY_FRAGMENTS':('api_key',),'inspect_component':lambda *a:{'sha256':'x'}})
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return b''
    def direct(*a,**k):return Response()
    def build(handler):
        def opened(url,**kw):
            handler.redirect_request(urllib.request.Request(url),None,302,'redirect',{},destination)
            return Response()
        return SimpleNamespace(open=opened)
    target=tmp_path/'result.json'
    with patch.object(urllib.request,'urlopen',direct),patch.object(urllib.request,'build_opener',build):
        with pytest.raises(ns['IntegrityError']):
            ns['atomic_download']('https://allowed.example/file',target,'','json',{})
    assert not target.exists()
    assert list(tmp_path.iterdir())==[]

def test_same_origin_redirect_remains_supported(tmp_path):
    ns=lift('tools/b0_bicam_arrival_audit.py',['atomic_download','validate_source_url','_secret_query_key','UsageError','IntegrityError','_AllowedHostRedirect'],
        {'SECRET_QUERY_FRAGMENTS':('api_key',),'inspect_component':lambda *a:{'sha256':'x'}})
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return b''
    def build(handler):
        def opened(url,**kw):
            redirected=handler.redirect_request(urllib.request.Request(url),None,302,'redirect',{},'https://allowed.example:443/new')
            assert redirected.full_url=='https://allowed.example:443/new'
            return Response()
        return SimpleNamespace(open=opened)
    with patch.object(urllib.request,'urlopen',lambda *a,**k:Response()),patch.object(urllib.request,'build_opener',build):
        assert ns['atomic_download']('https://allowed.example/file',tmp_path/'result.json','','json',{})=={'sha256':'x'}


@pytest.mark.parametrize('destination,allowed',[
    ('https://cdn.example/file',True),('https://cdn.example:444/file',True),
    ('https://unapproved.example/file',False),('http://cdn.example/file',False),
    ('https://user:pw@cdn.example/file',False),('https://cdn.example/file?api_key=fake',False)])
def test_explicit_approved_hosts_apply_identically_to_redirects(destination,allowed):
    ns=lift('tools/b0_bicam_arrival_audit.py',['_AllowedHostRedirect','validate_source_url','_secret_query_key','UsageError'],
        {'SECRET_QUERY_FRAGMENTS':('api_key',)})
    handler=ns['_AllowedHostRedirect']('https://origin.example/data',{'origin.example','cdn.example'})
    args=(urllib.request.Request('https://origin.example/data'),None,302,'redirect',{},destination)
    if allowed:assert handler.redirect_request(*args).full_url==destination
    else:
        with pytest.raises(ns['UsageError']):handler.redirect_request(*args)


def test_fetch_passes_explicit_hosts_to_each_download(tmp_path):
    calls=[]
    ns=lift('tools/b0_bicam_arrival_audit.py',['fetch_snapshot','UsageError'],{
        'SOURCE_COMPONENTS':{'fixture':['one','two']},
        'atomic_download':lambda *a,**k:calls.append(k['allowed_hosts']),
        'build_arrival_manifest':lambda *a:{'status':'fixture'}})
    manifest={'schema':'fixture','components':{n:{'url':'https://origin.example/data',
        'filename':n+'.csv','format':'csv','columns':{}} for n in ('one','two')}}
    ns['fetch_snapshot'](tmp_path/'snapshot',manifest,'hash',allowed_hosts=iter(['origin.example','cdn.example']))
    assert calls==[frozenset(['origin.example','cdn.example'])]*2


def test_cli_passes_existing_allow_host_option_to_fetch(tmp_path):
    calls=[]
    args=SimpleNamespace(fetch=True,audit=False,source_manifest=tmp_path/'source.json',
        billstatus_parsed=tmp_path/'parsed',out_dir=tmp_path/'out',snapshot_dir=tmp_path/'snapshot',
        allow_host=['origin.example','cdn.example'],replace_audit=False)
    ns=lift('tools/b0_bicam_arrival_audit.py',['main','UsageError','IntegrityError'],{
        'build_parser':lambda:SimpleNamespace(parse_args=lambda argv:args),
        'validate_source_manifest':lambda *a,**k:({'schema':'fixture'},'hash'),
        'validate_output_gate':lambda *a:None,
        'fetch_snapshot':lambda *a,**k:calls.append(k['allowed_hosts']) or {},
        'run_audits':lambda *a:{'status':'PASS'},'write_audit_outputs':lambda *a:None})
    with patch.dict(os.environ,{'BICAM_FETCH_OK':'1'}):assert ns['main']([])==0
    assert calls==[args.allow_host]
