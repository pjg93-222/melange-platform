"""Current official membership facts, separate from immutable admission history.

An explicitly withdrawn cosponsor ceases earning credit. A missing name is not
treated as a withdrawal. Sponsors and role corrections require exact Bioguide
identity in the hash-bound official repository, never a name approximation.
"""
from pathlib import Path
import json
from datetime import datetime, timezone
from legislation.congress_bill_repository import sha256_file
from legislation.verify_collector_roster_updates import build_ledger_view, desk_summary
import bill_rules as BR
import chain_transaction as CT

RELATIVE = 'data/legislation/official_membership_movements.json'
# Self-healing phase 2 (Paul 2026-10-15): past this window the sidecar is served as last-good and labelled STALE_LABELLED
# (d['freshness_status'], a stdout line, data/engine/source_freshness.json) instead of raising. The repository-sha binding and the
# row-by-row re-derivation from the source snapshot below are unchanged and still refuse.
WINDOW_DAYS = 8


def enabled(root):
    path=Path(root)/'data/engine/sentinel_admission_policy.json'
    return path.is_file() and json.loads(path.read_text(encoding='utf-8')).get('automatic_movements') is True


def build(bundle,repository,receipt,repository_sha256,overrides):
    view=build_ledger_view(bundle); facts=[]; additions=[]; unresolved=[]
    for key,bill in sorted(repository['bills'].items()):
        acts=view['repo_key_to_acts'].get(key,set())
        if len(acts)!=1: continue
        act=next(iter(acts)); seed=view['seeds'][key]
        override=BR.override_for(overrides,*act)
        if BR.excluded_bill(*act,seed.get('title'),seed.get('summary')) or (override and not BR.override_included(override)):
            continue
        people=[('sponsor',bill.get('sponsor') or {})]+[('original',p) for p in bill.get('original_cosponsors',[])]+[('cosponsor',p) for p in bill.get('cosponsors',[])]
        seen=set()
        for role,person in people:
            bio=person.get('bioguide') or ''
            name=view['roster'].get(bio)
            if not name:
                unresolved.append({'repository_key':key,'bioguide':bio,'role':role});continue
            if bio in seen: raise ValueError('official roster contains duplicate person: '+key+' '+bio)
            seen.add(bio)
            row={'schema':'collector-roster-update-v1','repository_key':key,
                 'bill':act[0],'congress':act[1],'member':name,'bioguide':bio,'role':role,
                 'join_date':person.get('date') or bill.get('introduced_date',''),
                 'withdrawn':bool(person.get('withdrawn_date')),'withdrawn_date':person.get('withdrawn_date') or '',
                 'areas':list(view['areas_by_act'][act]),
                 **{f:str(seed.get(f) or '') for f in ('title','sponsor','summary','action')},
                 'ncos':bill.get('cosponsor_count',0),'url':bill.get('url',''),
                 'source':'verified official BILLSTATUS roster','verified_at':receipt['completed_at'],
                 'repository_sha256':repository_sha256}
            facts.append(row)
            old=view['members_by_act'].get(act,{}).get(name)
            if not row['withdrawn'] and old!=role:
                if desk_summary(row['summary'])!=row['summary']:
                    raise ValueError('official movement would alter seeded summary: '+key)
                additions.append(row)
    return {'schema':'official-membership-movements-v1','status':'PASS',
            'checked_at':receipt['completed_at'],'repository_sha256':repository_sha256,
            'facts':facts,'additions':additions,'unresolved':unresolved}


def load(root):
    path=Path(root)/RELATIVE
    if not path.exists(): return {'facts':[],'additions':[]}
    d=json.loads(path.read_text(encoding='utf-8'))
    if d.get('schema')!='official-membership-movements-v1' or d.get('status')!='PASS':
        raise ValueError('official membership movements are not a valid PASS')
    if d['repository_sha256']!=sha256_file(Path(root)/'bill_repository_live.json'):
        raise ValueError('official membership movements refer to a different repository')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(d['checked_at'].replace('Z','+00:00'))).total_seconds()
    freshness=CT.age_status(age,WINDOW_DAYS,'official membership facts are stale')   # a future stamp still raises
    keys=[(r['repository_key'],r['bioguide']) for r in d['facts']]
    if len(keys)!=len(set(keys)): raise ValueError('duplicate official membership identity')
    # Re-derive the projection from the source snapshot, rather than trusting
    # a self-asserted receipt on a mutable sidecar.
    repo=json.loads((Path(root)/'bill_repository_live.json').read_text(encoding='utf-8'))
    for row in d['facts']:
        b=repo['bills'][row['repository_key']]
        people=([b['sponsor']] if row['role']=='sponsor' else
                b['original_cosponsors'] if row['role']=='original' else b['cosponsors'] if row['role']=='cosponsor' else [])
        p=next((p for p in people if p.get('bioguide')==row['bioguide']),None)
        if p is None or bool(p.get('withdrawn_date'))!=row['withdrawn']:
            raise ValueError('movement differs from the official source')
    if freshness==CT.STALE_LABELLED:
        d['freshness_status']=freshness   # in-memory label only; never part of the stored sidecar
    CT.note_freshness(root,'official_membership_movements',freshness,checked_at=d['checked_at'],window_days=WINDOW_DAYS,
                      producer='fec/legislation/official_membership_movements.py')
    return d


def index(document):
    return {(r['repository_key'],r['bioguide']):r for r in document.get('facts',[])}


def eligible(row,facts):
    fact=facts.get((row['repository_key'],row['bioguide']))
    return fact is None or (not fact['withdrawn'] and BR.canonical_role(row['role'])==BR.canonical_role(fact['role']))


def apply_to_desk(bills,document):
    from legislation.congress_bill_repository import parse_bill_identity
    by_name={(r['repository_key'],r['member']):r for r in document.get('facts',[])}
    removed=0
    for name,rows in bills.items():
        kept=[]
        for row in rows:
            try: key=parse_bill_identity(row['bill'],row['congress'])['key']
            except ValueError: kept.append(row);continue
            fact=by_name.get((key,name))
            if fact and (fact['withdrawn'] or BR.canonical_role(row['role'])!=BR.canonical_role(fact['role'])):
                removed+=1
            else: kept.append(row)
        bills[name]=kept
    return removed
