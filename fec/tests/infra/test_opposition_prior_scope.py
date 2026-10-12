# -*- coding: utf-8 -*-
"""Standalone synthetic tests for opposition-prior directional scope."""

import argparse
import csv
import json
import os
import re
import sys
import tempfile

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from lobbying import build_opposition_prior as BOP

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(
        ('  OK   ' if cond else 'FAIL  ') + name
        + (('  :: ' + str(detail)) if (detail and not cond) else '')
    )


def write_csv(path, header, rows):
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


fixture = tempfile.mkdtemp()
attribution_path = os.path.join(fixture, 'fec_member_attribution.csv')
associated_path = os.path.join(fixture, 'fec_member_associated.csv')
missing_roster = os.path.join(fixture, 'missing_roster.json')
missing_lobbyview = os.path.join(fixture, 'missing_lobbyview.json')

attribution_header = [
    'bioguide', 'candidate_ids', 'industry', 'side', 'cycle', 'month',
    'direct_dollars', 'sponsor_dollars', 'n_contributions', 'first_date',
    'last_date',
]
attribution_rows = [
    ['M0001', 'H0001', 'credit_union', 'ours', '2023-2024', '2023-05',
     '1000', '0', '2', '2023-05-01', '2023-05-15'],
    ['M0001', 'H0001', 'community_banks', 'opposition', '2023-2024',
     '2023-06', '500', '250', '1', '2023-06-01', '2023-06-01'],
    ['M0001', 'H0001', 'crypto', 'opposition', '2023-2024', '2023-07',
     '-100', '0', '1', '2023-07-01', '2023-07-01'],
    ['M0002', 'H0002', 'credit_union', 'ours', '2023-2024', '2023-04',
     '100', '0', '1', '2023-04-01', '2023-04-01'],
]
write_csv(attribution_path, attribution_header, attribution_rows)

associated_header = [
    'bioguide', 'candidate_id', 'industry', 'side', 'cycle',
    'associated_dollars', 'assoc_support_dollars', 'assoc_oppose_dollars',
    'assoc_mixed_dollars', 'assoc_conduit_dollars',
    'assoc_relational_dollars', 'assoc_noncond_trade_dollars',
]
associated_rows = [
    ['M0001', 'H0001', 'credit_union', 'ours', '2023-2024', '375', '100',
     '50', '0', '25', '200', '0'],
    ['M0001', 'H0001', 'community_banks', 'opposition', '2023-2024',
     '4400', '1000', '700', '300', '400', '2000', '0'],
    ['M0001', 'H0001', 'community_banks', 'opposition', '2025-2026',
     '600', '0', '0', '0', '0', '600', '0'],
    ['M0001', 'H0001', 'crypto', 'opposition', '2023-2024', '-500', '0',
     '0', '0', '0', '-500', '0'],
    ['M0001', 'S0001', 'crypto', 'opposition', '2023-2024', '300', '0',
     '0', '0', '0', '300', '0'],
    ['M0002', 'H0002', 'credit_union', 'ours', '2023-2024', '115', '10',
     '20', '0', '5', '80', '0'],
    ['', 'X9999', 'crypto', 'opposition', '2023-2024', '100', '0', '0',
     '0', '0', '100', '0'],
]
write_csv(associated_path, associated_header, associated_rows)


def run_build(out_dir):
    result = BOP._run(argparse.Namespace(
        attribution=attribution_path,
        out=out_dir,
        roster=missing_roster,
        lobbyview=missing_lobbyview,
    ))
    output_path = os.path.join(out_dir, 'fec_opposition_prior.json')
    with open(output_path, 'r', encoding='utf-8') as handle:
        output_text = handle.read()
    payload = json.loads(output_text)
    return result, payload, output_text


first_out = tempfile.mkdtemp()
first_result, first_payload, first_text = run_build(first_out)
ok('builder returns 0 for synthetic fixtures', first_result == 0, first_result)

member = first_payload['members']['M0001']
ok(
    'a. opposition-scoped directional member totals',
    member['opposition_assoc_oppose_total'] == 700.0
    and member['opposition_assoc_support_total'] == 1000.0
    and member['opposition_assoc_mixed_total'] == 300.0
    and member['opposition_assoc_conduit_total'] == 400.0,
    member,
)
ok(
    'b. all-industry totals retain ours plus opposition',
    member['assoc_oppose_total'] == 750.0 == 700.0 + 50.0
    and member['assoc_support_total'] == 1100.0 == 1000.0 + 100.0
    and member['assoc_mixed_total'] == 300.0
    and member['assoc_conduit_total'] == 425.0 == 400.0 + 25.0,
    member,
)

