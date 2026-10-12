"""Project every ZCTA's internal point onto the S8 map canvas.

Dev-time only, like build_district_geo.py: the console never runs this and
never reaches a network. It reads the Census ZCTA gazetteer already in
``mentat/.cache/geo/`` and writes ``mentat/app/geo/zcta_points.json``.

    python mentat/tools/build_zip_centroids.py

WHY THIS FILE EXISTS

``zcta_cd119.json`` answers "which district is this ZIP in" and carries no
geometry at all - no centroid, no latitude, no longitude - so a page holding it
still cannot draw a dot for a ZIP. The P4a map wants to place donor locations
and to let a reader filter by ZIP, and both need a point on the canvas. The
gazetteer has the point (INTPTLAT / INTPTLONG, the Census-published internal
point, which lies inside the ZCTA), and build_district_geo.py already owns the
projection that put every district path where it is. This script is the join,
and nothing more.

NOTHING IS RE-DERIVED HERE. The Albers class, the REGIONS table, the region
choice (``region_of``), the placement fit (``build_states`` / ``fit``) and the
writer are IMPORTED from build_district_geo, and the placements are recomputed
from the same state shapefile that produced ``cd119.json``. They come out
identical to the ``projection.regions`` block shipped inside cd119.json
(checked 2026-09-19: all four placed regions' scale and translate match to the
six decimals that file records), so a point written here lands in the same
pixel as the district path drawn under it.

CHOOSING THE REGION - exactly the way build_district_geo chooses it

A district feature picks its region from its STATEFP (``region_of``). A
gazetteer row has no STATEFP, so the state is read off the ZIP crosswalk
(``zcta_cd119.json``: the first two characters of the largest district part's
key are the postal code) and turned back into a STATEFP. When the crosswalk
has no answer, the fallback projects the point with each region's own conic
and keeps the region whose declared canvas box contains the result. Checked
2026-09-19: the gazetteer and the crosswalk carry exactly the same 33,791
ZCTAs, so the fallback fires zero times today; it exists so a future gazetteer
that runs ahead of the crosswalk degrades instead of crashing.

A REGION BOX IS A LAYOUT TARGET, NOT A BORDER

``REGIONS[name]['box']`` is the rectangle ``fit`` centres a region's geometry
inside. build_district_geo does not box-test its own features - project_rings
projects them and ships whatever comes out - so this tool does not either when
the state already named the region. The box test is the FALLBACK's tool, where
it does real work: it is what stops an unknown point being claimed by the
lower-48 conic.

One real ZIP proves the difference. ZCTA 98281 is Point Roberts, Washington,
the exclave hanging below Vancouver at 48.98N; the 20m state file generalises
it away, so the lower-48 fit never saw it, and it projects to (162.7, 5.3) -
0.74 px above the top of the lower-48 box, and plainly on the 975x610 canvas.
Dropping a Washington ZIP out of a filter whose whole point is that every ZIP
is selectable would be the worse error, so a point whose state named its
region is kept whenever it lands ON THE CANVAS, and the handful that fall
outside their region's box are listed by name under ``outside_region_box``.

Hawaii's CLIP EXTENT is the one border (build_district_geo
REGIONS['hawaii']['clip'], lane L3, 2026-09-26): as d3's clipExtent drops
it, a Hawaiian point outside it is not placed - project_point returns None,
the box fallback finds no region, and the ZCTA is omitted with its state.
No Hawaiian ZCTA lies outside it today.

WHAT IS ACTUALLY OMITTED (measured 2026-09-19, 12 of 33,791 ZCTAs)

  * 11 ZCTAs in American Samoa (1), Guam (7) and the Northern Marianas (3).
    Those territories have no 20m boundary in the Census cartographic files,
    so build_district_geo never fits a placement for their regions (see its
    TERRITORY_FALLBACK note). Their ZIPs keep a district key in the crosswalk
    but there is nowhere on this canvas to put them.
  * Nothing else. The US Virgin Islands were expected to be a second case,
    because VI has no 20m polygon either and shares Puerto Rico's conic; in
    fact all six VI ZCTAs land inside the caribbean box (x 930-934 of a box
    that runs to 963), because that box's fit is limited by its height rather
    than its width. Checked, not assumed.

Omissions are listed with their state, exactly as build_district_geo writes
``undrawn`` and ``unplaced`` rather than losing a row.

OUTPUT (mentat/app/geo/zcta_points.json)

    {"vintage":"2024","canvas":[975,610],"count":N,
     "points":{"<zip5>":[x, y, "<region key>", "<state postal>"]},
     "outside_region_box":{"<zip5>":"<region key>"},
     "omitted":{"<zip5>":"<state postal>"},"build":{...}}

x and y are canvas pixels rounded to one decimal, the precision cd119.json
writes its paths with (``build_district_geo.DECIMALS``).
"""

