import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from audit.system_manifest_check import _packed_object_verified
from engine.money_manifest_pin import prepare_update, finish_update


def test_packed_git_object_verified_and_missing_object_rejected(tmp_path):
    subprocess.run(['git', 'init', '--bare', str(tmp_path)], check=True, capture_output=True)
    oid = subprocess.run(['git', '--git-dir='+str(tmp_path), 'hash-object', '-w', '--stdin'],
                         input=b'preserved evidence', capture_output=True, check=True).stdout.decode().strip()
    subprocess.run(['git', '--git-dir='+str(tmp_path), 'update-ref', 'refs/evidence/test', oid], check=True)
    subprocess.run(['git', '--git-dir='+str(tmp_path), 'repack', '-a', '-d'], check=True, capture_output=True)
    subprocess.run(['git', '--git-dir='+str(tmp_path), 'prune-packed'], check=True)
    rel = 'objects/'+oid[:2]+'/'+oid[2:]
    assert not (tmp_path/rel).exists()
    assert _packed_object_verified(tmp_path, rel)
    assert not _packed_object_verified(tmp_path, 'objects/00/'+'0'*38)
    assert not _packed_object_verified(tmp_path, 'config')


def test_money_pin_cannot_bless_unexplained_or_concurrent_changes(tmp_path):
    path=tmp_path/'data/system/system_manifest.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'stores':[{'id':'repo.congestion','integrity_policy':'hash_always',
        'hashes':{'congestion.json':'old'}}], 'unrelated':'preserve'}))
    with pytest.raises(ValueError, match='outside'):
        prepare_update(tmp_path, 'unexplained')
    prepared=prepare_update(tmp_path, 'old');finish_update(prepared,'new')
    result=json.loads(path.read_text())
    assert result['stores'][0]['hashes']['congestion.json']=='new'
    assert result['unrelated']=='preserve'
    prepared=prepare_update(tmp_path,'new');path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError, match='changed'):
        finish_update(prepared,'next')


def test_scheduled_chain_binds_final_ranks_before_publication():
    import ast
    path=Path(__file__).resolve().parents[2]/'run_pipeline.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    chain=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
               and any(isinstance(t,ast.Name) and t.id=='CHAIN' for t in node.targets))
    names=[name for name,args in chain]
    assert names.index('compute_pillars.py') < names.index('legislation_culling_status.py') < names.index('build_dashboard_bundle.py')
    assert names.index('make_combined.py') < names.index('sync_live_dashboard_alias.py') < names.index('audit_source_to_dashboard.py')
    assert names[-1]=='audit_full.py'
