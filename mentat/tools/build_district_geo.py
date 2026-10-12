"""Build the static congressional-district geography the S8 map draws from.

Dev-time only. The console never runs this and never reaches a network to draw
a map: this script downloads the Census cartographic boundary files once,
projects them, and writes plain JSON under ``mentat/app/geo/`` that the page
fetches like any other static asset.

    python mentat/tools/build_district_geo.py              # everything
    python mentat/tools/build_district_geo.py --only 119   # one vintage
    python mentat/tools/build_district_geo.py --no-zcta    # skip crosswalks
    python mentat/tools/build_district_geo.py --refresh    # re-download

Pure standard library on purpose (json, zipfile, struct, math, urllib): S8's
data layer must build on both console Pythons with nothing installed, so the
shapefile reader and the projection live in this file rather than in geopandas
or shapely. Ruling 11 of S6_DESIGN (shapely into the launcher Python) is about
the *geocoding* layer, not about drawing, and is still Paul's to make.

WHAT THE CENSUS ACTUALLY PUBLISHES (checked 2026-09-17, directory indexes read,
not guessed - the names the S8 brief expected are wrong in two places):

  vintage 119  https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_20m.zip
  vintage 118  https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_cd118_20m.zip
  vintage 116  https://www2.census.gov/geo/tiger/GENZ2021/shp/cb_2021_us_cd116_20m.zip
  states       https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_20m.zip

  * GENZ2022 carries NO cd116 file. Its congressional file is
    cb_2022_us_cd118_20m.zip - the 2022 vintage had already moved to the new
    (118th) maps. GENZ2021 is the newest vintage that still carries cd116,
    which is the 116th=117th Congress map (both Congresses sat on the same
    2011 lines), so that is what this script uses for the 2021-2022 slot.
  * The ZCTA relationship files are named cd-first, not zcta-first:
    tab20_cd11920_zcta520_natl.txt, tab20_cd11820_zcta520_natl.txt. There is
    NO cd116 relationship file in rel2020/cd-sld/ (the 2020 ZCTAs were only
    ever tabulated against the 118th and 119th districts) and the older
    rel/ directory stops at zcta_cd111_rel_10.txt. Vintage 116 therefore gets
    boundaries but no ZIP crosswalk, and index.json says so rather than
    substituting a crosswalk from another map. See ZCTA_SOURCES.

PROJECTION (all parameters here, nothing implicit)

Albers conic equal area, computed on the unit sphere with Snyder's formulas
(Map Projections - A Working Manual, eq. 14-1..14-4):

    n  = (sin f1 + sin f2) / 2
    C  = cos^2 f1 + 2 n sin f1
    p  = sqrt(C - 2 n sin f) / n
    p0 = sqrt(C - 2 n sin f0) / n
    t  = n (l - l0)    (l - l0 wrapped into -180..180 degrees, as d3 wraps a
                        rotated longitude; Alaska's islands east of 180 need it)
    x  = p sin t
    y  = p0 - p cos t          (y grows north; the screen transform flips it)

Screen coordinates are ``sx = tx + k*x``, ``sy = ty - k*y``, so north is up.

Five regions, each with its own conic parameters, laid out like the familiar
d3 ``geoAlbersUsa`` picture (lower 48 filling the canvas, Alaska and Hawaii as
insets under the south-west, the territories along the bottom). This is
*d3-like*, not pixel-identical to d3: d3 hard-codes offsets for a 960x500 box
from ``center([-0.6, 38.7])``, while here every region is fitted into an
explicitly declared target box (REGIONS below), which is reproducible and
survives a canvas size change. The canvas is 975 x 610, the us-atlas size.

  lower 48   parallels 29.5 / 45.5, central meridian -96, origin 37.5
             (the conventional USA Albers; origin only sets the y offset,
             which the fit then absorbs)
  Alaska     parallels 55 / 65, central meridian -154, origin 50,
             scale capped at 0.35 x the lower-48 scale (d3's factor), so it
             reads as an inset and not as a third of the country
  Hawaii     parallels 8 / 18, central meridian -157, origin 13, scale 1.0x
  Caribbean  parallels 18.0 / 18.5, central meridian -66.4, origin 17.8,
             scale 1.0x - Puerto Rico and the US Virgin Islands together
  Pacific    American Samoa, Guam and the Northern Marianas, one conic each
             (they are 3,000 miles apart), scale capped at 1.0x

Every region is projected with ITS OWN conic and then fitted into its target
box, so no geometry is ever run through the lower-48 conic from the far side of
the date line. A state or district whose STATEFP is in no region is written to
the output's ``unplaced`` list and never silently dropped.

Hawaii alone is also CLIPPED, the way d3's geoAlbersUsa clips each inset to
its clipExtent (lane L3, 2026-09-26): its conic draws only inside
REGIONS['hawaii']['clip'], the canvas around the Hawaii box one pixel clear
of the Alaska, lower-48 and American Samoa boxes (x 247-371, y 497-610).
The 20m files hold the main islands only, which the fit put inside the box;
the 500k files build_state_legislative_geo and build_geo_layers project
through this module also carry the Northwestern Hawaiian Islands, which the
Hawaii conic drew west of the box - the nearer ones inside the Alaska box,
French Frigate Shoals on the Kenai Peninsula, the farther ones off the
canvas. A ring wholly outside the clip is removed (project_rings_counted
counts it), a ring crossing it is cut to it, a ring inside it is untouched,
and a point outside it is not drawn (in_clip). The clip is wider than the box
on purpose: the 500k Big Island reaches 0.135 px east of the box (Cape
Kumukahi), Kaula lies 5 px west of it and the hexagons over the islands reach
further still, and none of that may move.

OUTPUT (mentat/app/geo/)

  cd119.json, cd118.json, cd116.json
    {"vintage","source","fetched_at","width","height","districts":{KEY:{...}},
     "states":{STUSPS:{...}},"unplaced":[...],"build":{...}}
    Every path ends each ring with "Z" and does NOT repeat the first point, as
    SVG intends. A consumer that parses the path back into rings for its own
    point-in-polygon test must close each ring first; leaving it open drops one
    segment and, on a state like New Mexico, one large triangle of area.
    KEY is "<STUSPS>-<NN>" for a numbered district and "<STUSPS>-AL" for an
    at-large state, DC and the territories (Census writes CD..FP "00" for an
    at-large state and "98" for a delegate seat; both mean one seat for the
    whole place, so both become AL). Paths are SVG "M x y L ... Z" with one
    decimal; bbox is [x0,y0,x1,y1]; centroid is the area-weighted centroid of
    the district's largest ring (a point inside the shape, unlike the centroid
    of a many-island district).
  zcta_cd119.json, zcta_cd118.json
    {"<zcta5>": [["<STUSPS>-<district>", share], ...]} sorted by share.
  index.json
    the vintage list, the cycle each one serves, and the crosswalk each one
    has - the only file the runtime reads to resolve a cycle.

SHARES are land area, not population: the Census relationship file carries
AREALAND_PART and AREAWATER_PART and no population column at all (checked
against the header of tab20_cd11920_zcta520_natl.txt), so land area is the
only split the source supports. A ZIP that straddles districts is therefore
approximate twice over - by area and by ZCTA-vs-ZIP - which is exactly what
ruling 8 says to show.
"""

