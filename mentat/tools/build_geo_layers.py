"""Build Mentat's v2 map geography from the cached Census files, with GeoPandas.

Offline and dev-time only (lane G, 2026-09-25). The console never runs this and
never imports anything it imports: GeoPandas, Shapely, pyproj and pyogrio live
in the SYSTEM Python (C:\\Users\\pgoff\\AppData\\Local\\Programs\\Python\\Python312),
because the console's own Python is managed by the Codex app and was reset on
the 2026-09-25 reboot. The console reads the files this tool writes, and the
lookup tables mentat/tools/geo_assign.py writes, and nothing else.

    python mentat/tools/build_geo_layers.py                     # everything, into staging
    python mentat/tools/build_geo_layers.py --states NY,NJ      # some states (index merges)
    python mentat/tools/build_geo_layers.py --levels tract,zcta --forms albers
    python mentat/tools/build_geo_layers.py --wgs84-states all  # street tiers for every state
    python mentat/tools/geo_assign.py tables                    # the lookup tables, into staging
    python mentat/tools/build_geo_layers.py --install [--replace]   # one rename into the app

Everything is built into mentat/.cache/geo_build/v2 (ignored by git) and reaches
mentat/app/geo/v2 only through --install, one directory rename of a complete,
verified tree: evidence_geo signs every *.json under mentat/app/geo on each request,
so a build writing there makes the PAC map answer 400 while it runs.

It reads only mentat/.cache/geo (the Census cartographic files and gazetteers
downloaded 2026-09-17, -19 and -25) and never reaches the network.

THE FRAME IS TODAY'S FRAME, NOT A NEW ONE

The Albers form is pre-projected into exactly the 975 x 610 canvas that
build_district_geo.py owns. Nothing here re-derives it: REGIONS, the Albers
constants (n, C, rho0, meridian) and the placement fit are IMPORTED from
build_district_geo, and the placements are recomputed through its own
build_states from the same cb_2024_us_state_20m.zip; the run stops if they
differ from the projection block cd119.json ships. Only the arithmetic is
vectorised (numpy over whole coordinate arrays instead of one point at a time);
mentat/tests/test_geo_v2_alignment.py pins it to build_district_geo.Albers and
the output paths to the shipped ones.

Hawaii is clipped, the way d3's geoAlbersUsa clips each inset to its
clipExtent (lane L3, 2026-09-26): after projection every Hawaiian geometry is
cut to build_district_geo.REGIONS['hawaii']['clip'] (Frame.clip), the canvas
around the Hawaii box one pixel clear of the Alaska, lower-48 and American
Samoa boxes, as build_district_geo.project_rings clips the rings of cd*.json
and sld/*. The Northwestern Hawaiian Islands, which the 500k files carry and
the 20m fit never held, drew west of the Hawaii box - the nearer ones inside
the Alaska box, French Frigate Shoals on the Kenai Peninsula, the farther ones
off the canvas - and are now removed and counted: clipped_rings in a file's
header and index entry; a feature left with nothing is listed under clipped
and not written. The main islands do not move. Rings that still project
entirely off the canvas are dropped and counted (offcanvas_rings), as
build_state_legislative_geo.drop_offcanvas does; after the clip there are none.

The Alaska conic wraps longitude into [-180, 180] degrees, as d3's geoAlbersUsa
does (lane L2, 2026-09-26). Until then it did not: the 102 Alaskan vertices east
of 180 degrees in the 20m state file (the Near Islands, Attu) drew at the top
left of the inset, and the Alaska fit that included them drew the whole inset at
scale 254.24 instead of the 0.35 x lower-48 cap (376.54, d3's factor). Every
shipped file was rebuilt with the wrap; only Alaska moved.

FORMS, TIERS AND LEVELS (index.json repeats every number)

  albers/national/      1 decimal. state.json and cd116/118/119.json are the
                        shipped files re-made through GeoPandas with today's own
                        simplifier (build_district_geo.simplify, 0.5 px) and
                        today's keys; county.json nationally and <ST>/sldu, sldl,
                        place, zcta, tract per state, coverage-simplified 0.2 px.
  albers/detail/<ST>/   2 decimals, coverage-simplified 0.05 px (about 300 m in the
                        lower 48, 2 screen px at the SVG engine's 40x ceiling):
                        state (500k counties dissolved), county, sldu, sldl,
                        place, zcta, tract.
  albers/hex/           hexagon paths, 2 decimals: hex25.json and hex50.json
                        nationally, <ST>/hex10.json per state.
  wgs84/z0/             GeoJSON, national, for MapLibre zooms 0-4: state and
                        cd116/118/119 (20m) and county (500k), simplified 0.01
                        degree, 3 decimals.
  wgs84/z5/<ST>/        zooms 5-9: state, county, sldu, sldl, place, zcta, tract
                        simplified 0.002 degree, and cd116/118/119 (20m, as
                        published), 4 decimals.
  wgs84/z10/<ST>/       zooms 10 and up: state, county, sldu, sldl, place, zcta,
                        tract simplified 0.0003 degree (about 30 m, under the
                        1:500,000 source's own generalisation), 4 decimals, with
                        state borders and coasts left at source detail so
                        neighbouring states' files meet exactly.
  wgs84/hex/            hex25, hex50 nationally; <ST>/hex10 per state.

  The per-state WGS84 tiers (z5, z10, hex10) are built for the states the
  offline street tiles cover (--wgs84-states extract, the default: every state
  whose 500k outline meets the bounds in a .pmtiles header under
  mentat/.cache/basemap/tiles; every state when there is none). Outside the
  extract the street renderer shows no streets and offers Boundaries, so the
  files would never be drawn; --wgs84-states all builds them everywhere
  (about 500 MB). index.json records the scope and the extract bounds.

  Simplification is topological (shapely.coverage_simplify, Visvalingam-Whyatt,
  each shared edge simplified once), so neighbouring polygons never gap or
  overlap. A layer whose coverage is not valid falls back to per-feature
  simplify and index.json says so for that file.

  Tract and ZCTA features carry no name (the id is the name) and, like places,
  no per-feature bbox; a reader derives both. Every feature has an anchor, a
  point inside it; a feature too small to draw at its tier keeps its anchor
  with "path": null and is counted under "collapsed".

  Every file is at most PART_BYTES; a bigger one is split into parts cut in
  Hilbert-curve order, so each part is one compact area with its own bbox in
  the index. Nothing national at tract or ZCTA level is ever one file.

HEXAGONS

Built in EPSG:5070 (NAD83 / Conus Albers, equal area), pointy-top, size = the
distance between the centres of two neighbouring hexagons (their flat-to-flat
width): side = size / sqrt(3), area = (sqrt(3)/2) size^2 (25 mi -> 541.3 sq mi).
The id of the hexagon holding EPSG:5070 point (x, y) is "h<size>:<q>:<r>":
    s = size * 1609.344 / sqrt(3)
    qf = (sqrt(3)/3 * x - y/3) / s ;  rf = (2/3 * y) / s ;  cube-round (q, r)
with rounding floor(v + 0.5) (never banker's rounding) and the standard
cube-coordinate correction. mentat/app/geo_lookup.py computes the same id with
plain math for any latitude/longitude; the tests pin the two against pyproj.
Every hexagon that touches a 500k state outline is kept once, and belongs to
the state holding its centre (else the state it overlaps most).

WHAT THE SOURCES ARE (the sha256 of each is written into index.json)

  cb_2024_us_state_20m      the frame fit, national state paths, z0 states
  cb_2021_us_cd116_20m, cb_2023_us_cd118_20m, cb_2024_us_cd119_20m
                            districts at 1:20,000,000; no 500k district file is
                            cached, so districts stop at the z5 tier (when
                            cb_*_cd1xx_500k.zip is cached, z5 uses it)
  cb_2024_us_county_500k    counties, and the 500k state outlines (dissolved)
  cb_2024_us_sldu_500k, cb_2024_us_sldl_500k   state legislative districts
  cb_2024_us_place_500k     places;  cb_2024_us_tract_500k  tracts
  cb_2020_us_zcta520_500k   ZCTAs (no state field: each belongs to the state
                            holding its gazetteer internal point)
  2024_Gaz_zcta_national    ZCTA internal points

MEMORY: tract, place and ZCTA are read and written one state at a time (ZCTA
through a one-pass split into per-state GeoParquet under .cache/geo/v2work);
the peak working set of every step is measured and written into index.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import psutil
import pyogrio
import pyproj
import shapely

TOOLS = Path(__file__).resolve().parent
# MENTAT_DIR lets a copy of this file outside the repo run against it (development only).
MENTAT = Path(os.environ.get('MENTAT_DIR') or TOOLS.parent)
sys.path.insert(0, str(MENTAT / 'tools'))
import build_district_geo as builder                                # noqa: E402
import build_zip_centroids as zip_points                            # noqa: E402

# Builds go to a STAGING folder outside the app tree and reach mentat/app/geo/v2 only
# through --install, one directory rename after the tree is complete and verified.
# evidence_geo.cached_geography signs EVERY *.json under mentat/app/geo on each
# request and answers 400 when that signature moves mid-request, so a build writing
# there breaks the PAC map for as long as it runs (lane C, 2026-09-25).
DEFAULT_OUT = MENTAT / '.cache' / 'geo_build' / 'v2'
INSTALLED = MENTAT / 'app' / 'geo' / 'v2'
DEFAULT_CACHE = MENTAT / '.cache' / 'geo'
DEFAULT_TILES = MENTAT / '.cache' / 'basemap' / 'tiles'
TODAY = MENTAT / 'app' / 'geo'
TOOL = 'mentat/tools/build_geo_layers.py'
VERSION = '2026-09-26.2'

WIDTH, HEIGHT = builder.WIDTH, builder.HEIGHT
PART_BYTES = 1_900_000

SOURCES = {
    'state20m': 'cb_2024_us_state_20m.zip',
    'cd116': 'cb_2021_us_cd116_20m.zip',
    'cd118': 'cb_2023_us_cd118_20m.zip',
    'cd119': 'cb_2024_us_cd119_20m.zip',
    'county': 'cb_2024_us_county_500k.zip',
    'sldu': 'cb_2024_us_sldu_500k.zip',
    'sldl': 'cb_2024_us_sldl_500k.zip',
    'place': 'cb_2024_us_place_500k.zip',
    'tract': 'cb_2024_us_tract_500k.zip',
    'zcta': 'cb_2020_us_zcta520_500k.zip',
    'gaz_zcta': '2024_Gaz_zcta_national.zip',
}
# A finer district file is used for the z5 tier when it is cached; none is today.
CD_500K = {'cd116': 'cb_2021_us_cd116_500k.zip', 'cd118': 'cb_2023_us_cd118_500k.zip',
           'cd119': 'cb_2024_us_cd119_500k.zip'}
CONGRESS = {'cd116': '116th (= 117th map)', 'cd118': '118th', 'cd119': '119th'}
VINTAGES = ('cd116', 'cd118', 'cd119')

LEAN_LEVELS = frozenset({'tract', 'zcta'})                  # no name: the id is the name
BBOX_LEVELS = frozenset({'state', 'county', 'sldu', 'sldl'})

TIERS = {
    'albers': {
        'national': dict(decimals=1, tolerance=0.2, unit='px', simplify_boundary=True,
                         use='national and state SVG views, 1x-8x'),
        'detail': dict(decimals=2, tolerance=0.05, unit='px', simplify_boundary=False,
                       use='zoomed SVG views up to the 40x ceiling'),
    },
    'wgs84': {
        'z0': dict(decimals=3, tolerance=0.01, unit='degree', simplify_boundary=True,
                   minzoom=0, maxzoom=5, use='MapLibre zooms 0-4, national files'),
        'z5': dict(decimals=4, tolerance=0.002, unit='degree', simplify_boundary=True,
                   minzoom=5, maxzoom=10, use='MapLibre zooms 5-9, per state'),
        'z10': dict(decimals=4, tolerance=0.0003, unit='degree', simplify_boundary=False,
                    minzoom=10, maxzoom=24, use='MapLibre zooms 10 and up, per state'),
    },
}
COMPAT = dict(decimals=builder.DECIMALS, min_step_px=builder.MIN_STEP,
              simplifier='build_district_geo.simplify (radial, the shipped algorithm)')

HEX_SIZES = (10, 25, 50)
HEX_NATIONAL = (25, 50)          # hex10 is written per state
MILE = 1609.344
SQRT3 = math.sqrt(3.0)
HEX_DECIMALS = {'albers': 2, 'wgs84': 4}


# ------------------------------------------------------------------ memory

class PeakMeter:
    """Peak working set per build step, sampled every 20 ms on a thread.

    Windows reports the process's own lifetime peak (peak_wset) exactly; the
    per-step figure is the sampled maximum between a step's start and end.
    """

    def __init__(self):
        self.process = psutil.Process()
        self.steps = []
        self._current = None
        self._peak = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(0.02):
            rss = self.process.memory_info().rss
            with self._lock:
                if rss > self._peak:
                    self._peak = rss

    def start(self, name):
        with self._lock:
            self._current = (name, time.time())
            self._peak = self.process.memory_info().rss

    def end(self, log=print):
        rss = self.process.memory_info().rss
        with self._lock:
            name, started = self._current
            peak = max(self._peak, rss)
        step = {'step': name, 'peak_mb': round(peak / 2 ** 20, 1),
                'seconds': round(time.time() - started, 2)}
        self.steps.append(step)
        log(f'    [{name}] peak {step["peak_mb"]} MB, {step["seconds"]} s')
        return step

    def close(self):
        self._stop.set()
        info = self.process.memory_info()
        peak = getattr(info, 'peak_wset', 0) or max((s['peak_mb'] for s in self.steps), default=0) * 2 ** 20
        return {'process_peak_wset_mb': round(peak / 2 ** 20, 1),
                'max_step_mb': max((s['peak_mb'] for s in self.steps), default=0),
                'steps': self.steps}


# ------------------------------------------------------------------- utils

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def write_bytes_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with open(temporary, 'wb') as handle:
        handle.write(payload)
    os.replace(temporary, path)
    return len(payload), hashlib.sha256(payload).hexdigest()


def fmt(value):
    """Shortest text for an already-rounded coordinate ('-0' becomes '0')."""
    text = repr(float(value))
    if text.endswith('.0'):
        text = text[:-2]
    return '0' if text == '-0' else text


def round_ring(coords, decimals):
    """Rounded ring with consecutive duplicates removed and closure kept, or None."""
    rounded = np.round(coords, decimals) + 0.0      # + 0.0 turns -0.0 into 0.0
    if len(rounded) < 2:
        return None
    keep = np.ones(len(rounded), dtype=bool)
    keep[1:] = np.any(rounded[1:] != rounded[:-1], axis=1)
    ring = rounded[keep]
    if len(ring) and np.all(ring[0] == ring[-1]):
        ring = ring[:-1]
    if len(ring) < 3:
        return None
    return np.vstack([ring, ring[:1]])


def polygon_parts(geom):
    for polygon in shapely.get_parts(geom):
        if polygon is None or shapely.is_empty(polygon):
            continue
        if shapely.get_type_id(polygon) != 3:          # skip stray lines/points from repairs
            continue
        exterior = shapely.get_coordinates(shapely.get_exterior_ring(polygon))
        holes = [shapely.get_coordinates(shapely.get_interior_ring(polygon, index))
                 for index in range(shapely.get_num_interior_rings(polygon))]
        yield exterior, holes


def on_canvas(ring):
    return bool(np.any((ring[:, 0] >= 0) & (ring[:, 0] <= WIDTH)
                       & (ring[:, 1] >= 0) & (ring[:, 1] <= HEIGHT)))


def svg_path(geom, decimals):
    """(path or None, rings dropped off canvas, bounds of what was written).

    "M x y L x y x y ... Z" per ring, rings joined by a space, each ring's first
    point not repeated before its Z - build_district_geo.to_path's grammar, so
    any reader of today's files reads these. A polygon whose exterior lies wholly
    off the canvas is dropped with its holes and counted.
    """
    pieces, dropped, kept = [], 0, []
    for exterior, holes in polygon_parts(geom):
        if not on_canvas(exterior):
            dropped += 1 + len(holes)
            continue
        for coords in [exterior] + holes:
            ring = round_ring(coords, decimals)
            if ring is None:
                continue
            body = ' '.join(f'{fmt(x)} {fmt(y)}' for x, y in ring[1:-1])
            pieces.append(f'M{fmt(ring[0][0])} {fmt(ring[0][1])} L{body} Z')
            kept.append(ring)
    if not pieces:
        return None, dropped, None
    stacked = np.vstack(kept)
    bounds = [float(stacked[:, 0].min()), float(stacked[:, 1].min()),
              float(stacked[:, 0].max()), float(stacked[:, 1].max())]
    return ' '.join(pieces), dropped, bounds


def geojson_geometry(geom, decimals):
    """RFC 7946 geometry text (exterior counter-clockwise), or None when empty."""
    oriented = shapely.orient_polygons(geom, exterior_cw=False)
    polygons = []
    for exterior, holes in polygon_parts(oriented):
        outer = round_ring(exterior, decimals)
        if outer is None:
            continue
        rings = [outer] + [ring for ring in (round_ring(hole, decimals) for hole in holes)
                           if ring is not None]
        polygons.append('[' + ','.join(
            '[' + ','.join(f'[{fmt(x)},{fmt(y)}]' for x, y in ring) + ']' for ring in rings) + ']')
    if not polygons:
        return None
    if len(polygons) == 1:
        return '{"type":"Polygon","coordinates":' + polygons[0] + '}'
    return '{"type":"MultiPolygon","coordinates":[' + ','.join(polygons) + ']}'


def rounded_box(bounds, decimals):
    return [round(float(value), decimals) for value in bounds]


def union_bounds(bounds_list, decimals):
    if not bounds_list:
        return None
    array = np.array(bounds_list, dtype=float)
    return [round(float(array[:, 0].min()), decimals), round(float(array[:, 1].min()), decimals),
            round(float(array[:, 2].max()), decimals), round(float(array[:, 3].max()), decimals)]


def compact(payload):
    return json.dumps(payload, separators=(',', ':'), ensure_ascii=False)


# ------------------------------------------------------------------- frame

class Frame:
    """Today's 975 x 610 canvas: build_district_geo's regions, conics and fit.

    project() is build_district_geo.Albers.__call__ plus the placement, over a
    whole coordinate array at once, with the SAME constants (the builder's own
    projector objects are read, not re-derived) and the same theta, its longitude
    difference wrapped into [-pi, pi] exactly as Albers.__call__ wraps it.
    """

    def __init__(self, cache, log=print):
        state_zip = Path(cache) / SOURCES['state20m']
        bundle = builder.build_states(state_zip, builder.STATE_SOURCE, log=lambda *_: None)
        self.codes = bundle[1]                     # STATEFP -> STUSPS, territories included
        self.names = bundle[2]                     # STUSPS -> name
        self.placements = bundle[3]
        self.undrawn = bundle[6]
        self.fips = {usps: fips for fips, usps in self.codes.items()}
        self.params = {}
        for name, (scale, tx, ty) in self.placements.items():
            albers = builder.projector(name)
            self.params[name] = (albers.n, albers.c, albers.rho0, albers.meridian, scale, tx, ty)
        self.check_against_shipped(log)

    def check_against_shipped(self, log):
        shipped = TODAY / 'cd119.json'
        if not shipped.is_file():
            log('  frame: no shipped cd119.json to compare with; continuing')
            return
        regions = json.loads(shipped.read_text(encoding='utf-8'))['projection']['regions']
        for name, region in regions.items():
            scale, tx, ty = self.placements[name]
            if (round(scale, 6), round(tx, 6), round(ty, 6)) != (
                    region['scale'], region['translate'][0], region['translate'][1]):
                raise SystemExit(f'frame drift: placement {name} differs from cd119.json')
        log(f'  frame: placements {sorted(self.placements)} match cd119.json to 6 decimals')

    @staticmethod
    def region_of(statefp):
        return builder.region_of(statefp)

    def project(self, lonlat, region):
        n, c, rho0, meridian, scale, tx, ty = self.params[region]
        lonlat = np.asarray(lonlat, dtype=float).reshape(-1, 2)
        longitude = np.radians(lonlat[:, 0])
        latitude = np.radians(lonlat[:, 1])
        rho = np.sqrt(np.maximum(c - 2 * n * np.sin(latitude), 0.0)) / n
        delta = longitude - meridian
        delta = np.where(delta > np.pi, delta - 2 * np.pi,
                         np.where(delta < -np.pi, delta + 2 * np.pi, delta))
        theta = n * delta
        x = rho * np.sin(theta)
        y = rho0 - rho * np.cos(theta)
        return np.column_stack((tx + scale * x, ty - scale * y))

    def project_geoms(self, geoms, statefps):
        """Projected geometries (None where the region has no placement), and regions."""
        geoms = np.asarray(geoms, dtype=object)
        out = np.empty(len(geoms), dtype=object)
        regions = np.array([self.region_of(fp) for fp in statefps], dtype=object)
        for region in set(regions.tolist()):
            mask = regions == region
            if region not in self.placements:
                out[mask] = None
                continue
            out[mask] = shapely.transform(geoms[mask], lambda xy, r=region: self.project(xy, r))
        return out, regions

    @staticmethod
    def clip(geom, region):
        """(geometry or None, rings removed): a projected geometry cut to its region's clip.

        build_district_geo.REGIONS[region]['clip'], canvas pixels, as d3's clipExtent. Only
        Hawaii has one; every other geometry comes back as the same object. A polygon wholly
        inside the extent is kept as it is (same coordinates, same order); a polygon whose
        bounds miss it is removed and its rings counted; a polygon crossing an edge is cut to
        it (shapely.clip_by_rect). None when nothing is left.
        """
        box = builder.REGIONS.get(region, {}).get('clip')
        if box is None or geom is None:
            return geom, 0
        x0, y0, x1, y1 = box
        kept, removed, changed = [], 0, False
        for part in shapely.get_parts(geom):
            if shapely.get_type_id(part) != 3:
                continue
            outline = shapely.get_coordinates(shapely.get_exterior_ring(part))
            if bool(np.all((outline[:, 0] >= x0) & (outline[:, 0] <= x1)
                           & (outline[:, 1] >= y0) & (outline[:, 1] <= y1))):
                kept.append(part)
                continue
            changed = True
            left, low, right, high = shapely.bounds(part).tolist()
            if right < x0 or left > x1 or high < y0 or low > y1:
                removed += 1 + int(shapely.get_num_interior_rings(part))
                continue
            pieces = [piece for piece in shapely.get_parts(shapely.clip_by_rect(part, x0, y0, x1, y1))
                      if shapely.get_type_id(piece) == 3 and not shapely.is_empty(piece)]
            if pieces:
                kept.extend(pieces)
            else:
                removed += 1 + int(shapely.get_num_interior_rings(part))
        if not changed:
            return geom, 0
        if not kept:
            return None, removed
        if len(kept) == 1 and shapely.get_type_id(geom) == 3:
            return kept[0], removed
        return shapely.multipolygons(kept), removed


# ----------------------------------------------------------------- reading

class Sources:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.hashed = {}

    def path(self, key, name=None):
        return self.cache / (name or SOURCES.get(key) or CD_500K[key])

    def info(self, key, name=None):
        path = self.path(key, name)
        if path.name not in self.hashed:
            self.hashed[path.name] = {'file': path.name, 'sha256': sha256_file(path),
                                      'bytes': path.stat().st_size}
        return self.hashed[path.name]

    def read(self, key, where=None, columns=None, name=None):
        path = self.path(key, name)
        if not path.is_file():
            raise SystemExit(f'{path} is missing; this tool never downloads. See the docstring.')
        frame = pyogrio.read_dataframe(path, where=where, columns=columns)
        if frame.crs is None or frame.crs.to_epsg() != 4269:
            raise SystemExit(f'{path.name}: expected NAD83 (EPSG:4269), found {frame.crs}')
        return frame


def has_seat(code):
    code = (code or '').strip().upper()
    return bool(code) and set(code) != {'Z'}


def simplify(geoms, tolerance, simplify_boundary):
    """(geometries, method): coverage simplification when the coverage is valid."""
    geoms = np.asarray(geoms, dtype=object)
    if not len(geoms) or tolerance <= 0:
        return geoms, 'none'
    try:
        if bool(shapely.coverage_is_valid(geoms)):
            return (shapely.coverage_simplify(geoms, tolerance, simplify_boundary=simplify_boundary),
                    'coverage')
    except Exception:                                   # noqa: BLE001 - fall back and say so
        pass
    return shapely.simplify(geoms, tolerance, preserve_topology=True), 'per-feature'


def simplify_regions(geoms, regions, tolerance, simplify_boundary):
    """simplify() over the whole collection; if it is not one valid coverage, per region.

    The regions are separate conics fitted into separate insets, so no edge is shared
    between two of them and each is a coverage of its own. One collection spanning
    regions can still fail as a single coverage where one inset's geometry is drawn over
    another's, as the Northwestern Hawaiian Islands, which the Hawaii fit never held, did
    inside the Alaska box at Alaska's corrected scale (2026-09-26: French Frigate Shoals on
    the Kenai Peninsula) until the Hawaii clip removed them (lane L3). Such an overlap must
    not send the whole nation to per-feature simplification.
    """
    result, method = simplify(geoms, tolerance, simplify_boundary)
    names = sorted(set(np.asarray(regions).tolist()))
    if method != 'per-feature' or len(names) < 2:
        return result, method
    regions = np.asarray(regions, dtype=object)
    result = np.empty(len(geoms), dtype=object)
    methods = {}
    for name in names:
        mask = regions == name
        result[mask], methods[name] = simplify(geoms[mask], tolerance, simplify_boundary)
    fallback = sorted(name for name, how in methods.items() if how != 'coverage')
    return result, 'coverage by region' + (' (per-feature: ' + ', '.join(fallback) + ')'
                                           if fallback else '')


def hilbert_order(points_lonlat):
    points = gpd.GeoSeries(np.asarray(points_lonlat, dtype=object), crs=4269)
    if len(points) < 2:
        return np.arange(len(points))
    return np.argsort(points.hilbert_distance().values, kind='stable')


# --------------------------------------------------------------- the writer

class Output:
    """Writes files, remembers their index entries, removes stale parts."""

    def __init__(self, root, log=print):
        self.root = Path(root)
        self.entries = {}
        self.cleared = set()
        self.build_notes = {}
        self.log = log

    def clear_group(self, folder, stem, suffix):
        """Remove a group's old files (stem.json, stem.1.json ...) before rewriting it."""
        directory = self.root / folder
        self.cleared.add(f'{folder}/{stem}')
        if not directory.is_dir():
            return
        for path in directory.glob(f'{stem}*{suffix}'):
            rest = path.name[len(stem):-len(suffix)]
            if rest == '' or (rest.startswith('.') and rest[1:].isdigit()):
                path.unlink()

    def write(self, relative, text, entry):
        path = self.root / relative
        size, digest = write_bytes_atomic(path, text.encode('utf-8'))
        entry = dict(entry, path=relative.replace('\\', '/'), bytes=size, sha256=digest)
        self.entries[entry['path']] = entry
        return entry

    def chunked(self, folder, stem, suffix, header, features, entry, kind):
        """Write features [(key, text, bounds lonlat, bounds px)] in parts of <= PART_BYTES.

        header is the JSON object text without its closing brace; every part gets
        "part"/"parts" and its own bbox, and one index entry.
        """
        self.clear_group(folder, stem, suffix)
        parts, current, size = [], [], 0
        overhead = len(header.encode('utf-8')) + 300
        for feature in features:
            length = len(feature[1].encode('utf-8')) + len(feature[0]) + 6
            if current and size + length + overhead > PART_BYTES:
                parts.append(current)
                current, size = [], 0
            current.append(feature)
            size += length
        if current:
            parts.append(current)
        written = []
        for number, part in enumerate(parts, start=1):
            name = f'{stem}{suffix}' if len(parts) == 1 else f'{stem}.{number}{suffix}'
            lonlat = union_bounds([f[2] for f in part if f[2] is not None], 5)
            if kind == 'albers':
                pixels = union_bounds([f[3] for f in part if f[3] is not None], 2)
                body = ','.join(f'{json.dumps(key)}:{text}' for key, text, _b, _p in part)
                text = (header + f',"part":{number},"parts":{len(parts)},'
                        f'"bbox_lonlat":{json.dumps(lonlat)},"bbox_px":{json.dumps(pixels)},'
                        f'"features":{{{body}}}}}')
            else:
                body = ','.join(text for _key, text, _b, _p in part)
                text = (header + f',"part":{number},"parts":{len(parts)},'
                        f'"bbox":{json.dumps(lonlat)},"features":[{body}]}}')
            meta = dict(entry, part=number, parts=len(parts), features=len(part),
                        bbox_lonlat=lonlat)
            if kind == 'albers':
                meta['bbox_px'] = pixels
            written.append(self.write(f'{folder}/{name}', text, meta))
        return written


