"""Alignment first: the v2 Albers layers sit exactly on today's shipped map.

Lane G (2026-09-25). mentat/tools/build_geo_layers.py re-makes the state and
congressional-district paths through GeoPandas; before any new layer ships,
those paths must reproduce today's mentat/app/geo files to within 0.5 px, and
the vectorised projection every v2 layer goes through must be
build_district_geo's own. Three parts:

  1. Paths. Every state in geo/cd119.json and every district in geo/cd116,
     cd118 and cd119.json against v2 albers/national/state.json and
     cd1xx.json: the symmetric distance (each vertex of one path to the nearest
     segment of the other, both ways) must be <= 0.5 px, and the two key sets
     must be identical. Plain Python + numpy, so it runs under either Python.
  2. The state legislative layers (500k, a different simplifier and tier) are
     measured against today's geo/sld files and must agree for 99.5% of today's
     vertices within 0.5 px - the fine layers share the frame too.
  3. The projection (system Python only; skipped where GeoPandas is absent):
     build_geo_layers.Frame.project against build_district_geo.Albers plus the
     placement on 3,000 source vertices, to 1e-6 px.

Each part has a negative control: a 0.6 px shift, a swapped vintage and a
perturbed parallel must all be caught.

Run from inside mentat/tests:
    python -m unittest test_geo_v2_alignment
"""
import json
import os
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MENTAT = Path(os.environ.get('MENTAT_DIR') or HERE.parent)
APP = MENTAT / 'app'
TODAY = APP / 'geo'
# the installed tree when there is one, else the staging tree it is built in
# (build_geo_layers.py --install moves staging into the app in one rename)
INSTALLED = TODAY / 'v2'
STAGING = MENTAT / '.cache' / 'geo_build' / 'v2'
V2 = Path(os.environ.get('MENTAT_GEO_V2') or (INSTALLED if INSTALLED.is_dir() else STAGING))
TOLERANCE_PX = 0.5


def path_rings(path):
    """Rings of an SVG path in today's grammar ("M x y L x y ... Z"), closed."""
    rings = []
    for chunk in (path or '').split('M')[1:]:
        body = chunk.replace('L', ' ').replace('Z', ' ').split()
        values = [float(v) for v in body]
        points = np.array(values, dtype=float).reshape(-1, 2)
        if len(points) >= 3:
            rings.append(np.vstack([points, points[:1]]))
    return rings


def segments(rings):
    starts = np.vstack([ring[:-1] for ring in rings])
    ends = np.vstack([ring[1:] for ring in rings])
    return starts, ends


def directed_distance(points, starts, ends, block=4096):
    """max over points of the distance to the nearest segment."""
    worst = 0.0
    delta = ends - starts
    span = np.einsum('ij,ij->i', delta, delta)
    span[span == 0] = 1e-18
    for first in range(0, len(points), block):
        chunk = points[first:first + block]
        rel = chunk[:, None, :] - starts[None, :, :]
        t = np.clip(np.einsum('ijk,jk->ij', rel, delta) / span[None, :], 0.0, 1.0)
        nearest = starts[None, :, :] + t[:, :, None] * delta[None, :, :]
        distance = np.sqrt(((chunk[:, None, :] - nearest) ** 2).sum(axis=2)).min(axis=1)
        worst = max(worst, float(distance.max()))
    return worst


def symmetric_distance(path_a, path_b, shift=(0.0, 0.0)):
    rings_a = path_rings(path_a)
    rings_b = [ring + np.array(shift) for ring in path_rings(path_b)]
    if not rings_a or not rings_b:
        return float('inf')
    a_start, a_end = segments(rings_a)
    b_start, b_end = segments(rings_b)
    points_a = np.vstack([ring[:-1] for ring in rings_a])
    points_b = np.vstack([ring[:-1] for ring in rings_b])
    return max(directed_distance(points_a, b_start, b_end),
               directed_distance(points_b, a_start, a_end))


def vertex_share_within(path_today, path_new, tolerance):
    """(share of today's vertices within tolerance of the new path, count)."""
    rings_today = path_rings(path_today)
    rings_new = path_rings(path_new)
    if not rings_today or not rings_new:
        return 0.0, 0
    starts, ends = segments(rings_new)
    points = np.vstack([ring[:-1] for ring in rings_today])
    delta = ends - starts
    span = np.einsum('ij,ij->i', delta, delta)
    span[span == 0] = 1e-18
    within = 0
    for first in range(0, len(points), 2048):
        chunk = points[first:first + 2048]
        rel = chunk[:, None, :] - starts[None, :, :]
        t = np.clip(np.einsum('ijk,jk->ij', rel, delta) / span[None, :], 0.0, 1.0)
        nearest = starts[None, :, :] + t[:, :, None] * delta[None, :, :]
        distance = np.sqrt(((chunk[:, None, :] - nearest) ** 2).sum(axis=2)).min(axis=1)
        within += int((distance <= tolerance).sum())
    return within / len(points), len(points)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def need(path):
    if not Path(path).is_file():
        raise unittest.SkipTest(f'{path} is not built; run mentat/tools/build_geo_layers.py')
    return load(path)


