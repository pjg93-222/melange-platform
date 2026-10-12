<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Melange — Influence map</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 208'><path d='M108 20L64 64L28 104L64 144L20 188M64 64L100 104L64 144' fill='none' stroke='%23EECD5C' stroke-width='24' stroke-linejoin='miter' stroke-miterlimit='6'/></svg>">
<style>
:root{color-scheme:dark;--bg:#1D1912;--bar:#282113;--panel:#2E2514;--panel-hover:#3B2E16;--rule:#433317;--border:#564019;--faint:#9D9784;--muted:#A49D8A;--text-soft:#D2CDBC;--text:#F3F3E6;--gold:#EECD5C;--gold-fill:#D2A63C;--gold-deep:#BB8525;--ui-display:"Century Gothic","Tw Cen MT",Futura,"Avenir Next","Segoe UI",sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1D1912;color:#F3F3E6;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;overflow:hidden}
#app{display:grid;grid-template-rows:auto auto 1fr;height:100vh;height:100dvh}
header{height:44px;padding:0 14px;border-bottom:1px solid #433317;display:flex;align-items:center;gap:12px;flex-wrap:nowrap}
header h1{font-family:var(--ui-display);font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.18em;flex-shrink:0;display:flex;align-items:center;gap:6px}
/* subtitle folded out of the 44px console row; the string stays in the DOM */
header .sub{display:none}
header .brand-glyph{flex:0 0 auto}
header .sub{color:#A49D8A;font-size:12px;flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#bar{display:flex;flex-wrap:nowrap;gap:10px;align-items:center;height:44px;padding:0 14px;border-bottom:1px solid #433317;background:#282113;overflow:visible}
#bar label{font-family:var(--ui-display);color:#A49D8A;font-size:10px;text-transform:uppercase;letter-spacing:.14em;margin-right:3px}
#bar > span{flex:0 0 auto}
select{background:#1D1912;color:#F3F3E6;border:1px solid #564019;border-radius:6px;padding:5px 8px;font-size:12px;min-width:200px}
select:hover,input#search:hover{border-color:#6B4E1F}
.seg{display:inline-flex;border:1px solid #564019;border-radius:6px;overflow:hidden}
.seg button{background:#1D1912;color:#A49D8A;border:0;padding:5px 9px;cursor:pointer;font-size:11px;transition:background 140ms ease-out,color 140ms ease-out}
.seg button:not(.on):hover{background:#3B2E16;color:#F3F3E6}
.seg button.on{background:#D2A63C;color:#1D1912;font-weight:600}
.seg button.on:hover{background:#EECD5C}
input#search{background:#1D1912;color:#F3F3E6;border:1px solid #564019;border-radius:6px;padding:6px 9px;width:170px}
#main{display:grid;grid-template-columns:236px 1fr 250px;min-height:0}
canvas{touch-action:none}
@media(max-width:640px){
  #app{height:auto;min-height:100vh;min-height:100dvh}
  #main{grid-template-columns:1fr}
  #left{display:none}                          /* phone: hide aggregate stats/budget/optimizer -> graph-first */
  #netwrap{height:66vh;min-height:340px}        /* canvas up top, prominent */
  #right{border:0;border-top:1px solid #433317;max-height:none}   /* member details appear BELOW the graph on tap */
  header{flex-wrap:wrap;gap:6px}header .sub{white-space:normal;flex-basis:100%}
  #bar{padding:8px 11px;gap:8px}
  select{min-width:0;width:100%}input#search{width:100%}
  .seg button{padding:9px 13px}
}
#left,#right{padding:12px 14px;overflow:auto;border-right:1px solid #433317}
#right{border-right:0;border-left:1px solid #433317}
#left h3,#right h3:not(#mname){font-family:var(--ui-display);font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:#A49D8A;margin-bottom:9px;font-weight:700}
.stat{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #433317}
.stat b{font-variant-numeric:tabular-nums}
.big{font-size:22px;font-weight:800;margin:4px 0}
.pill{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.tgt{padding:6px 7px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;gap:6px;align-items:center}
.tgt:hover{background:#3B2E16}
.tgt small{color:#A49D8A}
#cv{display:block;width:100%;height:100%;cursor:grab}
#netwrap{position:relative;min-width:0}
#tip{position:fixed;pointer-events:none;background:#2E2514ee;border:1px solid #564019;border-radius:7px;padding:8px 10px;font-size:12px;max-width:250px;display:none;z-index:9;box-shadow:0 6px 24px rgba(18,14,7,.63)}
#tip b{color:#F3F3E6}
#legend{position:absolute;left:10px;bottom:10px;background:#1D1912cc;border:1px solid #433317;border-radius:8px;padding:8px 10px;font-size:11px}
#legend span{display:inline-flex;align-items:center;margin-right:10px}
.hint{color:#9D9784;font-size:11px;margin-top:8px}
#slider{width:100%;margin:8px 0 2px;accent-color:#EECD5C}
.frow{display:flex;justify-content:space-between;font-size:11px;color:#A49D8A}
#optcard{margin-top:16px;border-top:1px solid #433317;padding-top:12px}
.optline{font-size:12px;color:#D2CDBC;margin-bottom:6px}
.optline b{color:#F3F3E6;font-variant-numeric:tabular-nums}
.oa{display:flex;justify-content:space-between;padding:3px 6px;border-radius:5px;cursor:pointer;font-size:12px}
.oa:hover{background:#3B2E16}.oa small{color:#A49D8A}
.conv{color:#16a34a}
#mpanel{display:none}
.mrow{display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-bottom:1px solid #433317}
.mrow b{font-variant-numeric:tabular-nums;color:#F3F3E6}
.scc label{display:block;font-size:11px;color:#A49D8A;margin:8px 0 2px}
.scc input[type=range]{width:100%;accent-color:#16a34a}
.scc input.comp{accent-color:#ef4444}
.delta{font-weight:800;font-variant-numeric:tabular-nums}.up{color:#16a34a}.down{color:#ef4444}
.recipe{font-size:12px;color:#D2CDBC;line-height:1.55}.recipe b{color:#F3F3E6}
.recruit{display:inline-block;background:#3B2E16;border:1px solid #564019;border-radius:5px;padding:1px 6px;margin:2px 3px 0 0;font-size:11px;cursor:pointer}
.recruit:hover{border-color:#EECD5C}
.abtn{background:#D2A63C;color:#1D1912;border:0;border-radius:6px;padding:6px 11px;cursor:pointer;font-size:12px;margin:10px 6px 0 0}
.abtn.ghost{background:#1D1912;color:#A49D8A;border:1px solid #564019}
.scout{margin-top:8px;font-size:12px}
.defwarn{color:#f59e0b;font-weight:700}
#engchk{position:absolute;right:10px;bottom:10px;font-size:10px;color:#9D9784;background:#1D1912cc;border:1px solid #433317;border-radius:6px;padding:4px 7px}
#mdlink{position:absolute;left:10px;top:10px;display:none;background:#D2A63C;color:#1D1912;border-radius:7px;padding:7px 12px;font-size:12px;font-weight:600;cursor:pointer;box-shadow:0 4px 16px rgba(18,14,7,.44);z-index:8}
#mdlink:hover{background:#EECD5C;text-decoration:none}#mdlink small{font-weight:400;opacity:.85}
.h2h{display:flex;height:15px;border-radius:7px;overflow:hidden;margin:7px 0 3px;border:1px solid #564019}
.h2h>span{display:block}.h2h .cu{background:#16a34a}.h2h .ind{background:#ef4444}
.h2hlab{display:flex;justify-content:space-between;font-size:10px;color:#A49D8A}
.h2hbe{font-size:11px;color:#D2CDBC;margin-top:4px}.h2hbe b{color:#F3F3E6}
#rtog{margin:2px 0 0}#rtog button,#pmode button,#execseg button{padding:4px 9px;font-size:11px}
.desklink{align-self:center;flex-shrink:0;border:1px solid #564019;border-radius:6px;padding:7px 13px;font-size:12px;font-weight:600;color:#D2CDBC;text-decoration:none;white-space:nowrap}
.desklink:hover{border-color:#EECD5C;color:#F3F3E6;background:#3B2E16}
/* Every member name on this page carries that member's desk address (cu-member-desk.html?m=NAME).
   .mlink = the name IS the profile link (these were dead text).
   .mprof = a small ▤ badge appended where the name already owns a same-page action
            (select this member in the network) that must not be taken away. */
.mlink{color:inherit;text-decoration:none;border-bottom:1px dotted #6B4E1F}
.mlink:hover{color:#F3F3E6;border-bottom-color:#EECD5C}
.mprof{display:inline-block;margin-left:3px;color:#9D9784;text-decoration:none;font-size:11px;line-height:1}
.mprof:hover{color:#EECD5C}
.mlink:focus-visible,.mprof:focus-visible{outline:2px solid #EECD5C;outline-offset:2px;border-radius:3px}
.tgt:focus-visible,.oa:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,a:focus-visible{outline:2px solid #EECD5C;outline-offset:1px}
/* --- pressure-to-flip gauge (member panel) : how much pro-pressure exists vs. the flip line --- */
.pgwrap{margin:6px 0 2px}
.pgcap{display:flex;justify-content:space-between;font-size:11px;color:#A49D8A;margin-bottom:4px}
.pgcap b{color:#F3F3E6;font-variant-numeric:tabular-nums}
.pgauge{position:relative;height:20px;border:1px solid #564019;border-radius:6px;background:#1D1912;overflow:hidden}
.pg-fill{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,#15803d,#22c55e)}
.pg-need{position:absolute;top:0;bottom:0;background:repeating-linear-gradient(45deg,#7f1d1d 0,#7f1d1d 5px,#b91c1c 5px,#b91c1c 10px);opacity:.9}
.pg-line{position:absolute;top:-3px;bottom:-3px;right:0;width:2px;background:#fbbf24;box-shadow:0 0 6px #fbbf24}
.pglab{display:flex;justify-content:space-between;font-size:10px;color:#A49D8A;margin-top:3px}
.pgnote{font-size:12px;color:#D2CDBC;margin-top:7px;line-height:1.55}.pgnote b{color:#F3F3E6}
#speechcov{margin-top:15px;border-top:1px solid #433317;padding-top:10px;color:#A49D8A;font-size:10px}
#speechcov summary{cursor:pointer;color:#C9A227;font-family:var(--ui-display);text-transform:uppercase;letter-spacing:.1em;font-weight:700}
#speechcov table{width:100%;border-collapse:collapse;margin-top:8px;font-variant-numeric:tabular-nums}
#speechcov th,#speechcov td{padding:3px 2px;border-bottom:1px solid #352912;text-align:right}
#speechcov th:first-child,#speechcov td:first-child{text-align:left}
#speechcov .scnote{margin-top:6px;line-height:1.4;color:#9D9784}
/* --- cascade overlay: red->green who-joins map --- */
#cascbar{position:absolute;left:50%;top:10px;transform:translateX(-50%);display:none;background:#1D1912ee;border:1px solid #564019;border-radius:9px;padding:7px 13px;font-size:12px;box-shadow:0 6px 24px rgba(18,14,7,.63);z-index:8;text-align:center;max-width:92%;white-space:nowrap}
#cascbar b{color:#F3F3E6}
#cascbar .scale{display:inline-block;width:110px;height:9px;border-radius:5px;vertical-align:middle;margin:0 7px;background:linear-gradient(90deg,#ef4444,#f59e0b,#16a34a)}
#cascbar .lo,#cascbar .hi{font-size:10px;color:#A49D8A;vertical-align:middle}
#layoutnote{position:absolute;right:10px;top:10px;display:none;max-width:390px;background:#1D1912ee;border:1px solid #564019;border-radius:8px;padding:6px 10px;color:#A49D8A;font-size:10px;line-height:1.4;box-shadow:0 4px 16px rgba(18,14,7,.44);z-index:7}
#layoutnote b{color:#F3F3E6}
/* ---- Console View popover (ratified 2026-08-24): the six draw-controls live behind one
   button; Issue, Network and Find stay on the bar. Chrome only. ---- */
#viewbtn{background:#1D1912;color:#A49D8A;border:1px solid #564019;border-radius:6px;padding:5px 11px;cursor:pointer;font-size:11px;flex:0 0 auto;transition:background 140ms ease-out,border-color 140ms ease-out,color 140ms ease-out}
#viewbtn:hover{border-color:#6B4E1F;color:#F3F3E6;background:#3B2E16}
#viewbtn[aria-expanded=true]{background:#3B2E16;color:#F3F3E6;border-color:#6B4E1F}
#viewpanel{position:absolute;right:14px;top:96px;z-index:30;display:none;background:rgba(40,33,19,.94);border:1px solid #564019;border-radius:9px;padding:12px 14px;box-shadow:0 10px 34px rgba(18,14,7,.55);max-width:min(680px,92vw)}
#viewpanel.open{display:block}
#viewpanel > span{display:flex;align-items:center;flex-wrap:wrap;gap:6px;padding:6px 0;border-bottom:1px solid #433317}
#viewpanel > span:last-child{border-bottom:0}
#viewpanel label{font-family:var(--ui-display);color:#A49D8A;font-size:10px;text-transform:uppercase;letter-spacing:.14em;display:inline-block;min-width:64px}
/* resize() sizes the canvas FROM #netwrap and a grid item defaults to min-height:auto, so
   the wrapper could grow past its row but never shrink back (measured latching 978->981px in
   a 712px row). The former duplicate holder of this id (the Network bar span) is now #netcfg,
   so these rules reach ONLY the canvas wrapper. */
#netwrap{min-height:0}
#main{min-height:0}
/* shadows tinted to the warm base instead of pure black */
@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition-duration:1ms!important;animation-duration:1ms!important}}
</style></head><body><div id=app>
<header><h1><svg class=brand-glyph width="19" height="31" viewBox="0 0 128 208" aria-hidden="true"><path d="M108 20 L64 64 L28 104 L64 144 L20 188 M64 64 L100 104 L64 144" fill="none" stroke="#EECD5C" stroke-width="19" stroke-linejoin="miter" stroke-miterlimit="6"/></svg>Melange — Influence map</h1>
<span class=sub>533 members of Congress, by where they stand on each issue</span>
<a id=desklink class=desklink href="cu-member-desk.html">Member desk ▸</a><a id=lobnetlink class=desklink href="lobby-network.html">Industry ▸</a><a id=stflink class=desklink href="staffer-network.html">Staffer network ▸</a></header>
<div id=bar>
<span><label>Issue</label><select id=issue aria-label="Select policy issue"></select><span title="CU advocacy frame: positive means aligned with credit-union priorities; CFPB positive means restrain CFPB reach" style="color:#A49D8A;font-size:10px;margin-left:5px">CU frame ⓘ</span></span>
<span id=netcfg style=display:none><label>Network</label><span class=seg id=netseg title="Which network the map and every engine on this page run on. Credit union = the 17-topic CU cosponsorship universe (the default, and what every published figure uses). All bills = the holistic 117th-119th all-bills graph. Each network has its own node layout, computed in one shared coordinate frame so the views stay directly comparable: a member that moves has been moved by its ties, not by a redrawn frame."><button data-net=cu class=on>Credit union</button><button data-net=holistic>All bills</button><button data-net=holistic_norm>All bills · normalized</button><button data-net=bank_reg>Bank regulation</button><button data-net=crypto>Crypto</button></span></span>
<span><label>Find</label><input id=search aria-label="Find member" placeholder="member name…" list=names><datalist id=names></datalist></span>
<button id=viewbtn type=button aria-expanded=false aria-controls=viewpanel title="Mode, approach, color, tier, layout and layer controls">View options</button>
</div>
<div id=viewpanel role=group aria-label="View options">
<span><label>Mode</label><span class=seg id=mode><button data-m=coal class=on>Coalition (build)</button><button data-m=flip>Persuade</button></span></span>
<span><label>Approach</label><span class=seg id=postureseg title="Coalition approach. Normal = ordinary relationship building, no second-industry term (the default, and the state whenever no bill is registered as contested). Show rival = the counterparty read is shown beside the normal ranking but never reorders it. Head-to-head = per-bill, with an explicitly named counterparty industry set. Approach is per-bill by design, never a global setting."><button data-pos=standard class=on>Normal</button><button data-pos=advisory>Show rival</button><button data-pos=contested>Head-to-head</button></span><span id=postbadge style="font-size:10px;color:#A49D8A;margin-left:6px"></span></span>
<span><label>Color</label><span class=seg id=color><button data-c=issue class=on>Where they stand</button><button data-c=central title="Recomputed from issue-adjusted influence probabilities whenever the legislative area changes">Reach on this issue</button><button data-c=party>Party</button><button data-c=gen>Overall</button><button data-c=elec>Electoral</button><button data-c=recept>How reachable</button><button data-c=antag>Combative in hearings</button></span></span>
<span><label>Tier</label><span class=seg id=tierseg title="Primacy tier (legislative + press inputs; drops the committee & demographic pillars): Tier 1 = top 13, Tier 2 = next 24, Tier 3 = next 48; the rest are untiered. The allocation engine uses these tiers for its general-priority term."><button data-t=0 class=on>All</button><button data-t=1>T1</button><button data-t=2>T2</button><button data-t=3>T3</button></span></span>
<span id=fieldhonorrow style=display:none><label>Field honors</label><span class=seg id=fieldhonorseg title="Earned recognition in the selected field; filtering does not change its cohort or cutoffs."><button data-h=0 class=on>All</button><button data-h=1>Champion</button><button data-h=2>Ally</button><button data-h=3>Highly Persuadable</button></span></span>
<span id=standingfilterrow style=display:none><label>Overall standing</label><select id=standingfilter aria-label="Filter overall behavioral standing"><option value="">All standings</option></select></span>
<span><label>Layout</label><span class=seg id=ltog><button data-l=cosp class=on title="Stable cosponsorship network">Standard view</button><button data-l=area title="Re-layout the same members and ties using this area's adjusted influence weights">Group by Policy Area</button><button data-l=committee>Committees</button></span></span>
<span><label>Show connections</label><span id=lybar style="display:inline-flex;gap:8px;align-items:center;font-size:11px;color:#A49D8A">
 <label style=cursor:pointer><input type=checkbox id=ly_c aria-label="Show cosponsor layer" checked> Co-sponsor</label>
 <label style=cursor:pointer><input type=checkbox id=ly_s aria-label="Show social layer" checked> social</label>
 <label style=cursor:pointer><input type=checkbox id=ly_h aria-label="Show committee layer" checked> Committee</label>
 <label style=cursor:pointer><input type=checkbox id=ly_e aria-label="Show working relationships" checked> Working relationships</label>
 <label style=cursor:pointer title="cross-media references (speeches/interviews/reposts), green=positive red=negative"><input type=checkbox id=ly_r aria-label="Show mentions of each other"> Mentions of each other</label>
 <label style=cursor:pointer title="appeared together on the same broadcast (substantial joint interview-episode appearances, 2+ shared episodes); violet=cross-party"><input type=checkbox id=ly_a aria-label="Show who was on TV together"> On TV together</label>
</span></span>
</div>
<div id=main>
 <div id=left>
   <h3>This issue</h3>
   <div id=istats></div>
   <div id=budgetwrap>
     <h3 style=margin-top:16px id=bhdr>Issue budget</h3>
     <input type=range id=slider aria-label="Issue budget" min=0 max=100 value=100>
     <div class=frow><span>$0</span><span id=bmax>$2M</span></div>
     <div class=big id=money>$0</div>
     <div id=mdetail class=hint></div>
   </div>
   <div id=optcard>
     <h3 id=opthdr>Budget across all issues</h3>
     <div id=optbody></div>
   </div>
   <div id=postcard>
     <h3 style=margin-top:16px id=posthdr>Approach</h3>
     <div id=postbody></div>
   </div>
    <details id=speechcov><summary id=speechcovsum>Speech corpus</summary><div id=speechcovbody></div></details>
 </div>
 <div id=netwrap><canvas id=cv></canvas><a id=mdlink href=#></a>
   <div id=cascbar></div>
   <div id=layoutnote></div>
   <div id=netnote style="color:#C9A227;font-size:11px;margin-top:2px"></div>
   <div id=legend></div>
   <div id=engchk></div>
 </div>
 <div id=right>
   <div id=tpanel><h3>Targets <span id=tgtmode style=color:#9D9784></span> <span style=float:right;color:#9D9784;font-weight:400;text-transform:none>click a node →</span></h3>
     <span class=seg id=rtog><button data-r=alloc class=on>Where the budget lands</button><button data-r=flip>Most persuadable</button></span>
     <div id=targets style=margin-top:8px></div></div>
   <div id=mpanel>
     <h3 id=mname></h3><div id=mtier></div><div id=mstance></div>
     <h3 style=margin-top:14px>Who can reach them <span style=color:#9D9784;font-weight:400;text-transform:none>· colleague influence</span></h3>
     <div id=mprime class=recipe></div>
     <h3 style=margin-top:14px>Scenario <span style=color:#9D9784;font-weight:400;text-transform:none>· live what-if</span></h3>
     <div class=scc>
       <label>CU money &nbsp;<b id=culab style=color:#16a34a>$0k</b></label><input type=range id=cusl aria-label="Credit union scenario money" min=0 max=20 step=1 value=0>
       <label>Competitor money &nbsp;<b id=colab style=color:#ef4444>$0k</b></label><input type=range id=cosl class=comp aria-label="Competitor scenario money" min=0 max=30 step=1 value=0>
       <div id=scout class=scout></div>
       <div style=margin-top:10px><div class=h2hlab><span style=color:#16a34a>CU pull</span><span>chance they move <b id=h2hpct style=color:#F3F3E6>50%</b></span><span style=color:#ef4444>industry</span></div>
         <div class=h2h><span class=cu id=h2hcu style=width:50%></span><span class=ind id=h2hind style=width:50%></span></div>
         <div class=h2hbe id=h2hbe></div></div>
     </div>
     <h3 style=margin-top:14px>Route to a signature <span style=color:#9D9784;font-weight:400;text-transform:none>· provably-optimal routes</span></h3>
     <div class=scc>
       <span class=seg id=pmode style=margin-bottom:5px><button data-p=path class=on title="Most-reliable recruitment chain from this member to a target vote">Path</button><button data-p=best title="Which pro member should introduce the bill to best reach a target">Best</button><button data-p=multi title="Reach several targets at once from this member (shared recruitment tree)">Multi</button><button data-p=budget title="Spend $X to recruit the widest diversified set of members">Budget</button><button data-p=cascade title="Seed this member as a co-sponsor and watch who is likely to join the cascade">Cascade</button></span>
       <span class=seg id=cascsub style="display:none;margin:0 0 5px"><button data-cs=who class=on title="Color the map by who activates if this one member co-sponsors">Who joins</button><button data-cs=max title="Find the recruitment route from this member that maximizes the cascade">Max route</button><button data-cs=flip title="Which members to recruit to flip THIS member — maximize the likelihood they join (Persuade)">Flip route</button></span>
       <label id=plabel>bill sponsor <b id=seednm style=color:#16a34a></b> → reach target:</label>
       <input id=tgtsearch aria-label="Target member" placeholder="target member…" list=names style="background:#1D1912;color:#F3F3E6;border:1px solid #564019;border-radius:6px;padding:5px 8px;width:100%;margin:5px 0;font-size:12px">
       <button class=abtn id=pathbtn>Build</button>
       <button class=abtn ghost id=backbtn>← Targets</button>
       <div id=pathout class=scout></div>
       <h3 style=margin-top:13px;border-top:1px solid #433317;padding-top:11px>What if…</h3>
       <label>members drop out of the bill:</label>
       <input id=dropsearch aria-label="Members who drop out" placeholder="who backs out (comma-sep)" list=names style="background:#1D1912;color:#F3F3E6;border:1px solid #564019;border-radius:6px;padding:5px 8px;width:100%;margin:5px 0;font-size:12px">
       <label style=margin-top:4px>executive branch:</label>
       <span class=seg id=execseg style=margin-top:3px><button data-e=none class=on>neutral</button><button data-e=oppose>opposes</button><button data-e=support>supports</button></span>
       <div id=shockout class=scout></div>
       <div style=font-size:10px;color:#9D9784;margin-top:7px;line-height:1.45>Path = strongest chain. "Holds" = chance every ask lands first-try. Max route grows outward one ask at a time; flip route remains target-specific.</div>
     </div>
   </div>
 </div>
</div></div><div id=tip></div>
<script>
const DATA=__DATA__;
const N=DATA.name.length;
const RMQ=matchMedia('(prefers-reduced-motion: reduce)').matches;
/* ---------- member addressing (2026-08-02) ----------
   A member's profile lives in the member desk, addressed by NAME through ?m=<name>.
   DESK_HREF is the ONE place that mints that address, so no site on this page can invent a
   different one. mlink(i) turns a name into a real anchor (it was dead text); mprof(i) is the
   compact ▤ badge used where the name already carries the network's own select-this-member
   click, which stays exactly as it was. Both are keyboard-focusable real anchors.
   In the combined Moneyball.html these hrefs are intercepted by the shell controller and become
   an in-page view switch, so the 33MB document is never re-fetched (see fec/surfaces/make_combined.py). */
const DESK_FILE='cu-member-desk.html';
function mesc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function renderSpeechCoverage(){
  const box=document.getElementById('speechcov'),body=document.getElementById('speechcovbody'),sum=document.getElementById('speechcovsum');
  const profile=DATA.speech_profile_meta,corpus=DATA.speech_meta&&DATA.speech_meta.corpus,rec=corpus&&corpus.source_media_recovery;
  if(!corpus&&!profile){box.style.display='none';return;}
  sum.textContent=profile?`Speech profiles · ${Number(profile.counts&&profile.counts.speech_pass||0).toLocaleString()} pass`:`Speech corpus · ${Number(corpus.prepared_rows||0).toLocaleString()} verified clips`;
  const by=rec&&rec.by_source||{};
  const rows=Object.entries(by).map(([source,r])=>`<tr><td>${mesc(source)}</td><td>${Number(r.packages||0).toLocaleString()}</td><td>${Number((r.prepared_packages||0)+(r.exact_timed_packages||0)).toLocaleString()}</td><td>${Number(r.audited_clips||0).toLocaleString()}</td><td>${Number(r.pending_packages||0).toLocaleString()}</td></tr>`).join('');
  const profileNote=profile?`${Number(profile.counts.speech_pass||0).toLocaleString()} members pass on a public-word/interaction baseline; ${Number(profile.counts.general_audio_profiles||0).toLocaleString()} also have a combined all-settings audio baseline. Thin audio limits claim strength, not pass/fail. `:'';
  body.innerHTML=`<div class=scnote>${mesc(profileNote)}${rec?mesc(rec.status):'No recovery inventory'} · descriptive speech only; never changes Big Five, rank, or allocation.</div>${corpus?`<table><thead><tr><th>source</th><th>packages</th><th>bound</th><th>audited clips</th><th>pending</th></tr></thead><tbody>${rows}</tbody></table>`:''}`;
}
function deskHref(i){return DESK_FILE+'?m='+encodeURIComponent(DATA.name[i]);}
function mlink(i,label){
  if(i==null||i<0||DATA.name[i]==null)return mesc(label==null?'':label);
  return `<a class=mlink href="${deskHref(i)}" title="Open ${mesc(DATA.name[i])} — full profile in the member desk">${mesc(label==null?DATA.name[i]:label)}</a>`;
}
/* a container that selects a member in the network must not fight the profile anchor inside it,
   and must not steal a click the user made while selecting text. */
function rowSelect(e,el){
  if(e&&e.target&&e.target.closest&&e.target.closest('a'))return;   // the profile anchor owns its own click
  const s=window.getSelection&&window.getSelection();
  if(s&&String(s).length>0)return;
  const i=+el.dataset.i; if(i>=0)selectMember(i);
}
function mprof(i){
  if(i==null||i<0||DATA.name[i]==null)return '';
  return `<a class=mprof href="${deskHref(i)}" title="Open ${mesc(DATA.name[i])} — full profile in the member desk" aria-label="Open ${mesc(DATA.name[i])} in the member desk">▤</a>`;
}
// live node positions (tweened between the cosponsorship layout and the committee-cluster layout)
let LX=DATA.x.slice(), LY=DATA.y.slice(), layout='cosp';
const SC={C:'Champion',F:'Defender',A:'Ally',H:'Highly Persuadable',P:'Persuadable',G:'Lean Persuadable',T:'True Neutral',L:'Lean Skeptic',N:'Neutral',S:'Skeptic',K:'Highly Skeptical',O:'Opponent','-':'—'};
const STCOL={C:'#16a34a',F:'#2dd4bf',A:'#86efac',H:'#38bdf8',P:'#7dd3fc',G:'#bae6fd',T:'#9ca3af',N:'#8b949e',L:'#fde3c0',S:'#f59e0b',K:'#f97316',O:'#ef4444','-':'#3a414b'};
// Public honors are a separate projection. DATA.stance/gen remain the original model
// states consumed by every simulation, gate and route below. Never write honors into them.
const HONORS=!!DATA.honors_meta;
const HONOR_COL={'Champion':'#16a34a','Overall Champion':'#fbbf24','Ally':'#86efac','Highly Persuadable':'#38bdf8',
  'Persuadable':'#7dd3fc','Lean Persuadable':'#bae6fd','True Neutral':'#9ca3af','Lean Skeptic':'#fde3c0',
  'Skeptic':'#f59e0b','Highly Skeptical':'#f97316','Opponent':'#ef4444','Uncommitted':'#9ca3af','Insufficient evidence':'#59616b',
  'Supportive actions':'#b8c8b0','Opposing actions':'#ef4444','Mixed actions':'#d4a96c','Neutral actions':'#9ca3af',
  'No recorded action':'#59616b','Evidence unavailable':'#a78bfa'};
const STANDING_LABELS=['Champion','Uncommitted','Ally','Highly Persuadable','Persuadable','Lean Persuadable','True Neutral','Lean Skeptic','Skeptic','Highly Skeptical','Opponent','Insufficient evidence'];
function honorRow(i,j=issue){return j===DATA.issues.length-1?DATA.overall_honors[i]:DATA.honors[i][j];}
function priorityLabel(i){const t=DATA.overall_honors[i].priority_tier;return t===1?'Tier 1 Champion':t?'Tier '+t:'Outside priority tiers';}
function priorityRankText(i){const rank=DATA.overall_honors[i].rank;return rank==null?'No priority rank':'Priority rank #'+rank;}
function mixedRecordText(i){return DATA.overall_honors[i].mixed_record?' · Mixed record':'';}
function publicLabel(i,j=issue){
  if(!HONORS)return SC[j===DATA.issues.length-1?DATA.gen[i]:DATA.stance[i][j]];
  const h=honorRow(i,j);
  return j===DATA.issues.length-1?h.stance:(h.recognition||h.alignment);
}
function publicColor(i,j=issue){
  return HONORS?(HONOR_COL[publicLabel(i,j)]||'#59616b'):STCOL[j===DATA.issues.length-1?DATA.gen[i]:DATA.stance[i][j]];
}
function honorRankText(i,j=issue){
  if(!HONORS)return '';
  const h=honorRow(i,j);
  if(j===DATA.issues.length-1)return h.stance_rank_start==null?'No primacy rank':`Primacy rank ${h.stance_rank_start}${h.stance_rank_end!==h.stance_rank_start?'–'+h.stance_rank_end:''} of ${h.stance_cohort_size}`;
  if(h.rank_start==null)return h.alignment;
  return `Field rank ${h.rank_start}${h.rank_end!==h.rank_start?'–'+h.rank_end:''} of ${h.cohort_size} with recorded action`;
}
function honorEvidence(i,j=issue){
  if(!HONORS)return '';
  const h=honorRow(i,j),isOverall=j===DATA.issues.length-1;
  const counts=h.action_count==null?'':`${h.action_count} actions · ${h.positive_action_count||0} supportive · ${h.negative_action_count||0} opposing${isOverall?' · '+(h.neutral_action_count||0)+' neutral':''}`;
  const cut=(isOverall?h.stance_cutoffs:h.cutoffs)||{},opp=h.opposition_cutoffs||{};
  const thresholds=isOverall?`Primacy cutoffs: Champion 13 · Ally ${cut.ally} · Highly Persuadable ${cut.highly_persuadable} · Persuadable ${cut.persuadable} · Lean Persuadable ${cut.lean_persuadable}.`:
    `Field cutoffs: Champion ${cut.champion} · Ally ${cut.ally} · Highly Persuadable ${cut.highly_persuadable}.`;
  const method=isOverall?' Explicit opposing leadership can cap the rank label. Unrelated supportive activity does not cancel it.':` Raw field action score ${h.raw_field_score??'—'}; tensor ${h.tensor_tiebreak??'unavailable'} (${h.tensor_status}).`;
  const policy=DATA.honors_meta.policy||{},terms=(policy.congresses||[]).join(', ');
  const bands=isOverall?'Repeated leadership adverse to CU interests in the beneficiary/tensor evidence across Congresses: Opponent. Repeated leadership within one Congress: Highly Skeptical. One sponsorship or repeated opposition: Skeptic. Weaker opposition with low primacy: Lean Skeptic. Remaining low-primacy records: Uncommitted. ':'';
  const pattern=isOverall?`${h.mixed_record?'Mixed record · ':''}${h.record_pattern||''}`:'';
  const opposition=isOverall&&h.opposition_rank_start!=null?` Opposition rank ${h.opposition_rank_start}${h.opposition_rank_start!==h.opposition_rank_end?'–'+h.opposition_rank_end:''} of ${h.opposition_cohort_size}.`:'';
  return `<details class=hint><summary>${mesc(honorRankText(i,j))}</summary><div>${mesc(counts)}${counts?'<br>':''}${mesc(h.alignment)}. ${mesc(pattern)} ${mesc(thresholds)}${mesc(method)}${mesc(opposition)}<br>${mesc((isOverall?h.stance_reason:h.reason)||'')}<br>Evidence as of ${mesc(DATA.honors_meta.as_of)}. Labels follow primacy and demonstrated opposition.</div>`+
    `<div>${mesc(bands)}${terms?'Congresses '+mesc(terms)+'. ':''}Generation ${mesc(DATA.honors_meta.generation_id)}${isOverall?' · board '+mesc(DATA.honors_meta.overall_board_mode):''}.</div>`+
    `<div style="overflow-wrap:anywhere;margin-top:5px">Full action evidence is retained in generation ${mesc(DATA.honors_meta.generation_id)}, for ${mesc(DATA.name[i])}${isOverall?'':' / '+mesc(DATA.issues[j])}.</div></details>`;
}
function honorStats(j=issue){
  const counts={};for(let i=0;i<N;i++){const label=publicLabel(i,j);counts[label]=(counts[label]||0)+1;}
  const rows=Object.keys(HONOR_COL).filter(label=>counts[label]).map(label=>
    `<div class=stat><span><span class=pill style=background:${HONOR_COL[label]}></span>${mesc(label)}</span><b>${counts[label]}</b></div>`).join('');
  const h=honorRow(0,j),overall=j===DATA.issues.length-1;
  const priorityCounts=overall?[1,2,3,0].map(t=>[t,DATA.overall_honors.filter(r=>r.priority_tier===t).length]):[];
  const priorities=overall?'<div class=hint>Overall priority</div>'+priorityCounts.map(([t,n])=>`<div class=stat><span>${t===1?'Tier 1 Champion':t?'Tier '+t:'Outside priority tiers'}</span><b>${n}</b></div>`).join('')+'<div class=hint>Stance</div>':'';
  return priorities+rows+`<div class=hint>${overall?`${h.stance_cohort_size} members with recorded action. Labels follow the primacy board with explicit opposition evidence.`:`${h.cohort_size} members with recorded action; ranks and cutoffs remain fixed when filtering.`} As of ${mesc(DATA.honors_meta.as_of)}. Standing names are not persuasion probabilities.</div>`;
}
const PTCOL={D:'#3b82f6',R:'#ef4444',I:'#a855f7','?':'#6b7280'};
const ELCOL={c:'#f97316',g:'#d946ef',d:'#22d3ee',n:'#3a414b'};   // contest·primary / contest·general / defend / none
const PLAYLBL={c:'contest · primary',g:'contest · general',d:'defend',n:'none'};
let issue=3, mode='coal', colorBy='issue', hi=-1, sel=-1, budget=1000000, rmode='alloc', flipHi=null, pmodeVal='path';
let ORD=[], FUND={}, SPREAD=null;
let cascadeOn=false, cascP=null, cascSeeds=null;   // cascade mode: per-member P(joins) + seed set (feature: explicit cascade, red->green)
let cascSub='who';   // cascade sub-mode: 'who' = single-seed activation map · 'max' = influence-max recruitment route
const SHOW_HUB_CORE=true;   // display dial for the max-route hub-core disclosure line: false removes the line AND its sweep, and changes nothing else
// --- Legislative+Press TIER (display filter; precomputed in Python, JS only READS it — no mirror math) ---
let tierFilter=0;   // 0 = all shown; 1/2/3 = that priority tier; 4 = outside priority tiers
let fieldHonorFilter=0, standingFilter='';
const TIERS=DATA.tier||null, TRANK=DATA.tierrank||null, TDETAIL=DATA.tierdetail||null, TIERCOL={1:'#fbbf24',2:'#93c5fd',3:'#d6a86a'};
function tierEvidence(detail){
  if(!detail)return '';
  const ceiling=detail.tier_ceiling===0?'untiered':`Tier ${detail.tier_ceiling}`;
  const grievances=(detail.open_events||[]).join(', ')||'none';
  const deliveries=(detail.delivery_events||[]).join(', ')||'none';
  const safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const grievanceDetail=(detail.grievances||[]).map(g=>`<div style="margin-top:4px"><b>${safe(g.event_id)}</b>: ${safe(g.reason)}<br><span style="color:#D2CDBC">Cure: ${safe(g.cure||'not specified')}</span></div>`).join('');
  const deliveryDetail=(detail.deliveries||[]).map(d=>`<div style="margin-top:4px"><b>${safe(d.event_id)}</b>: ${safe(d.instrument)} on ${safe(d.bill)}${d.congress?' ('+safe(d.congress)+')':''} moved ${Number(d.credit||0).toFixed(3)} credit.</div>`).join('');
  return `<div style="color:#A49D8A;font-size:10px;margin-top:5px">Conduct ledger: grievance ${Number(detail.gross_severity||0).toFixed(2)} (${safe(grievances)}) - delivery ${Number(detail.delivered_credit||0).toFixed(2)} (${safe(deliveries)}) = net ${Number(detail.net_severity||0).toFixed(2)}; ceiling ${safe(ceiling)}.${grievanceDetail}${deliveryDetail}</div>`;
}
const CURVED_ISSUES=new Set((DATA.stance_curves&&DATA.stance_curves.issue_indices)||[]);
const tierCounts=TIERS?TIERS.reduce((a,t)=>(a[t]=(a[t]||0)+1,a),{}):{};
function viewFiltersActive(){return tierFilter>0||(HONORS&&(issue===DATA.issues.length-1?standingFilter!=='':fieldHonorFilter>0));}
function tierHidden(i){return (tierFilter>0&&(HONORS?DATA.overall_honors[i].priority_tier!==(tierFilter===4?0:tierFilter):TIERS&&TIERS[i]!==tierFilter))||(HONORS&&(issue===DATA.issues.length-1?(standingFilter!==''&&DATA.overall_honors[i].stance!==standingFilter):(fieldHonorFilter>0&&honorRow(i).recognition!==({1:'Champion',2:'Ally',3:'Highly Persuadable'}[fieldHonorFilter]))));}
function refreshHonorControls(){if(!HONORS)return;const overall=issue===DATA.issues.length-1;document.getElementById('fieldhonorrow').style.display=overall?'none':'flex';document.getElementById('standingfilterrow').style.display=overall?'flex':'none';}
if(HONORS){
  const seg=document.getElementById('tierseg');
  seg.title='Overall priority tiers from the active board: T1 Champion, T2 and T3. Priority tier is separate from observed behavioral standing. Filtering does not change ranks or cohorts.';
  seg.parentElement.querySelector('label').textContent='Overall priority';
  const outside=document.createElement('button');outside.dataset.t='4';outside.textContent='Outside priority tiers';seg.appendChild(outside);
  const standings=document.getElementById('standingfilter');
  STANDING_LABELS.forEach(label=>{const option=document.createElement('option');option.value=label;option.textContent=label;standings.appendChild(option);});
  standings.title='Stance from observed actions; these names are not persuasion probabilities. This filter does not alter the cohort or priority tiers.';
  document.querySelector('#color [data-c=issue]').textContent='Field record';
  document.querySelector('#color [data-c=gen]').textContent='Overall standing';
  document.getElementById('viewbtn').title='Mode, approach, color, priority, field honors, standing, layout and layer controls';
  refreshHonorControls();
}
// build issue dropdown
const isel=document.getElementById('issue');
DATA.issues.forEach((s,i)=>{let o=document.createElement('option');o.value=i;o.textContent=HONORS?(i===DATA.issues.length-1?'Overall CU record':s):s+(CURVED_ISSUES.has(i)?' · curved labels':'');isel.appendChild(o);});
isel.value=issue;
const dl=document.getElementById('names');
DATA.name.forEach(n=>{let o=document.createElement('option');o.value=n;dl.appendChild(o);});
const slider=document.getElementById('slider');
// canvas + camera
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
let DPR=Math.min(devicePixelRatio||1,2),W=0,H=0;
let cam={s:1,tx:0,ty:0},drag=null,panMoved=false;
function resize(){const r=cv.parentElement.getBoundingClientRect();W=r.width;H=r.height;cv.width=W*DPR;cv.height=H*DPR;cv.style.width=W+'px';cv.style.height=H+'px';fit();draw();}
function fit(){if(!Number.isFinite(W)||!Number.isFinite(H)||W<=48||H<=72)return;const xs=LX.filter(Number.isFinite),ys=LY.filter(Number.isFinite);if(!xs.length||!ys.length)return;const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);const s=Math.min((W-48)/Math.max(1,x1-x0),(H-72)/Math.max(1,y1-y0));cam.s=s;cam.tx=W/2-(x0+x1)*s/2;cam.ty=H/2-(y0+y1)*s/2;}
function toScreen(x,y){return [x*cam.s+cam.tx, y*cam.s+cam.ty];}
// funded set: flip = static per-target; coalition = budget-frontier seeds up to the slider budget (live lookup of precomputed path)
function fundedOrdered(){
  if(mode==='flip'){return Object.entries(DATA.flip[issue]).map(([i,v])=>[+i,v]).sort((a,b)=>b[1]-a[1]);}
  const f=DATA.frontier[issue];
  if(!f){return Object.entries(DATA.coal[issue]).map(([i,v])=>[+i,v]).sort((a,b)=>b[1]-a[1]);}
  const out=[];for(const [b,s,si] of f.path){if(b<=budget&&si>=0)out.push([si,Math.round(b/1000)]);}
  return out;
}
function frontierSpread(){const f=DATA.frontier[issue];if(!f)return null;let sp=f.base;for(const [b,s,si] of f.path){if(b<=budget)sp=s;}return sp;}
function computeFund(){ORD=fundedOrdered();FUND={};ORD.forEach(([i,v])=>FUND[i]=(mode==='flip'?v:50));SPREAD=(mode==='coal'&&DATA.frontier[issue])?frontierSpread():null;}
function allocOf(i){return FUND[i]||0;}
function kfmt(v){return String(+(+v).toFixed(3));}   // QME 2026-10-01: DATA flip/coal/opt.alloc are thousands of dollars, exact decimals when not whole ($500 -> 0.5); print them as is, no float noise
function receptCol(v){ // pressure_mult 0.50 (resistant, blue) .. 1.0 (grey) .. 1.60 (susceptible, red)
  const t=Math.max(0,Math.min(1,(v-0.5)/1.1));
  const lerp=(a,b,k)=>Math.round(a+(b-a)*k);
  const c=t<0.455?[lerp(96,139,t/0.455),lerp(165,148,t/0.455),lerp(250,158,t/0.455)]
                 :[lerp(139,239,(t-0.455)/0.545),lerp(148,68,(t-0.455)/0.545),lerp(158,68,(t-0.455)/0.545)];
  return `rgb(${c[0]},${c[1]},${c[2]})`;}
function antagCol(v){ // ratified hearing-antagonism facet: null (no hearing data) grey, z<=0 neutral, z>0 grey->red
  if(v==null)return '#30363d';
  if(v<=0)return '#8b949e';
  const t=Math.min(v/3,1);
  return `rgb(${Math.round(139+109*t)},${Math.round(148-67*t)},${Math.round(158-85*t)})`;
}
function centralCol(v){ // issue-specific outbound influence: low slate -> blue -> high gold
  v=Math.max(0,Math.min(1,Number(v)||0));const lerp=(a,b,k)=>Math.round(a+(b-a)*k);
  const c=v<.5?[lerp(51,37,v*2),lerp(65,99,v*2),lerp(85,235,v*2)]
               :[lerp(37,251,(v-.5)*2),lerp(99,191,(v-.5)*2),lerp(235,36,(v-.5)*2)];
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function colOf(i){if(colorBy==='central')return centralCol(CENTPCT[i]);if(colorBy==='party')return PTCOL[DATA.party[i]];if(colorBy==='gen')return publicColor(i,DATA.issues.length-1);if(colorBy==='elec')return ELCOL[DATA.play[i]];if(colorBy==='recept')return receptCol(DATA.recept?DATA.recept[i]:1);if(colorBy==='antag')return antagCol(DATA.antag?DATA.antag[i]:null);return publicColor(i);}
function cascCol(p){ // cascade join-likelihood: red #ef4444 (won't join) -> amber #f59e0b -> green #16a34a (near-certain)
  p=Math.max(0,Math.min(1,p));const lerp=(a,b,k)=>Math.round(a+(b-a)*k);
  return p<0.5?`rgb(${lerp(239,245,p/0.5)},${lerp(68,158,p/0.5)},${lerp(68,11,p/0.5)})`
              :`rgb(${lerp(245,22,(p-0.5)/0.5)},${lerp(158,163,(p-0.5)/0.5)},${lerp(11,74,(p-0.5)/0.5)})`;}
function radOf(i){const a=allocOf(i);return 2.0+(a>0?Math.min(Math.sqrt(a)*0.8,7):0);}   // capped so outsized-influence nodes don't swamp smaller ones
function draw(){
 ctx.setTransform(DPR,0,0,DPR,0,0);ctx.clearRect(0,0,W,H);
 const CASC=cascadeOn&&cascP;                                   // cascade mode: color every node red->green by P(joins)
 const RT=(!CASC&&route&&route.nodes&&route.nodes.length)?route:null;   // an active pathfinder route lights up its members
 const RC=RT&&routeCasc;                                         // route + per-member activation likelihood -> tint the field red->green
 let focus=-1, fset=null;
 if(!CASC){ if(RT){fset=new Set(RT.nodes);focus=(RT.seed!=null?RT.seed:RT.nodes[0]);} else focus=(hi>=0?hi:sel); }
 const dimming=!CASC&&(fset||focus>=0);                          // spotlight/route: fade everyone not in focus
 const inFoc=i=>fset?fset.has(i):(i===focus);
 if(layout!=='committee'){const _on=id=>{const el=document.getElementById(id);return !el||el.checked;};
 const LYON={c:_on('ly_c'),s:_on('ly_s'),h:_on('ly_h'),e:_on('ly_e'),p:_on('ly_a')};LYON.m=LYON.c||LYON.s;   // mixed edges show if either parent layer is on
 // 'p' = co-appearance provenance, which build_dashboard_bundle emits for holistic edges. It is
 // bound to the existing 'On TV together' box. Without the key LYON['p'] is undefined ->
 // falsy -> the draw loops skip that edge silently. LATENT today (the shipped holistic elay is
 // all c/m, so 0 edges are affected) but it fails closed and invisibly the moment one appears.
 if(layout==='area'){   // same underlying ties, but weight their display by the selected area's adjusted influence kernel
   for(let k=0;k<EDGES.length;k++){if(ELAYS&&!LYON[ELAYS[k]])continue;
     const [a,b]=EDGES[k];if(viewFiltersActive()&&(tierHidden(a)||tierHidden(b)))continue;
     const p=Math.max((PE[a]&&PE[a][b])||0,(PE[b]&&PE[b][a])||0);
     const A=toScreen(LX[a],LY[a]),B=toScreen(LX[b],LY[b]);ctx.beginPath();ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);
      ctx.lineWidth=.35+1.8*p;ctx.globalAlpha=Math.min(.5,.025+1.15*p);ctx.strokeStyle=p>0?'#58a6ff':'#6e7681';ctx.stroke();}
   ctx.globalAlpha=1;
 }else{
    ctx.lineWidth=0.5;ctx.strokeStyle='rgba(139,148,158,0.10)';ctx.beginPath();   // ties hidden in committee view (they'd crisscross the clusters)
   for(let k=0;k<EDGES.length;k++){if(ELAYS&&!LYON[ELAYS[k]])continue;
     const [a,b]=EDGES[k];if(viewFiltersActive()&&(tierHidden(a)||tierHidden(b)))continue;const A=toScreen(LX[a],LY[a]),B=toScreen(LX[b],LY[b]);ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);}
   ctx.stroke();
 }}
 const _lya=document.getElementById('ly_a');
 if(_lya&&_lya.checked&&DATA.coap){   // co-appearance layer: shared broadcasts; violet = cross-party pairing, teal = same-party
   for(const [a,b,n,bip] of DATA.coap){const A=toScreen(LX[a],LY[a]),B=toScreen(LX[b],LY[b]);
     ctx.beginPath();ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);
     ctx.lineWidth=bip?1.4:0.9;ctx.globalAlpha=Math.min(0.8,0.22+n*0.1);
     ctx.strokeStyle=bip?'#a78bfa':'#2dd4bf';ctx.stroke();}
   ctx.globalAlpha=1;}
 const _lyr=document.getElementById('ly_r');
 if(_lyr&&_lyr.checked&&DATA.refs){   // signed reference layer: green=net positive, red=net negative, alpha by evidence count
   for(const [a,b,net,n] of DATA.refs){const A=toScreen(LX[a],LY[a]),B=toScreen(LX[b],LY[b]);
     ctx.beginPath();ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);
     ctx.lineWidth=net<0?1.3:0.9;ctx.globalAlpha=Math.min(0.75,0.18+n*0.09);
      ctx.strokeStyle=net>0.15?'#34d399':(net<-0.4?'#ef4444':'#8b949e');ctx.stroke();}   /* red gated at -0.4: gold check 2026-07-04 measured negative-class precision 0.68, all errors over-signed neutrals (0 inversions in 50) */
   ctx.globalAlpha=1;}
 if(CASC){   // cascade spread structure: light up the ties that carry the cascade, tinted by the weaker endpoint's join-likelihood
   ctx.lineWidth=1.2;for(let k=0;k<EDGES.length;k++){const [a,b]=EDGES[k];const pm=Math.min(cascP[a],cascP[b]);if(pm<0.08)continue;
     const A=toScreen(LX[a],LY[a]),B=toScreen(LX[b],LY[b]);ctx.beginPath();ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);
     ctx.globalAlpha=Math.min(0.7,0.14+pm*0.7);ctx.strokeStyle=cascCol(pm);ctx.stroke();}
   ctx.globalAlpha=1;}
 else if(focus>=0&&!fset&&PE[focus]){   // single-member spotlight: draw its influence ties so its reach is legible over the dimmed field
   ctx.lineWidth=1.1;for(const v in PE[focus]){const p=PE[focus][v];const A=toScreen(LX[focus],LY[focus]),B=toScreen(LX[+v],LY[+v]);
     ctx.beginPath();ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);ctx.globalAlpha=Math.min(0.9,0.22+p*1.5);ctx.strokeStyle='#58a6ff';ctx.stroke();}
   ctx.globalAlpha=1;}
 const order=[...Array(N).keys()].sort((p,q)=>allocOf(p)-allocOf(q));
 for(const i of order){const [sx,sy]=toScreen(LX[i],LY[i]);const r=radOf(i)*Math.max(1,cam.s*1);
   if(tierHidden(i)){ctx.beginPath();ctx.arc(sx,sy,r,0,7);ctx.fillStyle='#433317';ctx.globalAlpha=0.12;ctx.fill();ctx.globalAlpha=1;continue;}   // tier filter: non-tier members = faint backdrop
   const dim=dimming&&!inFoc(i);                                // fade everyone not in the focused member / route
   let fill,al;
   if(CASC){fill=cascCol(cascP[i]);al=1;}
   else if(RC&&dim){if(route.fliproute&&routeCasc[i]<=0.001){fill='#564019';al=0.16;}else{fill=cascCol(routeCasc[i]);al=0.6;}}   // tint by activation likelihood (cascade) or leverage-on-target (flip); zero-leverage members stay grey
   else if(dim){fill='#564019';al=0.16;}
   else{fill=colOf(i);al=(colorBy==='issue'&&(HONORS?honorRow(i).evidence_status==='no_recorded_action':DATA.stance[i][issue]==='-'))?0.45:(colorBy==='elec'&&DATA.play[i]==='n')?0.3:(colorBy==='antag'&&(!DATA.antag||DATA.antag[i]==null))?0.3:1;if(r>4.5)al*=0.78;}
   ctx.beginPath();ctx.arc(sx,sy,r,0,7);ctx.fillStyle=fill;
   ctx.globalAlpha=al;ctx.fill();ctx.globalAlpha=1;
   if(!CASC&&!dim&&allocOf(i)>0){ctx.lineWidth=1.1;ctx.strokeStyle='#fff';ctx.stroke();}
   if(CASC&&cascSeeds&&cascSeeds.has(i)){ctx.lineWidth=2.4;ctx.strokeStyle='#fff';ctx.beginPath();ctx.arc(sx,sy,r+3,0,7);ctx.stroke();}   // seed = the source co-sponsor
   if(!dim&&!RT&&(i===hi||i===sel)){ctx.lineWidth=2;ctx.strokeStyle='#EECD5C';ctx.beginPath();ctx.arc(sx,sy,r+3,0,7);ctx.stroke();}
 }
 if(focus>=0&&!fset){   // redraw the focused member ON TOP so it stands out above the dimmed field: glow + ring + name
   const [sx,sy]=toScreen(LX[focus],LY[focus]),r=radOf(focus)*Math.max(1,cam.s);
   /* Selection uses an outline, without a decorative halo. */
   ctx.beginPath();ctx.arc(sx,sy,r,0,7);ctx.fillStyle=colOf(focus);ctx.fill();
   ctx.lineWidth=2.5;ctx.strokeStyle='#EECD5C';ctx.beginPath();ctx.arc(sx,sy,r+3,0,7);ctx.stroke();
    ctx.fillStyle='#F3F3E6';ctx.font='700 11px system-ui,Segoe UI,sans-serif';ctx.textAlign='center';ctx.textBaseline='bottom';
   ctx.fillText(DATA.name[focus].split(' ').slice(-1)[0],sx,sy-r-6);}
 if(flipHi&&sel<0){ctx.lineWidth=1.6;ctx.strokeStyle='#a78bfa';for(const i of flipHi){const [sx,sy]=toScreen(LX[i],LY[i]);const r=radOf(i)*Math.max(1,cam.s);ctx.beginPath();ctx.arc(sx,sy,r+3,0,7);ctx.stroke();}}
 if(RT)drawRoute(RT);   // animated Google-Maps-style path: draws itself in, glows, lights up members, labels start/target
 if(dropped.size){ctx.strokeStyle='#ef4444';ctx.lineWidth=2;for(const i of dropped){const [sx,sy]=toScreen(LX[i],LY[i]);ctx.beginPath();ctx.moveTo(sx-5,sy-5);ctx.lineTo(sx+5,sy+5);ctx.moveTo(sx+5,sy-5);ctx.lineTo(sx-5,sy+5);ctx.stroke();}}
 if(layout==='committee'&&DATA.comms){ctx.save();ctx.textAlign='center';ctx.textBaseline='middle';ctx.font='700 13px system-ui,Segoe UI,sans-serif';
   for(const c of DATA.comms){const [lx,ly]=toScreen(c[1],c[2]);ctx.lineWidth=3.5;ctx.strokeStyle='rgba(29,25,18,0.85)';ctx.fillStyle='rgba(255,255,255,0.96)';
     ctx.strokeText(c[0],lx,ly);ctx.fillText(c[0],lx,ly);}ctx.restore();}
}
// interaction
cv.addEventListener('mousemove',e=>{
 const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;
 if(drag){cam.tx+=mx-drag.x;cam.ty+=my-drag.y;drag.x=mx;drag.y=my;panMoved=true;draw();return;}
 let best=-1,bd=99;for(let i=0;i<N;i++){if(tierHidden(i))continue;const [sx,sy]=toScreen(LX[i],LY[i]);const d=Math.hypot(sx-mx,sy-my);if(d<Math.max(6,radOf(i)+3)&&d<bd){bd=d;best=i;}}
 hi=best;const tip=document.getElementById('tip');
 if(best>=0){const i=best,a=allocOf(i);const pl=PLAYLBL[DATA.play[i]];
  tip.style.display='block';tip.style.left=Math.min(e.clientX+14,innerWidth-260)+'px';tip.style.top=(e.clientY+14)+'px';
  const pr=prime(i,issue);   // explicit flip pressure on hover (or activation-likelihood while cascading / diagramming)
  const flipLine=(cascadeOn&&cascP)?`<br>Joins cascade: <b style=color:${cascCol(cascP[i])}>${Math.round(cascP[i]*100)}%</b>`
    :(route&&route.fliproute&&routeCasc)?(routeCasc[i]>0.001?`<br>Leverage to flip: <b style=color:${cascCol(routeCasc[i])}>${Math.round(routeCasc[i]*100)}%</b>`:'')
    :(route&&routeCasc)?`<br>Activates: <b style=color:${cascCol(routeCasc[i])}>${Math.round(routeCasc[i]*100)}%</b>`
    :pr?(pr.money===null?`<br>Flip: <b style=color:#f59e0b>beyond reach</b> (${flipPct(pr.pressure,pr.theta)}% of the way)`
        :`<br>Flip pressure: <b>${flipPct(pr.pressure,pr.theta)}%</b> of the way · <b style=color:#fbbf24>${Math.max(0,100-flipPct(pr.pressure,pr.theta))}%</b> to go`):'';
  const curveNote=HONORS?`<br>${mesc(honorRankText(i))}`:CURVED_ISSUES.has(issue)?' <span style=color:#EECD5C>(sign-preserving curved label)</span>':'';
  // sp[0] is DESCRIPTIVE clip count and sp[8] is VERIFIED-VOICEPRINT minutes; they are two
  // different measurements, so they are reported as separate clauses and never summed.
  // "audio coverage limited" is now reserved for a member with neither layer.
  const spAudio=s=>{const parts=[];
    if(s[0]>0)parts.push(`${s[0].toLocaleString()} clips · ${s[1].toLocaleString()} min · ${s[2]} settings`);
    if(s[8]!=null)parts.push(`${Number(s[8]).toLocaleString()} min verified voiceprint (standardized, not clip-based)`);
    return parts.length?parts.join(' · '):'audio coverage limited';};
  const sp=DATA.speech&&DATA.speech[i],speechLine=sp?`<br>Observed speech: <b>${sp[5]}</b> · ${sp[7].toLocaleString()} attributed public words · ${spAudio(sp)}${sp[0]>0?` · ${String(sp[6]).replaceAll('_',' ')}`:''} <span style=color:#8b949e>(descriptive only)</span>`:'';
  tip.innerHTML=`<b>${DATA.name[i]}</b> (${DATA.party[i]}) · ${HONORS?'reporting ':''}rank ${DATA.rank[i]} · <b>${DATA.bp[i]}%</b> bipartisan<br>On <b>${DATA.issues[issue]}</b>: <b style=color:${publicColor(i)}>${mesc(publicLabel(i))}</b>${curveNote}${flipLine}<br>Reach on this issue: <b style=color:${centralCol(CENTPCT[i])}>top ${Math.max(1,100-Math.round(CENTPCT[i]*100))}% of Congress</b><br>${HONORS?`Overall priority: ${mesc(priorityLabel(i))} · ${mesc(priorityRankText(i))}<br>Stance: ${mesc(publicLabel(i,DATA.issues.length-1)+mixedRecordText(i))}<br><span style=color:#A49D8A>Standing names are not persuasion probabilities.</span>`:'Overall: '+mesc(publicLabel(i,DATA.issues.length-1))} · electoral: <b style=color:${ELCOL[DATA.play[i]]}>${pl}</b>${speechLine}<br>${mode==='coal'?'Coalition seed':'Persuasion target'}: <b>${a?(mode==='coal'?'enters @ $'+kfmt(a)+'k':'$'+kfmt(a)+'k'):'—'}</b>`;}
 else tip.style.display='none';
 draw();
});
cv.addEventListener('mouseleave',()=>{hi=-1;document.getElementById('tip').style.display='none';draw();});
cv.addEventListener('mousedown',e=>{const r=cv.getBoundingClientRect();drag={x:e.clientX-r.left,y:e.clientY-r.top};panMoved=false;cv.style.cursor='grabbing';});
cv.addEventListener('click',()=>{if(!panMoved){if(hi>=0)selectMember(hi);else backToTargets();}});
addEventListener('mouseup',()=>{drag=null;cv.style.cursor='grab';});
cv.addEventListener('wheel',e=>{e.preventDefault();const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;const f=e.deltaY<0?1.12:0.893;const nx=mx-(mx-cam.tx)*f,ny=my-(my-cam.ty)*f;cam.s*=f;cam.tx=nx;cam.ty=ny;draw();},{passive:false});
// --- touch (mobile): 1-finger pan + tap-to-select, 2-finger pinch-zoom ---
let tdrag=null,pinch=null;
cv.addEventListener('touchstart',e=>{const r=cv.getBoundingClientRect();
 if(e.touches.length===1){const t=e.touches[0];tdrag={x:t.clientX-r.left,y:t.clientY-r.top};panMoved=false;pinch=null;}
 else if(e.touches.length>=2){const a=e.touches[0],b=e.touches[1];pinch={d:Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY),cx:(a.clientX+b.clientX)/2-r.left,cy:(a.clientY+b.clientY)/2-r.top};tdrag=null;}},{passive:true});
cv.addEventListener('touchmove',e=>{e.preventDefault();const r=cv.getBoundingClientRect();
 if(pinch&&e.touches.length>=2){const a=e.touches[0],b=e.touches[1],nd=Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY),cx=(a.clientX+b.clientX)/2-r.left,cy=(a.clientY+b.clientY)/2-r.top,f=nd/(pinch.d||nd);
  cam.tx+=cx-pinch.cx;cam.ty+=cy-pinch.cy;cam.tx=cx-(cx-cam.tx)*f;cam.ty=cy-(cy-cam.ty)*f;cam.s*=f;pinch={d:nd,cx:cx,cy:cy};panMoved=true;draw();}
 else if(tdrag&&e.touches.length===1){const t=e.touches[0],mx=t.clientX-r.left,my=t.clientY-r.top;cam.tx+=mx-tdrag.x;cam.ty+=my-tdrag.y;tdrag.x=mx;tdrag.y=my;panMoved=true;draw();}},{passive:false});
cv.addEventListener('touchend',e=>{if(e.touches.length===0){if(tdrag&&!panMoved){const mx=tdrag.x,my=tdrag.y;let best=-1,bd=99;for(let i=0;i<N;i++){if(tierHidden(i))continue;const [sx,sy]=toScreen(LX[i],LY[i]);const d=Math.hypot(sx-mx,sy-my);if(d<Math.max(12,radOf(i)+8)&&d<bd){bd=d;best=i;}}if(best>=0){selectMember(best);if(window.innerWidth<=640){const rp=document.getElementById('right');if(rp)setTimeout(function(){rp.scrollIntoView({behavior:'smooth',block:'start'});},70);}}else backToTargets();}tdrag=null;pinch=null;}},{passive:true});
function flyTo(i){cam.s=2.4;cam.tx=W/2-LX[i]*cam.s;cam.ty=H/2-LY[i]*cam.s;draw();}
// ============ LIVE SCENARIO/PATHFINDER ENGINE (faithful port of flip_model + competitive cascade + lt_priming) ============
const EG=DATA.engine,K=EG.const,BPF=EG.bpf,PTY=DATA.party;
// COMPET_MULT (Paul 2026-10-05): EG.cmult = per-member competitiveness multiplier c, scaling EFFECTIVE dollars on BOTH money legs (absent in dial-OFF / old bundles -> neutral 1). Read as EG.cmult inline (no global) so every sliced copy of these functions stays self-contained.
const PM=EG.pmult||null,EMU=EG.emult||null,DM=EG.dmult||null;   // receptivity psych parity (2026-07-09): pressure/edge/delivery multipliers mirrored from the Python engine (old bundles -> neutral 1.0)
let ADJ=Array.from({length:N},()=>({}));for(const [u,v,w] of EG.adj){ADJ[u][v]=w;ADJ[v][u]=w;}
// HOLISTIC NETWORK TOGGLE (Paul-ruled 2026-08-18). The holistic adjacency travels in the same
// bundle and the kernel is rebuilt in place when the user switches. Absent from CU-only bundles,
// in which case the control never appears and nothing below this line runs.
let NETCFG='cu';
let EDGES=DATA.edges,ELAYS=DATA.elay;
const HOLMETA=DATA.holistic||null;
// ---- BASE LAYOUT PER NETWORK CONFIG ---------------------------------------------------
// Each configuration ships its own node layout, emitted in ONE coordinate frame (the producer
// Procrustes-aligns each onto the CU layout), so switching configurations moves a member only
// as far as its tie structure actually moved it. Absent keys fall back to the CU layout, so an
// older or CU-only bundle simply never moves.
// ---- NETWORK REGISTRY ------------------------------------------------------------------
// Five configurations now (Paul promoted bank_reg then crypto on 2026-08-18). The per-config
// ternary chains this replaces were already four deep at three configs; at five they are how
// a config silently ends up reading another one's data. `sfx` is the bundle key suffix, `meta`
// the bundle key holding that config's honesty block.
// EVERY JS token now equals its NET_CONFIG value. The `norm` alias was retired 2026-08-19: it
// was kept on the belief that renaming would break path4_parity.py, but that script greps three
// KERNEL FORMULA lines and never the config tokens, so the blocker did not exist. With five
// configs an alias that differs from the env value is exactly how a call site reads the wrong
// graph, so the bundle keys were renamed to match in the same change.
const NETS={cu:{sfx:null,meta:null},
            holistic:{sfx:'holistic',meta:'holistic'},
            holistic_norm:{sfx:'holistic_norm',meta:'holistic_norm'},
            bank_reg:{sfx:'bank_reg',meta:'bank_reg'},
            crypto:{sfx:'crypto',meta:'crypto'}};
function netSfx(cfg){return (NETS[cfg]||NETS.cu).sfx;}
function netAdj(cfg){const s=netSfx(cfg);return s?(EG['adj_'+s]||null):EG.adj;}
function netEdges(cfg){const s=netSfx(cfg);return (s&&DATA['edges_'+s])?DATA['edges_'+s]:DATA.edges;}
function netElay(cfg){const s=netSfx(cfg);return (s&&DATA['elay_'+s])?DATA['elay_'+s]:DATA.elay;}
function netMeta(cfg){const m=(NETS[cfg]||NETS.cu).meta;return m?(DATA[m]||null):null;}
const BASE_XY={};
for(const k in NETS){const s=NETS[k].sfx;
  BASE_XY[k]=s?[DATA['x_'+s]||DATA.x, DATA['y_'+s]||DATA.y]:[DATA.x,DATA.y];}
function baseX(){return (BASE_XY[NETCFG]||BASE_XY.cu)[0];}
function baseY(){return (BASE_XY[NETCFG]||BASE_XY.cu)[1];}
function cpf(u,v){return (PTY[u]!==PTY[v]&&'DR'.indexOf(PTY[u])>=0&&'DR'.indexOf(PTY[v])>=0)?(K.DCROSS+(1-K.DCROSS)*Math.sqrt(BPF[u]*BPF[v])):1;}   // CALIBRATED: cross-party DIFFUSION damped 0.4-1.0x (homophily: Harmon; Andris; cross<within)
function align(u,v){const a=PTY[u],b=PTY[v],ra=a==='D'||a==='R',rb=b==='D'||b==='R';if(a===b&&ra)return K.SRCPRO;if(a!==b&&ra&&rb)return K.SRCANTI;return 1;}   // QME 2026-10-01: explicit D/R checks (indexOf('') is 0, so '' or a bare-substring token used to count as a party). SOURCE-ALIGNMENT for persuasion: co-partisan x1.7 / out x0.5 (Furnas/Bakker)
let PEBASE=Array.from({length:N},()=>({}));function buildPEBASE(){PEBASE=Array.from({length:N},()=>({}));for(let u=0;u<N;u++)for(const v in ADJ[u]){const wc=ADJ[u][v]*cpf(u,+v)*(EMU?EMU[+v]:1);PEBASE[u][v]=wc/(wc+6);}}buildPEBASE();   // = moneyball_core.build_P before the interactive-only issue/chair layer
let PE=PEBASE.map(row=>({...row}));
const IP=DATA.influence_policy||{domains:[],roles:[],role_priors:{'-':{source_shock:1,target_resistance:1}}},ROLEPRIORS=IP.role_priors||{'-':{source_shock:1,target_resistance:1}};
const CPOL=IP.centrality||{method:'weighted Katz-Bonacich outbound reach',attenuation_fraction:.82,iterations:80,rank_method:'tie-aware midrank percentile'};
function domainPolicy(){return (IP.domains||[])[issue]||{edge_scale:1,ic_edge_scale:1,lt_threshold_scale:1};}
function roleCode(u){const row=(IP.roles||[])[u]||'';return row[issue]||'-';}
function rolePrior(u){return ROLEPRIORS[roleCode(u)]||ROLEPRIORS['-']||{source_shock:1,target_resistance:1};}
function configureIssueNetwork(){
  const d=domainPolicy();PE=Array.from({length:N},()=>({}));
  for(let u=0;u<N;u++)for(const sv in PEBASE[u]){const v=+sv,p=Math.max(.000001,Math.min(.999999,PEBASE[u][sv])),odds=p/(1-p),source=rolePrior(u),target=rolePrior(v);
    const factor=(d.edge_scale||1)*(d.ic_edge_scale||1)*(source.source_shock||1)/(target.target_resistance||1);
    PE[u][v]=Math.max(0,Math.min(.995,(odds*factor)/(1+odds*factor)));}
  DEG=PE.map(o=>Object.keys(o).length);meanDeg=DEG.reduce((a,b)=>a+b,0)/N;PR=pageRankScores();PRMAX=Math.max(...PR)||1;
  CENT=issueKatzScores();CENTPCT=midrankPercentiles(CENT);
}
const SCN={C:'Champion',F:'Defender',A:'Ally',H:'Highly Persuadable',P:'Persuadable',G:'Lean Persuadable',T:'True Neutral',L:'Lean Skeptic',N:'Neutral',S:'Skeptic',K:'Highly Skeptical',O:'Opponent','-':'—'};
const CAMP=t=>(t==='C'||t==='F'||t==='A'?'pro':t==='O'||t==='S'||t==='K'?'anti':'neu');   // QME 2026-10-01: exact one-letter tiers; 'CFA'.indexOf('') is 0, so '' / undefined used to read as pro
const PERS=EG.pers,THETA=EG.theta;
function baseTiers(i){const t=new Array(N);for(let u=0;u<N;u++)t[u]=DATA.stance[u][i];return t;}
function flipModel(i,tiers,cu,ind){
  const nA=new Float64Array(N),nB=new Float64Array(N);
  for(let u=0;u<N;u++){let a=0,b=0;const nb=ADJ[u];for(const v in nb){const c=CAMP(tiers[v]);if(c==='neu')continue;const wc=nb[v]*align(u,+v);if(c==='pro')a+=wc;else b+=wc;}nA[u]=a;nB[u]=b;}   // persuasion proximity uses SOURCE-ALIGNMENT
  const mA=Math.max(...nA)||1,mB=Math.max(...nB)||1;   // 2026-10-01 (Paul: "Match backend"): the REAL peer maximum, 1 only when it is 0 (an empty camp) = flip_model.topic_setup `max(nA.values()) or 1` and the desk's desk_roi; it used to floor each maximum at 1
  const eng=EG.eng[i],priority=(EG.priority&&EG.priority[i])||EG.tierp;let total=0;const pf=new Float64Array(N),tg=new Float64Array(N);
  for(let u=0;u<N;u++){const pm=PM?PM[u]:1,cm=EG.cmult?EG.cmult[u]:1;const a=K.WM*pm*(cm*cu[u]+1)/((cm*cu[u]+1)+K.HALFF*EG.rcp[u])+K.WP*nA[u]/mA;const b=K.WM*pm*(cm*ind[u]+1)/((cm*ind[u]+1)+K.HALFF*EG.rcp[u])+K.WP*nB[u]/mB;const p=(a+b)>0?a/(a+b):0;pf[u]=p;tg[u]=Math.max(0,eng[u]+K.BASE*priority[u])*(PERS[SCN[tiers[u]]]||0)*(1+0.3*BPF[u])*(DM?DM[u]:1);total+=tg[u]*p;}   // Direct issue engagement + tier/LOO-orientation priority; floor only after combination, then pressure/delivery parity.
  return {pf,tg,nA,nB,mA,mB,total};
}
function pflipOf(u,nAu,nBu,mA,mB,cu,ind){const pm=PM?PM[u]:1,cm=EG.cmult?EG.cmult[u]:1;const a=K.WM*pm*(cm*cu+1)/((cm*cu+1)+K.HALFF*EG.rcp[u])+K.WP*nAu/mA;const b=K.WM*pm*(cm*ind+1)/((cm*ind+1)+K.HALFF*EG.rcp[u])+K.WP*nBu/mB;return (a+b)>0?a/(a+b):0;}
function shiftLean(u,dollars){const r=(EG.cmult?EG.cmult[u]:1)*dollars/EG.rcp[u];return K.MAXSHIFT*(r/(r+K.HALF))*(PM?PM[u]:1);}   // x pressure_mult (psych parity 2026-07-09 = money_response.shift_lean)
// QME 2026-10-01 (mirrors lt_priming.py LT_MONEY_PARITY): prime()'s money term = money_response.shift_lean/MAXSHIFT = pm*r/(r+HALF), so the inverse divides the unit target by pressure_mult and uses the HALF dial (K.LTHALF; 0.10 when parity is off). K.LTPAR===0 -> old (pm 1, K.HALF). Old bundles lack K.LTPAR/LTHALF -> parity on.
function ltPM(u){return K.LTPAR===0?1:Math.max(PM?PM[u]:1,1e-9);}
function dollarsForUnits(u,units){units=Math.min(units/ltPM(u),0.98);const h=(K.LTHALF!=null&&K.LTPAR!==0)?K.LTHALF:(K.LTPAR===0?0.10:K.HALF);return h*EG.rcp[u]*units/(1-units)/(EG.cmult?EG.cmult[u]:1);}
// stance-integrity gates (2026-07-04, mirrors fec/engine/lt_priming.py; 'Lean Skeptic' carved out of every one):
// overall Skeptic/Highly-Skeptical/Opponent can be neither target nor recruit; recruit pricing scales by
// persuadability (True-Neutral-anchored, LS pinned flat); theta grows with declared-Opponent-cell breadth.
const BLOCKOV={S:1,K:1,O:1};   // DATA.gen overall codes blocked ('L' Lean Skeptic deliberately allowed)
const nOpp=Array.from({length:N},(_,u)=>{let k=0;const s=DATA.stance[u];for(let j=0;j<s.length-1;j++)if(s[j]==='O')k++;return k;});   // 18 real issues; last char = overall layer
function rcost(u,i){const t=SCN[DATA.stance[u][i]];return t==='Lean Skeptic'?K.CEDGE:K.CEDGE*0.5/Math.max(0.05,PERS[t]||PERS['—']);}
function prime(v,i){   // lt_priming: cheapest pressure+money to flip member v on issue i
  const code=DATA.stance[v][i],tier=SCN[code];
  if(!(tier in THETA)||CAMP(code)==='pro')return null;
  if(tier==='Opponent'&&!K.LTOPP)return null;   // QME 2026-10-01 = lt_priming.py: committed Opponent cells are never targets (K.LTOPP=1 <- LT_OPP_TARGET=1 restores)
  if(BLOCKOV[DATA.gen[v]])return null;   // one mild topic cell can't make a system-level Skeptic a target
  const _dp=domainPolicy(),_rp=rolePrior(v),theta=(THETA[tier]+Math.max(0,-(EG.nlraw[v])/120)+0.25*nOpp[v])*(_dp.lt_threshold_scale||1)*(_rp.target_resistance||1);   // domain sensitivity + jurisdiction-chair stubbornness raise the LT flip line
  // Bakker cross-cue signing (mirrors fec/engine/lt_priming.py xf(), default-on): cross-party pro-cues pass
  // through sqrt(bpf_u*bpf_v) — non-bridge out-party cues ~0, bridge pairs keep full influence.
  let pressure=0;const cand=[];const pv=PTY[v];
  for(const u in PE[v]){const uu=+u;const pe=(PE[uu][v]||0)*((PTY[uu]===pv)?1:Math.sqrt((BPF[uu]||0)*(BPF[v]||0)));const c=CAMP(DATA.stance[uu][i]);if(c==='pro')pressure+=pe;else if(c!=='anti'&&!BLOCKOV[DATA.gen[uu]]&&pe>1e-6)cand.push([uu,pe]);}   // u's pull ON target v = PE[u][v] (receiver v's emult + target_resistance), mirrors lt_priming.py; index was reversed until 2026-09-30
  const gap=theta-pressure;
  if(gap<=1e-6)return {tier,theta,pressure,gap,money:0,recruits:[]};
  const mcap=0.97*ltPM(v),cap=Math.min(mcap,gap),avg=dollarsForUnits(v,cap)/cap;
  // 2026-08-03 mirror of fec/engine/lt_priming.py: (1) the Math.max(0.1,add) divide-by-zero guard is gone —
  // the cand push already filters pe>1e-6, and below add=0.1 the guard stopped cancelling and became a
  // price; (2) the rate is rcost/add ($ per influence-unit), NOT rcost/add**2 — avg is $/unit, so the
  // old form compared $/unit**2 against $/unit and rejected recruits that are cheaper than money.
  cand.sort((a,b)=>rcost(a[0],i)/a[1]-rcost(b[0],i)/b[1]);
  let got=0;const recruits=[];for(const [u,add] of cand){if(got>=gap)break;if(rcost(u,i)/add<avg){got+=add;recruits.push(u);}}
  const resid=Math.max(0,gap-got);if(resid>mcap)return {tier,theta,pressure,gap,money:null,recruits};
  return {tier,theta,pressure,gap,money:resid>1e-6?Math.round(dollarsForUnits(v,resid)):0,recruits};
}
function cheapestFlips(i,n){   // batch: cheapest members to tip into co-sponsoring on issue i (lt_priming, sorted by $)
  const out=[];for(let v=0;v<N;v++){const r=prime(v,i);if(r&&r.money!==null&&(r.money>0||r.recruits.length>0))out.push([v,r.money||0,r.recruits.length]);}
  out.sort((a,b)=>a[1]-b[1]);return out.slice(0,n);
}
function sm(n){var lo=n>>>0,hi=(n/4294967296)>>>0,h=Math.imul(lo^hi,2654435761)>>>0;h=Math.imul(h^(h>>>15),2246822519)>>>0;h=Math.imul(h^(h>>>13),3266489917)>>>0;h^=h>>>16;return (h>>>0)/4294967296;}   // deterministic integer hash -> [0,1); plain 32-bit math (keys fit in 2^53)
const NMb=N;
function spread(tiers,ind,tally){   // competitive cascade, edge-keyed CRN; optional `tally` out-param accumulates per-node pro-activation counts (presentation only; return value + math unchanged, mirror holds). SHOCKS (Noble-Turner 2024 going public): the exec cue is
  // PARTY-ASYMMETRIC and ISSUE-DEPENDENT, not a flat dial.  Per-issue regime mixture EG.sal[issue]: P(rally)=p,
  // P(retreat)=q, P(backfire)=b, estimated from the D-R stance gap z.  Co-partisans of the exec always respond MORE
  // (beta_co > beta_out); on polarized issues (q high) an exec SUPPORT cue makes out-party bystanders COUNTER-mobilize
  // (Lee 2009 partisan retreat) and an OPPOSE cue demobilizes them.  Old flat 1.6/0.6 = the special case p=1,q=b=0.
  const sal=(EG.sal&&EG.sal[issue])||{p:.6,q:.3,b:.1};
  const efm=u=>{const co=PTY[u]===(K.EXECP||'R');
    if(execMode==='oppose') return co?1+0.8*(sal.p+sal.q):Math.max(0.2,1+0.8*(sal.p-sal.q-sal.b));
    if(execMode==='support')return co?Math.max(0.2,1-0.7*(sal.p+sal.q)):Math.max(0.2,1-0.7*(sal.p-sal.q-sal.b));
    return 1;};
  const A=[],anti=[];for(let u=0;u<N;u++){if(dropped.has(u))continue;const c=CAMP(tiers[u]);if(c==='pro')A.push(u);else if(c==='anti')anti.push(u);}
  let tot=0;
  for(let it=0;it<K.MC;it++){
    const B=anti.slice(),bk=(700000000+it)*NMb;
    for(let u=0;u<N;u++)if(!dropped.has(u)&&CAMP(tiers[u])==='neu'&&sm(bk+u)<Math.min(0.9,(ind[u]||0)*efm(u)/K.BSCALE))B.push(u);
    const col=new Int8Array(N);for(const dz of dropped)col[dz]=3;for(const s of A)col[s]=1;for(const s of B)col[s]=2;
    let fr=[];for(const s of A)fr.push(s*2);for(const s of B)fr.push(s*2+1);
    const itb=it*NMb*NMb;
    while(fr.length){const nf=[];for(const e of fr){const u=e>>1,c=(e&1)?2:1;const ub=u*NMb;for(const v in PE[u]){if(col[v]===0&&sm(itb+ub+(+v))<PE[u][v]){col[v]=c;nf.push((+v)*2+(c-1));}}}fr=nf;}
    for(let k=0;k<N;k++)if(col[k]===1){tot++;if(tally)tally[k]++;}
  }
  return tot/K.MC;
}
// ---- multi-objective co-sponsor pathfinder (RELINK-style): grow a recruitment TREE from ONE seed that
// maximizes co-sponsor spread (recruitability x onward reach) while steering toward a key persuadable target ----
let DEG=PE.map(o=>Object.keys(o).length);let route=null,dropped=new Set(),execMode='none';
// ---- route presentation: fit the camera to the whole path, then draw it in like a map route (animated) ----
let routeAnim=null,routeRAF=null,routeCasc=null;   // routeCasc[i] = P(member i activates) with the whole recruited route as the cascade seed
function noRoute(){route=null;routeCasc=null;legend();}   // clear an active route diagram + restore the normal legend
function fitCam(nodes){   // camera that frames all route members with padding (so a path to a far target is never off-screen)
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;for(const i of nodes){x0=Math.min(x0,LX[i]);y0=Math.min(y0,LY[i]);x1=Math.max(x1,LX[i]);y1=Math.max(y1,LY[i]);}
  const pad=80,bw=Math.max(60,x1-x0),bh=Math.max(60,y1-y0),s=Math.min((W-2*pad)/bw,(H-2*pad)/bh,5.5),cx=(x0+x1)/2,cy=(y0+y1)/2;
  return {s,tx:W/2-cx*s,ty:H/2-cy*s};
}
function startRoute(){   // frame the whole route immediately (robust — always visible), then animate the line drawing itself in
  if(route&&route.nodes&&route.nodes.length){const f=fitCam(route.nodes);cam.s=f.s;cam.tx=f.tx;cam.ty=f.ty;
    if(!route.fliproute)routeCasc=cascadeProbs(route.unreachable?[route.seed]:route.nodes).p;}   // cascade routes: field = who activates. flip routes carry their own routeCasc (leverage).
  else routeCasc=null;
  routeAnim={t0:performance.now()};legend();
  if(!routeRAF)routeRAF=requestAnimationFrame(routeTick);
  draw();
}
function routeTick(){   // redraw each frame while a route is shown -> the draw-in + the flowing pulse animate (rAF auto-pauses on hidden tabs)
  if(!route){routeRAF=null;return;}
  draw();
  routeRAF=requestAnimationFrame(routeTick);
}
function routeProgress(){if(!routeAnim)return 1;return Math.max(0,Math.min(1,(performance.now()-routeAnim.t0-120)/820));}   // 0..1 line draw-in
function drawRoute(RT){
  const nodes=RT.nodes,P=nodes.map(i=>toScreen(LX[i],LY[i])),prog=routeProgress(),GRN='#34d399';
  ctx.save();ctx.lineCap='round';ctx.lineJoin='round';
  if(RT.unreachable){   // no reliable chain -> dashed red connector between the two endpoints, nothing green
    if(P.length>=2){ctx.setLineDash([7,6]);ctx.strokeStyle='#ef4444';ctx.lineWidth=2;ctx.globalAlpha=0.85;
      ctx.beginPath();ctx.moveTo(P[0][0],P[0][1]);ctx.lineTo(P[P.length-1][0],P[P.length-1][1]);ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;}
  } else if(RT.ordered&&P.length>1){   // ordered chain -> reveal the polyline sequentially (map-style), with a comet head
    const seg=[];let tot=0;for(let k=1;k<P.length;k++){const d=Math.hypot(P[k][0]-P[k-1][0],P[k][1]-P[k-1][1]);seg.push(d);tot+=d;}
    const drawLen=prog*tot;let acc=0,hx=P[0][0],hy=P[0][1];
    ctx.strokeStyle=GRN;ctx.lineWidth=3.2;ctx.shadowColor=GRN;ctx.shadowBlur=13;ctx.beginPath();ctx.moveTo(P[0][0],P[0][1]);
    for(let k=1;k<P.length;k++){const d=seg[k-1];if(acc+d<=drawLen){ctx.lineTo(P[k][0],P[k][1]);acc+=d;hx=P[k][0];hy=P[k][1];}
      else{const f=d>0?(drawLen-acc)/d:0;hx=P[k-1][0]+(P[k][0]-P[k-1][0])*f;hy=P[k-1][1]+(P[k][1]-P[k-1][1])*f;ctx.lineTo(hx,hy);break;}}
    ctx.stroke();
    let cum=0;for(let k=0;k<P.length;k++){if(k)cum+=seg[k-1];if(drawLen>=cum-0.5){ctx.fillStyle=GRN;ctx.beginPath();ctx.arc(P[k][0],P[k][1],Math.max(4,radOf(nodes[k])*Math.max(1,cam.s)*0.5),0,7);ctx.fill();}}
    if(prog<1){ctx.fillStyle='#eafff4';ctx.shadowBlur=18;ctx.beginPath();ctx.arc(hx,hy,5.5,0,7);ctx.fill();}   // comet head while drawing
    else if(tot>0){const ph=(performance.now()/1500)%1;let pl=ph*tot,a2=0,px=P[0][0],py=P[0][1];   // gentle flowing pulse once drawn
      for(let k=1;k<P.length;k++){const d=seg[k-1];if(a2+d>=pl){const f=d>0?(pl-a2)/d:0;px=P[k-1][0]+(P[k][0]-P[k-1][0])*f;py=P[k-1][1]+(P[k][1]-P[k-1][1])*f;break;}a2+=d;}
      ctx.fillStyle='#eafff4';ctx.shadowBlur=16;ctx.beginPath();ctx.arc(px,py,4.5,0,7);ctx.fill();}
    ctx.shadowBlur=0;
  }else{   // tree / diversified set -> glow the edges + light the members (fade in)
    ctx.globalAlpha=prog;ctx.strokeStyle=GRN;ctx.lineWidth=2.6;ctx.shadowColor=GRN;ctx.shadowBlur=11;ctx.beginPath();
    for(const [u,v] of (RT.edges||[])){const A=toScreen(LX[u],LY[u]),B=toScreen(LX[v],LY[v]);ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);}
    ctx.stroke();ctx.shadowBlur=0;ctx.globalAlpha=1;
    for(const i of nodes){const [sx,sy]=toScreen(LX[i],LY[i]);ctx.fillStyle=GRN;ctx.beginPath();ctx.arc(sx,sy,Math.max(4,radOf(i)*Math.max(1,cam.s)*0.5),0,7);ctx.fill();}
  }
  const ringLabel=(idx,col,txt)=>{const [sx,sy]=toScreen(LX[idx],LY[idx]),r=radOf(idx)*Math.max(1,cam.s)+6;
    ctx.strokeStyle=col;ctx.lineWidth=2.6;ctx.shadowColor=col;ctx.shadowBlur=9;ctx.beginPath();ctx.arc(sx,sy,r,0,7);ctx.stroke();ctx.shadowBlur=0;
    ctx.fillStyle=col;ctx.font='700 11px system-ui,Segoe UI,sans-serif';ctx.textAlign='center';ctx.textBaseline='bottom';ctx.fillText(txt,sx,sy-r-3);};
  if(RT.seed!=null)ringLabel(RT.seed,'#22c55e',DATA.name[RT.seed].split(' ').pop()+' · start');
  for(const t of (RT.targets||[]))if(t!==RT.seed)ringLabel(t,'#fbbf24',DATA.name[t].split(' ').pop()+(RT.fliproute?' · flip target':' · target'));
  if(RT.fliproute&&RT.topLever!=null&&RT.topLever>=0&&RT.topLever!==(RT.targets&&RT.targets[0]))ringLabel(RT.topLever,'#34d399','★ '+DATA.name[RT.topLever].split(' ').pop());   // highlight the highest-leverage colleague
  if(RT.coalition){ctx.strokeStyle='#e6edf3';ctx.lineWidth=1.8;for(const i of nodes){const [sx,sy]=toScreen(LX[i],LY[i]);ctx.beginPath();ctx.arc(sx,sy,radOf(i)*Math.max(1,cam.s)+3,0,7);ctx.stroke();}}   // ○ white ring = your coalition members
  ctx.restore();
}
function distToTarget(t){const d=new Float64Array(N);d[t]=1;const done=new Uint8Array(N);while(true){let u=-1,bu=0;for(let k=0;k<N;k++)if(!done[k]&&d[k]>bu){bu=d[k];u=k;}if(u<0)break;done[u]=1;for(const v in PE[u]){const np=d[u]*PE[u][v];if(np>d[+v])d[+v]=np;}}return d;}   // max-product proximity to target
// PROVABLY-OPTIMAL recruitment chain: max-product path = the single MOST-RELIABLE chain (Dijkstra on -log p).
// Reliability = product of per-ask success probs = P(the whole chain holds). This is the "mathematical certainty":
// not a guarantee the bill passes, but the exact, maximal probability of the optimal recruitment route.
// Grounded in Sotoudeh-Porter-Krishnagopal (cosponsorship *influence*, not count, drives passage) + most-reliable-
// path theory (Chen, Shi, Zhang & Lam, Transportmetrica B 2016: maximize the probability the whole chain holds).
function tracePath(sponsor,target,dT){const path=[sponsor],seen=new Set([sponsor]);let cur=sponsor,g=0;while(cur!==target&&g++<N){let nx=-1,bn=-1;for(const v in PE[cur]){const vi=+v;if(seen.has(vi)||dropped.has(vi))continue;const sc=PE[cur][v]*dT[vi];if(sc>bn){bn=sc;nx=vi;}}if(nx<0)break;path.push(nx);seen.add(nx);cur=nx;}return {path,reached:cur===target};}
// FEC-realistic recruitment cost: a PAC -> candidate is capped at ~$5k/election ($10k/cycle); harder asks cost
// MORE relationship/information (Furnas et al.: money is capped, info is the lever) but the DOLLARS never exceed
// the legal ceiling. So difficulty shows up in RELIABILITY, not in a $100k check. cost in [$1k, $10k].
function recruitCost(p){return Math.round(1000+9000*(1-Math.max(0,Math.min(1,p))));}
function pathReliability(path){let p=1;for(let k=1;k<path.length;k++)p*=PE[path[k-1]][path[k]];return p;}   // passive-diffusion product (used only for RANKING paths)
// ACTIVE-recruitment ask success: a deliberate, FUNDED ask that leverages an existing cosponsorship tie lands far
// more often than spontaneous diffusion (cosponsoring is low-risk — Fowler; reciprocity — Bratton-Rouse). ~14x the passive odds.
const ASKM=0.07;
function activeP(p){return p/(p+(1-p)*ASKM);}
function chainWeak(path){let w=1;for(let k=1;k<path.length;k++)w=Math.min(w,activeP(PE[path[k-1]][path[k]]));return w;}   // the bottleneck ask (removed chainConf geomean -- it was the flattering, non-probabilistic number)
function chainHold(path){let p=1;for(let k=1;k<path.length;k++)p*=activeP(PE[path[k-1]][path[k]]);return p;}   // HONEST whole-chain: P(every ask lands first-try, no reroute) -- a conservative floor (rerouting only improves it)
function pathCost(path){let t=0;for(let k=1;k<path.length;k++)t+=recruitCost(PE[path[k-1]][path[k]]);return t;}
function buildPath(sponsor,target){
  const dT=distToTarget(target),{path,reached}=tracePath(sponsor,target,dT),hops=[];let total=0;
  for(let k=1;k<path.length;k++){const p=PE[path[k-1]][path[k]],cost=recruitCost(p);total+=cost;hops.push({cost,p});}
  return {path,hops,total,reached,target,seed:sponsor,rel:pathReliability(path)};
}
// BEST SPONSOR for a target: among members already PRO on the issue (eligible bill introducers), the one whose
// optimal chain to the target is most reliable / cheapest. One Dijkstra (dT), then O(path) per candidate. (Paper 04.)
function bestSponsors(target,n){
  const dT=distToTarget(target),out=[];
  for(let u=0;u<N;u++){if(u===target||'CFA'.indexOf(DATA.stance[u][issue])<0)continue;const {path,reached}=tracePath(u,target,dT);if(reached&&path.length>1)out.push({sponsor:u,ch:chainHold(path),cost:pathCost(path),hops:path.length-1,path});}
  out.sort((a,b)=>b.ch-a.ch||a.cost-b.cost);return out.slice(0,n);   // rank by whole-chain hold (active), then cost
}
// MULTI-TARGET: from one sponsor, the union of optimal chains to several targets (shared recruitment tree).
// Grounded in Aref-Neal optimal signed-coalition partition: targets in one coalition share recruitment substrate.
function multiTarget(sponsor,targets){
  const tree=new Set([sponsor]),edges=[],paths=[];let reached=0,total=0;
  for(const t of targets){const dT=distToTarget(t),{path,reached:rc}=tracePath(sponsor,t,dT);if(rc)reached++;paths.push({t,path,reached:rc,rel:pathReliability(path)});
    for(let k=1;k<path.length;k++){tree.add(path[k]);if(!edges.some(e=>e[0]===path[k-1]&&e[1]===path[k])){edges.push([path[k-1],path[k]]);total+=recruitCost(PE[path[k-1]][path[k]]);}}}
  return {tree,edges,paths,reached,n:targets.length,total};
}
// GCOMB-grounded BUDGET-CONSTRAINED recruitment (Manchanda et al., NeurIPS'20): cost-bounded max-coverage greedy.
// Marginal gain counts only NEWLY-covered members (GCOMB locality loc(v,S)=|N(v)\∪N(u)|) so co-located recruits
// aren't redundant; candidates pruned to non-trivial degree (GCOMB pruning). Monotone-submodular coverage -> the
// classic greedy keeps the (1-1/e)≈0.63 guarantee (GCOMB matches it: 0.998 vs greedy 0.99 vs optimal 1.0), no ML
// needed at our scale. cov[v] = prob v is covered = 1-Π(1-p) over selected recruiters; reach = Σ cov.
let meanDeg=DEG.reduce((a,b)=>a+b,0)/N;
function budgetRecruit(sponsor,nSeeds,target,opts={}){
  const dT=(target!=null&&target>=0)?distToTarget(target):null,cov=new Float64Array(N),sel=[sponsor],inSel=new Uint8Array(N);
  const direct=opts.directOnly?new Set(Object.keys(PE[sponsor]).map(Number)):null;
  inSel[sponsor]=1;cov[sponsor]=1;for(const v in PE[sponsor])cov[+v]=1-(1-cov[+v])*(1-PE[sponsor][v]);
  while(sel.length<nSeeds){
    let best=-1,bg=1e-9;
    for(let c=0;c<N;c++){if(inSel[c]||dropped.has(c)||DEG[c]<Math.max(1,0.1*meanDeg))continue;
      if(direct&&!direct.has(c))continue;
      if(opts.stanceSafe&&(CAMP(DATA.stance[c][issue])==='anti'||BLOCKOV[DATA.gen[c]]))continue;
      let g=1-cov[c];for(const v in PE[c])g+=PE[c][v]*(1-cov[+v]);if(dT)g*=(1+2*dT[c]);
      if(g>bg){bg=g;best=c;}}
    if(best<0)break;sel.push(best);inSel[best]=1;cov[best]=1;for(const v in PE[best])cov[+v]=1-(1-cov[+v])*(1-PE[best][v]);
  }
  let reach=0;for(let u=0;u<N;u++)reach+=cov[u];return {sel,reach};
}
// Weighted PageRank is a cheap global-structure prior for the live dashboard. The 2026 Scientific Reports
// benchmark found PageRank competitive with much costlier IM methods in time-critical settings. It does not
// replace the issue/stance/reliability terms below; it breaks local-degree tunnel vision when two edges have
// similar marginal reach.
function pageRankScores(iter=18,damp=0.85){
  let pr=new Float64Array(N);pr.fill(1/N);
  for(let it=0;it<iter;it++){
    const nx=new Float64Array(N);nx.fill((1-damp)/N);let sink=0;
    for(let u=0;u<N;u++){let z=0;for(const v in PE[u])z+=PE[u][v];
      if(z<=1e-12){sink+=pr[u];continue;}
      for(const v in PE[u])nx[+v]+=damp*pr[u]*PE[u][v]/z;}
    const share=damp*sink/N;for(let v=0;v<N;v++)nx[v]+=share;pr=nx;
  }
  return pr;
}
let PR=pageRankScores(),PRMAX=Math.max(...PR)||1;
// Area centrality deliberately consumes PE, the same issue/chair-adjusted probability kernel used by
// every path and cascade.  Outbound Katz-Bonacich counts direct and downstream influence walks, while
// the attenuation is re-scaled to the current area's spectral radius so the series remains convergent.
function issueKatzScores(){
  const matvec=x=>{const y=new Float64Array(N);for(let u=0;u<N;u++){let s=0;for(const v in PE[u])s+=PE[u][v]*x[+v];y[u]=s;}return y;};
  let x=new Float64Array(N);x.fill(1);let spectral=0;
  for(let it=0;it<40;it++){const y=matvec(x);let norm=0;for(let i=0;i<N;i++)norm=Math.max(norm,Math.abs(y[i]));if(norm<=1e-12){spectral=0;break;}spectral=norm;for(let i=0;i<N;i++)x[i]=y[i]/norm;}
  const delta=spectral>0?Math.max(.05,Math.min(.95,Number(CPOL.attenuation_fraction)||.82))/spectral:0;
  const out=new Float64Array(N);let term=new Float64Array(N);term.fill(1);
  for(let it=0;it<(Number(CPOL.iterations)||80);it++){const y=matvec(term);let mx=0;for(let i=0;i<N;i++){term[i]=delta*y[i];out[i]+=term[i];mx=Math.max(mx,Math.abs(term[i]));}if(mx<1e-10)break;}
  return out;
}
function midrankPercentiles(values){
  const pct=new Float64Array(N),order=Array.from({length:N},(_,i)=>i).sort((a,b)=>values[a]-values[b]||a-b),den=Math.max(1,N-1);
  for(let lo=0;lo<N;){let hi=lo;while(hi+1<N&&values[order[hi+1]]===values[order[lo]])hi++;const p=((lo+hi)/2)/den;for(let k=lo;k<=hi;k++)pct[order[k]]=p;lo=hi+1;}
  return pct;
}
let CENT=issueKatzScores(),CENTPCT=midrankPercentiles(CENT);
// CLOSED-NETWORK MAX ROUTE (RELINK design): the action is an EDGE, not a target node. Start with the
// selected member, repeatedly activate the highest-value edge on the current recruitment frontier, and let
// newly recruited members expose their own outgoing edges. Each recruiter gets at most `fanout` asks.
// IM-CSN is non-submodular, so this is explicitly a sequential marginal-spread heuristic, not a (1-1/e) claim.
function closedCascadeRoute(sponsor,edgeBudget=6,fanout=2){
  const inTree=new Uint8Array(N),outdeg=new Uint8Array(N),pathProb=new Float64Array(N),cov=new Float64Array(N);
  const nodes=[sponsor],edges=[],steps=[];inTree[sponsor]=1;pathProb[sponsor]=1;cov[sponsor]=1;
  while(edges.length<edgeBudget){
    let best=null,bscore=1e-12;
    for(const u of nodes){if(outdeg[u]>=fanout)continue;
      for(const sv in PE[u]){const v=+sv;if(inTree[v]||dropped.has(v))continue;
        if(CAMP(DATA.stance[v][issue])==='anti'||BLOCKOV[DATA.gen[v]])continue;
        const p=PE[u][v],arrival=pathProb[u]*activeP(p);if(arrival<=1e-9)continue;
        let marginal=1-cov[v];for(const sw in PE[v])marginal+=PE[v][sw]*(1-cov[+sw]);
        const structural=0.75+0.25*PR[v]/PRMAX,score=arrival*marginal*structural;
        if(score>bscore+1e-12||(Math.abs(score-bscore)<=1e-12&&best&&p>best.p)){
          best={u,v,p,arrival,marginal,score};bscore=score;}}
    }
    if(!best)break;
    const {u,v,p,arrival,marginal,score}=best;outdeg[u]++;inTree[v]=1;pathProb[v]=arrival;
    nodes.push(v);edges.push([u,v]);steps.push({u,v,p,arrival,marginal,score});
    cov[v]=1-(1-cov[v])*(1-arrival);
    for(const sw in PE[v]){const w=+sw;cov[w]=1-(1-cov[w])*(1-arrival*PE[v][sw]);}
  }
  let expected=0,hold=1,weak=1;for(const s of steps){expected+=s.arrival;const ap=activeP(s.p);hold*=ap;weak=Math.min(weak,ap);}
  return {nodes,edges,steps,expected,hold,weak,fanout};
}
function cascadeReach(seedArr){   // influence cascade: seed the recruited chain as co-sponsors, spread through the network vs the opposition
  const S=new Set(seedArr),tiers=baseTiers(issue);
  for(let u=0;u<N;u++){if(CAMP(tiers[u])==='anti')continue;tiers[u]=S.has(u)?'C':'N';}
  return spread(tiers,EG.ind);
}
// ---- CASCADE MODE: per-member P(joins the cascade) from a seed set. Same seeding as cascadeReach()
// (all non-opposition reset to Neutral except the seeds), same competitive-cascade CRN, but tallies each
// member's activation frequency over the MC runs -> a probability that drives the red->green node coloring.
function cascadeProbs(seedArr){
  const S=new Set(seedArr),tiers=baseTiers(issue);
  for(let u=0;u<N;u++){if(CAMP(tiers[u])==='anti')continue;tiers[u]=S.has(u)?'C':'N';}
  const tally=new Float64Array(N);spread(tiers,EG.ind,tally);
  const p=new Float64Array(N);for(let k=0;k<N;k++)p[k]=tally[k]/K.MC;
  for(const s of S)p[s]=1;   // seeds are the source -> certain
  return {p,seeds:S};
}
function cascadeRun(){   // seed the cascade from the selected member; recolor the graph + list the likely joiners
  if(sel<0)return;
  route=null;routeCasc=null;   // 'who' sub-mode shows the single-seed field, no route
  const cp=cascadeProbs([sel]);cascP=cp.p;cascSeeds=cp.seeds;cascadeOn=true;
  const bt=baseTiers(issue);let reach=0;const joiners=[];
  for(let i=0;i<N;i++){if(cascSeeds.has(i))continue;reach+=cascP[i];if(cascP[i]>=0.03&&CAMP(bt[i])==='neu')joiners.push([i,cascP[i]]);}
  joiners.sort((a,b)=>b[1]-a[1]);
  const po=document.getElementById('pathout'),last=i=>DATA.name[i].split(' ').slice(-1)[0];
  po.innerHTML=`<div class=mrow><span>Cascade seed</span><b>${DATA.name[sel]}</b></div>`
   +`<div class=mrow><span>Expected to join on</span><b class="delta up">~${reach.toFixed(0)} members</b></div>`
   +`<div class=hint style="margin:6px 0">graph is colored <b style=color:#16a34a>green</b>=likely → <b style=color:#ef4444>red</b>=unlikely to activate · most likely first</div>`
   +`<div style="font-size:11px;color:#D2CDBC;line-height:1.75">`+(joiners.length?joiners.slice(0,14).map(([i,p])=>`<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${cascCol(p)};margin-right:6px;vertical-align:middle"></span><span class=lnk data-i=${i} style="cursor:pointer;text-decoration:underline">${mesc(DATA.name[i])}</span>${mprof(i)} <span style=color:#A49D8A>${Math.round(p*100)}%</span>`).join('<br>'):'<span style=color:#9D9784>no neutral members are meaningfully reachable from this seed.</span>')+(joiners.length>14?`<br><span style=color:#9D9784>+${joiners.length-14} more (see the greener nodes)</span>`:'')+`</div>`;
  po.querySelectorAll('.lnk').forEach(el=>el.onclick=()=>selectMember(+el.dataset.i));
  const cb=document.getElementById('cascbar');cb.style.display='block';
  cb.innerHTML=`⚡ <b>Cascade</b> from <b>${last(sel)}</b> · ~<b>${reach.toFixed(0)}</b> likely to join &nbsp;<span class=lo>unlikely</span><span class=scale></span><span class=hi>likely</span>`;
  legend();draw();
}
// HUB CORE (disclosure only, 2026-09-19): an all-533-seed replay inside the published page found the max
// route is not 533 different plans — French Hill lands in 64-89% of them on every issue, Tom Emmer 72-82%,
// Andy Barr 65-78%, and 72-106 seeds return an IDENTICAL recruit set. The hubs are genuine (the House
// Financial Services core), so the ruling was to DISCLOSE the convergence rather than redesign the route:
// nothing here touches closedCascadeRoute, the route, or any row the panel already reports.
// Shares come from the page's OWN gate + closedCascadeRoute(s,6,2) over every seed, counting only seeds
// that actually yield a route — maxCascadeRoute's own two refusals — so the denominator is exactly
// route_replay.json's "viable" (533 - refused - noEdge) and the percentages equal the audited ones.
// Dropped-out members are not seeded: they are out of the network, so they are not a route either.
// ~1.0 s for ~490 seeds on this machine, far past a click's budget, so the sweep runs in timed slices and
// the line fills in when ready. Cached on (issue, network config, dropouts) — the only three inputs
// closedCascadeRoute reads that can change — and EVERY slice re-checks that key, so a configuration change
// mid-sweep drops the run instead of tallying two configurations into one number; the render checks it too,
// so a cached line can never outlive the configuration it was measured on.
const HUBSLICE=25;   // ms of sweeping per slice — one frame's worth, so the canvas stays interactive
let hubCore=null,hubCoreKey='',hubCoreRun=null;
function hubCoreKeyNow(){return issue+'|'+NETCFG+'|'+[...dropped].sort((a,b)=>a-b).join(',');}
function hubCoreStart(){
  const key=hubCoreKeyNow();
  if(hubCoreKey===key&&(hubCore||hubCoreRun!==null))return;   // already measured, or already sweeping
  if(hubCoreRun!==null)clearTimeout(hubCoreRun);
  hubCoreKey=key;hubCore=null;hubCoreRun=null;
  const seeds=[];for(let s=0;s<N;s++){if(!dropped.has(s)&&CAMP(DATA.stance[s][issue])!=='anti'&&!BLOCKOV[DATA.gen[s]])seeds.push(s);}
  const hits=new Float64Array(N);let at=0,routes=0,ms=0;
  const step=()=>{
    if(hubCoreKeyNow()!==key){hubCoreRun=null;return;}   // issue / network / dropouts moved -> drop the sweep
    const t0=performance.now();
    while(at<seeds.length&&performance.now()-t0<HUBSLICE){
      const nd=closedCascadeRoute(seeds[at++],6,2).nodes;
      if(nd.length<2)continue;   // no reachable edge = no route, so not a route in the denominator either
      routes++;for(let k=1;k<nd.length;k++)hits[nd[k]]++;
    }
    ms+=performance.now()-t0;
    if(at<seeds.length){hubCoreRun=setTimeout(step,0);return;}
    hubCoreRun=null;
    const top=[];for(let i=0;i<N;i++)if(hits[i])top.push([i,hits[i]/routes]);
    top.sort((a,b)=>b[1]-a[1]||a[0]-b[0]);
    hubCore={key,routes,ms,top:top.slice(0,3)};
    hubCoreFill();
  };
  hubCoreRun=setTimeout(step,0);
}
function hubCoreHTML(){
  if(!SHOW_HUB_CORE||!hubCore||hubCore.key!==hubCoreKeyNow()||!hubCore.routes||!hubCore.top.length)return '';
  return `<div class=hint style="margin:6px 0">Hub core on this issue: `
   +hubCore.top.map(([i,sh])=>`<span class=lnk data-i=${i} style="cursor:pointer;text-decoration:underline">${mesc(DATA.name[i].split(' ').slice(-1)[0])}</span>${mprof(i)} <b>${Math.round(sh*100)}%</b>`).join(' · ')
   +` of all ${hubCore.routes} max-cascade routes — most routes converge on these members.</div>`;
}
function hubCoreFill(){const el=document.getElementById('hubcore');if(!el)return;el.innerHTML=hubCoreHTML();el.querySelectorAll('.lnk').forEach(x=>x.onclick=()=>selectMember(+x.dataset.i));}
// MAX-CASCADE ROUTE: an outward, multi-hop closed-network edge-activation tree. This is deliberately
// different from flipRoute(): max route has no target and grows the highest-spread frontier; flip route fixes
// one target and ranks inward leverage on that target.
function maxCascadeRoute(){
  if(sel<0)return;
  cascadeExit();   // leave the single-seed activation map — we're drawing a route now
  const po=document.getElementById('pathout'),last=i=>DATA.name[i].split(' ').slice(-1)[0];
  if(CAMP(DATA.stance[sel][issue])==='anti'||BLOCKOV[DATA.gen[sel]]){
    noRoute();po.innerHTML=`<div class=mrow><span>Max-cascade route from</span><b>${last(sel)}</b></div><div class=hint style="margin:6px 0;color:#f59e0b">Not a viable pro-CU cascade seed on this issue. Pick a neutral/pro member without a system-level skeptic gate.</div>`;draw();return;
  }
  const plan=closedCascadeRoute(sel,6,2),nodes=plan.nodes,recruits=nodes;
  if(!plan.edges.length){
    noRoute();po.innerHTML=`<div class=mrow><span>Max-cascade route from</span><b>${last(sel)}</b></div><div class=hint style="margin:6px 0;color:#f59e0b">No stance-viable recruitment edge is reachable from this member after dropouts and system-level gates.</div>`;draw();return;
  }
  route={edges:plan.edges,nodes,seed:sel,targets:[],ordered:false,maxcasc:true,recruits,cascadeSeeds:nodes};
  startRoute();   // frames the route, animates it in, and computes routeCasc = who activates from this recruited set
  const reachAll=cascadeReach(nodes),reachSeed=cascadeReach([sel]),gain=Math.max(0,reachAll-reachSeed);
  if(SHOW_HUB_CORE)hubCoreStart();   // lazily sweep every viable seed for the hub-core line; cached, sliced, never blocks this click
  po.innerHTML=`<div class=mrow><span>Max-cascade route from</span><b>${last(sel)}</b></div>`
   +`<div class=mrow><span>Activate <b>${plan.edges.length}</b> edges → if route lands</span><b class="delta up">~${reachAll.toFixed(0)} members</b></div>`
   +`<div class=mrow><span>vs. ${last(sel)} alone</span><b>~${reachSeed.toFixed(0)} <span class="delta up">(+${gain.toFixed(0)})</span></b></div>`
   +`<div class=mrow><span>Expected route recruits · all asks hold</span><b>${plan.expected.toFixed(1)} · ${(plan.hold*100).toFixed(0)}%</b></div>`
   +`<div class=hint style="margin:6px 0">grow outward one ask at a time, max ${plan.fanout} asks per recruiter · no fixed flip target</div>`
   +(SHOW_HUB_CORE?`<div id=hubcore>${hubCoreHTML()}</div>`:'')
   +`<div style="font-size:11px;color:#D2CDBC;line-height:1.75">`+plan.steps.map(({u,v,p,arrival},k)=>`<span style=color:#9D9784>${k+1}.</span> <span class=lnk data-i=${u} style="cursor:pointer;text-decoration:underline">${mesc(DATA.name[u])}</span>${mprof(u)} → <span class=lnk data-i=${v} style="cursor:pointer;text-decoration:underline">${mesc(DATA.name[v])}</span>${mprof(v)} <span style=color:#A49D8A>${Math.round(activeP(p)*100)}% ask · ${Math.round(arrival*100)}% route</span>`).join('<br>')+`</div>`;
  po.querySelectorAll('.lnk').forEach(el=>el.onclick=()=>selectMember(+el.dataset.i));
}
// FLIP ROUTE (Persuade): pick a TARGET member; ALWAYS show which colleagues have the most LEVERAGE over them
// (single-hop cross-cue-signed pressure; green=get them, ★=highest), and — when the target is actually flippable —
// prime()'s cheapest recruit plan on top. Works for any member, whether or not there's a concrete flip this cycle.
function flipRoute(){
  if(sel<0)return;
  cascadeExit();
  const target=sel, r=prime(target,issue), po=document.getElementById('pathout'), code=DATA.stance[target][issue];
  // leverage of each recruitable colleague over the target — independent of whether the target itself is flippable
  const pv=PTY[target], lev=new Float64Array(N); let mx=1e-9;
  for(const u in PE[target]){const uu=+u,c=CAMP(DATA.stance[uu][issue]);
    if(c==='anti'||BLOCKOV[DATA.gen[uu]])continue;   // anti / system-skeptic isn't a viable pro lever
    const pe=PE[target][uu]*((PTY[uu]===pv)?1:Math.sqrt((BPF[uu]||0)*(BPF[target]||0)));
    if(pe>lev[uu])lev[uu]=pe;}
  let topU=-1;for(let u=0;u<N;u++)if(lev[u]>mx){mx=lev[u];topU=u;}   // ★ highest-leverage colleague
  for(let u=0;u<N;u++)lev[u]=mx>1e-9?lev[u]/mx:0;
  const ranked=[];for(let u=0;u<N;u++)if(lev[u]>0.001)ranked.push([u,lev[u]]);ranked.sort((a,b)=>b[1]-a[1]);
  const recruits=(r&&r.recruits)?r.recruits.slice():[], recSet=new Set(recruits);
  const drawn=recruits.length?recruits:ranked.slice(0,6).map(x=>x[0]);   // draw the plan, else the top levers
  const nodes=[...new Set([target,...drawn,...(topU>=0?[topU]:[])])];
  const edges=drawn.map(u=>[u,target]);if(topU>=0&&!recSet.has(topU)&&!drawn.includes(topU))edges.push([topU,target]);
  route={edges,nodes,seed:null,targets:[target],topLever:topU,ordered:false,fliproute:true};
  routeCasc=lev;
  startRoute();
  const camp=CAMP(code), last=i=>DATA.name[i].split(' ').slice(-1)[0];
  let head;
  if(camp==='pro')head=`<div class=mrow><span><b>${DATA.name[target]}</b></span><b style=color:#16a34a>${HONORS?'Model: already supportive':'already pro'}</b></div><div class=hint style="margin:5px 0">no flip needed — colleagues with the most leverage over them:</div>`;
  else if(code==='-')head=`<div class=mrow><span><b>${DATA.name[target]}</b></span><b style=color:#A49D8A>${HONORS?'No modeled stance':'no stance'} on ${DATA.issues[issue]}</b></div><div class=hint style="margin:5px 0">colleagues with the most influence over them:</div>`;
  else if(!r||r.money===null)head=`<div class=mrow><span>Flip <b>${DATA.name[target]}</b></span><b style=color:#f59e0b>hard this cycle</b></div><div class=hint style="margin:5px 0">${r?flipPct(r.pressure,r.theta)+'% of the way — a full close is beyond reach, but the ':'the '}highest-leverage levers:</div>`;
  else head=`<div class=mrow><span>To flip <b>${DATA.name[target]}</b></span><b class="delta up">${flipPct(r.pressure,r.theta)}% → 100%</b></div>`
   +`<div class=mrow><span>Recruit <b>${recruits.length}</b>${r.money?' + money':''}</span><b>${r.money?'$'+(r.money/1000).toFixed(0)+'k':'—'}</b></div>`
   +`<div class=hint style="margin:5px 0">get these to flip <b>${last(target)}</b> (★ = most leverage · field = green helps most):</div>`;
  po.innerHTML=head+`<div style="font-size:11px;color:#D2CDBC;line-height:1.75">`+(ranked.length?ranked.slice(0,14).map(([u,l])=>`<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${cascCol(l)};margin-right:6px;vertical-align:middle"></span>${u===topU?'<b style=color:#34d399>★</b> ':''}<span class=lnk data-i=${u} style="cursor:pointer;text-decoration:underline">${mesc(DATA.name[u])}</span>${mprof(u)} <span style=color:#A49D8A>${Math.round(l*100)}%</span>${recSet.has(u)?' <span style=color:#16a34a>· recruit</span>':''}`).join('<br>'):'<span style=color:#9D9784>no recruitable colleagues have leverage over this member (isolated on this issue).</span>')+`</div>`;
  po.querySelectorAll('.lnk').forEach(el=>el.onclick=()=>selectMember(+el.dataset.i));
}
function runCascadeMode(){   // dispatch the cascade sub-mode for the selected member
  if(sel<0)return;
  const lab=document.getElementById('plabel'),nm=DATA.name[sel].split(' ').slice(-1)[0];
  if(cascSub==='max'){if(lab)lab.innerHTML='best route from <b style=color:#16a34a>'+nm+'</b> to maximize the cascade:';maxCascadeRoute();}
  else if(cascSub==='flip'){if(lab)lab.innerHTML='who to recruit to flip <b style=color:#fbbf24>'+nm+'</b> (maximize their join likelihood):';flipRoute();}
  else{if(lab)lab.innerHTML='cascade from <b style=color:#16a34a>'+nm+'</b> — colors the map by who joins on:';cascadeRun();}
}
function cascadeExit(){if(!cascadeOn)return;cascadeOn=false;cascP=null;cascSeeds=null;const cb=document.getElementById('cascbar');if(cb)cb.style.display='none';legend();draw();}
// COALITION HANDOFF: bring a group built in the Member Desk's coalition builder into the network — highlight the
// members (white ring), frame the camera on them, and color the field red->green by who's likely to join (cascade
// seeded from the group). Reached via ?coal=Name1,Name2 or window.__netCoalition(names) from the combined build.
function loadCoalition(idx){
  idx=[...new Set(idx.filter(i=>Number.isInteger(i)&&i>=0&&i<N))];
  if(!idx.length)return;
  cascadeExit();sel=-1;
  document.getElementById('mpanel').style.display='none';document.getElementById('tpanel').style.display='block';document.getElementById('mdlink').style.display='none';
  route={edges:[],nodes:idx,seed:null,targets:[],coalition:true,ordered:false};
  startRoute();   // fitCam(idx) frames the group + routeCasc=cascadeProbs(idx) colors the field by who's likely to join
}
window.__netCoalition=function(names){loadCoalition((names||[]).map(n=>DATA.name.indexOf(n)));};   // hook the combined-build controller calls
// ---- member panel + live scenario ----
const cusl=document.getElementById('cusl'),cosl=document.getElementById('cosl');
let baseCache=null,cascStr='';
function cacheBaseline(v){baseCache=[];for(let i=0;i<DATA.issues.length;i++){const m=flipModel(i,baseTiers(i),EG.cu,EG.ind);baseCache.push({nAsel:m.nA[v],nBsel:m.nB[v],mA:m.mA,mB:m.mB,tgsel:m.tg[v],pfbase:m.pf[v]});}}
function renderMemberSummary(v){
  const code=DATA.stance[v][issue],pl=PLAYLBL[DATA.play[v]],cp=Math.round(CENTPCT[v]*100);
  if(HONORS){const ov=DATA.overall_honors[v],badge=document.getElementById('mtier');
    const color=({0:'#A49D8A',1:'#fbbf24',2:'#93c5fd',3:'#d6a86a'})[ov.priority_tier];
    badge.innerHTML=`<div class=mrow><span>Overall priority</span><b style="color:${color}">${mesc(priorityLabel(v))}</b></div><div class=hint>${mesc(priorityRankText(v))}</div>`;
    badge.style.margin='3px 0 9px';}
  const overall=issue===DATA.issues.length-1;
  const standingRow=HONORS&&!overall?`<div class=mrow><span>Stance</span><b style=color:${publicColor(v,DATA.issues.length-1)}>${mesc(publicLabel(v,DATA.issues.length-1)+mixedRecordText(v))}</b></div>`+honorEvidence(v,DATA.issues.length-1):'';
  document.getElementById('mstance').innerHTML=
    `<div class=mrow><span>${HONORS&&overall?'Stance':'On '+DATA.issues[issue]}</span><b style=color:${publicColor(v)}>${mesc(publicLabel(v)+(HONORS&&overall?mixedRecordText(v):''))}</b></div>`+honorEvidence(v)+standingRow+
    `<div class=mrow title="recomputed from the issue-adjusted social-influence kernel"><span>Reach on this issue</span><b style=color:${centralCol(CENTPCT[v])}>top ${Math.max(1,100-cp)}% of Congress</b></div>`+
    `<div class=mrow><span>${HONORS?'Action record':'Overall'} · party</span><b>${mesc(HONORS?DATA.overall_honors[v].alignment:publicLabel(v,DATA.issues.length-1))} · ${DATA.party[v]}</b></div>`+
    `<div class=mrow><span>Bipartisan · conf · ${HONORS?'reporting ':''}rank</span><b>${DATA.bp[v]}% · ${DATA.cf[v]} · #${DATA.rank[v]}</b></div>`+
    `<div class=mrow><span>Electoral play</span><b style=color:${ELCOL[DATA.play[v]]}>${pl}</b></div>`+
    ((DATA.effectiveness_shadow_html||[])[v]||'');
}
function selectMember(v){
  cascadeExit();   // selecting a member = inspect it (spotlight + pressure gauge); re-press Cascade to re-seed from here
  sel=v;document.getElementById('tpanel').style.display='none';document.getElementById('mpanel').style.display='block';
  document.getElementById('mname').innerHTML=mlink(v);
  const _mt=document.getElementById('mtier');   // Legislative+Press tier badge
  if(_mt&&!HONORS){if(TIERS){const _tv=TIERS[v],_tr=TRANK?TRANK[v]:0;
    _mt.innerHTML=(_tv?`<span style="display:inline-block;background:${TIERCOL[_tv]};color:#1D1912;font-weight:800;font-size:11px;letter-spacing:.5px;padding:3px 10px;border-radius:11px">TIER ${_tv}</span>`
      :`<span style="display:inline-block;border:1px solid #564019;color:#A49D8A;font-weight:700;font-size:11px;letter-spacing:.5px;padding:2px 9px;border-radius:11px">UNTIERED</span>`)
      +` <span style="color:#A49D8A;font-size:11px">Primacy board${_tr?` · rank #${_tr}`:''}</span>`
      +tierEvidence(TDETAIL?TDETAIL[v]:null);
    _mt.style.margin='3px 0 9px';}else _mt.innerHTML='';}
  renderMemberSummary(v);
  cusl.value=0;cosl.value=0;cascStr='';noRoute();
  const _sn=document.getElementById('seednm');if(_sn)_sn.textContent=DATA.name[v].split(' ').slice(-1)[0];
  document.getElementById('tgtsearch').value='';document.getElementById('pathout').innerHTML='';
  const _md=document.getElementById('mdlink');_md.href='cu-member-desk.html?m='+encodeURIComponent(DATA.name[v]);_md.innerHTML='▤ Member details <small>· '+DATA.name[v]+'</small>';_md.style.display='block';
  cacheBaseline(v);renderPrime();runScenario();
  if(pmodeVal==='cascade')runCascadeMode();   // pick a member -> immediately show its cascade / max-route (keep the wide view, don't zoom in)
  else{flyTo(v);draw();}
}
// visual "pressure to flip" meter: green = pro-pressure already present, hatched red = the gap still needed,
// gold line at the right edge = the flip line (resistance threshold theta). Makes the ask explicit at a glance.
function flipPct(pressure,theta){return theta>0?Math.round(Math.max(0,Math.min(100,pressure/theta*100))):0;}
function pgauge(pressure,theta){
  const pf=theta>0?Math.max(0,Math.min(100,pressure/theta*100)):0;
  return `<div class=pgwrap><div class=pgcap><span>Pressure on them</span><span><b>${Math.round(pf)}%</b> of the way</span></div>`
   +`<div class=pgauge><div class=pg-fill style="width:${pf.toFixed(1)}%"></div>`
   +`<div class=pg-need style="left:${pf.toFixed(1)}%;width:${(100-pf).toFixed(1)}%"></div>`
   +`<div class=pg-line></div></div>`
   +`<div class=pglab><span style=color:#22c55e>0%</span><span style=color:#fbbf24>100% = flips ◀</span></div></div>`;
}
function renderPrime(){
  const r=prime(sel,issue),el=document.getElementById('mprime');
  if(!r){el.innerHTML=HONORS?`<span style=color:#9D9784>No modeled flip route is available for this member on this issue.</span>`:`<span style=color:#9D9784>Already pro / committed on this issue — no flip needed.</span>`;return;}
  const pct=flipPct(r.pressure,r.theta),togo=Math.max(0,100-pct);
  if(r.money===null){el.innerHTML=pgauge(r.pressure,r.theta)
     +`<div class=pgnote><b style=color:#f59e0b>A longer journey.</b> At <b>${pct}%</b> of the flip line — the remaining <b>${togo}%</b> is beyond what colleague pressure and money can close this cycle.</div>`;return;}
  const rc=r.recruits.slice(0,6).map(u=>`<span class=recruit data-i=${u}>${mesc(DATA.name[u].split(' ').slice(-1)[0])}${mprof(u)}</span>`).join('');
  el.innerHTML=pgauge(r.pressure,r.theta)
   +`<div class=pgnote>${HONORS?'Modeled persuasion':'Tier <b>'+r.tier+'</b> —'} <b>${pct}%</b> of the way to the flip line, <b>${togo}%</b> to go.<br>`
   +`Cheapest way across: <b>$${(r.money/1000).toFixed(0)}k</b> CU money${r.recruits.length?` + activate <b>${r.recruits.length}</b> already-pro / neutral neighbor${r.recruits.length>1?'s':''}`:''}.</div>`
   +(rc?`<div style=margin-top:5px>recruit: ${rc}</div>`:'');
  el.querySelectorAll('.recruit').forEach(s=>{s.onmouseenter=()=>{hi=+s.dataset.i;draw();};s.onclick=e=>rowSelect(e,s);});
}
function runScenario(){
  const cu=+cusl.value*1000,co=+cosl.value*1000;
  document.getElementById('culab').textContent='$'+cusl.value+'k';document.getElementById('colab').textContent='$'+cosl.value+'k';
  let dThis=0,dAll=0;
  for(let i=0;i<DATA.issues.length;i++){const c=baseCache[i],pfn=pflipOf(sel,c.nAsel,c.nBsel,c.mA,c.mB,EG.cu[sel]+cu,EG.ind[sel]+co);const d=c.tgsel*(pfn-c.pfbase);if(i<DATA.issues.length-1)dAll+=d;if(i===issue)dThis=d;}   // QME 2026-10-01: the appended 'Overall CU stance' layer is last and is not one of the 17 issues; desk_roi excludes it too

  const lean=shiftLean(sel,cu)-shiftLean(sel,co)*(K.LOSSFRAME||1),defects=Math.abs(lean)>=K.DTHRESH;   // competitor leg x LOSSFRAME (psych parity 2026-07-09 = money_response loss-frame asymmetry)
  const sg=x=>(x>=0?'up':'down'),ar=x=>(x>=0?'+':'');
  document.getElementById('scout').innerHTML=
    `<div class=mrow><span>How winnable · this issue</span><b class="delta ${sg(dThis)}">${ar(dThis)}${dThis.toFixed(3)}</b></div>`
   +`<div class=mrow><span>How winnable · all 17 issues</span><b class="delta ${sg(dAll)}">${ar(dAll)}${dAll.toFixed(2)}</b></div>`
   +`<div class=mrow><span>Net-lean shift</span><b class="delta ${sg(lean)}">${ar(lean)}${lean.toFixed(1)}</b></div>`
   +(defects?`<div class=defwarn style=margin-top:6px>⚠ crosses defection threshold (${K.DTHRESH}) — overall stance would shift ${lean>0?'toward pro':'toward anti'}.</div>`:'')
   +cascStr;
  // HEAD-TO-HEAD (this issue): CU pull vs industry pull -> flip odds + break-even spend
  const c=baseCache[issue],pm=PM?PM[sel]:1,cmS=EG.cmult?EG.cmult[sel]:1;
  const A=K.WM*pm*(cmS*(EG.cu[sel]+cu)+1)/((cmS*(EG.cu[sel]+cu)+1)+K.HALFF*EG.rcp[sel])+K.WP*c.nAsel/c.mA;
  const Bv=K.WM*pm*(cmS*(EG.ind[sel]+co)+1)/((cmS*(EG.ind[sel]+co)+1)+K.HALFF*EG.rcp[sel])+K.WP*c.nBsel/c.mB;
  const pct=Math.round(100*A/(A+Bv));
  document.getElementById('h2hcu').style.width=pct+'%';document.getElementById('h2hind').style.width=(100-pct)+'%';document.getElementById('h2hpct').textContent=pct+'%';
  const tA=c.pfbase*Bv/(1-c.pfbase),q=(tA-K.WP*c.nAsel/c.mA)/(K.WM*pm);let be;
  if(co===0)be='<span style=color:#9D9784>add competitor $ to see the CU break-even</span>';
  else if(q>=1)be='<b style=color:#f59e0b>CU money alone can’t fully offset</b> $'+(co/1000)+'k of competitor spend here';
  else{const need=Math.max(0,Math.round(((q*K.HALFF*EG.rcp[sel]/(1-q))-1)/cmS-EG.cu[sel]));be='to neutralize $'+(co/1000)+'k competitor → <b>$'+Math.round(need/1000)+'k</b> CU money';}
  document.getElementById('h2hbe').innerHTML=be;
}
const pedges=p=>p.slice(1).map((v,k)=>[p[k],v]);
function runPath(){
  const po=document.getElementById('pathout'),tv=document.getElementById('tgtsearch').value.trim(),last=i=>DATA.name[i].split(' ').slice(-1)[0];
  if(pmodeVal==='cascade'){if(sel<0){po.innerHTML='<span style=color:#f59e0b>Select a member first — it seeds the cascade.</span>';return;}runCascadeMode();return;}
  if(pmodeVal==='best'){   // who should introduce the bill to reach this target? (paper 04)
    const t=DATA.name.indexOf(tv);
    if(t<0){po.innerHTML='<span style=color:#f59e0b>Type the target vote you need, then Build.</span>';noRoute();draw();return;}
    const bs=bestSponsors(t,8);
    if(!bs.length){po.innerHTML='<span style=color:#f59e0b>No pro member can reach this target on this issue.</span>';noRoute();draw();return;}
    const b0=bs[0];route={edges:pedges(b0.path),nodes:b0.path,seed:b0.sponsor,targets:[t],ordered:true};
    po.innerHTML=`<div class=mrow><span>Best sponsor → ${last(t)}</span><b>${last(b0.sponsor)}</b></div>`
     +`<div class=mrow><span title="Scenario output from the ASKM dial; not an empirical probability">Modeled chain holds (ASKM=${ASKM.toFixed(2)}) · cost</span><b>${(b0.ch*100).toFixed(0)}% · $${(b0.cost/1000).toFixed(0)}k</b></div>`
     +`<div style=margin-top:6px;font-size:11px;color:#D2CDBC;line-height:1.6>`+bs.map((s,i)=>`<span style=color:#9D9784>${i+1}.</span> <span class=lnk data-i=${s.sponsor} style=cursor:pointer;text-decoration:underline>${mesc(DATA.name[s.sponsor])}</span>${mprof(s.sponsor)} <span style=color:#A49D8A>${(s.ch*100).toFixed(0)}% · $${Math.round(s.cost/1000)}k · ${s.hops}h</span>`).join('<br>')+`</div>`;
    po.querySelectorAll('.lnk').forEach(el=>el.onclick=()=>selectMember(+el.dataset.i));startRoute();return;
  }
  if(pmodeVal==='budget'){   // GCOMB budget-constrained max-coverage recruitment (diversified set)
    const bk=parseFloat(tv)||0;
    if(bk<=0){po.innerHTML='<span style=color:#f59e0b>Enter a budget in $k (e.g. 300).</span>';noRoute();draw();return;}
    const ns=Math.max(2,Math.round(bk*1000/K.CEDGE)),br=budgetRecruit(sel,ns,-1);
    route={edges:[],nodes:br.sel,seed:sel,targets:[]};
    po.innerHTML=`<div class=mrow><span>Budget · recruits</span><b>$${bk}k · ${br.sel.length}</b></div>`
     +`<div class=mrow><span>Expected reach (coverage)</span><b>~${br.reach.toFixed(0)} members</b></div>`
     +`<div style=margin-top:6px;font-size:11px;color:#D2CDBC;line-height:1.6>`+br.sel.slice(1,13).map((v,k)=>`<span style=color:#9D9784>${k+1}.</span> ${mlink(v)}`).join('<br>')+(br.sel.length>13?`<br><span style=color:#9D9784>+${br.sel.length-13} more</span>`:'')+`</div>`;
    startRoute();return;
  }
  if(pmodeVal==='multi'){   // reach several targets from one sponsor (shared tree; paper 06 coalitions)
    const ts=tv.split(',').map(s=>DATA.name.indexOf(s.trim())).filter(i=>i>=0&&i!==sel);
    if(!ts.length){po.innerHTML='<span style=color:#f59e0b>Type targets separated by commas.</span>';noRoute();draw();return;}
    const mt=multiTarget(sel,ts);route={edges:mt.edges,nodes:[...mt.tree],seed:sel,targets:ts};
    po.innerHTML=`<div class=mrow><span>Reached</span><b class="delta ${mt.reached===mt.n?'up':'down'}">${mt.reached}/${mt.n} targets</b></div>`
     +`<div class=mrow><span>Shared tree · cost</span><b>${mt.tree.size} members · $${(mt.total/1000).toFixed(0)}k</b></div>`
     +`<div style=margin-top:6px;font-size:11px;color:#D2CDBC;line-height:1.6>`+mt.paths.map(p=>`${mlink(p.t)} <span style=color:${p.reached?'#16a34a':'#ef4444'}>${p.reached?(chainHold(p.path)*100).toFixed(0)+'% holds':'unreachable'}</span>`).join('<br>')+`</div>`;
    startRoute();return;
  }
  // sponsor -> target
  const t=DATA.name.indexOf(tv);
  if(t<0){po.innerHTML='<span style=color:#f59e0b>Type a target member to build the path toward.</span>';noRoute();draw();return;}
  if(t===sel){po.innerHTML='<span style=color:#f59e0b>Sponsor and target are the same member.</span>';noRoute();draw();return;}
  const r=buildPath(sel,t);
  if(!r.reached){   // greedy max-product walk never lands on the target -> don't draw the meaningless wander; show it honestly
    route={nodes:[sel,t],edges:[],seed:sel,targets:[t],ordered:false,unreachable:true};
    po.innerHTML=`<div class=mrow><span>Path to ${last(t)}</span><b class="delta down">no reliable route</b></div>`
     +`<div class=hint style=margin-top:6px>No high-probability cosponsorship chain connects <b>${last(sel)}</b> to <b>${last(t)}</b> on this issue. Try <b>Best</b> to find a sponsor who can reach them, or pick a closer target.</div>`;
    startRoute();return;
  }
  r.reach=cascadeReach(r.path);route={edges:pedges(r.path),nodes:r.path,seed:sel,targets:[t],ordered:true};
  po.innerHTML=`<div class=mrow><span>Path · <b>${r.path.length}</b> members</span><b class="delta ${r.reached?'up':'down'}">${r.reached?'reaches target':'unreachable'}</b></div>`
   +`<div class=mrow><span title="Scenario output from the ASKM dial; not an empirical probability">Modeled chain holds (ASKM=${ASKM.toFixed(2)}) · weakest ask</span><b>${(chainHold(r.path)*100).toFixed(0)}% · ${(chainWeak(r.path)*100).toFixed(0)}%</b></div>`
   +`<div class=mrow><span>Direct recruitment</span><b>$${(r.total/1000).toFixed(0)}k</b></div>`
   +`<div class=mrow><span>Influence cascade</span><b>~${r.reach.toFixed(0)} co-sponsors</b></div>`
   +`<div style=margin-top:7px;font-size:11px;color:#D2CDBC;line-height:1.65>`
   +r.path.map((v,k)=>`<span style=color:#9D9784>${k+1}.</span> ${mlink(v)}`+(k===0?' <span style=color:#16a34a>· sponsor</span>':` <span style=color:#A49D8A>+$${Math.round(r.hops[k-1].cost/1000)}k · ${(activeP(r.hops[k-1].p)*100).toFixed(0)}% ask</span>`)+(v===t?' <span style=color:#fbbf24>· target</span>':'')).join('<br>')+`</div>`;
  startRoute();
}
function backToTargets(){cascadeExit();sel=-1;hi=-1;noRoute();document.getElementById('mpanel').style.display='none';document.getElementById('tpanel').style.display='block';document.getElementById('mdlink').style.display='none';document.getElementById('search').value='';document.getElementById('tgtsearch').value='';document.getElementById('pathout').innerHTML='';fit();draw();}
function applyShocks(){
  const ds=document.getElementById('dropsearch').value.trim();
  dropped=new Set(ds?ds.split(',').map(x=>DATA.name.indexOf(x.trim())).filter(i=>i>=0):[]);
  let parts=[];
  if(dropped.size)parts.push('<b>'+[...dropped].map(i=>DATA.name[i].split(' ').slice(-1)[0]).join(', ')+'</b> out');
  if(execMode!=='none'){const s=(EG.sal&&EG.sal[issue])||{p:.6,q:.3,b:.1,z:0};
   parts.push('exec <b class="'+(execMode==='oppose'?'down':'up')+'">'+execMode+'s</b> · going-public regime (Noble–Turner): rally '+Math.round(s.p*100)+'% / retreat '+Math.round(s.q*100)+'% / backfire '+Math.round(s.b*100)+'%'+(execMode==='support'&&s.q>s.p?' — <span style=color:#f59e0b>polarized issue: out-party counter-mobilizes</span>':''));
   if(EG.adv&&EG.adv[issue])parts.push('CU advisory: <b>'+EG.adv[issue]+'</b>');}
  document.getElementById('shockout').innerHTML=parts.length?('shock active \u2014 '+parts.join(' \u00b7 ')):'';
  if(sel>=0&&document.getElementById('tgtsearch').value.trim())runPath();
  if(pmodeVal==='cascade'&&sel>=0)runCascadeMode();   // keep the cascade / max-route live under dropouts / exec cue
  draw();
}
// JS/Python parity self-check. The comparison is UNCHANGED — same t, same reference, same tolerance.
// Only its always-on text moved into title= (plain-language pass): a pass shows a bare check mark, a
// divergence still shows a visible warning on the canvas, exactly as before.
function updateEngChk(){
  const d=domainPolicy(),el=document.getElementById('engchk'),leaders=(IP.roles||[]).reduce((n,row)=>n+(((row||'')[issue]||'-')!=='-'),0);
  const note=` · issue IC ×${Number(d.ic_edge_scale||1).toFixed(2)} · LT ×${Number(d.lt_threshold_scale||1).toFixed(2)} · ${leaders} jurisdiction leaders`;
  const r=DATA.engine.refflip[issue];
  if(!r){el.textContent='—';el.title='Overall view — no per-issue self-check to run.'+note;return;}
  const t=flipModel(issue,baseTiers(issue),EG.cu,EG.ind).total,ok=Math.abs(t-r)<=Math.max(0.02,0.01*r);   // QME 2026-10-01: was max(0.2,3%) -- the 0.2 floor swamped small refflip; measured worst drift after D-077 is 0.23%. `if(!r)` above still skips the synthetic Overall layer (refflip 0)
  el.textContent=ok?'✓':'⚠ self-check failed';
  el.title=(ok?'Self-check passed':'SELF-CHECK FAILED')+` — this page computed ${t.toFixed(2)}, the published figure is ${r.toFixed(2)}.`+note;
}
// panels
function fmt(n){return '$'+Math.round(n).toLocaleString();}
function frontierKneeText(f){return f.knee_found===true?'best value at $'+(f.knee/1000).toFixed(0)+'k':'best-value point not identified within this range';}
function refresh(){
 if(HONORS)refreshHonorControls();
 const f=DATA.frontier[issue], useSlider=(mode==='coal'&&!!f);
 const st=DATA.stats[issue];
 const tc={C:0,F:0,A:0,H:0,P:0,G:0,T:0,N:0,L:0,S:0,K:0,O:0,'-':0};
 for(let i=0;i<N;i++){const c=DATA.stance[i][issue];tc[c]=(tc[c]||0)+1;}
 const tierRows=[['C','Champion',tc.C],['F','Defender',tc.F],['A','Ally',tc.A],
   ['H','Highly Persuadable',tc.H],['P','Persuadable',tc.P],['G','Lean Persuadable',tc.G],
   ['T','True Neutral',tc.T+tc.N],['L','Lean Skeptic',tc.L],['S','Skeptic',tc.S],
   ['K','Highly Skeptical',tc.K],['O','Opponent',tc.O],['-','No evidence',tc['-']]];
 document.getElementById('istats').innerHTML=HONORS?honorStats():tierRows.filter(x=>x[2]>0).map(([c,label,n])=>
   `<div class=stat><span><span class=pill style=background:${STCOL[c]}></span>${label}</span><b>${n}</b></div>`).join('');
 document.getElementById('bhdr').textContent=useSlider?'Issue budget':(mode==='coal'?'Money deployed':'Money deployed');
 slider.style.display=useSlider?'block':'none';
 document.querySelector('#budgetwrap .frow').style.display=useSlider?'flex':'none';
 if(useSlider){const mx=f.path[f.path.length-1][0];budget=(+slider.value/100)*mx;document.getElementById('bmax').textContent='$'+(mx/1e6).toFixed(1)+'M';}
 computeFund();
 if(useSlider){
   document.getElementById('money').textContent='$'+(budget/1e6).toFixed(2)+'M';
   // SPREAD is the modelled OUTPUT (projected co-sponsors at this budget) against f.base, the unfunded
   // baseline -- without it the line states only inputs and never says what the spend buys. Both are
   // non-null here by construction: useSlider requires mode==='coal' && DATA.frontier[issue], which is the
   // exact condition computeFund() uses to set SPREAD. "$50k each" mirrors the flat FUND pin at :304 --
   // in coalition mode every entrant is allocated the same 50, so the count implies no tailored amount.
   document.getElementById('mdetail').innerHTML=`~<b>${SPREAD.toFixed(0)}</b> co-sponsors, up from <b>${f.base.toFixed(0)}</b> · <b>${ORD.length}</b> members funded at $50k each · ${frontierKneeText(f)} · drag to set budget`;
 }else if(DATA.engine.refflip[issue]===0){   // Overall = aggregate stance layer; summing every issue's budget is meaningless
   document.getElementById('money').textContent='—';
   const ob=DATA.opt?('$'+(DATA.opt.budget/1e6).toFixed(1)+'M'):'joint';
   document.getElementById('mdetail').innerHTML=`aggregate stance view (no single issue budget) — your <b>${ob} total</b> plan is the optimizer card below`;
 }else{
   const tot=mode==='coal'?st.coal:st.flip;
   document.getElementById('money').textContent=fmt(tot);
   document.getElementById('mdetail').innerHTML=(mode==='coal'?`competitive cascade · ${st.coalN} seeds (capped $50k) · `:`competitive linear-threshold · persuadable targets · `)+'<span style=color:#9D9784>this is your full $2M focused on THIS issue — issues are alternatives, not additive</span>';
 }
 if(rmode==='flip'){
   document.getElementById('tgtmode').textContent='· internal pressure to flip';
   const cf=cheapestFlips(issue,16),tot=cf.reduce((s,r)=>s+r[1],0);flipHi=new Set(cf.map(r=>r[0]));
   document.getElementById('targets').innerHTML=(cf.length?`<div class=hint style=margin-bottom:6px>cheapest tip-ins on this issue · <b style=color:#D2CDBC>$${(tot/1000).toFixed(0)}k</b> to flip ${cf.length}</div>`:'')+cf.map(([v,m,nr])=>
     `<div class=tgt tabindex=0 data-i=${v}><span><span class=pill style=background:${publicColor(v)}></span>${mesc(DATA.name[v])}${mprof(v)}</span><small>$${Math.round(m/1000)}k${nr?' +'+nr:''}</small></div>`).join('')||'<div class=hint>none primeable with money on this issue</div>';
 }else{
   flipHi=null;
   document.getElementById('tgtmode').textContent='· '+(mode==='coal'?'coalition (entry order)':'persuasion ($)');
   const arr=ORD.slice(0,16);
   document.getElementById('targets').innerHTML=arr.map(([i,v])=>
     `<div class=tgt tabindex=0 data-i=${i}><span><span class=pill style=background:${publicColor(i)}></span>${mesc(DATA.name[i])}${mprof(i)}</span><small>${mode==='coal'&&useSlider?'@$'+kfmt(v)+'k':'$'+kfmt(v)+'k'}</small></div>`).join('')||'<div class=hint>no funded targets</div>';
 }
 document.querySelectorAll('.tgt').forEach(el=>el.onclick=e=>rowSelect(e,el));
 if(sel>=0){renderMemberSummary(sel);cacheBaseline(sel);renderPrime();runScenario();}   // keep the member panel, issue stance, area centrality, and scenario in sync with issue/mode (cascade re-runs on issue change / shocks, not every refresh)
 updateEngChk();
 if(HONORS)legend();
 draw();
}
// optimizer card (static cross-topic CU allocation)
function optCard(){
 const o=DATA.opt;if(!o){document.getElementById('optbody').innerHTML='<div class=hint>not available</div>';return;}
 document.getElementById('opthdr').textContent=`Budget across all issues · $${(o.budget/1e6).toFixed(1)}M`;
 const top=o.alloc.slice(0,8);
 let h=`<div class=optline>winnability <b>${o.base.toFixed(1)} → ${o.final.toFixed(1)}</b> (<b>+${(o.final-o.base).toFixed(1)}</b> expected flips)</div>`;
 h+=`<div class=optline style=color:#A49D8A>$${(o.budget/1e6).toFixed(1)}M across 17 issues · ${o.alloc.length} members${o.conv.length?` · ${o.conv.length} convert`:''}</div>`;
 // OPTIMALITY GUARANTEE INDICATOR. This is the only surface that separates a run whose approximation
 // guarantee HOLDS from one whose does not, so it must never be dropped for being technical.
 // optimize.py: certificate_valid = no modeled money-triggered conversion is reachable AND every member's
 // chunk marginals verified non-increasing AND no conversion actually fired (:142, :99). When it holds the
 // objective is separable and concave on the $INC grid, so highest-next-marginal IS the exact optimum --
 // opt_bound is set to the ACHIEVED value cur (:147), i.e. a ceiling the plan already reaches, NOT a
 // tolerance. Never render it as "within X". Plain claim on screen, solver + certified-OPT on hover.
 const OPTMODE={fixed_state_lazy_greedy:'fixed-state lazy greedy',modeled_threshold_replanning:'modeled threshold replanning'};
 const _omode=OPTMODE[o.mode]||o.mode||'unrecorded';            // 'legacy' (stale artifact) stays 'legacy', never mislabelled
 const _obnd=(o.bound==null)?null:Number(o.bound).toFixed(1);
 const _ocert=!!o.certificate;
 const _otxt=_ocert?'Best possible split of this budget':'No guarantee this is the best split';
 const _otip=_ocert
   ?`Solver: ${_omode}. Holding every member's stance state fixed and searching on the optimizer's funding grid, `
     +(_obnd!=null
        ?`no allocation of this budget scores above ${_obnd} expected flips (certified OPT ≤ ${_obnd}) — and this plan reaches it.`
        :`this run is certified exactly optimal, but the artifact recorded no numeric ceiling for it.`)
   :`Solver: ${_omode}. The fixed-state optimality certificate does not hold here — either a modeled funding threshold can change a member's stance state mid-plan, or per-member returns did not diminish the way the certificate requires. This allocation is still the optimizer's best pass; it is simply not certified as the best possible.`;
 h+=`<div class=optline style=color:#9D9784 title="${_otip.replace(/"/g,'&quot;')}">${_otxt}</div>`;
 h+=top.map(([i,v])=>`<div class=oa tabindex=0 data-i=${i}><span>${o.conv.includes(i)?'<span class=conv>● </span>':''}${mesc(DATA.name[i])}${mprof(i)}</span><small>$${v}k</small></div>`).join('');
 document.getElementById('optbody').innerHTML=h;
 document.querySelectorAll('.oa').forEach(el=>el.onclick=e=>rowSelect(e,el));
}
// legend
function legend(){
 if(cascadeOn||(route&&routeCasc)){let header='Likelihood to activate',note,labels=[[0,"won't"],[0.35,'maybe'],[0.65,'likely'],[1,'near-certain']];
   if(cascadeOn){note=`chance they join · based on ${K.MC} simulated runs · ○ white ring = seed`;}
   else if(route.fliproute){header='Who to recruit to flip the target';labels=[[0,'no help'],[0.4,'some'],[0.7,'strong'],[1,'top lever']];
     const tn=(route.targets&&route.targets[0]!=null)?DATA.name[route.targets[0]].split(' ').slice(-1)[0]:'target';
     note=`<b style=color:#16a34a>green</b> members have the most leverage to flip <b>${tn}</b> · green nodes = the recruit plan`;}
   else if(route.coalition){header='Who joins your coalition';let rc=0;const st=new Set(route.nodes);for(let i=0;i<N;i++)if(!st.has(i))rc+=routeCasc[i];
     note=`<b>${route.nodes.length}</b>-member coalition (○ white ring) · field = <b>~${rc.toFixed(0)}</b> more likely to join`;}
   else{let rc=0;const st=new Set(route.nodes);for(let i=0;i<N;i++)if(!st.has(i))rc+=routeCasc[i];
     note=`<b style=color:#34d399>green path</b> = your ${route.nodes.length} recruits · field = <b>~${rc.toFixed(0)}</b> more likely to activate`;}
   document.getElementById('legend').innerHTML=
   '<span style="color:#F3F3E6;font-weight:600">'+header+'</span><br>'
   +labels.map(([v,lb])=>`<span><span class=pill style="background:${cascCol(v)}"></span>${lb}</span>`).join('')
   +`<br><span style=color:#9D9784>${note}</span>`;return;}
 let items;
 if(colorBy==='central')items=[[0,'low'],[.5,'middle'],[1,'high']].map(([v,lb])=>`<span><span class=pill style=background:${centralCol(v)}></span>${lb}</span>`).join('')+`<span style=color:#9D9784> · ${DATA.issues[issue]} outbound reach · issue IC + jurisdiction-role sensitivity · recomputed on area change</span>`;
 else if(colorBy==='recept')items=[[0.55,'Independent-minded'],[0.8,''],[1.0,'Neutral'],[1.3,''],[1.55,'Highly receptive']].map(([v,lb])=>`<span><span class=pill style=background:${receptCol(v)}></span>${lb}</span>`).join('')+'<span style=color:#9D9784> · behavioral receptivity (cosponsorship, participation, floor dynamics, live-interview reactivity × electoral incentive × outlook)</span>';
 else if(colorBy==='antag')items=[[0,'Even-keeled'],[1.2,''],[2.4,'Highly antagonistic']].map(([v,lb])=>`<span><span class=pill style=background:${antagCol(v)}></span>${lb}</span>`).join('')+'<span><span class=pill style=background:#30363d></span>no hearing data</span><span style=color:#9D9784> · hearing-antagonism facet (member-vs-member interrupts + Haiku-verified hostile-witness questioning; ideology/seniority/party/minority-controlled; display-only, rank-independent)</span>';
 else if(colorBy==='elec')items=[['c','Contest · primary'],['g','Contest · general'],['d','Defend'],['n','No play']].map(([k,lb])=>`<span><span class=pill style=background:${ELCOL[k]}></span>${lb}</span>`).join('');
 else if(colorBy==='party')items=[['D','Democrat'],['R','Republican'],['I','Independent']].map(([k,lb])=>`<span><span class=pill style=background:${PTCOL[k]}></span>${lb}</span>`).join('');
 else if(HONORS){const j=colorBy==='gen'?DATA.issues.length-1:issue,labels=new Set(DATA.name.map((_,i)=>publicLabel(i,j)));
   items=Object.keys(HONOR_COL).filter(label=>labels.has(label)).map(label=>`<span><span class=pill style=background:${HONOR_COL[label]}></span>${mesc(label)}</span>`).join('');}
 else items=[['C','Champion'],['A','Ally'],['H','Hi-Persuad'],['P','Persuad'],['G','Lean-Pers'],['T','Neutral'],['L','Lean-Skep'],['S','Skeptic'],['K','Hi-Skeptic'],['O','Opponent']].map(([k,lb])=>`<span><span class=pill style=background:${STCOL[k]}></span>${lb}</span>`).join('');
 const _tn=HONORS&&viewFiltersActive()?`<span style="color:#EECD5C;font-weight:700">${[tierFilter===4?'Outside priority tiers':tierFilter?'Overall priority T'+tierFilter:'',issue!==DATA.issues.length-1&&fieldHonorFilter?'Field '+({1:'Champion',2:'Ally',3:'Highly Persuadable'}[fieldHonorFilter]):'',issue===DATA.issues.length-1&&standingFilter?'Standing '+standingFilter:''].filter(Boolean).map(mesc).join(' · ')}</span> <span style=color:#9D9784>· ${DATA.name.filter((_,i)=>!tierHidden(i)).length} shown · ranks and cohorts unchanged</span><br>`:tierFilter?`<span style="color:${TIERCOL[tierFilter]};font-weight:700">● Tier ${tierFilter} only</span> <span style=color:#9D9784>· legislative + press · ${tierCounts[tierFilter]||0} members</span><br>`:'';
 document.getElementById('legend').innerHTML=_tn+items+'<br><span style=color:#9D9784>○ white ring = funded · size = $ · scroll to zoom, drag to pan</span>';
}
// controls
isel.onchange=()=>{issue=+isel.value;slider.value=100;configureIssueNetwork();if(layout==='area')setLayout('area',true);refresh();if(pmodeVal==='cascade'&&sel>=0)runCascadeMode();};   // reconfigure domain IC/LT + chair modifiers, and re-layout area topology, before recomputing routes
slider.oninput=()=>refresh();
document.querySelectorAll('#mode button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#mode button').forEach(x=>x.classList.remove('on'));b.classList.add('on');mode=b.dataset.m;refresh();
  if(pmodeVal==='cascade'&&sel>=0){cascSub=(mode==='flip')?'flip':(cascSub==='flip'?'who':cascSub);document.querySelectorAll('#cascsub button').forEach(x=>x.classList.toggle('on',x.dataset.cs===cascSub));runCascadeMode();}});   // Persuade<->Coalition swaps the cascade sub-mode default (Flip route <-> Who joins)
document.querySelectorAll('#color button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#color button').forEach(x=>x.classList.remove('on'));b.classList.add('on');colorBy=b.dataset.c;cascadeExit();legend();draw();});
document.querySelectorAll('#tierseg button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#tierseg button').forEach(x=>x.classList.remove('on'));b.classList.add('on');tierFilter=+b.dataset.t;legend();draw();});   // Legislative+Press tier filter (display only)
document.querySelectorAll('#fieldhonorseg button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#fieldhonorseg button').forEach(x=>x.classList.remove('on'));b.classList.add('on');fieldHonorFilter=+b.dataset.h;legend();draw();});
document.getElementById('standingfilter').onchange=e=>{standingFilter=e.target.value;legend();draw();};
// ===== COALITION POSTURE (standard / advisory / contested) — DISPLAY ONLY =====
// Mirrors fec/contested_bills.py. Standard is the structural default: no registry, or no record for a bill,
// means no second-industry term. Advisory shows the counterparty read beside the standard ranking and never
// reorders it. Contested is per-bill with a named counterparty industry set. NOTHING here re-ranks or
// re-allocates: no engine consumes the registry yet (the CONTESTED_V1 gate is unopened), and this panel says
// so plainly rather than implying an effect it does not have.
let posture='standard';
const _pesc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
// DISPLAY ONLY. Engine values stay 'standard'|'advisory'|'contested' everywhere -- dataset.pos, the
// `posture` variable, r.mode, every comparison and contested_bills.json are untouched. This map is
// applied at the point of render so a raw engine token never reaches the screen. An unmapped value
// falls through unchanged (and still escapes) rather than being silently renamed.
const POSLABEL={standard:'Normal',advisory:'Show rival',contested:'Head-to-head'};
const _plab=v=>POSLABEL[v]||v;
function _precrow(r){
  return `<div class=mrow style="align-items:flex-start"><span><b style=color:#D2CDBC>${_pesc(r.bill)}</b> <span style=color:#9D9784>(${_pesc(r.congress)}th)</span><br>`
    +`<span style="font-size:10px;color:#A49D8A">vs ${_pesc((r.ind||[]).join(', '))}</span></span>`
    +`<b style="color:${r.mode==='contested'?'#f59e0b':'#38bdf8'};text-transform:uppercase;font-size:10px">${_pesc(_plab(r.mode))}</b></div>`;
}
function renderPosture(){
  const P=DATA.posture, badge=document.getElementById('postbadge'), body=document.getElementById('postbody');
  if(!badge||!body)return;
  if(!P){badge.innerHTML='';body.innerHTML='<span class=hint style=color:#9D9784>Approach registry not present in this bundle — rebuild with build_dashboard_bundle.py.</span>';return;}
  const absent=P.status!=='present', act=P.active||[], dor=P.dormant||[];
  const inMode=act.filter(r=>posture==='contested'?r.mode==='contested':true);
  badge.innerHTML=absent?'<span style=color:#9D9784>no contested bills · Normal everywhere</span>'
    :`<span style=color:#38bdf8>${act.length} active</span>${dor.length?` <span style=color:#9D9784>· ${dor.length} dormant</span>`:''}`;
  let h='';
  if(posture==='standard'){
    h=`<div class=hint style="color:#A49D8A;line-height:1.6">Ordinary coalition building. Targets rank on receptivity, pivotality and access cost; no second-industry term enters. `
      +`This is the default and it is <b>structural</b> — with no registry record a bill is in the Normal approach, so peace is never something you have to remember to switch back on.</div>`;
  } else if(posture==='advisory'){
    h=`<div class=hint style="color:#A49D8A;line-height:1.6">The counterparty read is shown <b>beside</b> the normal ranking and <b>does not reorder it</b> — the alternative route when presenting next targets.</div>`;
  } else {
    h=`<div class=hint style="color:#A49D8A;line-height:1.6">Per-bill approach with an explicitly named counterparty industry set. Scoped and dated — never global, because the shop can be contested with an industry on one bill and cooperating with it on another the same week.</div>`;
  }
  if(absent){
    h+=`<div style="margin-top:10px;padding:9px 11px;border:1px dashed #564019;border-radius:6px;color:#A49D8A;font-size:11px;line-height:1.6">`
      +`<b style=color:#D2CDBC>No bills registered.</b> Every bill is in the Normal approach. To put a bill into the Show rival or Head-to-head approach, create <code style=color:#38bdf8>${_pesc(P.file||'contested_bills.json')}</code> at the data root — one record per bill with its counterparty industries, active dates and who ratified it.</div>`;
  } else if(!inMode.length){
    h+=`<div style="margin-top:10px;color:#9D9784;font-size:11px">No active ${_pesc(_plab(posture))} records.</div>`;
  } else {
    h+='<div style=margin-top:10px>'+inMode.map(_precrow).join('')+'</div>';
  }
  if(!P.consumer&&posture!=='standard'){
    h+=`<div style="margin-top:10px;color:#f59e0b;font-size:10px;line-height:1.55">⚠ Display only — no engine consumes the registry yet, so allocations and rankings on this page are unchanged. Wiring the consumer is a separate, ratified step.</div>`;
  }
  body.innerHTML=h;
}
document.querySelectorAll('#postureseg button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#postureseg button').forEach(x=>x.classList.remove('on'));b.classList.add('on');posture=b.dataset.pos;renderPosture();});
document.querySelectorAll('#lybar input').forEach(cb=>cb.onchange=()=>draw());   // layer toggles redraw only
// AREA TOPOLOGY: a deterministic spectral layout of the issue-adjusted PE kernel.  It moves only the
// visualization: membership and the underlying relationship set remain fixed.  A lazy normalized walk
// exposes weighted communities/bridges without the jitter and run-to-run drift of a random force layout.
function areaTopologyPositions(){
  const BX=baseX(),BY=baseY();   // reference frame follows the network configuration: the
  // orientation search below exists to hold the area view in the same frame as the cosp view,
  // so it must use the same reference that config's cosp view uses.
  const nb=Array.from({length:N},()=>[]),deg=new Float64Array(N);
  for(let u=0;u<N;u++)for(const sv in PE[u]){const v=+sv;if(v<=u)continue;
    const w=.5*((PE[u][v]||0)+((PE[v]&&PE[v][u])||0));if(w<=1e-12)continue;
    nb[u].push([v,w]);nb[v].push([u,w]);deg[u]+=w;deg[v]+=w;}
  const trivial=new Float64Array(N);let tn=0;for(let i=0;i<N;i++){trivial[i]=Math.sqrt(deg[i]);tn+=trivial[i]*trivial[i];}
  tn=Math.sqrt(tn)||1;for(let i=0;i<N;i++)trivial[i]/=tn;
  const orth=(v,bases)=>{for(const b of bases){let dot=0;for(let i=0;i<N;i++)dot+=v[i]*b[i];for(let i=0;i<N;i++)v[i]-=dot*b[i];}
    let z=0;for(let i=0;i<N;i++)z+=v[i]*v[i];z=Math.sqrt(z)||1;for(let i=0;i<N;i++)v[i]/=z;return v;};
  let qx=new Float64Array(N),qy=new Float64Array(N);
  for(let i=0;i<N;i++){if(deg[i]>1e-12){qx[i]=(BX[i]-500)/500+((i*17)%29-14)*1e-5;qy[i]=(BY[i]-500)/500+((i*31)%37-18)*1e-5;}}
  orth(qx,[trivial]);orth(qy,[trivial,qx]);
  for(let it=0;it<70;it++){
    const zx=new Float64Array(N),zy=new Float64Array(N);
    for(let u=0;u<N;u++){let sx=0,sy=0;for(const [v,w] of nb[u]){const a=w/Math.sqrt(Math.max(1e-12,deg[u]*deg[v]));sx+=a*qx[v];sy+=a*qy[v];}
      zx[u]=.5*qx[u]+.5*sx;zy[u]=.5*qy[u]+.5*sy;}   // lazy walk shifts the spectrum to [0,1]
    orth(zx,[trivial]);orth(zy,[trivial,zx]);
    let dx=0,dy=0;for(let i=0;i<N;i++){dx+=zx[i]*qx[i];dy+=zy[i]*qy[i];}if(dx<0)for(let i=0;i<N;i++)zx[i]*=-1;if(dy<0)for(let i=0;i<N;i++)zy[i]*=-1;
    qx=zx;qy=zy;
  }
  const corr=(a,b)=>{let am=0,bm=0;for(let i=0;i<N;i++){am+=a[i];bm+=b[i];}am/=N;bm/=N;let n=0,da=0,db=0;for(let i=0;i<N;i++){const x=a[i]-am,y=b[i]-bm;n+=x*y;da+=x*x;db+=y*y;}return n/Math.sqrt(Math.max(1e-18,da*db));};
  const bx=BX,by=BY,candidates=[[qx,qy],[qy,qx], [Float64Array.from(qx,x=>-x),qy],[qx,Float64Array.from(qy,y=>-y)],
    [Float64Array.from(qx,x=>-x),Float64Array.from(qy,y=>-y)],[Float64Array.from(qy,y=>-y),qx],[qy,Float64Array.from(qx,x=>-x)],[Float64Array.from(qy,y=>-y),Float64Array.from(qx,x=>-x)]];
  let best=candidates[0],score=-Infinity;for(const pair of candidates){const s=corr(pair[0],bx)+corr(pair[1],by);if(s>score){score=s;best=pair;}}
  const scale=v=>{const s=Array.from(v).sort((a,b)=>a-b),lo=s[Math.floor(.02*(N-1))],hi=s[Math.ceil(.98*(N-1))],span=Math.max(1e-9,hi-lo);
    return Array.from(v,x=>75+850*Math.max(0,Math.min(1,(x-lo)/span)));};
  const ax=scale(best[0]),ay=scale(best[1]);
  for(let i=0;i<N;i++)if(deg[i]<=1e-12){ax[i]=BX[i];ay[i]=BY[i];}   // isolated members keep their stable reference position
  return {x:ax,y:ay};
}
let layoutEpoch=0;
// ONE tween owns LX/LY. layoutEpoch is the cancellation token: a newer tween increments it and every
// older frame callback sees the mismatch and returns, so overlapping triggers (fast-clicking the
// layout or network control) hand off smoothly from wherever the field currently is, instead of two
// loops writing LX[i] from different snapshots in the same frame. t0 is captured INSIDE, i.e. after
// all caller-side precomputation, so the animation never starts already half-elapsed.
function tweenTo(tx,ty,dur){
  dur=RMQ?1:(dur||850);
  // The cached hover target is about to be somewhere else; leaving it set would draw the gold ring
  // on a node that slid out from under the cursor, and the click handler would then select it.
  if(hi>=0){hi=-1;const _t=document.getElementById('tip');if(_t)_t.style.display='none';}
  const x0=LX.slice(),y0=LY.slice(),t0=performance.now(),epoch=++layoutEpoch,
        anchor=sel>=0?sel:-1,anchorScreen=anchor>=0?toScreen(LX[anchor],LY[anchor]):null;
  (function anim(now){if(epoch!==layoutEpoch)return;const k=Math.min(1,(now-t0)/dur),e=k<.5?2*k*k:1-Math.pow(-2*k+2,2)/2;
    for(let i=0;i<N;i++){LX[i]=x0[i]+(tx[i]-x0[i])*e;LY[i]=y0[i]+(ty[i]-y0[i])*e;}
    if(anchor>=0){cam.tx=anchorScreen[0]-LX[anchor]*cam.s;cam.ty=anchorScreen[1]-LY[anchor]*cam.s;}   // selected member stays fixed on screen for orientation
    draw();if(k<1)requestAnimationFrame(anim);})(t0);
}
function setLayout(L,recompute=false,dur){
  if((L===layout&&!recompute)||(L==='committee'&&!DATA.cx))return;
  layout=L;document.querySelectorAll('#ltog button').forEach(x=>x.classList.toggle('on',x.dataset.l===L));
  const area=L==='area'?areaTopologyPositions():null,tx=L==='committee'?DATA.cx:(area?area.x:baseX()),ty=L==='committee'?DATA.cy:(area?area.y:baseY());
  const note=document.getElementById('layoutnote');
  if(L==='area'){note.style.display='block';note.innerHTML=`<b>Group by Policy Area · ${DATA.issues[issue]}</b><br>Positions and line emphasis visualize issue-adjusted weights. No members or underlying ties were added or removed.`;}
  else note.style.display='none';
  tweenTo(tx,ty,dur||850);
}
// ---- HOLISTIC NETWORK TOGGLE ----------------------------------------------------------
// Rebuilds the cascade kernel in place from whichever adjacency is selected, then re-runs the
// same configuration path a domain change already uses, so every engine on the page (cascade,
// pathfinder, centrality, PageRank, budget recruit) follows the switch rather than a subset.
// Node POSITIONS follow the configuration. Each network ships its own layout, warm-started from
// the credit-union layout and Procrustes-aligned back onto it by the producer, so all three live
// in ONE coordinate frame and a node's displacement measures structural change rather than solver
// gauge. Committee layout is EXEMPT: DATA.cx/cy comes from workbook committee membership, which no
// network configuration changes, and the anchor labels at DATA.comms are drawn at fixed coords.
function setNetConfig(cfg){
  if(cfg===NETCFG)return;
  const src=netAdj(cfg);
  if(!src)return;
  const _pbx=baseX(),_pby=baseY();   // base layout of the OUTGOING config, before NETCFG flips
  NETCFG=cfg;
  document.querySelectorAll('#netseg button').forEach(x=>x.classList.toggle('on',x.dataset.net===cfg));
  ADJ=Array.from({length:N},()=>({}));for(const [u,v,w] of src){ADJ[u][v]=w;ADJ[v][u]=w;}
  EDGES=netEdges(cfg);
  ELAYS=netElay(cfg);
  buildPEBASE();
  configureIssueNetwork();       // rebuilds PE, degrees, PageRank and Katz from the new PEBASE
  const note=document.getElementById('netnote');
  // A promoted sector runs the SAME engines as CU, so the banner states how far below CU it sits
  // rather than letting the toggle imply parity. Generated from the bundle meta -- the producer
  // measures these, the surface never restates them by hand.
  const _sm=netMeta(cfg), _isSector=(cfg==='bank_reg'||cfg==='crypto');
  if(note&&_isSector&&_sm){
    const f=_sm.engine_floor||{};
    note.textContent=(cfg==='bank_reg'
      ? 'Bank / depository-institution regulation: '+(_sm.pairs||0).toLocaleString()+' member pairs, '
        +'universe defined by CRS subject terms over the 117th-119th. '
      : 'Digital assets: '+(_sm.pairs||0).toLocaleString()+' member pairs, universe defined by a '
        +'display-title screen because NO CRS subject term for crypto exists. Candidates, not labels. ')
      +'COSPONSORSHIP ONLY: the press, committee-affinity, social and co-appearance layers are NOT '
      +'applied here, and cross-party damping still uses the CU-defined bipartisan prior. '
      +'WEAKER THAN THE PUBLISHED NETWORK: strongest tie p='+(f.p_max!==undefined?f.p_max:'?')
      +' against '+(f.cu_p_max!==undefined?f.cu_p_max:'?')+' on credit union, and '
      +(f.dyads_p_ge_010!==undefined?f.dyads_p_ge_010:'?')+' pairs above p=0.10 against '
      +(f.cu_dyads_p_ge_010!==undefined?f.cu_dyads_p_ge_010:'?')+'. '
      +(_sm.members_isolated?(_sm.members_isolated+' of 533 members have NO TIE in this network, so '
        +'engine output for them is uninformative. '):'')
      // Concentration is the reason a sector graph is thin, and it is more actionable than the
      // aggregate: a reader told "444 pairs" pictures a graph, not one member holding a fifth of it.
      +((_sm.concentration&&_sm.concentration.dominated)
        ? ('DOMINATED BY ONE HUB: '+_sm.concentration.hub+' has '+_sm.concentration.hub_degree
           +' ties against '+_sm.concentration.second_degree+' for the next member, and carries '
           +_sm.concentration.hub_share_of_weight_pct+'% of all edge weight -- so cascade reach '
           +'collapses when seeded anywhere else. ')
        : (_sm.concentration
           ? ('Most connected: '+_sm.concentration.hub+' ('+_sm.concentration.hub_degree+' ties, '
              +_sm.concentration.hub_share_of_weight_pct+'% of edge weight). ')
           : ''))
      +'Published figures use the credit-union network.';
  }
  else if(note)note.textContent=cfg==='holistic_norm'
    ? ('All bills, NORMALIZED: chamber strata equalized, and per-member total tie mass capped at 1.5x the median, '
       +'which removes the activity artifact in the raw view: Eleanor Norton, a NON-VOTING DC delegate, is PageRank #1 of 533 in the raw all-bills view and #197 here (#78 in the credit-union network). '
       +'Media layers use the relaxed admission rule. Published figures use the credit-union network.')
    : cfg==='holistic'
    ? ('All-bills network: '+(HOLMETA?HOLMETA.pairs.toLocaleString():'?')+' member pairs, 117th-119th, every bill type. '
       +'Cosponsorship is all-bills; the press, committee-affinity and social layers remain CU-scoped, '
       +'and cross-party damping still uses the CU-defined bipartisan prior. Published figures use the credit-union network.')
    : '';
  if(typeof refresh==='function')refresh();
  // The drawn route was computed on the OLD adjacency and fitCam framed it against the OLD
  // positions; leaving it up would slide a stale path across the canvas and off screen.
  if(route)noRoute();
  const _nbx=baseX(),_nby=baseY();
  if(layout==='area'){setLayout('area',true,600);}          // PE changed -> respace the embedding
  else if(layout==='committee'||(_nbx===_pbx&&_nby===_pby)){draw();}   // committee positions are
  // network-independent; identical array refs mean this bundle carries no layout for the new config
  else{tweenTo(_nbx,_nby,600);}
}
if(Object.keys(NETS).some(k=>{const a=netAdj(k);return NETS[k].sfx&&a&&a.length;})){
  document.getElementById('netcfg').style.display='';
  document.querySelectorAll('#netseg button').forEach(b=>b.onclick=()=>setNetConfig(b.dataset.net));
}
document.querySelectorAll('#ltog button').forEach(b=>b.onclick=()=>setLayout(b.dataset.l));
// ---- Console View popover wiring (chrome; the moved controls keep their ids + handlers) ----
{const vb=document.getElementById('viewbtn'),vp=document.getElementById('viewpanel');
 const vclose=()=>{vp.classList.remove('open');vb.setAttribute('aria-expanded','false');};
 vb.onclick=e=>{e.stopPropagation();const o=vp.classList.toggle('open');vb.setAttribute('aria-expanded',o?'true':'false');};
 document.addEventListener('click',e=>{if(vp.classList.contains('open')&&!vp.contains(e.target)&&e.target!==vb)vclose();});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')vclose();});}
// keyboard activation for the target/allocation rows (they carry tabindex=0; this makes the
// repaired focus ring OPERATIVE rather than decorative)
document.addEventListener('keydown',e=>{
  if((e.key==='Enter'||e.key===' ')&&e.target.classList&&(e.target.classList.contains('tgt')||e.target.classList.contains('oa'))){
    e.preventDefault();e.target.click();}});
document.querySelectorAll('#rtog button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#rtog button').forEach(x=>x.classList.remove('on'));b.classList.add('on');rmode=b.dataset.r;refresh();});
document.getElementById('search').onchange=e=>{const i=DATA.name.indexOf(e.target.value);if(i>=0)selectMember(i);};
cusl.oninput=runScenario;cosl.oninput=runScenario;
document.getElementById('pathbtn').onclick=runPath;
document.getElementById('dropsearch').onchange=applyShocks;
document.querySelectorAll('#execseg button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#execseg button').forEach(x=>x.classList.remove('on'));b.classList.add('on');execMode=b.dataset.e;applyShocks();});
document.getElementById('backbtn').onclick=backToTargets;
document.querySelectorAll('#pmode button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#pmode button').forEach(x=>x.classList.remove('on'));b.classList.add('on');pmodeVal=b.dataset.p;
  const lab=document.getElementById('plabel'),inp=document.getElementById('tgtsearch');
  inp.style.display='';
  if(pmodeVal==='best'){lab.textContent='find the best sponsor to reach:';inp.placeholder='target vote…';}
  else if(pmodeVal==='multi'){lab.textContent='reach targets (from this member):';inp.placeholder='target1, target2, …';}
  else if(pmodeVal==='budget'){lab.textContent='budget $k from this member (diversified recruits):';inp.placeholder='e.g. 300';}
  else if(pmodeVal==='cascade'){lab.innerHTML='cascade — pick a member; the map colors by who activates:';inp.style.display='none';}
  else{lab.innerHTML='bill sponsor <b id=seednm style=color:#16a34a></b> → reach target:';inp.placeholder='target member…';if(sel>=0)document.getElementById('seednm').textContent=DATA.name[sel].split(' ').slice(-1)[0];}
  document.getElementById('cascsub').style.display=(pmodeVal==='cascade')?'inline-flex':'none';   // sub-toggle only in cascade mode
  if(pmodeVal==='cascade'){cascSub=(mode==='flip')?'flip':(cascSub==='flip'?'who':cascSub);   // Persuade -> default to the Flip-route sub-mode
    document.querySelectorAll('#cascsub button').forEach(x=>x.classList.toggle('on',x.dataset.cs===cascSub));}
  document.getElementById('pathout').innerHTML='';noRoute();
  if(pmodeVal==='cascade'){if(sel>=0){fit();runCascadeMode();}else{document.getElementById('pathout').innerHTML='<span style=color:#f59e0b>Select a member first — it seeds the cascade.</span>';draw();}}
  else{cascadeExit();draw();}});
document.querySelectorAll('#cascsub button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#cascsub button').forEach(x=>x.classList.remove('on'));b.classList.add('on');cascSub=b.dataset.cs;
  if(sel>=0)runCascadeMode();else document.getElementById('pathout').innerHTML='<span style=color:#f59e0b>Select a member first.</span>';});
addEventListener('resize',resize);
renderSpeechCoverage();configureIssueNetwork();legend();optCard();renderPosture();resize();refresh();
try{const _cp=new URLSearchParams(location.search).get('coal');if(_cp)loadCoalition(_cp.split(',').map(s=>DATA.name.indexOf(s.trim())));}catch(e){}   // ?coal=Name1,Name2 -> load a Member-Desk coalition group
</script></body></html>
