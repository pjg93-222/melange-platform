"""Resume an exhaustive older LDA acquisition with existing audited collector.

All source payloads stay immutable. Each year must pass independent twin UUID,
payload and count reconciliation before it can be consumed downstream.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fec.staffer_network.lda_composite_repair import (
    SharedRequestPacer, _headers, _atomic_json, load_api_key,
    fetch_window, compare_twins, query_count, _load_completed_window_result,
)
from fec.staffer_network.reconcile_held_evidence import sha


def run(root, key_file, cutoff, years, *, rpm=120, max_in_flight_requests=8):
    if not (1 <= rpm <= 120):
        raise ValueError('Registered LDA API rate must be between 1 and 120 RPM')
    if not (2 <= max_in_flight_requests <= 8):
        raise ValueError('max_in_flight_requests must be between 2 and 8')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    path=root/'backfill_status.json'
    if path.exists():
        state=json.loads(path.read_text())
        if state['cutoff_exclusive']!=cutoff or state['years']!=years:raise ValueError('Resume parameters differ')
    else:
        state={'schema':'lda-exhaustive-historical-backfill-v1','years':years,'cutoff_exclusive':cutoff,
               'started_at':datetime.now(timezone.utc).isoformat(),'status':'RUNNING','completed':{},'current':None,
               'source':'https://lda.gov/api/v1/filings/','terms_checked_at':'2026-09-08',
               'source_notice':'Senate Office of Public Records cannot vouch for the data or analyses derived from these data after the data have been retrieved from LDA.gov.',
               'rate_limit_rpm':rpm,'production_activated':False,'builder_sha256':sha(Path(__file__))}
    prior_builder=state.get('builder_sha256')
    state['builder_sha256']=sha(Path(__file__))
    if prior_builder and prior_builder != state['builder_sha256']:
        state.setdefault('prior_builder_sha256',[]).append(prior_builder)
    state['rate_limit_rpm']=rpm
    state['max_in_flight_requests']=max_in_flight_requests
    state['twin_execution']='CONCURRENT_SHARED_PACER'
    headers=_headers(load_api_key(key_file));pacer=SharedRequestPacer(rpm)
    state['status']='RUNNING';state.pop('error',None)
    stop=threading.Event();lock=threading.Lock()
    def save():
        with lock:
            state['updated_at']=datetime.now(timezone.utc).isoformat()
            current=state.get('current')
            if current:
                twins=current.get('twins') or [current.get('twin')]
                current['downloaded_pages_by_twin']={}
                for twin in twins:
                    if not twin:continue
                    folder=root/'windows'/current['window_id']/twin
                    current['downloaded_pages_by_twin'][twin]=sum(1 for _ in folder.glob('page_*.json')) if folder.exists() else 0
                current['downloaded_pages']=sum(current['downloaded_pages_by_twin'].values())
            _atomic_json(path,state)
    def heartbeat():
        while not stop.wait(20):save()
    monitor=threading.Thread(target=heartbeat,daemon=True);monitor.start()
    try:
        # Reconcile recent changes as well as older years. A source year is not
        # presumed complete because a different posted-date window was checked.
        specs=[(year,'2026-08-29',cutoff,'refresh') for year in range(2021,2027)]
        specs += [(year,f'{year}-01-01',cutoff,'historical') for year in years]
        for year,start,end,kind in specs:
            window=f'{kind}-{year}-through-{cutoff[:10]}'
            if window in state['completed']:continue
            if shutil.disk_usage(root).free < 10*1024**3:raise RuntimeError('Acquisition paused: less than 10 GiB free space')
            state['current']={'window_id':window,'year':year,'kind':kind,'twins':['A','B'],'downloaded_pages':0}
            save()
            per_twin_in_flight=max_in_flight_requests//2
            twin_stop=threading.Event()

            def acquire_twin(twin):
                try:
                    manifest=root/'windows'/window/twin/'window_manifest.json'
                    # Completed twins are revalidated using saved bytes by the same
                    # collector; it is not safe to treat a partial page set as done.
                    if manifest.exists():
                        saved_manifest=json.loads(manifest.read_text(encoding='utf-8'))
                        return _load_completed_window_result(root,{
                            'window_id':window,'filing_year':year,'start_date':start,
                            'end_date_exclusive':end,
                            'max_in_flight_requests':saved_manifest['max_in_flight_requests']},twin)
                    return fetch_window(root,window_id=window,twin=twin,filing_year=year,start_date=start,
                        end_date_exclusive=end,headers=headers,pacer=pacer,max_in_flight_requests=per_twin_in_flight,
                        resume_existing_base_pages=(root/'windows'/window/twin).exists(),stop_event=twin_stop)
                except Exception:
                    twin_stop.set()
                    raise

            with ThreadPoolExecutor(max_workers=2,thread_name_prefix='lda-twin') as executor:
                results=list(executor.map(acquire_twin,('A','B')))
            comparison=compare_twins(*results)
            count,_=query_count(year,start,end,headers=headers,pacer=pacer)
            if not comparison['pass'] or count!=comparison['count_b']:raise ValueError('Twin or final source count reconciliation failed: '+window)
            state['completed'][window]={'year':year,'kind':kind,'records':count,'comparison':comparison}
            save();print('VERIFIED',window,count,flush=True)
        state['status']='ACQUISITION_PASS';state['current']=None;state['completed_at']=datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        state['status']='ATTENTION_REQUIRED';state['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        stop.set();monitor.join(timeout=5);save()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--key-file',type=Path,required=True);p.add_argument('--cutoff',default='2026-09-08')
    p.add_argument('--years',default=','.join(map(str,range(2020,1995,-1))))
    p.add_argument('--rpm',type=int,default=120)
    p.add_argument('--max-in-flight',type=int,default=8)
    a=p.parse_args();run(a.root,a.key_file,a.cutoff,[int(x) for x in a.years.split(',')],rpm=a.rpm,max_in_flight_requests=a.max_in_flight)


if __name__=='__main__':main()
