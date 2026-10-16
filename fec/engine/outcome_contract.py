"""Import-safe machinery for a NEW, committed prospective receptivity contract.

No contract or criterion is created here. The July 2026 snapshot and all historical
looks remain untouched. This is forecasting machinery, never spending-effect evidence.
Prepared outcome receipts are required; this module never imports production engines.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

PROTOCOL_TEXT = """H1 (seeding, Chen/SNAM): frozen seed_prob correlates POSITIVELY with NEW cosponsorships adopted after the freeze (Spearman over members with panel coverage in both snapshots).
H2 (pressure, Byrne/F&H): frozen pressure_mult correlates POSITIVELY with |overall-tier movement| after the freeze; strongest among members the user's In-contact log marks engaged (run both unconditional and engaged-only when contact data exists).
BARS: rho >= +.20 PASS; +.10..+.20 DIRECTIONAL; below +.10 magnitudes stay design choices; rho <= -.10 INDICTED.
MINIMUM DATA: >= 60 days elapsed AND >= 150 members with any cosponsorship delta.
Engaged-only scoring is unavailable until separately specified, receipted contact data exists."""
CLAIM_SCOPE = 'receptivity_forecast_only_not_spending_effects'
TORD = {'Champion':4,'Defender':4,'Ally':3,'Highly Persuadable':2,'Persuadable':1,
        'Lean Persuadable':0.5,'True Neutral':0,'Neutral':0,'Lean Skeptic':-0.5,
        'Skeptic':-1,'Highly Skeptical':-2,'Opponent':-4}
HEX = re.compile(r'^[0-9a-f]{64}$')
LOOK_ID = re.compile(r'^receptivity-v2-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')

class Refusal(ValueError):
    """Fail closed without authorizing or silently repeating a look."""

def require(condition, message):
    if not condition:
        raise Refusal(message)

def now_utc():
    return dt.datetime.now(dt.timezone.utc)

def timestamp(value):
    require(isinstance(value,str), 'UTC timestamp required')
    try:
        result=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise Refusal('Invalid UTC timestamp') from exc
    require(result.tzinfo is not None and result.utcoffset()==dt.timedelta(0), 'UTC timestamp required')
    return result

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')

def read_json(path, expected_sha256=None):
    raw=Path(path).read_bytes()
    if expected_sha256 is not None:
        require(hashlib.sha256(raw).hexdigest()==expected_sha256, 'Parsed input fingerprint drift')
    return json.loads(raw)

def inside(root, relative):
    require(isinstance(relative,str) and relative and not Path(relative).is_absolute(), 'Repository-relative path required')
    path=(root/relative).resolve()
    require(path.is_relative_to(root.resolve()), 'Path escapes repository')
    return path

def pin(root, spec):
    require(isinstance(spec,dict) and set(spec)=={'path','sha256'}, 'Exact path/sha256 pin required')
    require(isinstance(spec['sha256'],str) and HEX.fullmatch(spec['sha256']), 'Invalid SHA-256')
    path=inside(root,spec['path'])
    require(path.is_file() and digest(path)==spec['sha256'], 'Missing or drifted input: '+spec['path'])
    return path

def git(root, *args):
    result=subprocess.run(['git','-c','gc.auto=0',*args],cwd=root,capture_output=True,check=False)
    require(result.returncode==0, 'Git lock verification failed')
    return result.stdout

def committed_bytes(root, path, commit, at):
    """Verify exact committed bytes and ancestry; normalize Git's timezone offset."""
    root=Path(root).resolve(); path=Path(path).resolve()
    require(path.is_relative_to(root), 'Evidence must be committed in this repository')
    require(isinstance(commit,str) and re.fullmatch(r'[0-9a-f]{40}',commit), 'Full externally supplied commit required')
    git(root,'merge-base','--is-ancestor',commit,'HEAD')
    raw=path.read_bytes(); rel=path.relative_to(root).as_posix()
    require(raw==git(root,'show',commit+':'+rel), 'Committed evidence bytes differ (including EOL)')
    committed=dt.datetime.fromisoformat(git(root,'show','-s','--format=%cI',commit).decode().strip())
    require(committed.tzinfo is not None, 'Git timestamp must have timezone')
    committed=committed.astimezone(dt.timezone.utc)
    require(committed<=at, 'Commit is future-dated')
    return raw,committed

