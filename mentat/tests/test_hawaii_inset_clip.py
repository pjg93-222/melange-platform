"""Hawaii draws only inside its clip extent, and nothing Hawaiian lands in the Alaska box.

Lane L3, 2026-09-26. d3's geoAlbersUsa clips each inset to its clipExtent; this frame did
not. The 500k layers (counties, state legislative districts, tracts, the dissolved state
outline) and the hexagon grids carry the Northwestern Hawaiian Islands, which the Hawaii
conic drew west of the Hawaii box: Nihoa to Lisianski on the canvas, inside the Alaska box
(French Frigate Shoals on the Kenai Peninsula at Alaska's corrected scale), the rest off the
canvas. build_district_geo now clips Hawaii to REGIONS['hawaii']['clip'], the canvas around
the Hawaii box one pixel clear of the Alaska, lower-48 and American Samoa boxes, and
build_state_legislative_geo, build_zip_centroids, build_geo_layers and map-layers.js apply
the same extent.

The clip is wider than the Hawaii layout box (258, 512, 352, 602) on purpose, and the last
test pins why: the 500k Big Island reaches x 352.14 (Cape Kumukahi), Kaula x 252.87, and the
hexagons over the main islands further; clipping to the layout box would have cut them.

This reads every shipped file that draws Hawaii - geo/cd*.json, geo/sld, geo/zcta_points.json
and the geo/v2 albers files - and checks every path vertex, anchor, centroid and box corner of
every Hawaiian feature. Plain Python, either interpreter. MENTAT_DIR points it at another
checkout; the lane ran it with the clipping tools over the pre-clip files, where it fails.

Run from inside mentat/tests:
    python -m unittest test_hawaii_inset_clip
"""
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MENTAT = Path(os.environ.get('MENTAT_DIR') or HERE.parent)
sys.path.insert(0, str(MENTAT / 'tools'))
import build_district_geo as builder                                # noqa: E402

GEO = MENTAT / 'app' / 'geo'
ALBERS = GEO / 'v2' / 'albers'
CLIP = builder.REGIONS['hawaii'].get('clip')
BOX = builder.REGIONS['hawaii']['box']
ALASKA = builder.REGIONS['alaska']['box']
# Every file that draws Hawaii; each must contribute at least one Hawaiian feature.
EXPECTED = (['cd116.json', 'cd118.json', 'cd119.json', 'sld/upper/HI.json', 'sld/lower/HI.json',
             'zcta_points.json']
            + [f'v2/albers/national/{name}.json' for name in ('state', 'cd116', 'cd118', 'cd119', 'county')]
            + [f'v2/albers/{tier}/HI/{level}.json' for tier in ('national', 'detail')
               for level in ('sldu', 'sldl', 'place', 'tract', 'zcta')]
            + [f'v2/albers/detail/HI/{level}.json' for level in ('state', 'county')]
            + ['v2/albers/hex/HI/hex10.json', 'v2/albers/hex/hex25.json', 'v2/albers/hex/hex50.json'])


def inside(box, x, y):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def path_points(path):
    values = [float(v) for v in (path or '').replace('M', ' ').replace('L', ' ').replace('Z', ' ').split()]
    return list(zip(values[0::2], values[1::2]))


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def hawaiian_features():
    """{file: [(key, feature)]} for every Hawaiian feature of every shipped file that draws one."""
    found = {}

    def add(path, items):
        found.setdefault(Path(path).relative_to(GEO).as_posix(), []).extend(
            (str(key), feature) for key, feature in items)

    for vintage in ('116', '118', '119'):
        payload = load(GEO / f'cd{vintage}.json')
        add(GEO / f'cd{vintage}.json', [(k, d) for k, d in list(payload['districts'].items())
                                        + list(payload['states'].items()) if d.get('region') == 'hawaii'])
    for chamber in ('upper', 'lower'):
        add(GEO / 'sld' / chamber / 'HI.json', [(d['id'], d) for d in load(GEO / 'sld' / chamber / 'HI.json')['districts']])
    points = load(GEO / 'zcta_points.json')['points']
    add(GEO / 'zcta_points.json', [(z, {'anchor': p[:2]}) for z, p in points.items() if p[2] == 'hawaii'])
    for name in ('state', 'cd116', 'cd118', 'cd119'):
        payload = load(ALBERS / 'national' / f'{name}.json')
        add(ALBERS / 'national' / f'{name}.json',
            [(k, d) for k, d in (payload.get('states') or payload.get('districts')).items() if d.get('region') == 'hawaii'])
    for folder in (ALBERS / 'national' / 'HI', ALBERS / 'detail' / 'HI', ALBERS / 'hex' / 'HI'):
        for path in sorted(folder.glob('*.json')):
            add(path, load(path)['features'].items())
    for pattern in ('national/county*.json', 'hex/hex25*.json', 'hex/hex50*.json'):
        for path in sorted(ALBERS.glob(pattern)):
            add(path, [(k, f) for k, f in load(path)['features'].items()
                       if f.get('state') == 'HI' or str(k).startswith('15')])
    return found


def feature_points(feature):
    points = path_points(feature.get('path'))
    for field in ('anchor', 'centroid'):
        if feature.get(field):
            points.append(tuple(feature[field]))
    if feature.get('bbox'):
        x0, y0, x1, y1 = feature['bbox']
        points += [(x0, y0), (x1, y1)]
    return points


