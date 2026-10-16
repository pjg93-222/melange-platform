# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT, MELANGE_DATA, to_host
"""Build fail-closed, direct audio delivery measurements (candidate; consumed by NOTHING).

This replaces the narrative layer's unsupported proxy translations.  ``loudnessPeaksPerSec`` is not
treated as speaking speed; cross-source mean loudness, HNR character language, and jitter-as-tension
are omitted.  A clip is usable only after TWO distinct reviews confirm target identity, target-only
segments, and the segment word counts.  Pace is target transcript words / selected elapsed minutes.
Pitch variation and volume dynamics are measured only inside those target-only segments.

Real builds require explicit --review and --out paths.  The expected review artifact is documented by
validate_clip_review(); no live artifact is ever overwritten implicitly.  The resulting .candidate is
not read by production code until a separate user-ratified promotion.
"""
import argparse, hashlib, json, math, os, re, sys
from collections import defaultdict
import numpy as np
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from moneyball_core import dump_atomic
from audio_rules import (MIN_CLIP_TARGET_SEC, MIN_CLIP_TARGET_WORDS,
                         MIN_INDEPENDENT_PROGRAMS, MIN_INDEPENDENT_SOURCES,
                         MIN_MEMBER_DISTINCT_TOPICS, MIN_MEMBER_SPEECH_UNITS,
                         MIN_MEMBER_TARGET_SEC, MIN_SEGMENT_SEC, MIN_TOPIC_SPEECH_UNITS)

ADIR = os.path.join(MB_ROOT, 'audio_pilot')
PITCH_MEAN = 'F0semitoneFrom27.5Hz_sma3nz_amean'
PITCH_VAR = 'F0semitoneFrom27.5Hz_sma3nz_stddevNorm'
VOLUME_DYN = 'loudness_sma3_stddevNorm'
MIN_COHORT_FOR_PERCENTILES = 80
MIN_PROGRAM_MEMBERS_FOR_PERCENTILES = 12
SHA_RE = re.compile(r'^[0-9a-f]{64}$')


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _manifest_rows(review_doc):
    """Load a hash-pinned accepted manifest for candidate rows, keyed by canonical row hash."""
    source = review_doc.get('source_manifest')
    if not source:
        return None
    if not isinstance(source, dict):
        raise ValueError('source_manifest is not an object')
    path = str(source.get('path') or '')
    expected = str(source.get('sha256') or '').lower()
    if not path or not SHA_RE.match(expected) or not os.path.isfile(path):
        raise ValueError('manifest-bound review has a missing/invalid source manifest')
    if _sha256(path).lower() != expected:
        raise ValueError('accepted clip manifest SHA256 no longer matches the review candidate')
    rows = {}
    with open(path, encoding='utf-8') as handle:
        for number,line in enumerate(handle,1):
            if not line.strip(): continue
            try: row=json.loads(line)
            except ValueError as exc: raise ValueError('invalid accepted manifest line %d'%number) from exc
            digest=hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,
                separators=(',',':')).encode('utf-8')).hexdigest()
            if digest in rows: raise ValueError('duplicate canonical accepted-manifest row hash')
            rows[digest]=row
    if int(source.get('row_n') or -1)!=len(rows):
        raise ValueError('accepted manifest row count no longer matches review candidate')
    return rows


def _manifest_clip(raw, manifest_rows):
    binding=raw.get('manifest_binding')
    if not binding:
        return None,None,[]
    if not isinstance(binding,dict):
        return None,None,['manifest_binding is not an object']
    errors=[]
    row=(manifest_rows or {}).get(str(binding.get('source_row_sha256') or '').lower())
    if row is None:
        return None,None,['manifest binding row is missing or changed']
    checks={'source_artifact_id':'artifact_id','turn_id':'turn_id','out_wav':'out_wav',
            'audio_sha256':'sha256','frames':'frames','samplerate':'samplerate',
            'transcript_path':'transcript_path','text_sha256':'text_sha256',
            'source_audio_sha256':'source_audio_sha256'}
    for stored,source in checks.items():
        if binding.get(stored)!=row.get(source): errors.append('manifest binding mismatch: '+stored)
    path=os.path.realpath(to_host(str(row.get('out_wav') or '')))
    allowed=os.path.realpath(MELANGE_DATA)
    try:
        if os.path.commonpath((path,allowed))!=allowed: errors.append('manifest audio path escapes accepted root')
    except ValueError:
        errors.append('manifest audio path is invalid')
    return row,path,errors


