# -*- coding: utf-8 -*-
"""Gate-3 verification battery (holistic v1; freeze addendum 2026-08-17).

STRUCTURE (rewritten 2026-08-17 after the PRO-1 adversarial round found the first version
self-retired into a scored PASS on any tree without the holistic workbooks -- which, since
*.xlsx is gitignored, meant EVERY tree but the build worktree. A gate that reports green
having run zero assertions is worse than no gate):

  TIER A - CONTAMINATION (INV-2). Runs ALWAYS, everywhere, against SYNTHETIC plants in a
    sandbox MONEYBALL_ROOT. Depends on no real artifact, so it cannot self-retire. This is
    the tier that actually protects production: ISO-2 (default env ignores the holistic
    family), ISO-3 (the weekly-refresh augmenter never bases on a holistic file, proven by
    a CONTENT-HASH control comparison rather than a stdout grep), ISO-6 + D3 (fail-closed
    selection), and NET_CONFIG input validation.
  TIER B - ARTIFACT INVARIANTS. Runs whenever holistic workbooks exist at this root, of ANY
    vintage -- the invariants (COR-2 re-weighting identity, PAR-4 symmetrization, ISO-5
    in-process cache isolation, HON-2 banner) hold for any valid holistic artifact, so
    nothing is pinned to a filename and nothing retires on lawful drift. Absent artifacts
    SKIP, reported separately and never counted as passes.

Sandbox plants go under a scratch MONEYBALL_ROOT only (ENV-5); the live tree is never written."""
import os, sys, io, json, glob, shutil, tempfile, subprocess, contextlib, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
FEC = os.path.join(WT, 'fec')
sys.path.insert(0, FEC)
os.environ.pop('NET_CONFIG', None)          # clean shell (ENV-2)
import moneyball_core as MC

P = F = S = 0
def ok(name, cond, detail=''):
    global P, F
    if cond: P += 1; print('  OK   ' + name)
    else:    F += 1; print('  FAIL ' + name + ('  :: ' + str(detail) if detail else ''))
def skip(name, why):
    global S
    S += 1; print('  SKIP ' + name + '  :: ' + why)

def newest(pat, want_aug=None, root=None):
    fs = glob.glob(os.path.join(root or WT, pat))
    if want_aug is not None:
        fs = [f for f in fs if ('AUG' in os.path.basename(f).upper()) == want_aug]
    return os.path.basename(max(fs, key=os.path.getmtime)) if fs else None

def edges_hash(path):
    """Canonical content hash of an Edges sheet - order-independent, ignores xlsx wall-clock."""
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)['Edges']
    rows = sorted(tuple(c for c in r) for r in ws.iter_rows(min_row=2, values_only=True))
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]

def plant_workbook(path, rows):
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Edges'
    ws.append(['From', 'To', 'Weight'])
    for r in rows: ws.append(list(r))
    wb.save(path)

# =====================================================================================
# TIER A - CONTAMINATION. Always runs. Synthetic plants only.
# =====================================================================================
print('[TIER A / NET_CONFIG input validation - fail-closed on anything but cu|holistic]')
for bad in ('', 'Holistic', 'HOLISTIC', ' holistic ', 'cu ', 'CU', 'true', '1'):
    os.environ['NET_CONFIG'] = bad
    try:
        MC._net_config(); got = 'ACCEPTED'
    except ValueError:
        got = 'raised'
    ok('NET_CONFIG=%r rejected (no silent config substitution)' % bad, got == 'raised', got)
os.environ.pop('NET_CONFIG', None)
ok('NET_CONFIG absent defaults to cu', MC._net_config() == 'cu')
# EVERY registry key must be accept-tested. holistic_norm shipped for a day WITHOUT one, so the
# accepted set was under-covered by exactly the config most likely to be got wrong; when bank_reg
# and crypto were promoted (Paul 2026-08-18) that gap would have doubled. Driven off the registry
# so a config added later cannot skip this assertion.
for _good in sorted(MC.NETWORKS):
    os.environ['NET_CONFIG'] = _good
    ok('NET_CONFIG=%s accepted (registry key)' % _good, MC._net_config() == _good)
os.environ.pop('NET_CONFIG', None)
ok('registry carries the five shipped configs',
   set(MC.NETWORKS) == {'cu', 'holistic', 'holistic_norm', 'bank_reg', 'crypto'},
   sorted(MC.NETWORKS))
