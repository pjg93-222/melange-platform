/* Run with MENTAT_NODE_MODULES pointing to a local Playwright installation. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const {once} = require('node:events');
const modules = process.env.MENTAT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium} = require(require.resolve('playwright', {paths:[modules]}));
(async () => {
  const trace=message=>{if(process.env.MENTAT_BROWSER_TRACE)console.log(`[browser] ${message}`);};
  const fixture = spawn(process.env.MENTAT_PYTHON || 'python', [path.resolve('mentat/tests/console_browser_fixture.py')], {stdio:['pipe','pipe','pipe'],windowsHide:true});
  trace(`Starting test ${process.pid}; temporary fixture ${fixture.pid}.`);
  let browser;
  const stderr=[]; fixture.stderr.on('data', chunk=>stderr.push(chunk.toString()));
  try {
    const origin = await new Promise((resolve,reject)=>{
      let output='';
      fixture.stdout.on('data',chunk=>{output+=chunk; if(output.includes('\n'))resolve(output.trim().split('\n')[0]);});
      fixture.on('exit',code=>reject(new Error(`Fixture exited ${code}: ${stderr.join('')}`)));
      fixture.on('error',reject);
    });
    browser = await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL || 'chrome'});
    const page = await browser.newPage({viewport:{width:1168,height:880}});
    page.setDefaultTimeout(15000);
    async function captureScreenshot(file,fullPage=true) {
      if(!file)return;
      await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});
      await page.screenshot({path:file,fullPage});
    }
    const errors=[],remote=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',request=>{if(!request.url().startsWith(origin) && !request.url().startsWith('blob:'))remote.push(request.url());});
    const savedEvidence={version:1,company:'Harborlight Credit Union',aliases:['HARBORLIGHT CU'],retrieved_at:'2026-09-10T00:00:00Z',scope:'Fixture evidence',
      committee_finances:[{cycle:2024,committee_id:'C00000001',committee_name:'First PAC'},{cycle:2026,committee_id:'C00000002',committee_name:'Second PAC'}],
      receipts:[],transfers:[],candidate_activity:[],temporal_routes:[]};
    const crossReference={available:true,status:'completed',run_id:'fictional-browser-fixture',report:{
      generated_at:'2026-09-10T00:00:00Z',scope:{industries:['Fictional credit union sample'],companies:[savedEvidence.company],cycles:[2024],source_count:1},
      summary:{input_records:5,automatic_decisions:2,unresolved_records:1,unresolved_amount:40,observed_original_giving:100,
        employee_giving:100,pac_outgoing:60,baseline_amount:160,amount_change:-60,verified_unique:false},
      steps:[{id:'collect',status:'completed'},{id:'connect',status:'review',detail:'Missing counterpart'}],
      unresolved_reasons:[{reason:'Reference missing',records:1,amount:40}],
      review_queue:[{company:savedEvidence.company,reason:'Reference missing',cycle:2024,amount:40,record_id:'fixture-123'}],
      changes:[{company:savedEvidence.company,reason:'Linked outgoing stage',cycle:2024,before:160,after:100,delta:-60,record_id:'fixture-456'}],
      limitations:['Fictional fixture only. Collection coverage remains unverified.'],
    }};
    crossReference.report.digest_available=true;
    crossReference.report.digest={basis:'Synthetic inspection threshold: $100,000.00. No production evidence is shown.',refunds:{
      linked_count:1,linked:[{company:savedEvidence.company,cycle:2024,refund_amount:-2000,original_amount:50000,refund_record_id:'R1',original_record_id:'O1',refund_source_url:'https://docquery.fec.gov/pdf/333/three.pdf',original_source_url:'https://docquery.fec.gov/pdf/000/orig.pdf'}],
      unlinked_count:1,unlinked:[{company:savedEvidence.company,cycle:2024,amount:-7500,description:'RETURN OF ERRONEOUS DEBIT',record_id:'U1'}]},outliers:{
      non_giving_count:1,non_giving:[{company:savedEvidence.company,cycle:2024,amount:15000,role:'other_receipt',description:'LOAN',record_id:'N1',source_url:'https://docquery.fec.gov/pdf/111/one.pdf'}],
      large_count:1,large:[{company:savedEvidence.company,cycle:2024,amount:120000,entity:'IND',record_id:'L1',source_url:'https://docquery.fec.gov/pdf/444/four.pdf'}]}};
    let extraSnapshot=false;
    await page.route('**/api/evidence?*',route=>route.fulfill({json:savedEvidence}));
    await page.route('**/api/evidence',route=>route.fulfill({json:{snapshots:[{id:'fixture-evidence',company:savedEvidence.company,cycles:[2024,2026],retrieved_at:savedEvidence.retrieved_at},...(extraSnapshot?[{id:'new-evidence',company:'Another Company',cycles:[2026]}]:[])]}}));
    await page.route('**/api/cross-reference?*',route=>route.fulfill({json:crossReference}));
    await page.route('**/api/cross-reference',route=>route.fulfill({json:crossReference}));

    const phase=process.env.UI_PHASE||'after',out=path.resolve(process.env.MENTAT_UI_OUTPUT || 'outputs/tabs-polish-20260921');
    fs.mkdirSync(out,{recursive:true});
    await page.route('**/api/insights/catalog',route=>route.fulfill({json:{datasets:[{id:'Credit Union Sample',label:'Credit Union Sample',available:true}],panels:[]}}));
    await page.route('**/api/insights/query',route=>{
      const q=route.request().postDataJSON(),panels={};
      for(const id of q.panels||[]) panels[id]={status:'ready',title:({favorites:'Favorite recipients',trends:'Giving over time',changes:'Changes in giving'})[id]||id,summary:'Synthetic records for interface testing.',columns:[{key:'recipient',label:'Recipient',type:'text'},{key:'amount_cents',label:'Amount',type:'money'}],rows:[{recipient:'Harborlight PAC',amount_cents:125000,filters:[{field:'committee_id',op:'eq',value:'C00000001'}]},{recipient:'Civic Horizon PAC',amount_cents:50000}],notices:[]};
      if(panels.trends){panels.trends.columns=[{key:'cycle',label:'Cycle',type:'text'},{key:'amount_cents',label:'Amount',type:'money'}];panels.trends.rows=['2021-2022','2023-2024','2025-2026'].map((cycle,i)=>({cycle,funding_source:'employee_original',amount_cents:[80000,120000,145000][i]}));}
      if(panels.changes){panels.changes.cards=[{funding_source:'employee_original',baseline_cents:120000,current_cents:145000,reconciled:true}];panels.changes.columns=[{key:'component',label:'Component',type:'text'},{key:'change_cents',label:'Change',type:'money'}];panels.changes.rows=[['Continuing observed companies',15000],['Newly observed companies',18000],['No longer observed companies',-8000]].map(([component,change_cents])=>({component,change_cents,funding_source:'employee_original'}));}
      return route.fulfill({json:{status:'ready',scope:{...q,basis_label:'Reported receipts',as_of:'2026-09-01',baseline_cycle:'2023-2024',current_cycle:'2025-2026'},panels,options:{companies:['Harborlight Credit Union'],funding_sources:['employee_original'],cycles:['2023-2024','2025-2026']},provenance:{workbook:'Synthetic UI workbook.xlsx',index_token:'ui-fixture'},notices:['Synthetic fixture. Coverage is incomplete.']}});
    });
    await page.goto(origin+'/');
    const audit=[],shellByWidth=new Map();
    async function capture(tab){
      for(const width of [1168,900,390]){
        await page.setViewportSize({width,height:width===1168?880:844});
        await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0)});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,tab+' must not overflow at '+width);
        if(width===390)await page.waitForFunction(tab=>{const el=document.getElementById('tab-'+tab),r=el.getBoundingClientRect(),n=el.parentElement.getBoundingClientRect();return r.left>=n.left-1 && r.right<=n.right+1;},tab);
        const shell=await page.evaluate(()=>{
          const style=selector=>getComputedStyle(document.querySelector(selector));
          const nav=style('.app-navigation'),work=style('.workspace'),title=style('.workspace-heading h1'),selected=style('.workspace-tabs [aria-selected=true]'),mark=style('.app-navigation .wordmark');
          return {navigationWidth:nav.width,navigationPadding:nav.padding,workspacePadding:work.padding,titleSize:title.fontSize,titleWeight:title.fontWeight,titleTracking:title.letterSpacing,selectedBackground:selected.backgroundColor,selectedColor:selected.color,wordmark:`${mark.fontWeight} ${mark.fontSize} ${mark.letterSpacing}`};
        });
        if(tab==='graph'){
          if(width>=1100)assert.equal(shell.navigationPadding.split(' ')[0],'20px',`Graph's desktop sidebar keeps board M4's 20px top padding at ${width}px`);
          // r5 (M4 at 1600x900): the wordmark's ink is M4's medium weight and tracking (Arial 600 21px at .1em;
          // the 500 it replaces renders Arial Regular, 28% lighter than the board), and the two-line footer's
          // ink sits at y810 and y827, 62px of padding above the bottom (50px was for a three-line stamp).
          if(width>=1100)assert.deepEqual([shell.wordmark,shell.navigationPadding],['600 21px 2.1px','20px 10px 62px 16px'],`Graph keeps board M4's wordmark and footer at ${width}px`);
          // The page title every M4-styled view shares (Graph, and Chart Studio below 1100px): serif 31px.
          if(width>=761)assert.deepEqual([shell.titleSize,shell.titleWeight,shell.titleTracking],['31px','500','normal'],`Graph keeps board M4's page title at ${width}px`);
        }else{
          // A view that carries its own board's page title pins those title fields; every other field, and
          // any title field it does not own, equals the shared shell (set by the first view that shows it).
          // Build 03: Chart Studio's page title is board S1's serif 40px.
          // Build 04: Insights' page title is board I1's serif 36px.
          // r4, TD-06 (Paul, 2026-09-24: "reconcile and complete the open items"): Loading and Collection
          // keep boards B20/L1-L4 and B22's semibold serif title, 35px.
          // r5 (audit men-b F1, TC-02): Cross-reference (B16), PAC evidence (B19) and Insights (I1) draw the same
          // semibold title; each keeps the tracking whose ink best overlaps its board's (Georgia Bold is wider).
          // r5 (chart-studio.css, "board S1 has no page scroll"): at 1100px and wider Chart Studio's workspace keeps
          // 23px under the studio grid instead of the shared 40px, so its sheet tabs end at the fold.
          const own=tab==='chart-studio'&&width>=1100?{titleSize:'40px',workspacePadding:'11px 22px 23px 26px'}
            :tab==='insights'&&width>=761?{titleSize:'35px',titleWeight:'600',titleTracking:'-2px'}
            :tab==='cross-reference'&&width>=761?{titleSize:'35px',titleWeight:'600',titleTracking:'-1.5px'}
            :tab==='evidence'&&width>=761?{titleSize:'35px',titleWeight:'600',titleTracking:'-0.5px'}
            :(tab==='loading'||tab==='collection')&&width>=761?{titleSize:'35px',titleWeight:'600'}:{};
          for(const [field,value] of Object.entries(own))assert.equal(shell[field],value,`${tab} keeps its board's ${field} ${value} at ${width}px`);
          const shared=shellByWidth.get(width)||{};
          for(const field of Object.keys(shell))if(!(field in own)&&!(field in shared))shared[field]=shell[field];
          shellByWidth.set(width,shared);
          const mine=Object.fromEntries(Object.keys(shell).filter(field=>!(field in own)).map(field=>[field,shell[field]]));
          assert.deepEqual(mine,Object.fromEntries(Object.keys(mine).map(field=>[field,shared[field]])),`${tab}: navigation and page styling must stay consistent at ${width}px`);
        }
        if(width===1168&&(tab==='loading'||tab==='collection')){
          // r4, TD-06: the head's spacing is the boards' (1600x900 frame, same px at 1168). B20: title ink at
          // y24, intro ink at y70, stepper at y112. B22: title ink and pill at y27, tab rule ending at y125.
          const head=await page.evaluate(tab=>{const box=selector=>document.querySelector(selector).getBoundingClientRect();
            return tab==='loading'?{titleTop:Math.round(box('#workspace-title').top),introTop:Math.round(box('#view-loading .view-intro').top),stepsTop:Math.round(box('#loading-steps').top)}
              :{titleTop:Math.round(box('#workspace-title').top),tabsBottom:Math.round(box('.run-collections').bottom)};},tab);
          assert.deepEqual(head,tab==='loading'?{titleTop:18,introTop:66,stepsTop:112}:{titleTop:21,tabsBottom:125},`${tab}: the page head keeps its board's spacing`);
        }
        if(width===1168&&tab==='loading'){
          // r5: B20/L1's roster card draws its buttons, drop line and draft note smaller than 16px, and the setup
          // note breaks before "Start collection." (ink widths in workspace-polish.css).
          const type=await page.evaluate(()=>{const s=q=>getComputedStyle(document.querySelector(q));
            return {add:s('#add-company').fontSize,paste:s('#paste-roster').fontSize,drop:s('#roster-drop').fontSize,draft:s('#draft-status').fontSize,note:s('#setup-note').maxWidth};});
          assert.deepEqual(type,{add:'14px',paste:'14px',drop:'13.5px',draft:'12px',note:'265px'},'loading: the roster card keeps B20\'s type sizes');
        }
        if(width===1168&&['cross-reference','insights','evidence'].includes(tab)){
          // r5: B16's subtitle ink stays at y60 with the run strip under it; I1's subtitle ink at y57 and filter card
          // at y92; B19's title ink at y25, "Split with Graph" at y21 and the rail at y72 (1600x900 frame, same px here).
          const head=await page.evaluate(tab=>{const top=selector=>Math.round(document.querySelector(selector).getBoundingClientRect().top);
            return tab==='cross-reference'?{titleTop:top('#workspace-title'),subtitleTop:top('.xref-subtitle')}
              :tab==='insights'?{titleTop:top('#workspace-title'),subtitleTop:top('.insights-subtitle'),controlsTop:top('#insights-controls')}
              :{titleTop:top('#workspace-title'),splitTop:top('#split-view'),railTop:top('.evidence-rail')};},tab);
          assert.deepEqual(head,tab==='cross-reference'?{titleTop:13,subtitleTop:55}:tab==='insights'?{titleTop:11,subtitleTop:54,controlsTop:92}
            :{titleTop:18,splitTop:21,railTop:72},`${tab}: the page head keeps its board's spacing`);
        }
        if(process.env.MENTAT_UI_SCREENSHOTS){
          await page.screenshot({path:path.join(out,`${phase}-${tab}-${width}.png`),fullPage:true});
          if(width===1168)await page.screenshot({path:path.join(out,`${phase}-${tab}-reference-size.png`),fullPage:false});
        }
        audit.push(await page.evaluate(({tab,width})=>{
          const visible=el=>el.getBoundingClientRect().width>0&&el.getBoundingClientRect().height>0;
          return {tab,width,scroll:document.documentElement.scrollWidth,controls:[...document.querySelectorAll(`#view-${tab} button,#view-${tab} select,#view-${tab} input, #view-${tab} summary`)].filter(visible).map(el=>({id:el.id,label:(el.getAttribute('aria-label')||el.textContent||el.placeholder||'').slice(0,65),height:Math.round(el.getBoundingClientRect().height)}))};
        },{tab,width}));
      }
      await page.setViewportSize({width:1168,height:880});
    }
    await page.waitForFunction(()=>document.querySelector('#loading-coverage').textContent.includes('Stale coverage'));
    await page.fill('#sector','Credit Unions');
    await page.locator('.company-name').fill(savedEvidence.company);
    await page.click('#add-company');
    await page.locator('.remove-company').last().focus();await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('company-name')),true,'Removing a company restores focus to the surviving input');
    await page.click('#review-roster');await page.waitForSelector('#upload-review:not([hidden])');
    assert.equal(await page.locator('#start').innerText(),'Start collection','Roster review leaves starting collection as a separate action');
    await capture('loading');
    await page.click('#tab-collection'); await page.click('[data-run-group=history]'); await page.waitForSelector('#monitor:not([hidden])');
    await capture('collection');
    await page.locator('#collection-coverage-details > summary').click();
    assert.match(await page.locator('#collection-coverage').innerText(),/Stale coverage/);
    await page.locator('#collection-coverage-details > summary').click();
    await page.locator('#collection-freshness-details > summary').click();
    assert.match(await page.locator('#report-freshness').innerText(),/verified/);
    await page.evaluate(()=>{const notice=document.getElementById('error');notice.classList.remove('error');notice.textContent='Run restored.';notice.hidden=false;});
    await page.click('#tab-graph');await page.waitForSelector('#graph-results:not([hidden])');assert.equal(await page.locator('#error').isHidden(),true,'Completed actions do not leak between tabs');await capture('graph');
    await page.click('#tab-chart-studio');
    // Build 03 (S1): the Data source controls live in the source chip's popover.
    await page.click('#studio-scope-change');
    await page.selectOption('#studio-source-mode','worksheet');
    await page.waitForFunction(()=>document.querySelectorAll('#studio-dimensions .studio-pill').length>0);
    await page.click('#studio-scope-change');
    await page.click('#studio-clear');
    await page.locator('#studio-dimensions .studio-pill').filter({hasText:'Company'}).first().click();
    await page.locator('#studio-measures .studio-pill').filter({hasText:'Amount'}).first().click();
    await page.waitForSelector('#studio-canvas svg');
    await page.evaluate(()=>{document.getElementById('studio-source-details').open=false;document.getElementById('studio-data-panel').scrollTop=0;});
    assert.equal(await page.locator('#studio-inspector').isVisible(),true,'1168px shows the Inspector as a third column');
    await capture('chart-studio');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('#studio-data-panel').isVisible(),false,'Mobile canvas is not pushed below the entire field list');
    await page.click('#studio-data-toggle');assert.equal(await page.locator('#studio-data-panel').isVisible(),true);
    await page.click('#studio-data-toggle');await page.click('#studio-scope-change');
    // Build 03 (S1): the source chip opens its popover (not the fields drawer).
    assert.equal(await page.locator('#studio-source-popover').isVisible(),true,'the source chip reveals and focuses its source controls');
    assert.equal(await page.evaluate(()=>document.activeElement.parentElement.id),'studio-source-details');
    // Build 03: Escape puts the source popover away and returns focus to its chip.
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'studio-scope-change');
    await page.click('#studio-data-toggle');
    await page.click('#studio-inspector-toggle');assert.equal(await page.locator('#studio-inspector').isVisible(),true);
    await page.click('#studio-inspector-close');assert.equal(await page.locator('#studio-inspector-toggle').evaluate(el=>el===document.activeElement),true);
    await page.setViewportSize({width:1168,height:880});
    await page.click('#studio-view-canvas');
    assert.equal(await page.locator('#studio-inspector-toggle').isVisible(),true,'Canvas keeps an Inspector control at desktop widths');
    await page.click('#studio-inspector-toggle');assert.equal(await page.locator('#studio-inspector').isVisible(),true);
    await page.click('#studio-inspector-close');await page.click('#studio-view-builder');
    await page.click('#tab-insights'); await page.waitForSelector('#insights-results article');await capture('insights');
    await page.click('#tab-cross-reference');await page.waitForSelector('#xref-results h2');await capture('cross-reference');
    // Integration (2026-09-23), re-homed for build 05 (board B16): the unmatched negative adjustments are a
    // section of the Refunds tab, no longer a <details>; the view's open-and-close evidence is its
    // "How this list is built" disclosure (#xref-method-toggle / #xref-method).
    await page.click('#xref-tab-refunds');
    const details=page.locator('.xref-adjustments'),summary=page.locator('#xref-method-toggle');
    assert.match(await details.innerText(),/-\$7,500\.00/);
    await summary.click();await summary.focus();
    assert.equal(await summary.getAttribute('aria-expanded'),'true');
    crossReference.report.generated_at='2026-09-11T00:00:00Z';
    await page.evaluate(()=>MentatCrossReference.refresh());
    await page.waitForFunction(()=>document.querySelector('.xref-scope').textContent.includes('2026-09-11'));
    assert.equal(await summary.getAttribute('aria-expanded'),'true','Refreshing the same run preserves open evidence');
    assert.equal(await page.locator('#xref-method').isVisible(),true,'Refreshing the same run preserves open evidence');
    assert.equal(await page.locator('#xref-tab-refunds').getAttribute('aria-selected'),'true','Refreshing the same run keeps its tab');
    assert.equal(await summary.evaluate(el=>el===document.activeElement),true,'Refreshing the same run preserves keyboard focus');
    const link=page.locator('#xref-results a').first();await link.focus();crossReference.report.generated_at='2026-09-12T00:00:00Z';
    await page.evaluate(()=>MentatCrossReference.refresh());
    assert.equal(await link.evaluate(el=>el===document.activeElement),true,'Filing links keep focus across a snapshot refresh');
    crossReference.run_id='different-run';crossReference.report.generated_at='2026-09-13T00:00:00Z';
    await page.evaluate(()=>MentatCrossReference.refresh());
    assert.equal(await summary.getAttribute('aria-expanded'),'false','A new run does not inherit old open evidence');
    assert.equal(await page.locator('#xref-method').isVisible(),false,'A new run does not inherit old open evidence');
    await page.click('#tab-evidence');
    await page.waitForFunction(()=>document.querySelector('#evidence-source option[value="snapshot:fixture-evidence"]'));
    await page.selectOption('#evidence-source','snapshot:fixture-evidence');
    // Build 06 (requests\test_workspace_polish_browser.patch, applied by the integration pass): the pane opens
    // from its tick in the picker rail (#evidence-add is hidden).
    await page.waitForFunction(()=>document.querySelector('#evidence-list input[type=checkbox]'));
    await page.locator('#evidence-list input[type=checkbox]').first().check();
    await page.waitForSelector('.evidence-pane h2');
    await capture('evidence');
    assert.deepEqual(errors,[]);assert.deepEqual(remote,[]);
    fs.writeFileSync(path.join(out,`${phase}-audit.json`),JSON.stringify({audit,errors},null,2));
    console.log('PASS seven workspace layouts at 1168, 900 and 390px; uniform navigation and page styling, roster focus, status isolation, coverage disclosures, Studio mobile panels, cross-reference refresh continuity, PAC pane, local requests only.');
    console.log(JSON.stringify({phase,views:audit.map(({tab,width,scroll})=>({tab,width,scroll})),errors}));
  } finally {
    if(browser)await Promise.race([browser.close(),new Promise(resolve=>setTimeout(resolve,10000))]);
    fixture.stdin.end('\n');
    setTimeout(()=>fixture.kill(),3000).unref();
  }
  process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
