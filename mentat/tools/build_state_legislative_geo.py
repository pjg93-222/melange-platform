"""Build the state-legislative geography the P4a map filters by, per state.

Dev-time only, like build_district_geo.py: the console never runs this and
never reaches a network. It reads the two Census cartographic files already in
``mentat/.cache/geo/`` and writes, under ``mentat/app/geo/``:

    sld/upper/<ST>.json   one file per state, upper chamber (sldu)
    sld/lower/<ST>.json   one file per state, lower chamber (sldl)
    sld/index.json        which states each chamber has, counts and sizes
                          (LF sizes: the same on a CRLF checkout and an LF export)
    zcta_sld.json         ZIP -> (upper GEOID, lower GEOID)

    python mentat/tools/build_state_legislative_geo.py

ONE FILE PER STATE ON PURPOSE. All fifty-two upper chambers together are about
5 MB and the lower chambers about 7 MB, which is fifteen times cd119.json. The
map only ever draws one state's legislature at a time (you have picked a state
to get here), so the page fetches one 20-400 KB file instead of holding the
country. ``sld/index.json`` is the small file it can hold.

NOTHING IS RE-PROJECTED HERE. ``Albers``, ``REGIONS``, ``region_of``,
``project_rings_counted`` (Hawaii's clip included), ``simplify``,
``to_path``, ``bbox_of``, ``centroid_of``, ``point_in_rings`` and the
shapefile reader are all IMPORTED from build_district_geo, and the
placements come from build_zip_centroids'
``canvas_placements``, which recomputes them through build_district_geo's own
``build_states`` from the same state shapefile cd119.json was fitted with.
A state-house path therefore lands exactly on top of the congressional path
already drawn for the same ground.

WHAT THE CENSUS FILES ACTUALLY CONTAIN (read 2026-09-19, not assumed)

  cb_2024_us_sldu_500k.zip   1,960 records = 1,958 seats across 52
                             states/territories, plus 2 water records
  cb_2024_us_sldl_500k.zip   4,874 records = 4,873 seats across 50
                             states/territories, plus 1 water record

  * Three records are not seats: SLDUST/SLDLST "ZZZ", NAMELSAD "State Senate
    Districts not defined" - Louisiana upper, Wisconsin upper and Wisconsin
    lower. They are the offshore and Great-Lakes water those states' seats
    stop at, and they are skipped the way build_district_geo.district_key
    skips CD..FP "ZZ", which is why Louisiana ships 39 senate seats and
    Wisconsin 33 and 99, the numbers those states actually elect. No ZIP was
    assigned to one of them, so the crosswalk is unaffected either way.
  * Nebraska (STATEFP 31) has 49 upper districts and NO lower record at all -
    its legislature is unicameral, so no ``sld/lower/NE.json`` is written.
  * The District of Columbia (11) has 8 UPPER records - the Census files the
    DC Council's eight wards as the upper chamber - and no lower record. So DC
    does get ``sld/upper/DC.json`` and does not get a lower file. (The brief
    guessed DC would have neither; it has one.)
  * Puerto Rico (72) has both: 8 upper and 40 lower.
  * American Samoa, Guam, the Northern Marianas and the US Virgin Islands
    appear in neither file, the same absence build_district_geo records in
    ``undrawn``.

SIMPLIFICATION TOLERANCE

build_district_geo simplifies at ``MIN_STEP`` = 0.5 px, which is right for 437
congressional districts on a 975 px canvas. A state-house district is up to
two hundred times smaller: at 0.5 px, 34 lower-chamber districts (13 in Alaska,
7 in Vermont, 6 in Rhode Island, 3 each in Maine and New Hampshire, 1 each in
New York and Wyoming) and 6 upper-chamber districts collapse below three
distinct points and would be dropped entirely - a filter layer that silently
omits a district is worse than a large file. This tool therefore runs the same
``simplify`` at ``--min-step`` 0.1 px, which is the finest step the shipped
one-decimal precision can express and loses no district in either chamber.
The cost is measured, not guessed: the largest file is then Texas' lower
chamber at 384,933 bytes (LF), inside the 600 KB per-file budget, and the two
chambers together are 12.2 MB. ``--min-step 0.25`` cuts that to 6.7 MB and
drops 8 Alaska lower and 2 Alaska upper districts; 0.5 cuts it to 4.1 MB and
drops 40 (34 lower, 6 upper).

THE ZIP ASSIGNMENT IS AN APPROXIMATION AND SAYS SO

``zcta_sld.json`` assigns each ZCTA to the one district containing its Census
internal point, by even-odd ray casting over every ring of the district
(holes included) in LATITUDE/LONGITUDE space - not in projected space, where
simplification has already moved vertices by up to a tenth of a pixel. A ZIP
that straddles two state-house districts is assigned to the one under its
internal point and nothing records the split, because the Census publishes no
ZCTA-to-SLD relationship file to split it by. That sentence is written into
both ``zcta_sld.json`` and ``sld/index.json`` under ``basis`` so the console
can show it rather than implying a precision the source does not have.

Three ways a ZIP ends with an empty slot, and the index separates them:
its state has no such chamber (Nebraska and DC in the lower chamber, and the
four territories in both); it has no ZCTA in the gazetteer at all (none do
today); or its internal point lies outside every district polygon in its
state - 38 ZCTAs in each chamber, listed by name under
``in_no_district_zctas``, nearly all barrier islands, spits and made ground
(the Outer Banks, Maine's islands, Fisher Island, Dauphin Island) where the
500k generalisation of the coast leaves the point just offshore.

``zcta_sld.json`` also carries the literal key ``"basis"`` alongside its
five-digit ZIP keys; a consumer iterating the object must skip any key that is
not five digits.
"""