from datetime import datetime, timezone
import argparse
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_district_geo as builder                                # noqa: E402

MENTAT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = MENTAT / 'app' / 'geo'
DEFAULT_CACHE = MENTAT / '.cache' / 'geo'

GAZETTEER = '2024_Gaz_zcta_national.zip'
GAZETTEER_SOURCE = ('https://www2.census.gov/geo/docs/maps-data/data/gazetteer/'
                    '2024_Gazetteer/2024_Gaz_zcta_national.zip')
STATE_ZIP = builder.STATE_SOURCE.rsplit('/', 1)[-1]
VINTAGE = '2024'
CROSSWALK = 'zcta_cd119.json'

# The non-lower48 regions are tried first, the same precedence region_of uses:
# lower48 is the catch-all that claims every STATEFP no other region lists, so
# a box test that asked it first would swallow points belonging to an inset.
BOX_ORDER = [name for name in builder.REGIONS if name != 'lower48'] + ['lower48']
# A conic is only meaningful within a quadrant or so of its central meridian.
# Guam sits 240 degrees from the lower-48 meridian; without this guard its
# point could land inside the lower-48 box by coincidence and put a Pacific
# ZIP in Kansas. 90 degrees is generous for every region actually placed.
MAX_MERIDIAN_DELTA = 90.0


# ------------------------------------------------------------------- reading

def read_gazetteer(zip_path):
    """([(zcta5, latitude, longitude)], malformed count) from the gazetteer.

    One tab-delimited text file inside the zip. Every header name carries
    trailing spaces (``'INTPTLONG'`` plus 138 of them in the 2024 file) and so
    does the last field of every data row, so both are stripped.
    """
    with zipfile.ZipFile(zip_path) as archive:
        name = next(entry for entry in archive.namelist() if entry.lower().endswith('.txt'))
        text = archive.read(name).decode('utf-8-sig', 'replace')
    lines = text.splitlines()
    if not lines:
        raise ValueError(f'{zip_path.name}: empty gazetteer')
    header = [field.strip() for field in lines[0].split('\t')]
    try:
        geoid_at = header.index('GEOID')
        lat_at = header.index('INTPTLAT')
        lon_at = header.index('INTPTLONG')
    except ValueError:
        raise ValueError(f'{zip_path.name}: unexpected gazetteer header {header}')
    rows, malformed = [], 0
    for line in lines[1:]:
        fields = line.split('\t')
        if len(fields) <= max(geoid_at, lat_at, lon_at):
            malformed += 1
            continue
        zcta = fields[geoid_at].strip()
        try:
            latitude, longitude = float(fields[lat_at]), float(fields[lon_at])
        except ValueError:
            malformed += 1
            continue
        if len(zcta) == 5 and zcta.isdigit():
            rows.append((zcta, latitude, longitude))
        else:
            malformed += 1
    return rows, malformed


def zcta_states(geo_dir, crosswalk=CROSSWALK):
    """{zcta5: STUSPS} read off the shipped ZIP-to-district crosswalk.

    The crosswalk's value is [[district key, share], ...] largest share first,
    and a district key is "<ST>-<seat>", so the first two characters of the
    first part are the state the ZCTA mostly lies in - the same state a
    district feature would have carried in its STATEFP. Missing file is not an
    error: the caller then falls back to the box test for every ZCTA.
    """
    path = Path(geo_dir) / crosswalk
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    out = {}
    for zcta, parts in payload.items():
        if parts and isinstance(parts, list) and isinstance(parts[0], list) and parts[0]:
            key = str(parts[0][0])
            if len(key) >= 2:
                out[zcta] = key[:2]
    return out