def validate_clip_review(rel, row):
    """Validate the complete double-review contract; return normalized segments."""
    errors = []
    if not isinstance(row, dict):
        return None, ['row is not an object']
    source_id = str(row.get('source_id') or '').strip()
    source_url = str(row.get('source_url') or '').strip()
    venue = str(row.get('venue') or '').strip().lower()
    outlet = str(row.get('outlet') or '').strip().lower()
    program = str(row.get('program') or '').strip().lower()
    aired_at = str(row.get('aired_at') or '').strip()
    recording_context = str(row.get('recording_context') or '').strip()
    speech_id = str(row.get('speech_id') or '').strip()
    if not source_id: errors.append('source_id missing')
    if not source_url.startswith(('https://', 'http://')): errors.append('source_url missing/invalid')
    if venue != 'on_air_interview': errors.append('venue must be on_air_interview')
    if not outlet: errors.append('outlet missing')
    if not program: errors.append('program missing')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', aired_at): errors.append('aired_at missing/invalid')
    if not recording_context: errors.append('recording_context missing')
    if not speech_id: errors.append('speech_id missing')
    topic = row.get('topic')
    if topic and not isinstance(topic, dict): errors.append('topic is not an object')
    if not isinstance(topic, dict): topic = {}
    topic_id = str(topic.get('id') or '').strip()
    topic_label = str(topic.get('label') or '').strip()
    topic_method = str(topic.get('method') or '').strip()
    topic_transcript_sha = str(topic.get('transcript_sha256') or '').lower()
    topic_spec_sha = str(topic.get('model_or_dictionary_sha256') or '').lower()
    if not topic_id: errors.append('topic id missing')
    if not topic_label: errors.append('topic label missing')
    if topic_method not in ('closed_caption_dictionary','structural_topic_model'):
        errors.append('topic method must be closed_caption_dictionary or structural_topic_model')
    if not SHA_RE.match(topic_transcript_sha): errors.append('topic transcript_sha256 invalid')
    if not SHA_RE.match(topic_spec_sha): errors.append('topic model_or_dictionary_sha256 invalid')
    reviews = row.get('reviews') or []
    if len(reviews) != 2:
        errors.append('exactly two reviews required')
    reviewer_ids = []
    for i, review in enumerate(reviews):
        if not isinstance(review, dict):
            errors.append('review %d is not an object' % (i + 1)); continue
        rid = str(review.get('reviewer') or '').strip()
        reviewer_ids.append(rid)
        if not rid: errors.append('review %d reviewer missing' % (i + 1))
        reviewed_at = str(review.get('reviewed_at') or '').strip()
        review_method = str(review.get('review_method') or '').strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', reviewed_at):
            errors.append('review %d reviewed_at missing/invalid' % (i + 1))
        if not review_method: errors.append('review %d review_method missing' % (i + 1))
        if review.get('identity') != 'TARGET': errors.append('review %d identity not TARGET' % (i + 1))
        if review.get('speaker') != 'TARGET_ONLY': errors.append('review %d speaker not TARGET_ONLY' % (i + 1))
        if review.get('segments_confirmed') is not True: errors.append('review %d segments unconfirmed' % (i + 1))
        if review.get('word_counts_confirmed') is not True: errors.append('review %d word counts unconfirmed' % (i + 1))
        if review.get('transcript_alignment_confirmed') is not True: errors.append('review %d transcript alignment unconfirmed' % (i + 1))
        if review.get('interviewer_excluded') is not True: errors.append('review %d interviewer not excluded' % (i + 1))
        if review.get('speech_unit_confirmed') is not True: errors.append('review %d speech unit unconfirmed' % (i + 1))
        if review.get('topic_from_aligned_transcript_confirmed') is not True:
            errors.append('review %d transcript topic unconfirmed' % (i + 1))
        if not str(review.get('identity_evidence') or '').strip(): errors.append('review %d identity evidence missing' % (i + 1))
    if len(reviewer_ids) == 2 and (not all(reviewer_ids) or reviewer_ids[0] == reviewer_ids[1]):
        errors.append('reviewers must be distinct')
    segments = row.get('segments') or []
    norm = []
    for i, seg in enumerate(segments):
        try:
            start = float(seg['start_sec']); end = float(seg['end_sec']); words = int(seg['word_count'])
        except (KeyError, TypeError, ValueError):
            errors.append('segment %d malformed' % (i + 1)); continue
        tsha = str(seg.get('transcript_sha256') or '').lower()
        if start < 0 or end - start < MIN_SEGMENT_SEC: errors.append('segment %d too short/invalid' % (i + 1))
        if words < 0: errors.append('segment %d negative word count' % (i + 1))
        if not SHA_RE.match(tsha): errors.append('segment %d transcript_sha256 invalid' % (i + 1))
        norm.append({'start_sec': start, 'end_sec': end, 'word_count': words,
                     'transcript_sha256': tsha})
    if not norm: errors.append('no valid target segments')
    if sum(s['end_sec'] - s['start_sec'] for s in norm) < MIN_CLIP_TARGET_SEC:
        errors.append('clip has < %ds confirmed target audio' % int(MIN_CLIP_TARGET_SEC))
    if sum(s['word_count'] for s in norm) < MIN_CLIP_TARGET_WORDS:
        errors.append('clip has < %d aligned target words' % MIN_CLIP_TARGET_WORDS)
    if errors:
        return None, errors
    return {'source_id': source_id, 'source_url': source_url, 'venue': venue,
            'outlet': outlet, 'program': program, 'program_key': outlet + '|' + program,
            'aired_at': aired_at, 'recording_context': recording_context,
            'speech_id': speech_id,
            'topic': {'id':topic_id,'label':topic_label,'method':topic_method,
                      'transcript_sha256':topic_transcript_sha,
                      'model_or_dictionary_sha256':topic_spec_sha},
            'segments': norm, 'reviewers': reviewer_ids}, []


