"""Apply a complete, current OpenFEC generation before downstream calculations."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
from datetime import datetime,timezone
from paths import ROOT, stored_name, to_host
from moneyball_core import dump_atomic
from collect.refresh_fec_money import digest


def apply(root):
    root=Path(root);path=root/'data/money/fec_refresh_current.json'
    if not path.exists():
        raise ValueError('No verified current FEC refresh; frozen money is not self-updating')
    d=json.loads(path.read_text(encoding='utf-8'))
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(d['checked_at'])).total_seconds()
    if d.get('status')!='PASS' or not -300<=age<=8*86400: raise ValueError('FEC refresh is failed/stale')
    for dependency,wanted in d['dependencies'].items():
        if stored_name(dependency)!='congestion.json' and digest(to_host(dependency))!=wanted:
            raise ValueError('FEC policy/identity mapping changed since collection')
    for source in d['sources']:
        if digest(to_host(source['path']))!=source['sha256']: raise ValueError('FEC evidence hash mismatch')
    output=d['output'];candidate=Path(to_host(output['path']))
    if digest(candidate)!=output['sha256']: raise ValueError('FEC money projection hash mismatch')
    data=json.loads(candidate.read_text(encoding='utf-8'))
    target=root/'data/engine/congestion.json';old=json.loads(target.read_text(encoding='utf-8'))
    if [(r['member'],r['bioguide']) for r in data]!=[(r['member'],r['bioguide']) for r in old]:
        raise ValueError('FEC generation has a different roster')
    # Retain every unrelated field changed since collection. Only the measured
    # money fields and their documented ratios belong to this producer.
    fields=('cu','banks','finserv','receipts','total','saturation','cu_pct','banks_pct','finserv_pct',
            'cu_adj','banks_adj','finserv_adj','industry_adj','net_lean','money_source_status')
    result=[{**before,**{k:after[k] for k in fields}} for before,after in zip(old,data)]
    from engine.money_manifest_pin import prepare_update, finish_update
    pin = prepare_update(root, digest(target))
    if result != old:
        dump_atomic(result,str(target))
    finish_update(pin, digest(target))
    dump_atomic({'schema':'fec-money-application-v1','status':'PASS','cycle':d['cycle'],
                 'source_receipt_sha256':digest(path),'congestion_sha256':digest(target),
                 'unresolved_members':d['unresolved_members']},str(root/'data/money/fec_money_application.json'))
    return len(result)


if __name__=='__main__': print('FEC money applied to',apply(ROOT),'members')
