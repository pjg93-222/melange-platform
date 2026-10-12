const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../surfaces/templates/make_member_desk_00.tpl'), 'utf8');
const helpers = source.slice(source.indexOf('const STANCE_ORDER='), source.indexOf('/* ---------- manual notes:'));
const ranked = source.slice(source.indexOf('function renderRanked(){'), source.indexOf('/* ======================= MEMBER VIEW'));
const profile = source.slice(source.indexOf('function openMember(i){'), source.indexOf('function section(title,key,inner){'));
const efficiency = source.slice(source.indexOf('function renderEff(){'), source.indexOf('/* ======================= VIEW SWITCH'));
function overall(overrides = {}) {
  return {recognition:'Champion',rank:13,priority_tier:1,evidence_status:'observed',
    stance:'Persuadable',stance_reason:'within_persuadable_rank_cutoff',
    stance_rank_start:160,stance_rank_end:160,stance_cohort_size:533,
    raw_overall_score:'14.25',mixed_record:false,record_pattern:'supportive',
    alignment:'Supportive actions',action_count:4,positive_action_count:4,negative_action_count:0,
    neutral_action_count:0,tensor_tiebreak:'6.75',tensor_status:'complete',
    stance_cutoffs:{ally:79,highly_persuadable:106,persuadable:266},
    opposition_rank_start:null,opposition_rank_end:null,opposition_cohort_size:100,
    opposition_cutoffs:{opponent:5,highly_skeptical:15,skeptic:20},stance_boundary_tie:false,...overrides};
}
function field(overrides = {}) {
  return {recognition:'Ally',evidence_status:'observed',action_count:4,positive_action_count:4,
    raw_field_score:'4.8',rank_start:5,rank_end:7,cohort_size:100,
    cutoffs:{champion:5,ally:15,highly_persuadable:20},alignment:'Supportive actions',
    tensor_status:'incomplete',reason:'within_ally_rank_cutoff',evidence_ids:['event-1'],...overrides};
}
function member(name, overrides = {}) {
  return {name,chamber:'House',party:'Democrat',district:'Test district',rank:499,score:.99,
    stances:'C',overall_stance:'C',radar:[.99],adjrank:{CFPB:1},tier:1,overall_say_do:'capped',
    press_n:.2,comm_n:.3,leg_n:.4,demo_n:.1,
    honors:{member_id:name,overall:overall(),fields:{CFPB:field()}},...overrides};
}
function harness(members, legacy = false) {
  const elements = {};
  const element = selector => elements[selector] ||= {innerHTML:'',textContent:'',children:[],
    appendChild(item){this.children.push(item);},setAttribute(){}};
  const D = {cats:['CFPB'],cats_short:['CFPB'],tiername:{C:'Champion',A:'Ally',O:'Opponent'},
    rank_honors:legacy?null:{schema:'rank-honors-v1',generation_id:'fixture',as_of:'2026-09-12',
      policy:{champion_pct:5,ally_pct:15,highly_persuadable_pct:20},
      overall_policy:{id:'overall_behavioral_standing_v1',ally_pct:15,highly_persuadable_pct:20,
        persuadable_pct:50,opponent_pct:5,highly_skeptical_pct:15,skeptic_pct:20},
      evidence_window:{congresses:[117,118,119]}}};
  const escape = text => String(text??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const context = {D,M:members,CATS:D.cats,STCOL:{C:'#16a34a',A:'#86efac'},
    esc:escape,escAttr:text=>escape(text).replace(/"/g,'&quot;'),
    $:element,document:{getElementById:id=>element('#'+id),querySelectorAll:()=>[],
      createElement:()=>({})},RMQ:true,DSPLIT:false,DEF:new Set(),
    rstate:{q:'',area:'CFPB',chamber:'all',sort:'rank',stance:'',priority:'',funded:false,potential:false},
    effState:{q:'',chamber:'all',sort:'rank'},MAXROI:1,
    byName:Object.fromEntries(members.map((m,i)=>[m.name,i])),
    f2:value=>Number(value).toFixed(2),money:()=>'$0',
    mlink:name=>name,tierChip:()=>'',potentialChip:()=>'',sparkHTML:()=>'',
    setView(){},history:{replaceState(){}},window:{},
    PILL:Object.fromEntries(['press','comm','leg','demo'].map(k=>[k,{c:'#888',l:k,w:.25}]))};
  for(const name of ['radar','conductCard','evCardHTML','trackerHTML','narrativeHTML','bioHTML',
    'personalHTML','committeeHTML','psychHTML','speechHTML','section','sub','legHTML','respHTML','responsivenessCard',
    'staffHTML','relHTML','demoHTML','pressHTML','methodHTML','wireManual','wireTracker','wireSectionFilters']){
    context[name]=()=>'';
  }
  vm.createContext(context);
  vm.runInContext(helpers+'\n'+ranked+'\n'+profile+'\n'+efficiency,context);
  return {context,elements};
}

test('all inline template scripts parse',()=>{
  const scripts = [...source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
  assert.ok(scripts.length > 0);
  for(const match of scripts)new vm.Script(match[1]);
});

test('media-only legacy Champion gets no public field honor or fabricated rank',()=>{
  const m=member('Media Only');
  m.honors.fields.CFPB=field({recognition:null,evidence_status:'no_recorded_action',
    action_count:0,positive_action_count:0,rank_start:null,rank_end:null,raw_field_score:null,
    alignment:'No recorded action',evidence_ids:[]});
  const {context,elements}=harness([m]);
  assert.equal(context.honorLabel(m,'CFPB'),'No recorded action');
  assert.equal(context.honorRank(m.honors.fields.CFPB),'—');
  context.renderRanked();
  assert.match(elements['#rows'].innerHTML,/No recorded action/);
  assert.doesNotMatch(elements['#rows'].innerHTML,/#1\b/);
  assert.equal(m.stances,'C');assert.equal(m.adjrank.CFPB,1);
});

test('public field order, residual ties, denominator and thresholds use producer records',()=>{
  const tied=member('Tied actor'), first=member('Rank one');
  first.radar=[0];first.honors.fields.CFPB=field({recognition:'Champion',rank_start:1,rank_end:1});
  const {context,elements}=harness([tied,first]);
  context.renderRanked();
  assert.ok(elements['#rows'].innerHTML.indexOf('Rank one') < elements['#rows'].innerHTML.indexOf('Tied actor'));
  assert.match(elements['#rows'].innerHTML,/Tied 5–7 of 100/);
  assert.match(context.honorFacts(tied,'CFPB'),/Cutoffs: Champion 5, Ally 15, Highly Persuadable 20/);
  assert.match(context.honorFacts(tied,'CFPB'),/4 recorded actions/);
  context.rstate.q='tied';context.renderRanked();
  assert.match(elements['#rows'].innerHTML,/Tied 5–7 of 100/);
  assert.doesNotMatch(elements['#rows'].innerHTML,/Rank one/);
});

test('original five-column table uses primacy rank and one stance badge',()=>{
  const champion=member('Top thirteen'),other=member('Other');
  champion.honors.overall=overall({stance:'Champion',stance_rank_start:13,stance_rank_end:13});
  other.honors.overall=overall({rank:400,priority_tier:0,recognition:null,stance:'Uncommitted'});
  const {context,elements}=harness([other,champion]);
  context.rstate.area='';context.renderRanked();
  const html=elements['#rows'].innerHTML;
  assert.ok(html.indexOf('Top thirteen')<html.indexOf('Other'));
  assert.match(html,/>Champion</);assert.match(html,/>Uncommitted</);
  assert.equal((html.match(/<td\b/g)||[]).length,10);
  assert.doesNotMatch(html,/Action rank/);
  assert.equal(elements['#th-stance'].textContent,'Stance · overall');
  assert.equal(champion.rank,499);
});

test('profile retains a single verdict with inspectable opposition evidence',()=>{
  const m=member('Profile actor');
  m.honors.overall=overall({stance:'Opponent',recognition:null,priority_tier:0,rank:529,
    opposition_evidence:{core_measures:['118:s:1838','119:s:3623'],measures:['118:s:1838','119:s:3623'],lead_measures:['118:s:1838','119:s:3623'],congresses:[118,119]}});
  const {context,elements}=harness([m]);context.openMember(0);
  const html=elements['#memberbody'].innerHTML;
  assert.match(html,/>Opponent</);assert.match(html,/Primacy rank #529/);
  assert.match(html,/118:s:1838/);assert.match(html,/Classification evidence/);
  assert.doesNotMatch(html,/>Behavioral standing</);
});

test('missing canonical field is an error; legacy mode retains the prior ladder',()=>{
  const m=member('Missing field');delete m.honors.fields.CFPB;
  const active=harness([m]);assert.throws(()=>active.context.publicBadge(m,'CFPB'),/record missing/);
  const legacy=harness([m],true);
  assert.match(legacy.context.publicBadge(m,'CFPB'),/>Champion</);
  legacy.context.renderRanked();assert.match(legacy.elements['#rows'].innerHTML,/#1/);
});

test('honor filters use shared labels without mutating the cohort or model',()=>{
  const m=member('Ally'), second=member('Lower');
  second.honors.fields.CFPB=field({recognition:null,rank_start:70,rank_end:70});
  const baseline=JSON.stringify([m,second]);
  const {context,elements}=harness([m,second]);
  context.rstate.stance='Ally';context.renderRanked();
  assert.match(elements['#rows'].innerHTML,/Tied 5–7 of 100/);
  assert.doesNotMatch(elements['#rows'].innerHTML,/Lower/);
  assert.equal(JSON.stringify([m,second]),baseline);
  context.rstate.stance='no_recognition';context.renderRanked();
  assert.match(elements['#rows'].innerHTML,/Below recognition threshold/);
});

test('all labels can be filtered including Champion and Uncommitted',()=>{
  const labels=['Champion','Uncommitted','Ally','Highly Persuadable','Persuadable','Lean Persuadable','True Neutral','Lean Skeptic','Skeptic','Highly Skeptical','Opponent','Insufficient evidence'];
  const members=labels.map((stance,i)=>{const m=member('Member '+i);m.honors.overall=overall({stance,rank:i+1});return m;});
  const {context,elements}=harness(members);context.rstate.area='';
  for(const stance of labels){context.rstate.stance=stance;context.renderRanked();assert.equal((elements['#rows'].innerHTML.match(/<tr /g)||[]).length,1);}
  context.configureRecognitionFilter('');
  assert.deepEqual(elements['#stancefilter'].children.map(item=>item.value),labels);
});

test('priority and standing filters intersect using canonical tiers and never shrink cohorts',()=>{
  const one=member('Priority one'),two=member('Priority two'),outside=member('Outside');
  two.honors.overall=overall({recognition:null,priority_tier:2,rank:40});
  outside.honors.overall=overall({recognition:null,priority_tier:0,rank:250});
  const baseline=JSON.stringify([one,two,outside]);
  const {context,elements}=harness([one,two,outside]);
  context.rstate.area='';context.rstate.stance='Persuadable';context.rstate.priority='2';
  context.renderRanked();
  assert.match(elements['#rows'].innerHTML,/Priority two/);
  assert.doesNotMatch(elements['#rows'].innerHTML,/Priority one|Outside/);
  assert.match(elements['#rows'].innerHTML,/Primacy rank #40/);
  context.rstate.priority='0';context.renderRanked();
  assert.match(elements['#rows'].innerHTML,/Outside priority tiers/);
  assert.doesNotMatch(elements['#rows'].innerHTML,/Priority two|Priority one/);
  assert.equal(JSON.stringify([one,two,outside]),baseline);
});

test('tier sorting takes precedence over selected field and keeps original rank',()=>{
  const one=member('Primacy first'),two=member('Field first');
  one.honors.fields.CFPB=field({rank_start:90,rank_end:90});
  two.honors.fields.CFPB=field({rank_start:1,rank_end:1});
  two.honors.overall=overall({rank:400,priority_tier:0,recognition:null,stance:'Uncommitted'});
  const {context,elements}=harness([two,one]);
  context.rstate.area='CFPB';context.rstate.sort='tier';context.renderRanked();
  const html=elements['#rows'].innerHTML;
  assert.ok(html.indexOf('Primacy first')<html.indexOf('Field first'));
  assert.match(html,/>400<\/span>/);assert.equal(elements['#th-rank'].textContent,'Primacy rank');
  context.rstate.sort='rank';context.renderRanked();
  assert.ok(elements['#rows'].innerHTML.indexOf('Field first')<elements['#rows'].innerHTML.indexOf('Primacy first'));
});

test('controls retain tier sort across area changes and expose no action-rank sort',()=>{
  const {context,elements}=harness([member('Controls')]);
  const controls=source.slice(source.indexOf("configureRecognitionFilter('');"),source.indexOf('function sparkHTML(m)'));
  vm.runInContext(controls+'\nglobalThis.controlState=rstate;',context);
  elements['#sort'].onchange({target:{value:'tier'}});
  elements['#area'].onchange({target:{value:'CFPB'}});
  assert.equal(context.controlState.sort,'tier');
  assert.doesNotMatch(source,/<option value=standing/);
  assert.doesNotMatch(source,/<th[^>]*id=th-priority/);
});

test('missing canonical overall fields fail closed and efficiency separates both measures',()=>{
  const m=member('Complete'),old=member('Old');
  old.honors.overall={rank:13,recognition:'Champion'};
  const missing=harness([old]);
  assert.throws(()=>missing.context.publicBadge(old,''),/priority\/standing missing/);
  const {context,elements}=harness([m]);context.renderEff();
  assert.match(elements['#effrows'].innerHTML,/>Tier 1 · Champion</);
  assert.match(elements['#effrows'].innerHTML,/>Persuadable</);
  assert.match(elements['#effrows'].innerHTML,/Priority rank #13/);
  assert.doesNotMatch(elements['#effrows'].innerHTML,/>499</);
});
