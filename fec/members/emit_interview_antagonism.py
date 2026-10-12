# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""Promote the Round-9 5/5-qualified interview-antagonism criterion to a display-only A facet.

Writes ``interview_antagonism.json`` with controlled antagonism and inverse Agreeableness scores.
This does not replace cooperation_v2 in the engine and does not change ranks. User ratified the
promotion on 2026-07-12: keep affective-N; make the validated A facet available honestly.
"""
import csv, hashlib, json
import numpy as np
_s.stdout.reconfigure(encoding='utf-8', errors='replace')
import moneyball_core as MC
from moneyball_core import dump_atomic

B = MB_ROOT
SRC = B + r'\data\stance\interview_antagonism_halves.json.candidate'
OUT = B + r'\data\stance\interview_antagonism.json'

def main():
    source_bytes = open(SRC, 'rb').read()
    source_sha = hashlib.sha256(source_bytes).hexdigest().upper()
    raw = json.loads(source_bytes.decode('utf-8'))
    if source_sha != '15FFB00BA199708C17733260205E0580CE766F3B37E0C69A74965B35B4AB0575':
        raise RuntimeError('Round-9 source fingerprint drift')
    rows = []
    for name, rec in raw.get('members', {}).items():
        a, b = rec['halves']['A'], rec['halves']['B']
        rows.append((name, (float(a['antagonism']) + float(b['antagonism'])) / 2,
                     float(a['antagonism']), float(b['antagonism']), sum(rec['words'])))
    rows.sort(); names = [r[0] for r in rows]
    if len(rows) < 250: raise RuntimeError('qualified criterion coverage collapsed below 250')
    party = MC.party(); b2n = MC.bio2name(); gender = {}
    with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            m = b2n.get(row.get('bioguide_id'))
            if m: gender[m] = row.get('gender', '')
    y = np.array([r[1] for r in rows], float)
    X = np.column_stack([np.ones(len(rows)),
                         [1.0 if party.get(m) == 'R' else 0.0 for m in names],
                         [1.0 if gender.get(m) == 'F' else 0.0 for m in names],
                         np.log1p([r[4] for r in rows])])
    res = y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
    antz = (res - res.mean()) / (res.std(ddof=1) or 1.0)
    members = {}
    for i, (name, mean, h1, h2, words) in enumerate(rows):
        members[name] = {'antagonism_z': round(float(antz[i]), 3),
                         'agreeableness_z': round(float(-antz[i]), 3),
                         'raw_antagonism': round(mean, 3), 'half_a': round(h1, 3),
                         'half_b': round(h2, 3), 'words': int(words)}
    dump_atomic({'members': members, 'n': len(members),
                 'basis': 'validated A-antagonism facet: blinded package-split interview behavior; controlled for party, gender, log words',
                 'gate': 'Round-9 criterion QUALIFIED 5/5 on 2026-07-12; see big5_a_interview_prereg.md',
                 'reliability_sb': 0.699, 'hearing_convergence_r': 0.160,
                 'source': {'file': 'data/stance/interview_antagonism_halves.json.candidate',
                            'sha256': source_sha, 'model': raw.get('model'),
                            'rubric_version': raw.get('rubric_version')},
                 'controls': {'role': 'residualization-only confound controls; never predictive A features',
                              'fields': ['party_R', 'female', 'log_interview_words']},
                 'scope': {'label': 'interview/media-covered stratum only',
                           'covered_n': len(members), 'roster_n': 533,
                           'percentile_reference': 'covered members only; not a full-roster percentile',
                           'imputation': 'forbidden'},
                 'selection_audit': {'prereg': 'big5_a_coverage_prereg.md',
                                     'verdict': 'DEMOGRAPHICALLY SELECTED MEDIA-COVERED STRATUM',
                                     'max_abs_smd': {'demographic': 0.620211,
                                                     'model_position': 0.378368,
                                                     'media_activity': 1.877120},
                                     'coverage': {'House': '232/433', 'Senate': '90/100',
                                                  'Democrat': '176/259', 'Republican': '144/272'}},
                 'engine': 'display-only; cooperation_v2 remains the engine A role; ranks unaffected'}, OUT, indent=1)
    top = sorted(names, key=lambda m: -members[m]['antagonism_z'])[:6]
    print('interview_antagonism.json: %d validated A-facet members | top antagonism: %s' %
          (len(members), ', '.join(top)))

if __name__ == '__main__':
    main()