def _smile():
    import opensmile
    return opensmile.Smile(feature_set=opensmile.FeatureSet.eGeMAPSv02,
                           feature_level=opensmile.FeatureLevel.Functionals)


def measure_clip(path, review, smile=None):
    """Measure one confirmed speech unit, concatenating only its target-speaker segments."""
    import soundfile as sf
    sig, sr = sf.read(path, always_2d=False)
    if getattr(sig, 'ndim', 1) > 1:
        sig = np.asarray(sig, float).mean(axis=1)
    else:
        sig = np.asarray(sig, float)
    smile = smile or _smile()
    parts = []
    total_sec = 0.0; total_words = 0
    for seg in review['segments']:
        a = max(0, int(round(seg['start_sec'] * sr)))
        b = int(round(seg['end_sec'] * sr))
        if b > len(sig) + 1: raise ValueError('segment exceeds audio length')
        b = min(len(sig), b)
        if b <= a: raise ValueError('segment outside audio bounds')
        part = sig[a:b]; parts.append(part)
        total_sec += len(part) / float(sr); total_words += seg['word_count']
    target=np.concatenate(parts)
    frame=smile.process_signal(target,sr).iloc[0]
    vals={key:float(frame[key]) for key in (PITCH_MEAN,PITCH_VAR,VOLUME_DYN)}
    if not all(math.isfinite(value) for value in vals.values()):
        raise ValueError('non-finite acoustic value')
    # eGeMAPS expresses F0 as semitones above 27.5 Hz.  Convert the speech-unit mean to Hz before
    # computing the within-speaker baseline/deviation used by Dietrich, Hayes, and O'Brien.
    mean_f0_hz=27.5*(2.0**(vals[PITCH_MEAN]/12.0))
    return {
        'target_elapsed_sec': round(total_sec, 3),
        'target_words': total_words,
        'delivery_rate_wpm': round(60.0 * total_words / total_sec, 3),
        'mean_f0_semitone':round(vals[PITCH_MEAN],8),'mean_f0_hz':round(mean_f0_hz,8),
        'pitch_variability_raw': round(vals[PITCH_VAR], 8),
        'volume_dynamics_raw': round(vals[VOLUME_DYN], 8),
    }


