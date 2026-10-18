"""Independent, resumable September press body recovery; no model/API calls.

Runs bounded official HTTP/WordPress retrieval, then replays the measured results
through the existing R historical-recovery materializer and scorer. Original
receipts are immutable; all additional provenance is bound in recovery_receipt.json.
"""
from __future__ import annotations
import argparse, concurrent.futures, csv, hashlib, json, os, re, subprocess
import sys, threading, time, traceback
import importlib
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Support both direct script execution and the canonical fec/ package bootstrap.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect.press_recovery_monitor import Telemetry, alive
from collect.press_source_extract import extract_source_article
from urllib.parse import urlsplit, urlunsplit, unquote

ROOT = Path(__file__).resolve().parents[2]
csv.field_size_limit(20_000_000)
def now(): return datetime.now(timezone.utc).isoformat()
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):
    if not p.exists(): return []
    with p.open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f))
def save(p, obj):
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
    for attempt in range(10):
        try: os.replace(tmp, p); return
        except PermissionError:
            if attempt == 9: raise
            time.sleep(.05)
def key(u):
    p = urlsplit(u)
    return (p.netloc.lower().removeprefix('www.') + p.path.rstrip('/') + ('?' + p.query if p.query else '')).lower()
def writecsv(p, rows, fields):
    with p.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader(); w.writerows(rows)

