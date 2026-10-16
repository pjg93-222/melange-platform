"""Synthetic-only one-look machinery tests. Never open LIVE research data."""
from pathlib import Path
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import uuid

import pytest

SOURCE=Path(__file__).resolve().parents[2]/'engine'/'outcome_contract.py'
spec=importlib.util.spec_from_file_location('outcome_contract_under_test',SOURCE)
oc=importlib.util.module_from_spec(spec); spec.loader.exec_module(oc)
LOCK=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
FREEZE=LOCK+dt.timedelta(days=1)
START=FREEZE+dt.timedelta(days=1)
FUTURE=START+dt.timedelta(days=60)

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(oc.canonical(value)+b'\n')

def pin(root,path): return {'path':path.relative_to(root).as_posix(),'sha256':oc.digest(path)}

def command(root,*args,when=LOCK):
    env={**os.environ,'GIT_AUTHOR_DATE':when.isoformat(),'GIT_COMMITTER_DATE':when.isoformat()}
    result=subprocess.run(['git','-c','core.autocrlf=false','-c','user.name=Synthetic Fixture',
                           '-c','user.email=fixture@example.invalid',*args],cwd=root,env=env,
                           capture_output=True,check=True)
    return result.stdout.decode().strip()

def setup(tmp_path,n=150,contract_changes=None,lock_time=LOCK,days=60):
    root=tmp_path; command(root,'init')
    for name in ('h1.py','h2.py','predictor.py'): (root/name).write_text('# Synthetic constructor only\n')
    constructors={name:pin(root,root/(name+'.py')) for name in ('h1','h2','predictor')}
    definition={'id':'SYNTHETIC-NOT-A-REAL-CRITERION','h1_counting_unit':'synthetic cumulative events',
                'h1_role_policy':'one synthetic member/event','h1_time_rule':'after synthetic freeze',
                'h1_scope_sha256':'f'*64,'h2_definition_id':'synthetic tiers','tier_ordinals':oc.TORD}
    baseline={'members':{str(i):{'n_cosponsored':i,'overall':'True Neutral'} for i in range(n)}}
    predictors={'members':{str(i):{'seed_prob':(i+1)/(n+1),'pressure_mult':.5+i/(n+1)} for i in range(n)}}
    write(root/'baseline.json',baseline); write(root/'predictors.json',predictors)
    (root/'synthetic-source.txt').write_text('Synthetic source, no real outcome values\n')
    receipt={'schema':'moneyball-outcomes-v2','as_of_utc':START.isoformat(),'definition':definition,
             'constructors':constructors,'inputs':[pin(root,root/'synthetic-source.txt')],
             'data':pin(root,root/'baseline.json')}
    write(root/'baseline-receipt.json',receipt)
    pr=copy.deepcopy(receipt);pr.update(schema='moneyball-predictors-v2',as_of_utc=FREEZE.isoformat(),data=pin(root,root/'predictors.json'))
    write(root/'predictor-receipt.json',pr)
    contract={'schema':'moneyball-prospective-contract-v2','status':'LOCKED',
              'look_id':'receptivity-v2-'+str(uuid.uuid4()),'claim_scope':oc.CLAIM_SCOPE,
              'protocol_sha256':hashlib.sha256(oc.PROTOCOL_TEXT.encode()).hexdigest(),
              'tool_sha256':oc.digest(SOURCE),'definition':definition,'constructors':constructors,
              'window_start_utc':START.isoformat(),'window_end_utc':(START+dt.timedelta(days=days)).isoformat(),
              'historical_reuse':False}
    contract.update(contract_changes or {}); write(root/'contract.json',contract)
    command(root,'add','contract.json'); command(root,'commit','-m','Synthetic pre-look lock',when=lock_time)
    commit=command(root,'rev-parse','HEAD')
    return root,contract,commit,receipt

