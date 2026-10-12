import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from members.reference_cache import key, legacy_key, bind_legacy, validate


def row(suffix):
    return {'a':'Alice Example','b':'Bob Sample','w':'x'*200+suffix}


def test_changed_suffix_cannot_inherit_frozen_legacy_label(tmp_path):
    first=row(' I support this proposal.');changed=row(' I oppose this proposal.')
    assert legacy_key(first)==legacy_key(changed)
    assert key(first)!=key(changed)
    legacy={legacy_key(first):{'cls':'positive','reason':'support'}}
    path=tmp_path/'baseline.json'
    assert bind_legacy(legacy,[first],path)==set()
    assert key(first) in legacy
    assert bind_legacy(legacy,[changed],path)==set()
    assert key(changed) not in legacy


def test_ambiguous_legacy_contexts_are_queued_until_exactly_classified(tmp_path):
    first=row(' support');second=row(' oppose')
    cache={legacy_key(first):{'cls':'positive','reason':'legacy'}}
    path=tmp_path/'baseline.json'
    assert bind_legacy(cache,[first,second],path)=={key(first),key(second)}
    fresh={legacy_key(first):cache[legacy_key(first)],key(second):{'cls':'negative','reason':'oppose'}}
    assert bind_legacy(fresh,[first,second],path)=={key(first)}
    assert fresh[key(second)]['cls']=='negative'


@pytest.mark.parametrize('value',[{'cls':'unknown','reason':'x'}, {'cls':'positive'}, {'cls':'positive','reason':4}])
def test_invalid_labels_rejected(value):
    with pytest.raises(ValueError): validate(value)


def test_bounded_refresh_resumes_to_completion_without_losing_prior_evidence(tmp_path):
    import json, os, sqlite3, subprocess
    (tmp_path/'data/engine').mkdir(parents=True)
    (tmp_path/'data/members').mkdir(parents=True)
    (tmp_path/'data/engine/congestion.json').write_text(json.dumps([
        {'member':'Alice Example','bioguide':'TEST01','chamber':'House'},
        {'member':'Bob Sample','bioguide':'TEST02','chamber':'Senate'}]))
    (tmp_path/'data/engine/sentinel_admission_policy.json').write_text(json.dumps({
        'reference_transport':'claude-cli','reference_model':'test-only','reference_batch_limit':1}))
    with sqlite3.connect(tmp_path/'social_media.db') as db:
        db.execute('CREATE TABLE speeches(member,date,source,text)')
        db.execute('CREATE TABLE members(bioguide,twitter)')
        for i in range(3):
            db.execute('INSERT INTO speeches VALUES (?,?,?,?)',('Alice Example',f'2026-09-0{i+1}',
                'TEST',f'I support Senator Sample and proposal number {i}.'))
    script=Path(__file__).resolve().parents[2]/'members/member_reference_layer.py'
    code="""import runpy,sys,types
fake=types.ModuleType('classifier_runtime')
fake.classify_json=lambda *args: ({'cls':'positive','reason':'synthetic support'},{'transport':'test-double'})
sys.modules['classifier_runtime']=fake
sys.argv=[sys.argv[1],'--score','--go']
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    for deferred in (2,1,0):
        result=subprocess.run([sys.executable,'-c',code,str(script)],capture_output=True,text=True,
            env={**os.environ,'MONEYBALL_ROOT':str(tmp_path),'MONEYBALL_DRAFT':str(tmp_path/'missing-draft')},timeout=45)
        assert result.returncode==0,result.stdout+result.stderr
        receipt=json.loads((tmp_path/'data/members/member_reference_classification_receipt.json').read_text())
        assert receipt['classified']==1 and receipt['deferred_contexts']==deferred
        edges=json.loads((tmp_path/'member_references.json').read_text())['edges']
        assert edges[0]['n_scored']==3-deferred
