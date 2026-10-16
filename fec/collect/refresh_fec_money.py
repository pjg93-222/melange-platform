"""Refresh the engine's existing direct-PAC money scope from OpenFEC.

Current two-year cycle; full replacement from amendment-processed Schedule B,
not an additive delta. Individual contributions, PAC-to-PAC transfers and
independent expenditures are outside this existing measurement's scope.
"""
from pathlib import Path
import sys, os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse, ast, csv, json, re, hashlib, urllib.request, urllib.parse, urllib.error, time, http.client, socket
from datetime import datetime, timezone, date
from decimal import Decimal
from collections import defaultdict
from paths import ROOT
from moneyball_core import dump_atomic

# FEC_VOID_NET (Paul confirmed 2026-10-11, D-163): a negative 24K/24Z (a void or
# refund on the PAYER's report) nets only against its own original check, so a
# voided check contributes $0 and never reduces another PAC's money or the
# current cycle when the original sits in an earlier cycle. '0' = the previous
# plain per-member-group sum, byte for byte. Other values RAISE.
VOID_NET=os.environ.get('FEC_VOID_NET','1')
if VOID_NET not in ('0','1'): raise ValueError('FEC_VOID_NET must be 0 or 1')
_STATED=re.compile(r'(\d{1,2})/(\d{1,2})/(\d{2,4})')


def stated_original(row):
    """The original check date a void's memo/description names ('VOID OF 8/20/2025 ...'), else None."""
    m=_STATED.search(' '.join(str(row.get(k) or '') for k in ('memo_text','disbursement_description')))
    if not m: return None
    mo,dy,yr=map(int,m.groups())
    try: return date(yr+2000 if yr<100 else yr,mo,dy)
    except ValueError: return None


def net_voids(rows,cycle,rejected):
    """Remaining value per row after each negative is netted against its original.

    Same committee + candidate only. Original = the same-amount earlier check the
    void's text names (within 7 days), else the latest same-amount earlier check
    in this cycle; a stated date before the cycle means the original is outside
    this cycle, so the void counts $0 here. A partial refund draws down the pair's
    latest earlier checks; any excess counts $0, never another pair's money.
    ponytail: in-cycle LIFO when the payer names no date; ambiguous across cycles
    (e.g. McClintock 2025-05-08) stays in-cycle, recipient Schedule A would settle it.
    """
    left={};pairs=defaultdict(list);start=date(cycle-1,1,1)
    for r in rows:
        left[r['sub_id']]=Decimal(str(r['disbursement_amount']));pairs[(r['committee_id'],r['candidate_id'])].append(r)
    day=lambda r:str(r.get('disbursement_date') or '')[:10]
    for rs in pairs.values():
        rs.sort(key=lambda r:(day(r),str(r['sub_id'])))
        for n in [r for r in rs if left[r['sub_id']]<0]:
            amount=-left[n['sub_id']];left[n['sub_id']]=Decimal(0);stated=stated_original(n)
            if stated and stated<start:
                rejected['void_of_check_outside_cycle']+=1;continue
            earlier=[r for r in rs if left[r['sub_id']]>0 and day(r)<=day(n)]
            same=[r for r in earlier if left[r['sub_id']]==amount]
            if stated:
                named=[r for r in same if day(r) and abs((date.fromisoformat(day(r))-stated).days)<=7]
                same=sorted(named,key=lambda r:abs((date.fromisoformat(day(r))-stated).days),reverse=True) or same
            if same:
                left[same[-1]['sub_id']]=Decimal(0);continue
            for r in reversed(earlier):
                take=min(amount,left[r['sub_id']]);left[r['sub_id']]-=take;amount-=take
                if not amount: break
            if amount: rejected['refund_exceeds_pair_money']+=1
    return left


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def committee_policy(path):
    groups={}
    for node in ast.parse(Path(path).read_text(encoding='utf-8')).body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            name=node.targets[0].id
            if name in ('CU','BANKS','FINSERV'):
                for identifier in ast.literal_eval(node.value):
                    if identifier in groups: raise ValueError('committee assigned to multiple groups')
                    groups[identifier]={'CU':'cu','BANKS':'banks','FINSERV':'finserv'}[name]
    if set(groups.values())!={'cu','banks','finserv'}: raise ValueError('incomplete retained committee policy')
    return groups


