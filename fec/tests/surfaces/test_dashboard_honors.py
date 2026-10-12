from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from surfaces.dashboard_honors import (dashboard_honors_payload, verify_dashboard_honors,
                                       honors_generation_marker, overall_stances_from_gen)
from surfaces.honors_publication import compact_honor_cell, PUBLIC_HONOR_FIELDS

TEMPLATE = FEC / 'surfaces/templates/make_dashboard_html_00.tpl'
NODE = shutil.which('node') or str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')


def document():
    no_action = {'evidence_status': 'no_recorded_action', 'recognition': None,
                 'alignment': 'No recorded action', 'action_count': 0,
                 'rank_start': None, 'rank_end': None, 'cohort_size': 20,
                 'cutoffs': {'champion': 1, 'ally': 3, 'highly_persuadable': 4},
                 'reason': 'no_recorded_action', 'evidence_ids': []}
    champion = {**no_action, 'evidence_status': 'observed', 'recognition': 'Champion',
                'alignment': 'Supportive actions', 'action_count': 3,
                'positive_action_count': 3, 'raw_field_score': '9',
                'rank_start': 1, 'rank_end': 1, 'evidence_ids': ['bill:119:hr1']}
    tied = {**champion, 'recognition': None, 'rank_start': 2, 'rank_end': 5,
            'tensor_status': 'tensor_unavailable_for_tie', 'reason': 'boundary_tie'}
    return {'schema': 'rank-honors-v1', 'generation_id': 'generation-one',
            'as_of': '2026-09-13', 'cats': ['Payments'], 'policy': {'champion_pct': 5},
            'overall_policy': {'id': 'overall_primacy_standing_v2', 'ally_pct': 15,
                               'highly_persuadable_pct': 20, 'persuadable_pct': 50,
                               'opponent_pct': 5, 'highly_skeptical_pct': 15, 'skeptic_pct': 20},
            'members': {
                name: {'member_id': name[0], 'fields': {'Payments': row},
                       'overall': {'recognition': 'Champion' if name == 'Bob' else None,
                                   'rank': [14, 1, 38][index], 'priority_tier': [2, 1, 3][index],
                                   'stance': ['Insufficient evidence', 'Persuadable', 'Ally'][index],
                                   'stance_reason': 'observed_action_rank', 'alignment': row['alignment'],
                                   'stance_rank_start': [None, 40, 10][index],
                                   'stance_rank_end': [None, 40, 10][index], 'stance_cohort_size': 100,
                                   'raw_overall_score': [None, '9', '15'][index],
                                   'action_count': [0, 4, 3][index], 'positive_action_count': [0, 3, 3][index],
                                   'negative_action_count': [0, 1, 0][index], 'neutral_action_count': 0,
                                   'mixed_record': name == 'Bob', 'record_pattern': 'mixed' if name == 'Bob' else 'one_direction',
                                   'stance_cutoffs': {'ally': 15, 'highly_persuadable': 20, 'persuadable': 50},
                                   'opposition_cohort_size': 10, 'opposition_rank_start': None,
                                   'opposition_rank_end': None,
                                   'opposition_cutoffs': {'opponent': 0, 'highly_skeptical': 1, 'skeptic': 2},
                                   'stance_boundary_tie': False}}
                for index, (name, row) in enumerate([('Ann', no_action), ('Bob', champion), ('Cal', tied)])}}


def bundle(doc):
    result = {'name': ['Ann', 'Bob', 'Cal'], 'issues': ['Payments', 'Overall CU stance'],
              'engine': {'categories': ['Payments', None]},
              # Deliberately contradictory legacy media/model inputs. `gen` is no
              # longer among them: D1.c makes it the single overall label, so it
              # decodes to exactly the standings the honors column carries.
              'stance': ['AC', 'NC', 'ON'], 'gen': '-PA', 'tier': [1, 2, 3]}
    # D1.c: the public overall column carries the same single label the page
    # renders from `gen`, not the honors generation's separate primacy band.
    result.update(dashboard_honors_payload(doc, result['name'], ['Payments'],
                                           overall_stances_from_gen(result['name'], result['gen'])))
    return result