from datetime import datetime, timezone
import argparse
import io
import json
import math
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

MENTAT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = MENTAT / 'app' / 'geo'
DEFAULT_CACHE = MENTAT / '.cache' / 'geo'

WIDTH, HEIGHT = 975, 610
USER_AGENT = 'Mentat-console/1.0 (local district map build; contact via repo)'
TIGER = 'https://www2.census.gov/geo/tiger'
REL2020 = 'https://www2.census.gov/geo/docs/maps-data/data/rel2020/cd-sld'

# vintage -> (url, the cycle it serves). Cycle mapping is S6_DESIGN ruling 9
# (2026 -> 119th, 2024 -> 118th, 2022 -> the 116th=117th map).
SOURCES = {
    '119': (f'{TIGER}/GENZ2024/shp/cb_2024_us_cd119_20m.zip', 2026),
    '118': (f'{TIGER}/GENZ2023/shp/cb_2023_us_cd118_20m.zip', 2024),
    '116': (f'{TIGER}/GENZ2021/shp/cb_2021_us_cd116_20m.zip', 2022),
}
STATE_SOURCE = f'{TIGER}/GENZ2024/shp/cb_2024_us_state_20m.zip'
# Only the vintages Census tabulated the 2020 ZCTAs against exist here.
ZCTA_SOURCES = {
    '119': f'{REL2020}/tab20_cd11920_zcta520_natl.txt',
    '118': f'{REL2020}/tab20_cd11820_zcta520_natl.txt',
    '116': None,
}

# STATEFP that need their own projection, and the box they are fitted into.
# (x0, y0, x1, y1) in canvas pixels; scale_cap is a multiple of the lower-48
# scale so relative sizes stay honest even when a box could hold more.
REGIONS = {
    'lower48': dict(fips=None, parallels=(29.5, 45.5), meridian=-96.0, origin=37.5,
                    box=(8.0, 6.0, 967.0, 496.0), scale_cap=None),
    'alaska': dict(fips={'02'}, parallels=(55.0, 65.0), meridian=-154.0, origin=50.0,
                   box=(10.0, 366.0, 246.0, 600.0), scale_cap=0.35),
    # Hawaii alone also has a clip extent (see the docstring): it draws only inside `clip`,
    # the canvas around its box one pixel clear of the Alaska, lower-48 and Samoa boxes.
    'hawaii': dict(fips={'15'}, parallels=(8.0, 18.0), meridian=-157.0, origin=13.0,
                   box=(258.0, 512.0, 352.0, 602.0), scale_cap=1.0,
                   clip=(247.0, 497.0, 371.0, 610.0)),
    'caribbean': dict(fips={'72', '78'}, parallels=(18.0, 18.5), meridian=-66.4, origin=17.8,
                      box=(838.0, 540.0, 963.0, 602.0), scale_cap=1.0),
    'samoa': dict(fips={'60'}, parallels=(-14.5, -14.1), meridian=-170.7, origin=-14.3,
                  box=(372.0, 566.0, 414.0, 602.0), scale_cap=1.0),
    'guam': dict(fips={'66'}, parallels=(13.3, 13.6), meridian=144.8, origin=13.4,
                 box=(424.0, 566.0, 460.0, 602.0), scale_cap=1.0),
    'marianas': dict(fips={'69'}, parallels=(14.9, 18.8), meridian=145.7, origin=16.5,
                     box=(470.0, 566.0, 502.0, 602.0), scale_cap=1.0),
}
# The 20m cartographic files carry 50 states + DC + Puerto Rico and nothing
# else: American Samoa, Guam, the Northern Marianas and the US Virgin Islands
# have no 20m polygon in either the state or the congressional file (checked
# 2026-09-17: 52 records in each). Their FIPS codes are still needed, because
# the ZCTA relationship file DOES carry their ZIPs (17 of them), and a ZIP the
# crosswalk drops is money the map would lose. So the code is resolved from
# this table when the state shapefile has no entry, the ZIP keeps its district
# key, and the place is listed in the output's `undrawn` map with the reason -
# the S8 ranked list can then show the amount even though nothing can be
# coloured. These four FIPS codes are fixed by FIPS 5-2 and will not change.
TERRITORY_FALLBACK = {'60': ('AS', 'American Samoa'), '66': ('GU', 'Guam'),
                      '69': ('MP', 'Commonwealth of the Northern Mariana Islands'),
                      '78': ('VI', 'United States Virgin Islands')}