def canvas_placements(cache_dir, log=print):
    """(placements, STATEFP->STUSPS, STUSPS->STATEFP) for the shipped canvas.

    Recomputed from the same state shapefile build_district_geo fits, through
    build_district_geo's own build_states, so these are the numbers cd119.json
    records under ``projection.regions`` and not a second fit that might drift.
    """
    state_zip = Path(cache_dir) / STATE_ZIP
    if not state_zip.is_file():
        raise FileNotFoundError(
            f'{state_zip} is missing. It is the file build_district_geo.py already '
            f'downloads; run that tool once, or put the file in {cache_dir}. This '
            f'tool never reaches the network.')
    _states, codes, _names, placements, _unplaced, _url, _undrawn = builder.build_states(
        state_zip, builder.STATE_SOURCE, log=log)
    fips_of = {stusps: fips for fips, stusps in codes.items()}
    return placements, codes, fips_of


# ---------------------------------------------------------------- projection

def project_point(longitude, latitude, region, placements):
    """Canvas pixels for one lon/lat in `region`, or None when it is unplaced.

    This is project_rings' screen transform for a single point: the same
    imported Albers conic and the same (scale, tx, ty) the paths were drawn
    with, so the dot lands on the shape. A point outside its region's clip
    extent (Hawaii's) is None too, as project_rings removes a ring there.
    """
    if region not in placements:
        return None
    scale, tx, ty = placements[region]
    x, y = builder.projector(region)(longitude, latitude)
    point = tx + scale * x, ty - scale * y
    return point if builder.in_clip(region, point) else None


def in_box(region, point):
    x0, y0, x1, y1 = builder.REGIONS[region]['box']
    return x0 <= point[0] <= x1 and y0 <= point[1] <= y1


def on_canvas(point):
    return 0 <= point[0] <= builder.WIDTH and 0 <= point[1] <= builder.HEIGHT


def meridian_delta(longitude, region):
    """Degrees from a region's central meridian, wrapped into -180..180."""
    return abs((longitude - builder.REGIONS[region]['meridian'] + 180.0) % 360.0 - 180.0)


def region_by_box(longitude, latitude, placements):
    """(region, point) for the region whose own conic puts this point in its box."""
    for region in BOX_ORDER:
        if region not in placements:
            continue
        if meridian_delta(longitude, region) > MAX_MERIDIAN_DELTA:
            continue
        point = project_point(longitude, latitude, region, placements)
        if point and in_box(region, point):
            return region, point
    return None, None


# --------------------------------------------------------------------- build

def build_points(rows, states, placements, fips_of):
    """(points, outside_box, omitted, stats). [x, y, region, state] per ZCTA.

    Three outcomes, in order:

      * the ZCTA's state names a region and the projected point lands in that
        region's layout box - the ordinary case, 33,778 of 33,791;
      * the state names a region and the point is on the canvas but outside
        that box - kept, and named in `outside_box` (Point Roberts, WA);
      * neither - the box fallback gets a turn, and a point no region box
        claims is omitted with its state rather than drawn somewhere it is not.
    """
    points, outside_box, omitted = {}, {}, {}
    from_state = from_box = 0
    for zcta, latitude, longitude in rows:
        stusps = states.get(zcta, '')
        statefp = fips_of.get(stusps)
        region = builder.region_of(statefp) if statefp else None
        point = project_point(longitude, latitude, region, placements) if region else None
        if point is not None and on_canvas(point):
            from_state += 1
            if not in_box(region, point):
                outside_box[zcta] = region
        else:
            # Either the crosswalk had no state for this ZCTA, or the state's
            # region has no placement (AS/GU/MP have no 20m boundary), or the
            # point projected clean off the canvas.
            region, point = region_by_box(longitude, latitude, placements)
            if region is None:
                omitted[zcta] = stusps
                continue
            from_box += 1
        points[zcta] = [round(point[0], builder.DECIMALS), round(point[1], builder.DECIMALS),
                        region, stusps]
    return points, outside_box, omitted, {
        'placed': len(points), 'from_state': from_state, 'from_box_fallback': from_box,
        'outside_region_box': len(outside_box), 'omitted': len(omitted)}