def _speaker_pitch_profile(rows):
    """Return speech-level z deviations and topic summaries, never an absolute-pitch trait."""
    if len(rows)<MIN_MEMBER_SPEECH_UNITS:
        return None,'fewer than %d confirmed speech units'%MIN_MEMBER_SPEECH_UNITS
    topics={row['topic']['id'] for row in rows}
    if len(topics)<MIN_MEMBER_DISTINCT_TOPICS:
        return None,'fewer than %d transcript-identified topics'%MIN_MEMBER_DISTINCT_TOPICS
    values=np.asarray([row['mean_f0_hz'] for row in rows],float)
    baseline=float(values.mean()); sd=float(values.std(ddof=1))
    if not math.isfinite(sd) or sd<1e-9:
        return None,'speaker baseline has zero/non-finite pitch dispersion'
    enriched=[]
    for row,value in zip(rows,values):
        item=dict(row); item['pitch_deviation_from_speaker_baseline_z']=round(float((value-baseline)/sd),6)
        enriched.append(item)
    topic_summary={}
    for topic_id in sorted(topics):
        selected=[row for row in enriched if row['topic']['id']==topic_id]
        if len(selected)<MIN_TOPIC_SPEECH_UNITS:
            continue
        deviations=[row['pitch_deviation_from_speaker_baseline_z'] for row in selected]
        topic_summary[topic_id]={
            'label':selected[0]['topic']['label'],'speech_units':len(selected),
            'mean_pitch_deviation_z':round(float(np.mean(deviations)),6),
            'minimum_pitch_deviation_z':round(float(min(deviations)),6),
            'maximum_pitch_deviation_z':round(float(max(deviations)),6),
            'meaning':'relative emotional intensity for this speaker/topic; positive is above the speaker baseline',
        }
    return {
        'mean_f0_hz':round(baseline,6),'sd_f0_hz':round(sd,6),'speech_units':len(rows),
        'distinct_topics':len(topics),'standardization':'(speech mean F0 - speaker mean F0) / speaker sample SD',
        'absolute_f0_enters_personality':False,
    },(enriched,topic_summary)


def _percentile(value, population):
    vals = np.sort(np.asarray(population, float))
    if len(vals) <= 1: return None
    return round(100.0 * float(np.searchsorted(vals, value, side='left')) / (len(vals) - 1), 2)


def _stable_band(percentiles):
    """Only label a cross-source extreme when every source agrees on the same outer quartile."""
    if not percentiles or any(p is None for p in percentiles): return None
    if min(percentiles) >= 75.0: return 'high_across_sources'
    if max(percentiles) <= 25.0: return 'low_across_sources'
    return None