def load_contract(root, path, lock_commit, at=None):
    """Only contract/source bytes are read here; never outcome or predictor values."""
    root=Path(root).resolve(); path=Path(path).resolve(); at=at or now_utc()
    raw,locked=committed_bytes(root,path,lock_commit,at)
    rel=path.relative_to(root).as_posix()
    contract=json.loads(raw)
    expected={'schema','status','look_id','claim_scope','protocol_sha256','tool_sha256','definition',
              'constructors','window_start_utc','window_end_utc','historical_reuse'}
    require(isinstance(contract,dict) and set(contract)==expected, 'Unknown or incomplete contract schema')
    require(contract['schema']=='moneyball-prospective-contract-v2' and contract['status']=='LOCKED', 'Contract is not LOCKED v2')
    require(isinstance(contract['look_id'],str) and LOOK_ID.fullmatch(contract['look_id']), 'New namespaced UUIDv4 look ID required; historical IDs refused')
    require(contract['historical_reuse'] is False, 'Historical or retired look reuse refused')
    require(contract['claim_scope']==CLAIM_SCOPE, 'This contract does not identify spending effects')
    require(contract['protocol_sha256']==hashlib.sha256(PROTOCOL_TEXT.encode()).hexdigest(), 'H1/H2 or bars differ from machinery contract')
    require(contract['tool_sha256']==digest(Path(__file__)), 'Scoring machinery fingerprint drift')
    start=timestamp(contract['window_start_utc']); end=timestamp(contract['window_end_utc'])
    require(locked<start<end, 'Lock must precede the declared future outcome window')
    definition=contract['definition']
    require(isinstance(definition,dict) and set(definition)=={'id','h1_counting_unit','h1_role_policy','h1_time_rule','h1_scope_sha256','h2_definition_id','tier_ordinals'}, 'Explicit criterion definition required')
    require(all(isinstance(definition[k],str) and definition[k] for k in definition if k!='tier_ordinals'), 'Empty criterion definition')
    require(HEX.fullmatch(definition['h1_scope_sha256']), 'Pinned criterion universe required')
    require(canonical(definition['tier_ordinals'])==canonical(TORD), 'Historical numeric tier treatment must not change silently')
    constructors=contract['constructors']
    require(isinstance(constructors,dict) and set(constructors)=={'h1','h2','predictor'}, 'Outcome and predictor constructor fingerprints required')
    for spec in constructors.values(): pin(root,spec)
    return {'contract':contract,'sha256':hashlib.sha256(raw).hexdigest(),'lock_commit':lock_commit,
            'locked_utc':locked.isoformat(),'contract_path':rel}

def ledger(root, contract):
    # No argument or environment variable can redirect the one-look ledger.
    path=Path(root).resolve()/'data/engine/outcome_looks'/contract['look_id']
    require(path.resolve()==path, 'Redirected ledger refused')
    return path

def exclusive_json(path, value):
    # O_EXCL protects reservation and freeze from concurrent calls; never replace.
    with Path(path).open('xb') as handle:
        handle.write(canonical(value)+b'\n'); handle.flush(); os.fsync(handle.fileno())

def source_receipt(root, path, context, at, schema, expected_sha256=None):
    require(Path(path).resolve().is_relative_to(Path(root).resolve()), 'Source receipt must be in repository')
    receipt=read_json(path,expected_sha256); contract=context['contract']
    require(isinstance(receipt,dict) and set(receipt)=={'schema','as_of_utc','definition','constructors','inputs','data'}, 'Invalid source receipt schema')
    require(receipt['schema']==schema, 'Legacy source archive cannot be reused')
    require(canonical(receipt['definition'])==canonical(contract['definition']), 'Criterion count/role/tier definition drift')
    require(canonical(receipt['constructors'])==canonical(contract['constructors']), 'Constructor provenance differs')
    for spec in receipt['constructors'].values(): pin(root,spec)
    require(isinstance(receipt['inputs'],list) and receipt['inputs'], 'Constructor input provenance required')
    for spec in receipt['inputs']: pin(root,spec)
    measured=timestamp(receipt['as_of_utc'])
    require(timestamp(context['locked_utc'])<=measured<=at, 'Outcome receipt predates lock or is future-dated')
    data_path=pin(root,receipt['data'])
    return receipt,data_path,measured