# symmetrize is NOT derivable from the name and must be declared per config -- holistic_norm is
# directed by design; everything else is mean-symmetrized.
ok('only holistic_norm is declared directed',
   {k for k, v in MC.NETWORKS.items() if not v['symmetrize']} == {'holistic_norm'},
   {k for k, v in MC.NETWORKS.items() if not v['symmetrize']})
# A promoted sector must never be able to masquerade as a production or holistic artifact.
import re as _re
for _k, _v in MC.NETWORKS.items():
    if _v.get('family') != 'sector': continue
    _nm = 'sector_network_%s_20260818.xlsx' % _v['sector']
    ok('%s artifact name fails the production glob' % _k,
       not _re.match(r'^congress_cosponsor_network', _nm))
    ok('%s artifact name fails the holistic glob' % _k,
       not _nm.startswith('holistic_network_'))

print('[TIER A / sandbox: ISO-2, ISO-6, D3 - synthetic plants, no real artifact needed]')
sb = tempfile.mkdtemp(prefix='holistic_sandbox_')
os.makedirs(os.path.join(sb, 'data', 'engine'), exist_ok=True)
CONG = os.path.join(WT, 'data', 'engine', 'congestion.json')
shutil.copy(CONG, os.path.join(sb, 'data', 'engine'))
_ros = [m['bioguide'] for m in json.load(open(CONG, encoding='utf-8')) if m.get('bioguide')][:6]
CU_PLANT = 'congress_cosponsor_network_20200101_000000.xlsx'
HOL_PLANT = 'holistic_network_BASE_29991231.xlsx'
plant_workbook(os.path.join(sb, CU_PLANT), [(_ros[0], _ros[1], 1.5), (_ros[2], _ros[3], 0.7)])

def run_loader(root, env_extra):
    code = ("import sys; sys.path.insert(0, r'%s'); import moneyball_core as MC; MC.load_network(); "
            "print('LOADED', MC._c.get('netfile') or MC._c.get('netfile@holistic'))" % FEC)
    env = {k: v for k, v in os.environ.items() if k != 'NET_CONFIG'}
    env['MONEYBALL_ROOT'] = root
    env.update(env_extra)
    return subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=env, cwd=FEC)

r = run_loader(sb, {'NET_CONFIG': 'holistic'})
ok('ISO-6: zero holistic files under NET_CONFIG=holistic -> raises, never a CU fallback',
   r.returncode != 0 and 'fail-closed' in (r.stderr + r.stdout), (r.stderr or r.stdout).strip()[-110:])

plant_workbook(os.path.join(sb, HOL_PLANT), [(_ros[0], _ros[1], 9.9)])   # newest by mtime AND by name
r = run_loader(sb, {})
ok('ISO-2: default env picks the CU family even with a NEWER holistic file present',
   r.returncode == 0 and CU_PLANT in r.stdout, (r.stderr or r.stdout).strip()[-110:])
r = run_loader(sb, {'NET_CONFIG': 'holistic'})
ok('holistic config picks the planted holistic file',
   r.returncode == 0 and HOL_PLANT in r.stdout, (r.stderr or r.stdout).strip()[-110:])

open(os.path.join(sb, 'holistic_network_XXX_20260817.xlsx'), 'wb').close()
r = run_loader(sb, {'NET_CONFIG': 'holistic'})
ok('D3: unclassifiable holistic_network_* name -> raises (ambiguity fail-closed)',
   r.returncode != 0 and 'unclassifiable' in (r.stderr + r.stdout), (r.stderr or r.stdout).strip()[-110:])
os.remove(os.path.join(sb, 'holistic_network_XXX_20260817.xlsx'))

print('[TIER A / ISO-3: the weekly-refresh augmenter never bases on a holistic file]')
print('        CONTENT-HASH control comparison. PRO-1 replaced the old stdout grep, which')
print('        printed before any layer folding and so could not see a contaminated fold.')

def run_augmenter(root, out_name):
    env = {k: v for k, v in os.environ.items() if k != 'NET_CONFIG'}
    env['MONEYBALL_ROOT'] = root
    env['AUG_OUT'] = os.path.join(root, out_name)
    return subprocess.run([sys.executable, os.path.join(FEC, 'network', 'build_multilayer_network.py')],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)