def build(review_path, only=None, review_schema='b5-audio-review-v2-candidate',
          output_schema='b5-audio-descriptors-v2-candidate',
          status='CANDIDATE_CONSUMED_BY_NOTHING_AWAITING_USER_RATIFICATION'):
    with open(review_path, encoding='utf-8') as f:
        review_doc = json.load(f)
    if review_doc.get('schema') != review_schema:
        raise ValueError('review schema must be ' + review_schema)
    manifest_rows=_manifest_rows(review_doc)
    clip_rows = review_doc.get('clips') or {}
    wanted = {x.strip() for x in (only or '').split(',') if x.strip()}
    accepted = {}; rejected = {}; smile = None
    for rel, raw in sorted(clip_rows.items()):
        if not isinstance(raw, dict):
            rejected[rel] = ['row is not an object']; continue
        member = str(raw.get('member') or rel.replace('\\', '/').split('/')[0]).strip()
        if wanted and member not in wanted: continue
        review, errors = validate_clip_review(rel, raw)
        manifest_row,manifest_path,manifest_errors=_manifest_clip(raw,manifest_rows)
        errors.extend(manifest_errors)
        path = manifest_path or os.path.join(ADIR, *rel.replace('\\', '/').split('/'))
        if not os.path.exists(path): errors.append('audio file missing')
        elif manifest_row is not None and _sha256(path)!=manifest_row.get('sha256'):
            errors.append('manifest-bound audio SHA256 mismatch')
        if errors:
            rejected[rel] = errors; continue
        try:
            smile = smile or _smile()
            measured = measure_clip(path, review, smile)
            accepted[rel] = {**review, **measured, 'audio_sha256': _sha256(path),
                             'member':member,'manifest_binding':raw.get('manifest_binding')}
        except Exception as e:
            rejected[rel] = ['measurement failed: ' + str(e)[:160]]
    by_member = defaultdict(list)
    for rel, row in accepted.items(): by_member[row.get('member') or rel.replace('\\', '/').split('/')[0]].append(row)
    eligible={}; member_exclusions={}; pitch_profiles={}
    for member,rows in by_member.items():
        reasons=[]
        if sum(r['target_elapsed_sec'] for r in rows)<MIN_MEMBER_TARGET_SEC: reasons.append('target seconds below member floor')
        if len({r['source_id'] for r in rows})<MIN_INDEPENDENT_SOURCES: reasons.append('independent source floor not met')
        if len({r['program_key'] for r in rows})<MIN_INDEPENDENT_PROGRAMS: reasons.append('independent program floor not met')
        baseline,pitch_result=_speaker_pitch_profile(rows)
        if baseline is None: reasons.append(pitch_result)
        if reasons:
            member_exclusions[member]=reasons; continue
        enriched,topic_summary=pitch_result
        eligible[member]=enriched; pitch_profiles[member]=(baseline,topic_summary)
    cohort_ok = len(eligible) >= MIN_COHORT_FOR_PERCENTILES
    program_members = defaultdict(set); program_values = defaultdict(lambda: defaultdict(list))
    for member, rows in eligible.items():
        for row in rows:
            pk = row['program_key']; program_members[pk].add(member)
            for key in ('delivery_rate_wpm', 'pitch_variability_raw', 'volume_dynamics_raw'):
                program_values[pk][key].append(float(row[key]))
    qualified_programs = {pk for pk, ms in program_members.items()
                          if len(ms) >= MIN_PROGRAM_MEMBERS_FOR_PERCENTILES}
    members = {}
    for member, rows in sorted(eligible.items()):
        pitch_baseline,topic_summary=pitch_profiles[member]
        sources = sorted({r['source_id'] for r in rows}); venues = sorted({r['venue'] for r in rows})
        programs = sorted({r['program_key'] for r in rows}); outlets = sorted({r['outlet'] for r in rows})
        metrics = {}
        for key in ('delivery_rate_wpm', 'pitch_variability_raw', 'volume_dynamics_raw'):
            vals = [r[key] for r in rows]
            pcts = [(_percentile(v, program_values[r['program_key']][key])
                     if cohort_ok and r['program_key'] in qualified_programs else None)
                    for r, v in zip(rows, vals)]
            metrics[key] = {
                'median': round(float(np.median(vals)), 4),
                'min': round(float(min(vals)), 4),
                'max': round(float(max(vals)), 4),
                'source_percentiles': pcts,
                'percentile_basis': 'within outlet+program only; unavailable for program cohorts below %d members' % MIN_PROGRAM_MEMBERS_FOR_PERCENTILES,
                'stable_outer_quartile': _stable_band(pcts),
            }
        members[member] = {
            'target_elapsed_sec': round(sum(r['target_elapsed_sec'] for r in rows), 3),
            'source_n': len(sources), 'sources': sources,
            'venue_n': len(venues), 'venues': venues,
            'program_n': len(programs), 'programs': programs,
            'outlet_n': len(outlets), 'outlets': outlets,
            'cross_program': len(programs) >= MIN_INDEPENDENT_PROGRAMS,
            'metrics': metrics,
            'pitch_baseline':pitch_baseline,
            'topic_emotional_intensity':topic_summary,
            'clips': rows,
            'narrative_contract': {
                'generalize_across_venues_only_if_cross_venue': True,
                'use_high_low_label_only_if_stable_outer_quartile_present': True,
                'never_infer_personality_mood_health_or_sincerity': True,
                'audio_may_modify_big_five': False,
            },
        }
    return {
        'schema': output_schema,
        'status': status,
        'measurement_contract': {
            'delivery_rate_wpm': 'confirmed target transcript words / confirmed target elapsed minutes; includes pauses',
            'pitch_variability_raw': PITCH_VAR + ' on confirmed target-only segments',
            'mean_f0_hz':'speech-unit mean F0 converted from eGeMAPS semitones; used only for within-speaker standardization',
            'pitch_deviation_from_speaker_baseline_z':'speech-unit mean F0 minus member mean, divided by member speech-unit sample SD',
            'topic_emotional_intensity':'mean within-speaker pitch deviation by transcript-identified topic; minimum %d speech units per reported topic'%MIN_TOPIC_SPEECH_UNITS,
            'volume_dynamics_raw': VOLUME_DYN + ' on confirmed target-only segments',
            'explicitly_excluded': ['loudnessPeaksPerSec as pace', 'cross-source mean loudness',
                                    'HNR character inference', 'jitter as tension or emotional tell',
                                    'absolute F0 or inferred gender as a personality input',
                                    'CNN apparent-personality labels as actual Big Five',
                                    'MFCC/chroma/spectral trait inference without political-domain validation'],
            'two_distinct_reviews_per_clip': True,
            'minimum_independent_sources_per_member': MIN_INDEPENDENT_SOURCES,
            'minimum_independent_programs_per_member': MIN_INDEPENDENT_PROGRAMS,
            'minimum_target_seconds_per_member': MIN_MEMBER_TARGET_SEC,
            'minimum_speech_units_per_member':MIN_MEMBER_SPEECH_UNITS,
            'minimum_distinct_topics_per_member':MIN_MEMBER_DISTINCT_TOPICS,
            'topic_identification_source':'reviewer-confirmed aligned transcript/closed captions, never title or Congressional Record rewrite',
            'allowed_topic_methods':['closed_caption_dictionary','structural_topic_model'],
            'minimum_aligned_target_words_per_clip': MIN_CLIP_TARGET_WORDS,
            'cohort_minimum_for_percentile_labels': MIN_COHORT_FOR_PERCENTILES,
            'program_member_minimum_for_percentile_labels': MIN_PROGRAM_MEMBERS_FOR_PERCENTILES,
            'percentile_normalization': 'within outlet+program; never raw cross-speaker pitch/energy',
            'on_air_interviews_only': True,
            'audio_may_modify_big_five': False,
        },
        'review_sha256': _sha256(review_path),
        'source_manifest':review_doc.get('source_manifest'),
        'cohort_percentiles_available': cohort_ok,
        'accepted_clip_n': len(accepted), 'rejected_clip_n': len(rejected),
        'rejected': rejected, 'member_exclusions':member_exclusions,'members': members,
    }