# ------------------------------------------------------- one level, one scope

def albers_features(frame, level, keys, names, geoms_lonlat, statefps, anchors_lonlat, order,
                    tier, extra=None):
    """[(key, feature json, bounds lonlat, bounds px)] in Hilbert order, plus notes."""
    settings = TIERS['albers'][tier]
    decimals = settings['decimals']
    projected, regions = frame.project_geoms(geoms_lonlat, statefps)
    placed = np.array([geom is not None for geom in projected], dtype=bool)
    notes = {'unplaced': sorted(str(k) for k, ok in zip(keys, placed) if not ok),
             'collapsed': [], 'offcanvas_rings': 0, 'method': 'none',
             'clipped': [], 'clipped_rings': 0}
    # Hawaii's clip extent (Frame.clip): a feature left with nothing is listed, not written.
    for index in np.flatnonzero(placed).tolist():
        clipped, removed = frame.clip(projected[index], regions[index])
        notes['clipped_rings'] += removed
        if clipped is None:
            placed[index] = False
            notes['clipped'].append(str(keys[index]))
        projected[index] = clipped
    notes['clipped'].sort()
    if not placed.any():
        return [], notes
    simplified = np.empty(len(projected), dtype=object)
    result, notes['method'] = simplify_regions(projected[placed], regions[placed],
                                               settings['tolerance'], settings['simplify_boundary'])
    simplified[placed] = result
    features = []
    for index in order:
        if not placed[index]:
            continue
        key = str(keys[index])
        path, dropped, bounds_px = svg_path(simplified[index], decimals)
        notes['offcanvas_rings'] += dropped
        anchor = frame.project(anchors_lonlat[index], regions[index])[0]
        if not builder.in_clip(regions[index], anchor):         # it stood on a clipped-away island
            anchor = shapely.get_coordinates(shapely.point_on_surface(projected[index]))[0]
        record = {} if level in LEAN_LEVELS else {'name': names[index]}
        record['path'] = path
        record['anchor'] = [round(float(anchor[0]), decimals), round(float(anchor[1]), decimals)]
        if extra is not None:
            record.update(extra[index])
        if path is None:
            notes['collapsed'].append(key)
        elif level in BBOX_LEVELS:
            record['bbox'] = rounded_box(bounds_px, decimals)
        features.append((key, compact(record), shapely.bounds(geoms_lonlat[index]).tolist(),
                         bounds_px))
    return features, notes