def outcomes(path, expected_sha256=None):
    document=read_json(path,expected_sha256)
    require(isinstance(document,dict) and set(document)=={'members'}, 'Outcome input must contain only members')
    require(isinstance(document['members'],dict) and document['members'], 'No outcome rows')
    for name,row in document['members'].items():
        require(isinstance(name,str) and isinstance(row,dict) and set(row)=={'n_cosponsored','overall'}, 'Outcome rows must not contain predictors or undefined fields')
        count=row['n_cosponsored']
        require(count is None or type(count) is int and count>=0, 'Cosponsorship count must be nonnegative integer or null')
        require(row['overall'] in TORD, 'Unknown tier; no silent default-to-zero')
    return document['members']

def predictions(path, expected_sha256=None):
    document=read_json(path,expected_sha256)
    require(isinstance(document,dict) and set(document)=={'members'} and
            isinstance(document['members'],dict) and document['members'], 'Exact predictor-only document required')
    result={}
    for name,row in document['members'].items():
        require(isinstance(name,str) and isinstance(row,dict) and set(row)=={'seed_prob','pressure_mult'},
                'Exact predictor-only row required')
        values={k:row.get(k) for k in ('seed_prob','pressure_mult')}
        require(all(type(v) in (int,float) and math.isfinite(v) for v in values.values()), 'Finite frozen predictors required')
        require(0<=values['seed_prob']<=1 and values['pressure_mult']>=0, 'Predictor physical domain violated')
        result[name]=values
    return result

def freeze(root, context, predictor_receipt_path, at=None):
    at=at or now_utc(); contract=context['contract']; folder=ledger(root,contract)
    require(not folder.exists(), 'Look ID already frozen/reserved; never overwrite or reuse')
    require(timestamp(context['locked_utc'])<=at<timestamp(contract['window_start_utc']), 'Freeze must precede future window')
    source_hash=digest(predictor_receipt_path)
    receipt,predictor_path,measured=source_receipt(root,predictor_receipt_path,context,at,'moneyball-predictors-v2',source_hash)
    predictor=predictions(predictor_path,receipt['data']['sha256'])
    require(all(len({row[key] for row in predictor.values()})>1 for key in ('seed_prob','pressure_mult')), 'Constant predictors cannot define Spearman')
    require(digest(predictor_receipt_path)==source_hash, 'Predictor receipt changed during freeze')
    pin(root,receipt['data'])
    folder.parent.mkdir(parents=True,exist_ok=True)
    try: folder.mkdir()
    except FileExistsError as exc: raise Refusal('Look ID already frozen; concurrent freeze refused') from exc
    exclusive_json(folder/'predictors.json',{'members':predictor})
    snapshot={**context,'frozen_utc':at.isoformat(),'protocol_text':PROTOCOL_TEXT,
              'predictor_as_of_utc':measured.isoformat(),'source_receipt':receipt,
              'source_receipt_sha256':source_hash,
              'member_ids':sorted(predictor),
              'files':{'predictors.json':digest(folder/'predictors.json')}}
    exclusive_json(folder/'snapshot.json',snapshot)
    exclusive_json(folder/'freeze_receipt.json',{'snapshot_sha256':digest(folder/'snapshot.json'),
                    'contract_sha256':context['sha256'],'frozen_utc':at.isoformat()})
    return {'status':'FROZEN_NEW_PROSPECTIVE_CONTRACT','snapshot':str(folder/'snapshot.json'),
            'claim_scope':CLAIM_SCOPE,'look_consumed':False}