crypto = member['associated_by_industry']['crypto']['2023-2024']
community = member['associated_by_industry']['community_banks']['2023-2024']
credit_union = member['associated_by_industry']['credit_union']['2023-2024']
ok(
    'c. per-industry directional and row-clamped relational fields',
    crypto['assoc_relational'] == -200.0
    and crypto['assoc_relational_used'] == 300.0
    and crypto['assoc_total'] == -200.0
    and community['assoc_support'] == 1000.0
    and community['assoc_oppose'] == 700.0
    and community['assoc_mixed'] == 300.0
    and community['assoc_conduit'] == 400.0
    and community['assoc_relational_used'] == 2000.0
    and credit_union['assoc_relational_used'] == 200.0,
    {'crypto': crypto, 'community_banks': community,
     'credit_union': credit_union},
)

opposition_written_baseline = sum(
    member['associated_by_industry'][industry][cycle][
        'assoc_relational_used'
    ]
    for industry, side in BOP.INDUSTRY_SIDE.items()
    if side == 'opposition'
    for cycle in BOP.COMPLETED_CYCLES
) / len(BOP.COMPLETED_CYCLES)
ok(
    'd. written opposition relational-used fields reproduce baseline',
    member['baseline_opposition'] == 1150.0
    and opposition_written_baseline == 1150.0,
    (member['baseline_opposition'], opposition_written_baseline),
)

opposition_written_current = sum(
    member['associated_by_industry'][industry]['2025-2026'][
        'assoc_relational_used'
    ]
    for industry, side in BOP.INDUSTRY_SIDE.items()
    if side == 'opposition'
)
ok(
    'd2. current-cycle written fields reproduce current_opposition_raw',
    opposition_written_current == 600.0
    and member['current_opposition_raw'] == 600.0
    and member['current_opposition_annualized'] == 600.0,
    (opposition_written_current, member['current_opposition_raw'],
     member['current_opposition_annualized']),
)

ours_only = first_payload['members']['M0002']
ok(
    'e. ours-only directional exposure stays out of opposition scope',
    ours_only['assoc_oppose_total'] == 20.0
    and ours_only['opposition_assoc_oppose_total'] == 0.0,
    ours_only,
)

coverage = first_payload['coverage']
ok(
    'f. synthetic coverage and zero-month annualization',
    coverage['n_nonmember_candidates_carried'] == 1
    and coverage['months_elapsed_current_cycle'] == 0
    and coverage['annualization_factor'] == 1.0,
    coverage,
)

second_out = tempfile.mkdtemp()
second_result, second_payload, second_text = run_build(second_out)
for payload in (first_payload, second_payload):
    del payload['provenance']['generated']
    del payload['provenance']['run_id']
first_masked = re.sub(
    r'"generated": "[^"]*"', '"generated": "X"', first_text
)
first_masked = re.sub(
    r'"run_id": "[^"]*"', '"run_id": "X"', first_masked
)
second_masked = re.sub(
    r'"generated": "[^"]*"', '"generated": "X"', second_text
)
second_masked = re.sub(
    r'"run_id": "[^"]*"', '"run_id": "X"', second_masked
)
ok(
    'g. byte-deterministic output excluding generated provenance',
    second_result == 0
    and first_payload == second_payload
    and first_masked == second_masked,
)

invalid_path = os.path.join(fixture, 'invalid_associated.csv')
write_csv(
    invalid_path,
    associated_header,
    [['M0003', 'H0003', 'crypto', 'opposition', '2023-2024', '10',
      '20', '0', '0', '0', '0', '0']],
)
try:
    BOP._read_associated(invalid_path)
    ingest_failed_loud = False
except BOP.IngestError:
    ingest_failed_loud = True
ok(
    'h. associated bucket mismatch remains fail-loud',
    ingest_failed_loud,
)

print('\nOPPOSITION-PRIOR SCOPE TESTS — %d passed, %d FAILED' % (P, F))
sys.exit(1 if F else 0)
