"""The exact unused provenance declaration cannot create a false consumed-input gate."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('audit_block_fixture',Path(__file__).with_name('test_reconcile42_full_audit_blocks.py'))
harness=importlib.util.module_from_spec(spec);spec.loader.exec_module(harness)

@pytest.mark.parametrize('case',['valid','missing-reads','malformed-reads','empty-reads','blank-read','consumed-role',
    'unknown-role','wrong-file','wrong-store','wrong-marker','blank-marker','wrong-bindings','bad-hash','bad-size','missing-hash'])
def test_known_unused_edges_are_not_verified_or_a_required_read(tmp_path,case):
    engine=tmp_path/'data/engine';engine.mkdir(parents=True)
    consumed=tmp_path/'input.json';consumed.write_bytes(b'fixture')
    consumed_record={'store':'repository','path':'input.json','bytes':7,'sha256':hashlib.sha256(b'fixture').hexdigest()}
    role='staffer_social_edges';reads=['fixture']
    record={'store':'repository','path':'staffer_social_edges.ndjson.gz','bytes':123,'sha256':'a'*64,
            'bound_against':[],'not_recomputed':'producer does not consume these bytes'}
    if case=='missing-reads':reads=None
    if case=='malformed-reads':reads={'fixture':True}
    if case=='empty-reads':reads=[]
    if case=='blank-read':reads=[' ']
    if case=='consumed-role':reads.append(role)
    if case=='unknown-role':role='unknown_provenance'
    if case=='wrong-file':record['path']='arbitrary.ndjson.gz'
    if case=='wrong-store':record['store']='machine-local'
    if case=='wrong-marker':record['not_recomputed']=True
    if case=='blank-marker':record['not_recomputed']=' '
    if case=='wrong-bindings':record['bound_against']=['verified']
    if case=='bad-hash':record['sha256']='invalid'
    if case=='bad-size':record['bytes']=True
    if case=='missing-hash':record.pop('sha256')
    for name in ('offices','organisations','clients'):
        meta={'generation':'current','generation_root':'generation','inputs':{'fixture':consumed_record,role:record},
              'counts':{'client_organisations_dropped':0,'filer_client_ties_dropped':0}}
        if case!='missing-reads':meta['reads']=reads
        (engine/f'staffer_{name}_section.json').write_text(json.dumps({'meta':meta}))
    calls=[]
    def digest(path):calls.append(Path(path));return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    verdicts,notices,ns=harness.execute(harness.siblings('_groups_specs','_groups_embed_why'),
        {'B':str(tmp_path),'_staff_gen_declared':'current','_file_sha256':digest})
    current=next(row for row in verdicts if row[0]=='staffer_groups_inputs_current')
    assert bool(current[1]) is (case=='valid')
    assert calls==[consumed]*3
    assert ns['_groups_inputs_checked']==3  # Provenance never inflates verified count.
    assert len(ns['_groups_inputs_not_consumed'])==(3 if case=='valid' else 0)
    assert any('NOT_APPLICABLE' in str(row) for row in notices) is (case=='valid')