def candidate_map(path):
    result={}
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            for candidate in re.findall(r'[HSP]\d[A-Z0-9]{2}\d{5}',row.get('fec_ids','')):
                bio=row['bioguide_id']
                if candidate in result and result[candidate]!=bio: raise ValueError('ambiguous candidate identity')
                result[candidate]=bio
    return result


def aggregate(transactions,candidates,groups,cycle):
    totals=defaultdict(lambda:defaultdict(Decimal)); seen={}; rejected=defaultdict(int); latest=''; kept=[]
    for row in transactions:
        identifier=str(row['sub_id'])
        payload=json.dumps(row,sort_keys=True)
        if identifier in seen:
            if seen[identifier]!=payload: raise ValueError('conflicting duplicate FEC transaction')
            rejected['duplicate_sub_id']+=1;continue
        seen[identifier]=payload
        if int(row['two_year_transaction_period'])!=cycle or row['committee_id'] not in groups:
            raise ValueError('FEC response violates requested committee/cycle scope')
        if row.get('memoed_subtotal') or row.get('memo_code')=='X':
            rejected['memo_excluded_under_retained_model_policy']+=1;continue
        if row.get('disbursement_type') not in ('24K','24Z'):
            rejected['not_direct_contribution']+=1;continue
        candidate=row.get('candidate_id')
        if candidate not in candidates:
            rejected['not_current_roster_candidate']+=1;continue
        value=Decimal(str(row['disbursement_amount']))
        if not value.is_finite(): raise ValueError('non-finite money amount')
        if VOID_NET=='0': totals[candidates[candidate]][groups[row['committee_id']]]+=value
        else: kept.append(row)
        latest=max(latest,str(row.get('disbursement_date') or '')[:10])
    if kept:
        left=net_voids(kept,cycle,rejected)
        for row in kept: totals[candidates[row['candidate_id']]][groups[row['committee_id']]]+=left[row['sub_id']]
    return totals,dict(rejected),latest


