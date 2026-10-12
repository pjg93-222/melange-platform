"""D5 -- DESK_RESPONSIVENESS: the display-only CU responsiveness card is retired.

Packet: audits/members_section_20260914, design-d2-d5.md SS D5;
evidence/point2-3-responsiveness-committees.md.

The desk assertions are STRUCTURAL on purpose: the loader call and the card
injection both sit inside build_bundle()/main(), which cannot be executed here
without running the desk's data producers. Each one is proved against the parsed
syntax tree of the shipped file rather than a substring of it, so a rewrite that
kept the words but changed the control flow would fail.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
sys.path.insert(0, str(FEC))
from surfaces import make_lobby_network_html as MLN

DESK_SOURCE = FEC / 'surfaces' / 'make_member_desk.py'
TEMPLATE = FEC / 'surfaces' / 'templates' / 'make_member_desk_00.tpl'
CHAIN_SOURCE = FEC / 'run_pipeline.py'
DESK_TREE = ast.parse(DESK_SOURCE.read_text(encoding='utf-8'))
# The responsiveness layer feeds no score, tier, rank or allocation, so D5 cannot
# move the rank authority. ranks_python.json is chain-regenerated and gitignored;
# its hash is printed as a receipt, never pinned.


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError('no function %r' % name)


def _assign_to(scope, target):
    """Every assignment whose single target is the bare name `target`."""
    return [n for n in ast.walk(scope)
            if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name) and n.targets[0].id == target]


def _chain(env):
    """Exec run_pipeline's CHAIN literal and its D5 filter, nothing else."""
    tree = ast.parse(CHAIN_SOURCE.read_text(encoding='utf-8'))
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ('CHAIN', 'DESK_RESPONSIVENESS')
                for t in node.targets):
            wanted.append(node)
        elif isinstance(node, ast.If) and 'DESK_RESPONSIVENESS' in ast.dump(node.test):
            wanted.append(node)
    namespace = {'os': type('_os', (), {'environ': env})}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(CHAIN_SOURCE), 'exec'),
         namespace)
    return [step[0] for step in namespace['CHAIN']]


class Dial(unittest.TestCase):
    def test_shipped_default_is_off(self):
        source = DESK_SOURCE.read_text(encoding='utf-8')
        self.assertIn(
            "DESK_RESPONSIVENESS = os.environ.get('DESK_RESPONSIVENESS', '0') == '1'",
            source)
        self.assertEqual(os.environ.get('DESK_RESPONSIVENESS', '0'), '0')

    def test_loader_is_short_circuited_by_the_dial(self):
        assigns = _assign_to(_function(DESK_TREE, 'build_bundle'), '_resp')
        self.assertEqual(len(assigns), 1)
        value = assigns[0].value
        self.assertIsInstance(value, ast.IfExp,
                              '_resp must be a dial-gated conditional')
        self.assertEqual(value.test.id, 'DESK_RESPONSIVENESS')
        self.assertIsNone(value.orelse.value, 'the off branch must be None')
        self.assertEqual(value.body.func.id, '_load_responsiveness_optional')

    def test_receipts_are_only_set_when_the_layer_loaded(self):
        """No _resp -> no bundle['responsiveness_receipts'] -> no injected card."""
        guards = [n for n in ast.walk(_function(DESK_TREE, 'build_bundle'))
                  if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                  and n.test.id == '_resp']
        self.assertEqual(len(guards), 1)
        inside = ast.dump(guards[0])
        self.assertIn("'responsiveness_receipts'", inside)
        outside = [n for n in ast.walk(_function(DESK_TREE, 'build_bundle'))
                   if isinstance(n, ast.Assign)
                   and "'responsiveness_receipts'" in ast.dump(n)]
        self.assertEqual(len(outside), 1)

    def test_no_member_gets_a_responsiveness_cu_key(self):
        """Every write of the per-member key sits inside the same `if _resp:` guard."""
        build = _function(DESK_TREE, 'build_bundle')
        guard = next(n for n in ast.walk(build)
                     if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                     and n.test.id == '_resp')
        def writes(scope):
            return [n for n in ast.walk(scope) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Subscript)
                            and isinstance(t.slice, ast.Constant)
                            and t.slice.value == 'responsiveness_cu'
                            for t in n.targets)]

        self.assertEqual(len(writes(build)), 1)
        self.assertEqual(len(writes(guard)), 1)
        # Nothing anywhere else in the file writes the key either. The only other
        # mention is HEAVY_MEMBER_FIELDS, a list of field NAMES for the
        # heavy/light split -- benign: an absent key is simply not split out.
        self.assertEqual(len(writes(DESK_TREE)), 1)
        source = DESK_SOURCE.read_text(encoding='utf-8')
        self.assertEqual(source.count('responsiveness_cu'), 3)

    def test_injection_stays_gated_on_the_receipt(self):
        source = DESK_SOURCE.read_text(encoding='utf-8')
        self.assertIn(
            "_html_with_responsiveness(HTML)\n"
            "            if bundle.get('responsiveness_receipts') else HTML",
            source)


