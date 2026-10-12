# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""Behavioral Extraversion v1 preregistered candidate (2026-07-13; zero API cost).

Measures two established Extraversion aspects from current member-attributed interview behavior:
  Enthusiasm       = mean(warmth, excitement/positive energy, gregariousness)
  Assertive-energy = mean(conversational assertiveness, within-appearance activity)
  E                = equal-weight mean(Enthusiasm, Assertive-energy)

Visibility is NOT an input.  It is evaluated only as a role/volume/leadership-controlled corroborator.
All five facets are residualized on party, gender, formal leadership, interview turns, substantial
appearances, and program breadth before chamber standardization.  No LLM or paid API is used.

No-arg prints lock/look/fingerprint status.  --simulate is synthetic-only.  --look is the one real
evaluation and writes behavioral_extraversion_v1.json.candidate ONLY after every locked bar and the
adversarial re-derivation pass.  The artifact is consumed by nothing pending the user's final review.
"""
import argparse, collections, csv, hashlib, itertools, json, math, os, re, sqlite3, sys
import numpy as np
from scipy.stats import pearsonr, spearmanr
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from moneyball_core import dump_atomic, party as party_map, bio2name

B = MB_ROOT
PREREG = os.path.join(B, 'big5_behavioral_extraversion_prereg.md')
OUT = os.path.join(B, 'behavioral_extraversion_v1.json.candidate')
MIN_CELL_TURNS = 12
MIN_PROGRAM_MEMBERS = 8
MIN_FULL_TURNS = 30
MIN_SUBSTANTIAL = 5
MIN_HALF_CELL_TURNS = 6
MIN_HALF_TURNS = 15
MIN_HALF_SUBSTANTIAL = 2
K_PERM = 5000
MAIN_SPLIT_SALTS = ('E-A', 'E-B', 'E-C', 'E-D', 'E-E')
FRESH_SPLIT_SALTS = ('E-F', 'E-G', 'E-H', 'E-I', 'E-J')

# Exact frozen lexical content.  Counts are member-turn matches per 1,000 words; INTERVIEW only.
WARM_RE = re.compile(
    r"\b(?:thank(?:s| you)?|appreciat(?:e|ed|ion)|grateful|gratitude|glad|pleasure|honou?r(?:ed)?|"
    r"welcome|good to see|friend(?:s|ship)?|love|proud of|congratulat(?:e|ions)|respect(?:ed|ful)?)\b",
    re.I,
)
EXCITE_RE = re.compile(
    r"\b(?:excited|exciting|excitement|enthusias(?:m|tic)|thrilled|delighted|eager|energ(?:y|ized|etic)|"
    r"fired up|amazing|incredible|fantastic|wonderful|terrific|passionate|can't wait|cannot wait|"
    r"looking forward)\b",
    re.I,
)
DEFENSE_RE = re.compile(
    r"\b(?:let me (?:finish|answer|respond|speak)|if I (?:may|could) finish|"
    r"I(?:'m| am) not (?:finished|done)|allow me to (?:finish|respond)|with all due respect)\b",
    re.I,
)


def program_of(package):
    p = (package or '').lower()
    if 'cnn.com' in p:
        m = re.search(r'/show/([^/]+)/', p); return 'cnn:' + (m.group(1) if m else '?')
    if 'meet-the-press' in p or 'nbcnews' in p: return 'mtp'
    if 'pbs.org' in p: return 'pbs'
    if 'npr.org' in p: return 'npr'
    return 'other'


def episode_key(package):
    return re.sub(r'/segment/\d+.*$', '', package or '')


def gini(values):
    xs = sorted(float(x) for x in values if x >= 0)
    if len(xs) < 2 or sum(xs) <= 0: return 0.0
    n = len(xs); total = sum(xs)
    return 2 * sum((i + 1) * x for i, x in enumerate(xs)) / (n * total) - (n + 1) / n


def zmap(values, chamber):
    out = {}
    for ch in ('House', 'Senate'):
        names = [m for m in values if chamber.get(m) == ch and math.isfinite(float(values[m]))]
        if not names: continue
        arr = np.asarray([values[m] for m in names], float); sd = float(arr.std(ddof=1)) if len(arr) > 1 else 1.0
        sd = sd if sd > 1e-12 else 1.0; mu = float(arr.mean())
        for m in names: out[m] = (float(values[m]) - mu) / sd
    return out


def residual_map(values, controls):
    names = sorted(m for m in values if all(m in c for c in controls))
    if len(names) < 3: return {}
    y = np.asarray([values[m] for m in names], float)
    X = np.asarray([[1.0] + [float(c[m]) for c in controls] for m in names], float)
    resid = y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
    return dict(zip(names, resid))


def corr_maps(left, right):
    names = sorted(set(left) & set(right))
    if len(names) < 3: return float('nan'), len(names)
    return float(pearsonr([left[m] for m in names], [right[m] for m in names]).statistic), len(names)


def partial_maps(left, right, controls, seed=20260713):
    names = sorted(set(left) & set(right) & set.intersection(*(set(c) for c in controls)))
    if len(names) < 4: return {'r': None, 'p': None, 'n': len(names)}
    x = np.asarray([left[m] for m in names], float); y = np.asarray([right[m] for m in names], float)
    Z = np.asarray([[1.0] + [float(c[m]) for c in controls] for m in names], float)
    x = x - Z @ np.linalg.lstsq(Z, x, rcond=None)[0]
    y = y - Z @ np.linalg.lstsq(Z, y, rcond=None)[0]
    obs = float(np.corrcoef(x, y)[0, 1])
    rng = np.random.default_rng(seed); ge = 0
    for _ in range(K_PERM):
        ge += int(float(np.corrcoef(x, rng.permutation(y))[0, 1]) >= obs)
    return {'r': obs, 'p': (ge + 1) / (K_PERM + 1), 'n': len(names)}


def sb(r):
    return 2 * r / (1 + r) if math.isfinite(r) and r > -1 else float('nan')


def split_bit(package, date, salt):
    raw = ('%s|%s|%s' % (package, date, salt)).encode('utf-8')
    return hashlib.sha256(raw).digest()[0] & 1


def load_context():
    congestion = json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json'), encoding='utf-8'))
    chamber = {r['member']: r['chamber'] for r in congestion}
    party0 = party_map()
    party = {m: 1.0 if party0.get(m) == 'R' else 0.0 for m in chamber if party0.get(m) in ('R', 'D')}
    b2n = bio2name(); gender = {}
    with open(os.path.join(B, 'fec', 'legislators.csv'), encoding='utf-8') as f:
        for row in csv.DictReader(f):
            m = b2n.get(row.get('bioguide_id'))
            if m and row.get('gender') in ('F', 'M'): gender[m] = 1.0 if row['gender'] == 'F' else 0.0
    panel = json.load(open(os.path.join(B, 'behavior_panel.json'), encoding='utf-8')).get('members', {})
    leader = {m: float((panel.get(m) or {}).get('committee_leader') or 0.0) for m in chamber}
    dispositions = json.load(open(os.path.join(B, 'behavioral_dispositions.json'), encoding='utf-8')).get('members', {})
    visibility = {m: float(r['visibility_z']) for m, r in dispositions.items() if r.get('visibility_z') is not None}
    blue = json.load(open(os.path.join(B, 'data', 'members', 'big5_blue_validated.json'), encoding='utf-8')).get('traits', {})
    other_traits = {t: {m: float(r[t]) for m, r in blue.items() if r.get(t) is not None}
                    for t in ('O', 'C', 'A', 'N')}
    return {'chamber': chamber, 'party': party, 'gender': gender, 'leader': leader,
            'visibility': visibility, 'other_traits': other_traits, 'congestion': congestion}


def load_appearances():
    con = sqlite3.connect(os.path.join(B, 'social_media.db'), timeout=60)
    con.execute('PRAGMA query_only=1')
    app = collections.defaultdict(list)
    for member, package, date, text in con.execute(
            "select member,package,date,text from speeches where source='INTERVIEW' order by rowid"):
        if member and package and text and text.strip():
            # CNN and some collectors store one interview episode as several /segment/N packages.
            # Merge those before measuring activity or assigning split halves so one real appearance
            # cannot be counted repeatedly or leak across the two halves.
            episode = episode_key(package)
            app[(member, program_of(episode), episode, str(date or ''))].append(text.strip())
    con.close(); return app


def subset_appearances(appearances, salt, half):
    return {k: v for k, v in appearances.items() if split_bit(episode_key(k[2]), k[3], salt) == half}


def build_scores(appearances, context, min_cell=MIN_CELL_TURNS, min_turns=MIN_FULL_TURNS,
                 min_substantial=MIN_SUBSTANTIAL):
    chamber = context['chamber']
    agg = collections.defaultdict(lambda: {'turns': 0, 'words': 0, 'warm': 0, 'excite': 0,
                                            'lens': [], 'defense': 0, 'apps': []})
    substantial = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for (member, program, package, date), turns in appearances.items():
        words = sum(len(t.split()) for t in turns)
        ep = substantial[member][(episode_key(package), date)]
        ep[0] += len(turns); ep[1] += words
        row = agg[(member, program)]
        row['turns'] += len(turns); row['words'] += words
        row['warm'] += sum(len(WARM_RE.findall(t)) for t in turns)
        row['excite'] += sum(len(EXCITE_RE.findall(t)) + t.count('!') for t in turns)
        row['lens'].extend(len(t.split()) for t in turns)
        row['defense'] += sum(len(DEFENSE_RE.findall(t)) for t in turns)
        row['apps'].append((len(turns), words))

    raw = collections.defaultdict(dict)
    for (member, program), row in agg.items():
        if row['turns'] < min_cell or row['words'] <= 0: continue
        raw[program][member] = {
            'turns': row['turns'],
            'warmth': 1000.0 * row['warm'] / row['words'],
            'excitement': 1000.0 * row['excite'] / row['words'],
            'activity_turns': float(np.mean([math.log1p(x[0]) for x in row['apps']])),
            'activity_words': float(np.mean([math.log1p(x[1]) for x in row['apps']])),
            'gini': gini(row['lens']),
            'defense': 1000.0 * row['defense'] / row['words'],
        }
    keys = ('warmth', 'excitement', 'activity_turns', 'activity_words', 'gini', 'defense')
    zsum = collections.defaultdict(lambda: collections.defaultdict(float)); weights = collections.Counter()
    programs = collections.defaultdict(set)
    for program, rows in raw.items():
        if len(rows) < MIN_PROGRAM_MEMBERS: continue
        for key in keys:
            vals = np.asarray([r[key] for r in rows.values()], float); sd = float(vals.std(ddof=1)) or 1.0
            mu = float(vals.mean())
            for member, row in rows.items(): zsum[member][key] += ((row[key] - mu) / sd) * row['turns']
        for member, row in rows.items():
            weights[member] += row['turns']; programs[member].add(program)
    pfeat = {m: {k: zsum[m][k] / w for k in keys} for m, w in weights.items() if w >= min_turns}

    # Gregariousness is opportunity-normalized joint-appearance behavior, reconstructed from the same
    # substantial-appearance definition as co_appearance_layer.py.  Raw counts never enter the facet.
    epi = collections.defaultdict(set)
    for member, episodes in substantial.items():
        for ep, (nturn, nword) in episodes.items():
            if nturn >= 4 or nword >= 200: epi[ep].add(member)
    sub_count = collections.Counter(); joint_count = collections.Counter(); partner_occ = collections.defaultdict(list)
    for ep, members in epi.items():
        for m in members: sub_count[m] += 1
        if 2 <= len(members) <= 8:
            for m in members:
                joint_count[m] += 1; partner_occ[m].extend(sorted(members - {m}))
    greg_components = {'joint_share': {}, 'partner_diversity': {}, 'partner_entropy': {}}
    for m, total in sub_count.items():
        if total < min_substantial or m not in pfeat: continue
        occ = partner_occ.get(m, []); counts = collections.Counter(occ)
        joint = joint_count[m]
        greg_components['joint_share'][m] = joint / total
        greg_components['partner_diversity'][m] = len(counts) / max(1, len(occ))
        if len(counts) <= 1:
            ent = 0.0
        else:
            probs = np.asarray(list(counts.values()), float); probs /= probs.sum()
            ent = float(-(probs * np.log(probs)).sum() / math.log(len(probs)))
        greg_components['partner_entropy'][m] = ent
    greg_z = [zmap(vals, chamber) for vals in greg_components.values()]
    greg = {m: float(np.mean([z[m] for z in greg_z])) for m in set.intersection(*(set(z) for z in greg_z))}

    raw_facets = {
        'warmth': {m: row['warmth'] for m, row in pfeat.items()},
        'excitement': {m: row['excitement'] for m, row in pfeat.items()},
        'activity': {m: (row['activity_turns'] + row['activity_words']) / 2.0 for m, row in pfeat.items()},
        'assertiveness': {m: (row['gini'] + row['defense']) / 2.0 for m, row in pfeat.items()},
        'gregariousness': greg,
    }
    log_turns = {m: math.log1p(v) for m, v in weights.items()}
    log_sub = {m: math.log1p(v) for m, v in sub_count.items()}
    program_breadth = {m: math.log1p(len(v)) for m, v in programs.items()}
    controls = [context['party'], context['gender'], context['leader'], log_turns, log_sub, program_breadth]
    facets = {name: zmap(residual_map(vals, controls), chamber) for name, vals in raw_facets.items()}
    common = set.intersection(*(set(v) for v in facets.values())) if facets else set()
    enthusiasm0 = {m: np.mean([facets[k][m] for k in ('warmth', 'excitement', 'gregariousness')]) for m in common}
    assertive0 = {m: np.mean([facets[k][m] for k in ('assertiveness', 'activity')]) for m in common}
    enthusiasm = zmap(enthusiasm0, chamber); assertive_energy = zmap(assertive0, chamber)
    common2 = set(enthusiasm) & set(assertive_energy)
    score = zmap({m: (enthusiasm[m] + assertive_energy[m]) / 2.0 for m in common2}, chamber)
    return {'score': score, 'enthusiasm': enthusiasm, 'assertive_energy': assertive_energy,
            'facets': facets, 'turns': dict(weights), 'substantial': dict(sub_count),
            'program_breadth': {m: len(v) for m, v in programs.items()},
            'controls': {'party': context['party'], 'gender': context['gender'], 'leader': context['leader'],
                         'log_turns': log_turns, 'log_substantial': log_sub, 'program_breadth': program_breadth}}


def reliability(appearances, context, salts):
    rows = []
    for salt in salts:
        halves = [build_scores(subset_appearances(appearances, salt, h), context,
                               MIN_HALF_CELL_TURNS, MIN_HALF_TURNS, MIN_HALF_SUBSTANTIAL) for h in (0, 1)]
        metrics = {}
        for name in ('score', 'enthusiasm', 'assertive_energy'):
            r, n = corr_maps(halves[0][name], halves[1][name]); metrics[name] = {'r_half': r, 'sb': sb(r), 'n': n}
        for name in ('warmth', 'excitement', 'gregariousness', 'assertiveness', 'activity'):
            r, n = corr_maps(halves[0]['facets'][name], halves[1]['facets'][name])
            metrics[name] = {'r_half': r, 'sb': sb(r), 'n': n}
        rows.append({'salt': salt, 'metrics': metrics})
    return rows


def load_rkh():
    rkh = {}
    with open(os.path.join(B, 'fec', 'liwc', 'rkh', 'congressbios.tab'), encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            try: rkh[int(float(row['icpsr']))] = float(row['extra_avg'])
            except (ValueError, TypeError, KeyError): pass
    b2i = {}
    for congress in (119, 118, 117):
        path = os.path.join(B, 'votes', 'HS%d_members.csv' % congress)
        if not os.path.exists(path): continue
        with open(path, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                bg = (row.get('bioguide_id') or '').strip()
                try: ic = int(float(row.get('icpsr') or ''))
                except ValueError: continue
                if bg and bg not in b2i: b2i[bg] = ic
    name_to_bio = {r['member']: r.get('bioguide') for r in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json')))}
    return {m: rkh[b2i[bg]] for m, bg in name_to_bio.items() if bg in b2i and b2i[bg] in rkh}


def rel_summary(rows):
    names = ('score', 'enthusiasm', 'assertive_energy', 'warmth', 'excitement',
             'gregariousness', 'assertiveness', 'activity')
    return {name: {'median_sb': float(np.nanmedian([r['metrics'][name]['sb'] for r in rows])),
                   'passes_050': sum(r['metrics'][name]['sb'] >= 0.50 for r in rows),
                   'median_n': int(np.median([r['metrics'][name]['n'] for r in rows]))}
            for name in names}


def controls_for(full):
    c = full['controls']
    return [c['party'], c['gender'], c['leader'], c['log_turns'], c['log_substantial'], c['program_breadth']]


def evaluate(appearances, context, split_salts=MAIN_SPLIT_SALTS):
    full = build_scores(appearances, context)
    rel_rows = reliability(appearances, context, split_salts); rel = rel_summary(rel_rows)
    controls = controls_for(full); rkh = load_rkh()
    rkh_test = partial_maps(full['score'], rkh, controls, seed=2026071301)
    vis_test = partial_maps(full['score'], context['visibility'], controls, seed=2026071302)
    aspect_r, aspect_n = corr_maps(full['enthusiasm'], full['assertive_energy'])
    discriminant = {t: partial_maps(full['score'], vals, controls, seed=2026071310 + i)
                    for i, (t, vals) in enumerate(context['other_traits'].items())}
    confounds = {}
    for name, vals in zip(('party', 'gender', 'leader', 'log_turns', 'log_substantial', 'program_breadth'), controls):
        confounds[name] = corr_maps(full['score'], vals)[0]
    facet_pairs = {}
    for a, b in itertools.combinations(sorted(full['facets']), 2):
        facet_pairs[a + '__' + b] = corr_maps(full['facets'][a], full['facets'][b])[0]
    facet_rel_pass = sum(rel[k]['median_sb'] >= 0.35 for k in ('warmth', 'excitement', 'gregariousness', 'assertiveness', 'activity'))
    bars = {
        'coverage': len(full['score']) >= 200 and (rkh_test['n'] or 0) >= 80,
        'overall_reliability': rel['score']['median_sb'] >= 0.60 and rel['score']['passes_050'] >= 4,
        'aspect_reliability': rel['enthusiasm']['median_sb'] >= 0.50 and rel['assertive_energy']['median_sb'] >= 0.50,
        'facet_reliability': facet_rel_pass >= 4,
        'rkh_convergence': rkh_test['r'] is not None and rkh_test['r'] >= 0.20 and rkh_test['p'] < 0.05,
        'visibility_corroboration': vis_test['r'] is not None and vis_test['r'] >= 0.10 and vis_test['p'] < 0.05,
        'aspect_coherence': math.isfinite(aspect_r) and 0.10 <= aspect_r <= 0.65,
        'discriminant': max(abs(v['r'] or 0.0) for v in discriminant.values()) <= 0.45,
        'confound_control': max(abs(v) for v in confounds.values() if math.isfinite(v)) <= 0.10,
        'facet_distinctness': max(abs(v) for v in facet_pairs.values() if math.isfinite(v)) <= 0.75,
    }
    return {'full': full, 'reliability_rows': rel_rows, 'reliability': rel,
            'rkh': rkh_test, 'visibility': vis_test, 'aspect_r': aspect_r, 'aspect_n': aspect_n,
            'discriminant': discriminant, 'confounds': confounds, 'facet_pairs': facet_pairs,
            'bars': bars, 'pass': all(bars.values())}


def leave_program_out(appearances, context, full_score):
    groups = ('cnn', 'mtp', 'pbs', 'npr', 'other'); rows = []
    for group in groups:
        if group == 'cnn': sub = {k: v for k, v in appearances.items() if not k[1].startswith('cnn:')}
        else: sub = {k: v for k, v in appearances.items() if k[1] != group}
        alt = build_scores(sub, context)['score']; names = sorted(set(full_score) & set(alt))
        rho = float(spearmanr([full_score[m] for m in names], [alt[m] for m in names]).statistic) if len(names) >= 3 else float('nan')
        rows.append({'excluded': group, 'rho': rho, 'n': len(names)})
    return rows


def rederive(appearances, context, primary):
    fresh = reliability(appearances, context, FRESH_SPLIT_SALTS); summary = rel_summary(fresh)
    loo = leave_program_out(appearances, context, primary['full']['score'])
    ok = (summary['score']['median_sb'] >= 0.60 and summary['score']['passes_050'] >= 4 and
          summary['enthusiasm']['median_sb'] >= 0.50 and summary['assertive_energy']['median_sb'] >= 0.50 and
          min(r['rho'] for r in loo if math.isfinite(r['rho'])) >= 0.65 and
          float(np.median([r['rho'] for r in loo if math.isfinite(r['rho'])])) >= 0.80)
    return {'fresh_reliability_rows': fresh, 'fresh_reliability': summary,
            'leave_program_out': loo, 'pass': bool(ok)}


def synthetic_battery():
    rng = np.random.default_rng(20260713); n = 400
    conf = rng.normal(size=(n, 4)); latent = rng.normal(size=n)
    facets = np.column_stack([0.7 * latent + 0.3 * conf[:, i % 4] + rng.normal(scale=0.7, size=n) for i in range(5)])
    def resid(v):
        X = np.column_stack([np.ones(n), conf]); return v - X @ np.linalg.lstsq(X, v, rcond=None)[0]
    score = np.mean(np.column_stack([resid(facets[:, i]) for i in range(5)]), axis=1)
    conf_ok = max(abs(np.corrcoef(score, conf[:, i])[0, 1]) for i in range(4)) < 1e-10
    y = 0.35 * (score - score.mean()) / score.std(ddof=1) + math.sqrt(1 - .35 ** 2) * rng.normal(size=n)
    recover = float(np.corrcoef(score, y)[0, 1]) >= 0.20
    warm_ok = len(WARM_RE.findall('Thank you, my friend. I appreciate and respect you.')) >= 4
    excite_ok = len(EXCITE_RE.findall("I'm excited, thrilled, energized, and looking forward.")) >= 4
    split_ok = split_bit('pkg', '2026-01-01', 'x') == split_bit('pkg', '2026-01-01', 'x')
    checks = {'residualization_removes_confounds': conf_ok, 'synthetic_signal_recovered': recover,
              'warmth_lexicon_operates': warm_ok, 'excitement_lexicon_operates': excite_ok,
              'stable_split_is_deterministic': split_ok}
    for k, v in checks.items(): print('  %s %s' % ('OK  ' if v else 'FAIL', k))
    print('SIMULATE BATTERY: %s' % ('ALL GREEN' if all(checks.values()) else 'FAILURE — look forbidden'))
    return all(checks.values())


PIN_FILES = ('social_media.db', 'data/engine/congestion.json', 'behavior_panel.json', 'behavioral_dispositions.json',
             'data/members/big5_blue_validated.json', 'fec/legislators.csv', 'fec/liwc/rkh/congressbios.tab',
             'votes/HS119_members.csv', 'votes/HS118_members.csv', 'votes/HS117_members.csv')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def current_pins():
    files = {p: sha256(os.path.join(B, *p.split('/'))) for p in PIN_FILES}
    con = sqlite3.connect(os.path.join(B, 'social_media.db')); con.execute('PRAGMA query_only=1')
    rows = int(con.execute("select count(*) from speeches where source='INTERVIEW'").fetchone()[0])
    packages = int(con.execute("select count(distinct package) from speeches where source='INTERVIEW'").fetchone()[0])
    con.close(); return {'files': files, 'interview_rows': rows, 'interview_packages': packages}


def prereg_pins():
    text = open(PREREG, encoding='utf-8').read()
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.S)
    return json.loads(m.group(1)) if m else None


def pins_match():
    expected = prereg_pins(); actual = current_pins()
    return expected == actual, expected, actual


def print_result(result, recheck=None):
    print('coverage: E=%d | RKH joined=%d' % (len(result['full']['score']), result['rkh']['n']))
    for k, row in result['reliability'].items():
        print('reliability %-18s median SB=%+.3f pass>=.50 %d/5 median-n=%d' %
              (k, row['median_sb'], row['passes_050'], row['median_n']))
    print('RKH E partial: r=%+.3f p=%.4f n=%d' % (result['rkh']['r'], result['rkh']['p'], result['rkh']['n']))
    print('visibility partial: r=%+.3f p=%.4f n=%d' %
          (result['visibility']['r'], result['visibility']['p'], result['visibility']['n']))
    print('aspect coherence: r=%+.3f n=%d' % (result['aspect_r'], result['aspect_n']))
    print('discriminant:', ' '.join('%s=%+.3f' % (k, v['r']) for k, v in result['discriminant'].items()))
    print('confounds:', ' '.join('%s=%+.3f' % kv for kv in result['confounds'].items()))
    for k, v in result['bars'].items(): print('BAR %-28s %s' % (k, 'PASS' if v else 'MISS'))
    if recheck:
        print('REDERIVATION fresh overall SB=%+.3f enthusiasm=%+.3f assertive-energy=%+.3f' %
              (recheck['fresh_reliability']['score']['median_sb'],
               recheck['fresh_reliability']['enthusiasm']['median_sb'],
               recheck['fresh_reliability']['assertive_energy']['median_sb']))
        print('REDERIVATION leave-program-out:', ' '.join('%s=%+.3f' % (r['excluded'], r['rho']) for r in recheck['leave_program_out']))
        print('REDERIVATION:', 'PASS' if recheck['pass'] else 'FRAGILE = MISS')


def candidate_doc(result):
    full = result['full']; members = {}
    for m in sorted(full['score']):
        members[m] = {'E': round(float(full['score'][m]), 6),
                      'enthusiasm': round(float(full['enthusiasm'][m]), 6),
                      'assertive_energy': round(float(full['assertive_energy'][m]), 6),
                      'facets': {k: round(float(v[m]), 6) for k, v in full['facets'].items()},
                      'qualifying_turns': int(full['turns'].get(m, 0)),
                      'substantial_appearances': int(full['substantial'].get(m, 0)),
                      'program_breadth': int(full['program_breadth'].get(m, 0))}
    return {'schema': 'behavioral-extraversion-v1-candidate',
            'status': 'CANDIDATE_CONSUMED_BY_NOTHING_AWAITING_USER_REVIEW',
            'method': 'E=equal-weight(Enthusiasm, Assertive-energy); visibility excluded from score',
            'facets': {'Enthusiasm': ['warmth', 'excitement', 'gregariousness'],
                       'Assertive-energy': ['assertiveness', 'activity']},
            'controls_removed': ['party', 'gender', 'formal leadership', 'interview turns',
                                 'substantial appearances', 'program breadth', 'chamber standardization'],
            'validation': json.loads(json.dumps(
                {k: v for k, v in result.items() if k not in ('full',)},
                default=lambda x: x.item() if hasattr(x, 'item') else str(x))),
            'members': members}


def status():
    if not os.path.exists(PREREG): print('prereg: ABSENT'); return
    text = open(PREREG, encoding='utf-8').read(); locked = 'STATUS: LOCKED' in text
    consumed = '\n## RESULT-E-BEHAVIORAL' in text
    match, _, _ = pins_match()
    print('prereg: %s | look: %s | fingerprints: %s' %
          ('LOCKED' if locked else 'UNLOCKED', 'CONSUMED' if consumed else 'AVAILABLE', 'MATCH' if match else 'DRIFT'))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--simulate', action='store_true'); ap.add_argument('--look', action='store_true')
    args = ap.parse_args()
    if args.simulate:
        if not synthetic_battery(): sys.exit(1)
        return
    if not args.look:
        status(); return
    if not os.path.exists(PREREG): print('!! LOOK REFUSED: prereg absent'); sys.exit(2)
    text = open(PREREG, encoding='utf-8').read()
    if 'STATUS: LOCKED' not in text: print('!! LOOK REFUSED: prereg not LOCKED'); sys.exit(2)
    if '\n## RESULT-E-BEHAVIORAL' in text: print('!! LOOK REFUSED: RESULT already recorded'); sys.exit(2)
    match, expected, actual = pins_match()
    if not match:
        print('!! LOOK REFUSED (not consumed): input fingerprint drift')
        print('expected', json.dumps(expected, sort_keys=True)); print('actual', json.dumps(actual, sort_keys=True)); sys.exit(2)
    if not synthetic_battery(): print('!! LOOK REFUSED (not consumed): simulate battery failure'); sys.exit(2)
    appearances = load_appearances(); context = load_context(); result = evaluate(appearances, context)
    recheck = rederive(appearances, context, result) if result['pass'] else None
    print_result(result, recheck)
    final_pass = result['pass'] and recheck and recheck['pass']
    print('FINAL VERDICT:', 'PASS' if final_pass else ('FRAGILE = MISS' if result['pass'] else 'MISS'))
    if final_pass:
        doc = candidate_doc(result); doc['rederivation'] = recheck
        dump_atomic(doc, OUT, indent=1)
        print('behavioral_extraversion_v1.json.candidate: %d members; consumed by nothing' % len(doc['members']))


if __name__ == '__main__':
    main()