LOWER48_SKIP = {fips for region in REGIONS.values() if region['fips'] for fips in region['fips']}
MIN_STEP = 0.5          # drop a projected point closer than this to the last kept one
DECIMALS = 1            # path precision in pixels
SHARE_FLOOR = 0.01      # a ZIP part smaller than this is dropped and the rest renormalised


# --------------------------------------------------------------------------- io

def fetch(url, cache_dir, refresh=False, log=print):
    """Download `url` into the cache once and return the local path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / url.rsplit('/', 1)[-1]
    if target.is_file() and target.stat().st_size and not refresh:
        log(f'  cached {target.name} ({target.stat().st_size:,} bytes)')
        return target
    started = time.time()
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    temporary = target.with_suffix(target.suffix + '.part')
    temporary.write_bytes(payload)
    temporary.replace(target)
    log(f'  fetched {target.name} ({len(payload):,} bytes, {time.time() - started:.1f}s)')
    return target


# ------------------------------------------------------------------ shapefile

def read_dbf(payload):
    """Rows of a .dbf as dicts of stripped strings (Census writes cp1252)."""
    count, header_length, record_length = struct.unpack_from('<IHH', payload, 4)
    fields, offset = [], 32
    while payload[offset] != 0x0D:
        name = payload[offset:offset + 11].split(b'\x00')[0].decode('ascii', 'replace')
        length = payload[offset + 16]
        fields.append((name, length))
        offset += 32
    rows = []
    for index in range(count):
        start = header_length + index * record_length
        record = payload[start:start + record_length]
        if not record or record[:1] == b'*':          # deleted
            rows.append(None)
            continue
        row, cursor = {}, 1
        for name, length in fields:
            row[name] = record[cursor:cursor + length].decode('cp1252', 'replace').strip()
            cursor += length
        rows.append(row)
    return rows


def read_shp(payload):
    """Rings of every polygon record in a .shp, one list of rings per record.

    Handles the null shape (0) and Polygon / PolygonZ / PolygonM (5/15/25):
    the XY point array comes first in all three, so the Z and M arrays that
    follow are simply not read. Multipoint and line types are not expected in
    a cartographic boundary file and raise rather than guess.
    """
    records, offset, total = [], 100, len(payload)
    while offset + 8 <= total:
        _, words = struct.unpack_from('>II', payload, offset)
        body = offset + 8
        offset = body + words * 2
        shape_type, = struct.unpack_from('<i', payload, body)
        if shape_type == 0:
            records.append([])
            continue
        if shape_type not in (5, 15, 25):
            raise ValueError(f'unexpected shapefile shape type {shape_type}')
        part_count, point_count = struct.unpack_from('<ii', payload, body + 36)
        parts = struct.unpack_from(f'<{part_count}i', payload, body + 44)
        points_at = body + 44 + part_count * 4
        flat = struct.unpack_from(f'<{point_count * 2}d', payload, points_at)
        rings = []
        for index, start in enumerate(parts):
            end = parts[index + 1] if index + 1 < part_count else point_count
            rings.append([(flat[2 * i], flat[2 * i + 1]) for i in range(start, end)])
        records.append(rings)
    return records


def read_shapefile(zip_path):
    """[(attributes, rings)] for the one shapefile inside a Census zip."""
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        shp = next(name for name in names if name.lower().endswith('.shp'))
        dbf = next(name for name in names if name.lower().endswith('.dbf'))
        shapes = read_shp(archive.read(shp))
        rows = read_dbf(archive.read(dbf))
    if len(shapes) != len(rows):
        raise ValueError(f'{zip_path.name}: {len(shapes)} shapes but {len(rows)} dbf rows')
    return [(row, rings) for row, rings in zip(rows, shapes) if row is not None]


# ------------------------------------------------------------------ projection

class Albers:
    """Albers conic equal area on the unit sphere (see the module docstring)."""

    def __init__(self, parallels, meridian, origin):
        first, second = (math.radians(value) for value in parallels)
        self.meridian = math.radians(meridian)
        self.n = (math.sin(first) + math.sin(second)) / 2
        if abs(self.n) < 1e-10:                       # degenerate: parallels on the equator
            self.n = math.sin(first) or 1e-10
        self.c = math.cos(first) ** 2 + 2 * self.n * math.sin(first)
        self.rho0 = self._rho(math.radians(origin))

    def _rho(self, latitude):
        inner = self.c - 2 * self.n * math.sin(latitude)
        return math.sqrt(max(inner, 0.0)) / self.n

    def __call__(self, longitude, latitude):
        rho = self._rho(math.radians(latitude))
        # The longitude difference is wrapped into [-180, 180] degrees, as d3's rotation
        # wraps it (geoAlbersUsa's Alaska conic is rotate([154, 0])). Alaska's Near and
        # Rat Islands lie east of 180 degrees: unwrapped they drew at the top left of the
        # inset, and the fit that included them shrank the whole inset to scale 254.24
        # instead of the 0.35 x lower-48 cap, 376.54 (lane L2, 2026-09-26). A longitude
        # already within 180 degrees of the meridian - every other region - takes exactly
        # the arithmetic it took before.
        delta = math.radians(longitude) - self.meridian
        if delta > math.pi:
            delta -= 2 * math.pi
        elif delta < -math.pi:
            delta += 2 * math.pi
        theta = self.n * delta
        return rho * math.sin(theta), self.rho0 - rho * math.cos(theta)


def ring_bounds(rings):
    xs = [point[0] for ring in rings for point in ring]
    ys = [point[1] for ring in rings for point in ring]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def fit(bounds, box, scale_cap=None, base_scale=None):
    """(scale, tx, ty) placing projected `bounds` inside canvas `box`.

    Uniform scale, centred in the box, capped at `scale_cap * base_scale` so an
    inset keeps its true size relative to the lower 48 instead of filling
    whatever room its box happens to offer.
    """
    x0, y0, x1, y1 = bounds
    bx0, by0, bx1, by1 = box
    span_x, span_y = max(x1 - x0, 1e-12), max(y1 - y0, 1e-12)
    scale = min((bx1 - bx0) / span_x, (by1 - by0) / span_y)
    if scale_cap is not None and base_scale is not None:
        scale = min(scale, scale_cap * base_scale)
    tx = (bx0 + bx1) / 2 - scale * (x0 + x1) / 2
    ty = (by0 + by1) / 2 + scale * (y0 + y1) / 2      # + because screen y is flipped
    return scale, tx, ty


def region_of(statefp):
    for name, region in REGIONS.items():
        if region['fips'] and statefp in region['fips']:
            return name
    return 'lower48'


def build_placements(features, log=print):
    """One (scale, tx, ty) per region, from the geometry actually present.

    The lower 48 is fitted first because every inset's cap is a multiple of its
    scale. A region with no geometry in this file gets no placement, and its
    features (there are none) cannot be drawn.
    """
    raw = {}
    for statefp, rings in features:
        name = region_of(statefp)
        projection = raw.setdefault(name, {'projection': projector(name), 'bounds': None})
        for ring in rings:
            for longitude, latitude in ring:
                x, y = projection['projection'](longitude, latitude)
                current = projection['bounds']
                projection['bounds'] = ((min(current[0], x), min(current[1], y),
                                         max(current[2], x), max(current[3], y))
                                        if current else (x, y, x, y))
    placements = {}
    if 'lower48' not in raw:
        raise ValueError('no lower-48 geometry in this shapefile; refusing to guess a scale')
    base_scale, tx, ty = fit(raw['lower48']['bounds'], REGIONS['lower48']['box'])
    placements['lower48'] = (base_scale, tx, ty)
    for name, region in REGIONS.items():
        if name == 'lower48' or name not in raw:
            continue
        placements[name] = fit(raw[name]['bounds'], region['box'],
                               region['scale_cap'], base_scale)
    log(f'  lower-48 scale {base_scale:.2f} px/unit; regions placed: '
        + ', '.join(sorted(placements)))
    return placements


_PROJECTORS = {}


def projector(name):
    if name not in _PROJECTORS:
        region = REGIONS[name]
        _PROJECTORS[name] = Albers(region['parallels'], region['meridian'], region['origin'])
    return _PROJECTORS[name]


def in_clip(region, point):
    """False when `region` has a clip extent and screen `point` lies outside it.

    d3's clipExtent drops such a point; build_zip_centroids.project_point and
    map-layers.js projectFrame do the same.
    """
    clip = REGIONS[region].get('clip')
    return clip is None or (clip[0] <= point[0] <= clip[2] and clip[1] <= point[1] <= clip[3])


def clip_ring(ring, box):
    """The part of one screen-space ring inside `box` (x0, y0, x1, y1), as d3's clipExtent keeps it.

    A ring wholly inside comes back as it is (the same list, untouched); a ring whose bounds
    miss the box comes back empty; a ring crossing an edge is cut to the box (Sutherland-Hodgman,
    exact against a rectangle) and comes back empty when fewer than three points are left.
    """
    x0, y0, x1, y1 = box
    if all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in ring):
        return ring
    xs = [x for x, _y in ring]
    ys = [y for _x, y in ring]
    if max(xs) < x0 or min(xs) > x1 or max(ys) < y0 or min(ys) > y1:
        return []
    points = list(ring)
    for axis, limit, keep_above in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
        kept = []
        for index, point in enumerate(points):
            before = points[index - 1]
            here_in = point[axis] >= limit if keep_above else point[axis] <= limit
            before_in = before[axis] >= limit if keep_above else before[axis] <= limit
            if here_in != before_in:
                share = (limit - before[axis]) / (point[axis] - before[axis])
                cut = [before[0] + share * (point[0] - before[0]),
                       before[1] + share * (point[1] - before[1])]
                cut[axis] = limit
                kept.append((cut[0], cut[1]))
            if here_in:
                kept.append(point)
        points = kept
        if not points:
            return []
    return points if len(points) >= 3 else []


def project_rings(rings, statefp, placements):
    """Screen-space rings for one feature, clipped to its region's extent, simplified, rounded."""
    out, name, _clipped = project_rings_counted(rings, statefp, placements)
    return out, name