def project(roster,totals,receipts,candidates):
    result=[]; mapped=set(candidates.values())
    for prior in roster:
        row=dict(prior); bio=row.get('bioguide')
        if bio not in mapped:
            # Never silently turn missing candidate identity into a fresh zero.
            row['money_source_status']='UNRESOLVED_CANDIDATE_ID_LAST_VALUE_RETAINED'
            result.append(row);continue
        for group in ('cu','banks','finserv'):
            row[group]=max(0,round(totals.get(bio,{}).get(group,Decimal(0))))
        row['receipts']=round(receipts.get(bio,Decimal(0)))
        row['money_source_status']='VERIFIED_OPENFEC_CURRENT_CYCLE'
        result.append(row)
    # Stale values retained on UNRESOLVED rows are excluded from the normalization pool.
    pool={g:sum(row[g] for row in result if not row['money_source_status'].startswith('UNRESOLVED')) or 1 for g in ('cu','banks','finserv')}
    for row in result:
        total=sum(row[g] for g in pool);row['total']=total
        row['saturation']=round(100*total/row['receipts'],2) if row['receipts']>=100000 else None
        if total:
            normalized={g:row[g]/pool[g] for g in pool};denominator=sum(normalized.values())
            for g in pool:
                row[g+'_pct']=round(100*row[g]/total,1)
                row[g+'_adj']=round(100*normalized[g]/denominator,1)
            row['industry_adj']=round(row['banks_adj']+row['finserv_adj'],1)
            row['net_lean']=round(row['cu_adj']-row['industry_adj'],1)
        else:
            for field in ('cu_pct','banks_pct','finserv_pct','cu_adj','banks_adj','finserv_adj','industry_adj','net_lean'):
                row[field]=None
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(ROOT));p.add_argument('--no-install',action='store_true')
    p.add_argument('--key-file',type=Path,default=Path.home()/'OneDrive/Desktop/API Keys/keys.json')
    a=p.parse_args(argv);root=a.root.resolve();now=datetime.now(timezone.utc)
    cycle=now.year+(now.year%2)
    key=os.environ.get('FEC_API_KEY')
    if not key: key=json.loads(a.key_file.read_text(encoding='utf-8'))['data_gov']['keys'][0]
    run=root/'data/money/fec_generations'/now.strftime('%Y%m%dT%H%M%S%fZ');run.mkdir(parents=True)
    sources=[];requests=0;cache_hits=0
    def fail(exc):
        # URLs contain no key; no authentication material enters a receipt.
        dump_atomic({'status':'FAIL','checked_at':now.isoformat(),'error':str(exc),'sources':sources},str(run/'receipt.json'))
        print(type(exc).__name__,str(exc));return 1
    try:
        policy=root/'fec/attic/full_congestion.py';mapping=root/'fec/legislators.csv';roster_path=root/'data/engine/congestion.json'
        groups=committee_policy(policy);candidates=candidate_map(mapping)
        roster=json.loads(roster_path.read_text(encoding='utf-8'));roster_bios={r['bioguide'] for r in roster}
        candidates={c:b for c,b in candidates.items() if b in roster_bios}
        dependencies={str(x):digest(x) for x in (policy,mapping,roster_path)}
        cache=root/'data/money/fec_page_cache';cache.mkdir(parents=True,exist_ok=True)
    except Exception as exc: return fail(exc)
    def pages(endpoint,parameters,cursor=False):
        nonlocal requests,cache_hits
        params=list(parameters)+[('per_page',100)];seen=set();page=1
        while True:
            requests+=1
            if requests>900: raise ValueError('FEC refresh request budget reached; last-good retained')
            url='https://api.open.fec.gov/v1/'+endpoint+'/?'+urllib.parse.urlencode(params)
            cache_key=hashlib.sha256(url.encode()).hexdigest();cached=cache/(cache_key+'.json');meta=cache/(cache_key+'.receipt.json')
            try:
                cached_meta=json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {}
                age=(now-datetime.fromisoformat(cached_meta.get('checked_at','2000-01-01T00:00:00+00:00'))).total_seconds()
                cache_used=cached.exists() and 0<=age<=86400 and cached_meta.get('sha256')==digest(cached)
            except (ValueError,TypeError,AttributeError,OSError): cache_used=False  # corrupt/odd cache receipt = miss
            if cache_used:
                raw=cached.read_bytes();cache_hits+=1
            else:
                request=urllib.request.Request(url,headers={'X-Api-Key':key,'User-Agent':'Melange source refresh/1.0'})
                for attempt in range(6):
                    try:
                        with urllib.request.urlopen(request,timeout=90) as response:
                            raw=response.read(15000001)
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code==429:
                            retry=exc.headers.get('Retry-After') or '60';exc.close()
                            if attempt==5 or not retry.isdigit() or int(retry)>300:
                                raise RuntimeError('OpenFEC rate limit; retry after '+retry+'; verified pages retained') from None
                            print('OpenFEC quota reset wait:',retry,'seconds',flush=True)
                            time.sleep(max(1,int(retry)))
                        elif 500<=exc.code<600 and attempt<5:
                            exc.close();wait=min(60,2**attempt*5)
                            print('OpenFEC HTTP',exc.code,'retry in',wait,'seconds',flush=True);time.sleep(wait)
                        else: raise
                    except (urllib.error.URLError,TimeoutError,socket.timeout,ConnectionError,http.client.HTTPException) as exc:
                        if attempt==5: raise
                        wait=min(60,2**attempt*5)
                        print('OpenFEC',type(exc).__name__,'retry in',wait,'seconds',flush=True);time.sleep(wait)
            if len(raw)>15000000: raise ValueError('FEC page exceeds envelope')
            document=json.loads(raw)
            if not isinstance(document.get('results'),list) or not isinstance(document.get('pagination'),dict):
                raise ValueError('malformed FEC response')
            if not cache_used:
                cached.write_bytes(raw)
                dump_atomic({'url':url,'checked_at':datetime.now(timezone.utc).isoformat(),'sha256':digest(cached)},str(meta))
            file=run/f'page-{requests:04d}.json';file.write_bytes(raw)
            sources.append({'url':url,'path':str(file),'sha256':digest(file)})
            yield from document['results']
            if not document['results']: break
            pagination=document['pagination']
            if cursor:
                indexes=pagination.get('last_indexes')
                if not indexes: break
                if any(v is None for v in indexes.values()):
                    raise ValueError('FEC cursor has a null sort value; refusing to paginate; last-good retained')
                token=json.dumps(indexes,sort_keys=True)
                if token in seen: raise ValueError('FEC pagination did not advance')
                seen.add(token)
                params=[(k,v) for k,v in params if not k.startswith('last_')]+list(indexes.items())
            else:
                if page>=int(pagination['pages']): break
                page+=1;params=[(k,v) for k,v in params if k!='page']+[('page',page)]
            time.sleep(.1)
    try:
        transactions=[]
        for committee in sorted(groups):
            transactions.extend(pages('schedules/schedule_b',[('committee_id',committee),('two_year_transaction_period',cycle),('sort','-disbursement_date'),('disbursement_type','24K'),('disbursement_type','24Z')],cursor=True))
            print('FEC',committee,'pages',requests,flush=True)
        totals,rejected,latest=aggregate(transactions,candidates,groups,cycle)
        receipts=defaultdict(Decimal);seen=set()
        ids=sorted(candidates)
        for start in range(0,len(ids),30):
            for row in pages('candidates/totals',[('cycle',cycle),('election_full','false')]+[('candidate_id',x) for x in ids[start:start+30]]):
                cid=row['candidate_id']
                if cid not in candidates or cid in seen: raise ValueError('unexpected/duplicate FEC candidate summary')
                seen.add(cid);amount=Decimal(str(row.get('receipts') or 0))
                if not amount.is_finite(): raise ValueError('non-finite FEC candidate receipts')
                receipts[candidates[cid]]+=amount
        if not totals or not receipts: raise ValueError('FEC refresh returned no attributable evidence')
        result=project(roster,totals,receipts,candidates)
        output=run/'congestion.json';dump_atomic(result,str(output))
        if any(digest(path)!=value for path,value in dependencies.items()): raise ValueError('FEC dependencies changed during refresh')
        receipt={'schema':'fec-money-refresh-v1','status':'PASS','checked_at':now.isoformat(),'cycle':cycle,
                 'scope':'existing CU/bank/financial-services direct PAC contributions; types 24K/24Z; memo policy retained',
                 'latest_attributed_transaction_date':latest,'sources':sources,'dependencies':dependencies,
                 'output':{'path':str(output),'sha256':digest(output)},'rejections':rejected,
                 'unresolved_members':[r['member'] for r in result if r['money_source_status'].startswith('UNRESOLVED')],
                 'counts':{'transactions':len(transactions),'pages':requests,'cache_hits':cache_hits,'members':len(result)},
                 'totals':{g:sum(r[g] for r in result if not r['money_source_status'].startswith('UNRESOLVED')) for g in groups.values()},
                 'totals_unresolved_stale':{g:sum(r[g] for r in result if r['money_source_status'].startswith('UNRESOLVED')) for g in groups.values()},
                 'n_unresolved':sum(1 for r in result if r['money_source_status'].startswith('UNRESOLVED'))}
        dump_atomic(receipt,str(run/'receipt.json'))
        if not a.no_install: dump_atomic(receipt,str(root/'data/money/fec_refresh_current.json'))
        print(json.dumps({k:v for k,v in receipt.items() if k not in ('sources','dependencies')}))
        return 0
    except Exception as exc: return fail(exc)


if __name__=='__main__': raise SystemExit(main())