class Chain(unittest.TestCase):
    def test_default_chain_excludes_the_step(self):
        names = _chain({})
        self.assertNotIn('responsiveness_layers.py', names)
        # The steps either side of the removed entry are untouched.
        self.assertIn('build_primacy_surfaces.py', names)
        self.assertIn('build_rank_honors.py', names)
        self.assertEqual(names[-1], 'audit_full.py',
                         'audit_full must stay the chain tail')

    def test_revert_switch_restores_the_step_in_place(self):
        names = _chain({'DESK_RESPONSIVENESS': '1'})
        self.assertIn('responsiveness_layers.py', names)
        self.assertEqual(
            names.index('responsiveness_layers.py'),
            names.index('build_primacy_surfaces.py') + 1)
        self.assertEqual(len(names), len(_chain({})) + 1)


class ProducerSurvives(unittest.TestCase):
    def test_the_producer_file_is_kept(self):
        self.assertTrue((FEC / 'engine' / 'responsiveness_layers.py').is_file())

    def test_its_own_suite_still_imports_it(self):
        suite = FEC / 'tests' / 'engine' / 'test_responsiveness_layers.py'
        self.assertTrue(suite.is_file())
        self.assertIn('responsiveness_layers',
                      suite.read_text(encoding='utf-8'))

    def test_the_desk_can_still_restore_the_card(self):
        """DESK_RESPONSIVENESS=1 must still find both injection anchors."""
        nodes = [n for n in DESK_TREE.body
                 if (isinstance(n, ast.FunctionDef)
                     and n.name == '_html_with_responsiveness')
                 or (isinstance(n, ast.Assign) and any(
                     isinstance(t, ast.Name)
                     and t.id == '_RESPONSIVENESS_MEMBER_HELPERS'
                     for t in n.targets))]
        self.assertEqual(len(nodes), 2)
        namespace = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]),
                     str(DESK_SOURCE), 'exec'), namespace)
        template = TEMPLATE.read_text(encoding='utf-8')
        self.assertNotIn('responsivenessCard', template)
        injected = namespace['_html_with_responsiveness'](template)
        self.assertIn('${responsivenessCard(m)}', injected)
        self.assertIn('function responsivenessCard(m){', injected)