def project_rings_counted(rings, statefp, placements):
    """(rings, region, rings its region's clip removed whole): project_rings, counted.

    Only a region with a ``clip`` extent (Hawaii) removes anything; every ring of every
    other region takes exactly the arithmetic it took before the clip existed.
    """
    name = region_of(statefp)
    if name not in placements:
        return [], name, 0
    scale, tx, ty = placements[name]
    projection = projector(name)
    clip = REGIONS[name].get('clip')
    out, clipped = [], 0
    for ring in rings:
        screen = []
        for longitude, latitude in ring:
            x, y = projection(longitude, latitude)
            screen.append((tx + scale * x, ty - scale * y))
        if clip is not None:
            screen = clip_ring(screen, clip)
            if not screen:
                clipped += 1
                continue
        simplified = simplify(screen)
        if len(simplified) >= 3:
            out.append(simplified)
    return out, name, clipped


def simplify(ring):
    """Drop points closer than MIN_STEP to the last kept one, then round.

    Cheap on purpose: at 20m source resolution on a 975 px canvas the source
    already carries sub-pixel detail, so a nearest-neighbour filter removes the
    invisible points without moving any visible vertex more than MIN_STEP. The
    ring is closed explicitly afterwards; a ring that collapses below three
    distinct points is dropped by the caller.
    """
    kept = []
    for point in ring:
        if not kept or math.hypot(point[0] - kept[-1][0], point[1] - kept[-1][1]) >= MIN_STEP:
            kept.append(point)
    rounded, seen = [], None
    for x, y in kept:
        point = (round(x, DECIMALS), round(y, DECIMALS))
        if point != seen:
            rounded.append(point)
            seen = point
    if len(rounded) >= 3 and rounded[0] != rounded[-1]:
        rounded.append(rounded[0])
    return rounded


