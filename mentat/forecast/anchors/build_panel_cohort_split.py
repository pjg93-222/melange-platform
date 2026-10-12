"""Party split (D/R by chamber) of the published PAC panel's cohorts, for the giving forecast's "Company PACs" candidate basis: the credit unions' 16 PACs (D-117 follow-up, 2026-10-08)
and Telecom's 8 PACs (2026-10-08), one pinned file each.

The panel bundle (app/data/pac-giving-current.json) carries a cohort's House+Senate TOTALS only. The D/R split of the same PACs is the partisan-dollar lane's own
canonical classification (accounting basis canonical_cycle_office_nominal_pac_D_R: candidate, office and party resolved for the target cycle from the saved official sources):
  * the anchor cycle (2023-24): <LOCALAPPDATA>/Mentat/forecast/partisan-refinement-round2-20261005/inputs/pac_party_chamber_v3/ledger.csv (admission receipt
    metadata/pac_party_chamber_v3/ADMISSION_RECEIPT.json);
  * the open cycle (2025-26) to the panel's cut: <LOCALAPPDATA>/Mentat/forecast/political-model-20261006/inputs/current2026/ledger.csv (the lane's current-2026 ledger).
Only the rows the panel itself counts (`hs` == 1: a House/Senate candidate id in the FEC file) are split, so every amount below is a part of the panel's own total or observed
amount and  D + R + Other + Unknown + unresolved-office pool  ==  the panel's number, to the cent (checked here and again at runtime). The unresolved pool is the lane's rows whose
office it could not resolve (it pools them over both chambers and parties); Other / Unknown are third-party, independent and unresolved-party recipients. The ids file whose sha256
is the panel's `ids_sha256` must list the ledger's committees (every credit-union id gives; Telecom's cohort has one id, C00377044, with no row in either ledger: the file then says so in
`silent_committees` and the to-the-cent totals are what proves nothing is missing).

    python build_panel_cohort_split.py            # rewrite panel_cohort_split.json (credit unions) and panel_cohort_split_telecom.json
    python build_panel_cohort_split.py --check    # exit 1 when a file differs from a fresh derivation
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
LANE = Path(os.environ.get('LOCALAPPDATA', '')) / 'Mentat' / 'forecast'
HISTORY = LANE / 'partisan-refinement-round2-20261005' / 'inputs' / 'pac_party_chamber_v3' / 'ledger.csv'
CURRENT = LANE / 'political-model-20261006' / 'inputs' / 'current2026' / 'ledger.csv'
ATTEMPT4 = Path('C:/melange-data/forecast-redesign-20261001-review/accuracy-refinement20261002/attempt4-codex-proposal-20261005')
# sector -> (file written, ids file whose sha256 is the panel's ids_sha256)
SECTORS = {
    'credit_unions': (HERE / 'panel_cohort_split.json', ATTEMPT4 / 'attempt4-pace-forward-r6-20261005/ids/ids_credit_unions.json'),
    'telecom': (HERE / 'panel_cohort_split_telecom.json', ATTEMPT4 / 'mentat-roster-ibis-20261005/ids/ids_telecom.json'),
}
SCHEMA = 'panel-cohort-party-split-v1'
BASIS = 'canonical_cycle_office_nominal_pac_D_R'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(path, sector):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return [r for r in csv.DictReader(stream) if r['sector'] == sector]


def bucket(selected):
    """{House|Senate: {D, R, Other, Unknown}, unresolved_office_pool} in cents of the rows the panel counts (hs == 1), classified by the lane."""
    out = {c: dict(D=0, R=0, Other=0, Unknown=0) for c in ('House', 'Senate')}
    out['unresolved_office_pool'] = 0
    for r in selected:
        if r['hs'] != '1':
            continue
        cents, scope = int(r['cents']), r['canonical_hs_eligibility']
        if scope == 'include_hs':
            party = r['canonical_party'] if r['canonical_party'] in ('D', 'R', 'Other') else 'Unknown'
            out[r['canonical_chamber']][party] += cents
        elif scope == 'unresolved_potential_hs':
            out['unresolved_office_pool'] += cents
        else:
            raise ValueError('a counted row the lane excludes: ' + scope)
    out['sum_cents'] = sum(v for c in ('House', 'Senate') for v in out[c].values()) + out['unresolved_office_pool']
    return out


def derive(panel_item, history, current, ids_path):
    """The file's content from the panel's item of one sector (pac_giving_forecast.catalog()), the lane's two ledgers (row lists of that sector) and the ids file; raises when anything fails to reconcile."""
    sector = panel_item['sector']
    now = panel_item['current']
    target, cut = now['target'], now['cut']
    anchor = target - 2
    totals = {h['cycle']: h['total_cents'] for h in now['trace']['history']}
    ids = set(json.loads(Path(ids_path).read_text(encoding='utf-8'))['ids'])
    seen_ids = {r['committee'] for r in history}
    if sha256(ids_path) != panel_item['cohort']['ids_sha256'] or not seen_ids <= ids or not {r['committee'] for r in current} <= ids:
        raise ValueError('the ledgers are not the panel cohort')
    silent = sorted(ids - seen_ids)
    cycles = {str(anchor): bucket([r for r in history if r['cycle'] == str(anchor)])}
    observed = bucket([r for r in current if r['cycle'] == str(target) and r['date'] <= cut])
    for name, got, want in ((str(anchor), cycles[str(anchor)]['sum_cents'], totals[anchor]), ('observed to ' + cut, observed['sum_cents'], now['observed_cents'])):
        if got != want:
            raise ValueError('%s: the split sums to %d cents, the panel has %d' % (name, got, want))
    out = dict(schema=SCHEMA, sector=sector, basis=BASIS, cohort_ids_sha256=panel_item['cohort']['ids_sha256'], count=panel_item['cohort']['count'], target=target,
               cycles=cycles, observed=dict(observed, through=cut),
               sources=dict(history_ledger_sha256=sha256(HISTORY), current_ledger_sha256=sha256(CURRENT), ids_sha256=sha256(ids_path)),
               note='Cents. Only rows the panel counts (hs == 1) are split; every bucket sums to the panel total (cycle) or observed amount (to the cut) to the cent.')
    if silent:
        out['silent_committees'] = silent          # cohort ids with no row in either ledger: they gave nothing the panel counts, which the cent-exact totals above confirm
    return out


def main(argv):
    sys.path.insert(0, str(HERE.parents[1] / 'app'))
    import pac_giving_forecast as panel
    items = {s['sector']: s for s in panel.catalog()['sectors']}
    status = 0
    for sector, (out, ids_path) in SECTORS.items():
        fresh = derive(items[sector], rows(HISTORY, sector), rows(CURRENT, sector), ids_path)
        text = json.dumps(fresh, indent=2, sort_keys=True) + '\n'
        if '--check' in argv:
            same = out.read_text(encoding='utf-8') == text
            print(out.name + ' ' + ('matches' if same else 'DIFFERS FROM') + ' a fresh derivation')
            status = status or (0 if same else 1)
        else:
            out.write_text(text, encoding='utf-8')
            print('wrote', out, hashlib.sha256(text.encode()).hexdigest())
    return status


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