def wgs84_features(level, keys, names, geoms_lonlat, order, tier, extra=None):
    settings = TIERS['wgs84'][tier]
    geoms = np.asarray(geoms_lonlat, dtype=object)
    simplified, method = simplify(geoms, settings['tolerance'], settings['simplify_boundary'])
    features, collapsed = [], []
    for index in order:
        key = str(keys[index])
        geometry = geojson_geometry(simplified[index], settings['decimals'])
        if geometry is None:
            collapsed.append(key)
            continue
        properties = {'id': key} if level in LEAN_LEVELS else {'id': key, 'name': names[index]}
        if extra is not None:
            properties.update(extra[index])
        text = ('{"type":"Feature","properties":' + compact(properties)
                + ',"geometry":' + geometry + '}')
        features.append((key, text, shapely.bounds(geoms[index]).tolist(), None))
    return features, {'method': method, 'collapsed': collapsed}


def write_level(output, frame, level, state, statefp, rows, source_info, tiers, log=print,
                extra_fn=None):
    """Write one level for one state (state None: national) in every requested tier.

    tiers: {'albers': [...], 'wgs84': [...]}.
    """
    national = state is None
    keys = rows['key'].tolist()
    names = rows['name'].tolist()
    geoms = rows.geometry.values
    statefps = rows['statefp'].tolist()
    extra = extra_fn(rows) if extra_fn else None
    anchors = shapely.point_on_surface(np.asarray(geoms, dtype=object))
    order = hilbert_order(anchors)
    anchor_xy = shapely.get_coordinates(anchors)
    written = []
    for form, tier_list in tiers.items():
        for tier in tier_list:
            settings = TIERS[form][tier]
            base = {'level': level, 'form': form, 'tier': tier, 'state': state,
                    'statefp': statefp, 'decimals': settings['decimals']}
            if form == 'albers':
                features, notes = albers_features(frame, level, keys, names, geoms, statefps,
                                                  anchor_xy, order, tier, extra)
                header = dict(base, width=WIDTH, height=HEIGHT, tolerance_px=settings['tolerance'],
                              simplified=notes['method'], collapsed=notes['collapsed'][:500],
                              collapsed_count=len(notes['collapsed']),
                              offcanvas_rings=notes['offcanvas_rings'],
                              unplaced=notes['unplaced'][:500], source=source_info['file'],
                              grammar='M x y L x y ... Z per ring; first point not repeated; '
                                      'close each ring before a point-in-polygon test')
                if notes['clipped_rings'] or notes['clipped']:
                    header.update(clipped_rings=notes['clipped_rings'], clipped=notes['clipped'][:500])
                folder = f'albers/{tier}' + ('' if national else f'/{state}')
                suffix = '.json'
            else:
                features, notes = wgs84_features(level, keys, names, geoms, order, tier, extra)
                header = dict({'type': 'FeatureCollection'}, **base,
                              tolerance_degree=settings['tolerance'], minzoom=settings['minzoom'],
                              maxzoom=settings['maxzoom'], simplified=notes['method'],
                              collapsed=notes['collapsed'][:500], source=source_info['file'],
                              promote_id='id')
                folder = f'wgs84/{tier}' + ('' if national else f'/{state}')
                suffix = '.geojson'
            entry = dict(base, source={'file': source_info['file'], 'sha256': source_info['sha256']},
                         simplified=notes['method'], tolerance=settings['tolerance'],
                         tolerance_unit=settings['unit'])
            for field in ('collapsed', 'unplaced', 'clipped'):
                if notes.get(field):
                    entry[field] = len(notes[field])
            for field in ('offcanvas_rings', 'clipped_rings'):
                if notes.get(field):
                    entry[field] = notes[field]
            if not features:
                output.clear_group(folder, level, suffix)
                continue
            written += output.chunked(folder, level, suffix, compact(header)[:-1], features,
                                      entry, form)
    return written


