# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""Locked sampling-frame audit for the Round-9 validated inverse-antagonism A facet.

Default mode verifies frozen inputs without calculating group differences. ``--look`` is the
single preregistered coverage look; it never reads antagonism values, fits scores, or changes the
engine. See ``big5_a_coverage_prereg.md``.
"""
import argparse, hashlib, json, math, statistics
_s.stdout.reconfigure(encoding='utf-8', errors='replace')
from moneyball_core import dump_atomic

B = MB_ROOT
PREREG = B + r'\big5_a_coverage_prereg.md'
OUT = B + r'\data\stance\interview_antagonism_coverage_audit.json'
PINS = {
    'candidate': (B + r'\data\stance\interview_antagonism_halves.json.candidate', '15FFB00BA199708C17733260205E0580CE766F3B37E0C69A74965B35B4AB0575'),
    'desk': (B + r'\member_desk_bundle.json', '34879F3397DEA3FA4E1203F61EBF7539DBA58F488C3335F3FC6332E37486416C'),
    'behavior': (B + r'\behavioral_dispositions.json', '57CA1E2B1731CBEED4411645C2B527A8DB0136362C2D3D4654DDA8FAAA852E03'),
    'blue': (B + r'\data\members\big5_v2.json', '385F7FC0881B624E938E7E860DFE593AF0E3C640104B6BCF2DE85C01F3341AC7'),
}

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest().upper()

def smd(a, b):
    if len(a) < 2 or len(b) < 2: return None
    va, vb = statistics.variance(a), statistics.variance(b)
    pooled = math.sqrt(((len(a)-1)*va + (len(b)-1)*vb) / (len(a)+len(b)-2))
    return (statistics.mean(a) - statistics.mean(b)) / pooled if pooled else None

def metric(name, values, included):
    a = [float(v) for m, v in values.items() if m in included and v is not None]
    b = [float(v) for m, v in values.items() if m not in included and v is not None]
    return {'metric': name, 'n_in': len(a), 'n_out': len(b),
            'mean_in': round(statistics.mean(a), 6) if a else None,
            'mean_out': round(statistics.mean(b), 6) if b else None,
            'smd': round(smd(a, b), 6) if smd(a, b) is not None else None}

def group_rates(members, included, key, levels):
    out = {}
    for level in levels:
        names = [m['name'] for m in members if key(m) == level]
        out[level] = {'n': len(names), 'covered': sum(n in included for n in names),
                      'rate': round(sum(n in included for n in names) / len(names), 6) if names else None}
    rates = [v['rate'] for v in out.values() if v['rate'] is not None]
    return out, round(100 * (max(rates)-min(rates)), 3) if rates else None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--look', action='store_true'); args = ap.parse_args()
    bad = [k for k,(p,h) in PINS.items() if not _o.path.exists(p) or sha(p) != h]
    if bad: raise SystemExit('INPUT DRIFT - coverage look refused: ' + ','.join(bad))
    print('A-coverage frozen inputs: MATCH')
    if not args.look:
        print('read-only pin check complete; --look required for the single locked coverage audit')
        return
    prereg = open(PREREG, encoding='utf-8').read()
    if 'STATUS: LOCKED - coverage look not yet taken.' not in prereg:
        raise SystemExit('LOOK REFUSED: prereg not armed or result already consumed')
    candidate = json.load(open(PINS['candidate'][0], encoding='utf-8'))
    included = set(candidate.get('members', {}))  # names only; criterion values remain unread
    desk = json.load(open(PINS['desk'][0], encoding='utf-8'))['members']
    bd = json.load(open(PINS['behavior'][0], encoding='utf-8'))['members']
    blue = json.load(open(PINS['blue'][0], encoding='utf-8'))['traits']
    roster = {m['name']: m for m in desk}
    if len(roster) != 533 or len(included) != 322 or not included <= set(roster):
        raise RuntimeError('frozen roster/coverage contract broken')
    vals = {
        'Republican': {n: 1 if m.get('party') == 'Republican' else 0 for n,m in roster.items()},
        'Female': {n: (1 if (m.get('bio') or {}).get('gender') == 'F' else 0)
                   if (m.get('bio') or {}).get('gender') in ('F','M') else None for n,m in roster.items()},
        'Senate': {n: 1 if m.get('chamber') == 'Senate' else 0 for n,m in roster.items()},
        'composite_score': {n: m.get('score') for n,m in roster.items()},
        'legislative_pillar': {n: m.get('leg_n') for n,m in roster.items()},
        'influence_pillar': {n: m.get('comm_n') for n,m in roster.items()},
        'demographic_pillar': {n: m.get('demo_n') for n,m in roster.items()},
        'log_articles': {n: math.log1p(len(m.get('articles') or [])) for n,m in roster.items()},
        'press_pillar': {n: m.get('press_n') for n,m in roster.items()},
        'visibility_z': {n: (bd.get(n) or {}).get('visibility_z') for n in roster},
        'log_blue_tokens': {n: math.log1p((blue.get(n) or {}).get('tokens', 0)) for n in roster},
    }
    blocks = {
        'demographic': [metric(k, vals[k], included) for k in ('Republican','Female','Senate')],
        'model_position': [metric(k, vals[k], included) for k in
                           ('composite_score','legislative_pillar','influence_pillar','demographic_pillar')],
        'media_activity': [metric(k, vals[k], included) for k in
                           ('log_articles','press_pillar','visibility_z','log_blue_tokens')],
    }
    def maxabs(block): return max(abs(x['smd']) for x in block if x['smd'] is not None)
    demo_max, model_max, media_max = (maxabs(blocks[k]) for k in
                                      ('demographic','model_position','media_activity'))
    rates = {}; gaps = {}
    rates['party'], gaps['party_pp'] = group_rates(desk, included, lambda m:m.get('party'),
                                                    ['Democrat','Republican','Independent'])
    rates['gender'], gaps['gender_pp'] = group_rates(desk, included, lambda m:(m.get('bio') or {}).get('gender') or 'Unknown',
                                                      ['F','M','Unknown'])
    rates['chamber'], gaps['chamber_pp'] = group_rates(desk, included, lambda m:m.get('chamber'), ['House','Senate'])
    demographic = 'MATERIAL FAILURE' if demo_max > .35 else ('CAUTION' if demo_max > .20 else 'CLEAN')
    model_position = 'CAUTION' if model_max > .20 else 'CLEAN'
    media_activity = 'FAIL' if media_max > .35 else 'CLEAN'
    if demographic == 'MATERIAL FAILURE': verdict = 'DEMOGRAPHICALLY SELECTED MEDIA-COVERED STRATUM'
    elif media_activity == 'FAIL': verdict = 'MEDIA-COVERED STRATUM ONLY'
    elif model_position == 'CAUTION': verdict = 'MODEL-POSITION-SELECTED STRATUM'
    else: verdict = 'OBSERVED BALANCE BARS CLEAR; NO IMPUTATION AUTHORIZED'
    result = {'status':'CONSUMED', 'n_roster':len(roster), 'n_included':len(included),
              'n_excluded':len(roster)-len(included), 'blocks':blocks,
              'max_abs_smd':{'demographic':round(demo_max,6),'model_position':round(model_max,6),'media_activity':round(media_max,6)},
              'gates':{'demographic':demographic,'model_position':model_position,'media_activity':media_activity},
              'coverage_rates':rates, 'max_rate_gaps_pp':gaps, 'verdict':verdict,
              'consequence':'scope only; no score/rubric/engine/rank change; no imputation',
              'input_sha256':{k:h for k,(_p,h) in PINS.items()}}
    dump_atomic(result, OUT, indent=1)
    print('A-COVERAGE LOOK: n=%d/%d | demo max |SMD|=%.3f %s | model=%.3f %s | media=%.3f %s' %
          (len(included),len(roster),demo_max,demographic,model_max,model_position,media_max,media_activity))
    for block, rows in blocks.items():
        print(' ',block,':','; '.join('%s %+.3f'%(x['metric'],x['smd']) for x in rows))
    print(' coverage gaps pp:', gaps)
    print(' VERDICT:', verdict)

if __name__ == '__main__': main()
