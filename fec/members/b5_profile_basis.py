# -*- coding: utf-8 -*-
"""Build the active construct-guarded profile basis from current, validated inputs."""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from moneyball_core import dump_atomic
from paths import ROOT

OUTPUT=os.path.join(ROOT,'data', 'members', 'profile_basis_v2.json')


def load(name):
    with open(os.path.join(ROOT,name),encoding='utf-8') as handle:
        return json.load(handle)


def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def member_basis(name,blue,dispositions,roles,style,audio,gold_traits):
    b=blue.get(name) or {}; d=dispositions.get(name) or {}
    role=roles.get(name) or {}; comm=style.get(name) or {}
    exposure=comm.get('public_exposure')
    external={}
    for trait in ('O','C','E','A','N'):
        result=gold_traits.get(trait) or {}; member=(result.get('members') or {}).get(name)
        external[trait]={
            'z':member.get(trait) if member else None,
            'percentile':member.get('percentile') if member else None,
            'status':result.get('status','WITHHELD_MISSING_VALIDATION'),
            'selected_weights':result.get('selected_weights') or {},
            'supporting_panels':result.get('supporting_panels') or [],
            'strict_discriminant_panels':result.get('strict_discriminant_panels') or [],
            'scope':result.get('scope'),
        }
    anchored=(gold_traits.get('E',{}).get('members') or {}).get(name)
    operational=None
    if exposure is not None:
        operational={
            'percentile':exposure.get('role_adjusted_percentile'),
            'z':exposure.get('role_adjusted_visibility_z'),
            'status':'OBSERVED_CONTEXT_NOT_TRAIT',
            'scope':'public exposure after formal-role adjustment; retained as context and one '
                    '10% channel in externally anchored Extraversion, not Extraversion by itself',
        }
    public=dict(exposure or {})
    public.update({
        'visibility_z':d.get('visibility_z'),
        'meaning':'observed and role-adjusted public/media exposure; residual may still reflect '
                  'newsworthiness or strategy and is not Extraversion',
    })
    return {
        'personality_facets':{
            'Openness':{
                'z':external['O']['z'],'percentile':external['O']['percentile'],'status':external['O']['status'],
                'scope':'broad public expert-perceived Openness; one-panel association supported; not private curiosity',
            },
            'Conscientiousness':{
                'z':external['C']['z'],'percentile':external['C']['percentile'],'status':external['C']['status'],
                'scope':'broad public expert-perceived Conscientiousness; one-panel association supported without discriminant dominance',
            },
            'Extraversion':{
                'z':anchored.get('E') if anchored else None,
                'percentile':anchored.get('percentile') if anchored else None,
                'status':'available_external_gold_anchor' if anchored else 'unavailable_missing_observer_channel',
                'scope':'OOF-validated public-personality E estimate from visibility-calibrated Blue, '
                        'observer-item Pink, interaction dominance, and role-adjusted visibility; not '
                        'self-report or a private-state measure',
            },
            'Agreeableness':{
                'z':external['A']['z'],'percentile':external['A']['percentile'],'status':external['A']['status'],
                'scope':'broad public expert-perceived Agreeableness validated against both independent panels',
            },
            'Neuroticism':{
                'z':external['N']['z'],'percentile':external['N']['percentile'],'status':external['N']['status'],
                'scope':'broad public expert-perceived Neuroticism, reverse expert Emotional Stability; one-panel association supported; not diagnosis',
            },
            'Openness_text_issue_breadth':{
                'z':b.get('O'),'scope':'qualified text read anchored to legislative issue breadth',
            },
            'Conscientiousness_text_diligence':{
                'z':b.get('C'),'scope':'qualified text read anchored to legislative diligence',
            },
            'Agreeableness_low_antagonism':{
                'z':b.get('A'),'scope':'qualified low-antagonism public-language facet; not legislative cooperation',
            },
            'Neuroticism_affective_content':{
                'z':b.get('N'),'scope':'qualified affective-content public-language read; not a diagnosis',
            },
        },
        'public_role_exposure':public,
        'behavioral_record':{
            'cooperation_z':d.get('cooperation_z'),'breadth_z':d.get('breadth_z'),'diligence_z':d.get('diligence_z'),
        },
        'observed_conversation':{
            'dominance_z':role.get('dominance_v2'),'expressiveness_z':role.get('expressiveness_v2'),
            'meaning':'venue-bound turn behavior; dominance is one 10% anchored-E channel, not Extraversion by itself',
        },
        'verified_audio_v2':audio.get(name),
        'externally_anchored_e':anchored,
        'external_gold_traits':external,
        'operational_public_e_cue':operational,
        'communication_style':{
            'raw_text':comm.get('raw_text') or {},'facets':comm.get('facets') or {},
            'elaboration':comm.get('elaboration'),'scope':comm.get('scope'),
            'extraversion_status':comm.get('extraversion_status'),
        },
    }


