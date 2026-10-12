# -*- coding: utf-8 -*-
"""Validate and build the externally anchored public-personality Extraversion estimate.

The estimator is deliberately fixed before using the gold values: an equal-weight mean of the
standardized source-primary Blue E channel (visibility-calibrated) and the standardized Pink
observer-item E channel.  The historic insider and academic panels are validation only; no gold
value is used to fit or tune a coefficient.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata

import numpy as np

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from moneyball_core import dump_atomic
from paths import ROOT

TRAITS=('O','C','E','A','ES')
GOLD='data/stance/big5_senate_expert_gold.json'
BLUE='data/members/big5_blue_validated.json'
PINK='data/members/big5_llm_opus.json'
DISPOSITIONS='behavioral_dispositions.json'
STYLE='communication_style.json'
VALIDATION_OUT=os.path.join(ROOT,'data','stance','big5_external_gold_validation.json')
E_OUT=os.path.join(ROOT,'data','stance','big5_e_gold_anchored.json')
MIN_COVERAGE=450
MIN_PANEL_OVERLAP=30
MIN_PANEL_AGREEMENT=.30
MIN_PANEL_E_R=.30
PERMUTATIONS=9999
SEED=20260715


def load(name):
    with open(os.path.join(ROOT,name),encoding='utf-8') as handle:
        return json.load(handle)


def sha256_file(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def corr(left,right):
    pairs=[(float(a),float(b)) for a,b in zip(left,right)
           if a is not None and b is not None and np.isfinite(a) and np.isfinite(b)]
    if len(pairs)<5:
        return None,len(pairs)
    x,y=map(np.asarray,zip(*pairs))
    if x.std()==0 or y.std()==0:
        return None,len(pairs)
    return float(np.corrcoef(x,y)[0,1]),len(pairs)


def zmap(values):
    names=sorted(values); vector=np.asarray([values[name] for name in names],float)
    sd=vector.std(ddof=1) or 1.0
    return {name:float((values[name]-vector.mean())/sd) for name in names}


def percentile_map(values):
    ordered=sorted(values.values()); denominator=max(1,len(ordered)-1)
    import bisect
    return {name:round(bisect.bisect_left(ordered,value)/denominator,3)
            for name,value in values.items()}


def name_key(value,source_order=False):
    text=unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode()
    text=re.sub(r'\b(Jr|Sr|II|III|IV)\.?\b','',text,flags=re.I)
    if source_order and ',' in text:
        last,first=text.split(',',1); text=first+' '+last
    tokens=re.findall(r'[A-Za-z]+',text.lower())
    return (tokens[0],tokens[-1]) if len(tokens)>=2 else tuple(tokens)


def map_gold(gold,roster):
    current={}
    for name in roster:
        current.setdefault(name_key(name),[]).append(name)
    panels={'senate_insiders':{},'academic_experts':{}}
    unmatched=[]; ambiguous=[]
    for row in gold['records']:
        matches=current.get(name_key(row['source_name'],True),[])
        if len(matches)==1:
            panels[row['panel']][matches[0]]=row
        elif matches:
            ambiguous.append({'source_name':row['source_name'],'matches':matches})
        else:
            unmatched.append(row['source_name'])
    if ambiguous:
        raise ValueError('ambiguous external-gold roster matches: %r'%ambiguous[:5])
    return panels,sorted(set(unmatched))


def one_sided_permutation_p(x,y,seed):
    x=np.asarray(x,float); y=np.asarray(y,float); observed=float(np.corrcoef(x,y)[0,1])
    rng=np.random.default_rng(seed); exceed=0
    for _ in range(PERMUTATIONS):
        if float(np.corrcoef(x,rng.permutation(y))[0,1])>=observed:
            exceed+=1
    return observed,(exceed+1)/(PERMUTATIONS+1)


def row_correlations(scores,panel):
    out={}; ns={}
    for trait in TRAITS:
        members=[name for name,row in panel.items() if name in scores and row.get(trait) is not None]
        out[trait],ns[trait]=corr([scores[name] for name in members],[panel[name][trait] for name in members])
    return out,ns


def instrument_matrix(instrument,panel,pink_raw=False):
    matrix={}; ns={}
    for source_trait in TRAITS:
        scores={}
        for name,row in instrument.items():
            value=(row.get('raw_z') or {}).get(source_trait) if pink_raw else row.get(source_trait)
            if value is not None:
                scores[name]=value
        matrix[source_trait],ns[source_trait]=row_correlations(scores,panel)
    return matrix,ns


def build_e(blue,pink):
    pink_e={name:row.get('raw_z',{}).get('E') for name,row in pink.items()
            if row.get('n_chunks',0)>0 and row.get('raw_z',{}).get('E') is not None}
    shared=sorted(set(blue)&set(pink_e))
    blue_e={name:blue[name]['E'] for name in shared if blue[name].get('E') is not None}
    shared=sorted(set(shared)&set(blue_e)); pink_e={name:pink_e[name] for name in shared}
    bz=zmap(blue_e); pz=zmap(pink_e)
    raw={name:.5*bz[name]+.5*pz[name] for name in shared}
    scores=zmap(raw); percentiles=percentile_map(scores)
    members={name:{
        'E':round(scores[name],6),'percentile':percentiles[name],
        'blue_visibility_channel_z':round(bz[name],6),'pink_observer_channel_z':round(pz[name],6),
        'channel_weights':{'blue_visibility':.5,'pink_observer':.5},
    } for name in shared}
    return scores,members,bz,pz


def validate():
    filenames=(GOLD,BLUE,PINK,DISPOSITIONS,STYLE)
    gold=load(GOLD); blue_doc=load(BLUE); pink_doc=load(PINK)
    if gold.get('schema')!='big5-senate-expert-gold-v1' or gold.get('status')!='FROZEN_EXTERNAL_GOLD':
        raise ValueError('external gold artifact missing/wrong schema')
    roster=[row['member'] for row in load('data/engine/congestion.json')]
    panels,unmatched=map_gold(gold,roster)
    blue=blue_doc.get('traits') or {}; pink=pink_doc.get('traits') or {}
    scores,members,blue_e,pink_e=build_e(blue,pink)
    agreement={}
    shared=sorted(set(panels['senate_insiders'])&set(panels['academic_experts']))
    for trait in TRAITS:
        agreement[trait],n=corr(
            [panels['senate_insiders'][name].get(trait) for name in shared],
            [panels['academic_experts'][name].get(trait) for name in shared])
        agreement[trait]={'r':agreement[trait],'n':n}
    panel_results={}; comparators={}
    all_pass=len(members)>=MIN_COVERAGE and agreement['E']['n']>=MIN_PANEL_OVERLAP and agreement['E']['r']>=MIN_PANEL_AGREEMENT
    for index,(panel_name,panel) in enumerate(panels.items()):
        row,ns=row_correlations(scores,panel)
        eligible=[name for name in panel if name in scores and panel[name].get('E') is not None]
        observed,pvalue=one_sided_permutation_p(
            [scores[name] for name in eligible],[panel[name]['E'] for name in eligible],SEED+index)
        other=max(abs(value) for trait,value in row.items() if trait!='E' and value is not None)
        passed=(len(eligible)>=MIN_PANEL_OVERLAP and observed>=MIN_PANEL_E_R and pvalue<.05 and observed>other)
        all_pass=all_pass and passed
        panel_results[panel_name]={
            'matched_current_members':len(panel),'e_n':len(eligible),'e_r':observed,
            'e_permutation_p_one_sided':pvalue,'e_row':row,'e_row_n':ns,
            'max_abs_heterotrait':other,'diagonal_dominant':observed>other,'pass':passed,
        }
        comparators[panel_name]={
            'blue_visibility_only_e_row':row_correlations(blue_e,panel)[0],
            'pink_observer_only_e_row':row_correlations(pink_e,panel)[0],
            'equal_weight_multimethod_e_row':row,
            'blue_full_instrument_by_gold_matrix':instrument_matrix(blue,panel)[0],
            'pink_full_instrument_by_gold_matrix':instrument_matrix(pink,panel,True)[0],
        }
    dispositions=load(DISPOSITIONS).get('members') or {}
    style=load(STYLE).get('members') or {}
    visibility={}
    for panel_name,panel in panels.items():
        visibility[panel_name]={}
        for trait in TRAITS:
            names=[name for name,row in panel.items() if row.get(trait) is not None and
                   dispositions.get(name,{}).get('visibility_z') is not None and
                   (style.get(name,{}).get('public_exposure') or {}).get('role_adjusted_visibility_z') is not None]
            raw,_=corr([panel[name][trait] for name in names],[dispositions[name]['visibility_z'] for name in names])
            adjusted,_=corr([panel[name][trait] for name in names],
                            [style[name]['public_exposure']['role_adjusted_visibility_z'] for name in names])
            visibility[panel_name][trait]={'raw_visibility_r':raw,'role_adjusted_visibility_r':adjusted,'n':len(names)}
    hashes={name:sha256_file(os.path.join(ROOT,name)) for name in filenames}
    validation={
        'schema':'big5-external-gold-validation-v1','status':'PASS' if all_pass else 'FAIL',
        'design':{
            'estimator':'equal mean of roster-standardized Blue E and Pink raw observer-item E; re-standardized',
            'gold_used_to_fit_weights':False,'blue_weight':.5,'pink_weight':.5,
            'visibility_role':'one nomological/criterion channel, not Extraversion by itself',
            'historic_gold_caveat':'ratings concern senators observed in the appendix era; this validates rank ordering on overlap, not population norms',
        },
        'gates':{'minimum_coverage':MIN_COVERAGE,'minimum_panel_overlap':MIN_PANEL_OVERLAP,
                 'minimum_panel_agreement':MIN_PANEL_AGREEMENT,'minimum_panel_e_r':MIN_PANEL_E_R,
                 'one_sided_permutation_p_below':.05,'e_row_diagonal_dominance':True},
        'coverage':{'e_members':len(members),'roster_members':len(roster),'panels':{k:len(v) for k,v in panels.items()},
                    'shared_panel_members':len(shared),'unmatched_historic_names':len(unmatched)},
        'panel_agreement':agreement,'panel_results':panel_results,'comparators':comparators,
        'nomological_visibility':visibility,'inputs_sha256':hashes,
        'verdict':'PASS' if all_pass else 'FAIL',
    }
    e_doc={
        'schema':'big5-e-gold-anchored-v1',
        'status':'ACTIVE_EXTERNAL_GOLD_ANCHORED' if all_pass else 'WITHHELD_FAILED_GATES',
        'scope':'externally anchored public-personality Extraversion estimate; not self-report, diagnosis, or private-state measurement',
        'method':'equal-weight standardized Blue visibility-calibrated E plus Pink observer-item E; no gold-fitted coefficients',
        'coverage_n':len(members) if all_pass else 0,'members':members if all_pass else {},
        'validation_summary':{name:{'e_r':row['e_r'],'n':row['e_n'],'p':row['e_permutation_p_one_sided'],
                                    'diagonal_dominant':row['diagonal_dominant']}
                              for name,row in panel_results.items()},
        'validation_sha256':None,'inputs_sha256':hashes,
    }
    dump_atomic(validation,VALIDATION_OUT,indent=1)
    e_doc['validation_sha256']=sha256_file(VALIDATION_OUT)
    dump_atomic(e_doc,E_OUT,indent=1)
    return validation,e_doc


def self_test():
    assert name_key('Capito, Shelley Moore',True)==name_key('Shelley Capito')
    assert name_key('King, Angus S., Jr.',True)==name_key('Angus King')
    assert round(corr([1,2,3,4,5],[2,4,6,8,10])[0],6)==1.0
    z=zmap({'a':1,'b':2,'c':3}); assert abs(sum(z.values()))<1e-9
    print('b5_external_gold_validate self-test: 4 passed, 0 failed')


def main():
    if '--self-test' in sys.argv:
        self_test(); return 0
    validation,e_doc=validate()
    print('external gold %s: E %d members | insiders r=%+.3f | academics r=%+.3f'%(
          validation['verdict'],e_doc['coverage_n'],
          validation['panel_results']['senate_insiders']['e_r'],
          validation['panel_results']['academic_experts']['e_r']))
    return 0 if validation['verdict']=='PASS' else 1


if __name__=='__main__':
    sys.exit(main())