def build(cache_dir, out_dir, log=print):
    cache_dir, out_dir = Path(cache_dir), Path(out_dir)
    gazetteer = cache_dir / GAZETTEER
    if not gazetteer.is_file():
        raise FileNotFoundError(
            f'{gazetteer} is missing. Download {GAZETTEER_SOURCE} into {cache_dir} '
            f'first; this tool never reaches the network.')
    log(f'gazetteer {gazetteer.name} ({gazetteer.stat().st_size:,} bytes)')
    rows, malformed = read_gazetteer(gazetteer)
    log(f'  {len(rows):,} ZCTAs, {malformed} lines skipped as malformed')

    placements, _codes, fips_of = canvas_placements(cache_dir, log=log)
    states = zcta_states(out_dir)
    unknown = sum(1 for row in rows if row[0] not in states)
    log(f'  states from {CROSSWALK}: {len(states):,} ZCTAs '
        f'({unknown:,} have no crosswalk state and need the box fallback)')

    points, outside_box, omitted, stats = build_points(rows, states, placements, fips_of)
    payload = {
        'vintage': VINTAGE,
        'canvas': [builder.WIDTH, builder.HEIGHT],
        'count': len(points),
        'points': dict(sorted(points.items())),
        'outside_region_box': dict(sorted(outside_box.items())),
        'omitted': dict(sorted(omitted.items())),
        'build': {
            'tool': 'mentat/tools/build_zip_centroids.py',
            'built_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            'source': GAZETTEER_SOURCE,
            'state_source': builder.STATE_SOURCE,
            'point': 'the Census internal point (INTPTLAT/INTPTLONG), which lies inside '
                     'the ZCTA; it is not the population centroid',
            'projection': 'the projection build_district_geo.py drew cd119.json with; the '
                          "region is chosen from the ZCTA's state exactly as region_of "
                          'chooses it for a district feature',
            'region_basis': {'from_state': stats['from_state'],
                             'from_box_fallback': stats['from_box_fallback']},
            'outside_region_box_reason': "a region's box is the rectangle its geometry was "
                                         'fitted into, not a border; a point whose state '
                                         'named its region is kept whenever it lands on the '
                                         'canvas, and the few that land outside that box are '
                                         'named here (98281 is Point Roberts, WA, which the '
                                         '20m state file generalises away, so the fit never '
                                         'saw it)',
            'omitted_reason': 'a ZCTA that no region can place is omitted and listed here '
                              'rather than drawn somewhere it is not: AS, GU and MP have no '
                              '20m boundary, so build_district_geo fits no placement for '
                              'their regions and there is no pixel to give them',
            'decimals': builder.DECIMALS,
        },
    }
    target = out_dir / 'zcta_points.json'
    builder.write_json(target, payload)
    log(f'  wrote {target.name}: {stats["placed"]:,} points '
        f'({stats["from_state"]:,} by state, {stats["from_box_fallback"]:,} by box fallback), '
        f'{stats["outside_region_box"]} outside their region box, '
        f'{stats["omitted"]} omitted, {target.stat().st_size:,} bytes')
    if outside_box:
        log('  outside their region box (kept): '
            + ', '.join(f'{zcta}({region})' for zcta, region in sorted(outside_box.items())))
    if omitted:
        log('  omitted: ' + ', '.join(f'{zcta}({state or "?"})'
                                      for zcta, state in sorted(omitted.items())))
    return payload, target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv)
    log = (lambda *_: None) if args.quiet else print
    started = time.time()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        build(args.cache, args.out, log=log)
    except (OSError, ValueError) as error:
        print(f'FAILED: {type(error).__name__}: {error}', file=sys.stderr)
        return 2
    log(f'done in {time.time() - started:.1f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
