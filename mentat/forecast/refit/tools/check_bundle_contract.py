"""Second opinion on a bundle: validate it (schema + cross-field invariants) with the contract's own Python validator (contracts/contract.py) and apply every shared
negative control of target 'models' (and, when the bundle carries a `cycle_model`, of target 'models_cycle'). Verification tool only (the pipeline itself is R).
usage: python check_bundle_contract.py <giving-models-v2.json> [<contracts dir>]   exit 0 = valid and every control rejected
"""
import copy
import json
import sys
from pathlib import Path

bundle = Path(sys.argv[1])
contracts = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[2] / 'contracts'
sys.path.insert(0, str(contracts))
import contract  # noqa: E402

doc = json.loads(bundle.read_text(encoding='utf-8'))
schema = contract.schema('models')
problems = contract.validate(doc, schema) or contract.check_model_invariants(doc)
print('problems:', len(problems))
for p in problems[:10]:
    print('  ', p)


def base_for(control):
    """A control that REPLACES a whole industry row (C09: a Telecom row with no giving model) tests that such a row is checked for giving inputs. Telecom has them in the bundle now (owner ruling
    2026-10-08), so the control is applied to the bundle without that industry's step blocks and IBIS path, which is what the replaced row would lack; every other control is applied as it is."""
    path = control['path']
    if len(path) == 2 and path[0] == 'industries' and 'cycle_model' in doc:
        base = copy.deepcopy(doc)
        cm = base['cycle_model']
        for measure in cm['measures'].values():
            measure['step'].pop(path[1], None)
        cm['budget_rule']['growth'].pop(path[1], None)
        cm.get('g_prior', {}).pop(path[1], None)
        return base
    return doc


accepted = []
for control in contract.negative_controls():
    if control['target'] != 'models' and not (control['target'] == 'models_cycle' and 'cycle_model' in doc):
        continue
    mutated = contract.apply_mutation(base_for(control), control)
    if not (contract.validate(mutated, schema) or contract.check_model_invariants(mutated)):
        accepted.append(control['id'])
print('negative controls accepted (must be none):', accepted)
sys.exit(0 if not problems and not accepted else 1)
