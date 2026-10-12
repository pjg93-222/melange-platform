# -*- coding: utf-8 -*-
import csv
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import district.fetch_acs_community as acs


class ACSCommunityTests(unittest.TestCase):
    def _write_fixture(self, root):
        specs = acs._source_specs('2024', '119')
        geo_path = os.path.join(root, specs['geography']['filename'])
        fields = [
            'FILEID', 'STUSAB', 'SUMLEVEL', 'COMPONENT', 'STATE',
            'CDCURR', 'GEO_ID', 'NAME',
        ]
        rows = [
            ['ACSSF', 'AL', '040', '00', '01', '', '0400000US01',
             'Alabama'],
            ['ACSSF', 'AL', '500', '00', '01', '01',
             '5001900US0101',
             'Congressional District 1 (119th Congress), Alabama'],
            # Must never overwrite the total state row.
            ['ACSSF', 'AL', '040', '43', '01', '', '040C243US01',
             'Alabama -- Rural'],
        ]
        with open(geo_path, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, delimiter='|', lineterminator='\n')
            writer.writerow(fields)
            writer.writerows(rows)

        for table, detail in acs.TABLES.items():
            path = os.path.join(root, specs[table]['filename'])
            columns = ['GEO_ID']
            for estimate, margin, _api in detail['variables'].values():
                columns.extend((estimate, margin))
            with open(path, 'w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=columns, delimiter='|',
                    lineterminator='\n',
                )
                writer.writeheader()
                for geo_id, scale in (
                        ('0400000US01', 2), ('5001900US0101', 1)):
                    row = {'GEO_ID': geo_id}
                    for metric, (
                            estimate, margin, _api
                            ) in detail['variables'].items():
                        base = {
                            'population': 100000,
                            'median_age': 40,
                            'median_household_income': 80000,
                            'population_16plus': 80000,
                            'civilian_labor_force': 50000,
                            'employed': 47000,
                            'unemployed': 3000,
                            'poverty_universe': 95000,
                            'below_poverty': 9500,
                            'occupied_housing_units': 40000,
                            'owner_occupied_units': 25000,
                            'renter_occupied_units': 15000,
                            'median_home_value': 320000,
                            'median_gross_rent': 1200,
                        }[metric]
                        row[estimate] = base * scale
                        row[margin] = max(1, base * 0.01)
                    writer.writerow(row)

        gaz_rows = {
            'district_gazetteer': (
                '2024_Gaz_119CDs_national.txt',
                'USPS\tGEOID\tALAND\tAWATER\tALAND_SQMI\t'
                'AWATER_SQMI\tINTPTLAT\tINTPTLONG\n'
                'AL\t0101\t2589988110\t0\t1000\t0\t32\t-86\n',
            ),
            'state_gazetteer': (
                '2024_Gaz_state_national.txt',
                'USPS\tGEOID\tNAME\tALAND\tAWATER\tALAND_SQMI\t'
                'AWATER_SQMI\tINTPTLAT\tINTPTLONG\n'
                'AL\t01\tAlabama\t5179976220\t0\t2000\t0\t32\t-86\n',
            ),
        }
        for key, (member, text) in gaz_rows.items():
            path = os.path.join(root, specs[key]['filename'])
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr(member, text)

    def test_builds_complete_district_and_state_context(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_fixture(root)
            artifact = acs.build_artifact(root, '2024', '119')

        self.assertEqual(artifact['schema'], acs.SCHEMA)
        self.assertEqual(artifact['coverage']['districts'], 1)
        self.assertEqual(artifact['coverage']['states'], 1)
        district = artifact['districts']['AL-01']
        state = artifact['states']['AL']
        self.assertTrue(district['complete'])
        self.assertEqual(district['region'], 'South')
        self.assertEqual(state['name'], 'Alabama')
        self.assertEqual(district['derived']['population_density_sq_mile'],
                         100.0)
        self.assertEqual(district['derived']['unemployment_rate'], 6.0)
        self.assertEqual(district['derived']['poverty_rate'], 10.0)
        self.assertEqual(district['derived']['owner_occupied_share'], 62.5)
        self.assertEqual(
            district['derived']['annual_rent_income_ratio'], 0.18
        )
        self.assertEqual(
            artifact['variable_catalog']['population']['variable'],
            'B01003_001E',
        )

    def test_census_sentinels_are_missing(self):
        self.assertIsNone(acs._number('-666666666'))
        self.assertIsNone(acs._number('-999999999'))
        self.assertEqual(acs._number('42'), 42)


if __name__ == '__main__':
    unittest.main()
