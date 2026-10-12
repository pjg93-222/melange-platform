# -*- coding: utf-8 -*-
"""Fail-closed validation for accepted profile artifacts whose producers are guarded/retired."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT
from members.validate_communication_style import sha256_file,validate_input_hashes


def load(name):
    with open(os.path.join(ROOT,name),encoding='utf-8') as handle:
        return json.load(handle)


def validate_all():
    errors=[]; roster={m['member'] for m in load('data/engine/congestion.json')}
    audio=load('data/members/b5_audio_descriptors_v2.json'); ac=audio.get('measurement_contract') or {}
    if audio.get('schema')!='b5-audio-descriptors-v2' or audio.get('status')!='ACTIVE_FAIL_CLOSED':
        errors.append('audio schema/status mismatch')
    if ac.get('two_distinct_reviews_per_clip') is not True or ac.get('audio_may_modify_big_five') is not False:
        errors.append('audio deployment contract mismatch')
    if (ac.get('minimum_speech_units_per_member')!=5 or ac.get('minimum_distinct_topics_per_member')!=2 or
        'member mean' not in str(ac.get('pitch_deviation_from_speaker_baseline_z') or '')):
        errors.append('audio pitch/topic baseline contract mismatch')
    if not set(audio.get('members') or {}).issubset(roster): errors.append('audio contains unknown members')
    if int(audio.get('accepted_clip_n') or 0)==0 and (audio.get('members') or {}):
        errors.append('audio members exist with zero accepted clips')
    review_path=os.path.join(ROOT,'data', 'members', 'b5_audio_reviews_v2.json')
    if not os.path.exists(review_path):
        errors.append('audio review source missing')
    elif sha256_file(review_path).lower()!=str(audio.get('review_sha256') or '').lower():
        errors.append('audio review_sha256 mismatch')
    candidate_path=os.path.join(ROOT,'data','members','b5_audio_reviews_v2.json.candidate')
    if not os.path.exists(candidate_path):
        errors.append('accepted-audio candidate intake missing')
    else:
        candidate=load('data/members/b5_audio_reviews_v2.json.candidate')
        source=candidate.get('source_manifest') or {}; partition=candidate.get('partition') or {}
        manifest_path=source.get('path') or ''
        if (candidate.get('schema')!='b5-audio-review-v2-candidate' or
                candidate.get('status')!='CANDIDATE_ONLY_CONSUMED_BY_NOTHING_AWAITING_DOUBLE_REVIEW'):
            errors.append('accepted-audio candidate schema/status mismatch')
        if (not manifest_path or not os.path.isfile(manifest_path) or
                sha256_file(manifest_path).lower()!=str(source.get('sha256') or '').lower()):
            errors.append('accepted-audio candidate manifest binding mismatch')
        if int(partition.get('queued') or 0)+int(partition.get('excluded') or 0)!=int(partition.get('total') or -1):
            errors.append('accepted-audio candidate partition mismatch')
        if (candidate.get('contract') or {}).get('active_artifacts_written') is not False:
            errors.append('accepted-audio candidate active-artifact isolation mismatch')
        descriptor_candidate_path=os.path.join(ROOT,'data','members','b5_audio_descriptors_v2.json.candidate')
        if not os.path.exists(descriptor_candidate_path):
            errors.append('candidate audio descriptor gate missing')
        else:
            descriptor_candidate=load('data/members/b5_audio_descriptors_v2.json.candidate')
            if (descriptor_candidate.get('schema')!='b5-audio-descriptors-v2-candidate' or
                    descriptor_candidate.get('status')!='CANDIDATE_CONSUMED_BY_NOTHING_AWAITING_USER_RATIFICATION' or
                    str(descriptor_candidate.get('review_sha256') or '').lower()!=sha256_file(candidate_path).lower() or
                    (descriptor_candidate.get('source_manifest') or {}).get('sha256')!=source.get('sha256')):
                errors.append('candidate audio descriptor/review/manifest binding mismatch')

    corpus=load('data/members/corpus_integrity_report.json'); db=corpus.get('database') or {}
    accepted=corpus.get('accepted_model') or {}; quarantine=corpus.get('quarantined_chrg') or {}
    if corpus.get('schema')!='corpus-integrity-v1' or corpus.get('status')!='PASS' or corpus.get('hard_errors'):
        errors.append('corpus integrity status failed')
    if accepted.get('cache_exact_match') is not True or accepted.get('cache_content_match') is not True:
        errors.append('accepted model cache mismatch')
    if db.get('exact_duplicate_surplus')!=0: errors.append('corpus has exact duplicate surplus')
    if quarantine.get('enters_default_big5') is not False or quarantine.get('repair_status')!='source-rebuilt and verified':
        errors.append('CHRG quarantine contract mismatch')
    if (quarantine.get('service_chamber_mismatch_rows',0)+quarantine.get('no_member_service_rows',0))!=0:
        errors.append('CHRG attribution mismatch remains')

    gold_doc=load('data/stance/big5_gold_anchored.json'); traits=gold_doc.get('traits') or {}
    e_doc=traits.get('E') or {}; em=e_doc.get('members') or {}
    if gold_doc.get('schema')!='big5-gold-anchored-v2' or set(traits)!={'O','C','E','A','N'}:
        errors.append('multitrait external-gold schema/trait mismatch')
    if any(not str(row.get('status') or '').startswith('ACTIVE_') for row in traits.values()):
        errors.append('one or more broad external-gold traits are not active')
    if e_doc.get('status')!='ACTIVE_EXTERNAL_GOLD_ANCHORED':
        errors.append('external-gold E status mismatch')
    if len(em)<450 or not set(em).issubset(roster): errors.append('external-gold E coverage/roster mismatch')
    ew=e_doc.get('selected_weights') or {}
    if ew.get('interaction_dominance',0)<.1 or ew.get('role_adjusted_visibility',0)<.1:
        errors.append('external-gold E behavioral-channel minimum mismatch')
    if not all((row.get('validation') or {}).get('predictions_are_out_of_fold') for row in traits.values()):
        errors.append('multitrait external-gold OOF contract mismatch')

    profile=load('data/members/profile_basis_v2.json'); pm=profile.get('members') or {}
    if profile.get('schema')!='profile-basis-v2' or profile.get('status')!='ACTIVE_CONSTRUCT_GUARDED':
        errors.append('profile basis schema/status mismatch')
    if set(pm)!=roster: errors.append('profile basis roster mismatch')
    errors.extend('profile '+error for error in validate_input_hashes(profile,ROOT))
    for name,row in pm.items():
        facets=row.get('personality_facets') or {}; ext=facets.get('Extraversion') or {}
        propagated=row.get('external_gold_traits') or {}
        canonical={'O':'Openness','C':'Conscientiousness','E':'Extraversion',
                   'A':'Agreeableness','N':'Neuroticism'}
        if set(propagated)!={'O','C','E','A','N'}:
            errors.append('external-gold trait verdict propagation missing: '+name)
        if name in em:
            if (ext.get('z')!=em[name].get('E') or ext.get('percentile')!=em[name].get('percentile') or
                ext.get('status')!='available_external_gold_anchor'):
                errors.append('external-gold E propagation mismatch: '+name)
        elif ext.get('z') is not None or ext.get('status')!='unavailable_missing_observer_channel':
            errors.append('missing E channel was imputed: '+name)
        for trait,result in traits.items():
            got=propagated.get(trait) or {}; member=(result.get('members') or {}).get(name)
            if got.get('status')!=result.get('status') or got.get('z')!=(member or {}).get(trait):
                errors.append('external-gold trait verdict mismatch: %s %s'%(name,trait))
            broad=facets.get(canonical[trait]) or {}
            if broad.get('z')!=(member or {}).get(trait) or broad.get('percentile')!=(member or {}).get('percentile'):
                errors.append('canonical broad trait propagation mismatch: %s %s'%(name,trait))
        if row.get('verified_audio_v2') is not None and name not in (audio.get('members') or {}):
            errors.append('unverified audio leaked: '+name)
    return errors,audio,pm


def main():
    errors,audio,pm=validate_all()
    if errors:
        print('FATAL: guarded profile artifact validation failed: '+'; '.join(errors[:12]))
        return 1
    print('validated guarded profile artifacts: audio %d verified members | all broad gold traits active with panel-strength labels | corpus PASS | profile basis %d members | provenance current'%(
          len(audio.get('members') or {}),len(pm)))
    return 0


if __name__=='__main__':
    sys.exit(main())
