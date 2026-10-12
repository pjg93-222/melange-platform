# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Audio-prosody pilot — SOLO-audio acquisition (big5_audio_prereg.md @3c94fc7). yt-dlp -> wav
(mono 16 kHz, duration-capped), into audio_pilot/<member>/, resumable via a yt-dlp download archive.
The in-sandbox YouTube CDN 403 is bypassed with the ios/web_safari/android player-client fallback
(format 18 -> extract audio). SOLO note: floor speeches are single-speaker; --search biases to floor
clips (C-SPAN-dominant). News/panel clips would contaminate the acoustic sample — a documented pilot
caveat vs the own-channel guarantee (attic/fetch_youtube_interviews). Then b5_audio_build -> b5_audio_eval.
Modes:
  --eligible [N]      rank pilot-eligible members (Pink+Blue+behavioral) by visibility; print top N
                      (default 80 — power fix, prereg Addendum 3) + seed b5_audio_sources.json
  --search [N]        AUTONOMOUS: for the top-N eligible, ytsearch floor-speech clips until >=60 s each
  (default)           fetch the explicit URLs in b5_audio_sources.json {member:[urls]}"""
import os, json, subprocess, glob, wave, contextlib
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from audio_rules import MIN_MEMBER_TARGET_SEC as MIN_SEC
B = MB_ROOT
ADIR = os.path.join(B, 'audio_pilot')
SRC = os.path.join(B, 'data', 'members', 'b5_audio_sources.json'); ARCH = os.path.join(B, 'b5_audio_dlarchive.txt')
YCLIENT = 'youtube:player_client=ios,web_safari,android'   # bypasses the sandbox CDN 403
PER_CLIP_SEC = 600     # cap any single clip at 10 min
SEARCH_N = 6           # search hits to pull per member
def _ff():
    try:
        import imageio_ffmpeg; return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception: return None

def _member_secs(md):
    t = 0.0
    for w in glob.glob(os.path.join(md, '*.wav')):
        try:
            with contextlib.closing(wave.open(w, 'rb')) as wv: t += wv.getnframes() / float(wv.getframerate() or 1)
        except Exception: pass
    return t

def _dl(target, outtmpl, ff, is_search=False):
    cmd = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '-x', '--audio-format', 'wav',
           '--ffmpeg-location', ff, '--extractor-args', YCLIENT, '--download-archive', ARCH,
           '--sleep-requests', '0.75', '--sleep-interval', '1', '--max-sleep-interval', '4',
           '--postprocessor-args', 'ffmpeg:-t %d -ac 1 -ar 16000' % PER_CLIP_SEC, '-o', outtmpl]
    cmd += (['--match-filter', 'duration>25 & duration<1800'] if is_search else ['--no-playlist'])
    cmd += [target]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        return r.returncode, (r.stderr or '')
    except Exception as e:
        return 1, str(e)[:120]

def rank_eligible(topn):
    pink = (json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8')).get('traits') or {})
    v2 = (json.load(open(B + r'\data\members\big5_v2.json', encoding='utf-8')).get('traits') or {})
    bd = (json.load(open(B + r'\behavioral_dispositions.json')).get('members') or {})
    NEED = ('cooperation_z', 'diligence_z', 'visibility_z', 'breadth_z')
    elig = [(bd[m]['visibility_z'], m) for m in pink
            if (pink[m].get('n_chunks', 0) or 0) >= 1 and m in v2
            and bd.get(m) and all(bd[m].get(k) is not None for k in NEED)]
    elig.sort(reverse=True)
    return len(elig), [m for _, m in elig[:topn]]

def eligible(topn=80):
    n, top = rank_eligible(topn)
    print('pilot-eligible members (Pink+Blue+behavioral): %d; top %d by visibility:' % (n, len(top)))
    for i, m in enumerate(top, 1): print('  %2d. %s' % (i, m))
    if not os.path.exists(SRC):
        json.dump({m: [] for m in top}, open(SRC, 'w'), indent=1)
        print('\nseeded %s (for manifest mode); or use --search N for autonomous floor-clip fetch' % os.path.basename(SRC))

def search_fetch(topn):
    ff = _ff()
    if not ff: print('!! imageio-ffmpeg missing — pip install imageio-ffmpeg'); return
    _, members = rank_eligible(topn)
    print('SEARCH-FETCH floor clips for %d members (>= %ds each; solo-biased floor speeches):' % (len(members), int(MIN_SEC)))
    ok = 0
    for member in members:
        md = os.path.join(ADIR, member); os.makedirs(md, exist_ok=True)
        have = _member_secs(md)
        if have >= MIN_SEC: print('  have %s: %.0fs (skip)' % (member, have)); ok += 1; continue
        q = 'ytsearch%d:%s floor speech c-span' % (SEARCH_N, member)
        rc, err = _dl(q, os.path.join(md, 'clip_%(id)s.%(ext)s'), ff, is_search=True)
        got = _member_secs(md)
        if got >= MIN_SEC: ok += 1; print('  ok  %s: %.0fs' % (member, got))
        else: print('  LOW %s: %.0fs (rc=%d %s)' % (member, got, rc, (err.strip().splitlines()[-1][:50] if err.strip() else '')))
    print('search-fetch: %d/%d members cleared %ds. Next: python fec/members/b5_audio_build.py' % (ok, len(members), int(MIN_SEC)))

def fetch():
    ff = _ff()
    if not ff: print('!! imageio-ffmpeg not available — pip install imageio-ffmpeg'); return
    if not os.path.exists(SRC): print('!! %s missing — run --eligible first (or use --search N)' % os.path.basename(SRC)); return
    srcs = json.load(open(SRC)); n_ok = 0
    for member, urls in srcs.items():
        md = os.path.join(ADIR, member); os.makedirs(md, exist_ok=True)
        for url in (urls or []):
            rc, err = _dl(url, os.path.join(md, 'clip_%(id)s.%(ext)s'), ff)
            if rc == 0: n_ok += 1; print('  ok  %s <- %s' % (member, url[:60]))
            else: print('  FAIL %s <- %s (%s)' % (member, url[:50], (err.strip().splitlines()[-1][:70] if err.strip() else '?')))
    print('fetched %d clips across %d members. Next: python fec/members/b5_audio_build.py' % (n_ok, len(srcs)))

if __name__ == '__main__':
    args = sys.argv
    n = next((int(a) for a in args if a.isdigit()), 80)
    if '--eligible' in args: eligible(n)
    elif '--search' in args: search_fetch(n)
    else: fetch()
