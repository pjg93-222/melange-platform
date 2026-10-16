"""Do not turn temporary network failures or empty API results into route bans."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import types
import unittest

import pytest
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from collect import recover_press_refresh as rpr
from collect.recover_press_refresh import unsupported_api

class RecoveryRoutes(unittest.TestCase):
    def test_definitive_unsupported_routes(self):
        self.assertTrue(unsupported_api(410,None))
        self.assertTrue(unsupported_api(404,{'code':'rest_no_route'}))

    def test_transient_or_ambiguous_responses_stay_eligible(self):
        for status,payload in [(200,[]),(404,None),(404,{'code':'rest_post_invalid_id'}),
                               (403,None),(429,None),(500,None),(503,None)]:
            with self.subTest(status=status,payload=payload):
                self.assertFalse(unsupported_api(status,payload))

URL='https://example.house.gov/news/documentsingle.aspx?DocumentID=1'

def run_worker(monkeypatch,tmp_path,get,rows=1,returncode=0,hours='1',r_writes_receipt=False,prefill=None):
    """Run the real main() with a fake clock, fake HTTP session and fake R; nothing touches the network or R."""
    src=tmp_path/'src';src.mkdir(exist_ok=True);out=tmp_path/'out';out.mkdir(exist_ok=True)
    if rows:
        with (src/'press_delta_pending.csv').open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['bioguide','member','url','title','date']);w.writeheader()
            w.writerow({'bioguide':'X000001','member':'Test Member','url':URL,'title':'A long enough title','date':'2026-07-01'})
    if prefill: prefill(out)
    clock={'t':0.0}
    monkeypatch.setattr(rpr,'time',types.SimpleNamespace(monotonic=lambda:clock['t'],sleep=lambda s:None,time=time.time))
    class Session:
        def __init__(self): self.headers={}
        def get(self,url,**kw): return get(url,clock)
    monkeypatch.setattr(requests,'Session',Session)
    import collect.refresh_press_stream as stream
    monkeypatch.setattr(stream,'find_rscript',lambda:'Rscript')
    def fake_r(cmd,**kw):
        if r_writes_receipt:
            refresh=Path(cmd[2]);refresh.mkdir(exist_ok=True)  # cmd = [Rscript, driver, refresh_dir, ...]
            (refresh/'press_network_retry_receipt.json').write_text('{"fresh": true}',encoding='utf-8')
        return types.SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(rpr.subprocess,'run',fake_r)
    monkeypatch.setattr(sys,'argv',['recover_press_refresh','--source',str(src),'--output',str(out),'--hours',hours])
    rpr.main()
    return out

def response(status):
    return types.SimpleNamespace(status_code=status,url=URL,headers={},content=b'')

def ledger(out): return [json.loads(l) for l in (out/'article_results.jsonl').read_text(encoding='utf-8').splitlines()]

def test_item_out_of_time_mid_route_is_deferred_not_final(monkeypatch,tmp_path):
    def get(url,clock): clock['t']=10_000;return response(404)  # the deadline passes during the request
    out=run_worker(monkeypatch,tmp_path,get)
    assert [x['status'] for x in ledger(out)]==['deferred_deadline']  # resume retries it (not loaded as final)

def test_item_with_time_left_is_unavailable(monkeypatch,tmp_path):
    out=run_worker(monkeypatch,tmp_path,lambda url,clock:response(404))
    assert [x['status'] for x in ledger(out)]==['unavailable']

def test_request_error_is_retried_but_attempted_needs_a_status(monkeypatch,tmp_path):
    calls=[]
    def get(url,clock): calls.append(url);raise requests.ConnectionError('down')
    out=run_worker(monkeypatch,tmp_path,get)
    assert len(calls)==2  # RequestException: recorded and retried once
    assert json.loads((out/'replay.json').read_text(encoding='utf-8'))[0]['attempted'] is False  # no http_status

def test_logic_error_is_recorded_and_not_retried(monkeypatch,tmp_path):
    calls=[]
    def get(url,clock): calls.append(url);raise ValueError('boom')
    out=run_worker(monkeypatch,tmp_path,get)
    assert len(calls)==1
    assert ledger(out)[0]['attempts']==[{'route':'official_detail','error':'ValueError: boom'}]

def test_http_status_counts_as_attempted(monkeypatch,tmp_path):
    out=run_worker(monkeypatch,tmp_path,lambda url,clock:response(404))
    assert json.loads((out/'replay.json').read_text(encoding='utf-8'))[0]['attempted'] is True

@pytest.mark.parametrize('content',['','not-a-pid','   \n'])
def test_unparsable_lock_is_stale(monkeypatch,tmp_path,content):
    def prefill(out): (out/'worker.lock').write_text(content,encoding='utf-8')
    out=run_worker(monkeypatch,tmp_path,lambda url,clock:response(404),rows=0,prefill=prefill)
    assert not (out/'worker.lock').exists()
    assert json.loads((out/'recovery_receipt.json').read_text(encoding='utf-8'))['pid']==os.getpid()

def test_live_lock_still_refuses(monkeypatch,tmp_path):
    def prefill(out): (out/'worker.lock').write_text(str(os.getpid()),encoding='utf-8')
    with pytest.raises(SystemExit): run_worker(monkeypatch,tmp_path,lambda url,clock:response(404),rows=0,prefill=prefill)

def test_missing_source_receipt_is_null_not_a_crash(monkeypatch,tmp_path):
    out=run_worker(monkeypatch,tmp_path,lambda url,clock:response(404),rows=0)
    receipt=json.loads((out/'recovery_receipt.json').read_text(encoding='utf-8'))
    assert receipt['status']=='REFRESH_FAILED' and receipt['source_receipt_sha256'] is None

@pytest.mark.parametrize('returncode',[0,3])
def test_fresh_refresh_receipt_completes(monkeypatch,tmp_path,returncode):
    out=run_worker(monkeypatch,tmp_path,lambda url,clock:response(404),rows=0,returncode=returncode,r_writes_receipt=True)
    receipt=json.loads((out/'recovery_receipt.json').read_text(encoding='utf-8'))
    assert receipt['status']=='COMPLETE' and receipt['refresh_exit_code']==returncode
    assert receipt['refresh_receipt']=={'fresh':True}

def test_stale_refresh_receipt_from_an_earlier_run_does_not_complete(monkeypatch,tmp_path):
    def prefill(out):
        refresh=out/'refresh';refresh.mkdir();stale=refresh/'press_network_retry_receipt.json'
        stale.write_text('{"stale": true}',encoding='utf-8');os.utime(stale,(1,1))
    out=run_worker(monkeypatch,tmp_path,lambda url,clock:response(404),rows=0,prefill=prefill)  # R wrote nothing new
    receipt=json.loads((out/'recovery_receipt.json').read_text(encoding='utf-8'))
    assert receipt['status']=='REFRESH_FAILED' and 'refresh_receipt' not in receipt
    assert (out/'refresh'/'press_network_retry_receipt.json').exists()  # never deleted

if __name__=='__main__': unittest.main()
