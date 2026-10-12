<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=referrer content=no-referrer>
<title>Melange — Staffer network</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 208'><path d='M108 20L64 64L28 104L64 144L20 188M64 64L100 104L64 144' fill='none' stroke='%23EECD5C' stroke-width='24' stroke-linejoin='miter' stroke-miterlimit='6'/></svg>">
<style>
:root{color-scheme:dark;--bg:#1D1912;--bar:#282113;--panel:#2E2514;--panel-hover:#3B2E16;--rule:#433317;--border:#564019;--faint:#9D9784;--muted:#A49D8A;--text-soft:#D2CDBC;--text:#F3F3E6;--gold:#EECD5C;--gold-fill:#D2A63C;--gold-deep:#BB8525;--ui-display:"Century Gothic","Tw Cen MT",Futura,"Avenir Next","Segoe UI",sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1D1912;color:#F3F3E6;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
#stf-root{min-height:100vh;background:#1D1912;color:#F3F3E6}
#stf-head{min-height:44px;padding:6px 14px;border-bottom:1px solid #433317;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
#stf-head h1{font-family:var(--ui-display);font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.18em;flex-shrink:0;display:flex;align-items:center;gap:6px;color:#F3F3E6}
#stf-head .stf-brand{flex:0 0 auto}
#stf-head .stf-spacer{flex:1 1 auto;min-width:0}
.stf-desklink{align-self:center;flex-shrink:0;border:1px solid #564019;border-radius:6px;padding:7px 13px;font-size:12px;font-weight:600;color:#D2CDBC;text-decoration:none;white-space:nowrap;background:transparent;font-family:inherit}
.stf-desklink:hover{border-color:#EECD5C;color:#F3F3E6;background:#3B2E16}
.stf-desklink:focus-visible{outline:2px solid #EECD5C;outline-offset:2px}
#stf-prov{background:#282113;border-bottom:1px solid #433317;padding:8px 14px;font-size:11px;color:#A49D8A}
#stf-provline{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline}
.stf-prov-k{font-family:var(--ui-display);text-transform:uppercase;letter-spacing:.12em;font-size:9px;color:#9D9784;margin-right:5px}
.stf-prov-v{color:#D2CDBC}
.stf-flag{border:1px solid #BB8525;color:#EECD5C;border-radius:4px;padding:1px 6px;font-family:var(--ui-display);text-transform:uppercase;letter-spacing:.1em;font-size:9px}
#stf-statement{margin-top:6px;color:#D2CDBC;max-width:110ch;line-height:1.5}
#stf-langnote{margin-top:3px;color:#9D9784;max-width:110ch;line-height:1.5}
#stf-main{padding:14px;display:flex;flex-direction:column;gap:18px}
.stf-sec{border:1px solid #433317;border-radius:8px;background:#2E2514;padding:12px 14px}
.stf-sec h2{font-family:var(--ui-display);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.16em;color:#EECD5C;margin-bottom:2px}
.stf-sub{font-size:11px;color:#9D9784;margin-bottom:10px;max-width:110ch;line-height:1.5}
.stf-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.stf-tile{border:1px solid #433317;border-radius:6px;background:#1D1912;padding:9px 11px}
.stf-tile .stf-tk{font-family:var(--ui-display);font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#A49D8A;display:block;line-height:1.35}
.stf-tile .stf-tv{font-size:19px;font-weight:700;color:#F3F3E6;display:block;margin-top:3px;font-variant-numeric:tabular-nums}
.stf-tile .stf-tn{font-size:10px;color:#9D9784;display:block;margin-top:2px}
#stf-pickrow{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}
#stf-pickrow label{font-family:var(--ui-display);color:#A49D8A;font-size:10px;text-transform:uppercase;letter-spacing:.14em}
#stf-pick{background:#1D1912;color:#F3F3E6;border:1px solid #564019;border-radius:6px;padding:6px 9px;font-size:12px;min-width:250px;font-family:inherit}
#stf-pick:focus-visible{outline:2px solid #EECD5C;outline-offset:1px}
#stf-stationname{font-size:15px;font-weight:700;color:#EECD5C}
#stf-stationmeta{font-size:11px;color:#A49D8A}
#stf-canvaswrap{border:1px solid #433317;border-radius:6px;background:#1D1912;margin:10px 0;position:relative}
#stf-canvas{display:block;width:100%;height:400px;touch-action:none}
#stf-readout{font-size:11px;color:#D2CDBC;min-height:16px;padding:0 10px 8px 10px}
#stf-legend{font-size:11px;color:#9D9784;line-height:1.6;margin-bottom:10px;max-width:110ch}
#stf-legend b{color:#D2CDBC;font-weight:600}
#stf-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.stf-chip{border:1px solid #564019;border-radius:999px;background:#1D1912;color:#D2CDBC;font:11px/1.2 inherit;padding:5px 10px;cursor:pointer}
.stf-chip:hover{border-color:#EECD5C;background:#3B2E16;color:#F3F3E6}
.stf-chip:focus-visible{outline:2px solid #EECD5C;outline-offset:2px}
.stf-grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.stf-block h3{font-family:var(--ui-display);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:#A49D8A;margin-bottom:6px}
.stf-scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12px}
th{font-family:var(--ui-display);font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#A49D8A;text-align:left;padding:6px 8px;border-bottom:1px solid #564019;white-space:nowrap;font-weight:700}
td{padding:5px 8px;border-bottom:1px solid #433317;color:#D2CDBC;vertical-align:top}
td.stf-n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
th.stf-n{text-align:right}
tr:last-child td{border-bottom:0}
.stf-sortbtn{background:transparent;border:0;color:#A49D8A;font:inherit;letter-spacing:.12em;text-transform:uppercase;cursor:pointer;padding:0}
.stf-sortbtn:hover{color:#EECD5C}
.stf-sortbtn:focus-visible{outline:2px solid #EECD5C;outline-offset:2px}
.stf-sorton{color:#EECD5C}
a.mlink{color:#F3F3E6;text-decoration:none;border-bottom:1px solid #564019}
a.mlink:hover{color:#EECD5C;border-bottom-color:#EECD5C}
a.mlink:focus-visible{outline:2px solid #EECD5C;outline-offset:2px}
.stf-dim{color:#9D9784}
.stf-tier{font-family:var(--ui-display);font-size:9px;letter-spacing:.1em;border:1px solid #564019;border-radius:3px;padding:1px 4px;color:#A49D8A}
#stf-rosterfoot{margin-top:8px;display:flex;gap:10px;align-items:center;font-size:11px;color:#9D9784}
#stf-showall{border:1px solid #564019;border-radius:6px;background:#1D1912;color:#D2CDBC;font:11px/1.2 inherit;padding:6px 11px;cursor:pointer}
#stf-showall:hover{border-color:#EECD5C;background:#3B2E16;color:#F3F3E6}
#stf-showall:focus-visible{outline:2px solid #EECD5C;outline-offset:2px}
@media(max-width:640px){#stf-main{padding:10px}.stf-grid2{grid-template-columns:1fr}.stf-tiles{grid-template-columns:1fr 1fr}#stf-canvas{height:320px}#stf-pick{min-width:0;width:100%}}
@media(prefers-reduced-motion:reduce){*{transition-duration:1ms!important;animation-duration:1ms!important;scroll-behavior:auto!important}}
</style></head><body>
<div id=stf-root>
<header id=stf-head><h1><svg class=stf-brand width="19" height="31" viewBox="0 0 128 208" aria-hidden="true"><path d="M108 20 L64 64 L28 104 L64 144 L20 188 M64 64 L100 104 L64 144" fill="none" stroke="#EECD5C" stroke-width="19" stroke-linejoin="miter" stroke-miterlimit="6"/></svg>Melange — Staffer network</h1>
<span class=stf-spacer></span>
<a id=stf-livelink class=stf-desklink hidden>Live staffer view ▸</a>
<a id=stf-netlink class=stf-desklink href="Moneyball Dashboard.html">Main dashboard ▸</a><a id=stf-desk class=stf-desklink href="cu-member-desk.html">Member desk ▸</a><a id=stf-indlink class=stf-desklink href="lobby-network.html">Industry ▸</a></header>
<div id=stf-prov>
<div id=stf-provline></div>
<div id=stf-statement></div>
<p id=stf-score-note class=stf-sub hidden></p>
<div id=stf-langnote></div>
</div>
<main id=stf-main>
<section class=stf-sec aria-labelledby=stf-coverh>
<h2 id=stf-coverh>Coverage</h2>
<div class=stf-sub id=stf-coversub></div>
<div class=stf-tiles id=stf-covertiles></div>
</section>
<section class=stf-sec aria-labelledby=stf-stationh>
<h2 id=stf-stationh>Member station</h2>
<div class=stf-sub>One office at a time: the people on its payroll record, and the offices that share people with it.</div>
<div id=stf-pickrow>
<label for=stf-pick>Find office</label>
<input id=stf-pick type=search autocomplete=off placeholder="member name…" list=stf-names aria-label="Find a member office">
<datalist id=stf-names></datalist>
<span id=stf-stationname></span><span id=stf-stationmeta></span>
</div>
<div class=stf-tiles id=stf-stationtiles></div>
<div id=stf-canvaswrap><canvas id=stf-canvas role=img aria-label="Station diagram: this office at the centre, its people around it, and the offices that share people with it"></canvas><div id=stf-readout></div></div>
<div id=stf-legend></div>
<div id=stf-chips></div>
<div class=stf-grid2>
<div class=stf-block><h3>Top staff</h3><div class=stf-scroll><table id=stf-topstaff><thead><tr><th class=stf-n>Rank</th><th>Person</th><th>Title on the record</th><th>Status</th><th>Identity tier</th><th title="People whose payroll intervals overlapped this person's under the same office header; the office named is where this person spent the most days.">Same payroll office, same time</th><th class=stf-n>Days with this office</th></tr></thead><tbody></tbody></table></div></div>
<div class=stf-block><h3>Titles on the current payroll</h3><div class=stf-scroll><table id=stf-titles><thead><tr><th>Title</th><th class=stf-n>People</th></tr></thead><tbody></tbody></table></div></div>
<div class=stf-block><h3>Shared staff by office</h3><div class=stf-scroll><table id=stf-sharedtbl><thead><tr><th>Other office</th><th class=stf-n>People</th></tr></thead><tbody></tbody></table></div></div>
<div class=stf-block><h3>Moves in</h3><div class=stf-scroll><table id=stf-movesin><thead><tr><th>Container</th><th>Kind</th><th>Member</th><th class=stf-n>People</th></tr></thead><tbody></tbody></table></div></div>
<div class=stf-block><h3>Moves out</h3><div class=stf-scroll><table id=stf-movesout><thead><tr><th>Container</th><th>Kind</th><th>Member</th><th class=stf-n>People</th></tr></thead><tbody></tbody></table></div></div>
</div>
</section>
<section class=stf-sec aria-labelledby=stf-rosterh>
<h2 id=stf-rosterh>Roster</h2>
<div class=stf-sub id=stf-rostersub></div>
<div class=stf-scroll><table id=stf-roster><thead><tr id=stf-rosterhead></tr></thead><tbody></tbody></table></div>
<div id=stf-rosterfoot><button id=stf-showall type=button></button><span id=stf-rostercount></span></div>
</section>
</main>
</div>
<script>
const SDATA=__DATA__;
(function(){
"use strict";
var META=SDATA.meta||{},GLB=SDATA["global"]||{},MEM=SDATA.members||{};
var BIOS=Object.keys(MEM);
var byName={},nameOf={};
BIOS.forEach(function(b){var r=MEM[b];nameOf[b]=r.display_name;byName[r.display_name]=b;});
var ROWS=BIOS.map(function(b){return MEM[b];});
var CAP=50,showAll=false,sortKey='staff_total',sortDir=-1,current=null;

function num(v){return (v===null||v===undefined||v!==v)?'—':Number(v).toLocaleString('en-US');}
function pct(v){return (v===null||v===undefined)?'—':(100*Number(v)).toFixed(1)+'%';}
function esc(s){return String(s===null||s===undefined?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function memberLink(bio){
  var n=nameOf[bio];
  if(!n)return '<span class=stf-dim>'+esc(bio||'—')+'</span>';
  return '<a class=mlink href="cu-member-desk.html?m='+encodeURIComponent(n)+'">'+esc(n)+'</a>';
}
function tile(k,v,note,tip){
  return '<div class=stf-tile'+(tip?' title="'+esc(tip)+'"':'')+'><span class=stf-tk>'+esc(k)+'</span><span class=stf-tv>'+v+'</span>'+(note?'<span class=stf-tn>'+esc(note)+'</span>':'')+'</div>';
}
/* The move tables' office names (owner ruling 2026-09-07, item 2, applied to the
   moves). This function used to end in `return s`, which is how HON:H001060
   reached a reader in 1,514 of 5,912 published move cells: every HON: key naming
   a member off the CURRENT 533-member roster missed nameOf and was printed as if
   it were a name. It is not the page's job to know what an office off the roster
   is called -- the House personnel ledger is, and the producer now resolves these
   through the SAME office_label() rule the office column goes through and ships
   the answers in META.move_office_names. A raw container key is never printed
   again: an office no record names says so (META.office_name_unresolved_label),
   and a page built from a section artifact older than this ruling names the stale
   artifact instead, the same staleness-honesty convention the office column and
   office_diversity use below. */
function containerLabel(id){
  var s=String(id||'');
  s=s.replace(/^container:office:(house|senate):/,'');
  s=s.replace(/^container:/,'');
  var hon=s.match(/^HON:(.+)$/);
  /* Not a member office: the record's own office string, printed as it reads. */
  if(!hon)return s;
  var named=META.move_office_names;
  if(named===undefined)return nameOf[hon[1]]||'Office name not in this section artifact (rebuild build_staffer_network_section.py)';
  /* The producer's answer first: it applied the whole rule, including the
     container's own member_bioguide, which this page does not carry. */
  if(named[hon[1]])return named[hon[1]];
  if(nameOf[hon[1]])return nameOf[hon[1]];
  return META.office_name_unresolved_label||'Office name not in the records';
}

/* ---------------------------------------------------------------- provenance */
function drawProvenance(){
  var ver=META.verification||{};
  var bits=[];
  function kv(k,v,tip){bits.push('<span'+(tip?' title="'+esc(tip)+'"':'')+'><span class=stf-prov-k>'+esc(k)+'</span><span class=stf-prov-v>'+esc(v)+'</span></span>');}
  kv('Generation',META.generation||'—');
  kv('Verification',(ver.status||'—')+' · '+num(ver.failures)+' failures',ver.path||'');
  bits.push('<span class=stf-flag>Exploratory · review required</span>');
  kv('Rule version',GLB.rule_version||'—');
  kv('Built',(META.built_at||'—'));
  kv('Roster',num(META.roster_size)+' members');
  document.getElementById('stf-provline').innerHTML=bits.join('');
  document.getElementById('stf-statement').textContent=META.statement||'';
  document.getElementById('stf-langnote').textContent=META.language_note||'';
  /* One meta block, two receipt shapes.  A v5 generation's ranking_controls
     carries `scenarios` -- counterfactual rebuilds, reported as a top-100
     retention band.  A v6 generation's policy_link_pagerank_control carries no
     scenarios at all: it is one DESCRIPTIVE ratio on the published persons-only
     operator, and its own `semantics` string says it is not a counterfactual.
     Print each in its own words rather than forcing one sentence onto both. */
  var controls=META.pagerank_policy_control,scoreNote=document.getElementById('stf-score-note');
  if(controls&&controls.scenarios&&controls.scenarios.length){
    var overlaps=controls.scenarios.map(function(s){return s.top100_person_overlap_with_published;}),lo=Math.min.apply(null,overlaps),hi=Math.max.apply(null,overlaps);
    scoreNote.hidden=false;
    scoreNote.textContent='Ranking sensitivity: the '+controls.scenarios.length+' tested restart/policy alternatives retained '+(lo===hi?lo:lo+'–'+hi)+' of the default top 100 people. These ranks reflect source coverage and model choices; they are exploratory.';
  }else if(controls&&controls.ratio_with_over_without!==undefined&&controls.ratio_with_over_without!==null){
    scoreNote.hidden=false;
    scoreNote.textContent='Policy-evidence check: people carrying a policy link score '+
      (Math.round(controls.ratio_with_over_without*100)/100)+'× the mean connectedness of those who do not ('+
      num(controls.people_with_policy_evidence)+' of '+
      num((controls.people_with_policy_evidence||0)+(controls.people_without_policy_evidence||0))+
      ' people). '+(controls.semantics||'A descriptive comparison, not a counterfactual and not a calibration.');
  }
}

/* ------------------------------------------------------------------ coverage */
function drawCoverage(){
  var nu=GLB.node_universe||{},ec=GLB.edge_counts||{},pc=GLB.person_components||{};
  var share=(pc.largest_component_size!==undefined&&nu.total)?(pc.largest_component_size/nu.total):null;
  var t=[];
  t.push(tile('People on the payroll record',num(nu.person),num(GLB.person_nodes_with_roster_member_attachment)+' attached to a roster office','node_universe.person; person_nodes_with_roster_member_attachment'));
  t.push(tile('Offices',num(nu.container),num((nu.container_by_kind||{}).member_office)+' member offices','node_universe.container by kind'));
  t.push(tile('Guaranteed co-tenure ties',num(ec.co_tenure_guaranteed),'person to person, overlapping calendars','edge_counts.co_tenure_guaranteed'));
  t.push(tile('Same-office pairs rejected for zero overlap',num(GLB.zero_overlap_pairs_rejected),GLB.zero_overlap_rule||'','edge_counts.co_tenure_zero_overlap_REJECTED_never_emitted'));
  t.push(tile('Full-graph component share',pct(share),num(pc.largest_component_size)+' of '+num(nu.total)+' graph nodes; roster-attached people occur in '+num(pc.distinct_component_ids)+' components','Full heterogeneous graph, including institutions; not the person-only co-tenure graph.'));
  t.push(tile('Members with any staff',num(GLB.members_with_any_staff),'of '+num(GLB.roster_size),'members_with_any_staff'));
  t.push(tile('Members with shared staff',num(GLB.members_with_shared_staff),'of '+num(GLB.roster_size),'members_with_shared_staff'));
  document.getElementById('stf-covertiles').innerHTML=t.join('');
  document.getElementById('stf-coversub').textContent=GLB.shared_staff_note||'';
}

/* ------------------------------------------------------------------- station */
function drawStationTiles(r){
  var t=[];
  t.push(tile('Staff on record',num(r.staff_total),'all stints, current and historic','staff_total'));
  t.push(tile('Current',num(r.staff_current),'','staff_current'));
  t.push(tile('Departed',num(r.staff_departed),'','staff_departed'));
  t.push(tile('Shared staff',num(r.shared_staff),num(r.shared_with_partner_count)+' roster offices in common','shared_staff; shared_with_partner_count'));
  t.push(tile('Moves in',num(r.moves_in),num(r.moves_in_office_pairs)+' office pairs','moves_in'));
  t.push(tile('Moves out',num(r.moves_out),num(r.moves_out_office_pairs)+' office pairs','moves_out'));
  document.getElementById('stf-stationtiles').innerHTML=t.join('');
}
function drawLegend(){
  document.getElementById('stf-legend').innerHTML=
   '<b>Reading the diagram.</b> The gold disc is this office. The inner ring is its current staff, the faded outer ring is departed staff; '+
   'a dot is larger when that person appears on the same payroll office at the same time as more people. Opacity follows the declared '+
   '<b>S1 / S2 / S3 evidence tier</b>; every tier remains subject to identity review. '+
   'Dots on a leader line to a labelled office are people attached to both offices. '+
   '<b>Connectors show a person attached to both offices. They are not ties between members.</b> '+
   'Only the top 15 people by member-office connection rank are drawn; the tiles above count everyone.';
}
function drawTables(r){
  var b,i,html,rank=0,lastRankKey=null;
  b=document.querySelector('#stf-topstaff tbody');
  html=(r.top_staff||[]).map(function(p,i){
    var tied=Number(p.rank_tie_size)>1;
    var rankKey=(p.connection_score??p.member_connection_strength)+'|'+p.member_connection_strength;
    if(!(tied && rankKey===lastRankKey)) rank=i+1;
    lastRankKey=rankKey;
    /* co_tenure_office_name/size replaced a bare co_tenure_degree number on
       2026-09-07 (owner ruling 1, RELABEL): the count alone read as a colleague
       count with no way to tell WHERE the overlap happened, so the cell now also
       names the specific payroll container -- not necessarily this page's own
       office. As of the owner ruling of 2026-09-07 on the office label it is the
       person's MOST RECENT office or committee (it was the one they spent the
       most days in). Same staleness-honesty convention as office_diversity
       below: a page built from an older section artifact has no such field, and
       that is said, not hidden behind the bare number. A present-but-null value
       means no dated payroll or Politico record places this person in an office
       at all, and the cell SAYS that rather than showing a bare number a reader
       would read as a missing office. An office that is on record but that no
       record names arrives already carrying the producer's own honest label
       (meta.office_name_unresolved_label); a container id never arrives here. */
    var coLabel=p.co_tenure_office_name
        ?(num(p.co_tenure_degree)+' · '+p.co_tenure_office_name+' ('+num(p.co_tenure_office_size)+' people)')
        :(p.co_tenure_office_name===undefined
            ? num(p.co_tenure_degree)
            : num(p.co_tenure_degree)+' · Office not on record');
    var tip='Member-office connection: '+(p.connection_score??'not recorded')+' · Diagnostic PageRank: '+p.pagerank_085+
            ' · Supported connection strength in this member’s workplaces: '+(p.member_connection_strength===undefined?'not recorded':p.member_connection_strength)+
            (tied?' · Equal ranking keys for '+p.rank_tie_size+' eligible people; the displayed list may end within this tie':'')+
            ' · component-local eigenvector diagnostic: '+p.eigen_component_local+
            /* office_diversity replaced bridging_diversity on 2026-09-07. A page built
               from a section artifact older than that rename has no such field, and
               printing "undefined" at a reader is worse than saying so: the guard names
               the stale artifact instead. Not a fallback to the old field -- reading it
               would hide the staleness that the chain step order exists to prevent. */
            ' · office diversity: '+(p.office_diversity===undefined
                ? 'not in this section artifact (rebuild build_staffer_network_section.py)'
                : p.office_diversity+' ('+(META.office_diversity_source||'source not recorded')+')')+
            ' · Same payroll office, same time: '+(p.co_tenure_office_name===undefined
                ? p.co_tenure_degree+' (office not in this section artifact; rebuild build_staffer_network_section.py)'
                : (p.co_tenure_office_name
                    ? p.co_tenure_degree+' · '+p.co_tenure_office_name+' ('+p.co_tenure_office_size+' people)'
                    : p.co_tenure_degree+' (no dated payroll or Politico record places this person in an office)'))+
            ' · other_member_count '+p.other_member_count+' · '+p.node_id;
    return '<tr title="'+esc(tip)+'"><td class=stf-n>'+rank+(tied?' =':'')+'</td><td><a href="http://127.0.0.1:8841/staffer-live/?person='+encodeURIComponent(p.node_id)+'">'+esc(p.display_name||'View source person')+'</a></td><td>'+esc(p.title_last||'—')+'</td><td>'+
      esc(p.status||'—')+'</td><td><span class=stf-tier>'+esc(p.identity_tier||'—')+'</span></td><td>'+
      esc(coLabel)+'</td><td class=stf-n>'+num(p.tenure_days_with_member)+'</td></tr>';
  }).join('');
  b.innerHTML=html||'<tr><td colspan=7 class=stf-dim>No people on record for this office.</td></tr>';

  b=document.querySelector('#stf-titles tbody');
  html=(r.titles||[]).map(function(t){
    return '<tr><td>'+esc(t.title_last||'—')+'</td><td class=stf-n>'+num(t.people)+'</td></tr>';
  }).join('');
  b.innerHTML=html||'<tr><td colspan=2 class=stf-dim>No current titles on record.</td></tr>';

  b=document.querySelector('#stf-sharedtbl tbody');
  html=(r.shared_with||[]).map(function(s){
    return '<tr><td>'+memberLink(s.bioguide)+'</td><td class=stf-n>'+num(s.people)+'</td></tr>';
  }).join('');
  b.innerHTML=html||'<tr><td colspan=2 class=stf-dim>No roster office shares a person with this one.</td></tr>';

  [['stf-movesin','top_sources'],['stf-movesout','top_destinations']].forEach(function(pair){
    var body=document.querySelector('#'+pair[0]+' tbody');
    var rowsHtml=(r[pair[1]]||[]).map(function(m){
      return '<tr title="'+esc(m.container_id||'')+'"><td>'+esc(containerLabel(m.container_id))+'</td><td class=stf-dim>'+
        esc(m.container_kind||'—')+'</td><td>'+(m.member_bioguide?memberLink(m.member_bioguide):'<span class=stf-dim>—</span>')+
        '</td><td class=stf-n>'+num(m.people)+'</td></tr>';
    }).join('');
    body.innerHTML=rowsHtml||'<tr><td colspan=4 class=stf-dim>None on record.</td></tr>';
  });
}
function drawChips(r){
  var el=document.getElementById('stf-chips');
  var parts=(r.shared_with||[]).filter(function(s){return nameOf[s.bioguide];}).map(function(s){
    return '<button type=button class=stf-chip data-stfname="'+esc(nameOf[s.bioguide])+'">'+esc(nameOf[s.bioguide])+' · '+num(s.people)+' shared</button>';
  });
  el.innerHTML=parts.length?('<span class=stf-dim style="align-self:center">Open another station:</span>'+parts.join('')):'';
}

/* -------------------------------------------------------------------- canvas */
var CV=null,CTX=null,HITS=[];
function sizeCanvas(){
  var c=document.getElementById('stf-canvas');if(!c)return null;
  var rect=c.getBoundingClientRect();
  var w=Math.max(280,Math.round(rect.width)),h=Math.max(220,Math.round(rect.height));
  var dpr=Math.min(2,window.devicePixelRatio||1);
  if(c.width!==Math.round(w*dpr)||c.height!==Math.round(h*dpr)){c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);}
  var ctx=c.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
  return {c:c,ctx:ctx,w:w,h:h};
}
function tierAlpha(t){return t==='S1'?1:(t==='S2'?0.72:(t==='S3'?0.45:0.6));}
function drawStation(r){
  var g=sizeCanvas();if(!g)return;
  var ctx=g.ctx,w=g.w,h=g.h;HITS=[];
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle='#1D1912';ctx.fillRect(0,0,w,h);
  if(!r){return;}
  var cx=w/2,cy=h/2;
  var rim=Math.max(70,Math.min(w,h)/2-56);
  var inner=rim*0.42,outer=rim*0.68;

  var staff=(r.top_staff||[]);
  var maxdeg=1;staff.forEach(function(p){if((p.co_tenure_degree||0)>maxdeg)maxdeg=p.co_tenure_degree;});
  var cur=staff.filter(function(p){return p.status==='current';});
  var dep=staff.filter(function(p){return p.status!=='current';});

  /* shared-staff leaders first, so the people dots sit on top */
  var partners=(r.shared_with||[]).filter(function(s){return nameOf[s.bioguide];}).slice(0,12);
  ctx.font='10px -apple-system,Segoe UI,Roboto,sans-serif';
  ctx.textAlign='center';ctx.textBaseline='middle';
  partners.forEach(function(s,i){
    var a=-Math.PI/2+(i/Math.max(1,partners.length))*Math.PI*2;
    var ax=cx+Math.cos(a)*rim,ay=cy+Math.sin(a)*rim;
    ctx.save();
    ctx.strokeStyle='rgba(187,133,37,0.55)';ctx.lineWidth=1;ctx.setLineDash([2,3]);
    ctx.beginPath();ctx.moveTo(cx+Math.cos(a)*(outer+10),cy+Math.sin(a)*(outer+10));ctx.lineTo(ax,ay);ctx.stroke();
    ctx.restore();
    var n=Math.min(6,s.people||1);
    for(var k=0;k<n;k++){
      var t=(k+1)/(n+1);
      var px=cx+Math.cos(a)*((outer+10)+((rim-outer-10)*t)),py=cy+Math.sin(a)*((outer+10)+((rim-outer-10)*t));
      ctx.beginPath();ctx.fillStyle='rgba(210,205,188,0.80)';ctx.arc(px,py,2.1,0,Math.PI*2);ctx.fill();
    }
    ctx.beginPath();ctx.fillStyle='#2E2514';ctx.strokeStyle='#EECD5C';ctx.lineWidth=1;
    ctx.arc(ax,ay,5.5,0,Math.PI*2);ctx.fill();ctx.stroke();
    var label=nameOf[s.bioguide];
    var lx=cx+Math.cos(a)*(rim+24),ly=cy+Math.sin(a)*(rim+16);
    ctx.fillStyle='#D2CDBC';
    ctx.fillText(label+' ('+s.people+')',Math.max(48,Math.min(w-48,lx)),Math.max(9,Math.min(h-9,ly)));
    HITS.push({x:ax,y:ay,r:14,name:label,note:label+' — '+s.people+' '+(s.people===1?'person':'people')+' attached to both offices'});
  });

  function ring(list,radius,fade){
    list.forEach(function(p,i){
      var a=-Math.PI/2+(i/Math.max(1,list.length))*Math.PI*2;
      var px=cx+Math.cos(a)*radius,py=cy+Math.sin(a)*radius;
      var rr=3+5*Math.sqrt(Math.max(0,p.co_tenure_degree||0)/maxdeg);
      ctx.beginPath();
      ctx.fillStyle=(fade?'rgba(157,151,132,':'rgba(238,205,92,')+(tierAlpha(p.identity_tier)*(fade?0.55:1)).toFixed(2)+')';
      ctx.arc(px,py,rr,0,Math.PI*2);ctx.fill();
      HITS.push({x:px,y:py,r:Math.max(9,rr+4),name:null,
        note:(p.title_last||'title not on the record')+' — '+(p.status||'status unknown')+
             ', identity tier '+(p.identity_tier||'—')+', '+num(p.co_tenure_degree)+' people on the same payroll office at the same time'});
    });
  }
  ring(dep,outer,true);
  ring(cur,inner,false);

  ctx.beginPath();ctx.fillStyle='#EECD5C';ctx.arc(cx,cy,13,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#1D1912';ctx.font='700 10px -apple-system,Segoe UI,Roboto,sans-serif';
  ctx.fillText(String(r.chamber||'').slice(0,1).toUpperCase()||'·',cx,cy+0.5);
  ctx.fillStyle='#F3F3E6';ctx.font='600 12px -apple-system,Segoe UI,Roboto,sans-serif';
  ctx.fillText(r.display_name||'',cx,cy+28);
}

/* ------------------------------------------------------------------- roster */
var COLS=[
 {k:'display_name',t:'Member',n:false,sortable:false},
 {k:'party',t:'Party',n:false,sortable:false},
 {k:'chamber',t:'Chamber',n:false,sortable:false},
 {k:'state',t:'State',n:false,sortable:false},
 {k:'staff_total',t:'Staff on record',n:true,sortable:true},
 {k:'staff_current',t:'Current',n:true,sortable:true},
 {k:'shared_staff',t:'Shared staff',n:true,sortable:true},
 {k:'moves_in',t:'Moves in',n:true,sortable:true},
 {k:'moves_out',t:'Moves out',n:true,sortable:true}
];
function drawRosterHead(){
  document.getElementById('stf-rosterhead').innerHTML=COLS.map(function(c){
    if(!c.sortable)return '<th'+(c.n?' class=stf-n':'')+'>'+esc(c.t)+'</th>';
    var on=(sortKey===c.k);
    return '<th'+(c.n?' class=stf-n':'')+' aria-sort="'+(on?(sortDir<0?'descending':'ascending'):'none')+'">'+
      '<button type=button class="stf-sortbtn'+(on?' stf-sorton':'')+'" data-stfsort="'+c.k+'">'+esc(c.t)+(on?(sortDir<0?' ▾':' ▴'):'')+'</button></th>';
  }).join('');
}
function drawRoster(){
  drawRosterHead();
  var list=ROWS.slice().sort(function(a,b){
    var x=a[sortKey],y=b[sortKey];
    if(x===y)return String(a.display_name).localeCompare(String(b.display_name));
    return (x<y?-1:1)*sortDir;
  });
  var shown=showAll?list:list.slice(0,CAP);
  document.querySelector('#stf-roster tbody').innerHTML=shown.map(function(r){
    return '<tr><td><a class=mlink href="cu-member-desk.html?m='+encodeURIComponent(r.display_name)+'">'+esc(r.display_name)+'</a></td>'+
      '<td class=stf-dim>'+esc(r.party||'—')+'</td><td class=stf-dim>'+esc(r.chamber||'—')+'</td><td class=stf-dim>'+esc(r.state||'—')+'</td>'+
      '<td class=stf-n>'+num(r.staff_total)+'</td><td class=stf-n>'+num(r.staff_current)+'</td><td class=stf-n>'+num(r.shared_staff)+'</td>'+
      '<td class=stf-n>'+num(r.moves_in)+'</td><td class=stf-n>'+num(r.moves_out)+'</td></tr>';
  }).join('');
  document.getElementById('stf-showall').textContent=showAll?('Show first '+CAP):('Show all '+list.length);
  document.getElementById('stf-rostercount').textContent='Showing '+shown.length+' of '+list.length+' members.';
}

/* -------------------------------------------------------------------- select */
function select(name){
  var bio=(name&&byName[name])?byName[name]:null;
  if(!bio){
    var keys=Object.keys(byName);
    if(name){
      var lower=String(name).toLowerCase();
      for(var i=0;i<keys.length;i++){if(keys[i].toLowerCase()===lower){bio=byName[keys[i]];break;}}
    }
  }
  if(!bio){
    var best=null,bestN=-1;
    BIOS.forEach(function(b){if(MEM[b].staff_total>bestN){bestN=MEM[b].staff_total;best=b;}});
    bio=best;
  }
  if(!bio)return;
  current=bio;
  var r=MEM[bio];
  document.getElementById('stf-stationname').textContent=r.display_name;
  document.getElementById('stf-stationmeta').textContent=' · '+(r.party||'—')+' · '+(r.chamber||'—')+(r.state?' · '+r.state:'');
  document.getElementById('stf-pick').value=r.display_name;
  document.getElementById('stf-readout').textContent='';
  drawStationTiles(r);drawTables(r);drawChips(r);drawStation(r);
}
function pick(name){
  if(window.gotoStaffer){window.gotoStaffer(name);return;}
  select(name);
}

/* ---------------------------------------------------------------------- wire */
document.getElementById('stf-names').innerHTML=Object.keys(byName).sort().map(function(n){
  return '<option value="'+esc(n)+'"></option>';}).join('');
drawProvenance();drawCoverage();drawLegend();
document.getElementById('stf-rostersub').textContent='All '+ROWS.length+' members. Click a column heading to sort. Names open the member desk.';
drawRoster();

document.getElementById('stf-pick').addEventListener('change',function(){pick(this.value);});
document.getElementById('stf-pick').addEventListener('keydown',function(e){if(e.key==='Enter'){pick(this.value);}});
document.getElementById('stf-showall').addEventListener('click',function(){showAll=!showAll;drawRoster();});
document.getElementById('stf-roster').addEventListener('click',function(e){
  var b=e.target.closest?e.target.closest('button[data-stfsort]'):null;if(!b)return;
  var k=b.getAttribute('data-stfsort');
  if(sortKey===k)sortDir=-sortDir;else{sortKey=k;sortDir=-1;}
  drawRoster();
});
document.getElementById('stf-chips').addEventListener('click',function(e){
  var b=e.target.closest?e.target.closest('button[data-stfname]'):null;if(!b)return;
  pick(b.getAttribute('data-stfname'));
});
(function(){
  var c=document.getElementById('stf-canvas');
  function at(e){
    var rect=c.getBoundingClientRect();
    return {x:e.clientX-rect.left,y:e.clientY-rect.top};
  }
  function hit(p){
    for(var i=0;i<HITS.length;i++){
      var d=Math.hypot(HITS[i].x-p.x,HITS[i].y-p.y);
      if(d<=HITS[i].r)return HITS[i];
    }
    return null;
  }
  c.addEventListener('mousemove',function(e){
    var t=hit(at(e));
    c.style.cursor=(t&&t.name)?'pointer':'default';
    document.getElementById('stf-readout').textContent=t?t.note:'';
  });
  c.addEventListener('mouseleave',function(){document.getElementById('stf-readout').textContent='';});
  c.addEventListener('click',function(e){
    var t=hit(at(e));
    if(t&&t.name)pick(t.name);
  });
  /* The canvas box can change without a window resize event -- a scrollbar appearing when the
     roster expands, a container reflow, the panel being unhidden in the combined page at a
     width it was never laid out at. A stale backing store then gets stretched into the new box
     and every hit-test coordinate is wrong. Observe the element itself; the window listener
     below stays as the fallback where ResizeObserver is unavailable. Redrawing changes only the
     backing store, never the element's CSS box, so this cannot feed itself. */
  var rot=null;
  try{
    if(window.ResizeObserver){
      new ResizeObserver(function(){
        if(rot)clearTimeout(rot);
        rot=setTimeout(function(){if(current)drawStation(MEM[current]);},120);
      }).observe(c);
    }
  }catch(x){}
})();
var rzt=null;
window.addEventListener('resize',function(){
  if(rzt)clearTimeout(rzt);
  rzt=setTimeout(function(){if(current)drawStation(MEM[current]);},150);
});

/* deep link: ?s=<Name>, with ?m=<Name> as a fallback */
function nameFromURL(){
  try{var sp=new URLSearchParams(location.search);return sp.get('s')||sp.get('m')||null;}catch(x){return null;}
}
select(nameFromURL());

/* combined-page hooks */
window.__stafferOpen=function(n){select(n||current&&MEM[current].display_name||null);};
window.__stafferShow=function(){if(current)drawStation(MEM[current]);};

/* Local live view, if and only if a local staffer service answers.  This is the only
   network call this page makes and it is never a script load; a rejection is swallowed. */
try{
  if(window.fetch){
    window.fetch('http://127.0.0.1:8841/api/staffer-live?limit=1',{mode:'no-cors',cache:'no-store'})
      .then(function(){var el=document.getElementById('stf-livelink');if(el){el.href='http://127.0.0.1:8841/staffer-live/';el.hidden=false;}})
      .catch(function(){});
  }
}catch(x){}
})();
</script>
</body></html>