r_with = run_augmenter(sb, 'aug_WITH_holistic_present.xlsx')
os.rename(os.path.join(sb, HOL_PLANT), os.path.join(sb, HOL_PLANT + '.hidden'))
r_ctrl = run_augmenter(sb, 'aug_CONTROL_no_holistic.xlsx')
os.rename(os.path.join(sb, HOL_PLANT + '.hidden'), os.path.join(sb, HOL_PLANT))
both_ok = r_with.returncode == 0 and r_ctrl.returncode == 0
ok('ISO-3 control runs both succeed', both_ok, ((r_with.stderr or '') + (r_ctrl.stderr or '')).strip()[-110:])
if both_ok:
    h_with = edges_hash(os.path.join(sb, 'aug_WITH_holistic_present.xlsx'))
    h_ctrl = edges_hash(os.path.join(sb, 'aug_CONTROL_no_holistic.xlsx'))
    ok('ISO-3: CU AUG content IDENTICAL whether or not a holistic file sits at the root',
       h_with == h_ctrl, 'with=%s control=%s' % (h_with, h_ctrl))
    ok('ISO-3: the CU augmenter reports the CU base',
       ('base: %s' % CU_PLANT) in r_with.stdout, r_with.stdout.strip()[:110])
else:
    skip('ISO-3 content-hash comparison', 'control runs did not both complete')
print('[TIER A / CU_IDENTITY_FIX: Menendez out-edges dropped from the defective CU vintage (decision G)]')
# The 2026-06-25 CU base credits Sen. Menendez's (M000639) sponsorships to Rep. Menendez (M001226).
# Plant that vintage NAME with one misattributed out-edge and one legitimate in-edge; the fix must
# drop the out-edge in the directed loader AND the CU augmenter, keep the in-edge, and the revert
# dial must restore it. Pre-fix code keeps the out-edge, so this section fails on it.
sbm = tempfile.mkdtemp(prefix='cu_identity_sandbox_')
os.makedirs(os.path.join(sbm, 'data', 'engine'), exist_ok=True)
shutil.copy(CONG, os.path.join(sbm, 'data', 'engine'))
_MEN, _X, _Y = 'M001226', _ros[0], _ros[1]
plant_workbook(os.path.join(sbm, 'congress_cosponsor_network_20260625_205554.xlsx'),
               [(_MEN, _X, 0.5), (_Y, _MEN, 0.3), (_ros[2], _ros[3], 0.7)])
def _directed(env_extra):
    code = ("import sys,json; sys.path.insert(0, r'%s'); import moneyball_core as MC; "
            "b=MC.bio2name(); d=MC.load_network_directed(); "
            "print('DIR', json.dumps([b['%s'] in d.get(b['%s'],{}), b['%s'] in d.get(b['%s'],{})]))"
            % (FEC, _X, _MEN, _MEN, _Y))
    env = {k: v for k, v in os.environ.items() if k not in ('NET_CONFIG', 'CU_IDENTITY_FIX')}
    env['MONEYBALL_ROOT'] = sbm; env['CU_PLACEHOLDER_BACKFILL'] = '0'; env.update(env_extra)   # backfill has its own suite
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=env, cwd=FEC)
    m = [l for l in r.stdout.splitlines() if l.startswith('DIR ')]
    return json.loads(m[0][4:]) if m else (r.stderr or r.stdout).strip()[-120:]
ok('CU_IDENTITY_FIX default: directed loader drops M001226 -> X, keeps Y -> M001226',
   _directed({}) == [False, True], _directed({}))
ok('CU_IDENTITY_FIX=0 restores the misattributed out-edge (revert switch)',
   _directed({'CU_IDENTITY_FIX': '0'}) == [True, True], _directed({'CU_IDENTITY_FIX': '0'}))
def _aug_pairs(env_extra, out_name):
    env = {k: v for k, v in os.environ.items() if k not in ('NET_CONFIG', 'CU_IDENTITY_FIX')}
    env['MONEYBALL_ROOT'] = sbm; env['AUG_OUT'] = os.path.join(sbm, out_name); env['CU_PLACEHOLDER_BACKFILL'] = '0'; env.update(env_extra)
    r = subprocess.run([sys.executable, os.path.join(FEC, 'network', 'build_multilayer_network.py')],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)
    if r.returncode != 0: return (r.stderr or r.stdout).strip()[-120:]
    import openpyxl as _oxm
    ws = _oxm.load_workbook(os.path.join(sbm, out_name), data_only=True)['Edges']
    return {(x[0], x[1]) for x in ws.iter_rows(min_row=2, values_only=True)}
