"""AST-lifted, temporary-only controls; no full audit, collector or model import."""
import ast
from collections import defaultdict
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET
import pytest
from fec.lane_file_io import atomic_write_bytes,atomic_write_text,open_ndjson

FEC=Path(__file__).resolve().parents[2]

def lift(label,names,extra=None):
    tree=ast.parse((FEC/label).read_text(encoding="utf-8-sig"))
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    assert {n.name for n in nodes}==set(names)
    ns=dict(ast=ast,os=os,Path=Path,json=json,hashlib=hashlib,csv=csv,io=io,defaultdict=defaultdict,
            open_ndjson=open_ndjson,atomic_write_bytes=atomic_write_bytes,atomic_write_text=atomic_write_text)
    ns.update(extra or {})
    exec(compile(ast.fix_missing_locations(ast.Module([ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0),*nodes],type_ignores=[])),label,"exec"),ns)
    return ns

@pytest.mark.parametrize("payload",["[]","null","1","true"])
def test_nonobject_ndjson_is_not_silently_dropped(tmp_path,payload):
    ns=lift("audit/audit_legfix5_coverage_reconciliation.py",["read_ndjson","ReconciliationError"])
    p=tmp_path/"control.ndjson";p.write_text('{"key":"good"}\n'+payload+'\n')
    with pytest.raises(ns["ReconciliationError"],match="object"):
        list(ns["read_ndjson"](p))

def test_object_ndjson_and_blank_lines_stay_valid(tmp_path):
    ns=lift("audit/audit_legfix5_coverage_reconciliation.py",["read_ndjson","ReconciliationError"])
    p=tmp_path/"control.ndjson";p.write_text('\n{"key":"good"}\n')
    assert list(ns["read_ndjson"](p))==[{"key":"good"}]

@pytest.mark.parametrize("expression",["Path('a').parents[99]","Path('a').with_suffix('bad')","Path('a').with_name('bad/name')"])
def test_invalid_static_path_operations_are_unresolved(expression,tmp_path):
    ns=lift("audit/audit_output_consumption.py",["_values","_combine","_call_name"])
    assert ns["_values"](ast.parse(expression,mode="eval").body,{},tmp_path/"source.py")==set()

def test_valid_static_path_stays_resolved(tmp_path):
    ns=lift("audit/audit_output_consumption.py",["_values","_combine","_call_name"])
    assert ns["_values"](ast.parse("Path('a.txt').with_suffix('.json')",mode="eval").body,{},tmp_path/"s.py")=={'a.json'}

@pytest.mark.parametrize("value,label",[(True,"YES"),(False,"NO"),(None,"UNKNOWN")])
def test_cutover_report_distinguishes_unknown(value,label):
    ns=lift("audit/audit_legislation_decay_propagation.py",["markdown_report"])
    summary=defaultdict(int,production_cutover_detected=value)
    report=ns["markdown_report"]({"status":"fixture","summary":summary,"downstream_consumer_ledger":[]})
    assert 'Production cutover detected: **'+label+'**' in report

@pytest.mark.parametrize("index",["-1","-2","2"])
def test_invalid_shared_string_index_is_rejected(index):
    ns=lift("audit/audit_postrefresh_workbook_ooxml_semantics.py",["_cell_value","WorkbookSemanticAuditError","require"],
        {"ET":ET,"MAIN_NS":"fixture"})
    cell=ET.fromstring('<c xmlns="fixture" r="A1" t="s"><v>'+index+'</v></c>')
    with pytest.raises(ns["WorkbookSemanticAuditError"],match="index"):
        ns["_cell_value"](cell,["first","last"])

def test_valid_shared_string_index_is_preserved():
    ns=lift("audit/audit_postrefresh_workbook_ooxml_semantics.py",["_cell_value","WorkbookSemanticAuditError","require"],
        {"ET":ET,"MAIN_NS":"fixture"})
    cell=ET.fromstring('<c xmlns="fixture" r="A1" t="s"><v>0</v></c>')
    assert ns["_cell_value"](cell,["first","last"])["value"]=="first"

