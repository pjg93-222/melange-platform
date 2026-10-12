"""G8 consumer smoke: the console's own calculator (mentat/app/giving_forecast.py, schema 2) loads a staged bundle and projects every industry it offers.
usage: MENTAT_GIVING_MODELS=<giving-models-v2.json> python g8_consumer_smoke.py      (or pass the bundle path as the first argument)
Checks
  1. the bundle loads through giving_forecast.models() (the contract validation and the anchor-contract check the calculator itself runs);
  2. catalog() answers and names the bundle's model_version;
  3. every offered industry answers a scenario request (the saved-outlook control scores, the industry's mean growth) with either a projection
     or a clean, reported status (anchor_unavailable / anchor_stale); an industry with a withheld model may refuse with a ValueError that says why.
Exit 0 = PASS (prints one line per industry), 1 = the calculator cannot read the bundle or raised something other than a clean refusal.
When no Mentat anchor snapshot exists (or it is stale) the projections would all answer anchor_unavailable, which proves nothing about the bundle's
arithmetic: the smoke then runs against the contract's own anchors example (fixture levels, the workbook-freshness check stubbed) and says so.
Read-only: nothing is written, nothing is collected.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]          # .../mentat
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'forecast' / 'contracts'))

if len(sys.argv) > 1:
    os.environ['MENTAT_GIVING_MODELS'] = str(Path(sys.argv[1]).resolve())
if not os.environ.get('MENTAT_GIVING_MODELS'):
    print('G8: no bundle given (argument or MENTAT_GIVING_MODELS)')
    sys.exit(1)

import giving_forecast as gf  # noqa: E402


def use_fixture_anchors(tmp):
    """Point the calculator at the contract's anchors example for this process only (the real snapshot is never touched)."""
    import contract
    path = Path(tmp) / 'anchors.json'
    path.write_text(json.dumps(contract.example('anchors')), encoding='utf-8')
    os.environ['MENTAT_GIVING_ANCHORS'] = str(path)
    shas = {i['mentat']['workbook']: i['mentat'].get('workbook_sha256') for i in contract.example('anchors')['industries'].values()}
    gf._published_workbook_sha256 = lambda name: shas.get(name)
    gf._load_anchors.cache_clear()


def scenario(bundle, industry):
    outlook = bundle['saved_outlook']['control']
    growth = industry.get('growth_mean')
    growth = 5.0 if growth is None else growth
    today = gf.date.today()
    current = today.year + today.year % 2
    horizons = {}
    for cycle in (current, current + 2):
        horizons[str(cycle)] = dict(growth_pct=growth, current_control={'House': outlook['House']['current_control'], 'Senate': outlook['Senate']['current_control']},
                                    control_scores={'House': outlook['House']['value'], 'Senate': outlook['Senate']['value']},
                                    annual_forecast_changes={str(current): 0, str(current + 1): 0, str(current + 2): 0})
        if bundle.get('cycle_model') is not None:
            del horizons[str(cycle)]['growth_pct']          # with a cycle model the growth input is the Industry revenue growth: blank = the IBIS default of the bundle
    return dict(industry=industry['id'], as_of=today.isoformat(), horizons=horizons)


def real_anchors_usable(bundle):
    try:
        gf.anchors()
        return all(gf._anchor_industry(i) for i in [r['id'] for r in gf.catalog()['industries']])
    except Exception:
        return False


def main():
    try:
        bundle = gf.models()
    except Exception as error:  # the calculator refuses a bundle it cannot use: that is the gate's answer
        print('G8 FAIL: the calculator cannot load the bundle:', error)
        return 1
    if bundle.get('schema_version') != 2:
        print('G8 FAIL: not a schema-2 bundle (schema_version=%r)' % bundle.get('schema_version'))
        return 1
    catalog = gf.catalog()
    if catalog.get('model_version') != bundle['model_version']:
        print('G8 FAIL: catalog names %r, bundle is %r' % (catalog.get('model_version'), bundle['model_version']))
        return 1
    offered = [row['id'] for row in catalog['industries']]
    print('G8 loaded %s (%s): offered %s; not offered %s' % (bundle['model_version'], gf.model_path().name, offered, [r['id'] for r in catalog['not_offered']]))
    failed = 0
    fixture = False
    if not real_anchors_usable(bundle):
        import tempfile
        tmp = tempfile.mkdtemp(prefix='g8_anchors_')
        use_fixture_anchors(tmp)
        fixture = True
        print('G8 note: no usable Mentat anchor snapshot; projecting against the contract anchors example (fixture levels, arithmetic check only)')
    for ident in offered:
        industry = bundle['industries'][ident]
        try:
            out = gf.project(scenario(bundle, industry))
        except ValueError as error:
            withheld = bool(industry.get('withheld'))
            print('  %-32s %s: %s' % (ident, 'refused (withheld model)' if withheld else 'FAIL refused', str(error)[:140]))
            failed += 0 if withheld else 1
            continue
        except Exception as error:  # anything else is a crash, not a refusal
            print('  %-32s FAIL %s: %s' % (ident, type(error).__name__, str(error)[:140]))
            failed += 1
            continue
        status = out.get('status')
        horizons = out.get('horizons') or []
        if status in ('anchor_unavailable', 'anchor_stale'):
            print('  %-32s clean status %s (%s)' % (ident, status, str(out.get('reason'))[:100]))
        elif horizons:
            json.dumps(out)   # the response must serialise as the console sends it
            print('  %-32s projected %d horizon(s)' % (ident, len(horizons)))
        else:
            print('  %-32s FAIL empty projection (status=%r)' % (ident, status))
            failed += 1
    print('G8 %s' % ('PASS' if not failed else 'FAIL (%d industr%s)' % (failed, 'y' if failed == 1 else 'ies')))
    return 1 if failed else 0


sys.exit(main())
