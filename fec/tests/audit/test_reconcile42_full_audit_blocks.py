"""Execute selected audit AST statements against fixtures, never import audit_full."""
import ast
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest

SOURCE=Path(__file__).resolve().parents[2]/'audit/audit_full.py'
TREE=ast.parse(SOURCE.read_text(encoding='utf-8-sig'))

def assign(node,name):
    return isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)

def siblings(start,stop):
    for parent in ast.walk(TREE):
        for _,values in ast.iter_fields(parent):
            if not isinstance(values,list):continue
            for i,node in enumerate(values):
                if isinstance(node,ast.AST) and assign(node,start):
                    for j in range(i+1,len(values)):
                        if isinstance(values[j],ast.AST) and assign(values[j],stop):return values[i:j]
    raise AssertionError((start,stop))

def check(label):
    return [n for n in ast.walk(TREE) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
        and isinstance(n.value.func,ast.Name) and n.value.func.id=='ck'
        and n.value.args and isinstance(n.value.args[0],ast.Constant) and n.value.args[0].value==label]

def execute(nodes,extra):
    verdicts=[];notices=[]
    ns=dict(os=os,json=json,csv=csv,re=re,Path=Path,sys=sys,
        ck=lambda *a:verdicts.append(a),print=lambda *a,**k:notices.append(a))
    ns.update(extra)
    exec(compile(ast.Module(nodes,type_ignores=[]),'isolated-audit-block','exec'),ns)
    return verdicts,notices,ns

@pytest.mark.parametrize('code,valid',[(0,True),(1,False)])
def test_child_exit_status_cannot_be_hidden_by_success_stdout(code,valid):
    fn=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='run')
    fake=SimpleNamespace(run=lambda *a,**k:SimpleNamespace(stdout='22 passed, 0 FAILED',returncode=code))
    _,_,ns=execute([fn],{'subprocess':fake,'FE':'unused'})
    output=ns['run']('audit/fixture.py')
    match=re.search(r'(\d+) passed, (\d+) FAILED',output)
    accepted=bool(match) and match.group(2)=='0' and int(match.group(1))>=22
    assert accepted is valid

@pytest.mark.parametrize('destination',[{}, {'general_audio_habits':{'clips':1}}])
def test_audio_destination_cannot_drop_available_layers(destination):
    block=next(n for n in ast.walk(TREE) if isinstance(n,ast.If)
        and ast.unparse(n.test)=="isinstance(_albun, dict) and _albun.get('members')")
    source={'general_audio_habits':{'clips':1},'verified_voiceprint_baseline':{'x':1}}
    verdicts,_,_=execute([block],{'_albun':{'members':[{'name':'fixture','speech_profile':destination}]},
        '_alrows':{'fixture':{'speech_profile':source}}})
    assert verdicts and verdicts[0][1] is False

def test_complete_audio_destination_passes():
    block=next(n for n in ast.walk(TREE) if isinstance(n,ast.If)
        and ast.unparse(n.test)=="isinstance(_albun, dict) and _albun.get('members')")
    profile={'general_audio_habits':{'clips':1}}
    verdicts,_,_=execute([block],{'_albun':{'members':[{'name':'fixture','speech_profile':profile}]},
        '_alrows':{'fixture':{'speech_profile':profile}}})
    assert verdicts[0][1] is True

@pytest.mark.parametrize('mode,expected',[('factor',False),('per_area_v1',False),('legxfactor',True)])
def test_existing_evidence_floor_applies_to_both_gated_modes(mode,expected):
    block=siblings('_subfloor','_engine_tier') if False else None
    assignment=next(n for n in ast.walk(TREE) if assign(n,'_subfloor'))
    statement=check('no tiered member sits below the evidence floor')[0]
    tiers={f'm{i}':1 for i in range(85)};credit={m:1 for m in tiers};credit['m0']=.5
    verdicts,_,_=execute([assignment,statement],{'_tier':tiers,'_tu_board':mode,'_tu_floor':.75,'_cuc':credit})
    assert verdicts[0][1] is expected

@pytest.mark.parametrize('document,expected',[(None,False),({'overall_board':{'top13':['fixture']}},True)])
def test_missing_honors_is_not_reported_as_loaded_authority(document,expected):
    statement=check('active honors generation loads as the crown authority')[0]
    verdicts,_,_=execute([statement],{'_honors_doc':document,'_honors_err':''})
    assert verdicts[0][1] is expected

@pytest.mark.parametrize('board,expected',[(None,False),(set(str(i) for i in range(13)),True)])
def test_fallback_crown_is_not_mislabeled_as_active_board(board,expected):
    statement=check('overall Champion crown exactly equals the active honors board top 13')[0]
    seats=set(str(i) for i in range(13))
    verdicts,_,_=execute([statement],{'_champ':seats,'_crown_seats':seats,'_board13':board})
    assert verdicts[0][1] is expected

