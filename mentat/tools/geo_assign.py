"""Build the v2 lookup tables, and bulk-assign a source's points, with GeoPandas.

Offline and dev-time only (lane G, 2026-09-25), under the SYSTEM Python that has
GeoPandas; the console reads what this writes through mentat/app/geo_lookup.py
and never imports GeoPandas, Shapely, pyproj or GeoPy itself.

    python mentat/tools/geo_assign.py tables                 # every lookup table
    python mentat/tools/geo_assign.py tables --states NY,NC  # tract and place tables only
    python mentat/tools/geo_assign.py pins                   # lookup/hex_pins.json only
    python mentat/tools/geo_assign.py assign --snapshot mentat/.cache/pac-evidence/culac-20260921
    python mentat/tools/geo_assign.py assign --points rows.csv [--force]

THE TABLES (written to the staging tree mentat/.cache/geo_build/v2/lookup/, which
build_geo_layers.py --install moves to mentat/app/geo/v2/lookup/ in one rename; each
is listed in v2/index.json with its sha256)

  states.json        STATEFP -> [usps, name, frame region, placed, lat, lon]
  tract/<SS>.json    2020 tract -> [lat, lon, cd116, cd118, cd119, sldu, sldl]
  zcta/<d>.json      ZCTA (by first digit) -> [lat, lon, state, county, tract, place,
                     cd116, cd118, cd119, sldu, sldl, h10, h25, h50]
  place/<SS>.json    place GEOID -> [name, lsad, funcstat, lat, lon, county, tract, zcta,
                     cd116, cd118, cd119, sldu, sldl, h10, h25, h50], plus "names":
                     normalised city name -> [place GEOIDs, best first]
  hex_pins.json      1,000 seeded points with the tool's own EPSG:5070 metres (pyproj)
                     and hexagon ids, which the console test must reproduce

  A district value is a GEOID when the unit lies in one district and
  [[GEOID, share], ...] (largest first, shares of land area in EPSG:5070, parts
  under 1% dropped) when it straddles. Points (a ZCTA's or a place's internal
  point) are joined with gpd.sjoin(predicate='within'); a point the 500k coast
  leaves just offshore takes the nearest unit of its own state within 20 km.

HOW A TRACT GETS ITS DISTRICTS (the "basis" block of every tract file repeats it)

  cd118, cd119   whole tracts: the Census CD-to-tract tabulation, as shipped in
                 geo/tract_cd118.json and tract_cd119.json (exact). Every other
                 tract straddles two or more districts by that tabulation, and its
                 shares come from overlaying the 500k tract with the 20m district
                 polygons - approximate, because no 500k district file is cached.
  cd116          the overlay only: the Census never tabulated 2020 tracts against
                 the 116th map. Approximate everywhere; 2022 draws the 118th map
                 anyway (geo/index.json).
  sldu, sldl     overlay with the 500k state-legislative polygons.
  A point's district (ZCTA, place, or an assigned point) is its tract's when the
  tract is whole, else the 20m polygon holding the point if that polygon is one of
  the tract's districts, else the tract's largest share.

BULK ASSIGNMENT

  assign reads a source's rows (a PAC evidence snapshot's receipts and payees, or
  a CSV/JSON of id, latitude, longitude, tract, zip, city, state), places each at
  the best precision (address | zip | city | state | none, as geo_lookup.locate
  does), and joins every located point to every level with gpd.sjoin
  (predicate='within'), one state at a time. The result is cached under
  mentat/state/geography/assigned-v2/<source sha256[:16]>-<tables stamp>.json, keyed
  by the source's hash, and a second run is a cache hit. Runtime state, never
  versioned or served: it is derived from contributor rows, so it does NOT go
  under app/geo.

  City names the place gazetteer cannot match may be looked up with GeoPy
  (Nominatim, one request per second, user agent "Mentat local console",
  answers cached in mentat/.cache/geo/geopy_cities.json) - here, in this tool,
  and only for those leftovers. When GeoPy is not installed the step is skipped
  and the leftovers are counted; nothing is installed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import pyproj
import shapely

TOOLS = Path(__file__).resolve().parent
MENTAT = Path(os.environ.get('MENTAT_DIR') or TOOLS.parent)
sys.path.insert(0, str(MENTAT / 'tools'))
sys.path.insert(0, str(MENTAT / 'app'))
import build_district_geo as builder                                # noqa: E402
import build_geo_layers as layers                                   # noqa: E402
import geo_lookup                                                   # noqa: E402

DEFAULT_CACHE = MENTAT / '.cache' / 'geo'
# tables/pins write the STAGING tree (build_geo_layers.DEFAULT_OUT; installed with
# build_geo_layers.py --install); assign reads the installed tree, else staging.
STAGING_V2 = layers.DEFAULT_OUT
INSTALLED_V2 = layers.INSTALLED
DEFAULT_V2 = STAGING_V2
DEFAULT_ASSIGNED = MENTAT / 'state' / 'geography' / 'assigned-v2'
TODAY = MENTAT / 'app' / 'geo'
TOOL = 'mentat/tools/geo_assign.py'
VERSION = '2026-09-25.1'
GAZ_PLACE = '2024_Gaz_place_national.zip'
NEAREST_M = 20000.0
SHARE_FLOOR = 0.01
VINTAGES = ('cd116', 'cd118', 'cd119')
USER_AGENT = 'Mentat local console'
GEOPY_CACHE = 'geopy_cities.json'
#: American Samoa, Guam, the Northern Marianas and the US Virgin Islands have no
#: 20m district polygon but exactly one (delegate) seat each by statute; their
#: district is <STATEFP>98, the code the Census gives a delegate seat - the same
#: translation build_district_geo's TERRITORY_FALLBACK and district_geo's
#: AT_LARGE_JURISDICTIONS make. Not a guess: there is no other seat to be in.
DELEGATE_ONLY = frozenset(builder.TERRITORY_FALLBACK)
CT_FIPS = '09'

TRACT_FIELDS = ['lat', 'lon', 'cd116', 'cd118', 'cd119', 'sldu', 'sldl']
ZCTA_FIELDS = ['lat', 'lon', 'state', 'county', 'tract', 'place', 'cd116', 'cd118', 'cd119',
               'sldu', 'sldl', 'h10', 'h25', 'h50']
PLACE_FIELDS = ['name', 'lsad', 'funcstat', 'lat', 'lon', 'county', 'tract', 'zcta', 'cd116',
                'cd118', 'cd119', 'sldu', 'sldl', 'h10', 'h25', 'h50']
ASSIGN_FIELDS = ['precision', 'lat', 'lon', 'state', 'county', 'tract', 'zcta', 'place', 'cd116',
                 'cd118', 'cd119', 'sldu', 'sldl', 'h10', 'h25', 'h50', 'basis']
TRACT_BASIS = {
    'cd118': 'whole tracts from the Census CD-to-tract tabulation (geo/tract_cd118.json); every '
             'other tract straddles and carries land-area shares from overlaying the 500k tract '
             'with the 20m district polygons (approximate)',
    'cd119': 'as cd118, from geo/tract_cd119.json',
    'cd116': 'overlay of the 500k tract with the 20m 116th-map polygons only (no Census '
             'tabulation of 2020 tracts against the 116th map exists); approximate',
    'sldu': 'overlay with the 500k upper-chamber polygons',
    'sldl': 'overlay with the 500k lower-chamber polygons',
    'lat_lon': 'a point inside the tract (representative point of the 500k polygon)',
}


# ------------------------------------------------------------------ helpers

def log_print(*parts):
    print(*parts, flush=True)


def write_json(path, payload, indent=None):
    text = json.dumps(payload, separators=(',', ':') if indent is None else None, indent=indent,
                      ensure_ascii=False)
    return layers.write_bytes_atomic(Path(path), (text + '\n').encode('utf-8'))


def r5(value):
    return round(float(value), 5)


def shares_value(pairs):
    """A GEOID for one district, [[GEOID, share], ...] for several (largest first)."""
    total = sum(area for _g, area in pairs)
    if total <= 0:
        return None
    kept = [(g, area / total) for g, area in pairs if area / total >= SHARE_FLOOR] or \
        [max(pairs, key=lambda item: item[1])]
    norm = sum(share for _g, share in kept)
    kept = sorted(((g, share / norm) for g, share in kept), key=lambda item: (-item[1], item[0]))
    if len(kept) == 1:
        return kept[0][0]
    return [[g, round(share, 3)] for g, share in kept]


def primary(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return value[0][0]
    return None


def candidates(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [g for g, _s in value]
    return []


def join_within(points, polygons, key, nearest=True):
    """Series (points' index) of polygons[key] holding each point; nearest within 20 km."""
    if not len(points) or not len(polygons):
        return pd.Series([None] * len(points), index=points.index, dtype=object)
    joined = gpd.sjoin(points[['geometry']], polygons[[key, 'geometry']], how='left',
                       predicate='within')
    joined = joined[~joined.index.duplicated(keep='first')]
    result = joined[key].astype(object).where(joined[key].notna(), None)
    missing = result.isna()
    if nearest and missing.any():
        lost = points.loc[missing[missing].index, ['geometry']].to_crs(5070)
        near = gpd.sjoin_nearest(lost, polygons[[key, 'geometry']].to_crs(5070), how='left',
                                 max_distance=NEAREST_M)
        near = near[~near.index.duplicated(keep='first')]
        found = near[key].dropna()
        result.loc[found.index] = found.values
    return result.reindex(points.index)


def overlay_shares(units, districts, unit_key, district_key):
    """{unit id: value} of district land-area shares, EPSG:5070."""
    if not len(units) or not len(districts):
        return {}
    left = units[[unit_key, 'geometry']].rename(columns={unit_key: 'unit'}).to_crs(5070)
    right = districts[[district_key, 'geometry']].rename(columns={district_key: 'district'}).to_crs(5070)
    pieces = gpd.overlay(left, right, how='intersection', keep_geom_type=True)
    pieces['area'] = pieces.geometry.area
    grouped = pieces.groupby(['unit', 'district'])['area'].sum()
    out = {}
    for (unit, district), area in grouped.items():
        out.setdefault(unit, []).append((district, float(area)))
    return {unit: shares_value(pairs) for unit, pairs in out.items()}


def hex_columns(lon, lat):
    return {f'h{size}': layers.hex_ids_lonlat(lon, lat, size) for size in layers.HEX_SIZES}


# ------------------------------------------------------------- the context

class Context:
    """Everything national the per-state steps share (small layers only)."""

    def __init__(self, cache, v2, log=log_print):
        self.cache = Path(cache)
        self.v2 = Path(v2)
        self.log = log
        self.sources = layers.Sources(cache)
        self.frame = layers.Frame(cache, log=lambda *_: None)
        counties = self.sources.read('county')
        self.counties = counties[['GEOID', 'STATEFP', 'geometry']]
        self.outlines = (counties.assign(statefp=counties['STATEFP'])[['statefp', 'geometry']]
                         .dissolve(by='statefp', method='coverage').reset_index())
        self.districts = {}
        self.key_to_geoid = {}
        for vintage in VINTAGES:
            frame_df = self.sources.read(vintage)
            rows = layers.cd_rows(frame_df, self.frame)
            rows = rows.rename(columns={'key': 'GEOID'})
            self.districts[vintage] = rows[['GEOID', 'statefp', 'district', 'geometry']]
            self.key_to_geoid[vintage] = dict(zip(rows['district'], rows['GEOID']))
            for fips, (usps, _name) in builder.TERRITORY_FALLBACK.items():
                self.key_to_geoid[vintage].setdefault(f'{usps}-AL', f'{fips}98')
        self.sld = {}
        for chamber, seat in (('sldu', 'SLDUST'), ('sldl', 'SLDLST')):
            frame_df = self.sources.read(chamber)
            frame_df = frame_df[frame_df[seat].map(layers.has_seat)]
            self.sld[chamber] = frame_df[['GEOID', 'STATEFP', 'geometry']]
        self.census_whole = {}
        for vintage in ('cd118', 'cd119'):
            path = TODAY / f'tract_{vintage}.json'
            payload = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
            mapping = {}
            for tract, key in (payload.get('districts') or {}).items():
                geoid = self.key_to_geoid[vintage].get(key)
                if geoid:
                    mapping[tract] = geoid
            self.census_whole[vintage] = mapping
            self.census_source = {v: f'geo/tract_{v}.json' for v in ('cd118', 'cd119')}
        log(f'  context: {len(self.counties):,} counties, districts '
            f'{ {v: len(d) for v, d in self.districts.items()} }, sld '
            f'{ {c: len(d) for c, d in self.sld.items()} }, census whole tracts '
            f'{ {v: len(m) for v, m in self.census_whole.items()} }')

    def state_layers(self, statefp, split_dir=None):
        """This state's tracts, places, ZCTAs, counties, SLDs and districts (lon/lat)."""
        out = {'county': self.counties[self.counties['STATEFP'] == statefp].rename(
            columns={'GEOID': 'gid'})}
        # the filter's own field must be among the columns read: with columns=['GEOID']
        # alone OGR evaluates STATEFP as missing and returns no rows at all
        tracts = self.sources.read('tract', where=f"STATEFP = '{statefp}'",
                                   columns=['GEOID', 'STATEFP'])
        out['tract'] = tracts.rename(columns={'GEOID': 'gid'})[['gid', 'geometry']]
        places = self.sources.read('place', where=f"STATEFP = '{statefp}'",
                                   columns=['GEOID', 'STATEFP'])
        out['place'] = places.rename(columns={'GEOID': 'gid'})[['gid', 'geometry']]
        if len(tracts) == 0 and statefp in ('01', '06', '36', '48'):
            raise SystemExit(f'no tracts read for STATEFP {statefp}: the reader is broken')
        if split_dir is not None:
            zctas = layers.read_zcta_state(split_dir, statefp)
            out['zcta'] = (zctas.rename(columns={'GEOID20': 'gid'})[['gid', 'geometry']]
                           if zctas is not None else gpd.GeoDataFrame({'gid': []}, geometry=[],
                                                                      crs=4269))
        for chamber, frame_df in self.sld.items():
            out[chamber] = frame_df[frame_df['STATEFP'] == statefp].rename(columns={'GEOID': 'gid'})
        for vintage, frame_df in self.districts.items():
            out[vintage] = frame_df[frame_df['statefp'] == statefp].rename(columns={'GEOID': 'gid'})
        return out


# ---------------------------------------------------------------- the tables

def tract_table(context, statefp, state_layers):
    """{tract: [lat, lon, cd116, cd118, cd119, sldu, sldl]} plus agreement stats."""
    tracts = state_layers['tract']
    inside = tracts.geometry.representative_point()
    values = {level: overlay_shares(tracts, state_layers[level], 'gid', 'gid')
              for level in (*VINTAGES, 'sldu', 'sldl')}
    stats = {'tracts': len(tracts), 'nearest': {}, 'census_whole': {}, 'census_disagree': {}}
    for level in (*VINTAGES, 'sldu', 'sldl'):
        missing = [gid for gid in tracts['gid'] if gid not in values[level]]
        if missing and len(state_layers[level]):
            points = gpd.GeoDataFrame({'gid': missing},
                                      geometry=inside[tracts['gid'].isin(missing)].values, crs=4269)
            found = join_within(points, state_layers[level], 'gid')
            for gid, value in zip(missing, found.tolist()):
                if value:
                    values[level][gid] = value
            stats['nearest'][level] = len(missing)
    if statefp in DELEGATE_ONLY:
        for vintage in VINTAGES:
            if not len(state_layers[vintage]):
                values[vintage] = {gid: f'{statefp}98' for gid in tracts['gid']}
                stats.setdefault('delegate_seat', []).append(vintage)
    # The Census tabulation (and the Census batch geocoder, vintage 420) key tracts by
    # their 2020 GEOIDs. Connecticut's 2022 planning regions replaced its counties in
    # tract GEOIDs (09001-09015 became 09110-09190) with every TRACTCE unchanged and
    # unique statewide, so there the two meet by TRACTCE.
    renumbered = statefp == CT_FIPS
    present = set(tracts['gid'])
    current_of = {gid[5:]: gid for gid in present}
    census_only = {}
    for vintage in ('cd118', 'cd119'):
        whole = {}
        for code, district in context.census_whole.get(vintage, {}).items():
            if code[:2] != statefp:
                continue
            current = current_of.get(code[5:]) if renumbered else (code if code in present else None)
            if current is None:
                census_only.setdefault(code, {})[vintage] = district
                continue
            whole[current] = district
        agree = disagree = 0
        examples = []
        for gid in tracts['gid']:
            census = whole.get(gid)
            if census is None:
                continue
            overlay = primary(values[vintage].get(gid))
            if overlay == census:
                agree += 1
            else:
                disagree += 1
                if len(examples) < 5:
                    examples.append([gid, census, overlay])
            values[vintage][gid] = census
        stats['census_whole'][vintage] = agree + disagree
        stats['census_disagree'][vintage] = {'count': disagree, 'examples': examples,
                                             'agree': agree}
    rows = {}
    for gid, point in zip(tracts['gid'], inside):
        rows[gid] = [r5(point.y), r5(point.x)] + [values[level].get(gid) for level in
                                                  (*VINTAGES, 'sldu', 'sldl')]
    # Tracts the Census tabulates but the 500k cartographic file leaves out (water-only
    # tracts, 99xxxx): no polygon, so no point and no overlay - the Census district only.
    for code, found in sorted(census_only.items()):
        if code not in rows:
            rows[code] = [None, None, None, found.get('cd118'), found.get('cd119'), None, None]
    stats['census_only'] = sorted(census_only)
    return rows, stats


def point_districts(points, tract_ids, tract_rows, state_layers, statefp=None):
    """{level: list} for points: the tract rule for districts, sjoin for the rest."""
    out = {}
    for vintage in VINTAGES:
        pip = join_within(points, state_layers[vintage], 'gid', nearest=True).tolist()
        if statefp in DELEGATE_ONLY and not len(state_layers[vintage]):
            pip = [f'{statefp}98'] * len(points)
        chosen = []
        for tract_id, inside in zip(tract_ids, pip):
            value = tract_rows.get(tract_id, [None] * 7)[2 + VINTAGES.index(vintage)] \
                if tract_id else None
            if isinstance(value, str):
                chosen.append(value)
            elif value and inside in candidates(value):
                chosen.append(inside)
            elif value:
                chosen.append(primary(value))
            else:
                chosen.append(inside)
        out[vintage] = chosen
    for chamber in ('sldu', 'sldl'):
        out[chamber] = join_within(points, state_layers[chamber], 'gid').tolist()
    return out


def read_gaz_places(cache):
    with zipfile.ZipFile(Path(cache) / GAZ_PLACE) as archive:
        name = next(entry for entry in archive.namelist() if entry.lower().endswith('.txt'))
        text = archive.read(name).decode('utf-8-sig', 'replace')
    lines = text.splitlines()
    header = [field.strip() for field in lines[0].split('\t')]
    rows = []
    for line in lines[1:]:
        fields = [field.strip() for field in line.split('\t')]
        if len(fields) < len(header):
            continue
        row = dict(zip(header, fields))
        try:
            row['lat'], row['lon'] = float(row['INTPTLAT']), float(row['INTPTLONG'])
            row['aland'] = int(row['ALAND'] or 0)
        except ValueError:
            continue
        rows.append(row)
    return rows


def place_name_index(rows):
    """{normalised key: [GEOIDs, best first]} with exact names before aliases.

    Aliases are deliberately few: the FIRST part of a hyphenated or slashed name
    (the consolidated governments - Nashville-Davidson, Louisville/Jefferson
    County, Athens-Clarke County, Augusta-Richmond County - are filed under the
    city's own name) and "Urban Honolulu" as Honolulu. Later parts are county
    names or the second city of a twin name (Winston-Salem's Salem) and are not
    aliased: a city the gazetteer cannot name stays unmatched rather than guessed.
    """
    entries = {}
    for row in rows:
        base = geo_lookup.strip_lsad(row['NAME'])
        keys = {(geo_lookup.normalise_place_name(base), 0)}
        pieces = re.split(r'[-/]', base)
        if len(pieces) > 1:
            keys.add((geo_lookup.normalise_place_name(geo_lookup.strip_lsad(pieces[0])), 1))
        if base.startswith('Urban '):
            keys.add((geo_lookup.normalise_place_name(base[len('Urban '):]), 1))
        for key, alias in keys:
            if key:
                entries.setdefault(key, []).append(
                    (alias, 0 if row['FUNCSTAT'] == 'A' else 1, -row['aland'], row['GEOID']))
    index = {}
    for key, found in entries.items():
        best = {}
        for alias, funcstat, aland, geoid in found:
            if geoid not in best or (alias, funcstat, aland) < best[geoid]:
                best[geoid] = (alias, funcstat, aland)
        index[key] = [geoid for geoid, _rank in sorted(best.items(), key=lambda kv: (kv[1], kv[0]))]
    return index


def build_tables(args, log=log_print):
    meter = layers.PeakMeter()
    lookup = Path(args.v2) / 'lookup'
    meter.start('context (counties, districts, legislatures)')
    context = Context(args.cache, args.v2, log)
    meter.end(log)
    subset = {code.strip().upper() for code in args.states.split(',') if code.strip()} or None
    statefps = [fp for fp in sorted(context.outlines['statefp'])
                if subset is None or context.frame.codes.get(fp) in subset]
    written = {}

    def record(path, payload, level, state, statefp, features, extra=None):
        size, digest = write_json(path, payload)
        rel = path.relative_to(Path(args.v2)).as_posix()
        written[rel] = dict({'level': level, 'form': 'lookup', 'tier': 'lookup', 'state': state,
                             'statefp': statefp, 'features': features, 'bytes': size,
                             'sha256': digest, 'path': rel}, **(extra or {}))

    # states.json
    rows = {}
    for fp, geom in zip(context.outlines['statefp'], context.outlines.geometry):
        usps = context.frame.codes.get(fp)
        region = context.frame.region_of(fp)
        point = geom.representative_point()
        rows[fp] = [usps, context.frame.names.get(usps, usps), region,
                    region in context.frame.placements, r5(point.y), r5(point.x)]
    record(lookup / 'states.json',
           {'version': 2, 'level': 'state', 'fields': ['usps', 'name', 'region', 'placed', 'lat', 'lon'],
            'rows': dict(sorted(rows.items())),
            'by_usps': {row[0]: fp for fp, row in sorted(rows.items()) if row[0]},
            'source': context.sources.info('county')['file']},
           'state', None, None, len(rows),
           {'source': {'file': context.sources.info('county')['file'],
                       'sha256': context.sources.info('county')['sha256']}})

    meter.start('zcta homes + split')
    homes, homeless, _nearest = layers.zcta_homes(args.cache, context.outlines, log=lambda *_: None)
    split_dir = layers.split_zcta(context.sources, Path(args.cache) / 'v2work', homes,
                                  log=lambda *_: None)
    gaz_zcta, _bad = layers.zip_points.read_gazetteer(Path(args.cache) / layers.SOURCES['gaz_zcta'])
    meter.end(log)
    gaz_place = read_gaz_places(args.cache)
    zcta_rows = {}
    stats_all = {}
    place_counts = {}
    for statefp in statefps:
        state = context.frame.codes.get(statefp)
        meter.start(f'lookup {state}')
        state_layers = context.state_layers(statefp, split_dir)
        # tracts
        t_rows, stats = tract_table(context, statefp, state_layers)
        stats_all[state] = stats
        tract_payload = {'version': 2, 'level': 'tract', 'statefp': statefp, 'state': state,
                         'fields': TRACT_FIELDS, 'basis': TRACT_BASIS, 'stats': stats,
                         'rows': dict(sorted(t_rows.items()))}
        if statefp == CT_FIPS:
            tract_payload['by_tractce'] = {gid[5:]: gid for gid in sorted(t_rows)
                                           if t_rows[gid][0] is not None}
            tract_payload['renumbered'] = (
                'Connecticut replaced its counties with planning regions in 2022: tract GEOIDs '
                'went from 09001-09015 to 09110-09190 with every TRACTCE unchanged and unique '
                'statewide. The Census batch geocoder (vintage 420) and geo/tract_cd*.json use '
                'the 2020 codes, so geo_lookup.for_tract matches a code it does not find by '
                'TRACTCE through by_tractce.')
        record(lookup / 'tract' / f'{statefp}.json', tract_payload,
               'tract', state, statefp, len(t_rows),
               {'source': {'file': context.sources.info('tract')['file'],
                           'sha256': context.sources.info('tract')['sha256']}})
        # ZCTA internal points whose home is this state
        if subset is None:
            mine = [(z, la, lo) for z, la, lo in gaz_zcta if homes.get(z) == statefp]
            if mine:
                points = gpd.GeoDataFrame({'zcta': [z for z, _a, _o in mine]},
                                          geometry=shapely.points([o for _z, _a, o in mine],
                                                                  [a for _z, a, _o in mine]),
                                          crs=4269)
                assigned = assign_state_points(points, state_layers, t_rows, statefp=statefp)
                for position, (z, la, lo) in enumerate(mine):
                    zcta_rows[z] = [r5(la), r5(lo), statefp] + [assigned[level][position] for level in
                                                                ('county', 'tract', 'place', *VINTAGES,
                                                                 'sldu', 'sldl', 'h10', 'h25', 'h50')]
        # places
        mine = [row for row in gaz_place if row['GEOID'][:2] == statefp]
        p_rows = {}
        if mine:
            points = gpd.GeoDataFrame({'place': [row['GEOID'] for row in mine]},
                                      geometry=shapely.points([row['lon'] for row in mine],
                                                              [row['lat'] for row in mine]), crs=4269)
            assigned = assign_state_points(points, state_layers, t_rows, with_place=False,
                                           statefp=statefp)
            for position, row in enumerate(mine):
                p_rows[row['GEOID']] = [row['NAME'], row['LSAD'], row['FUNCSTAT'], r5(row['lat']),
                                        r5(row['lon'])] + [assigned[level][position] for level in
                                                           ('county', 'tract', 'zcta', *VINTAGES,
                                                            'sldu', 'sldl', 'h10', 'h25', 'h50')]
        names = place_name_index(mine)
        place_counts[state] = len(p_rows)
        record(lookup / 'place' / f'{statefp}.json',
               {'version': 2, 'level': 'place', 'statefp': statefp, 'state': state,
                'fields': PLACE_FIELDS, 'rows': dict(sorted(p_rows.items())),
                'names': dict(sorted(names.items())),
                'names_rule': 'geo_lookup.normalise_place_name of the gazetteer NAME without its '
                              'legal suffix (geo_lookup.strip_lsad), plus each part of a hyphenated '
                              'or slashed name and "Urban X" as X; candidates ordered exact before '
                              'alias, incorporated (FUNCSTAT A) before statistical, larger land area '
                              'first'},
               'place', state, statefp, len(p_rows),
               {'source': {'file': GAZ_PLACE, 'sha256': layers.sha256_file(Path(args.cache) / GAZ_PLACE)}})
        del state_layers
        meter.end(log)
    if subset is None:
        by_digit = {}
        for z, row in zcta_rows.items():
            by_digit.setdefault(z[0], {})[z] = row
        for digit, rows_ in sorted(by_digit.items()):
            record(lookup / 'zcta' / f'{digit}.json',
                   {'version': 2, 'level': 'zcta', 'digit': digit, 'fields': ZCTA_FIELDS,
                    'basis': {'point': 'ZCTA internal point, 2024 gazetteer',
                              'levels': 'gpd.sjoin(predicate="within") of the point, nearest unit '
                                        'of its state within 20 km when the 500k coast leaves it '
                                        'offshore; districts by the tract rule (tract files)',
                              'shares': 'ZIP-to-district land-area shares stay in geo/zcta_cd*.json '
                                        '(district_geo.district_for_zip)'},
                    'without_state': homeless, 'rows': dict(sorted(rows_.items()))},
                   'zcta', None, None, len(rows_),
                   {'source': {'file': layers.SOURCES['zcta'],
                               'sha256': context.sources.info('zcta')['sha256']}})
        meter.start('hex pins')
        pins = hex_pins()
        record(lookup / 'hex_pins.json', pins, 'hex_pins', None, None, len(pins['points']),
               {'source': {'file': 'computed (pyproj EPSG:4269 -> EPSG:5070)', 'sha256': None}})
        meter.end(log)
    memory = meter.close()
    merge_index(Path(args.v2), written, {'lookup_tables': {
        'tool': TOOL, 'tool_version': VERSION,
        'built_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'states': len(statefps), 'zcta_rows': len(zcta_rows), 'places': sum(place_counts.values()),
        'census_agreement': {state: {v: s['census_disagree'][v] for v in s['census_disagree']}
                             for state, s in stats_all.items()},
        'memory': memory}})
    total_agree = sum(s['census_disagree'][v]['agree'] for s in stats_all.values()
                      for v in s['census_disagree'])
    total_disagree = sum(s['census_disagree'][v]['count'] for s in stats_all.values()
                         for v in s['census_disagree'])
    log(f'  tables: {len(written)} files; overlay vs Census whole tracts: {total_agree:,} agree, '
        f'{total_disagree:,} disagree; process peak {memory["process_peak_wset_mb"]} MB')
    return written, memory


def assign_state_points(points, state_layers, tract_rows, with_place=True, statefp=None):
    """{level: list aligned with points} for one state's points."""
    out = {'county': join_within(points, state_layers['county'], 'gid').tolist(),
           'tract': join_within(points, state_layers['tract'], 'gid').tolist()}
    out['place'] = (join_within(points, state_layers['place'], 'gid', nearest=False).tolist()
                    if with_place else [None] * len(points))
    zctas = state_layers.get('zcta')
    out['zcta'] = (join_within(points, zctas, 'gid').tolist() if zctas is not None
                   else [None] * len(points))
    out.update(point_districts(points, out['tract'], tract_rows, state_layers, statefp))
    lon = shapely.get_x(points.geometry.values)
    lat = shapely.get_y(points.geometry.values)
    out.update(hex_columns(lon, lat))
    return out


def hex_pins(count=1000, seed=20260925):
    """Seeded points over every placed and unplaced region, with the tool's answers."""
    rng = np.random.default_rng(seed)
    boxes = [((24.0, 49.5), (-125.0, -66.9), 860), ((51.0, 71.5), (-190.0, -129.0), 60),
             ((18.9, 22.3), (-160.3, -154.8), 30), ((17.8, 18.6), (-67.3, -64.5), 20),
             ((13.2, 20.6), (144.6, 146.1), 20), ((-14.4, -14.1), (-170.9, -169.4), 10)]
    lats, lons = [], []
    for (lat0, lat1), (lon0, lon1), n in boxes:
        lats.append(rng.uniform(lat0, lat1, n))
        lons.append(rng.uniform(lon0, lon1, n))
    # rounded FIRST, so the stored coordinates are exactly the ones projected and hexed
    lat = np.round(np.concatenate(lats)[:count], 7)
    lon = np.round((np.concatenate(lons)[:count] + 180.0) % 360.0 - 180.0, 7)
    x, y = layers.to_5070().transform(lon, lat)
    points = []
    ids = {size: layers.hex_ids_lonlat(lon, lat, size) for size in layers.HEX_SIZES}
    for i in range(len(lat)):
        points.append([float(lat[i]), float(lon[i]), round(float(x[i]), 4), round(float(y[i]), 4)] +
                      [ids[size][i] for size in layers.HEX_SIZES])
    return {'version': 2, 'seed': seed, 'crs': 'EPSG:4269 -> EPSG:5070 (pyproj '
            f'{pyproj.__version__}, PROJ {pyproj.proj_version_str})',
            'fields': ['lat', 'lon', 'x', 'y', 'h10', 'h25', 'h50'],
            'rule': 'geo_lookup.hex_id(lat, lon, size) and albers_5070(lat, lon) must reproduce '
                    'these (x, y to 1 mm); a JS port can pin to the same file',
            'points': points}


def merge_index(v2, written, notes):
    index_path = Path(v2) / 'index.json'
    payload = json.loads(index_path.read_text(encoding='utf-8')) if index_path.is_file() else {
        'version': 2, 'files': []}
    entries = {entry['path']: entry for entry in payload.get('files', [])}
    entries.update(written)
    entries = {path: entry for path, entry in entries.items() if (Path(v2) / path).is_file()}
    payload['files'] = [entries[path] for path in sorted(entries)]
    summary = {}
    for entry in payload['files']:
        slot = summary.setdefault(f"{entry['form']}/{entry['tier']}/{entry['level']}",
                                  {'files': 0, 'bytes': 0, 'features': 0, 'largest': 0})
        slot['files'] += 1
        slot['bytes'] += entry['bytes']
        slot['features'] += entry.get('features', 0) or 0
        slot['largest'] = max(slot['largest'], entry['bytes'])
    payload['summary'] = dict(sorted(summary.items()))
    payload['totals'] = {'files': len(payload['files']),
                         'bytes': sum(entry['bytes'] for entry in payload['files'])}
    payload.setdefault('build_notes', {}).update(notes)
    write_json(index_path, payload, indent=1)


# --------------------------------------------------------------- assignment

#: The only fields a snapshot row contributes. json.load keeps every dict it builds,
#: and a 320 MB snapshot (CULAC) held whole is over a gigabyte of Python objects;
#: trimming each dict as it is built keeps the parse to the text plus these fields.
SNAPSHOT_KEEP = frozenset({'committee_id', 'cycle', 'receipts', 'disbursements', 'sub_id',
                           'transaction_id', 'contributor_zip', 'contributor_city',
                           'contributor_state', 'recipient_zip', 'recipient_city',
                           'recipient_state'})


def _keep_snapshot_fields(obj):
    return {key: value for key, value in obj.items() if key in SNAPSHOT_KEEP}


def source_path(args):
    return Path(args.snapshot) / 'snapshots.json' if args.snapshot else Path(args.points)


def source_rows(args):
    """(rows [{id, latitude, longitude, tract, zip, city, state}], source path)."""
    if args.snapshot:
        path = source_path(args)
        with open(path, encoding='utf-8') as handle:
            units = json.load(handle, object_hook=_keep_snapshot_fields)
        rows = []
        for unit in units:
            for schedule, prefix in (('receipts', 'contributor'), ('disbursements', 'recipient')):
                for row in unit.get(schedule) or []:
                    identifier = str(row.get('sub_id') or row.get('transaction_id') or '')
                    rows.append({'id': f'{schedule[0]}:{unit.get("committee_id")}:'
                                       f'{unit.get("cycle")}:{identifier}',
                                 'latitude': None, 'longitude': None, 'tract': None,
                                 'zip': row.get(f'{prefix}_zip'),
                                 'city': row.get(f'{prefix}_city'),
                                 'state': row.get(f'{prefix}_state')})
        return rows, path
    path = Path(args.points)
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.json':
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get('rows', [])
    else:
        rows = list(csv.DictReader(io.StringIO(text)))
    for number, row in enumerate(rows):
        row.setdefault('id', str(number))
    return rows, path


def tables_stamp(v2):
    index = json.loads((Path(v2) / 'index.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256()
    for entry in index.get('files', []):
        if entry.get('form') == 'lookup':
            digest.update(f"{entry['path']}:{entry['sha256']};".encode())
    return digest.hexdigest()


def geopy_leftovers(leftovers, cache, log=log_print):
    """({(city, state): [lat, lon, address] or None}, status) - only when GeoPy exists."""
    try:
        from geopy.geocoders import Nominatim                        # noqa: F401
        from geopy.extra.rate_limiter import RateLimiter
    except ImportError:
        return {}, 'GeoPy is not installed in this Python; the step was skipped (nothing installed)'
    cache_path = Path(cache) / GEOPY_CACHE
    known = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.is_file() else {}
    geocoder = Nominatim(user_agent=USER_AGENT, timeout=15)
    geocode = RateLimiter(geocoder.geocode, min_delay_seconds=1.0, max_retries=2)
    answers, asked = {}, 0
    for city, state in sorted(leftovers):
        key = f'{city}|{state}'
        if key not in known:
            location = geocode({'city': city, 'state': state, 'country': 'United States'},
                               exactly_one=True, country_codes='us')
            known[key] = ([location.latitude, location.longitude, location.address]
                          if location else None)
            asked += 1
            if asked % 25 == 0:
                layers.write_bytes_atomic(cache_path, json.dumps(known).encode('utf-8'))
        answers[(city, state)] = known[key]
    layers.write_bytes_atomic(cache_path, json.dumps(known).encode('utf-8'))
    return answers, f'GeoPy Nominatim: {asked} asked, {len(leftovers) - asked} from the cache'


def assign(args, log=log_print):
    meter = layers.PeakMeter()
    path = source_path(args)
    source_sha = layers.sha256_file(path)
    stamp = tables_stamp(args.v2)
    out_dir = Path(args.assigned)
    target = out_dir / f'{source_sha[:16]}-{stamp[:8]}.json'
    if target.is_file() and not args.force:                 # checked before any parse
        payload = json.loads(target.read_text(encoding='utf-8'))
        log(f'  cache hit: {target.name} ({payload["summary"]["line"]})')
        return payload, target, True
    meter.start('read source rows')
    rows, source_path_read = source_rows(args)
    meter.end(log)
    if layers.sha256_file(source_path_read) != source_sha:
        raise SystemExit(f'{source_path_read} changed while it was read; run again')
    os.environ['MENTAT_GEO_V2'] = str(Path(args.v2))
    geo_lookup.reset_caches()
    meter.start('resolve precision')
    resolved, leftovers = [], {}
    for row in rows:
        answer = geo_lookup.locate(latitude=row.get('latitude'), longitude=row.get('longitude'),
                                   tract=row.get('tract'), zip=row.get('zip'), city=row.get('city'),
                                   state=row.get('state'))
        city = str(row.get('city') or '').strip()
        state_row = geo_lookup.for_state(row.get('state'))
        if answer['precision'] in ('state', 'none') and city and state_row:
            leftovers.setdefault((city.upper(), state_row['usps']), []).append(len(resolved))
        resolved.append(answer)
    geopy_answers, geopy_status = geopy_leftovers(set(leftovers), args.cache, log)
    geopy_matched = 0
    for key, positions in leftovers.items():
        found = geopy_answers.get(key)
        if found:
            geopy_matched += 1
            for position in positions:
                answer = resolved[position]
                answer.update(precision='city', located=True, point=[found[0], found[1]],
                              basis=f'GeoPy Nominatim: {found[2]}')
    meter.end(log)
    meter.start('sjoin located points, state by state')
    located = [(i, a) for i, a in enumerate(resolved) if a['point'] is not None]
    points = gpd.GeoDataFrame({'row': [i for i, _a in located]},
                              geometry=shapely.points([a['point'][1] for _i, a in located],
                                                      [a['point'][0] for _i, a in located]), crs=4269)
    context = Context(args.cache, args.v2, log=lambda *_: None)
    states = join_within(points, context.outlines.rename(columns={'statefp': 'gid'}), 'gid').tolist()
    points['statefp'] = states
    split_dir = layers.split_zcta(context.sources, Path(args.cache) / 'v2work',
                                  layers.zcta_homes(args.cache, context.outlines,
                                                    log=lambda *_: None)[0], log=lambda *_: None)
    joined = {}
    for statefp, group in points.groupby('statefp', dropna=True):
        state_layers = context.state_layers(statefp, split_dir)
        tract_payload = json.loads((Path(args.v2) / 'lookup' / 'tract' / f'{statefp}.json')
                                   .read_text(encoding='utf-8'))
        assigned = assign_state_points(group, state_layers, tract_payload['rows'], statefp=statefp)
        for position, row_index in enumerate(group['row'].tolist()):
            joined[row_index] = {level: assigned[level][position] for level in assigned}
            joined[row_index]['state'] = statefp
        del state_layers
    meter.end(log)
    out_rows, mismatches = {}, []
    for index, (row, answer) in enumerate(zip(rows, resolved)):
        sj = joined.get(index)
        if sj is not None:
            values = [answer['precision'], r5(answer['point'][0]), r5(answer['point'][1]),
                      sj['state'], sj['county'], sj['tract'], sj['zcta'], sj['place'], sj['cd116'],
                      sj['cd118'], sj['cd119'], sj['sldu'], sj['sldl'], sj['h10'], sj['h25'], sj['h50'],
                      answer['basis']]
            if answer['precision'] == 'zip':
                expect = answer['geoid']
                for level in ('county', 'tract', 'cd118', 'cd119', 'sldu', 'sldl'):
                    if expect.get(level) and expect[level] != sj[level]:
                        mismatches.append([row['id'], level, expect[level], sj[level]])
        else:
            point = answer['point'] or [None, None]
            values = [answer['precision'], point[0] and r5(point[0]), point[1] and r5(point[1]),
                      None if answer['point'] else answer['geoid'].get('state')] + \
                [None] * 12 + [answer['basis'] if not answer['point'] else
                               answer['basis'] + '; the point lies in no state or territory']
        out_rows[str(row['id'])] = values
    counts = geo_lookup.precision_counts(resolved)
    summary = {'rows': len(rows), 'by_precision': counts,
               'line': geo_lookup.located_line(resolved, 'point'),
               'city_leftovers': {'names': len(leftovers),
                                  'rows': sum(len(v) for v in leftovers.values()),
                                  'geopy': geopy_status, 'geopy_matched': geopy_matched,
                                  'examples': sorted(f'{c}, {s}' for c, s in leftovers)[:40]},
               'zip_rows_disagreeing_with_lookup': len(mismatches),
               'disagreement_examples': mismatches[:20]}
    memory = meter.close()
    payload = {'version': 2, 'tool': TOOL, 'tool_version': VERSION,
               'built_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
               'source': {'path': str(path), 'sha256': source_sha,
                          'kind': 'snapshot' if args.snapshot else 'points'},
               'tables_stamp': stamp, 'fields': ASSIGN_FIELDS, 'summary': summary,
               'memory': memory, 'rows': out_rows}
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(target, payload)
    log(f'  assigned {len(rows):,} rows -> {target} ({summary["line"]}; by precision {counts}; '
        f'{summary["city_leftovers"]["names"]} city names unmatched, '
        f'{summary["city_leftovers"]["rows"]} rows; {geopy_status}; zip rows disagreeing with the '
        f'lookup: {len(mismatches)}; peak {memory["process_peak_wset_mb"]} MB)')
    return payload, target, False


def write_pins(args, log=log_print):
    """Only lookup/hex_pins.json (and its index entry)."""
    path = Path(args.v2) / 'lookup' / 'hex_pins.json'
    pins = hex_pins()
    size, digest = write_json(path, pins)
    rel = path.relative_to(Path(args.v2)).as_posix()
    merge_index(Path(args.v2), {rel: {
        'level': 'hex_pins', 'form': 'lookup', 'tier': 'lookup', 'state': None, 'statefp': None,
        'features': len(pins['points']), 'bytes': size, 'sha256': digest, 'path': rel,
        'source': {'file': 'computed (pyproj EPSG:4269 -> EPSG:5070)', 'sha256': None}}}, {})
    log(f'  wrote {rel}: {len(pins["points"])} points, {size:,} bytes')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    tables = sub.add_parser('tables')
    tables.add_argument('--states', default='')
    pins = sub.add_parser('pins')
    points = sub.add_parser('assign')
    group = points.add_mutually_exclusive_group(required=True)
    group.add_argument('--snapshot', default='')
    group.add_argument('--points', default='')
    points.add_argument('--force', action='store_true')
    points.add_argument('--assigned', type=Path, default=DEFAULT_ASSIGNED)
    for command in (tables, points, pins):
        command.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    for command in (tables, pins):
        command.add_argument('--v2', type=Path, default=STAGING_V2)
    points.add_argument('--v2', type=Path, default=(INSTALLED_V2 if (INSTALLED_V2 / 'index.json')
                                                    .is_file() else STAGING_V2))
    args = parser.parse_args(argv)
    if args.command in ('tables', 'pins') and Path(args.v2).resolve() == INSTALLED_V2.resolve():
        raise SystemExit('refusing to write into mentat/app/geo/v2: write staging, then '
                         'build_geo_layers.py --install')
    started = time.time()
    if args.command == 'tables':
        build_tables(args)
    elif args.command == 'pins':
        write_pins(args)
    else:
        assign(args)
    log_print(f'done in {time.time() - started:.1f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
