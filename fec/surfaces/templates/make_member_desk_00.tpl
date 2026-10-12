<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
__HONORS_GENERATION_META__
<title>Melange — Member Desk</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 208'><path d='M108 20L64 64L28 104L64 144L20 188M64 64L100 104L64 144' fill='none' stroke='%23EECD5C' stroke-width='24' stroke-linejoin='miter' stroke-miterlimit='6'/></svg>">
<style>
:root{color-scheme:dark;--bg:#1D1912;--panel:#282113;--panel2:#2E2514;--line:#433317;--line2:#564019;--ink:#F3F3E6;--muted:#A49D8A;--faint:#9D9784;--blue:#D2A63C;--green:#16a34a;--ui-display:"Century Gothic","Tw Cen MT",Futura,"Avenir Next","Segoe UI",sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,Menlo,Consolas,monospace}
a{color:#EECD5C;text-decoration:none}a:hover{text-decoration:underline}
/* accessibility: visible keyboard focus everywhere */
button,select,input,a,.tgt,.sugg,.chip,summary,tbody tr{outline:none}
button:focus-visible,select:focus-visible,input:focus-visible,a:focus-visible,summary:focus-visible,.chip:focus-visible,tbody tr:focus-visible{outline:2px solid var(--blue);outline-offset:2px;border-radius:4px}
button,select,.chip,summary,.seg button{transition:background .12s,border-color .12s,color .12s}
select:hover,input:hover{border-color:#6B4E1F}
header{padding:11px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;background:var(--panel)}
.brand .kick{font-family:var(--ui-display);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#D2A63C;font-weight:700}
.brand h1{font-family:var(--ui-display);font-size:16px;font-weight:700;text-transform:uppercase;letter-spacing:.16em;display:flex;align-items:center;gap:6px}
.brand .brand-glyph{flex:0 0 auto}
.topnav{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.seg{display:inline-flex;border:1px solid var(--line2);border-radius:6px;overflow:hidden}
.seg button{background:var(--bg);color:#A49D8A;border:0;padding:6px 12px;cursor:pointer;font-size:12px}
.seg button.on{background:var(--blue);color:#1D1912;font-weight:600}
.ghostbtn{background:var(--bg);color:#A49D8A;border:1px solid var(--line2);border-radius:6px;padding:6px 11px;cursor:pointer;font-size:12px;display:inline-block}
.ghostbtn:hover{border-color:var(--blue);color:#F3F3E6;text-decoration:none}
.netlink{border:1px solid var(--line2);border-radius:6px;padding:6px 11px;font-size:12px;color:#A49D8A}
/* back-to-dashboard: mirrors the .desklink treatment on the network dashboard so the trip is
   symmetric — you leave the dashboard on a bordered pill and you come back on the same pill. */
.deskhome{border:1px solid var(--line2);border-radius:6px;padding:7px 13px;font-size:12px;font-weight:600;color:#D2CDBC;background:var(--bg);white-space:nowrap;text-decoration:none}
.deskhome:hover{border-color:var(--blue);color:#F3F3E6;background:#3B2E16;text-decoration:none}
/* every member name is a real anchor to that member's profile (?m=NAME) */
.mlink{color:inherit;text-decoration:none;border-bottom:1px dotted #6B4E1F}
.mlink:hover{color:#F3F3E6;border-bottom-color:#EECD5C;text-decoration:none}
.mlink:focus-visible{outline:2px solid var(--blue);outline-offset:2px;border-radius:3px}
.view{padding:14px 18px;max-width:1280px;margin:0 auto}
.view[hidden]{display:none}
/* ---- staff directory (2026-07-02) ---- */
.stfcard{border:1px solid var(--line,#433317);border-radius:10px;padding:8px 10px;margin:6px 0;background:rgba(255,255,255,.02)}
.stfcard.gold{border:2px solid #d4a017;box-shadow:0 0 6px rgba(212,160,23,.45)}
.stfhead{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.stfhead b{font-size:13.5px}
.staffer-people-link{font-size:13.5px;font-weight:700;color:inherit;text-decoration-thickness:1px;text-underline-offset:3px}
.staffer-people-link:hover{color:var(--gold,#eab308)}
.staffer-people-link:focus-visible{outline:2px solid var(--blue,#60a5fa);outline-offset:3px;border-radius:3px}
.stftitle{color:var(--faint);font-size:12px}
.stfstar{cursor:pointer;font-size:15px;color:#564019;user-select:none}
.stfstar.on{color:#eab308}
.stfmeta{font-size:12px;margin:3px 0;color:var(--faint)}
.stfmeta a{color:inherit}
.stfctl{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.stfctl .mini{font-size:11px;padding:2px 8px;border-radius:6px;border:1px solid var(--line,#433317);background:transparent;color:var(--faint);cursor:pointer}
.stfctl .mini.on{border-color:#d4a017;color:#d4a017}
.stfnote textarea{width:100%;min-height:44px;margin-top:5px;font-size:12px;background:rgba(0,0,0,.25);color:inherit;border:1px solid var(--line,#433317);border-radius:6px}
.stfmedia{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.stfmedia .chip{font-size:11px;border:1px solid var(--line,#433317);border-radius:10px;padding:1px 8px}
.stfmedia .chip .x{cursor:pointer;margin-left:5px;color:var(--faint)}
#contactbody .memgrp{margin:12px 0}
#contactbody .memgrp h3{cursor:pointer;margin:4px 0}
.stfsearch{width:100%;margin:4px 0 6px;padding:5px 8px;font-size:12px;background:rgba(0,0,0,.25);color:inherit;border:1px solid var(--line,#433317);border-radius:6px}
/* ranked */
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
input,select{background:var(--bg);color:var(--ink);border:1px solid var(--line2);border-radius:6px;padding:7px 10px;font-size:13px}
.search{position:relative}.search input{width:240px;padding-left:30px}.search svg{position:absolute;left:9px;top:8px;color:var(--faint)}
.toggle{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:12px}
.chips{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:10px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line2);border-radius:20px;padding:4px 11px;cursor:pointer;font-size:12px;color:#A49D8A}
.chip.on{background:#3B2E16;border-color:var(--blue);color:#F3F3E6}
.chip .dot{width:8px;height:8px;border-radius:50%}
.countline{color:var(--muted);font-size:12px;margin-bottom:8px;display:flex;justify-content:space-between}
.tablewrap{border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:680px}
th{text-align:left;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:7px 10px;border-bottom:1px solid var(--line);background:var(--panel2)}
td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
tr:last-child td{border-bottom:0}
tbody tr{cursor:pointer}tbody tr:hover{background:#2E2514}
.num{text-align:right;font-variant-numeric:tabular-nums}
.rk{font-weight:700;font-variant-numeric:tabular-nums}
.nm{font-weight:600}.dist{color:var(--muted);font-size:11px}
.tier{display:inline-flex;align-items:center;gap:6px;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:600}
.tier .d{width:7px;height:7px;border-radius:50%;background:currentColor}
.potential-tag{display:inline-flex;align-items:center;border:1px solid #34d39966;background:#34d3991f;color:#86efac;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:800;line-height:1.2;letter-spacing:.03em;vertical-align:middle;margin-left:6px;white-space:nowrap}
.spark{display:inline-flex;align-items:flex-end;gap:3px;height:24px}
.spark i{width:7px;border-radius:2px 2px 0 0;display:block;min-height:3px}
.scorecell{display:flex;align-items:center;gap:8px}
.sbar{flex:1;height:7px;background:#433317;border-radius:4px;overflow:hidden;min-width:60px}
.sbar i{display:block;height:100%;background:linear-gradient(90deg,#1f6feb,#34d399)}
.sval{font-variant-numeric:tabular-nums;font-weight:700;width:38px;text-align:right}
/* member */
.mhead{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:14px}
.mhead h2{font-size:24px;font-weight:800}
.mhead .meta{color:var(--muted);font-size:13px;margin-top:3px}
.verdict{display:inline-flex;align-items:center;gap:7px;border-radius:20px;padding:4px 12px;font-size:12px;font-weight:700;margin-bottom:8px}
.verdict .d{width:8px;height:8px;border-radius:50%;background:currentColor}
.figs{display:flex;gap:10px;flex-wrap:wrap}
.fig{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 15px;min-width:120px}
.fig .v{font-size:21px;font-weight:800;font-variant-numeric:tabular-nums}
.fig .l{color:var(--muted);font-size:11px;margin-top:2px}
.fig.accent .v{color:#34d399}
.districtbrief{background:linear-gradient(135deg,#2a2518,#211d15);border:1px solid #c2862a66;border-radius:11px;padding:14px 16px;margin-bottom:14px}
.districtbrief p{font-size:13.5px;line-height:1.65;color:var(--ink);margin:0 0 10px}
.districtbrief p:last-child{margin-bottom:0}
.gen{display:grid;grid-template-columns:1.3fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:880px){.gen{grid-template-columns:1fr}}
@media(max-width:640px){
  .view{padding:10px 11px}
  header{padding:9px 11px;gap:9px}h1{font-size:16px}
  .topnav{gap:7px;width:100%}
  .controls{gap:7px}
  .search{flex:1 1 100%}.search input{width:100%}
  .tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch;width:100%}
  .mhead{gap:10px}.figs{gap:7px}.fig{flex:1 1 calc(50% - 7px);min-width:0;padding:9px 12px}
  button,select,input,.ghostbtn,.seg button{min-height:40px}
  .povsel{min-height:34px}
}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px}
.eyebrow{font-family:var(--ui-display);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:700;margin-bottom:10px}
.sec{margin-bottom:12px}
.sec>summary{cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;
  font-family:var(--ui-display);text-transform:uppercase;letter-spacing:.14em;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:13px 15px;font-weight:700;font-size:14px}
.sec>summary::-webkit-details-marker{display:none}
.sec[open]>summary{border-radius:12px 12px 0 0;border-bottom:0}
.sec .body{background:var(--panel);border:1px solid var(--line);border-top:0;border-radius:0 0 12px 12px;padding:15px}
.sec>summary .chev{color:var(--faint);font-size:12px;transition:transform .15s}
.sec[open]>summary .chev{transform:rotate(90deg)}
/* nested group block: one step quieter than .sec - smaller, not uppercase, panel2 ground */
.sub{margin:0 0 10px}
.sub:last-child{margin-bottom:0}
.sub>summary{cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;
  font-family:var(--ui-display);letter-spacing:.05em;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:9px 12px;font-weight:700;font-size:12.5px}
.sub>summary::-webkit-details-marker{display:none}
.sub[open]>summary{border-radius:10px 10px 0 0;border-bottom:0}
.sub .subbody{background:var(--panel2);border:1px solid var(--line);border-top:0;border-radius:0 0 10px 10px;padding:12px}
.sub>summary .chev{color:var(--faint);font-size:11px;transition:transform .15s}
.sub[open]>summary .chev{transform:rotate(90deg)}
.mrow{display:flex;justify-content:space-between;padding:5px 0;font-size:12.5px;border-bottom:1px solid #433317;gap:10px}
.mrow:last-child{border-bottom:0}.mrow b{font-variant-numeric:tabular-nums;color:#F3F3E6}
.arealist{max-height:420px;overflow-y:auto}
.arow{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #433317;font-size:12px}
.arow:last-child{border-bottom:0}
.arow .aname{flex:1;color:#D2CDBC;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.arow .arank{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;min-width:62px;text-align:right}
.dgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
@media(max-width:560px){.dgrid{grid-template-columns:repeat(2,1fr)}}
.dcell{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px}
.dcell .v{font-size:16px;font-weight:800;font-variant-numeric:tabular-nums}.dcell .l{color:var(--muted);font-size:11px;margin-top:3px}
.bars{display:flex;flex-direction:column;gap:6px}
.barrow{display:grid;grid-template-columns:150px 1fr 46px;align-items:center;gap:9px;font-size:12px}
.barrow .t{color:#D2CDBC;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.barrow .track{height:8px;background:#433317;border-radius:4px;overflow:hidden}
.barrow .track i{display:block;height:100%}
.barrow .val{text-align:right;font-variant-numeric:tabular-nums;color:#F3F3E6}
.areasel{display:flex;gap:8px;align-items:center;margin-bottom:11px;flex-wrap:wrap}
.partner{display:grid;grid-template-columns:1fr 70px 1fr;align-items:center;gap:9px;font-size:12px;padding:4px 0;cursor:pointer;border-bottom:1px solid #433317}
.partner:hover{background:#2E2514}.partner .pn{font-weight:600}
.ev{border:1px solid var(--line);border-radius:10px;padding:11px 13px;margin-bottom:9px;border-left:3px solid var(--faint)}
.ev[data-pol="1"]{border-left-color:#34d399}.ev[data-pol="-1"]{border-left-color:#ef4444}.ev[data-pol="0"]{border-left-color:#9ca3af}
.ev-top{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:4px;gap:8px}
.ev-title{font-weight:600;display:block;margin-bottom:3px}
.ev-sig,.ev-note{font-size:11.5px;color:var(--muted);margin-top:3px}
.ev-basis{color:var(--faint)}
.cnt{color:var(--muted);font-size:11px;margin-bottom:8px}
.billrow{border:1px solid var(--line);border-radius:9px;padding:10px 12px;margin-bottom:8px}
.billtop{display:flex;align-items:center;gap:9px;margin-bottom:3px}
.rolebadge{font-size:9.5px;font-weight:700;border-radius:5px;padding:2px 7px;text-transform:uppercase;letter-spacing:.03em}
.billno{font-weight:700;font-variant-numeric:tabular-nums}
.billcong{color:var(--faint);font-size:11px;margin-left:auto}
.billtitle{font-weight:600;font-size:13px;margin-bottom:3px}
.billmeta{color:var(--muted);font-size:11.5px}
.billsum{color:var(--muted);font-size:11.5px;margin-top:5px;line-height:1.5}
/* bill repository */
.billrepo{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media(max-width:900px){.billrepo{grid-template-columns:1fr}}
.billcard{background:var(--panel);border:1px solid var(--line);border-radius:11px;overflow:hidden}
.billcard[open]{border-color:#6B4E1F}
.billcard>summary{list-style:none;cursor:pointer;padding:13px 14px;display:flex;gap:10px;align-items:flex-start}
.billcard>summary::-webkit-details-marker{display:none}
.billcard .bbody{border-top:1px solid var(--line);padding:13px 14px}
.billhead{flex:1;min-width:0}.billhead .billtitle{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pinbtn{border:1px solid var(--line2);background:var(--bg);color:var(--muted);border-radius:7px;padding:4px 8px;cursor:pointer;white-space:nowrap}
.pinbtn.on{color:#fbbf24;border-color:#fbbf2466;background:#fbbf2412}
.livebadge{font-size:9px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;border-radius:999px;padding:2px 7px;color:#86efac;background:#16a34a1f;border:1px solid #16a34a55}
.livebadge.stale{color:#fbbf24;background:#f59e0b14;border-color:#f59e0b55}
.repo-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
@media(max-width:700px){.repo-cols{grid-template-columns:1fr}}
.repo-block{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px}
.repo-list{font-size:11.5px;color:#D2CDBC;line-height:1.65}
.billlobby{margin-top:12px}.billlobby-head{display:flex;justify-content:space-between;gap:10px;align-items:baseline;margin-bottom:7px}
.billlobby-grid{display:grid;grid-template-columns:1.05fr 1.35fr 1fr 1fr;gap:12px}
@media(max-width:1250px){.billlobby-grid{grid-template-columns:1fr 1fr}}
@media(max-width:900px){.billlobby-grid{grid-template-columns:1fr}}
.billlobby-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}
.billlobby-chip{display:inline-grid;grid-template-columns:8px auto auto;align-items:center;gap:6px;background:#282113;border:1px solid var(--line2);border-radius:999px;padding:4px 8px;font-size:11px;line-height:1.2}
.billlobby-chip i{width:8px;height:8px;border-radius:50%}.billlobby-chip small{grid-column:2;color:var(--faint);font-size:9.5px}.billlobby-chip b{grid-row:1/3;grid-column:3;color:#D2CDBC;font-variant-numeric:tabular-nums}
.billlobby-row{padding:6px 0;border-bottom:1px solid #433317;font-size:11.5px;line-height:1.45}.billlobby-row:last-child{border-bottom:0}
.billlobby-row .detail{color:var(--faint);font-size:10.5px;margin-top:2px}.billlobby-count{color:#86efac;font-weight:700;font-variant-numeric:tabular-nums}
.billlobby-note{color:var(--faint);font-size:10.5px;line-height:1.5;margin-top:7px}
.nextscore{font-variant-numeric:tabular-nums;color:#86efac;font-weight:700}
.billnote{width:100%;min-height:58px;margin-top:6px;background:var(--bg);color:var(--ink);border:1px solid var(--line2);border-radius:7px;padding:8px;resize:vertical}
.snaprow{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 8px;border-bottom:1px solid #433317;font-size:12px}
.snaprow:last-child{border-bottom:0}.snapacts{display:flex;gap:6px}.snapacts button{font-size:10px;padding:3px 7px}
/* graded-appearance passage: the CU keyword window from cu_passages.py (display-only) */
.ev-snip{color:var(--muted);font-size:11.5px;margin-top:6px;line-height:1.55;padding:6px 10px;
  border-left:2px solid var(--line);border-radius:4px;background:rgba(255,255,255,.02)}
.ev-grade{margin-top:5px}
.empty{color:var(--faint);font-size:12.5px;padding:6px 0;font-style:italic}
.notebox{width:100%;min-height:90px;background:var(--bg);color:var(--ink);border:1px solid var(--line2);border-radius:8px;padding:10px;font:13px/1.5 inherit;resize:vertical}
.filelist{display:flex;flex-direction:column;gap:5px;margin:8px 0}
.fileitem{display:flex;justify-content:space-between;align-items:center;background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font-size:12px}
.btn{background:var(--blue);color:#1D1912;border:0;border-radius:6px;padding:7px 13px;cursor:pointer;font-size:12px;margin-top:8px;margin-right:6px}
.btn.ghost{background:var(--bg);color:#A49D8A;border:1px solid var(--line2)}
.del{color:#f87171;cursor:pointer;font-size:11px}
.savednote{white-space:pre-wrap;font-size:13px;line-height:1.55;color:#D2CDBC}
.backbtn{background:var(--bg);color:#A49D8A;border:1px solid var(--line2);border-radius:6px;padding:6px 11px;cursor:pointer;font-size:12px}
/* coalition builder */
.cbgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:880px){.cbgrid{grid-template-columns:1fr}}
.coalchip{display:inline-flex;align-items:center;gap:7px;background:#3B2E16;border:1px solid var(--line2);border-radius:20px;padding:4px 8px 4px 11px;margin:3px;font-size:12px}
.coalchip .x{cursor:pointer;color:#f87171;font-weight:700}
.sugg{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:7px 9px;border-radius:8px;cursor:pointer;font-size:12.5px;border-bottom:1px solid #433317}
.sugg:hover{background:#2E2514}.sugg .add{color:#34d399;font-weight:700}
.legendrow{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--muted);margin-top:8px}
.legendrow span{display:inline-flex;align-items:center;gap:5px}
.pdot{width:9px;height:9px;border-radius:50%}
/* investment efficiency */
.methnote{margin:-2px 0 12px;max-width:760px}
.methnote>summary{cursor:pointer;list-style:none;color:var(--blue);font-size:12px;display:inline-flex;align-items:center;gap:5px}
.methnote>summary::-webkit-details-marker{display:none}
.methnote>summary::before{content:'ⓘ';font-size:11px}
.methnote[open]>summary{margin-bottom:6px}
.methbody{font-size:11.5px;color:var(--muted);line-height:1.7;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.effcell{display:flex;align-items:center;gap:8px}
.effval{font-variant-numeric:tabular-nums;font-weight:700;width:42px;text-align:right}
.effbar{position:relative;height:8px;background:#433317;border-radius:4px;flex:1;min-width:70px}
.effbar .effmid{position:absolute;left:50%;top:-1px;bottom:-1px;width:1px;background:#564019}
.effbar i{position:absolute;top:0;bottom:0;display:block;border-radius:3px}
/* engagement tracker */
.trackgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
@media(max-width:640px){.trackgrid{grid-template-columns:1fr}}
.trackgrid label{display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--muted)}
.trackgrid input,.trackgrid select{font-size:13px;color:var(--ink)}
/* print / PDF: just the member one-pager, ink-friendly */
@media print{
  header,#view-ranked,#view-efficiency,#view-coalition,#view-bills,#backbtn,#printbtn,.netlink,.deskhome,#app-network{display:none!important}
  html,body{background:#fff!important;color:#1D1912!important}
  .view{max-width:none;margin:0;padding:0}
  details.sec>.body,details.sub>.subbody{display:block!important}
  .sec[open]>summary,.sec>summary,.sub[open]>summary,.sub>summary{border:1px solid #D2CDBC!important}
  .card,.districtbrief,.sec>summary,.sec .body,.sub>summary,.sub .subbody,.fig,.dcell,.billrow,.ev,.notebox,input,select{background:#fff!important;color:#1D1912!important;border-color:#D2CDBC!important;box-shadow:none!important}
  .eyebrow,.nm,.dist,.meta,.l,.billmeta,.billsum,.ev-top,.ev-basis,.ev-snip,.cnt,.mrow span,.trackgrid label{color:#433317!important}
  .ev-snip{background:#fff!important;border-left-color:#D2CDBC!important}
  h2,.nm,.billtitle,.dcell .v,.fig .v,b{color:#1D1912!important}
  a{color:#1D1912!important;text-decoration:none}.mlink{border-bottom:0!important}
  svg text{fill:#433317!important} svg path[stroke="#3B2E16"],svg line{stroke:#D2CDBC!important}
}
.ev-ov{display:flex;align-items:center;gap:8px;margin-top:7px;flex-wrap:wrap}
.ovlabel{font-size:11px;color:var(--faint)}
.povsel{font-size:11px;padding:2px 7px;border-radius:6px;background:#2E2514;color:#F3F3E6;border:1px solid #564019;cursor:pointer}
.ovtag{font-size:10px;font-weight:600;padding:2px 8px;border-radius:999px;letter-spacing:.02em}
/* ---- Live desk (ratified 2026-08-24) ---- */
.seg button:not(.on):hover{background:#3B2E16;color:#F3F3E6}
.seg button.on:hover{background:#EECD5C}
/* Sticky ranked-table headers, >=1000px only: .tablewrap{overflow-x:auto} forces
   overflow-y:auto too, creating a scroll container that defeats viewport sticky; clip removes
   the container but also horizontal panning, so below 1000px (where the 680px-min table needs
   to pan) the shipped behaviour is kept. */
@media(min-width:1000px){
  .tablewrap{overflow-x:clip;overflow-y:visible}
  thead th{position:sticky;top:0;z-index:5;box-shadow:0 1px 0 var(--line)}
}
/* The member profile arrives as a right-edge slide-over ABOVE the still-live ranked list.
   96%-opaque panel; NO backdrop-filter anywhere on this page (measured 2,298ms parse block). */
#view-member:not([hidden]){position:fixed;z-index:70;top:0;right:0;bottom:0;width:min(680px,100vw);margin:0;padding:14px 18px 40px;overflow:auto;overscroll-behavior:contain;background:rgba(40,33,19,.96);border-left:1px solid var(--line2);box-shadow:inset 0 1px 0 rgba(243,243,230,.055),0 18px 48px rgba(18,14,7,.58);animation:mel-slidein 280ms cubic-bezier(.22,.61,.36,1)}
@keyframes mel-slidein{from{transform:translate3d(100%,0,0)}to{transform:translate3d(0,0,0)}}

/* ============================================================================
   VARIANT C - SLIDE-DOWN SHEET
   Full-width sheet dropping from the top edge instead of sliding in from the
   right. The left border becomes a bottom border; the reading column is capped
   so body text is not full-bleed on a wide monitor.
   max-width:none is required: .view{max-width:1280px} still applies to this
   element and would otherwise cap the sheet at 1280px and left-anchor it
   (left/right/width become over-constrained, so right is dropped).
   ========================================================================= */
#view-member:not([hidden]){top:0;left:0;right:0;bottom:0;width:auto;max-width:none;border-left:0;border-bottom:1px solid var(--line2);animation:mel-slidedown 280ms cubic-bezier(.22,.61,.36,1)}

/* transform only - same 280ms and same curve as the shipped slide-in */
@keyframes mel-slidedown{from{transform:translate3d(0,-100%,0)}to{transform:translate3d(0,0,0)}}

/* Keep the measure readable on a wide monitor. margin-left/right longhands are
   used so the element's vertical margins are left exactly as shipped. */
#memberbody{max-width:1040px;margin-left:auto;margin-right:auto}

/* Phones: no measured column, no centring - identical to the shipped panel. */
@media(max-width:640px){
  #memberbody{max-width:none;margin-left:0;margin-right:0}
}

.mel-scrim{position:fixed;inset:0;z-index:65;background:rgba(29,25,18,.48);opacity:0;visibility:hidden;transition:opacity 260ms linear,visibility 0s linear 260ms}
.mel-scrim[data-open]{opacity:1;visibility:visible;transition:opacity 260ms linear,visibility 0s}
body.mel-over-open{overflow:hidden}
#mel-profiles-note{position:fixed;right:14px;bottom:14px;z-index:90;background:rgba(40,33,19,.96);border:1px solid var(--line2);border-radius:8px;padding:9px 13px;font-size:12px;color:#D2CDBC}
#rows tr.mel-enter{animation:mel-fade 190ms ease-out both}
@keyframes mel-fade{from{opacity:0}to{opacity:1}}
/* PRINT: the shipped ink-friendly one-pager hides #view-ranked and prints the member view; a
   fixed, animated panel would print BLANK without this flatten. */
@media print{
  .mel-scrim{display:none!important}
  #view-member:not([hidden]){position:static!important;width:auto!important;padding:0!important;overflow:visible!important;border:0!important;box-shadow:none!important;background:#fff!important;animation:none!important}
  body.mel-over-open{overflow:visible!important}
  #memberbody{max-width:none!important;margin-left:0!important;margin-right:0!important}
}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition-duration:1ms!important;animation-duration:1ms!important}}
</style></head><body>
<header>
  <div class=brand><div class=kick>Melange</div><h1><svg class=brand-glyph width="19" height="31" viewBox="0 0 128 208" aria-hidden="true"><path d="M108 20 L64 64 L28 104 L64 144 L20 188 M64 64 L100 104 L64 144" fill="none" stroke="#EECD5C" stroke-width="19" stroke-linejoin="miter" stroke-miterlimit="6"/></svg>Melange — Member Desk</h1></div>
  <div class=topnav>
    <span class=seg id=viewseg><button data-v=ranked class=on>Members</button><button data-v=bills>Bills</button><button data-v=layers>Spending efficiency</button><button data-v=efficiency>Investment efficiency</button><button data-v=coalition>Coalition builder</button><button data-v=contact>Contact log</button></span>
    <a class="netlink deskhome" id=deskhome href="Moneyball Dashboard.html">◀ Main dashboard</a>
    <a class=deskhome id=deskindustry href="lobby-network.html">Industry ▸</a>
    <button class=ghostbtn id=exportbtn>⭳ Export notes</button>
    <label class=ghostbtn for=importfile>⭱ Import notes</label><input id=importfile type=file accept="application/json,.json" hidden>
    <button class=ghostbtn id=povexport title="Download your manual article-stance calls (press_overrides_from_dashboard.json). Merge into the ledger with: python fec/apply_press_overrides.py --merge-only — the next chain run applies it. (The legacy no-flag re-flow is retired: frozen 06-30 basis.)">⭳ Export stance overrides</button>
    <label class=ghostbtn for=povimport>⭱ Import overrides</label><input id=povimport type=file accept="application/json,.json" hidden>
    <span id=povhint style="color:var(--faint);font-size:11px"></span>
  </div>
</header>
<div id=integritybar style="padding:7px 18px;background:#282113;border-bottom:1px solid var(--line);font-size:11px;color:var(--muted)"></div>

<section id=view-ranked class=view>
  <div class=controls>
    <div class=search><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.3-4.3"></path></svg><input id=q aria-label="Search member or district" placeholder="Search member or district…" autocomplete=off></div>
    <span class=seg id=chamber><button data-v=all class=on>All</button><button data-v=House>House</button><button data-v=Senate>Senate</button></span>
    <select id=area><option value="">All legislative areas</option></select>
    <select id=stancefilter><option value="">All stances</option></select>
    <select id=priorityfilter aria-label="Filter overall priority" hidden><option value="">All priority tiers</option><option value=1>Tier 1 · Champion</option><option value=2>Tier 2</option><option value=3>Tier 3</option><option value=0>Outside priority tiers</option></select>
    <select id=sort><option value=rank>Sort: Rank</option><option value=potential>Sort: District opportunity</option><option value=potential_adjusted>Sort: Structurally adjusted potential</option><option value=tier>Sort: Tier</option><option value=score>Sort: Score</option><option value=invested>Sort: Invested ($)</option><option value=name>Sort: Name</option></select>
    <label class=toggle><input type=checkbox id=fundedOnly aria-label="Show funded members only"> Funded only</label>
    <label class=toggle><input type=checkbox id=potentialOnly aria-label="Show Demographic Potential members only"> Demographic Potential only</label>
  </div>
  <div class=countline><span id=count></span><span id=stancehint style=color:var(--faint)>Click any member for the full profile</span></div>
  <div class=tablewrap><table><thead><tr>
    <th class=num style=width:80px id=th-rank>Rank</th><th>Member</th><th style=width:180px id=th-stance>Stance</th>
    <th style=width:96px id=th-profile>Profile</th><th style=width:150px id=th-score>Score</th>
  </tr></thead><tbody id=rows></tbody></table></div>
</section>

<section id=view-member class=view hidden><div id=memberbody></div></section>
<section id=view-contact class=view hidden><div id=contactbody></div></section>

<section id=view-bills class=view hidden>
  <h2 style=font-size:20px;font-weight:800;margin-bottom:4px>Bills</h2>
  <div style=color:var(--muted);margin-bottom:12px>Track the full CU bill universe, inspect sponsors and amendments, and rank the next most suitable coalition member by the incremental expected reach they add on the selected bill's issue-adaptive network.</div>
  <div class=controls>
    <div class=search><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.3-4.3"></path></svg><input id=billq aria-label="Search bills" placeholder="Bill, title, sponsor..." autocomplete=off></div>
    <select id=billarea><option value="">All legislative areas</option></select>
    <select id=billcongress><option value="">All Congresses</option></select>
    <label class=toggle><input type=checkbox id=billtracked aria-label="Show pinned legislation only"> Pinned only</label>
  </div>
  <div class=countline><span id=billcount></span><span id=billfresh style=color:var(--faint)></span></div>
  <div id=billrepo class=billrepo></div>
  <div style="text-align:center;margin-top:12px"><button class=ghostbtn id=billmore style=display:none>Load more bills</button></div>
</section>

<section id=view-layers class=view hidden>
  <details class=sec open><summary>Spending efficiency<span class=chev>▸</span></summary><div class=body>
    <div class=eyebrow>Best value</div><div id=layers-underpriced></div>
    <div class=eyebrow style=margin-top:15px>Crowded field</div><div id=layers-contested></div>
  </div></details>
  <details class=sec open><summary>Most accessible<span class=chev>▸</span></summary><div class=body>
    <div class=eyebrow>Top 15 easy targets</div><div id=layers-easy></div>
  </div></details>
  <details class=methnote><summary>How to read this</summary><div class=methbody>
    <div>Percentiles: 100 = highest in Congress. Labels mark the top and bottom quarter of members.</div>
    <div style=margin-top:6px>PAC dollars and lobbying attention are different tools and are never added together. Crowding shows how much other financial-industry activity surrounds a member across the expanded 1,095-organization universe.</div>
    <div style=margin-top:6px>Threat-board members are left out as adversaries. This is a planning aid only and does not change any member's official ranking. Hover over a row to see what drives it.</div>
  </div></details>
</section>

<section id=view-coalition class=view hidden>
  <h2 style=font-size:20px;font-weight:800;margin-bottom:4px>Coalition builder</h2>
  <div style=color:var(--muted);margin-bottom:12px>Add cosponsors to a bill, then the cosponsorship network suggests the most likely next cosponsors. Bias the search toward a legislative area if you like.
    <b style=color:var(--ink)>Reach</b> ranks by the members each candidate newly brings in, net of whoever your set already reaches — the widest-net answer, and on this network it favours the handful of members with the largest cosponsorship books. <b style=color:var(--ink)>Coalition-specific</b> subtracts what a candidate gives to any comparable set of this size, so the members everybody cosponsors with stop crowding out the ones tied to <i>yours</i>.</div>
  <div class=areasel>
    <div class=search><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.3-4.3"></path></svg><input id=cbsearch aria-label="Add a cosponsor" placeholder="Add a cosponsor…" list=allnames autocomplete=off></div>
    <datalist id=allnames></datalist>
    <select id=cbarea><option value="">No area bias</option></select>
    <select id=cbparty><option value="">All parties</option><option value=Democrat>Democrats</option><option value=Republican>Republicans</option><option value=Independent>Independents</option></select>
    <select id=cbcommittee><option value="">All committees</option></select>
    <span class=seg id=cbbasis aria-label="Suggestion ranking basis"><button data-b=cosponsor class=on title="Who actually signs onto other members' bills — directed sponsor→cosponsor ties plus cosponsorship propensity. Sponsorship pull is deliberately not scored.">Cosponsor propensity</button><button data-b=reach>Reach</button><button data-b=specific>Coalition-specific</button><button data-b=underpriced title="Capability the market has not already bid up, divided by access price">Underpriced</button></span>
    <button class=ghostbtn id=cbsave>Save snapshot</button>
    <button class=ghostbtn id=cbclear>Clear</button>
  </div>
  <div class=cbgrid>
    <div class=card><div class=eyebrow>Bill cosponsors <span id=cbcount style=color:var(--faint)></span></div><div id=cblist></div><div id=cbstats style=margin-top:10px></div>
      <button class=btn id=cbnet style=display:none>View coalition in network ↗</button></div>
    <div class=card><div class=eyebrow>Suggested next cosponsors</div><div id=cbsugg></div></div>
  </div>
  <div class=card style=margin-top:12px><div class=eyebrow>Saved snapshots</div><div id=cbsnapshots></div></div>
</section>

<section id=view-efficiency class=view hidden>
  <h2 style=font-size:20px;font-weight:800;margin-bottom:4px>Investment efficiency</h2>
  <div style=color:var(--muted);margin-bottom:10px;max-width:760px>Where the next credit-union dollar moves the most. Sorted by <b style=color:#34d399>ROI</b> — expected weighted flips gained from the next $10k.</div>
  <div class=controls>
    <div class=search><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.3-4.3"></path></svg><input id=effq aria-label="Search efficiency table" placeholder="Search member…" autocomplete=off></div>
    <span class=seg id=effch><button data-v=all class=on>All</button><button data-v=House>House</button><button data-v=Senate>Senate</button></span>
    <select id=effsort>
      <option value=roi>Sort: ROI · flips per $10k</option>
      <option value=value>Sort: Undervalued (value)</option>
      <option value=gap>Sort: Funding gap</option>
      <option value=invested>Sort: Invested (high→low)</option>
      <option value=rank>Sort: Overall rank</option>
    </select>
  </div>
  <div class=countline><span id=effcount></span><span style=color:var(--faint)>Click any member for the full profile</span></div>
  <div class=tablewrap><table><thead><tr>
    <th class=num style=width:70px id=th-eff-rank>Rank</th><th>Member</th>
    <th class=num style=width:82px>Invested</th><th class=num style=width:88px>Suggested</th>
    <th class=num style=width:82px>Gap</th><th class=num style=width:58px>Value</th>
    <th style=width:160px>ROI · flips / $10k</th>
  </tr></thead><tbody id=effrows></tbody></table></div>
</section>

<script>
const D=__BUNDLE__;
const DSPLIT=__DSPLIT__;   // 1 = profile-heavy fields arrive via cu-member-desk-profiles.js
// DEMO_POTENTIAL mode (D-034).  demographic_potential.metadata() writes
// footprint_employment_lq_min ONLY under DEMO_POTENTIAL=footprint -- growth_room
// metadata has to stay byte-identical to before the dial existed -- so the key's
// presence IS the mode, and its value is the qualification gate the card states.
// Under footprint the sector `signals` list is empty by construction and the
// reach-residual structural companion is not_applicable_for_mode, so the two
// displays that read them need a footprint branch rather than a blank.
const DPFOOT=(D.demographic_potential_meta||{}).footprint_employment_lq_min??null;
const DPMODE=DPFOOT==null?'growth_room':'footprint';
if(DPMODE==='footprint'){const _adjOpt=document.querySelector('#sort option[value=potential_adjusted]');
  if(_adjOpt)_adjOpt.textContent='Sort: District opportunity (no structural companion)';}
const RMQ=matchMedia('(prefers-reduced-motion: reduce)').matches;
if(!D.efficiency_layers||D.efficiency_layers.schema!=='efficiency-layers-v2')throw new Error('efficiency-layers-v2 hard input missing');
if(!D.efficiency_layers_display||!D.efficiency_layers_display.members)throw new Error('efficiency percentile display payload missing');
const M=D.members, CATS=D.cats, DEF=new Set(D.defensive), COAL=D.coalitions, WT=D.weights;
const EFF=D.efficiency_layers,EFFD=D.efficiency_layers_display;
const EXTRA=D.art_extra_cats, DEMO_AXES=D.demo_axes;
const byName={}; M.forEach((m,i)=>byName[m.name]=i);
const SD=(D.integrity&&D.integrity.source_display)||{};
const excluded=Object.entries(SD).filter(([k])=>/(excluded|routed)/.test(k)).reduce((n,[,v])=>n+(Number(v)||0),0);
const generated=(D.freshness&&D.freshness.generated_at)||'unknown';
document.getElementById('integritybar').innerHTML=`<b style="color:var(--ink)">Data freshness:</b> generated ${esc(generated)} · <b style="color:var(--ink)">Exclusion accounting:</b> ${excluded.toLocaleString()} source rows explicitly excluded or routed. Open a member's media sections for item-level reasons.`;
// cosponsor adjacency for builder
const ADJ={},ADJPROB={}; D.adj.forEach(([a,b,w,p])=>{const probability=p==null?w/(w+6):p;(ADJ[a]=ADJ[a]||[]).push([b,probability]);(ADJ[b]=ADJ[b]||[]).push([a,probability]);(ADJPROB[a]=ADJPROB[a]||{})[b]=probability;(ADJPROB[b]=ADJPROB[b]||{})[a]=probability;});
// DIRECTED cosponsor graph, sponsor -> cosponsor (2026-08-03). ADJ above is the SYMMETRISED graph
// and it is what every basis ranked on until today. Symmetrising merges two opposite assets:
//   out-edge (u SPONSORED, v signed)  = u's pull as a SPONSOR
//   in-edge  (u sponsored, V SIGNED)  = v's willingness to COSPONSOR
// The pathfinder is handed a sponsor and asked who to recruit as cosponsors, so only the in-side
// is the asset. Measured on the live graph the top-20 symmetrised hubs draw 92.8% of their degree
// from OUT-edges, i.e. the builder's whole hub set is "people whose own bills attract signatures".
// DIN[v] = who v has signed on with; DOUT[s] = who has signed s's bills.
const DIN={},DOUT={};(D.dadj||[]).forEach(([s,v,w,p])=>{const probability=p==null?w/(w+6):p;
  (DIN[v]=DIN[v]||[]).push([s,probability]);(DOUT[s]=DOUT[s]||[]).push([v,probability]);});
const HAS_DIR=(D.dadj||[]).length>0;
// Shared, interactive-only domain/chair policy. Production ranks and allocation
// artifacts are unchanged; this layer reconfigures the live IC/LT workbench.
const IP=D.influence_policy||{domains:[],roles:[],role_priors:{'-':{source_shock:1,target_resistance:1}}};
const ROLEPRIORS=IP.role_priors||{'-':{label:'Member',source_shock:1,target_resistance:1}};
function domainOf(area){const i=CATS.indexOf(area);return i>=0?(IP.domains[i]||null):null;}
function roleCodeAt(i,area){const ai=CATS.indexOf(area),row=(IP.roles||[])[i]||'';return ai>=0?(row[ai]||'-'):'-';}
function rolePriorAt(i,area){return ROLEPRIORS[roleCodeAt(i,area)]||ROLEPRIORS['-']||{label:'Member',source_shock:1,target_resistance:1};}
function adaptiveEdgeProbability(base,u,v,area){
  if(!(base>0))return 0;
  if(!area)return Math.max(0,Math.min(1,base));
  const d=domainOf(area)||{edge_scale:1,ic_edge_scale:1},source=rolePriorAt(u,area),target=rolePriorAt(v,area);
  const p=Math.max(0.000001,Math.min(0.999999,base||0)),odds=p/(1-p);
  const factor=(d.edge_scale||1)*(d.ic_edge_scale||1)*(source.source_shock||1)/(target.target_resistance||1);
  return Math.max(0,Math.min(.995,(odds*factor)/(1+odds*factor)));
}
function committeeBase(name){return String(name||'').replace(/\s+-\s+.*$/,'').replace(/\s+\(full\).*$/,'').trim();}
function memberHasCommittee(m,committee){return !committee||(m.committees||[]).some(c=>committeeBase(c.name)===committee);}
const STCOL={C:'#16a34a',F:'#2dd4bf',A:'#86efac',H:'#38bdf8',P:'#7dd3fc',G:'#bae6fd',T:'#9ca3af',N:'#8b949e',L:'#fde3c0',S:'#f59e0b',K:'#f97316',O:'#ef4444','-':'#3a414b'};
const PTCOL={Democrat:'#3b82f6',Republican:'#ef4444',Independent:'#a855f7','':'#6b7280'};
const PILL={press:{c:'#3274a1',l:'Press sentiment (diminished)',w:WT.press},comm:{c:'#8458ae',l:'Influence',w:WT.comm},leg:{c:'#0e7c66',l:'Legislative action',w:WT.leg},demo:{c:'#c2862a',l:'Demographic',w:WT.demo}};
const $=s=>document.querySelector(s);
const money=n=>(n&&n>0)?'$'+Math.round(n).toLocaleString():'—';
const f2=v=>v==null?'—':Number(v).toFixed(2);
const f3=v=>v==null?'—':Number(v).toFixed(3);
const pc=v=>v==null?'—':Number(v).toFixed(1)+'%';
const n0=n=>n==null?'—':Number(n).toLocaleString();
const finiteNum=v=>{if(v==null||v==='')return null;const n=Number(v);return Number.isFinite(n)?n:null;};
const compactDollars=v=>{const n=finiteNum(v);if(n==null)return '—';const a=Math.abs(n);
  if(a>=1e9)return '$'+(n/1e9).toFixed(1).replace(/\.0$/,'')+'B';
  if(a>=1e6)return '$'+(n/1e6).toFixed(0)+'M';
  return '$'+Math.round(n).toLocaleString();};
const billionsKusd=v=>{const n=finiteNum(v);if(n==null)return '—';const a=Math.abs(n),sign=n<0?'-':'';
  if(a>=1e6)return sign+'$'+(a/1e6).toFixed(1)+'B';
  if(a>=1e3)return sign+'$'+(a/1e3).toFixed(1)+'M';
  return sign+'$'+Math.round(a).toLocaleString()+'K';};
const sharePc=v=>{const n=finiteNum(v);return n==null?'—':(n*100).toFixed(1)+'%';};
const ordinal=v=>{const n=finiteNum(v);if(n==null)return '—';const r=Math.round(n),a=Math.abs(r)%100,b=a%10;return r+((a>=11&&a<=13)?'th':b===1?'st':b===2?'nd':b===3?'rd':'th');};
/* Industry Standout: the four conventional location-quotient bands. DISPLAY ONLY - the underlying
   LQ is unchanged and stays on each figure's title= hover. */
const lqBand=v=>{const n=finiteNum(v);if(n==null)return '—';return n<1?'Not Specialized':n<1.25?'Slightly Specialized':n<2?'Specialized':'Highly Specialized';};
const last=n=>n.split(' ').slice(-1)[0];
/* ---- stance system (Champion..Opponent), per legislative area ---- */
const STANCE_ORDER=['C','F','A','H','P','G','T','N','L','S','K','O'];      // champion -> opponent (symmetric middle)
const STANCE_CURVES=new Set((D.stance_curves&&D.stance_curves.categories)||[]);
const POSTURE={C:'activate',F:'reinforce',A:'reinforce',H:'persuade',P:'persuade',G:'nurture',T:'qualify',N:'qualify',L:'monitor',S:'steward',K:'steward',O:'steward','-':'qualify'};
const stanceName=t=>(D.tiername&&D.tiername[t])||'—';
function stanceAt(m,area){ if(!area) return m.overall_stance||'-'; const i=CATS.indexOf(area); return (i>=0&&m.stances)?(m.stances[i]||'-'):'-'; }
function stanceBadge(t){const col=STCOL[t]||'#6b7280';return `<span class=tier style="color:${col};background:${col}1A"><span class=d></span>${stanceName(t)}</span>`;}

/* Public earned recognition is projected from one shared generation. Legacy
   stanceAt/stances remain inputs to the existing coalition and persuasion model. */
const HONORS=D.rank_honors||null;
const HONOR_COLORS={'Champion':'#fbbf24','Defender':'#fbbf24','Ally':'#86efac','Highly Persuadable':'#38bdf8','Persuadable':'#7dd3fc','Lean Persuadable':'#bae6fd','True Neutral':'#9ca3af','Lean Skeptic':'#fde3c0','Skeptic':'#f59e0b','Highly Skeptical':'#f97316','Opponent':'#ef4444'};
const OVERALL_STANDINGS=['Champion','Uncommitted','Ally','Highly Persuadable','Persuadable','Lean Persuadable','True Neutral','Lean Skeptic','Skeptic','Highly Skeptical','Opponent','Insufficient evidence'];
const STANDING_COLORS={'Champion':'#fbbf24','Uncommitted':'#9ca3af','Ally':'#86efac','Highly Persuadable':'#38bdf8','Persuadable':'#7dd3fc','Lean Persuadable':'#bae6fd','True Neutral':'#9ca3af','Lean Skeptic':'#fde3c0','Skeptic':'#f59e0b','Highly Skeptical':'#f97316','Opponent':'#ef4444','Insufficient evidence':'#9ca3af'};
if(HONORS)document.getElementById('integritybar').innerHTML+=` <b style="color:var(--ink)">Recognition evidence:</b> through ${esc(HONORS.as_of)}.`;
function honorAt(m,area){
  if(!HONORS)return null;
  const row=area?m.honors?.fields?.[area]:m.honors?.overall;
  if(!row)throw new Error('Shared honors record missing for '+m.name+(area?' / '+area:''));
  if(!area&&(!OVERALL_STANDINGS.includes(row.stance)||!Number.isInteger(row.priority_tier)||row.priority_tier<0||row.priority_tier>3))throw new Error('Shared overall priority/standing missing for '+m.name);
  return row;
}
function priorityLabel(m){const tier=honorAt(m,'').priority_tier;return tier===1?'Tier 1 · Champion':tier?'Tier '+tier:'Outside priority tiers';}
function priorityBadge(m){
  const row=honorAt(m,''),label=priorityLabel(m),col=({1:'#fbbf24',2:'#93c5fd',3:'#d6a86a'})[row.priority_tier]||'#9ca3af';
  const rank=row.rank==null?'No priority rank':'Priority rank #'+row.rank;
  return `<span title="${escAttr('Overall priority · '+rank)}"><span class=tier style="color:${col};background:${col}1A">${esc(label)}</span><small style="display:block;color:var(--faint);margin-top:3px">${esc(rank)}</small></span>`;
}
function standingRank(row){
  if(row.stance_rank_start==null)return 'No action rank';
  return (row.stance_rank_start===row.stance_rank_end?'#'+row.stance_rank_start:'Tied '+row.stance_rank_start+'–'+row.stance_rank_end)+' of '+row.stance_cohort_size;
}
function priorityMatches(m,value){return value===''||value==null||honorAt(m,'').priority_tier===Number(value);}
function overallRankOrder(a,b,sort){
  const ar=honorAt(a,''),br=honorAt(b,'');
  const order=sort==='tier'?((ar.priority_tier||99)-(br.priority_tier||99)||(ar.rank??Infinity)-(br.rank??Infinity))
    :(ar.rank??Infinity)-(br.rank??Infinity);
  return order||String(a.honors.member_id).localeCompare(String(b.honors.member_id));
}
function overallPolicyText(){
  return 'Overall labels follow the primacy board: Champion top 13; Ally top 15%; Highly Persuadable top 20%; Persuadable top 50%; Lean Persuadable top 75%. Below those bands: Uncommitted unless opposition is demonstrated. Repeated sponsorship or original cosponsorship of measures adverse to CU interests in the beneficiary/tensor evidence across Congresses establishes persistent opposition; unrelated positive actions do not cancel it. Tier sorting always uses primacy. Missing action is insufficient evidence, not neutrality.';
}
function honorLabel(m,area){
  const row=honorAt(m,area);
  if(!row)return stanceName(stanceAt(m,area));
  if(!area)return row.stance;
  /* Rule G labels every record that has evidence, so the two "threshold"
     strings below are reachable only under the legacy ladder. */
  if(row.recognition)return row.recognition;
  if(row.evidence_status==='unavailable')return 'Evidence unavailable';
  if(row.evidence_status!=='observed')return 'No recorded action';
  return Number(row.raw_field_score)>0&&row.positive_action_count>0?'Below recognition threshold':'No supportive honor';
}
function honorBands(){
  const p=(HONORS&&HONORS.policy)||{};
  if(p.ladder!=='g')return ['Champion','Ally','Highly Persuadable'];
  return ['Champion','Defender','Ally','Highly Persuadable','Persuadable','Lean Persuadable',
          p.zero_record_label||'True Neutral','Lean Skeptic','Skeptic','Highly Skeptical','Opponent'];
}
function honorCutoffText(row){
  const c=row.cutoffs||{};
  if(!c.positive)return `Cutoffs: Champion ${c.champion}, Ally ${c.ally}, Highly Persuadable ${c.highly_persuadable}`;
  const n=c.negative||{};
  return `Cutoffs in this field's supportive cohort: Champion ${c.positive.champion}, Ally ${c.positive.ally}, `
    +`Highly Persuadable ${c.positive.highly_persuadable}, Persuadable ${c.positive.persuadable}`
    +` \u00b7 opposing cohort: Opponent ${n.opponent}, Highly Skeptical ${n.highly_skeptical}, Skeptic ${n.skeptic}`;
}
function measureTitle(id){
  const titles=(HONORS&&HONORS.measure_titles)||{};
  return titles[id]||'';
}
function actionLedger(m,cat){
  const row=honorAt(m,cat),rows=(row&&row.field_actions)||[];
  if(!rows.length)return '';
  const KIND={legislative:'Bill',letter:'Letter',vote:'Floor vote',press:'Press'};
  const body=rows.map(a=>{
    const title=measureTitle(a.m);
    const dir=a.d>0?'supports CU position':a.d<0?'opposes CU position':'neutral direction';
    return `<tr><td style="padding:2px 6px 2px 0;white-space:nowrap">${esc(a.m)}</td>`
      +`<td style="padding:2px 6px 2px 0">${esc(title)}</td>`
      +`<td style="padding:2px 6px 2px 0;white-space:nowrap">${esc(KIND[a.k]||a.k)}</td>`
      +`<td style="padding:2px 6px 2px 0;white-space:nowrap">${esc(a.r)}</td>`
      +`<td style="padding:2px 6px 2px 0;white-space:nowrap">${esc(dir)}</td>`
      +`<td class=num style="padding:2px 0;white-space:nowrap">${esc(a.v)}</td></tr>`;}).join('');
  const per=row.years_served?` per year over ${row.years_served} years of service`:'';
  return `<table style="width:100%;border-collapse:collapse;margin-top:4px"><thead><tr style="color:var(--faint)">`
    +`<th style="text-align:left;font-weight:600">Measure</th><th style="text-align:left;font-weight:600">Title</th>`
    +`<th style="text-align:left;font-weight:600">Kind</th><th style="text-align:left;font-weight:600">Role</th>`
    +`<th style="text-align:left;font-weight:600">Direction</th><th style="text-align:right;font-weight:600">Contribution</th>`
    +`</tr></thead><tbody>${body}</tbody><tfoot><tr><td colspan=5 style="padding-top:3px">Total${per}</td>`
    +`<td class=num style="padding-top:3px">${esc(row.raw_field_score)}</td></tr></tfoot></table>`;
}
function honorRank(row){
  if(!row||row.evidence_status!=='observed'||row.rank_start==null)return '—';
  return row.rank_start===row.rank_end?'#'+row.rank_start:'Tied '+row.rank_start+'–'+row.rank_end;
}
function honorRankOrder(a,b,area){
  const ra=honorAt(a,area),rb=honorAt(b,area);
  return (ra.rank_start??Infinity)-(rb.rank_start??Infinity)
    ||String(a.honors.member_id).localeCompare(String(b.honors.member_id));
}
function honorFacts(m,area){
  const row=honorAt(m,area);if(!row)return '';
  const asOf='Evidence through '+HONORS.as_of;
  const count=row.action_count+' recorded action'+(row.action_count===1?'':'s');
  const window='Congresses '+(HONORS.evidence_window?.congresses||[]).join(', ');
  if(!area){
    const o=row.opposition_evidence||{};
    return `${row.stance} · Primacy rank #${row.rank} · ${priorityLabel(m)} · ${count} (${row.positive_action_count} supportive, ${row.negative_action_count} opposing) · ${row.stance_reason.replaceAll('_',' ')}${o.core_measures?.length?' · Measures adverse to CU interests: '+o.core_measures.join(', '):''} · ${asOf}`;
  }
  const cuts=row.cutoffs;
  const denominator=row.sub_cohort_size||row.cohort_size;
  const rank=row.evidence_status==='observed'?(row.rank_start==null?`Net neutral · ${row.cohort_size} members with recorded action`
    :`${honorRank(row)} of ${denominator} with recorded action`):`No field rank · ${row.cohort_size} members with recorded action`;
  const cohort=row.sub_cohort?` · Ranked among this field's ${row.sub_cohort==='positive'?'supportive':row.sub_cohort==='negative'?'opposing':'net-neutral'} records`:'';
  return `${row.alignment} · ${rank}${cohort} · ${count} · Raw action score: ${row.raw_field_score??'—'} · ${honorCutoffText(row)} · ${asOf} · ${window} · Tensor: ${row.tensor_status}`;
}
function publicBadge(m,area){
  if(!HONORS)return stanceBadge(stanceAt(m,area));
  const row=honorAt(m,area),col=(area?HONOR_COLORS[row.recognition]:STANDING_COLORS[row.stance])||'#9ca3af';
  const label=honorLabel(m,area),detail=area
    ?`${honorRank(row)}${row.evidence_status==='observed'?' of '+row.cohort_size:''} · ${row.action_count} actions`
    :'';
  return `<span title="${escAttr(honorFacts(m,area))}"><span class=tier style="color:${col};background:${col}1A"><span class=d></span>${esc(label)}</span>${detail?`<small style="display:block;color:var(--faint);margin-top:3px">${esc(detail)}</small>`:''}</span>`;
}
function honorMatches(m,area,value){
  const row=honorAt(m,area);
  if(!area)return row.stance===value;
  if(value==='no_recognition')return !row.recognition;
  if(value==='no_recorded_action')return row.evidence_status==='no_recorded_action';
  return row.recognition===value||row.alignment===value;
}
function honorFieldRows(m){
  return CATS.map(cat=>`<div class=arow style="display:block;padding:8px 0"><div style="display:flex;justify-content:space-between;gap:12px"><span class=aname>${esc(cat)}</span>${publicBadge(m,cat)}</div><details style="font-size:10px;color:var(--faint);margin-top:4px"><summary>${esc(honorFacts(m,cat))}</summary><div>Reason: ${esc(honorAt(m,cat).reason)}</div>${actionLedger(m,cat)}<div style="margin-top:4px">Generation ${esc(HONORS.generation_id)} · ${esc(m.honors.member_id)} · ${esc(cat)}</div></details></div>`).join('');
}
function configureRecognitionFilter(area){
  const select=$('#stancefilter');
  const options=HONORS
    ?(area?honorBands().map(label=>[label,label]).concat([['no_recognition','No earned recognition'],['no_recorded_action','No recorded action']])
      :OVERALL_STANDINGS.map(label=>[label,label]))
    :STANCE_ORDER.map(t=>[t,stanceName(t)]);
  select.innerHTML=`<option value="">${HONORS?(area?'All field recognition':'All stances'):'All stances'}</option>`;
  options.forEach(([value,label])=>{const option=document.createElement('option');option.value=value;option.textContent=label;select.appendChild(option);});
  select.setAttribute('aria-label',HONORS?(area?'Filter field recognition':'Filter stance'):'Filter stance');
}

/* ---------- manual notes: localStorage + baked-in fold-in ---------- */
const LSKEY='mdesk_manual_v1';
let MAN={}; try{MAN=JSON.parse(localStorage.getItem(LSKEY))||{}}catch(e){MAN={}}
function saveMAN(){localStorage.setItem(LSKEY,JSON.stringify(MAN));}
function manField(name,field,baked){const o=MAN[name]||{}; return (o[field]!=null&&o[field]!=='')?o[field]:(baked||null);}
function setMan(name,field,val){MAN[name]=MAN[name]||{}; MAN[name][field]=val; saveMAN();}
function filesOf(name){return (MAN[name]&&MAN[name].files)||[];}

/* ---------- pinned legislation, notes, and manual amendment annotations ---------- */
const BILLKEY='mdesk_bill_tracker_v1';
let BILLSTATE={};try{BILLSTATE=JSON.parse(localStorage.getItem(BILLKEY))||{}}catch(e){BILLSTATE={}}
function saveBillState(){localStorage.setItem(BILLKEY,JSON.stringify(BILLSTATE));}
function billState(key){return BILLSTATE[key]||{};}
function setBillState(key,field,value){(BILLSTATE[key]=BILLSTATE[key]||{})[field]=value;saveBillState();}

/* ---------- staff-directory state: gold(contacted)/star/meeting/notes/media (localStorage) ---------- */
const STFKEY='mdesk_staffdir_v1';
let STF={}; try{STF=JSON.parse(localStorage.getItem(STFKEY))||{}}catch(e){STF={}}
function saveSTF(){localStorage.setItem(STFKEY,JSON.stringify(STF));}
function stf(id){return STF[id]||{};}
function setStf(id,k,v){(STF[id]=STF[id]||{})[k]=v;saveSTF();}
let STFIDX={};
// DESK_SPLIT: member.staffdir is a deferred field, so the index built here at parse time is
// empty and __melangeProfileMerge must rebuild it once the profiles payload lands. Idempotent:
// the monolith (DSPLIT=0) build calls this exactly once, below, with staffdir already present.
function rebuildStaffIndex(){
  STFIDX={};D.members.forEach((m,i)=>(m.staffdir||[]).forEach(sf=>STFIDX[sf.id]={s:sf,m:m.name,mi:i}));
  const cv=document.getElementById('view-contact');if(cv&&!cv.hidden)renderContact();
}
rebuildStaffIndex();

/* ---------- manual PRESS-STANCE overrides: localStorage -> press_overrides_from_dashboard.json ---------- */
const POVKEY='mdesk_pressov_v1';
let POV={}; try{POV=JSON.parse(localStorage.getItem(POVKEY))||{}}catch(e){POV={}}   // url -> {stance,title}
const POVCOL={support:'#34d399',oppose:'#ef4444',neutral:'#9ca3af'};
function savePOV(){localStorage.setItem(POVKEY,JSON.stringify(POV));}
function setPOV(url,stance,title){ if(!url)return; if(!stance){delete POV[url];} else {POV[url]={stance:stance,title:title||''};} savePOV(); }
/* POV is imported JSON: only the three labels the desk knows may reach innerHTML or the POVCOL lookup */
function povOf(url){const s=POV[url]&&POV[url].stance;return typeof s==='string'&&/^(support|oppose|neutral)$/.test(s)?s:null;}
function povCount(){return Object.keys(POV).length;}
function updatePovHint(){const h=$('#povhint');if(h)h.textContent=povCount()?('✎ '+povCount()+' pending override'+(povCount()>1?'s':'')+' — Export to apply'):'';}

/* ---------- radar chart (SVG) ---------- */
function radar(vals,labels,color,R,vals2,color2,labelColors){
  const n=vals.length, padX=96, padY=44, cx=R+padX, cy=R+padY, W=2*cx, H=2*cy;
  const pt=(i,r)=>{const a=-Math.PI/2+i*2*Math.PI/n; return [cx+Math.cos(a)*r*R, cy+Math.sin(a)*r*R];};
  // FIXED pixel height + fully-QUOTED attributes + explicit closing tags. (Unquoted attrs ending in "…1/>"
  // get the slash swallowed into the value in HTML parsing, so the tag never self-closes and every following
  // element nests inside the first path and vanishes — that was the "radar not loading" bug.)
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;max-width:${W}px;height:${H}px;margin:0 auto">`;
  for(const g of [0.2,0.4,0.6,0.8,1]){let p='';for(let i=0;i<n;i++){const[x,y]=pt(i,g);p+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1);}s+=`<path d="${p}Z" fill="none" stroke="#433317" stroke-width="1"></path>`;}
  for(let i=0;i<n;i++){const[x,y]=pt(i,1);s+=`<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#433317" stroke-width="1"></line>`;}
  const valid=v=>v!=null&&Number.isFinite(Number(v));const complete=vals.every(valid);
  let p='',started=false;for(let i=0;i<n;i++){if(!valid(vals[i])){started=false;continue;}const[x,y]=pt(i,Math.max(0.02,Number(vals[i])));p+=(started?'L':'M')+x.toFixed(1)+' '+y.toFixed(1);started=true;}
  if(p)s+=`<path d="${p}${complete?'Z':''}" fill="${complete?color+'33':'none'}" stroke="${color}" stroke-width="2" stroke-linejoin="round"></path>`;
  for(let i=0;i<n;i++){if(!valid(vals[i]))continue;const[x,y]=pt(i,Math.max(0.02,Number(vals[i])));s+=`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.4" fill="${color}"></circle>`;}
  if(vals2&&color2){  // overlay series (run-4p: LLM item-read vs v2, same axes) — dashed, unfilled
    // OVERLAP REGION (run-4y, user: 'their overlapping space highlighted in light green'): both
    // polygons are star-shaped about the center, so the intersection boundary at angle t is
    // min(rA(t), rB(t)), sampled via ray-segment intersection. Wedges around radar.skipGreen axes
    // collapse to center (mechanism kept for reuse; no axis skipped since 2026-07-06 — user rule:
    // the overlap spans all five, Consc included). try/catch: the region is
    // decoration — the two polygons must NEVER vanish because of it.
    try{
      const rad_at=(vv,th)=>{const sec=2*Math.PI/n;let i0=Math.floor((th+Math.PI/2)/sec);i0=((i0%n)+n)%n;const i1=(i0+1)%n;
        const a0=-Math.PI/2+i0*sec;const r0=Math.max(0.02,vv[i0]||0),r1=Math.max(0.02,vv[i1]||0);
        const A=[Math.cos(a0)*r0,Math.sin(a0)*r0],Bq=[Math.cos(a0+sec)*r1,Math.sin(a0+sec)*r1];
        const d=[Math.cos(th),Math.sin(th)];const cr=(u,v)=>u[0]*v[1]-u[1]*v[0];
        const den=cr(d,[Bq[0]-A[0],Bq[1]-A[1]]);return den?Math.abs(cr(A,Bq)/den):0;};
      const SK=radar.skipGreen||[];const sec=2*Math.PI/n;let g='';const STEPS=n*24;
      for(let k=0;k<=STEPS;k++){const th=-Math.PI/2+k*2*Math.PI/STEPS;
        const near=SK.some(si=>{let dd=Math.abs(th-(-Math.PI/2+si*sec));dd=Math.min(dd,2*Math.PI-dd);return dd<sec/2;});
        const rr=near?0:Math.max(0,Math.min(rad_at(vals,th),rad_at(vals2,th)));
        g+=(k?'L':'M')+(cx+Math.cos(th)*rr*R).toFixed(1)+' '+(cy+Math.sin(th)*rr*R).toFixed(1);}
      s+=`<path d="${g}Z" fill="#86efac" fill-opacity="0.30" stroke="#86efac" stroke-opacity="0.5" stroke-width="1"></path>`;
    }catch(e){}
    let q='';for(let i=0;i<n;i++){const[x,y]=pt(i,Math.max(0.02,vals2[i]||0));q+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1);}
    s+=`<path d="${q}Z" fill="none" stroke="${color2}" stroke-width="2" stroke-dasharray="5 3" stroke-linejoin="round"></path>`;
    for(let i=0;i<n;i++){const[x,y]=pt(i,Math.max(0.02,vals2[i]||0));s+=`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" fill="${color2}"></circle>`;}
  }
  for(let i=0;i<n;i++){const[x,y]=pt(i,1.14);const c=Math.cos(-Math.PI/2+i*2*Math.PI/n);const anch=Math.abs(c)<0.34?'middle':(c>0?'start':'end');
    const lc=(labelColors&&labelColors[i])||'#A49D8A';const bold=(labelColors&&labelColors[i])?'font-weight="700"':'';
    s+=`<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" font-size="9" fill="${lc}" ${bold} text-anchor="${anch}" dominant-baseline="middle">${esc(labels[i]||'')}${(labelColors&&labelColors[i])?' ✓':''}</text>`;}
  return s+'</svg>';
}

/* ---------- industry makeup pie (SVG) ---------- */
const INDUSTRY_FAMILY={office:'#58a6ff',retail:'#f59e0b',industrial:'#f85149',services_other:'#34d399'};
function mixHex(a,b,t){
  const rgb=h=>[1,3,5].map(i=>parseInt(h.slice(i,i+2),16));
  const aa=rgb(a),bb=rgb(b),cc=aa.map((v,i)=>Math.round(v+(bb[i]-v)*t));
  return '#'+cc.map(v=>v.toString(16).padStart(2,'0')).join('');
}
function industryColor(code,meta){
  const sm=(meta&&meta.sector_meta)||{},row=sm[code]||{},group=row.group||'industrial';
  const peers=Object.keys(sm).filter(k=>(sm[k]||{}).group===group).sort();
  const ix=Math.max(0,peers.indexOf(code));
  if(group==='industrial'){
    const t=peers.length>1?ix/(peers.length-1):0;
    return mixHex('#f85149','#8b949e',t);
  }
  return mixHex(INDUSTRY_FAMILY[group]||'#8b949e','#000000',Math.min(.48,ix*.10));
}
function attr(s){return esc(s).replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function pieSVG(shares,meta,R,opts){
  opts=opts||{};R=Number(R)||92;
  const sm=(meta&&meta.sector_meta)||{},cx=R+4,cy=R+4,W=2*(R+4);
  const entries=Object.keys(sm).map(code=>({code:code,value:Math.max(0,Number((shares||{})[code])||0)}))
    .filter(e=>e.value>0);
  const total=entries.reduce((n,e)=>n+e.value,0);
  let s=`<svg viewBox="0 0 ${W} ${W}" width="100%" height="${W}" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;max-width:${W}px;height:${W}px;margin:0 auto" role="img" aria-label="${attr(opts.label||'Main industries in the district')}">`;
  if(total<=0)return s+`<circle cx="${cx}" cy="${cy}" r="${R}" fill="#433317"></circle></svg>`;
  entries.forEach(e=>e.share=e.value/total);
  entries.sort((a,b)=>b.share-a.share||a.code.localeCompare(b.code));
  const shown=entries.filter(e=>e.share>=.0025);
  const tiny=entries.filter(e=>e.share<.0025);
  if(tiny.length)shown.push({code:'__small__',share:tiny.reduce((n,e)=>n+e.share,0),tiny:tiny});
  const color=e=>e.code==='__small__'?'#8b949e':industryColor(e.code,meta);
  const title=e=>e.code==='__small__'
    ?'Small sectors: '+e.tiny.map(x=>(sm[x.code]||{}).name||x.code).join(', ')
    :((sm[e.code]||{}).name||e.code);
  if(shown.length===1||shown[0].share>=1-1e-12){
    const e=shown[0];
    return s+`<circle cx="${cx}" cy="${cy}" r="${R}" fill="${color(e)}" stroke="${opts.stroke||'#1D1912'}" stroke-width="${opts.strokeWidth||1}"><title>${esc(title(e))}</title></circle></svg>`;
  }
  let angle=-Math.PI/2;
  shown.forEach(e=>{
    const end=angle+e.share*Math.PI*2;
    const x1=cx+Math.cos(angle)*R,y1=cy+Math.sin(angle)*R;
    const x2=cx+Math.cos(end)*R,y2=cy+Math.sin(end)*R;
    const large=e.share>.5?1:0;
    const d=`M ${cx} ${cy} L ${x1.toFixed(3)} ${y1.toFixed(3)} A ${R} ${R} 0 ${large} 1 ${x2.toFixed(3)} ${y2.toFixed(3)} Z`;
    s+=`<path d="${d}" fill="${color(e)}" stroke="${opts.stroke||'#1D1912'}" stroke-width="${opts.strokeWidth||1}"><title>${esc(title(e))}</title></path>`;
    angle=end;
  });
  return s+'</svg>';
}

/* ---------- electoral history (two-series inline SVG: general % + primary %, 2016-2024) ---------- */
function evChart(h){
  const YEARS=[2016,2018,2020,2022,2024];
  const gm={},pm={}; (h.general||[]).forEach(g=>gm[g.year]=g); (h.primary||[]).forEach(p=>pm[p.year]=p);
  const W=560,H=196,padL=30,padR=12,padT=12,padB=24;
  const X=y=>padL+YEARS.indexOf(y)/(YEARS.length-1)*(W-padL-padR);
  const Y=v=>padT+(1-v/100)*(H-padT-padB);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="xMidYMid meet" style="display:block;max-width:${W}px;margin:2px auto 0">`;
  for(const g of [0,25,50,75,100]){const yy=Y(g).toFixed(1);
    s+=`<line x1="${padL}" y1="${yy}" x2="${W-padR}" y2="${yy}" stroke="#433317" stroke-width="1"></line>`;
    s+=`<text x="${(padL-5).toFixed(1)}" y="${yy}" font-size="9" fill="#9D9784" text-anchor="end" dominant-baseline="middle">${g}</text>`;}
  YEARS.forEach(y=>{s+=`<text x="${X(y).toFixed(1)}" y="${(H-7).toFixed(1)}" font-size="9" fill="#9D9784" text-anchor="middle">'${String(y).slice(2)}</text>`;});
  function draw(mp,col,isP){
    const val=y=>mp[y]?(mp[y].pct!=null?mp[y].pct:(isP&&mp[y].unopposed?100:null)):null;
    const pts=YEARS.filter(y=>val(y)!=null).map(y=>[X(y),Y(val(y)),y]);
    let o='';
    if(pts.length>1){let p='';pts.forEach((q,i)=>p+=(i?'L':'M')+q[0].toFixed(1)+' '+q[1].toFixed(1));
      o+=`<path d="${p}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round"></path>`;}
    pts.forEach(q=>{const uo=isP&&mp[q[2]]&&mp[q[2]].unopposed&&mp[q[2]].pct==null;
      o+= uo ? `<circle cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="3.4" fill="#282113" stroke="${col}" stroke-width="1.6"></circle>`
             : `<circle cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="3" fill="${col}"></circle>`;});
    return o;
  }
  s+=draw(gm,'#58a6ff',false);   // general
  s+=draw(pm,'#f59e0b',true);    // primary (unopposed -> hollow ring at 100)
  return s+'</svg>';
}
function evCardHTML(m){
  const h=m.electoral;
  if(!h) return `<div class=card style=margin-bottom:14px><div class=eyebrow>Electoral history</div><div class=empty>No federal-election history on file — first elected 2024, or not matched to the FEC / MEDSL records.</div></div>`;
  const fc=h.forecast||{}, sec=fc.security; let lbl='—',lc='#9ca3af';
  if(sec!=null){ if(sec>=0.66){lbl='Safe';lc='#34d399';} else if(sec>=0.33){lbl='Lean / likely';lc='#f59e0b';} else {lbl='Competitive';lc='#ef4444';} }
  const badge=`<span style="font-weight:700;font-size:11px;color:${lc};background:${lc}1A;border:1px solid ${lc}55;border-radius:20px;padding:3px 11px">Current outlook: ${lbl}</span>`;
  const leg=`<div style="display:flex;gap:16px;font-size:11px;color:var(--muted);margin-top:6px;flex-wrap:wrap">
    <span><span style="display:inline-block;width:13px;height:2px;background:#58a6ff;vertical-align:middle;margin-right:5px"></span>General %</span>
    <span><span style="display:inline-block;width:13px;height:2px;background:#f59e0b;vertical-align:middle;margin-right:5px"></span>Primary %</span>
    <span><span style="display:inline-block;width:7px;height:7px;border:1.5px solid #f59e0b;border-radius:50%;vertical-align:middle;margin-right:5px"></span>Unopposed primary</span></div>`;
  return `<div class=card style=margin-bottom:14px>
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:4px;flex-wrap:wrap">
      <div class=eyebrow style=margin-bottom:0>Electoral history — vote share by cycle</div>${badge}</div>
    ${evChart(h)}${leg}
    <div style="font-size:10.5px;color:var(--faint);margin-top:7px">Primary &amp; general from the FEC <i>Federal Elections</i> books (2016–2022); 2024 general from MEDSL; 2024 primary pending.</div>
  </div>`;
}

/* ---------- populate area selects ---------- */
const allAreas=CATS.concat(EXTRA);
[$('#area'),$('#cbarea'),$('#billarea')].forEach(sel=>{CATS.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;sel.appendChild(o);});});
M.forEach(m=>{const o=document.createElement('option');o.value=m.name;$('#allnames').appendChild(o);});
const COMMITTEE_FILTERS=[...new Set(M.flatMap(m=>(m.committees||[]).map(c=>committeeBase(c.name))).filter(Boolean))].sort();
COMMITTEE_FILTERS.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;$('#cbcommittee').appendChild(o);});

/* ======================= RANKED VIEW ======================= */
// stance filter dropdown (Champion..Opponent)
configureRecognitionFilter('');
$('#priorityfilter').hidden=!HONORS;

if(HONORS){$('#sort option[value=rank]').textContent='Sort: Rank';$('#sort option[value=tier]').textContent='Sort: Tier (primacy)';}

const rstate={q:'',chamber:'all',funded:false,potential:false,sort:'rank',area:'',stance:'',priority:''};
$('#q').oninput=e=>{rstate.q=e.target.value.toLowerCase().trim();renderRanked();};
$('#fundedOnly').onchange=e=>{rstate.funded=e.target.checked;renderRanked();};
$('#potentialOnly').onchange=e=>{rstate.potential=e.target.checked;renderRanked();};
$('#sort').onchange=e=>{rstate.sort=e.target.value;renderRanked();};
$('#area').onchange=e=>{rstate.area=e.target.value;if(HONORS){rstate.stance='';configureRecognitionFilter(rstate.area);if(rstate.area&&rstate.sort==='standing'){rstate.sort='rank';$('#sort').value='rank';}}renderRanked();};
$('#stancefilter').onchange=e=>{rstate.stance=e.target.value;renderRanked();};
$('#priorityfilter').onchange=e=>{rstate.priority=e.target.value;renderRanked();};
document.querySelectorAll('#chamber button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#chamber button').forEach(x=>x.classList.remove('on'));b.classList.add('on');rstate.chamber=b.dataset.v;renderRanked();});
function sparkHTML(m){return `<div class=spark>`+['press','comm','leg','demo'].map(k=>{const v=m[k+'_n']||0;return `<i style="height:${Math.max(12,v*100)}%;background:${PILL[k].c}"></i>`;}).join('')+`</div>`;}
const TIERCOL={1:'#fbbf24',2:'#93c5fd',3:'#d6a86a'};   // Legislative+Press tier: gold / ice-blue / bronze
function tierChip(m){if(HONORS)return '';const t=m.tier||0;return t?`<span title="Primacy board · Tier ${t}${m.tierrank?' · rank #'+m.tierrank:''}" style="display:inline-block;background:${TIERCOL[t]};color:#1D1912;font-weight:800;font-size:9px;line-height:1;letter-spacing:.3px;padding:2px 4px;border-radius:5px;vertical-align:middle;margin-left:6px">T${t}</span>`:'';}
function conductCard(m){
  const d=m.tier_detail;if(!d)return '';
  const ceiling=d.tier_ceiling===0?'Untiered':`Tier ${d.tier_ceiling}`;
  const grievances=(d.open_events||[]).join(', ')||'None';
  const deliveries=(d.delivery_events||[]).join(', ')||'None';
  const grievanceDetail=(d.grievances||[]).map(g=>`<div style="margin:7px 0;color:var(--muted)"><b>${esc(g.event_id)}</b>: ${esc(g.reason)}<br><span style="color:var(--text)">Cure: ${esc(g.cure||'not specified')}</span></div>`).join('');
  const deliveryDetail=(d.deliveries||[]).map(x=>`<div style="margin:7px 0;color:var(--muted)"><b>${esc(x.event_id)}</b>: ${esc(x.instrument)} on ${esc(x.bill)}${x.congress?' ('+esc(x.congress)+')':''} moved ${Number(x.credit||0).toFixed(3)} credit.</div>`).join('');
  return `<div class=card style=margin-bottom:14px><div class=eyebrow>Primacy conduct ledger</div>
    <div class=mrow><span>Open grievance events</span><b>${esc(grievances)} · ${Number(d.gross_severity||0).toFixed(2)}</b></div>
    <div class=mrow><span>Delivery events</span><b>${esc(deliveries)} · ${Number(d.delivered_credit||0).toFixed(2)}</b></div>
    <div class=mrow><span>Net severity · best permitted tier</span><b>${Number(d.net_severity||0).toFixed(2)} · ${ceiling}</b></div>
    ${grievanceDetail}${deliveryDetail}</div>`;
}
// D-034 footprint display helpers.  DPLABEL/DPRAW read the shipped metadata rather
// than hardcoding the four keys, so the weight set stays the single source of truth.
const DPSHARE=new Set(['cu_branch_share','cu_deposit_share']);
const dp3=v=>{const n=finiteNum(v);return n==null?'—':n.toFixed(3);};
const dpValue=(key,raw)=>DPSHARE.has(key)?sharePc(raw):dp3(raw);
const dpPct=v=>v==null?'—':Math.round(Number(v)*100);
function dpBasis(p){const lab=(D.demographic_potential_meta||{}).component_labels||{};
  const raw=p.raw||{},comp=p.components||{};
  return Object.keys(lab).map(k=>`${lab[k]} ${dpValue(k,raw[k])} (${dpPct(comp[k])} pct)`).join(' · ')||'no components scored';}
// The 'Structurally adjusted' view falls back to the plain potential when the
// companion is not scored in this mode -- otherwise every cell reads '—' and the
// order collapses to alphabetical, which looks like data loss rather than a dial.
function potentialChip(m){const p=m.demographic_potential||{};if(!p.qualified)return '';
  const sectors=(p.signals||[]).map(row=>row.name||row.sector).join(', ');
  const score=p.score==null?'':` · ${Number(p.score).toFixed(1)}`;
  const rank=p.rank==null?'':` · #${p.rank} of ${p.market_count} markets`;
  const structural=p.structural_adjustment||{},adjusted=structural.available?` · structural companion ${Number(structural.score).toFixed(1)} (#${structural.rank})`:'';
  const basis=DPMODE==='footprint'?' · Credit-union footprint: '+dpBasis(p):' · Industry Standout + competitive shift: '+sectors;
  return `<span class=potential-tag title="${attr((p.tier||'Qualified')+rank+adjusted+basis)}">Demographic Potential${score}</span>`;}
function renderRanked(){
  // DEMO_POTENTIAL (D-034), re-derived locally rather than read from the DPMODE
  // const above: test_member_desk_honors.cjs lifts this one function out of the
  // template and runs it against a context that carries D but none of this
  // file's top-level consts.  Under footprint nothing scores the two reach
  // components, so the reach-residual companion is not_applicable_for_mode and
  // no member carries a structural score; the "Structurally adjusted" view then
  // falls back to the plain potential rather than emptying every cell and
  // collapsing the order to alphabetical.
  const dpAdjustable=((D.demographic_potential_meta||{})
    .footprint_employment_lq_min??null)==null;
  const potentialView=(m,adjusted)=>{const p=m.demographic_potential||{};
    return (adjusted&&dpAdjustable)?(p.structural_adjustment||{}):p;};
  const _rowsEl=document.getElementById('rows');
  const _before=(!RMQ&&_rowsEl)?new Map(Array.prototype.map.call(_rowsEl.children,tr=>[tr.getAttribute('data-i'),tr.getBoundingClientRect().top])):null;
  const area=rstate.area, ai=area?CATS.indexOf(area):-1;
  let r=M.filter(m=>{
    if(rstate.q&&!(m.name.toLowerCase().includes(rstate.q)||(m.district||'').toLowerCase().includes(rstate.q)))return false;
    if(rstate.chamber!=='all'&&m.chamber!==rstate.chamber)return false;
    if(rstate.funded&&!(m.invested>0))return false;
    if(rstate.potential&&!(m.demographic_potential&&m.demographic_potential.qualified))return false;
    if(HONORS&&!priorityMatches(m,rstate.priority))return false;
    if(rstate.stance&&(HONORS?!honorMatches(m,area,rstate.stance):stanceAt(m,area)!==rstate.stance))return false;
    return true;
  });
  if(area&&rstate.sort!=='tier'){r.sort(HONORS?(a,b)=>honorRankOrder(a,b,area):(a,b)=>(b.radar[ai]||0)-(a.radar[ai]||0));}
  else{const s=rstate.sort;r.sort((a,b)=>s==='potential_adjusted'?((potentialView(b,true).score??-1)-(potentialView(a,true).score??-1)||(potentialView(a,true).rank??9999)-(potentialView(b,true).rank??9999)||a.name.localeCompare(b.name)):s==='potential'?((b.demographic_potential?.score??-1)-(a.demographic_potential?.score??-1)||(a.demographic_potential?.rank??9999)-(b.demographic_potential?.rank??9999)||a.name.localeCompare(b.name)):s==='tier'?((a.tier||99)-(b.tier||99)||(a.tierrank||9999)-(b.tierrank||9999)):s==='score'?(b.score||0)-(a.score||0):s==='invested'?(b.invested||0)-(a.invested||0):s==='name'?a.name.localeCompare(b.name):(a.rank||9999)-(b.rank||9999));}   // 'tier': T1->T2->T3->untiered, then by leg+press rank
  if(HONORS&&(rstate.sort==='tier'||(!area&&rstate.sort==='rank')))r.sort((a,b)=>overallRankOrder(a,b,rstate.sort));
  const potentialSort=!area&&rstate.sort==='potential',adjustedPotentialSort=!area&&rstate.sort==='potential_adjusted',potentialMode=potentialSort||adjustedPotentialSort;
  const standingSort=false;
  $('#count').textContent=`Showing ${r.length} of ${M.length} members`+(area?` · ${area}`:'');
  $('#th-rank').textContent=rstate.sort==='tier'?'Primacy rank':potentialMode?'Opportunity rank':HONORS?(area?'Field rank':standingSort?'Action rank':'Priority rank'):'Rank';
  $('#th-stance').textContent=HONORS?(area?'Recognition · '+area:'Stance · overall'):area?('Stance · '+area):'Stance · overall';
  $('#th-score').textContent=area?(HONORS?'Raw action score':area):(adjustedPotentialSort?(dpAdjustable?'Adjusted potential':'Potential score'):potentialSort?'Potential score':standingSort?'Raw action score':HONORS?'Model score':'Score');
  $('#rows').innerHTML=r.map((m,i)=>{
    const p=m.demographic_potential||{};
    const pv=potentialView(m,adjustedPotentialSort);
    const scoreCell=potentialMode
      ? (pv.score==null?'<span style=color:var(--faint)>—</span>':`<div class=scorecell><div class=sbar><i style=width:${pv.score}%></i></div><span class=sval>${Number(pv.score).toFixed(1)}</span></div>`)
      : area
      ? HONORS?`<span class=sval>${honorAt(m,area).evidence_status==='observed'?esc(honorAt(m,area).raw_field_score):'—'}</span>`:`<div class=scorecell><div class=sbar><i style=width:${(m.radar[ai]||0)*100}%></i></div><span class=sval>${f2(m.radar[ai])}</span></div>`
      : standingSort?`<span class=sval>${esc(honorAt(m,'').raw_overall_score??'—')}</span>`
      : `<div class=scorecell><div class=sbar><i style=width:${(m.score||0)*100}%></i></div><span class=sval>${f2(m.score)}</span></div>`;
    const rk=rstate.sort==='tier'&&HONORS?honorAt(m,'').rank:potentialMode?(pv.rank==null?'—':'#'+pv.rank):area?(HONORS?honorRank(honorAt(m,area)):('#'+(m.adjrank[area]||'—'))):standingSort?standingRank(honorAt(m,'')):(HONORS?(honorAt(m,'').rank??'—'):(m.rank??'—'));
    return `<tr data-i=${byName[m.name]}>
      <td class=num><span class=rk>${rk}</span></td>
      <td><div class=nm>${mlink(m.name)}${tierChip(m)}${potentialChip(m)}</div><div class=dist>${esc(m.chamber)} · ${esc(m.party||'—')} · ${esc(m.district||'')}</div></td>
      <td>${publicBadge(m,area)}</td>
      <td>${sparkHTML(m)}</td>
      <td>${scoreCell}</td></tr>`;
  }).join('');
  document.querySelectorAll('#rows tr').forEach(tr=>tr.onclick=e=>rowOpen(e,tr));
  if(_before){
    let _moved=0;
    Array.prototype.forEach.call(_rowsEl.children,tr=>{
      const prev=_before.get(tr.getAttribute('data-i'));
      if(prev===undefined){tr.classList.add('mel-enter');return;}
      const d=prev-tr.getBoundingClientRect().top;
      if(Math.abs(d)<1)return;
      tr.style.transform='translateY('+d+'px)';tr.style.transition='none';_moved++;
    });
    // rAF can be starved (backgrounded tab); race a timer so no row is ever stranded at its
    // OLD position, and hard-clear afterwards. Worst case the table snaps - today's behaviour.
    let _played=false;
    const _play=()=>{if(_played)return;_played=true;
      Array.prototype.forEach.call(_rowsEl.children,tr=>{
        if(!tr.style.transform)return;
        tr.style.transition='transform 420ms cubic-bezier(.22,.61,.36,1)';tr.style.transform='';});};
    requestAnimationFrame(_play);setTimeout(_play,32);
    setTimeout(()=>{Array.prototype.forEach.call(_rowsEl.children,tr=>{tr.style.transition='';tr.style.transform='';tr.classList.remove('mel-enter');});},580);
  }
}

/* ======================= MEMBER VIEW ======================= */
function openMember(i){
  const m=M[i]; if(!m)return;
  if(DSPLIT&&!window.__profilesReady){
    window.__pendingOpen=i;
    if(!document.getElementById('mel-profiles-note')){
      const n=document.createElement('div');n.id='mel-profiles-note';n.setAttribute('role','status');
      n.textContent='Profile data is still arriving - this member will open the moment it lands.';
      document.body.appendChild(n);}
    return;
  }
  setView('member'); document.getElementById('view-member').scrollTop=0;
  history.replaceState(null,'','?m='+encodeURIComponent(m.name));
  const os=m.overall_stance||'-', col=STCOL[os]||'#6b7280';   // overall stance tier drives the verdict
  // A member with no overall stance used to open on a bare em-dash pill ('— · qualify · overall').
  // Say it in words instead. stanceName() itself is unchanged: the ranked table and the per-area
  // badges still print the em-dash they always did.
  const verdictName=stanceName(os)==='—'?'No stance on record':stanceName(os);
  // per-area stance, sorted champion -> opponent (then by score)
  // say-do gate (2026-07-07): ★ = Proven (sponsored bill in this area advanced past introduction);
  // ° = tier capped Champion/Defender->Ally for lacking a sponsored bill in the area (talk without paper)
  const provSet=new Set(m.proven||[]), gcapSet=new Set(m.gcap||[]);
  const areaRows=HONORS?[]:CATS.map((cat,ix)=>({cat,ix,st:(m.stances&&m.stances[ix])||'-',score:m.radar[ix],rank:m.adjrank[cat],def:DEF.has(cat),curved:STANCE_CURVES.has(cat)}))
    .sort((a,b)=>{const na=STANCE_ORDER.indexOf(a.st),nb=STANCE_ORDER.indexOf(b.st);return (na<0?99:na)-(nb<0?99:nb)||(b.score-a.score);});
  const stanceByArea=HONORS?honorFieldRows(m):areaRows.map(a=>`<div class=arow><span class=aname>${esc(a.cat)}${a.def?' <span style=color:var(--faint)>(def)</span>':''}${a.curved?' <span title="Label is curved within positive and negative evidence for this legislative area; the raw score and rank are unchanged" style=color:#EECD5C>(curved)</span>':''}${provSet.has(a.ix)?' <span title="Proven: sponsored bill in this area advanced past introduction" style=color:#f59e0b>★</span>':''}${gcapSet.has(a.ix)?' <span title="Capped from Champion: no sponsored bill in this area backs the rhetoric" style=color:var(--faint)>°</span>':''}</span>${stanceBadge(a.st)}<span class=arank>#${a.rank||'—'} · ${f2(a.score)}</span></div>`).join('');
  const legRadar=radar(m.radar,D.cats_short,'#34d399',90);
  const evLbl=m.ev_play, evVal=m.ev_value;
  // pillars
  const pillars=['press','comm','leg','demo'].map(k=>{const v=m[k+'_n'],raw=m[k+'_raw'];
    return `<div class=barrow><span class=t><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${PILL[k].c};margin-right:5px"></span>${PILL[k].l} <span style=color:var(--faint)>×${PILL[k].w.toFixed(2)}</span></span><span class=track><i style="width:${(v||0)*100}%;background:${PILL[k].c}"></i></span><span class=val>${f2(v)}</span></div>`;}).join('');
  const roleInfluence=(m.formal_role_influence_nominal||0)>0
    ?`<div style="font-size:10px;color:var(--faint);margin-top:5px">${esc(m.formal_role_influence_office||'Party leadership office')} — titled public-exposure context.</div>`:'';
  let h=`<div class=mhead>
    <div>
      ${HONORS?`<div class=verdict>${publicBadge(m,'')}</div><details style="font-size:10px;color:var(--faint);margin-top:8px"><summary>Classification evidence</summary><p>${esc(honorFacts(m,''))}</p><p>${esc(overallPolicyText())}</p></details>`:`<div class=verdict style="color:${col};background:${col}26"><span class=d></span>${verdictName} · ${POSTURE[os]||'qualify'} <span style=opacity:.7;font-weight:500>· overall${m.overall_say_do?' · say-do capped':''}</span></div>`}
      <h2>${esc(m.name)}${potentialChip(m)}</h2><div class=meta>${esc(m.chamber)} · ${esc(m.party||'—')} · ${esc(m.district||'')}</div>
    </div>
    <div style=display:flex;gap:8px>
      <button class=backbtn id=backbtn>◀ Members</button>
      <button class=backbtn id=printbtn>⎙ Print / PDF</button>
      <a class="netlink deskhome" href="Moneyball Dashboard.html">◀ Main dashboard</a>
    </div>
  </div>
  <div class=figs style=margin-bottom:14px>
    <div class=fig><div class=v>#${HONORS?(honorAt(m,'').rank??'—'):(m.rank??'—')}</div><div class=l>${HONORS?'Overall priority rank':'Overall rank'}</div></div>
    <div class=fig><div class=v>${m.tier?('Tier '+m.tier):'Not tiered'}</div><div class=l>Primacy tier${m.tier&&!HONORS?' · overall rank unchanged':''}${m.tier&&m.tierrank?' · #'+m.tierrank:''}</div></div>
    <div class=fig><div class=v>${f2(m.score)}</div><div class=l>${HONORS?'Model score':'Overall score'}</div></div>
    ${(m.demographic_potential||{}).score==null?'':`<div class=fig><div class=v>${Number(m.demographic_potential.score).toFixed(1)}</div><div class=l>District opportunity · ${ordinal(m.demographic_potential.rank)} of ${m.demographic_potential.market_count}</div></div>`}
    <div class=fig accent><div class=v>${String(m.money_source_status||'').startsWith('UNRESOLVED')?'Unresolved':money(m.invested)}</div><div class=l>Credit-union PAC contributions</div></div>
    <div class=fig><div class=v>${String(m.money_source_status||'').startsWith('UNRESOLVED')?'Unresolved':money(m.ind_money)}</div><div class=l>Bank &amp; financial-service PACs</div></div>
    <div class=fig><div class=v>${(m.roi||0).toFixed(3)}</div><div class=l>Return per $10,000</div></div>
    <div class=fig><div class=v>${evVal>0?money(evVal):'—'}</div><div class=l>Electoral exposure${evLbl&&evLbl!=='No active play'?' · <span style=color:#f59e0b>'+evLbl+'</span>':''}</div></div>
  </div>
  ${conductCard(m)}
  <div class=gen>
    <div class=card><div class=eyebrow>${HONORS?'Analytical field signals':'Legislative strengths — relative to all members'}</div>${legRadar}${HONORS?'<div style="font-size:10px;color:var(--faint)">Model signals combine legislative and media inputs. Earned recognition uses the recorded actions shown alongside.</div>':''}
      <div class=mrow style=margin-top:8px><span>Pillar composition</span><span></span></div>${pillars}${roleInfluence}</div>
    <div class=card><div class=eyebrow>${HONORS?'Earned recognition by legislative area':'Stance by legislative area'}</div><div style="font-size:10px;color:var(--faint);margin-bottom:7px">${HONORS?`<b>Recorded action required.</b> Champion: top ${HONORS.policy.champion_pct}%; Ally: within top ${HONORS.policy.ally_pct}%; Highly Persuadable: within top ${HONORS.policy.highly_persuadable_pct}%. Cutoffs use each field's full observed cohort; ties must fit entirely. Positive net support is required. Tensor only resolves equal action scores. Highly Persuadable names a rank distinction, not a persuasion probability. Evidence through ${esc(HONORS.as_of)}.`:'<b>CU advocacy frame:</b> positive labels mean alignment with credit-union priorities. For CFPB, Champion/Ally means support for actions that <b>restrain CFPB reach</b>; it does not mean support for expanding the agency.'}</div><div class=arealist>${stanceByArea}</div></div>
  </div>
  ${evCardHTML(m)}
  <div class=card id=trackcard style=margin-bottom:14px>${trackerHTML(m)}</div>`;
  // sections
  h+=section('Profile','who',
      narrativeHTML(m)
     +sub('Biography',bioHTML(m))
     +sub('Personal details',personalHTML(m))
     +sub('Committee positions',committeeHTML(m))
     +sub('Working style',psychHTML(m))
     +sub('Observed speech & context',speechHTML(m)));
  // Single-child groups render their child BARE (as 'District' already does): a sub() wrapper
  // around the only member of a group is a second click that buys nothing.
  h+=section('Bills','stand',legHTML(m));
  h+=section('Access','reach',
      sub('Responsiveness',respHTML(m))
     +sub('Staff',staffHTML(m))
     +sub('Working relationships',relHTML(m)));
  h+=section('District'+(m.industry&&m.industry.redrawn?'*':''),'district',demoHTML(m));
  // Renamed to 'Press & coverage': every funding figure lives in the header tiles above, so the
  // previous name promised money this group has never held.
  h+=section('Press & coverage','money',pressHTML(m));
  h+=section('Sources & method','method',methodHTML(m));
  $('#memberbody').innerHTML=h;
  $('#backbtn').onclick=()=>{setView('ranked');history.replaceState(null,'','./');};
  $('#printbtn').onclick=()=>{document.querySelectorAll('#app-member details.sec,#app-member details.sub').forEach(d=>d.open=true);window.print();};
  wireManual(m);
  wireTracker(m);
  wireSectionFilters(m);
}
function section(title,key,inner){
  return `<details class=sec data-key="${key}"><summary>${title}<span class=chev>▸</span></summary><div class=body>${inner}</div></details>`;
}
/* ---- 'Sources & method': the methodology passages, gathered out of the
   inline surfaces they used to interrupt. Nothing here is recomputed - every row is the
   same string the page used to print in place. ---- */
function methodHTML(m){
  const P=m.psy||{},X=P.ext||{};
  const districtBriefMeta=D.district_briefs_meta||{};
  const potential=m.demographic_potential||{},pmeta=D.demographic_potential_meta||{};
  const plabels=pmeta.component_labels||{},psens=pmeta.sensitivity_audit||{};
  const equalSensitivity=psens.equal_weights||{},omittedSensitivity=psens.minimum_leave_one_out_spearman||{};
  const defs=Array.isArray(pmeta.component_definitions)?pmeta.component_definitions:[];
  const sensitivityText=psens.scenario_count?`Sensitivity audit: equal weights rho ${f2(equalSensitivity.spearman_rho)}, top-${psens.top_n||20} overlap ${Math.round(Number(equalSensitivity.top_overlap_rate||0)*100)}%; weakest leave-one-out rho ${f2(omittedSensitivity.spearman_rho)} when ${esc(plabels[omittedSensitivity.component]||omittedSensitivity.component||'—')} is omitted.`:'Sensitivity audit unavailable.';
  const rows=[];
  const add=(term,body)=>{if(body)rows.push(`<dt style="font-family:var(--ui-display);text-transform:uppercase;letter-spacing:.12em;font-size:10px;font-weight:700;color:var(--muted);margin-top:11px">${term}</dt><dd style="font-size:11.5px;line-height:1.6;color:var(--faint);margin:3px 0 0">${body}</dd>`);};
  if(HONORS){
    add('Recognition source',`Shared generation ${esc(HONORS.generation_id)}; field policy ${esc(HONORS.policy.id)}; overall standing policy ${esc(HONORS.overall_policy.id)}. Overall priority comes from the ${esc(HONORS.overall_board.mode)} board. Reporting/model rank: #${m.rank??'—'}. Field cohorts use recorded action in Congresses ${(HONORS.evidence_window.congresses||[]).map(esc).join(', ')}. Evidence through ${esc(HONORS.as_of)}.`);
    add('Overall priority and behavioral standing',esc(overallPolicyText()));
  }
  add('Industry Standout',"Location quotient, banded for reading: under 1.00 Not Specialized; 1.00–1.24 Slightly Specialized; 1.25–1.99 Specialized; 2.00 and above Highly Specialized. The exact quotient is unchanged and stays on each figure's hover.");
  if(P.pressure_mult!=null)add('Engagement responsiveness (engines)',`<b>${P.pressure_mult}×</b>`);
  /* The approach brief is the first thing inside 'Profile', so its provenance is stated here and
     here only — the brief itself carries no explanatory text (Paul 2026-09-01). Shown for withheld
     members too: it describes the feature, not one member's instance. */
  if(P.narrative&&P.narrative.format==='descriptive-v3')add('Profile summary','Three plain-language paragraphs summarize the public record, public communication, and the limits of the available evidence. These summaries use checked templates tied to the recorded source measures. Each profile is checked for source consistency, complete coverage, and a length of 200–400 words. These checks are deterministic; they are not an independent human review of every audio recording. A recurring interview pattern requires two independently produced interviews in different settings. Single-source observations remain provisional.');
  else if(P.narrative||P.narrative_status)add('Approach brief','Written from the measures already on this profile: the personality axes, the observed-behaviour percentiles, the speech measures and the hearing-antagonism facet. Those are gathered into one evidence packet per member and drafted by Claude Sonnet on 31 August 2026. Each brief is checked mechanically for length, for banned phrasing, and for wording reused from another member. No check confirms a sentence against the measure it came from.');
  if(m.district_brief)add('District brief',`Three evidence-grounded paragraphs covering household and housing conditions, credit-union relevance, and industry specialization. The corpus was generated ${esc(districtBriefMeta.generated||'—')} and passed deterministic word-band, figure-cap, evidence-grounding, formatting, ban-list, and novelty checks. It is displayed as narrative context, not as a score. ${esc(((districtBriefMeta.known_limitations||[])[0])||'')}`);
  if(m.cook_pvi)add('Cook PVI · 2026 source snapshot',`Cook Political Report's rounded label describes historical presidential partisan lean relative to the nation; it is not an election forecast. This snapshot covers voting House districts only, with no Senate or territorial values. A district-code match does not verify the current member or current district boundaries. Source incumbent names are not used for matching. ${cookPviSourceHTML()} This context does not enter scores, ranks, allocations, receptivity, ROI, or efficiency calculations.`);
  if(P.big5_pct||P.big5_fb_pct)add('Personality read','Gold-validated: gold-anchored axes use the OOF Senate-gold composites.');
  if(X.do_pct!=null)add('Credit-union bill delivery','Sponsored + cosponsored CU bills weighted by role and by how far each advanced. Not passage-weighted, so it does not reward holding the majority.');
  if(X.antag)add('Antagonist flag',`<div style="font-size:11px;font-weight:700;color:#f59e0b;background:#f59e0b22;border:1px solid #f59e0b55;padding:2px 8px;border-radius:10px;display:inline-block;margin-top:4px">Signed-mention antagonist set (Aref-Neal splinter)</div>`);
  if(potential.qualified){
    add('District opportunity — qualification',`${esc(pmeta.method||'Industry Standout and positive competitive shift in a credit-union-relevant constituency sector.')} ${esc(pmeta.score_formula||'')}`);
    add('District opportunity — sensitivity',sensitivityText);
    add('Structurally adjusted / 100','Companion diagnostic: only employer-reach components are residualized; baseline sticker and score remain unchanged');
    add('Business-census cross-check (CBP)','County Business Patterns is the business census used to check the LODES-based figures. Where it does not cover a sector the district card says "not in the business census" rather than showing a zero, and the hover names the CBP cross-check location quotient.');
    defs.forEach(def=>add(`${esc(def.label||def.key)} — how it is built`,`<b>Raw:</b> ${esc(def.raw_formula||'—')}<br><b>Normalize:</b> ${esc(def.normalization||'—')}`));
  }
  /* Source codes lifted out of the district statistics themselves (2026-09-02 plain-language pass).
     The tiles now carry plain words and keep the code on their hover; these rows are the durable
     statement of where each number comes from, so nothing was deleted by the rename. */
  if(m.community&&m.community.complete)add('Census tables behind the community snapshot','The three links under that card are Census profile families, shown by what they cover: <b>DP05</b> as "People", <b>DP03</b> as "Jobs and income", <b>DP04</b> as "Housing". The figures themselves come from the detailed tables behind those profiles rather than from the profiles, and the hover on each tile names the exact table and variable it used. "Median monthly rent" is the ACS median <i>gross</i> rent, which includes tenant-paid utilities. "Homes lived in by their owner" is the owner-occupied share of occupied housing units.');
  if(m.demo)add('The older district statistics','Five tiles in the first grid — "Homeowners stretched by housing costs", "Workers in finance / real estate", "Homeowners who have a mortgage", "Business establishments" and "Income per person" — are carried through from the original Member Desk workbook rather than recollected here: they arrive already computed in <span class=mono>data/members/member_desk_src.json</span>, which no script in this repository writes. The exact Census variable behind each one, and the cost-to-income cut-off behind the cost-burden figure, are not recorded anywhere in this system, so no variable and no threshold is stated on those tiles. Where the ACS community snapshot below covers the same quantity it is preferred and the older tile is dropped instead of being shown twice.');
  if(m.hmda)add('Mortgage lending (HMDA)','Home Mortgage Disclosure Act records. "Mortgages made" counts loan originations. "Share for buying a home" is the share of those whose stated purpose is a home purchase; the rest are refinances and other purposes. "Share in lower-income areas" counts loans in census tracts whose income is below 80% of the surrounding area median — a tract-basis measure, not a borrower-income one.');
  /* The four supporting measures that used to sit in a second, district-local fold.
     Consolidated here so the profile has exactly one method section. Nothing is
     recomputed: each cell is the same string the district card used to print, and both
     decorations are rebuilt from the same fields demoHTML reads. */
  const gh=D.gh_meta||{},cells=[];
  if(m.employers){
    const large=(m.employers||{}).large||{};
    const largeTip=String(gh.large_incomplete||'Incomplete source coverage; totals are lower bounds.');
    const dagger=large.complete===false?` <span title="${attr(largeTip)}">†</span>`:'';
    cells.push(`<div class=dcell><div class=v>${n0(finiteNum(large.est_1000plus))}${dagger}</div><div class=l>Workplaces with 1,000 or more staff</div></div>`);
  }
  if(m.financial){
    const banks=(m.financial||{}).banks||{};
    const hq=String(gh.sod_hq_deposits||''),hqTitle=hq?` title="${attr(hq)}"`:'';
    const hhi=finiteNum(banks.hhi);
    cells.push(`<div class=dcell${hqTitle}><div class=v>${hhi==null?'—':n0(hhi)}</div><div class=l>Deposit concentration</div></div>`);
    cells.push(`<div class=dcell><div class=v style="font-size:13px">${esc(banks.top1_name||'—')}</div><div class=l>Top bank</div></div>`);
  }
  if(m.hmda){
    const hmda=m.hmda||{};
    cells.push(`<div class=dcell><div class=v>${sharePc(hmda.home_purchase_share)}</div><div class=l>Share for buying a home</div></div>`);
  }
  return `<dl style="margin:0">${rows.join('')}</dl>${cells.length?`<div class=dgrid style="margin-top:11px">${cells.join('')}</div>`:''}`;
}
/* One step quieter than section(): a nested block inside a group, open by default. */
function sub(title,inner){
  return `<details class=sub><summary>${title}<span class=chev>▸</span></summary><div class=subbody>${inner}</div></details>`;
}
/* ---- engagement tracker (CRM-lite, localStorage) ---- */
const TRK_STATUS=['—','Not started','Researching','Outreach sent','Meeting set','Met','Committed','Declined'];
const TRK_COL={'Committed':'#34d399','Met':'#2dd4bf','Meeting set':'#7dd3fc','Outreach sent':'#fcd9a8','Researching':'#9ca3af','Not started':'#6e7681','Declined':'#ef4444'};
function trackerHTML(m){
  const t=(MAN[m.name]&&MAN[m.name].tracker)||{}, cur=t.status||'—';
  return `<div class=eyebrow>Engagement tracker <span style=color:var(--faint);font-weight:400;text-transform:none>· saved on this device, included in Export notes</span></div>
    <div class=trackgrid>
      <label>Status<select class=trk data-f=status>${TRK_STATUS.map(s=>`<option ${s===cur?'selected':''}>${s}</option>`).join('')}</select></label>
      <label>Last contact<input class=trk data-f=last type=date aria-label="Last contact date" value="${esc(t.last||'')}"></label>
      <label>Owner<input class=trk data-f=owner aria-label="Relationship owner" value="${esc(t.owner||'')}" placeholder="lead staffer"></label>
      <label style=grid-column:1/-1>Next step<input class=trk data-f=next aria-label="Next relationship step" value="${esc(t.next||'')}" placeholder="e.g. schedule interchange meeting before markup"></label>
    </div>`;
}
function wireTracker(m){
  document.querySelectorAll('#trackcard .trk').forEach(el=>el.onchange=()=>{
    MAN[m.name]=MAN[m.name]||{}; MAN[m.name].tracker=MAN[m.name].tracker||{};
    MAN[m.name].tracker[el.dataset.f]=el.value; saveMAN();
  });
}
/* ----- section renderers ----- */
function stfCard(sf,office){
  const st=Object.assign({},sf.st||{},stf(sf.id));   // baked file state (staff_state.json) under localStorage overlay (run-4r)
  const starred=st.star!=null?st.star:sf.star;
  const sourceId=`quorum:${sf.id}`;
  const peopleHref=`/?v=staffer&s=${encodeURIComponent(office)}&person=${encodeURIComponent(sourceId)}&source_only=1`;
  const media=(st.media||[]).map((mm,k)=>`<span class=chip>${safeUrl(mm.u)?`<a href="${safeUrl(mm.u)}" target=_blank rel=noopener>${esc(mm.l||mm.u)}</a>`:`<span>${esc(mm.l||mm.u)}</span>`}<span class=x data-act=delmedia data-sid=${sf.id} data-k=${k}>×</span></span>`).join('');
  return `<div class="stfcard${st.gold?' gold':''}" data-sid=${sf.id}>
    <div class=stfhead><span class="stfstar${starred?' on':''}" data-act=star data-sid=${sf.id} title="${esc(sf.why||'industry-relevant')} — click to toggle">${starred?'★':'☆'}</span><a class=staffer-people-link href="${peopleHref}" data-staffer-source="${attr(sourceId)}" data-staffer-office="${attr(office)}" title="Open ${attr(sf.n)} in People and prepare path finding">${esc(sf.n)}</a><span class=stftitle>${esc(sf.t)}</span>${starred?'<span style="font-size:9px;font-weight:700;color:#c2862a;border:1px solid #c2862a66;border-radius:8px;padding:0 5px;margin-left:6px">CU-relevant</span>':''}</div>
    ${(sf.dom&&sf.dom.length)?`<div style="margin:2px 0 2px 20px">${sf.dom.map(dd=>{const cu=/banking|financial|credit union|consumer|housing|\btax|small business|insurance|securities|fintech|monetary|treasur|econom/i.test(dd);return `<span style="font-size:9.5px;border-radius:8px;padding:0 6px;margin-right:4px;border:1px solid ${cu?'#c2862a66':'var(--line,#433317)'};color:${cu?'#c2862a':'var(--faint)'}">${esc(dd)}</span>`;}).join('')}<span style="font-size:9px;color:var(--muted)"> ${sf.dsrc==='quorum'?'':'· from title'}</span></div>`:''}
    <div class=stfmeta>${sf.e?`<a href="mailto:${esc(sf.e)}">${esc(sf.e)}</a>`:''}${sf.e&&sf.p?' · ':''}${sf.p?`<a href="tel:${esc(sf.p)}">${esc(sf.p)}</a>`:''}</div>
    <div class=stfctl>
      <button class="mini${st.gold?' on':''}" data-act=gold data-sid=${sf.id} title="contact established — gold outline">${st.gold?'✓ Contacted':'Mark contacted'}</button>
      <button class="mini${st.meet?' on':''}" data-act=meet data-sid=${sf.id} title="show in the In-contact panel">${st.meet?'🤝 Meeting set':'Set up meeting'}</button>
      <button class=mini data-act=note data-sid=${sf.id}>Note</button>
      <button class=mini data-act=media data-sid=${sf.id}>+ Media</button>
    </div>
    <div class=stfnote ${st.note?'':'hidden'}><textarea class=stfnotebox data-sid=${sf.id} placeholder="notes…">${esc(st.note||'')}</textarea></div>
    <div class=stfmedia>${media}</div>
  </div>`;
}
function staffHTML(m){
  const note=manField(m.name,'staff',m.staff);const files=filesOf(m.name);
  const dir=(m.staffdir||[]);
  const nrel=dir.filter(s=>{const t=Object.assign({},s.st||{},stf(s.id));return t.star!=null?t.star:s.star;}).length;
  const ncon=dir.filter(s=>Object.assign({},s.st||{},stf(s.id)).gold).length;
  const dirHTML=dir.length?`<div style="font-size:11px;color:var(--faint);margin-bottom:4px">${dir.length} staffers · <b style="color:#c2862a">${nrel} CU-relevant</b> · ${ncon} contacted <span style="float:right">interaction marks save on this device — use ⭳ Export staff state (In-contact panel), drop <b>staff_state.json</b> in the Melange Systems folder to make them permanent</span></div>
    <input class=stfsearch data-stfsearch aria-label="Filter staffers by name or title" placeholder="filter ${dir.length} staffers (name/title)…">
    <div class=stflist>${dir.map(sf=>stfCard(sf,m.name)).join('')}</div>`:`<div class=empty>No staffer directory rows matched for this office.</div>`;
  return `<div id=staff-view>${dirHTML}<hr style="border-color:var(--line,#433317);margin:10px 0">
  ${note?`<div class=savednote>${esc(note)}</div>`:`<div class=empty>No office-level staff notes yet.</div>`}
    ${files.length?`<div class=filelist>`+files.map((f,k)=>`<div class=fileitem>${/^data:/i.test(f.data)?`<a href="${attr(f.data)}" download="${esc(f.name)}">📎 ${esc(f.name)}</a>`:`<span>📎 ${esc(f.name)}</span>`}<span class=del data-f=${k} data-field=staff>remove</span></div>`).join('')+`</div>`:''}
    <button class=btn data-edit=staff>${note?'Edit':'Add'} note</button><label class=btn for=file-staff style=cursor:pointer>Upload file</label><input id=file-staff type=file data-field=staff hidden multiple>
    <div id=staff-edit hidden><textarea class=notebox data-field=staff>${esc(note||'')}</textarea><button class=btn data-save=staff>Save</button><button class="btn ghost" data-cancel=staff>Cancel</button></div></div>`;
}
function respHTML(m){
  const P=m.psy||{};
  const READS={'agree-susceptible':['Consistently open to meetings','#16a34a'],'agree-resistant':['Consistently guarded','#ef4444'],'diverge':['Signals disagree — worth confirming in person','#f59e0b'],'mixed':['Mixed reads','#8b949e'],'both-neutral':['Both reads neutral','#8b949e'],'trait-only':['Behavioral read only — no persona coverage','#6b7280']};
  const rd=READS[P.susc_read||'trait-only']||READS['trait-only'];
  const suscRows=(P.pressure_mult!=null)?`<div class=mrow><span>Meeting take-rate</span><b>${P.seed_prob!=null?Math.round(P.seed_prob*100)+'%':'—'}</b></div>
      <div class=mrow><span>${P.basis==='behavioral'?'Record-based':'Presentation-based'}</span><b>${P.susc_trait_z>0?'+':''}${P.susc_trait_z}</b></div>
      ${P.persona_susc_z!=null?`<div class=mrow><span>Presentation-based (public)</span><b>${P.persona_susc_z>0?'+':''}${P.persona_susc_z}</b></div>`:''}
      <div style="font-size:11px;font-weight:700;color:${rd[1]};background:${rd[1]}22;border:1px solid ${rd[1]}55;padding:2px 8px;border-radius:10px;display:inline-block;margin-top:4px">${rd[0]}</div>`:'';
  const appr=P.approach?`<div class=eyebrow style=margin-top:8px>Quick read</div><div style="font-size:12px;line-height:1.45;color:var(--ink,#D2CDBC);background:#2E2514;border:1px solid var(--line,#433317);border-radius:8px;padding:7px 9px">${esc(P.approach)}</div>`:'';
  return `${suscRows}${appr}`;
}
/* Approach brief. Promoted out of psychHTML (Paul 2026-08-31) so it is the first thing
   inside 'Profile' instead of two folds deep. openMember renders it as BARE content,
   NOT wrapped in sub(): wrapping it would put it back behind a second click. Markup,
   colour literals, heading strings and the withheld state are the psychHTML originals,
   verbatim -- this function renders the same bytes psychHTML used to render.
   Returns '' when the member has neither a narrative nor a narrative_status, so a member
   with no brief contributes no node and no whitespace at all to the top of the group.
   The three paragraphs go through esc() like every other member-sourced field in this file;
   esc() replaces [&<>] only, so clean prose renders byte-for-byte as before. */
function narrativeHTML(m){
  const P=m.psy||{};
  const NAR=P.narrative;
  if(NAR&&NAR.format==='descriptive-v3')return `<div class=eyebrow style=margin-top:10px>Profile summary</div>
      <div style="font-size:12px;line-height:1.52;color:var(--ink,#D2CDBC);background:#2E2514;border:1px solid var(--line,#433317);border-radius:8px;padding:10px 12px">
        <div style="color:#7dd3fc;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px">Public record</div><div style=margin-bottom:8px>${esc(NAR.p1)}</div>
        <div style="color:#7dd3fc;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px">Public communication</div><div style=margin-bottom:8px>${esc(NAR.p2)}</div>
        <div style="color:#7dd3fc;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px">Context and evidence</div><div>${esc(NAR.p3)}</div>
      </div>`;
  const MP=NAR&&NAR.meeting_playbook;
  const LF=MP&&MP.observed_longform;
  const longform=LF?`<div style="margin:7px 0;padding:7px 8px;background:#172033;border-left:3px solid #7dd3fc;border-radius:4px">
        <div style="color:#7dd3fc;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.05em">Reviewed long-form example</div>
        <div>${safeUrl(LF.url)?`<a href="${safeUrl(LF.url)}" target=_blank rel=noopener>${esc(LF.title||'Source')}</a>`:esc(LF.title||'Source')} · ${esc(LF.recorded_at||LF.published||'date unavailable')} · ${esc((LF.source_era||'').replaceAll('_',' '))}</div>
        <div>${esc(LF.summary||'')}</div>${(LF.findings||[]).map(x=>`<div style="margin-top:4px"><b>Observed:</b> ${esc(x.finding)}${x.meeting_implication?` <b>Meeting use:</b> ${esc(x.meeting_implication)}`:''}</div>`).join('')}
        <div class=muted style="margin-top:4px">${esc(LF.scope||'')}</div>
      </div>`:'';
  const playbook=MP?`<div style="margin-bottom:10px;padding-bottom:9px;border-bottom:1px solid var(--line,#433317)">
        <div style="color:#EECD5C;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">In the room</div>
        <div style=margin-bottom:3px><b>Open:</b> ${esc(MP.open)}</div>
        <div style=margin-bottom:3px><b>Bring:</b> ${esc(MP.bring)}</div>
        <div style=margin-bottom:3px><b>Run the room:</b> ${esc(MP.run_the_room)}</div>${longform}
        <div><b>Close:</b> ${esc(MP.close)}</div>
      </div>`:'';
  return NAR?`<div class=eyebrow style=margin-top:10px>Suggested approach</div>
      <div style="font-size:12px;line-height:1.52;color:var(--ink,#D2CDBC);background:#2E2514;border:1px solid var(--line,#433317);border-radius:8px;padding:10px 12px">
        ${playbook}<div style="color:#7dd3fc;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px">Working read — public evidence, not a private prediction</div><div style=margin-bottom:8px>${esc(NAR.p1)}</div>
        <div style="color:#7dd3fc;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px">What you may hear</div><div style=margin-bottom:8px>${esc(NAR.p2)}</div>
        <div style="color:#7dd3fc;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px">Why this approach</div><div>${esc(NAR.p3)}</div>
      </div>`:(P.narrative_status?`<div class=eyebrow style=margin-top:10px>Suggested approach</div><div class=empty>Approach brief pending.</div>`:'');
}
function speechHTML(m){
  const S=m.speech_observed,U=m.speech_profile;
  if(!S&&!U)return '<div class=empty>Speech profile unavailable for this member.</div>';
  const C=S&&S.coverage||{},H=S&&S.headline_interviews_only||null,labels={washington_journal:'Washington Journal',npr:'NPR interview',sunday_show:'Sunday show',longform_podcast:'Long-form podcast',house_floor:'House floor'};
  const n=v=>v==null?'n/a':Number(v).toFixed(2).replace(/\.00$/,'');
  const pct=v=>{if(v==null)return 'n/a';const n=Math.round(Number(v)),r=n%100,s=(r>=11&&r<=13)?'th':({1:'st',2:'nd',3:'rd'}[n%10]||'th');return `${n}${s} pct`;};
  const tiers=Object.entries(C.identity_tiers||{}).map(([k,v])=>`${esc(k.replaceAll('_',' '))}: ${Number(v).toLocaleString()}`).join(' · ');
  const W=U&&U.general_word_habits&&U.general_word_habits.raw_text_observations||{},I=U&&U.interaction_habits||null,G=U&&U.general_audio_habits||null,V=U&&U.verified_voiceprint_baseline||null,L=U&&U.longform_interview_evidence||null,LD=U&&U.longform_measured_delivery||null;
  const interaction=I?Object.entries(I.measures||{}).filter(([key,x])=>key!=='defense'&&x&&!x.neutral).map(([,x])=>`<div class=mrow><span>${esc(x.definition)}</span><b>${esc(x.label)}</b></div>`).join(''):'';
  // Two complementary audio layers on DIFFERENT SCALES. G carries raw per-clip means and
  // has a percentile comparison; V carries z scores standardized inside its own source and
  // has none. They render as separate blocks with different columns so the page can never
  // present a z score and a raw mean under one "Comparison" heading.
  const CHLABEL={pause:'Mean unvoiced pause',pace:'Voiced-segment pace',volume_dyn:'Loudness variability',pitch_var:'Normalized pitch variability',strain:'Local jitter (non-clinical)',f0_mean:'Mean F0'};
  const channels=G?Object.entries(G.channels||{}).map(([key,x])=>`<tr><td>${esc(CHLABEL[key]||key)}</td><td>${n(x.value)}</td><td>${esc(x.band&&x.band.label||'descriptive only')}</td></tr>`).join(''):'';
  const vchannels=V?Object.entries(V.standardized_channels||{}).map(([key,x])=>`<tr><td>${esc(CHLABEL[key]||key)}</td><td>${n(x)}</td></tr>`).join(''):'';
  const fc=G&&G.floor_composition||null,fk=fc&&fc.composition;
  const gHead=fk==='floor_only'?'Audio baseline · House floor recordings only'
    :fk==='floor_dominant'?'Audio baseline · mostly House floor recordings'
    :'General audio baseline · all accepted settings';
  const gNote=(fk==='floor_only'||fk==='floor_dominant')
    ?'Floor microphones and speaking format differ materially from interview settings. These values are comparable to other floor recordings, not to interview delivery.'
    :(fc&&fc.floor_clips?`${Number(fc.floor_clips).toLocaleString()} of ${Number(fc.total_clips).toLocaleString()} clips are House-floor recordings, where microphones and speaking format differ from interview settings.`:'');
  const audioBlocks=[
    G?`<div class=eyebrow style=margin-top:12px>${esc(gHead)}</div>${gNote?`<div style="font-size:10px;color:var(--faint);line-height:1.5;margin:2px 0 6px">${esc(gNote)}</div>`:''}<div class=mrow><span>Accepted audio</span><b>${Number(G.clips||0).toLocaleString()} clips · ${n(G.minutes)} min · ${esc((G.coverage_strength||'limited').replaceAll('_',' '))}</b></div><div style="overflow:auto"><table class=tbl><thead><tr><th>Channel</th><th>Value</th><th>Comparison</th></tr></thead><tbody>${channels}</tbody></table></div><div style="font-size:10px;color:var(--faint);line-height:1.5;margin-top:3px">Values are means over accepted clips; the comparison column places this member among the members measured the same way.</div>`:'',
    V?`<div class=eyebrow style=margin-top:12px>Audio baseline · verified voiceprint, not clip-segmented</div><div style="font-size:10px;color:var(--faint);line-height:1.5;margin:2px 0 6px">${n(V.minutes)} min of identity-verified audio, standardized within source. Values are z-scores and are not directly comparable to the clip-based measures ${G?'shown above':'shown for other members'}.</div><div class=mrow><span>Verified audio</span><b>${n(V.minutes)} min · ${esc((V.coverage_strength||'limited').replaceAll('_',' '))}</b></div><div style="overflow:auto"><table class=tbl><thead><tr><th>Channel</th><th>Standardized value (z)</th></tr></thead><tbody>${vchannels}</tbody></table></div>`:''
  ].filter(Boolean).join('');
  const lfc=LD&&LD.candidate_only||{},lfb=LD&&LD.qualified_baseline||{},lfr=LD&&LD.qualified_role_conditioned||{};
  const longformSources=(L&&L.source_records||[]).map(x=>safeUrl(x.url)?`<a href="${safeUrl(x.url)}" target=_blank rel=noopener>${esc(x.title||x.channel||'Source')}</a>`:esc(x.title||x.channel||'Source')).join(' · ');
  const longformQualified=(Number(lfb.turns||0)+Number(lfr.turns||0))>0;
  const longform=LD?(longformQualified
    ?`<div class=eyebrow style=margin-top:12px>Long-form complement · source-bound review passed</div><div class=mrow><span>Qualified complete turns</span><b>${Number(lfb.turns||0).toLocaleString()} baseline · ${Number(lfr.turns||0).toLocaleString()} current-role conditioned</b></div>${longformSources?`<div style="font-size:10px;color:var(--faint);line-height:1.5;margin-top:3px">${longformSources}</div>`:''}`
    :`<div class=eyebrow style=margin-top:12px>Long-form complement · review pending</div><div style="font-size:10px;color:var(--faint);line-height:1.55;margin:2px 0 6px">${Number(lfc.turns||0).toLocaleString()} complete ${Number(lfc.turns||0)===1?'turn is':'turns are'} retained as candidate evidence (${n(lfc.minutes)} min). ${Number(lfc.turns||0)===1?'It does':'They do'} not modify the clip baseline, verified voiceprint, psychometrics, profile prose, or meeting guidance until the source-bound review passes.</div>${longformSources?`<div style="font-size:10px;line-height:1.5">${longformSources}</div>`:''}`):'';
  const defensive=U&&U.repeated_defensive_reactions,topics=U&&U.supported_topic_associations||[];
  const universal=U?`<div style="font-size:11px;line-height:1.5;color:var(--ink,#D2CDBC);background:#243223;border:1px solid #3f6b45;border-radius:8px;padding:8px 10px;margin-bottom:10px"><b style=color:#86efac>Speech profile: ${esc(U.status||'PASS')}</b> · Every accepted setting contributes to the general baseline. Thin coverage limits claim strength; it does not fail the member.</div>
    <div class=eyebrow>Public-word &amp; interaction baseline</div><div class=mrow><span>Attributed public words</span><b>${Number(W.interview_words||0).toLocaleString()} words · ${Number(W.interview_turns||0).toLocaleString()} turns · ${Number(W.appearances||0).toLocaleString()} appearances</b></div>
    ${interaction||'<div class=empty>No non-neutral interaction habit is strong enough to highlight.</div>'}
    ${defensive?`<div class=mrow><span>Repeated explicit floor-defense language <small>· ${Number(defensive.independent_programs||0)} independent programs</small></span><b>${esc(defensive.band&&defensive.band.label||'supported')}</b></div>`:''}
    ${audioBlocks||'<div class=empty>Audio coverage is currently too limited for a general acoustic comparison. The word/interaction speech profile still passes.</div>'}${longform}
    ${topics.length?`<div class=eyebrow style=margin-top:12px>Repeated public topic associations</div>${topics.map(x=>`<div class=mrow><span>${esc(x.topic||'topic')}</span><b>association only · not a proven trigger</b></div>`).join('')}`:''}`:'';
  const head=!S?'':H?`<div class=eyebrow>Interview-only detail <span style="color:#86efac;font-weight:700">· House floor excluded</span></div>
    <div class=mrow><span>Observed interview audio</span><b>${H.clips.toLocaleString()} clip${H.clips===1?'':'s'} · ${n(H.minutes)} min · ${H.episodes} episode${H.episodes===1?'':'s'}</b></div>
    <div class=mrow><span>Typical pitch (F0 semitones above 27.5 Hz)</span><b>${n(H.f0_mean_mean)}</b></div>
    <div class=mrow><span>Normalized pitch variability</span><b>${n(H.pitch_var_mean)} · ${pct(H.pitch_var_percentile)}</b></div>
    <div class=mrow><span>Volume dynamics</span><b>${n(H.volume_dyn_mean)} · ${pct(H.volume_dyn_percentile)}</b></div>
    <div class=mrow><span>Voiced-segment pace</span><b>${n(H.pace_mean)}/sec · ${pct(H.pace_percentile)}</b></div>
    <div class=mrow><span>Mean unvoiced pause</span><b>${n(H.pause_mean)} sec · ${pct(H.pause_percentile)}</b></div>`
    :'<div class=empty>No interview-setting audio in the detailed descriptive snapshot; any House-floor coverage below is context-only.</div>';
  const settingRows=Object.entries(S&&S.by_setting||{}).sort((a,b)=>a[0].localeCompare(b[0])).map(([key,v])=>`<tr><td>${esc(labels[key]||key)}</td><td>${v.clips.toLocaleString()}</td><td>${n(v.minutes)}</td><td>${n(v.f0_mean_mean)}</td><td>${n(v.pitch_var_mean)}</td><td>${n(v.volume_dyn_mean)}</td></tr>`).join('');
  const settings=settingRows?`<div class=eyebrow style=margin-top:12px>Setting breakdown</div><div style="overflow:auto"><table class=tbl><thead><tr><th>Setting</th><th>Clips</th><th>Min</th><th>Pitch</th><th>Pitch var.</th><th>Volume dyn.</th></tr></thead><tbody>${settingRows}</tbody></table></div>`:'';
  const contrasts=(S&&S.setting_contrasts||[]).slice(0,8).map(x=>{const d=x.delta_first_minus_second||{};return `<div class=mrow><span>${esc(labels[x.first]||x.first)} minus ${esc(labels[x.second]||x.second)} <small>(${x.first_clips}/${x.second_clips} clips)</small></span><b>pitch ${n(d.f0_mean)} · pitch var. ${n(d.pitch_var)} · volume ${n(d.volume_dyn)}</b></div>`;}).join('');
  const subjects=(S&&S.subject_effects||[]).slice(0,8).map(x=>`<div class=mrow><span>${esc(x.topic)} <small>· ${x.reactions} WJ reactions</small></span><b>pitch Δ ${x.mean_f0_delta_semitones>0?'+':''}${n(x.mean_f0_delta_semitones)} st · loudness Δ ${x.mean_loudness_delta>0?'+':''}${n(x.mean_loudness_delta)}</b></div>`).join('');
  const caveats=(S&&S.caveats||[]).map(x=>`<div>• ${esc(x)}</div>`).join('');
  return `${universal}${S?`<div style="font-size:11px;line-height:1.5;color:var(--ink,#D2CDBC);background:#2E2514;border:1px solid var(--line,#433317);border-radius:8px;padding:8px 10px;margin:10px 0"><b>Detailed descriptive snapshot.</b> These observations never modify Big Five, rank, stance, persuasion, or allocation. Setting and subject differences are associations, not causal effects.</div>
    <div class=mrow><span>Accepted, cut, hash/PCM-verified snapshot coverage</span><b>${C.clips.toLocaleString()} clips · ${n(C.minutes)} min · ${C.setting_count} settings</b></div>
    <div style="font-size:10px;color:var(--faint);margin-bottom:8px">Identity tiers: ${tiers||'n/a'}</div>${head}${settings}${contrasts?`<div class=eyebrow style=margin-top:12px>Within-member setting contrasts</div>${contrasts}`:''}${subjects?`<div class=eyebrow style=margin-top:12px>Observed subject reactions <span style=color:#f59e0b>· Washington Journal taxonomy is unadjudicated</span></div>${subjects}`:''}${caveats?`<div style="font-size:10px;color:var(--faint);line-height:1.5;margin-top:10px">${caveats}</div>`:''}`:''}`;
}
function psychHTML(m){
  const note=manField(m.name,'psych',m.psychometrics);
  const P=m.psy||{};
  const pctLabel=v=>{const n=Math.round(v*100),m=n%100,s=(m>=11&&m<=13)?'th':({1:'st',2:'nd',3:'rd'}[n%10]||'th');return `${n}${s} pct`;};
  const bar=(l,v,c)=>`<div class=mrow><span>${l}</span><b>${pctLabel(v)}</b></div><div style="height:7px;background:#433317;border-radius:4px;overflow:hidden;margin:2px 0 8px"><i style="display:block;height:100%;width:${Math.round(v*100)}%;background:${c}"></i></div>`;
  let comp='<div class=empty>No computed psychometrics for this member (no text corpus).</div>';
  if(P.big5_pct||P.behav_pct||P.communication_style){
    // Accepted decision profile: five observable behavioral roles. Neuroticism belongs only to the
    // separate Big-Five display below; public exposure remains separately labeled context.
    const BLAB=['Cross-party cooperation','Follow-through','Conversational dominance','Media appearances','Issue breadth'];
    /* 2026-07-04 user directive: TWO radar charts — Behavior and Big-5, same geometry. n/a axes plot
       at the 0.5 neutral ring (never fake-low) and are listed under the chart. */
    const bna=P.behav_pct?BLAB.filter((_,i)=>P.behav_pct[i]==null):[];
    const behav=P.behav_pct
      ?radar(P.behav_pct.map(v=>v==null?0.5:v),BLAB,'#2dd4bf',82)
        +(bna.length?`<div style="font-size:10px;color:var(--faint)">n/a (plotted at neutral): ${bna.join(', ')}</div>`:'')
      :'<div class=empty>No behavioral coverage.</div>';
    const roles2=(P.expressiveness_v2_pct!=null&&P.behav_pct&&P.behav_pct[2]!=null)
      ?`<div class=eyebrow style="margin-top:8px">Conversation dynamics <span style="color:#86efac;font-weight:700">&middot; observed interviews</span></div>
        ${bar('Conversational dominance',P.behav_pct[2],'#2dd4bf')}
        ${bar('Verbal expressiveness',P.expressiveness_v2_pct,'#a78bfa')}
        <div style="font-size:10px;color:var(--faint);margin-top:-3px">${P.behavior_scope&&P.behavior_scope.interaction_turns?P.behavior_scope.interaction_turns.toLocaleString():0} qualifying turns.</div>`
      :'<div style="font-size:10px;color:var(--faint);margin-top:6px">Conversation dynamics: insufficient observed interview coverage.</div>';
    const CS=P.communication_style||null;
    const styleFacet=(key,label,color)=>CS&&CS.facets&&CS.facets[key]&&CS.facets[key].percentile!=null
      ?bar(label,CS.facets[key].percentile,color):'';
    let style='';
    if(CS){
      const raw=CS.raw_text||{};const ex=CS.public_exposure||{};
      const role=ex.formal_role||(ex.committee_leader?'committee leadership':'no listed central or committee leadership office');
      const exposure=(ex.observed_visibility_percentile!=null&&ex.role_adjusted_percentile!=null)
        ?`<div style="font-size:11px;line-height:1.5;color:var(--ink,#D2CDBC);background:#2E2514;border:1px solid var(--line,#433317);border-radius:8px;padding:7px 9px;margin-bottom:7px"><b>Public exposure context:</b> ${pctLabel(ex.observed_visibility_percentile)} observed; <b>${pctLabel(ex.role_adjusted_percentile)} after formal-role adjustment</b> (${esc(role)}).</div>`:'';
      const rawline=raw.mean_words_per_turn!=null
        ?`<div style="font-size:10px;color:var(--faint);line-height:1.55;margin-top:3px">Actual member-attributed interview text: <b>${raw.mean_words_per_turn}</b> mean words/turn, <b>${raw.median_words_per_turn}</b> median, <b>${raw.p90_words_per_turn}</b> 90th percentile; <b>${raw.mean_words_per_appearance}</b> mean words/appearance. Warmth cues: <b>${raw.warmth_cues_per_1000_words}</b>/1,000 words; positive-energy/excitement cues: <b>${raw.excitement_cues_per_1000_words}</b>/1,000; joint substantial appearances: <b>${raw.joint_appearance_share==null?'n/a':Math.round(raw.joint_appearance_share*100)+'%'}</b>.</div>`:'';
      style=`<div class=eyebrow style="margin-top:9px">Observed communication style <span style="color:#86efac;font-weight:700">&middot; words and interaction behavior</span></div>${exposure}${styleFacet('warmth','Warmth cues','#2dd4bf')}${styleFacet('excitement','Positive energy / excitement cues','#f59e0b')}${styleFacet('activity','Within-appearance activity','#60a5fa')}${styleFacet('gregariousness','Joint-appearance gregariousness','#a78bfa')}${styleFacet('assertiveness','Conversational assertiveness','#fb7185')}${rawline}`;
    }
    const hearA=(P.hearing_antag_pct!=null)
      ?`<b>Hearing low-antagonism (separate facet):</b> ${pctLabel(P.hearing_antag_pct)} — inverse of observed committee-hearing antagonism (interruptions given, chair defiance, friction).<br>`
      :'';
    const aval=(P.a_validated_pct!=null)
      ?`<div class=eyebrow style="margin-top:8px">Cooperation &amp; low-antagonism</div>
        <div style="font-size:11px;line-height:1.5;color:var(--ink,#D2CDBC);background:#2E2514;border:1px solid var(--line,#433317);border-radius:8px;padding:7px 9px">
        <b>Cross-party cooperation:</b> ${P.behav_pct&&P.behav_pct[0]!=null?pctLabel(P.behav_pct[0]):'n/a'} — share of observable bipartisan cosponsorship behavior.<br>
        <b>Low-antagonism A facet:</b> ${pctLabel(P.a_validated_pct)} — absence of hostility/contempt in observed interviews.<br>
        ${hearA}</div>`
      :(P.hearing_antag_pct!=null
        ?`<div class=eyebrow style="margin-top:8px">Cooperation &amp; low-antagonism</div>
          <div style="font-size:11px;line-height:1.5;color:var(--ink,#D2CDBC);background:#2E2514;border:1px solid var(--line,#433317);border-radius:8px;padding:7px 9px">
          <b>Cross-party cooperation:</b> ${P.behav_pct&&P.behav_pct[0]!=null?pctLabel(P.behav_pct[0]):'n/a'} — share of observable bipartisan cosponsorship behavior.<br>
          <b>Interview low-antagonism A facet:</b> insufficient interview coverage for this member.<br>
          ${hearA}</div>`
        :'<div style="font-size:10px;color:var(--faint);margin-top:6px">Low-antagonism A facet: insufficient interview coverage.</div>');
    const B5AX=['Sociability','Openness','Diligence','Cooperativeness','Neuroticism'];
    // Estimate fill: gold-anchored values stay primary; empty slots render the
    // labeled estimate layer (big5_fb_pct) instead of vanishing. Estimates are display-only.
    const FB=P.big5_fb_pct||[];
    const goldVec=P.big5_pct||[null,null,null,null,null];
    const merged=goldVec.map((v,i)=>v!=null?v:(FB[i]!=null?FB[i]:null));
    const est=B5AX.filter((_,i)=>goldVec[i]==null&&FB[i]!=null);
    const b5na=B5AX.filter((_,i)=>merged[i]==null);
    const rad=merged.some(v=>v!=null)?radar(merged.map(v=>v==null?0.5:v),B5AX,'#7dd3fc',82)
      +(est.length?`<div style="font-size:10px;color:#f59e0b">Estimate slots (not gold-anchored): ${est.join(', ')}.</div>`:'')
      +(b5na.length?`<div style="font-size:10px;color:var(--faint)">Axes with no displayable source (not plotted): ${b5na.join(', ')}</div>`:''):'';
    const persona=(P.communion_pct!=null)?bar('Warmth — how warm they read',P.communion_pct,'#2dd4bf')+bar('Assertiveness — how forceful they read',P.agency_pct,'#c2862a'):'<div class=empty>No persona cue coverage yet for this member.</div>';
    comp=`<div class=gen style=margin-bottom:12px>
      <div><div class=eyebrow>Decision profile <span style="color:#86efac;font-weight:700">· five observable behavioral roles</span></div>${behav}
      <div style="font-size:10px;color:var(--faint);margin:3px 0 6px">Cooperation is cross-party legislative action; follow-through is roll-call/legislative execution; conversational dominance is observed interview behavior; media appearances is exposure; breadth is issue coverage.</div>${roles2}${style}${aval}
      ${rad?`<div class=eyebrow style="margin-top:8px">Personality read</div>
      ${rad}`:''}</div>
      <div><div class=eyebrow>Projected persona <span style=color:var(--faint)>· how they come across publicly</span></div>${persona}</div></div>`;
  }
  // EFFECTIVENESS DISPLAY = the measure the ENGINE ranks on (Paul 2026-08-03: "The display and the
  // engine should be the same."). do_z is CU legislative delivery, z within chamber, from
  // say_do_gap.py — the same quantity moneyball_core.delivery_mult feeds to flip_model,
  // per_topic_flip, per_topic_coalition and build_dashboard_bundle. It replaces classic CEL LES as
  // the headline because LES is passage-weighted and therefore majority-biased (2.29x, d=0.79);
  // LES is retained below as a labelled third-party comparison with that bias stated on the page.
  const eff=P.ext&&P.ext.do_pct!=null?`<div class=eyebrow style=margin-top:8px>Legislative effectiveness <span style=color:var(--faint)>· the measure this model ranks on</span></div>
    <div class=mrow><span>Credit-union bill delivery</span><b>${Math.round(P.ext.do_pct*100)}th pct</b></div>`:'';
  const ext=P.ext?`<div class=eyebrow style=margin-top:8px>External measures (public datasets)</div>
    ${P.ext.les!=null?`<div class=mrow><span>Vanderbilt lawmaking score <span style=color:var(--faint)>· third-party comparison</span></span><b>${P.ext.les.toFixed(2)} · ${Math.round(P.ext.les_pct*100)}th pct</b></div>
    <div style="font-size:11px;color:#f59e0b;margin-top:2px">Not used for ranking. CEL LES is passage-weighted and passage requires majority control: on the CEL panel 115–118 majority-party members average 2.29× the minority (Cohen d=0.79, rising to 2.87× in the 118th House), so a minority-party member reads low here regardless of their own record.</div>`:''}
    ${P.ext.dwnom1!=null?`<div class=mrow><span>Ideology, left to right</span><b>${P.ext.dwnom1>0?'+':''}${P.ext.dwnom1.toFixed(2)}</b></div>`:''}
    ${P.ext.kb_pct!=null?`<div class=mrow><span>Connections across Congress</span><b>${Math.round(P.ext.kb_pct*100)}th pct</b></div>`:''}
    ${P.ext.ft!=null?`<div class=mrow><span>Bill advancement</span><b>${P.ext.ft.toFixed(2)}</b></div>`:''}`:'';
  return `<div id=psych-view>${comp}${eff}${ext}
    <div class=eyebrow style=margin-top:6px>Notes</div>
    ${note?`<div class=savednote>${esc(note)}</div>`:`<div class=empty>No working-style notes yet. Add how they decide, what motivates them, and what lands.</div>`}
    <button class=btn data-edit=psych>${note?'Edit':'Add'} note</button>
    <div id=psych-edit hidden><textarea class=notebox data-field=psych>${esc(note||'')}</textarea><button class=btn data-save=psych>Save</button><button class="btn ghost" data-cancel=psych>Cancel</button></div></div>`;
}
function bioHTML(m){
  const b=m.bio||{};const rows=[];
  const add=(l,v)=>{if(v)rows.push(`<div class=mrow><span>${l}</span><b>${v}</b></div>`);};
  add('Full name',(b.first_name&&b.last_name)?[b.first_name,b.middle_name,b.last_name,b.suffix].filter(Boolean).join(' '):m.name);
  add('State',b.state);add('Born',b.birthday);add('Gender',b.gender==='M'?'Male':b.gender==='F'?'Female':b.gender);
  add('Congress ID',b.bioguide);
  const links=[];if(safeUrl(b.url))links.push(`<a href="${safeUrl(b.url)}" target=_blank rel=noopener>Official site</a>`);
  if(b.twitter)links.push(`<a href="https://x.com/${encodeURIComponent(b.twitter)}" target=_blank rel=noopener>X/@${esc(b.twitter)}</a>`);
  if(b.facebook)links.push(`<a href="https://www.facebook.com/${encodeURIComponent(b.facebook)}" target=_blank rel=noopener>Facebook</a>`);
  if(b.youtube)links.push(`<a href="https://www.youtube.com/user/${encodeURIComponent(b.youtube)}" target=_blank rel=noopener>YouTube</a>`);
  if(b.ballotpedia_id)links.push(`<a href="https://ballotpedia.org/${encodeURIComponent(b.ballotpedia_id)}" target=_blank rel=noopener>Ballotpedia</a>`);
  if(b.wikipedia_id)links.push(`<a href="${safeUrl((b.wikipedia||{}).url)||safeUrl('https://en.wikipedia.org/wiki/'+encodeURIComponent(b.wikipedia_id))}" target=_blank rel=noopener>Wikipedia</a>`);
  if(b.bioguide)links.push(`<a href="https://bioguide.congress.gov/search/bio/${encodeURIComponent(b.bioguide)}" target=_blank rel=noopener>Congress bio</a>`);
  const ws=(b.wikipedia||{}).summary||{};
  const wikiLabels={overview:'Overview',early_life:'Early life & education',career:'Career',family_personal:'Family & personal life',life_experiences:'Notable life experiences'};
  const wikiRows=Object.keys(wikiLabels).filter(k=>ws[k]).map(k=>`<div style="margin-top:10px"><div class=eyebrow>${wikiLabels[k]}</div><div style="font-size:12px;line-height:1.55">${esc(ws[k])}</div></div>`).join('');
  const wikiMeta=wikiRows?`<div style="font-size:10px;color:var(--faint);margin-top:9px">Extractive summary from the linked Wikipedia revision${b.wikipedia.revision_id?' '+esc(b.wikipedia.revision_id):''}${b.wikipedia.revision_timestamp?' · '+esc(String(b.wikipedia.revision_timestamp).slice(0,10)):''}. Display context only; not used in scoring.</div>`:'';
  const note=manField(m.name,'bio',m.bio_manual);
  return `${rows.join('')||'<div class=empty>No structured bio on file.</div>'}
    ${links.length?`<div style=margin-top:8px>${links.join(' · ')}</div>`:''}
    ${wikiRows}${wikiMeta}
    <div style=margin-top:12px><div class=eyebrow>Notes</div><div id=bio-view>${note?`<div class=savednote>${esc(note)}</div>`:'<div class=empty>Add background, career, district history, prior interactions.</div>'}
    <button class=btn data-edit=bio>${note?'Edit':'Add'} note</button>
    <div id=bio-edit hidden><textarea class=notebox data-field=bio>${esc(note||'')}</textarea><button class=btn data-save=bio>Save</button><button class="btn ghost" data-cancel=bio>Cancel</button></div></div></div>`;
}
const PERSCAT={family:'Family',favorite:'Favorites',books_media:'Books, film, TV & music',food:'Food & drink',
  inspiration:'Inspirations & mentors',quotes_values:'Quotes & guiding words',hobbies_sports:'Hobbies, sports & fandom',
  formative:'Formative experiences',faith:'Faith & practice',pets:'Pets',places_travel:'Places & travel',
  personal_routine:'Personal routines',other:'Other personal details'};
const BIO_FACT_STOP=new Set(['about','after','again','also','among','because','been','before','being','between','both','during','from','have','into','member','more','most','over','said','that','their','there','these','they','this','those','through','under','until','very','were','when','where','which','while','with','would']);
function factTerms(value){return new Set(String(value||'').toLowerCase().replace(/[^a-z0-9\s]/g,' ').split(/\s+/).filter(x=>x.length>=4&&!BIO_FACT_STOP.has(x)));}
function personalClaimInBiography(m,claim){
  const summary=(((m||{}).bio||{}).wikipedia||{}).summary||{},ct=factTerms(claim);
  if(ct.size<3)return false;
  const sentences=Object.values(summary).join(' ').split(/(?<=[.!?])\s+/);
  return sentences.some(sentence=>{const st=factTerms(sentence);if(st.size<3)return false;
    let shared=0;ct.forEach(term=>{if(st.has(term))shared++;});
    return shared>=3&&shared/Math.min(ct.size,st.size)>=.55;});
}
function personalHTML(m){
  const p=m.personal||{},allItems=p.items||[],eligible=allItems.filter(x=>x.display_eligible===true);
  const duplicates=[],items=[];eligible.forEach(x=>(personalClaimInBiography(m,x.claim)?duplicates:items).push(x));
  const hidden=allItems.length-eligible.length,dupes=duplicates.length;
  const suppressed=`${hidden?` ${hidden} automated catch-net excerpt${hidden===1?' was':'s were'} hidden by the personal-detail precision gate.`:''}${dupes?` ${dupes} detail${dupes===1?' was':'s were'} already covered in the biography and suppressed here.`:''}`;
  if(!items.length) return `<div class=empty>No additional speaker-verified personal details found in the current corpus.${suppressed}</div>`;
  const groups={};items.forEach(x=>(groups[x.category]||(groups[x.category]=[])).push(x));
  const src=x=>{const bits=[x.source,x.date].filter(Boolean).map(esc);const lab=bits.join(' · ')||'source';
    return safeUrl(x.url)?`<a href="${safeUrl(x.url)}" target=_blank rel=noopener>${lab} ↗</a>`:`<span>${lab}${x.locator?' · '+esc(x.locator):''}</span>`;};
  const cards=Object.keys(groups).sort((a,b)=>(PERSCAT[a]||a).localeCompare(PERSCAT[b]||b)).map(cat=>
    `<div class=eyebrow style="margin-top:12px">${esc(PERSCAT[cat]||cat)} <span style=color:var(--faint)>· ${groups[cat].length}</span></div>`+
    groups[cat].map(x=>`<div style="background:#2E2514;border:1px solid var(--line,#433317);border-radius:8px;padding:8px 10px;margin:5px 0">
      <div style="font-size:12px;font-weight:650;color:var(--ink,#D2CDBC)">${esc(x.claim)}</div>
      <details class=methnote style="margin:4px 0 0"><summary>View exact evidence</summary><div class=methbody>“${esc(x.evidence)}”<div style="margin-top:5px;color:var(--faint)">${src(x)} · ${esc(x.confidence)} confidence</div></div></details></div>`).join('')).join('');
  return `<div style="font-size:11px;color:var(--muted);margin-bottom:5px">${items.length} additional public, speaker-verified member details · exact excerpt and locator retained${hidden?` · ${hidden} catch-net excerpt${hidden===1?'':'s'} hidden by the precision gate`:''}${dupes?` · ${dupes} biography duplicate${dupes===1?'':'s'} suppressed`:''}</div>${cards}`;
}
function relHTML(m){
  const partners=m.partners||[];
  const maxw=Math.max(1,...partners.map(p=>p.w));
  const ptHTML=partners.length?partners.map(p=>{const col=PTCOL[{D:'Democrat',R:'Republican',I:'Independent'}[p.party]||'']||'#6b7280';
    return `<div class=partner data-i=${byName[p.name]??-1}><span class=pn>${mlink(p.name)}</span><span class=track style="height:8px;background:#433317;border-radius:4px;overflow:hidden"><i style="display:block;height:100%;width:${(p.w/maxw)*100}%;background:${col}"></i></span><span style=color:var(--muted);text-align:right>#${p.rank} · ${p.w}</span></div>`;}).join(''):'<div class=empty>No cosponsorship ties in the network for this member.</div>';
  const st=m.state;const sm=st?M.filter(x=>x.state===st&&x.name!==m.name).sort((a,b)=>(a.rank||9999)-(b.rank||9999)):[];
  const smHTML=sm.length?sm.map(x=>`<div class=partner data-i=${byName[x.name]}><span class=pn>${mlink(x.name)}</span><span style=color:var(--muted)>${esc(x.chamber)}</span><span style=color:var(--muted);text-align:right>#${x.rank??'—'}</span></div>`).join(''):'<div class=empty>No other members parsed for this state.</div>';
  const rf=m.refs||{};
  const rfRow=(r,arrow)=>{const col=r.net>0.15?'#34d399':(r.net<-0.15?'#ef4444':'#8b949e');
    const SRCL={CREC:'floor',CHRG:'hearing',INTERVIEW:'interview',PRESS:'press',X:'X'};
    return `<div class=partner data-i=${byName[r.who]??-1}><span class=pn>${arrow} ${mlink(r.who)}</span><span style="color:${col};font-weight:700">${r.net>0?'+':''}${r.net}</span><span style=color:var(--muted);text-align:right>${r.pos}+ / ${r.neg}&minus; · n=${r.n}</span></div>${r.q?`<div style="color:var(--faint);font-size:11px;margin:-2px 0 6px 4px">“…${esc(r.q)}…”${r.src?` <span style="color:var(--muted)">(${esc(SRCL[r.src]||r.src)})</span>`:''}</div>`:''}`;};
  const rofHTML=(rf.out||[]).map(r=>rfRow(r,'→')).join('')||'<div class=empty>No scored references by this member in the speech/interview/social corpus.</div>';
  const rifHTML=(rf['in']||[]).map(r=>rfRow(r,'←')).join('')||'<div class=empty>No scored references to this member.</div>';
  return `<div class=eyebrow>Top cosponsorship partners</div>${ptHTML}
    <div class=eyebrow style=margin-top:14px>Cross-media references — makes <span style=color:var(--faint)>· green +, red − · quote window centered on the mention · floor speeches, hearings, interviews, press, X</span></div>${rofHTML}
    <div class=eyebrow style=margin-top:14px>Cross-media references — receives</div>${rifHTML}
    <div class=eyebrow style=margin-top:14px>Same-state delegation — ${esc(st||'?')}</div>${smHTML}`;
}
const RELCOL={Highly:'#16a34a',Moderately:'#f59e0b',Slightly:'#8b949e'};
function committeeHTML(m){
  const cs=m.committees||[];
  if(!cs.length) return '<div class=empty>No committee assignments parsed for this member.</div>';
  // full committees, jurisdiction-relevant first (Highly>Moderately>Slightly), full committees before subs
  const rank={Highly:0,Moderately:1,Slightly:2};
  const rows=cs.slice().sort((a,b)=>(rank[a.rel]??9)-(rank[b.rel]??9)).map(c=>{
    const rc=RELCOL[c.rel]||'#6b7280';
    const role=c.role?`<span style="font-size:11px;font-weight:700;color:#1D1912;background:#cbb46a;padding:1px 6px;border-radius:4px;margin-left:6px">${esc(c.role)}</span>`:'';
    return `<div class=arow><span class=aname>${esc(c.name)}${role}</span><span style="font-size:11px;font-weight:700;color:${rc};background:${rc}22;border:1px solid ${rc}55;padding:1px 8px;border-radius:10px">${esc(c.rel)}</span></div>`;}).join('');
  const sc=m.committee_score!=null?(+m.committee_score).toFixed(2):'—';
  return `<div class=eyebrow>Committee &amp; subcommittee seats <span style=color:var(--faint)>· badge = jurisdictional relevance to CU issues · committee score ${sc}</span></div>${rows}`;
}
/* Plain-language rendering of the structural-adjustment 'reason' enum that
   fec/district/demographic_potential.py::_structural_adjustment stores. DISPLAY ONLY - the stored
   enum, the residual and the adjusted percentile are all unchanged; only the words differ. An
   unmapped reason still prints its raw value rather than vanishing. */
const REACH_NOT_ADJUSTED={reach_evidence_missing:'no reach evidence',model_not_fit:'model not fitted'};
/* Wording pass on the producer's USASpending method note (fec/collect/fetch_district_employers.py,
   method_notes.usaspending), which is baked into the district-employers artifact as one lower-case
   semicolon run-on. DISPLAY ONLY, and an exact-shape match: any other string passes through
   untouched, so a producer reword cannot be silently rewritten here. */
const prettyEmployerMethod=s=>String(s).replace(
  /^place-of-performance top-10 federal recipients FY(\d{2,4}); booking\/HQ caveats apply\.?$/,
  'Place-of-performance top-10 federal recipients, FY$1. Booking and HQ caveats apply.');
function cookPviSourceHTML(){
  const meta=D.cook_pvi_meta||{};
  const url=String(meta.source_page_url||'');
  const link=/^https:\/\/www\.cookpolitical\.com\//i.test(url)?` <a href="${attr(url)}" target=_blank rel=noopener>Cook Political Report source ↗</a>`:'';
  return `Article published ${esc(meta.article_publication_date||'date unavailable')}; snapshot retrieved ${esc(meta.retrieval_date||'date unavailable')}.${link}`;
}
function cookPviHTML(m){
  const pvi=m.cook_pvi;
  if(!pvi)return '';
  const available=pvi.status==='available'&&typeof pvi.pvi_label==='string'&&!!pvi.pvi_label.trim();
  const status=available?'available':(['outside_scope','unmatched'].includes(pvi.status)?pvi.status:'unavailable');
  let body;
  if(available){
    const district=pvi.district_label||pvi.district_id||'District code unavailable';
    body=`<div class=dgrid style="margin-bottom:8px"><div class=dcell><div class=v>${esc(pvi.pvi_label)}</div><div class=l>${esc(district)} · rounded source label</div></div></div>
      <p class=empty>Historical presidential partisan lean relative to the nation; not an election forecast.</p>
      <p class=empty>Historical district-code match only. Current member and district boundaries are unverified.</p>`;
  }else{
    const message=pvi.status==='outside_scope'?'Outside scope: this snapshot covers voting House districts, with no Senate or territorial values.'
      :pvi.status==='unmatched'?'Unavailable: no district-code match in this source snapshot.'
      :'Unavailable: no Cook PVI source value loaded.';
    body=`<p class=empty>${message}</p>`;
  }
  return `<div class=cook-pvi-context data-cook-pvi-status="${attr(status)}" style="margin-bottom:14px">
    <div class=eyebrow style="margin-bottom:8px">Cook PVI · 2026 source snapshot</div>${body}
    <div class=empty>${cookPviSourceHTML()}</div>
  </div>`;
}
function demoHTML(m){
  const d=m.demo||{},community=m.community||{},ce=community.estimates||{},cd=community.derived||{},cm=D.community_meta||{};
  const dr=radar(m.demo_radar||[],D.demo_short,'#c2862a',82);
  const brief=m.district_brief||{};
  const districtBrief=brief.p1&&brief.p2&&brief.p3?`<div class=districtbrief>
    <div class=eyebrow style="color:#EECD5C;margin-bottom:9px">${m.chamber==='Senate'?'State':'District'} brief</div>
    <p>${esc(brief.p1)}</p><p>${esc(brief.p2)}</p><p>${esc(brief.p3)}</p>
  </div>`:'';
  const popBasis=m.demo_population_normalization||{};
  /* Plain-language pass 2026-09-02. The Senate scaling note keeps every number, the link and the
     arithmetic; the term of art 'Senate population normalization' and the phrase
     'equivalent-district population' move to the hover so the provenance is still findable. */
  const popBasisNote=popBasis.method?`<div class=empty style="margin:-4px 0 12px" title="${attr('Senate population normalization: state population divided by apportioned House seats, applied before demographic percentile normalization')}"><b>Putting a state on a district scale:</b> ${n0(popBasis.raw_state_population)} people in the state &divide; ${n0(popBasis.house_seats)} House districts = <b>${n0(popBasis.normalization_population)}</b> people per district-sized share, so senators and representatives rank on the same scale. ${safeUrl(popBasis.source)?`<a href="${safeUrl(popBasis.source)}" target=_blank rel=noopener>Britannica seat table &nearr;</a>`:'Britannica seat table'}</div>`:'';
  const populationValue=popBasis.method?popBasis.normalization_population:(ce.population??d.constituents);
  const populationLabel=popBasis.method?'People per district-sized share':(ce.population!=null?'Population':'People counted for poverty');
  const populationTitle=popBasis.method?'Equivalent-district population: state population divided by apportioned House seats, before demographic percentile normalization':(ce.population!=null?'2024 ACS total population':'ACS poverty-universe population — the people for whom poverty status is determined, slightly below total population');
  /* District deduplication (2026-09-01). The structural community card below prints total
     population, median household income, median age and poverty rate ALREADY, and each of those
     carries its published 90% margin-of-error hover, which the base grid never had. Wherever that
     card renders (community.complete is true for 533/533 members) the base grid's copies are pure
     duplicates and are dropped; where it does not render they come back, in their original slots,
     with their original values. Two look-alikes are NOT duplicates and are never dropped:
       - the Senate equivalent-district figure ('People per district-sized share' = state population
         / apportioned House seats) and the poverty-universe fallback ('People counted for poverty')
         are different quantities from the card's 'Total population', so the tile only goes when its
         label is plain 'Population';
       - 'Homeowners who have a mortgage' (mortgaged share of owner-occupied units) is a different
         measure, from a different producer, than the card's owner-occupied share tile ('Homes lived
         in by their owner'). Both stay. */
  const communityCovers=!!(community&&community.complete);
  const populationDuplicated=communityCovers&&!popBasis.method&&ce.population!=null;
  const cells=[...(communityCovers?[]:[[money(ce.median_household_income??d.income),'Median household income']]),
    [pc(d.pct_costburden),'Homeowners stretched by housing costs','Mortgaged households cost-burdened. The cost-to-income cut-off behind this figure is not recorded in this system, so none is stated — see Sources & method.'],
    [pc(d.pct_finance),'Workers in finance / real estate'],
    ...(populationDuplicated?[]:[[n0(populationValue),populationLabel,populationTitle]]),
    [pc(d.pct_ownerocc_mort),'Homeowners who have a mortgage','Owner-occupied with a mortgage: the mortgaged share of owner-occupied homes'],[n0(d.establishments),'Business establishments'],
    ...(communityCovers?[]:[[ce.median_age??d.median_age??'—','Median age'],[pc(cd.poverty_rate??d.poverty_rate),'Poverty rate']]),
    [money(d.percap),'Income per person','Per-capita income']]
    .map(([v,l,t])=>`<div class=dcell${t?` title="${attr(t)}"`:''}><div class=v>${v}</div><div class=l>${l}</div></div>`).join('');
  const base=`${cookPviHTML(m)}${districtBrief}<div class=gen style=margin-bottom:12px><div>${dr}</div><div class=dgrid>${cells}</div></div>${popBasisNote}`;
  const variableCatalog=cm.variable_catalog||{},moe=community.moe_90||{};
  const moeTitle=metric=>{
    const row=variableCatalog[metric]||{},margin=finiteNum(moe[metric]),bits=[];
    if(row.table||row.variable)bits.push([row.table,row.variable].filter(Boolean).join(' '));
    if(margin!=null)bits.push(`90% margin of error ±${Math.round(margin).toLocaleString()}`);
    return bits.length?` title="${attr(bits.join(' · '))}"`:'';
  };
  /* DP05 / DP03 / DP04 are Census profile-family codes, not reading matter. The plain name is shown,
     the code stays on the hover and is restated in 'Sources & method'; the link targets, their order
     and the keys used to look them up are all unchanged. */
  const COMMUNITY_LINK_NAMES={DP05:'People',DP03:'Jobs and income',DP04:'Housing'};
  const profileFamilies=cm.profile_families||{};
  const communityLinks=['DP05','DP03','DP04'].map(key=>{
    const url=(cm.official_urls||{})[key],name=COMMUNITY_LINK_NAMES[key]||key;
    const tip=attr('Census profile '+key+(profileFamilies[key]?' · '+profileFamilies[key]:''));
    return safeUrl(url)?`<a href="${safeUrl(url)}" target=_blank rel=noopener title="${tip}">${esc(name)} ↗</a>`:`<span title="${tip}">${esc(name)}</span>`;
  }).join(' · ');
  const communityCard=community&&community.complete?`<div class=eyebrow style="margin-top:13px" title="${attr('Structural community context · ACS 1-year: the Census Bureau American Community Survey, one-year estimates')}">Community snapshot · ${esc(String(community.vintage||cm.vintage||'2024'))} ACS 1-year${m.chamber==='Senate'?' · state':' · district'}</div>
    <div class=dgrid style="margin-bottom:8px">
      <div class=dcell${moeTitle('population')}><div class=v>${n0(ce.population)}</div><div class=l>Total population</div></div>
      <div class=dcell title="${attr('People per square mile of land area')}"><div class=v>${finiteNum(cd.population_density_sq_mile)==null?'—':n0(cd.population_density_sq_mile)}</div><div class=l>People per square mile</div></div>
      <div class=dcell${moeTitle('median_household_income')}><div class=v>${money(ce.median_household_income)}</div><div class=l>Median household income</div></div>
      <div class=dcell${moeTitle('median_age')}><div class=v>${finiteNum(ce.median_age)==null?'—':Number(ce.median_age).toFixed(1)}</div><div class=l>Median age</div></div>
      <div class=dcell${moeTitle('unemployed')}><div class=v>${pc(cd.unemployment_rate)}</div><div class=l>Unemployment rate</div></div>
      <div class=dcell${moeTitle('below_poverty')}><div class=v>${pc(cd.poverty_rate)}</div><div class=l>Poverty rate</div></div>
      <div class=dcell${moeTitle('owner_occupied_units')}><div class=v>${pc(cd.owner_occupied_share)}</div><div class=l>Homes lived in by their owner</div></div>
      <div class=dcell${moeTitle('median_home_value')}><div class=v>${money(ce.median_home_value)}</div><div class=l>Median home value</div></div>
      <div class=dcell${moeTitle('median_gross_rent')}><div class=v>${money(ce.median_gross_rent)}</div><div class=l>Median monthly rent</div></div>
      <div class=dcell title="${attr('12 × median gross rent / median household income; ratio of published medians, not household-level burden')}"><div class=v>${finiteNum(cd.annual_rent_income_ratio)==null?'—':pc(Number(cd.annual_rent_income_ratio)*100)}</div><div class=l>Rent as a share of income</div></div>
      <div class=dcell><div class=v>${finiteNum(cd.home_value_income_ratio)==null?'—':Number(cd.home_value_income_ratio).toFixed(2)+'×'}</div><div class=l>Home price to income</div></div>
      <div class=dcell><div class=v>${esc(community.region||'—')}</div><div class=l>Census region</div></div>
    </div>
    <div class=empty>Census sources: ${communityLinks}. Hover any figure for the exact Census table it came from and its published 90% margin of error. ${esc(cm.method||'')}</div>`:'';
  const oldFooter=`<div class=empty>District-level demographics shown. Named largest employers are not in the current dataset — add them in the notes fields, or wait for a future data pull.</div>`;
  const gh=D.gh_meta||{},stateSuffix=m.chamber==='Senate'?' — state aggregate':'';
  const fyRaw=gh.usaspending_fy==null?'':String(gh.usaspending_fy).replace(/^FY/i,'');
  const fyMatch=fyRaw.match(/(?:19|20)\d{2}/);
  const employerVintage=fyRaw?` (${esc('FY'+(fyMatch?fyMatch[0].slice(-2):fyRaw))})`:'';
  const hmdaVintage=gh.hmda_year==null?'':` (${esc(String(gh.hmda_year))})`;
  const employerAggregateTitle=m.chamber==='Senate'
    ?` title="${attr('State aggregate ranked from district top lists — partial view; sub-district-cutoff recipients omitted')}"`:'';
  const ghVintages=[];
  let employerCard='',financialCard='',hmdaCard='';
  if(m.employers){
    const employers=m.employers||{},large=employers.large||{};
    const federal=(Array.isArray(employers.top_federal)?employers.top_federal:[]).slice(0,3).map(row=>{
      const amount=finiteNum(row&&row.amount);
      return `<div class=dcell style="display:flex;justify-content:space-between;gap:10px;align-items:baseline">
        <div class=l style="margin-top:0;color:var(--ink)">${esc((row&&row.name)||'—')}</div>
        <div class=v style="font-size:13px;white-space:nowrap">${compactDollars(amount)}</div></div>`;
    }).join('');
    const incomplete=large.complete===false;
    const largeTip=String(gh.large_incomplete||'Incomplete source coverage; totals are lower bounds.');
    const dagger=incomplete?` <span title="${attr(largeTip)}">†</span>`:'';
    const employerMethod=String(gh.employer_method||'Federal place-of-performance award recipients; use as employer candidates, not verified employee-count rankings.');
    employerCard=`<div class=eyebrow style="margin-top:13px"${employerAggregateTitle}>Largest employer candidates${employerVintage}${stateSuffix}</div>
      <div class=dgrid style="margin-bottom:11px">${federal}
        <div class=dcell><div class=v>${n0(finiteNum(large.est_500plus))}${dagger}</div><div class=l>Workplaces with 500 or more staff</div></div>
      </div>
      <div class=empty>${esc(prettyEmployerMethod(employerMethod))}</div>`;
    if(gh.employers_vintage)ghVintages.push(esc(String(gh.employers_vintage)));
  }
  if(m.financial){
    const financial=m.financial||{},banks=financial.banks||{},cus=financial.cus||{};
    const hq=String(gh.sod_hq_deposits||''),hqTitle=hq?` title="${attr(hq)}"`:'';
    const hhi=finiteNum(banks.hhi);
    financialCard=`<div class=eyebrow style="margin-top:13px">Credit unions vs. banks${stateSuffix}</div>
      <div class=dgrid style="margin-bottom:11px">
        <div class=dcell><div class=v>${n0(finiteNum(banks.n_branches))}</div><div class=l>Bank branches</div></div>
        <div class=dcell><div class=v>${n0(finiteNum(cus.n_branches))}</div><div class=l>CU branches</div></div>
        <div class=dcell><div class=v>${sharePc(financial.cu_branch_share)}</div><div class=l>CU branch share</div></div>
        <div class=dcell${hqTitle}><div class=v>${billionsKusd(banks.deposits_kusd)}</div><div class=l>Bank deposits</div></div>
        <div class=dcell title="${attr('CU estimated shares — shares are the credit-union term for member deposits')}"><div class=v>${billionsKusd(cus.est_shares_kusd)}</div><div class=l>CU deposits (estimated)</div></div>
        <div class=dcell><div class=v style="font-size:13px">${esc(cus.top1_name||'—')}</div><div class=l>Top CU</div></div>
      </div>`;
    if(gh.fin_vintages)ghVintages.push(esc(String(gh.fin_vintages)));
  }
  if(m.hmda){
    const hmda=m.hmda||{};
    hmdaCard=`<div class=eyebrow style="margin-top:13px">Mortgage lending${hmdaVintage}${stateSuffix}</div>
      <div class=dgrid style="margin-bottom:11px">
        <div class=dcell title="${attr('Loan originations reported under HMDA, the Home Mortgage Disclosure Act')}"><div class=v>${n0(finiteNum(hmda.n_originations))}</div><div class=l>Mortgages made</div></div>
        <div class=dcell title="${attr('Home-purchase share: the share of originations whose stated purpose is buying a home; the rest are refinances and other purposes')}"><div class=v>${sharePc(hmda.home_purchase_share)}</div><div class=l>Share for buying a home</div></div>
        <div class=dcell title="${attr('HMDA low-and-moderate-income tracts: census tracts whose income is under 80% of the surrounding area median')}"><div class=v>${sharePc(hmda.lmi_tract_share)}</div><div class=l>Share in lower-income areas</div></div>
      </div>`;
    if(gh.hmda_year)ghVintages.push('Mortgage data (HMDA) '+esc(String(gh.hmda_year)));
  }
  const ghCards=employerCard+financialCard+hmdaCard;
  const ghFooter=ghVintages.length?`<div class=empty>${ghVintages.join('. ')}.</div>`:'';
  if(!m.industry){
    if(!ghCards)return `${base}
    ${communityCard}
    ${oldFooter}`;
    return `${base}
      ${communityCard}
      ${ghCards}
      ${ghFooter}`;
  }

  const ind=m.industry,meta=D.industry_meta||{},sm=meta.sector_meta||{},short=meta.short_names||{};
  const yearText=value=>{const n=Number(value);return value==null||value===''||!Number.isFinite(n)?'':String(n);};
  const shares=ind.makeup||{},small=new Set((ind.flags&&ind.flags.small)||[]);
  const potential=m.demographic_potential||{},potentialMeta=D.demographic_potential_meta||{};
  const potentialSignals=Array.isArray(potential.signals)?potential.signals:[];
  const potentialWeights=potentialMeta.score_weights||{},potentialLabels=potentialMeta.component_labels||{};
  const potentialDefs=Array.isArray(potentialMeta.component_definitions)?potentialMeta.component_definitions:[];
  const potentialSources=potentialMeta.source_catalog||{},potentialSensitivity=potentialMeta.sensitivity_audit||{};
  const potentialBars=Object.keys(potentialWeights).map(key=>{
    const value=Number((potential.components||{})[key]),available=(potential.available||{})[key]!==false;
    return `<div class=barrow><span class=t title="${attr(available?'Measured for this district':'NOT MEASURED for this district — filled in at the neutral middle, 0.50, so it neither helps nor hurts the score. Marked with a dagger.')}">${esc(potentialLabels[key]||key)} <span style=color:var(--faint)>×${Math.round(Number(potentialWeights[key])*100)}%</span>${available?'':' †'}</span><span class=track><i style="width:${Number.isFinite(value)?value*100:0}%;background:#34d399"></i></span><span class=val>${Number.isFinite(value)?Math.round(value*100):'—'}</span></div>`;
  }).join('');
  const potentialDefinitionRows=potentialDefs.map(def=>{
    const links=(def.sources||[]).map(key=>{
      const source=potentialSources[key]||{};
      const label=source.agency||key;
      return safeUrl(source.official_url)?`<a href="${safeUrl(source.official_url)}" target=_blank rel=noopener>${esc(label)}</a>`:esc(label);
    }).join(' + ');
    return `<div class=dcell style="grid-column:1/-1"><div class=v style="font-size:12px">${esc(def.label||def.key)} (${Math.round(Number(def.weight)*100)}%)</div>
      <div class=l><b>Source:</b> ${links||'—'}<br><b>Caveat:</b> ${esc(def.caveat||'—')}</div></div>`;
  }).join('');
  const memberSensitivity=potential.sensitivity||{};
  const structural=potential.structural_adjustment||{},structuralMeta=potentialMeta.structural_adjustment||{};
  const structuralControls=(structuralMeta.controls||[]).map(row=>row.label||row.key).filter(Boolean).join(', ');
  const structuralReachRows=(structuralMeta.targets||[]).map(key=>{
    const row=(structural.components||{})[key]||{},value=finiteNum(row.adjusted_percentile);
    return `<div class=dcell><div class=v style="font-size:12px">${esc(potentialLabels[key]||key)}</div><div class=l>${row.available?`Residual ${Number(row.residual)>=0?'+':''}${f2(row.residual)} · adjusted percentile ${value==null?'—':Math.round(value*100)}`:`Not adjusted — ${esc(REACH_NOT_ADJUSTED[row.reason]||row.reason||'source evidence unavailable')}`}</div></div>`;
  }).join('');
  const structuralComparison=structuralMeta.comparison_with_baseline||{};
  const structuralMethod=structural.available?`<div style="border:1px solid #EECD5C55;background:#EECD5C0d;border-radius:8px;padding:8px;margin:8px 0">
    <b>Structural companion score:</b> ${esc(structuralMeta.description||'Employer reach is residualized against structural controls.')} Controls: ${esc(structuralControls||'—')}. ${esc(structuralMeta.excluded_reason||'')} Baseline comparison: rho ${f2(structuralComparison.spearman_rho)}, top-${structuralComparison.top_n||20} overlap ${Math.round(Number(structuralComparison.top_overlap_rate||0)*100)}%.
    <div class=dgrid style="margin-top:7px">${structuralReachRows}</div>
  </div>`:'';
  // D-034: footprint scores four credit-union presence components and no sectors,
  // so the sector grid below has nothing to show.  Render the components it DID
  // score -- value and percentile -- and state the gate that decided who is scored
  // at all.  Under growth_room both consts keep the original sector rendering and
  // the note is '', so the emitted HTML is byte-identical.
  const potentialFootprintRows=Object.keys(potentialWeights).map(key=>{
    const raw=(potential.raw||{})[key],measured=(potential.available||{})[key]!==false;
    const tip=(potentialLabels[key]||key)+(DPSHARE.has(key)?' — the share of this market held by credit unions':' — location quotient: 1.000 means credit-union activity here is exactly as concentrated as it is nationally')+(measured?'':' — NOT MEASURED here; scored at the neutral 0.50');
    return `<div class=dcell title="${attr(tip)}"><div class=v style="font-size:13px">${dpValue(key,raw)}</div><div class=l>${esc(potentialLabels[key]||key)} · ${dpPct((potential.components||{})[key])} percentile${measured?'':' †'}</div></div>`;
  }).join('');
  const potentialGateNote=DPMODE!=='footprint'?'':`<div class=empty>Qualifying gate: credit-union employment concentration (LQ) at or above ${dp3(DPFOOT)} — this market measures ${dp3(potential.employment_lq)}. Markets below the gate are not scored and do not appear in the ${potential.market_count||potentialMeta.qualified_markets||0} ranked here.</div>`;
  const potentialCard=potential.qualified?`<div style="border:1px solid #34d39966;background:#34d3990d;border-radius:10px;padding:11px 12px;margin:0 0 12px">
    <div class=eyebrow style="color:#86efac;margin-bottom:7px">Demographic Potential · ${esc(potential.tier||'Qualified')} opportunity</div>
    <div class=dgrid style="margin-bottom:10px">
      <div class=dcell title="${attr('Potential score / 100 — the Demographic Potential score, on a 0 to 100 scale')}"><div class=v>${potential.score==null?'—':Number(potential.score).toFixed(1)}</div><div class=l>Opportunity score out of 100</div></div>
      <div class=dcell><div class=v>${potential.rank==null?'—':'#'+potential.rank}</div><div class=l>of ${potential.market_count||potentialMeta.qualified_markets||'—'} qualifying markets</div></div>
      <div class=dcell><div class=v>${potential.percentile==null?'—':Math.round(Number(potential.percentile)*100)}</div><div class=l>Opportunity percentile</div></div>
      <div class=dcell title="${attr('Evidence coverage: the share of the components below that rest on real evidence rather than the neutral 0.50 fill-in')}"><div class=v>${potential.evidence_coverage==null?'—':Math.round(Number(potential.evidence_coverage)*100)+'%'}</div><div class=l>How much of this was measured</div></div>
      <div class=dcell title="${attr('Rank range across sensitivity scenarios')}"><div class=v>${memberSensitivity.rank_min==null?'—':'#'+memberSensitivity.rank_min+'–#'+memberSensitivity.rank_max}</div><div class=l>Rank if the weights were set differently</div></div>
      <div class=dcell title="${attr('Structurally adjusted / 100 — see Sources & method')}"><div class=v>${structural.available?Number(structural.score).toFixed(1):'—'}</div><div class=l>Score adjusted for district structure</div></div>
      <div class=dcell title="${attr('Adjusted rank. The comparison is against the comparable baseline: the unadjusted rank recomputed over only the markets that could be adjusted, not the full baseline table.')}"><div class=v>${structural.available?'#'+structural.rank:'—'}</div><div class=l>Rank after that adjustment${structural.available?` · ${Number(structural.rank_delta)>0?'+':''}${structural.rank_delta} vs the same markets before adjustment`:''}</div></div>
    </div>
    <div style="display:grid;gap:6px;margin-bottom:10px">${potentialBars}</div>
    <div class=dgrid>${DPMODE==='footprint'?potentialFootprintRows:potentialSignals.map(row=>{
      const cross=row.crosscheck==='not_covered'?`<span title="${attr('CBP: not covered. The County Business Patterns business census does not cover this sector, so no cross-check is possible.')}">not in the business census</span>`:`<span title="${attr('CBP cross-check location quotient '+f2(row.cbp_lq_est)+' (County Business Patterns)')}">business-census check: ${lqBand(row.cbp_lq_est)}</span>`;
      return `<div class=dcell><div class=v style="font-size:13px">${esc(short[row.sector]||row.name||row.sector)}</div>
        <div class=l><span title="${attr('NAICS industry code')}">Industry ${esc(row.naics||'—')}</span> · <span title="${attr('Location quotient '+f2(row.lq))}">Industry Standout: ${lqBand(row.lq)}</span> · <span title="${attr('Competitive shift: jobs gained or lost beyond what national growth and the local industry mix would predict')}">local job shift</span> ${Number(row.competitive_shift)>0?'+':''}${n0(row.competitive_shift)}${row.competitive_shift_rate==null?'':` (${sharePc(row.competitive_shift_rate)} of base)`} · ${cross}</div></div>`;
    }).join('')}</div>${potentialGateNote}
    <details class=methnote style="margin:8px 0 0"><summary>How this score is built</summary><div class=methbody>
      <div style="border:1px solid #f59e0b66;background:#f59e0b12;border-radius:8px;padding:8px;margin-bottom:8px"><b>Weight provenance:</b> ${esc(potentialMeta.weight_basis||'Weights are policy assumptions, not empirical coefficients.')} ${esc(potentialMeta.weight_rationale||'')}</div>
      ${structuralMethod}
      <div class=dgrid>${potentialDefinitionRows}</div>
      <div style="margin-top:8px"><b>Optimization limit:</b> ${esc(potentialMeta.optimization_requirement||'Outcome data are required to estimate empirical weights.')}</div>
    </div></details>
  </div>`:'';
  const sectors=Object.keys(sm).sort((a,b)=>(Number(shares[b])||0)-(Number(shares[a])||0)||a.localeCompare(b));
  const legend=sectors.map(code=>{
    const row=sm[code]||{},pct=(Number(shares[code])||0)*100;
    const dagger=small.has(code)?` <span title="≤500 jobs">†</span>`:'';
    return `<div title="${attr(row.name||code)}" style="display:grid;grid-template-columns:10px 42px minmax(0,1fr) auto;gap:5px;align-items:center;min-width:0;font-size:10.5px">
      <span style="display:block;width:9px;height:9px;background:${industryColor(code,meta)};border-radius:2px"></span>
      <span class=mono style=color:var(--faint)>${esc(row.naics||'')}</span>
      <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(short[code]||row.name||code)}${dagger}</span>
      <b class=mono>${pct.toFixed(1)}%</b></div>`;
  }).join('');
  const topMetric=values=>Object.keys(sm).filter(code=>!small.has(code)&&Number.isFinite(Number((values||{})[code])))
    .map(code=>({code:code,value:Number(values[code])}))
    .sort((a,b)=>b.value-a.value||a.code.localeCompare(b.code))[0]||null;
  const topLQ=topMetric(ind.lq),topGQ=topMetric(ind.gq);
  const emp=ind.employment||{},isState=ind.kind==='state'&&ind.n_districts!=null;
  const divided=isState&&emp.per_district!=null;
  const empValue=divided?emp.per_district:emp.total;
  const empLabel=divided?'Jobs per district':'Jobs (latest year)';
  const empTitle=divided?` title="${attr('Employment ÷ districts: the state total divided evenly by the state apportioned House-district count')}"`:'';
  const empNote=divided?`<div style="font-size:10px;color:var(--faint);margin-top:2px">state total: ${n0(emp.total)}</div>`:'';
  const window=Array.isArray(ind.window)?ind.window:[],y0=yearText(window[0]),y1=yearText(window[1]);
  const windowSuffix=(y0||y1)?` ${y0}–${y1}`:'';
  const ss=ind.ss_total;
  const ssNums=ss?[Number(ss.delta),Number(ss.national_growth),Number(ss.industry_mix)]:[];
  const shiftTip=attr('Shift-share: the total job change split into national growth, industry mix, and the local (competitive) shift left over.');
  let shiftCell=`<div class=dcell title="${shiftTip}"><div class=v style="font-size:12px">Job change, broken down: —</div></div>`;
  if(ss&&ssNums.every(Number.isFinite)){
    const delta=ssNums[0],ng=Math.round(ssNums[1]),im=Math.round(ssNums[2]),cs=delta-ng-im;
    const compDominates=Math.abs(cs)>Math.abs(ng)&&Math.abs(cs)>Math.abs(im);
    const compValue=compDominates?`<b>${n0(cs)}</b>`:n0(cs);
    shiftCell=`<div class=dcell title="${shiftTip}"><div class=v style="font-size:12px">${n0(delta)} jobs = ${n0(ng)} national + ${n0(im)} industry mix + ${compValue} local</div><div class=l>Where the job change came from${windowSuffix}</div></div>`;
  }
  const metrics=`<div class=dgrid style="margin-top:11px">
    <div class=dcell${empTitle}><div class=v>${n0(empValue)}</div><div class=l>${empLabel}</div>${empNote}</div>
    <div class=dcell${topLQ?` title="${attr('Location quotient '+f2(topLQ.value))}"`:''}><div class=v style="font-size:13px">${topLQ?esc(short[topLQ.code]||topLQ.code)+' · '+lqBand(topLQ.value):'—'}</div><div class=l>Industry Standout: strongest sector</div></div>
    <div class=dcell${topGQ?` title="${attr('Growth quotient (top) '+f2(topGQ.value))}"`:''}><div class=v>${topGQ?esc(short[topGQ.code]||topGQ.code)+' · '+f2(topGQ.value):'—'}</div><div class=l>Fastest-growing sector</div></div>
    ${shiftCell}
  </div>`;
  const cu=ind.cu_metrics||{},cuSnap=cu.snapshot||{},cuProxy=cu.longitudinal_proxy||{},cuShift=cuProxy.shift_share||{};
  const exact3=value=>{const n=finiteNum(value);return n==null?'—':n.toFixed(3);};
  const exact1=value=>{const n=finiteNum(value);return n==null?'—':n.toLocaleString(undefined,{minimumFractionDigits:1,maximumFractionDigits:1});};
  const cuWindow=Array.isArray(cuProxy.window)?cuProxy.window.join('–'):'';
  const cuSource=(cu.source||{}).official_url;
  /* The proxy sector code, kept verbatim for the hovers. The face of the card shows the sector by
     name; the code itself is provenance and lives only here. Default 'NAICS 52' = Finance and
     Insurance, the same default the two name fallbacks below use. */
  const cuProxyNaics=cuProxy.naics?'NAICS '+cuProxy.naics:'NAICS 52';
  const cuCard=cu.schema?`<div style="border:1px solid #38bdf866;background:#38bdf80d;border-radius:10px;padding:11px 12px;margin:12px 0">
    <div class=eyebrow style="color:#7dd3fc;margin-bottom:7px">Credit-union industry, in numbers</div>
    <div class=dgrid>
      <div class=dcell title="${attr('CU employment location quotient '+exact3(cuSnap.employment_lq)+' · NAICS '+(cuSnap.naics||'522130'))}"><div class=v style="font-size:13px">${lqBand(cuSnap.employment_lq)}</div><div class=l>Industry Standout: credit-union jobs</div></div>
      <div class=dcell title="${attr('CU establishment location quotient '+exact3(cuSnap.establishment_lq))}"><div class=v style="font-size:13px">${lqBand(cuSnap.establishment_lq)}</div><div class=l>Industry Standout: credit-union locations</div></div>
      <div class=dcell title="${attr('CU employment estimate · County Business Patterns (CBP) '+String(cuSnap.year||'—'))}"><div class=v>${exact1(cuSnap.employment)}</div><div class=l>Credit-union jobs (estimated) · ${esc(String(cuSnap.year||'—'))}</div></div>
      <div class=dcell title="${attr('CU establishments — County Business Patterns (CBP) counts one establishment per physical location')}"><div class=v>${exact1(cuSnap.establishments)}</div><div class=l>Credit-union locations · ${esc(cuSnap.scope||'')}</div></div>
      <div class=dcell title="${attr('Location quotient '+exact3(cuProxy.location_quotient)+' · '+cuProxyNaics)}"><div class=v style="font-size:13px">${lqBand(cuProxy.location_quotient)}</div><div class=l>Industry Standout: ${esc(cuProxy.name||'Finance and Insurance')}</div></div>
      <div class=dcell title="${attr('Growth quotient '+exact3(cuProxy.growth_quotient)+' · '+cuProxyNaics)}"><div class=v>${exact3(cuProxy.growth_quotient)}</div><div class=l>How fast the sector is growing · ${esc(cuProxy.name||'Finance and Insurance')} stand-in${cuWindow?' · '+esc(cuWindow):''}</div></div>
      <div class=dcell style="grid-column:1/-1" title="${attr('Shift-share analysis · longitudinal proxy for '+cuProxyNaics+': the total job change split into national growth, industry mix, and the local (competitive) shift left over')}"><div class=v style="font-size:12px">${exact1(cuShift.delta)} jobs = ${exact1(cuShift.national_growth)} national + ${exact1(cuShift.industry_mix)} industry mix + ${exact1(cuShift.competitive_shift)} local</div><div class=l>Where the job change came from · ${esc(cuProxy.name||'Finance and Insurance')} used as a stand-in${cuWindow?' · '+esc(cuWindow):''}</div></div>
    </div>
    <div class=empty style="margin-top:7px">${esc(cuProxy.reason||'')}${safeUrl(cuSource)?` <a href="${safeUrl(cuSource)}" target=_blank rel=noopener title="${attr('County Business Patterns (CBP), U.S. Census Bureau')}">Official business-census source ↗</a>`:''}</div>
  </div>`:'';
  const lodes=meta.lodes||{};
  let vintage=`<span title="${attr('LODES: the Census Bureau Longitudinal Employer-Household Dynamics Origin-Destination Employment Statistics, the source for the district job counts on this card')}">Jobs data: LODES ${esc(lodes.version||'—')} ${esc(lodes.job_type||'—')}</span>, latest year ${yearText(ind.latest_year)}, window ${y0}–${y1}.`;
  if(ind.redrawn)vintage+=' * District boundaries redrawn since the 118th Congress.';
  if(isState)vintage+=` State-level data; raw totals divided by ${n0(ind.n_districts)} districts where noted.`;
  if(!ghCards)return `${base}
    ${communityCard}
    ${potentialCard}
    ${cuCard}
    <div class=eyebrow style="margin-top:13px">Main industries in the district</div>
    <div class=gen style="margin-bottom:8px"><div>${pieSVG(shares,meta,92,{label:'Main industries in the district'})}</div>
      <div><div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 12px">${legend}</div>
      ${small.size?'<div style="font-size:10px;color:var(--faint);margin-top:6px">† ≤500 jobs</div>':''}</div></div>
    ${metrics}
    <div class=empty>${vintage}</div>`;
  const combinedVintage=vintage+(ghVintages.length?' '+ghVintages.join('. ')+'.':'');
  return `${base}
    ${communityCard}
    ${potentialCard}
    ${cuCard}
    <div class=eyebrow style="margin-top:13px">Main industries in the district</div>
    <div class=gen style="margin-bottom:8px"><div>${pieSVG(shares,meta,92,{label:'Main industries in the district'})}</div>
      <div><div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 12px">${legend}</div>
      ${small.size?'<div style="font-size:10px;color:var(--faint);margin-top:6px">† ≤500 jobs</div>':''}</div></div>
    ${metrics}
    ${ghCards}
    <div class=empty>${combinedVintage}</div>`;
}
const ROLEBADGE={sponsor:['Sponsor','#16a34a'],original:['Orig. cosponsor','#2dd4bf'],cosponsor:['Cosponsor','#7dd3fc']};
function legHTML(m){
  const bills=m.bills||[];
  const effectiveness=m.effectiveness_shadow_html||'';
  if(!bills.length)return effectiveness+'<div class=empty>No tracked CU-relevant bills for this member (as sponsor, original cosponsor, or cosponsor).</div>';
  const areas=[...new Set(bills.flatMap(b=>b.areas||[]))].sort();
  const roleSeg=`<span class=seg id=legrole><button data-r="" class=on>All roles</button><button data-r=sponsor>Sponsored</button><button data-r=original>Orig. cosponsor</button><button data-r=cosponsor>Cosponsor</button></span>`;
  const areaSel=`<select class=secarea data-for=leg><option value="">All areas</option>${areas.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('')}</select>`;
  return effectiveness+`<div class=areasel>${roleSeg}${areaSel}</div><div id=leg-body></div>`;
}
function legBody(m,area,role){
  const bills=(m.bills||[]).filter(b=>(!area||(b.areas||[]).includes(area))&&(!role||b.role===role));
  if(!bills.length)return '<div class=empty>No bills'+(role?' as '+(ROLEBADGE[role][0].toLowerCase()):'')+(area?' in '+esc(area):'')+'.</div>';
  return `<div class=cnt>${bills.length} bill${bills.length>1?'s':''}</div>`+bills.map(b=>{const r=ROLEBADGE[b.role]||['',''];
    return `<div class=billrow>
      <div class=billtop><span class=rolebadge style="color:${r[1]};background:${r[1]}1f">${r[0]}</span>
        ${safeUrl(b.url)?`<a class=billno href="${safeUrl(b.url)}" target=_blank rel=noopener>${esc(b.bill)}</a>`:`<span class=billno>${esc(b.bill)}</span>`}
        <span class=billcong>${b.congress?b.congress+'th Cong.':''}</span></div>
      <div class=billtitle>${esc(b.title)}</div>
      <div class=billmeta>${(b.areas||[]).map(esc).join(' · ')} · ${esc(b.sponsor)}${b.ncos?` · ${b.ncos} cosponsors`:''}</div>
      ${b.summary?`<div class=billsum>${esc(b.summary)}</div>`:''}</div>`;}).join('');
}
// graded sentiment scale (score_label), most positive -> most negative
const SENT_ORDER=['Very strongly positive','Strongly positive','Positive','Weakly positive','Very weakly positive','Neutral','Very weakly negative','Weakly negative','Negative','Strongly negative','Very strongly negative'];
const SENT_NEU=SENT_ORDER.indexOf('Neutral');
function sentCol(l){const i=SENT_ORDER.indexOf(l);if(i<0||i===SENT_NEU)return '#9ca3af';return i<SENT_NEU?'#34d399':'#ef4444';}
// source filter: 'bluesky' unifies the scored Social-Master posts (source social, rel bluesky)
// with the unscored footprint posts (source bluesky); 'social' is the remaining Quorum channels.
function srcMatch(a,v){
  if(!v)return true;
  if(v.startsWith('network:'))return a.source==='interview'&&a.network===v.slice(8);
  if(v==='bluesky')return a.source==='bluesky'||(a.source==='social'&&a.rel==='bluesky');
  if(v==='social')return a.source==='social'&&a.rel!=='bluesky';
  return a.source===v;
}
const FOOTPRINT_SRC={interview:'broadcast appearance',hearing:'committee hearing',bluesky:'Bluesky footprint'};
function pressDomain(a){
  if(!a||a.source!=='press')return '';
  try{return new URL(a.url).hostname.toLowerCase().replace(/^www\./,'')||'source unavailable';}
  catch(_){return 'source unavailable';}
}
function pressHTML(m){
  const arts=m.articles||[];
  const mex=m.media_exclusions||{};
  const mt=m.media_totals||{};
  const filtered=(mex.unrelated||0)+(mex.offtopic||0);
  const audit=filtered?`<div class=cnt>${filtered} filtered social items (${mex.unrelated||0} unrelated, ${mex.offtopic||0} off-topic)</div>`:'';
  const sourceCounts={press:0,social:0,bluesky:0,interview:0,hearing:0};
  arts.forEach(a=>{if(a.source==='bluesky'||(a.source==='social'&&a.rel==='bluesky'))sourceCounts.bluesky++;
    else if(Object.hasOwn(sourceCounts,a.source))sourceCounts[a.source]++;});
  const sourceLabels={press:'press items',social:'social posts',bluesky:'Bluesky posts',interview:'interviews',hearing:'hearings'};
  const included=Object.entries(sourceCounts).filter(([,count])=>count).map(([key,count])=>`${count.toLocaleString()} ${sourceLabels[key]}`);
  if(mt.bs_pending)included.push('Bluesky handle pending verification — posts withheld');
  const fpline=included.length?`<div class=cnt><b>Coverage included:</b> ${included.join(' · ')}</div>`:'';
  const outletCounts=mt.iv_sources||{};
  const outletRows=Object.entries(outletCounts);
  const outletLine=outletRows.length?`<details class=methnote><summary>All ${outletRows.length} interview source${outletRows.length===1?'':'s'} (${sourceCounts.interview.toLocaleString()} appearances)</summary><div class=methbody>${outletRows.map(([name,count])=>`${esc(name)} ${Number(count).toLocaleString()}`).join(' · ')}</div></details>`:'';
  const pressOutlets={};arts.filter(a=>a.source==='press').forEach(a=>{const host=pressDomain(a);pressOutlets[host]=(pressOutlets[host]||0)+1;});
  const pressRows=Object.entries(pressOutlets).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]));
  const pressOutletLine=pressRows.length?`<details class=methnote><summary>All ${pressRows.length} press publisher${pressRows.length===1?'':'s'} (${sourceCounts.press.toLocaleString()} items)</summary><div class=methbody>${pressRows.map(([name,count])=>`${esc(name)} ${Number(count).toLocaleString()}`).join(' · ')}</div></details>`:'';
  const sentimentNote='<div class=cnt>Press sentiment is active at a diminished 10% overall weight and may affect issue and overall labels; legislative action remains the stronger policy signal.</div>';
  if(!arts.length)return audit+fpline+outletLine+pressOutletLine+sentimentNote+'<div class=empty>No tracked credit-union press or social items — and no interview, hearing, or Bluesky footprint — on record for this member.</div>';
  const areas=[...new Set(arts.flatMap(a=>a.cats||[]))].sort();
  const present=[...new Set(arts.map(a=>a.label).filter(Boolean))];
  const ordered=SENT_ORDER.filter(l=>present.includes(l)).concat(present.filter(l=>SENT_ORDER.indexOf(l)<0));
  const networkOpts=Object.keys(outletCounts).map(name=>['network:'+name,name]);
  const SRC_OPTS=[['press','Press'],['social','Social (X · FB · Truth)'],['bluesky','Bluesky'],['interview','All interviews'],...networkOpts,['hearing','Hearings']];
  const srcSel=`<select class=secsrc data-for=press><option value="">All sources</option>${SRC_OPTS.map(([v,l])=>{const n=arts.filter(a=>srcMatch(a,v)).length;return n?`<option value="${v}">${l} (${n})</option>`:'';}).join('')}</select>`;
  const sentSel=`<select class=secsent data-for=press><option value="">Sentiment</option>${ordered.map(l=>`<option value="${esc(l)}">${esc(l)} (${arts.filter(a=>a.label===l).length})</option>`).join('')}</select>`;
  const areaSel=`<select class=secarea data-for=press><option value="">All areas</option>${areas.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('')}</select>`;
  return audit+fpline+outletLine+pressOutletLine+sentimentNote+`<div class=areasel>${srcSel}${sentSel}${areaSel}</div><div id=press-body></div>`;
}
function pressBody(m,area,sent,src,limit=50){
  const arts=(m.articles||[]).filter(a=>srcMatch(a,src)&&(!area||(a.cats||[]).includes(area))&&(!sent||a.label===sent)).slice().sort((a,b)=>String(b.date||'').localeCompare(String(a.date||''))||String(a.title||'').localeCompare(String(b.title||'')));
  if(!arts.length)return '<div class=empty>No '+(sent?esc(sent).toLowerCase()+' ':'')+'media items'+(area?' in '+esc(area):'')+(src?' from '+esc(src):'')+' for this member.</div>';
  const shown=arts.slice(0,limit),more=shown.length<arts.length;
  return `<div class=cnt>Showing ${shown.length.toLocaleString()} of ${arts.length.toLocaleString()} media item${arts.length>1?'s':''}${src?' · '+esc(src.replace(/^network:/,'')):''}${sent?' · '+esc(sent):''}${area?' · '+esc(area):''}</div>`+shown.map(a=>{
    const foot=!!FOOTPRINT_SRC[a.source];   // display-only appearance/post record — no CU-stance override UI
    const base=({support:'support',oppose:'oppose',neutral:'neutral'}[a.stance])||'neutral';   // current call (model + any baked file correction)
    const ov=foot?null:povOf(a.url); const cur=ov||base; const effPol={support:1,oppose:-1,neutral:0}[cur];
    const eu=encodeURIComponent(a.url||''), et=encodeURIComponent(a.title||'');
    const opts=['support','oppose','neutral'].map(o=>`<option value="${o}"${cur===o?' selected':''}>${o[0].toUpperCase()+o.slice(1)}</option>`).join('');
    const tag=(ov&&ov!==base)?`<span class=ovtag style="color:${POVCOL[ov]};background:${POVCOL[ov]}1A">✎ edited → ${ov} (was ${base})</span>`:(a.corr?`<span class=ovtag style="color:#a78bfa;background:#a78bfa1A">✎ file-corrected</span>`:'');
    const basis=FOOTPRINT_SRC[a.source]||(a.source==='social'?('social stance: '+esc(a.stance||'n/a')):(a.ssrc==='bill'?'via tracked-bill direction':('DEBATE stance: '+esc(a.stance||'n/a')+(a.sconf?` (${Math.round(a.sconf*100)}% conf)`:''))));
    return `<div class=ev data-pol="${effPol}">
    <div class=ev-top><span>${esc(a.date||'')}${a.dest==='est'?' <span style=color:var(--faint)>(est.)</span>':''}</span><span>${(a.cats||[]).slice(0,3).map(esc).join(' · ')}</span></div>
    <div class=ev-top><span>Source: ${esc(a.source||'press')}</span><span>${a.scored===false?(a.gstance?'appearance record · CU stance graded from transcript':'visible, not scored: '+esc(a.exclusion||'excluded')):'scored evidence'}</span></div>
    ${safeUrl(a.url)?`<a class=ev-title href="${safeUrl(a.url)}" target=_blank rel=noopener title="${esc(a.title||'')}">${esc(clipTitle(a.title||''))}</a>`:`<span class=ev-title title="${esc(a.title||'')}">${esc(clipTitle(a.title||''))}</span>`}
    <div class=ev-sig>${a.label?`<b style="color:${sentCol(a.label)}">${esc(a.label)}</b>`:''}<span class=ev-basis> · ${basis}${a.rel?' · '+esc(a.rel):''}${a.also?' · '+esc(a.also):''}</span></div>
    ${a.gstance?`<div class=ev-grade><span class=ovtag style="color:${POVCOL[a.gstance]||'#9ca3af'};background:${POVCOL[a.gstance]||'#9ca3af'}1A">CU stance: ${esc(a.gstance)}${!HONORS&&a.gtier?' · '+esc(a.gtier):''}</span>${a.gn>1?`<span class=ev-basis> · first of ${a.gn} graded passages on this record</span>`:''}</div>`:''}
    ${a.snip?`<div class=ev-snip>“${esc(clipSnip(a.snip))}”</div>`:''}
    ${foot?'':`<div class=ev-ov>${tag}<span class=ovlabel>CU stance:</span><select class=povsel data-povurl="${eu}" data-povtitle="${et}" data-base="${base}">${opts}</select></div>`}</div>`;
  }).join('')+(more?`<button class=btn id=pressmore data-next="${Math.min(arts.length,limit+50)}">Show 50 more (${(arts.length-shown.length).toLocaleString()} remaining)</button>`:'');
}
function esc(s){return (s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
/* href-safe URL: attribute-escaped http(s) only; anything else (javascript:, data:, relative, empty) -> '' so callers render no link */
function safeUrl(u){const s=String(u==null?'':u).trim();return /^https?:\/\//i.test(s)?attr(s):'';}
/* ---------- member addressing ----------
   A member is addressed by NAME through the ?m= query parameter (the same address the network
   dashboard writes into its "Member details" link). mlink() is the ONE place that mints that
   address, so every member name on this page is a real, focusable, middle-clickable anchor.
   The delegated handler below turns a plain left click into an in-page openMember() so the 33MB
   document is never re-fetched; ctrl/cmd/middle click falls through to a normal new-tab load,
   and with JS off the href still resolves to the right profile. */
function memberHref(name){return '?m='+encodeURIComponent(name==null?'':String(name));}
function mlink(name,label){
  const i=byName[name], txt=esc(label==null?name:label);
  if(name==null||i==null)return txt;   // unresolved member: plain text, never a dead link
  return `<a class=mlink href="${memberHref(name)}" data-mi=${i} title="Open ${esc(name)} — full profile">${txt}</a>`;
}
/* resolve an inbound ?m= / #m= address; tolerant of trailing space and case so a hand-typed or
   copy-pasted deep link still lands on the member instead of the top of the ranked list. */
function resolveMemberAddress(raw){
  if(raw==null)return null;
  const q=String(raw).trim(); if(!q)return null;
  if(byName[q]!=null)return byName[q];
  const lc=q.toLowerCase();
  for(const k in byName){if(k.toLowerCase()===lc)return byName[k];}
  return null;
}
/* A table row stays a convenience click target, but it must not fight the anchor inside it and it
   must not steal a click the user made while selecting text (accessibility review 2026-08-02). */
function rowOpen(e,el){
  if(e&&e.target&&e.target.closest&&e.target.closest('a'))return;   // the name anchor owns its own click
  const s=window.getSelection&&window.getSelection();
  if(s&&String(s).length>0)return;                                  // never hijack a text selection
  const i=+el.dataset.i; if(i>=0)openMember(i);
}
function inboundMemberAddress(){
  const q=new URLSearchParams(location.search).get('m');
  if(q)return q;
  const h=String(location.hash||'');
  if(h.startsWith('#m='))return decodeURIComponent(h.slice(3));
  return null;
}
document.addEventListener('click',e=>{
  if(!e.target.closest)return;
  const a=e.target.closest('a.mlink[data-mi]'); if(!a)return;
  if(e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
  e.preventDefault(); openMember(+a.dataset.mi);
});
function clipTitle(value,limit=220){const text=String(value||'').replace(/\s+/g,' ').trim();return text.length>limit?text.slice(0,limit-1).trimEnd()+'…':text;}
// graded-appearance passage: longer than a title, cut on a word boundary
function clipSnip(value,limit=400){const text=String(value||'').replace(/\s+/g,' ').trim();
  return text.length<=limit?text:text.slice(0,limit).replace(/\s+\S*$/,'').trimEnd()+'…';}

/* ----- wire section area filters + member links ----- */
function wireSectionFilters(m){
  const ls={area:'',role:''}, ps={area:'',sent:'',src:'',limit:50};
  const renderLeg=()=>{const lb=$('#leg-body');if(lb)lb.innerHTML=legBody(m,ls.area,ls.role);};
  const renderPress=()=>{const pb=$('#press-body');if(pb)pb.innerHTML=pressBody(m,ps.area,ps.sent,ps.src,ps.limit);};
  renderLeg(); renderPress();
  const _pbox=$('#press-body');
  if(_pbox)_pbox.addEventListener('change',e=>{const s=e.target.closest('.povsel');if(!s)return;
    const val=s.value, base=s.dataset.base;                       // picking the baseline clears the override
    setPOV(decodeURIComponent(s.dataset.povurl||''), val===base?null:val, decodeURIComponent(s.dataset.povtitle||''));
    renderPress(); updatePovHint();});
  if(_pbox)_pbox.addEventListener('click',e=>{const more=e.target.closest('#pressmore');if(!more)return;
    ps.limit=Number(more.dataset.next||ps.limit+50);renderPress();});
  document.querySelectorAll('.secarea').forEach(sel=>sel.onchange=()=>{
    if(sel.dataset.for==='leg'){ls.area=sel.value;renderLeg();}
    else {ps.area=sel.value;ps.limit=50;renderPress();}
  });
  const ssel=document.querySelector('.secsent[data-for=press]');
  if(ssel)ssel.onchange=()=>{ps.sent=ssel.value;ps.limit=50;renderPress();};
  const csel=document.querySelector('.secsrc[data-for=press]');
  if(csel)csel.onchange=()=>{ps.src=csel.value;ps.limit=50;renderPress();};
  const rseg=document.getElementById('legrole');
  if(rseg)rseg.querySelectorAll('button').forEach(b=>b.onclick=()=>{rseg.querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');ls.role=b.dataset.r;renderLeg();});
  document.querySelectorAll('.partner').forEach(el=>el.onclick=e=>rowOpen(e,el));
}
/* ----- wire manual edit/save/upload ----- */
function wireManual(m){
  document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const k=b.dataset.edit;$('#'+k+'-edit').hidden=false;b.hidden=true;});
  document.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=()=>{openMember(byName[m.name]);});
  document.querySelectorAll('[data-save]').forEach(b=>b.onclick=()=>{const k=b.dataset.save;const ta=document.querySelector('textarea[data-field='+k+']');setMan(m.name,k,ta.value.trim());openMember(byName[m.name]);});
  document.querySelectorAll('input[type=file][data-field]').forEach(inp=>inp.onchange=e=>{
    const k=inp.dataset.field;const fs=[...e.target.files];let left=fs.length;
    MAN[m.name]=MAN[m.name]||{};MAN[m.name].files=MAN[m.name].files||[];
    fs.forEach(file=>{const rd=new FileReader();rd.onload=()=>{MAN[m.name].files.push({name:file.name,type:file.type,data:rd.result,field:k});if(--left===0){saveMAN();openMember(byName[m.name]);}};rd.readAsDataURL(file);});
  });
  document.querySelectorAll('.del[data-f]').forEach(el=>el.onclick=()=>{const k=+el.dataset.f;MAN[m.name].files.splice(k,1);saveMAN();openMember(byName[m.name]);});
}

/* ======================= BILL REPOSITORY ======================= */
function billIdentity(b){
  const raw=String(b.bill||'').toUpperCase().replace(/[^A-Z0-9]/g,''),m=raw.match(/^(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)(\d+)$/);
  if(!m)return null;const congress=Number(b.congress);if(!congress)return null;
  const labels={HR:'H.R.',S:'S.',HRES:'H.Res.',SRES:'S.Res.',HJRES:'H.J.Res.',SJRES:'S.J.Res.',HCONRES:'H.Con.Res.',SCONRES:'S.Con.Res.'};
  return {key:`${congress}:${m[1].toLowerCase()}:${Number(m[2])}`,display:(labels[m[1]]||m[1]+'.')+Number(m[2]),congress};
}
function buildBillRepository(){
  const map={};
  M.forEach((member,mi)=>(member.bills||[]).forEach(source=>{
    const id=billIdentity(source);if(!id)return;
    const row=map[id.key]||(map[id.key]={...id,title:source.title||id.display,sponsor:source.sponsor||'',summary:source.summary||'',action:source.action||'',url:source.url||'',areas:new Set(),sponsors:new Set(),originals:new Set(),cosponsors:new Set(),coalition:new Set()});
    (source.areas||[]).forEach(area=>row.areas.add(area));row.coalition.add(mi);
    if(source.role==='sponsor')row.sponsors.add(mi);else if(source.role==='original')row.originals.add(mi);else row.cosponsors.add(mi);
  }));
  const live=((D.bill_live||{}).bills)||{};
  const lobbying=((D.bill_lobbying||{}).bills)||{};
  Object.values(map).forEach(row=>{
    const cached=live[row.key]||null;
    if(cached){row.live=cached;row.title=cached.title||row.title;row.summary=cached.summary||row.summary;row.url=cached.url||row.url;row.action=((cached.latest_action||{}).text)||row.action;(cached.areas||[]).forEach(area=>row.areas.add(area));}
    row.lobbying=lobbying[row.key]||null;
    row.areas=[...row.areas].sort();row.sponsors=[...row.sponsors];row.originals=[...row.originals];row.cosponsors=[...row.cosponsors];row.coalition=[...row.coalition];
  });
  return Object.values(map).sort((a,b)=>b.congress-a.congress||a.display.localeCompare(b.display,undefined,{numeric:true}));
}
let BILLS=[],BILL_BY_KEY={};
const billStateFilter={q:'',area:'',congress:'',tracked:false,limit:40};
// DESK_SPLIT: member.bills is a deferred field, so the index built here at parse time is empty
// and __melangeProfileMerge must rebuild it once the profiles payload lands. Idempotent: the
// monolith (DSPLIT=0) build calls this exactly once, below, with bills already present.
function rebuildBillRepository(){
  BILLS=buildBillRepository();BILL_BY_KEY=Object.fromEntries(BILLS.map(b=>[b.key,b]));
  const sel=$('#billcongress');
  if(sel){const keep=sel.value;while(sel.options.length>1)sel.remove(1);
    [...new Set(BILLS.map(b=>b.congress))].sort((a,b)=>b-a).forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c+'th Congress';sel.appendChild(o);});
    // keep the reader's congress filter if it survived the rebuild, else fall back to All Congresses
    sel.value=[...sel.options].some(o=>o.value===keep)?keep:'';billStateFilter.congress=sel.value;}
  const bv=document.getElementById('view-bills');if(bv&&!bv.hidden)renderBills();
}
rebuildBillRepository();
function baseEdge(u,v){return ((ADJPROB[u]||{})[v])||0;}
const READINESS={C:1,F:1,A:.95,H:.82,P:.68,G:.56,T:.45,N:.42,L:.30,S:.14,K:.07,O:.02,'-':.34};
// Compatibility diagnostic only: this preserves the owner's IC/LT calculation and its ratified weights,
// but its attachment-sum score is retired. Neither recommender ranks on candidateInfluence().score;
// nextPeople() and suggest() use coverage/marginalGain or the centred specificResidual instead.
function candidateInfluence(v,seeds,area){
  let miss=1,pressure=0;
  seeds.forEach(u=>{const p=adaptiveEdgeProbability(baseEdge(u,v),u,v,area);miss*=1-p;pressure+=p;});
  const ic=1-miss,d=domainOf(area)||{lt_threshold_scale:1},rp=rolePriorAt(v,area),threshold=(d.lt_threshold_scale||1)*(rp.target_resistance||1);
  const ai=CATS.indexOf(area),code=ai>=0?((M[v].stances||'')[ai]||'-'):(M[v].overall_stance||'-'),lt=Math.min(1,pressure/Math.max(.05,threshold));
  return {score:.55*ic+.35*lt+.10*(READINESS[code]||.2),ic,lt,role:roleCodeAt(v,area)};
}
// Ranks on the SAME basis the coalition builder is set to (see basisScores). The section copy
// ("the incremental expected reach they add") stays accurate: `gain` is still marginalGain and
// is still what each row reports — only the ORDER follows the basis.
function nextPeople(bill,limit=6){
  const taken=new Set(bill.coalition||[]),area=(bill.areas||[])[0]||'',pool=new Set();
  // Same pool policy as suggest(): every eligible non-coalition member in the bill's chamber,
  // not just the union of the coalition's neighbours (2026-08-03 pool fix + chamber gate). A bill
  // repository row is the purest form of the pathfinder's task — the sponsor is literally given.
  const chambers=new Set([...taken].map(i=>M[i].chamber).filter(Boolean));
  for(let v=0;v<M.length;v++)if(!taken.has(v)&&(!chambers.size||chambers.has(M[v].chamber)))pool.add(v);
  if(!pool.size)return [];
  const cov=coverage(taken,area),cand=[...pool],sc=basisScores(cand,taken,area,cov,cbBasis);
  return cand.map((i,k)=>[i,{score:sc[k],gain:marginalGain(i,cov,taken,area),already:cov[i],role:roleCodeAt(i,area)}])
    .sort((a,b)=>b[1].score-a[1].score||a[0]-b[0]).slice(0,limit);
}
function relatedPress(bill){
  const token=String(bill.display||'').toLowerCase().replace(/[^a-z0-9]/g,''),areas=new Set(bill.areas||[]),out=[],seen=new Set();
  (bill.coalition||[]).forEach(mi=>(M[mi].articles||[]).forEach(a=>{
    const hay=String((a.title||'')+' '+(a.url||'')).toLowerCase().replace(/[^a-z0-9]/g,''),direct=token&&hay.includes(token),area=(a.cats||[]).some(c=>areas.has(c));
    const key=a.url||a.title;if((direct||area)&&key&&!seen.has(key)){seen.add(key);out.push({member:M[mi].name,direct,...a});}
  }));
  return out.sort((a,b)=>Number(b.direct)-Number(a.direct)||String(b.date||'').localeCompare(String(a.date||''))).slice(0,4);
}
function relatedAnecdotes(bill){
  const token=String(bill.display||'').toLowerCase().replace(/[^a-z0-9]/g,''),titleWords=String(bill.title||'').toLowerCase().split(/[^a-z]+/).filter(w=>w.length>7).slice(0,4),out=[];
  (bill.coalition||[]).forEach(mi=>(((M[mi].personal||{}).items)||[]).filter(a=>a.narrative_eligible===true).forEach(a=>{
    const hay=String((a.claim||'')+' '+(a.evidence||'')).toLowerCase(),flat=hay.replace(/[^a-z0-9]/g,'');
    if((token&&flat.includes(token))||titleWords.some(w=>hay.includes(w)))out.push({member:M[mi].name,...a});
  }));
  return out.slice(0,3);
}
function namesFor(indices){return indices.map(i=>M[i].name);}
function uniqueNames(rows){return [...new Set((rows||[]).map(v=>String(v||'').trim()).filter(Boolean))];}
const BILL_LOBBY_SECTOR_COLORS={bank:'#60a5fa',credit_union:'#34d399',crypto:'#c084fc',financial_other:'#fbbf24',fintech:'#22d3ee',telecom:'#f472b6'};
function billLobbySectorLabel(value){return String(value||'unclassified').split('|').map(v=>v.replace(/_/g,' ')).join(' / ');}
function billLobbySectorColor(value){return BILL_LOBBY_SECTOR_COLORS[String(value||'').split('|')[0]]||'#94a3b8';}
function billLobbyCount(value){return Number(value||0).toLocaleString();}
function billLobbyingBlocks(bill){
  const lobbying=bill.lobbying;if(!lobbying)return '';
  const totals=lobbying.totals||{},orgs=lobbying.organizations||[],lobbyists=lobbying.top_lobbyists||[],members=lobbying.members_lobbied||[],forecast=lobbying.joining_forecast||[];
  const orgHtml=orgs.map(org=>`<span class=billlobby-chip title="${attr(billLobbySectorLabel(org.sector))}"><i style="background:${billLobbySectorColor(org.sector)}"></i><span>${esc(org.name)}<small style=display:block>${esc(billLobbySectorLabel(org.sector))}</small></span><b>×${billLobbyCount(org.count)}</b></span>`).join('');
  const lobbyistHtml=lobbyists.map(row=>{const top=row.top_interaction||{},firm=row.firm?` &mdash; ${esc(row.firm)}`:'',topLine=top.organization?`Top across corpus: ${esc(top.organization)} · ${esc(top.bill||top.bill_key||'')} · ${esc(top.date||'date unavailable')}${Number(top.count||0)>1?' · '+billLobbyCount(top.count)+' interactions':''}`:'No top interaction available';return `<div class=billlobby-row><div><b>${esc(row.name)}</b>${firm} <span class=billlobby-count>· ${billLobbyCount(row.count)} interactions</span></div><div class=detail>${topLine} · Industry: ${esc(billLobbySectorLabel(row.industry))}</div></div>`;}).join('');
  const memberHtml=members.map(row=>`<div class=billlobby-row>${mlink(row.name)} <span class=detail>${esc(row.bioguide_id||'')} · ${esc(row.role||'member')}${row.is_former?' · former member':''}</span></div>`).join('');
  const forecastHtml=forecast.map(row=>`<div class=billlobby-row><b class=billlobby-count style=float:right>${(Number(row.probability||0)*100).toFixed(1)}%</b>${mlink(row.name)} <span class=detail>${row.joined===1?'joined ✓':row.joined===0?'not joined':'outcome pending'}${row.is_former?' · former member':''}</span></div>`).join('');
  const dates=totals.first_filing_date||totals.last_filing_date?` · ${esc(totals.first_filing_date||'')}–${esc(totals.last_filing_date||'')}`:'';
  const sidecar=D.bill_lobbying||{},coverage=[sidecar.coverage_note,sidecar.member_basis_note,forecast.length?sidecar.forecast_note:''].filter(Boolean).map(esc).join(' ');
  return `<div class=billlobby><div class=billlobby-head><div class=eyebrow>Bill-citing lobbying</div><div class=billmeta>${billLobbyCount(totals.filings)} filings · ${billLobbyCount(totals.orgs)} organizations · ${billLobbyCount(totals.interactions)} bill-citing interactions${dates}</div></div><div class=billlobby-grid>
    <div class=repo-block><div class=eyebrow>Lobbied by</div><div class=billlobby-chips>${orgHtml||'<div class=empty>No organizations retained.</div>'}</div></div>
    <div class=repo-block><div class=eyebrow>Top lobbyists</div>${lobbyistHtml||'<div class=empty>No named lobbyists on these filings.</div>'}</div>
    <div class=repo-block><div class=eyebrow>Members lobbied</div>${memberHtml||'<div class=empty>No sponsor/cosponsor roster available.</div>'}</div>
    <div class=repo-block><div class=eyebrow>Joining forecast</div>${forecastHtml||'<div class=empty>No forecast: the panel covers finance-committee bills, 117th Congress onward.</div>'}</div>
    </div>${coverage?`<div class=billlobby-note>${coverage}</div>`:''}</div>`;
}
function billCard(bill){
  const state=billState(bill.key),live=bill.live||{},next=nextPeople(bill),press=relatedPress(bill),anecdotes=relatedAnecdotes(bill),lobbyingHtml=billLobbyingBlocks(bill);
  const hasLiveOriginals=Array.isArray(live.original_cosponsors),hasLiveLater=Array.isArray(live.cosponsors);
  const liveOriginals=(live.original_cosponsors||[]).map(p=>p.name).filter(Boolean),liveLater=(live.cosponsors||[]).map(p=>p.name).filter(Boolean),liveSponsor=(live.sponsor||{}).name;
  const originals=uniqueNames(hasLiveOriginals?liveOriginals:namesFor(bill.originals));
  const later=uniqueNames(hasLiveLater?liveLater:namesFor(bill.cosponsors)).filter(name=>!originals.includes(name));
  const sponsors=uniqueNames(liveSponsor?[liveSponsor]:[...namesFor(bill.sponsors),bill.sponsor]);
  const amendments=[...(live.amendments||[]),...((state.manualAmendments)||[])];
  const badge=bill.live?`<span class="livebadge${live.stale?' stale':''}">${live.stale?'last good':'live cache'}</span>`:'';
  const areas=(bill.areas||[]).map(a=>`<span class=rolebadge style="background:#D2A63C22;color:#93c5fd">${esc(a)}</span>`).join(' ');
  const people=(label,rows)=>`<div class=mrow><span>${label}</span><b>${rows.length?esc(rows.slice(0,8).join(', '))+(rows.length>8?' +'+(rows.length-8):''):'-'}</b></div>`;
  const nextHtml=next.map(([i,s])=>{const rp=rolePriorAt(i,(bill.areas||[])[0]||''),role=s.role!=='-'?` | ${esc(rp.label)}`:'';return `<div class=sugg billnext data-mi=${i} data-key="${attr(bill.key)}"><span>${esc(M[i].name)} <span style=color:var(--faint)>${esc(M[i].party||'')}${role}</span><br><small style=color:var(--faint)>adds ~${s.gain.toFixed(1)} members of new reach | ${(s.already*100).toFixed(0)}% already covered by this set</small></span><span class=nextscore>+${s.score.toFixed(1)}</span></div>`;}).join('');
  const amendHtml=amendments.length?amendments.map(a=>`<div class=billrow><div class=billtop><b>${esc(a.display||'Amendment note')}</b><span class=billcong>${esc(((a.latest_action||{}).date)||a.date||'')}</span></div><div class=billmeta>${esc(a.description||((a.latest_action||{}).text)||'No description supplied.')}</div>${safeUrl(a.url)?`<a href="${safeUrl(a.url)}" target=_blank rel=noopener>Congress.gov amendment -&gt;</a>`:''}</div>`).join(''):'<div class=empty>No amendments returned by the live cache.</div>';
  const pressHtml=press.length?press.map(a=>`<div class=ev>${safeUrl(a.url)?`<a class=ev-title href="${safeUrl(a.url)}" target=_blank rel=noopener>${esc(a.title||'Untitled item')}</a>`:`<span class=ev-title>${esc(a.title||'Untitled item')}</span>`}<div class=ev-top><span>${esc(a.member)} | ${esc(a.source||'press')}</span><span>${esc(a.date||'')}</span></div><div class=ev-note>${a.direct?'Direct bill reference':'Shared legislative area'}</div></div>`).join(''):'<div class=empty>No directly or area-linked press items in the current source bundle.</div>';
  const anecdoteHtml=anecdotes.length?anecdotes.map(a=>`<div class=ev><div class=ev-title>${esc(a.claim)}</div><div class=ev-top><span>${esc(a.member)} | ${esc(a.source||'')}</span><span>${esc(a.date||'')}</span></div></div>`).join(''):'<div class=empty>No source-grounded anecdotes matched this bill or its distinctive title terms.</div>';
  return `<details class=billcard data-key="${attr(bill.key)}"><summary><div class=billhead><div class=billtop><span class=billno>${esc(bill.display)}</span>${badge}<span class=billcong>${bill.congress}th</span></div><div class=billtitle>${esc(bill.title||bill.display)}</div><div class=billmeta>${esc(bill.action||'No latest action in cache')} | ${later.length+originals.length} cosponsors</div></div><button class="pinbtn${state.pinned?' on':''}" data-pin="${attr(bill.key)}" aria-label="Pin ${attr(bill.display)}">${state.pinned?'* Pinned':'Pin'}</button></summary>
    <div class=bbody><div>${areas}</div><div class=billsum>${esc(bill.summary||'No summary available.')}</div>
      ${(safeUrl(bill.url)||safeUrl(live.url))?`<div style="margin-top:9px"><a href="${safeUrl(bill.url)||safeUrl(live.url)}" target=_blank rel=noopener>Open on Congress.gov -&gt;</a></div>`:''}
      <div class=repo-cols><div class=repo-block><div class=eyebrow>Current coalition</div>${people('Sponsor',sponsors)}${people('Original cosponsors',originals)}${people('Additional cosponsors',later)}</div>
      <div class=repo-block><div class=eyebrow>Potential next people</div><div class=repo-list>${nextHtml||'<div class=empty>No candidates available.</div>'}</div></div></div>
      ${lobbyingHtml}
      <div class=repo-cols><div class=repo-block><div class=eyebrow>Press items related</div>${pressHtml}</div><div class=repo-block><div class=eyebrow>Anecdotes related</div>${anecdoteHtml}</div></div>
      <div class=repo-block style=margin-top:12px><div class=eyebrow>Amendments</div>${amendHtml}<button class="ghostbtn billaddamend" data-key="${attr(bill.key)}">Add amendment note</button></div>
      <div class=repo-block style=margin-top:12px><div class=eyebrow>Tracking notes</div><textarea class=billnote data-key="${attr(bill.key)}" placeholder="Next action, whip count, committee timing...">${esc(state.note||'')}</textarea></div>
    </div></details>`;
}
function renderBills(){
  const q=billStateFilter.q,matches=BILLS.filter(b=>(!q||(`${b.display} ${b.title} ${b.sponsor}`).toLowerCase().includes(q))&&(!billStateFilter.area||(b.areas||[]).includes(billStateFilter.area))&&(!billStateFilter.congress||String(b.congress)===billStateFilter.congress)&&(!billStateFilter.tracked||billState(b.key).pinned)),rows=matches.slice(0,billStateFilter.limit);
  $('#billcount').textContent=`Showing ${rows.length} of ${matches.length} matching bills (${BILLS.length} total)`;
  const live=D.bill_live||{},cull=D.legislation_culling||{},cc=cull.counts||{},cullText=cull.status?` | culling ${cc.scoring_ready_category_edges||0} scoring edges, ${cc.held_decision_units||0} held visible/non-scoring`:'';
  $('#billfresh').textContent=(live.updated_at?`Congress.gov cache ${live.updated_at}${Object.keys(live.failures||{}).length?' | '+Object.keys(live.failures).length+' last-good fallbacks':''}`:'Congress.gov live cache not yet synced')+cullText;
  $('#billrepo').innerHTML=rows.map(billCard).join('')||'<div class=empty>No bills match the current filters.</div>';
  const more=$('#billmore');more.style.display=rows.length<matches.length?'inline-block':'none';more.textContent=`Load ${Math.min(40,matches.length-rows.length)} more bills`;
}
$('#billq').oninput=e=>{billStateFilter.q=e.target.value.toLowerCase().trim();billStateFilter.limit=40;renderBills();};
$('#billarea').onchange=e=>{billStateFilter.area=e.target.value;billStateFilter.limit=40;renderBills();};
$('#billcongress').onchange=e=>{billStateFilter.congress=e.target.value;billStateFilter.limit=40;renderBills();};
$('#billtracked').onchange=e=>{billStateFilter.tracked=e.target.checked;billStateFilter.limit=40;renderBills();};
$('#billmore').onclick=()=>{billStateFilter.limit+=40;renderBills();};
document.addEventListener('click',e=>{
  const pin=e.target.closest('[data-pin]');if(pin){e.preventDefault();e.stopPropagation();const key=pin.dataset.pin;setBillState(key,'pinned',!billState(key).pinned);renderBills();return;}
  const add=e.target.closest('.billaddamend');if(add){const key=add.dataset.key,display=prompt('Amendment label (for example H.Amdt.12):');if(!display)return;const description=prompt('Amendment note or purpose:')||'';const rows=(billState(key).manualAmendments||[]).concat([{display,description,date:new Date().toISOString().slice(0,10)}]);setBillState(key,'manualAmendments',rows);renderBills();return;}
  const next=e.target.closest('.billnext');if(next){const bill=BILL_BY_KEY[next.dataset.key];coal=new Set(bill.coalition||[]);coal.add(+next.dataset.mi);cbArea=(bill.areas||[])[0]||'';$('#cbarea').value=cbArea;setView('coalition');renderCoal();}
});
document.addEventListener('change',e=>{if(e.target.classList&&e.target.classList.contains('billnote'))setBillState(e.target.dataset.key,'note',e.target.value.trim());});

/* ======================= COALITION BUILDER ======================= */
let coal=new Set(), cbArea='',cbParty='',cbCommittee='';
const CBSNAPKEY='mdesk_coal_snapshots_v1';
let CBSNAPS=[];try{CBSNAPS=JSON.parse(localStorage.getItem(CBSNAPKEY))||[]}catch(e){CBSNAPS=[]}
function saveSnapshots(){localStorage.setItem(CBSNAPKEY,JSON.stringify(CBSNAPS));}
$('#cbsearch').onchange=e=>{const i=byName[e.target.value.trim()];if(i!=null){coal.add(i);e.target.value='';renderCoal();}};
$('#cbarea').onchange=e=>{cbArea=e.target.value;renderCoal();};
$('#cbparty').onchange=e=>{cbParty=e.target.value;renderCoal();};
$('#cbcommittee').onchange=e=>{cbCommittee=e.target.value;renderCoal();};
document.querySelectorAll('#cbbasis button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#cbbasis button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');cbBasis=b.dataset.b;renderCoal();});
$('#cbclear').onclick=()=>{coal.clear();renderCoal();};
$('#cbsave').onclick=()=>{if(!coal.size){alert('Add at least one coalition member before saving a snapshot.');return;}const name=(prompt('Snapshot name:')||'').trim();if(!name)return;CBSNAPS.unshift({id:Date.now(),name,members:[...coal].map(i=>M[i].name),area:cbArea,party:cbParty,committee:cbCommittee,saved_at:new Date().toISOString()});saveSnapshots();renderSnapshots();};
$('#cbnet').onclick=()=>{if(!coal.size)return;const names=[...coal].map(i=>M[i].name);if(window.gotoNetworkCoalition)window.gotoNetworkCoalition(names);else window.open('Moneyball Dashboard.html?coal='+encodeURIComponent(names.join(',')),'_blank');};   // combined: in-page bridge · standalone: open the network with the group preloaded
// cov[v] = P(at least one coalition member reaches v); members already in the coalition pin at 1.
// Shared by the reach stat and by the recommender, so the headline number and the suggestion list
// can never disagree about what the current set already covers.
function coverage(seeds=coal,area=cbArea){
  const cov=new Float64Array(M.length);
  seeds.forEach(s=>{cov[s]=1;(ADJ[s]||[]).forEach(([v,p])=>{if(!seeds.has(v)){const ep=adaptiveEdgeProbability(p,s,v,area);cov[v]=1-(1-cov[v])*(1-ep);}});});
  return cov;
}
// MARGINAL GAIN — the exact increment reachEstimate() gains from adding v:
//   dR(v) = miss(v) + SUM over w outside the coalition of miss(w)*p(v,w),   miss(x) = 1-cov(x).
// miss(v) discounts a candidate the set already pulls in for free; the sum counts only reach that
// is NEW. That is the already-covered correction — the network analogue of the leave-one-out
// sector mean in lobby_champions.py, which exists because the members every finance org works
// (Andy Barr, David Kustoff) otherwise crowd out the ones specific to the set in front of you.
// Coverage is submodular, so dR(v) is non-increasing as the coalition grows: a hub RETIRES from
// the list once its neighbourhood is covered instead of climbing it. Until 2026-08-02 this ranked
// the ABSOLUTE tie sum into the set, which is monotone INCREASING in the coalition — adding a
// hub's neighbours PROMOTED the hub — so the two highest-degree members (Tom Emmer 171 ties,
// Andy Barr 164, out of 2,555 edges) sat at the top of essentially every list no matter who had
// already been added. Deterministic: nothing is sampled here, and the variety comes from the
// marginal actually updating, never from noise.
function marginalGain(v,cov,seeds=coal,area=cbArea){
  if(seeds.has(v))return 0;
  let g=1-cov[v];
  (ADJ[v]||[]).forEach(([w,p])=>{if(w!==v&&!seeds.has(w))g+=(1-cov[w])*adaptiveEdgeProbability(p,v,w,area);});
  return g;
}
// COALITION-SPECIFIC residual — the network transcription of lobby_champions.py's leave-one-out
// sector mean. A candidate's pull on THIS set is sum of p(s,v) over the coalition; the part of it
// that is not about you is what v would give ANY comparable set of this size. The residual is the
// difference, so the members everyone cosponsors with (Tom Emmer, Andy Barr) carry a large
// subtraction and only stay on top when your set really is their core. SCOPE: this is an affinity
// ranking, NOT the reach objective — the submodular (1-1/e) reasoning attaches to the Reach basis
// only, and the reach stat below the panel keeps measuring reach in both bases.
//
// RE-CENTRED 2026-08-03. The previous null was deg(v)*|S|/(N-1) — what v would give any set of
// that size IF their cosponsorship book were spread evenly over all 532 others. It is not: when
// the seeds are House Republicans, Emmer's book concentrates in that same pool, so the uniform
// null UNDER-corrects, and under-corrects further as |S| grows. Measured on the live graph,
// 300 coalitions per cell, House-Republican seeds — the old comment's claim that the score was
// "centred, not monotone increasing" was falsified on concentration:
//        |S|          3      5      8     12     20
//        HHI      0.114  0.121  0.134  0.191  0.226      (monotone WORSE)
//        Emmer#1   26.0%  27.0%  26.0%  34.3%  36.3%
// Coalition BUILDING is exactly the growing-|S| regime, so the reassuring size-5 numbers quoted
// on 2026-08-02 were taken in the best cell. The null is now the PEER UNIVERSE the seeds are
// actually drawn from — the leave-one-out same-side comparison pool of lobby_champions.py — i.e.
// for each stratum (party x chamber) present among the seeds, the mean tie v has to a member of
// that stratum, times how many seeds come from it. Measured after the change:
//        HHI      0.041  0.046  0.053  0.057  0.064      (flat)
//        Emmer#1    6.7%   5.0%   3.3%   4.0%   3.7%
// A per-tie normalisation variant was tried and REJECTED: it overshoots to mean degree 6.7 and
// surfaces unknowns.
const DEGREE=(()=>{const d=new Float64Array(M.length);
  for(let v=0;v<M.length;v++)(ADJ[v]||[]).forEach(([,p])=>{d[v]+=p;});return d;})();
const STRATKEY=(()=>{const a=new Array(M.length);
  for(let i=0;i<M.length;i++)a[i]=(M[i].party||'')+'|'+(M[i].chamber||'');return a;})();
const STRATMEM=(()=>{const m={};for(let i=0;i<M.length;i++)(m[STRATKEY[i]]=m[STRATKEY[i]]||[]).push(i);return m;})();
// mean tie from every member into one stratum, leave-one-out. Depends only on the static graph
// and the selected area, so it is cached per (stratum, area) and built at most once each.
const _MTC={};
function meanTieInto(sk,area){
  const ck=sk+'\0'+(area||'');
  if(_MTC[ck])return _MTC[ck];
  const mem=STRATMEM[sk]||[],inS=new Uint8Array(M.length);
  for(let k=0;k<mem.length;k++)inS[mem[k]]=1;
  const out=new Float64Array(M.length);
  for(let v=0;v<M.length;v++){let tot=0;
    (ADJ[v]||[]).forEach(([u,p])=>{if(inS[u])tot+=adaptiveEdgeProbability(p,u,v,area);});
    out[v]=tot/Math.max(1,mem.length-(inS[v]?1:0));}
  return _MTC[ck]=out;
}
function seedStrata(seeds){const c={};seeds.forEach(s=>{const k=STRATKEY[s];c[k]=(c[k]||0)+1;});return c;}
// ===================== COSPONSOR-PROPENSITY BASIS (2026-08-03) =====================
// Paul: "for the pathfinder we are putting too much emphasis on if something has sponsored.
// Remember, the core ethos for the pathfinder is that we already found a sponsor, it is finding
// the best path around them to build the most effective co-sponsor coalition."
// So the sponsor is GIVEN and sponsorship capability is NOT what we are shopping for. Two pieces:
//   (1) DIRECTED affinity  - of the coalition's edges, only the ones where a coalition member
//       SPONSORED and the candidate SIGNED count. Recentred against the same leave-one-out
//       same-stratum null as specificResidual(), rebuilt on in-edges.
//   (2) COSPONSOR PROPENSITY - how readily the candidate signs onto other people's bills at all,
//       from the desk's own adjudicated bills ledger, area-conditioned and empirical-Bayes shrunk.
// Sponsorship (D.cospon.spons) is carried for DISPLAY ONLY and enters no score.
const CPR=D.cospon||{},CP_COS=CPR.cos||[],CP_SPO=CPR.spons||[],CP_AREA=CPR.area||[],CP_MIX=CPR.area_share||[];
const HAS_CPROP=CP_COS.length===M.length;
const CP_EB=4;   // empirical-Bayes prior in "bills": a member seen on 2 bills is not evidence of a
                 // specialist. Matches the EB_K spirit in coalition_pricing.py (thin denominators
                 // must not invent extremes); it is a smoothing constant, not a ratified dial.
// mean IN-tie from one stratum into every member, leave-one-out — the directed twin of
// meanTieInto(). Cached per (stratum, area) exactly like its symmetric counterpart.
const _MTCIN={};
function meanInTieInto(sk,area){
  const ck=sk+' '+(area||'');
  if(_MTCIN[ck])return _MTCIN[ck];
  const mem=STRATMEM[sk]||[],inS=new Uint8Array(M.length);
  for(let k=0;k<mem.length;k++)inS[mem[k]]=1;
  const out=new Float64Array(M.length);
  for(let v=0;v<M.length;v++){let tot=0;
    (DIN[v]||[]).forEach(([u,p])=>{if(inS[u])tot+=adaptiveEdgeProbability(p,u,v,area);});
    out[v]=tot/Math.max(1,mem.length-(inS[v]?1:0));}
  return _MTCIN[ck]=out;
}
// v's demonstrated willingness to sign onto THIS set's bills: the probability mass of in-edges
// from coalition members who SPONSORED to v who SIGNED. RAW, not recentred.
//
// specificResidual() subtracts a leave-one-out stratum null because the SYMMETRISED graph has
// out-hubs (Rutherford/Emmer/Barr carry 162-165 edges each) that would otherwise top every list.
// The in-side has no such hub: the largest in-degree on this graph is 12. Subtracting the null
// here was actively harmful and was measured doing so — for a random small coalition NO candidate
// has an in-tie, so every score collapsed to MINUS the expected tie, i.e. the list ranked members
// by how DISCONNECTED they are, and one low-connection member took #1 on 75% of size-3 coalitions.
// Raw tie is already set-specific (it is zero for everyone but this set's actual past cosigners),
// so when the set has no cosponsorship history the affinity half simply goes flat and the
// propensity half decides — which is the honest answer to "no direct evidence with this sponsor".
// meanInTieInto() is retained but NOT wired into any score: it is what the null would have been,
// kept next to stratumPropensity() as the record of a measured-and-rejected alternative.
function directedTie(v,seeds,area){
  let tie=0;
  (DIN[v]||[]).forEach(([u,p])=>{if(seeds.has(u))tie+=adaptiveEdgeProbability(p,u,v,area);});
  return tie;
}
// COSPONSOR PROPENSITY CONDITIONED ON THE SPONSOR'S STRATUM — "how readily does v sign onto bills
// sponsored by members LIKE this coalition (same party x chamber)", which is the conditioning Paul
// asked for. This is meanInTieInto(), i.e. v's IN-degree restricted to the seed strata, so it
// carries no sponsorship pull of its own (largest in-degree on this graph is 12; largest
// out-degree is 165). It is dense where directedTie is sparse: directedTie only fires for the
// handful of members who signed one of THESE seeds' bills, while this fires for anyone who has
// signed anyone comparable, and so it is what actually discriminates the field.
// Scale is deliberate: directedTie is a sum of edge probabilities (~0.03-0.3 each) while this is a
// per-stratum MEAN (~0.001-0.01), so exact history with this sponsor outranks resemblance by an
// order of magnitude and this only decides among candidates with no direct history.
function stratumPropensity(v,seeds,area,strata){
  const st=strata||seedStrata(seeds);
  let e=0;
  for(const k in st)e+=st[k]*meanInTieInto(k,area)[v];
  return e;
}
// EB-shrunk propensity rate. With no area selected this is the member's overall cosponsorship
// volume; with an area it is the rate at which their signatures land in THAT area, shrunk toward
// the global area mix so a member with three bills cannot top the list on one lucky match.
function cosponRate(v,area){
  if(!HAS_CPROP)return 0;
  const n=CP_COS[v]||0;
  if(!area)return n;
  const ai=CATS.indexOf(area); if(ai<0)return n;
  const q=CP_MIX[ai]||0,k=((CP_AREA[v]||[])[ai])||0;
  return (k+CP_EB*q)/(n+CP_EB);
}
// ---------- SET-IMPLIED AREA MIX: the propensity term for the NO-AREA landing view ----------
// (2026-08-03).  cosponRate(v,'') above is a per-member CONSTANT -- the raw count of bills v has
// signed -- so with no issue selected the propensity half of the cosponsor basis carried no
// set-to-set variation at all, and the affinity half is the 80%-zero directedTie block that
// rank01 flattens onto a single midrank.  The view every user lands on before selecting
// anything therefore ranked on ABSOLUTE COSPONSORSHIP VOLUME.  Measured on the live 533-member
// bundle, 300 sponsor-anchored House coalitions per size, deterministic, no area selected:
//        |S|            3      5      8     12     20
//        distinct #1   26     25     28     26     25
//        HHI        .0856  .1118  .1090  .1405  .1299
//        Meuser #1  19.0%  23.3%  24.7%  30.3%  27.3%
// One high-volume signer took the top slot on a quarter to a third of every coalition you could
// build, and which coalition you built barely entered it.
//
// THIS IS A DIFFERENT MECHANISM, NOT A DIFFERENT CONSTANT.  Ungating the priced-influence term
// below was tried and MEASURED and fails in both directions (see INFL_W) for exactly this
// reason: a bounded term has nothing to be proportional against when the ranking underneath it
// does not vary with the set.  What the no-area view lacks is not weight, it is a set signature.
// It has one available: the areas the coalition's own members actually sign in.
//
// coalAreaMix() is that signature, and it is an OVER-INDEX, not a share.  The raw share is
// dominated by whatever the corpus is big in and so is nearly identical for every coalition --
// the same invariance in new coordinates.  Weighting each area by how much THIS set over-indexes
// it against the global signature mix is the leave-one-out peer null of lobby_champions.py
// written in area space, and it is what makes the term discriminate.  Both were measured, same
// protocol:
//                      distinct #1        HHI          top member
//   raw share            45-58         .044-.057       10.7-19.0%   (better, still short)
//   over-index           85-98         .019-.023        4.3-6.3%    <- shipped
// Under the over-index Meuser takes 2.3-4.3% and rho against symmetrised degree is .164-.197.
//
// mixPropensity() is then cosponRate() itself, evaluated one area at a time and averaged over
// that mix -- the same empirical-Bayes shrunk-share algebra the with-area view uses, so the two
// views agree about what propensity MEANS and differ only in where the area weights come from.
// The member's absolute volume cancels out of (k+CP_EB*q)/(n+CP_EB); removing that invariant is
// the entire point of the term.
//
// REJECTED, all measured on the same protocol before this one was chosen:
//  * specificResidual() alone as the no-area basis (the obvious candidate -- it is already
//    peer-recentred and already set-aware).  Flat enough: distinct 50-59, HHI .047-.055.  But it
//    ranks on the SYMMETRISED graph and the list it actually SHOWS reads as a degree ranking --
//    rho vs symmetrised degree .354-.424 against .164-.197 here.  It re-imports precisely the
//    sponsorship pull this basis exists to exclude, so it is flat and WRONG rather than flat.
//  * half specificResidual + half raw volume: worse than shipped (Barr #1 on 18.0-23.7%).
//  * directedTie summed over the coalition's active areas.  An area only re-scales edge
//    probabilities, so this is the same 80%-zero block rescaled: distinct 25-28, HHI .068-.098,
//    i.e. no change -- the direct confirmation that the missing variation is in the propensity
//    half and not the affinity half.
//  * three-way blend adding specificResidual at 0.2 alongside these two: distinct 93-104, HHI
//    .017-.022, materially the same numbers for a term that carries symmetrised degree.  Not
//    taken; the simpler in-side-only version is not beaten.
// Returns null -- and the score falls back to the shipped raw-volume term -- when the seeds carry
// no adjudicated signatures at all (15 House members have an empty ledger), so an absent
// denominator and a real zero are never conflated.
function coalAreaMix(seeds){
  const raw=new Float64Array(CATS.length); let tot=0;
  seeds.forEach(s=>{const row=CP_AREA[s]||[];
    for(let a=0;a<raw.length;a++){const x=row[a]||0;raw[a]+=x;tot+=x;}});
  if(!(tot>0))return null;
  const w=new Float64Array(CATS.length); let pos=0;
  for(let a=0;a<w.length;a++){const d=raw[a]/tot-(CP_MIX[a]||0); if(d>0){w[a]=d;pos+=d;}}
  if(!(pos>0)){for(let a=0;a<w.length;a++)w[a]=raw[a]/tot;return w;}
  for(let a=0;a<w.length;a++)w[a]/=pos;
  return w;
}
// cosponRate() averaged over a coalition's implied area mix.
function mixPropensity(v,mix){
  const n=CP_COS[v]||0,row=CP_AREA[v]||[];
  let s=0;
  for(let a=0;a<mix.length;a++){const w=mix[a];if(!w)continue;
    const q=CP_MIX[a]||0,k=row[a]||0;s+=w*((k+CP_EB*q)/(n+CP_EB));}
  return s;
}
// SELECTIVITY (lift): rate in the selected area divided by the global rate in that area. >1 means
// this member's signatures over-represent the issue — selective; ~1 means they sign across the
// board and merely happen to have touched it. Reported on the row so an indiscriminate signer and
// a genuine issue ally are never shown as the same recommendation.
function cosponLift(v,area){
  if(!HAS_CPROP||!area)return null;
  const ai=CATS.indexOf(area); if(ai<0)return null;
  const q=CP_MIX[ai]||0; if(!(q>0))return null;
  return cosponRate(v,area)/q;
}
// SELECTIVITY WITH A REAL OPPORTUNITY DENOMINATOR (2026-08-03, Paul: "Yeah we gotta account for
// selectivity"). cosponLift() above is the member's own ledger over itself, which is why it was
// shipped as a display readout and kept out of the score. This is the honest version:
//   opp = bills in this area the member COULD have signed - own chamber, a Congress they served
//         in, not a bill they sponsored themselves - from the BILLSTATUS per-bill rosters
//   sel = signatures / opp, empirical-Bayes shrunk with EB_K=2.0 (ratified, coalition_pricing.py)
//         toward the AREA's own global signature rate, so a thin denominator cannot pin an extreme
// Produced by fec/cosponsor_selectivity.py. Returns null when the member had no opportunity in the
// area at all (never served the right chamber, or the area has no adjudicated bills) so that a
// real zero and an absent denominator are never conflated.
const SELJ=D.selectivity||{},SEL_S=SELJ.sel||[],SEL_O=SELJ.opp||[],SEL_G=SELJ.sig||[];
const HAS_SEL=SEL_S.length===M.length;
function cosponSel(v,area){
  if(!HAS_SEL||!area)return null;
  const ai=CATS.indexOf(area); if(ai<0)return null;
  const o=(SEL_O[v]||[])[ai]; if(!(o>0))return null;
  return (SEL_S[v]||[])[ai];
}
function cosponSelCounts(v,area){
  if(!HAS_SEL||!area)return null;
  const ai=CATS.indexOf(area); if(ai<0)return null;
  const o=(SEL_O[v]||[])[ai]; if(!(o>0))return null;
  return [(SEL_G[v]||[])[ai]||0,o];
}
// midrank percentile of an array of scores, ties averaged -> [0,1]
function rank01(vals){
  const n=vals.length,ord=vals.map((_x,k)=>k).sort((a,b)=>vals[a]-vals[b]),r=new Array(n);
  for(let k=0;k<n;){let j=k;while(j+1<n&&vals[ord[j+1]]===vals[ord[k]])j++;
    const p=((k+j)/2)/Math.max(1,n-1);for(let t=k;t<=j;t++)r[ord[t]]=p;k=j+1;}
  return r;
}
function specificResidual(v,seeds=coal,area=cbArea,strata){
  let tie=0;
  (ADJ[v]||[]).forEach(([u,p])=>{if(seeds.has(u))tie+=adaptiveEdgeProbability(p,u,v,area);});
  const st=strata||seedStrata(seeds);
  let expected=0;
  for(const k in st)expected+=st[k]*meanTieInto(k,area)[v];
  return tie-expected;
}
// DEFAULT = coalition-specific (2026-08-02, bug: "the next recommender always asks Andy Barr").
// Measured on the live 533-member graph, 300 coalitions per cell: the Reach basis returns only
// 2-4 distinct top picks (concentration HHI 0.79-0.94) because Tom Emmer and Andy Barr hold 171
// and 164 of the 2,555 cosponsorship edges — correct as an answer to "who casts the widest net",
// useless as an answer to "who next". The coalition-specific basis returns 17-45 distinct top
// picks (HHI 0.12-0.30) on the same coalitions. Flip this string to 'reach' (and move class=on in
// the #cbbasis markup) to restore reach as the landing basis; both remain one click apart.
// SUPERSEDED 2026-08-03 as the LANDING basis by 'cosponsor' (see basisScores): the coalition-
// specific residual is still ranking on the SYMMETRISED graph, and on this graph symmetrised
// degree is 92.8% sponsorship pull for the members who dominate it. Every basis below remains
// one click away; set this back to 'specific' to restore the 2026-08-02 landing behaviour.
let cbBasis='cosponsor';
// UNDERPRICED basis (2026-08-03). Reach and affinity both answer "who moves the most", never
// "who costs the least to move". PRICING carries the per-member access price and a
// majority-neutral capability score from coalition_pricing.py; see that file for the ratified
// constants (SEN_K, LOWCONF_K, EB_K, w_c, w_x) and for why classic Legislative Effectiveness is
// NOT one of the capability inputs. Absent the artifact these fall back to neutral values and
// the basis degrades gracefully to the re-centred affinity ranking.
const PRICING=D.pricing||{};
const P_CAP=PRICING.cap||[],P_PRICE=PRICING.price||[],P_IGN=PRICING.ign||[],P_JUR=PRICING.jur||[];
const capOf=i=>{const v=P_CAP[i];return typeof v==='number'?v:0.5;};
const priceOf=i=>{const v=P_PRICE[i];return typeof v==='number'&&v>0?v:1;};
const HAS_PRICING=P_PRICE.length===M.length;
// PRICED-INFLUENCE WEIGHT for the cosponsor basis (2026-08-03). Paul: "the emmer and barr may
// have overcorrected"; his standing target is that hubs appear "proportional to their genuine
// value NET of access cost and crowding, rather than deterministic", roughly 5-15%.
// 0.25 is the MEASURED value, not a preference. Sponsor-anchored coalitions, 300 per size
// {3,5,8,12,20}, issue selected, deterministic splitmix64 (no sampling enters the product):
//       w      Emmer#1      Barr#1     distinct   HHI          rho vs symmetrised degree
//     0.00   0.0-0.3%    1.0-2.0%    102-120   .015-.017   .099-.128     (shipped, too low)
//     0.15   4.0-7.7%    4.7-6.3%     80-93    .021-.030   .171-.184
//     0.20   6.0-10.3%   5.3-7.3%     75-88    .025-.036   .202-.214
//     0.25   7.7-10.7%   6.7-9.0%     73-84    .031-.042   .237-.259     <- both mid-band
//     0.30   9.3-14.3%   7.3-10.7%    60-73    .044-.060   .283-.302     (rho breaches ~0.30)
//     0.40  14.7-19.7%  13.0-13.7%    39-54    .075-.099   .389-.408     (over the band)
const INFL_W=0.25;
// ONE scorer, shared by the coalition-builder panel and the Bill Repository's nextPeople().
// Until 2026-08-03 nextPeople() had no basis at all: it arrived from the other merge parent
// ranking on candidateInfluence(), and the union merge rewired it to marginalGain — so it got
// the REACH half of the 2026-08-02 fix and not the BASIS half. Measured over all 453 real bills:
//   before that merge   68 distinct #1 picks, HHI 0.046, 192/533 members ever shown
//   after  that merge   12 distinct #1 picks, HHI 0.211,  85/533 members ever shown
// i.e. concentration rose 4.6x and member coverage more than halved, with Emmer taking #1 on
// 168 of 453 bills instead of 36. Both recommenders now read cbBasis through this function.
function basisScores(cand,seeds,area,cov,basis){
  if(basis==='reach')return cand.map(v=>marginalGain(v,cov,seeds,area));
  const strata=seedStrata(seeds);
  // COSPONSOR basis — the recruitment default. Half demonstrated willingness to sign onto THIS
  // set's bills (directed in-edges, recentred on the same leave-one-out stratum null), half
  // general cosponsor propensity from the bills ledger, area-conditioned when an area is picked.
  // Both halves are rank-transformed within the candidate pool so neither can swamp the other,
  // exactly as the underpriced basis does. NOTHING here reads sponsorship: not out-degree, not
  // symmetrised degree, not capability (whose intro-rate component IS a sponsorship rate).
  // Falls back to the affinity residual if the directed graph is missing from the bundle.
  if(basis==='cosponsor'){
    if(!HAS_DIR&&!HAS_CPROP)return cand.map(v=>specificResidual(v,seeds,area,strata));
    // stratumPropensity() was TRIED here as a second, denser affinity term and REJECTED on
    // measurement: it is an in-degree volume statistic and so is cosponRate, so the two halves
    // ended up measuring the same thing and one high-volume cosponsor took #1 on 93.7% of size-3
    // coalitions (HHI 0.879 vs 0.671 without it). stratumPropensity() is therefore DEAD on the
    // scoring path and deliberately left defined as the record of that measurement.
    const aff=rank01(cand.map(v=>HAS_DIR?directedTie(v,seeds,area):0));
    // WITH an area selected this is byte-for-byte the shipped line: _mix is null, so the ternary
    // resolves to cosponRate(v,area) and nothing else on this path moves.  Proven, not asserted --
    // 3,000 with-area coalitions (300 x sizes {3,5,8,12,20} x two sponsor pools) produce top-16
    // lists with an identical SHA-256 before and after, and the summary statistics hash identical
    // too.  With NO area selected cosponRate() is a per-member constant and the propensity half is
    // conditioned on the mix the COALITION implies instead; see coalAreaMix() above for the
    // measurement and for the four alternatives that were measured and rejected.
    const _mix=area?null:coalAreaMix(seeds);
    const pro=rank01(cand.map(v=>_mix?mixPropensity(v,_mix):cosponRate(v,area)));
    // SELECTIVITY (2026-08-03). cosponRate is a volume-and-share statistic: two members with the
    // same area rate rank identically whether that rate came from focus or from signing everything
    // in sight. cosponSel() splits them on a real opportunity denominator. It takes HALF of the
    // propensity half (0.25/0.25), leaving the affinity/propensity balance at 0.5/0.5 exactly as
    // before, and it is rank-transformed in the same pool as the other two so it cannot swamp them
    // — which is also what bounds a thin-denominator member to a percentile instead of an extreme.
    // Fires only when an area is selected AND the artifact is present; otherwise this is
    // byte-identical to the pre-2026-08-03 line (measured: 500/500 no-area coalitions unchanged).
    const _useSel=HAS_SEL&&area&&cand.some(v=>cosponSel(v,area)!=null);
    const sel=_useSel?rank01(cand.map(v=>{const s=cosponSel(v,area);return s==null?0:s;})):null;
    const _base=cand.map((_v,k)=>0.5*aff[k]+(sel?0.25*pro[k]+0.25*sel[k]:0.5*pro[k]));
    // PRICED INFLUENCE (2026-08-03) — the overcorrection fix. Everything above this line is
    // recruitability and NOTHING is influence, so Tom Emmer took the top slot on 0.0-0.3% of
    // coalitions and Andy Barr on 1.0-2.0%: moderated past the point of deletion.
    //
    // DIAGNOSIS, because the obvious explanation is wrong. The directed graph really is
    // sponsor-heavy (max out-degree 165 vs max in-degree 13; the top-20 symmetrised hubs draw
    // 85.8% of their weighted degree from out-edges), but that does NOT zero the hubs here: Emmer's
    // in-side weighted degree is at the 94.9th percentile and his cosponsorship count at the
    // 94.5th. The basis is not penalising him. What it is doing is refusing to distinguish
    // anybody: directedTie is exactly 0 for ~80% of the candidate pool, rank01 hands every one of
    // them the SAME midrank (~0.398), and the surviving propensity half is a volume statistic on
    // which hundreds of members sit within a whisker of each other. The result was a near-flat
    // top — 102-120 distinct #1 picks, HHI 0.015 — where the winner was decided by the radar
    // area-bias rather than by anything about the coalition. Emmer's 0% was a symptom of a
    // degenerate ranking, not of an anti-hub term.
    //
    // THE TERM. New reach the candidate brings this set (marginalGain — already submodular, so a
    // hub retires once the set covers its neighbourhood) divided by its access price (role x
    // seniority x low-confidence, with crowding already inside price via W_X x attention_pct),
    // scaled to the pool maximum. MAGNITUDE IS DELIBERATELY KEPT: rank-transforming this term was
    // measured and REJECTED — it maps the whole top of the field onto ~1.0, so a hub's hundredfold
    // reach advantage becomes one rank step and even w=0.40 left Emmer at 2.7-5.3%.
    //
    // GATED ON `area`, exactly like the selectivity term above it, and for a measured reason: with
    // no area selected the cosponsor basis is very nearly coalition-INVARIANT (cosponRate(v,'') is
    // a per-member constant and the affinity half is the 80% tie block), so the ranking has no
    // set-to-set variation for a bounded influence term to be proportional against — it flips
    // straight from never winning to always winning (w=0.05 -> Emmer 4-16%, rho 0.36-0.39;
    // w=0.20 -> Emmer 42-58%, rho 0.66-0.70). No single ungated weight satisfies both views.
    // THE GATE STAYS after the 2026-08-03 no-area fix (coalAreaMix/mixPropensity above).  That fix
    // gives the no-area RANKING its set-to-set variation back, but this term is a marginal-gain
    // reach statistic over price and neither factor is conditioned by an area that was never
    // selected, so the flip-to-always-winning behaviour measured above is unchanged by it.
    // With no area this line is inert: verified identical top-5 lists on 1500/1500 no-area
    // coalitions. HAS_PRICING is required so that a missing coalition_pricing.json degrades to the
    // old behaviour instead of silently becoming an unpriced reach boost.
    if(!area||!HAS_PRICING)return _base;
    const _infl=cand.map(v=>marginalGain(v,cov,seeds,area)/priceOf(v));
    let _mx=0;for(let k=0;k<_infl.length;k++)if(_infl[k]>_mx)_mx=_infl[k];
    if(!(_mx>0))return _base;
    return _base.map((x,k)=>(1-INFL_W)*x+INFL_W*(_infl[k]/_mx));
  }
  const resid=cand.map(v=>specificResidual(v,seeds,area,strata));
  if(basis!=='underpriced')return resid;
  // value = half set-affinity (rank within THIS pool) + half majority-neutral capability;
  // divided by access price. The affinity half is rank-transformed so the two halves are on
  // one scale and neither can be swamped by the other's units.
  const ord=resid.map((_x,k)=>k).sort((a,b)=>resid[a]-resid[b]);
  const rp=new Array(cand.length);
  for(let k=0;k<ord.length;){let j=k;while(j+1<ord.length&&resid[ord[j+1]]===resid[ord[k]])j++;
    const p=((k+j)/2)/Math.max(1,cand.length-1);for(let t=k;t<=j;t++)rp[ord[t]]=p;k=j+1;}
  return cand.map((v,k)=>(0.5*rp[k]+0.5*capOf(v))/priceOf(v));
}
function suggest(cov){
  if(!coal.size)return [];
  if(!cov)cov=coverage();
  // CANDIDATE POOL — every non-coalition member eligible on the active area (2026-08-03 pool fix).
  // Until today the pool was the UNION OF THE COALITION'S NEIGHBOURS. That pre-biased every score
  // that ran on it: a member is only ever CONSIDERED if they already have a tie to the set, and
  // hubs are in everybody's neighbourhood by construction, so each successive scoring fix was
  // re-ranking an already hub-selected shortlist. The scorers all handle a zero-tie candidate
  // correctly on their own (the recentred residual goes negative, the cosponsor basis falls back
  // to propensity), so the narrowing bought nothing but the bias.
  // CHAMBER GATE. A cosponsor signs the SPONSOR'S bill, so the recruitable pool is the sponsor's
  // own chamber. The neighbour-union pool hid this: neighbours of House members are nearly all
  // House members, so the constraint was satisfied by accident. Widening the pool exposed it —
  // with House seeds every Senator scored a directed residual of EXACTLY 0 (no House member has
  // ever sponsored a bill a Senator cosponsored, because they cannot), which ranked all 100
  // Senators above every House member with a merely negative residual, and the recommender
  // started proposing senators as cosponsors of House bills. Coalitions spanning both chambers
  // (companion bills) keep both chambers eligible.
  const chambers=new Set([...coal].map(i=>M[i].chamber).filter(Boolean));
  const pool=new Set();
  for(let v=0;v<M.length;v++)if(!coal.has(v)&&(!chambers.size||chambers.has(M[v].chamber)))pool.add(v);
  let cand=[...pool].filter(v=>(!cbParty||M[v].party===cbParty)&&memberHasCommittee(M[v],cbCommittee));
  if(!cand.length)return [];
  // marginalGain is DISPLAY-ONLY on the two affinity bases, so it is computed for the 16 rows
  // that survive rather than for the whole pool (often 200-450 members).
  const sc=basisScores(cand,coal,cbArea,cov,cbBasis);
  let scored=cand.map((v,k)=>[v,sc[k]]);
  // unchanged user-facing area bias, applied sign-preservingly so a NEGATIVE coalition-specific
  // residual is pushed up rather than inverted by the multiplier.
  // GUARD 2026-08-03: radar spans exactly [-1,+1], so f hit 0 in 12 of 9,594 member-area cells —
  // a negative residual then became -Infinity and that member was silently buried, while a
  // non-negative one became exactly 0 and floated mid-list. The affected members are precisely
  // those with the STRONGEST engagement on the selected issue, so this was not a rounding
  // curiosity. Floor f at a small positive value; every other cell is arithmetically unchanged.
  if(cbArea){const ai=CATS.indexOf(cbArea);
    scored.forEach(a=>{const f=Math.max(0.05,1+(M[a[0]].radar[ai]||0));a[1]=a[1]>=0?a[1]*f:a[1]/f;});}
  scored.sort((a,b)=>b[1]-a[1]||a[0]-b[0]);                                                     // ties break on index: stable, no RNG
  // [member, ranked score, raw new reach, already-covered, cosponsorships, ties to THIS set,
  //  issue lift]. Every column is reported in every basis, so switching the basis changes the
  //  order and never what a row CLAIMS.
  //  SELECTIVITY (Paul's question: is a member who signs everything the same as one who signs
  //  selectively on this issue?). `lift` was the first answer — the member's area rate over the
  //  global area mix — and it stayed OUT of the score on purpose, because both its numerator and
  //  its denominator come from the member's own ledger, so it is a ratio on single-digit counts
  //  and would have handed the top of the list to anyone with one signature in the right place.
  //  SUPERSEDED 2026-08-03 by cosponSel(), which has a real OPPORTUNITY denominator (the bills in
  //  the area they could have signed and didn't, from the BILLSTATUS rosters) and therefore may
  //  enter the score — it does, at 0.25 in basisScores(). `lift` is retained on the row as the
  //  ledger-only view; `sel` is the scored one, and the row reports the raw signed/eligible counts
  //  behind it so the claim is checkable without trusting the shrinkage.
  return scored.slice(0,16).map(([v,s])=>[v,s,marginalGain(v,cov),cov[v],
    (HAS_CPROP?CP_COS[v]:null),(HAS_DIR?directedTie(v,coal,cbArea):null),cosponLift(v,cbArea),
    cosponSel(v,cbArea),cosponSelCounts(v,cbArea)]);
}
function reachEstimate(cov){ // expected issue-adaptive IC coverage = members with >=1 tie to coalition (1-Π(1-PE))
  if(!coal.size)return 0;
  if(!cov)cov=coverage();
  let r=0;for(let i=0;i<cov.length;i++)r+=cov[i];return r;
}
function renderCoal(){
  $('#cbcount').textContent=coal.size?`· ${coal.size}`:'';
  const list=[...coal].map(i=>M[i]);
  $('#cblist').innerHTML=coal.size?list.map(m=>{const col=PTCOL[m.party]||'#6b7280';return `<span class=coalchip><span class=pdot style=background:${col}></span>${mlink(m.name)}<span class=x data-i=${byName[m.name]} role=button tabindex=0 aria-label="Remove ${esc(m.name)} from the coalition">✕</span></span>`;}).join(''):'<div class=empty>Search above to add bill cosponsors. Suggestions appear once you add the first.</div>';
  // coverage() is pure in (coal, cbArea) and both the headline reach stat and the recommender
  // need it, so it is computed ONCE here and handed to both. That is also what keeps the two
  // from ever disagreeing about what the current set already covers (see the note above it).
  const cov=coal.size?coverage():null;
  // party split + reach
  if(coal.size){const dd=list.filter(m=>m.party==='Democrat').length,rr=list.filter(m=>m.party==='Republican').length,ii=coal.size-dd-rr,d=domainOf(cbArea);
    $('#cbstats').innerHTML=`<div class=mrow><span>Coalition size</span><b>${coal.size}</b></div><div class=mrow><span>Party split (D · R · I)</span><b>${dd} · ${rr} · ${ii}</b></div><div class=mrow><span>Expected issue-adaptive reach</span><b>~${reachEstimate(cov).toFixed(0)} members</b></div>
    ${d?`<div class=mrow><span>${esc(cbArea)} sensitivity</span><b>IC ×${Number(d.ic_edge_scale||1).toFixed(2)} · LT ×${Number(d.lt_threshold_scale||1).toFixed(2)} <small style=color:var(--faint)>(LT context only; ranking is marginal reach)</small></b></div>`:''}
    <div class=legendrow><span><span class=pdot style=background:#3b82f6></span>Dem</span><span><span class=pdot style=background:#ef4444></span>Rep</span><span><span class=pdot style=background:#a855f7></span>Ind</span></div>`;}
  else $('#cbstats').innerHTML='';
  $('#cbnet').style.display=coal.size?'inline-block':'none';
  // suggestions
  // The strength bar is relative to the best candidate on whichever basis is showing. The
  // coalition-specific residual is signed, so shift by the minimum before scaling; when every
  // score is non-negative (the Reach basis) the shift is zero and this is the original formula.
  const sg=suggest(cov),lo=Math.min(0,...sg.map(s=>s[1])),mx=Math.max(0.001,...sg.map(s=>s[1]-lo));
  // Each row states WHY: the reach this member adds that the set does not already have, and how
  // much of the member the set already pulls in. A heavily-tied member scores high on an empty
  // set and falls away once you have added their neighbourhood — that drop is the point.
  $('#cbsugg').innerHTML=coal.size?(sg.length?sg.map(([i,s,gain,already,cosn,dtie,lift,sel,selc])=>{const m=M[i],col=PTCOL[m.party]||'#6b7280',rp=rolePriorAt(i,cbArea),role=roleCodeAt(i,cbArea)!=='-'?' · '+rp.label:'';
    // cosponsor evidence line: how much this member signs, whether they have signed THIS set's
    // bills before, and whether their signatures concentrate on the selected issue (see suggest()).
    const cosBits=[];
    if(cosn!=null)cosBits.push(`signed ${cosn} bill${cosn===1?'':'s'} as cosponsor`);
    if(dtie!=null&&dtie>0)cosBits.push(`<b style=color:#34d399>has cosponsored this set</b>`);
    // SELECTIVITY, scored (2026-08-03): the raw signed-of-eligible counts are printed so the claim
    // is checkable without trusting the shrinkage. `lift` (ledger-only) follows as the weaker view.
    if(selc)cosBits.push(`<b>signed ${selc[0]} of ${selc[1]}</b> eligible ${esc(cbArea.split(' ')[0])} bills${sel!=null?` (sel ${sel.toFixed(2)})`:''}`);
    if(lift!=null)cosBits.push(`${lift>=1.15?'concentrates':lift<=0.85?'spread across issues':'broad'} on ${esc(cbArea.split(' ')[0])} (${lift.toFixed(1)}×)`);
    return `<div class=sugg data-i=${i}><span><span class=pdot style="background:${col};display:inline-block;margin-right:6px"></span>${mlink(m.name)} <span style=color:var(--faint)>${esc(m.chamber)} · #${m.rank??'—'}${role}${cbArea?' · '+f2(m.radar[CATS.indexOf(cbArea)])+' '+cbArea.split(' ')[0]:''}</span><br><small style=color:var(--faint)>${cosBits.join(' · ')}</small><br><small style=color:var(--faint)>adds ~${gain.toFixed(1)} members of new reach · ${(already*100).toFixed(0)}% already covered by this set</small></span><span class=add>+ ${((s-lo)/mx*100).toFixed(0)}</span></div>`;}).join(''):'<div class=empty>No eligible members remain under the current party and committee filters.</div>'):'<div class=empty>Add the bill\'s sponsor to see who to recruit as cosponsors.</div>';
  const drop=el=>{coal.delete(+el.dataset.i);renderCoal();};
  document.querySelectorAll('#cblist .x').forEach(el=>{el.onclick=()=>drop(el);
    el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();drop(el);}};});
  // the suggestion row adds the member; the name anchor inside it opens the profile instead
  document.querySelectorAll('#cbsugg .sugg').forEach(el=>el.onclick=e=>{
    if(e.target.closest&&e.target.closest('a'))return;
    coal.add(+el.dataset.i);renderCoal();});
  renderSnapshots();
}
function renderSnapshots(){
  $('#cbsnapshots').innerHTML=CBSNAPS.length?CBSNAPS.map(s=>`<div class=snaprow><span><b>${esc(s.name)}</b> <span style=color:var(--faint)>· ${(s.members||[]).length} members${s.area?' · '+esc(s.area):''}</span></span><span class=snapacts><button class=ghostbtn data-snapload=${s.id}>Load</button><button class=ghostbtn data-snapdel=${s.id}>Delete</button></span></div>`).join(''):'<div class=empty>No saved coalition snapshots yet.</div>';
}
document.addEventListener('click',e=>{
  const load=e.target.closest('[data-snapload]');if(load){const s=CBSNAPS.find(x=>String(x.id)===load.dataset.snapload);if(!s)return;coal=new Set((s.members||[]).map(n=>byName[n]).filter(i=>i!=null));cbArea=s.area||'';cbParty=s.party||'';cbCommittee=s.committee||'';$('#cbarea').value=cbArea;$('#cbparty').value=cbParty;$('#cbcommittee').value=cbCommittee;renderCoal();return;}
  const del=e.target.closest('[data-snapdel]');if(del){CBSNAPS=CBSNAPS.filter(x=>String(x.id)!==del.dataset.snapdel);saveSnapshots();renderSnapshots();}
});
[$('#cbarea')].forEach(()=>{});CATS.forEach(c=>{});  // (areas already populated above)

/* ======================= EFFICIENCY LAYERS ======================= */
function effPct(v){return v==null?'—':String(Math.round(Number(v)));}
function effInt(v){return v==null?'—':Math.round(Number(v)).toLocaleString();}
function escAttr(s){return esc(s).replace(/"/g,'&quot;');}
function effDollar(v){
  if(v==null)return '—';
  const n=Number(v),a=Math.abs(n);
  return '$'+(a>=1000000?(n/1000000).toFixed(1)+'M':a>=1000?(n/1000).toFixed(1)+'k':Math.round(n).toLocaleString());
}
function effMemberLink(name){return mlink(name);}
function effInvestmentTable(entries){
  if(!entries.length)return '<div class=empty>No members in this label on this vintage.</div>';
  let h='<div class=tablewrap><table><thead><tr><th>Member</th><th class=num>Engine value</th><th class=num>CU PAC $</th><th class=num>Opposing PAC $</th><th class=num>Opposing lobbying</th><th class=num>Crowding</th><th class=num>Score</th></tr></thead><tbody>';
  for(const [name,row] of entries){
    const raw=row.components.raw,shown=EFFD.members[name].investment;
    const tip=`${name}: engine value ${effPct(shown.engine_value)}/100; CU PAC ${effDollar(raw.cu_pac_dollars)}; opposing PAC ${effDollar(raw.opposing_pac_dollars)}; opposing lobbying ${effInt(raw.opposing_lobby_attention)}; signed opposition from other financial industries ${effInt(raw.signed_non_cu_opposition_count)}; crowding ${effPct(shown.crowding)}/100; investment score ${effPct(shown.score)}/100`;
    h+=`<tr title="${escAttr(tip)}"><td>${effMemberLink(name)}</td><td class=num>${effPct(shown.engine_value)}</td><td class=num>${effDollar(raw.cu_pac_dollars)}</td><td class=num>${effDollar(raw.opposing_pac_dollars)}</td><td class=num>${effInt(raw.opposing_lobby_attention)}</td><td class=num>${effPct(shown.crowding)}</td><td class=num><b>${effPct(shown.score)}</b></td></tr>`;
  }
  return h+'</tbody></table></div>';
}
function effLobbyingTable(entries){
  if(!entries.length)return '<div class=empty>No eligible easy targets on this vintage.</div>';
  let h='<div class=tablewrap><table><thead><tr><th>Member</th><th class=num>Openness</th><th class=num>Alignment</th><th class=num>Room to grow</th><th class=num>Crowding</th><th class=num>Score</th></tr></thead><tbody>';
  for(const [name,row] of entries){
    const raw=row.components.raw,shown=EFFD.members[name].lobbying;
    const alignmentTip=shown.alignment==null?'alignment — (no CU-board signal)':`alignment ${effPct(shown.alignment)}/100`;
    const tip=`${name}: openness ${effPct(shown.openness)}/100; ${alignmentTip}; room to grow ${effPct(shown.room_to_grow)}/100, based on ${effInt(raw.portfolio_bills)} portfolio bills and ${effInt(raw.cu_lobby_attention)} credit-union lobbying touches; crowding ${effPct(shown.crowding)}/100; lobbying score ${effPct(shown.score)}/100`;
    h+=`<tr title="${escAttr(tip)}"><td>${effMemberLink(name)}</td><td class=num>${effPct(shown.openness)}</td><td class=num>${effPct(shown.alignment)}</td><td class=num>${effPct(shown.room_to_grow)}</td><td class=num>${effPct(shown.crowding)}</td><td class=num><b>${effPct(shown.score)}</b></td></tr>`;
  }
  return h+'</tbody></table></div>';
}
function renderEfficiencyLayers(){
  if(!EFF||EFF.schema!=='efficiency-layers-v2')throw new Error('efficiency-layers-v2 hard input missing');
  if(!EFFD||!EFFD.members)throw new Error('efficiency percentile display payload missing');
  const entries=Object.entries(EFF.members||{});
  const under=entries.filter(([,row])=>row.investment.label==='UNDERPRICED').sort((a,b)=>b[1].investment.score-a[1].investment.score||a[0].localeCompare(b[0])).slice(0,15);
  const contested=entries.filter(([,row])=>row.investment.label==='CONTESTED').sort((a,b)=>a[1].investment.score-b[1].investment.score||a[0].localeCompare(b[0])).slice(0,10);
  const easy=entries.filter(([,row])=>row.lobbying.eligible&&!row.lobbying.adversary&&row.lobbying.label==='EASY TARGET').sort((a,b)=>b[1].lobbying.score-a[1].lobbying.score||a[0].localeCompare(b[0])).slice(0,15);
  document.getElementById('layers-underpriced').innerHTML=effInvestmentTable(under);
  document.getElementById('layers-contested').innerHTML=effInvestmentTable(contested);
  document.getElementById('layers-easy').innerHTML=effLobbyingTable(easy);
}

/* ======================= INVESTMENT EFFICIENCY ======================= */
const effState={q:'',chamber:'all',sort:'roi'};
$('#effq').oninput=e=>{effState.q=e.target.value.toLowerCase().trim();renderEff();};
$('#effsort').onchange=e=>{effState.sort=e.target.value;renderEff();};
document.querySelectorAll('#effch button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#effch button').forEach(x=>x.classList.remove('on'));b.classList.add('on');effState.chamber=b.dataset.v;renderEff();});
const MAXROI=Math.max(0.001,...M.map(m=>m.roi||0));
if(HONORS){$('#th-eff-rank').textContent='Priority rank';$('#effsort option[value=rank]').textContent='Sort: Overall priority rank';}
function renderEff(){
  let r=M.filter(m=>(!effState.q||m.name.toLowerCase().includes(effState.q))&&(effState.chamber==='all'||m.chamber===effState.chamber));
  const s=effState.sort;
  r.sort((a,b)=> s==='gap'?(b.gap||0)-(a.gap||0) : s==='invested'?(b.invested||0)-(a.invested||0) : s==='rank'?(HONORS?overallRankOrder(a,b,'rank'):((a.rank||9999)-(b.rank||9999))) : s==='value'?(b.eff_value||0)-(a.eff_value||0) : (b.roi||0)-(a.roi||0));
  $('#effcount').textContent=`Showing ${r.length} of ${M.length} members`;
  $('#effrows').innerHTML=r.map(m=>{
    const ev=m.eff_value||0, roi=m.roi||0, gap=m.gap||0;
    const gapTxt=gap>0?('+$'+Math.abs(gap).toLocaleString()):gap<0?('−$'+Math.abs(gap).toLocaleString()):'—';
    const gapCol=gap>0?'#34d399':gap<0?'#ef4444':'var(--muted)';
    return `<tr data-i=${byName[m.name]}>
      <td class=num><span class=rk>${HONORS?(honorAt(m,'').rank??'—'):(m.rank??'—')}</span></td>
      <td><div class=nm>${mlink(m.name)}${potentialChip(m)}<\/div><div class=dist>${esc(m.chamber)} · ${esc(m.party||'—')}${HONORS?`<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:5px"><div><div>Overall priority</div>${priorityBadge(m)}</div><div><div>Behavioral standing</div>${publicBadge(m,'')}</div></div>`:` · <span style="color:${STCOL[m.overall_stance]||'#888'}">${stanceName(m.overall_stance)}</span>`}</div></td>
      <td class=num>${money(m.invested)}</td>
      <td class=num>${m.opt_alloc>0?money(m.opt_alloc):'—'}</td>
      <td class=num style="color:${gapCol}">${gapTxt}</td>
      <td class=num style="color:${ev>=0?'#86efac':'#f0a0a0'}">${ev>=0?'+':''}${ev.toFixed(2)}</td>
      <td><div class=scorecell><div class=sbar><i style="width:${(roi/MAXROI*100).toFixed(1)}%;background:linear-gradient(90deg,#0e7c66,#34d399)"></i></div><span class=sval>${roi.toFixed(3)}</span></div></td>
    </tr>`;}).join('');
  document.querySelectorAll('#effrows tr').forEach(tr=>tr.onclick=e=>rowOpen(e,tr));
}

/* ======================= VIEW SWITCH + EXPORT/IMPORT ======================= */
function setView(v){
  // Live desk: 'member' is a slide-over ABOVE the ranked list, so both stay visible and the
  // reader's place in 533 rows survives. Every other view keeps the shipped swap.
  const scrim=document.getElementById('melscrim');
  if(v==='member'){
    document.querySelectorAll('.view').forEach(s=>{s.hidden=(s.id!=='view-member'&&s.id!=='view-ranked');});
    document.getElementById('view-ranked').hidden=false;
    document.getElementById('view-member').hidden=false;
    document.body.classList.add('mel-over-open');
    if(scrim)scrim.setAttribute('data-open','');
    return;
  }
  document.body.classList.remove('mel-over-open');
  if(scrim)scrim.removeAttribute('data-open');
  document.querySelectorAll('.view').forEach(s=>s.hidden=true);
  $('#view-'+v).hidden=false;
  document.querySelectorAll('#viewseg button').forEach(b=>b.classList.toggle('on',b.dataset.v===v||(v==='member'&&false)));
  if(v==='bills')renderBills();
  if(v==='layers')renderEfficiencyLayers();
  if(v==='coalition')renderCoal();
  if(v==='efficiency')renderEff();
  if(v==='contact')renderContact();
}
document.querySelectorAll('#viewseg button').forEach(b=>b.onclick=()=>{setView(b.dataset.v);if(b.dataset.v==='ranked')history.replaceState(null,'','./');});
$('#exportbtn').onclick=()=>{const blob=new Blob([JSON.stringify(MAN,null,1)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='member_desk_manual.json';a.click();};
$('#importfile').onchange=e=>{const f=e.target.files[0];if(!f)return;const rd=new FileReader();rd.onload=()=>{try{const o=JSON.parse(rd.result);Object.keys(o).forEach(k=>{MAN[k]=Object.assign(MAN[k]||{},o[k]);});saveMAN();alert('Imported notes for '+Object.keys(o).length+' members.');}catch(err){alert('Invalid JSON.');}};rd.readAsText(f);};
$('#povexport').onclick=()=>{
  const cs=Object.keys(POV).map(u=>({url:u,stance:POV[u].stance,note:"manual override via dashboard"+(POV[u].title?(" — "+POV[u].title):"")}));
  if(!cs.length){alert("No manual overrides set yet. Use the Set-CU-stance dropdown on any press article first.");return;}
  const out={_README:"Manual press-stance calls from the dashboard. Merge with:  python fec/apply_press_overrides.py --merge-only  (into press_stance_overrides.json; the NEXT CHAIN RUN applies it via press_stance_stack + build_media_blend). The legacy no-flag re-flow is retired (frozen 06-30 basis; Durbin canary incident 2026-07-16). Match by url (exact). stance: support|oppose|neutral.",corrections:cs};
  const b=new Blob([JSON.stringify(out,null,2)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(b);const repoPath="data/stance/press_overrides_from_dashboard.json";a.download=repoPath.split("/").pop();a.click();
};
$('#povimport').onchange=e=>{const f=e.target.files[0];if(!f)return;const rd=new FileReader();rd.onload=()=>{try{const o=JSON.parse(rd.result);let n=0;(o.corrections||[]).forEach(c=>{if(c.url&&c.stance){POV[c.url]={stance:c.stance,title:c.note||""};n++;}});savePOV();updatePovHint();alert("Imported "+n+" override(s). Reopen a member Press section to see them.");}catch(err){alert("Invalid JSON.");}};rd.readAsText(f);};

/* ======================= STAFF DIRECTORY interactions ======================= */
document.addEventListener('click',e=>{
  const link=e.target.closest&&e.target.closest('[data-staffer-source]');
  if(!link||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
  if(typeof window.__melangeOpenStaffer!=='function')return;
  e.preventDefault();
  window.__melangeOpenStaffer({office:link.dataset.stafferOffice,sourceId:link.dataset.stafferSource});
});
document.addEventListener('click',e=>{
  const el=e.target.closest('[data-act]'); if(!el)return;
  const id=el.dataset.sid, act=el.dataset.act, st=stf(id), card=el.closest('.stfcard');
  if(act==='gold'){setStf(id,'gold',!st.gold); if(card){card.classList.toggle('gold',!st.gold); el.classList.toggle('on',!st.gold); el.textContent=!st.gold?'✓ Contacted':'Mark contacted';} if(!$('#view-contact').hidden)renderContact();}
  else if(act==='star'){const cur=(st.star!=null?st.star:(STFIDX[id]?STFIDX[id].s.star:0)); setStf(id,'star',cur?0:1); el.classList.toggle('on',!cur); el.textContent=!cur?'★':'☆';}
  else if(act==='meet'){setStf(id,'meet',!st.meet); if(el.classList){el.classList.toggle('on',!st.meet); el.textContent=!st.meet?'🤝 Meeting set':'Set up meeting';} if(!$('#view-contact').hidden)renderContact();}
  else if(act==='note'){const box=card.querySelector('.stfnote'); box.hidden=!box.hidden; if(!box.hidden)box.querySelector('textarea').focus();}
  else if(act==='media'){const u=prompt('Media URL (article, clip, photo, doc link):'); if(!u)return; const l=prompt('Label (optional):')||u.replace(/^https?:\/\//,'').slice(0,40); const md=(st.media||[]).concat([{u:u,l:l}]); setStf(id,'media',md); const wrap=card.querySelector('.stfmedia'); const k=md.length-1; wrap.insertAdjacentHTML('beforeend',`<span class=chip>${safeUrl(u)?`<a href="${safeUrl(u)}" target=_blank rel=noopener>${esc(l)}</a>`:`<span>${esc(l)}</span>`}<span class=x data-act=delmedia data-sid=${id} data-k=${k}>×</span></span>`);}
  else if(act==='delmedia'){const md=(st.media||[]); md.splice(+el.dataset.k,1); setStf(id,'media',md); const wrap=el.closest('.stfmedia'); if(wrap){wrap.innerHTML=md.map((mm,k)=>`<span class=chip>${safeUrl(mm.u)?`<a href="${safeUrl(mm.u)}" target=_blank rel=noopener>${esc(mm.l||mm.u)}</a>`:`<span>${esc(mm.l||mm.u)}</span>`}<span class=x data-act=delmedia data-sid=${id} data-k=${k}>×</span></span>`).join('');}}
  else if(act==='openmem'){openMember(+el.dataset.mi); setView('member');}
});
document.addEventListener('change',e=>{
  if(e.target.classList&&e.target.classList.contains('stfnotebox')) setStf(e.target.dataset.sid,'note',e.target.value.trim());
});
document.addEventListener('input',e=>{
  if(e.target.hasAttribute&&e.target.hasAttribute('data-stfsearch')){
    const q=e.target.value.toLowerCase();
    e.target.parentElement.querySelectorAll('.stfcard').forEach(c=>{
      const sf=STFIDX[c.dataset.sid]; const hay=sf?(sf.s.n+' '+sf.s.t).toLowerCase():'';
      c.style.display=hay.includes(q)?'':'none';});
  }
});
function renderContact(){
  const groups={};
  Object.keys(STF).forEach(id=>{ if(STF[id].meet&&STFIDX[id]){ (groups[STFIDX[id].m]=groups[STFIDX[id].m]||[]).push(id); } });
  const mems=Object.keys(groups).sort();
  const inner=mems.length?mems.map(mn=>{
    const mi=STFIDX[groups[mn][0]].mi;
    return `<div class=memgrp><h3>${mlink(mn)} ▸</h3>`+groups[mn].map(id=>stfCard(STFIDX[id].s)).join('')+`</div>`;
  }).join(''):`<div class=empty>No meetings set up yet. Open a member's Staff section and hit “Set up meeting”.</div>`;
  const ngold=Object.keys(STF).filter(id=>STF[id].gold).length;
  $('#contactbody').innerHTML=`<h2>In contact</h2>
    <div style="color:var(--faint);font-size:12px;margin-bottom:8px">${mems.length} office(s) · ${Object.keys(groups).reduce((a,m)=>a+groups[m].length,0)} staffer(s) with meetings · ${ngold} contacted overall
    &nbsp; <button class=ghostbtn id=stfexport>⭳ Export staff state</button> <label class=ghostbtn for=stfimport>⭱ Import</label><input id=stfimport type=file accept=".json" hidden></div>${inner}`;
  const ex=$('#stfexport'); if(ex)ex.onclick=()=>{const b=new Blob([JSON.stringify(STF,null,1)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='staff_state.json';a.click();};
  const im=$('#stfimport'); if(im)im.onchange=e=>{const f=e.target.files[0];if(!f)return;const rd=new FileReader();rd.onload=()=>{try{const o=JSON.parse(rd.result);Object.keys(o).forEach(k=>{STF[k]=Object.assign(STF[k]||{},o[k]);});saveSTF();renderContact();alert('Imported staff state for '+Object.keys(o).length+' staffer(s).');}catch(err){alert('Invalid JSON.');}};rd.readAsText(f);};
}
updatePovHint();

/* ---------- init ---------- */
renderRanked();
/* inbound deep link: ?m=<member name> (also accepts #m=<member name>). The network dashboard,
   the industry page and every member-name anchor on this page all mint that address, so arriving
   with it must land ON the member — not at the top of a 30MB document. */
{
  const qp=inboundMemberAddress(), mi=resolveMemberAddress(qp);
  if(mi!=null)openMember(mi);
  else{
    setView('ranked');
    if(qp)$('#stancehint').textContent=`No member matches “${qp}” — showing the full ranked list.`;
  }
}
/* keep in-page member links working with browser Back/Forward */
window.addEventListener('popstate',()=>{
  const mi=resolveMemberAddress(inboundMemberAddress());
  if(mi!=null)openMember(mi); else setView('ranked');
});
// ---- Live-desk overlay chrome + split-payload merge hook (ratified 2026-08-24) ----
{const sc=document.createElement('div');sc.id='melscrim';sc.className='mel-scrim';document.body.appendChild(sc);
 const closeOver=()=>{if(!document.body.classList.contains('mel-over-open'))return;
   setView('ranked');history.replaceState(null,'','./');};
 sc.addEventListener('click',closeOver);
 document.addEventListener('keydown',e=>{if(e.key==='Escape')closeOver();});}
window.__profilesReady = DSPLIT?false:true;
window.__melangeProfileMerge=function(D2){
  const heavy=D2.members_heavy;
  for(let i=0;i<heavy.length;i++){const h=heavy[i];for(const k in h){D.members[i][k]=h[k];}}
  D.bill_lobbying=D2.bill_lobbying;
  rebuildBillRepository();   // member.bills only exists now; the parse-time index was empty
  rebuildStaffIndex();       // same for member.staffdir -> STFIDX (filter + In-contact panel)
  window.__profilesReady=true;
  const n=document.getElementById('mel-profiles-note');if(n)n.remove();
  if(window.__pendingOpen!=null){const q=window.__pendingOpen;window.__pendingOpen=null;openMember(q);}
};
</script>__PROFILES_TAG__</body></html>