# ------------------------------------------------------------ the national

def compat_rings(frame, geom, region):
    """build_district_geo.project_rings on a GeoPandas geometry: project, clip, simplify, round."""
    box = builder.REGIONS[region].get('clip')
    rings = []
    for exterior, holes in polygon_parts(geom):
        for coords in [exterior] + holes:
            screen = [tuple(point) for point in frame.project(coords, region).tolist()]
            if box is not None:
                screen = builder.clip_ring(screen, box)
                if not screen:
                    continue
            kept = builder.simplify(screen)
            if len(kept) >= 3:
                rings.append(kept)
    return rings


def compat_national(output, frame, sources, log=print):
    """state and cd116/118/119 through today's own simplifier, keyed as today."""
    info = sources.info('state20m')
    states = sources.read('state20m')
    records = {}
    for row in states.itertuples():
        region = frame.region_of(row.STATEFP)
        if region not in frame.placements:
            continue
        rings = compat_rings(frame, row.geometry, region)
        if not rings:
            continue
        records[row.STUSPS] = {'name': row.NAME, 'statefp': row.STATEFP,
                               'path': builder.to_path(rings), 'bbox': builder.bbox_of(rings),
                               'region': region}
    entry = {'form': 'albers', 'tier': 'national', 'state': None, 'statefp': None, 'compat': True,
             'decimals': COMPAT['decimals'], 'tolerance': COMPAT['min_step_px'],
             'tolerance_unit': 'px', 'simplified': 'radial (build_district_geo.simplify)'}
    payload = {'level': 'state', 'form': 'albers', 'tier': 'national', 'width': WIDTH,
               'height': HEIGHT, 'compat': COMPAT, 'source': info['file'],
               'projection': builder.projection_payload(frame.placements),
               'undrawn': frame.undrawn, 'states': dict(sorted(records.items()))}
    output.write('albers/national/state.json', compact(payload),
                 dict(entry, level='state', features=len(records),
                      source={'file': info['file'], 'sha256': info['sha256']}))
    log(f'  albers/national/state.json: {len(records)} states')
    for vintage in VINTAGES:
        info = sources.info(vintage)
        districts = sources.read(vintage)
        field = next(name for name in districts.columns if name.startswith('CD') and name.endswith('FP'))
        records, skipped, labels = {}, [], {}
        for row in districts.itertuples():
            stusps = frame.codes.get(row.STATEFP)
            key = builder.district_key(stusps, getattr(row, field)) if stusps else None
            if key is None:
                skipped.append({'geoid': row.GEOID, 'reason': 'no seat (water/unassigned)'})
                continue
            region = frame.region_of(row.STATEFP)
            if region not in frame.placements:
                skipped.append({'geoid': row.GEOID, 'reason': f'no placement for {region}'})
                continue
            rings = compat_rings(frame, row.geometry, region)
            centroid, basis = builder.centroid_of(rings)
            labels[basis] = labels.get(basis, 0) + 1
            name = getattr(row, 'NAMELSAD', '') or builder.district_name({}, key)
            records[key] = {'geoid': row.GEOID, 'name': name, 'path': builder.to_path(rings),
                            'bbox': builder.bbox_of(rings), 'centroid': centroid, 'region': region}
        payload = {'level': vintage, 'form': 'albers', 'tier': 'national', 'width': WIDTH,
                   'height': HEIGHT, 'compat': COMPAT, 'congress': CONGRESS[vintage],
                   'source': info['file'], 'skipped': skipped, 'centroid_basis': labels,
                   'districts': dict(sorted(records.items()))}
        output.write(f'albers/national/{vintage}.json', compact(payload),
                     dict(entry, level=vintage, features=len(records),
                          source={'file': info['file'], 'sha256': info['sha256']}))
        log(f'  albers/national/{vintage}.json: {len(records)} districts, {len(skipped)} skipped')


