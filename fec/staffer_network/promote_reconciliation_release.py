"""Promote an evaluated reconciliation candidate with hash-bound test evidence."""
import argparse
import json
from pathlib import Path

from .reconcile_held_evidence import packed, sha


def run(candidate,evaluation,tests,output):
    candidate,evaluation,tests,output=map(Path,(candidate,evaluation,tests,output))
    if output.exists():raise ValueError('Release paths are immutable; choose a new path')
    release=json.loads(candidate.read_bytes());report=json.loads(evaluation.read_bytes());receipt=json.loads(tests.read_bytes())
    if release.get('status')!='CANDIDATE':raise ValueError('Expected a candidate release')
    if report.get('status')!='PASS' or report.get('candidate_sha256')!=sha(candidate):raise ValueError('Evaluation does not match candidate')
    if receipt.get('status')!='PASS':raise ValueError('Tests did not pass')
    for path,digest in receipt.get('code_sha256',{}).items():
        if sha(path)!=digest:raise ValueError('Tested code changed: '+path)
    if report.get('ledger_sha256')!=release.get('inputs',{}).get('ledger_sha256'):
        raise ValueError('Evaluation ledger does not match candidate')
    release['status']='PASS'
    release['validation']={'candidate_sha256':sha(candidate),'evaluation_path':str(evaluation),
        'evaluation_sha256':sha(evaluation),'test_receipt_path':str(tests),'test_receipt_sha256':sha(tests),
        'code_sha256':receipt.get('code_sha256',{}),'clean_baseline_evaluation':True,
        'integration_corrections':['Readable reconciled-client labels are retained in routes.',
            'Partial career observations remain separate from structured employment history.',
            'Saved routes report both restored endpoints.',
            'Exact-name one-to-one Quorum source records bind to Politico identities; Politico remains the role/date authority.']}
    output.write_text(json.dumps(release,indent=2),encoding='utf8')
    print(packed({'status':'PASS','path':str(output),'sha256':sha(output),'identity_bindings':len(release['identity_bindings'])}),flush=True)
    return release


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True);parser.add_argument('--evaluation',type=Path,required=True)
    parser.add_argument('--tests',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.candidate,args.evaluation,args.tests,args.output)