from datetime import datetime, timezone
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_district_geo as builder                                # noqa: E402
import build_zip_centroids as points_tool                           # noqa: E402

MENTAT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = MENTAT / 'app' / 'geo'
DEFAULT_CACHE = MENTAT / '.cache' / 'geo'

GENZ = 'https://www2.census.gov/geo/tiger/GENZ2024/shp'
# chamber -> (cached zip name, source url, the district-number field)
CHAMBERS = {
    'upper': ('cb_2024_us_sldu_500k.zip', f'{GENZ}/cb_2024_us_sldu_500k.zip', 'SLDUST'),
    'lower': ('cb_2024_us_sldl_500k.zip', f'{GENZ}/cb_2024_us_sldl_500k.zip', 'SLDLST'),
}
VINTAGE = '2024'
DEFAULT_MIN_STEP = 0.1
BASIS = ('ZCTA internal point in district polygon; a ZIP that straddles districts is '
         'assigned to the one containing its internal point')


def lf_size(path):
    """The file's size with LF line endings: the bytes git stores for it.

    build_district_geo.write_json writes text, which a Windows checkout ends
    with CRLF, and a clean LF export (git archive, eol=lf) one byte shorter per
    line. sld/index.json records this size, so it describes the file the same
    way on both (lane F2c, 2026-09-26; before, it recorded the on-disk CRLF
    size and test_state_legislative_geo failed on every LF export).
    """
    return len(Path(path).read_bytes().replace(b'\r\n', b'\n'))


def closed(rings):
    """Rings with the first point repeated last, which point_in_rings needs.

    build_district_geo's own warning, in cd119.json's ``build.path_rings``:
    "close the ring before any point-in-polygon test". Shapefile rings are
    closed by the format's own rule, but a source that broke that rule would
    otherwise lose one edge per ring and mis-assign the ZIPs near it, silently.
    """
    out = []
    for ring in rings:
        if len(ring) < 3:
            continue
        out.append(list(ring) if ring[0] == ring[-1] else list(ring) + [ring[0]])
    return out


