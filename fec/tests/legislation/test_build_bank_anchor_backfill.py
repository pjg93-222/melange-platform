# -*- coding: utf-8 -*-
"""Cheap contracts for the official-source bank-anchor backfill."""
from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'fec'))

import legislation.build_bank_anchor_backfill as bank


def person(full_name, bioguide, original=None):
    node = ET.Element('item')
    ET.SubElement(node, 'fullName').text = full_name
    ET.SubElement(node, 'bioguideId').text = bioguide
    if original is not None:
        ET.SubElement(node, 'isOriginalCosponsor').text = (
            'true' if original else 'false'
        )
    return node


class BankAnchorContractTests(unittest.TestCase):
    def test_exact_declared_anchor_contract(self):
        expected = {
            ('S.1045', '118'): (
                'BILLSTATUS-118s1045.xml',
                'bank_primary',
                'L0:manual-adjudication',
                ['Examination & Supervision'],
                bank.CLAWBACK_RATIONALE,
            ),
            ('S.1790', '118'): (
                'BILLSTATUS-118s1790.xml',
                'bank_primary',
                'L0:manual-adjudication',
                ['Examination & Supervision'],
                bank.CLAWBACK_RATIONALE,
            ),
            ('S.4050', '119'): (
                'BILLSTATUS-119s4050.xml',
                'bank_primary',
                'L0:manual-adjudication',
                ['Examination & Supervision'],
                bank.CLAWBACK_RATIONALE,
            ),
            ('S.3538', '118'): (
                'BILLSTATUS-118s3538.xml',
                'bank_primary',
                'L1:association-position',
                ['Charter Modernization'],
                bank.ILC_RATIONALE,
            ),
            ('S.3734', '119'): (
                'BILLSTATUS-119s3734.xml',
                'bank_primary',
                'L1:association-position',
                ['Charter Modernization'],
                bank.ILC_RATIONALE,
            ),
            ('S.838', '119'): (
                'BILLSTATUS-119s838.xml',
                'bank_primary',
                'L1:association-position',
                ['Member Business Lending'],
                bank.ACRE_RATIONALE,
            ),
            ('H.R.1822', '119'): (
                'BILLSTATUS-119hr1822.xml',
                'bank_primary',
                'L1:association-position',
                ['Member Business Lending'],
                bank.ACRE_RATIONALE,
            ),
            ('S.2669', '118'): (
                'BILLSTATUS-118s2669.xml',
                'crypto_primary',
                'L0:manual-adjudication',
                ['Technology & Emerging'],
                (
                    'DAAMLA AML superstructure on digital assets; '
                    'Blockchain Association formally opposed'
                ),
            ),
            ('S.954', '119'): (
                'BILLSTATUS-119s954.xml',
                'crypto_primary',
                'L1:association-position',
                ['Technology & Emerging'],
                'strategic bitcoin reserve; industry-championed',
            ),
            ('H.R.2032', '119'): (
                'BILLSTATUS-119hr2032.xml',
                'crypto_primary',
                'L1:association-position',
                ['Technology & Emerging'],
                'strategic bitcoin reserve; industry-championed',
            ),
            ('H.R.9330', '119'): (
                'BILLSTATUS-119hr9330.xml',
                'fintech',
                'L1:association-position',
                ['Payments'],
                'AFC-backed EWA framework reintroduction',
            ),
            ('H.R.3557', '118'): (
                'BILLSTATUS-118hr3557.xml',
                'telecom_primary',
                'L1:association-position',
                ['Technology & Emerging'],
                'NCTA and USTelecom-backed broadband permitting reform (American Broadband Deployment Act of 2023)',
            ),
            ('H.R.2289', '119'): (
                'BILLSTATUS-119hr2289.xml',
                'telecom_primary',
                'L1:association-position',
                ['Technology & Emerging'],
                'NCTA-led trade-association coalition letter urging floor action on the American Broadband Deployment Act of 2025',
            ),
            ('S.3587', '117'): (
                'BILLSTATUS-117s3587.xml',
                'telecom_primary',
                'L1:association-position',
                ['Technology & Emerging'],
                'NCTA-supported USDA ReConnect broadband funding (Connect Unserved Americans Act)',
            ),
            ('S.4821', '118'): (
                'BILLSTATUS-118s4821.xml',
                'telecom_primary',
                'L1:association-position',
                ['Technology & Emerging'],
                'USTelecom-endorsed faster federal-land broadband permits',
            ),
        }
        actual = {
            key: (
                value['xml'],
                value['class'],
                value['authority'],
                value['areas'],
                value['rationale'],
            )
            for key, value in bank.ANCHORS.items()
        }
        self.assertEqual(actual, expected)
        self.assertEqual(len(bank.ANCHORS), 15)
        self.assertEqual(
            bank.ACT_WEIGHT,
            {
                'bank_primary': 0.15,
                'crypto_primary': 0.15,
                'fintech': 0.15,
                'telecom_primary': 0.15,
            },
        )
        self.assertEqual(
            bank.COLLECTION_RECOVERY,
            'bank-anchor-backfill',
        )
        self.assertEqual(
            bank.MEMBERSHIP_SCHEMA,
            'bank-anchor-membership-v1',
        )
        self.assertNotIn(('S.2190', '118'), bank.ANCHORS)
        self.assertNotIn(('S.875', '119'), bank.ANCHORS)
        self.assertNotIn(('H.R.2702', '119'), bank.ANCHORS)

    def test_tagged_date_defaults_so_existing_anchors_stay_byte_identical(self):
        self.assertEqual(bank.DEFAULT_TAGGED, '2026-08-10')
        explicit = {
            key for key, value in bank.ANCHORS.items() if 'tagged' in value
        }
        self.assertEqual(
            explicit,
            {('H.R.3557', '118'), ('H.R.2289', '119'),
             ('S.3587', '117'), ('S.4821', '118')},
        )
        for key in explicit:
            self.assertEqual(bank.ANCHORS[key]['tagged'], '2026-10-07')
        for key, value in bank.ANCHORS.items():
            if key not in explicit:
                self.assertEqual(
                    value.get('tagged', bank.DEFAULT_TAGGED), '2026-08-10'
                )

    def test_every_pinned_billstatus_exists_and_is_the_declared_act(self):
        # The four telecom anchors (2026-10-07) ship their official XML in the
        # repo; the backfill itself re-checks identity at chain time.
        for key, config in bank.ANCHORS.items():
            with self.subTest(anchor=key):
                path = bank.BILLSTATUS / config['xml']
                self.assertTrue(path.is_file(), str(path))
                self.assertEqual(
                    bank.display_key(bank.parse_billstatus(path)), key
                )
        telecom = sorted(
            key for key, value in bank.ANCHORS.items()
            if value['class'] == 'telecom_primary'
        )
        self.assertEqual(
            telecom,
            [('H.R.2289', '119'), ('H.R.3557', '118'),
             ('S.3587', '117'), ('S.4821', '118')],
        )

    def test_strict_bioguide_map_rejects_historical_name_collisions(self):
        sponsor = person(
            'Sen. Brown, Sherrod [D-OH]',
            'B000944',
        )
        resolved_cosponsor = person(
            'Sen. Kennedy, John [R-LA]',
            'K000393',
            original=True,
        )
        departed_cosponsor = person(
            'Sen. Menendez, Robert [D-NJ]',
            'M000639',
            original=True,
        )
        official = {
            'sponsor': sponsor,
            'cosponsors': [
                resolved_cosponsor,
                departed_cosponsor,
            ],
        }
        bioguide_map = {
            'K000393': 'John Kennedy',
            'CURRENT-BROWN': 'Shontel Brown',
            'CURRENT-MENENDEZ': 'Robert Menendez',
        }

        resolved, departed = bank.resolve_official_people(
            official,
            bioguide_map,
        )

        self.assertEqual(
            [(role, member) for _, role, member in resolved],
            [('original', 'John Kennedy')],
        )
        self.assertEqual(
            [
                (role, bank.xml_text(node, 'bioguideId'))
                for node, role in departed
            ],
            [
                ('sponsor', 'B000944'),
                ('original', 'M000639'),
            ],
        )
        self.assertEqual(
            bank.xml_text(official['sponsor'], 'fullName'),
            'Sen. Brown, Sherrod [D-OH]',
        )
        self.assertEqual(
            sum(
                bank.ROLE_WEIGHT[role]
                for _, role, _ in resolved
            ),
            0.5,
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
