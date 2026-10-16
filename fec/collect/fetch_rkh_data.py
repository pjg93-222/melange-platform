import os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-replication-lookup'}
from paths import ROOT
OUT = os.path.join(ROOT,'fec','liwc','rkh')
os.makedirs(OUT, exist_ok=True)
FILES = {
    2732477: 'LifetimeMeansHouse.tab',          # JDH6KR: House lifetime OCEAN means
    2732485: 'LifetimeMeansHouse.do',           # its codebook-ish do-file
    2732478: 'congressbios.tab',                # member crosswalk
    2732458: 'readme.rtf',
    2732494: 'hou114.tab',                      # 114th Congress scores (newest era)
    3138856: 'LifetimeMeansHouse_byCong.tab',   # CORRXG: House by-Congress
    3138857: 'LifetimeMeansSenate_byCong.tab',  # CORRXG: SENATE by-Congress
}
failed = []
for fid, name in FILES.items():
    p = os.path.join(OUT, name)
    if os.path.exists(p):
        print('cached:', name); continue
    u = 'https://dataverse.harvard.edu/api/access/datafile/%d' % fid
    part = p + '.part'
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=120) as r:
            ctype, clen = r.headers.get('Content-Type', ''), r.headers.get('Content-Length')
            data = r.read()
        if clen and clen.isdigit() and int(clen) != len(data):
            raise ValueError('short read %d/%s' % (len(data), clen))
        if 'html' in ctype.lower() or data.lstrip()[:1] == b'<' or b'<html' in data[:512].lower():
            raise ValueError('HTML/error page, not a data file (%s)' % ctype)
        with open(part, 'wb') as fh:
            fh.write(data)
        os.replace(part, p)
        print('fetched: %-34s %9d B' % (name, len(data)))
    except Exception as e:
        failed.append(name)
        if os.path.exists(part):
            os.remove(part)
        print('FAILED : %-34s %s' % (name, str(e)[:120]))

# peek headers
for name in ('LifetimeMeansHouse.tab', 'LifetimeMeansHouse_byCong.tab', 'LifetimeMeansSenate_byCong.tab', 'hou114.tab', 'congressbios.tab'):
    p = os.path.join(OUT, name)
    if os.path.exists(p):
        with open(p, encoding='utf-8', errors='replace') as fh:
            lines = [next(fh, '') for _ in range(2)]
        print('\n---', name)
        print('  header:', lines[0].strip()[:220])
        print('  row1  :', lines[1].strip()[:220])
if failed:
    print('\nFAILED files: %s' % ', '.join(failed)); sys.exit(1)
