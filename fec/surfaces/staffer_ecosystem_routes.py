"""Sparse report-period associations. No employment or personal familiarity inferred."""
from collections import defaultdict
import json
import sqlite3
import time

from .staffer_activity_index import ActivitySearchLimited

CLASSES = {
    0: ('co_reported_lobbying_activity', 'Same issue section in a lobbying filing', 1.0),
    1: ('co_reported_filing', 'Same organization and quarterly filing', 1.5),
    2: ('co_reported_registrant_period', 'Same registrant and reporting period', 2.0),
    3: ('shared_client_period', 'Shared client during the reporting period', 3.0),
}


def period_neighbors(index, person, options, budget):
    source_ids = index.to_source.get(person, [])
    if not options.get('include_review', True):
        return {}
    from .staffer_reconciled_evidence import official_neighbors, filing_allowed
    official = official_neighbors(index,person,options)
    if not source_ids:
        return official
    def tick():
        budget['neighbor_checks'] += 1
        if budget['neighbor_checks'] > options['max_neighbor_checks'] or time.monotonic() > budget['deadline']:
            raise ActivitySearchLimited()
    con = sqlite3.connect(index.database.as_uri() + '?mode=ro&immutable=1', uri=True)
    con.row_factory = sqlite3.Row
    con.set_progress_handler(lambda: int(time.monotonic() > budget['deadline']), 1000)
    periods = defaultdict(dict)
    try:
        placeholders = ','.join('?' for _ in source_ids)
        own = list(con.execute('SELECT * FROM member WHERE person IN (' + placeholders + ')', source_ids))
        # Scan each firm/client quarter once, even for a lobbyist with hundreds
        # of client filings. The old per-filing join repeatedly scanned the same
        # firm quarter and could exhaust the person-view budget.
        scopes = defaultdict(list)
        for left in own:
            if not filing_allowed(index,left):continue
            if getattr(index,'reconciliation_as_of',None) and left['start'] > index.reconciliation_as_of:
                continue  # Retain future-dated source records in the archive, not routes.
            for field in ('firm_id','client_id'):
                value = getattr(index,'client_to_group',{}).get(left[field],left[field]) if field=='client_id' else left[field]
                scopes[field,value,left['start'],left['end_exclusive']].append(left)
        for (field, value, start, end), lefts in scopes.items():
            tick()
            lefts.sort(key=lambda r:(r['filing_uuid'],r['person'],r['firm_id']))
            activities, filings = {}, {}
            own_firms = {r['firm_id'] for r in lefts}
            for left in lefts:
                filings.setdefault(left['filing_uuid'],left)
                for act in json.loads(left['activities']):activities.setdefault(act,left)
            values = getattr(index,'client_group_members',{}).get(value,[value]) if field=='client_id' else [value]
            others = con.execute('SELECT * FROM member WHERE '+field+' IN ('+','.join('?' for _ in values)+') AND start=? AND end_exclusive=?',(*values,start,end))
            for right in others:
                tick()
                if not filing_allowed(index,right):continue
                if field=='client_id' and right['firm_id'] in own_firms:
                    continue  # Already processed through its firm-quarter scope.
                target = index.to_node.get(right['person'])
                if not target or target == person:
                    continue
                right_activities = set(json.loads(right['activities']))
                shared = right_activities & activities.keys()
                left = min((activities[a] for a in shared),key=lambda r:(r['filing_uuid'],r['person'],r['firm_id'])) if shared else filings.get(right['filing_uuid'],lefts[0])
                kind = 0 if shared else 1 if left['filing_uuid']==right['filing_uuid'] else 2 if field=='firm_id' else 3
                key = (start,end)
                rank = (kind,tuple(sorted({left['filing_uuid'],right['filing_uuid']})),tuple(sorted([left['firm_id'],right['firm_id']])),tuple(sorted([left['person'],right['person']])))
                old = periods[target].get(key)
                if old is not None and old[0] <= rank:
                    continue
                left_activities = set(json.loads(left['activities']))
                same_activity = left_activities & right_activities
                proof = {'relationship':CLASSES[kind][0], 'start':key[0], 'end_exclusive':key[1],
                    'filing_uuids':sorted({left['filing_uuid'],right['filing_uuid']}),
                    'source_person_ids':[left['person'],right['person']],
                    'registrant_ids':[left['firm_id'],right['firm_id']],
                    'client_ids':[left['client_id'],right['client_id']],
                    'client_identity_evidence':[getattr(index,'client_identity_proofs',{})[c] for c in sorted({left['client_id'],right['client_id']}) if c in getattr(index,'client_identity_proofs',{})],
                    'source_activity_ids':sorted(same_activity or (left_activities | right_activities)),
                    'source_edge_ids':sorted(set(json.loads(left['edge_ids'])+json.loads(right['edge_ids']))),
                    'posted':[left['posted'],right['posted']]}
                # Choose the strongest evidence once per quarter, deterministically
                # in either traversal direction. Repeated issue pages add no weight.
                periods[target][key] = (rank,proof)
    except sqlite3.OperationalError as exc:
        if 'interrupt' in str(exc).lower():
            raise ActivitySearchLimited() from exc
        raise
    finally:
        con.close()
    result = {}
    for target, evidence in periods.items():
        ordered = [evidence[k] for k in sorted(evidence)]
        strongest = min(rank[0] for rank,proof in ordered)
        proof = next(proof for rank,proof in reversed(ordered) if rank[0]==strongest)
        relation,label,multiplier = CLASSES[strongest]
        client_group = getattr(index,'client_to_group',{}).get(proof['client_ids'][0],proof['client_ids'][0])
        org = ('organization:'+client_group) if strongest==3 else ('organization:'+proof['registrant_ids'][0])
        org_label = (getattr(index,'client_group_labels',{}).get(client_group) if strongest==3 else None) or index.nodes.get(org,{}).get('label',org)
        result[target] = {'source':person,'target':target,'relationship':relation,'relationship_label':label,
            'temporal_status':'reported_activity_period','employer_id':org,'employer_label':org_label,
            'start':proof['start'],'end_exclusive':proof['end_exclusive'],
            'report_period':{'start':proof['start'],'end_exclusive':proof['end_exclusive']},
            'observed_period_count':len(ordered), 'observed_periods':[p for _,p in ordered],
            'period_counts_by_relationship':{CLASSES[k][0]:sum(rank[0]==k for rank,p in ordered) for k in CLASSES},
            'first_observed_period':ordered[0][1]['start'],'last_observed_period_end_exclusive':ordered[-1][1]['end_exclusive'],
            'duration_semantics':'Distinct shared reporting periods; continuous service and daily collaboration unverified',
            'continuous_employment_asserted':False,'personal_familiarity_established':False,'current_evidence':False,
            'source_relation':True,'review_only':True,'confidence':None,
            'confidence_scope':label+'; employment overlap and personal familiarity not established',
            'cost':options['reported_activity_cost']*multiplier,'cost_overlay_only':True,
            'cost_semantics':'Issue 1×, filing 1.5×, registrant-period 2×, client-mediated 3× base report cost; duration shown separately',
            'via_organization':{'id':org,'label':org_label,'role':'shared_client'} if strongest==3 else None,
            'provenance':{'index':str(index.database),'index_sha256':index.receipt['sha256'],
                'source_receipt_sha256':index.receipt['source_receipt_sha256'],
                'version_rule':'Latest uniquely posted registrant/reconciled-client/quarter filing; earlier versions retained in archive',
                'representative_period':proof,
                'identity_overlays':[index.identity_basis[s] for s in sorted(set(index.to_source.get(person,[])+index.to_source.get(target,[]))) if s in index.identity_basis]}}
    for other,hop in official.items():
        if other not in result or hop['cost']<result[other]['cost']:
            result[other]=hop
    return result