def build():
    roster=[row['member'] for row in load('data/engine/congestion.json')]
    blue_doc=load('data/members/big5_blue_validated.json')
    disp_doc=load('behavioral_dispositions.json')
    roles_doc=load('data/members/behavioral_roles_v2_candidate.json')
    style_doc=load('communication_style.json')
    audio_doc=load('data/members/b5_audio_descriptors_v2.json')
    gold_doc=load('data/stance/big5_gold_anchored.json')
    if style_doc.get('schema')!='communication-style-v1' or style_doc.get('status')!='ACTIVE_DISPLAY_ONLY':
        raise ValueError('communication_style.json missing or wrong schema/status')
    if audio_doc.get('schema')!='b5-audio-descriptors-v2' or audio_doc.get('status')!='ACTIVE_FAIL_CLOSED':
        raise ValueError('active fail-closed audio descriptor artifact missing or wrong schema/status')
    if gold_doc.get('schema')!='big5-gold-anchored-v2' or gold_doc.get('status')!='ACTIVE_WITH_TRAIT_LEVEL_GATES':
        raise ValueError('multitrait external-gold artifact missing or wrong schema/status')
    if (gold_doc.get('traits') or {}).get('E',{}).get('status')!='ACTIVE_EXTERNAL_GOLD_ANCHORED':
        raise ValueError('externally anchored E failed OOF validation')
    inactive=[trait for trait,row in (gold_doc.get('traits') or {}).items()
              if not str(row.get('status') or '').startswith('ACTIVE_')]
    if inactive:
        raise ValueError('broad gold traits not integrated: '+','.join(inactive))
    if set(style_doc.get('members') or {})!=set(roster):
        raise ValueError('communication-style roster mismatch')
    members={name:member_basis(
        name,blue_doc.get('traits') or {},disp_doc.get('members') or {},
        roles_doc.get('members') or {},style_doc.get('members') or {},audio_doc.get('members') or {},
        gold_doc.get('traits') or {},
    ) for name in roster}
    inputs={}
    for name in ('data/members/big5_blue_validated.json','behavioral_dispositions.json','data/members/behavioral_roles_v2_candidate.json',
                 'communication_style.json','data/members/b5_audio_descriptors_v2.json'):
        inputs[name]=sha256_file(os.path.join(ROOT,name))
    inputs['data/stance/big5_gold_anchored.json']=sha256_file(os.path.join(ROOT,'data','stance','big5_gold_anchored.json'))
    return {
        'schema':'profile-basis-v2','status':'ACTIVE_CONSTRUCT_GUARDED',
        'construct_contract':{
            'legacy_blue_E':'retained at 20% as the visibility-calibrated channel in anchored E',
            'visibility':'observed and formal-role-adjusted public exposure is separately labeled context and a 10% E channel',
            'Extraversion':'OOF external-gold-checked public-personality estimate: 20% Blue, 60% Pink, '
                           '10% interaction dominance, 10% role-adjusted visibility; not self-report/private state',
            'Openness':'broad gold estimate integrated with one-panel association support; qualified text facet retained separately',
            'Conscientiousness':'broad gold estimate integrated with one-panel association support but no discriminant dominance; qualified text/diligence context retained',
            'Agreeableness':'broad two-panel gold-anchored A integrated; qualified low-antagonism facet remains separate',
            'Neuroticism':'broad reverse-ES estimate integrated with one-panel association support; qualified affective-content read remains separate',
            'external_gold_traits':'all O/C/E/A/N estimates propagate with panel-strength labels; missing member evidence remains null',
            'communication_style':'direct text/interaction observations; facets are not averaged into E',
            'audio':'speech-level delivery and topic-conditioned within-speaker pitch deviations only; '
                    'absent unless active audio-v2 verification passes; never modifies Big Five',
        },
        'inputs_sha256':inputs,'members':members,
    }


def self_test():
    row=member_basis('Example',{'Example':{'O':.1,'C':-.2,'E':9,'A':.3,'N':.4}},
        {'Example':{'visibility_z':2}}, {'Example':{'dominance_v2':1}},
        {'Example':{'public_exposure':None,'facets':{},'raw_text':{},'extraversion_status':'unavailable'}},{},
        {'O':{'status':'ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED','members':{'Example':{'O':.11,'percentile':.55}}},
         'C':{'status':'ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED','members':{'Example':{'C':-.12,'percentile':.44}}},
         'E':{'status':'ACTIVE_EXTERNAL_GOLD_ANCHORED','members':{'Example':{'E':.25,'percentile':.61}}},
         'A':{'status':'ACTIVE_EXTERNAL_GOLD_ANCHORED','members':{'Example':{'A':.31,'percentile':.66}}},
         'N':{'status':'ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED','members':{'Example':{'N':.41,'percentile':.71}}}})
    assert row['personality_facets']['Extraversion']['z']==.25
    assert row['personality_facets']['Openness']['z']==.11
    assert row['personality_facets']['Conscientiousness']['z']==-.12
    assert row['personality_facets']['Agreeableness']['z']==.31
    assert row['personality_facets']['Neuroticism']['z']==.41
    assert row['verified_audio_v2'] is None
    assert '9' not in json.dumps(row,sort_keys=True)
    print('b5_profile_basis self-test: 7 passed, 0 failed')


def main():
    if '--self-test' in sys.argv:
        self_test(); return
    doc=build(); dump_atomic(doc,OUTPUT,indent=1)
    print('profile_basis_v2.json: %d guarded profiles'%len(doc['members']))


if __name__=='__main__':
    main()