_pmx = (min(_MEN, _X), max(_MEN, _X)); _pmy = (min(_MEN, _Y), max(_MEN, _Y))
_a1 = _aug_pairs({}, 'aug_identity_fix.xlsx')
ok('CU augmenter drops the misattributed Menendez pair and keeps his in-edge pair',
   isinstance(_a1, set) and _pmx not in _a1 and _pmy in _a1, _a1)
_a0 = _aug_pairs({'CU_IDENTITY_FIX': '0'}, 'aug_identity_revert.xlsx')
ok('CU augmenter with CU_IDENTITY_FIX=0 keeps the pair (byte-for-byte old behaviour)',
   isinstance(_a0, set) and _pmx in _a0, _a0)
ok('CU_IDENTITY_FIX is keyed to the defective vintage only (a re-scrape is never filtered)',
   MC.cu_dropped_sponsors('congress_cosponsor_network_20270101_000000.xlsx') == frozenset()
   and MC.cu_dropped_sponsors(os.path.join('x', 'congress_cosponsor_network_20260625_205554.xlsx')) == {'M001226'})
os.environ['CU_IDENTITY_FIX'] = 'yes'
try:
    MC.cu_dropped_sponsors('congress_cosponsor_network_20260625_205554.xlsx'); _got = 'ACCEPTED'
except ValueError:
    _got = 'raised'
os.environ.pop('CU_IDENTITY_FIX', None)
ok('CU_IDENTITY_FIX rejects values other than 0|1', _got == 'raised', _got)
shutil.rmtree(sbm, ignore_errors=True)

print('[TIER A / PAR-5 + PAR-6 + COR-9: the holistic WRITE side, sandboxed]')
# PRO-1 finding: the augmenter's entire holistic branch had zero automated coverage while
# RECORD claimed PAR-5 / COR-9 / PAR-6 verdicts. These run against synthetic layer inputs.
import datetime as _dt
def _wjson(rel, obj):
    fp = os.path.join(sb, *rel)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    json.dump(obj, open(fp, 'w', encoding='utf-8'))
    return fp
A, B, C_, D_ = _ros[0], _ros[1], _ros[2], _ros[3]
_n2 = {m['bioguide']: m['member'] for m in json.load(open(CONG, encoding='utf-8')) if m.get('bioguide')}
LAYERS = [(['data', 'stance', 'press_engagement.json'], {'symmetric': [], 'directed': []}),
          (['data', 'stance', 'press_hcoi.json'], {'edges': [], 'communities': []}),
          (['data', 'lobbying', 'social_interactions.json'], {'symmetric': []}),
          (['data', 'network', 'co_appearance.json'],
           {'edges': [{'a': _n2[C_], 'b': _n2[D_], 'n': 4}, {'a': _n2[A], 'b': _n2[B], 'n': 1}]})]
for rel, obj in LAYERS: _wjson(rel, obj)

def run_holistic_aug(out_name, **extra):
    env = dict(os.environ); env['MONEYBALL_ROOT'] = sb; env['NET_CONFIG'] = 'holistic'
    env.pop('COAP_CAP', None); env.update(extra)       # the fixture pins the DEFAULT cap
    env['AUG_OUT'] = os.path.join(sb, out_name)
    return subprocess.run([sys.executable, os.path.join(FEC, 'network', 'build_multilayer_network.py')],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)