def to_path(rings):
    """SVG path data; holes keep their source winding so fill-rule punches them."""
    pieces = []
    for ring in rings:
        body = ' '.join(f'{x:g} {y:g}' for x, y in ring[1:-1] or ring[1:])
        pieces.append(f'M{ring[0][0]:g} {ring[0][1]:g} L{body} Z' if body
                      else f'M{ring[0][0]:g} {ring[0][1]:g} Z')
    return ' '.join(pieces)


def ring_area_centroid(ring):
    """(signed area, cx, cy) of a closed ring, shoelace."""
    area = cx = cy = 0.0
    for index in range(len(ring) - 1):
        x0, y0 = ring[index]
        x1, y1 = ring[index + 1]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area /= 2.0
    if abs(area) < 1e-9:
        xs = [point[0] for point in ring]
        ys = [point[1] for point in ring]
        return 0.0, sum(xs) / len(xs), sum(ys) / len(ys)
    return area, cx / (6 * area), cy / (6 * area)


def point_in_rings(rings, point):
    """Even-odd ray cast over every ring, so holes read as outside."""
    x, y = point
    crossings = 0
    for ring in rings:
        for index in range(len(ring) - 1):
            x0, y0 = ring[index]
            x1, y1 = ring[index + 1]
            if (y0 > y) != (y1 > y) and x0 + (y - y0) * (x1 - x0) / (y1 - y0) > x:
                crossings += 1
    return crossings % 2 == 1


def edge_distance(rings, point):
    """Shortest distance from `point` to any ring segment."""
    x, y = point
    best = float('inf')
    for ring in rings:
        for index in range(len(ring) - 1):
            x0, y0 = ring[index]
            x1, y1 = ring[index + 1]
            dx, dy = x1 - x0, y1 - y0
            span = dx * dx + dy * dy
            t = 0.0 if span == 0 else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / span))
            best = min(best, math.hypot(x - (x0 + t * dx), y - (y0 + t * dy)))
    return best


def visual_center(rings, bounds):
    """The interior point furthest from any edge, by coarse grid then refinement.

    A polylabel in eight lines. Needed because a congressional district is
    routinely a crescent: the area centroid of IL-07, MD-06 or NJ-06 lands in
    the NEIGHBOURING district, which would put the map's label and its
    hover target on the wrong seat.
    """
    steps = 16
    window = bounds
    best, best_distance = None, -1.0
    for _pass in range(3):                     # coarse sweep, then two refinements
        lo_x, lo_y, hi_x, hi_y = window
        for row in range(1, steps):
            for column in range(1, steps):
                point = (lo_x + (hi_x - lo_x) * column / steps,
                         lo_y + (hi_y - lo_y) * row / steps)
                if not point_in_rings(rings, point):
                    continue
                distance = edge_distance(rings, point)
                if distance > best_distance:
                    best, best_distance = point, distance
        if best is None:
            return None
        half_x, half_y = (hi_x - lo_x) / steps, (hi_y - lo_y) / steps
        window = (best[0] - half_x, best[1] - half_y, best[0] + half_x, best[1] + half_y)
    return best