def spearman(pairs):
    def rank(values):
        order=sorted(range(len(values)),key=values.__getitem__); result=[0.0]*len(values); i=0
        while i<len(order):
            j=i
            while j+1<len(order) and values[order[j+1]]==values[order[i]]: j+=1
            for k in range(i,j+1): result[order[k]]=(i+j)/2.0
            i=j+1
        return result
    x,y=(rank(list(v)) for v in zip(*pairs)); n=len(x)
    mx,my=sum(x)/n,sum(y)/n
    numerator=sum((a-mx)*(b-my) for a,b in zip(x,y))
    denom=math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))
    require(denom>0, 'Undefined Spearman: constant or empty input')
    return numerator/denom

def verdict(value):
    return 'PASS' if value>=.20 else 'DIRECTIONAL' if value>=.10 else 'INDICTED' if value<=-.10 else 'NULL — magnitudes stay design choices'

def score(root, context, snapshot_path, current_receipt_path, at=None, *, freeze_commit,
          baseline_receipt_path, baseline_commit):
    at=at or now_utc(); contract=context['contract']; folder=ledger(root,contract)
    require(Path(snapshot_path).resolve()==(folder/'snapshot.json').resolve(), 'Legacy or redirected snapshot refused')
    require(not any((folder/name).exists() for name in ('look-reservation.json','result.json','failure-after-reservation.json')),
            'One look already reserved/consumed; no rerun')
    raw,anchored=committed_bytes(root,folder/'snapshot.json',freeze_commit,at)
    git(root,'merge-base','--is-ancestor',context['lock_commit'],freeze_commit)
    snapshot=json.loads(raw)
    require(all(snapshot[k]==context[k] for k in context), 'Lock provenance differs from freeze')
    frozen=timestamp(snapshot['frozen_utc'])
    start=timestamp(contract['window_start_utc']); end=timestamp(contract['window_end_utc'])
    require(timestamp(context['locked_utc'])<=frozen<=anchored<start<end<=at, 'Lock/freeze/window ordering violated')
    require(digest(folder/'predictors.json')==snapshot['files']['predictors.json'], 'Frozen input fingerprint drift')
    baseline_raw,baseline_anchored=committed_bytes(root,baseline_receipt_path,baseline_commit,at)
    git(root,'merge-base','--is-ancestor',freeze_commit,baseline_commit)
    require(start<=baseline_anchored<end, 'Baseline must be anchored during the window, before its end')
    baseline_receipt,baseline_path,baseline_asof=source_receipt(root,baseline_receipt_path,context,at,'moneyball-outcomes-v2',hashlib.sha256(baseline_raw).hexdigest())
    current_hash=digest(current_receipt_path)
    receipt,data_path,measured=source_receipt(root,current_receipt_path,context,at,'moneyball-outcomes-v2',current_hash)
    require(baseline_asof==start and measured==end, 'Outcome cutoffs must exactly match the declared window')
    # Criterion-only eligibility: predictors remain unopened until the reservation.
    baseline=outcomes(baseline_path,baseline_receipt['data']['sha256']); current=outcomes(data_path,receipt['data']['sha256'])
    require(set(baseline)==set(snapshot['member_ids']), 'Baseline differs from frozen roster')
    deltas=[]
    for name,row in baseline.items():
        if name not in current or row['n_cosponsored'] is None or current[name]['n_cosponsored'] is None: continue
        delta=current[name]['n_cosponsored']-row['n_cosponsored']
        require(delta>=0, 'Negative count delta: criterion/role drift; clipping is forbidden')
        deltas.append((name,delta))
    days=(end-start).total_seconds()/86400
    movers=sum(delta>0 for _,delta in deltas)
    moved=[(name,abs(TORD[current[name]['overall']]-TORD[row['overall']]))
           for name,row in baseline.items() if name in current]
    def stable_inputs():
        require(digest(current_receipt_path)==current_hash, 'Current receipt changed during scoring')
        require(Path(baseline_receipt_path).read_bytes()==baseline_raw, 'Baseline receipt changed during scoring')
        require(Path(snapshot_path).read_bytes()==raw, 'Snapshot changed during scoring')
        for item in (receipt,baseline_receipt):
            pin(root,item['data'])
            for spec in item['inputs']+list(item['constructors'].values()): pin(root,spec)
        require(digest(Path(__file__))==contract['tool_sha256'], 'Scoring machinery changed')
    stable_inputs()
    if days<60 or movers<150:
        return {'status':'UNDERPOWERED','days':days,'covered':len(deltas),'movers':movers,'look_consumed':False}
    require(len({delta for _,delta in deltas})>1 and len({value for _,value in moved})>1,
            'Undefined Spearman: constant outcome criterion; no look consumed')
    reservation={'contract_sha256':context['sha256'],'snapshot_sha256':digest(folder/'snapshot.json'),
                 'freeze_commit':freeze_commit,'baseline_commit':baseline_commit,
                 'baseline_receipt_sha256':hashlib.sha256(baseline_raw).hexdigest(),
                 'current_receipt_sha256':current_hash,'reserved_utc':at.isoformat()}
    try: exclusive_json(folder/'look-reservation.json',reservation)
    except FileExistsError as exc: raise Refusal('Concurrent or previously consumed look refused') from exc
    try:
        # Hash again at the boundary, then parse frozen predictors exactly once.
        require(digest(folder/'predictors.json')==snapshot['files']['predictors.json'], 'Frozen predictors changed at reservation')
        predictor=predictions(folder/'predictors.json',snapshot['files']['predictors.json'])
        require(set(baseline)<=set(predictor), 'Baseline includes members outside frozen roster')
        h1=spearman([(predictor[name]['seed_prob'],delta) for name,delta in deltas])
        h2=spearman([(predictor[name]['pressure_mult'],value) for name,value in moved])
        result={'status':'RESULT','look_consumed':True,'claim_scope':CLAIM_SCOPE,
                'h1':{'rho':h1,'n':len(deltas),'verdict':verdict(h1)},
                'h2':{'rho':h2,'n':len(moved),'verdict':verdict(h2)},
                'engaged_only':'NOT_IMPLEMENTED_NO_CONTACT_CONTRACT','reservation':reservation}
        stable_inputs()
        require(digest(folder/'predictors.json')==snapshot['files']['predictors.json'], 'Predictors changed during scoring')
        exclusive_json(folder/'result.json',result)
        return result
    except Exception as exc:
        exclusive_json(folder/'failure-after-reservation.json',{'status':'RESERVED_FAILURE_NO_AUTOMATIC_RETRY',
                       'error_type':type(exc).__name__,'look_consumed':True})
        raise

