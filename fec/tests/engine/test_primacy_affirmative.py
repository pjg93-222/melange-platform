"""Affirmative advocacy, strict source boundaries, and fixed Tier 1 eligibility."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine import primacy_factors as PF, build_primacy_surfaces as BUILD
import tier_util as TU


class PositiveContributions(unittest.TestCase):
    def score(self, acts, cells=None, adjudicated=None):
        tags = {bill+'|119':tag for bill,role,tag in acts}
        bundle = {'members':[{'name':'A','bills':[
            {'bill':bill,'congress':'119','role':role} for bill,role,tag in acts]}]}
        return PF.compute(bundle,tags,adjudicated,cu_cells=cells)[0]['A']

    def test_role_mass_and_amendment_mass_are_applied_once(self):
        acts = [('H.R.1','sponsor',{'class':'cu_primary'}),
                ('H.R.2','original',{'class':'cu_primary'}),
                ('H.R.3','cosponsor',{'class':'cu_primary'}),
                ('S.Amdt.1','sponsor',{'class':'cu_primary'})]
        self.assertEqual(self.score(acts)[PF.AFFIRMATIVE_PRIMARY_FIELD],2.25)

    def test_shared_support_and_ordinary_letters_do_not_meet_primary_gate(self):
        row=self.score([('H.R.1','sponsor',{'class':'shared','acu_position':'SUPPORT'}),
                        ('LTR-A','sponsor',{'class':'cu_primary'})])
        self.assertEqual(row[PF.AFFIRMATIVE_PRIMARY_FIELD],0)

    def test_negative_neutral_partial_and_tensor_precedence(self):
        examples=[({'class':'cu_primary','acu_position':'OPPOSE'},None,0),
            ({'class':'cu_primary','authority':'automated-official-evidence-policy',
              'automated_category_directions':[1,-1]},None,0),
            ({'class':'cu_primary','authority':'automated-official-evidence-policy',
              'automated_category_directions':[1,1,-1]},None,1/3),
            ({'class':'cu_primary','acu_position':'OPPOSE'},{('H.R.1','119'):.5},.5),
            ({'class':'cu_primary','acu_position':'SUPPORT'},{('H.R.1','119'):-1},0)]
        for tag,cells,want in examples:
            with self.subTest(tag=tag,cells=cells):
                self.assertAlmostEqual(self.score([('H.R.1','sponsor',tag)],cells)[PF.AFFIRMATIVE_PRIMARY_FIELD],want)

    def test_adjudicated_primary_credit_counts_once(self):
        credits={'A':{'denominator_delta':1,'numerator_delta':1,
                      'mix':{'cu_primary':1},'events':['approved']}}
        row=self.score([('H.R.1','original',{'class':'cu_primary'})],adjudicated=credits)
        self.assertEqual(row[PF.AFFIRMATIVE_PRIMARY_FIELD],1.5)
        self.assertEqual(row['agenda_credit'],1.5)

    def test_duplicate_act_cannot_double_support(self):
        act=('H.R.1','sponsor',{'class':'cu_primary'})
        with self.assertRaisesRegex(ValueError,'duplicate primacy act'):
            self.score([act,act])

    def test_adjudication_boolean_cannot_manufacture_one_credit(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'credits.ndjson'
            for value in (True,False):
                path.write_text(json.dumps({'member':'A','event_id':'e','beneficiary_class':'cu_primary','role_weight':value}),encoding='utf-8')
                with self.subTest(value=value), self.assertRaisesRegex(ValueError,'boolean'):
                    PF.load_adjudicated_credits(path)

    def test_revert_omits_new_member_field(self):
        with patch.object(PF,'PRIMACY_T1_MIN_AFFIRMATIVE',0):
            self.assertNotIn(PF.AFFIRMATIVE_PRIMARY_FIELD,self.score([('H.R.1','sponsor',{'class':'cu_primary'})]))


class AffirmativeEligibility(unittest.TestCase):
    def test_net_positive_raw_record_is_required(self):
        floors=TU.affirmative_slot_floors(list('ABCD'),
            {'A':(1,-.1),'B':(1,0),'C':(.74,.9),'D':(.75,.1)},buckets=(1,1,1))
        self.assertEqual(floors,{'A':1,'B':1,'C':1})
        self.assertEqual(TU._reseat_with_floors(list('ABCD'),floors),list('DABC'))

    def test_joint_conduct_can_make_thirteen_seats_infeasible(self):
        order=['M%02d'%i for i in range(15)]
        evidence={m:(1,.5) if i<13 else (0,.5) for i,m in enumerate(order)}
        floors=TU.affirmative_slot_floors(order,evidence)
        floors['M00']=13  # conduct excludes one of the only13 affirmative members
        with self.assertRaisesRegex(ValueError,'Infeasible'):
            TU._reseat_with_floors(order,floors)

    def test_invalid_values_and_missing_members_refuse(self):
        for value in (True,None,float('nan'),float('inf'),-1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TU.affirmative_slot_floors(['A'],{'A':(value,.5)},buckets=(1,1,1))
        with self.assertRaises(ValueError): TU.affirmative_slot_floors(['A'],{},buckets=(1,1,1))
        for cutoff in (True,-1,float('nan'),float('inf')):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                TU.affirmative_slot_floors(['A'],{'A':(1,.5)},minimum=cutoff,buckets=(1,1,1))

    def test_disabled_gate_requires_no_evidence(self):
        self.assertEqual(TU.affirmative_slot_floors(['A'],{},minimum=0),{})

    def test_pin_cannot_override_affirmative_gate(self):
        from importlib.util import spec_from_file_location,module_from_spec
        spec=spec_from_file_location('composition',Path(__file__).with_name('test_primacy_constraint_composition.py'))
        module=module_from_spec(spec);spec.loader.exec_module(module)
        board=module.BoardEntrypoints()
        tiers,ranks,_=board.board('per_area_v1',unqualified='M89',overrides={'M89':1},positions={'M89':1},conduct_member=None)
        self.assertNotEqual(tiers['M89'],1)
        self.assertGreater(ranks['M89'],13)


class EvidenceMigration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=self.tmp.name
        self.path=Path(self.root)/TU.PRIMACY_PATH;self.path.parent.mkdir(parents=True)
        self.valid={'_constants':{'affirmative_primary_credit_policy':PF.AFFIRMATIVE_PRIMARY_POLICY},
                    'members':{'A':{PF.AFFIRMATIVE_PRIMARY_FIELD:1,'factor_raw':.5}}}

    def write(self,doc): self.path.write_text(json.dumps(doc),encoding='utf-8')

    def test_valid_artifact_and_normal_sequence_need_no_seed(self):
        self.write(self.valid)
        self.assertEqual(TU.load_primacy_affirmative_evidence(self.root),{'A':(1,.5)})
        self.assertFalse(TU.affirmative_schema_seed_required(self.root))
        steps=(('engine/primacy_factors.py',self.root),)
        self.assertEqual(BUILD.with_affirmative_factor_seed(steps,self.root),steps)

    def test_missing_schema_seeds_actual_producer_before_analytic_desk(self):
        self.write({'members':{'A':{'factor_raw':.5}}})
        steps=(('surfaces/make_member_desk.py','--data','--analytic-inputs-only'),
               ('engine/primacy_factors.py',self.root))
        self.assertEqual(BUILD.with_affirmative_factor_seed(steps,self.root),
                         (('engine/primacy_factors.py',self.root),)+steps)
        with self.assertRaisesRegex(ValueError,'contract is missing'):
            TU.load_primacy_affirmative_evidence(self.root)

    def test_bad_present_evidence_is_not_silently_recomputed(self):
        for value in (True,None,-1,float('nan'),float('inf')):
            self.valid['members']['A'][PF.AFFIRMATIVE_PRIMARY_FIELD]=value
            self.write(self.valid)
            with self.subTest(value=value), self.assertRaises(ValueError):
                TU.affirmative_schema_seed_required(self.root)

    def test_unknown_policy_refuses(self):
        self.valid['_constants']['affirmative_primary_credit_policy']='unknown'
        self.write(self.valid)
        with self.assertRaisesRegex(ValueError,'unknown affirmative'):
            TU.affirmative_schema_seed_required(self.root)

    def test_legacy_mode_and_revert_do_not_seed(self):
        steps=(('engine/primacy_factors.py',self.root),)
        with patch.object(TU,'PRIMACY_BOARD','legxfactor'):
            self.assertEqual(BUILD.with_affirmative_factor_seed(steps,self.root),steps)
        with patch.object(TU,'PRIMACY_T1_MIN_AFFIRMATIVE',0):
            self.assertEqual(BUILD.with_affirmative_factor_seed(steps,self.root),steps)


if __name__=='__main__': unittest.main()