def centroid_of(rings):
    """A label point INSIDE the district, not merely its average.

    First choice is the area centroid of the largest ring, which is both exact
    and inside for a compact district and costs nothing. When that point falls
    outside the shape (a crescent, or a district wrapped round a city), the
    grid search takes over. Returns None only when neither can find a point,
    which the caller reports rather than papering over.
    """
    if not rings:
        return None, 'none'
    best = None
    for ring in rings:
        area, cx, cy = ring_area_centroid(ring)
        if best is None or abs(area) > abs(best[0]):
            best = (area, cx, cy)
    point, basis = (best[1], best[2]), 'area'
    if not point_in_rings(rings, point):
        point, basis = visual_center(rings, ring_bounds(rings)), 'grid'
        if point is None:
            return None, 'none'
    # A district can be thinner than the rounding step (NJ-06 on the 116th map
    # is), and then rounding the label point to one decimal moves it outside
    # the shape. Keep two decimals for those rather than ship a label in the
    # neighbouring seat; the extra bytes are a handful.
    rounded = [round(point[0], DECIMALS), round(point[1], DECIMALS)]
    if not point_in_rings(rings, tuple(rounded)):
        rounded = [round(point[0], DECIMALS + 1), round(point[1], DECIMALS + 1)]
    return rounded, basis


def bbox_of(rings):
    bounds = ring_bounds(rings)
    return [round(value, DECIMALS) for value in bounds] if bounds else None


# ---------------------------------------------------------------------- build

def district_key(stusps, cdfp):
    """"<ST>-NN", or "<ST>-AL" for an at-large state, DC or a delegate seat.

    Census writes CD..FP "00" for a state with one at-large seat and "98" for a
    delegate seat (DC and the territories). Both mean one seat for the whole
    place, so both read AL. "ZZ" marks water/unassigned area and has no seat.
    """
    code = (cdfp or '').strip().upper()
    if code in ('00', '98'):
        return f'{stusps}-AL'
    if code in ('', 'ZZ'):
        return None
    if code.isdigit():
        return f'{stusps}-{int(code):02d}'
    return None


def district_name(row, key):
    """The Census label, or one built from the key when the vintage omits it.

    The 2018-2021 cartographic district files carry no NAMELSAD field (it
    arrives with the 2022 vintage), so cd116 would otherwise ship nameless.
    """
    name = (row.get('NAMELSAD') or '').strip()
    if name:
        return name
    number = key.rsplit('-', 1)[-1]
    return ('Delegate District (at Large)' if number == 'AL'
            else f'Congressional District {int(number)}')


def cd_field(row):
    for name in row:
        if re.fullmatch(r'CD\d+FP', name):
            return name
    raise ValueError(f'no CD<n>FP field in the district dbf (fields: {sorted(row)})')


def build_states(zip_path, url, log=print):
    """(states payload, STATEFP -> STUSPS, features for the placement fit)."""
    features = read_shapefile(zip_path)
    codes = {row['STATEFP']: row['STUSPS'] for row, _ in features}
    names = {row['STUSPS']: row.get('NAME', '') for row, _ in features}
    undrawn = {}
    for fips, (stusps, name) in TERRITORY_FALLBACK.items():
        if fips not in codes:
            codes[fips], names[stusps] = stusps, name
            undrawn[f'{stusps}-AL'] = {
                'stusps': stusps, 'name': name, 'statefp': fips,
                'reason': 'no boundary in the 20m cartographic files; its ZIPs still '
                          'resolve to this key so the amount can be listed, not lost'}
    placements = build_placements([(row['STATEFP'], rings) for row, rings in features], log=log)
    states, unplaced = {}, []
    for row, rings in features:
        projected, region = project_rings(rings, row['STATEFP'], placements)
        if not projected:
            unplaced.append({'statefp': row['STATEFP'], 'stusps': row['STUSPS'],
                             'reason': f'no placement for region {region}'})
            continue
        states[row['STUSPS']] = {'name': row.get('NAME', ''), 'statefp': row['STATEFP'],
                                 'path': to_path(projected), 'bbox': bbox_of(projected),
                                 'region': region}
    log(f'  states: {len(states)} placed, {len(unplaced)} unplaced, '
        f'{len(undrawn)} territories with no 20m boundary')
    return states, codes, names, placements, unplaced, url, undrawn


def projection_payload(placements):
    """The exact constants the page needs to project one point onto this canvas.

    Shipped in every vintage file because the map has to place things that are
    not districts: a geocoded contributor address arrives as longitude and
    latitude, and without these numbers the client would have to re-derive the
    fit from the paths. Read the region for a point from its STATEFP (or from
    which region's states contain it), then apply the formula below.
    """
    regions = {}
    for name, (scale, tx, ty) in sorted(placements.items()):
        region = REGIONS[name]
        regions[name] = {'parallels': list(region['parallels']),
                         'meridian': region['meridian'], 'origin': region['origin'],
                         'scale': round(scale, 6),
                         'translate': [round(tx, 6), round(ty, 6)],
                         'statefp': sorted(region['fips']) if region['fips'] else None}
        if region.get('clip'):
            regions[name]['clip'] = list(region['clip'])
    return {'kind': 'albers conic equal area, one conic per region',
            'formula': ('n = (sin f1 + sin f2)/2; C = cos^2 f1 + 2 n sin f1; '
                        'p = sqrt(C - 2 n sin f)/n; p0 = sqrt(C - 2 n sin f0)/n; '
                        't = n (l - l0), l - l0 wrapped into -180..180 degrees; '
                        'x = p sin t; y = p0 - p cos t; '
                        'screen = [translate[0] + scale*x, translate[1] - scale*y]. '
                        'f1/f2 = parallels, f0 = origin, l0 = meridian, all in degrees, '
                        'radians inside. The lower48 region takes every STATEFP that no '
                        'other region lists. A region with a clip [x0, y0, x1, y1] (screen '
                        "pixels) draws only inside it, as d3's clipExtent: a point outside it "
                        'is not drawn and a ring is cut to it.'),
            'regions': regions}