class PathAlignment(unittest.TestCase):
    """Part 1: states and districts reproduce today's paths to <= 0.5 px."""

    def compare(self, today, new, label):
        self.assertEqual(sorted(today), sorted(new), f'{label}: key sets differ')
        worst, where = 0.0, None
        for key in sorted(today):
            distance = symmetric_distance(today[key]['path'], new[key]['path'])
            if distance > worst:
                worst, where = distance, key
        self.assertLessEqual(worst, TOLERANCE_PX, f'{label}: {where} is {worst:.3f} px off')
        return worst, where

    def test_states(self):
        today = need(TODAY / 'cd119.json')['states']
        new = need(V2 / 'albers' / 'national' / 'state.json')['states']
        worst, where = self.compare(today, new, 'states')
        print(f'\n  states: {len(today)} compared, worst {worst:.3f} px ({where})')

    def test_districts(self):
        for vintage in ('116', '118', '119'):
            today = need(TODAY / f'cd{vintage}.json')['districts']
            new = need(V2 / 'albers' / 'national' / f'cd{vintage}.json')['districts']
            worst, where = self.compare(today, new, f'cd{vintage}')
            print(f'\n  cd{vintage}: {len(today)} compared, worst {worst:.3f} px ({where})')

    def test_frame_constants_are_todays(self):
        today = need(TODAY / 'cd119.json')['projection']['regions']
        index = need(V2 / 'index.json')
        self.assertEqual(index['frame']['width'], 975)
        self.assertEqual(index['frame']['height'], 610)
        self.assertEqual(index['frame']['projection']['regions'], today)

    # -- negative controls: the detector must see a real misalignment
    def test_negative_control_shift_is_caught(self):
        today = need(TODAY / 'cd119.json')['districts']
        new = need(V2 / 'albers' / 'national' / 'cd119.json')['districts']
        for key in ('NY-12', 'NC-12', 'AK-AL', 'CA-12'):
            distance = symmetric_distance(today[key]['path'], new[key]['path'], shift=(0.6, 0.0))
            self.assertGreater(distance, TOLERANCE_PX, f'a 0.6 px shift of {key} went unseen')

    def test_negative_control_wrong_vintage_is_caught(self):
        today = need(TODAY / 'cd116.json')['districts']
        new = need(V2 / 'albers' / 'national' / 'cd119.json')['districts']
        # NC-12 was redrawn between the 116th and 119th maps
        self.assertGreater(symmetric_distance(today['NC-12']['path'], new['NC-12']['path']),
                           TOLERANCE_PX)


class LegislativeAlignment(unittest.TestCase):
    """Part 2: the 500k state-legislative layers share the frame."""

    def test_sld_vertices_sit_on_todays_paths(self):
        sld_index = need(TODAY / 'sld' / 'index.json')
        totals = {'within': 0, 'vertices': 0}
        worst_share, worst_key = 1.0, None
        chambers = {'upper': 'sldu', 'lower': 'sldl'}
        for chamber, level in chambers.items():
            for state in sorted(sld_index['chambers'][chamber]['states']):
                new_path = V2 / 'albers' / 'national' / state / f'{level}.json'
                if not new_path.is_file():
                    parts = sorted((V2 / 'albers' / 'national' / state).glob(f'{level}.*.json'))
                    if not parts:
                        self.fail(f'{new_path} missing')
                    features = {}
                    for part in parts:
                        features.update(load(part)['features'])
                else:
                    features = load(new_path)['features']
                today = load(TODAY / 'sld' / chamber / f'{state}.json')['districts']
                for district in today:
                    entry = features.get(district['id'])
                    self.assertIsNotNone(entry, f'{chamber} {district["id"]} missing from v2')
                    if not entry.get('path'):
                        continue
                    share, count = vertex_share_within(district['path'], entry['path'], TOLERANCE_PX)
                    totals['within'] += round(share * count)
                    totals['vertices'] += count
                    if share < worst_share:
                        worst_share, worst_key = share, f'{chamber} {district["id"]}'
        overall = totals['within'] / totals['vertices']
        print(f'\n  sld: {totals["vertices"]:,} of today\'s vertices, {overall:.4%} within '
              f'{TOLERANCE_PX} px of v2; lowest single district {worst_share:.2%} ({worst_key})')
        self.assertGreaterEqual(overall, 0.995)

    def test_negative_control_other_state_is_caught(self):
        today = need(TODAY / 'sld' / 'upper' / 'RI.json')['districts'][0]
        features = need(V2 / 'albers' / 'national' / 'CT' / 'sldu.json')['features']
        other = next(iter(features.values()))
        share, _count = vertex_share_within(today['path'], other['path'], TOLERANCE_PX)
        self.assertLess(share, 0.5)