r = run_holistic_aug('holistic_network_AUG_29991231.xlsx')
ok('holistic augmenter runs in the sandbox', r.returncode == 0, (r.stderr or r.stdout).strip()[-140:])
if r.returncode == 0:
    import openpyxl as _ox
    _pv = _ox.load_workbook(os.path.join(sb, 'holistic_network_AUG_29991231.xlsx'), data_only=True)['Provenance']
    _rows = {(x[0], x[1]): (x[2], x[3]) for x in _pv.iter_rows(min_row=2, values_only=True) if x and x[0] in _ros}
    _k = (min(C_, D_), max(C_, D_))
    # PAR-6 + D2a (2026-10-11): w = min(COAP_CAP, COAP_W*n); default cap = HCoI ceiling 0.12,
    # so n=4 gives min(0.12, 0.20) = 0.12 (the pre-D2a cap 0.5 gave 0.20).
    ok('PAR-6/D2a: co-appearance w = min(cap, COAP_W*n) at the HCoI-parity cap (n=4 -> 0.12)',
       _k in _rows and abs(_rows[_k][0] - 0.12) < 1e-9 and _rows[_k][1] == 'coappear', _rows.get(_k))
    # Negative control: the revert dial COAP_CAP=0.5 must FAIL the D2a fixture (0.20, not 0.12),
    # proving the check discriminates the cap rather than passing on any weight.
    r05 = run_holistic_aug('holistic_network_AUG_29991229.xlsx', COAP_CAP='0.5')
    _w05 = None
    if r05.returncode == 0:
        _p05 = _ox.load_workbook(os.path.join(sb, 'holistic_network_AUG_29991229.xlsx'), data_only=True)['Provenance']
        _w05 = {(x[0], x[1]): x[2] for x in _p05.iter_rows(min_row=2, values_only=True) if x and x[0] in _ros}.get(_k)
    ok('D2a negative control: COAP_CAP=0.5 reproduces the old 0.20 and fails the 0.12 fixture',
       _w05 is not None and abs(_w05 - 0.20) < 1e-9 and abs(_w05 - 0.12) > 1e-9, _w05)
    _k2 = (min(A, B), max(A, B))
    ok('PAR-6: episode floor n>=2 excludes an n=1 pair',
       _k2 not in _rows or _rows[_k2][1] != 'coappear', _rows.get(_k2))

os.rename(os.path.join(sb, 'data', 'network', 'co_appearance.json'),
          os.path.join(sb, 'data', 'network', 'co_appearance.json.hidden'))
r = run_holistic_aug('holistic_network_AUG_29991230.xlsx')
ok('PAR-5: a MISSING declared layer input HARD-FAILS under holistic (never print-and-continue)',
   r.returncode != 0 and 'PAR-5' in (r.stdout + r.stderr), (r.stderr or r.stdout).strip()[-110:])
os.rename(os.path.join(sb, 'data', 'network', 'co_appearance.json.hidden'),
          os.path.join(sb, 'data', 'network', 'co_appearance.json'))

env_bad = dict(os.environ); env_bad['MONEYBALL_ROOT'] = sb; env_bad['NET_CONFIG'] = 'holistic'
env_bad['AUG_OUT'] = os.path.join(sb, 'congress_cosponsor_network_AUG29991231.xlsx')
r = subprocess.run([sys.executable, os.path.join(FEC, 'network', 'build_multilayer_network.py')],
                   capture_output=True, text=True, encoding='utf-8', errors='replace', env=env_bad)
ok('COR-9: the holistic augmenter REFUSES a production-family output name',
   r.returncode != 0 and 'COR-9' in (r.stdout + r.stderr), (r.stderr or r.stdout).strip()[-110:])

shutil.rmtree(sb, ignore_errors=True)

# =====================================================================================
# TIER B - ARTIFACT INVARIANTS. Runs on ANY holistic vintage present; skips if absent.
# =====================================================================================
HOL_AUG = newest('holistic_network_AUG_*.xlsx')
HOL_BASE = newest('holistic_network_BASE_*.xlsx')
CU_AUG = newest('congress_cosponsor_network_*.xlsx', True)
CU_BASE = newest('congress_cosponsor_network_*.xlsx', False)

print('[TIER B / ISO-5 + HON-2: in-process config switch and cache isolation]')
if not (HOL_AUG and HOL_BASE and CU_AUG and CU_BASE):
    skip('TIER B (ISO-5, HON-2, COR-2, PAR-4)',
         'no holistic workbooks at this root (*.xlsx is gitignored; deploy them to arm these). '
         'holistic AUG=%s BASE=%s' % (HOL_AUG, HOL_BASE))