def unsupported_api(status, payload):
    """A vanished endpoint or WordPress route error, not an empty search result."""
    return status == 410 or (status == 404 and isinstance(payload, dict)
                             and payload.get('code') == 'rest_no_route')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=ROOT/'outputs/press_refresh_20260920')
    ap.add_argument('--output', type=Path, default=ROOT/'outputs/press_recovery_20260920')
    ap.add_argument('--hours', type=float, default=3)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--per-host', type=int, choices=(1,2), default=2)
    ap.add_argument('--retry-unavailable', action='store_true')
    ap.add_argument('--reparse-saved', action='store_true')
    a = ap.parse_args(); out = a.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    lock = out/'worker.lock'
    if lock.exists():
        try: old_pid = int(lock.read_text().strip())
        except ValueError: old_pid = None  # empty/corrupt lock (torn write) is stale, not fatal
        if alive(old_pid): raise SystemExit(f'Worker {old_pid} is still running.')
        lock.unlink(missing_ok=True)
    try: fd = os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise SystemExit('Another worker acquired the lock.')
    os.write(fd, str(os.getpid()).encode()); os.close(fd)
    if a.retry_unavailable and (out/'recovery_receipt.json').exists():
        backup=out/'passes'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup.mkdir(parents=True,exist_ok=True)
        for name in ['recovery_receipt.json','article_outcomes.csv','RECEIPT.md']:
            if (out/name).exists(): shutil.copy2(out/name,backup/name)
        if (out/'refresh').exists(): shutil.copytree(out/'refresh',backup/'refresh')
    started = now(); receipt = {'status':'RUNNING','pid':os.getpid(),'started_at':started,'source':str(a.source),'output':str(out),'ai_calls':0}
    save(out/'recovery_receipt.json', receipt)
    telemetry = Telemetry(out)
    telemetry.event('started', message='Preparing the recovery queue')
    try:
        vendor = out/'vendor'; sys.path.insert(0, str(vendor))
        try: from bs4 import BeautifulSoup
        except ImportError:
            subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--target',str(vendor),'beautifulsoup4'],check=True,timeout=180)
            sys.path.insert(0, str(vendor.resolve()))
            importlib.invalidate_caches()
            from bs4 import BeautifulSoup
        import requests
        pending = read(a.source/'press_delta_pending.csv'); failed = read(a.source/'press_delta_failed_bodies.csv')
        full = read(a.source/'press_delta_fulltext.csv'); index = read(a.source/'press_refreshed_index.csv')
        lookup = {(r.get('bioguide'),key(r['url'])):r for r in index+full}
        scope = {}
        for r in failed+pending:
            if r.get('source') == 'OFFICIAL_MENTION': continue
            k=(r.get('bioguide'),key(r['url']))
            scope[k]={**lookup.get(k,{}),**r}
        stance = read(ROOT/'press_stance_final.csv') + read(a.source/'press_delta_scored.csv')
        evidence = Counter(r.get('bioguide') for r in stance)
        recent = Counter(r.get('bioguide') for r in stance if (r.get('date') or '') >= '2026-06-25' and re.match(r'^2026-\d\d-\d\d',r.get('date','')))
        topic = re.compile(r'credit.union|NCUA|bank|financ|lending|consumer|interchange|deposit|debit|payment|CFPB', re.I)
        for r in scope.values():
            d = r.get('date_final') or r.get('date') or ''
            r['priority'] = (100 if topic.search(r.get('title','')+' '+r['url']) else 0)+(30 if d >= '2026-06-25' else 0)+(20 if not recent[r.get('bioguide')] else 0)+(5 if evidence[r.get('bioguide')] else 0)
        ordered = sorted(scope.values(),key=lambda r:(-r['priority'],r.get('bioguide',''),r['url']))
        writecsv(out/'priority_queue.csv',ordered,['bioguide','member','url','title','date','priority','collection_status'])
        members=defaultdict(list)
        for r in ordered: members[r.get('bioguide')].append(r)
        priorities=[{'bioguide':bg,'member':rows[0].get('member',''),'missing_rows':len(rows),'topic_candidates':sum(bool(topic.search(r.get('title','')+' '+r['url'])) for r in rows),'current_dated':sum((r.get('date_final') or r.get('date') or '') >= '2026-06-25' for r in rows),'existing_stance_rows':evidence[bg],'current_stance_rows':recent[bg],'priority':max(r['priority'] for r in rows)} for bg,rows in members.items()]
        writecsv(out/'member_priority.csv',sorted(priorities,key=lambda r:-r['priority']),list(priorities[0]) if priorities else ['bioguide'])
        failed_keys = {(x.get('bioguide'),key(x['url'])) for x in failed}
        diagnosis={'pending_rows':len(pending),'failed_rows':len(failed),'failed_statuses':dict(Counter(r.get('collection_status') for r in failed)),'pending_undated':sum(not r.get('date') for r in pending),'pending_overlap_failed':sum((r.get('bioguide'),key(r['url'])) in failed_keys for r in pending),'excluded_short_mentions':sum(r.get('source')=='OFFICIAL_MENTION' for r in failed),'unique_article_member_scope':len(scope),'pending_by_host':dict(Counter(urlsplit(r['url']).netloc for r in pending)),'mechanism':'press_request_deadlines.R shares a 600-second member deadline across fallback requests; press_backlog.R leaves unattempted/deferred URLs pending. failed_bodies includes pending and short mention rows, not only exhausted HTTP failures. retryable_body_candidates/recovered_bodies are populated in the --retry-from branch, so zero in the original live run does not mean no eligible failures. Unknown dates are blocking uncertainty, not proof of current publication.','priority_method':'Topic cues, known current dates, lack of current dated stance rows, then existing stance evidence. Title cues establish retrieval priority only, never stance.'}
        save(out/'diagnosis.json',diagnosis)
        deadline=time.monotonic()+a.hours*3600
        raw=out/'responses'; raw.mkdir(exist_ok=True)
        results={}; previous_results={}; ledger=out/'article_results.jsonl'
        if ledger.exists():
            for line in ledger.read_text(encoding='utf-8').splitlines():
                try:
                    item=json.loads(line)
                    previous_results[key(item['url'])]=item
                    if item['status']=='recovered' or (not a.retry_unavailable and item['status']=='unavailable'): results[key(item['url'])]=item
                except (ValueError,KeyError): pass
        def extract(html, url, expected):
            specialized=extract_source_article(html,url,expected)
            if specialized: return specialized
            soup=BeautifulSoup(html,'html.parser')
            title=soup.find('h1'); title=title.get_text(' ',strip=True) if title else ''
            for n in soup.select('script:not([type="application/ld+json"]),style,nav,footer,header,aside,form'): n.decompose()
            date=''; date_source=''
            for selector,attr in [('meta[property="article:published_time"]','content'),('meta[name="date"]','content'),('meta[name="datePublished"]','content'),('time[datetime]','datetime')]:
                n=soup.select_one(selector)
                if n and re.match(r'^\d{4}-\d\d-\d\d',n.get(attr,'')): date=n[attr][:10];date_source=selector;break
            bodies=[]
            for n in soup.select('script[type="application/ld+json"]'):
                try:
                    obj=json.loads(n.string or n.get_text()); stack=obj if isinstance(obj,list) else [obj]
                    while stack:
                        x=stack.pop()
                        if not isinstance(x,dict): continue
                        stack.extend(x.get('@graph',[]))
                        if x.get('@type') in ['NewsArticle','Article','PressRelease']:
                            if x.get('articleBody'): bodies.append(('jsonld',BeautifulSoup(x['articleBody'],'html.parser').get_text(' ',strip=True)))
                            if not date and re.match(r'^\d{4}-\d\d-\d\d',str(x.get('datePublished',''))): date=x['datePublished'][:10];date_source='jsonld.datePublished'
                except (ValueError,TypeError): pass
            # Drupal House, WordPress House, and Senate/Fireside detail containers.
            selectors=['.field--name-body','.field-name-body','.entry-content','.et_pb_post_content','.news-article .body','.press-release .body','.news-article','.press-release','.ArticleBody','.article-content','[itemprop="articleBody"]','article']
            for s in selectors:
                n=soup.select_one(s)
                if n: bodies.append((s,n.get_text(' ',strip=True)))
            for route,body in bodies:
                body=re.sub(r'\s+',' ',body).strip()
                if len(body.split())<40 or len(body)<250: continue
                if re.search(r'access denied|verify you are human|just a moment|page not found|enable javascript and cookies',title+' '+body[:350],re.I): continue
                tokens=set(re.findall(r'[a-z]{4,}',(expected or unquote(urlsplit(url).path)).lower()))
                if tokens and len(tokens & set(re.findall(r'[a-z]{4,}',(title+' '+body[:1200]).lower()))) < min(2,len(tokens)): continue
                return {'body':body,'title_detail':title or expected,'date_detail':date,'date_source':date_source,'extractor':route}
            return None
        if a.reparse_saved:
            telemetry.update(stage='reparsing_saved',total=len({key(r['url']) for r in ordered}))
            for row in ordered:
                k=key(row['url'])
                if k in results: continue
                prior=previous_results.get(k,{})
                for evidence in prior.get('attempts',[]):
                    if evidence.get('route')!='official_detail' or evidence.get('http_status')!=200 or not evidence.get('sha256'): continue
                    cached=raw/(evidence['sha256']+'.html')
                    if not cached.exists() or sha(cached)!=evidence['sha256']: continue
                    telemetry.event('parsing_saved',row,route='saved_official_detail')
                    recovered=extract(cached.read_bytes(),row['url'],row.get('title',''))
                    if recovered:
                        item=dict(prior,**recovered,status='recovered',recovery_route='saved_official_detail',
                                  response_sha256=evidence['sha256'],source_url=evidence.get('final_url',row['url']),reparsed_at=now())
                        results[k]=item
                        with ledger.open('a',encoding='utf-8') as f: f.write(json.dumps(item,ensure_ascii=False)+'\n')
                        telemetry.event('result',row,status='recovered',words=len(item['body'].split()),
                                        extractor=item['extractor'],preview=item['body'][:300])
                        telemetry.update(processed=len(results),recovered=sum(x['status']=='recovered' for x in results.values()))
                        break
                else:
                    telemetry.event('result',row,status='requires_network_retry',words=0)
            a.reparse_saved=False
            receipt.update(stage='retrieval',articles_processed=len(results),recovered_articles=sum(x['status']=='recovered' for x in results.values()),updated_at=now())
            save(out/'recovery_receipt.json',receipt)
        api_support = {}
        api_support_lock = threading.Lock()
        def supports_api(session, target, row):
            endpoint = target.split('?',1)[0]
            with api_support_lock:
                if endpoint in api_support: return api_support[endpoint]
                telemetry.event('checking_route',row,request_url=endpoint,route='wordpress')
                try:
                    response = session.get(endpoint, params={'per_page':1}, timeout=(7,18))
                    try: payload = response.json()
                    except ValueError: payload = None
                    supported = not unsupported_api(response.status_code,payload)
                    proof = {'endpoint':endpoint,'http_status':response.status_code,
                             'supported':supported,'response_sha256':hashlib.sha256(response.content).hexdigest(),
                             'at':now()}
                    with (out/'api_route_probes.jsonl').open('a',encoding='utf-8') as f:
                        f.write(json.dumps(proof)+'\n')
                    api_support[endpoint] = supported
                    telemetry.event('route_checked',row,route='wordpress',http_status=response.status_code,
                                    message='Endpoint available or inconclusive' if supported else 'Unsupported endpoint; skipping repeated queries')
                    return supported
                except requests.RequestException:
                    # An outage is not evidence that a route does not exist.
                    api_support[endpoint] = True
                    return True
        def hostwork(rows):
            session=requests.Session();session.headers['User-Agent']='MelangeSystemsResearchCollector/1.0 (public-records research)'
            output=[]
            for row in rows:
                u=row['url']; k=key(u)
                if k in results: continue
                item={'url':u,'status':'unavailable','attempts':[],'attempted_at':now()}
                if time.monotonic()>=deadline:
                    item['status']='deferred_deadline';output.append(item)
                    with write_lock:
                        with ledger.open('a',encoding='utf-8') as f: f.write(json.dumps(item)+'\n')
                    telemetry.event('result',row,status=item['status']);continue
                host=urlsplit(u).hostname or ''
                if not (host.endswith('.house.gov') or host.endswith('.senate.gov')):
                    item['status']='outside_official_scope';output.append(item)
                    with write_lock:
                        with ledger.open('a',encoding='utf-8') as f: f.write(json.dumps(item)+'\n')
                    telemetry.event('result',row,status=item['status']);continue
                p=urlsplit(u); base=urlunsplit((p.scheme,p.netloc,'','',''));slug=p.path.rstrip('/').split('/')[-1]
                routes=[('official_detail',u)]
                if row.get('press_wp_endpoint') and row.get('press_wp_api_id'): routes.append(('wordpress',row['press_wp_endpoint'].rstrip('/')+'/'+row['press_wp_api_id']))
                if re.search(r'/\d{4}/\d\d/|/press-releases?/',p.path):
                    routes.extend([('wordpress',base+'/wp-json/wp/v2/posts?slug='+slug),('wordpress',base+'/wp-json/wp/v2/press-releases?slug='+slug)])
                for route,target in routes:
                    if time.monotonic()>=deadline: break
                    # Only official same-host API routes are permitted.
                    if urlsplit(target).hostname != host: continue
                    if route=='wordpress' and '?' in target and not supports_api(session,target,row):
                        item['attempts'].append({'route':route,'url':target,'skipped':True,
                            'rejection':'unsupported_endpoint_confirmed_by_probe'})
                        continue
                    for attempt in range(2):
                        try:
                            telemetry.event('fetching',row,route=route,request_url=target,attempt=attempt+1)
                            response=session.get(target,timeout=(7,18))
                            evidence={'route':route,'url':target,'http_status':response.status_code,'final_url':response.url}
                            item['attempts'].append(evidence)
                            telemetry.event('response',row,route=route,http_status=response.status_code)
                            if response.status_code in (429,500,502,503,504) and attempt==0:
                                time.sleep(min(10,max(1,int(response.headers.get('Retry-After','2')) if response.headers.get('Retry-After','2').isdigit() else 2)));continue
                            if response.status_code!=200: break
                            if (urlsplit(response.url).hostname or '').removeprefix('www.') != host.removeprefix('www.'): evidence['rejection']='redirected_host';break
                            content=response.content
                            if len(content)>8_000_000: evidence['rejection']='response_size';break
                            digest=hashlib.sha256(content).hexdigest();evidence['sha256']=digest
                            (raw/(digest+('.json' if route=='wordpress' else '.html'))).write_bytes(content)
                            recovered=None
                            telemetry.event('parsing',row,route=route,bytes=len(content))
                            if route=='wordpress':
                                data=response.json();data=data if isinstance(data,list) else [data]
                                for post in data:
                                    if key(post.get('link',''))!=k: continue
                                    body=post.get('content',{}).get('rendered','');title=post.get('title',{}).get('rendered','')
                                    recovered=extract('<article><h1>'+title+'</h1>'+body+'</article>',u,row.get('title',''))
                                    if recovered: recovered.update(date_detail=str(post.get('date',''))[:10],date_source='wordpress.date');break
                            else: recovered=extract(response.content,u,row.get('title',''))
                            if recovered:
                                item.update(recovered,status='recovered',recovery_route=route,response_sha256=digest,source_url=response.url);break
                            evidence['rejection']='no_verified_article_body'
                            break
                        except Exception as e:
                            error=type(e).__name__+': '+str(e)[:220]
                            item['attempts'].append({'route':route,'error':error})
                            telemetry.event('request_failed',row,route=route,error=error)
                            if not isinstance(e,requests.RequestException): break  # a parse/logic error will not heal on retry
                    if item['status']=='recovered': break
                    time.sleep(.3)
                # Out of time before the routes finished: resume must retry, not treat it as final.
                if item['status']!='recovered' and time.monotonic()>=deadline: item['status']='deferred_deadline'
                output.append(item)
                # Append each completed article so interrupted host batches are resumable.
                with write_lock:
                    with ledger.open('a',encoding='utf-8') as f: f.write(json.dumps(item,ensure_ascii=False)+'\n')
                    results[k]=item
                    telemetry.update(processed=len(results),recovered=sum(x['status']=='recovered' for x in results.values()))
                    telemetry.event('result',row,status=item['status'],words=len(item.get('body','').split()),extractor=item.get('extractor',''),preview=item.get('body','')[:300],outcome=item['attempts'][-1] if item['attempts'] else {})
                    if len(results)%25==0:
                        receipt.update(stage='retrieval',articles_processed=len(results),recovered_articles=sum(x['status']=='recovered' for x in results.values()),updated_at=now());save(out/'recovery_receipt.json',receipt)
                time.sleep(.4)
            return output
        write_lock=threading.Lock();hosts=defaultdict(list);seen=set()
        for row in ordered:
            if key(row['url']) not in seen: hosts[urlsplit(row['url']).netloc].append(row);seen.add(key(row['url']))
        telemetry.update(stage='retrieval',total=len(seen),processed=len(results),recovered=sum(x['status']=='recovered' for x in results.values()))
        telemetry.event('queue_ready',message=f'{len(seen)} unique URLs; {len(results)} completed results resumed')
        batches=[]
        for rows in hosts.values():
            remaining=[row for row in rows if key(row['url']) not in results]
            for lane in range(a.per_host):
                if remaining[lane::a.per_host]: batches.append(remaining[lane::a.per_host])
        receipt['concurrency']={'total_workers':a.workers,'per_host':a.per_host}
        telemetry.update(per_host=a.per_host,workers=a.workers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
            for batch in pool.map(hostwork,batches):
                for item in batch: results[key(item['url'])]=item
        replay=[]
        for row in ordered:
            r=results[key(row['url'])]
            replay.append({'bioguide':row.get('bioguide',''),'url':row['url'],'body':r.get('body',''),'date_detail':r.get('date_detail') or row.get('date_final') or row.get('date') or '', 'title_detail':r.get('title_detail') or row.get('title',''),'attempted':any('http_status' in x for x in r['attempts']),'recovered':r['status']=='recovered'})
        save(out/'replay.json',replay)
        refresh=out/'refresh';refresh.mkdir(exist_ok=True)
        # Execute the real materializer/scorer with a measured retrieval adapter.
        # Recovered host bodies are never assigned to OFFICIAL_MENTION rows here.
        from collect.refresh_press_stream import find_rscript
        rscript=find_rscript()
        original=ROOT/'fec/collect/refresh_press_delta.R'
        hook='''
PRESS_BACKLOG_DIRECTORY <- file.path(run_root, "recovery_attempts")
dir.create(PRESS_BACKLOG_DIRECTORY, recursive=TRUE, showWarnings=FALSE)
.recovery_rows <- jsonlite::fromJSON(Sys.getenv("PRESS_RECOVERY_REPLAY"))
fetch_member_fulltext <- function(bg, urls, force=FALSE, js=FALSE) {
  x <- .recovery_rows[.recovery_rows$bioguide == bg & .recovery_rows$url %in% urls,,drop=FALSE]
  z <- tibble::tibble(url=x$url,title_detail=x$title_detail,
    date_detail=as.Date(x$date_detail),body=x$body)
  saveRDS(z,file.path(CHECKPOINT_DIR,"fulltext",paste0(bg,".rds")))
  press_member_network_result(list(bioguide=bg, requested_urls=urls,
    attempted_urls=x$url[x$attempted], completed_urls=x$url[x$recovered],
    budget_deferred_urls=setdiff(urls,x$url[x$attempted])))
  z
}
'''
        code=original.read_text(encoding='utf-8')
        code=re.sub(r'^wrapper_file <- .*$', 'wrapper_file <- '+json.dumps(str(original).replace('\\','/')),code,flags=re.M)
        marker='future::plan(future::multisession, workers = FUTURE_WORKERS)'
        if code.count(marker)!=1: raise RuntimeError('R integration marker changed')
        code=code.replace(marker,hook+'\n'+marker)
        driver=out/'replay_refresh.R';driver.write_text(code,encoding='utf-8')
        env=os.environ.copy();env['PRESS_RECOVERY_REPLAY']=str(out/'replay.json');env['PRESS_REVISIT_URL_CAP']='0'
        receipt.update(stage='rerunning_refresh',updated_at=now());save(out/'recovery_receipt.json',receipt)
        telemetry.update(stage='rerunning_refresh',active={})
        telemetry.event('refresh',message='Running R historical recovery and scoring')
        started_epoch=time.time()  # refresh/ is reused, not cleared; only a receipt written after this counts
        with (out/'refresh.log').open('w',encoding='utf-8') as log:
            run=subprocess.run([str(rscript),str(driver),str(refresh),'2026-06-25','1','50000','--retry-from',str(a.source.resolve())],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
        finalrows=read(refresh/'press_delta_fulltext.csv'); finalmap={(r.get('bioguide'),key(r['url'])):r for r in finalrows}
        outcomes=[]
        for row in ordered:
            r=results[key(row['url'])]; f=finalmap.get((row.get('bioguide'),key(row['url'])),{})
            outcomes.append({'bioguide':row.get('bioguide'),'member':row.get('member'),'url':row['url'],'priority':row['priority'],'status':r['status'],'attempts':len(r['attempts']),'date':r.get('date_detail',''),'recovery_route':r.get('recovery_route',''),'included_in_refresh':len(f.get('body','').split())>=20,'last_outcome':json.dumps(r['attempts'][-1]) if r['attempts'] else r['status']})
        writecsv(out/'article_outcomes.csv',outcomes,list(outcomes[0]) if outcomes else ['url','status'])
        unique=Counter(results[k]['status'] for k in seen); counts=Counter(x['status'] for x in outcomes)
        source_receipt=a.source/'press_network_retry_receipt.json'
        refresh_receipt=refresh/'press_network_retry_receipt.json'
        fresh=refresh_receipt.exists() and refresh_receipt.stat().st_mtime>=started_epoch
        receipt.update(status='COMPLETE' if run.returncode in (0,3) and fresh else 'REFRESH_FAILED',stage='finished',completed_at=now(),refresh_exit_code=run.returncode,unique_articles=dict(unique),article_member_rows=dict(counts),original_pending_rows=len(pending),original_failed_rows=len(failed),pending_is_subset_not_additive=diagnosis['pending_overlap_failed']==len(pending),excluded_short_mentions=diagnosis['excluded_short_mentions'],recovered_rows_in_refresh=sum(x['included_in_refresh'] and x['status']=='recovered' for x in outcomes),scope='Historical September 20 body recovery; no claim of fresh inventory or downstream stance promotion',sha256={str(p.relative_to(out)):sha(p) for p in [out/'diagnosis.json',out/'priority_queue.csv',out/'member_priority.csv',out/'replay.json',driver,out/'article_outcomes.csv']},source_receipt_sha256=sha(source_receipt) if source_receipt.exists() else None,worker_sha256=sha(__file__))
        if fresh: receipt['refresh_receipt']=json.loads(refresh_receipt.read_text(encoding='utf-8'))
        save(out/'recovery_receipt.json',receipt)
        summary=f"# September 20 bounded recovery\n\nStatus: {receipt['status']}.\n\nOriginal pending rows: {len(pending)}; failed rows: {len(failed)}; overlap: {diagnosis['pending_overlap_failed']}. Excluded short mention slices: {diagnosis['excluded_short_mentions']}.\n\nUnique article outcomes: {dict(unique)}. Article/member outcomes: {dict(counts)}.\n\nRecovered rows included in refresh: {receipt['recovered_rows_in_refresh']}. Refresh exit code: {run.returncode}.\n\nSee diagnosis.json, member_priority.csv, article_outcomes.csv and refresh.log for the bounded evidence. This is historical body recovery; live inventory and published stance inputs are not relabeled.\n"
        (out/'RECEIPT.md').write_text(summary,encoding='utf-8')
    except Exception:
        receipt.update(status='ERROR',completed_at=now(),error=traceback.format_exc());save(out/'recovery_receipt.json',receipt);raise
    finally:
        telemetry.event('finished',message=receipt['status'])
        telemetry.close(receipt['status'])
        lock.unlink(missing_ok=True)

if __name__=='__main__': main()