def main(argv=None, root=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--status',action='store_true')
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--score',metavar='SNAPSHOT')
    parser.add_argument('--contract'); parser.add_argument('--lock-commit')
    parser.add_argument('--current-receipt')
    parser.add_argument('--predictor-receipt'); parser.add_argument('--freeze-commit')
    parser.add_argument('--baseline-receipt'); parser.add_argument('--baseline-commit')
    args=parser.parse_args(argv)
    if not args.freeze and not args.score:
        print('STATUS: new committed v2 contract required; legacy snapshot scoring disabled. No outcome or predictor files opened. No look consumed.')
        return 0
    try:
        require(args.contract and args.lock_commit, 'Legacy invocation refused: --contract and --lock-commit are required; no look consumed')
        root=Path(root or Path(__file__).resolve().parents[2])
        context=load_contract(root,args.contract,args.lock_commit)
        if args.freeze:
            require(args.predictor_receipt, '--predictor-receipt required')
            result=freeze(root,context,args.predictor_receipt)
        else:
            require(all((args.current_receipt,args.freeze_commit,args.baseline_receipt,args.baseline_commit)),
                    'Current/baseline receipts and immutable freeze/baseline commits required; no look consumed')
            path=Path(args.current_receipt).resolve()
            require(path.is_relative_to(root.resolve()), 'Current receipt must be in repository')
            result=score(root,context,args.score,path,freeze_commit=args.freeze_commit,
                         baseline_receipt_path=args.baseline_receipt,baseline_commit=args.baseline_commit)
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    except (Refusal,OSError,ValueError,KeyError,TypeError) as exc:
        print('REFUSED: '+str(exc)); return 2
