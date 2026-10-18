"""Synthetic filesystem observations only; producer/full audit are never imported."""
import ast
import fnmatch
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

AUDIT=Path(__file__).resolve().parents[2]/"audit"

def functions(filename, names, extra=None):
    tree=ast.parse((AUDIT/filename).read_text(encoding="utf-8-sig"))
    selected=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in names]
    assert {node.name for node in selected}==set(names)
    ns={"os":os,"fnmatch":fnmatch,"hashlib":hashlib,
        "_TOP_KEEP":24,"HASH_ALWAYS_MAX_FILES":200}
    ns.update(extra or {})
    exec(compile(ast.Module(selected,type_ignores=[]),filename,"exec"),ns)
    return ns

@pytest.mark.parametrize("which",["checker","producer"])
@pytest.mark.parametrize("failure",["directory","entry"])
def test_failed_walk_is_incomplete(tmp_path,which,failure):
    filename="system_manifest_check.py" if which=="checker" else "build_system_manifest.py"
    name="_observe" if which=="checker" else "walk_store"
    ns=functions(filename,[name,"_excluded"])
    if failure=="directory":
        fake=patch.object(os,"scandir",side_effect=PermissionError("synthetic"))
    else:
        class Entry:
            name="entry";path=str(tmp_path/"entry")
            def is_dir(self,**kw):return False
            def stat(self,**kw):raise PermissionError("synthetic")
        class Scan:
            def __enter__(self):return iter([Entry()])
            def __exit__(self,*args):pass
        fake=patch.object(os,"scandir",return_value=Scan())
    with fake:
        result=(ns[name](str(tmp_path),[],20) if which=="checker" else
                ns[name](str(tmp_path),[],20,{"used":0,"total":100}))
    assert result["complete" if which=="checker" else "walk_complete"] is False
    assert result["unreadable_entries"]==1

@pytest.mark.parametrize("which",["checker","producer"])
def test_readable_walk_remains_complete(tmp_path,which):
    (tmp_path/"data.txt").write_text("fixture")
    filename="system_manifest_check.py" if which=="checker" else "build_system_manifest.py"
    name="_observe" if which=="checker" else "walk_store"
    ns=functions(filename,[name,"_excluded"])
    result=(ns[name](str(tmp_path),[],20) if which=="checker" else
            ns[name](str(tmp_path),[],20,{"used":0,"total":100}))
    assert result["complete" if which=="checker" else "walk_complete"] is True
    assert result["file_count"]==1

@pytest.mark.parametrize("secret",[True,False])
def test_secret_sentinel_cannot_reach_hash_reader(tmp_path,secret):
    p=tmp_path/"synthetic.txt";p.write_bytes(b"fixture")
    calls=[];verdicts=[]
    ns=functions("system_manifest_check.py",["_check_sentinels"],{
        "_git_bookkeeping":lambda *a:False,
        "_sha256":lambda path:calls.append(path) or "expected"})
    store={"path":str(p),"class":"SECRET" if secret else "SOURCE",
        "measured":{"kind":"file"},"sentinels":[{"rel":p.name,"bytes":7,"sha256":"expected"}]}
    ns["_check_sentinels"](store,"fixture","prior",lambda *a:verdicts.append(a),lambda *a:None)
    assert bool(calls) is (not secret)
    assert verdicts and verdicts[-1][1] is (not secret)

def test_incomplete_observation_cannot_pass_scale_checks():
    tree=ast.parse((AUDIT/"system_manifest_check.py").read_text(encoding="utf-8-sig"))
    blocks=[node for node in ast.walk(tree) if isinstance(node,ast.If)
        and ast.unparse(node.test)=="not m.get('walk_complete', True)"]
    assert len(blocks)==2
    block=min(blocks,key=lambda n:n.lineno)
    verdicts=[]
    ns={"m":{"walk_complete":True,"file_count":0,"size_class":"empty","bytes":0},
        "s":{},"sid":"fixture","gen_utc":"prior","path":"unused","limit":20,
        "git_dir":False,"note":None,"cls":"SOURCE","COUNT_TOLERANCE_FLOOR":5,
        "COUNT_TOLERANCE_PCT":{"SOURCE":.05},"_size_class":lambda n:"empty",
        "_observe":lambda *a:{"complete":False,"unreadable_entries":1,"bytes":0,"file_count":0},
        "_check_sentinels":lambda *a:None,"skip":lambda *a:None,"ck":lambda *a:verdicts.append(a)}
    exec(compile(ast.Module([block],type_ignores=[]),"synthetic-scale-block","exec"),ns)
    assert any(not row[1] for row in verdicts)
    assert not any(row[1] for row in verdicts)