@pytest.mark.parametrize('generation,expected',[('old',False),('current',True)])
def test_staffer_network_generation_matches_producer(generation,expected):
    statement=next(n for n in check('staffer_section_present') if not isinstance(n.value.args[1],ast.Constant))
    verdicts,_,_=execute([statement],{'_staff_meta':{'generation':generation},'_staff_gen_declared':'current',
        '_staff':{'members':{'fixture':{}},'global':{'fixture':1}}})
    assert verdicts[0][1] is expected

def test_filename_tautology_is_not_counted_as_verified_content():
    checks=check('network artifact families are disjoint at the live root')
    assert checks==[]

@pytest.mark.parametrize('present',[False,True])
def test_press_read_failure_is_recorded_without_aborting_other_checks(tmp_path,present):
    tone=tmp_path/'data/stance/press_refresh_tone.csv';mentions=tmp_path/'data/press/official_mentions.jsonl'
    tone.parent.mkdir(parents=True);mentions.parent.mkdir(parents=True)
    if present:tone.write_text('value\nfixture\n');mentions.write_text('{}\n')
    nodes=siblings('_press_tone_path','_email_manifest_path')
    verdicts,_,_=execute(nodes,{'B':str(tmp_path),'_press_feed':{'schema':'press-refresh-engine-feed-v1','status':'ACTIVE_TYPED_SUPPLEMENT'},
        '_press_outputs_ok':True,'_press_source_errors':[], '_press_counts':{'tone_rows':1,'official_mentions':1},
        '_press_contract':{'scored_host_articles_enter_press_stance':True,'official_mentions_enter_press_stance':False,'official_mentions_enter_speech_turns':False}})
    target=next(row for row in verdicts if row[0]=='press refresh feed is hash-bound and keeps official mentions out of stance/speech')
    assert target[1] is present

@pytest.mark.parametrize('changed',[False,True])
def test_group_repository_input_hashes_are_current(tmp_path,changed):
    engine=tmp_path/'data/engine';engine.mkdir(parents=True)
    data=tmp_path/'input.json';data.write_bytes(b'original')
    sha=hashlib.sha256(data.read_bytes()).hexdigest()
    for name in ('offices','organisations','clients'):
        payload={'meta':{'generation':'current','generation_root':'fixture','inputs':{'fixture':{'path':'input.json','store':'repository','sha256':sha}},
            'counts':{'client_organisations_dropped':0,'filer_client_ties_dropped':0}}}
        (engine/f'staffer_{name}_section.json').write_text(json.dumps(payload))
    if changed:data.write_bytes(b'changed')
    nodes=siblings('_groups_specs','_groups_embed_why')
    verdicts,_,_=execute(nodes,{'B':str(tmp_path),'_staff_gen_declared':'current',
        '_staff_resolve_input':lambda rec,*a:str(tmp_path/rec['path']),
        '_file_sha256':lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()})
    matching=[r for r in verdicts if r[0]=='staffer_groups_repository_inputs_current']
    assert matching and matching[0][1] is (not changed)


@pytest.mark.parametrize('case',['machine-local','traversal','absent','malformed','wrong-size','generation-relative'])
def test_group_binding_boundaries_are_not_false_passes(tmp_path,case):
    engine=tmp_path/'data/engine';engine.mkdir(parents=True)
    data=tmp_path/'generation/input.json';data.parent.mkdir();data.write_bytes(b'fixture')
    rec={'path':'input.json','store':'repository','sha256':hashlib.sha256(b'fixture').hexdigest(),'bytes':7}
    if case=='machine-local':rec['store']='machine-local'
    elif case=='traversal':rec['path']='../outside'
    elif case=='absent':rec['path']='absent.json'
    elif case=='malformed':rec['sha256']=None
    elif case=='wrong-size':rec['bytes']=999
    for name in ('offices','organisations','clients'):
        # No v6 groups role is required: older v5 uses its other declared bindings.
        payload={'meta':{'generation':'current','generation_root':'generation','inputs':{'fixture':rec},
            'counts':{'client_organisations_dropped':0,'filer_client_ties_dropped':0}}}
        (engine/f'staffer_{name}_section.json').write_text(json.dumps(payload))
    calls=[]
    def digest(path):calls.append(path);return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    verdicts,notices,_=execute(siblings('_groups_specs','_groups_embed_why'),{
        'B':str(tmp_path),'_staff_gen_declared':'current','_file_sha256':digest})
    matches=[r for r in verdicts if r[0]=='staffer_groups_inputs_current']
    assert matches and matches[0][1] is (case=='generation-relative')
    if case in ('machine-local','traversal','malformed'):assert calls==[]
    if case=='machine-local':assert any('unverified' in str(n) for n in notices)
