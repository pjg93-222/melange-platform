"""Apply a complete, current OpenFEC generation before downstream calculations."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
from datetime import datetime,timezone
from paths import ROOT, stored_name, to_host
from moneyball_core import dump_atomic
from collect.refresh_fec_money import digest
import chain_transaction as CT

# Self-healing phase 2 (Paul 2026-10-15): past this window the last verified generation is still APPLIED and labelled
# STALE_LABELLED (receipt + data/engine/source_freshness.json) instead of raising. Everything that proves the generation is
# the one the receipt describes -- status PASS, the dependency / source / projection hashes, the roster -- stays strict.
WINDOW_DAYS=8
# The measured money fields and their documented ratios: the only fields this producer owns.
FIELDS=('cu','banks','finserv','receipts','total','saturation','cu_pct','banks_pct','finserv_pct',
        'cu_adj','banks_adj','finserv_adj','industry_adj','net_lean','money_source_status')
MAPPING_CHANGED='FEC policy/identity mapping changed since collection'
ROSTER_CHANGED='FEC generation has a different roster'


def _mapping_drift(root,current,wanted):
    """fec/legislators.csv changed since collection. Allowed only as refresh_roster.py's drift -- candidate ids ADDED (Paul's
    per-change OK) and departed members' ids removed -- proven against the collected bytes, found by hash in the roster lane's
    backups. The added ids' money is not in this generation; any other change still refuses."""
    import roster
    from collect.refresh_fec_money import candidate_map
    collected=next((p for p in sorted(root.glob('backups/roster*/**/legislators.csv')) if digest(p)==wanted),None)
    if collected is None: raise ValueError(MAPPING_CHANGED)
    before,after=candidate_map(collected),candidate_map(current)
    gone={d['bioguide'] for d in roster.departed(root)}
    if any((after.get(c,b) if b in gone else after.get(c))!=b for c,b in before.items()): raise ValueError(MAPPING_CHANGED)
    added=sorted(set(after)-set(before))
    print('  !! FEC money: identity mapping drift; %d candidate ids added since collection carry no money in this generation (%s)'
          %(len(added),', '.join(added)))
    return {'collected_mapping_sha256':wanted,'candidate_ids_added_since':added}


def _roster_drift(root,old,data):
    """-> (withheld, dropped): roster arrivals the generation predates keep their zero-money rows; departed members it still
    carries are dropped. Any other roster difference, a changed bioguide, or an arrival row carrying money still refuses."""
    import roster
    try: withheld,dropped,bad_missing,bad_extra=roster.drift([r['member'] for r in old],[r['member'] for r in data],root)
    except (OSError,ValueError,KeyError): raise ValueError(ROSTER_CHANGED)
    pairs={(r['member'],r['bioguide']) for r in old}
    if (bad_missing or bad_extra or any((r['member'],r['bioguide']) not in pairs for r in data if r['member'] not in dropped)
            or any(r.get(k) for r in old if r['member'] in withheld for k in FIELDS[:-1])):
        raise ValueError(ROSTER_CHANGED)
    if [(r['member'],r['bioguide']) for r in data if r['member'] not in dropped]!=[(r['member'],r['bioguide']) for r in old if r['member'] not in withheld]:
        raise ValueError(ROSTER_CHANGED)
    withheld,dropped=sorted(withheld),sorted(dropped)
    if withheld or dropped:
        print('  !! FEC money: roster drift; %d WITHHELD (%s), %d DROPPED (%s)'
              %(len(withheld),', '.join(withheld),len(dropped),', '.join(dropped)))
    return withheld,dropped


def _generation_before_roster_refresh(root,old,withheld,dropped):
    """sha256 of the generation bytes congestion.json held before a roster refresh rewrote it (old minus the withheld arrivals ==
    that generation minus the departed members), else None. Lets the money pin advance across a roster refresh and nothing else."""
    want=[r for r in old if r['member'] not in withheld]
    for p in sorted((root/'data/money/fec_generations').glob('*/congestion.json')):
        if [r for r in json.loads(p.read_text(encoding='utf-8')) if r['member'] not in dropped]==want: return digest(p)
    return None


def apply(root):
    root=Path(root);path=root/'data/money/fec_refresh_current.json'
    if not path.exists():
        raise ValueError('No verified current FEC refresh; frozen money is not self-updating')
    d=json.loads(path.read_text(encoding='utf-8'))
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(d['checked_at'])).total_seconds()
    if d.get('status')!='PASS': raise ValueError('FEC refresh is failed/stale')
    freshness=CT.age_status(age,WINDOW_DAYS,'FEC refresh is failed/stale')   # a future stamp still raises
    mapping=None
    for dependency,wanted in d['dependencies'].items():
        name=stored_name(dependency)
        if name!='congestion.json' and digest(to_host(dependency))!=wanted:
            if name!='legislators.csv': raise ValueError(MAPPING_CHANGED)
            mapping=_mapping_drift(root,to_host(dependency),wanted)
    for source in d['sources']:
        if digest(to_host(source['path']))!=source['sha256']: raise ValueError('FEC evidence hash mismatch')
    output=d['output'];candidate=Path(to_host(output['path']))
    if digest(candidate)!=output['sha256']: raise ValueError('FEC money projection hash mismatch')
    data=json.loads(candidate.read_text(encoding='utf-8'))
    target=root/'data/engine/congestion.json';old=json.loads(target.read_text(encoding='utf-8'))
    before_sha=digest(target)
    withheld=dropped=[]
    if [(r['member'],r['bioguide']) for r in data]!=[(r['member'],r['bioguide']) for r in old]:
        withheld,dropped=_roster_drift(root,old,data)
    # Retain every unrelated field changed since collection; a withheld arrival keeps its whole row.
    by_member={r['member']:r for r in data}
    result=[{**before,**{k:by_member[before['member']][k] for k in FIELDS}} if before['member'] in by_member else before
            for before in old]
    from engine.money_manifest_pin import prepare_update, finish_update
    try: pin=prepare_update(root,digest(target))
    except ValueError:
        lane=_generation_before_roster_refresh(root,old,withheld,dropped) if (withheld or dropped) else None
        if lane is None: raise
        pin=prepare_update(root,lane)            # still refuses unless the pin names exactly those bytes
    if result != old:
        dump_atomic(result,str(target))
    finish_update(pin, digest(target))
    receipt={'schema':'fec-money-application-v1','status':'PASS','cycle':d['cycle'],
             'source_receipt_sha256':digest(path),'congestion_sha256':digest(target),
             'unresolved_members':d['unresolved_members']}
    if withheld or dropped: receipt['roster_drift']={'withheld':withheld,'dropped':dropped,'congestion_before_sha256':before_sha}
    if mapping: receipt['identity_mapping_drift']=mapping
    if freshness==CT.STALE_LABELLED:   # absent key == FRESH, so the in-window receipt bytes are unchanged
        receipt.update(freshness_status=freshness,source_checked_at=d['checked_at'],freshness_window_days=WINDOW_DAYS)
    dump_atomic(receipt,str(root/'data/money/fec_money_application.json'))
    CT.note_freshness(root,'fec_money',freshness,checked_at=d['checked_at'],window_days=WINDOW_DAYS,
                      producer='fec/engine/apply_fec_money_refresh.py')
    return len(result)


if __name__=='__main__': print('FEC money applied to',apply(ROOT),'members')