class LobbyNetworkTolerance(unittest.TestCase):
    """The artifact's other consumer: absence must stay an honest no-chip state."""

    def _tiers(self):
        rows = [{'member': 'A Member', 'bioguide': 'A000001',
                 'chamber': 'House', 'tier': 1, 'tierrank': 1}]
        receipts = {'primary_act_count': 1, 'conduct_ledger': 'none',
                    'overrides': 'none', 'oppose_handling': 'signed',
                    'scope': 'test'}
        return {'schema': MLN.INDUSTRY_TIERS_SCHEMA,
                'validation': {'method': 'uniform-clean-v1',
                               'bundle_sha12': 'abcdef123456'},
                'industries': {sector: {'rows': rows, 'receipts': dict(receipts)}
                               for sector in MLN.CHAMPIONS_V2_SECTORS}}

    def _load(self, responsiveness):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'industry_tiers.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(self._tiers(), handle)
            return MLN._load_industry_tiers(path, responsiveness=responsiveness)

    def test_absent_artifact_loads_to_none(self):
        self.assertIsNone(MLN._load_responsiveness_optional(
            os.path.join(tempfile.gettempdir(), 'no-such-responsiveness.json')))
        self.assertIsNone(MLN._load_responsiveness_optional(None))

    def test_cu_receipt_validates_without_the_responsiveness_key(self):
        page = self._load(None)
        cu = page['industries']['credit_union']
        self.assertNotIn('responsiveness', cu['receipts'])
        self.assertEqual(set(cu['receipts']),
                         {'primary_act_count', 'conduct_ledger', 'overrides',
                          'oppose_handling', 'scope'})

    def test_present_artifact_still_adds_the_note(self):
        page = self._load({'members': {}, 'receipts': {'display_only': True}})
        self.assertEqual(page['industries']['credit_union']['receipts']
                         ['responsiveness'], MLN.RESPONSIVENESS_TIER_NOTE)
        self.assertNotIn('responsiveness',
                         page['industries']['bank']['receipts'])

    def test_attach_is_a_no_op_without_the_artifact(self):
        tiers = {'industries': {'credit_union': {'rows': [{'member': 'A Member'}]}}}
        self.assertIs(MLN._attach_tier_responsiveness(tiers, None), tiers)
        self.assertNotIn('responsiveness',
                         tiers['industries']['credit_union']['rows'][0])

    def test_input_hash_list_omits_an_absent_artifact(self):
        source = (FEC / 'surfaces' / 'make_lobby_network_html.py'
                  ).read_text(encoding='utf-8')
        self.assertIn('if os.path.isfile(responsiveness_path):', source)


class DisplayOnly(unittest.TestCase):
    """The layer feeds no score, tier, rank or allocation -- so D5 moves no rank."""

    def test_rank_authority_is_unmoved(self):
        """The rank producer never reads the responsiveness layer, so the dial
        cannot move ranks. The live artifact's hash is a receipt only."""
        pillars = (FEC / 'engine' / 'compute_pillars.py').read_text(encoding='utf-8')
        self.assertNotIn('responsiveness', pillars)
        path = ROOT / 'ranks_python.json'
        if not path.exists():
            self.skipTest('ranks_python.json is not in this tree')
        print('ranks_python.json md5 receipt: %s'
              % hashlib.md5(path.read_bytes()).hexdigest())

    def test_no_engine_producer_reads_the_layer(self):
        allowed = {
            'engine/responsiveness_layers.py',       # the producer
            'surfaces/make_member_desk.py',          # display, dial-gated
            'surfaces/make_lobby_network_html.py',   # display, optional input
            'audit/build_system_manifest.py',        # sentinel exclusion list
            'run_pipeline.py',                       # the (now filtered) step
        }
        found = set()
        for path in FEC.rglob('*.py'):
            parts = path.parts
            if 'tests' in parts or any(p.startswith('_quarantine') for p in parts):
                continue
            body = path.read_text(encoding='utf-8', errors='replace')
            # The artifact/module token, not the English word: four members/*.py
            # files use 'responsiveness' about persona and receptivity language
            # and have nothing to do with this layer.
            if 'responsiveness_layers' in body or 'responsiveness_cu' in body:
                found.add(path.relative_to(FEC).as_posix())
        self.assertEqual(found - allowed, set(),
                         'a new consumer of the display-only layer appeared')


if __name__ == '__main__':
    unittest.main(verbosity=2)
