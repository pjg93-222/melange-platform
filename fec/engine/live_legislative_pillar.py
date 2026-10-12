"""Recompute the legislative rank pillar from accepted evidence, without Excel caches.

Preserves the workbook's agenda/efficiency blend and topic priorities. Direction
comes from the accepted per-bill routing decision, once, rather than from a stale
member stance label. Efficiency measures signed advancement among sponsored and
originally cosponsored bills; letters and unknown outcomes are not successes.
"""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import math
import re

from legislation.congress_bill_repository import parse_bill_identity

ADVANCED={'Passed House','Passed Senate','Agreed to in Senate','Resolving Differences',
          'Became Law','Failed to pass over veto'}


def percentranks(values):
    ordered=sorted(values.values())
    first={}
    for i,value in enumerate(ordered):first.setdefault(value,i)
    return {name:math.floor(first[value]/max(1,len(ordered)-1)*1e5)/1e5
            for name,value in values.items()}


def advancement(tracker,latest):
    """Conservative tri-state advancement; procedural votes are not bill passage."""
    if tracker in ADVANCED:return True
    text=str(latest or '').casefold()
    if re.search(r'became (?:public|private) law|signed by president|presented to president|'
                 r'received in (?:the )?(?:senate|house)|passed (?:the )?(?:house|senate)|'
                 r'on passage[,: ].*\bpassed\b|resolving differences',text):
        return True
    if re.search(r'motion to suspend the rules and pass.*agreed to',text):return True
    if tracker in {'Introduced','Referred to Committee'} or re.search(r'\bintroduced\b|referred to (?:the )?.*committee',text):
        return False
    return None


def measure(members,rows,bills,topic_weights,efficiency_weights,blend):
    if len(members)!=len(set(members)):raise ValueError('Duplicate pillar roster member')
    if len(blend)!=2 or any(not math.isfinite(x) or x<0 for x in blend) or abs(sum(blend)-1)>1e-9:
        raise ValueError('Invalid agenda/efficiency blend')
    agenda=dict.fromkeys(members,0.0); groups=defaultdict(list); effects=[]; unknown=[]; seen=set()
    for row in rows:
        if row.get('disposition')!='selected':continue
        name=row['member'];category=row['effective_category']
        if name not in agenda:raise ValueError('Routing member is outside pillar roster: '+name)
        if category not in topic_weights:raise ValueError('Unweighted legislative category: '+category)
        identity=(name,str(row.get('congress')),row['bill'],category)
        if identity in seen:raise ValueError('Duplicate selected legislative evidence: '+str(identity))
        seen.add(identity)
        signed=float(row['contribution']);weight=abs(float(topic_weights[category]))
        agenda[name]+=signed*weight
        if row.get('direction_mechanism')=='automated-official-evidence-policy':
            effects.append({'member':name,'bill':row['bill'],'category':category,
                            'category_contribution':signed,'agenda_contribution':signed*weight})
        if row.get('effective_role') not in {'sponsor','original','original-cosponsor'} or not row.get('sign'):continue
        try:key=parse_bill_identity(row['bill'],row['congress'])['key']
        except (ValueError,KeyError):continue  # non-bill evidence has no legislative success outcome
        source=bills.get(key,{})
        state=advancement(row.get('tracker_status'),(source.get('latest_action') or {}).get('text'))
        if state is None:
            unknown.append({'member':name,'bill':row['bill'],'congress':row['congress']});continue
        groups[(name,category)].append((float(row['decay_weight']),int(row['sign']),state))
    rates={}
    efficiency=dict.fromkeys(members,0.0)
    for category,weight in efficiency_weights.items():
        values={}
        for name in members:
            observations=groups[(name,category)]
            denominator=sum(d for d,sign,state in observations)
            values[name]=sum(d*sign for d,sign,state in observations if state)/denominator if denominator else 0.0
        scale=max([abs(x) for x in values.values()]+[0.0]) or 1.0
        for name in members:efficiency[name]+=abs(weight)*values[name]/scale
        rates[category]=values
    agenda_rank=percentranks(agenda);efficiency_rank=percentranks(efficiency)
    pillar={name:blend[0]*agenda_rank[name]+blend[1]*efficiency_rank[name] for name in members}
    return {'agenda':agenda,'efficiency':efficiency,'pillar':pillar,'agenda_percentile':agenda_rank,
            'efficiency_percentile':efficiency_rank,'advancement_rates':rates,'automatic_bill_effects':effects,
            'unknown_advancement':unknown,'selected_evidence':len(seen)}