def self_test():
    good = {
        'source_id': 'source-a', 'source_url': 'https://example.test/a', 'venue': 'on_air_interview',
        'outlet': 'example network', 'program': 'example show', 'aired_at': '2026-07-13',
        'recording_context': 'broadcast interview, studio microphone, compressed stream',
        'speech_id':'speech-a','topic':{'id':'housing','label':'Housing','method':'closed_caption_dictionary',
            'transcript_sha256':'b'*64,'model_or_dictionary_sha256':'c'*64},
        'segments': [{'start_sec': 0, 'end_sec': 60, 'word_count': 120,
                      'transcript_sha256': 'a' * 64}],
        'reviews': [
            {'reviewer': 'reviewer-1', 'identity': 'TARGET', 'speaker': 'TARGET_ONLY',
              'reviewed_at': '2026-07-13', 'review_method': 'audio plus official video',
              'segments_confirmed': True, 'word_counts_confirmed': True,
              'transcript_alignment_confirmed': True, 'interviewer_excluded': True,
              'speech_unit_confirmed':True,'topic_from_aligned_transcript_confirmed':True,
              'identity_evidence': 'direct introduction'},
            {'reviewer': 'reviewer-2', 'identity': 'TARGET', 'speaker': 'TARGET_ONLY',
              'reviewed_at': '2026-07-13', 'review_method': 'independent transcript and video check',
              'segments_confirmed': True, 'word_counts_confirmed': True,
              'transcript_alignment_confirmed': True, 'interviewer_excluded': True,
              'speech_unit_confirmed':True,'topic_from_aligned_transcript_confirmed':True,
              'identity_evidence': 'official source'},
        ],
    }
    norm, errors = validate_clip_review('Example/clip_a.wav', good)
    assert not errors and norm['segments'][0]['word_count'] == 120
    same = json.loads(json.dumps(good)); same['reviews'][1]['reviewer'] = 'reviewer-1'
    assert 'reviewers must be distinct' in validate_clip_review('x', same)[1]
    multi = json.loads(json.dumps(good)); multi['reviews'][0]['speaker'] = 'MULTI_SPEAKER'
    assert any('speaker not TARGET_ONLY' in x for x in validate_clip_review('x', multi)[1])
    bad_sha = json.loads(json.dumps(good)); bad_sha['segments'][0]['transcript_sha256'] = 'nope'
    assert any('transcript_sha256' in x for x in validate_clip_review('x', bad_sha)[1])
    short = json.loads(json.dumps(good)); short['segments'][0]['end_sec'] = 20
    assert any('confirmed target audio' in x for x in validate_clip_review('x', short)[1])
    too_few_words = json.loads(json.dumps(good)); too_few_words['segments'][0]['word_count'] = 49
    assert any('aligned target words' in x for x in validate_clip_review('x', too_few_words)[1])
    bad_venue = json.loads(json.dumps(good)); bad_venue['venue'] = 'floor'
    assert any('on_air_interview' in x for x in validate_clip_review('x', bad_venue)[1])
    no_align = json.loads(json.dumps(good)); no_align['reviews'][0]['transcript_alignment_confirmed'] = False
    assert any('alignment unconfirmed' in x for x in validate_clip_review('x', no_align)[1])
    no_topic = json.loads(json.dumps(good)); no_topic['topic'] = {}
    assert any('topic' in x for x in validate_clip_review('x', no_topic)[1])
    pitch_rows=[]
    for i,value in enumerate((100,105,110,115,120)):
        row={'mean_f0_hz':value,'topic':{'id':'a' if i<3 else 'b','label':'A' if i<3 else 'B'}}
        pitch_rows.append(row)
    baseline,result=_speaker_pitch_profile(pitch_rows)
    assert baseline['speech_units']==5 and len(result[1])==2
    assert _stable_band([76, 88]) == 'high_across_sources'
    assert _stable_band([12, 24]) == 'low_across_sources'
    assert _stable_band([20, 80]) is None
    print('b5_audio_descriptors_candidate self-test: 14 passed, 0 FAILED')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--active', action='store_true',
                    help='explicitly rebuild the fail-closed active v2 artifact from an active v2 review')
    ap.add_argument('--review', help='double-reviewed candidate JSON')
    ap.add_argument('--out', help='candidate output JSON; required for a real build')
    ap.add_argument('--only', help='comma-separated member names')
    args = ap.parse_args()
    if args.self_test:
        self_test(); return
    if not args.review or not args.out:
        ap.error('--review and --out are required; candidate builds never write implicitly')
    review_path = args.review if os.path.isabs(args.review) else os.path.join(MB_ROOT, args.review)
    out_path = args.out if os.path.isabs(args.out) else os.path.join(MB_ROOT, args.out)
    if args.active:
        doc = build(review_path, args.only,
                    review_schema='b5-audio-review-v2',
                    output_schema='b5-audio-descriptors-v2',
                    status='ACTIVE_FAIL_CLOSED')
        doc['deployment_contract'] = {
            'legacy_b5_audio_cache_fallback': False,
            'legacy_model_only_verification_fallback': False,
            'unverified_audio_enters_profiles': False,
            'zero_verified_members_is_valid': True,
            'on_air_interviews_only': True,
            'program_conditioned_percentiles': True,
            'within_speaker_pitch_deviation':True,
            'transcript_identified_topics':True,
            'audio_may_modify_big_five': False,
        }
    else:
        doc = build(review_path, args.only)
    dump_atomic(doc, out_path, indent=1)
    print('%s: %d verified members, %d accepted clips, %d rejected; consumed by nothing' %
          (os.path.basename(out_path), len(doc['members']), doc['accepted_clip_n'], doc['rejected_clip_n']))


if __name__ == '__main__':
    main()
