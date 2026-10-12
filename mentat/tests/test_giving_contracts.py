"""The Giving Forecast contract (brain DE-021): schemas, shared examples, shared negative controls, and the validator itself.

The same example files and negative controls are asserted against the R pipeline's bundle writer (mentat/forecast/refit) and
against the anchor builder (mentat/forecast/anchors); this suite holds the Python side and proves the validator can fail.
"""
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'forecast' / 'contracts'))
import contract  # noqa: E402


def _schema_nodes(node, path='$'):
    """Every schema node of a schema document, skipping the maps whose keys are names, not keywords."""
    yield path, node
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key in ('properties', '$defs'):
            for name, child in value.items():
                yield from _schema_nodes(child, path + '.' + key + '.' + name)
        elif key == 'additionalProperties' and isinstance(value, dict):
            yield from _schema_nodes(value, path + '.additionalProperties')
        elif key == 'items' and isinstance(value, dict):
            yield from _schema_nodes(value, path + '.items')


class ContractTests(unittest.TestCase):
    def test_schemas_only_use_the_documented_keyword_subset(self):
        for kind in contract.SCHEMAS:
            for path, node in _schema_nodes(contract.schema(kind)):
                with self.subTest(kind=kind, path=path):
                    self.assertEqual(set(node) - contract.SUPPORTED_KEYWORDS, set())

    def test_the_bundle_example_validates(self):
        self.assertEqual(contract.validate_kind(contract.example('models'), 'models'), [])
        self.assertEqual(contract.check_model_invariants(contract.example('models')), [])

    @staticmethod
    def _accepted_controls(validator, target='models'):
        """Ids of the negative controls of one target a validator wrongly accepts (empty = every control was rejected)."""
        example = contract.example(target)
        out = []
        for c in contract.negative_controls():
            if c['target'] == target:
                path = ['commercial_banks' if step == '<industry>' else step for step in c['path']]
                if not validator(contract.apply_mutation(example, {**c, 'path': path})):
                    out.append(c['id'])
        return out

    def test_every_bundle_negative_control_fails_validation(self):
        self.assertGreaterEqual(len([c for c in contract.negative_controls() if c['target'] == 'models']), 10)
        self.assertEqual(self._accepted_controls(contract.validate_model), [])

    def test_a_schema_only_validator_misses_the_model_invariant_controls(self):
        missed = self._accepted_controls(lambda doc: contract.validate_kind(doc, 'models'))
        kinds = {c['id']: c['kind'] for c in contract.negative_controls() if c['target'] == 'models'}
        self.assertEqual(sorted(missed), sorted(i for i, k in kinds.items() if k == 'invariant'))
        self.assertGreaterEqual(len(missed), 3)

    def test_the_anchor_selection_rule_is_an_invariant(self):
        import copy
        base = contract.example('models')
        entry = lambda doc: doc['share_models']['control_House']['anchors']['commercial_banks']
        # same_phase chosen although it does not win in every design (the example's chrono gain is negative for banks House)
        doc = copy.deepcopy(base)
        entry(doc)['anchor'] = 'same_phase'
        doc['share_models']['control_House']['prediction']['industries']['commercial_banks'] = dict(entry(doc)['prediction']['same_phase'])
        self.assertTrue(any('same_phase chosen without beating' in p for p in contract.check_model_invariants(doc)))
        # previous_cycle kept although same_phase wins in both designs
        doc = copy.deepcopy(base)
        a = entry(doc)
        a['backtest']['same_phase'].update(loeo_mae=a['backtest']['previous_cycle']['loeo_mae'] * 0.5, chrono_mae=a['backtest']['previous_cycle']['chrono_mae'] * 0.5)
        a['backtest']['same_phase_gain'] = dict(loeo=0.5, chrono=0.5)
        self.assertTrue(any('previous_cycle kept although' in p for p in contract.check_model_invariants(doc)))
        # ...and the consistent flip is accepted
        a['anchor'] = 'same_phase'
        doc['share_models']['control_House']['prediction']['industries']['commercial_banks'] = dict(a['prediction']['same_phase'])
        self.assertEqual(contract.check_model_invariants(doc), [])
        self.assertEqual(contract.validate_kind(doc, 'models'), [])

    def test_a_validator_that_accepts_everything_is_caught_by_the_negative_controls(self):
        # Negative control of the negative controls: a permissive validator must be reported, control by control.
        accepted = self._accepted_controls(lambda doc: [])
        self.assertEqual(len(accepted), len([c for c in contract.negative_controls() if c['target'] == 'models']))

    # --- the optional cycle model (owner rulings 2026-10-07): the cycle example is the models example carrying `cycle_model` and the Pres term in the Senate model --------------
    def test_the_cycle_example_validates_and_the_plain_example_still_does(self):
        for kind in ('models', 'models_cycle'):
            with self.subTest(kind=kind):
                self.assertEqual(contract.validate_kind(contract.example(kind), 'models'), [])
                self.assertEqual(contract.validate_model(contract.example(kind)), [])
        self.assertNotIn('cycle_model', contract.example('models'))                 # a bundle without it is valid: the earlier calculator path (pointer rollback)
        cm = contract.example('models_cycle')['cycle_model']
        self.assertEqual(sorted(cm['measures']), ['general_giving', 'pac_to_candidates'])
        self.assertEqual(cm['budget_rule']['weight_model'], 0.5)
        self.assertEqual(sorted(cm['budget_rule']['growth']['commercial_banks']['annual']), ['2023', '2024', '2025', '2026', '2027'])

    def test_every_cycle_model_negative_control_fails_validation(self):
        controls = [c for c in contract.negative_controls() if c['target'] == 'models_cycle']
        self.assertGreaterEqual(len(controls), 8)
        self.assertEqual(self._accepted_controls(contract.validate_model, 'models_cycle'), [])
        # ... and a schema-only validator misses exactly the invariant ones; a permissive validator misses all
        missed = self._accepted_controls(lambda doc: contract.validate_kind(doc, 'models'), 'models_cycle')
        self.assertEqual(sorted(missed), sorted(c['id'] for c in controls if c['kind'] == 'invariant'))
        self.assertEqual(len(self._accepted_controls(lambda doc: [], 'models_cycle')), len(controls))

    def test_the_cycle_model_invariants_are_what_they_claim(self):
        import copy
        base = contract.example('models_cycle')
        self.assertEqual(contract.check_cycle_model(base), [])
        def problems(edit):
            doc = copy.deepcopy(base)
            edit(doc)
            return contract.check_cycle_model(doc)
        self.assertTrue(any('drift.d.1' in p for p in problems(lambda d: d['cycle_model']['measures']['pac_to_candidates']['drift']['d'].update({'1': 0.0}))))
        self.assertTrue(any('df is not pairs - 1' in p for p in problems(lambda d: d['cycle_model']['measures']['general_giving']['step']['credit_unions'].update(df=2))))
        self.assertTrue(any('no entry for property_casualty' in p for p in problems(lambda d: d['cycle_model']['budget_rule']['growth'].pop('property_casualty'))))
        self.assertTrue(any('no growth for 2027' in p for p in problems(lambda d: d['cycle_model']['budget_rule']['growth']['credit_unions']['annual'].pop('2027'))))
        self.assertTrue(any('step: no entry for credit_unions' in p for p in problems(lambda d: d['cycle_model']['measures']['general_giving']['step'].pop('credit_unions'))))
        self.assertTrue(any('weight_model' in p for p in problems(lambda d: d['cycle_model']['budget_rule'].update(weight_model=0.5000001))))
        self.assertTrue(any('Pres' in p for p in problems(lambda d: d['share_models']['control_Senate'].update(terms=['Intercept']))))
        # the optional block is not required by the plain bundle: the Senate rule only applies when a cycle model is present
        plain = copy.deepcopy(contract.example('models'))
        self.assertNotIn('Pres', plain['share_models']['control_Senate']['terms'])
        self.assertEqual(contract.validate_model(plain), [])

    def test_an_offered_industry_without_the_pac_capability_needs_no_giving_inputs(self):
        """Owner ruling 2026-10-08 (Telecom is offered for lobbying only): the cycle model checks the general-giving step and the IBIS path of the industries offered for giving."""
        import copy
        bundle = copy.deepcopy(contract.example('models_cycle'))
        row = copy.deepcopy(bundle['industries']['property_casualty'])
        row.update(id='telecom', label='Telecommunications', mentat_key='telecom', capabilities=dict(pac=False, control=False, lobbying=True), withheld=dict(pac='no giving model', control='no giving model'))
        row['lobbying_only'] = True
        bundle['industries']['telecom'] = row
        self.assertEqual(contract.check_cycle_model(bundle), [])                                  # flagged lobbying only: no step block, no growth path
        self.assertEqual(contract.validate_model(bundle), [])
        for edit in (lambda r: r.update(lobbying_only=False), lambda r: r.pop('lobbying_only')):       # flag false, or no flag with the PAC capability merely withheld: checked
            doc = copy.deepcopy(bundle)
            edit(doc['industries']['telecom'])
            self.assertTrue(any('no entry for telecom' in p for p in contract.check_cycle_model(doc)))

    def test_a_full_telecom_row_is_checked_for_giving_inputs_and_passes_with_them(self):
        """Owner ruling 2026-10-08 (B09, wireless + cable): Telecom is offered for giving and lobbying, carries no lobbying_only flag, and has a general-giving step block and an IBIS path."""
        import copy
        bundle = copy.deepcopy(contract.example('models_cycle'))
        row = copy.deepcopy(bundle['industries']['property_casualty'])
        row.update(id='telecom', label='Telecommunications', mentat_key='telecom', opensecrets_codes=['B09'], code='B09', ibisworld_code='51721+51711a', lobbying_only=False,
                   capabilities=dict(pac=True, control=False, lobbying=True), withheld=dict(control='general-giving model only'))
        bundle['industries']['telecom'] = row
        cm = bundle['cycle_model']
        self.assertTrue(any('no entry for telecom' in p for p in contract.check_cycle_model(bundle)))                  # offered for giving: the step block and the path are required
        for measure in ('pac_to_candidates', 'general_giving'):
            cm['measures'][measure]['step']['telecom'] = copy.deepcopy(cm['measures'][measure]['step']['property_casualty'])
        self.assertTrue(any('budget_rule.growth: no entry for telecom' in p for p in contract.check_cycle_model(bundle)))
        cm['budget_rule']['growth']['telecom'] = dict(copy.deepcopy(cm['budget_rule']['growth']['property_casualty']), report_id='51721+51711a', edition='2024-10 (51721) + 2024-12 (51711a)')
        cm['g_prior']['telecom'] = copy.deepcopy(cm['g_prior']['property_casualty'])
        self.assertEqual(contract.check_cycle_model(bundle), [])
        self.assertEqual(contract.validate_model(bundle), [])
        doc = copy.deepcopy(bundle)
        doc['cycle_model']['budget_rule']['growth']['telecom']['annual'].pop('2027')
        self.assertTrue(any('telecom: no growth for 2027' in p for p in contract.check_cycle_model(doc)))

    def test_the_lobbying_persistence_block_is_optional_and_typed(self):
        import copy
        example = contract.example('models')
        self.assertIn('persistence', example['lobby_model']['log_refit'])
        without = copy.deepcopy(example)
        del without['lobby_model']['log_refit']['persistence']
        self.assertEqual(contract.validate_model(without), [])                                     # a bundle published before the ruling stays valid (pointer rollback)
        for edit in (lambda p: p['pooled']['1'].pop('q95'), lambda p: p['pooled']['4'], lambda p: p['industries']['commercial_banks']['1'].update(q80=-1), lambda p: p.pop('window'),
                     lambda p: p['industries']['commercial_banks']['1'].update(extra=1)):
            doc = copy.deepcopy(example)
            try:
                edit(doc['lobby_model']['log_refit']['persistence'])
            except KeyError:
                doc['lobby_model']['log_refit']['persistence']['pooled']['4'] = dict(n=1, q80=0.1, q95=0.2)
            self.assertTrue(contract.validate_kind(doc, 'models'))

    def test_the_anchor_odd_year_block_is_optional_typed_and_bounded_by_the_cycle_total(self):
        import copy
        example = contract.example('anchors')
        self.assertEqual(self._anchor_validator(example), [])
        self.assertIn('odd_year', example['industries']['commercial_banks']['general_giving'])
        without = copy.deepcopy(example)
        for ind in without['industries'].values():
            ind['general_giving'].pop('odd_year', None)
            ind['pac_to_candidates'].pop('odd_year', None)
        self.assertEqual(self._anchor_validator(without), [])                       # a snapshot built before the cycle model stays valid
        for edit in (lambda i: i['general_giving'].update(odd_year={'FY24': 1.0}), lambda i: i['general_giving'].update(odd_year={'2024': 'x'}),
                     lambda i: i['general_giving'].update(odd_year=[1.0]), lambda i: i['pac_to_candidates']['odd_year']['2024'].pop('to_house_senate'),
                     lambda i: i['pac_to_candidates']['odd_year']['2024'].update(extra=1)):
            self.assertTrue(self._problems(example, edit))
        self.assertTrue(any('outside 0' in p for p in self._problems(example, lambda i: i['general_giving']['odd_year'].update({'2024': -1.0}))))
        self.assertTrue(any('odd_year.2024: to_house_senate differs' in p for p in self._problems(
            example, lambda i: i['pac_to_candidates']['odd_year']['2024'].update(to_house_senate=1.0))))

    def test_the_anchor_example_validates_and_is_consistent(self):
        example = contract.example('anchors')
        self.assertEqual(contract.validate_kind(example, 'anchors'), [])
        self.assertEqual(contract.check_anchor_invariants(example), [])
        self.assertEqual({'commercial_banks', 'credit_unions'} <= set(example['industries']), True)
        self.assertNotIn('auto_light_duty_manufacturing', example['industries'])       # auto has no Mentat data
        self.assertEqual(example['industries']['community_banks']['forecast_industry'], None)   # separate, never merged into banks

    def test_the_anchor_exclusion_fields_are_optional_and_typed(self):
        import copy
        example = contract.example('anchors')            # built before the 2026-10-01 ruling: carries none of the exclusion fields, and stays valid
        self.assertEqual(contract.validate_kind(example, 'anchors'), [])
        row = {'House': {'D': 1.0, 'R': 2.0, 'other': 0.0}, 'Senate': {'D': 0.0, 'R': 0.0, 'other': 0.0}}

        def edited(edit):
            doc = copy.deepcopy(example)
            banks = doc['industries']['commercial_banks']
            banks['general_giving'].update(excluded_funding_sources=['non_giving'], excluded_non_giving={'2024': 191721.37})
            banks['candidate_receipts'].update(excluded_funding_sources=['non_giving'], excluded_non_giving={'2024': copy.deepcopy(row)})
            edit(banks['general_giving'], banks['candidate_receipts'])
            return contract.validate_kind(doc, 'anchors')
        self.assertEqual(edited(lambda g, c: None), [])
        self.assertTrue(edited(lambda g, c: g.update(excluded_non_giving=[191721.37])))                       # a per-cycle object, not a list
        self.assertTrue(edited(lambda g, c: g.update(excluded_non_giving={'2024': 'a lot'})))                  # numbers
        self.assertTrue(edited(lambda g, c: g.update(excluded_non_giving={'FY24': 1.0})))                      # cycle-year keys
        self.assertTrue(edited(lambda g, c: g.update(excluded_funding_sources=[1])))                           # names
        self.assertTrue(edited(lambda g, c: c['excluded_non_giving'].update({'2024': {'House': row['House']}})))   # both chambers, D / R / other each
        self.assertTrue(edited(lambda g, c: g.update(surprise=1)))                                             # the blocks stay closed objects

    @staticmethod
    def _reporting_example():
        """The anchors example (built BEFORE the 2026-10-01 ruling, so its levels still hold the non_giving receipts) rewritten as a snapshot of the current basis: levels after
        the exclusion + what was left out = the by-source split of the Analysis Amount, and both the general-giving and the candidate block name and report what they left out."""
        import copy
        doc = copy.deepcopy(contract.example('anchors'))
        general, candidates = doc['industries']['commercial_banks']['general_giving'], doc['industries']['commercial_banks']['candidate_receipts']
        left_out = {cycle: split.get('non_giving', 0.0) for cycle, split in general['by_funding_source'].items()}
        general.update(excluded_funding_sources=['non_giving'], excluded_non_giving=left_out,
                       cycles={cycle: round(level - left_out.get(cycle, 0.0), 2) for cycle, level in general['cycles'].items()})
        zero = {'House': {'D': 0.0, 'R': 0.0, 'other': 0.0}, 'Senate': {'D': 0.0, 'R': 0.0, 'other': 0.0}}
        candidates.update(excluded_funding_sources=['non_giving'], excluded_non_giving={cycle: copy.deepcopy(zero) for cycle in candidates['cycles']})
        return doc

    def _problems(self, doc, edit):
        import copy
        mutated = copy.deepcopy(doc)
        edit(mutated['industries']['commercial_banks'])
        return self._anchor_validator(mutated)

    def test_a_reported_anchor_exclusion_must_add_up_to_the_funding_source_split(self):
        doc = self._reporting_example()
        self.assertEqual(self._anchor_validator(doc), [])                     # levels after the exclusion + what was left out = the split of the Analysis Amount
        self.assertTrue(any('cycles + excluded_non_giving' in p for p in self._problems(doc, lambda i: i['general_giving']['cycles'].update(
            {'2026': i['general_giving']['cycles']['2026'] + 5}))))
        self.assertTrue(any('excluded_non_giving.2024' in p for p in self._problems(doc, lambda i: i['general_giving']['excluded_non_giving'].update(
            {'2024': i['general_giving']['excluded_non_giving']['2024'] + 5}))))
        self.assertTrue(any('excluded_non_giving.2024' in p for p in self._problems(doc, lambda i: i['general_giving'].update(excluded_funding_sources=[]))))   # the names say what was left out

    def test_a_block_that_reports_the_exclusion_reports_both_the_names_and_the_amounts(self):
        doc = self._reporting_example()
        for block in ('general_giving', 'candidate_receipts'):
            with self.subTest(block=block):
                names_only = self._problems(doc, lambda i: i[block].pop('excluded_non_giving'))
                self.assertTrue(any(block + ': excluded_funding_sources is reported without excluded_non_giving' in p for p in names_only), names_only)
                amounts_only = self._problems(doc, lambda i: i[block].pop('excluded_funding_sources'))
                self.assertTrue(any(block + ': excluded_non_giving is reported without excluded_funding_sources' in p for p in amounts_only), amounts_only)
        # the case a reviewer probed: pre-ruling levels (the example's own: they still hold the non_giving receipts) that merely name the bucket they claim to have left out
        legacy = self._problems(contract.example('anchors'), lambda i: i['general_giving'].update(excluded_funding_sources=['non_giving']))
        self.assertTrue(any('excluded_funding_sources is reported without excluded_non_giving' in p for p in legacy), legacy)

    def test_the_general_giving_and_candidate_blocks_report_the_exclusion_together_or_not_at_all(self):
        doc = self._reporting_example()
        for block in ('general_giving', 'candidate_receipts'):
            with self.subTest(block=block):
                problems = self._problems(doc, lambda i: [i[block].pop(key) for key in ('excluded_funding_sources', 'excluded_non_giving')])
                self.assertTrue(any('must both report the non_giving exclusion or neither' in p for p in problems), problems)
        # neither block reporting is the pre-ruling format: valid (the example itself)
        self.assertEqual(self._anchor_validator(contract.example('anchors')), [])

    def test_the_candidate_exclusion_has_one_entry_per_candidate_cycle_and_the_general_blocks_sources(self):
        doc = self._reporting_example()
        cycles = doc['industries']['commercial_banks']['candidate_receipts']['cycles']
        cycle = sorted(cycles)[0]
        missing = self._problems(doc, lambda i: i['candidate_receipts']['excluded_non_giving'].pop(cycle))
        self.assertTrue(any('candidate_receipts.excluded_non_giving: its cycles differ from the candidate cycles' in p for p in missing), missing)
        extra = self._problems(doc, lambda i: i['candidate_receipts']['excluded_non_giving'].update({'1999': i['candidate_receipts']['excluded_non_giving'][cycle]}))
        self.assertTrue(any('its cycles differ from the candidate cycles' in p for p in extra), extra)
        for names in ([], ['non_giving', 'pac_activity'], ['pac_activity']):
            with self.subTest(names=names):
                problems = self._problems(doc, lambda i: i['candidate_receipts'].update(excluded_funding_sources=names))
                self.assertTrue(any('candidate_receipts.excluded_funding_sources: differs from general_giving.excluded_funding_sources' in p for p in problems), problems)

    def _anchor_validator(self, doc):
        return contract.validate_kind(doc, 'anchors') or contract.check_anchor_invariants(doc)

    def test_every_anchor_negative_control_fails_validation_or_the_invariants(self):
        self.assertEqual(self._accepted_controls(self._anchor_validator, 'anchors'), [])
        self.assertEqual(len([c for c in contract.negative_controls() if c['target'] == 'anchors']), 20)

    def test_a_schema_only_validator_misses_the_invariant_controls(self):
        # The invariant controls are schema-valid by design: only the cross-field checks catch them.
        missed = self._accepted_controls(lambda doc: contract.validate_kind(doc, 'anchors'), 'anchors')
        kinds = {c['id']: c['kind'] for c in contract.negative_controls() if c['target'] == 'anchors'}
        self.assertEqual(sorted(missed), sorted(i for i, k in kinds.items() if k == 'invariant'))

    def test_the_example_itself_is_not_changed_by_a_mutation(self):
        example = contract.example('models')
        before = json.dumps(example, sort_keys=True)
        contract.apply_mutation(example, {'op': 'delete', 'path': ['pac_model']})
        self.assertEqual(json.dumps(example, sort_keys=True), before)

    def test_a_v1_bundle_is_not_a_v2_bundle(self):
        v1 = contract.read_json(ROOT / 'app' / 'data' / 'giving-models-v1.json')
        problems = contract.validate_kind(v1, 'models')
        self.assertTrue(any('schema_version' in p for p in problems))
        self.assertTrue(any('anchor_contract' in p for p in problems))

    def test_validator_reports_each_kind_of_violation(self):
        doc = {'type': 'object', 'properties': {'a': {'type': 'integer', 'minimum': 1}, 'b': {'type': 'string', 'pattern': '^x'}},
               'required': ['a'], 'additionalProperties': False}
        self.assertEqual(contract.validate({'a': 1, 'b': 'xy'}, doc), [])
        self.assertTrue(contract.validate({'b': 'xy'}, doc))
        self.assertTrue(contract.validate({'a': 0}, doc))
        self.assertTrue(contract.validate({'a': 1, 'b': 'y'}, doc))
        self.assertTrue(contract.validate({'a': 1, 'c': 1}, doc))
        self.assertTrue(contract.validate({'a': True}, doc))            # a boolean is not an integer
        self.assertTrue(contract.validate({'a': float('inf')}, doc))     # non-finite numbers are rejected
        self.assertTrue(contract.validate({'a': 1.5}, doc))
        self.assertEqual(contract.validate({'a': 2.0}, doc), [])        # an integral float is an integer (R writes 2024 as 2024.0)

    def test_validator_refuses_keywords_outside_the_subset(self):
        with self.assertRaises(ValueError):
            contract.validate({}, {'type': 'object', 'oneOf': []})

    def test_the_bundle_example_has_no_non_finite_numbers(self):
        def walk(node):
            if isinstance(node, float):
                self.assertTrue(math.isfinite(node))
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(contract.example('models'))


if __name__ == '__main__':
    unittest.main()