def has_seat(code):
    """False for the Census water/unassigned district code, as district_key is.

    build_district_geo.district_key rejects CD..FP "ZZ" as "water/unassigned".
    The state-legislative files use the three-character form: SLDUST "ZZZ",
    NAMELSAD "State Senate Districts not defined". Three such records exist
    (Louisiana upper, Wisconsin upper, Wisconsin lower) and each is the
    offshore or Great-Lakes water its state's seats stop at. Shipping them
    would put "Districts not defined" in a filter list beside real seats.
    """
    code = (code or '').strip().upper()
    return bool(code) and set(code) != {'Z'}


def drop_offcanvas(rings):
    """(rings the canvas can hold, count dropped).

    The guard for a ring that projects wholly off the canvas. Since lane L3
    (2026-09-26) no district needs it; the two that did are Hawaiian. The 500k
    state-legislative file carries the Northwestern Hawaiian Islands -
    Papahanaumokuakea, out to Kure Atoll at 178.3W - inside Senate District 25
    and House District 51, as 15 separate rings in each (each district's other
    two rings are on Oahu; counted 2026-09-26). The 20m congressional
    file does not: cd119.json's HI-02 bbox stops at x=258, the west edge of the
    Hawaii inset box, because the hawaii placement was fitted to the main
    islands alone. Projected with that placement, an atoll 1,300 miles west
    lands at x=-43, off the canvas, and the nearer ones inside the Alaska box.

    Re-fitting the Hawaii inset to hold them would move every Hawaiian pixel
    and break the one thing this tool must not break - that its paths sit
    exactly on cd119.json's. build_district_geo clips the Hawaii inset to its
    extent instead, as d3's clipExtent does, so project_rings_counted removes
    those rings first and the index counts them under clipped_rings; whatever
    the canvas still cannot hold is dropped here and counted too.
    """
    kept, dropped = [], 0
    for ring in rings:
        if any(0 <= x <= builder.WIDTH and 0 <= y <= builder.HEIGHT for x, y in ring):
            kept.append(ring)
        else:
            dropped += 1
    return kept, dropped


def read_chamber(cache_dir, chamber, log=print):
    """({STATEFP: [(geoid, namelsad, raw rings)]}, url, no-seat rows)."""
    name, url, field = CHAMBERS[chamber]
    path = Path(cache_dir) / name
    if not path.is_file():
        raise FileNotFoundError(
            f'{path} is missing. Download {url} into {cache_dir} first; this tool '
            f'never reaches the network.')
    log(f'{chamber}: {path.name} ({path.stat().st_size:,} bytes)')
    features = builder.read_shapefile(path)
    by_state, seen, no_seat = {}, set(), []
    for row, rings in features:
        geoid = (row.get('GEOID') or '').strip()
        if not geoid or geoid in seen:
            continue
        seen.add(geoid)
        if not has_seat(row.get(field)):
            no_seat.append({'geoid': geoid, 'statefp': row['STATEFP'],
                            'name': (row.get('NAMELSAD') or '').strip(),
                            'reason': 'no seat (water/unassigned)'})
            continue
        by_state.setdefault(row['STATEFP'], []).append(
            (geoid, (row.get('NAMELSAD') or '').strip(), rings))
    log(f'  {len(features):,} records, {len(seen):,} distinct GEOIDs, '
        f'{len(no_seat)} with no seat, {len(by_state)} states/territories')
    return by_state, url, no_seat


