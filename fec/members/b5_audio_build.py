# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Audio-prosody feature builder (big5_audio_prereg.md @3c94fc7). Scans audio_pilot/<member>/*.wav
(solo clips), extracts eGeMAPS v02 (88 functionals, openSMILE) per clip, duration-weighted mean-pool
per member, z-within-source, writes b5_audio_cache.json. PARALLEL (multiprocessing) + INCREMENTAL
(per-clip cache b5_audio_clipcache.json) so it can be PRE-BUILT while the fetch is still running:
re-running only featurizes new clips, then re-aggregates. Safe against concurrent fetch writes
(partial/failed wavs are skipped and retried next run). Mirrors b5_emb_build.py.
Usage: python fec/members/b5_audio_build.py [--jobs N]      (--test = synthetic member end-to-end)"""
import os, json, glob, wave, contextlib, multiprocessing as mp
import numpy as np
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from audio_rules import MIN_MEMBER_TARGET_SEC as MIN_SEC
from moneyball_core import dump_atomic
B = MB_ROOT
ADIR = os.path.join(B, 'audio_pilot'); CLIPCACHE = os.path.join(B, 'data', 'members', 'b5_audio_clipcache.json')
VERIFY = os.path.join(B, 'data', 'members', 'b5_audio_verify.json'); OUT = os.path.join(B, 'data', 'members', 'b5_audio_cache.json')
DEF_JOBS = max(1, min(8, (os.cpu_count() or 2) - 2))

def clip_seconds(path):
    try:
        with contextlib.closing(wave.open(path, 'rb')) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        try:
            import soundfile as sf; i = sf.info(path); return i.frames / float(i.samplerate or 1)
        except Exception:
            return 0.0

_SMILE = None
def _winit():
    global _SMILE
    import opensmile
    _SMILE = opensmile.Smile(feature_set=opensmile.FeatureSet.eGeMAPSv02, feature_level=opensmile.FeatureLevel.Functionals)
def _wfeat(path):
    try:
        df = _SMILE.process_file(path)
        return (path, df.values[0].astype(float).tolist(), list(df.columns), None)
    except Exception as e:
        return (path, None, None, str(e)[:80])

def featurize(paths, jobs):
    out = {}; names = None
    if jobs <= 1 or len(paths) <= 1:
        _winit()
        for p in paths:
            _, v, n, err = _wfeat(p)
            if v: out[p] = v; names = names or n
            else: print('  !! eGeMAPS fail %s (%s)' % (os.path.basename(p), err))
    else:
        with mp.Pool(jobs, initializer=_winit) as pool:
            for p, v, n, err in pool.imap_unordered(_wfeat, paths, chunksize=1):
                if v: out[p] = v; names = names or n
                else: print('  !! eGeMAPS fail %s (%s)' % (os.path.basename(p), err))
    return out, names

def build(jobs=DEF_JOBS, adir=ADIR, clipcache=CLIPCACHE, verify=VERIFY, out=OUT):
    cache = json.load(open(clipcache, encoding='utf-8')) if os.path.exists(clipcache) else {'names': None, 'clips': {}}
    clips = sorted(glob.glob(os.path.join(adir, '*', '*.wav')))
    rel = lambda c: os.path.relpath(c, adir).replace('\\', '/')
    # identity-verification gate: only clips verified SOLO_TARGET & identity != MISMATCH
    # (b5_audio_verify.json; captions + Haiku hard-signal speaker/identity check). No verify file
    # -> behave as before (keep all). This is what makes the voiceprints clean (no multi-speaker,
    # no-caption, or wrong-member contamination) without touching the featurizer or aggregation math.
    VER = {}
    _vp = verify
    if os.path.exists(_vp):
        VER = {k: v for k, v in json.load(open(_vp, encoding='utf-8')).items() if isinstance(v, dict)}
    _keep = lambda r: (not VER) or bool(VER.get(r, {}).get('keep'))
    if VER:
        _b = len(clips); clips = [c for c in clips if _keep(rel(c))]
        print('  verification gate: %d/%d clips verified-clean (dropped %d multi/no-caption/wrong-member)' % (len(clips), _b, _b - len(clips)))
    todo = [c for c in clips if rel(c) not in cache['clips']]
    print('clips on disk: %d | cached: %d | to featurize: %d (jobs=%d)' % (len(clips), len(clips) - len(todo), len(todo), jobs))
    if todo:
        feats, names = featurize(todo, jobs)
        if names and not cache['names']: cache['names'] = names
        for p, v in feats.items():
            cache['clips'][rel(p)] = {'v': [round(x, 5) for x in v], 'sec': round(clip_seconds(p), 1)}
        dump_atomic(cache, clipcache)
        print('  featurized %d clips -> %s' % (len(feats), os.path.basename(clipcache)))
    names = cache['names']
    if not names: print('no features yet — nothing to aggregate'); return
    bym = {}
    for r, d in cache['clips'].items():
        if not _keep(r): continue                                 # identity-verification gate (ignore any cached non-clean clip)
        v = np.array(d['v'], float)
        if d['sec'] < 1.0 or not np.isfinite(v).all(): continue   # drop degenerate/NaN clips (0-length downloads,
        bym.setdefault(r.split('/')[0], []).append((v, d['sec']))  # too-short segments) -> else 1 NaN row poisons all z
    feats = {}; secs = {}
    for m, cl in bym.items():
        tot = sum(s for _, s in cl)
        if tot < MIN_SEC: continue
        V = np.array([v for v, _ in cl]); w = np.array([s for _, s in cl])
        agg = (V * w[:, None]).sum(0) / w.sum()
        if not np.isfinite(agg).all(): continue                   # belt-and-suspenders member guard
        feats[m] = agg; secs[m] = tot
    if not feats: print('no members >= %ds yet (largest short of floor)' % int(MIN_SEC)); return
    M = np.array([feats[m] for m in feats]); mu = M.mean(0)
    sd = M.std(0, ddof=1) if len(feats) > 1 else np.ones(M.shape[1])
    sd = np.where(sd < 1e-12, 1.0, sd)
    Z = {m: np.clip((feats[m] - mu) / sd, -5.0, 5.0).round(5).tolist() for m in feats}  # winsorize: eGeMAPS heavy tails -> cap high-leverage outliers
    dump_atomic({'members': sorted(feats), 'feature_names': names, 'z': Z,
                 'seconds': {m: round(secs[m], 1) for m in secs}, 'feature_set': 'eGeMAPSv02',
                 'n_features': len(names), 'min_sec': MIN_SEC,
                 'note': 'audio-prosody pilot; parallel+incremental clip cache; duration-weighted mean, z-within-source. prereg @3c94fc7'},
                out)
    print('wrote %s: %d members >= %ds, %d features (eGeMAPSv02)' % (os.path.basename(out), len(feats), int(MIN_SEC), len(names)))

if __name__ == '__main__':
    jobs = DEF_JOBS
    for a in sys.argv:
        if a.startswith('--jobs='): jobs = int(a.split('=')[1])
    if '--test' in sys.argv:
        import soundfile as sf, tempfile
        root = tempfile.mkdtemp(prefix='b5_audio_test_')   # scratch root: never touches live audio_pilot / data/members
        td = os.path.join(root, 'audio_pilot', '__test__'); os.makedirs(td); os.makedirs(os.path.join(root, 'data'))
        sr = 16000
        for i in range(2):
            t = np.linspace(0, 100, 100 * sr, endpoint=False)
            sig = 0.3 * np.sin(2 * np.pi * (120 + 15 * i) * t) * (1 + 0.25 * np.sin(2 * np.pi * 3 * t))
            sf.write(os.path.join(td, 'clip%d.wav' % i), sig.astype('float32'), sr)
        out = os.path.join(root, 'data', 'b5_audio_cache.json')
        build(jobs=1, adir=os.path.join(root, 'audio_pilot'), clipcache=os.path.join(root, 'data', 'clipcache.json'),
              verify=os.path.join(root, 'data', 'verify.json'), out=out)
        assert '__test__' in json.load(open(out, encoding='utf-8'))['members'], 'synthetic member missing from output'
        print('--test OK (scratch root %s)' % root)
    else:
        build(jobs)