def build_vintage(vintage, cache_dir, out_dir, state_bundle, refresh=False, log=print):
    url, cycle = SOURCES[vintage]
    log(f'vintage {vintage} ({url.rsplit("/", 1)[-1]})')
    zip_path = fetch(url, cache_dir, refresh, log)
    states, codes, _names, placements, state_unplaced, state_url, undrawn = state_bundle
    features = read_shapefile(zip_path)
    districts, unplaced, skipped, labels = {}, list(state_unplaced), [], {}
    field = cd_field(features[0][0])
    for row, rings in features:
        statefp = row['STATEFP']
        stusps = codes.get(statefp)
        if stusps is None:
            skipped.append({'statefp': statefp, 'geoid': row.get('GEOID', ''),
                            'reason': 'STATEFP not in the state shapefile'})
            continue
        key = district_key(stusps, row.get(field))
        if key is None:
            skipped.append({'statefp': statefp, 'geoid': row.get('GEOID', ''),
                            'cdfp': row.get(field), 'reason': 'no seat (water/unassigned)'})
            continue
        projected, region = project_rings(rings, statefp, placements)
        if not projected:
            unplaced.append({'key': key, 'geoid': row.get('GEOID', ''),
                             'reason': f'no placement for region {region}'})
            continue
        centroid, basis = centroid_of(projected)
        labels.setdefault(basis, []).append(key)
        districts[key] = {'geoid': row.get('GEOID', ''), 'name': district_name(row, key),
                          'path': to_path(projected), 'bbox': bbox_of(projected),
                          'centroid': centroid, 'region': region}
    payload = {'vintage': vintage, 'source': url, 'state_source': state_url,
               'fetched_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
               'cycle': cycle, 'width': WIDTH, 'height': HEIGHT,
               'projection': projection_payload(placements),
               'districts': dict(sorted(districts.items())),
               'states': dict(sorted(states.items())),
               'undrawn': dict(sorted(undrawn.items())),
               'unplaced': unplaced, 'skipped': skipped,
               'build': {'projection': 'albers-usa-like (see build_district_geo.py)',
                         'min_step_px': MIN_STEP, 'decimals': DECIMALS,
                         'tool': 'mentat/tools/build_district_geo.py',
                         'path_rings': 'each ring ends with Z and does not repeat its first '
                                       'point; close the ring before any point-in-polygon test',
                         'equal_area_check': 'projected px^2 / true m^2 lands within 0.1% of '
                                             'scale^2/R^2 for the large low-water states '
                                             '(NM +0.02%, KS -0.03%, CO -0.09%), which is the '
                                             'equal-area property holding; small and '
                                             'water-heavy states differ by the 20m source '
                                             'generalisation, not by the projection',
                         'centroid_basis': {basis: len(keys) for basis, keys
                                            in sorted(labels.items())},
                         'centroid_grid_searched': sorted(labels.get('grid', [])),
                         'centroid_missing': sorted(labels.get('none', []))}}
    target = out_dir / f'cd{vintage}.json'
    write_json(target, payload)
    log(f'  wrote {target.name}: {len(districts)} districts, {len(states)} states, '
        f'{target.stat().st_size:,} bytes, {len(skipped)} no-seat rows, '
        f'{len(unplaced)} unplaced')
    return payload, target


def build_zcta(vintage, cache_dir, out_dir, codes, refresh=False, log=print):
    """ZCTA -> [(district key, land-area share)] for one vintage."""
    url = ZCTA_SOURCES.get(vintage)
    if not url:
        log(f'  zcta {vintage}: no Census relationship file exists; skipped')
        return None, None
    path = fetch(url, cache_dir, refresh, log)
    parts, rows, dropped_rows = {}, 0, 0
    with path.open(encoding='utf-8-sig', errors='replace') as handle:
        header = handle.readline().rstrip('\n').split('|')
        try:
            cd_at = next(index for index, name in enumerate(header)
                         if re.fullmatch(r'GEOID_CD\d+_20', name))
            zcta_at = header.index('GEOID_ZCTA5_20')
            land_at = header.index('AREALAND_PART')
        except (StopIteration, ValueError):
            raise ValueError(f'{path.name}: unexpected header {header}')
        for line in handle:
            fields = line.rstrip('\n').split('|')
            if len(fields) <= max(cd_at, zcta_at, land_at):
                continue
            zcta, geoid = fields[zcta_at].strip(), fields[cd_at].strip()
            if not zcta or not geoid or len(geoid) < 3:
                dropped_rows += 1          # the district's own total line, or a stray
                continue
            key = district_key(codes.get(geoid[:2], ''), geoid[2:])
            if key is None or key.startswith('-'):
                dropped_rows += 1
                continue
            try:
                land = float(fields[land_at] or 0)
            except ValueError:
                land = 0.0
            by_key = parts.setdefault(zcta, {})
            by_key[key] = by_key.get(key, 0.0) + land
            rows += 1
    crosswalk, water_only, split = {}, 0, 0
    for zcta, by_key in parts.items():
        total = sum(by_key.values())
        if total <= 0:                     # a ZCTA of pure water: split evenly, and say so
            water_only += 1
            shares = {key: 1.0 / len(by_key) for key in by_key}
        else:
            shares = {key: value / total for key, value in by_key.items()}
        kept = {key: value for key, value in shares.items() if value >= SHARE_FLOOR} or shares
        total = sum(kept.values())
        ordered = sorted(kept.items(), key=lambda item: (-item[1], item[0]))
        rounded = [[key, round(value / total, 3)] for key, value in ordered]
        residual = round(1.0 - sum(share for _, share in rounded), 3)
        if rounded and residual:
            rounded[0][1] = round(rounded[0][1] + residual, 3)
        crosswalk[zcta] = rounded
        split += len(rounded) > 1
    target = out_dir / f'zcta_cd{vintage}.json'
    write_json(target, dict(sorted(crosswalk.items())))
    log(f'  wrote {target.name}: {len(crosswalk):,} ZCTAs ({split:,} split across districts, '
        f'{water_only} water-only), {rows:,} source parts, {dropped_rows:,} non-ZCTA lines, '
        f'{target.stat().st_size:,} bytes')
    return {'zctas': len(crosswalk), 'split': split, 'source': url,
            'basis': 'land area (AREALAND_PART); the file carries no population column',
            'water_only': water_only, 'bytes': target.stat().st_size}, target


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(',', ':'), sort_keys=False) + '\n',
                    encoding='utf-8')