else:
    adj_cu = MC.load_network()
    ok('engine loader picks the newest CU AUG under default env',
       MC._c.get('netfile') == CU_AUG, MC._c.get('netfile'))
    dadj_cu = MC.load_network_directed()
    ok('directed loader picks the newest CU base under default env',
       MC._c.get('dnetfile') == CU_BASE, MC._c.get('dnetfile'))
    _cu_dsig = sum(len(v) for v in dadj_cu.values())

    os.environ['NET_CONFIG'] = 'holistic'
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        adj_h = MC.load_network()
        dadj_h = MC.load_network_directed()
    ok('holistic engine graph = the holistic AUG',
       MC._c.get('netfile@holistic') == HOL_AUG, MC._c.get('netfile@holistic'))
    ok('holistic directed graph = the holistic BASE (direction preserved)',
       MC._c.get('dnetfile@holistic') == HOL_BASE, MC._c.get('dnetfile@holistic'))
    ok('HON-2 banner printed on holistic load', '[NET_CONFIG=holistic]' in buf.getvalue(), buf.getvalue()[:70])
    ok('distinct adjacency objects per config', adj_h is not adj_cu and len(adj_h) == len(adj_cu) == 533)
    ok('distinct DIRECTED objects per config', dadj_h is not dadj_cu)

    os.environ.pop('NET_CONFIG', None)
    adj_cu2 = MC.load_network()
    dadj_cu2 = MC.load_network_directed()
    # PRO-1 fix: the old battery checked cache-key MEMBERSHIP, which stays true even when a
    # holistic graph clobbers the production key. Check CONTENT and OBJECT IDENTITY of BOTH
    # caches after the round trip - the defect this must catch is a directed loader that
    # writes _c['dadj'] regardless of config.
    ok('ISO-5 undirected: switch back to cu serves the SAME cached CU object', adj_cu2 is adj_cu)
    ok('ISO-5 DIRECTED: switch back to cu serves the SAME cached CU object (not the holistic graph)',
       dadj_cu2 is dadj_cu and sum(len(v) for v in dadj_cu2.values()) == _cu_dsig)
    ok('ISO-5: production directed cache still holds the CU file after the round trip',
       MC._c.get('dnetfile') == CU_BASE, MC._c.get('dnetfile'))
    ok('ISO-5: production and holistic directed caches hold DIFFERENT graphs',
       MC._c['dadj'] is not MC._c['dadj@holistic'])
    ok('all eight cache keys present and config-scoped',
       all(k in MC._c for k in ('adj', 'adj@holistic', 'dadj', 'dadj@holistic',
                                'netfile', 'netfile@holistic', 'dnetfile', 'dnetfile@holistic')))
    _h_edges = sum(len(v) for v in adj_h.values()) // 2
    _c_edges = sum(len(v) for v in adj_cu.values()) // 2
    print('  (density: holistic %d pairs vs CU %d pairs of 141,778 possible)' % (_h_edges, _c_edges))
    ok('holistic graph is materially denser than CU (sanity, not a bar)', _h_edges > 10 * _c_edges)

    print('[TIER B / COR-2: re-weighting identity on the shipped BASE (all rows)]')
    import openpyxl
    ws = openpyxl.load_workbook(os.path.join(WT, HOL_BASE), read_only=True, data_only=True)['Edges']
    rows = bad = zero_role = 0
    for r_ in ws.iter_rows(min_row=2, values_only=True):
        w, wo, wr = r_[2], r_[8], r_[9]
        rows += 1
        if abs(w - (1.0 * wo + 0.2 * wr)) > 1e-9 * max(1.0, abs(w)): bad += 1
        if r_[4] == 0 or r_[5] == 0: zero_role += 1
    ok('Weight == 1.0*WOriginal + 0.2*WRegular on every row (1e-9 rel)', bad == 0, '%d bad of %d' % (bad, rows))
    ok('single-role rows present (zero-role emission basis intact)', zero_role > 0, zero_role)

    print('[TIER B / PAR-4: holistic AUG symmetrization semantics]')
    ws2 = openpyxl.load_workbook(os.path.join(WT, HOL_AUG), read_only=True, data_only=True)['Edges']
    n = ordered = dup = 0
    seen = set()
    for r_ in ws2.iter_rows(min_row=2, values_only=True):
        a, b = r_[0], r_[1]
        n += 1
        if a < b: ordered += 1
        k = (min(a, b), max(a, b))
        if k in seen: dup += 1
        seen.add(k)
    ok('AUG: one row per pair, min/max re-keyed (source<target on all rows, zero dups)',
       ordered == n and dup == 0, '%d/%d ordered, %d dups' % (ordered, n, dup))

print('HOLISTIC CONFIG BATTERY — %d passed, %d FAILED, %d SKIPPED' % (P, F, S))
if S:
    print('  (SKIPPED checks are NOT counted as passes; TIER A contamination checks always run)')
sys.exit(1 if F else 0)
