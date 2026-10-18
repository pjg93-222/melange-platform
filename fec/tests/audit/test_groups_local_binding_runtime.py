"""Actual audit AST block, temporary synthetic files only; never import full audit."""
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import pytest

spec=importlib.util.spec_from_file_location('audit_block_fixture',Path(__file__).with_name('test_reconcile42_full_audit_blocks.py'))
harness=importlib.util.module_from_spec(spec);spec.loader.exec_module(harness)

PRODUCER="""
LDA_GENERATION='generation-20260830T231200Z'
LDA_DATA_ROOT=os.path.join(LOCAL_MELANGE,'staffer_network','normalized','lda-source-native')
LDA_RECEIPT='lda_source_native_corpus_receipt.json'
LDA_LOBBYISTS='lda_lobbyist_assertions.ndjson.gz'
LDA_ORGANISATIONS='lda_organization_assertions.ndjson.gz'
LDA_ACTIVITIES='lda_activity_assertions.ndjson.gz'
LDA_EDGES='lda_industry_edges.ndjson.gz'
raise RuntimeError('must not import producer')
"""

@pytest.mark.parametrize('case',['valid','hash-mismatch','size-mismatch','unknown-role','wrong-generation',
    'path-traversal','missing-producer','malformed-producer','missing-constant','unset-root','not-recomputed'])
def test_exact_machine_local_binding_and_fail_closed_cases(tmp_path,case):
    engine=tmp_path/'data/engine';engine.mkdir(parents=True)
    producer=tmp_path/'fec/surfaces/build_staffer_groups_section.py';producer.parent.mkdir(parents=True)
    if case!='missing-producer':
        source=PRODUCER
        if case=='malformed-producer':source='invalid python!!!'
        if case=='missing-constant':source=source.replace("LDA_RECEIPT='lda_source_native_corpus_receipt.json'",'')
        producer.write_text(source)
    local=tmp_path/'local';generation='generation-20260830T231200Z';filename='lda_activity_assertions.ndjson.gz'
    obj=local/'staffer_network/normalized/lda-source-native'/generation/filename
    obj.parent.mkdir(parents=True);obj.write_bytes(b'fixture')
    role='lda_activity_assertions'
    record={'store':'machine-local','path':generation+'/'+filename,'bytes':7,'sha256':hashlib.sha256(b'fixture').hexdigest()}
    if case=='hash-mismatch':record['sha256']='f'*64
    if case=='size-mismatch':record['bytes']=999
    if case=='unknown-role':role='unapproved'
    if case=='wrong-generation':record['path']=record['path'].replace('20260830','20260831')
    if case=='path-traversal':record['path']='../outside'
    if case=='not-recomputed':record['not_recomputed']='producer did not hash'
    for name in ('offices','organisations','clients'):
        payload={'meta':{'generation':'current','generation_root':'generation','inputs':{role:record},
            'counts':{'client_organisations_dropped':0,'filer_client_ties_dropped':0}}}
        (engine/f'staffer_{name}_section.json').write_text(json.dumps(payload))
    calls=[]
    def digest(path):calls.append(path);return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    verdicts,notices,_=harness.execute(harness.siblings('_groups_specs','_groups_embed_why'),
        {'B':str(tmp_path),'LOCAL_MELANGE':None if case=='unset-root' else str(local),
         '_staff_gen_declared':'current','_file_sha256':digest})
    result=next(row for row in verdicts if row[0]=='staffer_groups_inputs_current')
    assert bool(result[1]) is (case=='valid')
    if case in ('valid','hash-mismatch','size-mismatch'):assert len(calls)==3
    else:assert calls==[]
    if case=='not-recomputed':assert any('unverified' in str(row) for row in notices)

def test_resolved_escape_is_refused_without_symlink_privilege(tmp_path):
    functions=[n for n in harness.ast.walk(harness.TREE) if isinstance(n,harness.ast.FunctionDef) and n.name=='_groups_resolve_local_input']
    _,_,ns=harness.execute(functions,{})
    local=tmp_path/'local';base=local/'staffer_network/normalized/lda-source-native';base.mkdir(parents=True)
    target=base/'generation-20260830T231200Z/lda_activity_assertions.ndjson.gz'
    outside=tmp_path/'outside';outside.write_bytes(b'fixture')
    contract={'generation':'generation-20260830T231200Z','rootParts':('staffer_network','normalized','lda-source-native'),
              'names':{'lda_activity_assertions':target.name}}
    record={'store':'machine-local','path':contract['generation']+'/'+target.name,'bytes':7,'sha256':'a'*64}
    original=Path.resolve
    def resolved(path,*args,**kwargs):
        return outside if path==target else original(path,*args,**kwargs)
    with patch.object(Path,'resolve',resolved),pytest.raises(ValueError):
        ns['_groups_resolve_local_input'](record,'lda_activity_assertions',str(local),contract)