# ------------------------------------------------------------------ hexagons

def hex_side(size_miles):
    return size_miles * MILE / SQRT3


def hex_axial(x, y, size_miles):
    """Vectorised EPSG:5070 metres -> axial (q, r) of the pointy-top hexagon holding them."""
    side = hex_side(size_miles)
    qf = (SQRT3 / 3.0 * x - y / 3.0) / side
    rf = (2.0 / 3.0 * y) / side
    sf = -qf - rf
    q = np.floor(qf + 0.5)
    r = np.floor(rf + 0.5)
    s = np.floor(sf + 0.5)
    dq, dr, ds = np.abs(q - qf), np.abs(r - rf), np.abs(s - sf)
    fix_q = (dq > dr) & (dq > ds)
    fix_r = ~fix_q & (dr > ds)
    q = np.where(fix_q, -r - s, q)
    r = np.where(fix_r, -q - s, r)
    return q.astype(np.int64), r.astype(np.int64)


def hex_center(q, r, size_miles):
    side = hex_side(size_miles)
    return side * SQRT3 * (q + r / 2.0), side * 1.5 * r


def hex_id(size_miles, q, r):
    return f'h{size_miles}:{int(q)}:{int(r)}'


def to_5070():
    return pyproj.Transformer.from_crs(4269, 5070, always_xy=True)


def from_5070():
    return pyproj.Transformer.from_crs(5070, 4269, always_xy=True)


