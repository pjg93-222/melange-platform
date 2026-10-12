import json

from fec.staffer_network.promote_reconciliation_release import run
from fec.staffer_network.reconcile_held_evidence import sha


def test_promotion_requires_matching_evaluation_and_tested_code(tmp_path):
    code=tmp_path/'code.py';code.write_text('tested=True\n')
    candidate=tmp_path/'candidate.json';candidate.write_text(json.dumps({'status':'CANDIDATE','identity_bindings':[],
        'inputs':{'ledger_sha256':'ledger'}}))
    evaluation=tmp_path/'evaluation.json';evaluation.write_text(json.dumps({'status':'PASS','candidate_sha256':sha(candidate),'ledger_sha256':'ledger'}))
    tests=tmp_path/'tests.json';tests.write_text(json.dumps({'status':'PASS','code_sha256':{str(code):sha(code)}}))
    output=tmp_path/'release.json';assert run(candidate,evaluation,tests,output)['status']=='PASS'
    assert json.loads(output.read_text())['validation']['clean_baseline_evaluation'] is True
