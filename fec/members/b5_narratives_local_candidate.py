# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""Zero-API, deterministic three-paragraph profile renderer (candidate; consumed by NOTHING).

The sentence architecture mirrors the original Fable-era briefs: compact public-working-style portrait,
specific manner-of-speech/landmine paragraph, and concrete suggested approach.  Unlike the old LLM
route, every sentence is selected from explicit evidence-bounded templates.  Public visibility is never
treated as Extraversion; audio enters only through b5_audio_descriptors_v2.json.candidate; no API key,
network call, or paid model is used.

Usage:
  python fec/members/b5_narratives_local_candidate.py --self-test
  python fec/members/b5_narratives_local_candidate.py --basis data/members/profile_basis_v2.json.candidate \
      --out member_narratives_v2.json.candidate [--only "Name,..."]
"""
import argparse, hashlib, json, math, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from moneyball_core import dump_atomic


def _z(block, key):
    value = ((block.get('personality_facets') or {}).get(key) or {}).get('z')
    try: return float(value) if value is not None and math.isfinite(float(value)) else None
    except (TypeError, ValueError): return None


def _state(value, threshold=0.75):
    if value is None: return 'missing'
    if value >= threshold: return 'high'
    if value <= -threshold: return 'low'
    return 'middle'


def _behavior_z(block, group, key):
    try:
        value = (block.get(group) or {}).get(key)
        return float(value) if value is not None and math.isfinite(float(value)) else None
    except (TypeError, ValueError):
        return None


def _working_style(block):
    raw_vis = _state(_behavior_z(block, 'public_role_exposure', 'visibility_z'))
    adjusted_vis = _state(_behavior_z(block, 'public_role_exposure', 'role_adjusted_visibility_z'))
    dom = _state(_behavior_z(block, 'observed_conversation', 'dominance_z'))
    exp = _state(_behavior_z(block, 'observed_conversation', 'expressiveness_z'))
    o = _state(_z(block, 'Openness')); c = _state(_z(block, 'Conscientiousness'))
    a = _state(_z(block, 'Agreeableness_low_antagonism'))
    n = _state(_z(block, 'Neuroticism_affective_content'))

    if raw_vis == 'high' and adjusted_vis == 'high':
        s1 = ('This member has high public exposure even after accounting for formal office obligations; '
              'that residual may reflect newsworthiness or strategy and still does not establish that they are personally outgoing.')
    elif raw_vis == 'high' and adjusted_vis in ('middle', 'low'):
        s1 = ('This member operates under a high public spotlight, but the exposure is not unusually high once formal office '
              'obligations are considered; neither the title nor its visibility establishes a personally outgoing disposition.')
    elif raw_vis == 'high':
        s1 = ('This member operates under a high public spotlight; no role adjustment is available for this member, '
              'and the exposure does not establish a personally outgoing disposition.')
    elif raw_vis == 'low':
        s1 = ('This member appears less often in the available public and media record, but lower exposure '
              'does not establish a private or introverted disposition.')
    else:
        s1 = ('The available exposure record does not support a strong claim about this member’s preference '
              'for public attention or private settings.')

    if dom == 'high' and exp == 'low':
        s2 = ('In observed exchanges they take and defend the floor readily, usually through compact interventions '
              'rather than long, elaborated turns—assertive without necessarily being expansive.')
    elif dom == 'high' and exp == 'high':
        s2 = ('In observed exchanges they readily take, hold, and elaborate on the floor, so discussion can become '
              'member-led unless the agenda is made explicit at the outset.')
    elif dom == 'low':
        s2 = ('Observed exchanges show limited floor-taking; use direct questions and protected response time rather '
              'than assuming silence or brevity reflects disagreement.')
    elif exp == 'high':
        s2 = ('Observed turns are relatively elaborated, making room for a developed answer more useful than a rapid '
              'sequence of narrowly framed prompts.')
    else:
        s2 = ('The conversation record does not establish a strong, general floor-taking style across settings.')

    if o == 'low':
        s3 = ('The current Openness read is anchored to a narrower legislative issue range, so the defensible working '
              'expectation is a more bounded policy lane—not a claim about private curiosity or imagination.')
    elif o == 'high':
        s3 = ('The current Openness read is anchored to a broad legislative issue range, supporting comfort moving '
              'across policy domains without claiming a general appetite for novelty outside public work.')
    else:
        s3 = ('The issue-breadth read is not extreme enough to justify a broad-versus-specialist characterization.')

    details = []
    if c == 'high': details.append('the public record shows comparatively strong legislative diligence')
    elif c == 'low': details.append('the diligence record is lighter, so implementation bandwidth should not be assumed')
    if a == 'high': details.append('interview language is comparatively low in antagonism')
    elif a == 'low': details.append('interview language is comparatively more antagonistic or confrontational')
    if n == 'high': details.append('negative-affect language appears more often in the measured text')
    elif n == 'low': details.append('negative-affect language appears less often in the measured text')
    s4 = ('Within those bounds, ' + '; '.join(details) +
          '—all public-record observations, not judgments about private character.') if details else (
          'The remaining measured facets sit near the middle or are missing, so the brief should resist filling those '
          'gaps with a generic personality story.')
    return ' '.join((s1, s2, s3, s4))


def _audio_sentence(audio):
    if not audio:
        return ('No audio claim is warranted yet: the v2 layer requires two independent clip reviews and at least two '
                'sources before any vocal description enters the brief.')
    metrics = audio.get('metrics') or {}
    pace = metrics.get('delivery_rate_wpm') or {}
    lo = pace.get('min'); hi = pace.get('max'); band = pace.get('stable_outer_quartile')
    if band == 'high_across_sources':
        pace_text = 'Delivery rate is consistently in the reviewed cohort’s upper quartile across sources.'
    elif band == 'low_across_sources':
        pace_text = 'Delivery rate is consistently in the reviewed cohort’s lower quartile across sources.'
    elif lo is not None and hi is not None and float(hi) - float(lo) >= 20:
        pace_text = ('Delivery rate varies materially by source—about %.0f to %.0f transcript words per elapsed '
                     'minute—so “fast” or “slow” is not a stable personal label.' % (lo, hi))
    elif lo is not None and hi is not None:
        pace_text = 'Reviewed delivery rates span about %.0f to %.0f transcript words per elapsed minute.' % (lo, hi)
    else:
        pace_text = 'The verified clips do not support a stable pace label.'
    pv = (metrics.get('pitch_variability_raw') or {}).get('stable_outer_quartile')
    vd = (metrics.get('volume_dynamics_raw') or {}).get('stable_outer_quartile')
    cues = []
    if pv == 'high_across_sources': cues.append('pitch variation is consistently broad')
    elif pv == 'low_across_sources': cues.append('pitch variation is consistently restrained')
    if vd == 'high_across_sources': cues.append('within-recording volume dynamics are consistently broad')
    elif vd == 'low_across_sources': cues.append('within-recording volume stays comparatively even')
    if cues:
        return pace_text + ' Across the same sources, ' + ' and '.join(cues) + '.'
    return pace_text + ' Pitch and volume evidence does not clear the cross-source extreme gate, so no dramatic vocal label is added.'


def _manner(block):
    dom = _state(_behavior_z(block, 'observed_conversation', 'dominance_z'))
    exp = _state(_behavior_z(block, 'observed_conversation', 'expressiveness_z'))
    o = _state(_z(block, 'Openness')); c = _state(_z(block, 'Conscientiousness'))
    sentences = [_audio_sentence(block.get('verified_audio_v2'))]
    if dom == 'high':
        sentences.append('In observed exchanges, put the bottom line early and expect the member to take control of the discussion; do not mistake floor-holding for personal sociability.')
    elif dom == 'low':
        sentences.append('In observed exchanges, direct questions and protected time to answer are more useful than forcing a rapid back-and-forth.')
    else:
        sentences.append('The interaction record does not justify a strong claim about interruption, re-entry, or floor defense across venues.')
    if exp == 'low':
        sentences.append('Turn-level evidence is relatively terse, so prepare crisp prompts and resist interpreting short answers as a complete personality portrait.')
    elif exp == 'high':
        sentences.append('Turn-level evidence is relatively elaborated, so leave room for a developed answer and plan transitions explicitly.')
    else:
        sentences.append('Turn length is not extreme enough to support a durable clipped-versus-discursive label.')
    if o == 'low':
        sentences.append('The avoidable friction is structural: do not open with an unbounded, multi-domain concept when a concrete, lane-specific ask can be made.')
    elif c == 'low':
        sentences.append('The avoidable friction is operational: do not rely on an open-ended next step when ownership, date, and follow-up can be specified.')
    else:
        sentences.append('Keep “landmines” evidence-based; the present record does not license invented private triggers or pressure reactions.')
    return ' '.join(sentences)


def _approach(block):
    dom = _state(_behavior_z(block, 'observed_conversation', 'dominance_z'))
    o = _state(_z(block, 'Openness')); c = _state(_z(block, 'Conscientiousness'))
    a = _state(_z(block, 'Agreeableness_low_antagonism'))
    moves = []
    if o == 'low':
        moves.append('Bring one bounded ask, anchor it in precedent or operational proof, and state the near-term result before the conceptual rationale.')
    elif o == 'high':
        moves.append('Show how the ask connects several policy domains, but still translate the larger frame into one concrete decision.')
    else:
        moves.append('Lead with the decision and its evidence rather than betting the meeting on either novelty or tradition.')
    if c == 'high':
        moves.append('Provide sourced implementation detail, a realistic sequence, and a follow-through plan that can withstand scrutiny.')
    elif c == 'low':
        moves.append('Carry the implementation burden yourself: use a one-page leave-behind, a named owner, and a dated next step.')
    else:
        moves.append('Keep supporting detail available, while making ownership and the next step explicit.')
    if a == 'low':
        moves.append('Use direct, non-moralizing logic and respect disagreement; do not force a consensus frame the interview-language record does not support.')
    elif a == 'high':
        moves.append('Common-ground language is reasonable, but treat it as a communication fit rather than proof of bipartisan cooperation.')
    else:
        moves.append('Use a neutral, professional tone; the low-antagonism read is not strong enough to prescribe warmth or confrontation.')
    if dom == 'high':
        moves.append('Open with the bottom line, let the member shape part of the discussion, and return to one explicit decision before closing.')
    elif dom == 'low':
        moves.append('Ask a direct question, leave protected response time, and close by confirming the next action rather than filling the silence.')
    else:
        moves.append('Choose meeting format from actual scheduling and prior interactions—not from visibility, which says nothing about extroversion.')
    return ' '.join(moves)


def render_member(block):
    p1 = _working_style(block); p2 = _manner(block); p3 = _approach(block)
    text = ' '.join((p1, p2, p3)).lower()
    forbidden = (' extrovert', ' introvert', 'rapid-fire pace', 'verified-solo audio')
    if any(x in text for x in forbidden):
        raise AssertionError('forbidden legacy claim leaked into local narrative')
    return {'p1': p1, 'p2': p2, 'p3': p3, 'model': 'local-deterministic-fable-style',
            'usage': {'input_tokens': 0, 'output_tokens': 0}, 'ok': True,
            'measurement_note': ('Candidate v2: visibility is role exposure and intrinsic E remains unknown; '
                                 'A is low-antagonism, N is affective-content, and audio is double-reviewed.')}


def build(basis_path, only=None):
    with open(basis_path, encoding='utf-8') as f: basis = json.load(f)
    if basis.get('schema') not in ('profile-basis-v2-candidate','profile-basis-v2'):
        raise ValueError('basis schema must be profile-basis-v2 or profile-basis-v2-candidate')
    wanted = {x.strip() for x in (only or '').split(',') if x.strip()}
    members = {m: render_member(v) for m, v in sorted((basis.get('members') or {}).items())
               if not wanted or m in wanted}
    return {'schema': 'member-narratives-v2-local-candidate',
            'status': 'CANDIDATE_CONSUMED_BY_NOTHING_AWAITING_USER_RATIFICATION',
            'writer': 'deterministic local templates; zero API/network/model cost',
            'style_target': 'original Fable-era three-paragraph suggested-approach cadence',
            'basis_sha256': hashlib.sha256(open(basis_path, 'rb').read()).hexdigest(),
            'members': members}


def self_test():
    block = {
        'personality_facets': {
            'Openness': {'z': -1.2}, 'Conscientiousness': {'z': -0.8},
            'Extraversion': {'z': None, 'status': 'unknown_no_independent_measure'},
            'Agreeableness_low_antagonism': {'z': -0.9},
            'Neuroticism_affective_content': {'z': 0.1},
        },
        'public_role_exposure': {'visibility_z': 2.8, 'role_adjusted_visibility_z': 0.2},
        'observed_conversation': {'dominance_z': 1.1, 'expressiveness_z': -0.9},
        'behavioral_record': {},
        'verified_audio_v2': {
            'metrics': {
                'delivery_rate_wpm': {'min': 108, 'max': 173, 'stable_outer_quartile': None},
                'pitch_variability_raw': {'stable_outer_quartile': None},
                'volume_dynamics_raw': {'stable_outer_quartile': 'low_across_sources'},
            }
        },
    }
    out = render_member(block)
    assert all(out[k] for k in ('p1', 'p2', 'p3'))
    joined = ' '.join(out[k] for k in ('p1', 'p2', 'p3')).lower()
    assert 'public spotlight' in joined and 'personally outgoing disposition' in joined
    assert '108 to 173' in joined and 'not a stable personal label' in joined
    assert 'extrovert' not in joined and 'introvert' not in joined
    assert out['usage']['input_tokens'] == 0 and out['usage']['output_tokens'] == 0
    assert out['model'] == 'local-deterministic-fable-style'
    print('b5_narratives_local_candidate self-test: 6 passed, 0 FAILED')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--basis', help='data/members/profile_basis_v2.json.candidate')
    ap.add_argument('--out', help='candidate output path; required for a real build')
    ap.add_argument('--only', help='comma-separated member names')
    args = ap.parse_args()
    if args.self_test:
        self_test(); return
    if not args.basis or not args.out:
        ap.error('--basis and --out are required; candidate builds never write implicitly')
    basis = args.basis if os.path.isabs(args.basis) else os.path.join(MB_ROOT, args.basis)
    out = args.out if os.path.isabs(args.out) else os.path.join(MB_ROOT, args.out)
    doc = build(basis, args.only)
    dump_atomic(doc, out, indent=1)
    print('%s: %d local three-paragraph candidates; API tokens 0; consumed by nothing' %
          (os.path.basename(out), len(doc['members'])))


if __name__ == '__main__':
    main()
