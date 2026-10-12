"""Offline trial of original giving; never changes production reporting rules.

Only apply relationships supported by the supplied FEC evidence. All other
included amounts remain provisional. This is not a complete unique-dollar total.
"""
from collections import defaultdict
from decimal import Decimal
import re

def text(value):
    return ' '.join(str(value or '').upper().split())

def cents(value):
    return int((Decimal(str(value))*100).quantize(Decimal('1')))

def memo(row):
    return row.get('memo_code')=='X' or row.get('memoed_subtotal') is True

def validate_evidence(row, evidence):
    fields={'sub_id':'sub_id','committee_id':'committee_id','contributor_name':'contributor_name',
            'transaction_id':'transaction_id','file_num':'file_number'}
    for stored,remote in fields.items():
        if text(row.get(stored))!=text(evidence.get(remote)):
            raise ValueError(f'FEC identity conflict: {row["sub_id"]}, {stored}')
    if cents(row['contribution_receipt_amount'])!=cents(evidence['contribution_receipt_amount']):
        raise ValueError('FEC amount conflict')
    if row['contribution_receipt_date'][:10]!=evidence['contribution_receipt_date'][:10]:
        raise ValueError('FEC date conflict')

def trial(rows, evidence, headers):
    if len({r['sub_id'] for r in rows})!=len(rows):
        raise ValueError('Duplicate source IDs in trial input')
    rows={r['sub_id']:dict(r) for r in rows}
    for sid,e in evidence.items():
        if sid in rows:validate_evidence(rows[sid],e)
    counted={sid:int(Decimal(r['current_receipt_cents'])) for sid,r in rows.items()}
    initial=dict(counted);changes=[];review=[];pac_activity=[];routes=[]
    def exclude(sid,reason,retained=None,proof=None):
        amount=counted[sid]
        if not amount:return
        counted[sid]=0
        changes.append(dict(source_id=sid,industry=rows[sid]['industry'],company=rows[sid]['baseline_company'],
                            cycle=int(rows[sid]['cycle']),delta_cents=-amount,reason=reason,
                            retained_source_id=retained,evidence=proof))
    # Amendment families are established by the filing header's original-report
    # reference, not amount similarity or globally repeated transaction IDs.
    families=defaultdict(list)
    for sid,r in rows.items():
        if not counted[sid] or sid not in evidence:continue
        e=evidence[sid];h=headers.get(str(e['file_number']))
        if not h or h.get('relationship_evidence') is False or memo(e) or not e.get('transaction_id'):continue
        if h['committee_id']!=r['committee_id'] or h['filing_form']!=e['filing_form']:
            raise ValueError('Filing identity conflict')
        root=h.get('original_file_num') or h['file_num']
        order=0 if not h.get('original_file_num') else h.get('amendment_number')
        if not str(order).isdigit():continue
        key=(r['industry'],r['baseline_company'],r['committee_id'],root,e['transaction_id'])
        families[key].append((sid,int(order)))
    for group in families.values():
        if len(group)<2:continue
        latest=max(order for _,order in group)
        winners=[sid for sid,order in group if order==latest]
        if len(winners)!=1:
            review.append({'reason':'ambiguous_amendment_order','source_ids':[sid for sid,_ in group]});continue
        winner=winners[0]
        for sid,order in group:
            if sid!=winner:
                exclude(sid,'superseded_in_observed_filing_family',winner,
                        {'original_file_num':headers[str(evidence[winner]['file_number'])]['original_file_num'],
                         'transaction_id':evidence[winner]['transaction_id'],
                         'before_file':evidence[sid]['file_number'],'after_file':evidence[winner]['file_number']})
    # Recipient-reported PAC giving belongs to the separate PAC activity view.
    # This is not a claim that Schedule B outgoing collection is complete.
    for sid,e in evidence.items():
        if sid not in counted or not counted[sid]:continue
        contributor=e.get('contributor') or {}
        if e.get('entity_type') in ('PAC','COM','CCM','PTY') and re.fullmatch(r'C\d{8}',str(e.get('contributor_id') or '')):
            if contributor.get('committee_id')!=e['contributor_id']:
                review.append({'reason':'unverified_pac_identity','source_ids':[sid]});continue
            pac_activity.append({'source_id':sid,'pac_id':e['contributor_id'],'recipient_id':e['committee_id'],
                                 'amount_cents':counted[sid],'company':rows[sid]['baseline_company'],
                                 'industry':rows[sid]['industry'],'cycle':int(rows[sid]['cycle'])})
            exclude(sid,'move_to_separate_pac_activity',proof={'contributor_id':e['contributor_id'],'source_url':e['pdf_url']})
    # Candidate matching is deliberately broader than the evidence used to
    # count a route once. Two reciprocal FEC earmark descriptions are required.
    # Same amount/date/name alone never generates an exclusion.
    index=defaultdict(list)
    for sid,r in rows.items():
        if counted[sid]>0:
            index[(r['industry'],r['baseline_company'],text(r['contributor_name']),r['contribution_receipt_date'][:10],counted[sid])].append(sid)
    proposals=[]
    for origin,oe in evidence.items():
        if origin not in counted or counted[origin]<=0 or memo(oe):continue
        desc=text(str(oe.get('receipt_type_full') or '')+' '+str(oe.get('memo_text') or ''))
        if 'EARMARKED FOR' not in desc:continue
        recipient_ids=re.findall(r'\bC\d{8}\b',desc)
        if len(set(recipient_ids))!=1:continue
        r=rows[origin];key=(r['industry'],r['baseline_company'],text(r['contributor_name']),r['contribution_receipt_date'][:10],counted[origin])
        possible=[sid for sid in index[key] if rows[sid]['committee_id']==recipient_ids[0] and sid!=origin]
        if len(possible)!=1:
            if possible:review.append({'reason':'ambiguous_routing_candidates','source_ids':[origin,*possible]})
            continue
        destination=possible[0];de=evidence.get(destination)
        if not de or memo(de):
            review.append({'reason':'destination_evidence_incomplete','source_ids':[origin,destination]});continue
        zip_origin=re.sub(r'\D','',str(oe.get('contributor_zip') or ''))[:5]
        zip_dest=re.sub(r'\D','',str(de.get('contributor_zip') or ''))[:5]
        name=text((oe.get('committee') or {}).get('name'))
        dest_desc=text(str(de.get('receipt_type_full') or '')+' '+str(de.get('memo_text') or ''))
        route_pattern=r'\bEARMARKED(?: \(NON-DIRECTED\))? (?:THROUGH|FROM) '+re.escape(name)+r'\b'
        if not name or len(zip_origin)!=5 or zip_origin!=zip_dest or not re.search(route_pattern,dest_desc):
            review.append({'reason':'reciprocal_route_not_established','source_ids':[origin,destination]});continue
        proposals.append((origin,destination))
    usage=defaultdict(int)
    for pair in proposals:
        for sid in pair:usage[sid]+=1
    for origin,destination in proposals:
        if usage[origin]!=1 or usage[destination]!=1:
            review.append({'reason':'overlapping_route_links','source_ids':[origin,destination]});continue
        proof={'origin_url':evidence[origin]['pdf_url'],'destination_url':evidence[destination]['pdf_url'],
               'basis':'reciprocal_FEC_earmark_descriptions_and_unique_donor_date_amount_zip_match'}
        routes.append({'origin_id':origin,'destination_id':destination,'amount_cents':counted[destination],**proof})
        exclude(destination,'same_original_gift_at_conduit_and_destination',origin,proof)
    assert sum(initial.values())+sum(c['delta_cents'] for c in changes)==sum(counted.values())
    return {'status':'experimental_partial_evidence','initial_cents':sum(initial.values()),
            'trial_cents':sum(counted.values()),'counted_cents':counted,'changes':changes,
            'pac_activity':pac_activity,'routes':routes,'review':review,
            'scope':'stored receipts; observed amendment families; only supplied FEC evidence; no latest-filing completeness claim'}
