<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=referrer content=no-referrer>
<title>Melange — Staffer groups</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 208'><path d='M108 20L64 64L28 104L64 144L20 188M64 64L100 104L64 144' fill='none' stroke='%23EECD5C' stroke-width='24' stroke-linejoin='miter' stroke-miterlimit='6'/></svg>">
<style>
:root{color-scheme:dark;--bg:#1D1912;--bar:#282113;--panel:#2E2514;--rule:#433317;--border:#564019;--faint:#9D9784;--muted:#A49D8A;--soft:#D2CDBC;--text:#F3F3E6;--gold:#EECD5C;--deep:#BB8525;--ui-display:"Century Gothic","Tw Cen MT",Futura,"Avenir Next","Segoe UI",sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1D1912;color:#F3F3E6;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
#stf-root{min-height:100vh;background:#1D1912;color:#F3F3E6}
#stf-head{min-height:44px;padding:6px 14px;border-bottom:1px solid #433317;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
#stf-head h1{font-family:var(--ui-display);font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.18em;flex-shrink:0;display:flex;align-items:center;gap:6px;color:#F3F3E6}
#stf-head .stf-brand{flex:0 0 auto}
#stf-head .stf-spacer{flex:1 1 auto;min-width:0}
#stf-flag{border:1px solid #BB8525;color:#EECD5C;border-radius:4px;padding:1px 6px;font-family:var(--ui-display);text-transform:uppercase;letter-spacing:.1em;font-size:9px;white-space:nowrap}
.stf-desklink{align-self:center;flex-shrink:0;border:1px solid #564019;border-radius:6px;padding:7px 13px;font-size:12px;font-weight:600;color:#D2CDBC;text-decoration:none;white-space:nowrap;background:transparent;font-family:inherit}
.stf-desklink:hover{border-color:#EECD5C;color:#F3F3E6;background:#3B2E16}
.stf-desklink:focus-visible{outline:2px solid #EECD5C;outline-offset:2px}
#stf-main{padding:14px;display:flex;flex-direction:column;gap:16px;max-width:132ch}
#stf-findrow{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
#stf-findrow label{font-family:var(--ui-display);color:#A49D8A;font-size:10px;text-transform:uppercase;letter-spacing:.14em}
#stf-find{background:#1D1912;color:#F3F3E6;border:1px solid #564019;border-radius:6px;padding:8px 11px;font-size:13px;min-width:290px;flex:1 1 290px;font-family:inherit}
#stf-find:focus-visible{outline:2px solid #EECD5C;outline-offset:1px}
#stf-findnote{font-size:11px;color:#9D9784}
.stf-sec h2{font-family:var(--ui-display);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.16em;color:#EECD5C;margin-bottom:2px}
.stf-sub{font-size:11px;color:#9D9784;margin-bottom:8px;max-width:110ch;line-height:1.5}
.stf-list{border-top:1px solid #433317}
.stf-item{border-bottom:1px solid #433317}
.stf-item>summary{list-style:none;cursor:pointer;padding:8px 10px;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px}
.stf-item>summary::-webkit-details-marker{display:none}
.stf-item>summary:hover{background:#282113}
.stf-item>summary:focus-visible{outline:2px solid #EECD5C;outline-offset:-2px}
.stf-item>summary::before{content:"\25B8";color:#BB8525;font-size:10px;margin-right:2px}
.stf-item[open]>summary::before{content:"\25BE"}
.stf-item[open]>summary{background:#282113}
.stf-nm{color:#F3F3E6;font-weight:600}
.stf-sl{color:#9D9784;font-size:11px;font-variant-numeric:tabular-nums}
.stf-body{padding:2px 10px 12px 24px}
.stf-people{display:flex;flex-direction:column;gap:7px;margin-top:4px}
.stf-p{line-height:1.35}
.stf-p .stf-pn{color:#D2CDBC}
.stf-p .stf-pd{display:block;color:#9D9784;font-size:11px;margin-top:1px}
.stf-fold{margin-top:10px;border-top:1px solid #433317;padding-top:8px}
.stf-fold>summary{list-style:none;cursor:pointer;font-family:var(--ui-display);font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:#A49D8A;padding:2px 0}
.stf-fold>summary::-webkit-details-marker{display:none}
.stf-fold>summary::before{content:"\25B8 ";color:#BB8525}
.stf-fold[open]>summary::before{content:"\25BE "}
.stf-fold>summary:hover{color:#EECD5C}
.stf-fold>summary:focus-visible{outline:2px solid #EECD5C;outline-offset:2px}
.stf-fold .stf-people{margin-top:8px}
.stf-fold-line{margin-top:10px;border-top:1px solid #433317;padding-top:8px;font-family:var(--ui-display);font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:#A49D8A}
.stf-more{display:block;margin-top:9px;color:#9D9784;font-size:11px}
.stf-showall{margin-top:9px;border:1px solid #564019;border-radius:6px;background:#1D1912;color:#D2CDBC;font:11px/1.2 inherit;padding:7px 12px;cursor:pointer}
.stf-showall:hover{border-color:#EECD5C;background:#3B2E16;color:#F3F3E6}
.stf-showall:focus-visible{outline:2px solid #EECD5C;outline-offset:2px}
#stf-srcfold{border:1px solid #433317;border-radius:8px;background:#2E2514}
#stf-srcfold>summary{list-style:none;cursor:pointer;padding:10px 12px;font-family:var(--ui-display);font-size:10px;text-transform:uppercase;letter-spacing:.16em;color:#A49D8A}
#stf-srcfold>summary::-webkit-details-marker{display:none}
#stf-srcfold>summary::before{content:"\25B8 ";color:#BB8525}
#stf-srcfold[open]>summary::before{content:"\25BE "}
#stf-srcfold>summary:hover{color:#EECD5C}
#stf-srcfold>summary:focus-visible{outline:2px solid #EECD5C;outline-offset:-2px}
#stf-srcbody{padding:0 12px 12px 12px;font-size:11px;color:#D2CDBC;line-height:1.6;max-width:110ch}
#stf-srcbody p{margin-top:8px}
#stf-srcbody .stf-k{font-family:var(--ui-display);text-transform:uppercase;letter-spacing:.12em;font-size:9px;color:#9D9784;margin-right:6px}
#stf-srcbody code{font-family:Consolas,monospace;font-size:10px;color:#A49D8A;word-break:break-all}
#stf-foot{padding:0 14px 16px 14px}
#stf-footline{font-size:11px;color:#9D9784;font-variant-numeric:tabular-nums}
.stf-dim{color:#9D9784}
@media(max-width:640px){#stf-main{padding:10px}#stf-find{min-width:0}.stf-body{padding-left:14px}}
@media(prefers-reduced-motion:reduce){*{transition-duration:1ms!important;animation-duration:1ms!important;scroll-behavior:auto!important}}
</style></head><body>
<div id=stf-root>
<header id=stf-head><h1><svg class=stf-brand width="19" height="31" viewBox="0 0 128 208" aria-hidden="true"><path d="M108 20 L64 64 L28 104 L64 144 L20 188 M64 64 L100 104 L64 144" fill="none" stroke="#EECD5C" stroke-width="19" stroke-linejoin="miter" stroke-miterlimit="6"/></svg>Melange — Staffer groups</h1>
<span id=stf-flag></span>
<span class=stf-spacer></span>
<a id=stf-livelink class=stf-desklink hidden>Live staffer view ▸</a>
<a id=stf-netlink class=stf-desklink href="Moneyball Dashboard.html">Main dashboard ▸</a></header>
<main id=stf-main>
<div id=stf-findrow>
<label for=stf-find>Find</label>
<input id=stf-find type=search autocomplete=off placeholder="a person, an office or an organisation…" aria-label="Find a person, an office or an organisation">
<span id=stf-findnote></span>
</div>
<section class=stf-sec aria-labelledby=stf-officeh>
<h2 id=stf-officeh>Offices</h2>
<div class=stf-sub id=stf-officesub></div>
<div class=stf-list id=stf-officelist></div>
<button id=stf-officemore class=stf-showall type=button hidden></button>
</section>
<section class=stf-sec aria-labelledby=stf-orgh>
<h2 id=stf-orgh>Organisations</h2>
<div class=stf-sub id=stf-orgsub></div>
<div class=stf-list id=stf-orglist></div>
<button id=stf-orgmore class=stf-showall type=button hidden></button>
</section>
<details id=stf-srcfold>
<summary>Sources and method</summary>
<div id=stf-srcbody></div>
</details>
</main>
<footer id=stf-foot><div id=stf-footline></div></footer>
</div>
<script>
const SDATA=__DATA__;
const GDATA=__OFFICES__;
const ODATA=__ORGS__;
const CDATA=__CLIENTS__;
(function(){
"use strict";
/* Four data islands, one per producer artifact.  The Groups data was one file
   until 2026-09-07; the combined document measured 8.29 MB against the real
   generation and no cap setting brought it under the 5 MB producer pin, so
   offices and organisations became two artifacts that each carry their own
   meta -- own generation binding, own input sha256 list, own counts, own byte
   total.  The client ties then needed a third, for the same reason and with a
   different answer: written as cards they measured 21.0 MB, and the threshold
   that fit shipped 203 of 41,203 client organisations.  CDATA is that third
   artifact NORMALIZED -- every name written once, every tie an index -- which
   holds all 41,203 clients and all 132,738 ties inside the pin.  Its keys are
   one or two characters and CDATA.meta.key_legend says what each one holds. */
var META=SDATA.meta||{},GLB=SDATA["global"]||{};
var GMETA=GDATA.meta||{},OMETA=ODATA.meta||{},CMETA=CDATA.meta||{};
var OFFICES=GDATA.offices||[],ORGS=ODATA.organisations||[];
/* The client tables, read straight off the island.  Nothing here is copied into
   a second shape: a card is built from index lookups at the moment it is opened,
   so the page holds the payload once and no name is duplicated in memory either. */
var CID=CDATA.c||[],CN=CDATA.cn||[],CT=CDATA.t||[],CTP=CDATA.tp||[];
var FID=CDATA.f||[],FN=CDATA.fn||[],FE=CDATA.fe||[],EN=CDATA.en||[];
var PLAB=CMETA.period_labels||[];
/* How many groups the page opens with.  The rest are one click or one search away;
   the whole point of the fold is that the first screen stays a first screen. */
var PAGE=8;
/* How many CLIENT matches one search draws.  There are 41,203 client
   organisations against 3,223 registrants, so a one-letter query can match tens
   of thousands of clients and building that many <details> elements locks the
   browser for seconds.  It applies to the client rows ONLY: the office and
   registrant lists are hundreds and low thousands of rows and have always drawn
   in full, and capping them would make the "Show all N" buttons say a number the
   page then did not draw.  Whenever this cap binds the page says so. */
var HITCAP=200;

function num(v){return (v===null||v===undefined||v!==v)?'—':Number(v).toLocaleString('en-US');}
function esc(s){return String(s===null||s===undefined?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function plural(n,one,many){return num(n)+' '+(Number(n)===1?one:many);}

/* One lowercase haystack per group: its own name plus every person name listed under
   it, so the single search box reaches people without a second index. */
function haystack(group,people){
  var bits=[String(group.name||'')];
  (people||[]).forEach(function(p){bits.push(String(p.display_name||''));});
  return bits.join('  ').toLowerCase();
}
var OFFICE_HAY=OFFICES.map(function(o){return haystack(o,o.people);});
/* An organisation card is a REGISTRANT again: the people whose most recent filing
   period names it.  Client organisations have their own tables and their own
   search path below, because 41,203 of them concatenated into card haystacks is
   the duplication the normalization exists to remove. */
var ORG_HAY=ORGS.map(function(o){return haystack(o,o.lobbyists);});
var CLIENT_HAY=CN.map(function(n){return String(n||'').toLowerCase();});
var FILER_HAY=FN.map(function(n){return String(n||'').toLowerCase();});

/* filer -> how many clients, and filer -> which ones: ONE pass over the tie rows.
   Both are what the artifact deliberately does not store, because the page can
   derive them in a few milliseconds and storing them would put the same facts in
   the payload twice.  FCOUNT is the client count a filer's own line states;
   FCLIENTS is what makes a client findable by the name of a lobbyist who files
   for it, which is how the card-shaped payload behaved before the split. */
var FCOUNT=[],FCLIENTS=[],i,j;
for(i=0;i<FN.length;i++){FCOUNT.push(0);FCLIENTS.push(null);}
for(i=0;i<CT.length;i++){
  var trow=CT[i];
  for(j=0;j<trow.length;j++){
    var fx=trow[j];
    if(fx<0||fx>=FN.length)continue;      /* the producer refuses to write one of these */
    FCOUNT[fx]+=1;
    if(!FCLIENTS[fx])FCLIENTS[fx]=[];
    FCLIENTS[fx].push(i);
  }
}

/* ------------------------------------------------------------------ rendering
   data-stf-val marks every span filled from the payload rather than authored by
   this template (owner ruling 2026-09-07, item 3): a registrant's own name, an
   office's own printed string, a person's own title are identifier values, and a
   rendered-page scan for advisory language is expected to strip these before it
   greps chrome.  The static-file build test achieves the same exemption more
   directly, by stripping the SDATA/GDATA/ODATA/CDATA data islands wholesale; the
   attribute here is what a scan of the LIVE page (opened in a browser) keys on. */
function personLine(name,detail){
  return '<div class=stf-p><span class=stf-pn data-stf-val>'+esc(name)+'</span>'+
         (detail?'<span class=stf-pd data-stf-val>'+esc(detail)+'</span>':'')+'</div>';
}
function officeDetail(p){
  var bits=[];
  if(p.title_last)bits.push(p.title_last);
  if(p.tenure_days!==null&&p.tenure_days!==undefined)bits.push(num(p.tenure_days)+' days on the record');
  return bits.join(' · ');
}
function moreLine(n,noun){
  return n?('<span class=stf-more>'+esc('and '+num(n)+' more '+noun+' not listed here')+'</span>'):'';
}
/* Owner ruling 2026-09-07, item 1: the payload lists CURRENT people only, in
   full (o.people is never mixed current/departed any more), and carries
   o.earlier_count as a count with no names attached.  There is nothing left to
   filter client-side; the fold becomes a plain line, not a second people list. */
function officeBody(o){
  var people=o.people||[];
  var html='<div class=stf-body>';
  html+='<div class=stf-people>'+(people.length
      ? people.map(function(p){return personLine(p.display_name,officeDetail(p));}).join('')
      : '<div class="stf-p stf-dim">No current people on this payroll record.</div>')+'</div>';
  if(o.earlier_count){
    html+='<div class=stf-fold-line>'+esc('Earlier staff · '+num(o.earlier_count))+'</div>';
  }
  html+=moreLine(o.more,'people');
  return html+'</div>';
}
/* Owner ruling 2026-09-07: "For the filer, you do what is chronologically the
   most recent. If it appears they are contract/mercenary they are tied to all
   those organizations as clients."  So there are two kinds of card here and they
   come from two different artifacts:

     registrant  ODATA.organisations -- the people whose most recent filing period
                 names it, its employees.  ONE employer per person, so a name
                 appears on exactly one of these.
     client      the CDATA tables -- the contract filers named on filings made for
                 it, resolved by index when the card is opened.  A person appears
                 under as many clients as they file for, and that IS the ruling,
                 so nothing here dedupes them.

   The employer line moved off the person and onto the registrant card, because
   under the ruling every person on one has that registrant as their employer --
   it was saying the heading again on every row.  What is left on a row is what
   varies: how well corroborated that person's registrant is (registrant_tier,
   said in the owner's words through OMETA.employer_corroboration), the period
   their employer is on file as of, and how many clients they file for.

   A single-filer registrant is still a NAME-ONLY line in the payload: it carries
   no `lobbyists` key at all, deliberately, so that "one person on file, not named
   here" can be told apart from "nobody on file" -- which is what an empty array
   would say.  Absence and emptiness get different sentences, and neither of them
   is silence. */
function personDetail(o,p){
  var bits=[p.lda_status_label];
  var phrase=(OMETA.employer_corroboration||{})[p.registrant_tier];
  if(p.employer_as_of)bits.push('on file as of '+p.employer_as_of);
  if(phrase)bits.push(phrase);
  if(p.client_count)bits.push(plural(p.client_count,'client organisation','client organisations'));
  return bits.filter(Boolean).join(' · ');
}
function formerLine(p){
  if(!p.former_employers||!p.former_employers.length)return '';
  return '<div class="stf-p stf-dim"><span class=stf-pd data-stf-val>'+
    esc('Earlier registrant'+(p.former_employers.length===1?'':'s')+': '+
        p.former_employers.map(function(f){
          return f.name+(f.last_filed?' (to '+f.last_filed+')':'');
        }).join('; '))+'</span></div>';
}
function registrantBody(o){
  var people=o.lobbyists;
  if(people===undefined||people===null){
    return '<div class=stf-body><div class="stf-p stf-dim">'+
      esc(plural(o.lobbyist_count,'person is','people are')+
          ' on file under this registrant, and not named here. This page names people where '+
          'two or more are on file under one registrant.')+'</div></div>';
  }
  var html='<div class=stf-body>';
  if(o.employer_line){
    html+='<div class=stf-fold-line><span data-stf-val>'+esc(o.employer_line)+'</span></div>';
  }
  html+='<div class=stf-people>'+(people.length
      ? people.map(function(p){
          return personLine(p.display_name,personDetail(o,p))+formerLine(p);
        }).join('')
      : '<div class="stf-p stf-dim">No people listed for this organisation.</div>')+'</div>';
  html+=moreLine(o.more,'people');
  return html+'</div>';
}
/* A CLIENT card, built entirely by index lookup.  ci is a position in CDATA.c;
   CT[ci] is that client's tie row and every entry in it is a position in CDATA.f,
   so a name is read from the one place it is written and never copied.  CDATA.f
   is ordered by display name, so a tie row -- which the producer writes in filer
   index order -- is already in name order and this sorts nothing. */
function filerDetail(fx,code){
  var bits=[];
  var slot=FE[fx];
  var employer=(slot===undefined||slot===null)?'':EN[slot];
  if(employer)bits.push('Employer on file: '+employer);
  if(FCOUNT[fx])bits.push(plural(FCOUNT[fx],'client organisation','client organisations'));
  var label=PLAB[code];
  if(label)bits.push('last filed '+label);
  return bits.join(' · ');
}
function clientBody(ci){
  var trow=CT[ci]||[],prow=CTP[ci]||[];
  var html='<div class=stf-body>';
  html+='<div class=stf-fold-line>'+esc(plural(trow.length,
        'lobbyist represents this organisation','lobbyists represent this organisation'))+'</div>';
  html+='<div class=stf-people>'+(trow.length
      ? trow.map(function(fx,k){return personLine(FN[fx],filerDetail(fx,prow[k]));}).join('')
      : '<div class="stf-p stf-dim">No people listed for this client.</div>')+'</div>';
  return html+'</div>';
}
function summaryLine(name,sub){
  return '<summary><span class=stf-nm data-stf-val>'+esc(name)+'</span><span class=stf-sl data-stf-val>'+esc(sub)+'</span></summary>';
}
function officeSummary(o){
  return summaryLine(o.name,plural(o.people_count,'person','people')+' · '+num(o.current_count)+' current');
}
/* The two card kinds count different things, so they say different things: a
   registrant's line is how many people it employs, a client's is how many
   contract lobbyists are named on filings made for it.  One number labelled two
   ways would read as the same fact about both. */
function orgSummary(o){
  var engagement=o.engagement==='contract'?'contract firm'
                :(o.engagement==='in_house'?'in-house':'');
  return summaryLine(o.name,plural(o.lobbyist_count,'person','people')+
                            (engagement?' · '+engagement:''));
}
function clientSummary(ci){
  return summaryLine(CN[ci],'client · '+
    plural((CT[ci]||[]).length,'lobbyist represents it','lobbyists represent it'));
}

/* Bodies are filled on first open.  Every list can be shown in full, and filling
   thousands of folds up front is the one thing that would make this page slow.
   A row says which artifact it came from, because the organisations list carries
   registrant cards and client cards side by side once a search brings clients in. */
function renderList(nodeId,rows){
  document.getElementById(nodeId).innerHTML=rows.map(function(r){
    return '<details class=stf-item data-row="'+r.i+'" data-kind="'+r.k+'">'+
           (r.k==='c'?clientSummary(r.i)
                     :(r.k==='o'?officeSummary(OFFICES[r.i]):orgSummary(ORGS[r.i])))+
           '</details>';
  }).join('')||'<div class="stf-p stf-dim" style="padding:10px">Nothing matches that search.</div>';
}
function wireLazy(nodeId){
  document.getElementById(nodeId).addEventListener('toggle',function(e){
    var el=e.target;
    if(!el||el.tagName!=='DETAILS'||!el.hasAttribute('data-row')||!el.open)return;
    if(el.getAttribute('data-filled'))return;
    el.setAttribute('data-filled','1');
    var index=Number(el.getAttribute('data-row')),kind=el.getAttribute('data-kind');
    el.insertAdjacentHTML('beforeend',
      kind==='c'?clientBody(index)
                :(kind==='o'?officeBody(OFFICES[index]):registrantBody(ORGS[index])));
  },true);
}

/* --------------------------------------------------------------------- search */
var query='',officeAll=false,orgAll=false;
function matches(hays,q){
  var out=[],k;
  if(!q){for(k=0;k<hays.length;k++)out.push(k);return out;}
  for(k=0;k<hays.length;k++){if(hays[k].indexOf(q)>=0)out.push(k);}
  return out;
}
/* Client organisations are reachable BY SEARCH and are not part of the browse
   list: there are 41,203 of them against 3,223 registrants, and a directory whose
   default page is thirteen times the size of the thing it is a directory of is
   not a directory.  A client matches on its own name, or on the name of any
   lobbyist who files for it -- the second path is FCLIENTS, and it is what the
   card-shaped payload used to give for free by concatenating roster names into
   every card's haystack. */
function clientMatches(q){
  if(!q)return [];
  var hit={},out=[],k,m;
  for(k=0;k<CLIENT_HAY.length;k++){if(CLIENT_HAY[k].indexOf(q)>=0)hit[k]=1;}
  for(k=0;k<FILER_HAY.length;k++){
    if(FILER_HAY[k].indexOf(q)<0)continue;
    var owned=FCLIENTS[k];
    if(!owned)continue;
    for(m=0;m<owned.length;m++)hit[owned[m]]=1;
  }
  for(var key in hit){if(Object.prototype.hasOwnProperty.call(hit,key))out.push(Number(key));}
  out.sort(function(a,b){
    return ((CT[b]||[]).length-(CT[a]||[]).length)||(CN[a]<CN[b]?-1:(CN[a]>CN[b]?1:0));
  });
  return out;
}
function draw(){
  var q=query.trim().toLowerCase();
  var oHit=matches(OFFICE_HAY,q),gHit=matches(ORG_HAY,q),cHit=clientMatches(q);
  var oShow=(officeAll||q)?oHit:oHit.slice(0,PAGE);
  var gShow=(orgAll||q)?gHit:gHit.slice(0,PAGE);
  var cShow=cHit,clientsCapped=0;
  if(cShow.length>HITCAP){clientsCapped=cShow.length-HITCAP;cShow=cShow.slice(0,HITCAP);}

  renderList('stf-officelist',oShow.map(function(i){return {k:'o',i:i};}));
  renderList('stf-orglist',gShow.map(function(i){return {k:'r',i:i};})
             .concat(cShow.map(function(i){return {k:'c',i:i};})));

  var ob=document.getElementById('stf-officemore'),gb=document.getElementById('stf-orgmore');
  ob.hidden=!!q||oHit.length<=PAGE;
  gb.hidden=!!q||gHit.length<=PAGE;
  ob.textContent=officeAll?('Show the first '+PAGE+' offices'):('Show all '+num(oHit.length)+' offices');
  gb.textContent=orgAll?('Show the first '+PAGE+' organisations'):('Show all '+num(gHit.length)+' organisations');

  document.getElementById('stf-findnote').textContent=q
    ? (num(oHit.length)+' offices, '+num(gHit.length)+' organisations and '+
       num(cHit.length)+' client organisations match'+
       (clientsCapped?('; the first '+num(HITCAP)+' client organisations are drawn, '+
                       num(clientsCapped)+' more are not'):''))
    : '';
}

/* ---------------------------------------------------------- sources and method */
/* Three artifacts, three provenances, and no pretending they are one.  Where the
   metas agree (they are built from the same generation in the same run) the fact
   is stated once; where they can differ -- built_at, byte totals, the input sets,
   the sidecar and LDA generations that only some of them bind -- each is shown,
   so a half-rebuilt page cannot look like a whole one. */
function drawSources(){
  var rows=[];
  function kv(k,v){rows.push('<div><span class=stf-k>'+esc(k)+'</span>'+esc(v)+'</div>');}
  function agree(a,b){return (a&&b&&a===b)?a:[a,b].filter(Boolean).join(' / ')||'—';}
  var gver=GMETA.verification||{},over=OMETA.verification||{},nver=META.verification||{};
  kv('Generation',agree(GMETA.generation,OMETA.generation)||META.generation||'—');
  kv('Verification',(gver.status||over.status||nver.status||'—')+' · '+
     num(gver.failures!==undefined?gver.failures:(over.failures!==undefined?over.failures:nver.failures))+
     ' failures');
  if(GMETA.container_source)kv('Containers read from',GMETA.container_source);
  if(OMETA.sidecar_generation)kv('Corroboration sidecar',OMETA.sidecar_generation);
  if(OMETA.lda_generation||CMETA.lda_generation)kv('LDA corpus',
     agree(OMETA.lda_generation,CMETA.lda_generation));
  kv('Offices built',(GMETA.built_at||'—')+(GMETA.bytes?' · '+num(GMETA.bytes)+' bytes':''));
  kv('Organisations built',(OMETA.built_at||'—')+(OMETA.bytes?' · '+num(OMETA.bytes)+' bytes':''));
  kv('Clients built',(CMETA.built_at||'—')+(CMETA.bytes?' · '+num(CMETA.bytes)+' bytes':''));
  /* One input list across the artifacts.  Roles they share are bound to the same
     sha256 by the same producer run, so a duplicate line would say nothing; a role
     whose sha256 DIFFERS between two of them is the one thing a reader must see,
     so that case is printed twice, labelled. */
  var merged={},dup=[];
  [['offices',GMETA.inputs||{}],['organisations',OMETA.inputs||{}],
   ['clients',CMETA.inputs||{}]].forEach(function(pair){
    var part=pair[0],ins=pair[1];
    Object.keys(ins).forEach(function(role){
      var f=ins[role]||{};
      if(merged[role]===undefined){merged[role]={f:f,part:part};}
      else if(String(merged[role].f.sha256||'')!==String(f.sha256||'')){
        dup.push({role:role,f:f,part:part});
      }
    });
  });
  var files=Object.keys(merged).sort();
  if(files.length){
    rows.push('<p><span class=stf-k>Inputs</span>'+files.map(function(k){
      var f=merged[k].f||{};
      return '<code>'+esc(k+' · '+(f.path||'')+' · '+String(f.sha256||'').slice(0,12))+'</code>';
    }).concat(dup.map(function(d){
      return '<code>'+esc('DISAGREES ('+d.part+') '+d.role+' · '+(d.f.path||'')+' · '+
             String(d.f.sha256||'').slice(0,12))+'</code>';
    })).join('<br>')+'</p>');
  }
  if(META.statement)rows.push('<p>'+esc(META.statement)+'</p>');
  if(META.language_note)rows.push('<p>'+esc(META.language_note)+'</p>');
  rows.push('<p>Same-office pairs rejected for zero overlap: '+num(GLB.zero_overlap_pairs_rejected)+
            '. Two people share an office here only when their service overlapped in time. '+
            'Offices and organisations group the people listed under them. They are not ties between members.</p>');
  /* The Groups note describes THIS page's per-office days.  The network section's
     note of the same name describes a per-member field, so it is not a fallback. */
  if(GMETA.tenure_note)rows.push('<p>'+esc(GMETA.tenure_note)+'</p>');
  if(META.name_policy)rows.push('<p>'+esc(META.name_policy)+'</p>');
  if(GMETA.excluded_container_kinds_note)rows.push('<p>'+esc(GMETA.excluded_container_kinds_note)+'</p>');
  if(OMETA.lda_status_note)rows.push('<p>'+esc(OMETA.lda_status_note)+'</p>');
  if(GMETA.current_day_note)rows.push('<p>'+esc(GMETA.current_day_note)+'</p>');
  if(OMETA.org_cap)rows.push('<p>'+esc('At most '+num(OMETA.org_cap)+
    ' lobbyists are listed under any one organisation; the count on each line is the full count.')+'</p>');
  if(OMETA.singleton_rule)rows.push('<p>'+esc(OMETA.singleton_rule)+'</p>');
  /* The employment ruling, in the owner's own sentence, then what it means here,
     then where the client half lives and how much of it is there.  That last part
     used to be a subtraction a reader had to do; it is now a plain count, and the
     count is every client the ruling reaches. */
  if(OMETA.employment_ruling)rows.push('<p><span class=stf-k>Employment ruling</span>'+
    esc('“'+OMETA.employment_ruling+'”'+
        (OMETA.employment_ruling_date?' — '+OMETA.employment_ruling_date:''))+'</p>');
  if(OMETA.employment_note)rows.push('<p>'+esc(OMETA.employment_note)+'</p>');
  if(OMETA.client_rule)rows.push('<p>'+esc(OMETA.client_rule)+'</p>');
  if(OMETA.clients_note)rows.push('<p>'+esc(OMETA.clients_note)+'</p>');
  if(CMETA.clients_rule)rows.push('<p>'+esc(CMETA.clients_rule)+'</p>');
  var cc=CMETA.counts||{};
  if(cc.client_organisations){
    rows.push('<p>'+esc('Client organisations on this page: '+num(cc.client_organisations)+
      ', tied to '+num(cc.contract_filers)+' contract lobbyists by '+
      num(cc.filer_client_ties)+' filer-client ties. '+num(cc.client_organisations_dropped)+
      ' client organisations and '+num(cc.filer_client_ties_dropped)+' ties were dropped. '+
      'A client is found by typing its name, or the name of a lobbyist who files for it.')+'</p>');
  }
  document.getElementById('stf-srcbody').innerHTML=rows.join('');
}

/* ----------------------------------------------------------------------- wire */
document.getElementById('stf-flag').textContent='Exploratory · review required';
/* The subtitle used to say "every current person is listed".  That is false where
   the office safety cap binds -- 37 of 721 offices on the 2026-09-05 generation,
   all large committees -- so it now says what the payload can actually back, and
   the exact number comes from the artifact's own counter rather than this file. */
var overCap=(GMETA.counts||{}).offices_over_current_cap;
document.getElementById('stf-officesub').textContent=
  'Member offices, committees and leadership offices, shown for the current day. Current '+
  'people are listed by name; how many served earlier is counted, not named.'+
  (overCap?(' In '+num(overCap)+' of these offices there are more current people than the page '+
            'names, and the remainder is counted on that office’s own line.'):'');
/* Two kinds of card can appear in this list, and only one of them is browsable, so
   the subtitle says exactly that.  A reader who is told only about registrants and
   then finds a client card in their search results has been misled by the heading. */
var clientCounts=CMETA.counts||{};
document.getElementById('stf-orgsub').textContent=
  'Lobbying registrants, with the people whose most recent filing period names them.'+
  (clientCounts.client_organisations
    ? (' The '+num(clientCounts.client_organisations)+' client organisations those people '+
       'file for are found by typing a name into Find: each opens with the lobbyists named '+
       'on filings made for it. A person appears under every client they file for.')
    : '');
/* Counts come from each artifact's own meta, never from another's.  The two people
   populations are disjoint by construction (an office person id starts `person:`,
   a lobbyist id starts `lda:lobbyist:`, and the producer refuses a build where
   either rule is broken), so adding them is exact, not an estimate.  Client
   organisations are counted separately rather than folded into the organisation
   total: they are a different artifact with a different id space, and one number
   covering both would be a sum no table on this page could reconcile. */
var counts=GMETA.counts||{},ocounts=OMETA.counts||{};
var listed=(counts.people_listed||0)+(ocounts.people_listed||0);
var footline=
  num(counts.offices!==undefined?counts.offices:OFFICES.length)+' offices · '+
  num(ocounts.organisations!==undefined?ocounts.organisations:ORGS.length)+' organisations · '+
  num(clientCounts.client_organisations!==undefined?clientCounts.client_organisations:CID.length)+
  ' client organisations · '+num(listed)+' people listed';
/* Where the byte cap forced the single-filer registrants down to a bare count,
   say so on the footer rather than letting the organisation total quietly shrink. */
if(OMETA.singleton_mode==='count_only'&&OMETA.singleton_registrants)
  footline+=' · '+num(OMETA.singleton_registrants)+' single-filer registrants not listed';
document.getElementById('stf-footline').textContent=footline;

wireLazy('stf-officelist');
wireLazy('stf-orglist');
drawSources();
draw();

var qt=null;
document.getElementById('stf-find').addEventListener('input',function(){
  var v=this.value;
  if(qt)clearTimeout(qt);
  qt=setTimeout(function(){query=v;draw();},140);
});
document.getElementById('stf-officemore').addEventListener('click',function(){officeAll=!officeAll;draw();});
document.getElementById('stf-orgmore').addEventListener('click',function(){orgAll=!orgAll;draw();});

/* combined-page hooks, same names the network surface publishes */
window.__stafferOpen=function(n){
  if(!n)return;
  var el=document.getElementById('stf-find');
  el.value=n;query=n;draw();
};
window.__stafferShow=function(){};

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
