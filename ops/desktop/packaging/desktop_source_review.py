"""Exact reviewed finding disposition for this local development installation.

This does not change security_gate, grant distribution approval, or convert its
raw FAIL into a PASS. Any other failure, changed file, or changed literal blocks.
"""
import argparse
import hashlib
import json
from pathlib import Path

PATH = 'mentat/tools/pocket/mentat-pocket.html'
FILE_SHA256 = '8af485379840492ded826feb2da4a8b58fb4995e0925f3f592aadf27d5231ce3'
VALUE = 'mentat-pocket-scope-v1'
LINE = 406
DETAIL = 'gitleaks rule generic-api-key line 406'


def disposition(report, source):
    file = Path(source)/PATH
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    literal = file.read_text(encoding='utf-8').splitlines()[LINE-1].strip()
    expected = literal == "var KEY = '" + VALUE + "';" and digest == FILE_SHA256
    checks = report.get('checks',[])
    required = {'exceptions','gitleaks','data_leak','exact_secrets'}
    blocked = []
    if report.get('stage') != 'export' or report.get('ci') is not False:
        blocked.append('Wrong raw gate scope')
    if len(checks)!=4 or {c.get('check') for c in checks}!=required:
        blocked.append('Incomplete raw gate checks')
    reviewed = 0
    for check in checks:
        if check.get('status')=='PASS' and not check.get('findings'):
            continue
        if (expected and check.get('stage')=='export' and check.get('check')=='gitleaks'
                and check.get('status')=='FAIL' and check.get('findings')==[[PATH,DETAIL]]):
            reviewed += 1
        else:
            blocked.append('Unreviewed gate result: '+str(check.get('check')))
    if reviewed!=1: blocked.append('Expected exact finding was not present')
    return {'scope':'local-development-installation-only',
        'result':'BLOCKED' if blocked else 'LOCAL_REVIEW_ACCEPTED',
        'rawGateResult':report.get('result'),'distributionApproved':False,
        'policyReviewPending':True,'blocked':blocked,
        'review':{'path':PATH,'rule':'generic-api-key','line':LINE,'fileSha256':digest,
            'valueSha256':hashlib.sha256(VALUE.encode()).hexdigest(),
            'literalMatches':expected,'rationale':'Fixed browser localStorage namespace, not an authentication credential.'}}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=disposition(json.loads(args.report.read_text()),args.source)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'result':result['result'],'rawGateResult':result['rawGateResult'],'distributionApproved':False}))
    raise SystemExit(1 if result['blocked'] else 0)