def write_index(out_dir, built, log=print):
    vintages = {}
    for vintage, info in sorted(built.items()):
        vintages[vintage] = {
            'file': f'cd{vintage}.json',
            'cycle': SOURCES[vintage][1],
            'congress': {'119': '119th', '118': '118th', '116': '116th (= 117th map)'}[vintage],
            'source': SOURCES[vintage][0],
            'districts': info['districts'],
            'zcta_file': info.get('zcta_file'),
            'zcta_source': info.get('zcta_source'),
            'zcta_basis': info.get('zcta_basis'),
        }
    payload = {
        'width': WIDTH, 'height': HEIGHT,
        'built_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'cycles': {str(SOURCES[vintage][1]): vintage for vintage in sorted(built)},
        'vintages': vintages,
        'notes': [
            'Cycle -> vintage follows S6_DESIGN ruling 9 (2026 -> 119th, 2024 -> 118th, '
            '2022 -> the 116th = 117th map).',
            'GENZ2022 carries no cd116 file (its congressional file is cd118); the 116th '
            'map comes from GENZ2021, the newest vintage that still publishes it.',
            'No ZCTA-to-CD relationship file exists for the 116th map: the 2020 ZCTAs were '
            'tabulated against the 118th and 119th only. district_for_zip returns nothing for '
            'cycle 2022 rather than colouring 116th-map districts with 118th-map ZIP shares.',
            'ZIP shares are land area, not population: the Census relationship file has no '
            'population column. Show them as approximate (ruling 8).',
            'OPEN QUESTION for Paul: the 2021-2022 FEC candidate master (cn22) already '
            'numbers House seats on the NEW 118th map (it carries NC-14, which the 116th map '
            'has no seat for), because 2022 candidates ran in the redistricted seats. Money '
            'in the 2022 cycle therefore maps to 118th districts even though the members '
            'sitting during 2021-2022 sat in 116th-map seats. Ruling 9 chose the 116th map; '
            'if the S8 map is about where the money went, cycle 2022 should point at vintage '
            '118 instead.',
        ],
    }
    target = out_dir / 'index.json'
    write_json(target, payload)
    log(f'  wrote {target.name}: {target.stat().st_size:,} bytes')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--only', action='append', choices=sorted(SOURCES),
                        help='build only this vintage (repeatable)')
    parser.add_argument('--no-zcta', action='store_true')
    parser.add_argument('--refresh', action='store_true', help='re-download cached files')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv)
    log = (lambda *_: None) if args.quiet else print

    started = time.time()
    args.out.mkdir(parents=True, exist_ok=True)
    log(f'out {args.out}')
    log(f'cache {args.cache}')
    log('states ' + STATE_SOURCE.rsplit('/', 1)[-1])
    try:
        state_zip = fetch(STATE_SOURCE, args.cache, args.refresh, log)
    except (urllib.error.URLError, OSError) as error:
        print(f'FAILED to fetch the state boundaries: {error}', file=sys.stderr)
        return 2
    state_bundle = build_states(state_zip, STATE_SOURCE, log=log)
    codes = state_bundle[1]

    built, failures = {}, []
    for vintage in (args.only or sorted(SOURCES, reverse=True)):
        try:
            payload, target = build_vintage(vintage, args.cache, args.out, state_bundle,
                                            args.refresh, log)
        except (urllib.error.URLError, OSError, ValueError) as error:
            failures.append(f'cd{vintage}: {type(error).__name__}: {error}')
            print(f'FAILED vintage {vintage}: {error}', file=sys.stderr)
            continue
        info = {'districts': len(payload['districts']), 'bytes': target.stat().st_size}
        if not args.no_zcta:
            try:
                zcta, zcta_target = build_zcta(vintage, args.cache, args.out, codes,
                                               args.refresh, log)
            except (urllib.error.URLError, OSError, ValueError) as error:
                failures.append(f'zcta_cd{vintage}: {type(error).__name__}: {error}')
                print(f'FAILED zcta {vintage}: {error}', file=sys.stderr)
                zcta, zcta_target = None, None
            if zcta:
                info.update(zcta_file=zcta_target.name, zcta_source=zcta['source'],
                            zcta_basis=zcta['basis'], zctas=zcta['zctas'],
                            zcta_bytes=zcta['bytes'])
        built[vintage] = info
    if built:
        write_index(args.out, built, log=log)
    log(f'done in {time.time() - started:.1f}s')
    for line in failures:
        print(f'UNFINISHED {line}', file=sys.stderr)
    return 1 if failures or not built else 0


if __name__ == '__main__':
    raise SystemExit(main())