def build_state_file(chamber, stusps, statefp, entries, placements, out_dir):
    """Write one sld/<chamber>/<ST>.json. Returns (payload, path, collapsed)."""
    region = builder.region_of(statefp)
    districts, collapsed, offcanvas, clipped = [], [], {}, {}
    anchor_fallback, subpixel = [], []
    for geoid, namelsad, rings in entries:
        projected, _region, removed = builder.project_rings_counted(rings, statefp, placements)
        if removed:
            clipped[geoid] = removed
        projected, dropped = drop_offcanvas(projected)
        if dropped:
            offcanvas[geoid] = dropped
        if not projected:
            collapsed.append(geoid)
            continue
        box = builder.bbox_of(projected)
        anchor, _basis = builder.centroid_of(projected)
        if anchor is None:
            # centroid_of hands back None when neither the area centroid nor
            # the grid search finds an interior point, which happens to a
            # district thinner than the grid: Alaska House 22 is downtown
            # Anchorage, 0.1 x 0.1 px inside an inset capped at 0.35x. The
            # box centre IS the shape at that size, and a district with no
            # anchor at all would have nothing to hang a label or a hit
            # target on, so it is used and the district is named.
            anchor = [round((box[0] + box[2]) / 2, builder.DECIMALS),
                      round((box[1] + box[3]) / 2, builder.DECIMALS)]
            anchor_fallback.append(geoid)
        if box[2] - box[0] < 1.0 and box[3] - box[1] < 1.0:
            subpixel.append(geoid)
        districts.append({'id': geoid, 'name': namelsad,
                          'path': builder.to_path(projected),
                          'bbox': box, 'anchor': anchor})
    districts.sort(key=lambda entry: entry['id'])
    payload = {'chamber': chamber, 'state': stusps, 'vintage': VINTAGE,
               'region': region, 'districts': districts}
    target = out_dir / 'sld' / chamber / f'{stusps}.json'
    builder.write_json(target, payload)
    return payload, target, {'collapsed': collapsed, 'offcanvas': offcanvas, 'clipped': clipped,
                             'anchor_fallback': anchor_fallback, 'subpixel': subpixel}


def assign_zctas(chamber, by_state, fips_of, zcta_rows, zcta_state, log=print):
    """{zcta5: GEOID} by point-in-polygon of the internal point, in lon/lat.

    A bounding-box prefilter on the raw (unprojected) rings does the work: a
    ZIP is tested against only the handful of districts whose box contains its
    point, so the even-odd ray cast runs a couple of times per ZIP instead of
    once per district in the state.
    """
    boxes = {}
    for statefp, entries in by_state.items():
        prepared = []
        for geoid, _name, rings in entries:
            ring_list = closed(rings)
            bounds = builder.ring_bounds(ring_list)
            if bounds:
                prepared.append((geoid, ring_list, bounds))
        boxes[statefp] = prepared
    assigned, no_state, no_district = {}, 0, []
    for zcta, latitude, longitude in zcta_rows:
        # fips_of is STUSPS -> STATEFP; the crosswalk gives a postal code and
        # the shapefile groups by STATEFP, so the map has to run that way.
        statefp = fips_of.get(zcta_state.get(zcta, ''))
        prepared = boxes.get(statefp) if statefp else None
        if not prepared:
            no_state += 1
            continue
        hit = ''
        for geoid, ring_list, (x0, y0, x1, y1) in prepared:
            if not (x0 <= longitude <= x1 and y0 <= latitude <= y1):
                continue
            if builder.point_in_rings(ring_list, (longitude, latitude)):
                hit = geoid
                break
        if hit:
            assigned[zcta] = hit
        else:
            no_district.append(zcta)
    log(f'  {chamber} assignment: {len(assigned):,} ZCTAs placed, '
        f'{no_state:,} in a state this chamber has no districts for, '
        f'{len(no_district):,} inside no district polygon')
    return assigned, {
        'placed': len(assigned), 'state_has_no_chamber': no_state,
        'in_no_district': len(no_district),
        # Named, not just counted: these are almost all barrier islands, spits
        # and made ground whose internal point falls just outside the 500k
        # cartographic generalisation of the district that contains them, and
        # a console that shows nothing for them should be able to say why.
        'in_no_district_zctas': sorted(no_district),
        'in_no_district_reason': 'the ZCTA internal point lies outside every district '
                                 'polygon in its state at 500k cartographic generalisation; '
                                 'nearly all are islands, spits or made ground on a '
                                 'simplified coastline'}