class DashboardHonorsTests(unittest.TestCase):
    def test_projection_is_separate_and_does_not_mutate_sources(self):
        doc = document()
        before = deepcopy(doc)
        data = bundle(doc)
        self.assertEqual(data['stance'], ['AC', 'NC', 'ON'])
        self.assertEqual(data['gen'], '-PA')
        self.assertEqual(data['honors'][0][0]['recognition'], None)
        data['honors'][1][0]['recognition'] = 'Ally'
        self.assertEqual(doc, before)

    def test_overall_projection_retains_independent_priority_and_standing_proof(self):
        doc = document()
        data = bundle(doc)
        required = {'priority_tier', 'rank', 'stance', 'stance_reason',
                    'stance_rank_start', 'stance_rank_end', 'stance_cohort_size',
                    'raw_overall_score', 'mixed_record', 'record_pattern',
                    'neutral_action_count', 'stance_cutoffs', 'opposition_rank_start',
                    'opposition_rank_end', 'opposition_cohort_size', 'opposition_cutoffs',
                    'stance_boundary_tie'}
        overall = data['overall_honors'][1]
        self.assertTrue(required <= overall.keys())
        self.assertEqual(overall['priority_tier'], 1)
        self.assertEqual(overall['stance'], 'Persuadable')
        self.assertEqual(overall['stance_rank_start'], 40)
        self.assertTrue(overall['mixed_record'])
        self.assertEqual(data['honors_meta']['overall_policy'], doc['overall_policy'])
        overall['stance_cutoffs']['ally'] = 0
        self.assertEqual(doc['members']['Bob']['overall']['stance_cutoffs']['ally'], 15)

    def test_compact_cells_preserve_qualification_but_leave_ledgers_in_generation(self):
        doc = document()
        row = doc['members']['Bob']['fields']['Payments']
        row.update(member_id='B000001', category_id='Payments', policy_id='field_rank_honors_v1',
                   missing_tensor_measures=['119:hr99'], boundary_tie=False)
        before = deepcopy(row)
        compact = compact_honor_cell(row)
        self.assertEqual(set(compact), set(row) & PUBLIC_HONOR_FIELDS)
        for key in PUBLIC_HONOR_FIELDS & row.keys():
            self.assertEqual(compact[key], row[key])
        for key in ('evidence_ids', 'missing_tensor_measures', 'member_id', 'category_id', 'policy_id'):
            self.assertNotIn(key, compact)
        compact['cutoffs']['champion'] = 0
        self.assertEqual(row, before)

    def test_stale_generation_or_incomplete_roster_cannot_publish(self):
        doc = document()
        data = bundle(doc)
        verify_dashboard_honors(data, doc)
        data['honors_meta']['generation_id'] = 'older'
        with self.assertRaisesRegex(ValueError, 'stale'):
            verify_dashboard_honors(data, doc)
        with self.assertRaisesRegex(ValueError, 'roster'):
            dashboard_honors_payload(doc, ['Ann'], ['Payments'])
        with self.assertRaisesRegex(ValueError, 'categories'):
            dashboard_honors_payload(doc, list(doc['members']), ['Interchange'])
        missing = bundle(doc)
        del missing['honors']
        with self.assertRaisesRegex(ValueError, 'rebuild'):
            verify_dashboard_honors(missing, doc)

    def test_separate_standing_policy_and_priority_are_required(self):
        doc = document()
        del doc['overall_policy']
        with self.assertRaisesRegex(ValueError, 'behavioral standing policy'):
            bundle(doc)
        doc = document()
        del doc['members']['Bob']['overall']['priority_tier']
        with self.assertRaisesRegex(ValueError, 'separate priority tier'):
            bundle(doc)

    def test_explicit_legacy_requires_legacy_bundle(self):
        self.assertEqual(dashboard_honors_payload(None, [], []), {})
        verify_dashboard_honors({'stance': ['A'], 'gen': 'C'}, None)
        with self.assertRaisesRegex(ValueError, 'legacy bundle'):
            verify_dashboard_honors(bundle(document()), None)

    def test_marker_identifies_generation_and_legacy_emits_none(self):
        self.assertEqual(honors_generation_marker(document()),
                         '<meta name="melange-honors-generation" content="generation-one">')
        self.assertEqual(honors_generation_marker(None), '')
        self.assertIn('&quot;', honors_generation_marker({'generation_id': 'unsafe"value'}))

    def node(self, script):
        if not Path(NODE).is_file():
            self.skipTest('Node runtime unavailable')
        result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_browser_honors_filter_and_engine_independence(self):
        source = TEMPLATE.read_text(encoding='utf-8')
        helpers = source[source.index('const SC='):source.index('const PTCOL=')]
        engine = source[source.index('const SCN='):source.index('// stance-integrity gates')]
        filter_function = next(line for line in source.splitlines() if line.startswith('function tierHidden('))
        script = '''
const DATA=__FIXTURE__, N=DATA.name.length; let issue=0, tierFilter=0, fieldHonorFilter=0, standingFilter='';
const TIERS=DATA.tier;
function mesc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
__HELPERS__
__FILTER__
const EG={pers:{Ally:0.3,Neutral:0.5,Opponent:0.05},theta:{},eng:[[1,2,3]],priority:[[1,1,1]],rcp:[1,1,1]};
const K={WM:1,WP:1,HALFF:1,BASE:1}, PM=null, DM=null, BPF=[0.1,0.2,0.3];
const ADJ=[{1:1,2:1},{0:1,2:1},{0:1,1:1}];function align(){return 1;}
__ENGINE__
const before=JSON.stringify(DATA.honors), modelBefore=JSON.stringify(flipModel(0,baseTiers(0),[1,2,3],[3,2,1]));
const labels=DATA.name.map((_,i)=>publicLabel(i));
const overall=DATA.name.map((_,i)=>publicLabel(i,1));
const tieRank=honorRankText(2), stats=honorStats();
fieldHonorFilter=1; const visible=DATA.name.filter((_,i)=>!tierHidden(i));
issue=1;tierFilter=1;const tierOne=DATA.name.filter((_,i)=>!tierHidden(i));
tierFilter=2;const tierTwo=DATA.name.filter((_,i)=>!tierHidden(i));
tierFilter=3;const tierThree=DATA.name.filter((_,i)=>!tierHidden(i));
tierFilter=0;standingFilter='Persuadable';const standingVisible=DATA.name.filter((_,i)=>!tierHidden(i));
DATA.overall_honors[0].priority_tier=0;tierFilter=4;standingFilter='';const outside=DATA.name.filter((_,i)=>!tierHidden(i));
standingFilter='Insufficient evidence';const outsideStanding=DATA.name.filter((_,i)=>!tierHidden(i));
standingFilter='Persuadable';const outsideOtherStanding=DATA.name.filter((_,i)=>!tierHidden(i));
const unchanged=before===JSON.stringify(DATA.honors);
DATA.honors[0][0].recognition='Highly Persuadable';DATA.overall_honors[0].stance='Opponent';
const modelAfter=JSON.stringify(flipModel(0,baseTiers(0),[1,2,3],[3,2,1]));
console.log(JSON.stringify({labels,overall,tieRank,stats,visible,tierOne,tierTwo,tierThree,standingVisible,outside,outsideStanding,outsideOtherStanding,unchanged,modelUnchanged:modelBefore===modelAfter,base:baseTiers(0)}));
'''.replace('__FIXTURE__', json.dumps(bundle(document()))).replace('__HELPERS__', helpers).replace('__FILTER__', filter_function).replace('__ENGINE__', engine)
        result = self.node(script)
        self.assertEqual(result['labels'], ['No recorded action', 'Champion', 'Supportive actions'])
        self.assertEqual(result['overall'], ['Insufficient evidence', 'Persuadable', 'Ally'])
        self.assertEqual(result['visible'], ['Bob'])
        self.assertEqual(result['tierOne'], ['Bob'])
        self.assertEqual(result['tierTwo'], ['Ann'])
        self.assertEqual(result['tierThree'], ['Cal'])
        self.assertEqual(result['standingVisible'], ['Bob'])
        self.assertEqual(result['outside'], ['Ann'])
        self.assertEqual(result['outsideStanding'], ['Ann'])
        self.assertEqual(result['outsideOtherStanding'], [])
        self.assertEqual(result['base'], ['A', 'N', 'O'])
        self.assertTrue(result['unchanged'])
        self.assertTrue(result['modelUnchanged'])
        self.assertIn('2–5 of 20', result['tieRank'])
        self.assertNotIn('Defender', result['stats'])
        self.assertNotIn('Ally', result['stats'])

    def test_complete_template_javascript_parses(self):
        if not Path(NODE).is_file():
            self.skipTest('Node runtime unavailable')
        source = TEMPLATE.read_text(encoding='utf-8').split('<script>', 1)[1].split('</script>', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / 'dashboard.js'
            script.write_text(source.replace('__DATA__', '{}'), encoding='utf-8')
            result = subprocess.run([NODE, '--check', str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_member_panel_keeps_priority_and_standing_separate(self):
        source = TEMPLATE.read_text(encoding='utf-8')
        helpers = source[source.index('const SC='):source.index('const PTCOL=')]
        render = source[source.index('function renderMemberSummary('):source.index('function selectMember(')]
        data = bundle(document())
        data.update({'play': 'nnn', 'party': 'DRI', 'bp': [1, 2, 3], 'cf': [50] * 3, 'rank': [5, 6, 7]})
        script = '''
const DATA=__FIXTURE__, N=DATA.name.length;let issue=0;
function mesc(s){return String(s??'');}
__HELPERS__
const PLAYLBL={n:'none'}, ELCOL={n:'gray'}, CENTPCT=[0,0.5,1];function centralCol(){return 'gray';}
const nodes={mtier:{style:{}},mstance:{}};const document={getElementById:id=>nodes[id]};
__RENDER__
renderMemberSummary(0);const noAction=nodes.mtier.innerHTML+nodes.mstance.innerHTML;
renderMemberSummary(1);const field=nodes.mtier.innerHTML+nodes.mstance.innerHTML;
issue=1;renderMemberSummary(1);const overall=nodes.mtier.innerHTML+nodes.mstance.innerHTML;
renderMemberSummary(2);const tierThree=nodes.mtier.innerHTML+nodes.mstance.innerHTML;
DATA.overall_honors[0].priority_tier=0;renderMemberSummary(0);const outside=nodes.mtier.innerHTML+nodes.mstance.innerHTML;
console.log(JSON.stringify({noAction,field,overall,tierThree,outside}));
'''.replace('__FIXTURE__', json.dumps(data)).replace('__HELPERS__', helpers).replace('__RENDER__', render)
        result = self.node(script)
        self.assertIn('No recorded action', result['noAction'])
        self.assertNotIn('>Ally<', result['noAction'])
        self.assertNotIn('Overall Champion', result['noAction'])
        self.assertIn('>Tier 2<', result['noAction'])
        self.assertIn('Priority rank #14', result['noAction'])
        self.assertIn('>Insufficient evidence<', result['noAction'])
        self.assertEqual(result['field'].count('Tier 1 Champion'), 1)
        self.assertIn('Field rank 1 of 20', result['field'])
        self.assertEqual(result['overall'].count('>Tier 1 Champion<'), 1)
        self.assertIn('>Stance<', result['overall'])
        self.assertIn('Persuadable · Mixed record', result['overall'])
        self.assertIn('Primacy rank 40 of 100', result['overall'])
        self.assertIn('>Tier 3<', result['tierThree'])
        self.assertIn('Priority rank #38', result['tierThree'])
        self.assertIn('>Ally<', result['tierThree'])
        self.assertIn('>Outside priority tiers<', result['outside'])

    def test_legacy_public_helpers_keep_old_labels_and_colors(self):
        source = TEMPLATE.read_text(encoding='utf-8')
        helpers = source[source.index('const SC='):source.index('const PTCOL=')]
        data = bundle(None)
        # No honors document here, so this exercises the pre-honors renderer on
        # its own legacy overall column.
        data['gen'] = 'CCN'
        result = self.node('const DATA=' + json.dumps(data) + ';let issue=0;' + helpers +
                           ';console.log(JSON.stringify({label:publicLabel(0),overall:publicLabel(0,1),color:publicColor(0),evidence:honorEvidence(0)}));')
        self.assertEqual(result, {'label': 'Ally', 'overall': 'Champion', 'color': '#86efac', 'evidence': ''})


if __name__ == '__main__':
    unittest.main()
