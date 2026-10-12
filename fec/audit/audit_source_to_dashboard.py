"""Reconcile automatically admitted evidence with the published measurements."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json,hashlib
from datetime import datetime,timezone
from paths import ROOT
from legislation import sentinel_admissions as SA
import bill_rules as BR
from engine.money_observation import industry_money_missing
from moneyball_core import dump_atomic


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(root,check_alias=True):
    root=Path(root);errors=[];effects=[];inputs={}
    try:
        if not (root/SA.RELATIVE).is_file(): raise ValueError('Automatic bill admission refresh has not run')
        admissions=SA.load(root)
        desk=read(root/'member_desk_bundle.json')
        by_name={m['name']:m for m in desk['members']}
        routing=read(root/'legislation_routing_audit.json')
        selected={(r.get('member'),r.get('bill'),BR._congress(r.get('congress')),r.get('effective_category')):r
                  for r in routing['rows'] if r['disposition']=='selected'}
        for row in admissions['memberships']:
            if row.get('withdrawn'): continue
            displayed=[b for b in by_name.get(row['member'],{}).get('bills',[])
                       if b['bill']==row['bill'] and str(b['congress'])==str(row['congress'])]
            if len(displayed)!=1 or BR.canonical_role(displayed[0]['role'])!=BR.canonical_role(row['role']):
                errors.append('Admitted membership is missing/duplicated/wrong-role in desk: '+row['member']+' '+row['bill'])
            decision=admissions['decisions'][row['repository_key']]
            for category in decision['categories']:
                key=(row['member'],row['bill'],BR._congress(row['congress']),category['category'])
                actual=selected.get(key)
                expected=category['direction']*BR.role_weight(BR.canonical_role(row['role']))*BR.congress_decay(row['congress'])
                # Explicit neutral decisions are routed with a zero contribution.
                if (actual is None and expected!=0) or (actual is not None and abs(actual['contribution']-expected)>1e-9):
                    errors.append('Admitted evidence did not reach category measurement: '+str(key))
                effects.append({'member':row['member'],'bill':row['bill'],'role':row['role'],
                                'category':category['category'],'direction':category['direction'],
                                'contribution':actual['contribution'] if actual else 0})
        money=read(root/'data/money/fec_refresh_current.json')
        application=read(root/'data/money/fec_money_application.json')
        if application['source_receipt_sha256']!=sha(root/'data/money/fec_refresh_current.json'):
            errors.append('FEC engine application references another source generation')
        if application['congestion_sha256']!=sha(root/'data/engine/congestion.json'):
            errors.append('FEC application does not match current engine inputs')
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(money['checked_at'])).total_seconds()
        if money['status']!='PASS' or not -300<=age<=8*86400: errors.append('FEC source generation is not current')
        congestion=read(root/'data/engine/congestion.json')
        if set(by_name)!={row['member'] for row in congestion}:
            errors.append('Member desk roster does not match the current money measurement roster')
        dashboard=read(root/'dashboard_data.json')
        rank_authority=read(root/'ranks_python.json')
        ranks=rank_authority['rank']
        policy_path=root/'data/engine/sentinel_admission_policy.json'
        if policy_path.is_file() and read(policy_path).get('live_legislative_pillar') is True:
            live=read(root/'data/engine/live_legislative_pillar.json')
            if rank_authority.get('legislative_pillar')!=live:
                errors.append('Headline rank did not consume the current live legislative pillar')
            for path,digest in live['inputs_sha256'].items():
                if sha(root/path)!=digest:errors.append('Live legislative pillar source has changed: '+path)
            from engine.live_legislative_pillar import percentranks
            legislative_percentiles=percentranks(live['pillar'])
            if any(abs(rank_authority['pcts'][name]['leg']-value)>1e-9 for name,value in legislative_percentiles.items()):
                errors.append('Current legislative measurement did not reach headline score percentiles')
        displayed_ranks=dict(zip(dashboard['name'],dashboard['rank'],strict=True))
        if displayed_ranks!=ranks:
            errors.append('Dashboard ranks do not match the current scoring authority')
        money_by_name={row['member']:row for row in congestion}
        reported=sorted(row['receipts'] for row in congestion if (row.get('receipts') or 0)>0)
        median=reported[len(reported)//2] if reported else 200000
        capacity_imputations=[]
        industry_values=sorted((r.get('banks') or 0)+(r.get('finserv') or 0) for r in congestion
                               if (r.get('banks') or 0)+(r.get('finserv') or 0)>0)
        industry_median=industry_values[len(industry_values)//2] if industry_values else 0
        industry_imputations=[]
        for i,name in enumerate(dashboard['name']):
            source=money_by_name.get(name)
            raw_receipts=(source or {}).get('receipts') or 0
            capacity=max(raw_receipts,200000) if raw_receipts>0 else median
            if raw_receipts==0: capacity_imputations.append(name)
            if source is None or dashboard['engine']['cu'][i]!=round(source['cu']) or dashboard['engine']['rcp'][i]!=round(capacity):
                errors.append('FEC money did not reach the interactive engine: '+name)
            if source is not None:
                missing=industry_money_missing(source)
                expected_industry=industry_median if missing else (source.get('banks') or 0)+(source.get('finserv') or 0)
                if missing: industry_imputations.append(name)
                if dashboard['engine']['ind'][i]!=round(expected_industry):
                    errors.append('Observed or explicitly imputed competing money did not reach the interactive engine: '+name)
        for member in congestion:
            shown=by_name.get(member['member'])
            if shown is not None and shown.get('invested')!=member['cu']:
                errors.append('FEC money did not reach member desk: '+member['member'])
            if shown is not None and shown.get('ind_money')!=round((member.get('banks') or 0)+(member.get('finserv') or 0)):
                errors.append('Observed competing-sector money did not reach member desk: '+member['member'])
        if check_alias and (root/'Moneyball_Redesign.html').exists() and sha(root/'Moneyball_Redesign.html')!=sha(root/'Moneyball.html'):
            # The clean workspace shell stands in for the copy only while its receipt binds it to this generation.
            from surfaces.sync_live_dashboard_alias import clean_workspace_current
            if not clean_workspace_current(root):errors.append('The dashboard served by the local preview is stale')
        for relative in ('member_desk_bundle.json','dashboard_data.json','ranks_python.json','archive_leg.json',
                         'legislation_routing_audit.json','data/money/fec_refresh_current.json','data/engine/congestion.json'):
            inputs[relative]=sha(root/relative)
        for relative in ('Moneyball.html','Moneyball_Redesign.html'):
            if (root/relative).exists():inputs[relative]=sha(root/relative)
        if (root/'data/engine/live_legislative_pillar.json').exists():
            inputs['data/engine/live_legislative_pillar.json']=sha(root/'data/engine/live_legislative_pillar.json')
        layers=None
        if rank_authority.get('score'):
            from audit.audit_measurement_layers import verify as verify_layers
            layers=verify_layers(root)
            errors.extend(layers['errors'])
            inputs.update(layers['inputs_sha256'])
        return {'schema':'source-to-dashboard-receipt-v1','status':'FAIL' if errors else 'PASS',
                'checked_at':datetime.now(timezone.utc).isoformat(),'errors':errors,'bill_effects':effects,
                'measurement_layers':layers,
                'verified_scope':['automatic bill membership and category contribution','FEC source to member desk and interactive engine','dashboard reporting ranks']
                    + (['legislative and headline arithmetic','primacy authority across all CU boards','adjudicated labels and engine priority'] if layers else []),
                'document_reference_classification':read(root/'data/members/member_reference_classification_receipt.json')
                    if (root/'data/members/member_reference_classification_receipt.json').is_file() else {'status':'NOT_RUN'},
                'admission_decisions':{k:{f:v.get(f) for f in ('disposition','rationale','confidence')}
                                       for k,v in admissions['decisions'].items()},
                'headline_legislative_effects':(rank_authority.get('legislative_pillar') or {}).get('automatic_bill_effects',[]),
                'money_cycle':money['cycle'],'money_source_date':money['latest_attributed_transaction_date'],
                'money_unresolved_members':money['unresolved_members'],
                'money_capacity_imputed_members':capacity_imputations,
                'money_industry_imputed_members':industry_imputations,
                'money_capacity_rule':'Existing engine: receipts floor $200,000; absent receipts use current positive-receipts median. These are modeled capacities, not reported receipts.',
                'inputs_sha256':inputs}
    except Exception as exc:
        return {'schema':'source-to-dashboard-receipt-v1','status':'FAIL',
                'checked_at':datetime.now(timezone.utc).isoformat(),'errors':[str(exc)],'bill_effects':effects}


if __name__=='__main__':
    result=verify(ROOT)
    dump_atomic(result,str(Path(ROOT)/'data/engine/source_to_dashboard_receipt.json'))
    print(json.dumps(result,indent=2));raise SystemExit(0 if result['status']=='PASS' else 1)
