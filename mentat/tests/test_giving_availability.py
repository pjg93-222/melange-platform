"""When the model package becomes usable (2026-10-09): a refit finished in the Eastern evening is available the same Eastern day.

`built_on` is the UTC date of `built_at_utc`, which runs one day ahead of the page's calendar (America/New_York) from 20:00 to 24:00 Eastern. The calculator
therefore reads the build INSTANT in the console's calendar, so the stamp's UTC date never makes a bundle "not available at that historical cutoff" on the day
it was built. Bundles stamped before this rule (UTC `built_on`, or no `built_at_utc` at all) are read the same way and stay valid.
usage: python -m unittest mentat/tests/test_giving_availability.py"""
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'app'))
sys.path.insert(0, str(HERE.parent / 'forecast' / 'contracts'))
import contract  # noqa: E402
import giving_forecast as gf  # noqa: E402
from test_giving_forecast_v2 import BANKS, World, scenario  # noqa: E402   (World's clock is 2026-09-30)


def built(stamp):
    """The contract example stamped as the refit stamps it: built_at_utc, and built_on = its UTC date."""
    bundle = contract.example('models')
    bundle.update(built_at_utc=stamp, built_on=stamp[:10])
    return bundle


class VintageTests(unittest.TestCase):
    def test_the_vintage_is_the_build_day_in_the_console_calendar(self):
        for stamp, day in (('2026-10-01T03:30:00Z', '2026-09-30'),        # 23:30 EDT, the UTC date is the next day
                           ('2026-10-01T03:59:59Z', '2026-09-30'),        # the last second before Eastern midnight
                           ('2026-10-01T04:00:00Z', '2026-10-01'),        # Eastern midnight
                           ('2026-10-09T04:02:14Z', '2026-10-09'),        # 00:02 EDT, the bundle published on 10-09
                           ('2026-11-02T04:30:00Z', '2026-11-01'),        # 23:30 EST (standard time, UTC-5)
                           ('2026-09-30T12:00:00Z', '2026-09-30')):       # morning: the two calendars agree
            with self.subTest(stamp=stamp):
                self.assertEqual(gf._model_vintage(built(stamp)).isoformat(), day)

    def test_a_bundle_without_a_build_instant_keeps_its_built_on(self):
        legacy = built('2026-10-01T03:30:00Z')
        del legacy['built_at_utc']
        self.assertEqual(gf._model_vintage(legacy).isoformat(), '2026-10-01')

    def test_a_vintage_dated_earlier_than_the_build_stays_earlier(self):
        bundle = built('2026-09-30T12:00:00Z')
        bundle['built_on'] = '2025-01-01'
        self.assertEqual(gf._model_vintage(bundle).isoformat(), '2025-01-01')


class EveningBuildTests(unittest.TestCase):
    """A bundle built at 23:30 EDT on 2026-09-30 (03:30Z, built_on 2026-10-01) with the console's clock at 2026-09-30."""

    def test_it_projects_for_the_as_of_date_of_that_same_local_day(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=built('2026-10-01T03:30:00Z')):
            out = gf.project(scenario(BANKS, as_of='2026-09-30'))
            self.assertEqual(out['as_of'], '2026-09-30')

    def test_the_catalog_names_the_local_day_so_the_date_picker_range_is_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=built('2026-10-01T03:30:00Z')):
            shown = gf.catalog()
            self.assertEqual((shown['built_on'], shown['as_of']), ('2026-09-30', '2026-09-30'))

    def test_the_day_before_the_build_is_still_a_historical_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=built('2026-10-01T03:30:00Z')), \
                self.assertRaisesRegex(ValueError, 'not available at that historical cutoff'):
            gf.project(scenario(BANKS, as_of='2026-09-29'))


if __name__ == '__main__':
    unittest.main()
