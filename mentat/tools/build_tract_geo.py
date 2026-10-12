"""Build the 2020-census-tract to congressional-district crosswalk (S8b).

WHY THIS EXISTS. The S8b design assumed the US Census geocoder's BATCH
endpoint returns a Congressional District for each address. It does not, and
no parameter makes it: measured 2026-09-18, `POST
geocoding.geo.census.gov/geocoder/geographies/addressbatch` with
`returntype=geographies` answers a fixed CSV whose geography columns are
STATE, COUNTY, TRACT, BLOCK - the 2020 census block - under every vintage, and
`layers=54`, `layers=Congressional Districts` and `layers=all` are all ignored
(byte-identical responses). Only the ONE-ADDRESS endpoint
(`geographies/address`) carries a "119th Congressional Districts" layer, and
one address at a time is exactly what the batch design existed to avoid.

So the batch answer is bridged locally: a 2020 census tract determines its
congressional district, because the 118th and 119th districts were drawn from
whole 2020 blocks and the Census publishes the tabulation. This tool turns
that published relationship file into the same shape `district_geo` already
reads for ZIPs.

    https://www2.census.gov/geo/docs/maps-data/data/rel2020/cd-sld/
        tab20_cd11920_tract20_natl.txt     -> geo/tract_cd119.json
        tab20_cd11820_tract20_natl.txt     -> geo/tract_cd118.json

ONLY WHOLLY-CONTAINED TRACTS ARE KEPT. A tract split across two districts is
left OUT of the file on purpose: an address in it would have to be placed by a
land-area share, which is exactly the approximation the ZIP crosswalk already
makes, so such a row keeps its ZIP basis and is counted as ZIP-based rather
than claimed as an exact street match. The count of omitted tracts is recorded
in the file so the omission is visible, not silent.

Run:  python mentat/tools/build_tract_geo.py [--geo-dir DIR] [--offline DIR]
"""
from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
from pathlib import Path
import sys
import urllib.request

BASE = 'https://www2.census.gov/geo/docs/maps-data/data/rel2020/cd-sld/'
SOURCES = {'119': 'tab20_cd11920_tract20_natl.txt',
           '118': 'tab20_cd11820_tract20_natl.txt'}
AT_LARGE = {'00', '98'}


def district_key(state_fips, geoid, states):
    """'CA-12' from a CD GEOID ('0612'), or None when the state is unknown."""
    text = str(geoid or '').strip()
    if len(text) != 4 or not text.isdigit():
        return None
    stusps = states.get(text[:2]) or states.get(str(state_fips or '').zfill(2))
    if not stusps:
        return None
    number = text[2:]
    if number in AT_LARGE:
        return f'{stusps}-AL'
    return f'{stusps}-{int(number):02d}'


def state_table(geo_dir, vintage):
    """{state FIPS: 'CA'} from the boundary file this vintage already ships."""
    payload = json.loads((Path(geo_dir) / f'cd{vintage}.json').read_text(encoding='utf-8'))
    table = {}
    for stusps, entry in (payload.get('states') or {}).items():
        fips = str(entry.get('statefp') or '').zfill(2)
        if fips:
            table[fips] = stusps
    for key, entry in (payload.get('undrawn') or {}).items():
        fips = str(entry.get('statefp') or '').zfill(2)
        if fips:
            table.setdefault(fips, entry.get('stusps') or key.split('-')[0])
    return table


def read_source(vintage, offline):
    name = SOURCES[vintage]
    if offline:
        return (Path(offline) / name).read_bytes()
    request = urllib.request.Request(BASE + name, headers={
        'User-Agent': 'Mentat/1.0 (PAC evidence district map; build_tract_geo.py)'})
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.read()


def build_one(vintage, geo_dir, offline=''):
    raw = read_source(vintage, offline)
    text = raw.decode('utf-8-sig', 'replace')
    lines = text.splitlines()
    header = lines[0].split('|')
    try:
        cd_at = header.index(f'GEOID_CD{vintage}_20')
        tract_at = header.index('GEOID_TRACT_20')
    except ValueError as error:
        raise SystemExit(f'{SOURCES[vintage]} has an unexpected header: {error}')
    states = state_table(geo_dir, vintage)
    parts = {}
    for line in lines[1:]:
        fields = line.split('|')
        if len(fields) <= max(cd_at, tract_at):
            continue
        tract = fields[tract_at].strip()
        if len(tract) != 11 or not tract.isdigit():
            continue
        key = district_key(tract[:2], fields[cd_at], states)
        if not key:
            continue
        parts.setdefault(tract, set()).add(key)
    whole = {tract: next(iter(keys)) for tract, keys in parts.items() if len(keys) == 1}
    straddling = sorted(tract for tract, keys in parts.items() if len(keys) > 1)
    payload = {
        'vintage': vintage,
        'source': BASE + SOURCES[vintage],
        'built_at': _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec='seconds'),
        'basis': ('whole 2020 census tracts only; a tract split across districts is omitted '
                  'so an address in it keeps its approximate ZIP basis'),
        'tracts': len(parts), 'whole_tracts': len(whole), 'straddling_tracts': len(straddling),
        'straddling': straddling[:200],
        'districts': whole}
    out = Path(geo_dir) / f'tract_cd{vintage}.json'
    out.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
    return {'vintage': vintage, 'file': out.name, 'bytes': out.stat().st_size,
            'tracts': len(parts), 'whole': len(whole), 'straddling': len(straddling)}


def amend_index(geo_dir, results):
    path = Path(geo_dir) / 'index.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    for result in results:
        entry = (payload.setdefault('vintages', {})).setdefault(result['vintage'], {})
        entry['tract_file'] = result['file']
        entry['tract_source'] = BASE + SOURCES[result['vintage']]
        entry['tract_basis'] = ('whole 2020 census tracts (the Census CD-to-tract tabulation); '
                                'a tract split across districts is omitted and keeps the ZIP basis')
    note = ('S8b: street addresses are geocoded by the US Census BATCH geocoder, which returns a '
            '2020 census block and NOT a congressional district (measured 2026-09-18; the layers '
            'parameter is ignored). The tract_cd*.json files bridge that answer to a district.')
    notes = payload.setdefault('notes', [])
    if note not in notes:
        notes.append(note)
    path.write_text(json.dumps(payload, indent=1), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geo-dir', default=os.environ.get('MENTAT_GEO_DIR')
                        or str(Path(__file__).resolve().parent.parent / 'app' / 'geo'))
    parser.add_argument('--offline', default='', help='a folder holding the relationship files')
    parser.add_argument('--vintage', action='append', choices=sorted(SOURCES), default=None)
    arguments = parser.parse_args(argv)
    wanted = arguments.vintage or sorted(SOURCES, reverse=True)
    results = []
    for vintage in wanted:
        result = build_one(vintage, arguments.geo_dir, arguments.offline)
        results.append(result)
        print(f"{result['file']}  {result['bytes']:,} bytes  "
              f"{result['whole']:,} whole tracts  {result['straddling']:,} straddling (omitted)")
    amend_index(arguments.geo_dir, results)
    print('index.json amended with tract_file / tract_source / tract_basis')
    return 0


if __name__ == '__main__':
    sys.exit(main())