def build(cache_dir, out_dir, min_step=DEFAULT_MIN_STEP, log=print):
    cache_dir, out_dir = Path(cache_dir), Path(out_dir)
    placements, codes, fips_of = points_tool.canvas_placements(cache_dir, log=log)

    gazetteer = cache_dir / points_tool.GAZETTEER
    zcta_rows, malformed = points_tool.read_gazetteer(gazetteer)
    zcta_state = points_tool.zcta_states(out_dir)
    log(f'gazetteer: {len(zcta_rows):,} ZCTAs ({malformed} malformed lines skipped), '
        f'{len(zcta_state):,} with a crosswalk state')

    previous_step = builder.MIN_STEP
    builder.MIN_STEP = min_step
    chambers, assignments = {}, {}
    try:
        for chamber in CHAMBERS:
            by_state, url, no_seat = read_chamber(cache_dir, chamber, log=log)
            states, total_bytes, total_districts = {}, 0, 0
            collapsed_all, offcanvas_all, clipped_all = {}, {}, {}
            anchor_fallback_all, subpixel_all = [], {}
            for statefp, entries in sorted(by_state.items()):
                stusps = codes.get(statefp)
                if stusps is None:
                    log(f'  WARNING statefp {statefp} is in no state shapefile; skipped')
                    continue
                payload, target, notes = build_state_file(
                    chamber, stusps, statefp, entries, placements, out_dir)
                size = lf_size(target)
                states[stusps] = {'file': f'sld/{chamber}/{stusps}.json',
                                  'districts': len(payload['districts']),
                                  'bytes': size, 'region': payload['region'],
                                  'statefp': statefp,
                                  'collapsed': len(notes['collapsed']),
                                  'subpixel': len(notes['subpixel'])}
                if notes['collapsed']:
                    collapsed_all[stusps] = notes['collapsed']
                if notes['offcanvas']:
                    offcanvas_all.update(notes['offcanvas'])
                if notes['clipped']:
                    clipped_all.update(notes['clipped'])
                anchor_fallback_all.extend(notes['anchor_fallback'])
                if notes['subpixel']:
                    subpixel_all[stusps] = notes['subpixel']
                total_bytes += size
                total_districts += len(payload['districts'])
            largest = max(states.items(), key=lambda item: item[1]['bytes'], default=None)
            assigned, stats = assign_zctas(chamber, by_state, fips_of, zcta_rows,
                                           zcta_state, log=log)
            assignments[chamber] = assigned
            chambers[chamber] = {
                'source': url, 'files': len(states), 'districts': total_districts,
                'bytes': total_bytes,
                'largest_file': {'state': largest[0], 'bytes': largest[1]['bytes'],
                                 'districts': largest[1]['districts']} if largest else None,
                'collapsed': collapsed_all,
                'no_seat': no_seat,
                'offcanvas_rings': dict(sorted(offcanvas_all.items())),
                'offcanvas_reason': 'rings that project entirely off the 975x610 canvas are '
                                    'dropped and counted here; since the Hawaii clip there '
                                    'are none (see clipped_rings)',
                'clipped_rings': dict(sorted(clipped_all.items())),
                'clipped_reason': "rings removed whole by the Hawaii inset's clip extent "
                                  "(build_district_geo REGIONS['hawaii']['clip'], as d3's "
                                  'geoAlbersUsa clips each inset to its clipExtent): the '
                                  'Northwestern Hawaiian Islands, which the 500k '
                                  'state-legislative file carries and the 20m congressional '
                                  'file does not. The Hawaii conic drew them west of the '
                                  'inset, the nearer ones inside the Alaska box and the '
                                  'farther ones off the canvas',
                'anchor_fallback': sorted(anchor_fallback_all),
                'subpixel': dict(sorted(subpixel_all.items())),
                'subpixel_reason': 'a district whose whole projected box is under a pixel in '
                                   'both directions: it is in the file and clickable, and '
                                   'only a zoom makes it readable. 29 of 1,958 upper and '
                                   '264 of 4,873 lower seats are, and they are the dense '
                                   'cities - Manhattan, Providence, Hartford, the DC wards '
                                   '- plus Anchorage, whose inset is capped at 0.35x the '
                                   'lower-48 scale. A city inset would be the fix and is a '
                                   "separate decision, not this tool's to make",
                'zcta_assignment': stats,
                'states': dict(sorted(states.items())),
            }
            biggest = (f'largest {largest[0]} {largest[1]["bytes"]:,} bytes '
                       f'({largest[1]["districts"]} districts)') if largest else 'no files'
            log(f'  {chamber}: {len(states)} files, {total_districts:,} districts, '
                f'{total_bytes:,} bytes, {biggest}, '
                f'{sum(len(v) for v in collapsed_all.values())} districts collapsed, '
                f'{sum(offcanvas_all.values())} off-canvas rings dropped from '
                f'{len(offcanvas_all)} districts, '
                f'{sum(clipped_all.values())} rings clipped from '
                f'{len(clipped_all)} districts, '
                f'{sum(len(v) for v in subpixel_all.values())} sub-pixel districts '
                f'({len(anchor_fallback_all)} needed a box-centre anchor)')
            del by_state
    finally:
        builder.MIN_STEP = previous_step

    index = {
        'vintage': VINTAGE,
        'canvas': [builder.WIDTH, builder.HEIGHT],
        'basis': BASIS,
        'built_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'tool': 'mentat/tools/build_state_legislative_geo.py',
        'min_step_px': min_step,
        'decimals': builder.DECIMALS,
        'notes': [
            'Nebraska is unicameral: 49 upper districts and no lower file.',
            'The Census files the eight DC Council wards as DC upper districts; DC has '
            'no lower file.',
            'American Samoa, Guam, the Northern Marianas and the US Virgin Islands are in '
            'neither Census file, the same absence cd119.json records under undrawn.',
            'Paths are in the same canvas and precision as cd119.json, projected with '
            'build_district_geo.py imported, not copied.',
            'Hawaii Senate District 25 and House District 51 reach the Northwestern '
            'Hawaiian Islands, which the Hawaii inset was never fitted to hold (the 20m '
            "congressional file, and so cd119.json's HI-02, has the main islands only); "
            'the Hawaii clip removes those rings and counts them under clipped_rings.',
        ],
        'chambers': chambers,
    }
    index_target = out_dir / 'sld' / 'index.json'
    builder.write_json(index_target, index)
    log(f'  wrote {index_target.parent.name}/{index_target.name}: '
        f'{index_target.stat().st_size:,} bytes')

    table = {'basis': BASIS}
    for zcta, _lat, _lon in zcta_rows:
        upper = assignments.get('upper', {}).get(zcta, '')
        lower = assignments.get('lower', {}).get(zcta, '')
        table[zcta] = [upper, lower]
    zcta_target = out_dir / 'zcta_sld.json'
    builder.write_json(zcta_target, table)
    log(f'  wrote {zcta_target.name}: {len(zcta_rows):,} ZCTAs, '
        f'{zcta_target.stat().st_size:,} bytes')
    return index, index_target, zcta_target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--min-step', type=float, default=DEFAULT_MIN_STEP,
                        help='simplifier tolerance in canvas pixels (default 0.1)')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv)
    log = (lambda *_: None) if args.quiet else print
    started = time.time()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        build(args.cache, args.out, min_step=args.min_step, log=log)
    except (OSError, ValueError) as error:
        print(f'FAILED: {type(error).__name__}: {error}', file=sys.stderr)
        return 2
    log(f'done in {time.time() - started:.1f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