def prepared(tmp_path,n=150,days=60):
    root,c,commit,receipt=setup(tmp_path,n,days=days)
    context=oc.load_contract(root,root/'contract.json',commit,FREEZE)
    result=oc.freeze(root,context,root/'predictor-receipt.json',FREEZE)
    snapshot=Path(result['snapshot'])
    command(root,'add',snapshot.relative_to(root).as_posix())
    command(root,'commit','-m','Synthetic immutable prediction freeze',when=FREEZE)
    freeze_commit=command(root,'rev-parse','HEAD')
    command(root,'add','baseline-receipt.json')
    command(root,'commit','-m','Synthetic baseline before window end',when=START)
    baseline_commit=command(root,'rev-parse','HEAD')
    write(root/'test-anchors.json',{'freeze_commit':freeze_commit,'baseline_commit':baseline_commit})
    current={'members':{str(i):{'n_cosponsored':2*i+1,'overall':'Ally' if i>=n//2 else 'True Neutral'} for i in range(n)}}
    write(root/'current.json',current)
    receipt=copy.deepcopy(receipt);receipt.update(as_of_utc=c['window_end_utc'],data=pin(root,root/'current.json'))
    write(root/'current-receipt.json',receipt)
    return root,context,Path(result['snapshot']),root/'current-receipt.json'

def run_score(root,context,snapshot,rp,at=FUTURE,**overrides):
    anchors=oc.read_json(root/'test-anchors.json')
    anchors['baseline_receipt_path']=root/'baseline-receipt.json'
    anchors.update(overrides)
    return oc.score(root,context,snapshot,rp,at,**anchors)

def test_import_and_status_have_no_data_io(monkeypatch,capsys):
    source=SOURCE.read_text(); namespace={'__file__':str(SOURCE)}
    def forbidden(*a,**k): raise AssertionError('Unexpected file/data operation')
    monkeypatch.setattr(Path,'open',forbidden)
    exec(compile(source,str(SOURCE),'exec'),namespace)
    assert namespace['main']([])==0
    assert namespace['main'](['--status'])==0
    assert 'No look consumed' in capsys.readouterr().out

def test_legacy_cli_refuses_without_reading_snapshot(monkeypatch,capsys):
    monkeypatch.setattr(oc,'read_json',lambda *a:pytest.fail('Legacy data opened'))
    assert oc.main(['--score','data/engine/outcome_snapshot_2026-07-04.json'])==2
    assert oc.main(['--freeze'])==2
    assert 'Legacy invocation refused' in capsys.readouterr().out

@pytest.mark.parametrize('change',[
 {'status':'DRAFT'},{'look_id':'outcome_snapshot_2026-07-04'}, {'historical_reuse':True},
 {'claim_scope':'spending_effects'}, {'protocol_sha256':'0'*64},{'tool_sha256':'0'*64},
 {'unexpected':'field'}])
def test_invalid_contract_refused(tmp_path,change):
    root,c,commit,r=setup(tmp_path,contract_changes=change)
    with pytest.raises(oc.Refusal):oc.load_contract(root,root/'contract.json',commit,FREEZE)

def test_exact_git_bytes_and_ancestry(tmp_path):
    root,c,commit,r=setup(tmp_path)
    p=root/'contract.json'; p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
    with pytest.raises(oc.Refusal,match='Committed evidence bytes'):oc.load_contract(root,p,commit,FREEZE)
    write(p,c);command(root,'checkout','--orphan','synthetic-unrelated')
    command(root,'commit','-m','Unrelated synthetic root')
    with pytest.raises(oc.Refusal,match='Git lock'):oc.load_contract(root,p,commit,FREEZE)

def test_future_lock_refused(tmp_path):
    root,c,commit,r=setup(tmp_path,lock_time=FUTURE)
    with pytest.raises(oc.Refusal,match='future-dated'):oc.load_contract(root,root/'contract.json',commit,FREEZE)

def test_constructor_provenance_is_real_bytes(tmp_path):
    root,c,commit,r=setup(tmp_path)
    (root/'h1.py').write_text('# PLANTED DRIFT\n')
    with pytest.raises(oc.Refusal,match='drifted input'):oc.load_contract(root,root/'contract.json',commit,FREEZE)

def test_freeze_never_overwrites(tmp_path):
    root,ctx,snapshot,receipt=prepared(tmp_path)
    before=snapshot.read_bytes()
    with pytest.raises(oc.Refusal,match='never overwrite'):oc.freeze(root,ctx,root/'predictor-receipt.json',FREEZE)
    assert snapshot.read_bytes()==before
    assert oc.read_json(snapshot)['protocol_text']==oc.PROTOCOL_TEXT
    assert oc.PROTOCOL_TEXT

@pytest.mark.parametrize('mutation', ['definition','constructor','before-freeze','future','data-hash','unknown-tier','negative-delta'])
def test_outcome_guards_precede_predictors_and_reservation(tmp_path,monkeypatch,mutation):
    root,ctx,snapshot,receipt_path=prepared(tmp_path)
    r=oc.read_json(receipt_path); current=oc.read_json(root/'current.json')
    if mutation=='definition':r['definition']['h1_role_policy']='PLANTED ROLE DRIFT'
    if mutation=='constructor':(root/'h2.py').write_text('# PLANTED CONSTRUCTOR DRIFT\n')
    if mutation=='before-freeze':r['as_of_utc']=FREEZE.isoformat()
    if mutation=='future':r['as_of_utc']=(FUTURE+dt.timedelta(days=1)).isoformat()
    if mutation=='data-hash':r['data']['sha256']='0'*64
    if mutation=='unknown-tier':current['members']['0']['overall']='unknown'
    if mutation=='negative-delta':current['members']['1']['n_cosponsored']=0
    if mutation in ('unknown-tier','negative-delta'):
        write(root/'current.json',current);r['data']=pin(root,root/'current.json')
    write(receipt_path,r)
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Predictors opened before refusal'))
    with pytest.raises(oc.Refusal):run_score(root,ctx,snapshot,receipt_path)
    assert not (snapshot.parent/'look-reservation.json').exists()

@pytest.mark.parametrize('n,days',[(149,60),(150,59)])
def test_underpowered_does_not_open_predictors_or_consume(tmp_path,monkeypatch,n,days):
    root,ctx,snapshot,rp=prepared(tmp_path,n,days)
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Predictors opened underpowered'))
    result=run_score(root,ctx,snapshot,rp)
    assert result['status']=='UNDERPOWERED' and result['look_consumed'] is False
    assert not (snapshot.parent/'look-reservation.json').exists()

def test_one_look_reserved_before_predictor_read_and_never_repeated(tmp_path,monkeypatch):
    root,ctx,snapshot,rp=prepared(tmp_path)
    original=oc.predictions
    def observe(path,*args):
        assert (snapshot.parent/'look-reservation.json').exists()
        return original(path,*args)
    monkeypatch.setattr(oc,'predictions',observe)
    result=run_score(root,ctx,snapshot,rp)
    assert result['status']=='RESULT' and result['h1']['rho']==1.0
    assert result['claim_scope']==oc.CLAIM_SCOPE
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Repeated predictor read'))
    with pytest.raises(oc.Refusal,match='reserved/consumed'):run_score(root,ctx,snapshot,rp)

def test_failure_after_reservation_stays_consumed(tmp_path,monkeypatch):
    root,ctx,snapshot,rp=prepared(tmp_path)
    def broken(path,*args): raise ValueError('SYNTHETIC failure')
    monkeypatch.setattr(oc,'predictions',broken)
    with pytest.raises(ValueError,match='SYNTHETIC'):run_score(root,ctx,snapshot,rp)
    assert (snapshot.parent/'failure-after-reservation.json').exists()
    with pytest.raises(oc.Refusal,match='reserved/consumed'):run_score(root,ctx,snapshot,rp)

def test_atomic_reservation_and_fixed_location(tmp_path):
    root,ctx,snapshot,rp=prepared(tmp_path)
    path=snapshot.parent/'look-reservation.json'
    oc.exclusive_json(path,{'synthetic':'first reservation'})
    with pytest.raises(FileExistsError):oc.exclusive_json(path,{'synthetic':'racing reservation'})
    assert oc.read_json(path)=={'synthetic':'first reservation'}
    assert snapshot.parent==root/'data/engine/outcome_looks'/ctx['contract']['look_id']
    with pytest.raises(oc.Refusal,match='redirected'):run_score(root,ctx,root/'other.json',rp)

def test_snapshot_fingerprint_and_tier_contract(tmp_path):
    root,ctx,snapshot,rp=prepared(tmp_path)
    snapshot.write_bytes(snapshot.read_bytes()+b' ')
    with pytest.raises(oc.Refusal,match='Committed evidence bytes'):run_score(root,ctx,snapshot,rp)
    assert oc.TORD['Highly Skeptical']==-2 and oc.TORD['Opponent']==-4

@pytest.mark.parametrize('value,expected',[(.20,'PASS'),(.10,'DIRECTIONAL'),(-.10,'INDICTED'),(.099,'NULL — magnitudes stay design choices')])
def test_locked_numeric_bars(value,expected):assert oc.verdict(value)==expected

def test_tie_ranking_independent_known_answer():
    assert oc.spearman([(1,1),(1,2),(2,3)])==pytest.approx(3**.5/2)
    with pytest.raises(oc.Refusal,match='Undefined'):oc.spearman([(1,1),(1,2)])

def test_non_utc_timestamp_rejected():
    with pytest.raises(oc.Refusal,match='UTC'):oc.timestamp('2026-01-01T00:00:00-04:00')

def test_git_commit_timezone_normalized(tmp_path):
    offset=dt.timezone(dt.timedelta(hours=-4))
    root,c,commit,r=setup(tmp_path,lock_time=LOCK.astimezone(offset))
    ctx=oc.load_contract(root,root/'contract.json',commit,FREEZE)
    assert ctx['locked_utc']==LOCK.isoformat()

@pytest.mark.parametrize('mutation',['local-baseline-edit','late-baseline','late-freeze','unrelated-freeze'])
def test_immutable_anchor_guards(tmp_path,monkeypatch,mutation):
    root,ctx,snapshot,rp=prepared(tmp_path)
    kwargs={}
    if mutation=='local-baseline-edit':
        r=oc.read_json(root/'baseline-receipt.json');r['as_of_utc']=FREEZE.isoformat()
        write(root/'baseline-receipt.json',r)
    if mutation=='late-baseline':
        command(root,'commit','--amend','--no-edit',when=FUTURE)
        kwargs['baseline_commit']=command(root,'rev-parse','HEAD')
    if mutation=='late-freeze':
        command(root,'commit','--allow-empty','-m','Too-late synthetic freeze',when=START)
        kwargs['freeze_commit']=command(root,'rev-parse','HEAD')
    if mutation=='unrelated-freeze':
        command(root,'checkout','--orphan','unrelated-freeze')
        command(root,'commit','-m','Unrelated synthetic freeze',when=FREEZE)
        kwargs['freeze_commit']=command(root,'rev-parse','HEAD')
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Predictors opened before anchor refusal'))
    with pytest.raises(oc.Refusal):run_score(root,ctx,snapshot,rp,**kwargs)
    assert not (snapshot.parent/'look-reservation.json').exists()

def test_coordinated_mutable_ledger_edits_cannot_replace_anchor(tmp_path,monkeypatch):
    root,ctx,snapshot,rp=prepared(tmp_path)
    pp=snapshot.parent/'predictors.json';pred=oc.read_json(pp)
    pred['members']['0']['seed_prob']=999;write(pp,pred)
    snap=oc.read_json(snapshot);snap['files']['predictors.json']=oc.digest(pp);write(snapshot,snap)
    fr=snapshot.parent/'freeze_receipt.json';r=oc.read_json(fr)
    r['snapshot_sha256']=oc.digest(snapshot);write(fr,r)
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Predictors opened after coordinated edit'))
    with pytest.raises(oc.Refusal,match='Committed evidence bytes'):run_score(root,ctx,snapshot,rp)
    assert not (snapshot.parent/'look-reservation.json').exists()

@pytest.mark.parametrize('criterion',['h1','h2'])
def test_constant_outcomes_refuse_without_consumption(tmp_path,monkeypatch,criterion):
    root,ctx,snapshot,rp=prepared(tmp_path)
    current=oc.read_json(root/'current.json')
    for name,row in current['members'].items():
        if criterion=='h1':row['n_cosponsored']=int(name)+1
        else:row['overall']='Ally'
    write(root/'current.json',current);r=oc.read_json(rp);r['data']=pin(root,root/'current.json');write(rp,r)
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Undefined criterion paired with predictors'))
    with pytest.raises(oc.Refusal,match='constant outcome'):run_score(root,ctx,snapshot,rp)
    assert not (snapshot.parent/'look-reservation.json').exists()

@pytest.mark.parametrize('marker',['result.json','failure-after-reservation.json'])
def test_missing_reservation_does_not_reopen_result(tmp_path,monkeypatch,marker):
    root,ctx,snapshot,rp=prepared(tmp_path)
    write(snapshot.parent/marker,{'synthetic':'consumed'})
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Consumed predictors reopened'))
    with pytest.raises(oc.Refusal,match='reserved/consumed'):run_score(root,ctx,snapshot,rp)

def test_actual_parallel_exclusive_reservation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'reservation.json'
    def reserve(i):
        try:oc.exclusive_json(path,{'writer':i});return True
        except FileExistsError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,[1,2]))
    assert sorted(results)==[False,True]
    assert oc.read_json(path)['writer'] in (1,2)

def test_parse_checks_exact_bytes_after_boundary_swap(tmp_path,monkeypatch):
    root,ctx,snapshot,rp=prepared(tmp_path)
    original=oc.outcomes
    def swap(path,*args):
        if Path(path)==root/'current.json':
            value=oc.read_json(path);value['members']['0']['n_cosponsored']=999;write(Path(path),value)
        return original(path,*args)
    monkeypatch.setattr(oc,'outcomes',swap)
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Swapped outcome paired'))
    with pytest.raises(oc.Refusal,match='Parsed input fingerprint drift'):run_score(root,ctx,snapshot,rp)
    assert not (snapshot.parent/'look-reservation.json').exists()

@pytest.mark.parametrize('bad',[True,-1,1.5,float('nan')])
def test_malformed_counts_refused(tmp_path,bad):
    path=tmp_path/'synthetic.json';write(path,{'members':{'0':{'n_cosponsored':bad,'overall':'Ally'}}})
    with pytest.raises(oc.Refusal):oc.outcomes(path)

@pytest.mark.parametrize('bad',[True,float('nan'),float('inf'),'1'])
def test_malformed_predictors_refused(tmp_path,bad):
    path=tmp_path/'synthetic.json';write(path,{'members':{'0':{'seed_prob':bad,'pressure_mult':1}}})
    with pytest.raises(oc.Refusal):oc.predictions(path)

def test_input_provenance_is_actual_content(tmp_path,monkeypatch):
    root,ctx,snapshot,rp=prepared(tmp_path)
    (root/'synthetic-source.txt').write_text('PLANTED SOURCE DRIFT\n')
    monkeypatch.setattr(oc,'predictions',lambda *a:pytest.fail('Source drift paired'))
    with pytest.raises(oc.Refusal,match='drifted input'):run_score(root,ctx,snapshot,rp)

@pytest.mark.parametrize('mutation',['top-level-outcome','row-outcome','seed-below-zero','seed-above-one',
                                      'negative-pressure','empty-members','missing-field'])
def test_predictor_schema_and_physical_domains(tmp_path,mutation):
    path=tmp_path/'synthetic.json'
    value={'members':{'0':{'seed_prob':0.5,'pressure_mult':1.0}}}
    row=value['members']['0']
    if mutation=='top-level-outcome':value['outcomes']={'PLANTED':'not allowed'}
    if mutation=='row-outcome':row['n_cosponsored']=5
    if mutation=='seed-below-zero':row['seed_prob']=-0.001
    if mutation=='seed-above-one':row['seed_prob']=1.001
    if mutation=='negative-pressure':row['pressure_mult']=-0.001
    if mutation=='empty-members':value['members']={}
    if mutation=='missing-field':del row['pressure_mult']
    write(path,value)
    with pytest.raises(oc.Refusal):oc.predictions(path)

def test_predictor_domain_boundaries_are_valid(tmp_path):
    path=tmp_path/'synthetic.json'
    value={'members':{'0':{'seed_prob':0,'pressure_mult':0},'1':{'seed_prob':1,'pressure_mult':2}}}
    write(path,value)
    assert oc.predictions(path)==value['members']

def test_git_commands_disable_automatic_gc(tmp_path,monkeypatch):
    from types import SimpleNamespace
    calls=[]
    def observe(argv,**kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=b'SYNTHETIC')
    monkeypatch.setattr(oc.subprocess,'run',observe)
    assert oc.git(tmp_path,'show','--format=%cI','HEAD')==b'SYNTHETIC'
    assert calls==[(['git','-c','gc.auto=0','show','--format=%cI','HEAD'],
                    {'cwd':tmp_path,'capture_output':True,'check':False})]