def recompute(root,workbook,members,topic_weights):
    import bill_rules as BR
    root=Path(root)
    paths=['legislation_routing_audit.json','bill_repository_live.json','archive_leg.json',
           'data/engine/sentinel_admission_policy.json']
    inputs={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
    rows=json.loads((root/paths[0]).read_text(encoding='utf-8'))['rows']
    bills=json.loads((root/paths[1]).read_text(encoding='utf-8'))['bills']
    sheet=workbook['Sub Layer - Normalized']
    efficiency_weights={}
    for col in range(33,50):
        category=BR.canonical_category(sheet.cell(2,col).value)
        weight=sheet.cell(3,col).value
        if not category or not isinstance(weight,(int,float)):
            raise ValueError('Unrecognized legislative efficiency dial: '+str(sheet.cell(2,col).value))
        if category in efficiency_weights:raise ValueError('Duplicate efficiency category')
        efficiency_weights[category]=float(weight)
    blended=workbook['Blended Model']
    blend=[float(blended.cell(i,28).value) for i in (10,11)]
    result=measure(members,rows,bills,topic_weights,efficiency_weights,blend)
    archive=json.loads((root/'archive_leg.json').read_text(encoding='utf-8'))
    for name in members:
        expected=sum(float(value)*abs(float(topic_weights[cat])) for cat,value in archive.get(name,{}).items())
        if abs(expected-result['agenda'][name])>1e-8:raise ValueError('Routing/archive agenda mismatch: '+name)
    result.update({'schema':'live-legislative-pillar-v1','inputs_sha256':inputs,
                   'blend':blend,'topic_weights':topic_weights,'efficiency_weights':efficiency_weights,
                   'policy':'Accepted signed role-weighted agenda; signed decay-weighted sponsored/original bill advancement, normalized per category; original 17 efficiency slots retained. Bill direction applied once. Workbook AB10/AB11 preserved.'})
    return result


def nonlegislative_inputs(workbook,members):
    """Evaluate existing committee and demographic input formulas from typed cells."""
    committee={row[0]:float(row[1] or 0) for row in workbook['Committee Data'].iter_rows(min_row=2,values_only=True) if row[0]}
    weights=[float(workbook['Sub Layer - Normalized'].cell(3,col).value) for col in range(22,33)]
    demographics={row[1]:sum(float(row[col-1] or 0)*weights[col-22] for col in range(22,33))
                  for row in workbook['Sub Layer - Raw'].iter_rows(min_row=4,max_row=536,values_only=True) if row[1]}
    if set(demographics)!=set(members):raise ValueError('Demographic inputs do not cover current rank roster')
    return {name:committee.get(name,0.0) for name in members},demographics


def from_effectiveness(comparison, *, mode):
    """Explicit candidate Leg% input; legacy recompute remains the default."""
    from engine.effectiveness_integration import (
        SCHEMA, RESULTS_MODE, COMPOSITE_MODE, legislative_percentiles,
    )
    if comparison.get('schema') != SCHEMA or comparison.get('status') != 'shadow_candidate':
        raise ValueError('Expected versioned shadow effectiveness comparison')
    keys = {RESULTS_MODE: 'results_only_leg_percentile',
            COMPOSITE_MODE: 'advocacy_composite_leg_percentile'}
    if mode not in keys:
        raise ValueError('Unknown effectiveness legislative mode: ' + str(mode))
    provided = {name: row.get(keys[mode]) for name, row in comparison['members'].items()}
    return {'schema': 'live-legislative-pillar-shadow-v1', 'mode': mode,
            'status': 'shadow_candidate', 'input_kind': 'chamber_percentile',
            'pillar': legislative_percentiles({}, mode=mode, provided=provided),
            'source_sha256': comparison['source_sha256'],
            'as_of': comparison.get('as_of'), 'congress': comparison['congress']}
