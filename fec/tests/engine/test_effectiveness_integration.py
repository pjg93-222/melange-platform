"""Shadow reporting, board, priority, and explanation integration contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from engine import effectiveness_integration as EI
from engine.live_legislative_pillar import from_effectiveness
from engine.primacy_factors import with_realized_results
import tier_util as TU
import moneyball_core as MC


def row(bioguide, chamber, results, advocacy, tensor=None, count=1):
    bucket = lambda value: {"net": value, "positive": max(value or 0,0), "negative": max(-(value or 0),0), "distinct_measures": count}
    return {"bioguide_id":bioguide, "chamber":chamber, "congress":118,
            "standard":{"results":bucket(results),"advocacy":bucket(advocacy)},
            "tensor":{"results":bucket(tensor if tensor is not None else results),"advocacy":bucket(advocacy)},
            "institutional_context":[{"role":"chair"}],"coverage":{}}


def comparison():
    source = {"members":[row('A','House',10,1),row('B','House',5,20),
                         row('C','Senate',100,1),row('D','Senate',90,2),
                         row('E','House',None,None,count=0)],
              "terms":{}, "as_of":"2025-01-03", "policy":{"policy_status":"shadow_candidate"},
              "contributions":[{"bioguide_id":"A","congress":118,"chamber":"House","measure_id":"118:hr:1","stage":"LAW","role":"sponsor","unit":.1,"denominator":50,"impact_weight":5,"evidence":"<script>bad()</script>"}]}
    identities = [{"bioguide":name,"name":name,"chamber":chamber} for name,chamber in [('A','House'),('B','House'),('C','Senate'),('D','Senate'),('E','House')]]
    legacy = {"pcts":{name:{"leg":.5,"press":.2,"inf":.3,"dem":.4} for name in 'ABCDE'},
              "rank":dict(zip('ABCDE',range(1,6))),"score":{name:.5 for name in 'ABCDE'}}
    factors = {"members":{name:{"factor_ranked":.8,"mix":{"cu_primary":100}} for name in 'ABCDE'}}
    return EI.build_shadow_comparison(source,identities,legacy,factors,congress=118)


class ReportingIntegration(unittest.TestCase):
    def test_chamber_percentile_bypasses_pooled_second_ranking(self):
        candidate = comparison()
        # House10 and Senate100 both top their chamber; no raw cross-chamber ranking.
        self.assertEqual(candidate['members']['A']['results_only_leg_percentile'],1)
        self.assertEqual(candidate['members']['C']['results_only_leg_percentile'],1)
        provided = {'A':.75,'B':.9,'C':.25}
        self.assertEqual(EI.legislative_percentiles({},mode=EI.RESULTS_MODE,provided=provided),provided)
        self.assertNotEqual(EI.legislative_percentiles(provided),provided)
        self.assertEqual(from_effectiveness(candidate,mode=EI.RESULTS_MODE)['pillar']['A'],1)

    def test_composite_retains_advocacy_and_outer_policy(self):
        candidate=comparison()['members']
        self.assertEqual(candidate['A']['advocacy_composite_leg_percentile'],.3)
        self.assertEqual(candidate['B']['advocacy_composite_leg_percentile'],.7)
        self.assertGreater(candidate['A']['results_only_score'],candidate['B']['results_only_score'])
        self.assertLess(candidate['A']['advocacy_composite_score'],candidate['B']['advocacy_composite_score'])
        self.assertAlmostEqual(candidate['A']['results_only_score'],.45+.10*.2+.40*.3+.05*.4)

    def test_missing_signal_never_gets_fabricated_rank(self):
        member=comparison()['members']['E']
        self.assertIsNone(member['results_only_score'])
        self.assertIsNone(member['results_only_rank'])
        self.assertIsNone(member['advocacy_composite_leg_percentile'])

    def test_member_changing_chamber_is_not_pooled(self):
        source={'members':[row('A','House',10,1),row('A','Senate',20,2)],'terms':{}}
        doc=EI.build_shadow_comparison(source,[{'name':'A','bioguide':'A','chamber':'Senate'}],{}, {},congress=118)
        self.assertEqual(doc['members']['A']['chamber'],'Senate')
        self.assertEqual(doc['members']['A']['other_chamber_records'][0]['chamber'],'House')

    def test_cli_writes_only_explicit_shadow_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=EI.write_shadow_comparison(comparison(),root/'comparison.json',shadow_root=root)
            command=[sys.executable,str(FEC/'engine'/'compute_pillars.py'),'--effectiveness-shadow',str(path),
                     '--shadow-root',str(root),'--mode',EI.RESULTS_MODE,'--output',str(root/'ranks.json')]
            run=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            output=json.loads((root/'ranks.json').read_text())
            self.assertEqual(output['pcts']['A']['leg'],1)
            self.assertIsNone(output['rank']['E'])
            with self.assertRaises(ValueError): EI.require_shadow_path(root.parent/'escape.json',root)


class BoardIntegration(unittest.TestCase):
    def test_results_gate_counts_distinct_measures_and_retains_conduct(self):
        doc=comparison()
        doc['members']['A']['distinct_signed_measures']=0
        doc['members']['B']['realized_results']=-1
        # Four qualified candidates can fill three tiers and honor C's floor.
        doc['members']['F']=dict(doc['members']['B'], realized_results=-2)
        with tempfile.TemporaryDirectory() as root, patch.object(TU,'load_tier_overrides',return_value={}),patch.object(TU,'load_tier_positions',return_value={}),patch.object(TU,'load_open_breaches',return_value={'C':{'effective_severity':9.0,'events':[]}}):
            tiers,ranks,detail=TU.realized_results_tiers(list('ABCDEF'),doc,root=root,with_detail=True,buckets=(1,1,1))
            self.assertEqual(tiers['A'],0)
            self.assertEqual(ranks['A'],0)
            self.assertEqual(tiers['E'],0)
            self.assertEqual(tiers['C'],0)
            self.assertEqual(detail['B']['orientation_factor'],.8)
            self.assertEqual(detail['B']['realized_results'],-1)
            self.assertEqual(ranks['C'],4)

    def test_results_short_roster_refuses_impossible_conduct_seat(self):
        doc=comparison()
        doc['members']['A']['distinct_signed_measures']=0
        with tempfile.TemporaryDirectory() as root, patch.object(TU,'load_tier_overrides',return_value={}),patch.object(TU,'load_tier_positions',return_value={}),patch.object(TU,'load_open_breaches',return_value={'C':{'effective_severity':9.0,'events':[]}}):
            with self.assertRaisesRegex(ValueError, 'Infeasible tier seating at rank 3 of 3'):
                TU.realized_results_tiers(list('ABCDE'),doc,root=root,buckets=(1,1,1))

    def test_allocation_priority_consumer_reads_explicit_results_board(self):
        with tempfile.TemporaryDirectory() as root, patch.object(TU,'load_tier_overrides',return_value={}),patch.object(TU,'load_tier_positions',return_value={}):
            path=EI.write_shadow_comparison(comparison(),Path(root)/'comparison.json',shadow_root=root)
            tier,rank,priority=MC.primacy_engine_state(list('ABCDE'),mode=EI.BOARD_MODE,
                effectiveness_path=path,shadow_root=root,root=root)
            self.assertEqual(tier['E'],0)
            self.assertEqual(priority['E'],0)
            self.assertEqual(priority['A'],MC.primacy_engine_value(tier['A']))
            self.assertGreater(rank['B'],rank['A'])

    def test_legacy_factors_are_preserved_in_candidate_sidecar(self):
        original={'members':{'A':{'factor_ranked':.25,'factor_shrunk':.3}}}
        out=with_realized_results(original,comparison())
        self.assertEqual(out['members']['A']['factor_ranked'],.25)
        self.assertEqual(out['members']['A']['realized_results'],10)
        self.assertNotIn('realized_results',original['members']['A'])

    def test_environment_cannot_switch_production_root(self):
        with tempfile.TemporaryDirectory() as root:
            path=EI.write_shadow_comparison(comparison(),Path(root)/'comparison.json',shadow_root=root)
            with patch.object(TU,'PRIMACY_BOARD',EI.BOARD_MODE):
                with self.assertRaises(ValueError):
                    TU.legislative_press_tiers(['A'],root=str(FEC.parent),effectiveness_path=path,shadow_root=root)

    def test_another_live_checkout_cannot_be_a_shadow_root(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)
            (source/'.git').mkdir()
            (source/'Moneyball Main Version - Blended.xlsx').touch()
            with self.assertRaises(ValueError):
                EI.require_shadow_path(source/'candidate.json',source)


class AllocationReplay(unittest.TestCase):
    def test_dollar_comparison_is_per_topic_and_retains_budget_changes(self):
        from engine.replay_effectiveness_allocations import summarize
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'old.json').write_text(json.dumps({'x':{'A':1000,'B':1000}}))
            (root/'new.json').write_text(json.dumps({'x':{'B':1000,'C':500}}))
            result=summarize(root/'old.json',root/'new.json')
            self.assertEqual(result['summary']['member_topic_absolute_dollar_change'],1500)
            self.assertEqual(result['summary']['baseline_topic_budget_sum'],2000)
            self.assertEqual(result['summary']['candidate_topic_budget_sum'],1500)

    def test_native_worker_cannot_write_to_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';shadow=root/'shadow';output=shadow/'worker'
            (source/'fec'/'engine').mkdir(parents=True);shadow.mkdir()
            (source/'fec'/'moneyball_core.py').write_text('def primacy_engine_state(roster=None): return ({},{},{})\ndef dump_atomic(*args,**kwargs): pass\n')
            forbidden=source/'forbidden.txt'
            (source/'fec'/'engine'/'per_topic_flip.py').write_text('open('+repr(str(forbidden))+', "w").write("bad")\n')
            board={'schema':'effectiveness-board-priority-comparison-v1','status':'shadow_candidate','members':{}}
            (shadow/'board.json').write_text(json.dumps(board))
            command=[sys.executable,str(FEC/'engine'/'replay_effectiveness_allocations.py'),
                     '--source-root',str(source),'--board',str(shadow/'board.json'),'--shadow-root',str(shadow),
                     '--output',str(output),'--engine','flip','--variant','baseline']
            run=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0)
            self.assertIn('blocked write outside shadow output',run.stderr)
            self.assertFalse(forbidden.exists())

    def test_flip_replay_writes_dials_sidecar_against_replayed_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';shadow=root/'shadow';output=shadow/'worker'
            (source/'fec'/'engine').mkdir(parents=True);shadow.mkdir()
            (source/'fec'/'moneyball_core.py').write_text('\n'.join([
                'import json,hashlib,os',
                'def primacy_engine_state(roster=None): return ({},{},{})',
                'def dump_atomic(obj,path,**kw): open(path,"w").write(json.dumps(obj))',
                'def write_engine_dials(artifact_path,sidecar_path=None):',
                '    sidecar_path=sidecar_path or os.path.splitext(artifact_path)[0]+".dials.json"',
                '    sha=hashlib.sha256(open(artifact_path,"rb").read()).hexdigest()',
                '    dump_atomic({"artifact_sha256":sha},sidecar_path)',
                '']))
            (source/'fec'/'engine'/'per_topic_flip.py').write_text('\n'.join([
                'import moneyball_core as MC',
                'outf='+repr(str(source/'per_topic_alloc.json')),
                'MC.dump_atomic({"x":{"A":1}},outf)',
                'MC.write_engine_dials(outf)',
                '']))
            board={'schema':'effectiveness-board-priority-comparison-v1','status':'shadow_candidate','members':{},
                   'source_sha256':'x','summary':{}}
            (shadow/'board.json').write_text(json.dumps(board))
            command=[sys.executable,str(FEC/'engine'/'replay_effectiveness_allocations.py'),
                     '--source-root',str(source),'--board',str(shadow/'board.json'),'--shadow-root',str(shadow),
                     '--output',str(output),'--engine','flip','--variant','baseline']
            run=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertFalse((source/'per_topic_alloc.json').exists())
            sidecar=json.loads((output/'per_topic_alloc.dials.json').read_text())
            receipt=json.loads((output/'receipt.json').read_text())
            artifact_sha=receipt['output_sha256'][str(output/'per_topic_alloc.json')]
            self.assertEqual(sidecar['artifact_sha256'],artifact_sha)
            self.assertIn(str(output/'per_topic_alloc.dials.json'),receipt['sidecar_sha256'])

class Explanations(unittest.TestCase):
    def test_shared_explanations_render_sources_and_escape_text(self):
        doc=comparison()
        desk=EI.attach_shadow_explanations({'members':[{'name':'A'}]},doc)
        dashboard=EI.attach_shadow_explanations({'name':['A']},doc)
        rendered=desk['members'][0]['effectiveness_shadow_html']
        self.assertEqual(rendered,dashboard['effectiveness_shadow_html'][0])
        self.assertIn('118:hr:1',rendered)
        self.assertIn('denominator',rendered)
        self.assertIn('chair',rendered)
        self.assertNotIn('<script>bad()',rendered)
        for file in ['make_member_desk_00.tpl','make_dashboard_html_00.tpl']:
            self.assertIn('effectiveness_shadow_html',(FEC/'surfaces'/'templates'/file).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