def hex_ids_lonlat(lon, lat, size_miles):
    """The tool's answer: pyproj NAD83 -> EPSG:5070, then hex_axial. Vectorised."""
    x, y = to_5070().transform(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    q, r = hex_axial(np.asarray(x), np.asarray(y), size_miles)
    return [hex_id(size_miles, a, b) for a, b in zip(q.tolist(), r.tolist())]


def hex_polygons(q, r, size_miles):
    side = hex_side(size_miles)
    cx, cy = hex_center(q, r, size_miles)
    angles = np.radians(30.0 + 60.0 * np.arange(6))
    xs = cx[:, None] + side * np.cos(angles)[None, :]
    ys = cy[:, None] + side * np.sin(angles)[None, :]
    rings = np.stack([xs, ys], axis=2)
    rings = np.concatenate([rings, rings[:, :1, :]], axis=1)
    return shapely.polygons(rings)


def build_hex_grid(size_miles, outlines_5070, log=print):
    """GeoDataFrame of every hexagon touching a state outline, with its home state."""
    side = hex_side(size_miles)
    geoms = outlines_5070.geometry.values
    statefps = outlines_5070['statefp'].tolist()
    touched = {}
    for index, geom in enumerate(geoms):
        x0, y0, x1, y1 = shapely.bounds(geom)
        qs, rs = [], []
        for r in range(int(math.floor(y0 / (1.5 * side))) - 1, int(math.ceil(y1 / (1.5 * side))) + 2):
            q0 = int(math.floor(x0 / (side * SQRT3) - r / 2.0)) - 1
            q1 = int(math.ceil(x1 / (side * SQRT3) - r / 2.0)) + 1
            span = np.arange(q0, q1 + 1)
            qs.append(span)
            rs.append(np.full(len(span), r))
        q = np.concatenate(qs)
        r = np.concatenate(rs)
        shapely.prepare(geom)
        hit = shapely.intersects(geom, hex_polygons(q, r, size_miles))
        for key in zip(q[hit].tolist(), r[hit].tolist()):
            touched.setdefault(key, set()).add(statefps[index])
    keys = sorted(touched)
    q = np.array([k[0] for k in keys], dtype=np.int64)
    r = np.array([k[1] for k in keys], dtype=np.int64)
    polygons = hex_polygons(q, r, size_miles)
    cx, cy = hex_center(q, r, size_miles)
    grid = gpd.GeoDataFrame({'q': q, 'r': r, 'cx': cx, 'cy': cy,
                             'id': [hex_id(size_miles, a, b) for a, b in zip(q, r)]},
                            geometry=polygons, crs=5070)
    home = np.full(len(grid), None, dtype=object)
    tree = shapely.STRtree(geoms)
    inside = tree.query(shapely.points(cx, cy), predicate='within')
    for point_index, outline_index in zip(inside[0].tolist(), inside[1].tolist()):
        if home[point_index] is None:
            home[point_index] = statefps[outline_index]
    by_overlap = 0
    for index in np.flatnonzero(np.array([h is None for h in home])).tolist():
        best, best_area = None, -1.0
        for fp in sorted(touched[(int(q[index]), int(r[index]))]):
            area = shapely.area(shapely.intersection(polygons[index], geoms[statefps.index(fp)]))
            if area > best_area:
                best, best_area = fp, area
        home[index] = best
        by_overlap += 1
    grid['statefp'] = home
    log(f'  hex{size_miles}: {len(grid):,} hexagons ({by_overlap:,} homed by overlap)')
    return grid


def hex_lonlat(grid):
    """Hexagon rings in lon/lat, longitudes kept continuous around each centre."""
    transformer = from_5070()
    coords = shapely.get_coordinates(grid.geometry.values)          # 7 per hexagon
    lon, lat = transformer.transform(coords[:, 0], coords[:, 1])
    clon, clat = transformer.transform(grid['cx'].values, grid['cy'].values)
    lon = np.asarray(lon).reshape(-1, 7)
    lat = np.asarray(lat).reshape(-1, 7)
    clon = np.asarray(clon)
    lon = clon[:, None] + ((lon - clon[:, None] + 180.0) % 360.0 - 180.0)
    return shapely.polygons(np.stack([lon, lat], axis=2)), np.column_stack([clon, np.asarray(clat)])


def write_hex(output, frame, size_miles, grid, form, statefps=None, log=print):
    """hex25/hex50 as one national file per form; hex10 one file per state."""
    polygons, centres = hex_lonlat(grid)
    area = round((SQRT3 / 2.0) * size_miles ** 2, 3)
    level = f'hex{size_miles}'
    decimals = HEX_DECIMALS[form]
    homes = grid['statefp'].values
    ids = grid['id'].values
    national = size_miles in HEX_NATIONAL
    groups = [(None, np.arange(len(grid)))] if national else [
        (fp, np.flatnonzero(homes == fp)) for fp in (statefps or sorted(set(homes.tolist())))]
    for statefp, indices in groups:
        if not len(indices):
            continue
        state = frame.codes.get(statefp) if statefp else None
        order = indices[hilbert_order(shapely.points(centres[indices]))]
        features, unplaced, offcanvas, clipped = [], [], [], []
        for index in order.tolist():
            key = str(ids[index])
            home = frame.codes.get(homes[index])
            centre = [round(float(centres[index][0]), 5), round(float(centres[index][1]), 5)]
            bounds = shapely.bounds(polygons[index]).tolist()
            if form == 'albers':
                region = frame.region_of(homes[index])
                if region not in frame.placements:
                    unplaced.append(key)
                    continue
                ring = frame.project(shapely.get_coordinates(polygons[index]), region)
                shape, _removed = frame.clip(shapely.polygons(ring), region)
                if shape is None:                 # wholly outside Hawaii's clip: the NW Hawaiian Islands
                    clipped.append(key)
                    continue
                path, _dropped, px_bounds = svg_path(shape, decimals)
                if path is None:                  # wholly off the canvas
                    offcanvas.append(key)
                    continue
                anchor = frame.project(centres[index], region)[0]
                if not builder.in_clip(region, anchor):
                    anchor = shapely.get_coordinates(shapely.point_on_surface(shape))[0]
                record = {'path': path, 'anchor': [round(float(anchor[0]), decimals),
                                                   round(float(anchor[1]), decimals)],
                          'center_lonlat': centre}
                if national:
                    record['state'] = home
                features.append((key, compact(record), bounds, px_bounds))
            else:
                properties = {'id': key, 'state': home, 'center': centre}
                text = ('{"type":"Feature","properties":' + compact(properties) + ',"geometry":'
                        + geojson_geometry(polygons[index], decimals) + '}')
                features.append((key, text, bounds, None))
        base = {'level': level, 'form': form, 'tier': 'hex', 'state': state, 'statefp': statefp,
                'decimals': decimals, 'size_miles': size_miles, 'area_sq_mi': area,
                'crs_built': 'EPSG:5070'}
        folder = f'{form}/hex' + (f'/{state}' if state else '')
        if form == 'albers':
            header = dict(base, width=WIDTH, height=HEIGHT, unplaced=unplaced[:500],
                          offcanvas=offcanvas[:500],
                          id='h<size>:<q>:<r>, pointy-top axial coordinates in EPSG:5070')
            if clipped:
                header['clipped'] = clipped[:500]
            suffix = '.json'
        else:
            header = dict({'type': 'FeatureCollection'}, **base, promote_id='id',
                          antimeridian='longitudes are continuous around each hexagon centre, so '
                                       'a hexagon on 180 degrees may carry a longitude past 180')
            suffix = '.geojson'
        entry = dict(base, source={'file': 'computed in EPSG:5070', 'sha256': None},
                     simplified='none', tolerance=0, tolerance_unit='none')
        if unplaced:
            entry['unplaced'] = len(unplaced)
        if offcanvas:
            entry['offcanvas'] = len(offcanvas)
        if clipped:
            entry['clipped'] = len(clipped)
        if features:
            output.chunked(folder, level, suffix, compact(header)[:-1], features, entry, form)


# ------------------------------------------------------------ zcta by state

def zcta_homes(cache, outlines, log=print):
    """({zcta5: statefp}, [zctas with no state]) from the gazetteer internal point.

    Within a state's 500k outline, else the nearest outline within 20 km (the
    barrier islands and spits whose point the 500k coast leaves offshore).
    """
    rows, malformed = zip_points.read_gazetteer(Path(cache) / SOURCES['gaz_zcta'])
    points = gpd.GeoDataFrame({'zcta': [z for z, _la, _lo in rows]},
                              geometry=shapely.points([lo for _z, _la, lo in rows],
                                                      [la for _z, la, _lo in rows]), crs=4269)
    joined = gpd.sjoin(points, outlines[['statefp', 'geometry']], how='left', predicate='within')
    joined = joined[~joined.index.duplicated(keep='first')]
    homes = {z: (fp if isinstance(fp, str) else None) for z, fp in zip(joined['zcta'], joined['statefp'])}
    missing = [z for z, fp in homes.items() if fp is None]
    if missing:
        lost = points[points['zcta'].isin(missing)].to_crs(5070)
        near = gpd.sjoin_nearest(lost, outlines[['statefp', 'geometry']].to_crs(5070),
                                 how='left', max_distance=20000, distance_col='metres')
        near = near[~near.index.duplicated(keep='first')]
        for zcta, fp in zip(near['zcta'], near['statefp']):
            homes[zcta] = fp if isinstance(fp, str) else None
    still = sorted(z for z, fp in homes.items() if not fp)
    log(f'  zcta homes: {len(homes):,} ZCTAs, {len(missing)} placed by nearest outline, '
        f'{len(still)} with no state ({malformed} malformed gazetteer lines)')
    return homes, still, missing


def split_zcta(sources, work, homes, log=print):
    """One pass over the national ZCTA file into per-state GeoParquet parts (cached)."""
    info = sources.info('zcta')
    target = Path(work) / 'zcta' / info['sha256'][:16]
    done = target / 'DONE'
    if done.is_file():
        log(f'  zcta split: reusing {target}')
        return target
    if target.exists():
        for path in target.rglob('*.parquet'):
            path.unlink()
    target.mkdir(parents=True, exist_ok=True)
    total = pyogrio.read_info(sources.path('zcta'))['features']
    step = 3000
    for batch, start in enumerate(range(0, total, step)):
        frame = pyogrio.read_dataframe(sources.path('zcta'), skip_features=start, max_features=step,
                                       columns=['GEOID20', 'ZCTA5CE20'])
        frame['statefp'] = [homes.get(z) or 'XX' for z in frame['GEOID20']]
        for fp, group in frame.groupby('statefp'):
            folder = target / fp
            folder.mkdir(exist_ok=True)
            group.to_parquet(folder / f'part-{batch:03d}.parquet')
        del frame
    done.write_text(datetime.now(timezone.utc).isoformat(), encoding='utf-8')
    log(f'  zcta split: {total:,} features into {target}')
    return target


def read_zcta_state(split_dir, statefp):
    parts = sorted((Path(split_dir) / statefp).glob('*.parquet'))
    if not parts:
        return None
    return gpd.GeoDataFrame(pd.concat([gpd.read_parquet(path) for path in parts],
                                      ignore_index=True), crs=4269)


# ----------------------------------------------------------------- helpers

def level_rows(frame_df, key, name, statefps):
    return gpd.GeoDataFrame({'key': frame_df[key].astype(str).values,
                             'name': frame_df[name].astype(str).values,
                             'statefp': np.asarray(statefps, dtype=object)},
                            geometry=frame_df.geometry.values, crs=frame_df.crs)


def cd_rows(districts, frame):
    """Seat rows of a district file: GEOID ids, today's key ('NY-12') alongside."""
    field = next(name for name in districts.columns if name.startswith('CD') and name.endswith('FP'))
    labels = districts['NAMELSAD'] if 'NAMELSAD' in districts else [''] * len(districts)
    keys, names, keep = [], [], []
    for statefp, code, label in zip(districts['STATEFP'], districts[field], labels):
        stusps = frame.codes.get(statefp)
        key = builder.district_key(stusps, code) if stusps else None
        keep.append(key is not None)
        if key is not None:
            keys.append(key)
            names.append(label or builder.district_name({}, key))
    subset = districts[np.array(keep, dtype=bool)]
    rows = level_rows(subset, 'GEOID', 'GEOID', subset['STATEFP'].values)
    rows['name'] = names
    rows['district'] = keys
    return rows


def district_extra(rows):
    return [{'district': key} for key in rows['district'].tolist()]


def place_extra(rows):
    return [{'label': label} for label in rows['label'].tolist()]


def pmtiles_bounds(tiles_dir):
    """[(file, [min_lon, min_lat, max_lon, max_lat])] from PMTiles v3 headers."""
    found = []
    for path in sorted(Path(tiles_dir).glob('*.pmtiles')) if Path(tiles_dir).is_dir() else []:
        with open(path, 'rb') as handle:
            header = handle.read(127)
        if len(header) < 127 or header[:7] != b'PMTiles' or header[7] != 3:
            continue
        min_lon, min_lat, max_lon, max_lat = struct.unpack_from('<iiii', header, 102)
        found.append((path.name, [min_lon / 1e7, min_lat / 1e7, max_lon / 1e7, max_lat / 1e7]))
    return found


def wgs84_scope(option, outlines, frame, tiles_dir, log=print):
    """(statefps for the per-state WGS84 tiers, a description for index.json)."""
    everything = sorted(outlines['statefp'].tolist())
    if option == 'all':
        return everything, {'mode': 'all'}
    if option != 'extract':
        wanted = {code.strip().upper() for code in option.split(',') if code.strip()}
        chosen = [fp for fp in everything if frame.codes.get(fp) in wanted]
        return chosen, {'mode': 'list', 'states': sorted(wanted)}
    extracts = pmtiles_bounds(tiles_dir)
    if not extracts:
        log('  wgs84 scope: no street extract found; building every state')
        return everything, {'mode': 'extract', 'extracts': [], 'fallback': 'all'}
    boxes = shapely.box(*np.array([bounds for _name, bounds in extracts]).T)
    hit = set()
    for box in np.atleast_1d(boxes):
        hit.update(outlines.loc[outlines.intersects(box), 'statefp'].tolist())
    chosen = sorted(hit)
    log(f'  wgs84 scope: {", ".join(frame.codes.get(fp, fp) for fp in chosen)} '
        f'(states meeting {", ".join(name for name, _b in extracts)})')
    return chosen, {'mode': 'extract',
                    'extracts': [{'file': name, 'bounds': bounds} for name, bounds in extracts],
                    'states': [frame.codes.get(fp, fp) for fp in chosen]}


# --------------------------------------------------------------- the build

def build(args, log=print):
    meter = PeakMeter()
    cache, out = Path(args.cache), Path(args.out)
    sources = Sources(cache)
    output = Output(out, log)
    forms = [form.strip() for form in args.forms.split(',') if form.strip()]
    levels = {level.strip() for level in args.levels.split(',') if level.strip()} or None
    subset = {code.strip().upper() for code in args.states.split(',') if code.strip()} or None

    def want(level):
        return levels is None or level in levels

    meter.start('frame')
    frame = Frame(cache, log=log)
    meter.end(log)

    meter.start('outlines: 500k counties dissolved by state')
    counties = sources.read('county')
    outlines = (counties.assign(statefp=counties['STATEFP'])[['statefp', 'geometry']]
                .dissolve(by='statefp', method='coverage').reset_index())
    log(f'  outlines: {len(outlines)} states/territories from {len(counties):,} counties')
    meter.end(log)

    every_state = sorted(outlines['statefp'].tolist())
    states = [fp for fp in every_state if subset is None or frame.codes.get(fp) in subset]
    street_states, scope = wgs84_scope(args.wgs84_states, outlines, frame, args.tiles, log)
    output.build_notes['wgs84_scope'] = scope

    # ---- national files (skipped when a state subset is asked for)
    if subset is None:
        meter.start('national: state + cd (compat), county, z0')
        if 'albers' in forms and (want('state') or want('cd')):
            compat_national(output, frame, sources, log)
        if want('county'):
            rows = level_rows(counties, 'GEOID', 'NAMELSAD', counties['STATEFP'].values)
            tiers = {form: tier for form, tier in (('albers', ['national']), ('wgs84', ['z0']))
                     if form in forms}
            write_level(output, frame, 'county', None, None, rows, sources.info('county'), tiers, log)
        if 'wgs84' in forms and want('state'):
            states20 = sources.read('state20m')
            rows = level_rows(states20, 'STATEFP', 'NAME', states20['STATEFP'].values)
            write_level(output, frame, 'state', None, None, rows, sources.info('state20m'),
                        {'wgs84': ['z0']}, log)
        if 'wgs84' in forms and want('cd'):
            for vintage in VINTAGES:
                rows = cd_rows(sources.read(vintage), frame)
                write_level(output, frame, vintage, None, None, rows, sources.info(vintage),
                            {'wgs84': ['z0']}, log, extra_fn=district_extra)
        meter.end(log)

    # ---- hexagons
    if want('hex'):
        outlines_5070 = outlines.to_crs(5070)
        for size in HEX_SIZES:
            if size in HEX_NATIONAL and subset is not None:
                continue
            meter.start(f'hex{size}')
            grid = build_hex_grid(size, outlines_5070, log)
            for form in forms:
                if size in HEX_NATIONAL:
                    write_hex(output, frame, size, grid, form, log=log)
                else:
                    scope_fps = states if form == 'albers' else [fp for fp in states
                                                                 if fp in street_states]
                    write_hex(output, frame, size, grid, form, statefps=scope_fps, log=log)
            del grid
            meter.end(log)
        del outlines_5070

    if args.national_only:
        return finish(output, frame, sources, meter, log)

    # ---- per state
    split_dir = None
    if want('zcta'):
        meter.start('zcta: homes + one-pass split into per-state parquet')
        homes, homeless, by_nearest = zcta_homes(cache, outlines, log)
        split_dir = split_zcta(sources, cache / 'v2work', homes, log)
        output.build_notes['zcta_home'] = {
            'basis': 'the state whose 500k outline holds the ZCTA gazetteer internal point, else '
                     'the nearest outline within 20 km',
            'by_nearest_outline': sorted(by_nearest), 'without_state': homeless}
        del homes
        meter.end(log)
    chambers = {}
    for chamber, seat in (('sldu', 'SLDUST'), ('sldl', 'SLDLST')):
        if want(chamber):
            frame_df = sources.read(chamber)
            chambers[chamber] = frame_df[frame_df[seat].map(has_seat)]
    for statefp in states:
        state = frame.codes.get(statefp)
        if state is None:
            log(f'  WARNING statefp {statefp} has no postal code; skipped')
            continue
        streets = 'wgs84' in forms and statefp in street_states
        tiers = {}
        if 'albers' in forms:
            tiers['albers'] = ['national', 'detail']
        if streets:
            tiers['wgs84'] = ['z5', 'z10']
        detail_only = {key: [t for t in value if t != 'national'] for key, value in tiers.items()}
        meter.start(f'state {state}')
        if want('state'):
            rows = level_rows(outlines[outlines['statefp'] == statefp], 'statefp', 'statefp',
                              [statefp])
            rows['name'] = frame.names.get(state, state)
            write_level(output, frame, 'state', state, statefp, rows, sources.info('county'),
                        detail_only, log)
        if want('county'):
            chosen = counties[counties['STATEFP'] == statefp]
            rows = level_rows(chosen, 'GEOID', 'NAMELSAD', chosen['STATEFP'].values)
            write_level(output, frame, 'county', state, statefp, rows, sources.info('county'),
                        detail_only, log)
        for chamber, frame_df in chambers.items():
            chosen = frame_df[frame_df['STATEFP'] == statefp]
            if len(chosen):
                rows = level_rows(chosen, 'GEOID', 'NAMELSAD', chosen['STATEFP'].values)
                write_level(output, frame, chamber, state, statefp, rows, sources.info(chamber),
                            tiers, log)
        if want('place'):
            chosen = sources.read('place', where=f"STATEFP = '{statefp}'")
            if len(chosen):
                rows = level_rows(chosen, 'GEOID', 'NAME', chosen['STATEFP'].values)
                rows['label'] = chosen['NAMELSAD'].astype(str).values
                write_level(output, frame, 'place', state, statefp, rows, sources.info('place'),
                            tiers, log, extra_fn=place_extra)
            del chosen
        if want('tract'):
            chosen = sources.read('tract', where=f"STATEFP = '{statefp}'")
            if len(chosen):
                rows = level_rows(chosen, 'GEOID', 'NAMELSAD', chosen['STATEFP'].values)
                write_level(output, frame, 'tract', state, statefp, rows, sources.info('tract'),
                            tiers, log)
            del chosen
        if want('zcta') and split_dir is not None:
            chosen = read_zcta_state(split_dir, statefp)
            if chosen is not None and len(chosen):
                rows = level_rows(chosen, 'GEOID20', 'ZCTA5CE20', [statefp] * len(chosen))
                write_level(output, frame, 'zcta', state, statefp, rows, sources.info('zcta'),
                            tiers, log)
            del chosen
        if streets and want('cd'):
            for vintage in VINTAGES:
                fine = CD_500K[vintage] if (cache / CD_500K[vintage]).is_file() else None
                districts = sources.read(vintage, where=f"STATEFP = '{statefp}'", name=fine)
                rows = cd_rows(districts, frame) if len(districts) else districts
                if len(rows):
                    write_level(output, frame, vintage, state, statefp, rows,
                                sources.info(vintage, name=fine), {'wgs84': ['z5']}, log,
                                extra_fn=district_extra)
        meter.end(log)
    return finish(output, frame, sources, meter, log)


def finish(output, frame, sources, meter, log):
    memory = meter.close()
    write_index(output, frame, sources, memory, log)
    return output, memory


def write_index(output, frame, sources, memory, log=print):
    index_path = output.root / 'index.json'
    previous = {}
    if index_path.is_file():
        try:
            previous = json.loads(index_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {}
    entries = {entry['path']: entry for entry in previous.get('files', [])}
    # a group this run rewrote loses its old entries (its stale parts were deleted)
    entries = {path: entry for path, entry in entries.items()
               if path.rsplit('/', 1)[0] + '/' + entry['level'] not in output.cleared}
    entries.update(output.entries)
    entries = {path: entry for path, entry in entries.items() if (output.root / path).is_file()}
    files = [entries[path] for path in sorted(entries)]
    summary = {}
    for entry in files:
        slot = summary.setdefault(f"{entry['form']}/{entry['tier']}/{entry['level']}",
                                  {'files': 0, 'bytes': 0, 'features': 0, 'largest': 0})
        slot['files'] += 1
        slot['bytes'] += entry['bytes']
        slot['features'] += entry.get('features', 0)
        slot['largest'] = max(slot['largest'], entry['bytes'])
    for key in sorted(SOURCES):
        if sources.path(key).is_file():
            sources.info(key)
    memory_log = previous.get('memory_runs', [])[-9:] + [dict(memory, finished_at=datetime.now(
        timezone.utc).replace(microsecond=0).isoformat())]
    payload = {
        'version': 2,
        'tool': TOOL, 'tool_version': VERSION,
        'built_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'frame': {'width': WIDTH, 'height': HEIGHT,
                  'projection': builder.projection_payload(frame.placements),
                  'from': 'build_district_geo.REGIONS + build_states(cb_2024_us_state_20m.zip); '
                          'identical to geo/cd119.json projection.regions'},
        'forms': {'albers': 'JSON; features keyed by id; SVG path strings in the 975x610 frame, '
                            '"M x y L ... Z" per ring (today\'s grammar); an anchor inside each',
                  'wgs84': 'GeoJSON FeatureCollection (RFC 7946 winding); properties.id is the '
                           'GEOID (use promoteId "id"); NAD83 coordinates written as WGS84 (under '
                           '2 m apart, far below the 1:500,000 generalisation)'},
        'tiers': TIERS,
        'compat': dict(COMPAT, files=['albers/national/state.json'] + [
            f'albers/national/{vintage}.json' for vintage in VINTAGES],
            keys='states by postal code, districts by "<ST>-<NN>"/"<ST>-AL" exactly as geo/cd*.json'),
        'hex': {'crs': 'EPSG:5070', 'sizes_miles': list(HEX_SIZES), 'mile_m': MILE,
                'size': 'distance between the centres of neighbouring hexagons (flat-to-flat width)',
                'orientation': 'pointy-top', 'side': 'size / sqrt(3)', 'area': '(sqrt(3)/2) * size^2',
                'id': 'h<size>:<q>:<r>',
                'formula': 's = size*1609.344/sqrt(3); qf = (sqrt(3)/3*x - y/3)/s; rf = (2/3*y)/s; '
                           'cube-round with floor(v+0.5); x, y = EPSG:5070 metres from NAD83 '
                           'longitude/latitude',
                'membership': 'every hexagon touching a 500k state outline; home state = the '
                              'outline holding its centre, else the largest overlap',
                'national': [f'hex{size}' for size in HEX_NATIONAL], 'per_state': ['hex10']},
        'sources': dict(sorted(sources.hashed.items())),
        'part_bytes': PART_BYTES,
        'build_notes': dict(previous.get('build_notes', {}), **output.build_notes),
        'files': files,
        'summary': dict(sorted(summary.items())),
        'totals': {'files': len(files), 'bytes': sum(entry['bytes'] for entry in files)},
        'memory_runs': memory_log,
        'notes': [
            'Hawaii is clipped to its extent (build_district_geo REGIONS[\'hawaii\'][\'clip\'], '
            'the canvas around the Hawaii box one pixel clear of the Alaska, lower-48 and American '
            'Samoa boxes), as d3\'s geoAlbersUsa clips each inset to its clipExtent (2026-09-26): '
            'the Northwestern Hawaiian Islands, which the 500k files carry and the 20m fit never '
            'held, are removed and counted (clipped_rings; a feature left with nothing is listed '
            'under clipped), and no Hawaiian pixel moves. Rings wholly off the canvas are still '
            'dropped and counted (offcanvas_rings); after the clip there are none.',
            'The Alaska conic wraps longitude into [-180, 180] degrees like d3\'s geoAlbersUsa '
            '(2026-09-26): the Near Islands east of 180 degrees draw at the west end of the '
            'Aleutian chain, and the inset takes the 0.35 x lower-48 cap (scale 376.54) instead '
            'of the 254.24 the unwrapped vertices forced on the fit.',
            'A national layer that is not one valid coverage is simplified region by region '
            '(simplify_regions), never per feature for the whole nation: before the Hawaii clip, '
            'French Frigate Shoals, drawn by the Hawaii conic inside the Alaska box, overlapped '
            'the Kenai Peninsula at Alaska\'s corrected scale.',
            'American Samoa, Guam and the Northern Marianas have no placement in the frame (the '
            '20m state file has no polygon for them): they are in every wgs84 file and in the '
            'lookup tables, and counted as unplaced in the albers ones.',
            'Congressional districts come from the 20m files only (no 500k district file is '
            'cached): albers/national, wgs84/z0 and wgs84/z5, never wgs84/z10, where 1:20,000,000 '
            'lines would sit a kilometre or more off the 500k tracts.',
            'A ZCTA belongs to the state holding its gazetteer internal point (else the nearest '
            'outline within 20 km); a ZCTA crossing a state line is drawn whole in that state\'s '
            'file only.',
            'Two levels that follow the same boundary (a county edge and its tracts\' edges) are '
            'simplified separately and can differ by up to the tier tolerance.',
        ],
    }
    size, _digest = write_bytes_atomic(index_path, (json.dumps(payload, indent=1) + '\n').encode('utf-8'))
    log(f'  wrote index.json: {len(files):,} files, {size:,} bytes')
    return payload


def verify_tree(root):
    """Problems with a built v2 tree: index.json must list every file, with its bytes and sha256."""
    root = Path(root)
    index_path = root / 'index.json'
    if not index_path.is_file():
        return [f'{index_path} is missing']
    index = json.loads(index_path.read_text(encoding='utf-8'))
    listed = {entry['path']: entry for entry in index.get('files', [])}
    on_disk = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
    problems = [f'not in index: {path}' for path in sorted(on_disk - set(listed) - {'index.json'})]
    problems += [f'missing: {path}' for path in sorted(set(listed) - on_disk)]
    for path, entry in listed.items():
        target = root / path
        if target.is_file() and (target.stat().st_size != entry['bytes']
                                 or sha256_file(target) != entry['sha256']):
            problems.append(f'changed since indexed: {path}')
    return problems


def install(staging, installed, replace=False, log=print):
    """Move a verified staging tree into the app in ONE directory rename.

    An existing installed tree is first renamed aside, out of the app tree, to
    <staging parent>/v2-previous-<UTC stamp> (never deleted). Both are renames on one
    volume, so the console sees the old tree, then no tree, then the new one - never
    a half-written file.
    """
    staging, installed = Path(staging).resolve(), Path(installed).resolve()
    problems = verify_tree(staging)
    if problems:
        raise SystemExit(f'not installing {staging}: {len(problems)} problems, first: {problems[:3]}')
    if installed.exists():
        if not replace:
            raise SystemExit(f'{installed} exists; pass --replace to move it aside first')
        aside = staging.parent / ('v2-previous-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        os.replace(installed, aside)
        log(f'  moved the installed tree aside to {aside}')
    installed.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, installed)
    log(f'  installed {staging} -> {installed}')
    return installed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT,
                        help='the staging folder the build writes (default mentat/.cache/geo_build/v2)')
    parser.add_argument('--install', action='store_true',
                        help='do not build: verify --out and move it to mentat/app/geo/v2 in one rename')
    parser.add_argument('--replace', action='store_true',
                        help='with --install: move an existing mentat/app/geo/v2 aside first')
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--tiles', type=Path, default=DEFAULT_TILES,
                        help='where the street .pmtiles extracts live (for --wgs84-states extract)')
    parser.add_argument('--states', default='', help='comma-separated postal codes (default: all); '
                                                     'national files are skipped when given')
    parser.add_argument('--levels', default='', help='comma-separated: state,cd,county,sldu,sldl,'
                                                      'place,zcta,tract,hex (default: all)')
    parser.add_argument('--forms', default='albers,wgs84')
    parser.add_argument('--wgs84-states', default='extract',
                        help='extract (default) | all | comma-separated postal codes')
    parser.add_argument('--national-only', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv)
    log = (lambda *_: None) if args.quiet else (lambda *a: print(*a, flush=True))
    if args.install:
        install(args.out, INSTALLED, replace=args.replace, log=log)
        return 0
    if Path(args.out).resolve() == INSTALLED.resolve():
        raise SystemExit('refusing to build straight into mentat/app/geo/v2: build into staging '
                         'and --install (see DEFAULT_OUT)')
    started = time.time()
    output, memory = build(args, log)
    total = sum(entry['bytes'] for entry in output.entries.values())
    log(f'done in {time.time() - started:.1f}s: {len(output.entries):,} files written, '
        f'{total:,} bytes, process peak {memory["process_peak_wset_mb"]} MB')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