def test_import_receipt_hashes_the_bytes_actually_parsed(tmp_path):
    packet=tmp_path/"packet";out=tmp_path/"out"
    (packet/"press").mkdir(parents=True);(packet/"live_augment").mkdir()
    item=packet/"press/statement_items.csv"
    original=b'id,member,bioguide,date,channel,url,direction,intensity_scaled,text\n1,Fixture,F001,2000-01-01,fixture,https://example.invalid,1,1,original\n'
    item.write_bytes(original)
    (packet/"press/statement_classes_joined.csv").write_text('id,model_class,effective_class\n1,CU_DIRECT,CU_DIRECT\n')
    (packet/"live_augment/press_by_member_amended_check.csv").write_text('member,xp,factor_num_delta,factor_den_delta,n_items\nFixture,all,0.1,0.1,1\n')
    ns=lift("tools/import_primacy_press.py",["import_packet"],
        {"CLASS_W":{"CU_DIRECT":1},"LEDGER":"ledger.ndjson","RECEIPT":"receipt.json"})
    original_open=Path.open;changed=False
    @contextmanager
    def racing_open(path,*args,**kwargs):
        nonlocal changed
        with original_open(path,*args,**kwargs) as stream:
            yield stream
        mode=args[0] if args else kwargs.get("mode","r")
        if path==item and "r" in mode and not changed:
            changed=True
            with original_open(item,"wb") as stream:stream.write(original.replace(b"original",b"changed!"))
    with patch.object(Path,"open",racing_open):
        receipt=ns["import_packet"](packet,out)
    source=next(row for row in receipt["sources"] if row["path"]=="press/statement_items.csv")
    assert source["sha256"]==hashlib.sha256(original).hexdigest()
    assert json.loads((out/"ledger.ndjson").read_text())["text_sha256"]==hashlib.sha256(b"original").hexdigest()

def report_namespace():
    return lift("audit/audit_candidate_membership_review_partition.py",["write_report","sha256_file"])

def test_report_cannot_overwrite_its_bound_input(tmp_path):
    source=tmp_path/"input.ndjson";source.write_bytes(b"original evidence")
    ns=report_namespace()
    with pytest.raises(ValueError,match="input"):
        ns["write_report"]({"source_bindings":{"ledger_file":str(source)}},source)
    assert source.read_bytes()==b"original evidence"

def test_report_preserves_previous_bytes_on_staging_failure(tmp_path):
    out=tmp_path/"report.json";out.write_bytes(b"previous")
    ns=report_namespace()
    with patch.object(os,"fsync",side_effect=OSError("synthetic staging failure")):
        with pytest.raises(OSError):ns["write_report"]({"fixture":1},out)
    assert out.read_bytes()==b"previous"

def test_report_sidecar_matches_completed_output(tmp_path):
    out=tmp_path/"report.json";ns=report_namespace()
    digest=ns["write_report"]({"fixture":1},out)
    assert digest==hashlib.sha256(out.read_bytes()).hexdigest()
    assert out.with_suffix('.json.sha256').read_text()==digest+'  report.json\n'

def test_rank_pointer_preserves_previous_bytes_on_staging_failure(tmp_path):
    tree=ast.parse((FEC/'audit/audit_postrefresh_rank_drift.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_audit')
    start=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pointer_path' for t in n.targets))
    ns={'out_root':tmp_path,'pointer':{'fixture':1},'pretty_bytes':lambda v:json.dumps(v).encode(),
        'atomic_write_bytes':atomic_write_bytes}
    p=tmp_path/'current.json';p.write_bytes(b'previous')
    with patch.object(os,'fsync',side_effect=OSError('synthetic staging failure')):
        with pytest.raises(OSError):exec(compile(ast.Module(fn.body[start:start+2],type_ignores=[]),'pointer-block','exec'),ns)
    assert p.read_bytes()==b'previous'

@pytest.mark.parametrize("same",["workbook","receipt"])
def test_recalculation_requires_distinct_inputs(tmp_path,same):
    class ReachedRead(Exception):pass
    def read(*a):raise ReachedRead()
    ns=lift("audit/audit_formula_cache_freshness.py",["build_audit","require","FormulaCacheAuditError"],{'file_item':read})
    a=tmp_path/'a.xlsx';b=a if same=='workbook' else tmp_path/'b.xlsx'
    ra=tmp_path/'a.json';rb=ra if same=='receipt' else tmp_path/'b.json'
    with pytest.raises(ns['FormulaCacheAuditError'],match='distinct'):
        ns['build_audit'](source_path=tmp_path/'source.xlsx',calculated_a_path=a,calculated_b_path=b,
            receipt_a_path=ra,receipt_b_path=rb,out_root=tmp_path/'out',expected_source_sha256='0'*64,temp_root=tmp_path/'temp')