def bounds(features):
    points = [point for _key, feature in features for point in path_points(feature.get('path'))]
    return [min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points), max(y for _, y in points)]


class ClipExtent(unittest.TestCase):
    """The extent itself, and the shipped projection blocks that must carry it."""

    def test_only_hawaii_is_clipped_and_its_extent_holds_its_box_and_meets_no_other_box(self):
        self.assertEqual([name for name, region in builder.REGIONS.items() if region.get('clip')], ['hawaii'])
        self.assertEqual(tuple(CLIP), (247.0, 497.0, 371.0, 610.0))
        self.assertTrue(CLIP[0] <= BOX[0] and CLIP[1] <= BOX[1] and BOX[2] <= CLIP[2] and BOX[3] <= CLIP[3],
                        f'the clip {CLIP} must hold the Hawaii box {BOX}')
        self.assertTrue(0 <= CLIP[0] < CLIP[2] <= builder.WIDTH and 0 <= CLIP[1] < CLIP[3] <= builder.HEIGHT)
        for name, region in builder.REGIONS.items():
            if name == 'hawaii':
                continue
            x0, y0, x1, y1 = region['box']
            self.assertFalse(CLIP[0] <= x1 and x0 <= CLIP[2] and CLIP[1] <= y1 and y0 <= CLIP[3],
                             f'the Hawaii clip {CLIP} meets the {name} box {region["box"]}')

    def test_the_shipped_projection_blocks_record_the_extent(self):
        blocks = {f'cd{vintage}.json': load(GEO / f'cd{vintage}.json')['projection'] for vintage in ('116', '118', '119')}
        blocks['v2/index.json'] = load(GEO / 'v2' / 'index.json')['frame']['projection']
        blocks['v2/albers/national/state.json'] = load(ALBERS / 'national' / 'state.json')['projection']
        for label, block in blocks.items():
            for name, region in block['regions'].items():
                self.assertEqual(region.get('clip'), list(CLIP) if name == 'hawaii' else None, f'{label} {name}')
            self.assertIn('clipExtent', block['formula'], label)


class HawaiianGeometry(unittest.TestCase):
    """Every Hawaiian vertex of every shipped file."""

    @classmethod
    def setUpClass(cls):
        cls.found = hawaiian_features()

    def test_every_file_that_draws_hawaii_was_read(self):
        for name in EXPECTED:
            self.assertTrue(self.found.get(name), f'{name}: no Hawaiian feature read')

    def test_no_hawaiian_vertex_falls_outside_the_clip_or_inside_the_alaska_box(self):
        outside, in_alaska, checked = [], [], 0
        for name, features in sorted(self.found.items()):
            for key, feature in features:
                for x, y in feature_points(feature):
                    checked += 1
                    if not inside(CLIP, x, y):
                        outside.append((name, key, x, y))
                    if inside(ALASKA, x, y):
                        in_alaska.append((name, key, x, y))
        print(f'\n  {checked:,} Hawaiian points in {len(self.found)} files; '
              f'{len(outside)} outside the clip, {len(in_alaska)} in the Alaska box')
        self.assertGreater(checked, 50000, 'the scan read too little to mean anything')
        self.assertEqual(outside, [], f'{len(outside)} Hawaiian points outside the clip {CLIP}, '
                                      f'first {outside[:4]}')
        self.assertEqual(in_alaska, [], f'{len(in_alaska)} Hawaiian points inside the Alaska box '
                                        f'{ALASKA}, first {in_alaska[:4]}')

    def test_the_clip_took_the_northwestern_islands_and_kept_every_main_island(self):
        by_file = {name: dict(features) for name, features in self.found.items()}
        # The 20m layers never held the NWHI: HI-01, HI-02 and the state outline are as fitted.
        self.assertEqual(by_file['cd119.json']['HI']['bbox'], [258.0, 526.6, 352.0, 587.4])
        # The 500k layers keep Kaula (x 252.87) and Cape Kumukahi (x 352.14), both outside the
        # layout box: the clip is not the box.
        self.assertEqual(bounds(self.found['v2/albers/detail/HI/state.json']), [252.87, 526.59, 352.14, 587.54])
        self.assertEqual(bounds(self.found['v2/albers/detail/HI/county.json']), [252.87, 526.59, 352.14, 587.54])
        self.assertLess(bounds(self.found['v2/albers/detail/HI/state.json'])[0], BOX[0])
        self.assertGreater(bounds(self.found['v2/albers/detail/HI/state.json'])[2], BOX[2])
        # Senate 25 and House 51 keep their two Oahu rings; the fifteen NWHI rings are counted.
        index = load(GEO / 'sld' / 'index.json')
        for chamber, geoid in (('upper', '15025'), ('lower', '15051')):
            district = by_file[f'sld/{chamber}/HI.json'][geoid]
            self.assertEqual(district['path'].count('M'), 2, f'{chamber} {geoid}')
            self.assertEqual(index['chambers'][chamber]['clipped_rings'], {geoid: 15}, chamber)
            self.assertEqual(index['chambers'][chamber]['offcanvas_rings'], {}, chamber)
        # The one all-NWHI tract is listed, not drawn, in both tiers.
        for tier in ('national', 'detail'):
            payload = load(ALBERS / tier / 'HI' / 'tract.json')
            self.assertEqual(payload.get('clipped'), ['15003981200'], tier)
            self.assertNotIn('15003981200', payload['features'], tier)


if __name__ == '__main__':
    unittest.main(verbosity=2)