class ProjectionPin(unittest.TestCase):
    """Part 3: Frame.project is build_district_geo's projection (system Python only)."""

    @classmethod
    def setUpClass(cls):
        try:
            import geopandas  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('GeoPandas is not in this Python; the pin runs under the '
                                    'system Python that runs the tool')
        sys.path.insert(0, str(MENTAT / 'tools'))
        import build_district_geo
        import build_geo_layers
        cls.builder = build_district_geo
        cls.layers = build_geo_layers
        cls.frame = build_geo_layers.Frame(build_geo_layers.DEFAULT_CACHE, log=lambda *_: None)
        features = build_district_geo.read_shapefile(
            build_geo_layers.DEFAULT_CACHE / 'cb_2024_us_state_20m.zip')
        rng = np.random.default_rng(20260925)
        cls.samples = []
        for row, rings in features:
            region = build_district_geo.region_of(row['STATEFP'])
            if region not in cls.frame.placements:
                continue
            points = [point for ring in rings for point in ring]
            chosen = rng.choice(len(points), size=min(60, len(points)), replace=False)
            cls.samples += [(region, points[i]) for i in chosen]

    def reference(self, region, lon, lat, parallels=None):
        region_def = self.builder.REGIONS[region]
        albers = self.builder.Albers(parallels or region_def['parallels'], region_def['meridian'],
                                     region_def['origin'])
        scale, tx, ty = self.frame.placements[region]
        x, y = albers(lon, lat)
        return tx + scale * x, ty - scale * y

    def test_vectorised_projection_matches_builder(self):
        self.assertGreaterEqual(len(self.samples), 3000)
        worst = 0.0
        for region, (lon, lat) in self.samples:
            got = self.frame.project(np.array([[lon, lat]]), region)[0]
            want = self.reference(region, lon, lat)
            worst = max(worst, abs(got[0] - want[0]), abs(got[1] - want[1]))
        print(f'\n  projection: {len(self.samples)} source vertices, worst {worst:.2e} px')
        self.assertLess(worst, 1e-6)

    def test_negative_control_perturbed_parallel_is_caught(self):
        worst = 0.0
        for region, (lon, lat) in self.samples[:500]:
            got = self.frame.project(np.array([[lon, lat]]), region)[0]
            parallels = tuple(p + 0.01 for p in self.builder.REGIONS[region]['parallels'])
            want = self.reference(region, lon, lat, parallels=parallels)
            worst = max(worst, abs(got[0] - want[0]), abs(got[1] - want[1]))
        self.assertGreater(worst, 1e-3)

    def test_alaskas_vertices_east_of_180_wrap_as_the_builder_wraps_them(self):
        # Lane L2, 2026-09-26: both projections wrap l - l0 into [-180, 180] degrees, as
        # d3's rotation does, so the Near Islands (Attu, 172.9 E) sit at the west end of
        # the Aleutian chain and the Alaska fit takes the 0.35 x lower-48 cap.
        features = self.builder.read_shapefile(
            self.layers.DEFAULT_CACHE / 'cb_2024_us_state_20m.zip')
        rings = next(rings for row, rings in features if row['STATEFP'] == '02')
        east = np.array([point for ring in rings for point in ring if point[0] > 0], dtype=float)
        self.assertGreaterEqual(len(east), 100)
        got = self.frame.project(east, 'alaska')
        want = np.array([self.reference('alaska', lon, lat) for lon, lat in east])
        self.assertLess(float(np.abs(got - want).max()), 1e-6)
        self.assertAlmostEqual(self.frame.placements['alaska'][0]
                               / self.frame.placements['lower48'][0], 0.35, places=9)
        adak = self.reference('alaska', -176.6, 51.9)
        self.assertTrue(bool(np.all(got[:, 0] < adak[0])),
                        'every Alaskan vertex east of 180 degrees draws west of Adak')


if __name__ == '__main__':
    unittest.main()
